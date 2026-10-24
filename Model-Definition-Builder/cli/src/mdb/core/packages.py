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
* `package_findings` is the content gate: every listed object is in the
  package and equals the checkout, nothing else is in it, and each report
  passes the checks its entry names. One Difference per sentence, each
  marked as `drift` (content that is behind the checkout - what happens to a
  package between two exports) or not (what a package must never be);
  `package_differences` gives all of them as sentences, and an empty list is
  the pass a release asks for.

`reset_report_defaults` writes the neutral option defaults into every copy of
the Prompt Builder report, which is what makes an export from a configured
site shippable without a hand edit.

THE PACKAGE AS A SOURCE. An import does not hand the whole package to the
transfer service: that service was measured to leave a file alone that exists
in the target folder under the same name with another id (the package's file
then sits in no folder, and the job still ends completed), and to put a
package whose root folder is not at the root of SAS Content into a new folder
there. So `PackageSource` reads the files, custom steps and job definitions
out of a package for the in-place write path of viya/content.py, and
`reports_package` cuts out what is left for the transfer service: the
reports, the files that are their children, and the folders they sit in.
`package_root_path` says which folder a package was exported from, which
decides whether its reports can be imported at all.
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


def earlier_packages_present(repo_root, release_file) -> list:
    """The package files in SAS-Viya-Integrations/ other than the one of the
    release (`release_file`): the packages of the releases up to 2.2.x, which
    the commit that adds the one package of 2.3.0 removes. While one of them
    is in a checkout, the package of the release may not be there yet. No
    name of an earlier file is written here: any other package file counts."""
    release = Path(release_file).resolve()
    return [path for path in default_packages(repo_root) if path.resolve() != release]


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
    # where the folder picker of a RAG setup starts: a folder of the exporting
    # site, which every other site would have to change - a site decides
    "ragServerFolder": "",
    "ragServerBrowseAbove": "false",
    "ragContentFolder": "",
    "ragContentBrowseAbove": "false",
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
                "jobDefinition": "jobDefinition", "report": "report", "decision": "decision"}
MANIFEST_KIND = {word: kind for kind, word in PACKAGE_KIND.items()}

#: The word a package has for a custom node type of SAS Intelligent
#: Decisioning. It sits in no folder: an export of a folder carries it as a
#: dependency of the decision that uses it, with its id, and the decision
#: names it by a `dntStatic` connector (read from an export of 2026-10-20).
NODE_TYPE_KIND = "decisionNodeType"
NODE_TYPE_URI = "/decisions/decisionNodeTypes/"

#: Objects that grant something to somebody: never part of a shipped package.
GRANT_KINDS = ("rule", "share")

_URI_PREFIX = {"folder": "/folders/folders/", "file": "/files/files/", "flowStep": "/dataFlows/steps/",
               "jobDefinition": "/jobDefinitions/definitions/", "report": "/reports/reports/",
               "decision": "/decisions/flows/", NODE_TYPE_KIND: NODE_TYPE_URI}


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
    #: For a custom node type: the path of the first decision that uses it.
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
    Without it the root is the folder the package itself says was exported:
    its one requested item, where that is a folder of the package - an
    export made in SAS Environment Manager from a folder below others holds
    the folders above it too, and the topmost of them is not the content.
    Where the package does not say, the root is the one folder that has no
    parent in the package; where several have none, the folder they all sit
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

    requested = package.get("requestedItems")
    if root_uri is None and isinstance(requested, list) and len(requested) == 1 and requested[0] in folders:
        root_uri = requested[0]
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
    # A file that belongs to a report sits in no folder: it is a CHILD of the
    # report, and says so itself - by a `parent` connector, or by the
    # `parentUri` of its file resource. That a report names a file (the
    # target of one of its `image` connectors) does not make the file its
    # child: such a file can sit in any folder and hold anything.
    for item in items:
        if item.path is None and item.kind == "file":
            owner = by_uri.get(_file_parent(item) or "")
            if owner is not None and owner.kind == "report" and owner.path is not None:
                item.owner = owner.path
    # A custom node type sits in no folder either: it belongs to the decisions
    # below the root that use it - a decision names it by a `dntStatic`
    # connector, and a step of the decision by its address.
    users = node_type_users(items)
    for item in items:
        if item.kind == NODE_TYPE_KIND and item.path is None and users.get(item.uri):
            item.owner = users[item.uri][0].path
    return root_uri, items


def custom_objects(value) -> list:
    """Every `customObject` of the steps of a decision version (or of any
    part of one): the node a step calls, `{"name", "type", "uri"}` - nested
    in a branch or not. A step whose node type was deleted keeps the name and
    loses the uri (measured on 2026-10-20)."""
    found: list = []

    def walk(part) -> None:
        if isinstance(part, dict):
            custom = part.get("customObject")
            if isinstance(custom, dict):
                found.append(custom)
            for key, item in part.items():
                if key != "customObject":
                    walk(item)
        elif isinstance(part, list):
            for item in part:
                walk(item)
    walk(value)
    return found


def node_type_uris(value) -> list:
    """The addresses of the custom node types the steps in `value` call, each once."""
    uris: list = []
    for custom in custom_objects(value):
        uri = str(custom.get("uri") or "")
        if uri.startswith(NODE_TYPE_URI) and uri not in uris:
            uris.append(uri)
    return uris


def _decision_connected(item: "Located") -> list:
    """The node types a packaged decision names by its `dntStatic` connectors."""
    return [str(c.get("uri")) for c in _connectors(item.detail) if c.get("type") == "dntStatic" and c.get("uri")]


def node_type_users(items: list) -> dict:
    """{node type uri: [the located decisions below the root that use it]}:
    by a connector, or by a step of any of their versions."""
    users: dict = {}
    for item in items:
        if item.kind != "decision" or item.path is None:
            continue
        uris = _decision_connected(item) + node_type_uris(_body(item.obj) or {})
        for uri in dict.fromkeys(uris):
            users.setdefault(uri, []).append(item)
    return users


def node_type_id(item: "Located") -> str:
    """The id a packaged node type has: the last part of its address."""
    return item.uri.rstrip("/").rsplit("/", 1)[-1] if item.uri else ""


def _file_parent(item: "Located") -> Optional[str]:
    """The URI of the object a packaged file says it is a child of."""
    named = _connected(item.detail, "parent")
    if named:
        return named
    resource = (_body(item.obj) or {}).get("fileResource")
    return (resource.get("parentUri") or None) if isinstance(resource, dict) else None


def package_root_path(package: dict, root_uri: Optional[str] = None) -> Optional[str]:
    """The SAS Content path of the folder a package was exported from, or
    None where the package does not say.

    Read from the object of the root folder. A folder at the root of SAS
    Content has no parent: its body names no `parentFolderUri` and the export
    gives it no `contentSourceLocation` - so its path is its name. A folder
    below others carries the path of its parent in `contentSourceLocation`.
    (Both read from the packages of the releases up to 2.2.x and from an
    export of a folder below a user's My Folder.)"""
    _root, items = locate(package, root_uri)
    folder = next((item for item in items if item.kind == "folder" and item.path == ""), None)
    if folder is None:
        return None
    body = (_body(folder.obj) or {}).get("folder")
    nested = bool(isinstance(body, dict) and body.get("parentFolderUri")) \
        or _connected(folder.detail, "parentFolder") is not None
    above = folder.obj.get("contentSourceLocation")
    if not nested and not above:
        return f"/{folder.name}"
    if isinstance(above, str) and above.startswith("/"):
        return f"{above.rstrip('/')}/{folder.name}"
    return None


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
    manifest lists (the folders of the listed objects included), the files
    that are children of a listed report and that its entry lists under
    `files:`, and the custom node types a listed decision uses and its entry
    lists under `nodeTypes:` by their id. Dropped: everything else - an object that is not listed, an
    object that is not below the content root (a dependency the export pulled
    in, a folder above the root, a picture a report shows from a folder of
    somebody's), a child of a report that the report's entry does not list,
    and every authorization rule and share, whatever it is a rule for. A
    dropped object goes with its entry in the package's requested items and
    with its mention in the connectors of the objects that stay, so nothing
    in the package names it any more.

    The never-ship rule (core/content.py) is asked of every object that would
    be kept: one on such a path is dropped and returned in `refused`, listed
    or not.
    """
    result = FilterResult()
    _root, items = locate(package, root_uri)
    listed_reports = {item.path: _listed(manifest, item) for item in items
                      if item.kind == "report" and _listed(manifest, item) is not None
                      and not _content.never_ship(item.path)}
    listed_decisions = {item.path: _listed(manifest, item) for item in items
                        if item.kind == "decision" and _listed(manifest, item) is not None
                        and not _content.never_ship(item.path)}
    users = node_type_users(items)
    kept: list = []
    for item in items:
        if item.kind in GRANT_KINDS:
            result.dropped.append(f"{item.kind} '{item.name}'")
            continue
        if item.path == "":
            kept.append(item)
            continue
        if item.kind == NODE_TYPE_KIND and item.owner is not None:
            # kept for a listed decision that uses it and lists it - by its id
            wanted = [user.path for user in users.get(item.uri, []) if user.path in listed_decisions
                      and node_type_id(item) in {n["id"] for n in listed_decisions[user.path].node_types}]
            if wanted:
                kept.append(item)
                result.kept.append(f"node type '{item.name}' ({node_type_id(item)}, used by decision '{wanted[0]}')")
            else:
                result.dropped.append(f"node type '{item.name}' ({node_type_id(item)}) - no decision the manifest "
                                      "lists uses it under this id")
            continue
        if item.owner is not None:
            if item.owner not in listed_reports:
                result.dropped.append(f"file '{item.name}' (belongs to report '{item.owner}')")
            elif item.name not in {listed["name"] for listed in listed_reports[item.owner].files}:
                result.dropped.append(f"file '{item.name}' (belongs to report '{item.owner}', whose manifest "
                                      "entry does not list it)")
            else:
                kept.append(item)
                result.kept.append(f"file '{item.owner}/{item.name}' (belongs to the report)")
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
    _reduce(package, items, kept)
    return result


def _reduce(package: dict, items: list, kept: list) -> None:
    """Make a parsed package hold `kept` of its `items` and nothing that
    names another object: the requested items, the count, and the related
    URIs of the connectors that stay."""
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


# ---------------------------------------------------------------------------
# A package as the source of an import
# ---------------------------------------------------------------------------
#: Where the content folder (and its package) holds the release table as CSV.
RELEASE_TABLE_PATH = "Data/ACCELERATOR_RELEASES.csv"

#: What a registered step carries that belongs to the system it was read
#: from, or that the service writes itself: not part of a step that is
#: registered somewhere else.
_STEP_INSTANCE_KEYS = ("id", "links", "version", "eTag", "createdBy", "modifiedBy", "creationTimeStamp",
                       "modifiedTimeStamp", "localDisplayName", "localDescription", "localUi")
_PARAMETER_KEYS = ("version", "name", "defaultValue", "type", "label", "required")


def _name_problem(name) -> Optional[str]:
    """Why a name cannot be one part of a path below the content root, or
    None: the rule the content manifest has for each part of a path."""
    text = str(name)
    if not text.strip():
        return "is empty"
    if text in (".", ".."):
        return f"is '{text}'"
    if "/" in text or "\\" in text:
        return "holds a slash"
    if text != text.strip():
        return "starts or ends with a blank"
    return None


def _path_check(items: list):
    """A function that says why the path of a located object cannot be
    taken (or None): its own name, or the name of a folder above it, is not
    a name a path can hold. A folder of such a name takes everything below
    it along - nothing is created inside a folder named '..'."""
    bad_folders = {item.path: _name_problem(item.name) for item in items
                   if item.kind == "folder" and item.path and _name_problem(item.name)}

    def check(item) -> Optional[str]:
        own = _name_problem(item.name)
        if own:
            return f"its name {own}"
        for folder, problem in bad_folders.items():
            if item.path and item.path != folder and item.path.startswith(folder + "/"):
                return f"the name of the folder '{folder}' above it {problem}"
        return None
    return check


class PackageSource:
    """What a transfer package holds for each file, custom step and job
    definition - the source `mdb builders-import` hands to the write path of
    `mdb content-deploy` (viya/content.py), so that these objects reach a
    site IN PLACE and not through the transfer service.

    How the package holds each (read from exported packages):

    * a file: the base64 of `{"fileResource": {...}, "content": "<base64 of
      the file's bytes>"}` - the bytes are taken as they are;
    * a custom step: the base64 of the step as the dataFlows service gives
      it - `templates`, `ui`, `flowMetadata`, name and description, plus the
      id, the links and the user of the exporting system, which are left out;
    * a job definition: the base64 of the definition - `code`, `parameters`,
      and the form as the property `form`.

    The entries are the package's own: every such object below its root
    folder, at the path the package gives it - the manifest of the checkout
    does not limit them, the never-ship rule and the path rule do. An empty
    folder of the package is an entry too, and is created. Not brought this
    way, and named in `left_out` with the reason: an authorization rule or
    share, an object that is not below the root folder, an object of a kind
    mdb does not import, an object on a path that never ships, and an object
    whose name - or the name of a folder above it - cannot be a part of a
    path (empty, '.', '..', a slash in it, a blank at either end: the rule
    the manifest has for a path). Reports and the files that are children of
    a report are `reports` / `children`: they go through the transfer
    service or not at all. So do the decisions and the custom node types
    they use (`decisions` / `node_types`), in an import job of their own; a
    node type no imported decision uses is left out. `notes` says what a report shows from a folder:
    such a file is brought in place under the id the site gives it, while
    the report keeps naming the id of the package.

    `checkout` is the content manifest of the checkout, where there is one:
    it says which parameters of a job definition are site settings.
    """

    name = "the package"

    def __init__(self, package: dict, checkout=None, root_uri: Optional[str] = None):
        self.package = package
        self.checkout = checkout
        self.root_uri, self.items = locate(package, root_uri)
        self.root_path = package_root_path(package, root_uri)
        self._at: dict = {}
        self.left_out: list = []
        self.reports: list = []
        self.children: list = []
        self.decisions: list = []
        self.node_types: list = []
        self.notes: list = []
        unknown = sorted(set(map(str, package)) - set(PACKAGE_FIELDS))
        if unknown:
            self.left_out.append(f"the field(s) {', '.join(unknown)} at the top of the package (an export does not "
                                 "write them, and they are not handed to the transfer service)")
        bad_path = _path_check(self.items)
        folders: list = []
        for item in self.items:
            kind = MANIFEST_KIND.get(item.kind)
            if item.kind in GRANT_KINDS:
                self.left_out.append(f"{item.kind} '{item.name}' (mdb imports no authorization rule and no share)")
            elif item.kind == NODE_TYPE_KIND:
                if item.owner is None:
                    self.left_out.append(f"node type '{item.name}' (no decision below the root folder of the package "
                                         "uses it)")
                else:
                    self.node_types.append(item)
            elif item.owner is not None:
                self.children.append(item)
            elif item.path is None:
                self.left_out.append(f"{item.kind} '{item.name}' (not below the root folder of the package)")
            elif kind is None:
                self.left_out.append(f"{item.kind} '{item.path}' (mdb does not import a {item.kind}: it brings files, "
                                     "custom steps and job definitions in place, and reports and decisions through "
                                     "the transfer service)")
            elif item.path == "":
                continue
            elif bad_path(item):
                self.left_out.append(f"{item.kind} '{item.path}' ({bad_path(item)} - a path below the content root "
                                     "is made of names without a slash, not empty, not '.' or '..', and with no "
                                     "blank at either end)")
            elif _content.never_ship(item.path):
                self.left_out.append(f"{item.shown} ({_content.never_ship(item.path)}, which never ships)")
            elif kind == "folder":
                folders.append(item)
            elif kind == "report":
                self.reports.append(item)
            elif kind == "decision":
                self.decisions.append(item)
            else:
                self._at.setdefault((kind, item.path), []).append(item)
        report_paths = {item.path for item in self.reports}
        self.left_out += [f"file '{item.name}' (belongs to report '{item.owner}', which is not imported)"
                          for item in self.children if item.owner not in report_paths]
        self.children = [item for item in self.children if item.owner in report_paths]
        # a node type goes with a decision that is imported, and not without one
        decision_paths = {item.path for item in self.decisions}
        users = node_type_users(self.items)
        self.left_out += [f"node type '{item.name}' (used by decision '{item.owner}', which is not imported)"
                          for item in self.node_types
                          if not any(user.path in decision_paths for user in users.get(item.uri, []))]
        self.node_types = [item for item in self.node_types
                           if any(user.path in decision_paths for user in users.get(item.uri, []))]
        # an empty folder of the package: nothing below it at all, not even a report
        below = [path for _kind, path in self._at] + [item.path for item in self.reports] \
            + [item.path for item in self.decisions] + [item.path for item in folders]
        self.empty_folders = [item.path for item in folders
                              if not any(path.startswith(item.path + "/") for path in below)]
        # a file a report shows from a folder (not a child of the report) is brought in place
        placed = {item.uri: item for items in self._at.values() for item in items if item.kind == "file"}
        for report in self.reports:
            for connector in _connectors(report.detail):
                shown = placed.get(connector.get("uri"))
                if shown is not None and connector.get("type") != "parentFolder":
                    self.notes.append(
                        f"report '{report.path}' shows the file '{shown.path}' ({shown.uri}) from a folder: the file is "
                        "brought in place and has the id this site gives it, and the report keeps naming the id of "
                        "the package - open the report and check that it shows the file")

    @property
    def has_content(self) -> bool:
        """Whether the package holds a file, a step, a job definition or an
        empty folder to bring in place."""
        return bool(self._at) or bool(self.empty_folders)

    def manifest(self, content_root: str, decisions: bool = True) -> "_content.ContentManifest":
        """The entries of the package as a manifest whose content root is
        the folder they are brought into. Without `decisions` the decisions
        are left out - and with them a folder that holds nothing else, which
        the in-place part would otherwise create for decisions that are not
        imported."""
        entries = [_content.Entry(path=path, kind="folder") for path in self.empty_folders]
        entries += [_content.Entry(path=path, kind=kind) for kind, path in self._at]
        entries += [_content.Entry(path=item.path, kind="report") for item in self.reports]
        if decisions:
            entries += [_content.Entry(path=item.path, kind="decision") for item in self.decisions]
        repo_root = self.checkout.repo_root if self.checkout is not None else Path(".")
        return _content.ContentManifest(repo_root=repo_root, content_root=content_root.rstrip("/"),
                                        package="", entries=entries)

    def _packaged(self, entry) -> dict:
        found = self._at.get((entry.kind, entry.path)) or []
        if len(found) != 1:
            raise ValueError(f"the package holds {len(found)} objects at this path - none is taken")
        body = _body(found[0].obj)
        if body is None:
            raise ValueError("the packaged content is unreadable")
        return body

    def file_bytes(self, entry) -> bytes:
        body = self._packaged(entry)
        try:
            return base64.b64decode(body.get("content") or "", validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("the packaged content is unreadable") from exc

    def step_body(self, entry) -> dict:
        body = {key: value for key, value in self._packaged(entry).items() if key not in _STEP_INSTANCE_KEYS}
        body.setdefault("properties", {})
        return body

    def step_document(self, entry) -> bytes:
        return json.dumps(self.step_body(entry), ensure_ascii=False).encode("utf-8")

    def job_body(self, entry) -> dict:
        body = self._packaged(entry)
        wanted = {
            "name": entry.name, "type": body.get("type") or "Compute", "code": body.get("code") or "",
            "parameters": [{key: p[key] for key in _PARAMETER_KEYS if key in p}
                           for p in body.get("parameters") or [] if isinstance(p, dict)],
            "properties": [{"name": "form", "value": p.get("value") or ""} for p in body.get("properties") or []
                           if isinstance(p, dict) and p.get("name") == "form"][:1],
        }
        if body.get("description"):
            wanted["description"] = body["description"]
        return wanted

    def release_table(self) -> Optional[bytes]:
        """The bytes of the release table the package holds
        (RELEASE_TABLE_PATH below its root), or None where it holds none -
        or holds it twice, or unreadable."""
        try:
            return self.file_bytes(_content.Entry(path=RELEASE_TABLE_PATH, kind="file"))
        except ValueError:
            return None

    def site_settings(self, entry) -> tuple:
        """The parameters of a packaged job definition that hold a value of
        the site: what the checkout's manifest marks for the entry at that
        path, and - whatever the checkout lists - the compute context and the
        content root wherever the packaged definition has them. A job
        definition the checkout does not list (or an importer without the
        manifest of this release) keeps the context a site set as well."""
        listed = self.checkout.find(entry.path, "jobDefinition") if self.checkout is not None else None
        names = list(_content.site_settings(listed)) if listed is not None else []
        packaged = {p.get("name") for p in self.job_body(entry).get("parameters") or []}
        names += [name for name in (_content.CONTEXT_PARAMETER, _content.CONTENT_ROOT_PARAMETER)
                  if name in packaged and name not in names]
        return tuple(names)


def reports_package(package: dict, root_uri: Optional[str] = None) -> tuple:
    """(a copy of a package reduced to what the transfer service has to
    bring, [what it holds as 'kind path']).

    That is the reports below the root folder, the files that are children of
    one, and the folders the reports sit in, up to the root. Files, custom
    steps and job definitions are not in it: they are brought in place. The
    package itself is not changed. The list is empty when the package holds
    no report - then there is nothing to hand to the transfer service."""
    reduced = json.loads(json.dumps(package))
    # a field at the top that an export does not write is not handed on
    for key in [key for key in reduced if key not in PACKAGE_FIELDS]:
        del reduced[key]
    _root, items = locate(reduced, root_uri)
    bad_path = _path_check(items)
    reports = [item for item in items if item.kind == "report" and item.path and not _content.never_ship(item.path)
               and not bad_path(item)]
    if not reports:
        return reduced, []
    paths = {item.path for item in reports}
    folders = {""}
    for path in paths:
        parts = path.split("/")[:-1]
        folders.update("/".join(parts[:depth]) for depth in range(1, len(parts) + 1))
    taken = {id(item) for item in reports}
    kept = [item for item in items
            if (item.kind == "folder" and item.path in folders) or id(item) in taken
            or (item.owner is not None and item.owner in paths)]
    _reduce(reduced, items, kept)
    return reduced, [f"{item.kind} {item.path if item.path else (item.owner + '/' + item.name if item.owner else item.name)}"
                     for item in kept]


def decisions_package(package: dict, root_uri: Optional[str] = None) -> tuple:
    """(a copy of a package reduced to its decisions, [what it holds as 'kind
    path'], [(name, id) of each node type it holds]).

    That is the decisions below the root folder, the custom node types they
    use, and the folders the decisions sit in, up to the root - what
    `mdb builders-import` hands to the transfer service in an import job of
    its own, after the reports. A node type travels with its id: the import
    creates it under that id where the site has none, and overwrites the one
    of that id in place. The package itself is not changed; the lists are
    empty when it holds no decision."""
    reduced = json.loads(json.dumps(package))
    for key in [key for key in reduced if key not in PACKAGE_FIELDS]:
        del reduced[key]
    _root, items = locate(reduced, root_uri)
    bad_path = _path_check(items)
    decisions = [item for item in items if item.kind == "decision" and item.path
                 and not _content.never_ship(item.path) and not bad_path(item)]
    if not decisions:
        return reduced, [], []
    users = node_type_users(items)
    taken = {id(item) for item in decisions}
    nodes = [item for item in items if item.kind == NODE_TYPE_KIND
             and any(id(user) in taken for user in users.get(item.uri, []))]
    folders = {""}
    for item in decisions:
        parts = item.path.split("/")[:-1]
        folders.update("/".join(parts[:depth]) for depth in range(1, len(parts) + 1))
    taken |= {id(item) for item in nodes}
    kept = [item for item in items if (item.kind == "folder" and item.path in folders) or id(item) in taken]
    _reduce(reduced, items, kept)
    held = [f"{item.kind} {item.path if item.path else item.name}" if item.kind != NODE_TYPE_KIND
            else f"{item.kind} {item.name} ({node_type_id(item)})" for item in kept]
    return reduced, held, [(item.name, node_type_id(item)) for item in nodes]


def mapping_preview(package: dict) -> dict:
    """The import mapping a package would be given, as far as the package
    itself says it: the tables its reports bind and their substitutions. A
    dry run shows what `rehost_mapping` makes of this without asking the
    transfer service, which only gives a mapping for a package that was
    uploaded."""
    tables, substitutions = [], []
    for obj, summary in _reports(package):
        for connector in obj.get("connectors") or []:
            if isinstance(connector, dict) and connector.get("type") == "table" and connector.get("uri"):
                spec = str(connector["uri"])
                if spec not in [t["source"] for t in tables]:
                    tables.append({"source": spec, "target": spec})
        properties = [{"name": name, "value": value} for name, value in (obj.get("substitutions") or {}).items()]
        if properties:
            substitutions.append({"resourceName": summary.get("name"), "properties": properties})
    return {"version": 1, "connectors": {"table": tables}, "substitutions": substitutions}


#: What `mdb package-export` writes into the USER_FIELDS in place of the
#: user ids of the exporting system: a text that is no user id.
NEUTRAL_USER = "sas-agentic-ai-accelerator"

#: The fields of a package whose value is a user id, by their key: who made
#: an object, who changed it last, and `userName`, which stands in the body
#: of a folder beside its `folder` object in the exports SAS Viya 2026.09
#: made on 2026-10-12 and 2026-10-13 (in all twelve folder bodies of each;
#: one user had made and exported everything, so whose id it is they do not
#: tell). This is the ONE list: `neutralise_users` rewrites these fields and
#: `named_users`, which the gate asks, counts them, in the same parts of a
#: package (`_user_parts`). Read off those exports and the four packages up
#: to 2.2.x by searching every value for the ids these fields hold: no other
#: field of an object that ships has a user id as its value. The others that
#: have one sit in what never ships - `userId` of a per-user report state,
#: `principal`, `sharedBy`, `sharedWith` and the description of a rule or a
#: share. A user id can also be PART of a path, in an export of a folder
#: below /Users/<id>: in `contentSourceLocation` and in the default of a job
#: definition's `contentRoot`. A path is not rewritten: the release file is
#: only written from the content root of the manifest (`mdb package-export`).
USER_FIELDS = ("createdBy", "modifiedBy", "userName")
USER_FIELDS_TEXT = " / ".join(USER_FIELDS)


def _user_parts(obj: dict) -> tuple:
    """(the report wrapper of a transfer object or None, its body or None,
    the parts of either that are searched for USER_FIELDS): of a report the
    object of its wrapper and the state records of its per-user states,
    behind the compression; of anything else its body (base64 JSON)."""
    wrapper = _report_wrapper(obj)
    if wrapper is not None:
        transferable = wrapper.get("transferableContent")
        states = transferable.get("reportStates") if isinstance(transferable, dict) else None
        return wrapper, None, [wrapper.get("object")] + [
            state.get("reportState") for state in (states if isinstance(states, list) else [])
            if isinstance(state, dict)]
    body = _body(obj)
    return None, body, [] if body is None else [body]


def _neutral(value) -> int:
    """Replace every USER_FIELDS value in a parsed value, in place; returns
    how many were replaced."""
    replaced = 0
    if isinstance(value, dict):
        for key in list(value):
            if key in USER_FIELDS and isinstance(value[key], str) and value[key] and value[key] != NEUTRAL_USER:
                value[key] = NEUTRAL_USER
                replaced += 1
            else:
                replaced += _neutral(value[key])
    elif isinstance(value, list):
        for item in value:
            replaced += _neutral(item)
    return replaced


def neutralise_users(package: dict) -> int:
    """Write NEUTRAL_USER into every field of USER_FIELDS a parsed package
    holds - its own, those of the object summaries, those inside the bodies
    (base64 JSON) and inside a report's wrapper (behind the compression) -
    and change nothing else; returns how many were replaced.

    WHY THIS IS SAFE FOR AN IMPORT. These fields are what the exporting
    system recorded about its objects. mdb never sends them anywhere: the
    in-place write path takes a step without them (_STEP_INSTANCE_KEYS), a
    job definition keeps the site's own, a file is sent as its bytes. What
    the transfer service does with them on an import of the reports is not
    measured; every package up to 2.2.x carried the exporter's id, and an
    import of such a package was never seen to make that user the owner of
    anything. A per-user report state, which carries a user id that does
    decide something (whose view it is), is removed before this.

    A body is written back as compact JSON (the form the transfer service
    gives a report wrapper); the values are the same, the bytes of the
    base64 are not."""
    replaced = _neutral({key: value for key, value in package.items() if key != "transferDetails"})
    for key in USER_FIELDS:
        if isinstance(package.get(key), str) and package[key] and package[key] != NEUTRAL_USER:
            package[key] = NEUTRAL_USER
    for detail in package.get("transferDetails") or []:
        obj = detail.get("transferObject") or {}
        replaced += _neutral(obj.get("summary") or {})
        content = obj.get("content") or ""
        wrapper, body, parts = _user_parts(obj)
        n = sum(_neutral(part) for part in parts)
        if n and wrapper is not None:
            flag = "FALSE" if content.startswith("FALSE###") else "TRUE"
            obj["content"] = deflate(json.dumps(wrapper, separators=(",", ":"), ensure_ascii=False), flag)
        elif n:
            obj["content"] = base64.b64encode(
                json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).decode("ascii")
        replaced += n
    return replaced


def named_users(package: dict) -> dict:
    """{user id: number of places} for every field of USER_FIELDS a package
    holds: its own, those of the object summaries and those inside the
    bodies and the report wrappers - the places `neutralise_users` rewrites,
    so that after it only NEUTRAL_USER is named. Nothing here removes one."""
    found: dict = {}

    def walk(value) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in USER_FIELDS and isinstance(item, str) and item:
                    found[item] = found.get(item, 0) + 1
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk({key: value for key, value in package.items() if key != "transferDetails"})
    for detail in package.get("transferDetails") or []:
        obj = detail.get("transferObject") or {}
        walk(obj.get("summary") or {})
        walk(_user_parts(obj)[2])
    return found


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
                # An option the code of the page posts that the packaged
                # report does not hold yet: the report gains a label when it
                # is saved in Visual Analytics with the newer page, which the
                # export of a release does. Until then the package is behind
                # the checkout - drift, which a release does not ship.
                problems.append(Difference(f"{where}: {_copy_name(index)} lacks {len(lacking)} option label(s): "
                                           + ", ".join(lacking), drift=True))
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


@dataclass
class Difference:
    """One sentence of the content gate.

    `drift` is True for a difference in CONTENT against the checkout - a
    file, a custom step, a job definition, one of the two generated files, an
    option label the page posts that the packaged report does not hold yet:
    the package is the content of an earlier commit. Everything else is the
    package being something it must never be, whatever the checkout holds."""

    text: str
    drift: bool = False


def _object_differences(item: Located, entry, manifest) -> list:
    """How a packaged file, step or job definition differs from the
    checkout: [Difference]."""
    def wrong(text: str) -> Difference:
        return Difference(text)

    def behind(text: str) -> Difference:
        return Difference(text, drift=True)

    body = _body(item.obj)
    if body is None:
        return [wrong(f"{item.shown}: the packaged content is unreadable")]
    if entry.kind == "file":
        try:
            packaged = base64.b64decode(body.get("content") or "")
        except (binascii.Error, ValueError):
            return [wrong(f"{item.shown}: the packaged content is unreadable")]
        found = []
        resource = body.get("fileResource") if isinstance(body.get("fileResource"), dict) else {}
        if resource.get("name") not in (None, item.name):
            found.append(wrong(f"file '{item.path}' is named '{resource.get('name')}' in its body"))
        expected = _content.file_bytes(manifest, entry)
        if entry.generated:
            errors, drift = _content.generated_findings(entry, expected, packaged, manifest.repo_root)
            return found + [wrong(f"file '{item.path}' {text}") for text in errors] \
                + [behind(f"file '{item.path}' {text}") for text in drift]
        if _content.lf_bytes(packaged) != _content.lf_bytes(expected):
            found.append(behind(f"file '{item.path}' differs from {entry.source} "
                                f"({len(_content.lf_bytes(packaged))} vs {len(_content.lf_bytes(expected))} bytes)"))
        return found
    if entry.kind == "step":
        expected = _content.step_body(manifest, entry)
        return [behind(f"step '{item.path}': '{part}' differs from {entry.source}")
                for part in _content.step_differences(expected, body)] \
            + [wrong(f"step '{item.path}' {text}") for text in _content.step_extras(expected, body)]
    sources = {"code": entry.code or "the code of a job definition that only serves a form",
               "form": entry.form or "no form", "parameters": "the parameters of its manifest entry"}
    expected = _content.job_definition_body(manifest, entry)
    return [behind(f"job definition '{item.path}': '{part}' differs from {sources[part]}")
            for part in _content.job_differences(expected, body)] \
        + [wrong(f"job definition '{item.path}' {text}") for text in _content.job_extras(expected, body)]


#: The parts of a packaged object that are its CONTENT - compared with the
#: checkout as content (and drift between releases). Everything else of the
#: object is what it says about itself: names, descriptions, links, users.
_CONTENT_PARTS = ("code", "templates", "ui", "localUi", "content")


def _without_content(value):
    if isinstance(value, dict):
        kept = {}
        for key, item in value.items():
            if key in _CONTENT_PARTS:
                continue
            if key == "properties" and isinstance(item, list):
                item = [p for p in item if not (isinstance(p, dict) and p.get("name") == "form")]
            kept[key] = _without_content(item)
        return kept
    if isinstance(value, list):
        return [_without_content(item) for item in value]
    return value


def _metadata_problems(item: Located, allowed=ALLOWED_HOSTS) -> list:
    """What a packaged file, step, job definition or folder carries beside
    its content and must not: sentences. Read from the exports of 2026-10-12
    and the packages up to 2.2.x, such an object has one connector - its
    `parentFolder`, beside the transfer object - no substitution, and names
    no host but a public one in what it says about itself (its summary, its
    body without the program, the form, the file's bytes and the step's
    templates). A host in its content is content, compared with the checkout."""
    obj = item.obj
    where = item.shown
    problems = []
    for connector in list(item.detail.get("connectors") or []):
        if isinstance(connector, dict) and connector.get("type") != "parentFolder":
            problems.append(f"{where} carries a connector of type '{connector.get('type')}' - an export gives such an "
                            "object its parentFolder only")
    for connector in obj.get("connectors") or []:
        problems.append(f"{where} carries a connector of type '{(connector or {}).get('type')}' in its transfer "
                        "object - an export gives such an object none there")
    if obj.get("substitutions"):
        problems.append(f"{where} carries substitutions ({', '.join(map(str, obj['substitutions']))}) - an export "
                        "gives such an object none")
    told = json.dumps([_without_content(obj.get("summary") or {}), _without_content(_body(obj) or {}),
                       {key: value for key, value in obj.items() if key not in ("content", "summary")}])
    for host, n in sorted(hosts_in(told).items()):
        if host not in allowed:
            problems.append(f"{where} names the host {host} ({n} time(s)) in what it says about itself, outside its "
                            "content")
    return problems


def _report_file_differences(report: Located, entry, children: list) -> list:
    """How the files that are children of a packaged report differ from the
    ones its manifest entry lists: sentences. A child is compared by its name
    and, where the entry gives one, by the SHA-256 of its bytes - a report's
    pictures have no source in the repository, so the hash is what stands in
    the checkout."""
    import hashlib
    where = f"report '{report.path}'"
    wanted = [dict(listed) for listed in entry.files]
    problems = []
    for child in children:
        body = _body(child.obj) or {}
        try:
            digest = hashlib.sha256(base64.b64decode(body.get("content") or "")).hexdigest()
        except (binascii.Error, ValueError):
            problems.append(f"file '{child.name}' of {where}: the packaged content is unreadable")
            continue
        match = next((w for w in wanted if w["name"] == child.name and w["sha256"] in (None, digest)), None)
        if match is not None:
            wanted.remove(match)
        elif any(w["name"] == child.name for w in entry.files):
            problems.append(f"file '{child.name}' of {where} is not the file its manifest entry lists "
                            f"(SHA-256 {digest})")
        else:
            problems.append(f"file '{child.name}' belongs to {where} and is not among the files its manifest "
                            "entry lists")
    problems += [f"{where}: the file '{w['name']}' its manifest entry lists is not in the package" for w in wanted]
    return problems


def _version_label(version: dict) -> str:
    return f"{version.get('majorRevision', '?')}.{version.get('minorRevision', '?')}"


#: The data step of the Create and the Update program of a custom node that
#: writes the request body of its content - one `put '...';` per line.
NODE_PROGRAM_START = "* Create the content for the node type;"
#: The fields of a signature term the gate compares with the program.
TERM_COMPARED = ("name", "dataType", "length", "direction")


def node_code(text) -> str:
    """The code of a node type - line ends, and blanks at the end of a line,
    aside."""
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def program_node_content(text: str) -> dict:
    """The content (code and signature terms) a SAS program gives a custom
    node type: the JSON its data step after NODE_PROGRAM_START writes, one
    `put '<line>';` at a time (a quote doubled in the SAS string is one).
    ValueError where the program writes none that can be read."""
    text = text.replace("\r\n", "\n")
    start = text.find(NODE_PROGRAM_START)
    if start < 0:
        raise ValueError(f"it has no data step after '{NODE_PROGRAM_START}'")
    end = text.find("run;", start)
    lines = re.findall(r"^\s*put '(.*)';\s*$", text[start:end if end >= 0 else None], flags=re.MULTILINE)
    try:
        content = json.loads("\n".join(line.replace("''", "'") for line in lines))
    except ValueError as exc:
        raise ValueError(f"the content it writes is no JSON ({exc})") from exc
    if not isinstance(content, dict) or not str(content.get("staticContent") or "").strip():
        raise ValueError("the content it writes has no staticContent")
    return content


def _terms(content: dict) -> list:
    return sorted(({key: term.get(key) for key in TERM_COMPARED}
                   for term in content.get("nodeTypeSignatureTerms") or [] if isinstance(term, dict)),
                  key=lambda term: str(term["name"]))


def _node_program_problems(shown: str, content: dict, listed: dict, repo_root) -> list:
    """Where the entry names the SAS program of the node type (`content`):
    the packaged code and terms are the ones that program writes - the
    import overwrites the node type of that id on every site with them."""
    program = listed.get("content")
    if not program or repo_root is None:
        return []
    try:
        expected = program_node_content(_content.repository_file(repo_root, program).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"{shown}: {program} cannot be read for the code of the release - {exc}"]
    problems = []
    if node_code(content.get("staticContent")) != node_code(expected.get("staticContent")):
        problems.append(f"{shown}: its code is not the code {program} writes - the import would give every site "
                        "the code of the package")
    if _terms(content) != _terms(expected):
        problems.append(f"{shown}: its terms ({', '.join(str(t['name']) for t in _terms(content)) or 'none'}) are not "
                        f"the ones {program} writes - name, type, length and direction")
    return problems


def _node_type_problems(node: Located, listed: dict, where: str, allowed=ALLOWED_HOSTS, repo_root=None) -> list:
    """What a packaged custom node type does not pass: it is the node type the
    entry of its decision lists - that name, that id in its summary, in its
    own representation and in its content - with code to run, the code and
    the terms of the program its entry names under `content`, and it carries
    nothing an export does not give one (no connector, no substitution)."""
    shown = f"node type '{node.name}' ({node_type_id(node)}) of {where}"
    body = _body(node.obj)
    rep = (body or {}).get("decisionNodeTypeRep")
    content = (body or {}).get("decisionNodeTypeContentRep")
    if not isinstance(rep, dict):
        return [f"{shown}: the packaged content is unreadable"]
    problems = []
    if node_type_id(node) != listed["id"] or rep.get("id") != listed["id"]:
        problems.append(f"{shown} has the id {rep.get('id')} in its body, its manifest entry lists {listed['id']}")
    if node.name != listed["name"] or rep.get("name") != listed["name"]:
        problems.append(f"{shown} is named '{rep.get('name')}', its manifest entry lists '{listed['name']}'")
    if not isinstance(content, dict) or not str(content.get("staticContent") or "").strip():
        problems.append(f"{shown} carries no code (decisionNodeTypeContentRep with staticContent)")
    else:
        if content.get("nodeTypeId") != listed["id"]:
            problems.append(f"{shown}: its content belongs to the node type {content.get('nodeTypeId')}")
        problems += _node_program_problems(shown, content, listed, repo_root)
    for connector in list(node.detail.get("connectors") or []) + list(node.obj.get("connectors") or []):
        problems.append(f"{shown} carries a connector of type '{(connector or {}).get('type')}' - an export gives a "
                        "node type none")
    if node.obj.get("substitutions"):
        problems.append(f"{shown} carries substitutions - an export gives a node type none")
    for host, n in sorted(hosts_in(json.dumps([node.obj.get("summary") or {}, body])).items()):
        if host not in allowed:
            problems.append(f"{shown} names the host {host} ({n} time(s))")
    return problems


def _decision_differences(item: Located, entry, items: list, allowed=ALLOWED_HOSTS, repo_root=None) -> list:
    """What a packaged decision does not pass: sentences.

    * its body is a decision of that name, with every version;
    * every step of every version that calls a custom node names the node
      type by its address (a node type that was deleted leaves a step with a
      name and no address), and the node types its steps and its `dntStatic`
      connectors name are exactly the ones its entry lists under `nodeTypes:`;
    * each of them is in the package, as `_node_type_problems` asks;
    * beside its `parentFolder` and the connectors to its node types (and
      the one with a name and nothing else the export writes beside each) it
      has no connector - one would be an object it depends on that does not
      ship - no substitution, and it names no host but a public one."""
    where = f"decision '{item.path}'"
    body = _body(item.obj)
    current = (body or {}).get("decisionVersionRep")
    if not isinstance(current, dict):
        return [f"{where}: the packaged content is unreadable"]
    problems = []
    if current.get("name") not in (None, item.name):
        problems.append(f"{where} is named '{current.get('name')}' in its body")
    for connector in item.detail.get("connectors") or []:
        if isinstance(connector, dict) and connector.get("type") != "parentFolder":
            problems.append(f"{where} carries a connector of type '{connector.get('type')}' - an export gives a "
                            "decision its parentFolder there")
    for connector in item.obj.get("connectors") or []:
        if not isinstance(connector, dict):
            continue
        if connector.get("type") == "dntStatic" or (not connector.get("type") and not connector.get("uri")):
            continue
        problems.append(f"{where} depends on {connector.get('uri') or connector.get('name')} (a connector of type "
                        f"'{connector.get('type')}'), which does not ship with it")
    if item.obj.get("substitutions"):
        problems.append(f"{where} carries substitutions ({', '.join(map(str, item.obj['substitutions']))})")
    versions = [current] + [v for v in body.get("revisions") or [] if isinstance(v, dict)]
    used: list = []
    for version in versions:
        for custom in custom_objects(version):
            uri = str(custom.get("uri") or "")
            if not uri:
                problems.append(f"{where}: a step of version {_version_label(version)} calls the node "
                                f"'{custom.get('name')}' and names no node type - its node type was deleted where "
                                "the decision was exported, and the version cannot run")
            elif uri not in used:
                used.append(uri)
    used += [uri for uri in _decision_connected(item) if uri not in used]
    wanted = {f"{NODE_TYPE_URI}{listed['id']}": listed for listed in entry.node_types}
    for uri in used:
        if uri not in wanted:
            problems.append(f"{where} uses the node type {uri}, which its manifest entry does not list")
    nodes = {other.uri: other for other in items if other.kind == NODE_TYPE_KIND}
    steps = set(node_type_uris(versions))
    for uri, listed in wanted.items():
        if uri not in steps:
            problems.append(f"{where}: no step calls the node type '{listed['name']}' ({listed['id']}) its manifest "
                            "entry lists")
        if uri not in nodes:
            problems.append(f"{where}: the node type '{listed['name']}' ({listed['id']}) is not in the package - an "
                            "import would leave the decision without it")
            continue
        problems += _node_type_problems(nodes[uri], listed, where, allowed, repo_root)
    told = json.dumps([item.obj.get("summary") or {}, body,
                       {key: value for key, value in item.obj.items() if key not in ("content", "summary")}])
    for host, n in sorted(hosts_in(told).items()):
        if host not in allowed:
            problems.append(f"{where} names the host {host} ({n} time(s))")
    return problems


#: The fields a transfer package has at its top, as the exports read so far
#: carry them. Another one is something an export does not write.
PACKAGE_FIELDS = ("createdBy", "creationTimeStamp", "description", "id", "links", "modifiedBy", "modifiedTimeStamp",
                  "name", "options", "requestedItems", "transferDetails", "transferObjectCount", "version")


def package_findings(package: dict, manifest, package_name: str = "") -> list:
    """The content gate: every difference between a package and the checkout
    as a Difference, in the order of the package. An empty list means the
    package is the checkout's content and nothing else:

    * every entry of the manifest is in the package, at its path, once;
    * a file equals the repository file (line endings aside), a generated file
      what its generator writes now (release.json: its kind, its fields, the
      release, the commit where git can say, and its entries), a step the
      .step file in `templates`, `ui` and `flowMetadata`, a job definition its
      program, its form and its parameters;
    * a step has the name and type of its file and no `properties` or
      `arguments`, a job definition the name and type of its entry, one form
      at most and no other property;
    * a report passes its checks, and the files that are its children are
      the ones its entry lists, with the hash given there;
    * a decision uses exactly the custom node types its entry lists, each is
      in the package under the id listed there (`_decision_differences`),
      and no node type is in it that no listed decision uses;
    * no object is in the package that the manifest does not list, none sits
      on a path that never ships, there is no authorization rule or share,
      the package has no field at its top that an export does not write, and
      it names no user id in a field of USER_FIELDS (NEUTRAL_USER stands
      there).

    Not compared, each for its reason: the description of a step or a job
    definition and the display fields the dataFlows service derives
    (`localDisplayName`, `localUi`) - text beside the content, which the
    deploy does not hold a site to either; the ids, the links and the time
    stamps of the objects - they are those of the exporting system (the users
    are held to NEUTRAL_USER, above);
    a line end (CRLF for LF) in a text file.
    """
    root, items = locate(package)
    if root is None:
        return [Difference("the package holds no folder, so its objects have no place below a content root")]
    problems: list = []
    seen: dict = {}
    listed_reports = {item.path: item for item in items
                      if item.kind == "report" and _listed(manifest, item) is not None}
    in_package = {item.uri for item in items if item.uri}
    unknown = sorted(set(map(str, package)) - set(PACKAGE_FIELDS))
    if unknown:
        problems.append(Difference(f"the package carries the field(s) {', '.join(unknown)} at its top, which an "
                                   "export does not write"))
    users = {user: n for user, n in named_users(package).items() if user != NEUTRAL_USER}
    if users:
        problems.append(Difference(
            f"the package names the user id(s) {', '.join(sorted(users))} in {USER_FIELDS_TEXT} "
            f"({sum(users.values())} place(s)) - an export of the release writes '{NEUTRAL_USER}' there, and "
            "`mdb package-check --fix` rewrites them"))
    users = node_type_users(items)
    for item in items:
        if item.kind in GRANT_KINDS:
            problems.append(Difference(f"the package carries the authorization {item.kind} '{item.name}'"))
            continue
        if item.path == "":
            continue
        if item.kind == NODE_TYPE_KIND and item.owner is not None:
            # checked with the decision that lists it; one no listed decision uses is not shipped
            if not any(_listed(manifest, user) is not None for user in users.get(item.uri, [])):
                problems.append(Difference(f"node type '{item.name}' ({node_type_id(item)}) is used by decision "
                                           f"'{item.owner}', which is not in the manifest"))
            continue
        if item.owner is not None:
            if item.owner not in listed_reports:
                problems.append(Difference(
                    f"file '{item.name}' belongs to report '{item.owner}', which is not in the manifest"))
            continue
        if item.path is None:
            problems.append(Difference(f"{item.kind} '{item.name}' is in the package but not below its content root"))
            continue
        entry = _listed(manifest, item)
        if entry is None:
            problems.append(Difference(f"{item.shown} is in the package but not in the manifest"))
            continue
        seen[(entry.kind, entry.path)] = seen.get((entry.kind, entry.path), 0) + 1
        if seen[(entry.kind, entry.path)] > 1:
            problems.append(Difference(f"{item.shown} is in the package {seen[(entry.kind, entry.path)]} times - "
                                       "one path holds one object"))
            continue
        forbidden = _content.never_ship(item.path)
        if forbidden:
            problems.append(Difference(f"{item.shown}: {forbidden} and never ships"))
            continue
        if entry.kind == "report":
            children = [child for child in items if child.owner == item.path]
            problems.extend(text if isinstance(text, Difference) else Difference(text) for text in
                            _report_differences(item, entry, in_package, package_name)
                            + _report_file_differences(item, entry, children))
            continue
        if entry.kind == "decision":
            problems.extend(Difference(text) for text in
                            _decision_differences(item, entry, items, repo_root=manifest.repo_root))
            continue
        problems.extend(Difference(text) for text in _metadata_problems(item))
        if entry.kind != "folder":
            try:
                problems.extend(_object_differences(item, entry, manifest))
            except (OSError, ValueError) as exc:
                problems.append(Difference(f"{item.shown}: cannot be compared with the checkout ({exc})"))
    for entry in manifest.all_entries():
        if (entry.kind, entry.path) not in seen:
            problems.append(Difference(f"{entry.kind} '{entry.path}' is in the manifest but not in the package"))
    return problems


def package_differences(package: dict, manifest, package_name: str = "") -> list:
    """`package_findings` as sentences: every difference, drift or not. What
    a release asks of a package (`mdb package-check --release`, and an
    export into the release file) is that this is empty."""
    return [difference.text for difference in package_findings(package, manifest, package_name)]

