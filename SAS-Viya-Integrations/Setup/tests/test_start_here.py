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

    assert sections == ["Release", "Content", "CAS tables of the reports", "CAS tables of the logging chain", "SAS Model Manager",
                        "Options of the Prompt Builder report",
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
    assert "both register the step" in step["todo"]
    report = one(lines, "Prompt Builder/Prompt Builder (report)")
    assert (report["state"], report["found"]) == (MISSING, "the object of that name is a folder")
    assert reports["Prompt Builder"] == ""


# ---------------------------------------------------------------------------
# A step is registered by a command, never by an upload
# ---------------------------------------------------------------------------

STEP = "Logging and Monitoring/LLM - Log Parser.step"
REGISTER = "Import the package of the accelerator again, or run mdb content-deploy: both register the step. "


def test_the_advice_for_a_step_that_is_missing_names_the_commands_and_not_an_upload(monkeypatch, tmp_path):
    """Measured on 2026-10-12: a .step file uploaded in the place of a registered step is a file of the
    folder and no step, and mdb content-deploy then registers the step and leaves that file beside
    it. So no advice of the page may be read as 'upload the step file'."""
    site = Site()
    site.remove(STEP)
    lines, _ = lines_of(monkeypatch, tmp_path, site)
    missing = one(lines, STEP + " (step)")

    assert (missing["state"], missing["found"]) == (MISSING, "not in its folder")
    assert missing["todo"] == REGISTER + "Uploading its .step file to the folder does not."

    # the file was uploaded: the commands register the step, and the file has to go by hand
    site.add(STEP, "file")
    lines, _ = lines_of(monkeypatch, tmp_path, site)
    uploaded = one(lines, STEP + " (step)")
    assert (uploaded["state"], uploaded["found"]) == (MISSING, "a file of that name is there, and no registered step")
    assert uploaded["todo"] == (REGISTER + "Then delete the file of that name, which neither of them removes: a step file that was "
                                           "uploaded opens empty in SAS Studio.")

    # a step whose folder is not there gets the same advice as a step that is not in its folder
    site.remove("Logging and Monitoring")
    lines, _ = lines_of(monkeypatch, tmp_path, site)
    assert one(lines, STEP + " (step)")["todo"] == missing["todo"]
    # and an object that is no step is not told about steps
    assert one(lines, "Logging and Monitoring/Log-Parser-Code.sas (file)")["todo"] == "Import the package of the accelerator again, or run mdb content-deploy."


@pytest.mark.parametrize("first", ["file", "step"])
def test_a_registered_step_with_an_uploaded_file_beside_it_is_present(monkeypatch, tmp_path, first):
    """What the folder holds after the advice of before was followed: two members of one name, a
    registered step and a file. The step is there whichever of the two the folder lists first."""
    site = Site()
    site.remove(STEP)
    for kind in (first, "step" if first == "file" else "file"):
        site.add(STEP, kind)
    lines, _ = lines_of(monkeypatch, tmp_path, site)
    entry = one(lines, STEP + " (step)")

    assert [member["contentType"] for member in site.folders[site.folder_ids["Logging and Monitoring"]] if member["name"] == STEP.split("/")[1]] \
        == (["file", "flowStep"] if first == "file" else ["flowStep", "file"])
    assert (entry["state"], entry["found"]) == (OK, "present, and a file of that name lies beside it")
    assert entry["todo"] == "Delete the file of that name: it is a step file that was uploaded, not the step."
    assert {other["state"] for other in lines} == {OK}


def test_no_advice_of_the_page_tells_to_upload_something(monkeypatch, tmp_path):
    """Every sentence the program can give as what to do, read from its text: an upload is named
    only as what does not register a step and as what a file beside a step is."""
    tree = ast.parse(harness.python_block())
    documentation = {id(node.body[0].value) for node in ast.walk(tree)
                     if isinstance(node, (ast.FunctionDef, ast.Module)) and node.body and isinstance(node.body[0], ast.Expr)}
    sentences = [node.value for node in ast.walk(tree)
                 if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in documentation]
    namespace = load(monkeypatch, tmp_path).namespace
    of_steps = [namespace[name] for name in ("STEP_MISSING", "STEP_UPLOADED", "STEP_BESIDE")]

    assert of_steps == [
        REGISTER + "Uploading its .step file to the folder does not.",
        REGISTER + "Then delete the file of that name, which neither of them removes: a step file that was uploaded opens empty in SAS Studio.",
        "Delete the file of that name: it is a step file that was uploaded, not the step."]
    assert sorted(sentence for sentence in sentences if re.search(r"upload", sentence, flags=re.IGNORECASE)) == sorted(of_steps)
    assert not [sentence for sentence in sentences if re.search(r"\bupload (it|the|a|its)\b", sentence, flags=re.IGNORECASE)]
    # each of the two that say how a step comes into the folder names the import and the command
    for advice in of_steps[:2]:
        assert "mdb content-deploy" in advice and "Import the package of the accelerator again" in advice


def test_a_step_is_found_with_or_without_the_ending_of_its_file(monkeypatch, tmp_path):
    entries = [("release.json", "file"), ("Custom Steps/MM - Get All Repositories", "step"), ("Custom Steps/LLM - List Available LLMs.step", "step")]
    site = Site(entries=[("release.json", "file")])
    site.release["entries"] = [{"path": path, "kind": kind} for path, kind in entries]
    site.add("Custom Steps/MM - Get All Repositories.step", "step")
    site.add("Custom Steps/LLM - List Available LLMs", "step")
    lines, _ = lines_of(monkeypatch, tmp_path, site)

    assert set(states(lines, "Content").values()) == {OK}


# ---------------------------------------------------------------------------
# A reference is no object of the folder
# ---------------------------------------------------------------------------

REFERENCE_FOUND = "a reference of that name is in its folder, and the object it names lives elsewhere"
REMOVE = "Remove the reference from the folder. Then: "
DEPLOY = "Import the package of the accelerator again, or run mdb content-deploy."
REFERENCE_BESIDE = "present, and a reference of that name is in its folder, which names an object that lives elsewhere"
REMOVE_ONLY = "Remove the reference from the folder: while it is there, the import and mdb content-deploy write nothing for this entry."


def test_the_reference_of_the_harness_has_the_shape_that_was_read():
    """Read on SAS Viya 2026.09 on 2026-10-13, the answer to adding a reference to a file to a folder: the type of
    the member says reference, and its contentType and its uri are the ones of the file it names."""
    site = Site()
    named = site.refer("RAG/rag_core/tokens.py")
    member = site.member("RAG/rag_core/tokens.py")

    assert {key: member[key] for key in ("name", "type", "contentType", "uri")} == {
        "name": "tokens.py", "type": "reference", "contentType": "file", "uri": named}
    assert named.startswith("/files/files/") and site.member("RAG/rag_core/__init__.py")["type"] == "child"


def test_a_reference_in_the_place_of_a_file_is_not_the_file(monkeypatch, tmp_path):
    """live-19, defect 3. Measured on 2026-10-13: a folder held a reference named tokens.py that pointed at a file
    of another folder, and no file of that name; mdb content-deploy reported the entry as FAILED, and the page
    said 'RAG/rag_core/tokens.py (file) | ok | present' and '70 of 72 entries'. The kind of the member was read
    from its contentType, which is the one of the object a reference names."""
    site = Site()
    site.remove("RAG/rag_core/adapters/base.py")
    named = site.refer("RAG/rag_core/adapters/base.py")
    lines, _ = lines_of(monkeypatch, tmp_path, site)
    entry = one(lines, "RAG/rag_core/adapters/base.py (file)")

    assert (entry["state"], entry["found"], entry["todo"]) == (MISSING, REFERENCE_FOUND, REMOVE + DEPLOY)
    # the one entry is missing, and the object the reference names is not asked for
    content = states(lines, "Content")
    assert [what for what, state in content.items() if state != OK] == ["RAG/rag_core/adapters/base.py (file)"]
    assert not [path for path in site.paths() if named in path]
    # the page counts it as not present, and shows the line
    done = run(monkeypatch, tmp_path, site)
    assert f"{len(ENTRIES) - 1} of {len(ENTRIES)} entries of release.json are present in this content root." in done.page
    assert ("<tr><td>RAG/rag_core/adapters/base.py (file)</td><td class=\"state missing\">missing</td><td>" + REFERENCE_FOUND + "</td><td>"
            + REMOVE + DEPLOY + "</td></tr>") in done.page


@pytest.mark.parametrize("path, kind, advice", [
    ("Logging and Monitoring/Log-Parser-Code.sas", "file", DEPLOY),
    (STEP, "step", REGISTER + "Uploading its .step file to the folder does not."),
    ("Data/Load-Accelerator-Tables", "jobDefinition", DEPLOY),
    ("Prompt Builder/Prompt Builder", "report", DEPLOY),
], ids=["file", "step", "job definition", "report"])
def test_a_reference_is_taken_for_no_kind_of_entry(monkeypatch, tmp_path, path, kind, advice):
    """A member is taken for an entry only when it is a child of the folder and of the kind of the entry. A
    reference to an object of that very kind - which is what its contentType and its uri say - is none."""
    site = Site()
    site.remove(path)
    named = site.refer(path, kind)
    lines, reports = lines_of(monkeypatch, tmp_path, site)
    entry = one(lines, f"{path} ({kind})")

    assert site.member(path)["contentType"] == harness.MEMBER_TYPE[kind] and site.member(path)["uri"].startswith(harness.URI_OF[kind])
    assert (entry["state"], entry["found"], entry["todo"]) == (MISSING, REFERENCE_FOUND, REMOVE + advice)
    assert not [asked for asked in site.paths() if named in asked]
    if kind == "report":
        # the report it names is not linked and its options are not read
        assert reports["Prompt Builder"] == "" and states(lines, "Options of the Prompt Builder report") == {"Options": NOT_CHECKED}


@pytest.mark.parametrize("first", [True, False], ids=["the reference first", "the file first"])
def test_a_file_with_a_reference_of_its_name_beside_it_is_no_line_that_is_ok(monkeypatch, tmp_path, first):
    """Measured on 2026-10-13 with the file and a reference of its name in one folder: mdb content-deploy and
    mdb builders-import reported the entry as FAILED and wrote nothing for it ('nothing says which one is meant -
    remove the reference'), and the page said 'RAG/rag_core/tokens.py (file) | ok | present' for the same folder.
    The line of the entry now says what stands in its way, whichever of the two the folder lists first. The file is
    there: the entry is counted as present, and its line is shown with the lines that are not ok."""
    site = Site()
    site.refer("RAG/rag_core/adapters/base.py", first=first)
    lines, _ = lines_of(monkeypatch, tmp_path, site)
    entry = one(lines, "RAG/rag_core/adapters/base.py (file)")

    assert [member["type"] for member in site.folders[site.folder_ids["RAG/rag_core/adapters"]] if member["name"] == "base.py"] \
        == (["reference", "child"] if first else ["child", "reference"])
    assert (entry["state"], entry["found"], entry["todo"]) == (MISSING, REFERENCE_BESIDE, REMOVE_ONLY)
    assert [other["what"] for other in lines if other["state"] != OK] == ["RAG/rag_core/adapters/base.py (file)"]
    done = run(monkeypatch, tmp_path, site)
    assert f"{len(ENTRIES)} of {len(ENTRIES)} entries of release.json are present in this content root." in done.page
    row = ("<tr><td>RAG/rag_core/adapters/base.py (file)</td><td class=\"state missing\">missing</td><td>" + REFERENCE_BESIDE + "</td><td>"
           + REMOVE_ONLY + "</td></tr>")
    # once in front of the list of every entry, which is folded, and once in it
    assert done.page.count(row) == 2 and done.page.index(row) < done.page.index("<details>")
    assert "1 missing" in done.page


@pytest.mark.parametrize("path, kind", [
    ("Logging and Monitoring/Log-Parser-Code.sas", "file"), (STEP, "step"), ("Data/Load-Accelerator-Tables", "jobDefinition"),
    ("RAG/rag_core", "folder"),
], ids=["file", "step", "job definition", "folder"])
def test_a_reference_of_the_name_and_kind_of_an_entry_beside_its_object_stands_in_its_way(monkeypatch, tmp_path, path, kind):
    """Every kind of entry mdb writes: the object and a reference to an object of that kind under one name. What
    lies below a folder, and the report the page links, are read as without the reference."""
    site = Site()
    site.refer(path, kind)
    lines, reports = lines_of(monkeypatch, tmp_path, site)
    entry = one(lines, f"{path} ({kind})")

    assert (entry["state"], entry["found"], entry["todo"]) == (MISSING, REFERENCE_BESIDE, REMOVE_ONLY)
    assert [other["what"] for other in lines if other["state"] != OK] == [f"{path} ({kind})"]
    assert reports["Prompt Builder"] == site.folders[site.folder_ids["Prompt Builder"]][0]["uri"]


def test_a_report_with_a_reference_of_its_name_beside_it_is_present_as_mdb_has_it(monkeypatch, tmp_path):
    """mdb writes no report: `mdb content-deploy` says `not deployed report ... (on the site)` for a report that is
    there before it looks at a reference of its name, and lists the reference at the end. The page had said
    `missing` for such a report, with `the import and mdb content-deploy write nothing for this entry` - a sentence
    that holds for what mdb writes. A report that is only a reference is not there, and its line says so."""
    source = (REPOSITORY / "Model-Definition-Builder" / "cli" / "src" / "mdb" / "viya" / "content.py").read_text(encoding="utf-8")
    looked_up = source.index('done(entry, NOT_DEPLOYED, "on the site" if existing else')
    assert looked_up < source.index("references = _references_named(listed, entry.name, entry.kind)")
    path = "Prompt Builder/Prompt Builder"
    site = Site()
    site.refer(path, "report")
    lines, reports = lines_of(monkeypatch, tmp_path, site)
    entry = one(lines, f"{path} (report)")
    assert (entry["state"], entry["found"], entry["todo"]) == (OK, "present", "")
    assert [other["what"] for other in lines if other["state"] != OK] == []
    assert reports["Prompt Builder"] == site.folders[site.folder_ids["Prompt Builder"]][0]["uri"]


@pytest.mark.parametrize("path, kind, other", [
    ("RAG/rag_core/adapters/base.py", "file", "report"), ("RAG/rag_core/adapters/base.py", "file", "folder"),
    (STEP, "step", "file"), ("Data/Load-Accelerator-Tables", "jobDefinition", "file"), ("RAG/rag_core", "folder", "file"),
], ids=["a file, a report", "a file, a folder", "a step, a file", "a job definition, a file", "a folder, a file"])
def test_a_reference_to_an_object_of_another_kind_is_no_part_of_the_check(monkeypatch, tmp_path, path, kind, other):
    """The rule of mdb is the name and the kind: a reference that has the name of an entry and names an object of
    another kind fails nothing there - mdb writes the object beside it. Beside the object the line is ok; alone, the
    entry is not in its folder, and the advice is the one of an entry that is missing, without a word of the
    reference."""
    site = Site()
    named = site.refer(path, other)
    lines, _ = lines_of(monkeypatch, tmp_path, site)
    entry = one(lines, f"{path} ({kind})")

    assert named.startswith(harness.URI_OF[other]) and (entry["state"], entry["found"], entry["todo"]) == (OK, "present", "")
    assert {line["state"] for line in lines} == {OK}

    alone = Site()
    alone.remove(path)
    alone.refer(path, other)
    lines, _ = lines_of(monkeypatch, tmp_path, alone)
    entry = one(lines, f"{path} ({kind})")
    advice = REGISTER + "Uploading its .step file to the folder does not." if kind == "step" else DEPLOY
    assert (entry["state"], entry["found"], entry["todo"]) == (MISSING, "not in its folder", advice)


def test_a_reference_beside_a_registered_step_is_no_file_that_was_uploaded(monkeypatch, tmp_path):
    """The line of a step names a file of its name that lies beside it: a .step file that was uploaded. A
    reference to a file is no such file."""
    site = Site()
    site.refer(STEP, "file")
    lines, _ = lines_of(monkeypatch, tmp_path, site)
    entry = one(lines, STEP + " (step)")

    assert (entry["state"], entry["found"], entry["todo"]) == (OK, "present", "")


def test_a_reference_to_a_folder_is_not_followed(monkeypatch, tmp_path):
    """A folder of the content root that is a reference to a folder elsewhere: the entry of the folder says so,
    the members of the folder it names are not listed, and the entries below it are not in the content root."""
    site = Site()
    site.remove("RAG/rag_core")
    named = site.refer("RAG/rag_core", "folder")
    lines, _ = lines_of(monkeypatch, tmp_path, site)
    folder = one(lines, "RAG/rag_core (folder)")

    assert (folder["state"], folder["found"], folder["todo"]) == (MISSING, REFERENCE_FOUND, REMOVE + DEPLOY)
    assert named.startswith("/folders/folders/") and not [path for path in site.paths() if named in path]
    below = [entry for entry in lines if entry["what"].startswith("RAG/rag_core/")]
    # the folder the reference names holds an __init__.py: followed, that entry would be present
    assert [entry["what"] for entry in below] == ["RAG/rag_core/adapters (folder)", "RAG/rag_core/__init__.py (file)",
                                                 "RAG/rag_core/adapters/base.py (file)"]
    assert {(entry["state"], entry["found"]) for entry in below} == {(MISSING, "its folder is not in the content root")}


def test_a_release_file_that_is_a_reference_is_not_read(monkeypatch, tmp_path):
    site = Site()
    site.remove("release.json")
    named = site.refer("release.json")
    lines, _ = lines_of(monkeypatch, tmp_path, site)
    release = one(lines, "release.json")

    assert (release["state"], release["found"]) == (MISSING, REFERENCE_FOUND)
    assert release["todo"] == REMOVE + "Import the package of the accelerator again, or run mdb content-deploy, which writes the file."
    assert site.paths("/files/files") == [] and not [path for path in site.paths() if named in path]
    # without the file no entry is known
    assert [entry for entry in lines if entry["section"] == "Content"] == []


def test_a_release_file_with_a_reference_beside_it_is_read_and_its_line_says_so(monkeypatch, tmp_path):
    """release.json is an entry of the deploy as well: with a reference to a file of its name beside it, mdb writes
    no release.json. The page reads the file - the entries it lists are checked - and its line is not ok."""
    site = Site()
    named = site.refer("release.json")
    lines, _ = lines_of(monkeypatch, tmp_path, site)
    release = one(lines, "release.json")

    assert (release["state"], release["todo"]) == (MISSING, REMOVE_ONLY)
    assert release["found"] == "release 2.3.0, commit 0123456789ab, of 2026-10-12; " + REFERENCE_BESIDE
    assert not [path for path in site.paths() if named in path]
    # the entries the file lists are checked; the file is one of them, and its line says the same
    content = [entry for entry in lines if entry["section"] == "Content"]
    assert len(content) == len(ENTRIES) and [entry["what"] for entry in content if entry["state"] != OK] == ["release.json (file)"]
    assert (one(lines, "release.json (file)")["found"], one(lines, "release.json (file)")["todo"]) == (REFERENCE_BESIDE, REMOVE_ONLY)


def test_a_reference_to_another_kind_under_the_name_of_the_release_file_is_no_release_file(monkeypatch, tmp_path):
    """Alone it is no file to read and nothing to remove: the file is not in the content root, and mdb writes it
    beside the reference. Beside the file it changes nothing."""
    site = Site()
    site.refer("release.json", "report")
    assert one(lines_of(monkeypatch, tmp_path, site)[0], "release.json")["state"] == OK

    alone = Site()
    alone.remove("release.json")
    named = alone.refer("release.json", "report")
    release = one(lines_of(monkeypatch, tmp_path, alone)[0], "release.json")
    assert (release["state"], release["found"]) == (MISSING, "not in the content root")
    assert release["todo"] == "Import the package of the accelerator again, or run mdb content-deploy, which writes the file."
    assert not [path for path in alone.paths() if named in path]


def test_the_page_and_mdb_have_one_rule_for_a_reference():
    """mdb fails an entry for a reference of its name that names an object of its kind, and tells the kind by what
    the uri starts with (viya/content.py: _MEMBER_URI, _named, _references_named). The page holds the same table."""
    block = harness.python_block()
    mdb = (REPOSITORY / "Model-Definition-Builder" / "cli" / "src" / "mdb" / "viya" / "content.py").read_text(encoding="utf-8")
    table = re.search(r"^_MEMBER_URI = (\{.*?\})$", mdb, flags=re.MULTILINE | re.DOTALL)
    of_the_page = re.search(r"^KIND_URI = (\{.*?\})$", block, flags=re.MULTILINE | re.DOTALL)

    assert ast.literal_eval(of_the_page.group(1)) == ast.literal_eval(table.group(1)) == harness.URI_OF
    assert 'return [m for m in members if m.get("name") == name and str(m.get("uri") or "").startswith(prefix)]' in mdb
    assert "return [m for m in _named(members, name, kind) if is_reference(m)]" in mdb
    assert "[found for found in named if found[0] == REFERENCE and found[1].startswith(of_kind)])" in block
    # the two places that judge an entry ask the one function
    assert block.count("def named_as(members, names, kind):") == 1
    assert block.count("references = named_as(members, [RELEASE_FILE], 'file')[1]") == 1
    assert block.count("present, references = named_as(members, names, kind)") == 1 and block.count("named_as(") == 3


def test_the_program_tells_a_reference_by_the_type_of_the_member():
    """The rule mdb has for a member of a folder (viya/content.py, is_reference): a member is a child unless its
    type says reference - in any case of the letters."""
    block = harness.python_block()

    assert ("kind = REFERENCE if text_of(item.get('type')).lower() == REFERENCE else "
            "str(item.get('contentType') or item.get('type') or '')") in block
    assert block.count("REFERENCE = 'reference'") == 1
    mdb = (REPOSITORY / "Model-Definition-Builder" / "cli" / "src" / "mdb" / "viya" / "content.py").read_text(encoding="utf-8")
    assert 'return str(member.get("type") or "child").lower() == "reference"' in mdb
    # the header of the program says what a reference is to the page
    header = " ".join(line.lstrip(" *") for line in harness.program().split("*/")[0].split("\n"))
    assert ("A member of a folder that is a reference to an object elsewhere is no entry: it is not followed. A reference that has the "
            "name of an entry and names an object of the kind of the entry stands in the way of that entry - mdb content-deploy and "
            "the import write nothing for it -, and the line of the entry says so, where the reference is all the folder holds under "
            "that name and where it lies beside the object. A report that is there is present whatever lies beside it: mdb "
            "writes no report and fails none over a reference. A reference to an object of another kind is no part of the check"
            ) in " ".join(header.split())


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


def test_a_project_without_a_model_is_missing_and_says_how_models_get_into_it(monkeypatch, tmp_path):
    """Open item 18 of integrate-18: an empty project of definitions was ok. It is there and holds nothing to call."""
    site = Site()
    site.models[site.projects[2]["id"]] = 0
    site.models[site.projects[0]["id"]] = 0
    lines, _ = lines_of(monkeypatch, tmp_path, site)
    entry = one(lines, "Project Decision Model Project (decision model definitions)")

    assert (entry["state"], entry["found"]) == (MISSING, "found, no model yet")
    assert entry["todo"] == ("Register the decision model definitions you need with mdb register. Without decision models this part of the "
                             "accelerator has nothing to call.")
    entry = one(lines, "Project LLM Model Project (LLM definitions)")
    assert (entry["state"], entry["todo"]) == (MISSING, "Register the LLM definitions you need with mdb register.")


@pytest.mark.parametrize("table", harness.CHAIN_TABLES)
def test_a_table_of_the_logging_chain_that_is_not_there_is_missing(monkeypatch, tmp_path, table):
    """Open item 18 of integrate-18: DECISION_FACT_SHEET and the four tables Get-All-Prompts.sas writes beside
    PROMPT_EXPERIMENTS were not looked at. They stand in a section of their own, behind the tables of the reports."""
    site = Site()
    del site.tables[table]
    lines, _ = lines_of(monkeypatch, tmp_path, site)
    chain = [entry for entry in lines if entry["section"] == "CAS tables of the logging chain"]

    assert [entry["what"].split(" ")[0] for entry in chain] == ["Public." + name for name in harness.CHAIN_TABLES]
    entry = next(entry for entry in chain if entry["what"].startswith(f"Public.{table} "))
    assert (entry["state"], entry["found"]) == (MISSING, "not in the CAS library")
    assert entry["todo"] == ("Run the job Load-Accelerator-Tables of the folder Data in this content root, or mdb load-facts."
                             if table == "DECISION_FACT_SHEET" else "Run Get-All-Prompts.sas or the step LLM - Get All Prompts.")
    assert all(other["state"] == OK for other in chain if other is not entry)


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
# A session that runs under a service account: one line at the top
# ---------------------------------------------------------------------------

AS_YOURSELF = ("Open this page in a compute context that runs as you: add &_contextName=<the name of that compute context> to the address "
               "of this page, or set the context in the parameter _contextName of the job definition.")
ASK = "Ask an administrator to open this page, or for the right to read it."


def refusing(prefix, status):
    """A complete installation whose services answer every path that starts this way with a status."""
    site = Site()
    site.refuse(prefix, status)
    return site


def work(tmp_path, name):
    """A WORK folder of its own for one of several runs of a test: each run writes its page there."""
    folder = tmp_path / name
    folder.mkdir()
    return folder


def summary_of(page):
    """The paragraph at the top of the installation check."""
    return re.search(r'<p class="summary">(.*?)</p>', page).group(1).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


def not_checked_rows(page):
    return re.findall(r'<tr><td>(.*?)</td><td class="state unchecked">', page)


def test_a_session_of_another_user_that_is_refused_everything_gets_one_line(monkeypatch, tmp_path):
    """Measured on 2026-10-12 on one run: opened without the parameter _contextName the job ran in the
    SAS Job Execution compute context, every request was answered 401, and the page read '13 ok, 0
    missing, 15 not checked': fifteen lines, twelve of which advised to ask an administrator. (On a
    later run that day the same context built the whole page: what its sessions may read depends on
    how the system configures it.) The made-up system gives the same fifteen lines; the page now has
    one, at its top, with what it is and what to do."""
    site = Site()
    site.refuse("/", 401)
    lines, _ = lines_of(monkeypatch, tmp_path, site)
    # fifteen as on the page that was measured, and the five tables of the logging chain this round added
    assert [entry["state"] for entry in lines].count(NOT_CHECKED) == 20 and [entry["state"] for entry in lines].count(OK) == 13
    assert [entry["todo"] for entry in lines].count(ASK) == 17        # the page of before: 12, and the five tables of the logging chain

    done = run(monkeypatch, tmp_path, refusing("/", 401), harness.SERVICE_ACCOUNT_SESSION)
    said = ("SAS Viya refused every request of this page (HTTP 401): its SAS session runs as another user than you, in the compute "
            "context SAS Job Execution compute context, and what such a session may read depends on how the system configures its compute "
            "context")

    assert done.rc == "0"
    assert summary_of(done.page) == "13 ok, 0 missing, 20 not checked. " + said + ". " + AS_YOURSELF
    # one row stands for the fifteen, and it is the first row of the page
    assert not_checked_rows(done.page) == ["Requests to SAS Viya"]
    rows = re.findall(r'<tr><td>(.*?)</td><td class="state (\w+)">', done.page)
    assert rows[0] == ("Requests to SAS Viya", "unchecked") and [css for _, css in rows[1:]] == ["ok"] * 13
    assert "Ask an administrator" not in done.page and "may not be read by this session" not in done.page
    assert done.page.index("SAS session of this page") < done.page.index("Python packages: Logging and Monitoring")
    # a report that could not be looked for is not said to be missing from the folder
    assert "<li>The report Prompt Builder could not be looked for.</li>" in done.page and "was not found in" not in done.page
    # the log has the counts and the same sentence, without the character an address takes
    assert done.sas.messages == [
        ("NOTE", "Start Here: the page holds 33 checks - 13 ok, 0 missing, 20 not checked."),
        ("NOTE", "Start Here: " + said + ". Open the page in a compute context that runs as you: the parameter _contextName, in the address "
                 "of the page or in the job definition, names the context.")]
    # neither user is on the page or in the log
    everything = done.page + " ".join(message for _, message in done.sas.messages)
    assert "svc4410" not in everything and "uid7781" not in everything


SIGN_IN = "Open this page again. If the line stays, ask an administrator why SAS Viya does not take the sign-in of your session."
RIGHTS = "Ask an administrator for the rights to read what this page checks."


def test_a_session_that_names_neither_user_and_is_refused_everything_says_both_ways_out(monkeypatch, tmp_path):
    """A session that does not say whose it is - the program run in SAS Studio - can be another user's: the
    line names the compute context it ran in where that is known, and the way to open the page as oneself."""
    done = run(monkeypatch, tmp_path, refusing("/", 401), {})

    assert summary_of(done.page) == ("13 ok, 0 missing, 20 not checked. SAS Viya refused every request of this page (HTTP 401). Open this "
                                     "page again. If the line stays, " + AS_YOURSELF[0].lower() + AS_YOURSELF[1:])
    assert not_checked_rows(done.page) == ["Requests to SAS Viya"] and "another user" not in summary_of(done.page)


@pytest.mark.parametrize("session", [harness.OWN_SESSION, dict(harness.OWN_SESSION, SYSUSERID="UID7781"),
                                     dict(harness.OWN_SESSION, SYSUSERID="uid7781@EXAMPLE.COM"),
                                     dict(harness.OWN_SESSION, SYSUSERID="EXAMPLE" + chr(92) + "uid7781")],
                         ids=["the user who opened the page", "the same user in other letters", "the same user with a domain behind @",
                              "the same user with a domain in front of a backslash"])
@pytest.mark.parametrize("status, todo", [(401, SIGN_IN), (403, RIGHTS)], ids=["401", "403"])
def test_a_session_of_the_user_that_is_refused_everything_is_told_what_to_ask_for(monkeypatch, tmp_path, session, status, todo):
    """Review-18: a user in his own session who lacks every right was told to open the page again and then in a
    compute context that runs as him - which it did already. The session runs as the person who opened the
    page: a 403 is a right to ask for, a 401 a sign-in that SAS Viya did not take. One person written with a
    domain is the same person: the page said 'does not run as you' for him."""
    done = run(monkeypatch, tmp_path, refusing("/", status), session)
    said = (f"SAS Viya refused every request of this page (HTTP {status}), which ran as you, in the compute context SAS Studio "
            "compute context")

    assert summary_of(done.page) == "13 ok, 0 missing, 20 not checked. " + said + ". " + todo
    assert not_checked_rows(done.page) == ["Requests to SAS Viya"]
    assert "another user" not in done.page and "_contextName" not in summary_of(done.page)
    # the log says the same, and the advice holds no ampersand
    assert done.sas.messages[-1] == ("NOTE", "Start Here: " + said + ". " + todo)


def test_a_session_of_the_user_refused_with_401_and_403_is_told_both(monkeypatch, tmp_path):
    site = refusing("/", 401)
    site.refuse("/casManagement/", 403)
    site.prefixes = dict(sorted(site.prefixes.items(), key=lambda item: -len(item[0])))
    done = run(monkeypatch, tmp_path, site, harness.OWN_SESSION)

    assert summary_of(done.page).endswith("(HTTP 401 and 403), which ran as you, in the compute context SAS Studio compute context. "
                                          + SIGN_IN + " " + RIGHTS)


def test_the_one_line_names_the_answers_that_refused(monkeypatch, tmp_path):
    done = run(monkeypatch, tmp_path, refusing("/", 403), harness.SERVICE_ACCOUNT_SESSION)
    assert "refused every request of this page (HTTP 403): its SAS session runs as another user than you" in summary_of(done.page)

    site = refusing("/", 401)
    site.refuse("/casManagement/", 403)
    site.prefixes = dict(sorted(site.prefixes.items(), key=lambda item: -len(item[0])))
    done = run(monkeypatch, tmp_path, site, harness.SERVICE_ACCOUNT_SESSION)
    assert "refused every request of this page (HTTP 401 and 403): its SAS session" in summary_of(done.page)
    assert not_checked_rows(done.page) == ["Requests to SAS Viya"]

    # a session whose compute context is not known says the same without it
    done = run(monkeypatch, tmp_path, refusing("/", 401), dict(harness.SERVICE_ACCOUNT_SESSION, _sh_hasContext="0"))
    assert ("(HTTP 401): its SAS session runs as another user than you, and what such a session may read depends on how the system "
            "configures its compute context. ") in summary_of(done.page)


@pytest.mark.parametrize("answered", ["/folders/", "/compute/", "/credentials/"])
def test_one_request_that_is_answered_keeps_every_line(monkeypatch, tmp_path, answered):
    """The one line stands for a session nothing answers. Where a service answers, each line says what it found."""
    site = Site()
    for prefix in SERVICES:
        if prefix != answered:
            site.refuse(prefix, 401)
    lines, _ = lines_of(monkeypatch, tmp_path, site)
    done = run(monkeypatch, tmp_path, site)

    assert "Requests to SAS Viya" not in done.page and "refused every request" not in done.page
    assert len(not_checked_rows(done.page)) >= [entry["state"] for entry in lines].count(NOT_CHECKED) > 5
    assert ASK in done.page
    assert re.search(r"\d+ ok, \d+ missing, \d+ not checked\. (release\.json|Content root): ", summary_of(done.page))


@pytest.mark.parametrize("status", [0, 500, 503, 404])
def test_a_session_that_is_not_refused_but_fails_keeps_every_line(monkeypatch, tmp_path, status):
    """401 and 403 are a session SAS Viya refuses. A service that is down, a request without an answer and a
    system without the accelerator are other things, and the page says them line by line."""
    loaded = load(monkeypatch, tmp_path, settings=harness.SERVICE_ACCOUNT_SESSION, packages=ALL_PACKAGES)
    functions = loaded.namespace
    statuses = []

    def answer(path, accept="application/json"):
        if status == 0:
            raise ConnectionError("no answer")
        return status, b"{}"

    lines, _ = functions["check_installation"](functions["recorded"](answer, statuses), dict(harness.DEFAULT_SETTINGS))
    session = functions["session_of"]("svc4410", "uid7781", "SAS Job Execution compute context")

    assert statuses and set(statuses) == {status}
    assert functions["one_line_for_a_refused_session"](lines, statuses, session) is lines
    # and so do a refusal among other answers, and a run that asked nothing
    assert functions["one_line_for_a_refused_session"](lines, [401, 401, status], session) is lines
    assert functions["one_line_for_a_refused_session"](lines, [], session) is lines
    assert functions["one_line_for_a_refused_session"](lines, [401, 403, 401], session) is not lines


def test_in_a_session_of_another_user_a_refused_line_advises_to_open_the_page_as_oneself(monkeypatch, tmp_path):
    """A service account that may read the folders and not CAS: the lines stay, and their advice is no longer
    to ask an administrator. The note under the summary says whose session built the page."""
    site = Site()
    for prefix in ("/casManagement/", "/modelPublish/"):
        site.refuse(prefix, 403)
    as_user = run(monkeypatch, work(tmp_path, 'own'), site, harness.OWN_SESSION)
    other = run(monkeypatch, work(tmp_path, 'other'), site, harness.SERVICE_ACCOUNT_SESSION)

    assert as_user.page.count(ASK) == 11 and "does not run as you" not in as_user.page
    assert "in a compute context that runs under a service account that is the service account" in as_user.page
    advice = (AS_YOURSELF[:-1] + " - or ask for the right the session lacks.").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    assert ASK not in other.page and other.page.count("<td>" + advice + "</td>") == 11
    note = re.search(r'<p class="note">(.*?)</p>', other.page).group(1).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    assert note == (f"Content root {ROOT}. This page was built by a SAS session that does not run as you, in the compute context SAS Job "
                    "Execution compute context: it shows what that session may read, which depends on how the system configures its "
                    "compute context. " + AS_YOURSELF + " It changed nothing: it only read.")
    assert "svc4410" not in other.page and "uid7781" not in other.page
    # the rows and their states are the same for both sessions
    states_of = lambda page: re.findall(r'<td class="state (\w+)">', page)
    assert states_of(as_user.page) == states_of(other.page)


def test_the_two_users_are_compared_and_go_no_further(monkeypatch, tmp_path):
    functions = load(monkeypatch, tmp_path).namespace
    session_of = functions["session_of"]

    assert session_of("svc4410", "uid7781", "A context") == {"other": True, "own": False, "context": "A context"}
    assert session_of("uid7781", "UID7781 ", " A context ") == {"other": False, "own": True, "context": "A context"}
    # one person written with a domain is one person; two persons of one domain are two
    for runs_as in ("uid7781@EXAMPLE.COM", "EXAMPLE" + chr(92) + "uid7781", "example" + chr(92) + "UID7781@example.com"):
        assert session_of(runs_as, "uid7781", "")["own"] is True, runs_as
    assert session_of("svc4410@EXAMPLE.COM", "uid7781@EXAMPLE.COM", "")["other"] is True
    # a session that does not name one of the two is neither another user's nor known to be the user's own
    for runs_as, started_for in (("svc4410", ""), ("", "uid7781"), ("", ""), ("svc4410", None), (None, None)):
        assert session_of(runs_as, started_for, "")["other"] is False and session_of(runs_as, started_for, "")["own"] is False
    # what the page is handed holds no user: two flags and the name of the context
    assert set(session_of("svc4410", "uid7781", "A context")) == {"other", "own", "context"}


def test_the_session_is_asked_only_for_what_it_has(monkeypatch, tmp_path):
    """SAS Job Execution sets SYS_COMPUTE_SESSION_OWNER and _contextName; a program run in SAS Studio may
    have neither. The SAS code looks with %symexist, and the Python reads a variable only behind its flag."""
    code = re.sub(r"/\*.*?\*/", "", harness.sas_code(), flags=re.DOTALL)
    assert "%let _sh_hasOwner = %symexist(SYS_COMPUTE_SESSION_OWNER);" in code and "%let _sh_hasContext = %symexist(_contextName);" in code
    assert code.index("%let _sh_hasContext = %symexist(_contextName);") < code.index("proc python restart;")
    # no statement of the SAS code resolves one of the three
    for name in ("sysuserid", "SYS_COMPUTE_SESSION_OWNER", "_contextName"):
        assert not re.search("&" + name, code, flags=re.IGNORECASE), name

    without = run(monkeypatch, work(tmp_path, 'studio'))
    assert "SYSUSERID" in without.sas.asked
    assert "SYS_COMPUTE_SESSION_OWNER" not in without.sas.asked and "_contextName" not in without.sas.asked
    with_both = run(monkeypatch, work(tmp_path, 'job'), settings=harness.OWN_SESSION)
    assert {"SYSUSERID", "SYS_COMPUTE_SESSION_OWNER", "_contextName"} <= set(with_both.sas.asked)
    # a page of a session that runs as its user is the page of before: no line about the session
    assert "SAS session of this page" not in with_both.page and without.page == with_both.page


def test_the_header_and_the_page_without_python_name_the_parameter():
    header = harness.program().split("*/")[0]
    assert re.search(r"^ \*   _contextName     the compute context the job runs in", header, flags=re.MULTILINE)
    flat = " ".join(line.lstrip(" *") for line in header.split("\n"))
    # what was measured on which run, and that it depends on the configuration of the context: no sentence says that it always refuses
    assert ("What the session of a compute context may read depends on how the system configures that context. Measured on the test "
            "system on 2026-10-12, in its SAS Job Execution compute context: on one run SAS Viya answered every request with 401 and the "
            "page had 15 lines that were not checked; on a later run the same day the page was built completely (97 lines ok)") in " ".join(flat.split())
    assert "under a service account, SAS Viya answered" not in flat and "always" not in flat
    code = re.sub(r"/\*.*?\*/", "", harness.sas_code(), flags=re.DOTALL)
    fallback = " ".join(re.findall(r"put '(.*)';", code))
    assert "the parameter _contextName, in the address of this page or in the job definition, names the context" in fallback
    assert "adding the parameter _contextName to the job definition" not in fallback


# ---------------------------------------------------------------------------
# The file release.json is asked for at the Files service only
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("uri", ["/identities/users/@currentUser", "https://other.example.org/files/files/abc", "/files/files/../../identities/users",
                                 "/files/files/abc/../../../credentials/domains/agentic-ai-keys/users/uid7781/secrets", "/reports/reports/abc",
                                 "/files/files/", "//other.example.org/files/files/abc"])
def test_a_release_file_that_is_no_file_of_the_files_service_is_not_asked_for(monkeypatch, tmp_path, uri):
    """The member of the folder names where its file is. The uri of a folder and of a report were held to
    their service before they were asked for; the one of release.json is held to /files/files/ as well."""
    site = Site()
    site.member("release.json")["uri"] = uri
    before = len(site.requests)
    lines, _ = lines_of(monkeypatch, tmp_path, site)
    release = one(lines, "release.json")

    assert (release["state"], release["found"]) == (MISSING, "the member of that name is no file of the Files service")
    assert "mdb content-deploy" in release["todo"]
    asked = [path for path, _ in site.requests[before:]]
    assert not [path for path in asked if path.startswith(("/files", "/identities", "/reports/reports/abc")) or "secrets" in path or "example.org" in path]
    assert [entry for entry in lines if entry["section"] == "Content"] == []
    # a uri of the Files service is asked for as before
    assert set(states(lines_of(monkeypatch, tmp_path, Site())[0], "Release").values()) == {OK}


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
    assert any(text.startswith("User Guide: Prompt Builder") for text in page.texts)


@pytest.mark.parametrize("address, linked", [
    ("https://sassoftware.github.io/sas-agentic-ai-accelerator", True),
    ("https://sassoftware.github.io/sas-agentic-ai-accelerator/", True),
    ("http://sassoftware.github.io/sas-agentic-ai-accelerator", False),
    ("https://evil.example.com/sas-agentic-ai-accelerator", False),
    ("https://sassoftware.github.io.evil.example.com/x", False),
    ("https://user@evil.example.com/x", False),
], ids=["the website", "the website with a slash", "the website without TLS", "another host", "a host that starts like it",
        "an address with a user"])
def test_a_guide_address_of_another_host_is_text(monkeypatch, tmp_path, address, linked):
    """Review-17 and review-18 (open item 14): guideUrl took any http(s) address, and a job parameter can come with
    the request - an address of this page could carry one that sends the reader elsewhere. The guides are links only
    to the website of the accelerator and to this SAS Viya; any other address is shown as text, with one line that
    says why."""
    done = run(monkeypatch, tmp_path, settings={"guideUrl": address})
    page = Page(done.page)
    guides = [link for link in page.links if not link["href"].startswith("/SASVisualAnalytics/")]

    if linked:
        assert len(guides) == 5 and all(link["href"].startswith("https://sassoftware.github.io/sas-agentic-ai-accelerator/") for link in guides)
        assert "the addresses of the guides are text, not links" not in done.page
    else:
        assert guides == []
        assert any(text == "User Guide: Prompt Builder: " + address.rstrip("/") + "/User-Guide/Prompt-Builder" for text in page.texts)
        assert "the addresses of the guides are text, not links" in done.page


CONTEXT_LINK = "/SASJobExecution/?_program=/SAS%20Agentic%20AI%20Accelerator/Start%20Here&_contextName="


@pytest.mark.parametrize("refused", [True, False], ids=["refused everything", "refused nothing"])
def test_a_page_whose_advice_is_another_compute_context_links_itself_in_the_others(monkeypatch, tmp_path, refused):
    """Open item 18 of integrate-18: the page said to add &_contextName= to its address and gave no link. In a
    session of another user it links itself in each compute context it found - or, where the contexts could not
    be listed, in each one the parameter computeContexts names -, but the one it ran in."""
    site = refusing("/", 401) if refused else Site()
    done = run(monkeypatch, tmp_path, site, harness.SERVICE_ACCOUNT_SESSION)
    page = Page(done.page)
    links = [link["href"] for link in page.links if link["href"].startswith("/SASJobExecution/")]

    assert CONTEXT_LINK + "SAS%20Studio%20compute%20context" in links and len(links) == len(set(links))
    assert CONTEXT_LINK + "SAS%20Job%20Execution%20compute%20context" not in links          # the context it ran in
    if refused:
        assert links == [CONTEXT_LINK + "SAS%20Studio%20compute%20context"]                # the contexts of the parameter
    assert "Whether a context runs as you depends on how the system configures it." in done.page
    # a session that runs as the person who opened the page is not sent elsewhere
    own = run(monkeypatch, work(tmp_path, "own"), site, harness.OWN_SESSION)
    assert not [link for link in Page(own.page).links if link["href"].startswith("/SASJobExecution/")]


def test_a_guide_address_on_this_sas_viya_is_a_link(monkeypatch, tmp_path):
    functions = load(monkeypatch, tmp_path).namespace
    hosts = {"sassoftware.github.io", "viya.example.com"}

    assert functions["guide_link"]("Guide", "https://viya.example.com/guides/x", hosts).startswith('<a href="https://viya.example.com/guides/x"')
    assert functions["guide_link"]("Guide", "https://viya.example.com:443/guides/x", hosts).startswith("<a ")
    assert functions["guide_link"]("Guide", "https://other.example.com/x", hosts) == "Guide: https://other.example.com/x"


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
    # the header names every parameter, and the three of SAS Job Execution
    header = code.split("*/")[0]
    for parameter in list(defaults) + ["_action", "_output_type", "_contextName"]:
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
    assert statements[-3:] == ["options &_sh_syntaxcheck.;", "%let syscc = 0;",
                               "%symdel _sh_viyaHost _sh_work _sh_rc _sh_syntaxcheck _sh_hasOwner _sh_hasContext / nowarn;"]
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
