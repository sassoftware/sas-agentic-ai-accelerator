# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""pgvector adapter tests.

DDL tests run everywhere. The live round-trip runs only when the repo .env
provides RAGSTORE_* credentials (local dev / CI with a database) and uses a
throwaway table name so it never touches real collections.
"""
from pathlib import Path

import pytest

from rag_core.adapters import get_adapter
from rag_core.adapters.pgvector import PgVectorAdapter, _ident
from rag_core.schema import Chunk, link_neighbors

REPO_ENV = Path(__file__).resolve().parents[3] / ".env"


def test_registry_returns_pgvector():
    assert isinstance(get_adapter("pgvector"), PgVectorAdapter)
    with pytest.raises(LookupError):
        get_adapter("faiss")


def test_identifier_allowlist():
    assert _ident("rag_demo_v1") == '"rag_demo_v1"'
    for bad in ('x"; DROP TABLE y;--', "Robert'); --", "UPPER", "1starts_with_digit"):
        with pytest.raises(ValueError):
            _ident(bad)


def test_ddl_contains_contract_pieces():
    ddl = PgVectorAdapter().ddl("rag_demo_v1", 384)
    assert "CREATE EXTENSION IF NOT EXISTS vector;" in ddl
    assert "vector(384) NOT NULL" in ddl
    assert "USING hnsw (embedding vector_cosine_ops)" in ddl
    assert "tsv" not in ddl


def test_ddl_sparse_optin_adds_tsvector():
    ddl = PgVectorAdapter().ddl("rag_demo_v1", 384, schema={"sparse": True})
    assert "tsv              tsvector" in ddl
    assert "USING gin (tsv)" in ddl


class RecordingCursor:
    """Captures the statements ensure_collection issues, in order."""

    def __init__(self, log, constraint_exists, existing_indexdef):
        self._log = log
        self._constraint_exists = constraint_exists
        self._existing_indexdef = existing_indexdef
        self._last = ""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._last = " ".join(str(sql).split())
        self._log.append(self._last)

    def fetchone(self):
        if "pg_constraint" in self._last:
            return (1,) if self._constraint_exists else None
        if "pg_indexes" in self._last:
            return (self._existing_indexdef,) if self._existing_indexdef else None
        return None


class RecordingConnection:
    def __init__(self, constraint_exists=False, existing_indexdef=None):
        self.statements = []
        self._constraint_exists = constraint_exists
        self._existing_indexdef = existing_indexdef

    def cursor(self):
        return RecordingCursor(self.statements, self._constraint_exists,
                               self._existing_indexdef)

    def commit(self):
        pass


def _ensure_log(constraint_exists=False, existing_indexdef=None):
    adapter = PgVectorAdapter()
    adapter._conn = RecordingConnection(constraint_exists, existing_indexdef)
    adapter.ensure_collection("rag_demo_v1", 8)
    return adapter._conn.statements


def test_a_full_index_is_replaced_by_the_partial_one_on_upgrade():
    """CREATE INDEX IF NOT EXISTS keeps whatever carries the name. Leaving a
    FULL index while retrieval filters on valid_to is the post-filter loss
    that cost SingleStore its rows - the reason pgvector uses a partial one."""
    full = "CREATE INDEX rag_demo_v1_hnsw_idx ON rag_demo_v1 USING hnsw (embedding)"
    log = _ensure_log(existing_indexdef=full)
    assert any(s.startswith("DROP INDEX IF EXISTS rag_demo_v1_hnsw_idx") for s in log)
    drop = next(i for i, s in enumerate(log) if s.startswith("DROP INDEX"))
    create = next(i for i, s in enumerate(log)
                  if "rag_demo_v1_hnsw_idx ON" in s and s.startswith("CREATE INDEX"))
    assert drop < create


def test_an_already_partial_index_is_left_alone():
    partial = ("CREATE INDEX rag_demo_v1_hnsw_idx ON rag_demo_v1 USING hnsw "
               "(embedding vector_cosine_ops) WHERE (valid_to IS NULL)")
    assert not [s for s in _ensure_log(existing_indexdef=partial)
                if s.startswith("DROP INDEX")]


def test_lineage_columns_are_added_before_the_partial_indexes():
    """The indexes are PARTIAL on `valid_to IS NULL`. Creating them before the
    ALTER that adds valid_to fails on a pre-lineage collection - which made
    the documented upgrade path impossible (found upgrading a live one)."""
    log = _ensure_log()
    first_index = next(i for i, s in enumerate(log) if s.startswith("CREATE INDEX"))
    last_column = max(i for i, s in enumerate(log) if "ADD COLUMN IF NOT EXISTS" in s)
    assert last_column < first_index
    assert any("valid_to IS NULL" in s for s in log[first_index:])


def test_an_older_collection_gains_the_live_row_constraint():
    """upsert names {collection}_live_uk as its ON CONFLICT target, so a
    collection that predates it would fail at load time, not at upgrade."""
    log = _ensure_log(constraint_exists=False)
    added = [s for s in log if "ADD CONSTRAINT rag_demo_v1_live_uk" in s]
    assert added and "UNIQUE NULLS NOT DISTINCT (chunk_id, valid_to)" in added[0]
    first_index = next(i for i, s in enumerate(log) if s.startswith("CREATE INDEX"))
    assert log.index(added[0]) < first_index


def test_an_existing_constraint_is_not_added_twice():
    assert not [s for s in _ensure_log(constraint_exists=True)
                if "ADD CONSTRAINT" in s]


def _make_chunks(doc_id: str, content_hash: str, texts: list) -> list:
    chunks = [
        Chunk.build(doc_id, f"/docs/{doc_id}.txt", i, text, content_hash,
                    "plaintext", "v1", "2026-07-27T12:00:00Z",
                    tags={"department": "legal" if i % 2 == 0 else "hr"})
        for i, text in enumerate(texts)
    ]
    link_neighbors(chunks)
    for i, chunk in enumerate(chunks):
        base = [0.0] * 8
        base[i % 8] = 1.0
        chunk.embedding = base
    return chunks


@pytest.fixture(scope="module")
def live_adapter():
    if not REPO_ENV.is_file():
        pytest.skip("no repo .env with RAGSTORE_* credentials")
    from rag_core.env import config_from_dotenv
    try:
        config = config_from_dotenv(str(REPO_ENV))
    except KeyError:
        pytest.skip("RAGSTORE_* incomplete in .env")
    adapter = get_adapter("pgvector")
    try:
        adapter.connect(config)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"pgvector not reachable: {type(exc).__name__}")
    yield adapter
    adapter.drop_collection("rag_pytest_scratch")
    adapter.drop_collection("rag_pytest_scratch_v2")
    adapter.drop_collection("rag_pytest_scratch_retired")
    adapter.close()


def test_live_roundtrip(live_adapter):
    a = live_adapter
    a.drop_collection("rag_pytest_scratch")
    a.ensure_collection("rag_pytest_scratch", dims=8)
    a.ensure_collection("rag_pytest_scratch", dims=8)  # idempotent

    chunks = _make_chunks("docA", "hashv1", ["alpha text", "beta text", "gamma text"])
    assert a.upsert("rag_pytest_scratch", [c.__dict__ for c in chunks]) == 3
    assert a.count("rag_pytest_scratch") == 3

    # re-upsert same version: idempotent, no duplicates
    a.upsert("rag_pytest_scratch", [c.__dict__ for c in chunks])
    assert a.count("rag_pytest_scratch") == 3

    query = [0.0] * 8
    query[0] = 1.0
    hits = a.search("rag_pytest_scratch", query, k=2)
    assert hits[0].chunk_id == chunks[0].chunk_id       # exact match wins
    assert hits[0].score > hits[1].score                # higher-is-better ordering
    assert hits[0].record["content"] == "alpha text"
    assert hits[0].record["tags"]["department"] == "legal"
    assert hits[0].record["next_id"] == chunks[1].chunk_id  # KG links round-trip

    filtered = a.search("rag_pytest_scratch", query, k=5,
                        filter={"department": "hr"})
    assert all(h.record["tags"]["department"] == "hr" for h in filtered)

    # document shrink: new version upserted first, stale deleted after (§2 ordering)
    v2 = _make_chunks("docA", "hashv2", ["alpha text v2"])
    for c in v2:
        c.pipeline_version = "v2"
    a.upsert("rag_pytest_scratch", [c.__dict__ for c in v2])
    deleted = a.delete("rag_pytest_scratch",
                       filter={"doc_id": "docA", "pipeline_version": {"$ne": "v2"}})
    assert deleted == 3
    assert a.count("rag_pytest_scratch") == 1

    ddl_artifact = a.ddl("rag_pytest_scratch", 8)
    assert "CREATE TABLE IF NOT EXISTS" in ddl_artifact


def test_live_retrieval_returns_everything_when_k_exceeds_the_collection(
        live_adapter):
    """The invariant SingleStore's ANN index broke, checked here too.

    Verified against the live cluster on 2026-07-30 across four collections
    (2, 2, 3 and 1578 live rows, with and without the partial live-row
    index): every k from 1 to 50 returned min(k, live), and the indexed
    top-5 was identical to the exact top-5. pgvector keeps its HNSW index
    by default because of this result - the partial index over live rows is
    what avoids the post-filter loss that costs recall elsewhere.
    """
    a = live_adapter
    a.drop_collection("rag_pytest_scratch")
    a.ensure_collection("rag_pytest_scratch", dims=8)
    chunks = _make_chunks("docK", "hash1",
                          ["one", "two", "three", "four", "five"])
    a.upsert("rag_pytest_scratch", [c.__dict__ for c in chunks])
    query = [0.0] * 8
    query[0] = 1.0
    for k in (1, 2, 3, 4, 5, 6, 9, 20):
        found = len(a.search("rag_pytest_scratch", query, k=k))
        assert found == min(k, 5), f"k={k} returned {found} of 5 live chunks"


def test_live_cutover_rename(live_adapter):
    a = live_adapter
    a.drop_collection("rag_pytest_scratch_v2")
    a.drop_collection("rag_pytest_scratch_retired")
    a.ensure_collection("rag_pytest_scratch_v2", dims=8)
    a.cutover("rag_pytest_scratch", "rag_pytest_scratch_v2")
    collections = a.list_collections()
    assert "rag_pytest_scratch" in collections
    assert "rag_pytest_scratch_retired" in collections
    assert "rag_pytest_scratch_v2" not in collections
    with pytest.raises(ValueError):
        a.cutover("rag_pytest_scratch", "does_not_exist_v9")
