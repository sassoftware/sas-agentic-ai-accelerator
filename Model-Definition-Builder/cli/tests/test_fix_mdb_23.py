# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What the gate and the review of round 23 found in mdb at 36124b8, each as
a class.

- An exception that ends a program of Model-Definition-Builder printed its
  text as it is - the body of an answer of SAS Viya that sasctl raises as an
  HTTPError, what was typed in a usage error of click - ESC and all. The
  tests take every program (every `__main__` and console script below
  Model-Definition-Builder), every command that reaches SAS Viya, every way
  in, and every exception click shows. Nothing signs in anywhere: the
  transport of requests is replaced.
- M4: a build of the checkout took away a file the package manifest names
  while the suite read it. The tests take every place content.py makes a
  path of the checkout, and every file the manifest names that a build of
  the checkout writes."""
import ast
import io
import json
import re
import sys
import urllib.error
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import conftest
import pytest
import requests
import typer

from mdb import cli
from mdb.core import content
from mdb.core.printable import CONTROL
from test_mdb_23 import (  # noqa: F401  (audit_script is a fixture)
    AUDIT_SCRIPT, MDB_ROOT, VIYA, _arguments, _python_files, _reaching_sas_viya, _sign_in_env, audit_script,
)

#: What a server answered: a bracket of rich's markup, a backslash, a CR LF, an ESC sequence and a C1 CSI byte.
BODY = 'bad [/red] \\ back\r\nline two \x1b[31mRED\x1b[0m \x9b2J end'
#: The same as every line of mdb prints it.
SHOWN = "bad [/red] \\ back\nline two [31mRED[0m 2J end"


def _controls(text: str) -> list:
    """The control characters of `text` that printable() takes out: every one but the line feed and the tab."""
    return sorted({f"U+{ord(c):04X}" for c in text if CONTROL.match(c)})


@pytest.fixture
def no_dotenv(monkeypatch):
    """main() loads the .env of the folders above the working folder: the folders above a worktree hold the
    real one."""
    monkeypatch.setitem(sys.modules, "dotenv", SimpleNamespace(find_dotenv=lambda **_: "",
                                                               load_dotenv=lambda *_, **__: False))


def _main(monkeypatch, capsys, *argv) -> tuple:
    """`mdb <argv>` as the console script runs it - cli.main(), not the CliRunner -, with the console of mdb
    writing to a buffer. Returns (exit code, all that was printed). Fails where an exception leaves main()."""
    printed = io.StringIO()
    monkeypatch.setattr(cli, "console", cli._Console(file=printed, width=1000, force_terminal=False, emoji=False))
    monkeypatch.setattr(sys, "argv", ["mdb", *argv])
    with pytest.raises(SystemExit) as ended:
        cli.main()
    out, err = capsys.readouterr()
    return ended.value.code, printed.getvalue() + out + err


def _answering(monkeypatch, status: int) -> list:
    """Every request of requests - and of sasctl, which sends with it - is answered: the HEAD of a token with
    302, the password grant with a token, every other request with `status` and BODY. Returns the requests."""
    sent = []

    def send(self, request, **options):
        sent.append((request.method, request.url.split("?")[0]))
        answer = requests.Response()
        answer.url, answer.request = request.url, request
        if request.method == "HEAD":
            answer.status_code, body = 302, b""
            answer.headers["Location"] = f"{VIYA}/SASLogon/login"
        elif "/SASLogon/oauth/token" in request.url:
            answer.status_code = 200
            answer.headers["Content-Type"] = "application/json"
            body = json.dumps({"access_token": "made.up.token", "token_type": "bearer", "expires_in": 3600}).encode()
        else:
            answer.status_code = status
            answer.headers["Content-Type"] = "text/plain; charset=utf-8"
            body = BODY.encode("utf-8")
        answer.raw = io.BytesIO(body)
        return answer
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    return sent


# ---- the programs: every one ends through the one place that prints an exception without control characters --
def test_the_programs_of_model_definition_builder_are_known():
    """Every Python file below Model-Definition-Builder that runs as a
    program, and every console script of the package: each is tested
    below. A new one fails here until it is."""
    mains = set()
    for path in _python_files():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.If) and ast.unparse(node.test) in ("__name__ == '__main__'",
                                                                      "'__main__' == __name__"):
                mains.add(path.relative_to(MDB_ROOT).as_posix())
    assert mains == {".claude/skills/model-audit/audit-models.py", "cli/src/mdb/cli.py"}
    pyproject = (MDB_ROOT / "cli" / "pyproject.toml").read_text(encoding="utf-8")
    scripts = re.search(r"^\[project\.scripts\]\n((?:[^\[\n].*\n|\n)*)", pyproject, re.M).group(1)
    assert [line for line in scripts.splitlines() if line.strip() and not line.startswith("#")] == [
        'mdb = "mdb.cli:main"']


@pytest.mark.parametrize("status", [401, 500])
@pytest.mark.parametrize("way", ["password", "token", "cli"])
def test_every_command_that_reaches_sas_viya_prints_an_answer_it_did_not_catch_without_control_characters(
        tmp_path, monkeypatch, capsys, repo_root, no_dotenv, way, status):
    """gate-23a, review-23a: SAS Viya answered the first request after
    the sign-in with 401 or 500 and a body holding an ESC; sasctl raised
    urllib's HTTPError with the body as its text, and `mdb list` ended with
    typer's traceback, which printed it as it is (exit code 1). Every
    command that reaches SAS Viya, with every way in: the command ends -
    with a line of its own, or with the traceback Python prints and exit
    code 1 - and nothing it prints holds a control character."""
    ended_by_traceback = []
    for command in sorted(_reaching_sas_viya()):
        tmp = tmp_path / command
        tmp.mkdir()
        with monkeypatch.context() as patched:
            _sign_in_env(patched, way, home=tmp / "home")
            patched.setenv("MDB_REPO", str(repo_root))
            patched.chdir(tmp)
            sent = _answering(patched, status)
            args = _arguments(command, tmp, patched, repo_root)
            code, text = _main(patched, capsys, command, *args)
        assert not _controls(text.replace("\n", "")), (command, _controls(text), text[-600:])
        assert any(method == "GET" for method, _ in sent), (command, sent)
        if "Traceback (most recent call last)" in text:
            assert code == 1 and f"HTTPError: HTTP Error {status}: {SHOWN}" in text, (command, code, text[-600:])
            ended_by_traceback.append(command)
    assert "list" in ended_by_traceback, ended_by_traceback


@pytest.mark.parametrize("status", [401, 500])
def test_the_audit_script_prints_an_answer_it_did_not_catch_without_control_characters(
        audit_script, monkeypatch, capsys, status):
    """gate-23a: the audit script with the password and a GET answered 500,
    review-23a: with SAS_VIYA_TOKEN and a GET answered 401 - both ended with
    the traceback of the HTTPError, its body as it is."""

    def answered():
        raise urllib.error.HTTPError(f"{VIYA}/modelRepository/projects", status, BODY, {}, None)
    monkeypatch.setattr(audit_script, "audit", answered)
    monkeypatch.setattr(sys, "argv", ["audit-models.py"])
    with pytest.raises(SystemExit) as ended:
        audit_script.main()
    out, err = capsys.readouterr()
    assert ended.value.code == 1 and out == "", (out, err)
    assert not _controls(err.replace("\n", "")), (_controls(err), err)
    assert err.startswith("Traceback (most recent call last):") and \
        err.endswith(f"urllib.error.HTTPError: HTTP Error {status}: {SHOWN}\n"), err


def test_the_audit_script_signs_in_and_prints_what_sas_model_manager_answered_without_control_characters(
        audit_script, monkeypatch, capsys, tmp_path):
    """The same through the sign-in and sasctl, with a token."""
    _sign_in_env(monkeypatch, "token", home=tmp_path)
    sent = _answering(monkeypatch, 401)
    monkeypatch.setattr(sys, "argv", ["audit-models.py"])
    with pytest.raises(SystemExit) as ended:
        audit_script.main()
    out, err = capsys.readouterr()
    assert ended.value.code == 1 and not _controls((out + err).replace("\n", "")), err
    assert f"HTTPError: HTTP Error 401: {SHOWN}" in err and sent[0] == ("HEAD", f"{VIYA}/"), (err, sent)


# ---- what click shows: every exception of click that a command ends with ---------------------------------
TYPED = "zz\x1b[31mRED\x9b\r"


def _click_errors() -> list:
    """Every class of exception that click shows to end a command: the ClickException of the click that
    typer uses and every subclass in its module."""
    module = sys.modules[cli._CLICK_ERROR.__module__]
    return sorted((kind for kind in vars(module).values()
                   if isinstance(kind, type) and issubclass(kind, cli._CLICK_ERROR)), key=lambda kind: kind.__name__)


#: An instance of each, with TYPED in what it shows.
_MAKE = {
    "ClickException": lambda kind, ctx: kind(TYPED),
    "UsageError": lambda kind, ctx: kind(TYPED, ctx),
    "BadParameter": lambda kind, ctx: kind(TYPED, ctx, param_hint="'--x'"),
    "MissingParameter": lambda kind, ctx: kind(TYPED, ctx, param_hint="'--x'", param_type="option"),
    "NoSuchOption": lambda kind, ctx: kind(f"--{TYPED}", ctx=ctx),
    "BadOptionUsage": lambda kind, ctx: kind("--x", TYPED, ctx),
    "BadArgumentUsage": lambda kind, ctx: kind(TYPED, ctx),
    "NoArgsIsHelpError": lambda kind, ctx: kind(ctx),
    "FileError": lambda kind, ctx: kind("f", hint=TYPED),
}


def test_every_exception_click_shows_is_known():
    """Every class of the installed click has its instance in _MAKE
    (NoArgsIsHelpError came with click 8.2: typer 0.12.0 runs with 8.1)."""
    found = {kind.__name__ for kind in _click_errors()}
    assert len(found) >= 8 and found <= set(_MAKE), found


@pytest.mark.parametrize("name", [kind.__name__ for kind in _click_errors()])
def test_every_exception_click_shows_is_printed_without_control_characters(monkeypatch, capsys, no_dotenv, name):
    """review-23a: `mdb list --x$'\\e[31m'zz` showed `No such option:
    --x^[[31mzz`. Each exception click shows, raised by a command with a
    text that holds control characters: the line shows the text without
    them."""
    kind, = [kind for kind in _click_errors() if kind.__name__ == name]
    info, = [info for info in cli.app.registered_commands if info.name == "list"]

    def ends(ctx: typer.Context):
        error = _MAKE[name](kind, ctx)
        if name == "NoArgsIsHelpError":
            error.message += TYPED          # typer shows the help of the command in its place, which mdb writes
        raise error
    monkeypatch.setattr(info, "callback", ends)
    code, text = _main(monkeypatch, capsys, "list")
    assert code == kind.exit_code and ("zz[31mRED" in text) == (name != "NoArgsIsHelpError"), (code, text)
    assert "\x1b[31mRED" not in text and "\x9b" not in text and "\r" not in text, repr(text)


@pytest.mark.parametrize("argv", [["list", f"--{TYPED}"], [f"zz{TYPED}"], ["list", "--help", f"--{TYPED}"],
                                  ["content-deploy", "--only", "a", "--only"]])
def test_what_was_typed_is_shown_without_control_characters(monkeypatch, capsys, no_dotenv, argv):
    """The same for what click's parser raises itself, at the top and in a command."""
    code, text = _main(monkeypatch, capsys, *argv)
    assert code in (0, 2), (code, text)
    assert "\x1b[31mRED" not in text and "\x9b" not in text and "\r" not in text, repr(text)


# ---- M4: a file a build of the checkout writes while the suite reads it ----------------------------------
CONTENT_PY = MDB_ROOT / "cli" / "src" / "mdb" / "core" / "content.py"


def test_every_file_of_the_checkout_the_content_module_reads_is_found_in_one_place():
    """content.repository_file is the one place: no other function of
    content.py makes a path of the checkout - a `joinpath`, a `/` or a
    `Path(...)` of more than one part."""
    tree = ast.parse(CONTENT_PY.read_text(encoding="utf-8"))
    one, = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "repository_file"]
    inside = {id(node) for node in ast.walk(one)}
    made = [node for node in ast.walk(tree)
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "joinpath")
            or (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div))
            or (isinstance(node, ast.Call) and ast.unparse(node.func) in ("Path", "PurePath") and len(node.args) > 1)]
    assert made and [f"{node.lineno}: {ast.unparse(node)}" for node in made if id(node) not in inside] == []


def test_the_files_a_build_of_the_checkout_writes_that_the_manifest_names_are_held(built_files_held):
    """The builds of the checkout are the node projects at its top (a
    package.json): of what they write, the manifest names the Prompt
    Builder's bundle alone - below vite's outDir -, and the session holds
    every file it names there; nothing else of a node project is named."""
    projects = sorted(path.parent.name for path in conftest.REPO_ROOT.glob("*/package.json"))
    assert projects == ["LLM-Prompt-Builder", "website"], projects
    vite = (conftest.REPO_ROOT / "LLM-Prompt-Builder" / "vite.config.ts").read_text(encoding="utf-8")
    assert re.findall(r"outDir:\s*'([^']+)'", vite) == ["dist"] and conftest.BUILT == "LLM-Prompt-Builder/dist/"
    manifest = content.load_manifest(conftest.REPO_ROOT)
    named = [value for value in manifest.sources() if value.split("/", 1)[0] in projects]
    assert named == conftest.built_files(conftest.REPO_ROOT) == ["LLM-Prompt-Builder/dist/index.html"]
    assert sorted(built_files_held) == named
    for relative, copy in built_files_held.items():
        assert content.repository_file(conftest.REPO_ROOT, relative) == copy and conftest.REPO_ROOT not in copy.parents
        elsewhere = conftest.REPO_ROOT / "elsewhere"            # another checkout: not held
        assert content.repository_file(elsewhere, relative) == elsewhere.joinpath(*PurePosixPath(relative).parts)


def test_the_manifest_of_the_checkout_loads_and_builds_while_a_build_has_taken_the_bundle_away(
        built_files_held, monkeypatch):
    """What the build does to the checkout - its bundle is not there -
    stood in for in this process alone: the manifest of the checkout loads,
    and the job definition of the Prompt Builder is built from the held
    copy. As a control, the checkout read directly fails as the hint test
    of test_mdb_21 failed."""
    gone = {conftest.REPO_ROOT.joinpath(*PurePosixPath(relative).parts) for relative in built_files_held}
    assert gone
    def taken_away(name):
        real = getattr(Path, name)

        def instead(self, *args, **options):
            if self in gone:
                if name in ("is_file", "exists"):
                    return False
                raise FileNotFoundError(2, "taken away by a build", str(self))
            return real(self, *args, **options)
        monkeypatch.setattr(Path, name, instead)
    for name in ("is_file", "exists", "open", "read_bytes", "read_text", "stat"):
        taken_away(name)
    assert not any(path.is_file() for path in gone)
    manifest = content.load_manifest(conftest.REPO_ROOT)
    ui, = [entry for entry in manifest.objects() if entry.form in built_files_held]
    body = content.job_definition_body(manifest, ui)
    assert body["properties"][0]["value"] == built_files_held[ui.form].read_text(encoding="utf-8")
    assert content.entry_hash(manifest, ui)
    # the control: the checkout as it is, without the held copy
    monkeypatch.setattr(content, "repository_file",
                        lambda repo_root, relative: Path(repo_root).joinpath(*PurePosixPath(relative).parts))
    with pytest.raises(content.ManifestError, match="form 'LLM-Prompt-Builder/dist/index.html' does not exist"):
        content.load_manifest(conftest.REPO_ROOT)
