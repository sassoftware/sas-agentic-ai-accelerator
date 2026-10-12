# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bring a SAS Content folder to the state of the checkout.

`mdb content-deploy` reads the content manifest (core/content.py) and, entry
by entry, makes the content folder hold what the checkout holds:

* a folder is created when it is missing;
* a file is uploaded - a repository file with LF line ends, a generated file
  as mdb writes it - and replaced when its bytes differ;
* a custom step is REGISTERED through /dataFlows/steps (a .step uploaded as a
  file renders as an empty step editor) and an existing one is updated IN
  PLACE with PUT, which keeps its id: saved flows reference a step by id, and
  a step that is deleted and posted again gets a new one;
* a job definition is created, or updated in place with its ETag: the program
  as `code`, an HTML form as the property `form`, the parameters.

A report is not deployed: it is authored in SAS Visual Analytics and reaches a
site with the transfer package. Nothing that is not in the manifest is
touched, and nothing is removed - the one delete this module sends is of a
file it replaces in the same step, because the Files service is given the new
content as a new file.

Two things earlier runs against a live system showed, and this handles:

* `POST /files/files` can answer 409 right after the old copy of the file was
  deleted, and 201 a little later. The upload is tried again
  (`UPLOAD_RETRIES`), and a file that is still not there afterwards is
  reported as deleted and not uploaded - never passed over.
* a step updated with PUT must come back under the id it had.

Every request goes through a session with `get` / `post` / `put` / `delete`
that take a path, as sasctl's does, so the tests run this against a fake.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..core import content as _content

STEP_MEDIA = "application/vnd.sas.data.flow.step+json"
DEFINITION_MEDIA = "application/vnd.sas.job.definition+json"

#: How often an upload that is answered 409 is tried again, and the seconds
#: waited before each further try.
UPLOAD_RETRIES = 5
UPLOAD_WAIT = (2, 5, 10, 20, 30)

#: The service a member of a folder belongs to, by the manifest's kind.
_MEMBER_URI = {"file": "/files/files/", "step": "/dataFlows/steps/",
               "jobDefinition": "/jobDefinitions/definitions/", "report": "/reports/reports/",
               "folder": "/folders/folders/"}

EQUAL, UPDATED, CREATED, NOT_DEPLOYED, FAILED = "equal", "updated", "created", "not deployed", "FAILED"


class DeployError(RuntimeError):
    pass


def _failure(what: str, response) -> DeployError:
    return DeployError(f"{what}: HTTP {response.status_code} {str(getattr(response, 'text', ''))[:300]}")


# ---------------------------------------------------------------------------
# folders
# ---------------------------------------------------------------------------
def find_folder(session, path: str) -> Optional[dict]:
    """The folder at a SAS Content path, or None when there is none."""
    response = session.get("/folders/folders/@item", params={"path": path})
    if response.status_code == 404:
        return None
    if not response.ok:
        raise _failure(f"Looking up the folder {path} failed", response)
    return response.json()


def create_folder(session, name: str, parent_id: Optional[str], description: str = "") -> dict:
    """Create one folder below `parent_id` (at the top of SAS Content when
    there is none)."""
    body = {"name": name}
    if description:
        body["description"] = description
    params = {"parentFolderUri": f"/folders/folders/{parent_id}"} if parent_id else None
    response = session.post("/folders/folders", params=params, data=json.dumps(body),
                            headers={"Content-Type": "application/json", "Accept": "application/json"})
    if not response.ok:
        raise _failure(f"Creating the folder {name} failed", response)
    return response.json()


def folder_members(session, folder_id: str) -> list:
    """Every member of a folder, read page by page."""
    members, start = [], 0
    while True:
        response = session.get(f"/folders/folders/{folder_id}/members", params={"limit": 200, "start": start})
        if not response.ok:
            raise _failure("Listing the members of a folder failed", response)
        page = response.json()
        items = page.get("items") or []
        members.extend(items)
        start += len(items)
        if not items or start >= int(page.get("count") or 0):
            return members


def _members_named(members: list, name: str, kind: str) -> list:
    prefix = _MEMBER_URI[kind]
    return [m for m in members if m.get("name") == name and str(m.get("uri") or "").startswith(prefix)]


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------
def file_content(session, uri: str) -> bytes:
    response = session.get(f"{uri}/content")
    if not response.ok:
        raise _failure(f"Reading {uri} failed", response)
    return response.content


def upload_file(session, folder_id: str, name: str, data: bytes,
                sleep: Callable[[float], None] = time.sleep) -> int:
    """Upload a file into a folder; returns how often a 409 was answered
    before the upload was taken.

    A raw upload is named by the Content-Disposition header - without it the
    Files service mints a `FileResource<timestamp>` name. A 409 is the answer
    seen right after the old copy of the same name was deleted; the same
    request was taken a little later, so it is sent again up to
    UPLOAD_RETRIES times before it counts as a failure.
    """
    conflicts = 0
    while True:
        response = session.post(
            "/files/files",
            params={"parentFolderUri": f"/folders/folders/{folder_id}", "filename": name},
            data=data,
            headers={"Content-Type": "application/octet-stream",
                     "Content-Disposition": f'attachment; filename="{name}"',
                     "Accept": "application/json"})
        if response.ok:
            return conflicts
        if response.status_code != 409 or conflicts >= UPLOAD_RETRIES:
            raise _failure(f"Uploading {name} failed"
                           + (f" after {conflicts + 1} tries" if conflicts else ""), response)
        sleep(UPLOAD_WAIT[min(conflicts, len(UPLOAD_WAIT) - 1)])
        conflicts += 1


def delete_object(session, uri: str) -> None:
    response = session.delete(uri)
    if not response.ok and response.status_code != 404:
        raise _failure(f"Deleting {uri} failed", response)


# ---------------------------------------------------------------------------
# custom steps
# ---------------------------------------------------------------------------
def get_step(session, uri: str) -> dict:
    response = session.get(uri, headers={"Accept": STEP_MEDIA})
    if not response.ok:
        raise _failure(f"Reading the step {uri} failed", response)
    return response.json()


def update_step(session, uri: str, definition: dict) -> None:
    """Update a registered step in place. The id is part of the body and of
    the address, and the answer has to carry it too: a step that came back
    under another id would have lost every flow that uses it."""
    step_id = uri.rsplit("/", 1)[-1]
    body = dict(definition)
    body["id"] = step_id
    response = session.put(uri, data=json.dumps(body).encode("utf-8"),
                           headers={"Content-Type": STEP_MEDIA, "Accept": STEP_MEDIA, "If-Match": "*"})
    if not response.ok:
        raise _failure(f"Updating the step {definition.get('name')} failed", response)
    try:
        answered = response.json().get("id")
    except (ValueError, AttributeError):
        answered = None
    if answered and answered != step_id:
        raise DeployError(f"the step {definition.get('name')} was updated at {uri} and answered with the id "
                          f"{answered} - saved flows reference {step_id}")


def create_step(session, folder_id: str, raw: bytes, name: str) -> None:
    response = session.post("/dataFlows/steps", params={"parentFolderUri": f"/folders/folders/{folder_id}"},
                            data=raw, headers={"Content-Type": "application/json", "Accept": STEP_MEDIA})
    if not response.ok:
        raise _failure(f"Registering the step {name} failed", response)


# ---------------------------------------------------------------------------
# job definitions
# ---------------------------------------------------------------------------
def get_job_definition(session, uri: str) -> tuple:
    """(definition, etag)."""
    response = session.get(uri, headers={"Accept": DEFINITION_MEDIA})
    if not response.ok:
        raise _failure(f"Reading the job definition {uri} failed", response)
    return response.json(), response.headers.get("ETag", "")


def update_job_definition(session, uri: str, live: dict, wanted: dict, etag: str) -> None:
    """A full replacement of an existing definition, guarded by its ETag: what
    the definition holds beside its program, form and parameters stays."""
    body = {key: value for key, value in live.items() if key != "links"}
    body.update(wanted)
    body["id"] = live.get("id") or uri.rsplit("/", 1)[-1]
    headers = {"Content-Type": DEFINITION_MEDIA, "Accept": DEFINITION_MEDIA}
    if etag:
        headers["If-Match"] = etag
    response = session.put(uri, data=json.dumps(body).encode("utf-8"), headers=headers)
    if not response.ok:
        raise _failure(f"Updating the job definition {wanted.get('name')} failed", response)


def create_job_definition(session, folder_id: str, wanted: dict) -> None:
    response = session.post("/jobDefinitions/definitions",
                            params={"parentFolderUri": f"/folders/folders/{folder_id}"},
                            data=json.dumps(wanted).encode("utf-8"),
                            headers={"Content-Type": DEFINITION_MEDIA, "Accept": DEFINITION_MEDIA})
    if not response.ok:
        raise _failure(f"Creating the job definition {wanted.get('name')} failed", response)


# ---------------------------------------------------------------------------
# the deploy
# ---------------------------------------------------------------------------
@dataclass
class Outcome:
    """What happened to one entry."""

    entry: object
    status: str            # equal | updated | created | not deployed | FAILED
    note: str = ""


@dataclass
class DeployResult:
    outcomes: list = field(default_factory=list)
    #: 'path (type)' of what sits in a manifest folder and is not in the manifest.
    unlisted: list = field(default_factory=list)
    #: True when the content root itself was missing (and, unless it was a
    #: dry run, created).
    root_created: bool = False

    def count(self, status: str) -> int:
        return sum(1 for outcome in self.outcomes if outcome.status == status)

    @property
    def failed(self) -> list:
        return [outcome for outcome in self.outcomes if outcome.status == FAILED]


def _ensure_root(session, content_root: str, dry_run: bool) -> tuple:
    """(folder id or None, created?) for the content root. A missing root is
    created with every folder above it that is missing too."""
    found = find_folder(session, content_root)
    if found:
        return found["id"], False
    if dry_run:
        return None, True
    parent_path, _, name = content_root.rstrip("/").rpartition("/")
    parent_id = _ensure_root(session, parent_path, dry_run)[0] if parent_path else None
    return create_folder(session, name, parent_id)["id"], True


def selected(manifest, only) -> tuple:
    """(folders, objects) of the manifest that a deploy of `only` covers:
    every entry at or below one of those paths, and the folders above them.
    Everything when `only` is empty."""
    wanted = [path.strip("/") for path in only or [] if path and path.strip("/")]
    if not wanted:
        return manifest.folders(), manifest.objects()

    def within(path: str) -> bool:
        return any(path == prefix or path.startswith(prefix + "/") for prefix in wanted)

    folders = [f for f in manifest.folders()
               if within(f.path) or any(prefix.startswith(f.path + "/") for prefix in wanted)]
    return folders, [entry for entry in manifest.objects() if within(entry.path)]


def deploy(session, manifest, content_root: Optional[str] = None, dry_run: bool = False,
           emit: Optional[Callable[[Outcome], None]] = None,
           sleep: Callable[[float], None] = time.sleep, only=None) -> DeployResult:
    """Bring `content_root` to the state of the checkout; returns one Outcome
    per entry of the manifest, folders first.

    With `dry_run` nothing is written: every outcome says what a run would do.
    `emit` is called with each outcome as it is known. An entry that fails is
    reported as FAILED with the reason and the deploy goes on with the next -
    what did not reach the folder is listed, not hidden behind the first error.
    `only` restricts the run to the entries at or below the given paths (and
    the folders above them); everything else is not looked at.
    """
    content_root = (content_root or manifest.content_root).rstrip("/")
    result = DeployResult()
    folders, objects = selected(manifest, only)

    def done(entry, status: str, note: str = "") -> None:
        outcome = Outcome(entry, status, note)
        result.outcomes.append(outcome)
        if emit:
            emit(outcome)

    root_id, root_created = _ensure_root(session, content_root, dry_run)
    folder_ids: dict = {"": root_id}
    members: dict = {}

    def members_of(folder_path: str) -> list:
        if folder_path not in members:
            folder_id = folder_ids.get(folder_path)
            members[folder_path] = folder_members(session, folder_id) if folder_id else []
        return members[folder_path]

    for entry in folders:
        parent_id = folder_ids.get(entry.folder)
        try:
            found = _members_named(members_of(entry.folder), entry.name, "folder") if parent_id else []
            if found:
                folder_ids[entry.path] = str(found[0]["uri"]).rsplit("/", 1)[-1]
                done(entry, EQUAL)
            elif dry_run:
                folder_ids[entry.path] = None
                done(entry, CREATED)
            elif parent_id is None:
                done(entry, FAILED, "its parent folder could not be created")
            else:
                folder_ids[entry.path] = create_folder(session, entry.name, parent_id, entry.description)["id"]
                done(entry, CREATED)
        except DeployError as exc:
            folder_ids.setdefault(entry.path, None)
            done(entry, FAILED, str(exc))

    for entry in objects:
        folder_id = folder_ids.get(entry.folder)
        try:
            existing = _members_named(members_of(entry.folder), entry.name, entry.kind) if folder_id else []
            if entry.kind == "report":
                done(entry, NOT_DEPLOYED, "on the site" if existing else "not on the site - it comes with the package")
                continue
            if folder_id is None and not dry_run:
                done(entry, FAILED, "its folder could not be created")
                continue
            status, note = _DEPLOYERS[entry.kind](session, manifest, entry, folder_id, existing, dry_run, sleep)
            done(entry, status, note)
        except (DeployError, OSError, ValueError) as exc:
            done(entry, FAILED, str(exc))

    # what else sits in the folders of the manifest: named, and left alone
    listed = {(entry.folder, entry.name, entry.kind) for entry in manifest.all_entries()}
    for folder_path, items in sorted(members.items()):
        for member in items:
            uri = str(member.get("uri") or "")
            kind = next((k for k, prefix in _MEMBER_URI.items() if uri.startswith(prefix)), None)
            if (folder_path, member.get("name"), kind) not in listed:
                what = kind or member.get("contentType") or uri.strip("/").split("/")[0] or "object"
                name = str(member.get("name"))
                result.unlisted.append(f"{folder_path}/{name} ({what})" if folder_path else f"{name} ({what})")
    result.root_created = root_created
    return result


def _deploy_file(session, manifest, entry, folder_id, existing, dry_run, sleep) -> tuple:
    wanted = _content.file_bytes(manifest, entry)
    if not existing:
        if not dry_run:
            upload_file(session, folder_id, entry.name, wanted, sleep)
        return CREATED, ""
    if len(existing) == 1 and file_content(session, existing[0]["uri"]) == wanted:
        return EQUAL, ""
    if dry_run:
        return UPDATED, ""
    for member in existing:
        delete_object(session, member["uri"])
    try:
        conflicts = upload_file(session, folder_id, entry.name, wanted, sleep)
    except DeployError as exc:
        raise DeployError(f"the file was DELETED and NOT uploaded - run the command again ({exc})") from exc
    notes = []
    if len(existing) > 1:
        notes.append(f"replaced {len(existing)} files of that name")
    if conflicts:
        notes.append(f"the upload was answered 409 {conflicts} time(s) and then taken")
    return UPDATED, "; ".join(notes)


def _deploy_step(session, manifest, entry, folder_id, existing, dry_run, sleep) -> tuple:
    wanted = _content.step_body(manifest, entry)
    if not existing:
        if not dry_run:
            create_step(session, folder_id, manifest.source_path(entry.source).read_bytes(), entry.name)
        return CREATED, ""
    uri = existing[0]["uri"]
    step_id = uri.rsplit("/", 1)[-1]
    parts = _content.step_differences(wanted, get_step(session, uri))
    if not parts:
        return EQUAL, f"id {step_id}"
    if not dry_run:
        update_step(session, uri, wanted)
    return UPDATED, f"{', '.join(parts)}; id {step_id} kept"


def _deploy_job_definition(session, manifest, entry, folder_id, existing, dry_run, sleep) -> tuple:
    wanted = _content.job_definition_body(manifest, entry)
    if not existing:
        if not dry_run:
            create_job_definition(session, folder_id, wanted)
        return CREATED, ""
    uri = existing[0]["uri"]
    live, etag = get_job_definition(session, uri)
    parts = _content.job_differences(wanted, live)
    if not parts:
        return EQUAL, ""
    if not dry_run:
        update_job_definition(session, uri, live, wanted, etag)
    return UPDATED, ", ".join(parts)


_DEPLOYERS = {"file": _deploy_file, "step": _deploy_step, "jobDefinition": _deploy_job_definition}
