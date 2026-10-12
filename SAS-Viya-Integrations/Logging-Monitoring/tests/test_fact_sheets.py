"""Load-Fact-Sheets.sas: a table is only replaced by a sheet that was read and that CAS held.

The program dropped each of its three tables and loaded the sheet behind it. A sheet that
was not in the folder, or that CAS did not take, left the site without the table it had.
Each sheet is now read and tried in CAS first, beside the table, and only a sheet CAS held
completely takes its place.

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


# ---------------------------------------------------------------------------
# Load-Fact-Sheets.sas
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("file, work, table, folder", SHEETS, ids=[sheet[2] for sheet in SHEETS])
def test_a_table_is_only_dropped_for_a_sheet_that_cas_held_completely(file, work, table, folder):
    code = without_comments(program("Load-Fact-Sheets.sas"))
    block = (f"%_lfs_read({file}, {work}, {table});\n"
             "%if &_lfs_ok. EQ 1 %then %do;\n"
             '    proc casUtil inCASLib="&_lfs_caslib." outCASLib="&_lfs_caslib.";\n'
             f"        dropTable casData='{table}' quiet;\n"
             f"        load data=work.{work} casOut='{table}';\n"
             f"        promote casData='{table}' casOut='{table}';\n"
             f"        save casData='{table}' casOut='{table}' replace;\n"
             "    run; quit;\n"
             "%end;\n")

    assert code.count(block) == 1
    # the table is named in no other statement that drops, loads, promotes or saves
    assert code.count(f"casData='{table}'") == 3 and code.count(f"casOut='{table}'") == 3
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


def test_the_macro_and_its_flag_are_gone_at_the_end():
    text = program("Load-Fact-Sheets.sas")
    tail = text[text.rindex("* Clean up;"):]

    assert "delete _lfs_llm_fact_sheet _lfs_embedding_fact_sheet _lfs_decision_fact_sheet;" in tail
    in_order(tail, "cas mySess terminate;", "%sysmacdelete _lfs_read;", "%symdel _lfs_ok / nowarn;",
             "%symdel _lfs_path _lfs_caslib _lfs_clean_cas_session;")
    # the header says what the program does with a table that exists, and who includes the file
    head = text.split("*/")[0]
    assert "A table that exists is only replaced by a sheet that was read" in head
    assert "The job definition Load-Accelerator-Tables includes this file from the" in head
