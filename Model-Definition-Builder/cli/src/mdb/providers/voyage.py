# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Voyage AI adapter - embeddings only, static catalog (Voyage has no models API)."""
from __future__ import annotations

from typing import Optional

import requests

from ..core.manifest import ModelManifest, OptionSpec
from .base import CatalogModel, ProviderAdapter, SmokeResult, http_smoke_failure


class VoyageAdapter(ProviderAdapter):
    id = "voyage"
    display_name = "Voyage AI"
    provider_tag = "Voyage"
    key_name = "VoyageAI"  # matches the LLM_API_KEYS KeyName the legacy fleet established
    env_key_var = "VOYAGE_API_KEY"
    docs_url = "https://dashboard.voyageai.com/api-keys"
    template = "emb_voyage"  # embeddings only - both slots point at the same template
    embedding_template = "emb_voyage"
    requirements_profile = "api-wrapper"
    static_catalog_file = "voyage.json"

    def endpoint(self, answers: dict) -> Optional[str]:
        return "https://api.voyageai.com/v1/embeddings"

    def embedding_endpoint(self, answers: dict) -> Optional[str]:
        return "https://api.voyageai.com/v1/embeddings"

    def embedding_options(self, cm: CatalogModel) -> dict[str, OptionSpec]:
        options = {"input_type": OptionSpec(default="document")}
        options.update(super().embedding_options(cm))
        return options

    def smoke_test(self, manifest: ModelManifest, api_key: Optional[str],
                   session: requests.Session) -> SmokeResult:
        if not api_key:
            return SmokeResult(ok=False, detail=f"No API key - set {self.env_key_var} in the environment or .env.")
        try:
            response = session.post(
                "https://api.voyageai.com/v1/embeddings",
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                json={"input": "OK", "model": manifest.provider.model_version, "input_type": "document"},
                timeout=60,
            )
            body = response.json()
            if response.status_code >= 300:
                return http_smoke_failure(response, body)
            vector = body["data"][0]["embedding"]
            return SmokeResult(ok=True, detail=f"Provider responded with a {len(vector)}-dimension embedding.")
        except Exception as exc:
            return SmokeResult(ok=False, detail=str(exc))
