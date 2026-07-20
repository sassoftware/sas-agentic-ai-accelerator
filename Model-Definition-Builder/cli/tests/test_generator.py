# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generator contract tests: determinism, fleet fidelity, load-bearing quirks."""
import json

import pytest

from mdb.core.generator import GenerationError, render_assets, score_file_name
from mdb.core.importer import import_folder
from mdb.core.manifest import (
    AuthBlock, MetadataBlock, ModelManifest, OptionSpec, PricingBlock,
    ProviderBlock, RuntimeBlock, TagsBlock,
)


def _manifest(**overrides) -> ModelManifest:
    base = dict(
        model_id="test_model_1",
        display_name="Test Model 1",
        provider=ProviderBlock(
            adapter="openai",
            model_version="gpt-test-1",
            endpoint="https://api.openai.com/v1/chat/completions",
            auth=AuthBlock(mode="api_key", key_name="OpenAI"),
        ),
        runtime=RuntimeBlock(template="openai_chat", requirements_profile="api-wrapper"),
        options={
            "temperature": OptionSpec(default=1),
            "top_p": OptionSpec(default=1),
            "max_tokens": OptionSpec(default=1000, max=16384),
        },
        tags=TagsBlock(size_class="LLM", license_class="Proprietary",
                       provider_tag="OpenAI", scr_sizing="small"),
        metadata=MetadataBlock(
            description="A test model.",
            context_length=128000,
            pricing=PricingBlock(cost_type="Tokens", input_token_price=1e-7, output_token_price=6e-7),
        ),
        modeler="tester",
    )
    base.update(overrides)
    return ModelManifest(**base)


def test_score_file_name_matches_fleet_convention():
    assert score_file_name("claude_sonnet_3_5") == "claudeSonnet35Score.py"
    assert score_file_name("qwen_25_05b") == "qwen2505bScore.py"
    assert score_file_name("gpt_4o_mini_2024_07_18") == "gpt4oMini20240718Score.py"


def test_generation_is_deterministic_and_lf_only(core):
    manifest = _manifest()
    first = render_assets(manifest, core)
    second = render_assets(manifest, core)
    assert first == second
    for name, content in first.items():
        assert b"\r" not in content, f"{name} contains CR - generated files must be LF-only"


def test_expected_asset_set(core):
    rendered = render_assets(_manifest(), core)
    assert set(rendered) == {
        "testModel1Score.py", "inputVar.json", "outputVar.json",
        "modelConfiguration.json", "options.json", "requirements.json",
        "README.md", "Model-Card.md",
    }


def test_load_bearing_conventions(core):
    rendered = render_assets(_manifest(), core)
    config = json.loads(rendered["modelConfiguration.json"])
    # name == model_id == fact-sheet key is the universal join invariant
    assert config["name"] == "test_model_1"
    assert config["scoreCodeFile"] == "testModel1Score.py"
    assert config["tags"] == ["LLM", "Proprietary", "OpenAI", "small"]
    assert config["function"] == "text generation"
    # options.json API_KEY default must equal the LLM_API_KEYS KeyName
    options = json.loads(rendered["options.json"])
    assert options["API_KEY"]["default"] == "OpenAI"
    assert options["API_KEY"]["range"] == "sk-****"
    # the canonical parser and the four-output contract are present
    score = rendered["testModel1Score.py"].decode()
    assert "_parse_options" in score
    assert '"Output: response, run_time, prompt_length, output_length"' in score
    # the central inputVar typo fix
    assert b"the the" not in rendered["inputVar.json"]


def test_reasoning_model_gets_no_temperature(core):
    manifest = _manifest(options={
        "reasoning_effort": OptionSpec(default="medium"),
        "max_completion_tokens": OptionSpec(default=4000, max=128000),
    })
    rendered = render_assets(manifest, core)
    score = rendered["testModel1Score.py"].decode()
    assert "reasoning_effort" in score
    assert "max_completion_tokens" in score
    assert '"temperature"' not in score
    # the normalized 5-level scale translates to the provider's own values
    assert '"maximum": "high"' in score
    assert '.get(str(options["reasoning_effort"]), "medium")' in score
    options = json.loads(rendered["options.json"])
    assert options["reasoning_effort"]["values"] == ["minimal", "low", "medium", "high", "maximum"]
    assert options["reasoning_effort"]["label"] == "Reasoning Effort"
    assert options["max_completion_tokens"]["label"] == "Max Tokens"


def test_unsupported_option_fails_loudly(core):
    manifest = _manifest(options={"thinking_budget": OptionSpec(default=0)})
    with pytest.raises(Exception, match="not supported by score template family"):
        render_assets(manifest, core)


def test_anthropic_thinking_block(core):
    manifest = _manifest(
        provider=ProviderBlock(
            adapter="anthropic", model_version="claude-test",
            endpoint="https://api.anthropic.com/v1/messages",
            params={"anthropic_version": "2023-06-01"},
            auth=AuthBlock(mode="api_key", key_name="Anthropic"),
        ),
        runtime=RuntimeBlock(template="anthropic_messages", requirements_profile="api-wrapper"),
        options={
            "temperature": OptionSpec(default=1),
            "max_tokens": OptionSpec(default=1000, max=64000),
            "thinking_budget": OptionSpec(default=0),
        },
    )
    score = render_assets(manifest, core)["testModel1Score.py"].decode()
    assert 'payload["thinking"]' in score
    assert "x-api-key" in score
    # extended thinking pins temperature and forbids top_p outright
    assert 'payload["temperature"] = 1' in score
    assert 'payload.pop("top_p", None)' in score


def _anthropic_manifest(**options):
    return _manifest(
        provider=ProviderBlock(
            adapter="anthropic", model_version="claude-test",
            endpoint="https://api.anthropic.com/v1/messages",
            params={"anthropic_version": "2023-06-01"},
            auth=AuthBlock(mode="api_key", key_name="Anthropic"),
        ),
        runtime=RuntimeBlock(template="anthropic_messages", requirements_profile="api-wrapper"),
        options=options,
    )


def test_anthropic_never_sends_temperature_and_top_p_together(core):
    """Claude 4.5+ rejects a request carrying both ("please use only one"), so the
    payload literal must not contain either - a runtime picker chooses one."""
    manifest = _anthropic_manifest(
        temperature=OptionSpec(default=1), top_p=OptionSpec(default=1),
        max_tokens=OptionSpec(default=1000, max=64000),
    )
    score = render_assets(manifest, core)["testModel1Score.py"].decode()
    # up to the dict's closing brace - not the one inside the messages list
    payload_literal = score.split("payload = {", 1)[1].split("\n    }", 1)[0]
    assert '"temperature"' not in payload_literal and '"top_p"' not in payload_literal
    assert '"max_tokens"' in payload_literal  # unrelated options still render inline
    assert 'if "top_p" in requested and "temperature" not in requested:' in score
    assert 'payload["top_p"] = float(options["top_p"])' in score
    assert 'payload["temperature"] = float(options["temperature"])' in score


def test_anthropic_lone_sampling_option_renders_unconditionally(core):
    """Only one member of the group present: no branch needed, just assign it."""
    manifest = _anthropic_manifest(
        top_p=OptionSpec(default=1), max_tokens=OptionSpec(default=1000, max=64000),
    )
    score = render_assets(manifest, core)["testModel1Score.py"].decode()
    assert 'payload["top_p"] = float(options["top_p"])' in score
    assert "in requested" not in score
    assert "temperature" not in score


def _hf_manifest(gated: bool = False, weights_source: str = "baked"):
    return _manifest(
        provider=ProviderBlock(
            adapter="hf-selfhosted", model_version="Qwen/Qwen2.5-0.5B-Instruct",
            params={"hf": {"repo": "Qwen/Qwen2.5-0.5B-Instruct", "gated": gated}},
            auth=AuthBlock(mode="none"),
        ),
        runtime=RuntimeBlock(
            template="hf_transformers", requirements_profile="hf-transformers",
            weights_source=weights_source,
        ),
        options={
            "temperature": OptionSpec(default=0.7),
            "top_p": OptionSpec(default=0.8),
            "max_tokens": OptionSpec(default=512, max=8192),
        },
    )


def test_hf_requirements_profile(core):
    """Default 'baked': weights are downloaded into the image at build time."""
    rendered = render_assets(_hf_manifest(), core)
    steps = json.loads(rendered["requirements.json"])
    commands = [s["command"] for s in steps]
    # hf download uses the huggingface_hub HTTP API - no git-lfs step needed
    assert not any("git-lfs" in c or "git lfs" in c for c in commands)
    # CPU-only torch keeps the SCR image lean (no bundled CUDA)
    assert any("--extra-index-url https://download.pytorch.org/whl/cpu torch" in c for c in commands)
    assert any("huggingface-hub>=0.18.0" in c for c in commands)
    assert commands[-1] == "hf download --quiet Qwen/Qwen2.5-0.5B-Instruct --local-dir /pybox/model/test_model_1"
    score = rendered["testModel1Score.py"].decode()
    assert "checkpoint = './test_model_1'" in score
    assert "max_new_tokens=int(options['max_tokens'])" in score


def test_hf_mounted_weights_skip_the_build_time_download(core):
    """'mounted': nothing is downloaded during the build; the scorer reads the
    shared llm-weights volume, so the image stays small and one staged copy of
    the weights serves every container and replica."""
    rendered = render_assets(_hf_manifest(weights_source="mounted"), core)
    commands = [s["command"] for s in json.loads(rendered["requirements.json"])]
    assert not any("hf download" in c for c in commands)
    # The pip steps still run - only the weight download moves out of the build.
    assert any("huggingface-hub>=0.18.0" in c for c in commands)
    score = rendered["testModel1Score.py"].decode()
    assert "checkpoint = '/pybox/model/mount/test_model_1'" in score


def test_hf_mounted_weights_work_for_gated_repositories(core):
    """A gated repository cannot authenticate during the build, so it must be
    staged on the shared volume - that path generates cleanly."""
    rendered = render_assets(_hf_manifest(gated=True, weights_source="mounted"), core)
    commands = [s["command"] for s in json.loads(rendered["requirements.json"])]
    assert not any("hf download" in c or "hf login" in c for c in commands)
    assert "checkpoint = '/pybox/model/mount/test_model_1'" in rendered["testModel1Score.py"].decode()


def test_hf_gated_baked_is_rejected_with_guidance(core):
    """Baking a gated repository into the image cannot work - fail loudly rather
    than emit a definition whose build dies at the download step."""
    with pytest.raises(GenerationError, match="weights_source: mounted"):
        render_assets(_hf_manifest(gated=True, weights_source="baked"), core)


def test_fleet_fidelity_gpt4o_mini_options_json(core, repo_root, fact_sheet):
    """The regenerated options.json of a legacy OpenAI folder is byte-identical."""
    folder = repo_root / "LLM-Definitions" / "gpt_4o_mini_2024_07_18"
    result = import_folder(folder, fact_sheet)
    rendered = render_assets(result.manifest, core)
    legacy = (folder / "options.json").read_bytes().replace(b"\r\n", b"\n")
    assert rendered["options.json"] == legacy
