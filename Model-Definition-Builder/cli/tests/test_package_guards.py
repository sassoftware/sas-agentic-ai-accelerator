# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What the two shipped Builder packages must hold.

A package is a second copy of things the repository holds as source - the UI
bundle, rag_core, the jobs, the steps - plus a report whose option labels and
defaults nothing else in the repository describes. Exporting one is a manual
step against a live environment, so every way it can go stale is checked
here instead of by whoever exports next:

  1. the Prompt Builder report holds every option label of the merged UI
     (`mdb options-restore` cannot add one);
  2. its defaults are neutral in the report AND in every per-user state;
  3. the UI job definition serves the committed LLM-Prompt-Builder/dist/index.html;
  4. the RAG runtime package carries no UI, and every file and step in it
     equals the repository's.

The first half tests the checks themselves on fabricated packages. The second
half runs them over the files in SAS-Viya-Integrations/. A package exported
before 2.3.0 cannot pass - it has no RAG labels, and it still carries the
standalone RAG UI - so for such a package a failing check is an expected
failure, named in the test report; once the packages are re-exported the same
checks fail the suite.
"""
import base64
import json
from pathlib import Path

import pytest

from mdb.core.options import MERGED_REPORT_LABELS, RAG_SWITCH
from mdb.core.packages import (
    LEGACY_RAG_PACKAGE, NEUTRAL_DEFAULTS, PROMPT_BUILDER_PACKAGE, RAG_RUNTIME_PACKAGE,
    UI_BUNDLE, UI_JOB_DEFINITION, check_package, deflate, form_mismatch, legacy_objects,
    missing_option_labels, non_neutral_defaults, package_objects, report_labels,
    report_option_copies, runtime_content_drift, runtime_ui_objects,
    shipped_builder_packages,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


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


def _step(name: str, folder: str, body: dict) -> dict:
    return {"transferObject": {
        "summary": {"type": "flowStep", "name": name},
        "contentSourceLocation": folder,
        "content": _b64({"name": name, **body})}}


def _folder(name: str) -> dict:
    return {"transferObject": {"summary": {"type": "folder", "name": name}, "content": _b64({"folder": {}})}}


def _package(*details) -> dict:
    return {"name": "test", "transferObjectCount": len(details), "transferDetails": list(details)}


ALL_NEUTRAL = {label: NEUTRAL_DEFAULTS.get(label, "x") for label in MERGED_REPORT_LABELS}


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
        "storeSslmode": "prefer"}
    assert set(NEUTRAL_DEFAULTS) <= set(MERGED_REPORT_LABELS)


@pytest.mark.parametrize("label,left_behind", [
    ("modelRepositoryID", "042c4094-82b2-4453-a692-28bf311b35b4"),
    ("llmProjectID", "c645a9d3-6f04-4c98-bef8-b86f69e330fc"),
    ("embeddingProjectID", "538b5482-d0b7-4660-9e7c-2cc2e408850e"),
    ("enableRag", "true"),
    ("enableOptimization", "true"),
    ("enableDecision", "false"),
    ("storeSslmode", "disable"),
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


# ---- 3: the UI bundle -------------------------------------------------------
def test_the_form_must_be_the_given_bundle():
    html = "<!DOCTYPE html>\n<html>ü</html>\n"
    assert form_mismatch(_package(_job_definition(UI_JOB_DEFINITION, html)), html) == []
    # line endings are not content: a Windows checkout reads the same file with CRLF
    assert form_mismatch(_package(_job_definition(UI_JOB_DEFINITION, html)), html.replace("\n", "\r\n")) == []
    problems = form_mismatch(_package(_job_definition(UI_JOB_DEFINITION, html + "<!-- old -->")), html)
    assert len(problems) == 1 and "is not the given UI bundle" in problems[0]
    # sizes are bytes, as a file listing shows them: the umlaut counts twice
    assert "(44 bytes)" in problems[0] and "(32 bytes)" in problems[0]


def test_a_missing_job_definition_or_form_is_a_problem():
    assert form_mismatch(_package(_folder("x")), "html") == [
        f"the package holds no job definition named '{UI_JOB_DEFINITION}'"]
    bare = {"transferObject": {"summary": {"type": "jobDefinition", "name": UI_JOB_DEFINITION},
                               "content": _b64({"properties": []})}}
    assert form_mismatch(_package(bare), "html") == [
        f"job definition '{UI_JOB_DEFINITION}' has no 'form' property"]


# ---- 4: the runtime package -------------------------------------------------
@pytest.fixture
def runtime_repo(tmp_path):
    """A repository with a little of each kind of runtime content."""
    files = {
        "SAS-Viya-Integrations/RAG/rag_core/__init__.py": "CORE = 1\n",
        "SAS-Viya-Integrations/RAG/rag_core/adapters/__init__.py": "ADAPTERS = 1\n",
        "SAS-Viya-Integrations/RAG/retrieve_context.py": "def score(): pass\n",
        "SAS-Viya-Integrations/RAG/tests/test_x.py": "# not deployed\n",
        "SAS-Viya-Integrations/RAG-Ingestion/Ingest-Documents.sas": "%put ingest;\r\n",
        "SAS-Viya-Integrations/Custom-Steps/RAG - Embed Chunks.step": json.dumps(
            {"name": "RAG - Embed Chunks.step", "templates": {"SAS": "%put embed;\n"}, "ui": "{\n}"}),
        "SAS-Viya-Integrations/Custom-Steps/LLM - Get Options.step": json.dumps(
            {"name": "LLM - Get Options.step", "templates": {"SAS": ""}, "ui": "{}"}),
    }
    for relative, text in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    return tmp_path


ROOT = "/SAS Agentic AI Accelerator/RAG"


def _runtime(**changes) -> dict:
    parts = {
        "core": _file("__init__.py", f"{ROOT}/rag_core", "CORE = 1\n"),
        "adapters": _file("__init__.py", f"{ROOT}/rag_core/adapters", "ADAPTERS = 1\n"),
        "model": _file("retrieve_context.py", f"{ROOT}/models", "def score(): pass\n"),
        "job": _file("Ingest-Documents.sas", f"{ROOT}/jobs", "%put ingest;\n"),
        "step": _step("RAG - Embed Chunks.step", f"{ROOT}/steps",
                      {"templates": {"SAS": "%put embed;\n"}, "ui": "{\n}", "localUi": "ignored"}),
    }
    parts.update(changes)
    return _package(_folder("RAG"), _folder("rag_core"), *[p for p in parts.values() if p is not None])


def test_a_runtime_package_equal_to_the_repository_passes(runtime_repo):
    """Two files called __init__.py are told apart by where they were
    exported from, a CRLF checkout is not a difference, and neither the
    tests folder nor another product's step is expected in the package."""
    package = _runtime()
    assert runtime_content_drift(package, runtime_repo) == []
    assert runtime_ui_objects(package) == []
    assert legacy_objects(package) == []


def test_a_runtime_package_under_another_content_root_is_located_all_the_same(runtime_repo):
    moved = json.loads(json.dumps(_runtime()).replace(ROOT, "/Projects/Team A/Retrieval"))
    assert runtime_content_drift(moved, runtime_repo) == []


def test_a_file_behind_the_repository_is_named(runtime_repo):
    package = _runtime(adapters=_file("__init__.py", f"{ROOT}/rag_core/adapters", "ADAPTERS = 0\n"))
    assert runtime_content_drift(package, runtime_repo) == [
        "file 'rag_core/adapters/__init__.py' differs from the repository (13 vs 13 characters)"]


def test_a_step_whose_code_or_ui_differs_is_named(runtime_repo):
    package = _runtime(step=_step("RAG - Embed Chunks.step", f"{ROOT}/steps",
                                  {"templates": {"SAS": "%put old;\n"}, "ui": "{ }"}))
    assert runtime_content_drift(package, runtime_repo) == [
        "step 'steps/RAG - Embed Chunks.step': 'templates' differs from the repository .step",
        "step 'steps/RAG - Embed Chunks.step': 'ui' differs from the repository .step"]


def test_what_is_missing_and_what_rides_along_are_both_named(runtime_repo):
    package = _runtime(model=None,
                       extra=_file("pipeline.yaml", f"{ROOT}/generated/demo", "steps: []\n"),
                       stray=_file("notes.py", f"{ROOT}/rag_core", "x = 1\n"))
    problems = runtime_content_drift(package, runtime_repo)
    assert len(problems) == 3
    assert problems[0].startswith("file 'pipeline.yaml' is in the package but is not runtime content")
    assert "generated/demo" in problems[0]
    assert problems[1].startswith("file 'rag_core/notes.py' is in the package but is not runtime content")
    assert problems[2] == "'models/retrieve_context.py' is in the repository but not in the package"


def test_a_ui_in_the_runtime_package_is_named():
    package = _runtime(report=_report("RAG Builder", {"viyaHost": "x"}),
                       ui=_job_definition("RAG-Builder-UI", "<html>"),
                       other=_job_definition("Scratch", "<html>"))
    assert runtime_ui_objects(package) == [
        "the runtime package carries report 'RAG Builder'",
        "the runtime package carries jobDefinition 'RAG-Builder-UI'",
        "the runtime package carries jobDefinition 'Scratch'"]
    # of those, the two that say "exported before 2.3.0"
    assert legacy_objects(package) == ["report RAG Builder", "jobDefinition RAG-Builder-UI"]


# ---- which packages are shipped ---------------------------------------------
def test_objects_and_report_labels_are_listed():
    package = _package(_folder("Prompt Builder"), _report("Prompt Builder", {"viyaHost": "x", "enableRag": "false"}),
                       _file("a.py", ROOT, ""), _job_definition("UI", ""))
    assert package_objects(package) == [("folder", "Prompt Builder"), ("report", "Prompt Builder"),
                                        ("file", "a.py"), ("jobDefinition", "UI")]
    assert report_labels(package) == {"Prompt Builder": {"viyaHost", "enableRag"}}
    assert report_labels(_package(_folder("x"))) == {}


def test_the_default_packages_are_an_ordered_list_not_a_pattern(tmp_path):
    """The Prompt Builder first, then the runtime - under its new file name,
    or under the one it had while it carried a UI when that is what the
    checkout holds. Nothing else in the folder is picked up."""
    folder = tmp_path / "SAS-Viya-Integrations"
    folder.mkdir()
    assert shipped_builder_packages(tmp_path) == []
    for name in (LEGACY_RAG_PACKAGE, PROMPT_BUILDER_PACKAGE, "SAS Agentic AI Accelerator.json",
                 "SAS-Agentic-AI-Accelerator-Some-Other-Builder.json"):
        (folder / name).write_text("{}", encoding="utf-8")
    assert [p.name for p in shipped_builder_packages(tmp_path)] == [PROMPT_BUILDER_PACKAGE, LEGACY_RAG_PACKAGE]
    (folder / RAG_RUNTIME_PACKAGE).write_text("{}", encoding="utf-8")
    assert [p.name for p in shipped_builder_packages(tmp_path)] == [PROMPT_BUILDER_PACKAGE, RAG_RUNTIME_PACKAGE]
    (folder / PROMPT_BUILDER_PACKAGE).unlink()
    assert [p.name for p in shipped_builder_packages(tmp_path)] == [RAG_RUNTIME_PACKAGE]
    assert (PROMPT_BUILDER_PACKAGE, RAG_RUNTIME_PACKAGE, LEGACY_RAG_PACKAGE) == (
        "SAS-Agentic-AI-Accelerator-Prompt-Builder.json",
        "SAS-Agentic-AI-Accelerator-RAG-Runtime.json",
        "SAS-Agentic-AI-Accelerator-RAG-Builder.json")


# ---- the packages this repository actually ships ----------------------------
def _shipped(name_filter) -> list:
    return [p for p in shipped_builder_packages(REPO_ROOT) if name_filter(p.name)]


PROMPT_PACKAGES = _shipped(lambda name: name == PROMPT_BUILDER_PACKAGE)
RUNTIME_PACKAGES = _shipped(lambda name: name != PROMPT_BUILDER_PACKAGE)


@pytest.fixture(scope="module")
def loaded():
    cache: dict = {}

    def load(path: Path) -> dict:
        if path not in cache:
            cache[path] = json.loads(path.read_text(encoding="utf-8"))
        return cache[path]
    return load


def _predates_merge(package: dict) -> bool:
    """Exported before 2.3.0: the report has no RAG switch (the Prompt Builder
    package), or the standalone RAG UI is still inside (the RAG package)."""
    labels = report_labels(package).get("Prompt Builder")
    return bool(legacy_objects(package)) or (labels is not None and RAG_SWITCH not in labels)


def _settle(package: dict, path: Path, problems: list) -> None:
    """Pass, fail, or - for a package from before 2.3.0 - an expected failure
    that names what the re-export has to fix."""
    if not problems:
        return
    text = f"{path.name}: " + "; ".join(problems)
    if _predates_merge(package):
        pytest.xfail(f"exported before 2.3.0 - {text}")
    pytest.fail(text)


@pytest.mark.parametrize("path", PROMPT_PACKAGES, ids=lambda p: p.name)
def test_shipped_prompt_builder_report_holds_every_option(path, loaded):
    package = loaded(path)
    _settle(package, path, missing_option_labels(package, MERGED_REPORT_LABELS))


@pytest.mark.parametrize("path", PROMPT_PACKAGES, ids=lambda p: p.name)
def test_shipped_prompt_builder_report_has_neutral_defaults(path, loaded):
    package = loaded(path)
    _settle(package, path, non_neutral_defaults(package))


@pytest.mark.parametrize("path", PROMPT_PACKAGES, ids=lambda p: p.name)
def test_shipped_prompt_builder_report_has_no_user_states(path, loaded):
    package = loaded(path)
    states = check_package(path).states
    _settle(package, path, [f"report '{s.report}' carries {s.states} per-user report state(s)" for s in states])


@pytest.mark.parametrize("path", PROMPT_PACKAGES, ids=lambda p: p.name)
def test_shipped_prompt_builder_ui_is_the_committed_bundle(path, loaded):
    package = loaded(path)
    bundle = REPO_ROOT.joinpath(*UI_BUNDLE)
    if not bundle.is_file():
        pytest.skip(f"{bundle} is not built in this checkout")
    # newline="" keeps the file's own line endings; the comparison ignores them
    with bundle.open(encoding="utf-8", newline="") as handle:
        html = handle.read()
    _settle(package, path, form_mismatch(package, html))


@pytest.mark.parametrize("path", RUNTIME_PACKAGES, ids=lambda p: p.name)
def test_shipped_runtime_package_carries_no_ui(path, loaded):
    package = loaded(path)
    _settle(package, path, runtime_ui_objects(package))


@pytest.mark.parametrize("path", RUNTIME_PACKAGES, ids=lambda p: p.name)
def test_shipped_runtime_package_equals_the_repository(path, loaded):
    package = loaded(path)
    _settle(package, path, runtime_content_drift(package, REPO_ROOT))


def test_both_builder_packages_are_shipped():
    """The parametrised checks above run over whatever is found; this is what
    notices when one of the two files is not there at all."""
    names = [p.name for p in shipped_builder_packages(REPO_ROOT)]
    assert names[:1] == [PROMPT_BUILDER_PACKAGE]
    assert names[1:] in ([RAG_RUNTIME_PACKAGE], [LEGACY_RAG_PACKAGE])
