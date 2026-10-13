# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What the gate and the review of the round after the pass of 2026-10-13
found in mdb, each as a test that fails on the code before it. Every service
is a fake (content_fakes, the fakes of test_builder_commands) or a made-up
answer: nothing here says how SAS Viya answers.
"""
import io
import json
import re
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console
from typer.testing import CliRunner

from content_fakes import CONTENT_ROOT, Response, make_repo
from mdb import cli
from mdb.core import content
from mdb.core.content import load_manifest
from mdb.viya import answer as answering
from mdb.viya import content as deploying
from mdb.viya import session as s
from test_builder_commands import (FILE_PATH, PACKAGE_FILE, Site, _built,  # noqa: F401
                                   _site_with_its_own_objects, discovery, importing, no_discovery, out, site)
from test_content_deploy import CORE, STEP_PATH, command, manifest, repo  # noqa: F401
from test_mdb_release_20 import _deploy_and_export, _git, _with_commit, gate, needs_git  # noqa: F401
from test_session import URL, _answered, env, fake_sasctl  # noqa: F401

REPOSITORY = Path(__file__).resolve().parents[3]


# ---- the answer of a refused request: one function, one line ----------------------
def test_an_answer_is_one_line_and_an_object_is_shown_as_json():
    """gate-20: a message with a line break made the FAILED line two lines,
    and a message that is an object was printed as a Python dict."""
    assert deploying._answer is answering.answer
    broken = {"message": "line one\nline two", "remediation": "do\tthis", "errorCode": 0}
    assert answering.answer(Response(412, broken)) == "HTTP 412 line one line two - do this (errorCode 0)"
    assert answering.answer(Response(500, content=b"first\r\nsecond\n\nthird")) == "HTTP 500 first second third"
    assert answering.answer(Response(400, {"message": {"a": 1}})) == 'HTTP 400 {"a": 1}'
    assert answering.answer(Response(400, {"message": "m", "errorCode": {"code": 7}})) == \
        'HTTP 400 m (errorCode {"code": 7})'
    # a body printed over several lines is still read as the JSON error it is
    pretty = json.dumps({"message": "refused", "remediation": "ask"}, indent=2)
    assert "\n" in pretty and answering.answer(SimpleNamespace(status_code=403, text=pretty)) == \
        "HTTP 403 refused - ask"
    # something that has no text at all
    assert answering.answer(SimpleNamespace(status_code=502)) == "HTTP 502"


@pytest.mark.parametrize("module, call", [
    ("cas", lambda m, r: m._drop_table(_Answers(r), "cas", "Public", "T")),
    ("cas", lambda m, r: m._save_table(_Answers(r), "cas", "Public", "T")),
    ("reports", lambda m, r: (_ for _ in ()).throw(m._failure("Reading the report failed", r))),
    ("transfer", lambda m, r: (_ for _ in ()).throw(m._failure("Starting the export failed", r))),
    ("transfer", lambda m, r: m._delete(_Answers(r), "/transfer/packages/p1", "package")),
])
def test_no_message_of_mdb_cuts_the_answer_of_the_server_in_the_middle(module, call):
    """gate-20: the deploy line showed the answer whole, and eleven other
    messages still cut it at 200 or 300 characters, wherever that fell."""
    import importlib

    refused = {"errorCode": 1234, "httpStatusCode": 409, "details": ["d" * 400],
               "message": "The table is in use by " + "another session " * 30 + "and was not replaced.",
               "remediation": "End that session and send the request again."}
    assert len(json.dumps(refused)) > 300 and len(refused["message"]) > 300
    with pytest.raises(RuntimeError) as caught:
        call(importlib.import_module(f"mdb.viya.{module}"), Response(409, refused))
    assert str(caught.value).endswith(
        f"HTTP 409 {refused['message']} - End that session and send the request again. (errorCode 1234)")


def test_no_module_that_talks_to_sas_viya_slices_the_text_of_an_answer():
    """The messages of the model repository are raised behind sasctl calls a
    test cannot stage without it: what holds them to the one function is that
    no module of mdb/viya cuts a text at a fixed length any more."""
    for path in sorted(Path(answering.__file__).parent.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"\[:\d\d+\]", source.replace("[:ANSWER_LIMIT]", "")), path.name
        if path.name in ("cas.py", "registry.py", "reports.py", "transfer.py", "content.py"):
            assert "from .answer import answer" in source, path.name


class _Answers:
    """A session that answers every write with one refusal."""

    def __init__(self, refusal):
        self.refusal = refusal

    def post(self, *args, **kwargs):
        return self.refusal

    put = delete = post


def test_the_sentence_of_a_refused_sign_in_shows_the_answer_whole():
    """gate-20: error and error_description were cut at 200 characters
    without a word about it."""
    said = {"error": "e" * 250, "error_description": "The account is locked. " * 20 + "Ask an\nadministrator."}
    refused = _answered(json.dumps(said))
    assert s._answer(refused) == (said["error"], " ".join(said["error_description"].split()))
    assert s._refusal(refused).endswith('Ask an administrator."')
    # a value that is no text is shown as the JSON it is
    assert s._answer(_answered('{"error":5,"error_description":{"reason":"locked"}}')) == ("5", '{"reason": "locked"}')


# ---- a 401 of the token address that sasctl raises as something else ----------------
@pytest.fixture
def logon(env, monkeypatch):
    """The real sasctl with the transport of requests replaced: every POST is
    answered 401 with the body the test sets. Skipped where sasctl is not
    installed (the `viya` extra)."""
    sasctl = pytest.importorskip("sasctl")
    import requests

    state = SimpleNamespace(body="", posted=[])

    def post(self, url, **kwargs):
        state.posted.append(url)
        response = requests.Response()
        response.status_code, response.url = 401, url
        response._content = state.body.encode("utf-8")
        response.headers["Content-Type"] = "application/json"
        return response

    monkeypatch.setattr(requests.Session, "post", post)
    monkeypatch.setitem(sys.modules, "sasctl", sasctl)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    env.setenv(s.URL_ENV, URL)
    env.setenv(s.USER_ENV, "u")
    env.setenv(s.PASSWORD_ENV, "s3cret-p")
    env.setenv("SAS_VIYA_VERIFY_SSL", "false")
    return state


@pytest.mark.parametrize("body, raised, said", [
    ('["Bad credentials"]', "AttributeError", "(no error_description could be read from the answer)"),
    ('{"error":null,"error_description":null}', "AttributeError",
     "(no error_description could be read from the answer)"),
    ('{"error":"unauthorized","error_description":5}', "AttributeError",
     'with error "unauthorized" and error_description "5"'),
    ('{"error":"invalid_client","error_description":"Unauthorized grant type: password"}', "RuntimeError",
     'with error "invalid_client" and error_description "Unauthorized grant type: password"'),
])
def test_a_401_that_sasctl_raises_as_something_else_is_a_refused_sign_in(logon, body, raised, said):
    """review-20: a 401 of the token address whose body is no object, whose
    error_description is no text, or says 'Unauthorized grant type', leaves
    sasctl as AttributeError or RuntimeError - and the command ended with a
    traceback after one sign-in."""
    logon.body = body
    with pytest.raises(s.ViyaSignInError) as caught:
        s.create_session()
    assert type(caught.value.__cause__).__name__ == raised and len(logon.posted) == 3
    assert str(caught.value).startswith(
        f"SAS Viya at {URL} refused the sign-in of u with SAS_VIYA_USER + SAS_VIYA_PASSWORD (3 sign-ins made): "
        f"HTTP 401 from /SASLogon/oauth/token {said}. Check the credentials")
    assert "s3cret-p" not in str(caught.value)


def test_a_port_in_the_url_is_handed_to_sasctl(env, fake_sasctl):
    """gate-20: SAS_VIYA_URL=http://127.0.0.1:4981 was asked at port 80 -
    sasctl takes the port from an argument of its own, which mdb did not
    give. For the password grant and for a token."""
    assert s._port("https://viya.example.com:8443") == 8443 and s._port("https://viya.example.com") is None
    assert s._port("viya.example.com:8443") is None and s._port("https://viya.example.com:port") is None
    env.setenv(s.URL_ENV, "https://viya.example.com:8443")
    env.setenv(s.USER_ENV, "u")
    env.setenv(s.PASSWORD_ENV, "p")
    s.create_session()
    env.setenv(s.TOKEN_ENV, "not-a-jwt")
    s.create_session()
    assert fake_sasctl == [(("https://viya.example.com:8443", "u", "p"), {"verify_ssl": True, "port": 8443}),
                           (("https://viya.example.com:8443",), {"token": "not-a-jwt", "verify_ssl": True,
                                                                 "port": 8443})]


def test_the_installed_sasctl_sends_the_grant_to_the_port(env, logon):
    env.setenv(s.URL_ENV, "http://127.0.0.1:4981")
    logon.body = '{"error":"invalid_client","error_description":"Bad credentials"}'
    with pytest.raises(s.ViyaSignInError, match="SAS Viya at http://127.0.0.1:4981 refused the sign-in of u"):
        s.create_session()
    assert logon.posted == ["http://127.0.0.1:4981/SASLogon/oauth/token#password"]


def _console(monkeypatch, width=80):
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, width=width, force_terminal=False))
    monkeypatch.setattr(cli, "Context", lambda: SimpleNamespace(repo=REPOSITORY))
    return out


@pytest.mark.parametrize("error, description", [
    ("unauthorized", "[/red] closed [bold]bold"),
    ("x[/]", "see [link=https://example.com]here[/link]"),
])
def test_what_sas_logon_answers_is_printed_as_it_is(logon, monkeypatch, error, description):
    """review-20: the sentence was printed with markup on. A closing tag in
    error or error_description ended the command with a MarkupError
    traceback and exit code 1, and another tag was read as markup."""
    out = _console(monkeypatch)
    logon.body = json.dumps({"error": error, "error_description": description})
    result = CliRunner().invoke(cli.app, ["content-deploy", "--dry-run"])
    assert result.exit_code == 2 and isinstance(result.exception, SystemExit), result.exception
    # one line at the width of a terminal, with the answer as it came
    line, = out.getvalue().splitlines()
    assert f'with error "{error}" and error_description "{description}". Check the credentials' in line
    assert len(line) > 80 and "s3cret-p" not in line


def test_the_sentence_for_a_missing_sasctl_keeps_its_bracket(monkeypatch):
    """The same print: read as markup, `cli[viya]` lost `[viya]`."""
    out = _console(monkeypatch, width=1000)
    sentence = 'sasctl is not installed - install the Viya extra: pip install -e "Model-Definition-Builder/cli[viya]"'

    def refuse():
        raise s.ViyaConfigError(sentence)
    monkeypatch.setattr(s, "create_session", refuse)
    result = CliRunner().invoke(cli.app, ["content-deploy", "--dry-run"])
    assert result.exit_code == 2 and out.getvalue() == sentence + "\n"


# ---- release.json and a run of a part: refused before the sign-in -------------------
@pytest.mark.parametrize("args", [
    ("--only", "release.json"),
    ("--only", "RAG", "--only", "release.json", "--dry-run"),
    ("--only", "release.json", "--to", "/Team/Other/release.json"),
    ("--only", "release.json", "--to", "/files/files/11111111-0000-4000-8000-000000000001", "--force"),
])
def test_a_part_that_names_release_json_is_refused_before_the_sign_in(command, monkeypatch, args):
    """review-20 and the pass of 2026-10-13: 'refused before anything is
    sent' - and the command signed in first (twice with a wrong password,
    and the refusal was never printed), and printed its header line in front
    of the sentence."""
    monkeypatch.setattr(cli, "_viya_session", lambda: pytest.fail("signed in for a run that is refused"))
    result, text, _viya = command(*args)
    assert result.exit_code == 1 and text == deploying.RECORD_NEEDS_ALL + "\n"
    # the rule is one function: the command asks it of the manifest, and so does the deploy
    manifest = load_manifest(cli.Context().repo)
    assert deploying.record_in_part(manifest, ["release.json"]) and deploying.record_in_part(manifest, ["RAG", "release.json"])
    assert not deploying.record_in_part(manifest, ["RAG"]) and not deploying.record_in_part(manifest, [])
    assert not deploying.record_in_part(manifest, ["release.json", "Data", "RAG", "Prompt Builder"])


# ---- a reference of the name of an entry that names an object of another kind ---------
def test_a_reference_of_another_kind_is_listed_as_what_it_is_and_fails_nothing(command):
    """gate-20 / review-20: the help said a reference 'of that name' fails
    the entry. The code needs name and kind - the kind says which one is
    meant - and listed the path of a manifest entry under 'Not in the
    manifest' without a word that the entry is something else."""
    result, text, viya = command()
    assert result.exit_code == 0, text
    viya.put_reference(f"{CONTENT_ROOT}/{CORE}", "/reports/reports/zz-other-kind")
    viya.put_reference(f"{CONTENT_ROOT}/{STEP_PATH}", "/files/files/a-file")
    viya.put_reference(f"{CONTENT_ROOT}/RAG", "/files/files/another-file")
    viya.put_reference(f"{CONTENT_ROOT}/RAG/not listed", "/files/files/a-third")
    viya.calls.clear()
    result, text, viya = command()
    assert result.exit_code == 0 and viya.wrote() == [] and "FAILED" not in text
    assert f"  equal        file          {CORE}  (id " in text
    assert "\nNot in the manifest and left alone (4) - " in text
    assert f"  {CORE} (reference to a report - not the file that is listed under this name)\n" in text
    assert f"  {STEP_PATH} (reference to a file - not the step that is listed under this name)\n" in text
    assert "  RAG (reference to a file - not the folder that is listed under this name)\n" in text
    assert "  RAG/not listed (reference to a file)\n" in text
    # the sentence of the help is the one the code keeps
    said = " ".join(cli.content_deploy.__doc__.split())
    assert ("or a reference of that name to an object of that kind elsewhere - alone, or beside the object itself - "
            "fail the entry: each is named on its line and none is written. A reference of that name to an object "
            "of another kind fails nothing and is listed at the end.") in said


# ---- the hints of two FAILED lines, run as printed -----------------------------------
def test_the_hint_for_a_reference_works_as_it_is_printed(command, repo):
    """gate-20: 'update the object it names with --to' was refused without
    --force where the reference names an object of another name - the case
    a site has: its own file, under its own name."""
    _, _, viya = command()
    core, = viya.at(f"{CONTENT_ROOT}/{CORE}")
    theirs = viya.put_member("/Team/Own/core_of_the_site.py", "/files/files/", b"THEIRS = 1\n")
    here = viya.folder_id(f"{CONTENT_ROOT}/RAG/rag_core")
    viya.members[here] = [m for m in viya.members[here] if m["uri"] != core]
    viya.put_reference(f"{CONTENT_ROOT}/{CORE}", theirs)
    result, text, _ = command()
    assert result.exit_code == 1
    hint = (f"remove the reference, or update the object it names from a checkout with `mdb content-deploy --only "
            f"\"{CORE}\" --to <its URI>` (with --force where that object has another name))\n")
    assert hint in text and f"names {theirs}, an object that lives elsewhere" in text
    # as printed, with the URI the line names: refused for the other name, and written with --force
    printed = shlex.split(hint.split("`")[1].replace("<its URI>", theirs))
    assert printed[:2] == ["mdb", "content-deploy"]
    result, text, _ = command(*printed[2:])
    assert result.exit_code == 1 and "pass --force to update it all the same" in text
    assert viya.files[theirs] == b"THEIRS = 1\n"
    result, text, _ = command(*printed[2:], "--force")
    assert result.exit_code == 0 and viya.files[theirs] == b"CORE = 1\n", text


def test_the_hint_for_two_objects_says_whose_content_the_command_writes(importing, no_discovery):
    """gate-20: `mdb builders-import` prints the line too, and the command
    it names writes the content of a checkout, not of the package."""
    no_discovery()
    viya = importing.viya
    mine = _site_with_its_own_objects(viya)
    viya.put_member(f"{CONTENT_ROOT}/{FILE_PATH}", "/files/files/", b"SECOND = 1\n")
    result, _fake, text = importing(Site(), "--no-reports", without_report=True)
    assert result.exit_code == 1
    assert (f"2 files of that name are in the folder (ids {mine[FILE_PATH].rsplit('/', 1)[-1]}, ") in text
    assert (f"update one from a checkout with `mdb content-deploy --only \"{FILE_PATH}\" --to <id>`, or remove the "
            "other)") in text


# ---- the commit release.json names: a commit id, and nothing around it ----------------
@needs_git
def test_only_the_id_of_a_commit_as_it_stands_is_a_commit_of_release_json(tmp_path, gate):
    """gate-20: three release.json commits made by hand. A full id with a
    blank in front or a line feed behind passed as a commit id in full; the
    id of an annotated tag was an ancestor and then failed on a sentence
    about its date; the id of a tree was 'a commit this clone does not
    hold'."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    make_repo(checkout)
    _git(checkout, "init", "-q")
    _git(checkout, "add", ".")
    _git(checkout, "commit", "-q", "-m", "first")
    head = _deploy_and_export(checkout)
    package_path = load_manifest(checkout).package_path
    assert content.commit_standing(checkout, head) == content.ANCESTOR and gate(checkout, "--release")[0] == 0
    for text in (" " + head, head + "\n", head + " ", "\t" + head):
        assert content.commit_standing(checkout, text) == content.NOT_A_COMMIT, repr(text)
        _with_commit(package_path, text)
        for form in ((), ("--release",)):
            code, printed = gate(checkout, *form)
            assert code == 1, repr(text)
            assert f"file 'release.json' names the commit {text!r}, which is no commit id in full (git" in printed
    # ids the clone holds that are no commit: an annotated tag, the tree of HEAD, the content of a file
    _git(checkout, "tag", "-a", "v0", "-m", "an annotated tag")
    others = {"tag": _git(checkout, "rev-parse", "v0"), "tree": _git(checkout, "rev-parse", "HEAD^{tree}"),
              "blob": _git(checkout, "rev-parse", "HEAD:CHANGELOG.md")}
    assert others["tag"] != head and _git(checkout, "rev-parse", "v0^{commit}") == head
    for kind, other in others.items():
        assert _git(checkout, "cat-file", "-t", other) == kind
        assert content.commit_standing(checkout, other) == content.OTHER_OBJECT, kind
        _with_commit(package_path, other)
        for form in ((), ("--release",)):
            code, printed = gate(checkout, *form)
            assert code == 1, kind
            assert (f"file 'release.json' names the commit {other[:12]}, which is the id of another object of this "
                    "clone - a tag, a tree or the content of a file - and not of a commit\n") in printed
            assert "is dated" not in printed and "does not hold" not in printed
    _with_commit(package_path, head)
    assert gate(checkout, "--release")[0] == 0


def test_an_error_that_is_no_answer_of_the_token_address_is_passed_on():
    """What tells the refusal: a frame of the traceback holds a 401 of the
    token address. The same error without one, with another status or from
    another address is the defect it looks like."""
    def raised(status, url):
        answer = SimpleNamespace(status_code=status, url=url, json=lambda: {"error_description": 5})  # noqa: F841
        try:
            raise AttributeError("'int' object has no attribute 'lower'")
        except AttributeError as exc:
            return exc
    token = f"{URL}/SASLogon/oauth/token#password"
    assert s._refused_sign_in(raised(401, token)) and s._answer(raised(401, token)) == ("", "5")
    assert not s._refused_sign_in(raised(503, token))
    assert not s._refused_sign_in(raised(401, f"{URL}/folders/folders"))
    assert not s._refused_sign_in(AttributeError("'int' object has no attribute 'lower'"))
    waited = []
    with pytest.raises(AttributeError):
        s._sign_in(lambda: (_ for _ in ()).throw(AttributeError("a defect")), sleep=waited.append)
    assert waited == []
