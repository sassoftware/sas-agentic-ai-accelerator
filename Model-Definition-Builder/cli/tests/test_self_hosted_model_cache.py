# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""A self-hosted scorer loads its weights once per worker process - no network.

The Python runtime of SAS Container Runtime runs the score file again for
every request (importlib exec_module in app_scoring.new_score). On a hack
environment of 2026-10-20 von_1_2 therefore read its 3 GB checkpoint from an
Azure Files volume on every call: 20-24 s per request, of which scoreModel
itself took 0.42 s. The self-hosted templates (dec_von, hf_transformers,
emb_sentence_transformers, hf_onnx) keep the loaded model in a module of
sys.modules, which outlives the runs of the file.

Each committed definition of those templates, and an hf_onnx definition as
`mdb add hf-selfhosted <repo> --runtime onnx` builds one (none is committed), is
rendered with its model library stubbed and run three times the way SCR
does, with a call of scoreModel after every run: the loader is called once,
and scoreModel answers every time - the first cut of this change moved the
`modelVersion` the Von scorer reads into the loader and failed every request
with a NameError (HTTP 422) on the cluster, which a test that only counted
loads did not see. The cache also holds when calls arrive together before
the first load, and a load that fails is not kept.
"""
import importlib.util
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from mdb.core.generator import effective_score_file, render_assets
from mdb.core.manifest import load_manifest
from mdb.providers.base import CatalogModel
from mdb.providers.hf_selfhosted import HuggingFaceAdapter

CACHE_MODULE = "_sas_accelerator_model_cache"
REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES = REPO_ROOT / "Model-Definition-Builder" / "definition-core" / "templates" / "score"
SELF_HOSTED = ("dec_von", "hf_transformers", "emb_sentence_transformers", "hf_onnx")


def _committed_definitions():
    """model_id -> folder of every committed definition whose generated score file uses a self-hosted template."""
    found = {}
    for kind_dir in ("LLM-Definitions", "Embedding-Definitions", "Decision-Definitions"):
        for yaml_file in sorted((REPO_ROOT / kind_dir).glob("*/definition.yaml")):
            manifest = load_manifest(yaml_file.parent)
            if manifest.runtime.template in SELF_HOSTED and \
                    effective_score_file(manifest) not in manifest.generation.overrides:
                found[manifest.model_id] = yaml_file.parent
    return found


COMMITTED = _committed_definitions()


def _onnx_manifest(weights_source: str = "baked"):
    """An hf_onnx definition as `mdb add hf-selfhosted` with runtime onnx builds it."""
    manifest = HuggingFaceAdapter().build_manifest(
        CatalogModel(ref="microsoft/Phi-3-mini-4k-instruct-onnx", display_name="Phi 3 Mini ONNX"),
        "phi_3_mini_onnx", {"runtime": "onnx", "params_billions": "3.8"}, "test",
    )
    manifest.runtime.weights_source = weights_source
    if weights_source == "mounted":
        manifest.provider.params["hf"]["onnx_dir"] = "cpu_and_mobile/cpu-int4-rtn-block-32-acc-level-4"
    return manifest


@pytest.fixture(autouse=True)
def fresh_worker(monkeypatch):
    """Each test is a new worker process: no cache, stub libraries only for the test."""
    monkeypatch.delitem(sys.modules, CACHE_MODULE, raising=False)
    yield
    sys.modules.pop(CACHE_MODULE, None)


def _stub(monkeypatch, dotted: str, **members):
    parts = dotted.split(".")
    for i in range(1, len(parts) + 1):
        name = ".".join(parts[:i])
        if name not in sys.modules or not getattr(sys.modules[name], "_mdb_stub", False):
            module = types.ModuleType(name)
            module._mdb_stub = True
            monkeypatch.setitem(sys.modules, name, module)
    for key, value in members.items():
        setattr(sys.modules[dotted], key, value)


def _write_score_file(tmp_path, manifest, core) -> Path:
    name = effective_score_file(manifest)
    path = tmp_path / name
    path.write_bytes(render_assets(manifest, core)[name])
    return path


def _exec_like_scr(path: Path):
    """One request of SCR: app_scoring.new_score runs the score file with exec_module."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _serve_like_scr(path: Path, call, times: int = 3):
    """`times` requests: the score file runs, then scoreModel answers, once per request."""
    modules, answers = [], []
    for _ in range(times):
        module = _exec_like_scr(path)
        modules.append(module)
        answers.append(call(module))
    return modules, answers


# -- Stand-ins for the model libraries, one per template ----------------------

def _von(monkeypatch, loads):
    evaluated = []

    class OptionMarkerBackend:
        def __init__(self, checkpoint_dir, device):
            loads.append((checkpoint_dir, device))

        def evaluate(self, state, questions, model):
            evaluated.append((state, model))
            return {"answers": {"team": {"type": "choice", "choice": "billing", "confidence": 0.9}},
                    "usage": {"input_tokens": 12, "output_tokens": 0}}

    _stub(monkeypatch, "von.backends.option_marker_backend", OptionMarkerBackend=OptionMarkerBackend)
    questions = '{"team": {"type": "choice", "options": ["billing", "technical"]}}'

    def call(module):
        return module.scoreModel("charged twice", questions, "{}")

    def check(modules, answers):
        version = modules[0].modelVersion
        assert loads == [(modules[0].checkpoint, "cpu")]
        # the warm-up runs with the load, not per request; every call names the model version
        assert evaluated == [("warm-up", version)] + [("charged twice", version)] * 3
        assert all(a[1:3] == ("billing", 0.9) for a in answers)
        assert len({id(m.backend) for m in modules}) == 1
    return call, check


def _hf_transformers(monkeypatch, loads):
    class _Inputs(dict):
        "What a tokenizer call returns: a mapping for generate(**...), indexable by row."
        def __init__(self):
            super().__init__(input_ids=[[1, 2, 3]])
            self.input_ids = self["input_ids"]

        def to(self, device):
            return self

        def __getitem__(self, key):
            return self.input_ids[key] if isinstance(key, int) else dict.__getitem__(self, key)

    class _Tokenizer:
        def apply_chat_template(self, messages, tokenize, add_generation_prompt):
            return "prompt"

        def __call__(self, texts, return_tensors):
            return _Inputs()

        def batch_decode(self, ids, skip_special_tokens):
            return ["OK"]

    class _Model:
        device = "cpu"

        def generate(self, **kwargs):
            return [[1, 2, 3, 4, 5]]

    class AutoTokenizer:
        @classmethod
        def from_pretrained(cls, checkpoint, **kwargs):
            loads.append(("AutoTokenizer", checkpoint))
            return _Tokenizer()

    class AutoModelForCausalLM:
        @classmethod
        def from_pretrained(cls, checkpoint, **kwargs):
            loads.append(("AutoModelForCausalLM", checkpoint))
            return _Model()

    _stub(monkeypatch, "transformers", AutoTokenizer=AutoTokenizer, AutoModelForCausalLM=AutoModelForCausalLM)

    def call(module):
        return module.scoreModel("Reply with OK.", "You are terse.", "{}")

    def check(modules, answers):
        checkpoint = modules[0].checkpoint
        assert loads == [("AutoTokenizer", checkpoint), ("AutoModelForCausalLM", checkpoint)]
        assert [(a[0], a[2], a[3]) for a in answers] == [("OK", 3, 2)] * 3
        assert len({(id(m.tokenizer), id(m.model)) for m in modules}) == 1
    return call, check


def _sentence_transformers(monkeypatch, loads):
    class _Vector(list):
        def tolist(self):
            return list(self)

    class _Ids:
        def size(self, dim):
            return 7

    class SentenceTransformer:
        tokenizer = type("Tok", (), {"num_special_tokens_to_add": lambda self: 2})()

        def __init__(self, checkpoint):
            loads.append(checkpoint)
            self.checkpoint = checkpoint

        def encode_query(self, text):
            return _Vector([0.1, 0.2])

        encode_document = encode_query

        def preprocess(self, texts):
            return {"input_ids": _Ids()}

    _stub(monkeypatch, "sentence_transformers", SentenceTransformer=SentenceTransformer)

    def call(module):
        return module.scoreModel("charged twice", "billing", "{}")

    def check(modules, answers):
        assert loads == [modules[0].checkpoint]
        assert [(a[0], a[2]) for a in answers] == [("[0.1, 0.2]", 5)] * 3
        assert len({id(m.model) for m in modules}) == 1
    return call, check


def _onnxruntime_genai(monkeypatch, loads):
    "A stand-in for onnxruntime_genai: the calls hf_onnx makes, and a generator that answers two tokens."
    class Model:
        def __init__(self, folder):
            loads.append(("Model", folder))

    class Tokenizer:
        def __init__(self, model):
            loads.append(("Tokenizer", type(model).__name__))

        def encode(self, prompt):
            return [1, 2, 3, 4]

        def decode(self, tokens):
            return "OK" if list(tokens) == [7, 8] else f"unexpected {tokens}"

    class GeneratorParams:
        def __init__(self, model):
            self.options = None

        def set_search_options(self, **options):
            self.options = options

    class Generator:
        def __init__(self, model, params):
            assert params.options, "the score code set no search options"
            self.pending = [7, 8]
            self.last = None

        def append_tokens(self, tokens):
            assert list(tokens) == [1, 2, 3, 4]

        def is_done(self):
            return not self.pending

        def generate_next_token(self):
            self.last = self.pending.pop(0)

        def get_next_tokens(self):
            return [self.last]

    _stub(monkeypatch, "onnxruntime_genai", Model=Model, Tokenizer=Tokenizer,
          GeneratorParams=GeneratorParams, Generator=Generator)

    def call(module):
        return module.scoreModel("Reply with OK.", "You are terse.", "{}")

    def check(modules, answers):
        assert loads == [("Model", modules[0].onnx_dir), ("Tokenizer", "Model")]
        assert [(a[0], a[2], a[3]) for a in answers] == [("OK", 4, 2)] * 3
        assert len({(id(m.model), id(m.tokenizer)) for m in modules}) == 1
    return call, check


HARNESS = {
    "dec_von": _von,
    "hf_transformers": _hf_transformers,
    "emb_sentence_transformers": _sentence_transformers,
    "hf_onnx": _onnxruntime_genai,
}


def test_every_template_that_loads_weights_is_covered():
    """A score template that includes the cache is one of SELF_HOSTED, and each of those includes it and has a stand-in."""
    including = {path.name.split(".")[0] for path in TEMPLATES.glob("*.py.j2")
                 if '{% include "partials/_model_cache.py.j2" %}' in path.read_text(encoding="utf-8")}
    assert including == set(SELF_HOSTED) == set(HARNESS)
    # the committed definitions cover all but hf_onnx, which is built in the test
    assert {load_manifest(folder).runtime.template for folder in COMMITTED.values()} == set(SELF_HOSTED) - {"hf_onnx"}
    assert len(COMMITTED) == 14


@pytest.mark.parametrize("model_id", sorted(COMMITTED))
def test_committed_definition_loads_once_and_answers_every_call(model_id, monkeypatch, tmp_path, core):
    manifest = load_manifest(COMMITTED[model_id])
    loads = []
    call, check = HARNESS[manifest.runtime.template](monkeypatch, loads)
    modules, answers = _serve_like_scr(_write_score_file(tmp_path, manifest, core), call)
    check(modules, answers)


@pytest.mark.parametrize("weights_source", ["baked", "mounted"])
def test_onnx_definition_loads_once_and_answers_every_call(weights_source, monkeypatch, tmp_path, core):
    manifest = _onnx_manifest(weights_source)
    loads = []
    call, check = _onnxruntime_genai(monkeypatch, loads)
    modules, answers = _serve_like_scr(_write_score_file(tmp_path, manifest, core), call)
    check(modules, answers)
    expected = "." if weights_source == "baked" else \
        "/pybox/model/mount/phi_3_mini_onnx/cpu_and_mobile/cpu-int4-rtn-block-32-acc-level-4"
    assert modules[0].onnx_dir == expected


def test_two_models_in_one_worker_keep_their_own_weights(monkeypatch, tmp_path, core):
    loads = []
    call, _ = _sentence_transformers(monkeypatch, loads)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a_path = _write_score_file(tmp_path / "a", load_manifest(COMMITTED["all_minilm_l6_v2"]), core)
    b_path = _write_score_file(tmp_path / "b", load_manifest(COMMITTED["bge_small_en_v15"]), core)
    a, a_answers = _serve_like_scr(a_path, call, times=2)
    b, b_answers = _serve_like_scr(b_path, call, times=2)
    # interleaved: the first model again after the second was loaded
    a2, a2_answers = _serve_like_scr(a_path, call, times=1)

    assert len(loads) == 2 and loads[0] != loads[1]
    assert a2[0].model.checkpoint == loads[0] and b[-1].model.checkpoint == loads[1]
    assert a[0].model is a2[0].model
    assert all(answer[0] == "[0.1, 0.2]" for answer in a_answers + b_answers + a2_answers)


def test_calls_that_arrive_together_load_the_weights_once(monkeypatch, tmp_path, core):
    """Four requests start before the first load has finished: one load, four answers."""
    loads = []
    call, _ = _sentence_transformers(monkeypatch, loads)
    loading = sys.modules["sentence_transformers"].SentenceTransformer.__init__

    def slow_init(self, checkpoint):
        time.sleep(0.3)  # a checkpoint read from a network volume
        loading(self, checkpoint)
    monkeypatch.setattr(sys.modules["sentence_transformers"].SentenceTransformer, "__init__", slow_init)
    path = _write_score_file(tmp_path, load_manifest(COMMITTED["all_minilm_l6_v2"]), core)
    start = threading.Barrier(4)
    results, errors = [], []

    def request():
        try:
            start.wait()
            module = _exec_like_scr(path)
            results.append((module, call(module)))
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=request) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert errors == []
    assert len(loads) == 1
    assert len(results) == 4 and len({id(module.model) for module, _ in results}) == 1
    assert all(answer[0] == "[0.1, 0.2]" for _, answer in results)


def test_a_load_that_fails_is_not_kept(monkeypatch, tmp_path, core):
    """The volume is not there at the first request: that request fails, the next one loads."""
    loads, attempts = [], []
    call, _ = _sentence_transformers(monkeypatch, loads)
    loading = sys.modules["sentence_transformers"].SentenceTransformer.__init__

    def failing_once(self, checkpoint):
        attempts.append(checkpoint)
        if len(attempts) == 1:
            raise OSError(f"{checkpoint} is not there")
        loading(self, checkpoint)
    monkeypatch.setattr(sys.modules["sentence_transformers"].SentenceTransformer, "__init__", failing_once)
    path = _write_score_file(tmp_path, load_manifest(COMMITTED["all_minilm_l6_v2"]), core)

    with pytest.raises(OSError, match="is not there"):
        _exec_like_scr(path)
    assert getattr(sys.modules[CACHE_MODULE], attempts[0], None) is None
    modules, answers = _serve_like_scr(path, call, times=2)

    assert len(attempts) == 2 and len(loads) == 1
    assert modules[0].model is modules[1].model
    assert [answer[0] for answer in answers] == ["[0.1, 0.2]"] * 2
