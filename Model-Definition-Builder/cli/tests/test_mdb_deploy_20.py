# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What a confined pass on SAS Viya 2026.09 (2026-10-13) and two reviews found
in `mdb content-deploy` and `mdb builders-import`, each as a test that fails on
the code before it:

  * a run of a part of the manifest wrote release.json - the record of a
    whole folder - into a folder that held nothing else;
  * `--only Data --to <root>/Data` wrote the content root of the manifest
    into the job definition, not the root the content went to;
  * a reference at a path of the manifest was listed as "not in the
    manifest" by the run that FAILED the entry for it, and beside the object
    itself it was passed over although two things of one name are never
    written;
  * the command a failed import names dropped --no-files and --out, and was
    refused its reports again after a refusal for the content root;
  * the line of a refused write cut the answer of the server in the middle,
    and no line said that the content root itself was created;
  * the line about the compute context named only the job definition a run
    creates, while one without the parameter gets it too;
  * a custom step was registered with the user id its .step file carries.

Every service is the fake of content_fakes: nothing here says how SAS Viya
answers. The answer of a refused write is made up in the SHAPE the pass
recorded.
"""
import io
import json
import re
import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console
from typer.testing import CliRunner

import test_builder_commands as imports
from content_fakes import CONTENT_ROOT, STEP, STEP_NAME, Response, Viya, make_repo
from mdb import cli
from mdb.core import content
from mdb.core.content import load_manifest
from mdb.viya import content as deploying
from mdb.viya.content import CREATED, EQUAL, FAILED, NOT_DEPLOYED, DeployError, deploy, deploy_to, resolve_target
# the fixtures of the two commands are those of their own test files
from test_builder_commands import (FILE_PATH, PACKAGE_FILE, TUNED, UI_PATH, Site, _built,  # noqa: F401
                                   _site_with_its_own_objects, discovery, importing, no_discovery, out, site)
from test_content_deploy import ADAPTERS, CORE, STEP_PATH, command, manifest, repo  # noqa: F401

REPOSITORY = Path(__file__).resolve().parents[3]


def _id(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def _statuses(result) -> dict:
    return {outcome.entry.path: outcome.status for outcome in result.outcomes}


def _parameters(viya, path) -> dict:
    body, _etag = viya.jobs[viya.at(path)[0]]
    return {p["name"]: p["defaultValue"] for p in body["parameters"]}


def _flat(text: str) -> str:
    """A text as one line, without the backticks of Markdown: what a
    sentence is compared by wherever it stands."""
    return " ".join(text.replace("`", "").split())


# ---- release.json: only by a run that covers the whole manifest ------------------
def test_a_run_of_a_part_does_not_write_the_record_of_the_whole_folder(command, manifest):
    """review-19: `--only release.json` on a fresh site wrote a release.json
    that names the release and every hash into a folder that held nothing
    else. Such a run is refused, with one line, before anything is sent."""
    for args in (("--only", "release.json"), ("--only", "RAG", "--only", "release.json"),
                 ("--only", "release.json", "--dry-run")):
        result, text, viya = command(*args)
        assert result.exit_code == 1, text
        assert text.count(deploying.RECORD_NEEDS_ALL) == 1
        assert "Done." not in text and "Nothing was written.\n" not in text
        # nothing was asked and nothing was written: not even the content root was made
        assert viya.calls == [] and viya.folders == {}
    assert deploying.RECORD_NEEDS_ALL == (
        "release.json is the record of the whole folder and is written only by a run that covers every entry of the "
        "manifest: this run covers a part of it (--only), so nothing was written - leave release.json out of "
        "--only, or run without --only")
    # the function itself holds the rule, not only the command
    with pytest.raises(DeployError, match="release.json is the record of the whole folder"):
        deploy(Viya(), manifest, only=["release.json"])
    # a part that does not name it is deployed as before, and release.json is not looked at
    result, text, viya = command("--only", "RAG")
    assert result.exit_code == 0 and "release.json" not in text and viya.at(f"{CONTENT_ROOT}/release.json") == []
    # parts that together are the whole manifest are a run of every entry
    whole = Viya()
    result = deploy(whole, manifest, only=["release.json", "Data", "RAG", "Prompt Builder"])
    assert result.failed == [] and len(whole.at(f"{CONTENT_ROOT}/release.json")) == 1


def test_the_record_is_not_written_into_a_file_a_site_has_elsewhere(command, manifest):
    """The same hole by another door: `--only release.json --to <a file>`."""
    viya = Viya()
    theirs = viya.put_member("/Team/Other/release.json", "/files/files/", b"{}\n")
    viya.calls.clear()
    outcome = deploy_to(viya, manifest, manifest.find("release.json", "file"), "/Team/Other/release.json")
    assert (outcome.status, outcome.note) == (FAILED, deploying.RECORD_NEEDS_ALL)
    assert viya.calls == [] and viya.files[theirs] == b"{}\n"
    result, text, _ = command("--only", "release.json", "--to", "/Team/Other/release.json")
    assert result.exit_code == 1 and deploying.RECORD_NEEDS_ALL in text and "Nothing was updated." in text


# ---- a folder of the manifest somewhere else: the content root its jobs read -----
def test_the_content_root_of_a_folder_that_goes_elsewhere_is_the_one_it_belongs_to(command, monkeypatch):
    """live-19: `--only Data --to <root>/Data --create` wrote contentRoot =
    the content root of the manifest into the job definition, which then read
    the Data folder of the real content root. Here the folder `Prompt
    Builder` of the test manifest holds the job that reads one."""
    result, text, viya = command("--only", "Prompt Builder", "--to", "/Team/Tables/Prompt Builder/", "--create")
    assert result.exit_code == 0, text
    assert _parameters(viya, "/Team/Tables/Prompt Builder/Optimize") == {
        "_action": "execute", "contentRoot": "/Team/Tables"}
    # a job the site pointed somewhere else is put right by the next run, like in a content root
    uri, = viya.at("/Team/Tables/Prompt Builder/Optimize")
    viya.jobs[uri][0]["parameters"][1]["defaultValue"] = CONTENT_ROOT
    result, text, viya = command("--only", "Prompt Builder", "--to", "/Team/Tables/Prompt Builder")
    assert f"  updated      jobDefinition Optimize  (parameters; id {_id(uri)} kept)\n" in text
    assert _parameters(viya, "/Team/Tables/Prompt Builder/Optimize")["contentRoot"] == "/Team/Tables"
    # a target that is that folder of no content root: refused before the sign-in, with what to give
    monkeypatch.setattr(cli, "_viya_session", lambda: pytest.fail("signed in for a target that is refused"))
    for target in ("/Team/Pages", "/Prompt Builder", "/Team/Prompt Builder 2"):
        result, text, _ = command("--only", "Prompt Builder", "--to", target, "--create")
        assert result.exit_code == 2, text
        assert (f"--only Prompt Builder --to {target}: the job definition Prompt Builder/Optimize reads the content "
                f"root it sits in (its parameter contentRoot), and {target} is not the folder Prompt Builder of a "
                "content root - a path that ends with /Prompt Builder, with the content root in front of it. Nothing "
                "was written. Give the folder as <content root>/Prompt Builder, or run `mdb content-deploy "
                "--content-root <content root> --only \"Prompt Builder\"`.") in text


def test_a_folder_without_a_job_that_reads_the_content_root_goes_anywhere(command):
    """The RAG runtime: a RAG content root is a folder of any name, and no
    job definition of it reads a content root."""
    result, text, viya = command("--only", "RAG", "--to", "/Team/Retrieval", "--create")
    assert result.exit_code == 0 and "6 entries: 6 created." in text


@pytest.mark.parametrize("target, folder, root", [
    ("/Team/Tables/Data", "Data", "/Team/Tables"), ("/Team/Tables/Data/", "/Data/", "/Team/Tables"),
    ("/A/RAG/rag_core", "RAG/rag_core", "/A"), ("/Users/me/My Folder/root/Data", "Data", "/Users/me/My Folder/root"),
    # not that folder of a content root
    ("/Data", "Data", ""), ("/Team/Tables", "Data", ""), ("/Team/MyData", "Data", ""), ("/Team/Data/x", "Data", ""),
])
def test_the_content_root_a_target_belongs_to(target, folder, root):
    assert content.root_of_part(target, folder) == root


def test_the_data_folder_of_the_repository_manifest_reads_the_root_it_went_to(monkeypatch):
    """The command of the pass, on the manifest that ships."""
    buffer, viya = io.StringIO(), Viya()
    monkeypatch.setattr(cli, "console", Console(file=buffer, width=1000, force_terminal=False))
    monkeypatch.setattr(cli, "Context", lambda: SimpleNamespace(repo=REPOSITORY))
    monkeypatch.setattr(cli, "_viya_session", lambda: viya)
    root = "/Users/me/My Folder/ZZ_VERIFY_R20/zz_loader"
    result = CliRunner().invoke(cli.app, ["content-deploy", "--only", "Data", "--to", f"{root}/Data", "--create"])
    assert result.exit_code == 0, buffer.getvalue()
    parameters = _parameters(viya, f"{root}/Data/Load-Accelerator-Tables")
    assert parameters["contentRoot"] == root != load_manifest(REPOSITORY).content_root
    result = CliRunner().invoke(cli.app, ["content-deploy", "--only", "Data", "--to", "/Team/Tables", "--create"])
    assert result.exit_code == 2 and "the job definition Data/Load-Accelerator-Tables reads the content root" in \
        buffer.getvalue()
    assert viya.folder_id("/Team") is None


# ---- one name, one object: a reference of the name of an entry -------------------
def _reference_alone(viya, path=ADAPTERS) -> str:
    """Take the file of an entry out of its folder and put a reference of its
    name there, which points at a file in another folder. Returns that file."""
    outside = viya.put_member("/Users/someone/My Folder/elsewhere.py", "/files/files/", b"THEIRS = 1\n")
    above, _, name = f"{CONTENT_ROOT}/{path}".rpartition("/")
    here = viya.folder_id(above)
    viya.members[here] = [m for m in viya.members[here] if m["name"] != name]
    viya.put_reference(f"{CONTENT_ROOT}/{path}", outside)
    return outside


def test_a_reference_at_a_path_of_the_manifest_is_not_called_not_in_the_manifest(command):
    """live-19: the run that FAILED RAG/rag_core/tokens.py for a reference of
    that name listed the same path under 'Not in the manifest and left
    alone'."""
    _, _, viya = command()
    outside = _reference_alone(viya)
    for args in ((), ("--dry-run",)):
        result, text, _ = command(*args)
        assert result.exit_code == 1, text
        assert (f"  FAILED       file          {ADAPTERS}  (a reference of that name is in the folder and names "
                f"{outside}, an object that lives elsewhere: it is not followed, and nothing was written") in text
        assert "Not in the manifest" not in text and "left alone" not in text
        assert "  not attempted file          release.json  (not written: 1 entry in front of it FAILED" in text
    # a reference that stands at no path of the manifest is what it was: not in the manifest, and left alone
    viya.put_reference(f"{CONTENT_ROOT}/RAG/rag_core/notes.py", outside)
    result, text, _ = command()
    assert "Not in the manifest and left alone (1) - not deployed, and `mdb package-export` leaves them out:\n" \
           "  RAG/rag_core/notes.py (reference to a file)\n" in text


def test_the_object_and_a_reference_of_its_name_are_both_named_and_neither_is_written(command, repo):
    """live-19: with the real file AND a reference of its name in the folder
    the run said `equal`, exit code 0, and named the reference as 'left
    alone' - while two objects of one name are both named and neither is
    written."""
    _, _, viya = command()
    core, = viya.at(f"{CONTENT_ROOT}/{CORE}")
    outside = viya.put_member("/Users/someone/My Folder/elsewhere.py", "/files/files/", b"THEIRS = 1\n")
    viya.put_reference(f"{CONTENT_ROOT}/{CORE}", outside)
    line = (f"  FAILED       file          {CORE}  (the file of that name (id {_id(core)}) and a reference of that "
            f"name, which names {outside}, an object that lives elsewhere, are in the folder: none was changed and "
            "the reference was not followed, because nothing says which one is meant - remove the reference)\n")
    # equal content, and content that differs: the same line, and no write either time
    for changed in (False, True):
        if changed:
            (repo / "src/rag_core/__init__.py").write_bytes(b"CORE = 2\n")
        viya.calls.clear()
        result, text, _ = command()
        assert result.exit_code == 1 and line in text, text
        assert viya.wrote() == [] and viya.files[core] == b"CORE = 1\n" and viya.files[outside] == b"THEIRS = 1\n"
        assert "left alone" not in text
        assert "14 entries: 11 equal, 1 not deployed, 1 not attempted, 1 FAILED." in text
    # every reference of the name is named, alone and beside the object
    second = viya.put_member("/Users/someone/My Folder/another.py", "/files/files/", b"OTHERS = 1\n")
    viya.put_reference(f"{CONTENT_ROOT}/{CORE}", second)
    result, text, _ = command()
    assert (f"(the file of that name (id {_id(core)}) and 2 references of that name, which name {outside}, {second}, "
            "objects that live elsewhere, are in the folder: none was changed and no reference was followed, because "
            "nothing says which one is meant - remove the references)\n") in text
    here = viya.folder_id(f"{CONTENT_ROOT}/RAG/rag_core")
    kept = list(viya.members[here])
    viya.members[here] = [m for m in kept if m["uri"] != core]
    result, text, _ = command()
    assert (f"(2 references of that name are in the folder and name {outside}, {second}, objects that live "
            "elsewhere: none is followed, and nothing was written - remove them, or update an object they name "
            "with --to)\n") in text and viya.wrote() == []
    viya.members[here] = kept
    # the references removed: the file is the one object of its name again, and is written
    viya.members[here] = [m for m in viya.members[here] if m.get("type") != "reference"]
    result, text, _ = command()
    assert result.exit_code == 0 and viya.files[core] == b"CORE = 2\n"


def test_the_import_follows_the_same_rule_for_a_reference(importing, no_discovery):
    """`mdb builders-import` runs the same write path: one truth for both."""
    no_discovery()
    viya = importing.viya
    mine = _site_with_its_own_objects(viya)
    outside = viya.put_member("/Users/someone/My Folder/elsewhere.py", "/files/files/", b"THEIRS = 1\n")
    viya.put_reference(f"{CONTENT_ROOT}/{FILE_PATH}", outside)
    result, fake, text = importing(Site(), "--no-reports", without_report=True)
    assert result.exit_code == 1, text
    assert (f"    FAILED       file          {FILE_PATH}  (the file of that name (id {_id(mine[FILE_PATH])}) and a "
            f"reference of that name, which names {outside}") in text
    assert viya.files[mine[FILE_PATH]] == b"CORE = 1\n" and "left alone" not in text
    # the reference alone, at the path of an object of the package
    here = viya.folder_id(f"{CONTENT_ROOT}/RAG/rag_core")
    viya.members[here] = [m for m in viya.members[here] if m.get("type") == "reference"]
    result, fake, text = importing(Site(), "--no-reports", without_report=True)
    assert result.exit_code == 1
    assert f"    FAILED       file          {FILE_PATH}  (a reference of that name is in the folder and names " in text
    assert "Not in the package" not in text and "left alone" not in text


def test_a_reference_of_the_name_of_a_folder_fails_the_folder_and_what_is_in_it(manifest):
    """The rule holds for a folder of the manifest too: beside a reference
    called RAG no second RAG is created, and nothing goes into either. A dry
    run says what the run does."""
    viya = Viya()
    viya.make_folder(CONTENT_ROOT)
    elsewhere = viya.make_folder("/Team/Other RAG")
    viya.put_reference(f"{CONTENT_ROOT}/RAG", f"/folders/folders/{elsewhere}")
    lines = []
    for dry_run in (True, False):
        viya.calls.clear()
        result = deploy(viya, manifest, dry_run=dry_run)
        statuses = _statuses(result)
        assert {path for path, status in statuses.items() if status == FAILED} == {
            "RAG", "RAG/rag_core", "RAG/steps", "RAG/rag_core/adapters", CORE, ADAPTERS, STEP_PATH}
        assert statuses["release.json"] == "not attempted" and statuses["Data"] == CREATED
        notes = {o.entry.path: o.note for o in result.failed}
        assert notes["RAG"].startswith(f"a reference of that name is in the folder and names /folders/folders/"
                                       f"{elsewhere}, an object that lives elsewhere: it is not followed")
        assert notes["RAG/rag_core"] == "the folder above it FAILED (its line says why): nothing was created in it"
        assert notes[CORE] == "its folder FAILED (the line of the folder says why): nothing was written into it"
        assert result.unlisted == [] and result.stopped_at is None
        lines.append([(o.entry.path, o.status, o.note) for o in result.failed])
        # nothing was created under the name, and nothing in the folder the reference names
        assert [m["type"] for m in viya.members[viya.folder_id(CONTENT_ROOT)] if m["name"] == "RAG"] == ["reference"]
        assert viya.members[elsewhere] == []
    assert lines[0] == lines[1]
    # two folders of the name: neither is taken for the one, and nothing goes into either
    twice = Viya()
    first = twice.make_folder(f"{CONTENT_ROOT}/RAG")
    second = twice._create_folder("RAG", twice.folder_id(CONTENT_ROOT))
    result = deploy(twice, manifest)
    note = next(o.note for o in result.failed if o.entry.path == "RAG")
    assert note == (f"2 folders of that name are in the folder (ids {first}, {second}): nothing was written into any "
                    "of them, because nothing says which one is meant - remove or rename the others")
    assert twice.members[first] == [] and twice.members[second] == [] and _statuses(result)[CORE] == FAILED


def test_a_reference_beside_a_report_or_an_entry_the_run_did_not_look_at_is_still_named(manifest):
    """Where no line of an entry names the reference - a report, which is
    never deployed, or an entry `only` left out - the list names it, and
    says that it is not the object listed under the name."""
    viya = Viya()
    deploy(viya, manifest)
    outside = viya.put_member("/Users/someone/My Folder/elsewhere.py", "/files/files/", b"THEIRS = 1\n")
    viya.put_reference(f"{CONTENT_ROOT}/{CORE}", outside)
    viya.put_reference(f"{CONTENT_ROOT}/Prompt Builder/Prompt Builder", "/reports/reports/another")
    result = deploy(viya, manifest, only=["Prompt Builder"])
    assert result.failed == [] and _statuses(result)["Prompt Builder/Prompt Builder"] == NOT_DEPLOYED
    assert result.unlisted == [
        "Prompt Builder/Prompt Builder (reference to a report - not the report that is listed under this name)"]
    result = deploy(viya, manifest)
    assert [o.entry.path for o in result.failed] == [CORE]
    assert result.unlisted == [
        "Prompt Builder/Prompt Builder (reference to a report - not the report that is listed under this name)"]


def test_a_path_that_names_the_object_and_a_reference_names_two_things(manifest):
    """`--to <path>`: the object beside a reference of its name is named by
    its id, where nothing can be taken for it."""
    viya = Viya()
    mine = viya.put_member("/Team/code/__init__.py", "/files/files/", b"OLD = 1\n")
    outside = viya.put_member("/Users/someone/My Folder/elsewhere.py", "/files/files/", b"THEIRS = 1\n")
    viya.put_reference("/Team/code/__init__.py", outside)
    with pytest.raises(DeployError) as raised:
        resolve_target(viya, "file", "/Team/code/__init__.py")
    assert str(raised.value) == (
        f"--to /Team/code/__init__.py: the folder holds the file of that name (id {_id(mine)}) and a reference of "
        f"that name, which names {outside} - name the file by its id")
    entry = manifest.find(CORE, "file")
    assert deploy_to(viya, manifest, entry, "/Team/code/__init__.py").status == FAILED
    assert viya.files[mine] == b"OLD = 1\n" and viya.wrote() == []
    assert deploy_to(viya, manifest, entry, _id(mine)).status == "updated" and viya.files[mine] == b"CORE = 1\n"
    assert viya.files[outside] == b"THEIRS = 1\n"


# ---- what a refused write answered, and the line for the content root -------------
#: A refusal in the shape the pass of 2026-10-13 recorded for a PUT of a file's content whose If-Match was
#: no longer the ETag of the file. Every value is made up.
REFUSED = {"details": ["path: /files/files/00000000-0000-4000-8000-000000000000/content"], "errorCode": 0,
           "httpStatusCode": 412, "links": [],
           "message": 'The values for the request header field "If-Match" ("etag-of-the-read") and the resource\'s '
                      'entity tag ("etag-of-another-write") are not the same.',
           "remediation": "Get the latest representation of the resource, apply the change to it and send the "
                          "request again.", "version": 2}


def test_the_answer_of_a_refused_request_is_shown_whole_or_cut_at_a_word():
    """live-19: '... "remediation":"Get the latest re))' - the first 300
    characters of the JSON, whatever stood there."""
    assert len(json.dumps(REFUSED)) > 300
    assert deploying._answer(Response(412, REFUSED)) == (
        'HTTP 412 The values for the request header field "If-Match" ("etag-of-the-read") and the resource\'s entity '
        'tag ("etag-of-another-write") are not the same. - Get the latest representation of the resource, apply the '
        "change to it and send the request again. (errorCode 0)")
    assert deploying._answer(Response(409, {"message": "An item of that name exists.", "errorCode": 11552})) == \
        "HTTP 409 An item of that name exists. (errorCode 11552)"
    assert deploying._answer(Response(403, {"message": "refused"})) == "HTTP 403 refused"
    assert deploying._answer(Response(500)) == "HTTP 500"
    # a body that is no JSON error: whole while it is short ...
    assert deploying._answer(Response(502, content=b"<html>Bad Gateway</html>")) == "HTTP 502 <html>Bad Gateway</html>"
    assert deploying._answer(Response(400, {"error": "x", "error_description": "y"})) == \
        'HTTP 400 {"error": "x", "error_description": "y"}'
    # ... and a long one ends at a word, and says that it goes on
    page = "The gateway did not get an answer in time. " * 12
    shown = deploying._answer(Response(504, content=page.encode()))
    assert len(page) > 300 and shown.endswith("...") and len(shown) <= len("HTTP 504 ") + 303
    kept = shown[len("HTTP 504 "):-3]
    assert page.startswith(kept) and page[len(kept)] == " " and not kept.endswith(" ")
    # a cut that falls on the end of a word keeps the word
    exact = ("word " * 60)[:299] + " and more words behind it"
    assert deploying._answer(Response(500, content=exact.encode())) == "HTTP 500 " + exact[:299] + "..."
    assert deploying._answer(Response(500, content=b"y" * 300 + b" and a tail")) == "HTTP 500 " + "y" * 300 + "..."
    # no blank at all: the first 300 characters
    assert deploying._answer(Response(500, content=b"x" * 400)) == "HTTP 500 " + "x" * 300 + "..."


def test_the_line_of_a_refused_update_holds_the_whole_answer(command, repo):
    _, _, viya = command()
    (repo / "src/rag_core/__init__.py").write_bytes(b"CORE = 2\n")
    real_put = viya.put

    def put(path, data=None, headers=None, **kwargs):
        if path.endswith("/content"):
            viya.calls.append(("PUT", path, None))
            return Response(412, REFUSED)
        return real_put(path, data=data, headers=headers, **kwargs)
    viya.put = put
    result, text, _ = command()
    assert result.exit_code == 1
    assert (f"  FAILED       file          {CORE}  (the file was left as it is: the update of its content was refused "
            f"(HTTP 412 {REFUSED['message']} - {REFUSED['remediation']} (errorCode 0)))\n") in text


def test_a_content_root_that_is_created_gets_a_line(command):
    """live-19: the run sent the POST for the root folder, and neither the
    dry run nor the run had a line for it."""
    root = "/Users/me/My Folder/ZZ_VERIFY_R20/root1"
    _, _, viya = command("--only", "RAG", "--dry-run")                    # the fake, before anything is on it
    viya.make_folder("/Users/me")
    result, text, _ = command("--content-root", root, "--create", "--dry-run")
    assert result.exit_code == 0, text
    assert (f"  created      folder        {root}  (the folder the content goes into, with every folder above it "
            "that is missing)\n") in text
    assert "14 entries: 13 created, 1 not deployed." in text and viya.wrote() == []
    result, text, _ = command("--content-root", root, "--create")
    assert result.exit_code == 0, text
    lines = text.splitlines()
    assert lines[1] == (f"  created      folder        {root}  (the folder the content goes into; created above it: "
                        "/Users/me/My Folder, /Users/me/My Folder/ZZ_VERIFY_R20)")
    # it is no entry: the count is the manifest's
    assert "14 entries: 13 created, 1 not deployed." in text
    # a folder that is there gets no such line
    result, text, _ = command("--content-root", root)
    assert "the folder the content goes into" not in text and "14 entries: 13 equal, 1 not deployed." in text
    # the folder form of --to, and a root whose parent is there
    result, text, _ = command("--only", "RAG", "--to", "/Users/me/Retrieval", "--create")
    assert "  created      folder        /Users/me/Retrieval  (the folder the content goes into)\n" in text


def test_the_import_prints_the_line_for_a_content_root_it_creates(importing, no_discovery):
    no_discovery()
    result, fake, text = importing(Site(), "--content-root", "/Team/Accelerator", "--create", "--no-reports",
                                   "--no-files", without_report=True)
    assert result.exit_code == 0, text
    assert ("    created      folder        /Team/Accelerator  (the folder the content goes into; created above it: "
            "/Team)\n") in text
