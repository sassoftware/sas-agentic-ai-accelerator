# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""How much a container build downloads for a self-hosted model, and when to
recommend the shared weights volume instead.

A definition with ``weights_source: baked`` downloads its Hugging Face
repository during the image build. A download of a few GB makes the build slow
and can end it: on the test system a build that fetched 2.6 GB failed twice
with a buildkit connection error, while the same weights staged on the shared
``llm-weights`` volume published in about a minute. Hugging Face reports the
size of every file of a repository without a download, so the size is known
before the build starts.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Optional

import requests

from .manifest import ModelManifest
from .netutil import get_json

#: Above this many bytes of weights a baked build gets the recommendation.
LARGE_DOWNLOAD_BYTES = 2 * 1000 ** 3
HF_API = "https://huggingface.co/api/models"


@dataclass
class BakedDownload:
    repo: str
    include: Optional[str]
    size: int  # bytes
    files: int

    @property
    def gigabytes(self) -> float:
        return self.size / 1000 ** 3

    @property
    def large(self) -> bool:
        return self.size > LARGE_DOWNLOAD_BYTES


def baked_source(manifest: ModelManifest) -> Optional[tuple[str, Optional[str]]]:
    """(repository, include pattern) the image build of this definition downloads, or None: a hosted
    model, mounted weights, or a requirements.json kept by hand, whose download mdb did not write."""
    hf = manifest.provider.params.get("hf") or {}
    if not hf.get("repo") or manifest.runtime.weights_source != "baked":
        return None
    if "requirements.json" in manifest.generation.overrides:
        return None
    return hf["repo"], hf.get("include") or None


def baked_download(manifest: ModelManifest, session: requests.Session) -> Optional[BakedDownload]:
    """The size of what the build downloads, asked of Hugging Face. None when the build downloads no
    weights. Raises what the request raises (offline, a gated or unknown repository)."""
    source = baked_source(manifest)
    if source is None:
        return None
    repo, include = source
    siblings = get_json(session, f"{HF_API}/{repo}?blobs=true").get("siblings") or []
    taken = [f for f in siblings if include is None or fnmatch.fnmatch(f.get("rfilename", ""), include)]
    return BakedDownload(repo=repo, include=include, size=sum(int(f.get("size") or 0) for f in taken), files=len(taken))


def mounting_advice(manifest: ModelManifest, download: Optional[BakedDownload]) -> Optional[str]:
    """One line that recommends the shared volume, for a baked download above the threshold; else None."""
    if download is None or not download.large:
        return None
    what = f"{download.repo}, {download.include}" if download.include else f"the whole of {download.repo}"
    narrow = "" if download.include else (
        " If the repository holds several builds of the model, provider.params.hf.include narrows the download.")
    return (f"{manifest.model_id}: the image build downloads {download.gigabytes:.1f} GB of weights ({what}). "
            f"Above {LARGE_DOWNLOAD_BYTES // 1000 ** 3} GB a build is slow and can fail: set "
            "'runtime.weights_source: mounted' in definition.yaml and stage the weights once on the shared "
            f"llm-weights volume (Administration Guide, 'Serving Open-Weight Models').{narrow}")
