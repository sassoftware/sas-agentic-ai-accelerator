# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The decision kind: a third scoring contract (state + typed questions ->
calibrated answers) with its own folder, fact sheet, Model Manager project
and score templates - one for the System One API (TypeSafe, OpenRouter, any
server that copies the wire format), one for Google's Decisions API (Gemini
Decision 1), translated inside the container, and one for Von baked into the
image."""
import json
import shutil
from types import SimpleNamespace

import pytest

from mdb.core.facts import COLUMNS_BY_KIND, row_values
from mdb.core.generator import SCORING_CONTRACT, effective_score_file, render_assets
from mdb.core.importer import _detect_family
from mdb.core.manifest import load_manifest
from mdb.core.paths import definitions_dir, fact_sheet_path, kind_of_folder
from mdb.core.validator import validate_folder
from mdb.providers import load_adapters
from mdb.providers.base import CatalogModel

from test_env_api_keys import _load_scorer
from mdb.viya.registry import KIND_PROJECT, PROJECT_META, build_model_attributes, project_variables


def test_decision_kind_has_folder_sheet_and_project(repo_root, core):
    assert definitions_dir(repo_root, "decision").name == "Decision-Definitions"
    assert fact_sheet_path(repo_root, "decision").name == "decision_fact_sheet.csv"
    assert kind_of_folder(repo_root / "Decision-Definitions" / "jev_1_13") == "decision"
    assert KIND_PROJECT["decision"] == "Decision Model Project"
    meta = PROJECT_META["decision"]
    assert meta["target_variable"] == "answer"
    assert meta["tags"] == ["Decision-Models", "SCR-Definitions", "Python"]
    names = [v["name"] for v in project_variables(core, "decision")]
    assert names == ["state", "questions", "options",
                     "answers", "answer", "confidence", "run_time", "prompt_length", "output_length"]


def test_openrouter_serves_jev_from_the_snapshot(core):
    """OpenRouter's /models omits its decision models; the bundled snapshot
    supplies Jev with its kind, so `mdb add openrouter typesafe/jev-1.13
    --kind decision` builds a decision definition on the systemone template."""
    openrouter = load_adapters()["openrouter"]
    catalog = {m.ref: m for m in openrouter.static_catalog(core.core_dir)}
    jev = catalog["typesafe/jev-1.13"]
    assert jev.kind == "decision" and jev.context_length == 32000
    manifest = openrouter.build_manifest(jev, "jev_test", {}, "tester")
    assert manifest.kind == "decision"
    assert manifest.runtime.template == "dec_systemone_api"
    assert manifest.provider.endpoint == "https://openrouter.ai/api/v1/systemone"
    assert manifest.tags.size_class == "Decision"
    assert manifest.options == {}  # typed questions carry their own settings
    rendered = render_assets(manifest, core)
    score = rendered[effective_score_file(manifest)].decode()
    assert "def scoreModel(state, questions, options):" in score
    assert '"Output: answers, answer, confidence, run_time, prompt_length, output_length"' in score
    assert '"questions": json.loads(questions)' in score
    assert json.loads(rendered["options.json"].decode())["API_KEY"]["default"] == "OpenRouter"
    config = json.loads(rendered["modelConfiguration.json"].decode())
    assert config["function"] == "classification" and config["targetVariable"] == "answer"
    assert config["tags"][0] == "Decision"


def test_typesafe_adapter_is_decision_only(core):
    typesafe = load_adapters()["typesafe"]
    assert typesafe.template == typesafe.decision_template == "dec_systemone_api"
    jev = {m.ref: m for m in typesafe.static_catalog(core.core_dir)}["jev-latest"]
    manifest = typesafe.build_manifest(jev, "jev_latest", {}, "tester")
    assert manifest.provider.endpoint == "https://api.typesafe.ai/v1/systemone"
    assert manifest.provider.auth.key_name == "TypeSafe"


def test_von_bakes_its_checkpoint(core):
    von = load_adapters()["von"]
    cm = CatalogModel(ref="von", display_name="von", source="manual entry")
    manifest = von.build_manifest(cm, "von_test", {}, "tester")
    assert manifest.kind == "decision" and manifest.runtime.template == "dec_von"
    assert manifest.provider.auth.mode == "none"
    rendered = render_assets(manifest, core)
    steps = json.loads(rendered["requirements.json"].decode())
    commands = " ".join(step["command"] for step in steps)
    assert "--no-deps --ignore-requires-python 'von-sdk>=1.2.2'" in commands
    assert "'transformers>=5.0.0'" in commands  # the dependencies resolve for the image's Python
    assert "hf download --quiet wfzyx/von --local-dir /pybox/model/von_test" in commands
    score = rendered[effective_score_file(manifest)].decode()
    assert "OptionMarkerBackend(checkpoint_dir=checkpoint" in score
    assert "checkpoint = './von_test'" in score
    assert 'os.environ.setdefault("HF_HUB_OFFLINE", "1")' in score
    assert "def scoreModel(state, questions, options):" in score


def test_shipped_decision_definitions_are_consistent(repo_root, core):
    sheet = fact_sheet_path(repo_root, "decision")
    for model_id in ("jev_1_13", "von_1_2", "clef_flash", "liquid_d1", "pplx_decider_27b", "gemini_decision_1"):
        folder = repo_root / "Decision-Definitions" / model_id
        issues = validate_folder(folder, core, sheet)
        assert not [i for i in issues if i.severity == "error"], [i.format() for i in issues]


def test_v012_flags_a_decision_template_on_a_chat_kind(repo_root, core, tmp_path):
    source = repo_root / "Decision-Definitions" / "jev_1_13"
    folder = tmp_path / "jev_1_13"
    shutil.copytree(source, folder)
    sheet = fact_sheet_path(repo_root, "decision")
    assert not [i for i in validate_folder(folder, core, sheet) if i.rule == "V012"]
    manifest = load_manifest(folder)
    manifest.kind = "llm"
    manifest.save(folder)
    v012 = [i for i in validate_folder(folder, core, sheet) if i.rule == "V012"]
    assert len(v012) == 1 and "a decision template" in v012[0].message


def test_decision_fact_row_and_contract(repo_root):
    manifest = load_manifest(repo_root / "Decision-Definitions" / "jev_1_13")
    row = row_values(manifest)
    assert set(row) == set(COLUMNS_BY_KIND["decision"])
    assert row["context_length"] == "32000"
    # What the model accepts, with the defaults a manifest that says nothing gets.
    assert row["max_options"] == "255" and row["question_types"] == "choice|noul|score"
    assert "`state`, `questions`, `options`" in SCORING_CONTRACT["decision"]


def test_catalog_carries_what_a_decision_model_accepts(core):
    """Tev1 takes choice questions with at most 24 options, Solar Decide 26:
    the snapshot says so, the manifest keeps it, the fact row and the Model
    Manager properties carry it for the Prompt Builder's fit check."""
    openrouter = load_adapters()["openrouter"]
    catalog = {m.ref: m for m in openrouter.static_catalog(core.core_dir)}
    assert {"cloudflare/clef-flash", "liquid/d1", "perplexity/pplx-decider-v1-27b",
            "upstage/solar-decide"} <= set(catalog)
    assert catalog["cloudflare/clef-flash"].max_options is None
    tev1 = catalog["togethercomputer/tev1-4b-experimental"]
    assert tev1.max_options == 24 and tev1.question_types == ["choice"]
    assert catalog["upstage/solar-decide"].max_options == 26
    manifest = openrouter.build_manifest(tev1, "tev1_test", {}, "tester")
    assert manifest.metadata.max_options == 24 and manifest.metadata.question_types == ["choice"]
    row = row_values(manifest)
    assert row["max_options"] == "24" and row["question_types"] == "choice"


def test_decision_attributes_carry_the_capability_properties(repo_root):
    folder = repo_root / "Decision-Definitions" / "jev_1_13"
    attrs = build_model_attributes(load_manifest(folder), folder, None, "https://viya-host/llm")
    properties = {p["name"]: p["value"] for p in attrs["properties"]}
    assert properties == {"contextLength": "32000", "maxOptions": "255", "questionTypes": "choice,noul,score"}
    assert all(p["type"] == "string" for p in attrs["properties"])
    llm_folder = repo_root / "LLM-Definitions" / "gpt_41_mini"
    assert "properties" not in build_model_attributes(load_manifest(llm_folder), llm_folder, None, "https://viya-host/llm")


def test_model_card_shows_an_evaluation_only_when_measured(core):
    openrouter = load_adapters()["openrouter"]
    jev = {m.ref: m for m in openrouter.static_catalog(core.core_dir)}["typesafe/jev-1.13"]
    manifest = openrouter.build_manifest(jev, "jev_card", {}, "tester")
    card = render_assets(manifest, core)["Model-Card.md"].decode()
    pricing = "## Pricing" + chr(10) * 2 + "Input: $0.042 per 1M tokens - Output: $0 per 1M tokens" + chr(10) * 2
    assert "## Evaluation" not in card
    assert pricing + "## Intended Use" in card
    manifest.metadata.evaluation = "Accuracy 0.95 on 41 cases."
    card = render_assets(manifest, core)["Model-Card.md"].decode()
    assert pricing + "## Evaluation" + chr(10) * 2 + "Accuracy 0.95 on 41 cases." + chr(10) * 2 + "## Intended Use" in card


def test_shipped_decision_cards_carry_their_measurements(repo_root):
    for model_id in ("jev_1_13", "von_1_2", "clef_flash", "liquid_d1", "pplx_decider_27b"):
        card = (repo_root / "Decision-Definitions" / model_id / "Model-Card.md").read_text(encoding="utf-8")
        assert "## Evaluation" in card and "a shortlist, not a ranking" in card


def test_sas_fact_sheet_loader_knows_the_decision_sheet(repo_root):
    loader = (repo_root / "SAS-Viya-Integrations" / "Logging-Monitoring" / "Load-Fact-Sheets.sas").read_text(encoding="utf-8")
    assert "decision_fact_sheet.csv" in loader
    # the sheet is read and counted beside its table, and the counted upload takes the place of the table
    for statement in ("%_lfs_read(decision_fact_sheet.csv, _lfs_decision_fact_sheet, DECISION_FACT_SHEET);\n"
                      "%_lfs_swap(DECISION_FACT_SHEET);\n",):
        assert statement in loader.replace("\r\n", "\n")


@pytest.mark.parametrize("score, expected", [
    ("def scoreModel(state, questions, options):\n from von.backends import x", ("decision", "dec_von", "von")),
    ("modelEndpoint = 'https://openrouter.ai/api/v1/systemone'\ndef scoreModel(state, questions, options):",
     ("decision", "dec_systemone_api", "openrouter")),
    ("modelEndpoint = 'https://api.typesafe.ai/v1/systemone'\ndef scoreModel(state, questions, options):",
     ("decision", "dec_systemone_api", "typesafe")),
    ("modelEndpoint = 'https://generativelanguage.googleapis.com/v1beta/decisions'\n"
     "def scoreModel(state, questions, options):", ("decision", "dec_gemini", "google")),
])
def test_importer_recognises_decision_scorers(score, expected):
    assert _detect_family(score) == expected


# -- Gemini Decision 1: Google's Decisions API -----------------------------------

#: What Google answered on 2026-11-12, trimmed: the answers in another order than the questions were asked, and
#: Google's own names for the usage.
GOOGLE_ANSWER = {
    "answers": {
        "tone": {"type": "score", "score": 1.12, "confidence": 0.76, "probabilities": {"0": 0.06, "1": 0.76, "2": 0.18},
                 "legend": {"0": "Calm", "1": "Frustrated", "2": "Angry"}},
        "escalate": {"type": "noul", "noul": 0.71, "confidence": 0.71},
        "team": {"type": "choice", "choice": "billing", "confidence": 0.99,
                 "probabilities": {"billing": 0.99, "technical": 0.01}},
    },
    "usage": {"total_tokens": 256, "total_input_tokens": 256, "total_output_tokens": 0,
              "input_tokens_by_modality": [{"modality": "text", "tokens": 256}]},
}
QUESTIONS = {
    "team": {"type": "choice", "instructions": "Which team?", "criteria": {"billing": "Payments", "technical": "Bugs"}},
    "escalate": {"type": "noul", "instructions": "The customer threatens to leave"},
    "tone": {"type": "score", "instructions": "How frustrated?", "criteria": ["Calm", "Frustrated", "Angry"]},
}


def _gemini_manifest(core):
    google = load_adapters()["google"]
    gemini = {m.ref: m for m in google.static_catalog(core.core_dir)}["gemini-decision-1"]
    return google.build_manifest(gemini, "gemini_test", {}, "tester")


def test_google_serves_gemini_decision_directly(core):
    """`mdb add google gemini-decision-1 --kind decision`: Google's own endpoint and key, the dec_gemini template."""
    manifest = _gemini_manifest(core)
    assert manifest.kind == "decision" and manifest.runtime.template == "dec_gemini"
    assert manifest.provider.endpoint == "https://generativelanguage.googleapis.com/v1beta/decisions"
    assert manifest.provider.auth.key_name == "Google"
    assert manifest.metadata.context_length == 262144
    assert manifest.metadata.pricing.input_token_price == pytest.approx(1e-07)
    assert manifest.options == {}
    rendered = render_assets(manifest, core)
    assert json.loads(rendered["options.json"].decode())["API_KEY"]["default"] == "Google"
    readme = rendered["README.md"].decode()
    assert "translates it for Google's Decisions API" in readme and "generativelanguage.googleapis.com" in readme
    # OpenRouter hosts it too, on its System One endpoint - the template that needs no translation.
    openrouter = load_adapters()["openrouter"]
    hosted = {m.ref: m for m in openrouter.static_catalog(core.core_dir)}["google/gemini-decision-1"]
    assert hosted.kind == "decision"
    assert openrouter.build_manifest(hosted, "gemini_or", {}, "tester").runtime.template == "dec_systemone_api"


def test_google_live_catalog_lists_decision_models(monkeypatch):
    """models.list names a decision model by its method, createDecision; its output token limit (1) means nothing."""
    from mdb.providers import google as google_module
    listing = {"models": [
        {"name": "models/gemini-x", "inputTokenLimit": 1000, "outputTokenLimit": 100,
         "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-decision-1", "displayName": "Gemini Decision 1", "inputTokenLimit": 262144,
         "outputTokenLimit": 1, "supportedGenerationMethods": ["createDecision"]},
        {"name": "models/aqa", "supportedGenerationMethods": ["generateAnswer"]},
    ]}
    monkeypatch.setattr(google_module, "get_json", lambda session, url, headers=None: listing)
    models = {m.ref: m for m in load_adapters()["google"].live_catalog(None, "key")}
    assert set(models) == {"gemini-x", "gemini-decision-1"}
    decision = models["gemini-decision-1"]
    assert decision.kind == "decision" and decision.context_length == 262144 and decision.max_output_tokens is None


def test_gemini_scorer_translates_the_contract(tmp_path, monkeypatch, core):
    """The container keeps the System One contract for its callers: the state goes to Google as the input text (a
    JSON state too - Google takes an object there for a content block and refuses it), the key in x-goog-api-key;
    the headline answers the FIRST question asked, though Google returns the answers in another order."""
    module = _load_scorer(tmp_path, _gemini_manifest(core), core)
    sent = []

    def post(url, headers=None, json=None, timeout=None):
        sent.append((url, headers, json))
        return SimpleNamespace(status_code=200, json=lambda: GOOGLE_ANSWER, text="")

    monkeypatch.setattr(module.requests, "post", post)
    state = json.dumps({"customer": "Acme", "message": "Charged twice"})
    answers, answer, confidence, _, prompt_length, output_length = module.scoreModel(
        [state], [json.dumps(QUESTIONS)], [json.dumps({"API_KEY": "the-key"})])
    url, headers, body = sent[-1]
    assert url == "https://generativelanguage.googleapis.com/v1beta/decisions"
    assert headers["x-goog-api-key"] == "the-key" and "Authorization" not in headers
    assert body == {"model": "gemini-decision-1", "input": state, "questions": QUESTIONS}
    assert (answer, confidence) == ("billing", 0.99)
    assert json.loads(answers) == GOOGLE_ANSWER["answers"]
    assert (prompt_length, output_length) == (256, 0)
    # The MAS REST API passes plain strings.
    assert module.scoreModel(state, json.dumps(QUESTIONS), "{API_KEY:the-key}")[1] == "billing"
    # A noul first: yes/no with the probability of the side it chose.
    first_noul = {"escalate": QUESTIONS["escalate"], "team": QUESTIONS["team"]}
    assert module.scoreModel(["x"], [json.dumps(first_noul)], [json.dumps({"API_KEY": "k"})])[1:3] == ("yes", 0.71)


def test_system_one_scorers_head_with_the_first_question(tmp_path, monkeypatch, core):
    """The same order fix for the System One template (OpenRouter serves Gemini Decision 1 there, in Google's
    order); Von's scorer takes the same helper."""
    openrouter = load_adapters()["openrouter"]
    hosted = {m.ref: m for m in openrouter.static_catalog(core.core_dir)}["google/gemini-decision-1"]
    module = _load_scorer(tmp_path, openrouter.build_manifest(hosted, "gemini_or", {}, "tester"), core)
    answer = {**GOOGLE_ANSWER, "usage": {"input_tokens": 256, "output_tokens": 0}}
    monkeypatch.setattr(module.requests, "post",
                        lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: answer, text=""))
    out = module.scoreModel(["state"], [json.dumps(QUESTIONS)], [json.dumps({"API_KEY": "k"})])
    assert out[1:3] == ("billing", 0.99) and out[4] == 256
    von = load_adapters()["von"]
    manifest = von.build_manifest(CatalogModel(ref="von", display_name="von", source="manual entry"), "von_o", {}, "t")
    score = render_assets(manifest, core)[effective_score_file(manifest)].decode()
    assert "answer, confidence = _headline(_first_answer(answersObject, questionsObject))" in score


def test_google_smoke_test_asks_a_decision():
    google = load_adapters()["google"]
    manifest = SimpleNamespace(kind="decision", provider=SimpleNamespace(
        model_version="gemini-decision-1", endpoint="https://generativelanguage.googleapis.com/v1beta/decisions"))
    sent = []

    class Session:
        def post(self, url, headers=None, json=None, timeout=None):
            sent.append((url, headers, json))
            return SimpleNamespace(status_code=200, json=lambda: {
                "answers": {"team": {"type": "choice", "choice": "billing", "confidence": 0.99}},
                "usage": {"total_input_tokens": 150}})

    result = google.smoke_test(manifest, "the-key", Session())
    assert result.ok and "150 input tokens" in result.detail and "team='billing'" in result.detail
    url, headers, body = sent[0]
    assert url.endswith("/v1beta/decisions") and headers["x-goog-api-key"] == "the-key"
    assert set(body) == {"model", "input", "questions"}
