# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Weaviate adapter tests.

Everything runs against an in-memory Weaviate (tests/fake_weaviate.py) that
answers the REST and GraphQL requests the adapter sends and behaves like the
server where the adapter depends on it - a batch replaces rather than merges,
undeclared properties are refused, IsNull needs indexNullState. The contract
tests are the ones the pgvector and SingleStore live suites run, so the three
backends answer the same questions the same way.

The live round trip runs only when WEAVIATE_HOST is set (WEAVIATE_PORT,
WEAVIATE_SSLMODE and WEAVIATE_RAG_PW optional) and uses a throwaway
collection, so it never touches a real one. Not yet run against a server.
"""
import json
import os

import pytest

from fake_weaviate import FakeWeaviate
from rag_core.adapters import backends, driver_for, get_adapter
from rag_core.adapters import weaviate as wv
from rag_core.adapters.weaviate import WeaviateAdapter, _ident, gql, live_id
from rag_core.schema import Chunk, link_neighbors

SCRATCH = "rag_pytest_wv"
CONFIG = {"host": "weaviate.example", "port": 8080, "dbname": "unused",
          "user": "rag", "password": "key-123", "sslmode": "disable"}


@pytest.fixture
def server():
    return FakeWeaviate()


@pytest.fixture
def adapter(server):
    a = WeaviateAdapter(session=server)
    a.connect(CONFIG)
    return a


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
        chunk.span = {"page": i + 1, "start": 0, "end": len(chunk.content)}
    return chunks


QUERY = [1.0] + [0.0] * 7


# ---------------------------------------------------------------------------
# registry, names, connection
# ---------------------------------------------------------------------------
def test_registry_returns_weaviate():
    assert isinstance(get_adapter("weaviate"), WeaviateAdapter)
    assert isinstance(get_adapter(" Weaviate "), WeaviateAdapter)
    assert dict(backends())["weaviate"] == "Weaviate"
    # REST over requests: nothing to install beyond what compute has
    assert driver_for("weaviate") == "requests"


def test_collection_names_map_one_to_one():
    assert _ident("rag_demo_v1") == "Rag_demo_v1"
    for bad in ("Rag_demo", "x; DROP", "1starts_with_digit", "a-b", ""):
        with pytest.raises(ValueError):
            _ident(bad)


def test_connect_checks_the_version_floor():
    for version in ("1.33.0", "1.33.2", "1.34.0-rc.1", "2.0.0"):
        WeaviateAdapter(session=FakeWeaviate(version=version)).connect(CONFIG)
    for version in ("1.32.9", "1.26.1", ""):
        with pytest.raises(RuntimeError, match="1.33"):
            WeaviateAdapter(session=FakeWeaviate(version=version)).connect(CONFIG)


def test_connect_sends_the_key_and_follows_sslmode(server):
    a = WeaviateAdapter(session=server)
    a.connect(CONFIG)
    assert server.calls[-1][1] == "http://weaviate.example:8080/v1/meta"
    assert server.headers[-1]["Authorization"] == "Bearer key-123"
    for sslmode in ("require", "prefer", "", "verify-full"):
        a.connect(dict(CONFIG, sslmode=sslmode))
        assert server.calls[-1][1].startswith("https://")


def test_an_unconnected_adapter_says_so():
    with pytest.raises(RuntimeError, match="connect"):
        WeaviateAdapter().count(SCRATCH)


def test_capabilities():
    caps = WeaviateAdapter().capabilities()
    assert caps["history"] and caps["as_of"] and caps["rollback"]   # lineage
    assert caps["run_history"] is False          # no SQL for rag_runs & co
    assert caps["hybrid"] is False               # waits for OQ15
    assert caps["cutover"] == "alias" and caps["alias"] is True
    assert caps["live_only_index"] is False


# ---------------------------------------------------------------------------
# the DDL artifact
# ---------------------------------------------------------------------------
def test_ddl_is_the_create_collection_json():
    spec = json.loads(WeaviateAdapter().ddl("rag_demo_v1", 384))
    assert spec["class"] == "Rag_demo_v1"
    assert spec["vectorizer"] == "none"
    assert spec["vectorIndexConfig"]["distance"] == "cosine"
    assert spec["invertedIndexConfig"]["indexNullState"] is True
    assert "dims=384" in spec["description"]
    props = {p["name"]: p for p in spec["properties"]}
    # exact matching ids: `word` would let doc_id "a-b" match "a"
    for name in ("chunk_id", "doc_id", "source_uri", "run_id", "retired_in_run"):
        assert props[name]["tokenization"] == "field"
        assert props[name]["indexSearchable"] is False
    # BM25-ready for hybrid, later
    assert props["content"]["tokenization"] == "word"
    assert props["content"]["indexSearchable"] is True
    assert props["valid_to"]["dataType"] == ["date"]
    assert props["chunk_index"]["dataType"] == ["int"]
    assert props["span"]["indexFilterable"] is False


def test_ddl_metrics():
    assert json.loads(WeaviateAdapter().ddl("a", 8, metric="l2"))[
        "vectorIndexConfig"]["distance"] == "l2-squared"
    assert json.loads(WeaviateAdapter().ddl("a", 8, metric="ip"))[
        "vectorIndexConfig"]["distance"] == "dot"
    with pytest.raises(ValueError):
        WeaviateAdapter().ddl("a", 8, metric="jaccard")


def test_graphql_values_are_escaped_and_operators_checked():
    rendered = gql({"path": ["doc_id"], "operator": "Equal",
                    "valueText": '"} } DROP { "'})
    assert rendered == ('{path: ["doc_id"], operator: Equal, '
                        'valueText: "\\"} } DROP { \\""}')
    with pytest.raises(ValueError):
        gql({"operator": "Equal) { x"})


# ---------------------------------------------------------------------------
# the contract the other backends' live suites prove
# ---------------------------------------------------------------------------
def test_roundtrip(adapter):
    a = adapter
    a.ensure_collection(SCRATCH, dims=8)
    a.ensure_collection(SCRATCH, dims=8)                 # idempotent
    assert a.dimensions(SCRATCH) == 8
    assert SCRATCH in a.list_collections()

    chunks = _make_chunks("docA", "hashv1", ["alpha text", "beta text", "gamma text"])
    assert a.upsert(SCRATCH, [c.__dict__ for c in chunks]) == 3
    assert a.count(SCRATCH) == 3
    a.upsert(SCRATCH, [c.__dict__ for c in chunks])      # idempotent
    assert a.count(SCRATCH, include_retired=True) == 3

    hits = a.search(SCRATCH, QUERY, k=2)
    assert hits[0].chunk_id == chunks[0].chunk_id
    assert hits[0].score > hits[1].score
    assert hits[0].record["content"] == "alpha text"
    assert hits[0].record["tags"]["department"] == "legal"
    assert hits[0].record["next_id"] == chunks[1].chunk_id
    assert hits[0].record["distance"] == pytest.approx(0.0, abs=1e-6)
    assert hits[0].record["span"]["start"] == 0
    assert hits[0].record["chunk_index"] == 0

    filtered = a.search(SCRATCH, QUERY, k=5, filter={"department": "hr"})
    assert filtered and all(h.record["tags"]["department"] == "hr" for h in filtered)
    by_doc = a.search(SCRATCH, QUERY, k=5, filter={"doc_id": "docA",
                                                   "chunk_index": {"$gte": 1}})
    assert sorted(h.record["chunk_index"] for h in by_doc) == [1, 2]


def test_retrieval_returns_everything_when_k_exceeds_the_collection(adapter):
    a = adapter
    a.ensure_collection(SCRATCH, dims=8)
    chunks = _make_chunks("docK", "hash1", ["one", "two", "three", "four", "five"])
    a.upsert(SCRATCH, [c.__dict__ for c in chunks])
    for k in (1, 2, 3, 4, 5, 6, 9, 20):
        assert len(a.search(SCRATCH, QUERY, k=k)) == min(k, 5)


def test_enrichment_columns_are_created_typed_and_written(adapter, server):
    a = adapter
    a.ensure_collection(SCRATCH, dims=8)
    assert a.attribute_columns(SCRATCH) == []
    delta = a.sync_attributes(SCRATCH, {"department": "string",
                                        "confidence": "decimal"})
    assert delta == {"added": ["confidence", "department", "enrich_version"],
                     "kept": []}
    props = {p["name"]: p for p in server.classes["Rag_pytest_wv"]["properties"]}
    assert props["confidence"]["dataType"] == ["number"]
    assert props["department"]["dataType"] == ["text"]
    assert a.sync_attributes(SCRATCH, {"department": "string"})["added"] == []

    chunks = _make_chunks("docA", "hashv1", ["alpha text", "beta text"])
    records = []
    for index, chunk in enumerate(chunks):
        record = dict(chunk.__dict__)
        record["department"] = "legal" if index == 0 else "hr"
        record["confidence"] = 0.75 if index == 0 else None
        record["enrich_version"] = "mm-1@v-2"
        records.append(record)
    assert a.upsert(SCRATCH, records) == 2
    hit = a.search(SCRATCH, QUERY, k=1)[0]
    assert hit.record["department"] == "legal"
    assert hit.record["confidence"] == pytest.approx(0.75)
    other = [h for h in a.search(SCRATCH, QUERY, k=2)
             if h.record["department"] == "hr"][0]
    assert other.record["confidence"] is None


def test_the_flattened_tags_are_not_enrichment_columns(adapter):
    a = adapter
    a.ensure_collection(SCRATCH, dims=8)
    a.upsert(SCRATCH, [c.__dict__ for c in _make_chunks("d", "h", ["x"])])
    assert "tag_department" in a._property_names(SCRATCH)
    assert a.attribute_columns(SCRATCH) == []
    assert a.sync_attributes(SCRATCH, {})["kept"] == []


def test_a_column_no_longer_produced_is_kept_and_survives_a_rewrite(adapter):
    """A Weaviate batch REPLACES the object. Re-loading a chunk without the
    column an earlier prompt filled must not erase that column - the promise
    `_columns_for` makes on the SQL backends."""
    a = adapter
    a.ensure_collection(SCRATCH, dims=8)
    a.sync_attributes(SCRATCH, {"department": "string", "confidence": "decimal"})
    chunk = _make_chunks("docA", "hashv1", ["alpha text"])[0]
    first = dict(chunk.__dict__, department="legal", confidence=0.5)
    a.upsert(SCRATCH, [first])
    born = a.search(SCRATCH, QUERY, k=1)[0]

    delta = a.sync_attributes(SCRATCH, {"department": "string"})
    assert delta["kept"] == ["confidence"]
    a.upsert(SCRATCH, [dict(chunk.__dict__, department="hr")])
    hit = a.search(SCRATCH, QUERY, k=1)[0]
    assert hit.record["department"] == "hr"                     # rewritten
    assert hit.record["confidence"] == pytest.approx(0.5)       # kept
    assert a.count(SCRATCH, include_retired=True) == 1
    stored = a._get(SCRATCH, None, ["valid_from"])[0]
    assert stored["valid_from"]                                 # generation start kept
    assert born.chunk_id == hit.chunk_id


def test_a_column_added_later_is_not_backfilled(adapter):
    a = adapter
    a.ensure_collection(SCRATCH, dims=8)
    a.upsert(SCRATCH, [c.__dict__ for c in _make_chunks("docA", "h", ["early"])])
    assert a.sync_attributes(SCRATCH, {"tone": "string"})["added"] == [
        "enrich_version", "tone"]
    assert a.search(SCRATCH, QUERY, k=1)[0].record["tone"] is None


def test_a_refused_column_name_never_reaches_the_server(adapter, server):
    a = adapter
    a.ensure_collection(SCRATCH, dims=8)
    before = len(server.classes["Rag_pytest_wv"]["properties"])
    for bad in ("drop table x", "tone}; x", "1tone", "a-b", "té", "tag_owner"):
        with pytest.raises(ValueError):
            a.sync_attributes(SCRATCH, {"tone": "string", bad: "string"})
    assert len(server.classes["Rag_pytest_wv"]["properties"]) == before


def test_an_uppercase_output_name_still_carries_its_value(adapter):
    a = adapter
    a.ensure_collection(SCRATCH, dims=8)
    assert a.sync_attributes(SCRATCH, {"Department": "string"})["added"] == [
        "department", "enrich_version"]
    chunk = _make_chunks("docA", "hashv1", ["alpha text"])[0]
    a.upsert(SCRATCH, [dict(chunk.__dict__, department="legal")])
    assert a.search(SCRATCH, QUERY, k=1)[0].record["department"] == "legal"


def test_retire_keeps_the_history(adapter):
    a = adapter
    a.ensure_collection(SCRATCH, dims=8)
    first = _make_chunks("docB", "hash1", ["one", "two"], "v1")
    a.upsert(SCRATCH, [c.__dict__ for c in first])
    second = _make_chunks("docB", "hash2", ["one changed"], "v2")
    for chunk in second:
        chunk.run_id = "run-1"
    a.upsert(SCRATCH, [c.__dict__ for c in second])
    retired = a.retire(SCRATCH, filter={"doc_id": "docB"},
                       keep_ids=[c.chunk_id for c in second], run_id="run-1")
    assert retired == 2
    assert a.count(SCRATCH) == 1
    assert a.count(SCRATCH, include_retired=True) == 3
    assert [h.record["content"] for h in a.search(SCRATCH, QUERY, k=5)] \
        == ["one changed"]

    assert len(a.search(SCRATCH, QUERY, k=5, as_of="2999-01-01 00:00:00")) == 1
    assert a.count(SCRATCH, as_of="2999-01-01T00:00:00Z") == 1
    assert a.count(SCRATCH, as_of="2000-01-01 00:00:00") == 0

    assert a.restore(SCRATCH, "run-1") == 2
    live = {h.record["content"] for h in a.search(SCRATCH, QUERY, k=9)}
    assert live == {"one", "two"}
    assert a.count(SCRATCH) == 2


def test_a_retired_generation_frees_the_live_id(adapter, server):
    """The live chunk sits at uuid5(chunk_id); retiring moves it, so the same
    chunk can be loaded live again while its old generation stays history."""
    a = adapter
    a.ensure_collection(SCRATCH, dims=8)
    chunk = _make_chunks("docC", "h", ["same text"])[0]
    a.upsert(SCRATCH, [chunk.__dict__])
    assert live_id(chunk.chunk_id) in server.objects["Rag_pytest_wv"]
    a.retire(SCRATCH, ids=[chunk.chunk_id], run_id="run-a")
    assert live_id(chunk.chunk_id) not in server.objects["Rag_pytest_wv"]
    a.upsert(SCRATCH, [chunk.__dict__])
    assert a.count(SCRATCH) == 1
    assert a.count(SCRATCH, include_retired=True) == 2


def test_an_interrupted_retire_loses_nothing_and_finishes_on_retry(
        adapter, monkeypatch):
    """No transactions: copy first, delete second. A failure in between
    leaves the live chunk live (retrieval unchanged), and repeating the call
    completes it without a second copy."""
    a = adapter
    a.ensure_collection(SCRATCH, dims=8)
    a.upsert(SCRATCH, [c.__dict__ for c in _make_chunks("docD", "h", ["x", "y"])])
    real = a._delete_ids
    monkeypatch.setattr(a, "_delete_ids", lambda *args: (_ for _ in ()).throw(
        RuntimeError("connection reset")))
    with pytest.raises(RuntimeError):
        a.retire(SCRATCH, filter={"doc_id": "docD"}, run_id="run-z")
    assert a.count(SCRATCH) == 2                         # still live
    assert len(a.search(SCRATCH, QUERY, k=9)) == 2       # retrieval unchanged
    monkeypatch.setattr(a, "_delete_ids", real)
    assert a.retire(SCRATCH, filter={"doc_id": "docD"}, run_id="run-z") == 2
    assert a.count(SCRATCH) == 0
    assert a.count(SCRATCH, include_retired=True) == 2   # one copy each


def test_retire_refuses_the_whole_collection(adapter):
    adapter.ensure_collection(SCRATCH, dims=8)
    with pytest.raises(ValueError):
        adapter.retire(SCRATCH)


def test_erasure_takes_the_retired_generations_too(adapter):
    a = adapter
    a.ensure_collection(SCRATCH, dims=8)
    a.upsert(SCRATCH, [c.__dict__ for c in _make_chunks("docD", "hash1", ["one", "two"])])
    second = _make_chunks("docD", "hash2", ["one changed"], "v2")
    a.upsert(SCRATCH, [c.__dict__ for c in second])
    a.retire(SCRATCH, keep_ids=[c.chunk_id for c in second],
             filter={"doc_id": "docD"}, run_id="run-2")
    a.upsert(SCRATCH, [c.__dict__ for c in _make_chunks("docE", "hash1", ["untouched"])])
    assert a.count(SCRATCH, include_retired=True) == 4
    assert a.delete(SCRATCH, filter={"doc_id": "docD"}) == 3
    assert a.count(SCRATCH, filter={"doc_id": "docD"}, include_retired=True) == 0
    assert a.count(SCRATCH, include_retired=True) == 1
    with pytest.raises(ValueError):
        a.delete(SCRATCH)


def test_delete_repeats_past_the_servers_limit():
    server = FakeWeaviate(delete_limit=2)
    a = WeaviateAdapter(session=server)
    a.connect(CONFIG)
    a.ensure_collection(SCRATCH, dims=8)
    a.upsert(SCRATCH, [c.__dict__ for c in
                       _make_chunks("docL", "h", ["a", "b", "c", "d", "e"])])
    assert a.delete(SCRATCH, filter={"doc_id": "docL"}) == 5
    assert a.count(SCRATCH, include_retired=True) == 0


def test_pruning_keeps_the_live_slice_intact(adapter):
    a = adapter
    a.ensure_collection(SCRATCH, dims=8)
    a.upsert(SCRATCH, [c.__dict__ for c in _make_chunks("docF", "hash1", ["one", "two"])])
    second = _make_chunks("docF", "hash2", ["one changed"], "v2")
    a.upsert(SCRATCH, [c.__dict__ for c in second])
    a.retire(SCRATCH, keep_ids=[c.chunk_id for c in second],
             filter={"doc_id": "docF"}, run_id="run-3")
    assert a.prune_history(SCRATCH, "2020-01-01 00:00:00") == 0
    assert a.prune_history(SCRATCH, "2999-01-01 00:00:00", dry_run=True) == 2
    assert a.count(SCRATCH, include_retired=True) == 3
    assert a.prune_history(SCRATCH, "2999-01-01 00:00:00") == 2
    assert a.count(SCRATCH, include_retired=True) == 1
    assert a.count(SCRATCH) == 1
    with pytest.raises(ValueError):
        a.prune_history(SCRATCH, "")


def test_a_read_refuses_to_stop_at_the_result_cap(adapter, monkeypatch):
    """Weaviate answers at most QUERY_MAXIMUM_RESULTS objects per query; a
    retire that handled only those would leave the rest live."""
    a = adapter
    monkeypatch.setattr(wv, "_PAGE", 2)
    monkeypatch.setattr(wv, "_MAX_RESULTS", 4)
    a.ensure_collection(SCRATCH, dims=8)
    a.upsert(SCRATCH, [c.__dict__ for c in
                       _make_chunks("docM", "h", ["a", "b", "c", "d", "e"])])
    with pytest.raises(RuntimeError, match="narrow the filter"):
        a.retire(SCRATCH, filter={"doc_id": "docM"}, run_id="r")
    assert a.count(SCRATCH) == 5                         # nothing half-done


def test_a_tag_no_chunk_carries_matches_nothing(adapter):
    """SQL reads a missing tag as NULL and no row matches it; Weaviate would
    refuse the query for an unknown property instead."""
    a = adapter
    a.ensure_collection(SCRATCH, dims=8)
    a.upsert(SCRATCH, [c.__dict__ for c in _make_chunks("docN", "h", ["a", "b"])])
    assert a.search(SCRATCH, QUERY, k=5, filter={"region": "emea"}) == []
    assert a.count(SCRATCH, filter={"region": {"$ne": "emea"}}) == 0
    either = a.search(SCRATCH, QUERY, k=5,
                      filter={"$or": [{"region": "emea"}, {"doc_id": "docN"}]})
    assert len(either) == 2
    assert a.retire(SCRATCH, filter={"region": "emea"}, run_id="r") == 0
    assert a.delete(SCRATCH, filter={"region": "emea"}) == 0


def test_search_refuses_what_it_cannot_do(adapter):
    adapter.ensure_collection(SCRATCH, dims=8)
    with pytest.raises(NotImplementedError):
        adapter.search(SCRATCH, QUERY, mode="hybrid", query_text="x")
    with pytest.raises(ValueError):
        adapter.search(SCRATCH, QUERY, raw_filter="doc_id = 'x'")
    with pytest.raises(RuntimeError, match="does not exist"):
        adapter.search("rag_missing", QUERY)


def test_drop_and_recreate_forgets_the_columns(adapter):
    a = adapter
    a.ensure_collection(SCRATCH, dims=8)
    a.sync_attributes(SCRATCH, {"tone": "string"})
    assert "tone" in a.attribute_columns(SCRATCH)
    a.drop_collection(SCRATCH)
    a.drop_collection(SCRATCH)                           # absent is fine
    a.ensure_collection(SCRATCH, dims=8)
    assert a.attribute_columns(SCRATCH) == []
    a.upsert(SCRATCH, [c.__dict__ for c in _make_chunks("d", "h", ["x"])])
    assert a.count(SCRATCH) == 1


def test_dimensions_of_a_collection_made_elsewhere_come_from_a_vector(adapter, server):
    a = adapter
    a.ensure_collection(SCRATCH, dims=8)
    a.upsert(SCRATCH, [c.__dict__ for c in _make_chunks("d", "h", ["x"])])
    server.classes["Rag_pytest_wv"]["description"] = "made by hand"
    a._forget_attributes(SCRATCH)
    assert a.dimensions(SCRATCH) == 8
    assert a.dimensions("rag_missing") == 0


def test_cutover_moves_an_alias(adapter, server):
    a = adapter
    a.ensure_collection(SCRATCH + "_v1", dims=8)
    a.ensure_collection(SCRATCH + "_v2", dims=8)
    a.upsert(SCRATCH + "_v2", [c.__dict__ for c in _make_chunks("d", "h", ["new"])])
    a.cutover(SCRATCH, SCRATCH + "_v1")
    assert server.aliases["Rag_pytest_wv"] == "Rag_pytest_wv_v1"
    a.cutover(SCRATCH, SCRATCH + "_v2")
    assert server.aliases["Rag_pytest_wv"] == "Rag_pytest_wv_v2"
    with pytest.raises(ValueError, match="does not exist"):
        a.cutover(SCRATCH, "does_not_exist_v9")
    with pytest.raises(ValueError, match="cannot rename"):
        a.cutover(SCRATCH + "_v1", SCRATCH + "_v2")


# ---------------------------------------------------------------------------
# the run-history gate and the DDL artifact name
# ---------------------------------------------------------------------------
def test_run_history_is_skipped_not_failed(adapter):
    from rag_core.steps import keeps_run_history, record_history
    said = []
    written = record_history(adapter, [], [], "run-1", SCRATCH, log=said.append)
    assert written == {"runs": 0, "events": 0}
    assert any("keeps no run history" in line for line in said)
    assert not any("not recorded" in line for line in said)
    assert keeps_run_history(adapter) is False
    assert keeps_run_history(get_adapter("pgvector")) is True


def test_the_ddl_artifact_is_json_for_weaviate():
    from rag_core.registration import ddl_artifact_name
    assert ddl_artifact_name("weaviate") == "vector-store-ddl.json"
    assert ddl_artifact_name("Weaviate") == "vector-store-ddl.json"
    assert ddl_artifact_name("pgvector") == "vector-store-ddl.sql"
    assert ddl_artifact_name("singlestore") == "vector-store-ddl.sql"


# ---------------------------------------------------------------------------
# the manifested retrieval model reads the same collection
# ---------------------------------------------------------------------------
def test_retrieval_model_reads_the_weaviate_collection(adapter, server, monkeypatch):
    """The manifested model carries its own query (it must run where rag_core
    cannot be imported), so the live filter, the class name and the distance
    have to be right there too."""
    a = adapter
    a.ensure_collection(SCRATCH, dims=8)
    chunks = _make_chunks("docC", "hash1", ["alpha text", "beta text"])
    a.upsert(SCRATCH, [c.__dict__ for c in chunks])
    a.retire(SCRATCH, ids=[chunks[1].chunk_id], run_id="run-x")

    import requests
    import retrieve_context
    monkeypatch.setattr(requests, "post", server.post)
    monkeypatch.setattr(retrieve_context, "BACKEND", "weaviate")
    monkeypatch.setattr(retrieve_context, "COLLECTION", SCRATCH)
    hits = retrieve_context._search(QUERY, 5, None, dict(CONFIG))
    assert [h["content"] for h in hits] == ["alpha text"]   # retired one is gone
    assert hits[0]["distance"] == pytest.approx(0.0, abs=1e-6)
    assert hits[0]["score"] == pytest.approx(1.0, abs=1e-6)
    assert hits[0]["page"] == 1 and hits[0]["span_start"] == 0
    assert hits[0]["filename"] == "docC.txt"
    # no run stamped on the chunk: the version registered with the model
    assert hits[0]["corpus_run_id"] == retrieve_context.INGESTION_RUN_ID
    url = server.calls[-1][1]
    assert url == "http://weaviate.example:8080/v1/graphql"
    assert server.headers[-1]["Authorization"] == "Bearer key-123"

    hits = retrieve_context._search(QUERY, 5, '{"doc_id": "nope"}', dict(CONFIG))
    assert hits == []
    with pytest.raises(ValueError):
        retrieve_context._search(QUERY, 5, '{"department": "hr"}', dict(CONFIG))


# ---------------------------------------------------------------------------
# live round trip (opt-in: WEAVIATE_HOST)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def live_adapter():
    host = os.getenv("WEAVIATE_HOST", "")
    if not host:
        pytest.skip("WEAVIATE_HOST not set - no live Weaviate to test against")
    adapter = get_adapter("weaviate")
    try:
        adapter.connect({"host": host,
                         "port": int(os.getenv("WEAVIATE_PORT") or 8080),
                         "sslmode": os.getenv("WEAVIATE_SSLMODE", "disable"),
                         "user": os.getenv("WEAVIATE_RAG_USER", ""),
                         "password": os.getenv("WEAVIATE_RAG_PW", ""),
                         "dbname": ""})
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Weaviate not reachable: {type(exc).__name__}")
    yield adapter
    adapter._call("DELETE", "/v1/aliases/" + _ident(SCRATCH + "_live"),
                  ok=(200, 204), missing_ok=True)
    for name in (SCRATCH, SCRATCH + "_v1", SCRATCH + "_v2"):
        adapter.drop_collection(name)
    adapter.close()


def test_live_roundtrip_lineage_and_cutover(live_adapter):
    a = live_adapter
    a.drop_collection(SCRATCH)
    a.ensure_collection(SCRATCH, dims=8)
    a.ensure_collection(SCRATCH, dims=8)
    assert a.dimensions(SCRATCH) == 8
    chunks = _make_chunks("docA", "hashv1", ["alpha text", "beta text", "gamma text"])
    assert a.upsert(SCRATCH, [c.__dict__ for c in chunks]) == 3
    a.upsert(SCRATCH, [c.__dict__ for c in chunks])
    assert a.count(SCRATCH, include_retired=True) == 3
    hits = a.search(SCRATCH, QUERY, k=2)
    assert hits[0].chunk_id == chunks[0].chunk_id
    assert hits[0].record["distance"] == pytest.approx(0.0, abs=1e-5)
    assert a.search(SCRATCH, QUERY, k=5, filter={"department": "hr"})
    assert a.search(SCRATCH, QUERY, k=5, filter={"region": "none"}) == []

    a.sync_attributes(SCRATCH, {"confidence": "decimal"})
    a.upsert(SCRATCH, [dict(chunks[0].__dict__, confidence=0.25)])
    assert a.retire(SCRATCH, filter={"doc_id": "docA"},
                    keep_ids=[chunks[0].chunk_id], run_id="run-1") == 2
    assert a.count(SCRATCH) == 1 and a.count(SCRATCH, include_retired=True) == 3
    assert a.restore(SCRATCH, "run-1") == 2
    assert a.count(SCRATCH) == 3
    assert a.prune_history(SCRATCH, "2999-01-01 00:00:00", dry_run=True) >= 0
    assert a.delete(SCRATCH, filter={"doc_id": "docA"}) >= 3
    assert a.count(SCRATCH, include_retired=True) == 0

    a.ensure_collection(SCRATCH + "_v1", dims=8)
    a.ensure_collection(SCRATCH + "_v2", dims=8)
    a.cutover(SCRATCH + "_live", SCRATCH + "_v1")
    a.cutover(SCRATCH + "_live", SCRATCH + "_v2")
