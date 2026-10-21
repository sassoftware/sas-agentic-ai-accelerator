# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Anthropic Messages API adapter."""
from __future__ import annotations

from typing import Optional

import requests

from ..core.manifest import ModelManifest
from ..core.netutil import get_json
from .base import CatalogModel, ProviderAdapter, SmokeResult, http_smoke_failure

ANTHROPIC_VERSION = "2023-06-01"
#: The effort words of output_config.effort, lowest first.
EFFORT_WORDS = ("low", "medium", "high", "xhigh", "max")


def _supported(capabilities: dict, *path: str) -> Optional[bool]:
    """capabilities[path...]['supported'] of a Models API entry, None where
    the entry does not say."""
    node = capabilities
    for key in path:
        node = node.get(key) if isinstance(node, dict) else None
    if isinstance(node, dict) and isinstance(node.get("supported"), bool):
        return node["supported"]
    return None


def _catalog_model(entry: dict) -> CatalogModel:
    """One entry of GET /v1/models. Since 2026 an entry carries the context
    window (max_input_tokens), the output cap (max_tokens) and its
    capabilities; an entry without them keeps the old assumptions (200k
    context, budget thinking)."""
    capabilities = entry.get("capabilities") or {}
    budget = _supported(capabilities, "thinking", "types", "enabled")
    adaptive = _supported(capabilities, "thinking", "types", "adaptive")
    efforts = [word for word in EFFORT_WORDS if _supported(capabilities, "effort", word)]
    adaptive_only = bool(adaptive) and budget is False
    return CatalogModel(
        ref=entry["id"],
        display_name=entry.get("display_name") or entry["id"],
        release_date=(entry.get("created_at") or "")[:10] or None,
        # uniform for older Claude models where the entry does not say; confirm in the wizard
        context_length=entry.get("max_input_tokens") or 200000,
        max_output_tokens=entry.get("max_tokens"),
        extended_thinking=budget is not False,
        # A model that refuses budget thinking (Claude 5.5) is steered by
        # effort, and refuses temperature / top_p / top_k as well - the
        # Models API has no field for sampling, so this follows the thinking.
        adaptive_thinking=adaptive_only,
        sampling=not adaptive_only,
        reasoning_efforts=efforts if adaptive_only and efforts else None,
        source="live",
    )


class AnthropicAdapter(ProviderAdapter):
    id = "anthropic"
    display_name = "Anthropic"
    provider_tag = "Anthropic"
    key_name = "Anthropic"
    env_key_var = "ANTHROPIC_API_KEY"
    docs_url = "https://console.anthropic.com/settings/keys"
    template = "anthropic_messages"
    requirements_profile = "api-wrapper"
    static_catalog_file = "anthropic.json"

    def endpoint(self, answers: dict) -> Optional[str]:
        return "https://api.anthropic.com/v1/messages"

    def provider_params(self, cm: CatalogModel, answers: dict) -> dict:
        return {"anthropic_version": ANTHROPIC_VERSION}

    def live_catalog(self, session: requests.Session, api_key: Optional[str]) -> list[CatalogModel]:
        if not api_key:
            raise PermissionError(f"Anthropic needs an API key ({self.env_key_var}) to list models.")
        payload = get_json(
            session, "https://api.anthropic.com/v1/models",
            headers={"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION},
        )
        models = []
        for entry in payload.get("data", []):
            models.append(_catalog_model(entry))
        return models

    def smoke_test(self, manifest: ModelManifest, api_key: Optional[str],
                   session: requests.Session) -> SmokeResult:
        if not api_key:
            return SmokeResult(ok=False, detail=f"No API key - set {self.env_key_var} in the environment or .env.")
        try:
            response = session.post(
                self.endpoint({}) or "",
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                },
                json={
                    "model": manifest.provider.model_version,
                    "max_tokens": 16,
                    "messages": [{"role": "user", "content": "Reply with the single word OK."}],
                },
                timeout=60,
            )
            body = response.json()
            if response.status_code >= 300:
                return http_smoke_failure(response, body)
            text = "".join(block["text"] for block in body["content"] if block["type"] == "text")
            usage = body.get("usage", {})
            return SmokeResult(ok=True, detail=(
                f"Provider responded ({usage.get('input_tokens', '?')} in / "
                f"{usage.get('output_tokens', '?')} out tokens): {text[:60]!r}"
            ))
        except Exception as exc:
            return SmokeResult(ok=False, detail=str(exc))
