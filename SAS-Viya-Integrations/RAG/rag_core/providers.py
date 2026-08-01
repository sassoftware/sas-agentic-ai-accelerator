# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Which credential-domain entry an embedding model needs, if any.

An embedding model that runs as a local container needs no key. One that
forwards to a hosted API reads its key from the SCR ``options`` argument, and
that key lives in the credential domain under the PROVIDER's name — the same
entry the Prompt Builder uses for that provider's LLMs (``OpenAI``,
``Google``, …). So the mapping this module owns is model → provider entry.

Mirrors the provider column of Embedding-Definitions/embedding_fact_sheet.csv
for the models whose cost_type is Tokens; everything else is served locally
and maps to "". A model that is not listed also maps to "", which is the safe
default: a locally-served model works, and an unlisted API model fails at the
first call with the container's own "missing API_KEY" error rather than
having a wrong key sent on its behalf.

Adding an API-backed embedding definition means adding a row here.
"""
from __future__ import annotations

#: model_id -> credential-domain entry name
API_KEY_ENTRIES = {
    "gemini_embedding_001": "Google",
    "text_embedding_3_large": "OpenAI",
    "text_embedding_3_small": "OpenAI",
    "titan_embed_text_v2": "AWS Bedrock",
    "voyage_35": "Voyage.ai",
    "voyage_35_lite": "Voyage.ai",
    "voyage_code_3": "Voyage.ai",
    "voyage_finance_2": "Voyage.ai",
    "voyage_law_2": "Voyage.ai",
}


def api_key_entry(model: str) -> str:
    """The domain entry holding this model's provider key, or "" if none."""
    return API_KEY_ENTRIES.get(str(model or "").strip(), "")


def api_key_for(model: str, secrets: dict) -> str:
    """The provider key for `model` out of a decoded secrets map.

    Returns "" for a locally-served model. Raises when the model needs a key
    the caller does not hold, because failing here names the missing entry -
    the alternative is an HTTP 422 from the container after the crawl and the
    chunking have already run.
    """
    entry = api_key_entry(model)
    if not entry:
        return ""
    key = str((secrets or {}).get(entry) or "").strip()
    if not key:
        raise KeyError(
            f"the embedding model {model} calls a hosted API and needs the "
            f"{entry!r} entry in the credential domain - add it and rerun the "
            "create-credential-domain script")
    return key
