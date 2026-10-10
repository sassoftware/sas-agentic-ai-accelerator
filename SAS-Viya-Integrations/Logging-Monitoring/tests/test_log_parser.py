"""The log parser: the code file and the custom step stay the same program, and the
parser turns the request blocks of every model kind into the twelve LLM_LOGS columns.

Run with: python -m pytest SAS-Viya-Integrations/Logging-Monitoring/tests
"""
import json
import math
from datetime import datetime

import pandas as pd
import pytest

from log_parser_harness import (
    DECISION_ANSWERS,
    DECISION_QUESTIONS,
    DECISION_STATE,
    EMBEDDED_DOCUMENT,
    as_log,
    code_file_program,
    decision_block,
    embedding_block,
    get_block,
    llm_block,
    log_line,
    parse,
    python_block,
    request_block,
    run_parser,
    startup_lines,
    step_definition,
    step_program,
)

# The columns of LLM_LOGS. Tables that sites append to and the LLM Usage Report hold
# exactly these names in this order, which is why they are spelled out here again.
LLM_LOGS_COLUMNS = [
    "timestamp", "model", "system_prompt", "user_prompt", "temperature", "top_p", "top_k", "max_tokens",
    "prompt_length", "output_length", "runtime", "response",
]
TEXT_COLUMNS = ["model", "system_prompt", "user_prompt", "response"]


def missing(value):
    return value is None or (isinstance(value, float) and math.isnan(value))


# ---------------------------------------------------------------------------
# One program in two places
# ---------------------------------------------------------------------------

def test_code_file_and_step_carry_the_same_python():
    assert python_block(code_file_program()) == python_block(step_program())


def test_step_program_is_the_code_file_without_its_configuration():
    """The code file sets the options a user of the step picks in its UI, and removes
    them again at the end. Everything in between is the program of the step."""
    code_file = code_file_program()
    step = step_program()
    assert code_file.count(step) == 1
    before, after = code_file.split(step)
    assert before.rstrip().endswith("%let _log_save_table = 1;")
    assert after.strip().startswith("%symdel _log_path ") and after.strip().count("\n") == 0


def test_step_announces_the_columns_the_parser_writes(monkeypatch, tmp_path):
    run = run_parser(monkeypatch, _write(tmp_path, as_log(llm_block())))
    labels = run.namespace["labels"]

    step = step_definition()
    column_controls = step["flowMetadata"]["outputPorts"][0]["columnDelta"]["updateColumnArguments"]
    values = json.loads(step["ui"])["values"]

    assert [values[control]["value"] for control in column_controls] == LLM_LOGS_COLUMNS
    assert {values[control]["value"]: values[control]["label"] for control in column_controls} == labels


# ---------------------------------------------------------------------------
# The table the parser describes
# ---------------------------------------------------------------------------

def _write(tmp_path, text, name="llms.log"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8", newline="")
    return path


def test_columns_are_the_twelve_of_llm_logs(monkeypatch, tmp_path):
    run = run_parser(monkeypatch, _write(tmp_path, as_log(llm_block(), decision_block(), embedding_block())))

    assert run.namespace["COLUMNS"] == LLM_LOGS_COLUMNS
    assert list(run.namespace["df"].columns) == LLM_LOGS_COLUMNS
    assert sorted(run.namespace["labels"]) == sorted(LLM_LOGS_COLUMNS)


@pytest.mark.parametrize("text", [
    "",
    as_log(startup_lines("2026-01-05 08:00:00", "demo_llm")),
    as_log(startup_lines("2026-01-05 08:00:00", "demo_llm"), get_block("2026-01-05 08:30:00"), get_block("2026-01-05 08:30:01", "None")),
], ids=["zero bytes", "start-up lines only", "requests that score nothing"])
def test_log_without_requests_gives_an_empty_table_and_uploads_nothing(monkeypatch, tmp_path, text):
    run = run_parser(monkeypatch, _write(tmp_path, text))

    frame = run.namespace["df"]
    assert len(frame) == 0
    assert list(frame.columns) == LLM_LOGS_COLUMNS
    assert run.uploads == []
    assert run.row_count == "0"


def test_rows_are_uploaded_once_under_the_staging_name_with_replace(monkeypatch, tmp_path):
    run = run_parser(monkeypatch, _write(tmp_path, as_log(llm_block(), decision_block(), embedding_block())))

    assert run.row_count == "3"
    assert len(run.uploads) == 1
    upload = run.uploads[0]
    assert upload["casout"]["name"] == "_TEMP_LLM_LOGS"
    assert upload["casout"]["caslib"] == "CASUSER"
    # A CAS session that is used again can still hold the staging table of an earlier run
    assert upload["casout"]["replace"] is True
    assert [var["name"] for var in upload["importoptions"]["vars"]] == LLM_LOGS_COLUMNS
    assert len(upload["frame"]) == 3


def test_every_column_is_uploaded_with_its_type(monkeypatch, tmp_path):
    """A log of embedding calls has no prompt and no response at all. The types are
    stated, so these columns are text columns whatever the log holds."""
    run = run_parser(monkeypatch, _write(tmp_path, as_log(embedding_block())))

    types = {var["name"]: var["type"] for var in run.uploads[0]["importoptions"]["vars"]}
    assert types == {column: ("varchar" if column in TEXT_COLUMNS else "double") for column in LLM_LOGS_COLUMNS}
    formats = {var["name"]: var.get("format") for var in run.uploads[0]["importoptions"]["vars"]}
    assert formats["timestamp"] == "DATETIME20."


def test_row_count_stays_unset_when_the_parser_fails(monkeypatch, tmp_path):
    with pytest.raises(FileNotFoundError) as failure:
        run_parser(monkeypatch, tmp_path / "no-such-file.log")
    assert failure.value.sas.macro_variables["_log_row_count"] == "-1"


def test_timestamp_becomes_a_sas_datetime(monkeypatch, tmp_path):
    run = run_parser(monkeypatch, _write(tmp_path, as_log(llm_block(timestamp="2026-01-05 09:15:00"))))

    seconds_since_1960 = (datetime(2026, 1, 5, 9, 15, 0) - datetime(1960, 1, 1)).total_seconds()
    assert run.namespace["df"].loc[0, "timestamp"] == seconds_since_1960


# ---------------------------------------------------------------------------
# One block per model kind
# ---------------------------------------------------------------------------

def test_llm_block(monkeypatch, tmp_path):
    system_prompt = "You are a concise assistant.\nAnswer in German."
    user_prompt = "Wie heißt die Hauptstadt von Österreich?\nSay \"only\" the city's name: {city}, please."
    frame = parse(monkeypatch, tmp_path, as_log(llm_block(
        system_prompt=system_prompt, user_prompt=user_prompt,
        response="Die Hauptstadt von Österreich\nist Wien.\n\nGern geschehen.")))

    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["timestamp"] == "2026-01-05 09:15:00"
    assert row["model"] == "demo_llm"
    assert row["system_prompt"] == system_prompt
    assert row["user_prompt"] == user_prompt
    assert (row["temperature"], row["top_p"], row["max_tokens"]) == ("0.2", "1", "40")
    assert missing(row["top_k"])
    assert (row["prompt_length"], row["output_length"], row["runtime"]) == (21, 9, 1.25)
    # The lines of a response are joined with a blank
    assert row["response"] == "Die Hauptstadt von Österreich ist Wien.  Gern geschehen."


def test_decision_block_keeps_questions_state_and_answers(monkeypatch, tmp_path):
    frame = parse(monkeypatch, tmp_path, as_log(decision_block(options="{temperature:0}")))

    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["model"] == "demo_decider"
    assert row["system_prompt"] == DECISION_QUESTIONS
    assert row["user_prompt"] == DECISION_STATE
    assert row["response"] == DECISION_ANSWERS
    # The answers are still the JSON the model returned, each answer with its confidence
    answers = json.loads(row["response"])
    assert answers["department"]["choice"] == "billing"
    assert answers["department"]["confidence"] == 0.9
    assert (row["prompt_length"], row["output_length"], row["runtime"]) == (120, 14, 0.4)
    assert row["temperature"] == "0"


def test_embedding_block_keeps_tokens_and_runtime_but_not_the_document(monkeypatch, tmp_path):
    frame = parse(monkeypatch, tmp_path, as_log(embedding_block()))

    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["model"] == "demo_embedder"
    assert row["prompt_length"] == 17
    assert row["runtime"] == 0.08
    for column in ("system_prompt", "user_prompt", "response", "output_length"):
        assert missing(row[column]), column
    # Neither the text nor a part of it reaches any cell
    assert not any(isinstance(cell, str) and "handbook" in cell for cell in row)
    assert EMBEDDED_DOCUMENT not in frame.to_csv()


# ---------------------------------------------------------------------------
# Redacted logs
# ---------------------------------------------------------------------------

def test_redacted_block_still_parses(monkeypatch, tmp_path):
    """The collection script replaces the provider key in the options and the values of
    the Cookie, Authorization and X-CSRF-TOKEN headers before it writes the file."""
    redacted_headers = ("Cookie: REDACTED", "Authorization: REDACTED", "X-Csrf-Token: REDACTED")
    frame = parse(monkeypatch, tmp_path, as_log(
        llm_block(options="{temperature:0.7,max_tokens:60,API_KEY:REDACTED}", headers=redacted_headers),
        decision_block(options="{API_KEY:REDACTED}", headers=redacted_headers),
        embedding_block(options="{Embedding_Mode:query,API_KEY:REDACTED}", headers=redacted_headers),
    ))

    assert list(frame["model"]) == ["demo_llm", "demo_decider", "demo_embedder"]
    llm, decision, embedding = (frame.iloc[i] for i in range(3))
    assert llm["system_prompt"] == "You are a concise assistant."
    assert llm["user_prompt"] == "What is the capital of Austria?"
    assert (llm["temperature"], llm["max_tokens"]) == ("0.7", "60")
    assert decision["system_prompt"] == DECISION_QUESTIONS
    assert decision["user_prompt"] == DECISION_STATE
    assert embedding["prompt_length"] == 17


def test_key_of_an_unredacted_log_reaches_no_column(monkeypatch, tmp_path):
    made_up_key = "sk-made-up-0123456789"
    frame = parse(monkeypatch, tmp_path, as_log(
        llm_block(options=f"{{temperature:0.7,API_KEY:{made_up_key}}}", headers=("Cookie: session=made-up-cookie",)),
        embedding_block(options=f"{{API_KEY:{made_up_key}}}"),
    ))

    assert frame.loc[0, "temperature"] == "0.7"
    table = frame.to_csv()
    assert made_up_key not in table
    assert "made-up-cookie" not in table


# ---------------------------------------------------------------------------
# Blocks of several kinds and containers in one file
# ---------------------------------------------------------------------------

def test_interleaved_blocks_of_all_kinds_keep_their_own_values(monkeypatch, tmp_path):
    """The collected file holds one container after the other, each with its start-up
    lines, its scoring requests and the requests that score nothing in between."""
    frame = parse(monkeypatch, tmp_path, as_log(
        startup_lines("2026-01-05 08:00:00", "demo_llm"),
        llm_block(timestamp="2026-01-05 09:00:00", prompt_length=10, output_length=1, run_time=1.5, response="first"),
        get_block("2026-01-05 09:00:30"),
        llm_block(timestamp="2026-01-05 09:01:00", model="demo_llm", options="{}", prompt_length=20, output_length=2, run_time=2.5, response="second"),
        startup_lines("2026-01-05 08:00:05", "demo_decider"),
        decision_block(timestamp="2026-01-05 09:02:00"),
        get_block("2026-01-05 09:02:30", "None"),
        startup_lines("2026-01-05 08:00:10", "demo_embedder"),
        embedding_block(timestamp="2026-01-05 09:03:00", tokens=5, run_time=0.01),
        embedding_block(timestamp="2026-01-05 09:03:01", tokens=6, run_time=0.02),
        llm_block(timestamp="2026-01-05 09:04:00", model="other_llm", prompt_length=30, output_length=3, run_time=3.5, response="third"),
    ))

    assert list(frame["timestamp"]) == [
        "2026-01-05 09:00:00", "2026-01-05 09:01:00", "2026-01-05 09:02:00",
        "2026-01-05 09:03:00", "2026-01-05 09:03:01", "2026-01-05 09:04:00",
    ]
    assert list(frame["model"]) == ["demo_llm", "demo_llm", "demo_decider", "demo_embedder", "demo_embedder", "other_llm"]
    assert list(frame["prompt_length"]) == [10, 20, 120, 5, 6, 30]
    assert list(frame["runtime"]) == [1.5, 2.5, 0.4, 0.01, 0.02, 3.5]
    assert [None if missing(value) else value for value in frame["output_length"]] == [1, 2, 14, None, None, 3]
    assert [None if missing(value) else value for value in frame["response"]] == ["first", "second", DECISION_ANSWERS, None, None, "third"]
    # The second LLM call set no option, so it must not show the options of the first
    assert frame.loc[0, "temperature"] == "0.2"
    assert missing(frame.loc[1, "temperature"])
    # The embedding calls follow a decision call and take none of its text
    assert missing(frame.loc[3, "system_prompt"]) and missing(frame.loc[3, "user_prompt"])


def test_score_lines_between_two_blocks_belong_to_no_request(monkeypatch, tmp_path):
    """A container's part of the file can begin in the middle of a request, because the
    collection cuts each log at a point in time. Its leftover score lines follow the
    finished request of the container before."""
    leftover = [
        log_line("2026-01-05 09:05:00", "scoreModel", "tokens: 999"),
        log_line("2026-01-05 09:05:00", "scoreModel", "run_time: 99.5"),
        log_line("2026-01-05 09:05:00", "scoreModel", "response: an answer to a request that is not in this file"),
        log_line("2026-01-05 09:05:00", "api", "Response: Status Code 200"),
    ]
    frame = parse(monkeypatch, tmp_path, as_log(leftover, llm_block(), leftover, embedding_block(), leftover))

    assert len(frame) == 2
    assert (frame.loc[0, "prompt_length"], frame.loc[0, "runtime"], frame.loc[0, "response"]) == (21, 1.25, "The capital of Austria is Vienna.")
    assert (frame.loc[1, "prompt_length"], frame.loc[1, "runtime"]) == (17, 0.08)
    assert missing(frame.loc[1, "response"])


def test_log_lines_quoted_in_a_prompt_or_a_response_are_text(monkeypatch, tmp_path):
    """Only a line that starts as a log line counts. The same words inside a prompt (one
    long 'Request Data:' line) or at the start of a response line are part of the text."""
    user_prompt = ("Explain these lines: 2026-01-05 09:00:00 - api - INFO - Request: POST /other_model"
                   " and 2026-01-05 09:00:00 - scoreModel - INFO - run_time: 77 and prompt_length: 88")
    frame = parse(monkeypatch, tmp_path, as_log(llm_block(
        user_prompt=user_prompt,
        response="Request: POST /other_model starts a request.\nrun_time: 77 is the time it took.\nResponse: Status Code 200 ends it.")))

    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["model"] == "demo_llm"
    assert row["user_prompt"] == user_prompt
    assert (row["prompt_length"], row["runtime"]) == (21, 1.25)
    assert row["response"] == ("Request: POST /other_model starts a request. run_time: 77 is the time it took."
                               " Response: Status Code 200 ends it.")


def test_requests_that_overlap_in_one_container_stay_one_row_each(monkeypatch, tmp_path):
    """The score lines carry no request id, so when a second request starts before the
    first one was answered they can not be told apart. Each request still gets its row
    with its own inputs, and no row receives the answers of both."""
    first = llm_block(timestamp="2026-01-05 09:00:00", user_prompt="question A", response="answer A")
    second = llm_block(timestamp="2026-01-05 09:00:01", user_prompt="question B", response="answer B")
    # The first nine lines of a block are logged before the model is scored
    frame = parse(monkeypatch, tmp_path, as_log(first[:9], second[:9], first[9:], second[9:]))

    assert list(frame["timestamp"]) == ["2026-01-05 09:00:00", "2026-01-05 09:00:01"]
    assert list(frame["user_prompt"]) == ["question A", "question B"]
    responses = [value for value in frame["response"] if not missing(value)]
    assert all(not ("answer A" in value and "answer B" in value) for value in responses)


# ---------------------------------------------------------------------------
# Lines that are not what the parser expects
# ---------------------------------------------------------------------------

def test_request_without_a_response_line_ends_at_the_next_request(monkeypatch, tmp_path):
    failed = llm_block(timestamp="2026-01-05 09:00:00", user_prompt="a call that failed")[:9] + [
        log_line("2026-01-05 09:00:02", "scoreModel", "HTTP 429: {\"error\": \"rate limit\"}", level="ERROR"),
    ]
    frame = parse(monkeypatch, tmp_path, as_log(failed, llm_block(timestamp="2026-01-05 09:01:00")))

    assert len(frame) == 2
    assert frame.loc[0, "user_prompt"] == "a call that failed"
    assert all(missing(frame.loc[0, column]) for column in ("prompt_length", "output_length", "runtime", "response"))
    assert (frame.loc[1, "prompt_length"], frame.loc[1, "response"]) == (21, "The capital of Austria is Vienna.")


def test_unreadable_request_data_keeps_the_row_and_its_numbers(monkeypatch, tmp_path):
    block = llm_block()
    data_line = next(index for index, line in enumerate(block) if " - Request Data: " in line)
    block[data_line] = block[data_line][:-25]   # the line was cut off
    frame = parse(monkeypatch, tmp_path, as_log(block))

    assert len(frame) == 1
    assert missing(frame.loc[0, "system_prompt"]) and missing(frame.loc[0, "user_prompt"])
    assert (frame.loc[0, "prompt_length"], frame.loc[0, "output_length"], frame.loc[0, "runtime"]) == (21, 9, 1.25)


def test_score_value_that_is_no_number_is_missing(monkeypatch, tmp_path):
    frame = parse(monkeypatch, tmp_path, as_log(
        llm_block(prompt_length="None", output_length="None", run_time="2e-05")))

    assert missing(frame.loc[0, "prompt_length"]) and missing(frame.loc[0, "output_length"])
    assert frame.loc[0, "runtime"] == 2e-05


def test_inputs_that_are_no_text_do_not_stop_the_parser(monkeypatch, tmp_path):
    frame = parse(monkeypatch, tmp_path, as_log(
        request_block("2026-01-05 09:00:00", "demo_llm",
                      [("systemPrompt", None), ("userPrompt", 42), ("options", None)],
                      [("prompt_length", 3), ("output_length", 1), ("run_time", 0.5), ("response", "ok")])))

    assert missing(frame.loc[0, "system_prompt"])
    assert frame.loc[0, "user_prompt"] == "42"
    assert frame.loc[0, "response"] == "ok"


def test_text_columns_stay_text_after_the_parser_prepared_the_upload(monkeypatch, tmp_path):
    run = run_parser(monkeypatch, _write(tmp_path, as_log(llm_block(), decision_block(), embedding_block())))

    frame = run.uploads[0]["frame"]
    for column in LLM_LOGS_COLUMNS:
        if column in TEXT_COLUMNS:
            assert all(missing(value) or isinstance(value, str) for value in frame[column]), column
        else:
            assert pd.api.types.is_numeric_dtype(frame[column]), column
