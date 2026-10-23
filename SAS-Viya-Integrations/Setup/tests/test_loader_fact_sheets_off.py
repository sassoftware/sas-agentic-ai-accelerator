"""Load-Accelerator-Tables.sas with factSheets=0: the release table only.

The files of the folder Data are the fact sheets of the release. A site that keeps definitions of its own
loads its sheets with mdb load-facts, and the job replaced them with the sheets of the release (an upgrade
review of 2026-10-21 found it; the changelog of 2.3.0 said the job "replaces no table that is not a release
table"). With factSheets=0 the job leaves the three fact sheet tables as they are, a fact sheet that is not
in the CAS library is a WARNING, and only the release table decides whether the job fails.

The macros are followed with macro_harness and the CASL of their PROC CAS step runs against a CAS library
that is made up, as in test_loader_followed.py.
"""
import sys
from pathlib import Path

SETUP = Path(__file__).resolve().parents[1]
REPOSITORY = SETUP.parents[1]
sys.path.insert(0, str(REPOSITORY / "SAS-Viya-Integrations" / "Logging-Monitoring" / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import casl_harness  # noqa: E402
from test_fix_content_22 import RefusingCAS  # noqa: E402
from test_loader_followed import RELEASES, SHEET_COLUMNS, follow, program, symbols  # noqa: E402

SHEETS = ("LLM_FACT_SHEET", "EMBEDDING_FACT_SHEET", "DECISION_FACT_SHEET")
LEFT = "NOTE: Load-Accelerator-Tables: factSheets is 0, the three fact sheet tables were left as they are."


def look(present):
    cas = RefusingCAS()
    for name in present:
        cas.table("Public", name, SHEET_COLUMNS, rows=3)
    _, log, macros = casl_harness.follow(program(), "_lat_look", symbols(_lat_missing="", factSheets="0"), cas)
    return log, macros


def test_the_fact_sheet_program_is_neither_asked_for_nor_included():
    generated, log, asked, macros = follow("_lat_find_fact_sheets", "200", 1, factSheets="0")

    assert asked == [] and log == [LEFT]
    # the include reads the file of one comment
    assert generated == ["filename _latlfs temp;", "data _null_;", "file _latlfs;",
                         "put '* Load-Accelerator-Tables: the fact sheets are not loaded in this run;';", "run;"]
    assert macros["_lat_failed"] == "0"


def test_a_refused_run_says_nothing_of_the_fact_sheets():
    _, log, asked, _ = follow("_lat_find_fact_sheets", "200", 1, factSheets="0", _lat_ready="0")

    assert asked == [] and log == []


def test_a_fact_sheet_that_is_not_there_is_a_warning_and_the_job_does_not_fail():
    log, macros = look([RELEASES, "LLM_FACT_SHEET"])

    assert log == ["WARNING: Load-Accelerator-Tables: not in the CAS library Public at the end of the run, or CAS did not say "
                   "whether they are: EMBEDDING_FACT_SHEET DECISION_FACT_SHEET. With factSheets 0 the job loads no fact sheet: "
                   "mdb load-facts loads them.",
                   f"NOTE: Load-Accelerator-Tables: {RELEASES} is in the CAS library Public."]
    assert macros["_lat_failed"] == "0" and macros["_lat_missing"] == ""


def test_the_release_table_still_decides_the_job():
    log, macros = look(SHEETS)

    assert log == ["ERROR: Load-Accelerator-Tables: not in the CAS library Public at the end of the run, or CAS did not say "
                   f"whether they are: {RELEASES}. The lines above say why."]
    assert macros["_lat_failed"] == "1"


def test_with_the_four_tables_there_the_note_is_the_one_of_before():
    log, macros = look((RELEASES,) + SHEETS)

    assert log == [f"NOTE: Load-Accelerator-Tables: {RELEASES}, LLM_FACT_SHEET, EMBEDDING_FACT_SHEET and DECISION_FACT_SHEET "
                   "are in the CAS library Public."]
    assert macros["_lat_failed"] == "0"


def test_the_page_says_that_the_fact_sheets_were_left():
    text = program()
    page = text[text.index("%macro _lat_page;"):text.index("%mend _lat_page;")]

    assert "if lengthn(symget('_lat_missing')) = 0 and symget('factSheets') = '0' then" in page
    assert "The job left the fact sheets as they are (factSheets 0): mdb load-facts loads them." in page


def test_the_manifest_gives_the_job_definition_the_parameter():
    manifest = (REPOSITORY / "SAS-Viya-Integrations" / "package-manifest.yaml").read_text(encoding="utf-8")

    assert '- {name: factSheets, type: character, default: "1", label: Load the fact sheets (1 or 0), siteSetting: true}' in manifest
