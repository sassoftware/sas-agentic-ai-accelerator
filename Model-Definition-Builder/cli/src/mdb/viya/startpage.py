# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Runs the job definition Start Here of a content folder and reads its
installation check back - what `mdb check` prints.

The job is submitted through SAS Job Execution with the argument
`mdbReport=1`, waited for, and its log is read: a job submitted through the
API keeps its log and no `_webout` (measured on 2026-11-03), so the program
writes its lines into the log as well - compressed JSON in base64, in lines
the log does not wrap. The job record is deleted when the check has read it.

Nothing here changes the site but the job record it creates and deletes:
the program of Start Here only reads.
"""
from __future__ import annotations

import base64
import json
import re
import time
import zlib
from dataclasses import dataclass, field
from typing import Callable, Optional

from .answer import answer, one_line
from .content import DeployError, find_folder, folder_members, _members_named

#: The name of the job definition at the root of the content folder.
START_HERE = "Start Here"
JOB_REQUEST = "application/vnd.sas.job.execution.job.request+json"
JOB = "application/vnd.sas.job.execution.job+json"
#: The states of a job that has ended.
ENDED = ("completed", "failed", "canceled", "cancelled", "timedOut", "warning")
#: A line of the report in the log, as Start-Here.sas writes it (REPORT_MARKER).
MARKER = re.compile(r"Start Here report (\d+)/(\d+) ([A-Za-z0-9+/=]+)\s*$")
#: What the program writes where PROC PYTHON did not run in the compute context.
NO_PYTHON = "the Python program left no page"
#: The kind of the report the program writes.
REPORT_KIND = "sas-agentic-ai-accelerator/start-here"


class CheckError(RuntimeError):
    """The check could not be run: the reason, in one line."""


@dataclass
class CheckRun:
    """One run of Start Here: the job as it ended, the lines of its log and the report in them."""

    state: str
    job_id: str = ""
    error: str = ""
    log: list = field(default_factory=list)
    report: Optional[dict] = None
    seconds: float = 0.0
    context: str = ""

    @property
    def python_missing(self) -> bool:
        return any(NO_PYTHON in line for line in self.log)


def find_start_here(session, content_root: str) -> str:
    """The uri of the job definition Start Here at the root of the content folder."""
    try:
        folder = find_folder(session, content_root)
    except DeployError as exc:
        raise CheckError(str(exc)) from exc
    if folder is None:
        raise CheckError(f"There is no folder {content_root} in SAS Content. Import the package of the accelerator with `mdb "
                         "builders-import`, or name the folder it was imported into with --content-root.")
    try:
        members = folder_members(session, folder["id"])
    except DeployError as exc:
        raise CheckError(str(exc)) from exc
    found = _members_named(members, START_HERE, "jobDefinition")
    if not found:
        raise CheckError(f"There is no job definition {START_HERE} in {content_root}. Run `mdb check` without --no-deploy, "
                         "which deploys it, or `mdb content-deploy --only \"Start Here\"`.")
    if len(found) > 1:
        raise CheckError(f"{len(found)} job definitions {START_HERE} are in {content_root}, and nothing says which one is meant. "
                         "Remove or rename all but one of them.")
    return str(found[0]["uri"])


def submit(session, definition_uri: str, context: Optional[str] = None) -> dict:
    """Submits the job: the report is asked for, and the compute context is the one given - else the one
    the job definition names."""
    arguments = {"mdbReport": "1"}
    if context:
        arguments["_contextName"] = context
    body = {"name": f"{START_HERE} (mdb check)", "jobDefinitionUri": definition_uri, "arguments": arguments}
    try:
        response = session.post("/jobExecution/jobs", data=json.dumps(body),
                                headers={"Content-Type": JOB_REQUEST, "Accept": JOB})
    except Exception as exc:
        raise CheckError(f"Submitting {START_HERE} got no answer ({type(exc).__name__}: {one_line(str(exc))})") from exc
    if not response.ok:
        raise CheckError(f"SAS Job Execution did not take the job {START_HERE}: {answer(response)}")
    return response.json()


def wait(session, job_id: str, timeout: float, sleep: Callable[[float], None] = time.sleep,
         clock: Callable[[], float] = time.monotonic, every: float = 3.0) -> dict:
    """The job once it has ended, or as it is when the time is up."""
    started = clock()
    while True:
        response = session.get(f"/jobExecution/jobs/{job_id}", headers={"Accept": JOB})
        job = response.json() if response.ok else {}
        if job.get("state") in ENDED or clock() - started >= timeout:
            return job
        sleep(every)


def log_of(session, job: dict) -> list:
    """The lines of the log of a job: the log as the Files service keeps it - a collection of lines, or text."""
    location = str(job.get("logLocation") or "")
    if not location:
        return []
    try:
        response = session.get(location.rstrip("/") + "/content")
    except Exception:
        return []
    if not response.ok:
        return []
    try:
        body = response.json()
    except ValueError:
        return response.text.splitlines()
    if isinstance(body, dict) and isinstance(body.get("items"), list):
        return [str(item.get("line") or "") for item in body["items"] if isinstance(item, dict)]
    return []


def report_from(lines: list) -> Optional[dict]:
    """The report the program wrote into the log, or None where there is none that is whole."""
    chunks: dict = {}
    total = 0
    for line in lines:
        found = MARKER.search(line)
        if found:
            number, total = int(found.group(1)), int(found.group(2))
            chunks.setdefault(number, found.group(3))
    if not total or sorted(chunks) != list(range(1, total + 1)):
        return None
    try:
        report = json.loads(zlib.decompress(base64.b64decode("".join(chunks[number] for number in range(1, total + 1)))))
    except (ValueError, zlib.error):
        return None
    return report if isinstance(report, dict) and report.get("kind") == REPORT_KIND else None


def run_start_here(session, definition_uri: str, context: Optional[str] = None, timeout: float = 300.0,
                   sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> CheckRun:
    """Submits Start Here, waits for it, reads the report from its log and deletes the job record."""
    started = clock()
    job = submit(session, definition_uri, context)
    job_id = str(job.get("id") or "")
    try:
        job = wait(session, job_id, timeout, sleep, clock)
        state = str(job.get("state") or "unknown")
        log = log_of(session, job) if state in ENDED else []
        error = job.get("error") if isinstance(job.get("error"), dict) else {}
        ran_in = (job.get("results") or {}).get("COMPUTE_CONTEXT") if isinstance(job.get("results"), dict) else ""
        return CheckRun(state=state if state in ENDED else "still running", job_id=job_id, error=one_line(error.get("message") or ""),
                        log=log, report=report_from(log), seconds=clock() - started, context=str(ran_in or context or ""))
    finally:
        if job_id and str(job.get("state") or "") in ENDED:
            try:
                session.delete(f"/jobExecution/jobs/{job_id}")
            except Exception:
                pass
