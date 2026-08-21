# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Document sources for the List/Extract steps (design §3).

A source answers two questions: which documents exist right now
(`entries`), and what are the bytes of one of them (`read`). The List step
diffs the entries against the ledger; the Extract step reads the bytes of
whatever came out as new or changed. Everything else in the pipeline works
off the ledger row, so adding a source never touches the other steps.

Two are shipped:

* `FileSystemSource`  — a folder visible from the compute context. The
  fingerprint is a streaming SHA-256 of the bytes.
* `ContentSource`     — a SAS Content folder, walked through the files
  service. The fingerprint is the file resource's ETag (falling back to
  size + modified timestamp): SAS Content mints a new ETag on every content
  change, so hashing the bytes would mean downloading the whole corpus
  twice per run for the same answer.

`source_uri` is the stable identity in BOTH cases — an absolute filesystem
path or an absolute SAS Content path. A moved or renamed document therefore
reads as delete + new, exactly as it does on disk.
"""
from __future__ import annotations

import hashlib
import os

DEFAULT_TIMEOUT = 60.0


def _suffix_of(name: str) -> str:
    return ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""


class FileSystemSource:
    """Documents in a folder visible from the compute context."""

    kind = "path"

    def __init__(self, root: str):
        self.root = str(root or "").rstrip("/") or "/"

    def describe(self) -> str:
        return f"compute server folder {self.root}"

    def entries(self, include_suffixes=None) -> list:
        if not os.path.isdir(self.root):
            raise ValueError(f"source path is not a directory visible from this "
                             f"compute context: {self.root!r}")
        found = []
        for folder, _dirs, files in os.walk(self.root):
            for filename in sorted(files):
                if include_suffixes and _suffix_of(filename) not in include_suffixes:
                    continue
                full = os.path.join(folder, filename)
                found.append({"source_uri": full, "source_kind": self.kind,
                              "mtime": str(os.path.getmtime(full))})
        return found

    def fingerprint(self, entry: dict, chunk_bytes: int = 1 << 20) -> str:
        digest = hashlib.sha256()
        with open(entry["source_uri"], "rb") as fh:
            for block in iter(lambda: fh.read(chunk_bytes), b""):
                digest.update(block)
        return digest.hexdigest()

    def read(self, source_uri: str) -> bytes:
        with open(source_uri, "rb") as fh:
            return fh.read()


class ContentSource:
    """Documents in a SAS Content folder, read through the files service.

    Needs a bearer token for the services base URL — inside a compute
    session that is `SAS_SERVICES_TOKEN`, which is also what the steps use
    for their CAS connection.
    """

    kind = "content"

    def __init__(self, base_url: str, token: str, root: str, verify=True,
                 session=None, timeout: float = DEFAULT_TIMEOUT):
        import requests  # imported here so filesystem-only sites need no requests

        self.base = str(base_url or "").rstrip("/")
        self.root = "/" + str(root or "").strip("/")
        self.verify = verify
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": "Bearer " + str(token or ""),
                                     "Accept": "application/json"})
        self._ids: dict = {}       # source_uri -> file id, filled by entries()

    def describe(self) -> str:
        return f"SAS Content folder {self.root}"

    # -- REST helpers -------------------------------------------------------
    def _get(self, endpoint: str, **params):
        # first argument is `endpoint`, never `path` - a query parameter of
        # this API is itself called `path` and would collide
        response = self.session.get(self.base + endpoint, params=params or None,
                                    timeout=self.timeout, verify=self.verify)
        if response.status_code >= 400:
            raise RuntimeError(f"SAS Content request failed ({response.status_code}) "
                               f"for {endpoint}: {response.text[:200]}")
        return response.json()

    def _folder_id(self, path: str) -> str:
        found = self._get("/folders/folders/@item", path=path)
        folder_id = found.get("id")
        if not folder_id:
            raise ValueError(f"SAS Content folder not found: {path!r}")
        return folder_id

    #: Members come one page at a time. The loop below is what makes this a
    #: request size rather than a corpus size limit.
    _PAGE = 500
    #: Refuse rather than spin: a service that ignored `start` would otherwise
    #: return the same page forever. 500 pages is 250,000 members.
    _MAX_PAGES = 500

    def _members(self, folder_id: str) -> list:
        """EVERY folder member - the ONLY listing that respects the folder.

        `GET /files/files?parentFolderUri=...` silently ignores the parameter
        and returns every file on the server (verified live), which would
        turn a three-document folder into a thousand-document corpus.

        Paged to exhaustion, because here a partial listing is not a partial
        result. `run_list` decides a document was DELETED by not seeing it, so
        stopping at the first page would make run_load retire - or with
        deleted_policy='purge' delete - every chunk of every document on the
        pages nobody asked for, while the documents sit untouched at the
        source. A corpus one document larger than a page would start losing
        the corpus.
        """
        items: list = []
        start = 0
        for _page in range(self._MAX_PAGES):
            payload = self._get(f"/folders/folders/{folder_id}/members",
                                start=start, limit=self._PAGE)
            batch = payload.get("items") or []
            # The collection echoes the offset it answered from. A service
            # that ignored `start` would otherwise hand back page one every
            # time, and the count check below would end the loop having
            # collected the same documents several times and none of the rest.
            echoed = payload.get("start")
            if isinstance(echoed, int) and echoed != start:
                raise RuntimeError(
                    f"listing folder {folder_id} asked for start={start} and "
                    f"was answered from {echoed} - refusing a listing that "
                    "repeats itself, because the documents it never reached "
                    "would be recorded as deleted")
            items.extend(batch)
            if len(batch) < self._PAGE:
                return items
            count = payload.get("count")
            if isinstance(count, int) and len(items) >= count:
                return items
            start += len(batch)
        raise RuntimeError(
            f"listing folder {folder_id} did not finish after "
            f"{self._MAX_PAGES} pages of {self._PAGE} - refusing to treat a "
            "partial listing as the whole folder, because the documents it "
            "never reached would be recorded as deleted")

    def _subfolders(self, folder_id: str, members=None) -> list:
        # `members` lets a caller that already has the listing reuse it -
        # paging a large folder twice to ask two questions about it is pure
        # waste, and the crawl asks both for every folder it visits
        members = self._members(folder_id) if members is None else members
        return [item for item in members
                if str(item.get("uri", "")).startswith("/folders/folders/")]

    def _files(self, folder_id: str, members=None) -> list:
        """File members with the metadata the fingerprint needs."""
        members = self._members(folder_id) if members is None else members
        files = []
        for item in members:
            uri = str(item.get("uri", ""))
            if "/files/files/" not in uri:
                continue
            meta = self._get("/files/files/" + uri.rsplit("/", 1)[-1])
            files.append({"id": meta.get("id"),
                          "name": meta.get("name") or item.get("name"),
                          "size": meta.get("size"),
                          "version": meta.get("fileVersion"),
                          "modifiedTimeStamp": meta.get("modifiedTimeStamp")})
        return files

    # -- source contract ----------------------------------------------------
    def entries(self, include_suffixes=None) -> list:
        found: list = []
        pending = [(self.root, self._folder_id(self.root))]
        while pending:
            path, folder_id = pending.pop(0)
            members = self._members(folder_id)
            for item in self._files(folder_id, members):
                name = item.get("name") or ""
                if include_suffixes and _suffix_of(name) not in include_suffixes:
                    continue
                source_uri = f"{path.rstrip('/')}/{name}"
                self._ids[source_uri] = item.get("id")
                found.append({
                    "source_uri": source_uri, "source_kind": self.kind,
                    "mtime": str(item.get("modifiedTimeStamp") or ""),
                    "_version": str(item.get("version") or ""),
                    "_size": str(item.get("size") or ""),
                })
            for member in self._subfolders(folder_id, members):
                child = member.get("name") or ""
                pending.append((f"{path.rstrip('/')}/{child}",
                                str(member["uri"]).rsplit("/", 1)[-1]))
        found.sort(key=lambda row: row["source_uri"])
        return found

    def fingerprint(self, entry: dict) -> str:
        raw = "|".join(str(entry.get(key) or "")
                       for key in ("_version", "_size", "mtime"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def file_id(self, source_uri: str) -> str:
        if source_uri not in self._ids:
            folder, _, _name = source_uri.rpartition("/")
            for item in self._files(self._folder_id(folder or "/")):
                self._ids[f"{folder}/{item.get('name')}"] = item.get("id")
            if source_uri not in self._ids:
                raise ValueError(f"no SAS Content file at {source_uri!r}")
        return self._ids[source_uri]

    def read(self, source_uri: str) -> bytes:
        response = self.session.get(
            f"{self.base}/files/files/{self.file_id(source_uri)}/content",
            timeout=self.timeout, verify=self.verify)
        if response.status_code >= 400:
            raise RuntimeError(f"reading {source_uri} from SAS Content failed "
                               f"({response.status_code})")
        return response.content


def reader_for(source_kind: str, base_url: str = "", token: str = "",
               verify=True):
    """A source that can only READ, for steps downstream of List Documents.

    They never crawl - they resolve the absolute `source_uri` the ledger
    already holds - so no document-folder parameter is needed on them.
    Returns None when the kind needs nothing beyond plain file access.
    """
    if str(source_kind or "").lower() == ContentSource.kind:
        return ContentSource(base_url, token, "/", verify)
    return None


def make_source(location: str, base_url: str = "", token: str = "",
                verify=True) -> object:
    """Build the source a step's path selector points at.

    SAS Studio path selectors hand over scheme-prefixed values —
    `sasserver:/path` for the compute file system, `sascontent:/path` for
    SAS Content. A bare path is treated as a filesystem path (that is what
    the ingestion job passes).
    """
    value = str(location or "").strip()
    lowered = value.lower()
    if lowered.startswith("sascontent:"):
        return ContentSource(base_url, token, value[len("sascontent:"):], verify)
    if lowered.startswith("sasserver:"):
        value = value[len("sasserver:"):]
    return FileSystemSource(value)
