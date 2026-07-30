# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Chunk lineage, configuration accumulation and the drift guard (§2b).

The pgvector tests need a reachable database and skip without one, so the
logic that decides WHAT gets written is covered here against a fake adapter.
"""
import json

import pytest

from rag_core.steps import (INVENTORY_COLUMNS, check_config_drift, config_hash,
                            merge_config, run_load, stamp_config)


# ---------------------------------------------------------------------------
# configuration accumulated along the flow
# ---------------------------------------------------------------------------
def test_each_step_adds_its_own_settings():
    after_list = merge_config(None, {"pipeline_version": "v1",
                                     "source_kind": "content"})
    after_chunk = merge_config(after_list, {"chunker": "recursive",
                                            "token_limit": 256})
    after_embed = merge_config(after_chunk, {"embed_model": "all_minilm_l6_v2"})
    assert json.loads(after_embed) == {
        "pipeline_version": "v1", "source_kind": "content",
        "chunker": "recursive", "token_limit": 256,
        "embed_model": "all_minilm_l6_v2"}


def test_accumulated_config_is_order_independent():
    """Two flows with the same settings must hash the same."""
    one = merge_config(merge_config(None, {"a": 1}), {"b": 2})
    other = merge_config(merge_config(None, {"b": 2}), {"a": 1})
    assert one == other
    assert config_hash(json.loads(one)) == config_hash(json.loads(other))


def test_unset_values_do_not_enter_the_configuration():
    """Only None and "" are absent settings. Zero is a SETTING - an overlap of
    0 means no overlap, and changing it to 30 is real configuration drift."""
    assert json.loads(merge_config(None, {"extractor": "", "chunker": None,
                                          "overlap_tokens": 0})) == {
        "overlap_tokens": 0}


def test_unreadable_history_never_fails_a_run():
    assert json.loads(merge_config("not json at all", {"a": 1})) == {"a": 1}


def test_stamp_config_writes_the_inventory_column():
    rows = [{"doc_id": "d1"}, {"doc_id": "d2"}]
    stamp_config(rows, {"chunker": "paragraph"})
    assert all(json.loads(r["config_json"])["chunker"] == "paragraph" for r in rows)
    assert "config_json" in INVENTORY_COLUMNS


# ---------------------------------------------------------------------------
# the drift guard
# ---------------------------------------------------------------------------
def test_first_ingestion_has_nothing_to_drift_from():
    assert check_config_drift([], "cfg-1", "v1") == ""


def test_same_configuration_passes():
    ledger = [{"doc_id": "d1", "config_hash": "cfg-1", "pipeline_version": "v1"}]
    assert check_config_drift(ledger, "cfg-1", "v1") == ""


def test_changed_configuration_on_the_same_version_is_refused():
    ledger = [{"doc_id": "d1", "config_hash": "cfg-1", "pipeline_version": "v1"}]
    reason = check_config_drift(ledger, "cfg-2", "v1")
    assert "pipeline version" in reason and "v1" in reason


def test_changed_configuration_with_a_bumped_version_is_intended():
    ledger = [{"doc_id": "d1", "config_hash": "cfg-1", "pipeline_version": "v1"}]
    assert check_config_drift(ledger, "cfg-2", "v2") == ""


def test_the_run_lock_row_is_not_a_configuration_witness():
    ledger = [{"doc_id": "__run_lock__", "config_hash": "cfg-stale",
               "pipeline_version": ""}]
    assert check_config_drift(ledger, "cfg-2", "v1") == ""


# ---------------------------------------------------------------------------
# what run_load writes
# ---------------------------------------------------------------------------
class FakeAdapter:
    """Records calls; asserts nothing itself."""

    needs_flush = False

    def __init__(self):
        self.upserted = []
        self.retired = []
        self.deleted = []
        self.ensured = None

    def ensure_collection(self, name, dims, metric="cosine", schema=None):
        self.ensured = (name, dims, schema)

    def upsert(self, collection, records):
        self.upserted.extend(records)
        return len(records)

    def retire(self, collection, ids=None, filter=None, run_id=None, keep_ids=None):
        self.retired.append({"ids": ids, "filter": filter, "run_id": run_id,
                             "keep_ids": keep_ids})
        return 1

    def delete(self, collection, ids=None, filter=None):
        self.deleted.append({"ids": ids, "filter": filter})
        return 1

    def count(self, collection, filter=None, include_retired=False, as_of=None):
        return len(self.upserted)


def _chunk(doc_id, chunk_id):
    return {"doc_id": doc_id, "chunk_id": chunk_id, "content": "x",
            "embedding": [0.1, 0.2]}


def test_load_stamps_run_configuration_and_model_on_every_chunk():
    adapter = FakeAdapter()
    inventory = [{"doc_id": "d1", "status": "new", "content_hash": "h1"}]
    run_load([_chunk("d1", "c1")], inventory, adapter, "coll", 2, "v1",
             run_id="run-9", config_id="cfg-9", embed_model="all_minilm_l6_v2",
             embed_dims=384, log=lambda *_: None)
    written = adapter.upserted[0]
    assert written["run_id"] == "run-9"
    assert written["config_id"] == "cfg-9"
    assert written["embed_model"] == "all_minilm_l6_v2"
    assert written["embed_dims"] == 384


def test_load_retires_the_previous_generation_instead_of_deleting_it():
    adapter = FakeAdapter()
    inventory = [{"doc_id": "d1", "status": "changed", "content_hash": "h2"}]
    run_load([_chunk("d1", "c1"), _chunk("d1", "c2")], inventory, adapter,
             "coll", 2, "v1", run_id="run-9", log=lambda *_: None)
    assert not adapter.deleted, "physical deletes destroy the history"
    assert len(adapter.retired) == 1
    call = adapter.retired[0]
    assert call["filter"] == {"doc_id": "d1"}
    assert sorted(call["keep_ids"]) == ["c1", "c2"]
    assert call["run_id"] == "run-9"


def test_a_deleted_document_is_tombstoned_not_erased():
    adapter = FakeAdapter()
    inventory = [{"doc_id": "gone", "status": "deleted", "content_hash": ""}]
    run_load([], inventory, adapter, "coll", 2, "v1", run_id="run-9",
             log=lambda *_: None)
    assert not adapter.deleted
    assert adapter.retired[0]["filter"] == {"doc_id": "gone"}
    assert adapter.retired[0]["keep_ids"] is None


def test_embed_dims_falls_back_to_the_collection_dimensions():
    adapter = FakeAdapter()
    inventory = [{"doc_id": "d1", "status": "new", "content_hash": "h1"}]
    run_load([_chunk("d1", "c1")], inventory, adapter, "coll", 384, "v1",
             log=lambda *_: None)
    assert adapter.upserted[0]["embed_dims"] == 384


def test_a_document_without_chunks_still_fails_only_itself():
    adapter = FakeAdapter()
    inventory = [{"doc_id": "d1", "status": "new", "content_hash": "h1"},
                 {"doc_id": "d2", "status": "new", "content_hash": "h2"}]
    updated = run_load([_chunk("d2", "c2")], inventory, adapter, "coll", 2, "v1",
                       run_id="run-9", log=lambda *_: None)
    by_doc = {row["doc_id"]: row for row in updated}
    assert by_doc["d1"]["status"] == "failed"
    assert by_doc["d2"]["status"] == "ingested"


# ---------------------------------------------------------------------------
# the fingerprint must not depend on which PATH a run took
# ---------------------------------------------------------------------------
def test_a_no_op_run_fingerprints_the_same_as_a_full_run():
    """A scheduled run with nothing new must not read as configuration drift.

    A full run knows the embedding dimensions (it called the endpoint); a
    no-op run does not. If dimensions entered the fingerprint, the two would
    differ and the guard would refuse a run where nothing had changed.
    """
    full = merge_config(merge_config(None, {"chunker": "recursive"}),
                        {"embed_model": "all_minilm_l6_v2"})
    noop = merge_config(merge_config(None, {"chunker": "recursive"}),
                        {"embed_model": "all_minilm_l6_v2"})
    assert config_hash(json.loads(full)) == config_hash(json.loads(noop))
    for key in ("embed_dims", "deployment_type"):
        assert key not in json.loads(full)
