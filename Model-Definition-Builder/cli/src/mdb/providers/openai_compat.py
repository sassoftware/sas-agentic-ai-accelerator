# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""One parameterized OpenAI-compatible adapter, instantiated per vendor.

OpenAI, OpenRouter, Mistral and Azure AI Foundry (v1 endpoint) all speak the
Chat Completions wire format; they differ only in base URL, auth header and
catalog source. OpenRouter doubles as the metadata backbone: its public
/models listing is the only programmatic source of pricing and context data.
"""
from __future__ import annotations

import os
from typing import Optional

import requests

from ..core.manifest import ModelManifest
from ..core.netutil import get_json
from .base import CatalogModel, ProviderAdapter, Question, SmokeResult


class OpenAICompatAdapter(ProviderAdapter):
    template = "openai_chat"

    def __init__(self, id: str, display_name: str, provider_tag: str, key_name: str,
                 env_key_var: str, base_url: str, docs_url: str = "",
                 static_catalog_file: Optional[str] = None, listing_needs_key: bool = True):
        self.id = id
        self.display_name = display_name
        self.provider_tag = provider_tag
        self.key_name = key_name
        self.env_key_var = env_key_var
        self.base_url = base_url.rstrip("/")
        self.docs_url = docs_url
        self.static_catalog_file = static_catalog_file
        self.listing_needs_key = listing_needs_key

    def endpoint(self, answers: dict) -> Optional[str]:
        return f"{self.base_url}/chat/completions"

    def live_catalog(self, session: requests.Session, api_key: Optional[str]) -> list[CatalogModel]:
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        elif self.listing_needs_key:
            raise PermissionError(f"{self.display_name} needs an API key ({self.env_key_var}) to list models.")
        payload = get_json(session, f"{self.base_url}/models", headers=headers)
        static_by_ref = {}
        models: list[CatalogModel] = []
        for entry in payload.get("data", []):
            ref = entry.get("id", "")
            if not ref:
                continue
            pricing = entry.get("pricing") or {}

            def _per_m(key: str) -> Optional[float]:
                raw = pricing.get(key)
                try:
                    return float(raw) * 1_000_000 if raw is not None else None
                except (TypeError, ValueError):
                    return None

            models.append(CatalogModel(
                ref=ref,
                display_name=entry.get("name") or ref,
                context_length=entry.get("context_length"),
                max_output_tokens=(entry.get("top_provider") or {}).get("max_completion_tokens"),
                input_price_per_m=_per_m("prompt"),
                output_price_per_m=_per_m("completion"),
                supported_parameters=entry.get("supported_parameters"),
                reasoning=bool(entry.get("supported_parameters"))
                and "reasoning" in (entry.get("supported_parameters") or [])
                and "temperature" not in (entry.get("supported_parameters") or []),
                source="live",
            ))
        return models

    def smoke_test(self, manifest: ModelManifest, api_key: Optional[str],
                   session: requests.Session) -> SmokeResult:
        if not api_key:
            return SmokeResult(ok=False, detail=f"No API key - set {self.env_key_var} in the environment or .env.")
        try:
            response = session.post(
                self.endpoint({}) or "",
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                json={
                    "model": manifest.provider.model_version,
                    "messages": [{"role": "user", "content": "Reply with the single word OK."}],
                },
                timeout=60,
            )
            body = response.json()
            if response.status_code >= 300:
                return SmokeResult(ok=False, detail=f"HTTP {response.status_code}: {body}")
            text = body["choices"][0]["message"]["content"]
            usage = body.get("usage", {})
            return SmokeResult(ok=True, detail=(
                f"Provider responded ({usage.get('prompt_tokens', '?')} in / "
                f"{usage.get('completion_tokens', '?')} out tokens): {text[:60]!r}"
            ))
        except Exception as exc:
            return SmokeResult(ok=False, detail=str(exc))


class AzureFoundryAdapter(OpenAICompatAdapter):
    """Azure AI Foundry / Azure OpenAI via the GA v1 endpoint (key-only mode).

    Models are addressed by DEPLOYMENT NAME (chosen by the user in Azure),
    and the endpoint is built at runtime from the azure_openai_resource
    option so one registered model works across environments.
    """

    template = "azure_openai_v1"

    def __init__(self):
        super().__init__(
            id="azure-foundry",
            display_name="Azure AI Foundry",
            provider_tag="Azure OpenAI",
            key_name="AzureOpenAI",
            env_key_var="AZURE_OPENAI_API_KEY",
            base_url="",  # built at runtime from the resource
            docs_url="https://learn.microsoft.com/azure/ai-foundry/",
        )

    def endpoint(self, answers: dict) -> Optional[str]:
        return None  # runtime-configurable via options

    def questions(self) -> list[Question]:
        return [
            Question("resource", "Azure resource host (e.g. myres.openai.azure.com or just 'myres')",
                     default=os.environ.get("AZURE_OPENAI_RESOURCE", "")),
            Question("deployment", "Deployment name (as chosen in Azure AI Foundry)"),
        ]

    def provider_params(self, cm: CatalogModel, answers: dict) -> dict:
        return {
            "resource": answers.get("resource", ""),
            # False keeps the definition environment-neutral: the resource is only
            # used CLI-side (smoke tests), while deployed containers resolve it via
            # the AZURE_OPENAI_RESOURCE environment variable or a per-call option.
            "commit_resource": bool(answers.get("commit_resource")),
        }

    def _resource_base(self, manifest: ModelManifest) -> str:
        host = (manifest.provider.params.get("resource")
                or os.environ.get("AZURE_OPENAI_RESOURCE") or "").strip()
        if host and "." not in host:
            host = f"{host}.openai.azure.com"
        return f"https://{host}/openai/v1"

    def live_catalog(self, session: requests.Session, api_key: Optional[str]) -> list[CatalogModel]:
        raise NotImplementedError(
            "Azure models are addressed by your deployment name - enter it in the wizard. "
            "(ARM-based deployment listing arrives in Phase 1.1.)"
        )

    def smoke_test(self, manifest: ModelManifest, api_key: Optional[str],
                   session: requests.Session) -> SmokeResult:
        if not api_key:
            return SmokeResult(ok=False, detail=f"No API key - set {self.env_key_var} in the environment or .env.")
        try:
            response = session.post(
                f"{self._resource_base(manifest)}/chat/completions",
                headers={"Content-Type": "application/json", "api-key": api_key},
                json={
                    "model": manifest.provider.model_version,
                    "messages": [{"role": "user", "content": "Reply with the single word OK."}],
                },
                timeout=60,
            )
            body = response.json()
            if response.status_code >= 300:
                return SmokeResult(ok=False, detail=f"HTTP {response.status_code}: {body}")
            return SmokeResult(ok=True, detail=f"Deployment responded: {body['choices'][0]['message']['content'][:60]!r}")
        except Exception as exc:
            return SmokeResult(ok=False, detail=str(exc))
