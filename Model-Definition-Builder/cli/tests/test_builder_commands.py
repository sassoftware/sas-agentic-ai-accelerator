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
  * one package that fails to import leaving the other one's report on
    package defaults;
  * a dry run burying its answer under a line per option the new report adds.
"""
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console
from typer.testing import CliRunner

from mdb import cli
from mdb.core.options import read_options
from mdb.core.packages import (LEGACY_RAG_PACKAGE, PROMPT_BUILDER_PACKAGE, RAG_RUNTIME_PACKAGE,
                               deflate)
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
                "enable_pgvector": "1"}


class Site:
    """The reports of a fake site, and everything that was done to them."""

    def __init__(self, **named):
        self.reports = {name: _xml(options) for name, options in named.items()}
        self.looked_up: list = []
        self.read: list = []
        self.written: list = []

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
    # what the file does not name keeps the package default
    assert restored["enableDecision"] == "true"
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
    cli._restore_options(None, json.loads(json.dumps(SAVED_2_2)), False)
    text = out.getvalue()
    for label in ("contentRoot", "ragComputeContext", "enableRag", "embeddingProjectID"):
        assert f"Prompt Builder: '{label}' is not an option of this report version - not written." in text
    assert fake.options("Prompt Builder")["computeContext"] == "SAS Job Execution compute context"


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
def _report_detail(name: str, options: dict) -> dict:
    wrapper = {"object": {"name": name},
               "transferableContent": {"content": _xml(options), "contentVersion": 1, "reportStates": None}}
    return {"transferObject": {"summary": {"type": "report", "name": name},
                               "content": deflate(json.dumps(wrapper, separators=(",", ":")))}}


def _other(kind: str, name: str) -> dict:
    return {"transferObject": {"summary": {"type": kind, "name": name}, "content": ""}}


@pytest.fixture
def importing(monkeypatch, tmp_path, site, discovery, out):
    """`mdb builders-import` against a fake transfer service. Returns a
    function that writes the two shipped packages into a fake checkout, runs
    the command and gives back (result, site, printed text)."""
    discovery({"llm": "llm-disc", "embedding": "emb-disc"})
    folder = tmp_path / "SAS-Viya-Integrations"
    folder.mkdir()
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
    monkeypatch.setattr(transfer, "get_mapping", lambda session, package_id: {})
    monkeypatch.setattr(transfer, "delete_package", lambda session, uri: None)
    monkeypatch.setattr(transfer, "job_messages", lambda session, job: ["the folder is locked"])
    monkeypatch.setattr(transfer, "wait_for_job", lambda session, uri, timeout=600: {"state": uri})

    def run(fake: Site, *args, runtime_name=RAG_RUNTIME_PACKAGE, runtime_fails=False, prompt_fails=False,
            runtime_objects=()):
        fake = site(fake)
        prompt = {"name": "Prompt Builder", "transferDetails": [
            _other("folder", "Prompt Builder"), _other("jobDefinition", "SAS-LLM-Prompt-Builder-UI"),
            _report_detail("Prompt Builder", NEW_DEFAULTS)]}
        runtime = {"name": "RAG Runtime", "transferDetails": [
            _other("folder", "RAG"), _other("flowStep", "RAG - Embed Chunks.step"), *runtime_objects]}
        (folder / PROMPT_BUILDER_PACKAGE).write_text(json.dumps(prompt), encoding="utf-8")
        (folder / runtime_name).write_text(json.dumps(runtime), encoding="utf-8")
        fails = {"Prompt Builder": prompt_fails, "RAG Runtime": runtime_fails}

        def start_import(session, name, package_uri, mapping=None, description=""):
            # an import that works puts the package's report on the site
            if not fails[name]:
                for detail in (prompt if name == "Prompt Builder" else runtime)["transferDetails"]:
                    summary = detail["transferObject"]["summary"]
                    if summary["type"] == "report":
                        fake.reports[summary["name"]] = _xml(
                            NEW_DEFAULTS if summary["name"] == "Prompt Builder" else OLD_RAG)
            return {"links": [{"rel": "self", "href": "failed" if fails[name] else "completed"}]}

        monkeypatch.setattr(transfer, "start_import", start_import)
        result = CliRunner().invoke(cli.app, ["builders-import", "--no-releases", *args])
        return result, fake, out.getvalue()
    return run


def test_import_restores_a_2_2_file_into_the_merged_report(importing, tmp_path):
    options = tmp_path / "builder-options.json"
    options.write_text(json.dumps({"version": 1, "reports": SAVED_2_2}), encoding="utf-8")
    result, fake, text = importing(Site(**{"Prompt Builder": OLD_PROMPT, "RAG Builder": OLD_RAG}),
                                   "--options", str(options))
    assert result.exit_code == 0, text
    # the Prompt Builder package first, then the runtime
    assert text.index(PROMPT_BUILDER_PACKAGE) < text.index(RAG_RUNTIME_PACKAGE)
    restored = fake.options("Prompt Builder")
    assert restored["enableRag"] == "true" and restored["contentRoot"] == "/Team/RAG"
    assert restored["ragComputeContext"] == "SAS Studio compute context"
    assert restored["llmProjectID"] == "llm-live"
    assert fake.written == ["Prompt Builder"]
    # the retired report is still on the site, untouched, and the command says so once
    assert fake.options("RAG Builder") == OLD_RAG
    assert text.count("A report named 'RAG Builder' still exists on this site.") == 1
    assert "This package predates 2.3.0" not in text
    assert "Done." in text and "Properties panel" not in text


def test_a_first_install_is_configured_from_discovery_with_rag_off(importing):
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


def test_a_failed_runtime_import_still_restores_the_report(importing, tmp_path):
    """The runtime package imports second. If it fails, the Prompt Builder
    report was already replaced - without the restore every user gets
    'Configuration required'."""
    options = tmp_path / "builder-options.json"
    options.write_text(json.dumps({"version": 1, "reports": SAVED_2_2}), encoding="utf-8")
    result, fake, text = importing(Site(**{"Prompt Builder": OLD_PROMPT}), "--options", str(options),
                                   runtime_fails=True)
    assert result.exit_code == 1
    assert "import ended failed" in text and "the folder is locked" in text
    assert fake.options("Prompt Builder")["llmProjectID"] == "llm-live"
    assert fake.options("Prompt Builder")["enableRag"] == "true"
    assert "Not everything was imported and configured." in text
    assert f"then run: mdb options-restore --file {options}" in text
    assert "Done." not in text


def test_a_failed_prompt_builder_import_leaves_the_old_report_alone(importing, tmp_path):
    options = tmp_path / "builder-options.json"
    options.write_text(json.dumps({"version": 1, "reports": SAVED_2_2}), encoding="utf-8")
    result, fake, text = importing(Site(**{"Prompt Builder": OLD_PROMPT}), "--options", str(options),
                                   prompt_fails=True)
    assert result.exit_code == 1
    assert fake.written == [] and fake.options("Prompt Builder") == OLD_PROMPT
    assert "Prompt Builder: its package did not import - options not restored." in text
    assert "not an option of this report version" not in text
    assert f"then run: mdb options-restore --file {options}" in text


def test_a_failed_first_install_discovers_nothing_and_says_how_to_go_on(importing, monkeypatch):
    def never(*_args, **_kwargs):
        raise AssertionError("discovery creates a missing project - it must not run for nothing")
    monkeypatch.setattr(registry, "ensure_repository_and_project", never)
    result, fake, text = importing(Site(), prompt_fails=True)
    assert result.exit_code == 1 and not isinstance(result.exception, AssertionError)
    assert fake.written == [] and "Prompt Builder" not in fake.reports
    assert "then run: mdb builders-import (it imports again and configures the report" in text


def test_a_package_from_before_the_merge_is_named_as_such(importing):
    """The fallback file name, with the standalone UI still inside: imported,
    because that is what the checkout ships, and said out loud."""
    result, fake, text = importing(
        Site(), runtime_name=LEGACY_RAG_PACKAGE,
        runtime_objects=(_other("jobDefinition", "RAG-Builder-UI"), _report_detail("RAG Builder", OLD_RAG)))
    assert result.exit_code == 0, text
    assert LEGACY_RAG_PACKAGE in text
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
    assert text.count("dry run - nothing imported.") == 2
    assert text.count("arrive with the imported report and are written then") == 1
    assert "is not an option of this report version" not in text
    assert "Done." not in text
