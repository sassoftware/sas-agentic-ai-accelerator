# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What the review of round 24 found in mdb at d932328, each as a class.
Nothing signs in anywhere: the transport of requests is replaced.

- review[5]: B2 was closed for tracebacks only. `mdb publish`, `register`,
  `retire` and the other lines that print the text of a caught exception
  printed the body of an answer that sasctl raises as urllib's HTTPError
  with its line break, so the answer stood on two lines. The tests take
  every command that reaches SAS Viya, and every handler of cli.py and of
  the modules of mdb/viya that can catch that HTTPError.
"""
import ast
import builtins
import importlib
import pkgutil
import urllib.error
from pathlib import Path

import pytest

import mdb
from mdb import cli
from test_fix_mdb_23 import BODY, SHOWN, _answering, _main, no_dotenv  # noqa: F401  (no_dotenv is a fixture)
from test_mdb_23 import _arguments, _reaching_sas_viya, _sign_in_env

#: The two halves of BODY, as a line shows them once its control characters are out.
HEAD, TAIL = SHOWN.split(" line two ")[0], "line two [31mRED[0m 2J end"


# ---- review[5]: the text of a caught exception is on the line that shows it ---------------------------------
@pytest.mark.parametrize("way", ["password", "token"])
def test_every_command_that_reaches_sas_viya_shows_an_answer_on_one_line(
        tmp_path, monkeypatch, capsys, repo_root, no_dotenv, way):
    """Every command that reaches SAS Viya, with SAS Viya answering 500 and
    BODY - a CR LF in the middle - to every request after the sign-in:
    wherever the answer is shown, in a line of the command or in the
    traceback, it is SHOWN, whole, on one line; its second half never
    starts a line of its own."""
    shown_in_a_line = set()
    for command in sorted(_reaching_sas_viya()):
        tmp = tmp_path / command
        tmp.mkdir()
        with monkeypatch.context() as patched:
            _sign_in_env(patched, way, home=tmp / "home")
            patched.setenv("MDB_REPO", str(repo_root))
            patched.chdir(tmp)
            _answering(patched, 500)
            args = _arguments(command, tmp, patched, repo_root)
            _code, text = _main(patched, capsys, command, *args)
        lines = text.splitlines()
        assert not [line for line in lines if line.strip().startswith(TAIL)], (command, text[-800:])
        for line in lines:
            if HEAD in line:
                assert SHOWN in line, (command, line)
                if "HTTPError: HTTP Error 500" not in line:
                    shown_in_a_line.add(command)
    # the lines of the review, each reached
    assert {"publish", "register", "retire"} <= shown_in_a_line, shown_in_a_line


def _viya_modules() -> list:
    """cli.py and every module of mdb/viya - the code that asks SAS Viya."""
    import mdb.viya
    names = ["mdb.cli"] + [f"mdb.viya.{info.name}" for info in pkgutil.iter_modules(mdb.viya.__path__)]
    return [importlib.import_module(name) for name in names]


def _known_names() -> dict:
    """Every name a handler can catch by: the globals of every module of mdb and the builtins."""
    names = dict(vars(builtins), yaml=importlib.import_module("yaml"))
    for info in pkgutil.walk_packages(mdb.__path__, "mdb."):
        try:
            names.update(vars(importlib.import_module(info.name)))
        except ImportError:                     # an adapter whose library is not installed
            continue
    return names


#: The calls that take the text of what they are given.
_TEXT_CALLS = {"str", "repr", "ascii", "format", "print", "_plain", "printable"}


def _parents(tree) -> dict:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _text_taken_wrongly(name_node, parents) -> bool:
    """Whether the exception's name stands where its text goes into a string other than through
    one_line(str(exc)): inside an f-string, `%`, or one of _TEXT_CALLS."""
    parent = parents[name_node]
    if isinstance(parent, ast.FormattedValue) or isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Mod):
        return True
    if isinstance(parent, ast.Call) and name_node in parent.args and ast.unparse(parent.func) in _TEXT_CALLS:
        above = parents.get(parent)
        return not (ast.unparse(parent.func) == "str" and isinstance(above, ast.Call)
                    and ast.unparse(above.func) == "one_line" and above.args[:1] == [parent])
    return False


def test_every_handler_that_can_catch_an_answer_takes_its_text_through_one_line():
    """The class: every named `except` of cli.py and of the modules of
    mdb/viya whose classes take urllib's HTTPError - the exception sasctl
    raises an answer of SAS Viya as. Where such a handler puts the text of
    its exception into a string, it is one_line(str(exc)) - one blank for
    every run of blanks, tabs and line breaks, as viya/answer.py does for
    every answer. Passing the exception to a function - type(), getattr(),
    the sign-in's own readers of an answer - takes no text here. A class a
    handler names that cannot be found fails the test."""
    known = _known_names()
    covered, faults = [], []
    for module in _viya_modules():
        path = Path(module.__file__)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = _parents(tree)
        for handler in ast.walk(tree):
            if not (isinstance(handler, ast.ExceptHandler) and handler.name and handler.type is not None):
                continue
            # the module's own names first; a class imported inside a function by the name it has where it is made
            scope = {**known, **vars(builtins), **vars(module)}
            caught = eval(compile(ast.Expression(handler.type), str(path), "eval"), scope)   # noqa: S307
            caught = caught if isinstance(caught, tuple) else (caught,)
            if not any(issubclass(urllib.error.HTTPError, kind) for kind in caught):
                continue
            covered.append(f"{module.__name__}:{handler.lineno}")
            for statement in handler.body:
                for node in ast.walk(statement):
                    if isinstance(node, ast.Name) and node.id == handler.name and isinstance(node.ctx, ast.Load) \
                            and _text_taken_wrongly(node, parents):
                        faults.append(f"{path.name}:{node.lineno}: {ast.unparse(parents[node])}")
    assert not faults, faults
    # publish, register, retire, the deploy's reads and writes, the transfer's delete, the sign-in
    assert len(covered) >= 30, covered
    assert {"mdb.viya.content", "mdb.viya.transfer", "mdb.viya.session", "mdb.viya.credentials"} <= \
        {where.split(":")[0] for where in covered}, covered


def test_one_line_is_what_a_handler_shows_of_an_answer():
    """The function itself, on the HTTPError the review raised: BODY on one line."""
    exc = urllib.error.HTTPError("http://x/modelRepository", 500, BODY, {}, None)
    assert cli.one_line(str(exc)) == "HTTP Error 500: " + BODY.replace("\r\n", " ")
