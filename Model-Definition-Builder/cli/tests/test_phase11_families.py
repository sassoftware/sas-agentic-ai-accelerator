# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Phase 1.1: embedding kind, Bedrock/Google/Voyage families, typed options."""
import json

from mdb.core.facts import COLUMNS_BY_KIND, row_values
from mdb.core.generator import render_assets
from mdb.core.importer import import_folder
from mdb.core.manifest import (
    AuthBlock, MetadataBlock, ModelManifest, OptionSpec, PricingBlock,
    ProviderBlock, RuntimeBlock, TagsBlock,
)


def _embedding_manifest(template: str, adapter: str, key_name: str | None = "OpenAI",
                        endpoint: str | None = None, params: dict | None = None,
                        options: dict | None = None) -> ModelManifest:
    return ModelManifest(
        kind="embedding",
        model_id="test_embed_1",
        display_name="Test Embed 1",
        provider=ProviderBlock(
            adapter=adapter, model_version="test-embed-model", endpoint=endpoint,
            params=params or {},
            auth=AuthBlock(mode="api_key", key_name=key_name) if key_name else AuthBlock(mode="none"),
        ),
        runtime=RuntimeBlock(template=template,
                             requirements_profile="hf-sentence-transformers"
                             if template == "emb_sentence_transformers" else "api-wrapper"),
        options=options or {
            "Embedding_Length": OptionSpec(default=1536),
            "Input_Token_Limit": OptionSpec(default=8192),
        },
        tags=TagsBlock(size_class="Embedding", license_class="Proprietary",
                       provider_tag="OpenAI", scr_sizing="small"),
        metadata=MetadataBlock(
            description="A test embedding model.",
            deployment_type="SCR" if template == "emb_sentence_transformers" else "API",
            pricing=PricingBlock(cost_type="Tokens", input_token_price=2e-8),
        ),
        modeler="tester",
    )


def test_embedding_contract_and_arity_fix(core):
    manifest = _embedding_manifest("emb_openai", "openai", endpoint="https://api.openai.com/v1/embeddings")
    rendered = render_assets(manifest, core)
    score = rendered["testEmbed1Score.py"].decode()
    # all three declared outputs are returned - the legacy 2-of-3 bug is fixed centrally
    assert "return embedding, run_time, tokens" in score
    assert '"Output: embedding, run_time, tokens"' in score
    # informational options never enter the scorer
    assert "Embedding_Length" not in score
    options = json.loads(rendered["options.json"])
    assert options["Embedding_Length"]["default"] == 1536
    assert options["Embedding_Length"]["range"] == "1536"
    # embedding variable contract + config constants
    assert json.loads(rendered["inputVar.json"])[0]["name"] == "document"
    config = json.loads(rendered["modelConfiguration.json"])
    assert config["targetVariable"] == "embedding"
    assert config["function"] == "embedding"
    assert config["algorithm"] == "Embedding"
    assert config["tags"][0] == "Embedding"


def test_sentence_transformers_bug_fixes(core):
    manifest = _embedding_manifest(
        "emb_sentence_transformers", "hf-selfhosted", key_name=None,
        params={"hf": {"repo": "BAAI/bge-small-en-v1.5", "gated": False}},
        options={
            "Embedding_Mode": OptionSpec(default="query"),
            "Embedding_Length": OptionSpec(default=384),
            "Input_Token_Limit": OptionSpec(default=512),
        },
    )
    rendered = render_assets(manifest, core)
    score = rendered["testEmbed1Score.py"].decode()
    # dumps the FULL vector (not element [0]) and tokenizes the DOCUMENT (not the vector)
    assert "json.dumps(embeddingObject.tolist())" in score
    assert "model.preprocess([document[0]])" in score
    assert "return embedding, run_time, tokens" in score
    steps = json.loads(rendered["requirements.json"])
    assert any("sentence-transformers" in s["command"] for s in steps)


def test_bedrock_converse_render(core):
    manifest = ModelManifest(
        model_id="test_bedrock_1",
        display_name="Test Bedrock 1",
        provider=ProviderBlock(
            adapter="bedrock", model_version="us.anthropic.claude-haiku-4-5-20251001-v1:0",
            params={"region": "us-east-1", "auth_variant": "bearer"},
            auth=AuthBlock(mode="api_key", key_name="AWSBedrock"),
        ),
        runtime=RuntimeBlock(template="bedrock_converse", requirements_profile="api-wrapper"),
        options={
            "temperature": OptionSpec(default=1),
            "top_p": OptionSpec(default=1),
            "max_tokens": OptionSpec(default=1000, max=64000),
        },
        tags=TagsBlock(size_class="LLM", license_class="Proprietary",
                       provider_tag="AWS Bedrock", scr_sizing="small"),
        metadata=MetadataBlock(description="Bedrock test.",
                               pricing=PricingBlock(cost_type="Tokens", input_token_price=1e-6,
                                                    output_token_price=5e-6)),
        modeler="tester",
    )
    score = render_assets(manifest, core)["testBedrock1Score.py"].decode()
    # region resolves env-neutrally, options land nested in inferenceConfig
    assert 'os.environ.get("AWS_BEDROCK_REGION", "us-east-1")' in score
    assert '"maxTokens": int(options["max_tokens"]),' in score
    assert '"inferenceConfig"' in score
    assert "urllib.parse.quote(modelId" in score  # ':' in model ids must be URL-encoded
    assert "usage']['inputTokens'" in score.replace('"', "'")


def test_gemini_generate_render(core):
    manifest = ModelManifest(
        model_id="test_gemini_1",
        display_name="Test Gemini 1",
        provider=ProviderBlock(adapter="google", model_version="gemini-2.5-flash",
                               auth=AuthBlock(mode="api_key", key_name="Google")),
        runtime=RuntimeBlock(template="gemini_generate", requirements_profile="api-wrapper"),
        options={
            "temperature": OptionSpec(default=1),
            "top_k": OptionSpec(default=40),
            "max_tokens": OptionSpec(default=1000, max=65536),
        },
        tags=TagsBlock(size_class="LLM", license_class="Proprietary",
                       provider_tag="Google", scr_sizing="small"),
        metadata=MetadataBlock(description="Gemini test.",
                               pricing=PricingBlock(cost_type="Tokens", input_token_price=3e-7,
                                                    output_token_price=2.5e-6)),
        modeler="tester",
    )
    score = render_assets(manifest, core)["testGemini1Score.py"].decode()
    assert '"topK": int(options["top_k"]),' in score
    assert '"maxOutputTokens": int(options["max_tokens"]),' in score
    assert "generationConfig" in score
    assert "x-goog-api-key" in score
    assert "usageMetadata" in score


def test_bedrock_titan_embedding_render(core):
    manifest = _embedding_manifest(
        "emb_bedrock_titan", "bedrock", key_name="AWSBedrock",
        params={"region": "us-east-1", "auth_variant": "bearer"},
        options={
            "normalize": OptionSpec(default=True),
            "Embedding_Length": OptionSpec(default=1024),
            "Input_Token_Limit": OptionSpec(default=8192),
        },
    )
    rendered = render_assets(manifest, core)
    score = rendered["testEmbed1Score.py"].decode()
    assert 'os.environ.get("AWS_BEDROCK_REGION", "us-east-1")' in score
    assert '"normalize": bool(options["normalize"]),' in score
    assert "return embedding, run_time, tokens" in score
    options = json.loads(rendered["options.json"])
    assert options["normalize"]["type"] == "bool"


def test_typed_options_emitted_additively(core):
    manifest = _embedding_manifest(
        "emb_voyage", "voyage", key_name="Voyage",
        endpoint="https://api.voyageai.com/v1/embeddings",
        options={
            "input_type": OptionSpec(default="document"),
            "Embedding_Length": OptionSpec(default=1024),
        },
    )
    options = json.loads(render_assets(manifest, core)["options.json"])
    # enum options carry the additive type/values fields for the Prompt Builder
    assert options["input_type"]["type"] == "enum"
    assert options["input_type"]["values"] == ["document", "query"]
    # numeric options stay legacy-shaped (no type field)
    assert "type" not in options["Embedding_Length"]


def test_embedding_fact_sheet_row(core):
    manifest = _embedding_manifest("emb_openai", "openai")
    values = row_values(manifest)
    assert set(values) == set(COLUMNS_BY_KIND["embedding"])
    assert values["max_tokens"] == "8192"
    assert values["embedding_length"] == "1536"


def test_import_legacy_embedding_folders(repo_root):
    fact_sheet = repo_root / "Embedding-Definitions" / "embedding_fact_sheet.csv"
    voyage = import_folder(repo_root / "Embedding-Definitions" / "voyage_35", fact_sheet)
    assert voyage.manifest.kind == "embedding"
    assert voyage.manifest.runtime.template == "emb_voyage"
    assert voyage.manifest.provider.model_version == "voyage-3.5"

    minilm = import_folder(repo_root / "Embedding-Definitions" / "all_minilm_l6_v2", fact_sheet)
    assert minilm.manifest.kind == "embedding"
    assert minilm.manifest.runtime.template == "emb_sentence_transformers"
    assert minilm.manifest.provider.params["hf"]["repo"] == "sentence-transformers/all-MiniLM-L6-v2"
