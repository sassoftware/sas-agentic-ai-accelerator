# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""`mdb content-deploy` against a SAS Viya that lives in memory.

What the command promises, each as a test: a folder that is empty gets
everything; a second run changes nothing; a changed file, step or job
definition is put right and nothing else is touched; a custom step is updated
IN PLACE and keeps its id, because flows reference a step by id; an upload
that is answered 409 right after the old copy was deleted is tried again; a
dry run writes nothing; a report is not deployed; what is not in the manifest
is left alone and named; nothing is removed but the old copy of a file that
is replaced.

No request here reaches a server, so none of this says how SAS Viya answers -
the fake answers the way the earlier scripts and the Prompt Builder saw it
answer.
"""
import io
import json
from types import SimpleNamespace

import pytest
from rich.console import Console
from typer.testing import CliRunner

from content_fakes import CONTENT_ROOT, STEP, STEP_NAME, Viya, make_repo
from mdb import cli
from mdb.core import content
from mdb.core.content import load_manifest
from mdb.viya import content as deployer
from mdb.viya.content import CREATED, EQUAL, FAILED, NOT_DEPLOYED, UPDATED, deploy

STEP_PATH = f"RAG/steps/{STEP_NAME}"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(content, "checkout_commit", lambda root: ("abc123", "2026-10-12T09:31:55+02:00"))
    return make_repo(tmp_path)


@pytest.fixture
def manifest(repo):
    return load_manifest(repo)


def _statuses(result) -> dict:
    return {outcome.entry.path: outcome.status for outcome in result.outcomes}


def _no_sleep(seconds):
    raise AssertionError("nothing to wait for")


# ---- an empty folder, and the second run ------------------------------------
def test_an_empty_site_gets_the_whole_folder(manifest):
    viya = Viya()
    result = deploy(viya, manifest, sleep=_no_sleep)
    assert result.failed == [] and result.root_created
    statuses = _statuses(result)
    assert statuses.pop("Prompt Builder/Prompt Builder") == NOT_DEPLOYED
    assert set(statuses.values()) == {CREATED} and len(statuses) == 6 + 7
    assert viya.tree(CONTENT_ROOT) == [
        "Data", "Data/ACCELERATOR_RELEASES.csv", "Prompt Builder", "Prompt Builder/Optimize", "Prompt Builder/UI",
        "RAG", "RAG/rag_core", "RAG/rag_core/__init__.py", "RAG/rag_core/adapters",
        "RAG/rag_core/adapters/__init__.py", "RAG/steps", STEP_PATH, "release.json"]
    # a file as the repository has it, whatever the line ends of the checkout
    assert viya.files[viya.at(f"{CONTENT_ROOT}/RAG/rag_core/__init__.py")[0]] == b"CORE = 1\n"
    # a step is registered with the dataFlows service, not uploaded as a file
    step_uri, = viya.at(f"{CONTENT_ROOT}/{STEP_PATH}")
    assert step_uri.startswith("/dataFlows/steps/") and viya.steps[step_uri]["templates"] == STEP["templates"]
    # a job definition: the program as code, the page as the property `form`
    ui, _ = viya.jobs[viya.at(f"{CONTENT_ROOT}/Prompt Builder/UI")[0]]
    assert ui["properties"] == [{"name": "form", "value": "<!DOCTYPE html>\n<html>ü</html>\n"}]
    assert ui["code"] == "/* SAS templated code goes here */" and ui["type"] == "Compute"
    assert [p["name"] for p in ui["parameters"]] == ["_contextName", "_action"]
    optimize, _ = viya.jobs[viya.at(f"{CONTENT_ROOT}/Prompt Builder/Optimize")[0]]
    assert optimize["code"] == "%put optimize;\n%put done;\n" and optimize["properties"] == []
    # the two files mdb writes
    release = json.loads(viya.files[viya.at(f"{CONTENT_ROOT}/release.json")[0]])
    assert release["commit"] == "abc123" and len(release["entries"]) == len(manifest.all_entries())
    assert viya.files[viya.at(f"{CONTENT_ROOT}/Data/ACCELERATOR_RELEASES.csv")[0]] == \
        content.release_table_bytes(manifest)
    # and the report: not this command's to bring
    report = next(o for o in result.outcomes if o.entry.kind == "report")
    assert report.note == "not on the site - it comes with the package"
    assert viya.at(f"{CONTENT_ROOT}/Prompt Builder/Prompt Builder") == []


def test_a_second_run_finds_everything_equal_and_writes_nothing(manifest):
    viya = Viya()
    deploy(viya, manifest)
    before = (dict(viya.files), dict(viya.steps), dict(viya.jobs), viya.tree(CONTENT_ROOT))
    viya.calls.clear()
    result = deploy(viya, manifest, sleep=_no_sleep)
    statuses = _statuses(result)
    assert statuses.pop("Prompt Builder/Prompt Builder") == NOT_DEPLOYED
    assert set(statuses.values()) == {EQUAL} and not result.root_created
    assert viya.wrote() == []
    assert (dict(viya.files), dict(viya.steps), dict(viya.jobs), viya.tree(CONTENT_ROOT)) == before
    assert result.unlisted == []


def test_a_test_root_below_other_folders_is_created_with_them(manifest):
    viya = Viya()
    viya.make_folder("/Users/me/My Folder")
    root = "/Users/me/My Folder/ZZ_VERIFY_R17/root"
    result = deploy(viya, manifest, root)
    assert result.failed == [] and result.root_created
    assert viya.folder_id(root) and viya.folder_id(CONTENT_ROOT) is None
    assert STEP_PATH in viya.tree(root)


# ---- what changed is put right, in place ------------------------------------
def test_a_step_is_updated_in_place_and_keeps_its_id(manifest, repo):
    """Flows hold /dataFlows/steps/<id>. Deleted and posted again a step gets
    a new id and every flow that uses it has lost it."""
    viya = Viya()
    deploy(viya, manifest)
    step_uri, = viya.at(f"{CONTENT_ROOT}/{STEP_PATH}")
    (repo / f"src/{STEP_NAME}").write_text(
        json.dumps({**STEP, "templates": {"SAS": "%put embed, fixed;\n"}, "ui": "{\"pages\": [1]}"}), encoding="utf-8")
    viya.calls.clear()
    result = deploy(viya, manifest)
    outcome = next(o for o in result.outcomes if o.entry.path == STEP_PATH)
    assert outcome.status == UPDATED
    assert outcome.note == f"templates, ui; id {step_uri.rsplit('/', 1)[-1]} kept"
    assert viya.at(f"{CONTENT_ROOT}/{STEP_PATH}") == [step_uri]                 # the same object
    assert viya.steps[step_uri]["templates"] == {"SAS": "%put embed, fixed;\n"}
    # one PUT at the step's own address, and release.json, whose hash of the step changed
    assert ("PUT", step_uri) in viya.wrote()
    assert not [call for call in viya.wrote() if call[1].startswith("/dataFlows/") and call[0] != "PUT"]
    changed = {o.entry.path for o in result.outcomes if o.status == UPDATED}
    assert changed == {STEP_PATH, "release.json"}
    assert _statuses(deploy(viya, manifest))[STEP_PATH] == EQUAL


def test_a_step_that_comes_back_under_another_id_is_a_failure(manifest, repo, monkeypatch):
    viya = Viya()
    deploy(viya, manifest)
    (repo / f"src/{STEP_NAME}").write_text(json.dumps({**STEP, "ui": "{}"}), encoding="utf-8")
    real_put = viya.put

    def put(path, **kwargs):
        response = real_put(path, **kwargs)
        if path.startswith("/dataFlows/steps/"):
            response._body = {**response.json(), "id": "another-id"}
        return response
    monkeypatch.setattr(viya, "put", put)
    outcome = next(o for o in deploy(viya, manifest).outcomes if o.entry.path == STEP_PATH)
    assert outcome.status == FAILED and "answered with the id another-id" in outcome.note


def test_a_job_definition_is_updated_in_place_with_its_etag(manifest, repo):
    viya = Viya()
    deploy(viya, manifest)
    uri, = viya.at(f"{CONTENT_ROOT}/Prompt Builder/UI")
    live, etag = viya.jobs[uri]
    viya.jobs[uri] = ({**live, "createdBy": "the site"}, etag)        # what the definition holds beside
    (repo / "src/index.html").write_text("<html>a newer build</html>", encoding="utf-8")
    result = deploy(viya, manifest)
    outcome = next(o for o in result.outcomes if o.entry.path == "Prompt Builder/UI")
    assert (outcome.status, outcome.note) == (UPDATED, "form")
    assert viya.at(f"{CONTENT_ROOT}/Prompt Builder/UI") == [uri]
    body, new_etag = viya.jobs[uri]
    assert body["properties"] == [{"name": "form", "value": "<html>a newer build</html>"}]
    assert body["id"] == uri.rsplit("/", 1)[-1] and body["createdBy"] == "the site" and new_etag != etag
    # a parameter an admin changed on the site is a difference too: the definition is the checkout's
    tuned = json.loads(json.dumps(body))
    tuned["parameters"][0]["defaultValue"] = "Team compute context"
    viya.jobs[uri] = (tuned, new_etag)
    outcome = next(o for o in deploy(viya, manifest).outcomes if o.entry.path == "Prompt Builder/UI")
    assert (outcome.status, outcome.note) == (UPDATED, "parameters")
    assert viya.jobs[uri][0]["parameters"][0]["defaultValue"] == "SAS Studio compute context"


def test_a_changed_file_is_replaced_and_an_equal_one_is_left(manifest, repo):
    viya = Viya()
    deploy(viya, manifest)
    kept, = viya.at(f"{CONTENT_ROOT}/RAG/rag_core/adapters/__init__.py")
    (repo / "src/rag_core/__init__.py").write_bytes(b"CORE = 2\r\n")
    viya.calls.clear()
    result = deploy(viya, manifest, sleep=_no_sleep)
    assert {o.entry.path for o in result.outcomes if o.status == UPDATED} == {"RAG/rag_core/__init__.py", "release.json"}
    uri, = viya.at(f"{CONTENT_ROOT}/RAG/rag_core/__init__.py")
    assert viya.files[uri] == b"CORE = 2\n"
    assert viya.at(f"{CONTENT_ROOT}/RAG/rag_core/adapters/__init__.py") == [kept]
    # the only deletes are of the two files that were replaced in the same step
    assert len([call for call in viya.wrote() if call[0] == "DELETE"]) == 2


def test_a_file_that_was_uploaded_twice_under_one_name_becomes_one(manifest):
    viya = Viya()
    deploy(viya, manifest)
    viya.put_member(f"{CONTENT_ROOT}/RAG/rag_core/__init__.py", "/files/files/", b"CORE = 0\n")
    outcome = next(o for o in deploy(viya, manifest).outcomes if o.entry.path == "RAG/rag_core/__init__.py")
    assert (outcome.status, outcome.note) == (UPDATED, "replaced 2 files of that name")
    uri, = viya.at(f"{CONTENT_ROOT}/RAG/rag_core/__init__.py")
    assert viya.files[uri] == b"CORE = 1\n"


# ---- the 409 after a delete ---------------------------------------------------
def test_an_upload_answered_409_after_the_delete_is_tried_again(manifest, repo):
    """Seen on the test system: POST /files/files answered 409 for a file
    whose old copy had just been deleted, and 201 a minute later. Without the
    repeat the file is missing from the folder."""
    viya = Viya(conflicts=2)
    deploy(viya, manifest)
    (repo / "src/rag_core/__init__.py").write_bytes(b"CORE = 2\n")
    waited = []
    result = deploy(viya, manifest, sleep=waited.append)
    outcome = next(o for o in result.outcomes if o.entry.path == "RAG/rag_core/__init__.py")
    assert outcome.status == UPDATED and outcome.note == "the upload was answered 409 2 time(s) and then taken"
    assert waited[:2] == [2, 5] and result.failed == []
    uri, = viya.at(f"{CONTENT_ROOT}/RAG/rag_core/__init__.py")
    assert viya.files[uri] == b"CORE = 2\n"


def test_an_upload_that_stays_refused_is_reported_as_deleted_and_not_uploaded(manifest, repo):
    viya = Viya(conflicts=99)
    deploy(viya, manifest)
    (repo / "src/rag_core/__init__.py").write_bytes(b"CORE = 2\n")
    waited = []
    result = deploy(viya, manifest, sleep=waited.append)
    failed = {o.entry.path: o.note for o in result.failed}
    assert set(failed) == {"RAG/rag_core/__init__.py", "release.json"}
    assert failed["RAG/rag_core/__init__.py"].startswith(
        "the file was DELETED and NOT uploaded - run the command again (Uploading __init__.py failed after 6 tries: "
        "HTTP 409")
    assert waited[:5] == [2, 5, 10, 20, 30]
    # the run went on: the other entries were still looked at
    assert _statuses(result)[STEP_PATH] == EQUAL
    # and the next run, with the service answering again, puts the file back
    viya._just_deleted.clear()
    result = deploy(viya, manifest, sleep=_no_sleep)
    assert result.failed == [] and _statuses(result)["RAG/rag_core/__init__.py"] == CREATED


def test_an_upload_refused_for_another_reason_is_not_repeated(manifest):
    viya = Viya()
    viya.refuse[("POST", "/files/files")] = 403
    result = deploy(viya, manifest, sleep=_no_sleep)
    assert {o.entry.kind for o in result.failed} == {"file"}
    assert all("HTTP 403" in o.note for o in result.failed)
    assert _statuses(result)[STEP_PATH] == CREATED


# ---- a dry run ----------------------------------------------------------------
def test_a_dry_run_on_an_empty_site_writes_nothing(manifest):
    viya = Viya()
    result = deploy(viya, manifest, dry_run=True)
    statuses = _statuses(result)
    assert statuses.pop("Prompt Builder/Prompt Builder") == NOT_DEPLOYED
    assert set(statuses.values()) == {CREATED} and result.root_created
    assert viya.wrote() == [] and viya.folders == {}


def test_a_dry_run_says_what_a_run_would_change(manifest, repo):
    viya = Viya()
    deploy(viya, manifest)
    (repo / "src/rag_core/__init__.py").write_bytes(b"CORE = 2\n")
    (repo / f"src/{STEP_NAME}").write_text(json.dumps({**STEP, "ui": "{}"}), encoding="utf-8")
    (repo / "src/optimize.sas").write_text("%put other;\n", encoding="utf-8")
    viya.calls.clear()
    result = deploy(viya, manifest, dry_run=True)
    assert {o.entry.path for o in result.outcomes if o.status == UPDATED} == {
        "RAG/rag_core/__init__.py", STEP_PATH, "Prompt Builder/Optimize", "release.json"}
    assert viya.wrote() == []
    assert viya.files[viya.at(f"{CONTENT_ROOT}/RAG/rag_core/__init__.py")[0]] == b"CORE = 1\n"


# ---- what is not in the manifest ----------------------------------------------
def test_what_is_not_in_the_manifest_is_named_and_left_alone(manifest):
    viya = Viya()
    deploy(viya, manifest)
    demo = viya.put_member(f"{CONTENT_ROOT}/RAG/LIVE_DEMO", "/reports/reports/", None)
    viya.put_member(f"{CONTENT_ROOT}/RAG/generated/RAG Ingest - Demo", "/jobDefinitions/definitions/", {"code": "x"})
    viya.put_member(f"{CONTENT_ROOT}/RAG/rag_core/notes.py", "/files/files/", b"x = 1\n")
    viya.put_member(f"{CONTENT_ROOT}/Prompt Builder/Prompt Builder", "/reports/reports/", None)
    viya.calls.clear()
    result = deploy(viya, manifest)
    assert result.unlisted == ["RAG/LIVE_DEMO (report)", "RAG/generated (folder)", "RAG/rag_core/notes.py (file)"]
    assert viya.wrote() == []
    assert viya.at(f"{CONTENT_ROOT}/RAG/LIVE_DEMO") == [demo]
    assert "RAG/generated/RAG Ingest - Demo" in viya.tree(CONTENT_ROOT)
    # the report that is listed is there: said so, and not touched
    report = next(o for o in result.outcomes if o.entry.kind == "report")
    assert (report.status, report.note) == (NOT_DEPLOYED, "on the site")


def test_an_object_of_another_kind_under_a_listed_name_is_not_taken_for_it(manifest):
    """A file called like a step is not the step: a .step that was uploaded as
    a file renders as an empty editor, and the step still has to be registered."""
    viya = Viya()
    viya.put_member(f"{CONTENT_ROOT}/{STEP_PATH}", "/files/files/", json.dumps(STEP).encode("utf-8"))
    result = deploy(viya, manifest)
    assert _statuses(result)[STEP_PATH] == CREATED
    uris = viya.at(f"{CONTENT_ROOT}/{STEP_PATH}")
    assert len(uris) == 2 and sorted(uri.split("/")[1] for uri in uris) == ["dataFlows", "files"]
    assert result.unlisted == [f"{STEP_PATH} (file)"]


def test_a_folder_that_cannot_be_created_fails_what_sits_in_it_and_nothing_else(manifest):
    viya = Viya()
    viya.make_folder(f"{CONTENT_ROOT}/Data")
    viya.make_folder(f"{CONTENT_ROOT}/Prompt Builder")
    rag = viya.make_folder(f"{CONTENT_ROOT}/RAG")
    viya.refuse[("POST", "/folders/folders")] = 403
    result = deploy(viya, manifest)
    failed = {o.entry.path: o.note for o in result.failed}
    assert set(failed) == {"RAG/rag_core", "RAG/steps", "RAG/rag_core/adapters", "RAG/rag_core/__init__.py",
                           "RAG/rag_core/adapters/__init__.py", STEP_PATH}
    assert "HTTP 403" in failed["RAG/rag_core"] and failed[STEP_PATH] == "its folder could not be created"
    assert _statuses(result)["Prompt Builder/UI"] == CREATED and viya.members[rag] == []


# ---- the command ----------------------------------------------------------------
@pytest.fixture
def command(monkeypatch, repo):
    out = io.StringIO()
    viya = Viya()
    monkeypatch.setattr(cli, "console", Console(file=out, width=1000, force_terminal=False))
    monkeypatch.setattr(cli, "Context", lambda: SimpleNamespace(repo=repo))
    monkeypatch.setattr(cli, "_viya_session", lambda: viya)
    monkeypatch.setattr(deployer.time, "sleep", lambda seconds: None)

    def run(*args):
        out.seek(0)
        out.truncate()
        result = CliRunner().invoke(cli.app, ["content-deploy", *args])
        return result, out.getvalue(), viya
    return run


def test_the_command_prints_a_line_per_entry(command):
    result, text, viya = command("--dry-run")
    assert result.exit_code == 0, text
    assert f"Dry run - nothing is written. What a run would do to {CONTENT_ROOT}:" in text
    assert "  created      folder        RAG/rag_core\n" in text
    assert f"  created      step          {STEP_PATH}\n" in text
    assert "  not deployed report        Prompt Builder/Prompt Builder  (not on the site - it comes with the package)" in text
    assert "14 entries: 13 created, 1 not deployed." in text and "Nothing was written." in text
    assert viya.wrote() == []

    result, text, viya = command()
    assert result.exit_code == 0, text
    assert f"Deploying to {CONTENT_ROOT}:" in text and "Done." in text
    result, text, viya = command()
    assert result.exit_code == 0, text
    step_id = viya.at(f"{CONTENT_ROOT}/{STEP_PATH}")[0].rsplit("/", 1)[-1]
    assert f"  equal        step          {STEP_PATH}  (id {step_id})\n" in text
    assert "14 entries: 13 equal, 1 not deployed." in text and "created" not in text


def test_the_command_deploys_to_the_root_it_is_given(command):
    result, text, viya = command("--content-root", "/Users/me/My Folder/ZZ_VERIFY_R17/root/")
    assert result.exit_code == 0, text
    assert viya.folder_id("/Users/me/My Folder/ZZ_VERIFY_R17/root") and viya.folder_id(CONTENT_ROOT) is None
    result, text, _ = command("--content-root", "relative/root")
    assert result.exit_code == 2 and "must be a SAS Content path" in text


def test_the_command_ends_with_exit_code_1_when_an_entry_failed(command):
    _, _, viya = command("--dry-run")
    viya.refuse[("POST", "/dataFlows/steps")] = 500
    result, text, _ = command()
    assert result.exit_code == 1
    assert f"  FAILED       step          {STEP_PATH}  (Registering the step {STEP_NAME} failed: HTTP 500" in text
    assert "14 entries: 12 created, 1 not deployed, 1 FAILED." in text
    assert "Not every entry reached the folder." in text and "Done." not in text


def test_the_command_lists_what_it_left_alone(command):
    _, _, viya = command()
    viya.put_member(f"{CONTENT_ROOT}/RAG/LIVE_DEMO", "/reports/reports/", None)
    result, text, _ = command()
    assert result.exit_code == 0, text
    assert "Not in the manifest and left alone (1) - not deployed, and `mdb package-export` leaves them out:\n" \
           "  RAG/LIVE_DEMO (report)\n" in text


def test_only_a_part_of_the_folder_is_deployed_when_one_is_named(command):
    """`--only RAG` is the RAG runtime: what the deploy-rag-content scripts
    run. The rest of the manifest is not looked at - not created, and not
    reported as anything."""
    result, text, viya = command("--only", "RAG")
    assert result.exit_code == 0, text
    assert f"Deploying to {CONTENT_ROOT} (only RAG):" in text
    assert viya.tree(CONTENT_ROOT) == ["RAG", "RAG/rag_core", "RAG/rag_core/__init__.py", "RAG/rag_core/adapters",
                                       "RAG/rag_core/adapters/__init__.py", "RAG/steps", STEP_PATH]
    assert "7 entries: 7 created." in text and "Prompt Builder" not in text and "release.json" not in text
    # a path below a folder brings the folders above it, and nothing beside it
    result, text, viya = command("--only", "RAG/rag_core/adapters/", "--content-root", "/Team")
    assert result.exit_code == 0, text
    assert viya.tree("/Team") == ["RAG", "RAG/rag_core", "RAG/rag_core/adapters", "RAG/rag_core/adapters/__init__.py"]
    # one object, and two parts
    result, text, viya = command("--only", "release.json", "--only", "Data", "--dry-run")
    assert result.exit_code == 0, text
    assert "3 entries: 3 created." in text and "RAG" not in text
    result, text, _ = command("--only", "Logging and Monitoring")
    assert result.exit_code == 2
    assert "--only Logging and Monitoring: the manifest lists nothing at or below that path." in text


def test_the_two_scripts_of_the_rag_runtime_call_the_command():
    """deploy-rag-content.ps1 / .sh are what the Prompt Builder page, the RAG
    steps and the guides tell an admin to run. They hold no upload logic of
    their own any more: one list decides what the RAG runtime is."""
    from pathlib import Path
    folder = Path(__file__).resolve().parents[3] / "SAS-Viya-Integrations" / "Other"
    for name in ("deploy-rag-content.ps1", "deploy-rag-content.sh"):
        script = (folder / name).read_text(encoding="utf-8")
        assert "content-deploy" in script and "--only" in script and "RAG" in script, name
        for own in ("Invoke-RestMethod", "curl ", "/files/files", "/dataFlows/steps"):
            assert own not in script, (name, own)
    assert "'--content-root', $root, '--only', 'RAG'" in (folder / "deploy-rag-content.ps1").read_text(encoding="utf-8")
    assert 'ARGS=(content-deploy --content-root "$ROOT" --only RAG)' in \
        (folder / "deploy-rag-content.sh").read_text(encoding="utf-8")


def test_a_manifest_that_cannot_be_used_stops_the_command_before_it_signs_in(command, repo, monkeypatch):
    path = repo / "SAS-Viya-Integrations" / "package-manifest.yaml"
    path.write_text(path.read_text(encoding="utf-8")
                    + "  - {path: RAG/generated/job.sas, kind: file, source: src/optimize.sas}\n"
                    + "  - {path: Flows/Ingest, kind: dataFlow}\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_viya_session", lambda: pytest.fail("signed in with a manifest that cannot be used"))
    result, text, _ = command()
    assert result.exit_code == 2
    assert "The content manifest cannot be used" in text
    assert "  'RAG/generated/job.sas': 'generated' holds what the Prompt Builder generates for a site's own setups " \
           "and never ships\n" in text
    assert "  'Flows/Ingest': unknown kind 'dataFlow' (one of folder, file, step, jobDefinition, report)\n" in text
