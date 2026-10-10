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
half runs them over the files in SAS-Viya-Integrations/. The two packages
exported for 2.2.x cannot pass - the report has no RAG labels, and the RAG
package still carries the standalone RAG UI - so for exactly those two
exports, known by their package id, a failing check is an expected failure
that is named in the test report. Every other package fails the suite: a
re-export gets a new id, whatever is wrong with it.

The last part guards the guards: the option labels asked for are the ones the
UI source posts, and the workflow that runs this suite starts on a change to
any file a check here reads.
"""
import base64
import json
import re
from pathlib import Path

import pytest

from mdb.core.options import MERGED_REPORT_LABELS
from mdb.core.packages import (
    LEGACY_RAG_PACKAGE, NEUTRAL_DEFAULTS, PROMPT_BUILDER_PACKAGE, RAG_RUNTIME_PACKAGE,
    RUNTIME_SOURCES, UI_BUNDLE, UI_JOB_DEFINITION, check_package, deflate, form_mismatch,
    legacy_objects, missing_option_labels, non_neutral_defaults, package_objects,
    report_labels, report_option_copies, runtime_content_drift, runtime_ui_objects,
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


#: The package ids of the two exports committed for 2.2.x: the Prompt Builder
#: and the RAG Builder package. An id belongs to one export - the transfer
#: service mints a new one every time, and `mdb package-export` keeps it - so
#: this names two files and nothing else. Remove it with the re-export.
PRE_MERGE_EXPORTS = frozenset({
    "5b466ee8-501d-4cac-b252-4dacac324f8e",
    "237f03e0-864b-46b4-a213-e999bdff231d",
})


def _predates_merge(package: dict) -> bool:
    """One of the two exports made before 2.3.0, by its id. Not by what is
    wrong with it: a report without the RAG labels, or a runtime package with
    the standalone UI inside, is also what a re-export gone wrong looks like,
    and that is what the checks are for."""
    return package.get("id") in PRE_MERGE_EXPORTS


def _settle(package: dict, path: Path, problems: list) -> None:
    """Pass, fail, or - for one of the two exports from before 2.3.0 - an
    expected failure that names what the re-export has to fix."""
    if not problems:
        return
    text = f"{path.name}: " + "; ".join(problems)
    if _predates_merge(package):
        pytest.xfail(f"exported before 2.3.0 - {text}")
    pytest.fail(text)


def test_only_the_two_exports_from_before_the_merge_are_excused():
    """A package is excused for being one of two known files, never for the
    defect it has: the same report under any other id fails."""
    old_report = {k: v for k, v in ALL_NEUTRAL.items() if k in ("viyaHost", "llmProjectID", "enableOptimization")}
    package = _package(_report("Prompt Builder", old_report))
    path = Path(PROMPT_BUILDER_PACKAGE)
    problems = missing_option_labels(package, MERGED_REPORT_LABELS)
    assert problems and "enableRag" in problems[0]
    with pytest.raises(pytest.fail.Exception, match="lacks 25 option label"):
        _settle({**package, "id": "0b0d0f6e-a-re-export-without-the-va-save"}, path, problems)
    with pytest.raises(pytest.fail.Exception):
        _settle(package, path, problems)                        # no id at all
    for known in PRE_MERGE_EXPORTS:
        with pytest.raises(pytest.xfail.Exception, match="exported before 2.3.0"):
            _settle({**package, "id": known}, path, problems)
    _settle({**package, "id": "anything"}, path, [])            # nothing wrong: nothing raised


def test_the_retired_ui_in_a_newly_exported_runtime_package_fails():
    """The RAG folder exported under the new file name with the old report and
    job definition still in it."""
    package = {**_runtime(report=_report("RAG Builder", {"viyaHost": "x"}),
                          ui=_job_definition("RAG-Builder-UI", "<html>")),
               "id": "6c1e2a54-a-new-export"}
    assert legacy_objects(package)
    with pytest.raises(pytest.fail.Exception, match="RAG-Builder-UI"):
        _settle(package, Path(RAG_RUNTIME_PACKAGE), runtime_ui_objects(package))


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


def test_both_builder_packages_are_shipped():
    """The parametrised checks above run over whatever is found; this is what
    notices when one of the two files is not there at all - and when a third
    is: the runtime package under its old name left next to the new one, or
    the RAG content root exported under a name nothing imports."""
    names = [p.name for p in shipped_builder_packages(REPO_ROOT)]
    assert names[:1] == [PROMPT_BUILDER_PACKAGE]
    assert names[1:] in ([RAG_RUNTIME_PACKAGE], [LEGACY_RAG_PACKAGE])
    folder = REPO_ROOT / "SAS-Viya-Integrations"
    assert sorted(p.name for p in folder.glob("SAS-Agentic-AI-Accelerator-*.json")) == sorted(names)
    if (folder / RAG_RUNTIME_PACKAGE).is_file():
        assert not (folder / LEGACY_RAG_PACKAGE).exists()


# ---- package-export: what it refuses, and what it does not write ------------
@pytest.fixture
def exporting(monkeypatch, tmp_path):
    """`mdb package-export` against a fake transfer service. Returns a
    function that runs the command on a given export and gives back (result,
    printed text, the SAS-Viya-Integrations folder of the fake checkout)."""
    import io
    from types import SimpleNamespace

    from rich.console import Console
    from typer.testing import CliRunner

    from mdb import cli
    from mdb.viya import transfer

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    out = io.StringIO()
    exported: list = []
    monkeypatch.setattr(cli, "_viya_session", lambda: _Session())
    monkeypatch.setattr(cli, "Context", lambda: SimpleNamespace(repo=tmp_path))
    monkeypatch.setattr(cli, "console", Console(file=out, width=1000, force_terminal=False))
    job = {"state": "completed", "packageUri": "/transfer/packages/p1",
           "links": [{"rel": "self", "href": "/transfer/exportJobs/j1"}]}
    monkeypatch.setattr(transfer, "folder_by_path", lambda session, path: {"id": "f1"})
    monkeypatch.setattr(transfer, "start_export", lambda session, name, items: exported.append(name) or job)
    monkeypatch.setattr(transfer, "wait_for_job", lambda session, href, timeout=600: job)
    monkeypatch.setattr(transfer, "delete_package", lambda session, uri: None)

    def run(package: dict, *args):
        monkeypatch.setattr(transfer, "download_package", lambda session, uri: json.dumps(package))
        result = CliRunner().invoke(cli.app, ["package-export", *args])
        return result, out.getvalue(), tmp_path / "SAS-Viya-Integrations", exported
    return run


RAG_FOLDER = ("--folder", "/SAS Agentic AI Accelerator/RAG")


def test_export_of_the_two_shipped_packages_ends_clean(exporting):
    result, text, folder, _ = exporting({**_package(_folder("Prompt Builder"), _report("Prompt Builder", ALL_NEUTRAL),
                                                    _job_definition(UI_JOB_DEFINITION, "<html>")), "id": "new-1"})
    assert result.exit_code == 0, text
    assert json.loads((folder / PROMPT_BUILDER_PACKAGE).read_text(encoding="utf-8"))["id"] == "new-1"
    result, text, folder, _ = exporting({**_runtime(), "id": "new-2"}, *RAG_FOLDER, "--name", "RAG Runtime")
    assert result.exit_code == 0, text
    assert (folder / RAG_RUNTIME_PACKAGE).is_file()
    assert text.count("Review the diff, then commit it") == 2 and "must not be committed" not in text


def test_export_of_the_rag_folder_under_its_own_name_is_refused_before_it_starts(exporting, tmp_path):
    """Without --name the RAG content root gives ...-RAG.json: a file
    `mdb builders-import` never reads, next to a runtime package that stays
    as it was. Nothing is exported and nothing written."""
    result, text, folder, exported = exporting(_runtime(), *RAG_FOLDER)
    assert result.exit_code == 2
    assert exported == [] and not folder.exists()
    assert "does not read SAS-Agentic-AI-Accelerator-RAG.json" in text
    assert 'Pass --name "RAG Runtime" for the RAG content root, or --out' in text
    # --out is the way to export any other folder, or under any other name
    elsewhere = tmp_path / "scratch" / "rag.json"
    result, text, _, exported = exporting(_runtime(), *RAG_FOLDER, "--out", str(elsewhere))
    assert result.exit_code == 0, text
    assert exported == ["RAG"] and elsewhere.is_file()


def test_export_with_the_retired_ui_inside_is_not_written(exporting):
    """The RAG folder as a 2.2.x site has it: the standalone report and its
    job definition still in it. The command says what it found, writes
    nothing and does not end with exit code 0."""
    retired = _runtime(report=_report("RAG Builder", {"viyaHost": "x"}),
                       ui=_job_definition("RAG-Builder-UI", "<html>"))
    result, text, folder, _ = exporting(retired, *RAG_FOLDER, "--name", "RAG Runtime")
    assert result.exit_code == 1
    assert not folder.exists()
    assert "The package carries report RAG Builder, jobDefinition RAG-Builder-UI" in text
    assert f"Nothing was written: there is no {RAG_RUNTIME_PACKAGE}." in text
    assert "Review the diff" not in text and "Wrote" not in text
    # left out by name, the same export is a runtime package
    result, text, folder, _ = exporting(retired, *RAG_FOLDER, "--name", "RAG Runtime",
                                        "--exclude", "RAG Builder", "--exclude", "RAG-Builder-UI")
    assert result.exit_code == 0, text
    assert legacy_objects(json.loads((folder / RAG_RUNTIME_PACKAGE).read_text(encoding="utf-8"))) == []


def test_export_of_a_report_the_exporter_did_not_reset_or_save_fails(exporting):
    """Two things only the exporting site can put right, and the export is
    where the exporter is looking: an option default that is the site's own,
    and a report that was not saved with the merged UI."""
    tuned = {**ALL_NEUTRAL, "judgeModel": "gpt_5_mini", "minOptimizeSamples": "10"}
    result, text, folder, _ = exporting(_package(_report("Prompt Builder", tuned)))
    assert result.exit_code == 1 and not folder.exists()
    assert "report 'Prompt Builder' - the report: judgeModel is 'gpt_5_mini', expected ''" in text
    assert "report 'Prompt Builder' - the report: minOptimizeSamples is '10', expected '30'" in text
    assert f"Nothing was written: there is no {PROMPT_BUILDER_PACKAGE}." in text
    unsaved = {k: v for k, v in ALL_NEUTRAL.items() if not k.startswith(("enableRag", "enable_", "rag"))}
    result, text, _, _ = exporting(_package(_report("Prompt Builder", unsaved)))
    assert result.exit_code == 1 and not folder.exists()
    assert "report 'Prompt Builder' - the report lacks 4 option label(s): enableRag, ragComputeContext, " \
           "enable_pgvector, enable_singlestore" in text


#: An export that is fit to ship, and the file `mdb builders-import` reads by
#: default as it sits in a checkout before the export.
def _fit(options: dict, states=()) -> dict:
    return {**_package(_folder("Prompt Builder"), _report("Prompt Builder", options, states),
                       _job_definition(UI_JOB_DEFINITION, "<html>")), "id": "new-1"}


SHIPPED_BEFORE = '{"id": "the package of the last release"}\n'


@pytest.mark.parametrize("label, value", [
    ("sslVerify", "false"), ("sslVerify", "False"), ("sslVerify", "0"), ("sslVerify", ""),
    *[(label, f"{neutral}-of-the-site") for label, neutral in NEUTRAL_DEFAULTS.items() if label != "sslVerify"]])
def test_an_export_with_a_default_of_the_site_leaves_the_shipped_package_alone(exporting, tmp_path, label, value):
    """The target is the file `mdb builders-import` reads when no package is
    named. Written first and judged afterwards, a report that switches TLS
    verification off - or RAG on, or names the site's judge model - is in
    the checkout with exit code 1 behind it, and the next import from that
    checkout brings it to a site without a line about it."""
    shipped = tmp_path / "SAS-Viya-Integrations" / PROMPT_BUILDER_PACKAGE
    shipped.parent.mkdir()
    shipped.write_text(SHIPPED_BEFORE, encoding="utf-8", newline="\n")
    result, text, _, _ = exporting(_fit({**ALL_NEUTRAL, label: value}))
    assert result.exit_code == 1
    assert shipped.read_bytes() == SHIPPED_BEFORE.encode("utf-8")
    assert sorted(p.name for p in shipped.parent.iterdir()) == [PROMPT_BUILDER_PACKAGE]
    # the option, the value, and the copy of the report it was found in
    assert (f"report 'Prompt Builder' - the report: {label} is '{value}', "
            f"expected '{NEUTRAL_DEFAULTS[label]}'") in text
    assert f"Nothing was written: {PROMPT_BUILDER_PACKAGE} is as it was before the export." in text
    assert "Wrote" not in text and "Review the diff" not in text


def test_an_export_to_a_named_file_is_not_written_either(exporting, tmp_path):
    """--out may name the shipped file, or one that is copied over it."""
    elsewhere = tmp_path / "scratch" / "prompt-builder.json"
    result, text, _, _ = exporting(_fit({**ALL_NEUTRAL, "sslVerify": "false"}), "--out", str(elsewhere))
    assert result.exit_code == 1 and not elsewhere.exists() and not elsewhere.parent.exists()
    assert "the report: sslVerify is 'false', expected 'true'" in text
    assert "Nothing was written: there is no prompt-builder.json." in text


def test_a_default_of_the_site_in_a_user_state_goes_with_the_state(exporting):
    """A state is removed from the export before it is judged, so what one
    user saved - verification off, in a view of their own - neither reaches
    the file nor stops the export: nobody but that user can reset it."""
    off = {**ALL_NEUTRAL, "sslVerify": "false", "enableRag": "true"}
    result, text, folder, _ = exporting(_fit(ALL_NEUTRAL, states=[off, off]))
    assert result.exit_code == 0, text
    assert "removed 2 per-user report state(s) from report 'Prompt Builder'" in text
    written = json.loads((folder / PROMPT_BUILDER_PACKAGE).read_text(encoding="utf-8"))
    assert report_option_copies(written) == [ALL_NEUTRAL]
    # and with the report itself off, the states do not hide it
    result, text, folder, _ = exporting(_fit(off, states=[ALL_NEUTRAL]))
    assert result.exit_code == 1
    assert "report 'Prompt Builder' - the report: sslVerify is 'false', expected 'true'" in text
    assert report_option_copies(json.loads((folder / PROMPT_BUILDER_PACKAGE).read_text(encoding="utf-8"))) == \
        [ALL_NEUTRAL]


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


def test_the_workflow_starts_on_every_file_a_check_reads():
    """Each check here compares a package, or the label list, with a file of
    the repository. A pull request that changes only that file - a rebuilt UI
    bundle, a RAG step, a new vector store, an option added in ddc.ts - makes
    the package or the list stale, and has to start the workflow that runs
    this suite, or the failure shows up on somebody else's change."""
    workflow = REPO_ROOT / ".github" / "workflows" / "verify-model-definitions.yml"
    if not workflow.is_file():
        pytest.skip(f"{workflow} is not in this checkout")
    read_here = ["/".join(UI_BUNDLE), "LLM-Prompt-Builder/src/va/ddc.ts",
                 "LLM-Prompt-Builder/src/objects/rag-backends.ts",
                 f"SAS-Viya-Integrations/{PROMPT_BUILDER_PACKAGE}", f"SAS-Viya-Integrations/{RAG_RUNTIME_PACKAGE}",
                 "Model-Definition-Builder/cli/tests/test_package_guards.py",
                 "CHANGELOG.md"]                    # test_releases.py builds the release table from it
    read_here += ["/".join(source["path"]) + "/a file" for source in RUNTIME_SOURCES.values()]

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
