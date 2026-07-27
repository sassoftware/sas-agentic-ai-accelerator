# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Portable filter subset compiled per backend, parameterized everywhere.

This kills the portal builder's injectable f-string filters (design §0/§4).
Grammar:
    {"field": value}                      equality
    {"field": {"$in": [...]}}             membership
    {"field": {"$gt"|"$gte"|"$lt"|"$lte"|"$ne": value}}
    {"$and": [f1, f2, ...]}  /  {"$or": [f1, f2, ...]}

Known fields map to real columns; anything else targets the tags JSONB
(text comparison). Field names are validated against a strict identifier
pattern — never interpolated raw.
"""
from __future__ import annotations

import re

_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_OPS = {"$gt": ">", "$gte": ">=", "$lt": "<", "$lte": "<=", "$ne": "<>"}

KNOWN_COLUMNS = {
    "chunk_id", "doc_id", "source_uri", "chunk_index", "content_hash",
    "extractor", "pipeline_version", "ingested_at", "heading_path",
    "prev_id", "next_id",
}


def _column(field: str) -> str:
    if not _FIELD.match(field):
        raise ValueError(f"invalid filter field name: {field!r}")
    if field in KNOWN_COLUMNS:
        return field
    return f"tags->>'{field}'"     # field validated above; values always bind


def compile_sql(filter_spec: dict) -> tuple:
    """Compile the portable grammar to (sql_condition, params) for %s backends."""
    if not filter_spec:
        return "TRUE", []
    clauses: list = []
    params: list = []
    for key, value in filter_spec.items():
        if key in ("$and", "$or"):
            if not isinstance(value, list) or not value:
                raise ValueError(f"{key} expects a non-empty list")
            sub = [compile_sql(f) for f in value]
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
                    clauses.append(f"{_column(key)} IN ({marks})")
                    params.extend(operand)
                elif op in _OPS:
                    clauses.append(f"{_column(key)} {_OPS[op]} %s")
                    params.append(operand)
                else:
                    raise ValueError(f"unsupported filter operator: {op!r}")
        else:
            clauses.append(f"{_column(key)} = %s")
            params.append(value)
    return " AND ".join(clauses), params
