"""Start-Here.sas: the page a site opens first, and its installation check.

The Python of the program runs here against a SAS Viya system that is made up (start_here_harness.Site):
a complete installation, and then one thing taken out of it at a time, a right that is lacking, a
service that is down, a release.json that is empty and an entry of it that is not in the folder.
The SAS code around it can not run here; the tests read it as text.

Three things hold for every page, whatever the system looks like: the program asks with GET
and nothing else, the page holds no secret, no token and no user id, and every text of it is
escaped.
"""
import ast
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

import start_here_harness as harness
from start_here_harness import ALL_PACKAGES, ENTRIES, ROOT, Site, get_of, load, run

REPOSITORY = Path(__file__).resolve().parents[3]
OK, MISSING, NOT_CHECKED = "ok", "missing", "not checked"


def lines_of(monkeypatch, tmp_path, site=None, settings=None, packages=ALL_PACKAGES):
    """The lines of the installation check for a site, and the uris of its two reports."""
    loaded = load(monkeypatch, tmp_path, site, settings, packages=packages)
    all_settings = dict(harness.DEFAULT_SETTINGS, **(settings or {}))
    return loaded.namespace["check_installation"](get_of(loaded.site), all_settings)


def states(lines, section=None):
    return {entry["what"]: entry["state"] for entry in lines if section is None or entry["section"] == section}


def one(lines, what):
    found = [entry for entry in lines if entry["what"] == what]
    assert len(found) == 1, (what, [entry["what"] for entry in lines])
    return found[0]


# ---------------------------------------------------------------------------
# A complete installation
# ---------------------------------------------------------------------------

def test_a_complete_installation_is_ok_in_every_line(monkeypatch, tmp_path):
    site = Site()
    lines, reports = lines_of(monkeypatch, tmp_path, site)

    assert {entry["state"] for entry in lines} == {OK}, [entry for entry in lines if entry["state"] != OK]
    assert [entry["section"] for entry in lines if entry["section"] != "Content"][:1] == ["Release"]
    assert one(lines, "release.json")["found"] == "release 2.3.0, commit 0123456789ab, of 2026-10-12"
    # every entry of release.json is a line of its own
    assert [entry["what"] for entry in lines if entry["section"] == "Content"] == [f"{path} ({kind})" for path, kind in ENTRIES]
    assert one(lines, "Public.ACCELERATOR_RELEASES (Prompt Builder)")["found"] == "100 rows, loaded 2026-10-07 16:12 UTC"
    assert one(lines, "Repository LLM Repository")["found"] == "found"
    assert one(lines, "Project LLM Model Project (LLM definitions)")["found"] == "found, 31 models"
    assert one(lines, "Project Decision Model Project (decision model definitions)")["found"] == "found, 5 models"
    assert states(lines, "Options of the Prompt Builder report") == {"modelRepositoryID": OK, "llmProjectID": OK, "embeddingProjectID": OK}
    assert one(lines, "Credential domain agentic-ai-keys")["found"] == "found"
    assert one(lines, "Publishing destinations")["found"] == "2: maslocal (microAnalyticService), llm-registry (privateDocker)"
    assert states(lines, "Compute contexts") == {
        "Optimize compute context (option computeContext of the Prompt Builder, for the prompt optimization job)": OK,
        "SAS Studio compute context (option ragComputeContext of the Prompt Builder, for the RAG ingestion job)": OK,
        "SAS Job Execution compute context (named by this job)": OK}
    assert reports == {"Prompt Builder": site.member("Prompt Builder/Prompt Builder")["uri"],
                       "LLM Usage Report": site.member("Logging and Monitoring/LLM Usage Report")["uri"]}
    # a line that is ok asks for nothing
    assert [entry["todo"] for entry in lines if entry["todo"]] == []


def test_the_sections_of_the_check_stand_in_one_order(monkeypatch, tmp_path):
    lines, _ = lines_of(monkeypatch, tmp_path)
    sections = []
    for entry in lines:
        if entry["section"] not in sections:
            sections.append(entry["section"])

    assert sections == ["Release", "Content", "CAS tables of the reports", "SAS Model Manager", "Options of the Prompt Builder report",
                        "Credentials", "Python packages: Logging and Monitoring", "Python packages: RAG",
                        "Python packages: Prompt optimization", "Publishing destinations", "Compute contexts"]
    assert all(entry["state"] in (OK, MISSING, NOT_CHECKED) for entry in lines)


def test_a_listing_is_read_to_its_last_page(monkeypatch, tmp_path):
    """A folder with more members than a page holds, and services that hand out pages of two."""
    whole, _ = lines_of(monkeypatch, tmp_path, Site())
    paged_site = Site(page_size=2)
    paged, _ = lines_of(monkeypatch, tmp_path, paged_site)

    assert paged == whole
    members = [query for path, query in paged_site.requests if path.endswith("/members")]
    assert {query["start"] for query in members} >= {"0", "2", "4"} and {query["limit"] for query in members} == {"100"}


# ---------------------------------------------------------------------------
# One thing missing at a time
# ---------------------------------------------------------------------------

def test_another_content_root_is_a_line_and_the_other_checks_still_run(monkeypatch, tmp_path):
    lines, reports = lines_of(monkeypatch, tmp_path, settings={"contentRoot": "/Some Other Folder"})

    root = one(lines, "Content root")
    assert (root["state"], root["found"]) == (MISSING, "the folder was not found")
    assert "set the parameter contentRoot of this job" in root["todo"]
    assert [entry for entry in lines if entry["section"] == "Content"] == []
    assert reports == {"Prompt Builder": "", "LLM Usage Report": ""}
    # the options of the report can not be read without the report, everything else is checked as before
    assert states(lines, "Options of the Prompt Builder report") == {"Options": NOT_CHECKED}
    assert set(states(lines, "CAS tables of the reports").values()) == {OK}
    assert set(states(lines, "SAS Model Manager").values()) == {OK}


@pytest.mark.parametrize("text, found", [
    ("", "the file is empty"), ("   \n", "the file is empty"), ("{not json", "the file is empty or holds no JSON object"),
    ("[]", "the file is empty or holds no JSON object"), ('"2.3.0"', "the file is empty or holds no JSON object"),
], ids=["no byte", "blanks", "no JSON", "a list", "a text"])
def test_an_empty_or_unreadable_release_file_is_a_line(monkeypatch, tmp_path, text, found):
    site = Site()
    site.release_text = text
    lines, _ = lines_of(monkeypatch, tmp_path, site)

    release = one(lines, "release.json")
    assert (release["state"], release["found"]) == (MISSING, found)
    assert "mdb content-deploy" in release["todo"]
    # without the file no entry is known, so none is listed as missing
    assert [entry for entry in lines if entry["section"] == "Content"] == []
    assert set(states(lines, "CAS tables of the reports").values()) == {OK}


def test_a_release_file_without_a_version_or_without_entries_says_so(monkeypatch, tmp_path):
    site = Site()
    site.release_text = json.dumps({"commit": "abc", "entries": []})
    lines, _ = lines_of(monkeypatch, tmp_path, site)

    assert (one(lines, "release.json")["state"], one(lines, "release.json")["found"]) == (MISSING, "the file names no version, commit abc")
    entries = one(lines, "Entries of release.json")
    assert (entries["state"], entries["found"]) == (MISSING, "the file lists no entry")

    # entries that are no entries are passed over, and a path that is listed twice is one line
    site.release_text = json.dumps({"version": "2.3.0", "entries": [
        "a text", {"kind": "file"}, {"path": "", "kind": "file"}, {"path": 7}, {"path": "release.json", "kind": "file"},
        {"path": "/release.json/", "kind": "file"}, {"path": "Start Here"}]})
    lines, _ = lines_of(monkeypatch, tmp_path, site)
    assert states(lines, "Content") == {"release.json (file)": OK, "Start Here": OK}


def test_release_file_that_is_not_in_the_root_is_missing(monkeypatch, tmp_path):
    site = Site()
    site.remove("release.json")
    lines, _ = lines_of(monkeypatch, tmp_path, site)

    release = one(lines, "release.json")
    assert (release["state"], release["found"]) == (MISSING, "not in the content root")
    assert site.paths("/files/files") == []


@pytest.mark.parametrize("path, kind", [entry for entry in ENTRIES if entry[0] != "release.json"],
                         ids=[entry[0] for entry in ENTRIES if entry[0] != "release.json"])
def test_an_entry_that_is_not_in_the_folder_is_missing_and_every_other_one_is_present(monkeypatch, tmp_path, path, kind):
    site = Site()
    site.remove(path)
    lines, _ = lines_of(monkeypatch, tmp_path, site)
    content = states(lines, "Content")

    gone = {f"{other} ({other_kind})" for other, other_kind in ENTRIES if other == path or other.startswith(path + "/")}
    assert {what for what, state in content.items() if state == MISSING} == gone
    assert {what for what, state in content.items() if state == OK} == set(content) - gone
    entry = one(lines, f"{path} ({kind})")
    assert entry["found"] == "not in its folder" and "mdb content-deploy" in entry["todo"]
    for what in gone - {f"{path} ({kind})"}:
        assert one(lines, what)["found"] == "its folder is not in the content root"


def test_an_entry_of_another_kind_than_the_object_of_its_name_is_missing(monkeypatch, tmp_path):
    site = Site()
    site.remove("Logging and Monitoring/LLM - Log Parser.step")
    site.add("Logging and Monitoring/LLM - Log Parser.step", "file")
    site.remove("Prompt Builder/Prompt Builder")
    site.add("Prompt Builder/Prompt Builder", "folder")
    lines, reports = lines_of(monkeypatch, tmp_path, site)

    step = one(lines, "Logging and Monitoring/LLM - Log Parser.step (step)")
    assert (step["state"], step["found"]) == (MISSING, "a file of that name is there, and no registered step")
    assert "registers the step" in step["todo"]
    report = one(lines, "Prompt Builder/Prompt Builder (report)")
    assert (report["state"], report["found"]) == (MISSING, "the object of that name is a folder")
    assert reports["Prompt Builder"] == ""


def test_a_step_is_found_with_or_without_the_ending_of_its_file(monkeypatch, tmp_path):
    entries = [("release.json", "file"), ("Custom Steps/MM - Get All Repositories", "step"), ("Custom Steps/LLM - List Available LLMs.step", "step")]
    site = Site(entries=[("release.json", "file")])
    site.release["entries"] = [{"path": path, "kind": kind} for path, kind in entries]
    site.add("Custom Steps/MM - Get All Repositories.step", "step")
    site.add("Custom Steps/LLM - List Available LLMs", "step")
    lines, _ = lines_of(monkeypatch, tmp_path, site)

    assert set(states(lines, "Content").values()) == {OK}


@pytest.mark.parametrize("change, what, state, found, todo", [
    (lambda site: site.tables.pop("LLM_LOGS"), "Public.LLM_LOGS (LLM Usage Report)", MISSING, "not in the CAS library", "Log-Parser-Code.sas"),
    (lambda site: site.tables.pop("ACCELERATOR_RELEASES"), "Public.ACCELERATOR_RELEASES (Prompt Builder)", MISSING, "not in the CAS library",
     "Load-Accelerator-Tables"),
    (lambda site: site.tables["LLM_FACT_SHEET"].update(state="unloaded"), "Public.LLM_FACT_SHEET (LLM Usage Report)", MISSING,
     "saved in the CAS library and not in memory", "Load the table in SAS Environment Manager"),
    (lambda site: site.repositories.clear(), "Repository LLM Repository", MISSING, "no repository of that name", "Run mdb setup"),
    (lambda site: site.projects.pop(1), "Project Embedding Model Project (embedding model definitions)", MISSING,
     "no project of that name in the repository", "Run mdb setup"),
    (lambda site: site.domains.clear(), "Credential domain agentic-ai-keys", MISSING, "no credential domain of that name", "mdb credentials-apply"),
    (lambda site: site.destinations.clear(), "Publishing destinations", MISSING, "none is defined", "Configuration for Publishing"),
    (lambda site: site.contexts.pop(2), "Optimize compute context (option computeContext of the Prompt Builder, for the prompt optimization job)",
     MISSING, "no compute context of that name", "SAS Environment Manager"),
    (lambda site: site.options.update(llmProjectID=""), "llmProjectID", MISSING, "the option is empty", "Options pane"),
    (lambda site: site.options.update(modelRepositoryID="another-id"), "modelRepositoryID", MISSING,
     "names another object than the repository that was found by its name", "mdb builders-import"),
], ids=["no log table", "no release table", "a table that is saved and not loaded", "no repository", "no embedding project", "no credential domain",
        "no publishing destination", "no compute context of an option", "an option that is empty", "an option that names another repository"])
def test_one_thing_missing_is_one_line_with_what_to_do(monkeypatch, tmp_path, change, what, state, found, todo):
    site = Site()
    change(site)
    lines, _ = lines_of(monkeypatch, tmp_path, site)
    entry = one(lines, what)

    assert (entry["state"], entry["found"]) == (state, found)
    assert todo in entry["todo"] and entry["todo"].endswith(".")
    # nothing else of the page changed its state, but what depends on the thing that is missing
    depends = {"Repository LLM Repository": ("Project ", "modelRepositoryID", "llmProjectID", "embeddingProjectID"),
               "Project Embedding Model Project (embedding model definitions)": ("embeddingProjectID",)}.get(what, ())
    for other in lines:
        if other["what"] != what and not other["what"].startswith(depends):
            assert other["state"] == OK, other
    for other in lines:
        if other["what"] != what and other["what"].startswith(depends):
            assert other["state"] == NOT_CHECKED, other


def test_a_project_without_a_model_is_there_and_says_how_models_get_into_it(monkeypatch, tmp_path):
    site = Site()
    site.models[site.projects[2]["id"]] = 0
    lines, _ = lines_of(monkeypatch, tmp_path, site)
    entry = one(lines, "Project Decision Model Project (decision model definitions)")

    assert (entry["state"], entry["found"]) == (OK, "found, no model yet")
    assert entry["todo"] == "Register the decision model definitions you need with mdb register."


def test_a_cas_server_of_another_name_is_one_line_and_its_tables_are_not_checked(monkeypatch, tmp_path):
    lines, _ = lines_of(monkeypatch, tmp_path, settings={"casServer": "cas-of-the-site"})
    tables = states(lines, "CAS tables of the reports")

    server = one(lines, "CAS server cas-of-the-site")
    assert (server["state"], server["found"]) == (MISSING, "not among the CAS servers: cas-shared-default")
    assert {state for what, state in tables.items() if what != "CAS server cas-of-the-site"} == {NOT_CHECKED} and len(tables) == 6


def test_the_credential_domain_is_the_one_the_builder_option_names(monkeypatch, tmp_path):
    site = Site()
    site.options["credentialDomain"] = "keys-of-the-site"
    lines, _ = lines_of(monkeypatch, tmp_path, site)
    assert one(lines, "Credential domain keys-of-the-site")["state"] == MISSING

    site.domains.append("keys-of-the-site")
    lines, _ = lines_of(monkeypatch, tmp_path, site)
    assert one(lines, "Credential domain keys-of-the-site")["state"] == OK

    # the option says none: the Builder looks up no keys, the domain of the job is looked for and the line says so
    site.options["credentialDomain"] = "none"
    lines, _ = lines_of(monkeypatch, tmp_path, site)
    entry = one(lines, "Credential domain agentic-ai-keys")
    assert (entry["state"], entry["found"]) == (OK, "found; the option credentialDomain of the Prompt Builder says none")


def test_without_rag_the_embedding_project_is_no_option_that_is_missing(monkeypatch, tmp_path):
    site = Site()
    site.options.update(enableRag="false", embeddingProjectID="", ragComputeContext="")
    lines, _ = lines_of(monkeypatch, tmp_path, site)

    assert states(lines, "Options of the Prompt Builder report") == {"modelRepositoryID": OK, "llmProjectID": OK}
    assert not [entry for entry in lines if "ragComputeContext" in entry["what"]]


# ---------------------------------------------------------------------------
# A right that is lacking, a service that is down: a line, never a failed job
# ---------------------------------------------------------------------------

SERVICES = {
    "/folders/": ("Release", "Content root"),
    "/files/": ("Release", "release.json"),
    "/reports/": ("Options of the Prompt Builder report", "Options"),
    "/casManagement/": ("CAS tables of the reports", None),
    "/modelRepository/": ("SAS Model Manager", "Repository LLM Repository"),
    "/credentials/": ("Credentials", "Credential domain agentic-ai-keys"),
    "/modelPublish/": ("Publishing destinations", "Publishing destinations"),
    "/compute/": ("Compute contexts", None),
}


@pytest.mark.parametrize("status", [401, 403, 500, 503])
@pytest.mark.parametrize("prefix", list(SERVICES))
def test_a_service_that_refuses_or_fails_gives_lines_that_are_not_checked(monkeypatch, tmp_path, prefix, status):
    site = Site()
    site.refuse(prefix, status)
    section, what = SERVICES[prefix]
    lines, _ = lines_of(monkeypatch, tmp_path, site)      # nothing is raised
    of_section = [entry for entry in lines if entry["section"] == section and (what is None or entry["what"] == what)]

    assert of_section and {entry["state"] for entry in of_section} == {NOT_CHECKED}
    for entry in of_section:
        assert f"HTTP {status}" in entry["found"], entry
        if status in (401, 403):
            assert "may not be read by this session" in entry["found"]
            assert entry["todo"] == "Ask an administrator to open this page, or for the right to read it."
        else:
            assert "the service answered" in entry["found"] and entry["todo"].startswith("Open this page again.")
    # what the service does not decide is checked as before
    untouched = {"/folders/": (), "/files/": ("Content",)}.get(prefix, ("Content", "Release"))
    for entry in lines:
        if entry["section"] in untouched:
            assert entry["state"] == OK, entry


def test_a_folder_below_the_root_that_may_not_be_read_leaves_its_entries_unchecked(monkeypatch, tmp_path):
    site = Site()
    site.answers[f"/folders/folders/{site.folder_ids['RAG/rag_core']}/members"] = 403
    lines, _ = lines_of(monkeypatch, tmp_path, site)
    content = states(lines, "Content")

    unchecked = {"RAG/rag_core/adapters (folder)", "RAG/rag_core/__init__.py (file)", "RAG/rag_core/adapters/base.py (file)"}
    assert {what for what, state in content.items() if state == NOT_CHECKED} == unchecked
    assert {state for what, state in content.items() if what not in unchecked} == {OK}
    assert one(lines, "RAG/rag_core/__init__.py (file)")["found"] == "its folder may not be read by this session (HTTP 403)"


def test_a_request_that_gets_no_answer_is_a_line(monkeypatch, tmp_path):
    loaded = load(monkeypatch, tmp_path, packages=ALL_PACKAGES)

    def no_answer(path, accept="application/json"):
        raise ConnectionError("Max retries exceeded with url: https://viya.example.com" + path)

    lines, reports = loaded.namespace["check_installation"](no_answer, dict(harness.DEFAULT_SETTINGS))
    of_services = [entry for entry in lines if not entry["section"].startswith("Python packages")]

    assert {entry["state"] for entry in of_services} == {NOT_CHECKED}
    assert all("the service did not answer" in entry["found"] or "the repository is not known" in entry["found"] for entry in of_services), of_services
    assert len([entry for entry in of_services if "the service did not answer" in entry["found"]]) >= 12
    assert reports == {"Prompt Builder": "", "LLM Usage Report": ""}
    assert not [entry for entry in lines if "viya.example.com" in json.dumps(entry)]


def test_an_answer_that_is_no_json_is_a_line(monkeypatch, tmp_path):
    loaded = load(monkeypatch, tmp_path, packages=ALL_PACKAGES)

    def html_page(path, accept="application/json"):
        return 200, b"<html><body>Sign in</body></html>"

    lines, _ = loaded.namespace["check_installation"](html_page, dict(harness.DEFAULT_SETTINGS))
    assert one(lines, "Content root")["state"] == NOT_CHECKED and "the answer could not be read" in one(lines, "Content root")["found"]
    assert MISSING not in {entry["state"] for entry in lines if not entry["section"].startswith("Python packages")}


# ---------------------------------------------------------------------------
# The Python packages
# ---------------------------------------------------------------------------

def test_a_package_that_is_not_installed_or_too_old_is_missing_for_its_part(monkeypatch, tmp_path):
    packages = dict(ALL_PACKAGES, swat=None, psycopg2=None, optuna=None, dspy="3.1.9", requests="2.30.0")
    packages["psycopg2-binary"] = None
    lines, _ = lines_of(monkeypatch, tmp_path, packages=packages)

    logging = states(lines, "Python packages: Logging and Monitoring")
    assert logging == {"pandas": OK, "swat": MISSING, "requests": OK}
    rag = states(lines, "Python packages: RAG")
    assert rag == {"requests": OK, "pandas": OK, "swat": MISSING, "psycopg2-binary": MISSING, "singlestoredb": OK, "pypdfium2": OK,
                   "markitdown": OK}
    optimization = [entry for entry in lines if entry["section"] == "Python packages: Prompt optimization"]
    assert [(entry["what"], entry["state"], entry["found"]) for entry in optimization] == [
        ("dspy 3.2.1 or later", MISSING, "version 3.1.9"), ("requests 2.31 or later", MISSING, "version 2.30.0"),
        ("optuna 3.0 or later", MISSING, "not installed")]
    assert one(lines, "psycopg2-binary")["todo"] == ("Have psycopg2-binary installed into the Python of this compute context. "
                                                     "It is needed for the pgvector backend.")
    assert one(lines, "dspy 3.2.1 or later")["todo"] == ("Have dspy 3.2.1 or later installed into the Python of this compute context. "
                                                         "It is needed for every optimization.")
    assert one(lines, "markitdown")["found"] == "version 0.1.3"


@pytest.mark.parametrize("version, least, enough", [
    ("3.2.1", "3.2.1", True), ("3.2.10", "3.2.9", True), ("3.10.0", "3.9", True), ("4.0", "3.2.1", True), ("3.2", "3.2.0", True),
    ("3.2.0", "3.2.1", False), ("2.31.0", "2.31", True), ("2.9", "2.31", False), ("3.2.1rc1", "3.2.1", True), ("3.2.1+cpu", "3.2.1", True),
])
def test_versions_are_compared_by_their_numbers(monkeypatch, tmp_path, version, least, enough):
    assert load(monkeypatch, tmp_path).namespace["at_least"](version, least) is enough


def test_a_package_whose_version_is_not_given_is_installed_all_the_same(monkeypatch, tmp_path):
    functions = load(monkeypatch, tmp_path).namespace
    lines = functions["package_lines"](lambda module: True, lambda names: "")

    assert {entry["state"] for entry in lines} == {OK} and {entry["found"] for entry in lines} == {"installed, its version is not given"}
    # a finder that raises for a module is a package that is not there
    def raises(module):
        raise ValueError(module + ".__spec__ is None")
    assert {entry["state"] for entry in functions["package_lines"](raises, lambda names: "1.0")} == {MISSING}


def test_the_packages_of_prompt_optimization_are_the_ones_of_its_requirements_file(monkeypatch, tmp_path):
    packages = load(monkeypatch, tmp_path).namespace["PACKAGES"]
    requirements = (REPOSITORY / "SAS-Viya-Integrations" / "Prompt-Optimization" / "requirements.txt").read_text(encoding="utf-8")
    required = [tuple(line.strip().split(">=")) for line in requirements.split("\n") if line.strip() and not line.startswith("#")]

    assert [(package, least) for part, package, _, _, least, _ in packages if part == "Prompt optimization"] == required
    assert required == [("dspy", "3.2.1"), ("requests", "2.31"), ("optuna", "3.0")]


def test_the_packages_of_the_rag_runtime_are_the_ones_its_code_imports(monkeypatch, tmp_path):
    """rag_core has no requirements file: what it needs is what it imports. Every module of another
    package that rag_core or the retrieval model imports is on the page, and pandas and swat, which
    the RAG steps import."""
    packages = load(monkeypatch, tmp_path).namespace["PACKAGES"]
    rag = REPOSITORY / "SAS-Viya-Integrations" / "RAG"
    imported = set()
    for path in list((rag / "rag_core").rglob("*.py")) + [rag / "retrieve_context.py"]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
    third_party = {module for module in imported if module not in sys.stdlib_module_names and module != "rag_core"}

    on_the_page = {module for part, _, module, _, _, _ in packages if part == "RAG"}
    assert third_party == {"requests", "psycopg2", "singlestoredb", "pypdfium2", "markitdown"}
    assert on_the_page == third_party | {"pandas", "swat"}
    step = json.loads((REPOSITORY / "SAS-Viya-Integrations" / "Custom-Steps" / "RAG - Load Vector Store.step").read_text(encoding="utf-8"))
    assert "import pandas" in step["templates"]["SAS"] and "import swat" in step["templates"]["SAS"]


def test_the_packages_of_logging_are_the_ones_the_three_programs_import(monkeypatch, tmp_path):
    packages = load(monkeypatch, tmp_path).namespace["PACKAGES"]
    logging = REPOSITORY / "SAS-Viya-Integrations" / "Logging-Monitoring"
    imported = set()
    for name in ("Log-Parser-Code.sas", "Get-All-Prompts.sas", "Link-Logs-To-Models.sas"):
        text = (logging / name).read_text(encoding="utf-8").replace("\r\n", "\n")
        block = text.split("\nsubmit;\n", 1)[1].split("\nendsubmit;", 1)[0]
        for node in ast.walk(ast.parse(block)):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])

    assert {module for module in imported if module not in sys.stdlib_module_names} == {"pandas", "swat", "requests"}
    assert {module for part, _, module, _, _, _ in packages if part == "Logging and Monitoring"} == {"pandas", "swat", "requests"}


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

class Page(HTMLParser):
    """The tags, the attributes and the text of a page."""

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.tags, self.attributes, self.links, self.texts, self.open = [], [], [], [], []
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attributes.extend((tag, name, value) for name, value in attrs)
        if tag == "a":
            self.links.append(dict(attrs))
        if tag not in ("meta", "br"):
            self.open.append(tag)

    def handle_endtag(self, tag):
        assert self.open and self.open[-1] == tag, (tag, self.open[-3:])
        self.open.pop()

    def handle_data(self, data):
        if data.strip() and "style" not in self.open:
            self.texts.append(" ".join(data.split()))


ALLOWED_TAGS = {"html", "head", "meta", "title", "style", "body", "main", "h1", "h2", "h3", "p", "ul", "li", "a", "table", "thead", "tbody",
                "tr", "th", "td", "details", "summary"}


def test_the_page_of_a_complete_installation(monkeypatch, tmp_path):
    site = Site()
    done = run(monkeypatch, tmp_path, site)
    page = Page(done.page)

    assert done.rc == "0" and page.open == []
    assert done.page.startswith('<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n')
    assert set(page.tags) <= ALLOWED_TAGS and page.tags.count("h1") == 1
    assert page.texts[0] == "SAS Agentic AI Accelerator - Start Here" and page.texts[1] == "SAS Agentic AI Accelerator"
    # three sentences of what the accelerator is
    welcome = page.texts[2]
    assert welcome.startswith("The accelerator brings large language models, embedding models and decision models into SAS Viya")
    assert len(re.findall(r"\.(?: |$)", welcome)) == 3
    # the two reports by the uri they have on this system, then the guides
    report_uris = [site.member("Prompt Builder/Prompt Builder")["uri"], site.member("Logging and Monitoring/LLM Usage Report")["uri"]]
    assert [link["href"] for link in page.links[:2]] == ["/SASVisualAnalytics/?reportUri=" + uri.replace("/", "%2F") for uri in report_uris]
    assert [link["href"] for link in page.links[2:]] == [
        "https://sassoftware.github.io/sas-agentic-ai-accelerator/User-Guide/Prompt-Builder",
        "https://sassoftware.github.io/sas-agentic-ai-accelerator/User-Guide/RAG",
        "https://sassoftware.github.io/sas-agentic-ai-accelerator/Administration-Guide/Introduction",
        "https://sassoftware.github.io/sas-agentic-ai-accelerator/Administration-Guide/Logging-&-Monitoring",
        "https://sassoftware.github.io/sas-agentic-ai-accelerator/Administration-Guide/Troubleshooting-Guide"]
    assert all(link.get("rel") == "noopener noreferrer" for link in page.links)
    # the summary: the counts and the release
    lines, _ = lines_of(monkeypatch, tmp_path, Site())
    assert f"{len(lines)} ok, 0 missing, 0 not checked. release.json: release 2.3.0, commit 0123456789ab, of 2026-10-12." in page.texts
    assert f"{len(ENTRIES)} of {len(ENTRIES)} entries of release.json are present in this content root." in page.texts
    assert f"Every entry ({len(ENTRIES)})" in page.texts
    assert "It changed nothing: it only read." in " ".join(page.texts)
    # one NOTE in the SAS log, with the counts
    assert done.sas.messages == [("NOTE", f"Start Here: the page holds {len(lines)} checks - {len(lines)} ok, 0 missing, 0 not checked.")]


def test_every_check_is_a_row_with_its_state_and_what_to_do(monkeypatch, tmp_path):
    site = Site()
    site.remove("RAG/rag_core/adapters/base.py")
    site.tables.pop("LLM_LOGS")
    site.refuse("/modelPublish/", 403)
    done = run(monkeypatch, tmp_path, site)
    lines, _ = lines_of(monkeypatch, tmp_path, site)
    rows = re.findall(r'<tr><td>(.*?)</td><td class="state (\w+)">(.*?)</td><td>(.*?)</td><td>(.*?)</td></tr>', done.page)

    # every line is a row; a missing entry stands in the short table of the content and in the table of every entry
    expected = []
    for entry in lines:
        expected.append((entry["what"], entry["state"]))
    in_page = [(Page("<p>" + what + "</p>").texts[0], state) for what, _, state, _, _ in rows]
    assert sorted(in_page) == sorted(expected + [("RAG/rag_core/adapters/base.py (file)", MISSING)])
    assert {(css, state) for _, css, state, _, _ in rows} == {("ok", OK), ("missing", MISSING), ("unchecked", NOT_CHECKED)}
    assert "1 missing" not in done.page and f"{sum(1 for e in lines if e['state'] == OK)} ok, 2 missing, 1 not checked." in done.page
    assert f"{len(ENTRIES) - 1} of {len(ENTRIES)} entries of release.json are present in this content root." in done.page
    # a row that is not ok says what to do, in one sentence that ends with a full stop
    for what, css, state, found, todo in rows:
        assert (todo != "") == (state != OK) or state == OK, what
        if state != OK:
            assert todo.endswith(".") and found, what


def test_the_page_needs_no_script_and_no_file_of_another_host(monkeypatch, tmp_path):
    done = run(monkeypatch, tmp_path)
    page = Page(done.page)

    assert "script" not in page.tags and "link" not in page.tags and "img" not in page.tags and "iframe" not in page.tags
    names = {name for _, name, _ in page.attributes}
    assert names <= {"lang", "charset", "name", "content", "href", "rel", "class", "scope"}
    assert not [name for name in names if name.startswith("on")] and "style" not in names and "src" not in names
    assert page.tags.count("style") == 1 and "@import" not in done.page and "url(" not in done.page
    # a record of the file fits a record of the DATA step that copies it
    assert max(len(line.encode("utf-8")) for line in done.page.split("\n")) < 32767


HOSTILE = '<script>alert("x")</script> & <img src=x onerror=alert(1)> \'" %put ERROR; &sysuserid.'


def test_every_text_of_the_page_is_escaped(monkeypatch, tmp_path):
    """Names come from the system and from the parameters of the job: a folder, an entry of release.json, a
    CAS server, a destination, a compute context, a project. None of them is read as HTML."""
    site = Site()
    site.root = "/Root " + HOSTILE
    site.release["version"] = "2.3.0 " + HOSTILE
    site.release["entries"].append({"path": "Data/" + HOSTILE.replace("/", "|"), "kind": "file " + HOSTILE})
    site.destinations.append({"name": HOSTILE, "destinationType": HOSTILE})
    site.contexts.append({"name": "Context " + HOSTILE})
    site.cas_servers.append(HOSTILE)
    site.options["computeContext"] = "Context " + HOSTILE
    settings = {"contentRoot": site.root, "casServer": "server " + HOSTILE, "caslib": "lib " + HOSTILE, "credentialDomain": HOSTILE,
                "repositoryName": HOSTILE, "llmProject": HOSTILE, "embeddingProject": HOSTILE, "decisionProject": HOSTILE,
                "computeContexts": HOSTILE + "|second " + HOSTILE, "guideUrl": 'https://docs.example.com/"><script>alert(1)</script>'}
    site.options["credentialDomain"] = ""
    done = run(monkeypatch, tmp_path, site, settings)
    page = Page(done.page)

    assert done.rc == "0" and page.open == []
    assert set(page.tags) <= ALLOWED_TAGS
    assert "<script" not in done.page and "<img" not in done.page and "onerror=" not in done.page.replace("onerror=alert(1)&gt;", "")
    # the hostile text is on the page as text, many times
    assert sum(1 for text in page.texts if '<script>alert("x")</script>' in text) > 10
    # no attribute holds anything but what the program wrote, and a link leads to this host or to the guides
    for tag, name, value in page.attributes:
        if name == "href":
            assert value.startswith("/SASVisualAnalytics/?reportUri=%2Freports%2Freports%2F") or value.startswith("https://docs.example.com/"), value
        elif name == "class":
            assert value in ("summary", "note", "state ok", "state missing", "state unchecked"), value
    assert all(set(link) == {"href", "rel"} for link in page.links)
    # and what goes to the SAS log is text for SAS
    for kind, message in done.sas.messages:
        assert kind == "NOTE" and not set(message) & set("%&;'\"")


@pytest.mark.parametrize("address", ["javascript:alert(1)", "//evil.example.com", "data:text/html,x", "", "/", "ftp://example.com", "https://",
                                     "/SASVisualAnalytics"])
def test_a_guide_address_that_is_no_http_address_is_no_link(monkeypatch, tmp_path, address):
    done = run(monkeypatch, tmp_path, settings={"guideUrl": address})
    page = Page(done.page)

    assert len(page.links) == 2 and all(link["href"].startswith("/SASVisualAnalytics/") for link in page.links)
    assert "User Guide: Prompt Builder" in page.texts


def test_a_report_that_is_not_there_is_named_and_not_linked(monkeypatch, tmp_path):
    site = Site()
    site.remove("Logging and Monitoring/LLM Usage Report")
    done = run(monkeypatch, tmp_path, site)
    page = Page(done.page)

    assert [link["href"] for link in page.links if link["href"].startswith("/")] == [
        "/SASVisualAnalytics/?reportUri=" + site.member("Prompt Builder/Prompt Builder")["uri"].replace("/", "%2F")]
    assert f"The report LLM Usage Report was not found in {ROOT}." in page.texts


# ---------------------------------------------------------------------------
# Nothing is changed, and nothing that is secret is asked for or shown
# ---------------------------------------------------------------------------

def test_the_program_asks_with_get_and_nothing_else():
    """The page changes nothing on the system. The Python calls requests.get and no other function of the
    package, and the SAS code around it holds no request, no CAS statement and no file but the page."""
    block = harness.python_block()
    tree = ast.parse(block)
    of_requests = [node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "requests"]
    assert of_requests == ["get"]
    for word in ("post", "put", "delete", "patch", "request", "Session", "urlopen", "method"):
        assert not re.search(rf"\b{word}\s*\(", block) and f"'{word.upper()}'" not in block and f'"{word.upper()}"' not in block, word
    assert not re.search(r"\b(subprocess|socket|urllib\.request|http\.client|shutil)\b", block)
    assert [node.names[0].name for node in ast.walk(tree) if isinstance(node, ast.Import)] == ["html", "importlib.util", "json", "os", "re", "requests"]
    # the one file the Python writes is the page, in the WORK folder
    opened = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open"]
    assert len(opened) == 1 and ast.unparse(opened[0].args[0]) == "os.path.join(setting('_sh_work'), PAGE_FILE)"
    assert not re.search(r"\bos\.(remove|unlink|rename|makedirs|mkdir|rmdir|system|popen)\b", block)

    code = re.sub(r"/\*.*?\*/", "", harness.sas_code(), flags=re.DOTALL).lower()
    for statement in ("proc http", "proc cas", "proc casutil", "filesrvc", "libname", "proc sql", "proc datasets", "proc import", "%include", "x '"):
        assert statement not in code, statement
    assert not re.search(r"^\s*cas\s", code, flags=re.MULTILINE) and not re.search(r"^\s*caslib\s", code, flags=re.MULTILINE)
    assert re.findall(r"^\s*data\s+([^;]+);", code, flags=re.MULTILINE) == ["_null_", "_null_"]
    assert re.findall(r"^\s*file\s+(\w+)", code, flags=re.MULTILINE) == ["_webout", "_webout"]
    assert re.findall(r"^\s*proc\s+(\w+)", code, flags=re.MULTILINE) == ["python", "python"]


def test_a_run_sends_get_requests_only_and_asks_for_no_secret(monkeypatch, tmp_path):
    site = Site()
    done = run(monkeypatch, tmp_path, site)

    # the stand-in for requests has get and nothing else, so any other call would have failed the run
    assert done.rc == "0" and len(site.requests) > 20
    assert not [path for path in site.paths() if "secret" in path.lower()]
    assert site.paths("/credentials") == ["/credentials/domains/agentic-ai-keys"]
    assert site.paths("/identities") == [] and site.paths("/authorization") == []
    assert {path.split("/")[1] for path in site.paths()} == {"folders", "files", "reports", "casManagement", "modelRepository", "credentials",
                                                           "modelPublish", "compute"}


@pytest.mark.parametrize("status", [None, 403, 500])
def test_the_page_holds_no_token_and_no_user_id(monkeypatch, tmp_path, status):
    """Every object of the made-up system was created by uid7781 and added by uid9024, and a refusal names uid7781 in its text."""
    site = Site()
    if status:
        for prefix in SERVICES:
            if prefix not in ("/folders/", "/files/"):
                site.refuse(prefix, status)
    done = run(monkeypatch, tmp_path, site)
    said = done.page + " ".join(message for _, message in done.sas.messages)

    assert "uid7781" in json.dumps(site.folders) and "uid9024" in json.dumps(site.folders)
    for secret in (harness.TOKEN, "Bearer", "uid7781", "uid9024", "example.com", "createdBy", "addedBy", "/certs/"):
        assert secret not in said, secret
    assert harness.VIYA_HOST not in said


def test_without_the_requests_package_the_page_says_so(monkeypatch, tmp_path):
    done = run(monkeypatch, tmp_path, without_requests=True, packages=dict(ALL_PACKAGES, requests=None))
    page = Page(done.page)

    assert done.rc == "0" and page.open == []
    assert "Python package requests" in page.texts and "not installed, so SAS Viya could not be asked" in page.texts
    assert "Have requests installed into the Python of this compute context." in page.texts


def test_a_check_that_ends_early_is_a_line_of_the_page(monkeypatch, tmp_path):
    loaded = load(monkeypatch, tmp_path, packages=ALL_PACKAGES)

    def broken(get, settings):
        raise KeyError("something this program did not foresee, for uid7781")

    loaded.namespace["check_installation"] = broken
    loaded.namespace["start_here"]()
    page = Page(loaded.page)

    assert loaded.rc == "0" and "they ended early with KeyError" in page.texts and "uid7781" not in loaded.page
    assert "0 ok, 0 missing, 1 not checked." in page.texts


# ---------------------------------------------------------------------------
# The SAS code around the Python
# ---------------------------------------------------------------------------

def test_the_parameters_of_the_job_and_what_the_program_takes_without_them():
    code = harness.sas_code()
    defaults = dict(re.findall(r"^%_sh_default\((\w+), (.*)\);$", code, flags=re.MULTILINE))

    assert defaults == harness.DEFAULT_SETTINGS
    assert ("%macro _sh_default(name, value);\n    %if not %symexist(&name.) %then %do;\n        %global &name.;\n"
            "        %let &name. = &value.;\n    %end;\n%mend _sh_default;") in code
    # the Python reads the same names, and reads them as text: none is resolved in a statement of the SAS code
    block = harness.python_block()
    read = ast.literal_eval(re.search(r"for name in (\('contentRoot'.*?\))\}", block, flags=re.DOTALL).group(1))
    assert set(read) == set(defaults)
    below = re.sub(r"/\*.*?\*/", "", code.split("%sysmacdelete _sh_default;")[1], flags=re.DOTALL)
    assert re.findall(r"&(\w+)", below) and set(re.findall(r"&(\w+)", below)) == {"_sh_rc", "_sh_work", "_sh_syntaxcheck"}
    # the header names every parameter, and the two of SAS Job Execution
    header = code.split("*/")[0]
    for parameter in list(defaults) + ["_action", "_output_type"]:
        assert re.search(rf"\b{parameter}\b", header), parameter
    assert "_output_type     html" in header


def test_the_page_is_copied_to_webout_and_a_session_without_it_gets_a_note():
    code = re.sub(r"/\*.*?\*/", "", harness.sas_code(), flags=re.DOTALL)
    send = code[code.index("%macro _sh_send;"):code.index("%mend _sh_send;")]

    # no fileref: a NOTE that names the file, and nothing is written
    assert send.index("%if %sysfunc(fileref(_webout)) GT 0 %then %do;") < send.index("%return;") < send.index("data _null_;")
    assert "It is the file &_sh_work./start_here.html.;" in send
    # the page of the Python program, line by line
    assert ('data _null_;\n            infile "&_sh_work./start_here.html" lrecl=32767 truncover;\n            file _webout lrecl=32767;\n'
            "            input;\n            put _infile_;\n        run;") in send
    assert harness.python_block().count("PAGE_FILE = 'start_here.html'") == 1
    # where the Python program left no page, a page of the SAS code says so
    fallback = send[send.index("%else %do;"):]
    puts = re.findall(r"put '(.*)';", fallback)
    assert puts[0] == "<!DOCTYPE html>" and puts[-1] == "</body></html>" and len(puts) == 7
    assert "The installation check could not run" in " ".join(puts) and "PROC PYTHON" in " ".join(puts)
    for text in puts:
        assert len(text) < 262 and not set(text) & set("%&'"), text
    page = Page("\n".join(puts))
    assert page.open == [] and set(page.tags) <= ALLOWED_TAGS
    assert [link["href"] for link in page.links] == ["https://sassoftware.github.io/sas-agentic-ai-accelerator"]


def test_the_job_ends_as_completed_whatever_the_session_could_not_do():
    """SAS Job Execution shows the page of a job that completed and its own error page for one that failed.
    An error must not keep the DATA step that writes the page from running, so syntax check mode is
    switched off while the program runs, and the condition code is set back at its end."""
    code = re.sub(r"/\*.*?\*/", "", harness.sas_code(), flags=re.DOTALL)
    statements = [line.strip() for line in code.split("\n") if line.strip()]

    assert statements.index("%let _sh_syntaxcheck = %sysfunc(getoption(syntaxcheck));") + 1 == statements.index("options nosyntaxcheck;")
    assert statements.index("options nosyntaxcheck;") < statements.index("proc python restart;") < statements.index("%_sh_send;")
    assert statements[-3:] == ["options &_sh_syntaxcheck.;", "%let syscc = 0;", "%symdel _sh_viyaHost _sh_work _sh_rc _sh_syntaxcheck / nowarn;"]
    assert "%let _sh_rc = -1;" in statements and statements.index("%let _sh_rc = -1;") < statements.index("proc python restart;")
    # no ABORT, and no statement that ends the session
    assert not re.search(r"\babort\b|%abort|endsas", code, flags=re.IGNORECASE)
    # the Python sets the return code as its last statement, behind the file of the page
    tree = ast.parse(harness.python_block())
    start_here = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "start_here")
    assert ast.unparse(start_here.body[-1]) == "hand_over('_sh_rc', '0')"
    assert ast.unparse(tree.body[-1]) == "start_here()"


def test_the_python_holds_no_line_that_would_end_it():
    block = harness.python_block()

    assert harness.program().count("\nsubmit;\n") == 1 and harness.program().count("\nendsubmit;\n") == 1
    assert "endsubmit" not in block and block.isascii()
    # nor what SAS code reads as a comment or as a macro variable, should the block ever be read as SAS code
    assert "/*" not in block and "*/" not in block
    assert re.findall(r"&\w+", block) == []
    compile(block, "Start-Here.sas (python)", "exec")
