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
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.content = b"x"
        self.text = "stub refused"
        self.headers = {"ETag": 'W/"stub"'}

    def json(self):
        return self._payload


class _ModelManager:
    """A SAS Model Manager that REMEMBERS its one model.

    The tag is a statement about state - what the model holds after a
    sequence of requests - so the stub keeps that state instead of answering
    each request on its own: the tags as the last PUT left them, and the
    score code as the last upload left it. Like the service, it drops the
    tags of a create.
    """

    def __init__(self, tags=None, refuse_upload=False, refuse_tag_update=False):
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
                self.score = upload[1].decode("utf-8")
                self.events.append(("score stored", list(self.model["tags"])))
            return _Reply(201, {})
        if url.endswith(MODEL):
            if method == "GET":
                return _Reply(200, dict(self.model, tags=list(self.model["tags"])))
            self.model_puts += 1
            if self.refuse_tag_update and self.model_puts > 1:
                return _Reply(500)
            self.model = dict(body)
            self.events.append(("model updated", list(self.model["tags"])))
            return _Reply(200, dict(self.model))
        if url.endswith("/modelRepository/models"):
            if method == "GET":
                return _Reply(200, {"items": [self.model] if self.model else []})
            self.model = dict(body, id="model-1", tags=[])
            return _Reply(201, {"count": 1, "items": [{"id": "model-1"}]})
        return _Reply(200, {})


def _old_template():
    """The template of a content root deployed before the setting."""
    return TEMPLATE.read_text(encoding="utf-8").replace("\nSSL_VERIFY = True\n", "\n")


def _register(service, verify=None, template=None, log=None):
    client = ViyaClient("https://viya.example", "token", session=service)
    settings = dict(SETTINGS)
    if verify is not None:
        settings["SSL_VERIFY"] = verify
    return register_setup(
        client, settings,
        template if template is not None else TEMPLATE.read_text(encoding="utf-8"),
        [], "/SAS Agentic AI Accelerator/RAG", "HR",
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
