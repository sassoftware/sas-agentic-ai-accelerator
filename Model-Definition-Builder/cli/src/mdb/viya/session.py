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


class ViyaSignInError(ViyaConfigError):
    """SAS Viya refused the credentials. A command ends with its sentence,
    as it does for credentials that are missing."""


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
    try:
        if auth.method == "password":
            session = _sign_in(lambda: Session(auth.url, auth.user, auth.password, verify_ssl=verify))
        else:
            session = Session(auth.url, token=auth.token, verify_ssl=verify)
    except Exception as exc:
        if not _refused_sign_in(exc):
            raise
        # A refusal is an answer, not a defect of mdb: one sentence, no traceback.
        if auth.method == "password":
            made = getattr(exc, SIGN_INS_MADE, 1)
            how = (f"the sign-in of {auth.user} with {USER_ENV} + {PASSWORD_ENV} "
                   f"({made} sign-in{'' if made == 1 else 's'} made)")
        else:
            how = f"the access token ({TOKEN_ENV if auth.method == 'token' else 'the SAS Viya CLI login'})"
        raise ViyaSignInError(
            f"SAS Viya at {auth.url} refused {how}: {_refusal(exc, auth.method == 'password')}. Check the "
            f"credentials - or use another way in: {THE_THREE_WAYS}."
        ) from exc
    session.auth_summary = auth.summary
    return session


#: How often the password grant is asked, and the seconds waited before the
#: second and the third time. LOGIN_ATTEMPTS_ENV overrides the number; 1
#: switches the repeat off.
LOGIN_ATTEMPTS = 3
LOGIN_WAIT = (2, 5)
LOGIN_ATTEMPTS_ENV = "MDB_LOGIN_ATTEMPTS"

#: What the answer to a wrong password says in `error_description`, and how
#: often a grant that is answered so is asked in all - never more often than
#: the number above. Measured on 2026-10-13: a password grant with a wrong
#: password got 401 with the JSON body
#: {"error":"invalid_client","error_description":"Bad credentials"}.
BAD_CREDENTIALS = "bad credentials"
BAD_CREDENTIALS_ATTEMPTS = 2

#: The attribute `_sign_in` gives the refusal it passes on: how many sign-ins
#: were made.
SIGN_INS_MADE = "mdb_sign_ins_made"


#: The address the password grant is sent to; a 401 from it is a refused sign-in.
TOKEN_PATH = "/SASLogon/oauth/token"


def _refused_sign_in(exc: Exception) -> bool:
    """Whether an exception is a sign-in that SAS Viya refused.

    sasctl has two ways of saying so, and both were seen: its own
    AuthenticationError, and - for the password grant, as it arrived on the
    test system - the HTTPError of requests for the 401 that
    /SASLogon/oauth/token answered, raised from inside sasctl
    (`401 Client Error: for url: https://<host>/SASLogon/oauth/token#password`).
    Both are recognised by name and by what they carry, so that this module
    imports neither sasctl nor requests to say so. A 401 from any other
    address, and any other status of the token address, is not a refused
    sign-in.
    """
    if type(exc).__name__ == "AuthenticationError":
        return True
    if type(exc).__name__ != "HTTPError":
        return False
    response = getattr(exc, "response", None)
    url = str(getattr(response, "url", "") or getattr(getattr(exc, "request", None), "url", "") or "")
    status = getattr(response, "status_code", None)
    if status is None and str(exc).startswith("401 "):
        status = 401
    return status == 401 and TOKEN_PATH in (url or str(exc))


def _answer(exc: Exception) -> tuple:
    """(error, error_description) of the answer a refused sign-in carries,
    each "" where it cannot be read.

    sasctl hands the answer of the token address on in one of its two ways
    only: the HTTPError of requests carries it as `response`, and its JSON
    body names both fields. sasctl's own AuthenticationError carries no
    response - sasctl has read the body by then (see `_bad_credentials`)."""
    try:
        body = getattr(exc, "response", None).json()
    except Exception:       # no response, no JSON
        return "", ""
    if not isinstance(body, dict):
        return "", ""
    return tuple(" ".join(str(body.get(key) or "").split())[:200] for key in ("error", "error_description"))


def _bad_credentials(exc: Exception) -> bool:
    """Whether a refused sign-in is the answer a wrong password got:
    `error_description` says BAD_CREDENTIALS.

    Either mdb reads that from the answer (`_answer`), or sasctl did:
    sasctl (1.11.8 was read, and a test runs the one installed) raises its
    AuthenticationError for the password grant where the token address
    answered 401 with "bad credentials" in the `error_description` of its
    JSON body, and passes every other 401 on as the HTTPError of requests."""
    return type(exc).__name__ == "AuthenticationError" or BAD_CREDENTIALS in _answer(exc)[1].lower()


def _refusal(exc: Exception, grant: bool = False) -> str:
    """What a refused sign-in says, without the address of the token
    endpoint spelled out twice and without anything that was sent: the
    `error` and the `error_description` of the answer where it carries them.
    `grant` says that the refusal is one of the password grant - there
    sasctl's AuthenticationError stands for an `error_description` that mdb
    cannot read itself (`_bad_credentials`)."""
    if type(exc).__name__ == "HTTPError":
        error, description = _answer(exc)
        said = [f'{name} "{value}"' for name, value in (("error", error), ("error_description", description)) if value]
        return f"HTTP 401 from {TOKEN_PATH}" + (
            f" with {' and '.join(said)}" if said else " (no error_description could be read from the answer)")
    text = str(exc).strip().rstrip(".") or type(exc).__name__
    if grant and type(exc).__name__ == "AuthenticationError":
        return f'{text} - sasctl says so for an answer whose error_description says "Bad credentials"'
    return text


def _attempts() -> int:
    try:
        return max(1, int(_env(LOGIN_ATTEMPTS_ENV) or LOGIN_ATTEMPTS))
    except ValueError:
        return LOGIN_ATTEMPTS


def _sign_in(make, attempts: int | None = None, sleep=None):
    """A session from `make()`, asked again when the password grant is refused.

    A password grant has been answered 401 on the test system while several
    sign-ins overlapped (2026-10-11 and 2026-10-12; the command went through
    when it was run again, and the body of that 401 was not recorded). So a
    refused grant is asked again - LOGIN_ATTEMPTS times in all - before the
    refusal is passed on. A grant that is answered "Bad credentials"
    (`_bad_credentials`), which is what a wrong password got on 2026-10-13,
    is asked BAD_CREDENTIALS_ATTEMPTS times at most: a wrong password costs
    two failed sign-ins, and one with MDB_LOGIN_ATTEMPTS=1, which switches
    every repeat off. Anything that is not a refused sign-in (the server not
    reachable, a certificate) is passed on at once. The refusal that is
    passed on carries the number of sign-ins made as SIGN_INS_MADE.
    """
    import time

    if attempts is None:
        attempts = _attempts()
    attempts = max(1, attempts)
    sleep = sleep or time.sleep
    for attempt in range(1, attempts + 1):
        try:
            return make()
        except Exception as exc:
            if not _refused_sign_in(exc):
                raise
            if attempt >= (min(attempts, BAD_CREDENTIALS_ATTEMPTS) if _bad_credentials(exc) else attempts):
                setattr(exc, SIGN_INS_MADE, attempt)
                raise
            sleep(LOGIN_WAIT[min(attempt - 1, len(LOGIN_WAIT) - 1)])
