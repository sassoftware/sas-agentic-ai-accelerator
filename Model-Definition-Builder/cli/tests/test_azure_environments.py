# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Azure multi-environment handling: definitions are environment-neutral by
default; the resource resolves per call via option > AZURE_OPENAI_RESOURCE
container env var > baked default. Every Azure host flavor is accepted
(openai / cognitiveservices / services.ai), and api_version switches from the
GA v1 endpoint to the legacy deployment-scoped route."""
import json

from mdb.core.generator import render_assets
from mdb.core.manifest import (
    AuthBlock, MetadataBlock, ModelManifest, OptionSpec, PricingBlock,
    ProviderBlock, RuntimeBlock, TagsBlock,
)
from mdb.core.validator import validate_folder
from mdb.providers.openai_compat import AzureFoundryAdapter


def _azure_manifest(commit_resource: bool, resource: str = "myres.openai.azure.com",
                    api_version: str = "") -> ModelManifest:
    params = {"resource": resource, "commit_resource": commit_resource}
    if api_version:
        params["api_version"] = api_version
    return ModelManifest(
        model_id="gpt_test_az",
        display_name="GPT Test (Azure)",
        provider=ProviderBlock(
            adapter="azure-foundry",
            model_version="my-gpt-deployment",
            params=params,
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


def _azure_embedding_manifest(commit_resource: bool, resource: str = "myres.openai.azure.com",
                              api_version: str = "") -> ModelManifest:
    params = {"resource": resource, "commit_resource": commit_resource}
    if api_version:
        params["api_version"] = api_version
    return ModelManifest(
        kind="embedding",
        model_id="emb_test_az",
        display_name="Embedding Test (Azure)",
        provider=ProviderBlock(
            adapter="azure-foundry",
            model_version="my-embedding-deployment",
            params=params,
            auth=AuthBlock(mode="api_key", key_name="AzureOpenAI"),
        ),
        runtime=RuntimeBlock(template="emb_azure_openai_v1", requirements_profile="api-wrapper"),
        options={
            "Embedding_Length": OptionSpec(default=1024),
            "Input_Token_Limit": OptionSpec(default=8192),
        },
        tags=TagsBlock(size_class="Embedding", license_class="Proprietary",
                       provider_tag="Azure OpenAI", scr_sizing="small"),
        metadata=MetadataBlock(
            description="Azure embedding test model.",
            pricing=PricingBlock(cost_type="Tokens", input_token_price=1e-7, output_token_price=None),
        ),
        modeler="tester",
    )


def test_embedding_endpoint_is_built_dynamically_not_baked_bare_path(core):
    """Regression test: azure-foundry embedding definitions used to render
    modelEndpoint = '/embeddings' (no host at all) because the adapter had no
    embedding-specific endpoint/template - the score script could never reach
    a real server. The embedding template must build the same
    resource/deployment/api_version-derived host as the LLM template."""
    rendered = render_assets(_azure_embedding_manifest(commit_resource=False), core)
    score = rendered["embTestAzScore.py"].decode()
    assert "modelEndpoint = '" not in score  # no static endpoint literal baked in
    assert 'os.environ.get("AZURE_OPENAI_RESOURCE", "")' in score
    assert "/openai/v1/embeddings" in score
    # Azure uses the api-key header, not OpenAI-style Bearer auth
    assert '"api-key": options["API_KEY"]' in score
    assert "Bearer" not in score
    options = json.loads(rendered["options.json"])
    assert options["azure_openai_resource"]["default"] == ""
    assert options["azure_api_version"]["default"] == ""


def test_embedding_api_version_bakes_the_legacy_deployment_route(core):
    manifest = _azure_embedding_manifest(
        commit_resource=True,
        resource="contoso-foundry.cognitiveservices.azure.com",
        api_version="2025-01-01-preview",
    )
    rendered = render_assets(manifest, core)
    score = rendered["embTestAzScore.py"].decode()
    assert 'os.environ.get("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")' in score
    assert "/openai/deployments/{deploymentName}/embeddings?api-version=" in score
    assert 'os.environ.get("AZURE_OPENAI_RESOURCE", "contoso-foundry.cognitiveservices.azure.com")' in score


def test_embedding_committed_resource_is_flagged(core, tmp_path, fact_sheet):
    manifest = _azure_embedding_manifest(commit_resource=True)
    rendered = render_assets(manifest, core)
    folder = tmp_path / "emb_test_az"
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


def test_environment_neutral_by_default(core):
    rendered = render_assets(_azure_manifest(commit_resource=False), core)
    score = rendered["gptTestAzScore.py"].decode()
    # env-var fallback present, no host baked in
    assert 'os.environ.get("AZURE_OPENAI_RESOURCE", "")' in score
    assert "myres.openai.azure.com" not in score
    # default API style: the GA v1 endpoint, api-version empty but runtime-overridable
    assert 'os.environ.get("AZURE_OPENAI_API_VERSION", "")' in score
    assert "/openai/v1/chat/completions" in score
    options = json.loads(rendered["options.json"])
    assert options["azure_openai_resource"]["default"] == ""
    assert "AZURE_OPENAI_RESOURCE" in options["azure_openai_resource"]["description"]
    assert options["azure_api_version"]["default"] == ""
    assert "AZURE_OPENAI_API_VERSION" in options["azure_api_version"]["description"]


def test_api_version_bakes_the_legacy_deployment_route(core):
    """A cognitiveservices host + api_version (the shape Azure AI Foundry
    portals hand out for some resources) renders the deployment-scoped URL."""
    manifest = _azure_manifest(
        commit_resource=True,
        resource="contoso-foundry.cognitiveservices.azure.com",
        api_version="2025-01-01-preview",
    )
    rendered = render_assets(manifest, core)
    score = rendered["gptTestAzScore.py"].decode()
    assert 'os.environ.get("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")' in score
    assert "/openai/deployments/{deploymentName}" in score
    # full hosts of any flavor are preserved verbatim
    assert 'os.environ.get("AZURE_OPENAI_RESOURCE", "contoso-foundry.cognitiveservices.azure.com")' in score
    options = json.loads(rendered["options.json"])
    assert options["azure_api_version"]["default"] == "2025-01-01-preview"


def test_adapter_chat_url_styles():
    adapter = AzureFoundryAdapter()
    v1 = _azure_manifest(commit_resource=True)
    assert adapter._chat_url(v1) == "https://myres.openai.azure.com/openai/v1/chat/completions"
    legacy = _azure_manifest(
        commit_resource=True,
        resource="myres.cognitiveservices.azure.com",
        api_version="2024-10-21",
    )
    assert adapter._chat_url(legacy) == (
        "https://myres.cognitiveservices.azure.com/openai/deployments/"
        "my-gpt-deployment/chat/completions?api-version=2024-10-21"
    )
    # bare resource names still expand to the classic Azure OpenAI host
    bare = _azure_manifest(commit_resource=True, resource="myres")
    assert adapter._chat_url(bare) == "https://myres.openai.azure.com/openai/v1/chat/completions"


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
