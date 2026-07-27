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

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _cursor(self):
        if self._conn is None:
            raise RuntimeError("adapter is not connected — call connect() first")
        return self._conn.cursor()

    # -- schema -------------------------------------------------------------
    def ddl(self, name: str, dims: int, metric: str = "cosine",
            schema: Optional[dict] = None) -> str:
        table = _ident(name)
        opclass, _ = _METRIC_OPS[metric]
        sparse = bool((schema or {}).get("sparse"))
        tsv_line = ",\n    tsv              tsvector" if sparse else ""
        statements = [
            "CREATE EXTENSION IF NOT EXISTS vector;",
            f"""CREATE TABLE IF NOT EXISTS {table} (
    chunk_id         text PRIMARY KEY,
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
    embedding        vector({int(dims)}) NOT NULL{tsv_line}
);""",
            f"CREATE INDEX IF NOT EXISTS {name}_doc_idx ON {table} (doc_id);",
            f"CREATE INDEX IF NOT EXISTS {name}_hnsw_idx ON {table} "
            f"USING hnsw (embedding {opclass});",
        ]
        if sparse:
            statements.append(
                f"CREATE INDEX IF NOT EXISTS {name}_tsv_idx ON {table} USING gin (tsv);")
        return "\n".join(statements)

    def ensure_collection(self, name: str, dims: int, metric: str = "cosine",
                          schema: Optional[dict] = None) -> None:
        with self._cursor() as cur:
            cur.execute(self.ddl(name, dims, metric, schema))
        self._conn.commit()

    # -- data ---------------------------------------------------------------
    _COLUMNS = ["chunk_id", "doc_id", "source_uri", "chunk_index", "content",
                "content_hash", "extractor", "pipeline_version", "ingested_at",
                "span", "heading_path", "tags", "prev_id", "next_id",
                "context_header", "entities", "relations", "embedding"]
    _JSON_COLUMNS = {"span", "tags", "entities", "relations"}

    def _row(self, record: dict) -> tuple:
        values = []
        for column in self._COLUMNS:
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
        columns = ", ".join(self._COLUMNS)
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in self._COLUMNS if c != "chunk_id")
        template = "(" + ", ".join(
            "%s::vector" if c == "embedding" else "%s" for c in self._COLUMNS) + ")"
        with self._cursor() as cur:
            execute_values(
                cur,
                f"INSERT INTO {table} ({columns}) VALUES %s "
                f"ON CONFLICT (chunk_id) DO UPDATE SET {updates}",
                [self._row(r) for r in records], template=template, page_size=200,
            )
            affected = cur.rowcount
        self._conn.commit()
        return affected

    def search(self, collection: str, vector: list, k: int = 5,
               filter: Optional[dict] = None, mode: str = "vector",
               query_text: Optional[str] = None,
               raw_filter: Optional[str] = None) -> list:
        if mode != "vector":
            raise NotImplementedError("hybrid search is opt-in and lands in P2 (OQ15)")
        table = _ident(collection)
        condition, params = compile_sql(filter or {})
        if raw_filter:
            condition = f"({condition}) AND ({raw_filter})"   # documented escape hatch
        vec_literal = "[" + ",".join(repr(float(v)) for v in vector) + "]"
        columns = ", ".join(c for c in self._COLUMNS if c != "embedding")
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

    def drop_collection(self, name: str) -> None:
        with self._cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {_ident(name)}")
        self._conn.commit()

    def list_collections(self) -> list:
        with self._cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema() ORDER BY table_name")
            return [r[0] for r in cur.fetchall()]

    def count(self, collection: str, filter: Optional[dict] = None) -> int:
        condition, params = compile_sql(filter or {})
        with self._cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {_ident(collection)} WHERE {condition}",
                        params)
            return int(cur.fetchone()[0])

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
        return {"alias": False, "sparse": True, "hybrid": False,
                "namespace": False, "cutover": "rename"}
