# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""How mdb signs in to SAS Viya (issue #27): a token, the password grant, or
the SAS Viya CLI's own login - in that order - and what it says when none of
them is usable. Everything here is resolved from the environment and the
~/.sas files; no network, and sasctl is stubbed."""
import base64
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mdb.viya import session as s

NOW = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)
URL = "https://viya.example.com"


def _jwt(exp: datetime | None) -> str:
    claims = {"sub": "me"} | ({"exp": int(exp.timestamp())} if exp else {})
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJSUzI1NiJ9.{body}.sig"


@pytest.fixture
def env(monkeypatch, tmp_path):
    """A clean environment and an empty home: no .env variables, no CLI login."""
    for name in (s.URL_ENV, s.TOKEN_ENV, s.USER_ENV, s.PASSWORD_ENV, s.CLI_PROFILE_ENV, "SAS_VIYA_VERIFY_SSL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return monkeypatch


def _cli_login(home: Path, token="cli-token", expiry="2026-09-04T11:00:00Z", endpoint=URL, profile="Default"):
    sas = home / ".sas"
    sas.mkdir(exist_ok=True)
    (sas / "credentials.json").write_text(json.dumps({
        profile: {"access-token": token, "expiry": expiry, "refresh-token": "r"},
    }), encoding="utf-8")
    (sas / "config.json").write_text(json.dumps({
        profile: {"oauth-client-id": "sas.cli", "output": "fulljson", "sas-endpoint": endpoint},
    }), encoding="utf-8")


# -- the order ----------------------------------------------------------------------

def test_token_wins_over_password_and_cli(env, tmp_path):
    env.setenv(s.URL_ENV, URL)
    env.setenv(s.TOKEN_ENV, _jwt(NOW + timedelta(hours=1)))
    env.setenv(s.USER_ENV, "u")
    env.setenv(s.PASSWORD_ENV, "p")
    _cli_login(tmp_path)
    auth = s.resolve_auth(now=NOW)
    assert auth.method == "token" and auth.token.startswith("eyJ") and auth.user is None
    assert "expires 2026-09-04 11:00 UTC" in auth.summary


def test_password_grant_when_no_token(env, tmp_path):
    env.setenv(s.URL_ENV, URL)
    env.setenv(s.USER_ENV, "u")
    env.setenv(s.PASSWORD_ENV, "p")
    _cli_login(tmp_path)  # present, but the explicit .env credentials win
    auth = s.resolve_auth(now=NOW)
    assert (auth.method, auth.user, auth.password, auth.token) == ("password", "u", "p", None)


def test_cli_login_when_nothing_else_is_set(env, tmp_path):
    env.setenv(s.URL_ENV, URL)
    _cli_login(tmp_path)
    auth = s.resolve_auth(now=NOW)
    assert auth.method == "cli" and auth.token == "cli-token"
    assert "profile Default" in auth.summary and "expires 2026-09-04 11:00 UTC" in auth.summary


def test_cli_login_supplies_the_url_when_it_is_unset(env, tmp_path):
    _cli_login(tmp_path)
    auth = s.resolve_auth(now=NOW)
    assert auth.url == URL and auth.method == "cli"


def test_cli_profile_follows_the_clis_own_variable(env, tmp_path):
    _cli_login(tmp_path, token="other-token", profile="dev", endpoint="https://dev.example.com")
    env.setenv(s.CLI_PROFILE_ENV, "dev")
    auth = s.resolve_auth(now=NOW)
    assert auth.token == "other-token" and auth.url == "https://dev.example.com"


def test_opaque_token_has_no_expiry_to_report(env):
    env.setenv(s.URL_ENV, URL)
    env.setenv(s.TOKEN_ENV, "not-a-jwt")
    auth = s.resolve_auth(now=NOW)
    assert auth.method == "token" and auth.summary.endswith(s.TOKEN_ENV)


# -- what it says when it cannot ---------------------------------------------------

def test_nothing_configured_names_all_three_ways(env):
    env.setenv(s.URL_ENV, URL)
    with pytest.raises(s.ViyaConfigError) as exc:
        s.resolve_auth(now=NOW)
    text = str(exc.value)
    assert s.TOKEN_ENV in text and s.USER_ENV in text and s.CLI_LOGIN_HINT in text


def test_half_a_password_grant_is_called_out(env):
    env.setenv(s.URL_ENV, URL)
    env.setenv(s.USER_ENV, "u")
    with pytest.raises(s.ViyaConfigError, match="must both be set"):
        s.resolve_auth(now=NOW)


def test_missing_url_without_any_cli_profile(env):
    with pytest.raises(s.ViyaConfigError, match=s.URL_ENV):
        s.resolve_auth(now=NOW)


def test_expired_token_is_refused_up_front(env):
    env.setenv(s.URL_ENV, URL)
    env.setenv(s.TOKEN_ENV, _jwt(NOW - timedelta(minutes=1)))
    with pytest.raises(s.ViyaConfigError, match="expired at 2026-09-04 09:59 UTC"):
        s.resolve_auth(now=NOW)


def test_expired_cli_login_says_to_log_in_again(env, tmp_path):
    env.setenv(s.URL_ENV, URL)
    _cli_login(tmp_path, expiry="2026-09-04T09:30:00Z")
    with pytest.raises(s.ViyaConfigError, match=s.CLI_LOGIN_HINT):
        s.resolve_auth(now=NOW)


def test_cli_login_for_another_server_is_not_used(env, tmp_path):
    env.setenv(s.URL_ENV, URL)
    _cli_login(tmp_path, endpoint="https://elsewhere.example.com")
    with pytest.raises(s.ViyaConfigError, match="elsewhere.example.com"):
        s.resolve_auth(now=NOW)


def test_cli_login_survives_a_missing_or_broken_config(env, tmp_path):
    env.setenv(s.URL_ENV, URL)
    _cli_login(tmp_path)
    (tmp_path / ".sas" / "config.json").write_text("{not json", encoding="utf-8")
    assert s.resolve_auth(now=NOW).method == "cli"
    (tmp_path / ".sas" / "credentials.json").write_text("{}", encoding="utf-8")
    with pytest.raises(s.ViyaConfigError, match="Provide one of"):
        s.resolve_auth(now=NOW)


# -- what reaches sasctl ------------------------------------------------------------

@pytest.fixture
def fake_sasctl(monkeypatch):
    calls = []

    class Session:
        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setitem(sys.modules, "sasctl", types.SimpleNamespace(Session=Session))
    return calls


def test_create_session_passes_a_token_as_a_token(env, fake_sasctl, tmp_path):
    env.setenv(s.URL_ENV, URL)
    _cli_login(tmp_path, expiry=(datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    session = s.create_session()
    assert fake_sasctl == [((URL,), {"token": "cli-token", "verify_ssl": True})]
    assert session.auth_summary.startswith(f"{URL} with the SAS Viya CLI login")


def test_create_session_passes_the_password_grant_positionally(env, fake_sasctl):
    env.setenv(s.URL_ENV, URL)
    env.setenv(s.USER_ENV, "u")
    env.setenv(s.PASSWORD_ENV, "p")
    env.setenv("SAS_VIYA_VERIFY_SSL", "false")
    session = s.create_session()
    assert fake_sasctl == [((URL, "u", "p"), {"verify_ssl": False})]
    assert session.auth_summary == f"{URL} as u (password grant)"


# -- a sign-in that is refused while sign-ins overlap ---------------------------------

class AuthenticationError(ValueError):
    """What sasctl raises for a password grant answered 401 - told by its name."""


def test_a_refused_password_grant_is_asked_again(env):
    """Seen on the test system: the grant answered 401 while several sign-ins
    overlapped. The next one is taken - and the one after it."""
    waited, asked = [], []

    def make():
        asked.append(1)
        if len(asked) < 3:
            raise _refused_grant()
        return "session"
    assert s._sign_in(make, sleep=waited.append) == "session"
    assert len(asked) == 3 and waited == [2, 5]


def test_a_wrong_password_is_passed_on_at_the_first_try(env):
    """review-19 / gate-19: a wrong password cost three failed sign-ins per
    command, and review-20 / gate-20: then two. What sasctl raises for the
    answer "Bad credentials" is not asked again, whatever the setting says:
    the refusal seen while sign-ins overlapped left sasctl another way."""
    waited, asked = [], []

    def make():
        asked.append(1)
        raise AuthenticationError("Authentication failed for user 'u'.")
    for setting in (None, "1", "not a number", "5"):
        del asked[:]
        if setting:
            env.setenv(s.LOGIN_ATTEMPTS_ENV, setting)
        with pytest.raises(AuthenticationError) as caught:
            s._sign_in(make, sleep=waited.append)
        assert len(asked) == 1 and waited == [] and getattr(caught.value, s.SIGN_INS_MADE) == 1, setting
    assert s.LOGIN_ATTEMPTS == 3 and not hasattr(s, "BAD_CREDENTIALS_ATTEMPTS")


def test_anything_but_a_refused_sign_in_is_not_repeated(env):
    """A server that is not reachable is not a sign-in that overlapped."""
    asked = []

    def make():
        asked.append(1)
        raise ConnectionError("no route to host")
    with pytest.raises(ConnectionError):
        s._sign_in(make, sleep=lambda seconds: pytest.fail("waited for nothing"))
    assert len(asked) == 1


def test_only_the_password_grant_is_repeated(env, monkeypatch, tmp_path):
    """A token is taken or not: asking again does not change the answer."""
    calls = []

    class Session:
        def __init__(self, *args, **kwargs):
            calls.append(args)
            if len(calls) == 1:
                # the grant: the 401 that is not "Bad credentials"; the token: sasctl's own error
                raise _refused_grant() if len(args) == 3 else AuthenticationError("refused")

    monkeypatch.setitem(sys.modules, "sasctl", types.SimpleNamespace(Session=Session))
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    env.setenv(s.URL_ENV, URL)
    env.setenv(s.USER_ENV, "u")
    env.setenv(s.PASSWORD_ENV, "p")
    assert s.create_session().auth_summary == f"{URL} as u (password grant)"
    assert calls == [(URL, "u", "p"), (URL, "u", "p")]
    del calls[:]
    env.delenv(s.USER_ENV)
    env.delenv(s.PASSWORD_ENV)
    env.setenv(s.TOKEN_ENV, "not-a-jwt")
    with pytest.raises(s.ViyaSignInError, match="refused the access token"):
        s.create_session()
    assert calls == [(URL,)]


# -- the refusal as it arrives from sasctl ---------------------------------------------

class HTTPError(IOError):
    """requests.HTTPError, as sasctl lets it through from the token request:
    told by its name, its response and the address the response is from."""

    def __init__(self, message, status=None, url=None, body=None):
        super().__init__(message)
        if status is not None:
            self.response = types.SimpleNamespace(status_code=status, url=url)
        if body is not None:
            self.response.json = lambda: json.loads(body)


TOKEN_URL = f"{URL}/SASLogon/oauth/token"


def _refused_grant():
    """What `mdb content-deploy` ended with on the test system on 2026-10-12:
    raise_for_status() inside sasctl's _request_token_with_oauth."""
    return HTTPError(f"401 Client Error:  for url: {TOKEN_URL}#password", 401, TOKEN_URL)


def test_the_refused_grant_is_recognised_as_sasctl_passes_it_on():
    assert s._refused_sign_in(_refused_grant())
    assert s._refused_sign_in(AuthenticationError("Authentication failed"))
    # read from the message where the exception carries no response
    assert s._refused_sign_in(HTTPError(f"401 Client Error:  for url: {TOKEN_URL}#password"))
    # not a refused sign-in: another status of the token address, a 401 of another address, another exception
    assert not s._refused_sign_in(HTTPError(f"503 Server Error: for url: {TOKEN_URL}", 503, TOKEN_URL))
    assert not s._refused_sign_in(HTTPError(f"401 Client Error: for url: {URL}/folders/folders", 401,
                                            f"{URL}/folders/folders"))
    assert not s._refused_sign_in(ConnectionError("no route to host"))
    assert not s._refused_sign_in(ValueError(f"401 Client Error:  for url: {TOKEN_URL}"))


def test_a_grant_refused_with_401_is_asked_again(env):
    """The live defect: the repeat knew sasctl's AuthenticationError only, so
    the 401 that really arrived was passed on at the first try."""
    waited, asked = [], []

    def make():
        asked.append(1)
        if len(asked) < 2:
            raise _refused_grant()
        return "session"
    assert s._sign_in(make, sleep=waited.append) == "session"
    assert len(asked) == 2 and waited == [2]


def test_a_refused_sign_in_ends_with_a_sentence_and_no_traceback(env, monkeypatch):
    """Refused every time, the sign-in is a ViyaConfigError - what every
    command turns into one red line and exit code 2 - and the sentence holds
    neither the password nor a stack."""
    asked = []

    class Session:
        def __init__(self, *args, **kwargs):
            asked.append(args)
            raise _refused_grant()

    monkeypatch.setitem(sys.modules, "sasctl", types.SimpleNamespace(Session=Session))
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    env.setenv(s.URL_ENV, URL)
    env.setenv(s.USER_ENV, "u")
    env.setenv(s.PASSWORD_ENV, "s3cret-p")
    with pytest.raises(s.ViyaSignInError) as caught:
        s.create_session()
    assert isinstance(caught.value, s.ViyaConfigError) and len(asked) == 3
    message = str(caught.value)
    assert message.startswith(f"SAS Viya at {URL} refused the sign-in of u with SAS_VIYA_USER + SAS_VIYA_PASSWORD "
                              "(3 sign-ins made): HTTP 401 from /SASLogon/oauth/token (no error_description could "
                              "be read from the answer). Check the credentials")
    assert "s3cret-p" not in message and "Traceback" not in message
    # anything else is still passed on as it is: a server that is not there is no refusal
    class Unreachable:
        def __init__(self, *args, **kwargs):
            raise ConnectionError("no route to host")

    monkeypatch.setitem(sys.modules, "sasctl", types.SimpleNamespace(Session=Unreachable))
    with pytest.raises(ConnectionError):
        s.create_session()


def test_the_command_line_shows_the_refusal_as_one_line(env, monkeypatch):
    """Through a command: exit code 2 and the sentence, as for credentials
    that are missing - no traceback reaches the user."""
    from typer.testing import CliRunner

    from mdb import cli

    class Session:
        def __init__(self, *args, **kwargs):
            raise _refused_grant()

    monkeypatch.setitem(sys.modules, "sasctl", types.SimpleNamespace(Session=Session))
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    monkeypatch.setattr(cli, "Context", lambda: types.SimpleNamespace(repo=Path(__file__).resolve().parents[3]))
    env.setenv(s.URL_ENV, URL)
    env.setenv(s.USER_ENV, "u")
    env.setenv(s.PASSWORD_ENV, "s3cret-p")
    result = CliRunner().invoke(cli.app, ["content-deploy", "--dry-run"])
    assert result.exit_code == 2 and not isinstance(result.exception, HTTPError)
    printed = " ".join(result.output.split())
    assert "refused the sign-in of u" in printed and "HTTP 401 from /SASLogon/oauth/token" in printed
    assert "Traceback" not in result.output and "s3cret-p" not in result.output


# -- what the answer of a refused sign-in says, and how often it is asked ----------------
#: The body a password grant with a wrong password got on 2026-10-13 (401, application/json, 64 bytes).
BAD_CREDENTIALS_BODY = '{"error":"invalid_client","error_description":"Bad credentials"}'


def _answered(body: str):
    return HTTPError(f"401 Client Error:  for url: {TOKEN_URL}#password", 401, TOKEN_URL, body)


def test_the_answer_of_a_refused_sign_in_is_read():
    assert len(BAD_CREDENTIALS_BODY) == 64
    assert s._answer(_answered(BAD_CREDENTIALS_BODY)) == ("invalid_client", "Bad credentials")
    assert s._bad_credentials(_answered(BAD_CREDENTIALS_BODY))
    assert s._bad_credentials(_answered('{"error":"unauthorized","error_description":"BAD CREDENTIALS"}'))
    assert s._bad_credentials(AuthenticationError("Authentication failed for user 'u'."))
    # another body, no JSON, no response at all: a 401 that is not that answer
    for other in (_answered('{"error":"unauthorized","error_description":"Too many sign-ins"}'), _answered("<html/>"),
                  _answered('["Bad credentials"]'), _answered('{"error":"Bad credentials"}'), _refused_grant(),
                  HTTPError(f"401 Client Error:  for url: {TOKEN_URL}#password")):
        assert s._refused_sign_in(other) and not s._bad_credentials(other)
    assert s._refusal(_answered(BAD_CREDENTIALS_BODY)) == (
        'HTTP 401 from /SASLogon/oauth/token with error "invalid_client" and error_description "Bad credentials"')
    assert s._refusal(_answered('{"error_description":"Too\\n many   sign-ins"}')) == (
        'HTTP 401 from /SASLogon/oauth/token with error_description "Too many sign-ins"')
    assert s._refusal(_answered("<html/>")) == (
        "HTTP 401 from /SASLogon/oauth/token (no error_description could be read from the answer)")
    assert s._refusal(AuthenticationError("Authentication failed for user 'u'."), grant=True) == (
        "Authentication failed for user 'u' - sasctl says so for an answer whose error_description says "
        '"Bad credentials"')
    # of a token, sasctl's error is passed on as it is: no grant was answered
    assert s._refusal(AuthenticationError("Authentication failed.")) == "Authentication failed"


@pytest.mark.parametrize("body, limit, sign_ins, said", [
    (BAD_CREDENTIALS_BODY, None, 1, 'with error "invalid_client" and error_description "Bad credentials"'),
    (BAD_CREDENTIALS_BODY, "1", 1, 'with error "invalid_client" and error_description "Bad credentials"'),
    (BAD_CREDENTIALS_BODY, "5", 1, 'with error "invalid_client" and error_description "Bad credentials"'),
    ('{"error":"unauthorized","error_description":"Too many sign-ins"}', None, 3,
     'with error "unauthorized" and error_description "Too many sign-ins"'),
    ('{"error":"unauthorized","error_description":"Too many sign-ins"}', "5", 5,
     'with error "unauthorized" and error_description "Too many sign-ins"'),
    ("<html/>", "1", 1, "(no error_description could be read from the answer)"),
])
def test_the_sentence_names_the_answer_and_the_sign_ins_made(env, monkeypatch, body, limit, sign_ins, said):
    """review-19 / gate-19: the sentence said `asked 3 time(s)` from the
    setting, whatever was sent, and nothing of what SAS Logon answered. The
    answer measured for a wrong password is asked once."""
    asked = []

    class Session:
        def __init__(self, *args, **kwargs):
            asked.append(args)
            raise _answered(body)

    monkeypatch.setitem(sys.modules, "sasctl", types.SimpleNamespace(Session=Session))
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    env.setenv(s.URL_ENV, URL)
    env.setenv(s.USER_ENV, "u")
    env.setenv(s.PASSWORD_ENV, "s3cret-p")
    if limit:
        env.setenv(s.LOGIN_ATTEMPTS_ENV, limit)
    with pytest.raises(s.ViyaSignInError) as caught:
        s.create_session()
    assert len(asked) == sign_ins
    made = "1 sign-in made" if sign_ins == 1 else f"{sign_ins} sign-ins made"
    assert str(caught.value).startswith(
        f"SAS Viya at {URL} refused the sign-in of u with SAS_VIYA_USER + SAS_VIYA_PASSWORD ({made}): HTTP 401 from "
        f"/SASLogon/oauth/token {said}. Check the credentials")
    assert "s3cret-p" not in str(caught.value)


def test_a_refusal_that_changes_is_judged_by_the_answer_of_each_sign_in(env):
    """A refusal that is not "Bad credentials", then the answer to a wrong
    password: the second sign-in is the last. The other way round the first
    is."""
    def answers(*bodies):
        left = list(bodies)

        def make():
            asked.append(1)
            raise _answered(left.pop(0))
        return make
    other = '{"error":"unauthorized","error_description":"Too many sign-ins"}'
    asked = []
    with pytest.raises(HTTPError) as caught:
        s._sign_in(answers(other, BAD_CREDENTIALS_BODY, other), sleep=lambda seconds: None)
    assert len(asked) == 2 == getattr(caught.value, s.SIGN_INS_MADE)
    del asked[:]
    with pytest.raises(HTTPError) as caught:
        s._sign_in(answers(BAD_CREDENTIALS_BODY, other, other), sleep=lambda seconds: None)
    assert len(asked) == 1 == getattr(caught.value, s.SIGN_INS_MADE)


def test_sasctl_itself_hands_the_measured_answer_on_as_mdb_reads_it(env, monkeypatch):
    """The real sasctl, with the transport of requests replaced: the answer
    measured for a wrong password arrives as sasctl's AuthenticationError,
    which carries no response, and any other 401 of the token address as the
    HTTPError of requests with the response. Skipped where sasctl is not
    installed (the `viya` extra)."""
    sasctl = pytest.importorskip("sasctl")
    import requests

    posted = []
    answer = {}

    def post(self, url, **kwargs):
        posted.append(url)
        response = requests.Response()
        response.status_code, response.url = 401, url
        response._content = answer["body"].encode("utf-8")
        response.headers["Content-Type"] = "application/json"
        return response

    monkeypatch.setattr(requests.Session, "post", post)
    monkeypatch.setitem(sys.modules, "sasctl", sasctl)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    env.setenv(s.URL_ENV, URL)
    env.setenv(s.USER_ENV, "u")
    env.setenv(s.PASSWORD_ENV, "s3cret-p")
    env.setenv("SAS_VIYA_VERIFY_SSL", "false")

    answer["body"] = BAD_CREDENTIALS_BODY
    with pytest.raises(s.ViyaSignInError) as caught:
        s.create_session()
    assert type(caught.value.__cause__).__name__ == "AuthenticationError"
    assert getattr(caught.value.__cause__, "response", None) is None
    assert posted == [f"{TOKEN_URL}#password"]
    assert str(caught.value).startswith(
        f"SAS Viya at {URL} refused the sign-in of u with SAS_VIYA_USER + SAS_VIYA_PASSWORD (1 sign-in made): "
        "Authentication failed for user 'u' - sasctl says so for an answer whose error_description says "
        '"Bad credentials". Check the credentials')

    del posted[:]
    answer["body"] = '{"error":"unauthorized","error_description":"Too many sign-ins"}'
    with pytest.raises(s.ViyaSignInError) as caught:
        s.create_session()
    assert type(caught.value.__cause__).__name__ == "HTTPError" and len(posted) == 3
    assert ('(3 sign-ins made): HTTP 401 from /SASLogon/oauth/token with error "unauthorized" and error_description '
            '"Too many sign-ins". Check the credentials') in str(caught.value)
    assert "s3cret-p" not in str(caught.value)
