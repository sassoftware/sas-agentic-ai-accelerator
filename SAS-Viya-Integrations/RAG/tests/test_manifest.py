# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Register Setup's artifacts: the manifested model and the governance files.

The manifested model is the one that gets deployed, so the tests here are
about refusing to produce a broken one rather than about formatting.
"""
import json
import pathlib
import re

import pytest

from rag_core import manifest
from rag_core.manifest import (collection_manifest, ingestion_manifest,
                               pipeline_yaml, render_retrieval_model,
                               settings_from_inventory)

TEMPLATE = pathlib.Path(__file__).resolve().parents[1] / "retrieve_context.py"

SETTINGS = {
    "setup": "HR Policies",
    "BACKEND": "pgvector",
    "COLLECTION": "rag_hr_policies_v1",
    "EMBED_MODEL": "all_minilm_l6_v2",
    "EMBED_ENDPOINT": "https://viya.example/llm",
    "STORE_HOST": "db.example",
    "STORE_PORT": "5432",
    "STORE_DB": "postgres",
    "STORE_SSLMODE": "require",
    "CREDENTIAL_DOMAIN": "agentic-ai-keys",
    "DEFAULT_K": 6,
    "INGESTION_RUN_ID": "run-123",
    "dims": 384,
    "rag_project": "RAG_HR",
    "tables_caslib": "casuser",
    "chunker": "recursive",
    "input_token_limit": 256,
    "overlap_tokens": 30,
    "embed_model": "all_minilm_l6_v2",
    "pipeline_version": "v1",
    "config_id": "cfg-abc",
    "source_kind": "content",
    "source": "/Public/HR",
}


# ---------------------------------------------------------------------------
# the manifested retrieval model
# ---------------------------------------------------------------------------
def test_the_real_template_manifests():
    """Runs against the SHIPPED retrieve_context.py, so a change to its
    MANIFEST block breaks this test rather than a customer deployment."""
    rendered = render_retrieval_model(TEMPLATE.read_text(encoding="utf-8"), SETTINGS)
    assert 'COLLECTION = "rag_hr_policies_v1"' in rendered
    assert 'STORE_HOST = "db.example"' in rendered
    assert 'INGESTION_RUN_ID = "run-123"' in rendered
    assert "DEFAULT_K = 6" in rendered


def test_no_placeholder_survives_manifesting():
    rendered = render_retrieval_model(TEMPLATE.read_text(encoding="utf-8"), SETTINGS)
    for placeholder in ("your-database-host", "your-database-name",
                        "your-sas-viya-host", "rag_p1_job_v1"):
        assert placeholder not in rendered, placeholder


def test_the_rest_of_the_file_is_untouched():
    template = TEMPLATE.read_text(encoding="utf-8")
    rendered = render_retrieval_model(template, SETTINGS)
    assert "def execute(" in rendered
    assert rendered.count("def ") == template.count("def ")
    # the manifested copy must still be valid python
    compile(rendered, "manifested.py", "exec")


@pytest.mark.parametrize("missing", ["COLLECTION", "STORE_HOST", "EMBED_MODEL"])
def test_a_missing_required_setting_is_refused(missing):
    settings = dict(SETTINGS)
    settings[missing] = ""
    with pytest.raises(ValueError, match=missing):
        render_retrieval_model(TEMPLATE.read_text(encoding="utf-8"), settings)


def test_a_template_without_the_marker_is_refused():
    with pytest.raises(ValueError, match="MANIFEST block"):
        render_retrieval_model("print('not the template')\n", SETTINGS)


def test_a_quote_in_a_value_is_refused():
    """Otherwise the rendered module is a syntax error - or worse, executable."""
    settings = dict(SETTINGS, COLLECTION='x"; import os; os.system("id")  #')
    with pytest.raises(ValueError, match="quotes"):
        render_retrieval_model(TEMPLATE.read_text(encoding="utf-8"), settings)


def test_manifesting_is_idempotent():
    once = render_retrieval_model(TEMPLATE.read_text(encoding="utf-8"), SETTINGS)
    twice = render_retrieval_model(once, SETTINGS)
    assert once == twice


# ---------------------------------------------------------------------------
# governance artifacts
# ---------------------------------------------------------------------------
def test_pipeline_yaml_carries_the_configuration():
    text = pipeline_yaml(SETTINGS)
    for expected in ("chunker: recursive", "input_token_limit: 256",
                     "model: all_minilm_l6_v2", "collection: rag_hr_policies_v1",
                     "pipeline_version: v1", "configuration: cfg-abc"):
        assert expected in text, expected


def test_pipeline_yaml_quotes_values_that_would_break_yaml():
    text = pipeline_yaml(dict(SETTINGS, source="/Public/HR: drafts #1"))
    assert '"/Public/HR: drafts #1"' in text


def test_ingestion_manifest_lists_documents_and_skips_the_lock():
    ledger = [
        {"doc_id": "d1", "source_uri": "/b.md", "status": "ingested",
         "chunk_count": 2, "content_hash": "h1", "run_id": "run-123"},
        {"doc_id": "d2", "source_uri": "/a.md", "status": "failed",
         "chunk_count": 0, "content_hash": "", "run_id": "run-123"},
        {"doc_id": "__run_lock__", "status": "lock"},
    ]
    parsed = json.loads(ingestion_manifest(SETTINGS, ledger))
    assert parsed["document_counts"] == {"ingested": 1, "failed": 1}
    assert [d["source_uri"] for d in parsed["documents"]] == ["/a.md", "/b.md"]
    assert parsed["ingestion_run_id"] == "run-123"


def test_collection_manifest_carries_the_ddl_and_the_history_contract():
    parsed = json.loads(collection_manifest(SETTINGS, ddl="CREATE TABLE x();"))
    assert parsed["dimensions"] == 384
    assert parsed["ddl"].startswith("CREATE TABLE")
    assert parsed["history"]["retires_previous_generations"] is True


# ---------------------------------------------------------------------------
# settings taken from the flow instead of retyped
# ---------------------------------------------------------------------------
def test_settings_come_from_the_inventory():
    rows = [{"doc_id": "__run_lock__", "config_json": "{}"},
            {"doc_id": "d1", "rag_project": "RAG_HR", "tables_caslib": "casuser",
             "pipeline_version": "v2", "config_hash": "cfg-9", "run_id": "run-7",
             "config_json": json.dumps({"chunker": "paragraph",
                                        "embed_model": "m1",
                                        "backend": "pgvector",
                                        "collection": "coll_1",
                                        "source_kind": "content"})}]
    settings = settings_from_inventory(rows, {"STORE_HOST": "db.example"})
    assert settings["rag_project"] == "RAG_HR"
    assert settings["COLLECTION"] == "coll_1"
    assert settings["EMBED_MODEL"] == "m1"
    assert settings["INGESTION_RUN_ID"] == "run-7"
    assert settings["config_id"] == "cfg-9"
    assert settings["STORE_HOST"] == "db.example"


def test_explicit_settings_win_over_the_inventory():
    rows = [{"doc_id": "d1", "config_json": json.dumps({"collection": "from_flow"})}]
    settings = settings_from_inventory(rows, {"COLLECTION": "explicit"})
    assert settings["COLLECTION"] == "explicit"


def test_an_unreadable_configuration_does_not_break_registration():
    rows = [{"doc_id": "d1", "rag_project": "P", "config_json": "{oops"}]
    assert settings_from_inventory(rows, {})["rag_project"] == "P"


# ---------------------------------------------------------------------------
# the REST layer, against a stub service
# ---------------------------------------------------------------------------
class _Reply:
    def __init__(self, status=200, payload=None, content=b"x"):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.content = content
        self.text = ""
        self.headers = {"ETag": 'W/"stub"'}

    def json(self):
        return self._payload


class _StubViya:
    """Enough of SAS Viya to check Register Setup's decisions."""

    def __init__(self, existing_model=False, existing_job=False):
        self.calls = []
        self.existing_model = existing_model
        self.existing_job = existing_job
        self.headers = {}

    @staticmethod
    def _asked_for(params) -> str:
        """The name an `eq(name,…)` filter asked for.

        The stub answers the way the service does - a filtered collection
        comes back carrying the name it was filtered on - so the caller's
        own check that the item really is the one it asked for is exercised
        rather than bypassed.
        """
        found = re.match(r"""eq\(name,(['"])(.*)\1\)$""",
                         str((params or {}).get("filter") or ""))
        return found.group(2) if found else ""

    def request(self, method, url, **kwargs):
        params = kwargs.get("params") or {}
        self.calls.append((method, url, params, kwargs.get("json")))
        if "/folders/folders/@item" in url:
            return _Reply(200, {"id": "folder-1"})
        if url.endswith("/members"):
            items = []
            if self.existing_model:
                items.append({"name": "retrieve_context.py",
                              "uri": "/files/files/old"})
            if self.existing_job:
                items.append({"name": "RAG Ingest - HR",
                              "uri": "/jobDefinitions/definitions/job-old"})
            items.append({"name": "RAG-Example.flw",
                          "uri": "/dataFlows/dataFlows/flow-9"})
            return _Reply(200, {"items": items})
        if url.endswith("/content") and "/files/files/" in url:
            return _Reply(204, {})
        if url.endswith("/files/files"):
            return _Reply(201, {"id": "file-1"})
        if "/projectVersions" in url:
            return _Reply(200, {"items": [{"id": "ver-1", "name": "Version 1"}]})
        if url.endswith("/modelRepository/projects/proj-1"):
            return _Reply(200, {"id": "proj-1", "latestVersion": "Version 1"})
        if "/modelRepository/projects" in url:
            return _Reply(200, {"items": [{"id": "proj-1",
                                           "name": self._asked_for(params)}]})
        if "/modelRepository/models/" in url and url.endswith("/contents"):
            return _Reply(201, {})
        if url.endswith("/modelRepository/models/model-new") and method == "GET":
            return _Reply(200, {"id": "model-new", "projectId": "proj-1",
                                "projectVersionId": "ver-1"})
        if url.endswith("/modelRepository/models/model-old") and method == "GET":
            return _Reply(200, {"id": "model-old", "projectId": "proj-1",
                                "projectVersionId": "ver-1",
                                "somethingTheServiceSet": "keep me"})
        if "/modelRepository/models" in url:
            if method == "GET":
                items = ([{"id": "model-old", "projectId": "proj-1",
                           "name": self._asked_for(params)}]
                         if self.existing_model else [])
                return _Reply(200, {"items": items})
            return _Reply(201, {"count": 1, "items": [{"id": "model-new"}]})
        if "/studioDevelopment/code" in url:
            return _Reply(200, {"code": "data _null_; run;"})
        if "/jobDefinitions/definitions" in url:
            return _Reply(201, {"id": "job-new"})
        return _Reply(200, {})


def _register(stub, **kwargs):
    from rag_core.registration import ViyaClient, register_setup
    client = ViyaClient("https://viya.example", "token", session=stub)
    return register_setup(client, dict(SETTINGS), TEMPLATE.read_text(encoding="utf-8"),
                          [{"doc_id": "d1", "source_uri": "/a.md",
                            "status": "ingested", "chunk_count": 1}],
                          "/SAS Agentic AI Accelerator/RAG", "HR",
                          ddl="CREATE TABLE x();", log=lambda *_: None, **kwargs)


def test_registration_writes_artifacts_and_registers_the_model():
    stub = _StubViya()
    result = _register(stub)
    assert result["model_id"] == "model-new"
    assert "pipeline.yaml" in result["artifacts"]
    assert "vector-store-ddl.sql" in result["artifacts"]
    posted = [url for method, url, _p, _b in stub.calls
              if method == "POST" and url.endswith("/files/files")]
    assert len(posted) == 5, "one file per artifact"


def test_re_registering_updates_the_model_instead_of_duplicating_it():
    stub = _StubViya(existing_model=True)
    result = _register(stub)
    assert result["model_id"] == "model-old"
    assert any(method == "PUT" and "/modelRepository/models/model-old" in url
               for method, url, _p, _b in stub.calls)


def test_the_score_file_is_uploaded_with_the_score_role():
    stub = _StubViya()
    _register(stub)
    roles = [params.get("role") for method, url, params, _b in stub.calls
             if url.endswith("/contents")]
    assert "score" in roles


def test_the_job_is_generated_from_the_flow_and_tied_to_it():
    stub = _StubViya()
    result = _register(stub, flow_path="/Users/x/My Folder/RAG-Example.flw")
    assert result["job_id"] == "job-new"
    body = next(body for method, url, _p, body in stub.calls
                if "/jobDefinitions/definitions" in url and method == "POST")
    assert body["properties"][0] == {
        "name": "DeployedResourceName",
        "value": "sascontent:/dataFlows/dataFlows/flow-9"}
    assert "data _null_; run;" in body["code"]


def _stored_job_code(stub):
    return next(body for method, url, _p, body in stub.calls
                if "/jobDefinitions/definitions" in url
                and method in ("POST", "PUT"))["code"]


@pytest.mark.parametrize("existing_job", [False, True])
def test_a_generated_job_never_aborts_its_session(existing_job):
    """`abort 42` in a job ends the session before SAS Job Execution gets its
    completion handshake, so the job reports running for ever. The switch has
    to be the FIRST statement: each step only defaults it when it is unset."""
    stub = _StubViya(existing_job=existing_job)
    _register(stub, flow_path="/Users/x/My Folder/RAG-Example.flw")
    code = _stored_job_code(stub)
    assert code.startswith("%let RAG_ABORT_ON_ERROR = 0;\n")
    assert code.index("%let RAG_ABORT_ON_ERROR = 0;") \
        < code.index("data _null_; run;")


@pytest.mark.parametrize("existing_job", [False, True])
def test_a_generated_job_reports_a_failed_step_as_a_failed_job(existing_job):
    """Without the abort a failing step returns and the program ends
    normally; only SYSCC tells SAS Job Execution that the run failed."""
    stub = _StubViya(existing_job=existing_job)
    _register(stub, flow_path="/Users/x/My Folder/RAG-Example.flw")
    code = _stored_job_code(stub)
    flow_end = code.index("data _null_; run;") + len("data _null_; run;")
    epilogue = code[flow_end:]
    assert code.rstrip().endswith("%_rag_job_outcome;")
    # a conditional in open code is not portable, so it lives in a macro
    assert "%macro _rag_job_outcome;" in epilogue
    assert epilogue.index("%macro _rag_job_outcome;") \
        < epilogue.index("%mend _rag_job_outcome;") \
        < epilogue.rindex("%_rag_job_outcome;")
    body = epilogue[epilogue.index("%macro _rag_job_outcome;"):
                    epilogue.index("%mend _rag_job_outcome;")]
    assert "%superq(RAG_JOB_FAILED) EQ 1 %then %let failed = 1;" in body
    assert "%if &failed. NE 1 %then %return;" in body
    assert "%put ERROR:" in body
    assert "%let SYSCC = 1012;" in body
    assert body.index("%then %return;") < body.index("%let SYSCC")


def test_the_job_outcome_is_read_from_a_flag_no_step_clears():
    """RAG_PIPELINE_FAILED belongs to a chain: List Documents, Purge
    Documents and Retrieve Context clear it when they start. A Retrieve
    Context node that runs after a failed ingestion chain - the user guide
    says to add one anywhere - left it at 0 at the end of the program, and
    the job ended as completed. The job has a flag of its own: the prologue
    sets it to 0, so a session that is used again starts clean, and only a
    failing step changes it."""
    stub = _StubViya()
    _register(stub, flow_path="/Users/x/My Folder/RAG-Example.flw")
    code = _stored_job_code(stub)
    flow_start = code.index("data _null_; run;")
    assert "%global RAG_JOB_FAILED;\n%let RAG_JOB_FAILED = 0;\n" \
        in code[:flow_start]
    # nothing after the generated code sets it back before it is tested
    assert "RAG_JOB_FAILED = 0" not in code[flow_start:]


STEPS = pathlib.Path(__file__).resolve().parents[2] / "Custom-Steps"

# the steps that start a chain: their guard clears RAG_PIPELINE_FAILED
CHAIN_STARTERS = {"RAG - List Documents", "RAG - Purge Documents",
                  "RAG - Retrieve Context"}


@pytest.mark.parametrize("path", sorted(STEPS.glob("RAG - *.step")),
                         ids=lambda path: path.stem)
def test_a_failing_step_sets_the_job_flag_and_no_step_clears_it(path):
    code = json.loads(path.read_text(encoding="utf-8"))["templates"]["SAS"]
    status = re.search(r"%macro (_\w+_status);(.*?)%mend \1;", code, re.S)
    assert status, f"{path.stem} has no _status macro"
    body = status.group(2)
    start = body.index("_state. EQ fail %then %do;")
    fail_branch = body[start:body.index("%return;", start)]
    assert "%let RAG_PIPELINE_FAILED = 1;" in fail_branch
    # declared before it is set: inside a macro %let alone would create a
    # local variable that is gone when the macro ends
    assert fail_branch.index("%global RAG_JOB_FAILED;") \
        < fail_branch.index("%let RAG_JOB_FAILED = 1;") \
        < fail_branch.index("%if &RAG_ABORT_ON_ERROR. NE 0")
    # the only assignment in the whole step
    assert re.findall(r"RAG_JOB_FAILED\s*=\s*(\S+)", code) == ["1;"]
    # why the job cannot read RAG_PIPELINE_FAILED: these three clear it
    guard = re.search(r"%macro (_\w+_guard);(.*?)%mend \1;", code, re.S)
    clears = "%let RAG_PIPELINE_FAILED = 0;" in guard.group(2)
    assert clears == (path.stem in CHAIN_STARTERS)


def test_all_nine_steps_are_checked_for_the_job_flag():
    assert len(list(STEPS.glob("RAG - *.step"))) == 9


def test_the_job_wrapper_keeps_the_generated_code_whole():
    from rag_core.registration import (JOB_EPILOGUE, JOB_PROLOGUE,
                                       scheduled_job_code)
    flow_code = "%let a = 1;\r\ndata _null_;\r\n    abort 42;\r\nrun;\r\n\r\n"
    code = scheduled_job_code(flow_code)
    assert code == JOB_PROLOGUE + flow_code.rstrip("\r\n") + "\n" + JOB_EPILOGUE
    # the last generated statement is terminated before the epilogue begins
    assert "run;\n\n/*" in code


def test_the_job_of_a_registration_without_tls_verification_is_the_same_job():
    """The TLS setting of 2.3.0 decides the score code of the retrieval model
    and its tag. The job is the flow, and is wrapped whatever the setting."""
    from rag_core.registration import ViyaClient, register_setup

    def job_code(ssl_verify):
        stub = _StubViya()
        result = register_setup(
            ViyaClient("https://viya.example", "token", session=stub),
            dict(SETTINGS, SSL_VERIFY=ssl_verify),
            TEMPLATE.read_text(encoding="utf-8"), [],
            "/SAS Agentic AI Accelerator/RAG", "HR", log=lambda *_: None,
            flow_path="/Users/x/My Folder/RAG-Example.flw")
        assert result["ssl_verify"] is ssl_verify
        assert result["job_id"] == "job-new"
        return _stored_job_code(stub)

    assert job_code(False) == job_code(True)
    assert job_code(False).startswith("%let RAG_ABORT_ON_ERROR = 0;\n")
    assert job_code(False).rstrip().endswith("%_rag_job_outcome;")


def test_a_job_that_is_refused_still_says_what_the_model_holds():
    """The job is the last request of a registration. When the service
    refuses it, the model exists and holds its score code - which the
    exception has to say (2.3.0) - and what was refused was the wrapped
    code, not the flow as generated."""
    from rag_core.registration import SCORE_CODE_STORED, UnexpectedStatus

    class _NoJobs(_StubViya):
        def request(self, method, url, **kwargs):
            reply = super().request(method, url, **kwargs)
            if "/jobDefinitions/definitions" in url:
                return _Reply(403, {"message": "no"})
            return reply

    stub = _NoJobs()
    with pytest.raises(UnexpectedStatus) as caught:
        _register(stub, flow_path="/Users/x/My Folder/RAG-Example.flw")
    assert caught.value.status_code == 403
    assert caught.value.registration["score_code"] == SCORE_CODE_STORED
    assert caught.value.registration["model_id"] == "model-new"
    assert _stored_job_code(stub).startswith("%let RAG_ABORT_ON_ERROR = 0;\n")


def test_a_failing_register_setup_sets_the_job_flag_before_it_says_anything():
    """The fail branch of this step is longer than the others since 2.3.0:
    it says what the stored score code does about TLS before it aborts. The
    flag is set before all of that, and the macro that writes the WARNING
    lines leaves both flags alone."""
    code = json.loads((STEPS / "RAG - Register Setup.step").read_text(
        encoding="utf-8"))["templates"]["SAS"]
    status = code[code.index("%macro _rgrg_status;"):
                  code.index("%mend _rgrg_status;")]
    fail = status[status.index("%if &_rgrg_state. EQ fail %then %do;"):]
    positions = [fail.index(marker) for marker in (
        "%let RAG_PIPELINE_FAILED = 1;", "%let RAG_JOB_FAILED = 1;",
        'putLog "ERROR: RAG - Register Setup failed:',
        "%_rgrg_tls_warnings;", "abort 42;", "%return;")]
    assert positions == sorted(positions)
    warnings = code[code.index("%macro _rgrg_tls_warnings;"):
                    code.index("%mend _rgrg_tls_warnings;")]
    assert "RAG_JOB_FAILED" not in warnings
    assert "RAG_PIPELINE_FAILED" not in warnings


def _newest_changelog(path) -> str:
    """The newest entry of the changelog on a step's About tab."""
    def find(node):
        if isinstance(node, dict):
            if node.get("id") == "textChangelog":
                return node["text"]
            node = list(node.values())
        if isinstance(node, list):
            for value in node:
                found = find(value)
                if found:
                    return found
        return None
    step = json.loads(path.read_text(encoding="utf-8"))
    return find(json.loads(step["ui"])).split("\n* Version: ")[0]


@pytest.mark.parametrize("path", sorted(STEPS.glob("RAG - *.step")),
                         ids=lambda path: path.stem)
def test_the_about_tab_of_a_step_names_the_job_flag(path):
    """A job only ends as failed when the steps it runs set the flag, and
    the About tab is where a site sees which step it has."""
    lines = _newest_changelog(path).split("\n      - ")
    assert "(11OCT2026)" in lines[0]
    flag = [line for line in lines
            if line.startswith("A step that fails also sets RAG_JOB_FAILED")]
    assert len(flag) == 1
    assert "a flag no step clears" in flag[0]
    # what the generated job holds is said by the step that generates it
    job = [line for line in lines
           if line.startswith("The job generated from a Studio Flow")]
    assert len(job) == (1 if path.stem == "RAG - Register Setup" else 0)
    for line in job:
        assert "RAG_ABORT_ON_ERROR = 0" in line and "sets SYSCC" in line
        assert "not part of the flow" in line
    # nothing of it has run in SAS yet, and the text says so
    for line in flag + job:
        assert "Not run in SAS" in line


def test_regenerating_updates_the_existing_job_definition():
    stub = _StubViya(existing_job=True)
    result = _register(stub, flow_path="/Users/x/My Folder/RAG-Example.flw")
    assert result["job_id"] == "job-old"
    assert any(method == "PUT" and "job-old" in url
               for method, url, _p, _b in stub.calls)


def test_no_flow_means_no_job():
    stub = _StubViya()
    result = _register(stub)
    assert result["job_id"] == ""
    assert not any("/jobDefinitions/" in url for _m, url, _p, _b in stub.calls)


def test_a_created_id_is_read_from_either_response_shape():
    """Model Manager answers a model POST with a collection wrapper, the other
    services with the resource itself (both verified live)."""
    from rag_core.registration import ViyaClient
    assert ViyaClient.created_id({"id": "direct"}) == "direct"
    assert ViyaClient.created_id({"count": 1, "items": [{"id": "wrapped"}]}) == "wrapped"
    with pytest.raises(RuntimeError, match="did not return the id"):
        ViyaClient.created_id({"count": 0, "items": []})


def test_updating_a_model_sends_the_id_in_the_body_too():
    """Without it the service answers 404 as though the model did not exist."""
    stub = _StubViya(existing_model=True)
    _register(stub)
    body = next(body for method, url, _p, body in stub.calls
                if method == "PUT" and "/modelRepository/models/" in url)
    assert body["id"] == "model-old"


def test_a_created_model_belongs_to_the_project_version():
    """Without projectVersionId the model cannot be updated later: the service
    answers 500 "the model has to belong to either a project version or
    folder" (verified live)."""
    stub = _StubViya()
    _register(stub)
    body = next(body for method, url, _p, body in stub.calls
                if method == "POST" and url.endswith("/modelRepository/models"))
    assert body["projectVersionId"] == "ver-1"


def test_updating_a_model_keeps_what_the_service_set():
    stub = _StubViya(existing_model=True)
    _register(stub)
    body = next(body for method, url, _p, body in stub.calls
                if method == "PUT" and "/modelRepository/models/" in url)
    assert body["somethingTheServiceSet"] == "keep me"
    assert body["projectVersionId"] == "ver-1"


def test_an_existing_artifact_has_its_content_replaced():
    """A second POST would 409: the files service allows one name per folder."""
    stub = _StubViya(existing_model=True)     # the stub lists retrieve_context.py
    _register(stub)
    assert any(method == "PUT" and url.endswith("/files/files/old/content")
               for method, url, _p, _b in stub.calls)
    assert not any(method == "DELETE" for method, _u, _p, _b in stub.calls)


def test_tags_are_applied_by_the_update_that_follows_a_create():
    """Model Manager drops tags on create and keeps them only on a subsequent
    update (verified live), so registration always ends with one."""
    stub = _StubViya()
    _register(stub)
    put = next(body for method, url, _p, body in stub.calls
               if method == "PUT" and "/modelRepository/models/model-new" in url)
    assert "pgvector" in put["tags"] and "all_minilm_l6_v2" in put["tags"]


def test_regenerating_a_job_sends_its_id_in_the_body():
    """The service otherwise reports "Job definition IDs do not match"."""
    stub = _StubViya(existing_job=True)
    _register(stub, flow_path="/Users/x/My Folder/RAG-Example.flw")
    body = next(body for method, url, _p, body in stub.calls
                if method == "PUT" and "/jobDefinitions/definitions/" in url)
    assert body["id"] == "job-old"


# ---------------------------------------------------------------------------
# SSL_VERIFY: the one optional name of the block
# ---------------------------------------------------------------------------
def _block(source):
    """The lines between the two markers of a manifested copy."""
    lines = source.splitlines()
    start = next(i for i, line in enumerate(lines)
                 if line.startswith(manifest.MANIFEST_START))
    end = next(i for i, line in enumerate(lines)
               if i > start and line.startswith(manifest.MANIFEST_END))
    return lines[start + 1:end]


def _old_template():
    """retrieve_context.py as a content root deployed before the setting
    holds it: no SSL_VERIFY line in the block."""
    template = TEMPLATE.read_text(encoding="utf-8")
    assert template.count("\nSSL_VERIFY = True\n") == 1
    return template.replace("\nSSL_VERIFY = True\n", "\n")


def test_the_setting_is_written_as_true_when_nothing_asks_for_anything_else():
    """A caller that does not know the setting - every caller before it
    existed - gets a model that verifies, with the line the template has."""
    rendered = render_retrieval_model(TEMPLATE.read_text(encoding="utf-8"), SETTINGS)
    assert _block(rendered).count("SSL_VERIFY = True") == 1
    assert manifest.manifested_ssl_verify(rendered) is True


def test_the_literal_false_writes_false():
    rendered = render_retrieval_model(TEMPLATE.read_text(encoding="utf-8"),
                                      dict(SETTINGS, SSL_VERIFY=False))
    block = _block(rendered)
    assert block.count("SSL_VERIFY = False") == 1
    assert not [line for line in block if line.startswith("SSL_VERIFY = True")]
    assert manifest.manifested_ssl_verify(rendered) is False
    compile(rendered, "manifested.py", "exec")


@pytest.mark.parametrize("value", [True, None, "", "False", "false", "no", "0",
                                   0, 0.0, [], 1, "True"])
def test_only_the_literal_false_writes_false(value):
    """The failure direction is the safe one, as in the Builder's renderer
    (`settings[key] === false`): a word, a zero or an empty value is not a
    request to stop verifying."""
    rendered = render_retrieval_model(TEMPLATE.read_text(encoding="utf-8"),
                                      dict(SETTINGS, SSL_VERIFY=value))
    assert _block(rendered).count("SSL_VERIFY = True") == 1
    assert manifest.manifested_ssl_verify(rendered) is True


def test_the_setting_is_the_last_line_of_the_block():
    """Strings, numbers, then the optional boolean - the order the Builder
    writes, so that the two renderers produce the same text."""
    rendered = render_retrieval_model(TEMPLATE.read_text(encoding="utf-8"),
                                      dict(SETTINGS, SSL_VERIFY=False))
    names = [line.split(" = ")[0] for line in _block(rendered)]
    assert names == (list(manifest.MANIFEST_STRINGS) + list(manifest.MANIFEST_NUMBERS)
                     + list(manifest.MANIFEST_OPTIONAL_BOOLEANS))


@pytest.mark.parametrize("asked", [True, False])
def test_a_template_without_the_setting_is_left_without_it(asked):
    """A content root deployed before the setting holds a template that has
    neither the constant nor the code that reads it. It still manifests, the
    line is not invented, and the copy verifies whatever was asked for."""
    old = _old_template()
    assert manifest.manifest_block_has(old, "SSL_VERIFY") is False
    rendered = render_retrieval_model(old, dict(SETTINGS, SSL_VERIFY=asked))
    assert not [line for line in rendered.splitlines()
                if line.startswith("SSL_VERIFY")]
    assert manifest.manifested_ssl_verify(rendered) is True
    # everything else is manifested as before
    assert [line.split(" = ")[0] for line in _block(rendered)] == (
        list(manifest.MANIFEST_STRINGS) + list(manifest.MANIFEST_NUMBERS))
    assert 'COLLECTION = "rag_hr_policies_v1"' in rendered


def test_the_caller_can_find_out_whether_the_setting_was_written():
    template = TEMPLATE.read_text(encoding="utf-8")
    assert manifest.manifest_block_has(template, "SSL_VERIFY") is True
    assert manifest.manifest_block_has(_old_template(), "SSL_VERIFY") is False
    assert manifest.manifest_block_has("print('not the template')\n", "SSL_VERIFY") is False
    # every name of the block answers, not only this one
    assert manifest.manifest_block_has(template, "COLLECTION") is True
    assert manifest.manifest_block_has(template, "SSL") is False


def test_a_line_outside_the_block_is_not_the_setting():
    """Only the block is rewritten, so only the block counts: a line of the
    same name further down would not be replaced and must not be read as
    'this template has the setting'."""
    outside = _old_template() + "\nSSL_VERIFY = False\n"
    assert manifest.manifest_block_has(outside, "SSL_VERIFY") is False
    assert manifest.manifested_ssl_verify(outside) is True
    rendered = render_retrieval_model(outside, dict(SETTINGS, SSL_VERIFY=False))
    assert not [line for line in _block(rendered) if line.startswith("SSL_VERIFY")]


@pytest.mark.parametrize("line,verifies", [
    ("SSL_VERIFY = False", False),
    ("SSL_VERIFY=False", False),
    ("SSL_VERIFY = False  # switched off for a test system", False),
    ("SSL_VERIFY = True", True),
    ('SSL_VERIFY = "False"', True),
    ("SSL_VERIFY = false", True),
    ("SSL_VERIFY = 0", True),
    ("SSL_VERIFY = Falsey", True),
    ("# SSL_VERIFY = False", True),
])
def test_what_a_stored_copy_does_is_read_from_its_text(line, verifies):
    """The tag follows the score code that is stored. retrieve_context.py
    switches verification off for the Python literal False and for nothing
    else, so nothing else is read as off here."""
    source = TEMPLATE.read_text(encoding="utf-8").replace(
        "\nSSL_VERIFY = True\n", "\n" + line + "\n")
    assert manifest.manifested_ssl_verify(source) is verifies


def test_manifesting_with_verification_off_is_idempotent_and_reversible():
    template = TEMPLATE.read_text(encoding="utf-8")
    off = render_retrieval_model(template, dict(SETTINGS, SSL_VERIFY=False))
    assert render_retrieval_model(off, dict(SETTINGS, SSL_VERIFY=False)) == off
    # a copy that was manifested with verification off is a template like any
    # other: manifested again without the request, it verifies again
    assert render_retrieval_model(off, SETTINGS) == render_retrieval_model(
        template, SETTINGS)


def test_a_false_setting_survives_the_way_the_step_assembles_its_settings():
    """settings_from_inventory drops blank extras. False is not a blank: a
    filter on truthiness would turn 'do not verify' into 'no value', and the
    model would be registered verifying without anybody having asked."""
    rows = [{"doc_id": "d1", "config_json": json.dumps({"collection": "c"})}]
    assert settings_from_inventory(rows, {"SSL_VERIFY": False})["SSL_VERIFY"] is False
    assert settings_from_inventory(rows, {"SSL_VERIFY": True})["SSL_VERIFY"] is True
    assert "SSL_VERIFY" not in settings_from_inventory(rows, {})
