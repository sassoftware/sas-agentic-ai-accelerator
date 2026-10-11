# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Register Setup's lookups: finding a project or model by its name.

A name is free text a person chose. "Bob's policies" is an ordinary thing to
call a RAG setup, and it is exactly the shape that ends a filter's string
literal early.

And the tag TLS-Verification-Off: on the model exactly when the score code it
holds has TLS verification off (the REST layer's other decisions are tested in
test_manifest.py, against a stub that answers each request on its own).
"""
import pathlib

import pytest

from rag_core import registration
from rag_core.registration import ViyaClient, name_filter, register_setup


def test_an_ordinary_name_uses_the_usual_quote():
    assert name_filter("Travel policies") == "eq(name,'Travel policies')"


def test_a_name_with_an_apostrophe_is_wrapped_in_the_other_quote():
    """Not doubled - delimited.

    Doubling is not part of the documented grammar; guessing it fails the same
    silent way the bug did.
    """
    assert name_filter("Bob's policies") == 'eq(name,"Bob\'s policies")'


def test_a_name_with_a_double_quote_falls_back_to_the_apostrophe():
    assert name_filter('The "final" draft') == """eq(name,'The "final" draft')"""


def test_a_name_carrying_both_quotes_is_refused_by_name():
    """Refused, not guessed.

    The alternative failure is invisible: a filter the service parses
    differently returns nothing, register_model reads that as "no such model",
    and every re-registration adds another duplicate.
    """
    with pytest.raises(ValueError, match="both"):
        name_filter("""Bob's "final" draft""")


# ---------------------------------------------------------------------------
# TLS-Verification-Off: the tag says what the stored score code does
# ---------------------------------------------------------------------------
TEMPLATE = pathlib.Path(__file__).resolve().parents[1] / "retrieve_context.py"
# the literal, not the constant: this is the text a filter in SAS Model
# Manager looks for, and the text the Prompt Builder sets
TLS_TAG = "TLS-Verification-Off"
MODEL = "/modelRepository/models/model-1"
SETTINGS = {
    "BACKEND": "pgvector", "COLLECTION": "rag_hr_policies_v1",
    "EMBED_MODEL": "all_minilm_l6_v2", "EMBED_ENDPOINT": "https://viya.example/llm",
    "STORE_HOST": "db.example", "STORE_PORT": "5432", "STORE_DB": "postgres",
    "STORE_SSLMODE": "require", "CREDENTIAL_DOMAIN": "agentic-ai-keys",
    "DEFAULT_K": 4, "INGESTION_RUN_ID": "run-1", "rag_project": "RAG_HR",
    "tables_caslib": "casuser",
}
STACK_TAGS = ["LLM", "RAG", "all_minilm_l6_v2", "pgvector"]


class _Reply:
    def __init__(self, status=200, payload=None, page=False):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.content = b"x"
        self.text = "<html>Bad Gateway</html>" if page else "stub refused"
        self.headers = {"ETag": 'W/"stub"'}
        # the page of an ingress in place of the service: no JSON in it
        self._page = page

    def json(self):
        if self._page:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload


class _ModelManager:
    """A SAS Model Manager that REMEMBERS its one model.

    The tag is a statement about state - what the model holds after a
    sequence of requests - so the stub keeps that state instead of answering
    each request on its own: the tags as the last PUT left them, and the
    score code as the last upload left it. Like the service, it drops the
    tags of a create.
    """

    def __init__(self, tags=None, refuse_upload=False, refuse_tag_update=False,
                 upload="", unreadable=False, listing="tags"):
        self.calls = []
        self.headers = {}
        self.model = None
        if tags is not None:
            self.model = {"id": "model-1", "name": "RAG Retrieval - HR",
                          "projectId": "proj-1", "projectVersionId": "ver-1",
                          "tags": list(tags)}
        self.score = None
        self.refuse_upload = refuse_upload
        self.refuse_tag_update = refuse_tag_update
        # what becomes of the upload of the score code: "" (stored and
        # answered), "refused" (the service says 409, nothing stored),
        # "page" (502 with the page of an ingress, nothing stored), "lost"
        # (stored, and the answer never arrives), "dropped" (not stored, and
        # no answer either - the caller cannot tell the last two apart)
        self.upload = upload
        # a model that cannot be read: every GET of it is answered 500
        self.unreadable = unreadable
        # how the listing shows the tags of a model: "tags" (as they are),
        # "null" (`tags: null`, a model without any) or "absent" (no key)
        self.listing = listing
        self.model_puts = 0
        # (what happened, the tags of the model at that moment)
        self.events = []

    def request(self, method, url, **kwargs):
        params = kwargs.get("params") or {}
        body = kwargs.get("json")
        self.calls.append((method, url, params, body))
        if "/folders/folders/@item" in url:
            return _Reply(200, {"id": "folder-1"})
        if url.endswith("/members"):
            return _Reply(200, {"items": []})
        if url.endswith("/files/files"):
            return _Reply(201, {"id": "file-1"})
        if "/projectVersions" in url:
            return _Reply(200, {"items": [{"id": "ver-1", "name": "Version 1"}]})
        if url.endswith("/modelRepository/projects/proj-1"):
            return _Reply(200, {"id": "proj-1", "latestVersion": "Version 1"})
        if "/modelRepository/projects" in url:
            return _Reply(200, {"items": [{"id": "proj-1", "name": "RAG Engineering"}]})
        if url.endswith(MODEL + "/contents"):
            upload = kwargs["files"]["files"]
            if params.get("role") == "score":
                if self.refuse_upload:
                    return _Reply(500)
                if self.upload == "refused":
                    return _Reply(409, {"httpStatusCode": 409})
                if self.upload == "page":
                    return _Reply(502, page=True)
                if self.upload == "dropped":
                    raise ConnectionError("connection reset by peer")
                self.score = upload[1].decode("utf-8")
                self.events.append(("score stored", list(self.model["tags"])))
                if self.upload == "lost":
                    raise TimeoutError("read timed out")
            return _Reply(201, {})
        if url.endswith(MODEL):
            if method == "GET":
                if self.unreadable:
                    return _Reply(500)
                return _Reply(200, dict(self.model, tags=list(self.model["tags"])))
            self.model_puts += 1
            if self.refuse_tag_update and self.model_puts > 1:
                return _Reply(500)
            self.model = dict(body)
            self.events.append(("model updated", list(self.model["tags"])))
            return _Reply(200, dict(self.model))
        if url.endswith("/modelRepository/models"):
            if method == "GET":
                if not self.model:
                    return _Reply(200, {"items": []})
                listed = dict(self.model)
                if self.listing == "null":
                    listed["tags"] = None
                elif self.listing == "absent":
                    listed.pop("tags")
                return _Reply(200, {"items": [listed]})
            self.model = dict(body, id="model-1", tags=[])
            return _Reply(201, {"count": 1, "items": [{"id": "model-1"}]})
        return _Reply(200, {})


def _old_template():
    """The template of a content root deployed before the setting."""
    return TEMPLATE.read_text(encoding="utf-8").replace("\nSSL_VERIFY = True\n", "\n")


def _register(service, verify=None, template=None, log=None, flow_path=""):
    client = ViyaClient("https://viya.example", "token", session=service)
    settings = dict(SETTINGS)
    if verify is not None:
        settings["SSL_VERIFY"] = verify
    return register_setup(
        client, settings,
        template if template is not None else TEMPLATE.read_text(encoding="utf-8"),
        [], "/SAS Agentic AI Accelerator/RAG", "HR", flow_path=flow_path,
        log=log or (lambda *_: None))


def test_a_model_registered_with_verification_off_carries_the_tag():
    service = _ModelManager()
    result = _register(service, verify=False)
    assert "SSL_VERIFY = False" in service.score.splitlines()
    assert TLS_TAG in service.model["tags"]
    assert result["ssl_verify"] is False
    assert result["ssl_verify_in_template"] is True
    assert result["tls_tag_error"] == ""


@pytest.mark.parametrize("verify", [None, True])
def test_a_model_registered_with_verification_on_does_not(verify):
    """None is every caller that does not know the setting."""
    service = _ModelManager()
    result = _register(service, verify=verify)
    assert "SSL_VERIFY = True" in service.score.splitlines()
    assert TLS_TAG not in service.model["tags"]
    assert result["ssl_verify"] is True
    assert result["ssl_verify_in_template"] is True
    assert result["tls_tag_error"] == ""
    # nothing to change, so no second update of the model
    assert service.model_puts == 1


def test_the_stack_tags_are_what_they_were_with_and_without_the_tls_tag():
    off = _ModelManager()
    _register(off, verify=False)
    assert off.model["tags"] == STACK_TAGS + [TLS_TAG]
    on = _ModelManager()
    _register(on, verify=True)
    assert on.model["tags"] == STACK_TAGS


def test_registering_again_with_verification_on_removes_the_tag():
    service = _ModelManager()
    _register(service, verify=False)
    assert TLS_TAG in service.model["tags"]
    result = _register(service, verify=True)
    assert "SSL_VERIFY = True" in service.score.splitlines()
    assert service.model["tags"] == STACK_TAGS
    assert result["ssl_verify"] is True and result["tls_tag_error"] == ""


def test_registering_again_with_verification_off_keeps_the_tag_once():
    service = _ModelManager()
    _register(service, verify=False)
    puts = service.model_puts
    _register(service, verify=False)
    assert service.model["tags"].count(TLS_TAG) == 1
    # the registration's own update, and none for a tag that already is right
    assert service.model_puts == puts + 1


def test_the_tag_changes_only_after_the_score_code_is_stored():
    """Both directions. Until the upload has gone through, the model holds the
    OLD score code, and the tag has to keep describing that one."""
    service = _ModelManager(tags=STACK_TAGS + [TLS_TAG])
    _register(service, verify=True)
    assert [what for what, _tags in service.events] == [
        "model updated", "score stored", "model updated"]
    assert TLS_TAG in service.events[0][1]
    assert TLS_TAG in service.events[1][1]
    assert TLS_TAG not in service.events[2][1]

    service = _ModelManager(tags=STACK_TAGS)
    _register(service, verify=False)
    assert [what for what, _tags in service.events] == [
        "model updated", "score stored", "model updated"]
    assert TLS_TAG not in service.events[0][1]
    assert TLS_TAG not in service.events[1][1]
    assert TLS_TAG in service.events[2][1]


def test_a_refused_upload_leaves_a_model_that_does_not_verify_tagged():
    """The dangerous direction: the model holds score code with verification
    off, a registration with verification on fails at the upload, and the tag
    must still be there - the code that does not verify is still the code."""
    service = _ModelManager(tags=STACK_TAGS + [TLS_TAG], refuse_upload=True)
    with pytest.raises(RuntimeError, match="contents returned 500"):
        _register(service, verify=True)
    assert service.score is None
    assert TLS_TAG in service.model["tags"]


def test_a_refused_upload_does_not_tag_a_model_that_verifies():
    service = _ModelManager(tags=STACK_TAGS, refuse_upload=True)
    with pytest.raises(RuntimeError, match="contents returned 500"):
        _register(service, verify=False)
    assert TLS_TAG not in service.model["tags"]


def test_a_template_older_than_the_setting_is_registered_verifying_and_says_so():
    """Verification was to be switched off, and the template cannot do that.
    The registration goes through, the model is not tagged - it verifies -
    and the result lets the step say that the setting did not take effect."""
    service = _ModelManager()
    result = _register(service, verify=False, template=_old_template())
    assert not [line for line in service.score.splitlines()
                if line.startswith("SSL_VERIFY")]
    assert TLS_TAG not in service.model["tags"]
    assert result["ssl_verify"] is True
    assert result["ssl_verify_in_template"] is False


def test_a_template_older_than_the_setting_still_loses_a_tag_it_no_longer_earns():
    """A model tagged by an earlier registration, registered again from an
    old template: the stored code verifies now, so the tag goes."""
    service = _ModelManager(tags=STACK_TAGS + [TLS_TAG])
    _register(service, verify=False, template=_old_template())
    assert service.model["tags"] == STACK_TAGS


@pytest.mark.parametrize("verify,tagged_before,direction", [
    (False, False, "written to"),
    (True, True, "removed from"),
])
def test_a_tag_that_cannot_be_updated_is_reported_not_swallowed(
        verify, tagged_before, direction):
    """The score code is stored by then, so the registration is not undone;
    the result and the log say which way the tag is now wrong."""
    tags = STACK_TAGS + ([TLS_TAG] if tagged_before else [])
    service = _ModelManager(tags=tags, refuse_tag_update=True)
    logged = []
    result = _register(service, verify=verify, log=logged.append)
    assert result["model_id"] == "model-1"
    assert result["ssl_verify"] is verify
    assert "returned 500" in result["tls_tag_error"]
    assert any(TLS_TAG in line and direction in line
               for line in logged)
    # the artifacts that follow the score code were still attached
    attached = [url for method, url, _p, _b in service.calls
                if method == "POST" and url.endswith("/contents")]
    assert len(attached) == 4


def test_the_log_says_what_the_stored_score_code_does():
    logged = []
    _register(_ModelManager(), verify=False, log=logged.append)
    assert "TLS certificate verification in the stored score code: OFF" in logged
    logged = []
    _register(_ModelManager(), verify=True, log=logged.append)
    assert "TLS certificate verification in the stored score code: on" in logged


def test_setting_the_tag_keeps_every_other_tag_and_sends_the_id():
    """Also a tag a person added by hand - this update changes one tag."""
    service = _ModelManager(tags=["LLM", "reviewed-by-security"])
    client = ViyaClient("https://viya.example", "token", session=service)
    assert client.set_tls_tag("model-1", True) is True
    assert service.model["tags"] == ["LLM", "reviewed-by-security",
                                     TLS_TAG]
    body = next(body for method, url, _p, body in service.calls if method == "PUT")
    assert body["id"] == "model-1"
    assert client.set_tls_tag("model-1", True) is False
    assert client.set_tls_tag("model-1", False) is True
    assert service.model["tags"] == ["LLM", "reviewed-by-security"]
    assert client.set_tls_tag("model-1", False) is False
    assert service.model_puts == 2


# ---------------------------------------------------------------------------
# a registration that fails on the way says how far it got
# ---------------------------------------------------------------------------
MISSING_FLOW = "/Flows/No Such Flow.flw"


@pytest.mark.parametrize("verify,tagged", [(False, True), (True, False)])
def test_a_failure_after_the_score_code_says_what_the_model_holds(verify, tagged):
    """The flow of the scheduled job does not exist: the registration fails,
    and the model holds the new score code all the same. Unticked, that is a
    model that does not verify - the failing run has to be able to say so."""
    service = _ModelManager()
    with pytest.raises(ValueError, match="no flow named") as caught:
        _register(service, verify=verify, flow_path=MISSING_FLOW)
    reached = caught.value.registration
    assert reached == {"model_id": "model-1",
                       "model_name": "RAG Retrieval - HR",
                       "score_code": "stored", "ssl_verify": verify,
                       "ssl_verify_in_template": True, "tls_tag_error": "",
                       "tls_tag_assumed": False}
    # and the model is what the exception says it is
    assert ("SSL_VERIFY = " + str(verify)) in service.score.splitlines()
    assert (TLS_TAG in service.model["tags"]) is tagged


def test_a_failure_after_the_score_code_says_that_the_tag_did_not_follow():
    service = _ModelManager(tags=STACK_TAGS, refuse_tag_update=True)
    with pytest.raises(ValueError, match="no flow named") as caught:
        _register(service, verify=False, flow_path=MISSING_FLOW)
    reached = caught.value.registration
    assert reached["score_code"] == "stored"
    assert reached["ssl_verify"] is False
    assert "returned 500" in reached["tls_tag_error"]
    assert TLS_TAG not in service.model["tags"]


def test_a_failure_before_the_model_exists_carries_nothing():
    """`registration` is about a model. A template that cannot be manifested
    fails before there is one."""
    service = _ModelManager()
    with pytest.raises(ValueError) as caught:
        _register(service, template="print('not the template')\n")
    assert not hasattr(caught.value, "registration")
    assert service.model is None


@pytest.mark.parametrize("upload,stored", [("lost", True), ("dropped", False)])
@pytest.mark.parametrize("verify,tags", [
    (False, STACK_TAGS),              # the tag would have to be written
    (True, STACK_TAGS + [TLS_TAG]),   # the tag would have to go
])
def test_an_upload_without_an_answer_is_not_known_and_the_tag_stays(
        upload, stored, verify, tags):
    """The connection dropped, or the answer timed out. The service may have
    stored the file (`lost`) or not (`dropped`), and the caller sees the same
    thing both times: it says that it does not know, and leaves the tag."""
    service = _ModelManager(tags=tags, upload=upload)
    logged = []
    with pytest.raises((ConnectionError, TimeoutError)) as caught:
        _register(service, verify=verify, log=logged.append)
    assert caught.value.registration["score_code"] == "unknown"
    assert caught.value.registration["ssl_verify"] is verify
    assert (service.score is not None) is stored
    # the tag is as the model had it: no update followed the upload
    assert service.model["tags"] == tags
    assert service.model_puts == 1
    said = [line for line in logged if "it is not known whether" in line]
    assert len(said) == 1
    assert "may have stored the file all the same" in said[0]
    assert TLS_TAG + " was not updated and may not say" in said[0]
    assert "run the registration again" in said[0]


def test_the_page_of_an_ingress_is_no_answer_of_the_service():
    """502 with a page: something answered, but not SAS Model Manager."""
    service = _ModelManager(tags=STACK_TAGS + [TLS_TAG], upload="page")
    logged = []
    with pytest.raises(RuntimeError, match="contents returned 502") as caught:
        _register(service, verify=True, log=logged.append)
    assert caught.value.from_service is False
    assert caught.value.registration["score_code"] == "unknown"
    assert TLS_TAG in service.model["tags"]
    assert any("it is not known whether" in line for line in logged)


def test_a_refusal_of_the_service_is_certain():
    """An answer of SAS Model Manager itself: nothing was stored, and the
    model keeps the score code and the tag it had."""
    service = _ModelManager(tags=STACK_TAGS + [TLS_TAG], upload="refused")
    logged = []
    with pytest.raises(RuntimeError, match="contents returned 409") as caught:
        _register(service, verify=True, log=logged.append)
    assert caught.value.status_code == 409 and caught.value.from_service is True
    assert caught.value.registration["score_code"] == "not stored"
    assert service.score is None
    assert service.model["tags"] == STACK_TAGS + [TLS_TAG]
    assert any("did not store retrieve_context.py" in line for line in logged)
    assert not any("it is not known whether" in line for line in logged)


def test_an_unexpected_status_is_still_a_runtime_error_with_the_old_text():
    """Callers match on RuntimeError and on the text; both are what they
    were. The status and whether the service worded the answer are new."""
    service = _ModelManager(refuse_upload=True)
    client = ViyaClient("https://viya.example", "token", session=service)
    with pytest.raises(RuntimeError) as caught:
        client.put_model_content("model-1", "retrieve_context.py", "x", role="score")
    assert isinstance(caught.value, registration.UnexpectedStatus)
    assert str(caught.value) == ("POST /modelRepository/models/model-1/contents "
                                 "returned 500: stub refused")
    assert caught.value.status_code == 500


# ---------------------------------------------------------------------------
# a model that cannot be read keeps the tag
# ---------------------------------------------------------------------------
def test_a_model_that_cannot_be_read_keeps_the_tag_the_listing_shows():
    """The GET of the model fails, the complete definition still updates it -
    and the definition must not take the tag away: the upload that follows is
    refused here, and the model still holds code that does not verify."""
    service = _ModelManager(tags=STACK_TAGS + [TLS_TAG], unreadable=True,
                            upload="refused")
    with pytest.raises(RuntimeError, match="contents returned 409"):
        _register(service, verify=True)
    assert service.model_puts == 1
    assert service.model["tags"] == STACK_TAGS + [TLS_TAG]


def test_a_model_that_cannot_be_read_and_is_not_tagged_stays_untagged():
    for listing in ("tags", "null"):
        service = _ModelManager(tags=STACK_TAGS, unreadable=True,
                                upload="refused", listing=listing)
        with pytest.raises(RuntimeError, match="contents returned 409"):
            _register(service, verify=False)
        assert service.model["tags"] == STACK_TAGS, listing


def test_a_model_nothing_is_known_about_keeps_the_tag():
    """Neither the model nor its entry in the listing says whether it is
    tagged. A tag too many is put right by the next registration that goes
    through; a model that does not verify and is not tagged is found by
    nobody."""
    service = _ModelManager(tags=STACK_TAGS + [TLS_TAG], unreadable=True,
                            upload="refused", listing="absent")
    with pytest.raises(RuntimeError, match="contents returned 409"):
        _register(service, verify=True)
    assert TLS_TAG in service.model["tags"]


@pytest.mark.parametrize("upload,score_code", [("refused", "not stored"),
                                               ("dropped", "unknown")])
def test_a_tag_written_to_a_model_nothing_is_known_about_is_reported(
        upload, score_code):
    """The model verifies and is not tagged, it cannot be read, and its entry
    in the listing shows no tags: the update WRITES the tag - a tag too many
    rather than one too few. When the upload then fails, the model carries a
    tag this registration put there, over code that verifies. The log must
    not say that the model keeps the tag as it had it, and the caller is
    told."""
    service = _ModelManager(tags=STACK_TAGS, unreadable=True, upload=upload,
                            listing="absent")
    logged = []
    with pytest.raises((RuntimeError, ConnectionError)) as caught:
        _register(service, verify=True, log=logged.append)
    assert service.score is None
    assert service.model["tags"] == STACK_TAGS + [TLS_TAG]
    assert caught.value.registration["score_code"] == score_code
    assert caught.value.registration["tls_tag_assumed"] is True
    assert not any("as it had them" in line for line in logged)
    assert not any("was not updated and may not say" in line for line in logged)
    said = [line for line in logged
            if "without knowing whether it carried it" in line]
    assert len(said) == 1
    assert "run the registration again" in said[0]


@pytest.mark.parametrize("options", [{}, {"unreadable": True}])
def test_a_tag_the_model_or_the_listing_showed_is_not_an_assumed_one(options):
    service = _ModelManager(tags=STACK_TAGS + [TLS_TAG], upload="refused",
                            **options)
    logged = []
    with pytest.raises(RuntimeError, match="contents returned 409") as caught:
        _register(service, verify=True, log=logged.append)
    assert caught.value.registration["tls_tag_assumed"] is False
    assert any("as it had them" in line for line in logged)
    assert not any("without knowing" in line for line in logged)


def test_a_registration_that_goes_through_reports_no_assumed_tag():
    """Once the score code is stored the tag is settled after it: what the
    update before the upload wrote no longer matters, and the result of a
    registration that goes through does not carry the key."""
    service = _ModelManager(tags=STACK_TAGS)
    result = _register(service, verify=True)
    assert "tls_tag_assumed" not in result
    assert service.model["tags"] == STACK_TAGS


def test_a_new_model_that_cannot_be_read_is_not_tagged_before_its_upload():
    """A model created a moment ago holds no score code: nothing to tag."""
    service = _ModelManager(unreadable=True, upload="refused")
    with pytest.raises(RuntimeError, match="contents returned 409"):
        _register(service, verify=False)
    assert service.model["tags"] == STACK_TAGS


def test_an_unreadable_model_reports_the_tag_it_could_not_settle():
    """The upload goes through, the tag cannot be read back to be settled:
    the registration says so instead of leaving it at that."""
    service = _ModelManager(tags=STACK_TAGS + [TLS_TAG], unreadable=True)
    logged = []
    result = _register(service, verify=True, log=logged.append)
    assert result["ssl_verify"] is True
    assert "returned 500" in result["tls_tag_error"]
    assert any(TLS_TAG in line and "removed from" in line for line in logged)


# ---------------------------------------------------------------------------
# the variables of the model
# ---------------------------------------------------------------------------
def _registered_variables(service):
    body = next(body for method, url, _p, body in service.calls
                if method == "POST" and url.endswith("/modelRepository/models"))
    return body["inputVariables"], body["outputVariables"]


def test_every_variable_is_registered_with_a_length():
    """A string variable without a length is stored with 99 by SAS Model
    Manager (found live with a model the Builder had written), which then
    says something else about the model than the Builder's list does. Every
    variable of the request carries one, and they are the Builder's."""
    service = _ModelManager()
    _register(service)
    inputs, outputs = _registered_variables(service)
    for variable in inputs + outputs:
        assert isinstance(variable["length"], int) and variable["length"] > 0, variable
    lengths = {v["name"]: v["length"] for v in inputs + outputs}
    assert lengths == {"question": 32000, "k": 8, "filter_json": 4000,
                       "retrieval_mode": 32, "options": 4000,
                       "context_dg": 32000, "context_envelope": 32000,
                       "retrieval_status": 512, "run_time": 8}
    assert {v["role"] for v in inputs} == {"input"}
    assert {v["role"] for v in outputs} == {"output"}


def test_the_comment_on_the_lengths_names_the_model_that_was_measured():
    """The control measurement ran on a model whose two context outputs had
    been set back to 99. The comment placed it on a model the Prompt Builder
    had written without lengths, which is not what was run."""
    source = pathlib.Path(registration.__file__).read_text(encoding="utf-8")
    comment = source.split("#: The retrieval model's variables", 1)[1].split(
        "RETRIEVAL_INPUTS = [", 1)[0]
    said = " ".join(line[2:].strip() for line in comment.splitlines())
    assert "on a model whose two context outputs were set back to 99" in said
    assert "written without lengths" not in said

