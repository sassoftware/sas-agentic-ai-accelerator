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
- review[7]: a credentials manifest written by hand was read with `str()`
  of what YAML made of it: `id:` empty or `~` became the group `None`,
  `0123` the group `83`, `yes` the group `True`, and apply wrote or deleted
  the credential of that other identity. The tests take every key that
  Manifest.parse reads.
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
from mdb.viya import credentials as cr
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
    starts a line of its own. Skipped where sasctl is not installed."""
    pytest.importorskip("sasctl")
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


# ---- review[7]: a value of the manifest is the text that was written, or it is refused -------------------------
def _parse(text: str):
    return cr.Manifest.parse(text, Path("credentials.yaml"))


#: Unquoted values YAML reads as no text: what `str` made of each, and how the refusal names it.
NO_TEXT = [("0123", "a number"), ("123", "a number"), ("1.50", "a number"), ("yes", "true or false"),
           ("off", "true or false"), ("2024-01-01", "a date"), ("[a]", "a list"), ("{a: 1}", "a mapping")]


def _keys_read_by_parse() -> dict:
    """{key: the expression that reads it} of every `<mapping>.get("<key>")` in Manifest.parse."""
    source = Path(cr.__file__).read_text(encoding="utf-8")
    parse = next(node for node in ast.walk(ast.parse(source))
                 if isinstance(node, ast.FunctionDef) and node.name == "parse")
    parents = _parents(parse)
    keys = {}
    for node in ast.walk(parse):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" \
                and node.args and isinstance(node.args[0], ast.Constant):
            keys[node.args[0].value] = ast.unparse(parents[node])
    return keys


#: How each key of the manifest stands in an identity, for a manifest that holds `value` there.
def _with(key: str, value: str) -> str:
    if key in ("domain", "source"):
        return f"{key}: {value}\nidentities:\n  - {{type: group, id: Team}}\n"
    if key == "identities":
        return f"identities: {value}\n"
    return f"identities:\n  - {{type: group, id: Team, {key}: {value}}}\n".replace("id: Team, id:", "id:")


def test_every_key_the_manifest_is_read_by_is_known():
    """The class: every key Manifest.parse reads. A new key fails here until it is in the tests below."""
    keys = _keys_read_by_parse()
    assert set(keys) == {"source", "identities", "domain", "only", "type", "id", "verbatim", "state"}, keys
    # every one but those checked against a list of their own values (type, state) and the list of identities
    # goes through _written
    assert {key for key, read in keys.items() if read.startswith("_written(")} == \
        {"source", "domain", "only", "id", "verbatim"}, keys


@pytest.mark.parametrize("key, written, read", [
    (key, written, read) for key in ("source", "identities", "domain", "only", "type", "id", "verbatim", "state")
    for written, read in NO_TEXT
    if not (key == "only" and read == "a list" or key == "verbatim" and read == "true or false")])
def test_a_value_yaml_reads_as_something_else_is_refused_for_every_key(key, written, read):
    """No key takes what `str` makes of a value YAML did not read as it
    was written - but a list for `only` and true or false for `verbatim`:
    the manifest is refused, and nothing is planned."""
    with pytest.raises(cr.CredentialError) as refused:
        _parse(_with(key, written))
    said = str(refused.value)
    if key in ("source", "domain", "id", "only", "verbatim"):
        kind = {"only": "a list", "verbatim": "true or false"}.get(key, "a text")
        assert f"'{key}' must be {kind}, and YAML read {read} there" in said, said
        assert not {"83", "True", "False", "1.5", "datetime"} & set(said.replace("(", " ").split()), said


@pytest.mark.parametrize("written", ["", "~", "null"])
def test_an_id_left_empty_is_no_id(written):
    """`id:` and `id: ~` became the group "None"."""
    with pytest.raises(cr.CredentialError, match="an identity with no id cannot be equipped"):
        _parse(f"identities:\n  - type: group\n    id: {written}\n")


@pytest.mark.parametrize("written, read", [("0123", "a number"), ("yes", "true or false"), ("1.50", "a number")])
def test_a_name_of_only_yaml_reads_as_no_text_is_refused(written, read):
    with pytest.raises(cr.CredentialError, match=f"a name of 'only' must be a text, and YAML read {read} there"):
        _parse(f"identities:\n  - {{id: Team, only: [OpenAI, {written}]}}\n")


def test_a_text_only_is_no_list_of_its_letters():
    """`only: OpenAI` was read as the names O, p, e, n, A and I."""
    with pytest.raises(cr.CredentialError, match="'only' must be a list, and YAML read a text there"):
        _parse("identities:\n  - {id: Team, only: OpenAI}\n")


@pytest.mark.parametrize("written", ["'no'", "1", "0"])
def test_verbatim_is_true_or_false(written):
    """`verbatim: 'no'` - a text - was read as true."""
    with pytest.raises(cr.CredentialError, match="'verbatim' must be true or false"):
        _parse(f"identities:\n  - {{id: Team, verbatim: {written}}}\n")


def test_quoted_the_same_values_are_the_texts_written():
    manifest = _parse("domain: '0123'\nsource: '0123'\nidentities:\n"
                      "  - {type: group, id: '0123', only: ['yes', '1.50'], verbatim: true}\n"
                      "  - {type: user, id: 'yes', verbatim: false}\n  - {id: 'null'}\n")
    assert manifest.domain == "0123" and manifest.source.name == "0123"
    assert [(who.type, who.id, who.only, who.verbatim) for who in manifest.identities] == [
        ("group", "0123", ("yes", "1.50"), True), ("user", "yes", (), False), ("group", "null", (), False)]


@pytest.mark.parametrize("domain", ["", "domain:\n", "domain: ~\n"])
def test_a_domain_left_out_or_empty_is_the_default_as_before(domain):
    assert _parse(f"{domain}identities:\n  - {{id: Team}}\n").domain == cr.DEFAULT_DOMAIN


@pytest.mark.parametrize("written, row", [("", "None"), ("~", "None"), ("0123", "83"), ("yes", "True")])
def test_credentials_apply_refuses_the_manifest_before_anything_is_asked(
        tmp_path, monkeypatch, capsys, no_dotenv, written, row):
    """The review's probe: `mdb credentials-apply --manifest <f> --dry-run`
    planned the group `None`, `83` or `True` and asked SAS Viya for it. It
    now ends with exit code 2 before any request, and names no such group."""
    _sign_in_env(monkeypatch, "token", home=tmp_path)
    sent = _answering(monkeypatch, 404)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=made-up\n", encoding="utf-8")
    manifest = tmp_path / "credentials.yaml"
    manifest.write_text(f"source: .env\nidentities:\n  - type: group\n    id: {written}\n", encoding="utf-8")
    code, text = _main(monkeypatch, capsys, "credentials-apply", "--manifest", str(manifest), "--dry-run")
    assert (code, sent) == (2, []), (code, sent, text)
    assert f"groups/{row}" not in text and f"| {row} " not in text, text
