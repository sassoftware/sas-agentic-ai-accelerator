# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Provider registry: builtin adapters plus 'mdb.providers' entry points."""
from __future__ import annotations

from importlib.metadata import entry_points

from .anthropic import AnthropicAdapter
from .base import ProviderAdapter
from .bedrock import BedrockAdapter
from .google import GoogleAdapter
from .hf_selfhosted import HuggingFaceAdapter
from .openai_compat import (
    AzureFoundryAdapter, AzureFoundryEnvAdapter, OpenAICompatAdapter,
    SelfHostedOpenAICompatAdapter,
)
from .voyage import VoyageAdapter


def _builtin_adapters() -> list[ProviderAdapter]:
    return [
        OpenAICompatAdapter(
            id="openrouter",
            display_name="OpenRouter",
            provider_tag="OpenRouter",
            key_name="OpenRouter",
            env_key_var="OPENROUTER_API_KEY",
            base_url="https://openrouter.ai/api/v1",
            docs_url="https://openrouter.ai/keys",
            listing_needs_key=False,  # the /models catalog is public
        ),
        OpenAICompatAdapter(
            id="openai",
            display_name="OpenAI",
            provider_tag="OpenAI",
            key_name="OpenAI",
            env_key_var="OPENAI_API_KEY",
            base_url="https://api.openai.com/v1",
            docs_url="https://platform.openai.com/api-keys",
            static_catalog_file="openai.json",
        ),
        AzureFoundryAdapter(),
        AzureFoundryEnvAdapter(),
        OpenAICompatAdapter(
            id="mistral",
            display_name="Mistral",
            provider_tag="Mistral",
            key_name="Mistral",
            env_key_var="MISTRAL_API_KEY",
            base_url="https://api.mistral.ai/v1",
            docs_url="https://console.mistral.ai/api-keys",
            static_catalog_file="mistral.json",
        ),
        AnthropicAdapter(),
        BedrockAdapter(),
        GoogleAdapter(),
        VoyageAdapter(),
        HuggingFaceAdapter(),
        SelfHostedOpenAICompatAdapter(
            id="ollama",
            display_name="Ollama",
            provider_tag="Ollama",
            base_url_env="OLLAMA_BASE_URL",
            base_url_default="http://localhost:11434/v1",
            token_env="OLLAMA_API_KEY",
            docs_url="https://ollama.com/search",
        ),
        SelfHostedOpenAICompatAdapter(
            id="vllm",
            display_name="vLLM",
            provider_tag="vLLM",
            base_url_env="VLLM_BASE_URL",
            base_url_default="http://localhost:8000/v1",
            token_env="VLLM_API_KEY",
            docs_url="https://docs.vllm.ai/en/latest/",
        ),
    ]


def load_adapters() -> dict[str, ProviderAdapter]:
    adapters = {adapter.id: adapter for adapter in _builtin_adapters()}
    try:
        discovered = entry_points(group="mdb.providers")
    except TypeError:  # Python < 3.10 select API
        discovered = entry_points().get("mdb.providers", [])
    for entry in discovered:
        try:
            adapter = entry.load()()
            adapters[adapter.id] = adapter
        except Exception as exc:  # a broken third-party adapter must not break the CLI
            print(f"WARNING: could not load provider adapter '{entry.name}': {exc}")
    return adapters
