# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""How long a hosted-API scorer waits for its provider (runtime.timeout_s).

Every hosted scorer passed timeout=60, the reasoning models included, and an answer at a high effort can take
minutes (upgrade review of 2026-10-21). A model that reasons gets 300 seconds: from mdb add, in the shipped
definitions, through an import of its folder, and on the way in - the NGINX Ingress ended a request after its
default of 60 seconds."""
import shutil
from pathlib import Path

import pytest

from mdb.core.generator import effective_score_file, render_assets
from mdb.core.manifest import MANIFEST_FILENAME, RuntimeBlock, load_manifest
from mdb.core.paths import FACT_SHEETS
from mdb.providers.base import REASONING_TIMEOUT_S, CatalogModel, call_timeout


@pytest.mark.parametrize("model, expected", [
    (CatalogModel(ref="o", display_name="o", reasoning=True), REASONING_TIMEOUT_S),
    (CatalogModel(ref="c", display_name="c", adaptive_thinking=True), REASONING_TIMEOUT_S),
    (CatalogModel(ref="g", display_name="g"), 60),
    (CatalogModel(ref="e", display_name="e", kind="embedding", reasoning=True), 60),
    (CatalogModel(ref="d", display_name="d", kind="decision", reasoning=True), 60),
], ids=["reasoning", "adaptive thinking", "neither", "embedding", "decision"])
def test_a_new_definition_of_a_model_that_reasons_waits_300_seconds(model, expected):
    assert call_timeout(model) == expected
    assert RuntimeBlock.model_fields["timeout_s"].default == 60


def test_every_shipped_definition_with_a_reasoning_effort_waits_300_seconds(repo_root):
    reasoning = []
    for path in sorted((repo_root / "LLM-Definitions").glob(f"*/{MANIFEST_FILENAME}")):
        manifest = load_manifest(path.parent)
        if "reasoning_effort" in manifest.options:
            reasoning.append(manifest.model_id)
            assert manifest.runtime.timeout_s == REASONING_TIMEOUT_S, manifest.model_id
    assert len(reasoning) >= 8


def test_the_score_code_carries_the_timeout(repo_root, core):
    manifest = load_manifest(repo_root / "LLM-Definitions" / "gpt_5_mini")
    score = render_assets(manifest, core)[effective_score_file(manifest)].decode("utf-8")
    assert "timeout=300," in score and "timeout=60" not in score


def test_an_import_keeps_the_timeout_of_the_score_code(repo_root, core, tmp_path):
    """An import without definition.yaml (mdb import, mdb pull --import) read no timeout and gave 60."""
    from mdb.core.importer import import_folder
    folder = tmp_path / "gpt_5_mini"
    shutil.copytree(repo_root / "LLM-Definitions" / "gpt_5_mini", folder)
    (folder / MANIFEST_FILENAME).unlink()

    manifest = import_folder(folder, repo_root / "LLM-Definitions" / FACT_SHEETS["llm"]).manifest
    assert manifest.runtime.timeout_s == REASONING_TIMEOUT_S


@pytest.mark.parametrize("template", ["deploy-modelName-template.yaml", "deploy-modelName-env-template.yaml",
                                      "deploy-modelName-PV-template.yaml"])
def test_the_ingress_lets_a_request_take_as_long_as_the_scorer_waits(repo_root, template):
    text = (repo_root / "SCR-LLM-Deployment-YAML" / template).read_text(encoding="utf-8")
    for annotation in ("proxy-read-timeout", "proxy-send-timeout"):
        assert f'nginx.ingress.kubernetes.io/{annotation}: "{REASONING_TIMEOUT_S}"' in text, annotation
    contour = (repo_root / "SCR-LLM-Deployment-YAML" / "llm-router-contour-template.yaml").read_text(encoding="utf-8")
    assert f"projectcontour.io/response-timeout: {REASONING_TIMEOUT_S}s" in contour
