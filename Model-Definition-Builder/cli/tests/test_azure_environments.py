# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Azure multi-environment handling.

Where an Azure container sends its requests is a property of the deployment,
not of the caller: the resource, the API style and an optional full endpoint
resolve as container environment variable > baked default and are NEVER
scoring options. Every Azure host flavor is accepted (openai /
cognitiveservices / services.ai), api_version switches from the GA v1 endpoint
to the legacy deployment-scoped route, and chat and embedding deployments share
one endpoint builder."""
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from mdb.core.generator import (
    AZURE_TEMPLATES, GenerationError, effective_score_file, render_assets,
)
from mdb.core.importer import _detect_family, import_folder
from mdb.core.manifest import (
    AuthBlock, GenerationBlock, MetadataBlock, ModelManifest, OptionSpec, PricingBlock,
    ProviderBlock, RuntimeBlock, TagsBlock,
)
from mdb.core.validator import validate_folder
from mdb.providers.base import CatalogModel
from mdb.providers.openai_compat import AzureFoundryAdapter, AzureFoundryEnvAdapter

# Everything that used to be an option and is now read from the container only.
CONNECTION_OPTIONS = ("azure_openai_resource", "azure_api_version", "api_version",
                      "endpoint_url", "azure_deployment")
AZURE_ENV_VARS = ("AZURE_OPENAI_RESOURCE", "AZURE_OPENAI_API_VERSION", "AZURE_OPENAI_ENDPOINT",
                  "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT")


def _azure_manifest(commit_resource: bool = False, resource: str = "myres.openai.azure.com",
                    api_version: str = "", kind: str = "llm",
                    options: dict | None = None) -> ModelManifest:
    params = {"resource": resource, "commit_resource": commit_resource}
    if api_version:
        params["api_version"] = api_version
    embedding = kind == "embedding"
    if options is None:
        options = ({"Embedding_Length": OptionSpec(default=1536)} if embedding else {
            "temperature": OptionSpec(default=1),
            "top_p": OptionSpec(default=1),
            "max_tokens": OptionSpec(default=1000, max=16384),
        })
    return ModelManifest(
        kind=kind,
        model_id="emb_test_az" if embedding else "gpt_test_az",
        display_name="Test (Azure)",
        provider=ProviderBlock(
            adapter="azure-foundry",
            model_version="my-deployment",
            params=params,
            auth=AuthBlock(mode="api_key", key_name="AzureOpenAI"),
        ),
        runtime=RuntimeBlock(template="emb_azure_openai_v1" if embedding else "azure_openai_v1",
                             requirements_profile="api-wrapper"),
        options=options,
        tags=TagsBlock(size_class="Embedding" if embedding else "LLM", license_class="Proprietary",
                       provider_tag="Azure OpenAI", scr_sizing="small"),
        metadata=MetadataBlock(
            description="Azure test model.",
            pricing=PricingBlock(cost_type="Tokens", input_token_price=1e-7, output_token_price=6e-7),
        ),
        modeler="tester",
    )


def _azure_env_manifest(resource: str = "", api_version: str = "") -> ModelManifest:
    return ModelManifest(
        model_id="gpt_test_az_env",
        display_name="Azure Test (environment-configured)",
        provider=ProviderBlock(
            adapter="azure-foundry-env",
            model_version="my-deployment",
            params={"resource": resource, "commit_resource": False, "api_version": api_version},
            auth=AuthBlock(mode="none"),
        ),
        runtime=RuntimeBlock(template="azure_openai_env", requirements_profile="api-wrapper"),
        options={
            "reasoning_effort": OptionSpec(default="medium"),
            "max_completion_tokens": OptionSpec(default=4000, max=128000),
        },
        tags=TagsBlock(size_class="LLM", license_class="Proprietary",
                       provider_tag="Azure OpenAI", scr_sizing="small"),
        metadata=MetadataBlock(
            description="Environment-configured Azure test model.",
            pricing=PricingBlock(cost_type="Tokens", input_token_price=2e-7, output_token_price=1.2e-6),
        ),
        modeler="tester",
    )


def _score_module(manifest: ModelManifest, core, tmp_path: Path):
    """Exec the rendered scorer the way SCR does, so resolution is tested as behavior."""
    rendered = render_assets(manifest, core)
    name = effective_score_file(manifest)
    path = tmp_path / f"{manifest.model_id}_{name}"
    path.write_bytes(rendered[name])
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module, rendered


class _FakeResponse:
    status_code = 200

    def __init__(self, kind: str):
        self._body = ({"data": [{"embedding": [0.1, 0.2, 0.3]}], "usage": {"prompt_tokens": 3}}
                      if kind == "embedding" else
                      {"choices": [{"message": {"content": "OK"}}],
                       "usage": {"prompt_tokens": 5, "completion_tokens": 1}})

    def json(self):
        return self._body


def _capture_post(module, monkeypatch, kind: str) -> dict:
    """Replace requests.post in an exec'd scorer; returns the dict the call fills in."""
    seen: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, body=json)
        return _FakeResponse(kind)

    monkeypatch.setattr(module.requests, "post", fake_post)
    return seen


def _clear_azure_env(monkeypatch):
    for var in AZURE_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# -- connection config is never an option ------------------------------------

@pytest.mark.parametrize("manifest", [
    _azure_manifest(), _azure_manifest(kind="embedding"), _azure_env_manifest(),
], ids=list(AZURE_TEMPLATES))
def test_connection_config_is_read_from_the_container_not_from_options(manifest, core):
    rendered = render_assets(manifest, core)
    options = json.loads(rendered["options.json"])
    for name in CONNECTION_OPTIONS:
        assert name not in options, f"{name} leaked into options.json"
    score = rendered[effective_score_file(manifest)].decode()
    for var in ("AZURE_OPENAI_RESOURCE", "AZURE_OPENAI_API_VERSION", "AZURE_OPENAI_ENDPOINT"):
        assert var in score, var
    # ...and the scorer does not read any of them from the options either.
    for name in CONNECTION_OPTIONS:
        assert f"'{name}'" not in score and f'"{name}"' not in score, name
    # No static endpoint literal of any kind - the URL is built per call.
    assert "modelEndpoint = '" not in score
    assert "_azure_endpoint(deploymentName, " in score


def test_environment_neutral_by_default(core):
    score = render_assets(_azure_manifest(commit_resource=False), core)["gptTestAzScore.py"].decode()
    assert "defaultResource = ''" in score
    assert "defaultApiVersion = ''" in score
    assert "myres.openai.azure.com" not in score


def test_api_version_bakes_the_legacy_deployment_route(core):
    """A cognitiveservices host + api_version (the shape Azure AI Foundry
    portals hand out for some resources) is kept as the baked default."""
    manifest = _azure_manifest(commit_resource=True,
                               resource="contoso-foundry.cognitiveservices.azure.com",
                               api_version="2025-01-01-preview")
    score = render_assets(manifest, core)["gptTestAzScore.py"].decode()
    assert "defaultApiVersion = '2025-01-01-preview'" in score
    assert "defaultResource = 'contoso-foundry.cognitiveservices.azure.com'" in score
    assert "/openai/deployments/{deploymentName}/{route}?api-version=" in score


def test_committed_resource_is_baked_and_flagged(core, tmp_path, fact_sheet):
    manifest = _azure_manifest(commit_resource=True)
    rendered = render_assets(manifest, core)
    assert "defaultResource = 'myres.openai.azure.com'" in rendered["gptTestAzScore.py"].decode()

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
    assert "per-call option" not in v009[0].hint
    assert not [i for i in issues if i.severity == "error" and i.rule != "V006"]


# -- resolution, tested as behavior of the rendered scorer ---------------------

def test_endpoint_resolves_env_over_baked_and_names_the_missing_variable(core, tmp_path, monkeypatch):
    _clear_azure_env(monkeypatch)
    neutral, _ = _score_module(_azure_manifest(commit_resource=False), core, tmp_path)
    with pytest.raises(RuntimeError, match="AZURE_OPENAI_RESOURCE"):
        neutral._azure_endpoint("dep", "chat/completions")

    # A bare resource name expands to the classic host; the GA v1 route is the default.
    monkeypatch.setenv("AZURE_OPENAI_RESOURCE", "contoso")
    assert neutral._azure_endpoint("dep", "chat/completions") == \
        "https://contoso.openai.azure.com/openai/v1/chat/completions"
    assert neutral._azure_endpoint("dep", "embeddings") == \
        "https://contoso.openai.azure.com/openai/v1/embeddings"

    # Any full host is used verbatim; an API version selects the legacy route.
    monkeypatch.setenv("AZURE_OPENAI_RESOURCE", "contoso.services.ai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
    assert neutral._azure_endpoint("dep", "embeddings") == \
        "https://contoso.services.ai.azure.com/openai/deployments/dep/embeddings?api-version=2024-10-21"

    # A full endpoint replaces everything else (gateways).
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://gw.example.com/openai/v1/chat/completions")
    assert neutral._azure_endpoint("dep", "chat/completions") == \
        "https://gw.example.com/openai/v1/chat/completions"

    # A committed resource is only the fallback: the environment still wins, a
    # blank variable does not shadow the baked default.
    _clear_azure_env(monkeypatch)
    committed, _ = _score_module(_azure_manifest(commit_resource=True, resource="baked"), core, tmp_path)
    assert committed._azure_endpoint("dep", "chat/completions") == \
        "https://baked.openai.azure.com/openai/v1/chat/completions"
    monkeypatch.setenv("AZURE_OPENAI_RESOURCE", "  ")
    assert "baked.openai.azure.com" in committed._azure_endpoint("dep", "chat/completions")
    monkeypatch.setenv("AZURE_OPENAI_RESOURCE", "elsewhere.cognitiveservices.azure.com")
    assert committed._azure_endpoint("dep", "chat/completions") == \
        "https://elsewhere.cognitiveservices.azure.com/openai/v1/chat/completions"


def test_chat_scorer_posts_with_the_api_key_header_to_the_resolved_host(core, tmp_path, monkeypatch):
    _clear_azure_env(monkeypatch)
    monkeypatch.setenv("AZURE_OPENAI_RESOURCE", "contoso")
    module, _ = _score_module(_azure_manifest(), core, tmp_path)
    seen = _capture_post(module, monkeypatch, "llm")
    # A caller-supplied connection option is ignored: it is not an option any more.
    response, _, prompt_length, output_length = module.scoreModel(
        ["hi"], ["sys"], ['{"API_KEY": "k-123", "temperature": 0.2, "azure_openai_resource": "evil"}'])
    assert (response, prompt_length, output_length) == ("OK", 5, 1)
    assert seen["url"] == "https://contoso.openai.azure.com/openai/v1/chat/completions"
    assert seen["headers"]["api-key"] == "k-123" and "Authorization" not in seen["headers"]
    assert seen["body"]["model"] == "my-deployment" and seen["body"]["temperature"] == 0.2


def test_embedding_scorer_posts_with_the_api_key_header_to_the_embeddings_route(core, tmp_path, monkeypatch):
    """The bug: an Azure embedding definition used to inherit the generic
    OpenAI template, posting a bearer token to the bare path '/embeddings'."""
    _clear_azure_env(monkeypatch)
    monkeypatch.setenv("AZURE_OPENAI_RESOURCE", "contoso.cognitiveservices.azure.com")
    module, rendered = _score_module(_azure_manifest(kind="embedding"), core, tmp_path)
    score = rendered["embTestAzScore.py"].decode()
    assert "Bearer" not in score
    seen = _capture_post(module, monkeypatch, "embedding")
    embedding, _, tokens = module.scoreModel(["a document"], ["proj"], ['{"API_KEY": "k-123"}'])
    assert json.loads(embedding) == [0.1, 0.2, 0.3] and tokens == 3
    assert seen["url"] == "https://contoso.cognitiveservices.azure.com/openai/v1/embeddings"
    assert seen["headers"]["api-key"] == "k-123" and "Authorization" not in seen["headers"]
    assert seen["body"] == {"input": "a document", "model": "my-deployment", "encoding_format": "float"}


def test_embedding_dimensions_option_reaches_the_request_body(core, tmp_path, monkeypatch):
    """text-embedding-3-* deployments accept 'dimensions'; the vocabulary maps it
    for the Azure family like for every other OpenAI-shaped embedding family."""
    _clear_azure_env(monkeypatch)
    monkeypatch.setenv("AZURE_OPENAI_RESOURCE", "contoso")
    manifest = _azure_manifest(kind="embedding", options={"dimensions": OptionSpec(default=512)})
    module, rendered = _score_module(manifest, core, tmp_path)
    assert json.loads(rendered["options.json"])["dimensions"]["default"] == 512
    seen = _capture_post(module, monkeypatch, "embedding")
    module.scoreModel(["doc"], ["proj"], ['{"API_KEY": "k"}'])
    assert seen["body"]["dimensions"] == 512


# -- the adapter: where the bug lived -----------------------------------------

def test_azure_adapter_builds_an_embedding_definition_on_its_own_template():
    adapter = AzureFoundryAdapter()
    # The OpenAI-compat parent derives both from base_url, which Azure has none
    # of: inheriting them produced template emb_openai + endpoint '/embeddings'.
    assert adapter.embedding_template == "emb_azure_openai_v1"
    assert adapter.embedding_endpoint({}) is None
    manifest = adapter.build_manifest(
        CatalogModel(ref="my-emb-deployment", display_name="My Embedding", kind="embedding",
                     source="manual entry"),
        "emb_test_az", {"resource": "myres", "api_version": ""}, "tester",
    )
    assert manifest.kind == "embedding"
    assert manifest.runtime.template == "emb_azure_openai_v1"
    assert manifest.provider.endpoint is None
    assert manifest.provider.auth.mode == "api_key" and manifest.provider.auth.key_name == "AzureOpenAI"
    assert manifest.provider.params["commit_resource"] is False


def test_adapter_url_styles_cover_both_routes():
    adapter = AzureFoundryAdapter()
    v1 = _azure_manifest(commit_resource=True)
    assert adapter._chat_url(v1) == "https://myres.openai.azure.com/openai/v1/chat/completions"
    assert adapter._embeddings_url(v1) == "https://myres.openai.azure.com/openai/v1/embeddings"
    legacy = _azure_manifest(commit_resource=True, resource="myres.cognitiveservices.azure.com",
                             api_version="2024-10-21", kind="embedding")
    assert adapter._embeddings_url(legacy) == (
        "https://myres.cognitiveservices.azure.com/openai/deployments/"
        "my-deployment/embeddings?api-version=2024-10-21"
    )
    # bare resource names still expand to the classic Azure OpenAI host
    assert adapter._chat_url(_azure_manifest(commit_resource=True, resource="myres")) == \
        "https://myres.openai.azure.com/openai/v1/chat/completions"


def test_static_endpoint_template_refuses_to_bake_a_bare_path(core):
    """The generator-level guard behind the fix: any template that bakes
    provider.endpoint must get an absolute URL, whatever adapter asked for it."""
    manifest = _azure_manifest(kind="embedding")
    manifest.provider.adapter = "openai"
    manifest.runtime.template = "emb_openai"
    manifest.provider.endpoint = ""
    with pytest.raises(GenerationError, match="bare path"):
        render_assets(manifest, core)
    manifest.provider.endpoint = "/embeddings"
    with pytest.raises(GenerationError, match="bare path"):
        render_assets(manifest, core)
    manifest.provider.endpoint = "https://api.openai.com/v1/embeddings"
    assert "modelEndpoint = 'https://api.openai.com/v1/embeddings'" in \
        render_assets(manifest, core)["embTestAzScore.py"].decode()
    # A hand-maintained scorer (generation.overrides) is not rendered, so not judged.
    manifest.provider.endpoint = ""
    manifest.generation = GenerationBlock(overrides=["embTestAzScore.py"])
    assert "embTestAzScore.py" not in render_assets(manifest, core)


# -- import: legacy folders and hand-written scorers ---------------------------

@pytest.mark.parametrize("host", [
    "contoso.openai.azure.com", "contoso.cognitiveservices.azure.com", "contoso.services.ai.azure.com",
])
def test_import_detects_every_azure_host_flavor(host):
    embedding = (f"import requests\nmodelEndpoint = 'https://{host}/openai/deployments/e/embeddings"
                 "?api-version=2024-10-21'\ndef scoreModel(document, project, options):\n    pass\n")
    assert _detect_family(embedding) == ("embedding", "emb_azure_openai_v1", "azure-foundry")
    chat = embedding.replace("embeddings", "chat/completions").replace(
        "def scoreModel(document, project, options)", "def scoreModel(userPrompt, systemPrompt, options)")
    assert _detect_family(chat) == ("llm", "azure_openai_v1", "azure-foundry")


def test_import_drops_legacy_connection_options_and_round_trips(core, tmp_path, fact_sheet, repo_root):
    """Older Azure folders exposed the connection as options; importing one
    must not carry them into the manifest, or regeneration fails on an option
    the vocabulary does not know."""
    source = repo_root / "LLM-Definitions" / "gpt_4o_mini_az_2024_07_18"
    folder = tmp_path / "gpt_4o_mini_az_2024_07_18"
    shutil.copytree(source, folder, ignore=shutil.ignore_patterns(".mdb-lock.json", "definition.yaml"))
    options = json.loads((folder / "options.json").read_text(encoding="utf-8"))
    options["azure_openai_resource"] = {"default": "legacy.openai.azure.com", "type": "string"}
    options["azure_api_version"] = {"default": "2024-10-21", "type": "string"}
    options["endpoint_url"] = {"default": "", "type": "string"}
    (folder / "options.json").write_text(json.dumps(options, indent=4), encoding="utf-8")

    result = import_folder(folder, fact_sheet)
    assert result.manifest.runtime.template == "azure_openai_v1"
    assert set(result.manifest.options) == {"temperature", "top_p"}
    assert not [n for n in result.notes if "outside the core set" in n]
    # The imported manifest regenerates without touching it by hand.
    rendered = render_assets(result.manifest, core)
    assert set(json.loads(rendered["options.json"])) == {"temperature", "top_p", "API_KEY"}


# -- azure_openai_env: the environment-configured specialist -----------------
#
# Same wire format as azure_openai_v1, but the key and the deployment (model)
# are resolved from the container's environment too, so one image serves any
# Azure deployment.


def test_env_template_resolves_key_and_deployment_from_the_environment(core):
    rendered = render_assets(_azure_env_manifest(), core)
    score = rendered["gptTestAzEnvScore.py"].decode()
    for env_var in AZURE_ENV_VARS:
        assert env_var in score, env_var
    assert "defaultDeploymentName = 'my-deployment'" in score
    assert "_from_env(DEPLOYMENT_ENV, defaultDeploymentName)" in score
    # The key is never a bare options lookup.
    assert 'options["API_KEY"]' not in score
    # Each missing value fails naming its variable rather than as an Azure 401/404.
    assert score.count("raise RuntimeError(") >= 3


def test_env_template_declares_no_api_key_input(core):
    """auth.mode 'none' means no API_KEY entry: UIs neither gate the model on a
    credential-domain entry nor send a key - the container supplies it."""
    options = json.loads(render_assets(_azure_env_manifest(), core)["options.json"])
    assert "API_KEY" not in options
    assert set(options) == {"reasoning_effort", "max_completion_tokens"}


def test_env_scorer_key_option_wins_blank_falls_back_deployment_from_env(core, tmp_path, monkeypatch):
    _clear_azure_env(monkeypatch)
    module, _ = _score_module(_azure_env_manifest(), core, tmp_path)
    seen = _capture_post(module, monkeypatch, "llm")
    with pytest.raises(RuntimeError, match="AZURE_OPENAI_API_KEY"):
        module.scoreModel(["hi"], ["sys"], ["{}"])
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "env-key")
    with pytest.raises(RuntimeError, match="AZURE_OPENAI_RESOURCE"):
        module.scoreModel(["hi"], ["sys"], ["{}"])
    monkeypatch.setenv("AZURE_OPENAI_RESOURCE", "contoso")

    # Nothing set by the caller: key and deployment come from the container.
    module.scoreModel(["hi"], ["sys"], ['{"reasoning_effort": "low"}'])
    assert seen["headers"]["api-key"] == "env-key"
    assert seen["body"]["model"] == "my-deployment"
    assert seen["url"] == "https://contoso.openai.azure.com/openai/v1/chat/completions"

    # A credential-domain caller's key wins; a blank one is 'not set'.
    module.scoreModel(["hi"], ["sys"], ['{"API_KEY": "caller-key"}'])
    assert seen["headers"]["api-key"] == "caller-key"
    module.scoreModel(["hi"], ["sys"], ['{"API_KEY": "  "}'])
    assert seen["headers"]["api-key"] == "env-key"

    # The deployment is the container's decision, not the caller's.
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "other-deployment")
    module.scoreModel(["hi"], ["sys"], ['{"azure_deployment": "evil"}'])
    assert seen["body"]["model"] == "other-deployment"


def test_env_template_reasoning_scale_matches_current_azure_deployments(core):
    """Azure's GPT-5.x deployments expose none|low|medium|high|xhigh and reject
    'minimal', so the normalized scale maps its two ends onto that range."""
    score = render_assets(_azure_env_manifest(), core)["gptTestAzEnvScore.py"].decode()
    assert '"minimal": "none"' in score
    assert '"maximum": "xhigh"' in score
    assert '"minimal": "minimal"' not in score


def test_env_template_keeps_both_endpoint_styles(core):
    plain = render_assets(_azure_env_manifest(), core)["gptTestAzEnvScore.py"].decode()
    assert "/openai/v1/{route}" in plain
    assert "/openai/deployments/{deploymentName}/{route}" in plain
    pinned = render_assets(_azure_env_manifest(api_version="2025-01-01-preview"), core)
    assert "defaultApiVersion = '2025-01-01-preview'" in pinned["gptTestAzEnvScore.py"].decode()


def test_env_adapter_builds_a_keyless_llm_definition():
    adapter = AzureFoundryEnvAdapter()
    assert adapter.id == "azure-foundry-env"
    # LLM only: there is no environment-keyed embedding template; Azure
    # embeddings go through azure-foundry --kind embedding.
    assert adapter.embedding_template is None
    # mdb's own live surface still finds the key in the environment...
    assert adapter.env_key_var == "AZURE_OPENAI_API_KEY"
    manifest = adapter.build_manifest(
        CatalogModel(ref="my-deployment", display_name="My Deployment", source="manual entry"),
        "gpt_test_az_env", {"resource": "", "api_version": ""}, "tester",
    )
    # ...but the generated definition declares no API_KEY input at all.
    assert manifest.provider.auth.mode == "none"
    assert manifest.runtime.template == "azure_openai_env"


def test_env_definition_validates_clean(core, tmp_path, fact_sheet):
    manifest = _azure_env_manifest()
    rendered = render_assets(manifest, core)
    folder = tmp_path / "gpt_test_az_env"
    folder.mkdir()
    manifest.save(folder)
    for name, content in rendered.items():
        (folder / name).write_bytes(content)
    from mdb.core import drift
    drift.write_lock(folder, (folder / "definition.yaml").read_bytes(), rendered)
    issues = validate_folder(folder, core, fact_sheet)
    # V006 is the fact-sheet row, which this throwaway model has no business in.
    assert not [i for i in issues if i.severity == "error" and i.rule != "V006"]
    assert not [i for i in issues if i.rule == "V010"], "options should all be in the vocabulary"
