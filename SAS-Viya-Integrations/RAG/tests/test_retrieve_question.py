# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The manifested retrieval model and a question padded with blanks.

A decision hands a character input to a model padded with blanks to the
length the variable is declared with. Found on a live system, in a test of
SAS Intelligent Decisioning: a 1000-character input arrived with about 900
trailing blanks, and the model returned them in the envelope - `"query"` was
1134 characters for a short question - and sent them to the embedding model
as part of the question. In SAS Micro Analytic Service one trailing blank
arrived.

The padding is not part of the question. The model works on the question
without it: that is what is embedded and what `query` returns. A question of
blanks only is still no question.

The same padding on the other inputs: a `filter_json` of blanks only is no
filter, a `retrieval_mode` of blanks only is the default mode (without the
note that a mode is not available), and a `k` of blanks only is the default
number of chunks. A `k` that is no number falls back to the default as well,
with a note, instead of failing the call. Whether a decision hands these
inputs over padded was not measured for them; `k` is a decimal input there.

`_store_config`, `_embed_query` and `_search` are replaced where a test is
about what `execute` passes on; one test goes through the real `_embed_query`
down to the request it sends.
"""
import importlib.util
import json
import pathlib
import sys
import types

import pytest

from rag_core.manifest import render_retrieval_model

TEMPLATE = pathlib.Path(__file__).resolve().parents[1] / "retrieve_context.py"
QUESTION = "How many days of leave does a new employee get?"
SETTINGS = {
    "BACKEND": "pgvector", "COLLECTION": "rag_hr_policies_v1",
    "EMBED_MODEL": "all_minilm_l6_v2", "EMBED_ENDPOINT": "https://viya.example/llm",
    "STORE_HOST": "db.example", "STORE_PORT": "5432", "STORE_DB": "postgres",
    "STORE_SSLMODE": "require", "CREDENTIAL_DOMAIN": "agentic-ai-keys",
    "DEFAULT_K": 4, "INGESTION_RUN_ID": "run-1",
}
HIT = {"chunk_id": "c1", "doc_id": "d1", "source_uri": "/docs/leave.pdf",
       "filename": "leave.pdf", "ingestion_timestamp": "2026-10-11T08:00:00Z",
       "distance": 0.25, "score": 0.75, "content": "Thirty days.",
       "heading_path": "Leave", "page": 2, "span_start": 10, "span_end": 22,
       "corpus_run_id": "run-1"}


def _load(manifested=False):
    """A fresh copy of the model: the template, or the copy rag_core writes
    for a setup - the file a decision actually runs."""
    if not manifested:
        spec = importlib.util.spec_from_file_location(
            "retrieve_context_question_under_test", TEMPLATE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    source = render_retrieval_model(TEMPLATE.read_text(encoding="utf-8"), SETTINGS)
    module = types.ModuleType("retrieve_context_manifested_question_under_test")
    exec(compile(source, "manifested.py", "exec"), module.__dict__)
    return module


def _wired(module, hits=None):
    """Replace the store and the embedding call; record what they are given."""
    seen = {"embedded": [], "searched": []}

    def embed(question, options):
        seen["embedded"].append(question)
        return [0.1, 0.2]

    def search(vector, k, filter_json, store):
        seen["searched"].append((vector, k, filter_json))
        return [dict(HIT)] if hits is None else hits

    module._store_config = lambda options: {"host": "db.example"}
    module._embed_query = embed
    module._search = search
    return seen


# ---------------------------------------------------------------------------
# the padded question
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("manifested", [False, True])
def test_a_question_padded_to_its_declared_length_is_worked_on_without_the_padding(
        manifested):
    """The input as SAS Intelligent Decisioning handed it over."""
    module = _load(manifested)
    seen = _wired(module)
    padded = QUESTION.ljust(1000)
    assert len(padded) == 1000

    context_dg, context_envelope, status, _ = module.execute(padded, 0, "", "vector", "")

    assert status == "ok"
    assert seen["embedded"] == [QUESTION]
    envelope = json.loads(context_envelope)
    assert envelope["query"] == QUESTION
    assert len(envelope["hits"]) == 1
    assert json.loads(context_dg)[1]["data"][0][-1] == "Thirty days."


def test_the_envelope_of_a_short_question_is_short_again():
    """1134 characters were seen live for a short question and no hit."""
    module = _load()
    _wired(module, hits=[])
    _, context_envelope, status, _ = module.execute(QUESTION.ljust(1000), 0, "", "vector", "")
    assert status == "ok - no chunks matched"
    assert len(context_envelope) < 250
    assert "  " not in context_envelope


@pytest.mark.parametrize("padded", [
    QUESTION + " ",                      # as SAS Micro Analytic Service did
    QUESTION + " " * 900,
    "   " + QUESTION,
    "\t" + QUESTION + " \r\n",
    " " * 40 + QUESTION + " " * 40,
])
def test_blanks_at_either_end_are_not_part_of_the_question(padded):
    module = _load()
    seen = _wired(module)
    _, context_envelope, status, _ = module.execute(padded, 0, "", "vector", "")
    assert status == "ok"
    assert seen["embedded"] == [QUESTION]
    assert json.loads(context_envelope)["query"] == QUESTION


def test_blanks_inside_the_question_are_kept():
    module = _load()
    seen = _wired(module)
    spaced = "leave  days   for\tnew employees"
    _, context_envelope, _, _ = module.execute(spaced + "   ", 0, "", "vector", "")
    assert seen["embedded"] == [spaced]
    assert json.loads(context_envelope)["query"] == spaced


def test_the_request_to_the_embedding_model_carries_the_question_unpadded(monkeypatch):
    """Through the real _embed_query, down to the body that is posted."""
    calls = []

    class _Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"embedding": "[0.1, 0.2]"}}

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return _Response()

    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(post=post))
    for name in ("RAGEMBED_ENDPOINT", "RAGEMBED_MODEL", "RAGEMBED_DEPLOYMENT_TYPE",
                 "RAGEMBED_SSLVERIFY", "SAS_SERVICES_URL"):
        monkeypatch.delenv(name, raising=False)
    module = _load()
    module.EMBED_ENDPOINT = "https://scr.example/llm"
    module._store_config = lambda options: {"host": "db.example"}
    module._search = lambda vector, k, filter_json, store: []

    _, _, status, _ = module.execute(QUESTION.ljust(1000), 0, "", "vector", "")

    assert status == "ok - no chunks matched"
    (url, kwargs), = calls
    assert url == "https://scr.example/llm/all_minilm_l6_v2/all_minilm_l6_v2"
    document = next(item for item in kwargs["json"]["inputs"]
                    if item["name"] == "document")
    assert document["value"] == QUESTION


# ---------------------------------------------------------------------------
# no question is still no question
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("nothing", ["", " ", " " * 1000, "\t \r\n", None, 0])
def test_a_question_of_blanks_only_is_still_empty(nothing):
    module = _load()
    seen = _wired(module)
    context_dg, context_envelope, status, _ = module.execute(nothing, 0, "", "vector", "")
    assert status == "retrieval failed: ValueError: question is empty"
    assert seen["embedded"] == [] and seen["searched"] == []
    envelope = json.loads(context_envelope)
    assert envelope["query"] == ""
    assert envelope["hits"] == []
    assert json.loads(context_dg)[1]["data"] == []


def test_a_question_that_is_not_a_string_is_read_as_its_text():
    module = _load()
    seen = _wired(module)
    _, context_envelope, status, _ = module.execute(42, 0, "", "vector", "")
    assert status == "ok"
    assert seen["embedded"] == ["42"]
    assert json.loads(context_envelope)["query"] == "42"


# ---------------------------------------------------------------------------
# the rest of the call is what it was
# ---------------------------------------------------------------------------
def test_everything_else_is_passed_on_as_before():
    module = _load()
    seen = _wired(module)
    _, context_envelope, status, _ = module.execute(
        QUESTION.ljust(1000), 7, '{"doc_id": "d1"}', " Vector ", '{"model": "m"}')
    assert status == "ok"
    assert seen["searched"] == [([0.1, 0.2], 7, '{"doc_id": "d1"}')]
    envelope = json.loads(context_envelope)
    assert envelope["retrieval_mode"] == "vector"
    assert envelope["collection"] == module.COLLECTION
    assert envelope["graph_context"] is None
    assert sorted(envelope) == ["collection", "graph_context", "hits",
                                "ingestion_run_id", "query", "retrieval_mode"]


def test_a_failure_still_returns_the_question_it_was_asked():
    module = _load()
    _wired(module)

    def refuse(options):
        raise RuntimeError("no vector-store credentials resolved")

    module._store_config = refuse
    _, context_envelope, status, _ = module.execute(QUESTION.ljust(1000), 0, "", "vector", "")
    assert status == ("retrieval failed: RuntimeError: no vector-store "
                      "credentials resolved")
    assert json.loads(context_envelope)["query"] == QUESTION


# ---------------------------------------------------------------------------
# a filter the decision left empty
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("empty", ["", None, " ", " " * 1024, "\t\n"])
def test_a_filter_of_blanks_only_is_no_filter(empty):
    """The same padding on the next input: without this, an empty filter
    that arrives as blanks is handed to the JSON parser, and the call ends in
    `retrieval failed: JSONDecodeError`."""
    module = _load()
    assert module._compile_filter(empty) == ("TRUE", [])


def test_a_padded_filter_is_still_the_filter():
    module = _load()
    condition, params = module._compile_filter('{"doc_id": "d1"}'.ljust(1024))
    assert condition == 'TRUE AND "doc_id" = %s'
    assert params == ["d1"]


def test_a_filter_that_is_not_an_object_is_still_refused():
    module = _load()
    with pytest.raises(ValueError, match="must be a JSON object"):
        module._compile_filter("[1, 2]")


# ---------------------------------------------------------------------------
# a retrieval mode the decision left empty
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("manifested", [False, True])
@pytest.mark.parametrize("empty", ["", None, " ", " " * 32, "\t \r\n"])
def test_a_mode_of_blanks_only_is_the_default_without_a_note(manifested, empty):
    """Before, blanks were taken for the name of a mode: the status read
    `ok (retrieval_mode  not available yet - used vector)`, and a decision
    that compares the status with `ok` took a retrieval that worked for one
    that did not."""
    module = _load(manifested)
    _wired(module)
    _, context_envelope, status, _ = module.execute(QUESTION, 0, "", empty, "")
    assert status == "ok"
    assert json.loads(context_envelope)["retrieval_mode"] == "vector"


@pytest.mark.parametrize("mode", ["vector", " Vector ", "VECTOR".ljust(32)])
def test_the_default_mode_padded_or_in_capitals_is_the_default_mode(mode):
    module = _load()
    _wired(module)
    _, _, status, _ = module.execute(QUESTION, 0, "", mode, "")
    assert status == "ok"


def test_another_mode_is_still_answered_with_vector_and_a_note():
    module = _load()
    _wired(module)
    _, context_envelope, status, _ = module.execute(QUESTION, 0, "", " Hybrid ", "")
    assert status == "ok (retrieval_mode hybrid not available yet - used vector)"
    assert json.loads(context_envelope)["retrieval_mode"] == "vector"


# ---------------------------------------------------------------------------
# a number of chunks nobody named
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("manifested", [False, True])
@pytest.mark.parametrize("nothing", [0, 0.0, None, "", " ", " " * 8, "\t\n", "0",
                                     " 0 ", float("nan"), "nan", False])
def test_no_number_of_chunks_is_the_manifested_default(manifested, nothing):
    """Blanks only failed the call before: `retrieval failed: ValueError:
    could not convert string to float`."""
    module = _load(manifested)
    seen = _wired(module)
    _, _, status, _ = module.execute(QUESTION, nothing, "", "vector", "")
    assert status == "ok"
    assert [k for _vector, k, _filter in seen["searched"]] == [module.DEFAULT_K]


@pytest.mark.parametrize("k,asked", [(7, 7), (7.9, 7), ("7", 7), (" 7 ", 7),
                                     ("7.0", 7), ("1e1", 10), (True, 1)])
def test_a_number_of_chunks_is_passed_on_as_its_whole_part(k, asked):
    module = _load()
    seen = _wired(module)
    _, _, status, _ = module.execute(QUESTION, k, "", "vector", "")
    assert status == "ok"
    assert [k for _vector, k, _filter in seen["searched"]] == [asked]


@pytest.mark.parametrize("k,said", [("many", "many"), (" five ", "five"),
                                    ("4 chunks", "4 chunks"), ([4], "[4]")])
def test_a_k_that_is_no_number_is_the_default_and_the_status_says_so(k, said):
    """The call is not failed for it: the default is used, and the status
    names the value that was not."""
    module = _load()
    seen = _wired(module)
    _, _, status, _ = module.execute(QUESTION, k, "", "vector", "")
    assert status == "ok (k %s is not a number - used %d)" % (said, module.DEFAULT_K)
    assert [k for _vector, k, _filter in seen["searched"]] == [module.DEFAULT_K]


def test_both_notes_stand_in_one_status():
    module = _load()
    _wired(module, hits=[])
    _, _, status, _ = module.execute(QUESTION, "many", "", "hybrid", "")
    assert status == ("ok - no chunks matched (retrieval_mode hybrid not "
                      "available yet - used vector) (k many is not a number - "
                      "used %d)" % module.DEFAULT_K)


def test_a_negative_number_of_chunks_is_passed_on_as_it_is():
    """Unchanged, and pinned so that it is seen: the database answers a
    negative LIMIT, not this function."""
    module = _load()
    seen = _wired(module)
    module.execute(QUESTION, -3, "", "vector", "")
    assert [k for _vector, k, _filter in seen["searched"]] == [-3]


# ---------------------------------------------------------------------------
# the status fits the length it is registered with
# ---------------------------------------------------------------------------
def _status_length():
    from rag_core.registration import RETRIEVAL_OUTPUTS

    return {name: length for name, _type, length, _text
            in RETRIEVAL_OUTPUTS}["retrieval_status"]


def test_a_status_with_both_notes_fits_its_declared_length():
    """An output longer than the length it is registered with makes SAS
    Micro Analytic Service fail the execution. The notes quote what the
    caller sent, so each quotes at most 40 characters of it."""
    module = _load()
    _wired(module, hits=[])
    _, _, status, _ = module.execute(QUESTION, "k" * 5000, "", "m" * 5000, "")
    assert status.startswith("ok - no chunks matched (retrieval_mode " + "m" * 40
                             + " not available yet")
    assert "(k " + "k" * 40 + " is not a number" in status
    assert len(status) <= _status_length()
    assert len(status) < 200


def test_a_failure_with_a_long_message_fits_its_declared_length():
    module = _load()
    _wired(module)

    def refuse(options):
        raise RuntimeError("x" * 5000)

    module._store_config = refuse
    _, _, status, _ = module.execute(QUESTION, 0, "", "vector", "")
    assert status == "retrieval failed: RuntimeError: " + "x" * 400
    assert len(status) <= _status_length()

