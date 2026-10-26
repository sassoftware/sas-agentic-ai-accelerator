# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bedrock: never temperature and topP in one request.

Claude 4.5 and newer refuse a request that carries both, on Anthropic's API (fixed in 1.1.0) and through
Bedrock's Converse API, where `claude_haiku_4_5_bedrock` still sent both in its inferenceConfig. The
generated scorers of both Bedrock templates now send the one the caller asked for, temperature where the
caller asked for both or neither. They run here against a stand-in for requests and for boto3."""
import sys
import types

import pytest

from mdb.core.generator import effective_score_file, render_assets
from mdb.core.manifest import load_manifest


class _Response:
    status_code = 200

    def json(self):
        return {"output": {"message": {"content": [{"text": "ok"}]}}, "usage": {"inputTokens": 3, "outputTokens": 1}}


def _scorer(score: str, monkeypatch):
    """The scoreModel of a score file, with requests and boto3 stood in for; returns it and the requests made."""
    sent = []
    fake_requests = types.SimpleNamespace(post=lambda url, **kw: sent.append(kw["json"]["inferenceConfig"]) or _Response())

    class _Client:
        def converse(self, **kw):
            sent.append(kw["inferenceConfig"])
            return _Response().json()

    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=lambda *a, **k: _Client()))
    namespace: dict = {"__name__": "bedrock_score"}
    exec(compile(score, "score.py", "exec"), namespace)
    return namespace["scoreModel"], sent


def _score(manifest, core) -> str:
    return render_assets(manifest, core)[effective_score_file(manifest)].decode("utf-8")


@pytest.mark.parametrize("template", ["bedrock_converse", "bedrock_converse_sigv4"])
@pytest.mark.parametrize("asked, expected", [
    ("", {"temperature": 1.0}),
    ("top_p:0.9", {"topP": 0.9}),
    ("temperature:0.2", {"temperature": 0.2}),
    ("temperature:0.2,top_p:0.9", {"temperature": 0.2}),
], ids=["neither", "top_p", "temperature", "both"])
def test_a_request_carries_temperature_or_top_p_never_both(repo_root, core, monkeypatch, template, asked, expected):
    manifest = load_manifest(repo_root / "LLM-Definitions" / "claude_haiku_4_5_bedrock")
    manifest.runtime.template = template
    score_model, sent = _scorer(_score(manifest, core), monkeypatch)

    options = "{" + ",".join(part for part in ("API_KEY:k", asked) if part) + "}"
    response, *_ = score_model("Hi", "Be brief.", options)

    assert response == "ok" and len(sent) == 1
    assert sent[0] == {**expected, "maxTokens": 1000}


def test_the_shipped_bedrock_definition_renders_the_picker(repo_root, core):
    manifest = load_manifest(repo_root / "LLM-Definitions" / "claude_haiku_4_5_bedrock")
    committed = (repo_root / "LLM-Definitions" / "claude_haiku_4_5_bedrock" / effective_score_file(manifest)).read_text(encoding="utf-8")

    assert 'if "top_p" in requested and "temperature" not in requested:' in committed
    assert 'inferenceConfig["topP"] = float(options["top_p"])' in committed
    assert '"topP": float' not in committed
