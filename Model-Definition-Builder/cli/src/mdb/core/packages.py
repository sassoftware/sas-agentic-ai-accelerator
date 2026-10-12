# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Transfer packages: find the environment that built one, before it ships.

THE PROBLEM. A Visual Analytics report keeps its options inside its own
content, so an exported transfer package carries the exporting deployment's
hostname in `viyaHost`, in `SCREndpoint`, and in the Data-Driven Content URL.
Re-exporting reintroduces all of it, every single time, no matter how careful
the last person was.

WHY IT KEEPS GETTING THROUGH. The export stores report content as
``TRUE###<base64 of a zlib stream>``. Only one occurrence - the DDC URL lifted
into ``substitutions`` - is visible as plain text. Every other one is inside
the compressed blob, where neither a plain search, a `grep`, nor a
base64-decoding scan can reach it. A package can look clean in every ordinary
review and still name an internal host seven times. That is exactly what
happened: a sanitisation applied by hand fixed the one visible occurrence and
left six, and they reached a public repository.

WHAT THIS CHECKS, AND WHY IT IS PHRASED BACKWARDS. It does not look for a
known bad hostname - it cannot, because the machine running the check in CI
has no `.env` and no idea which deployment produced the package. It asserts
the opposite and stronger thing: every host a report names must be the
documented placeholder or on a short allowlist of genuinely public addresses.
Any other host is a finding, whoever exported it and whatever it is called.

WHAT ELSE AN EXPORT BRINGS ALONG. A report is exported with every user's saved
view of it (`reportStates`): a full copy of the report per user, with that
user's id. Each copy holds every option default again, so a default reset in
the report alone leaves the exporter's value in the copies. `strip_report_states`
removes them at export, and the check reports any that are left.

THE ONE PACKAGE, AND WHAT IT MUST HOLD. One package is shipped: the content
folder, with exactly the objects `SAS-Viya-Integrations/package-manifest.yaml`
lists (core/content.py). Almost all of it is a second copy of things the
repository holds as source - the UI bundle, the job programs, rag_core, the
custom steps - and an option restore cannot add a label a shipped report
lacks, so the second half of this module does three things with a package and
the manifest:

* `locate` says where each object of a package sits below the content root;
* `filter_package` keeps the listed objects of an export and drops every
  other one with its entries in the package, and every authorization rule and
  share;
* `package_differences` is the content gate: every listed object is in the
  package and equals the checkout, nothing else is in it, and each report
  passes the checks its entry names. One sentence per difference; an empty
  list is a pass.

`reset_report_defaults` writes the neutral option defaults into every copy of
the Prompt Builder report, which is what makes an export from a configured
site shippable without a hand edit.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import content as _content
from .options import LEGACY_REPORTS, MERGED_REPORT, MERGED_REPORT_LABELS, read_options, write_options

#: The host a shipped package is supposed to name. `mdb options-restore`
#: writes the site's real value over it after import.
PLACEHOLDER_HOST = "your-sas-viya-host"

#: Hosts a package may legitimately name: public addresses that are the same
#: for every deployment. Anything else identifies somebody's environment.
#: Add to this only after looking at what the host is actually doing in the
#: report - the point of the check is that it makes someone look.
ALLOWED_HOSTS = frozenset({
    PLACEHOLDER_HOST,
    "sassoftware.github.io",   # the documentation link in the report footer
    "www.sas.com",
    "sas.com",
    # Cited in a calculated item's USER_EDIT_REPRESENTATION comment in the
    # Monitoring Baseline report - "/* Token based prices from
    # https://llmpricecheck.com/ */" - as the source of the per-token prices
    # in the cost expression. Attribution, not a call to anything.
    "llmpricecheck.com",
})

#: A report's content, as the transfer service stores it. FALSE### is the
#: same envelope uncompressed; both are handled so a future export that stops
#: compressing does not silently skip the check.
_ENVELOPE = re.compile(r"^(TRUE|FALSE)###(.*)$", re.DOTALL)

#: Hosts are read from URLs rather than from free text: a bare dotted string
#: could be anything, but the authority of an absolute URL is always a host.
_URL_HOST = re.compile(r"https?://([A-Za-z0-9._-]+(?::\d+)?)")


@dataclass
class Finding:
    """One host a package names that it should not."""

    package: str
    report: str
    host: str
    occurrences: int
    compressed: bool

    @property
    def where(self) -> str:
        return "compressed report content" if self.compressed else "plain text"


@dataclass
class StateFinding:
    """Per-user report states a packaged report still carries."""

    package: str
    report: str
    states: int


@dataclass
class Result:
    findings: list = field(default_factory=list)
    reports_checked: int = 0
    packages_checked: int = 0
    #: StateFinding per report with saved per-user states. Kept apart from
    #: `findings`: a state names no host by itself, so it does not decide `ok`.
    states: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings


def describe_findings(result: Result) -> str:
    """Every host finding of a check as one line of text, for a message that
    has to say what is still wrong and where."""
    return "; ".join(
        f"{f.report}: {f.host} x{f.occurrences} in {f.where}" for f in result.findings)


def inflate(content: str):
    """The report content behind a ``TRUE###``/``FALSE###`` envelope, or None.

    Returns None for anything that is not that envelope, so a caller can pass
    every string in a package and let this decide.
    """
    if not isinstance(content, str):
        return None
    match = _ENVELOPE.match(content.strip())
    if not match:
        return None
    flag, payload = match.group(1), match.group(2)
    try:
        raw = base64.b64decode(payload)
    except (binascii.Error, ValueError):
        return None
    if flag == "FALSE":
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    for window in (15, -15, 47):          # zlib, raw deflate, gzip
        try:
            return zlib.decompress(raw, window).decode("utf-8")
        except (zlib.error, UnicodeDecodeError):
            continue
    return None


def deflate(text: str, flag: str = "TRUE") -> str:
    """The inverse of `inflate`, for writing a rewritten report back."""
    if flag == "FALSE":
        return "FALSE###" + base64.b64encode(text.encode("utf-8")).decode("ascii")
    return "TRUE###" + base64.b64encode(
        zlib.compress(text.encode("utf-8"), 9)).decode("ascii")


def hosts_in(text: str) -> dict:
    """{host: occurrences} for every absolute URL in `text`."""
    found: dict = {}
    for host in _URL_HOST.findall(text or ""):
        bare = host.split(":", 1)[0].lower()
        found[bare] = found.get(bare, 0) + 1
    return found


def _reports(package: dict):
    """(transferObject, summary) for each report in a parsed package."""
    for detail in package.get("transferDetails") or []:
        obj = detail.get("transferObject") or {}
        summary = obj.get("summary") or {}
        if summary.get("type") == "report":
            yield obj, summary


def check_package(path, allowed=ALLOWED_HOSTS) -> Result:
    """Every host the reports in one package name, minus the allowed ones."""
    path = Path(path)
    return check_text(path.read_text(encoding="utf-8"), path.name, allowed)


def check_text(text: str, package_name: str, allowed=ALLOWED_HOSTS) -> Result:
    """`check_package` for a package that is not a file: `mdb package-export`
    asks this of an export before it writes it. `package_name` is the file
    name the findings carry."""
    result = Result(packages_checked=1)
    package = json.loads(text)
    for obj, summary in _reports(package):
        result.reports_checked += 1
        name = summary.get("name") or "(unnamed report)"
        result.findings.extend(report_host_findings(obj, name, package_name, allowed))
        states = len(report_copies(obj)) - 1
        if states > 0:
            result.states.append(StateFinding(package_name, name, states))
    return result


def report_host_findings(obj: dict, name: str, package_name: str = "", allowed=ALLOWED_HOSTS) -> list:
    """Every host one packaged report names that is not allowed."""
    findings = []
    # the substitution map: the one occurrence a plain search would find
    plain = json.dumps(obj.get("substitutions") or {})
    for host, n in hosts_in(plain).items():
        if host not in allowed:
            findings.append(Finding(package_name, name, host, n, compressed=False))
    # and the report itself, which is where the rest of them hide
    inner = inflate(obj.get("content") or "")
    if inner is not None:
        for host, n in hosts_in(inner).items():
            if host not in allowed:
                findings.append(Finding(package_name, name, host, n, compressed=True))
    return findings


def _report_wrapper(obj: dict):
    """The parsed JSON wrapper of a packaged report, or None.

    The transfer service stores a report as `{"object": ..., "transferableContent":
    {"content": "<xml>", "contentVersion": n, "reportStates": [...]}}` behind
    the envelope. Content that inflates to anything else (bare XML, as a test
    or an older export may hold) has no wrapper.
    """
    inner = inflate(obj.get("content") or "")
    if inner is None:
        return None
    try:
        wrapper = json.loads(inner)
    except ValueError:
        return None
    return wrapper if isinstance(wrapper, dict) else None


def report_copies(obj: dict) -> list:
    """Every copy of the report XML one packaged report holds: the report
    itself first, then one per saved per-user state. Empty when the content
    is not a readable report."""
    wrapper = _report_wrapper(obj)
    if wrapper is None:
        inner = inflate(obj.get("content") or "")
        return [inner] if inner else []
    transferable = wrapper.get("transferableContent") or {}
    copies = [transferable.get("content") or ""]
    for state in transferable.get("reportStates") or []:
        copies.append((state or {}).get("content") or "")
    return copies


def strip_report_states(text: str) -> tuple:
    """Remove every per-user report state from a package. Returns (text,
    {report name: states removed}).

    A state is one user's saved view: their id and a full copy of the report.
    Neither belongs in a shipped package, and the copies are why a default
    reset in the report alone is not enough. Like `sanitise_package` this
    swaps the report content token and leaves every other byte alone; the
    wrapper is re-serialised the way the transfer service writes it (compact,
    non-ASCII kept), which reproduces an untouched wrapper byte for byte.
    """
    package = json.loads(text)
    removed: dict = {}
    for obj, summary in _reports(package):
        content = obj.get("content") or ""
        wrapper = _report_wrapper(obj)
        if wrapper is None:
            continue
        transferable = wrapper.get("transferableContent")
        if not isinstance(transferable, dict) or not transferable.get("reportStates"):
            continue
        name = summary.get("name") or "(unnamed report)"
        removed[name] = removed.get(name, 0) + len(transferable["reportStates"])
        transferable["reportStates"] = None
        flag = "FALSE" if content.startswith("FALSE###") else "TRUE"
        token = deflate(json.dumps(wrapper, separators=(",", ":"), ensure_ascii=False), flag)
        if text.count(content) != 1:
            raise ValueError(
                f"report content of '{name}' is not unique in the package, "
                "refusing to edit it blind")
        text = text.replace(content, token)
    json.loads(text)          # still a valid package
    return text, removed


def sanitise_package(path, allowed=ALLOWED_HOSTS) -> tuple:
    """Rewrite every disallowed host to the placeholder. Returns (text, fixed).

    The file is edited as TEXT and the report content token is swapped whole,
    so every other byte the export wrote is preserved: these files are large,
    generated, and reviewed as diffs. Re-serialising the JSON would reformat
    the package and bury the one change that matters.
    """
    path = Path(path)
    return sanitise_text(path.read_text(encoding="utf-8"), path.name, allowed)


def sanitise_text(text: str, package_name: str, allowed=ALLOWED_HOSTS) -> tuple:
    """`sanitise_package` for a package that is not a file. `package_name` is
    the file name an error carries."""
    package = json.loads(text)
    fixed: dict = {}

    for obj, _summary in _reports(package):
        content = obj.get("content") or ""
        inner = inflate(content)
        if inner is None:
            continue
        offenders = {h for h in hosts_in(inner) if h not in allowed}
        if offenders:
            rewritten = inner
            for host in offenders:
                rewritten = rewritten.replace(host, PLACEHOLDER_HOST)
                fixed[host] = fixed.get(host, 0) + inner.count(host)
            flag = "FALSE" if content.startswith("FALSE###") else "TRUE"
            token = deflate(rewritten, flag)
            # the token is base64 and unique in the file, so a literal swap is
            # exact; assert that rather than trust it
            if text.count(content) != 1:
                raise ValueError(
                    f"{package_name}: report content is not unique in the file, "
                    "refusing to edit it blind")
            text = text.replace(content, token)

    # the plain-text substitutions, in whatever is left
    for obj, _summary in _reports(json.loads(text)):
        for host, n in hosts_in(json.dumps(obj.get("substitutions") or {})).items():
            if host not in allowed:
                text = text.replace(host, PLACEHOLDER_HOST)
                fixed[host] = fixed.get(host, 0) + n

    json.loads(text)          # still a valid package
    return text, fixed


def default_packages(repo_root) -> list:
    """Every transfer package file in SAS-Viya-Integrations/, whatever its
    name: what the host check is asked of, so that no package file sits in
    the repository unchecked."""
    folder = Path(repo_root) / "SAS-Viya-Integrations"
    return sorted(p for p in folder.glob("*.json") if p.is_file()) \
        if folder.is_dir() else []


# ---------------------------------------------------------------------------
# The Prompt Builder report: its option labels and its neutral defaults
# ---------------------------------------------------------------------------

#: Objects only a package exported before 2.3.0 holds: the report and the job
#: definition of the standalone RAG UI. Importing them brings that UI back.
LEGACY_OBJECTS = tuple(
    [("report", name) for name in sorted(LEGACY_REPORTS)]
    + [("jobDefinition", "RAG-Builder-UI")])

#: What a shipped Prompt Builder report must hold in every copy: no ids of the
#: exporting environment, the optional parts off, the default part on, the
#: vector-store TLS mode the documentation names, and for every other option
#: an exporting site is likely to have tuned the default the UI and the
#: documentation name. `modelCardReportURI` is a report id of the exporting
#: site written as a relative URI, which the host check cannot see.
#: `sslVerify` is on: a package exported from a site that switched TLS
#: verification off for its manifested models must not switch it off for
#: every site that imports the package.
#:
#: `mdb package-export` writes these values into the report it exports
#: (`reset_report_defaults`), and `mdb package-check` asks for them.
#:
#: Not listed: `viyaHost` and `SCREndpoint` (the host check owns them), and
#: `computeContext` / `ragComputeContext`, whose shipped value is decided when
#: the report is saved with the merged UI.
NEUTRAL_DEFAULTS = {
    "modelRepositoryID": "",
    "llmProjectID": "",
    "embeddingProjectID": "",
    "enableDecision": "true",
    "enableOptimization": "false",
    "enableRag": "false",
    "storeSslmode": "prefer",
    "judgeModel": "",
    "modelCardReportURI": "",
    "minOptimizeSamples": "30",
    "deploymentType": "k8s",
    "credentialDomain": "agentic-ai-keys",
    "sslVerify": "true",
    "contentRoot": "/SAS Agentic AI Accelerator/RAG",
    "casServer": "cas-shared-default",
}


def _object_kind(summary: dict) -> str:
    """An object's type as one word. Content files, rules and shares carry
    their media type (application/vnd.sas.file+json, ...authorization.rule+json);
    the rest a plain name (report, folder, flowStep, jobDefinition)."""
    kind = summary.get("type") or "?"
    if "/" in kind:
        kind = kind.rsplit(".", 1)[-1].replace("+json", "")
    return kind


def package_objects(package: dict) -> list:
    """(type, name) of every object a package carries."""
    objects = []
    for detail in package.get("transferDetails") or []:
        summary = (detail.get("transferObject") or {}).get("summary") or {}
        objects.append((_object_kind(summary), summary.get("name") or "?"))
    return objects


def legacy_objects(package: dict) -> list:
    """The LEGACY_OBJECTS a package carries, as 'type name'. Not empty means
    the package was exported before 2.3.0."""
    present = set(package_objects(package))
    return [f"{kind} {name}" for kind, name in LEGACY_OBJECTS if (kind, name) in present]


def _find_object(package: dict, kind: str, name: str):
    for detail in package.get("transferDetails") or []:
        obj = detail.get("transferObject") or {}
        summary = obj.get("summary") or {}
        if _object_kind(summary) == kind and summary.get("name") == name:
            return obj
    return None


def _body(obj: dict):
    """The JSON body of a packaged object that is not a report: base64 of the
    object's own representation (a job definition, a step, a file wrapper)."""
    try:
        body = json.loads(base64.b64decode(obj.get("content") or ""))
    except (binascii.Error, ValueError):
        return None
    return body if isinstance(body, dict) else None


def report_option_copies(package: dict, report: str = MERGED_REPORT) -> list:
    """{label: default} for every copy of one packaged report - the report
    first, then each per-user state. Empty when the package has no such
    report."""
    obj = _find_object(package, "report", report)
    return [read_options(copy) for copy in report_copies(obj)] if obj else []


def report_labels(package: dict) -> dict:
    """{report name: set of option labels} for every report a package
    carries - what a site's report will hold once the package is imported."""
    labels: dict = {}
    for obj, summary in _reports(package):
        copies = report_copies(obj)
        labels[summary.get("name") or "(unnamed report)"] = set(read_options(copies[0])) if copies else set()
    return labels


def _copy_name(index: int) -> str:
    return "the report" if index == 0 else f"report state {index}"


def missing_option_labels(package: dict, required, report: str = MERGED_REPORT) -> list:
    """Problems when a packaged report lacks an option label it must hold.

    `mdb options-restore` cannot add a label, so a report shipped without one
    leaves that option at nothing on every site, with a 'not an option of this
    report version' line on every import.
    """
    copies = report_option_copies(package, report)
    if not copies:
        return [f"the package holds no report named '{report}'"]
    problems = []
    for index, options in enumerate(copies):
        lacking = [label for label in required if label not in options]
        if lacking:
            problems.append(f"{_copy_name(index)} lacks {len(lacking)} option label(s): "
                            + ", ".join(lacking))
    return problems


def non_neutral_defaults(package: dict, expected=None, report: str = MERGED_REPORT) -> list:
    """Problems when an option default of a packaged report is not the neutral
    one, in the report or in any per-user state.

    Only labels the copy holds are judged - whether a label is there at all is
    `missing_option_labels`' question - so this can be asked of a report from
    before an option existed.
    """
    expected = NEUTRAL_DEFAULTS if expected is None else expected
    copies = report_option_copies(package, report)
    if not copies:
        return [f"the package holds no report named '{report}'"]
    problems = []
    for index, options in enumerate(copies):
        for label, wanted in expected.items():
            if label in options and options[label] != wanted:
                problems.append(f"{_copy_name(index)}: {label} is '{options[label]}', "
                                f"expected '{wanted}'")
    return problems


def reset_report_defaults(text: str, defaults=None, report: str = MERGED_REPORT) -> tuple:
    """Write the neutral option defaults into EVERY copy of a packaged report:
    the report itself and each per-user state. Returns (text, {copy: labels
    that were reset}) - 'the report', 'report state 1', ...

    The Prompt Builder report of a configured site holds that site's values as
    its defaults: repository and project ids, a judge model, RAG switched on.
    None of them belongs in a shipped package, and a reset of the report alone
    leaves them in the copies the states hold. Only labels a copy holds are
    written (`write_options` never inserts one), every other part of the
    report is left alone, and like the host rewrite this swaps the report
    content token and no other byte of the package.
    """
    defaults = NEUTRAL_DEFAULTS if defaults is None else defaults
    package = json.loads(text)
    reset: dict = {}
    for obj, summary in _reports(package):
        if summary.get("name") != report:
            continue
        content = obj.get("content") or ""
        flag = "FALSE" if content.startswith("FALSE###") else "TRUE"
        wrapper = _report_wrapper(obj)
        if wrapper is None:
            inner = inflate(content)
            if inner is None:
                continue
            updated, result = write_options(inner, defaults)
            if not result.applied:
                continue
            reset[_copy_name(0)] = sorted(result.applied)
            token = deflate(updated, flag)
        else:
            transferable = wrapper.get("transferableContent")
            if not isinstance(transferable, dict):
                continue
            holders = [transferable] + list(transferable.get("reportStates") or [])
            changed = False
            for index, holder in enumerate(holders):
                if not isinstance(holder, dict) or not holder.get("content"):
                    continue
                updated, result = write_options(holder["content"], defaults)
                if result.applied:
                    holder["content"] = updated
                    reset[_copy_name(index)] = sorted(result.applied)
                    changed = True
            if not changed:
                continue
            token = deflate(json.dumps(wrapper, separators=(",", ":"), ensure_ascii=False), flag)
        if text.count(content) != 1:
            raise ValueError(
                f"report content of '{report}' is not unique in the package, refusing to edit it blind")
        text = text.replace(content, token)
    json.loads(text)          # still a valid package
    return text, reset


# ---------------------------------------------------------------------------
# Where the objects of a package sit
# ---------------------------------------------------------------------------

#: The word a package has for each kind of the manifest.
PACKAGE_KIND = {"folder": "folder", "file": "file", "step": "flowStep",
                "jobDefinition": "jobDefinition", "report": "report"}
MANIFEST_KIND = {word: kind for kind, word in PACKAGE_KIND.items()}

#: Objects that grant something to somebody: never part of a shipped package.
GRANT_KINDS = ("rule", "share")

_URI_PREFIX = {"folder": "/folders/folders/", "file": "/files/files/", "flowStep": "/dataFlows/steps/",
               "jobDefinition": "/jobDefinitions/definitions/", "report": "/reports/reports/"}


def object_uri(summary: dict) -> str:
    """The URI of the object a transfer object carries: its summary's own
    `self` link, or the URI its type and id make. That is what the package's
    requested items and connectors name - not the id of the transfer object."""
    for link in summary.get("links") or []:
        if isinstance(link, dict) and link.get("rel") == "self" and link.get("uri"):
            return link["uri"]
    prefix = _URI_PREFIX.get(_object_kind(summary))
    return f"{prefix}{summary['id']}" if prefix and summary.get("id") else ""


def _connectors(detail: dict) -> list:
    """Every connector of one object: the ones the package lists beside the
    transfer object and the ones inside it."""
    obj = detail.get("transferObject") or {}
    return [c for c in list(detail.get("connectors") or []) + list(obj.get("connectors") or [])
            if isinstance(c, dict)]


def _connected(detail: dict, kind: str) -> Optional[str]:
    return next((c.get("uri") for c in _connectors(detail) if c.get("type") == kind and c.get("uri")), None)


@dataclass
class Located:
    """One object of a package and where it sits."""

    detail: dict
    kind: str            # the package's word: folder, file, flowStep, jobDefinition, report, rule, share
    name: str
    uri: str
    #: Path below the content root; '' for the root folder itself; None for an
    #: object that is not below it.
    path: Optional[str] = None
    #: For a file that belongs to a report (an image): the path of that report.
    owner: Optional[str] = None

    @property
    def obj(self) -> dict:
        return self.detail.get("transferObject") or {}

    @property
    def shown(self) -> str:
        return f"{self.kind} '{self.path if self.path else self.name}'"


def locate(package: dict, root_uri: Optional[str] = None) -> tuple:
    """(root uri, [Located]) for a parsed package.

    An object's place is read from the package's own connectors: a
    `parentFolder` connector names the folder it sits in, a `parent` connector
    the report an image belongs to. Walking the folders up to the content root
    gives the path below it, whatever the root is called and wherever it sits -
    a test root works like `/SAS Agentic AI Accelerator`.

    `root_uri` is the folder that was exported (`/folders/folders/<id>`).
    Without it the root is the one folder of the package that has no parent in
    the package. Where several have none, the root is the folder they all sit
    in when there is one (a package without an object for its root folder),
    else the one with the most objects below.
    """
    items = []
    for detail in package.get("transferDetails") or []:
        summary = (detail.get("transferObject") or {}).get("summary") or {}
        items.append(Located(detail=detail, kind=_object_kind(summary), name=summary.get("name") or "?",
                             uri=object_uri(summary)))
    folders = {item.uri: item for item in items if item.kind == "folder" and item.uri}
    parents = {item.uri: _connected(item.detail, "parentFolder") for item in folders.values()}

    def chain(uri) -> list:
        """The folders from `uri` up to the top one, as uris."""
        walked = []
        while uri in folders and uri not in walked:
            walked.append(uri)
            uri = parents.get(uri)
        return walked

    if root_uri is None:
        tops = [uri for uri in folders if parents.get(uri) not in folders]
        above = {parents.get(uri) for uri in tops}
        if len(tops) == 1:
            root_uri = tops[0]
        elif tops and len(above) == 1 and None not in above:
            root_uri = above.pop()
        elif tops:
            below = {top: 0 for top in tops}
            for item in items:
                start = item.uri if item.kind == "folder" else _connected(item.detail, "parentFolder")
                walked = chain(start)
                if walked and walked[-1] in below:
                    below[walked[-1]] += 1
            root_uri = max(tops, key=lambda top: below[top])

    def folder_path(uri) -> Optional[str]:
        """The path of a folder below the root: '' for the root itself -
        whether or not the package holds an object for it - and None for a
        folder the root is not above."""
        names, walked = [], set()
        while uri is not None and uri not in walked:
            if uri == root_uri:
                return "/".join(reversed(names))
            if uri not in folders:
                return None
            walked.add(uri)
            names.append(folders[uri].name)
            uri = parents.get(uri)
        return None

    by_uri = {item.uri: item for item in items if item.uri}
    for item in items:
        if item.kind in GRANT_KINDS:
            continue
        if item.kind == "folder":
            item.path = folder_path(item.uri)
            continue
        folder = _connected(item.detail, "parentFolder")
        if folder is not None:
            base = folder_path(folder)
            item.path = None if base is None else (f"{base}/{item.name}" if base else item.name)
    # A file that belongs to a report sits in no folder. It says so itself (a
    # `parent` connector), or only the report does: an image the report shows
    # is the target of one of the report's own connectors.
    for item in items:
        if item.path is None and item.kind == "file":
            owner = by_uri.get(_connected(item.detail, "parent") or "")
            if owner is not None and owner.kind == "report" and owner.path is not None:
                item.owner = owner.path
    for report in items:
        if report.kind != "report" or report.path is None:
            continue
        for connector in _connectors(report.detail):
            target = by_uri.get(connector.get("uri") or "")
            if target is not None and target.kind == "file" and target.path is None and target.owner is None:
                target.owner = report.path
    return root_uri, items


# ---------------------------------------------------------------------------
# Keeping what the manifest lists
# ---------------------------------------------------------------------------
@dataclass
class FilterResult:
    #: 'kind path' of what stayed, what was dropped, and what the never-ship
    #: rule refused although the manifest lists it.
    kept: list = field(default_factory=list)
    dropped: list = field(default_factory=list)
    refused: list = field(default_factory=list)


def _listed(manifest, item: Located):
    """The manifest entry of a located object, or None."""
    kind = MANIFEST_KIND.get(item.kind)
    if kind is None or item.path is None or item.path == "":
        return None
    return manifest.find(item.path, kind)


def filter_package(package: dict, manifest, root_uri: Optional[str] = None) -> FilterResult:
    """Reduce a parsed package to what the manifest lists; returns what
    happened to each object.

    Kept: the content root folder, every object whose path and kind the
    manifest lists (the folders of the listed objects included), and the files
    that belong to a listed report. Dropped: everything else - an object that
    is not listed, an object that is not below the content root (a dependency
    the export pulled in, a folder above the root), and every authorization
    rule and share, whatever it is a rule for. A dropped object goes with its
    entry in the package's requested items and with its mention in the
    connectors of the objects that stay, so nothing in the package names it
    any more.

    The never-ship rule (core/content.py) is asked of every object that would
    be kept: one on such a path is dropped and returned in `refused`, listed
    or not.
    """
    result = FilterResult()
    _root, items = locate(package, root_uri)
    listed_reports = {item.path for item in items
                      if item.kind == "report" and _listed(manifest, item) is not None
                      and not _content.never_ship(item.path)}
    kept: list = []
    for item in items:
        if item.kind in GRANT_KINDS:
            result.dropped.append(f"{item.kind} '{item.name}'")
            continue
        if item.path == "":
            kept.append(item)
            continue
        if item.owner is not None:
            if item.owner in listed_reports:
                kept.append(item)
                result.kept.append(f"file '{item.owner}/{item.name}' (belongs to the report)")
            else:
                result.dropped.append(f"file '{item.name}' (belongs to report '{item.owner}')")
            continue
        if _listed(manifest, item) is None:
            result.dropped.append(item.shown if item.path is not None
                                  else f"{item.kind} '{item.name}' (not below the content root)")
            continue
        if _content.never_ship(item.path):
            result.refused.append(f"{item.shown}: {_content.never_ship(item.path)}")
            continue
        kept.append(item)
        result.kept.append(item.shown)

    kept_details = [item.detail for item in kept]
    gone = {item.uri for item in items if item.uri} - {item.uri for item in kept if item.uri}
    for detail in kept_details:
        for connector in _connectors(detail):
            related = connector.get("relatedUris")
            if isinstance(related, list):
                connector["relatedUris"] = [uri for uri in related if uri not in gone]
    package["transferDetails"] = kept_details
    if isinstance(package.get("requestedItems"), list):
        here = {item.uri for item in kept if item.uri}
        package["requestedItems"] = [uri for uri in package["requestedItems"] if uri in here]
    if isinstance(package.get("transferObjectCount"), int):
        package["transferObjectCount"] = len(kept_details)
    return result


# ---------------------------------------------------------------------------
# The content gate
# ---------------------------------------------------------------------------
def _table_names(obj: dict) -> list:
    """The CAS tables a packaged report binds, by name."""
    names = []
    for connector in obj.get("connectors") or []:
        if isinstance(connector, dict) and connector.get("type") == "table":
            spec = dict(part.split("=", 1) for part in str(connector.get("uri") or "").split(";") if "=" in part)
            if spec.get("table"):
                names.append(spec["table"])
    return names


def _report_differences(item: Located, entry, in_package: set, package_name: str = "") -> list:
    """What a packaged report does not pass: the checks every report gets
    (no host beyond the allowed ones, no per-user state, nothing it depends
    on left out of the package) and the ones its manifest entry names (an
    option set, the tables it binds). `in_package` is the uris of the
    package's objects."""
    obj, where = item.obj, f"report '{item.path}'"
    problems = [f"{where} names the host {f.host} ({f.occurrences} time(s), in {f.where})"
                for f in report_host_findings(obj, item.name, package_name)]
    lacking = sorted({str(c.get("uri")) for c in obj.get("connectors") or []
                      if isinstance(c, dict) and str(c.get("uri") or "").startswith("/")
                      and c.get("uri") not in in_package})
    problems += [f"{where} depends on {uri}, which is not in the package" for uri in lacking]
    copies = report_copies(obj)
    if not copies:
        return problems + [f"{where}: the packaged content is unreadable"]
    if len(copies) > 1:
        problems.append(f"{where} carries {len(copies) - 1} per-user report state(s)")
    checks = entry.checks or {}
    if checks.get("optionSet") == "prompt-builder":
        for index, copy in enumerate(copies):
            options = read_options(copy)
            lacking = [label for label in MERGED_REPORT_LABELS if label not in options]
            if lacking:
                problems.append(f"{where}: {_copy_name(index)} lacks {len(lacking)} option label(s): "
                                + ", ".join(lacking))
            for label, wanted in NEUTRAL_DEFAULTS.items():
                if label in options and options[label] != wanted:
                    problems.append(f"{where}: {_copy_name(index)}: {label} is '{options[label]}', "
                                    f"expected '{wanted}'")
    if "tables" in checks:
        bound = {name.upper() for name in _table_names(obj)}
        wanted = {name.upper() for name in checks["tables"]}
        for name in sorted(wanted - bound):
            problems.append(f"{where} does not bind the table {name}")
        for name in sorted(bound - wanted):
            problems.append(f"{where} binds the table {name}, which its manifest entry does not name")
    return problems


def _object_differences(item: Located, entry, manifest) -> list:
    """How a packaged file, step or job definition differs from the checkout."""
    body = _body(item.obj)
    if body is None:
        return [f"{item.shown}: the packaged content is unreadable"]
    if entry.kind == "file":
        try:
            packaged = base64.b64decode(body.get("content") or "")
        except (binascii.Error, ValueError):
            return [f"{item.shown}: the packaged content is unreadable"]
        expected = _content.file_bytes(manifest, entry)
        if entry.generated:
            return [f"file '{item.path}' {problem}"
                    for problem in _content.generated_differences(entry, expected, packaged)]
        if _content.lf_bytes(packaged) != _content.lf_bytes(expected):
            return [f"file '{item.path}' differs from {entry.source} "
                    f"({len(_content.lf_bytes(packaged))} vs {len(_content.lf_bytes(expected))} bytes)"]
        return []
    if entry.kind == "step":
        return [f"step '{item.path}': '{part}' differs from {entry.source}"
                for part in _content.step_differences(_content.step_body(manifest, entry), body)]
    sources = {"code": entry.code or "the code of a job definition that only serves a form",
               "form": entry.form or "no form", "parameters": "the parameters of its manifest entry"}
    return [f"job definition '{item.path}': '{part}' differs from {sources[part]}"
            for part in _content.job_differences(_content.job_definition_body(manifest, entry), body)]


def package_differences(package: dict, manifest, package_name: str = "") -> list:
    """The content gate: every difference between a package and the checkout,
    one sentence each. An empty list means the package is the checkout's
    content and nothing else:

    * every entry of the manifest is in the package, at its path;
    * a file equals the repository file (line endings aside), a generated file
      what its generator writes now (release.json by its entries), a step the
      .step file in `templates`, `ui` and `flowMetadata`, a job definition its
      program, its form and its parameters;
    * a report passes its checks;
    * no object is in the package that the manifest does not list, none sits
      on a path that never ships, and there is no authorization rule or share.
    """
    root, items = locate(package)
    if root is None:
        return ["the package holds no folder, so its objects have no place below a content root"]
    problems: list = []
    seen: set = set()
    listed_reports = {item.path for item in items if item.kind == "report" and _listed(manifest, item) is not None}
    in_package = {item.uri for item in items if item.uri}
    for item in items:
        if item.kind in GRANT_KINDS:
            problems.append(f"the package carries the authorization {item.kind} '{item.name}'")
            continue
        if item.path == "":
            continue
        if item.owner is not None:
            if item.owner not in listed_reports:
                problems.append(f"file '{item.name}' belongs to report '{item.owner}', which is not in the manifest")
            continue
        if item.path is None:
            problems.append(f"{item.kind} '{item.name}' is in the package but not below its content root")
            continue
        entry = _listed(manifest, item)
        if entry is None:
            problems.append(f"{item.shown} is in the package but not in the manifest")
            continue
        seen.add((entry.kind, entry.path))
        forbidden = _content.never_ship(item.path)
        if forbidden:
            problems.append(f"{item.shown}: {forbidden} and never ships")
            continue
        if entry.kind == "report":
            problems.extend(_report_differences(item, entry, in_package, package_name))
        elif entry.kind != "folder":
            try:
                problems.extend(_object_differences(item, entry, manifest))
            except (OSError, ValueError) as exc:
                problems.append(f"{item.shown}: cannot be compared with the checkout ({exc})")
    for entry in manifest.all_entries():
        if (entry.kind, entry.path) not in seen:
            problems.append(f"{entry.kind} '{entry.path}' is in the manifest but not in the package")
    return problems

