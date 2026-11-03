# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""mdb check: the verdict of each part of the accelerator, the lines of the compute server, and the page.

A user of 2.4.x opened Start Here in a compute context whose Python did not trust the certificate of
its SAS Viya: 20 lines said "the service did not answer" with the advice to look at the state of a
service that was well. And on a site that does not use RAG, the lines of the RAG runtime read missing
like the ones that matter. The check now runs in mdb, asks the compute server in a compute session of
its own, and says what each part needs.
"""
import re

import pytest

import start_here_harness as harness
from start_here_harness import ALL_PACKAGES, PARTS, Site, get_of, load, probe, run

OK, MISSING, NOT_CHECKED, NOT_USED = "ok", "missing", "not checked", "not used"


def summary_of(page):
    return re.search(r'<p class="summary">(.*?)</p>', page).group(1).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


def verdicts_of(page):
    """{part: (verdict, css class)} of the table by part."""
    return {part: (verdict, css) for part, css, verdict in
            re.findall(r'<tr><th scope="row">(.*?)</th><td class="verdict (\w+)">(.*?)</td>', page)}


def compute(lines):
    return {entry["what"]: entry for entry in lines if entry["section"] == "Compute server"}


# ---------------------------------------------------------------------------
# The compute server
# ---------------------------------------------------------------------------

def test_a_compute_server_that_does_not_trust_the_certificate_is_one_line_with_what_to_do(monkeypatch, tmp_path):
    """The case of the user who reported it."""
    done = run(monkeypatch, tmp_path, probes=[probe(viya={"status": None, "failure": "tls", "host": "https://viya.example.com"})])
    line = compute(done.lines)["SAS Studio compute context: SAS Viya from the compute server"]

    assert line["state"] == MISSING and line["part"] == "Prompt building"
    assert line["found"] == ("the Python of this compute context does not trust the TLS certificate of SAS Viya (the address the SAS "
                             "option SERVICESBASEURL names)")
    assert "Point SAS_VIYA_CABUNDLE at a file with the CA certificates of your site" in line["todo"]
    assert "SAS_VIYA_VERIFY_SSL=false" in line["todo"]
    assert line["todo"].endswith("/Administration-Guide/Troubleshooting-Guide#program-certificate-verify-failed")
    # prompt building is not ready, and no line says that a service did not answer
    assert verdicts_of(done.page)["Prompt building"] == ("1 to do", "missing")
    assert "did not answer" not in done.page


@pytest.mark.parametrize("failure, found, todo", [
    ("connect", "this compute context could not connect to SAS Viya (the address the SAS option SERVICESBASEURL names) - the name does "
     "not resolve from inside the cluster",
     "reach the address the SAS option SERVICESBASEURL names"),
    ("timeout", "SAS Viya (the address the SAS option SERVICESBASEURL names) did not answer this compute context in time",
     "check the way from the compute server"),
    ("other", "the request from this compute context ended without an answer", "The log of the compute session names the error."),
])
def test_a_host_that_cannot_be_reached_and_a_timeout_get_their_own_line(monkeypatch, tmp_path, failure, found, todo):
    lines = run(monkeypatch, tmp_path, probes=[probe(viya={"status": None, "failure": failure, "host": "https://viya.example.com"})]).lines
    line = compute(lines)["SAS Studio compute context: SAS Viya from the compute server"]

    assert line["found"].startswith(found) and todo in line["todo"] and "SAS_VIYA_CABUNDLE" not in line["todo"]


def test_a_compute_server_that_answers_is_ok_and_says_where(monkeypatch, tmp_path):
    lines = compute(run(monkeypatch, tmp_path).lines)

    assert lines["SAS Studio compute context: PROC PYTHON"]["state"] == OK
    assert lines["SAS Studio compute context: SAS Viya from the compute server"]["found"] == (
        "answered HTTP 401: the compute server reaches SAS Viya and trusts its certificate")


def test_a_compute_context_without_python_is_missing_and_its_packages_are_not_checked(monkeypatch, tmp_path):
    lines = run(monkeypatch, tmp_path, probes=[probe(python=False)]).lines

    python = compute(lines)["SAS Studio compute context: PROC PYTHON"]
    assert python["state"] == MISSING and python["found"] == "does not run in this compute context"
    assert "SAS Studio compute context: SAS Viya from the compute server" not in compute(lines)
    packages = [entry for entry in lines if entry["section"].startswith("Python packages: ")]
    assert packages and all(entry["state"] == NOT_CHECKED for entry in packages)
    assert packages[0]["found"] == "not looked at: no Python ran in SAS Studio compute context"


def test_a_session_that_could_not_be_started_is_not_checked_with_the_reason(monkeypatch, tmp_path):
    lines = run(monkeypatch, tmp_path, probes=[probe(started=False, python=False, error="HTTP 403: not allowed")]).lines

    line = compute(lines)["SAS Studio compute context: a compute session"]
    assert line["state"] == NOT_CHECKED and line["found"] == "no session could be started: HTTP 403: not allowed"


def test_rag_and_optimization_need_a_context_that_runs_as_you(monkeypatch, tmp_path):
    """Their jobs read and write as the person who starts them: measured on 2026-11-03, the SAS Job
    Execution compute context of the test system ran its sessions as another user."""
    done = run(monkeypatch, tmp_path, probes=[probe(context="SAS Job Execution compute context", sameUser=False)])
    lines = compute(done.lines)

    rag = lines["SAS Job Execution compute context: the user of its sessions (RAG)"]
    assert rag["state"] == MISSING and rag["part"] == "RAG" and "option ragComputeContext" in rag["todo"]
    assert lines["SAS Job Execution compute context: the user of its sessions (Prompt optimization)"]["part"] == "Prompt optimization"
    # prompt building and logging have no such line: their jobs may run as a service account
    assert not [what for what in lines if "(Prompt building)" in what or "(Logging" in what]
    assert verdicts_of(done.page)["RAG"] == ("1 to do", "missing")


def test_each_part_takes_its_packages_from_the_context_it_runs_in(monkeypatch, tmp_path):
    studio = probe(packages={"pandas": "2.2.3", "swat": "1.18.1", "requests": "2.32.3"}, parts=["Prompt building", "Logging and the LLM Usage Report"])
    rag = probe(context="RAG context", parts=["RAG"], packages=dict(ALL_PACKAGES, markitdown=None))
    optimize = probe(context="Optimize context", parts=["Prompt optimization"], packages={"dspy": "3.1.0", "requests": "2.32.3"})
    lines = run(monkeypatch, tmp_path, probes=[studio, rag, optimize]).lines
    by = {(entry["section"], entry["what"]): entry for entry in lines}

    assert by[("Python packages: Logging and Monitoring", "pandas")]["found"] == "version 2.2.3 in SAS Studio compute context"
    assert by[("Python packages: RAG", "markitdown")]["found"] == "not installed in RAG context"
    assert by[("Python packages: Prompt optimization", "dspy 3.2.1 or later")]["found"] == "version 3.1.0 in Optimize context"
    assert by[("Python packages: Prompt optimization", "optuna 3.0 or later")]["state"] == MISSING
    assert "installed into the Python of the compute context Optimize context" in by[("Python packages: Prompt optimization", "optuna 3.0 or later")]["todo"]


def test_the_contexts_to_probe_are_one_per_context_the_parts_run_in(monkeypatch, tmp_path):
    plan = load(monkeypatch, tmp_path).namespace["contexts_to_probe"]
    settings = dict(harness.DEFAULT_SETTINGS)

    assert plan(None, settings) == [("SAS Studio compute context", ["Prompt building", "Logging and the LLM Usage Report", "RAG",
                                                                    "Prompt optimization"])]
    options = {"enableRag": "true", "ragComputeContext": "RAG context", "enableOptimization": "true", "computeContext": "Optimize context"}
    assert plan(options, settings) == [("SAS Studio compute context", ["Prompt building", "Logging and the LLM Usage Report"]),
                                       ("RAG context", ["RAG"]), ("Optimize context", ["Prompt optimization"])]
    # a part switched off is not probed, and --compute-context names the first context
    assert plan(dict(options, enableOptimization="false"), settings, "Mine") == [
        ("Mine", ["Prompt building", "Logging and the LLM Usage Report"]), ("RAG context", ["RAG"])]
    assert plan(None, dict(settings, computeContexts="First|Second")) [0][0] == "First"


# ---------------------------------------------------------------------------
# The parts
# ---------------------------------------------------------------------------

def test_a_complete_site_is_ready_in_every_part(monkeypatch, tmp_path):
    page = run(monkeypatch, tmp_path).page

    assert verdicts_of(page) == {part: ("ready", "ok") for part in PARTS}
    assert "not used" not in summary_of(page)


def test_a_site_without_rag_and_optimization_has_nothing_to_do_there(monkeypatch, tmp_path):
    site = Site()
    site.options.update(enableRag="false", enableOptimization="false")
    site.projects = site.projects[:1] + site.projects[2:]
    packages = {name: version for name, version in ALL_PACKAGES.items()
                if name not in ("psycopg2", "psycopg2-binary", "singlestoredb", "pypdfium2", "markitdown", "dspy", "optuna")}
    done = run(monkeypatch, tmp_path, site, packages=packages)

    verdicts = verdicts_of(done.page)
    assert verdicts["RAG"] == ("not used here", "unused") and verdicts["Prompt optimization"] == ("not used here", "unused")
    assert verdicts["Prompt building"] == ("ready", "ok") and verdicts["Logging and the LLM Usage Report"] == ("ready", "ok")
    unused = {entry["what"]: entry for entry in done.lines if entry["state"] == NOT_USED}
    assert set(unused) == {"psycopg2-binary", "singlestoredb", "pypdfium2", "markitdown", "dspy 3.2.1 or later", "optuna 3.0 or later",
                           "Project Embedding Model Project (embedding model definitions)",
                           # an option that is set is checked with RAG off as well, and it names no project that is there
                           "embeddingProjectID"}
    assert unused["dspy 3.2.1 or later"]["todo"] == ("Nothing to do: the option enableOptimization of the Prompt Builder switches "
                                                     "Prompt optimization off on this site.")
    assert not [entry for entry in done.lines if entry["part"] in ("RAG", "Prompt optimization") and entry["state"] == MISSING]


def test_a_backend_that_is_not_offered_needs_no_driver(monkeypatch, tmp_path):
    site = Site()
    site.options.update(enable_singlestore="false")
    lines = run(monkeypatch, tmp_path, site, packages=dict(ALL_PACKAGES, singlestoredb=None)).lines

    driver = next(entry for entry in lines if entry["what"] == "singlestoredb")
    assert driver["state"] == NOT_USED and "does not offer SingleStore" in driver["todo"]
    assert next(entry for entry in lines if entry["what"] == "psycopg2-binary")["state"] == OK


def test_decision_models_switched_off_need_no_project_with_models(monkeypatch, tmp_path):
    site = Site()
    site.models[site.projects[2]["id"]] = 0
    (tmp_path / "on").mkdir()
    (tmp_path / "off").mkdir()
    on = run(monkeypatch, tmp_path / "on", site).page
    site.options["enableDecision"] = "false"
    off = run(monkeypatch, tmp_path / "off", site).page

    assert verdicts_of(on)["Decision models"] == ("1 to do", "missing")
    assert verdicts_of(off)["Decision models"] == ("not used here", "unused")


def test_where_the_options_cannot_be_read_every_part_counts_as_used(monkeypatch, tmp_path):
    functions = load(monkeypatch, tmp_path).namespace

    assert functions["parts_in_use"](None) == {"Prompt building": True, "Logging and the LLM Usage Report": True, "RAG": None,
                                               "Prompt optimization": None, "Decision models": None}
    lines = functions["with_parts"]([functions["line"]("Python packages: RAG", "markitdown", MISSING, "not installed")], None)
    assert lines[0]["state"] == MISSING and lines[0]["used"] is True


def test_every_line_has_one_of_the_parts(monkeypatch, tmp_path):
    parts = {entry["what"]: entry["part"] for entry in run(monkeypatch, tmp_path).lines}

    assert set(parts.values()) == set(PARTS)
    assert parts["release.json"] == "Prompt building"
    assert parts["Public.ACCELERATOR_RELEASES (Prompt Builder)"] == "Prompt building"
    assert parts["Public.LLM_LOGS (LLM Usage Report)"] == "Logging and the LLM Usage Report"
    assert parts["Public.MODEL_CATALOG (Link-Logs-To-Models.sas)"] == "Logging and the LLM Usage Report"
    assert parts["Project Embedding Model Project (embedding model definitions)"] == "RAG"
    assert parts["Project Decision Model Project (decision model definitions)"] == "Decision models"
    assert parts["embeddingProjectID"] == "RAG" and parts["llmProjectID"] == "Prompt building"
    assert parts["SAS Studio compute context: SAS Viya from the compute server"] == "Prompt building"
    assert parts["SAS Studio compute context: the user of its sessions (RAG)"] == "RAG"


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

def test_the_page_says_when_it_was_checked_and_how_to_bring_it_up_to_date(monkeypatch, tmp_path):
    page = run(monkeypatch, tmp_path, checked={"when": "2026-11-03 14:12 UTC", "by": "mdb check 2.4.3"}).page

    assert ("Last checked on 2026-11-03 14:12 UTC by mdb check 2.4.3, for the content root /SAS Agentic AI Accelerator. Run mdb check "
            "again to bring this page up to date") in page
    assert "<h3>By part</h3>" in page


def test_the_page_holds_no_two_braces_side_by_side(monkeypatch, tmp_path):
    """SAS Job Execution reads a form as a Go template: two braces open one of its actions. A text of the site
    that holds them - a folder name, an answer - is written with entities."""
    done = run(monkeypatch, tmp_path, settings={"caslib": "Lib{{.Secret}}"})

    assert "{{" not in done.page and "}}" not in done.page
    assert "Lib&#123;&#123;.Secret&#125;&#125;.LLM_LOGS" in done.page


def test_the_page_the_release_ships_is_the_placeholder_of_the_module(monkeypatch, tmp_path):
    functions = load(monkeypatch, tmp_path).namespace
    shipped = (harness.REPOSITORY / "SAS-Viya-Integrations" / "Setup" / "Start-Here.html").read_text(encoding="utf-8")

    assert shipped.replace("\r\n", "\n") == functions["placeholder_page"]()
    assert "This installation has not been checked yet." in shipped and "<pre>mdb check</pre>" in shipped
    assert "<script" not in shipped and "{{" not in shipped
