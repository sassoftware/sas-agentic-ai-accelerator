# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What the second check of round 23 found in mdb at 6cae4ec, each as a
class. Nothing signs in anywhere: the transport of requests is replaced.

- gate: SAS_VIYA_URL with an empty port (`http://127.0.0.1:`) was taken,
  and the default port was asked. The tests take every form of a port -
  empty, no number, 0, out of range, and ports that can be used - with and
  without a path.
"""
import pytest

from mdb.viya import session as s
from test_fix_mdb_23 import _answering, _main, no_dotenv  # noqa: F401  (no_dotenv is a fixture)
from test_mdb_21 import ADDRESS, NO_PORT
from test_mdb_23 import _sign_in_env
from test_session import env, fake_sasctl  # noqa: F401


# ---- gate: every form of a port --------------------------------------------------------------------------
REFUSED = ["", "4983x", "0", "65536", "99999"]
TAKEN = ["1", "4983", "65535"]
TAILS = ["", "/", "/SASLogon"]


def _ways(env) -> list:
    """The password and the token, one after the other, each alone."""
    def way(credentials):
        for name in (s.USER_ENV, s.PASSWORD_ENV, s.TOKEN_ENV):
            env.delenv(name, raising=False)
        for name, value in credentials.items():
            env.setenv(name, value)
    return [lambda: way({s.USER_ENV: "u", s.PASSWORD_ENV: "p"}), lambda: way({s.TOKEN_ENV: "not-a-jwt"})]


@pytest.mark.parametrize("tail", TAILS)
@pytest.mark.parametrize("port", REFUSED)
def test_a_port_that_is_no_number_from_1_to_65535_is_refused_before_any_request(env, fake_sasctl, port, tail):
    """gate (round 23, second check): `http://127.0.0.1:` - a colon with
    nothing behind it - was asked at port 80; urlparse reads an empty port
    as no port. Each form is refused with the line of a port that is no
    number, for a password and for a token, and sasctl is not called."""
    url = f"http://127.0.0.1:{port}{tail}"
    env.setenv(s.URL_ENV, url)
    for way in _ways(env):
        way()
        with pytest.raises(s.ViyaConfigError) as caught:
            s.create_session()
        assert str(caught.value) == f"SAS_VIYA_URL is {url!r}, " + ADDRESS.format(NO_PORT)
    assert fake_sasctl == []


@pytest.mark.parametrize("tail", TAILS)
@pytest.mark.parametrize("port", TAKEN)
def test_a_port_from_1_to_65535_reaches_sasctl(env, fake_sasctl, port, tail):
    url = f"http://127.0.0.1:{port}{tail}"
    env.setenv(s.URL_ENV, url)
    env.setenv(s.TOKEN_ENV, "not-a-jwt")
    s.create_session()
    assert fake_sasctl == [((url,), {"token": "not-a-jwt", "verify_ssl": True, "port": int(port)})]


@pytest.mark.parametrize("way", ["password", "token"])
@pytest.mark.parametrize("tail", TAILS)
def test_mdb_list_with_an_empty_port_ends_with_the_one_line_and_sends_nothing(
        monkeypatch, capsys, no_dotenv, tmp_path, way, tail):
    """The same through `mdb list` as the console script runs it: exit
    code 2, the one line, and no request."""
    _sign_in_env(monkeypatch, way, home=tmp_path)
    url = f"http://127.0.0.1:{tail}"
    monkeypatch.setenv(s.URL_ENV, url)
    sent = _answering(monkeypatch, 500)
    code, text = _main(monkeypatch, capsys, "list")
    assert (code, sent) == (2, []), (code, sent, text)
    assert text == f"SAS_VIYA_URL is {url!r}, " + ADDRESS.format(NO_PORT) + "\n"
