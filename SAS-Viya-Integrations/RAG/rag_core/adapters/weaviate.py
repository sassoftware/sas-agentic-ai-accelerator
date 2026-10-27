# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Weaviate adapter — the third backend, and the first that speaks no SQL.

Same contract as pgvector, including chunk lineage, as-of reads and rollback,
over Weaviate's REST and GraphQL API with `requests` only: the ingestion
steps need no extra driver, and the manifested retrieval model reads the same
collections the same way. Weaviate 1.33 or newer (collection aliases are GA
from there; `connect()` refuses an older server).

What Weaviate does differently, and how the adapter answers it:

* **Collection = one Weaviate collection.** Weaviate capitalises the first
  letter of a collection name, so `support_docs` is stored as `Support_docs`.
  The identifier rule is the pgvector one (lower case first), which keeps the
  mapping one-to-one in both directions.

* **The object id IS the key.** Ids are UUIDs, so a live chunk lives at
  uuid5(chunk_id). Retiring it writes a COPY under an id derived from the
  chunk, its generation and the retiring run, with `valid_to` set, and then
  deletes the live object - which frees the live id for the next generation.
  Weaviate has no multi-object transactions, so the order is copy first,
  delete second: a failure in between leaves a duplicate (a retired copy next
  to the still-live object), never a lost chunk, and retrieval never sees the
  copy because it reads `valid_to IsNull` only. Repeating the call finishes
  the job; the copy id is deterministic.

* **A write replaces the whole object.** pgvector's `ON CONFLICT DO UPDATE`
  touches only the columns a batch carries. A Weaviate batch replaces, so an
  enrichment column this batch does not carry - kept from a prompt the setup
  no longer runs - and the generation's `valid_from` are read from the live
  object first and written back with it.

* **Tags.** Weaviate cannot filter inside a JSON text, so `tags` is stored as
  JSON (the round trip) AND each tag as its own `tag_<name>` text property
  (the filter). Enrichment outputs may therefore not start with `tag_` here.

* **No partial index.** Retired generations stay in the vector index and are
  filtered out at query time, like SingleStore (`live_only_index: False`);
  `prune_history` keeps that bounded.

* **No run history.** The run-history tables (`rag_runs`, `rag_doc_events`,
  `rag_configs`) are SQL written over the store's connection, which Weaviate
  does not have: `capabilities()["run_history"]` is False and the Load step
  and the ingestion job skip them. Chunk lineage is unaffected.

* **`ddl()` is JSON**, the create-collection request body - the governance
  artifact Register Setup saves as vector-store-ddl.json.

Auto-schema is a server setting (AUTOSCHEMA_ENABLED), not a collection one:
the adapter declares every property it writes before writing it and sends no
other, so it behaves the same whichever way the server is configured.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import uuid
from typing import Optional

from ..filters import TAG_PREFIX, compile_weaviate, weaviate_date
from .base import SearchHit, VectorStoreAdapter

_IDENT = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
# a tag that can become a property name; any other tag stays in the JSON only
_TAG_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,59}$")
_DIMS = re.compile(r"\bdims=(\d+)\b")

#: the oldest server the adapter accepts: collection aliases are GA in 1.33
MIN_VERSION = (1, 33)

#: fixed namespace for the object ids, so every process derives the same id
#: for the same chunk
NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL,
                       "https://github.com/sassoftware/sas-agentic-ai-accelerator"
                       "/rag/weaviate")

_SSL_DISABLED = {"disable", "false", "off", "no", "0"}

# adapter metric -> (Weaviate distance, distance -> higher-is-better score)
_METRICS = {
    "cosine": ("cosine", lambda distance: 1.0 - distance),
    "l2": ("l2-squared", lambda distance: -distance),
    "ip": ("dot", lambda distance: -distance),
}
_SCORE = {weaviate: score for weaviate, score in _METRICS.values()}

# Weaviate caps what one query or one batch delete reaches (QUERY_MAXIMUM_RESULTS,
# default 10000). Reads page below it; deletes repeat until nothing matches.
_PAGE = 500
_MAX_RESULTS = 10000
_BATCH = 200

_GQL_ENUM_KEYS = {"operator"}
_GQL_OPERATORS = {"And", "Or", "Equal", "NotEqual", "GreaterThan",
                  "GreaterThanEqual", "LessThan", "LessThanEqual",
                  "ContainsAny", "ContainsAll", "IsNull", "Like"}


def _ident(name: str) -> str:
    """The Weaviate collection name of an accelerator collection."""
    if not _IDENT.match(str(name)):
        raise ValueError(
            f"invalid collection name {name!r} (expected ^[a-z][a-z0-9_]{{0,62}}$)")
    return name[0].upper() + name[1:]


def _metric(metric: str) -> tuple:
    try:
        return _METRICS[metric]
    except KeyError:
        raise ValueError(f"unsupported metric {metric!r} for Weaviate "
                         f"(supported: {', '.join(sorted(_METRICS))})") from None


def live_id(chunk_id: str) -> str:
    """Where the live generation of a chunk is stored."""
    return str(uuid.uuid5(NAMESPACE, str(chunk_id)))


def retired_id(chunk_id: str, valid_from, retired_in_run) -> str:
    """Where a retired generation is stored: one id per chunk, generation and
    retiring run, so retiring twice is idempotent and two generations of one
    chunk never overwrite each other."""
    key = "\x1f".join((str(chunk_id), str(valid_from or ""),
                       str(retired_in_run or "")))
    return str(uuid.uuid5(NAMESPACE, key))


def _now() -> str:
    return weaviate_date(datetime.datetime.now(datetime.timezone.utc))


def _version(text: str) -> tuple:
    numbers = re.findall(r"\d+", str(text or ""))[:3]
    return tuple(int(n) for n in numbers) or (0,)


def gql(value, key: str = "") -> str:
    """A value as GraphQL input. Strings are JSON-escaped (GraphQL string
    escapes are JSON's), operators are enums and checked against a list -
    nothing a caller passes is spliced in unescaped."""
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {gql(v, k)}" for k, v in value.items()) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(gql(v, key) for v in value) + "]"
    if key in _GQL_ENUM_KEYS:
        if value not in _GQL_OPERATORS:
            raise ValueError(f"unsupported Weaviate operator {value!r}")
        return str(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return repr(value)
    return json.dumps(str(value))


def _and(*operands):
    """AND of where trees; None operands are 'no condition'."""
    present = [operand for operand in operands if operand]
    if not present:
        return None
    if len(present) == 1:
        return present[0]
    return {"operator": "And", "operands": present}


_LIVE = {"path": ["valid_to"], "operator": "IsNull", "valueBoolean": True}


def _as_of(stamp: str) -> dict:
    moment = weaviate_date(stamp)
    return _and({"path": ["valid_from"], "operator": "LessThanEqual",
                 "valueDate": moment},
                {"operator": "Or", "operands": [
                    _LIVE,
                    {"path": ["valid_to"], "operator": "GreaterThan",
                     "valueDate": moment}]})


def _ids_where(path: str, values: list) -> dict:
    return {"path": [path], "operator": "ContainsAny",
            "valueTextArray": [str(v) for v in values]}


class WeaviateAdapter(VectorStoreAdapter):
    name = "weaviate"

    #: (property, Weaviate type, index) - "field": exact-match filterable,
    #: "word": BM25-searchable text, "range": filterable with range filters,
    #: "": stored only. `field` tokenization is what makes `Equal` exact: the
    #: default `word` tokenization would let doc_id "a-b" match "a".
    _PROPERTIES = (
        ("chunk_id", "text", "field"), ("doc_id", "text", "field"),
        ("source_uri", "text", "field"), ("chunk_index", "int", "range"),
        ("content", "text", "word"), ("content_hash", "text", "field"),
        ("extractor", "text", "field"), ("pipeline_version", "text", "field"),
        ("ingested_at", "date", "range"), ("span", "text", ""),
        ("heading_path", "text", "field"), ("tags", "text", ""),
        ("prev_id", "text", "field"), ("next_id", "text", "field"),
        ("context_header", "text", "word"), ("entities", "text", ""),
        ("relations", "text", ""), ("run_id", "text", "field"),
        ("config_id", "text", "field"), ("embed_model", "text", "field"),
        ("embed_dims", "int", "range"), ("valid_from", "date", "range"),
        ("valid_to", "date", "range"), ("retired_in_run", "text", "field"),
    )
    _COLUMNS = ["chunk_id", "doc_id", "source_uri", "chunk_index", "content",
                "content_hash", "extractor", "pipeline_version", "ingested_at",
                "span", "heading_path", "tags", "prev_id", "next_id",
                "context_header", "entities", "relations",
                "run_id", "config_id", "embed_model", "embed_dims", "embedding"]
    _JSON_COLUMNS = {"span", "tags", "entities", "relations"}
    _DATE_COLUMNS = {"ingested_at", "valid_from", "valid_to"}
    _INT_COLUMNS = {"chunk_index", "embed_dims"}
    _LINEAGE = ("valid_from", "valid_to", "retired_in_run")
    #: Prompt Builder output type -> Weaviate data type
    _ATTRIBUTE_TYPES = {"string": "text", "decimal": "number"}

    def __init__(self, session=None):
        self._session = session
        self._base = ""
        self._headers: dict = {}
        self._verify = True
        self._schemas: dict = {}
        self.server_version = ""

    # -- connection ---------------------------------------------------------
    def connect(self, config: dict) -> None:
        """Open the HTTP session and check the server.

        `dbname` is not used: Weaviate has no databases. `password` is the
        Weaviate API key, sent as a bearer token; `user` names the user the
        key belongs to and is not sent. TLS follows `sslmode` as on
        SingleStore - only an explicit disable means plain HTTP - and
        certificates are verified against RAGSTORE_CABUNDLE (default: the
        bundle SAS Viya mounts into its pods) when that file exists.
        """
        if self._session is None:
            import requests  # lazy, like the other drivers

            self._session = requests.Session()
        sslmode = str(config.get("sslmode") or "").strip().lower()
        scheme = "http" if sslmode in _SSL_DISABLED else "https"
        port = int(config.get("port") or 8080)
        self._base = f"{scheme}://{config['host']}:{port}"
        self._headers = {"Content-Type": "application/json",
                         "Accept": "application/json"}
        if config.get("password"):
            self._headers["Authorization"] = "Bearer " + str(config["password"])
        self._timeout = float(config.get("connect_timeout", 15)) * 4
        self._verify = self._tls_verify()
        meta = self._call("GET", "/v1/meta")
        self.server_version = str((meta or {}).get("version") or "")
        if _version(self.server_version) < MIN_VERSION:
            raise RuntimeError(
                f"Weaviate {self.server_version or '(unknown version)'} is older "
                f"than {'.'.join(map(str, MIN_VERSION))}, the oldest version "
                "this adapter supports (collection aliases are generally "
                "available from 1.33) - upgrade the server")

    @staticmethod
    def _tls_verify():
        word = os.getenv("RAGSTORE_SSLVERIFY", "").strip().lower()
        if word in ("false", "no", "0"):
            return False
        bundle = os.getenv("RAGSTORE_CABUNDLE", "/security/trustedcerts.pem")
        return bundle if os.path.isfile(bundle) else True

    def close(self) -> None:
        if self._session is not None and hasattr(self._session, "close"):
            self._session.close()
        self._session = None

    def _call(self, method: str, path: str, body=None, ok=(200,),
              missing_ok: bool = False):
        """One REST request; the JSON answer, None for an allowed 404."""
        if self._session is None or not self._base:
            raise RuntimeError("adapter is not connected — call connect() first")
        response = self._session.request(
            method, self._base + path, json=body, headers=self._headers,
            verify=self._verify, timeout=getattr(self, "_timeout", 60))
        if missing_ok and response.status_code == 404:
            return None
        if response.status_code not in ok:
            raise RuntimeError(f"Weaviate {method} {path} answered "
                               f"{response.status_code}: {_error_text(response)}")
        if not response.content:
            return {}
        return response.json()

    def _graphql(self, query: str) -> dict:
        answer = self._call("POST", "/v1/graphql", {"query": query}) or {}
        if answer.get("errors"):
            messages = "; ".join(str(e.get("message", e))
                                 for e in answer["errors"])[:400]
            raise RuntimeError("Weaviate refused the query: " + messages)
        return answer.get("data") or {}

    # -- schema -------------------------------------------------------------
    def _property_spec(self, name: str, kind: str, index: str) -> dict:
        spec = {"name": name, "dataType": [kind]}
        if kind == "text":
            spec["tokenization"] = "word" if index == "word" else "field"
            spec["indexSearchable"] = index == "word"
            spec["indexFilterable"] = index in ("field", "word")
        else:
            spec["indexFilterable"] = bool(index)
            if index == "range":
                spec["indexRangeFilters"] = True
        return spec

    def _collection_spec(self, name: str, dims: int, metric: str) -> dict:
        distance, _ = _metric(metric)
        return {
            "class": _ident(name),
            # dimensions are not part of a Weaviate schema (the first vector
            # sets them), so the description carries them for dimensions()
            "description": (f"SAS Agentic AI Accelerator RAG collection {name}; "
                            f"dims={int(dims)}; metric={metric}"),
            "vectorizer": "none",
            "vectorIndexType": "hnsw",
            "vectorIndexConfig": {"distance": distance},
            # IsNull on valid_to is the live-row filter
            "invertedIndexConfig": {"indexNullState": True},
            "properties": [self._property_spec(*p) for p in self._PROPERTIES],
        }

    def ddl(self, name: str, dims: int, metric: str = "cosine",
            schema: Optional[dict] = None) -> str:
        """The create-collection request body (the governance artifact).

        JSON rather than SQL - Register Setup saves it as vector-store-ddl.json.
        A DBA can pre-create the collection by POSTing it to /v1/schema.
        """
        return json.dumps(self._collection_spec(name, dims, metric), indent=2) + "\n"

    def _schema(self, collection: str, refresh: bool = False) -> Optional[dict]:
        """The collection's schema, cached per collection; None if absent."""
        if refresh or collection not in self._schemas:
            self._schemas[collection] = self._call(
                "GET", f"/v1/schema/{_ident(collection)}", missing_ok=True)
        return self._schemas[collection]

    def _property_names(self, collection: str) -> set:
        found = self._schema(collection) or {}
        return {str(p.get("name")) for p in found.get("properties") or []}

    def _forget_attributes(self, collection: str = "") -> None:
        super()._forget_attributes(collection)
        if collection:
            self._schemas.pop(collection, None)
        else:
            self._schemas.clear()

    def _add_property(self, collection: str, spec: dict) -> None:
        self._call("POST", f"/v1/schema/{_ident(collection)}/properties", spec)

    def ensure_collection(self, name: str, dims: int, metric: str = "cosine",
                          schema: Optional[dict] = None) -> None:
        """Create the collection, or add what an existing one lacks.

        The sparse opt-in needs nothing here: `content` and `context_header`
        are BM25-indexed from the start, so hybrid retrieval can arrive later
        without re-creating or re-ingesting a collection.
        """
        self._forget_attributes(name)   # this call may CREATE the collection
        if self._schema(name) is None:
            self._call("POST", "/v1/schema", self._collection_spec(name, dims, metric))
        else:
            present = self._property_names(name)
            for prop in self._PROPERTIES:
                if prop[0] not in present:
                    self._add_property(name, self._property_spec(*prop))
        self._forget_attributes(name)

    # -- enrichment attribute columns ---------------------------------------
    def _existing_columns(self, collection: str) -> set:
        return {n.lower() for n in self._property_names(collection)}

    def attribute_columns(self, collection: str) -> list:
        # the flattened tags are the adapter's own, not enrichment output
        return [c for c in super().attribute_columns(collection)
                if not c.startswith(TAG_PREFIX)]

    def sync_attributes(self, collection: str, wanted: dict) -> dict:
        """Add a property for every stored output that does not have one.

        The contract of the base class - every name checked before any is
        created, the stamp column always, nothing dropped, nothing backfilled
        - with Weaviate's property API in place of ALTER TABLE.
        """
        existing = self._existing_columns(collection)
        desired = dict(wanted)
        desired.setdefault(self._ENRICH_STAMP, "string")
        for column in desired:
            name = str(column).lower()
            if not self._ATTRIBUTE_OK.match(name):
                raise ValueError(
                    f"{column!r} cannot be a column name - use letters, "
                    "digits and underscores, starting with a letter")
            if name.startswith(TAG_PREFIX):
                raise ValueError(
                    f"{column!r} cannot be a column name on Weaviate - names "
                    f"starting with {TAG_PREFIX} hold the chunk tags there")
        added: list = []
        for column, kind in sorted(desired.items()):
            name = str(column).lower()
            if name in existing:
                continue
            data_type = self._ATTRIBUTE_TYPES.get(str(kind), "text")
            self._add_property(collection, self._property_spec(
                name, data_type, "field" if data_type == "text" else "range"))
            added.append(name)
        if added:
            self._forget_attributes(collection)
        kept = sorted((existing - self._canonical_columns())
                      - {str(c).lower() for c in desired}
                      - {c for c in existing if c.startswith(TAG_PREFIX)})
        return {"added": added, "kept": kept}

    def _ensure_tags(self, collection: str, records: list) -> None:
        """Declare a `tag_<name>` property for every tag these records carry."""
        present = self._property_names(collection)
        wanted = sorted({TAG_PREFIX + str(key)
                         for record in records
                         for key in (record.get("tags") or {})
                         if _TAG_KEY.match(str(key))} - present)
        for name in wanted:
            self._add_property(collection, self._property_spec(name, "text", "field"))
        if wanted:
            self._forget_attributes(collection)

    # -- reading ------------------------------------------------------------
    def _where(self, collection: str, filter: Optional[dict]):
        return compile_weaviate(filter or {}, self._property_names(collection))

    def _get(self, collection: str, where, fields: list,
             vector: bool = False) -> list:
        """Every object matching `where`, paged below Weaviate's result cap.

        Raises rather than returning part of the set: a retire or restore
        that silently handled the first 10000 objects would leave the rest
        live.
        """
        extra = "id vector" if vector else "id"
        objects: list = []
        offset = 0
        while True:
            arguments = f"limit: {_PAGE}, offset: {offset}"
            if where:
                arguments = f"where: {gql(where)}, " + arguments
            data = self._graphql(
                "{ Get { " + _ident(collection) + "(" + arguments + ") { "
                + " ".join(fields) + " _additional { " + extra + " } } } }")
            page = (data.get("Get") or {}).get(_ident(collection)) or []
            objects.extend(page)
            if len(page) < _PAGE:
                return objects
            offset += _PAGE
            if offset + _PAGE > _MAX_RESULTS:
                raise RuntimeError(
                    f"more than {_MAX_RESULTS - _PAGE} objects in "
                    f"{collection} match - narrow the filter")

    def _record(self, obj: dict) -> dict:
        record = {key: value for key, value in obj.items() if key != "_additional"}
        for column in self._JSON_COLUMNS:
            if isinstance(record.get(column), str):
                try:
                    record[column] = json.loads(record[column])
                except (ValueError, TypeError):
                    pass
        if record.get("tags") is None and "tags" in record:
            record["tags"] = {}
        return record

    # -- writing ------------------------------------------------------------
    def _value(self, column: str, value):
        if value is None:
            return None
        if column in self._JSON_COLUMNS:
            return json.dumps(value)
        if column in self._DATE_COLUMNS:
            return weaviate_date(value)
        if column in self._INT_COLUMNS:
            return int(value)
        return value

    def _batch(self, collection: str, objects: list) -> None:
        for start in range(0, len(objects), _BATCH):
            answer = self._call("POST", "/v1/batch/objects",
                                {"objects": objects[start:start + _BATCH]})
            for result in answer or []:
                errors = ((result.get("result") or {}).get("errors") or {}).get("error")
                if errors:
                    raise RuntimeError(
                        "Weaviate refused an object: "
                        + str(errors[0].get("message", errors[0]))[:300])

    def _delete_where(self, collection: str, where) -> int:
        """Batch delete until nothing matches; returns how many went."""
        removed = 0
        while True:
            answer = self._call("DELETE", "/v1/batch/objects", {
                "match": {"class": _ident(collection), "where": where},
                "output": "minimal", "dryRun": False}) or {}
            results = answer.get("results") or {}
            if int(results.get("failed") or 0):
                raise RuntimeError(f"Weaviate failed to delete "
                                   f"{results.get('failed')} object(s)")
            done = int(results.get("successful") or 0)
            removed += done
            limit = int(results.get("limit") or _MAX_RESULTS)
            if done == 0 or int(results.get("matches") or 0) < limit:
                return removed

    def _delete_ids(self, collection: str, ids: list) -> int:
        removed = 0
        for start in range(0, len(ids), _PAGE):
            removed += self._delete_where(
                collection, _ids_where("id", ids[start:start + _PAGE]))
        return removed

    def upsert(self, collection: str, records: list) -> int:
        if not records:
            return 0
        self._ensure_tags(collection, records)
        columns = [c for c in self._columns_for(collection, records)
                   if c != "embedding"]
        # what a replace would otherwise lose: the generation's start, and
        # the enrichment columns of an earlier prompt this batch does not carry
        kept = [c for c in self.attribute_columns(collection) if c not in columns]
        known = self._property_names(collection)
        previous: dict = {}
        ids = [live_id(r["chunk_id"]) for r in records]
        for start in range(0, len(ids), _PAGE):
            for obj in self._get(collection, _ids_where("id", ids[start:start + _PAGE]),
                                 ["valid_from"] + kept):
                previous[obj["_additional"]["id"]] = obj
        objects = []
        for record, object_id in zip(records, ids):
            earlier = previous.get(object_id) or {}
            properties = {}
            for column in columns:
                value = self._value(column, record.get(column))
                if column == "tags" and value is None:
                    value = "{}"
                if value is not None:
                    properties[column] = value
            for column in kept:
                if earlier.get(column) is not None:
                    properties[column] = earlier[column]
            for key, value in (record.get("tags") or {}).items():
                name = TAG_PREFIX + str(key)
                if value is not None and name in known:
                    properties[name] = value if isinstance(value, str) else json.dumps(value)
            properties["valid_from"] = earlier.get("valid_from") or _now()
            objects.append({"class": _ident(collection), "id": object_id,
                            "properties": properties,
                            "vector": [float(v) for v in record["embedding"]]})
        self._batch(collection, objects)
        return len(records)

    def _retire_matching(self, collection: str, where, run_id,
                         keep_ids=None) -> int:
        """Copy every live object matching `where` to its retired id, then
        delete the live one (see the module docstring for the order)."""
        if where is False:
            return 0
        names = sorted(self._property_names(collection))
        live = self._get(collection, _and(_LIVE, where), names, vector=True)
        if keep_ids is not None:
            keep = {str(k) for k in keep_ids}
            live = [obj for obj in live if str(obj.get("chunk_id")) not in keep]
        if not live:
            return 0
        stamp = _now()
        copies = []
        for obj in live:
            properties = {k: v for k, v in obj.items()
                          if k != "_additional" and v is not None}
            properties["valid_to"] = stamp
            if run_id is not None:
                properties["retired_in_run"] = run_id
            copies.append({
                "class": _ident(collection),
                "id": retired_id(obj.get("chunk_id"), obj.get("valid_from"), run_id),
                "properties": properties,
                "vector": obj["_additional"].get("vector") or []})
        self._batch(collection, copies)
        self._delete_ids(collection, [obj["_additional"]["id"] for obj in live])
        return len(live)

    def retire(self, collection: str, ids: Optional[list] = None,
               filter: Optional[dict] = None, run_id: Optional[str] = None,
               keep_ids: Optional[list] = None) -> int:
        """Tombstone live chunks instead of deleting them (see PgVectorAdapter).

        `keep_ids` is applied to the fetched objects rather than in the
        filter: it can hold every chunk of a large document, and Weaviate has
        no NOT over a list.
        """
        if not ids and keep_ids is None and not filter:
            raise ValueError("retire() needs ids, keep_ids or a filter - refusing "
                             "to tombstone a whole collection implicitly")
        where = self._where(collection, filter)
        if ids:
            where = False if where is False else _and(_ids_where("chunk_id", ids), where)
        return self._retire_matching(collection, where, run_id, keep_ids)

    def search(self, collection: str, vector: list, k: int = 5,
               filter: Optional[dict] = None, mode: str = "vector",
               query_text: Optional[str] = None,
               raw_filter: Optional[str] = None,
               as_of: Optional[str] = None) -> list:
        """Search the LIVE slice, or the slice as it stood at `as_of`.

        Hybrid waits for the cross-backend contract (OQ15), as on the other
        backends; the collection is BM25-ready for it already.
        """
        if mode != "vector":
            raise NotImplementedError("hybrid search is opt-in and lands in P2 (OQ15)")
        if raw_filter:
            raise ValueError("raw_filter is SQL and Weaviate has none - use the "
                             "filter grammar instead")
        found = self._schema(collection)
        if found is None:
            raise RuntimeError(f"collection {collection!r} does not exist")
        where = self._where(collection, filter)
        if where is False:
            return []
        where = _and(where, _as_of(as_of) if as_of else _LIVE)
        fields = [c for c in self._columns_for(collection) if c != "embedding"]
        arguments = ("nearVector: " + gql({"vector": [float(v) for v in vector]})
                     + f", limit: {int(k)}")
        if where:
            arguments += ", where: " + gql(where)
        data = self._graphql(
            "{ Get { " + _ident(collection) + "(" + arguments + ") { "
            + " ".join(fields) + " _additional { id distance } } } }")
        to_score = _SCORE.get(
            (found.get("vectorIndexConfig") or {}).get("distance") or "cosine",
            _SCORE["cosine"])
        hits = []
        for obj in (data.get("Get") or {}).get(_ident(collection)) or []:
            distance = float(obj["_additional"].get("distance") or 0.0)
            record = self._record(obj)
            # the same number pgvector's `<=>` returns, for a cosine collection
            record["distance"] = distance
            hits.append(SearchHit(record.get("chunk_id"), to_score(distance), record))
        return hits

    def delete(self, collection: str, ids: Optional[list] = None,
               filter: Optional[dict] = None) -> int:
        """Physically remove objects — the erasure path (see PgVectorAdapter).

        Every generation goes: the condition does not mention valid_to, so a
        filter on doc_id takes the live object and every retired copy.
        """
        if ids:
            removed = 0
            for start in range(0, len(ids), _PAGE):
                removed += self._delete_where(
                    collection, _ids_where("chunk_id", ids[start:start + _PAGE]))
            return removed
        if filter:
            where = self._where(collection, filter)
            if where is False:
                return 0
            if where is None:
                raise ValueError("delete() needs ids or a filter — refusing to empty "
                                 "a collection implicitly (use drop_collection)")
            return self._delete_where(collection, where)
        raise ValueError("delete() needs ids or a filter — refusing to empty "
                         "a collection implicitly (use drop_collection)")

    def prune_history(self, collection: str, before: str,
                      dry_run: bool = False) -> int:
        """Drop retired generations tombstoned before `before` (retention)."""
        if not str(before or "").strip():
            raise ValueError("prune_history() needs a cutoff timestamp — "
                             "refusing to drop all history implicitly")
        where = _and({"path": ["valid_to"], "operator": "IsNull",
                      "valueBoolean": False},
                     {"path": ["valid_to"], "operator": "LessThan",
                      "valueDate": weaviate_date(before)})
        if dry_run:
            return self._count_where(collection, where)
        return self._delete_where(collection, where)

    def restore(self, collection: str, run_id: str) -> int:
        """Undo one run's retirements (see PgVectorAdapter.restore).

        The opposite order to pgvector's two statements: what the run
        inserted is tombstoned FIRST, because a live id has to be free before
        a retired generation can return to it. Not atomic; repeating it
        finishes what a failure interrupted.
        """
        self._retire_matching(
            collection,
            {"path": ["run_id"], "operator": "Equal", "valueText": str(run_id)},
            f"rollback-of-{run_id}")
        names = sorted(self._property_names(collection))
        retired = self._get(
            collection,
            {"path": ["retired_in_run"], "operator": "Equal",
             "valueText": str(run_id)},
            names, vector=True)
        if not retired:
            return 0
        revived = []
        for obj in retired:
            properties = {k: v for k, v in obj.items()
                          if k != "_additional" and v is not None
                          and k not in ("valid_to", "retired_in_run")}
            revived.append({"class": _ident(collection),
                            "id": live_id(obj.get("chunk_id")),
                            "properties": properties,
                            "vector": obj["_additional"].get("vector") or []})
        self._batch(collection, revived)
        self._delete_ids(collection, [obj["_additional"]["id"] for obj in retired])
        return len(retired)

    def drop_collection(self, name: str) -> None:
        self._forget_attributes(name)
        self._call("DELETE", f"/v1/schema/{_ident(name)}", ok=(200, 204),
                   missing_ok=True)
        self._forget_attributes(name)

    def list_collections(self) -> list:
        answer = self._call("GET", "/v1/schema") or {}
        names = []
        for found in answer.get("classes") or []:
            stored = str(found.get("class") or "")
            name = stored[:1].lower() + stored[1:]
            if _IDENT.match(name):
                names.append(name)
        return sorted(names)

    def _count_where(self, collection: str, where) -> int:
        if where is False:
            return 0
        arguments = f"(where: {gql(where)})" if where else ""
        data = self._graphql("{ Aggregate { " + _ident(collection) + arguments
                             + " { meta { count } } } }")
        rows = (data.get("Aggregate") or {}).get(_ident(collection)) or []
        return int(((rows[0] if rows else {}).get("meta") or {}).get("count") or 0)

    def count(self, collection: str, filter: Optional[dict] = None,
              include_retired: bool = False, as_of: Optional[str] = None) -> int:
        where = self._where(collection, filter)
        if where is False:
            return 0
        if as_of:
            where = _and(where, _as_of(as_of))
        elif not include_retired:
            where = _and(where, _LIVE)
        return self._count_where(collection, where)

    def dimensions(self, collection: str) -> int:
        """The collection's vector width.

        A Weaviate schema has no vector width - the first vector sets it - so
        ensure_collection writes it into the description; a collection made
        elsewhere is measured from one stored vector instead.
        """
        found = self._schema(collection)
        if not found:
            return 0
        written = _DIMS.search(str(found.get("description") or ""))
        if written:
            return int(written.group(1))
        data = self._graphql("{ Get { " + _ident(collection)
                             + "(limit: 1) { _additional { vector } } } }")
        rows = (data.get("Get") or {}).get(_ident(collection)) or []
        return len(((rows[0] if rows else {}).get("_additional") or {})
                   .get("vector") or [])

    def cutover(self, alias: str, new_collection: str) -> None:
        """Point the alias at the new collection — atomic on the server.

        Readers address the alias; the previous target stays a collection of
        its own (pgvector renames it to <alias>_retired), to be dropped once
        it is no longer needed. A COLLECTION of the alias's name cannot become
        an alias, because Weaviate cannot rename it: that is refused.
        """
        target, name = _ident(new_collection), _ident(alias)
        if self._schema(new_collection, refresh=True) is None:
            raise ValueError(f"cutover target {new_collection!r} does not exist")
        current = self._call("GET", f"/v1/aliases/{name}", missing_ok=True)
        if current:
            self._call("PUT", f"/v1/aliases/{name}", {"class": target})
            return
        if self._schema(alias, refresh=True) is not None:
            raise ValueError(
                f"{alias!r} is a collection, and Weaviate cannot rename it into "
                "an alias - drop it or choose another alias name")
        self._call("POST", "/v1/aliases", {"alias": name, "class": target})

    def capabilities(self) -> dict:
        return {"alias": True, "sparse": False, "hybrid": False,
                "namespace": False, "cutover": "alias",
                "history": True, "as_of": True, "rollback": True,
                "live_only_index": False, "transactional_ddl": False,
                "run_history": False}


def _error_text(response) -> str:
    try:
        body = response.json()
    except Exception:
        return str(getattr(response, "text", ""))[:300]
    errors = body.get("error") if isinstance(body, dict) else None
    if isinstance(errors, list) and errors:
        return str(errors[0].get("message", errors[0]))[:300]
    return json.dumps(body)[:300]
