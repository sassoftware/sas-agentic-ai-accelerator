# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import pytest

from rag_core.filters import compile_sql


def test_empty_filter_is_true():
    assert compile_sql({}) == ("TRUE", [])


def test_equality_known_column():
    sql, params = compile_sql({"doc_id": "d1"})
    assert sql == "doc_id = %s"
    assert params == ["d1"]


def test_unknown_field_goes_to_tags():
    sql, params = compile_sql({"department": "legal"})
    assert sql == "tags->>'department' = %s"
    assert params == ["legal"]


def test_in_and_comparison():
    sql, params = compile_sql({"extractor": {"$in": ["pdf-text", "markdown"]},
                               "chunk_index": {"$gte": 2}})
    assert "extractor IN (%s, %s)" in sql
    assert "chunk_index >= %s" in sql
    assert params == ["pdf-text", "markdown", 2]


def test_and_or_nesting():
    sql, params = compile_sql({"$or": [{"doc_id": "a"}, {"doc_id": "b"}]})
    assert sql == "(doc_id = %s OR doc_id = %s)"
    assert params == ["a", "b"]


def test_injection_shaped_field_rejected():
    with pytest.raises(ValueError):
        compile_sql({"doc_id; DROP TABLE x--": "v"})
    with pytest.raises(ValueError):
        compile_sql({"a'||'b": "v"})


def test_values_are_never_interpolated():
    sql, params = compile_sql({"doc_id": "'; DROP TABLE chunks; --"})
    assert "DROP TABLE" not in sql          # value stays a bound parameter
    assert params == ["'; DROP TABLE chunks; --"]


def test_unsupported_operator_rejected():
    with pytest.raises(ValueError):
        compile_sql({"doc_id": {"$regex": ".*"}})
