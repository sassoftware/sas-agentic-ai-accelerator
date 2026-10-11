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

# The experiment run of the prompt that was kept in its tracker
RUN_QUESTION = "Who signs a travel request?"
RUN_CONTEXT = "Chapter 3: the line manager signs a travel request."


def made_up_viya():
    """A SAS Model Manager with one prompt template, one decision template, one RAG setup that was
    also registered with the step RAG - Register Setup, and the three models that answer."""
    viya = FakeViya()
    prompts = viya.add_project("Handbook Desk", ["LLM", "Prompt-Engineering"])
    prompt = viya.add_model(prompts, "Handbook Answer", {
        "Prompt-Experiment-Tracker.json": [
            header_row(1, SYSTEM_PROMPT, USER_TEMPLATE, [variable("question", RUN_QUESTION), variable("context", RUN_CONTEXT)]),
            model_row(1, "demo_llm", best_prompt=True),
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
    for project_name, model_name in (("LLM Model Project", "demo_llm"), ("Embedding Model Project", "demo_embedder"),
                                     ("Decision Model Project", "demo_decider")):
        answering[model_name] = viya.add_model(viya.add_project(project_name), model_name)
    return viya, {"prompt": prompt, "decision": decision, "setup": setup, "registered": registered, **answering}


def made_up_log():
    """The lines the three containers log for ten requests, as (what the request is, its lines)."""
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
        ("experiment run of the Builder",
         llm_block("2026-01-05 09:30:00", system_prompt=SYSTEM_PROMPT, user_prompt=f"Question: {RUN_QUESTION}\nContext: {RUN_CONTEXT}",
                   user_agent=BROWSER)),
        ("the prompts of that run from the published prompt",
         llm_block("2026-01-05 09:31:00", system_prompt=SYSTEM_PROMPT, user_prompt=f"Question: {RUN_QUESTION}\nContext: {RUN_CONTEXT}")),
        ("health check",
         llm_block("2026-01-05 09:40:00", system_prompt="You are a health check.", user_prompt="Reply with OK.", user_agent="curl/8.5.0")),
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
                              source_table="HANDBOOK_RUNS") for which in ("setup", "registered")], columns=run_columns)

    linked = link_harness.run_program(monkeypatch, tables={
        ("Public", "LLM_LOGS"): upload["frame"], ("Public", "MODEL_CATALOG"): catalog, ("Public", "RAG_RUNS"): runs})
    return {"requests": [what for what, _ in requests], "parsed": parsed, "upload": upload, "pulled": pulled, "catalog": catalog,
            "linked": linked, "models": models}


def test_every_request_of_the_log_is_linked_to_its_model_and_to_what_sent_it(chain):
    models = chain["models"]
    table = chain["linked"].new_table
    assert chain["linked"].sas.lines("ERROR") == [] and chain["linked"].sas.macro_variables["_lnk_row_count"] == "10"

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
        "experiment run of the Builder":      ("llm", "demo_llm", "experiment run", prompt, "prompt", "1", ""),
        "the prompts of that run from the published prompt": ("llm", "demo_llm", "template", prompt, "prompt", "", ""),
        "health check":                       ("llm", "demo_llm", "none", "", "", "", ""),
    }
    assert len(table) == len(expected)
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
    # A request that failed is linked like one that was answered, and says that it failed
    assert (by_request["published decision that failed"]["failed"], by_request["published decision"]["failed"]) == (1, 0)
    # The query of the retrieval and the prompt that carried it meet in the hash of the question
    assert by_request["query of the retrieval"]["input_hash"] == linked_prompt["rag_query_hash"] == link_harness.text_hash(QUESTION)
    assert linked_prompt["rag_collection"] == COLLECTION


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
    assert notes[0].startswith("10 of 10 rows of Public.LLM_LOGS were linked against ")
    assert notes[1:4] == [
        "4 llm rows: 4 linked to a model; caller by experiment run 1, template 2, ambiguous 0, none 1; 1 linked to a RAG setup.",
        "2 decision rows: 2 linked to a model; caller by experiment run 0, questions and state 2, questions 0, ambiguous 0, none 0.",
        "4 embedding rows: 4 linked to a model; caller by retrieval query 1, ingestion run 1, ambiguous 0, none 2.",
    ]


def test_catalog_of_the_pull_holds_what_the_link_was_given(chain):
    models = chain["models"]
    rows = chain["catalog"].to_dict("records")
    described = {(row["role"], row["name"], row["runID"]): row for row in rows}

    template = described[("prompt", "Handbook Answer", "")]
    assert (template["template_source"], template["target_model"]) == ("score code", "demo_llm")
    assert (template["system_template"], template["user_template"]) == (SYSTEM_PROMPT, USER_TEMPLATE)
    run = described[("prompt", "Handbook Answer", "1")]
    assert run["resolved_user_prompt"] == f"Question: {RUN_QUESTION}\nContext: {RUN_CONTEXT}"
    assert run["resolved_user_hash"] == link_harness.text_hash(run["resolved_user_prompt"])
    decision = described[("decision", "Request Triage", "")]
    assert (json.loads(decision["system_template"]), decision["user_template"]) == (json.loads(QUESTIONS), "Request: {{request}}")
    assert {(row["role"], row["name"], row["template_source"], row["collection"]) for row in rows if row["role"] == "rag"} == {
        ("rag", "Staff Handbook", "builder", COLLECTION), ("rag", "RAG Retrieval - Staff Handbook", "step", COLLECTION)}
    assert {(row["role"], row["name"], row["modelID"]) for row in rows if row["role"] in ("llm", "embedding", "decision_model")} == {
        ("llm", "demo_llm", models["demo_llm"]["id"]), ("embedding", "demo_embedder", models["demo_embedder"]["id"]),
        ("decision_model", "demo_decider", models["demo_decider"]["id"])}


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

    # MODEL_CATALOG and RAG_RUNS: every column the link program reads is one the pull writes
    catalog_columns = {name.lower() for name, _, _ in pull["CATALOG_COLUMNS"]}
    read_of_catalog = set(re.findall(r"\bcell\('(\w+)'\)", link_python))
    assert set(link["CATALOG_KEY_COLUMNS"]) <= read_of_catalog <= catalog_columns
    assert read_of_catalog == catalog_columns - {"projectname"}
    run_columns = {name.lower() for name, _, _ in pull["RAG_RUN_COLUMNS"]}
    assert set(link["RUN_COLUMNS"]) | set(re.findall(r"run_cell\('(\w+)'\)", link_python)) <= run_columns

    # The kinds of the parser, the roles of the pull and what the link program takes them for
    assert set(link["ANSWER_ROLE"]) == {"llm", "embedding", "decision"}
    assert {parser["kind_of"]({name}) for name in ("document", "questions", "systemPrompt")} == set(link["ANSWER_ROLE"])
    assert set(link["ANSWER_ROLE"].values()) == {"llm", "embedding", "decision_model"}
    assert link["BUILDER_SETUP"] == "builder"
    # The user agents the parser writes for requests no Builder page sent
    assert [parser["user_agent_class"](agent).lower() for agent in ("python-requests/2.32.5", "SAS/9.4 (Viya)")] == list(link["NOT_A_BROWSER"])
    assert parser["user_agent_class"](BROWSER) == "browser"


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
