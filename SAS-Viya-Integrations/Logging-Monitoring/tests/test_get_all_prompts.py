"""Tests of Get-All-Prompts.sas and of the custom step "LLM - Get All Prompts".

The Python of the program runs here as it stands in the SAS program, against a
made-up SAS Model Manager (see get_all_prompts_harness.py). What the SAS code
around it does - the DATA steps, PROC CASUTIL, the stacking of the run tables -
does not run here; of that code the tests check what can be read from its text.
"""
import hashlib
import json
import re

import pytest

from get_all_prompts_harness import (
    ANSWERS, DEFAULT_SETTINGS, INTEGRATIONS, QUESTIONS, FakeViya, code_file_program, core,
    decision_header, decision_score_code, functions, header_row, model_row, optimization_entry, prompt_score_code,
    python_block, rag_setup, run_pull, step_definition, step_pipeline_yaml, step_program, step_ui, variable)

# The columns of PROMPT_EXPERIMENTS the report "LLM Usage Report" reads, with their type
REPORT_TEXTS = ["createdBy", "description", "function", "input_type", "model", "modelLink", "modifiedBy", "name",
                "normalize", "projectLink", "reasoning_effort", "response", "systemPrompt", "userPrompt"]
REPORT_NUMBERS = ["api_key_required", "best_prompt", "creationTime", "dimensions", "fastest_prompt",
                  "fewest_tokens_prompt", "frequency_penalty", "integrated_llm_call", "max_completion_tokens",
                  "max_tokens", "modifiedTime", "output_length", "output_variables_count", "presence_penalty",
                  "prompt_length", "runID", "run_time", "seed", "temperature", "thinking_budget", "top_k", "top_p",
                  "unique_id", "variables_count"]
# The columns the table had before this release, in their order
COLUMNS_BEFORE = [
    "unique_id", "name", "description", "createdBy", "modifiedBy", "function", "runID", "model", "systemPrompt",
    "userPrompt", "response", "run_time", "prompt_length", "output_length", "cost", "cheapest_prompt", "best_prompt",
    "fastest_prompt", "fewest_tokens_prompt", "judge_rank", "judge_best", "judge_cost", "judge_model",
    "judge_confidence", "judge_mode", "judge_panel", "judge_agreement", "judge_chairman_model", "variables_count",
    "integrated_llm_call", "output_variables_count", "creationTime", "modifiedTime", "temperature", "top_p", "top_k",
    "max_tokens", "thinking_budget", "max_completion_tokens", "seed", "frequency_penalty", "presence_penalty",
    "dimensions", "api_key_required", "reasoning_effort", "input_type", "normalize", "projectLink", "modelLink"]
# What the link program reads of MODEL_CATALOG, in the order of the contract
CATALOG_CONTRACT = ["modelID", "projectID", "projectName", "name", "role", "runID", "target_model",
                    "system_template", "user_template", "resolved_system_prompt", "resolved_user_prompt", "collection"]


def contract_hash(text):
    return hashlib.sha256(" ".join(text.split()).encode("utf-8", errors="replace")).hexdigest()


def prompt_viya():
    """One prompt project with a prompt template that has two runs."""
    viya = FakeViya()
    project = viya.add_project("Service Desk", ["LLM", "Prompt-Engineering"])
    tracker = [
        header_row(1, "You answer questions about {{topic}}.", "Question: {{question}}",
                   [variable("topic", "travel expenses"), variable("question", "Who signs a claim?")]),
        model_row(1, "demo_llm", "The line manager.", best_prompt=1),
        model_row(1, "other_llm", "Your manager signs it.", options="{temperature:1,API_KEY:Provider}",
                  fastest_prompt=False, cost=0.002),
        header_row(2, "You answer questions about {{topic}} briefly.", "Question: {{question}}",
                   [variable("topic", "travel expenses"), variable("question", "Is a taxi refunded?")]),
        model_row(2, "demo_llm", "Yes, with a receipt."),
    ]
    model = viya.add_model(project, "Desk Answer", {"Prompt-Experiment-Tracker.json": tracker})
    return viya, project, model


# ---------------------------------------------------------------------------
# The variables of a run
# ---------------------------------------------------------------------------

def test_variables_are_filled_in_as_the_prompt_builder_does_it(monkeypatch, tmp_path):
    substitute = functions(monkeypatch, tmp_path)["substitute_variables"]
    variables = [variable("claim", "MC-7"), variable("amount", "1200.50", "decimal")]

    assert substitute("Claim {{claim}} over {{amount}} EUR, again {{claim}}.", variables) == \
        "Claim MC-7 over 1200.50 EUR, again MC-7."
    # blanks and line ends inside the braces, a placeholder of no variable, a name that only starts like one
    assert substitute("{{ claim }}|{{\nclaim\t}}|{{other}}|{{claims}}|{claim}", variables) == \
        "MC-7|MC-7|{{other}}|{{claims}}|{claim}"
    # the name counts as written
    assert substitute("{{Claim}}", variables) == "{{Claim}}"


def test_a_value_goes_in_as_it_is(monkeypatch, tmp_path):
    substitute = functions(monkeypatch, tmp_path)["substitute_variables"]

    # not trimmed, and nothing in it is read as part of a replacement pattern
    assert substitute("[{{note}}]", [variable("note", "  two words \n")]) == "[  two words \n]"
    assert substitute("{{path}}", [variable("path", r"C:\temp\1 costs $1 and \g<0>")]) == r"C:\temp\1 costs $1 and \g<0>"
    # a tracker that was not written by the Prompt Builder may hold a number
    assert substitute("{{n}} and {{x}}", [variable("n", 3), variable("x", 2.5)]) == "3 and 2.5"


def test_the_variables_are_taken_one_after_the_other(monkeypatch, tmp_path):
    substitute = functions(monkeypatch, tmp_path)["substitute_variables"]

    # the value of the first variable holds the placeholder of the second, which is filled in afterwards
    assert substitute("{{a}}", [variable("a", "see {{b}}"), variable("b", "B")]) == "see B"
    # the other way round the placeholder stays: the first variable was already done
    assert substitute("{{b}}", [variable("a", "A"), variable("b", "see {{a}}")]) == "see {{a}}"


def test_blanks_inside_the_braces_are_the_ones_javascript_counts(monkeypatch, tmp_path):
    substitute = functions(monkeypatch, tmp_path)["substitute_variables"]
    variables = [variable("claim", "MC-7")]

    assert substitute("{{\u00a0claim\ufeff}}", variables) == "MC-7"
    # a character Python counts as a blank and JavaScript does not
    assert substitute("{{\x1cclaim}}", variables) == "{{\x1cclaim}}"


def test_a_run_without_variables_sends_its_templates(monkeypatch, tmp_path):
    namespace = functions(monkeypatch, tmp_path)

    assert namespace["substitute_variables"]("Keep {{this}}.", None) == "Keep {{this}}."
    assert namespace["resolved_prompts"](header_row(1, "S {{a}}", "U {{a}}", None)) == ("S {{a}}", "U {{a}}")


def test_a_decision_run_fills_the_variables_into_the_state_only(monkeypatch, tmp_path):
    resolved = functions(monkeypatch, tmp_path)["resolved_prompts"]
    questions = json.dumps({"q": {"type": "noul", "instructions": "Is {{request}} mentioned?"}})

    assert resolved(decision_header(1, "Request: {{request}}", questions, [variable("request", "a refund")])) == \
        (questions, "Request: a refund")


def test_the_text_hash_is_the_one_of_the_contract(monkeypatch, tmp_path):
    text_hash = functions(monkeypatch, tmp_path)["text_hash"]

    assert text_hash("Wer  zahlt\n die Reise?") == contract_hash("Wer zahlt die Reise?")
    assert text_hash(" one\ttwo ") == hashlib.sha256(b"one two").hexdigest()
    assert len(text_hash("")) == 64 and text_hash("A") != text_hash("a")


# ---------------------------------------------------------------------------
# PROMPT_EXPERIMENTS
# ---------------------------------------------------------------------------

def test_one_row_per_run_and_model_with_the_ids_of_model_and_project(monkeypatch, tmp_path):
    viya, project, model = prompt_viya()
    rows = run_pull(monkeypatch, tmp_path, viya).rows("experiments")

    assert [(row["runID"], row["model"]) for row in rows] == [(1, "demo_llm"), (1, "other_llm"), (2, "demo_llm")]
    assert [row["unique_id"] for row in rows] == [1, 2, 3]
    first = rows[0]
    assert (first["modelID"], first["projectID"], first["projectName"]) == (model["id"], project["id"], "Service Desk")
    assert (first["name"], first["description"], first["createdBy"], first["modifiedBy"], first["function"]) == \
        ("Desk Answer", "About Desk Answer", "ada", "grace", "Prompt")
    assert first["modelLink"] == f"https://viya.example.com/SASModelManager/models/{model['id']}"
    assert first["projectLink"] == f"https://viya.example.com/SASModelManager/projects/{project['id']}"
    assert first["kind"] == "prompt" and first["expected"] == ""
    # the templates of the header row, and what was sent
    assert first["systemPrompt"] == "You answer questions about {{topic}}."
    assert first["userPrompt"] == "Question: {{question}}"
    assert first["resolved_system_prompt"] == "You answer questions about travel expenses."
    assert first["resolved_user_prompt"] == "Question: Who signs a claim?"
    assert first["resolved_user_hash"] == contract_hash("Question: Who signs a claim?")
    assert json.loads(first["variables"])[1] == variable("question", "Who signs a claim?")
    assert rows[2]["resolved_user_prompt"] == "Question: Is a taxi refunded?"
    # what the model row holds
    assert (first["response"], first["run_time"], first["prompt_length"], first["output_length"]) == \
        ("The line manager.", 1.25, 21, 9)
    assert (first["best_prompt"], first["fastest_prompt"], first["fewest_tokens_prompt"], first["cheapest_prompt"]) == \
        (1, 1, 0, 0)
    assert (rows[1]["fastest_prompt"], rows[1]["cost"], rows[1]["best_prompt"]) == (0, 0.002, None)
    assert (first["variables_count"], first["integrated_llm_call"], first["output_variables_count"]) == (2, 0, 0)


def test_the_timestamps_of_the_model_are_sas_datetimes(monkeypatch, tmp_path):
    viya, _, _ = prompt_viya()
    run = run_pull(monkeypatch, tmp_path, viya)
    row = run.rows("experiments")[0]

    # 2026-01-05T09:15:00.250Z, in seconds since 1960-01-01
    assert row["creationTime"] == pytest.approx((24111 * 86400) + 9 * 3600 + 15 * 60 + 0.25)
    assert row["modifiedTime"] - row["creationTime"] == pytest.approx(86400 + 3600 + 15 * 60 - 0.25)
    sas_datetime = run.namespace["sas_datetime"]
    assert sas_datetime("1960-01-01T00:00:00Z") == 0
    assert sas_datetime("1960-01-01T01:00:00+01:00") == 0
    assert sas_datetime("1960-01-01T00:00:01.500000+00:00") == 1.5
    assert sas_datetime("") is None and sas_datetime(None) is None and sas_datetime("soon") is None


def test_the_options_of_a_run_fill_their_columns(monkeypatch, tmp_path):
    viya = FakeViya()
    project = viya.add_project("Options", ["Prompt-Engineering"])
    viya.add_model(project, "Many Options", {"Prompt-Experiment-Tracker.json": [
        header_row(1, "S", "U", []),
        model_row(1, "llm_a", options="{temperature:0.7,top_p:0.9,top_k:40,max_tokens:256,seed:7,frequency_penalty:0.1,"
                                      "presence_penalty:0.2,thinking_budget:1024,max_completion_tokens:4000,"
                                      "reasoning_effort:medium}"),
        model_row(1, "llm_b", options="{max_length:128,API_KEY:Provider}"),
        model_row(1, "embedder", options="{input_type:query,dimensions:256,normalize:true}"),
        model_row(1, "llm_c", options="{}"),
        model_row(1, "llm_d", options=""),
    ]})
    a, b, embedder, c, d = run_pull(monkeypatch, tmp_path, viya).rows("experiments")

    assert (a["temperature"], a["top_p"], a["top_k"], a["max_tokens"], a["seed"]) == (0.7, 0.9, 40, 256, 7)
    assert (a["frequency_penalty"], a["presence_penalty"], a["thinking_budget"], a["max_completion_tokens"]) == \
        (0.1, 0.2, 1024, 4000)
    assert (a["reasoning_effort"], a["api_key_required"]) == ("medium", 0)
    # max_length is the max_tokens of some models; a key makes the run one that needs a key
    assert (b["max_tokens"], b["api_key_required"], b["temperature"]) == (128, 1, None)
    assert (embedder["input_type"], embedder["dimensions"], embedder["normalize"]) == ("query", 256, "true")
    # a run with empty options had no key, a run without options says nothing about it
    assert c["api_key_required"] == 0 and d["api_key_required"] is None


def test_the_value_of_a_key_is_not_copied_into_the_options(monkeypatch, tmp_path):
    redacted = functions(monkeypatch, tmp_path)["redacted_options"]

    assert redacted("{temperature:0.2,API_KEY:sk-made-up-0123456789}") == "{temperature:0.2,API_KEY:REDACTED}"
    assert redacted("{API_KEY:sk-made-up,max_tokens:40}") == "{API_KEY:REDACTED,max_tokens:40}"
    assert redacted("{access_token:abc,client_secret:def,password:ghi}") == \
        "{access_token:REDACTED,client_secret:REDACTED,password:REDACTED}"
    # the names the log parser goes by: the same option is redacted in the log table and here
    assert redacted("{Authorization:Bearer abc,pwd:x,session_cookie:y,token_id:abc,temperature:1}") == \
        "{Authorization:REDACTED,pwd:REDACTED,session_cookie:REDACTED,token_id:REDACTED,temperature:1}"
    assert redacted('{"api_key":"abc","hf_token":"def"}') == '{"api_key":REDACTED,"hf_token":REDACTED}'
    assert redacted("{token_limit:512,max_new_tokens:64}") == "{token_limit:512,max_new_tokens:64}"
    # options without a key stay as they are stored, max_tokens is no token
    for options in ("{temperature:0.2,top_p:1,max_tokens:40,max_completion_tokens:9}", "{}", ""):
        assert redacted(options) == options


def test_the_options_column_holds_the_options_without_the_key(monkeypatch, tmp_path):
    viya, _, _ = prompt_viya()
    rows = run_pull(monkeypatch, tmp_path, viya).rows("experiments")

    assert rows[0]["options"] == "{temperature:0.2,top_p:1,max_tokens:40}"
    assert rows[1]["options"] == "{temperature:1,API_KEY:REDACTED}"


def test_a_run_without_a_system_prompt_keeps_its_own_prompts(monkeypatch, tmp_path):
    """The program of the releases before carried the header of the run before into such a run."""
    viya = FakeViya()
    project = viya.add_project("Service Desk", ["Prompt-Engineering"])
    viya.add_model(project, "No System Prompt", {"Prompt-Experiment-Tracker.json": [
        header_row(1, "You are terse.", "First: {{q}}", [variable("q", "one")], judge_model="demo_judge",
                   judge_confidence="high", judge_mode="single", judge_cost=0.01,
                   manifest={"integratedLLMCall": True, "selectedOutputs": ["response"],
                             "outputVariables": [{"name": "verdict"}, {"name": "reason"}]}),
        model_row(1, "demo_llm"),
        header_row(2, "", "Second: {{q}}", [variable("q", "two"), variable("unused", "x")]),
        model_row(2, "demo_llm"),
    ]})
    first, second = run_pull(monkeypatch, tmp_path, viya).rows("experiments")

    assert (first["systemPrompt"], first["userPrompt"]) == ("You are terse.", "First: {{q}}")
    assert (first["judge_model"], first["judge_confidence"], first["judge_mode"], first["judge_cost"]) == \
        ("demo_judge", "high", "single", 0.01)
    assert (first["variables_count"], first["integrated_llm_call"], first["output_variables_count"]) == (1, 1, 2)
    assert (second["systemPrompt"], second["userPrompt"]) == ("", "Second: {{q}}")
    assert (second["resolved_system_prompt"], second["resolved_user_prompt"]) == ("", "Second: two")
    assert (second["judge_model"], second["judge_confidence"], second["judge_mode"], second["judge_cost"]) == \
        ("", "", "", None)
    assert (second["variables_count"], second["integrated_llm_call"], second["output_variables_count"]) == (2, 0, 0)


def test_the_judge_of_a_run_is_on_every_row_of_it(monkeypatch, tmp_path):
    viya = FakeViya()
    project = viya.add_project("Service Desk", ["Prompt-Engineering"])
    viya.add_model(project, "Judged", {"Prompt-Experiment-Tracker.json": [
        header_row(1, "S", "U", [], judge_model="", judge_mode="council", judge_panel="judge_a, judge_b",
                   judge_agreement="2/2", judge_chairman_model="judge_c", judge_confidence="medium",
                   judge_reasoning="B is complete. " * 400),
        model_row(1, "llm_a", judge_rank=2, judge_best=0),
        model_row(1, "llm_b", judge_rank=1, judge_best=1),
    ]})
    a, b = run_pull(monkeypatch, tmp_path, viya).rows("experiments")

    assert (a["judge_rank"], a["judge_best"], b["judge_rank"], b["judge_best"]) == (2, 0, 1, 1)
    for row in (a, b):
        assert (row["judge_mode"], row["judge_panel"], row["judge_agreement"], row["judge_chairman_model"]) == \
            ("council", "judge_a, judge_b", "2/2", "judge_c")
        # the reasoning is shortened to the length of its column
        assert row["judge_reasoning"].startswith("B is complete. B is") and len(row["judge_reasoning"]) == 2000


def test_a_decision_template_is_marked_and_keeps_its_expected_answers(monkeypatch, tmp_path):
    viya = FakeViya()
    project = viya.add_project("Claims", ["Decision", "Prompt-Engineering", "Decision-Engineering"])
    model = viya.add_model(project, "Request Routing", {"Prompt-Experiment-Tracker.json": [
        decision_header(1),
        model_row(1, "demo_decider", ANSWERS, options="{}", best_prompt=1, output_length=0, fewest_tokens_prompt=None),
    ]}, algorithm="Decision-Template")
    run = run_pull(monkeypatch, tmp_path, viya)
    (row,) = run.rows("experiments")

    assert (row["kind"], row["model"], row["modelID"]) == ("decision", "demo_decider", model["id"])
    assert json.loads(row["expected"]) == {"team": "repairs", "urgent": "no"}
    # the questions are the system prompt and are sent as they are, the state is the user prompt
    assert row["systemPrompt"] == QUESTIONS and row["resolved_system_prompt"] == QUESTIONS
    assert row["userPrompt"] == "Request: {{request}}"
    assert row["resolved_user_prompt"] == "Request: My windscreen has a crack since the hail on Tuesday."
    assert json.loads(row["response"])["team"]["choice"] == "repairs"
    assert row["fewest_tokens_prompt"] is None and row["best_prompt"] == 1


def test_a_model_without_a_run_has_one_row(monkeypatch, tmp_path):
    viya = FakeViya()
    project = viya.add_project("Claims", ["Prompt-Engineering", "Decision-Engineering"])
    viya.add_model(project, "No File")
    viya.add_model(project, "Empty Tracker", {"Prompt-Experiment-Tracker.json": []}, algorithm="Decision-Template")
    other = viya.add_project("Service Desk", ["Prompt-Engineering"])
    viya.add_model(other, "A Prompt")
    viya.add_model(other, "Tagged", tags=["Decision-Template"])
    rows = run_pull(monkeypatch, tmp_path, viya).rows("experiments")

    assert [(row["name"], row["runID"], row["model"], row["kind"]) for row in rows] == [
        ("No File", None, "", "decision"), ("Empty Tracker", None, "", "decision"),
        ("A Prompt", None, "", "prompt"), ("Tagged", None, "", "decision")]
    assert all(row["systemPrompt"] == "" and row["api_key_required"] is None for row in rows)
    assert all(row["modelID"] and row["projectID"] for row in rows)


def test_only_the_projects_with_the_tag_are_read(monkeypatch, tmp_path):
    viya, _, _ = prompt_viya()
    elsewhere = viya.add_project("Credit Scoring", ["Classification"])
    viya.add_model(elsewhere, "Gradient Boosting")
    tagged = viya.add_project("Own Tag", ["My-Prompts"])
    viya.add_model(tagged, "Mine")

    assert {row["name"] for row in run_pull(monkeypatch, tmp_path, viya).rows("experiments")} == {"Desk Answer"}
    mine = run_pull(monkeypatch, tmp_path, viya, {"_gap_tag": "My-Prompts"})
    assert {row["name"] for row in mine.rows("experiments")} == {"Mine"}
    assert "2 project(s)" not in " ".join(mine.sas.of_type("NOTE"))


# ---------------------------------------------------------------------------
# PROMPT_OPTIMIZATIONS
# ---------------------------------------------------------------------------

def test_one_row_per_optimization(monkeypatch, tmp_path):
    viya, project, model = prompt_viya()
    viya.add_file(model, "Prompt-Optimization-Tracker.json", [
        optimization_entry(1),
        optimization_entry(2, status="failed", finishedAt="2026-02-04T09:00:00.000000+00:00", metricAfter=None,
                           error="The judge model could not be reached.", usage=None, optimizer="gepa"),
        optimization_entry(3, type="comparison", targetModel="demo_llm, other_llm", optimizer=None,
                           skippedReason="baseline-perfect", targets=[{"model": "demo_llm", "status": "scored"}]),
    ])
    first, failed, comparison = run_pull(monkeypatch, tmp_path, viya).rows("optimizations")

    assert (first["modelID"], first["projectID"], first["projectName"], first["name"]) == \
        (model["id"], project["id"], "Service Desk", "Desk Answer")
    assert (first["optimizationId"], first["status"], first["targetModel"], first["optimizer"], first["metric"]) == \
        (1, "succeeded", "demo_llm", "bootstrap", "judge")
    assert (first["judgeModel"], first["datasetSource"], first["datasetRef"]) == \
        ("demo_judge", "tracker", "Prompt-Experiment-Tracker.json")
    assert (first["sampleCount"], first["trainSize"], first["validationSize"]) == (40, 28, 12)
    assert (first["metricBefore"], first["metricAfter"]) == (0.61, 0.78)
    assert first["finishedAt"] - first["startedAt"] == pytest.approx(12 * 60 + 30.5)
    assert (first["targetCalls"], first["targetPromptTokens"], first["targetOutputTokens"], first["targetRunTime"]) == \
        (120, 9000, 2100, 96.5)
    assert (first["judgeCalls"], first["judgePromptTokens"], first["judgeOutputTokens"], first["judgeRunTime"]) == \
        (52, 14000, 800, 40.0)
    assert (first["error"], first["type"], first["jobId"]) == ("", "", "eeeeeeee-0000-4000-8000-000000000001")
    assert first["computeContext"] == "SAS Job Execution compute context"
    assert (failed["status"], failed["metricAfter"], failed["targetCalls"], failed["error"]) == \
        ("failed", None, None, "The judge model could not be reached.")
    assert (comparison["type"], comparison["targetModel"], comparison["optimizer"], comparison["skippedReason"]) == \
        ("comparison", "demo_llm, other_llm", "", "baseline-perfect")


# ---------------------------------------------------------------------------
# The manifested score code
# ---------------------------------------------------------------------------

def test_the_templates_of_a_manifested_prompt_are_read_from_its_score_code(monkeypatch, tmp_path):
    manifested = functions(monkeypatch, tmp_path)["manifested_templates"]

    assert manifested(prompt_score_code()) == {
        "role": "prompt", "target_model": "demo_llm",
        # a placeholder no variable belongs to stays as it was typed, a variable is a placeholder again
        "system_template": "You answer questions about {{topic}} in one sentence.",
        "user_template": "Question: {{question}}\nContext: {{context}}",
    }


def test_quotes_backslashes_and_braces_of_a_prompt_come_back_as_they_were_typed(monkeypatch, tmp_path):
    manifested = functions(monkeypatch, tmp_path)["manifested_templates"]
    # as the Prompt Builder writes them: \\ for a backslash, \" for a quote, {{ }} for braces
    code = prompt_score_code(
        system_literal='f"""Answer as JSON {{\\"answer\\": \\"...\\"}} and keep C:\\\\temp as it is."""',
        user_literal='f"""{str(question).strip()}"""', inputs="question")

    found = manifested(code)
    assert found["system_template"] == 'Answer as JSON {"answer": "..."} and keep C:\\temp as it is.'
    assert found["user_template"] == "{{question}}"


def test_a_prompt_that_was_manifested_without_variables(monkeypatch, tmp_path):
    """A run stored without variables: the system prompt is a plain text, the user prompt names its inputs."""
    manifested = functions(monkeypatch, tmp_path)["manifested_templates"]
    code = prompt_score_code(system_literal='"""Summarise the note. Use {curly} words."""',
                             user_literal='f"note: {str(note).strip()}; tone: {str(tone).strip()}"', inputs="note, tone")

    assert manifested(code) == {"role": "prompt", "target_model": "demo_llm",
                                "system_template": "Summarise the note. Use {curly} words.",
                                "user_template": "note: {{note}}; tone: {{tone}}"}


def test_score_code_of_earlier_releases_escapes_the_finished_text(monkeypatch, tmp_path):
    manifested = functions(monkeypatch, tmp_path)["manifested_templates"]
    code = (
        "def scoreModel(userPrompt):\n"
        '    "Output: llmBody, llmURL"\n'
        '    llm = "old_llm"\n'
        '    endpoint = "https://viya.example.com/llm"\n'
        '    systemPrompt = """Be kind.""".replace(\'\\n\', "\\\\n").replace("\'", \'"\')\n'
        '    userPrompt = f"userPrompt: {str(userPrompt).strip()}".replace(\'\\n\', "\\\\n")\n'
        "    return systemPrompt, userPrompt\n")

    assert manifested(code) == {"role": "prompt", "target_model": "old_llm", "system_template": "Be kind.",
                                "user_template": "userPrompt: {{userPrompt}}"}


def test_the_templates_of_a_manifested_decision_template(monkeypatch, tmp_path):
    manifested = functions(monkeypatch, tmp_path)["manifested_templates"]

    assert manifested(decision_score_code()) == {
        "role": "decision", "target_model": "demo_decider", "system_template": QUESTIONS,
        "user_template": "Request: {{request}}"}


def test_score_code_that_does_not_compile_is_read_statement_by_statement(monkeypatch, tmp_path):
    """A release of the Prompt Builder wrote such code for some decision templates."""
    manifested = functions(monkeypatch, tmp_path)["manifested_templates"]
    state = 'f"""Request:\n{str(request).strip()}\n\nPlease decide."""'
    broken = decision_score_code(state_literal=state) + "    try:\n        answers = 1\n  team = 2\n"
    with pytest.raises(SyntaxError):
        compile(broken, "broken", "exec")

    assert manifested(broken) == {
        "role": "decision", "target_model": "demo_decider", "system_template": QUESTIONS,
        "user_template": "Request:\n{{request}}\n\nPlease decide."}


def test_other_python_is_no_manifested_template(monkeypatch, tmp_path):
    manifested = functions(monkeypatch, tmp_path)["manifested_templates"]

    assert manifested("def scoreModel(question):\n    return question\n") is None
    assert manifested("import os\n\ndef retrieve(question):\n    systemPrompt = 'x'\n    userPrompt = 'y'\n") is None
    assert manifested("def scoreModel(a):\n    systemPrompt = build(a)\n    userPrompt = f'{a.upper()}'\n") is None
    assert manifested("") is None and manifested(None) is None and manifested("this is ( not python") is None
    # the code is parsed and never run
    assert manifested("raise SystemExit(1)\n" + prompt_score_code())["target_model"] == "demo_llm"


# ---------------------------------------------------------------------------
# MODEL_CATALOG
# ---------------------------------------------------------------------------

def test_the_catalog_has_the_columns_of_the_contract(monkeypatch, tmp_path):
    namespace = functions(monkeypatch, tmp_path)
    names = [name for name, _, _ in namespace["CATALOG_COLUMNS"]]

    assert names[:len(CATALOG_CONTRACT)] == CATALOG_CONTRACT
    lengths = {name: (kind, length) for name, kind, length in namespace["CATALOG_COLUMNS"]}
    assert lengths["modelID"] == ("c", 36) and lengths["projectID"] == ("c", 36)
    assert all(lengths[name][0] in "ct" for name in CATALOG_CONTRACT)


def test_every_run_is_in_the_catalog_with_what_it_sent(monkeypatch, tmp_path):
    viya, project, model = prompt_viya()
    run = run_pull(monkeypatch, tmp_path, viya)
    runs = [row for row in run.catalog(role="prompt") if row["runID"]]

    assert [(row["runID"], row["target_model"]) for row in runs] == [("1", "demo_llm"), ("1", "other_llm"), ("2", "demo_llm")]
    first = runs[0]
    assert (first["modelID"], first["projectID"], first["projectName"], first["name"]) == \
        (model["id"], project["id"], "Service Desk", "Desk Answer")
    assert (first["system_template"], first["user_template"]) == \
        ("You answer questions about {{topic}}.", "Question: {{question}}")
    assert (first["resolved_system_prompt"], first["resolved_user_prompt"]) == \
        ("You answer questions about travel expenses.", "Question: Who signs a claim?")
    assert first["resolved_system_hash"] == contract_hash("You answer questions about travel expenses.")
    assert first["resolved_user_hash"] == contract_hash("Question: Who signs a claim?")
    assert first["collection"] == "" and first["template_source"] == ""


def test_the_template_row_is_the_newest_run_marked_as_best(monkeypatch, tmp_path):
    viya, _, model = prompt_viya()
    (template,) = run_pull(monkeypatch, tmp_path, viya).catalog(modelID=model["id"], runID="")

    # run 2 is newer, run 1 is the one with a best prompt
    assert (template["role"], template["target_model"], template["template_source"]) == ("prompt", "demo_llm", "best run")
    assert template["system_template"] == "You answer questions about {{topic}}."
    assert template["user_template"] == "Question: {{question}}"
    # the row of the template itself names no run and carries nothing that was sent
    assert (template["resolved_system_prompt"], template["resolved_user_prompt"], template["resolved_user_hash"]) == \
        ("", "", "")


def test_without_a_best_prompt_the_template_row_is_the_newest_run(monkeypatch, tmp_path):
    viya = FakeViya()
    project = viya.add_project("Service Desk", ["Prompt-Engineering"])
    model = viya.add_model(project, "Unmarked", {"Prompt-Experiment-Tracker.json": [
        header_row(1, "Old.", "{{q}}", [variable("q", "one")]), model_row(1, "llm_a"),
        header_row(12, "Newest.", "{{q}}", [variable("q", "two")]), model_row(12, "llm_a"), model_row(12, "llm_b"),
        header_row(3, "Middle.", "{{q}}", [variable("q", "three")]), model_row(3, "llm_a"),
    ]})
    (template,) = run_pull(monkeypatch, tmp_path, viya).catalog(modelID=model["id"], runID="")

    assert (template["system_template"], template["target_model"], template["template_source"]) == \
        ("Newest.", "", "newest run")


def test_the_manifested_score_code_is_preferred_for_the_template_row(monkeypatch, tmp_path):
    viya, _, model = prompt_viya()
    viya.add_file(model, "desk_answer.py", prompt_score_code(llm="published_llm"), role="score")
    viya.add_file(model, "requirements.json", [{"step": "install requests", "command": "pip install requests"}])
    run = run_pull(monkeypatch, tmp_path, viya)
    (template,) = run.catalog(modelID=model["id"], runID="")

    assert (template["role"], template["target_model"], template["template_source"]) == \
        ("prompt", "published_llm", "score code")
    assert template["system_template"] == "You answer questions about {{topic}} in one sentence."
    assert template["user_template"] == "Question: {{question}}\nContext: {{context}}"
    # the runs are in the catalog all the same
    assert len(run.catalog(modelID=model["id"])) == 4


def test_a_manifested_decision_template_in_the_catalog(monkeypatch, tmp_path):
    viya = FakeViya()
    project = viya.add_project("Claims", ["Prompt-Engineering", "Decision-Engineering"])
    model = viya.add_model(project, "Request Routing", {
        "Prompt-Experiment-Tracker.json": [decision_header(1), model_row(1, "demo_decider", ANSWERS, options="{}")],
        "request_routing.py": decision_score_code(llm="published_decider"),
        # a helper file that is no score code of a template is passed over
        "helpers.py": ("def helper():\n    return 1\n", "documentation"),
    }, algorithm="Decision-Template")
    template, of_run = run_pull(monkeypatch, tmp_path, viya).catalog(modelID=model["id"])

    assert (template["role"], template["runID"], template["target_model"], template["template_source"]) == \
        ("decision", "", "published_decider", "score code")
    assert (template["system_template"], template["user_template"]) == (QUESTIONS, "Request: {{request}}")
    assert (of_run["role"], of_run["runID"], of_run["target_model"]) == ("decision", "1", "demo_decider")
    assert of_run["resolved_system_prompt"] == QUESTIONS
    assert of_run["resolved_user_prompt"] == "Request: My windscreen has a crack since the hail on Tuesday."


def test_a_model_without_runs_and_score_code_is_not_in_the_catalog(monkeypatch, tmp_path):
    viya = FakeViya()
    project = viya.add_project("Service Desk", ["Prompt-Engineering"])
    viya.add_model(project, "Just Created")
    run = run_pull(monkeypatch, tmp_path, viya)

    assert run.rows("catalog") == [] and len(run.rows("experiments")) == 1


def definition_viya():
    viya = FakeViya()
    llms = viya.add_project("LLM Model Project", ["LLM-Models"])
    viya.add_model(llms, "demo_llm", function="LLM")
    viya.add_model(llms, "other_llm", function="LLM", tags=["deprecated"])
    embedders = viya.add_project("Embedding Model Project", ["Embedding-Models"])
    viya.add_model(embedders, "demo_embedder", function="Embedding")
    deciders = viya.add_project("Decision Model Project", ["Decision-Models"])
    viya.add_model(deciders, "demo_decider", function="classification")
    return viya, llms, embedders, deciders


def test_the_models_that_answer_are_found_by_the_name_of_their_project(monkeypatch, tmp_path):
    viya, llms, embedders, deciders = definition_viya()
    # a project whose name only starts like the one that is looked for
    viya.add_model(viya.add_project("LLM Model Project Archive"), "retired_llm")
    run = run_pull(monkeypatch, tmp_path, viya)

    assert [(row["role"], row["name"], row["projectID"], row["projectName"]) for row in run.rows("catalog")] == [
        ("llm", "demo_llm", llms["id"], "LLM Model Project"), ("llm", "other_llm", llms["id"], "LLM Model Project"),
        ("embedding", "demo_embedder", embedders["id"], "Embedding Model Project"),
        ("decision_model", "demo_decider", deciders["id"], "Decision Model Project")]
    assert all(row["modelID"] and row["runID"] == "" and row["target_model"] == "" for row in run.rows("catalog"))
    assert run.sas.of_type("WARNING") == []


def test_the_models_that_answer_can_be_found_by_id_by_tag_and_in_several_projects(monkeypatch, tmp_path):
    viya, llms, embedders, _ = definition_viya()
    second = viya.add_project("Bob's LLMs")
    viya.add_model(second, "bobs_llm")
    run = run_pull(monkeypatch, tmp_path, viya, {
        "_gap_llm_project": f"{llms['id']} | Bob's LLMs", "_gap_embedding_project": "tag:Embedding-Models",
        "_gap_decision_project": ""})

    assert [(row["role"], row["name"]) for row in run.rows("catalog")] == [
        ("llm", "demo_llm"), ("llm", "other_llm"), ("llm", "bobs_llm"), ("embedding", "demo_embedder")]
    # an empty setting asks for nothing and warns of nothing
    assert run.sas.of_type("WARNING") == []


def test_a_project_of_the_models_that_is_not_found_is_a_warning(monkeypatch, tmp_path):
    viya, _, _ = prompt_viya()
    run = run_pull(monkeypatch, tmp_path, viya, {"_gap_embedding_project": "", "_gap_decision_project": ""})

    assert run.rc == "0" and len(run.rows("experiments")) == 3
    assert run.sas.of_type("WARNING") == [
        "No project LLM Model Project was found, the llm models it would hold are not in the catalog."]


# ---------------------------------------------------------------------------
# RAG_SETUPS
# ---------------------------------------------------------------------------

def rag_viya():
    viya = FakeViya()
    project = viya.add_project("Handbook Knowledge", ["LLM", "RAG-Engineering"], function="RAG")
    builder = viya.add_model(project, "Staff Handbook", {
        "rag-setup.json": rag_setup(), "pipeline.yaml": "collection: handbook_v1\n",
        "retrieve_context.py": "COLLECTION = 'handbook_v1'\n\ndef scoreModel(question):\n    return question\n",
    }, function="RAG")
    step = viya.add_model(
        project, "RAG Retrieval - Travel Rules", {"pipeline.yaml": step_pipeline_yaml(), "retrieve_context.py": "pass\n"},
        properties={"collection": "travel_rules_v1", "pipelineVersion": "v2", "configuration": "0f1e2d3c",
                    "ingestionRunId": "20260210T101500Z-1a2b"},
        function="RAG", algorithm="RAG", trainTable="cas-shared-default/Public/TRAVEL_LEDGER")
    return viya, project, builder, step


def test_a_setup_saved_by_the_prompt_builder(monkeypatch, tmp_path):
    viya, project, builder, _ = rag_viya()
    row = run_pull(monkeypatch, tmp_path, viya).rows("rag_setups")[0]

    assert (row["modelID"], row["projectID"], row["projectName"], row["name"], row["source"]) == \
        (builder["id"], project["id"], "Handbook Knowledge", "Staff Handbook", "builder")
    assert (row["collection"], row["backend"], row["embedding_model"], row["embedding_dims"]) == \
        ("handbook_v1", "pgvector", "demo_embedder", 384)
    assert (row["chunker"], row["input_token_limit"], row["overlap_tokens"]) == ("recursive", 256, 32)
    assert (row["tables_prefix"], row["tables_caslib"], row["runs_table"]) == ("HANDBOOK", "Public", "HANDBOOK_RUNS")
    assert (row["source_path"], row["pipeline_version"], row["artifacts_folder"]) == \
        ("/data/handbook", "v1", "/Projects/RAG/generated")
    assert row["job_definition_uri"] == "/jobDefinitions/definitions/ffffffff-0000-4000-8000-000000000001"
    assert (row["ingestion_run_id"], row["configuration"]) == ("", "")
    assert (row["description"], row["createdBy"], row["modifiedBy"]) == ("About Staff Handbook", "ada", "grace")
    assert row["modifiedTime"] > row["creationTime"] > 0


def test_a_model_registered_by_the_step_register_setup(monkeypatch, tmp_path):
    viya, project, _, step = rag_viya()
    row = run_pull(monkeypatch, tmp_path, viya).rows("rag_setups")[1]

    assert (row["modelID"], row["projectID"], row["name"], row["source"]) == \
        (step["id"], project["id"], "RAG Retrieval - Travel Rules", "step")
    assert (row["collection"], row["backend"], row["embedding_model"], row["embedding_dims"]) == \
        ("travel_rules_v1", "pgvector", "demo_embedder", 384)
    assert (row["chunker"], row["input_token_limit"], row["overlap_tokens"]) == ("sentence", 512, 50)
    assert (row["tables_prefix"], row["tables_caslib"], row["runs_table"]) == ("TRAVEL", "Public", "TRAVEL_RUNS")
    assert (row["source_path"], row["pipeline_version"], row["configuration"], row["ingestion_run_id"]) == \
        ("/data/travel", "v2", "0f1e2d3c", "20260210T101500Z-1a2b")
    assert (row["artifacts_folder"], row["job_definition_uri"]) == ("", "")


def test_a_step_model_without_its_pipeline_file_is_read_from_its_properties(monkeypatch, tmp_path):
    viya = FakeViya()
    project = viya.add_project("Knowledge", ["RAG-Engineering"])
    model = viya.add_model(project, "RAG Retrieval - Old Setup",
                           properties={"collection": "old_v1", "pipelineVersion": "v1", "configuration": "abc",
                                       "ingestionRunId": "run-7"},
                           trainTable="cas-shared-default/casuser/OLDSETUP_LEDGER")
    run = run_pull(monkeypatch, tmp_path, viya)
    (row,) = run.rows("rag_setups")

    assert (row["source"], row["collection"], row["pipeline_version"], row["configuration"], row["ingestion_run_id"]) == \
        ("step", "old_v1", "v1", "abc", "run-7")
    # the training table of such a model is <CAS server>/<caslib>/<prefix>_LEDGER
    assert (row["tables_prefix"], row["tables_caslib"], row["runs_table"]) == ("OLDSETUP", "casuser", "OLDSETUP_RUNS")
    assert (row["backend"], row["embedding_model"], row["embedding_dims"]) == ("", "", None)
    # the properties are not in the listing of the project, the model itself was asked for them
    assert (f"/modelRepository/models/{model['id']}",
            {"@accept": "application/vnd.sas.models.model+json"}) in viya.requests


def test_the_pipeline_file_of_the_step_is_read(monkeypatch, tmp_path):
    parse = functions(monkeypatch, tmp_path)["parse_pipeline_yaml"]
    parsed = parse(step_pipeline_yaml(setup="Bob's: rules #1", collection="travel_rules_v1"))

    assert parsed["setup"] == "Bob's: rules #1" and parsed["generated_at"] == "2026-02-10T10:20:00+00:00"
    assert parsed["tables"] == {"project": "TRAVEL", "caslib": "Public"}
    assert parsed["embedding"] == {"model": "demo_embedder", "endpoint": "https://viya.example.com/llm",
                                   "dimensions": "384"}
    assert parsed["store"]["collection"] == "travel_rules_v1" and parsed["run"]["configuration"] == "0f1e2d3c"
    assert parse("") == {} and parse(None) == {} and parse("just words\n") == {}


def test_a_model_of_a_rag_project_that_holds_no_setup_is_left_out(monkeypatch, tmp_path):
    viya, _, _, _ = rag_viya()
    viya.add_model(viya.projects[0], "Notes", {"readme.md": "Nothing to see."})
    run = run_pull(monkeypatch, tmp_path, viya)

    assert [row["name"] for row in run.rows("rag_setups")] == ["Staff Handbook", "RAG Retrieval - Travel Rules"]
    assert "The model Notes of the project Handbook Knowledge holds no RAG setup and is left out." in run.sas.of_type("NOTE")


def test_every_setup_is_in_the_catalog_with_its_collection(monkeypatch, tmp_path):
    viya, project, builder, step = rag_viya()
    rows = run_pull(monkeypatch, tmp_path, viya).catalog(role="rag")

    assert [(row["modelID"], row["name"], row["collection"], row["target_model"], row["runID"]) for row in rows] == [
        (builder["id"], "Staff Handbook", "handbook_v1", "demo_embedder", ""),
        (step["id"], "RAG Retrieval - Travel Rules", "travel_rules_v1", "demo_embedder", "")]
    # who wrote the model: the link program takes the setup of the Builder where both hold one collection
    assert [row["template_source"] for row in rows] == ["builder", "step"]
    assert all(row["projectID"] == project["id"] and row["system_template"] == "" for row in rows)


def test_the_run_tables_to_look_for(monkeypatch, tmp_path):
    viya, project, builder, step = rag_viya()
    viya.add_model(project, "No Tables", {"rag-setup.json": rag_setup("notes_v1", prefix="", caslib="")})
    viya.add_model(project, "Odd Names", {"rag-setup.json": rag_setup("with space", prefix="ODD", caslib="My Lib")})
    viya.add_model(project, "Bad Prefix", {"rag-setup.json": rag_setup("bad_v1", prefix="9 LIVES", caslib="Public")})
    viya.add_model(project, "Bad Caslib", {"rag-setup.json": rag_setup("bad_v2", prefix="BAD", caslib='Pub"lic')})
    run = run_pull(monkeypatch, tmp_path, viya)

    assert run.rows("rag_run_sources") == [
        {"modelID": builder["id"], "projectID": project["id"], "tables_caslib": "Public",
         "runs_table": "HANDBOOK_RUNS", "collection": "handbook_v1"},
        {"modelID": step["id"], "projectID": project["id"], "tables_caslib": "Public",
         "runs_table": "TRAVEL_RUNS", "collection": "travel_rules_v1"},
        # a caslib with a blank can be named, a collection that can not stand in SAS code is no filter
        {"modelID": run.rows("rag_setups")[3]["modelID"], "projectID": project["id"], "tables_caslib": "My Lib",
         "runs_table": "ODD_RUNS", "collection": ""},
    ]
    assert run.sas.of_type("WARNING")[:2] == [
        "The run table Public.9 LIVES_RUNS of the RAG setup Bad Prefix can not be named in SAS code and was left out",
        "The run table Pub`lic.BAD_RUNS of the RAG setup Bad Caslib can not be named in SAS code and was left out"]
    # all six setups are in RAG_SETUPS all the same
    assert len(run.rows("rag_setups")) == 6


def test_the_columns_of_a_run_table_are_the_ones_rag_core_writes(monkeypatch, tmp_path):
    namespace = functions(monkeypatch, tmp_path)
    monkeypatch.syspath_prepend(str(INTEGRATIONS / "RAG"))
    from rag_core import history

    texts = [name for name, _ in namespace["RAG_RUN_TEXTS"]]
    assert sorted(namespace["RAG_RUN_NUMBERS"]) == sorted(history.RUN_NUMERIC)
    assert sorted(texts + namespace["RAG_RUN_NUMBERS"]) == sorted(history.RUN_COLUMNS)
    names = [name for name, _, _ in namespace["RAG_RUN_COLUMNS"]]
    assert names[:2] == ["modelID", "projectID"] and names[-2:] == ["source_caslib", "source_table"]
    assert len(set(names)) == len(names) == len(history.RUN_COLUMNS) + 4


# ---------------------------------------------------------------------------
# Paging and failures
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("without_count", [False, True])
def test_every_listing_is_read_to_its_last_page(monkeypatch, tmp_path, without_count):
    viya = FakeViya(page_size=2, without_count=without_count)
    projects = [viya.add_project(f"Prompts {number}", ["Prompt-Engineering"]) for number in range(5)]
    for project in projects:
        for number in range(3):
            model = viya.add_model(project, f"{project['name']} - Template {number}")
            for extra in range(4):
                viya.add_file(model, f"note-{extra}.md", "A note.")
            # the tracker is the last of five files, so it is on the third page
            viya.add_file(model, "Prompt-Experiment-Tracker.json",
                          [header_row(1, "S", "U", []), model_row(1, "demo_llm")])
    definitions = viya.add_project("LLM Model Project")
    for number in range(7):
        viya.add_model(definitions, f"llm_{number}")
    run = run_pull(monkeypatch, tmp_path, viya)

    assert len(run.rows("experiments")) == 15
    assert all(row["model"] == "demo_llm" for row in run.rows("experiments"))
    assert [row["name"] for row in run.catalog(role="llm")] == [f"llm_{number}" for number in range(7)]
    starts = [query["start"] for path, query in viya.requests
              if path == "/modelRepository/projects" and "Prompt-Engineering" in query.get("filter", "")]
    assert starts[:3] == ["0", "2", "4"]
    assert "5 project(s) with the tag Prompt-Engineering and 15 model(s) in them were read." in run.sas.of_type("NOTE")


def test_a_listing_that_repeats_its_page_ends(monkeypatch, tmp_path):
    """A service that ignores start would otherwise be asked for ever."""
    list_all = functions(monkeypatch, tmp_path)["list_all"]
    asked = []

    def get_json(path):
        asked.append(path)
        return {"items": [{"id": "a"}, {"id": "b"}], "count": 9}

    assert list_all(get_json, "/things?filter=x", 2) == [{"id": "a"}, {"id": "b"}]
    assert asked == ["/things?filter=x&start=0&limit=2", "/things?filter=x&start=2&limit=2"]
    assert list_all(lambda path: {"items": []}, "/things", 2) == []
    assert list_all(lambda path: None, "/things", 2) == []
    # a listing whose count is reached is not asked again
    del asked[:]
    assert len(list_all(lambda path: asked.append(path) or {"items": [{"id": 1}, {"id": 2}], "count": 2}, "/things", 50)) == 2
    assert asked == ["/things?start=0&limit=50"]


def test_a_file_that_can_not_be_read_is_a_warning_with_the_model_and_the_pull_goes_on(monkeypatch, tmp_path):
    viya, project, good = prompt_viya()
    broken = viya.add_model(project, "Broken & Co", {"Prompt-Experiment-Tracker.json": "[{\"runId\": 1, ",
                                                    "Prompt-Optimization-Tracker.json": [optimization_entry(4)]})
    gone = viya.add_model(project, "Gone")
    viya.failing.add(viya.add_file(gone, "Prompt-Experiment-Tracker.json", []) + "/content")
    viya.add_model(project, "After", {"Prompt-Experiment-Tracker.json": [header_row(1, "S", "U", []), model_row(1, "demo_llm")]})
    run = run_pull(monkeypatch, tmp_path, viya, {"_gap_llm_project": "", "_gap_embedding_project": "",
                                                 "_gap_decision_project": ""})

    assert run.rc == "0"
    rows = run.rows("experiments")
    assert [(row["name"], row["runID"]) for row in rows] == [
        ("Desk Answer", 1), ("Desk Answer", 1), ("Desk Answer", 2), ("Broken & Co", None), ("Gone", None), ("After", 1)]
    # the other file of the model whose tracker is broken is read
    assert [row["optimizationId"] for row in run.rows("optimizations")] == [4]
    warnings = run.sas.of_type("WARNING")
    assert len(warnings) == 2
    # the name went through SAS statements, so its ampersand did not
    assert warnings[0].startswith(f"The file Prompt-Experiment-Tracker.json of the model Broken + Co ({broken['id']}) "
                                  "could not be read and is left out: JSONDecodeError")
    assert warnings[1].startswith(f"The file Prompt-Experiment-Tracker.json of the model Gone ({gone['id']}) "
                                  "could not be read and is left out: RuntimeError: HTTP 500")
    assert all("'" not in warning and '"' not in warning and ";" not in warning for warning in warnings)


def test_a_model_or_a_project_that_can_not_be_listed_is_a_warning(monkeypatch, tmp_path):
    viya, project, model = prompt_viya()
    locked = viya.add_project("Locked", ["Prompt-Engineering"])
    viya.add_model(locked, "Unseen")
    viya.failing.add(f"/modelRepository/projects/{locked['id']}/models")
    viya.failing.add(f"/modelRepository/models/{model['id']}/contents")
    run = run_pull(monkeypatch, tmp_path, viya, {"_gap_llm_project": "", "_gap_embedding_project": "",
                                                 "_gap_decision_project": ""})

    assert run.rc == "0"
    # the model whose files can not be listed is in the table without its runs
    assert [(row["name"], row["runID"]) for row in run.rows("experiments")] == [("Desk Answer", None)]
    assert [warning.split(":")[0] for warning in run.sas.of_type("WARNING")] == [
        f"The files of the model Desk Answer ({model['id']}) could not be listed and is left out",
        "The models of the project Locked could not be listed and is left out"]


def test_projects_that_can_not_be_listed_end_the_pull_and_nothing_is_handed_over(monkeypatch, tmp_path):
    viya, _, _ = prompt_viya()
    viya.failing.add("/modelRepository/projects")
    run = run_pull(monkeypatch, tmp_path, viya)

    # _gap_rc stays at -1, which the SAS code reads as: leave the output tables as they are
    assert run.rc == "-1"
    assert run.sas.of_type("ERROR") == [
        "SAS Model Manager could not be read: RuntimeError: HTTP 500 for /modelRepository/projects?filter=eq(tags,"
        "`Prompt-Engineering`)+start=0+limit=100"]
    assert list(tmp_path.iterdir()) == []
    assert not any(name.startswith("_gap_rows_") for name in run.sas.macro_variables)


def test_without_the_requests_package_the_pull_says_so(monkeypatch, tmp_path):
    run = run_pull(monkeypatch, tmp_path, without_requests=True)

    assert run.rc == "-1" and list(tmp_path.iterdir()) == []
    assert run.sas.of_type("ERROR") == [
        "The Python package requests is needed to read SAS Model Manager and is not installed."]


def test_two_outputs_can_not_have_one_name(monkeypatch, tmp_path):
    viya, _, _ = prompt_viya()
    run = run_pull(monkeypatch, tmp_path, viya, {"_gap_out_catalog": "rag_setups"})

    assert run.rc == "-1" and viya.requests == []
    assert run.sas.of_type("ERROR") == ["The outputs rag_setups and catalog have the same table name rag_setups."]
    unnamed = run_pull(monkeypatch, tmp_path, viya, {"_gap_out_rag_runs": ""})
    assert unnamed.rc == "-1" and unnamed.sas.of_type("ERROR") == ["The output rag_runs has no table name."]


def test_without_a_cas_library_nothing_is_read(monkeypatch, tmp_path):
    """The custom step leaves the library empty when its output table is no CAS table, and says so itself."""
    viya, _, _ = prompt_viya()
    run = run_pull(monkeypatch, tmp_path, viya, {"_gap_out_caslib": ""})

    assert run.rc == "-1" and viya.requests == [] and run.sas.messages == []


def test_the_requests_carry_the_token_and_ask_for_what_they_read(monkeypatch, tmp_path):
    viya, project, model = prompt_viya()
    run_pull(monkeypatch, tmp_path, viya)

    first_path, first_query = viya.requests[0]
    assert first_path == "/modelRepository/projects"
    assert first_query == {"filter": "eq(tags,'Prompt-Engineering')", "start": "0", "limit": "100",
                           "@accept": "application/json"}
    assert (f"/modelRepository/projects/{project['id']}/models",
            {"start": "0", "limit": "100", "@accept": "application/json"}) in viya.requests
    # a file is asked for as whatever it is
    content = [query for path, query in viya.requests if path.endswith("/content")]
    assert content == [{"@accept": "*/*"}]
    # nothing is written: the stand-in only knows GET, and every request was one
    assert len(viya.requests) > 5


# ---------------------------------------------------------------------------
# What is handed to SAS
# ---------------------------------------------------------------------------

TABLE_KEYS = ["experiments", "optimizations", "rag_setups", "rag_run_sources", "catalog"]


def test_an_output_without_rows_still_has_its_columns(monkeypatch, tmp_path):
    run = run_pull(monkeypatch, tmp_path)

    assert run.rc == "0" and run.sas.of_type("ERROR") == []
    for key in TABLE_KEYS:
        columns = run.namespace["TABLES"][key]
        assert run.rows(key) == [] and run.macro(f"_gap_rows_{key}") == "0"
        # the LENGTH statement names every column in its order: it is what gives an empty table its columns
        declared = re.findall(r"(\w+) (\$\d+|8)", run.macro(f"_gap_length_{key}"))
        assert [name for name, _ in declared] == [name for name, _, _ in columns]
        assert [length == "8" for _, length in declared] == [kind == "n" for _, kind, _ in columns]
    assert run.macro("_gap_length_catalog").startswith("modelID $36 projectID $36 projectName $256 name $256 role $16")
    # a text that follows its values has a small length of its own when there is no value
    assert "systemPrompt $8 userPrompt $8 response $8" in run.macro("_gap_length_experiments")
    rag_runs = re.findall(r"(\w+) (\$\d+|8)", run.macro("_gap_length_rag_runs"))
    assert rag_runs[:3] == [("modelID", "$36"), ("projectID", "$36"), ("run_id", "$64")]
    assert run.macro("_gap_runs_texts").split()[0] == "run_id" and "embed_seconds" in run.macro("_gap_runs_numbers").split()
    assert sorted(name for name, _ in rag_runs[2:-2]) == \
        sorted(run.macro("_gap_runs_texts").split() + run.macro("_gap_runs_numbers").split())


def test_the_map_states_type_and_length_of_every_column(monkeypatch, tmp_path):
    viya, _, _ = prompt_viya()
    run = run_pull(monkeypatch, tmp_path, viya)

    for key in TABLE_KEYS:
        (dataset,) = run.map(key)["DATASETS"]
        assert (dataset["DSNAME"], dataset["TABLEPATH"]) == ("root", "/root")
        ordinal, *variables = dataset["VARIABLES"]
        assert ordinal == {"NAME": "ordinal_root", "TYPE": "ORDINAL", "PATH": "/root"}
        columns = run.namespace["TABLES"][key]
        assert [variable["NAME"] for variable in variables] == [name for name, _, _ in columns]
        declared = dict(re.findall(r"(\w+) (\$\d+|8)", run.macro(f"_gap_length_{key}")))
        for variable, (name, kind, length) in zip(variables, columns):
            assert variable["PATH"] == "/root/" + name
            if kind == "n":
                assert variable == {"NAME": name, "TYPE": "NUMERIC", "PATH": "/root/" + name}
                assert declared[name] == "8"
            else:
                assert variable["TYPE"] == "CHARACTER" and 1 <= variable["CURRENT_LENGTH"] <= length
                # the same length in the map and in the LENGTH statement, so SAS has one length per column
                assert declared[name] == f"${variable['CURRENT_LENGTH']}"
                assert all(len(row[name].encode("utf-8")) <= variable["CURRENT_LENGTH"] for row in run.rows(key))
        for row in run.rows(key):
            assert list(row) == [name for name, _, _ in columns]
            assert all(isinstance(row[name], str) if kind != "n" else row[name] is None or isinstance(row[name], (int, float))
                       for name, kind, _ in columns)


def test_a_long_text_column_is_as_long_as_its_longest_value(monkeypatch, tmp_path):
    viya = FakeViya()
    project = viya.add_project("Service Desk", ["Prompt-Engineering"])
    long_prompt = "ä" * 20000  # 40,000 bytes
    viya.add_model(project, "Long", {"Prompt-Experiment-Tracker.json": [
        header_row(1, long_prompt, "Short.", []), model_row(1, "demo_llm", "x" * 300)]})
    run = run_pull(monkeypatch, tmp_path, viya)
    (row,) = run.rows("experiments")
    lengths = dict(re.findall(r"(\w+) \$(\d+)", run.macro("_gap_length_experiments")))

    # cut at the longest text SAS holds, between two characters
    assert row["systemPrompt"] == "ä" * 16383 and lengths["systemPrompt"] == "32766"
    assert (lengths["userPrompt"], lengths["response"]) == ("8", "300")
    # the hash is the one of the whole text, so a prompt that was cut can still be compared
    assert row["resolved_system_hash"] == contract_hash(long_prompt)
    assert lengths["name"] == "256" and lengths["modelID"] == "36"


def test_the_columns_the_report_reads_keep_their_name_and_type(monkeypatch, tmp_path):
    columns = functions(monkeypatch, tmp_path)["EXPERIMENT_COLUMNS"]
    kinds = {name: kind for name, kind, _ in columns}

    assert all(kinds[name] in "ct" for name in REPORT_TEXTS)
    assert all(kinds[name] == "n" for name in REPORT_NUMBERS)
    # the columns the table had are in front, in their order, and the new ones behind them
    names = [name for name, _, _ in columns]
    assert names[:len(COLUMNS_BEFORE)] == COLUMNS_BEFORE
    assert names[len(COLUMNS_BEFORE):] == [
        "modelID", "projectID", "projectName", "kind", "expected", "options", "variables", "resolved_system_prompt",
        "resolved_user_prompt", "resolved_system_hash", "resolved_user_hash", "judge_reasoning"]
    assert len(set(names)) == len(names)


def test_what_travels_through_sas_statements_is_cleaned(monkeypatch, tmp_path):
    sas_safe = functions(monkeypatch, tmp_path)["sas_safe"]

    assert sas_safe("Bob's \"100%\" R&D; done\nnext") == "Bob`s `100pct ` R+D, done next"


# ---------------------------------------------------------------------------
# The SAS code around the Python, as far as its text says
# ---------------------------------------------------------------------------

def sas_code(program):
    """A SAS program without its Python and without its comments."""
    code = program.replace(python_block(program), "")
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
    return "\n".join(line for line in code.split("\n") if not line.strip().startswith("*"))


def test_the_outputs_are_set_at_the_top_of_the_code_file():
    program = code_file_program()
    settings = dict(re.findall(r"^%let (_gap_\w+?)\s*=\s*(.*?);$", program.split("cas mySess;")[0], flags=re.MULTILINE))

    assert settings == DEFAULT_SETTINGS
    code = sas_code(program)
    # no table name and no library is written into the code below the settings
    below = code.split("cas mySess;", 1)[1]
    for name in ("PROMPT_EXPERIMENTS", "PROMPT_OPTIMIZATIONS", "RAG_SETUPS", "MODEL_CATALOG", "'Public'", '"Public"'):
        assert name not in below
    assert below.count('inCASlib="&_gap_out_caslib." outCASLib="&_gap_out_caslib."') == 1
    for key in ("experiments", "optimizations", "rag_setups", "rag_runs", "catalog"):
        assert len(re.findall(rf"%_gap_publish\(\w+, &_gap_out_{key}\.\);", below)) == 1


def test_every_table_is_dropped_loaded_promoted_and_saved():
    code = sas_code(code_file_program())
    publish = code[code.index("%macro _gap_publish("):code.index("%mend _gap_publish;")]

    assert [line.strip().split(" ")[0] for line in publish.split("proc casUtil")[1].split("quit;")[0].split("\n")[1:-1]] == \
        ["dropTable", "load", "promote", "save"]
    assert 'save casData="&_gap_name." casOut="&_gap_name..sashdat" replace;' in publish


def test_the_run_ends_with_one_line_per_output_table():
    code = sas_code(code_file_program())
    written = code[code.index("%macro _gap_written("):code.index("%mend _gap_written;")]
    write = code[code.index("%macro _gap_write_tables;"):code.index("%mend _gap_write_tables;")]

    assert "%put NOTE: &_gap_out_caslib..&_gap_name. was written with &_gap_obs. rows and &_gap_vars. columns.;" in written
    assert "%put ERROR: The table &_gap_out_caslib..&_gap_name. was not written.;" in written
    calls = re.findall(r"%_gap_(publish|written)\((\w+), &_gap_out_(\w+)\.\);", write)
    tables = [("_gap_all_data", "experiments"), ("_gap_all_optimizations", "optimizations"),
              ("_gap_all_rag_setups", "rag_setups"), ("_gap_all_rag_runs", "rag_runs"), ("_gap_all_catalog", "catalog")]
    # all five tables are written first, then the five lines follow one another
    assert calls == [("publish", *table) for table in tables] + [("written", *table) for table in tables]


def test_the_macro_variables_the_sas_code_reads_are_the_ones_the_python_sets(monkeypatch, tmp_path):
    run = run_pull(monkeypatch, tmp_path)
    code = sas_code(code_file_program())
    set_by_python = {name for name in run.sas.macro_variables if name not in DEFAULT_SETTINGS} - \
        {"_gap_viyaHost", "_gap_work", "_gap_rc"}

    for key in TABLE_KEYS:
        assert {f"_gap_rows_{key}", f"_gap_length_{key}"} <= set_by_python
    assert {"_gap_length_rag_runs", "_gap_runs_texts", "_gap_runs_numbers"} <= set_by_python
    # every one of them is read by the SAS code, directly or through %_gap_read(<key>, ...)
    read_keys = re.findall(r"%_gap_read\((\w+), \w+\);", code)
    assert sorted(read_keys) == sorted(TABLE_KEYS)
    assert "&&_gap_rows_&_gap_key." in code and "&&_gap_length_&_gap_key." in code
    for name in ("_gap_length_rag_runs", "_gap_runs_texts", "_gap_runs_numbers", "_gap_rows_rag_run_sources", "_gap_rc"):
        assert f"&{name}." in code
    # and all of them are deleted at the end
    deleted = set(re.findall(r"_gap_\w+", code[code.index("%symdel _gap_viyaHost"):]))
    assert set_by_python <= deleted
    # the Python reads its settings under the names the SAS code sets them
    asked = set(re.findall(r"setting\('(_gap_\w+)'\)", python_block(code_file_program())))
    assert "setting('_gap_out_' + key)" in python_block(code_file_program())
    asked |= {"_gap_out_" + key for key in ("experiments", "optimizations", "rag_setups", "rag_runs", "catalog")}
    assert asked == set(DEFAULT_SETTINGS) | {"_gap_viyaHost", "_gap_work"}


def test_the_files_are_read_from_where_the_python_writes_them(monkeypatch, tmp_path):
    run = run_pull(monkeypatch, tmp_path)
    code = sas_code(code_file_program())

    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(
        f"_gap_{key}.{extension}" for key in TABLE_KEYS for extension in ("json", "map"))
    assert 'filename _gapjs "&_gap_work./_gap_&_gap_key..json";' in code
    assert 'filename _gapmp "&_gap_work./_gap_&_gap_key..map";' in code
    assert "libname _gapjs json fileref=_gapjs map=_gapmp;" in code
    assert "%let _gap_work = %sysfunc(pathname(work));" in code_file_program()
    assert run.sas.macro_variables["_gap_work"] == str(tmp_path)


def test_every_labelled_column_is_a_column_of_its_table(monkeypatch, tmp_path):
    namespace = functions(monkeypatch, tmp_path)
    code = sas_code(code_file_program())
    labels = code[code.index("proc datasets lib=work nolist;"):code.index("libname _gapout cas")]
    tables = {"_gap_all_data": "EXPERIMENT_COLUMNS", "_gap_all_optimizations": "OPTIMIZATION_COLUMNS",
              "_gap_all_rag_setups": "RAG_SETUP_COLUMNS", "_gap_all_rag_runs": "RAG_RUN_COLUMNS",
              "_gap_all_catalog": "CATALOG_COLUMNS"}
    blocks = re.split(r"modify (\w+);", labels)[1:]

    assert blocks[0::2] == list(tables)
    for table, block in zip(blocks[0::2], blocks[1::2]):
        columns = {name: kind for name, kind, _ in namespace[tables[table]]}
        labelled = re.findall(r"(\w+) = '[^']+'", block)
        assert set(labelled) <= set(columns) and len(set(labelled)) == len(labelled)
        if table != "_gap_all_rag_runs":
            assert set(labelled) == set(columns), table
        for name in re.findall(r"format ([\w ]+?) datetime", block)[0].split() if "format" in block else []:
            assert columns[name] == "n"
    # the labels the report shows are the ones the table had
    assert "name = 'Prompt Name'" in blocks[1] and "run_time = 'Run Time (s)'" in blocks[1]
    assert "format creationTime modifiedTime datetime22.8;" in blocks[1]


def test_a_run_table_is_only_read_when_it_is_in_memory():
    code = sas_code(code_file_program())
    stack = code[code.index("%macro _gap_stack_rag_runs;"):code.index("%mend _gap_stack_rag_runs;")]

    assert "table.tableExists result=runTable /" in stack
    assert "%else %if &_gap_runs_exists. EQ 0 %then %do;" in stack
    assert "is not in memory, its runs are not in &_gap_out_rag_runs..;" in stack
    # nothing loads, saves, drops or changes a table of a setup
    for action in ("loadTable", "table.save", "dropTable", "alterTable", "table.promote", "load casData", "casOut"):
        assert action not in stack
    assert 'modelID = "&_gap_rs_model.";' in stack and 'projectID = "&_gap_rs_project.";' in stack
    # a run table can hold the runs of other collections of the same vector store: a setup gets the runs of its own
    assert "if collection NE '' and collection NE \"&_gap_rs_collection.\" then delete;" in stack
    assert "%if %length(&_gap_rs_collection.) GT 0 %then %do;" in stack
    # a column is read as text or as a number whatever the release that published the table wrote it as
    assert "&_gap_col. = cats(_gap_s&_gap_j.);" in stack
    assert "&_gap_col. = input(cats(_gap_s&_gap_j.), ?? best32.);" in stack
    assert "call missing(&_gap_col.);" in stack
    # the table exists before any setup is looked at, so it is written when no setup has runs
    assert stack.index("data work._gap_all_rag_runs;") < stack.index("%do _gap_i = 1 %to")


def test_the_output_tables_are_left_alone_when_the_reading_failed():
    code = sas_code(code_file_program())
    write = code[code.index("%macro _gap_write_tables;"):code.index("%mend _gap_write_tables;")]

    assert "%let _gap_rc = -1;" in code and code.index("%let _gap_rc = -1;") < code.index("proc python restart;")
    guard = write.index("%if &_gap_rc. NE 0 %then %do;")
    assert guard < write.index("%return;", guard) < write.index("%_gap_read(") < write.index("%_gap_publish(")
    assert "were left as they are.;" in write
    assert python_block(code_file_program()).rstrip().endswith("run_pull()")


def test_the_python_holds_no_line_that_would_end_it():
    block = python_block(code_file_program())

    assert code_file_program().count("\nsubmit;\n") == 1 and code_file_program().count("\nendsubmit;\n") == 1
    assert "endsubmit" not in block
    # the code file has no character outside ASCII in its Python
    assert block.isascii()
    # nor what SAS code reads as a comment or as a macro variable, should the block ever be read as SAS code
    assert "/*" not in block and "*/" not in block
    assert re.findall(r"&\w+", block) == []
    compile(block, "Get-All-Prompts.sas (python)", "exec")


# ---------------------------------------------------------------------------
# The custom step
# ---------------------------------------------------------------------------

def test_the_step_runs_the_core_of_the_code_file():
    assert core(step_program()) == core(code_file_program())
    assert python_block(step_program()) == python_block(code_file_program())
    # the core is everything between the settings and the clean-up
    assert core(code_file_program()).count("\n") > 800


def test_the_step_file_is_one_line_of_json():
    from get_all_prompts_harness import STEP_FILE
    raw = STEP_FILE.read_bytes()

    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw and b"\n" not in raw
    step = json.loads(raw.decode("utf-8"))
    assert json.dumps(step, separators=(",", ":"), ensure_ascii=False).encode("utf-8") == raw
    ui = json.loads(step["ui"])
    assert step["ui"] == json.dumps(ui, indent="\t", ensure_ascii=False)
    assert step["localUi"] == json.dumps(ui, separators=(",", ":"), sort_keys=True, ensure_ascii=False)
    assert step["description"] == step["localDescription"]


def controls(children):
    found = {}
    for child in children:
        found[child["id"]] = child
        found.update(controls(child.get("children", [])))
    return found


def test_the_step_keeps_its_controls_and_has_one_for_every_setting():
    ui = step_ui()
    by_id = controls(ui["pages"])

    # the controls the step had, under their ids
    for control_id in ("pageOptions", "text1", "_gap_output", "pageAbout", "textAbout", "sectionPrereqs", "textPrereqs",
                       "sectionDocumentation", "textDocumentation", "sectionChangelog", "textChangelog"):
        assert control_id in by_id
    assert by_id["_gap_output"] == {"id": "_gap_output", "type": "outputtable", "label": "Prompt experiment CAS table:",
                                    "required": True, "placeholder": "PROMPT_EXPERIMENTS", "visible": ""}
    assert step_definition()["flowMetadata"]["outputPorts"][0]["name"] == "_gap_output"
    # every setting of the code file but the two the output table gives
    settings = {name: value for name, value in DEFAULT_SETTINGS.items()
                if name not in ("_gap_out_caslib", "_gap_out_experiments")}
    for name, default in settings.items():
        assert by_id[name]["type"] == "textfield" and by_id[name]["placeholder"] == default
        assert ui["values"][name] == default
    assert ui["values"]["_gap_output"] == {"library": "", "table": ""}
    assert len(by_id) == len(re.findall(r'"id": "', step_definition()["ui"]))


def test_the_step_takes_its_settings_from_its_controls():
    program = step_program()
    head = program.split(core(program))[0]
    code = sas_code(program)

    # no setting of a control is overwritten: the step only fills in a field that was left empty
    assert re.findall(r"^%let (_gap_\w+)", head, flags=re.MULTILINE) == ["_gap_out_caslib", "_gap_out_experiments"]
    assert "%let _gap_out_experiments = &_gap_output_name.;" in head
    assert "%let _gap_out_caslib=%sysfunc(getlcaslib(&_gap_output_lib));" in head
    defaults = dict(re.findall(r"^%_gap_setting\((_gap_\w+),\s*(.*?)\);$", head, flags=re.MULTILINE))
    assert defaults == {
        "_gap_tag": "Prompt-Engineering", "_gap_rag_tag": "RAG-Engineering", "_gap_llm_project": "",
        "_gap_embedding_project": "", "_gap_decision_project": "", "_gap_out_optimizations": "PROMPT_OPTIMIZATIONS",
        "_gap_out_rag_setups": "RAG_SETUPS", "_gap_out_rag_runs": "RAG_RUNS", "_gap_out_catalog": "MODEL_CATALOG"}
    # an output table that is no CAS table: the step says so and leaves the library empty, so nothing is read or written
    assert head.index("%let _gap_out_caslib =;") < head.index('%if "%upcase(&_gap_output_engine)"="CAS" %then %do;')
    assert 'putlog "ERROR: The selected output library does not point to a CASlib";' in head
    assert "%if %length(&_gap_out_caslib.) EQ 0 %then %return;" in code
    # the step starts a CAS session only when there is none, and ends the one it started
    assert "%if %sysfunc(sessfound(%sysfunc(getoption(sessref)))) NE 1 %then %do;" in head
    assert "cas mySess" not in program and program.count("cas gapMySess;") == 1
    tail = program.split(core(program))[1]
    assert "%if &_gap_clean_cas_session. %then %do;\n    cas gapMySess terminate;\n%end;" in tail
    assert "delete _gap_all_data _gap_all_optimizations _gap_all_rag_setups _gap_all_rag_runs _gap_all_catalog;" in tail


def test_the_about_tab_names_the_five_tables():
    by_id = controls(step_ui()["pages"])
    about = by_id["textAbout"]["text"]

    for words in ("Prompt experiments", "Prompt optimizations", "RAG setups", "RAG runs", "Model catalog",
                  "modelID, runID, model", "modelID, optimizationId", "modelID, run_id"):
        assert words in about
    assert by_id["textChangelog"]["text"].startswith("* Version: 2.0 (11OCT2026)")
    assert by_id["textChangelog"]["text"].endswith("* Version: 1.0 (09AUG2025)\n      - Initial version")
    # an About tab says what the step does, not what was or was not run
    texts = " ".join(control.get("text", "") for control in by_id.values())
    assert "not run in SAS" not in texts and "not been run" not in texts


# ---------------------------------------------------------------------------
# The names of the output tables
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("setting, name, said", [
    ("_gap_out_optimizations", "ZZ_VERIFY_R14_PROMPT_OPTIMIZATIONS",
     "The table name ZZ_VERIFY_R14_PROMPT_OPTIMIZATIONS of the output optimizations has 34 characters. A SAS libref addresses a "
     "CAS table by a name of 32 characters at most, nothing was read or written."),
    ("_gap_out_catalog", "A" * 33,
     "The table name " + "A" * 33 + " of the output catalog has 33 characters. A SAS libref addresses a CAS table by a name "
     "of 32 characters at most, nothing was read or written."),
    ("_gap_out_rag_runs", "RAG RUNS",
     "The table name RAG RUNS of the output rag_runs is no SAS name: letters, digits and underscores, and no digit in front. "
     "Nothing was read or written."),
    ("_gap_out_experiments", "2026_EXPERIMENTS",
     "The table name 2026_EXPERIMENTS of the output experiments is no SAS name: letters, digits and underscores, and no digit "
     "in front. Nothing was read or written."),
], ids=["34 characters", "33 characters", "a blank", "a digit in front"])
def test_a_table_name_no_libref_can_address_ends_the_pull_before_it_reads(monkeypatch, tmp_path, setting, name, said):
    """CAS takes a longer name. The table was written, promoted and saved, and the last step
    of the program, which looks at it through a libref, reported it as not written."""
    viya, _, _ = prompt_viya()
    run = run_pull(monkeypatch, tmp_path, viya, {setting: name})

    assert run.rc == "-1" and viya.requests == [] and list(tmp_path.iterdir()) == []
    assert run.sas.of_type("ERROR") == [said]


def test_a_table_name_of_32_characters_is_taken(monkeypatch, tmp_path):
    viya, _, _ = prompt_viya()
    names = {"_gap_out_experiments": "E" * 32, "_gap_out_optimizations": "_" + "o" * 31, "_gap_out_rag_setups": "S1",
             "_gap_out_rag_runs": "r_2", "_gap_out_catalog": "ZZ_VERIFY_R15_MODEL_CATALOG"}
    run = run_pull(monkeypatch, tmp_path, viya, names)

    assert run.rc == "0" and run.sas.of_type("ERROR") == []


def test_all_five_names_are_looked_at_before_anything_is_read(monkeypatch, tmp_path):
    """The name that is too long is the one of the last table: nothing was asked of SAS Model Manager."""
    viya, _, _ = prompt_viya()
    check = functions(monkeypatch, tmp_path)["check_output_names"]
    good = {"experiments": "A", "optimizations": "B", "rag_setups": "C", "rag_runs": "D", "catalog": "E"}

    assert check(good) == ""
    for output in good:
        assert "has 40 characters" in check(dict(good, **{output: "T" * 40}))
    program = python_block(code_file_program())
    assert program.index("problem = check_output_names(") < program.index("tables = pull(get_json, get_text, settings, say)")


# ---------------------------------------------------------------------------
# A RAG setup without a collection
# ---------------------------------------------------------------------------

def test_a_setup_whose_properties_can_not_be_read_is_not_in_the_catalog(monkeypatch, tmp_path):
    """SAS Model Manager answered the request for one model with HTTP 500. The row of such a
    setup has no collection, and a row of the catalog without one can be linked to no request."""
    viya, project, builder, step = rag_viya()
    lost = viya.add_model(project, "RAG Retrieval - Content Sample", function="RAG", algorithm="RAG")
    viya.failing.add(f"/modelRepository/models/{lost['id']}")
    run = run_pull(monkeypatch, tmp_path, viya)

    assert [row["name"] for row in run.rows("rag_setups")] == ["Staff Handbook", "RAG Retrieval - Travel Rules", "RAG Retrieval - Content Sample"]
    assert run.rows("rag_setups")[2]["collection"] == ""
    assert [row["name"] for row in run.catalog(role="rag")] == ["Staff Handbook", "RAG Retrieval - Travel Rules"]
    assert [warning for warning in run.sas.of_type("WARNING") if "RAG" in warning] == [
        f"The properties of the model RAG Retrieval - Content Sample ({lost['id']}) could not be read: RuntimeError: HTTP 500 for "
        f"/modelRepository/models/{lost['id']}. The setup has no collection without them and is left out of the model catalog, "
        "where no request could be linked to it. Its row of the RAG setups stays."]
    # the run table of such a setup is not looked for either: it names no table
    assert [row["modelID"] for row in run.rows("rag_run_sources")] == [builder["id"], step["id"]]


def test_a_setup_whose_properties_can_not_be_read_but_whose_pipeline_file_names_the_collection_stays(monkeypatch, tmp_path):
    viya, project, _, step = rag_viya()
    viya.failing.add(f"/modelRepository/models/{step['id']}")
    viya.details[step["id"]] = None
    run = run_pull(monkeypatch, tmp_path, viya)

    assert [row["collection"] for row in run.catalog(role="rag")] == ["handbook_v1", "travel_rules_v1"]
    assert [warning for warning in run.sas.of_type("WARNING") if "RAG" in warning] == [
        f"The properties of the model RAG Retrieval - Travel Rules ({step['id']}) could not be read: RuntimeError: HTTP 500 for "
        f"/modelRepository/models/{step['id']}"]


def test_a_setup_file_that_names_no_collection_is_not_in_the_catalog(monkeypatch, tmp_path):
    viya = FakeViya()
    project = viya.add_project("Knowledge", ["RAG-Engineering"])
    empty = viya.add_model(project, "No Store", {"rag-setup.json": {"version": 1}})
    broken = viya.add_model(project, "Broken", {"rag-setup.json": "{ not json"})
    run = run_pull(monkeypatch, tmp_path, viya)

    assert [row["name"] for row in run.rows("rag_setups")] == ["No Store", "Broken"]
    assert run.catalog(role="rag") == []
    warnings = [warning for warning in run.sas.of_type("WARNING") if "No project" not in warning]
    assert len(warnings) == 3 and warnings[0] == (f"The RAG setup No Store ({empty['id']}) names no collection and is left out of the model catalog, "
                           "where no request could be linked to it. Its row of the RAG setups stays.")
    assert warnings[1].startswith("The file rag-setup.json of the model Broken ") and warnings[2].startswith(f"The RAG setup Broken ({broken['id']}) names no collection")


# ---------------------------------------------------------------------------
# The CAS session of the code file
# ---------------------------------------------------------------------------

def test_the_code_file_starts_a_cas_session_only_where_none_is_connected_and_ends_only_that_one():
    """Behind Log-Parser-Code.sas in one SAS session, 'cas mySess;' gave the WARNING 'A session
    with the name MYSESS already exists', which SAS Job Execution reports as the error of the
    job, and 'cas mySess terminate;' ended the session the librefs of the parser were bound to."""
    program = code_file_program()
    head = program[:program.index(core(program))]
    tail = program[program.index(core(program)) + len(core(program)):]

    assert ("%if %sysfunc(sessfound(%sysfunc(getoption(sessref)))) NE 1 %then %do;\n"
            "    %let _gap_clean_cas_session = 1;\n"
            "    cas mySess;\n"
            "%end;\n"
            "%else %do;\n"
            "    %let _gap_clean_cas_session = 0;\n"
            "%end;\n") in head
    statements = [line.strip() for line in sas_code(program).split("\n")]
    assert [line for line in statements if line.lower().startswith("cas ")] == ["cas mySess;", "cas mySess terminate;"]
    assert "%if &_gap_clean_cas_session. %then %do;\n    cas mySess terminate;\n%end;" in tail
    assert "_gap_clean_cas_session / nowarn;" in tail
    # the check is the one of the step and of the log parser
    check = "%if %sysfunc(sessfound(%sysfunc(getoption(sessref)))) NE 1 %then %do;"
    assert check in step_program().split(core(step_program()))[0]
    assert check in (INTEGRATIONS / "Logging-Monitoring" / "Log-Parser-Code.sas").read_text(encoding="utf-8")
