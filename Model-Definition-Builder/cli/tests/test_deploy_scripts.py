# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""deploy-rag-content.ps1 / .sh, run for real with a stand-in for mdb.

The two scripts hand everything to `mdb content-deploy --only RAG --to`.
What they do themselves is start mdb and pass its exit code on - and that is
where the pass of 2026-10-12 saw the PowerShell script fail: in Windows
PowerShell 5.1, with the error stream of the script redirected (`*>` or
`2>&1`), the first line mdb wrote to stderr - urllib3's
InsecureRequestWarning, with SAS_VIYA_VERIFY_SSL=false - became a
NativeCommandError, and `$ErrorActionPreference = 'Stop'` ended the script
before mdb's own lines and exit code.

The stand-in prints a warning to stderr, one line to stdout and the
arguments it was given, and ends with the exit code MDB_STANDIN_EXIT names.
A test that needs a shell this machine lacks is skipped.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

FOLDER = Path(__file__).resolve().parents[3] / "SAS-Viya-Integrations" / "Other"
POWERSHELL = shutil.which("powershell.exe") if os.name == "nt" else None


def _bash_that_reads_this_checkout():
    """The bash on the PATH, where it finds the script at the path the test
    hands it. On Windows the first `bash` of a PowerShell or cmd session is
    commonly the launcher of the Windows Subsystem for Linux, which knows no
    `C:/...` path: with it the script is `No such file or directory`, which
    says nothing about the script. Git Bash reads the path."""
    bash = shutil.which("bash")
    if bash is None:
        return None
    try:
        seen = subprocess.run([bash, "-c", f'test -f "{(FOLDER / "deploy-rag-content.sh").as_posix()}"'],
                              capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return bash if seen.returncode == 0 else None


BASH = _bash_that_reads_this_checkout()
WARNING = "InsecureRequestWarning: Unverified HTTPS request"


def _standin(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "mdb.cmd").write_text(
        "@echo off\r\n"
        f"echo connectionpool.py:1100: {WARNING} 1>&2\r\n"
        "echo args: %*\r\n"
        "echo 41 entries: 41 equal.\r\n"
        "exit /b %MDB_STANDIN_EXIT%\r\n", encoding="ascii")
    script = folder / "mdb"
    script.write_bytes(
        b"#!/usr/bin/env bash\n"
        + f"echo 'connectionpool.py:1100: {WARNING}' >&2\n".encode("ascii")
        + b'echo "args: $*"\n'
        + b"echo '41 entries: 41 equal.'\n"
        + b'exit "${MDB_STANDIN_EXIT:-0}"\n')
    script.chmod(0o755)
    return folder


@pytest.mark.skipif(POWERSHELL is None, reason="no Windows PowerShell 5.1 on this machine")
@pytest.mark.parametrize("code", [0, 1])
def test_a_warning_on_stderr_does_not_stop_the_powershell_script(tmp_path, code):
    standin = _standin(tmp_path / "bin")
    out = tmp_path / "out.txt"
    runner = tmp_path / "run.ps1"
    # the caller redirects every stream of the script, as the pass did
    runner.write_text(
        f"$env:PATH = '{standin}' + ';' + $env:PATH\n"
        f"$env:MDB_STANDIN_EXIT = '{code}'\n"
        f"& '{FOLDER / 'deploy-rag-content.ps1'}' -ContentRoot '/Team/Retrieval' -DryRun *> '{out}'\n"
        "exit $LASTEXITCODE\n", encoding="utf-8")
    done = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner)],
                          capture_output=True, text=True, timeout=120)
    text = out.read_text(encoding="utf-16") if out.read_bytes()[:2] == b"\xff\xfe" else out.read_text()
    assert done.returncode == code, (done.stdout, done.stderr, text)
    # mdb ran to its end: its warning, its own line, and the arguments the script gave it
    assert WARNING in text and "41 entries: 41 equal." in text
    assert "args: content-deploy --only RAG --to /Team/Retrieval --dry-run" in text


@pytest.mark.skipif(POWERSHELL is None, reason="no Windows PowerShell 5.1 on this machine")
def test_the_powershell_script_passes_create_on(tmp_path):
    standin = _standin(tmp_path / "bin")
    env = {**os.environ, "PATH": f"{standin}{os.pathsep}{os.environ['PATH']}", "MDB_STANDIN_EXIT": "0"}
    done = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(FOLDER / "deploy-rag-content.ps1"), "-ContentRoot", "/Team/New", "-Create"],
                          capture_output=True, text=True, timeout=120, env=env)
    assert done.returncode == 0, done.stderr
    assert "args: content-deploy --only RAG --to /Team/New --create" in done.stdout


@pytest.mark.skipif(BASH is None, reason="no bash on this machine that reads the path of this checkout")
@pytest.mark.parametrize("code", [0, 1])
def test_a_warning_on_stderr_does_not_stop_the_bash_script(tmp_path, code):
    """`set -euo pipefail` looks at exit codes, not at stderr: the warning is
    passed on and the exit code is mdb's - also with stderr into stdout."""
    standin = _standin(tmp_path / "bin")
    script = (FOLDER / "deploy-rag-content.sh").as_posix()
    bin_dir = standin.as_posix()
    if os.name == "nt" and len(bin_dir) > 1 and bin_dir[1] == ":":
        bin_dir = f"/{bin_dir[0].lower()}{bin_dir[2:]}"                # Git Bash spells C:/x as /c/x
    command = (f'export PATH="{bin_dir}:$PATH" MDB_STANDIN_EXIT={code}; '
               f'bash "{script}" -r "/Team/Retrieval" -c -n 2>&1')
    done = subprocess.run([BASH, "-c", command], capture_output=True, text=True, timeout=120)
    assert done.returncode == code, (done.stdout, done.stderr)
    assert WARNING in done.stdout and "41 entries: 41 equal." in done.stdout
    assert "args: content-deploy --only RAG --to /Team/Retrieval --create --dry-run" in done.stdout


def test_both_scripts_pass_create_only_when_asked():
    ps1 = (FOLDER / "deploy-rag-content.ps1").read_text(encoding="utf-8")
    sh = (FOLDER / "deploy-rag-content.sh").read_text(encoding="utf-8")
    assert "if ($Create) { $arguments += '--create' }" in ps1
    assert 'if [ -n "$CREATE" ]; then ARGS+=(--create); fi' in sh
    # the native call runs with 'Continue', so a stderr line is no terminating error
    assert ps1.index("$ErrorActionPreference = 'Continue'") < ps1.index("& mdb @arguments")
