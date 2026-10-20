# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""A self-hosted scorer loads its weights once per worker process - no network.

The Python runtime of SAS Container Runtime runs the score file again for
every request (importlib exec_module in app_scoring.new_score). On a hack
environment of 2026-10-20 von_1_2 therefore read its 3 GB checkpoint from an
Azure Files volume on every call: 20-24 s per request, of which scoreModel
itself took 0.42 s. The self-hosted templates (dec_von, hf_transformers,
emb_sentence_transformers) now keep the loaded model in a module of
sys.modules, which outlives the runs of the file.

Each test renders the committed definition, stubs the model library, and runs
the file three times the way SCR does: the loader is called once, and
scoreModel answers after every run - the first cut of this change moved the
`modelVersion` the Von scorer reads into the loader and failed every request
with a NameError (HTTP 422) on the cluster, which a test that only counted
loads did not see.
"""
import importlib.util
import sys
import types

import pytest

from mdb.core.generator import effective_score_file, render_assets
from mdb.core.manifest import load_manifest

CACHE_MODULE = "_sas_accelerator_model_cache"


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


def _run_like_scr(tmp_path, manifest, core, times: int = 3):
    """exec_module the rendered score file `times` times, as app_scoring.new_score does per request."""
    name = effective_score_file(manifest)
    path = tmp_path / name
    path.write_bytes(render_assets(manifest, core)[name])
    modules = []
    for _ in range(times):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    return modules


def test_von_loads_its_backend_once_per_worker(monkeypatch, tmp_path, core, repo_root):
    built, evaluated = [], []

    class OptionMarkerBackend:
        def __init__(self, checkpoint_dir, device):
            built.append((checkpoint_dir, device))

        def evaluate(self, state, questions, model):
            evaluated.append((state, model))
            return {"answers": {"team": {"type": "choice", "choice": "billing", "confidence": 0.9}},
                    "usage": {"input_tokens": 12, "output_tokens": 0}}

    _stub(monkeypatch, "von.backends.option_marker_backend", OptionMarkerBackend=OptionMarkerBackend)
    manifest = load_manifest(repo_root / "Decision-Definitions" / "von_1_2")
    modules = _run_like_scr(tmp_path, manifest, core)
    questions = '{"team": {"type": "choice", "options": ["billing", "technical"]}}'
    answers = [m.scoreModel("charged twice", questions, "{}") for m in modules]

    assert built == [("/pybox/model/mount/von_1_2", "cpu")]
    # the warm-up runs with the load, not per request; every call names the model version
    assert evaluated == [("warm-up", "von-1.2.0")] + [("charged twice", "von-1.2.0")] * 3
    assert all(a[1:3] == ("billing", 0.9) for a in answers)
    assert len({id(m.backend) for m in modules}) == 1


def test_hf_transformers_loads_tokenizer_and_model_once(monkeypatch, tmp_path, core, repo_root):
    loads = []

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
    manifest = load_manifest(repo_root / "LLM-Definitions" / "smollm_135m")
    modules = _run_like_scr(tmp_path, manifest, core)
    answers = [m.scoreModel("Reply with OK.", "You are terse.", "{}") for m in modules]

    assert sorted(name for name, _ in loads) == ["AutoModelForCausalLM", "AutoTokenizer"]
    assert [(a[0], a[2], a[3]) for a in answers] == [("OK", 3, 2)] * 3
    assert len({(id(m.tokenizer), id(m.model)) for m in modules}) == 1


def test_sentence_transformers_loads_the_model_once(monkeypatch, tmp_path, core, repo_root):
    loads = []

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

        def encode_query(self, text):
            return _Vector([0.1, 0.2])

        encode_document = encode_query

        def preprocess(self, texts):
            return {"input_ids": _Ids()}

    _stub(monkeypatch, "sentence_transformers", SentenceTransformer=SentenceTransformer)
    manifest = load_manifest(repo_root / "Embedding-Definitions" / "all_minilm_l6_v2")
    modules = _run_like_scr(tmp_path, manifest, core)
    answers = [m.scoreModel("charged twice", "billing", "{}") for m in modules]

    assert len(loads) == 1
    assert [(a[0], a[2]) for a in answers] == [("[0.1, 0.2]", 5)] * 3
    assert len({id(m.model) for m in modules}) == 1


def test_two_models_in_one_worker_keep_their_own_weights(monkeypatch, tmp_path, core, repo_root):
    loads = []

    class SentenceTransformer:
        def __init__(self, checkpoint):
            loads.append(checkpoint)
            self.checkpoint = checkpoint

    _stub(monkeypatch, "sentence_transformers", SentenceTransformer=SentenceTransformer)
    first = load_manifest(repo_root / "Embedding-Definitions" / "all_minilm_l6_v2")
    second = load_manifest(repo_root / "Embedding-Definitions" / "bge_small_en_v15")
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a = _run_like_scr(tmp_path / "a", first, core, times=2)
    b = _run_like_scr(tmp_path / "b", second, core, times=2)

    assert len(loads) == 2 and loads[0] != loads[1]
    assert a[-1].model.checkpoint == loads[0] and b[-1].model.checkpoint == loads[1]
