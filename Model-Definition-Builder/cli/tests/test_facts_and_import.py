# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Fact-sheet upsert semantics and the legacy-folder import round-trip."""
import shutil

from mdb.core.facts import read_row, upsert_row
from mdb.core.importer import import_folder


def test_upsert_preserves_legacy_rows(tmp_path, repo_root, fact_sheet, core):
    working_copy = tmp_path / "llm_fact_sheet.csv"
    shutil.copy(fact_sheet, working_copy)
    before = working_copy.read_text(encoding="utf-8")

    result = import_folder(repo_root / "LLM-Definitions" / "claude_sonnet_3_5", fact_sheet)
    outcome = upsert_row(working_copy, result.manifest)
    after = working_copy.read_text(encoding="utf-8")

    # Only the claude_sonnet_3_5 line may differ; every other byte is preserved
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    assert len(before_lines) == len(after_lines)
    for old, new in zip(before_lines, after_lines):
        if old.startswith('"claude_sonnet_3_5"'):
            continue
        assert old == new
    assert outcome in ("updated", "unchanged")

    # Idempotency: a second upsert changes nothing
    assert upsert_row(working_copy, result.manifest) == "unchanged"


def test_import_claude_sonnet_3_5(repo_root, fact_sheet):
    result = import_folder(repo_root / "LLM-Definitions" / "claude_sonnet_3_5", fact_sheet)
    manifest = result.manifest
    assert manifest.provider.adapter == "anthropic"
    assert manifest.runtime.template == "anthropic_messages"
    assert manifest.provider.model_version == "claude-3-5-sonnet-20240620"
    assert manifest.provider.auth.key_name == "Anthropic"
    assert manifest.tags.as_list()[:4] == ["LLM", "Proprietary", "Anthropic", "small"]
    assert manifest.metadata.pricing.input_token_price == 3e-06
    # the existing PDF model card is preserved as an override
    assert "Model-Card.md" in manifest.generation.overrides


def test_import_qwen_hf(repo_root, fact_sheet):
    result = import_folder(repo_root / "LLM-Definitions" / "qwen_25_05b", fact_sheet)
    manifest = result.manifest
    assert manifest.provider.adapter == "hf-selfhosted"
    assert manifest.runtime.template == "hf_transformers"
    assert manifest.provider.params["hf"]["repo"] == "Qwen/Qwen2.5-0.5B-Instruct"
    assert manifest.metadata.deployment_type == "SCR"
    assert manifest.provider.auth.mode == "none"


def test_import_openai(repo_root, fact_sheet):
    result = import_folder(repo_root / "LLM-Definitions" / "gpt_4o_mini_2024_07_18", fact_sheet)
    manifest = result.manifest
    assert manifest.provider.adapter == "openai"
    assert manifest.runtime.template == "openai_chat"
    assert manifest.provider.model_version == "gpt-4o-mini-2024-07-18"
    assert read_row(fact_sheet, "gpt_4o_mini_2024_07_18") is not None
