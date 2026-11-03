# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What `mdb check` does on a site: the installation check, the probe of the compute server, and the page.

The checks (core/start_here.py) read SAS Viya through the session of mdb, with GET requests only. What only
the compute server can say - whether PROC PYTHON runs, which packages its Python has, whether it reaches
SAS Viya and trusts its certificate, and as whom its sessions run - is asked in one compute session per
compute context: a short program of mdb's own, whose answer comes back in the log of the session. The page
is written into the job definition Start Here, which serves it as a form: opening it runs nothing.

The session and the compute sessions are deleted when they have answered; the form of Start Here is the
one thing this changes on the site.
"""
from __future__ import annotations

import base64
import json
import re
import time
import zlib
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..core import start_here as check
from .answer import answer, one_line
from .content import DeployError, _members_named, find_folder, folder_members, get_job_definition, update_job_definition

#: The name of the job definition at the root of the content folder.
START_HERE = "Start Here"
#: The settings of the check, where the job definition on the site does not hold them - the parameters of
#: the manifest entry Start Here.
DEFAULT_SETTINGS = {
    "contentRoot": "/SAS Agentic AI Accelerator", "caslib": "Public", "casServer": "cas-shared-default",
    "credentialDomain": "agentic-ai-keys", "repositoryName": "LLM Repository", "llmProject": "LLM Model Project",
    "embeddingProject": "Embedding Model Project", "decisionProject": "Decision Model Project",
    "computeContexts": "SAS Studio compute context|SAS Job Execution compute context",
    "guideUrl": "https://sassoftware.github.io/sas-agentic-ai-accelerator",
}
#: A line of the answer of the probe in the log: the marker, its number, the number of lines and base64.
MARKER = re.compile(r"mdb check probe (\d+)/(\d+) ([A-Za-z0-9+/=]+)\s*$")
#: The states of a compute job that has ended.
JOB_ENDED = ("completed", "error", "warning", "canceled", "cancelled", "failed")

#: The program of the probe. It asks SAS Viya once, at the address the SAS option SERVICESBASEURL names,
#: with the CA bundle the programs of the accelerator take (SAS_VIYA_VERIFY_SSL, SAS_VIYA_CABUNDLE,
#: SSLCALISTLOC, CAS_CLIENT_SSL_CA_LIST) - any answer is an answer, 401 included -, reads which of the
#: modules are installed, and writes what it found into the log. It uses the standard library only.
PROBE_PYTHON = r'''
import base64, json, os, socket, ssl, urllib.error, urllib.request, zlib
import importlib.util
try:
    from importlib import metadata
except ImportError:
    metadata = None
MODULES = json.loads(base64.b64decode('@MODULES@').decode('utf-8'))
WORDS = 40
found = {'user': str(SAS.symget('SYSUSERID') or ''), 'host': str(SAS.symget('_mc_viya') or '').rstrip('/'), 'packages': {}}
for module, names in MODULES.items():
    try:
        installed = importlib.util.find_spec(module) is not None
    except Exception:
        installed = False
    if not installed:
        continue
    version = ''
    for name in names:
        try:
            version = metadata.version(name) if metadata else ''
            break
        except Exception:
            continue
    found['packages'][module] = version
off = os.environ.get('SAS_VIYA_VERIFY_SSL', '').strip().lower() in ('false', 'no', '0')
bundle = os.environ.get('SAS_VIYA_CABUNDLE') or os.environ.get('SSLCALISTLOC') or os.environ.get('CAS_CLIENT_SSL_CA_LIST')
try:
    if off:
        context = ssl._create_unverified_context()
    else:
        context = ssl.create_default_context(cafile=bundle) if bundle else ssl.create_default_context()
    try:
        with urllib.request.urlopen(urllib.request.Request(found['host'] + '/identities/', method='GET'), timeout=30, context=context) as answer:
            found['status'] = answer.status
    except urllib.error.HTTPError as error:
        found['status'] = error.code
except Exception as error:
    reason = getattr(error, 'reason', error)
    names = [klass.__name__ for klass in type(reason).__mro__] + [klass.__name__ for klass in type(error).__mro__]
    text = str(reason)
    if any('SSL' in name or 'Certificate' in name for name in names) or 'CERTIFICATE_VERIFY_FAILED' in text:
        found['failure'] = 'tls'
    elif any(name in names for name in ('timeout', 'TimeoutError')) or 'timed out' in text:
        found['failure'] = 'timeout'
    elif any(name in names for name in ('gaierror', 'ConnectionRefusedError', 'ConnectionError', 'OSError')):
        found['failure'] = 'connect'
    else:
        found['failure'] = 'other'
    found['error'] = type(reason).__name__ + ': ' + ' '.join(text.split()[:WORDS])
packed = base64.b64encode(zlib.compress(json.dumps(found).encode('utf-8'), 9)).decode('ascii')
parts = [packed[start:start + 76] for start in range(0, len(packed), 76)] or ['']
for number, part in enumerate(parts, 1):
    print('mdb check probe ' + str(number) + '/' + str(len(parts)) + ' ' + part)
'''


class CheckError(RuntimeError):
    """The check could not be run: the reason, in one line."""


@dataclass
class CheckResult:
    """One run of the check: its lines, the verdicts, the probes and the page written."""

    lines: list
    verdicts: list
    reports: dict
    probes: list = field(default_factory=list)
    page: str = ""
    written: bool = False

    @property
    def counts(self) -> dict:
        return check.counts_of(self.lines)

    @property
    def core_ready(self) -> bool:
        return any(part == check.CORE and verdict == "ready" for part, verdict, _ in self.verdicts)


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


def settings_of(definition: dict, content_root: str) -> dict:
    """The settings of the check: the defaults of the parameters of Start Here on the site, else the
    release's; the content root is the folder the check is run for."""
    settings = dict(DEFAULT_SETTINGS)
    for parameter in definition.get("parameters") or []:
        name, value = parameter.get("name"), parameter.get("defaultValue")
        if name in settings and isinstance(value, str) and value.strip():
            settings[name] = value.strip()
    settings["contentRoot"] = content_root.rstrip("/")
    return settings


def get_of(session) -> Callable:
    """The get(path, accept) of the checks, over the session of mdb: (status, bytes); an exception for no answer."""
    def get(path, accept="application/json"):
        response = session.get(path, headers={"Accept": accept})
        return response.status_code, response.content
    return get


def current_user(session) -> str:
    """The id of the user mdb signed in as, or the empty text."""
    try:
        response = session.get("/identities/users/@currentUser", headers={"Accept": "application/json"})
        return str(response.json().get("id") or "") if response.ok else ""
    except Exception:
        return ""


def probe_program() -> list:
    """The lines of the program of the probe: the address of SAS Viya for the Python, and the Python."""
    modules = {}
    for _, _, module, names, _, _ in check.PACKAGES:
        modules.setdefault(module, list(names))
    packed = base64.b64encode(json.dumps(modules).encode("utf-8")).decode("ascii")
    return (["%let _mc_viya = %sysfunc(getoption(SERVICESBASEURL));", "proc python restart;", "submit;"]
            + PROBE_PYTHON.replace("@MODULES@", packed).strip("\n").split("\n") + ["endsubmit;", "run;", "quit;"])


def answer_in(lines: list) -> Optional[dict]:
    """The answer the probe wrote into the log, or None where there is none that is whole."""
    chunks: dict = {}
    total = 0
    for text in lines:
        found = MARKER.search(text)
        if found:
            number, total = int(found.group(1)), int(found.group(2))
            chunks.setdefault(number, found.group(3))
    if not total or sorted(chunks) != list(range(1, total + 1)):
        return None
    try:
        value = json.loads(zlib.decompress(base64.b64decode("".join(chunks[number] for number in range(1, total + 1)))))
    except (ValueError, zlib.error):
        return None
    return value if isinstance(value, dict) else None


def probe(session, context: str, parts: list, user: str, timeout: float = 180.0,
          sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic) -> dict:
    """What the compute server of one compute context says, as core.start_here.compute_lines reads it. The
    compute session is deleted when it has answered."""
    found = {"context": context, "parts": list(parts), "started": False, "python": False}
    try:
        listed = session.get("/compute/contexts", params={"filter": f'eq(name,"{context}")', "limit": 10},
                             headers={"Accept": "application/json"})
        items = [item for item in (listed.json().get("items") or []) if item.get("name") == context] if listed.ok else []
        if not items:
            found["error"] = "no compute context of that name" if listed.ok else answer(listed)
            return found
        started = session.post(f"/compute/contexts/{items[0]['id']}/sessions", data=json.dumps({"name": "mdb check"}),
                               headers={"Content-Type": "application/json", "Accept": "application/json"})
        if not started.ok:
            found["error"] = answer(started)
            return found
    except Exception as exc:
        found["error"] = f"{type(exc).__name__}: {one_line(str(exc))}"
        return found
    session_id = started.json().get("id")
    found["started"] = True
    try:
        job = session.post(f"/compute/sessions/{session_id}/jobs", data=json.dumps({"code": probe_program()}),
                           headers={"Content-Type": "application/json", "Accept": "application/json"})
        if not job.ok:
            found["error"] = answer(job)
            return found
        job_id = job.json().get("id")
        begun = clock()
        while True:
            state = session.get(f"/compute/sessions/{session_id}/jobs/{job_id}/state", headers={"Accept": "text/plain"})
            if (state.text or "").strip() in JOB_ENDED or clock() - begun >= timeout:
                break
            sleep(2)
        log = session.get(f"/compute/sessions/{session_id}/jobs/{job_id}/log", params={"limit": 100000},
                          headers={"Accept": "application/json"})
        lines = [str(item.get("line") or "") for item in (log.json().get("items") or [])] if log.ok else []
        said = answer_in(lines)
        if said is None:
            return found
        found["python"] = True
        found["packages"] = said.get("packages") or {}
        found["viya"] = {"status": said.get("status"), "failure": said.get("failure", ""), "host": said.get("host", "")}
        if user and said.get("user"):
            found["sameUser"] = check.person_of(said["user"]) == check.person_of(user)
        return found
    except Exception as exc:
        found["error"] = f"{type(exc).__name__}: {one_line(str(exc))}"
        return found
    finally:
        try:
            session.delete(f"/compute/sessions/{session_id}")
        except Exception:
            pass


def write_page(session, uri: str, page: str) -> None:
    """The page as the form of the job definition, in place: its program, its parameters and its id stay."""
    live, etag = get_job_definition(session, uri)
    update_job_definition(session, uri, live, {"properties": [{"name": "form", "value": page}]}, etag)


def run_check(session, content_root: str, context: Optional[str] = None, checked: Optional[dict] = None,
              write: bool = True, probe_with: Callable = probe) -> CheckResult:
    """The check of a content folder: the settings of its Start Here, one probe per compute context the
    parts run in, every line, and - unless `write` is off - the page written into Start Here."""
    uri = find_start_here(session, content_root)
    try:
        definition, _ = get_job_definition(session, uri)
    except DeployError as exc:
        raise CheckError(str(exc)) from exc
    settings = settings_of(definition, content_root)
    get = get_of(session)
    options = check.read_options(get, settings)
    user = current_user(session)
    probes = [probe_with(session, name, parts, user) for name, parts in check.contexts_to_probe(options, settings, context)]
    statuses: list = []
    lines, reports = check.check_installation(check.recorded(get, statuses), settings, probes)
    lines = check.one_line_for_a_refused_session(lines, statuses)
    # the address of SAS Viya the compute server named: a guide on this SAS Viya is a link
    host = next((p["viya"].get("host", "") for p in probes if isinstance(p.get("viya"), dict) and p["viya"].get("host")), "")
    page = check.render_page(settings, lines, reports, checked, host)
    result = CheckResult(lines=lines, verdicts=check.part_verdicts(lines), reports=reports, probes=probes, page=page)
    if write:
        try:
            write_page(session, uri, page)
        except DeployError as exc:
            raise CheckError(f"The check ran, and the page could not be written into {START_HERE}: {exc}") from exc
        result.written = True
    return result
