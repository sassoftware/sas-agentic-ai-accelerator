# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Transfer packages must not name the environment that exported them.

The failure this guards against already happened: a hand-sanitised package
fixed the one visible occurrence of an internal hostname and left six inside
zlib-compressed report content, and they reached a public repository. The
tests below are written against that specific shape - a host that a grep
cannot see.

The last test is the one that matters in practice: it checks the packages this
repository actually ships, so a re-export that reintroduces a host fails the
suite rather than a review.
"""
import json
from pathlib import Path

import pytest

from mdb.core.packages import (ALLOWED_HOSTS, PLACEHOLDER_HOST, check_package,
                               default_packages, deflate, hosts_in, inflate,
                               sanitise_package)

REPO_ROOT = Path(__file__).resolve().parents[3]

INTERNAL = "viya4-example.internal.test"


def _package(report_content: str, substitutions=None) -> dict:
    """A transfer package with one report, shaped like a real export."""
    return {
        "version": 4,
        "name": "test package",
        "transferDetails": [
            {"transferObject": {
                "id": "11111111-2222-3333-4444-555555555555",
                "summary": {"id": "11111111-2222-3333-4444-555555555555",
                            "type": "report", "name": "Test Builder"},
                "content": report_content,
                "substitutions": substitutions or {},
            }},
        ],
    }


def _bird(host: str) -> str:
    """Report content of the shape the options live in."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<SASReport>'
        f'<PromptDefinition label="viyaHost"><DefaultValue><String>https://{host}'
        '</String></DefaultValue></PromptDefinition>'
        f'<PromptDefinition label="SCREndpoint"><DefaultValue><String>https://{host}/llm'
        '</String></DefaultValue></PromptDefinition>'
        '<Link href="https://sassoftware.github.io/docs"/>'
        '</SASReport>')


def _write(tmp_path: Path, package: dict, name="pkg.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(package), encoding="utf-8")
    return path


# ---- the envelope ---------------------------------------------------------
def test_a_compressed_report_round_trips():
    text = _bird(INTERNAL)
    assert inflate(deflate(text)) == text


def test_an_uncompressed_envelope_is_read_too():
    """FALSE### is the same envelope without the deflate. Handling it means a
    future export that stops compressing does not silently skip the check."""
    text = _bird(INTERNAL)
    assert inflate(deflate(text, "FALSE")) == text


def test_anything_that_is_not_an_envelope_is_ignored():
    assert inflate("just a string") is None
    assert inflate("") is None
    assert inflate(None) is None


def test_hosts_come_from_urls_not_from_prose():
    """A bare dotted string could be a version or a filename; the authority of
    an absolute URL is always a host."""
    assert hosts_in("see https://example.test/a and http://other.test:8080/b") == {
        "example.test": 1, "other.test": 1}
    assert hosts_in("version 1.2.3, file my.report.json") == {}


# ---- the check ------------------------------------------------------------
def test_a_host_hidden_in_compressed_content_is_found(tmp_path):
    """The whole point: this occurrence is invisible to grep."""
    path = _write(tmp_path, _package(deflate(_bird(INTERNAL))))
    assert INTERNAL not in path.read_text(encoding="utf-8")   # not greppable
    result = check_package(path)
    assert not result.ok
    assert [(f.host, f.compressed) for f in result.findings] == [(INTERNAL, True)]


def test_a_host_in_the_substitution_map_is_found(tmp_path):
    path = _write(tmp_path, _package(
        deflate(_bird(PLACEHOLDER_HOST)),
        {"VisualElement:ve8": f"https://{INTERNAL}/SASJobExecution/?_program=/x"}))
    result = check_package(path)
    assert [(f.host, f.compressed) for f in result.findings] == [(INTERNAL, False)]


def test_a_clean_package_passes(tmp_path):
    path = _write(tmp_path, _package(
        deflate(_bird(PLACEHOLDER_HOST)),
        {"VisualElement:ve8": f"https://{PLACEHOLDER_HOST}/SASJobExecution/"}))
    result = check_package(path)
    assert result.ok
    assert result.reports_checked == 1


def test_public_addresses_are_allowed(tmp_path):
    """The report footer links to the published documentation. That is the
    same host for every deployment and must not read as a finding."""
    path = _write(tmp_path, _package(deflate(_bird(PLACEHOLDER_HOST))))
    assert check_package(path).ok
    assert "sassoftware.github.io" in ALLOWED_HOSTS


def test_every_occurrence_is_counted_not_just_the_first(tmp_path):
    path = _write(tmp_path, _package(deflate(_bird(INTERNAL))))
    finding = check_package(path).findings[0]
    assert finding.occurrences == 2          # viyaHost and SCREndpoint


# ---- the fix --------------------------------------------------------------
def test_the_fix_reaches_into_the_compressed_content(tmp_path):
    path = _write(tmp_path, _package(
        deflate(_bird(INTERNAL)),
        {"VisualElement:ve8": f"https://{INTERNAL}/SASJobExecution/"}))
    text, fixed = sanitise_package(path)
    path.write_text(text, encoding="utf-8", newline="")
    assert fixed[INTERNAL] >= 2
    assert check_package(path).ok
    # and the report still inflates to a well-formed document
    package = json.loads(path.read_text(encoding="utf-8"))
    inner = inflate(package["transferDetails"][0]["transferObject"]["content"])
    assert inner.startswith('<?xml version="1.0"')
    assert PLACEHOLDER_HOST in inner and INTERNAL not in inner


def test_the_fix_leaves_the_rest_of_the_package_alone(tmp_path):
    """These files are large, generated and reviewed as diffs, so a rewrite
    must not reformat anything it did not have to change."""
    package = _package(deflate(_bird(INTERNAL)))
    package["description"] = "  spacing   and \"quoting\" preserved  "
    path = _write(tmp_path, package)
    text, _fixed = sanitise_package(path)
    assert json.loads(text)["description"] == package["description"]
    assert json.loads(text)["transferDetails"][0]["transferObject"]["summary"] \
        == package["transferDetails"][0]["transferObject"]["summary"]


def test_a_package_with_no_report_is_not_an_error(tmp_path):
    path = _write(tmp_path, {"transferDetails": [
        {"transferObject": {"summary": {"type": "jobDefinition", "name": "j"},
                            "content": "not an envelope"}}]})
    result = check_package(path)
    assert result.ok and result.reports_checked == 0


# ---- the packages this repository actually ships --------------------------
@pytest.mark.parametrize("package", default_packages(REPO_ROOT),
                         ids=lambda p: p.name)
def test_shipped_packages_name_no_environment(package):
    """The guard that makes this automatic.

    An export always reintroduces the exporting deployment's hostname, and all
    but one occurrence is invisible to any ordinary search. This is what turns
    "remember to sanitise it" into something that fails on its own.
    """
    result = check_package(package)
    assert result.ok, "\n".join(
        f"{f.package}: {f.report} names {f.host} x{f.occurrences} in {f.where}"
        for f in result.findings)
