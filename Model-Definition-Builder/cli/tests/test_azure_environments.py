# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Azure multi-environment handling: definitions are environment-neutral by
default; the resource resolves per call via option > AZURE_OPENAI_RESOURCE
container env var > baked default."""
import json

from mdb.core.generator import render_assets
from mdb.core.manifest import (
    AuthBlock, MetadataBlock, ModelManifest, OptionSpec, PricingBlock,
    ProviderBlock, RuntimeBlock, TagsBlock,
)
from mdb.core.validator import validate_folder


def _azure_manifest(commit_resource: bool) -> ModelManifest:
    return ModelManifest(
        model_id="gpt_test_az",
        display_name="GPT Test (Azure)",
        provider=ProviderBlock(
            adapter="azure-foundry",
            model_version="my-gpt-deployment",
            params={"resource": "myres.openai.azure.com", "commit_resource": commit_resource},
            auth=AuthBlock(mode="api_key", key_name="AzureOpenAI"),
        ),
        runtime=RuntimeBlock(template="azure_openai_v1", requirements_profile="api-wrapper"),
        options={
            "temperature": OptionSpec(default=1),
            "top_p": OptionSpec(default=1),
            "max_tokens": OptionSpec(default=1000, max=16384),
        },
        tags=TagsBlock(size_class="LLM", license_class="Proprietary",
                       provider_tag="Azure OpenAI", scr_sizing="small"),
        metadata=MetadataBlock(
            description="Azure test model.",
            pricing=PricingBlock(cost_type="Tokens", input_token_price=1e-7, output_token_price=6e-7),
        ),
        modeler="tester",
    )


def test_environment_neutral_by_default(core):
    rendered = render_assets(_azure_manifest(commit_resource=False), core)
    score = rendered["gptTestAzScore.py"].decode()
    # env-var fallback present, no host baked in
    assert 'os.environ.get("AZURE_OPENAI_RESOURCE", "")' in score
    assert "myres.openai.azure.com" not in score
    options = json.loads(rendered["options.json"])
    assert options["azure_openai_resource"]["default"] == ""
    assert "AZURE_OPENAI_RESOURCE" in options["azure_openai_resource"]["description"]


def test_committed_resource_is_baked_and_flagged(core, tmp_path, fact_sheet):
    manifest = _azure_manifest(commit_resource=True)
    rendered = render_assets(manifest, core)
    score = rendered["gptTestAzScore.py"].decode()
    assert 'os.environ.get("AZURE_OPENAI_RESOURCE", "myres.openai.azure.com")' in score
    options = json.loads(rendered["options.json"])
    assert options["azure_openai_resource"]["default"] == "myres.openai.azure.com"

    # validate reminds about the environment binding (warning, not error)
    folder = tmp_path / "gpt_test_az"
    folder.mkdir()
    manifest.save(folder)
    for name, content in rendered.items():
        (folder / name).write_bytes(content)
    from mdb.core import drift
    drift.write_lock(folder, (folder / "definition.yaml").read_bytes(), rendered)
    issues = validate_folder(folder, core, fact_sheet)
    v009 = [i for i in issues if i.rule == "V009"]
    assert len(v009) == 1 and v009[0].severity == "warning"
    assert not [i for i in issues if i.severity == "error" and i.rule != "V006"]
