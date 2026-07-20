# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Custom (non-vocabulary) options: allowed with inline typing, passed through
to the provider as-is, surfaced to UIs with the author's own name/description,
and flagged by a V010 warning so the author knows what they gave up."""
import json

import pytest

from mdb.core import drift
from mdb.core.generator import GenerationError, list_custom_options, render_assets
from mdb.core.manifest import (
    AuthBlock, MetadataBlock, ModelManifest, OptionSpec, PricingBlock,
    ProviderBlock, RuntimeBlock, TagsBlock,
)
from mdb.core.validator import validate_folder


def _manifest_with(options) -> ModelManifest:
    return ModelManifest(
        model_id="test_custom_1",
        display_name="Test Custom 1",
        provider=ProviderBlock(
            adapter="openai", model_version="gpt-test",
            endpoint="https://api.openai.com/v1/chat/completions",
            auth=AuthBlock(mode="api_key", key_name="OpenAI"),
        ),
        runtime=RuntimeBlock(template="openai_chat", requirements_profile="api-wrapper"),
        options=options,
        tags=TagsBlock(size_class="LLM", license_class="Proprietary",
                       provider_tag="OpenAI", scr_sizing="small"),
        metadata=MetadataBlock(
            description="Custom option test.",
            pricing=PricingBlock(cost_type="Tokens", input_token_price=1e-7, output_token_price=6e-7),
        ),
        modeler="tester",
    )


def test_custom_option_passes_through_with_author_metadata(core):
    manifest = _manifest_with({
        "temperature": OptionSpec(default=1),
        "verbosity": OptionSpec(type="enum", default="medium", values=["low", "medium", "high"],
                                description="How verbose the answer should be."),
    })
    rendered = render_assets(manifest, core)
    score = rendered["testCustom1Score.py"].decode()
    # sent to the provider as-is under its own name
    assert '"verbosity": str(options["verbosity"]),' in score
    options = json.loads(rendered["options.json"])
    # UIs see the author's own name/description; no standardized label
    assert options["verbosity"]["description"] == "How verbose the answer should be."
    assert options["verbosity"]["type"] == "enum"
    assert "label" not in options["verbosity"]
    assert list_custom_options(manifest, core) == ["verbosity"]


def test_custom_informational_option_stays_out_of_the_scorer(core):
    manifest = _manifest_with({
        "temperature": OptionSpec(default=1),
        "Internal_Note": OptionSpec(type="string", default="run this only in EU regions",
                                    description="Deployment note.", informational=True),
    })
    rendered = render_assets(manifest, core)
    score = rendered["testCustom1Score.py"].decode()
    assert "Internal_Note" not in score
    assert "Internal_Note" in json.loads(rendered["options.json"])


def test_untyped_unknown_option_still_fails_loudly(core):
    manifest = _manifest_with({"totally_unknown": OptionSpec(default=1)})
    with pytest.raises(GenerationError, match="not in the option vocabulary"):
        render_assets(manifest, core)


def test_validate_warns_v010_for_custom_options(core, tmp_path, fact_sheet):
    manifest = _manifest_with({
        "temperature": OptionSpec(default=1),
        "verbosity": OptionSpec(type="enum", default="medium", values=["low", "medium", "high"],
                                description="How verbose the answer should be."),
    })
    folder = tmp_path / "test_custom_1"
    folder.mkdir()
    manifest.save(folder)
    rendered = render_assets(manifest, core)
    for name, content in rendered.items():
        (folder / name).write_bytes(content)
    drift.write_lock(folder, (folder / "definition.yaml").read_bytes(), rendered)
    issues = validate_folder(folder, core, fact_sheet)
    v010 = [i for i in issues if i.rule == "V010"]
    assert len(v010) == 1
    assert v010[0].severity == "warning"
    assert "verbosity" in v010[0].message
    assert "no cross-provider value translation" in v010[0].message
