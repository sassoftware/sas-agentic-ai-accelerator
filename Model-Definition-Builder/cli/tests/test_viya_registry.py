# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Pure (no-network) parts of the register/publish path: attribute enrichment
and the content manifest with its load-bearing roles."""
from mdb.core.manifest import load_manifest
from mdb.viya.registry import (
    PROJECT_META, REPOSITORY, build_model_attributes, content_files,
    ensure_repository_and_project, project_variables,
)


def test_attributes_enrichment_matches_register_script(repo_root, fact_sheet):
    folder = repo_root / "LLM-Definitions" / "gpt_41_mini"
    manifest = load_manifest(folder)
    from mdb.core.facts import read_row
    row = read_row(fact_sheet, "gpt_41_mini")
    attrs = build_model_attributes(manifest, folder, row, "https://viya-host/llm")
    assert attrs["name"] == "gpt_41_mini"
    assert attrs["endPoint"] == "https://viya-host/llm/gpt_41_mini/gpt_41_mini"
    assert attrs["llmModelType"] == "GPT"
    assert attrs["provider"] == "OpenAI"
    # (input 0.0000004 + output 0.0000016) / 2
    assert abs(attrs["costPerCall"] - 1e-06) < 1e-12
    assert attrs["toolVersion"] == "3.11"


def test_seconds_cost_type(repo_root):
    folder = repo_root / "Embedding-Definitions" / "titan_embed_text_v2"
    manifest = load_manifest(folder)
    attrs = build_model_attributes(
        manifest, folder, {"cost_type": "Seconds", "second_cost": "0.00004", "provider": "AWS Bedrock"},
        "https://viya-host/llm",
    )
    assert attrs["costPerCall"] == 0.00004


def test_content_files_roles(repo_root):
    folder = repo_root / "LLM-Definitions" / "gpt_5_mini"
    manifest = load_manifest(folder)
    files = content_files(manifest, folder)
    by_name = {name: role for _, name, role in files}
    # load-bearing conventions from register-LLMs.py
    assert by_name["gpt_5_mini.py"] == "score"
    assert by_name["requirements.json"] == "python pickle"
    assert by_name["options.json"] == "documentation"
    assert by_name["outputVar.json"] is None and by_name["inputVar.json"] is None
    # registered models carry their manifest for the CLI/web round-trip
    assert by_name["definition.yaml"] == "documentation"
    assert by_name["Model-Card.md"] == "documentation"
    # every listed file exists on disk
    assert all(path.is_file() for path, _, _ in files)


# --- environment bootstrap (mdb setup / auto-ensure on register) -------------

def test_project_meta_matches_setup_script():
    assert REPOSITORY == "LLM Repository"
    assert PROJECT_META["llm"]["project"] == "LLM Model Project"
    assert PROJECT_META["llm"]["function"] == "LLM"
    assert PROJECT_META["llm"]["tags"] == ["LLM-Models", "SCR-Definitions", "Python"]
    assert PROJECT_META["embedding"]["project"] == "Embedding Model Project"
    assert PROJECT_META["embedding"]["function"] == "Embedding"
    assert PROJECT_META["embedding"]["tags"] == ["Embedding-Models", "SCR-Definitions", "Python"]


def test_project_variables_carry_roles(core):
    for kind in ("llm", "embedding"):
        variables = project_variables(core, kind)
        assert {v["role"] for v in variables} == {"input", "output"}
        assert all(v.get("name") for v in variables)


class _FakeResponse:
    def __init__(self, status_code=201):
        self.status_code = status_code
        self.text = ""


class _FakeSession:
    def __init__(self):
        self.posts = []

    def post(self, url, data=None, headers=None):
        self.posts.append(url)
        return _FakeResponse(201)


def _patch_mr(monkeypatch, *, repo, project, sink):
    from sasctl.services import model_repository as mr
    monkeypatch.setattr(mr, "get_repository", lambda *a, **k: repo)
    monkeypatch.setattr(mr, "get_project", lambda *a, **k: project)
    monkeypatch.setattr(mr, "create_project",
                        lambda **kw: sink.append(kw) or type("P", (), {"id": "pid"})())


def test_ensure_creates_missing_repository_and_project(core, monkeypatch):
    created_projects = []
    _patch_mr(monkeypatch, repo=None, project=None, sink=created_projects)
    session = _FakeSession()
    created = ensure_repository_and_project(session, "embedding", core, "me@example.com")
    assert created == ["repository 'LLM Repository'", "project 'Embedding Model Project'"]
    assert session.posts == ["/modelRepository/repositories"]
    assert created_projects[0]["function"] == "Embedding"
    assert created_projects[0]["targetVariable"] == "response"
    assert created_projects[0]["modelResponsibleParty"] == "me@example.com"


def test_ensure_is_noop_when_everything_exists(core, monkeypatch):
    created_projects = []
    _patch_mr(monkeypatch, repo=object(), project=object(), sink=created_projects)
    session = _FakeSession()
    created = ensure_repository_and_project(session, "llm", core, "me")
    assert created == []
    assert session.posts == []
    assert created_projects == []
