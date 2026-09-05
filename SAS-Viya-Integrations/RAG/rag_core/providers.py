# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Which credential-domain entry a model needs, if any.

A model that runs as a local container needs no key. One that forwards to a
hosted API reads its key from the SCR ``options`` argument, and that key lives
in the credential domain under the KeyName the definition's ``API_KEY`` option
references (``key_name`` in definition.yaml, ``API_KEY.default`` in
options.json) — the same entry the Prompt Builder resolves for that model
(``OpenAI``, ``Google``, ``AzureOpenAI``, …), and the name the
create-credential-domain scripts and ``mdb credentials-apply`` write. So the
mapping this module owns is model → key entry.

Two maps, one rule. ``API_KEY_ENTRIES`` covers the embedding models of
Embedding-Definitions/embedding_fact_sheet.csv whose cost_type is Tokens;
``LLM_KEY_ENTRIES`` covers every LLM of LLM-Definitions/llm_fact_sheet.csv
whose deployment_type is API — the models the Enrich stage can be pointed at.
The entry is each definition's ``API_KEY.default``; a hosted model whose
options declare no ``API_KEY`` (the container holds the key, e.g. the
environment-configured Azure definition) and everything served locally map
to "".

An unlisted model maps to "" too, which is the safe default in one direction
and an honest failure in the other: a locally-served model works, and an
unlisted API model fails at the first call with the container's own "missing
API_KEY" error rather than having a wrong key sent on its behalf.

Adding an API-backed definition means adding a row here:

    python -m rag_core.providers --from ../../Embedding-Definitions/embedding_fact_sheet.csv
    python -m rag_core.providers --llm-from ../../LLM-Definitions/llm_fact_sheet.csv
"""
from __future__ import annotations

#: embedding model_id -> credential-domain entry name
API_KEY_ENTRIES = {
    "gemini_embedding_001": "Google",
    "text_embedding_3_large": "OpenAI",
    "text_embedding_3_small": "OpenAI",
    "titan_embed_text_v2": "AWSBedrock",
    "voyage_35": "VoyageAI",
    "voyage_35_lite": "VoyageAI",
    "voyage_code_3": "VoyageAI",
    "voyage_finance_2": "VoyageAI",
    "voyage_law_2": "VoyageAI",
}


#: LLM model_id -> credential-domain entry name (deployment_type API only)
LLM_KEY_ENTRIES = {
    "claude_haiku_4_5_bedrock": "AWSBedrock",
    "claude_sonnet_4_5": "Anthropic",
    "free_models_router": "OpenRouter",
    "gemini_flash_25": "Google",
    "google_gemma_4_31b_free": "OpenRouter",
    "gpt_41_mini": "OpenAI",
    "gpt_4o_2024_05_13": "OpenAI",
    "gpt_4o_mini_2024_07_18": "OpenAI",
    "gpt_4o_mini_2025_01_01": "OpenAI",
    "gpt_4o_mini_az_2024_07_18": "AzureOpenAI",
    "gpt_56_sol": "OpenAI",
    "gpt_5_mini": "OpenAI",
    "ling_3_0_flash_free": "OpenRouter",
    "llama_33_70b": "OpenRouter",
    "mistral_small_32": "Mistral",
    "moonshotai_kimi_k3": "OpenRouter",
    "nvidia_nemotron_3_ultra_free": "OpenRouter",
    "poolside_laguna_s_2_1_free": "OpenRouter",
}


def api_key_entry(model: str) -> str:
    """The domain entry holding this model's provider key, or "" if none."""
    return API_KEY_ENTRIES.get(str(model or "").strip(), "")


def llm_api_key_entry(model: str) -> str:
    """The same question for an LLM — which entry holds ITS provider key."""
    return LLM_KEY_ENTRIES.get(str(model or "").strip(), "")


def llm_api_key_for(model: str, secrets: dict, required: bool = True) -> str:
    """The provider key an enrichment prompt's LLM needs.

    `required` is what the manifested prompt's own signature says: a score
    module that takes no API_KEY parameter serves a local container and needs
    nothing, so a missing entry is not a problem to raise about. When it DOES
    take one, an empty key produces an authentication failure on every chunk
    of the corpus, so this refuses early and names the entry to add.
    """
    entry = llm_api_key_entry(model)
    key = str((secrets or {}).get(entry) or "").strip() if entry else ""
    if key or not required:
        return key
    if not entry:
        raise KeyError(
            f"the prompt this setup enriches with calls {model or 'an LLM'}, "
            "which needs an API key, but that model is not in this rag_core's "
            "copy of the LLM fact sheet - so there is no way to tell which "
            "credential-domain entry holds its key. Add the model to "
            "rag_core/providers.py, or enrich with a locally served model")
    raise KeyError(
        f"the prompt this setup enriches with calls {model} and needs the "
        f"{entry!r} entry in the credential domain - add it to your .env and "
        "rerun the create-credential-domain script")


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


def key_entries(csv_path: str, api_only: bool) -> dict:
    """model_id -> key entry for the hosted models of one fact sheet.

    The sheet says which models call a hosted API - the embedding sheet
    through cost_type, the LLM sheet through deployment_type, so the caller
    passes which test applies rather than this guessing. The entry itself is
    the `API_KEY` default of the model's options.json, which sits in the
    definition folder next to the sheet; a hosted model without an `API_KEY`
    option needs no entry and is left out.
    """
    import csv
    import json
    import os

    entries = {}
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        for row in sorted(csv.DictReader(fh), key=lambda r: r.get("model_id") or ""):
            hosted = ((row.get("deployment_type") or "").strip() == "API"
                      if api_only else
                      (row.get("cost_type") or "").strip() == "Tokens")
            if not hosted:
                continue
            options = os.path.join(os.path.dirname(csv_path), row["model_id"], "options.json")
            try:
                with open(options, encoding="utf-8") as ofh:
                    entry = str(json.load(ofh).get("API_KEY", {}).get("default") or "").strip()
            except (OSError, ValueError):
                entry = ""
            if entry:
                entries[row["model_id"]] = entry
    return entries


def _regenerate(csv_path: str, variable: str, api_only: bool) -> str:
    """Re-emit one of the maps as source (developer utility)."""
    lines = [variable + " = {"]
    for model_id, entry in key_entries(csv_path, api_only).items():
        lines.append(f'    "{model_id}": "{entry}",')
    lines.append("}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    if "--from" in sys.argv:
        print(_regenerate(sys.argv[sys.argv.index("--from") + 1],
                          "API_KEY_ENTRIES", api_only=False))
    elif "--llm-from" in sys.argv:
        print(_regenerate(sys.argv[sys.argv.index("--llm-from") + 1],
                          "LLM_KEY_ENTRIES", api_only=True))
    else:
        print(__doc__)
