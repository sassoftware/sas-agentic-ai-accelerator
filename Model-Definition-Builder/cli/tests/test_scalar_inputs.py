# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Issue #26: a scorer must read the same value whether its inputs arrive as
one-element lists (CAS / DATA step / the SCR container) or as plain strings
(the MAS REST API). A str answers len() and [0] too, so the old indexing did
not fail under MAS - it silently scored the first character of the prompt."""
import json
from pathlib import Path

import pytest

from mdb.core.generator import effective_score_file, render_assets
from mdb.providers import load_adapters
from mdb.providers.base import CatalogModel

TEMPLATES = Path(__file__).resolve().parents[3] / "Model-Definition-Builder" / "definition-core" / "templates"
INPUTS = ("userPrompt", "systemPrompt", "document", "project")


class _Series:
    """Just enough of a pandas Series: what SCR hands a scorer."""

    def __init__(self, *items):
        self._items = list(items)

    def __len__(self):
        return len(self._items)

    @property
    def iloc(self):
        return self._items


class _Resp:
    status_code = 200

    def __init__(self, kind):
        self._body = ({"data": [{"embedding": [0.5, 0.25]}], "usage": {"prompt_tokens": 7}}
                      if kind == "embedding" else
                      {"choices": [{"message": {"content": "OK"}}],
                       "usage": {"prompt_tokens": 9, "completion_tokens": 1}})

    def json(self):
        return self._body


def _scorer(core, kind, monkeypatch):
    """Exec the rendered OpenAI scorer of one kind with requests.post captured."""
    adapter = load_adapters()["openai"]
    cm = CatalogModel(ref="m", display_name="M", kind=kind, source="manual")
    manifest = adapter.build_manifest(cm, f"scalar_{kind}", {}, "tester")
    src = render_assets(manifest, core)[effective_score_file(manifest)].decode()
    ns: dict = {}
    exec(src, ns)
    seen: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, body=json)
        return _Resp(kind)

    monkeypatch.setattr(ns["requests"], "post", fake_post)
    return ns, seen


# -- every template, by source --------------------------------------------------

def test_no_template_indexes_an_input_any_more():
    for template in sorted((TEMPLATES / "score").glob("*.j2")):
        text = template.read_text(encoding="utf-8")
        for name in INPUTS:
            assert f"{name}[0]" not in text, f"{template.name} still indexes {name}[0]"
        # ...and each one normalises its two inputs through the shared helper.
        assert "= _scalar(" in text, template.name
    assert "def _scalar(value):" in (TEMPLATES / "partials" / "_options_parser.py.j2").read_text(encoding="utf-8")


def test_hand_maintained_scorers_are_fixed_too():
    """The six legacy scorers kept via generation.overrides carry the same fix."""
    root = TEMPLATES.parents[2]
    for rel in ("llama_31_405b/llama31405bScore.py", "llama_32_1b/llama321bScore.py",
                "llama_32_3b/llama323bScore.py", "mistral_nemo/mistralNemoScore.py",
                "phi_35_mini/phi35miniScore.py", "phi_3_mini_4k/phi3Score.py"):
        text = (root / "LLM-Definitions" / rel).read_text(encoding="utf-8")
        assert "def _scalar(value):" in text and "_scalar(userPrompt)" in text, rel
        assert "userPrompt[0]" not in text and "systemPrompt[0]" not in text, rel


# -- the helper's contract ---------------------------------------------------------

def test_scalar_and_options_accept_every_calling_convention(core, monkeypatch):
    ns, _ = _scorer(core, "llm", monkeypatch)
    scalar, parse = ns["_scalar"], ns["_parse_options"]
    for shape in ("What is SAS Viya?", ["What is SAS Viya?"], ("What is SAS Viya?",), _Series("What is SAS Viya?")):
        assert scalar(shape) == "What is SAS Viya?", shape
    assert scalar(None) == "" and scalar([]) == "" and scalar(_Series()) == ""
    opts = {"API_KEY": "k", "temperature": 0.2}
    for shape in (json.dumps(opts), [json.dumps(opts)], _Series(json.dumps(opts)), opts, [opts],
                  "{API_KEY:k,temperature:0.2}"):
        assert parse(shape) == opts, shape
    for empty in (None, "", [], [""], _Series(), _Series("")):
        assert parse(empty) == {}, empty


# -- the scorers, called both ways -------------------------------------------------

PROMPT = "What is SAS Viya? Answer in one sentence."
SYSTEM = "You are a helpful assistant."


def test_chat_scorer_reads_the_whole_prompt_under_both_conventions(core, monkeypatch):
    ns, seen = _scorer(core, "llm", monkeypatch)
    options = json.dumps({"API_KEY": "k-1", "temperature": 0.2})
    # MAS REST: plain strings.
    response, _, prompt_length, output_length = ns["scoreModel"](PROMPT, SYSTEM, options)
    assert (response, prompt_length, output_length) == ("OK", 9, 1)
    assert seen["body"]["messages"] == [{"role": "system", "content": SYSTEM},
                                        {"role": "user", "content": PROMPT}]
    assert seen["body"]["temperature"] == 0.2 and seen["headers"]["Authorization"] == "Bearer k-1"
    mas_body = seen["body"]
    # CAS / SCR: one-element lists and Series must produce the identical request.
    ns["scoreModel"]([PROMPT], [SYSTEM], [options])
    assert seen["body"] == mas_body
    ns["scoreModel"](_Series(PROMPT), _Series(SYSTEM), _Series(options))
    assert seen["body"] == mas_body


def test_embedding_scorer_reads_the_whole_document_under_both_conventions(core, monkeypatch):
    ns, seen = _scorer(core, "embedding", monkeypatch)
    document = "A document that is definitely longer than one character."
    embedding, _, tokens = ns["scoreModel"](document, "proj", '{"API_KEY": "k-1"}')
    assert json.loads(embedding) == [0.5, 0.25] and tokens == 7
    assert seen["body"]["input"] == document
    ns["scoreModel"]([document], ["proj"], ['{"API_KEY": "k-1"}'])
    assert seen["body"]["input"] == document
