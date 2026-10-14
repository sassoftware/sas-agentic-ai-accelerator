# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What the gate, the review and the live pass of round 21 found in mdb, each
with the input that showed it. The service is a fake in memory and the token
address a stand-in for the transport of requests: no test signs in anywhere,
and every key is made up."""
import json

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


@pytest.mark.parametrize("eol", ["\n", "\r\n"])
@pytest.mark.parametrize("final", [True, False])
def test_a_key_added_where_the_last_line_is_cut_leaves_no_empty_line(mdb, eol, final):
    """gate-21a C2 'state absent last key no newline': with `state: absent`
    as the last line of a file without a line end, `credentials-add <type>
    <id> --only <name>` left an empty line inside the identity."""
    head = "source: .env\nidentities:\n  - type: group\n    id: A\n".replace("\n", eol)
    text = head + "    state: absent" + (eol if final else "")
    mdb.path.write_bytes(text.encode("utf-8"))
    code, said = mdb("credentials-add", "group", "A", "--only", "OpenAI")
    assert code == 0, said
    assert mdb.path.read_bytes() == (head + "    only: [OpenAI]" + (eol if final else "")).encode("utf-8")


# ---- `identities: []` is refused in the words of the editor --------------------------------
def test_an_empty_list_in_brackets_is_refused_as_a_list_the_editor_cannot_edit(mdb):
    """gate-21a C2: `identities: []` ended `credentials-add` with the loader's
    sentence that a manifest that equips nobody is almost certainly a
    mistake - said to a command that would add one."""
    text = "source: .env\nidentities: []\n"
    for args in (("credentials-add", "group", "New"), ("credentials-remove", "group", "New")):
        mdb.path.write_text(text, encoding="utf-8")
        code, said = mdb(*args)
        assert code == 2 and mdb.path.read_text(encoding="utf-8") == text
        assert ("credentials.yaml: 'identities' is not a block list (one `- ` line per identity), so it cannot be "
                "edited in place") in said
        assert "equips nobody" not in said
    # apply and report still refuse a manifest that equips nobody, in the loader's words
    code, said = mdb("credentials-report")
    assert code == 2 and "'identities' must be a non-empty list" in said


# ---- the id of an identity is one segment of the address ---------------------------------
@pytest.mark.parametrize("who, segment", [
    ("Sales#EMEA", "Sales%23EMEA"), ("A?b=1", "A%3Fb=1"),
    ("x/../../other/groups/F", "x%2F..%2F..%2Fother%2Fgroups%2FF"), ("...", "..."),
    ("A B", "A%20B"), ("100%", "100%25"), ("ada@example.com", "ada@example.com"), ("sas-be-sa", "sas-be-sa"),
    ("a:b+c", "a:b+c"), ("Grüne", "Gr%C3%BCne"),
])
def test_the_id_is_one_segment_of_the_address_of_its_credential(who, segment):
    """review-21a case 8: an id with `#` or `?` was cut there by the HTTP
    client, so the request - the DELETE as well - was about another
    identity; an id with `/../` reached another path. The address requests
    sends is the one mdb builds."""
    requests = pytest.importorskip("requests")
    for kind in ("group", "user"):
        path = credentials.credential_path("my keys/x", credentials.Identity(type=kind, id=who))
        assert path == f"/credentials/domains/my%20keys%2Fx/{kind}s/{segment}"
        assert requests.Request("DELETE", "https://viya.example.com" + path).prepare().path_url == path


def test_an_id_that_is_a_step_of_the_path_is_refused(mdb):
    """`..` alone is read as a step of the path by the HTTP client, encoded
    or not: no request names it."""
    for who in (".", ".."):
        with pytest.raises(credentials.CredentialError, match="is read as a step of the path"):
            credentials.credential_path("d", credentials.Identity(type="group", id=who))
    mdb.path.write_text("source: .env\nidentities:\n  - {type: group, id: '..', state: absent}\n", encoding="utf-8")
    code, said = mdb("credentials-apply")
    assert code == 2 and "'..' cannot be named in the address of a credential" in said and mdb.site.calls == []


class _Answer(_Response):
    """An answer with a text, as requests gives one."""

    def __init__(self, status_code: int, payload: dict):
        super().__init__(status_code, payload)
        self.text = json.dumps(payload)


NO_GROUP = {"errorCode": 12310, "httpStatusCode": 404,
            "message": "The credential could not be stored because the group identity ZZ_NOGROUP does not exist."}


def test_a_refused_write_of_a_credential_names_the_reason_of_the_service(mdb):
    """live-21a: for a group that does not exist the apply printed only
    `storing the credential for group 'ZZ_VERIFY_R21a_NOGROUP' failed: HTTP
    404` and dropped the reason the service gave (errorCode 12310)."""
    from test_credentials_commands import SECRETS
    mdb.path.write_text("source: .env\nidentities:\n  - {type: group, id: ZZ_NOGROUP}\n"
                        "  - {type: group, id: Former, state: absent}\n", encoding="utf-8")
    mdb.site.holds("groups", "Former")
    put = mdb.site.put
    mdb.site.put = lambda path, **kw: _Answer(404, NO_GROUP) if path.endswith("ZZ_NOGROUP") else put(path, **kw)
    mdb.site.delete = lambda path, **_: _Answer(403, {"message": "You are not allowed [to] delete it."})
    code, said = mdb("credentials-apply")
    assert code == 1
    assert ("storing the credential for group 'ZZ_NOGROUP' failed: HTTP 404 The credential could not be stored "
            "because the group identity ZZ_NOGROUP does not exist. (errorCode 12310)") in said
    assert "removing the credential for group 'Former' failed: HTTP 403 You are not allowed [to] delete it." in said
    get = mdb.site.get
    mdb.site.get = lambda path, **_: _Answer(503, {"message": "The service is [not] available."})
    code, said = mdb("credentials-report")
    assert "reading the credential for group 'Former' failed: HTTP 503 The service is [not] available." in said
    mdb.site.get = get
    # an answer to the write that holds a value it carried is not shown - plain or as the service stores it
    for echoed in (SECRETS[0], credentials.encode({"OpenAI": SECRETS[0]})["OpenAI"]):
        mdb.site.put = lambda path, **kw: (_Answer(404, {"message": f"no such group for {echoed}"})
                                           if path.endswith("ZZ_NOGROUP") else put(path, **kw))
        code, said = mdb("credentials-apply")
        assert code == 1 and "storing the credential for group 'ZZ_NOGROUP' failed: HTTP 404\n" in said
        assert echoed not in said


def test_the_credentials_commands_write_their_dashes_as_the_other_commands_do(mdb):
    """live-21a: the credentials commands printed an em dash (`names only —
    no values`, `Dry run — nothing was written`), content-deploy and
    package-export ` - `. And `user zz_u: 1 entries stored.` (gate-21a C3)."""
    runs = [("credentials-init", "--manifest", "c.yaml"), ("credentials-init", "--manifest", "c.yaml"),
            ("credentials-add", "group", "LLMConsumers", "-m", "c.yaml"),
            ("credentials-add", "group", "LLMConsumers", "-m", "c.yaml"),
            ("credentials-add", "user", "ada", "-m", "c.yaml", "--only", "OpenAI"),
            ("credentials-add", "user", "bob", "-m", "c.yaml", "--only", "Nope"),
            ("credentials-apply", "-m", "c.yaml", "--dry-run"), ("credentials-apply", "-m", "c.yaml"),
            ("credentials-report", "-m", "c.yaml"), ("credentials-remove", "user", "ada", "-m", "c.yaml"),
            ("credentials-remove", "user", "ada", "-m", "c.yaml"), ("credentials-apply", "-m", "c.yaml"),
            ("credentials-remove", "group", "Gone", "-m", "c.yaml"), ("credentials-apply", "-m", "c.yaml"),
            ("credentials-report", "-m", "c.yaml"), ("credentials-add", "group", "X", "-m", "nothere/x.yaml",
                                                     "--env-file", "nothere.env")]
    for args in runs:
        mdb(*args)
    said = "".join(mdb.printed)
    assert "—" not in said
    assert "user ada: 1 entry stored." in said and "group LLMConsumers: 4 entries stored." in said
    assert "Dry run - nothing was written" in said and "(names only - no values)" in said
    assert "group Gone: no credential to remove - it is already gone." in said
    (mdb.path.parent / "bad.yaml").write_text("identities: [{type: group, id: A}]\n", encoding="utf-8")
    code, said = mdb("credentials-add", "group", "B", "-m", "bad.yaml")
    assert code == 2 and "cannot be edited in place - edit the file by hand" in said


def test_the_report_counts_a_credential_that_could_not_be_read_neither_way(mdb):
    """gate-21a C6: the row said `unreadable` and the line below the table
    `1 of 1 identities marked state: absent hold none` - every marked
    identity that was not found was counted as gone, also one mdb could not
    look at."""
    mdb.path.write_text("source: .env\nidentities:\n  - {type: group, id: Gone, state: absent}\n"
                        "  - {type: group, id: Left, state: absent}\n  - {type: group, id: Kept}\n"
                        "  - {type: user, id: ada}\n", encoding="utf-8")
    mdb.site.holds("groups", "Kept")
    get = mdb.site.get
    mdb.site.get = lambda path, **kw: _Response(403) if path.endswith(("/Gone", "/ada")) else get(path, **kw)
    code, said = mdb("credentials-report")
    assert code == 0
    assert "1 of 2 named identities hold a credential; 1 could not be read.\n" in said
    assert "1 of 2 identities marked `state: absent` hold none; 1 could not be read.\n" in said
    mdb.site.get = get
    code, said = mdb("credentials-report")
    assert "1 of 2 named identities hold a credential.\n" in said
    assert "2 of 2 identities marked `state: absent` hold none.\n" in said


def test_the_report_says_whom_a_group_credential_reaches(mdb):
    """review-21a, from FINDINGS item 10: apply prints the reach of a group
    credential, the report showed `yes` for a group without it - the picture
    a colleague's failed rollout had."""
    from mdb.viya.credentials import GROUP_REACH
    mdb.path.write_text("source: .env\nidentities:\n  - {type: group, id: PromptEngineers}\n"
                        "  - {type: user, id: ada}\n  - {type: group, id: Gone, state: absent}\n", encoding="utf-8")
    code, said = mdb("credentials-report")
    assert code == 0 and GROUP_REACH not in said                 # nobody holds one
    mdb.site.holds("users", "ada")
    mdb.site.holds("groups", "Gone")                             # a group that is to lose its credential
    code, said = mdb("credentials-report")
    assert GROUP_REACH not in said
    mdb.site.holds("groups", "PromptEngineers")
    code, said = mdb("credentials-report")
    assert said.count(GROUP_REACH) == 1


@pytest.mark.parametrize("order", [("", "state: absent, "), ("state: absent, ", "")])
def test_apply_and_report_refuse_an_identity_listed_twice(mdb, order):
    """review-21a case 1: one identity listed present and absent was
    written and deleted (or the other way round) in one run that ended
    `Done.`, and the report told the admin to run apply again, which never
    converges. credentials-add and -remove refused such a manifest."""
    first, second = order
    mdb.path.write_text(f"source: .env\nidentities:\n  - {{{first}type: group, id: LLMConsumers}}\n"
                        f"  - {{{second}type: group, id: LLMConsumers}}\n  - {{type: user, id: ada}}\n"
                        "  - {type: user, id: ada}\n  - {type: user, id: bob}\n", encoding="utf-8")
    for args in (("credentials-apply",), ("credentials-apply", "--dry-run"), ("credentials-report",)):
        code, said = mdb(*args)
        assert code == 2, args
        assert ("credentials.yaml: group 'LLMConsumers' is listed 2 times; user 'ada' is listed 2 times - make "
                "each one entry by hand first. Nothing was asked of SAS Viya.") in said
    assert mdb.site.calls == []
    assert mdb("credentials-add", "group", "LLMConsumers")[0] == 2


def test_a_domain_the_manifest_does_not_name_is_refused(mdb):
    """review-21a case 7: with a manifest, --domain of apply and report was
    not read and nothing said so - `mdb credentials-apply --domain
    zz_throwaway` went to agentic-ai-keys, and since this release it also
    removes there."""
    mdb.path.write_text("source: .env\nidentities:\n  - {type: group, id: A, state: absent}\n", encoding="utf-8")
    for args in (("credentials-apply", "--dry-run"), ("credentials-apply",), ("credentials-report",)):
        code, said = mdb(*args, "--domain", "zz_throwaway")
        assert code == 2, args
        assert ("--domain zz_throwaway is read only with --identity: credentials.yaml names the credential domain "
                "agentic-ai-keys. Nothing was asked of SAS Viya.") in said
    assert mdb.site.calls == []
    # the domain the manifest names, given or not, is the run's
    for given in ((), ("--domain", "agentic-ai-keys")):
        code, said = mdb("credentials-apply", "--dry-run", *given)
        assert code == 0 and "Credential domain 'agentic-ai-keys'" in said
    # with --identity the option names the domain, and its default is still agentic-ai-keys
    code, said = mdb("credentials-report", "--identity", "A", "--domain", "zz_throwaway")
    assert code == 0 and "Credential domain 'zz_throwaway'" in said
    assert mdb.site.calls[-1] == ("GET", "/credentials/domains/zz_throwaway/groups/A")
    code, said = mdb("credentials-apply", "--identity", "A")
    assert code == 0 and said.rstrip().endswith("Check it with `mdb credentials-report --identity A`.")
    assert ("PUT", "/credentials/domains/agentic-ai-keys/groups/A") in mdb.site.calls


EMPTY_FILE = [("credentials-init", "--manifest"), ("credentials-init", "--file"), ("credentials-init", "--env-file"),
              ("credentials-add group G", "--manifest"), ("credentials-add group G", "--env-file"),
              ("credentials-add group G", "--source"), ("credentials-remove group G", "--manifest"),
              ("credentials-apply", "--manifest"), ("credentials-apply", "--env-file"),
              ("credentials-apply", "--keys-file"), ("credentials-apply --identity G", "--manifest"),
              ("credentials-report", "--manifest")]


@pytest.mark.parametrize("value", ["", "  "])
@pytest.mark.parametrize("command, option", EMPTY_FILE)
def test_an_empty_file_of_the_credentials_commands_is_refused(mdb, command, option, value):
    """gate-21a C5, review-21a: `--manifest ""` was read as not given -
    credentials-add wrote ./credentials.yaml, apply and report read it (an
    apply deletes too), credentials-init said `. already exists`; an empty
    --env-file and --source were passed over. As for --only and --domain,
    an empty value is refused before anything is read or written."""
    listing = sorted(path.name for path in mdb.path.parent.iterdir())
    code, said = mdb(*command.split(), option, value)
    named = "--manifest / --file" if command == "credentials-init" and option != "--env-file" else option
    assert code == 2 and (f"{named} is empty - what a variable of the shell that is not set gives - and names no "
                          "file. Nothing was done.") in said, said
    assert sorted(path.name for path in mdb.path.parent.iterdir()) == listing and mdb.site.calls == []


@pytest.mark.parametrize("args, line", [
    (["options-save", "--file", ""], "--file is empty"), (["options-restore", "--file", ""], "--file is empty"),
    (["options-restore", "--file", "."], ". is a folder, not an options file."),
    (["options-save", "--file", "."], ". is a folder; --file names the options file to write."),
    (["package-export", "--out", ""], "--out is empty"), (["builders-import", "--out", ""], "--out is empty"),
    (["setup", "--out", ""], "--out is empty"),
    (["deploy", "--all", "--registry", "r.example.com", "--host", "h.example.com", "--out", ""], "--out is empty"),
])
def test_an_empty_file_or_folder_of_the_other_commands_is_refused(monkeypatch, tmp_path, args, line):
    """gate-21a N5, review-21a: `package-export --out ""` went on to the
    sign-in, `options-restore --file ""` and `--file .` ended with a
    PermissionError traceback (also at b4dc710), `options-save --file ""`
    reached the sign-in."""
    from types import SimpleNamespace
    from pathlib import Path
    import io
    from rich.console import Console
    from typer.testing import CliRunner
    from mdb import cli
    repository = Path(__file__).resolve().parents[3]
    out = io.StringIO()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "console", Console(file=out, width=1000, force_terminal=False, emoji=False))
    monkeypatch.setattr(cli, "Context", lambda: SimpleNamespace(repo=repository))
    monkeypatch.setattr(cli, "_viya_session", lambda: pytest.fail("signed in"))
    result = CliRunner().invoke(cli.app, args)
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
    assert result.exit_code in (1, 2) and line in out.getvalue(), out.getvalue()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("root", ["/", "//", "Teams/x"])
@pytest.mark.parametrize("command", ["content-deploy", "builders-import"])
def test_a_content_root_that_is_no_folder_is_named_so(monkeypatch, tmp_path, command, root):
    """gate-21a N5: `--content-root /` was refused with 'must be a SAS
    Content path, starting with a slash' - it starts with one."""
    from types import SimpleNamespace
    from pathlib import Path
    import io
    from rich.console import Console
    from typer.testing import CliRunner
    from mdb import cli
    repository = Path(__file__).resolve().parents[3]
    out = io.StringIO()
    package = tmp_path / "p.json"
    package.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli, "console", Console(file=out, width=1000, force_terminal=False, emoji=False))
    monkeypatch.setattr(cli, "Context", lambda: SimpleNamespace(repo=repository))
    monkeypatch.setattr(cli, "_viya_session", lambda: pytest.fail("signed in"))
    args = [command, "--content-root", root] + ([str(package)] if command == "builders-import" else [])
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 2, (result.exception, out.getvalue())
    assert out.getvalue() == ("--content-root must name a SAS Content folder, starting with a slash - and not / "
                              "itself, the root of SAS Content.\n")


#: An answer of a server or of a model with what a terminal acts on: clear the screen, move the cursor, set the
#: title (OSC ... BEL), the C1 CSI byte, NUL, DEL.
ESCAPES = "refused \x1b[2J\x1b[1;1H \x1b]0;owned\x07 tail \x9b31m \x00 \x7f [/red] done"


def test_a_value_reaches_the_terminal_without_a_control_character():
    """gate-21a N1, review-21a: ESC, an OSC title sequence, the C1 CSI byte,
    NUL and DEL of a value reached the terminal, with markup on and off, in
    a table cell and with force_terminal; rich takes out the bell only, so
    an OSC sequence lost its end and swallowed what followed."""
    import io
    from rich.table import Table
    from mdb import cli
    kept = "refused [2J[1;1H ]0;owned tail 31m   [/red] done"
    for terminal in (False, True):
        console = cli._Console(file=io.StringIO(), width=1000, force_terminal=terminal, emoji=False,
                               color_system=None)
        console.print(f"[red]{cli._plain(ESCAPES)}[/red]", highlight=False, soft_wrap=True)
        console.print(ESCAPES, markup=False, highlight=False, soft_wrap=True)
        table = Table(title=cli._plain(ESCAPES))
        table.add_column("Value")
        table.add_row(cli._plain(ESCAPES))
        console.print(table)
        printed = console.file.getvalue()
        assert not cli._CONTROL.search(printed.replace("\n", "")), repr(printed)
        assert printed.count(kept) == 4, repr(printed)
    # the console of mdb is one of these, and a prompt reads its question without them
    assert type(cli.console) is cli._Console
    shown = cli._Console(file=io.StringIO(), width=1000, force_terminal=False, emoji=False)
    assert cli.Prompt.ask(f"Delete '{cli._plain(ESCAPES)}'?", console=shown, stream=io.StringIO("x\n")) == "x"
    assert shown.file.getvalue().startswith(f"Delete '{kept}'?")
    # --json output keeps them, as escapes
    shown = cli._Console(file=io.StringIO(), width=1000, force_terminal=False, emoji=False)
    shown.print_json(json.dumps([ESCAPES, "Grüne"]))
    assert not cli._CONTROL.search(shown.file.getvalue().replace("\n", ""))
    assert json.loads(shown.file.getvalue()) == [ESCAPES, "Grüne"]


@pytest.mark.parametrize("url, shown, problem", [
    ("http://http://127.0.0.1:4981", None, "it holds a second // behind the scheme"),
    ("https://https://viya.example.com", None, "it holds a second // behind the scheme"),
    ("https://https//viya.example.com", None, "it holds a second // behind the scheme"),
    ("https:// viya.example.com", None, "it holds a blank"), ("https://viya example.com", None, "it holds a blank"),
    ("https//viya.example.com", None, "it holds a slash, and no scheme (https://) stands in front of the host"),
    ("//viya.example.com", None, "it holds a slash, and no scheme (https://) stands in front of the host"),
    ("http://someone:secret@127.0.0.1:4981", "http://***@127.0.0.1:4981",
     "it holds an @, as a user or a password in front of the host does - mdb signs in with SAS_VIYA_USER + "
     "SAS_VIYA_PASSWORD, "
     "SAS_VIYA_TOKEN or a SAS Viya CLI login"),
    ("http://u:pa/ss@viya.example.com", "http://***@viya.example.com",
     "it holds an @, as a user or a password in front of the host does - mdb signs in with SAS_VIYA_USER + "
     "SAS_VIYA_PASSWORD, SAS_VIYA_TOKEN or a SAS Viya CLI login"),
    ("u:secret@viya.example.com", "***@viya.example.com",
     "it holds a colon, and no scheme (https://) stands in front of the host"),
    ("https://ada@viya.example.com/SASLogon", "https://***@viya.example.com/SASLogon",
     "it holds an @, as a user or a password in front of the host does - mdb signs in with SAS_VIYA_USER + "
     "SAS_VIYA_PASSWORD, "
     "SAS_VIYA_TOKEN or a SAS Viya CLI login"),
])
def test_an_address_sasctl_would_read_another_way_is_refused(env, url, shown, problem):
    """gate-21a N8 and the last case of its sign-in run: a doubled scheme
    passed the check and sasctl asked the host `http` at port 80, which
    ended with a ConnectionError traceback; a blank in the host,
    `https//host` and `//host` passed as well. And an address with
    user:password@ was printed whole in the first line of a command."""
    from test_mdb_21 import ADDRESS
    env.setenv(s.URL_ENV, url)
    env.setenv(s.USER_ENV, "u")
    env.setenv(s.PASSWORD_ENV, "p")
    with pytest.raises(s.ViyaConfigError) as caught:
        s.resolve_auth()
    assert str(caught.value) == f"SAS_VIYA_URL is '{shown or url}', " + ADDRESS.format(problem)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("url", ["https://viya.example.com", "https://viya.example.com/", "http://127.0.0.1:4981",
                                 "viya.example.com", "HTTPS://Viya.Example.com:8443/SASLogon?x=1"])
def test_an_address_mdb_took_before_is_taken(env, url):
    env.setenv(s.URL_ENV, url)
    env.setenv(s.TOKEN_ENV, "not-a-jwt")
    assert s.resolve_auth().url == url


URL = "https://viya.example.com"
TOKEN_URL = f"{URL}/SASLogon/oauth/token#password"


@pytest.mark.parametrize("answer, said", [
    ((200, "<html>login</html>", None),
     "HTTP 200 from /SASLogon/oauth/token, without an access token mdb could read"),
    ((200, '{"hello": 1}', None), "HTTP 200 from /SASLogon/oauth/token, without an access token mdb could read"),
    ((404, "", f"{URL}/elsewhere#password"),
     "HTTP 404 from /elsewhere, where /SASLogon/oauth/token sent the sign-in on (no error_description could be "
     "read from the answer)"),
    ((401, '{"error": "unauthorized", "error_description": "Full authentication is required"}',
      f"{URL}/SASLogon/login#password"),
     'HTTP 401 from /SASLogon/login, where /SASLogon/oauth/token sent the sign-in on with error "unauthorized" '
     'and error_description "Full authentication is required"'),
])
def test_an_answer_of_the_token_address_without_a_token_is_one_line(env, monkeypatch, answer, said):
    """gate-21a, sign-in against a stand-in: a redirect whose target answers
    an error, a 200 whose body is no JSON and a 200 without access_token
    ended with a traceback and exit code 1. The real sasctl, with the
    transport of requests replaced; skipped where sasctl is not installed."""
    import sys
    pytest.importorskip("sasctl")
    import requests
    status, body, landed = answer
    posted = []

    def post(self, url, **kwargs):
        posted.append(url)
        response = requests.Response()
        response.status_code, response.url = status, landed or url
        response._content = body.encode("utf-8")
        if landed:
            earlier = requests.Response()
            earlier.status_code, earlier.url = 302, url
            response.history = [earlier]
        return response

    monkeypatch.setattr(requests.Session, "post", post)
    monkeypatch.setitem(sys.modules, "sasctl", pytest.importorskip("sasctl"))
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    env.setenv(s.URL_ENV, URL)
    env.setenv(s.USER_ENV, "u")
    env.setenv(s.PASSWORD_ENV, "s3cret-p")
    env.setenv("SAS_VIYA_VERIFY_SSL", "false")
    with pytest.raises(s.ViyaSignInError) as caught:
        s.create_session()
    assert posted == [TOKEN_URL]
    assert str(caught.value) == (
        f"SAS Viya at {URL} did not answer the sign-in of u with SAS_VIYA_USER + SAS_VIYA_PASSWORD (1 sign-in "
        f"made) with a token: {said}. mdb reads only HTTP 401 as a refusal of the credentials, and asks no other "
        "answer again: run the command again, and where the answer stays the same, look at SAS Logon.")
    assert "s3cret-p" not in str(caught.value)


def test_the_requests_of_apply_name_the_identity_whole(mdb):
    mdb.path.write_text("source: .env\nidentities:\n  - {type: group, id: 'Sales#EMEA'}\n"
                        "  - {type: group, id: '../x', state: absent}\n", encoding="utf-8")
    assert mdb("credentials-apply")[0] == 0
    assert mdb.site.calls == [("GET", "/credentials/domains/agentic-ai-keys/groups/Sales%23EMEA"),
                              ("GET", "/credentials/domains/agentic-ai-keys/groups/..%2Fx"),
                              ("PUT", "/credentials/domains/agentic-ai-keys"),
                              ("PUT", "/credentials/domains/agentic-ai-keys/groups/Sales%23EMEA"),
                              ("DELETE", "/credentials/domains/agentic-ai-keys/groups/..%2Fx")]
