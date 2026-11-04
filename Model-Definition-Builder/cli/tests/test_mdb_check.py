# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""mdb check on a site: the probe of the compute server, the page written into Start Here, and the command.

The probe is a short program of mdb's own that runs in a compute session; here its Python runs with a
stand-in for the SAS object against an address where nothing listens, so what it writes into the log is
what mdb reads. The Compute service and the job definition are fakes that answer the requests a run makes.
The checks themselves are tested in test_start_here*.py.
"""
import io
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console
from typer.testing import CliRunner

import start_here_harness as harness
from mdb import cli
from mdb.core import start_here
from mdb.viya import startpage
from mdb.viya.startpage import (CheckError, CheckResult, PROBE_PYTHON, answer_in, find_start_here, probe, probe_program, run_check,
                                settings_of)

REPO = Path(__file__).resolve().parents[3]
ROOT = "/SAS Agentic AI Accelerator"
DEFINITION = "/jobDefinitions/definitions/0f0f0f0f-1111-2222-3333-444444444444"


# ---------------------------------------------------------------------------
# The program of the probe
# ---------------------------------------------------------------------------

class SAS:
    def __init__(self, values):
        self.values = values

    def symget(self, name):
        return self.values[name]


def run_probe(host, capsys):
    """The Python of the probe, as PROC PYTHON runs it, and the lines it printed."""
    lines = probe_program()
    python = "\n".join(lines[lines.index("submit;") + 1:lines.index("endsubmit;")])
    exec(compile(python, "probe", "exec"), {"SAS": SAS({"SYSUSERID": "uid7781", "_mc_viya": host}), "__name__": "probe"})
    return capsys.readouterr().out.splitlines()


def test_the_probe_says_what_it_found_in_lines_mdb_reads(capsys):
    printed = run_probe("https://127.0.0.1:9", capsys)
    found = answer_in(["NOTE: " + text for text in printed])

    assert found["user"] == "uid7781" and found["host"] == "https://127.0.0.1:9"
    # nothing listens there: no answer, and the kind of failure
    assert found["failure"] in ("connect", "timeout") and "status" not in found
    # the packages are the modules of the check that this Python has
    assert set(found["packages"]) <= {module for _, _, module, _, _, _ in start_here.PACKAGES}
    assert "requests" in found["packages"]
    # the lines are short: the log of a compute job wraps a line at its width
    assert printed and all(len("NOTE: " + text) < 128 for text in printed)


def test_the_program_of_the_probe_runs_in_one_proc_python_and_holds_no_macro_trigger():
    lines = probe_program()

    assert lines[0] == "%let _mc_viya = %sysfunc(getoption(SERVICESBASEURL));"
    assert lines[1:3] == ["proc python restart;", "submit;"] and lines[-3:] == ["endsubmit;", "run;", "quit;"]
    python = "\n".join(lines[3:-3])
    # an ampersand or a percent sign in the submitted Python would be read by the macro processor
    assert "&" not in python and "%" not in python and "/*" not in python
    # it asks SAS Viya once, with GET, and imports nothing that is not in the standard library
    assert python.count("urlopen(") == 1 and "method='GET'" in python
    assert not re.search(r"^\s*import (requests|pandas|swat)", python, flags=re.MULTILINE)


def test_an_answer_that_is_not_whole_is_none(capsys):
    printed = ["NOTE: " + text for text in run_probe("https://127.0.0.1:9", capsys)]

    assert answer_in(printed[1:]) is None or len(printed) == 1
    assert answer_in([text.replace("mdb check probe", "mdb check other") for text in printed]) is None
    assert answer_in([]) is None


# ---------------------------------------------------------------------------
# The Compute service
# ---------------------------------------------------------------------------

class Response:
    def __init__(self, status=200, body=None, text=None):
        self.status_code, self._body = status, body
        self.text = text if text is not None else (json.dumps(body) if body is not None else "")
        self.content = self.text.encode("utf-8")
        self.headers = {"ETag": '"e1"'}

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        if self._body is None:
            raise ValueError("no JSON")
        return self._body


class Compute:
    """The Compute service: a context, a session, a job whose log holds what the probe printed."""

    def __init__(self, log, contexts=("SAS Studio compute context",), refuse_session=False, state="completed"):
        self.log, self.contexts, self.refuse_session, self.state = log, contexts, refuse_session, state
        self.posted, self.deleted = [], []

    def get(self, path, params=None, headers=None, **kw):
        if path == "/compute/contexts":
            wanted = re.search(r'eq\(name,"(.*)"\)', params["filter"]).group(1)
            return Response(200, {"items": [{"id": "ctx-1", "name": name} for name in self.contexts if name == wanted]})
        if path == "/compute/sessions/ses-1/jobs/job-1/state":
            return Response(200, text=self.state)
        if path == "/compute/sessions/ses-1/jobs/job-1/log":
            return Response(200, {"items": [{"line": text} for text in self.log]})
        raise AssertionError(path)

    def post(self, path, data=None, headers=None, **kw):
        self.posted.append((path, json.loads(data)))
        if path == "/compute/contexts/ctx-1/sessions":
            return Response(403, {"message": "not allowed"}) if self.refuse_session else Response(201, {"id": "ses-1"})
        if path == "/compute/sessions/ses-1/jobs":
            return Response(201, {"id": "job-1"})
        raise AssertionError(path)

    def delete(self, path, **kw):
        self.deleted.append(path)
        return Response(204)


def probe_log(capsys, **change):
    """The log of a probe that found these values."""
    import base64
    import zlib
    found = {"user": "uid7781", "host": "https://viya.example.com", "packages": {"requests": "2.32.3"}, "status": 401}
    found.update(change)
    packed = base64.b64encode(zlib.compress(json.dumps(found).encode("utf-8"))).decode("ascii")
    parts = [packed[start:start + 76] for start in range(0, len(packed), 76)]
    return ["1    proc python restart;"] + [f"mdb check probe {n}/{len(parts)} {part}" for n, part in enumerate(parts, 1)]


def test_a_probe_starts_one_session_reads_its_log_and_deletes_it(capsys):
    compute = Compute(probe_log(capsys))
    found = probe(compute, "SAS Studio compute context", ["Prompt building"], "uid7781", sleep=lambda s: None)

    assert found == {"context": "SAS Studio compute context", "parts": ["Prompt building"], "started": True, "python": True,
                     "packages": {"requests": "2.32.3"}, "viya": {"status": 401, "failure": "", "host": "https://viya.example.com"},
                     "sameUser": True}
    assert [path for path, _ in compute.posted] == ["/compute/contexts/ctx-1/sessions", "/compute/sessions/ses-1/jobs"]
    assert compute.posted[1][1]["code"] == probe_program()
    assert compute.deleted == ["/compute/sessions/ses-1"]


def test_a_session_of_another_user_is_said_and_no_user_id_goes_further(capsys):
    found = probe(Compute(probe_log(capsys, user="svc4410")), "SAS Studio compute context", ["RAG"], "uid7781", sleep=lambda s: None)

    assert found["sameUser"] is False and "uid7781" not in json.dumps(found) and "svc4410" not in json.dumps(found)
    # one person, written with a domain, is the same person
    assert probe(Compute(probe_log(capsys, user="EXAMPLE\\UID7781")), "SAS Studio compute context", ["RAG"], "uid7781",
                 sleep=lambda s: None)["sameUser"] is True


def test_a_log_without_an_answer_is_a_context_without_python(capsys):
    found = probe(Compute(["ERROR: The PYTHON procedure is not available."]), "SAS Studio compute context", ["Prompt building"], "u",
                  sleep=lambda s: None)

    assert found["started"] is True and found["python"] is False


def test_a_context_that_is_not_there_or_refuses_a_session_is_not_started(capsys):
    missing = probe(Compute([], contexts=()), "Nowhere", ["Prompt building"], "u", sleep=lambda s: None)
    refused = probe(Compute([], refuse_session=True), "SAS Studio compute context", ["Prompt building"], "u", sleep=lambda s: None)

    assert missing["started"] is False and missing["error"] == "no compute context of that name"
    assert refused["started"] is False and refused["error"].startswith("HTTP 403")


# ---------------------------------------------------------------------------
# The settings and the page
# ---------------------------------------------------------------------------

def test_the_settings_are_the_ones_the_site_gave_start_here():
    definition = {"parameters": [{"name": "caslib", "defaultValue": "Reports"}, {"name": "repositoryName", "defaultValue": ""},
                                 {"name": "_action", "defaultValue": "form"}]}
    settings = settings_of(definition, "/Team/Accelerator/")

    assert settings["caslib"] == "Reports" and settings["repositoryName"] == "LLM Repository"
    assert settings["contentRoot"] == "/Team/Accelerator" and "_action" not in settings


class Folder:
    """The content root with the job definition Start Here."""

    def __init__(self, members=None):
        self.members = [{"name": "Start Here", "uri": DEFINITION, "type": "child", "contentType": "jobDefinition"}] \
            if members is None else members
        self.put = []

    def get(self, path, params=None, headers=None, **kw):
        if path == "/folders/folders/@item":
            return Response(200, {"id": "root-id"}) if params["path"] == ROOT else Response(404)
        if path == "/folders/folders/root-id/members":
            return Response(200, {"items": self.members, "count": len(self.members)})
        if path == DEFINITION:
            return Response(200, {"id": "0f0f", "name": "Start Here", "code": "/* SAS templated code goes here */",
                                  "parameters": [{"name": "caslib", "defaultValue": "Public"}],
                                  "properties": [{"name": "form", "value": "<p>the page of the release</p>"}]})
        raise AssertionError(path)

    def put_(self, path, data=None, headers=None, **kw):
        self.put.append((path, json.loads(data), headers))
        return Response(200, {})


@pytest.mark.parametrize("members, said", [
    ([], "There is no job definition Start Here in /SAS Agentic AI Accelerator"),
    ([{"name": "Start Here", "uri": DEFINITION, "type": "child"}, {"name": "Start Here", "uri": DEFINITION + "2", "type": "child"}],
     "2 job definitions Start Here are in"),
    ([{"name": "Start Here", "uri": DEFINITION, "type": "reference"}], "There is no job definition Start Here"),
])
def test_the_job_definition_is_found_once_or_not_at_all(members, said):
    with pytest.raises(CheckError, match=re.escape(said)):
        find_start_here(Folder(members), ROOT)


def test_a_content_root_that_is_not_there_names_the_import():
    with pytest.raises(CheckError, match=r"There is no folder /Elsewhere in SAS Content\. Import the package"):
        find_start_here(Folder(), "/Elsewhere")


def test_the_page_is_written_into_the_form_and_the_rest_of_the_definition_stays(monkeypatch):
    folder = Folder()
    folder.put = []
    session = SimpleNamespace(get=folder.get, put=folder.put_)
    site = harness.Site()
    monkeypatch.setattr(startpage, "get_of", lambda s: harness.get_of(site))
    monkeypatch.setattr(startpage, "current_user", lambda s: "uid7781")
    asked = []

    def fake_probe(session, context, parts, user):
        asked.append((context, parts, user))
        return harness.probe(context=context, parts=parts)

    result = run_check(session, ROOT, None, {"when": "2026-11-03 10:00 UTC", "by": "mdb check 2.4.3"}, probe_with=fake_probe)

    assert result.written and result.core_ready
    # one probe per compute context the parts run in, as the options of the Prompt Builder of the site name them
    assert asked == [("SAS Studio compute context", ["Prompt building", "Logging and the LLM Usage Report", "RAG"], "uid7781"),
                     ("Optimize compute context", ["Prompt optimization"], "uid7781")]
    [(path, body, headers)] = folder.put
    assert path == DEFINITION and headers["If-Match"] == '"e1"'
    assert body["code"] == "/* SAS templated code goes here */" and body["parameters"] == [{"name": "caslib", "defaultValue": "Public"}]
    assert body["properties"] == [{"name": "form", "value": result.page}]
    assert "Last checked on 2026-11-03 10:00 UTC by mdb check 2.4.3" in result.page and "{{" not in result.page


def test_without_write_the_page_stays(monkeypatch):
    folder = Folder()
    session = SimpleNamespace(get=folder.get, put=folder.put_)
    monkeypatch.setattr(startpage, "get_of", lambda s: harness.get_of(harness.Site()))
    monkeypatch.setattr(startpage, "current_user", lambda s: "")
    result = run_check(session, ROOT, None, None, write=False, probe_with=lambda *a: harness.probe())

    assert not result.written and folder.put == []


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------

def result_of(lines=None, probes=None):
    """A CheckResult of the harness's complete site, with these lines in place of its own where given."""
    lines = lines if lines is not None else harness_lines()
    return CheckResult(lines=lines, verdicts=start_here.part_verdicts(lines), reports={}, probes=probes or [harness.probe()],
                       page="<p>page</p>", written=True)


def harness_lines(change=None, probes=None):
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        site = harness.Site()
        if change:
            change(site)
        return harness.run(None, folder, site, probes=probes).lines


@pytest.fixture
def command(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=out, width=1000, force_terminal=False))
    monkeypatch.setattr(cli, "Context", lambda: SimpleNamespace(repo=REPO))
    deployed = []

    def deploy(session, manifest, root, dry_run, emit, only=None, **kwargs):
        deployed.append((root, dry_run, only, kwargs.get("create_root")))
        return SimpleNamespace(failed=[])
    monkeypatch.setattr("mdb.viya.content.deploy", deploy)
    session = SimpleNamespace(__enter__=None)

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def run(result, *args, raises=None):
        calls = []

        def fake_run_check(session, root, context, checked, write=True):
            calls.append((root, context, checked, write))
            if raises:
                raise raises
            return result
        monkeypatch.setattr("mdb.viya.startpage.run_check", fake_run_check)
        monkeypatch.setattr(cli, "_viya_session", lambda out=None: Session())
        out.seek(0)
        out.truncate()
        outcome = CliRunner().invoke(cli.app, ["check", *args])
        return outcome, out.getvalue(), deployed, calls
    return run


def test_the_command_prints_the_parts_and_writes_the_page(command):
    outcome, text, deployed, calls = command(result_of())

    assert outcome.exit_code == 0, text + outcome.output
    assert re.search(r"Prompt building +ready", text) and re.search(r"Decision models +ready", text)
    assert "The page Start Here holds this check now." in text
    # the job definition was brought to the checkout first, in place in the content root, which is not created
    assert deployed == [(ROOT, False, ["Start Here"], False)]
    [(root, context, checked, write)] = calls
    assert root == ROOT and context is None and write is True
    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d UTC", checked["when"]) and checked["by"].startswith("mdb check ")


def test_a_line_that_is_not_ok_is_printed_with_what_to_do_and_is_exit_code_1(command):
    lines = harness_lines(lambda site: site.tables.pop("ACCELERATOR_RELEASES"))
    outcome, text, _, _ = command(result_of(lines), "--no-deploy")

    assert outcome.exit_code == 1
    assert re.search(r"Prompt building +1 to do", text)
    assert "  missing      Public.ACCELERATOR_RELEASES (Prompt Builder): not in the CAS library" in text
    assert "what to do: Run the job Load-Accelerator-Tables of the folder Data in this content root, or mdb load-releases." in text


def test_a_certificate_the_compute_server_does_not_trust_is_said_with_what_mdb_knows(command):
    probes = [harness.probe(viya={"status": None, "failure": "tls", "host": "https://viya.example.com"})]
    outcome, text, _, _ = command(result_of(harness_lines(probes=probes), probes), "--no-deploy")

    assert outcome.exit_code == 1
    assert "SAS Studio compute context: SAS Viya from the compute server: the Python of this compute context does not trust" in text
    assert "what to do: Point SAS_VIYA_CABUNDLE at a file with the CA certificates of your site" in text
    assert ("mdb reached SAS Viya from this machine, and the compute server of SAS Studio compute context did not: the cause lies "
            "between the compute server and SAS Viya, not in the installation.") in text


def test_all_prints_every_line_and_no_page_leaves_it(command):
    outcome, text, deployed, calls = command(result_of(), "--all", "--no-deploy", "--no-page", "--compute-context", "Mine")

    assert "  ok           release.json: release 2.3.0" in text and deployed == []
    assert calls[0][1] == "Mine" and calls[0][3] is False


def test_json_is_the_check(command):
    outcome, text, _, _ = command(result_of(), "--json", "--no-deploy")
    printed = json.loads(text)

    assert outcome.exit_code == 0 and printed["pageWritten"] is True and printed["contentRoot"] == ROOT
    assert [part["verdict"] for part in printed["parts"]] == ["ready"] * 5
    assert set(printed["lines"][0]) == {"part", "section", "what", "state", "found", "todo"}


def test_a_check_that_could_not_run_is_one_line(command):
    outcome, text, _, _ = command(None, "--no-deploy", raises=CheckError("There is no folder /X in SAS Content."))

    assert outcome.exit_code == 1 and "There is no folder /X in SAS Content." in text


def test_a_deploy_that_fails_stops_the_command(command, monkeypatch):
    monkeypatch.setattr("mdb.viya.content.deploy", lambda *a, **k: SimpleNamespace(failed=["Start Here"]))
    outcome, text, _, calls = command(result_of())

    assert outcome.exit_code == 1 and "Start Here could not be brought to this checkout" in text and calls == []
