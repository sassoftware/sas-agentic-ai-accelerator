# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The Enrich stage: the prompt contract, the mapping, and what it refuses.

The score code these tests load is the shape the Prompt Builder manifests -
`def scoreModel(...)` with an `"Output: ..."` docstring - so a change to that
generator that broke the contract would fail here rather than during someone's
ingestion.
"""
import pytest

from rag_core.enrich import (CHUNK_FIELDS, PromptModel, document_context,
                             field_values, parse_mapping, parse_names,
                             render_mapping, run_enrich, validate_selection)
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


def test_selected_outputs_are_kept_as_tags():
    prompt = PromptModel(PARSING_PROMPT)
    rows, failures = run_enrich(chunks(1), prompt, {"chunk": "chunk"},
                                tag_outputs=["department"], log=lambda m: None)
    assert failures == []
    assert rows[0]["tags"]["department"] == "finance"
    # tags only: nothing was asked to become a header, so none is written
    assert rows[0].get("context_header") is None


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
                                tag_outputs=["department"], log=lambda m: None)
    assert len(failures) == 1
    assert "JSON" in failures[0][1]
    assert rows[0]["tags"] == {}         # "unknown" was a default, not an answer


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


def test_a_setup_that_does_not_enrich_keeps_the_fingerprint_it_had():
    """The upgrade guarantee.

    The enrichment settings join the configuration fingerprint, which is what
    makes the drift guard refuse a half-enriched collection. That is only
    acceptable if a corpus which does NOT enrich hashes exactly as it did
    before the stage existed - otherwise every existing collection would
    refuse its next run after an upgrade.
    """
    from rag_core.steps import config_hash

    plain = {"backend": "pgvector", "collection": "liti", "chunker": "recursive",
             "tokens": "256", "overlap": "30", "embedModel": "all_minilm_l6_v2",
             "pipelineVersion": "v1"}
    # pinned: this value is what deployed ledgers already carry, so a change
    # to it is a change that would refuse every existing collection's next run
    assert config_hash(plain) == "8905e7ef1e4f9205"
    enriched = dict(plain, enrichPrompt="abc", enrichCode="deadbeef0001",
                    enrichMapping="chunk=chunk", enrichHeader="response",
                    enrichTags="")
    assert config_hash(enriched) != config_hash(plain)
    # and editing the prompt alone moves it, which is the point
    assert config_hash(dict(enriched, enrichCode="deadbeef0002")) != config_hash(enriched)


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
