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

from ..core.manifest import (
    AuthBlock, GenerationBlock, MetadataBlock, ModelManifest, OptionSpec,
    PricingBlock, ProviderBlock, RuntimeBlock, TagsBlock,
)
from ..core.netutil import get_json
from .base import CatalogModel, ProviderAdapter, Question, SmokeResult


class OpenAICompatAdapter(ProviderAdapter):
    template = "openai_chat"
    embedding_template = "emb_openai"

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

    def embedding_endpoint(self, answers: dict) -> Optional[str]:
        return f"{self.base_url}/embeddings"

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


class SelfHostedOpenAICompatAdapter(OpenAICompatAdapter):
    """Ollama, vLLM, or any self-hosted OpenAI-compatible inference server.

    Serves both chat (LLM) and embedding models over the OpenAI wire format.
    The definition is environment-neutral: the base URL resolves at scoring
    time from an environment variable (per deployment) with a localhost default
    baked in, and an optional bearer token is read from a second env var. The
    model weights live on the server, not in the SCR image, so the container
    only needs the thin api-wrapper requirements.
    """

    template = "openai_compat_selfhosted"
    embedding_template = "emb_openai_compat_selfhosted"

    def __init__(self, id: str, display_name: str, provider_tag: str,
                 base_url_env: str, base_url_default: str, token_env: str,
                 docs_url: str = ""):
        super().__init__(
            id=id, display_name=display_name, provider_tag=provider_tag,
            key_name=None, env_key_var=None, base_url=base_url_default,
            docs_url=docs_url, listing_needs_key=False,
        )
        self.base_url_env = base_url_env
        self.base_url_default = base_url_default
        self.token_env = token_env

    def _resolved_base(self, answers: Optional[dict] = None) -> str:
        # For CLI-side operations (smoke test, catalog browse) the environment
        # variable wins: it points at the operator's real server. A per-call
        # base_url or the baked localhost default is the fallback. (The deployed
        # score script uses the runtime precedence: option > env var > default.)
        base = (os.environ.get(self.base_url_env)
                or (answers or {}).get("base_url")
                or self.base_url_default)
        return base.rstrip("/")

    def questions(self) -> list[Question]:
        return [
            Question("kind", "Model kind: llm or embedding", default="llm", required=False),
            Question("base_url", f"Server base URL (blank = {self.base_url_default}, "
                                 f"overridable at runtime via {self.base_url_env})",
                     default=self.base_url_default, required=False),
            Question("license", "License class (Open-Source or Proprietary)",
                     default="Open-Source", required=False),
            Question("embedding_length", "Embedding vector length (embedding kind only)",
                     default="", required=False),
            Question("context_length", "Input token limit / context length",
                     default="", required=False),
        ]

    def _wants_embedding(self, cm: CatalogModel, answers: dict) -> bool:
        return (answers.get("kind") or cm.kind or "llm").strip().lower() == "embedding"

    def build_manifest(self, cm: CatalogModel, model_id: str, answers: dict, modeler: str) -> ModelManifest:
        kind_answer = (answers.get("kind") or "").strip().lower()
        if kind_answer and kind_answer not in ("llm", "embedding"):
            raise ValueError(f"kind must be 'llm' or 'embedding', got {answers.get('kind')!r}.")
        is_embedding = self._wants_embedding(cm, answers)
        kind = "embedding" if is_embedding else "llm"
        # Only bake a non-default base URL; a blank/default keeps the localhost
        # convenience default and stays fully environment-driven.
        chosen_base = (answers.get("base_url") or "").strip()
        baked_base = chosen_base if chosen_base and chosen_base != self.base_url_default else self.base_url_default
        params = {
            "base_url": baked_base,
            "base_url_env": self.base_url_env,
            "token_env": self.token_env,
        }

        def _int(value, fallback):
            try:
                return int(value)
            except (TypeError, ValueError):
                return fallback

        if is_embedding:
            emb_len = _int(answers.get("embedding_length"), cm.embedding_length or 768)
            ctx_len = _int(answers.get("context_length"), cm.context_length or 8192)
            options = {
                "Embedding_Length": OptionSpec(default=emb_len),
                "Input_Token_Limit": OptionSpec(default=ctx_len),
            }
            template = self.embedding_template
            size_class = "Embedding"
            # Keep the Model Card / metadata context in step with the option default.
            context_for_meta = ctx_len
        else:
            options = self.default_options(cm)
            template = self.template
            size_class = "LLM"
            context_for_meta = _int(answers.get("context_length"), cm.context_length) or None

        return ModelManifest(
            kind=kind,
            model_id=model_id,
            display_name=cm.display_name,
            provider=ProviderBlock(
                adapter=self.id,
                model_version=cm.ref,
                params=params,
                auth=AuthBlock(mode="none"),
            ),
            runtime=RuntimeBlock(template=template, requirements_profile="api-wrapper"),
            options=options,
            tags=TagsBlock(
                size_class=size_class,
                license_class=(answers.get("license") or "Open-Source").strip() or "Open-Source",
                provider_tag=self.provider_tag,
                scr_sizing="small",
            ),
            metadata=MetadataBlock(
                description=answers.get("description")
                or f"{cm.display_name}, served from a self-hosted {self.display_name} server.",
                context_length=context_for_meta,
                deployment_type="API",
                pricing=PricingBlock(cost_type="Seconds"),
            ),
            modeler=modeler,
            generation=GenerationBlock(catalog_provenance=f"{self.id} ({cm.source})"),
        )

    def live_catalog(self, session: requests.Session, api_key: Optional[str]) -> list[CatalogModel]:
        base = self._resolved_base()
        headers = {}
        token = os.environ.get(self.token_env)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        payload = get_json(session, f"{base}/models", headers=headers)
        models: list[CatalogModel] = []
        for entry in payload.get("data", []):
            ref = entry.get("id", "")
            if ref:
                models.append(CatalogModel(ref=ref, display_name=entry.get("name") or ref, source="live"))
        return models

    def smoke_test(self, manifest: ModelManifest, api_key: Optional[str],
                   session: requests.Session) -> SmokeResult:
        base = self._resolved_base(manifest.provider.params)
        headers = {"Content-Type": "application/json"}
        token = os.environ.get(self.token_env)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            if manifest.kind == "embedding":
                response = session.post(
                    f"{base}/embeddings", headers=headers,
                    json={"model": manifest.provider.model_version, "input": "ping",
                          "encoding_format": "float"},
                    timeout=60,
                )
                body = response.json()
                if response.status_code >= 300:
                    return SmokeResult(ok=False, detail=f"HTTP {response.status_code}: {body}")
                dims = len(body["data"][0]["embedding"])
                return SmokeResult(ok=True, detail=f"Server returned a {dims}-dim embedding from {base}.")
            response = session.post(
                f"{base}/chat/completions", headers=headers,
                json={"model": manifest.provider.model_version,
                      "messages": [{"role": "user", "content": "Reply with the single word OK."}]},
                timeout=60,
            )
            body = response.json()
            if response.status_code >= 300:
                return SmokeResult(ok=False, detail=f"HTTP {response.status_code}: {body}")
            text = body["choices"][0]["message"]["content"]
            return SmokeResult(ok=True, detail=f"Server responded from {base}: {text[:60]!r}")
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            return SmokeResult(ok=False, detail=f"{exc} (is the server reachable at {base}?)")
        except Exception as exc:
            # Reached the server but got an unexpected response shape/body.
            return SmokeResult(ok=False, detail=f"{exc} (unexpected response from {base})")


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
