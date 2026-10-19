# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What two deployments of 2026-10-18/19 proved about the newest models, as
mdb rules - no network.

- GPT-6 takes other reasoning_effort words than its family: gpt-6-luna
  none..xhigh, gpt-6.1-sol low..xhigh, and both refuse 'minimal'. A
  definition carries its own `value_map` on the option, filled by `mdb add`
  from the catalog.
- Claude 5.5 refuses temperature / top_p / top_k (other than the default)
  and thinking {type: enabled}; it takes output_config.effort, offered as
  reasoning_effort, and gets max_tokens 4000 so a judge verdict is not cut off.
- inception/mercury-decide is a decision model of the OpenRouter snapshot.
- `mdb load-facts` loads an empty table for a kind without definitions,
  because the LLM Usage Report reads all three.
- Every definition of the repository still regenerates byte for byte.
"""
import contextlib
import importlib.util
import io
import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from mdb import cli
from mdb.core.facts import COLUMNS_BY_KIND, empty_sheet
from mdb.core.generator import (
    GenerationError, drop_family_value_maps, effective_score_file, render_assets,
)
from mdb.core.manifest import MANIFEST_FILENAME, OptionSpec, load_manifest
from mdb.core.paths import DEFINITION_DIRS, FACT_SHEETS
from mdb.providers import load_adapters
from mdb.providers.anthropic import _catalog_model
from mdb.providers.base import ADAPTIVE_MAX_TOKENS, CatalogModel, effort_value_map
from mdb.viya import cas

SAMPLING = ("temperature", "top_p", "top_k")
NEW_LLMS = {"gpt_6_luna": ("openai", "gpt-6-luna"), "gpt_61_sol": ("openai", "gpt-6.1-sol"),
            "claude_haiku_5_5": ("anthropic", "claude-haiku-5-5"),
            "claude_sonnet_5_5": ("anthropic", "claude-sonnet-5-5"),
            "claude_opus_5_5": ("anthropic", "claude-opus-5-5")}


def _lf(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


def _printed(monkeypatch) -> io.StringIO:
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", cli._Console(file=out, width=1000, force_terminal=False, emoji=False))
    return out


def _static(core, adapter_id: str, ref: str) -> CatalogModel:
    adapter = load_adapters()[adapter_id]
    return next(m for m in adapter.static_catalog(core.core_dir) if m.ref == ref)


def _added(core, adapter_id: str, ref: str, model_id: str):
    """The manifest `mdb add <adapter> <ref> --offline` builds, before it is written."""
    adapter = load_adapters()[adapter_id]
    manifest = adapter.build_manifest(_static(core, adapter_id, ref), model_id, {}, "test")
    drop_family_value_maps(manifest, core)
    return manifest


def _payload_line(manifest, core, needle: str) -> str:
    score = render_assets(manifest, core)[effective_score_file(manifest)].decode("utf-8")
    return next(line for line in score.splitlines() if needle in line)


def _scorer(tmp_path: Path, manifest, core):
    path = tmp_path / effective_score_file(manifest)
    path.write_bytes(render_assets(manifest, core)[effective_score_file(manifest)])
    spec = importlib.util.spec_from_file_location(f"scorer_{manifest.model_id}_{id(path)}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Sent:
    """requests.post of a scorer: records the payload, answers like the provider."""

    def __init__(self, body: dict):
        self.payloads, self.body = [], body

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.payloads.append(json)
        body = self.body
        return type("R", (), {"status_code": 200, "json": lambda self: body, "text": ""})()


ANTHROPIC_ANSWER = {"content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "OK"}],
                    "usage": {"input_tokens": 18, "output_tokens": 4}}
OPENAI_ANSWER = {"choices": [{"message": {"content": "OK"}}], "usage": {"prompt_tokens": 9, "completion_tokens": 3}}


# -- the effort map a catalog's accepted values give --------------------------

@pytest.mark.parametrize("accepted, expected", [
    (["none", "low", "medium", "high", "xhigh"],
     {"minimal": "none", "low": "low", "medium": "medium", "high": "high", "maximum": "xhigh"}),
    (["low", "medium", "high", "xhigh"],
     {"minimal": "low", "low": "low", "medium": "medium", "high": "high", "maximum": "xhigh"}),
    (["low", "medium", "high", "xhigh", "max"],
     {"minimal": "low", "low": "low", "medium": "medium", "high": "high", "maximum": "max"}),
    # GPT-5: the openai_chat family's own map
    (["minimal", "low", "medium", "high"],
     {"minimal": "minimal", "low": "low", "medium": "medium", "high": "high", "maximum": "high"}),
    # a level the provider lacks goes to the nearest one, the higher on a tie
    (["low", "high"], {"minimal": "low", "low": "low", "medium": "high", "high": "high", "maximum": "high"}),
    # the order the catalog lists them in does not matter
    (["xhigh", "low", "none"], {"minimal": "none", "low": "low", "medium": "low", "high": "xhigh", "maximum": "xhigh"}),
])
def test_effort_value_map_from_accepted_values(accepted, expected):
    assert effort_value_map(accepted) == expected


@pytest.mark.parametrize("accepted", [None, [], ["turbo"]])
def test_effort_value_map_without_known_values_is_none(accepted):
    assert effort_value_map(accepted) is None


def test_the_gpt5_style_map_is_the_family_map(core):
    family = core.vocabulary["reasoning_effort"]["families"]["openai_chat"]["value_map"]
    assert effort_value_map(["minimal", "low", "medium", "high"]) == family
    anthropic = core.vocabulary["reasoning_effort"]["families"]["anthropic_messages"]["value_map"]
    assert effort_value_map(["low", "medium", "high", "xhigh", "max"]) == anthropic


# -- value_map on the option: the manifest's rules ----------------------------

@pytest.mark.parametrize("value_map, words", [
    ({}, "value_map is empty"),
    ({"minimal": ""}, "value_map['minimal'] must be a non-empty text"),
    ({"minimal": "  "}, "value_map['minimal'] must be a non-empty text"),
    ({"": "low"}, "an empty key was given"),
])
def test_value_map_texts_are_checked_by_the_manifest(value_map, words):
    with pytest.raises(ValidationError, match=words.replace("[", r"\[").replace("]", r"\]")):
        OptionSpec(default="medium", value_map=value_map)


def test_value_map_of_a_definition_renders_in_place_of_the_family_map(core):
    manifest = _added(core, "openai", "gpt-6-luna", "luna")
    line = _payload_line(manifest, core, '"reasoning_effort": {')
    assert line.strip() == ('"reasoning_effort": {"minimal": "none", "low": "low", "medium": "medium", '
                            '"high": "high", "maximum": "xhigh"}.get(str(options["reasoning_effort"]), "medium"),')


@pytest.mark.parametrize("value_map, words", [
    ({"minimal": "none", "low": "low", "medium": "medium", "high": "high"}, "no entry for maximum"),
    ({"minimal": "none", "low": "low", "medium": "medium", "high": "high", "maximum": "xhigh", "turbo": "x"},
     "turbo is not a value of the option"),
])
def test_value_map_keys_must_be_the_normalized_values(core, value_map, words):
    manifest = _added(core, "openai", "gpt-6-luna", "luna")
    manifest.options["reasoning_effort"].value_map = value_map
    with pytest.raises(GenerationError, match=words) as raised:
        render_assets(manifest, core)
    assert "minimal | low | medium | high | maximum" in str(raised.value)


def test_value_map_on_an_option_its_family_does_not_translate_is_refused(core):
    manifest = _added(core, "openai", "gpt-6-luna", "luna")
    manifest.options["max_completion_tokens"].value_map = {"a": "b"}
    with pytest.raises(GenerationError, match="does not translate its values - remove value_map"):
        render_assets(manifest, core)


def test_a_map_equal_to_the_family_map_is_not_written(core):
    manifest = _added(core, "openai", "gpt-6-luna", "luna")
    manifest.options["reasoning_effort"].value_map = dict(
        core.vocabulary["reasoning_effort"]["families"]["openai_chat"]["value_map"])
    assert drop_family_value_maps(manifest, core) == ["reasoning_effort"]
    assert manifest.options["reasoning_effort"].value_map is None


# -- GPT-6: what mdb add writes, and what the scorer sends ---------------------

@pytest.mark.parametrize("ref, minimal", [("gpt-6-luna", "none"), ("gpt-6.1-sol", "low")])
def test_gpt6_is_a_reasoning_model_with_its_own_effort_words(core, tmp_path, monkeypatch, ref, minimal):
    manifest = _added(core, "openai", ref, "gpt6")
    assert list(manifest.options) == ["reasoning_effort", "max_completion_tokens"]
    assert manifest.options["reasoning_effort"].value_map["minimal"] == minimal
    assert manifest.options["reasoning_effort"].value_map["maximum"] == "xhigh"
    assert manifest.metadata.context_length == 1050000
    scorer = _scorer(tmp_path, manifest, core)
    sent = _Sent(OPENAI_ANSWER)
    monkeypatch.setattr(scorer.requests, "post", sent)
    for level in ("minimal", "maximum", "medium"):
        scorer.scoreModel(["Hi"], ["Be terse."], [json.dumps({"API_KEY": "k", "reasoning_effort": level})])
    assert [p["reasoning_effort"] for p in sent.payloads] == [minimal, "xhigh", "medium"]
    assert all("max_tokens" not in p and "temperature" not in p for p in sent.payloads)


@pytest.mark.parametrize("ref, prices", [("gpt-6-luna", (1e-07, 5e-07)), ("gpt-6.1-sol", (2e-06, 1e-05)),
                                         ("claude-haiku-5-5", (1e-07, 5e-07)),
                                         ("claude-sonnet-5-5", (2e-06, 1e-05)),
                                         ("claude-opus-5-5", (4e-06, 2e-05))])
def test_catalog_prices_are_per_token_without_float_noise(core, ref, prices):
    adapter_id = "openai" if ref.startswith("gpt") else "anthropic"
    pricing = _added(core, adapter_id, ref, "m").metadata.pricing
    # equal to the literal, so definition.yaml says 1.0e-07 and not 1.0000000000000001e-07
    assert (pricing.input_token_price, pricing.output_token_price) == prices


# -- Claude 5.5: no sampling, effort, room for thinking ------------------------

@pytest.mark.parametrize("ref", ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"])
def test_claude_55_gets_effort_and_max_tokens_and_no_sampling(core, ref):
    manifest = _added(core, "anthropic", ref, "claude")
    assert list(manifest.options) == ["reasoning_effort", "max_tokens"]
    assert manifest.options["max_tokens"].default == ADAPTIVE_MAX_TOKENS == 4000
    assert manifest.options["max_tokens"].max == 128000
    assert manifest.options["reasoning_effort"].default == "medium"
    # low..max is the anthropic_messages family's own map: nothing written
    assert manifest.options["reasoning_effort"].value_map is None
    assert manifest.metadata.context_length == 1000000
    score = render_assets(manifest, core)[effective_score_file(manifest)].decode("utf-8")
    assert '"output_config": {"effort": {"minimal": "low", "low": "low", "medium": "medium", "high": "high", ' \
           '"maximum": "max"}.get(str(options["reasoning_effort"]), "medium")},' in score
    for word in (*SAMPLING, "thinking", "budget_tokens"):
        assert f'"{word}"' not in score


def test_claude_55_scorer_sends_effort_and_ignores_a_temperature_option(core, tmp_path, monkeypatch):
    scorer = _scorer(tmp_path, _added(core, "anthropic", "claude-haiku-5-5", "haiku"), core)
    sent = _Sent(ANTHROPIC_ANSWER)
    monkeypatch.setattr(scorer.requests, "post", sent)
    response, _, prompt_length, output_length = scorer.scoreModel(
        ["Hi"], ["Be terse."], ['{API_KEY:k,temperature:0,top_p:0.5,reasoning_effort:maximum}'])
    assert (response, prompt_length, output_length) == ("OK", 18, 4)  # the thinking block is not the answer
    scorer.scoreModel(["Hi"], ["Be terse."], ['{API_KEY:k,reasoning_effort:minimal}'])
    scorer.scoreModel(["Hi"], ["Be terse."], ['{API_KEY:k}'])
    assert [p["output_config"] for p in sent.payloads] == [{"effort": "max"}, {"effort": "low"}, {"effort": "medium"}]
    assert all(p["max_tokens"] == 4000 for p in sent.payloads)
    assert not any(set(p) & {*SAMPLING, "thinking"} for p in sent.payloads)


def test_claude_45_keeps_sampling_and_the_thinking_budget(core):
    manifest = _added(core, "anthropic", "claude-sonnet-4-5-20250929", "sonnet45")
    assert list(manifest.options) == ["temperature", "top_p", "max_tokens", "thinking_budget"]
    assert manifest.options["max_tokens"].default == 1000


def test_effort_and_thinking_budget_exclude_each_other(core):
    manifest = _added(core, "anthropic", "claude-haiku-5-5", "haiku")
    manifest.options["thinking_budget"] = OptionSpec(default=0)
    with pytest.raises(GenerationError, match="'reasoning_effort' and 'thinking_budget' exclude each other"):
        render_assets(manifest, core)


def test_anthropic_live_entry_reads_window_output_cap_and_thinking():
    adaptive = _catalog_model({
        "id": "claude-haiku-5-5", "display_name": "Claude Haiku 5.5", "created_at": "2026-10-07T18:00:00Z",
        "max_input_tokens": 1000000, "max_tokens": 128000,
        "capabilities": {"thinking": {"supported": True, "types": {"enabled": {"supported": False},
                                                                   "adaptive": {"supported": True}}},
                         "effort": {"supported": True, **{w: {"supported": True}
                                                          for w in ("low", "medium", "high", "xhigh", "max")}}}})
    assert (adaptive.context_length, adaptive.max_output_tokens, adaptive.release_date) == (1000000, 128000,
                                                                                            "2026-10-07")
    assert (adaptive.adaptive_thinking, adaptive.sampling, adaptive.extended_thinking) == (True, False, False)
    assert adaptive.reasoning_efforts == ["low", "medium", "high", "xhigh", "max"]
    budget = _catalog_model({
        "id": "claude-sonnet-4-5-20250929", "max_input_tokens": 200000, "max_tokens": 64000,
        "capabilities": {"thinking": {"types": {"enabled": {"supported": True}, "adaptive": {"supported": False}}},
                         "effort": {"supported": False, "low": {"supported": False}}}})
    assert (budget.adaptive_thinking, budget.sampling, budget.extended_thinking) == (False, True, True)
    assert budget.reasoning_efforts is None
    bare = _catalog_model({"id": "claude-x"})  # an entry from before the Models API said more
    assert (bare.context_length, bare.max_output_tokens, bare.extended_thinking, bare.sampling) == (
        200000, None, True, True)


def test_live_entry_without_capabilities_takes_the_snapshot_rules(core):
    live = [_catalog_model({"id": "claude-opus-5-5", "display_name": "Claude Opus 5.5"})]
    static = load_adapters()["anthropic"].static_catalog(core.core_dir)
    model = cli._enrich_from_static(live, static)[0]
    assert (model.adaptive_thinking, model.sampling, model.extended_thinking) == (True, False, False)
    assert model.reasoning_efforts == ["low", "medium", "high", "xhigh", "max"]
    assert model.source == "live, enriched from static snapshot 2026-10-19"


def test_openai_live_listing_of_gpt6_gets_the_snapshot_effort_words(core):
    live = [CatalogModel(ref="gpt-6-luna", display_name="gpt-6-luna", source="live")]
    model = cli._enrich_from_static(live, load_adapters()["openai"].static_catalog(core.core_dir))[0]
    assert (model.display_name, model.reasoning) == ("GPT-6 Luna", True)
    assert model.reasoning_efforts == ["none", "low", "medium", "high", "xhigh"]


# -- Mercury Decide is a decision model of the snapshot ------------------------

def test_mercury_decide_is_a_decision_model_with_its_price(core):
    model = _static(core, "openrouter", "inception/mercury-decide")
    assert (model.kind, model.context_length, model.per_token_input, model.per_token_output) == (
        "decision", 65536, 2e-08, 0.0)
    manifest = _added(core, "openrouter", "inception/mercury-decide", "mercury")
    assert (manifest.kind, manifest.runtime.template, manifest.options) == ("decision", "dec_systemone_api", {})


# -- mdb add writes exactly what the repository holds --------------------------

@pytest.mark.parametrize("model_id", [*NEW_LLMS, "mercury_decide"])
def test_mdb_add_offline_writes_the_committed_definition(tmp_path, monkeypatch, repo_root, model_id):
    monkeypatch.setenv("MDB_REPO", str(repo_root))
    monkeypatch.setenv("MDB_DEFINITIONS", str(tmp_path / "defs"))
    monkeypatch.setenv("SAS_RESPONSIBLE_PARTY", "SAS Agentic AI Accelerator")
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    committed = repo_root / ("Decision-Definitions" if model_id == "mercury_decide" else "LLM-Definitions") / model_id
    manifest = load_manifest(committed)
    args = [manifest.provider.adapter, manifest.provider.model_version, "--id", model_id, "--offline", "--yes"]
    if model_id == "mercury_decide":
        args += ["--description", manifest.metadata.description]
    out = _printed(monkeypatch)
    result = CliRunner().invoke(cli.app, ["add", *args])
    assert result.exit_code == 0, out.getvalue()
    written = tmp_path / "defs" / committed.parent.name / model_id
    files = {p.name: _lf(p) for p in written.iterdir() if p.is_file()}
    assert files == {p.name: _lf(p) for p in committed.iterdir() if p.is_file()}


# -- every committed definition regenerates byte for byte ----------------------

def _committed_definitions(repo_root: Path) -> list:
    return [folder for name in DEFINITION_DIRS.values() for folder in sorted((repo_root / name).iterdir())
            if (folder / MANIFEST_FILENAME).is_file()]


def test_every_committed_definition_regenerates_byte_for_byte(core, repo_root):
    definitions = _committed_definitions(repo_root)
    assert len(definitions) >= 57
    for folder in definitions:
        manifest = load_manifest(folder)
        for name, content in render_assets(manifest, core).items():
            if name in manifest.generation.overrides:
                continue
            # LF as the drift gate compares: a Windows checkout (core.autocrlf) holds CRLF
            assert _lf(folder / name) == content, f"{folder.name}/{name} differs from a fresh render"


def test_only_the_gpt6_definitions_carry_a_value_map(repo_root):
    carrying = sorted(folder.name for folder in _committed_definitions(repo_root)
                      if "value_map" in (folder / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert carrying == ["gpt_61_sol", "gpt_6_luna"]


# -- load-facts: an empty table for a kind without definitions -----------------

class _CAS:
    """A stand-in CAS (casManagement REST): records each uploaded table."""

    def __init__(self):
        self.uploads = {}

    def get(self, path, **kw):
        return type("R", (), {"status_code": 200, "json": lambda self: {"items": [{"name": "cas-shared-default"}]},
                              "text": ""})()

    def put(self, path, **kw):
        return type("R", (), {"status_code": 404, "json": lambda self: {}, "text": ""})()

    def post(self, path, **kw):
        if path.endswith("/tables"):
            self.uploads[kw["data"]["tableName"]] = kw["files"]["file"][1]
        return type("R", (), {"status_code": 201, "json": lambda self: {}, "text": ""})()


def _site(tmp_path, monkeypatch, repo_root, kinds: dict) -> Path:
    """A relocated definitions root: for each kind, the folders to copy and
    whether its sheet comes along."""
    defs = tmp_path / "defs"
    for kind, (folders, with_sheet) in kinds.items():
        source, target = repo_root / DEFINITION_DIRS[kind], defs / DEFINITION_DIRS[kind]
        target.mkdir(parents=True, exist_ok=True)
        for name in folders:
            shutil.copytree(source / name, target / name, ignore=shutil.ignore_patterns("__pycache__"))
        if with_sheet:
            shutil.copy2(source / FACT_SHEETS[kind], target / FACT_SHEETS[kind])
    monkeypatch.setenv("MDB_REPO", str(repo_root))
    monkeypatch.setenv("MDB_DEFINITIONS", str(defs))
    return defs


def test_load_facts_loads_an_empty_table_for_a_kind_without_definitions(tmp_path, monkeypatch, repo_root):
    _site(tmp_path, monkeypatch, repo_root, {"llm": (["claude_haiku_5_5"], True),
                                             "decision": (["mercury_decide"], True)})
    stand_in = _CAS()
    monkeypatch.setattr(cli, "_viya_session", lambda: contextlib.nullcontext(stand_in))
    out = _printed(monkeypatch)
    result = CliRunner().invoke(cli.app, ["load-facts"])
    assert result.exit_code == 0, out.getvalue()
    assert set(stand_in.uploads) == {"LLM_FACT_SHEET", "EMBEDDING_FACT_SHEET", "DECISION_FACT_SHEET"}
    assert stand_in.uploads["EMBEDDING_FACT_SHEET"] == empty_sheet("embedding") == (
        ",".join(COLUMNS_BY_KIND["embedding"]) + "\n").encode("utf-8")
    assert stand_in.uploads["DECISION_FACT_SHEET"] == (repo_root / "Decision-Definitions"
                                                       / FACT_SHEETS["decision"]).read_bytes()
    lines = [line.strip() for line in out.getvalue().splitlines()]
    assert ("EMBEDDING_FACT_SHEET: no embedding definitions - loaded an empty table with the sheet's columns "
            "(the LLM Usage Report reads it)") in lines
    assert "LLM_FACT_SHEET: uploaded, promoted (global) and saved to disk" in lines
    # nothing is written into the site's folders
    assert not (tmp_path / "defs" / "Embedding-Definitions" / FACT_SHEETS["embedding"]).exists()


def test_load_facts_skips_a_kind_whose_definitions_have_no_sheet(tmp_path, monkeypatch, repo_root):
    _site(tmp_path, monkeypatch, repo_root, {"llm": (["claude_haiku_5_5"], True),
                                             "embedding": (["voyage_35"], False)})
    stand_in = _CAS()
    monkeypatch.setattr(cli, "_viya_session", lambda: contextlib.nullcontext(stand_in))
    out = _printed(monkeypatch)
    result = CliRunner().invoke(cli.app, ["load-facts"])
    assert result.exit_code == 0, out.getvalue()
    assert set(stand_in.uploads) == {"LLM_FACT_SHEET", "DECISION_FACT_SHEET"}
    assert "embedding_fact_sheet.csv not found - run 'mdb sync --rebuild' first, skipping." in out.getvalue()


def test_load_facts_without_any_sheet_signs_in_to_nothing(tmp_path, monkeypatch, repo_root):
    _site(tmp_path, monkeypatch, repo_root, {"llm": ([], False)})
    monkeypatch.setattr(cli, "_viya_session", lambda: pytest.fail("signed in without a sheet to load"))
    out = _printed(monkeypatch)
    result = CliRunner().invoke(cli.app, ["load-facts"])
    assert result.exit_code == 1
    assert "No fact sheet was loaded - generate them first with 'mdb sync --rebuild'." in out.getvalue()


def test_the_stand_in_follows_the_cas_contract(tmp_path):
    # the stand-in answers what mdb.viya.cas asks: unload (404: nothing
    # loaded), upload to the tables collection, save the item
    sheet = tmp_path / "e.csv"
    sheet.write_bytes(empty_sheet("embedding"))
    stand_in = _CAS()
    assert cas.load_fact_sheet(stand_in, sheet, "embedding", "Public", "cas-shared-default") == {
        "table": "EMBEDDING_FACT_SHEET", "dropped": False}
    assert stand_in.uploads == {"EMBEDDING_FACT_SHEET": empty_sheet("embedding")}
