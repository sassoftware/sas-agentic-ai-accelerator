# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The accelerator's SAS Content folder, as one allowlist.

WHAT THIS IS. `SAS-Viya-Integrations/package-manifest.yaml` lists every object
that ships in the content folder (`/SAS Agentic AI Accelerator` unless a site
moved it): one entry per object, with the path below the content root, the
kind, and where the object comes from in the repository. Three commands read
it and nothing else decides what ships:

* `mdb content-deploy` brings a content folder to the state of the checkout;
* `mdb package-export` keeps the listed objects of an export and drops the rest;
* `mdb package-check` compares the one transfer package with the checkout.

WHY A LIST AND NOT THE FOLDER. A content folder on a shared system is not only
shipped content: the Prompt Builder writes generated artifacts into
`<content root>/RAG/generated`, another user can park a report in it, and an
export with dependencies brings authorization rules that name users. "Whatever
is in the folder" is therefore not a release rule. The list is, and the
never-ship rule below holds even against the list.

THE KINDS.

    folder          a folder. The folders of every entry are implied, so an
                    entry is only needed for an empty folder or a description.
    file            `source: <repository path>`, or `generated: <generator>`
                    for a file mdb writes itself (GENERATORS).
    step            `source: <repository path of the .step file>`. A custom
                    step is registered through /dataFlows/steps, not uploaded.
    jobDefinition   `code: <repository path of the program>`, optional
                    `form: <repository path of an HTML file>`, optional
                    `parameters:` (name, type, default, label) and
                    `description`. A job definition holds a COPY of its
                    program and of its form.
    report          no repository source: a report is authored in SAS Visual
                    Analytics and the package is its only source. `checks:`
                    names what `mdb package-check` asks of it.

This module holds the manifest and what each entry is expected to be - the
bytes of a file, the compared parts of a step and of a job definition - so that
the deploy and the check ask the same question. Nothing here needs SAS Viya.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Optional

import yaml

from .. import __version__

#: Where the manifest lives in a checkout.
MANIFEST_PATH = ("SAS-Viya-Integrations", "package-manifest.yaml")

KINDS = ("folder", "file", "step", "jobDefinition", "report")

#: The fields an entry of each kind may carry besides `path` and `kind`.
FIELDS = {
    "folder": ("description",),
    "file": ("source", "generated"),
    "step": ("source",),
    "jobDefinition": ("code", "form", "parameters", "description"),
    "report": ("checks",),
}

#: What a job definition parameter may carry, and the types SAS Job Execution knows.
PARAMETER_FIELDS = ("name", "type", "default", "label", "required")
PARAMETER_TYPES = ("character", "numeric", "date")

#: What `checks:` of a report may carry. `optionSet` names a set of option
#: labels and neutral defaults mdb knows (OPTION_SETS); `tables` the CAS tables
#: the report binds, by name. Hosts and per-user states are checked for every
#: report and need no entry.
CHECK_FIELDS = ("optionSet", "tables")
OPTION_SETS = ("prompt-builder",)

#: The code SAS Job Execution stores for a job definition that only serves a
#: form. An entry with a `form` and no `code` is expected to hold exactly this.
FORM_ONLY_CODE = "/* SAS templated code goes here */"

#: The parts of a custom step that are its content. Everything else in a .step
#: file is who saved it and when.
STEP_PARTS = ("templates", "ui", "flowMetadata")

#: The parts of a job definition that are its content.
JOB_PARTS = ("code", "form", "parameters")


class ManifestError(ValueError):
    """The manifest cannot be used. `problems` holds one sentence per defect."""

    def __init__(self, where, problems):
        self.where = str(where)
        self.problems = list(problems)
        super().__init__(f"{self.where}: " + "; ".join(self.problems))


# ---------------------------------------------------------------------------
# what may never ship
# ---------------------------------------------------------------------------
def never_ship(path: str) -> Optional[str]:
    """Why an object at `path` (below the content root) may not ship, or None.

    Showcase and example content lives outside the product and outside the
    repository; `generated` is where the Prompt Builder writes the artifacts of
    a site's own RAG setups; `ZZ_VERIFY...` is the name test objects carry.
    The rule is asked of the manifest when it is loaded and again of every
    object an export keeps, so a listed path does not get such an object out.
    """
    for segment in str(path or "").replace("\\", "/").split("/"):
        low = segment.strip().lower()
        if "showcases" in low:
            return f"'{segment}' is showcase content"
        if "example" in low:
            return f"'{segment}' is example content"
        if low == "generated":
            return f"'{segment}' holds what the Prompt Builder generates for a site's own setups"
        if low.startswith("zz_verify"):
            return f"'{segment}' is a test object"
    return None


def content_root_problem(content_root: str) -> Optional[str]:
    """Why a folder may not be exported as a content root, or None. A test
    root may carry a ZZ_VERIFY name; showcase and example folders may not be
    exported under any name."""
    for segment in str(content_root or "").split("/"):
        low = segment.lower()
        if "showcases" in low or "example" in low:
            return f"'{segment}' is showcase or example content, which never ships"
    return None


# ---------------------------------------------------------------------------
# the manifest
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Entry:
    """One object of the content folder."""

    path: str
    kind: str
    source: Optional[str] = None
    generated: Optional[str] = None
    code: Optional[str] = None
    form: Optional[str] = None
    parameters: tuple = ()
    description: str = ""
    checks: dict = field(default_factory=dict, hash=False, compare=False)
    #: True for a folder no entry names: the folder of a listed object.
    implied: bool = False

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    @property
    def folder(self) -> str:
        return self.path.rsplit("/", 1)[0] if "/" in self.path else ""


@dataclass
class ContentManifest:
    repo_root: Path
    content_root: str
    #: Repository path of the one transfer package, with forward slashes.
    package: str
    entries: list = field(default_factory=list)

    @property
    def package_path(self) -> Path:
        return self.repo_root.joinpath(*PurePosixPath(self.package).parts)

    def folders(self) -> list:
        """Every folder below the content root, listed or implied, parents first."""
        listed = {e.path: e for e in self.entries if e.kind == "folder"}
        paths = set(listed)
        for entry in self.entries:
            parts = entry.path.split("/")
            for depth in range(1, len(parts)):
                paths.add("/".join(parts[:depth]))
        return [listed.get(path) or Entry(path=path, kind="folder", implied=True)
                for path in sorted(paths, key=lambda p: (p.count("/"), p.casefold()))]

    def objects(self) -> list:
        """Every entry that is not a folder, in the order of the file."""
        return [e for e in self.entries if e.kind != "folder"]

    def all_entries(self) -> list:
        """Folders first (parents before children), then the objects."""
        return self.folders() + self.objects()

    def find(self, path: str, kind: str) -> Optional[Entry]:
        if kind == "folder":
            return next((e for e in self.folders() if e.path == path), None)
        return next((e for e in self.entries if e.path == path and e.kind == kind), None)

    def source_path(self, relative: str) -> Path:
        return self.repo_root.joinpath(*PurePosixPath(relative).parts)

    def sources(self) -> list:
        """Every repository path an entry is a copy of, with forward slashes."""
        found = []
        for entry in self.entries:
            for value in (entry.source, entry.code, entry.form):
                if value and value not in found:
                    found.append(value)
        return found


def manifest_file(repo_root) -> Path:
    return Path(repo_root).joinpath(*MANIFEST_PATH)


def _repo_file_problem(repo_root: Path, value, what: str) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return f"{what} must name a file of the repository"
    pure = PurePosixPath(value.replace("\\", "/"))
    if pure.is_absolute() or ".." in pure.parts or ":" in value:
        return f"{what} '{value}' must be a path inside the repository"
    if not repo_root.joinpath(*pure.parts).is_file():
        return f"{what} '{value}' does not exist in the repository"
    return None


def _path_problem(path) -> Optional[str]:
    if not isinstance(path, str) or not path.strip():
        return "an entry has no path"
    if path != path.strip() or path.startswith("/") or path.endswith("/") or "\\" in path:
        return f"'{path}': a path is written below the content root, with forward slashes and no slash at either end"
    if any(not segment.strip() or segment != segment.strip() or segment in (".", "..")
           for segment in path.split("/")):
        return f"'{path}': a path segment is empty, '.' or '..', or starts or ends with a blank"
    return None


def _parameters(raw, where: str, problems: list) -> tuple:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        problems.append(f"{where}: parameters must be a list")
        return ()
    result, names = [], set()
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
            problems.append(f"{where}: a parameter needs a name")
            continue
        unknown = sorted(set(item) - set(PARAMETER_FIELDS))
        if unknown:
            problems.append(f"{where}: parameter '{item['name']}' has unknown field(s) {', '.join(unknown)}")
        kind = item.get("type", "character")
        if kind not in PARAMETER_TYPES:
            problems.append(f"{where}: parameter '{item['name']}' has unknown type '{kind}' "
                            f"(one of {', '.join(PARAMETER_TYPES)})")
        if item["name"] in names:
            problems.append(f"{where}: parameter '{item['name']}' is listed twice")
        names.add(item["name"])
        default = item.get("default", "")
        result.append({
            "name": item["name"], "type": kind,
            "default": "" if default is None else (str(default).lower() if isinstance(default, bool) else str(default)),
            "label": str(item.get("label") or ""), "required": bool(item.get("required", False)),
        })
    return tuple(result)


def _checks(raw, where: str, problems: list) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        problems.append(f"{where}: checks must be a mapping")
        return {}
    unknown = sorted(set(raw) - set(CHECK_FIELDS))
    if unknown:
        problems.append(f"{where}: unknown check(s) {', '.join(unknown)} (known: {', '.join(CHECK_FIELDS)})")
    checks: dict = {}
    if "optionSet" in raw:
        if raw["optionSet"] not in OPTION_SETS:
            problems.append(f"{where}: unknown optionSet '{raw['optionSet']}' (known: {', '.join(OPTION_SETS)})")
        checks["optionSet"] = raw["optionSet"]
    if "tables" in raw:
        tables = raw["tables"]
        if not isinstance(tables, list) or not all(isinstance(t, str) and t.strip() for t in tables):
            problems.append(f"{where}: tables must be a list of table names")
        else:
            checks["tables"] = [t.strip() for t in tables]
    return checks


def parse_manifest(document, repo_root, where="package-manifest.yaml", check_sources: bool = True) -> ContentManifest:
    """A ContentManifest from the parsed YAML, or ManifestError with every
    defect found: an unknown kind or field, a source that does not exist, two
    entries with one path, an object listed where a folder has to be, a path
    the never-ship rule forbids."""
    repo_root = Path(repo_root)
    problems: list = []
    if not isinstance(document, dict):
        raise ManifestError(where, ["the manifest must be a mapping with version, contentRoot, package and entries"])
    unknown = sorted(set(document) - {"version", "contentRoot", "package", "entries"})
    if unknown:
        problems.append(f"unknown top-level field(s) {', '.join(map(str, unknown))}")
    if document.get("version") != 1:
        problems.append(f"version must be 1, not {document.get('version')!r}")
    content_root = document.get("contentRoot")
    if not isinstance(content_root, str) or not content_root.startswith("/") or content_root.rstrip("/") != content_root \
            or len(content_root) < 2:
        problems.append("contentRoot must be a SAS Content path such as '/SAS Agentic AI Accelerator'")
        content_root = "/"
    elif content_root_problem(content_root):
        problems.append(f"contentRoot: {content_root_problem(content_root)}")
    package = document.get("package")
    if not isinstance(package, str) or not package.endswith(".json") or PurePosixPath(package).is_absolute() \
            or ".." in PurePosixPath(package).parts or "\\" in package:
        problems.append("package must be the repository path of the transfer package, a .json file")
        package = "package.json"
    raw_entries = document.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        problems.append("entries must be a list with at least one entry")
        raw_entries = []

    entries: list = []
    seen: dict = {}
    for index, raw in enumerate(raw_entries, start=1):
        if not isinstance(raw, dict):
            problems.append(f"entry {index} is not a mapping")
            continue
        path, kind = raw.get("path"), raw.get("kind")
        bad_path = _path_problem(path)
        if bad_path:
            problems.append(f"entry {index}: {bad_path}")
            continue
        where_entry = f"'{path}'"
        if kind not in KINDS:
            problems.append(f"{where_entry}: unknown kind {kind!r} (one of {', '.join(KINDS)})")
            continue
        unknown = sorted(set(raw) - {"path", "kind", *FIELDS[kind]})
        if unknown:
            problems.append(f"{where_entry}: a {kind} has no field(s) {', '.join(map(str, unknown))} "
                            f"(it takes {', '.join(FIELDS[kind]) or 'none'})")
        forbidden = never_ship(path)
        if forbidden:
            problems.append(f"{where_entry}: {forbidden} and never ships")
        key = path.casefold()
        if key in seen:
            problems.append(f"{where_entry}: listed twice (as {seen[key]} and as {kind}) - one path, one entry")
            continue
        seen[key] = kind

        values: dict = {}
        if kind == "file":
            if ("source" in raw) == ("generated" in raw):
                problems.append(f"{where_entry}: a file has either a source or a generator")
            elif "generated" in raw:
                if raw["generated"] not in GENERATORS:
                    problems.append(f"{where_entry}: unknown generator {raw['generated']!r} "
                                    f"(known: {', '.join(GENERATORS)})")
                values["generated"] = raw["generated"]
            else:
                bad = _repo_file_problem(repo_root, raw["source"], "source") if check_sources else None
                if bad:
                    problems.append(f"{where_entry}: {bad}")
                values["source"] = raw["source"]
        elif kind == "step":
            bad = _repo_file_problem(repo_root, raw.get("source"), "source") if check_sources else None
            if not isinstance(raw.get("source"), str):
                problems.append(f"{where_entry}: a step needs the source of its .step file")
            elif bad:
                problems.append(f"{where_entry}: {bad}")
            elif check_sources:
                try:
                    step = json.loads(repo_root.joinpath(*PurePosixPath(raw["source"]).parts).read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    problems.append(f"{where_entry}: source '{raw['source']}' is not a readable .step file ({exc})")
                else:
                    if not isinstance(step, dict) or step.get("name") != path.rsplit("/", 1)[-1]:
                        problems.append(
                            f"{where_entry}: the step in '{raw['source']}' is named "
                            f"'{step.get('name') if isinstance(step, dict) else '?'}', and a step is registered "
                            "under the name its file holds - the last part of the path must be that name")
            values["source"] = raw.get("source") if isinstance(raw.get("source"), str) else None
        elif kind == "jobDefinition":
            if "code" not in raw and "form" not in raw:
                problems.append(f"{where_entry}: a job definition needs a code or a form")
            for part in ("code", "form"):
                if part in raw:
                    bad = _repo_file_problem(repo_root, raw[part], part) if check_sources else None
                    if not isinstance(raw[part], str):
                        problems.append(f"{where_entry}: {part} must name a file of the repository")
                    elif bad:
                        problems.append(f"{where_entry}: {bad}")
                    else:
                        values[part] = raw[part]
            values["parameters"] = _parameters(raw.get("parameters"), where_entry, problems)
        elif kind == "report":
            values["checks"] = _checks(raw.get("checks"), where_entry, problems)
        if "description" in raw and kind in ("folder", "jobDefinition"):
            if not isinstance(raw["description"], str):
                problems.append(f"{where_entry}: description must be text")
            else:
                values["description"] = raw["description"]
        entries.append(Entry(path=path, kind=kind, **values))

    # an object cannot sit where a folder has to be
    by_key = {entry.path.casefold(): entry for entry in entries}
    for entry in entries:
        parts = entry.path.split("/")
        for depth in range(1, len(parts)):
            above = by_key.get("/".join(parts[:depth]).casefold())
            if above is not None and above.kind != "folder":
                problems.append(f"'{above.path}' is listed as {above.kind} and is the folder of '{entry.path}'")
    if problems:
        # one line per defect, each once
        raise ManifestError(where, list(dict.fromkeys(problems)))
    return ContentManifest(repo_root=repo_root, content_root=content_root, package=package, entries=entries)


def load_manifest(repo_root, path=None, check_sources: bool = True) -> ContentManifest:
    """The manifest of a checkout (or the file at `path`), validated."""
    repo_root = Path(repo_root)
    source = Path(path) if path else manifest_file(repo_root)
    if not source.is_file():
        raise ManifestError(source, ["the file does not exist"])
    try:
        document = yaml.safe_load(source.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ManifestError(source, [f"not readable as YAML ({exc})"]) from exc
    return parse_manifest(document, repo_root, source, check_sources)


# ---------------------------------------------------------------------------
# what an entry is expected to be
# ---------------------------------------------------------------------------
def lf(value):
    """Line endings normalised, in a string or in every string of a nested
    value: a checkout may hold CRLF where SAS Content holds LF, and that
    difference is not a difference in content."""
    if isinstance(value, str):
        return value.replace("\r\n", "\n")
    if isinstance(value, dict):
        return {key: lf(item) for key, item in value.items()}
    if isinstance(value, list):
        return [lf(item) for item in value]
    return value


def lf_bytes(data: bytes) -> bytes:
    """The bytes of a text file with LF line endings; anything that is not
    UTF-8 text (an image) as it is."""
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    return data.replace(b"\r\n", b"\n")


def file_bytes(manifest: ContentManifest, entry: Entry) -> bytes:
    """The bytes a file entry has in SAS Content.

    A repository file is deployed with LF line endings whatever the checkout
    holds - a Windows checkout has CRLF where the repository has LF - so the
    same file leaves every checkout with the same bytes and the same hash. A
    generated file is deployed as its generator writes it.
    """
    if entry.generated:
        return GENERATORS[entry.generated](manifest)
    return lf_bytes(manifest.source_path(entry.source).read_bytes())


def _read_text(manifest: ContentManifest, relative: str) -> str:
    with manifest.source_path(relative).open(encoding="utf-8", newline="") as handle:
        return lf(handle.read())


def step_body(manifest: ContentManifest, entry: Entry) -> dict:
    """The .step file of a step entry, parsed."""
    return json.loads(manifest.source_path(entry.source).read_text(encoding="utf-8"))


#: What the dataFlows service adds to a port, or leaves out of one, when it
#: stores a step: measured by comparing registered steps with their .step files.
_PORT_KEYS_DROPPED = ("id", "label", "localDisplayName", "localDescription")


def _port_view(port) -> dict:
    if not isinstance(port, dict):
        return {"value": port}
    view = {key: value for key, value in port.items() if value is not None and key not in _PORT_KEYS_DROPPED}
    view.setdefault("defaultEntries", 0)
    return view


def flow_metadata_view(metadata) -> dict:
    """A step's flowMetadata as it is compared: the ports in their order,
    without what the service adds (`localDisplayName`, `defaultEntries: 0`) or
    leaves out (`id`, `label`, a null `columnDelta`, an empty `inputPorts`)."""
    metadata = metadata if isinstance(metadata, dict) else {}
    return {side: [_port_view(port) for port in metadata.get(side) or []]
            for side in ("inputPorts", "outputPorts")}


def step_view(body: dict) -> dict:
    """The compared parts of a step, in comparable form."""
    body = body if isinstance(body, dict) else {}
    return {"templates": lf(body.get("templates")), "ui": lf(body.get("ui")),
            "flowMetadata": flow_metadata_view(body.get("flowMetadata"))}


def step_differences(expected: dict, actual: dict) -> list:
    """The STEP_PARTS in which two steps differ."""
    one, other = step_view(expected), step_view(actual)
    return [part for part in STEP_PARTS if one[part] != other[part]]


def job_definition_body(manifest: ContentManifest, entry: Entry) -> dict:
    """The job definition an entry describes, as the jobDefinitions service
    takes it: the program as `code`, the HTML form as the property `form`."""
    body: dict = {
        "name": entry.name, "type": "Compute",
        "code": _read_text(manifest, entry.code) if entry.code else FORM_ONLY_CODE,
        "parameters": [
            {"version": 1, "name": p["name"], "defaultValue": p["default"], "type": p["type"],
             **({"label": p["label"]} if p["label"] else {}), "required": p["required"]}
            for p in entry.parameters],
        "properties": [{"name": "form", "value": _read_text(manifest, entry.form)}] if entry.form else [],
    }
    if entry.description:
        body["description"] = entry.description
    return body


def job_view(body: dict) -> dict:
    """The compared parts of a job definition, in comparable form."""
    body = body if isinstance(body, dict) else {}
    forms = [p.get("value") or "" for p in body.get("properties") or []
             if isinstance(p, dict) and p.get("name") == "form"]
    parameters = sorted(
        ({"name": p.get("name") or "", "type": p.get("type") or "character",
          "defaultValue": "" if p.get("defaultValue") is None else str(p.get("defaultValue")),
          "label": p.get("label") or "", "required": bool(p.get("required"))}
         for p in body.get("parameters") or [] if isinstance(p, dict)),
        key=lambda p: p["name"])
    return {"code": lf(body.get("code") or ""), "form": lf(forms[0]) if forms else None, "parameters": parameters}


def job_differences(expected: dict, actual: dict) -> list:
    """The JOB_PARTS in which two job definitions differ."""
    one, other = job_view(expected), job_view(actual)
    return [part for part in JOB_PARTS if one[part] != other[part]]


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def entry_hash(manifest: ContentManifest, entry: Entry) -> Optional[str]:
    """The content hash release.json carries for an entry.

    * file: SHA-256 of the bytes the file has in SAS Content (`file_bytes`);
    * step: SHA-256 of its compared parts (`step_view`) as canonical JSON;
    * job definition: SHA-256 of its compared parts (`job_view`), the same way;
    * folder, report, and release.json itself: none - a folder has no content,
      a report has no source in the repository, and a file cannot hold its own
      hash.
    """
    if entry.kind == "file":
        if entry.generated == RELEASE_INFO:
            return None
        return _sha256(file_bytes(manifest, entry))
    if entry.kind == "step":
        return _sha256(_canonical(step_view(step_body(manifest, entry))))
    if entry.kind == "jobDefinition":
        return _sha256(_canonical(job_view(job_definition_body(manifest, entry))))
    return None


# ---------------------------------------------------------------------------
# generated files
# ---------------------------------------------------------------------------
RELEASE_TABLE = "release-table"
RELEASE_INFO = "release-info"
RELEASE_INFO_KIND = "sas-agentic-ai-accelerator/release"


def release_table_bytes(manifest: ContentManifest) -> bytes:
    """ACCELERATOR_RELEASES as CSV, byte for byte what `mdb load-releases
    --csv` writes from the checkout's CHANGELOG.md."""
    from .releases import csv_bytes, parse_changelog, rows_from
    changelog = manifest.repo_root / "CHANGELOG.md"
    releases = parse_changelog(changelog.read_text(encoding="utf-8"))
    if not releases:
        raise ValueError(f"{changelog} contains no '## [version] - date' release headings")
    return csv_bytes(rows_from(releases))


def checkout_commit(repo_root) -> tuple:
    """(commit, date) of the checkout: the id of HEAD and its committer date
    as ISO 8601. Two empty strings where the folder is not a git checkout or
    git is not installed - a downloaded archive has neither."""
    try:
        done = subprocess.run(
            ["git", "-C", str(repo_root), "show", "-s", "--format=%H %cI", "HEAD"],
            capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return "", ""
    parts = done.stdout.split()
    if done.returncode != 0 or len(parts) != 2:
        return "", ""
    return parts[0], parts[1]


def release_info(manifest: ContentManifest) -> dict:
    """What release.json holds: the release, the commit of the checkout it was
    deployed from with that commit's date, and every entry of the manifest
    with its kind and content hash (`entry_hash`)."""
    commit, date = checkout_commit(manifest.repo_root)
    return {
        "kind": RELEASE_INFO_KIND,
        "version": __version__,
        "commit": commit,
        "date": date,
        "entries": [{"path": entry.path, "kind": entry.kind, "hash": entry_hash(manifest, entry)}
                    for entry in manifest.all_entries()],
    }


def release_info_bytes(manifest: ContentManifest) -> bytes:
    return (json.dumps(release_info(manifest), indent=2, ensure_ascii=False) + "\n").encode("utf-8")


#: The files mdb writes itself, by the name a manifest entry gives in `generated:`.
GENERATORS: dict = {
    RELEASE_TABLE: release_table_bytes,
    RELEASE_INFO: release_info_bytes,
}


def release_info_differences(expected: bytes, actual: bytes) -> list:
    """How a release.json differs from the one the checkout gives, by its
    entries: the commit and the date say when a folder was deployed and are
    not compared."""
    try:
        packaged = json.loads(actual.decode("utf-8"))
        listed = {(e.get("path"), e.get("kind")): e.get("hash") for e in packaged.get("entries") or []}
    except (ValueError, AttributeError, TypeError):
        return ["is not a readable release.json"]
    wanted = {(e["path"], e["kind"]): e["hash"] for e in json.loads(expected.decode("utf-8"))["entries"]}
    problems = []
    for key in sorted(set(wanted) - set(listed), key=str):
        problems.append(f"does not list {key[1]} '{key[0]}'")
    for key in sorted(set(listed) - set(wanted), key=str):
        problems.append(f"lists {key[1]} '{key[0]}', which the manifest does not")
    for key in sorted(set(wanted) & set(listed), key=str):
        if wanted[key] != listed[key]:
            problems.append(f"holds another hash for {key[1]} '{key[0]}' than the checkout gives")
    return problems


def generated_differences(entry: Entry, expected: bytes, actual: bytes) -> list:
    """How a generated file differs from what its generator writes now."""
    if entry.generated == RELEASE_INFO:
        return release_info_differences(expected, actual)
    if lf_bytes(expected) != lf_bytes(actual):
        return [f"differs from what the checkout generates ({len(actual)} vs {len(expected)} bytes)"]
    return []
