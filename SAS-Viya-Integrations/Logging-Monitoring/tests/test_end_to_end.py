"""The three programs in a row: parse a log, pull the catalog, link the rows.

Log-Parser-Code.sas, Get-All-Prompts.sas and Link-Logs-To-Models.sas are written to one
contract - the columns of the log table, the columns of MODEL_CATALOG and RAG_RUNS, the
hash of a text - and each has tests of its own that build the other side by hand. Here
nothing is built by hand: the rows the parser makes of log lines and the catalog the pull
makes of tracker, setup and score code files are what the link program is given.

The Python of the three programs runs outside of SAS, with the stand-ins of their
harnesses. What SAS and CAS do between them - the upload, the DATA steps that read the
files of the pull, the stacking of the run tables - is not part of this test. Every log
line and every file is made up for it.
"""
import json
import re

import pandas as pd
import pytest

import get_all_prompts_harness as pull_harness
import link_logs_harness as link_harness
import log_parser_harness as parser_harness
from get_all_prompts_harness import (
    ANSWERS,
    QUESTIONS,
    FakeViya,
    decision_header,
    decision_score_code,
    header_row,
    model_row,
    optimization_entry,
    prompt_score_code,
    rag_setup,
    variable,
)
from log_parser_harness import (
    as_log,
    decision_block,
    decision_inputs,
    embedding_block,
    failed_block,
    llm_block,
    retrieval_envelope,
)

# A prompt template that is fed by a retrieval, the way the Prompt Builder manifests it
SYSTEM_PROMPT = "You answer questions of employees from the passages of the staff handbook. Name the chapter."
USER_TEMPLATE = "Question: {{question}}\nContext: {{context}}"
COLLECTION = "handbook_v1"
QUESTION = "How long does a travel refund take?"
BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

# The two experiment runs of the prompt that were kept in its tracker. They sent the same prompts,
# so only the run time the tracker holds of each answer tells their requests apart
RUN_QUESTION = "Who signs a travel request?"
RUN_CONTEXT = "Chapter 3: the line manager signs a travel request."
RUN_USER_PROMPT = f"Question: {RUN_QUESTION}\nContext: {RUN_CONTEXT}"
RUN_TIMES = {1: 1.25, 2: 2.5}

# What the Prompt Builder sends a judge for a run: the task of the run - both prompts as they were sent -
# and the answers, in the user prompt of the call. The system prompt of the judge is made up here
JUDGE_SYSTEM_PROMPT = "You rank the answers several assistants gave to one task and name the best of them."
JUDGE_USER_PROMPT = "\n".join([
    "== TASK: SYSTEM PROMPT ==", SYSTEM_PROMPT, "", "== TASK: USER PROMPT ==", RUN_USER_PROMPT, "",
    "== CANDIDATE RESPONSES ==", "[A]\nThe line manager signs it, see chapter 3.", "", "Return the JSON object now."])

# What an optimization sends: the system prompt it started from as the objective of the calls to its target,
# and as the task of the calls to its own judge. The wording around it is made up here
OPTIMIZER_SYSTEM_PROMPT = "You are given the fields question and context and return the field response. Your objective is: " + SYSTEM_PROMPT
OPTIMIZER_USER_PROMPT = "question: Who books a rental car?\ncontext: Chapter 4: the travel desk books rental cars."
OPTIMIZER_JUDGE_SYSTEM_PROMPT = "You compare the answer of an assistant with a reference answer for one task."
OPTIMIZER_JUDGE_USER_PROMPT = "== TASK ==\n" + SYSTEM_PROMPT + "\n\n== TASK INPUTS ==\nquestion: Who books a rental car?\n\nReturn the JSON object now."
OPTIMIZATION_STARTED = "2026-01-05T10:00:00.250000+00:00"
OPTIMIZATION_FINISHED = "2026-01-05T10:12:30.500000+00:00"


def made_up_viya():
    """A SAS Model Manager with one prompt template that was run twice, judged and optimized, one decision
    template, one RAG setup that was also registered with the step RAG - Register Setup, and the four
    models that answer."""
    viya = FakeViya()
    prompts = viya.add_project("Handbook Desk", ["LLM", "Prompt-Engineering"])
    run_variables = [variable("question", RUN_QUESTION), variable("context", RUN_CONTEXT)]
    prompt = viya.add_model(prompts, "Handbook Answer", {
        "Prompt-Experiment-Tracker.json": [
            header_row(1, SYSTEM_PROMPT, USER_TEMPLATE, run_variables),
            model_row(1, "demo_llm", run_time=RUN_TIMES[1], best_prompt=True),
            # The second run sent the same prompts, and a judge ranked its answers
            header_row(2, SYSTEM_PROMPT, USER_TEMPLATE, run_variables, judge_model="demo_judge", judge_mode="single"),
            model_row(2, "demo_llm", run_time=RUN_TIMES[2]),
        ],
        "Prompt-Optimization-Tracker.json": [
            optimization_entry(1, startedAt=OPTIMIZATION_STARTED, finishedAt=OPTIMIZATION_FINISHED, targetModel="demo_llm", judgeModel="demo_judge",
                               baselinePrompt={"systemPrompt": SYSTEM_PROMPT, "userPrompt": USER_TEMPLATE}),
        ],
        "HandbookAnswer.py": prompt_score_code(llm="demo_llm", system_literal='f"""' + SYSTEM_PROMPT + '"""'),
    })
    decisions = viya.add_project("Request Desk", ["LLM", "Prompt-Engineering", "Decision-Engineering"])
    decision = viya.add_model(decisions, "Request Triage", {
        "Prompt-Experiment-Tracker.json": [decision_header(1), model_row(1, "demo_decider", ANSWERS, options="{}")],
        "RequestTriage.py": decision_score_code(llm="demo_decider"),
    }, algorithm="Decision-Template")
    knowledge = viya.add_project("Handbook Knowledge", ["LLM", "RAG-Engineering"], function="RAG")
    setup = viya.add_model(knowledge, "Staff Handbook", {"rag-setup.json": rag_setup(COLLECTION)}, function="RAG")
    registered = viya.add_model(
        knowledge, "RAG Retrieval - Staff Handbook",
        {"pipeline.yaml": pull_harness.step_pipeline_yaml(setup="Staff Handbook", collection=COLLECTION, project="HANDBOOK")},
        properties={"collection": COLLECTION}, function="RAG", algorithm="RAG")
    answering = {}
    llm_project = viya.add_project("LLM Model Project")
    for project, model_name in ((llm_project, "demo_llm"), (llm_project, "demo_judge"), (viya.add_project("Embedding Model Project"), "demo_embedder"),
                                (viya.add_project("Decision Model Project"), "demo_decider")):
        answering[model_name] = viya.add_model(project, model_name)
    return viya, {"prompt": prompt, "decision": decision, "setup": setup, "registered": registered, **answering}


def made_up_log():
    """The lines the four containers log for seventeen requests, as (what the request is, its lines)."""
    manifested_user_prompt = f"Question: {QUESTION}\nContext: {retrieval_envelope(COLLECTION, QUESTION)}"
    return [
        ("document of an ingestion run",
         embedding_block("2026-01-05 08:05:00", document="Chapter 7: travel expenses are refunded within thirty days.", project="rag")),
        ("document outside of a run",
         embedding_block("2026-01-05 08:30:00", document="Chapter 8: a made-up chapter that nobody ingested in a run.", project="rag")),
        ("query of the retrieval",
         embedding_block("2026-01-05 09:14:58", document=QUESTION, options="{Embedding_Mode:query,API_KEY:sk-made-up-0123456789}", project="rag")),
        ("published prompt",
         llm_block("2026-01-05 09:15:00", system_prompt=SYSTEM_PROMPT, user_prompt=manifested_user_prompt)),
        ("query no prompt carried",
         embedding_block("2026-01-05 09:16:00", document="What is the dress code?", options="{Embedding_Mode:query}", project="rag")),
        ("published decision",
         decision_block("2026-01-05 09:20:00", questions=QUESTIONS, state="Request: The invoice of March lists the towing twice.",
                        answer="billing (0.900)")),
        ("published decision that failed",
         failed_block("2026-01-05 09:21:00", "demo_decider", decision_inputs("Request: Where is my courtesy car?", QUESTIONS))),
        ("first experiment run of the Builder",
         llm_block("2026-01-05 09:30:00", system_prompt=SYSTEM_PROMPT, user_prompt=RUN_USER_PROMPT, run_time=RUN_TIMES[1], user_agent=BROWSER)),
        ("the prompts of that run from the published prompt",
         llm_block("2026-01-05 09:31:00", system_prompt=SYSTEM_PROMPT, user_prompt=RUN_USER_PROMPT, run_time=RUN_TIMES[1])),
        ("second experiment run of the Builder",
         llm_block("2026-01-05 09:32:00", system_prompt=SYSTEM_PROMPT, user_prompt=RUN_USER_PROMPT, run_time=RUN_TIMES[2], user_agent=BROWSER)),
        ("the prompts of the runs sent once more from the Builder",
         llm_block("2026-01-05 09:33:00", system_prompt=SYSTEM_PROMPT, user_prompt=RUN_USER_PROMPT, run_time=3.75, user_agent=BROWSER)),
        ("judge of the second run",
         llm_block("2026-01-05 09:34:00", model="demo_judge", system_prompt=JUDGE_SYSTEM_PROMPT, user_prompt=JUDGE_USER_PROMPT, user_agent=BROWSER)),
        ("health check",
         llm_block("2026-01-05 09:40:00", system_prompt="You are a health check.", user_prompt="Reply with OK.", user_agent="curl/8.5.0")),
        ("call of the optimization",
         llm_block("2026-01-05 10:05:00", system_prompt=OPTIMIZER_SYSTEM_PROMPT, user_prompt=OPTIMIZER_USER_PROMPT)),
        ("call of the optimization to its judge",
         llm_block("2026-01-05 10:06:00", model="demo_judge", system_prompt=OPTIMIZER_JUDGE_SYSTEM_PROMPT, user_prompt=OPTIMIZER_JUDGE_USER_PROMPT)),
        ("the call of the optimization after it had ended",
         llm_block("2026-01-05 10:30:00", system_prompt=OPTIMIZER_SYSTEM_PROMPT, user_prompt=OPTIMIZER_USER_PROMPT)),
        ("the query of the retrieval long after its prompt",
         embedding_block("2026-01-05 10:45:00", document=QUESTION, options="{Embedding_Mode:query}", project="rag")),
    ]


@pytest.fixture
def chain(monkeypatch, tmp_path):
    """Runs the three programs one after the other and returns what each of them left behind."""
    requests = made_up_log()
    log_path = tmp_path / "llms.log"
    log_path.write_text(as_log(*(lines for _, lines in requests)), encoding="utf-8", newline="")
    parsed = parser_harness.run_parser(monkeypatch, log_path)
    (upload,) = parsed.uploads

    viya, models = made_up_viya()
    work = tmp_path / "work"
    work.mkdir()
    pulled = pull_harness.run_pull(monkeypatch, work, viya)
    assert pulled.rc == "0" and pulled.sas.of_type("ERROR") == [] and pulled.sas.of_type("WARNING") == []
    catalog = pd.DataFrame(pulled.rows("catalog"), columns=[name for name, _, _ in pulled.namespace["CATALOG_COLUMNS"]])

    # RAG_RUNS is stacked by the SAS code of the pull, which no test runs: one run of the run table of the
    # setup, with the columns the pull gives that table and once for each of the two models of the setup
    run_columns = [name for name, _, _ in pulled.namespace["RAG_RUN_COLUMNS"]]
    runs = pd.DataFrame([dict(dict.fromkeys(run_columns), modelID=models[which]["id"], projectID=models[which]["projectId"],
                              run_id="20260105T080000Z-1a2b3c", collection=COLLECTION, embed_model="demo_embedder", status="succeeded",
                              started_at="2026-01-05 08:00:00", finished_at="2026-01-05 08:10:00", source_caslib="Public",
                              source_table="HANDBOOK_RUNS", run_source=pulled.namespace["RUN_FROM_TABLE"])
                         for which in ("setup", "registered")], columns=run_columns)

    linked = link_harness.run_program(monkeypatch, tables={
        ("Public", "LLM_LOGS"): upload["frame"], ("Public", "MODEL_CATALOG"): catalog, ("Public", "RAG_RUNS"): runs})
    return {"requests": [what for what, _ in requests], "parsed": parsed, "upload": upload, "pulled": pulled, "catalog": catalog,
            "linked": linked, "models": models}


def test_every_request_of_the_log_is_linked_to_its_model_and_to_what_sent_it(chain):
    models = chain["models"]
    table = chain["linked"].new_table
    assert chain["linked"].sas.lines("ERROR") == [] and chain["linked"].sas.lines("WARNING") == []
    assert chain["linked"].sas.macro_variables["_lnk_row_count"] == "17"

    prompt, decision, setup = models["prompt"]["id"], models["decision"]["id"], models["setup"]["id"]
    expected = {
        # what the request is:                (kind, model that answered, method, caller, kind of caller, run, RAG setup of the prompt)
        "document of an ingestion run":       ("embedding", "demo_embedder", "ingestion run", setup, "rag", "", ""),
        "document outside of a run":          ("embedding", "demo_embedder", "none", "", "", "", ""),
        "query of the retrieval":             ("embedding", "demo_embedder", "retrieval query", setup, "rag", "", ""),
        "published prompt":                   ("llm", "demo_llm", "template", prompt, "prompt", "", setup),
        "query no prompt carried":            ("embedding", "demo_embedder", "none", "", "", "", ""),
        "published decision":                 ("decision", "demo_decider", "questions and state", decision, "decision", "", ""),
        "published decision that failed":     ("decision", "demo_decider", "questions and state", decision, "decision", "", ""),
        # Two runs with the same prompts: each request gets the run whose run time the tracker holds for it
        "first experiment run of the Builder": ("llm", "demo_llm", "experiment run", prompt, "prompt", "1", ""),
        "the prompts of that run from the published prompt": ("llm", "demo_llm", "template", prompt, "prompt", "", ""),
        "second experiment run of the Builder": ("llm", "demo_llm", "experiment run", prompt, "prompt", "2", ""),
        # A further request with the prompts of the runs is of the prompt, and of no run
        "the prompts of the runs sent once more from the Builder": ("llm", "demo_llm", "template", prompt, "prompt", "", ""),
        # The calls the Prompt Builder makes for the prompt are linked to the prompt
        "judge of the second run":            ("llm", "demo_judge", "judge", prompt, "prompt", "2", ""),
        "health check":                       ("llm", "demo_llm", "none", "", "", "", ""),
        "call of the optimization":           ("llm", "demo_llm", "optimization", prompt, "prompt", "", ""),
        "call of the optimization to its judge": ("llm", "demo_judge", "optimization", prompt, "prompt", "", ""),
        "the call of the optimization after it had ended": ("llm", "demo_llm", "none", "", "", "", ""),
        # The same question embedded again, with no prompt behind it
        "the query of the retrieval long after its prompt": ("embedding", "demo_embedder", "none", "", "", "", ""),
    }
    assert len(table) == len(expected) == len(chain["requests"])
    for what, row in zip(chain["requests"], table.to_dict("records")):
        kind, model, method, caller, caller_kind, run_id, rag_setup_id = expected[what]
        assert (row["kind"], row["model"], row["model_id"]) == (kind, model, models[model]["id"]), what
        assert (row["match_method"], row["caller_model_id"], row["caller_kind"], row["experiment_run_id"], row["rag_model_id"]) == \
            (method, caller, caller_kind, run_id, rag_setup_id), what
        assert row["match_candidates"] == (0 if method == "none" else 1), what

    by_request = dict(zip(chain["requests"], table.to_dict("records")))
    # The caller is named with its project, as SAS Model Manager holds them
    linked_prompt = by_request["published prompt"]
    assert (linked_prompt["caller_name"], linked_prompt["caller_project_id"]) == ("Handbook Answer", models["prompt"]["projectId"])
    assert by_request["published decision"]["caller_name"] == "Request Triage"
    assert by_request["query of the retrieval"]["caller_name"] == "Staff Handbook"
    assert by_request["judge of the second run"]["caller_name"] == by_request["call of the optimization"]["caller_name"] == "Handbook Answer"
    # A request that failed is linked like one that was answered, and says that it failed
    assert (by_request["published decision that failed"]["failed"], by_request["published decision"]["failed"]) == (1, 0)
    # The query of the retrieval and the prompt that carried it meet in the hash of the question
    assert by_request["query of the retrieval"]["input_hash"] == linked_prompt["rag_query_hash"] == link_harness.text_hash(QUESTION)
    assert linked_prompt["rag_collection"] == COLLECTION
    # The question that was embedded again has the same hash, and no prompt follows it within the window
    assert by_request["the query of the retrieval long after its prompt"]["input_hash"] == linked_prompt["rag_query_hash"]


def test_a_run_is_the_request_with_its_run_time(chain):
    table = chain["linked"].new_table
    catalog = chain["catalog"]
    runs = table[table["match_method"] == "experiment run"]
    # Every run of the catalog has one request, and the run time of that request is the one the catalog holds for the run
    assert sorted(zip(runs["experiment_run_id"], runs["runtime"])) == [(str(run), time) for run, time in sorted(RUN_TIMES.items())]
    for row in runs.to_dict("records"):
        (of_run,) = catalog[(catalog["role"] == "prompt") & (catalog["runID"] == row["experiment_run_id"])].to_dict("records")
        assert (of_run["modelID"], of_run["target_model"], of_run["run_time"]) == (row["caller_model_id"], row["model"], row["runtime"])
    # The three other requests with these prompts are of the same prompt and of no run
    same_prompts = table[(table["system_prompt"] == SYSTEM_PROMPT) & (table["user_prompt"] == RUN_USER_PROMPT)]
    assert sorted(zip(same_prompts["match_method"], same_prompts["experiment_run_id"], same_prompts["runtime"])) == [
        ("experiment run", "1", 1.25), ("experiment run", "2", 2.5), ("template", "", 1.25), ("template", "", 3.75)]
    assert chain["linked"].sas.lines("WARNING") == []


def test_link_changes_nothing_but_the_link_columns(chain):
    parser_frame = chain["upload"]["frame"]
    table = chain["linked"].new_table
    link_columns = chain["linked"].namespace["LINK_COLUMNS"]
    others = [column for column in parser_frame.columns if column not in link_columns]

    assert list(table.columns) == list(parser_frame.columns) == chain["parsed"].namespace["COLUMNS"]
    assert len(others) == 27
    pd.testing.assert_frame_equal(table[others], parser_frame[others].reset_index(drop=True))
    # The parser wrote the link columns empty, and the key of the options never reached the table
    assert parser_frame[link_columns].isna().all().all()
    assert table.loc[2, "options"] == "{Embedding_Mode:query,API_KEY:REDACTED}"


def test_summary_of_the_link_counts_what_the_log_holds(chain):
    notes = chain["linked"].sas.lines("NOTE")
    assert notes[0].startswith("17 of 17 rows of Public.LLM_LOGS were linked against ")
    assert notes[1:4] == [
        "10 llm rows: 10 linked to a model, caller by experiment run 2, template 3, optimization 2, judge 1, ambiguous 0, none 2, and 1 linked to a RAG setup.",
        "2 decision rows: 2 linked to a model, caller by experiment run 0, questions and state 2, questions 0, ambiguous 0, none 0.",
        "5 embedding rows: 5 linked to a model, caller by retrieval query 1, ingestion run 1, ambiguous 0, none 3.",
    ]
    # A line of the SAS log is a SAS statement on its way there: a semicolon would end it and the rest would run as code
    for program in ("parsed", "pulled", "linked"):
        for _, message in chain[program].sas.messages:
            assert ";" not in message and "%" not in message and "&" not in message, message


def test_catalog_of_the_pull_holds_what_the_link_was_given(chain):
    models = chain["models"]
    rows = chain["catalog"].to_dict("records")
    described = {(row["role"], row["name"], row["runID"]): row for row in rows if row["role"] != "optimization"}
    prompt = models["prompt"]

    template = described[("prompt", "Handbook Answer", "")]
    assert (template["template_source"], template["target_model"]) == ("score code", "demo_llm")
    assert (template["system_template"], template["user_template"]) == (SYSTEM_PROMPT, USER_TEMPLATE)
    for run_id, run_time in RUN_TIMES.items():
        run = described[("prompt", "Handbook Answer", str(run_id))]
        assert run["resolved_user_prompt"] == RUN_USER_PROMPT
        assert run["resolved_user_hash"] == link_harness.text_hash(run["resolved_user_prompt"])
        # What tells the requests of the two runs apart: the run time and the token counts the tracker holds of the answer
        assert (run["target_model"], run["run_time"], run["prompt_length"], run["output_length"]) == ("demo_llm", run_time, 21, 9)
    decision = described[("decision", "Request Triage", "")]
    assert (json.loads(decision["system_template"]), decision["user_template"]) == (json.loads(QUESTIONS), "Request: {{request}}")
    assert {(row["role"], row["name"], row["template_source"], row["collection"]) for row in rows if row["role"] == "rag"} == {
        ("rag", "Staff Handbook", "builder", COLLECTION), ("rag", "RAG Retrieval - Staff Handbook", "step", COLLECTION)}
    assert {(row["role"], row["name"], row["modelID"]) for row in rows if row["role"] in ("llm", "embedding", "decision_model")} == {
        ("llm", "demo_llm", models["demo_llm"]["id"]), ("llm", "demo_judge", models["demo_judge"]["id"]),
        ("embedding", "demo_embedder", models["demo_embedder"]["id"]), ("decision_model", "demo_decider", models["demo_decider"]["id"])}

    # The judge of the second run: the prompts of the run are the texts its call holds, and no run time and no window are known of it
    (judge,) = [row for row in rows if row["role"] == "judge"]
    assert (judge["modelID"], judge["name"], judge["runID"], judge["target_model"]) == (prompt["id"], "Handbook Answer", "2", "demo_judge")
    assert (judge["resolved_system_prompt"], judge["resolved_user_prompt"]) == (SYSTEM_PROMPT, RUN_USER_PROMPT)
    assert pd.isna(judge["run_time"]) and (judge["window_start"], judge["window_end"], judge["optimization_id"]) == ("", "", "")
    assert judge["resolved_system_prompt"] in JUDGE_USER_PROMPT and judge["resolved_user_prompt"] in JUDGE_USER_PROMPT

    # The optimization: one row per model it called, with the system prompt it started from and the seconds it started and ended in
    optimizations = [row for row in rows if row["role"] == "optimization"]
    assert [(row["modelID"], row["runID"], row["optimization_id"], row["target_model"]) for row in optimizations] == [
        (prompt["id"], "", "1", "demo_llm"), (prompt["id"], "", "1", "demo_judge")]
    for row in optimizations:
        assert (row["resolved_system_prompt"], row["resolved_user_prompt"]) == (SYSTEM_PROMPT, "")
        assert (row["window_start"], row["window_end"]) == ("2026-01-05T10:00:00Z", "2026-01-05T10:12:31Z")
    assert SYSTEM_PROMPT in OPTIMIZER_SYSTEM_PROMPT and SYSTEM_PROMPT in OPTIMIZER_JUDGE_USER_PROMPT

    # The key of the catalog
    keys = [(row["modelID"], row["role"], row["runID"], row["optimization_id"], row["target_model"]) for row in rows]
    assert len(set(keys)) == len(keys) == len(rows)


def test_pull_refuses_a_table_name_that_no_libref_can_address(monkeypatch, tmp_path):
    name = "PROMPT_OPTIMIZATIONS_OF_THE_DESK1"
    assert len(name) == 33
    viya, _ = made_up_viya()
    refused = pull_harness.run_pull(monkeypatch, tmp_path, viya, settings={"_gap_out_optimizations": name})
    # One ERROR line, before SAS Model Manager was asked anything and before a file was written
    assert refused.sas.of_type("ERROR") == [
        f"The table name {name} of the output optimizations has 33 characters. A SAS libref addresses a CAS table "
        "by a name of 32 characters at most, nothing was read or written."]
    assert refused.rc == "-1" and viya.requests == [] and list(tmp_path.iterdir()) == []

    # One character less is a name a libref can address
    taken = pull_harness.run_pull(monkeypatch, tmp_path, viya, settings={"_gap_out_optimizations": name[:32]})
    assert taken.rc == "0" and taken.sas.of_type("ERROR") == []
    assert len(taken.rows("catalog")) > 0 and len(taken.rows("optimizations")) == 1


# ---------------------------------------------------------------------------
# The contract between the three programs, read from their code
# ---------------------------------------------------------------------------

def test_programs_agree_on_the_columns_they_hand_each_other(chain):
    parser, pull, link = chain["parsed"].namespace, chain["pulled"].namespace, chain["linked"].namespace
    link_python = link_harness.python_block(link_harness.code_file_program())

    # The log table: the link columns are the last nine of the parser, and the link program reads no column the parser does not write
    assert parser["COLUMNS"][-9:] == parser["LINK_COLUMNS"] == link["LINK_COLUMNS"]
    assert set(link["CONTENT_COLUMNS"] + link["OPTIONAL_COLUMNS"]) <= set(parser["COLUMNS"])
    assert set(re.findall(r"record\.get\('(\w+)'\)", link_python)) <= set(parser["COLUMNS"])

    # MODEL_CATALOG and RAG_RUNS: every column the link program reads is one the pull writes. It reads all of them but
    # the name of the project and the two token counts of a run, which are in the catalog for its reader
    catalog_columns = {name.lower() for name, _, _ in pull["CATALOG_COLUMNS"]}
    read_of_catalog = set(re.findall(r"\bcell\('(\w+)'\)", link_python))
    assert set(link["CATALOG_KEY_COLUMNS"]) <= read_of_catalog <= catalog_columns
    assert read_of_catalog == catalog_columns - {"projectname", "prompt_length", "output_length"}
    # The catalog the tests of the link program build by hand has the columns of the pull, in their order
    assert link_harness.CATALOG_COLUMNS == [name for name, _, _ in pull["CATALOG_COLUMNS"]]
    run_columns = {name.lower() for name, _, _ in pull["RAG_RUN_COLUMNS"]}
    assert set(link["RUN_COLUMNS"]) | set(re.findall(r"run_cell\('(\w+)'\)", link_python)) <= run_columns

    # The kinds of the parser, the roles of the pull and what the link program takes them for
    assert set(link["ANSWER_ROLE"]) == {"llm", "embedding", "decision"}
    assert {parser["kind_of"]({name}) for name in ("document", "questions", "systemPrompt")} == set(link["ANSWER_ROLE"])
    assert set(link["ANSWER_ROLE"].values()) == {"llm", "embedding", "decision_model"}
    assert link["BUILDER_SETUP"] == "builder"
    roles = set(chain["catalog"]["role"])
    assert roles == set(link["ANSWER_ROLE"].values()) | {"prompt", "decision", "rag"} | set(link["CALL_ROLES"])
    # The roles of the calls are the methods their rows get, and the LLM line of the summary counts them
    assert list(link["CALL_ROLES"]) == ["optimization", "judge"] == link["METHODS"]["llm"][-2:]
    # The user agents the parser writes for requests no Builder page sent
    assert [parser["user_agent_class"](agent).lower() for agent in ("python-requests/2.32.5", "SAS/9.4 (Viya)")] == list(link["NOT_A_BROWSER"])
    assert parser["user_agent_class"](BROWSER) == "browser"

    # The window of an optimization: the text the pull writes is the second the link program reads
    started, finished = pull["utc_text"](OPTIMIZATION_STARTED), pull["utc_text"](OPTIMIZATION_FINISHED, later=True)
    assert (link["sas_seconds"](started), link["sas_seconds"](finished)) == (
        link_harness.sas_datetime("2026-01-05 10:00:00"), link_harness.sas_datetime("2026-01-05 10:12:31"))
    # The project the RAG runtime names in its embedding requests, which the parser keeps as it was sent
    assert link["RUNTIME_PROJECT"] == "rag"


def test_programs_hash_a_text_the_same_way(chain):
    hashes = [chain[run].namespace["text_hash"] for run in ("parsed", "pulled", "linked")]
    for text in ("How long does a travel refund take?", "  two\tlines\r\nof   text ", "", "Erstattung für Österreich", "caf\udce9"):
        assert len({hash_of(text) for hash_of in hashes}) == 1, text
    assert hashes[0]("How long does a travel refund take?") == "e422a7ae4d49d80be7ae1d8c9b32f06398796c0f498f0e5f3c0e342edf7a9032"


def test_programs_name_the_same_tables_by_default():
    parser = dict(re.findall(r"^%let (_log_\w+) = (.*);$", parser_harness.code_file_program(), flags=re.MULTILINE))
    pull = dict(re.findall(r"^%let (_gap_\w+?)\s*= (.*);$", pull_harness.code_file_program(), flags=re.MULTILINE))
    link = dict(re.findall(r"^%let (_lnk_\w+) = (.*);$", link_harness.code_file_program(), flags=re.MULTILINE))

    assert (link["_lnk_log_caslib"], link["_lnk_log_table"]) == (parser["_log_target_table_lib"], parser["_log_target_table_name"]) == ("Public", "LLM_LOGS")
    assert (link["_lnk_catalog_caslib"], link["_lnk_catalog_table"]) == (pull["_gap_out_caslib"], pull["_gap_out_catalog"]) == ("Public", "MODEL_CATALOG")
    assert (link["_lnk_runs_caslib"], link["_lnk_runs_table"]) == (pull["_gap_out_caslib"], pull["_gap_out_rag_runs"]) == ("Public", "RAG_RUNS")
    # Every table name the programs come with is one a CAS libref can address
    for name in [parser["_log_target_table_name"]] + [value for key, value in pull.items() if key.startswith("_gap_out_") and key != "_gap_out_caslib"]:
        assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,31}", name), name
