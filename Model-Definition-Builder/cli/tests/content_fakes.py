# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What the content tests share: a small checkout with a content manifest, a
transfer package built the way the transfer service builds one, and a SAS Viya
that lives in memory.

None of this is a test. The shapes are the ones read from the packages the
repository shipped up to 2.2.x and from the requests of deploy-rag-content:

* an object of a package says where it sits through a `parentFolder` connector
  beside its transfer object; a file that belongs to a report through a
  `parent` connector inside it, or not at all - then only the report's own
  `image` connector names it;
* the requested items and the connectors name an object by the URI of its
  summary, not by the id of the transfer object;
* the dataFlows service stores a step's ports its own way (no `id`, no
  `label`, a `localDisplayName`, `defaultEntries`), drops an empty
  `inputPorts` and the empty `properties`.
"""
import base64
import json
from pathlib import Path

from mdb.core import content
from mdb.core.options import MERGED_REPORT_LABELS
from mdb.core.packages import NEUTRAL_DEFAULTS, deflate

CONTENT_ROOT = "/SAS Agentic AI Accelerator"
PACKAGE = "SAS-Viya-Integrations/SAS-Agentic-AI-Accelerator.json"

STEP_NAME = "RAG - Embed Chunks.step"
STEP = {
    "name": STEP_NAME, "displayName": STEP_NAME, "description": "Embeds chunks.", "type": "code",
    "metadataVersion": 1, "properties": {},
    "flowMetadata": {
        "inputPorts": [{"name": "_in", "displayName": "_in", "minEntries": 1, "maxEntries": 1, "type": "table",
                        "id": "_in", "label": "_in"}],
        "outputPorts": [{"name": "_out", "displayName": "_out", "minEntries": 1, "maxEntries": 1,
                         "columnDelta": None, "type": "table"}]},
    "ui": "{\n\t\"pages\": []\n}",
    "templates": {"SAS": "%put embed;\n"},
}

CHANGELOG = """# Changelog

## [2.3.0] - Unreleased

### Added

- **Prompt Builder: one package.** The content folder ships as one transfer package.

## [2.2.0] - 2026-10-07

The release before.

### Fixed

- **RAG runtime: the ledger is loaded back.** A saved ledger is loaded by the next run.
"""

MANIFEST = f"""version: 1
contentRoot: {CONTENT_ROOT}
package: {PACKAGE}
entries:
  - {{path: release.json, kind: file, generated: release-info}}
  - {{path: Data/ACCELERATOR_RELEASES.csv, kind: file, generated: release-table}}
  - {{path: RAG/rag_core/__init__.py, kind: file, source: src/rag_core/__init__.py}}
  - {{path: RAG/rag_core/adapters/__init__.py, kind: file, source: src/rag_core/adapters/__init__.py}}
  - {{path: "RAG/steps/{STEP_NAME}", kind: step, source: "src/{STEP_NAME}"}}
  - path: Prompt Builder/UI
    kind: jobDefinition
    form: src/index.html
    description: The page.
    parameters:
      - {{name: _contextName, default: SAS Studio compute context, label: Context Name}}
      - {{name: _action, default: "prompts,form,execute"}}
  - path: Prompt Builder/Optimize
    kind: jobDefinition
    code: src/optimize.sas
    parameters:
      - {{name: _action, default: execute}}
  - path: Prompt Builder/Prompt Builder
    kind: report
    checks:
      optionSet: prompt-builder
      tables: [ACCELERATOR_RELEASES]
"""

SOURCES = {
    "src/rag_core/__init__.py": "CORE = 1\r\n",                       # a Windows checkout: CRLF
    "src/rag_core/adapters/__init__.py": "ADAPTERS = 1\n",
    f"src/{STEP_NAME}": json.dumps(STEP),
    "src/index.html": "<!DOCTYPE html>\n<html>ü</html>\n",
    "src/optimize.sas": "%put optimize;\r\n%put done;\r\n",
}


def make_repo(root: Path, manifest: str = MANIFEST, sources=None) -> Path:
    """A checkout with a changelog, a content manifest and its sources."""
    (root / "CHANGELOG.md").write_bytes(CHANGELOG.encode("utf-8"))
    for relative, text in {**SOURCES, **(sources or {})}.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    target = root / "SAS-Viya-Integrations" / "package-manifest.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(manifest.encode("utf-8"))
    return root


ALL_NEUTRAL = {label: NEUTRAL_DEFAULTS.get(label, "x") for label in MERGED_REPORT_LABELS}


def report_xml(options: dict) -> str:
    return "<SASReport>" + "".join(
        f'<PromptDefinition label="{label}" hidden="true" isParameter="true" dataType="string" name="pr{i}">'
        f'<DefaultValue><String behavior="fixed">{value}</String></DefaultValue></PromptDefinition>'
        for i, (label, value) in enumerate(options.items())) + "</SASReport>"


def b64(value) -> str:
    data = value if isinstance(value, bytes) else (value if isinstance(value, str) else json.dumps(value)).encode("utf-8")
    return base64.b64encode(data).decode("ascii")


# ---------------------------------------------------------------------------
# a transfer package
# ---------------------------------------------------------------------------
class Package:
    """Builds a transfer package object by object. `build()` gives the parsed
    package; every method returns the URI of what it added."""

    def __init__(self, root: str = "SAS Agentic AI Accelerator", above: tuple = ()):
        self._n = 0
        self.details: list = []
        self.uris: dict = {}            # path below the root -> uri
        parent = None
        for name in above:              # folders above the content root, as an export of a nested root has them
            parent = self._folder_object(name, parent)
        self.root_uri = self._folder_object(root, parent)
        self.uris[""] = self.root_uri

    def _id(self) -> str:
        self._n += 1
        return f"00000000-0000-4000-8000-{self._n:012d}"

    def _add(self, kind: str, name: str, uri_prefix: str, content: str, parent=None, inner=None, **extra) -> str:
        resource = self._id()
        uri = f"{uri_prefix}{resource}"
        detail = {"transferObject": {
            "id": self._id(), "version": 2, "state": "enabled", "substitutions": {},
            "summary": {"id": resource, "type": kind, "name": name,
                        "links": [{"method": "GET", "rel": "self", "href": uri, "uri": uri}]},
            "content": content, "connectors": list(inner or []), **extra}}
        if parent is not None:
            detail["connectors"] = [{"id": self._id(), "uri": parent, "type": "parentFolder",
                                     "contentType": "application/vnd.sas.content.folder+json",
                                     "relatedUris": [], "forceMapping": False}]
        self.details.append(detail)
        return uri

    def _folder_object(self, name: str, parent) -> str:
        return self._add("folder", name, "/folders/folders/", b64({"folder": {"name": name}}), parent)

    def folder(self, path: str) -> str:
        if path not in self.uris:
            above, _, name = path.rpartition("/")
            self.uris[path] = self._folder_object(name, self.folder(above))
        return self.uris[path]

    def _in(self, path: str) -> tuple:
        above, _, name = path.rpartition("/")
        return self.folder(above), name

    def file(self, path: str, data) -> str:
        parent, name = self._in(path)
        data = data if isinstance(data, bytes) else data.encode("utf-8")
        return self._add("application/vnd.sas.file+json", name, "/files/files/",
                         b64({"fileResource": {"name": name, "size": len(data)}, "content": b64(data)}), parent)

    def step(self, path: str, body: dict) -> str:
        parent, name = self._in(path)
        return self._add("flowStep", name, "/dataFlows/steps/", b64({**body, "name": name}), parent)

    def job(self, path: str, body: dict) -> str:
        parent, name = self._in(path)
        return self._add("jobDefinition", name, "/jobDefinitions/definitions/", b64({**body, "name": name}), parent)

    def report(self, path: str, options: dict, states=(), tables=("ACCELERATOR_RELEASES",), host="your-sas-viya-host",
               images=()) -> str:
        parent, name = self._in(path)
        wrapper = {"object": {"name": name}, "transferableContent": {
            "content": report_xml(options), "contentVersion": 1,
            "reportStates": [{"content": report_xml(state), "contentVersion": 1,
                              "reportState": {"userId": f"user{i}"}} for i, state in enumerate(states)] or None}}
        inner = [{"id": self._id(), "type": "table", "relatedUris": [], "forceMapping": False,
                  "uri": f"server=cas-shared-default;library=Public;table={table}"} for table in tables]
        inner += [{"id": self._id(), "type": "image", "uri": image, "relatedUris": [], "forceMapping": False}
                  for image in images]
        uri = self._add("report", name, "/reports/reports/", deflate(json.dumps(wrapper, separators=(",", ":"))),
                        parent, inner, substitutions={"VisualElement:ve9": f"https://{host}/SASJobExecution/"})
        for connector in inner:
            connector["relatedUris"] = [uri]
        return uri

    def image(self, name: str, report_uri=None) -> str:
        """A file that sits in no folder: an image of a report. With
        `report_uri` it says so itself; without, only a report's `image`
        connector does."""
        inner = [{"id": self._id(), "type": "parent", "uri": report_uri, "relatedUris": []}] if report_uri else []
        return self._add("application/vnd.sas.file+json", name, "/files/files/",
                         b64({"fileResource": {"name": name}, "content": b64(b"\x89PNG\xff")}), None, inner)

    def grant(self, kind: str, name: str, target: str) -> str:
        """An authorization rule or share on `target`."""
        return self._add(f"application/vnd.sas.authorization.{kind}+json", name, f"/authorization/{kind}s/",
                         b64({"principal": "someone", "objectUri": target}), None,
                         [{"id": self._id(), "type": "objectUri", "uri": target, "relatedUris": []}])

    def build(self, requested=None) -> dict:
        uris = [d["transferObject"]["summary"]["links"][0]["uri"] for d in self.details]
        for detail in self.details:
            for connector in detail.get("connectors") or []:
                connector["relatedUris"] = [
                    other["transferObject"]["summary"]["links"][0]["uri"] for other in self.details
                    if any(c["uri"] == connector["uri"] for c in other.get("connectors") or [])]
        return {"id": "package-1", "name": "SAS Agentic AI Accelerator", "version": 1,
                "options": {"includeRules": "true", "includeDependencies": "true"},
                "requestedItems": list(uris if requested is None else requested),
                "transferObjectCount": len(self.details), "transferDetails": list(self.details)}


def package_of(manifest, change=None, **kwargs) -> Package:
    """A package that holds exactly what the manifest lists, as the checkout
    has it. `change` maps a path to what the package holds there instead:
    None to leave the object out, bytes for a file, a dict for the body of a
    step or of a job definition, a dict of options for a report."""
    change = change or {}
    package = Package(**kwargs)
    for entry in manifest.all_entries():
        if entry.path in change and change[entry.path] is None:
            continue
        other = change.get(entry.path)
        if entry.kind == "folder":
            package.folder(entry.path)
        elif entry.kind == "file":
            package.file(entry.path, content.file_bytes(manifest, entry) if other is None else other)
        elif entry.kind == "step":
            package.step(entry.path, as_registered(content.step_body(manifest, entry)) if other is None else other)
        elif entry.kind == "jobDefinition":
            package.job(entry.path, content.job_definition_body(manifest, entry) if other is None else other)
        else:
            package.report(entry.path, ALL_NEUTRAL if other is None else other,
                           tables=entry.checks.get("tables", ()))
    return package


def as_registered(step: dict) -> dict:
    """A step the way the dataFlows service gives it back."""
    def port(p):
        kept = {k: v for k, v in p.items() if k not in ("id", "label") and v is not None}
        return {**kept, "localDisplayName": kept.get("displayName"), "defaultEntries": 0}
    body = {k: v for k, v in step.items() if k != "properties"}
    meta = {side: [port(p) for p in (step.get("flowMetadata") or {}).get(side) or []]
            for side in ("inputPorts", "outputPorts")}
    body["flowMetadata"] = {side: ports for side, ports in meta.items() if ports}
    body["localUi"] = "{}"
    return body


# ---------------------------------------------------------------------------
# SAS Viya, in memory
# ---------------------------------------------------------------------------
class Response:
    def __init__(self, status=200, body=None, content=b"", headers=None):
        self.status_code = status
        self.ok = status < 400
        self._body = body
        self.content = content
        self.text = json.dumps(body) if body is not None else content.decode("utf-8", "replace")
        self.headers = headers or {}

    def json(self):
        return self._body


class Viya:
    """Folders, files, custom steps and job definitions, with the calls made.

    `conflicts` is how often an upload of a name that was just deleted is
    answered 409 before it is taken - what the Files service did on the test
    system. `refuse` maps (method, path prefix) to a status to answer with.
    """

    def __init__(self, conflicts: int = 0):
        self._n = 0
        self.folders: dict = {}          # id -> {"name", "parent", "description"}
        self.members: dict = {}          # folder id -> [{"name", "uri"}]
        self.files: dict = {}            # uri -> bytes
        self.steps: dict = {}            # uri -> body
        self.jobs: dict = {}             # uri -> (body, etag)
        self.calls: list = []
        self.conflicts = conflicts
        self.refuse: dict = {}
        self._just_deleted: dict = {}    # (folder id, name) -> conflicts left

    # -- what a test reads -------------------------------------------------
    def _new(self) -> str:
        self._n += 1
        return f"11111111-0000-4000-8000-{self._n:012d}"

    def folder_id(self, path: str):
        current = None
        for name in [s for s in path.split("/") if s]:
            current = next((fid for fid, f in self.folders.items()
                            if f["name"] == name and f["parent"] == current), None)
            if current is None:
                return None
        return current

    def at(self, path: str) -> list:
        """The uris of every object called `path`'s last part in its folder."""
        above, _, name = path.rpartition("/")
        return [m["uri"] for m in self.members.get(self.folder_id(above), []) if m["name"] == name]

    def tree(self, root: str) -> list:
        """Every path below `root`, sorted."""
        found = []

        def walk(folder_id, prefix):
            for member in self.members.get(folder_id, []):
                path = f"{prefix}{member['name']}"
                found.append(path)
                if member["uri"].startswith("/folders/folders/"):
                    walk(member["uri"].rsplit("/", 1)[-1], path + "/")
        walk(self.folder_id(root), "")
        return sorted(found)

    def wrote(self) -> list:
        return [(method, path) for method, path, _ in self.calls if method != "GET"]

    # -- what a test prepares ----------------------------------------------
    def make_folder(self, path: str) -> str:
        current = None
        for name in [s for s in path.split("/") if s]:
            found = next((fid for fid, f in self.folders.items()
                          if f["name"] == name and f["parent"] == current), None)
            current = found or self._create_folder(name, current)
        return current

    def _create_folder(self, name, parent, description="") -> str:
        folder_id = self._new()
        self.folders[folder_id] = {"name": name, "parent": parent, "description": description}
        self.members[folder_id] = []
        if parent:
            self.members[parent].append({"name": name, "uri": f"/folders/folders/{folder_id}"})
        return folder_id

    def put_member(self, path: str, uri_prefix: str, value) -> str:
        above, _, name = path.rpartition("/")
        folder_id = self.make_folder(above)
        uri = f"{uri_prefix}{self._new()}"
        self.members[folder_id].append({"name": name, "uri": uri})
        store = {"/files/files/": self.files, "/dataFlows/steps/": self.steps}.get(uri_prefix)
        if store is not None:
            store[uri] = value
        elif uri_prefix == "/jobDefinitions/definitions/":
            self.jobs[uri] = ({**value, "id": uri.rsplit("/", 1)[-1]}, '"1"')
        return uri

    # -- the session ---------------------------------------------------------
    def _refused(self, method, path):
        for (m, prefix), status in self.refuse.items():
            if m == method and path.startswith(prefix):
                return Response(status, {"message": "refused"})
        return None

    def get(self, path, params=None, headers=None, **kw):
        self.calls.append(("GET", path, params))
        if self._refused("GET", path):
            return self._refused("GET", path)
        params = params or {}
        if path == "/folders/folders/@item":
            folder_id = self.folder_id(params["path"])
            return Response(200, {"id": folder_id, "name": self.folders[folder_id]["name"]}) if folder_id \
                else Response(404, {"message": "no such folder"})
        if path.startswith("/folders/folders/") and path.endswith("/members"):
            items = self.members.get(path.split("/")[3], [])
            start, limit = int(params.get("start", 0)), int(params.get("limit", 20))
            return Response(200, {"items": items[start:start + limit], "count": len(items)})
        if path.endswith("/content") and path[:-len("/content")] in self.files:
            return Response(200, content=self.files[path[:-len("/content")]])
        if path in self.steps:
            return Response(200, {**as_registered(self.steps[path]), "id": path.rsplit("/", 1)[-1]})
        if path in self.jobs:
            body, etag = self.jobs[path]
            return Response(200, body, headers={"ETag": etag})
        return Response(404, {"message": "not found"})

    def post(self, path, params=None, data=None, headers=None, **kw):
        self.calls.append(("POST", path, params))
        if self._refused("POST", path):
            return self._refused("POST", path)
        params = params or {}
        parent = str(params.get("parentFolderUri") or "").rsplit("/", 1)[-1] or None
        if path == "/folders/folders":
            body = json.loads(data)
            folder_id = self._create_folder(body["name"], parent, body.get("description", ""))
            return Response(201, {"id": folder_id, "name": body["name"]})
        if path == "/files/files":
            name = params["filename"]
            assert f'filename="{name}"' in headers["Content-Disposition"]
            left = self._just_deleted.get((parent, name), 0)
            if left:
                self._just_deleted[(parent, name)] = left - 1
                return Response(409, {"message": "conflict"})
            uri = f"/files/files/{self._new()}"
            self.files[uri] = bytes(data)
            self.members[parent].append({"name": name, "uri": uri})
            return Response(201, {"id": uri.rsplit("/", 1)[-1]})
        if path == "/dataFlows/steps":
            body = json.loads(data)
            uri = f"/dataFlows/steps/{self._new()}"
            self.steps[uri] = body
            self.members[parent].append({"name": body["name"], "uri": uri})
            return Response(201, {"id": uri.rsplit("/", 1)[-1]})
        if path == "/jobDefinitions/definitions":
            body = json.loads(data)
            uri = f"/jobDefinitions/definitions/{self._new()}"
            self.jobs[uri] = ({**body, "id": uri.rsplit("/", 1)[-1]}, '"1"')
            self.members[parent].append({"name": body["name"], "uri": uri})
            return Response(201, self.jobs[uri][0])
        return Response(400, {"message": "bad request"})

    def put(self, path, data=None, headers=None, **kw):
        self.calls.append(("PUT", path, None))
        if self._refused("PUT", path):
            return self._refused("PUT", path)
        body = json.loads(data)
        if path in self.steps:
            assert headers["If-Match"] == "*" and body["id"] == path.rsplit("/", 1)[-1]
            self.steps[path] = {k: v for k, v in body.items() if k != "id"}
            return Response(200, {**body})
        if path in self.jobs:
            _, etag = self.jobs[path]
            if headers.get("If-Match") != etag:
                return Response(412, {"message": "precondition failed"})
            self.jobs[path] = (body, f'"{int(etag.strip(chr(34))) + 1}"')
            return Response(200, body)
        return Response(404, {"message": "not found"})

    def delete(self, path, **kw):
        self.calls.append(("DELETE", path, None))
        if self._refused("DELETE", path):
            return self._refused("DELETE", path)
        for folder_id, items in self.members.items():
            for member in list(items):
                if member["uri"] == path:
                    items.remove(member)
                    self._just_deleted[(folder_id, member["name"])] = self.conflicts
        self.files.pop(path, None)
        return Response(204)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False
