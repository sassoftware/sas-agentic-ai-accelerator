# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""SCR embedding client (contract probed live — design §0).

POST {scrEndpoint}/{model}/{model} (k8s) or https://{model-with-dashes}.{host}/{model}
(aca) with body {"inputs": [{"name": "document"|"options"|"project", "value": ...}]}.
`options` is the SCR single-string form `{key:value,...}` — no quotes, no spaces
(same contract as scr-api.ts / the LLM containers). Response wraps outputs in
`data`; `data.embedding` is a JSON-stringified float array.
"""
from __future__ import annotations

import json
import time

import requests

MAX_DOCUMENT_CHARS = 100_000  # probed contract ceiling


def options_string(options: dict) -> str:
    return "{" + ",".join(f"{k}:{v}" for k, v in options.items()) + "}"


class EmbeddingClient:
    def __init__(self, scr_endpoint: str, model_name: str, deployment_type: str = "k8s",
                 project: str = "rag", timeout: float = 60.0, max_retries: int = 3,
                 backoff: float = 1.5, verify_ssl: bool = True, session=None,
                 api_key: str = ""):
        """`api_key` is the provider key for a model that calls a hosted API.

        The score code of an API-backed embedding container reads it out of
        the SCR ``options`` argument, so the secret travels per call and is
        never baked into a container environment. Locally-served models
        ignore it, which is why passing it is harmless and omitting it fails
        only for the models that need it.
        """
        self.api_key = str(api_key or "")
        self.model_name = model_name
        self.project = project
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self.verify_ssl = verify_ssl
        self.session = session or requests.Session()
        if deployment_type == "aca":
            host = scr_endpoint.replace("https://", "").replace("http://", "").rstrip("/")
            self.url = f"https://{model_name.replace('_', '-')}.{host}/{model_name}"
        else:
            self.url = f"{scr_endpoint.rstrip('/')}/{model_name}/{model_name}"
        self.usage = {"calls": 0, "run_time": 0.0, "tokens": 0}

    def _options(self, mode: str) -> dict:
        """The SCR options map. API_KEY is included only when we hold one, so
        a locally-served model never receives a key it has no use for."""
        options = {"Embedding_Mode": mode}
        if self.api_key:
            options["API_KEY"] = self.api_key
        return options

    def embed(self, text: str, mode: str = "document") -> list:
        """Embed one text; mode is 'document' (ingest) or 'query' (retrieval)."""
        if len(text) > MAX_DOCUMENT_CHARS:
            raise ValueError(f"document of {len(text)} chars exceeds the SCR contract "
                             f"ceiling of {MAX_DOCUMENT_CHARS}")
        body = {"inputs": [
            {"name": "document", "value": text},
            {"name": "options", "value": options_string(self._options(mode))},
            {"name": "project", "value": self.project},
        ]}
        last_error: Exception = RuntimeError("no attempt made")
        for attempt in range(self.max_retries):
            try:
                response = self.session.post(
                    self.url, json=body, timeout=self.timeout, verify=self.verify_ssl,
                    headers={"Accept": "application/json"},
                )
                if response.status_code in (429, 502, 503, 504):
                    raise RuntimeError(f"retryable HTTP {response.status_code}")
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data", payload)
                embedding = data["embedding"]
                if isinstance(embedding, str):
                    embedding = json.loads(embedding)
                self.usage["calls"] += 1
                self.usage["run_time"] += float(data.get("run_time") or 0)
                self.usage["tokens"] += int(data.get("tokens") or 0)
                return [float(v) for v in embedding]
            except (requests.RequestException, RuntimeError, KeyError, ValueError) as exc:
                last_error = exc
                if attempt < self.max_retries - 1:
                    time.sleep(self.backoff * (2 ** attempt))
        raise RuntimeError(f"embedding call failed after {self.max_retries} attempts "
                           f"({self.url}): {last_error}") from last_error

    def smoke(self) -> int:
        """One tiny call; returns the embedding dimension (fails fast on misconfig)."""
        return len(self.embed("smoke test", mode="query"))

    @classmethod
    def from_env(cls, **overrides) -> "EmbeddingClient":
        """Build the client from RAGEMBED_* environment variables.

        The SCR/MAS destination path (design §4a): a published retrieval
        container has no Viya session, so its embedding call is configured at
        deployment time — the same env-var pattern the LLM containers use.

          RAGEMBED_ENDPOINT         required  SCR base URL (e.g. https://host/llm)
          RAGEMBED_MODEL            required  embedding model name
          RAGEMBED_DEPLOYMENT_TYPE  optional  k8s (default) | aca
          RAGEMBED_PROJECT          optional  project tag sent with each call
          RAGEMBED_SSLVERIFY        optional  false disables TLS verification;
                                    a path is used as the CA bundle

        Keyword overrides win over the environment (the manifested
        retrieve_context.py passes its baked-in setup values as overrides, so
        env vars only need to cover what the manifest cannot know).
        """
        import os

        endpoint = overrides.pop("scr_endpoint", None) or os.environ.get("RAGEMBED_ENDPOINT", "")
        model = overrides.pop("model_name", None) or os.environ.get("RAGEMBED_MODEL", "")
        if not endpoint or not model:
            raise KeyError("embedding configuration incomplete: set RAGEMBED_ENDPOINT "
                           "and RAGEMBED_MODEL (or pass scr_endpoint/model_name)")
        deployment = overrides.pop("deployment_type", None) \
            or os.environ.get("RAGEMBED_DEPLOYMENT_TYPE", "k8s")
        project = overrides.pop("project", None) or os.environ.get("RAGEMBED_PROJECT", "rag")
        raw_verify = os.environ.get("RAGEMBED_SSLVERIFY", "")
        verify = overrides.pop("verify_ssl", None)
        if verify is None:
            if raw_verify.lower() in ("false", "no", "off", "0"):
                verify = False
            elif raw_verify:
                verify = raw_verify        # path to a CA bundle
            else:
                verify = True
        return cls(endpoint, model, deployment_type=deployment, project=project,
                   verify_ssl=verify, **overrides)
