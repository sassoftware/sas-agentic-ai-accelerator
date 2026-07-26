# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Free-model pricing, the V008 unknown-pricing warning, sorted fact-sheet
upserts, and the rate-limited (HTTP 429) smoke-test classification."""
import shutil
from types import SimpleNamespace

from mdb.core import drift
from mdb.core.facts import remove_row, upsert_row
from mdb.core.generator import render_assets
from mdb.core.manifest import (
    AuthBlock, MetadataBlock, ModelManifest, OptionSpec, PricingBlock,
    ProviderBlock, RuntimeBlock, TagsBlock,
)
from mdb.core.validator import validate_folder
from mdb.providers.base import http_smoke_failure


def _openrouter_manifest(input_price, output_price) -> ModelManifest:
    return ModelManifest(
        model_id="free_test_model",
        display_name="Free Test Model",
        provider=ProviderBlock(
            adapter="openrouter",
            model_version="vendor/free-test-model:free",
            endpoint="https://openrouter.ai/api/v1/chat/completions",
            auth=AuthBlock(mode="api_key", key_name="OpenRouter"),
        ),
        runtime=RuntimeBlock(template="openai_chat", requirements_profile="api-wrapper"),
        options={
            "temperature": OptionSpec(default=1),
            "top_p": OptionSpec(default=1),
            "max_tokens": OptionSpec(default=1000, max=16384),
        },
        tags=TagsBlock(size_class="LLM", license_class="Proprietary",
                       provider_tag="OpenRouter", scr_sizing="small"),
        metadata=MetadataBlock(
            description="A free catalog model.",
            pricing=PricingBlock(cost_type="Tokens",
                                 input_token_price=input_price,
                                 output_token_price=output_price),
        ),
        modeler="tester",
    )


def _validate(manifest, core, tmp_path, fact_sheet):
    folder = tmp_path / manifest.model_id
    folder.mkdir()
    manifest.save(folder)
    rendered = render_assets(manifest, core)
    for name, content in rendered.items():
        (folder / name).write_bytes(content)
    drift.write_lock(folder, (folder / "definition.yaml").read_bytes(), rendered)
    return validate_folder(folder, core, fact_sheet)


def test_v008_fires_for_unknown_pricing(core, tmp_path, fact_sheet):
    issues = _validate(_openrouter_manifest(None, None), core, tmp_path, fact_sheet)
    v008 = [i for i in issues if i.rule == "V008"]
    assert len(v008) == 1 and v008[0].severity == "warning"


def test_v008_accepts_explicit_zero_for_free_models(core, tmp_path, fact_sheet):
    """A :free model's prices ARE 0 - an explicit zero is a correct answer,
    not a placeholder, and must not be flagged."""
    issues = _validate(_openrouter_manifest(0.0, 0.0), core, tmp_path, fact_sheet)
    assert not [i for i in issues if i.rule == "V008"]


def test_upsert_inserts_in_sorted_position(tmp_path, repo_root, fact_sheet):
    """Removing a row and upserting it again reproduces the sheet byte for
    byte - i.e. upsert inserts where rebuild_sheet would put it, so an
    `mdb add` never desyncs the committed sheet from a fresh rebuild."""
    from mdb.core.manifest import load_manifest
    working = tmp_path / "llm_fact_sheet.csv"
    shutil.copy(fact_sheet, working)
    before = working.read_bytes()
    manifest = load_manifest(repo_root / "LLM-Definitions" / "gemini_flash_25")
    assert remove_row(working, "gemini_flash_25") == "removed"
    assert upsert_row(working, manifest) == "added"
    assert working.read_bytes() == before


class _StubPrompt:
    """Answers Prompt.ask by label; anything unlisted keeps its default. A
    list value yields one element per call (for looping prompts)."""

    answers: dict = {}

    @classmethod
    def ask(cls, label, default=""):
        value = cls.answers.get(label)
        if isinstance(value, list):
            return value.pop(0) if value else default
        return value if value is not None else default


class _StubConfirm:
    answer = True

    @classmethod
    def ask(cls, label, default=True):
        return cls.answer


def _patched_review(monkeypatch, manifest, confirm, answers, skip_review=False):
    from mdb import cli as mdb_cli
    _StubConfirm.answer = confirm
    _StubPrompt.answers = answers
    monkeypatch.setattr(mdb_cli, "Confirm", _StubConfirm)
    monkeypatch.setattr(mdb_cli, "Prompt", _StubPrompt)
    mdb_cli._review_catalog_values(manifest, skip_review=skip_review)


def test_review_accept_keeps_everything(monkeypatch):
    manifest = _openrouter_manifest(1e-07, 2e-07)
    _patched_review(monkeypatch, manifest, confirm=True, answers={})
    assert manifest.options["temperature"].default == 1
    assert manifest.metadata.pricing.input_token_price == 1e-07


def test_review_adjust_edits_options_metadata_and_pricing(monkeypatch):
    manifest = _openrouter_manifest(1e-07, 2e-07)
    _patched_review(monkeypatch, manifest, confirm=False, answers={
        "options.temperature.default": "0.5",
        "metadata.context_length": "32768",
        "pricing.output_token_price": "6e-07",
    })
    assert manifest.options["temperature"].default == 0.5
    assert manifest.metadata.context_length == 32768
    # untouched prompts keep their defaults
    assert manifest.options["top_p"].default == 1
    assert manifest.metadata.pricing.input_token_price == 1e-07
    assert manifest.metadata.pricing.output_token_price == 6e-07


def test_review_renames_and_drops_options(monkeypatch):
    """The provider contract sometimes wants a different option NAME (newer
    OpenAI-style models take max_completion_tokens, not max_tokens) - the
    review can fix that without editing definition.yaml."""
    manifest = _openrouter_manifest(1e-07, 2e-07)
    _patched_review(monkeypatch, manifest, confirm=False, answers={
        "Rename or drop an option (old=new renames, -name drops, Enter continues)":
            ["max_tokens=max_completion_tokens", "-top_p", ""],
    })
    assert "max_completion_tokens" in manifest.options
    assert "max_tokens" not in manifest.options
    assert "top_p" not in manifest.options
    # the renamed option keeps its spec
    assert manifest.options["max_completion_tokens"].max == 16384


def test_default_options_follow_supported_parameters():
    from mdb.providers.base import CatalogModel
    from mdb.providers.openai_compat import OpenAICompatAdapter
    adapter = OpenAICompatAdapter(
        id="openai", display_name="OpenAI", provider_tag="OpenAI", key_name="OpenAI",
        env_key_var="OPENAI_API_KEY", base_url="https://api.openai.com/v1",
    )
    modern = CatalogModel(
        ref="gpt-modern", display_name="GPT Modern", max_output_tokens=8192,
        supported_parameters=["temperature", "top_p", "max_completion_tokens"],
    )
    options = adapter.default_options(modern)
    assert "max_completion_tokens" in options and "max_tokens" not in options
    assert options["max_completion_tokens"].max == 8192.0
    classic = CatalogModel(
        ref="gpt-classic", display_name="GPT Classic", max_output_tokens=4096,
        supported_parameters=["temperature", "top_p", "max_tokens"],
    )
    assert "max_tokens" in adapter.default_options(classic)
    # no catalog knowledge -> the traditional default stays
    unknown = CatalogModel(ref="gpt-unknown", display_name="GPT Unknown")
    assert "max_tokens" in adapter.default_options(unknown)


def test_review_accept_still_asks_when_pricing_unknown(monkeypatch):
    manifest = _openrouter_manifest(None, None)
    _patched_review(monkeypatch, manifest, confirm=True, answers={
        "pricing.input_token_price": "0",
        "pricing.output_token_price": "0",
    })
    assert manifest.metadata.pricing.input_token_price == 0.0
    assert manifest.metadata.pricing.output_token_price == 0.0


def test_review_skipped_leaves_unknown_pricing(monkeypatch):
    manifest = _openrouter_manifest(None, None)
    _patched_review(monkeypatch, manifest, confirm=True, answers={}, skip_review=True)
    assert manifest.metadata.pricing.input_token_price is None


def test_http_smoke_failure_classifies_rate_limits():
    limited = http_smoke_failure(SimpleNamespace(status_code=429), {"error": "slow down"})
    assert limited.ok is False and limited.inconclusive is True
    assert "429" in limited.detail and "retry" in limited.detail
    hard = http_smoke_failure(SimpleNamespace(status_code=500), {"error": "boom"})
    assert hard.ok is False and hard.inconclusive is False
