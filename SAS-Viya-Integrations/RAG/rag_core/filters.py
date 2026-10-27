# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Portable filter subset compiled per backend, parameterized everywhere.

This kills the portal builder's injectable f-string filters (design §0/§4).
Grammar:
    {"field": value}                      equality
    {"field": {"$in": [...]}}             membership
    {"field": {"$gt"|"$gte"|"$lt"|"$lte"|"$ne": value}}
    {"$and": [f1, f2, ...]}  /  {"$or": [f1, f2, ...]}

Known fields map to real columns; anything else targets the tags JSON column
(text comparison). Field names are validated against a strict identifier
pattern — never interpolated raw.

Only the JSON accessor differs between the SQL backends, so the grammar is
compiled once and the dialect decides how a tag is read: `tags->>'x'` on
Postgres, `JSON_EXTRACT_STRING(tags, 'x')` on the MySQL-protocol stores.

Weaviate has no SQL, so `compile_weaviate` turns the same grammar into the
operand tree of a Weaviate `where` filter - the JSON a batch delete carries
and the GraphQL input a query renders. Values are typed fields of that tree,
never text spliced into a statement, which keeps the property this module
exists for. A tag is read from its own `tag_<name>` property there, because
Weaviate cannot filter inside a JSON text.
"""
from __future__ import annotations

import datetime
import json
import re

_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_OPS = {"$gt": ">", "$gte": ">=", "$lt": "<", "$lte": "<=", "$ne": "<>"}

KNOWN_COLUMNS = {
    "chunk_id", "doc_id", "source_uri", "chunk_index", "content_hash",
    "extractor", "pipeline_version", "ingested_at", "heading_path",
    "prev_id", "next_id",
}

_TAG_ACCESSOR = {
    "postgres": "tags->>'{field}'",
    "mysql": "JSON_EXTRACT_STRING(tags, '{field}')",
}


def _column(field: str, dialect: str = "postgres") -> str:
    if not _FIELD.match(field):
        raise ValueError(f"invalid filter field name: {field!r}")
    if field in KNOWN_COLUMNS:
        return field
    try:
        accessor = _TAG_ACCESSOR[dialect]
    except KeyError:
        raise ValueError(f"unsupported filter dialect: {dialect!r}") from None
    return accessor.format(field=field)   # field validated above; values always bind


def compile_sql(filter_spec: dict, dialect: str = "postgres") -> tuple:
    """Compile the portable grammar to (sql_condition, params) for %s backends."""
    if not filter_spec:
        return "TRUE", []
    clauses: list = []
    params: list = []
    for key, value in filter_spec.items():
        if key in ("$and", "$or"):
            if not isinstance(value, list) or not value:
                raise ValueError(f"{key} expects a non-empty list")
            sub = [compile_sql(f, dialect) for f in value]
            joiner = " AND " if key == "$and" else " OR "
            clauses.append("(" + joiner.join(s for s, _ in sub) + ")")
            for _, p in sub:
                params.extend(p)
        elif isinstance(value, dict):
            for op, operand in value.items():
                if op == "$in":
                    if not isinstance(operand, list) or not operand:
                        raise ValueError("$in expects a non-empty list")
                    marks = ", ".join(["%s"] * len(operand))
                    clauses.append(f"{_column(key, dialect)} IN ({marks})")
                    params.extend(operand)
                elif op in _OPS:
                    clauses.append(f"{_column(key, dialect)} {_OPS[op]} %s")
                    params.append(operand)
                else:
                    raise ValueError(f"unsupported filter operator: {op!r}")
        else:
            clauses.append(f"{_column(key, dialect)} = %s")
            params.append(value)
    return " AND ".join(clauses), params


# ---------------------------------------------------------------------------
# Weaviate
# ---------------------------------------------------------------------------
#: the property a tag is flattened into on Weaviate (tag `department` ->
#: property `tag_department`); the adapter writes it, the filter reads it
TAG_PREFIX = "tag_"

#: Weaviate data type of a known column that is not text - it decides which
#: value field a comparison carries (valueInt, valueDate, else valueText)
_WEAVIATE_TYPES = {"chunk_index": "int", "ingested_at": "date"}
_WEAVIATE_OPS = {"$gt": "GreaterThan", "$gte": "GreaterThanEqual",
                 "$lt": "LessThan", "$lte": "LessThanEqual", "$ne": "NotEqual"}
_VALUE_FIELD = {"text": "valueText", "int": "valueInt", "date": "valueDate"}


def weaviate_date(value) -> str:
    """An RFC 3339 UTC timestamp, the only form Weaviate's date type takes.

    The pipeline stamps `2026-07-30T12:00:00Z`, the SQL backends hand back
    `2026-07-30 12:00:00`, and a caller's as-of date can be either - all of
    them are the same instant here. A value without an offset is UTC, as it
    is everywhere else in the pipeline.
    """
    if isinstance(value, datetime.datetime):
        moment = value
    elif isinstance(value, datetime.date):
        moment = datetime.datetime(value.year, value.month, value.day)
    else:
        text = str(value or "").strip().replace(" ", "T", 1)
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            moment = datetime.datetime.fromisoformat(text)
        except ValueError:
            raise ValueError(f"not a timestamp: {value!r}") from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    moment = moment.astimezone(datetime.timezone.utc)
    stamp = moment.strftime("%Y-%m-%dT%H:%M:%S")
    if moment.microsecond:
        stamp += f".{moment.microsecond:06d}"
    return stamp + "Z"


def _weaviate_property(field: str) -> tuple:
    """(property name, Weaviate type) a filter field addresses."""
    if not _FIELD.match(field):
        raise ValueError(f"invalid filter field name: {field!r}")
    if field in KNOWN_COLUMNS:
        return field, _WEAVIATE_TYPES.get(field, "text")
    return TAG_PREFIX + field, "text"


def _weaviate_value(kind: str, value):
    if kind == "int":
        return int(value)
    if kind == "date":
        return weaviate_date(value)
    # a tag is compared as text, as the SQL accessors read it: a number or a
    # boolean in the tags JSON is its JSON spelling there too
    return value if isinstance(value, str) else json.dumps(value)


def compile_weaviate(filter_spec: dict, known: set = None):
    """Compile the portable grammar to a Weaviate `where` operand tree.

    Returns None for an empty filter (no condition), the tree as a dict, or
    False when the filter cannot match anything. False is only possible when
    `known` - the collection's property names - is given: a comparison with a
    tag no chunk was ever written with is NULL in SQL, which no row matches,
    and Weaviate would refuse the query for an unknown property instead.
    """
    if not filter_spec:
        return None
    operands: list = []
    for key, value in filter_spec.items():
        if key in ("$and", "$or"):
            if not isinstance(value, list) or not value:
                raise ValueError(f"{key} expects a non-empty list")
            parts = [compile_weaviate(f, known) for f in value]
            if key == "$and":
                if any(part is False for part in parts):
                    return False
                parts = [part for part in parts if part]
                if parts:
                    operands.append(parts[0] if len(parts) == 1 else
                                    {"operator": "And", "operands": parts})
            else:
                if any(part is None for part in parts):
                    continue            # one side matches everything
                parts = [part for part in parts if part is not False]
                if not parts:
                    return False
                operands.append(parts[0] if len(parts) == 1 else
                                {"operator": "Or", "operands": parts})
            continue
        prop, kind = _weaviate_property(key)
        if known is not None and prop not in known:
            return False                # NULL compares false, every operator
        conditions = value.items() if isinstance(value, dict) else [(None, value)]
        for op, operand in conditions:
            if op is None:
                operands.append({"path": [prop], "operator": "Equal",
                                 _VALUE_FIELD[kind]: _weaviate_value(kind, operand)})
            elif op == "$in":
                if not isinstance(operand, list) or not operand:
                    raise ValueError("$in expects a non-empty list")
                operands.append({"path": [prop], "operator": "ContainsAny",
                                 _VALUE_FIELD[kind] + "Array":
                                     [_weaviate_value(kind, item) for item in operand]})
            elif op in _WEAVIATE_OPS:
                operands.append({"path": [prop], "operator": _WEAVIATE_OPS[op],
                                 _VALUE_FIELD[kind]: _weaviate_value(kind, operand)})
            else:
                raise ValueError(f"unsupported filter operator: {op!r}")
    if not operands:
        return None
    if len(operands) == 1:
        return operands[0]
    return {"operator": "And", "operands": operands}
