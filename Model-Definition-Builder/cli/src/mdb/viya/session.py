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

The sign-in asks SAS Viya before the command does: the password grant is a
request of its own (asked again where it is refused, see `_sign_in`), and
with a token (1 and 3) one HEAD of the address is sent, whose answer is not
read. Where that request gets no answer the command ends with one line
(ViyaSignInError).
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .answer import one_line

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
    """The sign-in gave no session: SAS Viya refused the credentials, the
    token address answered with another error or with no token, or the
    request got no answer at all. A command ends with its sentence, as it
    does for credentials that are missing."""


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


def _port(url: str) -> int | None:
    """The port a URL with a scheme names (https://host:8443), or None."""
    try:
        return urlparse(url).port if "://" in url else None
    except ValueError:      # a port that is no number
        return None


def _address_problem(url: str) -> str:
    """Why an address of SAS Viya cannot be used, or "". Asked before any
    request: sasctl takes the scheme and the host from the address and
    builds every request from them, so an address it reads another way than
    its author ends in a request to another place and a traceback.

    * `host:8443` - a port and no scheme: sasctl reads `host` as the scheme
      and asks `host://host:8443/...`. A host alone it takes as https.
    * A port that is no number from 1 to 65535 was dropped, and the default
      port was asked - an empty one too (`http://host:`), which urlparse
      reads as no port.
    * A scheme other than https or http: sasctl sends with requests, which
      connects for these two alone.
    * An IPv6 address in brackets: sasctl takes the host without its
      brackets and builds an address from it that names no host.
    * A blank, a slash without a scheme (`https//host`, `//host`), a second
      `//` behind the scheme (`http://http://host`): sasctl asked the host
      `http` or `https`, or a host with a blank.
    * A user and a password in front of the host (`user:password@host`):
      mdb signs in with its own variables, and the address is printed in the
      first line of a command."""
    if any(character.isspace() for character in url):
        return "it holds a blank"
    if "://" not in url:
        if ":" in url:
            return "it holds a colon, and no scheme (https://) stands in front of the host"
        return "it holds a slash, and no scheme (https://) stands in front of the host" if "/" in url else ""
    try:
        parsed = urlparse(url)
    except ValueError:
        return "it cannot be read as an address"
    if parsed.scheme.lower() not in ("https", "http"):
        return "its scheme is neither https nor http"
    if "//" in url.split("://", 1)[1]:
        return "it holds a second // behind the scheme"
    if "@" in url.split("://", 1)[1]:
        return (f"it holds an @, as a user or a password in front of the host does - mdb signs in with {USER_ENV} + "
                f"{PASSWORD_ENV}, {TOKEN_ENV} or a SAS Viya CLI login")
    try:
        port = parsed.port
    except ValueError:
        port = 0
    if port == 0 or parsed.netloc.endswith(":"):
        return "its port is no number from 1 to 65535"
    if not parsed.hostname:
        return "it names no host"
    if ":" in parsed.hostname:
        return "its host is an IPv6 address, which sasctl - what mdb signs in with - does not ask"
    return ""


def _shown(url: str) -> str:
    """An address as a line may show it: without a user and a password in
    front of the host - everything up to the last `@`, also where a
    password holds a `/` or the address has no scheme."""
    if "@" not in url:
        return url
    scheme, separator, _ = url.partition("://")
    return (scheme + separator if separator else "") + "***@" + url.rsplit("@", 1)[1]


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

    named_by = URL_ENV
    if not url and login and login.endpoint:
        url, named_by = login.endpoint, f"The sas-endpoint of the SAS Viya CLI profile {login.profile}"
    if not url:
        raise ViyaConfigError(
            f"Missing Viya configuration: {URL_ENV} (set it in the environment or .env"
            f"{', or log in with the SAS Viya CLI whose profile names the server' if not token else ''})."
        )
    problem = _address_problem(url)
    if problem:
        raise ViyaConfigError(
            f"{named_by} is '{_shown(url)}', which mdb cannot use: {problem}. Give the address of SAS Viya as "
            "https://<host>, or https://<host>:<port> where it listens on a port of its own. Nothing was asked "
            "of SAS Viya."
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
                f"{_shown(login.endpoint)}. Log in there with `{CLI_LOGIN_HINT}` (or pick another profile via "
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
    # sasctl takes the scheme and the host from the URL and the port from an
    # argument of its own: without it a port in SAS_VIYA_URL is dropped
    port = _port(auth.url)
    where = {"port": port} if port else {}
    if auth.method == "password":
        how = f"the sign-in of {auth.user} with {USER_ENV} + {PASSWORD_ENV}"
    else:
        how = f"the access token ({TOKEN_ENV if auth.method == 'token' else 'the SAS Viya CLI login'})"
    try:
        if auth.method == "password":
            session = _sign_in(lambda: Session(auth.url, auth.user, auth.password, verify_ssl=verify, **where))
        else:
            session = Session(auth.url, token=auth.token, verify_ssl=verify, **where)
    except Exception as exc:
        status = _token_address_status(exc)
        if not _refused_sign_in(exc) and status is None:
            missing = _unanswered(exc)
            if missing is not None:
                # nothing answered - no server at that host and port, a certificate, a timeout: one line, no
                # traceback; what requests says names the address and the cause, nothing that was sent
                raise ViyaSignInError(
                    f"SAS Viya at {auth.url} could not be asked for {how}: {type(missing).__name__} "
                    f"({one_line(str(missing))}). Check {URL_ENV} and that SAS Viya can be reached from here."
                ) from exc
            raise
        # An answer of the token address is an answer, not a defect of mdb: one sentence, no traceback.
        if auth.method == "password":
            made = getattr(exc, SIGN_INS_MADE, 1)
            how += f" ({made} sign-in{'' if made == 1 else 's'} made)"
        if not _refused_sign_in(exc):
            # any other status of the token address: mdb does not read it as a word about the credentials,
            # and does not ask again
            raise ViyaSignInError(
                f"SAS Viya at {auth.url} did not answer {how} with a token: {_said(exc, status)}. mdb reads "
                "only HTTP 401 as a refusal of the credentials, and asks no other answer again: run the command "
                "again, and where the answer stays the same, look at SAS Logon."
            ) from exc
        raise ViyaSignInError(
            f"SAS Viya at {auth.url} refused {how}: {_refusal(exc, auth.method == 'password')}. Check the "
            f"credentials - or use another way in: {THE_THREE_WAYS}."
        ) from exc
    held = getattr(session, "auth", None)
    if hasattr(held, "access_token") and not (isinstance(held.access_token, str) and held.access_token):
        # sasctl takes a JSON answer whose access_token is null or a number for a token; the first request
        # then ended with a TypeError, on a line about that request
        raise ViyaSignInError(
            f"SAS Viya at {auth.url} did not answer {how} with a token: the answer to the sign-in holds no "
            "access token mdb could read. mdb reads only HTTP 401 as a refusal of the credentials, and asks no "
            "other answer again: run the command again, and where the answer stays the same, look at SAS Logon.")
    if auth.method != "password":
        # a token is taken without a request, so an address where nothing answers was found by the first request
        # of the command - which ended with the traceback of requests, or with a line of its own for each identity
        # of credentials-report. One request without an effect is asked first, as the password grant is: any
        # answer is enough, and what it says is not read
        try:
            _probe(session, auth.url)
        except Exception as exc:
            missing = _unanswered(exc)
            if missing is None:
                raise
            raise ViyaSignInError(
                f"SAS Viya at {auth.url} could not be asked with {how}: {type(missing).__name__} "
                f"({one_line(str(missing))}). Check {URL_ENV} and that SAS Viya can be reached from here."
            ) from exc
    session.auth_summary = auth.summary
    return session


def _probe(session, url: str) -> None:
    """One HEAD of the address `url` names (its scheme, host and port), with
    what the session sends - the token, the certificate check, the proxies.
    It is sent past sasctl's own `request`, which reads a 401 that says
    'access token expired' and asks the token address again: the answer is
    not read, whatever it is."""
    import requests
    parsed = urlparse(url if "://" in url else f"https://{url}")
    requests.Session.request(session, "HEAD", f"{parsed.scheme}://{parsed.netloc}/", allow_redirects=False)


def _unanswered(exc: Exception):
    """The exception of requests that `exc` is, or that sasctl raised it
    over, and None for anything else - here, what a request ends with where
    nothing answered: no server at that host and port, a certificate, a
    timeout. sasctl (1.11.8 was read) raises a RuntimeError ('SSL handshake
    failed ...') in the place of the SSLError of requests where
    REQUESTS_CA_BUNDLE is not set; the SSLError is its context."""
    from requests.exceptions import RequestException, SSLError
    if isinstance(exc, RequestException):
        return exc
    if isinstance(exc, RuntimeError) and isinstance(exc.__context__, SSLError):
        return exc.__context__
    return None


#: How often the password grant is asked, and the seconds waited before the
#: second and the third time. LOGIN_ATTEMPTS_ENV overrides the number; 1
#: switches the repeat off.
LOGIN_ATTEMPTS = 3
LOGIN_WAIT = (2, 5)
LOGIN_ATTEMPTS_ENV = "MDB_LOGIN_ATTEMPTS"

#: What the answer to a wrong password says in `error_description`; a grant
#: that is answered so is not asked again. Measured on 2026-10-13: a password
#: grant with a wrong password got 401 with the JSON body
#: {"error":"invalid_client","error_description":"Bad credentials"}.
BAD_CREDENTIALS = "bad credentials"

#: What `error_description` says where the client may not use the password
#: grant: `Unauthorized grant type: password`. sasctl (1.11.8 was read) takes
#: it for final as well. Not seen from SAS Logon in any pass; a test answers
#: it from a stand-in.
UNAUTHORIZED_GRANT = "unauthorized grant type"

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
    sign-in. Any other exception is one only where sasctl raised it over a
    401 of the token address (`_held_answer`).
    """
    if type(exc).__name__ == "AuthenticationError":
        return True
    if type(exc).__name__ != "HTTPError":
        return _held_answer(exc) is not None
    response = getattr(exc, "response", None)
    url = str(getattr(response, "url", "") or getattr(getattr(exc, "request", None), "url", "") or "")
    status = getattr(response, "status_code", None)
    if status is None and str(exc).startswith("401 "):
        status = 401
    return status == 401 and TOKEN_PATH in (url or str(exc))


def _token_address_status(exc: Exception):
    """The HTTP status the token address answered with, where an exception
    is the HTTPError of requests for an answer of that address that is no
    401 - a 500, a 503, a 400, a 403, a 429 - and None for anything else.
    mdb does not read such an answer as a refusal of the credentials and
    does not ask again: what a 400 or a 403 of SAS Logon means was not
    measured, and no repeat is known to change one. It ends a command with
    one line all the same.

    Two more answers are the token address's (gate-21a, against a
    stand-in): a redirect - the HTTPError is then of the address it led to,
    and the token address stands in its history -, and an answer sasctl
    could not read a token from - a 200 whose body is no JSON, or no
    `access_token` - which ends in a TypeError of sasctl; the frame that
    read the answer still holds it (`_held_answer`)."""
    if type(exc).__name__ != "HTTPError":
        held = _held_answer(exc, None)
        if held is None or (held.status_code == 401 and TOKEN_PATH in str(getattr(held, "url", "") or "")):
            return None
        return held.status_code
    response = getattr(exc, "response", None)
    url = str(getattr(response, "url", "") or getattr(getattr(exc, "request", None), "url", "") or "")
    asked = [url] + [str(getattr(earlier, "url", "") or "") for earlier in getattr(response, "history", None) or []]
    status = getattr(response, "status_code", None)
    if not isinstance(status, int) or isinstance(status, bool) or (status == 401 and TOKEN_PATH in url):
        return None
    return status if any(TOKEN_PATH in address for address in asked) else None


def _held_answer(exc: Exception, status=401):
    """The 401 answer of the token address that a frame in the traceback of
    `exc` holds, or None. With `status` None: its answer of any status, and
    also the answer of an address the token address redirected the sign-in
    to - a login page that answers 200 - which carries the token address in
    its history.

    sasctl (1.11.8 was read) reads the body of a 401 of the token address
    before it raises, and some bodies leave it as an exception that is
    neither of its two ways and carries no response: a JSON body that is no
    object, or whose `error_description` is no text (AttributeError), and an
    `error_description` that says "Unauthorized grant type" (RuntimeError).
    The frame of sasctl that read the answer still holds it."""
    found = None
    frame = getattr(exc, "__traceback__", None)
    while frame is not None:
        for value in list(frame.tb_frame.f_locals.values()):
            try:
                code = getattr(value, "status_code", None)
                asked = [getattr(value, "url", "")]
                if status is None:
                    asked += [getattr(earlier, "url", "") for earlier in getattr(value, "history", None) or []]
                if (code == status if status is not None else isinstance(code, int) and not isinstance(code, bool)) \
                        and any(TOKEN_PATH in str(address or "") for address in asked):
                    found = value
            except Exception:       # a local that cannot be asked
                pass
        frame = frame.tb_next
    return found


def _answer(exc: Exception) -> tuple:
    """(error, error_description) of the answer a refused sign-in carries,
    each whole and on one line, and "" where it cannot be read.

    The HTTPError of requests carries the answer of the token address as
    `response`, and its JSON body names both fields. sasctl's own
    AuthenticationError carries no response - sasctl has read the body by
    then (see `_bad_credentials`); for it, and for anything else sasctl
    raised over the answer, the answer is the one `_held_answer` finds."""
    response = getattr(exc, "response", None)
    try:
        body = (_held_answer(exc) if response is None else response).json()
    except Exception:       # no response, no JSON
        return "", ""
    if not isinstance(body, dict):
        return "", ""
    return tuple(one_line(body.get(key) or "") for key in ("error", "error_description"))


def _bad_credentials(exc: Exception) -> bool:
    """Whether a refused sign-in is the answer a wrong password got:
    `error_description` says BAD_CREDENTIALS.

    Either mdb reads that from the answer (`_answer`), or sasctl did:
    sasctl (1.11.8 was read, and a test runs the one installed) raises its
    AuthenticationError for the password grant where the token address
    answered 401 with "bad credentials" in the `error_description` of its
    JSON body, and passes another 401 on as the HTTPError of requests - or,
    for some bodies, as what `_held_answer` names."""
    return type(exc).__name__ == "AuthenticationError" or BAD_CREDENTIALS in _answer(exc)[1].lower()


def _final(exc: Exception) -> bool:
    """Whether a refused sign-in is one no repeat changes: the answer to a
    wrong password (`_bad_credentials`), or an `error_description` that says
    UNAUTHORIZED_GRANT - the client may not use the password grant at all."""
    return _bad_credentials(exc) or UNAUTHORIZED_GRANT in _answer(exc)[1].lower()


def _refusal(exc: Exception, grant: bool = False) -> str:
    """What a refused sign-in says, without the address of the token
    endpoint spelled out twice and without anything that was sent: the
    `error` and the `error_description` of the answer where it carries them.
    `grant` says that the refusal is one of the password grant - there
    sasctl's AuthenticationError stands for an `error_description` that mdb
    cannot read itself (`_bad_credentials`). Every other refused sign-in is
    a 401 of the token address (`_refused_sign_in`) and is said as one."""
    if type(exc).__name__ != "AuthenticationError":
        return _said(exc)
    text = str(exc).strip().rstrip(".") or type(exc).__name__
    if grant:
        return f'{text} - sasctl says so for an answer whose error_description says "Bad credentials"'
    return text


def _said(exc: Exception, status: int = 401) -> str:
    """The status of an answer of the token address, with the `error` and the
    `error_description` of its body where it carries them. An answer that
    came from where the token address redirected the sign-in names that
    path (no host, nothing behind it); a 2xx is one no token could be read
    from."""
    response = getattr(exc, "response", None)
    if response is None:
        response = _held_answer(exc, None)
    path = urlparse(str(getattr(response, "url", "") or "")).path
    where = TOKEN_PATH if not path or TOKEN_PATH in path else f"{path}, where {TOKEN_PATH} sent the sign-in on"
    if 200 <= status < 300:
        return f"HTTP {status} from {where}, without an access token mdb could read"
    error, description = _answer(exc)
    said = [f'{name} "{value}"' for name, value in (("error", error), ("error_description", description)) if value]
    return f"HTTP {status} from {where}" + (
        f" with {' and '.join(said)}" if said else " (no error_description could be read from the answer)")


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
    refusal is passed on; MDB_LOGIN_ATTEMPTS=1 switches the repeat off. A
    grant that is answered "Bad credentials" (`_bad_credentials`), which is
    what a wrong password got on 2026-10-13, is not asked again: a wrong
    password costs one failed sign-in. Neither is one answered "Unauthorized
    grant type" (`_final`): the client may not use the grant, whenever it is
    asked. Neither refusal is the one seen while sign-ins overlapped: the
    traceback saved on 2026-10-12 ends at the last
    `response.raise_for_status()` of sasctl's token request (core.py:1405 of
    sasctl 1.11.8), which a 401 reaches only with a JSON body whose
    `error_description` says neither.
    Anything that is not a refused sign-in (the server not reachable, a
    certificate, another status of the token address) is passed on at once.
    What is passed on carries the number of sign-ins made as SIGN_INS_MADE.
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
            if not _refused_sign_in(exc) or attempt >= attempts or _final(exc):
                try:
                    setattr(exc, SIGN_INS_MADE, attempt)
                except Exception:       # an exception that takes no attribute: the count is then not known
                    pass
                raise
            sleep(LOGIN_WAIT[min(attempt - 1, len(LOGIN_WAIT) - 1)])
