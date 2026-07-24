# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""MDB_LLM_DEFINITIONS / MDB_EMBEDDING_DEFINITIONS relocate the definition
folders (e.g. into the user's own git repository); fact sheets and the retire
archive follow, and nothing changes when the variables are unset."""
from pathlib import Path

from mdb.core.paths import archive_dir, definitions_dir, fact_sheet_path


def test_defaults_without_overrides(tmp_path, monkeypatch):
    monkeypatch.delenv("MDB_LLM_DEFINITIONS", raising=False)
    monkeypatch.delenv("MDB_EMBEDDING_DEFINITIONS", raising=False)
    repo = tmp_path / "accelerator"
    assert definitions_dir(repo, "llm") == repo / "LLM-Definitions"
    assert definitions_dir(repo, "embedding") == repo / "Embedding-Definitions"
    assert fact_sheet_path(repo, "llm") == repo / "LLM-Definitions" / "llm_fact_sheet.csv"
    assert archive_dir(repo) == repo / "_archive"
    assert archive_dir(repo, "llm") == repo / "_archive"


def test_llm_override_relocates_dir_sheet_and_archive(tmp_path, monkeypatch):
    own_repo = tmp_path / "my-repo" / "definitions"
    monkeypatch.setenv("MDB_LLM_DEFINITIONS", str(own_repo))
    monkeypatch.delenv("MDB_EMBEDDING_DEFINITIONS", raising=False)
    repo = tmp_path / "accelerator"

    resolved = definitions_dir(repo, "llm")
    assert resolved == own_repo.resolve()
    # created on demand so a fresh directory in the user's repo just works
    assert resolved.is_dir()
    assert fact_sheet_path(repo, "llm") == own_repo.resolve() / "llm_fact_sheet.csv"
    assert archive_dir(repo, "llm") == own_repo.resolve() / "_archive"
    # the other kind stays in the accelerator clone
    assert definitions_dir(repo, "embedding") == repo / "Embedding-Definitions"
    # kind-less archive (no relocation context) stays repo-based
    assert archive_dir(repo) == repo / "_archive"


def test_overrides_are_independent_per_kind(tmp_path, monkeypatch):
    llm_dir = tmp_path / "a" / "llm-defs"
    emb_dir = tmp_path / "b" / "emb-defs"
    monkeypatch.setenv("MDB_LLM_DEFINITIONS", str(llm_dir))
    monkeypatch.setenv("MDB_EMBEDDING_DEFINITIONS", str(emb_dir))
    repo = tmp_path / "accelerator"
    assert definitions_dir(repo, "llm") == llm_dir.resolve()
    assert definitions_dir(repo, "embedding") == emb_dir.resolve()
    assert fact_sheet_path(repo, "embedding") == emb_dir.resolve() / "embedding_fact_sheet.csv"


def test_blank_override_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("MDB_LLM_DEFINITIONS", "   ")
    repo = tmp_path / "accelerator"
    assert definitions_dir(repo, "llm") == repo / "LLM-Definitions"
