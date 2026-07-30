# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The SQL the manifested retrieval model emits, per backend.

The live pgvector tests skip wherever the database is not reachable — which is
everywhere outside the cluster — so the dialect the model builds is pinned
here against a recording stub instead. Adding a second backend must not change
a character of the first one's query.
"""
import pytest

import retrieve_context


class StubCursor:
    def __init__(self, recorder, rows):
        self._recorder = recorder
        self._rows = rows
        self.description = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._recorder.append((sql, list(params or [])))
        # the live-column probe asks for a count first
        self._answer = [(1,)] if "information_schema" in sql else self._rows

    def fetchone(self):
        return self._answer[0]

    def fetchall(self):
        return self._answer


class StubConnection:
    def __init__(self, rows):
        self.statements = []
        self._rows = rows
        self.closed = False

    def cursor(self):
        return StubCursor(self.statements, self._rows)

    def commit(self):
        pass

    def close(self):
        self.closed = True


ROW = ("doc-1", "chunk-1", "/docs/a.txt", "2026-07-30 12:00:00", "alpha text",
       "Intro", {"page": 3, "start": 5, "end": 10}, "run-7", 0.25)


@pytest.fixture
def recorded(monkeypatch):
    """Run _search against a stub and hand back (hits, statements)."""
    def run(backend, rank_value, filter_json=None, vector=(0.6, 0.8)):
        connection = StubConnection([ROW[:-1] + (rank_value,)])
        monkeypatch.setattr(retrieve_context, "BACKEND", backend)
        monkeypatch.setattr(retrieve_context, "COLLECTION", "rag_demo_v1")
        monkeypatch.setattr(retrieve_context, "_connect", lambda store: connection)
        hits = retrieve_context._search(list(vector), 4, filter_json, {})
        assert connection.closed
        return hits, connection.statements
    return run


def test_pgvector_query_is_unchanged(recorded):
    hits, statements = recorded("pgvector", 0.25)
    probe, search = statements
    assert "current_schema()" in probe[0]
    sql, params = search
    assert '(embedding <=> %s::vector) AS ranked' in sql
    assert 'FROM "rag_demo_v1"' in sql
    assert "ORDER BY ranked ASC LIMIT %s" in sql
    assert "AND valid_to IS NULL" in sql
    # the raw vector, not a normalized one
    assert params[0] == "[0.6,0.8]"
    assert params[-1] == 4
    assert hits[0]["distance"] == pytest.approx(0.25)
    assert hits[0]["score"] == pytest.approx(0.75)


def test_singlestore_query_normalizes_and_ranks_by_dot_product(recorded):
    # a unit-length query vector, so the dot product IS the cosine similarity
    hits, statements = recorded("singlestore", 0.75)
    probe, search = statements
    assert "DATABASE()" in probe[0]
    sql, params = search
    assert "DOT_PRODUCT(embedding, %s :> VECTOR(2)) AS ranked" in sql
    assert "FROM `rag_demo_v1`" in sql
    assert "ORDER BY ranked DESC LIMIT %s" in sql
    assert "AND valid_to = '9999-12-31 00:00:00'" in sql
    assert params[0] == "[0.6,0.8]"          # already unit length
    # a dot product of 0.75 is the same hit as a pgvector distance of 0.25
    assert hits[0]["distance"] == pytest.approx(0.25)
    assert hits[0]["score"] == pytest.approx(0.75)


def test_a_non_unit_query_vector_is_normalized_for_singlestore(recorded):
    """The SCR embedding endpoint does not promise unit vectors, and without
    normalization the dot product is not a cosine at all."""
    _, statements = recorded("singlestore", 0.9, vector=(3.0, 4.0))
    assert statements[1][1][0] == "[0.6,0.8]"
    # pgvector computes the cosine itself, so it must keep the raw vector
    _, statements = recorded("pgvector", 0.1, vector=(3.0, 4.0))
    assert statements[1][1][0] == "[3.0,4.0]"


def test_a_zero_query_vector_does_not_divide_by_zero(recorded):
    _, statements = recorded("singlestore", 0.0, vector=(0.0, 0.0))
    assert statements[1][1][0] == "[0.0,0.0]"


def test_filter_columns_are_quoted_per_dialect(recorded):
    _, statements = recorded("pgvector", 0.1, '{"doc_id": "d1"}')
    assert ' AND "doc_id" = %s' in statements[1][0]
    _, statements = recorded("singlestore", 0.1, '{"doc_id": "d1"}')
    assert " AND `doc_id` = %s" in statements[1][0]


def test_an_unknown_filter_column_is_refused(recorded):
    with pytest.raises(ValueError):
        recorded("pgvector", 0.1, '{"; DROP TABLE x --": "d1"}')


def test_a_json_span_arriving_as_text_is_parsed(monkeypatch):
    """psycopg2 hands back a dict; a MySQL driver may hand back the text."""
    connection = StubConnection([ROW[:6] + ('{"page": 9, "start": 1, "end": 2}',
                                            "run-7", 0.5)])
    monkeypatch.setattr(retrieve_context, "BACKEND", "singlestore")
    monkeypatch.setattr(retrieve_context, "COLLECTION", "rag_demo_v1")
    monkeypatch.setattr(retrieve_context, "_connect", lambda store: connection)
    hits = retrieve_context._search([1.0, 0.0], 1, None, {})
    assert hits[0]["page"] == 9 and hits[0]["span_end"] == 2


def test_default_ports_follow_the_backend(monkeypatch):
    for backend, port in (("pgvector", 5432), ("singlestore", 3306)):
        monkeypatch.setattr(retrieve_context, "BACKEND", backend)
        monkeypatch.setattr(retrieve_context, "STORE_PORT", "")
        monkeypatch.setattr(retrieve_context, "STORE_HOST", "a-host")
        monkeypatch.setattr(retrieve_context, "STORE_DB", "a-db")
        for name in ("PGVECTOR_PORT", "SINGLESTORE_PORT", "RAGSTORE_PORT"):
            monkeypatch.delenv(name, raising=False)
        config = retrieve_context._store_config({"user": "u", "password": "p"})
        assert config["port"] == port


def test_a_backend_prefixed_setting_beats_the_shared_fallback(monkeypatch):
    monkeypatch.setattr(retrieve_context, "BACKEND", "singlestore")
    monkeypatch.setenv("RAGSTORE_HOST", "shared-host")
    monkeypatch.setenv("SINGLESTORE_HOST", "its-own-host")
    config = retrieve_context._store_config({"user": "u", "password": "p"})
    assert config["host"] == "its-own-host"
    monkeypatch.delenv("SINGLESTORE_HOST")
    config = retrieve_context._store_config({"user": "u", "password": "p"})
    assert config["host"] == "shared-host"
