# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Provisioning the accelerator's credential domain, for many identities at once.

WHY THIS IS HERE. `SAS-Viya-Integrations/Other/create-credential-domain.ps1`
(and its `.sh` twin) equip ONE identity per run from the repository's
git-ignored `.env`. That is right for a demo and wrong for a rollout: equipping
a department means running a script once per person, with no record of who was
equipped and no way to see afterwards what a deployment actually holds. mdb
already owns the register/publish/options lifecycle and already reads the same
`.env`, so credentials become one more governed artifact instead of a side
script. The shell scripts keep working — they are what an administrator with
no Python runs, and the admin guide documents them.

WHAT A CREDENTIAL IS. One domain (default `agentic-ai-keys`) holds a
credential per identity, each carrying a map of named secrets:

    OpenAI, Anthropic, Google, …          LLM provider API keys, under the
                                          provider names the fact sheets use
    PGVECTOR_RAG_USER / _PW               vector-store credentials — the
    SINGLESTORE_RAG_USER / _PW            backend prefix lets one domain
                                          serve several stores
    <BACKEND>_HOST/_PORT/_DB/_SSLMODE     where the store LIVES. Not secret,
                                          but carried here so the RAG Builder
                                          never asks a user for a hostname

A USER credential overrides a GROUP one, which is what makes "the department
can call OpenAI, except Ada who has her own quota" expressible.

THREE API FACTS THIS MODULE IS SHAPED BY, each established live 2026-08-04:

1. **There is no collection endpoint.** `/credentials/domains/{d}/users` and
   `…/groups` answer 404 "No static resource" — only the per-identity item
   exists. So "who holds a credential" cannot be listed; it can only be
   answered by asking about identities you already name. `report()` therefore
   probes the identities in the manifest rather than pretending to enumerate.

2. **A credential GET returns no secrets at all** — not the values, not even
   the entry NAMES. The body carries `createdBy`, `modifiedBy`,
   `modifiedTimeStamp` and nothing about content. So a dry run can honestly
   say "will create" or "will replace, and I cannot see what is there now",
   and must not pretend to compute a diff. This is a privacy property, not a
   gap: one identity is never shown another's secrets.

3. **PUT replaces the whole credential.** There is no merge. Every apply must
   therefore carry the complete set of entries that identity should end up
   with, which is why a manifest entry names a source file rather than a
   patch.

4. **A group credential is only found when the reader asks for it**
   (established live 2026-08-05, by removing a real user credential and
   watching what happened). Resolution goes through
   `GET /credentials/domains/{d}/secrets`, which returns the CALLER's own
   credential and, without more, nothing else: with the user credential
   removed it answered 404 `"The credential for the user ... in the domain
   ..."` even though the group credential existed and the caller was a member.
   The group is consulted only with `?lookupInGroup=true`, which then answers
   200 with `identityType: group`.

   This is the single most consequential fact about group credentials, because
   getting it wrong fails OPEN in the worst direction: provisioning looks
   perfect - the credential is written, `credentials-report` says the group
   holds it - and every call still behaves as though the group had nothing.
   Anything that reads this domain must pass the flag; every caller in this
   repository does, which is worth re-checking whenever a new one is added.
   `?lookupInGroup=false` is equivalent to omitting it.

REMOVAL IS SOMETHING THE MANIFEST SAYS, never something it implies. Because
of fact 1 nobody can list the holders of a credential, so a line that is
missing from the manifest cannot mean "remove": the tool would have to delete
what it cannot see. An identity whose credential should go carries
`state: absent`, and the apply deletes exactly that item. Nothing is ever
removed because a line is gone.

THE MANIFEST IS EDITED AS TEXT. `edit_identity()` adds or changes one identity
by splicing text at the positions the YAML parser reports, because the YAML
library this CLI depends on drops every comment on a dump - and the manifest
is mostly comments. Every edit is read back with the loader and compared with
what was intended before anything is written.

SECRET VALUES NEVER LEAVE THIS MODULE. Nothing here returns, prints, logs or
formats a value; the planning types carry entry NAMES and counts only. The
values are read from the source file and base64-encoded straight into the
request body.
"""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

#: `.env` variable -> domain entry name, for the LLM providers. An entry name
#: is the KeyName a definition's `API_KEY` option references (`key_name` in
#: definition.yaml, `API_KEY.default` in options.json): the Prompt Builder
#: and the RAG Builder look a model's key up under exactly that name, so a
#: renamed entry is a disabled model. Mirrors the shell scripts exactly: the
#: tools must agree or an identity equipped by one would be missing keys
#: under the other's names.
PROVIDER_ENTRIES = {
    "OPENAI_API_KEY": "OpenAI",
    "ANTHROPIC_API_KEY": "Anthropic",
    "GEMINI_API_KEY": "Google",
    "OPENROUTER_API_KEY": "OpenRouter",
    "AZURE_OPENAI_API_KEY": "AzureOpenAI",
    "MISTRAL_API_KEY": "Mistral",
    "VOYAGE_API_KEY": "VoyageAI",
    "HUGGINGFACE_API_KEY": "HuggingFace",
    "AWS_BEDROCK_API_KEY": "AWSBedrock",
    "TYPESAFE_API_KEY": "TypeSafe",
}

#: `<BACKEND>_RAG_USER` / `<BACKEND>_RAG_PW` — carried over verbatim, uppercased.
STORE_CREDENTIAL = re.compile(r"^[A-Za-z][A-Za-z0-9]*_RAG_(USER|PW)$")

#: `<BACKEND>_HOST/_PORT/_DB/_SSLMODE` — where a store lives. Not secret, but
#: the domain is the one place every identity can already read.
STORE_LOCATION = re.compile(r"^[A-Za-z][A-Za-z0-9]*_(HOST|PORT|DB|SSLMODE)$")

DEFAULT_DOMAIN = "agentic-ai-keys"
#: The manifest a command reads when none is named, matching the convention
#: `options-save`/`options-restore` already set with builder-options.json.
DEFAULT_MANIFEST = "credentials.yaml"
DOMAIN_TYPE = "base64"
DOMAIN_DESCRIPTION = (
    "Keys for the SAS Agentic AI Accelerator (LLM providers and RAG vector stores)."
)

IDENTITY_TYPES = ("user", "group")

#: What a manifest may say about an identity's credential. `present` is the
#: default; `absent` is the only way a credential is ever removed.
STATES = ("present", "absent")

#: The identity the scaffold writes for its author to replace.
PLACEHOLDER_ID = "CHANGE-ME-to-a-real-group"

#: The comment the scaffold writes above that identity, line by line. It is
#: about the placeholder, so it leaves the file with it (`edit_identity`).
PLACEHOLDER_COMMENT = (
    "# Replace with the groups and users this deployment should equip. The first",
    "# `mdb credentials-add` replaces this placeholder.",
)

#: Said once per apply that writes a group credential. A credential written
#: for a group the users are not members of looks like a finished rollout and
#: leaves every one of them without a key.
GROUP_REACH = (
    "A group credential reaches only the members of that group, and a user's "
    "own credential replaces it for that user.")


class CredentialError(RuntimeError):
    """A manifest or source file this module refuses to act on."""


# ---------------------------------------------------------------------------
# Reading the sources
# ---------------------------------------------------------------------------
def read_name_value_file(path: Path) -> dict:
    """`NAME=VALUE` lines -> {name: value}, comments and blanks ignored.

    Quotes are stripped the way the shell scripts strip them, so the same
    `.env` produces the same entries whichever tool reads it.
    """
    entries: dict = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CredentialError(f"cannot read {path}: {exc}") from exc
    for line in lines:
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#") or "=" not in trimmed:
            continue
        name, _, value = trimmed.partition("=")
        value = value.strip().strip('"').strip("'")
        if name.strip() and value:
            entries[name.strip()] = value
    return entries


def map_entries(raw: dict, verbatim: bool = False) -> dict:
    """`.env` variables -> domain entries. Values pass through untouched.

    An entry that is present but EMPTY is skipped: a blank key is a
    placeholder waiting to be filled in, and storing it would put empty
    entries in the domain and mask the real "this identity has no credential"
    case — which is the one the Builder reports usefully.
    """
    if verbatim:
        return {name: value for name, value in raw.items() if value}
    mapped: dict = {}
    for name, value in raw.items():
        if not value:
            continue
        # Matched on the UPPERCASED name. PowerShell's -match and its
        # hashtables are case-insensitive while Python's re and dicts are
        # not, so the two shipped scripts disagreed: a lowercase
        # `singlestore_rag_user` was stored by the .ps1 and silently dropped
        # by the .sh (found 2026-08-04, and fixed in the .sh at the same
        # time). Uppercasing first makes all three agree.
        upper = name.upper()
        if upper in PROVIDER_ENTRIES:
            mapped[PROVIDER_ENTRIES[upper]] = value
        elif STORE_CREDENTIAL.match(upper) or STORE_LOCATION.match(upper):
            mapped[upper] = value
    return mapped


def read_manifest_text(path: Path) -> str:
    """A manifest's text with its line ends as they are in the file.

    Read as bytes on purpose: a text-mode read turns every `\\r\\n` into
    `\\n`, and an edit written back would then change every line of a file
    the author expects to see one line changed in.
    """
    try:
        return path.read_bytes().decode("utf-8")
    except OSError as exc:
        raise CredentialError(f"cannot read {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise CredentialError(f"{path} is not UTF-8 text: {exc}") from exc


def write_manifest_text(path: Path, text: str) -> None:
    """Write an edited manifest back, line ends untouched."""
    try:
        path.write_bytes(text.encode("utf-8"))
    except OSError as exc:
        raise CredentialError(f"cannot write {path}: {exc}") from exc


def _not_yaml(path: Path, exc) -> CredentialError:
    """The refusal of a manifest YAML cannot read: where the reader stopped,
    and nothing of the text. The error of the YAML library quotes the line it
    stopped at, and a key file handed as the manifest by mistake
    (`--manifest .env`) would be printed with a value; the problem it names
    can be a word of the file as well (an alias)."""
    mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
    where = f" (the reader stopped at line {mark.line + 1}, column {mark.column + 1})" if mark else ""
    return CredentialError(f"{path} is not valid YAML{where}. Nothing of its text is shown: a key file "
                           "handed as the manifest would be shown with its values.")


def encode(entries: dict) -> dict:
    """The `secrets` map as the domain stores it (base64, per domain type)."""
    return {
        name: base64.b64encode(str(value).encode("utf-8")).decode("ascii")
        for name, value in entries.items()
    }


# ---------------------------------------------------------------------------
# The manifest
# ---------------------------------------------------------------------------
@dataclass
class Identity:
    """One identity to equip, and where its entries come from."""

    type: str
    id: str
    #: Source file for this identity; falls back to the manifest default.
    source: Optional[Path] = None
    #: Store entries verbatim instead of applying the provider mapping.
    verbatim: bool = False
    #: Keep only these entry names. Empty = everything the source yields.
    only: tuple = ()
    #: `absent` = this identity's credential is to be removed, not written.
    state: str = "present"

    def __post_init__(self) -> None:
        if self.type not in IDENTITY_TYPES:
            raise CredentialError(
                f"identity '{self.id}': type must be one of "
                f"{', '.join(IDENTITY_TYPES)}, not '{self.type}'")
        if not str(self.id).strip():
            raise CredentialError("an identity with no id cannot be equipped")
        if self.state not in STATES:
            raise CredentialError(
                f"identity '{self.id}': state must be one of "
                f"{', '.join(STATES)}, not '{self.state}'")

    @property
    def path_segment(self) -> str:
        """`users` or `groups` — the collection the item lives under."""
        return "users" if self.type == "user" else "groups"

    @property
    def absent(self) -> bool:
        """True when the manifest asks for this credential to be removed."""
        return self.state == "absent"


@dataclass
class Manifest:
    """Who to equip, with what, in which domain."""

    domain: str = DEFAULT_DOMAIN
    source: Optional[Path] = None
    identities: list = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        """Read a credentials manifest (YAML). Paths resolve against it."""
        return cls.parse(read_manifest_text(path), path)

    @classmethod
    def parse(cls, text: str, path: Path, allow_empty: bool = False) -> "Manifest":
        """A manifest from its text. `path` is where the text lives (or will
        live): paths inside it resolve against that file's directory.

        `allow_empty` is for the editor alone, which may be handed a manifest
        whose author deleted the placeholder and left `identities:` empty, or
        wrote `identities: []` - the editor says itself what it can do with
        that.
        """
        import yaml

        try:
            raw = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            raise _not_yaml(path, exc) from None
        if not isinstance(raw, dict):
            raise CredentialError(f"{path}: expected a mapping at the top level")

        base = path.parent
        default_source = raw.get("source")
        listed = raw.get("identities")
        if allow_empty and listed in (None, []):
            listed = []
        elif not isinstance(listed, list) or not listed:
            raise CredentialError(
                f"{path}: 'identities' must be a non-empty list — a manifest "
                "that equips nobody is almost certainly a mistake")

        identities = []
        for index, item in enumerate(listed, start=1):
            if not isinstance(item, dict):
                raise CredentialError(
                    f"{path}: identity {index} must be a mapping with 'type' and 'id'")
            source = item.get("source")
            only = item.get("only") or ()
            identities.append(Identity(
                type=str(item.get("type", "group")),
                id=str(item.get("id", "")),
                source=(base / str(source)).resolve() if source else None,
                verbatim=bool(item.get("verbatim", False)),
                only=tuple(str(name) for name in only),
                # A key that is missing means present. A key that is there
                # with any other value - a YAML `no`, an empty value, a typo -
                # is refused: a guess here would write or delete a credential.
                state=str(item.get("state", "present")),
            ))
        return cls(
            domain=str(raw.get("domain") or DEFAULT_DOMAIN),
            source=(base / str(default_source)).resolve() if default_source else None,
            identities=identities,
        )


#: How far up a relative source may reach before an absolute path reads better.
#: Two covers the shipped layout (`../../.env` from a folder beside the repo);
#: beyond that a `../../../../../..` chain is technically correct and useless
#: to the person reviewing the manifest — and it breaks the moment either end
#: moves, which is the opposite of what relative paths are for.
MAX_RELATIVE_HOPS = 2


def relative_source(source: Path, manifest: Path) -> str:
    """How the manifest should spell its source path.

    Relative when the `.env` is NEAR the manifest, because a pair kept
    together should survive being moved or checked out elsewhere. Absolute
    otherwise — including when no relative path can be formed at all, as
    happens across drives on Windows.
    """
    import os

    try:
        relative = Path(os.path.relpath(source, manifest.parent))
    except ValueError:
        return Path(source).as_posix()
    if sum(1 for part in relative.parts if part == "..") > MAX_RELATIVE_HOPS:
        return Path(source).as_posix()
    return relative.as_posix()


def scaffold(source: Path, manifest: Path, domain: str = DEFAULT_DOMAIN,
             entry_names: tuple = (), as_argument: Optional[str] = None) -> str:
    """A starter manifest, listing what the source actually carries.

    The entry names are written as a COMMENT rather than as configuration:
    they are what an author needs in front of them to write an `only:` list,
    and putting them in the document itself would make the manifest go stale
    the moment a key is added to the `.env`. No value is ever written.

    `as_argument` is the name of the manifest as a shell takes it in the
    commands the head of the file shows - in quotes where it holds a blank.
    The command line gives it; without it the name stands as it is.
    """
    listed = "\n".join(f"#   {name}" for name in entry_names) or "#   (none found)"
    named = as_argument or manifest.name
    about_the_placeholder = "\n".join("  " + line for line in PLACEHOLDER_COMMENT)
    return f"""# Credentials manifest for `mdb credentials-apply --manifest`.
#
# It says WHO gets keys and WHERE THOSE KEYS COME FROM. It never contains a
# key, so unlike the .env it is meant to be committed and reviewed in a diff:
# who may call which provider is a decision worth having a history of.
#
#   mdb credentials-apply  --manifest {named} --dry-run
#   mdb credentials-apply  --manifest {named}
#   mdb credentials-report --manifest {named}
#
# Identities are added and marked for removal with a command as well as by
# hand; both edit this file and leave its comments alone, but for the one
# about the placeholder, which leaves with the placeholder:
#
#   mdb credentials-add    group LLMConsumers --manifest {named}
#   mdb credentials-remove group FormerTeam   --manifest {named}
#
# A GROUP credential reaches only the members of that group: equip a group
# the people who open the Prompt Builder are members of. A USER credential
# overrides a GROUP one, which is how "the department shares a key, except the
# one person with their own quota" is expressed. Writing a credential REPLACES
# it whole, so each source must carry everything that identity should end up
# with. Creating the domain, or any group credential, needs SAS administrator
# rights.

domain: {domain}

# Relative paths resolve against THIS file, so the manifest and the .env files
# it names can be moved together.
source: {relative_source(source, manifest)}

# Entries that source carries today. Use these names in an `only:` list - they
# are how the DOMAIN spells them, not how the .env does (OpenAI, not
# OPENAI_API_KEY). This list is a comment because it goes stale the moment a
# key is added; `mdb credentials-init --force` refreshes it.
{listed}

identities:
{about_the_placeholder}
  - type: group
    id: {PLACEHOLDER_ID}

  # Narrow an identity to some of the entries:
  # - type: group
  #   id: RAGEngineers
  #   only: [PGVECTOR_RAG_USER, PGVECTOR_RAG_PW]

  # The service account a scheduled flow runs as - without it, a job launched
  # in a context that runs servers as a service account resolves the domain as
  # THAT identity and finds nothing:
  # - type: user
  #   id: sas-be-sa

  # A different key set for one team:
  # - type: group
  #   id: FraudAnalytics
  #   source: envs/production.env

  # A credential that is to be removed. The next `mdb credentials-apply`
  # deletes it; a line that is merely deleted from this file removes nothing:
  # - type: group
  #   id: FormerTeam
  #   state: absent
"""


# ---------------------------------------------------------------------------
# Editing the manifest in place
# ---------------------------------------------------------------------------
@dataclass
class ManifestEdit:
    """A manifest after one identity was added to it or changed in it.

    `changes` says what changed in words fit to print: entry names, paths and
    states, never a value.
    """

    text: str
    manifest: Manifest
    identity: Identity
    #: "added" | "placeholder" | "updated" | "unchanged" — placeholder when
    #: the new identity took the place of the scaffold's placeholder.
    outcome: str
    changes: tuple = ()


def _flow(value) -> str:
    """A scalar, or a list in flow style, as YAML spells it — quoted only
    where YAML would otherwise read something else (`'123'`, `'no'`)."""
    import yaml

    listed = isinstance(value, (list, tuple))
    dumped = yaml.safe_dump(
        [str(item) for item in value] if listed else [str(value)],
        default_flow_style=True, allow_unicode=True, width=2 ** 31).strip()
    return dumped if listed else dumped[1:-1]


def _content_end(node) -> int:
    """Index just past the last character a node really occupies.

    A block collection's own end mark sits at the NEXT token, past every
    comment and blank line that follows it, so its end is taken from its last
    leaf. Scalars and flow collections end where their text ends.
    """
    import yaml

    if isinstance(node, yaml.ScalarNode) or node.flow_style or not node.value:
        return node.end_mark.index
    last = node.value[-1]
    return _content_end(last[1] if isinstance(node, yaml.MappingNode) else last)


def _line_start(text: str, index: int) -> int:
    return text.rfind("\n", 0, index) + 1


def _line_after(text: str, index: int) -> int:
    end = text.find("\n", index)
    return len(text) if end < 0 else end + 1


def _appended(text: str, at: int, block: str, eol: str) -> str:
    """Whole lines to insert at `at`. A file whose last line has no line end
    keeps that property: the block then starts with the missing line end
    instead of finishing with one."""
    if at == len(text) and text and not text.endswith("\n"):
        return eol + block[:-len(eol)]
    return block


def _identities_node(text: str, path: Path):
    """The `identities` key and its list as the parser sees them: nodes that
    carry the position of every key and value in the text."""
    import yaml

    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        raise _not_yaml(path, exc) from None
    name = listed = None
    if isinstance(root, yaml.MappingNode):
        for key, value in root.value:
            if isinstance(key, yaml.ScalarNode) and key.value == "identities":
                name, listed = key, value
    if listed is None:
        raise CredentialError(
            f"{path}: there is no 'identities' list to add to — write a "
            "manifest with `mdb credentials-init`")
    empty = (isinstance(listed, yaml.ScalarNode)
             and listed.start_mark.index == listed.end_mark.index)
    block = (isinstance(listed, yaml.SequenceNode) and not listed.flow_style
             and all(isinstance(item, yaml.MappingNode) and item.value
                     for item in listed.value))
    if not (empty or block):
        raise CredentialError(
            f"{path}: 'identities' is not a block list (one `- ` line per "
            "identity), so it cannot be edited in place — edit the file by hand")
    return name, listed


def _cut(text: str, node, position: int) -> Optional[tuple]:
    """The span to delete so that one key of an identity is gone, or None
    when it cannot be taken out without touching its neighbours or a
    comment - a note on the line of the key stays where it is."""
    key, value = node.value[position]
    end = _content_end(value)
    if node.flow_style:
        if position > 0:
            span = _content_end(node.value[position - 1][1]), end
        elif len(node.value) > 1:
            span = key.start_mark.index, node.value[1][0].start_mark.index
        else:
            return None
    else:
        start = _line_start(text, key.start_mark.index)
        if text[start:key.start_mark.index].strip():
            return None             # the key shares its line with the dash
        span = start, _line_after(text, end)
    around = (text[span[0]:key.start_mark.index] + text[key.end_mark.index:value.start_mark.index]
              + text[end:span[1]])
    return None if "#" in around else span


class _CommentInList(Exception):
    """A list that is to be rewritten holds a comment a rewrite would drop,
    or would leave beside another entry. Carries the key of the list."""


def _comment_in_list(text: str, listed) -> bool:
    """Whether a list holds a comment: between its entries, inside the
    brackets of a list written `[…]`, or - in a list written one entry per
    line - behind its last entry, on that entry's line. Outside its entries
    a `#` in the text of a list is a comment and nothing else."""
    edges = [listed.start_mark.index]
    for item in listed.value:
        edges += [item.start_mark.index, _content_end(item)]
    end = _content_end(listed)
    edges.append(end if listed.flow_style else _line_after(text, end))
    return any("#" in text[start:stop] for start, stop in zip(edges[0::2], edges[1::2]))


def _placeholder_comment(text: str, item) -> Optional[tuple]:
    """(start, end) of the lines right above an identity where they are the
    comment the scaffold writes above its placeholder, word for word; None
    where they are anything else - a comment its author changed stays."""
    end = _line_start(text, item.start_mark.index)
    above = text[:end].splitlines(keepends=True)[-len(PLACEHOLDER_COMMENT):]
    if [line.strip() for line in above] != list(PLACEHOLDER_COMMENT):
        return None
    return end - sum(len(line) for line in above), end


def _item_splices(text: str, node, sets: dict, drop: tuple, eol: str) -> list:
    """(start, end, replacement) for one existing identity: `sets` are keys
    to write — replaced where they are, appended where they are not — and
    `drop` are keys to take out. An identity written `{…}` stays that way.
    Raises _CommentInList where a list that is to be rewritten holds a
    comment."""
    import yaml

    pairs = {key.value: (position, value)
             for position, (key, value) in enumerate(node.value)}
    sets = dict(sets)
    splices = []
    for name in drop:
        if name not in pairs:
            continue
        cut = _cut(text, node, pairs[name][0])
        if cut is None:
            # `state: present` says the same as no `state` line at all.
            sets[name] = "present"
        else:
            splices.append((cut[0], cut[1], ""))

    fresh = []
    for name, new in sets.items():
        if name not in pairs:
            fresh.append(f"{name}: {_flow(new)}")
            continue
        value = pairs[name][1]
        start, end = value.start_mark.index, _content_end(value)
        if isinstance(value, yaml.SequenceNode) and _comment_in_list(text, value):
            raise _CommentInList(name)
        if (isinstance(value, yaml.SequenceNode) and not value.flow_style
                and isinstance(new, (list, tuple))):
            # A list written one name per line stays one name per line.
            joint = eol + " " * value.start_mark.column
            rendered = joint.join("- " + _flow(item) for item in new)
        else:
            rendered = _flow(new)
            if start == end:                # `key:` with nothing after it
                rendered = " " + rendered
        splices.append((start, end, rendered))

    if fresh:
        last = _content_end(node.value[-1][1])
        if node.flow_style:
            splices.append((last, last, "".join(", " + pair for pair in fresh)))
        else:
            at = _line_after(text, last)
            indent = " " * node.value[0][0].start_mark.column
            block = "".join(indent + pair + eol for pair in fresh)
            gone = [start for start, end, new in splices if end == at and not new]
            if gone and at == len(text) and not text.endswith("\n"):
                # the last line of a file without a line end is cut: the new keys take its place, and the
                # file still ends without one
                splices.append((gone[0], gone[0], block[:-len(eol)]))
            else:
                splices.append((at, at, _appended(text, at, block, eol)))
    return splices


def _entry_splice(text: str, listed_under, listed, who: Identity,
                  source: Optional[str], eol: str) -> tuple:
    """(start, end, replacement) that appends one identity, in block style,
    after the last one the list holds."""
    import yaml

    gap = ""
    if isinstance(listed, yaml.SequenceNode):
        column = listed.start_mark.column
        last = listed.value[-1]
        end = _content_end(last)
        # Identities that span lines are set apart by a blank line, the way
        # the scaffold and the shipped example write them; a list of one-line
        # `{…}` identities stays compact.
        if "\n" in text[last.start_mark.index:end]:
            gap = eol
    else:                                   # `identities:` with nothing under it
        column = listed_under.start_mark.column + 2
        end = listed.end_mark.index
    at = _line_after(text, end)
    dash = " " * column
    lines = [f"{dash}- type: {_flow(who.type)}", f"{dash}  id: {_flow(who.id)}"]
    if who.only:
        lines.append(f"{dash}  only: {_flow(who.only)}")
    if source:
        lines.append(f"{dash}  source: {_flow(source)}")
    if who.absent:
        lines.append(f"{dash}  state: absent")
    block = gap + "".join(line + eol for line in lines)
    return at, at, _appended(text, at, block, eol)


def edit_identity(text: str, path: Path, type: str, id: str,
                  only: Optional[tuple] = None, source: Optional[str] = None,
                  state: Optional[str] = None) -> ManifestEdit:
    """Add one identity to a manifest's text, or change the one that is there.

    `only`, `source` and `state` are written when given and left as they are
    when None; `source` is the path as the manifest should spell it. An
    identity is the same one when type and id are equal as the loader reads
    them. The scaffold's placeholder is replaced by the first identity that is
    added. Returns the new text — nothing is written here.

    Only the identity itself is touched. Every other character of the text —
    comments, blank lines, order, line ends, the other identities — is
    carried over as it is, because the edit is a splice at the positions the
    YAML parser reports and not a dump of the parsed document. What comes out
    is read back with the loader and compared with what was intended; a
    layout the splice does not handle is refused rather than guessed at.

    One comment goes: the one the scaffold wrote above its placeholder
    (PLACEHOLDER_COMMENT), with the placeholder it is about, where it still
    stands there word for word. And one comment is a reason to refuse: an
    `only:` list that is to be rewritten and holds a comment between its
    entries is left as it is, with a CredentialError, because nothing says
    which entry of the new list such a comment belongs to.
    """
    from dataclasses import replace

    Identity(type=type, id=id)              # refuses a wrong type or an empty id
    before = Manifest.parse(text, path, allow_empty=True)
    listed_under, listed = _identities_node(text, path)

    same = [index for index, who in enumerate(before.identities)
            if (who.type, who.id) == (type, id)]
    if len(same) > 1:
        raise CredentialError(
            f"{path}: {type} '{id}' is listed {len(same)} times — make it one "
            "entry by hand first")
    placeholders = [index for index, who in enumerate(before.identities)
                    if who.id == PLACEHOLDER_ID]
    if same:
        index, outcome = same[0], "updated"
    elif placeholders:
        index, outcome = placeholders[0], "placeholder"
    else:
        index, outcome = None, "added"
    current = before.identities[index] if index is not None else Identity(type=type, id=id)

    wanted = replace(current, type=type, id=id)
    changes = []
    if only is not None and set(only) != set(current.only):
        wanted = replace(wanted, only=tuple(str(name) for name in only))
        changes.append(f"only: {', '.join(current.only) or 'every entry'} -> "
                       f"{', '.join(wanted.only) or 'every entry'}")
    if source is not None and (path.parent / source).resolve() != current.source:
        wanted = replace(wanted, source=(path.parent / source).resolve())
        was = relative_source(current.source, path) if current.source else "the manifest's source"
        changes.append(f"source: {was} -> {source}")
    if state is not None and state != current.state:
        wanted = replace(wanted, state=state)
        changes.append(f"state: {current.state} -> {state}")
    if outcome == "updated" and not changes:
        return ManifestEdit(text, before, current, "unchanged")

    eol = "\r\n" if "\r\n" in text else "\n"
    if index is None:
        splices = [_entry_splice(text, listed_under, listed, wanted,
                                 source if wanted.source else None, eol)]
    else:
        sets: dict = {}
        if outcome == "placeholder":
            if wanted.type != current.type:
                sets["type"] = wanted.type
            sets["id"] = wanted.id
        if wanted.only != current.only:
            sets["only"] = wanted.only
        if wanted.source != current.source:
            sets["source"] = source
        if wanted.absent and not current.absent:
            sets["state"] = "absent"
        drop = ("state",) if current.absent and not wanted.absent else ()
        try:
            splices = _item_splices(text, listed.value[index], sets, drop, eol)
        except _CommentInList as held:
            raise CredentialError(
                f"{path}: the `{held.args[0]}:` list of {type} '{id}' holds a comment, which a rewrite of the "
                "list would drop or leave beside another entry — nothing was written; edit the list by "
                "hand") from None
        if outcome == "placeholder":
            # the comment the scaffold wrote about its placeholder leaves with it
            comment = _placeholder_comment(text, listed.value[index])
            if comment:
                splices.append((comment[0], comment[1], ""))

    edited = text
    for start, end, new in sorted(splices, reverse=True):
        edited = edited[:start] + new + edited[end:]

    # The proof: what was spliced must load as exactly the manifest that was
    # there plus this one change, or it is not written.
    intended = list(before.identities)
    if index is None:
        intended.append(wanted)
    else:
        intended[index] = wanted
    try:
        after = Manifest.parse(edited, path)
        proven = (after.identities == intended and after.domain == before.domain
                  and after.source == before.source)
    except CredentialError:
        proven = False
    if not proven:
        raise CredentialError(
            f"{path}: the manifest is laid out in a way this edit cannot be "
            "made in safely — nothing was written; edit the file by hand")
    # A new identity has no "before" to tell a change against.
    return ManifestEdit(edited, after, wanted, outcome,
                        tuple(changes) if outcome == "updated" else ())


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------
@dataclass
class Step:
    """What will happen to one identity. Carries NAMES and counts, never values."""

    identity: Identity
    entry_names: tuple
    #: "create" | "replace" | "remove" — replace when a credential already
    #: exists, remove when the manifest marks the identity `state: absent`.
    action: str = "create"
    #: Who last wrote the existing credential, when the server says.
    existing_by: str = ""
    existing_at: str = ""
    #: Whether a credential was there when the plan was made. Only a remove
    #: step reads it: removing what is already gone is reported, not failed.
    found: bool = False
    #: Why this identity is being skipped, or "" when it is not.
    problem: str = ""

    @property
    def ok(self) -> bool:
        return not self.problem


def planned_entries(manifest: Manifest, identity: Identity) -> tuple:
    """(entry names, problem) for one identity — what its source would give
    it, or why it cannot be equipped. NAMES only, never a value.

    One function, so that `credentials-apply` and `credentials-add` refuse
    the same thing in the same words: an `only` name the source does not
    carry is as wrong when it is typed as when it is applied.
    """
    source = identity.source or manifest.source
    if source is None:
        return (), ("no source file: set 'source' on the identity or a default at "
                    "the top of the manifest")
    if not Path(source).is_file():
        return (), f"source file not found: {source}"
    entries = map_entries(read_name_value_file(Path(source)), identity.verbatim)
    if identity.only:
        missing = [name for name in identity.only if name not in entries]
        entries = {name: value for name, value in entries.items()
                   if name in identity.only}
        if missing:
            return tuple(sorted(entries)), (
                "the source does not carry " + ", ".join(sorted(missing))
                + " — 'only' names entries as the DOMAIN spells them "
                  "(OpenAI, PGVECTOR_RAG_PW), not as the .env does")
    if not entries:
        return (), (
            f"no recognised entries in {source} — expected provider keys "
            "(OPENAI_API_KEY, …), <BACKEND>_RAG_USER/_PW pairs and/or "
            "<BACKEND>_HOST/_PORT/_DB/_SSLMODE settings")
    return tuple(sorted(entries)), ""


def build_steps(manifest: Manifest, existing) -> list:
    """One Step per identity: what it would get, and what is already there.

    `existing` is called with (identity) and returns the credential body or
    None. It cannot report CONTENT — the API returns no secrets on a GET — so
    a replace step honestly says the current entries are unknown rather than
    computing a diff nobody can compute.
    """
    steps = []
    for identity in manifest.identities:
        if identity.absent:
            # A removal reads no source and carries no entries: there is
            # nothing to write, only an item to delete. It is planned before
            # any source check, so a manifest can remove the credential of an
            # identity whose key file is long gone.
            body = existing(identity)
            step = Step(identity, (), action="remove", found=body is not None)
        else:
            names, problem = planned_entries(manifest, identity)
            if problem:
                steps.append(Step(identity, names, problem=problem))
                continue
            body = existing(identity)
            step = Step(identity, names, action="replace" if body else "create",
                        found=body is not None)
        if body:
            step.existing_by = str(body.get("modifiedBy") or body.get("createdBy") or "")
            step.existing_at = str(body.get("modifiedTimeStamp")
                                   or body.get("creationTimeStamp") or "")
        steps.append(step)
    return steps


def entries_for(manifest: Manifest, identity: Identity) -> dict:
    """The entries one identity should end up with. Values included — callers
    hand this straight to `encode()` and must not print it."""
    source = identity.source or manifest.source
    entries = map_entries(read_name_value_file(Path(source)), identity.verbatim)
    if identity.only:
        entries = {name: value for name, value in entries.items()
                   if name in identity.only}
    return entries


# ---------------------------------------------------------------------------
# Talking to the service
# ---------------------------------------------------------------------------
def _segment(value: str) -> str:
    """A value as one segment of an address: what would end the segment or
    the path (`/`, `?`, `#`), a `%`, a blank and what else an address does
    not carry as it is, percent-encoded. Every other character a segment may
    hold (`@`, `:`, `+`, ...) stays as it is. `.` and `..` alone are refused:
    the HTTP client reads them as a step of the path, encoded or not."""
    from urllib.parse import quote

    if value in (".", ".."):
        raise CredentialError(f"'{value}' cannot be named in the address of a credential: it is read as a step "
                              "of the path")
    return quote(value, safe="!$&'()*+,;=:@")


def credential_path(domain: str, identity: Identity) -> str:
    """The address of one credential. The domain and the id are one segment
    each (`_segment`): an id with `#` or `?` was cut there by the HTTP client,
    and one with `/../` reached another path - a GET, a PUT or a DELETE of
    another identity."""
    return f"/credentials/domains/{_segment(domain)}/{identity.path_segment}/{_segment(identity.id)}"


def fetch_credential(session, domain: str, identity: Identity) -> Optional[dict]:
    """The credential's METADATA, or None when the identity has none.

    Never carries secrets: the service omits the map entirely on a GET, which
    is why nothing downstream can diff content.
    """
    response = session.get(credential_path(domain, identity))
    if response.status_code == 404:
        return None
    if response.status_code >= 300:
        raise CredentialError(
            f"reading the credential for {identity.type} '{identity.id}' failed: "
            f"HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError:
        return {}


def ensure_domain(session, domain: str) -> None:
    """Create or update the domain itself (idempotent PUT).

    Requires administrator rights. A user may (re)create their own credential
    in a domain that already exists, so this is the step that most often needs
    someone else to have gone first.
    """
    import json

    response = session.put(
        f"/credentials/domains/{_segment(domain)}",
        data=json.dumps({"id": domain, "type": DOMAIN_TYPE,
                         "description": DOMAIN_DESCRIPTION}),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    if response.status_code >= 300:
        raise CredentialError(
            f"creating or updating the domain '{domain}' failed: HTTP "
            f"{response.status_code} — creating a domain requires SAS "
            "administrator rights")


def put_credential(session, domain: str, identity: Identity, entries: dict) -> None:
    """Write one identity's credential. PUT REPLACES the whole secrets map."""
    import json

    body = {
        "domainId": domain,
        "domainType": DOMAIN_TYPE,
        "identityType": identity.type,
        "identityId": identity.id,
        "properties": {},
        "secrets": encode(entries),
    }
    response = session.put(
        credential_path(domain, identity),
        data=json.dumps(body),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    if response.status_code >= 300:
        detail = ""
        if response.status_code in (401, 403):
            detail = (" — writing a GROUP credential requires SAS "
                      "administrator rights; a user may write their own")
        raise CredentialError(
            f"storing the credential for {identity.type} '{identity.id}' failed: "
            f"HTTP {response.status_code}{detail}")


def delete_credential(session, domain: str, identity: Identity) -> bool:
    """Remove one identity's credential. True when something was removed."""
    response = session.delete(credential_path(domain, identity))
    if response.status_code == 404:
        return False
    if response.status_code >= 300:
        raise CredentialError(
            f"removing the credential for {identity.type} '{identity.id}' failed: "
            f"HTTP {response.status_code}")
    return True
