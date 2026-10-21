"""Load-Accelerator-Tables.sas: a PROC CAS step that halted is no answer (content-23).

A step that halts runs nothing behind the action it stopped at, so no CALL SYMPUTX of it runs (measured on SAS Viya in
round 22 for Get-All-Prompts.sas). The macro variables of the three steps of the job start at the value of a question
CAS did not answer - the class test of test_content_23.py in the tests of Logging-Monitoring takes them from the code -
and these tests let the made-up CAS library of casl_harness halt at each action of the steps:

- the question whether `<table>_NEW` is there: up to this change 'unknown' was the WARNING that tableExists said
  unknown, an answer CAS never gave; now it is the ERROR line of that question, and nothing is uploaded;
- the upload and its questions: the WARNING that nothing said what became of the upload, as before;
- the look at the end: up to this change the NOTE that the four tables are there; now each is named in the ERROR line.

SAS and CAS can not run here: the tests say what the job does IF a step stops at an action, as the made-up library has it.
"""
import sys
from pathlib import Path

import pytest

SETUP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SETUP.parents[0] / "Logging-Monitoring" / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import casl_harness  # noqa: E402
from test_content_23 import HaltingCAS  # noqa: E402
from test_loader_followed import RELEASES, SHEET_COLUMNS, columns, loaded, program, release, saved, symbols  # noqa: E402

TAKEN_NOT_SAID = (f"ERROR: Load-Accelerator-Tables: CAS did not say whether there is a table Public.{RELEASES}_NEW, the name the file is "
                  f"uploaded under first. Nothing was uploaded, the table Public.{RELEASES} was left as it is.")
NOTHING_SAID = (f"WARNING: Load-Accelerator-Tables: nothing said what became of the upload of ACCELERATOR_RELEASES.csv (messages above), "
                f"the table Public.{RELEASES} may not be loaded.")
SHEETS = ("LLM_FACT_SHEET", "EMBEDDING_FACT_SHEET", "DECISION_FACT_SHEET")


def _library(halt_from):
    cas = HaltingCAS(halt_from=halt_from, upload_rows=755)
    cas.table("Public", RELEASES, columns(), rows=453, of="the table of before")
    cas.file("Public", RELEASES + ".sashdat", columns(), rows=453, of="the table of before")
    return cas


def test_the_question_for_the_name_of_the_upload_that_halted_uploads_nothing():
    cas = _library(halt_from=1)
    log, macros = release(cas)

    assert cas.halted == ["table.tableExists"] and log == [TAKEN_NOT_SAID] and macros["_lat_failed"] == "1"
    assert cas.session == {} and loaded(cas)["of"] == "the table of before" and saved(cas)["of"] == "the table of before"


def test_a_step_of_the_upload_that_halted_says_that_nothing_said_what_became_of_it():
    counter = _library(halt_from=999)
    log, macros = release(counter)
    assert macros["_lat_outcome"] == "loaded" and counter.count > 2
    for halt_from in range(2, counter.count + 1):
        cas = _library(halt_from=halt_from)
        log, macros = release(cas)
        assert cas.halted and log == [NOTHING_SAID] and macros["_lat_outcome"] == "unknown", (halt_from, cas.halted, log)


@pytest.mark.parametrize("halt_from", [1, 2, 3, 4])
def test_the_look_at_the_end_that_halted_names_the_tables_it_did_not_get_an_answer_for(halt_from):
    """The four tables are there; the step halts at the question for the n-th. Up to this change the job wrote the NOTE
    that all four are in the CAS library, and ended as a completed job."""
    cas = HaltingCAS(halt_from=halt_from)
    for name in (RELEASES,) + SHEETS:
        cas.table("Public", name, SHEET_COLUMNS, rows=3)
    generated, log, macros = casl_harness.follow(program(), "_lat_look", symbols(_lat_missing=""), cas)

    assert cas.halted and log == [f"ERROR: Load-Accelerator-Tables: not in the CAS library Public at the end of the run, or CAS did not say "
                                  f"whether they are: {RELEASES} {' '.join(SHEETS)}. The lines above say why."]
    assert macros["_lat_failed"] == "1"
