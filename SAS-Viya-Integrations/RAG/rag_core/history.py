# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Run history for a RAG project (design §6).

The ledger records CURRENT state: one row per document, overwritten every
run. That answers "what is in the corpus now" and nothing else. Four of the
five questions an owner asks after six months — how big was the corpus last
month, which documents changed, which configuration produced this chunk, what
did the ingestion cost — need an append-only record, and the columns that look
like history do not behave like it (`run_id` and `updated_at` are re-stamped
onto every ledger row on every run).

Three tables, next to the chunks rather than in CAS:

  rag_runs        one row per run, opened at the start and closed at the end
  rag_doc_events  append-only, one row per document per run WHERE SOMETHING
                  HAPPENED — the corpus change log
  rag_configs     the parameters behind a config_id, so the hash has an
                  inverse and "which configuration produced this" is
                  answerable

They live in the vector store's database because a CAS table is
overwrite-in-place by construction here (droptable → promote → save), has no
transactional append and no constraints, and an empty run deletes the saved
file outright. That is the wrong substrate for an append-only log. The
consequence is that history is invisible to Visual Analytics, so `runs()` and
`doc_events()` return rows the caller publishes to CAS for reporting — the
transactional copy stays authoritative, the reporting copy is disposable.
"""
from __future__ import annotations

import datetime
import json

RUN_COLUMNS = [
    "run_id", "rag_project", "collection", "backend", "started_at",
    "finished_at", "status", "pipeline_version", "config_id", "embed_model",
    "embed_dims", "chunker", "input_token_limit", "overlap_tokens",
    "docs_new", "docs_changed", "docs_unchanged", "docs_deleted",
    "docs_failed", "docs_ingested", "chunks_written", "chunks_retired",
    "collection_chunks", "embed_calls", "embed_tokens", "embed_seconds",
    "error_text",
]


# Which of the above are MEASURES rather than labels.
#
# The CAS publish stages every column as varchar unless it is named here, and
# a count published as text is a category in Visual Analytics: it cannot be
# summed, averaged or multiplied by a price. That is the difference between a
# reportable run history and a table someone has to cast by hand.
RUN_NUMERIC = [
    "embed_dims", "input_token_limit", "overlap_tokens",
    "docs_new", "docs_changed", "docs_unchanged", "docs_deleted",
    "docs_failed", "docs_ingested", "chunks_written", "chunks_retired",
    "collection_chunks", "embed_calls", "embed_tokens", "embed_seconds",
]

EVENT_COLUMNS = [
    "run_id", "doc_id", "source_uri", "source_kind", "status",
    "previous_content_hash", "new_content_hash", "chunk_count_before",
    "chunk_count_after", "error_text",
]

EVENT_NUMERIC = ["chunk_count_before", "chunk_count_after"]

# what a run's document counts are keyed on
_STATUS_COUNTS = ("new", "changed", "unchanged", "deleted", "failed", "ingested")

_DIALECTS = {
    "postgres": {
        "text": "text",
        "json": "jsonb",
        "stamp": "timestamptz",
        "now": "now()",
        "serial": "bigserial PRIMARY KEY",
        "upsert": "ON CONFLICT ({key}) DO UPDATE SET {sets}",
        "excluded": "EXCLUDED.{column}",
        "shard": "",
    },
    "mysql": {
        "text": "LONGTEXT",
        "json": "JSON",
        "stamp": "DATETIME(6)",
        "now": "CURRENT_TIMESTAMP(6)",
        "serial": "BIGINT AUTO_INCREMENT, KEY (id)",
        "upsert": "ON DUPLICATE KEY UPDATE {sets}",
        "excluded": "VALUES({column})",
        "shard": ",\n    SHARD KEY ({shard})",
    },
}


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S")


def started_at_from_run_id(run_id: str) -> str:
    """When the run began, recovered from its own id.

    The id is minted as `run-<unix seconds>[-<session>]` at the moment the run
    lock is taken, which IS the start of the run. Without this the row is
    created when the run closes and `started_at` takes its column default, so
    every run appeared to take zero seconds - a fabricated measurement that
    looks like a real one.
    """
    parts = str(run_id or "").split("-")
    if len(parts) < 2 or not parts[1].isdigit():
        return ""
    seconds = int(parts[1])
    # sanity: a plausible epoch, not a counter that happens to be numeric
    if not 1_000_000_000 < seconds < 4_000_000_000:
        return ""
    moment = datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


class History:
    """Reads and writes the three history tables on an open connection.

    The connection belongs to the vector-store adapter — history sits beside
    the chunks it describes, in the same transaction scope.
    """

    def __init__(self, connection, dialect: str = "postgres"):
        if dialect not in _DIALECTS:
            raise ValueError(f"unsupported history dialect {dialect!r}")
        self._conn = connection
        self._d = _DIALECTS[dialect]
        self.dialect = dialect

    # -- schema --------------------------------------------------------------
    def ddl(self) -> list:
        d = self._d
        key = "VARCHAR(64)" if self.dialect == "mysql" else d["text"]
        short = "VARCHAR(128)" if self.dialect == "mysql" else d["text"]
        return [
            f"""CREATE TABLE IF NOT EXISTS rag_runs (
    run_id            {key} NOT NULL,
    rag_project       {short},
    collection        {short},
    backend           {short},
    started_at        {d['stamp']} NOT NULL DEFAULT {d['now']},
    finished_at       {d['stamp']},
    status            {short},
    pipeline_version  {short},
    config_id         {key},
    embed_model       {short},
    embed_dims        integer,
    chunker           {short},
    input_token_limit integer,
    overlap_tokens    integer,
    docs_new          integer DEFAULT 0,
    docs_changed      integer DEFAULT 0,
    docs_unchanged    integer DEFAULT 0,
    docs_deleted      integer DEFAULT 0,
    docs_failed       integer DEFAULT 0,
    docs_ingested     integer DEFAULT 0,
    chunks_written    integer DEFAULT 0,
    chunks_retired    integer DEFAULT 0,
    collection_chunks integer DEFAULT 0,
    embed_calls       integer DEFAULT 0,
    embed_tokens      integer DEFAULT 0,
    embed_seconds     double precision DEFAULT 0,
    error_text        {d['text']},
    PRIMARY KEY (run_id){d['shard'].format(shard='run_id')}
)""",
            f"""CREATE TABLE IF NOT EXISTS rag_doc_events (
    id                    {d['serial']},
    run_id                {key} NOT NULL,
    doc_id                {key} NOT NULL,
    source_uri            {d['text']},
    source_kind           {short},
    status                {short},
    previous_content_hash {key},
    new_content_hash      {key},
    chunk_count_before    integer,
    chunk_count_after     integer,
    error_text            {d['text']},
    recorded_at           {d['stamp']} NOT NULL DEFAULT {d['now']}
    {d['shard'].format(shard='run_id')}
)""",
            f"""CREATE TABLE IF NOT EXISTS rag_configs (
    config_id     {key} NOT NULL,
    settings      {d['json']},
    first_seen_at {d['stamp']} NOT NULL DEFAULT {d['now']},
    PRIMARY KEY (config_id){d['shard'].format(shard='config_id')}
)""",
        ]

    def ensure_tables(self) -> None:
        with self._conn.cursor() as cursor:
            for statement in self.ddl():
                cursor.execute(statement)
        self._commit()

    def _commit(self):
        try:
            self._conn.commit()
        except Exception:
            pass

    # -- writing -------------------------------------------------------------
    def open_run(self, run_id: str, **fields) -> None:
        """Record that a run started. Idempotent: a re-run of the same id
        updates the opening row rather than raising."""
        values = {"run_id": run_id, "status": "running",
                  "started_at": _now()}
        values.update({k: v for k, v in fields.items() if k in RUN_COLUMNS})
        self._upsert("rag_runs", "run_id", values)

    def close_run(self, run_id: str, status: str = "completed",
                  counts: dict = None, **fields) -> None:
        """Record how a run ended, with its document counts and cost."""
        values = {"run_id": run_id, "status": status, "finished_at": _now()}
        # the run began when its id was minted, not when it closed
        began = started_at_from_run_id(run_id)
        if began:
            values["started_at"] = began
        for name in _STATUS_COUNTS:
            values["docs_" + name] = int((counts or {}).get(name, 0))
        values.update({k: v for k, v in fields.items() if k in RUN_COLUMNS})
        self._upsert("rag_runs", "run_id", values)

    def _upsert(self, table: str, key: str, values: dict) -> None:
        columns = list(values)
        marks = ", ".join(["%s"] * len(columns))
        sets = ", ".join(
            f"{c} = " + self._d["excluded"].format(column=c)
            for c in columns if c != key)
        clause = self._d["upsert"].format(key=key, sets=sets)
        with self._conn.cursor() as cursor:
            cursor.execute(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({marks}) "
                + clause, [values[c] for c in columns])
        self._commit()

    def record_events(self, run_id: str, events: list) -> int:
        """Append what happened to each document. Unchanged documents are NOT
        recorded — the point is a change log, not a per-run copy of the
        ledger, which would grow by the size of the corpus every run."""
        rows = [e for e in events
                if str(e.get("status", "")).lower() != "unchanged"]
        if not rows:
            return 0
        columns = EVENT_COLUMNS
        marks = "(" + ", ".join(["%s"] * len(columns)) + ")"
        # paged like upsert: a first ingestion of 10,000 documents in one
        # statement is 100,000 placeholders, which the driver or the server
        # refuses long before the data is large
        for start in range(0, len(rows), 200):
            page = rows[start:start + 200]
            params: list = []
            for event in page:
                for column in columns:
                    params.append(run_id if column == "run_id"
                                  else event.get(column))
            with self._conn.cursor() as cursor:
                cursor.execute(
                    f"INSERT INTO rag_doc_events ({', '.join(columns)}) VALUES "
                    + ", ".join([marks] * len(page)), params)
            self._commit()
        return len(rows)

    def record_config(self, config_id: str, settings) -> None:
        """Give the configuration hash an inverse. First writer wins: the
        settings behind a hash cannot change, or the hash would differ."""
        if not config_id:
            return
        payload = settings if isinstance(settings, str) else json.dumps(
            settings, sort_keys=True)
        columns = ["config_id", "settings"]
        clause = self._d["upsert"].format(
            key="config_id",
            sets="settings = " + self._d["excluded"].format(column="settings"))
        with self._conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO rag_configs (config_id, settings) VALUES (%s, %s) "
                + clause, [config_id, payload])
        self._commit()

    # -- reading (for the CAS publish) ---------------------------------------
    def _select(self, sql: str, params: list) -> list:
        with self._conn.cursor() as cursor:
            cursor.execute(sql, params)
            names = [d[0] for d in cursor.description]
            return [dict(zip(names, row)) for row in cursor.fetchall()]

    def runs(self, collection: str = "", limit: int = 500) -> list:
        where, params = ("WHERE collection = %s", [collection]) if collection \
            else ("", [])
        return self._select(
            f"SELECT * FROM rag_runs {where} ORDER BY started_at DESC "
            f"LIMIT %s", params + [int(limit)])

    def doc_events(self, collection: str = "", limit: int = 5000) -> list:
        if collection:
            return self._select(
                "SELECT e.* FROM rag_doc_events e JOIN rag_runs r "
                "ON r.run_id = e.run_id WHERE r.collection = %s "
                "ORDER BY e.recorded_at DESC LIMIT %s", [collection, int(limit)])
        return self._select(
            "SELECT * FROM rag_doc_events ORDER BY recorded_at DESC LIMIT %s",
            [int(limit)])

    def configs(self, limit: int = 500) -> list:
        return self._select(
            "SELECT * FROM rag_configs ORDER BY first_seen_at DESC LIMIT %s",
            [int(limit)])


def events_from_inventory(inventory: list, previous: dict = None) -> list:
    """Turn a run's inventory into document events.

    `previous` maps doc_id to its ledger row before the run, which is where
    the before-values come from — without it the change log records what a
    document became but not what it was.
    """
    previous = previous or {}
    events = []
    for row in inventory:
        doc_id = row.get("doc_id")
        if not doc_id or doc_id == "__run_lock__":
            continue
        was = previous.get(doc_id, {})
        events.append({
            "doc_id": doc_id,
            "source_uri": row.get("source_uri", ""),
            "source_kind": row.get("source_kind", ""),
            "status": row.get("status", ""),
            "previous_content_hash": was.get("content_hash", ""),
            "new_content_hash": row.get("content_hash", ""),
            "chunk_count_before": int(was.get("chunk_count") or 0),
            "chunk_count_after": int(row.get("chunk_count") or 0),
            "error_text": (row.get("error_text") or "")[:500],
        })
    return events


def status_counts(inventory: list) -> dict:
    counts = {name: 0 for name in _STATUS_COUNTS}
    for row in inventory:
        if row.get("doc_id") == "__run_lock__":
            continue
        status = str(row.get("status", "")).lower()
        if status in counts:
            counts[status] += 1
    return counts
