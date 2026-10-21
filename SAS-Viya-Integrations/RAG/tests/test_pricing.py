# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The prices rag_core prints in the run log are the prices of the fact sheets.

rag_core/pricing.py holds a copy of the price columns, because the Python of
a job has no CAS client to read the sheets with. A definition added with mdb
writes its row to the sheet and nothing else: until the copy is regenerated,
an enrichment run with that model logs "cost unknown". The Claude Haiku 4.5
(Bedrock) row has had its prices in the sheet since 2026-09-13 while the copy
still read it as unpriced, and nothing failed - these tests are what fails now.
"""
import pathlib

from rag_core import pricing

REPO = pathlib.Path(__file__).resolve().parents[3]
EMBEDDING_SHEET = REPO / "Embedding-Definitions" / "embedding_fact_sheet.csv"
LLM_SHEET = REPO / "LLM-Definitions" / "llm_fact_sheet.csv"


def test_the_embedding_prices_are_what_the_fact_sheet_says():
    assert pricing.prices(str(EMBEDDING_SHEET)) == pricing.PRICES


def test_the_llm_prices_are_what_the_fact_sheet_says():
    assert pricing.llm_prices(str(LLM_SHEET)) == pricing.LLM_PRICES


def test_the_printed_maps_are_the_maps():
    """What `python -m rag_core.pricing --from / --llm-from` prints is source
    that, pasted into the module, gives these same maps."""
    scope = {}
    exec(pricing._regenerate(str(EMBEDDING_SHEET)), scope)
    exec(pricing._regenerate_llm(str(LLM_SHEET)), scope)
    assert scope["PRICES"] == pricing.PRICES
    assert scope["LLM_PRICES"] == pricing.LLM_PRICES


def test_a_token_priced_llm_without_both_prices_is_left_out(tmp_path):
    sheet = tmp_path / "llm_fact_sheet.csv"
    sheet.write_text(
        "model_id,cost_type,input_token_price,output_token_price,second_cost\n"
        "both,Tokens,0.000001,0.000005,.\n"
        "input_only,Tokens,0.000001,.,.\n"
        "local,Seconds,.,.,0.000039178\n"
        "unpriced,Seconds,.,.,.\n",
        encoding="utf-8")
    assert pricing.llm_prices(str(sheet)) == {
        "both": ("Tokens", 1e-06, 5e-06),
        "local": ("Seconds", 3.9178e-05, 0.0),
    }
