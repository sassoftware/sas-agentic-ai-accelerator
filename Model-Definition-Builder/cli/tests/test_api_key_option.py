# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""A call without the API_KEY option to a hosted model (live test on zeus,
2026-10-20): the score code read options['API_KEY'] unchecked, failed with a
KeyError, and the SCR container answered HTTP 422 whose errorMessage was only
the marker line of the traceback ('~~~~~~~^^^^^^^^^^^').

The SCR container answers a call whose score code raised with the second to
last line of the traceback - the source line of the raise, or its marker
line - not the text of the exception. Its logs show it: 'Error occurred in
the model scoring function:     raise RuntimeError(f"Request failed: ...")'.

- every template that sends the key reads it through _api_key(): a missing
  or blank API_KEY option fails before any request, naming the option
- the line SCR shows the caller names the option (or the variable in
  auth.mode env, and the variable of each Azure environment value)
- no template or partial spreads a raise over several lines, which SCR would
  show as 'raise RuntimeError('
- every committed scorer that sends the key goes through _api_key()
"""
import json
import traceback

import pytest

from mdb.core.generator import KEY_OPTION_TEMPLATES, render_assets

from test_env_api_keys import CASES, DUMMY, HEADER, _call, _load_scorer, _manifest
from test_azure_environments import _azure_env_manifest, _clear_azure_env

OPTION_MESSAGE = "No API key: pass the provider key in the API_KEY option of the call."


def _scr_error_line(module, kind, options) -> str:
    """What the SCR container puts behind '422 Unprocessable Entity:' for a call that
    raised: the second to last line of the traceback."""
    try:
        _call(module, kind, options)
    except Exception:
        return traceback.format_exc().splitlines()[-2].strip()
    raise AssertionError("the call did not fail")


def _scorer_without_requests(tmp_path, manifest, core, monkeypatch):
    module = _load_scorer(tmp_path, manifest, core)
    sent = []

    class Captured(Exception):
        pass

    def post(url, headers=None, **_):
        sent.append(headers)
        raise Captured()

    monkeypatch.setattr(module.requests, "post", post)
    return module, sent, Captured


def test_every_key_template_is_covered():
    assert set(CASES) == set(KEY_OPTION_TEMPLATES)


@pytest.mark.parametrize("template", sorted(CASES))
def test_a_call_without_the_option_fails_naming_it(template, tmp_path, monkeypatch, core):
    manifest = _manifest(template)
    assert "API_KEY" in json.loads(render_assets(manifest, core)["options.json"])
    monkeypatch.setenv("AZURE_OPENAI_RESOURCE", "res")
    module, sent, Captured = _scorer_without_requests(tmp_path, manifest, core, monkeypatch)
    # No option, a blank one, one of blanks, and the options of the live test (a string without API_KEY).
    for options in ({}, {"API_KEY": ""}, {"API_KEY": "   "}, {"API_KEY": None}, {"temperature": 0.2}):
        with pytest.raises(RuntimeError) as refused:
            _call(module, manifest.kind, options)
        assert str(refused.value) == OPTION_MESSAGE
    assert sent == []
    # A key still travels as before, in the provider's header.
    header = HEADER.get(template, "Authorization")
    with pytest.raises(Captured):
        _call(module, manifest.kind, {"API_KEY": DUMMY})
    assert sent[-1][header] == (f"Bearer {DUMMY}" if header == "Authorization" else DUMMY)


@pytest.mark.parametrize("template", sorted(CASES))
def test_the_line_scr_shows_names_the_option(template, tmp_path, monkeypatch, core):
    manifest = _manifest(template)
    monkeypatch.setenv("AZURE_OPENAI_RESOURCE", "res")
    module, _, _ = _scorer_without_requests(tmp_path, manifest, core, monkeypatch)
    assert _scr_error_line(module, manifest.kind, {}) == f'raise RuntimeError("{OPTION_MESSAGE}")'


@pytest.mark.parametrize("template", sorted(CASES))
def test_the_line_scr_shows_names_the_variable_in_env_mode(template, tmp_path, monkeypatch, core):
    manifest = _manifest(template, "env")
    env_var = manifest.provider.auth.env_var
    monkeypatch.setenv("AZURE_OPENAI_RESOURCE", "res")
    monkeypatch.delenv(env_var, raising=False)
    module, sent, _ = _scorer_without_requests(tmp_path, manifest, core, monkeypatch)
    assert _scr_error_line(module, manifest.kind, {}) == (
        f'raise RuntimeError("No API key: set the {env_var} environment variable on this container '
        '(or pass an API_KEY option).")')
    assert sent == []


def test_the_lines_scr_shows_for_the_azure_environment(tmp_path, monkeypatch, core):
    _clear_azure_env(monkeypatch)
    module = _load_scorer(tmp_path, _azure_env_manifest(), core)
    monkeypatch.setattr(module.requests, "post", lambda *a, **k: pytest.fail("no request without a key"))
    assert _scr_error_line(module, "llm", {}) == (
        'raise RuntimeError("No Azure API key: set the AZURE_OPENAI_API_KEY environment variable on this '
        'container.")')
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", DUMMY)
    assert _scr_error_line(module, "llm", {}).startswith(
        'raise RuntimeError("No Azure resource: set the AZURE_OPENAI_RESOURCE environment variable')
    monkeypatch.setattr(module, "defaultDeploymentName", "")
    assert _scr_error_line(module, "llm", {}) == (
        'raise RuntimeError("No Azure deployment: set the AZURE_OPENAI_DEPLOYMENT environment variable on '
        'this container.")')


def test_no_template_spreads_a_raise_over_several_lines(core):
    templates = core.core_dir / "templates"
    spread = [f"{path.relative_to(templates)}:{number}"
              for path in sorted(templates.rglob("*.py.j2"))
              for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
              if line.rstrip().endswith("raise RuntimeError(")]
    assert spread == []


def test_every_committed_scorer_that_sends_the_key_checks_it(repo_root):
    reading = []
    for folder in ("LLM-Definitions", "Embedding-Definitions", "Decision-Definitions"):
        for path in sorted((repo_root / folder).glob("*/*.py")):
            text = path.read_text(encoding="utf-8")
            if "options['API_KEY']" in text or 'options["API_KEY"]' in text:
                reading.append(path)
                assert '    options["API_KEY"] = _api_key(options)\n' in text, path
                assert "def _api_key(options):" in text, path
    assert len(reading) >= 30
