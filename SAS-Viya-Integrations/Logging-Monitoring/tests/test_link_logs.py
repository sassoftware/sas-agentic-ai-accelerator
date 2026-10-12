"""Link-Logs-To-Models.sas: every log row gets the model that answered and, where the
content of the request shows it, the prompt, decision template or RAG setup that made it.

Run with: python -m pytest SAS-Viya-Integrations/Logging-Monitoring/tests
"""
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
    catalog_table,
    code_file_program,
    decision_row,
    embedding_row,
    envelope,
    experiment_run,
    functions,
    ingestion_run,
    llm_row,
    log_table,
    lower_case,
    prompt_template,
    python_block,
    rag_setup,
    run_program,
    runs_table,
    sas_datetime,
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
    settings = dict(re.findall(r"^%let (_lnk_\w+) = (.*);$", program[:program.index("proc cas;")], flags=re.MULTILINE))
    assert settings == {
        "_lnk_log_caslib": "Public", "_lnk_log_table": "LLM_LOGS",
        "_lnk_catalog_caslib": "Public", "_lnk_catalog_table": "MODEL_CATALOG",
        "_lnk_runs_caslib": "Public", "_lnk_runs_table": "RAG_RUNS",
        "_lnk_redo_all": "0", "_lnk_min_literal": "20", "_lnk_utc_offset": "0",
        "_lnk_promote_table": "1", "_lnk_save_table": "1",
    }


def test_every_macro_variable_of_the_program_is_removed_at_its_end():
    program = code_file_program()
    assigned = set(re.findall(r"%let (_lnk_\w+)", program)) | {"_casSessionUUID"}
    removed = set(" ".join(re.findall(r"^%symdel (.*);$", program, flags=re.MULTILINE)).split())
    assert assigned == removed


def test_missing_table_is_one_error_line_that_names_the_program_that_writes_it():
    errors = re.findall(r"^\s*%put (ERROR: .*);$", code_file_program(), flags=re.MULTILINE)
    log_missing, catalog_missing, not_linked = errors
    assert "Log-Parser-Code.sas" in log_missing and "LLM - Log Parser" in log_missing
    assert "Get-All-Prompts.sas" in catalog_missing and "&_lnk_catalog_caslib..&_lnk_catalog_table." in catalog_missing
    assert "left as it is" in catalog_missing and "left as it is" in not_linked


def test_python_is_skipped_when_a_table_is_missing(monkeypatch):
    """The SAS program sets _lnk_ready to 0 with its ERROR line. The Python then connects to nothing."""
    run = run_program(monkeypatch)
    assert run.cas is None
    assert run.sas.messages == []
    assert run.sas.macro_variables["_lnk_row_count"] == "-1"


def test_swap_of_the_table_is_the_one_of_the_parser():
    """The new table takes the place of the log table the way the parser replaces a table: dropped
    twice, renamed, promoted and saved under the same name."""
    program = code_file_program()
    swap = program[program.index("%macro _lnk_handle_output_table;"):program.index("%mend _lnk_handle_output_table;")]
    actions = re.findall(r"^\s*table\.(\w+)", swap, flags=re.MULTILINE)
    assert actions == ["tableExists", "dropTable", "dropTable", "alterTable", "promote", "save", "dropTable", "dropTable"]
    assert 'rename = "&_lnk_log_table."' in swap
    assert swap.index("%if &_lnk_promote_table. %then %do;") < swap.index("table.promote")
    assert swap.index("%if &_lnk_save_table. %then %do;") < swap.index("table.save")


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
    """The question is embedded, and hours later a prompt carries the retrieval of the same question: there is no time window."""
    rows = [embedding_row(QUESTION, timestamp="2026-01-05 06:00:00"), leave_request(timestamp="2026-01-05 11:30:00")]
    row = link(program, rows, [LLM, EMBEDDER, HANDBOOK])[0]
    assert row == {"model_id": uuid(3), "caller_model_id": uuid(30), "caller_project_id": uuid(900), "caller_kind": "rag", "caller_name": "Staff handbook",
                   "experiment_run_id": "", "rag_model_id": "", "match_method": "retrieval query", "match_candidates": 1}


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
        "2 llm rows: 2 linked to a model; caller by experiment run 0, template 1, ambiguous 0, none 1; 1 linked to a RAG setup.",
        "1 decision rows: 1 linked to a model; caller by experiment run 0, questions and state 1, questions 0, ambiguous 0, none 0.",
        "3 embedding rows: 3 linked to a model; caller by retrieval query 1, ingestion run 1, ambiguous 0, none 1.",
    ]
    assert run.sas.lines("ERROR") == [] and run.sas.lines("WARNING") == []


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
