# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The export, the gate and the release procedure, as round 19 left them open.

  1. The user ids of an export. The exports SAS Viya 2026.09 made on
     2026-10-12 and 2026-10-13 carry a user id in `userName` of each of
     their folder bodies; the gate counted that field and the export did not
     rewrite it, so an export in that shape ends with "Nothing was written"
     unless its differences are allowed. The packages below are fabricated
     in the SHAPE of those exports - every id in them is made up.
  2. The commit id of release.json: an abbreviated id passed the gate.
  3. The release procedure on a `main` that only takes squash merges, run in
     real git repositories with the whole command `mdb package-check
     --release` - and the order that fails the tag.
  4. The last line of a release export, and the help texts.

The tests of 1, 2 and 4 failed on the code before its fixes (the one of the
fixture aside); those of 3 hold the procedure CONTRIBUTING.md describes to
what git and the gate do.
"""
import io
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console
from typer.testing import CliRunner

from content_fakes import CONTENT_ROOT, b64, make_repo, package_of
from mdb import cli
from mdb.core import content
from mdb.core.content import load_manifest
from mdb.core.packages import (
    NEUTRAL_USER, USER_FIELDS, _body, _report_wrapper, deflate, locate, named_users, neutralise_users,
    package_differences,
)
from test_package_guards import checking, exporting, manifest, repo  # noqa: F401 - the fixtures of the gate tests

REPO_ROOT = Path(__file__).resolve().parents[3]
STAMP = "2026-10-13T09:19:08.741Z"


# ---- 4: the last line of a release export, the help texts ----------------------


#: A sentence about what was not measured, tested or run - not the word "run" in what a command does.
NOT_RUN = re.compile(r"not (been )?(measured|tested)\b|untested|unmeasured|(has|have) not (been )?run\b"
                     r"|not run (in|against) SAS|never (been )?(run|measured|sent)\b", re.I)


def _help_texts(command, name: str) -> list:
    texts = [(name, " ".join((command.help or "").split()))]
    texts += [(f"{name} {param.name}", " ".join((getattr(param, "help", "") or "").split()))
              for param in command.params]
    for sub_name, sub in (getattr(command, "commands", None) or {}).items():
        texts += _help_texts(sub, f"{name} {sub_name}")
    return texts


def test_no_help_text_says_what_was_not_measured():
    """gate-19: `mdb package-export --help` ended with a sentence about what
    has not been measured. That is said in the changelog and the guide."""
    import typer
    texts = _help_texts(typer.main.get_command(cli.app), "mdb")
    assert len(texts) > 100 and any(name == "mdb package-export" for name, _ in texts)
    assert [f"{name}: {match.group(0)}" for name, text in texts for match in NOT_RUN.finditer(text)] == []
    assert NOT_RUN.search("What the transfer service does with neutral user fields has not been measured.")
    assert not NOT_RUN.search("Nothing is run. A step that is not run keeps its id.")


# ---- 2 and 3: real git repositories --------------------------------------------
GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="no git on this machine")


def _git(cwd, *args) -> str:
    done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c",
                           "init.defaultBranch=main", "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false",
                           "-c", "tag.gpgsign=false", *args], cwd=cwd, capture_output=True, text=True)
    assert done.returncode == 0, f"git {' '.join(args)}: {done.stderr}"
    return done.stdout.strip()


@pytest.fixture
def gate(monkeypatch):
    """`mdb package-check` in a checkout that is a real git repository - no
    commit function is replaced. Returns run(checkout, *args) -> (exit code,
    printed text)."""
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, width=1000, force_terminal=False))
    monkeypatch.setattr(cli, "_viya_session", lambda: pytest.fail("the check reads files and signs in nowhere"))

    def run(checkout: Path, *args):
        out.seek(0)
        out.truncate()
        monkeypatch.setattr(cli, "Context", lambda: SimpleNamespace(repo=checkout))
        result = CliRunner().invoke(cli.app, ["package-check", *args])
        assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
        return result.exit_code, out.getvalue()
    return run


def _deploy_and_export(checkout: Path) -> str:
    """What `mdb content-deploy` and `mdb package-export` leave in a checkout
    whose folder equals it: the package file, with a release.json that names
    the commit that is checked out. Returns that commit."""
    listed = load_manifest(checkout)
    listed.package_path.write_text(json.dumps(package_of(listed).build(), indent=2), encoding="utf-8", newline="\n")
    return content.checkout_commit(checkout)[0]


def _with_commit(package_path: Path, commit: str) -> None:
    """Write another commit into the release.json of a package file - a
    release.json edited by hand."""
    package = json.loads(package_path.read_text(encoding="utf-8"))
    for detail in package["transferDetails"]:
        obj = detail["transferObject"]
        if obj["summary"]["name"] == "release.json":
            body = _body(obj)
            info = json.loads(__import__("base64").b64decode(body["content"]))
            info["commit"] = commit
            body["content"] = b64(json.dumps(info, indent=2) + "\n")
            obj["content"] = b64(body)
    package_path.write_text(json.dumps(package, indent=2), encoding="utf-8", newline="\n")


@needs_git
def test_an_abbreviated_commit_id_is_no_commit_id(tmp_path, gate):
    """review-19 / gate-19: the check took 4 to 64 hexadecimal digits while
    its message said git names a commit with 40; `head[:7]` of an ancestor
    passed --release. Only an id in full is one."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    make_repo(checkout)
    _git(checkout, "init", "-q")
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-q", "-m", "first")
    head = _deploy_and_export(checkout)
    assert re.fullmatch(r"[0-9a-f]{40}", head)
    assert content.commit_standing(checkout, head) == content.ANCESTOR
    for text in (head[:4], head[:7], head[:39], head + "0", head.upper(), "HEAD", "main", ""):
        assert content.commit_standing(checkout, text) == content.NOT_A_COMMIT, text
        assert content.commit_standing(None, text) == content.NOT_A_COMMIT, text
    # 64 digits are the id of a SHA-256 repository: a commit id in full, which this clone does not hold
    assert content.commit_standing(checkout, "0" * 64) == content.NOT_HELD
    assert content.commit_standing(checkout, "0" * 40) == content.NOT_HELD
    package_path = load_manifest(checkout).package_path
    assert gate(checkout, "--release")[0] == 0
    _with_commit(package_path, head[:7])
    for form in ((), ("--release",)):                             # wrong whatever the checkout holds: both forms
        code, text = gate(checkout, *form)
        assert code == 1
        assert (f"SAS-Agentic-AI-Accelerator.json: file 'release.json' names the commit '{head[:7]}', which is no "
                "commit id in full (git writes one with 40 hexadecimal digits, 64 in a SHA-256 repository)\n") in text
    # without a checkout to ask, the form alone is judged - the same form
    listed = load_manifest(checkout)
    entry = listed.find("release.json", "file")
    expected = content.file_bytes(listed, entry)
    short = json.dumps({**json.loads(expected), "commit": head[:12]}).encode("utf-8")
    assert content.generated_findings(entry, expected, short) == (
        [f"names the commit '{head[:12]}', which is no commit id in full (git writes one with 40 hexadecimal "
         "digits, 64 in a SHA-256 repository)"], [])
    assert content.generated_findings(entry, expected, expected) == ([], [])
