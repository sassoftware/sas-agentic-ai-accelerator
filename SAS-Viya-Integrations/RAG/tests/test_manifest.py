# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Register Setup's artifacts: the manifested model and the governance files.

The manifested model is the one that gets deployed, so the tests here are
about refusing to produce a broken one rather than about formatting.
"""
import json
import pathlib

import pytest

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

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs.get("params") or {},
                           kwargs.get("json")))
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
            return _Reply(200, {"items": [{"id": "proj-1"}]})
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
                items = ([{"id": "model-old", "projectId": "proj-1"}]
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
    assert body["code"].startswith("data _null_")


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
