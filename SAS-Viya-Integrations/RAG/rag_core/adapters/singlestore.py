# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""SingleStore adapter — the second backend (memsql 9.0.34 probed live).

Same contract as pgvector, including chunk lineage, as-of reads and rollback.
Three things SingleStore does differently, each probed rather than assumed:

* **No NULL tombstone.** There is no `UNIQUE NULLS NOT DISTINCT`, so a live
  row is marked by a SENTINEL `valid_to` of 9999-12-31 and uniqueness is a
  plain `UNIQUE KEY (chunk_id, valid_to)`. It gives the same guarantee — one
  live row per chunk, several generations coexisting — and as a bonus the
  as-of window needs no NULL special case, because the sentinel sorts after
  every real timestamp.

* **No cosine metric.** `COSINE_SIMILARITY` does not exist and the vector
  index rejects `metric_type: COSINE`; only `DOT_PRODUCT` and
  `EUCLIDEAN_DISTANCE` are available. Cosine therefore comes from the
  identity dot(â, b̂) == cos(a, b): vectors are L2-normalized on the way in
  and queries on the way out, which keeps the ANN index usable and makes the
  returned `distance` numerically the same as pgvector's `<=>`.

* **No partial indexes, and no ANN index by default.** A vector index here
  cannot be limited to live rows, and — measured live — it also LOSES rows:
  see `_statements`. Retrieval therefore uses exact KNN unless a deployment
  opts in with schema={"ann": True}. Reported as `ann_index: False` and
  `live_only_index: False` in capabilities() rather than hidden.

Also: `ADD COLUMN IF NOT EXISTS` is a syntax error here, and DATE/TIME
conversion rejects ISO-8601 `T`/`Z` — both handled below.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime
from typing import Optional

from ..filters import compile_sql
from .base import SearchHit, VectorStoreAdapter

_IDENT = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

# a live row's valid_to; later than any real timestamp, so `valid_to > as_of`
# selects live rows as well without a NULL branch
SENTINEL = "9999-12-31 00:00:00"

_SSL_DISABLED = {"disable", "false", "off", "no", "0"}

# (ranking expression, higher-is-better, normalize vectors, index metric_type)
_METRICS = {
    "cosine": ("DOT_PRODUCT", True, True, "DOT_PRODUCT"),
    "ip": ("DOT_PRODUCT", True, False, "DOT_PRODUCT"),
    "l2": ("EUCLIDEAN_DISTANCE", False, False, "EUCLIDEAN_DISTANCE"),
}

_VECTOR_TYPE = re.compile(r"vector\s*\(\s*(\d+)")


def _ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(
            f"invalid collection name {name!r} (expected ^[a-z][a-z0-9_]{{0,62}}$)")
    return f"`{name}`"


def _metric(metric: str) -> tuple:
    try:
        return _METRICS[metric]
    except KeyError:
        raise ValueError(f"unsupported metric {metric!r} for SingleStore "
                         f"(supported: {', '.join(sorted(_METRICS))})") from None


def _normalize(vector) -> list:
    """Unit-length copy of the vector; a zero vector is left alone."""
    values = [float(v) for v in vector]
    norm = math.sqrt(sum(v * v for v in values))
    if not norm:
        return values
    return [v / norm for v in values]


def _vector_literal(vector, normalize: bool) -> str:
    values = _normalize(vector) if normalize else [float(v) for v in vector]
    return "[" + ",".join(repr(v) for v in values) + "]"


def _timestamp(value):
    """A value SingleStore's DATE/TIME conversion accepts.

    `2026-07-30T12:00:00Z` — what the pipeline stamps and what Postgres takes
    happily — is rejected with "Invalid DATE/TIME in type conversion", which
    would fail on the first real chunk.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (datetime, date)):
        return value
    text = str(value).strip().replace("T", " ")
    if text.endswith("Z"):
        text = text[:-1]
    # a trailing numeric offset (+02:00) is not accepted either
    text = re.sub(r"([+-]\d{2}:?\d{2})$", "", text).strip()
    return text or None


class SingleStoreAdapter(VectorStoreAdapter):
    name = "singlestore"

    def __init__(self):
        self._conn = None
        # normalization has to agree between write and read, so the metric a
        # collection was ensured with becomes the default for upsert/search on
        # this adapter. A non-cosine collection addressed from a fresh process
        # must be given the same `metric` on every call.
        self._metric = "cosine"

    # -- connection ---------------------------------------------------------
    def connect(self, config: dict) -> None:
        import singlestoredb  # lazy; present in the SAS compute Python

        sslmode = str(config.get("sslmode") or "").strip().lower()
        self._conn = singlestoredb.connect(
            host=config["host"], port=int(config.get("port", 3306)),
            database=config["dbname"], user=config["user"],
            password=config["password"],
            ssl_disabled=sslmode in _SSL_DISABLED,
            connect_timeout=int(config.get("connect_timeout", 15)),
        )

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _cursor(self):
        if self._conn is None:
            raise RuntimeError("adapter is not connected — call connect() first")
        return self._conn.cursor()

    def _execute(self, sql: str, params=None):
        """One statement, committed. SingleStore is autocommit, so the commit
        is a no-op that keeps the two adapters reading alike."""
        with self._cursor() as cursor:
            cursor.execute(sql, params) if params else cursor.execute(sql)
            return cursor.rowcount

    # -- schema -------------------------------------------------------------
    def _statements(self, name: str, dims: int, metric: str,
                    schema: Optional[dict]) -> list:
        """CREATE TABLE, plus the ANN index only when it was asked for.

        The index is OPT-IN, against the obvious instinct, because measured
        live it loses rows: on a collection of 6 rows the identical query
        returned 1, 2, 1, 0, 1 and 1 rows for LIMIT 1 to 9, and dropping the
        index returned the correct 4 every time. Neither SEARCH_OPTIONS
        ("ef", "k") nor OPTIMIZE TABLE FULL changed it.

        Approximate search is allowed to return an imperfect ORDER. It is not
        allowed to return three fewer chunks than exist, and for retrieval
        that feeds an answer, silently returning nothing is the worst failure
        available - the decision flows on with empty context and no error.
        So exact KNN is the default, and a deployment that needs the index
        for a large collection turns it on deliberately with
        schema={"ann": True} and validates recall on its own data.
        """
        table = _ident(name)
        _, _, _, index_metric = _metric(metric)
        if (schema or {}).get("sparse"):
            raise NotImplementedError(
                "sparse/hybrid retrieval is not implemented for SingleStore "
                "(capabilities() reports sparse: False)")
        statements = [
            f"""CREATE TABLE IF NOT EXISTS {table} (
    chunk_id         VARCHAR(64)  NOT NULL,
    doc_id           VARCHAR(64)  NOT NULL,
    source_uri       TEXT         NOT NULL,
    chunk_index      INT          NOT NULL,
    content          LONGTEXT     NOT NULL,
    content_hash     VARCHAR(64)  NOT NULL,
    extractor        VARCHAR(64)  NOT NULL,
    pipeline_version VARCHAR(64)  NOT NULL,
    ingested_at      DATETIME(6),
    span             JSON,
    heading_path     TEXT,
    tags             JSON,
    prev_id          VARCHAR(64),
    next_id          VARCHAR(64),
    context_header   TEXT,
    entities         JSON,
    relations        JSON,
    run_id           VARCHAR(64),
    config_id        VARCHAR(64),
    embed_model      VARCHAR(128),
    embed_dims       INT,
    valid_from       DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    valid_to         DATETIME(6)  NOT NULL DEFAULT '{SENTINEL}',
    retired_in_run   VARCHAR(64),
    embedding        VECTOR({int(dims)}) NOT NULL,
    SHARD KEY (chunk_id),
    UNIQUE KEY {name}_live_uk (chunk_id, valid_to),
    KEY {name}_doc_idx (doc_id),
    KEY {name}_hist_idx (doc_id, valid_from, valid_to),
    SORT KEY (doc_id)
)"""]
        if (schema or {}).get("ann"):
            statements.append(
                f"ALTER TABLE {table} ADD VECTOR INDEX {name}_ann_idx "
                f"(embedding) INDEX_OPTIONS "
                f'\'{{"index_type":"HNSW_FLAT","metric_type":"{index_metric}"}}\'')
        return statements

    def ddl(self, name: str, dims: int, metric: str = "cosine",
            schema: Optional[dict] = None) -> str:
        """The collection schema, including chunk lineage (design §2b).

        Distribution: `SHARD KEY (chunk_id)` keeps every generation of a chunk
        on one partition, and a unique key must contain the shard key — which
        the live key does. `SORT KEY (doc_id)` is what makes the per-document
        retirement scan cheap on a columnstore.
        """
        return ";\n".join(self._statements(name, dims, metric, schema)) + ";"

    # columns a collection created before lineage existed will not have
    _LINEAGE_COLUMNS = (("run_id", "VARCHAR(64)"), ("config_id", "VARCHAR(64)"),
                        ("embed_model", "VARCHAR(128)"), ("embed_dims", "INT"),
                        ("valid_from", "DATETIME(6) NOT NULL "
                                       "DEFAULT CURRENT_TIMESTAMP(6)"),
                        ("valid_to", f"DATETIME(6) NOT NULL DEFAULT '{SENTINEL}'"),
                        ("retired_in_run", "VARCHAR(64)"))

    def _columns_present(self, name: str) -> set:
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = %s", [name])
            return {str(row[0]).lower() for row in cursor.fetchall()}

    def ensure_collection(self, name: str, dims: int, metric: str = "cosine",
                          schema: Optional[dict] = None) -> None:
        statements = self._statements(name, dims, metric, schema)
        self._metric = metric
        existed = bool(self._columns_present(name))
        self._execute(statements[0])
        if not existed:
            # any ANN index is added once, at creation: re-adding it raises
            # "index already exists" rather than being a no-op
            for statement in statements[1:]:
                self._execute(statement)
        # an EXISTING collection keeps its rows and gains the lineage columns.
        # ADD COLUMN IF NOT EXISTS is a syntax error here, so what is missing
        # is read from the catalog first.
        present = self._columns_present(name)
        for column, coltype in self._LINEAGE_COLUMNS:
            if column not in present:
                self._execute(f"ALTER TABLE {_ident(name)} "
                              f"ADD COLUMN {column} {coltype}")

    # -- data ---------------------------------------------------------------
    _COLUMNS = ["chunk_id", "doc_id", "source_uri", "chunk_index", "content",
                "content_hash", "extractor", "pipeline_version", "ingested_at",
                "span", "heading_path", "tags", "prev_id", "next_id",
                "context_header", "entities", "relations",
                "run_id", "config_id", "embed_model", "embed_dims", "embedding"]
    _JSON_COLUMNS = {"span", "tags", "entities", "relations"}
    _LIVE = f"valid_to = '{SENTINEL}'"
    _DIALECT = "mysql"

    def _row(self, record: dict, normalize: bool) -> tuple:
        values = []
        for column in self._COLUMNS:
            value = record.get(column)
            if column in self._JSON_COLUMNS:
                values.append(json.dumps(value) if value is not None else
                              ("{}" if column == "tags" else None))
            elif column == "embedding":
                values.append(_vector_literal(value, normalize))
            elif column == "ingested_at":
                values.append(_timestamp(value))
            else:
                values.append(value)
        return tuple(values)

    def upsert(self, collection: str, records: list,
               metric: Optional[str] = None) -> int:
        if not records:
            return 0
        _, _, normalize, _ = _metric(metric or self._metric)
        table = _ident(collection)
        columns = ", ".join(self._COLUMNS)
        marks = "(" + ", ".join(["%s"] * len(self._COLUMNS)) + ")"
        updates = ", ".join(f"{c} = VALUES({c})"
                            for c in self._COLUMNS if c != "chunk_id")
        written = 0
        for start in range(0, len(records), 200):
            page = records[start:start + 200]
            self._execute(
                f"INSERT INTO {table} ({columns}) VALUES "
                + ", ".join([marks] * len(page))
                + f" ON DUPLICATE KEY UPDATE {updates}",
                [value for record in page
                 for value in self._row(record, normalize)])
            # MySQL's affected-rows counts an update as 2 and an unchanged row
            # as 0, so it cannot answer "how many chunks were written" — the
            # batch either succeeded whole or raised
            written += len(page)
        return written

    def _in_clause(self, column: str, values: list) -> tuple:
        marks = ", ".join(["%s"] * len(values))
        return f"{column} IN ({marks})", list(values)

    def retire(self, collection: str, ids: Optional[list] = None,
               filter: Optional[dict] = None, run_id: Optional[str] = None,
               keep_ids: Optional[list] = None) -> int:
        """Tombstone live rows instead of deleting them (see PgVectorAdapter).

        The sentinel is what makes this work without NULLs: setting valid_to
        to now() both marks the row retired and frees the live key for the
        next generation.
        """
        table = _ident(collection)
        clauses = [self._LIVE]
        params: list = []
        if ids:
            clause, values = self._in_clause("chunk_id", ids)
            clauses.append(clause)
            params.extend(values)
        if keep_ids is not None:
            if keep_ids:
                clause, values = self._in_clause("chunk_id", keep_ids)
                clauses.append(f"NOT ({clause})")
                params.extend(values)
        if filter:
            condition, filter_params = compile_sql(filter, self._DIALECT)
            clauses.append(f"({condition})")
            params.extend(filter_params)
        if not ids and keep_ids is None and not filter:
            raise ValueError("retire() needs ids, keep_ids or a filter - refusing "
                             "to tombstone a whole collection implicitly")
        return self._execute(
            f"UPDATE {table} SET valid_to = NOW(6), retired_in_run = %s "
            f"WHERE {' AND '.join(clauses)}", [run_id, *params])

    def search(self, collection: str, vector: list, k: int = 5,
               filter: Optional[dict] = None, mode: str = "vector",
               query_text: Optional[str] = None,
               raw_filter: Optional[str] = None,
               as_of: Optional[str] = None, metric: Optional[str] = None) -> list:
        """Search the LIVE slice, or the slice as it stood at `as_of`.

        The live filter is a WHERE predicate, not an index predicate, so
        results are always correct — but with retired generations in the
        collection the ANN index has more rows to walk than pgvector's
        partial index does.
        """
        if mode != "vector":
            raise NotImplementedError("hybrid search is opt-in and lands in P2 (OQ15)")
        function, higher_is_better, normalize, _ = _metric(metric or self._metric)
        table = _ident(collection)
        condition, params = compile_sql(filter or {}, self._DIALECT)
        if as_of:
            stamp = _timestamp(as_of)
            condition = f"({condition}) AND valid_from <= %s AND valid_to > %s"
            params = [*params, stamp, stamp]
        else:
            condition = f"({condition}) AND {self._LIVE}"
        if raw_filter:
            condition = f"({condition}) AND ({raw_filter})"   # documented escape hatch
        literal = _vector_literal(vector, normalize)
        columns = ", ".join(c for c in self._COLUMNS if c != "embedding")
        # the query vector needs an explicit cast: a bare string parameter is
        # accepted where the target column types it, but not as a function
        # argument
        cast = f"%s :> VECTOR({len(vector)})"
        with self._cursor() as cursor:
            cursor.execute(
                f"SELECT {columns}, {function}(embedding, {cast}) AS score "
                f"FROM {table} WHERE {condition} "
                f"ORDER BY score {'DESC' if higher_is_better else 'ASC'} LIMIT %s",
                [literal, *params, int(k)])
            names = [d[0] for d in cursor.description]
            rows = cursor.fetchall()
        hits = []
        for row in rows:
            record = dict(zip(names, row))
            score = float(record.pop("score"))
            for column in self._JSON_COLUMNS:
                if isinstance(record.get(column), (str, bytes)):
                    try:
                        record[column] = json.loads(record[column])
                    except (ValueError, TypeError):
                        pass
            # cosine similarity of unit vectors IS 1 - the pgvector distance,
            # so `distance` means the same thing on both backends
            record["distance"] = (1.0 - score) if higher_is_better else score
            hits.append(SearchHit(record["chunk_id"],
                                  score if higher_is_better else -score, record))
        return hits

    def delete(self, collection: str, ids: Optional[list] = None,
               filter: Optional[dict] = None) -> int:
        """Physically remove rows — the erasure path (see PgVectorAdapter).

        A filter on doc_id takes the live row and every retired generation
        with it. Irreversible; retire() is the routine path.
        """
        table = _ident(collection)
        if ids:
            clause, values = self._in_clause("chunk_id", ids)
            return self._execute(f"DELETE FROM {table} WHERE {clause}", values)
        if filter:
            condition, params = compile_sql(filter, self._DIALECT)
            return self._execute(f"DELETE FROM {table} WHERE {condition}", params)
        raise ValueError("delete() needs ids or a filter — refusing to empty "
                         "a collection implicitly (use drop_collection)")

    def prune_history(self, collection: str, before: str,
                      dry_run: bool = False) -> int:
        """Drop retired generations tombstoned before `before` (retention).

        This matters more here than on pgvector: the ANN index cannot be
        limited to live rows, so retained history is index the search has to
        walk. Pruning is how a SingleStore collection keeps its retrieval
        cost flat.
        """
        if not str(before or "").strip():
            raise ValueError("prune_history() needs a cutoff timestamp — "
                             "refusing to drop all history implicitly")
        table = _ident(collection)
        cutoff = _timestamp(before)
        condition = f"valid_to <> '{SENTINEL}' AND valid_to < %s"
        if dry_run:
            with self._cursor() as cursor:
                cursor.execute(
                    f"SELECT count(*) FROM {table} WHERE {condition}", [cutoff])
                return int(cursor.fetchone()[0])
        return self._execute(f"DELETE FROM {table} WHERE {condition}", [cutoff])

    def drop_collection(self, name: str) -> None:
        self._execute(f"DROP TABLE IF EXISTS {_ident(name)}")

    def list_collections(self) -> list:
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = DATABASE() ORDER BY table_name")
            return [row[0] for row in cursor.fetchall()]

    def count(self, collection: str, filter: Optional[dict] = None,
              include_retired: bool = False, as_of: Optional[str] = None) -> int:
        condition, params = compile_sql(filter or {}, self._DIALECT)
        if as_of:
            stamp = _timestamp(as_of)
            condition = f"({condition}) AND valid_from <= %s AND valid_to > %s"
            params = [*params, stamp, stamp]
        elif not include_retired:
            condition = f"({condition}) AND {self._LIVE}"
        with self._cursor() as cursor:
            cursor.execute(
                f"SELECT count(*) FROM {_ident(collection)} WHERE {condition}",
                params)
            return int(cursor.fetchone()[0])

    def dimensions(self, collection: str) -> int:
        """The collection's vector width, read from the column type.

        SingleStore reports it as `vector(384, F32)` in the catalog.
        """
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT column_type FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = %s "
                "AND column_name = 'embedding'", [collection])
            row = cursor.fetchone()
        if not row:
            return 0
        found = _VECTOR_TYPE.search(str(row[0]).lower())
        return int(found.group(1)) if found else 0

    def restore(self, collection: str, run_id: str) -> int:
        """Undo one run's retirements (see PgVectorAdapter.restore).

        Two statements, and SingleStore will not wrap DDL-free multi-statement
        work in a cluster-wide transaction, so a failure between them leaves
        the first applied — rerunning restore() is safe and idempotent.
        """
        table = _ident(collection)
        restored = self._execute(
            f"UPDATE {table} SET valid_to = '{SENTINEL}', retired_in_run = NULL "
            f"WHERE retired_in_run = %s", [run_id])
        self._execute(
            f"UPDATE {table} SET valid_to = NOW(6), retired_in_run = %s "
            f"WHERE run_id = %s AND {self._LIVE}",
            [f"rollback-of-{run_id}", run_id])
        return restored

    def _table_exists(self, name: str) -> bool:
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = %s", [name])
            return bool(int(cursor.fetchone()[0]))

    def cutover(self, alias: str, new_collection: str) -> None:
        """Rename, like pgvector — but in two statements, not one transaction.

        SingleStore has no transactional DDL, so between the two renames the
        alias name does not exist. Callers that cannot tolerate that window
        should cut over when nothing is retrieving.
        """
        _ident(alias), _ident(new_collection)   # validate both before any DDL
        if not self._table_exists(new_collection):
            raise ValueError(f"cutover target {new_collection!r} does not exist")
        if self._table_exists(alias):
            self._execute(f"ALTER TABLE {_ident(alias)} RENAME TO "
                          f"{_ident(alias + '_retired')}")
        self._execute(f"ALTER TABLE {_ident(new_collection)} RENAME TO "
                      f"{_ident(alias)}")

    def capabilities(self) -> dict:
        # live_only_index is the one honest gap against pgvector: without
        # partial indexes the ANN index spans retired generations too, so
        # retrieval cost grows with retained history
        return {"alias": False, "sparse": False, "hybrid": False,
                "namespace": False, "cutover": "rename",
                "history": True, "as_of": True, "rollback": True,
                "ann_index": False, "live_only_index": False,
                "normalized_vectors": True, "transactional_ddl": False}
