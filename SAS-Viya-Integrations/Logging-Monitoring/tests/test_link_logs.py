"""Link-Logs-To-Models.sas: every log row gets the model that answered and, where the
content of the request shows it, the prompt, decision template or RAG setup that made it.

Run with: python -m pytest SAS-Viya-Integrations/Logging-Monitoring/tests
"""
import ast
import json
import math
import re

import pandas as pd
import pytest

from link_logs_harness import (
    LINK_COLUMNS,
    LOG_COLUMNS,
    OLD_COLUMNS,
    PARSER_COLUMNS,
    QUESTIONS,
    QUESTIONS_AS_SENT,
    FakeCAS,
    answer_model,
    catalog_row,
    catalog_table,
    code_file_program,
    decision_row,
    embedding_row,
    envelope,
    experiment_run,
    functions,
    ingestion_run,
    judge_call,
    llm_row,
    log_table,
    lower_case,
    optimization_call,
    prompt_template,
    python_block,
    rag_setup,
    run_program,
    runs_table,
    sas_datetime,
    unsafe_for_sas,
    uuid,
)

# The models that answer in these tests
LLM = answer_model("demo_llm", "llm", uuid(1))
DECIDER = answer_model("demo_decider", "decision_model", uuid(2))
EMBEDDER = answer_model("demo_embedder", "embedding", uuid(3))

# A prompt that is fed by a retrieval
LEAVE_SYSTEM = "You answer questions of employees about the staff handbook.\nAnswer in one sentence and name the chapter."
LEAVE_USER = "Question: {{question}}\n\nPassages:\n{{passages}}\n\nAnswer:"
LEAVE_PROMPT = prompt_template(uuid(10), "Leave desk", LEAVE_SYSTEM, LEAVE_USER)
QUESTION = "How many days of leave does a new employee get?"
HANDBOOK = rag_setup(uuid(30), "Staff handbook", "handbook_v1")

# A decision template
TICKET_STATE = "Ticket: {{ticket}}"
TICKET_TEMPLATE = prompt_template(uuid(20), "Ticket routing", json.dumps(QUESTIONS, indent=2), TICKET_STATE, role="decision")

CALLER_COLUMNS = ["caller_model_id", "caller_project_id", "caller_kind", "caller_name", "experiment_run_id"]


def missing(value):
    return value is None or (isinstance(value, float) and math.isnan(value))


@pytest.fixture
def program(monkeypatch):
    """The functions of the program."""
    return functions(monkeypatch)


def link(program, rows, catalog, runs=None, agent=None, **settings):
    """Links made-up log rows to made-up catalog rows and returns the link columns, one dict per row.

    agent  the user agent of every row: "browser" makes them requests of the Builder page. The
           builders write python-requests, the user agent of the score code of a published model.
    """
    if agent is not None:
        rows = [dict(row, user_agent=agent) for row in rows]
    links, _ = program["link_rows"](lower_case(log_table(*rows)), lower_case(catalog_table(*catalog)),
                                   None if runs is None else lower_case(runs_table(*runs)), **settings)
    assert list(links.columns) == LINK_COLUMNS
    assert len(links) == len(rows)
    return links.to_dict("records")


def leave_request(question=QUESTION, collection="handbook_v1", **values):
    """The request the Leave desk prompt sends: its template with a question and the envelope of a retrieval."""
    user_prompt = LEAVE_USER.replace("{{question}}", question).replace("{{passages}}", envelope(collection, question))
    return llm_row(LEAVE_SYSTEM, user_prompt, retrieval=(collection, question), **values)


# ---------------------------------------------------------------------------
# The program as text
# ---------------------------------------------------------------------------

def test_settings_are_macro_variables_with_the_default_tables():
    program = code_file_program()
    top = program[:program.index("proc cas;")]
    # no LET statement at the top sets a setting over a value that is there
    assert dict(re.findall(r"^%let (_lnk_\w+) = (.*);$", top, flags=re.MULTILINE)) == {}
    settings = dict(re.findall(r"^%_lnk_default\((_lnk_\w+), (.*)\);$", top, flags=re.MULTILINE))
    assert settings == {
        "_lnk_log_caslib": "Public", "_lnk_log_table": "LLM_LOGS",
        "_lnk_catalog_caslib": "Public", "_lnk_catalog_table": "MODEL_CATALOG",
        "_lnk_runs_caslib": "Public", "_lnk_runs_table": "RAG_RUNS",
        "_lnk_redo_all": "0", "_lnk_min_literal": "20", "_lnk_utc_offset": "0", "_lnk_query_window": "300",
        "_lnk_promote_table": "1", "_lnk_save_table": "1",
    }


def test_every_macro_variable_of_the_program_is_removed_at_its_end():
    program = code_file_program()
    settings = set(re.findall(r"^%_lnk_default\((_lnk_\w+),", program, flags=re.MULTILINE))
    # the two names of the macro that sets a default are its own, and gone with it
    assigned = (set(re.findall(r"%let (_lnk_\w+)", program)) | settings | {"_casSessionUUID"}) - {"_lnk_name", "_lnk_value"}
    removed = set(" ".join(re.findall(r"^%symdel (.*?)(?: / nowarn)?;$", program, flags=re.MULTILINE)).split())
    assert len(settings) == 12 and assigned == removed
    assert "%sysmacdelete _lnk_default;" in program


def test_missing_table_is_one_error_line_that_names_the_program_that_writes_it():
    errors = re.findall(r"^\s*%put (ERROR: .*);$", code_file_program(), flags=re.MULTILINE)
    # content-22: in front of them the line of a listing of saved files that CAS refused, which names no table as not found
    refused, log_missing, catalog_missing, not_linked = errors
    assert refused.startswith("ERROR: CAS did not say whether a table the program reads is in memory") and "not found" not in refused
    assert "Log-Parser-Code.sas" in log_missing and "LLM - Log Parser" in log_missing
    assert "Get-All-Prompts.sas" in catalog_missing and "&_lnk_catalog_caslib..&_lnk_catalog_table." in catalog_missing
    assert "left as it is" in catalog_missing and "left as it is" in not_linked


def test_python_is_skipped_when_a_table_is_missing(monkeypatch):
    """The SAS program sets _lnk_ready to 0 with its ERROR line. The Python then connects to nothing."""
    run = run_program(monkeypatch)
    assert run.cas is None
    assert run.sas.messages == []
    assert run.sas.macro_variables["_lnk_row_count"] == "-1"


def output_macro():
    program = code_file_program()
    return program[program.index("%macro _lnk_handle_output_table;"):program.index("%mend _lnk_handle_output_table;")]


def test_swap_of_the_table_is_the_one_of_the_parser():
    """The new table takes the place of the log table the way the parser replaces a table: dropped
    twice, renamed, promoted and saved under the same name."""
    swap = output_macro()
    actions = re.findall(r"^\s*table\.(\w+)", swap, flags=re.MULTILINE)
    # Behind the swap: what is done for a table that was loaded and not linked, and the two staging tables that are dropped
    assert actions == ["tableExists", "dropTable", "dropTable", "alterTable", "promote", "save", "promote", "tableExists", "dropTable", "dropTable"]
    assert 'rename = "&_lnk_log_table."' in swap
    assert swap.index("%if &_lnk_promote_table. or &_lnk_log_scope. EQ 2 %then %do;") < swap.index("table.promote")
    assert swap.index("%if &_lnk_save_table. or &_lnk_log_saved. %then %do;") < swap.index("table.save")


# The condition under which the program puts a log table in place that it loaded and did not link
LOADED_AND_NOT_LINKED = "%if (&_lnk_unchanged. EQ 1 or &_lnk_row_count. EQ 0) and &_lnk_log_scope. EQ 0 and &_lnk_log_exists. EQ 1 %then %do;"


def loaded_and_not_linked(**macro_variables):
    """Whether the program puts the loaded table in place, for the values its macro variables have behind the Python."""
    expression = LOADED_AND_NOT_LINKED[len("%if "):-len(" %then %do;")]
    for name, value in macro_variables.items():
        expression = expression.replace(f"&{name}.", str(int(value)))
    assert "&" not in expression, expression
    return bool(eval(expression.replace(" EQ ", " == ")))


def test_log_table_that_was_loaded_from_its_saved_file_and_not_linked_is_put_in_place():
    """After a restart of the CAS server the log table is a saved file and no table in memory. The program
    loads it to link it. A run that then has nothing to link builds no table, and the loaded one was a
    table of the CAS session of the program: gone when that session ended, where a run that links leaves
    a promoted table. It is promoted now as _lnk_promote_table says, the saved file is not written again,
    and one line of the SAS log says what was done."""
    program = code_file_program()
    macro = output_macro()

    assert macro.count(LOADED_AND_NOT_LINKED) == 1
    # Loaded by this program: not in memory when the scope was asked, which is before the macro that loads, and in memory behind it
    assert program.index("call symputx('_lnk_log_scope', found.exists);") < program.index("%_lnk_ensure_loaded(%superq(_lnk_log_caslib), %superq(_lnk_log_table), _lnk_log_exists);")
    assert 'casOut = {casLib = "&_lnk_lib.", name = "&_lnk_name."};' in program
    # The run that links nothing, and the table without rows: the two runs that end with a NOTE and build no table
    assert loaded_and_not_linked(_lnk_unchanged=1, _lnk_row_count=6, _lnk_log_scope=0, _lnk_log_exists=1)
    assert loaded_and_not_linked(_lnk_unchanged=0, _lnk_row_count=0, _lnk_log_scope=0, _lnk_log_exists=1)
    # Not for a table that was in memory, as a session table or promoted: it is left as it is
    assert not loaded_and_not_linked(_lnk_unchanged=1, _lnk_row_count=6, _lnk_log_scope=1, _lnk_log_exists=1)
    assert not loaded_and_not_linked(_lnk_unchanged=1, _lnk_row_count=6, _lnk_log_scope=2, _lnk_log_exists=1)
    # Not for a run that linked rows - the swap puts the new table in place - nor for one that ended with an ERROR line
    assert not loaded_and_not_linked(_lnk_unchanged=0, _lnk_row_count=6, _lnk_log_scope=0, _lnk_log_exists=1)
    assert not loaded_and_not_linked(_lnk_unchanged=0, _lnk_row_count=-1, _lnk_log_scope=0, _lnk_log_exists=1)
    assert not loaded_and_not_linked(_lnk_unchanged=0, _lnk_row_count=-1, _lnk_log_scope=0, _lnk_log_exists=0)

    block = macro[macro.index(LOADED_AND_NOT_LINKED):]
    block = block[:block.index("\n    proc cas;\n        table.dropTable /\n                casLib = 'CASUSER',")]
    # It stands behind the three ways the macro can go, so behind the swap of a run that linked
    assert macro.index("%put ERROR: The rows could not be linked") < macro.index(LOADED_AND_NOT_LINKED)
    # Promoted where the option says so, with the action of the swap, and asked what the table is afterwards
    assert re.findall(r"^\s*table\.(\w+)", block, flags=re.MULTILINE) == ["promote", "tableExists"]
    # content-23: the question behind the promote starts at -1, the value of a step that halted
    assert re.findall(r"%if (.*) %then %do;\n\s*%let _lnk_log_scope = -1;\n\s*proc cas;\n\s*table\.promote /", block) == ["&_lnk_promote_table."]
    assert ('table.promote /\n                    casLib = "&_lnk_log_caslib.",\n                    name = "&_lnk_log_table.",\n'
            '                    targetLib = "&_lnk_log_caslib.",\n                    target = "&_lnk_log_table.",\n                    quiet = True;') in block
    assert "call symputx('_lnk_log_scope', found.exists);" in block
    # Nothing was linked, so the saved file holds what the table holds: it is not written, and nothing is dropped or renamed
    assert not re.search(r"table\.save|dropTable|rename|loadTable", block)
    # One line for each way it can end, none of them an ERROR line: a run with nothing to link is no failed job
    lines = re.findall(r"^\s*%put (\w+): (.*);$", block, flags=re.MULTILINE)
    # fix-content-22: and a WARNING where CAS refused to say whether the table is promoted
    assert [kind for kind, _ in lines] == ["NOTE", "WARNING", "WARNING", "NOTE"]
    assert re.findall(r"^        %(?:else )?(?:%?if (.*) %then )?%do;$", block, flags=re.MULTILINE) == [
        "&_lnk_promote_table.", "&_lnk_log_scope. EQ 2", "&_lnk_log_scope. EQ -1", "&_lnk_promote_table.", ""]
    promoted, not_answered, not_promoted, not_asked = [message for _, message in lines]
    assert promoted == ("The table &_lnk_log_caslib..&_lnk_log_table. was not in memory. It was loaded from its saved file and promoted as it was loaded, "
                        "the saved file was left as it is.")
    assert not_answered == ("The table &_lnk_log_caslib..&_lnk_log_table. was not in memory. It was loaded from its saved file, and CAS did not "
                            "say whether it was promoted then: where it was not, it is a table of this CAS session and gone when the session "
                            "ends. The saved file was left as it is.")
    for message in (not_promoted, not_asked):
        assert message.startswith("The table &_lnk_log_caslib..&_lnk_log_table. was not in memory. It was loaded from its saved file and ")
        assert "so it is a table of this CAS session and gone when the session ends. The saved file was left as it is." in message
    assert "could not be promoted" in not_promoted and "not promoted, as _lnk_promote_table is not 1" in not_asked
    # A message of the SAS code holds nothing that ends its statement or starts a macro call but the variables it names
    for message in (promoted, not_answered, not_promoted, not_asked):
        assert not re.search(r"[;%'\"]", message) and set(re.findall(r"&(\w+)\.", message)) == {"_lnk_log_caslib", "_lnk_log_table"}


def test_promoted_table_stays_promoted_and_a_saved_one_is_saved_again_whatever_the_options_say():
    """With _lnk_promote_table = 0 the swap would leave a session table where a promoted one was, and with
    _lnk_save_table = 0 a saved file that brings back the table without its links. What the table is, is
    asked before anything is loaded or dropped: the parser does the same when it appends."""
    program = code_file_program()
    swap = output_macro()
    # The only two conditions the promotion and the save of a linked table stand under
    swap = swap[:swap.index(LOADED_AND_NOT_LINKED)]
    assert re.findall(r"%if (.*) %then %do;\n\s*table\.promote", swap) == ["&_lnk_promote_table. or &_lnk_log_scope. EQ 2"]
    assert re.findall(r"%if (.*) %then %do;\n\s*if tableWritten then do;\n\s*table\.save", swap) == ["&_lnk_save_table. or &_lnk_log_saved."]

    # 2 is what tableExists answers for a promoted table. It is asked of the log table before the macro that loads a saved table runs
    scope = program.index("call symputx('_lnk_log_scope', found.exists);")
    asked = program.rindex("table.tableExists result=found status=asked /", 0, scope)
    assert 'casLib = "&_lnk_log_caslib.",' in program[asked:scope] and 'name = "&_lnk_log_table.";' in program[asked:scope]
    assert program.index("%let _lnk_log_scope = -1;") < scope < program.index("%macro _lnk_ensure_loaded")
    # The saved file is looked for by its name without case, and only when the table is not saved anyway
    saved = program.index("call symputx('_lnk_log_saved', 1);")
    listing = program[program.index("%let _lnk_log_saved = 0;"):saved]
    assert "%if &_lnk_save_table. NE 1 %then %do;" in listing and "table.fileInfo result=listing status=listed /" in listing
    assert 'if upcase(savedEntry.Name) EQ upcase("&_lnk_log_table..sashdat") then' in listing
    assert saved < program.index("%macro _lnk_ensure_loaded")


def test_program_starts_a_cas_session_only_when_none_is_connected_and_ends_only_its_own():
    """A session that is connected is used and left alone. 'cas mysess;' in front of the check started a second
    session under a name that existed - a WARNING in the log and an error code on the job."""
    program = code_file_program()
    statements = re.findall(r"^\s*(cas\s+\w+.*;)$", program, flags=re.MULTILINE | re.IGNORECASE)
    assert statements == ["cas lnkMySess;", "cas lnkMySess terminate;"]
    check = "%if %sysfunc(sessfound(%sysfunc(getoption(sessref)))) NE 1 %then %do;\n    %let _lnk_clean_cas_session = 1;\n    cas lnkMySess;\n%end;"
    assert check in program
    assert "%if &_lnk_clean_cas_session. %then %do;\n    cas lnkMySess terminate;\n%end;" in program
    # Nothing asks CAS for anything before the check
    assert program.index(check) < program.index("proc cas;")


# ---------------------------------------------------------------------------
# Templates and questions
# ---------------------------------------------------------------------------

def test_template_is_its_fixed_text_around_its_variables(program):
    parts, literal = program["template_parts"]("Question:  {{question}}\n\nPassages:\n{{ passages }}\n\nAnswer:")
    assert parts == ("Question:", "Passages:", "Answer:")
    assert literal == len("Question:Passages:Answer:")


@pytest.mark.parametrize("sent", [
    "Question: Why? Passages: one two Answer:",
    "Question:\nWhy?\n\nPassages:\n\none\ntwo\n\nAnswer:",      # the values span lines, the whitespace differs
    "Question: Passages: Answer:",                               # both variables are empty
    "Question: Passages: Answer: Passages: x Answer:",           # the fixed text also shows up inside a value
])
def test_variable_stands_for_any_text(program, sent):
    parts, _ = program["template_parts"]("Question: {{question}}\n\nPassages:\n{{passages}}\n\nAnswer:")
    assert program["fits"](parts, program["squeeze"](sent))


@pytest.mark.parametrize("sent", [
    "Note. Question: Why? Passages: one Answer:",     # text before the template
    "Question: Why? Passages: one Answer: Because.",  # text behind it
    "Question: Why? Answer:",                         # a fixed part is missing
    "Question: Why? Answer: Passages:",               # the parts are in another order
    "Question: Passages:",
])
def test_template_is_anchored_at_both_ends_and_keeps_its_order(program, sent):
    parts, _ = program["template_parts"]("Question: {{question}}\n\nPassages:\n{{passages}}\n\nAnswer:")
    assert not program["fits"](parts, program["squeeze"](sent))


def test_fixed_texts_between_two_variables_keep_their_order(program):
    parts, _ = program["template_parts"]("Name: {{name}} City: {{city}} Job: {{job}} Signed, the desk")
    assert program["fits"](parts, "Name: Ada City: Graz Job: clerk Signed, the desk")
    assert not program["fits"](parts, "Name: Ada Job: clerk City: Graz Signed, the desk")


def test_template_without_a_variable_is_the_whole_text(program):
    parts, _ = program["template_parts"]("Answer in one word.")
    assert program["fits"](parts, "Answer in one word.")
    assert not program["fits"](parts, "Answer in one word. Really.")


def test_questions_are_compared_as_json(program):
    keys = program["question_keys"]
    reordered = json.dumps({"refund": QUESTIONS["refund"], "department": QUESTIONS["department"]}, indent=4)
    assert set(keys(QUESTIONS_AS_SENT)) & set(keys(reordered))
    changed = json.dumps(dict(QUESTIONS, refund={"type": "noul", "instructions": "Is money asked back?"}))
    assert not set(keys(QUESTIONS_AS_SENT)) & set(keys(changed))


def test_questions_that_are_no_json_are_compared_as_text(program):
    keys = program["question_keys"]
    assert keys("department:  billing or\ntechnical") == keys("department: billing or technical") == ["T:department: billing or technical"]
    # A JSON text meets the same text on a side that could not be parsed
    assert set(keys('{"a": 1}')) & set(keys('{"a":   1}'))


QUARTER_PAST_NINE = sas_datetime("2026-01-05 09:15:00")


@pytest.mark.parametrize("value, seconds", [
    (QUARTER_PAST_NINE, QUARTER_PAST_NINE),                       # the SAS datetime of the log table
    ("2026-01-05 09:15:00", QUARTER_PAST_NINE),                   # the text the RAG pipeline records, UTC
    ("2026-01-05T09:15:00Z", QUARTER_PAST_NINE),
    ("2026-01-05T11:15:00+02:00", QUARTER_PAST_NINE),
    ("2026-01-05 09:15:00.250000", QUARTER_PAST_NINE + 0.25),
    (pd.Timestamp("2026-01-05 09:15:00"), QUARTER_PAST_NINE),
    (str(QUARTER_PAST_NINE), QUARTER_PAST_NINE),
    ("", None), (None, None), (float("nan"), None), ("yesterday", None),
])
def test_times_are_read_as_seconds_since_1960(program, value, seconds):
    assert QUARTER_PAST_NINE == 2083223700.0
    assert program["sas_seconds"](value) == seconds


# ---------------------------------------------------------------------------
# The model that answered
# ---------------------------------------------------------------------------

def test_model_id_is_the_model_with_the_name_of_the_container(program):
    links = link(program, [llm_row(), decision_row(), embedding_row(), llm_row(model="Demo_LLM"), llm_row(model="other_llm")], [LLM, DECIDER, EMBEDDER])
    assert [row["model_id"] for row in links] == [uuid(1), uuid(2), uuid(3), uuid(1), ""]


def test_model_id_takes_the_model_of_the_kind_of_the_row(program):
    """A decision model and an LLM of the same name are two models; the kind of the row says which one answered."""
    catalog = [answer_model("shared", "llm", uuid(1)), answer_model("shared", "decision_model", uuid(2))]
    links = link(program, [llm_row(model="shared"), decision_row(model="shared")], catalog)
    assert [row["model_id"] for row in links] == [uuid(1), uuid(2)]


def test_model_id_stays_empty_when_two_models_have_the_name(program):
    catalog = [answer_model("demo_llm", "llm", uuid(1)), answer_model("demo_llm", "llm", uuid(4))]
    assert link(program, [llm_row()], catalog)[0]["model_id"] == ""


def test_row_without_a_kind_takes_it_from_the_model(program):
    """Rows an older parser wrote have no kind. The role of the model that answered says what they are."""
    old_llm = llm_row(LEAVE_SYSTEM, "Question: Why? Passages: none Answer:", kind=None)
    old_decision = decision_row(kind=None)
    links = link(program, [old_llm, old_decision], [LLM, DECIDER, LEAVE_PROMPT, TICKET_TEMPLATE])
    assert [(row["model_id"], row["match_method"], row["caller_name"]) for row in links] == [
        (uuid(1), "template", "Leave desk"), (uuid(2), "questions and state", "Ticket routing")]


# ---------------------------------------------------------------------------
# LLM rows
# ---------------------------------------------------------------------------

def test_experiment_run_links_the_row_to_the_run_that_sent_its_prompts(program):
    resolved_user = "Question: Why?\n\nPassages:\nnone\n\nAnswer:"
    run = experiment_run(uuid(10), "Leave desk", "3", "demo_llm", LEAVE_SYSTEM, LEAVE_USER, LEAVE_SYSTEM, resolved_user)
    # The request holds the prompts with other whitespace than the tracker
    row = link(program, [llm_row(LEAVE_SYSTEM.replace("\n", " "), "Question: Why? Passages: none Answer:")], [LLM, run], agent="browser")[0]
    assert row == {"model_id": uuid(1), "caller_model_id": uuid(10), "caller_project_id": uuid(900), "caller_kind": "prompt", "caller_name": "Leave desk",
                   "experiment_run_id": "3", "rag_model_id": "", "match_method": "experiment run", "match_candidates": 1}


def test_experiment_run_is_ahead_of_a_template(program):
    """Another prompt has a template the request fits as well. The run that sent exactly these prompts decides."""
    run = experiment_run(uuid(11), "Leave desk draft", "1", "demo_llm", LEAVE_SYSTEM, "{{everything}}", LEAVE_SYSTEM, "Question: Why? Passages: none Answer:")
    row = link(program, [llm_row(LEAVE_SYSTEM, "Question: Why? Passages: none Answer:")], [LLM, LEAVE_PROMPT, run], agent="browser")[0]
    assert (row["match_method"], row["caller_model_id"], row["experiment_run_id"]) == ("experiment run", uuid(11), "1")


def test_several_runs_with_the_same_prompts_give_the_newest(program):
    runs = [experiment_run(uuid(10), "Leave desk", run_id, "demo_llm", LEAVE_SYSTEM, LEAVE_USER, LEAVE_SYSTEM, "Question: Why? Passages: none Answer:")
            for run_id in ("9", "10", "2")]
    row = link(program, [llm_row(LEAVE_SYSTEM, "Question: Why? Passages: none Answer:")], [LLM] + runs, agent="browser")[0]
    assert (row["match_method"], row["caller_model_id"], row["experiment_run_id"], row["match_candidates"]) == ("experiment run", uuid(10), "10", 3)


def test_run_id_of_a_numeric_column_is_written_without_a_decimal(program):
    run = experiment_run(uuid(10), "Leave desk", 4.0, "demo_llm", LEAVE_SYSTEM, LEAVE_USER, LEAVE_SYSTEM, "Question: Why? Passages: none Answer:")
    assert link(program, [llm_row(LEAVE_SYSTEM, "Question: Why? Passages: none Answer:")], [LLM, run], agent="browser")[0]["experiment_run_id"] == "4"


def test_runs_of_two_prompts_with_the_same_prompts_are_ambiguous(program):
    runs = [experiment_run(uuid(number), name, "1", "demo_llm", LEAVE_SYSTEM, LEAVE_USER, LEAVE_SYSTEM, "Question: Why? Passages: none Answer:")
            for number, name in ((10, "Leave desk"), (11, "Leave desk copy"))]
    row = link(program, [llm_row(LEAVE_SYSTEM, "Question: Why? Passages: none Answer:")], [LLM] + runs, agent="browser")[0]
    assert (row["match_method"], row["match_candidates"]) == ("ambiguous", 2)
    assert [row[column] for column in CALLER_COLUMNS] == [""] * 5
    assert row["model_id"] == uuid(1)


def test_run_against_another_model_is_no_experiment_run_of_this_row(program):
    """The run was sent to other_llm. The row of demo_llm fits the templates of that run, so it is linked
    to the prompt - by its template, and without a run."""
    run = experiment_run(uuid(10), "Leave desk", "3", "other_llm", LEAVE_SYSTEM, LEAVE_USER, LEAVE_SYSTEM, "Question: Why? Passages: none Answer:")
    row = link(program, [llm_row(LEAVE_SYSTEM, "Question: Why? Passages: none Answer:")], [LLM, run], agent="browser")[0]
    assert (row["match_method"], row["caller_model_id"], row["experiment_run_id"]) == ("template", uuid(10), "")


@pytest.mark.parametrize("agent, method, run_id", [
    ("browser", "experiment run", "3"),
    ("", "experiment run", "3"),                  # a row of a parser that wrote no user agent
    ("other", "experiment run", "3"),
    ("python-requests", "template", ""),          # the score code of the published prompt
    ("SAS", "template", ""),
])
def test_experiment_run_is_a_request_of_a_browser(program, agent, method, run_id):
    """The Builder page sends its runs from a browser. A published prompt that is called with the values
    of a run sends the very same prompts: it is linked to the prompt, by its template, and not to the run."""
    resolved_user = "Question: Why? Passages: none Answer:"
    run = experiment_run(uuid(10), "Leave desk", "3", "demo_llm", LEAVE_SYSTEM, LEAVE_USER, LEAVE_SYSTEM, resolved_user)
    row = link(program, [llm_row(LEAVE_SYSTEM, resolved_user)], [LLM, run], agent=agent)[0]
    assert (row["match_method"], row["caller_model_id"], row["experiment_run_id"]) == (method, uuid(10), run_id)

    state = "Ticket: I was charged twice for my plan this morning."
    run = experiment_run(uuid(20), "Ticket routing", "3", "demo_decider", QUESTIONS_AS_SENT, TICKET_STATE, "", state, role="decision")
    row = link(program, [decision_row()], [DECIDER, run], agent=agent)[0]
    assert (row["match_method"], row["caller_model_id"], row["experiment_run_id"]) == (method.replace("template", "questions and state"), uuid(20), run_id)


@pytest.mark.parametrize("hashes", [True, False])
def test_run_without_resolved_prompts_is_its_templates_when_they_hold_no_variable(program, hashes):
    """With its hash columns filled such a run holds the hash of an empty text, which names no request."""
    fixed = experiment_run(uuid(10), "Health check", "1", "demo_llm", "You are a health check of the platform.", "Reply with OK.", "", "", hashes=hashes)
    with_variable = experiment_run(uuid(11), "Leave desk", "1", "demo_llm", LEAVE_SYSTEM, LEAVE_USER, "", "", hashes=hashes)
    rows = [llm_row("You are a health check of the platform.", "Reply with OK."), llm_row(LEAVE_SYSTEM, "Question: Why? Passages: none Answer:")]
    assert [row["match_method"] for row in link(program, rows, [LLM, fixed, with_variable], agent="browser")] == ["experiment run", "template"]


def test_hash_of_a_text_is_the_one_the_other_programs_compute(program):
    """The digests the tests of the log parser and of Get-All-Prompts.sas write out for the same texts."""
    hash_of = program["text_hash"]
    assert hash_of("How long does a travel refund take?") == "e422a7ae4d49d80be7ae1d8c9b32f06398796c0f498f0e5f3c0e342edf7a9032"
    assert hash_of("  How long\tdoes a travel\r\nrefund   take?\n") == hash_of("How long does a travel refund take?")
    assert hash_of(None) == hash_of("") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    # A character that is no UTF-8 is hashed as a question mark, as in the two other programs
    assert hash_of("caf\udce9") == hash_of("caf?")


def test_experiment_run_whose_prompt_is_longer_than_a_column_is_found_by_its_hash(program):
    """The catalog takes the hash of the whole prompt and cuts the text to the length of a SAS column.
    The request holds the whole prompt: its hash is the one of the catalog, its text is not."""
    resolved_user = "Question: Why?\n\nPassages:\n" + "A made-up passage about leave. " * 40 + "\n\nAnswer:"
    cut = experiment_run(uuid(10), "Leave desk", "3", "demo_llm", LEAVE_SYSTEM, LEAVE_USER, LEAVE_SYSTEM, resolved_user, column_length=200)
    assert len(cut["resolved_user_prompt"]) == 200 < len(resolved_user)
    row = link(program, [llm_row(LEAVE_SYSTEM, resolved_user)], [LLM, cut], agent="browser")[0]
    assert (row["match_method"], row["caller_model_id"], row["experiment_run_id"]) == ("experiment run", uuid(10), "3")
    # Without the hash only the text can be compared, and the cut text is not the request: the templates of the run still link the prompt
    cut_without_hash = experiment_run(uuid(10), "Leave desk", "3", "demo_llm", LEAVE_SYSTEM, LEAVE_USER, LEAVE_SYSTEM, resolved_user, column_length=200, hashes=False)
    row = link(program, [llm_row(LEAVE_SYSTEM, resolved_user)], [LLM, cut_without_hash], agent="browser")[0]
    assert (row["match_method"], row["caller_model_id"], row["experiment_run_id"]) == ("template", uuid(10), "")


def test_catalog_without_hash_columns_compares_the_prompts_of_a_run_as_text(program):
    resolved_user = "Question: Why?\n\nPassages:\nnone\n\nAnswer:"
    run = experiment_run(uuid(10), "Leave desk", "3", "demo_llm", LEAVE_SYSTEM, LEAVE_USER, LEAVE_SYSTEM, resolved_user, hashes=False)
    catalog = lower_case(catalog_table(LLM, run)).drop(columns=["resolved_system_hash", "resolved_user_hash", "template_source"])
    rows = lower_case(log_table(llm_row(LEAVE_SYSTEM.replace("\n", " "), "Question: Why? Passages: none Answer:", user_agent="browser")))
    links, _ = program["link_rows"](rows, catalog)
    assert links.loc[0, ["match_method", "experiment_run_id"]].tolist() == ["experiment run", "3"]


def test_template_links_a_prompt_with_a_retrieval_to_its_prompt_and_to_the_setup_of_its_collection(program):
    row = link(program, [leave_request()], [LLM, LEAVE_PROMPT, HANDBOOK])[0]
    assert row == {"model_id": uuid(1), "caller_model_id": uuid(10), "caller_project_id": uuid(900), "caller_kind": "prompt", "caller_name": "Leave desk",
                   "experiment_run_id": "", "rag_model_id": uuid(30), "match_method": "template", "match_candidates": 1}


def test_setup_of_the_collection_does_not_need_a_prompt(program):
    """The retrieval names its collection, so the setup is known where the prompt is not."""
    row = link(program, [leave_request()], [LLM, HANDBOOK])[0]
    assert (row["match_method"], row["caller_model_id"], row["rag_model_id"]) == ("none", "", uuid(30))


def test_collection_of_two_setups_is_decided_by_the_ingestion_run_of_the_retrieval(program):
    """The Builder's setup and a model the step RAG - Register Setup wrote can hold the same collection."""
    setups = [HANDBOOK, rag_setup(uuid(31), "RAG Retrieval - Staff handbook", "handbook_v1")]
    undecided = link(program, [leave_request()], [LLM, LEAVE_PROMPT] + setups)[0]
    assert (undecided["match_method"], undecided["rag_model_id"]) == ("template", "")
    # The envelope names run-1767604500, and RAG_RUNS has that run for one of the two setups
    runs = [ingestion_run("run-1767604500", "handbook_v1", "2026-01-05 09:15:00", "2026-01-05 09:20:00", model_id=uuid(31))]
    assert link(program, [leave_request()], [LLM, LEAVE_PROMPT] + setups, runs)[0]["rag_model_id"] == uuid(31)


def test_of_several_retrievals_in_a_prompt_the_last_one_names_the_ingestion_run(program):
    """The log parser takes the collection and the question of the last retrieval a prompt holds, and the
    ingestion run is taken of the same retrieval."""
    setups = [HANDBOOK, rag_setup(uuid(31), "RAG Retrieval - Staff handbook", "handbook_v1")]
    runs = [ingestion_run("run-1767604500", "handbook_v1", "2026-01-05 09:15:00", "2026-01-05 09:20:00", model_id=uuid(30)),
            ingestion_run("run-1767690900", "handbook_v1", "2026-01-06 09:15:00", "2026-01-06 09:20:00", model_id=uuid(31))]
    earlier, last = envelope("handbook_v1", "How is a sick day reported?", run_id="run-1767604500"), envelope("handbook_v1", QUESTION, run_id="run-1767690900")
    for passages, setup in ((earlier + "\n" + last, uuid(31)), (last + "\n" + earlier, uuid(30)), (earlier, uuid(30))):
        row = llm_row(LEAVE_SYSTEM, LEAVE_USER.replace("{{question}}", QUESTION).replace("{{passages}}", passages), retrieval=("handbook_v1", QUESTION))
        assert link(program, [row], [LLM, LEAVE_PROMPT] + setups, runs)[0]["rag_model_id"] == setup, passages[:60]


def test_setup_the_builder_saved_is_ahead_of_the_model_the_step_registered(program):
    """A setup that was saved in the Prompt Builder and registered with the step RAG - Register Setup is two
    models with one collection, and Get-All-Prompts.sas stacks the runs of their run table once for each.
    The catalog says which of the two the Builder saved: that one holds the setup."""
    saved = rag_setup(uuid(30), "Staff handbook", "handbook_v1", source="builder")
    registered = rag_setup(uuid(31), "RAG Retrieval - Staff handbook", "handbook_v1", source="step")
    runs = [ingestion_run("run-1767604500", "handbook_v1", "2026-01-05 09:15:00", "2026-01-05 09:20:00", model_id=model_id) for model_id in (uuid(30), uuid(31))]
    rows = [leave_request(timestamp="2026-01-05 11:30:00"), embedding_row(QUESTION, timestamp="2026-01-05 11:29:59"),
            embedding_row("Chapter 7.", mode="document", timestamp="2026-01-05 09:17:00")]
    for catalog in ([saved, registered], [registered, saved]):
        prompt, query, document = link(program, rows, [LLM, EMBEDDER, LEAVE_PROMPT] + catalog, runs)
        assert (prompt["match_method"], prompt["rag_model_id"]) == ("template", uuid(30))
        assert (query["match_method"], query["caller_model_id"], query["caller_name"]) == ("retrieval query", uuid(30), "Staff handbook")
        assert (document["match_method"], document["caller_model_id"]) == ("ingestion run", uuid(30))

    # Two setups of the Builder, or two models of the step, with one collection: nothing says which one it was
    for source in ("builder", "step"):
        twins = [rag_setup(uuid(30), "Staff handbook", "handbook_v1", source=source), rag_setup(uuid(31), "Staff handbook copy", "handbook_v1", source=source)]
        prompt, query, document = link(program, rows, [LLM, EMBEDDER, LEAVE_PROMPT] + twins, runs)
        assert (prompt["match_method"], prompt["rag_model_id"]) == ("template", "")
        assert (query["match_method"], query["match_candidates"]) == ("ambiguous", 2)
        assert (document["match_method"], document["match_candidates"]) == ("ambiguous", 2)


def test_most_specific_template_wins_and_the_candidates_are_counted(program):
    general = prompt_template(uuid(12), "Anything about the handbook", LEAVE_SYSTEM, "{{input}}")
    row = link(program, [leave_request()], [LLM, general, LEAVE_PROMPT])[0]
    assert (row["match_method"], row["caller_name"], row["match_candidates"]) == ("template", "Leave desk", 2)


def test_two_prompts_with_the_same_templates_are_ambiguous(program):
    copy = prompt_template(uuid(12), "Leave desk copy", LEAVE_SYSTEM, LEAVE_USER.replace("{{question}}", "{{q}}"))
    row = link(program, [leave_request()], [LLM, LEAVE_PROMPT, copy, HANDBOOK])[0]
    assert (row["match_method"], row["match_candidates"]) == ("ambiguous", 2)
    assert [row[column] for column in CALLER_COLUMNS] == [""] * 5
    # What does not depend on the caller is still there
    assert (row["model_id"], row["rag_model_id"]) == (uuid(1), uuid(30))


def test_one_prompt_with_several_patterns_is_one_candidate(program):
    """The saved template and the templates of earlier runs are patterns of the same prompt."""
    earlier = experiment_run(uuid(10), "Leave desk", "1", "demo_llm", LEAVE_SYSTEM, "Question: {{question}} Passages: {{passages}}", LEAVE_SYSTEM, "Question: x Passages: y")
    row = link(program, [leave_request()], [LLM, LEAVE_PROMPT, earlier])[0]
    assert (row["match_method"], row["caller_model_id"], row["match_candidates"]) == ("template", uuid(10), 1)


def test_template_of_an_earlier_run_links_what_the_saved_template_no_longer_fits(program):
    """The template was changed after it was tested. The run kept the template it was tested with."""
    saved = prompt_template(uuid(10), "Leave desk", LEAVE_SYSTEM + " Be friendly.", LEAVE_USER)
    earlier = experiment_run(uuid(10), "Leave desk", "1", "demo_llm", LEAVE_SYSTEM, LEAVE_USER, LEAVE_SYSTEM, "Question: x Passages: y Answer:")
    assert link(program, [leave_request()], [LLM, saved])[0]["match_method"] == "none"
    assert link(program, [leave_request()], [LLM, saved, earlier])[0]["match_method"] == "template"


@pytest.mark.parametrize("system_template, user_template, method", [
    ("{{system}}", "{{user}}", "none"),                       # nothing but variables
    ("", "{{question}}", "none"),
    ("Answer briefly.", "{{question}}", "none"),              # 14 characters of fixed text
    ("Answer in one word.", "Q: {{question}}", "none"),       # 16 + 2 = 18
    ("Answer in one word.", "Q:: {{question}}", "none"),      # 19
    ("Answer in one word.", "Ask: {{question}}", "template"),  # 20
])
def test_template_needs_twenty_characters_of_fixed_text(program, system_template, user_template, method):
    system = system_template.replace("{{system}}", "Answer in one word.")
    user = user_template.replace("{{question}}", "What is the capital of Austria?").replace("{{user}}", "What is the capital of Austria?")
    catalog = [LLM, prompt_template(uuid(10), "Short", system_template, user_template)]
    assert link(program, [llm_row(system, user)], catalog)[0]["match_method"] == method


def test_minimum_of_fixed_text_is_a_setting(program):
    catalog = [LLM, prompt_template(uuid(10), "Short", "Answer briefly.", "{{question}}")]
    row = llm_row("Answer briefly.", "What is the capital of Austria?")
    assert link(program, [row], catalog)[0]["match_method"] == "none"
    assert link(program, [row], catalog, min_literal=10)[0]["match_method"] == "template"


def test_experiment_run_needs_the_minimum_as_well(program):
    run = experiment_run(uuid(10), "Tiny", "1", "demo_llm", "Be brief.", "{{question}}", "Be brief.", "Hi")
    assert link(program, [llm_row("Be brief.", "Hi")], [LLM, run], agent="browser")[0]["match_method"] == "none"


def test_prompt_that_names_another_model_only_wins_when_no_other_prompt_fits(program):
    """The catalog says the Leave desk prompt calls other_llm. The request went to demo_llm: the prompt
    can still be the caller - it may have been tested against demo_llm in the Builder - but a prompt
    that is known to call demo_llm, or of which no model is known, is ahead of it."""
    for_other = prompt_template(uuid(10), "Leave desk", LEAVE_SYSTEM, LEAVE_USER, target_model="other_llm")
    general = prompt_template(uuid(12), "Anything about the handbook", LEAVE_SYSTEM, "{{input}}", target_model="demo_llm")
    unknown = prompt_template(uuid(13), "Handbook, any model", LEAVE_SYSTEM, "{{input}}")

    alone = link(program, [leave_request()], [LLM, for_other])[0]
    assert (alone["match_method"], alone["caller_name"], alone["match_candidates"]) == ("template", "Leave desk", 1)
    for other_prompt in (general, unknown):
        row = link(program, [leave_request()], [LLM, for_other, other_prompt])[0]
        assert (row["match_method"], row["caller_model_id"], row["match_candidates"]) == ("template", other_prompt["modelID"], 2)
    # Sent to the model the catalog names, the more specific template wins as always
    row = link(program, [leave_request(model="other_llm")], [answer_model("other_llm", "llm", uuid(5)), for_other, unknown])[0]
    assert row["caller_name"] == "Leave desk"


def test_prompt_known_for_this_model_through_a_run_is_not_lowered(program):
    for_other = prompt_template(uuid(10), "Leave desk", LEAVE_SYSTEM, LEAVE_USER, target_model="other_llm")
    tested_here = experiment_run(uuid(10), "Leave desk", "2", "demo_llm", LEAVE_SYSTEM, LEAVE_USER, LEAVE_SYSTEM, "Question: x Passages: y Answer:")
    general = prompt_template(uuid(12), "Anything about the handbook", LEAVE_SYSTEM, "{{input}}", target_model="demo_llm")
    assert link(program, [leave_request()], [LLM, for_other, tested_here, general])[0]["caller_name"] == "Leave desk"


def test_variable_in_the_system_prompt(program):
    """Templates with a variable in the system prompt are found through the start of their system
    prompt, through its end when it starts with the variable, and are compared with every row when
    a variable stands at both ends."""
    by_start = prompt_template(uuid(10), "Desk of a department", "You answer questions of employees about {{topic}}. Answer in one sentence.", "{{question}}")
    by_end = prompt_template(uuid(11), "Signed assistant", "{{persona}} Always sign your answer with the name of the travel desk.", "{{question}}")
    by_middle = prompt_template(uuid(12), "Reviewer", "{{persona}} You review contracts of the purchasing department. {{style}}", "{{question}}")
    rows = [llm_row("You answer questions of employees about travel expenses. Answer in one sentence.", "May I fly business?"),
            llm_row("You are Ada. Always sign your answer with the name of the travel desk.", "May I fly business?"),
            llm_row("You are Bo. You review contracts of the purchasing department. Be strict.", "May I fly business?"),
            llm_row("You answer questions of employees about travel expenses. Answer in two sentences.", "May I fly business?")]
    links = link(program, rows, [LLM, by_start, by_end, by_middle])
    assert [(row["match_method"], row["caller_name"]) for row in links] == [
        ("template", "Desk of a department"), ("template", "Signed assistant"), ("template", "Reviewer"), ("none", "")]


def test_llm_row_that_fits_nothing(program):
    row = link(program, [llm_row("You are a storyteller for children.", "Tell a story about a fox.")], [LLM, LEAVE_PROMPT, TICKET_TEMPLATE, HANDBOOK])[0]
    assert row == {"model_id": uuid(1), "caller_model_id": "", "caller_project_id": "", "caller_kind": "", "caller_name": "",
                   "experiment_run_id": "", "rag_model_id": "", "match_method": "none", "match_candidates": 0}


def test_decision_template_is_no_caller_of_an_llm_row(program):
    """The questions of a decision are sent to a decision model. An LLM row with that text is no decision."""
    row = link(program, [llm_row(QUESTIONS_AS_SENT, "Ticket: the printer is broken.")], [LLM, TICKET_TEMPLATE])[0]
    assert row["match_method"] == "none"


# ---------------------------------------------------------------------------
# Decision rows
# ---------------------------------------------------------------------------

def test_questions_and_state_link_a_decision_row_to_its_template(program):
    """The template holds the questions with other whitespace and its keys in another order than the request."""
    reordered = json.dumps({"refund": QUESTIONS["refund"], "department": QUESTIONS["department"]}, indent=2)
    template = prompt_template(uuid(20), "Ticket routing", reordered, TICKET_STATE, role="decision")
    row = link(program, [decision_row()], [DECIDER, template])[0]
    assert row == {"model_id": uuid(2), "caller_model_id": uuid(20), "caller_project_id": uuid(900), "caller_kind": "decision", "caller_name": "Ticket routing",
                   "experiment_run_id": "", "rag_model_id": "", "match_method": "questions and state", "match_candidates": 1}


@pytest.mark.parametrize("state_template", ["Claim: {{claim}}", "{{record}}", ""])
def test_questions_alone_link_a_decision_row_whose_state_does_not_show_the_template(program, state_template):
    """The state template does not fit the state, or has no fixed text that could."""
    template = prompt_template(uuid(20), "Ticket routing", QUESTIONS_AS_SENT, state_template, role="decision")
    row = link(program, [decision_row()], [DECIDER, template])[0]
    assert (row["match_method"], row["caller_model_id"], row["match_candidates"]) == ("questions", uuid(20), 1)


def test_other_questions_are_no_match(program):
    other = dict(QUESTIONS, refund={"type": "noul", "instructions": "Does the customer threaten to leave?"})
    template = prompt_template(uuid(20), "Ticket routing", json.dumps(other), TICKET_STATE, role="decision")
    row = link(program, [decision_row()], [DECIDER, template])[0]
    assert (row["match_method"], row["caller_model_id"], row["model_id"]) == ("none", "", uuid(2))


def test_questions_that_are_no_json_fall_back_to_their_text(program):
    template = prompt_template(uuid(20), "Free questions", "department: billing or technical?\nrefund: yes or no?", TICKET_STATE, role="decision")
    row = link(program, [decision_row(questions="department: billing or technical? refund: yes or no?")], [DECIDER, template])[0]
    assert row["match_method"] == "questions and state"


def test_decision_experiment_run(program):
    state = "Ticket: I was charged twice for my plan this morning."
    run = experiment_run(uuid(20), "Ticket routing", "7", "demo_decider", json.dumps(QUESTIONS, indent=2), TICKET_STATE, "", state, role="decision")
    other_state = experiment_run(uuid(20), "Ticket routing", "8", "demo_decider", json.dumps(QUESTIONS, indent=2), TICKET_STATE, "", "Ticket: the printer is broken.", role="decision")
    links = link(program, [decision_row(), decision_row(state="Ticket: the lift is stuck.")], [DECIDER, run, other_state], agent="browser")
    assert [(row["match_method"], row["experiment_run_id"], row["caller_model_id"]) for row in links] == [
        ("experiment run", "7", uuid(20)), ("questions and state", "", uuid(20))]


def test_decision_experiment_run_as_get_all_prompts_writes_it(program):
    """The pull writes the questions of a decision run as they are stored into the resolved system prompt,
    and the hash of both texts. A state that is longer than a column is found by its hash."""
    state = "Ticket: I was charged twice for my plan this morning." + " And once more the month before." * 20
    stored = json.dumps(QUESTIONS, indent=2)
    run = experiment_run(uuid(20), "Ticket routing", "7", "demo_decider", stored, TICKET_STATE, stored, state, role="decision", column_length=500)
    assert len(stored) < len(run["resolved_user_prompt"]) == 500 < len(state)
    row = link(program, [decision_row(state=state)], [DECIDER, run], agent="browser")[0]
    assert (row["match_method"], row["experiment_run_id"], row["caller_model_id"]) == ("experiment run", "7", uuid(20))


def test_two_templates_with_the_same_questions(program):
    """The state decides when it fits the state template of one of them, otherwise nothing does."""
    tickets = prompt_template(uuid(20), "Ticket routing", QUESTIONS_AS_SENT, TICKET_STATE, role="decision")
    mails = prompt_template(uuid(21), "Mail routing", QUESTIONS_AS_SENT, "Mail: {{mail}}", role="decision")
    any_record = prompt_template(uuid(22), "Routing", QUESTIONS_AS_SENT, "{{record}}", role="decision")
    links = link(program, [decision_row(), decision_row(state="Call: the customer wants a refund.")], [DECIDER, tickets, mails, any_record])
    assert [(row["match_method"], row["caller_name"], row["match_candidates"]) for row in links] == [
        ("questions and state", "Ticket routing", 3), ("ambiguous", "", 3)]


def test_questions_below_the_minimum_match_nothing(program):
    template = prompt_template(uuid(20), "Empty", "{}", "{{record}}", role="decision")
    assert link(program, [decision_row(questions="{}", state="x")], [DECIDER, template])[0]["match_method"] == "none"


# ---------------------------------------------------------------------------
# Embedding rows
# ---------------------------------------------------------------------------

def test_retrieval_query_links_an_embedding_row_to_the_setup_of_the_prompt_that_carried_it(program):
    """The question is embedded, and two seconds later a prompt carries the retrieval of the same question."""
    rows = [embedding_row(QUESTION, timestamp="2026-01-05 11:29:58"), leave_request(timestamp="2026-01-05 11:30:00")]
    row = link(program, rows, [LLM, EMBEDDER, HANDBOOK])[0]
    assert row == {"model_id": uuid(3), "caller_model_id": uuid(30), "caller_project_id": uuid(900), "caller_kind": "rag", "caller_name": "Staff handbook",
                   "experiment_run_id": "", "rag_model_id": "", "match_method": "retrieval query", "match_candidates": 1}


@pytest.mark.parametrize("embedded_at, method", [
    ("2026-01-05 11:30:00", "retrieval query"),   # logged in the same second as the prompt
    ("2026-01-05 11:25:00", "retrieval query"),   # 300 seconds before it
    ("2026-01-05 11:24:59", "none"),              # 301 seconds
    ("2026-01-05 06:00:00", "none"),              # hours before: the text is the same, the request is another one
    ("2026-01-05 11:30:01", "none"),              # after the prompt: no prompt follows it
    ("2026-01-06 11:29:58", "none"),
])
def test_prompt_of_a_retrieval_follows_its_query_within_the_window(program, embedded_at, method):
    """A query is linked through the prompt that carries its retrieval, and that prompt is sent right after the
    retrieval. The same question is asked again and again: a prompt of another hour is the retrieval of another request."""
    rows = [embedding_row(QUESTION, timestamp=embedded_at), leave_request(timestamp="2026-01-05 11:30:00")]
    row = link(program, rows, [LLM, EMBEDDER, HANDBOOK])[0]
    assert (row["match_method"], row["caller_model_id"]) == (method, uuid(30) if method == "retrieval query" else "")


def test_window_of_a_retrieval_is_a_setting(program):
    rows = [embedding_row(QUESTION, timestamp="2026-01-05 11:20:00"), leave_request(timestamp="2026-01-05 11:30:00")]
    assert link(program, rows, [LLM, EMBEDDER, HANDBOOK])[0]["match_method"] == "none"
    assert link(program, rows, [LLM, EMBEDDER, HANDBOOK], query_window=600.0)[0]["match_method"] == "retrieval query"
    assert link(program, rows, [LLM, EMBEDDER, HANDBOOK], query_window=599.0)[0]["match_method"] == "none"


def test_query_gets_the_setup_of_the_nearest_prompt_that_follows_it(program):
    """The same question is asked of two collections. Each embedding belongs to the retrieval whose prompt comes next,
    and an embedding no prompt follows is linked to neither - not to the one setup the question was once asked of."""
    catalog = [LLM, EMBEDDER, HANDBOOK, rag_setup(uuid(32), "Travel policy", "travel_v1")]
    rows = [embedding_row(QUESTION, timestamp="2026-01-05 09:00:00"), leave_request(timestamp="2026-01-05 09:00:02"),
            embedding_row(QUESTION, timestamp="2026-01-05 09:01:00"), leave_request(collection="travel_v1", timestamp="2026-01-05 09:01:03"),
            embedding_row(QUESTION, timestamp="2026-01-05 15:00:00")]
    links = link(program, rows, catalog)
    assert [(links[position]["match_method"], links[position]["caller_name"]) for position in (0, 2, 4)] == [
        ("retrieval query", "Staff handbook"), ("retrieval query", "Travel policy"), ("none", "")]
    # Two prompts follow the first embedding within the window: the nearer one is its own
    assert links[0]["match_candidates"] == 1


def test_query_embedded_for_one_collection_is_not_linked_to_the_setup_of_another(program):
    """Review of the release before: the question was embedded for the travel policy, where no prompt carried it,
    and was linked to the handbook because a prompt of the handbook once held the same text."""
    catalog = [LLM, EMBEDDER, HANDBOOK, rag_setup(uuid(32), "Travel policy", "travel_v1")]
    rows = [embedding_row(QUESTION, timestamp="2026-01-05 09:00:00"), leave_request(timestamp="2026-01-05 09:00:02"),
            embedding_row(QUESTION, timestamp="2026-01-26 16:00:00")]
    query, prompt, later = link(program, rows, catalog)
    assert (query["match_method"], query["caller_name"], prompt["rag_model_id"]) == ("retrieval query", "Staff handbook", uuid(30))
    assert (later["match_method"], later["caller_model_id"]) == ("none", "")


def test_query_that_no_prompt_carried_is_not_linked(program):
    rows = [embedding_row("What is the dress code?"), leave_request()]
    row = link(program, rows, [LLM, EMBEDDER, HANDBOOK])[0]
    assert (row["match_method"], row["caller_model_id"], row["model_id"]) == ("none", "", uuid(3))


def test_query_that_was_asked_of_two_collections_is_ambiguous(program):
    rows = [embedding_row(QUESTION), leave_request(), leave_request(collection="travel_v1")]
    catalog = [LLM, EMBEDDER, HANDBOOK, rag_setup(uuid(32), "Travel policy", "travel_v1")]
    row = link(program, rows, catalog)[0]
    assert (row["match_method"], row["match_candidates"], row["caller_model_id"]) == ("ambiguous", 2, "")


def test_query_of_a_collection_without_a_setup_is_not_linked(program):
    assert link(program, [embedding_row(QUESTION), leave_request()], [LLM, EMBEDDER])[0]["match_method"] == "none"


def test_query_is_not_linked_to_a_setup_that_embeds_with_another_model(program):
    setup = rag_setup(uuid(30), "Staff handbook", "handbook_v1", target_model="other_embedder")
    assert link(program, [embedding_row(QUESTION), leave_request()], [LLM, EMBEDDER, setup])[0]["match_method"] == "none"
    setup = rag_setup(uuid(30), "Staff handbook", "handbook_v1", target_model="demo_embedder")
    assert link(program, [embedding_row(QUESTION), leave_request()], [LLM, EMBEDDER, setup])[0]["match_method"] == "retrieval query"


def test_ingestion_run_links_the_embedding_rows_inside_its_window(program):
    runs = [ingestion_run("run-1767604500", "handbook_v1", "2026-01-05 09:15:00", "2026-01-05 09:20:00", model_id=uuid(30))]
    times = ["2026-01-05 09:14:59", "2026-01-05 09:15:00", "2026-01-05 09:17:30", "2026-01-05 09:20:00", "2026-01-05 09:20:01"]
    rows = [embedding_row("Chapter 7: travel expenses.", mode="document", timestamp=timestamp) for timestamp in times]
    links = link(program, rows, [EMBEDDER, HANDBOOK], runs)
    assert [row["match_method"] for row in links] == ["none", "ingestion run", "ingestion run", "ingestion run", "none"]
    assert links[2] == {"model_id": uuid(3), "caller_model_id": uuid(30), "caller_project_id": uuid(900), "caller_kind": "rag", "caller_name": "Staff handbook",
                        "experiment_run_id": "", "rag_model_id": "", "match_method": "ingestion run", "match_candidates": 1}


def test_two_overlapping_ingestion_runs_link_nothing_where_they_overlap(program):
    runs = [ingestion_run("run-1767604500", "handbook_v1", "2026-01-05 09:15:00", "2026-01-05 09:20:00", model_id=uuid(30)),
            ingestion_run("run-1767604680", "travel_v1", "2026-01-05 09:18:00", "2026-01-05 09:25:00", model_id=uuid(32))]
    catalog = [EMBEDDER, HANDBOOK, rag_setup(uuid(32), "Travel policy", "travel_v1")]
    rows = [embedding_row("Chapter 7.", mode="document", timestamp=timestamp) for timestamp in ("2026-01-05 09:16:00", "2026-01-05 09:19:00", "2026-01-05 09:24:00")]
    links = link(program, rows, catalog, runs)
    assert [(row["match_method"], row["caller_name"], row["match_candidates"]) for row in links] == [
        ("ingestion run", "Staff handbook", 1), ("ambiguous", "", 2), ("ingestion run", "Travel policy", 1)]


def test_ingestion_run_of_another_embedding_model_or_without_an_end_links_nothing(program):
    runs = [ingestion_run("run-1767604500", "handbook_v1", "2026-01-05 09:15:00", "2026-01-05 09:20:00", embed_model="other_embedder", model_id=uuid(30)),
            ingestion_run("run-1767604501", "handbook_v1", "2026-01-05 09:15:00", "", model_id=uuid(30))]
    links, summary = program["link_rows"](lower_case(log_table(embedding_row("Chapter 7.", mode="document", timestamp="2026-01-05 09:17:00"))),
                                         lower_case(catalog_table(EMBEDDER, HANDBOOK)), lower_case(runs_table(*runs)))
    assert links["match_method"].tolist() == ["none"]
    assert summary["runs_without_window"] == 1


def test_ingestion_run_without_a_setup_id_takes_the_setup_of_its_collection(program):
    """A run table that was not stacked by Get-All-Prompts.sas has no modelID."""
    runs = [ingestion_run("run-1767604500", "handbook_v1", "2026-01-05 09:15:00", "2026-01-05 09:20:00")]
    row = embedding_row("Chapter 7.", mode="document", timestamp="2026-01-05 09:17:00")
    assert link(program, [row], [EMBEDDER, HANDBOOK], runs)[0]["caller_model_id"] == uuid(30)
    assert link(program, [row], [EMBEDDER], runs)[0]["match_method"] == "none"


def test_run_that_two_setups_hold_is_one_run(program):
    """The run is in RAG_RUNS once per setup that publishes the run table. It is one window, and which setup it was is open."""
    runs = [ingestion_run("run-1767604500", "handbook_v1", "2026-01-05 09:15:00", "2026-01-05 09:20:00", model_id=model_id) for model_id in (uuid(30), uuid(31))]
    row = embedding_row("Chapter 7.", mode="document", timestamp="2026-01-05 09:17:00")
    assert link(program, [row], [EMBEDDER, HANDBOOK], runs[:1] * 2)[0]["match_method"] == "ingestion run"
    both = link(program, [row], [EMBEDDER, HANDBOOK, rag_setup(uuid(31), "RAG Retrieval - Staff handbook", "handbook_v1")], runs)[0]
    assert (both["match_method"], both["match_candidates"]) == ("ambiguous", 2)


def test_clock_of_the_containers_ahead_of_utc(program):
    """The runs are recorded in UTC. A log of containers that run two hours ahead is moved back by the setting."""
    runs = [ingestion_run("run-1767604500", "handbook_v1", "2026-01-05 09:15:00", "2026-01-05 09:20:00", model_id=uuid(30))]
    row = embedding_row("Chapter 7.", mode="document", timestamp="2026-01-05 11:17:00")
    assert link(program, [row], [EMBEDDER, HANDBOOK], runs)[0]["match_method"] == "none"
    assert link(program, [row], [EMBEDDER, HANDBOOK], runs, utc_offset=2.0)[0]["match_method"] == "ingestion run"


def test_embedding_row_without_runs_table_or_mode(program):
    rows = [embedding_row("Chapter 7.", mode="document"), embedding_row("Chapter 7.", embedding_mode=None)]
    assert [row["match_method"] for row in link(program, rows, [EMBEDDER, HANDBOOK])] == ["none", "none"]


# ---------------------------------------------------------------------------
# A second run
# ---------------------------------------------------------------------------

def test_second_run_leaves_linked_rows_alone_and_a_redo_links_them_again(program):
    rows = lower_case(log_table(leave_request(), llm_row("You are a storyteller for children.", "Tell a story about a fox.")))
    first, _ = program["link_rows"](rows, lower_case(catalog_table(LLM, LEAVE_PROMPT)))
    assert first["match_method"].tolist() == ["template", "none"]
    rows[LINK_COLUMNS] = first

    # The prompt was renamed, a prompt for the second row was saved and a new request came in
    renamed = prompt_template(uuid(10), "Leave desk 2026", LEAVE_SYSTEM, LEAVE_USER)
    stories = prompt_template(uuid(14), "Stories", "You are a storyteller for children.", "Tell a story about {{animal}}.")
    catalog = lower_case(catalog_table(LLM, renamed, stories, HANDBOOK))
    rows = pd.DataFrame(rows.to_dict("records") + [leave_request(question="May I carry leave over?")], columns=LOG_COLUMNS)

    second, summary = program["link_rows"](rows, catalog)
    assert second.iloc[:2].to_dict("records") == first.to_dict("records")
    assert (second.loc[2, "caller_name"], second.loc[2, "rag_model_id"]) == ("Leave desk 2026", uuid(30))
    assert (summary["linked"], summary["left"]) == (1, 2)

    redone, summary = program["link_rows"](rows, catalog, redo_all=True)
    assert redone["caller_name"].tolist() == ["Leave desk 2026", "Stories", "Leave desk 2026"]
    assert redone["rag_model_id"].tolist() == [uuid(30), "", uuid(30)]
    assert (summary["linked"], summary["left"]) == (3, 0)


def test_linked_row_is_still_what_a_query_row_is_linked_through(program):
    """The prompt row was linked by an earlier run and is left alone; the embedding row that came later finds its setup through it."""
    linked = leave_request()
    linked.update(model_id=uuid(1), match_method="template", caller_model_id=uuid(10), match_candidates=1.0)
    links = link(program, [linked, embedding_row(QUESTION)], [LLM, EMBEDDER, HANDBOOK])
    assert (links[0]["match_method"], links[0]["rag_model_id"]) == ("template", "")
    assert (links[1]["match_method"], links[1]["caller_model_id"]) == ("retrieval query", uuid(30))


# ---------------------------------------------------------------------------
# The work is not rows times catalog
# ---------------------------------------------------------------------------

def test_row_is_only_compared_with_the_templates_its_system_prompt_leads_to(program):
    """500 requests and 5000 templates: every request is compared with the templates its own system
    prompt leads to, not with the catalog."""
    catalog = [LLM] + [prompt_template(uuid(1000 + number), f"Prompt {number}", f"You are assistant number {number} of the service desk.", "Question: {{question}}")
                       for number in range(3000)]
    catalog += [prompt_template(uuid(5000 + number), f"Desk {number}", "You work at desk " + str(number) + " and you answer {{topic}} questions.", "{{question}}")
                for number in range(1000)]
    catalog += [prompt_template(uuid(7000 + number), f"Signed {number}", "{{persona}} Always close with the number " + str(number) + " of your department.", "{{question}}")
                for number in range(1000)]
    rows = [llm_row(f"You are assistant number {number} of the service desk.", f"Question: what is {number} plus one?") for number in range(500)]
    compared = []
    fits = program["fits"]
    program["fits"] = lambda parts, squeezed: compared.append(1) or fits(parts, squeezed)
    links = link(program, rows, catalog)
    assert [row["caller_name"] for row in links] == [f"Prompt {number}" for number in range(500)]
    assert len(compared) == 500


def test_the_same_prompts_are_compared_once(program):
    compared = []
    fits = program["fits"]
    program["fits"] = lambda parts, squeezed: compared.append(1) or fits(parts, squeezed)
    links = link(program, [leave_request()] * 50, [LLM, LEAVE_PROMPT])
    assert {row["match_method"] for row in links} == {"template"}
    assert len(compared) == 1


# ---------------------------------------------------------------------------
# The table in CAS
# ---------------------------------------------------------------------------

SHOWCASE_ROWS = [
    leave_request(timestamp="2026-01-05 09:30:00"),
    decision_row(timestamp="2026-01-05 09:31:00"),
    embedding_row(QUESTION, timestamp="2026-01-05 09:29:00"),
    embedding_row("Chapter 7: travel expenses.", mode="document", timestamp="2026-01-05 09:17:00"),
    llm_row("You are a storyteller for children.", "Tell a story about a fox.", timestamp="2026-01-05 09:40:00"),
    embedding_row("What is the dress code?", timestamp="2026-01-05 09:41:00"),
]
SHOWCASE_CATALOG = catalog_table(LLM, DECIDER, EMBEDDER, LEAVE_PROMPT, TICKET_TEMPLATE, HANDBOOK)
SHOWCASE_RUNS = runs_table(ingestion_run("run-1767604500", "handbook_v1", "2026-01-05 09:15:00", "2026-01-05 09:20:00", model_id=uuid(30)))


def showcase_tables(log=None, catalog=SHOWCASE_CATALOG, runs=SHOWCASE_RUNS):
    tables = {("Public", "LLM_LOGS"): log_table(*SHOWCASE_ROWS) if log is None else log, ("Public", "MODEL_CATALOG"): catalog}
    if runs is not None:
        tables[("Public", "RAG_RUNS")] = runs
    return tables


def test_new_table_is_the_log_table_with_its_link_columns_filled(monkeypatch):
    before = log_table(*SHOWCASE_ROWS)
    run = run_program(monkeypatch, showcase_tables())
    after = run.new_table

    assert list(after.columns) == LOG_COLUMNS
    assert len(after) == len(before)
    pd.testing.assert_frame_equal(after[OLD_COLUMNS + PARSER_COLUMNS], before[OLD_COLUMNS + PARSER_COLUMNS])
    assert after["match_method"].tolist() == ["template", "questions and state", "retrieval query", "ingestion run", "none", "none"]
    assert after["model_id"].tolist() == [uuid(1), uuid(2), uuid(3), uuid(3), uuid(1), uuid(3)]
    assert after["caller_model_id"].fillna("").tolist() == [uuid(10), uuid(20), uuid(30), uuid(30), "", ""]
    assert after["rag_model_id"].fillna("").tolist() == [uuid(30), "", "", "", "", ""]
    assert run.sas.macro_variables["_lnk_row_count"] == "6"
    assert run.sas.macro_variables["_lnk_refused"] == "0"
    # The log table itself is only replaced by the SAS program, once the new table exists
    pd.testing.assert_frame_equal(FakeCAS.tables[("PUBLIC", "LLM_LOGS")], before)


def test_columns_the_program_does_not_know_stay_where_they_are(monkeypatch):
    """A site added a column in the middle of the table and one at its end."""
    log = log_table(*SHOWCASE_ROWS)
    log.insert(12, "site", ["Vienna", "Graz", "Linz", "Vienna", "Graz", "Linz"])
    log["reviewed_on"] = [21000.0, None, 21002.0, None, None, 21005.0]
    run = run_program(monkeypatch, showcase_tables(log))
    after = run.new_table
    assert list(after.columns) == list(log.columns)
    untouched = [column for column in log.columns if column not in LINK_COLUMNS]
    pd.testing.assert_frame_equal(after[untouched], log[untouched])


def test_rows_keep_their_order_when_cas_hands_them_out_in_another(monkeypatch):
    """The rows are numbered in the copy and the link columns are merged back by that number, so a
    table that comes back in another order than it was numbered in still gets every link on its row."""
    run = run_program(monkeypatch, showcase_tables())
    in_order = run.new_table

    fetch = FakeCAS._table_fetch

    def shuffled_fetch(self, table, **parameters):
        key = self.key(table["caslib"], table["name"])
        if key == ("CASUSER", "_LNK_STAGE"):
            original = FakeCAS.tables[key]
            FakeCAS.tables[key] = original.iloc[::-1].reset_index(drop=True)
            try:
                return fetch(self, table, **parameters)
            finally:
                FakeCAS.tables[key] = original
        return fetch(self, table, **parameters)

    monkeypatch.setattr(FakeCAS, "_table_fetch", shuffled_fetch)
    run = run_program(monkeypatch, showcase_tables())
    pd.testing.assert_frame_equal(run.new_table, in_order)


def test_link_columns_keep_the_labels_of_the_table_and_get_one_where_it_has_none(monkeypatch):
    labels = {("Public", "LLM_LOGS"): {"model_id": "Model Manager model", "timestamp": "The datetime of the request"}}
    run = run_program(monkeypatch, showcase_tables(), labels=labels)
    upload = run.cas.uploads[0]
    assert upload["casout"] == {"name": "_LNK_LINKS", "caslib": "CASUSER", "replace": True}
    variables = {variable["name"]: variable for variable in upload["importoptions"]["vars"]}
    assert list(variables) == ["_lnk_row", "_lnk_one"] + LINK_COLUMNS == list(upload["frame"].columns)
    assert variables["model_id"] == {"name": "model_id", "type": "varchar", "label": "Model Manager model"}
    assert variables["match_method"]["label"] == "The rule that linked the request to its caller"
    assert {variable["type"] for name, variable in variables.items() if name in LINK_COLUMNS[:-1]} == {"varchar"}
    assert variables["match_candidates"]["type"] == variables["_lnk_row"]["type"] == "double"
    # Only the link columns and the row number leave Python - no prompt, no response
    assert upload["frame"]["_lnk_row"].tolist() == [1, 2, 3, 4, 5, 6]


def test_summary_is_one_note_per_kind(monkeypatch):
    run = run_program(monkeypatch, showcase_tables())
    notes = run.sas.lines("NOTE")
    assert re.fullmatch(r"6 of 6 rows of Public\.LLM_LOGS were linked against 6 catalog rows in \d+\.\d\d seconds\.", notes[0])
    assert notes[1:] == [
        "2 llm rows: 2 linked to a model, caller by experiment run 0, template 1, optimization 0, judge 0, ambiguous 0, none 1, and 1 linked to a RAG setup.",
        "1 decision rows: 1 linked to a model, caller by experiment run 0, questions and state 1, questions 0, ambiguous 0, none 0.",
        "3 embedding rows: 3 linked to a model, caller by retrieval query 1, ingestion run 1, ambiguous 0, none 1.",
    ]
    assert run.sas.lines("ERROR") == [] and run.sas.lines("WARNING") == []
    # A semicolon would end the statement the line is written to the SAS log with, and the rest of the line would run as SAS code
    assert not [note for note in notes if ";" in note]


def test_notes_name_what_takes_no_part(monkeypatch):
    catalog = catalog_table(LLM, DECIDER, EMBEDDER, LEAVE_PROMPT, HANDBOOK, prompt_template(uuid(15), "Pass through", "{{system}}", "{{user}}"),
                            prompt_template(uuid(16), "Brief", "Answer briefly.", "{{question}}"))
    runs = runs_table(ingestion_run("run-1767604500", "handbook_v1", "2026-01-05 09:15:00", "", model_id=uuid(30)))
    run = run_program(monkeypatch, showcase_tables(catalog=catalog, runs=runs))
    notes = run.sas.lines("NOTE")
    assert notes[-2] == ("2 prompt(s) or decision template(s) of the catalog have a template with less than 20 characters of fixed text. "
                         "Such a template takes no part, as it would fit the requests of other callers.")
    assert notes[-1] == "1 ingestion run(s) have no start or no end and take no part."


def test_second_run_reads_the_prompts_of_the_new_rows_only(monkeypatch):
    first = run_program(monkeypatch, showcase_tables())
    linked = first.new_table
    new_row = leave_request(question="May I carry leave over?", timestamp="2026-01-06 08:00:00")
    log = pd.DataFrame(linked.to_dict("records") + [new_row], columns=LOG_COLUMNS)
    # The prompt is gone from the catalog: a row that was linked keeps its link all the same
    run = run_program(monkeypatch, showcase_tables(log, catalog=catalog_table(LLM, DECIDER, EMBEDDER, HANDBOOK)))

    fetches = [parameters for action, parameters in run.cas.actions if action == "table.fetch" and parameters["table"]["name"] == "_LNK_STAGE"]
    with_prompts = [parameters for parameters in fetches if {"name": "user_prompt"} in parameters["fetchVars"]]
    assert [parameters["table"].get("where") for parameters in with_prompts] == ["missing(match_method)"]
    after = run.new_table
    pd.testing.assert_frame_equal(after.iloc[:6].reset_index(drop=True), linked, check_dtype=False)
    assert (after.loc[6, "match_method"], after.loc[6, "rag_model_id"], after.loc[6, "model_id"]) == ("none", uuid(30), uuid(1))
    assert run.sas.lines("NOTE")[0].endswith("6 rows that have a match_method were left as they are.")

    redo = run_program(monkeypatch, showcase_tables(log, catalog=catalog_table(LLM, DECIDER, EMBEDDER, HANDBOOK)), _lnk_redo_all="1")
    with_prompts = [parameters for action, parameters in redo.cas.actions if action == "table.fetch" and {"name": "user_prompt"} in (parameters.get("fetchVars") or [])]
    assert [parameters["table"].get("where") for parameters in with_prompts] == [None]
    assert redo.new_table["match_method"].tolist() == ["none", "none", "retrieval query", "ingestion run", "none", "none", "none"]


def test_table_without_a_user_agent_column_is_linked(monkeypatch):
    """The user agent only says which rows can be experiment runs. A table without the column is linked without it."""
    log = log_table(*SHOWCASE_ROWS).drop(columns=["user_agent"])
    run = run_program(monkeypatch, showcase_tables(log))
    assert list(run.new_table.columns) == list(log.columns)
    assert run.new_table["match_method"].tolist() == ["template", "questions and state", "retrieval query", "ingestion run", "none", "none"]
    fetched = [variable["name"] for action, parameters in run.cas.actions if action == "table.fetch" for variable in parameters.get("fetchVars") or []]
    assert "user_agent" not in fetched


def test_table_is_read_page_by_page(monkeypatch):
    """The fetch action hands out a limited number of rows per call."""
    python = python_block(code_file_program()).replace("READ_PAGE = 2000", "READ_PAGE = 4")
    rows = [leave_request(question=f"Question number {number}?", timestamp="2026-01-05 10:00:00") for number in range(9)]
    run = run_program(monkeypatch, showcase_tables(log_table(*rows)), python=python)
    pages = [(parameters["from"], parameters["to"]) for action, parameters in run.cas.actions
             if action == "table.fetch" and parameters["table"]["name"] == "_LNK_STAGE" and {"name": "kind"} in parameters["fetchVars"]]
    assert pages == [(1, 4), (5, 8), (9, 12)]
    assert run.new_table["match_method"].tolist() == ["template"] * 9


def test_log_table_of_an_older_parser_is_refused_with_one_error_line(monkeypatch):
    twelve_columns = log_table(*SHOWCASE_ROWS)[OLD_COLUMNS]
    run = run_program(monkeypatch, showcase_tables(twelve_columns))
    errors = run.sas.lines("ERROR")
    assert len(errors) == 1
    assert errors[0].startswith("The table Public.LLM_LOGS has no column kind, embedding_mode, input_hash, rag_collection, rag_query_hash, model_id,")
    assert "Log-Parser-Code.sas" in errors[0] and "LLM - Log Parser" in errors[0] and errors[0].endswith("The table was left as it is.")
    assert run.sas.macro_variables["_lnk_refused"] == "1"
    assert run.sas.macro_variables["_lnk_row_count"] == "-1"
    # Nothing was copied, uploaded or built
    assert [action for action, _ in run.cas.actions] == ["table.columnInfo"]
    assert run.cas.uploads == [] and run.new_table is None


def test_table_that_is_no_catalog_is_refused_with_one_error_line(monkeypatch):
    no_catalog = SHOWCASE_CATALOG.drop(columns=["role"])
    run = run_program(monkeypatch, showcase_tables(catalog=no_catalog))
    errors = run.sas.lines("ERROR")
    assert len(errors) == 1
    assert "Public.MODEL_CATALOG has no column role" in errors[0] and "Get-All-Prompts.sas" in errors[0]
    assert run.sas.macro_variables["_lnk_refused"] == "1"
    assert run.cas.uploads == [] and run.new_table is None


def test_table_that_grew_while_it_was_linked_is_left_as_it_is(monkeypatch):
    """The parser appended rows after the rows were numbered. The new table would lose them, so it is not kept."""
    run = run_program(monkeypatch, showcase_tables(), added_meanwhile=log_table(llm_row()))
    errors = run.sas.lines("ERROR")
    assert len(errors) == 1
    assert errors[0].startswith("The new table has 6 rows, 6 were linked and Public.LLM_LOGS has 7:")
    assert run.new_table is None
    assert run.sas.macro_variables["_lnk_row_count"] == "-1"


def test_log_table_without_rows(monkeypatch):
    run = run_program(monkeypatch, showcase_tables(log_table()))
    assert run.sas.macro_variables["_lnk_row_count"] == "0"
    assert run.cas.uploads == [] and run.new_table is None


def test_runs_table_that_is_missing_or_has_other_columns(monkeypatch):
    run = run_program(monkeypatch, showcase_tables(runs=None), _lnk_runs_exists="0")
    assert run.new_table["match_method"].tolist()[3] == "none"
    assert not [parameters for action, parameters in run.cas.actions if action == "table.fetch" and parameters["table"]["name"] == "RAG_RUNS"]

    other = pd.DataFrame([{"run": "run-1767604500", "began": "2026-01-05 09:15:00"}])
    run = run_program(monkeypatch, showcase_tables(runs=other))
    assert run.new_table["match_method"].tolist()[3] == "none"
    assert "The table Public.RAG_RUNS has no column run_id, collection, embed_model, started_at, finished_at, so no embedding row is linked to an ingestion run." in run.sas.lines("NOTE")


def test_settings_reach_the_matching(monkeypatch):
    catalog = catalog_table(LLM, DECIDER, EMBEDDER, HANDBOOK, prompt_template(uuid(16), "Stories", "{{persona}}", "Tell a story about {{animal}}."))
    assert run_program(monkeypatch, showcase_tables(catalog=catalog)).new_table["match_method"].tolist()[4] == "none"
    run = run_program(monkeypatch, showcase_tables(catalog=catalog), _lnk_min_literal="15", _lnk_utc_offset="1")
    assert run.new_table["match_method"].tolist()[3:5] == ["none", "template"]


# ---------------------------------------------------------------------------
# What is handed to SAS
# ---------------------------------------------------------------------------

def uses_of_the_sas_object():
    """Every place of the program's Python that names the SAS object, as (what it takes of it, the function the place is in)."""
    tree = ast.parse(python_block(code_file_program()))
    found = []

    def visit(node, function):
        if isinstance(node, ast.FunctionDef):
            function = node.name
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "SAS":
            found.append((node.attr, function))
        for child in ast.iter_child_nodes(node):
            visit(child, function)

    visit(tree, None)
    return tree, found


def test_every_text_for_sas_takes_one_of_two_ways():
    """SAS.logMessage writes its text into a SAS statement and SAS.symput into an assignment. The program has one
    place for each: a message is made safe on its way, and a macro variable is only ever given a whole number,
    which takes the same way."""
    tree, found = uses_of_the_sas_object()
    assert sorted(found, key=str) == [("logMessage", "say"), ("sasfnc", None), ("sasfnc", None), ("symget", "setting"), ("symput", "put_number")]
    functions_of_the_program = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert ast.unparse(functions_of_the_program["say"].body[-1]) == "SAS.logMessage(sas_safe(message), messageType=message_type)"
    assert ast.unparse(functions_of_the_program["put_number"].body[-1]) == "SAS.symput(sas_safe(name), sas_safe(str(int(value))))"

    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    # The two options of the SAS session that say where CAS is
    assert sorted(ast.unparse(call) for call in calls if ast.unparse(call.func) == "SAS.sasfnc") == [
        "SAS.sasfnc('getoption', 'cashost')", "SAS.sasfnc('getoption', 'casport')"]
    # The macro variables the Python sets are named in the code, and the messages of the table are written by say
    assert sorted(ast.unparse(call.args[0]) for call in calls if ast.unparse(call.func) == "put_number") == ["'_lnk_refused'", "'_lnk_row_count'", "'_lnk_unchanged'"]
    assert [ast.unparse(call) for call in calls if ast.unparse(call.func) == "link_table"] == ["link_table(conn, settings, log=say)"]


def test_names_of_the_settings_stand_in_the_code_for_cas_as_names():
    """The code of the two DATA steps is the only code the Python writes. What it holds of the settings - the log
    table and its CAS library - is written as a name in quotes, so a quote in a name ends nothing."""
    tree, _ = uses_of_the_sas_object()
    codes = [keyword.value for node in ast.walk(tree) if isinstance(node, ast.Call) for keyword in node.keywords if keyword.arg == "code"]
    assert len(codes) == 2
    inserted = sorted({ast.unparse(value.value) for code in codes for value in ast.walk(code) if isinstance(value, ast.FormattedValue)})
    assert inserted == ["' '.join(LINK_COLUMNS)", "named(LINKS_TABLE)", "named(STAGE_TABLE)", "named(log_table)", "named(new_table)", "quoted(log_caslib)"]
    # The only condition a table is read under is written out in the code
    wheres = {ast.unparse(node) for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str) and "missing(" in node.value}
    assert wheres == {"'missing(match_method)'"}


HOSTILE_TEXTS = [
    "a caller; %put ERROR: this ran as SAS code;",
    "%let stolen = &sysuserid.;",
    "%nrstr(%put never;)",
    "it's a name",
    'a "quoted" name',
    "an open ( parenthesis",
    "a closed ) parenthesis ( and an open one",
    ")(",
    "line one\nline two\r\nline three\tand a tab",
    "the start /* of a comment",
    "the end */ of a comment",
    "a zero \x00 byte, an end of file \x1a mark and a line   separator",
    "half of a pair \udce9",
    "x" * 5000,
    "(" * 3000,
    "; " * 2000,
]


@pytest.mark.parametrize("hostile", HOSTILE_TEXTS)
def test_text_that_would_break_a_sas_statement_is_made_safe(program, hostile):
    assert unsafe_for_sas(hostile) != []
    assert unsafe_for_sas(program["sas_safe"](hostile)) == []


def test_text_that_sas_takes_as_it_is_stays_as_it_is(program):
    safe = program["sas_safe"]
    for harmless in ("6 of 6 rows of Public.LLM_LOGS were linked against 6 catalog rows in 0.01 seconds.",
                     "2 prompt(s) or decision template(s) of the catalog have a template with less than 20 characters of fixed text.",
                     "Größe: 3 - ok, [x] = 1, a/b, a*b"):
        assert safe(harmless) == harmless
    assert safe("50% of A&B; 'x' and \"y\"") == "50pct of A+B, `x` and `y`"
    assert safe("two\nlines") == "two lines"
    assert safe("a (b") == "a [b" and safe("(a) (b)") == "(a) (b)"
    long = safe("word " * 400)
    assert len(long) == 900 and long.startswith("word word ") and long.endswith("...")


HOSTILE_TABLE = "LLM';%put ERROR: ran as code;LOGS"
HOSTILE_CASLIB = "Pub'lic\"&sysuserid"
HOSTILE_NAME = "Leave desk'; %put ERROR: ran as code; %let x = &sysuserid; /* \") \n"


def hostile_tables(log, catalog):
    return {(HOSTILE_CASLIB, HOSTILE_TABLE): log, (HOSTILE_CASLIB, "CATALOG;%put x;"): catalog, (HOSTILE_CASLIB, "RUNS)"): pd.DataFrame([{"run": "run-1"}])}


HOSTILE_SETTINGS = {"_lnk_log_caslib": HOSTILE_CASLIB, "_lnk_log_table": HOSTILE_TABLE, "_lnk_catalog_caslib": HOSTILE_CASLIB, "_lnk_catalog_table": "CATALOG;%put x;",
                    "_lnk_runs_caslib": HOSTILE_CASLIB, "_lnk_runs_table": "RUNS)"}


def test_hostile_names_reach_sas_as_text_and_cas_as_names(monkeypatch):
    """Table names with a quote, a semicolon and a macro call, a model and a prompt whose names hold a statement:
    the tables are linked, CAS is given the names as they are, and every line for the SAS log is one text.
    run_program fails for a message SAS would not have taken as text."""
    deep = "[" * 100000 + "]" * 100000
    catalog = catalog_table(
        answer_model(HOSTILE_NAME, "llm", uuid(1)), answer_model(HOSTILE_NAME, "llm", uuid(4)), DECIDER, EMBEDDER, HANDBOOK,
        prompt_template(uuid(10), HOSTILE_NAME, LEAVE_SYSTEM, LEAVE_USER),
        prompt_template(uuid(21), HOSTILE_NAME, deep, TICKET_STATE, role="decision"))
    log = log_table(leave_request(model=HOSTILE_NAME), decision_row(questions=deep), embedding_row(QUESTION, timestamp="2026-01-05 09:14:58"))
    run = run_program(monkeypatch, hostile_tables(log, catalog), table_labels={(HOSTILE_CASLIB, HOSTILE_TABLE): HOSTILE_NAME}, **HOSTILE_SETTINGS)

    # CAS got the names as they are: the stand-in found the tables under them, and the new table is named after the log table
    after = run.new_table
    assert FakeCAS.key(HOSTILE_CASLIB, HOSTILE_TABLE + "_NEW") in FakeCAS.tables and len(after) == 3
    assert after["match_method"].tolist() == ["template", "none", "retrieval query"]
    # What is data stays what it was: the name of the caller in the table, the label of the table
    assert after.loc[0, "caller_name"] == " ".join(HOSTILE_NAME.split())
    assert run.new_table_label == HOSTILE_NAME.strip()
    steps = [parameters["code"] for action, parameters in run.cas.actions if action == "dataStep.runCode"]
    assert len(steps) == 2 and all("\n" not in step for step in steps)
    assert "set 'LLM'';%put ERROR: ran as code;LOGS'n(caslib='Pub''lic\"&sysuserid');" in steps[0]

    # Every kind of message that names something of the tables was written, and each of them as one text
    notes, warnings = run.sas.lines("NOTE"), run.sas.lines("WARNING")
    assert [note for note in notes if note.startswith("3 of 3 rows of Pub`lic`+sysuserid.LLM`,pct put ERROR: ran as code,LOGS were linked against 7 catalog rows in ")]
    assert [note for note in notes if note.startswith("The table Pub`lic`+sysuserid.RUNS] has no column run_id,")]
    assert [note for note in notes if note.startswith("The catalog holds 2 models named leave desk`, pct put error: ran as code, pct let x = +sysuserid, / * `] with the role llm - ")]
    assert warnings == [
        "The catalog row of Leave desk`, pct put ERROR: ran as code, pct let x = +sysuserid, / * `] [00000000-0000-0000-0000-000000000021] could not be read "
        "- RecursionError - and takes no part in the links.",
        "1 row(s) could not be compared with the catalog - RecursionError - and got match_method none.",
    ]
    assert run.sas.lines("ERROR") == []
    assert {name: value for name, value in run.sas.macro_variables.items() if name in ("_lnk_row_count", "_lnk_unchanged", "_lnk_refused")} == {
        "_lnk_row_count": "3", "_lnk_unchanged": "0", "_lnk_refused": "0"}


def test_refusal_of_a_table_with_a_hostile_name_is_one_safe_error_line(monkeypatch):
    twelve_columns = log_table(*SHOWCASE_ROWS)[OLD_COLUMNS]
    run = run_program(monkeypatch, hostile_tables(twelve_columns, SHOWCASE_CATALOG), **HOSTILE_SETTINGS)
    (error,) = run.sas.lines("ERROR")
    assert error.startswith("The table Pub`lic`+sysuserid.LLM`,pct put ERROR: ran as code,LOGS has no column kind, embedding_mode,")
    assert run.sas.macro_variables["_lnk_refused"] == "1" and run.new_table is None

    no_catalog = SHOWCASE_CATALOG.drop(columns=["role"])
    run = run_program(monkeypatch, hostile_tables(log_table(*SHOWCASE_ROWS), no_catalog), **HOSTILE_SETTINGS)
    (error,) = run.sas.lines("ERROR")
    assert error.startswith("The table Pub`lic`+sysuserid.CATALOG,pct put x, has no column role,")


@pytest.mark.parametrize("setting", ["_lnk_log_caslib", "_lnk_log_table", "_lnk_catalog_table", "_lnk_runs_table"])
def test_table_name_with_a_line_break_is_refused_before_anything_is_asked_of_cas(monkeypatch, setting):
    """A line break can not stand in a name of the code of a DATA step."""
    run = run_program(monkeypatch, showcase_tables(), **{setting: "LLM_LOGS\ndata x; run;"})
    (error,) = run.sas.lines("ERROR")
    assert error == ("The settings name a table or a CAS library with a line break or another character that is no text: LLM_LOGS data x, run,. "
                     "Nothing was linked.")
    assert run.cas.actions == [] and run.sas.macro_variables["_lnk_refused"] == "1"


def test_setting_that_is_no_number_gives_a_warning_and_the_default(monkeypatch):
    run = run_program(monkeypatch, showcase_tables(), _lnk_min_literal="20; %put x;", _lnk_utc_offset="", _lnk_query_window="soon")
    assert run.sas.lines("WARNING") == [
        "The setting _lnk_min_literal is 20, pct put x,, which is no number. 20 is taken in its place.",
        "The setting _lnk_utc_offset is empty, which is no number. 0 is taken in its place.",
        "The setting _lnk_query_window is soon, which is no number. 300 is taken in its place.",
    ]
    assert run.new_table["match_method"].tolist() == ["template", "questions and state", "retrieval query", "ingestion run", "none", "none"]


# ---------------------------------------------------------------------------
# The run of an experiment by its run time
# ---------------------------------------------------------------------------

RESOLVED = "Question: Why? Passages: none Answer:"


def leave_run(run_id, run_time, caller=10, name="Leave desk"):
    """A run of the Leave desk prompt against demo_llm, with the run time its tracker holds."""
    return experiment_run(uuid(caller), name, run_id, "demo_llm", LEAVE_SYSTEM, LEAVE_USER, LEAVE_SYSTEM, RESOLVED, run_time=run_time)


def sent(runtime, timestamp="2026-01-05 09:15:00", **values):
    """A request of the Builder page with the prompts of those runs and the run time the container logged."""
    return llm_row(LEAVE_SYSTEM, RESOLVED, runtime=runtime, timestamp=timestamp, user_agent="browser", **values)


def run_links(links):
    return [(row["match_method"], row["experiment_run_id"], row["match_candidates"]) for row in links]


def test_run_is_the_one_whose_run_time_is_the_run_time_of_the_request(program):
    """Runs 1 and 2 were sent with the same prompts. The tracker holds the run time each request returned, and the
    log holds the same number: each request gets its own run, and a third request with these prompts is no run."""
    catalog = [LLM, leave_run("1", 13.098107), leave_run("2", 15.004382)]
    rows = [sent(13.0981074), sent(15.0043821, "2026-01-05 09:17:00"), sent(14.2, "2026-01-05 09:30:00")]
    links = link(program, rows, catalog)
    assert run_links(links) == [("experiment run", "1", 1), ("experiment run", "2", 1), ("template", "", 1)]
    assert {row["caller_model_id"] for row in links} == {uuid(10)}
    # Whatever the order of the rows in the table
    assert run_links(link(program, rows[::-1], catalog)) == [("template", "", 1), ("experiment run", "2", 1), ("experiment run", "1", 1)]


@pytest.mark.parametrize("logged, linked", [
    (1.25, True), (1.2504, True), (1.2496, True),     # 1.250 with three decimals
    (1.251, False), (1.2494, False), (12.5, False),
    (None, False),                                    # a request that failed logged no run time
])
def test_run_times_are_compared_with_three_decimals(program, logged, linked):
    row = link(program, [sent(logged)], [LLM, leave_run("3", 1.25)])[0]
    assert (row["match_method"], row["experiment_run_id"]) == (("experiment run", "3") if linked else ("template", ""))
    assert row["caller_model_id"] == uuid(10)


def test_run_is_given_to_one_request_only(program):
    """Two requests with the prompts and the run time of one run: the earlier one is the run. The other one keeps
    the prompt as its caller and gets no run."""
    catalog = [LLM, leave_run("3", 1.25)]
    rows = [sent(1.25, "2026-01-05 09:20:00"), sent(1.25, "2026-01-05 09:15:00"), sent(1.25, "2026-01-05 09:25:00")]
    assert run_links(link(program, rows, catalog)) == [("template", "", 1), ("experiment run", "3", 1), ("template", "", 1)]
    # Two runs with the same prompts and the same run time: the requests get them in their order
    twins = [LLM, leave_run("4", 1.25), leave_run("3", 1.25)]
    assert run_links(link(program, rows, twins)) == [("experiment run", "4", 2), ("experiment run", "3", 2), ("template", "", 1)]


def test_run_that_a_row_holds_from_an_earlier_link_is_not_given_again(program):
    held = sent(1.25)
    held.update(model_id=uuid(1), match_method="experiment run", caller_model_id=uuid(10), caller_kind="prompt", experiment_run_id="3", match_candidates=1.0)
    rows = [held, sent(1.25, "2026-01-05 09:00:00")]
    links = link(program, rows, [LLM, leave_run("3", 1.25)])
    assert run_links(links) == [("experiment run", "3", 1.0), ("template", "", 1)]
    # Linked again, the earlier request is the run
    assert run_links(link(program, rows, [LLM, leave_run("3", 1.25)], redo_all=True)) == [("template", "", 1), ("experiment run", "3", 1)]


def test_catalog_without_run_times_links_a_run_as_the_release_before_did_and_says_so(program):
    """Without a run time nothing tells the requests with the prompts of a run apart: each of them gets the newest
    run with these prompts, match_candidates holds the number of those runs and the summary counts the rows."""
    rows = lower_case(log_table(sent(1.25), sent(7.5, "2026-01-05 09:20:00")))
    catalog = catalog_table(LLM, *[leave_run(run_id, None) for run_id in ("9", "10", "2")])
    links, summary = program["link_rows"](rows, lower_case(catalog))
    assert run_links(links.to_dict("records")) == [("experiment run", "10", 3), ("experiment run", "10", 3)]
    assert summary["untimed_runs"] == 2
    # The catalog of the release before has no such column
    links, summary = program["link_rows"](rows, lower_case(catalog).drop(columns=["run_time", "window_start", "window_end", "optimization_id"]))
    assert run_links(links.to_dict("records")) == [("experiment run", "10", 3), ("experiment run", "10", 3)]
    assert summary["untimed_runs"] == 2

    # A run with a run time is compared, also beside one without: the request that is none of the timed runs is left to the run without
    mixed = lower_case(catalog_table(LLM, leave_run("1", None), leave_run("2", 1.25), leave_run("3", 9.0)))
    links, summary = program["link_rows"](rows, mixed)
    assert run_links(links.to_dict("records")) == [("experiment run", "2", 1), ("experiment run", "1", 1)]
    assert summary["untimed_runs"] == 1


def test_run_without_a_run_time_is_named_in_the_log(monkeypatch):
    log = log_table(sent(1.25), sent(7.5, "2026-01-05 09:20:00"))
    run = run_program(monkeypatch, showcase_tables(log, catalog=catalog_table(LLM, leave_run("3", None))))
    assert run.new_table["experiment_run_id"].tolist() == ["3", "3"]
    assert run.sas.lines("NOTE")[-1] == ("2 row(s) were linked to an experiment run without a run time to compare, as the catalog holds none for the runs with their prompts. "
                                        "They carry the newest of those runs, and match_candidates is the number of those runs.")
    timed = run_program(monkeypatch, showcase_tables(log, catalog=catalog_table(LLM, leave_run("3", 1.25))))
    assert timed.new_table["experiment_run_id"].fillna("").tolist() == ["3", ""]
    assert not [note for note in timed.sas.lines("NOTE") if "without a run time" in note]


def test_decision_run_is_found_by_its_run_time_as_well(program):
    state = "Ticket: I was charged twice for my plan this morning."
    runs = [experiment_run(uuid(20), "Ticket routing", run_id, "demo_decider", json.dumps(QUESTIONS, indent=2), TICKET_STATE, "", state, role="decision", run_time=run_time)
            for run_id, run_time in (("7", 0.4), ("8", 0.613))]
    rows = [decision_row(runtime=0.6129), decision_row(runtime=0.4), decision_row(runtime=0.33)]
    links = link(program, rows, [DECIDER] + runs, agent="browser")
    assert run_links(links) == [("experiment run", "8", 1), ("experiment run", "7", 1), ("questions and state", "", 1)]
    assert {row["caller_model_id"] for row in links} == {uuid(20)}


def test_run_time_decides_between_the_runs_of_two_prompts_with_the_same_prompts(program):
    runs = [leave_run("1", 1.25), leave_run("1", 2.5, caller=11, name="Leave desk copy")]
    first, second, third = link(program, [sent(2.5), sent(1.25), sent(4.0)], [LLM] + runs)
    assert (first["match_method"], first["caller_name"], second["caller_name"]) == ("experiment run", "Leave desk copy", "Leave desk")
    # The request that is neither run fits the templates of both prompts
    assert (third["match_method"], third["match_candidates"], third["experiment_run_id"]) == ("ambiguous", 2, "")
    same = [leave_run("1", 1.25), leave_run("1", 1.25, caller=11, name="Leave desk copy")]
    row = link(program, [sent(1.25)], [LLM] + same)[0]
    assert (row["match_method"], row["match_candidates"], row["caller_model_id"]) == ("ambiguous", 2, "")


def test_rows_that_no_longer_find_their_run_are_counted(monkeypatch):
    """The id of a run is its place in the tracker. Run 1 is deleted in the Builder, the run that was 2 is 1 now,
    and the catalog is pulled again: the rows that were linked before name runs that are no longer theirs."""
    log = log_table(sent(1.25), sent(2.5, "2026-01-05 09:20:00"))
    first = run_program(monkeypatch, showcase_tables(log, catalog=catalog_table(LLM, leave_run("1", 1.25), leave_run("2", 2.5))))
    linked = first.new_table
    assert linked["experiment_run_id"].tolist() == ["1", "2"] and first.sas.lines("WARNING") == []

    renumbered = catalog_table(LLM, leave_run("1", 2.5))
    run = run_program(monkeypatch, showcase_tables(linked, catalog=renumbered))
    assert run.sas.lines("WARNING") == [
        "2 row(s) that were linked to an experiment run earlier no longer name that run: for 1 the catalog has no run with their id, for 1 the run with their id has "
        "another run time than the request. The id of a run is its place in the tracker and changes when a run is deleted. Link all rows again with _lnk_redo_all = 1."]
    # Nothing was linked, so the table is as it was
    assert run.new_table is None and run.sas.macro_variables["_lnk_unchanged"] == "1"

    redone = run_program(monkeypatch, showcase_tables(linked, catalog=renumbered), _lnk_redo_all="1")
    assert redone.new_table["experiment_run_id"].fillna("").tolist() == ["", "1"]
    assert redone.new_table["match_method"].tolist() == ["template", "experiment run"]
    assert redone.sas.lines("WARNING") == []


def test_rows_of_a_catalog_without_run_times_are_only_counted_when_their_run_is_gone(program):
    held = sent(1.25)
    held.update(model_id=uuid(1), match_method="experiment run", caller_model_id=uuid(10), experiment_run_id="3", match_candidates=1.0)
    rows = lower_case(log_table(held))
    for catalog, stale in (([LLM, leave_run("3", None)], {"gone": 0, "other time": 0}), ([LLM, leave_run("3", 1.2504)], {"gone": 0, "other time": 0}),
                           ([LLM, leave_run("3", 8.0)], {"gone": 0, "other time": 1}), ([LLM, leave_run("2", 1.25)], {"gone": 1, "other time": 0}),
                           ([LLM, LEAVE_PROMPT], {"gone": 1, "other time": 0})):
        _, summary = program["link_rows"](rows, lower_case(catalog_table(*catalog)))
        assert summary["stale_runs"] == stale


# ---------------------------------------------------------------------------
# The calls the Builder makes for a prompt: an optimization, a judge
# ---------------------------------------------------------------------------

# What an optimizer makes of the system prompt of the prompt it works on: its own frame around it
FRAME = "The fields you are given are question and passages. The field you return is response.\nKeep to this structure. Your task:\n"
OPTIMIZER_SYSTEM = FRAME + LEAVE_SYSTEM
WINDOW = ("2026-01-05T10:00:00.250000+00:00", "2026-01-05T10:01:00.750000+00:00")
LEAVE_OPTIMIZATION = optimization_call(uuid(10), "Leave desk", "demo_llm", LEAVE_SYSTEM, WINDOW)
JUDGE = answer_model("demo_judge", "llm", uuid(4))
JUDGE_SYSTEM = "You compare the answers several assistants gave to one task and name the best of them."


def optimizer_request(**values):
    """A request an optimizer sent to demo_llm while it worked on the Leave desk prompt."""
    request = dict(system_prompt=OPTIMIZER_SYSTEM, user_prompt="question: How many days of leave?\npassages: none", timestamp="2026-01-05 10:00:30")
    request.update(values)
    return llm_row(**request)


def judge_request(system=LEAVE_SYSTEM, user=RESOLVED, **values):
    """The request a judge gets from the Builder page: the prompts of the run and the answers to rank."""
    shown = f"The task, its system prompt:\n{system}\n\nThe task, its user prompt:\n{user}\n\nThe answers:\n[A]\n25 days.\n\n[B]\nThirty days, chapter 4."
    request = dict(system_prompt=JUDGE_SYSTEM, user_prompt=shown, model="demo_judge", user_agent="browser")
    request.update(values)
    return llm_row(**request)


def test_call_of_an_optimization_is_linked_to_the_prompt_it_was_made_for(program):
    """The request is no request of the prompt: its system prompt is the frame of the optimizer around the system
    prompt of the prompt. It holds that text, it went to the model the optimization ran against, and it was sent
    while the optimization ran."""
    assert link(program, [optimizer_request()], [LLM, LEAVE_PROMPT])[0]["match_method"] == "none"
    row = link(program, [optimizer_request()], [LLM, LEAVE_PROMPT, LEAVE_OPTIMIZATION])[0]
    assert row == {"model_id": uuid(1), "caller_model_id": uuid(10), "caller_project_id": uuid(900), "caller_kind": "prompt", "caller_name": "Leave desk",
                   "experiment_run_id": "", "rag_model_id": "", "match_method": "optimization", "match_candidates": 1}


@pytest.mark.parametrize("change, method", [
    ({"timestamp": "2026-01-05 10:00:00"}, "optimization"),     # the second the optimization started in
    ({"timestamp": "2026-01-05 10:01:01"}, "optimization"),     # the second it ended in
    ({"timestamp": "2026-01-05 09:59:59"}, "none"),
    ({"timestamp": "2026-01-05 10:01:02"}, "none"),
    ({"timestamp": "2026-01-06 10:00:30"}, "none"),
    ({"model": "other_llm"}, "none"),                           # not the model the optimization ran against
    ({"system_prompt": FRAME + "You answer questions of employees about the canteen."}, "none"),
    ({"system_prompt": FRAME, "user_prompt": "instructions so far: " + LEAVE_SYSTEM}, "optimization"),   # the text stands in the user prompt
    ({"user_agent": "browser"}, "optimization"),
])
def test_call_of_an_optimization_needs_the_text_the_model_and_the_time(program, change, method):
    row = link(program, [optimizer_request(**change)], [LLM, LEAVE_PROMPT, LEAVE_OPTIMIZATION])[0]
    assert (row["match_method"], row["caller_model_id"]) == (method, uuid(10) if method == "optimization" else "")


def test_window_of_an_optimization_is_utc_like_the_ingestion_runs(program):
    late = optimizer_request(timestamp="2026-01-05 12:00:30")
    assert link(program, [late], [LLM, LEAVE_OPTIMIZATION])[0]["match_method"] == "none"
    assert link(program, [late], [LLM, LEAVE_OPTIMIZATION], utc_offset=2.0)[0]["match_method"] == "optimization"


def test_optimization_against_several_models_and_one_without_a_window(program):
    several = optimization_call(uuid(10), "Leave desk", "other_llm, Demo_LLM", LEAVE_SYSTEM, WINDOW)
    assert link(program, [optimizer_request()], [LLM, several])[0]["match_method"] == "optimization"
    # Where the catalog holds no window, the text and the model link the call at any time
    open_end = optimization_call(uuid(10), "Leave desk", "demo_llm", LEAVE_SYSTEM)
    assert link(program, [optimizer_request(timestamp="2026-03-01 08:00:00")], [LLM, open_end])[0]["match_method"] == "optimization"
    started = optimization_call(uuid(10), "Leave desk", "demo_llm", LEAVE_SYSTEM, (WINDOW[0], ""))
    assert [row["match_method"] for row in link(program, [optimizer_request(timestamp="2026-01-05 09:00:00"), optimizer_request(timestamp="2026-03-01 08:00:00")],
                                                [LLM, started])] == ["none", "optimization"]


@pytest.mark.parametrize("unusable", [
    optimization_call(uuid(10), "Leave desk", "", LEAVE_SYSTEM, WINDOW),                           # no model
    optimization_call(uuid(10), "Leave desk", "demo_llm", "", WINDOW),                             # no text
    optimization_call(uuid(10), "Leave desk", "demo_llm", "Answer briefly.", WINDOW),              # 14 characters
    optimization_call(uuid(10), "Leave desk", "demo_llm", LEAVE_SYSTEM, ("last Monday", WINDOW[1])),
    optimization_call(uuid(10), "Leave desk", "demo_llm", LEAVE_SYSTEM, (WINDOW[1], WINDOW[0])),   # it ends before it starts
])
def test_call_row_of_the_catalog_that_can_not_be_checked_takes_no_part(program, unusable):
    """A row that names no model, has too little text to be told from other requests, or a window that is none:
    it links nothing, and the summary counts it."""
    request = optimizer_request(system_prompt=FRAME + LEAVE_SYSTEM + " Answer briefly.")
    links, summary = program["link_rows"](lower_case(log_table(request)), lower_case(catalog_table(LLM, unusable)))
    assert links["match_method"].tolist() == ["none"] and summary["unusable_calls"] == 1


def test_call_rows_that_take_no_part_are_named_in_the_log(monkeypatch):
    catalog = catalog_table(LLM, DECIDER, EMBEDDER, LEAVE_PROMPT, optimization_call(uuid(10), "Leave desk", "", LEAVE_SYSTEM, WINDOW))
    run = run_program(monkeypatch, showcase_tables(catalog=catalog))
    assert ("1 catalog row(s) of an optimization or a judge name no model, hold less than 20 characters of text or have a window that can not be read. "
            "Such a row takes no part.") in run.sas.lines("NOTE")


def test_request_that_is_the_text_of_a_call_is_no_call(program):
    """The published prompt was called while it was optimized, with an input its user template does not fit. The
    request is the system prompt of the prompt, it does not hold it: nothing says an optimization sent it."""
    row = llm_row(LEAVE_SYSTEM, "Something its user template does not fit.", timestamp="2026-01-05 10:00:30")
    assert link(program, [row], [LLM, LEAVE_PROMPT, LEAVE_OPTIMIZATION])[0]["match_method"] == "none"


def test_experiment_run_and_template_are_ahead_of_the_calls_made_for_a_prompt(program):
    """A prompt whose templates take the whole request of the optimizer is the caller, as for any other request."""
    wrapped = prompt_template(uuid(12), "Optimizer frame", "The fields you are given are {{fields}} Your task: {{task}}", "{{input}}")
    row = link(program, [optimizer_request()], [LLM, wrapped, LEAVE_OPTIMIZATION])[0]
    assert (row["match_method"], row["caller_name"]) == ("template", "Optimizer frame")
    run = experiment_run(uuid(12), "Optimizer frame", "5", "demo_llm", "{{all}}", "{{input}}", OPTIMIZER_SYSTEM, "question: How many days of leave?\npassages: none", run_time=1.25)
    row = link(program, [optimizer_request(user_agent="browser")], [LLM, run, LEAVE_OPTIMIZATION])[0]
    assert (row["match_method"], row["caller_name"], row["experiment_run_id"]) == ("experiment run", "Optimizer frame", "5")


def test_call_of_a_judge_is_linked_to_the_prompt_and_the_run_it_judged(program):
    """The judge is shown the prompts of the run and its answers. The catalog holds the prompts of every run that
    was judged, and the model that judged."""
    judged = judge_call(uuid(10), "Leave desk", "3", "demo_judge", LEAVE_SYSTEM, RESOLVED)
    catalog = [LLM, JUDGE, LEAVE_PROMPT, judged]
    row = link(program, [judge_request()], catalog)[0]
    assert row == {"model_id": uuid(4), "caller_model_id": uuid(10), "caller_project_id": uuid(900), "caller_kind": "prompt", "caller_name": "Leave desk",
                   "experiment_run_id": "3", "rag_model_id": "", "match_method": "judge", "match_candidates": 1}
    # The same request to a model that is not the judge of that run, and the judgment of other prompts
    assert link(program, [judge_request(model="demo_llm")], catalog)[0]["match_method"] == "none"
    assert link(program, [judge_request(user="Question: Why not? Passages: none Answer:")], catalog)[0]["match_method"] == "none"
    assert link(program, [judge_request(system="You answer questions of employees about the canteen.")], catalog)[0]["match_method"] == "none"
    # A call of the score code of a published model is no less a judge call than one of a browser
    assert link(program, [judge_request(user_agent="python-requests")], catalog)[0]["match_method"] == "judge"


def test_judge_call_names_the_run_where_the_catalog_leaves_one(program):
    """Two runs with the same prompts were judged: the call is one of the prompt, and which run it judged is not said
    - unless the catalog holds how long each judgment took."""
    judged = [judge_call(uuid(10), "Leave desk", run_id, "demo_judge", LEAVE_SYSTEM, RESOLVED) for run_id in ("3", "4")]
    row = link(program, [judge_request()], [JUDGE] + judged)[0]
    assert (row["match_method"], row["caller_model_id"], row["experiment_run_id"], row["match_candidates"]) == ("judge", uuid(10), "", 1)
    timed = [judge_call(uuid(10), "Leave desk", run_id, "demo_judge", LEAVE_SYSTEM, RESOLVED, run_time=run_time) for run_id, run_time in (("3", 6.5), ("4", 7.25))]
    assert [row["experiment_run_id"] for row in link(program, [judge_request(runtime=7.2501), judge_request(runtime=6.5), judge_request(runtime=9.0)], [JUDGE] + timed)] == ["4", "3", ""]
    # The run whose prompts the call holds in full is ahead of one whose prompts are a part of them
    longer = [judge_call(uuid(10), "Leave desk", "3", "demo_judge", LEAVE_SYSTEM, RESOLVED),
              judge_call(uuid(10), "Leave desk", "9", "demo_judge", LEAVE_SYSTEM, "Postscript. " + RESOLVED + " In one sentence.")]
    assert [row["experiment_run_id"] for row in link(program, [judge_request(), judge_request(user="Postscript. " + RESOLVED + " In one sentence.")], [JUDGE] + longer)] == ["3", "9"]


def test_call_that_fits_two_prompts_is_ambiguous_and_the_one_that_holds_more_wins(program):
    calls = [judge_call(uuid(10), "Leave desk", "3", "demo_judge", LEAVE_SYSTEM, RESOLVED), judge_call(uuid(11), "Leave desk copy", "1", "demo_judge", LEAVE_SYSTEM, RESOLVED)]
    row = link(program, [judge_request()], [JUDGE] + calls)[0]
    assert (row["match_method"], row["match_candidates"]) == ("ambiguous", 2)
    assert [row[column] for column in CALLER_COLUMNS] == [""] * 5 and row["model_id"] == uuid(4)
    calls[1] = judge_call(uuid(11), "Leave desk copy", "1", "demo_judge", LEAVE_SYSTEM, "none Answer:")
    row = link(program, [judge_request()], [JUDGE] + calls)[0]
    assert (row["match_method"], row["caller_name"], row["experiment_run_id"], row["match_candidates"]) == ("judge", "Leave desk", "3", 2)


def test_optimization_is_asked_before_the_judge(program):
    """An optimization that scores with a judge sends the judge the task as well. The window says that it was
    the optimization, and the run of an earlier judgment is not written to the row."""
    catalog = [JUDGE, optimization_call(uuid(10), "Leave desk", "demo_judge", LEAVE_SYSTEM, WINDOW), judge_call(uuid(10), "Leave desk", "3", "demo_judge", LEAVE_SYSTEM, RESOLVED)]
    inside, outside = link(program, [judge_request(timestamp="2026-01-05 10:00:30"), judge_request(timestamp="2026-01-05 11:00:00")], catalog)
    assert (inside["match_method"], inside["experiment_run_id"]) == ("optimization", "")
    assert (outside["match_method"], outside["experiment_run_id"]) == ("judge", "3")


def test_caller_of_a_call_is_described_by_the_catalog_row_of_the_caller_itself(program):
    """The row of a call names the model it was made for. What kind of caller that is and how it is named
    says the row of the template, whichever of the two the catalog holds first."""
    call = optimization_call(uuid(20), "Ticket routing as the optimizer names it", "demo_llm", LEAVE_SYSTEM, WINDOW)
    for catalog in ([LLM, call, TICKET_TEMPLATE], [LLM, TICKET_TEMPLATE, call]):
        row = link(program, [optimizer_request()], catalog)[0]
        assert (row["match_method"], row["caller_kind"], row["caller_name"], row["caller_model_id"]) == ("optimization", "decision", "Ticket routing", uuid(20))
    row = link(program, [optimizer_request()], [LLM, call])[0]
    assert (row["caller_kind"], row["caller_name"]) == ("prompt", "Ticket routing as the optimizer names it")


def test_decision_and_embedding_rows_are_no_calls_for_a_prompt(program):
    catalog = [DECIDER, EMBEDDER, optimization_call(uuid(10), "Leave desk", "demo_decider", "Ticket: I was charged twice"),
               optimization_call(uuid(10), "Leave desk", "demo_embedder", "Ticket: I was charged twice")]
    assert [row["match_method"] for row in link(program, [decision_row(), embedding_row()], catalog)] == ["none", "none"]


def test_many_calls_for_one_model_are_not_compared_one_by_one(program):
    """300 runs of one prompt were judged by one model. Their prompts start alike and end alike: a request is
    compared with the calls a piece of its own text leads to, not with all of them."""
    def prompts(number):
        return "You are assistant number one of the service desk and you answer in one sentence.", f"Question: what is {number} plus one? Answer with the number only, please."

    calls = [judge_call(uuid(10), "Service desk", str(number), "demo_judge", *prompts(number)) for number in range(1, 301)]
    rows = [judge_request(*prompts(number)) for number in (1, 57, 300, 301)]
    compared = []
    holds = program["holds"]
    program["holds"] = lambda texts, system, user: compared.append(1) or holds(texts, system, user)
    links = link(program, rows, [JUDGE] + calls)
    assert [(row["match_method"], row["experiment_run_id"]) for row in links] == [("judge", "1"), ("judge", "57"), ("judge", "300"), ("none", "")]
    assert len(compared) <= 8
    # With as few calls as a model usually has, each of them is compared and the result is the same
    compared.clear()
    links = link(program, rows, [JUDGE] + calls[:20] + calls[56:57])
    assert [(row["match_method"], row["experiment_run_id"]) for row in links] == [("judge", "1"), ("judge", "57"), ("none", ""), ("none", "")]
    assert len(compared) == 4 * 21


def test_many_calls_whose_texts_have_no_word_to_start_at_are_looked_for_at_every_place(program):
    """The piece a call is found through starts where a word of its text starts, and is looked for where a word of
    the prompt starts. A text of one word has no such place: with such a call the pieces are looked for everywhere."""
    calls = [judge_call(uuid(10), "Archive", str(number), "demo_judge", f"ticket-{number:04d}-of-the-archive-of-the-service-desk", "") for number in range(1, 61)]
    rows = [judge_request(system="see below", user=f"ref=ticket-{number:04d}-of-the-archive-of-the-service-desk!") for number in (7, 60, 61)]
    assert [(row["match_method"], row["experiment_run_id"]) for row in link(program, rows, [JUDGE] + calls)] == [("judge", "7"), ("judge", "60"), ("none", "")]


def test_summary_counts_the_calls_made_for_a_prompt(monkeypatch):
    log = log_table(leave_request(timestamp="2026-01-05 09:30:00"), optimizer_request(), judge_request(timestamp="2026-01-05 11:00:00"))
    catalog = catalog_table(LLM, JUDGE, LEAVE_PROMPT, LEAVE_OPTIMIZATION, judge_call(uuid(10), "Leave desk", "3", "demo_judge", LEAVE_SYSTEM, RESOLVED))
    run = run_program(monkeypatch, showcase_tables(log, catalog=catalog))
    assert run.new_table["match_method"].tolist() == ["template", "optimization", "judge"]
    assert run.new_table["caller_model_id"].tolist() == [uuid(10)] * 3 and run.new_table["experiment_run_id"].fillna("").tolist() == ["", "", "3"]
    assert run.sas.lines("NOTE")[1] == "3 llm rows: 3 linked to a model, caller by experiment run 0, template 1, optimization 1, judge 1, ambiguous 0, none 0, and 0 linked to a RAG setup."


# ---------------------------------------------------------------------------
# Content that can not be read
# ---------------------------------------------------------------------------

# Nested deeper than Python reads JSON: json.loads raises a RecursionError, which is no ValueError
DEEP = "[" * 100000 + "]" * 100000


def test_row_whose_content_can_not_be_read_is_not_linked_and_the_rows_around_it_are(program):
    rows = lower_case(log_table(decision_row(), decision_row(questions=DEEP), decision_row(questions=DEEP, user_agent="browser"), decision_row()))
    links, summary = program["link_rows"](rows, lower_case(catalog_table(DECIDER, TICKET_TEMPLATE)))
    assert links["match_method"].tolist() == ["questions and state", "none", "none", "questions and state"]
    assert links["model_id"].tolist() == [uuid(2)] * 4 and links["match_candidates"].tolist() == [1, 0, 0, 1]
    assert summary["failed_rows"] == {1: "RecursionError", 2: "RecursionError"}
    # The row has a match_method now, so the next run does not meet it again
    rows[LINK_COLUMNS] = links
    _, summary = program["link_rows"](rows, lower_case(catalog_table(DECIDER, TICKET_TEMPLATE)))
    assert summary["failed_rows"] == {} and summary["linked"] == 0


@pytest.mark.parametrize("broken", [
    prompt_template(uuid(21), "Deep questions", DEEP, TICKET_STATE, role="decision"),
    experiment_run(uuid(21), "Deep questions", "1", "demo_decider", "{}", TICKET_STATE, DEEP, "Ticket: x", role="decision"),
])
def test_catalog_row_whose_content_can_not_be_read_is_left_out_with_a_warning(monkeypatch, broken):
    catalog = catalog_table(LLM, DECIDER, EMBEDDER, LEAVE_PROMPT, broken, TICKET_TEMPLATE, HANDBOOK)
    log = log_table(*SHOWCASE_ROWS, decision_row(questions=DEEP, timestamp="2026-01-05 09:50:00"))
    run = run_program(monkeypatch, showcase_tables(log, catalog=catalog))
    assert run.new_table["match_method"].tolist() == ["template", "questions and state", "retrieval query", "ingestion run", "none", "none", "none"]
    assert run.sas.lines("WARNING") == [
        "The catalog row of Deep questions (00000000-0000-0000-0000-000000000021) could not be read - RecursionError - and takes no part in the links.",
        "1 row(s) could not be compared with the catalog - RecursionError - and got match_method none.",
    ]
    assert run.sas.lines("ERROR") == [] and run.sas.macro_variables["_lnk_row_count"] == "7"
    # Nothing of the row that was left out is in the index
    index = run.namespace["build_index"](lower_case(catalog))
    assert uuid(21) not in index["callers"] and index["skipped"] == [("Deep questions", uuid(21), "RecursionError")]


def test_many_catalog_rows_that_can_not_be_read_are_counted_after_the_first_five(program):
    catalog = [DECIDER, TICKET_TEMPLATE] + [prompt_template(uuid(40 + number), f"Deep {number}", DEEP, TICKET_STATE, role="decision") for number in range(8)]
    _, summary = program["link_rows"](lower_case(log_table(decision_row())), lower_case(catalog_table(*catalog)))
    lines = program["warning_lines"](summary)
    assert len(lines) == 6 and lines[0].startswith("The catalog row of Deep 0 (") and lines[5] == "3 more catalog rows could not be read and take no part in the links."


# ---------------------------------------------------------------------------
# Links that were made too easily
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("project, method", [("rag", "ingestion run"), ("RAG", "ingestion run"), ("", "ingestion run"), (None, "ingestion run"),
                                             ("my own script", "none"), ("handbook", "none")])
def test_ingestion_run_links_the_documents_the_rag_runtime_embedded(program, project, method):
    """The RAG runtime sends rag as the project of every request. A document another program embedded while a run
    was under way lies in the window of that run and is no chunk of it."""
    runs = [ingestion_run("run-1767604500", "handbook_v1", "2026-01-05 09:15:00", "2026-01-05 09:20:00", model_id=uuid(30))]
    row = embedding_row("Chapter 7.", mode="document", timestamp="2026-01-05 09:17:00", project=project)
    assert link(program, [row], [EMBEDDER, HANDBOOK], runs)[0]["match_method"] == method


def test_table_without_a_project_or_a_runtime_column_is_linked(monkeypatch):
    log = log_table(*SHOWCASE_ROWS).drop(columns=["project", "runtime"])
    run = run_program(monkeypatch, showcase_tables(log))
    assert run.new_table["match_method"].tolist() == ["template", "questions and state", "retrieval query", "ingestion run", "none", "none"]


def test_setup_of_the_builder_is_only_taken_for_the_model_that_is_its_registration(program):
    """Two models hold one collection. The setup the Prompt Builder saved is taken when the other one is the model the
    step RAG - Register Setup registered for it: the catalog says that the step wrote it, the step names its model
    after the setup, and RAG_RUNS shows the ingestion runs both hold. Another setup that only has the same collection
    name leaves the link open."""
    saved = rag_setup(uuid(30), "Staff handbook", "handbook_v1", target_model="demo_embedder", source="builder")
    rows = [leave_request(timestamp="2026-01-05 11:30:00"), embedding_row(QUESTION, timestamp="2026-01-05 11:29:59"),
            embedding_row("Chapter 7.", mode="document", timestamp="2026-01-05 09:17:00")]

    def linked_with(other, both_hold_the_run=False):
        holders = (uuid(30), uuid(31)) if both_hold_the_run else (uuid(30),)
        runs = [ingestion_run("run-1767604400", "handbook_v1", "2026-01-05 09:15:00", "2026-01-05 09:20:00", model_id=model_id) for model_id in holders]
        prompt, query, document = link(program, rows, [LLM, EMBEDDER, LEAVE_PROMPT, saved, other], runs)
        return prompt["rag_model_id"], query["match_method"], document["match_method"]

    taken, left_open = (uuid(30), "retrieval query", "ingestion run"), ("", "ambiguous", "ingestion run")
    assert linked_with(rag_setup(uuid(31), "RAG Retrieval - Staff handbook", "handbook_v1", source="step")) == taken
    assert linked_with(rag_setup(uuid(31), "rag retrieval - staff  HANDBOOK", "handbook_v1", target_model="demo_embedder", source="step")) == taken
    # Another setup with the same collection name
    assert linked_with(rag_setup(uuid(31), "RAG Retrieval - Something else", "handbook_v1", source="step")) == left_open
    # ... which is the registration all the same when both hold the same ingestion run
    assert linked_with(rag_setup(uuid(31), "RAG Retrieval - Something else", "handbook_v1", source="step"), both_hold_the_run=True) == taken
    # Named after the setup, but embedding with another model
    assert linked_with(rag_setup(uuid(31), "RAG Retrieval - Staff handbook", "handbook_v1", target_model="other_embedder", source="step"))[:2] == left_open[:2]
    assert linked_with(rag_setup(uuid(31), "RAG Retrieval - Staff handbook", "handbook_v1", target_model="other_embedder", source="step"), both_hold_the_run=True)[:2] == left_open[:2]
    # A model of which the catalog does not say that the step wrote it
    assert linked_with(rag_setup(uuid(31), "RAG Retrieval - Staff handbook", "handbook_v1")) == left_open
    # Three models: every one beside the setup of the Builder has to be its registration
    related = rag_setup(uuid(31), "RAG Retrieval - Staff handbook", "handbook_v1", source="step")
    stranger = rag_setup(uuid(33), "RAG Retrieval - Something else", "handbook_v1", source="step")
    prompt = link(program, rows[:1], [LLM, LEAVE_PROMPT, saved, related, stranger])[0]
    assert prompt["rag_model_id"] == ""


def test_two_models_with_one_name_leave_model_id_empty_and_are_named(monkeypatch):
    """Two definition projects hold a model with the name of the container: nothing in the log says which answered."""
    twin = answer_model("demo_llm", "llm", uuid(4))
    twin["projectID"] = uuid(901)
    run = run_program(monkeypatch, showcase_tables(catalog=catalog_table(LLM, twin, DECIDER, EMBEDDER, LEAVE_PROMPT, TICKET_TEMPLATE, HANDBOOK)))
    assert run.new_table["model_id"].fillna("").tolist() == ["", uuid(2), uuid(3), uuid(3), "", uuid(3)]
    # The caller does not depend on it
    assert run.new_table["match_method"].tolist() == ["template", "questions and state", "retrieval query", "ingestion run", "none", "none"]
    assert run.sas.lines("NOTE")[-1] == (
        f"The catalog holds 2 models named demo_llm with the role llm - {uuid(1)} in project {uuid(900)}, {uuid(4)} in project {uuid(901)} - "
        "so the name of the container does not say which of them answered, and model_id is empty in 2 row(s).")
    # The same model twice in the catalog is one model
    run = run_program(monkeypatch, showcase_tables(catalog=catalog_table(LLM, LLM, DECIDER, EMBEDDER, LEAVE_PROMPT, TICKET_TEMPLATE, HANDBOOK)))
    assert run.new_table["model_id"].tolist()[0] == uuid(1) and not [note for note in run.sas.lines("NOTE") if "models named" in note]


# ---------------------------------------------------------------------------
# A run that has nothing to write, and the label of the table
# ---------------------------------------------------------------------------

def test_run_that_links_no_row_leaves_the_table_as_it_is(monkeypatch):
    """Every row has a match_method: nothing is uploaded, no new table is built, and the SAS program neither drops
    nor promotes nor saves. One NOTE says so."""
    linked = run_program(monkeypatch, showcase_tables()).new_table
    run = run_program(monkeypatch, showcase_tables(linked))
    assert run.sas.messages == [("NOTE", "0 of 6 rows of Public.LLM_LOGS were linked, as every row has a match_method. The table was left as it is. "
                                         "With _lnk_redo_all = 1 all rows are linked again.")]
    assert run.new_table is None and run.cas.uploads == []
    assert [action for action, _ in run.cas.actions if action not in ("table.columnInfo", "table.fetch")] == ["dataStep.runCode"]
    assert (run.sas.macro_variables["_lnk_unchanged"], run.sas.macro_variables["_lnk_row_count"]) == ("1", "6")
    pd.testing.assert_frame_equal(FakeCAS.tables[("PUBLIC", "LLM_LOGS")], linked)

    # The SAS program swaps the table only for a run that built one, and has no ERROR line for a run that did not
    macro = output_macro()
    assert re.findall(r"^    %(?:else %)?if (.*) %then %do;$", macro, flags=re.MULTILINE) == [
        "&_lnk_row_count. GT 0 and &_lnk_unchanged. NE 1", "&_lnk_row_count. EQ 0", "&_lnk_row_count. LT 0 and &_lnk_ready. EQ 1 and &_lnk_refused. NE 1",
        LOADED_AND_NOT_LINKED[len("%if "):-len(" %then %do;")]]
    assert "%let _lnk_unchanged = 0;" in code_file_program()

    redo = run_program(monkeypatch, showcase_tables(linked), _lnk_redo_all="1")
    assert redo.new_table is not None and (redo.sas.macro_variables["_lnk_unchanged"], redo.sas.macro_variables["_lnk_row_count"]) == ("0", "6")
    first = run_program(monkeypatch, showcase_tables())
    assert first.sas.macro_variables["_lnk_unchanged"] == "0"


def test_linked_table_keeps_the_label_of_the_table(monkeypatch):
    """The new table is written by a DATA step, which gives it no label: it gets the one the log table had."""
    label = {("Public", "LLM_LOGS"): "Extracted LLM Log Data"}
    run = run_program(monkeypatch, showcase_tables(), table_labels=label)
    assert run.new_table_label == "Extracted LLM Log Data"
    assert [parameters for action, parameters in run.cas.actions if action == "table.alterTable" and "label" in parameters] == [
        {"caslib": "Public", "name": "LLM_LOGS_NEW", "label": "Extracted LLM Log Data"}]

    # A table without a label gets none, and CAS is not asked to set one
    run = run_program(monkeypatch, showcase_tables())
    assert run.new_table_label == "" and not [parameters for action, parameters in run.cas.actions if action == "table.alterTable" and "label" in parameters]

    # A label CAS does not take is no reason to keep the links back
    run = run_program(monkeypatch, showcase_tables(), table_labels=label, label_refused=True)
    assert run.new_table["match_method"].tolist() == ["template", "questions and state", "retrieval query", "ingestion run", "none", "none"]
    assert run.sas.macro_variables["_lnk_row_count"] == "6" and run.sas.lines("ERROR") == []
    assert "The label of the table Public.LLM_LOGS could not be given to the linked table, which has none now." in run.sas.lines("NOTE")


def test_window_of_a_retrieval_reaches_the_matching_from_its_setting(monkeypatch):
    """The showcase query is embedded a minute before its prompt."""
    assert run_program(monkeypatch, showcase_tables(), _lnk_query_window="60").new_table["match_method"].tolist()[2] == "retrieval query"
    assert run_program(monkeypatch, showcase_tables(), _lnk_query_window="59").new_table["match_method"].tolist()[2] == "none"
    assert run_program(monkeypatch, showcase_tables(), _lnk_query_window="-5").new_table["match_method"].tolist()[2] == "none"
