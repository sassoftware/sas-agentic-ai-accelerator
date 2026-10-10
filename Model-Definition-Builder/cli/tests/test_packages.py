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


def test_objects_that_do_not_belong_in_a_package_can_be_left_out():
    """By name, whatever the type: the demo report someone parked in the
    folder, a scratch job definition, a stray file."""
    from mdb.core.packages import drop_objects
    package = {
        "transferObjectCount": 5,
        "transferDetails": [
            {"transferObject": {"summary": {"type": "folder", "name": "RAG"}}},
            {"transferObject": {"summary": {"type": "flowStep", "name": "RAG - Embed Chunks.step"}}},
            {"transferObject": {"summary": {"type": "report", "name": "LIVE_DEMO"}}},
            {"transferObject": {"summary": {"type": "jobDefinition", "name": "Scratch-Job"}}},
            {"transferObject": {"summary": {"type": "application/vnd.sas.file+json", "name": "notes.txt"}}},
        ],
    }
    assert drop_objects(package, ["live_demo"]) == ["report LIVE_DEMO"]
    assert drop_objects(package, ["Scratch-Job", "notes.txt"]) == [
        "jobDefinition Scratch-Job", "application/vnd.sas.file+json notes.txt"]
    assert [d["transferObject"]["summary"]["name"] for d in package["transferDetails"]] == [
        "RAG", "RAG - Embed Chunks.step"]
    assert package["transferObjectCount"] == 2
    assert drop_objects(package, ["nothing here", ""]) == [] and package["transferObjectCount"] == 2


# ---- what package-export says when a host is still there ------------------
def test_a_failed_check_is_described_host_by_host(tmp_path):
    """package-export ends with this text when the rewrite left a host behind.
    It has to say which report and where, or the message is "something is
    wrong" about a file nobody can read."""
    from mdb.core.packages import describe_findings
    path = _write(tmp_path, _package(
        deflate(_bird(INTERNAL)),
        {"VisualElement:ve8": f"https://{INTERNAL}/SASJobExecution/"}))
    result = check_package(path)
    assert describe_findings(result) == (
        f"Test Builder: {INTERNAL} x1 in plain text; "
        f"Test Builder: {INTERNAL} x2 in compressed report content")
    assert describe_findings(check_package(_write(tmp_path, _package(deflate(_bird(PLACEHOLDER_HOST)))))) == ""


def test_package_export_reports_a_remaining_host_instead_of_crashing(tmp_path, monkeypatch):
    """The whole failure path of the command, with the service faked: an
    export whose report names a host the rewrite does not touch. The command
    must end with the finding, not with an AttributeError."""
    import io
    from types import SimpleNamespace

    from rich.console import Console
    from typer.testing import CliRunner

    from mdb import cli
    from mdb.core import packages
    from mdb.viya import transfer

    exported = json.dumps(_package(deflate(_bird(INTERNAL))))
    job = {"state": "completed", "packageUri": "/transfer/packages/p1",
           "links": [{"rel": "self", "href": "/transfer/exportJobs/j1"}]}
    monkeypatch.setattr(transfer, "folder_by_path", lambda session, path: {"id": "f1"})
    monkeypatch.setattr(transfer, "start_export", lambda session, name, items: job)
    monkeypatch.setattr(transfer, "wait_for_job", lambda session, href, timeout=600: job)
    monkeypatch.setattr(transfer, "download_package", lambda session, uri: exported)
    monkeypatch.setattr(transfer, "delete_package", lambda session, uri: None)
    # a rewrite that fixes nothing: what is left is what the check must report
    monkeypatch.setattr(packages, "sanitise_package", lambda path, allowed=None: ("", {}))

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(cli, "_viya_session", lambda: _Session())
    monkeypatch.setattr(cli, "Context", lambda: SimpleNamespace(repo=tmp_path))
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, width=400, force_terminal=False))

    target = tmp_path / "exported.json"
    outcome = CliRunner().invoke(cli.app, ["package-export", "--out", str(target)])
    assert outcome.exit_code == 1, outcome.output
    assert not isinstance(outcome.exception, AttributeError)
    assert (f"exported.json still names a host: Test Builder: {INTERNAL} x2 "
            "in compressed report content") in out.getvalue()


# ---- per-user report states ------------------------------------------------
def _wrapped(report_xml: str, states=()) -> str:
    """Report content the way the transfer service stores it: a JSON wrapper
    around the XML, with one full copy of the report per saved user state."""
    return deflate(json.dumps({
        "object": {"id": "r1", "name": "Test Builder"},
        "transferableContent": {
            "content": report_xml, "contentVersion": 7,
            "reportStates": [{"content": xml, "contentVersion": 7,
                              "reportState": {"id": f"s{i}", "userId": f"user{i}"}}
                             for i, xml in enumerate(states)] or None,
        },
    }, separators=(",", ":"), ensure_ascii=False))


def test_report_states_are_counted_and_removed(tmp_path):
    from mdb.core.packages import report_copies, strip_report_states
    clean = _bird(PLACEHOLDER_HOST)
    package = _package(_wrapped(clean, states=[clean, _bird(INTERNAL)]))
    package["description"] = "  spacing   preserved  "
    path = _write(tmp_path, package)

    result = check_package(path)
    assert [(s.package, s.report, s.states) for s in result.states] == [("pkg.json", "Test Builder", 2)]
    # a host that only a user's state names is still a host the package names
    assert [(f.host, f.compressed) for f in result.findings] == [(INTERNAL, True)]

    text, removed = strip_report_states(path.read_text(encoding="utf-8"))
    assert removed == {"Test Builder": 2}
    path.write_text(text, encoding="utf-8")
    after = check_package(path)
    assert after.ok and after.states == []
    stripped = json.loads(text)
    obj = stripped["transferDetails"][0]["transferObject"]
    assert report_copies(obj) == [clean]
    wrapper = json.loads(inflate(obj["content"]))
    assert wrapper["transferableContent"]["reportStates"] is None
    assert "user0" not in inflate(obj["content"])
    # everything outside the report content is as it was
    assert stripped["description"] == package["description"]
    assert obj["summary"] == package["transferDetails"][0]["transferObject"]["summary"]
    # a package without states comes back byte for byte
    assert strip_report_states(text) == (text, {})


def test_a_report_without_the_wrapper_has_one_copy_and_no_states(tmp_path):
    from mdb.core.packages import report_copies, strip_report_states
    package = _package(deflate(_bird(PLACEHOLDER_HOST)))
    assert report_copies(package["transferDetails"][0]["transferObject"]) == [_bird(PLACEHOLDER_HOST)]
    path = _write(tmp_path, package)
    assert check_package(path).states == []
    text = path.read_text(encoding="utf-8")
    assert strip_report_states(text) == (text, {})
