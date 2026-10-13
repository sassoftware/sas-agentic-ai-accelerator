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


def test_the_refusal_of_a_part_is_one_line_on_a_terminal_of_any_width(command, monkeypatch):
    """'Says so in one line': at the width of a terminal the console broke
    the sentence into several."""
    buffer = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=buffer, width=80, force_terminal=False))
    result = CliRunner().invoke(cli.app, ["content-deploy", "--only", "release.json"])
    assert result.exit_code == 1 and deploying.RECORD_NEEDS_ALL in buffer.getvalue().splitlines()
    # the same print carries the line for a folder that is not there
    result = CliRunner().invoke(cli.app, ["content-deploy", "--content-root", "/Team/A folder with a long name/root"])
    assert result.exit_code == 1 and buffer.getvalue().splitlines()[-1] == (
        "there is no folder /Team/A folder with a long name/root, and nothing was created: pass --create to create "
        "it (with every folder above it that is missing)")


def test_the_record_is_not_written_into_a_file_a_site_has_elsewhere(command, manifest):
    """The same hole by another door: `--only release.json --to <a file>`."""
    viya = Viya()
    theirs = viya.put_member("/Team/Other/release.json", "/files/files/", b"{}\n")
    viya.calls.clear()
    outcome = deploy_to(viya, manifest, manifest.find("release.json", "file"), "/Team/Other/release.json")
    assert (outcome.status, outcome.note) == (FAILED, deploying.RECORD_NEEDS_ALL)
    assert viya.calls == [] and viya.files[theirs] == b"{}\n"
    # the command says the same sentence, and nothing else: it ends before it signs in
    result, text, _ = command("--only", "release.json", "--to", "/Team/Other/release.json")
    assert result.exit_code == 1 and text == deploying.RECORD_NEEDS_ALL + "\n"


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


# ---- the command a failed import names ------------------------------------------
def _options_of(command_function) -> set:
    """Every option flag a command of the app takes, as typer declares them."""
    import typer
    declared = typer.main.get_command(cli.app).commands[command_function]
    return {flag for parameter in declared.params if getattr(parameter, "param_type_name", "") == "option"
            for flag in parameter.opts if flag.startswith("--")}


def test_the_command_to_repeat_knows_every_option_of_the_command():
    """gate-19 / live-19: the line was built from a list of arguments that
    had fallen behind the command (--no-files, --out). One function builds
    it, and this test fails when the command gets an option it does not
    know."""
    line = cli._import_command(
        [Path("p.json")], None, "Lib", "TABLE", "cas", True, 30, "Logs", "/Team/Root", "Team context", True, True,
        True, "rules", True)
    assert line == ("mdb builders-import p.json --caslib Lib --report-caslib Logs --table TABLE --server cas "
                    '--content-root /Team/Root --compute-context "Team context" --no-releases --no-reports '
                    "--no-options-capture --create --out rules --no-files --timeout 30")
    words = set(shlex.split(line)) | set(shlex.split(cli._import_command(None, Path("o.json"), None, None, None,
                                                                         False, cli.IMPORT_TIMEOUT)))
    # a dry run prints no such line; --help is typer's own
    assert len(_options_of("builders-import")) >= 15
    assert _options_of("builders-import") - words - {"--help"} == {"--dry-run"}
    # the current directory is where --out points without being given
    assert cli._import_command(None, None, None, None, None, False, cli.IMPORT_TIMEOUT, out=".") == \
        "mdb builders-import"
    assert cli._import_command(None, None, None, None, None, False, cli.IMPORT_TIMEOUT,
                               out="my rules/here") == 'mdb builders-import --out "my rules/here"'


def _repeat(text: str) -> list:
    """The arguments of the command a failed run prints, as a shell reads them."""
    line = text.split("then run:\n", 1)[1].splitlines()[0]
    words = shlex.split(line)
    assert words[:2] == ["mdb", "builders-import"], line
    return words[1:]


def test_the_command_to_repeat_keeps_no_files_and_out(importing, no_discovery, tmp_path):
    """live-19: a run with --no-files named a repeat without it, which writes
    the commands file the run was told not to write."""
    no_discovery()
    viya = importing.viya
    _site_with_its_own_objects(viya)
    viya.refuse[("PUT", "/files/files/")] = 500
    result, fake, text = importing(Site(), "--no-reports", "--no-files")
    assert result.exit_code == 1
    assert "then run:\nmdb builders-import --no-releases --no-reports --no-files\n" in text
    rules = tmp_path / "rules of the site"
    result, fake, text = importing(Site(), "--no-reports", "--out", str(rules))
    assert result.exit_code == 1
    assert _repeat(text) == ["builders-import", "--no-releases", "--no-reports", "--out", rules.as_posix()]


def test_the_command_to_repeat_is_not_refused_its_reports_again(importing, no_discovery, out):
    """gate-19: reports refused for another content root, and the repeat was
    the same command - refused for the same reason, exit code 1 again. The
    repeat leaves the reports out, says so, and RUN AS PRINTED ends well."""
    no_discovery()
    viya = importing.viya
    result, fake, text = importing(Site(**{"Prompt Builder": TUNED}), "--content-root", "/Team/Accelerator",
                                   "--create", "--no-files")
    assert result.exit_code == 1 and "Reports (Prompt Builder/Prompt Builder): NOT imported" in text
    assert _repeat(text) == ["builders-import", "--content-root", "/Team/Accelerator", "--no-releases",
                             "--no-reports", "--create", "--no-files"]
    assert ("(it brings again what did not arrive in place; with --no-reports no report is imported or configured; "
            "--no-reports is added here, because the reports are not imported into /Team/Accelerator - the line "
            "above says why)") in text
    before = len(out.getvalue())
    again = CliRunner().invoke(cli.app, _repeat(text))
    printed = out.getvalue()[before:]
    assert again.exit_code == 0, printed
    assert "Reports (Prompt Builder/Prompt Builder): NOT imported - left out (--no-reports)." in printed
    assert "    7 entries: 7 equal." in printed and "Done." in printed
    assert importing.uploaded == {} and fake.written == []


def test_where_one_package_went_through_the_repeat_says_which_one_needs_a_run_of_its_own(importing, tmp_path):
    """Two packages, the reports of one refused: --no-reports for the repeat
    would leave out the reports of the other as well. The command stays as
    it was run, and its sentence says that it ends the same way and what to
    do about it."""
    nested = {**_built(root="root1", location="/Users/me/My Folder/ZZ_VERIFY_R20").build(), "name": "A test root"}
    other = tmp_path / "pkgs" / "nested.json"
    main = tmp_path / "SAS-Viya-Integrations" / PACKAGE_FILE
    result, fake, text = importing(Site(), str(main), str(other), "--no-files", extra=[(other, nested)])
    assert result.exit_code == 1, text
    assert "imported 3 object(s) through the transfer service." in text and "nested.json" in text
    assert "--no-reports" not in _repeat(text)
    assert ("(it imports again and configures the report from this deployment; the reports of nested.json are not "
            "imported by it either - the line above says why - and it ends with exit code 1 for them: bring that "
            "package in a run of its own with --no-reports)") in text


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


# ---- the compute context: one sentence ---------------------------------------------
def test_one_sentence_says_which_job_definitions_get_the_context_of_the_options():
    """review-19: the line said 'given to a job definition this run creates'
    while one that exists without the parameter gets it too. The line, the
    help of the command and of --compute-context, and the README carry the
    same sentence."""
    assert cli.CONTEXT_RULE == ("given to a job definition the import creates and to one on the site that has no "
                                "_contextName; a _contextName a job definition on the site holds is kept")
    assert cli.CONTEXT_RULE in _flat(cli.builders_import.__doc__)
    readme = (REPOSITORY / "SAS-Viya-Integrations" / "README.md").read_text(encoding="utf-8")
    assert cli.CONTEXT_RULE in _flat(readme)
    import typer
    declared = typer.main.get_command(cli.app).commands["builders-import"]
    option = next(p for p in declared.params if "--compute-context" in getattr(p, "opts", ()))
    assert cli.CONTEXT_RULE in _flat(option.help)
    for text in (cli.builders_import.__doc__, readme, (REPOSITORY / "SAS-Viya-Integrations" /
                                                       "package-manifest.yaml").read_text(encoding="utf-8")):
        assert "this run creates; one the site has keeps" not in _flat(text)
        assert not re.search(r"job definition the import creates gets", _flat(text))


def test_a_job_definition_without_the_parameter_gets_the_context_as_the_sentence_says(importing, no_discovery):
    no_discovery()
    viya = importing.viya
    mine = _site_with_its_own_objects(viya)
    body, etag = viya.jobs[mine[UI_PATH]]
    viya.jobs[mine[UI_PATH]] = ({**body, "parameters": body["parameters"][1:]}, etag)   # no _contextName
    options = {**TUNED, "ragComputeContext": "RAG compute context"}
    result, fake, text = importing(Site(**{"Prompt Builder": options}))
    assert result.exit_code == 0, text
    assert f"Compute context: RAG compute context (the ingestion compute context of the options) - " \
           f"{cli.CONTEXT_RULE}." in text
    assert imports._context(viya) == "RAG compute context"
    # and one that holds a value keeps it
    viya.jobs[mine[UI_PATH]][0]["parameters"][0]["defaultValue"] = "Site context"
    result, fake, text = importing(Site(**{"Prompt Builder": options}))
    assert imports._context(viya) == "Site context"


# ---- what the texts of the two commands say ------------------------------------------
def test_the_help_and_the_readme_say_what_the_commands_do():
    deploy_help, import_help = _flat(cli.content_deploy.__doc__), _flat(cli.builders_import.__doc__)
    readme = _flat((REPOSITORY / "SAS-Viya-Integrations" / "README.md").read_text(encoding="utf-8"))
    assert "only by a run that covers every entry of the manifest" in deploy_help
    assert "A run of a part that names it (--only release.json) is refused with one line before the command signs " \
           "in, and writes nothing." in deploy_help
    assert "only by a run that covers every entry of the manifest - a run of a part that names it, --only " \
           "release.json, is refused" in readme
    assert "or a reference of that name to an object of that kind elsewhere - alone, or beside the object itself - " \
           "fail the entry" in deploy_help
    assert "a reference of that name to an object of that kind elsewhere counts as one of them, alone or beside " \
           "the object itself" in import_help
    assert "or a reference of that name to an object of that kind elsewhere - alone, or beside the object itself " \
           "- fail the entry" in readme
    assert "(--only Data --to \"/Team/Tables/Data\" writes /Team/Tables)" in deploy_help
    assert "--only Data --to \"/Team/Tables/Data\" writes /Team/Tables into contentRoot" in readme
    assert "with --no-reports added where the reports were refused for the content root" in import_help
    # nothing here states what has or has not run against SAS Viya: that belongs in the changelog and the guide
    for text in (deploy_help, import_help):
        assert not re.search(r"not (been )?(run|measured)|has not run|untested", text)


# ---- a custom step is registered without who saved it --------------------------------
SAVED_BY = {"createdBy": "someone", "modifiedBy": "someone else", "creationTimeStamp": "2024-08-01T09:00:00.000Z",
            "modifiedTimeStamp": "2025-01-01T09:00:00.000Z", "version": 2, "eTag": '"abc"', "localUi": "{}",
            "links": [{"rel": "self", "href": "/dataFlows/steps/the-id-on-the-system-it-was-saved-on"}]}


def test_a_step_reaches_a_site_without_the_user_its_step_file_carries(tmp_path, monkeypatch):
    """gate-19: ten .step files of the repository carry the id of the user
    who saved them, and the deploy POSTed the file as it is - while the
    package names no user. A step is registered, and updated, without what
    belongs to the system it was saved on."""
    monkeypatch.setattr(content, "checkout_commit", lambda root: ("abc123", "2026-10-13T09:00:00+02:00"))
    make_repo(tmp_path, sources={f"src/{STEP_NAME}": json.dumps({**SAVED_BY, **STEP})})
    manifest = load_manifest(tmp_path)
    viya = Viya()
    assert deploy(viya, manifest).failed == []
    uri, = viya.at(f"{CONTENT_ROOT}/{STEP_PATH}")
    assert viya.steps[uri] == STEP and not set(SAVED_BY) & set(viya.steps[uri])
    # the update writes the same body
    (tmp_path / f"src/{STEP_NAME}").write_text(json.dumps({**SAVED_BY, **STEP, "ui": "{}"}), encoding="utf-8")
    assert _statuses(deploy(viya, manifest))[STEP_PATH] == "updated"
    assert viya.steps[uri] == {**STEP, "ui": "{}"}
    assert _statuses(deploy(viya, manifest))[STEP_PATH] == EQUAL


def test_no_step_of_the_repository_is_registered_with_a_user():
    shipped = load_manifest(REPOSITORY)
    source = content.CheckoutSource(shipped)
    steps = [entry for entry in shipped.objects() if entry.kind == "step"]
    assert steps
    for entry in steps:
        on_disk = json.loads(shipped.source_path(entry.source).read_text(encoding="utf-8"))
        sent = json.loads(source.step_document(entry))
        assert not {"createdBy", "modifiedBy", "creationTimeStamp", "modifiedTimeStamp", "links", "id"} & set(sent)
        # what is compared, and the name the step is registered under, are those of the file
        assert content.step_differences(on_disk, sent) == [] and sent["name"] == on_disk["name"] == entry.name
        assert {key: on_disk.get(key) for key in ("displayName", "description", "type", "metadataVersion")} == \
            {key: sent.get(key) for key in ("displayName", "description", "type", "metadataVersion")}
