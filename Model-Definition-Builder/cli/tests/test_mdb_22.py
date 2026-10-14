# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What the gate and the review of round 21 found in mdb at 844b922, each
with the input that showed it. The service is a fake in memory and the token
address a stand-in for the transport of requests: no test signs in anywhere,
and every key is made up.

The tests of the first part run `mdb` in a process of its own, with the
typer this interpreter has: a traceback is printed by typer only there. Run
them with the lowest typer pyproject.toml allows (0.12.0) as well - the one
that prints the locals of every frame unless it is told not to."""
import ast
import base64
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from mdb import cli
from mdb.viya import credentials
from mdb.viya import session as s
from test_credentials_commands import _Response, mdb  # noqa: F401

SOURCE = Path(cli.__file__).resolve().parent
REPOSITORY = Path(__file__).resolve().parents[3]

#: Made up, and distinctive enough that finding one in an output is a leak - in plain text and in base64.
OPENAI, PGPW = "sk-made-up-OPENAIVALUE", "pgpw-made-up-SECRET"
LEAKS = (OPENAI, PGPW, *(base64.b64encode(value.encode()).decode() for value in (OPENAI, PGPW)))

#: Runs mdb's credentials commands against a service whose requests raise where MODE says.
RUN = r'''
import os, sys
import requests
from mdb import cli
from mdb.viya import credentials

os.chdir(sys.argv[1])
MODE = sys.argv[2]


class Answer:
    def __init__(self, status):
        self.status_code, self.text = status, ""

    def json(self):
        raise ValueError("no body")


class Site:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def get(self, path, **_):
        if MODE == "get":
            raise requests.exceptions.ConnectionError("Connection reset by peer")
        return Answer(404)

    def put(self, path, data=None, headers=None):
        if MODE == "put" and "/groups/" in path:
            raise requests.exceptions.ReadTimeout("Read timed out. (read timeout=60)")
        return Answer(200)

    def delete(self, path, **_):
        if MODE == "delete":
            raise requests.exceptions.ConnectionError("Connection reset by peer")
        return Answer(204)


cli._viya_session = lambda: Site()
if MODE == "uncaught":
    # a defect of mdb inside the frame that holds the values: nothing catches it
    def encode(entries):
        raise RuntimeError("a defect of mdb")
    credentials.encode = encode
sys.argv = ["mdb", *sys.argv[3:]]
cli.main()
'''


def _run(tmp_path: Path, mode: str, *args: str):
    (tmp_path / ".env").write_text(f"OPENAI_API_KEY={OPENAI}\nPGVECTOR_RAG_USER=u\nPGVECTOR_RAG_PW={PGPW}\n",
                                   encoding="utf-8")
    (tmp_path / "credentials.yaml").write_text(
        "source: .env\nidentities:\n  - {type: group, id: G}\n  - {type: user, id: gone, state: absent}\n",
        encoding="utf-8")
    script = tmp_path / "run.py"
    script.write_text(RUN, encoding="utf-8")
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join([str(SOURCE.parent), os.environ.get("PYTHONPATH", "")]),
                       COLUMNS="200", PYTHONIOENCODING="utf-8")
    done = subprocess.run([sys.executable, str(script), str(tmp_path), mode, *args], capture_output=True,
                          text=True, encoding="utf-8", env=environment, timeout=120)
    return done.returncode, done.stdout, done.stderr


# ---- M1: a request of the credentials commands that raises -----------------------------------
def test_no_typer_app_of_mdb_prints_the_locals_of_a_traceback():
    """review-21b (medium): typer 0.12.0, which pyproject.toml allows, prints
    the locals of every frame of a traceback by default - those of
    put_credential hold every value in plain text and in base64."""
    import typer
    apps = [value for value in vars(cli).values() if isinstance(value, typer.Typer)]
    assert len(apps) == 2
    assert all(app.pretty_exceptions_show_locals is False for app in apps)


def test_a_defect_in_the_frame_that_holds_the_values_prints_none_of_them(tmp_path):
    """The traceback of an exception nothing catches, under the typer of this
    interpreter: the frames of put_credential and encode hold the values."""
    code, out, err = _run(tmp_path, "uncaught", "credentials-apply")
    assert code == 1 and "RuntimeError" in err and "a defect of mdb" in err, (out, err)
    assert not [leak for leak in LEAKS if leak in out + err], (out, err)


@pytest.mark.parametrize("mode, args, code, line", [
    ("put", ["credentials-apply"], 1, "storing the credential for group 'G' failed: no answer (ReadTimeout)"),
    ("delete", ["credentials-apply"], 1, "removing the credential for user 'gone' failed: no answer (ConnectionError)"),
    ("get", ["credentials-apply"], 2, "reading the credential for group 'G' failed: no answer (ConnectionError)"),
    ("get", ["credentials-report"], 0, "reading the credential for group 'G' failed: no answer (ConnectionError)"),
])
def test_a_request_that_raises_ends_with_one_line_that_names_no_value(tmp_path, mode, args, code, line):
    """review-21b: a connection reset or a timeout of a request of
    credentials-apply ended in a traceback - with typer 0.12.0 one that
    printed every value the write carried."""
    done, out, err = _run(tmp_path, mode, *args)
    assert done == code and line in out and "Traceback" not in out + err, (out, err)
    assert not [leak for leak in LEAKS if leak in out + err], (out, err)


# ---- M2: the guard of a refused write compares as answer() prints ----------------------------
class _Answer:
    def __init__(self, status: int, text: str):
        self.status_code, self.text = status, text


@pytest.mark.parametrize("value, text", [
    ("pg  two  blanks", json.dumps({"message": "bad value pg  two  blanks", "errorCode": 1})),
    ("pg\ttab\nline", json.dumps({"message": "bad value pg\ttab\nline here", "errorCode": 1})),
    # a body that is no JSON is ended at 300 characters, in front of the word the cut falls into: here behind
    # `pg cut`, the first two words of the value
    ("pg cut " + "x" * 30, "y" * 285 + " pg cut " + "x" * 30 + " and more"),
    ("pg cut " + "x" * 30, "y" * 285 + " pg cut " + "x" * 30),
    ("pg/slash+plus", '{"message": "bad value pg\\/slash+plus"}'),
])
def test_a_refused_write_whose_answer_holds_a_value_prints_no_part_of_it(value, text):
    """gate-21b B5, review-21b: the guard compared the value as it is with
    the answer after one_line() had collapsed every run of blanks, and after
    answer() had cut a body that is no JSON at 300 characters."""
    session = type("S", (), {"put": lambda self, path, **_: _Answer(404, text)})()
    who = credentials.Identity(type="group", id="G")
    with pytest.raises(credentials.CredentialError) as caught:
        credentials.put_credential(session, "d", who, {"PGVECTOR_RAG_PW": value})
    assert str(caught.value) == "storing the credential for group 'G' failed: HTTP 404"


def test_a_refused_write_whose_answer_holds_no_value_still_shows_it():
    session = type("S", (), {"put": lambda self, path, **_: _Answer(
        404, json.dumps({"message": "the group  identity G does not exist", "errorCode": 12310}))})()
    with pytest.raises(credentials.CredentialError) as caught:
        credentials.put_credential(session, "d", credentials.Identity(type="group", id="G"), {"X": "pg  two"})
    assert str(caught.value) == ("storing the credential for group 'G' failed: HTTP 404 the group identity G does "
                                 "not exist (errorCode 12310)")


def test_a_refused_read_or_removal_of_another_status_names_the_message_of_the_service():
    """live-21b notRun: a refused read or removal with a status other than
    404, which shows the message of the service, could not be staged."""
    body = {"message": "Access denied for the domain zz.", "errorCode": 1234}
    answer = _Response(403, body)
    answer.text = json.dumps(body)
    session = type("S", (), {"get": lambda self, path, **_: answer, "delete": lambda self, path, **_: answer})()
    who = credentials.Identity(type="group", id="G")
    for call, verb in ((credentials.fetch_credential, "reading"), (credentials.delete_credential, "removing")):
        with pytest.raises(credentials.CredentialError) as caught:
            call(session, "zz", who)
        assert str(caught.value) == (f"{verb} the credential for group 'G' failed: HTTP 403 Access denied for the "
                                     "domain zz. (errorCode 1234)")


# ---- M3: the default of a prompt is cleaned like every other text ----------------------------
def _asks(tree) -> list:
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "ask"]


def test_every_prompt_of_mdb_asks_through_the_prompts_that_clean_what_they_show():
    """gate-21b B2: the default of a Prompt.ask went to the terminal with an
    ESC, an OSC, the C1 CSI byte, NUL and DEL in it."""
    asks = [(path.name, node) for path in sorted(SOURCE.rglob("*.py"))
            for node in _asks(ast.parse(path.read_text(encoding="utf-8")))]
    assert len(asks) >= 17
    assert {(name, ast.unparse(node.func.value)) for name, node in asks} == {("cli.py", "Prompt"), ("cli.py", "Confirm")}
    assert issubclass(cli.Prompt, cli._EmojiOff) and issubclass(cli.Confirm, cli._EmojiOff)


@pytest.mark.parametrize("control", ["\x1b[31m", "\x1b]0;owned\x07", "\x9b2J", "\x00", "\x7f", "\x0e", "\x85"])
def test_the_default_a_prompt_shows_holds_no_control_character(control):
    out = io.StringIO()
    console = Console(file=out, width=200, force_terminal=False)
    answer = cli.Prompt.ask("metadata.description", default=f"desc{control}end", console=console,
                            stream=io.StringIO(""))
    assert answer == f"desc{control}end"            # the value itself is what it was
    shown = out.getvalue()
    assert f"(desc{cli._printable(control)}end)" in shown, repr(shown)
    assert not re.search(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", shown), repr(shown)
    assert cli.Confirm.ask("Write these files?", default=True, console=console, stream=io.StringIO("")) is True


# ---- M4: the sign-in ---------------------------------------------------------------------------
URL = "https://viya.example.com"


@pytest.fixture
def token_address(monkeypatch, tmp_path):
    """The real sasctl, with the transport of requests replaced: `answer` is
    (status, body, the address it landed at) or an exception to raise."""
    pytest.importorskip("sasctl")
    import requests
    for name in (s.URL_ENV, s.TOKEN_ENV, s.USER_ENV, s.PASSWORD_ENV, s.CLI_PROFILE_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    monkeypatch.setenv(s.URL_ENV, URL)
    monkeypatch.setenv(s.USER_ENV, "u")
    monkeypatch.setenv(s.PASSWORD_ENV, "s3cret-p")
    monkeypatch.setenv("SAS_VIYA_VERIFY_SSL", "false")
    posted = []

    def post(self, url, **kwargs):
        posted.append(url)
        if isinstance(post.answer, Exception):
            raise post.answer
        status, body, landed = post.answer
        response = requests.Response()
        response.status_code, response.url = status, landed or url
        response._content = body.encode("utf-8")
        if landed:
            earlier = requests.Response()
            earlier.status_code, earlier.url = 302, url
            response.history = [earlier]
        return response

    monkeypatch.setattr(requests.Session, "post", post)
    post.posted = posted
    return post


NO_TOKEN = ("SAS Viya at https://viya.example.com did not answer the sign-in of u with SAS_VIYA_USER + "
            "SAS_VIYA_PASSWORD{made} with a token: {said}. mdb reads only HTTP 401 as a refusal of the credentials, "
            "and asks no other answer again: run the command again, and where the answer stays the same, look at "
            "SAS Logon.")


@pytest.mark.parametrize("body", ['{"access_token": null}', '{"access_token": 5}', '{"access_token": ""}',
                                  '{"access_token": ["x"]}'])
def test_a_200_whose_access_token_is_no_text_ends_at_the_sign_in(token_address, body):
    """gate-21b: `{"access_token": null}` was taken as a sign-in; the first
    request ended with exit code 1 and a TypeError on the line of the folder
    lookup."""
    token_address.answer = (200, body, None)
    with pytest.raises(s.ViyaSignInError) as caught:
        s.create_session()
    assert str(caught.value) == NO_TOKEN.format(
        made="", said="the answer to the sign-in holds no access token mdb could read")
    assert len(token_address.posted) == 1 and "s3cret-p" not in str(caught.value)


@pytest.mark.parametrize("body", ["<html>login</html>", '{"hello": 1}'])
def test_a_redirect_to_a_page_that_answers_200_is_one_line(token_address, body):
    """review-21b: a redirect of the token address to a login page that
    answers 200 - HTML, or JSON without a token - ended with a traceback."""
    token_address.answer = (200, body, f"{URL}/SASLogon/login")
    with pytest.raises(s.ViyaSignInError) as caught:
        s.create_session()
    assert str(caught.value) == NO_TOKEN.format(made=" (1 sign-in made)", said=(
        "HTTP 200 from /SASLogon/login, where /SASLogon/oauth/token sent the sign-in on, without an access token "
        "mdb could read"))


def test_an_address_where_nothing_answers_is_one_line(token_address, monkeypatch):
    """gate-21b: `SAS_VIYA_URL=http://127.0.0.1:` ended with the traceback
    of requests (ConnectionError) and exit code 1."""
    import requests
    token_address.answer = requests.exceptions.ConnectionError(
        "HTTPConnectionPool(host='127.0.0.1', port=80): Max retries exceeded with url: /SASLogon/oauth/token")
    with pytest.raises(s.ViyaSignInError) as caught:
        s.create_session()
    assert str(caught.value) == (
        "SAS Viya at https://viya.example.com could not be asked for the sign-in of u with SAS_VIYA_USER + "
        "SAS_VIYA_PASSWORD: ConnectionError (HTTPConnectionPool(host='127.0.0.1', port=80): Max retries exceeded "
        "with url: /SASLogon/oauth/token). Check SAS_VIYA_URL and that SAS Viya can be reached from here.")
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, width=80, force_terminal=False))
    result = CliRunner().invoke(cli.app, ["credentials-report", "--identity", "G"])
    assert result.exit_code == 2 and isinstance(result.exception, SystemExit)
    assert out.getvalue().count("\n") == 1 and "ConnectionError" in out.getvalue()


def test_the_texts_say_that_blanks_at_either_end_of_the_address_are_taken_out(monkeypatch, tmp_path):
    """gate-21b: a blank at either end and a trailing tab or line break of
    SAS_VIYA_URL were taken out without a word, while the texts said that a
    blank ends a command with one line."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv(s.TOKEN_ENV, "not-a-jwt")
    for value in (" http://127.0.0.1:4983", "http://127.0.0.1:4983 ", "http://127.0.0.1:4983\t",
                  "http://127.0.0.1:4983\n"):
        monkeypatch.setenv(s.URL_ENV, value)
        assert s.resolve_auth().url == "http://127.0.0.1:4983"
    readme = " ".join((REPOSITORY / "Model-Definition-Builder" / "README.md").read_text(encoding="utf-8").split())
    assert "a blank inside it, a second `//`" in readme
    assert "Blanks, tabs and line breaks at either end of the value are taken out" in readme


# ---- M5: what an option names -------------------------------------------------------------------
def _parameters():
    """(command, parameter) of every parameter of every command of mdb that
    takes a text or a path, from the Typer app itself."""
    import typer.main
    found = []

    def walk(command, name):
        for sub_name, sub in sorted(getattr(command, "commands", {}).items()):
            walk(sub, f"{name} {sub_name}".strip())
        for param in getattr(command, "params", ()):
            if not getattr(param, "is_flag", False) and param.type.name in ("text", "str", "path"):
                found.append((name, param))
    walk(typer.main.get_command(cli.app), "")
    return found


def _invocation(command: str, param, value: str) -> list:
    """The command with `value` for one parameter, and a value that is not
    empty for every argument before it."""
    words = command.split()
    if param.param_type_name == "option":
        return [*words, param.opts[0], value]
    group = __import__("typer.main", fromlist=["get_command"]).get_command(cli.app)
    for word in words:
        group = group.commands[word]
    arguments = [p for p in group.params if p.param_type_name == "argument"]
    return [*words, *("x" if p.name != param.name else value for p in arguments)]


def _shown(param) -> str:
    return " / ".join(word for word in param.opts if word.startswith("--")) or (param.metavar or param.name.upper())


PARAMETERS = _parameters()


def test_the_parameters_are_found_from_the_app():
    names = {(command, _shown(param)) for command, param in PARAMETERS}
    assert len(PARAMETERS) >= 85
    assert {("builders-import", "--options"), ("builders-import", "--caslib"), ("load-facts", "--caslib"),
            ("load-releases", "--caslib"), ("content-deploy", "--compute-context"), ("package-export", "--name"),
            ("credentials-apply", "--identity-type"), ("provider check", "ADAPTER_ID")} <= names


@pytest.mark.parametrize("value", ["", " ", "\t"])
@pytest.mark.parametrize("command, param", PARAMETERS, ids=[f"{c}:{_shown(p)}" for c, p in PARAMETERS])
def test_an_empty_value_of_a_path_or_a_name_is_refused_with_exit_code_2(monkeypatch, tmp_path, command, param,
                                                                        value):
    """review-21b: the class 'an empty value is read as not given' was not
    closed - `builders-import --options ""` read `.`, an empty --caslib
    loaded into Public, an empty --compute-context or --name fell back to the
    default; `credentials-add --only` with a tab was not refused as empty.
    Every parameter that takes a text or a path refuses it, before the
    command does anything, with exit code 2 - but the two whose value is the
    text itself."""
    monkeypatch.chdir(tmp_path)
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, width=1000, force_terminal=False))
    def ran():
        raise AssertionError("the command ran")
    monkeypatch.setattr(cli, "Context", ran)
    monkeypatch.setattr(cli, "_viya_session", ran)
    result = CliRunner().invoke(cli.app, _invocation(command, param, value))
    said = out.getvalue()
    if (command, _shown(param)) in cli._TEXT_ITSELF:
        assert "is empty" not in said
        return
    assert result.exit_code == 2 and isinstance(result.exception, SystemExit), (result.output, said)
    if (command, _shown(param)) == ("content-deploy", "--only"):
        # rich prints a tab as blanks up to the next tab stop
        assert said.startswith('--only "') and '" names no path of the manifest: an empty value' in said, said
    else:
        line = ("--content-root is empty - what a variable of the shell that is not set gives. "
                if _shown(param) == "--content-root" else
                f"{_shown(param)} is empty - what a variable of the shell that is not set gives - and names no ")
        assert said.startswith(line), said
    assert said.count("\n") == 1, said
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("command", ["options-save", "options-restore"])
def test_options_save_and_restore_refuse_a_folder_with_one_exit_code(monkeypatch, tmp_path, command):
    """review-21b: `--file .` ended options-save with exit code 2 and
    options-restore with exit code 1."""
    from types import SimpleNamespace
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "Context", lambda: SimpleNamespace(repo=REPOSITORY))
    monkeypatch.setattr(cli, "_viya_session", lambda: pytest.fail("signed in"))
    result = CliRunner().invoke(cli.app, [command, "--file", "."])
    assert result.exit_code == 2


def test_builders_import_refuses_an_options_file_that_is_a_folder(monkeypatch, tmp_path):
    """review-21b: `builders-import --options .` said '. not found - run `mdb
    options-save --file .` first', a command that is refused."""
    from types import SimpleNamespace
    out = io.StringIO()
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "saved").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "console", Console(file=out, width=1000, force_terminal=False))
    monkeypatch.setattr(cli, "Context", lambda: SimpleNamespace(repo=REPOSITORY))
    monkeypatch.setattr(cli, "_content_manifest", lambda ctx, check_sources=True: SimpleNamespace(
        package_path=tmp_path / "package.json", content_root="/SAS Agentic AI Accelerator"))
    monkeypatch.setattr(cli, "_viya_session", lambda: pytest.fail("signed in"))
    result = CliRunner().invoke(cli.app, ["builders-import", "package.json", "--options", "saved"])
    assert result.exit_code == 2 and out.getvalue() == "saved is a folder, not an options file.\n", out.getvalue()
