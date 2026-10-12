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

from mdb import cli
from mdb.core.options import read_options
from mdb.core.packages import deflate
from mdb.viya import cas, registry, reports, transfer


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
        def find_report(session, name):
            fake.looked_up.append(name)
            return {"id": name, "name": name} if name in fake.reports else None

        def get_content(session, report_id):
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
MANIFEST = """version: 1
contentRoot: /SAS Agentic AI Accelerator
package: SAS-Viya-Integrations/SAS-Agentic-AI-Accelerator.json
entries:
  - {path: Prompt Builder/Prompt Builder, kind: report, checks: {optionSet: prompt-builder}}
  - {path: RAG/rag_core/__init__.py, kind: file, source: not/in/this/checkout.py}
"""


def _report_detail(name: str, options: dict) -> dict:
    wrapper = {"object": {"name": name},
               "transferableContent": {"content": _xml(options), "contentVersion": 1, "reportStates": None}}
    return {"transferObject": {"summary": {"type": "report", "name": name},
                               "content": deflate(json.dumps(wrapper, separators=(",", ":")))}}


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
    """`mdb builders-import` against a fake transfer service. Returns a
    function that writes the package into a fake checkout, runs the command
    there and gives back (result, site, printed text). `run.imports` holds
    the mapping each import was started with, by package name."""
    from mdb.viya import content as viya_content
    discovery({"llm": "llm-disc", "embedding": "emb-disc"})
    folder = tmp_path / "SAS-Viya-Integrations"
    folder.mkdir()
    # only the list is read: an importer's checkout need not hold every source
    (folder / "package-manifest.yaml").write_text(MANIFEST, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SAS_VIYA_URL", "https://site.example")
    monkeypatch.setattr(cli, "Context", lambda: SimpleNamespace(repo=tmp_path, core=None))

    class _Session:
        hostname = "site.example"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(cli, "_viya_session", lambda: _Session())
    monkeypatch.setattr(cas, "resolve_server", lambda session, server: "cas-shared-default")
    monkeypatch.setattr(transfer, "upload_package", lambda session, path: {"id": Path(path).name})
    monkeypatch.setattr(transfer, "get_mapping", lambda session, package_id: _mapping())
    monkeypatch.setattr(transfer, "delete_package", lambda session, uri: None)
    monkeypatch.setattr(transfer, "job_messages", lambda session, job: ["the folder is locked"])
    monkeypatch.setattr(transfer, "wait_for_job", lambda session, uri, timeout=600: {"state": uri})
    monkeypatch.setattr(viya_content, "find_folder", lambda session, path: {"id": "content-folder-1", "name": path})

    def run(fake: Site, *args, fails=False, extra=(), without_report=False):
        fake = site(fake)
        details = [_other("folder", "SAS Agentic AI Accelerator"), _other("folder", "Prompt Builder"),
                   _other("jobDefinition", "SAS-LLM-Prompt-Builder-UI"), _other("flowStep", "RAG - Embed Chunks.step")]
        if not without_report:
            details.append(_report_detail("Prompt Builder", NEW_DEFAULTS))
        package = {"name": "SAS Agentic AI Accelerator", "transferDetails": details}
        (folder / PACKAGE_FILE).write_text(json.dumps(package), encoding="utf-8")
        named = {"SAS Agentic AI Accelerator": package}
        for path, other in extra:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(json.dumps(other), encoding="utf-8")
            named[other["name"]] = other
        failing = {fails} if isinstance(fails, str) else ({"SAS Agentic AI Accelerator"} if fails else set())
        run.imports = {}

        def start_import(session, name, package_uri, mapping=None, description=""):
            run.imports[name] = mapping
            # an import that works puts the package's reports on the site
            if name not in failing:
                for detail in named[name]["transferDetails"]:
                    summary = detail["transferObject"]["summary"]
                    if summary["type"] == "report":
                        fake.reports[summary["name"]] = _xml(
                            NEW_DEFAULTS if summary["name"] == "Prompt Builder" else OLD_RAG)
            return {"links": [{"rel": "self", "href": "failed" if name in failing else "completed"}]}

        monkeypatch.setattr(transfer, "start_import", start_import)
        result = CliRunner().invoke(cli.app, ["builders-import", "--no-releases", *args])
        return result, fake, out.getvalue()
    return run


def _captured_files(tmp_path) -> list:
    return sorted(path.name for path in tmp_path.glob("builder-options-captured-*.json"))


def test_import_restores_a_2_2_file_into_the_merged_report(importing, tmp_path):
    options = tmp_path / "builder-options.json"
    options.write_text(json.dumps({"version": 1, "reports": SAVED_2_2}), encoding="utf-8")
    result, fake, text = importing(Site(**{"Prompt Builder": OLD_PROMPT, "RAG Builder": OLD_RAG}),
                                   "--options", str(options))
    assert result.exit_code == 0, text
    assert f"{PACKAGE_FILE} - SAS Agentic AI Accelerator: 2 folder, 1 jobDefinition, 1 flowStep, 1 report" in text
    assert "imported 5 object(s)." in text
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
            f"({PACKAGE_FILE} did not import - this imports again and writes the options back") in text
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
    assert "imported 4 object(s)" in text
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
    assert commands[0].startswith("sas-viya authorization create-rule -o /folders/folders/content-folder-1 ")
    assert commands[1].startswith(
        "sas-viya authorization create-rule --container-uri /folders/folders/content-folder-1 ")
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
        extra=[(old, {"name": "RAG Builder", "transferDetails": [
            _other("folder", "RAG"), _other("jobDefinition", "RAG-Builder-UI"),
            _report_detail("RAG Builder", OLD_RAG)]})])
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
