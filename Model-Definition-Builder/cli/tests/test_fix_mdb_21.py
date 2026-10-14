# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What the gate, the review and the live pass of round 21 found in mdb, each
with the input that showed it. The service is a fake in memory and the token
address a stand-in for the transport of requests: no test signs in anywhere,
and every key is made up."""
from mdb.viya import credentials
from mdb.viya import session as s
from test_credentials_commands import _Response, mdb  # noqa: F401
from test_session import env  # noqa: F401


# ---- a value in a sentence of mdb's own stands between quotes as it is -------------------
def test_an_id_with_a_backslash_keeps_one_in_the_sentence_of_mdb(mdb):
    """gate-21a C6: `reading the credential for group 'G [/x] \\\\\\\\'` - repr
    doubled every backslash of the id in the row of a credential that could
    not be read."""
    who = credentials.Identity(type="group", id="G [/x] \\")
    session = type("S", (), {"get": lambda self, path: _Response(403), "delete": lambda self, path: _Response(403),
                             "put": lambda self, path, **_: _Response(500)})()
    for call in (lambda: credentials.fetch_credential(session, "d", who),
                 lambda: credentials.delete_credential(session, "d", who),
                 lambda: credentials.put_credential(session, "d", who, {})):
        try:
            call()
        except credentials.CredentialError as exc:
            assert "group 'G [/x] \\' failed: HTTP" in str(exc) and "\\\\" not in str(exc)
        else:
            raise AssertionError("no refusal")
    try:
        credentials.ensure_domain(session, "d\\x")
    except credentials.CredentialError as exc:
        assert "the domain 'd\\x' failed" in str(exc)
    assert mdb("credentials-add", "group", "G [/x] \\")[0] == 0
    mdb.site.get = lambda path, **_: _Response(403)
    code, said = mdb("credentials-report")
    assert code == 0 and "reading the credential for group 'G [/x] \\' failed: HTTP 403" in said
    assert "\\\\" not in said


def test_an_address_with_a_backslash_is_named_with_one(env):
    env.setenv(s.URL_ENV, "C:\\viya")
    env.setenv(s.TOKEN_ENV, "not-a-jwt")
    try:
        s.resolve_auth()
    except s.ViyaConfigError as exc:
        assert str(exc).startswith("SAS_VIYA_URL is 'C:\\viya', which mdb cannot use")
    else:
        raise AssertionError("not refused")
