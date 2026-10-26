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
import re


# ---------------------------------------------------------------------------
# Host names out of the text of an error before it is stored
# ---------------------------------------------------------------------------
# The text of a failure is kept where everyone who reads a RAG setup sees it:
# `error_text` of the ledger and of the step tables, and `rag_runs` /
# `rag_doc_events` in the vector store. A database driver writes the address it
# failed at into its message - SingleStore names the internal host and port of
# the node that failed (`Leaf Error (node-1.svc-singlestore.svc.cluster.local:3306)`),
# psycopg the server it could not reach - and a provider's HTTP error its URL.
# `without_hosts` puts `<host>` in their place and leaves the rest of the text.
#
# It masks what such messages hold, not every word with a dot: a name followed
# by a port, the host of a URL, an IPv4 address, a name of a cluster's own
# domain (`.local`, `.internal`, `.svc`), and a quoted name after "host name",
# "server at" or "host". A file name (`report.v2.pdf`) or a Python module path
# (`singlestoredb.exceptions`) in the text stays as it is.
HOST = "<host>"

_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_NAME = rf"{_LABEL}(?:\.{_LABEL})+"

_PATTERNS = (
    # the host of a URL: scheme://[user@]host[:port]
    re.compile(rf"(?<=://)(?:[^/\s@]+@)?({_NAME}|\[[0-9A-Fa-f:.]+\])"),
    # a name or an address followed by a port
    re.compile(rf"(?<![\w.-])({_NAME})(?=:\d{{2,5}}\b)"),
    # an IPv4 address
    re.compile(r"(?<![\w.])((?:\d{1,3}\.){3}\d{1,3})(?![\w.])"),
    # a name of a cluster's own domain
    re.compile(rf"(?<![\w.-])({_LABEL}(?:\.{_LABEL})*\.(?:local|internal|svc))(?![\w-])", re.IGNORECASE),
    # psycopg: could not translate host name "x" / connection to server at "x" / host "x";
    # urllib3 and requests: HTTPSConnectionPool(host='x', port=443)
    re.compile(rf'(?:host name|server at|host)(?:\s+|=)["\']({_LABEL}(?:\.{_LABEL})*)["\']', re.IGNORECASE),
)


def without_hosts(text) -> str:
    """`text` with every host name and address it names replaced by <host>."""
    text = "" if text is None else str(text)
    for pattern in _PATTERNS:
        text = pattern.sub(lambda m: m.group(0).replace(m.group(1), HOST), text)
    return text


RUN_COLUMNS = [
    "run_id", "rag_project", "collection", "backend", "started_at",
    "finished_at", "status", "pipeline_version", "config_id", "embed_model",
    "embed_dims", "chunker", "input_token_limit", "overlap_tokens",
    "docs_new", "docs_changed", "docs_unchanged", "docs_deleted",
    "docs_skipped", "docs_excluded", "docs_failed", "docs_ingested",
    "chunks_written", "chunks_retired",
    "collection_chunks", "embed_calls", "embed_tokens", "embed_seconds",
    "enrich_model", "enrich_calls", "enrich_input_tokens",
    "enrich_output_tokens", "enrich_seconds", "enrich_failed",
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
    "docs_skipped", "docs_excluded", "docs_failed", "docs_ingested",
    "chunks_written", "chunks_retired",
    "collection_chunks", "embed_calls", "embed_tokens", "embed_seconds",
    "enrich_calls", "enrich_input_tokens", "enrich_output_tokens",
    "enrich_seconds", "enrich_failed",
]

EVENT_COLUMNS = [
    "run_id", "doc_id", "source_uri", "source_kind", "status",
    "previous_content_hash", "new_content_hash", "chunk_count_before",
    "chunk_count_after", "error_text",
]

EVENT_NUMERIC = ["chunk_count_before", "chunk_count_after"]

# what a run's document counts are keyed on. close_run writes one
# `docs_<name>` column per entry, so each name needs that column in
# RUN_COLUMNS, in ddl() AND - for a store that already exists - in
# additive_migrations(); a name without all three makes every rag_runs
# write fail on an unknown column.
_STATUS_COUNTS = ("new", "changed", "unchanged", "deleted", "skipped",
                  "excluded",
                  "failed", "ingested")

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
        "schema": "current_schema()",
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
        "schema": "DATABASE()",
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
    docs_skipped      integer DEFAULT 0,
    docs_excluded     integer DEFAULT 0,
    docs_failed       integer DEFAULT 0,
    docs_ingested     integer DEFAULT 0,
    chunks_written    integer DEFAULT 0,
    chunks_retired    integer DEFAULT 0,
    collection_chunks integer DEFAULT 0,
    embed_calls       integer DEFAULT 0,
    embed_tokens      integer DEFAULT 0,
    embed_seconds     double precision DEFAULT 0,
    enrich_model      {short},
    enrich_calls      integer DEFAULT 0,
    enrich_input_tokens  integer DEFAULT 0,
    enrich_output_tokens integer DEFAULT 0,
    enrich_seconds    double precision DEFAULT 0,
    enrich_failed     integer DEFAULT 0,
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

    def additive_migrations(self) -> list:
        """Columns added after the first release, one ALTER each.

        A history table created by an earlier version is already sitting in a
        customer's database with rows in it, and CREATE TABLE IF NOT EXISTS
        will not widen it. Postgres supports ADD COLUMN IF NOT EXISTS but the
        MySQL-protocol stores do not, so ensure_tables() decides which of
        these to run: the ones whose column the table lacks, or - when the
        store cannot say which columns it has - all of them, ignoring the
        duplicate-column error.
        """
        short = "VARCHAR(128)" if self.dialect == "mysql" else self._d["text"]
        return [
            "ALTER TABLE rag_runs ADD COLUMN docs_skipped integer DEFAULT 0",
            # the Enrich stage (2026-08-02): a collection built before it
            # existed keeps answering, and its runs simply record no LLM cost
            f"ALTER TABLE rag_runs ADD COLUMN enrich_model {short}",
            "ALTER TABLE rag_runs ADD COLUMN enrich_calls integer DEFAULT 0",
            "ALTER TABLE rag_runs ADD COLUMN enrich_input_tokens integer DEFAULT 0",
            "ALTER TABLE rag_runs ADD COLUMN enrich_output_tokens integer DEFAULT 0",
            "ALTER TABLE rag_runs ADD COLUMN enrich_seconds double precision DEFAULT 0",
            "ALTER TABLE rag_runs ADD COLUMN enrich_failed integer DEFAULT 0",
            # documents the deployment excludes (a source file with code
            # ingestion off) are counted apart from the ones the pipeline
            # could not read and skipped
            "ALTER TABLE rag_runs ADD COLUMN docs_excluded integer DEFAULT 0",
        ]

    def _existing_columns(self, table: str):
        """The columns `table` has in the schema this connection writes to,
        in lower case - or None when the store does not say.

        None covers a role that may not read information_schema, a driver
        that answers differently and an empty answer alike: the caller then
        falls back to trying every ALTER.
        """
        try:
            with self._conn.cursor() as cursor:
                cursor.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = %s AND table_schema = "
                    + self._d["schema"], [table])
                names = {str(row[0]).lower() for row in cursor.fetchall()}
        except Exception:
            self._rollback()
            return None
        self._commit()
        return names or None

    def ensure_tables(self) -> None:
        with self._conn.cursor() as cursor:
            for statement in self.ddl():
                cursor.execute(statement)
        self._commit()
        existing = self._existing_columns("rag_runs")
        for statement in self.additive_migrations():
            # "ALTER TABLE rag_runs ADD COLUMN <name> <type> ..."
            column = statement.split()[5].lower()
            if existing is not None and column in existing:
                continue
            try:
                with self._conn.cursor() as cursor:
                    cursor.execute(statement)
                self._commit()
            except Exception:
                self._rollback()              # Postgres poisons the txn
                if existing is None:
                    # the columns are not known, so "already present" is the
                    # expected outcome after the first run and is ignored
                    continue
                # The column was missing a moment ago. Either a concurrent
                # run added it in between, which is as good - or the store
                # refused the ALTER (on PostgreSQL only the owner of rag_runs
                # may alter it). That one is raised: the caller then logs
                # the refusal itself and writes nothing, instead of failing
                # later on an INSERT that names a column nobody could add.
                now = self._existing_columns("rag_runs")
                if now is None or column not in now:
                    raise

    def _commit(self):
        try:
            self._conn.commit()
        except Exception:
            pass

    def _write(self, sql: str, params: list) -> None:
        """One history statement, committed - or rolled back when it fails.

        The connection is the vector-store adapter's own. On PostgreSQL a
        statement that fails leaves the open transaction aborted, and every
        later statement on that connection is refused until it is rolled
        back - including the reads that publish history to CAS and whatever
        the adapter does next. So a failed write is undone here, where it
        happened, and the error still reaches the caller.
        """
        try:
            with self._conn.cursor() as cursor:
                cursor.execute(sql, params)
        except Exception:
            self._rollback()
            raise
        self._commit()

    def _rollback(self):
        try:
            self._conn.rollback()
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
        if values.get("error_text"):
            values["error_text"] = without_hosts(values["error_text"])
        self._upsert("rag_runs", "run_id", values)

    def _upsert(self, table: str, key: str, values: dict) -> None:
        columns = list(values)
        marks = ", ".join(["%s"] * len(columns))
        sets = ", ".join(
            f"{c} = " + self._d["excluded"].format(column=c)
            for c in columns if c != key)
        clause = self._d["upsert"].format(key=key, sets=sets)
        self._write(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({marks}) "
            + clause, [values[c] for c in columns])

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
            self._write(
                f"INSERT INTO rag_doc_events ({', '.join(columns)}) VALUES "
                + ", ".join([marks] * len(page)), params)
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
        self._write(
            "INSERT INTO rag_configs (config_id, settings) VALUES (%s, %s) "
            + clause, [config_id, payload])

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


# -- the CAS publish, for a caller without SWAT --------------------------------
#
# RAG - Load Vector Store publishes what `runs()` and `doc_events()` return
# through SWAT: its cas_stage puts the rows into a DataFrame, and upload_frame
# writes that as a CSV file and has CAS read it with the column types the step
# names. The job Ingest-Documents.sas has no SWAT. It writes such a file
# itself and SAS uploads it, with the same types - the three functions below
# are the step's cas_stage up to the upload, so that <project>_RUNS and
# <project>_DOC_EVENTS hold the same columns whichever of the two wrote them.
def cas_types(columns, numeric=()) -> list:
    """(column, CAS type) in the order of `columns`: a measure is a double,
    every other column a varchar - a count staged as text is a category in
    Visual Analytics (see RUN_NUMERIC)."""
    return [(column, "double" if column in numeric else "varchar")
            for column in columns]


def cas_values(rows, columns, numeric=()) -> list:
    """One list of values per row, as the Load step stages them: a measure
    as a float with 0 for a missing or unreadable one, anything else as its
    text with an empty text for None."""
    data = []
    for row in rows:
        record = []
        for column in columns:
            value = row.get(column)
            if column in numeric:
                try:
                    record.append(float(value or 0))
                except Exception:
                    record.append(0.0)
            else:
                record.append("" if value is None else str(value))
        data.append(record)
    return data


def write_cas_file(path: str, rows, columns, numeric=()) -> int:
    """Write `rows` as the CSV file a CAS upload reads and return how many
    there are: the column names in the first line, then a line per row of
    cas_values(), UTF-8, a value quoted only where it has to be. The file
    is written for no rows too, with its first line only."""
    import csv

    data = cas_values(rows, columns, numeric)
    with open(path, "w", encoding="utf-8", errors="replace", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(list(columns))
        writer.writerows(data)
    return len(data)


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
            "error_text": without_hosts(row.get("error_text"))[:500],
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
