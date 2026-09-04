# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The model card is what an operator reads in SAS Model Manager when a
container misbehaves. It must state the right scoring contract for the kind
and name the environment variables the container needs (issue #26, bug 3
asked for the Azure route note here)."""
from mdb.core.generator import render_assets
from mdb.providers import load_adapters
from mdb.providers.base import CatalogModel


def _card(core, adapter_key, kind="llm", answers=None, ref="m", **cm_extra):
    adapter = load_adapters()[adapter_key]
    cm = CatalogModel(ref=ref, display_name="M", kind=kind, source="manual", **cm_extra)
    manifest = adapter.build_manifest(cm, f"card_{adapter_key.replace('-', '_')}_{kind}", answers or {}, "tester")
    rendered = render_assets(manifest, core)
    return rendered["Model-Card.md"].decode(), rendered["README.md"].decode()


def test_llm_card_states_the_llm_contract(core):
    card, _ = _card(core, "openai")
    assert "`userPrompt`, `systemPrompt`, `options` → `response`" in card
    assert "LLM Prompt Builder" in card and "`document`" not in card


def test_embedding_card_states_the_embedding_contract(core):
    card, _ = _card(core, "openai", kind="embedding")
    assert "`document`, `project`, `options` → `embedding`, `run_time`, `tokens`" in card
    assert "RAG Builder" in card and "`userPrompt`" not in card


def test_plain_api_card_needs_no_container_configuration(core):
    card, readme = _card(core, "openai")
    assert "## Deployment" in card and "## Deployment" in readme
    assert "`API_KEY` option; the container needs no environment configuration" in card


def test_azure_card_names_the_resource_and_explains_the_route(core):
    card, readme = _card(core, "azure-foundry", answers={"resource": "contoso", "deployment": "dep"})
    for text in (card, readme):
        assert "`AZURE_OPENAI_RESOURCE` is **required**" in text
        assert "`AZURE_OPENAI_API_VERSION` is optional" in text
        assert "bare 401" in text and "`AZURE_OPENAI_ENDPOINT`" in text
        assert "(nothing: the GA route)" in text
    assert "`AZURE_OPENAI_API_KEY`" not in card  # per-call key for the plain Azure template
    assert "The key arrives per call in the `API_KEY` option" in card


def test_azure_card_shows_a_baked_version_and_a_committed_resource(core):
    card, _ = _card(core, "azure-foundry",
                    answers={"resource": "contoso", "deployment": "dep", "api_version": "2024-10-21",
                             "commit_resource": True})
    assert "commits `contoso` as the default, so the variable is optional" in card
    assert "(`2024-10-21`, the legacy route)" in card


def test_azure_env_card_names_the_key_and_deployment_variables(core):
    card, _ = _card(core, "azure-foundry-env", ref="gpt-x")
    assert "`AZURE_OPENAI_API_KEY`" in card and "`AZURE_OPENAI_DEPLOYMENT`" in card
    assert "defaulting to `gpt-x`" in card and "declares no `API_KEY` option" in card


def test_azure_embedding_card_has_the_same_route_note(core):
    card, _ = _card(core, "azure-foundry", kind="embedding", answers={"resource": "contoso", "deployment": "emb"})
    assert "`AZURE_OPENAI_RESOURCE` is **required**" in card and "bare 401" in card
    assert "`document`, `project`, `options`" in card


def test_bedrock_card_names_the_region(core):
    card, _ = _card(core, "bedrock", answers={"region": "eu-central-1"})
    assert "`AWS_BEDROCK_REGION` selects the region (default `eu-central-1`)" in card
    assert "Bedrock API key" in card


def test_selfhosted_card_names_the_base_url_and_token_variables(core):
    card, _ = _card(core, "ollama")
    assert "OLLAMA_BASE_URL" in card and "OLLAMA_API_KEY" in card
    assert "The weights stay on that server" in card
