# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Vector store adapter interface (design §4).

Scores are normalized HIGHER-IS-BETTER on every backend (fixes the portal
pgVector direction bug); absolute scores are never comparable across backends.
`ddl()` returns what ensure_collection would execute — the vector-store-ddl.sql
governance artifact and the DBA pre-creation path. `cutover()` is the §2
collection-versioning switch; backends without aliases implement it as a
transactional rename or pointer and say so in capabilities().
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Optional


class SearchHit:
    __slots__ = ("chunk_id", "score", "record")

    def __init__(self, chunk_id: str, score: float, record: dict):
        self.chunk_id = chunk_id
        self.score = score          # higher is better, always
        self.record = record        # full canonical-schema round-trip


class VectorStoreAdapter(ABC):
    name: str = "base"

    # -- enrichment attribute columns ---------------------------------------
    #
    # What the Enrich stage extracts is stored in REAL columns, one per output,
    # so it can be selected, filtered and aggregated like any other column
    # rather than being buried in a JSON blob (owner decision 2026-08-03).
    # The mechanics are identical on both backends - only the type names and
    # the schema predicate differ - so they live here.

    #: Prompt Builder output type -> SQL type. Overridden per dialect.
    _ATTRIBUTE_SQL = {"string": "text", "decimal": "double precision"}
    #: Written alongside the first stored output: which prompt, at which
    #: version, produced this chunk's enrichment.
    _ENRICH_STAMP = "enrich_version"
    #: A column name this adapter will create. Anything else is refused rather
    #: than interpolated - these names reach DDL, so they are never user text.
    _ATTRIBUTE_OK = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

    def _schema_predicate(self) -> str:
        """SQL for "in the schema this connection writes to"."""
        return "table_schema = current_schema()"

    def _existing_columns(self, collection: str) -> set:
        with self._cursor() as cur:            # type: ignore[attr-defined]
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                f"WHERE table_name = %s AND {self._schema_predicate()}",
                [collection])
            return {str(row[0]).lower() for row in cur.fetchall()}

    def _canonical_columns(self) -> set:
        """Every column the pipeline itself owns on a chunk table."""
        return {c.lower() for c in getattr(self, "_COLUMNS", [])} | {
            "id", "valid_from", "valid_to", "retired_in_run", "tsv"}

    def attribute_columns(self, collection: str) -> list:
        """The extra columns THIS collection carries, cached per collection.

        Read from the table rather than from the setup: a collection keeps
        columns from prompts it no longer runs, and a write that named a
        column the table does not have would fail the whole batch.
        """
        cache = getattr(self, "_attribute_cache", None)
        if cache is None:
            cache = {}
            self._attribute_cache = cache
        if collection not in cache:
            cache[collection] = sorted(
                self._existing_columns(collection) - self._canonical_columns())
        return cache[collection]

    def _forget_attributes(self, collection: str = "") -> None:
        """Drop the cached column list — this table's shape just changed.

        Found live 2026-08-03: without this, dropping a collection and
        recreating it in the same process left the cache naming columns the
        new table does not have, and the next write failed the whole batch
        with "Unknown column". Reachable from purge, cutover and re-register,
        none of which open a new connection.
        """
        cache = getattr(self, "_attribute_cache", None)
        if cache is None:
            return
        if collection:
            cache.pop(collection, None)
        else:
            cache.clear()

    def sync_attributes(self, collection: str, wanted: dict) -> dict:
        """Add a column for every stored output that does not have one.

        Returns `{"added": [...], "kept": [...]}` — `kept` being columns the
        collection already has that this setup no longer produces. NOTHING is
        ever dropped and nothing is backfilled: an added column is NULL for
        every chunk written before it existed, and the caller says so in the
        run log, because a column that is empty for the first half of a corpus
        is otherwise indistinguishable from one the LLM had nothing to say for.
        """
        if not wanted:
            return {"added": [], "kept": []}
        existing = self._existing_columns(collection)
        desired = dict(wanted)
        desired.setdefault(self._ENRICH_STAMP, "string")
        # EVERY name is checked before ANY column is created. Validating
        # inside the loop below meant an alphabetically earlier name had
        # already been added by the time a later one was refused, leaving the
        # table half-migrated - and DDL does not roll back on either engine.
        for column in desired:
            if not self._ATTRIBUTE_OK.match(str(column).lower()):
                raise ValueError(
                    f"{column!r} cannot be a column name - use letters, "
                    "digits and underscores, starting with a letter")
        added: list = []
        with self._cursor() as cur:            # type: ignore[attr-defined]
            for column, kind in sorted(desired.items()):
                name = str(column).lower()
                if name in existing:
                    continue
                sql_type = self._ATTRIBUTE_SQL.get(str(kind), self._ATTRIBUTE_SQL["string"])
                cur.execute(f"ALTER TABLE {self._table(collection)} "
                            f"ADD COLUMN {name} {sql_type}")
                added.append(name)
        if added:
            self._commit()
            getattr(self, "_attribute_cache", {}).pop(collection, None)
        kept = sorted((existing - self._canonical_columns())
                      - {str(c).lower() for c in desired})
        return {"added": added, "kept": kept}

    def _columns_for(self, collection: str, records: Optional[list] = None) -> list:
        """The canonical columns plus whatever enrichment added.

        Given `records`, an enrichment column is named only when the batch
        actually carries a key for it. That is what keeps the promise
        `sync_attributes` makes about its `kept` columns: they belong to a
        prompt this setup no longer runs, so no record holds a value for them,
        `_row` would supply None, and `SET col = EXCLUDED.col` would write that
        NULL over what the earlier prompt wrote. Since a chunk id is stable
        across a re-ingestion, editing the end of a document is enough to
        rewrite its earlier chunks and erase the column there - silently, and
        with nothing left in the live rows to recover it from.

        A read passes no records and gets every column, which is what a
        SELECT wants.
        """
        canonical = list(getattr(self, "_COLUMNS", []))
        attributes = self.attribute_columns(collection)
        if records is None:
            return canonical + attributes
        # `run_load` flattens the enrichment map onto the chunk, so a key is
        # present exactly when this run produced a value for it - including
        # None for a chunk whose LLM call failed, which SHOULD be written.
        carried = {key for record in records for key in record}
        return canonical + [column for column in attributes if column in carried]

    def _table(self, collection: str) -> str:
        """The quoted table name; adapters that quote differently override."""
        return collection

    def _commit(self) -> None:
        connection = getattr(self, "_conn", None)
        if connection is not None:
            connection.commit()

    @abstractmethod
    def connect(self, config: dict) -> None: ...

    @abstractmethod
    def ensure_collection(self, name: str, dims: int, metric: str = "cosine",
                          schema: Optional[dict] = None) -> None: ...

    @abstractmethod
    def ddl(self, name: str, dims: int, metric: str = "cosine",
            schema: Optional[dict] = None) -> str:
        """The DDL/collection-spec ensure_collection would execute (governance artifact)."""

    @abstractmethod
    def upsert(self, collection: str, records: list) -> int: ...

    @abstractmethod
    def search(self, collection: str, vector: list, k: int = 5,
               filter: Optional[dict] = None, mode: str = "vector",
               query_text: Optional[str] = None,
               raw_filter: Optional[str] = None) -> list: ...

    @abstractmethod
    def delete(self, collection: str, ids: Optional[list] = None,
               filter: Optional[dict] = None) -> int:
        """Physically remove rows, live and retired alike — the erasure path.

        Backends that keep lineage must NOT restrict this to live rows:
        erasing a document has to take its history with it, or the bytes are
        still there. The reversible everyday operation is retire().
        """

    def prune_history(self, collection: str, before: str,
                      dry_run: bool = False) -> int:
        """Drop retired generations tombstoned before `before` (retention).

        Live rows are never touched, so retrieval is unaffected — only how
        far back an as-of read can reach. Backends without lineage have no
        history to prune.
        """
        raise NotImplementedError(
            f"{self.name} does not keep chunk history, so there is none to prune")

    @abstractmethod
    def drop_collection(self, name: str) -> None: ...

    @abstractmethod
    def list_collections(self) -> list: ...

    @abstractmethod
    def count(self, collection: str, filter: Optional[dict] = None) -> int: ...

    @abstractmethod
    def cutover(self, alias: str, new_collection: str) -> None: ...

    def dimensions(self, collection: str) -> int:
        """The collection's vector width; 0 when the store cannot report it."""
        return 0

    def flush(self, collection: str) -> None:
        """Eventually-consistent stores refresh here; default no-op."""

    @property
    def needs_flush(self) -> bool:
        return False

    def capabilities(self) -> dict:
        return {"alias": False, "sparse": False, "hybrid": False,
                "namespace": False, "cutover": "none"}

    def close(self) -> None:
        pass
