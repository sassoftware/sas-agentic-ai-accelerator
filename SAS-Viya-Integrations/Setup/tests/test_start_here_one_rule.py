"""Start-Here.sas and mdb content-deploy: ONE rule for what a folder holds under the name of an entry.

review-20b and gate-20b: the page and mdb judged one state differently. For a step the page looked for
objects and references under the name with and without the ending .step, where mdb takes the name of
the entry as it is; and for two objects of the name and kind of an entry mdb said FAILED where the
page said `ok | present`.

The rule is the one of mdb (Model-Definition-Builder/cli/src/mdb/viya/content.py, which this file
reads and runs and does not change). It is written down here ONCE, as a table: the kind of the entry
and what its folder holds under its name - how many objects of its kind, how many references to an
object of its kind - give the outcome of mdb content-deploy and the line of the page. Each row is
then built as a content folder that is made up (start_here_harness.Site) and asked twice: the Python
of Start-Here.sas reads the folder, and `deploy` of mdb reads the SAME folder in a dry run. Both have
to give what the table says.

What mdb does with an object it found - compare it, update it - is not part of the rule and is stood
in for: one object is `equal`, none is `created`. The page compares no content either: an object mdb
would update is `present` on the page.

Below a folder. mdb writes nothing below a folder it fails and looks at nothing there (`its folder
FAILED`). The page says what it can read: below a name two folders have it checks nothing, because the
listing decides which of them comes first; below a folder with a reference to a folder beside it, it
reads that one folder, and the line of the folder says that nothing is written below it. The second
table holds that.
"""
import ast
import importlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote, urlencode

import pytest

import start_here_harness as harness
from start_here_harness import ALL_PACKAGES, ENTRIES, ROOT, Site, get_of, load, run

REPOSITORY = Path(__file__).resolve().parents[3]
MDB = REPOSITORY / "Model-Definition-Builder" / "cli" / "src" / "mdb"
OK, MISSING, NOT_CHECKED = "ok", "missing", "not checked"

REFERENCE_ALONE = "a reference of that name is in its folder, and the object it names lives elsewhere"
REFERENCE_BESIDE = "present, and a reference of that name is in its folder, which names an object that lives elsewhere"


def several(count):
    return f"{count} objects of that name and kind are in its folder, and nothing says which one is meant"


# ---------------------------------------------------------------------------
# THE RULE
# ---------------------------------------------------------------------------

# One entry of each kind of the made-up content folder
ENTRY = {"file": "RAG/rag_core/adapters/base.py", "step": "Logging and Monitoring/LLM - Log Parser.step",
         "jobDefinition": "Data/Load-Accelerator-Tables", "folder": "RAG/rag_core", "report": "Prompt Builder/Prompt Builder",
         "decision": "Decisions/Call LLM Node"}
# The kinds mdb writes. A report and a decision it does not write: they come with the package
WRITTEN = ("file", "step", "jobDefinition", "folder")
PACKAGE_ONLY = ("report", "decision")

# (objects of the name and kind, references of the name to an object of the kind):
#     (what mdb says of the entry, the state of its line on the page, what the line found, counted as present)
RULE = {
    (0, 0): ("created", MISSING, "not in its folder", False),
    (1, 0): ("equal", OK, "present", True),
    (2, 0): ("FAILED", MISSING, several(2), True),
    (3, 0): ("FAILED", MISSING, several(3), True),
    (0, 1): ("FAILED", MISSING, REFERENCE_ALONE, False),
    (0, 2): ("FAILED", MISSING, REFERENCE_ALONE, False),
    (1, 1): ("FAILED", MISSING, REFERENCE_BESIDE, True),
    (1, 2): ("FAILED", MISSING, REFERENCE_BESIDE, True),
    # two objects are named before a reference beside them: below two folders the page reads nothing
    (2, 1): ("FAILED", MISSING, several(2), True),
}
# The same for a report and a decision: mdb says `not deployed` with one of two notes and fails none, whatever has its name
ON_THE_SITE, NOT_ON_THE_SITE = "on the site", "not on the site - it comes with the package"
RULE_OF_A_REPORT = {
    (0, 0): (NOT_ON_THE_SITE, MISSING, "not in its folder", False),
    (1, 0): (ON_THE_SITE, OK, "present", True),
    (2, 0): (ON_THE_SITE, OK, "present", True),
    (0, 1): (NOT_ON_THE_SITE, MISSING, REFERENCE_ALONE, False),
    (0, 2): (NOT_ON_THE_SITE, MISSING, REFERENCE_ALONE, False),
    (1, 1): (ON_THE_SITE, OK, "present", True),
    (1, 2): (ON_THE_SITE, OK, "present", True),
    (2, 1): (ON_THE_SITE, OK, "present", True),
}
# What else can have the name of an entry and changes no outcome of mdb and no state of the page: a reference to an
# object of another kind, an object of another kind, and - for a step - a step under the name without its ending
OTHER_KIND = {"file": "report", "step": "file", "jobDefinition": "file", "folder": "file", "report": "file", "decision": "file"}
BESIDE = ("nothing else", "a reference to another kind", "an object of another kind", "the other spelling")


def cases():
    for kind in WRITTEN + PACKAGE_ONLY:
        for held in (RULE_OF_A_REPORT if kind in PACKAGE_ONLY else RULE):
            for beside in BESIDE:
                if beside != "the other spelling" or kind == "step":
                    yield kind, held, beside


def folder_with(kind, held, beside="nothing else", first=False):
    """The made-up content folder with `held` = (objects, references) under the name of the entry of that kind."""
    objects, references = held
    path = ENTRY[kind]
    site = Site()
    if objects == 0:
        site.remove(path)
    for _ in range(objects - 1):
        site.twin(path, kind, first=first)
    for _ in range(references):
        site.refer(path, kind, first=first)
    if beside == "a reference to another kind":
        site.refer(path, OTHER_KIND[kind], first=first)
    elif beside == "an object of another kind":
        site.twin(path, OTHER_KIND[kind], first=first)
    elif beside == "the other spelling":
        site.twin(path[:-len(".step")], kind, first=first)
        site.refer(path[:-len(".step")], kind, first=first)
    return site


# ---------------------------------------------------------------------------
# Asking the page, and asking mdb
# ---------------------------------------------------------------------------

def page_lines(monkeypatch, tmp_path, site):
    loaded = load(monkeypatch, tmp_path, site, packages=ALL_PACKAGES)
    lines, _ = loaded.namespace["check_installation"](get_of(site), dict(harness.DEFAULT_SETTINGS))
    return {entry["what"]: entry for entry in lines if entry["section"] == "Content"}


@pytest.fixture(scope="module")
def mdb_content():
    """viya/content.py of THIS checkout, loaded from its source under a name of its own: an mdb that is installed
    from another checkout is not the one that is asked. Nothing of it is changed on disk."""
    spec = importlib.util.spec_from_file_location("mdb_of_this_checkout", MDB / "__init__.py", submodule_search_locations=[str(MDB)])
    package = importlib.util.module_from_spec(spec)
    sys.modules["mdb_of_this_checkout"] = package
    spec.loader.exec_module(package)
    content = importlib.import_module("mdb_of_this_checkout.viya.content")
    assert Path(content.__file__).resolve() == (MDB / "viya" / "content.py").resolve()
    return content


class _Answer:
    def __init__(self, response):
        self.status_code, self.content, self.headers = response.status_code, response.content, {}
        self.ok = 200 <= response.status_code < 300

    def json(self):
        return json.loads(self.content.decode("utf-8"))


class _Session:
    """What mdb asks with, answered by the Site the page reads: GET and nothing else."""

    def __init__(self, site):
        self.site = site

    def get(self, path, params=None, headers=None):
        url = harness.VIYA_HOST + quote(path, safe="/@") + ("?" + urlencode(params) if params else "")
        return _Answer(self.site.get(url, headers={"Authorization": "Bearer " + harness.TOKEN}, verify=True, timeout=30))


def mdb_outcomes(monkeypatch, content, site):
    """{(path, kind): (status, note)} of a dry run of mdb's deploy over the entries the page checks."""
    core = sys.modules["mdb_of_this_checkout.core.content"]
    manifest = core.ContentManifest(repo_root=REPOSITORY, content_root=ROOT, package="a-package.json",
                                    entries=[core.Entry(path=path, kind=kind) for path, kind in ENTRIES])

    def found_or_not(session, source, entry, folder_id, existing, dry_run, settings, new_values=None):
        assert dry_run and len(existing) <= 1, "a deployer of mdb is handed one object at most"
        return (content.EQUAL if existing else content.CREATED), ""

    monkeypatch.setattr(content, "_DEPLOYERS", {kind: found_or_not for kind in ("file", "step", "jobDefinition")})
    result = content.deploy(_Session(site), manifest, dry_run=True, source=object())
    return {(outcome.entry.path, outcome.entry.kind): (outcome.status, outcome.note) for outcome in result.outcomes}


# ---------------------------------------------------------------------------
# The table, held against both
# ---------------------------------------------------------------------------

def test_the_table_names_every_kind_of_entry_and_the_made_up_folder_has_one_of_each(mdb_content):
    assert set(ENTRY) == set(WRITTEN) | set(PACKAGE_ONLY) == set(mdb_content._KIND_URI) == set(harness.URI_OF)
    assert set(mdb_content._TRANSFER_URI) == {"decision"} and set(mdb_content._content.TRANSFER_KINDS) == set(PACKAGE_ONLY)
    assert all((path, kind) in ENTRIES for kind, path in ENTRY.items())
    # the table by which the page tells the kind of a member is the one of mdb
    of_the_page = re.search(r"^KIND_URI = (\{.*?\})$", harness.python_block(), flags=re.MULTILINE | re.DOTALL)
    assert ast.literal_eval(of_the_page.group(1)) == mdb_content._KIND_URI == harness.URI_OF
    # and the table of this file leaves out no case of up to three members of one name and kind, two of them of either sort
    assert set(RULE) - {(3, 0)} == set(RULE_OF_A_REPORT) == {(objects, references) for objects in range(3) for references in range(3)
                                                            if objects + references <= 3}


@pytest.mark.parametrize("kind, held, beside", list(cases()),
                         ids=[f"{kind}, {held[0]} objects, {held[1]} references, {beside}" for kind, held, beside in cases()])
def test_the_page_and_mdb_say_of_an_entry_what_the_table_says(monkeypatch, tmp_path, mdb_content, kind, held, beside):
    path = ENTRY[kind]
    said, state, found, counted = (RULE_OF_A_REPORT if kind in PACKAGE_ONLY else RULE)[held]
    site = folder_with(kind, held, beside)

    # mdb, for this folder
    status, note = mdb_outcomes(monkeypatch, mdb_content, site)[(path, kind)]
    if kind in PACKAGE_ONLY:
        assert (status, note) == ("not deployed", said)
    else:
        assert status == said, note
    # the page, for the same folder
    entry = page_lines(monkeypatch, tmp_path, site)[f"{path} ({kind})"]
    assert (entry["state"], bool(entry["state"] == OK or entry.get("present"))) == (state, counted), entry
    if beside == "nothing else":
        assert entry["found"] == found
    # one sentence for both: a line of the page is ok exactly where mdb found the entry and failed nothing
    assert (entry["state"] == OK) == (status == "equal" or note == ON_THE_SITE)


@pytest.mark.parametrize("kind, held", [(kind, held) for kind in WRITTEN + PACKAGE_ONLY for held in ((2, 0), (1, 1), (1, 2), (2, 1))],
                         ids=lambda value: str(value))
def test_the_order_of_the_listing_changes_no_line(monkeypatch, tmp_path, kind, held):
    """Two members of one name: whichever the folder lists first, the line of the entry is the same."""
    path = ENTRY[kind]
    lines = [page_lines(monkeypatch, tmp_path, folder_with(kind, held, first=first))[f"{path} ({kind})"] for first in (False, True)]

    assert lines[0] == lines[1]


@pytest.mark.parametrize("kind", WRITTEN + PACKAGE_ONLY)
def test_the_kind_of_a_member_is_the_one_its_uri_says_for_the_page_as_for_mdb(monkeypatch, tmp_path, mdb_content, kind):
    """mdb tells the kind of a member by what its uri starts with and reads no contentType. The page told an object
    by its contentType until this round, and in the passes of 2026-10-13 the two found the same entries in the same
    folders. A member whose contentType names another kind than its uri is made up here: it is judged by its uri on
    the page as well. The object of the entry with another contentType is the entry, and an object whose uri is of
    another kind is not, whatever its contentType says."""
    path = ENTRY[kind]
    site = Site()
    site.member(path)["contentType"] = "something else"
    status, note = mdb_outcomes(monkeypatch, mdb_content, site)[(path, kind)]
    entry = page_lines(monkeypatch, tmp_path, site)[f"{path} ({kind})"]
    assert (status == "equal" or note == ON_THE_SITE) and (entry["state"], entry["found"]) == (OK, "present")

    other = Site()
    other.member(path)["uri"] = harness.URI_OF[OTHER_KIND[kind]] + "00000000-0000-4000-8000-000000000000"
    status, note = mdb_outcomes(monkeypatch, mdb_content, other)[(path, kind)]
    entry = page_lines(monkeypatch, tmp_path, other)[f"{path} ({kind})"]
    assert (status, note) in (("created", ""), ("not deployed", NOT_ON_THE_SITE)) and entry["state"] == MISSING
    assert entry["found"] in (f"the object of that name is a {OTHER_KIND[kind]}", "a file of that name is there, and no registered step")


@pytest.mark.parametrize("written, reference", [("reference", True), ("REFERENCE", True), ("Reference", True), ("child", False), (None, False),
                                                ("", False), (" reference ", False), ("referenced", False)],
                         ids=lambda value: repr(value))
def test_a_member_is_a_reference_for_the_page_where_it_is_one_for_mdb(monkeypatch, tmp_path, mdb_content, written, reference):
    """mdb: a member is a child unless its type says reference, in any case of the letters - and as it is written,
    blanks and all. The page reads the type with the same expression. A file beside a member of its name and kind
    is failed by both where that member is a reference, and is one of two files where it is none."""
    path, kind = ENTRY["file"], "file"
    site = Site()
    site.refer(path, kind)
    member = [found for found in site.folders[site.folder_ids["RAG/rag_core/adapters"]] if found["name"] == "base.py"][1]
    if written is None:
        del member["type"]
    else:
        member["type"] = written
    assert mdb_content.is_reference(member) is reference

    status, note = mdb_outcomes(monkeypatch, mdb_content, site)[(path, kind)]
    entry = page_lines(monkeypatch, tmp_path, site)[f"{path} ({kind})"]
    assert status == "FAILED" and ("a reference of that name" in note) is reference and ("2 files of that name" in note) is not reference
    assert (entry["state"], entry["found"]) == (MISSING, REFERENCE_BESIDE if reference else several(2))


def test_an_entry_without_a_kind_mdb_knows_is_any_object_of_its_name(monkeypatch, tmp_path):
    """mdb writes release.json with a kind for every entry. An entry without one - a file somebody made by hand -
    is present where an object of its name is there, as before: there is no kind to count two objects of."""
    site = Site()
    site.release["entries"] = [{"path": "release.json", "kind": "file"}, {"path": "Start Here"}, {"path": "Data", "kind": "a kind of later"}]
    site.twin("Start Here", "file")
    lines = page_lines(monkeypatch, tmp_path, site)

    assert {what: (entry["state"], entry["found"]) for what, entry in lines.items()} == {
        "release.json (file)": (OK, "present"), "Start Here": (OK, "present"), "Data (a kind of later)": (OK, "present")}


# Below a folder: (folders of the name, references of the name to a folder) ->
#     (what mdb says of a file below it, the state of the line of that file on the page, what the line found)
NOT_READ = "its folder was not read: it is, or lies below, one of two or more folders of one name, and nothing says which one is meant"
BELOW = {
    (1, 0): ("equal", OK, "present"),
    (0, 0): ("created", MISSING, "its folder is not in the content root"),
    (0, 1): ("FAILED", MISSING, "its folder is not in the content root"),
    (2, 0): ("FAILED", NOT_CHECKED, NOT_READ),
    (2, 1): ("FAILED", NOT_CHECKED, NOT_READ),
    # the one folder of the name is read; the line of the folder says that nothing is written below it
    (1, 1): ("FAILED", OK, "present"),
}
TWO_FOLDERS = ("Remove or rename all but one of them: while they are there, the import and mdb content-deploy write nothing for this entry. "
               "Nothing is written below the folder either.")
FOLDER_TODO = {
    (2, 0): TWO_FOLDERS,
    (2, 1): TWO_FOLDERS,
    (1, 1):"Remove the reference from the folder: while it is there, the import and mdb content-deploy write nothing for this entry. "
            "Nothing is written below the folder either.",
}


@pytest.mark.parametrize("held", list(BELOW), ids=[f"{held[0]} folders, {held[1]} references" for held in BELOW])
@pytest.mark.parametrize("first", [False, True], ids=["listed last", "listed first"])
def test_what_lies_below_a_folder(monkeypatch, tmp_path, mdb_content, held, first):
    said, state, found = BELOW[held]
    site = folder_with("folder", held, first=first)
    below = [(path, kind) for path, kind in ENTRIES if path.startswith(ENTRY["folder"] + "/")]
    assert [kind for _, kind in below] == ["folder", "file", "file"]

    outcomes = mdb_outcomes(monkeypatch, mdb_content, site)
    lines = page_lines(monkeypatch, tmp_path, site)
    assert {outcomes[entry][0] for entry in below} == {said}
    for path, kind in below:
        # a folder and the two files of the made-up folder, one and two folders below it
        entry = lines[f"{path} ({kind})"]
        assert (entry["state"], entry["found"]) == (state, found), entry
    if state == NOT_CHECKED:
        assert {lines[f"{path} ({kind})"]["todo"] for path, kind in below} == {"Remove or rename all but one of the folders of that name."}
    if held in FOLDER_TODO:
        assert lines[f"{ENTRY['folder']} (folder)"]["todo"] == FOLDER_TODO[held]
    # nothing but what the table names is not ok
    wrong = {what for what, entry in lines.items() if entry["state"] != OK}
    expected_wrong = ({f"{ENTRY['folder']} (folder)"} if held != (1, 0) else set()) | ({f"{path} ({kind})" for path, kind in below} if state != OK else set())
    assert wrong == expected_wrong


def test_no_folder_of_a_name_two_folders_have_is_asked_for(monkeypatch, tmp_path):
    """Neither of the two is listed: the page asks for the members of no folder it can not tell from its twin."""
    site = folder_with("folder", (2, 0))
    twins = [member["uri"] for member in site.folders[site.folder_ids["RAG"]] if member["name"] == "rag_core"]
    page_lines(monkeypatch, tmp_path, site)

    assert len(twins) == 2 and not [path for path in site.paths() if any(path.startswith(uri) for uri in twins)]


def test_the_page_counts_and_shows_an_entry_two_objects_have(monkeypatch, tmp_path):
    """As with a reference beside the object (round 20): the object is there, so the entry is counted as present,
    and its line stands with the lines that are not ok, in front of the folded list."""
    site = folder_with("file", (2, 0))
    done = run(monkeypatch, tmp_path, site)
    row = ('<tr><td>RAG/rag_core/adapters/base.py (file)</td><td class="state missing">missing</td><td>' + several(2) + "</td><td>"
           "Remove or rename all but one of them: while they are there, the import and mdb content-deploy write nothing for this entry."
           "</td></tr>")

    assert f"{len(ENTRIES)} of {len(ENTRIES)} entries of release.json are present in this content root." in done.page
    assert done.page.count(row) == 2 and done.page.index(row) < done.page.index("<details>") and "1 missing" in done.page


# ---------------------------------------------------------------------------
# release.json, which the page reads before it knows an entry
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("first", [False, True], ids=["listed last", "listed first"])
def test_two_release_files_are_not_read(monkeypatch, tmp_path, mdb_content, first):
    """mdb fails release.json where the content root holds two files of that name. The page reads neither: the
    entries it checks would be the ones of whichever the listing names first."""
    site = Site()
    site.twin("release.json", "file", first=first)
    loaded = load(monkeypatch, tmp_path, site, packages=ALL_PACKAGES)
    lines, _ = loaded.namespace["check_installation"](get_of(site), dict(harness.DEFAULT_SETTINGS))
    release = [entry for entry in lines if entry["section"] == "Release"]

    assert [(entry["what"], entry["state"], entry["found"]) for entry in release] == [("release.json", MISSING, several(2))]
    assert release[0]["todo"] == ("Remove or rename all but one of them: while they are there, the import and mdb content-deploy write "
                                  "nothing for this entry.")
    assert site.paths("/files/files") == [] and [entry for entry in lines if entry["section"] == "Content"] == []
    assert mdb_outcomes(monkeypatch, mdb_content, site)[("release.json", "file")][0] == "FAILED"


# ---------------------------------------------------------------------------
# What to do about a report that is not there
# ---------------------------------------------------------------------------

BRING_REPORT = ("Import the package of the accelerator again, or run mdb builders-import: a report comes with the package, and "
                "mdb content-deploy writes none.")


@pytest.mark.parametrize("held, lead", [((0, 0), ""), ((0, 1), "Remove the reference from the folder. Then: ")],
                         ids=["not in its folder", "only a reference"])
def test_the_advice_for_a_report_names_what_brings_a_report(monkeypatch, tmp_path, mdb_content, held, lead):
    """review-20b. The page advised `run mdb content-deploy` for a report that is missing, and mdb content-deploy
    writes no report: its line for the entry is `not deployed ... (not on the site - it comes with the package)`."""
    site = folder_with("report", held)
    entry = page_lines(monkeypatch, tmp_path, site)[f"{ENTRY['report']} (report)"]

    assert mdb_outcomes(monkeypatch, mdb_content, site)[(ENTRY["report"], "report")] == ("not deployed", NOT_ON_THE_SITE)
    assert entry["todo"] == lead + BRING_REPORT
    # and a report whose folder is not there
    gone = Site()
    gone.remove("Prompt Builder")
    lines = page_lines(monkeypatch, tmp_path, gone)
    assert (lines[f"{ENTRY['report']} (report)"]["found"], lines[f"{ENTRY['report']} (report)"]["todo"]) == (
        "its folder is not in the content root", BRING_REPORT)
    # the other kinds keep their advice: both commands write them
    assert lines["Prompt Builder/SAS-LLM-Prompt-Builder-UI (jobDefinition)"]["todo"] == "Import the package of the accelerator again, or run mdb content-deploy."


def test_the_line_of_a_report_takes_its_advice_from_one_sentence(monkeypatch, tmp_path):
    """The sentence of a report names the import and mdb builders-import, and mdb content-deploy only as what
    writes none; the one statement that picks the advice of an entry picks it for a report."""
    namespace = load(monkeypatch, tmp_path).namespace
    block = harness.python_block()

    assert namespace["REPORT_MISSING"] == BRING_REPORT and "run mdb content-deploy" not in namespace["REPORT_MISSING"]
    assert namespace["ENTRY_MISSING"] == "Import the package of the accelerator again, or run mdb content-deploy."
    assert ("deploy = STEP_MISSING if kind == 'step' else REPORT_MISSING if kind == 'report' else DECISION_MISSING if kind == 'decision' "
            "else ENTRY_MISSING") in block
    assert block.count("REPORT_MISSING") == 2 and block.count("ENTRY_MISSING") == 2 and block.count("DECISION_MISSING") == 2


BRING_DECISION = ("Import the package of the accelerator again, or run mdb builders-import: a decision comes with the package, with the "
                  "Call LLM node, and mdb content-deploy writes none. Where builders-import names mdb decision-node-migrate, run that first.")


@pytest.mark.parametrize("held, lead", [((0, 0), ""), ((0, 1), "Remove the reference from the folder. Then: ")],
                         ids=["not in its folder", "only a reference"])
def test_the_advice_for_a_decision_names_the_import_and_not_content_deploy(monkeypatch, tmp_path, mdb_content, held, lead):
    """The decision of the content folder comes with the package, with the Call LLM node type it carries: mdb content-deploy
    writes none (`not deployed ... (not on the site - it comes with the package)`), and the page says so."""
    site = folder_with("decision", held)
    entry = page_lines(monkeypatch, tmp_path, site)[f"{ENTRY['decision']} (decision)"]

    assert mdb_outcomes(monkeypatch, mdb_content, site)[(ENTRY["decision"], "decision")] == ("not deployed", NOT_ON_THE_SITE)
    assert entry["state"] == MISSING and entry["todo"] == lead + BRING_DECISION
    assert "run mdb content-deploy" not in BRING_DECISION
    # a decision that is there is present, as mdb says `on the site`
    there = page_lines(monkeypatch, tmp_path, Site())[f"{ENTRY['decision']} (decision)"]
    assert (there["state"], there["found"]) == (OK, "present")
    assert mdb_outcomes(monkeypatch, mdb_content, Site())[(ENTRY["decision"], "decision")] == ("not deployed", ON_THE_SITE)
    # and a decision whose folder is not there
    gone = Site()
    gone.remove("Decisions")
    line = page_lines(monkeypatch, tmp_path, gone)[f"{ENTRY['decision']} (decision)"]
    assert (line["found"], line["todo"]) == ("its folder is not in the content root", BRING_DECISION)
