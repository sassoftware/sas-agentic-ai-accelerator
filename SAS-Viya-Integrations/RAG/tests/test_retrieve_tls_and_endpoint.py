# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The manifested retrieval model: TLS verification and the embedding endpoint.

Both are decided where nobody is watching - inside a decision running on a
destination - so the tests pin every rung rather than a sample of them.

* `_ssl_verify`: the environment wins in both directions, the manifested
  SSL_VERIFY decides the rest, and a copy without the constant verifies.
* the embedding endpoint: options > RAGEMBED_ENDPOINT > the manifested
  EMBED_ENDPOINT > SAS_SERVICES_URL + "/llm".
"""
import importlib.util
import itertools
import os
import pathlib
import sys
import types

import pytest

from rag_core.manifest import render_retrieval_model

TEMPLATE = pathlib.Path(__file__).resolve().parents[1] / "retrieve_context.py"
DEFAULT_BUNDLE = "/security/trustedcerts.pem"

PREFIXES = ("RAGSTORE", "RAGEMBED")
OFF_WORDS = ("false", "no", "0")
ON_WORDS = ("true", "yes", "1")
# None = the variable is not set at all
WORDS = (None,) + OFF_WORDS + ON_WORDS + ("garbage",)
# the same words the way a person types them into a deployment
SPELLINGS = ("FALSE", " No ", "True", " YES\t", "", "   ", "off", "on", "2")

_ENVIRONMENT = ("RAGSTORE_SSLVERIFY", "RAGSTORE_CABUNDLE", "RAGEMBED_SSLVERIFY",
                "RAGEMBED_CABUNDLE", "RAGEMBED_ENDPOINT", "RAGEMBED_MODEL",
                "RAGEMBED_DEPLOYMENT_TYPE", "RAGEMBED_TIMEOUT", "RAGEMBED_API_KEY",
                "RAGSTORE_CREDENTIALS_URL", "SAS_SERVICES_URL", "SAS_SERVICES_TOKEN")


def _load(source=None):
    """A fresh copy of the module, so a constant set in one test cannot leak
    into the next. `source` is a manifested text; default is the template."""
    if source is None:
        spec = importlib.util.spec_from_file_location(
            "retrieve_context_tls_under_test", TEMPLATE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    module = types.ModuleType("retrieve_context_manifested_under_test")
    exec(compile(source, "manifested.py", "exec"), module.__dict__)
    return module


def _before_the_option(prefix):
    """_ssl_verify as it shipped before SSL_VERIFY existed, kept verbatim: a
    model manifested with verification on must not behave differently."""
    bundle = os.getenv(prefix + "_CABUNDLE", "/security/trustedcerts.pem")
    verify = bundle if os.path.isfile(bundle) else True
    if os.getenv(prefix + "_SSLVERIFY", "").strip().lower() in ("false", "no", "0"):
        verify = False
    return verify


def _expected(manifested, word, verified):
    """The contract, written out: `verified` is what "on" resolves to."""
    spoken = (word or "").strip().lower()
    if spoken in OFF_WORDS:
        return False
    if spoken in ON_WORDS:
        return verified
    return verified if manifested else False


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    for name in _ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def bundle(tmp_path):
    path = tmp_path / "trustedcerts.pem"
    path.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    return str(path)


def _set_word(monkeypatch, prefix, word):
    if word is None:
        monkeypatch.delenv(prefix + "_SSLVERIFY", raising=False)
    else:
        monkeypatch.setenv(prefix + "_SSLVERIFY", word)


# ---------------------------------------------------------------------------
# the constant in the MANIFEST block
# ---------------------------------------------------------------------------
def test_the_template_ships_with_verification_on():
    assert _load().SSL_VERIFY is True


def test_the_constant_is_one_bare_line_inside_the_manifest_block():
    """The Builder rewrites the block by constant name. A second line that
    starts the same way - in the documentation, say - would be a second
    candidate for a rewrite that looks for the name."""
    lines = TEMPLATE.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines)
                 if line.startswith("# ---- MANIFEST: rewritten per RAG Setup"))
    end = next(i for i, line in enumerate(lines)
               if i > start and line.startswith("# ----------------------------------------"))
    assert lines[start + 1:end].count("SSL_VERIFY = True") == 1
    assert [i for i, line in enumerate(lines)
            if line.lstrip().startswith("SSL_VERIFY")] == [
                lines.index("SSL_VERIFY = True")]


def test_every_other_constant_is_still_in_the_block():
    """rag_core.manifest and the Builder both look the block up by these
    names; the new line must not have displaced one."""
    module = _load()
    for name in ("BACKEND", "COLLECTION", "EMBED_MODEL", "EMBED_ENDPOINT",
                 "STORE_HOST", "STORE_PORT", "STORE_DB", "STORE_SSLMODE",
                 "CREDENTIAL_DOMAIN", "DEFAULT_K", "INGESTION_RUN_ID"):
        assert hasattr(module, name), name


# ---------------------------------------------------------------------------
# _ssl_verify: SSL_VERIFY x <prefix>_SSLVERIFY x bundle, for both prefixes
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("prefix,manifested,word,present", list(itertools.product(
    PREFIXES, (True, False), WORDS, (True, False))))
def test_the_whole_matrix(monkeypatch, tmp_path, bundle, prefix, manifested,
                          word, present):
    module = _load()
    module.SSL_VERIFY = manifested
    path = bundle if present else str(tmp_path / "not-there.pem")
    monkeypatch.setenv(prefix + "_CABUNDLE", path)
    _set_word(monkeypatch, prefix, word)

    result = module._ssl_verify(prefix)

    assert result == _expected(manifested, word, path if present else True)
    # requests reads a str as a bundle path and anything else as a flag, and
    # `1 == True` in Python - so the type is part of the result
    assert isinstance(result, (bool, str))
    if manifested:
        assert result == _before_the_option(prefix)


@pytest.mark.parametrize("prefix,manifested,word,mounted", list(itertools.product(
    PREFIXES, (True, False), WORDS, (True, False))))
def test_the_matrix_with_the_bundle_sas_viya_mounts(monkeypatch, prefix,
                                                    manifested, word, mounted):
    """No <prefix>_CABUNDLE: the default path, present in a SAS Viya pod and
    absent in the Python sidecar of SAS Micro Analytic Service."""
    module = _load()
    module.SSL_VERIFY = manifested
    _set_word(monkeypatch, prefix, word)
    with monkeypatch.context() as patch:
        patch.setattr(os.path, "isfile",
                      lambda path: mounted and path == DEFAULT_BUNDLE)
        result = module._ssl_verify(prefix)
        before = _before_the_option(prefix)

    assert result == _expected(manifested, word,
                               DEFAULT_BUNDLE if mounted else True)
    if manifested:
        assert result == before


@pytest.mark.parametrize("prefix,manifested,word,present", list(itertools.product(
    PREFIXES, (True, False), SPELLINGS, (True, False))))
def test_a_word_is_read_without_its_case_and_padding(monkeypatch, tmp_path, bundle,
                                                     prefix, manifested, word,
                                                     present):
    """And "off"/"on"/"2" are not words this file knows: the manifested
    default decides, as for no value at all."""
    module = _load()
    module.SSL_VERIFY = manifested
    path = bundle if present else str(tmp_path / "not-there.pem")
    monkeypatch.setenv(prefix + "_CABUNDLE", path)
    monkeypatch.setenv(prefix + "_SSLVERIFY", word)

    result = module._ssl_verify(prefix)

    assert result == _expected(manifested, word, path if present else True)
    if manifested:
        assert result == _before_the_option(prefix)


@pytest.mark.parametrize("prefix,other", [("RAGSTORE", "RAGEMBED"),
                                          ("RAGEMBED", "RAGSTORE")])
def test_one_prefix_does_not_answer_for_the_other(monkeypatch, tmp_path, bundle,
                                                  prefix, other):
    module = _load()
    monkeypatch.setenv(prefix + "_CABUNDLE", bundle)
    monkeypatch.setenv(other + "_CABUNDLE", str(tmp_path / "not-there.pem"))
    monkeypatch.setenv(other + "_SSLVERIFY", "false")
    assert module._ssl_verify(prefix) == bundle
    assert module._ssl_verify(other) is False

    module.SSL_VERIFY = False
    monkeypatch.setenv(other + "_SSLVERIFY", "true")
    assert module._ssl_verify(prefix) is False
    assert module._ssl_verify(other) is True


@pytest.mark.parametrize("value", ["False", "false", "no", 0, None, "", [], "True", 1])
def test_only_the_literal_false_switches_verification_off(bundle, monkeypatch, value):
    """The failure direction is the safe one: whatever a renderer writes that
    is not the Python literal False keeps verification on."""
    module = _load()
    module.SSL_VERIFY = value
    for prefix in PREFIXES:
        monkeypatch.setenv(prefix + "_CABUNDLE", bundle)
        assert module._ssl_verify(prefix) == bundle


# ---------------------------------------------------------------------------
# copies that were manifested, not the template
# ---------------------------------------------------------------------------
_SETUP = {
    "BACKEND": "pgvector", "COLLECTION": "rag_demo_v1",
    "EMBED_MODEL": "all_minilm_l6_v2", "EMBED_ENDPOINT": "https://scr.example/scr",
    "STORE_HOST": "db.example", "STORE_PORT": "5432", "STORE_DB": "postgres",
    "STORE_SSLMODE": "require", "CREDENTIAL_DOMAIN": "agentic-ai-keys",
    "DEFAULT_K": 4, "INGESTION_RUN_ID": "run-1"}


def _manifested(ssl_verify=None):
    """The template the way a renderer leaves it, with the constant's line
    written by hand: `ssl_verify` is the text of its value, and None is a
    copy without the line - what a renderer older than the setting wrote.
    The copies rag_core.manifest itself writes are executed further down."""
    rendered = render_retrieval_model(TEMPLATE.read_text(encoding="utf-8"), _SETUP)
    lines = [line for line in rendered.splitlines()
             if not line.startswith("SSL_VERIFY")]
    if ssl_verify is not None:
        lines.insert(lines.index('INGESTION_RUN_ID = "run-1"') + 1,
                     "SSL_VERIFY = " + ssl_verify)
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("prefix,word,present", list(itertools.product(
    PREFIXES, WORDS, (True, False))))
def test_a_copy_without_the_constant_behaves_as_before(monkeypatch, tmp_path, bundle,
                                                       prefix, word, present):
    """A setup manifested by a renderer that does not write SSL_VERIFY - the
    Register Setup step before release 2.3.0, or a Builder older than the
    option - must neither fail on the missing name nor stop verifying."""
    module = _load(_manifested())
    assert not hasattr(module, "SSL_VERIFY")
    monkeypatch.setenv(prefix + "_CABUNDLE",
                       bundle if present else str(tmp_path / "not-there.pem"))
    _set_word(monkeypatch, prefix, word)
    assert module._ssl_verify(prefix) == _before_the_option(prefix)


@pytest.mark.parametrize("prefix,word,present", list(itertools.product(
    PREFIXES, WORDS, (True, False))))
def test_a_copy_manifested_with_verification_off(monkeypatch, tmp_path, bundle,
                                                 prefix, word, present):
    """The text the Builder writes for an unticked option, executed."""
    module = _load(_manifested("False"))
    path = bundle if present else str(tmp_path / "not-there.pem")
    monkeypatch.setenv(prefix + "_CABUNDLE", path)
    _set_word(monkeypatch, prefix, word)
    assert module._ssl_verify(prefix) == _expected(
        False, word, path if present else True)


def test_a_copy_manifested_with_verification_on_equals_the_template(monkeypatch, bundle):
    module = _load(_manifested("True"))
    for prefix, word in itertools.product(PREFIXES, WORDS):
        monkeypatch.setenv(prefix + "_CABUNDLE", bundle)
        _set_word(monkeypatch, prefix, word)
        assert module._ssl_verify(prefix) == _before_the_option(prefix)


@pytest.mark.parametrize("prefix,asked,word,present", list(itertools.product(
    PREFIXES, (True, False, None), WORDS, (True, False))))
def test_the_copy_rag_core_writes_does_what_was_asked(monkeypatch, tmp_path, bundle,
                                                      prefix, asked, word, present):
    """The text the Register Setup step stores, executed - not a line put in
    by hand. `asked` is the step's checkbox as it reaches the renderer: False
    for unticked, True for ticked, None for a caller that sends nothing."""
    settings = dict(_SETUP) if asked is None else dict(_SETUP, SSL_VERIFY=asked)
    module = _load(render_retrieval_model(TEMPLATE.read_text(encoding="utf-8"),
                                          settings))
    assert module.SSL_VERIFY is (asked is not False)
    path = bundle if present else str(tmp_path / "not-there.pem")
    monkeypatch.setenv(prefix + "_CABUNDLE", path)
    _set_word(monkeypatch, prefix, word)
    assert module._ssl_verify(prefix) == _expected(
        asked is not False, word, path if present else True)


# ---------------------------------------------------------------------------
# the two calls that use it
# ---------------------------------------------------------------------------
class _Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _requests(monkeypatch):
    """Stands in for `requests`, which both callers import where they use it;
    records every call instead of making one."""
    calls = []

    def post(url, **kwargs):
        calls.append(("POST", url, kwargs))
        return _Response({"data": {"embedding": "[0.1, 0.2]"}})

    def get(url, **kwargs):
        calls.append(("GET", url, kwargs))
        return _Response({"secrets": {}})

    monkeypatch.setitem(sys.modules, "requests",
                        types.SimpleNamespace(post=post, get=get))
    return calls


@pytest.mark.parametrize("manifested,word,expected", [
    (True, None, "bundle"), (True, "false", False),
    (False, None, False), (False, "true", "bundle"), (False, "garbage", False),
])
def test_the_embedding_call_verifies_as_ragembed_says(monkeypatch, bundle,
                                                      manifested, word, expected):
    calls = _requests(monkeypatch)
    module = _load()
    module.SSL_VERIFY = manifested
    module.EMBED_ENDPOINT = "https://scr.example/scr"
    monkeypatch.setenv("RAGEMBED_CABUNDLE", bundle)
    # the other prefix says the opposite, and must not be heard here
    monkeypatch.setenv("RAGSTORE_SSLVERIFY", "true" if expected is False else "false")
    _set_word(monkeypatch, "RAGEMBED", word)

    assert module._embed_query("a question", {}) == [0.1, 0.2]
    assert calls[0][2]["verify"] == (bundle if expected == "bundle" else expected)


@pytest.mark.parametrize("manifested,word,expected", [
    (True, None, "bundle"), (True, "no", False),
    (False, None, False), (False, "yes", "bundle"), (False, "garbage", False),
])
def test_the_credentials_call_verifies_as_ragstore_says(monkeypatch, bundle,
                                                        manifested, word, expected):
    calls = _requests(monkeypatch)
    module = _load()
    module.SSL_VERIFY = manifested
    monkeypatch.setenv("SAS_SERVICES_TOKEN", "a-token")
    monkeypatch.setenv("SAS_SERVICES_URL", "https://viya.example")
    monkeypatch.setenv("RAGSTORE_CABUNDLE", bundle)
    monkeypatch.setenv("RAGEMBED_SSLVERIFY", "true" if expected is False else "false")
    _set_word(monkeypatch, "RAGSTORE", word)

    assert module._domain_secrets() == {}
    assert calls[0][1].startswith("https://viya.example/credentials/domains/")
    assert calls[0][2]["verify"] == (bundle if expected == "bundle" else expected)


# ---------------------------------------------------------------------------
# the embedding endpoint, rung by rung
# ---------------------------------------------------------------------------
def _everything_set(monkeypatch, module):
    module.EMBED_ENDPOINT = "https://manifested.example/scr"
    monkeypatch.setenv("RAGEMBED_ENDPOINT", "https://environment.example/scr")
    monkeypatch.setenv("SAS_SERVICES_URL", "https://viya.example")
    return {"scr_endpoint": "https://options.example/scr"}


def test_the_options_input_outranks_everything(monkeypatch):
    module = _load()
    options = _everything_set(monkeypatch, module)
    assert module._embed_endpoint(options) == "https://options.example/scr"


def test_the_environment_override_outranks_the_manifested_endpoint(monkeypatch):
    module = _load()
    _everything_set(monkeypatch, module)
    assert module._embed_endpoint({}) == "https://environment.example/scr"


def test_the_manifested_endpoint_outranks_sas_services_url(monkeypatch):
    """The finding: with SAS_SERVICES_URL set - every compute session sets
    it - the model embedded the question at <SAS Viya host>/llm although the
    setup was manifested for another endpoint, the one its corpus was
    embedded at."""
    module = _load()
    _everything_set(monkeypatch, module)
    monkeypatch.delenv("RAGEMBED_ENDPOINT")
    assert module._embed_endpoint({}) == "https://manifested.example/scr"


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_a_blank_manifested_endpoint_falls_back_to_sas_services_url(monkeypatch, blank):
    module = _load()
    module.EMBED_ENDPOINT = blank
    monkeypatch.setenv("SAS_SERVICES_URL", "https://viya.example/")
    assert module._embed_endpoint({}) == "https://viya.example/llm"


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_override_is_no_override(monkeypatch, blank):
    """A variable that is set but empty, or an options key with nothing in
    it, must not turn into a request to "/model/model"."""
    module = _load()
    module.EMBED_ENDPOINT = "https://manifested.example/scr"
    monkeypatch.setenv("RAGEMBED_ENDPOINT", blank)
    assert module._embed_endpoint({"scr_endpoint": blank}) == "https://manifested.example/scr"
    assert module._embed_endpoint({"scr_endpoint": None}) == "https://manifested.example/scr"


def test_nothing_resolves_to_nothing():
    module = _load()
    module.EMBED_ENDPOINT = ""
    assert module._embed_endpoint({}) == ""


def test_a_trailing_slash_is_dropped_on_every_rung(monkeypatch):
    module = _load()
    module.EMBED_ENDPOINT = "https://manifested.example/scr/"
    assert module._embed_endpoint({}) == "https://manifested.example/scr"
    monkeypatch.setenv("RAGEMBED_ENDPOINT", "https://environment.example/scr/")
    assert module._embed_endpoint({}) == "https://environment.example/scr"
    assert module._embed_endpoint({"scr_endpoint": "https://options.example/scr/"}) \
        == "https://options.example/scr"


@pytest.mark.parametrize("options,environment,manifested,services,expected", [
    ({"scr_endpoint": "https://options.example/scr"}, "https://environment.example/scr",
     "https://manifested.example/scr", "https://viya.example",
     "https://options.example/scr/all_minilm_l6_v2/all_minilm_l6_v2"),
    ({}, "https://environment.example/scr", "https://manifested.example/scr",
     "https://viya.example",
     "https://environment.example/scr/all_minilm_l6_v2/all_minilm_l6_v2"),
    ({}, None, "https://manifested.example/scr", "https://viya.example",
     "https://manifested.example/scr/all_minilm_l6_v2/all_minilm_l6_v2"),
    ({}, None, "", "https://viya.example",
     "https://viya.example/llm/all_minilm_l6_v2/all_minilm_l6_v2"),
])
def test_the_url_the_question_is_posted_to(monkeypatch, options, environment,
                                           manifested, services, expected):
    """The same four rungs, read off the request that is actually sent."""
    calls = _requests(monkeypatch)
    module = _load()
    module.EMBED_ENDPOINT = manifested
    module.EMBED_MODEL = "all_minilm_l6_v2"
    if environment:
        monkeypatch.setenv("RAGEMBED_ENDPOINT", environment)
    monkeypatch.setenv("SAS_SERVICES_URL", services)

    module._embed_query("a question", options)

    assert calls[0][:2] == ("POST", expected)


def test_the_app_per_model_layout_uses_the_manifested_endpoint_too(monkeypatch):
    calls = _requests(monkeypatch)
    module = _load()
    module.EMBED_ENDPOINT = "https://apps.example.io"
    module.EMBED_MODEL = "all_minilm_l6_v2"
    monkeypatch.setenv("RAGEMBED_DEPLOYMENT_TYPE", "aca")
    monkeypatch.setenv("SAS_SERVICES_URL", "https://viya.example")

    module._embed_query("a question", {})

    assert calls[0][1] == "https://all-minilm-l6-v2.apps.example.io/all_minilm_l6_v2"


def test_the_documentation_names_the_order_the_code_uses():
    """The order is the kind of thing that is changed in one place only."""
    text = TEMPLATE.read_text(encoding="utf-8")
    positions = [text.index(marker) for marker in (
        "1. scr_endpoint in the per-call options input",
        "2. the environment variable RAGEMBED_ENDPOINT",
        "3. the manifested EMBED_ENDPOINT, when it is not blank",
        '4. SAS_SERVICES_URL + "/llm"')]
    assert positions == sorted(positions)
