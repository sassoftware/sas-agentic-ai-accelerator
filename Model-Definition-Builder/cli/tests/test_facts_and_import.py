# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Fact-sheet upsert semantics and the legacy-folder import round-trip."""
import shutil

from mdb.core.facts import read_row, remove_row, upsert_row
from mdb.core.importer import import_folder


def test_remove_row_deletes_only_the_target(tmp_path, repo_root, fact_sheet):
    working_copy = tmp_path / "llm_fact_sheet.csv"
    shutil.copy(fact_sheet, working_copy)
    before = working_copy.read_text(encoding="utf-8")
    assert read_row(working_copy, "gemini_flash_25") is not None

    assert remove_row(working_copy, "gemini_flash_25") == "removed"
    assert read_row(working_copy, "gemini_flash_25") is None
    # exactly one line gone; every surviving line is byte-identical
    before_lines = [l for l in before.splitlines() if not l.startswith('"gemini_flash_25"')]
    after_lines = working_copy.read_text(encoding="utf-8").splitlines()
    assert before_lines == after_lines
    # removing an absent model is a no-op
    assert remove_row(working_copy, "gemini_flash_25") == "absent"


def test_upsert_preserves_legacy_rows(tmp_path, repo_root, fact_sheet, core):
    from mdb.core.manifest import load_manifest
    working_copy = tmp_path / "llm_fact_sheet.csv"
    shutil.copy(fact_sheet, working_copy)
    before = working_copy.read_text(encoding="utf-8")

    manifest = load_manifest(repo_root / "LLM-Definitions" / "gemini_flash_25")
    outcome = upsert_row(working_copy, manifest)
    after = working_copy.read_text(encoding="utf-8")

    # Only the gemini_flash_25 line may differ; every other byte is preserved
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    assert len(before_lines) == len(after_lines)
    for old, new in zip(before_lines, after_lines):
        if old.startswith('"gemini_flash_25"'):
            continue
        assert old == new
    assert outcome in ("updated", "unchanged")

    # Idempotency: a second upsert changes nothing
    assert upsert_row(working_copy, manifest) == "unchanged"


LEGACY_SCORE = """import requests
modelVersion = 'claude-3-5-sonnet-20240620'
modelEndpoint = 'https://api.anthropic.com/v1/messages'
def scoreModel(userPrompt, systemPrompt, options):
    pass
"""
LEGACY_CONFIG = {
    "name": "claude_test_legacy", "scoreCodeFile": "claudeTestScore.py",
    "description": "Legacy test.", "toolVersion": "3.11-5", "modeler": "tester",
    "tags": ["LLM", "Proprietary", "Anthropic", "small"],
}
LEGACY_OPTIONS = {
    "temperature": {"default": 1, "range": "0 - 2", "description": "t"},
    "API_KEY": {"default": "Anthropic", "range": "sk-****", "description": "k"},
}


def test_import_legacy_anthropic_shape(tmp_path, fact_sheet):
    """Importer semantics against a synthesized legacy folder (the real fleet
    is fully migrated; historical folders live in git history)."""
    import json
    folder = tmp_path / "claude_test_legacy"
    folder.mkdir()
    (folder / "claudeTestScore.py").write_text(LEGACY_SCORE, encoding="utf-8")
    (folder / "modelConfiguration.json").write_text(json.dumps(LEGACY_CONFIG), encoding="utf-8")
    (folder / "options.json").write_text(json.dumps(LEGACY_OPTIONS), encoding="utf-8")
    (folder / "requirements.json").write_text("[]", encoding="utf-8")
    (folder / "Model-Card.pdf").write_bytes(b"%PDF-1.4 test")

    result = import_folder(folder, fact_sheet)
    manifest = result.manifest
    assert manifest.provider.adapter == "anthropic"
    assert manifest.runtime.template == "anthropic_messages"
    assert manifest.provider.model_version == "claude-3-5-sonnet-20240620"
    assert manifest.provider.auth.key_name == "Anthropic"
    assert manifest.tags.as_list()[:4] == ["LLM", "Proprietary", "Anthropic", "small"]
    # legacy score filename is preserved, PDF model card stays authoritative
    assert manifest.generation.score_code_file == "claudeTestScore.py"
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
