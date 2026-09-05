# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""sasctl session factory with the framework's .env conventions.

Three ways to authenticate, tried in this order (the first one that is
configured wins):

1. ``SAS_VIYA_TOKEN`` - an OAuth access token from any source (the SAS Viya
   CLI, the VS Code extension, an authorization-code flow you ran yourself).
2. ``SAS_VIYA_USER`` + ``SAS_VIYA_PASSWORD`` - the password grant.
3. The SAS Viya CLI's own login: ``sas-viya auth loginCode`` (or ``auth
   login``) leaves an access token in ``~/.sas/credentials.json``, and mdb
   reads it. On an SSO / SCIM / OIDC site, where no password grant is
   possible, that is all that is needed - and everything mdb creates is
   owned by the person who logged in, not by a service account.

``SAS_VIYA_URL`` names the server; when it is unset, the CLI profile's
``sas-endpoint`` is used. The CLI profile is ``Default`` unless
``SAS_CLI_PROFILE`` (the CLI's own variable) says otherwise.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

TOKEN_ENV = "SAS_VIYA_TOKEN"
USER_ENV = "SAS_VIYA_USER"
PASSWORD_ENV = "SAS_VIYA_PASSWORD"
URL_ENV = "SAS_VIYA_URL"
CLI_PROFILE_ENV = "SAS_CLI_PROFILE"
CLI_LOGIN_HINT = "sas-viya auth loginCode"

THE_THREE_WAYS = (
    f"{TOKEN_ENV} (an access token), {USER_ENV} + {PASSWORD_ENV}, or a SAS Viya CLI login "
    f"(`{CLI_LOGIN_HINT}` - mdb reads ~/.sas/credentials.json)"
)


class ViyaConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class CliLogin:
    """What the SAS Viya CLI left behind for one profile."""
    profile: str
    token: str
    expiry: datetime | None
    endpoint: str | None
    path: Path


@dataclass(frozen=True)
class Auth:
    """A resolved way in: exactly one of token / (user, password) is set."""
    method: str            # "token" | "password" | "cli"
    url: str
    token: str | None = None
    user: str | None = None
    password: str | None = None
    detail: str = ""

    @property
    def summary(self) -> str:
        if self.method == "password":
            return f"{self.url} as {self.user} (password grant)"
        if self.method == "token":
            return f"{self.url} with {TOKEN_ENV}{self.detail}"
        return f"{self.url} with the SAS Viya CLI login{self.detail}"


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _host(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return (parsed.hostname or "").lower()


def _fmt(when: datetime | None) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if when else "?"


def jwt_expiry(token: str) -> datetime | None:
    """The ``exp`` claim of a JWT access token, or None when it has none (or
    the token is opaque). Decoded, not verified - the server verifies."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        exp = claims.get("exp")
        return datetime.fromtimestamp(int(exp), tz=timezone.utc) if exp else None
    except Exception:
        return None


def _parse_cli_expiry(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def cli_login(profile: str | None = None, sas_dir: Path | None = None) -> CliLogin | None:
    """Read the SAS Viya CLI's login for one profile (``Default`` unless
    ``SAS_CLI_PROFILE`` or `profile` says otherwise). None when the CLI was
    never used on this machine or that profile has no access token."""
    sas_dir = sas_dir or Path.home() / ".sas"
    profile = profile or _env(CLI_PROFILE_ENV) or "Default"
    credentials_path = sas_dir / "credentials.json"
    try:
        credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entry = credentials.get(profile) if isinstance(credentials, dict) else None
    token = (entry or {}).get("access-token") if isinstance(entry, dict) else None
    if not token:
        return None
    endpoint = None
    try:
        config = json.loads((sas_dir / "config.json").read_text(encoding="utf-8"))
        endpoint = ((config.get(profile) or {}).get("sas-endpoint") or None) if isinstance(config, dict) else None
    except (OSError, ValueError):
        pass
    return CliLogin(profile=profile, token=str(token).strip(), expiry=_parse_cli_expiry(entry.get("expiry")),
                    endpoint=endpoint, path=credentials_path)


def resolve_auth(now: datetime | None = None) -> Auth:
    """Decide how to authenticate from the environment alone (no network).

    Raises ViyaConfigError with a message that names every way in when
    nothing usable is configured, and a specific one when the only candidate
    is expired or points at another server."""
    now = now or _now()
    url = _env(URL_ENV)
    token = _env(TOKEN_ENV)
    user, password = _env(USER_ENV), _env(PASSWORD_ENV)
    login = None if (token or (user and password)) else cli_login()

    if not url and login and login.endpoint:
        url = login.endpoint
    if not url:
        raise ViyaConfigError(
            f"Missing Viya configuration: {URL_ENV} (set it in the environment or .env"
            f"{', or log in with the SAS Viya CLI whose profile names the server' if not token else ''})."
        )

    if token:
        expiry = jwt_expiry(token)
        if expiry and expiry <= now:
            raise ViyaConfigError(
                f"{TOKEN_ENV} expired at {_fmt(expiry)} - generate a new access token "
                f"(or unset it and use one of: {THE_THREE_WAYS})."
            )
        detail = f" (expires {_fmt(expiry)})" if expiry else ""
        return Auth("token", url, token=token, detail=detail)

    if user and password:
        return Auth("password", url, user=user, password=password)

    if login:
        if login.endpoint and _host(login.endpoint) != _host(url):
            raise ViyaConfigError(
                f"No SAS Viya credentials for {url}: the SAS Viya CLI login (profile {login.profile}) is for "
                f"{login.endpoint}. Log in there with `{CLI_LOGIN_HINT}` (or pick another profile via "
                f"{CLI_PROFILE_ENV}), or provide {TOKEN_ENV} or {USER_ENV} + {PASSWORD_ENV}."
            )
        if login.expiry and login.expiry <= now:
            raise ViyaConfigError(
                f"The SAS Viya CLI login (profile {login.profile}) expired at {_fmt(login.expiry)} - run "
                f"`{CLI_LOGIN_HINT}` again (or provide {TOKEN_ENV} or {USER_ENV} + {PASSWORD_ENV})."
            )
        detail = f" (profile {login.profile}, expires {_fmt(login.expiry)})"
        return Auth("cli", url, token=login.token, detail=detail)

    partial = ""
    if user or password:
        partial = f" {USER_ENV} and {PASSWORD_ENV} must both be set for the password grant."
    raise ViyaConfigError(
        f"No SAS Viya credentials for {url}.{partial} Provide one of: {THE_THREE_WAYS}. "
        "Set them in the environment or .env."
    )


def create_session():
    """A sasctl Session for the resolved credentials. The session carries an
    ``auth_summary`` attribute - one line saying which way in was used."""
    try:
        from sasctl import Session
    except ImportError as exc:
        raise ViyaConfigError(
            'sasctl is not installed - install the Viya extra: pip install -e "Model-Definition-Builder/cli[viya]"'
        ) from exc
    auth = resolve_auth()
    verify = os.environ.get("SAS_VIYA_VERIFY_SSL", "true").strip().lower() == "true"
    if not verify:
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    if auth.method == "password":
        session = Session(auth.url, auth.user, auth.password, verify_ssl=verify)
    else:
        session = Session(auth.url, token=auth.token, verify_ssl=verify)
    session.auth_summary = auth.summary
    return session
