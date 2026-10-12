# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""`mdb content-deploy` against a SAS Viya that lives in memory.

What the command promises, each as a test: a folder that is empty gets
everything; a second run changes nothing; a changed file, step or job
definition is put right IN PLACE - the object keeps its id, a file is not
deleted and uploaded again - and nothing else is touched; a dry run writes
nothing; a report is not deployed; what is not in the manifest is left alone
and named.

Then what a run does when something goes wrong, because that is where the
first version lost files: the run stops at the first write that fails and
says what it wrote and what it did not attempt; a file whose update is
refused is left as it is; a reference to an object elsewhere is never
followed; two objects of one name are both named and neither is guessed.

Then the site settings (`_contextName`, `contentRoot`), and `--to`: one entry
into the object a site has somewhere else, and one folder of the manifest
into a folder of another name.

No request here reaches a server, so none of this says how SAS Viya answers -
the fake answers the way the earlier scripts, the Prompt Builder and the pass
of 2026-10-12 saw it answer, and for the update of a file's content the way
the Files service documents it.
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
from mdb.viya.content import (CREATED, EQUAL, FAILED, NOT_ATTEMPTED, NOT_DEPLOYED, UPDATED, DeployError, deploy,
                              deploy_to, resolve_target)

STEP_PATH = f"RAG/steps/{STEP_NAME}"
CORE = "RAG/rag_core/__init__.py"
ADAPTERS = "RAG/rag_core/adapters/__init__.py"
UI = "Prompt Builder/UI"
OPTIMIZE = "Prompt Builder/Optimize"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(content, "checkout_commit", lambda root: ("abc123", "2026-10-12T09:31:55+02:00"))
    return make_repo(tmp_path)


@pytest.fixture
def manifest(repo):
    return load_manifest(repo)


def _statuses(result) -> dict:
    return {outcome.entry.path: outcome.status for outcome in result.outcomes}


def _outcome(result, path):
    return next(o for o in result.outcomes if o.entry.path == path)


def _id(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


# ---- an empty folder, and the second run ------------------------------------
def test_an_empty_site_gets_the_whole_folder(manifest):
    viya = Viya()
    result = deploy(viya, manifest)
    assert result.failed == [] and result.root_created and result.stopped_at is None
    statuses = _statuses(result)
    assert statuses.pop("Prompt Builder/Prompt Builder") == NOT_DEPLOYED
    assert set(statuses.values()) == {CREATED} and len(statuses) == 6 + 7
    assert viya.tree(CONTENT_ROOT) == [
        "Data", "Data/ACCELERATOR_RELEASES.csv", "Prompt Builder", OPTIMIZE, UI,
        "RAG", "RAG/rag_core", CORE, "RAG/rag_core/adapters", ADAPTERS, "RAG/steps", STEP_PATH, "release.json"]
    # a file as the repository has it, whatever the line ends of the checkout
    assert viya.files[viya.at(f"{CONTENT_ROOT}/{CORE}")[0]] == b"CORE = 1\n"
    # a step is registered with the dataFlows service, not uploaded as a file
    step_uri, = viya.at(f"{CONTENT_ROOT}/{STEP_PATH}")
    assert step_uri.startswith("/dataFlows/steps/") and viya.steps[step_uri]["templates"] == STEP["templates"]
    # a job definition: the program as code, the page as the property `form`
    ui, _ = viya.jobs[viya.at(f"{CONTENT_ROOT}/{UI}")[0]]
    assert ui["properties"] == [{"name": "form", "value": "<!DOCTYPE html>\n<html>ü</html>\n"}]
    assert ui["code"] == "/* SAS templated code goes here */" and ui["type"] == "Compute"
    assert [p["name"] for p in ui["parameters"]] == ["_contextName", "_action"]
    optimize, _ = viya.jobs[viya.at(f"{CONTENT_ROOT}/{OPTIMIZE}")[0]]
    assert optimize["code"] == "%put optimize;\n%put done;\n" and optimize["properties"] == []
    # the two files mdb writes
    release = json.loads(viya.files[viya.at(f"{CONTENT_ROOT}/release.json")[0]])
    assert release["commit"] == "abc123" and len(release["entries"]) == len(manifest.all_entries())
    assert viya.files[viya.at(f"{CONTENT_ROOT}/Data/ACCELERATOR_RELEASES.csv")[0]] == \
        content.release_table_bytes(manifest)
    # each line of what was created names the id it got
    assert _outcome(result, STEP_PATH).note == f"id {_id(step_uri)}"
    # and the report: not this command's to bring
    report = next(o for o in result.outcomes if o.entry.kind == "report")
    assert report.note == "not on the site - it comes with the package"
    assert viya.at(f"{CONTENT_ROOT}/Prompt Builder/Prompt Builder") == []
    # nothing was ever deleted
    assert not [call for call in viya.wrote() if call[0] == "DELETE"]


def test_a_second_run_finds_everything_equal_and_writes_nothing(manifest):
    viya = Viya()
    deploy(viya, manifest)
    before = (dict(viya.files), dict(viya.steps), dict(viya.jobs), viya.tree(CONTENT_ROOT))
    ids = viya.ids(CONTENT_ROOT)
    viya.calls.clear()
    result = deploy(viya, manifest)
    statuses = _statuses(result)
    assert statuses.pop("Prompt Builder/Prompt Builder") == NOT_DEPLOYED
    assert set(statuses.values()) == {EQUAL} and not result.root_created
    assert viya.wrote() == []
    assert (dict(viya.files), dict(viya.steps), dict(viya.jobs), viya.tree(CONTENT_ROOT)) == before
    assert result.unlisted == []
    # an equal line carries the id of the object, for a file and a job definition as for a step
    for path in (CORE, STEP_PATH, UI):
        assert _outcome(result, path).note == f"id {_id(ids[path])}"


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
    outcome = _outcome(result, STEP_PATH)
    assert outcome.status == UPDATED
    assert outcome.note == f"templates, ui; id {_id(step_uri)} kept"
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
    outcome = _outcome(deploy(viya, manifest), STEP_PATH)
    assert outcome.status == FAILED and "answered with the id another-id" in outcome.note


def test_a_changed_file_gets_its_content_replaced_and_stays_the_file_it_was(manifest, repo):
    """The first version deleted a file that differed and uploaded it again:
    a new id, and no file at all when the upload failed. The content is
    replaced under the ETag of the file instead - nothing is deleted."""
    viya = Viya()
    deploy(viya, manifest)
    uri, = viya.at(f"{CONTENT_ROOT}/{CORE}")
    kept, = viya.at(f"{CONTENT_ROOT}/{ADAPTERS}")
    (repo / "src/rag_core/__init__.py").write_bytes(b"CORE = 2\r\n")
    viya.calls.clear()
    result = deploy(viya, manifest)
    assert {o.entry.path for o in result.outcomes if o.status == UPDATED} == {CORE, "release.json"}
    assert _outcome(result, CORE).note == f"content replaced in place; id {_id(uri)} kept"
    assert viya.at(f"{CONTENT_ROOT}/{CORE}") == [uri] and viya.files[uri] == b"CORE = 2\n"
    assert viya.at(f"{CONTENT_ROOT}/{ADAPTERS}") == [kept]
    # two PUTs of content - the file and release.json - and no other write: no DELETE, no POST
    release, = viya.at(f"{CONTENT_ROOT}/release.json")
    assert sorted(viya.wrote()) == sorted([("PUT", f"{uri}/content"), ("PUT", f"{release}/content")])
    assert viya.etags[uri] == 2
    assert _statuses(deploy(viya, manifest))[CORE] == EQUAL


def test_the_update_of_a_file_names_the_version_it_read(manifest, repo, monkeypatch):
    """`If-Match` with the ETag the content was read under: somebody who
    changed the file in between is not written over."""
    viya = Viya()
    deploy(viya, manifest)
    uri, = viya.at(f"{CONTENT_ROOT}/{CORE}")
    (repo / "src/rag_core/__init__.py").write_bytes(b"CORE = 2\n")
    sent = {}
    real_put = viya.put

    def put(path, data=None, headers=None, **kwargs):
        sent[path] = dict(headers)
        return real_put(path, data=data, headers=headers, **kwargs)
    monkeypatch.setattr(viya, "put", put)
    assert _statuses(deploy(viya, manifest))[CORE] == UPDATED
    assert sent[f"{uri}/content"]["If-Match"] == '"1"'
    assert sent[f"{uri}/content"]["Content-Type"] == "application/octet-stream"
    assert "Content-Disposition" not in sent[f"{uri}/content"]            # no name is sent: the file keeps its own
    # a file somebody else changed after its version was asked for: refused, and left as it is
    (repo / "src/rag_core/__init__.py").write_bytes(b"CORE = 3\n")
    real_get = viya.get

    def get(path, **kwargs):
        response = real_get(path, **kwargs)
        if path == uri:
            viya.files[uri], viya.etags[uri] = b"somebody else\n", viya.etags[uri] + 1
        return response
    monkeypatch.setattr(viya, "get", get)
    outcome = _outcome(deploy(viya, manifest), CORE)
    assert outcome.status == FAILED
    assert outcome.note.startswith("the file was left as it is: the update of its content was refused (HTTP 412")
    assert viya.files[uri] == b"somebody else\n"


def test_a_job_definition_is_updated_in_place_with_its_etag(manifest, repo):
    viya = Viya()
    deploy(viya, manifest)
    uri, = viya.at(f"{CONTENT_ROOT}/{UI}")
    live, etag = viya.jobs[uri]
    # what the definition holds beside its content, and a property the site added
    viya.jobs[uri] = ({**live, "createdBy": "the site",
                       "properties": live["properties"] + [{"name": "siteNote", "value": "ours"}]}, etag)
    (repo / "src/index.html").write_text("<html>a newer build</html>", encoding="utf-8")
    result = deploy(viya, manifest)
    outcome = _outcome(result, UI)
    assert (outcome.status, outcome.note) == (UPDATED, f"form; id {_id(uri)} kept")
    assert viya.at(f"{CONTENT_ROOT}/{UI}") == [uri]
    body, new_etag = viya.jobs[uri]
    assert body["properties"] == [{"name": "siteNote", "value": "ours"},
                                  {"name": "form", "value": "<html>a newer build</html>"}]
    assert body["id"] == _id(uri) and body["createdBy"] == "the site" and new_etag != etag
    # a parameter that is no site setting is the checkout's: changed on the site, it is put back
    tuned = json.loads(json.dumps(body))
    tuned["parameters"][1]["defaultValue"] = "form,execute"
    viya.jobs[uri] = (tuned, new_etag)
    outcome = _outcome(deploy(viya, manifest), UI)
    assert (outcome.status, outcome.note) == (UPDATED, f"parameters; id {_id(uri)} kept")
    assert viya.jobs[uri][0]["parameters"][1]["defaultValue"] == "prompts,form,execute"


# ---- a run that goes wrong: it stops, and nothing is lost ----------------------
def _four_changed_files(repo):
    """What the review of the first version did: change every file, then let
    the Files service refuse."""
    (repo / "src/rag_core/__init__.py").write_bytes(b"CORE = 2\n")
    (repo / "src/rag_core/adapters/__init__.py").write_bytes(b"ADAPTERS = 2\n")
    (repo / "CHANGELOG.md").write_bytes((repo / "CHANGELOG.md").read_bytes().replace(b"The release before.", b"Before."))


def test_the_run_stops_at_the_first_write_that_is_refused(manifest, repo):
    """With the delete in front of the upload and a run that went on, a
    Files service answering 500 cost every file that differed. Now the first
    refusal ends the run, and every file is still in its folder."""
    viya = Viya()
    deploy(viya, manifest)
    before = dict(viya.files)
    _four_changed_files(repo)
    viya.refuse[("PUT", "/files/files/")] = 500
    viya.calls.clear()
    result = deploy(viya, manifest)
    # release.json is the first file of the manifest: its update is refused, and that is the end
    assert [o.entry.path for o in result.failed] == ["release.json"]
    assert result.stopped_at is result.failed[0]
    assert result.failed[0].note.startswith(
        "the file was left as it is: the update of its content was refused (HTTP 500")
    behind = [o.entry.path for o in result.not_attempted]
    assert behind == ["Data/ACCELERATOR_RELEASES.csv", CORE, ADAPTERS, STEP_PATH, UI, OPTIMIZE,
                      "Prompt Builder/Prompt Builder"]
    assert all(_statuses(result)[path] == NOT_ATTEMPTED for path in behind)
    # one write was sent, nothing was deleted, and every file holds what it held
    assert viya.wrote() == [("PUT", f"{viya.at(CONTENT_ROOT + '/release.json')[0]}/content")]
    assert viya.files == before
    assert result.stop_line() == (
        "Stopped at the first write that failed: file release.json. Written before it: 0 created, 0 updated. Not "
        "attempted behind it: 7 entries. Nothing was removed.")
    # with the service answering again the next run finishes the job
    viya.refuse.clear()
    result = deploy(viya, manifest)
    assert result.failed == [] and result.count(UPDATED) == 4 and result.stopped_at is None


def test_a_write_that_gets_no_answer_stops_the_run_and_says_what_is_known(manifest, repo):
    """A timeout is not an answer: whether the write was taken is not known,
    and the line says so - and that the file was not removed."""
    viya = Viya()
    deploy(viya, manifest)
    (repo / "src/rag_core/__init__.py").write_bytes(b"CORE = 2\n")
    (repo / "src/rag_core/adapters/__init__.py").write_bytes(b"ADAPTERS = 2\n")
    core, = viya.at(f"{CONTENT_ROOT}/{CORE}")
    viya.drop[("PUT", core)] = TimeoutError("read timed out")
    result = deploy(viya, manifest)
    assert _statuses(result)["release.json"] == UPDATED               # written before the stop
    outcome = _outcome(result, CORE)
    assert outcome.status == FAILED and result.stopped_at is outcome
    assert outcome.note == ("no answer to the update of the content of __init__.py (TimeoutError: read timed out) "
                            "- the file holds its old or its new content, and was not removed")
    assert _statuses(result)[ADAPTERS] == NOT_ATTEMPTED
    assert viya.at(f"{CONTENT_ROOT}/{CORE}") == [core] and viya.files[core] == b"CORE = 1\n"
    assert "Written before it: 0 created, 1 updated." in result.stop_line()


def test_an_upload_that_is_refused_stops_the_run_too(manifest):
    viya = Viya()
    viya.refuse[("POST", "/files/files")] = 403
    result = deploy(viya, manifest)
    assert [o.entry.path for o in result.failed] == ["release.json"] and "HTTP 403" in result.failed[0].note
    assert len(result.not_attempted) == 7 and _statuses(result)[STEP_PATH] == NOT_ATTEMPTED
    # the folders were there before the first object was looked at
    assert viya.tree(CONTENT_ROOT) == ["Data", "Prompt Builder", "RAG", "RAG/rag_core", "RAG/rag_core/adapters",
                                       "RAG/steps"]


def test_an_object_that_cannot_be_read_fails_and_the_run_goes_on(manifest):
    """Nothing was sent that changes the site, so there is nothing to stop for."""
    viya = Viya()
    deploy(viya, manifest)
    core, = viya.at(f"{CONTENT_ROOT}/{CORE}")
    viya.refuse[("GET", f"{core}/content")] = 503
    viya.calls.clear()
    result = deploy(viya, manifest)
    assert [o.entry.path for o in result.failed] == [CORE] and result.stopped_at is None
    assert "HTTP 503" in result.failed[0].note and result.not_attempted == []
    assert _statuses(result)[ADAPTERS] == EQUAL and viya.wrote() == []
    viya.refuse.clear()
    viya.drop[("GET", f"{core}/content")] = TimeoutError("read timed out")
    result = deploy(viya, manifest)
    assert "no answer (TimeoutError: read timed out)" in _outcome(result, CORE).note and result.stopped_at is None


# ---- what is never written: a reference, and one of two ----------------------------
def test_a_reference_to_an_object_elsewhere_is_never_followed(manifest, repo):
    """A member of a folder can be a pointer at a file that lives in another
    folder. Matched by name alone it was compared, and the file it points at
    was deleted - an object outside the folders of the manifest."""
    viya = Viya()
    deploy(viya, manifest)
    outside = viya.put_member("/Users/someone/My Folder/__init__.py", "/files/files/", b"THEIRS = 1\n")
    # the reference stands where the listed file would be, which is not there
    here = viya.folder_id(f"{CONTENT_ROOT}/RAG/rag_core/adapters")
    viya.members[here] = [m for m in viya.members[here] if m["name"] != "__init__.py"]
    viya.put_reference(f"{CONTENT_ROOT}/{ADAPTERS}", outside)
    viya.calls.clear()
    result = deploy(viya, manifest)
    outcome = _outcome(result, ADAPTERS)
    assert outcome.status == FAILED and result.stopped_at is None
    assert outcome.note.startswith(f"a reference of that name is in the folder and names {outside}, an object that "
                                   "lives elsewhere: it is not followed, and nothing was written")
    # the file elsewhere was not read, not written and is still there
    assert viya.files[outside] == b"THEIRS = 1\n"
    assert not [call for call in viya.calls if outside in call[1]] and viya.wrote() == []
    assert result.unlisted == [f"{ADAPTERS} (reference to a file)"]
    # beside the object itself a reference is only named
    viya.put_reference(f"{CONTENT_ROOT}/{CORE}", outside)
    (repo / "src/rag_core/__init__.py").write_bytes(b"CORE = 2\n")
    result = deploy(viya, manifest)
    assert _statuses(result)[CORE] == UPDATED and viya.files[outside] == b"THEIRS = 1\n"
    assert f"{CORE} (reference to a file)" in result.unlisted


def test_two_registered_steps_of_one_name_are_both_named_and_none_is_guessed(manifest, repo):
    """Flows reference a step by id. With two of one name the first was
    updated and the second kept the old program, without a word."""
    viya = Viya()
    deploy(viya, manifest)
    first, = viya.at(f"{CONTENT_ROOT}/{STEP_PATH}")
    second = viya.put_member(f"{CONTENT_ROOT}/{STEP_PATH}", "/dataFlows/steps/", {**STEP, "templates": {"SAS": "%put old;"}})
    (repo / f"src/{STEP_NAME}").write_text(json.dumps({**STEP, "ui": "{}"}), encoding="utf-8")
    before = (dict(viya.steps[first]), dict(viya.steps[second]))
    viya.calls.clear()
    result = deploy(viya, manifest)
    outcome = _outcome(result, STEP_PATH)
    assert outcome.status == FAILED and result.stopped_at is None
    assert outcome.note == (
        f"2 registered steps of that name are in the folder (ids {_id(first)}, {_id(second)}): none was changed, "
        "because nothing says which one is meant - update one with `mdb content-deploy --only "
        f"\"{STEP_PATH}\" --to <id>`, or remove the other")
    assert (viya.steps[first], viya.steps[second]) == before
    assert not [call for call in viya.wrote() if "/dataFlows/" in call[1]]
    # the entries behind it were still brought
    assert _statuses(result)[UI] == EQUAL and result.not_attempted == []
    # and the one that is named by its id is updated
    updated = deploy_to(viya, manifest, manifest.find(STEP_PATH, "step"), _id(second))
    assert (updated.status, updated.note) == (UPDATED, f"templates, ui; id {_id(second)} kept")
    assert viya.steps[first] == before[0] and viya.steps[second]["ui"] == "{}"


def test_two_files_of_one_name_are_not_made_one(manifest):
    """Nothing is deleted - also not the second file of a name."""
    viya = Viya()
    deploy(viya, manifest)
    first, = viya.at(f"{CONTENT_ROOT}/{CORE}")
    second = viya.put_member(f"{CONTENT_ROOT}/{CORE}", "/files/files/", b"CORE = 0\n")
    viya.calls.clear()
    outcome = _outcome(deploy(viya, manifest), CORE)
    assert outcome.status == FAILED and outcome.note.startswith(
        f"2 files of that name are in the folder (ids {_id(first)}, {_id(second)}): none was changed")
    assert viya.wrote() == [] and viya.files[second] == b"CORE = 0\n"


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
        CORE, STEP_PATH, OPTIMIZE, "release.json"}
    assert viya.wrote() == [] and {method for method, _path, _params in viya.calls} == {"GET"}
    assert viya.files[viya.at(f"{CONTENT_ROOT}/{CORE}")[0]] == b"CORE = 1\n"


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
    assert set(failed) == {"RAG/rag_core", "RAG/steps", "RAG/rag_core/adapters", CORE, ADAPTERS, STEP_PATH}
    assert "HTTP 403" in failed["RAG/rag_core"] and failed[STEP_PATH] == "its folder could not be created"
    assert _statuses(result)[UI] == CREATED and viya.members[rag] == []


# ---- site settings: the compute context and the content root ---------------------
def _parameters(viya, root, path) -> dict:
    body, _etag = viya.jobs[viya.at(f"{root}/{path}")[0]]
    return {p["name"]: p["defaultValue"] for p in body["parameters"]}


def test_a_new_job_definition_gets_the_default_of_a_site_setting(manifest):
    viya = Viya()
    deploy(viya, manifest)
    assert _parameters(viya, CONTENT_ROOT, UI)["_contextName"] == "SAS Studio compute context"
    assert _parameters(viya, CONTENT_ROOT, OPTIMIZE)["contentRoot"] == CONTENT_ROOT


def test_a_deploy_leaves_the_compute_context_of_a_site_alone(manifest, repo):
    """Every deploy set `_contextName` back to the value of the manifest: a
    site that runs its jobs in a context of its own lost that with each
    release. The parameter is a site setting - it is the site's."""
    viya = Viya()
    deploy(viya, manifest)
    uri, = viya.at(f"{CONTENT_ROOT}/{UI}")
    body, etag = viya.jobs[uri]
    body["parameters"][0]["defaultValue"] = "Team compute context"
    viya.calls.clear()
    result = deploy(viya, manifest)
    assert _statuses(result)[UI] == EQUAL and viya.wrote() == []
    # when the definition is written for another reason, the site's value is written back with it
    (repo / "src/index.html").write_text("<html>a newer build</html>", encoding="utf-8")
    result = deploy(viya, manifest)
    assert _outcome(result, UI).note == f"form; id {_id(uri)} kept"
    assert _parameters(viya, CONTENT_ROOT, UI) == {"_contextName": "Team compute context",
                                                   "_action": "prompts,form,execute"}


def test_a_compute_context_that_is_given_is_written(manifest):
    viya = Viya()
    deploy(viya, manifest)
    uri, = viya.at(f"{CONTENT_ROOT}/{UI}")
    result = deploy(viya, manifest, settings={"_contextName": "Team compute context"})
    assert _outcome(result, UI).note == f"parameters; id {_id(uri)} kept"
    assert _parameters(viya, CONTENT_ROOT, UI)["_contextName"] == "Team compute context"
    # a job definition without the parameter is not given one
    assert "_contextName" not in _parameters(viya, CONTENT_ROOT, OPTIMIZE)
    assert _statuses(result)[OPTIMIZE] == EQUAL
    # and it stays: the next run without the option leaves it
    assert _statuses(deploy(viya, manifest))[UI] == EQUAL
    # a new folder gets it at once
    other = Viya()
    deploy(other, manifest, settings={"_contextName": "Team compute context"})
    assert _parameters(other, CONTENT_ROOT, UI)["_contextName"] == "Team compute context"


def test_a_site_setting_the_definition_lacks_is_added_with_its_default(manifest):
    """A definition from before the parameter existed: it gets it."""
    viya = Viya()
    deploy(viya, manifest)
    uri, = viya.at(f"{CONTENT_ROOT}/{OPTIMIZE}")
    body, etag = viya.jobs[uri]
    viya.jobs[uri] = ({**body, "parameters": body["parameters"][:1]}, etag)
    assert _outcome(deploy(viya, manifest), OPTIMIZE).status == UPDATED
    assert _parameters(viya, CONTENT_ROOT, OPTIMIZE) == {"_action": "execute", "contentRoot": CONTENT_ROOT}


# ---- one entry into the object a site has somewhere else -------------------------
def test_a_step_a_site_registered_elsewhere_is_updated_in_place(manifest, repo):
    """Earlier releases put a step into /Custom Steps or a user's folder,
    and the flows of a site reference that step by its id. The package places
    it somewhere else; this updates the step the flows use."""
    viya = Viya()
    old = {**STEP, "templates": {"SAS": "%put the parser of an earlier release;\n"}}
    theirs = viya.put_member(f"/Custom Steps/{STEP_NAME}", "/dataFlows/steps/", old)
    entry = manifest.find(STEP_PATH, "step")
    # a dry run first: it says what a run would do
    outcome = deploy_to(viya, manifest, entry, f"/Custom Steps/{STEP_NAME}", dry_run=True)
    assert (outcome.status, outcome.note) == (UPDATED, f"templates; id {_id(theirs)} kept")
    assert viya.wrote() == [] and viya.steps[theirs] == old
    outcome = deploy_to(viya, manifest, entry, f"/Custom Steps/{STEP_NAME}")
    assert (outcome.status, outcome.note) == (UPDATED, f"templates; id {_id(theirs)} kept")
    assert viya.steps[theirs]["templates"] == STEP["templates"]
    # one write, at the step's own address; no folder was made, and the content root does not exist
    assert viya.wrote() == [("PUT", theirs)] and viya.folder_id(CONTENT_ROOT) is None
    assert viya.at(f"/Custom Steps/{STEP_NAME}") == [theirs]
    assert deploy_to(viya, manifest, entry, f"/Custom Steps/{STEP_NAME}").status == EQUAL
    # by its id and by its URI
    viya.steps[theirs] = old
    assert deploy_to(viya, manifest, entry, _id(theirs)).status == UPDATED
    viya.steps[theirs] = old
    assert deploy_to(viya, manifest, entry, theirs).status == UPDATED


def test_a_step_keeps_the_name_it_has_on_the_site(manifest):
    viya = Viya()
    theirs = viya.put_member("/Team/Steps/Embed (ours)", "/dataFlows/steps/",
                             {**STEP, "name": "Embed (ours)", "templates": {"SAS": "%put old;\n"}})
    outcome = deploy_to(viya, manifest, manifest.find(STEP_PATH, "step"), "/Team/Steps/Embed (ours)")
    assert outcome.status == UPDATED
    assert viya.steps[theirs]["name"] == "Embed (ours)" and viya.steps[theirs]["templates"] == STEP["templates"]


def test_a_target_that_is_not_there_is_never_created(manifest):
    viya = Viya()
    viya.make_folder("/Custom Steps")
    entry = manifest.find(STEP_PATH, "step")
    outcome = deploy_to(viya, manifest, entry, f"/Custom Steps/{STEP_NAME}")
    assert outcome.status == FAILED and outcome.note == (
        f"--to /Custom Steps/{STEP_NAME}: the folder /Custom Steps holds no registered step named '{STEP_NAME}'. "
        "Nothing was written - this option updates an object and never creates one.")
    assert "there is no folder /No Such" in deploy_to(viya, manifest, entry, f"/No Such/{STEP_NAME}").note
    assert "not a SAS Content path" in deploy_to(viya, manifest, entry, "Custom Steps/x").note
    # an id that names nothing: the read says so
    missing = deploy_to(viya, manifest, entry, "11111111-0000-4000-8000-999999999999")
    assert missing.status == FAILED and "HTTP 404" in missing.note
    # the URI of an object of another kind is not a step
    assert "not a SAS Content path" in deploy_to(viya, manifest, entry,
                                                  "/files/files/11111111-0000-4000-8000-999999999999").note
    assert viya.wrote() == []


def test_a_file_and_a_job_definition_are_updated_elsewhere_the_same_way(manifest):
    viya = Viya()
    file_uri = viya.put_member("/Team/code/__init__.py", "/files/files/", b"OLD = 1\n")
    job_uri = viya.put_member("/Team/jobs/Optimize", "/jobDefinitions/definitions/",
                              {"name": "Optimize", "type": "Compute", "code": "%put old;\n", "parameters": [],
                               "properties": []})
    outcome = deploy_to(viya, manifest, manifest.find(CORE, "file"), "/Team/code/__init__.py")
    assert outcome.note == f"content replaced in place; id {_id(file_uri)} kept" and viya.files[file_uri] == b"CORE = 1\n"
    outcome = deploy_to(viya, manifest, manifest.find(OPTIMIZE, "jobDefinition"), _id(job_uri))
    assert outcome.note == f"code, parameters; id {_id(job_uri)} kept"
    assert viya.jobs[job_uri][0]["code"] == "%put optimize;\n%put done;\n"
    # a reference in the folder is not the object
    viya.put_reference("/Team/links/__init__.py", file_uri)
    assert "holds no file named" in deploy_to(viya, manifest, manifest.find(CORE, "file"),
                                               "/Team/links/__init__.py").note
    # and of two steps of one name a path names neither
    viya.put_member(f"/Twice/{STEP_NAME}", "/dataFlows/steps/", STEP)
    viya.put_member(f"/Twice/{STEP_NAME}", "/dataFlows/steps/", STEP)
    with pytest.raises(DeployError, match="the folder holds 2 registered steps of that name"):
        resolve_target(viya, "step", f"/Twice/{STEP_NAME}")


# ---- the command ----------------------------------------------------------------
@pytest.fixture
def command(monkeypatch, repo):
    out = io.StringIO()
    viya = Viya()
    monkeypatch.setattr(cli, "console", Console(file=out, width=1000, force_terminal=False))
    monkeypatch.setattr(cli, "Context", lambda: SimpleNamespace(repo=repo))
    monkeypatch.setattr(cli, "_viya_session", lambda: viya)

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
    step_id = _id(viya.at(f"{CONTENT_ROOT}/{STEP_PATH}")[0])
    file_id = _id(viya.at(f"{CONTENT_ROOT}/{CORE}")[0])
    assert f"  equal        step          {STEP_PATH}  (id {step_id})\n" in text
    assert f"  equal        file          {CORE}  (id {file_id})\n" in text
    assert "14 entries: 13 equal, 1 not deployed." in text and "created" not in text


def test_the_command_deploys_to_the_root_it_is_given(command):
    result, text, viya = command("--content-root", "/Users/me/My Folder/ZZ_VERIFY_R17/root/")
    assert result.exit_code == 0, text
    assert viya.folder_id("/Users/me/My Folder/ZZ_VERIFY_R17/root") and viya.folder_id(CONTENT_ROOT) is None
    result, text, _ = command("--content-root", "relative/root")
    assert result.exit_code == 2 and "must be a SAS Content path" in text


def test_a_content_root_that_is_given_is_written_into_the_job_definitions_that_read_one(command):
    """Start Here and Load-Accelerator-Tables read the folder their parameter
    `contentRoot` names. Deployed into another root they checked and loaded
    the default one."""
    root = "/Users/me/My Folder/ZZ_VERIFY_R18/root1"
    result, text, viya = command("--content-root", root)
    assert result.exit_code == 0, text
    assert _parameters(viya, root, OPTIMIZE) == {"_action": "execute", "contentRoot": root}
    assert "contentRoot" not in _parameters(viya, root, UI)
    # the default root, not named: a value a site set there is left alone
    result, text, viya = command()
    assert result.exit_code == 0, text
    uri, = viya.at(f"{CONTENT_ROOT}/{OPTIMIZE}")
    viya.jobs[uri][0]["parameters"][1]["defaultValue"] = "/Team/Accelerator"
    result, text, viya = command()
    assert f"  equal        jobDefinition {OPTIMIZE}  (id {_id(uri)})\n" in text
    assert _parameters(viya, CONTENT_ROOT, OPTIMIZE)["contentRoot"] == "/Team/Accelerator"


def test_the_command_writes_the_compute_context_it_is_given(command):
    result, text, viya = command()
    uri, = viya.at(f"{CONTENT_ROOT}/{UI}")
    result, text, viya = command("--compute-context", "Team compute context")
    assert result.exit_code == 0, text
    assert f"  updated      jobDefinition {UI}  (parameters; id {_id(uri)} kept)\n" in text
    assert _parameters(viya, CONTENT_ROOT, UI)["_contextName"] == "Team compute context"
    result, text, viya = command()
    assert "13 equal, 1 not deployed." in text
    assert _parameters(viya, CONTENT_ROOT, UI)["_contextName"] == "Team compute context"


def test_the_command_stops_at_the_first_failed_write_and_lists_what_it_did_not_attempt(command):
    _, _, viya = command("--dry-run")
    viya.refuse[("POST", "/dataFlows/steps")] = 500
    result, text, _ = command()
    assert result.exit_code == 1
    assert f"  FAILED       step          {STEP_PATH}  (Registering the step {STEP_NAME} failed: HTTP 500" in text
    assert f"  not attempted jobDefinition {UI}\n" in text
    assert f"  not attempted jobDefinition {OPTIMIZE}\n" in text
    assert "14 entries: 10 created, 3 not attempted, 1 FAILED." in text
    assert (f"Stopped at the first write that failed: step {STEP_PATH}. Written before it: 10 created, 0 updated. "
            "Not attempted behind it: 3 entries. Nothing was removed.") in text
    assert "Not every entry reached the folder." in text and "Done." not in text
    # the next run brings the rest and leaves what is there
    viya.refuse.clear()
    result, text, _ = command()
    assert result.exit_code == 0 and "14 entries: 10 equal, 3 created, 1 not deployed." in text


def test_the_command_lists_what_it_left_alone(command):
    _, _, viya = command()
    viya.put_member(f"{CONTENT_ROOT}/RAG/LIVE_DEMO", "/reports/reports/", None)
    result, text, _ = command()
    assert result.exit_code == 0, text
    assert "Not in the manifest and left alone (1) - not deployed, and `mdb package-export` leaves them out:\n" \
           "  RAG/LIVE_DEMO (report)\n" in text


def test_only_a_part_of_the_folder_is_deployed_when_one_is_named(command):
    """`--only RAG` is the RAG runtime. The rest of the manifest is not
    looked at - not created, and not reported as anything."""
    result, text, viya = command("--only", "RAG")
    assert result.exit_code == 0, text
    assert f"Deploying to {CONTENT_ROOT} (only RAG):" in text
    assert viya.tree(CONTENT_ROOT) == ["RAG", "RAG/rag_core", CORE, "RAG/rag_core/adapters", ADAPTERS, "RAG/steps",
                                       STEP_PATH]
    assert "7 entries: 7 created." in text and "Prompt Builder" not in text and "release.json" not in text
    # a path below a folder brings the folders above it, and nothing beside it
    result, text, viya = command("--only", "RAG/rag_core/adapters/", "--content-root", "/Team")
    assert result.exit_code == 0, text
    assert viya.tree("/Team") == ["RAG", "RAG/rag_core", "RAG/rag_core/adapters", ADAPTERS]
    # one object, and two parts
    result, text, viya = command("--only", "release.json", "--only", "Data", "--dry-run")
    assert result.exit_code == 0, text
    assert "3 entries: 3 created." in text and "RAG" not in text
    result, text, _ = command("--only", "Logging and Monitoring")
    assert result.exit_code == 2
    assert "--only Logging and Monitoring: the manifest lists nothing at or below that path." in text


def test_one_command_updates_the_step_a_site_registered_elsewhere(command):
    """The command an upgrade step names: the manifest path of the step, and
    where the site has it."""
    _, _, viya = command("--dry-run")
    theirs = viya.put_member(f"/Custom Steps/{STEP_NAME}", "/dataFlows/steps/",
                             {**STEP, "templates": {"SAS": "%put old;\n"}})
    result, text, _ = command("--only", STEP_PATH, "--to", f"/Custom Steps/{STEP_NAME}", "--dry-run")
    assert result.exit_code == 0, text
    assert f"Dry run - nothing is written. What a run would do to /Custom Steps/{STEP_NAME} from {STEP_PATH}:" in text
    assert f"  updated      step          {STEP_PATH}  (templates; id {_id(theirs)} kept)\n" in text
    assert "Nothing was written." in text and viya.wrote() == []
    result, text, _ = command("--only", STEP_PATH, "--to", f"/Custom Steps/{STEP_NAME}")
    assert result.exit_code == 0, text
    assert f"Updating /Custom Steps/{STEP_NAME} from {STEP_PATH}:" in text and "Done." in text
    assert viya.steps[theirs]["templates"] == STEP["templates"] and viya.wrote() == [("PUT", theirs)]
    assert viya.folder_id(CONTENT_ROOT) is None                       # nothing else was deployed
    result, text, _ = command("--only", STEP_PATH, "--to", _id(theirs))
    assert result.exit_code == 0 and f"  equal        step          {STEP_PATH}  (id {_id(theirs)})\n" in text
    # a step that is not where the command says: nothing is created, and the exit code says so
    result, text, _ = command("--only", STEP_PATH, "--to", f"/Elsewhere/{STEP_NAME}")
    assert result.exit_code == 1 and "there is no folder /Elsewhere" in text and "Nothing was updated." in text
    # --to needs the one entry it is about
    result, text, _ = command("--to", f"/Custom Steps/{STEP_NAME}")
    assert result.exit_code == 2 and "--to goes with exactly one --only" in text
    result, text, _ = command("--only", STEP_PATH, "--only", CORE, "--to", "/x")
    assert result.exit_code == 2 and "--to goes with exactly one --only" in text
    result, text, _ = command("--only", "Prompt Builder/Prompt Builder", "--to", "/x/Prompt Builder")
    assert result.exit_code == 2 and "A report is not deployed" in text
    result, text, _ = command("--only", STEP_PATH, "--to", "/x", "--content-root", "/y")
    assert result.exit_code == 2 and "--content-root does not go with it" in text


def test_a_folder_of_the_manifest_goes_into_a_folder_of_another_name(command):
    """A RAG content root is a setting of the Prompt Builder and need not be
    `<content root>/RAG`. `--only RAG --to <that folder>` deploys the RAG
    runtime there - what `-ContentRoot` of the deploy-rag-content scripts
    names."""
    result, text, viya = command("--only", "RAG", "--to", "/Team/Retrieval")
    assert result.exit_code == 0, text
    assert "Deploying to /Team/Retrieval (the entries below RAG of the manifest):" in text
    assert viya.tree("/Team/Retrieval") == ["rag_core", "rag_core/__init__.py", "rag_core/adapters",
                                            "rag_core/adapters/__init__.py", "steps", f"steps/{STEP_NAME}"]
    assert viya.folder_id(CONTENT_ROOT) is None and viya.folder_id("/Team/Retrieval/RAG") is None
    assert "6 entries: 6 created." in text
    assert "  created      file          rag_core/__init__.py" in text
    result, text, viya = command("--only", "RAG", "--to", "/Team/Retrieval/")
    assert result.exit_code == 0 and "6 entries: 6 equal." in text
    # the default RAG root is the folder RAG of the content root, either way of saying it
    result, text, viya = command("--only", "RAG", "--to", f"{CONTENT_ROOT}/RAG", "--dry-run")
    assert result.exit_code == 0 and "6 entries: 6 created." in text
    result, text, _ = command("--only", "RAG", "--to", "Team/Retrieval")
    assert result.exit_code == 2 and "--only RAG is a folder of the manifest" in text


def test_the_two_scripts_of_the_rag_runtime_call_the_command():
    """deploy-rag-content.ps1 / .sh are what the Prompt Builder page, the RAG
    steps and the guides tell an admin to run. They hold no upload logic of
    their own: one list decides what the RAG runtime is, and the RAG content
    root they are given is a folder of any name."""
    from pathlib import Path
    folder = Path(__file__).resolve().parents[3] / "SAS-Viya-Integrations" / "Other"
    for name in ("deploy-rag-content.ps1", "deploy-rag-content.sh"):
        script = (folder / name).read_text(encoding="utf-8")
        assert "content-deploy" in script and "--only" in script and "--to" in script, name
        for own in ("Invoke-RestMethod", "curl ", "/files/files", "/dataFlows/steps", "has to be a folder named RAG"):
            assert own not in script, (name, own)
    assert "'content-deploy', '--only', 'RAG', '--to', $root" in (folder / "deploy-rag-content.ps1").read_text(encoding="utf-8")
    assert 'ARGS=(content-deploy --only RAG --to "$ROOT")' in \
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


# ---- the manifest of this repository -----------------------------------------
def test_the_manifest_of_the_repository_deploys_and_passes_the_gate(monkeypatch):
    """The tests above run on a manifest of a few entries. This one takes the
    list that ships: a dry run on an empty site names every entry of it and
    writes nothing, a run creates everything but the two reports, a second run
    finds all of it equal, and a package that holds what was deployed - plus
    the reports as the package would bring them - passes the content gate, but
    for the pictures of the usage report: their bytes are not in the
    repository, only their hashes, and the package made here holds made-up
    ones. Still no request reaches a server."""
    from pathlib import Path

    from content_fakes import package_of
    from mdb.core.packages import package_differences

    # the commit release.json names is the checkout's - also where the tests run in an archive without git
    monkeypatch.setattr(content, "checkout_commit", lambda root: ("c" * 40, "2026-10-12T09:31:55+02:00"))
    monkeypatch.setattr(content, "commit_in_history", lambda root, commit: None)
    shipped = load_manifest(Path(__file__).resolve().parents[3])
    entries = shipped.all_entries()
    reports = [entry for entry in entries if entry.kind == "report"]
    assert len(reports) == 2

    viya = Viya()
    dry = deploy(viya, shipped, dry_run=True)
    assert [outcome.entry.path for outcome in dry.outcomes] == [entry.path for entry in entries]
    assert viya.wrote() == [] and dry.root_created
    assert {path: status for path, status in _statuses(dry).items() if status != CREATED} == \
        {entry.path: NOT_DEPLOYED for entry in reports}

    first = deploy(viya, shipped)
    assert first.failed == [] and first.unlisted == []
    assert first.count(CREATED) == len(entries) - 2 and first.count(NOT_DEPLOYED) == 2
    # the two pages a site opens first are job definitions, not files
    (start,) = viya.at(f"{shipped.content_root}/Start Here")
    (release,) = viya.at(f"{shipped.content_root}/release.json")
    assert start.startswith("/jobDefinitions/definitions/") and release.startswith("/files/files/")

    written = len(viya.wrote())
    second = deploy(viya, shipped)
    assert second.count(EQUAL) == len(entries) - 2 and second.failed == []
    assert len(viya.wrote()) == written

    pictures = [(entry.path, listed["name"]) for entry in reports for listed in entry.files if listed["sha256"]]
    assert pictures, "no report of the manifest lists a file with its hash"
    import hashlib
    differences = package_differences(package_of(shipped).build(), shipped)
    # each made-up picture is not the one the entry lists, and so the listed one is not in the package
    assert sorted(differences) == sorted(
        [f"file '{name}' of report '{path}' is not the file its manifest entry lists (SHA-256 "
         + hashlib.sha256(f"made up: {name}".encode("utf-8")).hexdigest() + ")" for path, name in pictures]
        + [f"report '{path}': the file '{name}' its manifest entry lists is not in the package"
           for path, name in pictures])
