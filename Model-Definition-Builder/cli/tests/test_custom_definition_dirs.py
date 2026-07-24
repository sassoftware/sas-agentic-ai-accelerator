# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""MDB_DEFINITIONS relocates the definition folders into a single root (e.g.
the user's own git repository) under which mdb keeps the familiar layout —
LLM-Definitions/, Embedding-Definitions/, _archive/ — creating folders as
needed. Nothing changes when the variable is unset."""
from pathlib import Path

from mdb.core.paths import archive_dir, definitions_dir, fact_sheet_path


def test_defaults_without_override(tmp_path, monkeypatch):
    monkeypatch.delenv("MDB_DEFINITIONS", raising=False)
    repo = tmp_path / "accelerator"
    assert definitions_dir(repo, "llm") == repo / "LLM-Definitions"
    assert definitions_dir(repo, "embedding") == repo / "Embedding-Definitions"
    assert fact_sheet_path(repo, "llm") == repo / "LLM-Definitions" / "llm_fact_sheet.csv"
    assert archive_dir(repo) == repo / "_archive"


def test_root_relocates_the_accelerator_layout(tmp_path, monkeypatch):
    own_repo = tmp_path / "my-repo"
    monkeypatch.setenv("MDB_DEFINITIONS", str(own_repo))
    repo = tmp_path / "accelerator"

    llm = definitions_dir(repo, "llm")
    emb = definitions_dir(repo, "embedding")
    assert llm == own_repo.resolve() / "LLM-Definitions"
    assert emb == own_repo.resolve() / "Embedding-Definitions"
    # created on demand so a fresh directory in the user's repo just works
    assert llm.is_dir() and emb.is_dir()
    # fact sheets and the retire archive follow the root
    assert fact_sheet_path(repo, "llm") == llm / "llm_fact_sheet.csv"
    assert fact_sheet_path(repo, "embedding") == emb / "embedding_fact_sheet.csv"
    assert archive_dir(repo) == own_repo.resolve() / "_archive"


def test_blank_override_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("MDB_DEFINITIONS", "   ")
    repo = tmp_path / "accelerator"
    assert definitions_dir(repo, "llm") == repo / "LLM-Definitions"
    assert archive_dir(repo) == repo / "_archive"
