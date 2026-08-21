# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Erasure, the deleted-document policy and history retention.

Three different operations that all end in rows leaving the database, kept
apart deliberately: retire() is reversible and routine, delete() is erasure,
prune_history() is housekeeping. The tests pin which one each caller reaches
for — confusing them is how a "cleanup" quietly destroys the audit trail.
"""
import pytest

from rag_core.steps import (PURGE_COLUMNS, resolve_doc_ids, run_load,
                            run_purge)


class FakeAdapter:
    needs_flush = False

    def __init__(self, counts=None):
        self.upserted = []
        self.retired = []
        self.deleted = []
        self.pruned = []
        self._counts = counts or {}

    def ensure_collection(self, name, dims, metric="cosine", schema=None):
        pass

    def upsert(self, collection, records):
        self.upserted.extend(records)
        return len(records)

    def retire(self, collection, ids=None, filter=None, run_id=None, keep_ids=None):
        self.retired.append({"filter": filter, "run_id": run_id,
                             "keep_ids": keep_ids})
        return 1

    def delete(self, collection, ids=None, filter=None):
        self.deleted.append({"ids": ids, "filter": filter})
        return 1

    def prune_history(self, collection, before, dry_run=False):
        self.pruned.append(before)
        return 7

    def count(self, collection, filter=None, include_retired=False, as_of=None):
        if filter:
            return self._counts.get(filter.get("doc_id"), 0)
        return len(self.upserted)


LEDGER = [
    {"doc_id": "d1", "source_uri": "/docs/a.txt", "status": "ingested"},
    {"doc_id": "d2", "source_uri": "/docs/b.txt", "status": "ingested"},
]


# ---------------------------------------------------------------------------
# the deleted-document policy
# ---------------------------------------------------------------------------
def test_a_vanished_document_is_tombstoned_by_default():
    adapter = FakeAdapter()
    inventory = [{"doc_id": "gone", "status": "deleted", "content_hash": ""}]
    run_load([], inventory, adapter, "coll", 2, "v1", run_id="r1",
             log=lambda *_: None)
    assert adapter.retired and not adapter.deleted


def test_the_purge_policy_removes_the_chunks_instead():
    adapter = FakeAdapter()
    inventory = [{"doc_id": "gone", "status": "deleted", "content_hash": ""}]
    run_load([], inventory, adapter, "coll", 2, "v1", run_id="r1",
             deleted_policy="purge", log=lambda *_: None)
    assert adapter.deleted == [{"ids": None, "filter": {"doc_id": "gone"}}]
    assert not adapter.retired


def test_the_policy_never_applies_to_a_merely_changed_document():
    """A changed document must keep its history under either policy — only a
    document that VANISHED from the source is subject to the choice."""
    adapter = FakeAdapter()
    inventory = [{"doc_id": "d1", "status": "changed", "content_hash": "h2"}]
    chunk = {"doc_id": "d1", "chunk_id": "c1", "content": "x",
             "embedding": [0.1, 0.2]}
    run_load([chunk], inventory, adapter, "coll", 2, "v1", run_id="r1",
             deleted_policy="purge", log=lambda *_: None)
    assert adapter.retired and not adapter.deleted


def test_an_unknown_policy_is_refused_before_anything_is_written():
    adapter = FakeAdapter()
    with pytest.raises(ValueError, match="deleted_policy"):
        run_load([], [], adapter, "coll", 2, "v1", deleted_policy="destroy",
                 log=lambda *_: None)
    assert not adapter.deleted and not adapter.upserted


# ---------------------------------------------------------------------------
# retention
# ---------------------------------------------------------------------------
def test_retention_is_off_unless_asked_for():
    adapter = FakeAdapter()
    run_load([], [], adapter, "coll", 2, "v1", log=lambda *_: None)
    assert not adapter.pruned


def test_retention_prunes_with_a_cutoff_in_the_past():
    adapter = FakeAdapter()
    run_load([], [], adapter, "coll", 2, "v1", retain_days=30,
             log=lambda *_: None)
    assert len(adapter.pruned) == 1
    assert adapter.pruned[0] < "9999"          # an actual timestamp, not a flag


def test_a_failing_prune_never_fails_a_good_load():
    """Housekeeping is not worth losing a successful ingestion over."""
    class Grumpy(FakeAdapter):
        def prune_history(self, collection, before, dry_run=False):
            raise RuntimeError("no rights to delete")

    adapter = Grumpy()
    messages = []
    inventory = [{"doc_id": "d1", "status": "new", "content_hash": "h1"}]
    chunk = {"doc_id": "d1", "chunk_id": "c1", "content": "x",
             "embedding": [0.1, 0.2]}
    updated = run_load([chunk], inventory, adapter, "coll", 2, "v1",
                       retain_days=7, log=messages.append)
    assert updated[0]["status"] == "ingested"
    assert any("pruning skipped" in m for m in messages)


def test_neither_policy_belongs_in_the_configuration_fingerprint():
    """The drift guard exists to catch a change in how chunks are PRODUCED.
    Deletion policy and retention change neither the chunks nor the vectors,
    so putting them in the fingerprint would demand a pipeline-version bump
    for a housekeeping setting — and refuse the run until it got one."""
    import inspect

    from rag_core import steps

    source = inspect.getsource(steps.run_load)
    body = source.split("adapter.ensure_collection", 1)[1]
    for setting in ("deleted_policy", "retain_days"):
        assert f'"{setting}"' not in body and f"'{setting}'" not in body, (
            f"{setting} looks like it is being merged into the configuration")


# ---------------------------------------------------------------------------
# targeted erasure
# ---------------------------------------------------------------------------
def test_a_dry_run_changes_nothing_and_says_what_it_would_do():
    adapter = FakeAdapter({"d1": 12})
    report, remaining = run_purge(["d1"], LEDGER, adapter, "coll",
                                  dry_run=True, log=lambda *_: None)
    assert not adapter.deleted
    assert remaining == LEDGER
    assert report[0]["chunks_removed"] == 12
    assert report[0]["outcome"] == "would purge"
    assert set(report[0]) == set(PURGE_COLUMNS)


def test_purging_removes_the_chunks_and_forgets_the_document():
    """Both, or the state is worse than before: chunks without a ledger row
    come back on the next run, a ledger row without chunks never does."""
    adapter = FakeAdapter({"d1": 12})
    report, remaining = run_purge(["d1"], LEDGER, adapter, "coll",
                                  dry_run=False, log=lambda *_: None)
    assert adapter.deleted == [{"ids": None, "filter": {"doc_id": "d1"}}]
    assert [row["doc_id"] for row in remaining] == ["d2"]
    assert report[0]["outcome"] == "purged"
    assert report[0]["ledger_removed"] == "yes"


def test_erasure_counts_retired_generations_too():
    """count() must include retired rows or the report understates what was
    destroyed — the previous generations are exactly what erasure is for."""
    seen = {}

    class Counting(FakeAdapter):
        def count(self, collection, filter=None, include_retired=False,
                  as_of=None):
            seen["include_retired"] = include_retired
            return 5

    run_purge(["d1"], LEDGER, Counting(), "coll", dry_run=True,
              log=lambda *_: None)
    assert seen["include_retired"] is True


def test_a_document_not_in_the_ledger_is_still_purged_but_flagged():
    adapter = FakeAdapter({"stray": 3})
    report, _ = run_purge(["stray"], LEDGER, adapter, "coll", dry_run=False,
                          log=lambda *_: None)
    assert report[0]["outcome"] == "purged"
    assert "not in the ledger" in report[0]["error_text"]


def test_purging_nothing_is_refused():
    for empty in ([], ["", "  "]):
        with pytest.raises(ValueError, match="whole collection"):
            run_purge(empty, LEDGER, FakeAdapter(), "coll", log=lambda *_: None)


# ---------------------------------------------------------------------------
# naming the documents to erase
# ---------------------------------------------------------------------------
def test_a_document_can_be_named_the_way_a_person_knows_it():
    """doc_id is a hash — nobody types one. The ledger maps a path or a file
    name onto it."""
    for selector in ("/docs/a.txt", "a.txt", "d1"):
        ids, unmatched = resolve_doc_ids([selector], LEDGER)
        assert ids == ["d1"] and not unmatched


def test_an_unknown_selector_is_reported_not_ignored():
    ids, unmatched = resolve_doc_ids(["a.txt", "never-existed.pdf"], LEDGER)
    assert ids == ["d1"]
    assert unmatched == ["never-existed.pdf"]


def test_an_ambiguous_file_name_resolves_to_every_match():
    """Same file name in two folders: erasing one and silently keeping the
    other would be the worst outcome, so both are listed and previewed."""
    ledger = LEDGER + [{"doc_id": "d3", "source_uri": "/archive/a.txt"}]
    ids, _ = resolve_doc_ids(["a.txt"], ledger)
    assert sorted(ids) == ["d1", "d3"]


def test_the_same_document_named_twice_is_purged_once():
    ids, _ = resolve_doc_ids(["a.txt", "/docs/a.txt", "d1"], LEDGER)
    assert ids == ["d1"]


def test_one_failing_document_does_not_stop_the_others():
    class Picky(FakeAdapter):
        def delete(self, collection, ids=None, filter=None):
            if filter["doc_id"] == "d1":
                raise RuntimeError("locked")
            return super().delete(collection, ids, filter)

    adapter = Picky({"d1": 1, "d2": 2})
    report, remaining = run_purge(["d1", "d2"], LEDGER, adapter, "coll",
                                  dry_run=False, log=lambda *_: None)
    outcomes = {row["doc_id"]: row["outcome"] for row in report}
    assert outcomes == {"d1": "failed", "d2": "purged"}
    # a failed erasure must not have its ledger row dropped, or the document
    # becomes invisible while its chunks are still being retrieved
    assert [row["doc_id"] for row in remaining] == ["d1"]
