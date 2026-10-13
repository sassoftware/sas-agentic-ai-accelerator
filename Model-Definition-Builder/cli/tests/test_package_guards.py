# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What the one shipped package must hold, and how an export becomes it.

The package is the content folder as a transfer package. Almost everything in
it is a second copy of something the repository holds as source - the UI
bundle, the job programs, rag_core, the custom steps - plus two reports whose
option labels and defaults nothing else in the repository describes.
Exporting it is a step against a live environment, so every way it can go
wrong is checked here instead of by whoever exports next:

  1. the Prompt Builder report holds every option label of the merged UI
     (`mdb options-restore` cannot add one);
  2. its defaults are neutral in the report AND in every per-user state - and
     an export resets them in every copy;
  3. an export is reduced to what the content manifest lists: an object that
     is not listed, an authorization rule, a share, a Showcases folder and a
     `generated` folder do not get into the file;
  4. the content gate: every listed object is in the package and equals the
     checkout, and nothing else is in it.

The first parts test these on fabricated packages. Then the gate runs over the
package file of this repository. That file is exported from the owner's
system, from a content folder that `mdb content-deploy` brought to the
checkout; while it is not in the checkout, the tests that read it are skipped
and say so.

The last part guards the guards: the option labels asked for are the ones the
UI source posts, and the workflow that runs this suite starts on a change to
any file a check here reads.
"""
import base64
import dataclasses
import hashlib
import io
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console
from typer.testing import CliRunner

from content_fakes import (ALL_NEUTRAL, CONTENT_ROOT, PACKAGE, STEP, STEP_NAME, Package, Viya, as_registered,
                           make_repo, package_of)
from mdb.viya.content import deploy
from mdb import cli
from mdb.core import content
from mdb.core.content import load_manifest, parse_manifest
from mdb.core.options import MERGED_REPORT_LABELS
from mdb.core.packages import (
    NEUTRAL_DEFAULTS, check_package, deflate, filter_package, legacy_objects, locate, missing_option_labels,
    non_neutral_defaults, package_differences, package_findings, package_objects, report_labels,
    report_option_copies, reset_report_defaults,
)
from mdb.viya import transfer

# what an import reads a package with
from mdb.core.packages import PackageSource, mapping_preview, named_users, package_root_path, reports_package

REPO_ROOT = Path(__file__).resolve().parents[3]
STEP_PATH = f"RAG/steps/{STEP_NAME}"
REPORT = "Prompt Builder/Prompt Builder"


# ---- fabricated packages --------------------------------------------------
def _b64(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value)
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _report_xml(options: dict) -> str:
    return "<SASReport>" + "".join(
        f'<PromptDefinition label="{label}" hidden="true" isParameter="true" dataType="string" name="pr{i}">'
        f'<DefaultValue><String behavior="fixed">{value}</String></DefaultValue></PromptDefinition>'
        for i, (label, value) in enumerate(options.items())) + "</SASReport>"


def _report(name: str, options: dict, states=()) -> dict:
    wrapper = {"object": {"name": name}, "transferableContent": {
        "content": _report_xml(options), "contentVersion": 1,
        "reportStates": [{"content": _report_xml(state), "contentVersion": 1,
                          "reportState": {"userId": f"user{i}"}} for i, state in enumerate(states)] or None}}
    return {"transferObject": {
        "summary": {"type": "report", "name": name},
        "content": deflate(json.dumps(wrapper, separators=(",", ":")))}}


def _job_definition(name: str, form: str) -> dict:
    return {"transferObject": {
        "summary": {"type": "jobDefinition", "name": name},
        "content": _b64({"name": name, "type": "Compute", "code": "",
                         "properties": [{"name": "form", "value": form}]})}}


def _file(name: str, folder: str, text: str) -> dict:
    return {"transferObject": {
        "summary": {"type": "application/vnd.sas.file+json", "name": name},
        "contentSourceLocation": folder,
        "content": _b64({"fileResource": {"name": name}, "content": _b64(text)})}}


def _folder(name: str) -> dict:
    return {"transferObject": {"summary": {"type": "folder", "name": name}, "content": _b64({"folder": {}})}}


def _package(*details) -> dict:
    return {"name": "test", "transferObjectCount": len(details), "transferDetails": list(details)}


# ---- 1: the labels ----------------------------------------------------------
def test_a_report_with_every_label_passes_and_a_missing_one_is_named():
    assert missing_option_labels(_package(_report("Prompt Builder", ALL_NEUTRAL)), MERGED_REPORT_LABELS) == []
    short = {k: v for k, v in ALL_NEUTRAL.items() if k not in ("ragComputeContext", "enable_pgvector")}
    problems = missing_option_labels(_package(_report("Prompt Builder", short)), MERGED_REPORT_LABELS)
    assert problems == ["the report lacks 2 option label(s): ragComputeContext, enable_pgvector"]


def test_a_user_state_that_lacks_a_label_is_a_problem_of_its_own():
    """A state is a full copy of the report: one saved before an option existed
    does not have it, whatever the report says."""
    package = _package(_report("Prompt Builder", ALL_NEUTRAL,
                               states=[ALL_NEUTRAL, {"viyaHost": "x"}]))
    problems = missing_option_labels(package, ["viyaHost", "enableRag"])
    assert problems == ["report state 2 lacks 1 option label(s): enableRag"]


def test_a_package_without_the_report_fails_every_report_check():
    package = _package(_folder("Prompt Builder"))
    assert missing_option_labels(package, ["viyaHost"]) == ["the package holds no report named 'Prompt Builder'"]
    assert non_neutral_defaults(package) == ["the package holds no report named 'Prompt Builder'"]
    assert report_option_copies(package) == []


# ---- 2: the defaults --------------------------------------------------------
def test_neutral_defaults_pass():
    assert non_neutral_defaults(_package(_report("Prompt Builder", ALL_NEUTRAL, states=[ALL_NEUTRAL]))) == []
    assert NEUTRAL_DEFAULTS == {
        "modelRepositoryID": "", "llmProjectID": "", "embeddingProjectID": "",
        "enableDecision": "true", "enableOptimization": "false", "enableRag": "false",
        "storeSslmode": "prefer",
        # what an exporting site tunes, and an export does not reset
        "judgeModel": "", "modelCardReportURI": "", "minOptimizeSamples": "30",
        "deploymentType": "k8s", "credentialDomain": "agentic-ai-keys",
        "contentRoot": "/SAS Agentic AI Accelerator/RAG", "casServer": "cas-shared-default",
        # TLS verification in manifested models: on, whatever the exporting site chose
        "sslVerify": "true"}
    assert set(NEUTRAL_DEFAULTS) <= set(MERGED_REPORT_LABELS)


@pytest.mark.parametrize("label,left_behind", [
    ("modelRepositoryID", "042c4094-82b2-4453-a692-28bf311b35b4"),
    ("llmProjectID", "c645a9d3-6f04-4c98-bef8-b86f69e330fc"),
    ("embeddingProjectID", "538b5482-d0b7-4660-9e7c-2cc2e408850e"),
    ("enableRag", "true"),
    ("enableOptimization", "true"),
    ("enableDecision", "false"),
    ("storeSslmode", "disable"),
    ("judgeModel", "gpt_5_mini"),
    # a report id of the exporting site, as a relative URI: no host to find
    ("modelCardReportURI", "/reports/reports/1f08db8d-0000-4000-8000-000000000000"),
    ("minOptimizeSamples", "10"),
    ("deploymentType", "aca"),
    ("credentialDomain", "team-keys"),
    # the exporting site switched TLS verification off for its own models
    ("sslVerify", "false"),
    ("contentRoot", "/Team/RAG"),
    ("casServer", "cas-team"),
])
def test_a_default_the_exporter_left_behind_is_found(label, left_behind):
    package = _package(_report("Prompt Builder", {**ALL_NEUTRAL, label: left_behind}))
    assert non_neutral_defaults(package) == [
        f"the report: {label} is '{left_behind}', expected '{NEUTRAL_DEFAULTS[label]}'"]


def test_a_default_left_behind_in_a_user_state_is_found():
    """The trap this exists for: the report was reset, the three copies in
    the per-user states were not, and a user who has one gets its values."""
    package = _package(_report("Prompt Builder", ALL_NEUTRAL,
                               states=[ALL_NEUTRAL, {**ALL_NEUTRAL, "enableRag": "true"}]))
    assert non_neutral_defaults(package) == ["report state 2: enableRag is 'true', expected 'false'"]


def test_a_label_the_report_does_not_have_is_not_judged():
    """Whether a label exists is the first check's question, so the defaults
    of a report from before the switches existed can still be checked."""
    assert non_neutral_defaults(_package(_report("Prompt Builder", {"modelRepositoryID": ""}))) == []
    assert non_neutral_defaults(_package(_report("Prompt Builder", {"x": "1"})), expected={"x": "0"}) == [
        "the report: x is '1', expected '0'"]


# ---- 2b: an export resets the defaults, in every copy --------------------------
TUNED = {**ALL_NEUTRAL, "modelRepositoryID": "042c4094-82b2-4453-a692-28bf311b35b4", "llmProjectID": "c645a9d3",
         "embeddingProjectID": "538b5482", "judgeModel": "gpt_5_mini", "enableRag": "true",
         "enableOptimization": "true", "minOptimizeSamples": "10", "storeSslmode": "disable",
         "modelCardReportURI": "/reports/reports/1f08db8d-0000-4000-8000-000000000000", "sslVerify": "false"}


def test_the_defaults_are_reset_in_all_four_copies_of_the_report():
    """The report and three per-user states: four copies of the XML, each
    with every default. A reset of the report alone leaves the exporting
    site's ids and its judge model in the other three."""
    other = {**ALL_NEUTRAL, "enableRag": "true", "judgeModel": "claude"}
    package = _package(_folder("Prompt Builder"), _report("Prompt Builder", TUNED, states=[TUNED, ALL_NEUTRAL, other]))
    package["description"] = "  spacing   preserved  "
    text = json.dumps(package, indent=2)
    assert len(report_option_copies(package)) == 4 and len(non_neutral_defaults(package)) == 10 + 10 + 2

    fixed, reset = reset_report_defaults(text)
    changed = sorted(label for label in TUNED if TUNED[label] != ALL_NEUTRAL[label])
    assert reset == {"the report": changed, "report state 1": changed,
                     "report state 3": ["enableRag", "judgeModel"]}
    after = json.loads(fixed)
    assert report_option_copies(after) == [ALL_NEUTRAL] * 4
    assert non_neutral_defaults(after) == [] and missing_option_labels(after, MERGED_REPORT_LABELS) == []
    # nothing but the report content changed
    assert after["description"] == package["description"]
    assert after["transferDetails"][0] == package["transferDetails"][0]
    assert after["transferDetails"][1]["transferObject"]["summary"] == package["transferDetails"][1]["transferObject"]["summary"]
    # and a report that is neutral comes back byte for byte
    assert reset_report_defaults(fixed) == (fixed, {})


def test_a_reset_writes_only_what_the_report_holds_and_only_that_report():
    """A label the report lacks is not inserted - whether it is there is the
    first check's question - and no other report is touched."""
    old = {"viyaHost": "https://your-sas-viya-host", "llmProjectID": "c645a9d3", "enableOptimization": "true"}
    package = _package(_report("Prompt Builder", old), _report("LLM Usage Report", {"judgeModel": "theirs"}))
    fixed, reset = reset_report_defaults(json.dumps(package))
    assert reset == {"the report": ["enableOptimization", "llmProjectID"]}
    after = json.loads(fixed)
    assert report_option_copies(after) == [{"viyaHost": "https://your-sas-viya-host", "llmProjectID": "",
                                            "enableOptimization": "false"}]
    assert report_option_copies(after, "LLM Usage Report") == [{"judgeModel": "theirs"}]
    # a report without the JSON wrapper (bare XML behind the envelope) is reset as well
    bare = _package({"transferObject": {"summary": {"type": "report", "name": "Prompt Builder"},
                                        "content": deflate(_report_xml(TUNED))}})
    fixed, reset = reset_report_defaults(json.dumps(bare))
    assert sorted(reset) == ["the report"] and report_option_copies(json.loads(fixed)) == [ALL_NEUTRAL]


# ---- 3: an export is reduced to what the manifest lists ------------------------
@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(content, "checkout_commit", lambda root: ("abc123", "2026-10-12T09:31:55+02:00"))
    # the made-up checkout is no git checkout: the commit it names is taken as its HEAD
    monkeypatch.setattr(content, "commit_standing", lambda root, commit: content.ANCESTOR)
    return make_repo(tmp_path)


@pytest.fixture
def manifest(repo):
    return load_manifest(repo)


def _paths(package: dict) -> list:
    return sorted(f"{item.kind} {item.path if item.path is not None else '(' + item.name + ')'}"
                  for item in locate(package)[1])


def test_objects_are_located_by_the_packages_own_connectors(manifest):
    """Two files called __init__.py are told apart by the folder each sits
    in, and the path is the one below the content root - whatever the root
    is called."""
    built = package_of(manifest, root="A test root")
    package = built.build()
    root, items = locate(package)
    assert root == built.root_uri
    assert _paths(package) == sorted(
        [f"{'flowStep' if e.kind == 'step' else e.kind} {e.path}" for e in manifest.all_entries()] + ["folder "])
    assert next(item for item in items if item.path == "").name == "A test root"


def test_a_root_below_other_folders_is_found_when_it_is_named(manifest):
    """An export of a nested folder brings the folders above it. With the
    exported folder named they are outside the root; without it the root is
    the one folder that has no parent in the package."""
    built = package_of(manifest, above=("Users", "me", "ZZ_VERIFY_R17"))
    package = built.build()
    root, items = locate(package, built.root_uri)
    assert root == built.root_uri
    assert sorted(item.name for item in items if item.path is None) == ["Users", "ZZ_VERIFY_R17", "me"]
    assert next(item for item in items if item.path == REPORT).kind == "report"
    # unnamed, the top folder is taken for the root - and nothing is where the manifest has it
    guessed, items = locate(package)
    assert guessed != built.root_uri
    assert next(item for item in items if item.kind == "report").path == f"me/ZZ_VERIFY_R17/SAS Agentic AI Accelerator/{REPORT}"


def test_a_package_without_an_object_for_its_root_folder_is_read_all_the_same(manifest):
    """Every object still names the folder it sits in. Whether an export of a
    top-level folder carries an object for that folder itself was not
    measured, so neither answer may change what the gate says."""
    built = package_of(manifest)
    package = built.build()
    package["transferDetails"] = [d for d in package["transferDetails"]
                                  if d["transferObject"]["summary"]["links"][0]["uri"] != built.root_uri]
    root, items = locate(package)
    assert root == built.root_uri and "" not in [item.path for item in items]
    assert next(item for item in items if item.kind == "report").path == REPORT
    assert package_differences(package, manifest) == []
    result = filter_package(package, manifest, built.root_uri)
    assert result.dropped == [] and package_differences(package, manifest) == []


def test_an_export_keeps_what_the_manifest_lists_and_nothing_else(manifest):
    built = package_of(manifest)
    report_uri = built.uris.get(REPORT) or next(
        d["transferObject"]["summary"]["links"][0]["uri"] for d in built.details
        if d["transferObject"]["summary"]["type"] == "report")
    demo = built.report("RAG/LIVE_DEMO", {"viyaHost": "x"})                       # another user's report
    built.job("RAG/generated/RAG Ingest - Site Documents", {"code": "x"})  # what the Builder generated
    built.file("RAG/rag_core/notes.py", "x = 1\n")                               # a stray file in a listed folder
    built.file("Showcases/Some Use Case/policy.md", "# policy\n")
    built.file("Example Use Cases/Another Use Case/training_data.csv", "a,b\n")
    built.step("ZZ_VERIFY_steps/RAG - Embed Chunks.step", STEP)
    built.job("RAG/RAG-Builder-UI", {"code": "x"})                                # the retired standalone UI
    built.grant("rule", "7cab3bd6", built.root_uri)
    built.grant("rule", "9da8fe35", report_uri)
    built.grant("share", "Share on LLM Usage Report", demo)
    package = built.build()
    before = len(package["transferDetails"])
    result = filter_package(package, manifest, built.root_uri)

    assert sorted(result.dropped) == sorted([
        "report 'RAG/LIVE_DEMO'", "folder 'RAG/generated'",
        "jobDefinition 'RAG/generated/RAG Ingest - Site Documents'",
        "file 'RAG/rag_core/notes.py'", "folder 'Showcases'", "folder 'Showcases/Some Use Case'",
        "file 'Showcases/Some Use Case/policy.md'", "folder 'Example Use Cases'", "folder 'Example Use Cases/Another Use Case'",
        "file 'Example Use Cases/Another Use Case/training_data.csv'", "folder 'ZZ_VERIFY_steps'",
        "flowStep 'ZZ_VERIFY_steps/RAG - Embed Chunks.step'", "jobDefinition 'RAG/RAG-Builder-UI'",
        "rule '7cab3bd6'", "rule '9da8fe35'", "share 'Share on LLM Usage Report'"])
    assert result.refused == []
    assert len(package["transferDetails"]) == before - 16 == package["transferObjectCount"]
    # what is left is the manifest, and the gate has nothing to say about it
    assert _paths(package) == _paths(package_of(manifest).build())
    assert package_differences(package, manifest) == []
    assert legacy_objects(package) == []
    # nothing in the package names a dropped object any more
    left = json.dumps(package)
    kept = {d["transferObject"]["summary"]["links"][0]["uri"] for d in package["transferDetails"]}
    assert set(package["requestedItems"]) == kept
    for uri in set(re.findall(r'"(/(?:files/files|reports/reports|folders/folders|dataFlows/steps|'
                              r'jobDefinitions/definitions|authorization/\w+)/[0-9a-f-]+)"', left)):
        assert uri in kept, uri


def test_a_path_that_never_ships_is_refused_even_when_the_manifest_lists_it(repo, manifest):
    """The loader refuses such a manifest. This is the second lock: a list
    that got past it - built in code, or by a loader that changed - still
    does not get the object out."""
    listed = parse_manifest({
        "version": 1, "contentRoot": CONTENT_ROOT, "package": PACKAGE,
        "entries": [{"path": "RAG/rag_core/__init__.py", "kind": "file", "source": "src/rag_core/__init__.py"}]}, repo)
    for path in ("Showcases/Some Use Case/policy.sas", "RAG/generated/job.sas", "Data/ZZ_VERIFY_x.sas"):
        listed.entries.append(content.Entry(path=path, kind="file", source="src/optimize.sas"))
    built = Package()
    built.file("RAG/rag_core/__init__.py", "CORE = 1\n")
    for path in ("Showcases/Some Use Case/policy.sas", "RAG/generated/job.sas", "Data/ZZ_VERIFY_x.sas"):
        built.file(path, "%put optimize;\n%put done;\n")
    package = built.build()
    result = filter_package(package, listed, built.root_uri)
    # the folders such a path runs through are refused with it
    assert result.refused == [
        "folder 'Showcases': 'Showcases' is showcase content",
        "folder 'Showcases/Some Use Case': 'Showcases' is showcase content",
        "file 'Showcases/Some Use Case/policy.sas': 'Showcases' is showcase content",
        "folder 'RAG/generated': 'generated' holds what the Prompt Builder generates for a site's own setups",
        "file 'RAG/generated/job.sas': 'generated' holds what the Prompt Builder generates for a site's own setups",
        "file 'Data/ZZ_VERIFY_x.sas': 'ZZ_VERIFY_x.sas' is a test object"]
    assert result.dropped == []
    assert [f"{item.kind} {item.path}" for item in locate(package)[1] if item.kind == "file"] == [
        "file RAG/rag_core/__init__.py"]
    # and the gate says the same of a package that holds one
    problems = package_differences(built.build(), listed)
    assert "file 'RAG/generated/job.sas': 'generated' holds what the Prompt Builder generates for a site's own " \
           "setups and never ships" in problems


PNG = b"\x89PNG\xff"


def _listing(manifest, *files):
    """The manifest with these files listed as the children of its report."""
    index = next(i for i, entry in enumerate(manifest.entries) if entry.path == REPORT)
    manifest.entries[index] = dataclasses.replace(manifest.entries[index], files=tuple(files))
    return manifest


def _with_pictures(manifest, extra=None):
    """A package whose report has the two pictures its entry lists. One says
    by a `parent` connector that it is the report's child, the other only by
    its file resource - as the pictures of the usage report did."""
    _listing(manifest, {"name": "background_blue.png", "sha256": hashlib.sha256(PNG).hexdigest()},
             {"name": "sas-logo-wht.png", "sha256": None})
    built = package_of(manifest, change={REPORT: None})
    report = built.report(REPORT, ALL_NEUTRAL)
    for image in (built.image("background_blue.png", parent_uri=report), built.image("sas-logo-wht.png", report)):
        built.attach(report, image)
    if extra:
        extra(built, report)
    return built


def test_the_pictures_of_a_listed_report_stay_with_it(manifest):
    """A report's pictures are files that sit in no folder: children of the
    report. The ones its entry lists stay; the picture of a report that is
    not listed goes with it."""
    def another_report(built, report):
        demo = built.report("RAG/LIVE_DEMO", {"viyaHost": "x"})
        built.image("demo.png", demo)
    built = _with_pictures(manifest, another_report)
    package = built.build()
    result = filter_package(package, manifest, built.root_uri)
    assert sorted(line for line in result.kept if "belongs to" in line) == [
        f"file '{REPORT}/background_blue.png' (belongs to the report)",
        f"file '{REPORT}/sas-logo-wht.png' (belongs to the report)"]
    assert sorted(result.dropped) == ["file 'demo.png' (belongs to report 'RAG/LIVE_DEMO')", "report 'RAG/LIVE_DEMO'"]
    assert package_differences(package, manifest) == []
    # a report whose picture was left behind is a difference
    lost = package_of(_listing(manifest), change={REPORT: None})
    lost.report(REPORT, ALL_NEUTRAL, images=["/files/files/e57e4290-7b82-44c4-a448-f74f4497145c"])
    assert package_differences(lost.build(), manifest) == [
        f"report '{REPORT}' depends on /files/files/e57e4290-7b82-44c4-a448-f74f4497145c, which is not in the package"]


def test_a_child_of_a_report_that_its_entry_does_not_list_does_not_ship(manifest):
    """Whatever said it belonged to a listed report stayed in the package and
    was never looked at: a file `secrets.env` with a parent connector to the
    Prompt Builder report passed the gate."""
    def a_stray_child(built, report):
        built.image("secrets.env", report, data=b"API_KEY=sk-live-123")
    built = _with_pictures(manifest, a_stray_child)
    assert package_differences(built.build(), manifest) == [
        f"file 'secrets.env' belongs to report '{REPORT}' and is not among the files its manifest entry lists"]
    package = built.build()
    result = filter_package(package, manifest, built.root_uri)
    assert result.dropped == [f"file 'secrets.env' (belongs to report '{REPORT}', whose manifest entry does not "
                              "list it)"]
    assert "secrets.env" not in json.dumps(package) and package_differences(package, manifest) == []


def _repaint(built, name: str, data: bytes) -> None:
    """Give the pictures of that name other bytes."""
    for detail in built.details:
        obj = detail["transferObject"]
        if obj["summary"]["name"] == name:
            body = json.loads(base64.b64decode(obj["content"]))
            body["content"] = base64.b64encode(data).decode("ascii")
            obj["content"] = base64.b64encode(json.dumps(body).encode("utf-8")).decode("ascii")


def test_a_picture_is_held_to_the_hash_its_entry_gives(manifest):
    """The bytes of a picture are in no file of the repository, so the hash
    in the manifest is what the gate compares - content nobody checked."""
    assert package_differences(_with_pictures(manifest).build(), manifest) == []
    changed = _with_pictures(manifest)
    _repaint(changed, "sas-logo-wht.png", b"another logo")
    assert package_differences(changed.build(), manifest) == []          # listed without a hash: by its name
    _repaint(changed, "background_blue.png", b"another picture")
    assert package_differences(changed.build(), manifest) == [
        f"file 'background_blue.png' of report '{REPORT}' is not the file its manifest entry lists (SHA-256 "
        f"{hashlib.sha256(b'another picture').hexdigest()})",
        f"report '{REPORT}': the file 'background_blue.png' its manifest entry lists is not in the package"]
    # two files of one name and one hash, as the usage report has them: both listed, both there
    _listing(manifest, *[{"name": "background_blue.png", "sha256": hashlib.sha256(PNG).hexdigest()}] * 2)
    twice = package_of(manifest, change={REPORT: None})
    report = twice.report(REPORT, ALL_NEUTRAL)
    twice.attach(report, twice.image("background_blue.png", report))
    assert package_differences(twice.build(), manifest) == [
        f"report '{REPORT}': the file 'background_blue.png' its manifest entry lists is not in the package"]
    twice.attach(report, twice.image("background_blue.png", parent_uri=report))
    assert package_differences(twice.build(), manifest) == []


def test_a_picture_from_a_folder_of_somebodys_is_not_a_child_of_the_report(manifest):
    """A report can show a picture that sits in any folder of SAS Content.
    That the report names it kept such a file in the package while its folder
    was dropped: a file `holiday.png` from `Private Pictures`."""
    built = package_of(manifest, change={REPORT: None}, above=("Top",))
    pictures = built._folder_object("Private Pictures", next(
        d["transferObject"]["summary"]["links"][0]["uri"] for d in built.details
        if d["transferObject"]["summary"]["name"] == "Top"))
    holiday = built._add("application/vnd.sas.file+json", "holiday.png", "/files/files/", _b64(
        {"fileResource": {"name": "holiday.png"}, "content": _b64("a holiday")}), pictures)
    loose = built.image("loose.png")                                   # in no folder, and nobody's child
    report = built.report(REPORT, ALL_NEUTRAL, images=[holiday, loose])
    package = built.build()
    result = filter_package(package, manifest, built.root_uri)
    assert sorted(line for line in result.dropped if "png" in line or "Private" in line) == [
        "file 'holiday.png' (not below the content root)", "file 'loose.png' (not below the content root)",
        "folder 'Private Pictures' (not below the content root)"]
    assert "holiday.png" not in json.dumps(package) and "loose.png" not in json.dumps(package)
    # and the report that shows them is then a difference: it lacks what it depends on
    assert package_differences(package, manifest) == [
        f"report '{REPORT}' depends on {uri}, which is not in the package" for uri in sorted([holiday, loose])]


# ---- 4: the content gate ---------------------------------------------------------
def test_a_package_that_is_the_checkout_passes(manifest):
    assert package_differences(package_of(manifest).build(), manifest) == []
    # under another root too, with CRLF where the checkout has LF
    crlf = content.file_bytes(manifest, manifest.find("RAG/rag_core/__init__.py", "file")).replace(b"\n", b"\r\n")
    moved = package_of(manifest, change={"RAG/rag_core/__init__.py": crlf}, root="Retrieval")
    assert package_differences(moved.build(), manifest) == []


def test_a_file_that_differs_or_is_missing_is_named(manifest):
    package = package_of(manifest, change={"RAG/rag_core/__init__.py": b"CORE = 0\n",
                                           "RAG/rag_core/adapters/__init__.py": None}).build()
    assert package_differences(package, manifest) == [
        "file 'RAG/rag_core/__init__.py' differs from src/rag_core/__init__.py (9 vs 9 bytes)",
        "file 'RAG/rag_core/adapters/__init__.py' is in the manifest but not in the package"]


def test_a_step_is_compared_in_its_program_its_form_and_its_ports(manifest):
    stale = as_registered({**STEP, "templates": {"SAS": "%put old;\n"}, "ui": "{ }"})
    assert package_differences(package_of(manifest, change={STEP_PATH: stale}).build(), manifest) == [
        f"step '{STEP_PATH}': 'templates' differs from src/{STEP_NAME}",
        f"step '{STEP_PATH}': 'ui' differs from src/{STEP_NAME}"]
    ports = as_registered(STEP)
    ports["flowMetadata"]["outputPorts"][0]["maxEntries"] = 2
    assert package_differences(package_of(manifest, change={STEP_PATH: ports}).build(), manifest) == [
        f"step '{STEP_PATH}': 'flowMetadata' differs from src/{STEP_NAME}"]
    assert package_differences(package_of(manifest, change={STEP_PATH: None}).build(), manifest) == [
        f"step '{STEP_PATH}' is in the manifest but not in the package"]
    # a step that was uploaded as a file is not the step
    as_file = package_of(manifest, change={STEP_PATH: None})
    as_file.file(STEP_PATH, json.dumps(STEP))
    assert package_differences(as_file.build(), manifest) == [
        f"file '{STEP_PATH}' is in the package but not in the manifest",
        f"step '{STEP_PATH}' is in the manifest but not in the package"]


def test_a_job_definition_is_compared_in_its_code_its_form_and_its_parameters(manifest):
    ui = content.job_definition_body(manifest, manifest.find("Prompt Builder/UI", "jobDefinition"))
    optimize = content.job_definition_body(manifest, manifest.find("Prompt Builder/Optimize", "jobDefinition"))
    older = {**ui, "properties": [{"name": "form", "value": "<html>the build before</html>"}],
             "parameters": ui["parameters"][:1]}
    package = package_of(manifest, change={"Prompt Builder/UI": older,
                                           "Prompt Builder/Optimize": {**optimize, "code": "%put before;\n"}}).build()
    assert package_differences(package, manifest) == [
        "job definition 'Prompt Builder/UI': 'form' differs from src/index.html",
        "job definition 'Prompt Builder/UI': 'parameters' differs from the parameters of its manifest entry",
        "job definition 'Prompt Builder/Optimize': 'code' differs from src/optimize.sas"]
    package = package_of(manifest, change={"Prompt Builder/UI": {**ui, "code": "<html>pasted as code</html>"},
                                           "Prompt Builder/Optimize": None}).build()
    assert package_differences(package, manifest) == [
        "job definition 'Prompt Builder/UI': 'code' differs from the code of a job definition that only serves a form",
        "jobDefinition 'Prompt Builder/Optimize' is in the manifest but not in the package"]


def test_the_generated_files_are_regenerated_and_compared(manifest, monkeypatch):
    table = content.release_table_bytes(manifest)
    stale = json.loads(content.release_info_bytes(manifest))
    stale["commit"], stale["date"] = "an older commit", "2026-10-01T00:00:00+02:00"
    package = package_of(manifest, change={"release.json": json.dumps(stale).encode("utf-8")}).build()
    assert package_differences(package, manifest) == []                 # commit and date are not compared
    next(e for e in stale["entries"] if e["path"] == STEP_PATH)["hash"] = "sha256:" + "0" * 64
    package = package_of(manifest, change={"release.json": json.dumps(stale).encode("utf-8"),
                                           "Data/ACCELERATOR_RELEASES.csv": table[:-40]}).build()
    assert package_differences(package, manifest) == [
        f"file 'release.json' holds another hash for step '{STEP_PATH}' than the checkout gives",
        f"file 'Data/ACCELERATOR_RELEASES.csv' differs from what the checkout generates ({len(table) - 40} vs "
        f"{len(table)} bytes)"]


def test_a_report_is_checked_by_what_its_entry_names(manifest):
    def problems(options=ALL_NEUTRAL, **kwargs):
        built = package_of(manifest, change={REPORT: None})
        built.report(REPORT, options, **kwargs)
        return package_differences(built.build(), manifest)

    assert problems() == []
    short = {k: v for k, v in ALL_NEUTRAL.items() if k not in ("ragComputeContext", "enable_pgvector")}
    assert problems(short) == [f"report '{REPORT}': the report lacks 2 option label(s): ragComputeContext, enable_pgvector"]
    assert problems({**ALL_NEUTRAL, "judgeModel": "gpt_5_mini"}) == [
        f"report '{REPORT}': the report: judgeModel is 'gpt_5_mini', expected ''"]
    assert problems(states=[ALL_NEUTRAL, {**ALL_NEUTRAL, "enableRag": "true"}]) == [
        f"report '{REPORT}' carries 2 per-user report state(s)",
        f"report '{REPORT}': report state 2: enableRag is 'true', expected 'false'"]
    assert problems(tables=("ACCELERATOR_RELEASES", "HMEQ")) == [
        f"report '{REPORT}' binds the table HMEQ, which its manifest entry does not name"]
    assert problems(tables=()) == [f"report '{REPORT}' does not bind the table ACCELERATOR_RELEASES"]
    assert problems(host="viya4-example.internal.test") == [
        f"report '{REPORT}' names the host viya4-example.internal.test (1 time(s), in plain text)"]
    assert package_differences(package_of(manifest, change={REPORT: None}).build(), manifest) == [
        f"report '{REPORT}' is in the manifest but not in the package"]


def test_what_is_not_listed_is_named_whatever_it_is(manifest):
    built = package_of(manifest)
    built.report("RAG/LIVE_DEMO", {"viyaHost": "x"})
    built.file("RAG/generated/pipeline.yaml", "steps: []\n")
    built.grant("rule", "7cab3bd6", built.root_uri)
    built.grant("share", "Share on LLM Usage Report", built.root_uri)
    built.image("orphan.png")
    assert package_differences(built.build(), manifest) == [
        "report 'RAG/LIVE_DEMO' is in the package but not in the manifest",
        "folder 'RAG/generated' is in the package but not in the manifest",
        "file 'RAG/generated/pipeline.yaml' is in the package but not in the manifest",
        "the package carries the authorization rule '7cab3bd6'",
        "the package carries the authorization share 'Share on LLM Usage Report'",
        "file 'orphan.png' is in the package but not below its content root"]
    assert package_differences({"transferDetails": []}, manifest) == [
        "the package holds no folder, so its objects have no place below a content root"]


# ---- 4b: what the gate did not see ---------------------------------------------
def test_a_second_object_at_a_listed_path_is_a_difference(manifest):
    """One path holds one object. A second report, step or file at a listed
    path was kept by the export and passed the gate - which only noted that
    the path had been seen."""
    built = package_of(manifest)
    built.file("RAG/rag_core/__init__.py", b"CORE = 1\n")                      # the same bytes, a second time
    built.step(STEP_PATH, as_registered(STEP))
    built.report(REPORT, ALL_NEUTRAL)
    assert package_differences(built.build(), manifest) == [
        "file 'RAG/rag_core/__init__.py' is in the package 2 times - one path holds one object",
        f"flowStep '{STEP_PATH}' is in the package 2 times - one path holds one object",
        f"report '{REPORT}' is in the package 2 times - one path holds one object"]
    assert not any(found.drift for found in package_findings(built.build(), manifest))


def test_a_job_definition_holds_one_form_and_no_other_property(manifest):
    """The comparison reads the first `form` property. A second one - the
    page of another build - and a property nobody lists rode along."""
    ui = content.job_definition_body(manifest, manifest.find("Prompt Builder/UI", "jobDefinition"))
    more = {**ui, "properties": ui["properties"] + [
        {"name": "form", "value": "<html>the bundle of another build</html>"},
        {"name": "prompts", "value": "{}"}, {"name": "DeployedResourceName", "value": "x"}]}
    assert package_differences(package_of(manifest, change={"Prompt Builder/UI": more}).build(), manifest) == [
        "job definition 'Prompt Builder/UI' carries 2 'form' properties - a job definition has one form",
        "job definition 'Prompt Builder/UI' carries the properties DeployedResourceName, prompts, which no entry "
        "of the manifest gives a job definition"]
    optimize = content.job_definition_body(manifest, manifest.find("Prompt Builder/Optimize", "jobDefinition"))
    other = {**optimize, "type": "Batch", "properties": [{"name": "note", "value": "x"}]}
    assert package_differences(package_of(manifest, change={"Prompt Builder/Optimize": other}).build(), manifest) == [
        "job definition 'Prompt Builder/Optimize' is of type 'Batch' in its body, its manifest entry says 'Compute'",
        "job definition 'Prompt Builder/Optimize' carries the property note, which no entry of the manifest gives "
        "a job definition"]


def test_a_step_holds_no_properties_and_no_arguments(manifest):
    step = as_registered(STEP)
    assert package_differences(package_of(manifest, change={STEP_PATH: {
        **step, "properties": {"hidden": "1"}, "arguments": {"x": 1}, "type": "other"}}).build(), manifest) == [
        f"step '{STEP_PATH}' is of type 'other' in its body, the .step file says 'code'",
        f"step '{STEP_PATH}' carries 'properties' the .step file does not have",
        f"step '{STEP_PATH}' carries 'arguments' the .step file does not have"]
    # what the service writes itself is not content: another description, the local display fields
    assert package_differences(package_of(manifest, change={STEP_PATH: {
        **step, "description": "Another text.", "localDisplayName": "Einbetten", "localUi": "{}"}}).build(),
        manifest) == []


def test_the_body_of_an_object_is_named_like_its_summary(manifest):
    """An object is found by the name of its summary. A body that says
    another name is another object under a listed one."""
    built = package_of(manifest)
    for detail in built.details:
        obj = detail["transferObject"]
        if obj["summary"]["name"] in (STEP_NAME, "UI", "__init__.py") and obj["summary"]["type"] != "folder":
            body = json.loads(base64.b64decode(obj["content"]))
            if "fileResource" in body:
                body["fileResource"]["name"] = "something_else.py"
            else:
                body["name"] = "Something Else"
            obj["content"] = _b64(body)
    assert package_differences(built.build(), manifest) == [
        "file 'RAG/rag_core/__init__.py' is named 'something_else.py' in its body",
        "file 'RAG/rag_core/adapters/__init__.py' is named 'something_else.py' in its body",
        f"step '{STEP_PATH}' is of name 'Something Else' in its body, the .step file says '{STEP_NAME}'",
        "job definition 'Prompt Builder/UI' is of name 'Something Else' in its body, its manifest entry says 'UI'"]


def test_what_an_object_says_about_itself_is_held_to_what_an_export_writes(manifest):
    """review-18: passing in BOTH modes were a job definition body with an
    absolute link to the exporting host and a step whose transfer object
    carried a connector and a substitution. Read from the real exports, such
    an object has its parentFolder connector only, no substitution, and names
    no host outside its content; anything else fails in both modes."""
    built = package_of(manifest)
    for detail in built.details:
        obj = detail["transferObject"]
        kind, name = obj["summary"]["type"], obj["summary"]["name"]
        if kind == "jobDefinition" and name == "UI":
            body = json.loads(base64.b64decode(obj["content"]))
            body["description"] = "built on https://viya4-exporter.internal.test/SASStudio"
            body["links"] = [{"rel": "self", "href": "https://viya4-exporter.internal.test/jobDefinitions/x"}]
            obj["content"] = _b64(body)
        if kind == "flowStep":
            obj["connectors"] = [{"type": "dataTable", "uri": "/dataTables/x"}]
            obj["substitutions"] = {"host": "x"}
            detail["connectors"].append({"type": "reference", "uri": "/somewhere"})
    found = package_findings(built.build(), manifest)
    texts = [d.text for d in found if not d.drift]
    assert ("jobDefinition 'Prompt Builder/UI' names the host viya4-exporter.internal.test (2 time(s)) in what it says "
            "about itself, outside its content") in texts
    assert f"flowStep '{STEP_PATH}' carries a connector of type 'dataTable' in its transfer object - an export gives " \
           "such an object none there" in texts
    assert f"flowStep '{STEP_PATH}' carries substitutions (host) - an export gives such an object none" in texts
    assert f"flowStep '{STEP_PATH}' carries a connector of type 'reference' - an export gives such an object its " \
           "parentFolder only" in texts
    # a public host in a parameter (the address of the guides) and a host in the content are no such finding
    plain = package_of(manifest)
    assert [d.text for d in package_findings(plain.build(), manifest)] == []


@pytest.mark.parametrize("segment", ["generated (copy)", "Generated-old", "generated"])
def test_a_folder_of_generated_content_under_another_name_never_ships(segment):
    """review-17/18: never_ship took 'generated' only as the exact name."""
    assert content.never_ship(f"RAG/{segment}/job.sas") == \
        f"'{segment}' holds what the Prompt Builder generates for a site's own setups"


def test_a_field_at_the_top_of_a_package_that_an_export_does_not_write_is_a_difference(manifest):
    package = package_of(manifest).build()
    package["createdBy"], package["links"] = "sas-agentic-ai-accelerator", []   # what an export writes
    assert package_differences(package, manifest) == []
    package["authorization"], package["substitutions"] = [{"principal": "someone"}], {"x": "y"}
    assert package_differences(package, manifest) == [
        "the package carries the field(s) authorization, substitutions at its top, which an export does not write"]


def test_the_gate_tells_content_that_is_behind_from_what_a_package_must_never_be(manifest, repo, monkeypatch):
    """`drift` is a difference in content against the checkout - a file, a
    step, a job definition, the two generated files. Everything else is not."""
    stale = json.loads(content.release_info_bytes(manifest))
    stale["version"] = "2.2.0"
    ui = content.job_definition_body(manifest, manifest.find("Prompt Builder/UI", "jobDefinition"))
    built = package_of(manifest, change={
        "release.json": json.dumps(stale).encode("utf-8"),
        "Data/ACCELERATOR_RELEASES.csv": b"another table\n",
        "RAG/rag_core/__init__.py": b"CORE = 0\n",
        STEP_PATH: as_registered({**STEP, "ui": "{ }"}),
        "Prompt Builder/UI": {**ui, "parameters": ui["parameters"][:1]},
        "Prompt Builder/Optimize": None, REPORT: None})
    built.report(REPORT, {**ALL_NEUTRAL, "judgeModel": "gpt_5_mini"}, states=[ALL_NEUTRAL],
                 host="viya4-example.internal.test", tables=())
    built.file("RAG/notes.txt", "x")
    built.grant("share", "a share", built.root_uri)
    found = package_findings(built.build(), manifest)
    assert [line.text for line in found if line.drift] == [
        f"file 'release.json' names release '2.2.0', this checkout is {content.__version__}",
        f"file 'Data/ACCELERATOR_RELEASES.csv' differs from what the checkout generates (14 vs "
        f"{len(content.release_table_bytes(manifest))} bytes)",
        "file 'RAG/rag_core/__init__.py' differs from src/rag_core/__init__.py (9 vs 9 bytes)",
        f"step '{STEP_PATH}': 'ui' differs from src/{STEP_NAME}",
        "job definition 'Prompt Builder/UI': 'parameters' differs from the parameters of its manifest entry"]
    assert [line.text for line in found if not line.drift] == [
        f"report '{REPORT}' names the host viya4-example.internal.test (1 time(s), in plain text)",
        f"report '{REPORT}' carries 1 per-user report state(s)",
        f"report '{REPORT}': the report: judgeModel is 'gpt_5_mini', expected ''",
        f"report '{REPORT}' does not bind the table ACCELERATOR_RELEASES",
        "file 'RAG/notes.txt' is in the package but not in the manifest",
        "the package carries the authorization share 'a share'",
        "jobDefinition 'Prompt Builder/Optimize' is in the manifest but not in the package"]
    # as sentences, in the order of the package: what a release asks to be empty
    assert package_differences(built.build(), manifest) == [line.text for line in found]


# ---- 5: a package as the source of an import --------------------------------------
def test_a_package_gives_what_each_of_its_objects_holds(manifest):
    """`mdb builders-import` brings files, steps and job definitions in place
    with the package as the source: the bytes of a file, the step without
    what belongs to the exporting system, the job definition as the service
    takes it."""
    built = package_of(manifest)
    for detail in built.details:                    # what an export adds to a step and a job definition
        obj = detail["transferObject"]
        if obj["summary"]["type"] in ("flowStep", "jobDefinition"):
            body = json.loads(base64.b64decode(obj["content"]))
            body.update({"id": "exported-id", "createdBy": "the exporter", "modifiedBy": "the exporter",
                         "creationTimeStamp": "2026-10-12T12:03:52Z", "version": 2,
                         "links": [{"rel": "self", "href": "/x/exported-id"}]})
            obj["content"] = _b64(body)
    source = PackageSource(built.build(), manifest)
    listed = source.manifest("/Some/Root/")
    assert listed.content_root == "/Some/Root"
    assert sorted((e.kind, e.path) for e in listed.objects()) == sorted((e.kind, e.path) for e in manifest.objects())
    assert [f.path for f in listed.folders()] == [f.path for f in manifest.folders()]
    assert source.has_content and source.left_out == [] and source.root_path == CONTENT_ROOT
    assert [item.path for item in source.reports] == [REPORT]

    checkout = content.CheckoutSource(manifest)
    for entry in listed.objects():
        if entry.kind == "file":
            assert source.file_bytes(entry) == checkout.file_bytes(manifest.find(entry.path, "file")), entry.path
        elif entry.kind == "step":
            body = source.step_body(entry)
            assert content.step_differences(checkout.step_body(manifest.find(entry.path, "step")), body) == []
            assert not {"id", "links", "createdBy", "modifiedBy", "version", "localUi"} & set(body)
            assert body["name"] == STEP_NAME and body["properties"] == {}
            assert json.loads(source.step_document(entry)) == body
        elif entry.kind == "jobDefinition":
            wanted = checkout.job_body(manifest.find(entry.path, "jobDefinition"))
            body = source.job_body(entry)
            assert content.job_differences(wanted, body) == [] and content.job_extras(wanted, body) == []
            assert set(body) <= {"name", "type", "code", "parameters", "properties", "description"}
            assert all(set(p) <= {"version", "name", "defaultValue", "type", "label", "required"}
                       for p in body["parameters"])
    # which parameters are the site's is read from the manifest of the checkout, where it lists the path -
    # and the compute context and the content root are the site's wherever the packaged definition has them,
    # also without a checkout (an import of a job definition the checkout does not list lost a site's context)
    assert source.site_settings(listed.find("Prompt Builder/UI", "jobDefinition")) == ("_contextName",)
    assert PackageSource(built.build()).site_settings(listed.find("Prompt Builder/UI", "jobDefinition")) == \
        ("_contextName",)
    assert PackageSource(built.build()).site_settings(listed.find("Prompt Builder/Optimize", "jobDefinition")) == \
        ("contentRoot",)


def test_what_a_package_holds_and_is_not_brought_in_place_is_named(manifest):
    built = package_of(manifest, above=("Users",))
    report = next(d["transferObject"]["summary"]["links"][0]["uri"] for d in built.details
                  if d["transferObject"]["summary"]["type"] == "report")
    built.image("logo.png", report)
    built.image("orphan.png")
    built.file("RAG/generated/pipeline.yaml", "steps: []\n")
    built.file("RAG/rag_core/__init__.py", b"CORE = 9\n")                       # a second object at one path
    built.grant("rule", "7cab3bd6", built.root_uri)
    source = PackageSource(built.build(), manifest, built.root_uri)
    assert source.left_out == [
        "folder 'Users' (not below the root folder of the package)",
        "file 'orphan.png' (not below the root folder of the package)",
        "folder 'RAG/generated' ('generated' holds what the Prompt Builder generates for a site's own setups, which "
        "never ships)",
        "file 'RAG/generated/pipeline.yaml' ('generated' holds what the Prompt Builder generates for a site's own "
        "setups, which never ships)",
        "rule '7cab3bd6' (mdb imports no authorization rule and no share)"]
    assert [item.name for item in source.children] == ["logo.png"]
    listed = source.manifest(CONTENT_ROOT)
    assert listed.find("RAG/generated/pipeline.yaml", "file") is None
    # of two objects at one path none is taken
    with pytest.raises(ValueError, match="the package holds 2 objects at this path - none is taken"):
        source.file_bytes(listed.find("RAG/rag_core/__init__.py", "file"))
    assert source.file_bytes(listed.find("RAG/rag_core/adapters/__init__.py", "file")) == b"ADAPTERS = 1\n"


def test_the_paths_of_a_package_are_held_to_the_rule_of_the_manifest(manifest):
    """review-18: a folder named '..' or an object whose name holds a slash
    became an entry, and the deploy asked the Folders service to create
    folders of those names. They are not taken - with everything below such
    a folder - and each gets a line that says why."""
    built = package_of(manifest)
    built.folder("..")
    built.file("../../Victim/evil2.py", b"x = 1\n")
    built.file("RAG/a\\b.py", b"x = 2\n")
    built.file("RAG/ trailing.py", b"x = 3\n")
    built.file("RAG/slash.py", b"x = 4\n")
    built.details[-1]["transferObject"]["summary"]["name"] = "x/y.py"           # a name with a slash in it
    built.file("RAG/rag_core/ok.py", b"ok = 1\n")
    source = PackageSource(built.build(), manifest)
    lines = "\n".join(source.left_out)
    assert "file 'RAG/x/y.py' (its name holds a slash" in lines
    assert ("folder '..' (its name is '..' - a path below the content root is made of names without a slash, not "
            "empty, not '.' or '..', and with no blank at either end)") in lines
    assert "folder '../..' (its name is '..'" in lines
    assert "file '../../Victim/evil2.py' (the name of the folder '..' above it is '..'" in lines
    assert "file 'RAG/a\\b.py' (its name holds a slash" in lines
    assert "file 'RAG/ trailing.py' (its name starts or ends with a blank" in lines
    listed = source.manifest(CONTENT_ROOT)
    assert not [e for e in listed.all_entries() if ".." in e.path.split("/") or "\\" in e.path or " trailing" in e.path]
    assert listed.find("RAG/rag_core/ok.py", "file") is not None


def test_an_object_of_a_kind_mdb_does_not_import_is_named_for_that(manifest):
    """It was 'not below the root folder of the package' - which it was."""
    built = package_of(manifest)
    built._add("dataFlow", "a flow.flw", "/dataFlows/dataFlows/", "", built.folder("RAG"))
    source = PackageSource(built.build(), manifest)
    assert ("dataFlow 'RAG/a flow.flw' (mdb does not import a dataFlow: it brings files, custom steps and job "
            "definitions in place, and reports through the transfer service)") in source.left_out


def test_an_empty_folder_of_a_package_is_brought_in_place(manifest):
    """It got no line and was not created."""
    built = package_of(manifest)
    built.folder("Empty Folder")
    built.folder("RAG/also empty")
    source = PackageSource(built.build(), manifest)
    assert source.empty_folders == ["Empty Folder", "RAG/also empty"]
    listed = source.manifest(CONTENT_ROOT)
    assert listed.find("Empty Folder", "folder") is not None and not listed.find("Empty Folder", "folder").implied
    # the folder of a report is the transfer service's, not an empty folder
    assert "Prompt Builder" not in source.empty_folders
    viya = Viya()
    result = deploy(viya, listed, CONTENT_ROOT, source=source, reports=False)
    assert {o.entry.path: o.status for o in result.outcomes}["Empty Folder"] == "created"
    assert viya.folder_id(f"{CONTENT_ROOT}/Empty Folder") and viya.folder_id(f"{CONTENT_ROOT}/RAG/also empty")
    # a package of empty folders only still has something to bring
    only = Package()
    only.folder("Just a folder")
    assert PackageSource(only.build()).has_content


def test_a_package_names_the_folder_it_was_exported_from(manifest):
    """An export made in SAS Environment Manager from a folder below others
    holds the folders above it; mdb took the topmost for the root, so the
    in-place paths started with the folders above the content. The package's
    one requested item is the folder that was exported."""
    built = package_of(manifest, above=("Users", "me", "My Folder"))
    package = built.build(requested=[built.root_uri])
    root, items = locate(package)
    assert root == built.root_uri
    assert {item.path for item in items if item.kind == "file"} >= {"release.json", "RAG/rag_core/__init__.py"}
    assert PackageSource(package, manifest).manifest(CONTENT_ROOT).find("RAG/rag_core/__init__.py", "file")
    # without it, the topmost folder - as before
    root, _items = locate(built.build())
    assert root != built.root_uri


def test_the_part_for_the_transfer_service_carries_no_field_an_export_does_not_write(manifest):
    built = _with_pictures(manifest)
    package = {**built.build(), "authorization": {"token": "x"}}
    reduced, _held = reports_package(package, built.root_uri)
    assert "authorization" not in reduced and "authorization" in package
    source = PackageSource(package, manifest)
    assert ("the field(s) authorization at the top of the package (an export does not write them, and they are not "
            "handed to the transfer service)") in source.left_out


def test_a_file_a_report_shows_from_a_folder_is_named(manifest):
    built = package_of(manifest)
    shown = built.file("Prompt Builder/shown.png", b"\x89PNG")
    report = next(d["transferObject"]["summary"]["links"][0]["uri"] for d in built.details
                  if d["transferObject"]["summary"]["type"] == "report")
    built.attach(report, shown)
    source = PackageSource(built.build(), manifest)
    assert source.notes == [
        f"report '{REPORT}' shows the file 'Prompt Builder/shown.png' ({shown}) from a folder: the file is brought in "
        "place and has the id this site gives it, and the report keeps naming the id of the package - open the "
        "report and check that it shows the file"]


def test_the_folder_a_package_was_exported_from_is_read_from_its_root_folder(manifest):
    """A folder at the root of SAS Content has no parent and no source
    location; one below others has both. That is how the packages of 2.2.x
    and an export from below a user's folder held it."""
    assert package_root_path(package_of(manifest).build()) == CONTENT_ROOT
    assert package_root_path(package_of(manifest, root="Accelerator").build()) == "/Accelerator"
    nested = package_of(manifest, root="root1", location="/Users/me/My Folder/ZZ_VERIFY_R18")
    assert package_root_path(nested.build()) == "/Users/me/My Folder/ZZ_VERIFY_R18/root1"
    # a root that has a parent and does not say where: not known, and not guessed
    silent = package_of(manifest, root="root1", location="/x").build()
    del next(d for d in silent["transferDetails"]
             if d["transferObject"]["summary"]["name"] == "root1")["transferObject"]["contentSourceLocation"]
    assert package_root_path(silent) is None
    assert package_root_path({"transferDetails": []}) is None


def test_the_part_of_a_package_for_the_transfer_service_is_its_reports(manifest):
    """The reports, the files that are their children and the folders they
    sit in. Nothing else: files, steps and job definitions do not pass the
    transfer service."""
    built = _with_pictures(manifest)
    package = built.build()
    before = json.dumps(package, sort_keys=True)
    reduced, held = reports_package(package, built.root_uri)
    assert json.dumps(package, sort_keys=True) == before                    # the package itself is not changed
    assert sorted(held) == sorted([
        "folder SAS Agentic AI Accelerator", "folder Prompt Builder", f"report {REPORT}",
        f"file {REPORT}/background_blue.png", f"file {REPORT}/sas-logo-wht.png"])
    assert sorted(package_objects(reduced)) == [
        ("file", "background_blue.png"), ("file", "sas-logo-wht.png"), ("folder", "Prompt Builder"),
        ("folder", "SAS Agentic AI Accelerator"), ("report", "Prompt Builder")]
    assert reduced["transferObjectCount"] == 5 == len(reduced["requestedItems"])
    kept = {d["transferObject"]["summary"]["links"][0]["uri"] for d in reduced["transferDetails"]}
    for uri in set(re.findall(r'"(/(?:files/files|reports/reports|folders/folders|dataFlows/steps|'
                              r'jobDefinitions/definitions)/[0-9a-f-]+)"', json.dumps(reduced))):
        assert uri in kept, uri
    # a package without a report: nothing to hand over
    assert reports_package(package_of(manifest, change={REPORT: None}).build())[1] == []


def test_a_dry_run_reads_the_mapping_off_the_package(manifest):
    """The transfer service gives a mapping only for a package that was
    uploaded. A dry run uploads nothing, so it shows what the package says."""
    from mdb.viya.transfer import rehost_mapping
    built = package_of(manifest, change={REPORT: None})
    built.report(REPORT, ALL_NEUTRAL, tables=("ACCELERATOR_RELEASES", "LLM_LOGS"))
    preview = mapping_preview(built.build())
    assert preview["connectors"]["table"] == [
        {"source": f"server=cas-shared-default;library=Public;table={name}",
         "target": f"server=cas-shared-default;library=Public;table={name}"}
        for name in ("ACCELERATOR_RELEASES", "LLM_LOGS")]
    assert preview["substitutions"] == [{"resourceName": "Prompt Builder", "properties": [
        {"name": "VisualElement:ve9", "value": "https://your-sas-viya-host/SASJobExecution/"}]}]
    _mapping, changes = rehost_mapping(preview, "site.example", "ACCELERATOR_RELEASES",
                                       "server=cas-shared-default;library=Team;table=RELEASES")
    assert changes == [
        "Prompt Builder: VisualElement:ve9 -> host site.example",
        "data source server=cas-shared-default;library=Public;table=ACCELERATOR_RELEASES -> "
        "server=cas-shared-default;library=Team;table=RELEASES",
        "data source server=cas-shared-default;library=Public;table=LLM_LOGS left as exported (not the release table)"]


def test_the_user_ids_a_package_names_are_counted():
    """An export names the people who made its objects: at its top, in every
    summary, and inside the bodies - a report's behind the compression."""
    wrapper = {"object": {"name": "Prompt Builder", "createdBy": "author", "modifiedBy": "editor"},
               "transferableContent": {"content": "<SASReport/>", "contentVersion": 1, "reportStates": None}}
    package = {"createdBy": "exporter", "modifiedBy": "exporter", "transferDetails": [
        {"transferObject": {"summary": {"type": "report", "name": "Prompt Builder", "createdBy": "author",
                                        "modifiedBy": "exporter"},
                            "content": deflate(json.dumps(wrapper, separators=(",", ":")))}},
        {"transferObject": {"summary": {"type": "folder", "name": "RAG", "createdBy": "exporter"},
                            "content": _b64({"folder": {"createdBy": "exporter"}, "userName": "exporter"})}}]}
    assert named_users(package) == {"exporter": 6, "author": 2, "editor": 1}
    assert named_users({"transferDetails": []}) == {}


def test_objects_and_report_labels_are_listed():
    package = _package(_folder("Prompt Builder"), _report("Prompt Builder", {"viyaHost": "x", "enableRag": "false"}),
                       _file("a.py", "/SAS Agentic AI Accelerator/RAG", ""), _job_definition("UI", ""))
    assert package_objects(package) == [("folder", "Prompt Builder"), ("report", "Prompt Builder"),
                                        ("file", "a.py"), ("jobDefinition", "UI")]
    assert report_labels(package) == {"Prompt Builder": {"viyaHost", "enableRag"}}
    assert report_labels(_package(_folder("x"))) == {}
    retired = _package(_report("RAG Builder", {"viyaHost": "x"}), _job_definition("RAG-Builder-UI", "<html>"),
                       _job_definition("Scratch", "<html>"))
    assert legacy_objects(retired) == ["report RAG Builder", "jobDefinition RAG-Builder-UI"]


# ---- the package this repository ships ---------------------------------------
NOT_EXPORTED_YET = "the package is exported from the owner's system after this round"


@pytest.fixture(scope="module")
def shipped_manifest():
    return load_manifest(REPO_ROOT)


@pytest.fixture(scope="module")
def shipped(shipped_manifest):
    """The package file of this repository, parsed. While the checkout does
    not hold it, a test that needs it is skipped with the reason."""
    path = shipped_manifest.package_path
    if not path.is_file():
        pytest.skip(NOT_EXPORTED_YET)
    return json.loads(path.read_text(encoding="utf-8"))


def _shipped_report(shipped: dict) -> dict:
    assert "Prompt Builder" in report_labels(shipped), "the package holds no report named 'Prompt Builder'"
    return shipped


def test_shipped_prompt_builder_report_holds_every_option(shipped):
    assert missing_option_labels(_shipped_report(shipped), MERGED_REPORT_LABELS) == []


def test_shipped_prompt_builder_report_has_neutral_defaults(shipped):
    assert non_neutral_defaults(_shipped_report(shipped)) == []


def test_shipped_reports_have_no_user_states_and_name_no_host(shipped_manifest, shipped):
    result = check_package(shipped_manifest.package_path)
    assert [f"report '{s.report}' carries {s.states} per-user report state(s)" for s in result.states] == []
    assert [f"{f.report} names {f.host}" for f in result.findings] == []
    assert result.reports_checked == len([e for e in shipped_manifest.objects() if e.kind == "report"])


def test_shipped_prompt_builder_ui_is_a_job_definition_with_one_form(shipped_manifest, shipped):
    """That the page in the package is the committed bundle is content: it
    is behind as soon as the UI is rebuilt, until the next export, and that
    is drift. What must hold at any commit is that the page is there as a job
    definition that serves one form."""
    entry = shipped_manifest.find("Prompt Builder/SAS-LLM-Prompt-Builder-UI", "jobDefinition")
    item = next((i for i in locate(shipped)[1] if i.kind == "jobDefinition" and i.path == entry.path), None)
    assert item is not None, f"the package holds no job definition at '{entry.path}'"
    body = json.loads(base64.b64decode(item.obj["content"]))
    assert content.job_extras(content.job_definition_body(shipped_manifest, entry), body) == []
    assert [p["name"] for p in body["properties"]] == ["form"] and body["code"] == content.FORM_ONLY_CODE


def test_shipped_package_carries_no_rule_no_share_and_no_retired_ui(shipped):
    kinds = [kind for kind, _name in package_objects(shipped)]
    assert "rule" not in kinds and "share" not in kinds
    assert legacy_objects(shipped) == []


def test_shipped_package_is_nothing_it_must_not_be(shipped_manifest, shipped):
    """The content gate, as `mdb package-check` runs it between releases:
    every entry of the manifest is in the package, nothing else is, and what
    differs from the checkout is content only - drift, which the release
    procedure and the workflow of a tag hold to zero with `--release`."""
    found = package_findings(shipped, shipped_manifest, shipped_manifest.package_path.name)
    assert [line.text for line in found if not line.drift] == []


def test_the_reason_a_package_test_is_skipped_is_the_agreed_one():
    assert NOT_EXPORTED_YET == "the package is exported from the owner's system after this round"


def _transfer_packages_below(folder: Path) -> list:
    """Every transfer package file below a folder, by what the file holds."""
    found = []
    for path in sorted(folder.rglob("*.json")):
        if "node_modules" in path.parts:
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(document, dict) and "transferDetails" in document:
            found.append(path.relative_to(REPO_ROOT).as_posix())
    return found


def test_the_release_package_is_the_only_package_once_it_is_there(shipped_manifest):
    """One package ships. The four files of the earlier releases stay until
    the commit that adds the new one, and go with it: next to it they would be
    four stale copies that some guide still names."""
    found = _transfer_packages_below(REPO_ROOT / "SAS-Viya-Integrations")
    if not shipped_manifest.package_path.is_file():
        assert shipped_manifest.package not in found
        pytest.skip(NOT_EXPORTED_YET)
    assert found == [shipped_manifest.package]


def test_no_code_knows_the_file_names_of_the_earlier_packages():
    """`mdb builders-import` imports the one file. A name of an earlier
    package left in the code would be a second default somebody trips over."""
    source = REPO_ROOT / "Model-Definition-Builder" / "cli" / "src" / "mdb"
    text = "\n".join(path.read_text(encoding="utf-8") for path in sorted(source.rglob("*.py")))
    for name in ("SAS-Agentic-AI-Accelerator-Prompt-Builder", "SAS-Agentic-AI-Accelerator-RAG-Runtime",
                 "SAS-Agentic-AI-Accelerator-RAG-Builder", "LLM Usage Report.json", "SAS Agentic AI Accelerator.json"):
        assert name not in text, name


# ---- package-export: what it writes, what it refuses ---------------------------
@pytest.fixture
def exporting(monkeypatch, repo):
    """`mdb package-export` against a fake transfer service. Returns a
    function that runs the command on a given export and gives back (result,
    printed text, the file the command writes by default, the exports made)."""
    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    out = io.StringIO()
    exported: list = []
    monkeypatch.setattr(cli, "_viya_session", lambda: _Session())
    monkeypatch.setattr(cli, "Context", lambda: SimpleNamespace(repo=repo))
    monkeypatch.setattr(cli, "console", Console(file=out, width=1000, force_terminal=False))
    job = {"state": "completed", "packageUri": "/transfer/packages/p1",
           "links": [{"rel": "self", "href": "/transfer/exportJobs/j1"}]}
    monkeypatch.setattr(transfer, "start_export", lambda session, name, items: exported.append((name, items)) or job)
    monkeypatch.setattr(transfer, "wait_for_job", lambda session, href, timeout=600: job)
    deleted: list = []
    monkeypatch.setattr(transfer, "delete_package", lambda session, uri: deleted.append(uri))
    monkeypatch.setattr(transfer, "delete_job", lambda session, uri: deleted.append(uri))
    exported_deleted = deleted

    def run(built: Package, *args):
        out.seek(0)
        out.truncate()
        del exported[:]
        monkeypatch.setattr(transfer, "folder_by_path",
                            lambda session, path: {"id": built.root_uri.rsplit("/", 1)[-1]})
        monkeypatch.setattr(transfer, "download_package", lambda session, uri: json.dumps(built.build(), indent=2))
        del deleted[:]
        result = CliRunner().invoke(cli.app, ["package-export", *args])
        return result, out.getvalue(), repo / "SAS-Viya-Integrations" / "SAS-Agentic-AI-Accelerator.json", exported
    run.deleted = exported_deleted
    return run


SHIPPED_BEFORE = '{"id": "the package of the last release"}\n'


def test_an_export_of_a_folder_that_is_the_checkout_is_written(exporting, manifest):
    built = package_of(manifest)
    result, text, target, exported = exporting(built)
    assert result.exit_code == 0, text
    assert exported == [("SAS Agentic AI Accelerator", [built.root_uri])]
    written = json.loads(target.read_text(encoding="utf-8"))
    assert package_differences(written, manifest) == []
    assert target.read_bytes().endswith(b"\n") and b"\r" not in target.read_bytes()
    assert "Wrote" in text and "15 objects: 7 folder, 4 file, 1 flowStep, 2 jobDefinition, 1 report; 0 left out" in text
    assert "left out:" not in text
    assert "Keep this file out of the repository" not in text and "names the user id" not in text
    # review-18: the last line said to commit the file "with the CHANGELOG entry that explains the change" - a
    # changelog edit after the export fails the gate of the release
    assert text.rstrip().splitlines()[-1] == (
        "Run `mdb package-check --release`, then commit this file - and nothing else with it: the changelog and every "
        "source the manifest names stay as they were at the deploy (release.json names that commit, and the release "
        "table was made from that changelog). Tag the commit that adds the file.")
    assert "CHANGELOG entry" not in text
    # what the export left on the server is deleted: the package and the record of the export job
    assert exporting.deleted == ["/transfer/packages/p1", "/transfer/exportJobs/j1"]


def test_an_export_whose_package_cannot_be_deleted_still_writes_the_file(exporting, manifest, monkeypatch):
    """live-18: when the DELETE of the export package raised, the command
    ended in a traceback, wrote no file although the package had been
    downloaded, and left the package on the server without a word."""
    def refused(session, uri):
        raise RuntimeError(f"deleting the package {uri} was refused: HTTP 403")
    monkeypatch.setattr(transfer, "delete_package", refused)
    built = package_of(manifest)
    result, text, target, _ = exporting(built)
    assert result.exit_code == 3, text
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert package_differences(json.loads(target.read_text(encoding="utf-8")), manifest) == []
    assert ("  the transfer package p1 is left on the server - delete it with `sas-viya transfer delete --id p1`, or "
            "DELETE /transfer/packages/p1 (deleting the package /transfer/packages/p1 was refused: HTTP 403).") in text
    assert ("SAS-Agentic-AI-Accelerator.json was written and checked; 1 object(s) of the export are left on the "
            "server (named above). Exit code 3.") in text
    # the job record is still deleted
    assert exporting.deleted == ["/transfer/exportJobs/j1"]
    # a download that fails writes nothing - and what the export left is deleted all the same
    monkeypatch.setattr(transfer, "delete_package", lambda session, uri: exporting.deleted.append(uri))

    def unreadable(session, uri):
        raise RuntimeError(f"Downloading the package {uri} failed: HTTP 503")
    monkeypatch.setattr(transfer, "download_package", unreadable)
    target.unlink()
    result = CliRunner().invoke(cli.app, ["package-export"])
    assert result.exit_code == 1 and not target.exists()
    assert exporting.deleted[-2:] == ["/transfer/packages/p1", "/transfer/exportJobs/j1"]


def test_the_delete_of_a_transfer_object_says_when_it_was_refused():
    from mdb.viya.transfer import delete_job, delete_package

    class Session:
        def __init__(self, answer):
            self.answer = answer

        def delete(self, uri):
            if isinstance(self.answer, Exception):
                raise self.answer
            return SimpleNamespace(ok=self.answer < 400, status_code=self.answer, text="no")
    delete_package(Session(204), "/transfer/packages/p1")
    with pytest.raises(RuntimeError, match=r"deleting the package /transfer/packages/p1 was refused: HTTP 403 no"):
        delete_package(Session(403), "/transfer/packages/p1")
    with pytest.raises(RuntimeError, match=r"no answer to the delete of the job record /transfer/exportJobs/j1 "
                                           r"\(TimeoutError: read timed out\)"):
        delete_job(Session(TimeoutError("read timed out")), "/transfer/exportJobs/j1")


def test_an_export_replaces_the_user_ids_it_names(exporting, manifest, repo):
    """M8 / A11: an export carries createdBy / modifiedBy of whoever made the
    objects. The release file names no one: the ids are replaced with a
    neutral text, the command says whose they were, and the gate fails a
    package that still names a user."""
    built = package_of(manifest)
    for detail in built.details:
        detail["transferObject"]["summary"]["createdBy"] = "the.exporter"
        detail["transferObject"]["summary"]["modifiedBy"] = "a.colleague"
    result, text, target, _ = exporting(built)
    assert result.exit_code == 0, text
    assert ("  Replaced the user id(s) a.colleague, the.exporter in createdBy / modifiedBy with "
            f"'sas-agentic-ai-accelerator' (30 place(s)); contentSourceLocation, the path of the folder each object "
            f"was exported from, is left as it is ({CONTENT_ROOT}).\n") in text
    written = target.read_text(encoding="utf-8")
    assert "the.exporter" not in written and "a.colleague" not in written
    assert named_users(json.loads(written)) == {"sas-agentic-ai-accelerator": 30}
    assert package_differences(json.loads(written), manifest) == []
    # --keep-user-ids: only into a file of its own, and then the gate names them
    result, text, target, _ = exporting(built, "--keep-user-ids")
    assert result.exit_code == 2 and "--keep-user-ids needs --out" in text
    elsewhere = repo / "scratch" / "kept.json"
    result, text, _, _ = exporting(built, "--keep-user-ids", "--out", str(elsewhere), "--allow-differences")
    assert result.exit_code == 0, text
    assert "the.exporter" in elsewhere.read_text(encoding="utf-8")
    assert ("the package names the user id(s) a.colleague, the.exporter in createdBy / modifiedBy (30 place(s)) - an "
            "export of the release writes 'sas-agentic-ai-accelerator' there, and `mdb package-check --fix` rewrites "
            "them") in package_differences(json.loads(elsewhere.read_text(encoding="utf-8")), manifest)


EARLIER_PACKAGES = [REPO_ROOT / "SAS-Viya-Integrations" / name for name in (
    "SAS-Agentic-AI-Accelerator-Prompt-Builder.json", "SAS-Agentic-AI-Accelerator-RAG-Builder.json",
    "SAS Agentic AI Accelerator.json")] + [REPO_ROOT / "SAS-Viya-Integrations" / "Logging-Monitoring" /
                                           "LLM Usage Report.json"]


def _decoded(package: dict) -> list:
    """Every part of a package as values: the package without its objects,
    and per object the transfer object with its body (base64 JSON) or its
    report wrapper (behind the compression) decoded in place."""
    from mdb.core.packages import _body, _report_wrapper
    parts = [{k: v for k, v in package.items() if k != "transferDetails"}]
    for detail in package.get("transferDetails") or []:
        obj = dict(detail.get("transferObject") or {})
        wrapper = _report_wrapper(obj)
        obj["content"] = wrapper if wrapper is not None else (_body(obj) or obj.get("content"))
        parts.append({**{k: v for k, v in detail.items() if k != "transferObject"}, "transferObject": obj})
    return parts


def _set_users(value, text):
    if isinstance(value, dict):
        return {k: (text if k in ("createdBy", "modifiedBy") and isinstance(v, str) and v else _set_users(v, text))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_set_users(v, text) for v in value]
    return value


@pytest.mark.parametrize("path", EARLIER_PACKAGES, ids=lambda p: p.name)
def test_every_user_id_of_a_real_export_is_covered_and_nothing_else_changes(path):
    """The four packages of the earlier releases are real exports. After the
    neutralisation no createdBy / modifiedBy names anyone - in the package,
    the summaries, the bodies, the report wrappers - and every other value is
    what it was. (A user id in a per-user report state, a rule or a share is
    in an object the export removes or drops; the gate fails those.)"""
    from mdb.core.packages import NEUTRAL_USER, neutralise_users
    if not path.is_file():
        pytest.skip("the earlier package files are removed by the commit that adds the release package")
    package = json.loads(path.read_text(encoding="utf-8"))
    before = _decoded(package)
    assert {user for user in named_users(package)} - {NEUTRAL_USER}       # it names people to begin with
    replaced = neutralise_users(package)
    assert replaced > 0
    assert set(named_users(package)) == {NEUTRAL_USER}
    after = _decoded(package)
    assert after == _set_users(before, NEUTRAL_USER)
    # the user ids of the export are nowhere in a createdBy / modifiedBy of the text any more
    assert not re.search(r'"(createdBy|modifiedBy)"\s*:\s*"(?!sas-agentic-ai-accelerator")',
                         json.dumps(_decoded(package), ensure_ascii=False))
    # a second pass has nothing to do
    assert neutralise_users(package) == 0


def test_check_fix_rewrites_the_user_ids(checking, manifest):
    built = package_of(manifest)
    for detail in built.details:
        detail["transferObject"]["summary"]["createdBy"] = "the.exporter"
    target = manifest.package_path
    target.write_text(json.dumps(built.build(), indent=2), encoding="utf-8")
    result, text, _ = checking()
    assert result.exit_code == 1 and "the package names the user id(s) the.exporter" in text
    result, text, _ = checking("--fix")
    assert result.exit_code == 0, text
    assert "user id(s) the.exporter in createdBy / modifiedBy -> sas-agentic-ai-accelerator (15)" in text
    assert "the.exporter" not in target.read_text(encoding="utf-8")


def test_an_export_from_a_configured_site_is_made_shippable(exporting, manifest):
    """The folder as the owner's system has it: the report holds the site's
    option values and the user's saved view, the host is the site's, and next
    to the shipped content sit a generated job, another user's report and
    the rules of the site. None of it is in the file, and nobody edited it."""
    built = package_of(manifest, change={REPORT: None})
    built.report(REPORT, TUNED, states=[TUNED], host="viya4-example.internal.test")
    built.report("RAG/LIVE_DEMO", {"viyaHost": "https://viya4-example.internal.test"})
    built.job("RAG/generated/RAG Ingest - Site Documents", {"code": "x"})
    built.grant("rule", "7cab3bd6", built.root_uri)
    result, text, target, _ = exporting(built)
    assert result.exit_code == 0, text
    for line in ("  left out: report 'RAG/LIVE_DEMO'\n", "  left out: folder 'RAG/generated'\n",
                 "  left out: jobDefinition 'RAG/generated/RAG Ingest - Site Documents'\n",
                 "  left out: rule '7cab3bd6'\n",
                 "  removed 1 per-user report state(s) from report 'Prompt Builder'\n",
                 "  replaced viya4-example.internal.test with the placeholder (1 occurrence(s))\n"):
        assert line in text, line
    assert "  reset 10 option default(s) of the report to the neutral values: embeddingProjectID, enableOptimization, " \
           "enableRag, judgeModel, llmProjectID, minOptimizeSamples, modelCardReportURI, modelRepositoryID, " \
           "sslVerify, storeSslmode\n" in text
    written = json.loads(target.read_text(encoding="utf-8"))
    assert report_option_copies(written) == [ALL_NEUTRAL]
    assert "viya4-example" not in target.read_text(encoding="utf-8") and check_package(target).ok
    assert package_differences(written, manifest) == []
    assert "4 left out" in text


def test_an_export_that_differs_from_the_checkout_leaves_the_shipped_package_alone(exporting, manifest):
    """The target is the file `mdb builders-import` reads when no package is
    named. Written first and judged afterwards, a stale step is in the
    checkout with exit code 1 behind it, and the next import from that
    checkout brings it to a site without a line about it."""
    built = package_of(manifest, change={STEP_PATH: as_registered({**STEP, "templates": {"SAS": "%put old;\n"}}),
                                         "RAG/rag_core/adapters/__init__.py": None})
    target = manifest.package_path
    target.write_text(SHIPPED_BEFORE, encoding="utf-8", newline="\n")
    result, text, target, _ = exporting(built)
    assert result.exit_code == 1
    assert target.read_bytes() == SHIPPED_BEFORE.encode("utf-8")
    assert f"  step '{STEP_PATH}': 'templates' differs from src/{STEP_NAME}\n" in text
    assert "  file 'RAG/rag_core/adapters/__init__.py' is in the manifest but not in the package\n" in text
    assert "2 difference(s) between the export and the checkout." in text and "`mdb content-deploy`" in text
    assert "Nothing was written: SAS-Agentic-AI-Accelerator.json is as it was before the export." in text
    assert "Wrote" not in text and "then commit this file" not in text


def test_an_export_of_a_report_that_was_not_saved_with_the_current_ui_is_not_written(exporting, manifest):
    """What only the exporting site can put right: a default can be reset, a
    label the report lacks cannot be added."""
    unsaved = {k: v for k, v in TUNED.items() if not k.startswith(("enableRag", "enable_", "rag"))}
    built = package_of(manifest, change={REPORT: None})
    built.report(REPORT, unsaved)
    result, text, target, _ = exporting(built)
    assert result.exit_code == 1 and not target.exists()
    assert f"  report '{REPORT}': the report lacks 4 option label(s): enableRag, ragComputeContext, enable_pgvector, " \
           "enable_singlestore\n" in text
    assert "is '" not in text                           # the defaults it holds were reset, and are no finding
    assert "Nothing was written: there is no SAS-Agentic-AI-Accelerator.json." in text


def test_an_export_of_another_root_needs_a_file_of_its_own(exporting, manifest, repo):
    """A test folder is not the release. Its export is written where --out
    says, and the file `mdb builders-import` reads is not touched."""
    root = "/Users/me/My Folder/ZZ_VERIFY_R17/root"
    built = package_of(manifest, root="root", above=("Users", "me", "My Folder", "ZZ_VERIFY_R17"))
    result, text, target, exported = exporting(built, "--content-root", root)
    assert result.exit_code == 2 and exported == [] and not target.exists()
    assert f"{root} is not the content root of the release ({CONTENT_ROOT})" in text and "Pass --out" in text

    elsewhere = repo / "scratch" / "r17.json"
    result, text, target, exported = exporting(built, "--content-root", root, "--out", str(elsewhere))
    assert result.exit_code == 0, text
    assert exported == [("root", [built.root_uri])] and not target.exists()
    written = json.loads(elsewhere.read_text(encoding="utf-8"))
    assert package_differences(written, manifest) == []
    # it equals the checkout, and still is not the file of the release: nothing to commit
    assert "then commit this file" not in text
    assert text.rstrip().splitlines()[-1] == (
        "The file equals the checkout and is not the release file "
        "(SAS-Viya-Integrations/SAS-Agentic-AI-Accelerator.json): there is nothing to commit.")
    # the folders above the root were part of the export and are not part of the file
    folders = [name for kind, name in package_objects(written) if kind == "folder"]
    assert "root" in folders and not {"Users", "me", "My Folder", "ZZ_VERIFY_R17"} & set(folders)
    assert "  left out: folder 'ZZ_VERIFY_R17' (not below the content root)\n" in text


def test_a_test_folder_without_reports_is_exported_with_its_differences_allowed(exporting, manifest, repo):
    """`mdb content-deploy` brings no report, so a test folder holds none and
    its export differs from the checkout by exactly that."""
    root = "/Users/me/My Folder/ZZ_VERIFY_R17/root"
    built = package_of(manifest, change={REPORT: None}, root="root")
    elsewhere = repo / "scratch" / "r17.json"
    result, text, _, _ = exporting(built, "--content-root", root, "--out", str(elsewhere))
    assert result.exit_code == 1 and not elsewhere.exists()
    assert f"  report '{REPORT}' is in the manifest but not in the package\n" in text
    result, text, _, _ = exporting(built, "--content-root", root, "--out", str(elsewhere), "--allow-differences")
    assert result.exit_code == 0, text
    assert "1 difference(s) to the checkout - written all the same (--allow-differences): this is not a package " \
           "of the release." in text
    assert elsewhere.is_file()
    # the last line says what to do with such a file - not to commit it, as it did
    assert "commit it" not in text and "then commit this file" not in text
    assert text.rstrip().splitlines()[-1] == (
        f"Keep this file out of the repository. `mdb builders-import {cli._shell_word(elsewhere)} --content-root "
        "<folder> --no-reports` brings what it holds into a test folder.")
    # never for the file of the release
    result, text, target, exported = exporting(built, "--allow-differences")
    assert result.exit_code == 2 and exported == [] and not target.exists()
    assert "--allow-differences needs --out" in text
    result, text, target, exported = exporting(built, "--allow-differences", "--out", str(target))
    assert result.exit_code == 2 and exported == [] and not target.exists()


def test_an_export_of_the_showcases_folder_is_refused_before_it_starts(exporting, manifest, repo):
    built = package_of(manifest, root="SAS Agentic AI Accelerator Showcases")
    result, text, _, exported = exporting(built, "--content-root", "/SAS Agentic AI Accelerator Showcases",
                                          "--out", str(repo / "showcases.json"))
    assert result.exit_code == 2 and exported == [] and not (repo / "showcases.json").exists()
    assert "'SAS Agentic AI Accelerator Showcases' is showcase or example content, which never ships" in text


def test_a_listed_path_that_never_ships_stops_the_export(exporting, manifest, monkeypatch):
    """The second lock, at the command: a manifest object that names such a
    path - the loader would have refused it - does not get it written."""
    manifest.entries.append(content.Entry(path="RAG/generated/job.sas", kind="file", source="src/optimize.sas"))
    monkeypatch.setattr(cli, "_content_manifest", lambda ctx, check_sources=True: manifest)
    built = package_of(manifest)
    result, text, target, _ = exporting(built)
    assert result.exit_code == 1 and not target.exists()
    assert "  never ships, although the manifest lists it: file 'RAG/generated/job.sas': 'generated' holds what the " \
           "Prompt Builder generates for a site's own setups\n" in text


# ---- package-check: the gate as a command ---------------------------------------
@pytest.fixture
def checking(monkeypatch, repo):
    """`mdb package-check` in the fake checkout. Returns a function that runs
    the command and gives back (result, printed text, the package file of
    the checkout)."""
    out = io.StringIO()
    monkeypatch.setattr(cli, "Context", lambda: SimpleNamespace(repo=repo))
    monkeypatch.setattr(cli, "console", Console(file=out, width=1000, force_terminal=False))
    monkeypatch.setattr(cli, "_viya_session", lambda: pytest.fail("the check reads files and signs in nowhere"))

    def run(*args):
        out.seek(0)
        out.truncate()
        result = CliRunner().invoke(cli.app, ["package-check", *args])
        return result, out.getvalue(), repo / "SAS-Viya-Integrations" / "SAS-Agentic-AI-Accelerator.json"
    return run


def test_the_check_says_when_the_checkout_holds_no_package(checking, repo):
    """The state of the repository until the package is exported: not a pass.
    --allow-missing is for the workflow that runs before that day - while a
    package file of an earlier release is still in the checkout."""
    earlier = repo / "SAS-Viya-Integrations" / "SAS-Agentic-AI-Accelerator-Prompt-Builder.json"
    earlier.write_text("{}\n", encoding="utf-8")
    result, text, target = checking()
    assert result.exit_code == 1 and not target.exists()
    assert "There is no SAS-Viya-Integrations/SAS-Agentic-AI-Accelerator.json in this checkout: nothing was checked." \
        in text
    result, text, _ = checking("--allow-missing")
    assert result.exit_code == 0 and "nothing was checked." in text
    # a file that is named and is not there is a mistake either way
    result, text, _ = checking("--allow-missing", str(target))
    assert result.exit_code == 2 and "not a file" in text


def test_allow_missing_ends_with_the_commit_that_adds_the_package(checking, repo):
    """integrate-18: nothing enforced that --allow-missing leaves the
    workflow with the commit that adds the package. That commit removes the
    earlier package files; without them a missing package fails, flag or no
    flag."""
    result, text, _ = checking("--allow-missing")
    assert result.exit_code == 1
    assert ("--allow-missing is not honoured: the package files of the earlier releases are gone from this "
            "checkout, so the commit that adds the package of the release is in it - and the package has to be "
            "here.") in text
    from mdb.core.packages import earlier_packages_present
    release = repo / "SAS-Viya-Integrations" / "SAS-Agentic-AI-Accelerator.json"
    assert earlier_packages_present(repo, release) == []
    # the release file itself is no earlier package; the files of the earlier releases in this checkout are
    release.write_text("{}\n", encoding="utf-8")
    assert earlier_packages_present(repo, release) == []
    shipped = REPO_ROOT / "SAS-Viya-Integrations" / "SAS-Agentic-AI-Accelerator.json"
    assert sorted(earlier_packages_present(REPO_ROOT, shipped)) == sorted(
        path for path in EARLIER_PACKAGES if path.parent.name == "SAS-Viya-Integrations" and path.is_file())


def test_the_check_passes_a_package_that_is_the_checkout(checking, manifest):
    result, text, target = checking()
    target.write_text(json.dumps(package_of(manifest).build(), indent=2), encoding="utf-8", newline="\n")
    result, text, _ = checking()
    assert result.exit_code == 0, text
    assert "1 package(s): every entry of the manifest is in the package and equals the checkout, and nothing else " \
           "is in it." in text
    # --allow-missing allows a missing file, not a wrong one
    result, text, _ = checking("--allow-missing")
    assert result.exit_code == 0, text


def test_the_check_prints_one_line_per_difference_and_fails(checking, manifest, repo):
    built = package_of(manifest, change={"RAG/rag_core/__init__.py": b"CORE = 0\n", "Prompt Builder/Optimize": None})
    built.report("RAG/LIVE_DEMO", {"viyaHost": "x"})
    built.grant("rule", "7cab3bd6", built.root_uri)
    target = manifest.package_path
    target.write_text(json.dumps(built.build()), encoding="utf-8")
    before = target.read_bytes()
    result, text, _ = checking()
    assert result.exit_code == 1
    # a file with other content is drift; the three others are what a package must never be
    assert text.splitlines()[:4] == [
        "drift: SAS-Agentic-AI-Accelerator.json: file 'RAG/rag_core/__init__.py' differs from "
        "src/rag_core/__init__.py (9 vs 9 bytes)",
        "SAS-Agentic-AI-Accelerator.json: report 'RAG/LIVE_DEMO' is in the package but not in the manifest",
        "SAS-Agentic-AI-Accelerator.json: the package carries the authorization rule '7cab3bd6'",
        "SAS-Agentic-AI-Accelerator.json: jobDefinition 'Prompt Builder/Optimize' is in the manifest but not in "
        "the package"]
    assert "3 difference(s) between the package and the checkout." in text
    assert "(1 more line(s) are drift and do not fail.)" in text
    assert target.read_bytes() == before                                    # a check writes nothing
    # --allow-missing is no way around a difference
    assert checking("--allow-missing")[0].exit_code == 1
    # for a release every line is an error, and none is called drift
    result, text, _ = checking("--release")
    assert result.exit_code == 1 and "drift" not in text
    assert "4 difference(s) between the package and the checkout." in text


def test_between_releases_a_change_of_the_checkout_is_drift_and_does_not_fail(checking, manifest, repo):
    """The package is exported from a system, so it holds the content of the
    commit it was exported at. With every difference an error, a fix to a
    program, a changelog entry or a fact sheet row made the workflow and six
    tests fail for every contributor until somebody with access to that
    system exported again."""
    target = manifest.package_path
    target.write_text(json.dumps(package_of(manifest).build()), encoding="utf-8")
    assert checking()[0].exit_code == 0 and checking("--release")[0].exit_code == 0
    # a fix to a program, a changelog entry, a file of the runtime, a step
    (repo / "src/optimize.sas").write_text("%put a fix;\n", encoding="utf-8")
    (repo / "CHANGELOG.md").write_bytes((repo / "CHANGELOG.md").read_bytes() + b"\n- **RAG runtime: a fix.** Text.\n")
    (repo / "src/rag_core/__init__.py").write_bytes(b"CORE = 2\n")
    (repo / f"src/{STEP_NAME}").write_text(json.dumps({**STEP, "ui": "{}"}), encoding="utf-8")
    result, text, _ = checking()
    assert result.exit_code == 0, text
    lines = text.splitlines()
    drift = [line for line in lines if line.startswith("drift: ")]
    assert len(drift) == 8 and all(line.startswith("drift: SAS-Agentic-AI-Accelerator.json: ") for line in drift)
    for expected in (
            "job definition 'Prompt Builder/Optimize': 'code' differs from src/optimize.sas",
            "file 'RAG/rag_core/__init__.py' differs from src/rag_core/__init__.py (9 vs 9 bytes)",
            f"step '{STEP_PATH}': 'ui' differs from src/{STEP_NAME}",
            "file 'release.json' holds another hash for jobDefinition 'Prompt Builder/Optimize' than the checkout gives",
            "file 'release.json' holds another hash for file 'Data/ACCELERATOR_RELEASES.csv' than the checkout gives"):
        assert f"drift: SAS-Agentic-AI-Accelerator.json: {expected}" in lines, expected
    assert any("file 'Data/ACCELERATOR_RELEASES.csv' differs from what the checkout generates" in line for line in drift)
    assert "1 package(s): 8 difference(s) in content to the checkout - drift, which does not fail between releases." \
        in text
    assert "every entry of the manifest is in the package and equals the checkout" not in text
    # the same package fails the gate of a release, line for line
    result, text, _ = checking("--release")
    assert result.exit_code == 1 and "drift" not in text
    assert "8 difference(s) between the package and the checkout." in text
    # and structure fails between releases as it did: an entry added to the manifest is not in the package
    path = repo / "SAS-Viya-Integrations" / "package-manifest.yaml"
    path.write_text(path.read_text(encoding="utf-8")
                    + "  - {path: RAG/jobs/New.sas, kind: file, source: src/optimize.sas}\n", encoding="utf-8")
    result, text, _ = checking()
    assert result.exit_code == 1
    assert "SAS-Agentic-AI-Accelerator.json: folder 'RAG/jobs' is in the manifest but not in the package" in text
    assert "SAS-Agentic-AI-Accelerator.json: file 'RAG/jobs/New.sas' is in the manifest but not in the package" in text
    assert "2 difference(s) between the package and the checkout." in text


def test_a_release_is_not_checked_against_a_package_that_is_missing(checking):
    result, text, target = checking("--release", "--allow-missing")
    assert result.exit_code == 1 and not target.exists() and "nothing was checked." in text


def test_fix_rewrites_what_can_be_rewritten_in_the_file(checking, manifest, repo):
    """A host, a user's state and an option default are in the file and can
    be put right there. Content cannot: that is the folder's to fix."""
    built = package_of(manifest, change={REPORT: None, "RAG/rag_core/__init__.py": b"CORE = 0\n"})
    built.report(REPORT, TUNED, states=[TUNED], host="viya4-example.internal.test")
    elsewhere = repo / "scratch.json"
    elsewhere.write_text(json.dumps(built.build(), indent=2), encoding="utf-8", newline="\n")
    result, text, target = checking(str(elsewhere))
    assert result.exit_code == 1 and not target.exists()
    # the host, the state, and ten defaults of the site in each of the two copies - and the file, which is drift
    assert "22 difference(s)" in text and "names the host viya4-example.internal.test" in text
    assert "(1 more line(s) are drift and do not fail.)" in text

    result, text, _ = checking(str(elsewhere), "--fix", "--release")
    assert result.exit_code == 1
    assert "scratch.json: reset 10 option default(s) of the report: " in text
    assert "scratch.json: reset 10 option default(s) of report state 1: " in text
    assert "scratch.json: removed 1 per-user report state(s) from report 'Prompt Builder'" in text
    assert "scratch.json: viya4-example.internal.test -> your-sas-viya-host (1)" in text
    assert text.splitlines()[-2] == \
        "scratch.json: file 'RAG/rag_core/__init__.py' differs from src/rag_core/__init__.py (9 vs 9 bytes)"
    assert "1 difference(s) between the package and the checkout." in text
    assert report_option_copies(json.loads(elsewhere.read_text(encoding="utf-8"))) == [ALL_NEUTRAL]
    # what is left is content, which the file cannot be fixed for and which does not fail between releases
    result, text, _ = checking(str(elsewhere))
    assert result.exit_code == 0
    assert "drift: scratch.json: file 'RAG/rag_core/__init__.py' differs from src/rag_core/__init__.py" in text


def test_a_manifest_that_cannot_be_used_ends_the_check_with_its_defects(checking, repo):
    path = repo / "SAS-Viya-Integrations" / "package-manifest.yaml"
    path.write_text(path.read_text(encoding="utf-8")
                    + "  - {path: Showcases/Some Use Case/x.sas, kind: file, source: src/optimize.sas}\n", encoding="utf-8")
    result, text, _ = checking()
    assert result.exit_code == 2
    assert "  'Showcases/Some Use Case/x.sas': 'Showcases' is showcase content and never ships\n" in text


# ---- the guards' own inputs --------------------------------------------------
_POSTED = re.compile(
    r"(?:textField|switchField)\(\s*'(?P<call>[A-Za-z0-9_]+)'"
    r"|\bname:\s*(?:'(?P<literal>[A-Za-z0-9_]+)'|(?P<generated>backendOptionKey\(backend\))|(?P<other>\S))")


def _posted_option_names(ddc: str, backends: list) -> list:
    """Every option name the UI source posts to the Options pane, with every
    switch on, in source order. Three spellings are read: textField('x', ...)
    and switchField('x', ...), an object literal's `name: 'x'`, and the one
    generated per vector store, `name: backendOptionKey(backend)`."""
    names: list = []
    for match in _POSTED.finditer(ddc[ddc.index("fields: ["):ddc.index("groups: [")]):
        # a `name:` written any other way is an option this reader would miss
        assert not match.group("other"), "ddc.ts names an option in a way this test does not read"
        if match.group("generated"):
            names.extend(f"enable_{key}" for key in backends)
        else:
            names.append(match.group("call") or match.group("literal"))
    return names


def test_the_reader_of_the_ui_source_finds_all_three_spellings():
    ddc = """fields: [
      textField(
        'viyaHost', 'SAS Viya host', config.viyaHost, 'tip'),
      { name: 'deploymentType', label: 'Deployment type', type: 'String' },
      switchField('enableRag', 'Enable RAG setups', features.rag, 'tip'),
      ...(features.rag ? [
        textField('contentRoot', 'RAG: content root', rb.contentRoot, 'tip'),
        ...RAG_BACKENDS.map((backend) => ({ name: backendOptionKey(backend), type: 'Boolean' })),
      ] : []),
    ],
    groups: [],"""
    assert _posted_option_names(ddc, ["pgvector", "chroma"]) == [
        "viyaHost", "deploymentType", "enableRag", "contentRoot", "enable_pgvector", "enable_chroma"]
    with pytest.raises(AssertionError, match="in a way this test does not read"):
        _posted_option_names(ddc.replace("name: 'deploymentType'", "name: DEPLOYMENT"), [])


def test_the_required_labels_are_the_options_the_ui_posts():
    """MERGED_REPORT_LABELS is what a shipped report is asked for, and what a
    restore counts as an option of this release. The UI source is where an
    option is added or renamed, so the two lists are compared here: an option
    the UI posts and the list lacks would never be asked of the report, and
    one the list holds and the UI dropped would be asked for ever."""
    source = REPO_ROOT / "LLM-Prompt-Builder" / "src"
    ddc, backends = source / "va" / "ddc.ts", source / "objects" / "rag-backends.ts"
    if not ddc.is_file() or not backends.is_file():
        pytest.skip(f"{source} is not in this checkout")
    table = re.search(r"export const RAG_BACKENDS\b.*?=\s*\[(.*?)\n\];", backends.read_text(encoding="utf-8"), re.S)
    keys = re.findall(r"\bkey:\s*'([A-Za-z0-9_]+)'", table.group(1)) if table else []
    assert keys, "RAG_BACKENDS is not declared the way this test reads it"
    posted = _posted_option_names(ddc.read_text(encoding="utf-8"), keys)
    assert len(posted) == len(set(posted)), "ddc.ts posts an option name twice"
    assert [name for name in posted if name not in MERGED_REPORT_LABELS] == [], \
        "posted by the UI, not in MERGED_REPORT_LABELS"
    assert [label for label in MERGED_REPORT_LABELS if label not in posted] == [], \
        "in MERGED_REPORT_LABELS, not posted by the UI"
    # and in the order of the Options pane, as the comment on the list says
    assert posted == list(MERGED_REPORT_LABELS)


def test_every_vector_store_the_ui_offers_has_its_switch_among_the_required_labels():
    """The UI posts one `enable_<backend>` option per row of RAG_BACKENDS.
    A store added there needs its label in the shipped report - and so in
    MERGED_REPORT_LABELS, or the first check above would never ask for it."""
    import re
    source = REPO_ROOT / "LLM-Prompt-Builder" / "src" / "objects" / "rag-backends.ts"
    if not source.is_file():
        pytest.skip(f"{source} is not in this checkout")
    table = re.search(r"export const RAG_BACKENDS\b.*?=\s*\[(.*?)\n\];", source.read_text(encoding="utf-8"), re.S)
    keys = re.findall(r"\bkey:\s*'([A-Za-z0-9_]+)'", table.group(1)) if table else []
    if not keys:
        pytest.skip("RAG_BACKENDS is not declared the way this test reads it")
    assert [f"enable_{key}" for key in keys if f"enable_{key}" not in MERGED_REPORT_LABELS] == []


def test_the_workflow_starts_on_every_file_a_check_reads():
    """The gate compares the package with files of the repository, and the
    label list with the UI source. A pull request that changes only such a
    file - a rebuilt UI bundle, a RAG step, a logging program, a fact sheet,
    an option added in ddc.ts - makes the package or the list stale, and has
    to start the workflow that runs this suite, or the failure shows up on
    somebody else's change."""
    workflow = REPO_ROOT / ".github" / "workflows" / "verify-model-definitions.yml"
    if not workflow.is_file():
        pytest.skip(f"{workflow} is not in this checkout")
    manifest = load_manifest(REPO_ROOT)
    read_here = ["LLM-Prompt-Builder/src/va/ddc.ts", "LLM-Prompt-Builder/src/objects/rag-backends.ts",
                 manifest.package, "SAS-Viya-Integrations/package-manifest.yaml",
                 "Model-Definition-Builder/cli/tests/test_package_guards.py",
                 "CHANGELOG.md"]                    # the release table in the package is built from it
    read_here += manifest.sources()

    def covered(path: str, patterns: list) -> bool:
        folder = path.rsplit("/", 1)[0]
        return any(pattern == path
                   or (pattern.endswith("/**") and path.startswith(pattern[:-2]))
                   or (pattern == f"{folder}/*.json" and path.endswith(".json"))
                   for pattern in patterns)

    text = workflow.read_text(encoding="utf-8")
    push, _, pull_request = text.partition("pull_request:")
    for trigger, block in (("push", push), ("pull_request", pull_request.split("jobs:", 1)[0])):
        patterns = re.findall(r'^\s+- "([^"]+)"\s*$', block, re.M)
        assert patterns, f"no quoted path filter under {trigger}"
        assert [path for path in read_here if not covered(path, patterns)] == [], trigger
    # and the gate itself is a step of the workflow
    assert re.search(r"^\s+run: mdb package-check\b", text, re.M)


def test_the_workflow_runs_the_lenient_gate_on_a_branch_and_the_strict_one_on_a_tag():
    """Between releases a difference in content is drift and must not turn a
    contributor's pull request red. A release is tagged, and a tag is held to
    the strict gate - with the history, which the commit of release.json is
    looked up in."""
    workflow = REPO_ROOT / ".github" / "workflows" / "verify-model-definitions.yml"
    if not workflow.is_file():
        pytest.skip(f"{workflow} is not in this checkout")
    text = workflow.read_text(encoding="utf-8")
    steps = re.split(r"\n      - (?=name:|uses:)", text.split("    steps:", 1)[1])
    gates = [step for step in steps if re.search(r"^\s+run: mdb package-check\b", step, re.M)]
    assert len(gates) == 2
    lenient, strict = (gates if "--release" in gates[1] else gates[::-1])
    assert "--release" not in re.search(r"run: (mdb package-check.*)", lenient).group(1)
    assert "if: startsWith(github.ref, 'refs/tags/')" not in lenient
    assert re.search(r"run: mdb package-check --release\s*$", strict, re.M)
    assert "if: startsWith(github.ref, 'refs/tags/')" in strict
    # a pushed tag starts the workflow, whatever paths the commit touched
    push = text.split("pull_request:", 1)[0]
    assert re.search(r"^    tags:\n      - '\[0-9\]\+\.\[0-9\]\+\.\[0-9\]\+'$", push, re.M)
    assert re.search(r"fetch-depth: 0", text)
