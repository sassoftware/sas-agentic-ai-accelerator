# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The Call LLM node under one id: `mdb decision-node-migrate`, and the
decision of the content folder that carries the node type - in the manifest,
in `mdb package-export` / `mdb package-check`, in `mdb content-deploy` and in
`mdb builders-import`.

SAS Intelligent Decisioning and the transfer service are faked with the
shapes read on viya4-s2 on 2026-10-20 (research r31): a node type is listed
and read under /decisions/decisionNodeTypes, renamed by a PUT under an ETag
and created under a chosen id by a POST with `id`; a decision lists its
versions under /revisions and each version names its node type in the
`customObject.uri` of a step; a re-import of a decision over itself with a
`dntStatic` mapping rewrites every version, locked ones included, and keeps
their ids; an import job can end `completed` with a task that failed, or with
no task at all for an object it skipped.
"""
import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console
from typer.testing import CliRunner

from content_fakes import CONTENT_ROOT, Package, Viya, make_repo, package_of
from mdb import cli
from mdb.core import content
from mdb.core.content import ManifestError, load_manifest, parse_manifest
from mdb.core.packages import (decisions_package, filter_package, named_users, neutralise_users, package_findings,
                               PackageSource)
from mdb.viya import content as viya_content
from mdb.viya import decision_nodes, transfer
from mdb.viya.decision_nodes import CALL_LLM_NODE_ID, CALL_LLM_NODE_NAME

# the fixtures of `mdb builders-import`, with a fake transfer service and a SAS Viya in memory
from test_builder_commands import (NO_CONTEXT, Site, _built, discovery, importing, no_discovery, out, site)  # noqa: F401

REPO_ROOT = Path(__file__).resolve().parents[3]
OLD = "0a0a0a0a-0000-4000-8000-000000000001"
NODE = "/decisions/decisionNodeTypes/"


# ---------------------------------------------------------------------------
# SAS Intelligent Decisioning and the transfer service, in memory
# ---------------------------------------------------------------------------
class Response:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self.ok = status < 400
        self._body = body
        self.text = json.dumps(body) if body is not None else ""
        self.headers = headers or {}

    def json(self):
        return self._body


class SID:
    """Node types (with their content and an ETag) and decisions (with their
    versions, each a list of the node type addresses its steps call), and
    every request that changed something."""

    def __init__(self):
        self.nodes: dict = {}         # id -> node type
        self.contents: dict = {}      # id -> content
        self.etags: dict = {}
        self.decisions: dict = {}     # id -> {"name", "versions": [{"id", "major", "uris"}]}
        self.writes: list = []
        #: the most items a page of decisions or versions holds, whatever limit is asked for (a service caps it)
        self.page_cap = None
        #: the most node types the listing gives - it names that limit in its answer
        self.node_type_cap = None
        #: the parameters of each listing of the node types
        self.node_type_listings: list = []

    def node(self, node_id, name, code="package x; endpackage;"):
        self.nodes[node_id] = {"id": node_id, "name": name, "type": "static", "themeId": "DNT_THEME1",
                               "hasInputs": True, "description": "the node", "links": [{"rel": "self"}]}
        self.contents[node_id] = {"id": f"c-{node_id}", "nodeTypeId": node_id, "contentType": "DS2",
                                  "staticContent": code, "nodeTypeSignatureTerms": [
                                      {"name": "llmBody", "dataType": "string", "length": 10485760,
                                       "direction": "input", "generateDataGridColumns": False, "id": "t1",
                                       "createdBy": "someone"}]}
        self.etags[node_id] = 1
        return node_id

    def decision(self, decision_id, name, *versions):
        """`versions`: the node type ids the steps of each version call, oldest first - None for a step that
        lost the address of its node (its node type was deleted)."""
        self.decisions[decision_id] = {"name": name, "versions": [
            {"id": f"{decision_id}-v{major}", "major": major, "uris": [NODE + node if node else None for node in nodes]}
            for major, nodes in enumerate(versions, start=1)]}

    def uses(self, decision_id) -> list:
        return [version["uris"] for version in self.decisions[decision_id]["versions"]]

    # -- the session ----------------------------------------------------------
    def get(self, path, params=None, headers=None, **kw):
        params = params or {}
        start, limit = int(params.get("start", 0)), int(params.get("limit", 20))
        if path == "/decisions/decisionNodeTypes":
            # a listing gives the summary of a node type, not its settings (measured: a POST of such a
            # summary is refused with 400 'The required property "hasInputs" is missing'); its count is the
            # size of the page, not the total (measured on 2026-10-21)
            self.node_type_listings.append(dict(params))
            limit = min(limit, self.node_type_cap or limit)
            items = [{"id": n["id"], "name": n["name"], "type": n.get("type")}
                     for n in self.nodes.values()][start:start + limit]
            return Response(200, {"items": items, "count": len(items), "start": start, "limit": limit})
        if self.page_cap:
            limit = min(limit, self.page_cap)

        def page(items):
            return Response(200, {"items": items[start:start + limit], "count": len(items)})
        if path.startswith(NODE):
            parts = path[len(NODE):].split("/")
            if parts[0] not in self.nodes:
                return Response(404, {"message": "not found"})
            if parts[1:] == ["content"]:
                return Response(200, self.contents[parts[0]]) if parts[0] in self.contents else \
                    Response(404, {"message": "no content"})
            return Response(200, self.nodes[parts[0]], {"ETag": f'"{self.etags[parts[0]]}"'})
        if path == "/decisions/flows":
            return page([{"id": d, "name": v["name"]} for d, v in self.decisions.items()])
        if path.startswith("/decisions/flows/"):
            parts = path[len("/decisions/flows/"):].split("/")
            decision = self.decisions.get(parts[0])
            if decision is None:
                return Response(404, {"message": "not found"})
            if parts[1:] == ["revisions"]:
                return page([{"id": v["id"], "majorRevision": v["major"], "minorRevision": 0}
                             for v in decision["versions"]])
            if len(parts) == 3 and parts[1] == "revisions":
                version = next((v for v in decision["versions"] if v["id"] == parts[2]), None)
                if version is None:
                    return Response(404, {"message": "not found"})
                return Response(200, {"id": version["id"], "flow": {"steps": [
                    {"customObject": {"name": "node", "type": "dntStatic", **({"uri": uri} if uri else {})}}
                    for uri in version["uris"]]}})
        return Response(404, {"message": "not found"})

    def post(self, path, params=None, data=None, headers=None, **kw):
        self.writes.append(("POST", path))
        body = json.loads(data)
        if path == "/decisions/decisionNodeTypes":
            if "hasInputs" not in body:
                return Response(400, {"message": 'The required property "hasInputs" is missing'})
            if body["id"] in self.nodes:
                # what was measured: answered 400, and the node type renamed all the same
                self.nodes[body["id"]]["name"] = body["name"]
                return Response(400, {"message": "rule exists"})
            if any(n["name"] == body["name"] for n in self.nodes.values()):
                return Response(409, {"message": "Decision node type names must be unique."})
            self.nodes[body["id"]] = {**body, "links": []}
            self.etags[body["id"]] = 1
            return Response(201, self.nodes[body["id"]])
        if path.startswith(NODE) and path.endswith("/content"):
            node_id = path[len(NODE):].split("/")[0]
            self.contents[node_id] = {**body, "nodeTypeId": node_id}
            return Response(201, self.contents[node_id])
        return Response(400, {"message": "bad request"})

    def put(self, path, data=None, headers=None, **kw):
        self.writes.append(("PUT", path))
        node_id = path[len(NODE):]
        if headers.get("If-Match") != f'"{self.etags[node_id]}"':
            return Response(412, {"message": "precondition failed"})
        body = json.loads(data)
        if any(n["name"] == body["name"] and i != node_id for i, n in self.nodes.items()):
            return Response(409, {"message": "Decision node type names must be unique."})
        assert "id" not in body or body["id"] == node_id
        self.nodes[node_id] = {**self.nodes[node_id], "name": body["name"]}
        self.etags[node_id] += 1
        return Response(200, self.nodes[node_id])

    def delete(self, path, **kw):
        self.writes.append(("DELETE", path))
        node_id = path[len(NODE):]
        self.nodes.pop(node_id, None)
        self.contents.pop(node_id, None)
        # what was measured: every step that called it, in every version, loses the address
        for decision in self.decisions.values():
            for version in decision["versions"]:
                version["uris"] = [None if uri == path else uri for uri in version["uris"]]
        return Response(204)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


@pytest.fixture
def sid(monkeypatch):
    """A site and its transfer service. `sid.fail_import` names decisions whose
    import task fails, `sid.skip_import` decisions the job leaves out without a
    task, `sid.after_import` is called after each import (a decision someone
    makes meanwhile). `sid.mappings` holds the mapping of each import."""
    site = SID()
    site.fail_import, site.skip_import, site.mappings, site.deleted = set(), set(), [], []
    #: decisions whose import ends completed with a task, and moves only their current version
    site.half_import = set()
    site.after_import = None
    jobs: dict = {}
    uploads: dict = {}

    def start_export(session, name, items, description=""):
        uri = f"/transfer/exportJobs/e{len(jobs)}"
        (decision_uri,) = items
        jobs[uri] = {"state": "completed", "packageUri": f"/transfer/packages/x{len(jobs)}",
                     "links": [{"rel": "self", "href": uri}], "decision": decision_uri.rsplit("/", 1)[-1]}
        return jobs[uri]

    def download_package(session, package_uri):
        job = next(j for j in jobs.values() if j.get("packageUri") == package_uri)
        decision_id = job["decision"]
        built = Package(root="Team", above=())
        used = sorted({node.rsplit("/", 1)[-1] for uris in site.uses(decision_id) for node in uris if node})
        for node_id in used:
            built.node_type(node_id, site.nodes.get(node_id, {}).get("name", "gone"))
        uri = built.decision("Decisions/" + site.decisions[decision_id]["name"],
                             [(node_id, site.nodes.get(node_id, {}).get("name", "gone")) for node_id in used])
        package = built.build()
        # the decision as the export names it: its own address
        for detail in package["transferDetails"]:
            summary = detail["transferObject"]["summary"]
            if summary["type"] == "decision":
                summary["id"] = decision_id
                summary["links"][0]["uri"] = summary["links"][0]["href"] = f"/decisions/flows/{decision_id}"
        assert uri
        return json.dumps(package)

    def upload_package_bytes(session, name, data):
        uploads[f"u{len(uploads)}"] = json.loads(data)
        return {"id": f"u{len(uploads) - 1}"}

    def start_import(session, name, package_uri, mapping=None, description=""):
        package = uploads[package_uri.rsplit("/", 1)[-1]]
        site.mappings.append(mapping)
        kinds = [d["transferObject"]["summary"]["type"] for d in package["transferDetails"]]
        assert kinds == ["decision"], "only the decision object is handed to the import"
        decision_id = package["transferDetails"][0]["transferObject"]["summary"]["id"]
        name_of = site.decisions[decision_id]["name"]
        tasks = []
        if decision_id in site.fail_import:
            tasks.append({"resourceType": "decision", "name": name_of, "state": "failed", "message": "locked"})
        elif decision_id not in site.skip_import:
            moves = {c["source"]: c["target"] for c in mapping["connectors"]["dntStatic"]}
            versions = site.decisions[decision_id]["versions"]
            for version in versions[-1:] if decision_id in site.half_import else versions:
                version["uris"] = [moves.get(uri, uri) for uri in version["uris"]]
            tasks.append({"resourceType": "decision", "name": name_of, "state": "completed"})
        uri = f"/transfer/importJobs/i{len(jobs)}"
        jobs[uri] = {"state": "completed", "links": [{"rel": "self", "href": uri}], "tasks": tasks}
        if site.after_import:
            site.after_import()
        return jobs[uri]

    monkeypatch.setattr(transfer, "start_export", start_export)
    monkeypatch.setattr(transfer, "wait_for_job", lambda session, uri, timeout=600: jobs[uri])
    monkeypatch.setattr(transfer, "download_package", download_package)
    monkeypatch.setattr(transfer, "upload_package_bytes", upload_package_bytes)
    monkeypatch.setattr(transfer, "get_mapping", lambda session, package_id: {"version": 1})
    monkeypatch.setattr(transfer, "start_import", start_import)
    monkeypatch.setattr(transfer, "job_tasks", lambda session, job: job["tasks"])
    monkeypatch.setattr(transfer, "job_messages", lambda session, job: [])
    monkeypatch.setattr(transfer, "delete_package", lambda session, uri: site.deleted.append(uri))
    monkeypatch.setattr(transfer, "delete_job", lambda session, uri: site.deleted.append(uri))
    monkeypatch.setattr(cli, "_viya_session", lambda: site)
    return site


TARGET = "1b1b1b1b-0000-4000-8000-000000000002"
#: The program that gives the Call LLM node the code and the terms of the release.
UPDATE_PROGRAM = ("SAS-Viya-Integrations/SAS-Intelligent-Decisioning-Integration/"
                  "Update-Custom-SAS-Intelligent-Decisioning-Node.sas")


def migrate(*args):
    """`mdb decision-node-migrate` on a throwaway node - (exit code, what it printed)."""
    buffer = io.StringIO()
    console = Console(file=buffer, width=1000, force_terminal=False)
    original, cli.console = cli.console, console
    try:
        result = CliRunner().invoke(cli.app, ["decision-node-migrate", "--node-name", "ZZ Call",
                                              "--target-id", TARGET, *args])
    finally:
        cli.console = original
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
    return result.exit_code, buffer.getvalue()


# ---------------------------------------------------------------------------
# mdb decision-node-migrate
# ---------------------------------------------------------------------------
def test_the_defaults_are_the_call_llm_node_of_the_package():
    assert (CALL_LLM_NODE_NAME, CALL_LLM_NODE_ID) == ("Call LLM", "f9efcbb7-f57b-4a62-b56d-1a89595e2039")
    entry = load_manifest(REPO_ROOT, check_sources=False).find("Decisions/Call LLM Node", "decision")
    assert entry.node_types == ({"name": CALL_LLM_NODE_NAME, "id": CALL_LLM_NODE_ID, "content": UPDATE_PROGRAM},)
    assert cli._migrate_command(CALL_LLM_NODE_NAME, CALL_LLM_NODE_ID, apply=True) == \
        "mdb decision-node-migrate --apply"
    assert cli._migrate_command("ZZ Call", TARGET) == \
        f'mdb decision-node-migrate --node-name "ZZ Call" --target-id {TARGET}'


def test_a_node_that_has_the_id_already_is_left_alone(sid):
    sid.node(TARGET, "ZZ Call")
    sid.decision("d1", "Uses it", [TARGET])
    code, text = migrate("--apply", "--delete-old")
    assert code == 0, text
    assert "Node type 'ZZ Call': it has the id 1b1b1b1b-0000-4000-8000-000000000002 already." in text
    assert "Nothing to do." in text and sid.writes == []


def test_a_target_id_that_is_taken_is_refused_and_nothing_is_sent(sid):
    sid.node(OLD, "ZZ Call")
    sid.node(TARGET, "Someone else")
    code, text = migrate("--apply")
    assert code == 1
    assert "The id 1b1b1b1b-0000-4000-8000-000000000002 is taken: the node type 'Someone else' has it." in text
    assert sid.writes == [] and sid.nodes[TARGET]["name"] == "Someone else"


def test_a_target_id_that_is_no_uuid_is_refused_before_signing_in(monkeypatch):
    monkeypatch.setattr(cli, "_viya_session", lambda: pytest.fail("signed in"))
    result = CliRunner().invoke(cli.app, ["decision-node-migrate", "--target-id", "F9EFCBB7"])
    assert result.exit_code == 2


def test_a_dry_run_lists_every_version_and_changes_nothing(sid):
    sid.node(OLD, "ZZ Call")
    sid.decision("d1", "Locked and current", [OLD], [OLD])
    sid.decision("d2", "Other node", ["2c2c2c2c-0000-4000-8000-000000000003"])
    code, text = migrate()
    assert code == 0, text
    assert "1 decision(s) call the old node type:" in text
    assert "  'Locked and current' (d1): version(s) 1.0 (locked), 2.0 (current)" in text
    assert "Other node" not in text
    assert "  1. rename the node type 0a0a0a0a-0000-4000-8000-000000000001 from 'ZZ Call' to " \
           "'ZZ Call (before 1b1b1b1b)'" in text
    assert "Dry run - nothing was changed." in text
    assert sid.writes == [] and sid.mappings == []


def test_without_decisions_the_node_is_renamed_and_created_under_the_id(sid):
    sid.node(OLD, "ZZ Call", code="the code")
    code, text = migrate("--apply")
    assert code == 0, text
    assert "No decision calls the old node type." in text
    assert sid.nodes[OLD]["name"] == "ZZ Call (before 1b1b1b1b)"
    assert sid.nodes[TARGET]["name"] == "ZZ Call" and sid.nodes[TARGET]["themeId"] == "DNT_THEME1"
    assert sid.contents[TARGET]["staticContent"] == "the code"
    # the terms are copied without what belongs to the old node type
    assert sid.contents[TARGET]["nodeTypeSignatureTerms"] == [
        {"name": "llmBody", "dataType": "string", "length": 10485760, "direction": "input",
         "generateDataGridColumns": False}]
    assert sid.mappings == [] and OLD in sid.nodes                      # kept without --delete-old
    assert "The old node type 'ZZ Call (before 1b1b1b1b)' (0a0a0a0a-0000-4000-8000-000000000001) is kept" in text


def test_locked_versions_are_moved_by_a_re_import_and_read_back(sid):
    sid.node(OLD, "ZZ Call")
    sid.decision("d1", "Locked and current", [OLD], [OLD])
    code, text = migrate("--apply")
    assert code == 0, text
    assert sid.uses("d1") == [[NODE + TARGET], [NODE + TARGET]]
    assert [v["id"] for v in sid.decisions["d1"]["versions"]] == ["d1-v1", "d1-v2"]
    (mapping,) = sid.mappings
    (moved,) = mapping["connectors"]["dntStatic"]
    assert (moved["source"], moved["target"], moved["resourceName"]) == (NODE + OLD, NODE + TARGET, "ZZ Call (before 1b1b1b1b)")
    assert moved["connectorId"]
    assert "moved       decision 'Locked and current' (d1): version(s) 1.0 (locked), 2.0 (current) call " \
           f"{NODE}{TARGET} - read back" in text
    # the export, its job record and the upload are deleted; the record of the import is kept
    assert sorted(sid.deleted) == ["/transfer/exportJobs/e0", "/transfer/packages/u0", "/transfer/packages/x0"]


def test_a_failed_import_task_fails_the_run_and_the_old_node_is_not_deleted(sid):
    sid.node(OLD, "ZZ Call")
    sid.decision("d1", "Fails", [OLD], [OLD])
    sid.decision("d2", "Moves", [OLD])
    sid.fail_import.add("d1")
    code, text = migrate("--apply", "--delete-old")
    assert code == 1
    assert "FAILED      decision 'Fails' (d1):" in text
    assert "import task decision 'Fails': failed - locked" in text
    assert sid.uses("d2") == [[NODE + TARGET]] and sid.uses("d1") == [[NODE + OLD], [NODE + OLD]]
    assert "not deleted 0a0a0a0a-0000-4000-8000-000000000001: 1 decision(s) were not moved and still call it." in text
    assert OLD in sid.nodes and ("DELETE", NODE + OLD) not in sid.writes


def test_a_decision_the_import_job_left_out_without_a_task_is_a_failure(sid):
    sid.node(OLD, "ZZ Call")
    sid.decision("d1", "Skipped", [OLD])
    sid.skip_import.add("d1")
    code, text = migrate("--apply")
    assert code == 1
    assert ("the import job ended completed and has no completed task for the decision 'Skipped' - it was not "
            "imported") in text


def test_delete_old_is_refused_while_a_version_still_calls_the_old_node(sid):
    sid.node(OLD, "ZZ Call")
    sid.decision("d1", "Moves", [OLD])
    # somebody makes a decision with the old node type while the run moves the others
    sid.after_import = lambda: sid.decision("d9", "Made meanwhile", [OLD])
    code, text = migrate("--apply", "--delete-old")
    assert code == 1
    assert "not deleted 0a0a0a0a-0000-4000-8000-000000000001: 1 decision(s) still call it - 'Made meanwhile' " \
           "1.0 (current)." in text
    assert OLD in sid.nodes


def test_delete_old_deletes_once_nothing_calls_it(sid):
    sid.node(OLD, "ZZ Call")
    sid.decision("d1", "Moves", [OLD], [OLD])
    code, text = migrate("--apply", "--delete-old")
    assert code == 0, text
    assert OLD not in sid.nodes and sid.uses("d1") == [[NODE + TARGET], [NODE + TARGET]]
    assert "deleted     0a0a0a0a-0000-4000-8000-000000000001 ('ZZ Call (before 1b1b1b1b)')" in text


def test_a_second_run_finds_everything_done(sid):
    sid.node(OLD, "ZZ Call")
    sid.decision("d1", "Moves", [OLD], [OLD])
    assert migrate("--apply")[0] == 0
    writes = list(sid.writes)
    code, text = migrate("--apply")
    assert code == 0, text
    assert sid.writes == writes                                     # nothing sent again
    assert "The old node type 'ZZ Call (before 1b1b1b1b)'" in text and "No decision calls the old node type." in text


def test_a_run_that_stopped_after_the_rename_is_finished_by_the_next(sid):
    sid.node(OLD, "ZZ Call (before 1b1b1b1b)")                      # an earlier run renamed it, and stopped
    sid.decision("d1", "Moves", [OLD])
    code, text = migrate("--apply")
    assert code == 0, text
    assert ("PUT", NODE + OLD) not in sid.writes
    assert sid.nodes[TARGET]["name"] == "ZZ Call" and sid.uses("d1") == [[NODE + TARGET]]


def test_a_run_that_stopped_before_the_content_gives_the_node_its_content(sid):
    sid.node(OLD, "ZZ Call (before 1b1b1b1b)", code="old code")
    sid.node(TARGET, "ZZ Call")
    del sid.contents[TARGET]
    code, text = migrate("--apply")
    assert code == 0, text
    assert sid.contents[TARGET]["staticContent"] == "old code"


def test_no_node_of_the_name_is_nothing_to_move(sid):
    code, text = migrate("--apply")
    assert code == 0
    assert (f"No node type named 'ZZ Call' is on this site, and none under the id {TARGET}: there is no node type to "
            "give that id.") in text and sid.writes == []


def test_a_refused_rename_stops_before_anything_else(sid, monkeypatch):
    sid.node(OLD, "ZZ Call")
    sid.decision("d1", "Moves", [OLD])
    monkeypatch.setattr(sid, "put", lambda path, data=None, headers=None, **kw: Response(403, {"message": "no"}))
    code, text = migrate("--apply")
    assert code == 1
    assert "FAILED      Renaming the node type 0a0a0a0a-0000-4000-8000-000000000001 to 'ZZ Call (before 1b1b1b1b)' " \
           "failed: HTTP 403 no" in text
    assert TARGET not in sid.nodes and sid.mappings == []


def test_the_target_id_is_asked_again_right_before_the_post(sid, monkeypatch):
    """Somebody creates the target id between the first look and the POST: no POST of an id that exists."""
    sid.node(OLD, "ZZ Call")
    rename = sid.put

    def put_and_meanwhile_create_the_target(path, data=None, headers=None, **kw):
        answer = rename(path, data=data, headers=headers, **kw)
        sid.node(TARGET, "Made meanwhile")
        return answer
    monkeypatch.setattr(sid, "put", put_and_meanwhile_create_the_target)
    code, text = migrate("--apply")
    assert code == 1
    assert f"The id {TARGET} was taken in the meantime. Stopped: no node type was created." in text
    assert ("POST", "/decisions/decisionNodeTypes") not in sid.writes
    assert sid.nodes[TARGET]["name"] == "Made meanwhile"


def test_a_locked_version_the_import_left_on_the_old_node_is_found_by_the_read_back(sid):
    """The import job ends completed with a completed task, and moved only the current version."""
    sid.node(OLD, "ZZ Call")
    sid.decision("d1", "Half moved", [OLD], [OLD])
    sid.half_import.add("d1")
    code, text = migrate("--apply", "--delete-old")
    assert code == 1
    assert "FAILED      decision 'Half moved' (d1):" in text
    assert f"version 1.0 (locked) calls {NODE}{OLD} after the import" in text
    assert OLD in sid.nodes and ("DELETE", NODE + OLD) not in sid.writes


def test_every_page_of_decisions_and_versions_is_read_and_the_node_types_in_one(sid):
    """A service gives at most a few decisions or versions a page, whatever limit is asked for. The node types are
    asked for in one page of 1000: their listing gives no total, and `start` does not page them."""
    for number in range(3):
        sid.node(f"5e5e5e5e-0000-4000-8000-00000000000{number}", f"Another node {number}", code=f"other {number}")
    sid.node(OLD, "ZZ Call")                                  # the last of four node types
    for number in range(3):
        sid.decision(f"d{number}", f"Other {number}", ["5e5e5e5e-0000-4000-8000-000000000000"])
    sid.decision("d9", "Third version calls it", ["5e5e5e5e-0000-4000-8000-000000000001"],
                 ["5e5e5e5e-0000-4000-8000-000000000001"], [OLD])  # the last decision, in its last version
    sid.page_cap = 1
    code, text = migrate("--apply")
    assert code == 0, text
    assert sid.node_type_listings and all(params == {"start": 0, "limit": 1000} for params in sid.node_type_listings)
    assert "  4 decision(s) on this site." in text and "full page" not in text
    assert "  'Third version calls it' (d9): version(s) 3.0 (current)" in text
    assert sid.uses("d9")[2] == [NODE + TARGET]


FULL_PAGE = "custom node types, a full page, and does not say how many there are: the list may be incomplete"


@pytest.mark.parametrize("count, warned", [(3, True), (2, False)])
def test_a_full_page_of_node_types_is_a_warning_and_fewer_are_not(sid, monkeypatch, count, warned):
    monkeypatch.setattr(decision_nodes, "NODE_TYPE_LIMIT", 3)
    sid.node(TARGET, "ZZ Call")
    for number in range(count - 1):
        sid.node(f"5e5e5e5e-0000-4000-8000-00000000000{number}", f"Another node {number}", code=f"other {number}")
    code, text = migrate()
    assert code == 0, text
    assert sid.node_type_listings == [{"start": 0, "limit": 3}]
    assert (f"Warning: SAS Intelligent Decisioning listed 3 {FULL_PAGE}, and a node type past it is not looked "
            "at." in text) is warned, text


def test_a_page_of_node_types_the_service_capped_is_full_at_its_own_limit(sid):
    sid.node(TARGET, "ZZ Call")
    sid.node(OLD, "Another node", code="other")
    sid.node_type_cap = 2
    code, text = migrate()
    assert code == 0, text
    assert f"Warning: SAS Intelligent Decisioning listed 2 {FULL_PAGE}" in text


def test_a_site_without_decisioning_has_nothing_to_migrate(monkeypatch):
    class NoDecisioning:
        def get(self, path, **kw):
            return Response(404, {"message": "Not Found"})

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False
    monkeypatch.setattr(cli, "_viya_session", lambda: NoDecisioning())
    code, text = migrate("--apply", "--delete-old")
    assert code == 0, text
    assert text == ("This site has no SAS Intelligent Decisioning (GET /decisions/decisionNodeTypes answers 404): there "
                    "is no node type and no decision to migrate.\n")


# ---- the node type of the id is there, and decisions call another one like it ----
HAND = "6f6f6f6f-0000-4000-8000-000000000006"


def test_a_node_renamed_by_hand_is_listed_with_its_decisions_and_nothing_says_it_is_unused(sid):
    """The site renamed its old node type by hand to free the name, then got the node type of the id (the package or
    the Create program): the decisions still call the old one."""
    sid.node(OLD, "ZZ Call old")
    sid.node(TARGET, "ZZ Call")
    sid.decision("d1", "Uses old", [OLD], [OLD])
    code, text = migrate("--apply", "--delete-old")
    assert code == 1
    assert (f"The node type 'ZZ Call old' ({OLD}) - its name starts with 'ZZ Call' and it has the code of {TARGET} - is "
            "called by 1 decision(s), which this run does not move:") in text
    assert "  'Uses old' (d1): version(s) 1.0 (locked), 2.0 (current)" in text
    assert f'  `mdb decision-node-migrate --node-name "ZZ Call" --target-id {TARGET} --from-id {OLD} --apply` moves ' \
           f"them to {TARGET}." in text
    assert "--delete-old: nothing is deleted" in text
    for wrong in ("there is nothing to move", "no old node type to delete", "Nothing to do"):
        assert wrong not in text
    assert sid.writes == [] and sid.uses("d1") == [[NODE + OLD], [NODE + OLD]]
    # a dry run says the same and ends with 0
    code, text = migrate()
    assert code == 0 and "'Uses old' (d1): version(s) 1.0 (locked), 2.0 (current)" in text


def test_from_id_moves_the_decisions_to_the_node_type_that_is_there_and_creates_nothing(sid):
    sid.node(OLD, "ZZ Call old")
    sid.node(TARGET, "ZZ Call")
    sid.decision("d1", "Uses old", [OLD], [OLD])
    code, text = migrate("--from-id", OLD)
    assert code == 0, text
    assert f"--from-id: the decisions that call the node type 'ZZ Call old' ({OLD}) are moved to {TARGET}" in text
    assert "  1. move 1 decision(s)" in text and sid.writes == []
    code, text = migrate("--from-id", OLD, "--apply", "--delete-old")
    assert code == 0, text
    assert sid.uses("d1") == [[NODE + TARGET], [NODE + TARGET]]
    assert f"deleted     {OLD} ('ZZ Call old'): no version of any decision calls it" in text
    # nothing was renamed or created: the writes are the delete of the old node type alone
    assert sid.writes == [("DELETE", NODE + OLD)] and sid.nodes[TARGET]["name"] == "ZZ Call"


def test_a_node_with_the_code_of_the_target_counts_whatever_its_name(sid):
    sid.node(HAND, "Our LLM node", code="the code")
    sid.node(TARGET, "ZZ Call", code="the code\r\n")
    sid.node(OLD, "Something else", code="other code")
    sid.decision("d1", "Uses the copy", [HAND])
    sid.decision("d2", "Uses another node", [OLD])
    code, text = migrate()
    assert code == 0, text
    assert (f"The node type 'Our LLM node' ({HAND}) - it has the code of {TARGET} - is called by 1 decision(s)") in text
    assert "Something else" not in text and "Uses another node" not in text


def test_a_node_type_whose_content_cannot_be_read_is_not_taken_for_a_copy(sid, monkeypatch):
    sid.node(TARGET, "ZZ Call", code="the code")
    sid.node(OLD, "A REST node type")
    read = sid.get

    def refusing_its_content(path, **kw):
        if path == f"{NODE}{OLD}/content":
            return Response(500, {"message": "not a static node type"})
        return read(path, **kw)
    monkeypatch.setattr(sid, "get", refusing_its_content)
    code, text = migrate()
    assert code == 0, text
    assert "A REST node type" not in text and "Nothing to do." in text


def test_a_node_like_the_target_that_no_decision_calls_is_said_so_and_kept(sid):
    sid.node(OLD, "ZZ Call (before 1b1b1b1b) copy")
    sid.node(TARGET, "ZZ Call")
    code, text = migrate("--apply", "--delete-old")
    assert code == 0, text
    assert f"The node type 'ZZ Call (before 1b1b1b1b) copy' ({OLD}) - its name starts with 'ZZ Call' and it has the " \
           f"code of {TARGET} - is called by no version of any decision." in text
    assert OLD in sid.nodes and sid.writes == []


def test_from_id_of_a_node_like_no_rule_is_moved_with_a_warning(sid):
    sid.node(OLD, "Something else", code="other code")
    sid.node(TARGET, "ZZ Call")
    sid.decision("d1", "Uses it", [OLD])
    code, text = migrate("--from-id", OLD, "--apply")
    assert code == 0, text
    assert f"  'Something else' does not have the code of {TARGET}: check that it is a copy of that node type" in text
    assert sid.uses("d1") == [[NODE + TARGET]]
    assert f"The old node type 'Something else' ({OLD}) is kept; published modules do not need it. Delete it in SAS " \
           "Intelligent Decisioning once you are sure" in text


def test_delete_old_keeps_a_from_id_node_type_that_is_no_copy(sid):
    """Its decisions are moved, and the node type stays as it was: deleting a node type that is not ours cannot be
    undone. The run ends with 0, as a move does."""
    sid.node(OLD, "Something else", code="other code")
    sid.node(TARGET, "ZZ Call")
    sid.decision("d1", "Uses it", [OLD], [OLD])
    code, text = migrate("--from-id", OLD, "--delete-old")
    assert code == 0, text
    assert f"keep the old node type {OLD} ('Something else') - it is no copy of {TARGET}, so --delete-old does not " \
           "delete it" in text
    assert f"delete the old node type {OLD}" not in text and sid.writes == []
    code, text = migrate("--from-id", OLD, "--apply", "--delete-old")
    assert code == 0, text
    assert f"its decisions get the code and the terms of {TARGET}; --delete-old does not delete it, as it is no " \
           "copy." in text
    assert sid.uses("d1") == [[NODE + TARGET], [NODE + TARGET]]
    assert f"not deleted {OLD} ('Something else'): it does not have the code of {TARGET}, so it is no copy and " \
           "--delete-old keeps it. Delete it in SAS Intelligent Decisioning once you are sure it is not needed." in text
    assert OLD in sid.nodes and ("DELETE", NODE + OLD) not in sid.writes
    assert "deleted     " not in text and "Not everything" not in text


def test_delete_old_deletes_a_from_id_node_type_whose_code_the_target_gets(sid):
    """A target without content gets the code and the terms of the --from-id node type: that one is a copy then,
    and --delete-old deletes it once no version calls it."""
    sid.node(OLD, "Something else", code="other code")
    sid.node(TARGET, "ZZ Call")
    del sid.contents[TARGET]
    sid.decision("d1", "Uses it", [OLD], [OLD])
    code, text = migrate("--from-id", OLD, "--apply", "--delete-old")
    assert code == 0, text
    assert "does not have the code of" not in text and "not deleted" not in text
    assert sid.contents[TARGET]["staticContent"] == "other code"
    assert sid.uses("d1") == [[NODE + TARGET], [NODE + TARGET]]
    assert f"deleted     {OLD} ('Something else'): no version of any decision calls it" in text and OLD not in sid.nodes


# ---- a node type named like the target, with other code, is no copy ----
OTHER = "5e5e5e5e-0000-4000-8000-000000000005"


def test_a_node_named_like_the_target_with_other_code_is_a_warning_and_never_fails_the_run(sid):
    """'ZZ Call Streaming' is another node type: its name starts like the node, its code is another. The first run
    with --apply names it and its decisions, moves only the old node type and ends with 0; so does the run after it."""
    sid.node(OLD, "ZZ Call", code="the call code")
    sid.node(OTHER, "ZZ Call Streaming", code="totally different streaming code")
    sid.decision("d1", "Uses old", [OLD])
    sid.decision("d2", "Uses streaming", [OTHER], [OTHER])
    warning = (f"The node type 'ZZ Call Streaming' ({OTHER}) - its name starts with 'ZZ Call', but its code is not the "
               f"code of {TARGET} - is called by 1 decision(s). It is not taken for a copy of {TARGET}, and this run "
               "does not move them:")
    command = f'`mdb decision-node-migrate --node-name "ZZ Call" --target-id {TARGET} --from-id {OTHER} --apply`'
    code, text = migrate()
    assert code == 0, text
    assert warning in text and "  'Uses streaming' (d2): version(s) 1.0 (locked), 2.0 (current)" in text
    assert f"  Once {TARGET} is there (this command with --apply), if it is an older version of that node type all " \
           f"the same, {command} moves them" in text
    code, text = migrate("--apply")
    assert code == 0, text
    assert warning in text and "Not everything is done" not in text
    assert sid.uses("d1") == [[NODE + TARGET]] and sid.uses("d2") == [[NODE + OTHER], [NODE + OTHER]]
    code, text = migrate("--apply")
    assert code == 0, text
    assert warning in text and f"  If it is an older version of that node type all the same, {command} moves them " \
                               f"to {TARGET} - they then run its code and its terms." in text
    assert "Not everything is done" not in text
    # the command it names warns that the node type is no copy - and moves its decisions where that is meant
    code, text = migrate("--from-id", OTHER, "--apply", "--delete-old")
    assert code == 0, text
    assert f"  'ZZ Call Streaming' does not have the code of {TARGET}, though its name starts with 'ZZ Call': check " \
           f"that it is a copy of that node type - its decisions get the code and the terms of {TARGET}; " \
           "--delete-old does not delete it, as it is no copy." in text
    assert sid.uses("d2") == [[NODE + TARGET], [NODE + TARGET]]
    assert OTHER in sid.nodes and f"not deleted {OTHER} ('ZZ Call Streaming')" in text


def test_a_first_run_that_leaves_decisions_on_a_copy_ends_with_1(sid):
    """A copy of the node type (its code, another name) that decisions call: the first run with --apply moves the
    decisions of the node type it renames, names the copy and ends with exit code 1 - after it moved."""
    sid.node(OLD, "ZZ Call", code="the call code")
    sid.node(HAND, "Our LLM node", code="the call code\r\n")
    sid.decision("d1", "Uses old", [OLD])
    sid.decision("d2", "Uses the copy", [HAND])
    code, text = migrate()
    assert code == 0, text
    assert (f"The node type 'Our LLM node' ({HAND}) - it has the code of {TARGET} - is called by 1 decision(s), which "
            "this run does not move:") in text
    assert f'  Once {TARGET} is there (this command with --apply), `mdb decision-node-migrate --node-name "ZZ Call" ' \
           f"--target-id {TARGET} --from-id {HAND} --apply` moves them to {TARGET}." in text
    code, text = migrate("--apply")
    assert code == 1, text
    assert sid.uses("d1") == [[NODE + TARGET]] and sid.uses("d2") == [[NODE + HAND]]
    assert "moved       decision 'Uses old' (d1)" in text
    assert text.rstrip().endswith(f"Not everything is done: 1 other node type(s) like {TARGET} are still called by "
                                  "decisions (above). Move them with --from-id.")
    # the command it names moves them, and the run after that ends with 0
    code, text = migrate("--from-id", HAND, "--apply")
    assert code == 0, text
    assert "check that it is a copy" not in text and sid.uses("d2") == [[NODE + TARGET]]
    assert migrate("--apply")[0] == 0


def test_a_copy_is_still_found_after_the_node_of_the_id_got_the_code_of_the_release(sid):
    """The first run names a copy and ends with 1; then builders-import (or the Update program) gives the node type of
    the id the code of the release. The copy still has the code of the old node type the run renamed: a re-run and a
    dry run still name it, and the re-run still ends with 1."""
    sid.node(OLD, "ZZ Call", code="the 2.2 call code")
    sid.node(HAND, "Our LLM node", code="the 2.2 call code")
    sid.decision("d1", "Uses old", [OLD])
    sid.decision("d2", "Uses the copy", [HAND], [HAND])
    code, text = migrate("--apply")
    assert code == 1 and "Not everything is done: 1 other node type(s)" in text, text
    sid.contents[TARGET]["staticContent"] = "the 2.3 call code"
    for args, exit_code in ((("--apply",), 1), ((), 0)):
        code, text = migrate(*args)
        assert code == exit_code, text
        assert (f"The node type 'Our LLM node' ({HAND}) - it has the code of the old node type - is called by 1 "
                "decision(s), which this run does not move:") in text
    assert sid.uses("d2") == [[NODE + HAND], [NODE + HAND]]
    code, text = migrate("--from-id", HAND, "--apply")
    assert code == 0, text
    assert sid.uses("d2") == [[NODE + TARGET], [NODE + TARGET]]
    assert migrate("--apply")[0] == 0


def test_a_from_id_run_that_leaves_decisions_on_another_copy_ends_with_1(sid):
    sid.node(TARGET, "ZZ Call", code="the code")
    sid.node(OLD, "ZZ Call old", code="the code")
    sid.node(HAND, "Our LLM node", code="the code")
    sid.decision("d1", "Uses old", [OLD])
    sid.decision("d2", "Uses the copy", [HAND])
    code, text = migrate("--from-id", OLD, "--apply")
    assert code == 1, text
    assert sid.uses("d1") == [[NODE + TARGET]] and sid.uses("d2") == [[NODE + HAND]]
    assert "Done. Every decision that called the old node type calls 'ZZ Call'" in text
    assert text.rstrip().endswith(f"Not everything is done: 1 other node type(s) like {TARGET} are still called by "
                                  "decisions (above). Move them with --from-id.")


def test_the_old_node_type_an_earlier_run_renamed_is_a_copy_whatever_its_code(sid):
    """The node type of the id got the code of a newer release after the move: the renamed old one keeps its old code,
    and is still the copy this command made."""
    sid.node(TARGET, "ZZ Call", code="the code of the release")
    sid.node(OLD, "ZZ Call (before 1b1b1b1b)", code="the code before")
    sid.node(HAND, "ZZ Call old", code="the code before")
    nodes = list(sid.nodes.values())
    found = decision_nodes.similar_node_types(sid, nodes, "ZZ Call", TARGET, sid.contents[TARGET])
    assert [(node["id"], copy) for node, _why, copy in found] == [(OLD, True), (HAND, False)]
    # without code of the target a name alone is no copy; where the code of a node type cannot be read, neither
    found = decision_nodes.similar_node_types(sid, nodes, "ZZ Call", TARGET, None)
    assert [(node["id"], why) for node, why, copy in found if not copy] == [
        (HAND, f"its name starts with 'ZZ Call', but {TARGET} has no code to compare")]
    del sid.contents[HAND]
    found = decision_nodes.similar_node_types(sid, nodes, "ZZ Call", TARGET, {"staticContent": "the code before"})
    assert [(node["id"], why, copy) for node, why, copy in found] == [
        (OLD, f"its name starts with 'ZZ Call' and it has the code of {TARGET}", True),
        (HAND, "its name starts with 'ZZ Call', but its code cannot be read", False)]


@pytest.mark.parametrize("args, expected, exit_code", [
    (["--from-id", "F9EFCBB7"], "is no node type id", 2),
    (["--from-id", TARGET], "--from-id names the node type of --target-id", 2),
])
def test_a_from_id_that_cannot_be_meant_is_refused_before_signing_in(monkeypatch, args, expected, exit_code):
    monkeypatch.setattr(cli, "_viya_session", lambda: pytest.fail("signed in"))
    code, text = migrate(*args)
    assert code == exit_code and expected in text


def test_from_id_needs_the_target_and_the_node_type_it_names(sid):
    sid.node(OLD, "ZZ Call old")
    code, text = migrate("--from-id", OLD, "--apply")
    assert code == 1 and "there is none of that id on this site" in text and sid.writes == []
    sid.node(TARGET, "ZZ Call")
    code, text = migrate("--from-id", HAND, "--apply")
    assert code == 1 and f"The node type {HAND} (--from-id) is not on this site." in text and sid.writes == []


def test_a_name_that_starts_like_the_node_is_named_where_neither_the_node_nor_the_id_is_there(sid):
    sid.node(OLD, "ZZ Call old")
    code, text = migrate()
    assert code == 0
    assert f"Node type(s) whose name starts with 'ZZ Call': 'ZZ Call old' ({OLD})." in text


# ---- versions whose node type was deleted earlier ----
def test_versions_whose_node_lost_its_address_are_a_warning_of_the_dry_run_and_of_the_run(sid):
    sid.node(OLD, "ZZ Call")
    sid.decision("d1", "Moves", [OLD])
    sid.decision("d2", "Broken earlier", [None], [OLD, None])
    code, text = migrate()
    assert code == 0, text
    warning = "Warning: 2 decision version(s) call a node whose node type is gone"
    assert warning in text
    assert "  'Broken earlier' (d2) version 1.0 (locked): step(s) 'node'" in text
    assert "  'Broken earlier' (d2) version 2.0 (current): step(s) 'node'" in text
    code, text = migrate("--apply")
    assert code == 0, text
    assert warning in text
    assert "Warning: the 2 decision version(s) listed above whose node type is gone still cannot run." in text
    assert sid.uses("d2") == [[None], [NODE + TARGET, None]]


def test_a_code_file_step_without_an_address_is_no_lost_node(sid, monkeypatch):
    """A step that runs a code file has a customObject too (type decisionPythonFile), found on viya4-s2 without an
    address where its code file was deleted: that is no node, and not a version this command speaks of."""
    sid.node(OLD, "ZZ Call")
    sid.decision("d1", "Moves", [OLD])
    sid.decision("d2", "Code file gone", [OLD])
    read = sid.get

    def with_a_code_file_step(path, **kw):
        answer = read(path, **kw)
        if path == "/decisions/flows/d2/revisions/d2-v1":
            answer._body["flow"]["steps"].append(
                {"customObject": {"isRestDNT": False, "name": "a python file", "type": "decisionPythonFile"}})
        return answer
    monkeypatch.setattr(sid, "get", with_a_code_file_step)
    code, text = migrate()
    assert code == 0, text
    assert "Warning" not in text and "a python file" not in text


def test_a_site_without_broken_versions_gets_no_warning(sid):
    sid.node(OLD, "ZZ Call")
    sid.decision("d1", "Moves", [OLD])
    code, text = migrate()
    assert code == 0 and "Warning" not in text


# ---------------------------------------------------------------------------
# the manifest
# ---------------------------------------------------------------------------
MANIFEST = """version: 1
contentRoot: /SAS Agentic AI Accelerator
package: SAS-Viya-Integrations/SAS-Agentic-AI-Accelerator.json
entries:
  - {path: release.json, kind: file, generated: release-info}
  - path: Decisions/Call LLM Node
    kind: decision
    nodeTypes:
      - {name: Call LLM, id: f9efcbb7-f57b-4a62-b56d-1a89595e2039}
"""


@pytest.fixture
def manifest(tmp_path):
    return load_manifest(make_repo(tmp_path, MANIFEST))


def test_a_decision_entry_names_its_node_types_by_name_and_id(manifest):
    entry = manifest.find("Decisions/Call LLM Node", "decision")
    assert entry.node_types == ({"name": "Call LLM", "id": CALL_LLM_NODE_ID},)
    assert content.entry_hash(manifest, entry) is None
    info = content.release_info(manifest)
    assert {"path": "Decisions/Call LLM Node", "kind": "decision", "hash": None} in info["entries"]


@pytest.mark.parametrize("node_types, problem", [
    (None, "a decision lists the custom node types it uses under nodeTypes"),
    ([{"name": "Call LLM", "id": "F9EFCBB7"}], "id is a UUID in lower case"),
    ([{"name": "Call LLM", "id": CALL_LLM_NODE_ID, "code": "x"}], "has unknown field(s) code"),
    ([{"name": "Call LLM", "id": CALL_LLM_NODE_ID}] * 2, "is listed twice"),
])
def test_a_decision_entry_without_usable_node_types_is_refused(tmp_path, node_types, problem):
    entry = {"path": "Decisions/D", "kind": "decision"}
    if node_types is not None:
        entry["nodeTypes"] = node_types
    document = {"version": 1, "contentRoot": "/R", "package": "p.json", "entries": [entry]}
    with pytest.raises(ManifestError) as caught:
        parse_manifest(document, tmp_path)
    assert any(problem in line for line in caught.value.problems), caught.value.problems


# ---------------------------------------------------------------------------
# package-export and package-check
# ---------------------------------------------------------------------------
def _export(manifest, node_id=CALL_LLM_NODE_ID):
    """What an export of the content folder holds: the decision, its node type
    as a dependency with the rule on it, and a decision somebody parked there
    with a node type of its own."""
    built = package_of(manifest, change={"Decisions/Call LLM Node": [(node_id, "Call LLM")]})
    built.grant("rule", "rule on the node", NODE + node_id)
    built.node_type("3d3d3d3d-0000-4000-8000-000000000004", "Their node")
    built.decision("Decisions/Parked", [("3d3d3d3d-0000-4000-8000-000000000004", "Their node")])
    return built.build()


def test_the_export_keeps_the_decision_and_its_node_type_and_drops_the_rest(manifest):
    package = _export(manifest)
    result = filter_package(package, manifest)
    assert "decision 'Decisions/Call LLM Node'" in result.kept
    assert f"node type 'Call LLM' ({CALL_LLM_NODE_ID}, used by decision 'Decisions/Call LLM Node')" in result.kept
    assert "decision 'Decisions/Parked'" in result.dropped
    assert "node type 'Their node' (3d3d3d3d-0000-4000-8000-000000000004) - no decision the manifest lists uses " \
           "it under this id" in result.dropped
    assert "rule 'rule on the node'" in result.dropped
    kinds = sorted(d["transferObject"]["summary"]["type"] for d in package["transferDetails"])
    assert kinds == ["application/vnd.sas.file+json", "decision", "decisionNodeType", "folder", "folder"]
    neutralise_users(package)                                   # as the export does next
    assert [text for text in (f.text for f in package_findings(package, manifest)) if "release.json" not in text] == []


def test_the_export_names_no_user_in_the_node_type(manifest):
    built = package_of(manifest, change={"Decisions/Call LLM Node": None})
    built.node_type(CALL_LLM_NODE_ID, "Call LLM", user="the exporter")
    built.decision("Decisions/Call LLM Node", [(CALL_LLM_NODE_ID, "Call LLM")], user="the exporter")
    package = built.build()
    filter_package(package, manifest)
    assert named_users(package).get("the exporter")
    neutralise_users(package)
    assert set(named_users(package)) == {"sas-agentic-ai-accelerator"}


def test_a_node_type_under_another_id_does_not_ship(manifest):
    package = _export(manifest, node_id=OLD)
    filter_package(package, manifest)
    texts = [f.text for f in package_findings(package, manifest)]
    assert f"decision 'Decisions/Call LLM Node' uses the node type {NODE}{OLD}, which its manifest entry does not " \
           "list" in texts
    assert f"decision 'Decisions/Call LLM Node': the node type 'Call LLM' ({CALL_LLM_NODE_ID}) is not in the " \
           "package - an import would leave the decision without it" in texts


def test_the_gate_holds_the_node_type_to_its_id_name_and_code(manifest):
    built = package_of(manifest, change={"Decisions/Call LLM Node": None})
    built.node_type(CALL_LLM_NODE_ID, "Call LLM", code="", content_id="c1")
    built.decision("Decisions/Call LLM Node", [(CALL_LLM_NODE_ID, "Call LLM")], unnamed=True)
    package = built.build()
    for detail in package["transferDetails"]:
        obj = detail["transferObject"]
        if obj["summary"]["type"] == "decisionNodeType":
            body = json.loads(base64.b64decode(obj["content"]))
            body["decisionNodeTypeRep"]["name"] = "Call LLM (copy)"
            obj["content"] = base64.b64encode(json.dumps(body).encode()).decode()
    texts = [f.text for f in package_findings(package, manifest)]
    shown = f"node type 'Call LLM' ({CALL_LLM_NODE_ID}) of decision 'Decisions/Call LLM Node'"
    assert f"{shown} is named 'Call LLM (copy)', its manifest entry lists 'Call LLM'" in texts
    assert f"{shown} carries no code (decisionNodeTypeContentRep with staticContent)" in texts
    assert ("decision 'Decisions/Call LLM Node': a step of version 1.0 calls the node 'Call LLM' and names no node "
            "type - its node type was deleted where the decision was exported, and the version cannot run") in texts


# ---- the code and the terms of the release: the program the entry names under `content` ----
def test_the_programs_of_the_release_write_one_content_and_the_gate_reads_it():
    from mdb.core.packages import program_node_content
    folder = REPO_ROOT / "SAS-Viya-Integrations" / "SAS-Intelligent-Decisioning-Integration"
    create, update = (program_node_content((folder / f"{kind}-Custom-SAS-Intelligent-Decisioning-Node.sas")
                                           .read_text(encoding="utf-8")) for kind in ("Create", "Update"))
    assert create == update
    code = update["staticContent"]
    assert code.startswith('package "${PACKAGE_NAME}" /inline;\n') and code.endswith("endpackage;")
    assert "webreq.setRequestContentType('application/json');" in code          # the doubled quote is one
    assert [term["name"] for term in update["nodeTypeSignatureTerms"]] == [
        "llmBody", "llmURL", "llmGenerated", "output_length", "prompt_length"]


PROGRAM = """%macro node;
    * Create the content for the node type;
    data _null_;
        file _dntIn;
        put '{';
        put '"contentType": "DS2",';
        put '"staticContent": "package \\"${PACKAGE_NAME}\\" /inline;\\n  x = ''y'';\\nendpackage;",';
        put '"nodeTypeSignatureTerms": [';
        put '{"name": "llmBody", "dataType": "string", "length": 10485760, "defaultValue": "", "direction": "input", "generateDataGridColumns": false}';
        put ']';
        put '}';
    run;
%mend;
"""


@pytest.fixture
def program_manifest(tmp_path):
    with_content = MANIFEST.replace("      - {name: Call LLM, id: f9efcbb7-f57b-4a62-b56d-1a89595e2039}\n",
                                    "      - name: Call LLM\n        id: f9efcbb7-f57b-4a62-b56d-1a89595e2039\n"
                                    "        content: Node/Update.sas\n")
    assert with_content != MANIFEST
    return load_manifest(make_repo(tmp_path, with_content, sources={"Node/Update.sas": PROGRAM}))


def _with_node_code(manifest, code):
    built = package_of(manifest, change={"Decisions/Call LLM Node": None})
    built.node_type(CALL_LLM_NODE_ID, "Call LLM", code=code)
    built.decision("Decisions/Call LLM Node", [(CALL_LLM_NODE_ID, "Call LLM")])
    return [f.text for f in package_findings(built.build(), manifest) if "release.json" not in f.text]


def test_the_gate_holds_the_node_type_to_the_code_and_the_terms_of_its_program(program_manifest):
    shown = f"node type 'Call LLM' ({CALL_LLM_NODE_ID}) of decision 'Decisions/Call LLM Node'"
    entry = program_manifest.find("Decisions/Call LLM Node", "decision")
    assert entry.node_types[0]["content"] == "Node/Update.sas"
    # the code of the program, line ends and blanks at the end of a line aside: no finding
    assert _with_node_code(program_manifest, 'package "${PACKAGE_NAME}" /inline;\r\n  x = \'y\';  \r\nendpackage;\r\n') == []
    # the stub an export of an older node would carry: a finding, before the package can ship
    assert _with_node_code(program_manifest, 'package "${PACKAGE_NAME}" /inline; endpackage;') == [
        f"{shown}: its code is not the code Node/Update.sas writes - the import would give every site the code of "
        "the package"]
    # the terms: another length is a finding
    (path,) = [p for p in program_manifest.repo_root.rglob("Update.sas")]
    path.write_text(PROGRAM.replace('"length": 10485760', '"length": 32767'), encoding="utf-8")
    assert _with_node_code(program_manifest, 'package "${PACKAGE_NAME}" /inline;\n  x = \'y\';\nendpackage;') == [
        f"{shown}: its terms (llmBody) are not the ones Node/Update.sas writes - name, type, length and direction"]
    # a program the gate cannot read is a finding of its own
    path.write_text("%macro node; %mend;", encoding="utf-8")
    texts = _with_node_code(program_manifest, "x")
    assert texts == [f"{shown}: Node/Update.sas cannot be read for the code of the release - it has no data step "
                     "after '* Create the content for the node type;'"]


def test_a_node_type_program_that_is_not_in_the_repository_is_refused(tmp_path):
    entry = {"path": "Decisions/D", "kind": "decision",
             "nodeTypes": [{"name": "Call LLM", "id": CALL_LLM_NODE_ID, "content": "Node/missing.sas"}]}
    document = {"version": 1, "contentRoot": "/R", "package": "p.json", "entries": [entry]}
    with pytest.raises(ManifestError) as caught:
        parse_manifest(document, tmp_path)
    assert "'Decisions/D': node type 'Call LLM': content 'Node/missing.sas' does not exist in the repository" in \
        " ".join(caught.value.problems)


def test_a_node_type_of_a_decision_that_is_not_listed_fails_the_gate(manifest):
    built = package_of(manifest)
    built.node_type("3d3d3d3d-0000-4000-8000-000000000004", "Their node")
    built.decision("Decisions/Parked", [("3d3d3d3d-0000-4000-8000-000000000004", "Their node")])
    texts = [f.text for f in package_findings(built.build(), manifest)]
    assert "decision 'Decisions/Parked' is in the package but not in the manifest" in texts
    assert ("node type 'Their node' (3d3d3d3d-0000-4000-8000-000000000004) is used by decision 'Decisions/Parked', "
            "which is not in the manifest") in texts


def test_a_package_without_the_decision_misses_it(manifest):
    package = package_of(manifest, change={"Decisions/Call LLM Node": None}).build()
    texts = [f.text for f in package_findings(package, manifest)]
    assert "decision 'Decisions/Call LLM Node' is in the manifest but not in the package" in texts


def test_the_decisions_part_of_a_package_holds_the_decisions_their_node_types_and_folders(manifest):
    package = _export(manifest)
    reduced, held, carried = decisions_package(package)
    assert carried == [("Call LLM", CALL_LLM_NODE_ID), ("Their node", "3d3d3d3d-0000-4000-8000-000000000004")]
    assert sorted(held) == ["decision Decisions/Call LLM Node", "decision Decisions/Parked",
                            f"decisionNodeType Call LLM ({CALL_LLM_NODE_ID})",
                            "decisionNodeType Their node (3d3d3d3d-0000-4000-8000-000000000004)",
                            "folder Decisions", "folder SAS Agentic AI Accelerator"]
    assert len(reduced["transferDetails"]) == 6 and len(package["transferDetails"]) > 6


def test_the_import_source_takes_the_decisions_apart_from_what_is_brought_in_place(manifest):
    package = _export(manifest)
    source = PackageSource(package, manifest)
    assert sorted(item.path for item in source.decisions) == ["Decisions/Call LLM Node", "Decisions/Parked"]
    assert sorted(item.name for item in source.node_types) == ["Call LLM", "Their node"]
    assert ("decision", "Decisions/Call LLM Node") not in source._at
    assert {entry.kind for entry in source.manifest("/R").entries} >= {"decision"}


# ---------------------------------------------------------------------------
# content-deploy
# ---------------------------------------------------------------------------
def test_content_deploy_says_whether_the_decision_is_on_the_site_and_writes_none(manifest):
    viya = Viya()
    result = viya_content.deploy(viya, manifest, dry_run=False)
    decision = next(o for o in result.outcomes if o.entry.kind == "decision")
    assert (decision.status, decision.note) == ("not deployed", "not on the site - it comes with the package")
    viya.put_member("/SAS Agentic AI Accelerator/Decisions/Call LLM Node", "/decisions/flows/", None)
    result = viya_content.deploy(viya, manifest, dry_run=True)
    decision = next(o for o in result.outcomes if o.entry.kind == "decision")
    assert (decision.status, decision.note) == ("not deployed", "on the site")
    assert not [call for call in viya.wrote() if "/decisions/" in call[1]]
    assert result.unlisted == []


def test_content_deploy_does_not_take_a_decision_to_another_place(tmp_path, monkeypatch):
    make_repo(tmp_path, MANIFEST)
    monkeypatch.setattr(cli, "Context", lambda: SimpleNamespace(repo=tmp_path, core=None))
    monkeypatch.setattr(cli, "_viya_session", lambda: pytest.fail("signed in"))
    result = CliRunner().invoke(cli.app, ["content-deploy", "--only", "Decisions/Call LLM Node", "--to", "/X/Y"])
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# builders-import
# ---------------------------------------------------------------------------
def _with_decision(node_id=CALL_LLM_NODE_ID) -> dict:
    built = _built()
    built.node_type(node_id, "Call LLM")
    built.decision("Decisions/Call LLM Node", [(node_id, "Call LLM")])
    built.grant("rule", "rule on the node", NODE + node_id)
    return built.build()


DONE_TASKS = [{"resourceType": "decision", "name": "Call LLM Node", "state": "completed"},
              {"resourceType": "decisionNodeType", "name": "Call LLM", "state": "completed"}]


def test_the_import_brings_the_decision_and_its_node_type_in_a_job_of_their_own(importing, no_discovery):
    no_discovery()
    importing.viya.node_types = {}
    importing.tasks = DONE_TASKS
    result, _fake, text = importing(Site(**{"Prompt Builder": NO_CONTEXT}), package=_with_decision())
    assert result.exit_code == 0, text
    assert f"    node type 'Call LLM' ({CALL_LLM_NODE_ID}): not on this site - the import creates it under this id." \
        in text
    assert "    imported 4 object(s) through the transfer service." in text
    handed = importing.uploaded["SAS Agentic AI Accelerator - decisions"]
    kinds = sorted(d["transferObject"]["summary"]["type"] for d in handed["transferDetails"])
    assert kinds == ["decision", "decisionNodeType", "folder", "folder"]
    reports = importing.uploaded["SAS Agentic AI Accelerator"]
    assert "decision" not in [d["transferObject"]["summary"]["type"] for d in reports["transferDetails"]]
    assert "not imported: rule 'rule on the node' (mdb imports no authorization rule and no share)" in text


def test_a_node_type_of_the_id_is_overwritten_and_the_line_says_so(importing, no_discovery):
    no_discovery()
    importing.viya.node_types = {CALL_LLM_NODE_ID: {"id": CALL_LLM_NODE_ID, "name": "Call LLM"}}
    importing.tasks = DONE_TASKS
    result, _fake, text = importing(Site(**{"Prompt Builder": NO_CONTEXT}), package=_with_decision())
    assert result.exit_code == 0, text
    assert (f"    node type 'Call LLM' ({CALL_LLM_NODE_ID}): on this site - the import overwrites it in place with the "
            "package's: local edits of its code and its terms are replaced.") in text


def test_a_call_llm_node_under_another_id_stops_the_decisions_and_names_the_migration(importing, no_discovery):
    no_discovery()
    importing.viya.node_types = {OLD: {"id": OLD, "name": "Call LLM"}}
    importing.tasks = DONE_TASKS
    result, _fake, text = importing(Site(**{"Prompt Builder": NO_CONTEXT}), package=_with_decision())
    assert result.exit_code == 1
    assert f"    node type 'Call LLM' ({CALL_LLM_NODE_ID}): this site has a node type 'Call LLM' under the id {OLD}." in text
    assert "`mdb decision-node-migrate` says what it would do, `mdb decision-node-migrate --apply` does it" in text
    assert "SAS Agentic AI Accelerator - decisions" not in importing.uploaded
    # the rest of the package arrived
    assert "SAS Agentic AI Accelerator" in importing.uploaded


def test_no_decisions_leaves_them_out_and_the_run_passes(importing, no_discovery):
    no_discovery()
    importing.viya.node_types = {OLD: {"id": OLD, "name": "Call LLM"}}
    result, _fake, text = importing(Site(**{"Prompt Builder": NO_CONTEXT}), "--no-decisions", package=_with_decision())
    assert result.exit_code == 0, text
    assert ("  Decisions (Decisions/Call LLM Node): left out (--no-decisions), and the node types they carry with "
            "them.") in text
    assert "SAS Agentic AI Accelerator - decisions" not in importing.uploaded


def test_a_decision_the_job_skipped_without_a_task_fails_the_run(importing, no_discovery):
    no_discovery()
    importing.viya.node_types = {}
    importing.tasks = [{"resourceType": "decisionNodeType", "name": "Call LLM", "state": "completed"}]
    result, _fake, text = importing(Site(**{"Prompt Builder": NO_CONTEXT}), package=_with_decision())
    assert result.exit_code == 1
    assert "      decision 'Call LLM Node': no completed task - the job did not import it" in text


def test_a_failed_node_type_task_fails_the_run(importing, no_discovery):
    no_discovery()
    importing.viya.node_types = {}
    importing.tasks = [{"resourceType": "decisionNodeType", "name": "Call LLM", "state": "failed",
                        "message": "names must be unique"}]
    result, _fake, text = importing(Site(**{"Prompt Builder": NO_CONTEXT}), package=_with_decision())
    assert result.exit_code == 1
    assert "      decisionNodeType 'Call LLM': failed - names must be unique" in text


@pytest.mark.parametrize("limit, warned", [(1, True), (2, False)])
def test_a_full_page_of_node_types_is_a_warning_of_the_import(importing, no_discovery, monkeypatch, limit, warned):
    no_discovery()
    monkeypatch.setattr(decision_nodes, "NODE_TYPE_LIMIT", limit)
    importing.viya.node_types = {CALL_LLM_NODE_ID: {"id": CALL_LLM_NODE_ID, "name": "Call LLM"}}
    importing.tasks = DONE_TASKS
    result, _fake, text = importing(Site(**{"Prompt Builder": NO_CONTEXT}), package=_with_decision())
    assert result.exit_code == 0, text
    assert ("    Warning: SAS Intelligent Decisioning listed 1 custom node types, a full page, and does not say how many "
            "there are" in text) is warned, text


def test_a_site_without_decisioning_gets_a_line_and_no_failure(importing, no_discovery):
    no_discovery()
    result, _fake, text = importing(Site(**{"Prompt Builder": NO_CONTEXT}), package=_with_decision())
    assert result.exit_code == 0, text
    assert "SAS Intelligent Decisioning does not answer on this site" in text
    assert "SAS Agentic AI Accelerator - decisions" not in importing.uploaded


def test_a_dry_run_asks_the_site_and_uploads_nothing(importing, no_discovery):
    no_discovery()
    importing.viya.node_types = {}
    result, _fake, text = importing(Site(**{"Prompt Builder": NO_CONTEXT}), "--dry-run", package=_with_decision())
    assert result.exit_code == 0, text
    assert "not on this site - the import creates it under this id." in text
    assert importing.uploaded == {}
    assert [method for method, _path in importing.viya.wrote()] == []


def test_a_node_type_the_job_left_out_without_a_task_fails_the_run(importing, no_discovery):
    no_discovery()
    importing.viya.node_types = {}
    importing.tasks = [{"resourceType": "decision", "name": "Call LLM Node", "state": "completed"}]
    result, _fake, text = importing(Site(**{"Prompt Builder": NO_CONTEXT}), package=_with_decision())
    assert result.exit_code == 1
    assert "      decisionNodeType 'Call LLM': no completed task - the job did not import it" in text


def test_a_decisions_job_that_did_not_end_completed_fails_the_run(importing, no_discovery):
    no_discovery()
    importing.viya.node_types = {}
    importing.tasks = DONE_TASKS
    result, _fake, text = importing(Site(**{"Prompt Builder": NO_CONTEXT}), package=_with_decision(),
                                    fails="SAS Agentic AI Accelerator - decisions")
    assert result.exit_code == 1
    assert "    import ended failed with 0 error(s):" in text
    assert "imported 4 object(s)" not in text


def test_a_decisions_job_that_counts_an_error_fails_the_run(importing, no_discovery, monkeypatch):
    """completed, with errorCount 1: the reports' job is waited for first, the decisions' job second."""
    no_discovery()
    importing.viya.node_types = {}
    importing.tasks = DONE_TASKS
    waited = []

    def wait_for_job(session, uri, timeout=600):
        waited.append(uri)
        return {"state": "completed", "errorCount": len(waited) - 1, "links": [{"rel": "self", "href": uri}]}
    monkeypatch.setattr(transfer, "wait_for_job", wait_for_job)
    result, _fake, text = importing(Site(**{"Prompt Builder": NO_CONTEXT}), package=_with_decision())
    assert len(waited) == 2
    assert result.exit_code == 1
    assert "    import ended completed with 1 error(s):" in text


def test_another_content_root_refuses_the_decisions_and_the_repeat_leaves_them_out(importing, no_discovery):
    """The decisions are refused for another content root like the reports; the command to repeat names both
    options, and runs to the end."""
    no_discovery()
    importing.viya.node_types = {}
    importing.tasks = DONE_TASKS
    result, _fake, text = importing(Site(**{"Prompt Builder": NO_CONTEXT}), "--content-root", "/Team/Accelerator",
                                    "--create", package=_with_decision())
    assert result.exit_code == 1
    assert ("  Decisions (Decisions/Call LLM Node): NOT imported - the package was exported from /SAS Agentic AI "
            "Accelerator and its content goes to /Team/Accelerator: the transfer service imports a decision into the "
            "folder it was exported from") in text
    assert "imports a report into" not in text.split("  Decisions (")[1]
    repeat = text.split("then run:\n", 1)[1].splitlines()[0]
    assert repeat == ('mdb builders-import --content-root /Team/Accelerator --no-releases --no-reports --no-decisions '
                      '--create')
    assert "; --no-reports and --no-decisions are added here, because the reports and the decisions are not imported " \
           "into /Team/Accelerator" in text
    result, _fake, text = importing(Site(**{"Prompt Builder": NO_CONTEXT}), *repeat.split()[2:],
                                    package=_with_decision())
    assert result.exit_code == 0, text
    assert "Decisions (Decisions/Call LLM Node): left out (--no-decisions)" in text


def test_no_reports_alone_with_another_content_root_still_fails_and_the_help_says_so(importing, no_discovery):
    no_discovery()
    importing.viya.node_types = {}
    importing.tasks = DONE_TASKS
    result, _fake, text = importing(Site(**{"Prompt Builder": NO_CONTEXT}), "--content-root", "/Team/Accelerator",
                                    "--create", "--no-reports", package=_with_decision())
    assert result.exit_code == 1
    assert "  Decisions (Decisions/Call LLM Node): NOT imported - " in text
    # the help and the README name both options, and no longer say that --no-reports is enough
    help_text = " ".join(CliRunner().invoke(cli.app, ["builders-import", "--help"],
                                            env={"COLUMNS": "200"}).output.split())
    readme = " ".join((REPO_ROOT / "SAS-Viya-Integrations" / "README.md").read_text(encoding="utf-8").split())
    for text in (" ".join(cli.builders_import.__doc__.split()), readme):
        assert "unless --no-reports says that is meant" not in text
        assert "for everything but the reports" not in text
    assert "unless --no-reports and --no-decisions say that is meant - --no-reports alone does not" in help_text
    import typer.main
    command = typer.main.get_command(cli.app).commands["builders-import"]
    (root_help,) = [param.help for param in command.params if "--content-root" in param.opts]
    assert "The reports and the decisions are imported only into the folder the package was exported from" in root_help
    assert "ends with exit code 1 unless `--no-reports --no-decisions` leaves both out" in readme


@pytest.mark.parametrize("case", ["--no-decisions", "a clash", "another content root"])
def test_decisions_that_are_not_imported_get_no_folder_in_place(importing, no_discovery, case):
    no_discovery()
    importing.viya.node_types = {OLD: {"id": OLD, "name": "Call LLM"}} if case == "a clash" else {}
    importing.tasks = DONE_TASKS
    args = {"--no-decisions": ["--no-decisions"], "a clash": [],
            "another content root": ["--content-root", "/Team/Accelerator", "--create", "--no-reports"]}[case]
    root = "/Team/Accelerator" if case == "another content root" else CONTENT_ROOT
    result, _fake, text = importing(Site(**{"Prompt Builder": NO_CONTEXT}), *args, package=_with_decision())
    assert "SAS Agentic AI Accelerator - decisions" not in importing.uploaded
    assert importing.viya.folder_id(f"{root}/Decisions") is None
    assert "Decisions  " not in text and "folder        Decisions" not in text
    # the rest of the content arrived in place
    assert importing.viya.folder_id(root) is not None


def test_decisions_that_are_imported_keep_their_folder_in_the_in_place_part(importing, no_discovery):
    no_discovery()
    importing.viya.node_types = {}
    importing.tasks = DONE_TASKS
    result, _fake, text = importing(Site(**{"Prompt Builder": NO_CONTEXT}), package=_with_decision())
    assert result.exit_code == 0, text
    assert importing.viya.folder_id(f"{CONTENT_ROOT}/Decisions") is not None


def test_the_help_of_the_migration_names_from_id_and_the_one_section_of_the_guide():
    result = CliRunner().invoke(cli.app, ["decision-node-migrate", "--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    flat = " ".join(result.output.split())
    assert "--from-id" in flat and "Deployment-of-Decisions#call-llm-node-uuid" in flat
    assert "the-call-llm-node" not in flat


def test_the_decision_nodes_module_holds_no_host():
    text = Path(decision_nodes.__file__).read_text(encoding="utf-8")
    assert "sas.com" not in text and "openstack" not in text
