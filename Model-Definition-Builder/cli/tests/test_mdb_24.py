# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What the second check of round 23 found in mdb at 6cae4ec, each as a
class. Nothing signs in anywhere: the transport of requests is replaced.

- gate: SAS_VIYA_URL with an empty port (`http://127.0.0.1:`) was taken,
  and the default port was asked. The tests take every form of a port -
  empty, no number, 0, out of range, and ports that can be used - with and
  without a path.
- live-23b B2: an exception that ends `mdb` or the audit script printed its
  text - the body of an answer of SAS Viya - with the line break of that
  body, so the line that names the exception was split. The tests take
  every exception a traceback shows (the one raised, its cause, its context,
  the members of a group, the message of a SyntaxError) and every place
  below Model-Definition-Builder that makes a traceback.
"""
import ast
import sys
import traceback
import urllib.error

import pytest

from mdb.core import printable
from mdb.viya import session as s
from test_fix_mdb_23 import BODY, SHOWN, _answering, _main, no_dotenv  # noqa: F401  (no_dotenv is a fixture)
from test_mdb_21 import ADDRESS, NO_PORT
from test_mdb_23 import MDB_ROOT, VIYA, _python_files, _sign_in_env, audit_script  # noqa: F401
from test_session import env, fake_sasctl  # noqa: F401

#: BODY on the one line of its exception, before the control characters are taken out.
ONE_LINE = "bad [/red] \\ back line two \x1b[31mRED\x1b[0m \x9b2J end"


# ---- gate: every form of a port --------------------------------------------------------------------------
REFUSED = ["", "4983x", "0", "65536", "99999"]
TAKEN = ["1", "4983", "65535"]
TAILS = ["", "/", "/SASLogon"]


def _ways(env) -> list:
    """The password and the token, one after the other, each alone."""
    def way(credentials):
        for name in (s.USER_ENV, s.PASSWORD_ENV, s.TOKEN_ENV):
            env.delenv(name, raising=False)
        for name, value in credentials.items():
            env.setenv(name, value)
    return [lambda: way({s.USER_ENV: "u", s.PASSWORD_ENV: "p"}), lambda: way({s.TOKEN_ENV: "not-a-jwt"})]


@pytest.mark.parametrize("tail", TAILS)
@pytest.mark.parametrize("port", REFUSED)
def test_a_port_that_is_no_number_from_1_to_65535_is_refused_before_any_request(env, fake_sasctl, port, tail):
    """gate (round 23, second check): `http://127.0.0.1:` - a colon with
    nothing behind it - was asked at port 80; urlparse reads an empty port
    as no port. Each form is refused with the line of a port that is no
    number, for a password and for a token, and sasctl is not called."""
    url = f"http://127.0.0.1:{port}{tail}"
    env.setenv(s.URL_ENV, url)
    for way in _ways(env):
        way()
        with pytest.raises(s.ViyaConfigError) as caught:
            s.create_session()
        assert str(caught.value) == f"SAS_VIYA_URL is {url!r}, " + ADDRESS.format(NO_PORT)
    assert fake_sasctl == []


@pytest.mark.parametrize("tail", TAILS)
@pytest.mark.parametrize("port", TAKEN)
def test_a_port_from_1_to_65535_reaches_sasctl(env, fake_sasctl, port, tail):
    url = f"http://127.0.0.1:{port}{tail}"
    env.setenv(s.URL_ENV, url)
    env.setenv(s.TOKEN_ENV, "not-a-jwt")
    s.create_session()
    assert fake_sasctl == [((url,), {"token": "not-a-jwt", "verify_ssl": True, "port": int(port)})]


@pytest.mark.parametrize("way", ["password", "token"])
@pytest.mark.parametrize("tail", TAILS)
def test_mdb_list_with_an_empty_port_ends_with_the_one_line_and_sends_nothing(
        monkeypatch, capsys, no_dotenv, tmp_path, way, tail):
    """The same through `mdb list` as the console script runs it: exit
    code 2, the one line, and no request."""
    _sign_in_env(monkeypatch, way, home=tmp_path)
    url = f"http://127.0.0.1:{tail}"
    monkeypatch.setenv(s.URL_ENV, url)
    sent = _answering(monkeypatch, 500)
    code, text = _main(monkeypatch, capsys, "list")
    assert (code, sent) == (2, []), (code, sent, text)
    assert text == f"SAS_VIYA_URL is {url!r}, " + ADDRESS.format(NO_PORT) + "\n"


# ---- live-23b B2: the line that names an exception is one line ------------------------------------------
def _raised(make) -> BaseException:
    try:
        make()
    except BaseException as exc:            # noqa: BLE001 - the exception is what the test looks at
        return exc
    raise AssertionError("nothing was raised")


def _answered():
    raise urllib.error.HTTPError(f"{VIYA}/modelRepository/projects", 500, BODY, {}, None)


def test_the_text_of_an_answer_is_on_the_line_that_names_its_exception():
    """The traceback Python prints, with BODY on one line: the frames are
    Python's lines as they are, and nothing else changes."""
    exc = _raised(_answered)
    python = "".join(traceback.format_exception(exc))
    assert python.endswith(f"urllib.error.HTTPError: HTTP Error 500: {BODY}\n")       # the split line
    shown = printable.traceback_text(exc)
    assert shown == python.replace(BODY, ONE_LINE)
    assert shown.splitlines()[-1] == f"urllib.error.HTTPError: HTTP Error 500: {ONE_LINE}"
    assert len(shown.splitlines()) == len(python.splitlines()) - 1


@pytest.mark.parametrize("text", ["plain", "two  blanks\tand a tab", "", "x" * 400])
def test_a_traceback_whose_texts_hold_no_line_break_is_the_one_python_prints(text):
    """Blanks and tabs inside one line are made one blank, as one_line does
    in every other line of mdb; a text that holds neither - and an empty
    one - is printed as Python prints it."""
    exc = _raised(lambda: (_ for _ in ()).throw(RuntimeError(text)))
    python = "".join(traceback.format_exception(exc))
    assert printable.traceback_text(exc) == (python.replace(text, " ".join(text.split())) if text else python)


def _chain():
    """An exception with a cause whose context is a third one: each text holds line breaks."""
    try:
        try:
            raise OSError("context\r\nof the cause")
        except OSError:
            raise LookupError("the cause\nof it")
    except LookupError as cause:
        raise RuntimeError("raised\n\n  last") from cause


def test_every_exception_of_a_chain_is_named_on_one_line():
    """The exception raised, its cause and the context of that cause."""
    exc = _raised(_chain)
    shown = printable.traceback_text(exc)
    lines = shown.splitlines()
    for line in ("OSError: context of the cause", "LookupError: the cause of it", "RuntimeError: raised last"):
        assert line in lines, shown
    assert not {"of the cause", "of it", "  last"} & set(lines), shown
    assert lines.count("") == "".join(traceback.format_exception(exc)).splitlines().count("") - 1   # "raised\n\n"
    assert [line for line in lines if line.startswith("  File ")] == \
        [line for line in "".join(traceback.format_exception(exc)).splitlines() if line.startswith("  File ")]
    assert "During handling of the above exception" in shown and "was the direct cause" in shown


@pytest.mark.skipif(sys.version_info < (3, 11), reason="exception groups came with Python 3.11")
def test_every_member_of_a_group_is_named_on_one_line():
    exc = _raised(lambda: (_ for _ in ()).throw(
        ExceptionGroup("the group\nof two", [ValueError("first\nmember"), _raised(_answered)])))  # noqa: F821
    lines = printable.traceback_text(exc).splitlines()
    assert any(line.endswith("ExceptionGroup: the group of two (2 sub-exceptions)") for line in lines), lines
    assert any(line.endswith("ValueError: first member") for line in lines), lines
    assert any(line.endswith(f"urllib.error.HTTPError: HTTP Error 500: {ONE_LINE}") for line in lines), lines
    rests = {"of two (2 sub-exceptions)", "member", "line two \x1b[31mRED\x1b[0m \x9b2J end"}
    assert not [line for line in lines if line.strip(" |+-") in rests], lines


@pytest.mark.parametrize("args", [("raised\nby hand",), ("read\r\nfrom an answer", ("answer.txt", 2, 3, "a line"))])
def test_the_message_of_a_syntax_error_is_on_the_line_that_names_it(args):
    """A SyntaxError names itself with its message, with or without the place it points at."""
    exc = _raised(lambda: (_ for _ in ()).throw(SyntaxError(*args)))
    lines = printable.traceback_text(exc).splitlines()
    assert f"SyntaxError: {' '.join(args[0].split())}" in lines, lines
    assert args[0].split("\n")[1] not in lines, lines


@pytest.mark.parametrize("status", [401, 500])
@pytest.mark.parametrize("way", ["password", "token", "cli"])
def test_mdb_list_ends_with_the_one_line_of_the_answer(monkeypatch, capsys, no_dotenv, tmp_path, way, status):
    """live-23b B2: `mdb list` against a stand-in that answers 401 or 500
    with BODY ended with a traceback whose last line was split."""
    _sign_in_env(monkeypatch, way, home=tmp_path)
    _answering(monkeypatch, status)
    code, text = _main(monkeypatch, capsys, "list")
    assert code == 1 and "Traceback (most recent call last):" in text, text
    assert text.endswith(f"\nurllib.error.HTTPError: HTTP Error {status}: {SHOWN}\n"), text[-400:]


@pytest.mark.parametrize("status", [401, 500])
@pytest.mark.parametrize("way", ["password", "token", "cli"])
def test_the_audit_script_ends_with_the_one_line_of_the_answer(audit_script, monkeypatch, capsys, tmp_path,
                                                               way, status):
    """The same for the audit script of the skill model-audit, through the sign-in and sasctl."""
    _sign_in_env(monkeypatch, way, home=tmp_path)
    _answering(monkeypatch, status)
    monkeypatch.setattr(sys, "argv", ["audit-models.py"])
    with pytest.raises(SystemExit) as ended:
        audit_script.main()
    out, err = capsys.readouterr()
    assert ended.value.code == 1 and out == "" and err.startswith("Traceback (most recent call last):"), err
    assert err.endswith(f"\nurllib.error.HTTPError: HTTP Error {status}: {SHOWN}\n"), err[-400:]


#: Every way the standard library makes or prints a traceback.
_TRACEBACK_MAKERS = {"format_exception", "format_exception_only", "format_exc", "print_exc", "print_exception",
                     "print_last", "format_tb", "print_tb", "format_stack", "print_stack", "TracebackException",
                     "excepthook"}


def test_every_traceback_below_model_definition_builder_is_made_by_traceback_text():
    """The class: every Python file below Model-Definition-Builder but the
    tests (test_mdb_23._python_files - the source of mdb and the scripts of
    the skills). Only traceback_text in core/printable.py names a maker of a
    traceback of the standard library, and the two programs (test_fix_mdb_23
    knows them) each print the exception that ends them through it, once."""
    makers, calls = [], {}
    for path in _python_files():
        name = path.relative_to(MDB_ROOT).as_posix()
        if name.startswith("cli/tests/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in _TRACEBACK_MAKERS or \
                    isinstance(node, ast.ImportFrom) and node.module == "traceback":
                makers.append(f"{name}: {ast.unparse(node)}")
            if isinstance(node, ast.Call) and ast.unparse(node.func) == "traceback_text":
                calls[name] = calls.get(name, 0) + 1
    assert makers == ["cli/src/mdb/core/printable.py: traceback.TracebackException"], makers
    assert calls == {".claude/skills/model-audit/audit-models.py": 1, "cli/src/mdb/cli.py": 1}, calls
