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
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path

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
class Result:
    findings: list = field(default_factory=list)
    reports_checked: int = 0
    packages_checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.findings


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
    result = Result(packages_checked=1)
    package = json.loads(path.read_text(encoding="utf-8"))
    for obj, summary in _reports(package):
        result.reports_checked += 1
        name = summary.get("name") or "(unnamed report)"

        # the substitution map: the one occurrence a plain search would find
        plain = json.dumps(obj.get("substitutions") or {})
        for host, n in hosts_in(plain).items():
            if host not in allowed:
                result.findings.append(
                    Finding(path.name, name, host, n, compressed=False))

        # and the report itself, which is where the rest of them hide
        inner = inflate(obj.get("content") or "")
        if inner is None:
            continue
        for host, n in hosts_in(inner).items():
            if host not in allowed:
                result.findings.append(
                    Finding(path.name, name, host, n, compressed=True))
    return result


def sanitise_package(path, allowed=ALLOWED_HOSTS) -> tuple:
    """Rewrite every disallowed host to the placeholder. Returns (text, fixed).

    The file is edited as TEXT and the report content token is swapped whole,
    so every other byte the export wrote is preserved: these files are large,
    generated, and reviewed as diffs. Re-serialising the JSON would reformat
    the package and bury the one change that matters.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
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
                    f"{path.name}: report content is not unique in the file, "
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
    """The transfer packages this repository ships."""
    folder = Path(repo_root) / "SAS-Viya-Integrations"
    return sorted(p for p in folder.glob("*.json") if p.is_file()) \
        if folder.is_dir() else []
