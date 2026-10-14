# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What the gate and the review of round 22 found in mdb at 5290563, each as
a class: the test takes every member from the code - every print of every
Python file of Model-Definition-Builder, every code point. Nothing signs in
anywhere: the transport of requests is replaced, and answers nothing."""
import ast
import importlib.util
import io
import json
import os
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

from mdb import cli
from mdb.core.manifest import ModelManifest
from mdb.core.printable import CONTROL
from mdb.viya import credentials
from mdb.viya import session as s
from test_credentials_commands import mdb  # noqa: F401

MDB_ROOT = Path(__file__).resolve().parents[2]
AUDIT_SCRIPT = MDB_ROOT / ".claude" / "skills" / "model-audit" / "audit-models.py"


# ---- the sign-in: one of the three ways, and a transport that answers nothing ----------------
VIYA = "http://127.0.0.1:1"
NO_ANSWER = "Check SAS_VIYA_URL and that SAS Viya can be reached from here."


def _sign_in_env(monkeypatch, way: str, user: str = "u", home: Path = None) -> None:
    """One of the three ways to sign in, and nothing of the others: no
    variable of SAS Viya or of sasctl from the environment of the run."""
    for name in list(os.environ):
        if name.startswith(("SAS_", "SASCTL_", "MDB_")) or name in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(s.URL_ENV, VIYA)
    if way == "token":
        monkeypatch.setenv(s.TOKEN_ENV, "not-a-jwt")
    elif way == "password":
        monkeypatch.setenv(s.USER_ENV, user)
        monkeypatch.setenv(s.PASSWORD_ENV, "p")
    if home is not None:
        monkeypatch.setattr(Path, "home", lambda: home)
        if way == "cli":
            (home / ".sas").mkdir(parents=True, exist_ok=True)
            expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            (home / ".sas" / "credentials.json").write_text(
                json.dumps({"Default": {"access-token": "cli-token", "expiry": expiry}}), encoding="utf-8")
            (home / ".sas" / "config.json").write_text(json.dumps({"Default": {"sas-endpoint": VIYA}}),
                                                       encoding="utf-8")


def _no_transport(monkeypatch, kind) -> list:
    """Every request of requests - and of sasctl, which sends with it - gets
    no answer: it raises `kind` as the transport does. Returns the requests
    sent, as (method, url)."""
    sent = []

    def send(self, request, **options):
        sent.append((request.method, request.url))
        raise kind(f"HTTPConnectionPool(host='127.0.0.1', port=1): Max retries exceeded with url: "
                   f"{request.path_url}", request=request)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    return sent


# ---- M1: foreign text printed raw - every print of every Python file of Model-Definition-Builder -----
def _python_files() -> list:
    """Every Python file below Model-Definition-Builder: the source of mdb,
    its tests and the scripts of the skills - not a virtual environment."""
    found = []
    for folder, folders, files in os.walk(MDB_ROOT):
        folders[:] = [name for name in folders if name not in (".venv", "venv", "__pycache__", "node_modules", ".git")]
        found += [Path(folder) / name for name in files if name.endswith(".py")]
    return sorted(found)


#: Every way to write to the terminal but `print` and the console of cli.py.
_RAW_WRITES = ("sys.stdout.write", "sys.stderr.write", "sys.__stdout__.write", "sys.__stderr__.write", "os.write",
               "pprint", "pprint.pprint", "typer.secho", "click.echo", "click.secho", "rich.print")


def test_every_print_of_mdb_and_of_its_skills_loses_its_control_characters():
    """gate-22b, review-22b: the audit script of the model-audit skill
    printed the sentence of a sign-in that failed - with what SAS Logon
    answered, SAS_VIYA_URL and SAS_VIYA_USER - and the names and the
    attributes SAS Model Manager answered with plain print(), so an ESC of
    an answer reached the terminal. Every `print` of every Python file of
    Model-Definition-Builder hands each value through `printable`; nothing
    writes to a stream another way; cli.py prints through its console, whose
    every text goes through `printable` (test_mdb_21), and `typer.echo` for
    the version alone."""
    files = _python_files()
    names = {path.relative_to(MDB_ROOT).as_posix() for path in files}
    assert {".claude/skills/model-audit/audit-models.py", "cli/src/mdb/cli.py", "cli/src/mdb/providers/__init__.py",
            "cli/src/mdb/viya/registry.py", "cli/tests/test_mdb_23.py"} <= names, names
    prints, faults = 0, []
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = ast.unparse(node.func)
            where = f"{path.relative_to(MDB_ROOT).as_posix()}:{node.lineno}"
            if name == "print":
                prints += 1
                if not node.args or not all(isinstance(arg, ast.Call) and ast.unparse(arg.func) in (
                        "printable", "_printable") for arg in node.args):
                    faults.append(f"{where}: {ast.unparse(node)}")
            elif name in _RAW_WRITES:
                faults.append(f"{where}: {ast.unparse(node)}")
            elif name == "typer.echo" and ast.unparse(node) != "typer.echo(f'mdb {__version__}')":
                faults.append(f"{where}: {ast.unparse(node)}")
    assert faults == []
    assert prints == 3        # the audit script's `say`, the adapter warning, the note of the model version bump
    assert cli._printable is cli.printable and cli._CONTROL is CONTROL


@pytest.fixture
def audit_script(monkeypatch):
    """The audit script as a module, without the .env of the checkout: the
    script loads one at its import, and the folders above a worktree hold
    the real one."""
    monkeypatch.setitem(sys.modules, "dotenv", types.SimpleNamespace(find_dotenv=lambda **_: "",
                                                                      load_dotenv=lambda *_, **__: False))
    spec = importlib.util.spec_from_file_location("audit_models_under_test", AUDIT_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ESC_TEXT = "zz\x1b[31muser\x9b\x07"


def test_the_audit_script_prints_the_sentence_of_a_failed_sign_in_without_its_control_characters(
        audit_script, monkeypatch, capsys):
    """review-22b D: SAS_VIYA_USER=$'zz\\x1b[31muser' with an address where
    nothing answers printed `... the sign-in of zz^[[31muser ...` with the
    ESC; mdb retire printed the same sentence without it."""
    _sign_in_env(monkeypatch, "password", user=ESC_TEXT)
    sent = _no_transport(monkeypatch, requests.exceptions.ConnectionError)
    monkeypatch.setattr(sys, "argv", ["audit-models.py"])
    with pytest.raises(SystemExit) as ended:
        audit_script.main()
    out, err = capsys.readouterr()
    assert ended.value.code == 2 and out == "" and len(sent) == 1, (out, err, sent)
    assert err.startswith("SAS Viya could not be used: SAS Viya at http://127.0.0.1:1 could not be asked for the "
                          "sign-in of zz[31muser with SAS_VIYA_USER + SAS_VIYA_PASSWORD: ConnectionError"), err
    assert not CONTROL.search(err.replace("\n", "")), repr(err)


@pytest.mark.parametrize("as_json", [False, True])
def test_the_audit_script_prints_what_sas_model_manager_answered_without_its_control_characters(
        audit_script, monkeypatch, capsys, as_json):
    """gate-22b: the rows - the name of a model, its family and its status
    as SAS Model Manager answered them - were printed with plain print()."""
    row = {"model_id": f"m{ESC_TEXT}", "kind": "llm", "llmodelType": f"f{ESC_TEXT}", "deploymentId": None,
           "modelStatus": f"s{ESC_TEXT}", "approvalState": None, "variables": 2, "duplicated_variables": True,
           "missing": ["endPoint"], "needs_update": True}
    monkeypatch.setattr(audit_script, "audit", lambda: [row])
    monkeypatch.setattr(sys, "argv", ["audit-models.py"] + (["--json"] if as_json else []))
    audit_script.main()
    out, err = capsys.readouterr()
    assert err == "" and not CONTROL.search(out.replace("\n", "")), repr(out)
    if as_json:
        assert json.loads(out)[0]["model_id"] == row["model_id"]      # JSON writes the ESC as \u001b
    else:
        assert "  - mzz[31muser (llm): endPoint; duplicated variables" in out, out
        assert "mzz[31muser" in out.splitlines()[2] and "fzz[31muse" in out.splitlines()[2], out


# ---- M2: a name that every line shows as another name - every code point ----------------------
_SHOWN_AS_ITSELF = range(0x20, 0x7F)


def _refused(make) -> bool:
    try:
        make()
    except (credentials.CredentialError, ValueError):
        return True
    return False


def test_an_identity_id_is_taken_for_every_code_point_that_shows_as_itself_and_for_no_other():
    """gate-22b, review-22b: `LLM<U+00A0>Consumers` was added beside `LLM
    Consumers` as a second identity, and so were `G1<U+2800>`, `G1<U+3164>`,
    `G1<U+034F>`, `G1<U+FE0F>`, `G1<U+115F>` and `G<U+00A0>1` beside G1 -
    every line showed them as the visible id. One rule over every code point:
    inside an id only printable ASCII is taken, at an end the same without
    the blank."""
    inside = [c for c in range(0x110000) if not _refused(lambda: credentials.Identity("group", f"G{chr(c)}1"))]
    assert inside == list(_SHOWN_AS_ITSELF)
    at_the_end = [c for c in range(0x110000) if not _refused(lambda: credentials.Identity("user", f"G1{chr(c)}"))]
    assert at_the_end == list(range(0x21, 0x7F))
    assert _refused(lambda: credentials.Identity("group", " G1"))


def test_a_credential_domain_is_taken_for_every_code_point_that_shows_as_itself_and_for_no_other():
    """The domain is the other name the credentials commands write from what
    they are given: `agentic-ai-keys<U+00A0>` was a second domain beside the
    one the Prompt Builder reads, shown alike. The same rule, in a manifest
    and for --domain (below)."""
    taken = [c for c in range(0x110000) if not _refused(lambda: credentials.Manifest(domain=f"k{chr(c)}k"))]
    assert taken == list(_SHOWN_AS_ITSELF)
    assert _refused(lambda: credentials.Manifest(domain="k ")) and _refused(lambda: credentials.Manifest(domain=" k"))


def test_a_model_id_is_taken_for_every_code_point_of_snake_case_and_for_no_other():
    """A model id is the name of its folder: `MODEL_ID_PATTERN.match` let a
    line feed at its end through (`$` matches in front of it), so `abc\\n`
    was a second definition beside abc on a file system that takes it."""
    taken = [c for c in range(0x110000) if not _refused(lambda: ModelManifest._valid_model_id(f"a{chr(c)}"))]
    assert taken == [ord(c) for c in "0123456789_abcdefghijklmnopqrstuvwxyz"]
    assert _refused(lambda: ModelManifest._valid_model_id("abc\n"))


def test_every_id_that_is_taken_is_printed_as_it_is():
    """The other half of the rule: an id of printable ASCII is printed as
    it is - every character, brackets and backslashes included - so two
    different ids never print the same."""
    out = io.StringIO()
    shown = cli._Console(file=out, width=1000, force_terminal=False, emoji=False)
    every = "".join(chr(c) for c in _SHOWN_AS_ITSELF).strip()
    for who in (every, "a\\[b]\\", "G 1", "G  1"):
        assert credentials.Identity("group", who).id == who
        out.seek(0)
        out.truncate()
        shown.print(f"Added group {cli._plain(who)} to m.yaml.")
        assert out.getvalue() == f"Added group {who} to m.yaml.\n", (who, out.getvalue())


@pytest.mark.parametrize("who", ["LLM Consumers", "LLM Consumers", "LLM　Consumers", "G͏2",
                                 "G1⠀", "G1ㅤ", "G1͏", "G1️", "G1ᅟ", "G 1", "G1᠎",
                                 "Jörg", "аda"])
def test_the_ids_the_findings_named_are_refused_by_every_command(mdb, who):  # noqa: F811
    """The inputs of gate-22b and review-22b, through the four commands
    that take an id and through a manifest written by hand."""
    assert mdb("credentials-add", "group", "LLM Consumers")[0] == 0
    assert mdb("credentials-add", "group", "G1")[0] == 0
    before = mdb.path.read_bytes()
    for args in (("credentials-add", "group", who), ("credentials-remove", "group", who),
                 ("credentials-apply", "--identity", who), ("credentials-report", "--identity", who)):
        code, said = mdb(*args)
        assert code == 2 and said.startswith("an identity id cannot hold a character outside printable ASCII"), \
            (args, said)
        assert said.count("\n") == 1, (args, said)
    assert mdb.path.read_bytes() == before and mdb.site.calls == []
    mdb.path.write_text(f"source: .env\nidentities:\n  - type: group\n    id: {json.dumps(who)}\n", encoding="utf-8")
    code, said = mdb("credentials-report")
    assert code == 2 and said.startswith("an identity id cannot hold") and mdb.site.calls == []


@pytest.mark.parametrize("command", ["credentials-init", "credentials-add", "credentials-apply", "credentials-report"])
def test_a_domain_a_line_shows_as_another_is_refused_by_every_command(mdb, command):  # noqa: F811
    """--domain of the four commands that take it, before anything is read,
    written or asked."""
    args = {"credentials-init": (), "credentials-add": ("group", "G1"), "credentials-apply": ("--identity", "G1"),
            "credentials-report": ("--identity", "G1")}[command]
    for domain in ("agentic-ai-keys ", "agentic‑ai-keys", "agentic-ai-keys\x1b"):
        code, said = mdb(command, *args, "--domain", domain)
        assert code == 2 and said.startswith("a credential domain cannot hold a character outside printable "
                                             "ASCII"), (command, domain, said)
        assert said.count("\n") == 1 and not mdb.path.exists() and mdb.site.calls == []
    mdb.path.write_text("domain: \"k\\u00a0\"\nsource: .env\nidentities:\n  - type: group\n    id: G1\n",
                        encoding="utf-8")
    code, said = mdb("credentials-report")
    assert code == 2 and said.startswith("a credential domain cannot hold") and mdb.site.calls == []
