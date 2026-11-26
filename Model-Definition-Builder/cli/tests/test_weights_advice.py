# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""A baked self-hosted model downloads its weights during the image build. Above 2 GB mdb recommends the
shared llm-weights volume: on the test system a build that fetched 2.6 GB failed twice (buildkit, EOF),
and the same weights staged on the volume published in about a minute (2026-11-26).

- the size comes from Hugging Face's file listing, with hf.include applied
- the advice names the size, the repository and the setting; below the threshold there is none
- mounted weights, hosted models and a requirements.json kept by hand are not asked about
- asking is best effort: offline or a failed request never fails a command
"""
import pytest

from mdb import cli
from mdb.core import weights
from mdb.core.manifest import load_manifest
from mdb.core.weights import BakedDownload, baked_download, baked_source, mounting_advice

GB = 1000 ** 3
#: A repository with two builds of one model, the way the ONNX repositories of Phi are laid out.
LISTING = {"siblings": [
    {"rfilename": "README.md", "size": 2000},
    {"rfilename": "cpu/int4/model.onnx", "size": 1000},
    {"rfilename": "cpu/int4/model.onnx.data", "size": int(2.7 * GB)},
    {"rfilename": "cuda/fp16/model.onnx.data", "size": 7 * GB},
]}


def _baked(repo_root, model_id="qwen_25_7b", **hf):
    manifest = load_manifest(repo_root / "LLM-Definitions" / model_id)
    manifest.provider.params["hf"].update(hf)
    return manifest


def _listing(monkeypatch, listing=LISTING):
    asked = []

    def get_json(session, url, headers=None, offline=False):
        asked.append(url)
        return listing

    monkeypatch.setattr(weights, "get_json", get_json)
    return asked


def test_the_size_is_what_the_build_downloads(repo_root, monkeypatch):
    asked = _listing(monkeypatch)
    whole = baked_download(_baked(repo_root), None)
    assert asked == ["https://huggingface.co/api/models/Qwen/Qwen2.5-7B-Instruct?blobs=true"]
    assert whole.files == 4 and whole.size == 2000 + 1000 + int(2.7 * GB) + 7 * GB and whole.large
    one = baked_download(_baked(repo_root, include="cpu/int4/*"), None)
    assert one.files == 2 and one.size == 1000 + int(2.7 * GB) and one.include == "cpu/int4/*"


def test_the_advice_names_size_repository_and_setting(repo_root):
    manifest = _baked(repo_root)
    advice = mounting_advice(manifest, BakedDownload("Qwen/Qwen2.5-7B-Instruct", None, int(15.24 * GB), 14))
    assert advice.startswith("qwen_25_7b: the image build downloads 15.2 GB of weights (the whole of Qwen/Qwen2.5-7B-Instruct).")
    assert "Above 2 GB" in advice and "'runtime.weights_source: mounted'" in advice
    assert advice.endswith("provider.params.hf.include narrows the download.")
    narrowed = mounting_advice(manifest, BakedDownload("org/repo", "cpu/int4/*", int(2.7 * GB), 2))
    assert "(org/repo, cpu/int4/*)" in narrowed and "narrows" not in narrowed


@pytest.mark.parametrize("size", [0, 1 * GB, 2 * GB])
def test_up_to_two_gigabytes_there_is_no_advice(repo_root, size):
    assert mounting_advice(_baked(repo_root), BakedDownload("org/repo", None, size, 3)) is None


def test_only_a_generated_baked_download_is_asked_about(repo_root):
    assert baked_source(_baked(repo_root)) == ("Qwen/Qwen2.5-7B-Instruct", None)
    # mounted: the build downloads nothing
    assert baked_source(load_manifest(repo_root / "LLM-Definitions" / "phi_3_mini_4k")) is None
    assert baked_source(load_manifest(repo_root / "Decision-Definitions" / "von_1_2")) is None
    # hosted: no weights
    assert baked_source(load_manifest(repo_root / "LLM-Definitions" / "gpt_5_mini")) is None
    # a requirements.json kept by hand: mdb did not write its download
    kept = _baked(repo_root)
    kept.generation.overrides.append("requirements.json")
    assert baked_source(kept) is None


def test_the_command_helper_prints_the_advice_and_never_fails(repo_root, monkeypatch, capsys):
    printed = []
    monkeypatch.setattr(cli, "_line", lambda text, style=None: printed.append((text, style)))
    _listing(monkeypatch)
    advice = cli._weights_advice(_baked(repo_root), session=object())
    assert advice and printed == [(advice, "yellow")]
    # a small download says nothing
    printed.clear()
    _listing(monkeypatch, {"siblings": [{"rfilename": "model.safetensors", "size": GB}]})
    assert cli._weights_advice(_baked(repo_root), session=object()) is None and printed == []

    # a request that fails is a dim note, not an error
    def refuse(session, url, headers=None, offline=False):
        raise RuntimeError("401 Client Error: gated repository")

    monkeypatch.setattr(weights, "get_json", refuse)
    assert cli._weights_advice(_baked(repo_root), session=object()) is None and printed == []
    # offline: not asked at all
    monkeypatch.setenv("MDB_OFFLINE", "1")
    monkeypatch.setattr(weights, "get_json", lambda *a, **k: pytest.fail("no request offline"))
    assert cli._weights_advice(_baked(repo_root), session=object()) is None
