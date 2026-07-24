# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Repository and definition-core path discovery.

The CLI is normally run from anywhere inside a clone of the
sas-agentic-ai-accelerator repository. The repo root is found by walking
upward until a directory containing ``LLM-Definitions`` is found; it can
always be overridden explicitly with ``--repo`` / ``MDB_REPO``.

The definition folders themselves can live OUTSIDE the accelerator clone:
``MDB_LLM_DEFINITIONS`` and ``MDB_EMBEDDING_DEFINITIONS`` (absolute paths,
typically set in the ``.env`` of your own repository) relocate them, so your
definitions can be committed to your own git repo while the accelerator clone
only supplies the templates (definition-core). Fact sheets and the retire
archive follow the relocated folders.
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
        "or set MDB_REPO / pass --repo. (When your definitions live in your own repository "
        "via MDB_LLM_DEFINITIONS / MDB_EMBEDDING_DEFINITIONS, MDB_REPO must still point at "
        "the accelerator clone - it supplies the definition-core templates.)"
    )


def core_dir(repo_root: Path) -> Path:
    core = repo_root / "Model-Definition-Builder" / "definition-core"
    if not core.is_dir():
        raise RepoNotFoundError(f"definition-core not found at {core}.")
    return core


def _definitions_override(kind: str) -> Path | None:
    """The MDB_LLM_DEFINITIONS / MDB_EMBEDDING_DEFINITIONS relocation, if set.

    Created on demand so pointing the variable at a fresh directory in your
    own repository just works on the first ``mdb`` run.
    """
    variable = "MDB_LLM_DEFINITIONS" if kind == "llm" else "MDB_EMBEDDING_DEFINITIONS"
    value = (os.environ.get(variable) or "").strip()
    if not value:
        return None
    path = Path(value).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def definitions_dir(repo_root: Path, kind: str) -> Path:
    override = _definitions_override(kind)
    if override is not None:
        return override
    return repo_root / ("LLM-Definitions" if kind == "llm" else "Embedding-Definitions")


def archive_dir(repo_root: Path, kind: str | None = None) -> Path:
    """Where `mdb retire` moves retired definitions. Git-ignored in the
    accelerator, so archiving a model takes it out of the tracked active set
    without losing a local copy. When the kind's definition folder is
    relocated, the archive lives inside it (add ``_archive/`` to your own
    repo's .gitignore for the same behavior)."""
    if kind is not None:
        override = _definitions_override(kind)
        if override is not None:
            return override / "_archive"
    return repo_root / "_archive"


def fact_sheet_path(repo_root: Path, kind: str) -> Path:
    name = "llm_fact_sheet.csv" if kind == "llm" else "embedding_fact_sheet.csv"
    return definitions_dir(repo_root, kind) / name
