# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""SingleStore adapter tests.

The DDL, normalization and timestamp tests run everywhere. The live
round-trip runs only when the repo .env carries SINGLESTORE_* credentials and
uses a throwaway collection name, so it never touches a real one.

Every assertion about SingleStore's own behaviour here was probed against a
live cluster (memsql 9.0.34) first — the "there is no cosine metric" and
"ISO-8601 timestamps are rejected" cases are the two that would otherwise
have failed on the first real chunk.
"""
from pathlib import Path

import pytest

from rag_core.adapters import backends, driver_for, get_adapter
from rag_core.adapters.singlestore import (SENTINEL, SingleStoreAdapter,
                                           _ident, _normalize, _timestamp)
from rag_core.schema import Chunk, link_neighbors

REPO_ENV = Path(__file__).resolve().parents[3] / ".env"
SCRATCH = "rag_pytest_s2"


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------
def test_registry_returns_singlestore():
    assert isinstance(get_adapter("singlestore"), SingleStoreAdapter)
    assert isinstance(get_adapter(" SingleStore "), SingleStoreAdapter)
    with pytest.raises(LookupError):
        get_adapter("faiss")


def test_registry_lists_both_backends_with_labels_and_drivers():
    listed = dict(backends())
    assert set(listed) == {"pgvector", "singlestore"}
    assert listed["singlestore"] == "SingleStore"
    assert driver_for("singlestore") == "singlestoredb"
    assert driver_for("nope") == ""


def test_identifier_allowlist():
    assert _ident("rag_demo_v1") == "`rag_demo_v1`"
    for bad in ("x`; DROP TABLE y;--", "Robert'); --", "UPPER", "1starts_with_digit"):
        with pytest.raises(ValueError):
            _ident(bad)


# ---------------------------------------------------------------------------
# DDL contract
# ---------------------------------------------------------------------------
def test_ddl_carries_the_history_model():
    ddl = SingleStoreAdapter().ddl("rag_demo_v1", 384)
    assert "VECTOR(384) NOT NULL" in ddl
    assert f"valid_to         DATETIME(6)  NOT NULL DEFAULT '{SENTINEL}'" in ddl
    # the sentinel replaces UNIQUE NULLS NOT DISTINCT, which SingleStore lacks
    assert "UNIQUE KEY rag_demo_v1_live_uk (chunk_id, valid_to)" in ddl
    assert "SHARD KEY (chunk_id)" in ddl
    assert "retired_in_run" in ddl


def test_ddl_index_metric_is_dot_product_for_cosine():
    """SingleStore rejects metric_type COSINE (probed), so cosine is served by
    normalized vectors ranked with a dot product."""
    ddl = SingleStoreAdapter().ddl("rag_demo_v1", 384, metric="cosine")
    assert '"metric_type":"DOT_PRODUCT"' in ddl
    assert '"index_type":"HNSW_FLAT"' in ddl
    l2 = SingleStoreAdapter().ddl("rag_demo_v1", 8, metric="l2")
    assert '"metric_type":"EUCLIDEAN_DISTANCE"' in l2


def test_ddl_refuses_an_unsupported_metric():
    with pytest.raises(ValueError):
        SingleStoreAdapter().ddl("rag_demo_v1", 8, metric="jaccard")


def test_ddl_refuses_the_sparse_optin():
    with pytest.raises(NotImplementedError):
        SingleStoreAdapter().ddl("rag_demo_v1", 8, schema={"sparse": True})


def test_capabilities_admits_the_partial_index_gap():
    caps = SingleStoreAdapter().capabilities()
    assert caps["history"] and caps["as_of"] and caps["rollback"]
    assert caps["live_only_index"] is False   # the honest gap against pgvector
    assert caps["normalized_vectors"] is True
    assert caps["cutover"] == "rename"


# ---------------------------------------------------------------------------
# the two conversions SingleStore forced on us
# ---------------------------------------------------------------------------
def test_iso_timestamps_are_made_acceptable():
    """`2026-07-30T12:00:00Z` is rejected by SingleStore's DATE/TIME
    conversion, and that is exactly what the pipeline stamps."""
    assert _timestamp("2026-07-30T12:00:00Z") == "2026-07-30 12:00:00"
    assert _timestamp("2026-07-30T12:00:00+02:00") == "2026-07-30 12:00:00"
    assert _timestamp("2026-07-30 12:00:00.123456") == "2026-07-30 12:00:00.123456"
    assert _timestamp("") is None and _timestamp(None) is None


def test_normalization_makes_a_dot_product_a_cosine():
    unit = _normalize([3.0, 4.0])
    assert unit == pytest.approx([0.6, 0.8])
    # dot product of two unit vectors is their cosine similarity
    other = _normalize([4.0, 3.0])
    assert sum(a * b for a, b in zip(unit, other)) == pytest.approx(0.96)


def test_a_zero_vector_survives_normalization():
    assert _normalize([0.0, 0.0, 0.0]) == [0.0, 0.0, 0.0]


def test_the_tag_filter_uses_the_mysql_accessor():
    from rag_core.filters import compile_sql
    condition, params = compile_sql({"department": "legal"}, "mysql")
    assert condition == "JSON_EXTRACT_STRING(tags, 'department') = %s"
    assert params == ["legal"]
    postgres, _ = compile_sql({"department": "legal"})
    assert postgres == "tags->>'department' = %s"


# ---------------------------------------------------------------------------
# live round trip
# ---------------------------------------------------------------------------
def _make_chunks(doc_id: str, content_hash: str, texts: list,
                 version: str = "v1") -> list:
    chunks = [
        Chunk.build(doc_id, f"/docs/{doc_id}.txt", i, text, content_hash,
                    "plaintext", version, "2026-07-30T12:00:00Z",
                    tags={"department": "legal" if i % 2 == 0 else "hr"})
        for i, text in enumerate(texts)
    ]
    link_neighbors(chunks)
    for i, chunk in enumerate(chunks):
        base = [0.0] * 8
        base[i % 8] = 1.0
        chunk.embedding = base
        # a real chunker sets this; it is what makes a citation openable, so
        # the JSON round trip through the store matters
        chunk.span = {"page": i + 1, "start": 0, "end": len(chunk.content)}
    return chunks


@pytest.fixture(scope="module")
def live_adapter():
    if not REPO_ENV.is_file():
        pytest.skip("no repo .env with SINGLESTORE_* credentials")
    from rag_core.env import config_from_dotenv
    try:
        config = config_from_dotenv(str(REPO_ENV), backend="singlestore")
    except KeyError:
        pytest.skip("SINGLESTORE_* incomplete in .env")
    adapter = get_adapter("singlestore")
    try:
        adapter.connect(config)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"SingleStore not reachable: {type(exc).__name__}")
    yield adapter
    for name in (SCRATCH, SCRATCH + "_v2", SCRATCH + "_retired"):
        adapter.drop_collection(name)
    adapter.close()


def test_live_roundtrip(live_adapter):
    a = live_adapter
    a.drop_collection(SCRATCH)
    a.ensure_collection(SCRATCH, dims=8)
    a.ensure_collection(SCRATCH, dims=8)          # idempotent, index included
    assert a.dimensions(SCRATCH) == 8

    chunks = _make_chunks("docA", "hashv1", ["alpha text", "beta text", "gamma text"])
    assert a.upsert(SCRATCH, [c.__dict__ for c in chunks]) == 3
    assert a.count(SCRATCH) == 3

    a.upsert(SCRATCH, [c.__dict__ for c in chunks])   # idempotent
    assert a.count(SCRATCH) == 3

    query = [0.0] * 8
    query[0] = 1.0
    hits = a.search(SCRATCH, query, k=2)
    assert hits[0].chunk_id == chunks[0].chunk_id      # exact match wins
    assert hits[0].score > hits[1].score               # higher-is-better
    assert hits[0].record["content"] == "alpha text"
    assert hits[0].record["tags"]["department"] == "legal"
    assert hits[0].record["next_id"] == chunks[1].chunk_id
    assert hits[0].record["distance"] == pytest.approx(0.0, abs=1e-6)
    assert hits[0].record["span"]["start"] == 0        # JSON round-trips

    filtered = a.search(SCRATCH, query, k=5, filter={"department": "hr"})
    assert filtered and all(h.record["tags"]["department"] == "hr" for h in filtered)


def test_live_retire_keeps_the_history(live_adapter):
    a = live_adapter
    a.drop_collection(SCRATCH)
    a.ensure_collection(SCRATCH, dims=8)
    first = _make_chunks("docB", "hash1", ["one", "two"], "v1")
    a.upsert(SCRATCH, [c.__dict__ for c in first])

    second = _make_chunks("docB", "hash2", ["one changed"], "v2")
    for chunk in second:
        chunk.run_id = "run-1"      # what run_load stamps; rollback needs it
    a.upsert(SCRATCH, [c.__dict__ for c in second])
    retired = a.retire(SCRATCH, filter={"doc_id": "docB"},
                       keep_ids=[c.chunk_id for c in second], run_id="run-1")
    assert retired == 2
    assert a.count(SCRATCH) == 1                      # live slice
    assert a.count(SCRATCH, include_retired=True) == 3

    # retrieval never sees a retired generation
    query = [0.0] * 8
    query[0] = 1.0
    assert [h.record["content"] for h in a.search(SCRATCH, query, k=5)] \
        == ["one changed"]

    # ... but an as-of read does
    as_of = a.search(SCRATCH, query, k=5, as_of="2999-01-01 00:00:00")
    assert len(as_of) == 1                            # the future is the present
    assert a.count(SCRATCH, as_of="2999-01-01 00:00:00") == 1

    # rollback: what run-1 retired comes back, what run-1 wrote goes away
    assert a.restore(SCRATCH, "run-1") == 2
    live = {h.record["content"] for h in a.search(SCRATCH, query, k=9)}
    assert live == {"one", "two"}
    assert a.count(SCRATCH) == 2


def test_live_retrieval_model_reads_the_singlestore_collection(live_adapter):
    """The manifested model is a separate implementation of the same query.

    It carries its own SQL (it must run where rag_core cannot be imported), so
    the dialect, the sentinel and the normalization have to be right there too
    — a store the pipeline can load into but the model cannot read is not a
    supported backend.
    """
    a = live_adapter
    a.drop_collection(SCRATCH)
    a.ensure_collection(SCRATCH, dims=8)
    chunks = _make_chunks("docC", "hash1", ["alpha text", "beta text"])
    a.upsert(SCRATCH, [c.__dict__ for c in chunks])
    a.retire(SCRATCH, ids=[chunks[1].chunk_id], run_id="run-x")

    import retrieve_context
    from rag_core.env import config_from_dotenv

    original = (retrieve_context.BACKEND, retrieve_context.COLLECTION)
    retrieve_context.BACKEND = "singlestore"
    retrieve_context.COLLECTION = SCRATCH
    try:
        store = config_from_dotenv(str(REPO_ENV), backend="singlestore")
        query = [0.0] * 8
        query[0] = 1.0
        hits = retrieve_context._search(query, 5, None, store)
    finally:
        retrieve_context.BACKEND, retrieve_context.COLLECTION = original

    assert [h["content"] for h in hits] == ["alpha text"]   # retired one is gone
    assert hits[0]["distance"] == pytest.approx(0.0, abs=1e-6)
    assert hits[0]["score"] == pytest.approx(1.0, abs=1e-6)
    assert hits[0]["page"] == 1 and hits[0]["span_start"] == 0
    assert hits[0]["filename"] == "docC.txt"


def test_live_erasure_takes_the_retired_generations_too(live_adapter):
    """Erasure that leaves the previous generation behind has erased nothing."""
    a = live_adapter
    a.drop_collection(SCRATCH)
    a.ensure_collection(SCRATCH, dims=8)
    first = _make_chunks("docD", "hash1", ["one", "two"], "v1")
    a.upsert(SCRATCH, [c.__dict__ for c in first])
    second = _make_chunks("docD", "hash2", ["one changed"], "v2")
    a.upsert(SCRATCH, [c.__dict__ for c in second])
    a.retire(SCRATCH, keep_ids=[c.chunk_id for c in second],
             filter={"doc_id": "docD"}, run_id="run-2")
    keep = _make_chunks("docE", "hash1", ["untouched"], "v1")
    a.upsert(SCRATCH, [c.__dict__ for c in keep])
    assert a.count(SCRATCH, include_retired=True) == 4

    a.delete(SCRATCH, filter={"doc_id": "docD"})
    assert a.count(SCRATCH, filter={"doc_id": "docD"},
                   include_retired=True) == 0     # history gone as well
    assert a.count(SCRATCH, include_retired=True) == 1   # the other doc stands


def test_live_pruning_keeps_the_live_slice_intact(live_adapter):
    a = live_adapter
    a.drop_collection(SCRATCH)
    a.ensure_collection(SCRATCH, dims=8)
    first = _make_chunks("docF", "hash1", ["one", "two"], "v1")
    a.upsert(SCRATCH, [c.__dict__ for c in first])
    second = _make_chunks("docF", "hash2", ["one changed"], "v2")
    a.upsert(SCRATCH, [c.__dict__ for c in second])
    a.retire(SCRATCH, keep_ids=[c.chunk_id for c in second],
             filter={"doc_id": "docF"}, run_id="run-3")
    assert a.count(SCRATCH, include_retired=True) == 3

    # nothing is old enough to prune yet
    assert a.prune_history(SCRATCH, "2020-01-01 00:00:00") == 0
    # a cutoff in the future catches every retired row - and only those
    assert a.prune_history(SCRATCH, "2999-01-01 00:00:00", dry_run=True) == 2
    assert a.count(SCRATCH, include_retired=True) == 3      # dry run changed nothing
    assert a.prune_history(SCRATCH, "2999-01-01 00:00:00") == 2
    assert a.count(SCRATCH, include_retired=True) == 1
    assert a.count(SCRATCH) == 1                            # live slice untouched
    with pytest.raises(ValueError):
        a.prune_history(SCRATCH, "")


def test_live_cutover_rename(live_adapter):
    a = live_adapter
    a.drop_collection(SCRATCH + "_v2")
    a.drop_collection(SCRATCH + "_retired")
    a.ensure_collection(SCRATCH, dims=8)
    a.ensure_collection(SCRATCH + "_v2", dims=8)
    a.cutover(SCRATCH, SCRATCH + "_v2")
    collections = a.list_collections()
    assert SCRATCH in collections
    assert SCRATCH + "_retired" in collections
    assert SCRATCH + "_v2" not in collections
    with pytest.raises(ValueError):
        a.cutover(SCRATCH, "does_not_exist_v9")
