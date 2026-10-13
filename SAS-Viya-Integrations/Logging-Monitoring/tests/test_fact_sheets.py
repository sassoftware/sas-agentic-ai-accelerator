"""Load-Fact-Sheets.sas: a table is only replaced by a sheet that was read and that CAS held.

The program dropped each of its three tables and loaded the sheet behind it. A sheet that
was not in the folder, or that CAS did not take, left the site without the table it had.
Each sheet is now read and tried in CAS first, beside the table, and only a sheet CAS held
completely takes its place - the upload whose rows were counted, promoted under the name of
the table, as Load-Accelerator-Tables does it for the release table; the sheet is not loaded
a second time.

The SAS code can not run here. The tests read it as text: the statements and their order.
"""
import re
from pathlib import Path

import pytest

FOLDER = Path(__file__).resolve().parents[1]
REPOSITORY = FOLDER.parents[1]

SHEETS = [
    ("llm_fact_sheet.csv", "_lfs_llm_fact_sheet", "LLM_FACT_SHEET", "LLM-Definitions"),
    ("embedding_fact_sheet.csv", "_lfs_embedding_fact_sheet", "EMBEDDING_FACT_SHEET", "Embedding-Definitions"),
    ("decision_fact_sheet.csv", "_lfs_decision_fact_sheet", "DECISION_FACT_SHEET", "Decision-Definitions"),
]


def program(name):
    return (FOLDER / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def without_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return "\n".join(line for line in text.split("\n") if not line.lstrip().startswith("*"))


def in_order(text, *markers):
    positions = [text.index(marker) for marker in markers]
    assert positions == sorted(positions), [marker for _, marker in sorted(zip(positions, markers))]


def read_macro():
    code = without_comments(program("Load-Fact-Sheets.sas"))
    return code[code.index("%macro _lfs_read("):code.index("%mend _lfs_read;")]


def swap_macro():
    code = without_comments(program("Load-Fact-Sheets.sas"))
    return code[code.index("%macro _lfs_swap("):code.index("%mend _lfs_swap;")]


# ---------------------------------------------------------------------------
# Load-Fact-Sheets.sas
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("file, work, table, folder", SHEETS, ids=[sheet[2] for sheet in SHEETS])
def test_a_table_is_only_dropped_for_a_sheet_that_cas_held_completely(file, work, table, folder):
    code = without_comments(program("Load-Fact-Sheets.sas"))
    block = f"%_lfs_read({file}, {work}, {table});\n%_lfs_swap({table});\n"

    assert code.count(block) == 1
    # the table is named nowhere else: no statement outside the two macros drops, loads, promotes or saves it
    assert code.count(table) == 2 and code.count(work) == 2
    assert "casData=" not in code and "proc casUtil" not in code.replace(read_macro(), "")
    # the file the program reads is the fact sheet the repository holds for that kind of model
    assert (REPOSITORY / folder / file).is_file()


def test_the_three_sheets_are_read_one_after_the_other_and_none_stops_the_others():
    code = without_comments(program("Load-Fact-Sheets.sas"))
    calls = re.findall(r"^%_lfs_read\((\S+), (\w+), (\w+)\);$", code, flags=re.MULTILINE)

    assert calls == [sheet[:3] for sheet in SHEETS]
    macro = read_macro()
    # a sheet that is not loaded is a WARNING that names the file or the rows and the table that stays
    warnings = re.findall(r"%put (WARNING: [^;]*);", macro)
    assert len(warnings) == 3 and "ERROR" not in macro.upper().replace("NOWARN", "")
    for warning in warnings:
        assert warning.endswith("the table &_lfs_caslib..&_lfs_table. was left as it is.") or \
            warning.endswith("The table &_lfs_caslib..&_lfs_table. was left as it is.")
        assert not set(warning) & set("%'\"") and warning.count("(") == warning.count(")")
    # no ABORT and no statement that ends the program: the next sheet is read whatever became of this one
    assert not re.search(r"\babort\b|%abort|endsas", code, flags=re.IGNORECASE)
    assert macro.count("%return;") == 2


def test_a_sheet_is_read_and_tried_in_cas_before_its_table_is_touched():
    macro = read_macro()

    # nothing of an earlier run or of the sheet in front stands in for this sheet
    in_order(macro, "%let _lfs_ok = 0;", "delete &_lfs_work.;", 'filename _lfs_fs filesrvc folderPath="&_lfs_path." filename="&_lfs_file.";')
    # a file that is not there is not handed to PROC IMPORT, which would end with an error
    in_order(macro, "%if %sysfunc(fexist(_lfs_fs)) NE 1 %then %do;", "%return;", "proc import dataFile=_lfs_fs")
    assert "out=work.&_lfs_work." in macro and "guessingRows=max;" in macro and "getNames=yes;" in macro
    # its rows are counted, and a sheet without rows goes no further
    in_order(macro, "proc import dataFile=_lfs_fs", "%let _lfs_rows = %sysfunc(attrn(&_lfs_dsid., nlobs));", "%if &_lfs_rows. LE 0 %then %do;",
             'load data=work.&_lfs_work. casOut="&_lfs_table._NEW" replace;')
    # it is loaded beside the table, under another name, counted there and dropped again
    in_order(macro, 'load data=work.&_lfs_work. casOut="&_lfs_table._NEW" replace;',
             'table.tableExists result = found / caslib = "&_lfs_caslib." name = "&_lfs_table._NEW";',
             'if upcase(entry.Name) == upcase("&_lfs_table._NEW") then staged = entry.Rows;',
             'table.dropTable / caslib = "&_lfs_caslib." name = "&_lfs_table._NEW" quiet = true;',
             'call symputx("_lfs_staged", staged, "L");',
             "%if &_lfs_staged. EQ &_lfs_rows. %then %do;", "%let _lfs_ok = 1;")
    # the table itself is named nowhere in the macro: it only tries the sheet
    assert not re.search(r'name = "&_lfs_table\."', macro) and "promote" not in macro.lower() and "save " not in macro.lower()
    assert macro.count("%let _lfs_ok = 1;") == 1 and macro.count("%do") == macro.count("%end")
    assert macro.count("proc casUtil") == 1 and macro.count("proc cas;") == 1 and macro.count("proc import") == 1


def test_the_counted_upload_takes_the_place_of_the_table_and_nothing_loads_the_sheet_again():
    """Integrate-18 open item 5: the program tried each sheet beside its table, dropped that
    trial, and then dropped the table and loaded the sheet a second time - a failure of CAS
    between the drop and the second load left the site without that table, and the load that
    took its place was not the one whose rows were counted. The counted upload is now kept and
    promoted under the name of the table, the statements Load-Accelerator-Tables ran with on
    2026-10-12 for the release table; a refused promote loads the table again from its file."""
    read, swap = read_macro(), swap_macro()

    # the trial is dropped only where it does not hold every row
    in_order(read, "if staged != &_lfs_rows. then do;",
             'table.dropTable / caslib = "&_lfs_caslib." name = "&_lfs_table._NEW" quiet = true;',
             'call symputx("_lfs_staged", staged, "L");')
    # nothing happens for a sheet that was not read completely
    in_order(swap, "%if &_lfs_ok. NE 1 %then %return;", "proc cas;")
    # the table is dropped, the counted upload promoted under its name and saved; no load of the sheet
    in_order(swap, 'table.dropTable status = dropped / caslib = "&_lfs_caslib." name = "&_lfs_table." quiet = true;',
             'table.promote status = promoted / caslib = "&_lfs_caslib." name = "&_lfs_table._NEW"',
             'targetLib = "&_lfs_caslib." target = "&_lfs_table.";', "if promoted.severity == 0 then do;",
             'table.save status = stored / table = {caslib = "&_lfs_caslib.", name = "&_lfs_table."}',
             'caslib = "&_lfs_caslib." name = "&_lfs_table." replace = true;', 'if stored.severity == 0 then outcome = "loaded";',
             "else do;", 'table.dropTable / caslib = "&_lfs_caslib." name = "&_lfs_table._NEW" quiet = true;',
             'table.loadTable status = restored / caslib = "&_lfs_caslib." path = "&_lfs_table..sashdat"',
             'casOut = {caslib = "&_lfs_caslib.", name = "&_lfs_table.", promote = true};',
             'call symputx("_lfs_outcome", outcome, "L");', "run; quit;")
    assert "load data=" not in swap and "work." not in swap and swap.count("proc cas;") == 1
    # every outcome but a saved table is a WARNING that says what the table holds; none is an ERROR or ends the program
    warnings = re.findall(r"%put (WARNING: [^;]*);", swap)
    assert len(warnings) == 3 and "ERROR" not in swap.upper()
    for warning in warnings:
        assert "&_lfs_caslib..&_lfs_table." in warning and not set(warning) & set("%'\"")
    # one test for a sheet that was not read, and one for each WARNING
    assert swap.count("%if") == swap.count("%put") + 1 and swap.count("%else %if") == 2


def test_an_error_for_one_sheet_does_not_put_a_batch_session_into_syntax_check_mode():
    """Behind an error a batch session checks syntax only and runs no further step - measured on
    2026-10-12 for the first DATA step behind a program that ended with an error condition. The
    sheets behind a sheet that CAS refused would not be read. The option that decides it is off
    while the program runs and is put back to what it was, as the ingestion job of a RAG setup
    does it while it publishes its run history."""
    code = without_comments(program("Load-Fact-Sheets.sas"))

    assert code.count("%let _lfs_syntaxcheck = %sysfunc(getoption(syntaxcheck));\noptions nosyntaxcheck;\n") == 1
    assert code.count("options &_lfs_syntaxcheck.;") == 1 and len(re.findall(r"^\s*options\b", code, flags=re.MULTILINE)) == 2
    # switched off in front of the first step of the program and put back behind its last one
    in_order(code, "options nosyntaxcheck;", "cas mySess;", "%macro _lfs_read(", "%_lfs_read(llm_fact_sheet.csv",
             "delete _lfs_llm_fact_sheet _lfs_embedding_fact_sheet _lfs_decision_fact_sheet;", "cas mySess terminate;",
             "options &_lfs_syntaxcheck.;")
    # the condition code is left as the program found or made it: an error stays an error of the job
    assert not re.search(r"syscc", code, flags=re.IGNORECASE)


def test_the_macro_and_its_flag_are_gone_at_the_end():
    text = program("Load-Fact-Sheets.sas")
    tail = text[text.rindex("* Clean up;"):]

    assert "delete _lfs_llm_fact_sheet _lfs_embedding_fact_sheet _lfs_decision_fact_sheet;" in tail
    in_order(tail, "cas mySess terminate;", "%sysmacdelete _lfs_read;", "%sysmacdelete _lfs_swap;", "options &_lfs_syntaxcheck.;",
             "%symdel _lfs_ok _lfs_syntaxcheck / nowarn;", "%symdel _lfs_path _lfs_caslib _lfs_clean_cas_session;")
    # the header says what the program does with a table that exists, and who includes the file
    head = text.split("*/")[0]
    assert "A table that exists is only replaced by a sheet that was read" in head
    assert "The job definition Load-Accelerator-Tables includes this file from the" in head
