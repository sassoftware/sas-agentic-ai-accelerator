# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""One name for a key, wherever it is written or read.

A model's key is looked up under the KeyName its `API_KEY` option references
(`AzureOpenAI`, not `Azure OpenAI`). Four things must spell it identically:
the two create-credential-domain scripts and mdb that WRITE the domain, and
the maps here that READ it. PR #31 found the Azure entry off by a space in the
writers; these tests keep all four aligned with the definitions themselves.
"""
import pathlib
import re

from rag_core import providers

RAG = pathlib.Path(__file__).resolve().parents[1]
REPO = RAG.parents[1]
OTHER = REPO / "SAS-Viya-Integrations" / "Other"
MDB_CREDENTIALS = REPO / "Model-Definition-Builder" / "cli" / "src" / "mdb" / "viya" / "credentials.py"


def _python_map(path: pathlib.Path, name: str) -> dict:
    block = re.search(name + r"\s*=\s*\{(.*?)\}", path.read_text(encoding="utf-8"), re.S).group(1)
    return dict(re.findall(r'"([A-Z_]+)":\s*"([^"]+)"', block))


def _powershell_map(path: pathlib.Path) -> dict:
    block = re.search(r"\$providerMap = \[ordered\]@\{(.*?)\}", path.read_text(encoding="utf-8"), re.S).group(1)
    return dict(re.findall(r"'([A-Z_]+)'\s*=\s*'([^']+)'", block))


def test_the_maps_are_what_the_definitions_say():
    assert providers.key_entries(
        str(REPO / "Embedding-Definitions" / "embedding_fact_sheet.csv"), api_only=False
    ) == providers.API_KEY_ENTRIES
    assert providers.key_entries(
        str(REPO / "LLM-Definitions" / "llm_fact_sheet.csv"), api_only=True
    ) == providers.LLM_KEY_ENTRIES


def test_the_three_writers_spell_every_entry_the_same():
    shell = _python_map(OTHER / "create-credential-domain.sh", "PROVIDER_MAP")
    powershell = _powershell_map(OTHER / "create-credential-domain.ps1")
    mdb = _python_map(MDB_CREDENTIALS, "PROVIDER_ENTRIES")
    assert shell and shell == powershell == mdb


def test_the_readers_only_ask_for_entries_a_writer_produces():
    written = set(_python_map(OTHER / "create-credential-domain.sh", "PROVIDER_MAP").values())
    asked = set(providers.API_KEY_ENTRIES.values()) | set(providers.LLM_KEY_ENTRIES.values())
    assert asked <= written


def test_the_renamed_entries():
    assert providers.llm_api_key_entry("gpt_4o_mini_az_2024_07_18") == "AzureOpenAI"
    assert providers.llm_api_key_entry("claude_haiku_4_5_bedrock") == "AWSBedrock"
    assert providers.api_key_entry("voyage_35") == "VoyageAI"
    assert providers.api_key_entry("titan_embed_text_v2") == "AWSBedrock"
    # a self-hosted scorer that takes an endpoint_url and no API_KEY needs no entry
    assert providers.llm_api_key_entry("llama_31_405b") == ""


def test_a_definition_switched_to_env_mode_leaves_the_map_when_it_is_regenerated(tmp_path):
    """mdb add --auth env: the container holds the key and options.json has no
    API_KEY. The static map still names the entry until it is regenerated, and
    the refusal says so; regenerated, the model needs no entry and gets ""."""
    import json

    import pytest

    sheet = tmp_path / "embedding_fact_sheet.csv"
    sheet.write_text("model_id,cost_type\ntext_embedding_3_small,Tokens\nvoyage_35,Tokens\n", encoding="utf-8")
    for model_id, options in (("text_embedding_3_small", {"Embedding_Length": {"default": 1536}}),
                              ("voyage_35", {"API_KEY": {"default": "VoyageAI"}})):
        (tmp_path / model_id).mkdir()
        (tmp_path / model_id / "options.json").write_text(json.dumps(options), encoding="utf-8")

    with pytest.raises(KeyError, match="regenerate the maps of rag_core/providers.py"):
        providers.api_key_for("text_embedding_3_small", {})
    regenerated = providers.key_entries(str(tmp_path / "embedding_fact_sheet.csv"), api_only=False)
    assert regenerated == {"voyage_35": "VoyageAI"}
    assert 'API_KEY_ENTRIES = {\n    "voyage_35": "VoyageAI",\n}' == providers._regenerate(
        str(sheet), "API_KEY_ENTRIES", api_only=False)
    original = dict(providers.API_KEY_ENTRIES)
    try:
        providers.API_KEY_ENTRIES.clear()
        providers.API_KEY_ENTRIES.update(regenerated)
        assert providers.api_key_for("text_embedding_3_small", {}) == ""
    finally:
        providers.API_KEY_ENTRIES.clear()
        providers.API_KEY_ENTRIES.update(original)
