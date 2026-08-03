# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""pgvector adapter — the P1 reference backend (Postgres 15.8 / pgvector 0.8.5 probed).

Collection = one table. Cutover = transactional rename (pgvector has no alias
concept — design §4). Scores: cosine distance flipped to similarity so higher
is better. All filters and values are bound parameters; identifiers pass a
strict allowlist regex and are double-quoted.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from ..filters import compile_sql
from .base import SearchHit, VectorStoreAdapter

_IDENT = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

_SSLMODE_BOOLEANS = {"false": "disable", "off": "disable", "no": "disable",
                     "true": "require", "yes": "require", "on": "require"}

_METRIC_OPS = {"cosine": ("vector_cosine_ops", "<=>"),
               "l2": ("vector_l2_ops", "<->"),
               "ip": ("vector_ip_ops", "<#>")}


def _ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(
            f"invalid collection name {name!r} (expected ^[a-z][a-z0-9_]{{0,62}}$)")
    return f'"{name}"'


class PgVectorAdapter(VectorStoreAdapter):
    name = "pgvector"

    def __init__(self):
        self._conn = None

    # -- connection ---------------------------------------------------------
    def connect(self, config: dict) -> None:
        import psycopg2  # lazy; requires is checked by the caller

        sslmode = str(config.get("sslmode") or "prefer").lower()
        sslmode = _SSLMODE_BOOLEANS.get(sslmode, sslmode)
        self._conn = psycopg2.connect(
            host=config["host"], port=int(config.get("port", 5432)),
            dbname=config["dbname"], user=config["user"],
            password=config["password"], sslmode=sslmode,
            connect_timeout=int(config.get("connect_timeout", 15)),
        )
        self._conn.autocommit = False

    #: the SQL dialect the history tables use on this backend
    HISTORY_DIALECT = "postgres"

    def raw_connection(self):
        """The open connection, for the run-history tables.

        History lives beside the chunks it describes and shares their
        transaction scope, so it reuses this connection rather than opening a
        second one.
        """
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _cursor(self):
        if self._conn is None:
            raise RuntimeError("adapter is not connected — call connect() first")
        return self._conn.cursor()

    # -- schema -------------------------------------------------------------
    def _statements(self, name: str, dims: int, metric: str = "cosine",
                    schema: Optional[dict] = None) -> list:
        """The collection schema, including chunk lineage (design §2b).

        A chunk row is valid for a period rather than forever: `valid_from`
        / `valid_to` plus `retired_in_run` make it possible to ask what the
        collection contained on a past date, and to roll back a bad
        ingestion. That means several generations of one `chunk_id` coexist,
        so the key is a surrogate and uniqueness applies to the LIVE row -
        `UNIQUE NULLS NOT DISTINCT (chunk_id, valid_to)`, which needs
        Postgres 15 or newer (15.8 probed).

        Both the vector index and the doc index are PARTIAL, over live rows
        only, so retained history never slows retrieval down.
        """
        table = _ident(name)
        opclass, _ = _METRIC_OPS[metric]
        sparse = bool((schema or {}).get("sparse"))
        tsv_line = ",\n    tsv              tsvector" if sparse else ""
        statements = [
            "CREATE EXTENSION IF NOT EXISTS vector;",
            f"""CREATE TABLE IF NOT EXISTS {table} (
    id               bigserial PRIMARY KEY,
    chunk_id         text NOT NULL,
    doc_id           text NOT NULL,
    source_uri       text NOT NULL,
    chunk_index      integer NOT NULL,
    content          text NOT NULL,
    content_hash     text NOT NULL,
    extractor        text NOT NULL,
    pipeline_version text NOT NULL,
    ingested_at      timestamptz NOT NULL,
    span             jsonb,
    heading_path     text,
    tags             jsonb NOT NULL DEFAULT '{{}}',
    prev_id          text,
    next_id          text,
    context_header   text,
    entities         jsonb,
    relations        jsonb,
    run_id           text,
    config_id        text,
    embed_model      text,
    embed_dims       integer,
    valid_from       timestamptz NOT NULL DEFAULT now(),
    valid_to         timestamptz,
    retired_in_run   text,
    embedding        vector({int(dims)}) NOT NULL{tsv_line},
    CONSTRAINT {name}_live_uk UNIQUE NULLS NOT DISTINCT (chunk_id, valid_to)
);""",
            f"CREATE INDEX IF NOT EXISTS {name}_doc_idx ON {table} (doc_id) "
            f"WHERE valid_to IS NULL;",
            f"CREATE INDEX IF NOT EXISTS {name}_hnsw_idx ON {table} "
            f"USING hnsw (embedding {opclass}) WHERE valid_to IS NULL;",
            f"CREATE INDEX IF NOT EXISTS {name}_hist_idx ON {table} "
            f"(doc_id, valid_from, valid_to);",
        ]
        if sparse:
            statements.append(
                f"CREATE INDEX IF NOT EXISTS {name}_tsv_idx ON {table} USING gin (tsv);")
        return statements

    def ddl(self, name: str, dims: int, metric: str = "cosine",
            schema: Optional[dict] = None) -> str:
        """The collection schema as one script (the governance artifact)."""
        return "\n".join(self._statements(name, dims, metric, schema))

    def _live_constraint(self, name: str) -> str:
        return f"{name}_live_uk"

    # columns a collection created before lineage existed will not have
    _LINEAGE_COLUMNS = (("run_id", "text"), ("config_id", "text"),
                        ("embed_model", "text"), ("embed_dims", "integer"),
                        ("valid_from", "timestamptz NOT NULL DEFAULT now()"),
                        ("valid_to", "timestamptz"), ("retired_in_run", "text"))

    def ensure_collection(self, name: str, dims: int, metric: str = "cosine",
                          schema: Optional[dict] = None) -> None:
        """Create the collection, or bring an older one up to the schema.

        Order matters and used to be wrong: the indexes are PARTIAL on
        `valid_to IS NULL`, so running the whole DDL first made upgrading a
        pre-lineage collection fail on a column that the very next statement
        would have added. Columns first, then the live-row constraint, then
        the indexes that depend on both.
        """
        self._forget_attributes(name)   # this call may CREATE the table
        statements = self._statements(name, dims, metric, schema)
        table = _ident(name)
        with self._cursor() as cur:
            for statement in statements[:2]:      # extension, then the table
                cur.execute(statement)
            # an EXISTING collection keeps its rows and gains the lineage
            # columns, so history starts from the upgrade rather than
            # forcing a re-embed of everything already loaded
            for column, coltype in self._LINEAGE_COLUMNS:
                cur.execute(f"ALTER TABLE {table} "
                            f"ADD COLUMN IF NOT EXISTS {column} {coltype}")
            # the live-row uniqueness lives in CREATE TABLE, so a collection
            # that predates lineage never gains it - and upsert names it as
            # its ON CONFLICT target, which would then fail at load time
            constraint = self._live_constraint(name)
            cur.execute("SELECT 1 FROM pg_constraint WHERE conname = %s",
                        [constraint])
            if not cur.fetchone():
                cur.execute(f"ALTER TABLE {table} ADD CONSTRAINT {constraint} "
                            f"UNIQUE NULLS NOT DISTINCT (chunk_id, valid_to)")
            for statement in statements[2:]:
                # IF NOT EXISTS keeps whatever index already carries the name,
                # so an upgraded collection would hold on to its FULL index
                # while retrieval filters on valid_to - and an unfiltered ANN
                # index behind a filtered query is what loses rows. Replace it.
                named = re.search(r"CREATE INDEX IF NOT EXISTS (\w+)", statement)
                if named and "WHERE valid_to IS NULL" in statement:
                    cur.execute("SELECT indexdef FROM pg_indexes "
                                "WHERE indexname = %s", [named.group(1)])
                    existing = cur.fetchone()
                    if existing and "valid_to IS NULL" not in str(existing[0]):
                        cur.execute(f"DROP INDEX IF EXISTS {named.group(1)}")
                cur.execute(statement)
        self._conn.commit()

    # -- data ---------------------------------------------------------------
    _COLUMNS = ["chunk_id", "doc_id", "source_uri", "chunk_index", "content",
                "content_hash", "extractor", "pipeline_version", "ingested_at",
                "span", "heading_path", "tags", "prev_id", "next_id",
                "context_header", "entities", "relations",
                "run_id", "config_id", "embed_model", "embed_dims", "embedding"]
    _JSON_COLUMNS = {"span", "tags", "entities", "relations"}
    _LIVE = "valid_to IS NULL"

    def _table(self, collection: str) -> str:
        return _ident(collection)

    def _row(self, record: dict, columns=None) -> tuple:
        values = []
        for column in (columns or self._COLUMNS):
            value = record.get(column)
            if column in self._JSON_COLUMNS:
                values.append(json.dumps(value) if value is not None else
                              ("{}" if column == "tags" else None))
            elif column == "embedding":
                values.append("[" + ",".join(repr(float(v)) for v in value) + "]")
            else:
                values.append(value)
        return tuple(values)

    def upsert(self, collection: str, records: list) -> int:
        if not records:
            return 0
        from psycopg2.extras import execute_values

        table = _ident(collection)
        # whatever the Enrich stage added to THIS collection rides along
        all_columns = self._columns_for(collection)
        columns = ", ".join(all_columns)
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in all_columns if c != "chunk_id")
        template = "(" + ", ".join(
            "%s::vector" if c == "embedding" else "%s" for c in all_columns) + ")"
        with self._cursor() as cur:
            # the conflict target is the LIVE-row constraint, named explicitly
            # rather than inferred: with NULLS NOT DISTINCT the inference rules
            # are easy to get subtly wrong, and re-loading identical content
            # must refresh the live row rather than add a second one
            execute_values(
                cur,
                f"INSERT INTO {table} ({columns}) VALUES %s "
                f"ON CONFLICT ON CONSTRAINT {collection}_live_uk "
                f"DO UPDATE SET {updates}",
                [self._row(r, all_columns) for r in records],
                template=template, page_size=200,
            )
            affected = cur.rowcount
        self._conn.commit()
        return affected

    def retire(self, collection: str, ids: Optional[list] = None,
               filter: Optional[dict] = None, run_id: Optional[str] = None,
               keep_ids: Optional[list] = None) -> int:
        """Tombstone live rows instead of deleting them.

        This is what the Load step calls. Physical deletion would make
        point-in-time reconstruction impossible and leave nothing to roll
        back to after a bad ingestion; `delete()` stays available for the
        erasure path, where removing the bytes is the point.
        """
        table = _ident(collection)
        clauses = [self._LIVE]
        params: list = []
        if ids:
            clauses.append("chunk_id = ANY(%s)")
            params.append(list(ids))
        if keep_ids is not None:
            clauses.append("NOT (chunk_id = ANY(%s))")
            params.append(list(keep_ids))
        if filter:
            condition, filter_params = compile_sql(filter)
            clauses.append(f"({condition})")
            params.extend(filter_params)
        if not ids and keep_ids is None and not filter:
            raise ValueError("retire() needs ids, keep_ids or a filter - refusing "
                             "to tombstone a whole collection implicitly")
        with self._cursor() as cur:
            cur.execute(
                f"UPDATE {table} SET valid_to = now(), retired_in_run = %s "
                f"WHERE {' AND '.join(clauses)}",
                [run_id, *params],
            )
            affected = cur.rowcount
        self._conn.commit()
        return affected

    def search(self, collection: str, vector: list, k: int = 5,
               filter: Optional[dict] = None, mode: str = "vector",
               query_text: Optional[str] = None,
               raw_filter: Optional[str] = None,
               as_of: Optional[str] = None) -> list:
        """Search the LIVE slice, or the slice as it stood at `as_of`.

        Retrieval must never see retired chunks - that is the whole point of
        tombstoning rather than deleting. `as_of` (an ISO timestamp) answers
        "what would this query have returned last month".
        """
        if mode != "vector":
            raise NotImplementedError("hybrid search is opt-in and lands in P2 (OQ15)")
        table = _ident(collection)
        condition, params = compile_sql(filter or {})
        if as_of:
            condition = (f"({condition}) AND valid_from <= %s::timestamptz "
                         f"AND (valid_to IS NULL OR valid_to > %s::timestamptz)")
            params = [*params, as_of, as_of]
        else:
            condition = f"({condition}) AND {self._LIVE}"
        if raw_filter:
            condition = f"({condition}) AND ({raw_filter})"   # documented escape hatch
        vec_literal = "[" + ",".join(repr(float(v)) for v in vector) + "]"
        columns = ", ".join(c for c in self._columns_for(collection)
                            if c != "embedding")
        with self._cursor() as cur:
            cur.execute(
                f"SELECT {columns}, (embedding <=> %s::vector) AS distance "
                f"FROM {table} WHERE {condition} ORDER BY distance ASC LIMIT %s",
                [vec_literal, *params, int(k)],
            )
            names = [d[0] for d in cur.description]
            hits = []
            for row in cur.fetchall():
                record = dict(zip(names, row))
                distance = float(record.pop("distance"))
                hits.append(SearchHit(record["chunk_id"], 1.0 - distance, record))
        self._conn.commit()
        return hits

    def delete(self, collection: str, ids: Optional[list] = None,
               filter: Optional[dict] = None) -> int:
        """Physically remove rows — the erasure path.

        Unlike retire() this ignores lineage entirely: a filter on doc_id
        takes the live row AND every retired generation with it, which is
        what erasure has to mean. It is irreversible; the routine path for a
        changed or vanished document is retire().
        """
        table = _ident(collection)
        with self._cursor() as cur:
            if ids:
                marks = ", ".join(["%s"] * len(ids))
                cur.execute(f"DELETE FROM {table} WHERE chunk_id IN ({marks})", ids)
            elif filter:
                condition, params = compile_sql(filter)
                cur.execute(f"DELETE FROM {table} WHERE {condition}", params)
            else:
                raise ValueError("delete() needs ids or a filter — refusing to empty "
                                 "a collection implicitly (use drop_collection)")
            affected = cur.rowcount
        self._conn.commit()
        return affected

    def prune_history(self, collection: str, before: str,
                      dry_run: bool = False) -> int:
        """Drop retired generations that were tombstoned before `before`.

        Retention, not erasure: live rows are never touched, so retrieval
        cannot change — only how far back the collection can be read. This is
        what stops a frequently re-ingested corpus from growing without
        bound.
        """
        if not str(before or "").strip():
            raise ValueError("prune_history() needs a cutoff timestamp — "
                             "refusing to drop all history implicitly")
        table = _ident(collection)
        condition = "valid_to IS NOT NULL AND valid_to < %s::timestamptz"
        with self._cursor() as cur:
            if dry_run:
                cur.execute(f"SELECT count(*) FROM {table} WHERE {condition}",
                            [before])
                affected = int(cur.fetchone()[0])
            else:
                cur.execute(f"DELETE FROM {table} WHERE {condition}", [before])
                affected = cur.rowcount
        self._conn.commit()
        return affected

    def drop_collection(self, name: str) -> None:
        self._forget_attributes(name)
        with self._cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {_ident(name)}")
        self._conn.commit()

    def list_collections(self) -> list:
        with self._cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema() ORDER BY table_name")
            return [r[0] for r in cur.fetchall()]

    def count(self, collection: str, filter: Optional[dict] = None,
              include_retired: bool = False, as_of: Optional[str] = None) -> int:
        condition, params = compile_sql(filter or {})
        if as_of:
            condition = (f"({condition}) AND valid_from <= %s::timestamptz "
                         f"AND (valid_to IS NULL OR valid_to > %s::timestamptz)")
            params = [*params, as_of, as_of]
        elif not include_retired:
            condition = f"({condition}) AND {self._LIVE}"
        with self._cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {_ident(collection)} WHERE {condition}",
                        params)
            return int(cur.fetchone()[0])

    def dimensions(self, collection: str) -> int:
        """The collection's vector width, read from the column itself.

        Register Setup needs it to emit the DDL artifact, and nothing else in
        the pipeline carries it: dimensions follow from the embedding model,
        so they are deliberately not part of the configuration fingerprint.
        For pgvector the width lives in the column's type modifier.
        """
        with self._cursor() as cur:
            cur.execute(
                "SELECT a.atttypmod FROM pg_attribute a "
                "JOIN pg_class c ON c.oid = a.attrelid "
                "WHERE c.relname = %s AND a.attname = 'embedding' "
                "AND a.attnum > 0", [collection])
            row = cur.fetchone()
        self._conn.commit()
        return int(row[0]) if row and row[0] and int(row[0]) > 0 else 0

    def restore(self, collection: str, run_id: str) -> int:
        """Undo one run's retirements - the rollback the report asked for.

        Rows this run tombstoned come back live; rows this run INSERTED are
        tombstoned in turn, so the collection returns to its prior state.
        """
        table = _ident(collection)
        with self._cursor() as cur:
            cur.execute(f"UPDATE {table} SET valid_to = NULL, retired_in_run = NULL "
                        f"WHERE retired_in_run = %s", [run_id])
            restored = cur.rowcount
            cur.execute(f"UPDATE {table} SET valid_to = now(), "
                        f"retired_in_run = %s WHERE run_id = %s AND {self._LIVE}",
                        [f"rollback-of-{run_id}", run_id])
        self._conn.commit()
        return restored

    def cutover(self, alias: str, new_collection: str) -> None:
        """Transactional rename: alias table (if any) steps aside, new one takes the name."""
        _ident(alias), _ident(new_collection)  # validate both before any DDL
        with self._cursor() as cur:
            cur.execute("SELECT to_regclass(%s), to_regclass(%s)", (alias, new_collection))
            alias_exists, new_exists = cur.fetchone()
            if new_exists is None:
                raise ValueError(f"cutover target {new_collection!r} does not exist")
            if alias_exists is not None:
                cur.execute(f"ALTER TABLE {_ident(alias)} RENAME TO "
                            f"{_ident(alias + '_retired')}")
            cur.execute(f"ALTER TABLE {_ident(new_collection)} RENAME TO {_ident(alias)}")
        self._conn.commit()

    def capabilities(self) -> dict:
        # sparse is FALSE deliberately: the tsv column and its index exist in
        # the DDL but nothing populates them, and advertising a capability
        # that silently returns nothing is worse than not having it (the
        # hybrid path lands with OQ15)
        return {"alias": False, "sparse": False, "hybrid": False,
                "namespace": False, "cutover": "rename",
                "history": True, "as_of": True, "rollback": True}
