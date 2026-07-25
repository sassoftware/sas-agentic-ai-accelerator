# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Repository and definition-core path discovery.

The CLI is normally run from anywhere inside a clone of the
sas-agentic-ai-accelerator repository. The repo root is found by walking
upward until a directory containing ``LLM-Definitions`` is found; it can
always be overridden explicitly with ``--repo`` / ``MDB_REPO``.

The definition folders themselves can live OUTSIDE the accelerator clone:
``MDB_DEFINITIONS`` (an absolute path, typically set in the ``.env`` of your
own repository) names a root under which mdb keeps the familiar layout —
``LLM-Definitions/``, ``Embedding-Definitions/`` and the retire ``_archive/``
— creating the folders as needed. Your definitions can then be committed to
your own git repo while the accelerator clone only supplies the templates
(definition-core).
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
        "via MDB_DEFINITIONS, MDB_REPO must still point at the accelerator clone - it "
        "supplies the definition-core templates.)"
    )


def core_dir(repo_root: Path) -> Path:
    core = repo_root / "Model-Definition-Builder" / "definition-core"
    if not core.is_dir():
        raise RepoNotFoundError(f"definition-core not found at {core}.")
    return core


def _definitions_root(repo_root: Path) -> tuple[Path, bool]:
    """The root the definition folders live under, and whether it is the
    MDB_DEFINITIONS relocation (your own repository) or the accelerator clone."""
    value = (os.environ.get("MDB_DEFINITIONS") or "").strip()
    if value:
        return Path(value).expanduser().resolve(), True
    return repo_root, False


def definitions_dir(repo_root: Path, kind: str) -> Path:
    root, relocated = _definitions_root(repo_root)
    path = root / ("LLM-Definitions" if kind == "llm" else "Embedding-Definitions")
    if relocated:
        # Created on demand so pointing MDB_DEFINITIONS at a fresh directory
        # in your own repository just works on the first mdb run.
        path.mkdir(parents=True, exist_ok=True)
    return path


def archive_dir(repo_root: Path) -> Path:
    """Where `mdb retire` moves retired definitions. Git-ignored in the
    accelerator, so archiving a model takes it out of the tracked active set
    without losing a local copy; under an MDB_DEFINITIONS root, add
    ``_archive/`` to your own repo's .gitignore for the same behavior."""
    root, _ = _definitions_root(repo_root)
    return root / "_archive"


def fact_sheet_path(repo_root: Path, kind: str) -> Path:
    name = "llm_fact_sheet.csv" if kind == "llm" else "embedding_fact_sheet.csv"
    return definitions_dir(repo_root, kind) / name
