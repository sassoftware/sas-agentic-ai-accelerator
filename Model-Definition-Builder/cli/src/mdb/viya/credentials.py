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

#: `.env` variable -> domain entry name, for the LLM providers. Mirrors the
#: shell scripts exactly: the two must agree or an identity equipped by one
#: tool would be missing keys under the other's names.
PROVIDER_ENTRIES = {
    "OPENAI_API_KEY": "OpenAI",
    "ANTHROPIC_API_KEY": "Anthropic",
    "GEMINI_API_KEY": "Google",
    "OPENROUTER_API_KEY": "OpenRouter",
    "AZURE_OPENAI_API_KEY": "Azure OpenAI",
    "MISTRAL_API_KEY": "Mistral",
    "VOYAGE_API_KEY": "Voyage.ai",
    "HUGGINGFACE_API_KEY": "HuggingFace",
    "AWS_BEDROCK_API_KEY": "AWS Bedrock",
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

    def __post_init__(self) -> None:
        if self.type not in IDENTITY_TYPES:
            raise CredentialError(
                f"identity {self.id!r}: type must be one of "
                f"{', '.join(IDENTITY_TYPES)}, not {self.type!r}")
        if not str(self.id).strip():
            raise CredentialError("an identity with no id cannot be equipped")

    @property
    def path_segment(self) -> str:
        """`users` or `groups` — the collection the item lives under."""
        return "users" if self.type == "user" else "groups"


@dataclass
class Manifest:
    """Who to equip, with what, in which domain."""

    domain: str = DEFAULT_DOMAIN
    source: Optional[Path] = None
    identities: list = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        """Read a credentials manifest (YAML). Paths resolve against it."""
        import yaml

        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except OSError as exc:
            raise CredentialError(f"cannot read {path}: {exc}") from exc
        except yaml.YAMLError as exc:
            raise CredentialError(f"{path} is not valid YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise CredentialError(f"{path}: expected a mapping at the top level")

        base = path.parent
        default_source = raw.get("source")
        listed = raw.get("identities")
        if not isinstance(listed, list) or not listed:
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
             entry_names: tuple = ()) -> str:
    """A starter manifest, listing what the source actually carries.

    The entry names are written as a COMMENT rather than as configuration:
    they are what an author needs in front of them to write an `only:` list,
    and putting them in the document itself would make the manifest go stale
    the moment a key is added to the `.env`. No value is ever written.
    """
    listed = "\n".join(f"#   {name}" for name in entry_names) or "#   (none found)"
    return f"""# Credentials manifest for `mdb credentials-apply --manifest`.
#
# It says WHO gets keys and WHERE THOSE KEYS COME FROM. It never contains a
# key, so unlike the .env it is meant to be committed and reviewed in a diff:
# who may call which provider is a decision worth having a history of.
#
#   mdb credentials-apply  --manifest {manifest.name} --dry-run
#   mdb credentials-apply  --manifest {manifest.name}
#   mdb credentials-report --manifest {manifest.name}
#
# A USER credential overrides a GROUP one, which is how "the department shares
# a key, except the one person with their own quota" is expressed. Writing a
# credential REPLACES it whole, so each source must carry everything that
# identity should end up with. Creating the domain, or any group credential,
# needs SAS administrator rights.

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
  # Replace with the groups and users this deployment should equip.
  - type: group
    id: CHANGE-ME-to-a-real-group

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
"""


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------
@dataclass
class Step:
    """What will happen to one identity. Carries NAMES and counts, never values."""

    identity: Identity
    entry_names: tuple
    #: "create" | "replace" — replace when a credential already exists.
    action: str = "create"
    #: Who last wrote the existing credential, when the server says.
    existing_by: str = ""
    existing_at: str = ""
    #: Why this identity is being skipped, or "" when it is not.
    problem: str = ""

    @property
    def ok(self) -> bool:
        return not self.problem


def build_steps(manifest: Manifest, existing) -> list:
    """One Step per identity: what it would get, and what is already there.

    `existing` is called with (identity) and returns the credential body or
    None. It cannot report CONTENT — the API returns no secrets on a GET — so
    a replace step honestly says the current entries are unknown rather than
    computing a diff nobody can compute.
    """
    steps = []
    for identity in manifest.identities:
        source = identity.source or manifest.source
        if source is None:
            steps.append(Step(identity, (), problem=(
                "no source file: set 'source' on the identity or a default at "
                "the top of the manifest")))
            continue
        if not Path(source).is_file():
            steps.append(Step(identity, (), problem=f"source file not found: {source}"))
            continue
        entries = map_entries(read_name_value_file(Path(source)), identity.verbatim)
        if identity.only:
            missing = [name for name in identity.only if name not in entries]
            entries = {name: value for name, value in entries.items()
                       if name in identity.only}
            if missing:
                steps.append(Step(identity, tuple(sorted(entries)), problem=(
                    "the source does not carry " + ", ".join(sorted(missing))
                    + " — 'only' names entries as the DOMAIN spells them "
                      "(OpenAI, PGVECTOR_RAG_PW), not as the .env does")))
                continue
        if not entries:
            steps.append(Step(identity, (), problem=(
                f"no recognised entries in {source} — expected provider keys "
                "(OPENAI_API_KEY, …), <BACKEND>_RAG_USER/_PW pairs and/or "
                "<BACKEND>_HOST/_PORT/_DB/_SSLMODE settings")))
            continue

        body = existing(identity)
        step = Step(identity, tuple(sorted(entries)),
                    action="replace" if body else "create")
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
def credential_path(domain: str, identity: Identity) -> str:
    return f"/credentials/domains/{domain}/{identity.path_segment}/{identity.id}"


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
            f"reading the credential for {identity.type} {identity.id!r} failed: "
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
        f"/credentials/domains/{domain}",
        data=json.dumps({"id": domain, "type": DOMAIN_TYPE,
                         "description": DOMAIN_DESCRIPTION}),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    if response.status_code >= 300:
        raise CredentialError(
            f"creating or updating the domain {domain!r} failed: HTTP "
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
            f"storing the credential for {identity.type} {identity.id!r} failed: "
            f"HTTP {response.status_code}{detail}")


def delete_credential(session, domain: str, identity: Identity) -> bool:
    """Remove one identity's credential. True when something was removed."""
    response = session.delete(credential_path(domain, identity))
    if response.status_code == 404:
        return False
    if response.status_code >= 300:
        raise CredentialError(
            f"removing the credential for {identity.type} {identity.id!r} failed: "
            f"HTTP {response.status_code}")
    return True
