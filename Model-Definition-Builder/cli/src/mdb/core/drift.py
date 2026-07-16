# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Drift tracking via a per-folder sidecar lockfile.

One mechanism for every file type: the lockfile records the sha256 of each
generated file, so committed bytes always equal registered bytes (no stamps
are injected into JSON). ``classify`` distinguishes "the manifest/template
changed, regenerate" from "someone hand-edited a generated file".
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .. import __version__

LOCK_FILENAME = ".mdb-lock.json"


class FileStatus(str, Enum):
    NEW = "new"                  # not on disk yet
    UNCHANGED = "unchanged"      # on disk == fresh render
    STALE = "stale"              # on disk != render, but matches the lock -> safe to regenerate
    HAND_EDITED = "hand-edited"  # on disk != render and != lock -> refuses without --force
    UNTRACKED = "untracked"      # on disk != render and no lock entry exists


@dataclass
class Classification:
    status: FileStatus
    filename: str


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_lock(folder: Path) -> dict | None:
    lock_path = folder / LOCK_FILENAME
    if not lock_path.is_file():
        return None
    try:
        return json.loads(lock_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def write_lock(folder: Path, manifest_bytes: bytes, files: dict[str, bytes]) -> None:
    lock = {
        "generator": f"mdb {__version__}",
        "manifest_sha256": _sha256(manifest_bytes),
        "files": {name: _sha256(content) for name, content in sorted(files.items())},
    }
    (folder / LOCK_FILENAME).write_bytes(
        (json.dumps(lock, indent=4, ensure_ascii=False) + "\n").encode("utf-8")
    )


def classify(folder: Path, rendered: dict[str, bytes]) -> list[Classification]:
    lock = load_lock(folder) or {}
    locked_hashes: dict[str, str] = lock.get("files", {})
    results: list[Classification] = []
    for name, fresh in sorted(rendered.items()):
        on_disk_path = folder / name
        if not on_disk_path.is_file():
            results.append(Classification(FileStatus.NEW, name))
            continue
        on_disk = on_disk_path.read_bytes().replace(b"\r\n", b"\n")
        if on_disk == fresh:
            results.append(Classification(FileStatus.UNCHANGED, name))
        elif locked_hashes.get(name) == _sha256(on_disk):
            results.append(Classification(FileStatus.STALE, name))
        elif name in locked_hashes:
            results.append(Classification(FileStatus.HAND_EDITED, name))
        else:
            results.append(Classification(FileStatus.UNTRACKED, name))
    return results
