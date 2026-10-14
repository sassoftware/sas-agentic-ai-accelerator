# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bring a SAS Content folder to the state of a source - IN PLACE.

`mdb content-deploy` reads the content manifest (core/content.py) and, entry
by entry, makes the content folder hold what the checkout holds.
`mdb builders-import` runs the same code with a transfer package as the
source (core/packages.py, PackageSource): the files, custom steps and job
definitions of a package reach a site through these requests, not through the
transfer service.

* a folder is created when it is missing;
* a file is uploaded when it is missing - a repository file with LF line
  ends, a generated file as mdb writes it - and one whose bytes differ gets
  its CONTENT replaced (`PUT /files/files/{id}/content` with the ETag of the
  file). The file object stays: same id, same place in the folder;
* a custom step is REGISTERED through /dataFlows/steps (a .step uploaded as a
  file renders as an empty step editor) and an existing one is updated with
  PUT, which keeps its id: saved flows reference a step by id;
* a job definition is created, or updated in place with its ETag: the program
  as `code`, an HTML form as the property `form`, the parameters. A parameter
  the manifest marks as a site setting keeps the value the site has.

An object that exists at its path is therefore updated under ITS OWN id,
whatever id the object had on the system the content comes from. That is the
reason a package is not handed to the transfer service for these objects: it
was measured to leave a file alone that exists in the target folder under the
same name with another id.

NOTHING IS DELETED. Not a file that is replaced, and not an object that is
not in the source. Where the Files service refuses the update of a file's
content, the file is left as it is and the run says so.

THE RUN STOPS AT THE FIRST WRITE THAT FAILS. A write that was refused, or
that got no answer, ends the run: the entry is FAILED with what is known of
the object, and every entry behind it is listed as not attempted. That holds
for a folder that cannot be created as well: nothing is written into a
folder half made. An entry that fails before anything was sent - two
objects of its name in the folder, an object that cannot be read - is FAILED
too, and the run goes on: nothing was written for it.

RELEASE.JSON IS WRITTEN LAST, only by a run that covers every object of the
manifest, and only when every entry in front of it was written: the record of
a folder names a release only when the folder holds it. A run that stopped,
or in which an entry FAILED, leaves the release.json an earlier run wrote as
it is, and so does a run of a part of the manifest (`only`): one that names
release.json among its entries is refused before anything is sent (the
command refuses it before it signs in) - so the folder's own record names
the last release that was written in full, or there is none.

WHAT IS NEVER WRITTEN. An object outside the folders of the source. A member
of a folder can be a REFERENCE to an object that lives somewhere else; a
reference is told by its member type, is never followed, and is no object of
the folder. ONE NAME, ONE OBJECT: where a folder holds two objects of the
name and kind of an entry, or a reference of that name and kind - alone, or
beside the object itself - the entry is FAILED, each of them is named on its
line and none is written: nothing says which one is meant. A reference of
that name to an object of another kind fails nothing - the kind says which
is meant - and is listed with what else sits in the folder.

A report is not deployed: it is authored in SAS Visual Analytics and reaches
a site through the transfer service.

Every request goes through a session with `get` / `post` / `put` that take a
path, as sasctl's does, so the tests run this against a fake.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..core import content as _content
from .answer import answer as _answer

STEP_MEDIA = "application/vnd.sas.data.flow.step+json"
DEFINITION_MEDIA = "application/vnd.sas.job.definition+json"

#: The service a member of a folder belongs to, by the manifest's kind.
_MEMBER_URI = {"file": "/files/files/", "step": "/dataFlows/steps/",
               "jobDefinition": "/jobDefinitions/definitions/", "report": "/reports/reports/",
               "folder": "/folders/folders/"}

#: What a line calls an object of each kind.
_WORD = {"file": "file", "step": "registered step", "jobDefinition": "job definition", "report": "report",
         "folder": "folder"}

EQUAL, UPDATED, CREATED, NOT_DEPLOYED, FAILED, NOT_ATTEMPTED = (
    "equal", "updated", "created", "not deployed", "FAILED", "not attempted")

_ID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


class DeployError(RuntimeError):
    """An entry could not be brought to the source. Nothing was sent that
    changes the site, unless it is a WriteFailed."""


class WriteFailed(DeployError):
    """A write was sent and was refused, or got no answer. The run stops."""


def _failure(what: str, response) -> DeployError:
    return DeployError(f"{what}: {_answer(response)}")


def _read(what: str, call):
    """One GET. No answer at all is a DeployError like a refusal is."""
    try:
        return call()
    except DeployError:
        raise
    except Exception as exc:                                   # a timeout, a dropped connection
        raise DeployError(f"{what}: no answer ({type(exc).__name__}: {exc})") from exc


def _write(what: str, call, unknown: str = ""):
    """One write. Refused or unanswered, it is a WriteFailed: `unknown` says
    what is then known of the object."""
    try:
        response = call()
    except Exception as exc:                                   # a timeout, a dropped connection
        raise WriteFailed(f"{what}: no answer ({type(exc).__name__}: {exc})"
                          + (f" - {unknown}" if unknown else "")) from exc
    if not response.ok:
        raise WriteFailed(f"{what}: {_answer(response)}")
    return response


def _id(uri: str) -> str:
    return str(uri).rstrip("/").rsplit("/", 1)[-1]


def _answered_id(response) -> str:
    try:
        return str(response.json().get("id") or "")
    except (ValueError, AttributeError, TypeError):
        return ""


# ---------------------------------------------------------------------------
# folders
# ---------------------------------------------------------------------------
def find_folder(session, path: str) -> Optional[dict]:
    """The folder at a SAS Content path, or None when there is none."""
    response = _read(f"Looking up the folder {path} failed",
                     lambda: session.get("/folders/folders/@item", params={"path": path}))
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
    return _write(f"Creating the folder {name} failed", lambda: session.post(
        "/folders/folders", params=params, data=json.dumps(body),
        headers={"Content-Type": "application/json", "Accept": "application/json"})).json()


def folder_members(session, folder_id: str) -> list:
    """Every member of a folder, read page by page."""
    members, start = [], 0
    while True:
        response = _read("Listing the members of a folder failed", lambda: session.get(
            f"/folders/folders/{folder_id}/members", params={"limit": 200, "start": start}))
        if not response.ok:
            raise _failure("Listing the members of a folder failed", response)
        page = response.json()
        items = page.get("items") or []
        members.extend(items)
        start += len(items)
        if not items or start >= int(page.get("count") or 0):
            return members


def is_reference(member: dict) -> bool:
    """Whether a folder member is a reference to an object that lives
    elsewhere, by its member type. A member is a child unless it says so."""
    return str(member.get("type") or "child").lower() == "reference"


def _named(members: list, name: str, kind: str) -> list:
    prefix = _MEMBER_URI[kind]
    return [m for m in members if m.get("name") == name and str(m.get("uri") or "").startswith(prefix)]


def _called(member: dict, kind: Optional[str]) -> str:
    """What a line calls the object a member of a folder is - for a
    reference: the object it names. The word of its kind (_WORD), as every
    other line has it; for an object of no kind of the manifest the content
    type the Folders service gives it, and where it gives none its address."""
    if kind:
        return _WORD[kind]
    uri = str(member.get("uri") or "")
    return str(member.get("contentType") or "") or (f"object at {uri}" if uri else "object")


def _members_named(members: list, name: str, kind: str) -> list:
    """The objects of a folder with this name and kind: its children. A
    reference is not one - what it names lives in another folder."""
    return [m for m in _named(members, name, kind) if not is_reference(m)]


def _references_named(members: list, name: str, kind: str) -> list:
    """The references of a folder that carry this name and point at an
    object of this kind."""
    return [m for m in _named(members, name, kind) if is_reference(m)]


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------
def read_file(session, uri: str) -> tuple:
    """(bytes, etag, content type) of a file."""
    response = _read(f"Reading {uri} failed", lambda: session.get(f"{uri}/content"))
    if not response.ok:
        raise _failure(f"Reading {uri} failed", response)
    headers = getattr(response, "headers", None) or {}
    return response.content, headers.get("ETag") or "", headers.get("Content-Type") or ""


def file_content(session, uri: str) -> bytes:
    return read_file(session, uri)[0]


def create_file(session, folder_id: str, name: str, data: bytes) -> str:
    """Upload a new file into a folder; returns its id where the answer
    gives one. A raw upload is named by the Content-Disposition header -
    without it the Files service mints a `FileResource<timestamp>` name."""
    return _answered_id(_write(f"Uploading {name} failed", lambda: session.post(
        "/files/files",
        params={"parentFolderUri": f"/folders/folders/{folder_id}", "filename": name},
        data=data,
        headers={"Content-Type": "application/octet-stream",
                 "Content-Disposition": f'attachment; filename="{name}"',
                 "Accept": "application/json"}),
        "whether the file is in the folder is not known"))


def update_file(session, uri: str, name: str, data: bytes, etag: str = "", content_type: str = "") -> None:
    """Replace the content of a file in place. The file object stays - its
    id, its place in the folder - and the Files service takes the new bytes
    only for the version of the file the ETag names (`If-Match`).

    `etag` is the ETag of the read whose content was COMPARED with what is to
    be written: a file somebody changed after that read is refused, not
    written over. (The pass of 2026-10-12 measured that GET /files/files/{id}
    and GET .../content answer with the same ETag.) Only where that read gave
    none is the ETag of the file object asked for. The file object is asked
    for its content type, which the write declares; no name is sent: the file
    keeps the one it has."""
    response = _read(f"Reading {uri} failed", lambda: session.get(
        uri, headers={"Accept": "application/vnd.sas.file+json, application/json"}))
    if response.ok:
        etag = etag or (getattr(response, "headers", None) or {}).get("ETag") or ""
        try:
            content_type = response.json().get("contentType") or content_type
        except (ValueError, AttributeError, TypeError):
            pass
    headers = {"Content-Type": content_type or "application/octet-stream", "Accept": "application/json"}
    if etag:
        headers["If-Match"] = etag
    try:
        response = session.put(f"{uri}/content", data=data, headers=headers)
    except Exception as exc:                                   # a timeout, a dropped connection
        raise WriteFailed(f"no answer to the update of the content of {name} ({type(exc).__name__}: {exc}) - the "
                          "file holds its old or its new content, and was not removed") from exc
    if not response.ok:
        raise WriteFailed("the file was left as it is: the update of its content was refused "
                          f"({_answer(response)})")


# ---------------------------------------------------------------------------
# custom steps
# ---------------------------------------------------------------------------
def get_step(session, uri: str) -> dict:
    response = _read(f"Reading the step {uri} failed", lambda: session.get(uri, headers={"Accept": STEP_MEDIA}))
    if not response.ok:
        raise _failure(f"Reading the step {uri} failed", response)
    return response.json()


def update_step(session, uri: str, definition: dict) -> None:
    """Update a registered step in place. The id is part of the body and of
    the address, and the answer has to carry it too: a step that came back
    under another id would have lost every flow that uses it."""
    step_id = _id(uri)
    body = dict(definition)
    body["id"] = step_id
    response = _write(f"Updating the step {definition.get('name')} failed", lambda: session.put(
        uri, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": STEP_MEDIA, "Accept": STEP_MEDIA, "If-Match": "*"}),
        "the step holds its old or its new content under the id it had")
    answered = _answered_id(response)
    if answered and answered != step_id:
        raise WriteFailed(f"the step {definition.get('name')} was updated at {uri} and answered with the id "
                          f"{answered} - saved flows reference {step_id}")


def create_step(session, folder_id: str, raw: bytes, name: str) -> str:
    return _answered_id(_write(f"Registering the step {name} failed", lambda: session.post(
        "/dataFlows/steps", params={"parentFolderUri": f"/folders/folders/{folder_id}"},
        data=raw, headers={"Content-Type": "application/json", "Accept": STEP_MEDIA}),
        "whether the step is registered is not known"))


# ---------------------------------------------------------------------------
# job definitions
# ---------------------------------------------------------------------------
def get_job_definition(session, uri: str) -> tuple:
    """(definition, etag)."""
    response = _read(f"Reading the job definition {uri} failed",
                     lambda: session.get(uri, headers={"Accept": DEFINITION_MEDIA}))
    if not response.ok:
        raise _failure(f"Reading the job definition {uri} failed", response)
    return response.json(), (getattr(response, "headers", None) or {}).get("ETag", "")


def update_job_definition(session, uri: str, live: dict, wanted: dict, etag: str) -> None:
    """Replace an existing definition, guarded by its ETag. Written from the
    source: the program, the form, the parameters, and the description where
    the source gives one. Kept from the site: the id and the name, a property
    that is not the form, and whatever else the definition holds."""
    body = {key: value for key, value in live.items() if key != "links"}
    for key in ("code", "parameters", "type"):
        if key in wanted:
            body[key] = wanted[key]
    if wanted.get("description"):
        body["description"] = wanted["description"]
    kept = [p for p in live.get("properties") or [] if isinstance(p, dict) and p.get("name") != "form"]
    body["properties"] = kept + [p for p in wanted.get("properties") or [] if p.get("name") == "form"]
    body["id"] = live.get("id") or _id(uri)
    body["name"] = live.get("name") or wanted.get("name")
    headers = {"Content-Type": DEFINITION_MEDIA, "Accept": DEFINITION_MEDIA}
    if etag:
        headers["If-Match"] = etag
    _write(f"Updating the job definition {body['name']} failed",
           lambda: session.put(uri, data=json.dumps(body).encode("utf-8"), headers=headers),
           "the definition holds its old or its new content")


def create_job_definition(session, folder_id: str, wanted: dict) -> str:
    return _answered_id(_write(f"Creating the job definition {wanted.get('name')} failed", lambda: session.post(
        "/jobDefinitions/definitions",
        params={"parentFolderUri": f"/folders/folders/{folder_id}"},
        data=json.dumps(wanted).encode("utf-8"),
        headers={"Content-Type": DEFINITION_MEDIA, "Accept": DEFINITION_MEDIA}),
        "whether the definition was created is not known"))


# ---------------------------------------------------------------------------
# the deploy
# ---------------------------------------------------------------------------
@dataclass
class Outcome:
    """What happened to one entry."""

    entry: object
    status: str            # equal | updated | created | not deployed | FAILED | not attempted
    note: str = ""


@dataclass
class DeployResult:
    outcomes: list = field(default_factory=list)
    #: 'path (type)' of what sits in a manifest folder and is not in the manifest.
    unlisted: list = field(default_factory=list)
    #: True when the content root itself was missing (and, unless it was a
    #: dry run, created).
    root_created: bool = False
    #: The outcome of the entry whose write failed and ended the run.
    stopped_at: Optional[Outcome] = None

    def count(self, status: str) -> int:
        return sum(1 for outcome in self.outcomes if outcome.status == status)

    @property
    def failed(self) -> list:
        return [outcome for outcome in self.outcomes if outcome.status == FAILED]

    @property
    def written(self) -> list:
        """The entries a run wrote (in a dry run: would write)."""
        return [outcome for outcome in self.outcomes if outcome.status in (CREATED, UPDATED)]

    @property
    def not_attempted(self) -> list:
        return [outcome for outcome in self.outcomes if outcome.status == NOT_ATTEMPTED]

    def stop_line(self) -> str:
        """The sentence that says what a stopped run wrote and did not."""
        if self.stopped_at is None:
            return ""
        entry = self.stopped_at.entry
        record = any(is_release_record(outcome.entry) for outcome in self.not_attempted)
        return (f"Stopped at the first write that failed: {entry.kind} {entry.path}. Written before it: "
                f"{self.count(CREATED)} created, {self.count(UPDATED)} updated"
                f"{', and the folder the content goes into' if self.root_created else ''}. Not attempted behind it: "
                f"{len(self.not_attempted)} entr{'y' if len(self.not_attempted) == 1 else 'ies'}. Nothing was "
                "removed." + (" release.json was not written: the folder's release.json is the one an earlier run "
                              "wrote, or there is none." if record else ""))


#: Where the release record of a content folder sits below its root.
RELEASE_RECORD = "release.json"


def is_release_record(entry) -> bool:
    """Whether an entry is the folder's release.json: the file mdb generates
    (`generated: release-info`), or - in a package, where nothing says how a
    file was made - the file of that name at the root."""
    return entry.kind == "file" and (getattr(entry, "generated", None) == _content.RELEASE_INFO
                                     or entry.path == RELEASE_RECORD)


#: Why a run that covers a part of the manifest and names release.json is refused.
RECORD_NEEDS_ALL = ("release.json is the record of the whole folder and is written only by a run that covers every "
                    "entry of the manifest: this run covers a part of it (--only), so nothing was written - leave "
                    "release.json out of --only, or run without --only")


def _ensure_root(session, content_root: str, dry_run: bool, create: bool = True) -> tuple:
    """(folder id or None, the folders made for it) for the content root: no
    folder for a root that is there. A missing root is created with every
    folder above it that is missing too, and each is listed, the root last -
    only where `create` allows it; otherwise a missing root is a DeployError
    and nothing is written. A dry run creates nothing and lists the root
    alone: it does not look up the folders above it. A folder that cannot be
    created ends it as a WriteFailed that names what was created before it."""
    found = find_folder(session, content_root)
    if found:
        return found["id"], []
    if not create:
        raise DeployError(f"there is no folder {content_root}, and nothing was created: pass --create to create "
                          "it (with every folder above it that is missing)")
    if dry_run:
        return None, [content_root]
    made: list = []

    def make(path: str, missing: bool = False):
        found = None if missing else find_folder(session, path)
        if found:
            return found["id"]
        parent_path, _, name = path.rstrip("/").rpartition("/")
        parent_id = make(parent_path) if parent_path else None
        try:
            created = create_folder(session, name, parent_id)["id"]
        except WriteFailed as exc:
            raise WriteFailed(f"{exc} - created before it: {', '.join(made) if made else 'nothing'}") from exc
        made.append(path)
        return created
    return make(content_root.rstrip("/"), missing=True), made


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


def record_in_part(manifest, only) -> bool:
    """Whether a run of `only` covers a part of the manifest and names
    release.json among its entries: the run RECORD_NEEDS_ALL refuses. Asked
    of the manifest alone - the command asks it before it signs in."""
    objects = selected(manifest, only)[1]
    return len(objects) != len(manifest.objects()) and any(is_release_record(entry) for entry in objects)


def deploy(session, manifest, content_root: Optional[str] = None, dry_run: bool = False,
           emit: Optional[Callable[[Outcome], None]] = None, only=None, source=None,
           settings: Optional[dict] = None, reports: bool = True, new_values: Optional[dict] = None,
           create_root: bool = True) -> DeployResult:
    """Bring `content_root` to the state of `source`; returns one Outcome per
    entry of the manifest, folders first and release.json last.

    `manifest` is the list of entries, `source` what each holds - the
    checkout unless another source is given. With `dry_run` nothing is
    written: every outcome says what a run would do, and every request is a
    GET. `emit` is called with each outcome as it is known - and first, where
    `content_root` itself is created, with a line for that folder, which is
    no entry and no outcome of the result. `only` restricts
    the run to the entries at or below the given paths (and the folders above
    them); everything else is not looked at, and a part that names
    release.json is a DeployError before anything is sent (RECORD_NEEDS_ALL).
    `settings` maps the name of a
    job definition parameter to the value to write there, into every job
    definition that has the parameter (the compute context that was given,
    the content root); `new_values` to the value a job definition gets only
    where this run creates it, or where it lacks the parameter - a site
    setting of a definition the site has keeps the site's value. With
    `reports` off a report entry gives no outcome; it still counts as
    listed. Without `create_root` a content root that is not there is a
    DeployError and nothing is written.

    The run stops at the first write that fails (see the module text); the
    result says where in `stopped_at`.
    """
    content_root = (content_root or manifest.content_root).rstrip("/")
    source = source or _content.CheckoutSource(manifest)
    result = DeployResult()
    folders, objects = selected(manifest, only)
    # the release record of the folder comes last: it names a release only
    # when everything in front of it was written
    objects = [entry for entry in objects if not is_release_record(entry)] \
        + [entry for entry in objects if is_release_record(entry)]
    # ... and only by a run that covers every object of the manifest: a run of
    # a part that names release.json is refused before anything is sent
    if record_in_part(manifest, only):
        raise DeployError(RECORD_NEEDS_ALL)

    def done(entry, status: str, note: str = "") -> Outcome:
        outcome = Outcome(entry, status, note)
        result.outcomes.append(outcome)
        if emit:
            emit(outcome)
        return outcome

    root_id, made = _ensure_root(session, content_root, dry_run, create_root)
    result.root_created = bool(made)
    if made and emit:
        # the folder itself is no entry of the manifest: it gets a line, and is not counted with the entries
        emit(Outcome(_content.Entry(path=content_root, kind="folder"), CREATED,
                     "the folder the content goes into, with every folder above it that is missing" if dry_run else
                     "the folder the content goes into"
                     + (f"; created above it: {', '.join(made[:-1])}" if made[:-1] else "")))
    folder_ids: dict = {"": root_id}
    members: dict = {}
    #: (folder, name, kind) of the entries whose own line names a reference of their name.
    referenced: set = set()
    #: The paths of the folders that FAILED before anything was sent: nothing goes into them.
    broken: set = set()

    def members_of(folder_path: str) -> list:
        if folder_path not in members:
            folder_id = folder_ids.get(folder_path)
            members[folder_path] = folder_members(session, folder_id) if folder_id else []
        return members[folder_path]

    for entry in folders:
        if result.stopped_at is not None:
            folder_ids.setdefault(entry.path, None)
            done(entry, NOT_ATTEMPTED)
            continue
        parent_id = folder_ids.get(entry.folder)
        try:
            if entry.folder in broken or (parent_id is None and not dry_run):
                raise DeployError("the folder above it FAILED (its line says why): nothing was created in it")
            listed = members_of(entry.folder) if parent_id else []
            found = _members_named(listed, entry.name, "folder")
            references = _references_named(listed, entry.name, "folder")
            if references:
                # one name, one object: nothing is created beside a reference of the name
                referenced.add((entry.folder, entry.name, entry.kind))
                raise DeployError(_referenced(entry, found, references, manifest.listed_path(entry.path),
                                              _root_readers(manifest, source, entry)))
            if len(found) > 1:
                raise DeployError(f"{len(found)} folders of that name are in the folder (ids "
                                  f"{', '.join(_id(m['uri']) for m in found)}): nothing was written into any of "
                                  "them, because nothing says which one is meant - remove or rename the others")
            if found:
                folder_ids[entry.path] = _id(found[0]["uri"])
                done(entry, EQUAL)
            elif dry_run:
                folder_ids[entry.path] = None
                done(entry, CREATED)
            else:
                folder_ids[entry.path] = create_folder(session, entry.name, parent_id, entry.description)["id"]
                done(entry, CREATED)
        except WriteFailed as exc:
            # a folder that cannot be created ends the run like any write:
            # nothing goes on into a folder that is half made
            folder_ids.setdefault(entry.path, None)
            result.stopped_at = done(entry, FAILED, str(exc))
        except DeployError as exc:
            folder_ids.setdefault(entry.path, None)
            broken.add(entry.path)
            done(entry, FAILED, str(exc))

    for entry in objects:
        if entry.kind == "report" and not reports:
            continue
        if entry.kind == "report" and result.stopped_at is not None:
            # a report is never deployed, whether the run stopped or not
            done(entry, NOT_DEPLOYED, "not looked up after the stop")
            continue
        if result.stopped_at is not None:
            done(entry, NOT_ATTEMPTED, "not written: the run stopped in front of it - the release.json of an "
                                       "earlier run, if there is one, is left as it is"
                 if is_release_record(entry) else "")
            continue
        if is_release_record(entry) and result.failed:
            n = len(result.failed)
            done(entry, NOT_ATTEMPTED, f"not written: {n} entr{'y' if n == 1 else 'ies'} in front of it FAILED, and "
                                       "release.json names a release only when every entry was written - the "
                                       "release.json of an earlier run, if there is one, is left as it is")
            continue
        folder_id = folder_ids.get(entry.folder)
        try:
            if entry.folder in broken or (folder_id is None and not dry_run):
                # in a dry run as in a run: nothing goes into a folder that FAILED
                if entry.kind == "report":
                    done(entry, NOT_DEPLOYED, "not looked up: its folder FAILED")
                else:
                    done(entry, FAILED, "its folder FAILED (the line of the folder says why): nothing was written "
                                        "into it")
                continue
            listed = members_of(entry.folder) if folder_id else []
            existing = _members_named(listed, entry.name, entry.kind)
            if entry.kind == "report":
                done(entry, NOT_DEPLOYED, "on the site" if existing else "not on the site - it comes with the package")
                continue
            references = _references_named(listed, entry.name, entry.kind)
            if references:
                referenced.add((entry.folder, entry.name, entry.kind))
                done(entry, FAILED, _referenced(entry, existing, references, manifest.listed_path(entry.path)))
                continue
            if len(existing) > 1:
                done(entry, FAILED, _several(entry, existing, manifest.listed_path(entry.path)))
                continue
            status, note = _DEPLOYERS[entry.kind](session, source, entry, folder_id, existing, dry_run,
                                                  settings, new_values)
            done(entry, status, note)
        except WriteFailed as exc:
            result.stopped_at = done(entry, FAILED, str(exc))
        except (DeployError, OSError, ValueError) as exc:
            done(entry, FAILED, str(exc))

    # what else sits in the folders of the manifest: named, and left alone
    listed_entries = {(entry.folder, entry.name, entry.kind) for entry in manifest.all_entries()}
    for folder_path, items in sorted(members.items()):
        for member in items:
            uri = str(member.get("uri") or "")
            kind = next((k for k, prefix in _MEMBER_URI.items() if uri.startswith(prefix)), None)
            key = (folder_path, member.get("name"), kind)
            if is_reference(member) and key in referenced:
                continue                                       # named on the line of its entry, which FAILED for it
            if is_reference(member) or key not in listed_entries:
                what = _called(member, kind)
                if is_reference(member):
                    # at a listed path the reference is what is not listed, not the path - whatever kind
                    # of object it names
                    listed_as = sorted(_WORD[k] for folder, name, k in listed_entries if (folder, name) == key[:2])
                    what = f"reference to {'an' if what[:1].lower() in 'aeiou' else 'a'} {what}" + (
                        f" - not the {' or '.join(listed_as)} that is listed under this name" if listed_as else "")
                name = str(member.get("name"))
                result.unlisted.append(f"{folder_path}/{name} ({what})" if folder_path else f"{name} ({what})")
    return result


def _to_hint(listed: str, target: str) -> str:
    """How one object of a site is updated where an entry FAILED over two
    things of its name. The command writes the content of a checkout - which
    the hint says, because `mdb builders-import` prints the line too.
    `listed` is the path of the entry in the whole manifest, which is what
    `--only` takes: a run of a part (`--only RAG --to ...`) names its entries
    below that part."""
    return f"from a checkout with `mdb content-deploy --only \"{listed}\" --to {target}`"


#: Why the line of release.json names no command to update one object: `--only release.json` is refused.
_RECORD_BY_A_WHOLE_RUN = "release.json is written only by a run that covers every entry of the manifest"


def _several(entry, existing: list, listed: str) -> str:
    ids = ", ".join(_id(m["uri"]) for m in existing)
    way_out = (f"remove the other: {_RECORD_BY_A_WHOLE_RUN}" if is_release_record(entry) else
               f"update one {_to_hint(listed, '<id>')}, or remove the other")
    return (f"{len(existing)} {_WORD[entry.kind]}s of that name are in the folder (ids {ids}): none was changed, "
            f"because nothing says which one is meant - {way_out}")


def _root_readers(manifest, source, folder) -> list:
    """The job definitions below a folder of the manifest that have the
    parameter contentRoot in what the source holds for them, by their path
    in the whole manifest. A folder with one goes somewhere else only as
    that folder of a content root (`root_of_part`)."""
    readers = []
    for entry in manifest.objects():
        if entry.kind != "jobDefinition" or not entry.path.startswith(folder.path + "/"):
            continue
        try:
            parameters = source.job_body(entry).get("parameters") or []
        except (OSError, ValueError):
            continue
        if any(isinstance(p, dict) and p.get("name") == _content.CONTENT_ROOT_PARAMETER for p in parameters):
            readers.append(manifest.listed_path(entry.path))
    return readers


def _referenced(entry, existing: list, references: list, listed: str, readers=()) -> str:
    """Why an entry is not written while a reference of its name and kind is
    in its folder: alone, or beside the object - or objects - of that name.
    Every object and every reference of the name is named. `listed` is the
    path of the entry in the whole manifest, `readers` what `_root_readers`
    found below a folder entry."""
    names = ", ".join(str(m.get("uri")) for m in references)
    one = len(references) == 1
    if not existing:
        update = f", or update {'the object it names' if one else 'an object they name'} "
        if is_release_record(entry):
            # no command updates one release.json: a run of a part that names it is refused
            hint = f": {_RECORD_BY_A_WHOLE_RUN}"
        elif entry.kind == "folder":
            # the folder form of --to takes the SAS Content path of a folder, whatever its name - unless a job
            # definition below it reads the content root it sits in: then the command takes that folder of a
            # content root only, and says so before it signs in
            hint = update + _to_hint(listed, "<the SAS Content path of that folder>")
            if readers:
                hint += (f" - a path that ends with /{listed}, with the content root in front of it, because the "
                         f"job definition {readers[0]} reads the content root it sits in")
        else:
            # an object named by its URI is written only under the name of the entry, or with --force
            hint = update + _to_hint(listed, "<its URI>") + " (with --force where that object has another name)"
        return (f"a reference of that name is in the folder and names {names}, an object that lives elsewhere: it "
                f"is not followed, and nothing was written - remove the reference{hint}" if one else
                f"{len(references)} references of that name are in the folder and name {names}, objects that live "
                f"elsewhere: none is followed, and nothing was written - remove them{hint}")
    ids = ", ".join(_id(m["uri"]) for m in existing)
    held = (f"the {_WORD[entry.kind]} of that name (id {ids})" if len(existing) == 1 else
            f"{len(existing)} {_WORD[entry.kind]}s of that name (ids {ids})")
    return (f"{held} and a reference of that name, which names {names}, an object that lives elsewhere, are in "
            "the folder: none was changed and the reference was not followed, because nothing says which one is "
            "meant - remove the reference" if one else
            f"{held} and {len(references)} references of that name, which name {names}, objects that live "
            "elsewhere, are in the folder: none was changed and no reference was followed, because nothing says "
            "which one is meant - remove the references")


def _deploy_file(session, source, entry, folder_id, existing, dry_run, settings, new_values=None) -> tuple:
    wanted = source.file_bytes(entry)
    if not existing:
        if dry_run:
            return CREATED, ""
        created = create_file(session, folder_id, entry.name, wanted)
        return CREATED, f"id {created}" if created else ""
    uri = existing[0]["uri"]
    held, etag, content_type = read_file(session, uri)
    if held == wanted:
        return EQUAL, f"id {_id(uri)}"
    if not dry_run:
        # the version that was compared is the version that is replaced
        update_file(session, uri, entry.name, wanted, etag, content_type)
    return UPDATED, f"content replaced in place; id {_id(uri)} kept"


def _deploy_step(session, source, entry, folder_id, existing, dry_run, settings, new_values=None) -> tuple:
    wanted = source.step_body(entry)
    if not existing:
        if dry_run:
            return CREATED, ""
        created = create_step(session, folder_id, source.step_document(entry), entry.name)
        return CREATED, f"id {created}" if created else ""
    uri = existing[0]["uri"]
    live = get_step(session, uri)
    parts = _content.step_differences(wanted, live)
    if not parts:
        return EQUAL, f"id {_id(uri)}"
    if not dry_run:
        # the step is the site's object: it keeps the name it has there
        update_step(session, uri, {**wanted, "name": live.get("name") or wanted.get("name")})
    return UPDATED, f"{', '.join(parts)}; id {_id(uri)} kept"


def _deploy_job_definition(session, source, entry, folder_id, existing, dry_run, settings, new_values=None) -> tuple:
    wanted = source.job_body(entry)
    site_names = source.site_settings(entry)
    if not existing:
        if dry_run:
            return CREATED, ""
        created = create_job_definition(session, folder_id,
                                        _content.settle_parameters(wanted, None, (), settings, new_values))
        return CREATED, f"id {created}" if created else ""
    uri = existing[0]["uri"]
    live, etag = get_job_definition(session, uri)
    wanted = _content.settle_parameters(wanted, live, site_names, settings, new_values)
    parts = _content.job_differences(wanted, live)
    if not parts:
        return EQUAL, f"id {_id(uri)}"
    if not dry_run:
        update_job_definition(session, uri, live, wanted, etag)
    return UPDATED, f"{', '.join(parts)}; id {_id(uri)} kept"


_DEPLOYERS = {"file": _deploy_file, "step": _deploy_step, "jobDefinition": _deploy_job_definition}


# ---------------------------------------------------------------------------
# one entry, into an object the site has somewhere else
# ---------------------------------------------------------------------------
def _object_name(session, kind: str, uri: str) -> str:
    """The name an object has on the site, read from the object itself."""
    accept = {"file": "application/vnd.sas.file+json, application/json", "step": STEP_MEDIA,
              "jobDefinition": DEFINITION_MEDIA}[kind]
    response = _read(f"Reading {uri} failed", lambda: session.get(uri, headers={"Accept": accept}))
    if not response.ok:
        raise _failure(f"Reading {uri} failed", response)
    try:
        return str(response.json().get("name") or "")
    except (ValueError, AttributeError, TypeError):
        return ""


def _folder_of(session, uri: str) -> str:
    """The SAS Content path of the folder an object sits in, as the Folders
    service names its ancestors (`GET /folders/ancestors?childUri=`, nearest
    first); "" when the service does not say. Only read."""
    try:
        response = session.get("/folders/ancestors", params={"childUri": uri},
                               headers={"Accept": "application/vnd.sas.content.folder.ancestor+json, "
                                                  "application/json"})
        if not response.ok:
            return ""
        names = [str(item.get("name") or "") for item in (response.json().get("ancestors") or [])
                 if isinstance(item, dict)]
    except Exception:                                          # a timeout, an answer of another shape
        return ""
    return "/" + "/".join(reversed(names)) if names and all(names) else ""


def resolve_target(session, kind: str, target: str) -> dict:
    """The object of `kind` that a `--to` names: {"uri", "name", "folder"} -
    its URI, the name it has on the site and the path of the folder it sits
    in ("" where the Folders service does not say).

    `target` is the object's URI (`/dataFlows/steps/<id>`), its id, or its
    SAS Content path (`/Custom Steps/LLM - Log Parser.step`). A path has to
    name exactly one object of that kind in its folder - a reference is not
    one, and a reference of that name beside the object makes the path name
    two things: the object is then named by its id - and nothing is ever
    created from it. An object named by its id or URI is read for its name
    and asked for its folder.
    """
    target = (target or "").strip()
    prefix = _MEMBER_URI[kind]
    uri = ""
    if target.startswith(prefix) and _ID.fullmatch(target[len(prefix):]):
        uri = target
    elif _ID.fullmatch(target):
        uri = prefix + target
    if uri:
        return {"uri": uri, "name": _object_name(session, kind, uri), "folder": _folder_of(session, uri)}
    if not target.startswith("/") or target.startswith(tuple(_MEMBER_URI.values())):
        raise DeployError(f"--to {target}: not a SAS Content path (starting with a slash), an id or the URI of a "
                          f"{_WORD[kind]} ({prefix}<id>)")
    folder_path, _, name = target.rstrip("/").rpartition("/")
    folder = find_folder(session, folder_path) if folder_path else None
    if not folder:
        raise DeployError(f"--to {target}: there is no folder {folder_path or '/'}")
    members = folder_members(session, folder["id"])
    found = _members_named(members, name, kind)
    if not found:
        raise DeployError(f"--to {target}: the folder {folder_path} holds no {_WORD[kind]} named '{name}'. "
                          "Nothing was written - this option updates an object and never creates one.")
    if len(found) > 1:
        raise DeployError(f"--to {target}: the folder holds {len(found)} {_WORD[kind]}s of that name (ids "
                          f"{', '.join(_id(m['uri']) for m in found)}) - name one of them by its id")
    references = _references_named(members, name, kind)
    if references:
        pointer = ("a reference of that name, which names" if len(references) == 1 else
                   f"{len(references)} references of that name, which name")
        raise DeployError(f"--to {target}: the folder holds the {_WORD[kind]} of that name (id "
                          f"{_id(found[0]['uri'])}) and {pointer} "
                          f"{', '.join(str(m.get('uri')) for m in references)} - name the {_WORD[kind]} by its id")
    return {"uri": found[0]["uri"], "name": name, "folder": folder_path}


def deploy_to(session, manifest, entry, target: str, dry_run: bool = False, source=None,
              settings: Optional[dict] = None, force: bool = False) -> Outcome:
    """Update ONE object a site has somewhere else, in place, from one entry
    of the manifest: the step a site registered in a folder of its own, which
    its flows reference by id. Nothing is created, no folder is looked at but
    the one a path names, and the object keeps its id and its name.

    The outcome names what was written: the name of the object on the site
    and the folder it sits in. An object whose name is not the name of the
    entry is not written unless `force` says it is meant - an id typed from
    another line updates another step."""
    source = source or _content.CheckoutSource(manifest)
    try:
        if entry.kind not in _DEPLOYERS:
            raise DeployError(f"a {entry.kind} is not updated this way")
        if is_release_record(entry):
            raise DeployError(RECORD_NEEDS_ALL)
        found = resolve_target(session, entry.kind, target)
        where = f"the {_WORD[entry.kind]} '{found['name'] or '?'}' " + (
            f"in {found['folder']}" if found["folder"] else "(the Folders service did not say in which folder)")
        if found["name"] != entry.name and not force:
            raise DeployError(f"--to {target} names {where}, not '{entry.name}': nothing was written - pass --force "
                              "to update it all the same")
        status, note = _DEPLOYERS[entry.kind](session, source, entry, None, [found], dry_run, settings)
    except (DeployError, OSError, ValueError) as exc:
        return Outcome(entry, FAILED, str(exc))
    return Outcome(entry, status, f"{note} - {where}")
