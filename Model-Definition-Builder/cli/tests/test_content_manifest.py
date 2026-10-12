# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The content manifest: the one list of what ships in the content folder.

Three commands act on the list - deploy, export, check - so a list that is
wrong is wrong three times. The first half is what the loader refuses: a kind
it does not know, a source that is not there, one path listed twice, an
object where a folder has to be, and a path that never ships. The second half
is what an entry is expected to be: the bytes of a file, the compared parts
of a step and of a job definition, and the two files mdb writes itself. The
last part reads the manifest of this repository.
"""
import csv
import io
import json
import re
from pathlib import Path

import pytest
import yaml

from content_fakes import CONTENT_ROOT, MANIFEST, PACKAGE, STEP, STEP_NAME, as_registered, make_repo
from mdb import __version__
from mdb.core import content
from mdb.core.content import ManifestError, load_manifest, never_ship, parse_manifest

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path)


def _document(**changes) -> dict:
    document = yaml.safe_load(MANIFEST)
    document.update(changes)
    return document


def _with(entry: dict) -> dict:
    document = yaml.safe_load(MANIFEST)
    document["entries"].append(entry)
    return document


def _problems(repo, document) -> list:
    with pytest.raises(ManifestError) as caught:
        parse_manifest(document, repo)
    return caught.value.problems


# ---- what loads -------------------------------------------------------------
def test_the_manifest_loads_with_its_folders_implied(repo):
    manifest = load_manifest(repo)
    assert manifest.content_root == CONTENT_ROOT
    assert manifest.package_path == repo / "SAS-Viya-Integrations" / "SAS-Agentic-AI-Accelerator.json"
    assert [(e.kind, e.path) for e in manifest.objects()] == [
        ("file", "release.json"), ("file", "Data/ACCELERATOR_RELEASES.csv"),
        ("file", "RAG/rag_core/__init__.py"), ("file", "RAG/rag_core/adapters/__init__.py"),
        ("step", f"RAG/steps/{STEP_NAME}"), ("jobDefinition", "Prompt Builder/UI"),
        ("jobDefinition", "Prompt Builder/Optimize"), ("report", "Prompt Builder/Prompt Builder")]
    # no entry names a folder: each is there because an object sits in it, parents first
    folders = manifest.folders()
    assert [f.path for f in folders] == ["Data", "Prompt Builder", "RAG", "RAG/rag_core", "RAG/steps",
                                         "RAG/rag_core/adapters"]
    assert all(f.implied and f.kind == "folder" for f in folders)
    assert manifest.find("RAG/rag_core", "folder").implied
    assert manifest.find("Prompt Builder/UI", "jobDefinition").parameters[0] == {
        "name": "_contextName", "type": "character", "default": "SAS Studio compute context",
        "label": "Context Name", "required": False, "siteSetting": True}
    assert manifest.find("Prompt Builder/UI", "jobDefinition").parameters[1]["siteSetting"] is False
    # a path is one object of one kind
    assert manifest.find("Prompt Builder/UI", "report") is None
    assert manifest.sources() == ["src/rag_core/__init__.py", "src/rag_core/adapters/__init__.py",
                                  f"src/{STEP_NAME}", "src/index.html", "src/optimize.sas"]


def test_a_listed_folder_keeps_its_description_and_is_not_implied(repo):
    manifest = parse_manifest(_with({"path": "Custom Steps", "kind": "folder", "description": "Steps."}), repo)
    listed = manifest.find("Custom Steps", "folder")
    assert (listed.implied, listed.description) == (False, "Steps.")
    assert "Custom Steps" in [f.path for f in manifest.folders()]


def test_a_missing_or_unreadable_manifest_says_so(tmp_path):
    with pytest.raises(ManifestError, match="does not exist"):
        load_manifest(tmp_path)
    make_repo(tmp_path, manifest="version: [1\n")
    with pytest.raises(ManifestError, match="not readable as YAML"):
        load_manifest(tmp_path)
    make_repo(tmp_path, manifest="- just\n- a list\n")
    with pytest.raises(ManifestError, match="must be a mapping"):
        load_manifest(tmp_path)


# ---- what the loader refuses ------------------------------------------------
def test_an_unknown_kind_is_refused(repo):
    assert _problems(repo, _with({"path": "Flows/Ingest", "kind": "dataFlow"})) == [
        "'Flows/Ingest': unknown kind 'dataFlow' (one of folder, file, step, jobDefinition, report)"]


def test_a_source_that_does_not_exist_is_refused(repo):
    assert _problems(repo, _with({"path": "RAG/jobs/Ingest.sas", "kind": "file", "source": "src/ingest.sas"})) == [
        "'RAG/jobs/Ingest.sas': source 'src/ingest.sas' does not exist in the repository"]
    assert _problems(repo, _with({"path": "Start Here", "kind": "jobDefinition", "code": "src/start.sas",
                                  "form": "src/index.html"})) == [
        "'Start Here': code 'src/start.sas' does not exist in the repository"]
    # and a source has to be a file of the repository, not a way out of it
    assert _problems(repo, _with({"path": "x.txt", "kind": "file", "source": "../outside.txt"})) == [
        "'x.txt': source '../outside.txt' must be a path inside the repository"]
    # the sources are not looked at when a command only needs the list
    manifest = parse_manifest(_with({"path": "RAG/jobs/Ingest.sas", "kind": "file", "source": "src/ingest.sas"}),
                              repo, check_sources=False)
    assert manifest.find("RAG/jobs/Ingest.sas", "file").source == "src/ingest.sas"


def test_two_entries_with_one_path_are_refused(repo):
    """Also when they differ in kind or in case: one path, one object."""
    assert _problems(repo, _with({"path": "Prompt Builder/UI", "kind": "report"})) == [
        "'Prompt Builder/UI': listed twice (as jobDefinition and as report) - one path, one entry"]
    assert _problems(repo, _with({"path": "prompt builder/ui", "kind": "jobDefinition", "form": "src/index.html"})) == [
        "'prompt builder/ui': listed twice (as jobDefinition and as jobDefinition) - one path, one entry"]


@pytest.mark.parametrize("path, reason", [
    ("Showcases/Some Use Case/decision.sas", "'Showcases' is showcase content"),
    ("Example Use Cases/notes.sas", "'Example Use Cases' is example content"),
    ("Examples/Third Use Case/x.sas", "'Examples' is example content"),
    ("RAG/generated/job.sas", "'generated' holds what the Prompt Builder generates for a site's own setups"),
    ("RAG/Generated/job.sas", "'Generated' holds what the Prompt Builder generates for a site's own setups"),
    ("Data/ZZ_VERIFY_releases.sas", "'ZZ_VERIFY_releases.sas' is a test object"),
    ("ZZ_VERIFY_R17/x.sas", "'ZZ_VERIFY_R17' is a test object"),
])
def test_a_path_that_never_ships_is_refused(repo, path, reason):
    assert never_ship(path) == reason
    assert _problems(repo, _with({"path": path, "kind": "file", "source": "src/optimize.sas"})) == [
        f"'{path}': {reason} and never ships"]


def test_what_ships_is_not_caught_by_the_rule():
    for path in ("RAG/rag_core/__init__.py", "Prompt Builder/Prompt Builder", "Data/ACCELERATOR_RELEASES.csv",
                 "RAG/rag_core/generated_ids.py", "Custom Steps/LLM - Get Options.step", "release.json"):
        assert never_ship(path) is None, path


def test_a_content_root_of_showcases_is_refused_and_a_test_root_is_not(repo):
    assert _problems(repo, _document(contentRoot="/SAS Agentic AI Accelerator Showcases")) == [
        "contentRoot: 'SAS Agentic AI Accelerator Showcases' is showcase or example content, which never ships"]
    assert content.content_root_problem("/Users/me/My Folder/ZZ_VERIFY_R17/root") is None
    assert content.content_root_problem("/SAS Agentic AI Accelerator") is None


def test_an_object_where_a_folder_has_to_be_is_refused(repo):
    assert _problems(repo, _with({"path": "RAG/rag_core", "kind": "file", "source": "src/optimize.sas"})) == [
        "'RAG/rag_core' is listed as file and is the folder of 'RAG/rag_core/__init__.py'",
        "'RAG/rag_core' is listed as file and is the folder of 'RAG/rag_core/adapters/__init__.py'"]


def test_every_defect_is_reported_at_once(repo):
    document = _document(version=2)
    document["entries"] += [
        {"path": "/Data/x.csv", "kind": "file", "source": "src/optimize.sas"},
        {"path": "Data/y.csv", "kind": "file"},
        {"path": "Data/z.csv", "kind": "file", "source": "src/optimize.sas", "generated": "release-table"},
        {"path": "Data/w.csv", "kind": "file", "generated": "the-weather"},
        {"path": "Data/v.csv", "kind": "file", "source": "src/optimize.sas", "code": "src/optimize.sas"},
        {"path": "Jobs/A", "kind": "jobDefinition"},
        {"path": "Jobs/B", "kind": "jobDefinition", "code": "src/optimize.sas",
         "parameters": [{"name": "n", "type": "money"}, {"default": "x"}, {"name": "n"}]},
        {"path": "Reports/R", "kind": "report", "checks": {"optionSet": "rag-builder", "images": 3}},
        {"path": "RAG/steps/Other Name.step", "kind": "step", "source": f"src/{STEP_NAME}"},
    ]
    assert _problems(repo, document) == [
        "version must be 1, not 2",
        "entry 9: '/Data/x.csv': a path is written below the content root, with forward slashes and no slash "
        "at either end",
        "'Data/y.csv': a file has either a source or a generator",
        "'Data/z.csv': a file has either a source or a generator",
        "'Data/w.csv': unknown generator 'the-weather' (known: release-table, release-info)",
        "'Data/v.csv': a file has no field(s) code (it takes source, generated)",
        "'Jobs/A': a job definition needs a code or a form",
        "'Jobs/B': parameter 'n' has unknown type 'money' (one of character, numeric, date)",
        "'Jobs/B': a parameter needs a name",
        "'Jobs/B': parameter 'n' is listed twice",
        "'Reports/R': unknown check(s) images (known: optionSet, tables)",
        "'Reports/R': unknown optionSet 'rag-builder' (known: prompt-builder)",
        f"'RAG/steps/Other Name.step': the step in 'src/{STEP_NAME}' is named '{STEP_NAME}', and a step is "
        "registered under the name its file holds - the last part of the path must be that name",
    ]


def test_a_site_setting_and_the_files_of_a_report_are_checked(repo):
    document = _document()
    document["entries"] += [
        {"path": "Jobs/C", "kind": "jobDefinition", "code": "src/optimize.sas",
         "parameters": [{"name": "_contextName", "siteSetting": "yes"}]},
        {"path": "Reports/R", "kind": "report", "files": [
            {"name": "a.png", "sha256": "ABC"}, {"sha256": "0" * 64}, {"name": "b.png", "size": 3}]},
        {"path": "Reports/S", "kind": "report", "files": "a.png"},
        {"path": "Data/x.csv", "kind": "file", "source": "src/optimize.sas", "files": []},
    ]
    assert _problems(repo, document) == [
        "'Jobs/C': parameter '_contextName': siteSetting is true or false",
        "'Reports/R': file 'a.png': sha256 is 64 lower-case hexadecimal digits",
        "'Reports/R': a file of a report needs a name",
        "'Reports/R': file 'b.png' has unknown field(s) size",
        "'Reports/S': files must be a list",
        "'Data/x.csv': a file has no field(s) files (it takes source, generated)",
    ]
    listed = parse_manifest(_with({"path": "Reports/R", "kind": "report", "files": [
        {"name": "a.png", "sha256": "0" * 64}, {"name": "a.png", "sha256": "0" * 64}, {"name": "live.png"}]}), repo)
    # one name may stand twice, and a file that is not static has no hash
    assert listed.find("Reports/R", "report").files == (
        {"name": "a.png", "sha256": "0" * 64}, {"name": "a.png", "sha256": "0" * 64},
        {"name": "live.png", "sha256": None})


def test_the_top_of_the_manifest_is_checked_too(repo):
    assert _problems(repo, {"version": 1, "contentRoot": "SAS Agentic AI Accelerator", "package": "p.zip",
                            "entries": [], "owner": "me"}) == [
        "unknown top-level field(s) owner",
        "contentRoot must be a SAS Content path such as '/SAS Agentic AI Accelerator'",
        "package must be the repository path of the transfer package, a .json file",
        "entries must be a list with at least one entry"]


# ---- what an entry is expected to be ----------------------------------------
def test_a_file_leaves_every_checkout_with_the_same_bytes(repo):
    """The Windows checkout holds CRLF where the repository holds LF. The
    file is deployed with LF either way, and its hash is of those bytes."""
    manifest = load_manifest(repo)
    entry = manifest.find("RAG/rag_core/__init__.py", "file")
    assert (repo / "src/rag_core/__init__.py").read_bytes() == b"CORE = 1\r\n"
    assert content.file_bytes(manifest, entry) == b"CORE = 1\n"
    (repo / "src/rag_core/__init__.py").write_bytes(b"CORE = 1\n")
    assert content.file_bytes(manifest, entry) == b"CORE = 1\n"
    # what is not text is not touched
    assert content.lf_bytes(b"\x89PNG\r\n\xff") == b"\x89PNG\r\n\xff"


def test_a_step_is_compared_by_its_content_not_by_how_the_service_stores_it(repo):
    """The dataFlows service gives a step back with its ports rewritten and
    without the empty `properties`. That is not a difference; a changed
    program, form or port is."""
    assert content.step_differences(STEP, as_registered(STEP)) == []
    assert content.step_differences(STEP, {**as_registered(STEP), "description": "Another text."}) == []
    assert content.step_differences(STEP, {**STEP, "templates": {"SAS": "%put embed;\r\n"}}) == []   # line ends
    assert content.step_differences(STEP, {**STEP, "templates": {"SAS": "%put old;\n"}}) == ["templates"]
    assert content.step_differences(STEP, {**STEP, "ui": "{}"}) == ["ui"]
    renamed = json.loads(json.dumps(STEP))
    renamed["flowMetadata"]["outputPorts"][0]["name"] = "_other"
    assert content.step_differences(STEP, renamed) == ["flowMetadata"]
    fewer = {**STEP, "flowMetadata": {"outputPorts": STEP["flowMetadata"]["outputPorts"]}}
    assert content.step_differences(STEP, fewer) == ["flowMetadata"]
    assert content.step_differences(STEP, {}) == ["templates", "ui", "flowMetadata"]
    # a step without input ports: the service leaves the empty list out
    no_inputs = {**STEP, "flowMetadata": {"inputPorts": [], "outputPorts": STEP["flowMetadata"]["outputPorts"]}}
    assert "inputPorts" not in as_registered(no_inputs)["flowMetadata"]
    assert content.step_differences(no_inputs, as_registered(no_inputs)) == []


def test_a_job_definition_is_its_program_its_form_and_its_parameters(repo):
    manifest = load_manifest(repo)
    ui = content.job_definition_body(manifest, manifest.find("Prompt Builder/UI", "jobDefinition"))
    assert ui == {
        "name": "UI", "type": "Compute", "description": "The page.",
        # no program of its own: what SAS Job Execution stores for a job that only serves a form
        "code": "/* SAS templated code goes here */",
        "parameters": [
            {"version": 1, "name": "_contextName", "defaultValue": "SAS Studio compute context", "type": "character",
             "label": "Context Name", "required": False},
            {"version": 1, "name": "_action", "defaultValue": "prompts,form,execute", "type": "character",
             "required": False}],
        # the form is a property, as the Prompt Builder package has always carried it
        "properties": [{"name": "form", "value": "<!DOCTYPE html>\n<html>ü</html>\n"}]}
    optimize = content.job_definition_body(manifest, manifest.find("Prompt Builder/Optimize", "jobDefinition"))
    assert optimize["code"] == "%put optimize;\n%put done;\n" and optimize["properties"] == []
    assert "description" not in optimize

    assert content.job_differences(ui, json.loads(json.dumps(ui))) == []
    # what the service adds or reorders is not a difference
    stored = {**ui, "id": "j1", "version": 2, "createdBy": "me", "parameters": list(reversed(ui["parameters"])),
              "code": ui["code"].replace("\n", "\r\n")}
    assert content.job_differences(ui, stored) == []
    assert content.job_differences(ui, {**ui, "code": "/* old */"}) == ["code"]
    assert content.job_differences(ui, {**ui, "properties": [{"name": "form", "value": "<html>old</html>"}]}) == ["form"]
    assert content.job_differences(ui, {**ui, "properties": []}) == ["form"]
    assert content.job_differences(ui, {**ui, "parameters": ui["parameters"][:1]}) == ["parameters"]
    tuned = json.loads(json.dumps(ui))
    tuned["parameters"][0]["defaultValue"] = "Team compute context"
    assert content.job_differences(ui, tuned) == ["parameters"]
    assert content.job_differences(optimize, {**optimize, "properties": [{"name": "form", "value": "x"}]}) == ["form"]


def test_a_site_setting_keeps_the_value_of_the_site(repo):
    """`_contextName` and `contentRoot` are values of a site. What is
    compared with a site, and written there, holds the site's value - unless
    a value was given, which wins, also for a parameter that is not marked."""
    manifest = load_manifest(repo)
    entry = manifest.find("Prompt Builder/UI", "jobDefinition")
    assert content.site_settings(entry) == ("_contextName",)
    assert content.site_settings(manifest.find("Prompt Builder/Optimize", "jobDefinition")) == ("contentRoot",)
    wanted = content.job_definition_body(manifest, entry)
    live = json.loads(json.dumps(wanted))
    live["parameters"][0]["defaultValue"] = "Team compute context"
    live["parameters"][1]["defaultValue"] = "form,execute"

    def values(body):
        return [p["defaultValue"] for p in body["parameters"]]

    # compared as the checkout has it, the site's context is a difference; settled, it is not
    assert content.job_differences(wanted, live) == ["parameters"]
    settled = content.settle_parameters(wanted, live, content.site_settings(entry))
    assert values(settled) == ["Team compute context", "prompts,form,execute"]
    assert values(wanted) == ["SAS Studio compute context", "prompts,form,execute"]          # not changed itself
    assert {key: value for key, value in settled.items() if key != "parameters"} == \
        {key: value for key, value in wanted.items() if key != "parameters"}
    # a new definition, and one that lacks the parameter: the default
    assert values(content.settle_parameters(wanted, None, ("_contextName",))) == values(wanted)
    assert values(content.settle_parameters(wanted, {"parameters": []}, ("_contextName",))) == values(wanted)
    # a value that was given is written whatever the site holds
    given = content.settle_parameters(wanted, live, ("_contextName",), {"_contextName": "Other context"})
    assert values(given)[0] == "Other context"
    assert values(content.settle_parameters(wanted, None, (), {"_action": "execute", "notThere": "x"})) == [
        "SAS Studio compute context", "execute"]
    assert values(content.settle_parameters(wanted, live, ("_contextName",), {"_contextName": None})) == [
        "Team compute context", "prompts,form,execute"]


def test_a_part_of_the_manifest_is_a_manifest_of_its_own(repo):
    manifest = load_manifest(repo)
    part = content.part_of(manifest, "RAG/", "/Team/Retrieval/")
    assert part.content_root == "/Team/Retrieval"
    assert [(e.kind, e.path) for e in part.objects()] == [
        ("file", "rag_core/__init__.py"), ("file", "rag_core/adapters/__init__.py"), ("step", f"steps/{STEP_NAME}")]
    assert [f.path for f in part.folders()] == ["rag_core", "steps", "rag_core/adapters"]
    # the entries still come from the same sources
    source = content.CheckoutSource(manifest)
    assert source.file_bytes(part.objects()[0]) == b"CORE = 1\n"
    assert source.step_body(part.objects()[2])["name"] == STEP_NAME
    assert json.loads(source.step_document(part.objects()[2])) == STEP


# ---- the two files mdb writes -----------------------------------------------
def test_the_release_table_is_the_csv_load_releases_writes(repo, monkeypatch):
    """Byte for byte: the loader in SAS and `mdb load-releases` read one format."""
    from typer.testing import CliRunner

    from mdb import cli
    from mdb.core.releases import COLUMNS
    manifest = load_manifest(repo)
    generated = content.file_bytes(manifest, manifest.find("Data/ACCELERATOR_RELEASES.csv", "file"))
    monkeypatch.setattr(cli, "Context", lambda: type("C", (), {"repo": repo})())
    written = repo / "out" / "releases.csv"
    result = CliRunner().invoke(cli.app, ["load-releases", "--csv", str(written)])
    assert result.exit_code == 0, result.output
    assert written.read_bytes() == generated
    rows = list(csv.reader(io.StringIO(generated.decode("utf-8"), newline="")))
    # 2.3.0: one Builder entry, a row per part; 2.2.0: its intro and one entry
    assert tuple(rows[0]) == COLUMNS and len(rows) == 1 + 3 + 2
    assert generated.count(b"\r\n") == len(rows)                       # the csv module's own line ends, kept
    assert generated.startswith(b'"version","release_date"')            # every value quoted


def test_release_json_names_the_release_and_every_entry_with_its_hash(repo, monkeypatch):
    monkeypatch.setattr(content, "checkout_commit", lambda root: ("abc123", "2026-10-12T09:31:55+02:00"))
    manifest = load_manifest(repo)
    text = content.file_bytes(manifest, manifest.find("release.json", "file"))
    info = json.loads(text)
    assert text.endswith(b"\n") and b"\r" not in text
    assert {k: v for k, v in info.items() if k != "entries"} == {
        "kind": "sas-agentic-ai-accelerator/release", "version": __version__, "commit": "abc123",
        "date": "2026-10-12T09:31:55+02:00"}
    assert [(e["kind"], e["path"]) for e in info["entries"]] == [(e.kind, e.path) for e in manifest.all_entries()]
    hashes = {e["path"]: e["hash"] for e in info["entries"]}
    # a folder has no content, a report no source, and a file cannot hold its own hash
    assert [path for path, value in hashes.items() if value is None] == [
        "Data", "Prompt Builder", "RAG", "RAG/rag_core", "RAG/steps", "RAG/rag_core/adapters",
        "release.json", "Prompt Builder/Prompt Builder"]
    assert all(re.fullmatch(r"sha256:[0-9a-f]{64}", value) for value in hashes.values() if value)
    import hashlib
    assert hashes["RAG/rag_core/__init__.py"] == "sha256:" + hashlib.sha256(b"CORE = 1\n").hexdigest()
    # a hash follows the content: the step's program, the job's form
    before = dict(hashes)
    (repo / f"src/{STEP_NAME}").write_text(json.dumps({**STEP, "templates": {"SAS": "%put new;\n"}}), encoding="utf-8")
    (repo / "src/index.html").write_text("<html>new</html>", encoding="utf-8")
    after = {e["path"]: e["hash"] for e in content.release_info(manifest)["entries"]}
    assert {path for path in after if after[path] != before[path]} == {f"RAG/steps/{STEP_NAME}", "Prompt Builder/UI"}


def test_release_json_is_compared_by_its_entries(repo, monkeypatch):
    """The commit and the date say when a folder was deployed: a package
    exported yesterday is not behind the checkout for carrying yesterday's."""
    manifest = load_manifest(repo)
    entry = manifest.find("release.json", "file")
    monkeypatch.setattr(content, "checkout_commit", lambda root: ("old", "2026-10-11T08:00:00+02:00"))
    packaged = content.file_bytes(manifest, entry)
    monkeypatch.setattr(content, "checkout_commit", lambda root: ("new", "2026-10-12T08:00:00+02:00"))
    expected = content.file_bytes(manifest, entry)
    assert packaged != expected
    assert content.generated_differences(entry, expected, packaged) == []
    stale = json.loads(packaged)
    stale["entries"] = [e for e in stale["entries"] if e["path"] != "Prompt Builder/Optimize"]
    next(e for e in stale["entries"] if e["path"] == "Data/ACCELERATOR_RELEASES.csv")["hash"] = "sha256:" + "0" * 64
    stale["entries"].append({"path": "RAG/LIVE_DEMO", "kind": "report", "hash": None})
    assert content.generated_differences(entry, expected, json.dumps(stale).encode("utf-8")) == [
        "does not list jobDefinition 'Prompt Builder/Optimize'",
        "lists report 'RAG/LIVE_DEMO', which the manifest does not",
        "holds another hash for file 'Data/ACCELERATOR_RELEASES.csv' than the checkout gives"]
    assert content.generated_differences(entry, expected, b"not json") == ["is not a readable release.json"]
    table = manifest.find("Data/ACCELERATOR_RELEASES.csv", "file")
    wanted = content.file_bytes(manifest, table)
    assert content.generated_differences(table, wanted, wanted.replace(b"\r\n", b"\n")) == []
    assert content.generated_differences(table, wanted, wanted + b"x") == [
        f"differs from what the checkout generates ({len(wanted) + 1} vs {len(wanted)} bytes)"]


def test_the_commit_of_a_folder_that_is_no_checkout_is_empty(tmp_path, monkeypatch):
    assert content.checkout_commit(tmp_path / "nowhere") == ("", "")

    def no_git(*args, **kwargs):
        raise FileNotFoundError("git")
    monkeypatch.setattr(content.subprocess, "run", no_git)
    assert content.checkout_commit(REPO_ROOT) == ("", "")


# ---- the manifest of this repository ----------------------------------------
@pytest.fixture(scope="module")
def shipped():
    return load_manifest(REPO_ROOT)


def test_the_repository_manifest_loads_and_names_the_one_package(shipped):
    assert shipped.content_root == "/SAS Agentic AI Accelerator"
    assert shipped.package == PACKAGE
    assert content.manifest_file(REPO_ROOT).relative_to(REPO_ROOT).as_posix() == \
        "SAS-Viya-Integrations/package-manifest.yaml"


def _listed(shipped, kind: str, folder: str) -> dict:
    return {e.path[len(folder) + 1:]: e for e in shipped.objects() if e.kind == kind and e.path.startswith(folder + "/")}


def test_every_file_of_the_rag_runtime_is_listed(shipped):
    """An entry per object means a new module of rag_core is not shipped
    until it is listed - and this is what notices that it is not."""
    core = REPO_ROOT / "SAS-Viya-Integrations" / "RAG" / "rag_core"
    in_repo = sorted(p.relative_to(core).as_posix() for p in core.rglob("*.py") if "__pycache__" not in p.parts)
    listed = _listed(shipped, "file", "RAG/rag_core")
    assert sorted(listed) == in_repo
    assert all(e.source == f"SAS-Viya-Integrations/RAG/rag_core/{name}" for name, e in listed.items())
    jobs = REPO_ROOT / "SAS-Viya-Integrations" / "RAG-Ingestion"
    assert sorted(_listed(shipped, "file", "RAG/jobs")) == sorted(p.name for p in jobs.glob("*.sas"))
    models = REPO_ROOT / "SAS-Viya-Integrations" / "RAG"
    assert sorted(_listed(shipped, "file", "RAG/models")) == sorted(p.name for p in models.glob("*.py"))


#: Steps of Custom-Steps/ that are in the repository and do not ship, each with
#: the reason. A step that is neither listed in the manifest nor named here
#: fails the test below, so leaving one out is a decision somebody wrote down.
STEPS_NOT_SHIPPED = {
    "LLM - Get Prompt Experiments.step":
        "reads the Prompt-Experiment-Tracker.json of one model as the step of August 2024 did; "
        "LLM - Get All Prompts is the step that pulls the experiments",
}


def test_every_custom_step_of_the_repository_is_listed_once(shipped):
    steps = REPO_ROOT / "SAS-Viya-Integrations" / "Custom-Steps"
    in_repo = sorted(p.name for p in steps.glob("*.step"))
    listed = sorted(e.name for e in shipped.objects() if e.kind == "step")
    assert set(STEPS_NOT_SHIPPED) <= set(in_repo), "a step named as not shipped is not in the repository"
    assert not set(STEPS_NOT_SHIPPED) & set(listed), "a step is listed and named as not shipped"
    assert listed == [name for name in in_repo if name not in STEPS_NOT_SHIPPED]
    in_repo = listed
    assert sorted(_listed(shipped, "step", "RAG/steps")) == [name for name in in_repo if name.startswith("RAG - ")]
    # the Builder looks the RAG steps up by name under <content root>/RAG/steps
    assert all(e.path.startswith(("RAG/steps/", "Logging and Monitoring/", "Custom Steps/"))
               for e in shipped.objects() if e.kind == "step")


def test_the_paths_other_parts_of_the_product_read_are_the_listed_ones(shipped):
    """Paths that are written down elsewhere: the Data-Driven Content URL and
    the report default name the UI job, the Builder reads the two programs
    and the model template by path, and Load-Fact-Sheets.sas reads Data/."""
    ui = shipped.find("Prompt Builder/SAS-LLM-Prompt-Builder-UI", "jobDefinition")
    assert ui.form == "LLM-Prompt-Builder/dist/index.html" and ui.code is None
    assert shipped.find("Prompt Builder/Optimize-Prompt-DSPy", "jobDefinition").code == \
        "SAS-Viya-Integrations/Prompt-Optimization/Optimize-Prompt-DSPy.sas"
    for path in ("RAG/jobs/Ingest-Documents.sas", "RAG/jobs/Test-Retrieval.sas", "RAG/models/retrieve_context.py",
                 "Data/llm_fact_sheet.csv", "Data/embedding_fact_sheet.csv", "Data/decision_fact_sheet.csv"):
        assert shipped.find(path, "file") is not None, path
    assert shipped.find("Data/ACCELERATOR_RELEASES.csv", "file").generated == "release-table"
    assert shipped.find("release.json", "file").generated == "release-info"
    # Load-Accelerator-Tables includes Load-Fact-Sheets.sas from this folder
    assert shipped.find("Logging and Monitoring/Load-Fact-Sheets.sas", "file") is not None


def test_the_two_setup_jobs_are_listed_with_the_parameters_their_programs_read(shipped):
    """Start Here sits at the root of the content folder and Load-Accelerator-Tables
    beside the files it loads. A parameter of the entry that the program does
    not read, or a setting of the program that is no parameter, is a typo
    nobody would see before the job runs."""
    start = shipped.find("Start Here", "jobDefinition")
    load = shipped.find("Data/Load-Accelerator-Tables", "jobDefinition")
    assert start.code == "SAS-Viya-Integrations/Setup/Start-Here.sas" and start.form is None
    assert load.code == "SAS-Viya-Integrations/Setup/Load-Accelerator-Tables.sas" and load.form is None
    defaults = {p["name"]: p["default"] for p in start.parameters}
    # without this SAS Job Execution opens ODS on _webout and the page is not the answer
    assert defaults["_action"] == "execute" and defaults["_output_type"] == "html"
    for entry, macro in ((start, "_sh_default"), (load, "_lat_default")):
        program = shipped.source_path(entry.code).read_text(encoding="utf-8")
        in_program = dict(re.findall(r"^%" + macro + r"\((\w+),\s*(.*?)\);\s*$", program, flags=re.M))
        assert in_program, f"{entry.path}: no %{macro}(name, value) line found"
        listed = {p["name"]: p["default"] for p in entry.parameters if not p["name"].startswith("_")}
        assert listed == in_program, entry.path
        assert listed["contentRoot"] == shipped.content_root
    reports = {e.path: e.checks for e in shipped.objects() if e.kind == "report"}
    assert reports == {
        "Prompt Builder/Prompt Builder": {"optionSet": "prompt-builder", "tables": ["ACCELERATOR_RELEASES"]},
        "Logging and Monitoring/LLM Usage Report": {
            "tables": ["LLM_LOGS", "PROMPT_EXPERIMENTS", "LLM_FACT_SHEET", "EMBEDDING_FACT_SHEET"]}}


def test_the_jobs_that_run_as_their_user_carry_a_compute_context_that_is_the_sites(shipped):
    """Start Here shows what its session may read, and Load-Accelerator-Tables
    loads as its session: opened without `_contextName`, Start Here ran in the
    SAS Job Execution compute context under a service account and every
    request was answered 401 (the pass of 2026-10-12). Both job definitions
    carry the parameter with the default the Prompt Builder job definition
    has, and it is a site setting in all three - as `contentRoot` is where a
    job reads one."""
    page = shipped.find("Prompt Builder/SAS-LLM-Prompt-Builder-UI", "jobDefinition")
    default = next(p for p in page.parameters if p["name"] == "_contextName")["default"]
    assert default == "SAS Studio compute context"
    for path in ("Start Here", "Data/Load-Accelerator-Tables", page.path):
        entry = shipped.find(path, "jobDefinition")
        context = [p for p in entry.parameters if p["name"] == "_contextName"]
        assert len(context) == 1 and context[0]["default"] == default and context[0]["siteSetting"], path
    for path in ("Start Here", "Data/Load-Accelerator-Tables"):
        assert content.site_settings(shipped.find(path, "jobDefinition")) == ("_contextName", "contentRoot"), path
    # and nothing else is one: every other parameter is the release's
    marked = {p["name"] for e in shipped.objects() if e.kind == "jobDefinition" for p in e.parameters
              if p["siteSetting"]}
    assert marked == {content.CONTEXT_PARAMETER, content.CONTENT_ROOT_PARAMETER}


def test_nothing_of_the_showcases_and_no_retired_object_is_listed(shipped):
    for entry in shipped.all_entries():
        assert never_ship(entry.path) is None, entry.path
    names = {e.name for e in shipped.all_entries()}
    assert not names & {"RAG Builder", "RAG-Builder-UI", "Monitoring Baseline", "LIVE_DEMO"}


def test_the_entries_of_the_repository_manifest_can_all_be_built(shipped):
    """Every entry gives what a deploy would write, and release.json lists
    them all."""
    info = content.release_info(shipped)
    assert len(info["entries"]) == len(shipped.all_entries())
    for entry in shipped.objects():
        if entry.kind == "file":
            assert content.file_bytes(shipped, entry)
        elif entry.kind == "step":
            assert content.step_body(shipped, entry)["name"] == entry.name
        elif entry.kind == "jobDefinition":
            assert content.job_definition_body(shipped, entry)["code"]
