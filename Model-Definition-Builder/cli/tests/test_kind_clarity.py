# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The llm-vs-embedding kind must be explicit and consistent: live catalogs
keep the static snapshot's kind, embedding-only adapters force it, and a
kind/template mismatch fails validation (V012)."""
import shutil

from mdb.cli import _enrich_from_static
from mdb.core.manifest import load_manifest
from mdb.core.validator import validate_folder
from mdb.providers import load_adapters
from mdb.providers.base import CatalogModel


def test_enrich_from_static_preserves_embedding_kind():
    """OpenAI's live /v1/models returns ids only - the static snapshot's kind
    and embedding_length must survive enrichment, otherwise a live add of a
    known embedding model silently builds an LLM definition."""
    live = [CatalogModel(ref="text-embedding-3-small", display_name="text-embedding-3-small", source="live")]
    static = [CatalogModel(ref="text-embedding-3-small", display_name="Text Embedding 3 Small",
                           kind="embedding", embedding_length=1536)]
    enriched = _enrich_from_static(live, static)
    assert enriched[0].kind == "embedding"
    assert enriched[0].embedding_length == 1536
    # An llm entry stays llm - enrichment only ever upgrades to embedding.
    live_llm = [CatalogModel(ref="gpt-x", display_name="gpt-x", source="live")]
    static_llm = [CatalogModel(ref="gpt-x", display_name="GPT X")]
    assert _enrich_from_static(live_llm, static_llm)[0].kind == "llm"


def test_voyage_adapter_is_embedding_only():
    """voyage can never produce an LLM definition - its chat template IS the
    embedding template, which `mdb add` uses to force kind=embedding."""
    adapter = load_adapters()["voyage"]
    assert adapter.embedding_template is not None
    assert adapter.template == adapter.embedding_template


def test_v012_flags_kind_template_mismatch(core, tmp_path, repo_root, fact_sheet):
    """A misclassified definition (embedding template, kind llm) must fail
    validation instead of surfacing only at smoke-test time."""
    source = repo_root / "Embedding-Definitions" / "all_minilm_l6_v2"
    folder = tmp_path / "all_minilm_l6_v2"
    shutil.copytree(source, folder)

    # The shipped definition is consistent - no V012.
    embedding_fact_sheet = repo_root / "Embedding-Definitions" / "embedding_fact_sheet.csv"
    issues = validate_folder(folder, core, embedding_fact_sheet)
    assert not [i for i in issues if i.rule == "V012"]

    # Flip the kind: template stays emb_*, kind claims llm -> V012 error.
    manifest = load_manifest(folder)
    manifest.kind = "llm"
    manifest.save(folder)
    issues = validate_folder(folder, core, embedding_fact_sheet)
    v012 = [i for i in issues if i.rule == "V012"]
    assert len(v012) == 1 and v012[0].severity == "error"


def test_providers_kinds_are_derivable():
    """`mdb providers` derives a kinds column - every adapter maps to exactly
    one of llm / embedding / llm + embedding."""
    for adapter in load_adapters().values():
        embedding_template = getattr(adapter, "embedding_template", None)
        if embedding_template is None:
            assert adapter.template, adapter.id
        # both-kinds adapters must have DISTINCT templates, otherwise the
        # embedding-only rule would misclassify them
        elif adapter.template == embedding_template:
            assert adapter.id == "voyage"
