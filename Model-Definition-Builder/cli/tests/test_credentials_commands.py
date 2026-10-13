# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""`mdb credentials-add`, `mdb credentials-remove`, and what `state: absent`
does to an apply and a report.

Two properties carry most of these tests.

The manifest is mostly comments, and the two commands edit it as text: every
byte outside the one identity has to be what it was. So the tests compare
whole files, bytes with bytes, on a manifest `credentials-init` wrote and on
one written by hand in flow style, with either kind of line end.

Nothing is removed unless the manifest says `state: absent`, and the two
commands never talk to the server: a removal is a line in a file until the
next apply.

The service is a fake in memory. No test signs in anywhere, and every key in
the `.env` of a test is made up.
"""
import io
import json
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from mdb import cli
from mdb.viya import credentials
from mdb.viya.credentials import (
    GROUP_REACH, PLACEHOLDER_ID, CredentialError, Manifest, build_steps,
    edit_identity, scaffold,
)

#: Made up, and distinctive enough that finding one in an output is a leak.
SECRETS = ("sk-made-up-openai", "or-made-up-openrouter", "rag_made_up_user",
           "pg-made-up-password")
ENV = (f"OPENAI_API_KEY={SECRETS[0]}\n"
       f"OPENROUTER_API_KEY={SECRETS[1]}\n"
       f"PGVECTOR_RAG_USER={SECRETS[2]}\n"
       f"PGVECTOR_RAG_PW={SECRETS[3]}\n")
ENTRIES = ("OpenAI", "OpenRouter", "PGVECTOR_RAG_PW", "PGVECTOR_RAG_USER")

DOMAIN = "/credentials/domains/agentic-ai-keys"

#: The commands that edit a file and must not sign in to do it.
OFFLINE = ("credentials-init", "credentials-add", "credentials-remove")


class _Response:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no body")
        return self._payload


class _Site:
    """The credentials service in memory: which items exist, and every call
    that was made. Like the real one it answers a read with metadata only."""

    def __init__(self):
        self.held: dict = {}
        self.calls: list = []
        self.refuse_delete = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def holds(self, kind: str, who: str, by: str = "admin") -> str:
        path = f"{DOMAIN}/{kind}/{who}"
        self.held[path] = {"modifiedBy": by, "modifiedTimeStamp": "2026-10-01T09:00:00Z"}
        return path

    def get(self, path, **_):
        self.calls.append(("GET", path))
        return _Response(200, self.held[path]) if path in self.held else _Response(404)

    def put(self, path, data=None, headers=None):
        self.calls.append(("PUT", path))
        if path != DOMAIN:
            self.held[path] = {"modifiedBy": "tester",
                               "modifiedTimeStamp": "2026-10-13T09:00:00Z",
                               "sent": json.loads(data)}
        return _Response(200, {})

    def delete(self, path, **_):
        self.calls.append(("DELETE", path))
        if self.refuse_delete:
            return _Response(self.refuse_delete)
        if path not in self.held:
            return _Response(404)
        del self.held[path]
        return _Response(204)

    def made(self, method: str) -> list:
        return [path for verb, path in self.calls if verb == method]


@pytest.fixture
def mdb(tmp_path, monkeypatch):
    """Runs a command in a directory that holds a made-up `.env`. Returns
    (exit code, everything printed). `mdb.site` is the fake service,
    `mdb.path` the default manifest, `mdb.printed` every output so far."""
    (tmp_path / ".env").write_text(ENV, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    site = _Site()

    def run(*args):
        out = io.StringIO()
        monkeypatch.setattr(cli, "console", Console(file=out, width=1000, force_terminal=False))
        monkeypatch.setattr(
            cli, "_viya_session",
            (lambda: pytest.fail(f"{args[0]} signed in to edit a file"))
            if args[0] in OFFLINE else (lambda: site))
        result = CliRunner().invoke(cli.app, list(args))
        if result.exception is not None and not isinstance(result.exception, SystemExit):
            raise result.exception
        printed = out.getvalue() + result.output
        run.printed.append(printed)
        return result.exit_code, printed

    run.site = site
    run.path = tmp_path / "credentials.yaml"
    run.printed = []
    return run


def _eol(data: bytes) -> bytes:
    return b"\r\n" if b"\r\n" in data else b"\n"


def _lines(eol: bytes, *lines: str) -> bytes:
    """Whole lines, each ending in the line end of the file under test."""
    return b"".join(line.encode("utf-8") + eol for line in lines)


def _row(printed: str, who: str) -> str:
    """The line of the printed table that is about one identity."""
    return next(line for line in printed.splitlines() if f" {who} " in line)


def _ids(path: Path) -> list:
    return [(who.type, who.id, who.only, who.state) for who in Manifest.load(path).identities]


# ---- a manifest `credentials-init` wrote ----------------------------------
def test_the_first_add_takes_the_place_of_the_placeholder_and_changes_nothing_else(mdb):
    assert mdb("credentials-init")[0] == 0
    before = mdb.path.read_bytes()
    assert before.count(b"id: " + PLACEHOLDER_ID.encode()) == 1

    code, said = mdb("credentials-add", "group", "LLMConsumers")

    assert code == 0
    assert mdb.path.read_bytes() == before.replace(
        b"id: " + PLACEHOLDER_ID.encode(), b"id: LLMConsumers")
    assert f"in place of the placeholder {PLACEHOLDER_ID}" in said
    assert "mdb credentials-apply --manifest credentials.yaml --dry-run" in said
    assert _ids(mdb.path) == [("group", "LLMConsumers", (), "present")]


def test_a_user_in_place_of_the_placeholder_changes_its_type_line_too(mdb):
    mdb("credentials-init")
    before = mdb.path.read_bytes()
    eol = _eol(before)

    assert mdb("credentials-add", "user", "sas-be-sa")[0] == 0

    assert mdb.path.read_bytes() == before.replace(
        _lines(eol, "  - type: group", f"    id: {PLACEHOLDER_ID}"),
        _lines(eol, "  - type: user", "    id: sas-be-sa"))
    assert _ids(mdb.path) == [("user", "sas-be-sa", (), "present")]


def test_a_second_add_is_its_own_block_after_the_last_identity(mdb):
    """Every comment of the scaffold, the commented examples below the
    identities included, is where it was: the new identity is a splice."""
    mdb("credentials-init")
    mdb("credentials-add", "group", "LLMConsumers")
    before = mdb.path.read_bytes()
    eol = _eol(before)

    code, said = mdb("credentials-add", "group", "RAGEngineers",
                     "--only", "PGVECTOR_RAG_USER,PGVECTOR_RAG_PW")

    last = _lines(eol, "    id: LLMConsumers")
    assert before.count(last) == 1
    assert mdb.path.read_bytes() == before.replace(last, last + _lines(
        eol, "", "  - type: group", "    id: RAGEngineers",
        "    only: [PGVECTOR_RAG_USER, PGVECTOR_RAG_PW]"))
    assert code == 0
    assert "Added group RAGEngineers to credentials.yaml." in said
    assert "only: PGVECTOR_RAG_USER, PGVECTOR_RAG_PW" in said
    assert _ids(mdb.path) == [
        ("group", "LLMConsumers", (), "present"),
        ("group", "RAGEngineers", ("PGVECTOR_RAG_USER", "PGVECTOR_RAG_PW"), "present")]


def test_an_identity_that_is_there_is_not_written_a_second_time(mdb):
    mdb("credentials-init")
    mdb("credentials-add", "group", "LLMConsumers")
    before = mdb.path.read_bytes()

    code, said = mdb("credentials-add", "group", "LLMConsumers")

    assert code == 0
    assert mdb.path.read_bytes() == before
    assert "group LLMConsumers is already in credentials.yaml" in said
    assert "nothing was changed" in said
    assert "mdb credentials-apply --manifest credentials.yaml --dry-run" in said
    # a group and a user of one name are two identities
    assert mdb("credentials-add", "user", "LLMConsumers")[0] == 0
    assert [who[:2] for who in _ids(mdb.path)] == [("group", "LLMConsumers"), ("user", "LLMConsumers")]


def test_only_and_source_update_the_entry_that_is_there_and_say_what_changed(mdb, tmp_path):
    (tmp_path / "envs").mkdir()
    (tmp_path / "envs" / "production.env").write_text(ENV, encoding="utf-8")
    mdb("credentials-init")
    mdb("credentials-add", "group", "LLMConsumers")
    before = mdb.path.read_bytes()
    eol = _eol(before)
    line = _lines(eol, "    id: LLMConsumers")

    code, said = mdb("credentials-add", "group", "LLMConsumers", "--only", "OpenRouter")
    assert code == 0
    narrowed = before.replace(line, line + _lines(eol, "    only: [OpenRouter]"))
    assert mdb.path.read_bytes() == narrowed
    assert "Updated group LLMConsumers in credentials.yaml:" in said
    assert "only: every entry -> OpenRouter" in said

    code, said = mdb("credentials-add", "group", "LLMConsumers",
                     "--only", "OpenRouter", "--only", "OpenAI",
                     "--source", "envs/production.env")
    assert code == 0
    assert mdb.path.read_bytes() == before.replace(line, line + _lines(
        eol, "    only: [OpenRouter, OpenAI]", "    source: envs/production.env"))
    assert "only: OpenRouter -> OpenRouter, OpenAI" in said
    assert "source: the manifest's source -> envs/production.env" in said
    assert Manifest.load(mdb.path).identities[0].source == (tmp_path / "envs" / "production.env").resolve()

    # the same values again are no change, in whichever order they are typed
    written = mdb.path.read_bytes()
    code, said = mdb("credentials-add", "group", "LLMConsumers", "--only", "OpenAI,OpenRouter",
                     "--source", "envs/production.env")
    assert code == 0 and mdb.path.read_bytes() == written
    assert "nothing was changed" in said


def test_an_only_name_the_source_does_not_carry_is_refused_in_the_words_of_the_apply(mdb):
    mdb("credentials-init")
    mdb("credentials-add", "group", "LLMConsumers")
    before = mdb.path.read_bytes()

    code, said = mdb("credentials-add", "group", "RAGEngineers",
                     "--only", "PGVECTOR_RAG_PW,OPENAI_API_KEY")

    assert code == 2
    assert mdb.path.read_bytes() == before
    assert "Nothing was written to credentials.yaml" in said
    # what an apply says about the same identity, had it been written by hand
    by_hand = mdb.path.with_name("by-hand.yaml")
    by_hand.write_text("source: .env\nidentities:\n  - {type: group, id: RAGEngineers, "
                       "only: [PGVECTOR_RAG_PW, OPENAI_API_KEY]}\n", encoding="utf-8")
    problem = build_steps(Manifest.load(by_hand), lambda who: None)[0].problem
    assert "OPENAI_API_KEY" in problem and problem in said

    # the same refusal on an identity that is already listed
    code, said = mdb("credentials-add", "group", "LLMConsumers", "--only", "Anthropic")
    assert code == 2 and problem.replace("OPENAI_API_KEY", "Anthropic") in said
    assert mdb.path.read_bytes() == before


def test_a_source_that_is_not_there_is_refused_before_it_is_written(mdb):
    mdb("credentials-init")
    before = mdb.path.read_bytes()

    code, said = mdb("credentials-add", "group", "FraudAnalytics", "--source", "envs/nowhere.env")

    assert code == 2
    assert "source file not found" in said
    assert mdb.path.read_bytes() == before


def test_without_a_manifest_the_add_writes_what_init_writes_with_the_identity_in_it(mdb, tmp_path):
    assert not mdb.path.exists()

    code, said = mdb("credentials-add", "group", "LLMConsumers")

    assert code == 0
    created = mdb.path.read_bytes()
    assert "Wrote credentials.yaml with group LLMConsumers as its identity" in said
    assert f"listing {len(ENTRIES)} available entries" in said
    for name in ENTRIES:
        assert f"#   {name}".encode() in created
    assert _ids(mdb.path) == [("group", "LLMConsumers", (), "present")]
    assert Manifest.load(mdb.path).source == (tmp_path / ".env").resolve()
    # the same file `credentials-init` writes, but for the identity
    mdb.path.unlink()
    mdb("credentials-init")
    assert created == mdb.path.read_bytes().replace(
        b"id: " + PLACEHOLDER_ID.encode(), b"id: LLMConsumers")


def test_without_a_manifest_and_without_a_source_nothing_is_written(mdb, tmp_path):
    (tmp_path / ".env").unlink()

    code, said = mdb("credentials-add", "group", "LLMConsumers")

    assert code == 2
    assert "No .env at .env" in said and "--env-file" in said
    assert not mdb.path.exists()

    # the option `credentials-init` has for the source works here as well
    (tmp_path / "keys.env").write_text(ENV, encoding="utf-8")
    target = tmp_path / "records" / "credentials.yaml"
    code, said = mdb("credentials-add", "user", "ada", "--only", "OpenAI",
                     "--env-file", "keys.env", "--manifest", str(target))
    assert code == 0
    loaded = Manifest.load(target)
    assert loaded.source == (tmp_path / "keys.env").resolve()
    assert [(who.type, who.id, who.only) for who in loaded.identities] == [("user", "ada", ("OpenAI",))]


def test_a_refused_only_name_leaves_no_new_manifest_behind(mdb):
    code, said = mdb("credentials-add", "group", "LLMConsumers", "--only", "OPENROUTER_API_KEY")

    assert code == 2
    assert "the source does not carry OPENROUTER_API_KEY" in said
    assert not mdb.path.exists()


def test_a_type_that_is_neither_group_nor_user_is_exit_code_2(mdb):
    mdb("credentials-init")
    before = mdb.path.read_bytes()
    for command in ("credentials-add", "credentials-remove"):
        code, said = mdb(command, "everyone", "LLMConsumers")
        assert code == 2 and "must be user or group" in said
    code, said = mdb("credentials-add", "group", "LLMConsumers", "--only", ",")
    assert code == 2 and "--only was given without an entry name" in said
    assert mdb.path.read_bytes() == before


# ---- a manifest written by hand, in flow style ----------------------------
FLOW = (
    "# who may call what - reviewed by the platform team",
    "domain: agentic-ai-keys",
    "source: .env          # relative to this file",
    "",
    "identities:",
    "  - {type: group, id: PromptEngineers}   # everyone who builds prompts",
    "  - {type: group, id: RAGEngineers, only: [PGVECTOR_RAG_USER, PGVECTOR_RAG_PW]}",
    "  - {type: user,  id: sas-be-sa}",
    "",
    "# the service account is the one a scheduled flow runs as",
)


@pytest.mark.parametrize("eol", [b"\n", b"\r\n"], ids=["lf", "crlf"])
def test_a_flow_style_manifest_keeps_every_byte_outside_the_identity(mdb, eol):
    """Existing `{…}` lines stay `{…}` lines and keep their comments; a new
    identity is written in block style. The last line has no line end, and
    still has none afterwards."""
    before = _lines(eol, *FLOW)[:-len(eol)]
    mdb.path.write_bytes(before)
    last = _lines(eol, "  - {type: user,  id: sas-be-sa}")

    assert mdb("credentials-add", "group", "LLMConsumers")[0] == 0
    added = before.replace(last, last + _lines(eol, "  - type: group", "    id: LLMConsumers"))
    assert mdb.path.read_bytes() == added

    code, said = mdb("credentials-add", "group", "PromptEngineers", "--only", "OpenAI")
    assert code == 0 and "only: every entry -> OpenAI" in said
    narrowed = added.replace(b"{type: group, id: PromptEngineers}   # everyone",
                             b"{type: group, id: PromptEngineers, only: [OpenAI]}   # everyone")
    assert mdb.path.read_bytes() == narrowed

    assert mdb("credentials-add", "group", "RAGEngineers", "--only", "PGVECTOR_RAG_PW")[0] == 0
    assert mdb.path.read_bytes() == narrowed.replace(
        b"only: [PGVECTOR_RAG_USER, PGVECTOR_RAG_PW]}", b"only: [PGVECTOR_RAG_PW]}")

    marked = mdb.path.read_bytes()
    assert mdb("credentials-remove", "user", "sas-be-sa")[0] == 0
    assert mdb.path.read_bytes() == marked.replace(
        b"{type: user,  id: sas-be-sa}", b"{type: user,  id: sas-be-sa, state: absent}")
    # and back: the line is what it was before it was marked
    assert mdb("credentials-add", "user", "sas-be-sa")[0] == 0
    assert mdb.path.read_bytes() == marked


def test_identities_are_the_same_the_way_the_loader_reads_them():
    """`type` defaults to group and an id is a string whatever YAML makes of
    it, so `- {id: 123}` is the group "123" and is not added again."""
    path = Path("credentials.yaml")
    text = "source: .env\nidentities:\n  - {id: 123}\n  - id: Team\n"

    assert edit_identity(text, path, "group", "123").outcome == "unchanged"
    assert edit_identity(text, path, "group", "Team").outcome == "unchanged"
    assert edit_identity(text, path, "group", "team").outcome == "added"     # ids are exact
    # an id YAML would read as a number is written so that it stays text
    added = edit_identity(text, path, "user", "123")
    assert added.text == text + "  - type: user\n    id: '123'\n"
    assert added.identity.id == "123"


def test_a_list_written_one_name_per_line_stays_one_name_per_line():
    text = ("identities:\n- id: Team\n  only:\n  - OpenAI\n  - Google\n"
            "  verbatim: false   # the provider mapping applies\n")

    edit = edit_identity(text, Path("credentials.yaml"), "group", "Team",
                         only=("OpenRouter", "PGVECTOR_RAG_PW", "PGVECTOR_RAG_USER"))

    assert edit.text == text.replace(
        "  - OpenAI\n  - Google\n",
        "  - OpenRouter\n  - PGVECTOR_RAG_PW\n  - PGVECTOR_RAG_USER\n")
    assert edit.changes == ("only: OpenAI, Google -> OpenRouter, PGVECTOR_RAG_PW, PGVECTOR_RAG_USER",)


def test_an_empty_identities_list_takes_its_first_identity():
    """What is left when the author deleted the placeholder by hand."""
    text = "source: .env\n\nidentities:\n  # nobody yet\n"

    edit = edit_identity(text, Path("credentials.yaml"), "group", "LLMConsumers")

    assert edit.text == text.replace("identities:\n", "identities:\n  - type: group\n    id: LLMConsumers\n")
    assert edit.outcome == "added"


@pytest.mark.parametrize("text, reason", [
    ("identities: [{type: group, id: Team}]\n", "not a block list"),
    ("source: .env\n", "no 'identities' list"),
    ("identities:\n  - {id: Team}\n  - {type: group, id: Team}\n", "listed 2 times"),
    ("identities:\n  - {id: Team, state: gone}\n", "state must be one of present, absent"),
    ("identities:\n  - {id: Team\n", "not valid YAML"),
])
def test_a_manifest_the_edit_cannot_be_made_in_is_refused_and_left_alone(mdb, text, reason):
    mdb.path.write_text(text, encoding="utf-8")
    before = mdb.path.read_bytes()

    for command in ("credentials-add", "credentials-remove"):
        code, said = mdb(command, "group", "Team" if "listed" in reason else "Other")
        assert code == 2, said
        assert reason in said
        assert mdb.path.read_bytes() == before


def test_an_edit_that_does_not_read_back_as_intended_is_not_handed_out(monkeypatch):
    """The proof behind every edit: the spliced text is loaded again and has
    to be the manifest that was there plus the one change."""
    text = "source: .env\nidentities:\n  - {type: group, id: Team}\n"
    path = Path("credentials.yaml")
    honest = credentials._entry_splice

    def wrong(text, listed_under, listed, who, source, eol):
        start, end, block = honest(text, listed_under, listed, who, source, eol)
        return start, end, block.replace("LLMConsumers", "SomebodyElse")

    monkeypatch.setattr(credentials, "_entry_splice", wrong)
    with pytest.raises(CredentialError, match="nothing was written"):
        edit_identity(text, path, "group", "LLMConsumers")
    monkeypatch.setattr(credentials, "_entry_splice", honest)
    assert edit_identity(text, path, "group", "LLMConsumers").outcome == "added"


# ---- removal: a line in the manifest, carried out by the apply ------------
def test_remove_marks_the_identity_absent_and_does_not_talk_to_the_server(mdb):
    mdb("credentials-init")
    mdb("credentials-add", "group", "LLMConsumers")
    mdb("credentials-add", "group", "FormerTeam", "--only", "OpenAI")
    before = mdb.path.read_bytes()
    eol = _eol(before)
    mdb.site.holds("groups", "FormerTeam")

    code, said = mdb("credentials-remove", "group", "FormerTeam")      # fails if it signs in

    assert code == 0
    line = _lines(eol, "    only: [OpenAI]")
    assert mdb.path.read_bytes() == before.replace(line, line + _lines(eol, "    state: absent"))
    assert "Marked group FormerTeam as `state: absent` in credentials.yaml" in said
    assert "deleted on the server by the next mdb credentials-apply --manifest credentials.yaml" in said
    assert "did not contact the server" in said
    assert mdb.site.calls == [] and f"{DOMAIN}/groups/FormerTeam" in mdb.site.held

    # a second time is no change
    marked = mdb.path.read_bytes()
    code, said = mdb("credentials-remove", "group", "FormerTeam")
    assert code == 0 and "already marked `state: absent`" in said
    assert mdb.path.read_bytes() == marked


def test_remove_adds_an_identity_the_manifest_does_not_list(mdb):
    """The credential was written by a script or by hand; the manifest never
    knew it. Removing it still goes through the manifest."""
    mdb("credentials-init")
    mdb("credentials-add", "group", "LLMConsumers")
    before = mdb.path.read_bytes()
    eol = _eol(before)

    code, said = mdb("credentials-remove", "user", "ada")

    last = _lines(eol, "    id: LLMConsumers")
    assert code == 0
    assert mdb.path.read_bytes() == before.replace(last, last + _lines(
        eol, "", "  - type: user", "    id: ada", "    state: absent"))
    assert "Added user ada to credentials.yaml with `state: absent`" in said


def test_remove_on_a_fresh_scaffold_takes_the_place_of_the_placeholder(mdb):
    """A placeholder is never something to keep: left beside the removal, the
    next apply would try to equip a group named CHANGE-ME."""
    mdb("credentials-init")
    before = mdb.path.read_bytes()
    eol = _eol(before)

    code, said = mdb("credentials-remove", "group", "FormerTeam")

    assert code == 0
    assert mdb.path.read_bytes() == before.replace(
        _lines(eol, f"    id: {PLACEHOLDER_ID}"),
        _lines(eol, "    id: FormerTeam", "    state: absent"))
    assert f"in place of the placeholder {PLACEHOLDER_ID}" in said
    assert _ids(mdb.path) == [("group", "FormerTeam", (), "absent")]


def test_remove_needs_a_manifest_to_write_into(mdb):
    code, said = mdb("credentials-remove", "group", "FormerTeam")

    assert code == 2
    assert "No credentials manifest at credentials.yaml" in said
    assert not mdb.path.exists()


def test_add_sets_an_identity_marked_absent_back_to_present(mdb):
    mdb("credentials-init")
    mdb("credentials-add", "group", "LLMConsumers")
    before = mdb.path.read_bytes()
    mdb("credentials-remove", "group", "LLMConsumers")
    assert _ids(mdb.path) == [("group", "LLMConsumers", (), "absent")]

    code, said = mdb("credentials-add", "group", "LLMConsumers")

    assert code == 0
    assert mdb.path.read_bytes() == before           # the `state` line is gone again
    assert "state: absent -> present" in said
    assert "set back to present" in said


def test_a_state_line_that_shares_its_line_with_the_dash_is_rewritten_not_cut():
    """`state: present` says what a missing line says, and taking the first
    key of a block entry out would pull its neighbour onto the dash line."""
    text = "identities:\n- state: absent   # left in March\n  id: Team\n"

    edit = edit_identity(text, Path("credentials.yaml"), "group", "Team", state="present")

    assert edit.text == text.replace("state: absent", "state: present")
    assert edit.identity.state == "present"


MIXED = """source: .env
identities:
  - {type: group, id: LLMConsumers}
  - {type: group, id: FormerTeam, state: absent}
  - {type: user, id: ada, state: absent, source: long-gone.env}
"""


def test_a_dry_run_shows_the_removals_and_removes_nothing(mdb):
    mdb.path.write_text(MIXED, encoding="utf-8")
    kept = mdb.site.holds("groups", "FormerTeam", by="sasadm")

    code, said = mdb("credentials-apply", "--dry-run")

    assert code == 0
    rows = {who: _row(said, who) for who in ("LLMConsumers", "FormerTeam", "ada")}
    assert "remove" in rows["FormerTeam"] and "written by sasadm 2026-10-01" in rows["FormerTeam"]
    # an identity that is to be removed needs no source and no entries
    assert "remove" in rows["ada"] and "already gone" in rows["ada"]
    assert "skip" not in said
    assert "create" in rows["LLMConsumers"]
    assert "nothing was written and nothing was removed" in said
    assert set(verb for verb, _ in mdb.site.calls) == {"GET"}
    assert kept in mdb.site.held


def test_an_apply_deletes_what_is_marked_absent_and_reports_what_was_already_gone(mdb):
    mdb.path.write_text(MIXED, encoding="utf-8")
    mdb.site.holds("groups", "FormerTeam")

    code, said = mdb("credentials-apply")

    assert code == 0
    assert mdb.site.made("DELETE") == [f"{DOMAIN}/groups/FormerTeam", f"{DOMAIN}/users/ada"]
    assert mdb.site.made("PUT") == [DOMAIN, f"{DOMAIN}/groups/LLMConsumers"]
    assert "group FormerTeam: credential removed." in said
    assert "user ada: no credential to remove — it is already gone." in said
    assert f"group LLMConsumers: {len(ENTRIES)} entries stored." in said
    assert list(mdb.site.held) == [f"{DOMAIN}/groups/LLMConsumers"]
    # the entries line describes what is written, not the removals
    assert "Entries: " + ", ".join(ENTRIES) in said


def test_a_run_that_only_removes_creates_no_domain(mdb):
    mdb.path.write_text("identities:\n  - {type: group, id: FormerTeam, state: absent}\n",
                        encoding="utf-8")
    mdb.site.holds("groups", "FormerTeam")

    code, said = mdb("credentials-apply")

    assert code == 0
    assert mdb.site.made("PUT") == []
    assert mdb.site.made("DELETE") == [f"{DOMAIN}/groups/FormerTeam"]
    assert GROUP_REACH not in said          # no group credential was written


def test_a_removal_the_server_refuses_fails_the_run(mdb):
    mdb.path.write_text(MIXED, encoding="utf-8")
    mdb.site.holds("groups", "FormerTeam")
    mdb.site.refuse_delete = 403

    code, said = mdb("credentials-apply")

    assert code == 1
    assert "removing the credential for group 'FormerTeam' failed: HTTP 403" in said
    assert f"{DOMAIN}/groups/FormerTeam" in mdb.site.held


def test_nothing_is_removed_because_a_line_is_missing(mdb):
    """The service cannot list who holds a credential, so the apply cannot
    know what a missing line used to say - and deletes nothing for it."""
    mdb.path.write_text("source: .env\nidentities:\n  - {type: group, id: LLMConsumers}\n",
                        encoding="utf-8")
    forgotten = mdb.site.holds("groups", "NoLongerInTheFile")

    assert mdb("credentials-apply")[0] == 0

    assert mdb.site.made("DELETE") == []
    assert forgotten in mdb.site.held


def test_the_report_shows_absent_as_expected_and_marks_what_is_still_there(mdb):
    mdb.path.write_text(MIXED, encoding="utf-8")
    mdb.site.holds("groups", "LLMConsumers")
    mdb.site.holds("groups", "FormerTeam", by="sasadm")

    code, said = mdb("credentials-report")

    assert code == 0
    rows = {who: _row(said, who) for who in ("LLMConsumers", "FormerTeam", "ada")}
    assert "yes" in rows["LLMConsumers"]
    assert "STILL THERE (manifest: absent)" in rows["FormerTeam"] and "sasadm" in rows["FormerTeam"]
    assert "absent (as the manifest says)" in rows["ada"]
    assert "1 of 1 named identities hold a credential." in said
    assert "1 of 2 identities marked `state: absent` hold none." in said
    assert "1 credential(s) the manifest marks absent are still there." in said
    assert set(verb for verb, _ in mdb.site.calls) == {"GET"}

    # after the apply the report has nothing left to mark
    mdb("credentials-apply")
    code, said = mdb("credentials-report")
    assert code == 0
    assert "STILL THERE" not in said
    assert "2 of 2 identities marked `state: absent` hold none." in said


@pytest.mark.parametrize("value", ["gone", "Absent", "no", ""])
def test_a_state_that_is_neither_present_nor_absent_ends_the_command(mdb, value):
    """Refused by the loader, so before a session is opened: a guess here
    would write or delete a credential."""
    mdb.path.write_text(f"source: .env\nidentities:\n  - type: group\n    id: LLMConsumers\n"
                        f"    state: {value}\n", encoding="utf-8")
    with pytest.raises(CredentialError, match="'LLMConsumers': state must be one of present, absent"):
        Manifest.load(mdb.path)

    for command in (("credentials-apply", "--dry-run"), ("credentials-apply",), ("credentials-report",)):
        code, said = mdb(*command)
        assert code == 2, said
        assert "state must be one of present, absent" in said
    assert mdb.site.calls == []


# ---- the trap: a group the users are not members of -----------------------
def test_an_apply_says_once_whom_a_group_credential_reaches(mdb):
    mdb.path.write_text("source: .env\nidentities:\n  - {type: group, id: LLMConsumers}\n"
                        "  - {type: group, id: RAGEngineers}\n  - {type: user, id: ada}\n",
                        encoding="utf-8")

    code, planned = mdb("credentials-apply", "--dry-run")
    assert code == 0 and planned.count(GROUP_REACH) == 1
    code, said = mdb("credentials-apply")
    assert code == 0 and said.count(GROUP_REACH) == 1
    assert "members of that group" in GROUP_REACH and "replaces it for that user" in GROUP_REACH

    # a run that writes no group credential has nothing to say about groups
    mdb.path.write_text("source: .env\nidentities:\n  - {type: user, id: ada}\n", encoding="utf-8")
    for command in (("credentials-apply", "--dry-run"), ("credentials-apply",)):
        code, said = mdb(*command)
        assert code == 0 and GROUP_REACH not in said


# ---- the property that matters most ---------------------------------------
def test_no_secret_value_is_printed_or_written_by_any_of_the_commands(mdb, tmp_path):
    (tmp_path / "team.env").write_text(ENV, encoding="utf-8")
    mdb("credentials-add", "group", "LLMConsumers")                       # writes the manifest
    mdb("credentials-add", "group", "RAGEngineers", "--only", "PGVECTOR_RAG_USER,PGVECTOR_RAG_PW")
    mdb("credentials-add", "group", "RAGEngineers", "--only", "PGVECTOR_RAG_PW", "--source", "team.env")
    mdb("credentials-add", "group", "RAGEngineers", "--only", "NotThere")  # refused
    mdb("credentials-add", "user", "ada")
    mdb("credentials-remove", "user", "ada")
    mdb("credentials-remove", "group", "FormerTeam")
    mdb("credentials-apply", "--dry-run")
    mdb("credentials-apply")
    mdb("credentials-report")
    mdb("credentials-add", "user", "ada")                                 # back to present
    mdb("credentials-apply")
    mdb("credentials-init", "--file", "second.yaml")

    assert len(mdb.printed) == 13
    everything = "\n".join(mdb.printed) + mdb.path.read_text(encoding="utf-8") \
        + (tmp_path / "second.yaml").read_text(encoding="utf-8")
    for secret in SECRETS:
        assert secret not in everything, f"{secret} leaked"
    # the names are there, because names are what make an output reviewable
    assert "PGVECTOR_RAG_PW" in everything and "OpenRouter" in everything
    # and the values did reach the service, where they belong
    sent = mdb.site.held[f"{DOMAIN}/groups/LLMConsumers"]["sent"]["secrets"]
    assert sorted(sent) == sorted(ENTRIES)


def test_the_scaffold_of_init_is_the_scaffold_function(mdb, tmp_path):
    """`credentials-init` and a `credentials-add` without a manifest start
    from one function, so there is one starter file and not two."""
    mdb("credentials-init")
    expected = scaffold((tmp_path / ".env").resolve(), mdb.path.resolve(), "agentic-ai-keys", ENTRIES)
    assert mdb.path.read_text(encoding="utf-8") == expected
    assert "mdb credentials-add" in expected and "mdb credentials-remove" in expected
