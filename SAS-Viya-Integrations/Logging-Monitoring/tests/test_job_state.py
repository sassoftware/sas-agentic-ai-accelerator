"""How a run ends that wrote an ERROR line and no table: Log-Parser-Code.sas, Get-All-Prompts.sas,
Link-Logs-To-Models.sas and the two custom steps.

None of the three raised a SAS error when it refused a table or could not read its input, so
SAS Job Execution reported the job as completed and a schedule saw no failure. Each program
now sets SYSCC as its last statement, the way Ingest-Documents.sas ends a failed run - for
every path that writes an ERROR line and writes no table, and for no other.

The SAS code can not run here. The tests read it as text, and run the Python of each program
for the paths the Python decides.
"""
import ast
import re
from pathlib import Path

import pandas as pd
import pytest

import get_all_prompts_harness as pull
import link_logs_harness as link
import log_parser_harness as parser

INTEGRATIONS = Path(__file__).resolve().parents[2]
INGESTION_JOB = INTEGRATIONS / "RAG-Ingestion" / "Ingest-Documents.sas"

PROGRAMS = {
    "Log-Parser-Code.sas": parser.code_file_program,
    "LLM - Log Parser.step": parser.step_program,
    "Get-All-Prompts.sas": pull.code_file_program,
    "LLM - Get All Prompts.step": pull.step_program,
    "Link-Logs-To-Models.sas": link.code_file_program,
}
ALL_PROGRAMS = pytest.mark.parametrize("name", list(PROGRAMS))

# The condition under which each program ends as failed
CONDITIONS = {
    "Log-Parser-Code.sas": "&_log_row_count. LT 0 or &_log_failed. EQ 1",
    "LLM - Log Parser.step": "&_log_row_count. LT 0 or &_log_failed. EQ 1",
    "Get-All-Prompts.sas": "&_gap_failed. EQ 1",
    "LLM - Get All Prompts.step": "&_gap_failed. EQ 1",
    "Link-Logs-To-Models.sas": "&_lnk_row_count. LT 0 or &_lnk_failed. EQ 1",
}

# Every statement of the SAS code that writes an ERROR line, by how its message starts, and the statement
# behind it that makes the run end as failed. None stands for a line that is only written where the row
# count of the program is below 0, which is the other half of the condition.
ERROR_LINES = {
    "Log-Parser-Code.sas": {
        "putLog 'ERROR: For this step to run please ensure the the selected file is located on the SAS Server.'": "call symputx('_log_ready', '0');",
        "%put ERROR: The table &_log_target_table_lib..&_log_target_table_name. is not a CAS table.": "%let _log_ready = 0;",
        'print (error) "The saved table &_log_target_table_lib..&_log_target_table_name. could not be loaded': "call symputx('_log_failed', '1');",
        'print (error) "The new table could not be created, the table &_log_target_table_lib..&_log_target_table_name. was left as it is. The DATA step': "call symputx('_log_failed', '1');",
        'print (error) "The new table could not be created, the table &_log_target_table_lib..&_log_target_table_name. was left as it is.";': "call symputx('_log_failed', '1');",
        "%put ERROR: The log could not be parsed": None,
    },
    "Get-All-Prompts.sas": {
        "%put ERROR: The table &_gap_out_caslib..&_gap_name. was not written.": "%let _gap_failed = 1;",
        "%put ERROR: The experiments could not be read from SAS Model Manager": "%let _gap_failed = 1;",
    },
    "Link-Logs-To-Models.sas": {
        "%put ERROR: The log table &_lnk_log_caslib..&_lnk_log_table. was not found.": "%let _lnk_ready = 0;",
        "%put ERROR: The catalog table &_lnk_catalog_caslib..&_lnk_catalog_table. was not found.": "%let _lnk_ready = 0;",
        'print (error) "The new table could not be created': "call symputx('_lnk_failed', 1);",
        "%put ERROR: The rows could not be linked": None,
    },
}
ERROR_LINES["LLM - Log Parser.step"] = ERROR_LINES["Log-Parser-Code.sas"]
ERROR_LINES["LLM - Get All Prompts.step"] = dict(
    {"%put ERROR: The selected output library does not point to a CASlib, nothing was read or written.": "%let _gap_failed = 1;"},
    **ERROR_LINES["Get-All-Prompts.sas"])
WRITES_AN_ERROR = re.compile(r"%put ERROR:|putlog\s+['\"]ERROR:|print \(error\)", re.IGNORECASE)


def sas_code(program):
    """A program without its Python."""
    return program[:program.index("submit;")] + program[program.index("endsubmit;"):]


def condition_code_of_the_ingestion_job():
    """What Ingest-Documents.sas sets SYSCC to for a run that failed."""
    found = re.findall(r"%if &_rag_rc\. NE 0 %then %let SYSCC = (\d+);", INGESTION_JOB.read_text(encoding="utf-8"))
    assert len(found) == 1
    return found[0]


def ends_as_failed(name, **macro_variables):
    """Whether a program ends with the condition code, for the values its macro variables have at its end."""
    expression = CONDITIONS[name]
    for variable, value in macro_variables.items():
        expression = expression.replace(f"&{variable}.", str(int(value)))
    assert "&" not in expression, expression
    return eval(expression.replace(" LT ", " < ").replace(" EQ ", " == "))


# ---------------------------------------------------------------------------
# The statement, read from the programs
# ---------------------------------------------------------------------------

@ALL_PROGRAMS
def test_program_ends_with_the_condition_code_of_a_failed_ingestion(name):
    code = sas_code(PROGRAMS[name]())
    block = f"%if {CONDITIONS[name]} %then %do;\n    %let SYSCC = {condition_code_of_the_ingestion_job()};\n%end;\n"

    assert condition_code_of_the_ingestion_job() == "1012"
    assert code.count(block) == 1
    assert len(re.findall(r"syscc\s*=", code, flags=re.IGNORECASE)) == 1
    # It is the last statement: behind it only the macro variables are deleted
    behind = [line.strip() for line in code.split(block)[1].split(";") if line.strip()]
    assert behind and all(statement.startswith("%symdel ") for statement in behind), behind
    # In front of it the program has done everything else: the output is handled and the session it started is ended
    front = code.split(block)[0]
    for done in {"Log": ("%_log_handle_output_table;", "cas llmLogMySess terminate;"), "Get": ("%_gap_write_tables;", "terminate;"),
                 "Lin": ("%_lnk_handle_output_table;", "cas lnkMySess terminate;"), "LLM": ("%end;",)}[name[:3]]:
        assert done in front, done
    # The comment in front of it is a statement of its own and holds nothing SAS would read as code
    comment = front.rstrip().split("\n")[-1]
    assert comment.startswith("* A ") and comment.endswith(";") and not re.search(r"[%&'\";]", comment[:-1])


@ALL_PROGRAMS
def test_every_error_line_of_the_sas_code_makes_the_run_end_as_failed(name):
    """The list above is every statement that writes an ERROR line. A new one fails this test until it is
    decided how its path ends."""
    code = sas_code(PROGRAMS[name]())
    lines = code.split("\n")
    writing = [index for index, line in enumerate(lines) if WRITES_AN_ERROR.search(line) and not line.lstrip().startswith(("*", "/*"))]
    expected = ERROR_LINES[name]

    found = {}
    for index in writing:
        starts = [start for start in expected if lines[index].strip().startswith(start)]
        assert starts, f"an ERROR line this test does not know: {lines[index].strip()[:120]}"
        # The longest start is the line: two of the parser begin alike
        found[max(starts, key=len)] = index
    assert set(found) == set(expected)
    for start, index in found.items():
        behind = [line.strip() for line in lines[index + 1:index + 8]]
        if expected[start] is not None:
            assert expected[start] in behind, start
        else:
            # Written in the branch that is left for a row count below 0
            above = [line.strip() for line in lines[max(0, index - 12):index]]
            row_count = "_log_row_count" if name.startswith(("Log", "LLM - Log")) else "_lnk_row_count"
            assert [line for line in above if line.startswith("%else %if") and f"&{row_count}. EQ 0" in line or f"&{row_count}. LT 0" in line], start
            assert f"%let {row_count} = -1;" in code


@ALL_PROGRAMS
def test_nothing_but_an_error_line_sets_what_the_condition_reads(name):
    """A WARNING, a NOTE, a log without requests, a run with nothing to link and a setup that is left out
    keep the condition code as it was: what the last statement reads is set where an ERROR line is written
    and nowhere else."""
    code = sas_code(PROGRAMS[name]())
    flag = {"Log": "_log_failed", "LLM - Log": "_log_failed", "Get": "_gap_failed", "LLM - Get": "_gap_failed", "Lin": "_lnk_failed"}[
        next(start for start in ("LLM - Log", "LLM - Get", "Log", "Get", "Lin") if name.startswith(start))]
    setting = [line.strip() for line in code.split("\n") if re.search(rf"%let {flag}\s*=|symputx\('{flag}'", line)]
    with_a_flag = [statement for statement in ERROR_LINES[name].values() if statement and flag in statement]

    assert setting[0] == f"%let {flag} = 0;" and setting.count(f"%let {flag} = 0;") == 1
    assert sorted(setting[1:]) == sorted(with_a_flag)
    # No line that writes a NOTE or a WARNING stands in front of a statement that sets it
    lines = [line.strip() for line in code.split("\n")]
    for index, line in enumerate(lines):
        if flag in line and (line.startswith("%let") or "symputx" in line) and not line.endswith("= 0;"):
            before = " ".join(lines[max(0, index - 6):index])
            assert WRITES_AN_ERROR.search(before), line
    assert flag in next(line for line in reversed(lines) if line.startswith(("%symdel", "_gap_out_catalog")) and flag in line)


def test_the_step_of_the_pull_refuses_a_library_that_is_no_cas_library_without_an_abort():
    """The step wrote its ERROR line in a DATA step and stopped it with ABORT, which left SAS in syntax
    check mode for everything behind it. It now says so in one line and sets what the last statement reads."""
    head = pull.step_program().split(pull.core(pull.step_program()))[0]

    assert "abort" not in head.lower() and "data _null_" not in head.lower()
    assert head.index("%let _gap_failed = 0;") < head.index("%let _gap_failed = 1;")


# ---------------------------------------------------------------------------
# What the Python leaves behind, for the paths it decides
# ---------------------------------------------------------------------------

def test_parser_row_count_stays_below_zero_for_a_refusal_and_for_a_log_that_could_not_be_read(monkeypatch, tmp_path):
    name = "Log-Parser-Code.sas"
    assert "%let _log_row_count = -1;" in sas_code(PROGRAMS[name]())
    log = tmp_path / "llms.log"
    log.write_text(parser.as_log(parser.llm_block()), encoding="utf-8", newline="")

    # A run that was refused, and a file that is not there
    refused = parser.run_parser(monkeypatch, log, ready="0")
    assert ends_as_failed(name, _log_row_count=refused.row_count, _log_failed=0)
    with pytest.raises(FileNotFoundError) as unread:
        parser.run_parser(monkeypatch, tmp_path / "not there.log")
    assert ends_as_failed(name, _log_row_count=unread.value.sas.macro_variables["_log_row_count"], _log_failed=0)

    # A log with requests, a log without, and a log whose requests could not be read, which is a WARNING
    assert not ends_as_failed(name, _log_row_count=parser.run_parser(monkeypatch, log).row_count, _log_failed=0)
    empty = tmp_path / "empty.log"
    empty.write_text("", encoding="utf-8")
    assert parser.run_parser(monkeypatch, empty).row_count == "0"
    assert not ends_as_failed(name, _log_row_count=0, _log_failed=0)
    prefixed = tmp_path / "prefixed.log"
    prefixed.write_text(parser.as_log(["[pod] " + line for line in parser.llm_block()]), encoding="utf-8", newline="")
    warned = parser.run_parser(monkeypatch, prefixed)
    assert [kind for kind, _ in warned.sas.messages] == ["WARNING"]
    assert not ends_as_failed(name, _log_row_count=warned.row_count, _log_failed=0)
    # A table that could not be written
    assert ends_as_failed(name, _log_row_count=1, _log_failed=1)


def test_parser_sets_its_row_count_as_the_last_statement_of_a_run_that_was_not_refused():
    tree = ast.parse(parser.python_block(parser.code_file_program()))
    guarded = tree.body[-1]

    assert isinstance(guarded, ast.If) and ast.unparse(guarded.test) == "SAS.symget('_log_ready') == '1'" and not guarded.orelse
    assert ast.unparse(guarded.body[-1]) == "put_number('_log_row_count', len(df))"
    assert ast.unparse(tree).count("'_log_row_count'") == 1


def test_link_row_count_stays_below_zero_for_every_run_that_wrote_an_error_line(monkeypatch):
    name = "Link-Logs-To-Models.sas"
    assert "%let _lnk_row_count = -1;" in sas_code(PROGRAMS[name]())
    catalog = link.catalog_table(link.answer_model("demo_llm"))
    tables = {("Public", "LLM_LOGS"): link.log_table(link.llm_row()), ("Public", "MODEL_CATALOG"): catalog}

    def left(run):
        return {key: run.sas.macro_variables[key] for key in ("_lnk_row_count", "_lnk_unchanged", "_lnk_refused")}

    # A table that was not found: the SAS code wrote the line, the Python links nothing
    not_ready = link.run_program(monkeypatch)
    assert left(not_ready)["_lnk_row_count"] == "-1" and ends_as_failed(name, _lnk_row_count=-1, _lnk_failed=0)
    # A table the Python refused: one written by a parser of before the link columns
    old = tables[("Public", "LLM_LOGS")][["timestamp", "model", "system_prompt", "user_prompt"]]
    refused = link.run_program(monkeypatch, {("Public", "LLM_LOGS"): old, ("Public", "MODEL_CATALOG"): catalog}, _lnk_runs_exists="0")
    assert len(refused.sas.lines("ERROR")) == 1 and left(refused) == {"_lnk_row_count": "-1", "_lnk_unchanged": "0", "_lnk_refused": "1"}
    assert ends_as_failed(name, _lnk_row_count=left(refused)["_lnk_row_count"], _lnk_failed=0)

    # A run that links, a run with a WARNING, and a run that finds nothing to link
    linked = link.run_program(monkeypatch, tables, _lnk_runs_exists="0")
    assert left(linked) == {"_lnk_row_count": "1", "_lnk_unchanged": "0", "_lnk_refused": "0"}
    assert not ends_as_failed(name, _lnk_row_count=1, _lnk_failed=0)
    warned = link.run_program(monkeypatch, tables, _lnk_runs_exists="0", _lnk_query_window="soon")
    assert len(warned.sas.lines("WARNING")) == 1 and not ends_as_failed(name, _lnk_row_count=left(warned)["_lnk_row_count"], _lnk_failed=0)
    again = link.run_program(monkeypatch, {("Public", "LLM_LOGS"): linked.new_table, ("Public", "MODEL_CATALOG"): catalog}, _lnk_runs_exists="0")
    assert left(again) == {"_lnk_row_count": "1", "_lnk_unchanged": "1", "_lnk_refused": "0"}
    assert not ends_as_failed(name, _lnk_row_count=1, _lnk_failed=0)
    # A new table that could not be put in the place of the log table
    assert ends_as_failed(name, _lnk_row_count=1, _lnk_failed=1)


def test_pull_leaves_its_return_code_below_zero_behind_every_error_line_of_the_python(monkeypatch, tmp_path):
    """Each ERROR line of the Python is followed by its return, in front of the statement that sets _gap_rc
    to 0. The SAS code then writes its own ERROR line and sets what the last statement reads."""
    tree = ast.parse(pull.python_block(pull.code_file_program()))
    run_pull = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "run_pull")
    errors = 0
    for node in ast.walk(run_pull):
        body = getattr(node, "body", [])
        for position, statement in enumerate(body if isinstance(body, list) else []):
            if isinstance(statement, ast.Expr) and ast.unparse(statement).startswith("say('ERROR', "):
                errors += 1
                assert isinstance(body[position + 1], ast.Return)
    assert errors == 4
    assert ast.unparse(run_pull.body[-1]) == "hand_over('_gap_rc', '0')"
    code = sas_code(pull.code_file_program())
    assert "%let _gap_rc = -1;" in code
    assert ("%if &_gap_rc. NE 0 %then %do;\n        %put ERROR: The experiments could not be read from SAS Model Manager, the output tables in "
            "&_gap_out_caslib. were left as they are.;\n        %let _gap_failed = 1;\n        %return;\n    %end;") in code

    # A table name that is refused, and a pull with nothing but WARNINGs
    assert pull.run_pull(monkeypatch, tmp_path, settings={"_gap_out_catalog": "x" * 33}).rc == "-1"
    viya = pull.FakeViya()
    setups = viya.add_project("Setups", tags=["RAG-Engineering"])
    viya.add_model(setups, "RAG Retrieval - without a collection", algorithm="RAG")
    warned = pull.run_pull(monkeypatch, tmp_path, viya=viya)
    assert warned.sas.of_type("WARNING") and warned.sas.of_type("ERROR") == [] and warned.rc == "0"
