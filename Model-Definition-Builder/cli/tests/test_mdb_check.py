# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""mdb check: Start Here is submitted through SAS Job Execution, its report is read from the log of
the job, and the lines are printed by part with what to do.

The report is made here by the program itself: the Python of Start-Here.sas is run with a stand-in
for the SAS object, so a change to the way the program writes its report that mdb cannot read fails
here. SAS Job Execution is a fake that answers the four requests a run makes.
"""
import io
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console
from typer.testing import CliRunner

from mdb import cli
from mdb.viya import startpage
from mdb.viya.startpage import CheckError, CheckRun, find_start_here, report_from, run_start_here

REPO = Path(__file__).resolve().parents[3]
PROGRAM = REPO / "SAS-Viya-Integrations" / "Setup" / "Start-Here.sas"
ROOT = "/SAS Agentic AI Accelerator"
DEFINITION = "/jobDefinitions/definitions/0f0f0f0f-1111-2222-3333-444444444444"


# ---------------------------------------------------------------------------
# The report as the program writes it
# ---------------------------------------------------------------------------

class SAS:
    """What the functions of the program that make the report need of PROC PYTHON."""

    def __init__(self):
        self.messages = []

    def logMessage(self, message, messageType="NOTE"):
        self.messages.append((messageType, message))


def program():
    """The functions of the Python of Start-Here.sas, without the run at its end."""
    lines = PROGRAM.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n")
    block = "\n".join(lines[lines.index("submit;") + 1:lines.index("endsubmit;")]).rstrip()
    assert block.endswith("\nstart_here()")
    namespace = {"SAS": SAS(), "__name__": "start_here"}
    exec(compile(block[:-len("start_here()")], "Start-Here.sas (python)", "exec"), namespace)
    return namespace


def line(part, section, what, state, found="", todo=""):
    return {"part": part, "section": section, "what": what, "state": state, "found": found, "todo": todo}


#: The lines of a site that has prompt building and logging, two things to do in logging and RAG off
SITE = [
    line("Prompt building", "Release", "release.json", "ok", "release 2.4.2, commit 12457858abcd, of 2026-11-03"),
    line("Prompt building", "SAS Model Manager", "Repository LLM Repository", "ok", "found"),
    line("Logging and the LLM Usage Report", "CAS tables of the reports", "Public.LLM_LOGS (LLM Usage Report)", "missing",
         "not in the CAS library", "Collect the logs of the containers and run Log-Parser-Code.sas or the step LLM - Log Parser."),
    line("Logging and the LLM Usage Report", "CAS tables of the reports", "Public.PROMPT_EXPERIMENTS (LLM Usage Report)", "missing",
         "not in the CAS library", "Run Get-All-Prompts.sas or the step LLM - Get All Prompts."),
    dict(line("RAG", "Python packages: RAG", "markitdown", "not used", "not installed",
              "Nothing to do: the option enableRag of the Prompt Builder switches RAG off on this site."), used=False),
]


def log_with_report(lines=SITE, session=None, unanswered_with=None):
    """The log of a run of Start Here with mdbReport=1, made by the program: the lines of the SAS log the job keeps."""
    functions = program()
    lines = [dict(entry) for entry in lines]
    if unanswered_with:
        statuses = [0] * 20
        lines = functions["one_line_for_an_unanswered_session"](
            [functions["line"]("Release", "Content root", "not checked", "the content root: the service did not answer")] * 20 + lines,
            statuses, [unanswered_with] * 20, "https://viya.example.com", "https://sassoftware.github.io/sas-agentic-ai-accelerator")
    session = session or {"other": False, "own": True, "context": "SAS Studio compute context"}
    settings = {"contentRoot": ROOT}
    report = functions["report_of"](settings, lines, functions["part_verdicts"](lines), session, "https://viya.example.com")
    chunks = functions["report_chunks"](report)
    return (["source: 1    /* the program, echoed */", "source: say('NOTE', f'{REPORT_MARKER} {number}/{len(parts)} {part}')"]
            + ["NOTE: " + chunk for chunk in chunks] + ["NOTE: The SAS System used:"])


def test_mdb_reads_the_report_the_program_writes():
    report = report_from(log_with_report())

    assert report["kind"] == "sas-agentic-ai-accelerator/start-here"
    assert [entry["what"] for entry in report["lines"]] == [entry["what"] for entry in SITE]
    assert [(part["part"], part["verdict"]) for part in report["parts"]] == [
        ("Prompt building", "ready"), ("Logging and the LLM Usage Report", "2 to do"), ("RAG", "not used here")]
    assert report["counts"] == {"ok": 2, "missing": 2, "not checked": 0, "not used": 1}


def test_a_report_that_is_not_whole_is_none():
    log = log_with_report()
    chunks = [index for index, text in enumerate(log) if "Start Here report" in text and text.startswith("NOTE")]
    assert len(chunks) > 1

    assert report_from(log[:chunks[-1]] + log[chunks[-1] + 1:]) is None
    assert report_from([text.replace("Start Here report", "Start Here record") for text in log]) is None
    assert report_from([]) is None
    # a chunk that is there twice is read once
    assert report_from(log + [log[chunks[0]]]) == report_from(log)


# ---------------------------------------------------------------------------
# SAS Job Execution
# ---------------------------------------------------------------------------

class Response:
    def __init__(self, status=200, body=None, text=""):
        self.status_code, self._body, self.text = status, body, text or (json.dumps(body) if body is not None else "")

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        if self._body is None:
            raise ValueError("no JSON")
        return self._body


class JobExecution:
    """The folder of the content root with its members, and SAS Job Execution: a job runs for `polls` gets."""

    def __init__(self, log=None, state="completed", polls=2, members=None, refuse_submit=False, context="SAS Studio compute context"):
        self.log = log_with_report() if log is None else log
        self.state, self.polls, self.refuse_submit, self.context = state, polls, refuse_submit, context
        self.members = [{"name": "Start Here", "uri": DEFINITION, "type": "child", "contentType": "jobDefinition"}] \
            if members is None else members
        self.posted, self.deleted, self.gets = [], [], []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, path, params=None, headers=None, **kw):
        self.gets.append(path)
        if path == "/folders/folders/@item":
            return Response(200, {"id": "root-id", "name": "SAS Agentic AI Accelerator"}) if params["path"] == ROOT else Response(404)
        if path == "/folders/folders/root-id/members":
            return Response(200, {"items": self.members, "count": len(self.members)})
        if path == "/jobExecution/jobs/job-1":
            self.polls -= 1
            state = "running" if self.polls > 0 else self.state
            return Response(200, {"id": "job-1", "state": state, "logLocation": "/files/files/log-1",
                                  "results": {"COMPUTE_CONTEXT": self.context},
                                  **({"error": {"message": "the job failed"}} if state == "failed" else {})})
        if path == "/files/files/log-1/content":
            return Response(200, {"items": [{"line": text, "type": "normal"} for text in self.log]})
        raise AssertionError(path)

    def post(self, path, data=None, headers=None, **kw):
        assert path == "/jobExecution/jobs"
        self.posted.append(json.loads(data))
        if self.refuse_submit:
            return Response(403, {"message": "not allowed", "errorCode": 1001})
        return Response(201, {"id": "job-1", "state": "pending"})

    def delete(self, path, **kw):
        self.deleted.append(path)
        return Response(204)


def test_the_job_is_submitted_with_the_report_asked_for_and_its_record_deleted():
    viya = JobExecution()
    run = run_start_here(viya, DEFINITION, "SAS Studio compute context", timeout=60, sleep=lambda seconds: None)

    assert viya.posted == [{"name": "Start Here (mdb check)", "jobDefinitionUri": DEFINITION,
                            "arguments": {"mdbReport": "1", "_contextName": "SAS Studio compute context"}}]
    assert run.state == "completed" and run.report["parts"][0]["verdict"] == "ready"
    assert run.context == "SAS Studio compute context"
    assert viya.deleted == ["/jobExecution/jobs/job-1"]


def test_without_a_compute_context_the_job_runs_in_the_one_its_definition_names():
    viya = JobExecution(context="SAS Job Execution compute context")
    run = run_start_here(viya, DEFINITION, None, timeout=60, sleep=lambda seconds: None)

    assert viya.posted[0]["arguments"] == {"mdbReport": "1"}
    assert run.context == "SAS Job Execution compute context"


def test_a_job_that_does_not_end_in_time_is_left_as_it_is():
    viya = JobExecution(polls=1000)
    clock = iter(range(0, 10_000, 10))
    run = run_start_here(viya, DEFINITION, None, timeout=30, sleep=lambda seconds: None, clock=lambda: next(clock))

    assert run.state == "still running" and run.report is None and run.job_id == "job-1"
    assert viya.deleted == []


def test_a_refused_submit_is_one_line():
    with pytest.raises(CheckError, match=r"SAS Job Execution did not take the job Start Here: HTTP 403"):
        run_start_here(JobExecution(refuse_submit=True), DEFINITION, None, timeout=60, sleep=lambda seconds: None)


@pytest.mark.parametrize("members, said", [
    ([], "There is no job definition Start Here in /SAS Agentic AI Accelerator"),
    ([{"name": "Start Here", "uri": DEFINITION, "type": "child"}, {"name": "Start Here", "uri": DEFINITION + "2", "type": "child"}],
     "2 job definitions Start Here are in"),
    # a reference of that name is no job definition of the folder
    ([{"name": "Start Here", "uri": DEFINITION, "type": "reference"}], "There is no job definition Start Here"),
])
def test_the_job_definition_is_found_once_or_not_at_all(members, said):
    with pytest.raises(CheckError, match=re.escape(said)):
        find_start_here(JobExecution(members=members), ROOT)


def test_a_content_root_that_is_not_there_names_the_import():
    with pytest.raises(CheckError, match=r"There is no folder /Elsewhere in SAS Content\. Import the package"):
        find_start_here(JobExecution(), "/Elsewhere")


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------

@pytest.fixture
def command(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, width=1000, force_terminal=False))
    monkeypatch.setattr(cli, "Context", lambda: SimpleNamespace(repo=REPO))
    monkeypatch.setattr(startpage.time, "sleep", lambda seconds: None)
    deployed = []

    def deploy(session, manifest, root, dry_run, emit, only=None, **kwargs):
        deployed.append((root, dry_run, only, kwargs.get("create_root")))
        return SimpleNamespace(failed=[])
    monkeypatch.setattr("mdb.viya.content.deploy", deploy)

    def run(viya, *args):
        monkeypatch.setattr(cli, "_viya_session", lambda out=None: viya)
        out.seek(0)
        out.truncate()
        result = CliRunner().invoke(cli.app, ["check", *args])
        return result, out.getvalue(), deployed
    return run


def test_the_command_prints_the_parts_and_what_to_do(command):
    result, text, deployed = command(JobExecution())

    assert result.exit_code == 0, text + result.output
    assert "Start Here ran in SAS Studio compute context" in text and ": 2 ok, 2 missing, 0 not checked, 1 not used." in text
    assert re.search(r"Prompt building +ready", text)
    assert re.search(r"Logging and the LLM Usage Report +2 to do", text)
    assert "  missing      Public.LLM_LOGS (LLM Usage Report): not in the CAS library" in text
    assert "what to do: Collect the logs of the containers and run Log-Parser-Code.sas" in text
    assert re.search(r"RAG +not used here", text)
    # what is ok, and what is not used, is not printed without --all
    assert "release.json" not in text and "markitdown" not in text
    # the job definition was brought to the checkout first, in place in the content root, which is not created
    assert deployed == [(ROOT, False, ["Start Here"], False)]


def test_all_prints_every_line(command):
    _, text, _ = command(JobExecution(), "--all", "--no-deploy")

    assert "  ok           release.json: release 2.4.2" in text and "  not used     markitdown: not installed" in text


def test_no_deploy_leaves_the_job_definition_as_it_is(command):
    result, _, deployed = command(JobExecution(), "--no-deploy")

    assert result.exit_code == 0 and deployed == []


def test_prompt_building_that_is_not_ready_is_exit_code_1(command):
    lines = [dict(SITE[0], state="missing", found="not in the content root", todo="Run mdb content-deploy.")] + SITE[1:]
    result, text, _ = command(JobExecution(log=log_with_report(lines)), "--no-deploy")

    assert result.exit_code == 1
    assert re.search(r"Prompt building +1 to do", text) and "what to do: Run mdb content-deploy." in text


def test_a_certificate_the_compute_context_does_not_trust_is_said_with_what_mdb_knows(command):
    result, text, _ = command(JobExecution(log=log_with_report(unanswered_with="tls")), "--no-deploy")

    assert result.exit_code == 1
    assert "No request of this page reached SAS Viya: the Python of this compute context does not trust the TLS certificate" in text
    assert "what to do: Point SAS_VIYA_CABUNDLE at a file with the CA certificates of your site" in text
    assert ("mdb reached SAS Viya from this machine, and the job did not reach it from its compute server: the cause lies between "
            "the compute server and SAS Viya (https://viya.example.com), not in the installation.") in text


def test_a_job_that_ran_as_another_user_says_how_to_run_it_as_you(command):
    log = log_with_report(session={"other": True, "own": False, "context": "SAS Job Execution compute context"})
    _, text, _ = command(JobExecution(log=log), "--no-deploy")

    assert ("The job ran as another user than you, in the compute context SAS Job Execution compute context: it saw what that "
            "account may read. Run `mdb check --compute-context \"SAS Studio compute context\"`") in text


def test_a_compute_context_without_python_is_named(command):
    log = ["NOTE: Start Here: the Python program left no page, the page says so."]
    result, text, _ = command(JobExecution(log=log, context="Python-less context"), "--no-deploy")

    assert result.exit_code == 1
    assert "Start Here ran in Python-less context" in text and "gave no report" in text
    assert "PROC PYTHON did not run in the compute context Python-less context" in text


def test_a_program_older_than_mdb_says_to_deploy_it(command):
    result, text, _ = command(JobExecution(log=["NOTE: Start Here: the page holds 33 checks - 33 ok, 0 missing, 0 not checked."]),
                              "--no-deploy")

    assert result.exit_code == 1
    assert "the program of Start Here on the site is older than this mdb. Run `mdb check` without --no-deploy" in text


def test_a_job_that_failed_says_so(command):
    result, text, _ = command(JobExecution(state="failed", log=[]), "--no-deploy")

    assert result.exit_code == 1 and "The job ended failed: the job failed." in text


def test_json_is_the_report_and_the_hints(command):
    result, text, _ = command(JobExecution(log=log_with_report(unanswered_with="connect")), "--json", "--no-deploy")
    printed = json.loads(text)

    assert result.exit_code == 1
    assert printed["state"] == "completed" and printed["context"] == "SAS Studio compute context"
    assert printed["report"]["unanswered"] == "connect" and printed["report"]["lines"][0]["section"] == "SAS session of this page"
    assert printed["hints"][0].startswith("mdb reached SAS Viya from this machine")


def test_a_deploy_that_fails_stops_the_command(command, monkeypatch):
    monkeypatch.setattr("mdb.viya.content.deploy", lambda *a, **k: SimpleNamespace(failed=["Start Here"]))
    viya = JobExecution()
    result, text, _ = command(viya)

    assert result.exit_code == 1 and "Start Here could not be brought to this checkout" in text and viya.posted == []
