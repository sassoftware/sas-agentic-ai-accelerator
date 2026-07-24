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


def test_http_smoke_failure_classifies_rate_limits():
    limited = http_smoke_failure(SimpleNamespace(status_code=429), {"error": "slow down"})
    assert limited.ok is False and limited.inconclusive is True
    assert "429" in limited.detail and "retry" in limited.detail
    hard = http_smoke_failure(SimpleNamespace(status_code=500), {"error": "boom"})
    assert hard.ok is False and hard.inconclusive is False
