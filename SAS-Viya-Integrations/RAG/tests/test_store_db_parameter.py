"""storeDb is required by the two RAG programs for the SQL backends only: Weaviate has no database name.

Measured on the DGX Spark (2026-11-05): Ingest-Documents.sas with backend=weaviate and no storeDb failed with
"required parameter storeDb is missing", although its header said the value is not used there.
"""
import re
import textwrap
from pathlib import Path

import pytest

JOBS = Path(__file__).resolve().parents[2] / "RAG-Ingestion"


def _validation(program: str) -> str:
    """The parameter check of a program's Python, from its comment to the raise, dedented."""
    text = (JOBS / program).read_text(encoding="utf-8").replace("\r\n", "\n")
    match = re.search(r"\n( *# Weaviate has no database name.*?raise RuntimeError\([^\n]*\)\n)", text, re.DOTALL)
    assert match, f"{program}: no parameter check found"
    return textwrap.dedent(match.group(1))


def _check(program: str, **params) -> None:
    exec(compile(_validation(program), program, "exec"), {"P": params})


BASE = {"Ingest-Documents.sas": {"sourcePath": "sascontent:/Public/Docs", "collection": "c_v1", "storeHost": "h"},
        "Test-Retrieval.sas": {"question": "q?", "collection": "c_v1", "storeHost": "h"}}


@pytest.mark.parametrize("program", sorted(BASE))
@pytest.mark.parametrize("backend", ["weaviate", " Weaviate "])
def test_weaviate_needs_no_database_name(program, backend):
    _check(program, backend=backend, storeDb="", **BASE[program])


@pytest.mark.parametrize("program", sorted(BASE))
@pytest.mark.parametrize("backend", ["pgvector", "singlestore"])
def test_a_sql_backend_still_needs_its_database_name(program, backend):
    with pytest.raises(RuntimeError, match="required parameter storeDb is missing"):
        _check(program, backend=backend, storeDb="", **BASE[program])
    _check(program, backend=backend, storeDb="vectors", **BASE[program])


@pytest.mark.parametrize("program", sorted(BASE))
def test_the_other_required_parameters_stay_required_for_weaviate(program):
    for name in BASE[program]:
        params = {**BASE[program], name: ""}
        with pytest.raises(RuntimeError, match=f"required parameter {name} is missing"):
            _check(program, backend="weaviate", storeDb="", **params)
