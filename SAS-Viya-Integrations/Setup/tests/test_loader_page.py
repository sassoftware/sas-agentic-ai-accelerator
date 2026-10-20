"""Load-Accelerator-Tables.sas: the page the job writes for SAS Job Execution.

live-27, measured on 2026-10-20: the job, started through its address in SAS Job Execution as Setup/README.md documents
it, answered with an empty page titled SAS Output - the job definition had no parameter _output_type, so SAS Job
Execution showed the output of ODS, and the job wrote none. What it loaded was in its log only.

The job definition has the parameter _output_type with the value html now, as Start Here has it, and the job writes a
short page to the fileref _webout: each of the four tables with its rows and what this run did with it - loaded it, left
it as it was, or did not find it in the CAS library at the end. Whether this run loaded a table is read from CAS: the rows
of each table and the time CAS made it are read before the tables are loaded and again at the end (macro _lat_tables),
and a table with another time, or one that was not there before, is one this run loaded - an upload that takes the place
of a table is a new table of CAS. A session without _webout - the program run in SAS Studio - writes no page, and a job
definition without _output_type=html none either, with a NOTE: SAS Job Execution then has its own output on _webout.

Measured on 2026-10-20 on SAS Viya 2026.09, with the job definition in a test folder, test table names in CASUSER and its
address in SAS Job Execution: a run that loaded the four tables answered with the page "The four tables are in the CAS
library CASUSER." and four rows "Loaded by this run" (934, 35, 16 and 6 rows); a run from a content root without
decision_fact_sheet.csv and without Load-Fact-Sheets.sas gave the release table "Loaded by this run" and the three fact
sheets "Left as it was", with the WARNING lines in the log. A run that fails - a table that is not in the CAS library at
the end, a parameter that is refused - ends with SYSCC 1012, and SAS Job Execution answers it with its own page "Job
Error", which names the ERROR line of the job (HTTP 202): the page of the job is not shown then. The same job run through
the REST API of the jobExecution service, whose session has no _webout, wrote no page and no NOTE.

SAS can not run here. The two macros are followed with macro_harness and casl_harness, against a CAS library that is made
up; the DATA step of the page is read as text.
"""
import re
import sys
from pathlib import Path

import pytest

SETUP = Path(__file__).resolve().parents[1]
REPOSITORY = SETUP.parents[1]
sys.path.insert(0, str(REPOSITORY / "SAS-Viya-Integrations" / "Logging-Monitoring" / "tests"))

import casl_harness  # noqa: E402
import macro_harness  # noqa: E402

PROGRAM = SETUP / "Load-Accelerator-Tables.sas"
MANIFEST = REPOSITORY / "SAS-Viya-Integrations" / "package-manifest.yaml"
FOUR = ["ACCELERATOR_RELEASES", "LLM_FACT_SHEET", "EMBEDDING_FACT_SHEET", "DECISION_FACT_SHEET"]


def program():
    return PROGRAM.read_text(encoding="utf-8").replace("\r\n", "\n")


def macro(name):
    text = program()
    return text[text.index(f"%macro {name}"):text.index(f"%mend {name};")]


def tables(cas, when, ready="1"):
    """Follows _lat_tables against a CAS library: the value it leaves in _lat_before or _lat_after."""
    symbols = {"_lat_ready": ready, "caslib": "Public", "releaseTable": FOUR[0], "_lat_when": when, "_lat_before": "", "_lat_after": ""}
    generated, log, macros = casl_harness.follow(program(), "_lat_tables", symbols, cas)
    assert log == []
    return macros["_lat_" + when], generated


class _Page(macro_harness.Macros):
    """macro_harness.Macros with %symexist and %upcase, which the macro of the page asks _output_type with."""

    def call(self, name, inner):
        if name == "symexist":
            return "1" if inner.strip().lower() in self.symbols else "0"
        if name == "upcase":
            return self.resolve(inner).upper()
        return super().call(name, inner)


def page(webout, **symbols):
    """Follows _lat_page: (the SAS code it generates, the lines of its log). webout is what FILEREF answers for _webout:
    0 where SAS Job Execution assigned it, a number above 0 where the session has none."""
    macros = _Page(symbols, {"fileref": lambda arguments: webout})
    generated, log = macros.run(casl_harness.in_blocks(program(), "_lat_page"), "_lat_page")
    return generated, log


# ---------------------------------------------------------------------------
# What this run loaded: the rows and the time of each table, before and after
# ---------------------------------------------------------------------------

def test_the_rows_and_the_time_of_each_table_are_read_before_and_after_the_run():
    cas = casl_harness.FakeCAS()
    cas.table("Public", FOUR[0], ["version"], rows=707)
    cas.table("Public", FOUR[1], ["model"], rows=35)
    cas.table("Public", FOUR[3], ["model"], rows=5)
    cas.table("Public", "LLM_LOGS", ["model"], rows=99)
    before, generated = tables(cas, "before")

    made = {name: cas.made[id(cas.promoted[cas.key("Public", name)])] for name in (FOUR[0], FOUR[1], FOUR[3])}
    assert before.split() == [f"707/{made[FOUR[0]]}", f"35/{made[FOUR[1]]}", "none/", f"5/{made[FOUR[3]]}"]
    # one PROC CAS step per table, each asks for the tables of the library and names no table that is not there
    assert generated.count("proc cas;") == 4 and not [line for line in generated if "tableExists" in line]
    assert [action for action, _ in cas.calls] == ["table.tableInfo"] * 4 and cas.errors == []

    # the run: the release table is replaced by an upload, the embedding sheet loaded, the two others left as they were
    del cas.promoted[cas.key("Public", FOUR[0])]
    cas.table("Public", FOUR[0], ["version"], rows=755)
    cas.table("Public", FOUR[2], ["model"], rows=16)
    after, _ = tables(cas, "after")
    assert [entry.split("/")[0] for entry in after.split()] == ["755", "35", "16", "5"]
    assert [a != b for a, b in zip(after.split(), before.split())] == [True, False, True, False]


def test_a_library_cas_does_not_list_is_unknown_and_a_refused_run_asks_for_nothing():
    cas = casl_harness.FakeCAS(refuse=("table.tableInfo",))
    cas.table("Public", FOUR[0], ["version"], rows=707)
    before, _ = tables(cas, "before")
    assert before.split() == ["unknown/"] * 4

    cas = casl_harness.FakeCAS()
    generated, log, macros = casl_harness.follow(program(), "_lat_tables", {"_lat_ready": "0", "caslib": "Public",
                                                                           "releaseTable": FOUR[0], "_lat_when": "before",
                                                                           "_lat_before": "", "_lat_after": ""}, cas)
    assert generated == [] and log == [] and cas.calls == [] and macros["_lat_before"] == ""


def test_the_two_reads_stand_around_the_loads_and_inside_the_cas_session():
    text = program()
    order = ["cas _latSess;", "%_lat_tables(before);", "%_lat_release_table;", "%include _latlfs;", "%_lat_look;",
             "%_lat_tables(after);", "%sysmacdelete _lat_tables;", "%_lat_page;", "%sysmacdelete _lat_page;",
             "cas _latSess terminate;", "%let SYSCC = 1012;"]
    positions = [text.index(marker) for marker in order]
    assert positions == sorted(positions)
    assert "%let _lat_before = ;\n%let _lat_after = ;\n" in text
    assert re.search(r"%symdel [^;]*\b_lat_before _lat_after / nowarn;", text)


# ---------------------------------------------------------------------------
# Where the page is written
# ---------------------------------------------------------------------------

def test_a_session_without_webout_writes_no_page_and_no_note():
    """The program run in SAS Studio, and the job run through the jobExecution service (measured on 2026-10-20)."""
    assert page(1, _output_type="html") == ([], [])
    assert page(1) == ([], [])


def test_a_job_definition_without_output_type_html_writes_no_page_and_says_so():
    note = ["NOTE: Load-Accelerator-Tables: no page was written to _webout, because the job definition has no parameter "
            "_output_type with the value html. The lines above say what was loaded."]
    assert page(0) == ([], note)
    assert page(0, _output_type="ods_html5") == ([], note)


@pytest.mark.parametrize("value", ["html", "HTML"])
def test_the_page_is_written_to_webout(value):
    generated, log = page(0, _output_type=value)

    assert log == [] and generated[:2] == ["data _null_;", "file _webout lrecl=32767;"] and generated[-1] == "run;"


def test_the_manifest_gives_the_job_definition_the_parameter():
    text = MANIFEST.read_text(encoding="utf-8")
    block = text.split("  - path: Data/Load-Accelerator-Tables\n")[1].split("\n  - path: ")[0].split("\n\n")[0]

    assert "      - {name: _output_type, type: character, default: html}\n" in block + "\n"
    assert block.index("{name: _action,") < block.index("{name: _output_type,")


# ---------------------------------------------------------------------------
# What the page says
# ---------------------------------------------------------------------------

def page_step():
    text = macro("_lat_page")
    return text[text.index("    data _null_;"):text.index("    run;\n", text.index("    data _null_;"))]


def test_every_text_of_the_page_is_a_literal_sas_reads_without_a_note():
    """A quoted string of more than 262 bytes makes SAS write a NOTE about unbalanced quotation marks."""
    literals = re.findall(r"'([^']*)'", page_step())
    assert literals and max(len(literal.encode("utf-8")) for literal in literals) <= 262


def test_the_page_names_each_table_with_its_rows_and_what_this_run_did_with_it():
    step = page_step()
    html = "".join(re.findall(r"'([^']*)'", step))

    for tag in ("html", "head", "style", "body", "main", "table", "thead", "tbody"):
        assert html.count(f"<{tag}") == html.count(f"</{tag}>") == 1, tag
    assert "<title>SAS Agentic AI Accelerator - Load Accelerator Tables</title>" in html and "<h1>Load Accelerator Tables</h1>" in html
    assert "<th>Table</th><th>Rows</th><th>This run</th>" in html
    # what the run did, from the two reads: not there, not listed, loaded, left as it was
    in_order = ["if _lat_rows = 'unknown' then _lat_state = 'CAS did not list the tables of the CAS library';",
                "else if _lat_rows = 'none' then _lat_state = 'Not in the CAS library';",
                "else if scan(_lat_before, 1, '/') = 'unknown' then do;",
                "else if _lat_after NE _lat_before then do;", "_lat_state = 'Loaded by this run';",
                "_lat_state = 'Left as it was';"]
    positions = [step.index(statement) for statement in in_order]
    assert positions == sorted(positions)
    # the summary: the four tables there, or the ones that are not; a refused parameter
    assert "'<p class=\"summary\">The four tables are in the CAS library '" in step
    assert "'<p class=\"summary\">The job failed: not in the CAS library '" in step
    assert ("put '<p class=\"summary\">A parameter of the job was refused, so nothing was read or written. The ERROR line of the "
            "log names it.</p>';") in step
    # a value of a parameter reaches the page through HTMLENCODE
    assert "_lat_lib = htmlencode(strip(symget('caslib')));" in step and "strip(htmlencode(strip(_lat_name)))" in step
    assert "htmlencode(strip(symget('_lat_missing')))" in step
    # the four tables in the order of the check at the end
    assert "scan(catx(' ', symget('releaseTable'), 'LLM_FACT_SHEET EMBEDDING_FACT_SHEET DECISION_FACT_SHEET'), _lat_i, ' ')" in step


def test_the_readmes_and_the_guide_say_what_the_page_shows():
    readme = " ".join((SETUP / "README.md").read_text(encoding="utf-8").split())
    guide = " ".join((REPOSITORY / "website" / "docs" / "Administration-Guide" / "Setup-Additional-UIs.md").read_text(encoding="utf-8").split())

    for text in (readme, guide):
        assert "Started from its address in SAS Job Execution, the job answers with a page" in text
        assert "*Loaded by this run*, *Left as it was* or *Not in the CAS library*" in text
        assert "SAS Job Execution answers a run that fails with its own page *Job Error*" in text
    assert "| `_output_type` | `html` |" in readme
