# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bulk credential provisioning: the mapping, the manifest, and what it refuses.

The mapping tests exist because this module and the two shell scripts under
SAS-Viya-Integrations/Other must agree exactly — an identity equipped by one
tool and read by the other would otherwise be missing keys under names nobody
looked for.

The strongest test here is the last one: no secret value may appear anywhere
in a plan, because a plan is what gets printed.
"""
import base64
import re
from pathlib import Path

import pytest

from mdb.viya.credentials import (
    PLACEHOLDER_ID, PROVIDER_ENTRIES, CredentialError, Identity, Manifest,
    build_steps, credential_path, delete_credential, encode, entries_for,
    map_entries, planned_entries, read_name_value_file, relative_source,
    scaffold,
)

REPO = Path(__file__).resolve().parents[3]


def test_every_definition_key_name_is_an_entry_the_scripts_write():
    """The Prompt Builder resolves a model's key with an exact lookup of its
    `API_KEY` default (the manifest's key_name) in the domain's secrets map.
    An entry the provisioning tools spell differently - `Azure OpenAI` for a
    definition that says `AzureOpenAI` (PR #31) - is a model disabled with a
    'no credential' note although the key is in the domain."""
    key_names = set()
    for folder in ("LLM-Definitions", "Embedding-Definitions"):
        for manifest in (REPO / folder).glob("*/definition.yaml"):
            found = re.search(r"^\s+key_name:\s*(\S+)", manifest.read_text(encoding="utf-8"), re.M)
            if found:
                key_names.add(found.group(1))
    assert key_names, "no definitions found - is the test running inside the repository?"
    assert key_names <= set(PROVIDER_ENTRIES.values())

ENV_BODY = """
# the accelerator's .env, as an admin really keeps it
OPENAI_API_KEY=sk-live-openai
ANTHROPIC_API_KEY='sk-live-anthropic'
GEMINI_API_KEY="gemini-live"
MISTRAL_API_KEY=
PGVECTOR_RAG_USER=rag_ingest
PGVECTOR_RAG_PW=pg-secret
singlestore_rag_user=s2_ingest
RAGSTORE_HOST=db.example.invalid
RAGSTORE_PORT=5432
SAS_VIYA_PASSWORD=not-a-credential-entry
SOMETHING_ELSE=ignored
"""


@pytest.fixture
def env_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text(ENV_BODY, encoding="utf-8")
    return path


# ---- the mapping, which must match the shell scripts ----------------------
def test_provider_variables_map_onto_their_provider_names(env_file):
    entries = map_entries(read_name_value_file(env_file))
    assert entries["OpenAI"] == "sk-live-openai"
    assert entries["Anthropic"] == "sk-live-anthropic"    # quotes stripped
    assert entries["Google"] == "gemini-live"


def test_store_credentials_and_locations_are_carried_uppercased(env_file):
    entries = map_entries(read_name_value_file(env_file))
    assert entries["PGVECTOR_RAG_USER"] == "rag_ingest"
    assert entries["PGVECTOR_RAG_PW"] == "pg-secret"
    assert entries["SINGLESTORE_RAG_USER"] == "s2_ingest"   # lower case in .env
    assert entries["RAGSTORE_HOST"] == "db.example.invalid"
    assert entries["RAGSTORE_PORT"] == "5432"


def test_an_empty_key_is_a_placeholder_not_a_credential(env_file):
    """Storing a blank entry would mask the real 'no credential' case, which
    is the one the Builder reports usefully."""
    assert "Mistral" not in map_entries(read_name_value_file(env_file))


def test_unrelated_variables_are_left_alone(env_file):
    entries = map_entries(read_name_value_file(env_file))
    assert "SAS_VIYA_PASSWORD" not in entries
    assert "SOMETHING_ELSE" not in entries


def test_verbatim_mode_stores_what_it_is_given(env_file):
    entries = map_entries(read_name_value_file(env_file), verbatim=True)
    assert entries["SAS_VIYA_PASSWORD"] == "not-a-credential-entry"
    assert "OpenAI" not in entries          # no mapping applied


def test_the_secrets_map_is_base64(env_file):
    encoded = encode(map_entries(read_name_value_file(env_file)))
    assert base64.b64decode(encoded["OpenAI"]).decode() == "sk-live-openai"


# ---- the manifest ---------------------------------------------------------
def _manifest(tmp_path, body: str):
    path = tmp_path / "credentials.yaml"
    path.write_text(body, encoding="utf-8")
    return Manifest.load(path)


def test_a_manifest_equips_several_identities(tmp_path, env_file):
    manifest = _manifest(tmp_path, """
domain: agentic-ai-keys
source: .env
identities:
  - {type: group, id: PromptEngineers}
  - {type: group, id: RAGEngineers, only: [PGVECTOR_RAG_USER, PGVECTOR_RAG_PW]}
  - {type: user,  id: sas-be-sa}
""")
    assert manifest.domain == "agentic-ai-keys"
    assert [who.id for who in manifest.identities] == [
        "PromptEngineers", "RAGEngineers", "sas-be-sa"]
    assert manifest.identities[2].type == "user"
    assert manifest.identities[1].only == ("PGVECTOR_RAG_USER", "PGVECTOR_RAG_PW")


def test_a_manifest_that_equips_nobody_is_refused(tmp_path):
    with pytest.raises(CredentialError, match="non-empty list"):
        _manifest(tmp_path, "domain: agentic-ai-keys\nidentities: []\n")


def test_an_unknown_identity_type_is_refused_by_name(tmp_path):
    with pytest.raises(CredentialError, match="type must be one of"):
        _manifest(tmp_path, "identities:\n  - {type: everyone, id: x}\n")


def test_an_identity_is_present_unless_the_manifest_says_absent(tmp_path, env_file):
    manifest = _manifest(tmp_path, """
source: .env
identities:
  - {type: group, id: LLMConsumers}
  - {type: group, id: RAGEngineers, state: present}
  - {type: group, id: FormerTeam, state: absent}
""")
    assert [who.state for who in manifest.identities] == ["present", "present", "absent"]
    assert [who.absent for who in manifest.identities] == [False, False, True]


@pytest.mark.parametrize("written", ["gone", "Absent", "no", "~", "[absent]"])
def test_any_other_state_is_refused_by_name(tmp_path, written):
    """`state` decides between writing a credential and deleting one, so a
    value that is neither is never read as the nearest of the two."""
    with pytest.raises(CredentialError) as refused:
        _manifest(tmp_path, f"identities:\n  - {{type: group, id: FormerTeam, state: {written}}}\n")
    assert "identity 'FormerTeam': state must be one of present, absent" in str(refused.value)


def test_group_and_user_address_different_endpoints():
    assert credential_path("d", Identity("group", "PromptEngineers")) == \
        "/credentials/domains/d/groups/PromptEngineers"
    assert credential_path("d", Identity("user", "some.user")) == \
        "/credentials/domains/d/users/some.user"


# ---- planning -------------------------------------------------------------
def test_a_plan_says_create_or_replace_and_who_wrote_it(tmp_path, env_file):
    manifest = _manifest(tmp_path, """
source: .env
identities:
  - {type: group, id: PromptEngineers}
  - {type: user,  id: newcomer}
""")
    held = {"PromptEngineers": {"modifiedBy": "admin",
                                "modifiedTimeStamp": "2026-07-30T09:00:00Z"}}
    steps = build_steps(manifest, lambda who: held.get(who.id))

    assert steps[0].action == "replace"
    assert steps[0].existing_by == "admin"
    assert steps[1].action == "create"
    assert steps[1].existing_by == ""
    assert "OpenAI" in steps[0].entry_names


def test_only_narrows_the_entries_and_names_what_is_missing(tmp_path, env_file):
    manifest = _manifest(tmp_path, """
source: .env
identities:
  - {type: group, id: RAGEngineers, only: [PGVECTOR_RAG_USER, PGVECTOR_RAG_PW]}
  - {type: group, id: Confused, only: [PGVECTOR_RAG_PW, OPENAI_API_KEY]}
""")
    steps = build_steps(manifest, lambda who: None)
    assert steps[0].ok
    assert steps[0].entry_names == ("PGVECTOR_RAG_PW", "PGVECTOR_RAG_USER")
    # 'only' names entries the way the DOMAIN spells them, not the .env
    assert not steps[1].ok
    assert "OPENAI_API_KEY" in steps[1].problem


def test_a_missing_source_is_reported_per_identity_not_raised(tmp_path, env_file):
    manifest = _manifest(tmp_path, """
source: .env
identities:
  - {type: group, id: Fine}
  - {type: group, id: Broken, source: nope.env}
""")
    steps = build_steps(manifest, lambda who: None)
    assert steps[0].ok
    assert not steps[1].ok and "not found" in steps[1].problem


def test_a_source_with_nothing_recognisable_is_refused(tmp_path):
    (tmp_path / "empty.env").write_text("NOTHING=useful\n", encoding="utf-8")
    manifest = _manifest(tmp_path, """
source: empty.env
identities:
  - {type: group, id: Nobody}
""")
    steps = build_steps(manifest, lambda who: None)
    assert not steps[0].ok
    assert "no recognised entries" in steps[0].problem


def test_an_absent_identity_is_planned_as_a_removal_and_needs_no_source(tmp_path):
    """No source at the top, a source that is not there, a source with
    nothing in it: each stops an identity that is to be equipped, and none
    stops one that is to be removed - there is nothing to read for it."""
    (tmp_path / "empty.env").write_text("NOTHING=useful\n", encoding="utf-8")
    manifest = _manifest(tmp_path, """
identities:
  - {type: group, id: FormerTeam, state: absent}
  - {type: user,  id: ada, state: absent, source: long-gone.env}
  - {type: user,  id: bob, state: absent, source: empty.env, only: [OpenAI]}
  - {type: group, id: Unequipped}
""")
    held = {"FormerTeam": {"modifiedBy": "sasadm", "modifiedTimeStamp": "2026-10-01T09:00:00Z"},
            "bob": {}}
    steps = build_steps(manifest, lambda who: held.get(who.id))

    assert [step.action for step in steps[:3]] == ["remove", "remove", "remove"]
    assert all(step.ok and step.entry_names == () for step in steps[:3])
    assert [step.found for step in steps[:3]] == [True, False, True]
    assert steps[0].existing_by == "sasadm"
    assert not steps[3].ok and "no source file" in steps[3].problem


def test_the_check_of_one_identity_is_the_check_of_the_plan(tmp_path, env_file):
    """`credentials-add` refuses with `planned_entries` what `credentials-apply`
    skips with `build_steps`: one function, so one wording."""
    manifest = _manifest(tmp_path, """
source: .env
identities:
  - {type: group, id: RAGEngineers, only: [PGVECTOR_RAG_USER, PGVECTOR_RAG_PW]}
  - {type: group, id: Confused, only: [PGVECTOR_RAG_PW, OPENAI_API_KEY]}
  - {type: group, id: Broken, source: nope.env}
""")
    steps = build_steps(manifest, lambda who: None)
    for step in steps:
        assert planned_entries(manifest, step.identity) == (step.entry_names, step.problem)
    assert [bool(step.problem) for step in steps] == [False, True, True]


class _Answer:
    def __init__(self, status_code):
        self.status_code = status_code


class _Deleting:
    """A session that answers a DELETE with one status and records the path."""

    def __init__(self, status_code):
        self.status_code, self.paths = status_code, []

    def delete(self, path):
        self.paths.append(path)
        return _Answer(self.status_code)


def test_deleting_a_credential_that_is_gone_is_an_answer_not_an_error():
    who = Identity("group", "FormerTeam", state="absent")

    removed = _Deleting(204)
    assert delete_credential(removed, "agentic-ai-keys", who) is True
    assert removed.paths == ["/credentials/domains/agentic-ai-keys/groups/FormerTeam"]
    assert delete_credential(_Deleting(404), "agentic-ai-keys", who) is False
    with pytest.raises(CredentialError, match="removing the credential for group 'FormerTeam' failed: HTTP 403"):
        delete_credential(_Deleting(403), "agentic-ai-keys", who)


def test_entries_for_returns_the_values_the_plan_withholds(tmp_path, env_file):
    manifest = _manifest(tmp_path, """
source: .env
identities:
  - {type: group, id: RAGEngineers, only: [PGVECTOR_RAG_PW]}
""")
    assert entries_for(manifest, manifest.identities[0]) == {"PGVECTOR_RAG_PW": "pg-secret"}


# ---- where the manifest lives ---------------------------------------------
def test_a_manifests_paths_resolve_against_itself_not_the_shell(tmp_path):
    """What makes a manifest portable: keep it beside the .env files it names
    and both can be moved, or checked out somewhere else, together."""
    (tmp_path / "config").mkdir()
    (tmp_path / "envs").mkdir()
    (tmp_path / "envs" / "prod.env").write_text(ENV_BODY, encoding="utf-8")
    path = tmp_path / "config" / "credentials.yaml"
    path.write_text("source: ../envs/prod.env\nidentities:\n  - {type: group, id: Team}\n",
                    encoding="utf-8")

    manifest = Manifest.load(path)
    assert manifest.source == (tmp_path / "envs" / "prod.env").resolve()
    assert build_steps(manifest, lambda who: None)[0].ok


def test_a_source_beside_the_manifest_is_written_relative(tmp_path):
    source = tmp_path / "envs" / "prod.env"
    manifest = tmp_path / "config" / "credentials.yaml"
    assert relative_source(source, manifest) == "../envs/prod.env"
    # the shipped layout: a records folder beside the repository
    assert relative_source(tmp_path / ".env",
                           tmp_path / "a" / "b" / "credentials.yaml") == "../../.env"


def test_a_distant_source_is_written_absolute(tmp_path):
    """A ../../../../../.. chain is technically correct and useless to the
    person reviewing the manifest — and it breaks the moment either end moves,
    which is the opposite of what a relative path is for."""
    manifest = tmp_path / "a" / "b" / "c" / "d" / "e" / "credentials.yaml"
    written = relative_source(tmp_path / ".env", manifest)
    assert ".." not in written
    assert written.endswith("/.env")


def test_a_scaffold_lists_the_entry_names_and_no_values(tmp_path, env_file):
    entries = map_entries(read_name_value_file(env_file))
    text = scaffold(env_file, tmp_path / "credentials.yaml",
                    "agentic-ai-keys", tuple(sorted(entries)))
    for name in ("OpenAI", "PGVECTOR_RAG_PW", "RAGSTORE_HOST"):
        assert name in text
    for secret in ("sk-live-openai", "pg-secret", "rag_ingest"):
        assert secret not in text, f"{secret} leaked into the scaffold"


def test_a_scaffold_is_a_manifest_this_tool_can_read_back(tmp_path, env_file):
    """A starter file that does not parse is worse than no starter file."""
    path = tmp_path / "credentials.yaml"
    path.write_text(scaffold(env_file, path, "agentic-ai-keys", ("OpenAI",)),
                    encoding="utf-8")

    manifest = Manifest.load(path)
    assert manifest.domain == "agentic-ai-keys"
    assert manifest.source == env_file.resolve()
    # One identity, the placeholder `mdb credentials-add` replaces. The
    # examples below it - the `state: absent` one included - are comments:
    # a starter file must not remove anything when it is applied.
    assert [(who.type, who.id, who.state) for who in manifest.identities] == [
        ("group", PLACEHOLDER_ID, "present")]
    # it plans, so the only thing standing between the scaffold and a run is
    # the author replacing the placeholder identity
    assert build_steps(manifest, lambda who: None)[0].ok


def test_a_scaffold_names_the_commands_that_edit_it(tmp_path, env_file):
    """The file is where its author looks first, so it says that identities
    can be added and marked for removal without editing it, and whom a group
    credential reaches."""
    text = scaffold(env_file, tmp_path / "team.yaml", "agentic-ai-keys", ("OpenAI",))
    assert "#   mdb credentials-add    group LLMConsumers --manifest team.yaml" in text
    assert "#   mdb credentials-remove group FormerTeam   --manifest team.yaml" in text
    assert "# A GROUP credential reaches only the members of that group" in text
    assert "  #   state: absent" in text
    assert text.count(PLACEHOLDER_ID) == 1


# ---- the property that matters most ---------------------------------------
def test_no_secret_value_ever_appears_in_a_plan(tmp_path, env_file):
    """A plan is what gets printed to a terminal and pasted into a ticket."""
    manifest = _manifest(tmp_path, """
source: .env
identities:
  - {type: group, id: PromptEngineers}
  - {type: user,  id: sas-be-sa}
""")
    steps = build_steps(manifest, lambda who: None)
    rendered = repr(steps)
    for secret in ("sk-live-openai", "sk-live-anthropic", "gemini-live",
                   "pg-secret", "s2_ingest", "rag_ingest"):
        assert secret not in rendered, f"{secret} leaked into the plan"
    # the NAMES are there, because that is what makes the plan reviewable
    assert "OpenAI" in rendered and "PGVECTOR_RAG_PW" in rendered
