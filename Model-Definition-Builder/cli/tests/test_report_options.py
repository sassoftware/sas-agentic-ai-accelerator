# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The report client behind options-save / options-restore, and the option
rewrite it feeds. Report content is BIRD XML on both legs: the read must ask
for it and the write must declare it - declaring +json for XML bytes was a 400
on every restore, and nothing here was tested.

The second half is the fold: the RAG Builder is a part of the Prompt Builder
and has no report of its own, but sites still hold the report it had and
options files saved from it. `fold_legacy` is what keeps an upgrade from
losing every RAG setting, so each rule it follows has a test."""
import pytest

from mdb.core.options import (
    BUILDER_REPORTS, LEGACY_DROPPED, LEGACY_RENAMES, LEGACY_REPORTS, MERGED_REPORT_LABELS,
    NON_PORTABLE, RAG_ONLY_LABELS, RAG_PROOF_LABELS, RAG_SWITCH, SHARED_LABELS, fold_legacy,
    merge_seed, option_text, options_file, read_options, write_options,
)
from mdb.viya.registry import builder_seed
from mdb.viya.reports import CONTENT_MEDIA, find_report, get_content, put_content


class _Response:
    def __init__(self, status_code: int, text: str = "", headers: dict | None = None, payload=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self._payload = payload

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    def json(self):
        return self._payload


class _Session:
    """Records every call; answers with whatever the test handed it."""

    def __init__(self, get: _Response | None = None, put: _Response | None = None):
        self._get, self._put = get, put
        self.calls: list = []

    def get(self, path, params=None, headers=None):
        self.calls.append(("GET", path, headers or {}, params))
        return self._get

    def put(self, path, data=None, headers=None):
        self.calls.append(("PUT", path, headers or {}, data))
        return self._put


SAS_ERROR = ('{"errorCode":10755,"message":"An error occurred. The content is invalid, '
             'possibly in the wrong format.","httpStatusCode":400}')

BIRD = """<?xml version="1.0" encoding="UTF-8"?>
<SASReport xmlns="http://www.sas.com/sasreportmodel/bird-4.2.4">
  <PromptDefinition label="contentRoot" hidden="true" isParameter="true" dataType="string" name="pr26">
    <DefaultValue><String behavior="fixed">/SAS Agentic AI Accelerator</String></DefaultValue>
  </PromptDefinition>
  <PromptDefinition label="SCREndpoint" hidden="true" isParameter="true" dataType="string" name="pr27">
    <DefaultValue><String behavior="fixed">https://old-host/llm</String></DefaultValue>
  </PromptDefinition>
  <PromptDefinition label="API_KEYS" hidden="true" isParameter="true" dataType="string" name="pr28">
    <DefaultValue><String behavior="fixed">key-value</String></DefaultValue>
  </PromptDefinition>
</SASReport>
"""


# -- the client: what goes over the wire ---------------------------------------

def test_content_media_is_the_xml_representation():
    assert CONTENT_MEDIA == "application/vnd.sas.report.content+xml"


def test_read_asks_for_xml_and_returns_the_etag():
    session = _Session(get=_Response(200, BIRD, {"ETag": '"ms4gykwt"'}))
    content, etag = get_content(session, "rep-1")
    assert content == BIRD and etag == '"ms4gykwt"'
    method, path, headers, _ = session.calls[0]
    assert (method, path) == ("GET", "/reports/reports/rep-1/content")
    # Pinned, not left to the server default: the same endpoint serves JSON
    # when asked, and the option rewrite would find nothing in that.
    assert headers["Accept"] == CONTENT_MEDIA


def test_write_declares_xml_and_guards_with_the_etag():
    session = _Session(put=_Response(204))
    put_content(session, "rep-1", BIRD, etag='"ms4gykwt"')
    method, path, headers, data = session.calls[0]
    assert (method, path) == ("PUT", "/reports/reports/rep-1/content")
    assert headers["Content-Type"] == CONTENT_MEDIA
    assert headers["If-Match"] == '"ms4gykwt"'
    assert data == BIRD.encode("utf-8")


def test_write_without_an_etag_sends_no_if_match():
    session = _Session(put=_Response(204))
    put_content(session, "rep-1", BIRD)
    assert "If-Match" not in session.calls[0][2]


def test_write_failure_carries_the_service_message():
    """A SAS REST error body names the problem; the status line alone does not."""
    session = _Session(put=_Response(400, SAS_ERROR))
    with pytest.raises(RuntimeError) as failure:
        put_content(session, "rep-1", BIRD)
    assert "HTTP 400" in str(failure.value)
    assert "10755" in str(failure.value) and "wrong format" in str(failure.value)


def test_read_failure_carries_the_service_message():
    session = _Session(get=_Response(404, '{"errorCode":11001,"message":"The report was not found."}'))
    with pytest.raises(RuntimeError, match="HTTP 404.*not found"):
        get_content(session, "rep-1")


def test_find_report_matches_the_exact_name_only():
    items = [{"id": "old", "name": "Prompt Builder (old)"}, {"id": "cur", "name": "Prompt Builder"}]
    session = _Session(get=_Response(200, payload={"items": items}))
    assert find_report(session, "Prompt Builder")["id"] == "cur"
    assert find_report(_Session(get=_Response(200, payload={"items": items[:1]})), "Prompt Builder") is None
    assert find_report(_Session(get=_Response(403)), "Prompt Builder") is None


# -- the rewrite: what options-restore does to the XML -------------------------

def test_options_are_read_by_label():
    assert read_options(BIRD) == {
        "contentRoot": "/SAS Agentic AI Accelerator",
        "SCREndpoint": "https://old-host/llm",
        "API_KEYS": "key-value",
    }


def test_restore_rewrites_only_the_named_values_and_reports_the_rest():
    updated, result = write_options(BIRD, {
        "SCREndpoint": "https://new-host/llm?a=1&b=2",   # changed, needs escaping
        "contentRoot": "/SAS Agentic AI Accelerator",     # already matches
        "API_KEYS": "sk-should-never-be-written",          # non-portable: ignored
        "vectorStores": "chroma",                          # not in this report version
    })
    assert result.applied == {"SCREndpoint": "https://new-host/llm?a=1&b=2"}
    assert result.unchanged == {"contentRoot": "/SAS Agentic AI Accelerator"}
    assert result.missing == ["vectorStores"]
    assert result.changed
    # The rewritten XML reads back with the new value, entities intact, and
    # the key placeholder untouched.
    assert "https://new-host/llm?a=1&amp;b=2" in updated
    assert read_options(updated)["SCREndpoint"] == "https://new-host/llm?a=1&b=2"
    assert read_options(updated)["API_KEYS"] == "key-value"
    # Everything outside the touched DefaultValue is byte-identical.
    assert updated.replace("https://new-host/llm?a=1&amp;b=2", "https://old-host/llm") == BIRD


def test_restore_with_nothing_to_change_is_a_no_op():
    updated, result = write_options(BIRD, {"contentRoot": "/SAS Agentic AI Accelerator"})
    assert updated == BIRD and not result.changed and result.missing == []


# -- switches: a JSON boolean is written the way the report stores one ----------

SWITCHES = """<SASReport>
  <PromptDefinition label="enableRag" hidden="true" isParameter="true" dataType="string" name="pr40">
    <DefaultValue><String behavior="fixed">false</String></DefaultValue>
  </PromptDefinition>
  <PromptDefinition label="enableDecision" hidden="true" isParameter="true" dataType="string" name="pr41">
    <DefaultValue><String behavior="fixed">true</String></DefaultValue>
  </PromptDefinition>
</SASReport>
"""


def test_a_json_boolean_is_written_as_the_word_a_checkbox_stores():
    """The switches are checkboxes, stored as the words `true` / `false` in a
    string parameter. `str(True)` is "True", which is not the stored word -
    and which differs from the stored value, so every restore would report a
    change that is none."""
    assert option_text(True) == "true" and option_text(False) == "false"
    assert option_text("True") == "True" and option_text(30) == "30" and option_text(None) == ""
    updated, result = write_options(SWITCHES, {"enableRag": True, "enableDecision": True})
    assert result.applied == {"enableRag": "true"}
    assert result.unchanged == {"enableDecision": "true"}
    assert read_options(updated) == {"enableRag": "true", "enableDecision": "true"}
    assert "True" not in updated
    # and back off again, from the boolean a hand-edited file would hold
    updated, result = write_options(updated, {"enableRag": False})
    assert result.applied == {"enableRag": "false"} and updated == SWITCHES


# How Visual Analytics stores the options of the Prompt Builder object, as a
# report saved on 2026-10-10 holds them (read there in the JSON form of the
# content, written here in the XML form this module works on):
# `enableOptimization` was set while it was a dropdown with the keys true /
# false, `recordHistory` is a checkbox. Both are string parameters holding
# the word.
STORED_BY_VA = """<SASReport>
  <PromptDefinition label="enableOptimization" hidden="true" isParameter="true" dataType="string" multiValued="false" name="pr183">
    <DefaultValue>
      <String behavior="fixed">true</String>
    </DefaultValue>
  </PromptDefinition>
  <PromptDefinition label="recordHistory" hidden="true" isParameter="true" dataType="string" multiValued="false" name="pr633">
    <DefaultValue>
      <String behavior="fixed">true</String>
    </DefaultValue>
  </PromptDefinition>
</SASReport>
"""

# The same two typed as booleans. No report seen so far holds this shape; it
# is covered so that such a report is not read as empty and not rewritten.
TYPED_BOOLEANS = """<SASReport>
  <PromptDefinition label="enableOptimization" hidden="true" isParameter="true" dataType="boolean" multiValued="false" name="pr183">
    <DefaultValue>
      <Boolean behavior="fixed">true</Boolean>
    </DefaultValue>
  </PromptDefinition>
  <PromptDefinition label="recordHistory" hidden="true" isParameter="true" dataType="boolean" multiValued="false" name="pr633">
    <DefaultValue>
      <Boolean behavior="fixed">false</Boolean>
    </DefaultValue>
  </PromptDefinition>
  <PromptDefinition label="contentRoot" hidden="true" isParameter="true" dataType="string" multiValued="false" name="pr627">
    <DefaultValue>
      <String behavior="fixed">/Team/RAG</String>
    </DefaultValue>
  </PromptDefinition>
</SASReport>
"""


def test_a_switch_reads_the_same_from_a_string_and_from_a_boolean_parameter():
    assert read_options(STORED_BY_VA) == {"enableOptimization": "true", "recordHistory": "true"}
    assert read_options(TYPED_BOOLEANS) == {
        "enableOptimization": "true", "recordHistory": "false", "contentRoot": "/Team/RAG"}
    # Whatever case such a report writes the word in, the options file holds one spelling.
    assert read_options(TYPED_BOOLEANS.replace(">true<", ">TRUE<"))["enableOptimization"] == "true"


def test_a_restore_keeps_the_element_a_parameter_has():
    """A string parameter stays a string, a boolean one a boolean: only the
    text between the tags changes, in the spelling its element takes."""
    updated, result = write_options(STORED_BY_VA, {"enableOptimization": False, "recordHistory": "true"})
    assert result.applied == {"enableOptimization": "false"} and result.unchanged == {"recordHistory": "true"}
    assert updated == STORED_BY_VA.replace(">true<", ">false<", 1)
    assert "<Boolean" not in updated and 'dataType="boolean"' not in updated

    # A boolean parameter takes true / false only: the JSON boolean, the word
    # in any case and the `1` a 2.2.x report stored all become that word.
    for given in (False, "false", "False", "0", "off"):
        updated, result = write_options(TYPED_BOOLEANS, {"enableOptimization": given})
        assert result.applied == {"enableOptimization": "false"}, given
        assert updated == TYPED_BOOLEANS.replace(">true<", ">false<"), given
    updated, result = write_options(TYPED_BOOLEANS, {"enableOptimization": "1", "recordHistory": "1",
                                                     "contentRoot": "1"})
    assert result.unchanged == {"enableOptimization": "true"}
    assert result.applied == {"recordHistory": "true", "contentRoot": "1"}
    assert updated.count("<Boolean") == 2 and updated.count('dataType="boolean"') == 2 and "<String" in updated
    assert read_options(updated) == {"enableOptimization": "true", "recordHistory": "true", "contentRoot": "1"}

    # A string parameter gets the value as given - `1` stays `1`.
    updated, result = write_options(STORED_BY_VA, {"recordHistory": "1"})
    assert result.applied == {"recordHistory": "1"} and read_options(updated)["recordHistory"] == "1"


def test_a_boolean_parameter_is_not_given_a_value_that_says_neither_on_nor_off():
    updated, result = write_options(TYPED_BOOLEANS, {"enableOptimization": "maybe", "recordHistory": ""})
    assert updated == TYPED_BOOLEANS and not result.changed
    assert result.rejected == {"enableOptimization": "maybe", "recordHistory": ""}
    assert result.missing == [] and result.unchanged == {}


def test_a_boolean_survives_the_options_file_and_the_seed_merge():
    """False is a value someone chose, not an option nobody set."""
    assert merge_seed({}, {"enableRag": False, "judgeModel": ""}) == {"enableRag": False}
    assert options_file("https://site", {"Prompt Builder": {"enableRag": False}})["reports"] == {
        "Prompt Builder": {"enableRag": False}}


# -- one report, and the one it replaced ----------------------------------------

def test_one_report_carries_both_seed_kinds():
    assert BUILDER_REPORTS == {"Prompt Builder": ("llm", "embedding")}
    assert LEGACY_REPORTS == {"RAG Builder"}
    assert not set(BUILDER_REPORTS) & LEGACY_REPORTS
    # every label a fold can produce is an option of the merged report
    assert set(SHARED_LABELS) | set(RAG_ONLY_LABELS) | set(LEGACY_RENAMES.values()) | {RAG_SWITCH} \
        <= set(MERGED_REPORT_LABELS)
    assert len(MERGED_REPORT_LABELS) == len(set(MERGED_REPORT_LABELS)) == 28
    assert not LEGACY_DROPPED & set(MERGED_REPORT_LABELS)


def test_the_seed_names_a_project_per_kind_it_was_given():
    seed = builder_seed("repo-1", {"llm": "llm-1", "embedding": "emb-1"}, "https://site/llm", "k8s")
    assert seed == {"modelRepositoryID": "repo-1", "llmProjectID": "llm-1", "embeddingProjectID": "emb-1",
                    "SCREndpoint": "https://site/llm", "deploymentType": "k8s"}
    # nothing the app does not read, and nowhere a key could be put
    assert not set(seed) & NON_PORTABLE
    # a kind that was not discovered gets no key - so it cannot blank a value
    # the live report holds
    partial = builder_seed("repo-1", {"llm": "llm-1"}, "https://site/llm", "k8s")
    assert "embeddingProjectID" not in partial and partial["llmProjectID"] == "llm-1"
    assert merge_seed(partial, {"embeddingProjectID": "tuned"})["embeddingProjectID"] == "tuned"


def test_tls_verification_is_an_option_of_the_merged_report_and_of_nothing_older():
    """sslVerify (2.3.0): listed where the Options pane has it, after the
    credential domain. The retired report never held it and discovery does
    not know it, so no seed carries it and no rule of the fold names it."""
    labels = list(MERGED_REPORT_LABELS)
    assert labels.index("sslVerify") == labels.index("credentialDomain") + 1
    assert "sslVerify" not in SHARED_LABELS + RAG_ONLY_LABELS
    assert "sslVerify" not in LEGACY_RENAMES and "sslVerify" not in NON_PORTABLE
    seed = builder_seed("repo-1", {"llm": "llm-1", "embedding": "emb-1"}, "https://site/llm", "k8s")
    assert "sslVerify" not in seed and "sslVerify" not in merge_seed(seed, {"viyaHost": "https://site"})
    # a site that set it keeps it through a save, off included
    assert merge_seed(seed, {"sslVerify": "false"})["sslVerify"] == "false"
    assert merge_seed(seed, {"sslVerify": False})["sslVerify"] is False


SSL_REPORT = (
    '<SASReport><PromptDefinition label="credentialDomain" hidden="true" isParameter="true" dataType="string" '
    'name="pr8"><DefaultValue><String behavior="fixed">agentic-ai-keys</String></DefaultValue></PromptDefinition>'
    '<PromptDefinition label="sslVerify" hidden="true" isParameter="true" dataType="string" name="pr9">'
    '<DefaultValue><String behavior="fixed">true</String></DefaultValue></PromptDefinition></SASReport>')


def test_a_restore_leaves_tls_verification_alone_unless_the_file_names_it():
    """A 2.2.x file has no such value: the default of the imported report
    stays. A file that names it is written, on or off."""
    updated, result = write_options(SSL_REPORT, {"credentialDomain": "team-keys"})
    assert result.applied == {"credentialDomain": "team-keys"} and result.missing == []
    assert read_options(updated)["sslVerify"] == "true"
    for given in (False, "false"):
        updated, result = write_options(SSL_REPORT, {"sslVerify": given})
        assert result.applied == {"sslVerify": "false"} and read_options(updated)["sslVerify"] == "false"
    updated, result = write_options(SSL_REPORT, {"sslVerify": True})
    assert updated == SSL_REPORT and result.unchanged == {"sslVerify": "true"}
    # a report from before the option existed: named, not inserted
    older = SSL_REPORT[:SSL_REPORT.index('<PromptDefinition label="sslVerify"')] + "</SASReport>"
    updated, result = write_options(older, {"sslVerify": "false"})
    assert updated == older and result.missing == ["sslVerify"]


# What a 2.2.x `mdb options-save` wrote on a site that used both builders: two
# sections, `computeContext` in both with different meanings, and the stale
# `enabledBackends`.
PROMPT_SECTION = {
    "viyaHost": "https://site.example",
    "modelRepositoryID": "repo-1",
    "llmProjectID": "llm-1",
    "SCREndpoint": "https://site.example/llm",
    "deploymentType": "k8s",
    "judgeModel": "gpt_5_mini",
    "enableOptimization": "true",
    "computeContext": "SAS Job Execution compute context",
    "optimizeJobProgram": "/SAS Agentic AI Accelerator/Prompt Builder/Optimize-Prompt-DSPy",
    "minOptimizeSamples": "10",
    "credentialDomain": "agentic-ai-keys",
}
RAG_SECTION = {
    "viyaHost": "https://site.example",
    "modelRepositoryID": "repo-1",
    "SCREndpoint": "https://site.example/llm",
    "deploymentType": "k8s",
    "credentialDomain": "agentic-ai-keys",
    "contentRoot": "/SAS Agentic AI Accelerator/RAG",
    "casServer": "cas-shared-default",
    "computeContext": "SAS Studio compute context",
    "storeSslmode": "disable",
    "deletedPolicy": "retire",
    "retainDays": "0",
    "recordHistory": "1",
    "embedReplicas": "1",
    "persistElements": "1",
    "persistChunks": "1",
    "enabledBackends": "pgvector",
    "enable_pgvector": "1",
    "enable_singlestore": "0",
    "embeddingProjectID": "emb-1",
}


def test_a_2_2_file_with_both_sections_folds_into_one():
    saved = {"Prompt Builder": dict(PROMPT_SECTION), "RAG Builder": dict(RAG_SECTION)}
    folded, messages = fold_legacy(saved)
    assert list(folded) == ["Prompt Builder"]
    merged = folded["Prompt Builder"]
    # every Prompt Builder value is what it was
    assert {label: merged[label] for label in PROMPT_SECTION} == PROMPT_SECTION
    # every RAG-only option arrives under its own name, in the form it had
    for label in RAG_ONLY_LABELS:
        assert merged[label] == RAG_SECTION[label], label
    assert merged["storeSslmode"] == "disable" and merged["enable_singlestore"] == "0"
    # the RAG compute context under its new name, the switch on, the dead label gone
    assert merged["ragComputeContext"] == "SAS Studio compute context"
    assert merged["enableRag"] == "true"
    assert "enabledBackends" not in merged
    assert set(merged) == set(PROMPT_SECTION) | set(RAG_ONLY_LABELS) | {"ragComputeContext", "enableRag"}
    # the input is left alone, and the fold says what it did
    assert saved == {"Prompt Builder": PROMPT_SECTION, "RAG Builder": RAG_SECTION}
    text = " ".join(messages)
    assert "computeContext -> ragComputeContext" in text and "enableRag set to true" in text
    assert "is kept" not in text          # the two reports agreed on every shared label


def test_the_fold_neither_invents_nor_drops_tls_verification():
    """Folding a 2.2.x file gives no sslVerify, so a restore leaves the
    report's default. A value the Prompt Builder section holds is kept as it
    is, and the fold has nothing to say about it."""
    folded, messages = fold_legacy({"Prompt Builder": dict(PROMPT_SECTION), "RAG Builder": dict(RAG_SECTION)})
    assert "sslVerify" not in folded["Prompt Builder"] and "sslVerify" not in " ".join(messages)
    for held in ("false", False, "true"):
        folded, messages = fold_legacy({"Prompt Builder": {**PROMPT_SECTION, "sslVerify": held},
                                        "RAG Builder": dict(RAG_SECTION)})
        assert folded["Prompt Builder"]["sslVerify"] is held
        assert "sslVerify" not in " ".join(messages)


def test_the_rename_never_touches_the_optimization_context():
    """Both reports called an option `computeContext`: the job context for
    optimization in one, the ingestion context in the other. Writing the
    second over the first would send every optimization run to SAS Studio's
    context - on a site that had configured both correctly."""
    folded, _ = fold_legacy({"Prompt Builder": dict(PROMPT_SECTION), "RAG Builder": dict(RAG_SECTION)})
    assert folded["Prompt Builder"]["computeContext"] == "SAS Job Execution compute context"
    assert folded["Prompt Builder"]["ragComputeContext"] == "SAS Studio compute context"
    # also when the Prompt Builder never had an optimization context: the
    # RAG one still does not become it
    prompt = {k: v for k, v in PROMPT_SECTION.items() if k != "computeContext"}
    folded, _ = fold_legacy({"Prompt Builder": prompt, "RAG Builder": dict(RAG_SECTION)})
    assert "computeContext" not in folded["Prompt Builder"]
    assert folded["Prompt Builder"]["ragComputeContext"] == "SAS Studio compute context"
    assert LEGACY_RENAMES == {"computeContext": "ragComputeContext"}


def test_a_discovery_only_section_does_not_switch_rag_on():
    """2.2.x wrote a RAG Builder section from discovery alone on a site with no
    such report: repository, embedding project, SCR endpoint, deployment type.
    That is not a site that uses RAG."""
    discovered = {"modelRepositoryID": "repo-1", "embeddingProjectID": "emb-1",
                  "SCREndpoint": "https://site.example/llm", "deploymentType": "k8s"}
    folded, messages = fold_legacy({"Prompt Builder": dict(PROMPT_SECTION), "RAG Builder": discovered})
    merged = folded["Prompt Builder"]
    assert "enableRag" not in merged
    assert merged["embeddingProjectID"] == "emb-1"       # still worth carrying
    assert set(merged) == set(PROMPT_SECTION) | {"embeddingProjectID"}
    assert any("enableRag not set" in m and "Options pane" in m for m in messages)
    # either proof label alone is enough
    for proof in RAG_PROOF_LABELS:
        folded, _ = fold_legacy({"Prompt Builder": {}, "RAG Builder": {**discovered, proof: "x"}})
        assert folded["Prompt Builder"]["enableRag"] == "true", proof


def test_a_file_saved_by_the_new_cli_is_left_as_it_is():
    saved = {"Prompt Builder": {**PROMPT_SECTION, "enableRag": "true", "ragComputeContext": "ctx",
                                "contentRoot": "/RAG"}}
    folded, messages = fold_legacy(saved)
    assert folded == saved and messages == []
    assert folded is not saved


def test_folding_twice_changes_nothing():
    once, _ = fold_legacy({"Prompt Builder": dict(PROMPT_SECTION), "RAG Builder": dict(RAG_SECTION)})
    twice, messages = fold_legacy(once)
    assert twice == once and messages == []


def test_conflicting_shared_labels_keep_the_prompt_builder_value_and_say_so():
    rag = {**RAG_SECTION, "SCREndpoint": "https://other.example/llm", "credentialDomain": "rag-keys"}
    folded, messages = fold_legacy({"Prompt Builder": dict(PROMPT_SECTION), "RAG Builder": rag})
    merged = folded["Prompt Builder"]
    assert merged["SCREndpoint"] == "https://site.example/llm"
    assert merged["credentialDomain"] == "agentic-ai-keys"
    endpoint = next(m for m in messages if m.startswith("SCREndpoint:"))
    assert "'https://site.example/llm' is kept" in endpoint and "'https://other.example/llm'" in endpoint
    domain = next(m for m in messages if m.startswith("credentialDomain:"))
    assert "'agentic-ai-keys' is kept" in domain and "'rag-keys'" in domain
    # the three that agree are not mentioned
    assert not [m for m in messages if m.startswith(("viyaHost:", "modelRepositoryID:", "deploymentType:"))]
    assert "add the vector store credentials to it" in domain
    assert set(SHARED_LABELS) == {"viyaHost", "modelRepositoryID", "SCREndpoint", "deploymentType",
                                  "credentialDomain"}


def test_a_kept_credential_domain_none_is_not_called_a_domain_to_add_credentials_to():
    """`none` was a documented value of the 2.2.x Prompt Builder: no credential
    lookup. It is kept - changing it would switch key lookups on for prompts -
    but nobody can add vector store credentials to it, and RAG stays disabled
    until the domain is set. The message has to say that, and name the domain
    the RAG Builder used."""
    prompt = {**PROMPT_SECTION, "credentialDomain": "None"}
    folded, messages = fold_legacy({"Prompt Builder": prompt, "RAG Builder": dict(RAG_SECTION)})
    merged = folded["Prompt Builder"]
    assert merged["credentialDomain"] == "None" and merged["enableRag"] == "true"
    domain = next(m for m in messages if m.startswith("credentialDomain:"))
    assert "'None' is kept" in domain and "RAG cannot be used with 'none'" in domain
    assert "set the credential domain to 'agentic-ai-keys' in the Options pane" in domain
    assert "add the vector store credentials" not in domain


def test_a_save_after_an_import_without_options_says_what_was_not_taken_over():
    """The upgrade done in the wrong order: the import first, without
    --options, so the merged report holds the package defaults, and the save
    afterwards. The rule stands - the merged report wins, the fold cannot tell
    a default from a value tuned since - so the text has to carry it: no
    "carried settings" that were not carried, and one line naming every RAG
    value of the retired report that is not the one kept."""
    defaults = {**PROMPT_SECTION, "enableRag": "false", "embeddingProjectID": "emb-discovered",
                "contentRoot": "/SAS Agentic AI Accelerator/RAG", "casServer": "cas-shared-default",
                "ragComputeContext": "SAS Studio compute context", "storeSslmode": "prefer",
                "deletedPolicy": "retire", "retainDays": "0", "recordHistory": "true", "embedReplicas": "1",
                "persistElements": "1", "persistChunks": "1", "enable_pgvector": "true",
                "enable_singlestore": "true"}
    tuned = {**RAG_SECTION, "contentRoot": "/Team/RAG", "storeSslmode": "disable"}
    folded, messages = fold_legacy({"Prompt Builder": defaults, "RAG Builder": tuned})
    merged = folded["Prompt Builder"]
    assert merged == defaults                       # nothing of the retired report was taken
    assert not [m for m in messages if "carried" in m and "nothing was carried" not in m]
    switch = next(m for m in messages if m.startswith("enableRag is 'false' in Prompt Builder and is kept"))
    assert "nothing was carried from 'RAG Builder'" in switch and "carried settings" not in switch
    differ = [m for m in messages if "differ" in m]
    assert differ == [
        "'RAG Builder': 4 value(s) differ and the Prompt Builder ones are kept: "
        "contentRoot '/SAS Agentic AI Accelerator/RAG' (was '/Team/RAG'), storeSslmode 'prefer' (was 'disable'), "
        "enable_singlestore 'true' (was '0'), embeddingProjectID 'emb-discovered' (was 'emb-1')."]
    # a checkbox the retired report stored as 1 and the merged one as true is
    # the same setting, and so are equal values: neither is in that line
    assert "recordHistory" not in differ[0] and "enable_pgvector" not in differ[0]
    assert "casServer" not in differ[0]


def test_a_dropped_legacy_value_is_named_under_the_name_it_would_have_been_carried_as():
    """computeContext of the retired report is ragComputeContext here."""
    prompt = {**PROMPT_SECTION, "ragComputeContext": "Tuned context"}
    _, messages = fold_legacy({"Prompt Builder": prompt, "RAG Builder": dict(RAG_SECTION)})
    differ = next(m for m in messages if "differ" in m)
    assert "1 value(s) differ" in differ
    assert "ragComputeContext 'Tuned context' (was 'SAS Studio compute context')" in differ
    # the rest was carried, so the switch is set and nothing else is said about it
    assert any("enableRag set to true: the site had the report 'RAG Builder' (contentRoot / casServer)." == m
               for m in messages)


def test_what_the_merged_report_holds_wins_over_the_retired_one():
    """A save taken after the upgrade, while the retired report still exists:
    its values are stale, and the merged report's are the tuned ones."""
    tuned = {**PROMPT_SECTION, "enableRag": "false", "storeSslmode": "require",
             "ragComputeContext": "Tuned context", "embeddingProjectID": "emb-2",
             "contentRoot": ""}
    folded, messages = fold_legacy({"Prompt Builder": tuned, "RAG Builder": dict(RAG_SECTION)})
    merged = folded["Prompt Builder"]
    assert merged["storeSslmode"] == "require"
    assert merged["ragComputeContext"] == "Tuned context"
    assert merged["embeddingProjectID"] == "emb-2"
    # an existing switch is never overwritten - an admin who turned RAG off
    # meant it - but the fold says the carried settings are waiting
    assert merged["enableRag"] == "false"
    assert any("enableRag is 'false'" in m and "is kept" in m for m in messages)
    # a label the merged report holds EMPTY counts as not set, and is filled
    assert merged["contentRoot"] == "/SAS Agentic AI Accelerator/RAG"
    # a label it lacks is filled
    assert merged["casServer"] == "cas-shared-default"
    # a JSON boolean is an existing switch too
    folded, _ = fold_legacy({"Prompt Builder": {"enableRag": False}, "RAG Builder": dict(RAG_SECTION)})
    assert folded["Prompt Builder"]["enableRag"] is False


def test_a_file_with_only_the_retired_section_still_yields_the_merged_report():
    """A RAG-only site: no Prompt Builder report was ever there to be saved."""
    folded, _ = fold_legacy({"RAG Builder": dict(RAG_SECTION)})
    assert list(folded) == ["Prompt Builder"]
    merged = folded["Prompt Builder"]
    assert merged["SCREndpoint"] == "https://site.example/llm" and merged["enableRag"] == "true"
    assert "computeContext" not in merged and "enabledBackends" not in merged
    # values that describe the old report itself, or a key, never travel
    folded, _ = fold_legacy({"RAG Builder": {"id": "RBO", "name": "RAG Builder", "API_KEYS": "k",
                                             "casServer": "cas"}})
    assert folded["Prompt Builder"] == {"casServer": "cas", "enableRag": "true"}


def test_other_reports_in_a_file_pass_through_untouched():
    other = {"something": "else"}
    folded, _ = fold_legacy({"My Own Report": other, "RAG Builder": dict(RAG_SECTION)})
    assert folded["My Own Report"] == other and "RAG Builder" not in folded
