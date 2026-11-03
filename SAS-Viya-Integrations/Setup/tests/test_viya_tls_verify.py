"""The TLS switch of the programs and custom steps that run in SAS and call SAS Viya with requests.

On a test system whose CA bundle cannot verify the certificate of its own ingress every one of those calls
failed with CERTIFICATE_VERIFY_FAILED, and the verify argument had no setting. The five programs and the ten
steps now choose it with the same function, _viya_tls_verify. SAS_VIYA_VERIFY_SSL=false (or no / 0) in the
environment of the SAS session switches verification off and says so in the SAS log, SAS_VIYA_CABUNDLE names a
bundle to verify against instead, and with neither set the answer is the one the old expression gave.

The function is written out at every site, as the programs are files of their own. These tests read it from
each site, hold the copies identical, and run the copy of this file against a stub of the SAS object.
"""
import json
import os
import re
import textwrap
import warnings
from pathlib import Path

import pytest

SETUP = Path(__file__).resolve().parents[1]
INTEGRATIONS = SETUP.parent
STEPS = INTEGRATIONS / "Custom-Steps"

# Every site, listed by name, so a site that is lost fails here and is not skipped
PROGRAMS = [
    "Logging-Monitoring/Get-All-Prompts.sas",
    "Setup/Start-Here.sas",
    "RAG-Ingestion/Ingest-Documents.sas",
    "RAG-Ingestion/Test-Retrieval.sas",
    "Prompt-Optimization/Optimize-Prompt-DSPy.sas",
]
STEP_FILES = [
    "LLM - Get All Prompts.step",
    "RAG - Chunk Documents.step",
    "RAG - Embed Chunks.step",
    "RAG - Enrich Chunks.step",
    "RAG - Extract Text.step",
    "RAG - List Documents.step",
    "RAG - Load Vector Store.step",
    "RAG - Purge Documents.step",
    "RAG - Register Setup.step",
    "RAG - Retrieve Context.step",
]

CANONICAL = '''def _viya_tls_verify():
    # TLS verification for calls from this SAS session to SAS Viya.
    #
    # SAS_VIYA_VERIFY_SSL=false (or no / 0) switches verification off, for a test system whose CA bundle cannot
    # verify the certificate of its own ingress - the name mdb uses for the same thing. SAS_VIYA_CABUNDLE names a
    # CA bundle to verify against instead. With neither set, the bundle of the session (SSLCALISTLOC, then
    # CAS_CLIENT_SSL_CA_LIST), else the certificates Python trusts by default - as before.
    if os.environ.get('SAS_VIYA_VERIFY_SSL', '').strip().lower() in ('false', 'no', '0'):
        import warnings
        warnings.filterwarnings('ignore', message='Unverified HTTPS request')
        try:
            SAS.logMessage('SAS_VIYA_VERIFY_SSL is false - the TLS certificate of SAS Viya is not verified in this step.', 'warning')
        except Exception:
            pass
        return False
    return (os.environ.get('SAS_VIYA_CABUNDLE') or os.environ.get('SSLCALISTLOC')
            or os.environ.get('CAS_CLIENT_SSL_CA_LIST') or True)
'''

# The expression of the programs before the function, in either quote style
OLD_EXPRESSION = re.compile(r"""os\.environ\.get\((['"])SSLCALISTLOC\1\) or os\.environ\.get\(\1CAS_CLIENT_SSL_CA_LIST\1\) or True""")

VARIABLES = ("SAS_VIYA_VERIFY_SSL", "SAS_VIYA_CABUNDLE", "SSLCALISTLOC", "CAS_CLIENT_SSL_CA_LIST")
UNSAFE_IN_A_MESSAGE = "'\";"


def program_text(relative):
    return (INTEGRATIONS / relative).read_text(encoding="utf-8")


def step_text(name):
    return json.loads((STEPS / name).read_text(encoding="utf-8"))["templates"]["SAS"]


SITES = [(path, lambda path=path: program_text(path)) for path in PROGRAMS] + \
        [(name, lambda name=name: step_text(name)) for name in STEP_FILES]
SITE_NAMES = [site for site, _ in SITES]


def helper_of(text):
    """The definition of _viya_tls_verify as the site writes it, from its def line to the line that ends with
    or True), taken out of its indentation."""
    lines = text.splitlines()
    starts = [number for number, line in enumerate(lines) if line.strip() == "def _viya_tls_verify():"]
    assert len(starts) == 1
    end = next(number for number in range(starts[0], len(lines)) if lines[number].rstrip().endswith("or True)"))
    return textwrap.dedent("\n".join(lines[starts[0]:end + 1])) + "\n"


class Stub:
    """The SAS object of a PROC PYTHON step, with the one method the function calls."""

    def __init__(self):
        self.messages = []

    def logMessage(self, text, message_type=None):
        self.messages.append((text, message_type))


class StubWithoutLog:
    """A SAS object a test made up, which cannot write to the log."""


def verify_with(sas=None):
    namespace = {"os": os, "SAS": Stub() if sas is None else sas}
    exec(CANONICAL, namespace)
    return namespace["_viya_tls_verify"], namespace["SAS"]


@pytest.fixture
def clean(monkeypatch):
    for name in VARIABLES:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


# ---------------------------------------------------------------------------
# The sites
# ---------------------------------------------------------------------------

def test_there_are_five_programs_and_ten_steps_and_each_exists():
    assert len(PROGRAMS) == 5 and len(STEP_FILES) == 10
    for relative in PROGRAMS:
        assert (INTEGRATIONS / relative).is_file(), relative
    for name in STEP_FILES:
        assert (STEPS / name).is_file(), name


@pytest.mark.parametrize("site, read", SITES, ids=SITE_NAMES)
def test_every_site_writes_the_function_as_this_file_does(site, read):
    assert helper_of(read()) == CANONICAL


@pytest.mark.parametrize("site, read", SITES, ids=SITE_NAMES)
def test_every_site_sets_its_verify_argument_with_the_function(site, read):
    assert re.search(r"^\s*(verify|VERIFY) = _viya_tls_verify\(\)\s*$", read(), re.M)


def test_no_program_or_step_chooses_verify_with_the_old_expression():
    found = []
    for path in sorted(INTEGRATIONS.rglob("*.sas")) + sorted(INTEGRATIONS.rglob("*.step")):
        if "tests" in path.relative_to(INTEGRATIONS).parts:
            continue
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".step":
            text = json.loads(text)["templates"]["SAS"]
        if OLD_EXPRESSION.search(text):
            found.append(str(path.relative_to(INTEGRATIONS)))
    assert found == []


def test_the_function_itself_does_not_match_the_old_expression():
    assert not OLD_EXPRESSION.search(CANONICAL)


# ---------------------------------------------------------------------------
# What the function answers
# ---------------------------------------------------------------------------

def test_with_nothing_set_python_trusts_what_it_trusts_by_default(clean):
    verify, sas = verify_with()
    assert verify() is True
    assert sas.messages == []


def test_the_bundle_of_the_session_is_used_even_if_the_file_is_not_there(clean):
    clean.setenv("SSLCALISTLOC", "/certs/trustedcerts.pem")
    verify, _ = verify_with()
    assert verify() == "/certs/trustedcerts.pem"


def test_the_bundle_of_cas_is_used_when_the_session_has_none(clean):
    clean.setenv("CAS_CLIENT_SSL_CA_LIST", "/cas/bundle.pem")
    verify, _ = verify_with()
    assert verify() == "/cas/bundle.pem"


def test_the_bundle_of_the_session_comes_before_the_one_of_cas(clean):
    clean.setenv("SSLCALISTLOC", "/certs/trustedcerts.pem")
    clean.setenv("CAS_CLIENT_SSL_CA_LIST", "/cas/bundle.pem")
    verify, _ = verify_with()
    assert verify() == "/certs/trustedcerts.pem"


def test_a_bundle_that_is_named_comes_before_the_bundles_of_the_session(clean):
    clean.setenv("SSLCALISTLOC", "/certs/trustedcerts.pem")
    clean.setenv("CAS_CLIENT_SSL_CA_LIST", "/cas/bundle.pem")
    clean.setenv("SAS_VIYA_CABUNDLE", "/my/bundle.pem")
    verify, sas = verify_with()
    assert verify() == "/my/bundle.pem"
    assert sas.messages == []


@pytest.mark.parametrize("value", ["false", "FALSE", " no ", "0"])
def test_a_false_word_switches_verification_off_and_says_so_once(clean, value):
    clean.setenv("SAS_VIYA_VERIFY_SSL", value)
    verify, sas = verify_with()
    assert verify() is False
    assert len(sas.messages) == 1
    text, level = sas.messages[0]
    assert level == "warning"
    assert not any(character in text for character in UNSAFE_IN_A_MESSAGE)


def test_the_switch_wins_over_a_bundle_that_is_named(clean):
    clean.setenv("SAS_VIYA_VERIFY_SSL", "false")
    clean.setenv("SAS_VIYA_CABUNDLE", "/my/bundle.pem")
    clean.setenv("SSLCALISTLOC", "/certs/trustedcerts.pem")
    verify, _ = verify_with()
    assert verify() is False


@pytest.mark.parametrize("value", ["true", "yes", "off", "garbage", ""])
def test_anything_but_a_false_word_leaves_verification_as_if_the_switch_were_not_set(clean, value):
    clean.setenv("SAS_VIYA_VERIFY_SSL", value)
    verify, sas = verify_with()
    assert verify() is True
    assert sas.messages == []
    clean.setenv("SSLCALISTLOC", "/certs/trustedcerts.pem")
    assert verify() == "/certs/trustedcerts.pem"
    assert sas.messages == []


def test_a_sas_object_that_cannot_write_to_the_log_does_not_stop_the_step(clean):
    clean.setenv("SAS_VIYA_VERIFY_SSL", "false")
    verify, _ = verify_with(StubWithoutLog())
    assert verify() is False


def test_with_verification_off_the_warning_of_urllib3_is_not_shown(clean):
    clean.setenv("SAS_VIYA_VERIFY_SSL", "false")
    verify, _ = verify_with()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("default")
        verify()
        warnings.warn("Unverified HTTPS request is being made", UserWarning)
    assert caught == []


# ---------------------------------------------------------------------------
# The same answers as before
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("session", [None, "/certs/trustedcerts.pem"])
@pytest.mark.parametrize("cas", [None, "/cas/bundle.pem"])
def test_with_neither_new_variable_set_the_answer_is_the_one_of_the_old_expression(clean, session, cas):
    if session:
        clean.setenv("SSLCALISTLOC", session)
    if cas:
        clean.setenv("CAS_CLIENT_SSL_CA_LIST", cas)
    old = os.environ.get('SSLCALISTLOC') or os.environ.get('CAS_CLIENT_SSL_CA_LIST') or True
    verify, _ = verify_with()
    assert verify() == old
