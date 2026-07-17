# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Pure (no-network) parts of the register/publish path: attribute enrichment
and the content manifest with its load-bearing roles."""
from mdb.core.manifest import load_manifest
from mdb.viya.registry import build_model_attributes, content_files


def test_attributes_enrichment_matches_register_script(repo_root, fact_sheet):
    folder = repo_root / "LLM-Definitions" / "gpt_41_mini"
    manifest = load_manifest(folder)
    from mdb.core.facts import read_row
    row = read_row(fact_sheet, "gpt_41_mini")
    attrs = build_model_attributes(manifest, folder, row, "https://viya-host/llm")
    assert attrs["name"] == "gpt_41_mini"
    assert attrs["endPoint"] == "https://viya-host/llm/gpt_41_mini/gpt_41_mini"
    assert attrs["llmModelType"] == "GPT"
    assert attrs["provider"] == "OpenAI"
    # (input 0.0000004 + output 0.0000016) / 2
    assert abs(attrs["costPerCall"] - 1e-06) < 1e-12
    assert attrs["toolVersion"] == "3.11"


def test_seconds_cost_type(repo_root):
    folder = repo_root / "Embedding-Definitions" / "titan_embed_text_v2"
    manifest = load_manifest(folder)
    attrs = build_model_attributes(
        manifest, folder, {"cost_type": "Seconds", "second_cost": "0.00004", "provider": "AWS Bedrock"},
        "https://viya-host/llm",
    )
    assert attrs["costPerCall"] == 0.00004


def test_content_files_roles(repo_root):
    folder = repo_root / "LLM-Definitions" / "gpt_5_mini"
    manifest = load_manifest(folder)
    files = content_files(manifest, folder)
    by_name = {name: role for _, name, role in files}
    # load-bearing conventions from register-LLMs.py
    assert by_name["gpt_5_mini.py"] == "score"
    assert by_name["requirements.json"] == "python pickle"
    assert by_name["options.json"] == "documentation"
    assert by_name["outputVar.json"] is None and by_name["inputVar.json"] is None
    # registered models carry their manifest for the CLI/web round-trip
    assert by_name["definition.yaml"] == "documentation"
    assert by_name["Model-Card.md"] == "documentation"
    # every listed file exists on disk
    assert all(path.is_file() for path, _, _ in files)
