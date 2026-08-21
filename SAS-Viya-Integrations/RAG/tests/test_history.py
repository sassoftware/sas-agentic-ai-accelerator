# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run history: the append-only record the ledger cannot be.

The ledger holds current state and is overwritten every run, so it can say
what the corpus contains and nothing about how it got there. These tests
defend the properties that make history worth having: a run is recorded even
when it fails, unchanged documents do not bloat the change log, and a
configuration hash gets an inverse.
"""
import json

import pytest

from rag_core.history import (EVENT_COLUMNS, RUN_COLUMNS, History,
                              events_from_inventory, status_counts)


class FakeCursor:
    def __init__(self, log, rows):
        self._log = log
        self._rows = rows
        self.description = [("run_id",), ("status",)]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._log.append((" ".join(str(sql).split()), list(params or [])))

    def fetchall(self):
        return self._rows


class FakeConnection:
    def __init__(self, rows=()):
        self.statements = []
        self._rows = list(rows)
        self.commits = 0

    def cursor(self):
        return FakeCursor(self.statements, self._rows)

    def commit(self):
        self.commits += 1


def _history(dialect="postgres", rows=()):
    conn = FakeConnection(rows)
    return History(conn, dialect), conn


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------
def test_the_three_tables_are_created():
    history, conn = _history()
    history.ensure_tables()
    sql = " ".join(s for s, _ in conn.statements)
    for table in ("rag_runs", "rag_doc_events", "rag_configs"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql


def test_each_dialect_gets_its_own_types():
    postgres, _ = _history("postgres")
    mysql, _ = _history("mysql")
    pg_sql = " ".join(postgres.ddl())
    my_sql = " ".join(mysql.ddl())
    assert "jsonb" in pg_sql and "timestamptz" in pg_sql
    assert "JSON" in my_sql and "DATETIME(6)" in my_sql
    # SingleStore needs a shard key; Postgres must not be given one
    assert "SHARD KEY" in my_sql and "SHARD KEY" not in pg_sql


def test_an_unknown_dialect_is_refused():
    with pytest.raises(ValueError):
        History(FakeConnection(), "oracle")


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------
def test_a_run_is_opened_before_it_can_succeed():
    """Opened at the start, so a run that dies mid-way still left a trace -
    the failure case is exactly when history matters most."""
    history, conn = _history()
    history.open_run("run-1", rag_project="P", collection="c", backend="pgvector")
    sql, params = conn.statements[0]
    assert sql.startswith("INSERT INTO rag_runs")
    assert "run-1" in params and "running" in params


def test_closing_a_run_records_counts_and_cost():
    history, conn = _history()
    history.close_run("run-1", status="completed",
                      counts={"new": 2, "changed": 1, "failed": 1},
                      chunks_written=7, embed_calls=3, embed_seconds=1.5)
    sql, params = conn.statements[0]
    assert "docs_new" in sql and "docs_failed" in sql
    assert 2 in params and 7 in params and 1.5 in params


def test_a_failed_run_is_still_recorded():
    history, conn = _history()
    history.close_run("run-9", status="failed", error_text="store unreachable")
    _, params = conn.statements[0]
    assert "failed" in params and "store unreachable" in params


def test_reopening_a_run_updates_rather_than_duplicating():
    for dialect, expected in (("postgres", "ON CONFLICT"),
                              ("mysql", "ON DUPLICATE KEY UPDATE")):
        history, conn = _history(dialect)
        history.open_run("run-1")
        assert expected in conn.statements[0][0]


def test_every_run_column_is_writable():
    """A column nobody can write is a column that will always read null."""
    history, conn = _history()
    history.close_run("run-1", **{c: 1 for c in RUN_COLUMNS
                                  if c not in ("run_id", "status")})
    written = conn.statements[0][0]
    for column in RUN_COLUMNS:
        assert column in written, f"{column} cannot be written"


# ---------------------------------------------------------------------------
# document events
# ---------------------------------------------------------------------------
def test_unchanged_documents_are_not_recorded():
    """Otherwise the change log grows by the size of the corpus every run and
    stops being a change log."""
    history, conn = _history()
    written = history.record_events("run-1", [
        {"doc_id": "a", "status": "unchanged"},
        {"doc_id": "b", "status": "changed"},
    ])
    assert written == 1
    assert "b" in conn.statements[0][1]
    assert "a" not in conn.statements[0][1]


def test_a_run_where_nothing_happened_writes_nothing():
    history, conn = _history()
    assert history.record_events("run-1", [{"doc_id": "a", "status": "unchanged"}]) == 0
    assert conn.statements == []


def test_events_carry_what_the_document_was_and_became():
    previous = {"d1": {"content_hash": "old", "chunk_count": 3}}
    events = events_from_inventory(
        [{"doc_id": "d1", "status": "changed", "content_hash": "new",
          "chunk_count": 5, "source_uri": "/a.md", "source_kind": "content"}],
        previous)
    event = events[0]
    assert event["previous_content_hash"] == "old"
    assert event["new_content_hash"] == "new"
    assert event["chunk_count_before"] == 3
    assert event["chunk_count_after"] == 5
    assert set(event) | {"run_id"} == set(EVENT_COLUMNS)


def test_the_run_lock_is_not_a_document():
    events = events_from_inventory([{"doc_id": "__run_lock__", "status": "lock"}])
    assert events == []


def test_status_counts_ignore_the_lock_row_too():
    counts = status_counts([{"doc_id": "a", "status": "new"},
                            {"doc_id": "b", "status": "new"},
                            {"doc_id": "__run_lock__", "status": "lock"}])
    assert counts["new"] == 2
    assert sum(counts.values()) == 2


# ---------------------------------------------------------------------------
# configs
# ---------------------------------------------------------------------------
def test_a_configuration_hash_gains_an_inverse():
    history, conn = _history()
    history.record_config("cfg-1", {"chunker": "recursive", "overlap": 30})
    sql, params = conn.statements[0]
    assert "rag_configs" in sql
    assert json.loads(params[1])["chunker"] == "recursive"


def test_settings_are_stored_deterministically():
    """Two dicts with the same content must serialise identically, or the
    same configuration would look like two."""
    a, conn_a = _history()
    b, conn_b = _history()
    a.record_config("cfg", {"x": 1, "y": 2})
    b.record_config("cfg", {"y": 2, "x": 1})
    assert conn_a.statements[0][1] == conn_b.statements[0][1]


def test_an_empty_config_id_is_not_recorded():
    history, conn = _history()
    history.record_config("", {"a": 1})
    assert conn.statements == []


# ---------------------------------------------------------------------------
# reading back for the CAS publish
# ---------------------------------------------------------------------------
def test_runs_can_be_filtered_to_one_collection():
    history, conn = _history(rows=[("run-1", "completed")])
    history.runs(collection="rag_demo_v1")
    sql, params = conn.statements[0]
    assert "WHERE collection = %s" in sql
    assert params[0] == "rag_demo_v1"


def test_doc_events_join_through_to_the_collection():
    """Events carry a run id, not a collection - the join is what makes
    'what changed in this collection' answerable."""
    history, conn = _history(rows=[])
    history.doc_events(collection="rag_demo_v1")
    sql, _ = conn.statements[0]
    assert "JOIN rag_runs" in sql and "r.collection = %s" in sql


# ---------------------------------------------------------------------------
# the step-level orchestration
# ---------------------------------------------------------------------------
class FakeAdapter:
    HISTORY_DIALECT = "postgres"

    def __init__(self, connection):
        self._conn = connection

    def raw_connection(self):
        return self._conn


def test_record_history_writes_a_run_and_its_events():
    from rag_core.steps import record_history
    conn = FakeConnection()
    inventory = [{"doc_id": "d1", "status": "changed", "content_hash": "new",
                  "chunk_count": 2, "source_uri": "/a.md"},
                 {"doc_id": "d2", "status": "unchanged", "content_hash": "same"}]
    previous = [{"doc_id": "d1", "content_hash": "old", "chunk_count": 1}]
    written = record_history(FakeAdapter(conn), inventory, previous, "run-1",
                             "rag_demo_v1", config_id="cfg",
                             settings={"chunker": "recursive"},
                             metrics={"chunks_written": 2},
                             log=lambda *_: None)
    assert written == {"runs": 1, "events": 1}   # unchanged excluded
    sql = " ".join(s for s, _ in conn.statements)
    assert "rag_doc_events" in sql and "rag_runs" in sql and "rag_configs" in sql


def test_history_failure_never_fails_the_load():
    """The ingestion already succeeded; losing the record of it is not a
    reason to lose the load."""
    from rag_core.steps import record_history

    class Refusing(FakeConnection):
        def cursor(self):
            raise RuntimeError("no permission to create tables")

    messages = []
    written = record_history(FakeAdapter(Refusing()), [], [], "run-1", "c",
                             log=messages.append)
    assert written == {"runs": 0, "events": 0}
    assert any("not recorded" in m and "unaffected" in m for m in messages)


def test_a_run_records_what_it_found_and_what_it_achieved():
    """run_load overwrites each successful row's status with 'ingested', so
    counting the post-load inventory alone reported 0 new / 0 changed for a
    run that plainly did work (found live in the published CAS table)."""
    from rag_core.steps import record_history
    conn = FakeConnection()
    discovery = [{"doc_id": "d1", "status": "new"},
                 {"doc_id": "d2", "status": "changed"},
                 {"doc_id": "d3", "status": "unchanged"}]
    after = [{"doc_id": "d1", "status": "ingested"},
             {"doc_id": "d2", "status": "ingested"},
             {"doc_id": "d3", "status": "unchanged"}]
    record_history(FakeAdapter(conn), after, [], "run-1", "coll",
                   discovery=discovery, log=lambda *_: None)
    close = [(s, p) for s, p in conn.statements if s.startswith("INSERT INTO rag_runs")][-1]
    columns = close[0].split("(")[1].split(")")[0].replace(" ", "").split(",")
    values = dict(zip(columns, close[1]))
    assert values["docs_new"] == 1
    assert values["docs_changed"] == 1
    assert values["docs_unchanged"] == 1
    assert values["docs_ingested"] == 2


def test_an_event_reports_what_the_document_did_not_that_it_loaded():
    from rag_core.steps import record_history
    conn = FakeConnection()
    record_history(FakeAdapter(conn), [{"doc_id": "d1", "status": "ingested"}],
                   [], "run-1", "coll",
                   discovery=[{"doc_id": "d1", "status": "new"}],
                   log=lambda *_: None)
    events = [(s, p) for s, p in conn.statements if s.startswith("INSERT INTO rag_doc_events")][0]
    assert "new" in events[1] and "ingested" not in events[1]


def test_a_failure_outcome_beats_the_discovery_status():
    from rag_core.steps import record_history
    conn = FakeConnection()
    record_history(FakeAdapter(conn), [{"doc_id": "d1", "status": "failed",
                                        "error_text": "no text layer"}],
                   [], "run-1", "coll",
                   discovery=[{"doc_id": "d1", "status": "changed"}],
                   log=lambda *_: None)
    events = [(s, p) for s, p in conn.statements if s.startswith("INSERT INTO rag_doc_events")][0]
    assert "failed" in events[1]


# ---------------------------------------------------------------------------
# the empty-column defects (found by adversarial review, 2026-07-31)
# ---------------------------------------------------------------------------
def test_the_cost_columns_are_actually_populated():
    """'What did last month's ingestion cost' was the reason these tables
    exist, and every real run recorded calls=0 tokens=0 seconds=0 because the
    EmbeddingClient's numbers were logged in the Embed step and discarded."""
    from rag_core.steps import record_history, stamp_usage
    conn = FakeConnection()
    inventory = [{"doc_id": "d1", "status": "ingested"}]
    stamp_usage(inventory, {"calls": 4, "tokens": 140, "run_time": 0.75})
    record_history(FakeAdapter(conn), inventory, [], "run-1785488641", "coll",
                   settings={"chunker": "recursive", "input_token_limit": 256,
                             "overlap_tokens": 30},
                   metrics={"chunks_written": 3, "chunks_retired": 1},
                   log=lambda *_: None)
    close = [(s, p) for s, p in conn.statements
             if s.startswith("INSERT INTO rag_runs")][-1]
    columns = close[0].split("(")[1].split(")")[0].replace(" ", "").split(",")
    values = dict(zip(columns, close[1]))
    assert values["embed_calls"] == 4
    assert values["embed_tokens"] == 140
    assert values["embed_seconds"] == 0.75
    assert values["chunks_written"] == 3 and values["chunks_retired"] == 1
    assert values["chunker"] == "recursive"
    assert values["input_token_limit"] == 256
    assert values["overlap_tokens"] == 30


def test_started_at_comes_from_the_run_id_not_the_close():
    """Created at close, started_at took its column default and every run
    appeared to take zero seconds - a fabricated measurement."""
    from rag_core.history import started_at_from_run_id
    from rag_core.steps import record_history
    assert started_at_from_run_id("run-1785488641") == "2026-07-31 09:04:01"
    assert started_at_from_run_id("run-1785488641-31428") == "2026-07-31 09:04:01"
    assert started_at_from_run_id("run-notanumber") == ""
    assert started_at_from_run_id("run-42") == ""      # not a plausible epoch

    conn = FakeConnection()
    record_history(FakeAdapter(conn), [], [], "run-1785488641", "coll",
                   log=lambda *_: None)
    close = [(s, p) for s, p in conn.statements
             if s.startswith("INSERT INTO rag_runs")][-1]
    columns = close[0].split("(")[1].split(")")[0].replace(" ", "").split(",")
    values = dict(zip(columns, close[1]))
    assert values["started_at"] == "2026-07-31 09:04:01"
    assert values["started_at"] < values["finished_at"]


def test_document_events_are_written_in_pages():
    """One statement for 10,000 documents is 100,000 placeholders."""
    history, conn = _history()
    events = [{"doc_id": f"d{i}", "status": "changed"} for i in range(450)]
    assert history.record_events("run-1", events) == 450
    inserts = [s for s, _ in conn.statements
               if s.startswith("INSERT INTO rag_doc_events")]
    assert len(inserts) == 3, "expected 200 + 200 + 50"
