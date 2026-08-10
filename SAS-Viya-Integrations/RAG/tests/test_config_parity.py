# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The job and the Studio flow must fingerprint a setup identically.

The RAG Builder's Manifest button generates BOTH for one setup, and both write
their fingerprint to the same ledger. When they disagreed, running the flow
once locked the scheduled job out of its own corpus - and the message it got,
"configuration drift", named nothing the person had changed.

The two assemble the configuration completely differently: the job knows it
all in one program, the flow accumulates it a step at a time. So the tests
here reproduce BOTH assembly paths and compare the result.
"""
import json
import pathlib
import re

import pytest

from rag_core.steps import (CONFIG_KEYS, canonical_config, config_hash,
                            merge_config)

RAG = pathlib.Path(__file__).resolve().parents[1]
REPO = RAG.parents[1]
JOB = REPO / "SAS-Viya-Integrations" / "RAG-Ingestion" / "Ingest-Documents.sas"
STEPS = REPO / "SAS-Viya-Integrations" / "Custom-Steps"


def flow_config():
    """What the five steps accumulate, in the order the flow runs them."""
    config = ""
    for additions in (
        {"pipeline_version": "v1", "source_kind": "path"},   # List
        {"extractor": "auto"},                               # Extract
        {"chunker": "recursive", "input_token_limit": 256,   # Chunk
         "overlap_tokens": 30},
        {"embed_model": "all_minilm_l6_v2"},                 # Embed
        {"backend": "pgvector", "collection": "hr_policies"},  # Load
    ):
        config = merge_config(config, additions)
    return json.loads(config)


def job_config():
    """What Ingest-Documents.sas builds - every value a job parameter string."""
    return {"backend": "pgvector", "collection": "hr_policies",
            "chunker": "recursive", "input_token_limit": "256",
            "overlap_tokens": "30", "embed_model": "all_minilm_l6_v2",
            "pipeline_version": "v1"}


def test_the_job_and_the_flow_fingerprint_the_same_setup_identically():
    assert config_hash(job_config()) == config_hash(flow_config())


def test_a_number_typed_as_text_is_the_same_configuration():
    """Job parameters arrive as strings, step controls as numbers.

    This alone was enough to make the two paths disagree, even where they
    already used the same key name.
    """
    assert (config_hash({**job_config(), "input_token_limit": 256})
            == config_hash({**job_config(), "input_token_limit": "256"}))


def test_the_overlap_of_a_chunker_that_does_not_overlap_is_not_drift():
    """The flow omitted the number for those chunkers and the job always sent
    it. Zeroing it in one place settles that, and is also true: a number
    nothing reads cannot change a chunk."""
    paragraph = {**job_config(), "chunker": "paragraph"}
    assert (config_hash({**paragraph, "overlap_tokens": 30})
            == config_hash({**paragraph, "overlap_tokens": 0})
            == config_hash({k: v for k, v in paragraph.items()
                            if k != "overlap_tokens"}))


@pytest.mark.parametrize("key,value", [
    ("chunker", "paragraph"),
    ("input_token_limit", 512),
    ("overlap_tokens", 60),
    ("embed_model", "granite_embedding_278m"),
    ("pipeline_version", "v2"),
    ("collection", "somewhere_else"),
    ("backend", "singlestore"),
])
def test_the_guard_still_catches_what_changes_a_vector(key, value):
    assert config_hash(job_config()) != config_hash({**job_config(), key: value})


@pytest.mark.parametrize("key,value", [("extractor", "pdfminer"),
                                       ("source_kind", "content")])
def test_what_the_ledger_can_see_for_itself_is_recorded_but_not_hashed(key, value):
    """A different extractor changes the text, so content_hash changes and the
    ledger re-ingests that document unprompted. A moved corpus changes doc_id,
    so the ledger sees deletes and adds. Neither needs the drift guard, and
    hashing them would have split the job from the flow all over again."""
    assert config_hash(flow_config()) == config_hash({**flow_config(), key: value})
    # still carried, so the run history can report it
    assert key in flow_config() or key == "extractor"


def test_an_unknown_key_cannot_change_the_fingerprint():
    """Anything a future step stamps - a cost tally, a note - must not read as
    a configuration change to the runs that came before it."""
    assert config_hash(flow_config()) == config_hash(
        {**flow_config(), "something_added_later": "whatever"})


def test_canonical_config_fills_every_key_whatever_it_was_given():
    assert set(canonical_config({}).keys()) == set(CONFIG_KEYS)
    assert canonical_config({})["input_token_limit"] == 0
    assert canonical_config({"input_token_limit": "not a number"})[
        "input_token_limit"] == 0


# ---------------------------------------------------------------------------
# the generators themselves, so this cannot drift again unnoticed
# ---------------------------------------------------------------------------
def test_the_ingestion_job_hashes_the_canonical_keys():
    """Read from the shipped .sas, because a key it spells differently is a
    value that silently does not count - which is exactly how this started."""
    source = JOB.read_text(encoding="utf-8")
    call = re.search(r"config_hash\(\{(.*?)\}\)", source, re.DOTALL)
    assert call, "Ingest-Documents.sas no longer calls config_hash with a dict"
    keys = set(re.findall(r'"([a-z_]+)":', call.group(1)))
    assert keys == set(CONFIG_KEYS)


def test_the_ingestion_job_stamps_the_run_on_every_chunk():
    """run_load's lineage arguments: without them a scheduled run writes blank
    run_id/config_id/embed_model, and restore(collection, "") matches every
    chunk the job ever wrote instead of one run's."""
    source = JOB.read_text(encoding="utf-8")
    call = re.search(r"run_load\((.*?)\n\n", source, re.DOTALL)
    assert call, "Ingest-Documents.sas no longer calls run_load"
    for argument in ("run_id=", "config_id=", "embed_model=", "embed_dims="):
        assert argument in call.group(1), f"run_load is missing {argument}"


def test_the_flow_steps_stamp_only_keys_the_fingerprint_knows_or_ignores():
    """Every key the flow contributes is either canonical or deliberately
    unhashed. A typo would otherwise be invisible: it would simply never
    reach the fingerprint."""
    recorded_but_not_hashed = {"extractor", "source_kind"}
    stamped = set()
    for step in sorted(STEPS.glob("RAG - *.step")):
        for block in re.findall(r"stamp_config\((.*?)\}\)",
                                step.read_text(encoding="utf-8"), re.DOTALL):
            stamped.update(re.findall(r'\\"([a-z_]+)\\":', block))
    assert stamped, "no stamp_config calls found in the custom steps"
    assert stamped <= set(CONFIG_KEYS) | recorded_but_not_hashed, (
        f"unexpected configuration keys: {stamped - set(CONFIG_KEYS) - recorded_but_not_hashed}")


def test_the_list_step_passes_the_include_code_setting():
    """The job has honoured it since it shipped; the flow silently did not."""
    source = (STEPS / "RAG - List Documents.step").read_text(encoding="utf-8")
    assert "_rgls_includeCode" in source
    assert "include_code=P[\\\"_rgls_includeCode\\\"]" in source
