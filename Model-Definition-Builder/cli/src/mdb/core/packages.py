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

THE SHIPPED PACKAGES, AND WHAT THEY MUST HOLD. Two packages are shipped: the
Prompt Builder (the UI job definition, the optimization job, the report) and
the RAG runtime (custom steps, rag_core, jobs, the retrieval model - no UI).
Both are second copies of things the repository holds as source, and an
option restore cannot add a label the shipped report lacks, so the second half
of this module compares a package with the repository: `missing_option_labels`,
`non_neutral_defaults`, `form_mismatch`, `runtime_ui_objects` and
`runtime_content_drift`. Each returns the problems it found as sentences; an
empty list is a pass.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from .options import LEGACY_REPORTS, MERGED_REPORT, read_options

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

        # the substitution map: the one occurrence a plain search would find
        plain = json.dumps(obj.get("substitutions") or {})
        for host, n in hosts_in(plain).items():
            if host not in allowed:
                result.findings.append(
                    Finding(package_name, name, host, n, compressed=False))

        # and the report itself, which is where the rest of them hide
        inner = inflate(obj.get("content") or "")
        if inner is None:
            continue
        for host, n in hosts_in(inner).items():
            if host not in allowed:
                result.findings.append(
                    Finding(package_name, name, host, n, compressed=True))

        states = len(report_copies(obj)) - 1
        if states > 0:
            result.states.append(StateFinding(package_name, name, states))
    return result


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


def drop_objects(package: dict, names) -> list:
    """Remove the objects called `names` from a parsed package; returns what
    was dropped as 'type name'. A folder exported from a shared development
    environment picks up whatever else lives in it - a demo report, a scratch
    job - and that must not ship. Objects are matched by name whatever their
    type, so a report, a job definition, a file or a step all go the same
    way. Only leaf objects are meant to: the package's requested items and
    the remaining objects' connectors are left alone, which is right for a
    report or a job definition and wrong for a folder with children.
    """
    wanted = {n.strip().lower() for n in names if n and n.strip()}
    if not wanted:
        return []
    kept, dropped = [], []
    for detail in package.get("transferDetails") or []:
        summary = (detail.get("transferObject") or {}).get("summary") or {}
        if (summary.get("name") or "").strip().lower() in wanted:
            dropped.append(f"{summary.get('type')} {summary.get('name')}")
        else:
            kept.append(detail)
    if dropped:
        package["transferDetails"] = kept
        if isinstance(package.get("transferObjectCount"), int):
            package["transferObjectCount"] = len(kept)
    return dropped


def default_packages(repo_root) -> list:
    """The transfer packages this repository ships."""
    folder = Path(repo_root) / "SAS-Viya-Integrations"
    return sorted(p for p in folder.glob("*.json") if p.is_file()) \
        if folder.is_dir() else []


# ---------------------------------------------------------------------------
# The two packages builders-import ships: which files they are, what they must hold
# ---------------------------------------------------------------------------

#: The package with the Prompt Builder UI, its optimization job and its report.
PROMPT_BUILDER_PACKAGE = "SAS-Agentic-AI-Accelerator-Prompt-Builder.json"

#: The package with the RAG runtime, and the file name it had while it also
#: carried a UI of its own. A checkout holds one of the two; the old name is
#: accepted so that `mdb builders-import` keeps finding the runtime in a
#: checkout whose package has not been exported under the new name.
RAG_RUNTIME_PACKAGE = "SAS-Agentic-AI-Accelerator-RAG-Runtime.json"
LEGACY_RAG_PACKAGE = "SAS-Agentic-AI-Accelerator-RAG-Builder.json"

#: The job definition that serves the Prompt Builder UI: its `form` property
#: is the single-file app, LLM-Prompt-Builder/dist/index.html.
UI_JOB_DEFINITION = "SAS-LLM-Prompt-Builder-UI"
UI_BUNDLE = ("LLM-Prompt-Builder", "dist", "index.html")

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

#: Where the runtime package's content lives in the repository, by the folder
#: it has under the RAG content root. `recursive` follows sub-folders;
#: `pattern` selects the files - the same selection deploy-rag-content makes,
#: which is the other way this content reaches a site.
RUNTIME_SOURCES = {
    "rag_core": {"path": ("SAS-Viya-Integrations", "RAG", "rag_core"), "pattern": "*.py", "recursive": True},
    "jobs": {"path": ("SAS-Viya-Integrations", "RAG-Ingestion"), "pattern": "*.sas", "recursive": False},
    "models": {"path": ("SAS-Viya-Integrations", "RAG"), "pattern": "*.py", "recursive": False},
    "steps": {"path": ("SAS-Viya-Integrations", "Custom-Steps"), "pattern": "RAG - *.step", "recursive": False},
}


def shipped_builder_packages(repo_root) -> list:
    """The packages `mdb builders-import` imports by default, in import order:
    the Prompt Builder first, then the RAG runtime under whichever of its two
    file names the checkout holds. A file that does not exist is left out."""
    folder = Path(repo_root) / "SAS-Viya-Integrations"
    runtime = folder / RAG_RUNTIME_PACKAGE
    if not runtime.is_file():
        runtime = folder / LEGACY_RAG_PACKAGE
    return [path for path in (folder / PROMPT_BUILDER_PACKAGE, runtime) if path.is_file()]


def _object_kind(summary: dict) -> str:
    """An object's type as one word. Content files carry their media type
    (application/vnd.sas.file+json); the rest a plain name (report, folder)."""
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


def _lf(value):
    """Line endings normalised, in a string or in every string of a nested
    value: a checkout may hold CRLF where the package holds LF, and that
    difference is not a difference in content."""
    if isinstance(value, str):
        return value.replace("\r\n", "\n")
    if isinstance(value, dict):
        return {key: _lf(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_lf(item) for item in value]
    return value


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


def form_mismatch(package: dict, html: str, job_definition: str = UI_JOB_DEFINITION) -> list:
    """Problems when a packaged job definition does not serve `html` as its
    `form` property - when the package carries an older build of the UI than
    the repository does."""
    obj = _find_object(package, "jobDefinition", job_definition)
    if obj is None:
        return [f"the package holds no job definition named '{job_definition}'"]
    body = _body(obj) or {}
    forms = [p.get("value") or "" for p in body.get("properties") or [] if p.get("name") == "form"]
    if not forms:
        return [f"job definition '{job_definition}' has no 'form' property"]
    if _lf(forms[0]) != _lf(html):
        return [f"job definition '{job_definition}': the form property "
                f"({len(forms[0].encode('utf-8'))} bytes) is not the given UI bundle "
                f"({len(html.encode('utf-8'))} bytes)"]
    return []


def runtime_ui_objects(package: dict) -> list:
    """Problems when a runtime package carries a UI: any report or job
    definition. The runtime is steps, files and their folders."""
    return [f"the runtime package carries {kind} '{name}'"
            for kind, name in package_objects(package) if kind in ("report", "jobDefinition")]


def _runtime_location(obj: dict):
    """(folder key, path below it) of a runtime object, from where it was
    exported: '/…/RAG/rag_core/adapters' is ('rag_core', ('adapters',)). None
    when the object does not sit in one of the RUNTIME_SOURCES folders."""
    segments = [s for s in str(obj.get("contentSourceLocation") or "").split("/") if s]
    for index in range(len(segments) - 1, -1, -1):
        if segments[index] in RUNTIME_SOURCES:
            return segments[index], tuple(segments[index + 1:])
    return None


def _runtime_repo_files(repo_root) -> dict:
    """{(folder key, sub-path..., file name): Path} for every repository file
    the runtime package is a copy of."""
    files: dict = {}
    for key, source in RUNTIME_SOURCES.items():
        base = Path(repo_root).joinpath(*source["path"])
        if not base.is_dir():
            continue
        found = base.rglob(source["pattern"]) if source["recursive"] else base.glob(source["pattern"])
        for path in found:
            if path.is_file() and "__pycache__" not in path.parts:
                files[(key, *path.relative_to(base).parts)] = path
    return files


def runtime_content_drift(package: dict, repo_root) -> list:
    """Problems when the runtime package and the repository disagree.

    The package is a second copy of rag_core, the jobs, the retrieval model
    and the RAG steps, and `mdb builders-import` writes it over whatever
    deploy-rag-content put on a site - so a package behind the repository
    silently rolls a site's runtime back. Every packaged file must equal the
    repository file (line endings aside), every RAG step's `templates` and
    `ui` must equal the repository .step, nothing the repository holds may be
    missing, and nothing else may ride along.
    """
    expected = _runtime_repo_files(repo_root)
    problems, seen = [], set()
    for detail in package.get("transferDetails") or []:
        obj = detail.get("transferObject") or {}
        summary = obj.get("summary") or {}
        kind, name = _object_kind(summary), summary.get("name") or "?"
        if kind not in ("file", "flowStep"):
            continue
        location = _runtime_location(obj)
        key = (location[0], *location[1], name) if location else None
        shown = "/".join(key) if key else name
        if key is None or key not in expected:
            problems.append(f"{kind} '{shown}' is in the package but is not runtime content "
                            f"of the repository (exported from '{obj.get('contentSourceLocation')}')")
            continue
        seen.add(key)
        body = _body(obj)
        if body is None:
            problems.append(f"{kind} '{shown}': the packaged content is unreadable")
            continue
        source = expected[key].read_text(encoding="utf-8")
        if kind == "file":
            try:
                packaged = base64.b64decode(body.get("content") or "").decode("utf-8")
            except (binascii.Error, ValueError):
                problems.append(f"file '{shown}': the packaged content is unreadable")
                continue
            if _lf(packaged) != _lf(source):
                problems.append(f"file '{shown}' differs from the repository "
                                f"({len(_lf(packaged))} vs {len(_lf(source))} characters)")
            continue
        step = json.loads(source)
        for part in ("templates", "ui"):
            if _lf(body.get(part)) != _lf(step.get(part)):
                problems.append(f"step '{shown}': '{part}' differs from the repository .step")
    for key in sorted(set(expected) - seen):
        problems.append(f"'{'/'.join(key)}' is in the repository but not in the package")
    return problems
