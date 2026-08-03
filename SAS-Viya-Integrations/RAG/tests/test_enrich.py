# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The Enrich stage: the prompt contract, the mapping, and what it refuses.

The score code these tests load is the shape the Prompt Builder manifests -
`def scoreModel(...)` with an `"Output: ..."` docstring - so a change to that
generator that broke the contract would fail here rather than during someone's
ingestion.
"""
import pytest

from rag_core.enrich import (CHUNK_FIELDS, PromptModel, attribute_schema,
                             document_context, field_values, list_versions,
                             merge_attribute_schemas, parse_mapping,
                             parse_names, parse_steps, render_mapping,
                             render_steps, run_enrich, validate_pipeline,
                             validate_selection)
from rag_core.steps import stamp_enrich_usage
from rag_core.pricing import estimate_enrich_cost
from rag_core.providers import llm_api_key_entry, llm_api_key_for

HEADER_PROMPT = '''
def scoreModel(chunk, document):
    "Output: response, run_time, prompt_length, output_length"
    llm = "gpt_4o_mini_2024_07_18"
    response = "This chunk is from " + str(document)[:12] + ": " + str(chunk)[:12]
    return response, 0.25, 120, 18
'''

PARSING_PROMPT = '''
def scoreModel(chunk):
    "Output: response, department, parse_status"
    llm = "gpt_5_mini"
    department = "unknown"
    parse_status = 0
    if "invoice" in str(chunk):
        department = "finance"
        parse_status = 1
    return "{}", department, parse_status
'''

FAILING_PROMPT = '''
def scoreModel(chunk):
    "Output: response"
    llm = "gpt_5_mini"
    return "LLM call failed: connection refused"
'''

DECISION_PROMPT = '''
def scoreModel(chunk):
    "Output: llmBody, llmURL"
    llm = "gpt_5_mini"
    return "{}", "https://example.invalid/llm"
'''

KEYED_PROMPT = '''
def scoreModel(chunk, API_KEY):
    "Output: response"
    llm = "gpt_5_mini"
    return "key of " + str(len(str(API_KEY))) + " chars"
'''


def chunks(count=3, doc="doc-1"):
    return [
        {"chunk_id": f"c{index}", "doc_id": doc, "chunk_index": index,
         "content": f"chunk body {index} about invoices",
         "source_uri": "/data/docs/policy.md", "heading_path": "Policy > Fees",
         "tags": {}}
        for index in range(count)
    ]


# ---- the contract ---------------------------------------------------------
def test_inputs_and_outputs_come_from_the_score_code():
    prompt = PromptModel(HEADER_PROMPT, name="Header prompt")
    assert prompt.inputs == ["chunk", "document"]
    assert prompt.outputs == ["response", "run_time", "prompt_length", "output_length"]
    assert prompt.llm == "gpt_4o_mini_2024_07_18"
    assert prompt.needs_api_key is False
    assert prompt.code_hash and len(prompt.code_hash) == 12


def test_api_key_is_resolved_not_mapped():
    prompt = PromptModel(KEYED_PROMPT)
    assert prompt.needs_api_key is True
    assert prompt.variables == ["chunk"]        # API_KEY is not a mapped input


def test_a_call_llm_node_prompt_is_refused_with_the_reason():
    with pytest.raises(RuntimeError, match="integrated LLM call"):
        PromptModel(DECISION_PROMPT, name="ID prompt")


def test_a_module_without_scoremodel_is_refused():
    with pytest.raises(RuntimeError, match="not a manifested prompt"):
        PromptModel("x = 1", name="not a prompt")


def test_outputs_must_be_declared():
    with pytest.raises(RuntimeError, match="Output"):
        PromptModel("def scoreModel(chunk):\n    return 'x'\n")


# ---- the mapping ----------------------------------------------------------
def test_document_context_rebuilds_the_document_and_caps_it():
    context = document_context(chunks(3), max_chars=20)
    doc = context["doc-1"]
    assert doc["count"] == 3
    assert doc["truncated"] is True
    assert len(doc["document"]) == 20


def test_field_values_cover_the_whole_vocabulary():
    rows = chunks(3)
    values = field_values(rows[1], document_context(rows))
    assert set(values) == set(CHUNK_FIELDS)
    assert values["chunk"] == rows[1]["content"]
    assert values["filename"] == "policy.md"
    assert values["position"] == "chunk 2 of 3"
    assert rows[0]["content"] in values["neighbours"]
    assert rows[2]["content"] in values["neighbours"]


def test_mapping_round_trips_through_the_job_parameter_form():
    mapping = {"document": "document", "chunk": "chunk"}
    assert render_mapping(mapping) == "chunk=chunk;document=document"
    assert parse_mapping("chunk=chunk;document=document") == mapping
    assert parse_names(" a , b ,, c ") == ["a", "b", "c"]


def test_validation_names_every_problem_at_once():
    prompt = PromptModel(HEADER_PROMPT)
    problems = validate_selection(prompt, {"chunk": "nonesuch", "extra": "chunk"},
                                  "sentiment", [])
    joined = " | ".join(problems)
    assert "document" in joined          # unmapped input
    assert "extra" in joined             # input the prompt does not have
    assert "nonesuch" in joined          # field that does not exist
    assert "sentiment" in joined         # output the prompt does not return


def test_storing_nothing_is_a_validation_error():
    prompt = PromptModel(HEADER_PROMPT)
    problems = validate_selection(prompt, {"chunk": "chunk", "document": "document"},
                                  "", [])
    assert any("nothing would be stored" in problem for problem in problems)


# ---- running --------------------------------------------------------------
def test_the_header_lands_where_the_embed_step_reads_it():
    prompt = PromptModel(HEADER_PROMPT)
    rows, failures = run_enrich(chunks(2), prompt,
                                {"chunk": "chunk", "document": "document"},
                                header_output="response", max_workers=2,
                                log=lambda message: None)
    assert failures == []
    assert all(row["context_header"].startswith("This chunk is from") for row in rows)
    assert prompt.usage == {"calls": 2, "input_tokens": 240, "output_tokens": 36,
                            "run_time": 0.5, "failed": 0}


def test_selected_outputs_are_kept_as_columns():
    prompt = PromptModel(PARSING_PROMPT)
    rows, failures = run_enrich(chunks(1), prompt, {"chunk": "chunk"},
                                column_outputs=["department"], log=lambda m: None)
    assert failures == []
    assert rows[0]["attributes"] == {"department": "finance"}
    # columns only: nothing was asked to become a header, so none is written
    assert rows[0].get("context_header") is None
    # and the chunk records which prompt wrote it
    assert rows[0]["enrich_version"].endswith("@" + prompt.code_hash)


def test_a_failed_call_is_never_stored_as_a_header():
    prompt = PromptModel(FAILING_PROMPT)
    rows, failures = run_enrich(chunks(2), prompt, {"chunk": "chunk"},
                                header_output="response", log=lambda m: None)
    assert len(failures) == 2
    assert prompt.usage["failed"] == 2
    # the chunk goes on to be embedded, plain - not dropped
    assert all(row.get("context_header") is None for row in rows)
    assert "connection refused" in failures[0][1]


def test_an_unparsed_response_does_not_store_the_prompt_authors_defaults():
    prompt = PromptModel(PARSING_PROMPT)
    rows = [dict(row, content="nothing to classify here") for row in chunks(1)]
    rows, failures = run_enrich(rows, prompt, {"chunk": "chunk"},
                                column_outputs=["department"], log=lambda m: None)
    assert len(failures) == 1
    assert "JSON" in failures[0][1]
    # "unknown" was the prompt author's default, not an answer
    assert rows[0].get("attributes") is None


def test_checkpointed_chunks_are_not_re_enriched():
    prompt = PromptModel(HEADER_PROMPT)
    rows = chunks(3)
    run_enrich(rows, prompt, {"chunk": "chunk", "document": "document"},
               header_output="response", already_enriched={"c0", "c1"},
               log=lambda m: None)
    assert prompt.usage["calls"] == 1


def test_an_oversized_header_is_cut_rather_than_swallowing_the_chunk():
    long_prompt = (
        'def scoreModel(chunk):\n'
        '    "Output: response"\n'
        '    llm = "gpt_5_mini"\n'
        '    return "x" * 5000\n')
    rows, failures = run_enrich(chunks(1), PromptModel(long_prompt), {"chunk": "chunk"},
                                header_output="response", log=lambda m: None)
    assert failures == []
    assert len(rows[0]["context_header"]) == 1000


def test_the_api_key_reaches_the_prompt_without_being_mapped():
    prompt = PromptModel(KEYED_PROMPT)
    rows, failures = run_enrich(chunks(1), prompt, {"chunk": "chunk"},
                                header_output="response", api_key="secret-value",
                                log=lambda m: None)
    assert failures == []
    assert rows[0]["context_header"] == "key of 12 chars"


def test_a_mapping_the_prompt_cannot_accept_fails_before_any_call():
    prompt = PromptModel(HEADER_PROMPT)
    with pytest.raises(ValueError, match="document"):
        run_enrich(chunks(1), prompt, {"chunk": "chunk"},
                   header_output="response", log=lambda m: None)
    assert prompt.usage["calls"] == 0


def test_the_header_is_what_gets_embedded():
    """The stage only pays for itself if the header reaches the vector.

    The Embed step reads `context_header` and prepends it; this asserts the
    two halves meet, which no unit test of either alone can show.
    """
    from rag_core.steps import run_embed

    class RecordingClient:
        def __init__(self):
            self.usage = {"calls": 0, "run_time": 0.0, "tokens": 0}
            self.texts = []

        def embed(self, text, mode="document"):
            self.texts.append(text)
            return [0.1, 0.2]

    prompt = PromptModel(HEADER_PROMPT)
    rows, _ = run_enrich(chunks(2), prompt,
                         {"chunk": "chunk", "document": "document"},
                         header_output="response", log=lambda m: None)
    client = RecordingClient()
    embedded, failed = run_embed(rows, client, log=lambda m: None)
    assert failed == []
    assert len(client.texts) == 2
    for text in client.texts:
        header, _, body = text.partition("\n")
        assert header.startswith("This chunk is from")
        assert body.startswith("chunk body")
    assert all(chunk["embedding"] == [0.1, 0.2] for chunk in embedded)


def test_enrichment_is_not_part_of_the_drift_fingerprint():
    """Changing a prompt must not demand a full re-ingest (owner, 2026-08-03).

    The fingerprint covers what would make a collection unreadable - the
    chunker, the window, the embedding model. Enrichment is deliberately
    outside it, so a corpus may carry headers from two prompt versions and
    each chunk records which one wrote it. The pinned value is what deployed
    ledgers already hold: a change here refuses every existing collection's
    next run.
    """
    from rag_core.steps import config_hash

    plain = {"backend": "pgvector", "collection": "liti", "chunker": "recursive",
             "tokens": "256", "overlap": "30", "embedModel": "all_minilm_l6_v2",
             "pipelineVersion": "v1"}
    assert config_hash(plain) == "8905e7ef1e4f9205"


# ---- columns --------------------------------------------------------------
def test_stored_outputs_are_typed_from_the_registered_variables():
    prompt = PromptModel(PARSING_PROMPT)
    prompt.output_types = {"department": "string", "confidence": "decimal"}
    assert attribute_schema(prompt, ["department", "confidence"]) == {
        "department": "string", "confidence": "decimal"}
    # an output the model never declared is text: readable, not summable
    assert attribute_schema(prompt, ["mystery"]) == {"mystery": "string"}


def test_a_decimal_output_is_stored_as_a_number_and_a_gap_as_null():
    numeric = ('def scoreModel(chunk):\n'
               '    "Output: response, confidence, parse_status"\n'
               '    llm = "gpt_5_mini"\n'
               '    if "invoices" in str(chunk):\n'
               '        return "{}", "0.75", 1\n'
               '    return "{}", "", 1\n')
    prompt = PromptModel(numeric)
    prompt.output_types = {"confidence": "decimal"}
    rows, failures = run_enrich(chunks(1), prompt, {"chunk": "chunk"},
                                column_outputs=["confidence"], log=lambda m: None)
    assert failures == []
    assert rows[0]["attributes"]["confidence"] == 0.75
    blank = [dict(row, content="nothing here") for row in chunks(1)]
    blank, _ = run_enrich(blank, prompt, {"chunk": "chunk"},
                          column_outputs=["confidence"], log=lambda m: None)
    # NULL, not 0 - "the prompt did not say" is not a measurement of zero
    assert blank[0]["attributes"]["confidence"] is None


def test_an_output_that_collides_with_a_chunk_column_is_refused_by_name():
    colliding = ('def scoreModel(chunk):\n'
                 '    "Output: response, score, content"\n'
                 '    llm = "gpt_5_mini"\n'
                 '    return "{}", 1, "x"\n')
    problems = validate_selection(PromptModel(colliding), {"chunk": "chunk"},
                                  "response", ["score", "content"])
    joined = " | ".join(problems)
    assert "score" in joined and "content" in joined
    assert "already uses those names" in joined


def test_an_output_name_that_is_not_an_identifier_is_refused():
    odd = ('def scoreModel(chunk):\n'
           '    "Output: response, Total Spend"\n'
           '    llm = "gpt_5_mini"\n'
           '    return "{}", 1\n')
    problems = validate_selection(PromptModel(odd), {"chunk": "chunk"},
                                  "response", ["Total Spend"])
    assert any("cannot be a column name" in problem for problem in problems)


# ---- the columns on the collection ----------------------------------------
class FakeCursor:
    """Just enough cursor to watch what DDL an adapter would run."""

    def __init__(self, owner):
        self.owner = owner

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.owner.executed.append((" ".join(sql.split()), params))

    def fetchall(self):
        return [(name,) for name in self.owner.existing]


class FakeStore:
    """A stand-in for whichever adapter, exercising the shared base logic."""

    _COLUMNS = ["chunk_id", "content", "embedding"]

    def __init__(self, existing):
        self.existing = existing
        self.executed = []
        self._conn = None

    def _cursor(self):
        return FakeCursor(self)


def _adapter(existing):
    from rag_core.adapters.base import VectorStoreAdapter

    store = FakeStore(existing)
    store.__class__ = type("Probe", (FakeStore, VectorStoreAdapter),
                           {"__abstractmethods__": frozenset()})
    return store


def test_new_outputs_become_columns_and_say_they_are_not_backfilled():
    store = _adapter(["chunk_id", "content", "embedding", "id", "valid_to"])
    delta = store.sync_attributes("liti", {"department": "string",
                                           "confidence": "decimal"})
    assert delta["added"] == ["confidence", "department", "enrich_version"]
    assert delta["kept"] == []
    ddl = [sql for sql, _ in store.executed if sql.startswith("ALTER")]
    assert "ALTER TABLE liti ADD COLUMN confidence double precision" in ddl
    assert "ALTER TABLE liti ADD COLUMN department text" in ddl


def test_a_column_that_already_exists_is_not_added_twice():
    store = _adapter(["chunk_id", "content", "embedding", "department",
                      "enrich_version"])
    delta = store.sync_attributes("liti", {"department": "string"})
    assert delta["added"] == []
    assert not [sql for sql, _ in store.executed if sql.startswith("ALTER")]


def test_a_column_the_setup_no_longer_produces_is_kept_not_dropped():
    store = _adapter(["chunk_id", "content", "embedding", "department",
                      "sentiment", "enrich_version"])
    delta = store.sync_attributes("liti", {"department": "string"})
    assert delta["kept"] == ["sentiment"]
    assert not [sql for sql, _ in store.executed if "DROP" in sql]


def test_the_collections_own_columns_are_what_a_write_uses():
    store = _adapter(["chunk_id", "content", "embedding", "department", "id"])
    # `id` is the table's own, not an enrichment column
    assert store.attribute_columns("liti") == ["department"]
    assert store._columns_for("liti") == ["chunk_id", "content", "embedding",
                                          "department"]


def test_a_column_name_that_reached_the_adapter_unchecked_is_refused():
    store = _adapter(["chunk_id"])
    with pytest.raises(ValueError, match="cannot be a column name"):
        store.sync_attributes("liti", {"drop table x; --": "string"})


# ---- version pinning ------------------------------------------------------
#
# The shapes below are what a live SAS Viya answered on 2026-08-02, including
# the two ways it says nothing: `/models/{versionId}/contents` returns 200 with
# an empty collection, and a version whose snapshot lost its file body returns
# 200 with zero bytes. Both would otherwise load as "a prompt with no code".
class FakeResponse:
    def __init__(self, status=200, payload=None, body=b""):
        self.status_code = status
        self._payload = payload
        self.content = body

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeViya:
    """Serves the model-repository paths the loader uses, and nothing else."""

    def __init__(self, code=HEADER_PROMPT, pinned_code=PARSING_PROMPT,
                 empty_version=""):
        self.code = code
        self.pinned_code = pinned_code
        self.empty_version = empty_version
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        path = url.replace("https://viya", "")
        if path.endswith("/modelVersions"):
            return FakeResponse(payload={"items": [
                {"id": "v-old", "modelVersionName": "1.0",
                 "creationTimeStamp": "2026-07-01T09:00:00Z"},
                {"id": "v-new", "modelVersionName": "1.1",
                 "creationTimeStamp": "2026-08-01T09:00:00Z"},
            ]})
        if "/history/" in path and path.endswith("/contents"):
            version = path.split("/history/")[1].split("/")[0]
            if version == self.empty_version:
                return FakeResponse(payload={"items": []})
            return FakeResponse(payload={"items": [
                {"name": "prompt.py", "role": "score", "fileUri": "",
                 "links": [{"rel": "content",
                            "uri": f"/history/{version}/score/content"}]},
                {"name": "outputVar.json", "role": "outputVariables",
                 "fileUri": "",
                 "links": [{"rel": "content",
                            "uri": f"/history/{version}/out/content"}]},
            ]})
        if path.endswith("/contents"):
            return FakeResponse(payload={"items": [
                {"name": "prompt.py", "role": "score",
                 "fileUri": "/files/files/abc"},
                {"name": "outputVar.json", "role": "outputVariables",
                 "fileUri": "/files/files/def"},
            ]})
        if path.endswith("/score/content"):
            return FakeResponse(body=self.pinned_code.encode("utf-8"))
        if path.endswith("/out/content"):
            return FakeResponse(body=b'[{"name": "department", "type": "string"}]')
        if path.endswith("/files/files/abc/content"):
            return FakeResponse(body=self.code.encode("utf-8"))
        if path.endswith("/files/files/def/content"):
            return FakeResponse(body=b'[{"name": "response", "type": "string"}]')
        return FakeResponse(payload={"name": "Situate the chunk"})


def test_latest_reads_the_model_and_pinned_reads_the_version():
    viya = FakeViya()
    latest = PromptModel.from_model_manager("https://viya", "t", "m1", session=viya)
    assert latest.version_id == ""
    assert latest.inputs == ["chunk", "document"]        # HEADER_PROMPT
    assert latest.name == "Situate the chunk"
    assert not any("/history/" in call for call in viya.calls)

    viya = FakeViya()
    pinned = PromptModel.from_model_manager("https://viya", "t", "m1",
                                            version_id="v-new", session=viya)
    assert pinned.version_id == "v-new"
    assert pinned.outputs == ["response", "department", "parse_status"]
    assert any("/history/v-new/contents" in call for call in viya.calls)
    # the version's own outputVar.json is what types its columns
    assert pinned.output_types == {"department": "string"}
    assert attribute_schema(pinned, ["department"]) == {"department": "string"}


def test_the_stamp_says_which_prompt_and_which_version_wrote_a_chunk():
    viya = FakeViya()
    pinned = PromptModel.from_model_manager("https://viya", "t", "m1",
                                            version_id="v-new", session=viya)
    assert pinned.stamp == "m1@v-new"
    floating = PromptModel.from_model_manager("https://viya", "t", "m1", session=viya)
    # following the model, the code hash is what identifies what ran
    assert floating.stamp == "m1@" + floating.code_hash


def test_a_version_whose_snapshot_kept_nothing_is_refused():
    viya = FakeViya(empty_version="v-old")
    with pytest.raises(RuntimeError, match="lists no content"):
        PromptModel.from_model_manager("https://viya", "t", "m1",
                                       version_id="v-old", session=viya)


def test_an_empty_score_body_is_refused_rather_than_run_as_an_empty_prompt():
    viya = FakeViya(pinned_code="")
    with pytest.raises(RuntimeError, match="EMPTY"):
        PromptModel.from_model_manager("https://viya", "t", "m1",
                                       version_id="v-new", session=viya)


def test_versions_come_back_oldest_first_with_their_labels():
    versions = list_versions("https://viya", "t", "m1", session=FakeViya())
    assert [v["label"] for v in versions] == ["1.0", "1.1"]
    assert [v["id"] for v in versions] == ["v-old", "v-new"]


# ---- cost and credentials -------------------------------------------------
def test_token_priced_enrichment_prices_input_and_output_separately():
    cost, basis = estimate_enrich_cost(
        "gpt_4o_mini_2024_07_18",
        {"calls": 2, "input_tokens": 1000, "output_tokens": 100})
    assert cost == pytest.approx(1000 * 1.5e-07 + 100 * 6e-07)
    assert "input tokens" in basis and "output tokens" in basis


def test_a_prompt_without_token_outputs_reads_as_unknown_not_free():
    cost, basis = estimate_enrich_cost("gpt_5_mini", {"calls": 5})
    assert cost is None
    assert "prompt_length" in basis


def test_a_locally_served_llm_is_priced_on_seconds():
    cost, _ = estimate_enrich_cost("qwen_25_05b", {"calls": 3, "run_time": 2.0})
    assert cost == pytest.approx(2.0 * 3.9178e-05)


def test_an_unpriced_llm_reads_as_unknown():
    assert estimate_enrich_cost("claude_haiku_4_5_bedrock", {"calls": 1})[0] is None
    assert estimate_enrich_cost("", {"calls": 1})[0] is None


def test_the_provider_entry_follows_the_llm():
    assert llm_api_key_entry("gpt_5_mini") == "OpenAI"
    assert llm_api_key_entry("qwen_25_05b") == ""        # local container
    assert llm_api_key_for("gpt_5_mini", {"OpenAI": "sk-x"}) == "sk-x"
    assert llm_api_key_for("qwen_25_05b", {}, required=False) == ""


def test_a_missing_provider_key_names_the_entry_to_add():
    with pytest.raises(KeyError, match="OpenAI"):
        llm_api_key_for("gpt_5_mini", {})
    with pytest.raises(KeyError, match="fact sheet"):
        llm_api_key_for("some_private_llm", {})


# ---- several prompts, one after the other ---------------------------------
SECOND_PROMPT = '''
def scoreModel(chunk):
    "Output: response, sentiment, parse_status"
    llm = "gpt_4o_mini_2024_07_18"
    sentiment = "positive" if "invoice" in str(chunk) else "neutral"
    parse_status = 1
    return "{}", sentiment, 1
'''


def test_the_packed_step_parameter_round_trips():
    packed = ("m1|v1|chunk=chunk;doc=document|response|department,tone|8"
              "~m2||chunk=chunk||sentiment|4")
    steps = parse_steps(packed)
    assert [step["model_id"] for step in steps] == ["m1", "m2"]
    assert steps[0]["version_id"] == "v1"
    assert steps[0]["mapping"] == {"chunk": "chunk", "doc": "document"}
    assert steps[0]["header_output"] == "response"
    assert steps[0]["column_outputs"] == ["department", "tone"]
    assert steps[0]["workers"] == 8
    # the second follows the model rather than a version, and stores no header
    assert steps[1]["version_id"] == ""
    assert steps[1]["header_output"] == ""
    assert steps[1]["workers"] == 4
    assert render_steps(steps) == packed


def test_a_step_list_survives_being_parsed_twice():
    steps = parse_steps("m1||chunk=chunk|response||4")
    assert parse_steps(steps) == steps


def test_a_record_with_no_model_is_dropped_not_refused():
    # how an enrichment that was configured and then turned off arrives
    assert parse_steps("~~") == []
    assert parse_steps("|||||") == []
    assert parse_steps("") == []
    assert len(parse_steps("m1||chunk=chunk||department|4~")) == 1


def test_a_truncated_record_keeps_its_defaults():
    step = parse_steps("m1|v2")[0]
    assert step["mapping"] == {} and step["column_outputs"] == []
    assert step["workers"] == 4                 # not zero, and not a crash


def test_only_one_step_may_write_the_context_header():
    stages = [
        {"prompt": PromptModel(HEADER_PROMPT, name="Situate"),
         "header_output": "response", "column_outputs": []},
        {"prompt": PromptModel(SECOND_PROMPT, name="Classify"),
         "header_output": "response", "column_outputs": ["sentiment"]},
    ]
    problems = validate_pipeline(stages)
    assert len(problems) == 1
    assert "only one enrichment step" in problems[0]
    # both are named, because "one of them is wrong" is not actionable
    assert "Situate" in problems[0] and "Classify" in problems[0]


def test_two_steps_may_not_store_the_same_column():
    stages = [
        {"prompt": PromptModel(PARSING_PROMPT, name="First"),
         "header_output": "", "column_outputs": ["department"]},
        {"prompt": PromptModel(SECOND_PROMPT, name="Second"),
         "header_output": "", "column_outputs": ["department", "sentiment"]},
    ]
    problems = validate_pipeline(stages)
    assert len(problems) == 1
    assert "department" in problems[0]
    assert "sentiment" not in problems[0]       # only the contested one


def test_a_header_step_followed_by_a_column_step_is_fine():
    stages = [
        {"prompt": PromptModel(HEADER_PROMPT), "header_output": "response",
         "column_outputs": []},
        {"prompt": PromptModel(SECOND_PROMPT), "header_output": "",
         "column_outputs": ["sentiment"]},
    ]
    assert validate_pipeline(stages) == []


def test_every_prompt_that_enriched_a_chunk_is_recorded():
    rows = chunks(2)
    first = PromptModel(HEADER_PROMPT, name="Situate", model_id="mm-1")
    second = PromptModel(SECOND_PROMPT, name="Classify", model_id="mm-2")
    rows, _ = run_enrich(rows, first, {"chunk": "chunk", "document": "document"},
                         header_output="response", log=lambda _: None)
    rows, _ = run_enrich(rows, second, {"chunk": "chunk"},
                         column_outputs=["sentiment"], log=lambda _: None)
    stamps = rows[0]["enrich_version"].split(",")
    assert len(stamps) == 2
    assert stamps[0].startswith("mm-1@") and stamps[1].startswith("mm-2@")
    # and both prompts' work survives: the header AND the column
    assert rows[0]["context_header"].startswith("This chunk is from")
    assert rows[0]["attributes"]["sentiment"] == "positive"


def test_re_running_the_same_prompt_replaces_its_own_stamp():
    rows = chunks(1)
    prompt = PromptModel(PARSING_PROMPT, name="Classify", model_id="mm-1")
    for _ in range(3):
        rows, _ = run_enrich(rows, prompt, {"chunk": "chunk"},
                             column_outputs=["department"], log=lambda _: None)
    assert rows[0]["enrich_version"].count("mm-1@") == 1


def test_the_column_lists_of_several_steps_become_one():
    first = PromptModel(PARSING_PROMPT)
    second = PromptModel(SECOND_PROMPT)
    merged = merge_attribute_schemas([
        attribute_schema(first, ["department"]),
        attribute_schema(second, ["sentiment"]),
    ])
    assert merged == {"department": "string", "sentiment": "string"}


def test_a_second_enrich_step_does_not_erase_the_first_ones_columns():
    # The Load step learns which columns to add from enrich_usage. Overwriting
    # it would have silently dropped the first prompt's columns and values.
    rows = [{"doc_id": "d1"}]
    stamp_enrich_usage(rows, {"calls": 4, "input_tokens": 100, "run_time": 1.5},
                       "gpt_5_mini", {"department": "string"})
    stamp_enrich_usage(rows, {"calls": 6, "input_tokens": 200, "run_time": 2.5},
                       "gpt_4o_mini_2024_07_18", {"tone": "decimal"})
    import json
    usage = json.loads(rows[0]["enrich_usage"])
    assert usage["attributes"] == {"department": "string", "tone": "decimal"}
    assert usage["calls"] == 10 and usage["input_tokens"] == 300
    assert usage["run_time"] == pytest.approx(4.0)
    assert usage["model"] == "gpt_5_mini,gpt_4o_mini_2024_07_18"


def test_an_unreadable_earlier_tally_never_fails_a_run():
    rows = [{"doc_id": "d1", "enrich_usage": "not json"}]
    stamp_enrich_usage(rows, {"calls": 2}, "gpt_5_mini", {"tone": "string"})
    import json
    assert json.loads(rows[0]["enrich_usage"])["calls"] == 2
