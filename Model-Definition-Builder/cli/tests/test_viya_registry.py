# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Pure (no-network) parts of the register/publish path: attribute enrichment
and the content manifest with its load-bearing roles.

The tests that monkeypatch sasctl are skipped when the optional [viya] extra
is not installed (e.g. the base CI job) - the registry module itself imports
sasctl lazily, so the non-sasctl tests here still run everywhere."""
import pytest

from mdb.core.manifest import load_manifest
from mdb.viya.registry import (
    PROJECT_META, REPOSITORY, build_model_attributes, content_files,
    ensure_repository_and_project, project_variables, register_model, unregister_model,
)


def test_attributes_enrichment(repo_root, fact_sheet):
    folder = repo_root / "LLM-Definitions" / "gpt_41_mini"
    manifest = load_manifest(folder)
    from mdb.core.facts import read_row
    row = read_row(fact_sheet, "gpt_41_mini")
    attrs = build_model_attributes(manifest, folder, row, "https://viya-host/llm")
    assert attrs["name"] == "gpt_41_mini"
    assert attrs["endPoint"] == "https://viya-host/llm/gpt_41_mini/gpt_41_mini"
    assert attrs["llmModelType"] == "GPT"  # derived from the model id, no longer hardcoded
    assert attrs["provider"] == "OpenAI"
    assert attrs["deploymentId"] == manifest.provider.model_version
    assert attrs["eventProbVar"] == "response"
    # per-token costs are carried precisely (not just the averaged costPerCall)
    assert abs(attrs["inputTokenCount"] - 4e-07) < 1e-12
    assert abs(attrs["outputTokenCount"] - 1.6e-06) < 1e-12
    # (input 0.0000004 + output 0.0000016) / 2
    assert abs(attrs["costPerCall"] - 1e-06) < 1e-12
    assert attrs["toolVersion"] == "3.11"


def test_family_derivation_covers_the_fleet(repo_root):
    from mdb.viya.registry import _llm_model_type
    cases = {
        ("LLM-Definitions", "claude_sonnet_4_5"): "Claude",
        ("LLM-Definitions", "gpt_41_mini"): "GPT",
        ("LLM-Definitions", "phi_35_mini"): "Phi",
        ("LLM-Definitions", "qwen_25_7b"): "Qwen",
    }
    for (kinddir, model_id), expected in cases.items():
        folder = repo_root / kinddir / model_id
        if not (folder / "definition.yaml").is_file():
            continue
        assert _llm_model_type(load_manifest(folder)) == expected


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
    # load-bearing Model Manager content-role conventions
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
    def __init__(self, status_code=201, body=None):
        self.status_code = status_code
        self.text = ""
        self._body = body if body is not None else {"id": "repo1", "folderId": "folder1"}

    def json(self):
        return self._body


class _FakeSession:
    def __init__(self, status_code=201):
        self.posts = []
        self._status = status_code

    def post(self, url, data=None, headers=None, files=None):
        self.posts.append(url)
        return _FakeResponse(self._status)


def _patch_mr(monkeypatch, *, repo, project, sink):
    pytest.importorskip("sasctl")
    from sasctl.services import model_repository as mr
    monkeypatch.setattr(mr, "get_repository", lambda *a, **k: repo)
    monkeypatch.setattr(mr, "get_project", lambda *a, **k: project)
    monkeypatch.setattr(mr, "create_project",
                        lambda **kw: sink.append(kw) or type("P", (), {"id": "pid"})())


def test_ensure_creates_missing_repository_and_project(core, monkeypatch):
    created_projects = []
    _patch_mr(monkeypatch, repo=None, project=None, sink=created_projects)
    session = _FakeSession()
    result = ensure_repository_and_project(session, "embedding", core, "me@example.com")
    assert result.created == ["repository 'LLM Repository'", "project 'Embedding Model Project'"]
    assert result.repository_id == "repo1" and result.repository_folder_id == "folder1"
    assert result.project_id == "pid"
    assert session.posts == ["/modelRepository/repositories"]
    assert created_projects[0]["function"] == "Embedding"
    # embedding target points at a variable the model actually emits, not 'response'
    assert created_projects[0]["targetVariable"] == "embedding"
    assert created_projects[0]["modelResponsibleParty"] == "me@example.com"


def test_ensure_is_noop_when_everything_exists(core, monkeypatch):
    created_projects = []
    _patch_mr(monkeypatch, repo=object(), project=object(), sink=created_projects)
    session = _FakeSession()
    result = ensure_repository_and_project(session, "llm", core, "me")
    assert result.created == []
    assert session.posts == []
    assert created_projects == []


def test_ensure_repository_create_race_is_not_a_rights_error(core, monkeypatch):
    # The create POST fails (lost the race), but a re-fetch shows the repo now
    # exists - so it must NOT raise the 'administrator rights' error.
    pytest.importorskip("sasctl")
    from sasctl.services import model_repository as mr
    calls = {"n": 0}

    def _get_repo(*a, **k):
        calls["n"] += 1
        return None if calls["n"] == 1 else object()  # absent first, present on re-fetch

    monkeypatch.setattr(mr, "get_repository", _get_repo)
    monkeypatch.setattr(mr, "get_project", lambda *a, **k: object())
    result = ensure_repository_and_project(_FakeSession(status_code=409), "llm", core, "me")
    assert result.created == []  # nothing we can claim we created


def test_ensure_repository_rights_error_when_still_absent(core, monkeypatch):
    pytest.importorskip("sasctl")
    from sasctl.services import model_repository as mr
    monkeypatch.setattr(mr, "get_repository", lambda *a, **k: None)  # stays absent
    monkeypatch.setattr(mr, "get_project", lambda *a, **k: object())
    with pytest.raises(RuntimeError, match="administrator"):
        ensure_repository_and_project(_FakeSession(status_code=403), "llm", core, "me")


def test_unregister_deletes_when_present(monkeypatch):
    pytest.importorskip("sasctl")
    from sasctl.services import model_repository as mr
    deleted = []
    monkeypatch.setattr(mr, "get_model", lambda mid: type("M", (), {"id": "abc123"})())
    monkeypatch.setattr(mr, "delete_model", lambda mid: deleted.append(mid))
    assert unregister_model(None, "all_minilm_l6_v2") == "deleted"
    assert deleted == ["abc123"]


def test_unregister_absent_when_missing(monkeypatch):
    pytest.importorskip("sasctl")
    from sasctl.services import model_repository as mr
    monkeypatch.setattr(mr, "get_model", lambda mid: None)
    monkeypatch.setattr(mr, "delete_model",
                        lambda *a: (_ for _ in ()).throw(AssertionError("should not delete")))
    assert unregister_model(None, "nope") == "absent"


# --- register_model create / skip / update (load-bearing content roles) -------

def _patch_register_env(monkeypatch, *, existing_model):
    pytest.importorskip("sasctl")
    from mdb.viya import registry
    from sasctl.services import model_repository as mr
    monkeypatch.setattr(mr, "get_repository", lambda *a, **k: object())
    monkeypatch.setattr(mr, "get_project", lambda *a, **k: object())
    monkeypatch.setattr(mr, "get_model", lambda *a, **k: existing_model)
    monkeypatch.setattr(registry.time, "sleep", lambda *a, **k: None)
    return mr


def test_register_model_create_uploads_content_with_roles(repo_root, fact_sheet, monkeypatch):
    from mdb.viya import registry
    from mdb.core.facts import read_row
    folder = repo_root / "LLM-Definitions" / "gpt_41_mini"
    manifest = load_manifest(folder)
    mr = _patch_register_env(monkeypatch, existing_model=None)
    uploaded = []
    monkeypatch.setattr(mr, "create_model", lambda **kw: type("M", (), {"id": "mid"})())
    monkeypatch.setattr(mr, "add_model_content",
                        lambda model, handle, name=None, role=None: uploaded.append((name, role)))
    monkeypatch.setattr(registry, "_put_tags", lambda *a, **k: None)

    result = register_model(None, manifest, folder, read_row(fact_sheet, "gpt_41_mini"),
                            "https://viya-host/llm")
    assert result.action == "created"
    by_name = dict(uploaded)
    # the roles the SCR build depends on
    assert uploaded[0][0] == "gpt_41_mini.py" and by_name["gpt_41_mini.py"] == "score"
    assert by_name["requirements.json"] == "python pickle"
    assert by_name["options.json"] == "documentation"
    assert by_name["definition.yaml"] == "documentation"
    assert by_name["inputVar.json"] is None and by_name["outputVar.json"] is None


def test_register_model_skips_when_present_and_not_update(repo_root, monkeypatch):
    folder = repo_root / "LLM-Definitions" / "gpt_41_mini"
    manifest = load_manifest(folder)
    mr = _patch_register_env(monkeypatch, existing_model=type("M", (), {"id": "mid"})())
    monkeypatch.setattr(mr, "add_model_content",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no upload on skip")))
    result = register_model(None, manifest, folder, None, "https://viya-host/llm", update=False)
    assert result.action == "skipped"


class _UpdateSession:
    def __init__(self):
        self.content_posts = []
        self.puts = []
        self.deleted_vars = []

    def get(self, url, data=None, headers=None):
        if "variables" in url:
            return _FakeResponse(200, {"items": [{"id": "v1"}, {"id": "v2"}]})
        return _FakeResponse(200, {})

    def delete(self, url, data=None, headers=None):
        self.deleted_vars.append(url)
        return _FakeResponse(204)

    def post(self, url, data=None, headers=None, files=None):
        if "contents" in url:
            self.content_posts.append(files["files"][0])  # uploaded name
        return _FakeResponse(201)

    def put(self, url, data=None, headers=None):
        self.puts.append(headers.get("If-Match"))
        return _FakeResponse(200)


def test_register_model_update_replaces_content_in_place(repo_root, fact_sheet, monkeypatch):
    from mdb.viya import registry
    from mdb.core.facts import read_row
    folder = repo_root / "LLM-Definitions" / "gpt_41_mini"
    manifest = load_manifest(folder)
    mr = _patch_register_env(monkeypatch, existing_model=type("M", (), {"id": "mid"})())
    details = type("D", (), {"_headers": {"ETag": "etag-1"},
                             "items": lambda self: {"id": "mid", "name": "gpt_41_mini"}.items()})()
    monkeypatch.setattr(mr, "get_model_details", lambda *a, **k: details)
    session = _UpdateSession()
    result = register_model(session, manifest, folder, read_row(fact_sheet, "gpt_41_mini"),
                            "https://viya-host/llm", update=True)
    assert result.action == "updated"
    # every content is replaced via onConflict=update, and the attribute PUT uses the ETag
    assert "gpt_41_mini.py" in session.content_posts
    assert "requirements.json" in session.content_posts
    assert session.puts == ["etag-1"]
    # existing variables are cleared before the inputVar/outputVar re-import, so
    # an --update does not duplicate them
    assert len(session.deleted_vars) == 2
