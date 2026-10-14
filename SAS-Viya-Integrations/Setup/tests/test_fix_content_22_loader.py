"""Load-Accelerator-Tables.sas: an action CAS refused is no answer (fix-content-22).

Round 22 found that the job read table.tableExists as `<result>.exists` without asking whether CAS had refused it, and
that a refused listing of the rows or of the columns read as 0 rows or as no column. Each action the job goes by now
reads its status (the class test of test_fix_content_22.py in the tests of Logging-Monitoring takes them from the code),
and these tests refuse each of them in turn, in the made-up CAS library of casl_harness:

- the question whether `<table>_NEW` is there, in front of the upload: nothing is uploaded, with an ERROR line;
- the question whether the upload is there and the count of its rows, the question whether the release table is in
  memory, the question whether `_LAT_SAVED` is there, and the listing of the columns of a table: the upload is dropped
  again and no table is loaded, with one ERROR line - and the release table, a table of the site and a saved file are
  left as they are;
- the question at the end whether a table is in the CAS library: the table is named in the ERROR line of the end.

Up to this change each refused question stopped the harness at the statement that read its answer; a refused listing
of the rows was the WARNING of an upload of 0 rows, and a refused listing of the columns the ERROR line of a table that
is no release table.

SAS and CAS can not run here: the tests say what the job does IF CAS answers a refused action with a severity other
than 0, as the made-up library has it.
"""
import sys
from pathlib import Path

import pytest

SETUP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SETUP.parents[0] / "Logging-Monitoring" / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import casl_harness  # noqa: E402
from test_fix_content_22 import RefusingCAS  # noqa: E402
from test_loader_followed import LOADED, RELEASES, SHEET_COLUMNS, columns, loaded, program, release, saved, symbols  # noqa: E402

TAKEN_REFUSED = (f"ERROR: Load-Accelerator-Tables: CAS refused to say whether there is a table Public.{RELEASES}_NEW, the name the file "
                 f"is uploaded under first. Nothing was uploaded, the table Public.{RELEASES} was left as it is.")
REFUSED = (f"ERROR: Load-Accelerator-Tables: CAS refused an action the job goes by to decide whether the upload takes the place of the "
           f"table Public.{RELEASES} - the ERROR lines above. The upload of ACCELERATOR_RELEASES.csv was dropped again and no table "
           f"Public.{RELEASES} was loaded.")


def _library(asks, there):
    """What the CAS library holds under the name of the release table: the release table of before, in memory and saved;
    a fact sheet of the site; or the release table as its saved file only, as a restart of the CAS server leaves it."""
    cas = RefusingCAS(asks=asks, upload_rows=755)
    if there == "release table":
        cas.table("Public", RELEASES, columns(), rows=453, of="the table of before")
        cas.file("Public", RELEASES + ".sashdat", columns(), rows=453, of="the table of before")
    elif there == "fact sheet":
        cas.table("Public", RELEASES, SHEET_COLUMNS, rows=30, of="a fact sheet")
    elif there == "saved file":
        cas.file("Public", RELEASES + ".sashdat", columns(), rows=453, of="the table of before")
    return cas


def _as_before(cas, there):
    """The table and the file of that name hold what they held before the run."""
    if there in ("release table", "fact sheet"):
        assert cas.promoted[cas.key("Public", RELEASES)]["of"] == ("a fact sheet" if there == "fact sheet" else "the table of before")
    else:
        assert loaded(cas) is None
    assert (saved(cas) or {}).get("of") == (None if there in ("fact sheet", None) else "the table of before")


def test_nothing_is_uploaded_where_cas_refused_the_question_for_the_name_of_the_upload():
    cas = _library({("table.tableExists", RELEASES + "_NEW", 1)}, "release table")
    log, macros = release(cas)

    assert log == [TAKEN_REFUSED] and macros["_lat_failed"] == "1"
    assert not [action for action, _ in cas.calls if action != "table.tableExists"] and cas.session == {}
    _as_before(cas, "release table")


CASES = {
    "the question for the upload": ({("table.tableExists", RELEASES + "_NEW", 2)}, ("release table", "saved file", None)),
    "the count of the upload": ({("table.tableInfo", "", None)}, ("release table", "saved file", None)),
    "the question for the table": ({("table.tableExists", RELEASES, None)}, ("release table", "fact sheet", None)),
    "the question for _LAT_SAVED": ({("table.tableExists", "_LAT_SAVED", None)}, ("saved file",)),
    "the columns of the table": ({("table.columnInfo", RELEASES, None)}, ("release table", "fact sheet")),
    "the columns of the saved file": ({("table.columnInfo", "_LAT_SAVED", None)}, ("saved file",)),
}


@pytest.mark.parametrize("case, there", [(case, there) for case, (_, places) in CASES.items() for there in places],
                         ids=[f"{case}, {there or 'nothing there'}" for case, (_, places) in CASES.items() for there in places])
def test_an_action_cas_refused_behind_the_upload_loads_no_table_and_writes_over_nothing(case, there):
    cas = _library(CASES[case][0], there)
    log, macros = release(cas)

    assert log == [REFUSED] and macros["_lat_failed"] == "1"
    _as_before(cas, there)
    # no table of the run is left in the session, nothing was promoted or saved, and the ERROR lines of CAS are the refusal
    assert cas.session == {} and [action for action, _ in cas.calls if action in ("table.promote", "table.save")] == []
    assert cas.errors and all(error.endswith(": the action was refused") for error in cas.errors)


@pytest.mark.parametrize("there", ["release table", "saved file", None])
def test_the_job_loads_the_table_where_cas_answers(there):
    cas = _library((), there)
    log, macros = release(cas)

    assert log == [LOADED] and macros["_lat_failed"] == "0" and cas.errors == []
    assert loaded(cas)["of"] == "upload" and saved(cas)["of"] == "upload" and cas.session == {}


SHEETS = ("LLM_FACT_SHEET", "EMBEDDING_FACT_SHEET", "DECISION_FACT_SHEET")


@pytest.mark.parametrize("refused", [(), ("LLM_FACT_SHEET",), (RELEASES, "DECISION_FACT_SHEET")], ids=["none", "one", "two"])
def test_the_end_names_a_table_cas_did_not_say_is_there(refused):
    """The look at the end asks for each of the four tables: a question CAS refused names the table in the ERROR line
    of the end, as a table that is not there does."""
    cas = RefusingCAS(asks={("table.tableExists", name, None) for name in refused})
    for name in (RELEASES,) + SHEETS:
        cas.table("Public", name, SHEET_COLUMNS, rows=3)
    generated, log, macros = casl_harness.follow(program(), "_lat_look", symbols(_lat_missing=""), cas)

    if refused:
        assert log == [f"ERROR: Load-Accelerator-Tables: not in the CAS library Public at the end of the run, or CAS refused to say whether "
                       f"they are: {' '.join(refused)}. The lines above say why."]
        assert macros["_lat_failed"] == "1"
    else:
        assert log == [f"NOTE: Load-Accelerator-Tables: {RELEASES}, LLM_FACT_SHEET, EMBEDDING_FACT_SHEET and DECISION_FACT_SHEET are in "
                       "the CAS library Public."]
        assert macros["_lat_failed"] == "0"


def test_the_end_names_a_table_that_is_not_there():
    cas = RefusingCAS()
    for name in (RELEASES, "LLM_FACT_SHEET"):
        cas.table("Public", name, SHEET_COLUMNS, rows=3)
    generated, log, macros = casl_harness.follow(program(), "_lat_look", symbols(_lat_missing=""), cas)

    assert log == ["ERROR: Load-Accelerator-Tables: not in the CAS library Public at the end of the run, or CAS refused to say whether "
                   "they are: EMBEDDING_FACT_SHEET DECISION_FACT_SHEET. The lines above say why."]


def test_the_readme_says_it():
    readme = " ".join((SETUP / "README.md").read_text(encoding="utf-8").split())

    assert ("The other actions of CAS the job goes by there - the question whether a table is there, the count of the rows of the "
            "upload and the listing of the columns of a table - are no answer either where CAS refuses one: the upload is dropped again "
            "and no table is loaded, with one more ERROR line, and where CAS refuses the question whether `<table>_NEW` is there, "
            "nothing is uploaded, with an ERROR line of that question.") in readme
    assert "when one of the four tables is not in the CAS library at its end or CAS refuses to say whether it is," in readme
