# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Transfer service: export a folder, import a package with a rewritten mapping.

No network. A fake session records the calls so the request shapes the service
recorded on its own jobs are what we send, and the mapping rewrite - the whole
point of importing through mdb rather than the CLI - is checked as a pure
function.
"""
import json

import pytest

from mdb.core.packages import PLACEHOLDER_HOST
from mdb.viya import transfer


class FakeResponse:
    def __init__(self, status=200, body=None, text=""):
        self.status_code = status
        self.ok = status < 400
        self._body = body if body is not None else {}
        self.text = text or json.dumps(self._body)

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, job_states=("running", "completed")):
        self.calls = []
        self._states = list(job_states)

    def get(self, path, **kw):
        self.calls.append(("GET", path, kw))
        if path == "/folders/folders/@item":
            return FakeResponse(200, {"id": "f-1", "name": "Prompt Builder"})
        if path.startswith("/transfer/exportJobs/") or path.startswith("/transfer/importJobs/"):
            if path.endswith("/tasks"):
                return FakeResponse(200, {"items": [{"name": "Prompt Builder", "state": "failed",
                                                     "message": "no such caslib"}]})
            state = self._states.pop(0) if len(self._states) > 1 else self._states[0]
            return FakeResponse(200, {"id": "j-1", "state": state, "packageUri": "/transfer/packages/p-1",
                                      "errorCount": 0,
                                      "links": [{"rel": "self", "href": path}]})
        if path == "/transfer/packages/p-1":
            return FakeResponse(200, {"id": "p-1", "name": "Prompt Builder", "transferDetails": []})
        if path.endswith("/mapping"):
            return FakeResponse(200, MAPPING())
        return FakeResponse(404, {}, "not found")

    def post(self, path, **kw):
        self.calls.append(("POST", path, kw))
        if path == "/transfer/exportJobs":
            return FakeResponse(201, {"id": "j-1", "state": "running",
                                      "links": [{"rel": "self", "href": "/transfer/exportJobs/j-1"}]})
        if path == "/transfer/packages":
            return FakeResponse(201, {"id": "p-9", "name": "Prompt Builder"})
        if path == "/transfer/importJobs":
            return FakeResponse(201, {"id": "i-1", "state": "running",
                                      "links": [{"rel": "self", "href": "/transfer/importJobs/i-1"}]})
        return FakeResponse(400, {}, "bad")

    def delete(self, path, **kw):
        self.calls.append(("DELETE", path, kw))
        return FakeResponse(204)


def MAPPING():
    return {
        "version": 1,
        "connectors": {"table": [
            {"source": "server=cas-shared-default;library=Public;table=ACCELERATOR_RELEASES",
             "target": "server=cas-shared-default;library=Public;table=ACCELERATOR_RELEASES",
             "connectorId": "c-1"},
            {"source": "server=cas-shared-default;library=Public;table=HMEQ",
             "target": "server=cas-shared-default;library=Public;table=HMEQ", "connectorId": "c-2"},
        ]},
        "substitutions": [{
            "resourceId": "/reports/reports/r-1", "resourceName": "Prompt Builder",
            "properties": [
                {"name": "Server-1", "value": "https://sassoftware.github.io"},
                {"name": "VisualElement:ve9",
                 "value": f"https://{PLACEHOLDER_HOST}/SASJobExecution/?_program=%2FX&_action=form"},
            ]}],
    }


def test_the_mapping_points_home_and_binds_the_release_table():
    mapping, changes = transfer.rehost_mapping(
        MAPPING(), "viya.example.com", "ACCELERATOR_RELEASES",
        "server=cas-shared-default;library=MyLib;table=RELEASES")
    props = {p["name"]: p["value"] for p in mapping["substitutions"][0]["properties"]}
    assert props["VisualElement:ve9"].startswith("https://viya.example.com/SASJobExecution/")
    assert props["Server-1"] == "https://sassoftware.github.io"          # public link untouched
    targets = {c["source"].split("table=")[1]: c["target"] for c in mapping["connectors"]["table"]}
    assert targets["ACCELERATOR_RELEASES"] == "server=cas-shared-default;library=MyLib;table=RELEASES"
    assert targets["HMEQ"].endswith("table=HMEQ")                          # other tables left alone
    assert any("host viya.example.com" in c for c in changes)
    assert any("HMEQ left as exported" in c for c in changes)


def test_a_mapping_already_matching_reports_no_table_change():
    mapping, changes = transfer.rehost_mapping(
        MAPPING(), "viya.example.com", "ACCELERATOR_RELEASES",
        "server=cas-shared-default;library=Public;table=ACCELERATOR_RELEASES")
    assert not [c for c in changes if c.startswith("data source server=cas-shared-default;library=Public;table=ACCELERATOR_RELEASES ->")]


def test_table_specs_round_trip_and_hosts_are_bare():
    spec = transfer.table_spec("cas-shared-default", "Public", "ACCELERATOR_RELEASES")
    assert transfer.parse_table_spec(spec) == {"server": "cas-shared-default", "library": "Public",
                                               "table": "ACCELERATOR_RELEASES"}
    assert transfer.host_of("https://viya.example.com/") == "viya.example.com"
    assert transfer.host_of("viya.example.com") == "viya.example.com"


def test_export_sends_the_recorded_request_shape_and_waits():
    session = FakeSession(job_states=("running", "running", "completed"))
    folder = transfer.folder_by_path(session, "/SAS Agentic AI Accelerator/Prompt Builder")
    job = transfer.start_export(session, "Prompt Builder", [f"/folders/folders/{folder['id']}"])
    method, path, kw = session.calls[-1]
    body = json.loads(kw["data"])
    assert (method, path) == ("POST", "/transfer/exportJobs")
    assert kw["headers"]["Content-Type"] == transfer.EXPORT_REQUEST
    assert body == {"version": 1, "name": "Prompt Builder", "description": "",
                    "items": ["/folders/folders/f-1"],
                    "options": {"includeDependencies": "true", "includeRules": "true"}}
    slept = []
    done = transfer.wait_for_job(session, "/transfer/exportJobs/j-1", timeout=30, poll=1, sleep=slept.append)
    assert done["state"] == "completed" and done["packageUri"] == "/transfer/packages/p-1"
    assert slept == [1, 1]  # two running polls, then completed
    text = transfer.download_package(session, "/transfer/packages/p-1")
    assert json.loads(text)["name"] == "Prompt Builder" and text.startswith("{\n  ")
    get = [c for c in session.calls if c[1] == "/transfer/packages/p-1"][-1]
    assert get[2]["headers"]["Accept"] == transfer.PACKAGE_MEDIA


def test_a_job_that_never_finishes_times_out():
    session = FakeSession(job_states=("running",))
    with pytest.raises(RuntimeError, match="still running"):
        transfer.wait_for_job(session, "/transfer/exportJobs/j-1", timeout=0, poll=1, sleep=lambda s: None)


def test_import_uploads_then_starts_the_job_with_the_mapping_inline(tmp_path):
    package = tmp_path / "pkg.json"
    package.write_text(json.dumps({"name": "Prompt Builder", "transferDetails": []}), encoding="utf-8")
    session = FakeSession()
    uploaded = transfer.upload_package(session, package)
    method, path, kw = session.calls[-1]
    assert (method, path) == ("POST", "/transfer/packages") and "file" in kw["files"]
    mapping = transfer.get_mapping(session, uploaded["id"])
    mapping, _ = transfer.rehost_mapping(mapping, "viya.example.com", "ACCELERATOR_RELEASES",
                                         "server=cas-shared-default;library=Public;table=ACCELERATOR_RELEASES")
    job = transfer.start_import(session, "Prompt Builder", "/transfer/packages/p-9", mapping)
    method, path, kw = session.calls[-1]
    body = json.loads(kw["data"])
    assert (method, path) == ("POST", "/transfer/importJobs")
    assert kw["headers"]["Content-Type"] == transfer.IMPORT_REQUEST
    assert body["version"] == 2 and body["packageUri"] == "/transfer/packages/p-9"
    assert body["mapping"]["substitutions"][0]["properties"][1]["value"].startswith("https://viya.example.com/")
    assert transfer.job_uri(job) == "/transfer/importJobs/i-1"


def test_job_messages_name_the_failing_task():
    session = FakeSession()
    job = {"links": [{"rel": "self", "href": "/transfer/importJobs/i-1"}]}
    assert transfer.job_messages(session, job) == ["Prompt Builder: failed no such caslib"]


def test_the_other_tables_follow_a_library_only_when_one_is_named():
    """A site that keeps LLM_LOGS and the fact sheets in a library of its own
    asks for it; the table keeps its name, because a report retargeted at a
    table with other columns loses its data items."""
    mapping, changes = transfer.rehost_mapping(
        MAPPING(), "viya.example.com", "ACCELERATOR_RELEASES",
        "server=cas-shared-default;library=MyLib;table=RELEASES", library="Monitoring", server="cas-team")
    targets = {c["source"].split("table=")[1]: c["target"] for c in mapping["connectors"]["table"]}
    assert targets == {"ACCELERATOR_RELEASES": "server=cas-shared-default;library=MyLib;table=RELEASES",
                       "HMEQ": "server=cas-team;library=Monitoring;table=HMEQ"}
    assert "data source server=cas-shared-default;library=Public;table=HMEQ -> " \
           "server=cas-team;library=Monitoring;table=HMEQ" in changes
    assert not any("left as exported" in c for c in changes)
    # without a server the table stays on the server the package names
    mapping, _ = transfer.rehost_mapping(MAPPING(), "h", "ACCELERATOR_RELEASES", "x", library="Monitoring")
    assert mapping["connectors"]["table"][1]["target"] == "server=cas-shared-default;library=Monitoring;table=HMEQ"
    # a second run over a mapping that already points there reports no change
    again, changes = transfer.rehost_mapping(mapping, "h", "ACCELERATOR_RELEASES", "x", library="Monitoring")
    assert not [c for c in changes if "HMEQ" in c]


# ---- the tasks of a job, read to the end -------------------------------------------
class _Tasks(FakeSession):
    """A job with more tasks than one page gives."""

    def __init__(self, tasks, status=200):
        super().__init__()
        self._tasks, self._status = tasks, status

    def get(self, path, **kw):
        if path.endswith("/tasks"):
            self.calls.append(("GET", path, kw))
            if self._status != 200:
                return FakeResponse(self._status, {}, "service unavailable")
            start, limit = kw["params"]["start"], kw["params"]["limit"]
            return FakeResponse(200, {"items": self._tasks[start:start + limit], "count": len(self._tasks)})
        return super().get(path, **kw)


WARNING = ("\n/files/files/f4040dc0 was updated but cannot be added to the intended folder\n"
           "An item named \"tokens.py\" of type \"File\" already exists in the folder \"rag_core\".\n")


def test_the_tasks_of_a_job_are_read_to_the_end():
    """One page is not the job: a task that ended with a warning can stand
    anywhere, and the measured one stood among 71."""
    tasks = [{"name": f"object {n}", "state": "completed", "message": "The resource was successfully imported."}
             for n in range(230)]
    tasks[217] = {"name": "tokens.py", "resourceType": "application/vnd.sas.file+json",
                  "state": "completedWithWarnings", "message": WARNING}
    session = _Tasks(tasks)
    job = {"id": "i-1", "links": [{"rel": "self", "href": "/transfer/importJobs/i-1"}]}
    read = transfer.job_tasks(session, job)
    assert len(read) == 230 and read[217]["name"] == "tokens.py"
    assert [call[2]["params"]["start"] for call in session.calls] == [0, 100, 200]
    assert {call[1] for call in session.calls} == {"/transfer/importJobs/i-1/tasks"}
    # a job without a self link is asked for by its id
    assert len(transfer.job_tasks(_Tasks(tasks[:3]), {"id": "i-9"})) == 3
    with pytest.raises(RuntimeError, match="Reading the tasks of the job /transfer/importJobs/i-1 failed: HTTP 503"):
        transfer.job_tasks(_Tasks(tasks, status=503), job)


def test_a_task_that_did_not_end_completed_is_a_line():
    """`completed` with its message of success is not one; a warning, a
    failure and a task without a state are."""
    assert transfer.task_problems([
        {"name": "rag_core", "resourceType": "folder", "state": "completed",
         "message": "The resource was successfully imported."},
        {"name": "tokens.py", "resourceType": "application/vnd.sas.file+json", "state": "completedWithWarnings",
         "message": WARNING},
        {"name": "Start Here", "resourceType": "jobDefinition", "state": "failed", "message": ""},
        {"resourceName": "LLM Usage Report", "state": "Completed"},
        {"id": "t-9"},
    ]) == [
        "file 'tokens.py': completedWithWarnings - /files/files/f4040dc0 was updated but cannot be added to the "
        "intended folder / An item named \"tokens.py\" of type \"File\" already exists in the folder \"rag_core\".",
        "jobDefinition 'Start Here': failed",
        "'t-9': no state"]
    assert transfer.task_problems([]) == []


def test_a_part_of_a_package_is_uploaded_from_memory():
    """What `mdb builders-import` hands to the transfer service is not a
    file: the reports of a package, cut out of it."""
    session = FakeSession()
    data = json.dumps({"name": "Prompt Builder", "transferDetails": []}).encode("utf-8")
    assert transfer.upload_package_bytes(session, "SAS-Agentic-AI-Accelerator.json", data)["id"] == "p-9"
    method, path, kw = session.calls[-1]
    assert (method, path) == ("POST", "/transfer/packages")
    assert kw["files"] == {"file": ("SAS-Agentic-AI-Accelerator.json", data, "application/json")}
