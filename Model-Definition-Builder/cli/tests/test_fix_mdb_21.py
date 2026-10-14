# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What the gate, the review and the live pass of round 21 found in mdb, each
with the input that showed it. The service is a fake in memory and the token
address a stand-in for the transport of requests: no test signs in anywhere,
and every key is made up."""
import pytest

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


# ---- a key file handed as the manifest is not printed -------------------------------------
def test_a_key_file_handed_as_the_manifest_is_refused_without_its_text(mdb):
    """review-21a case 2: `--manifest .env` printed the YAML error with the
    line it stopped at - a line of the .env with its value."""
    (mdb.path.parent / "keys.env").write_text(
        "OPENAI_API_KEY=sk-made-up-x\nPGVECTOR_RAG_PW=pg-made-up: password\n", encoding="utf-8")
    for args in (("credentials-add", "group", "G"), ("credentials-remove", "group", "G"), ("credentials-apply",),
                 ("credentials-report",)):
        code, said = mdb(*args, "--manifest", "keys.env")
        assert code == 2, args
        assert "keys.env is not valid YAML (the reader stopped at line 2, column 27). Nothing of its text is shown" in said
        assert "made-up" not in said and "password" not in said, said
    # the problem the reader names can be a word of the file: an alias
    (mdb.path.parent / "alias.yaml").write_text("identities: *made-up-alias\n", encoding="utf-8")
    for args in (("credentials-remove", "group", "G"), ("credentials-apply",)):
        code, said = mdb(*args, "--manifest", "alias.yaml")
        assert code == 2 and "alias.yaml is not valid YAML (the reader stopped at line 1" in said, said
        assert "made-up" not in said


# ---- credentials-add on an identity marked absent keeps a comment on the state line -----------
ABSENT_WITH_NOTE = ("source: .env\nidentities:\n  - type: group\n    id: Former\n"
                    "    state: absent   # ticket 4711: left the company\n    only: [OpenAI]\n"
                    "  - {type: group,  # a note inside the braces\n     id: Flow, state: absent}\n"
                    "  - {type: group, id: Gone,  # a note in front of the state\n     state: absent}\n")


@pytest.mark.parametrize("eol", ["\n", "\r\n"])
@pytest.mark.parametrize("final", [True, False])
def test_a_note_on_the_state_line_stays_when_the_identity_is_added_back(mdb, eol, final):
    """gate-21a C1/C2 and review-21a case 3: `credentials-add` took the whole
    `state:` line out, with the note behind it, and said nothing. The line
    is rewritten to `state: present` now, and the note stays byte for byte -
    as where the key shares its line with the dash."""
    text = ABSENT_WITH_NOTE.replace("\n", eol)
    if not final:
        text = text[:-len(eol)]
    mdb.path.write_bytes(text.encode("utf-8"))
    code, said = mdb("credentials-add", "group", "Former")
    assert code == 0 and "  state: absent -> present" in said
    assert mdb.path.read_bytes() == text.replace("state: absent   # ticket", "state: present   # ticket").encode("utf-8")
    # in braces: the state goes where no note stands in what is cut, and is rewritten where one does
    assert mdb("credentials-add", "group", "Flow")[0] == 0
    assert mdb("credentials-add", "group", "Gone")[0] == 0
    assert mdb.path.read_bytes() == text.replace("state: absent   # ticket", "state: present   # ticket").replace(
        "id: Flow, state: absent}", "id: Flow}").replace(
        f"state{eol}     state: absent}}", f"state{eol}     state: present}}").encode("utf-8")
    # without a note the line still goes, as before
    plain = text.replace("   # ticket 4711: left the company", "")
    mdb.path.write_bytes(plain.encode("utf-8"))
    assert mdb("credentials-add", "group", "Former")[0] == 0
    assert mdb.path.read_bytes() == plain.replace(f"    state: absent{eol}", "").encode("utf-8")
