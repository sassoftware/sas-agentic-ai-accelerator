# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

import pytest

from mdb.core.generator import CoreAssets
from mdb.core.paths import core_dir, fact_sheet_path

REPO_ROOT = Path(__file__).resolve().parents[3]

#: The seconds a test waits for a process it starts - mdb, a shell, a deploy script - before it fails. A
#: machine that other work slows down a hundredfold took more than 120 s to start mdb (review-22a); the
#: limit is there for a process that hangs, not to time one that is slow.
PROCESS_TIMEOUT = 1800


@pytest.fixture(scope="session")
def repo_root() -> Path:
    assert (REPO_ROOT / "LLM-Definitions").is_dir(), "tests must run inside the accelerator repo"
    return REPO_ROOT


@pytest.fixture(scope="session")
def core(repo_root: Path) -> CoreAssets:
    return CoreAssets.load(core_dir(repo_root))


@pytest.fixture(scope="session")
def fact_sheet(repo_root: Path) -> Path:
    return fact_sheet_path(repo_root, "llm")
