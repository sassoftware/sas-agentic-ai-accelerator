# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""End-to-end pipeline test: List -> Extract -> Chunk -> Embed(fake) -> Load(live pgvector).

Covers the §2 contracts: streaming-hash diff, per-doc failure isolation,
idempotent incremental re-run, document change, document deletion, and the
upsert-first-then-delete-stale ordering.
"""
import hashlib
from pathlib import Path

import pytest

from rag_core.extractors import ExtractorRegistry
from rag_core.steps import (config_hash, merge_ledger, run_chunk, run_embed,
                            run_extract, run_list, run_load)

REPO_ENV = Path(__file__).resolve().parents[3] / ".env"
COLLECTION = "rag_pytest_pipeline"


class FakeEmbeddingClient:
    """Deterministic 8-dim embeddings; content-sensitive so search is meaningful."""

    def __init__(self):
        self.usage = {"calls": 0, "run_time": 0.0, "tokens": 0}

    def embed(self, text, mode="document"):
        self.usage["calls"] += 1
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [b / 255.0 for b in digest[:8]]


@pytest.fixture()
def corpus(tmp_path):
    (tmp_path / "policy.md").write_text(
        "# Vacation Policy\n\nEmployees receive 30 days.\n\n"
        "## Carry-over\n\nUp to 5 days carry over to Q1.\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text(
        "The retrieval latency budget is 500 milliseconds.", encoding="utf-8")
    (tmp_path / "broken.pdf").write_bytes(b"%PDF-not-really-a-pdf")
    return tmp_path


def _run_pipeline(source, ledger, adapter, run_id):
    registry = ExtractorRegistry()
    client = FakeEmbeddingClient()
    chash = config_hash({"chunker": "recursive", "tokens": 217})
    inventory = run_list(str(source), ledger, run_id, "v1", chash, log=lambda *_: None)
    elements, inventory = run_extract(inventory, registry, log=lambda *_: None)
    chunks = run_chunk(elements, inventory, "recursive", 256, "v1", log=lambda *_: None)
    embedded, failed = run_embed(chunks, client, max_workers=2, log=lambda *_: None)
    assert not failed
    inventory = run_load(embedded, inventory, adapter, COLLECTION, 8, "v1",
                         log=lambda *_: None)
    return merge_ledger(ledger, inventory), client


@pytest.fixture(scope="module")
def adapter():
    if not REPO_ENV.is_file():
        pytest.skip("no repo .env with RAGSTORE_* credentials")
    from rag_core.adapters import get_adapter
    from rag_core.env import config_from_dotenv
    try:
        config = config_from_dotenv(str(REPO_ENV))
    except KeyError:
        pytest.skip("RAGSTORE_* incomplete in .env")
    a = get_adapter("pgvector")
    try:
        a.connect(config)
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"pgvector not reachable: {type(exc).__name__}")
    a.drop_collection(COLLECTION)
    yield a
    a.drop_collection(COLLECTION)
    a.close()


def test_full_pipeline_incremental(adapter, corpus):
    # --- first run: 2 good docs ingested, 1 corrupt doc quarantined -------
    ledger, client = _run_pipeline(corpus, [], adapter, "run1")
    by_status = {}
    for row in ledger:
        by_status.setdefault(row["status"], []).append(row["source_uri"])
    assert len(by_status.get("ingested", [])) == 2
    assert len(by_status.get("failed", [])) == 1
    assert "broken.pdf" in by_status["failed"][0]
    failed_row = next(r for r in ledger if r["status"] == "failed")
    assert failed_row["error_text"]                       # reason recorded
    first_calls = client.usage["calls"]
    assert adapter.count(COLLECTION) > 0

    # --- retrieval sanity: a chunk's own embedding retrieves that chunk ---
    # (the fake embedder is an exact content hash, so self-retrieval is the
    # deterministic check; semantic matching is the real container's job)
    probe = FakeEmbeddingClient()
    policy_doc = next(r for r in ledger if "policy.md" in r["source_uri"])
    stored = adapter.search(COLLECTION, probe.embed("anything"), k=10,
                            filter={"doc_id": policy_doc["doc_id"]})
    target = stored[0].record["content"]
    hits = adapter.search(COLLECTION, probe.embed(target), k=1)
    assert hits[0].record["content"] == target
    assert "30 days" in " ".join(h.record["content"] for h in stored)

    # --- second run, nothing changed: no re-embedding, ledger stable ------
    count_before = adapter.count(COLLECTION)
    ledger2, client2 = _run_pipeline(corpus, ledger, adapter, "run2")
    assert client2.usage["calls"] == 0                    # unchanged -> no calls
    assert adapter.count(COLLECTION) == count_before
    assert sum(1 for r in ledger2 if r["status"] == "unchanged") == 2

    # --- change one doc: only it re-embeds; stale chunks removed ----------
    (corpus / "notes.txt").write_text(
        "The retrieval latency budget is 500 milliseconds. Now with hybrid.",
        encoding="utf-8")
    ledger3, client3 = _run_pipeline(corpus, ledger2, adapter, "run3")
    assert 0 < client3.usage["calls"] < first_calls       # only the changed doc
    changed = next(r for r in ledger3 if "notes.txt" in r["source_uri"])
    assert changed["status"] == "ingested"
    hits = adapter.search(COLLECTION, FakeEmbeddingClient().embed(
        "The retrieval latency budget is 500 milliseconds. Now with hybrid."), k=2)
    assert any("hybrid" in h.record["content"] for h in hits)
    # old version's chunks are gone
    notes_chunks = adapter.count(COLLECTION, filter={"doc_id": changed["doc_id"]})
    assert notes_chunks == changed["chunk_count"]

    # --- delete a doc: tombstoned and removed from the store --------------
    (corpus / "policy.md").unlink()
    ledger4, _ = _run_pipeline(corpus, ledger3, adapter, "run4")
    gone = next(r for r in ledger4 if "policy.md" in r["source_uri"])
    assert gone["status"] == "deleted"
    assert adapter.count(COLLECTION, filter={"doc_id": gone["doc_id"]}) == 0


def test_config_hash_is_order_insensitive():
    a = config_hash({"chunker": "recursive", "k": 5})
    b = config_hash({"k": 5, "chunker": "recursive"})
    assert a == b
    assert a != config_hash({"k": 6, "chunker": "recursive"})
