# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What the review of round 24 found in mdb at 615ffc4, as a class.

- review[0]: the scaffold that `mdb credentials-init`, and `mdb
  credentials-add` without a manifest, write put the domain and the source
  in unquoted. `--domain 2024` (0123, yes) wrote a manifest every other
  credentials command refused, `--domain "team-keys #2"` was read back as
  `team-keys`, `--domain "keys: x"` was no YAML, and an --env-file named
  0123 was refused as a number.

The class is "mdb writes a manifest value that it reads back as something
else". Every value mdb writes into a manifest goes in through one function,
`_flow`. The tests take, from the code, every command of cli.py that writes
a manifest and every function of mdb/viya/credentials.py that writes its
text, and every key Manifest.parse reads; they write each value of VALUES
into every field a writer writes, read it back, and run every credentials
command of cli.py on the file.

Nothing signs in anywhere: the credentials service is the fake in memory of
test_credentials_commands.py, and every key in a `.env` is made up.
"""
import ast
import os
import re
from pathlib import Path

import pytest
import yaml

from mdb import cli
from mdb.viya import credentials as cr
from test_credentials_commands import ENTRIES, ENV, mdb  # noqa: F401  (mdb is a fixture)
from test_fix_mdb_24 import _keys_read_by_parse

#: Values YAML would read as another kind, cut, or not read at all, written unquoted.
VALUES = ["2024", "0123", "yes", "no", "~", "null", "true", "1e3", "a #b", "k: v", "-x", "- x", "[x]", "{x}",
          "&a", "*a", "!t", "%x", "@x", "`x", "'q", '"q', "a'b", 'a"b', "?", ":x", "#x", "|", ">", "on",
          "0o17", ".inf", "2026-10-15", "12:30", "<<", "a,b"]

#: Characters a file name cannot hold on this system.
NOT_IN_A_FILE_NAME = set('<>:"/\\|?*') if os.name == "nt" else {"/"}


def _file_name(value: str) -> bool:
    return not set(value) & NOT_IN_A_FILE_NAME


def _raw(path: Path) -> dict:
    """The manifest as YAML reads it - what every credentials command starts from."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _written_keys(raw: dict) -> set:
    return set(raw) | {key for who in raw.get("identities") or () for key in who}


# ---- the writers, taken from the code ------------------------------------------------------------------------
def _tree(module) -> ast.Module:
    return ast.parse(Path(module.__file__).read_text(encoding="utf-8"))


def _functions(tree: ast.Module) -> dict:
    return {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}


def _calls(node) -> set:
    return {call.func.id for call in ast.walk(node)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)}


def _writing_commands() -> dict:
    """{command name: function} of every command of cli.py that writes a manifest file."""
    found = {}
    for function in _functions(_tree(cli)).values():
        for decorator in function.decorator_list:
            if (isinstance(decorator, ast.Call) and getattr(decorator.func, "attr", "") == "command"
                    and {"_write_new_manifest", "write_manifest_text"} & _calls(function)):
                found[decorator.args[0].value] = function
    return found


def _credentials_commands() -> set:
    """Every command of cli.py named credentials-..."""
    return {command.name for command in cli.app.registered_commands
            if (command.name or "").startswith("credentials-")}


def _text_writers() -> set:
    """The functions of credentials.py whose text a manifest is written from: the one whose text the commands'
    scaffold returns, and every function whose result edit_identity splices in."""
    functions = _functions(_tree(cr))
    scaffold_calls = {name for node in ast.walk(_functions(_tree(cli))["_credential_scaffold"])
                      if isinstance(node, ast.Return) for name in _calls(node)} & set(functions)
    spliced = set()
    for node in ast.walk(functions["edit_identity"]):
        if isinstance(node, ast.Assign) and any(getattr(target, "id", "") == "splices" for target in node.targets):
            spliced |= _calls(node.value) & set(functions)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and getattr(node.func.value, "id", "") == "splices"):        # splices.append(...), .extend(...)
            spliced |= {name for argument in node.args for name in _calls(argument)} & set(functions)
    return scaffold_calls | spliced


#: A key of a YAML mapping, or the dash of a list item, at the end of the text written so far on a line;
#: \0 stands for a value put in, such as the indentation or the name of a key.
_AT_A_VALUE = re.compile(r"[\0 ]*(- )?([\0A-Za-z_]+: )?")


def _put_in_unquoted(function: ast.FunctionDef) -> list:
    """Every value an f-string of `function` puts where YAML reads a value, without `_flow`; and every
    `rendered` (the text that replaces a value) that is not made by `_flow`."""
    found = []
    for node in ast.walk(function):
        if isinstance(node, ast.JoinedStr):
            line = ""
            for part in node.values:
                if isinstance(part, ast.Constant):
                    line = (line + part.value).rsplit("\n", 1)[-1]
                    continue
                at_a_value = _AT_A_VALUE.fullmatch(line) and line.rstrip("\0 ") and not line.lstrip().startswith("#")
                if at_a_value and "_flow" not in _calls(part):
                    found.append(ast.unparse(part.value))
                line += "\0"
        if isinstance(node, ast.Assign) and any(getattr(target, "id", "") == "rendered" for target in node.targets):
            if "_flow" not in _calls(node.value) and "rendered" not in {n.id for n in ast.walk(node.value)
                                                                          if isinstance(n, ast.Name)}:
                found.append(ast.unparse(node.value))
    return found


def test_every_writer_is_known_and_puts_every_value_in_through_flow():
    """The commands that write a manifest, the functions its text comes from, and the credentials commands
    that read one: a new one fails here until the round trips below take it."""
    assert set(_writing_commands()) == {"credentials-init", "credentials-add", "credentials-remove"}
    assert _text_writers() == {"scaffold", "_entry_splice", "_item_splices"}
    assert _credentials_commands() == {"credentials-init", "credentials-add", "credentials-remove",
                                       "credentials-apply", "credentials-report"}
    functions = _functions(_tree(cr))
    for name in _text_writers():
        assert _put_in_unquoted(functions[name]) == [], name
    # the check finds a value put in unquoted, as the scaffold put the domain and the source in before
    planted = ast.parse('def f(domain, source):\n    return f"""# {domain}\ndomain: {domain}\n'
                        'source: {relative_source(source)}\n  - id: {_flow(domain)}\n"""')
    assert _put_in_unquoted(planted.body[0]) == ["domain", "relative_source(source)"]


@pytest.mark.parametrize("value", VALUES)
def test_flow_spells_a_value_yaml_reads_back_as_it_is(value):
    """_flow, where a value of a mapping and an item of a list stand."""
    assert yaml.safe_load(f"key: {cr._flow(value)}\n") == {"key": value}
    assert yaml.safe_load(f"- {cr._flow(value)}\n") == [value]
    assert yaml.safe_load(f"key: {cr._flow([value, value])}\n") == {"key": [value, value]}


# ---- every writer, every field, read back --------------------------------------------------------------------
@pytest.mark.parametrize("value", VALUES)
def test_the_scaffold_and_the_editor_write_every_field_so_that_it_reads_back_as_given(tmp_path, value):
    """scaffold() and every road of edit_identity() - the placeholder, a new identity, an identity updated, a
    list written one name per line, a removal - with `value` in every field each writes. What YAML reads is
    the value given, and Manifest.parse takes it."""
    path = tmp_path / "credentials.yaml"
    written = set()

    text = cr.scaffold(tmp_path / value, path, value, ENTRIES)
    raw = yaml.safe_load(text)
    assert (raw["domain"], raw["source"], raw["identities"][0]["id"]) == (value, value, cr.PLACEHOLDER_ID)
    assert cr.Manifest.parse(text, path).domain == value
    written |= _written_keys(raw)

    edit = cr.edit_identity(text, path, "user", value, only=(value, "OpenAI"), source=value)   # the placeholder
    assert edit.outcome == "placeholder"
    edit = cr.edit_identity(edit.text, path, "group", "Team", only=(value,), source=value)      # a new identity
    assert edit.outcome == "added"
    edit = cr.edit_identity(edit.text, path, "group", "Team", only=("OpenAI", value), source=f"{value}/{value}",
                            state="absent")                                                  # updated
    assert (edit.outcome, edit.changes[-1]) == ("updated", "state: present -> absent")
    raw = yaml.safe_load(edit.text)
    assert raw["domain"] == value and raw["source"] == value
    assert raw["identities"] == [
        {"type": "user", "id": value, "only": [value, "OpenAI"], "source": value},
        {"type": "group", "id": "Team", "only": ["OpenAI", value], "source": f"{value}/{value}", "state": "absent"}]
    manifest = cr.Manifest.parse(edit.text, path)
    assert [(who.type, who.id, who.only, who.state) for who in manifest.identities] == [
        ("user", value, (value, "OpenAI"), "present"), ("group", "Team", ("OpenAI", value), "absent")]
    if _file_name(value):                                       # `k: v` is a path on drive k: to Windows
        assert manifest.identities[1].source == (tmp_path / value / value).resolve()
    written |= _written_keys(raw)

    block = (f"domain: {cr._flow(value)}\nsource: .env\nidentities:\n  - type: group\n    id: {cr._flow(value)}\n"
             "    only:\n      - OpenAI\n")
    edit = cr.edit_identity(block, path, "group", value, only=(value, "OpenRouter"), state="present")
    assert "      - " + cr._flow(value) in edit.text.splitlines()                    # one name per line still
    assert yaml.safe_load(edit.text)["identities"] == [{"type": "group", "id": value, "only": [value, "OpenRouter"]}]

    # every key Manifest.parse reads is written by a writer here, but verbatim, which no writer writes
    assert written == set(_keys_read_by_parse()) - {"verbatim"}, written


@pytest.mark.parametrize("value", VALUES)
def test_every_command_that_writes_a_manifest_writes_a_value_every_credentials_command_reads_back(
        mdb, tmp_path, value):  # noqa: F811  (mdb is the fixture)
    """credentials-init --domain/--env-file, credentials-add without a manifest (--domain, --env-file, the id),
    credentials-add on a manifest (the id, --source) and credentials-remove (the id), each with `value`;
    after each, the file reads back as given, and then every credentials command takes it. A value no file
    can be named on this system goes in for the domain and the ids, and the source is .env."""
    source = value if _file_name(value) else ".env"
    (tmp_path / source).write_text(ENV, encoding="utf-8")
    ran = set()

    def run(*args):
        ran.add(args[0])
        code, said = mdb(*args)
        assert code == 0, (args, said)
        assert "must be a text" not in said and "not valid YAML" not in said, (args, said)
        return said

    # credentials-init
    run("credentials-init", "--manifest", "init.yaml", f"--domain={value}", f"--env-file={source}")
    raw = _raw(tmp_path / "init.yaml")
    assert (raw["domain"], raw["source"]) == (value, source)
    # credentials-add without a manifest: the scaffold with the identity in it
    run("credentials-add", "--manifest", "credentials.yaml", f"--domain={value}", f"--env-file={source}",
        "group", "--", value)
    raw = _raw(mdb.path)
    assert (raw["domain"], raw["source"], raw["identities"]) == (value, source, [{"type": "group", "id": value}])
    # credentials-add on that manifest: a new identity with its own source, then the identity updated
    run("credentials-add", f"--source={source}", "user", "--", value)
    run("credentials-add", "--source=.env", "group", "--", value)
    # credentials-remove: an identity that is there, and one that is not
    run("credentials-remove", "user", "--", value)
    run("credentials-remove", "group", "--", f"{value}-gone")
    raw = _raw(mdb.path)
    assert (raw["domain"], raw["source"]) == (value, source)
    assert raw["identities"] == [{"type": "group", "id": value, "source": ".env"},
                                 {"type": "user", "id": value, "source": source, "state": "absent"},
                                 {"type": "group", "id": f"{value}-gone", "state": "absent"}]
    assert ran == set(_writing_commands())

    # every other credentials command takes both files
    for manifest in ("init.yaml", "credentials.yaml"):
        run("credentials-add", "--manifest", manifest, "group", "Team")
        run("credentials-remove", "--manifest", manifest, "group", "Team")
        run("credentials-apply", "--manifest", manifest, "--dry-run")
        run("credentials-report", "--manifest", manifest)
        assert _raw(tmp_path / manifest)["domain"] == value
    assert ran == _credentials_commands()
    assert [call for call in mdb.site.calls if call[0] != "GET"] == []          # dry runs and reports only


@pytest.mark.parametrize("domain, read", [("2024", "2024"), ("team-keys #2", "team-keys #2"), ("keys: x", "keys: x")])
def test_the_review_s_runs(mdb, tmp_path, domain, read):  # noqa: F811
    """The review's runs: init --domain, and an add without a manifest, then a report."""
    code, said = mdb("credentials-init", "--manifest", "m.yaml", f"--domain={domain}")
    assert code == 0, said
    assert cr.Manifest.load(tmp_path / "m.yaml").domain == read
    code, said = mdb("credentials-add", "group", "Team", "--manifest", "a.yaml", f"--domain={domain}")
    assert code == 0, said
    assert cr.Manifest.load(tmp_path / "a.yaml").domain == read
    code, said = mdb("credentials-report", "--manifest", "a.yaml")
    assert code == 0, said


def test_an_env_file_named_0123_is_the_source_0123(mdb, tmp_path):  # noqa: F811
    (tmp_path / "0123").write_text(ENV, encoding="utf-8")
    assert mdb("credentials-init", "--env-file", "0123")[0] == 0
    assert mdb("credentials-add", "group", "Team")[0] == 0
    assert cr.Manifest.load(mdb.path).source == (tmp_path / "0123").resolve()


def test_the_default_scaffold_is_byte_for_byte_what_it_was():
    """The values of the default scaffold need no quotes, and _flow adds none."""
    text = cr.scaffold(Path("/x/.env"), Path("/x/credentials.yaml"), cr.DEFAULT_DOMAIN, ("OpenAI",))
    lines = text.splitlines()
    assert "domain: agentic-ai-keys" in lines and "source: .env" in lines
    assert f"    id: {cr.PLACEHOLDER_ID}" in lines
