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


# ---------------------------------------------------------------------------
# one grammar, three backends
# ---------------------------------------------------------------------------
from rag_core.filters import compile_weaviate, weaviate_date  # noqa: E402

# (filter, Postgres, MySQL-protocol, Weaviate) - the SQL pair with its params
PARITY = [
    ({"doc_id": "d1"},
     ("doc_id = %s", ["d1"]), ("doc_id = %s", ["d1"]),
     {"path": ["doc_id"], "operator": "Equal", "valueText": "d1"}),
    ({"department": "legal"},
     ("tags->>'department' = %s", ["legal"]),
     ("JSON_EXTRACT_STRING(tags, 'department') = %s", ["legal"]),
     {"path": ["tag_department"], "operator": "Equal", "valueText": "legal"}),
    ({"extractor": {"$in": ["pdf-text", "markdown"]}},
     ("extractor IN (%s, %s)", ["pdf-text", "markdown"]),
     ("extractor IN (%s, %s)", ["pdf-text", "markdown"]),
     {"path": ["extractor"], "operator": "ContainsAny",
      "valueTextArray": ["pdf-text", "markdown"]}),
    ({"chunk_index": {"$gte": 2}},
     ("chunk_index >= %s", [2]), ("chunk_index >= %s", [2]),
     {"path": ["chunk_index"], "operator": "GreaterThanEqual", "valueInt": 2}),
    ({"doc_id": {"$ne": "d1"}},
     ("doc_id <> %s", ["d1"]), ("doc_id <> %s", ["d1"]),
     {"path": ["doc_id"], "operator": "NotEqual", "valueText": "d1"}),
    ({"ingested_at": {"$lt": "2026-07-30 12:00:00"}},
     ("ingested_at < %s", ["2026-07-30 12:00:00"]),
     ("ingested_at < %s", ["2026-07-30 12:00:00"]),
     {"path": ["ingested_at"], "operator": "LessThan",
      "valueDate": "2026-07-30T12:00:00Z"}),
    ({"$or": [{"doc_id": "a"}, {"doc_id": "b"}]},
     ("(doc_id = %s OR doc_id = %s)", ["a", "b"]),
     ("(doc_id = %s OR doc_id = %s)", ["a", "b"]),
     {"operator": "Or", "operands": [
         {"path": ["doc_id"], "operator": "Equal", "valueText": "a"},
         {"path": ["doc_id"], "operator": "Equal", "valueText": "b"}]}),
    ({"doc_id": "a", "department": "hr"},
     ("doc_id = %s AND tags->>'department' = %s", ["a", "hr"]),
     ("doc_id = %s AND JSON_EXTRACT_STRING(tags, 'department') = %s", ["a", "hr"]),
     {"operator": "And", "operands": [
         {"path": ["doc_id"], "operator": "Equal", "valueText": "a"},
         {"path": ["tag_department"], "operator": "Equal", "valueText": "hr"}]}),
]


@pytest.mark.parametrize("spec,postgres,mysql,weaviate", PARITY)
def test_one_grammar_three_backends(spec, postgres, mysql, weaviate):
    assert compile_sql(spec) == postgres
    assert compile_sql(spec, "mysql") == mysql
    assert compile_weaviate(spec) == weaviate


def test_weaviate_empty_filter_is_no_condition():
    assert compile_weaviate({}) is None
    assert compile_weaviate(None) is None


def test_weaviate_refuses_what_sql_refuses():
    with pytest.raises(ValueError):
        compile_weaviate({"doc_id; DROP TABLE x--": "v"})
    with pytest.raises(ValueError):
        compile_weaviate({"doc_id": {"$regex": ".*"}})
    with pytest.raises(ValueError):
        compile_weaviate({"$and": []})


def test_weaviate_values_stay_values():
    tree = compile_weaviate({"doc_id": '"} DROP {"'})
    assert tree["valueText"] == '"} DROP {"'


def test_a_tag_value_is_compared_as_text_like_the_sql_accessors():
    assert compile_weaviate({"priority": 2})["valueText"] == "2"
    assert compile_weaviate({"urgent": True})["valueText"] == "true"


def test_a_tag_the_collection_lacks_matches_nothing():
    known = {"doc_id", "tag_department"}
    assert compile_weaviate({"region": "emea"}, known) is False
    assert compile_weaviate({"doc_id": "a", "region": "emea"}, known) is False
    assert compile_weaviate({"$or": [{"region": "emea"}, {"doc_id": "a"}]}, known) \
        == {"path": ["doc_id"], "operator": "Equal", "valueText": "a"}
    assert compile_weaviate({"$or": [{"region": "x"}, {"region": "y"}]}, known) is False
    assert compile_weaviate({"department": "hr"}, known)["path"] == ["tag_department"]


def test_weaviate_dates_are_rfc3339_utc():
    assert weaviate_date("2026-07-30T12:00:00Z") == "2026-07-30T12:00:00Z"
    assert weaviate_date("2026-07-30 12:00:00") == "2026-07-30T12:00:00Z"
    assert weaviate_date("2026-07-30T14:00:00+02:00") == "2026-07-30T12:00:00Z"
    assert weaviate_date("2026-07-30 12:00:00.123456") == "2026-07-30T12:00:00.123456Z"
    assert weaviate_date("2026-07-30") == "2026-07-30T00:00:00Z"
    with pytest.raises(ValueError):
        weaviate_date("last tuesday")
