# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What the gate and the review of the round after the pass of 2026-10-13
found in mdb, each as a test that fails on the code before it. Every service
is a fake (content_fakes, the fakes of test_builder_commands) or a made-up
answer: nothing here says how SAS Viya answers.
"""
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from content_fakes import Response
from mdb.viya import answer as answering
from mdb.viya import content as deploying
from mdb.viya import session as s
from test_session import URL, _answered, env, fake_sasctl  # noqa: F401


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
