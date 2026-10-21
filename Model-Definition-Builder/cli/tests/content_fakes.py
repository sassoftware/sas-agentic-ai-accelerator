# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What the content tests share: a small checkout with a content manifest, a
transfer package built the way the transfer service builds one, and a SAS Viya
that lives in memory.

None of this is a test. The shapes are the ones read from the packages the
repository shipped up to 2.2.x and from the requests of deploy-rag-content:

* an object of a package says where it sits through a `parentFolder` connector
  beside its transfer object; a file that is a child of a report through a
  `parent` connector inside it and through the `parentUri` of its file
  resource - one of the four pictures of the usage report had only the
  latter. A file a report merely shows (the target of its `image` connector)
  can sit in any folder;
* the root folder of a package that was exported from the root of SAS Content
  has no parent: no `parentFolderUri` in its body, no `contentSourceLocation`.
  A folder exported from below others carries both;
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
from mdb.core.packages import NEUTRAL_DEFAULTS, deflate, program_node_content

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
      - {{name: _contextName, default: SAS Studio compute context, label: Context Name, siteSetting: true}}
      - {{name: _action, default: "prompts,form,execute"}}
  - path: Prompt Builder/Optimize
    kind: jobDefinition
    code: src/optimize.sas
    parameters:
      - {{name: _action, default: execute}}
      - {{name: contentRoot, default: {CONTENT_ROOT}, siteSetting: true}}
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

    def __init__(self, root: str = "SAS Agentic AI Accelerator", above: tuple = (), location: str = ""):
        self._n = 0
        self.details: list = []
        self.uris: dict = {}            # path below the root -> uri
        parent = None
        for name in above:              # folders above the content root, as an export of a nested root has them
            parent = self._folder_object(name, parent)
        self.root_uri = self._folder_object(root, parent)
        self.uris[""] = self.root_uri
        if location:
            # a root that was exported from below other folders, which the
            # package no longer holds: it still says where it was
            wrapper = self.details[-1]["transferObject"]
            wrapper["contentSourceLocation"] = location
            wrapper["content"] = b64({"folder": {"name": root, "parentFolderUri": "/folders/folders/above"}})

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

    def image(self, name: str, report_uri=None, parent_uri=None, data: bytes = b"\x89PNG\xff") -> str:
        """A file that sits in no folder: a picture of a report. With
        `report_uri` it is a child of that report and says so by a `parent`
        connector, with `parent_uri` only by its file resource. With neither
        it is no report's child: only a report's `image` connector names it."""
        inner = [{"id": self._id(), "type": "parent", "uri": report_uri, "relatedUris": []}] if report_uri else []
        resource = {"name": name}
        if report_uri or parent_uri:
            resource["parentUri"] = report_uri or parent_uri
        return self._add("application/vnd.sas.file+json", name, "/files/files/",
                         b64({"fileResource": resource, "content": b64(data)}), None, inner)

    def attach(self, report_uri: str, image_uri: str) -> None:
        """Make a report show a file: an `image` connector of the report."""
        for detail in self.details:
            obj = detail["transferObject"]
            if obj["summary"]["links"][0]["uri"] == report_uri:
                obj["connectors"].append({"id": self._id(), "type": "image", "uri": image_uri,
                                          "relatedUris": [report_uri], "forceMapping": False})

    def node_type(self, node_id: str, name: str, code: str = 'package "${PACKAGE_NAME}" /inline; endpackage;',
                  content_id=None, user=None, terms=None) -> str:
        """A custom node type as an export carries it: in no folder, under its
        own id, with its content (code and terms) in the same body - and, with
        `user`, the user who made it in createdBy."""
        if terms is None:
            terms = [{"name": "llmBody", "dataType": "string", "length": 10485760, "direction": "input",
                      "generateDataGridColumns": False}]
        who = {"createdBy": user} if user else {}
        uri = f"/decisions/decisionNodeTypes/{node_id}"
        body = {"decisionNodeTypeRep": {"id": node_id, "name": name, "type": "static", "themeId": "DNT_THEME1",
                                        **who, "links": [{"rel": "self", "uri": uri}]},
                "decisionNodeTypeContentRep": {"id": content_id or self._id(), "nodeTypeId": node_id,
                                               "contentType": "DS2", "staticContent": code, **who,
                                               "nodeTypeSignatureTerms": terms}}
        self.details.append({"connectors": [], "transferObject": {
            "id": self._id(), "version": 2, "state": "enabled", "substitutions": {},
            "summary": {"id": node_id, "type": "decisionNodeType", "name": name, **who,
                        "links": [{"method": "GET", "rel": "self", "href": uri, "uri": uri}]},
            "content": b64(body), "connectors": []}})
        return uri

    def decision(self, path: str, nodes=(), versions: int = 2, unnamed: bool = False, user=None) -> str:
        """A decision whose every version has one step per node type of
        `nodes` ((id, name), ...): a custom node step that names its node type
        by its address - or, with `unnamed`, the oldest version's step has lost
        it (its node type was deleted). The decision names each node type by a
        `dntStatic` connector, and beside it by one with a name only, as an
        export writes them."""
        parent, name = self._in(path)
        who = {"createdBy": user} if user else {}

        def version(major: int, lost: bool = False) -> dict:
            steps = [{"type": "application/vnd.sas.decision.step.custom.object", "id": self._id(),
                      "customObject": {"isRestDNT": False, "name": node_name, "type": "dntStatic",
                                       **({} if lost else {"uri": f"/decisions/decisionNodeTypes/{node_id}"})},
                      "mappings": [{"direction": "input", "stepTermName": "llmBody", "targetDecisionTermName": "llmBody"}]}
                     for node_id, node_name in nodes]
            return {"id": self._id(), "name": name, "majorRevision": major, "minorRevision": 0,
                    **who, "flow": {"steps": steps}}
        body = {"decisionVersionRep": version(versions),
                "revisions": [version(major, unnamed and major == 1) for major in range(1, versions)],
                "globalVariables": {}, "specificVersionExport": False}
        inner = []
        for node_id, node_name in nodes:
            inner.append({"id": self._id(), "name": node_name, "relatedUris": [], "forceMapping": False, "hints": {}})
            inner.append({"id": self._id(), "name": node_name, "type": "dntStatic", "relatedUris": [],
                          "forceMapping": False, "hints": {}, "uri": f"/decisions/decisionNodeTypes/{node_id}"})
        return self._add("decision", name, "/decisions/flows/", b64(body), parent, inner)

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
        elif entry.kind == "decision":
            # the node types the entry lists, as dependencies of the decision
            nodes = [(listed["id"], listed["name"]) for listed in entry.node_types] if other is None else other
            programs = {listed["id"]: listed.get("content") for listed in entry.node_types}
            for node_id, node_name in nodes:
                if programs.get(node_id):
                    # the code and the terms of the program the entry names, as the release exports them
                    made = program_node_content(content.repository_file(manifest.repo_root, programs[node_id])
                                                .read_text(encoding="utf-8"))
                    package.node_type(node_id, node_name, code=made["staticContent"],
                                      terms=made["nodeTypeSignatureTerms"])
                else:
                    package.node_type(node_id, node_name)
            package.decision(entry.path, nodes)
        else:
            report = package.report(entry.path, ALL_NEUTRAL if other is None else other,
                                    tables=entry.checks.get("tables", ()))
            # the files its entry lists, as children of the report. Their
            # bytes are made up, so an entry that gives a hash is not met.
            for index, listed in enumerate(entry.files):
                image = package.image(listed["name"], report if index % 2 else None, report,
                                      data=f"made up: {listed['name']}".encode("utf-8"))
                package.attach(report, image)
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
    """Folders, files, custom steps, job definitions and reports, with the
    calls made.

    A member of a folder carries its `type` as the Folders service gives it:
    `child` for an object of the folder, `reference` for a pointer at an
    object that lives elsewhere. A file has an ETag that changes with its
    content, and its content is replaced with `PUT .../content` under that
    ETag - the file keeps its id. `refuse` maps (method, path prefix) to a
    status to answer with, `drop` to an exception to raise instead of an
    answer (a timeout).
    """

    def __init__(self):
        self._n = 0
        self.folders: dict = {}          # id -> {"name", "parent", "description"}
        self.members: dict = {}          # folder id -> [{"name", "uri", "type"}]
        self.files: dict = {}            # uri -> bytes
        self.etags: dict = {}            # file uri -> version number
        self.steps: dict = {}            # uri -> body
        self.jobs: dict = {}             # uri -> (body, etag)
        self.reports: dict = {}          # report name -> content (XML)
        self.calls: list = []
        self.refuse: dict = {}
        self.drop: dict = {}
        #: The custom node types of SAS Intelligent Decisioning, by id - None
        #: for a site without it, where the collection answers 404.
        self.node_types = None

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
                if member["uri"].startswith("/folders/folders/") and member.get("type") != "reference":
                    walk(member["uri"].rsplit("/", 1)[-1], path + "/")
        walk(self.folder_id(root), "")
        return sorted(found)

    def wrote(self) -> list:
        return [(method, path) for method, path, _ in self.calls if method != "GET"]

    def ids(self, root: str) -> dict:
        """{path below root: uri} of every object that is not a folder."""
        return {path: self.at(f"{root}/{path}")[0] for path in self.tree(root)
                if not self.at(f"{root}/{path}")[0].startswith("/folders/")}

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
            self.members[parent].append({"name": name, "uri": f"/folders/folders/{folder_id}", "type": "child"})
        return folder_id

    def put_member(self, path: str, uri_prefix: str, value) -> str:
        above, _, name = path.rpartition("/")
        folder_id = self.make_folder(above)
        uri = f"{uri_prefix}{self._new()}"
        self.members[folder_id].append({"name": name, "uri": uri, "type": "child"})
        store = {"/files/files/": self.files, "/dataFlows/steps/": self.steps}.get(uri_prefix)
        if store is not None:
            store[uri] = value
        elif uri_prefix == "/jobDefinitions/definitions/":
            self.jobs[uri] = ({**value, "id": uri.rsplit("/", 1)[-1]}, '"1"')
        if uri_prefix == "/files/files/":
            self.etags[uri] = 1
        return uri

    def put_reference(self, path: str, uri: str) -> None:
        """A member of the folder that points at an object living elsewhere."""
        above, _, name = path.rpartition("/")
        self.members[self.make_folder(above)].append({"name": name, "uri": uri, "type": "reference"})

    # -- the session ---------------------------------------------------------
    def _refused(self, method, path):
        for (m, prefix), error in self.drop.items():
            if m == method and path.startswith(prefix):
                raise error
        for (m, prefix), status in self.refuse.items():
            if m == method and path.startswith(prefix):
                return Response(status, {"message": "refused"})
        return None

    def _file_headers(self, uri) -> dict:
        return {"ETag": f'"{self.etags.get(uri, 1)}"', "Content-Type": "application/octet-stream"}

    def _holder(self, uri):
        """(folder id, member) of the child member that is `uri`, or (None, None)."""
        for folder_id, items in self.members.items():
            for member in items:
                if member["uri"] == uri and member.get("type") != "reference":
                    return folder_id, member
        return None, None

    def ancestors(self, uri) -> list:
        """The folders above an object, nearest first, as /folders/ancestors gives them."""
        folder_id, _member = self._holder(uri)
        found = []
        while folder_id:
            found.append({"id": folder_id, "name": self.folders[folder_id]["name"], "type": "folder"})
            folder_id = self.folders[folder_id]["parent"]
        return found

    def get(self, path, params=None, headers=None, **kw):
        self.calls.append(("GET", path, params))
        refused = self._refused("GET", path)
        if refused:
            return refused
        params = params or {}
        if path == "/folders/folders/@item":
            folder_id = self.folder_id(params["path"])
            return Response(200, {"id": folder_id, "name": self.folders[folder_id]["name"]}) if folder_id \
                else Response(404, {"message": "no such folder"})
        if path.startswith("/folders/folders/") and path.endswith("/members"):
            items = self.members.get(path.split("/")[3], [])
            start, limit = int(params.get("start", 0)), int(params.get("limit", 20))
            return Response(200, {"items": items[start:start + limit], "count": len(items)})
        if path == "/folders/ancestors":
            if self._holder(params.get("childUri"))[0] is None:
                return Response(404, {"message": "not found"})
            return Response(200, {"childUri": params.get("childUri"), "ancestors": self.ancestors(params["childUri"])})
        if path.endswith("/content") and path[:-len("/content")] in self.files:
            uri = path[:-len("/content")]
            return Response(200, content=self.files[uri], headers=self._file_headers(uri))
        if path in self.files:
            member = self._holder(path)[1] or {}
            return Response(200, {"id": path.rsplit("/", 1)[-1], "name": member.get("name"),
                                  "contentType": "application/octet-stream"}, headers=self._file_headers(path))
        if path in self.steps:
            return Response(200, {**as_registered(self.steps[path]), "id": path.rsplit("/", 1)[-1]})
        if path in self.jobs:
            body, etag = self.jobs[path]
            return Response(200, body, headers={"ETag": etag})
        if path == "/reports/reports":
            wanted = str(params.get("filter", ""))
            return Response(200, {"items": [{"id": name, "name": name} for name in self.reports
                                            if f'"{name}"' in wanted]})
        if path.startswith("/reports/reports/") and path.endswith("/content"):
            name = path[len("/reports/reports/"):-len("/content")]
            if name in self.reports:
                return Response(200, content=self.reports[name].encode("utf-8"), headers={"ETag": '"r1"'})
        if path.startswith("/casManagement/servers"):
            return Response(200, {"items": [{"name": "cas-shared-default"}]})
        if path == "/decisions/decisionNodeTypes" and self.node_types is not None:
            items = list(self.node_types.values())
            start, limit = int(params.get("start", 0)), int(params.get("limit", 20))
            return Response(200, {"items": items[start:start + limit], "count": len(items)})
        if path.startswith("/decisions/decisionNodeTypes/") and self.node_types is not None \
                and path.rsplit("/", 1)[-1] in self.node_types:
            return Response(200, self.node_types[path.rsplit("/", 1)[-1]], headers={"ETag": '"n1"'})
        return Response(404, {"message": "not found"})

    def post(self, path, params=None, data=None, headers=None, **kw):
        self.calls.append(("POST", path, params))
        refused = self._refused("POST", path)
        if refused:
            return refused
        params = params or {}
        parent = str(params.get("parentFolderUri") or "").rsplit("/", 1)[-1] or None
        if path == "/folders/folders":
            body = json.loads(data)
            folder_id = self._create_folder(body["name"], parent, body.get("description", ""))
            return Response(201, {"id": folder_id, "name": body["name"]})
        if path == "/files/files":
            name = params["filename"]
            assert f'filename="{name}"' in headers["Content-Disposition"]
            if any(m["name"] == name and m["uri"].startswith("/files/files/") for m in self.members[parent]):
                # the rule the Folders service was seen to hold: one name per kind in a folder
                return Response(409, {"message": f'An item named "{name}" of type "File" already exists'})
            uri = f"/files/files/{self._new()}"
            self.files[uri] = bytes(data)
            self.etags[uri] = 1
            self.members[parent].append({"name": name, "uri": uri, "type": "child"})
            return Response(201, {"id": uri.rsplit("/", 1)[-1]})
        if path == "/dataFlows/steps":
            body = json.loads(data)
            uri = f"/dataFlows/steps/{self._new()}"
            self.steps[uri] = body
            self.members[parent].append({"name": body["name"], "uri": uri, "type": "child"})
            return Response(201, {"id": uri.rsplit("/", 1)[-1]})
        if path == "/jobDefinitions/definitions":
            body = json.loads(data)
            uri = f"/jobDefinitions/definitions/{self._new()}"
            self.jobs[uri] = ({**body, "id": uri.rsplit("/", 1)[-1]}, '"1"')
            self.members[parent].append({"name": body["name"], "uri": uri, "type": "child"})
            return Response(201, self.jobs[uri][0])
        return Response(400, {"message": "bad request"})

    def put(self, path, data=None, headers=None, **kw):
        self.calls.append(("PUT", path, None))
        refused = self._refused("PUT", path)
        if refused:
            return refused
        if path.endswith("/content") and path[:-len("/content")] in self.files:
            uri = path[:-len("/content")]
            if headers.get("If-Match") != f'"{self.etags[uri]}"':
                return Response(412 if headers.get("If-Match") else 428, {"message": "precondition"})
            self.files[uri] = bytes(data)
            self.etags[uri] += 1
            return Response(200, {"id": uri.rsplit("/", 1)[-1]})
        if path.startswith("/reports/reports/") and path.endswith("/content"):
            self.reports[path[len("/reports/reports/"):-len("/content")]] = bytes(data).decode("utf-8")
            return Response(204)
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
        """Nothing of the content code deletes. A test sees it in `wrote()`."""
        self.calls.append(("DELETE", path, None))
        for items in self.members.values():
            for member in list(items):
                if member["uri"] == path:
                    items.remove(member)
        self.files.pop(path, None)
        return Response(204)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False
