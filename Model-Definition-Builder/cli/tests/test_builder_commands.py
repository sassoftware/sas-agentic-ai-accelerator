# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""options-save, options-restore and builders-import, with SAS Viya faked.

These commands are what stands between a site's configuration and an import
that overwrites it, and the rules they follow live in two places: the pure
fold in core/options.py (tested in test_report_options.py) and the order in
which the commands read, fold, filter and write - which is here. Each test
is one way an upgrade from 2.2.x could lose something:

  * the save not reading the retired "RAG Builder" report any more;
  * the restore writing a 2.2.x file's RAG section into the retired report
    instead of the Prompt Builder;
  * one Model Manager project that cannot be discovered costing the other;
  * an import without an options file putting the package's defaults over
    what the site had, or a failed run naming a way on that reads the
    options again from a report the import already replaced;
  * a dry run burying its answer under a line per option the new report adds.
"""
import io
import json
import shlex
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import SimpleNamespace

import pytest
from rich.console import Console
from typer.testing import CliRunner

from content_fakes import STEP, STEP_NAME, Package, Viya, as_registered
from mdb import cli
from mdb.core.options import read_options
from mdb.viya import registry, reports, transfer


def _xml(options: dict) -> str:
    return "<SASReport>" + "".join(
        f'<PromptDefinition label="{label}" hidden="true" isParameter="true" dataType="string" name="pr{i}">'
        f'<DefaultValue><String behavior="fixed">{value}</String></DefaultValue></PromptDefinition>'
        for i, (label, value) in enumerate(options.items())) + "</SASReport>"


# The report a 2.2.x site has, and the one the 2.3.0 package brings.
OLD_PROMPT = {"viyaHost": "https://site.example", "modelRepositoryID": "repo-live", "llmProjectID": "llm-live",
              "SCREndpoint": "https://site.example/llm", "deploymentType": "k8s",
              "enableOptimization": "true", "computeContext": "SAS Job Execution compute context",
              "credentialDomain": "agentic-ai-keys", "judgeModel": ""}
OLD_RAG = {"viyaHost": "https://site.example", "modelRepositoryID": "repo-live",
           "SCREndpoint": "https://site.example/llm", "deploymentType": "k8s",
           "credentialDomain": "agentic-ai-keys", "contentRoot": "/Team/RAG", "casServer": "cas-shared-default",
           "computeContext": "SAS Studio compute context", "storeSslmode": "disable",
           "enabledBackends": "", "enable_pgvector": "1", "embeddingProjectID": "emb-live"}
NEW_DEFAULTS = {"viyaHost": "https://your-sas-viya-host", "modelRepositoryID": "", "llmProjectID": "",
                "SCREndpoint": "https://your-sas-viya-host/llm", "deploymentType": "k8s",
                "enableOptimization": "false", "computeContext": "SAS Job Execution compute context",
                "credentialDomain": "agentic-ai-keys", "judgeModel": "",
                "enableDecision": "true", "enableRag": "false", "embeddingProjectID": "",
                "contentRoot": "/SAS Agentic AI Accelerator/RAG", "casServer": "cas-shared-default",
                "ragComputeContext": "SAS Studio compute context", "storeSslmode": "prefer",
                "enable_pgvector": "1", "sslVerify": "true"}


class Site:
    """The reports of a fake site, and everything that was done to them."""

    def __init__(self, **named):
        self.reports = {name: _xml(options) for name, options in named.items()}
        self.looked_up: list = []
        self.read: list = []
        self.written: list = []
        #: True for a site whose report cannot be written (someone has it open)
        self.refuses = False
        #: Reports that are on the site and whose content cannot be read, and
        #: reports the service does not answer a lookup for.
        self.unreadable: set = set()
        self.unanswered: set = set()

    def options(self, name: str) -> dict:
        return read_options(self.reports[name])


@pytest.fixture
def out(monkeypatch):
    """Everything the command prints, unwrapped."""
    buffer = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=buffer, width=1000, force_terminal=False))
    return buffer


@pytest.fixture
def site(monkeypatch):
    def install(fake: Site) -> Site:
        def find_report(session, name, strict=False):
            fake.looked_up.append(name)
            if strict and name in fake.unanswered:
                raise RuntimeError(f"Looking up the report '{name}' failed: HTTP 503")
            return {"id": name, "name": name} if name in fake.reports else None

        def get_content(session, report_id):
            if report_id in fake.unreadable:
                raise RuntimeError("Reading the report content failed: HTTP 503")
            fake.read.append(report_id)
            return fake.reports[report_id], '"etag"'

        def put_content(session, report_id, content, etag=None):
            if fake.refuses:
                raise RuntimeError("HTTP 412")
            fake.written.append(report_id)
            fake.reports[report_id] = content

        monkeypatch.setattr(reports, "find_report", find_report)
        monkeypatch.setattr(reports, "get_content", get_content)
        monkeypatch.setattr(reports, "put_content", put_content)
        return fake
    return install


@pytest.fixture
def discovery(monkeypatch):
    """Model Manager as discovery sees it: {kind: project id}, or an exception
    for a kind that cannot be discovered."""
    monkeypatch.setenv("SAS_SCR_ENDPOINT", "https://discovered.example/llm")
    monkeypatch.setenv("SAS_DEPLOYMENT_TYPE", "k8s")

    def install(projects: dict) -> None:
        def ensure(session, kind, core, responsible_party):
            if isinstance(projects[kind], Exception):
                raise projects[kind]
            return SimpleNamespace(created=[], repository_id="repo-disc", repository_folder_id="folder-1",
                                   project_id=projects[kind])
        monkeypatch.setattr(registry, "ensure_repository_and_project", ensure)
    return install


CTX = SimpleNamespace(core=None)


# ---- discovery ---------------------------------------------------------------
def test_discovery_fills_both_projects_of_the_one_report(discovery, out):
    discovery({"llm": "llm-disc", "embedding": "emb-disc"})
    assert cli._discovered_options(None, CTX) == {"Prompt Builder": {
        "modelRepositoryID": "repo-disc", "llmProjectID": "llm-disc", "embeddingProjectID": "emb-disc",
        "SCREndpoint": "https://discovered.example/llm", "deploymentType": "k8s"}}
    assert out.getvalue() == ""


def test_a_failing_embedding_project_does_not_cost_the_llm_project(discovery, out):
    discovery({"llm": "llm-disc", "embedding": RuntimeError("no embedding project template")})
    values = cli._discovered_options(None, CTX)
    assert values == {"Prompt Builder": {
        "modelRepositoryID": "repo-disc", "llmProjectID": "llm-disc",
        "SCREndpoint": "https://discovered.example/llm", "deploymentType": "k8s"}}
    # no empty embeddingProjectID that a restore would write over a live value
    assert "embeddingProjectID" not in values["Prompt Builder"]
    assert "Prompt Builder: could not discover the embedding project (no embedding project template)" \
        in out.getvalue()


def test_nothing_discovered_is_no_seed_at_all(discovery, out):
    discovery({"llm": RuntimeError("down"), "embedding": RuntimeError("down")})
    assert cli._discovered_options(None, CTX) == {}


# ---- options-save ------------------------------------------------------------
def test_save_reads_the_retired_report_and_folds_it_in(site, discovery, out):
    """The first step of the documented upgrade, run with the new CLI on a
    2.2.x site: both reports exist, and nothing of the RAG one may be lost."""
    discovery({"llm": "llm-disc", "embedding": "emb-disc"})
    fake = site(Site(**{"Prompt Builder": OLD_PROMPT, "RAG Builder": OLD_RAG}))
    captured = cli._capture_options(None, cli._discovered_seeds(None, CTX))
    assert list(captured) == ["Prompt Builder"]
    saved = captured["Prompt Builder"]
    assert fake.read == ["Prompt Builder", "RAG Builder"] and fake.written == []
    # the live reports beat discovery - also the retired one's project id
    assert saved["llmProjectID"] == "llm-live" and saved["embeddingProjectID"] == "emb-live"
    assert saved["modelRepositoryID"] == "repo-live"
    # the RAG settings, the rename, the switch
    assert saved["contentRoot"] == "/Team/RAG" and saved["storeSslmode"] == "disable"
    assert saved["ragComputeContext"] == "SAS Studio compute context"
    assert saved["computeContext"] == "SAS Job Execution compute context"
    assert saved["enableRag"] == "true"
    # empty and dead options are not saved
    assert "enabledBackends" not in saved and "judgeModel" not in saved
    text = out.getvalue()
    assert "'RAG Builder': read from the retired report of that name and folded into the Prompt Builder section." in text
    assert "computeContext -> ragComputeContext" in text
    # 8 the Prompt Builder report holds, 6 carried from the retired one, the switch
    assert len(saved) == 15 and "Prompt Builder: 15 options (report + discovery)." in text
    # nothing about deleting: the report must still be there until the import is done
    assert "delete" not in text.lower()


def test_a_later_save_does_not_let_the_retired_report_overwrite_tuned_values(site, out):
    """After the upgrade, while the retired report has not been deleted yet."""
    tuned = {**NEW_DEFAULTS, "enableRag": "false", "storeSslmode": "require", "embeddingProjectID": "emb-new",
             "contentRoot": "/Team/RAG-2"}
    site(Site(**{"Prompt Builder": tuned, "RAG Builder": OLD_RAG}))
    saved = cli._capture_options(None, {})["Prompt Builder"]
    assert saved["storeSslmode"] == "require" and saved["contentRoot"] == "/Team/RAG-2"
    assert saved["embeddingProjectID"] == "emb-new" and saved["enableRag"] == "false"
    assert "enableRag is 'false' in Prompt Builder and is kept" in out.getvalue()


def test_save_on_a_site_without_the_retired_report_is_what_it_was(site, discovery, out):
    discovery({"llm": "llm-disc", "embedding": "emb-disc"})
    fake = site(Site(**{"Prompt Builder": OLD_PROMPT}))
    saved = cli._capture_options(None, cli._discovered_seeds(None, CTX))["Prompt Builder"]
    assert fake.looked_up == ["Prompt Builder", "RAG Builder"] and fake.read == ["Prompt Builder"]
    # discovery supplies the embedding project the report has no value for
    assert saved["embeddingProjectID"] == "emb-disc" and saved["llmProjectID"] == "llm-live"
    assert "enableRag" not in saved and "retired" not in out.getvalue()


def test_save_carries_tls_verification_only_from_a_report_that_holds_it(site, discovery, out):
    """sslVerify is an option of the report and nothing else: discovery does
    not know it, a 2.2.x report does not hold it, and the save invents no
    value for it. A site that switched verification off is saved as that."""
    discovery({"llm": "llm-disc", "embedding": "emb-disc"})
    seeds = cli._discovered_seeds(None, CTX)
    assert "sslVerify" not in seeds["Prompt Builder"]
    site(Site(**{"Prompt Builder": OLD_PROMPT, "RAG Builder": OLD_RAG}))
    assert "sslVerify" not in cli._capture_options(None, seeds)["Prompt Builder"]
    site(Site(**{"Prompt Builder": {**NEW_DEFAULTS, "sslVerify": "false"}, "RAG Builder": OLD_RAG}))
    assert cli._capture_options(None, seeds)["Prompt Builder"]["sslVerify"] == "false"
    site(Site(**{"Prompt Builder": NEW_DEFAULTS}))
    assert cli._capture_options(None, seeds)["Prompt Builder"]["sslVerify"] == "true"
    assert "sslVerify" not in out.getvalue()


def test_save_with_no_report_and_no_discovery_captures_nothing(site, out):
    site(Site())
    assert cli._capture_options(None, {}) == {}
    assert "Prompt Builder: no live report and nothing discovered - skipped." in out.getvalue()


# ---- options-restore ---------------------------------------------------------
SAVED_2_2 = {"Prompt Builder": {k: v for k, v in OLD_PROMPT.items() if v},
             "RAG Builder": {k: v for k, v in OLD_RAG.items() if v}}


def test_restore_of_a_2_2_file_writes_the_merged_report_only(site, out):
    """After the import: the Prompt Builder report is the new one, the retired
    report is still there. Everything goes into the former; the latter is
    looked up to say it is retired, and neither read for options nor written."""
    fake = site(Site(**{"Prompt Builder": NEW_DEFAULTS, "RAG Builder": OLD_RAG}))
    failures = cli._restore_options(None, json.loads(json.dumps(SAVED_2_2)), False, "site.example")
    assert failures == 0
    assert fake.read == ["Prompt Builder"] and fake.written == ["Prompt Builder"]
    restored = fake.options("Prompt Builder")
    assert restored["llmProjectID"] == "llm-live" and restored["embeddingProjectID"] == "emb-live"
    assert restored["contentRoot"] == "/Team/RAG" and restored["storeSslmode"] == "disable"
    assert restored["ragComputeContext"] == "SAS Studio compute context"
    assert restored["computeContext"] == "SAS Job Execution compute context"
    assert restored["enableRag"] == "true" and restored["enableOptimization"] == "true"
    # what the file does not name keeps the package default - a 2.2.x file
    # knows nothing of TLS verification, so that stays on
    assert restored["enableDecision"] == "true" and restored["sslVerify"] == "true"
    assert fake.options("RAG Builder") == OLD_RAG
    text = out.getvalue()
    assert "A report named 'RAG Builder' still exists on this site. It is retired" in text
    assert "delete the report 'RAG Builder' and the job definition 'RAG-Builder-UI'" in text
    assert "Nothing was deleted." in text
    assert "no such report here" not in text and "not an option of this report version" not in text


def test_restore_says_nothing_about_a_retired_report_that_is_gone(site, out):
    fake = site(Site(**{"Prompt Builder": NEW_DEFAULTS}))
    assert cli._restore_options(None, json.loads(json.dumps(SAVED_2_2)), False) == 0
    assert fake.written == ["Prompt Builder"] and "retired" not in out.getvalue()
    # a second restore finds everything in place
    assert cli._restore_options(None, json.loads(json.dumps(SAVED_2_2)), False) == 0
    assert fake.written == ["Prompt Builder"]
    assert "Prompt Builder: already matches" in out.getvalue()


def test_restore_into_a_report_from_before_the_merge_names_what_it_cannot_write(site, out):
    """options-restore run before the import: the 2.2.x report has none of the
    RAG labels, and each one is worth a line - outside a dry run of an import,
    a missing label is the signal."""
    fake = site(Site(**{"Prompt Builder": OLD_PROMPT}))
    # ... and the report counts as not written: RAG is off on the site and no
    # RAG setting reached it, which must not look like a restore that worked
    assert cli._restore_options(None, json.loads(json.dumps(SAVED_2_2)), False) == 1
    text = out.getvalue()
    for label in ("contentRoot", "ragComputeContext", "enableRag", "embeddingProjectID"):
        assert f"Prompt Builder: '{label}' is not an option of this report version - not written." in text
    assert "Prompt Builder: 7 option(s) the Prompt Builder reads cannot be written" in text
    assert "the report on this site is from before 2.3.0, or was not saved with the merged UI" in text
    assert fake.options("Prompt Builder")["computeContext"] == "SAS Job Execution compute context"


def test_tls_verification_is_restored_as_the_file_says(site, out):
    """A site that switched verification off gets that back after an import,
    whichever way the file spells it; a file that says on leaves it on."""
    fake = site(Site(**{"Prompt Builder": NEW_DEFAULTS}))
    for given in (False, "false"):
        fake.reports["Prompt Builder"] = _xml(NEW_DEFAULTS)
        assert cli._restore_options(None, {"Prompt Builder": {"sslVerify": given}}, False) == 0
        assert fake.options("Prompt Builder")["sslVerify"] == "false", given
    assert "Prompt Builder: restored 1 option(s): sslVerify" in out.getvalue()
    for given in (True, "true"):
        fake.reports["Prompt Builder"] = _xml(NEW_DEFAULTS)
        assert cli._restore_options(None, {"Prompt Builder": {"sslVerify": given}}, False) == 0
        assert fake.options("Prompt Builder")["sslVerify"] == "true", given


def test_tls_verification_that_cannot_be_written_is_a_failure(site, out):
    """A report saved before the option existed cannot hold it. The file said
    verification is off for this site, the report will manifest with it on:
    that is an option of this release that was not written."""
    older = {label: value for label, value in NEW_DEFAULTS.items() if label != "sslVerify"}
    fake = site(Site(**{"Prompt Builder": older}))
    assert cli._restore_options(None, {"Prompt Builder": {"sslVerify": "false", "llmProjectID": "llm-live"}},
                                False) == 1
    text = out.getvalue()
    assert "Prompt Builder: 'sslVerify' is not an option of this report version - not written." in text
    assert "Prompt Builder: 1 option(s) the Prompt Builder reads cannot be written (sslVerify)" in text
    assert fake.options("Prompt Builder")["llmProjectID"] == "llm-live"


def test_a_label_no_report_version_holds_is_named_and_is_no_failure(site, out):
    """A 2.2.x file names options that are gone for good (the key table of the
    optimization job). The report is not behind for lacking them, and what
    the report does hold is written."""
    fake = site(Site(**{"Prompt Builder": NEW_DEFAULTS}))
    saved = {"Prompt Builder": {"llmProjectID": "llm-live", "optimizeKeyTable": "LLM_API_KEYS"}}
    assert cli._restore_options(None, saved, False) == 0
    text = out.getvalue()
    assert "'optimizeKeyTable' is not an option of this report version - not written." in text
    assert "cannot be written" not in text
    assert fake.options("Prompt Builder")["llmProjectID"] == "llm-live"


def test_a_value_a_checkbox_cannot_take_is_named_and_counts(site, out):
    """A report that types a switch as a boolean takes on or off and nothing
    else. A value it was not given must not pass for a restore that worked."""
    fake = site(Site(**{"Prompt Builder": NEW_DEFAULTS}))
    fake.reports["Prompt Builder"] = fake.reports["Prompt Builder"].replace(
        '<PromptDefinition label="enableRag" hidden="true" isParameter="true" dataType="string"',
        '<PromptDefinition label="enableRag" hidden="true" isParameter="true" dataType="boolean"')
    saved = {"Prompt Builder": {"enableRag": "maybe", "llmProjectID": "llm-live"}}
    assert cli._restore_options(None, saved, False) == 1
    assert "Prompt Builder: 'enableRag' is a checkbox in this report and 'maybe' says neither on nor off" \
        in out.getvalue()
    restored = fake.options("Prompt Builder")
    assert restored["enableRag"] == "false" and restored["llmProjectID"] == "llm-live"


def test_every_rejected_value_gets_a_line_and_the_report_does_not_match(site, out):
    """One line per label, each with the value it was given - and when
    nothing else was left to write, no "already matches" under them."""
    fake = site(Site(**{"Prompt Builder": NEW_DEFAULTS}))
    for label in ("enableRag", "sslVerify"):
        fake.reports["Prompt Builder"] = fake.reports["Prompt Builder"].replace(
            f'<PromptDefinition label="{label}" hidden="true" isParameter="true" dataType="string"',
            f'<PromptDefinition label="{label}" hidden="true" isParameter="true" dataType="boolean"')
    before = fake.reports["Prompt Builder"]
    saved = {"Prompt Builder": {"enableRag": "maybe", "sslVerify": ""}}
    for dry_run in (True, False):
        out.seek(0)
        out.truncate()
        assert cli._restore_options(None, saved, dry_run) == 1
        text = out.getvalue()
        assert text.count("is a checkbox in this report and") == 2
        assert "'enableRag' is a checkbox in this report and 'maybe' says neither on nor off - not written." in text
        assert "'sslVerify' is a checkbox in this report and '' says neither on nor off - not written." in text
        assert "already matches" not in text
    # nothing was written: TLS verification is still on
    assert fake.written == [] and fake.reports["Prompt Builder"] == before


def test_a_report_behind_the_file_still_gets_what_it_can_hold(site, out):
    fake = site(Site(**{"Prompt Builder": {**OLD_PROMPT, "llmProjectID": ""}}))
    assert cli._restore_options(None, json.loads(json.dumps(SAVED_2_2)), False) == 1
    assert fake.written == ["Prompt Builder"]
    assert fake.options("Prompt Builder")["llmProjectID"] == "llm-live"


def test_a_dry_run_before_an_import_sums_up_what_arrives_with_it(site, out):
    """builders-import --dry-run on a 2.2.x site: the live report lacks every
    option the package's report adds. That is one fact, not fifteen."""
    fake = site(Site(**{"Prompt Builder": OLD_PROMPT}))
    arriving = {"Prompt Builder": set(NEW_DEFAULTS)}
    saved = json.loads(json.dumps(SAVED_2_2))
    saved["Prompt Builder"]["optimizeKeyTable"] = "LLM_API_KEYS"     # gone from the new report too
    assert cli._restore_options(None, saved, True, "site.example", arriving) == 0
    assert fake.written == []
    text = out.getvalue()
    assert text.count("arrive with the imported report and are written then") == 1
    summary = next(line for line in text.splitlines() if "arrive with the imported report" in line)
    assert "Prompt Builder: 7 option(s)" in summary
    for label in ("contentRoot", "casServer", "ragComputeContext", "storeSslmode", "enable_pgvector",
                  "embeddingProjectID", "enableRag"):
        assert label in summary
    # only what the package's report lacks as well is still reported by name
    assert text.count("is not an option of this report version") == 1
    assert "'optimizeKeyTable' is not an option of this report version" in text


def test_a_dry_run_on_a_first_install_says_the_report_arrives(site, out):
    site(Site())
    cli._restore_options(None, {"Prompt Builder": {"llmProjectID": "x", "SCREndpoint": "y"}}, True, "h",
                         {"Prompt Builder": {"llmProjectID", "SCREndpoint"}})
    assert ("Prompt Builder: not on this site yet - it arrives with the import, and the 2 saved option(s) "
            "are written then.") in out.getvalue()


# ---- builders-import ---------------------------------------------------------
PACKAGE_FILE = "SAS-Agentic-AI-Accelerator.json"
CONTENT_ROOT = "/SAS Agentic AI Accelerator"
UI_PATH = "Prompt Builder/SAS-LLM-Prompt-Builder-UI"
STEP_PATH = f"RAG/steps/{STEP_NAME}"
FILE_PATH = "RAG/rag_core/__init__.py"
MANIFEST = f"""version: 1
contentRoot: {CONTENT_ROOT}
package: SAS-Viya-Integrations/SAS-Agentic-AI-Accelerator.json
entries:
  - {{path: Prompt Builder/Prompt Builder, kind: report, checks: {{optionSet: prompt-builder}}}}
  - {{path: {FILE_PATH}, kind: file, source: not/in/this/checkout.py}}
  - path: {UI_PATH}
    kind: jobDefinition
    form: not/in/this/checkout.html
    parameters:
      - {{name: _contextName, default: SAS Studio compute context, siteSetting: true}}
      - {{name: _action, default: "prompts,form,execute"}}
"""

#: The job definition of the page as a package carries it.
UI_JOB = {"type": "Compute", "code": "/* SAS templated code goes here */",
          "createdBy": "the exporter", "links": [{"rel": "self", "href": "/jobDefinitions/definitions/exported"}],
          "parameters": [
              {"version": 1, "name": "_contextName", "defaultValue": "SAS Studio compute context",
               "type": "character", "required": False},
              {"version": 1, "name": "_action", "defaultValue": "prompts,form,execute", "type": "character",
               "required": False}],
          "properties": [{"name": "form", "value": "<html>the page of the release</html>"}]}


def _built(report: bool = True, **kwargs) -> Package:
    """The package of a release, small: the page, a custom step, a file of
    the RAG runtime and - unless it is left out - the Prompt Builder report
    as the package brings it."""
    built = Package(**kwargs)
    built.job(UI_PATH, UI_JOB)
    built.step(STEP_PATH, {**as_registered(STEP), "id": "exported-step", "createdBy": "the exporter"})
    built.file(FILE_PATH, b"CORE = 2\n")
    if report:
        built.report("Prompt Builder/Prompt Builder", NEW_DEFAULTS)
    return built


def _retired_package() -> dict:
    """A package of 2.2.x that somebody names: the standalone RAG UI."""
    built = Package()
    built.job("RAG/RAG-Builder-UI", UI_JOB)
    built.report("RAG/RAG Builder", OLD_RAG, tables=())
    return {**built.build(), "name": "RAG Builder"}


def _other(kind: str, name: str) -> dict:
    return {"transferObject": {"summary": {"type": kind, "name": name}, "content": ""}}


def _mapping() -> dict:
    """The import mapping the transfer service gives for the package: the
    tables its two reports bind, and the Data-Driven Content URL."""
    def table(name):
        return {"source": f"server=cas-shared-default;library=Public;table={name}",
                "target": f"server=cas-shared-default;library=Public;table={name}"}
    return {"version": 1,
            "connectors": {"table": [table("ACCELERATOR_RELEASES"), table("LLM_LOGS"), table("LLM_FACT_SHEET")]},
            "substitutions": [{"resourceName": "Prompt Builder", "properties": [
                {"name": "VisualElement:ve9", "value": "https://your-sas-viya-host/SASJobExecution/"}]}]}


@pytest.fixture
def importing(monkeypatch, tmp_path, site, discovery, out):
    """`mdb builders-import` against a fake transfer service and a SAS Viya
    that lives in memory. Returns a function that writes the package into a
    fake checkout, runs the command there and gives back (result, site,
    printed text). On `run`: `viya`, the system the files, steps and job
    definitions are brought into in place; `imports`, the mapping each import
    through the transfer service was started with, by package name;
    `uploaded`, what was handed to the transfer service, by package name;
    `tasks`, what the tasks of an import job answer (all completed unless a
    test says otherwise)."""
    discovery({"llm": "llm-disc", "embedding": "emb-disc"})
    folder = tmp_path / "SAS-Viya-Integrations"
    folder.mkdir()
    # only the list is read: an importer's checkout need not hold every source
    (folder / "package-manifest.yaml").write_text(MANIFEST, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SAS_VIYA_URL", "https://site.example")
    monkeypatch.setattr(cli, "Context", lambda: SimpleNamespace(repo=tmp_path, core=None))
    viya = Viya()
    monkeypatch.setattr(cli, "_viya_session", lambda: viya)
    monkeypatch.setattr(transfer, "get_mapping", lambda session, package_id: _mapping())
    monkeypatch.setattr(transfer, "job_messages", lambda session, job: ["the folder is locked"])
    monkeypatch.setattr(transfer, "wait_for_job",
                        lambda session, uri, timeout=600: {"state": uri, "links": [{"rel": "self", "href": uri}]})
    monkeypatch.setattr(transfer, "job_tasks", lambda session, job: list(run.tasks))

    def run(fake: Site, *args, fails=False, extra=(), without_report=False, package=None):
        fake = site(fake)
        package = package or _built(report=not without_report).build()
        (folder / PACKAGE_FILE).write_text(json.dumps(package), encoding="utf-8")
        named = {package["name"]: package}
        for path, other in extra:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(json.dumps(other), encoding="utf-8")
            named[other["name"]] = other
        failing = {fails} if isinstance(fails, str) else ({"SAS Agentic AI Accelerator"} if fails else set())
        run.imports, run.uploaded = {}, {}

        def upload_package_bytes(session, name, data):
            handed = json.loads(data)
            run.uploaded[handed["name"]] = handed
            return {"id": handed["name"]}

        def start_import(session, name, package_uri, mapping=None, description=""):
            run.imports[name] = mapping
            # an import that works puts the reports it was handed on the site
            if name not in failing:
                for detail in run.uploaded[name]["transferDetails"]:
                    summary = detail["transferObject"]["summary"]
                    if summary["type"] == "report":
                        fake.reports[summary["name"]] = _xml(
                            NEW_DEFAULTS if summary["name"] == "Prompt Builder" else OLD_RAG)
                        fake.unreadable.discard(summary["name"])       # the report that was there is replaced
            return {"links": [{"rel": "self", "href": "failed" if name in failing else "completed"}]}

        monkeypatch.setattr(transfer, "upload_package_bytes", upload_package_bytes)
        monkeypatch.setattr(transfer, "start_import", start_import)
        # what is given back is what THIS run printed
        out.seek(0)
        out.truncate()
        result = CliRunner().invoke(cli.app, ["builders-import", "--no-releases", *args])
        return result, fake, out.getvalue()
    run.viya, run.tasks = viya, []
    return run


def _captured_files(tmp_path) -> list:
    return sorted(path.name for path in tmp_path.glob("builder-options-captured-*.json"))


def test_import_restores_a_2_2_file_into_the_merged_report(importing, tmp_path):
    options = tmp_path / "builder-options.json"
    options.write_text(json.dumps({"version": 1, "reports": SAVED_2_2}), encoding="utf-8")
    result, fake, text = importing(Site(**{"Prompt Builder": OLD_PROMPT, "RAG Builder": OLD_RAG}),
                                   "--options", str(options))
    assert result.exit_code == 0, text
    assert f"{PACKAGE_FILE} - SAS Agentic AI Accelerator: 5 folder, 1 jobDefinition, 1 flowStep, 1 file, 1 report" \
        in text
    # the report, with the two folders it sits in, is what the transfer service was handed
    assert "  Reports, through the transfer service (2 folder, 1 report): Prompt Builder/Prompt Builder\n" in text
    assert "imported 3 object(s) through the transfer service." in text
    handed = importing.uploaded["SAS Agentic AI Accelerator"]
    assert sorted((d["transferObject"]["summary"]["type"], d["transferObject"]["summary"]["name"])
                  for d in handed["transferDetails"]) == [
        ("folder", "Prompt Builder"), ("folder", "SAS Agentic AI Accelerator"), ("report", "Prompt Builder")]
    assert handed["transferObjectCount"] == 3 == len(handed["requestedItems"])
    # everything else came in place, with the package as the source
    assert f"  Files, custom steps and job definitions, in place in {CONTENT_ROOT}:\n" in text
    assert "    7 entries: 7 created." in text
    viya = importing.viya
    assert viya.files[viya.at(f"{CONTENT_ROOT}/{FILE_PATH}")[0]] == b"CORE = 2\n"
    assert viya.steps[viya.at(f"{CONTENT_ROOT}/{STEP_PATH}")[0]]["templates"] == STEP["templates"]
    page, _ = viya.jobs[viya.at(f"{CONTENT_ROOT}/{UI_PATH}")[0]]
    assert page["properties"] == [{"name": "form", "value": "<html>the page of the release</html>"}]
    restored = fake.options("Prompt Builder")
    assert restored["enableRag"] == "true" and restored["contentRoot"] == "/Team/RAG"
    assert restored["ragComputeContext"] == "SAS Studio compute context"
    assert restored["llmProjectID"] == "llm-live"
    assert fake.written == ["Prompt Builder"]
    # the file that was named is the source: nothing was read from the report, nothing kept in a second file
    assert fake.read == ["Prompt Builder"] and _captured_files(tmp_path) == []
    # the retired report is still on the site, untouched, and the command says so once
    assert fake.options("RAG Builder") == OLD_RAG
    assert text.count("A report named 'RAG Builder' still exists on this site.") == 1
    assert "Nothing was deleted." in text
    assert "This package predates 2.3.0" not in text
    assert "Done." in text and "Properties panel" not in text


def test_a_first_install_is_configured_from_discovery_with_rag_off(importing, tmp_path):
    result, fake, text = importing(Site())
    assert result.exit_code == 0, text
    configured = fake.options("Prompt Builder")
    assert configured["llmProjectID"] == "llm-disc" and configured["embeddingProjectID"] == "emb-disc"
    assert configured["modelRepositoryID"] == "repo-disc"
    assert configured["SCREndpoint"] == "https://discovered.example/llm"
    # the placeholder host is gone, and RAG stays off until someone enables it
    assert configured["viyaHost"] == "https://site.example"
    assert configured["enableRag"] == "false" and configured["enableDecision"] == "true"
    assert "retired" not in text
    assert "so RAG is off until it is enabled - set them in the Options pane" in text
    # there was no report to read options from, and no file is left behind for it
    assert "Options: read from the report" not in text and _captured_files(tmp_path) == []


# ---- an upgrade is one command: the options are kept without a file ----------
def test_an_upgrade_without_options_keeps_what_the_site_had(importing, no_discovery, tmp_path):
    """The import replaces the report and with it every option an admin set.
    Without --options the command reads them first - the retired report's
    too - and writes them back, so `mdb builders-import` alone is an upgrade."""
    asked = no_discovery()
    result, fake, text = importing(Site(**{"Prompt Builder": OLD_PROMPT, "RAG Builder": OLD_RAG}))
    assert result.exit_code == 0, text
    kept, = _captured_files(tmp_path)
    assert f"Options: read from the report on this site and kept in {kept}; they are written back after the import." \
        in text
    assert f"Writing back the options the report held before the import ({kept}):" in text
    restored = fake.options("Prompt Builder")
    assert restored["llmProjectID"] == "llm-live" and restored["modelRepositoryID"] == "repo-live"
    assert restored["SCREndpoint"] == "https://site.example/llm" and restored["enableOptimization"] == "true"
    # what the retired report held is folded in, as `mdb options-save` folds it
    assert restored["enableRag"] == "true" and restored["contentRoot"] == "/Team/RAG"
    assert restored["embeddingProjectID"] == "emb-live" and restored["storeSslmode"] == "disable"
    assert restored["ragComputeContext"] == "SAS Studio compute context"
    # nothing was discovered: the report had its values, and discovery creates what is missing
    assert asked == []
    assert "keep the package's defaults" not in text and "Done." in text
    # the kept file is an options file like any other
    document = json.loads((tmp_path / kept).read_text(encoding="utf-8"))
    assert document["kind"] == "sas-agentic-ai-accelerator/builder-options"
    assert document["deployment"] == "https://site.example" and list(document["reports"]) == ["Prompt Builder"]
    assert document["reports"]["Prompt Builder"]["llmProjectID"] == "llm-live"
    # the retired report is still there: the import removed nothing, and the command says so
    assert fake.options("RAG Builder") == OLD_RAG
    assert text.count("A report named 'RAG Builder' still exists on this site.") == 1


def test_a_site_with_only_the_retired_report_keeps_its_rag_options(importing, no_discovery, tmp_path):
    no_discovery()
    result, fake, text = importing(Site(**{"RAG Builder": OLD_RAG}))
    assert result.exit_code == 0, text
    restored = fake.options("Prompt Builder")
    assert restored["contentRoot"] == "/Team/RAG" and restored["enableRag"] == "true"
    assert restored["embeddingProjectID"] == "emb-live" and len(_captured_files(tmp_path)) == 1


def test_a_failed_import_after_the_capture_names_the_kept_file(importing, tmp_path):
    """The repeat must not read the options again: had the import replaced
    the report before it failed, a second capture would be of the package's
    defaults. The line names the file the first run kept."""
    result, fake, text = importing(Site(**{"Prompt Builder": OLD_PROMPT}), fails=True)
    assert result.exit_code == 1
    kept, = _captured_files(tmp_path)
    assert fake.written == [] and fake.options("Prompt Builder") == OLD_PROMPT
    assert "Prompt Builder: its package did not import - options not restored." in text
    assert (f"then run:\nmdb builders-import --options {kept} --no-releases\n"
            f"({PACKAGE_FILE} did not import in full - this imports again and writes the options back") in text
    assert "\nmdb options-restore" not in text and "Done." not in text


def test_options_that_could_not_be_written_back_are_still_in_the_kept_file(importing, tmp_path):
    """The import went through and the write-back did not. The site is on the
    package's defaults; what it had is in the file, and the line says how to
    put it back."""
    site_with_a_locked_report = Site(**{"Prompt Builder": OLD_PROMPT})
    site_with_a_locked_report.refuses = True
    result, fake, text = importing(site_with_a_locked_report)
    assert result.exit_code == 1
    kept, = _captured_files(tmp_path)
    assert "Prompt Builder: writing the report failed (HTTP 412)." in text
    assert fake.options("Prompt Builder")["llmProjectID"] == ""              # the package's default
    assert (f"then run:\nmdb options-restore --file {kept}\n(every package imported - only the options are left "
            "to write)") in text
    assert json.loads((tmp_path / kept).read_text(encoding="utf-8"))["reports"]["Prompt Builder"]["llmProjectID"] \
        == "llm-live"


def test_two_runs_keep_two_files(importing, tmp_path):
    """A kept file is never written over: the report a second run reads may
    already be the imported one."""
    importing(Site(**{"Prompt Builder": OLD_PROMPT}))
    importing(Site(**{"Prompt Builder": TUNED}))
    kept = [json.loads((tmp_path / name).read_text(encoding="utf-8"))["reports"]["Prompt Builder"]["llmProjectID"]
            for name in _captured_files(tmp_path)]
    assert sorted(kept) == ["llm-live", "llm-tuned"]


def test_a_failed_import_leaves_the_old_report_alone(importing, tmp_path):
    options = tmp_path / "builder-options.json"
    options.write_text(json.dumps({"version": 1, "reports": SAVED_2_2}), encoding="utf-8")
    result, fake, text = importing(Site(**{"Prompt Builder": OLD_PROMPT}), "--options", str(options), fails=True)
    assert result.exit_code == 1
    assert "import ended failed" in text and "the folder is locked" in text
    assert fake.written == [] and fake.options("Prompt Builder") == OLD_PROMPT
    assert "Prompt Builder: its package did not import - options not restored." in text
    assert "not an option of this report version" not in text
    # the report on the site is the 2.2.x one: a restore alone cannot reach it
    assert (f"then run:\nmdb builders-import --options {cli._shell_word(options)} --no-releases\n"
            f"({PACKAGE_FILE} did not import") in text
    assert "\nmdb options-restore" not in text and "Done." not in text
    assert "Not everything was imported and configured." in text


@pytest.fixture
def no_discovery(monkeypatch):
    """Discovery creates a missing repository or project, so where it must
    not run, it must not be called at all. Returns the kinds it was asked
    for - the command catches what a discovery raises, so raising alone
    would not show."""
    asked: list = []

    def never(session, kind, *_args, **_kwargs):
        asked.append(kind)
        raise AssertionError("discovery creates a missing project - it must not run here")

    def install() -> list:
        monkeypatch.setattr(registry, "ensure_repository_and_project", never)
        return asked
    return install


def test_a_failed_first_install_discovers_nothing_and_says_how_to_go_on(importing, no_discovery):
    asked = no_discovery()
    result, fake, text = importing(Site(), fails=True)
    assert result.exit_code == 1 and asked == []
    assert fake.written == [] and "Prompt Builder" not in fake.reports
    assert "No Builder report was imported" in text
    assert "then run:\nmdb builders-import --no-releases\n(it imports again and configures the report" in text


TUNED = {**NEW_DEFAULTS, "viyaHost": "https://site.example", "SCREndpoint": "https://scr.site.example/llm",
         "modelRepositoryID": "repo-tuned", "llmProjectID": "llm-tuned", "embeddingProjectID": "emb-tuned",
         "enableRag": "true"}


# ---- a package that carries no report leaves the report alone -----------------
def test_a_package_without_the_report_leaves_the_report_alone(importing, no_discovery, tmp_path):
    """A package exported from a folder that holds no report - a test folder
    that `mdb content-deploy` filled. The Prompt Builder report on the site
    is not this run's to read, to configure or to write."""
    asked = no_discovery()
    result, fake, text = importing(Site(**{"Prompt Builder": TUNED}), without_report=True)
    assert result.exit_code == 0, text
    assert "    7 entries: 7 created." in text and "transfer service" not in text
    assert importing.uploaded == {} and importing.imports == {}
    assert asked == [] and fake.read == [] and fake.written == []
    assert fake.options("Prompt Builder") == TUNED and _captured_files(tmp_path) == []
    assert "No Builder report was imported - the options of a report on this site are left as they are." in text
    # and nothing about package defaults of a report that was not imported
    assert "Done." in text and "keep the package's defaults" not in text


def test_a_package_without_the_report_still_names_a_retired_report(importing, no_discovery):
    no_discovery()
    result, fake, text = importing(Site(**{"Prompt Builder": TUNED, "RAG Builder": OLD_RAG}), without_report=True)
    assert result.exit_code == 0, text
    assert text.count("A report named 'RAG Builder' still exists on this site.") == 1
    assert fake.written == []


def test_options_named_with_a_package_without_the_report_are_restored_all_the_same(importing, tmp_path):
    """--options is an instruction, whatever was imported."""
    options = tmp_path / "builder-options.json"
    options.write_text(json.dumps({"version": 1, "reports": {"Prompt Builder": {"llmProjectID": "llm-file"}}}),
                       encoding="utf-8")
    result, fake, text = importing(Site(**{"Prompt Builder": TUNED}), "--options", str(options), without_report=True)
    assert result.exit_code == 0, text
    assert fake.options("Prompt Builder")["llmProjectID"] == "llm-file"
    assert fake.options("Prompt Builder")["embeddingProjectID"] == "emb-tuned"


def test_a_restore_that_cannot_reach_the_report_does_not_end_with_done(importing, tmp_path):
    """A package without the report with a 2.2.x options file, on a site whose
    Prompt Builder report is still the 2.2.x one: nothing of the RAG part can
    be written. The exit code has to say so - the next step of the upgrade is
    deleting the retired report."""
    options = tmp_path / "builder-options.json"
    options.write_text(json.dumps({"version": 1, "reports": SAVED_2_2}), encoding="utf-8")
    result, fake, text = importing(Site(**{"Prompt Builder": OLD_PROMPT, "RAG Builder": OLD_RAG}),
                                   "--options", str(options), without_report=True)
    assert result.exit_code == 1
    assert "enableRag" not in fake.options("Prompt Builder")
    assert "Prompt Builder: 7 option(s) the Prompt Builder reads cannot be written" in text
    assert "Not everything was imported and configured." in text and "Done." not in text
    # the package imported: what is left is the options
    assert (f"then run:\nmdb options-restore --file {cli._shell_word(options)}\n(every package imported - "
            "only the options are left to write)") in text
    assert "\nmdb builders-import" not in text


# ---- the tables of the reports -------------------------------------------------
def test_the_release_table_is_bound_and_the_other_tables_are_left_as_exported(importing):
    result, fake, text = importing(Site(), "--caslib", "Team", "--table", "RELEASES")
    assert result.exit_code == 0, text
    targets = {c["source"].rsplit("=", 1)[-1]: c["target"]
               for c in importing.imports["SAS Agentic AI Accelerator"]["connectors"]["table"]}
    assert targets == {"ACCELERATOR_RELEASES": "server=cas-shared-default;library=Team;table=RELEASES",
                       "LLM_LOGS": "server=cas-shared-default;library=Public;table=LLM_LOGS",
                       "LLM_FACT_SHEET": "server=cas-shared-default;library=Public;table=LLM_FACT_SHEET"}
    assert "mapping: data source server=cas-shared-default;library=Public;table=LLM_LOGS left as exported" in text
    assert "mapping: Prompt Builder: VisualElement:ve9 -> host site.example" in text


def test_the_other_tables_are_retargeted_where_the_site_asks_for_it(importing):
    """A site that keeps LLM_LOGS and the fact sheets in a library of its own:
    the usage report is bound there, table by table and under the same names."""
    result, fake, text = importing(Site(), "--report-caslib", "Monitoring")
    assert result.exit_code == 0, text
    targets = {c["source"].rsplit("=", 1)[-1]: c["target"]
               for c in importing.imports["SAS Agentic AI Accelerator"]["connectors"]["table"]}
    assert targets == {"ACCELERATOR_RELEASES": "server=cas-shared-default;library=Public;table=ACCELERATOR_RELEASES",
                       "LLM_LOGS": "server=cas-shared-default;library=Monitoring;table=LLM_LOGS",
                       "LLM_FACT_SHEET": "server=cas-shared-default;library=Monitoring;table=LLM_FACT_SHEET"}
    assert ("mapping: data source server=cas-shared-default;library=Public;table=LLM_LOGS -> "
            "server=cas-shared-default;library=Monitoring;table=LLM_LOGS") in text
    assert "left as exported" not in text


# ---- the rules of the content folder -------------------------------------------
def test_the_rules_of_the_content_folder_are_written_as_commands_to_review(importing, tmp_path):
    """The package carries no authorization rule. Like `mdb setup` for the
    repository, the command writes the commands and runs none."""
    result, fake, text = importing(Site(), "--out", str(tmp_path / "rules"))
    assert result.exit_code == 0, text
    written = (tmp_path / "rules" / "sas-viya-cli-content-commands.txt").read_text(encoding="utf-8")
    commands = [line for line in written.splitlines() if line.startswith("sas-viya ")]
    assert len(commands) == 2 and all(" -g LLMConsumers -p Read " in line for line in commands)
    root_id = importing.viya.folder_id(CONTENT_ROOT)
    assert root_id and commands[0].startswith(f"sas-viya authorization create-rule -o /folders/folders/{root_id} ")
    assert commands[1].startswith(
        f"sas-viya authorization create-rule --container-uri /folders/folders/{root_id} ")
    assert "/SAS Agentic AI Accelerator" in written and "sas-viya-cli-commands.txt" in written
    assert "Wrote sas-viya-cli-content-commands.txt" in text and "Review the file before running it." in text
    # the file mdb setup writes has another name and is not written over
    assert sorted(p.name for p in (tmp_path / "rules").iterdir()) == ["sas-viya-cli-content-commands.txt"]


def test_no_rules_file_without_an_import_or_when_it_is_not_wanted(importing, tmp_path):
    result, _, text = importing(Site(), "--no-files")
    assert result.exit_code == 0 and not (tmp_path / "sas-viya-cli-content-commands.txt").exists()
    result, _, text = importing(Site(), "--dry-run")
    assert result.exit_code == 0 and not (tmp_path / "sas-viya-cli-content-commands.txt").exists()
    result, _, text = importing(Site(), fails=True)
    assert result.exit_code == 1 and not (tmp_path / "sas-viya-cli-content-commands.txt").exists()
    result, _, text = importing(Site())
    assert result.exit_code == 0 and (tmp_path / "sas-viya-cli-content-commands.txt").is_file()


def test_the_rules_carry_a_placeholder_where_the_folder_is_not_found():
    text = registry.content_rules_text("/Team/Accelerator", None)
    assert "-o /folders/folders/<content-folder-id> " in text and "/Team/Accelerator" in text


# ---- the package of the checkout -----------------------------------------------
def test_without_a_package_in_the_checkout_the_command_says_so(importing, tmp_path, monkeypatch):
    importing(Site())                                           # sets the fake checkout up
    (tmp_path / "SAS-Viya-Integrations" / PACKAGE_FILE).unlink()
    monkeypatch.setattr(cli, "_viya_session", lambda: pytest.fail("signed in without a package to import"))
    before = len(cli.console.file.getvalue())
    result = CliRunner().invoke(cli.app, ["builders-import"])
    text = cli.console.file.getvalue()[before:]
    assert result.exit_code == 2
    assert "This checkout holds no SAS-Viya-Integrations/SAS-Agentic-AI-Accelerator.json" in text
    result = CliRunner().invoke(cli.app, ["builders-import", str(tmp_path / "elsewhere.json")])
    assert result.exit_code == 2 and "elsewhere.json not found." in cli.console.file.getvalue()


def test_the_command_to_repeat_is_the_one_that_was_run(importing, tmp_path):
    """A package that was named and flags that were given are in the line: a
    repeat without them would import another package, or bind the reports to
    other tables."""
    options = tmp_path / "builder-options.json"
    options.write_text(json.dumps({"version": 1, "reports": SAVED_2_2}), encoding="utf-8")
    package = tmp_path / "SAS-Viya-Integrations" / PACKAGE_FILE
    result, fake, text = importing(Site(**{"Prompt Builder": TUNED}), str(package), "--options", str(options),
                                   "--caslib", "Team", "--report-caslib", "Monitoring", "--table", "RELEASES",
                                   "--timeout", "30", fails=True)
    assert result.exit_code == 1
    assert (f"then run:\nmdb builders-import {cli._shell_word(package)} --options {cli._shell_word(options)} "
            "--caslib Team --report-caslib Monitoring --table RELEASES --no-releases --timeout 30\n(") in text


@pytest.mark.parametrize("width", [80, 120, 40])
def test_the_command_to_repeat_is_one_line_on_a_terminal_of_any_width(importing, monkeypatch, out, tmp_path, width):
    """The line is selected and pasted. Wrapped by the console at the width
    of the terminal it is several commands, and the first of them - `mdb
    builders-import` alone - imports the default package without the options
    file. A directory named like a Rich style tag has to stay in it too."""
    monkeypatch.setattr(cli, "console", Console(file=out, width=width, force_terminal=False))
    options = tmp_path / "builder-options.json"
    options.write_text(json.dumps({"version": 1, "reports": SAVED_2_2}), encoding="utf-8")
    other = tmp_path / "pkgs[old]" / "RAG[old].json"
    main = tmp_path / "SAS-Viya-Integrations" / PACKAGE_FILE
    result, fake, text = importing(
        Site(**{"Prompt Builder": OLD_PROMPT}), str(main), str(other), "--options", str(options),
        extra=[(other, {"name": "A test folder", "transferDetails": [_other("folder", "RAG")]})],
        fails="A test folder")
    assert result.exit_code == 1
    command = (f"mdb builders-import {cli._shell_word(main)} {cli._shell_word(other)} "
               f"--options {cli._shell_word(options)} --no-releases")
    assert len(command) > width and "pkgs[old]/RAG[old].json" in command
    lines = text.splitlines()
    assert command in lines, text
    at = lines.index(command)
    # the sentences around it are prose, and wrap as the console likes
    assert lines[at - 1].endswith("run:")
    assert "".join(lines[at + 1:]).replace(" ", "").startswith("(RAG[old].jsondidnotimport")
    # and what is pasted is what was run
    assert shlex.split(command)[2:] == [main.as_posix(), other.as_posix(), "--options", options.as_posix(),
                                        "--no-releases"]


def test_the_command_to_repeat_quotes_a_path_with_a_space():
    assert cli._import_command(None, None, None, None, None, False, cli.IMPORT_TIMEOUT) == "mdb builders-import"
    packages, options = Path("my packages") / "p.json", Path("my files") / "o.json"
    assert cli._import_command([packages], options, None, None, "cas a", True, cli.IMPORT_TIMEOUT) == (
        'mdb builders-import "my packages/p.json" --options "my files/o.json" --server "cas a" --no-releases')
    assert cli._import_command(None, None, "Team", None, None, False, cli.IMPORT_TIMEOUT, "My Logs") == (
        'mdb builders-import --caslib Team --report-caslib "My Logs"')


@pytest.mark.parametrize("value, word", [
    # nothing a shell reads: as it is
    ("Team", "Team"), ("cas-shared-default", "cas-shared-default"), ("A_B.c:1", "A_B.c:1"),
    (PurePosixPath("saved/builder-options.json"), "saved/builder-options.json"),
    # a Windows path with forward slashes: bash eats a backslash, and Windows reads both
    (PureWindowsPath(r"saved\builder-options.json"), "saved/builder-options.json"),
    (PureWindowsPath(r"C:\Users\me\o.json"), "C:/Users/me/o.json"),
    # anything else in double quotes, which PowerShell, bash and cmd all read
    (PureWindowsPath(r"C:\a b\o.json"), '"C:/a b/o.json"'),
    (PureWindowsPath(r"C:\R&D\p.json"), '"C:/R&D/p.json"'),
    (PureWindowsPath(r"C:\Users\O'Brien\p.json"), '"C:/Users/O\'Brien/p.json"'),
    (PurePosixPath("a(b).json"), '"a(b).json"'), ("a;b", '"a;b"'), ("~x", '"~x"'), ("a*", '"a*"'),
    ("=ls", '"=ls"'),                                        # zsh reads a leading = as "the path of"
    ("", '""'),
    # what double quotes do not keep as it is, in single quotes
    (PurePosixPath('a"b.json'), "'a\"b.json'"), ("cost$HOME", "'cost$HOME'"), ("a`b", "'a`b'"),
    ("wow!now", "'wow!now'"), ("a\\\\b c", "'a\\\\b c'"),
])
def test_an_argument_of_the_command_to_repeat_is_safe_to_paste(value, word):
    assert cli._shell_word(value) == word
    # read back the way a POSIX shell reads it
    assert shlex.split(word) == [value.as_posix() if hasattr(value, "as_posix") else value]


def test_an_argument_with_both_kinds_of_quote_is_quoted_for_the_shell_of_the_platform():
    """The one case with no spelling that both shells read."""
    assert cli._shell_word("it's $5", windows=False) == "'it'\\''s $5'"
    assert shlex.split(cli._shell_word("it's $5", windows=False)) == ["it's $5"]
    assert cli._shell_word("it's $5", windows=True) == "'it''s $5'"


def test_the_command_to_repeat_leaves_out_an_empty_value():
    """`--caslib ""` is read as not given by the run. Printed as `--caslib`
    with nothing behind it, the repeat takes the next flag as the name."""
    assert cli._import_command(None, None, "", "", "", True, cli.IMPORT_TIMEOUT, "") == \
        "mdb builders-import --no-releases"


# ---- a dry run -------------------------------------------------------------------
def test_a_dry_run_without_options_discovers_nothing(importing, no_discovery, tmp_path):
    """`--dry-run` is documented as "import nothing". Discovery creates the
    repository and the projects when they are missing, so on a first install
    a dry run that discovers is a dry run that writes."""
    asked = no_discovery()
    result, fake, text = importing(Site(), "--dry-run")
    assert result.exit_code == 0, text
    assert asked == [] and fake.written == [] and "Prompt Builder" not in fake.reports
    assert "Would configure the imported report from this deployment" in text
    assert "Prompt Builder: not on this site yet - it arrives with the import." in text
    assert text.count("dry run - nothing imported.") == 1 and "Done." not in text
    assert importing.imports == {} and _captured_files(tmp_path) == []


def test_a_dry_run_on_a_site_with_the_report_reads_its_options_and_keeps_no_file(importing, no_discovery, tmp_path):
    """The options are the report's own, so comparing the report with them
    would say "already matches" about nothing. The dry run says what a run
    does with them, and leaves no file and no write behind."""
    asked = no_discovery()
    result, fake, text = importing(Site(**{"Prompt Builder": TUNED}), "--dry-run")
    assert result.exit_code == 0, text
    assert asked == [] and fake.written == [] and fake.options("Prompt Builder") == TUNED
    assert "Prompt Builder: 17 options (report)." in text
    assert ("Options: read from the report on this site; a run keeps them in a file and writes them back after "
            "the import.") in text
    assert "already matches" not in text and "Would configure the imported report" not in text
    assert _captured_files(tmp_path) == [] and importing.imports == {}


def test_a_package_from_before_the_merge_is_named_as_such(importing, tmp_path):
    """A package of an earlier release that somebody names, with the
    standalone UI still inside: imported, because it was asked for, and said
    out loud."""
    old = tmp_path / "old" / "RAG-Builder-of-2.2.json"
    main = tmp_path / "SAS-Viya-Integrations" / PACKAGE_FILE
    result, fake, text = importing(
        Site(), str(main), str(old),
        extra=[(old, _retired_package())])
    assert result.exit_code == 0, text
    assert "RAG-Builder-of-2.2.json" in text
    assert ("This package predates 2.3.0: it carries report RAG Builder, jobDefinition RAG-Builder-UI"
            in text)
    # the report it brought is on the site now: retired, and not configured
    assert text.count("A report named 'RAG Builder' still exists on this site.") == 1
    assert fake.written == ["Prompt Builder"] and fake.options("RAG Builder") == OLD_RAG


def test_a_dry_run_imports_nothing_and_counts_what_the_new_report_adds(importing, tmp_path):
    options = tmp_path / "builder-options.json"
    options.write_text(json.dumps({"version": 1, "reports": SAVED_2_2}), encoding="utf-8")
    result, fake, text = importing(Site(**{"Prompt Builder": OLD_PROMPT, "RAG Builder": OLD_RAG}),
                                   "--options", str(options), "--dry-run")
    assert result.exit_code == 0, text
    assert fake.written == [] and fake.options("Prompt Builder") == OLD_PROMPT
    assert text.count("dry run - nothing imported.") == 1
    assert text.count("arrive with the imported report and are written then") == 1
    assert "is not an option of this report version" not in text


# ---- in place: a site whose objects have other ids ----------------------------------
def _id(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


NO_CONTEXT = {**TUNED, "ragComputeContext": ""}


def _site_with_its_own_objects(viya, root=CONTENT_ROOT) -> dict:
    """A site that has the step, the file and the job definition of the
    package at their paths - registered by a deploy of its own, so under ids
    the package knows nothing of, and with the content of an earlier release.
    Returns {path: uri}."""
    return {
        STEP_PATH: viya.put_member(f"{root}/{STEP_PATH}", "/dataFlows/steps/",
                                   {**STEP, "templates": {"SAS": "%put the step of an earlier release;\n"}}),
        FILE_PATH: viya.put_member(f"{root}/{FILE_PATH}", "/files/files/", b"CORE = 1\n"),
        UI_PATH: viya.put_member(f"{root}/{UI_PATH}", "/jobDefinitions/definitions/", {
            "name": "SAS-LLM-Prompt-Builder-UI", "type": "Compute", "code": UI_JOB["code"],
            "parameters": [{**UI_JOB["parameters"][0], "defaultValue": "Team compute context"},
                           UI_JOB["parameters"][1]],
            "properties": [{"name": "form", "value": "<html>the page of an earlier release</html>"}]}),
    }


def test_an_object_registered_under_another_id_is_updated_in_place_and_keeps_its_id(importing, no_discovery):
    """What the transfer service was measured not to do: a file that sat in
    the folder under the same name with another id was left as it was, the
    package's file was written into no folder, and the job ended completed.
    Here the object at the path is the object that is written."""
    no_discovery()
    viya = importing.viya
    mine = _site_with_its_own_objects(viya)
    result, fake, text = importing(Site(**{"Prompt Builder": NO_CONTEXT}))
    assert result.exit_code == 0, text
    # one line per entry, with the id the object has on THIS site
    for line in (
            f"    updated      step          {STEP_PATH}  (templates; id {_id(mine[STEP_PATH])} kept)\n",
            f"    updated      file          {FILE_PATH}  (content replaced in place; id {_id(mine[FILE_PATH])} kept)\n",
            f"    updated      jobDefinition {UI_PATH}  (form; id {_id(mine[UI_PATH])} kept)\n",
            "    equal        folder        RAG/steps\n",
            "    7 entries: 4 equal, 3 updated.\n"):
        assert line in text, line
    # the same objects, and one of each name
    assert viya.ids(CONTENT_ROOT) == mine
    assert viya.steps[mine[STEP_PATH]]["templates"] == STEP["templates"]
    assert viya.files[mine[FILE_PATH]] == b"CORE = 2\n"
    page, _etag = viya.jobs[mine[UI_PATH]]
    assert page["properties"] == [{"name": "form", "value": "<html>the page of the release</html>"}]
    # the id, the links and the user of the exporting system reached nothing
    assert "exported" not in json.dumps([viya.steps, [body for body, _ in viya.jobs.values()]])
    # the compute context the site set in the job definition is the site's: left as it is
    assert page["parameters"][0]["defaultValue"] == "Team compute context"
    # nothing was deleted, and no object was posted beside one that was there
    assert {method for method, _path in viya.wrote()} == {"PUT"}
    # a second import finds it all equal, under the same ids, and writes nothing
    viya.calls.clear()
    result, fake, text = importing(Site(**{"Prompt Builder": NO_CONTEXT}))
    assert result.exit_code == 0, text
    assert f"    equal        step          {STEP_PATH}  (id {_id(mine[STEP_PATH])})\n" in text
    assert f"    equal        file          {FILE_PATH}  (id {_id(mine[FILE_PATH])})\n" in text
    assert "    7 entries: 7 equal.\n" in text and viya.wrote() == [] and viya.ids(CONTENT_ROOT) == mine


def test_two_objects_of_one_name_on_the_site_are_named_and_the_rest_is_brought(importing, no_discovery):
    no_discovery()
    viya = importing.viya
    mine = _site_with_its_own_objects(viya)
    second = viya.put_member(f"{CONTENT_ROOT}/{STEP_PATH}", "/dataFlows/steps/", dict(STEP))
    result, fake, text = importing(Site(**{"Prompt Builder": NO_CONTEXT}))
    assert result.exit_code == 1
    assert (f"    FAILED       step          {STEP_PATH}  (2 registered steps of that name are in the folder (ids "
            f"{_id(mine[STEP_PATH])}, {_id(second)}): none was changed") in text
    assert "    7 entries: 4 equal, 2 updated, 1 FAILED." in text and "Stopped at" not in text
    assert viya.steps[mine[STEP_PATH]]["templates"] == {"SAS": "%put the step of an earlier release;\n"}
    assert viya.files[mine[FILE_PATH]] == b"CORE = 2\n"
    assert f"({PACKAGE_FILE} did not import in full - this imports again" in text


def test_the_in_place_part_stops_at_the_first_write_that_fails(importing, no_discovery):
    """The reports are in, so their options are written back whatever comes
    after; of the content the line says what was written and what was not."""
    no_discovery()
    viya = importing.viya
    mine = _site_with_its_own_objects(viya)
    viya.refuse[("PUT", "/files/files/")] = 500
    result, fake, text = importing(Site(**{"Prompt Builder": NO_CONTEXT}))
    assert result.exit_code == 1
    assert (f"    FAILED       file          {FILE_PATH}  (the file was left as it is: the update of its content "
            "was refused (HTTP 500") in text
    assert "    7 entries: 4 equal, 2 updated, 1 FAILED." in text
    # the file is the last object of the package, so nothing stands behind it; what was written says so
    assert (f"    Stopped at the first write that failed: file {FILE_PATH}. Written before it: 0 created, 2 "
            "updated. Not attempted behind it: 0 entries. Nothing was removed.") in text
    assert viya.files[mine[FILE_PATH]] == b"CORE = 1\n"
    assert fake.written == ["Prompt Builder"] and fake.options("Prompt Builder")["llmProjectID"] == "llm-tuned"
    assert "Not everything was imported and configured." in text
    # an entry behind the one that failed is listed, not passed over
    built = _built()
    built.file("RAG/rag_core/later.py", b"LATER = 1\n")
    result, fake, text = importing(Site(**{"Prompt Builder": NO_CONTEXT}), package=built.build())
    assert "    not attempted file          RAG/rag_core/later.py\n" in text
    assert "Not attempted behind it: 1 entry." in text and viya.at(f"{CONTENT_ROOT}/RAG/rag_core/later.py") == []


def test_every_object_of_the_package_gets_a_line(importing, no_discovery):
    """review-18: an empty folder of the package got no line and was not
    created, a folder named '..' was created (inside the root), and an object
    of a kind mdb does not know was 'not below the root folder'."""
    no_discovery()
    viya = importing.viya
    built = _built(report=False)
    built.folder("Empty Folder")
    built.file("../../Victim/evil2.py", b"x = 1\n")
    built._add("dataFlow", "a flow.flw", "/dataFlows/dataFlows/", "", built.folder("RAG"))
    result, fake, text = importing(Site(), package=built.build())
    assert result.exit_code == 0, text
    assert "    created      folder        Empty Folder\n" in text and viya.folder_id(f"{CONTENT_ROOT}/Empty Folder")
    assert "  not imported: folder '..' (its name is '..'" in text
    assert "  not imported: file '../../Victim/evil2.py' (the name of the folder '..' above it is '..'" in text
    assert "  not imported: dataFlow 'RAG/a flow.flw' (mdb does not import a dataFlow" in text
    assert ".." not in viya.tree(CONTENT_ROOT) and viya.folder_id("/Victim") is None


# ---- where the content goes, and where the reports may ------------------------------
ROOT2 = "/Users/me/My Folder/ZZ_VERIFY_R18/root2"
START_JOB = {"type": "Compute", "code": "%put start here;\n", "parameters": [
    {"version": 1, "name": "_contextName", "defaultValue": "SAS Studio compute context", "type": "character"},
    {"version": 1, "name": "contentRoot", "defaultValue": CONTENT_ROOT, "type": "character"}]}


def _never(what: str):
    def fail(*args, **kwargs):
        pytest.fail(f"the transfer service was called ({what})")
    return fail


def test_the_content_goes_to_the_root_that_is_named_and_the_reports_stay_out(importing, no_discovery, tmp_path):
    """A pass inside a test root: everything but the reports is brought into
    the folder, in place, and the transfer service is not called at all."""
    asked = no_discovery()
    viya = importing.viya
    mine = _site_with_its_own_objects(viya, ROOT2)
    built = _built()
    built.job("Start Here", START_JOB)
    result, fake, text = importing(Site(**{"Prompt Builder": TUNED}), "--content-root", ROOT2, "--no-reports",
                                   "--no-files", package=built.build())
    assert result.exit_code == 0, text
    assert importing.uploaded == {} and importing.imports == {}
    assert "  Reports (Prompt Builder/Prompt Builder): NOT imported - left out (--no-reports).\n" in text
    assert f"  Files, custom steps and job definitions, in place in {ROOT2}:\n" in text
    assert f"    updated      step          {STEP_PATH}  (templates; id {_id(mine[STEP_PATH])} kept)\n" in text
    assert "    created      jobDefinition Start Here  (id " in text
    assert "    8 entries: 4 equal, 3 updated, 1 created.\n" in text
    assert viya.folder_id(CONTENT_ROOT) is None and {**viya.ids(ROOT2), **mine} == viya.ids(ROOT2)
    # the root that was named is the root the job reads
    start, _ = viya.jobs[viya.at(f"{ROOT2}/Start Here")[0]]
    assert {p["name"]: p["defaultValue"] for p in start["parameters"]}["contentRoot"] == ROOT2
    # no report was imported: the report of the site was not read, not written, and nothing was discovered
    assert asked == [] and fake.read == [] and fake.written == [] and _captured_files(tmp_path) == []
    assert "No Builder report was imported - the options of a report on this site are left as they are." in text
    assert "Done." in text


def test_a_report_is_not_imported_into_a_root_the_package_does_not_come_from(importing, no_discovery, tmp_path):
    """The transfer service takes no target folder. With another content
    root the reports would land in the folder of the package - so they are
    not imported, the command says so, and the exit code says it too."""
    no_discovery()
    viya = importing.viya
    # a folder that is not there is not created without --create: nothing is written
    result, fake, text = importing(Site(**{"Prompt Builder": TUNED}), "--content-root", "/Team/Accelerator")
    assert result.exit_code == 1 and viya.wrote() == []
    assert "there is no folder /Team/Accelerator, and nothing was created: pass --create" in text
    result, fake, text = importing(Site(**{"Prompt Builder": TUNED}), "--content-root", "/Team/Accelerator",
                                   "--create")
    assert result.exit_code == 1
    assert importing.uploaded == {} and fake.read == [] and fake.written == [] and _captured_files(tmp_path) == []
    assert ("  Reports (Prompt Builder/Prompt Builder): NOT imported - the package was exported from "
            f"{CONTENT_ROOT} and its content goes to /Team/Accelerator: the transfer service imports a report "
            "into the folder it was exported from, and no mapping to another folder is known to work.\n") in text
    assert "--no-reports to bring everything but the reports" in text
    # the content was brought all the same
    assert "    7 entries: 7 created." in text and FILE_PATH in viya.tree("/Team/Accelerator")
    assert "then run:\nmdb builders-import --content-root /Team/Accelerator --no-releases --create\n" in text


def test_a_package_from_a_folder_below_others_never_reaches_the_transfer_service(importing, no_discovery):
    """Measured: such a package lands in a NEW folder at the root of SAS
    Content, named like its root, whatever folder it was meant for."""
    no_discovery()
    nested = _built(root="root1", location="/Users/me/My Folder/ZZ_VERIFY_R18").build()
    for root in (None, "/Users/me/My Folder/ZZ_VERIFY_R18/root1"):
        args = ["--content-root", root, "--create"] if root else []
        result, fake, text = importing(Site(), *args, package=nested)
        assert result.exit_code == 1, text
        assert importing.uploaded == {}
        assert ("NOT imported - the package was exported from /Users/me/My Folder/ZZ_VERIFY_R18/root1, a folder "
                "that is not at the root of SAS Content: the transfer service puts such a package into a new "
                f"folder at the root of SAS Content (measured), not into {root or CONTENT_ROOT}.") in text
    result, fake, text = importing(Site(), "--content-root", ROOT2, "--no-reports", "--create", package=nested)
    assert result.exit_code == 0 and importing.uploaded == {}
    # a root that does not say where it was exported from is not guessed either
    silent = json.loads(json.dumps(nested))
    del next(d for d in silent["transferDetails"]
             if d["transferObject"]["summary"]["name"] == "root1")["transferObject"]["contentSourceLocation"]
    result, fake, text = importing(Site(), package=silent)
    assert result.exit_code == 1 and importing.uploaded == {}
    assert "NOT imported - the package does not say which folder it was exported from." in text


def test_the_refusal_of_a_root_is_a_rule_and_guesses_nothing():
    root = "/SAS Agentic AI Accelerator"
    assert cli._transfer_refusal(root, root) is None
    assert "does not say" in cli._transfer_refusal(None, root)
    assert "not at the root of SAS Content" in cli._transfer_refusal("/Users/me/root1", "/Users/me/root1")
    assert "no mapping to another folder is known to work" in cli._transfer_refusal(root, "/Other Root")
    assert "no mapping to another folder is known to work" in cli._transfer_refusal(root, root.lower())


# ---- the tasks of the import job ----------------------------------------------------
def test_a_task_that_ended_with_a_warning_is_printed_and_fails_the_run(importing, tmp_path):
    """The job of the measured import ended `completed` with errorCount 0,
    and mdb said "imported 71 object(s)". Three of its tasks had ended
    completedWithWarnings: an object the service had written into no folder."""
    importing.tasks = [
        {"name": "SAS Agentic AI Accelerator", "resourceType": "folder", "state": "completed",
         "message": "The resource was successfully imported."},
        {"name": "Prompt Builder", "resourceType": "report", "state": "completedWithWarnings",
         "message": "\n/reports/reports/exported was updated but cannot be added to the intended folder\n"
                    "An item named \"Prompt Builder\" of type \"Report\" already exists in the folder "
                    "\"Prompt Builder\".\n"},
        {"name": "background.png", "resourceType": "application/vnd.sas.file+json", "state": "failed"}]
    result, fake, text = importing(Site(**{"Prompt Builder": TUNED}))
    assert result.exit_code == 1
    assert "    the import job ended completed, and 2 of its tasks did not:\n" in text
    assert ("      report 'Prompt Builder': completedWithWarnings - /reports/reports/exported was updated but "
            "cannot be added to the intended folder / An item named \"Prompt Builder\" of type \"Report\" already "
            "exists in the folder \"Prompt Builder\".\n") in text
    assert "      file 'background.png': failed\n" in text
    assert "through the transfer service.\n" not in text and "Done." not in text
    # the report the import was meant to bring is not known to be the one on the site: its options stay in the file
    assert "Prompt Builder: its package did not import - options not restored." in text
    assert fake.written == []
    kept, = _captured_files(tmp_path)
    assert f"then run:\nmdb builders-import --options {kept} --no-releases\n" in text
    # the content was still brought in place
    assert "    7 entries: 7 created." in text


def test_an_import_whose_tasks_cannot_be_read_is_not_called_done(importing, monkeypatch):
    def unreadable(session, job):
        raise RuntimeError("Reading the tasks of the job completed failed: HTTP 503")
    monkeypatch.setattr(transfer, "job_tasks", unreadable)
    result, fake, text = importing(Site())
    assert result.exit_code == 1 and "Reading the tasks of the job completed failed: HTTP 503" in text
    assert "through the transfer service.\n" not in text


# ---- the capture stops the command ---------------------------------------------------
def _unreadable(**reports) -> Site:
    fake = Site(**reports)
    fake.unreadable = {"Prompt Builder"}
    return fake


def test_a_report_that_cannot_be_read_stops_the_command_before_anything_is_imported(importing, no_discovery,
                                                                                    tmp_path):
    """The capture exists so that an import does not cost a site its options.
    A report that was there and could not be read was passed over: the
    import replaced it, discovery configured the new one, and the run ended
    `Done.` with exit code 0."""
    asked = no_discovery()
    viya = importing.viya
    result, fake, text = importing(_unreadable(**{"Prompt Builder": TUNED}))
    assert result.exit_code == 1
    assert "Prompt Builder: it is on this site and its content cannot be read (Reading the report content failed: " \
           "HTTP 503)." in text
    assert "Nothing was imported." in text and "--options <file>" in text and "--no-options-capture" in text
    # nothing: no upload, no import, no write in place, no file, no discovery, the report as it was
    assert importing.uploaded == {} and importing.imports == {} and viya.wrote() == [] and viya.folders == {}
    assert asked == [] and fake.written == [] and fake.options("Prompt Builder") == TUNED
    assert _captured_files(tmp_path) == [] and "Done." not in text and "imported" not in text.replace(
        "Nothing was imported.", "")
    # a dry run stops the same way: it says what a run would do
    result, fake, text = importing(_unreadable(**{"Prompt Builder": TUNED}), "--dry-run")
    assert result.exit_code == 1 and "Nothing was imported." in text
    # the retired report holds options too
    fake = Site(**{"Prompt Builder": TUNED, "RAG Builder": OLD_RAG})
    fake.unreadable = {"RAG Builder"}
    result, fake, text = importing(fake)
    assert result.exit_code == 1 and "RAG Builder: it is on this site and its content cannot be read" in text
    # and a service that does not say whether the report is there is not "no report"
    fake = Site()
    fake.unanswered = {"Prompt Builder"}
    result, fake, text = importing(fake)
    assert result.exit_code == 1 and importing.uploaded == {}
    assert "Prompt Builder: whether it is on this site could not be asked (Looking up the report 'Prompt Builder' " \
           "failed: HTTP 503)." in text


def test_the_two_ways_out_of_a_report_that_cannot_be_read(importing, tmp_path):
    options = tmp_path / "builder-options.json"
    options.write_text(json.dumps({"version": 1, "reports": {"Prompt Builder": {"llmProjectID": "llm-file"}}}),
                       encoding="utf-8")
    # the options are named: the report on the site is not read for them before the import
    stuck = _unreadable(**{"Prompt Builder": TUNED})
    result, fake, text = importing(stuck, "--options", str(options))
    assert result.exit_code == 0, text
    assert fake.options("Prompt Builder")["llmProjectID"] == "llm-file"
    # or the import is asked to go on without them: the new report is configured from this deployment
    result, fake, text = importing(_unreadable(**{"Prompt Builder": TUNED}), "--no-options-capture")
    assert result.exit_code == 0, text
    assert "Options: not read from the report on this site (--no-options-capture)." in text
    assert fake.options("Prompt Builder")["llmProjectID"] == "llm-disc" and _captured_files(tmp_path) == []
    # a package that brings no report replaces none: nothing is read, and nothing stops
    result, fake, text = importing(_unreadable(**{"Prompt Builder": TUNED}), without_report=True)
    assert result.exit_code == 0, text
    # and options that are named for a report that still cannot be read are a line, not a traceback
    result, fake, text = importing(_unreadable(**{"Prompt Builder": TUNED}), "--options", str(options),
                                   without_report=True)
    assert result.exit_code == 1 and result.exception.__class__ is SystemExit
    assert "Prompt Builder: reading the report failed (Reading the report content failed: HTTP 503) - its options " \
           "were not written." in text


# ---- the compute context ---------------------------------------------------------------
def _context(viya, root=CONTENT_ROOT) -> str:
    page, _etag = viya.jobs[viya.at(f"{root}/{UI_PATH}")[0]]
    return {p["name"]: p["defaultValue"] for p in page["parameters"]}["_contextName"]


def test_a_compute_context_the_site_set_survives_the_import(importing, no_discovery):
    """The import handed the ingestion compute context of the options it
    restores to every job definition with `_contextName` as a GIVEN value, so
    the context a site set for the Prompt Builder page (and for Start Here)
    was replaced on every upgrade - the shipped report holds one. An existing
    value is left alone; only --compute-context writes over it."""
    no_discovery()
    viya = importing.viya
    _site_with_its_own_objects(viya)
    assert _context(viya) == "Team compute context"
    options = {**TUNED, "ragComputeContext": "RAG compute context"}
    result, fake, text = importing(Site(**{"Prompt Builder": options}))
    assert result.exit_code == 0, text
    assert ("Compute context: RAG compute context (the ingestion compute context of the options) - given to a job "
            "definition this run creates; one the site has keeps its _contextName.") in text
    assert _context(viya) == "Team compute context"
    assert f"    updated      jobDefinition {UI_PATH}  (form; id " in text
    # the one that is given is written, into every definition that has the parameter
    result, fake, text = importing(Site(**{"Prompt Builder": options}), "--compute-context", "Given context")
    assert result.exit_code == 0, text
    assert ("Compute context: Given context (--compute-context) - written into _contextName of every job definition "
            "that has it.") in text and _context(viya) == "Given context"
    # and stays when the next import is not given one
    result, fake, text = importing(Site(**{"Prompt Builder": options}))
    assert result.exit_code == 0 and _context(viya) == "Given context"


def test_a_job_definition_the_import_creates_gets_the_context_of_the_options(importing, no_discovery):
    """Start Here without `_contextName` ran in the SAS Job Execution compute
    context, under a service account, and checked nothing. A definition the
    site does not have yet gets the context the options know runs as the
    user: the ingestion compute context."""
    no_discovery()
    viya = importing.viya
    built = _built()
    built.job("Start Here", START_JOB)
    result, fake, text = importing(Site(**{"Prompt Builder": {**TUNED, "ragComputeContext": "RAG compute context"}}),
                                   package=built.build())
    assert result.exit_code == 0, text
    start, _ = viya.jobs[viya.at(f"{CONTENT_ROOT}/Start Here")[0]]
    assert {p["name"]: p["defaultValue"] for p in start["parameters"]}["_contextName"] == "RAG compute context"
    assert _context(viya) == "RAG compute context"
    # a retired report's computeContext WAS the ingestion context: folded in, it counts for a new definition
    other = Viya()
    importing.viya.__dict__.update(other.__dict__)
    result, fake, text = importing(Site(**{"RAG Builder": {**OLD_RAG, "computeContext": "Old RAG context"}}))
    assert result.exit_code == 0, text
    assert _context(viya) == "Old RAG context"


def test_the_content_root_a_job_reads_is_the_folder_the_content_goes_to(importing, no_discovery):
    """review-18: a package exported from another root with
    --allow-differences carries that root in Start Here; imported into the
    default root, Start Here was created checking the exporter's folder."""
    no_discovery()
    viya = importing.viya
    built = _built(report=False)
    built.job("Start Here", {**START_JOB, "parameters": [
        START_JOB["parameters"][0], {**START_JOB["parameters"][1], "defaultValue": "/Users/x/My Folder/ZZ/root1"}]})
    result, fake, text = importing(Site(), package=built.build())
    assert result.exit_code == 0, text
    start, _ = viya.jobs[viya.at(f"{CONTENT_ROOT}/Start Here")[0]]
    assert {p["name"]: p["defaultValue"] for p in start["parameters"]}["contentRoot"] == CONTENT_ROOT


def test_a_job_definition_the_checkout_does_not_list_keeps_its_context(importing, no_discovery):
    """review-18: Start Here and Load-Accelerator-Tables, which the manifest of
    the importer's checkout did not list, lost a hand-set `_contextName` in a
    plain import. The compute context and the content root are site values
    wherever a packaged definition has them."""
    no_discovery()
    viya = importing.viya
    viya.put_member(f"{CONTENT_ROOT}/Start Here", "/jobDefinitions/definitions/", {
        "name": "Start Here", **START_JOB, "parameters": [
            {**START_JOB["parameters"][0], "defaultValue": "Site context"}, START_JOB["parameters"][1]]})
    built = _built()
    built.job("Start Here", START_JOB)
    result, fake, text = importing(Site(**{"Prompt Builder": NO_CONTEXT}), package=built.build())
    assert result.exit_code == 0, text
    start, _ = viya.jobs[viya.at(f"{CONTENT_ROOT}/Start Here")[0]]
    assert {p["name"]: p["defaultValue"] for p in start["parameters"]} == {
        "_contextName": "Site context", "contentRoot": CONTENT_ROOT}
    assert "    equal        jobDefinition Start Here  (id " in text


def test_the_context_of_the_optimization_job_is_not_taken_for_it(importing, no_discovery):
    """`computeContext` is where the optimization job runs - commonly the
    SAS Job Execution compute context, the very context Start Here checked
    nothing in. It is never written into a job definition."""
    no_discovery()
    viya = importing.viya
    _site_with_its_own_objects(viya)
    options = {**TUNED, "computeContext": "SAS Job Execution compute context", "ragComputeContext": ""}
    result, fake, text = importing(Site(**{"Prompt Builder": options}))
    assert result.exit_code == 0, text
    assert "Compute context:" not in text and _context(viya) == "Team compute context"
    assert cli._known_context({"Prompt Builder": options}) == ""
    assert cli._known_context({"Prompt Builder": {"ragComputeContext": " X "}}) == "X"
    assert cli._known_context(None) == "" and cli._known_context({}) == ""
    # only what was given is written: nothing, or the context and the root
    assert cli._deploy_settings(None, "") == {} and cli._deploy_settings("/Root/", " C ") == {
        "_contextName": "C", "contentRoot": "/Root"}


# ---- a dry run asks, and nothing else ----------------------------------------------------
#: Every request a dry run may make, as the command's own text lists them.
DRY_RUN_REQUESTS = (
    r"/casManagement/servers\?limit=100", r"/reports/reports", r"/reports/reports/[^/]+/content",
    r"/folders/folders/@item", r"/folders/folders/[^/]+/members", r"/files/files/[^/]+/content",
    r"/dataFlows/steps/[^/]+", r"/jobDefinitions/definitions/[^/]+")


def test_a_dry_run_makes_get_requests_only(importing, no_discovery, monkeypatch, tmp_path):
    """The dry run uploaded the package to the server and deleted it again:
    two writes, to read a mapping. It now reads the mapping off the package.
    Run here against a SAS Viya that answers every request itself - reports,
    CAS servers, folders, files, steps, job definitions - so that every
    request the command makes is one this test sees."""
    import re
    asked = no_discovery()
    viya = importing.viya
    mine = _site_with_its_own_objects(viya)
    viya.reports = {"Prompt Builder": _xml(TUNED), "RAG Builder": _xml(OLD_RAG)}
    before = json.dumps([viya.reports, viya.steps, {k: v.decode() for k, v in viya.files.items()},
                         [body for body, _ in viya.jobs.values()], viya.tree(CONTENT_ROOT)], sort_keys=True)
    folder = tmp_path / "SAS-Viya-Integrations"
    (folder / PACKAGE_FILE).write_text(json.dumps(_built().build()), encoding="utf-8")
    for name in ("upload_package_bytes", "upload_package", "start_import", "get_mapping", "delete_package",
                 "start_export"):
        monkeypatch.setattr(transfer, name, _never(name))
    viya.calls.clear()
    result = CliRunner().invoke(cli.app, ["builders-import", "--dry-run", "--report-caslib", "Monitoring"])
    text = cli.console.file.getvalue()
    assert result.exit_code == 0, text
    # every request is a GET, and one of those the command names
    assert {method for method, _path, _params in viya.calls} == {"GET"}
    for _method, path, _params in viya.calls:
        assert any(re.fullmatch(pattern, path) for pattern in DRY_RUN_REQUESTS), path
    assert {next(p for p in DRY_RUN_REQUESTS if re.fullmatch(p, path)) for _m, path, _p in viya.calls} == \
        set(DRY_RUN_REQUESTS)
    # nothing changed anywhere, and nothing was left behind
    assert json.dumps([viya.reports, viya.steps, {k: v.decode() for k, v in viya.files.items()},
                       [body for body, _ in viya.jobs.values()], viya.tree(CONTENT_ROOT)], sort_keys=True) == before
    assert asked == [] and _captured_files(tmp_path) == []
    assert not (tmp_path / "sas-viya-cli-content-commands.txt").exists()
    # and it says what a run would do
    assert "Release table: would load Public.ACCELERATOR_RELEASES on cas-shared-default." in text
    assert "Options: read from the report on this site; a run keeps them in a file and writes them back" in text
    assert "  Reports, through the transfer service (2 folder, 1 report): Prompt Builder/Prompt Builder\n" in text
    assert "    mapping: Prompt Builder: VisualElement:ve9 -> host site.example\n" in text
    assert f"    updated      step          {STEP_PATH}  (templates; id {_id(mine[STEP_PATH])} kept)\n" in text
    assert "    7 entries: 4 equal, 3 updated.\n" in text
    assert text.count("dry run - nothing imported.") == 1 and "Done." not in text
    assert "A report named 'RAG Builder' still exists on this site." in text


def test_the_command_to_repeat_carries_the_new_options():
    assert cli._import_command(None, None, None, None, None, True, cli.IMPORT_TIMEOUT, None, "/Team/Accelerator",
                               "Team compute context", True, True) == (
        'mdb builders-import --content-root /Team/Accelerator --compute-context "Team compute context" '
        "--no-releases --no-reports --no-options-capture")
    # with an options file in the line nothing is captured anyway
    assert cli._import_command(None, Path("o.json"), None, None, None, False, cli.IMPORT_TIMEOUT,
                               no_capture=True) == "mdb builders-import --options o.json"
