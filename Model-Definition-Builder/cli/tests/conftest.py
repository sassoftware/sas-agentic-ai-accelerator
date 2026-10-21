# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import os
import time
from pathlib import Path

#: typer (0.27.3, `typer.rich_utils`, read once at its import) forces a terminal - colours, and the help in
#: boxes of the terminal's width - where one of these is set, and GitHub Actions sets GITHUB_ACTIONS: the tests
#: that read the help or an error of mdb then found `\x1b[1m...` in place of its text (PR #48, 3.11 and 3.12;
#: on Windows without these the same tests passed). The tests read what a pipe gets, so the session drops them
#: before anything imports typer; the processes the tests start inherit that.
for _name in ("GITHUB_ACTIONS", "FORCE_COLOR", "PY_COLORS"):
    os.environ.pop(_name, None)

import pytest
import yaml

from mdb.core import content
from mdb.core.generator import CoreAssets
from mdb.core.paths import core_dir, fact_sheet_path

REPO_ROOT = Path(__file__).resolve().parents[3]

#: The seconds a test waits for a process it starts - mdb, a shell, a deploy script, git - before it fails. A
#: full run of this suite on a machine under other work took 4098 s (review-22a), where a run of the same commit
#: took 258 s (fix-mdb-22), about 16 times as long; in it a test that started mdb with a limit of 120 s failed,
#: and its trace was not kept. The limit is there for a process that hangs, not to time one that is slow.
PROCESS_TIMEOUT = 1800


def bash_candidates() -> list:
    """The bash programs a test that runs a shell script may take, in order: the first `bash` of the PATH, then on
    Windows the Git Bash next to the git of the PATH. On Windows the first `bash` of a PowerShell or cmd session is
    commonly the launcher of the Windows Subsystem for Linux (...\\WindowsApps\\bash.exe), which knows no `C:/...`
    path and hands Linux no variable of the Windows environment; Git Bash puts its own bash first on the PATH, so a
    test run from Git Bash and one run from PowerShell found different programs (round-29 review). Each test asks
    a candidate for what it needs before it takes it."""
    import os
    import shutil
    candidates = [shutil.which("bash")]
    git = shutil.which("git")
    if os.name == "nt" and git:
        for folder in Path(git).resolve().parents:
            candidates += [folder / "bin" / "bash.exe", folder / "usr" / "bin" / "bash.exe"]
    return [str(candidate) for candidate in candidates if candidate is not None and Path(candidate).is_file()]

#: The folder of the checkout a build writes that the package manifest names a file in: `npm run build` in
#: LLM-Prompt-Builder (vite, `outDir: 'dist'`) empties it and writes it again about two seconds later. A test
#: that loads the package manifest in that time fails with `form 'LLM-Prompt-Builder/dist/index.html' does not
#: exist in the repository`: review-23a, with such a build repeated in the tree, saw the hint test of test_mdb_21
#: fail so in 2 runs of 3 - the test that failed once in round 22 and once in review-23a, both times without a
#: trace that was kept. In a copy of 36124b8 without the bundle, 61 tests of this suite failed.
BUILT = "LLM-Prompt-Builder/dist/"
#: How long the session waits at its start for a file of BUILT that a build has just taken away.
SETTLE_SECONDS = 60


def _settled(path: Path):
    """The bytes of `path` once two reads half a second apart agree - a
    build that writes it is done -, or None where it is not there for
    SETTLE_SECONDS."""
    deadline, last = time.monotonic() + SETTLE_SECONDS, None
    while True:
        try:
            data = path.read_bytes()
        except OSError:
            data = None
        if data is not None and data == last:
            return data
        if time.monotonic() > deadline:
            return None
        last = data
        time.sleep(0.5)


def built_files(repo_root: Path) -> list:
    """Every file of BUILT the package manifest of `repo_root` names."""
    document = yaml.safe_load(content.manifest_file(repo_root).read_text(encoding="utf-8")) or {}
    return sorted({value for entry in document.get("entries") or [] if isinstance(entry, dict)
                   for value in (entry.get("source"), entry.get("code"), entry.get("form"))
                   if isinstance(value, str) and value.startswith(BUILT)})


@pytest.fixture(scope="session", autouse=True)
def built_files_held(tmp_path_factory):
    """The files of BUILT that the package manifest names, as they were at
    the start of the session: for those, the file of the checkout that mdb
    reads (`content.repository_file`) is the held copy, so a build of the
    checkout while the suite runs changes nothing a test reads. A file that
    is not there at the start is not held: the tests read the checkout as it
    is. Yields {path in the manifest: held copy}."""
    held_root, held = tmp_path_factory.mktemp("built"), {}
    for relative in built_files(REPO_ROOT):
        data = _settled(content.repository_file(REPO_ROOT, relative))
        if data is not None:
            copy = content.repository_file(held_root, relative)
            copy.parent.mkdir(parents=True, exist_ok=True)
            copy.write_bytes(data)
            held[relative] = copy
    checkout_file = content.repository_file

    def held_or_checkout(repo_root, relative: str) -> Path:
        if relative in held and Path(repo_root).resolve() == REPO_ROOT:
            return held[relative]
        return checkout_file(repo_root, relative)
    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(content, "repository_file", held_or_checkout)
        yield held


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
