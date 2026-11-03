"""Start-Here.sas: the verdict of each part of the accelerator, the one line for a session whose
requests got no answer at all, and the report mdb check reads from the log of the job.

A user of 2.4.x opened the page in a compute context whose Python did not trust the certificate of
its SAS Viya: 20 lines said "the service did not answer" with the advice to look at the state of a
service that was well. And on a site that does not use RAG, the lines of the RAG runtime read
missing like the ones that matter.
"""
import base64
import json
import re
import zlib

import pytest

import start_here_harness as harness
from start_here_harness import ALL_PACKAGES, Site, get_of, load, run

OK, MISSING, NOT_CHECKED, NOT_USED = "ok", "missing", "not checked", "not used"


# ---------------------------------------------------------------------------
# A site whose requests end without an answer
# ---------------------------------------------------------------------------

# The exceptions of requests, by the names of their classes: the SSLError of requests is a ConnectionError too
class ConnectionError(OSError):                                    # noqa: A001 - the name requests gives it
    pass


class SSLError(ConnectionError):
    pass


class Timeout(OSError):
    pass


class ConnectTimeout(ConnectionError, Timeout):
    pass


class Unanswered(Site):
    """A site where every request ends with an exception - or the ones below a prefix."""

    def __init__(self, error, prefixes=("/",)):
        super().__init__()
        self.error, self.failing = error, prefixes

    def get(self, url, headers=None, verify=None, timeout=None):
        path = url[len(harness.VIYA_HOST):]
        if any(path.startswith(prefix) for prefix in self.failing):
            self.requests.append((path, {}))
            raise self.error
        return super().get(url, headers=headers, verify=verify, timeout=timeout)


CERTIFICATE = SSLError("HTTPSConnectionPool(host='viya.example.com', port=443): Max retries exceeded with url: /folders/folders/@item "
                       "(Caused by SSLError(SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
                       "unable to get local issuer certificate')))")


def summary_of(page):
    return re.search(r'<p class="summary">(.*?)</p>', page).group(1).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


def check_rows(page):
    """(check, css class of its state) of every row of a check, in their order."""
    return re.findall(r'<tr><td>(.*?)</td><td class="state (\w+)">', page)


def verdicts_of(page):
    """{part: (verdict, css class)} of the table by part."""
    return {part: (verdict, css) for part, css, verdict in
            re.findall(r'<tr><th scope="row">(.*?)</th><td class="verdict (\w+)">(.*?)</td>', page)}


def test_the_kind_of_failure_is_told_by_the_class_of_the_exception(monkeypatch, tmp_path):
    kind = load(monkeypatch, tmp_path).namespace["failure_kind"]

    assert kind(CERTIFICATE) == "tls"
    # a certificate that failed is said in the text where the class is another one
    assert kind(OSError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")) == "tls"
    assert kind(ConnectionError("Failed to resolve 'viya.example.com'")) == "connect"
    assert kind(ConnectTimeout("timed out")) == "timeout"
    assert kind(Timeout("read timed out")) == "timeout"
    assert kind(ValueError("something else")) == "other"


def test_a_session_whose_requests_end_on_the_certificate_gets_one_line_that_says_so(monkeypatch, tmp_path):
    """The page of the user who reported it: 8 ok, 5 missing, 20 not checked - the packages, which need no
    request, and a line per check that said the service did not answer."""
    done = run(monkeypatch, tmp_path, Unanswered(CERTIFICATE), packages=dict(ALL_PACKAGES, dspy=None, optuna=None))
    page = done.page

    assert done.rc == "0"
    summary = summary_of(page)
    assert summary.startswith("11 ok, 2 missing, 20 not checked. No request of this page reached SAS Viya: the Python of this compute "
                              "context does not trust the TLS certificate of https://viya.example.com.")
    assert "Point SAS_VIYA_CABUNDLE at a file with the CA certificates of your site" in summary
    assert "SAS_VIYA_VERIFY_SSL=false" in summary
    assert ("https://sassoftware.github.io/sas-agentic-ai-accelerator/Administration-Guide/Troubleshooting-Guide"
            "#program-certificate-verify-failed") in summary
    # one row stands for the twenty, it is the first row, and no row says that a service did not answer
    rows = check_rows(page)
    assert rows[0] == ("Requests to SAS Viya", "unchecked") and [css for _, css in rows].count("unchecked") == 1
    assert "the service did not answer" not in page
    assert "look at the state of the service" not in page
    # the log says it once, with the counts
    assert ("NOTE", "Start Here: the page holds 33 checks - 11 ok, 2 missing, 20 not checked.") in done.sas.messages


@pytest.mark.parametrize("error, said, todo", [
    (ConnectionError("Failed to resolve 'viya.example.com'"),
     "this compute context could not connect to https://viya.example.com - the name does not resolve from inside the cluster",
     "reach the address the SAS option SERVICESBASEURL names"),
    (ConnectTimeout("Connection to viya.example.com timed out"), "https://viya.example.com did not answer this compute context in time",
     "check the way from the compute server"),
    (ValueError("nothing requests raises"), "every request ended without an answer", "Hand the log of this job to an administrator"),
])
def test_a_host_that_cannot_be_reached_and_a_timeout_get_their_own_line(monkeypatch, tmp_path, error, said, todo):
    page = run(monkeypatch, tmp_path, Unanswered(error)).page

    assert "No request of this page reached SAS Viya: " + said in summary_of(page)
    assert todo in summary_of(page)
    assert "SAS_VIYA_CABUNDLE" not in page


def test_where_some_requests_are_answered_the_lines_stay(monkeypatch, tmp_path):
    """CAS cannot be reached and every other service answers: no line at the top, the lines of the tables say it."""
    done = run(monkeypatch, tmp_path, Unanswered(ConnectionError("refused"), prefixes=("/casManagement/",)))

    assert "No request of this page reached SAS Viya" not in done.page
    assert done.page.count("the CAS servers: the service did not answer") == 10


def test_a_session_without_requests_gets_no_line_about_answers(monkeypatch, tmp_path):
    done = run(monkeypatch, tmp_path, without_requests=True)

    assert "No request of this page reached SAS Viya" not in done.page
    assert "not installed, so SAS Viya could not be asked" in done.page


# ---------------------------------------------------------------------------
# The parts
# ---------------------------------------------------------------------------

def test_a_complete_site_is_ready_in_every_part(monkeypatch, tmp_path):
    page = run(monkeypatch, tmp_path).page

    assert verdicts_of(page) == {part: ("ready", "ok") for part in
                                 ("Prompt building", "Logging and the LLM Usage Report", "RAG", "Prompt optimization", "Decision models")}
    # the summary has no count of lines that are not used where there is none
    assert "not used" not in summary_of(page)


def test_a_site_without_rag_and_optimization_has_nothing_to_do_there(monkeypatch, tmp_path):
    site = Site()
    site.options.update(enableRag="false", enableOptimization="false")
    site.projects = site.projects[:1] + site.projects[2:]
    packages = {name: version for name, version in ALL_PACKAGES.items() if name not in ("psycopg2", "psycopg2-binary", "singlestoredb",
                                                                                         "pypdfium2", "markitdown", "dspy", "optuna")}
    done = run(monkeypatch, tmp_path, site, packages=packages)
    lines = done.namespace["check_installation"](get_of(site), dict(harness.DEFAULT_SETTINGS))[0]

    verdicts = verdicts_of(done.page)
    assert verdicts["RAG"] == ("not used here", "unused") and verdicts["Prompt optimization"] == ("not used here", "unused")
    assert verdicts["Prompt building"] == ("ready", "ok") and verdicts["Logging and the LLM Usage Report"] == ("ready", "ok")
    unused = {entry["what"]: entry for entry in lines if entry["state"] == NOT_USED}
    assert set(unused) == {"psycopg2-binary", "singlestoredb", "pypdfium2", "markitdown", "dspy 3.2.1 or later", "optuna 3.0 or later",
                           "Project Embedding Model Project (embedding model definitions)",
                           # an option that is set is checked with RAG off as well, and it names no project that is there
                           "embeddingProjectID"}
    assert unused["dspy 3.2.1 or later"]["todo"] == ("Nothing to do: the option enableOptimization of the Prompt Builder switches "
                                                     "Prompt optimization off on this site.")
    # nothing of the two parts is missing, and what is ok there stays ok
    assert not [entry for entry in lines if entry["part"] in ("RAG", "Prompt optimization") and entry["state"] == MISSING]
    assert {entry["what"]: entry["state"] for entry in lines if entry["part"] == "RAG"}["requests"] == OK
    assert summary_of(done.page).startswith(f"{sum(1 for e in lines if e['state'] == OK)} ok, 0 missing, 0 not checked, 8 not used.")


def test_a_backend_that_is_not_offered_needs_no_driver(monkeypatch, tmp_path):
    site = Site()
    site.options.update(enable_singlestore="false")
    lines = load(monkeypatch, tmp_path, site, packages=dict(ALL_PACKAGES, singlestoredb=None)).namespace["check_installation"](
        get_of(site), dict(harness.DEFAULT_SETTINGS))[0]

    driver = next(entry for entry in lines if entry["what"] == "singlestoredb")
    assert driver["state"] == NOT_USED and "does not offer SingleStore" in driver["todo"]
    # offered (the default), the driver of pgvector is needed
    pgvector = next(entry for entry in lines if entry["what"] == "psycopg2-binary")
    assert pgvector["state"] == OK


def test_decision_models_switched_off_need_no_project_with_models(monkeypatch, tmp_path):
    site = Site()
    site.models[site.projects[2]["id"]] = 0
    on = run(monkeypatch, harness_work(tmp_path, "on"), site).page
    site.options["enableDecision"] = "false"
    off = run(monkeypatch, harness_work(tmp_path, "off"), site).page

    assert verdicts_of(on)["Decision models"][1] == "missing" and verdicts_of(on)["Decision models"][0] == "1 to do"
    assert verdicts_of(off)["Decision models"] == ("not used here", "unused")


def test_where_the_options_cannot_be_read_every_part_counts_as_used(monkeypatch, tmp_path):
    loaded = load(monkeypatch, tmp_path, packages=dict(ALL_PACKAGES, markitdown=None))
    parts_in_use = loaded.namespace["parts_in_use"]

    assert parts_in_use(None) == {"Prompt building": True, "Logging and the LLM Usage Report": True, "RAG": None,
                                  "Prompt optimization": None, "Decision models": None}
    lines = loaded.namespace["with_parts"]([loaded.namespace["line"]("Python packages: RAG", "markitdown", MISSING, "not installed")], None)
    assert lines[0]["state"] == MISSING and lines[0]["used"] is True


def test_every_line_has_one_of_the_parts(monkeypatch, tmp_path):
    loaded = load(monkeypatch, tmp_path, packages=ALL_PACKAGES)
    lines, _ = loaded.namespace["check_installation"](get_of(loaded.site), dict(harness.DEFAULT_SETTINGS))
    parts = {entry["what"]: entry["part"] for entry in lines}

    assert set(parts.values()) == set(loaded.namespace["PARTS"])
    assert parts["release.json"] == "Prompt building"
    assert parts["Public.ACCELERATOR_RELEASES (Prompt Builder)"] == "Prompt building"
    assert parts["Public.LLM_LOGS (LLM Usage Report)"] == "Logging and the LLM Usage Report"
    assert parts["Public.MODEL_CATALOG (Link-Logs-To-Models.sas)"] == "Logging and the LLM Usage Report"
    assert parts["Project Embedding Model Project (embedding model definitions)"] == "RAG"
    assert parts["Project Decision Model Project (decision model definitions)"] == "Decision models"
    assert parts["embeddingProjectID"] == "RAG" and parts["llmProjectID"] == "Prompt building"
    assert parts["Optimize compute context (option computeContext of the Prompt Builder, for the prompt optimization job)"] == \
        "Prompt optimization"
    assert parts["SAS Studio compute context (option ragComputeContext of the Prompt Builder, for the RAG ingestion job)"] == "RAG"
    assert parts["SAS Job Execution compute context (named by this job)"] == "Prompt building"


# ---------------------------------------------------------------------------
# The report for mdb check
# ---------------------------------------------------------------------------

MARKER = re.compile(r"^Start Here report (\d+)/(\d+) ([A-Za-z0-9+/=]+)$")


def report_in(messages):
    """The report as mdb check reads it: the NOTE lines with the marker, in order, unpacked."""
    chunks = [MARKER.match(message) for kind, message in messages if kind == "NOTE"]
    chunks = [match for match in chunks if match]
    assert chunks and [int(match.group(1)) for match in chunks] == list(range(1, int(chunks[0].group(2)) + 1))
    return json.loads(zlib.decompress(base64.b64decode("".join(match.group(3) for match in chunks))))


def test_with_mdb_report_the_lines_go_into_the_log_as_well(monkeypatch, tmp_path):
    done = run(monkeypatch, tmp_path, settings={"mdbReport": "1"})
    report = report_in(done.sas.messages)
    lines, _ = done.namespace["check_installation"](get_of(done.site), dict(harness.DEFAULT_SETTINGS))

    assert report["kind"] == "sas-agentic-ai-accelerator/start-here" and report["version"] == 1
    assert report["contentRoot"] == harness.ROOT and report["host"] == harness.VIYA_HOST and report["unanswered"] == ""
    assert [entry["what"] for entry in report["lines"]] == [entry["what"] for entry in lines]
    assert set(report["lines"][0]) == {"part", "section", "what", "state", "found", "todo", "count"}
    assert [part["verdict"] for part in report["parts"]] == ["ready"] * 5
    assert report["counts"] == {"ok": len(lines), "missing": 0, "not checked": 0, "not used": 0}
    # whose session it is, as whether the two users are one - neither id
    assert report["session"] == {"otherUser": False, "ownUser": False, "context": ""}
    # the page is the same page
    assert "<h3>By part</h3>" in done.page


def test_the_lines_of_the_report_stay_below_the_width_at_which_the_log_wraps(monkeypatch, tmp_path):
    """The log of a job submitted through the API wrapped a line at 128 characters (2026-11-03)."""
    done = run(monkeypatch, tmp_path, settings={"mdbReport": "1"})
    lines = [message for kind, message in done.sas.messages if MARKER.match(message)]

    assert lines and all(len("NOTE: " + line) < 128 for line in lines)
    # what goes to the log is text SAS takes as it is: the marker, two numbers and base64
    assert all(done.namespace["sas_safe"](line) == line for line in lines)


def test_the_report_holds_no_token_and_no_user_id(monkeypatch, tmp_path):
    done = run(monkeypatch, tmp_path, Unanswered(CERTIFICATE), settings=dict(harness.SERVICE_ACCOUNT_SESSION, mdbReport="1"))
    report = report_in(done.sas.messages)
    text = json.dumps(report)

    assert report["unanswered"] == "tls" and report["lines"][0]["section"] == "SAS session of this page"
    assert report["session"] == {"otherUser": True, "ownUser": False, "context": "SAS Job Execution compute context"}
    for secret in (harness.TOKEN, "svc4410", "uid7781"):
        assert secret not in text


def test_without_mdb_report_the_log_holds_no_report(monkeypatch, tmp_path):
    done = run(monkeypatch, tmp_path)

    assert not [message for _, message in done.sas.messages if message.startswith("Start Here report")]


def harness_work(tmp_path, name):
    folder = tmp_path / name
    folder.mkdir()
    return folder
