# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Repository and definition-core path discovery.

The CLI is normally run from anywhere inside a clone of the
sas-agentic-ai-accelerator repository. The repo root is found by walking
upward until a directory containing ``LLM-Definitions`` is found; it can
always be overridden explicitly with ``--repo`` / ``MDB_REPO``.
"""
from __future__ import annotations

import os
from pathlib import Path


class RepoNotFoundError(RuntimeError):
    pass


def find_repo_root(start: Path | None = None) -> Path:
    override = os.environ.get("MDB_REPO")
    if override:
        root = Path(override).resolve()
        if not (root / "LLM-Definitions").is_dir():
            raise RepoNotFoundError(
                f"MDB_REPO points to {root}, but no LLM-Definitions directory was found there."
            )
        return root
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "LLM-Definitions").is_dir():
            return candidate
    raise RepoNotFoundError(
        "Could not find the repository root (no LLM-Definitions directory in the current "
        "directory or any parent). Run mdb from inside the sas-agentic-ai-accelerator clone, "
        "or set MDB_REPO / pass --repo."
    )


def core_dir(repo_root: Path) -> Path:
    core = repo_root / "Model-Definition-Builder" / "definition-core"
    if not core.is_dir():
        raise RepoNotFoundError(f"definition-core not found at {core}.")
    return core


def definitions_dir(repo_root: Path, kind: str) -> Path:
    return repo_root / ("LLM-Definitions" if kind == "llm" else "Embedding-Definitions")


def fact_sheet_path(repo_root: Path, kind: str) -> Path:
    if kind == "llm":
        return repo_root / "LLM-Definitions" / "llm_fact_sheet.csv"
    return repo_root / "Embedding-Definitions" / "embedding_fact_sheet.csv"
