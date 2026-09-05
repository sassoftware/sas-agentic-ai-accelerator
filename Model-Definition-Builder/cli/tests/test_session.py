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
