# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What a run's embedding calls cost, for the log.

The AUTHORITATIVE cost is RAG_RUN_COST (Logging-Monitoring/Build-RAG-Cost-View
.sas), which joins the run history to EMBEDDING_FACT_SHEET in CAS. That view
stays the number to report on, because the fact sheet is where prices are
maintained and a price can change after a run.

This module exists for one narrower job: printing a cost in the run log while
the run is happening. A person reading a log should not have to open a report
to learn whether the last ten minutes cost a cent or a hundred dollars. Doing
that needs prices in the compute session, and the fact sheet is a CAS table the
Python side of the job does not have a client for.

So this is a deliberate, small COPY of the two price columns, and it is a copy
that must be regenerated when Embedding-Definitions/embedding_fact_sheet.csv
changes:

    python -m rag_core.pricing --from ../../Embedding-Definitions/embedding_fact_sheet.csv

A model missing here yields no cost line at all rather than a zero, on the same
principle as the view: an unpriced model must read as unknown, never as free.
"""
from __future__ import annotations

#: model_id -> (cost_type, unit_price)
#: Tokens  -> USD per input token       Seconds -> USD per embedding second
#: Generated from embedding_fact_sheet.csv; see the module docstring.
PRICES = {
    "all_minilm_l6_v2": ("Seconds", 3.9178e-05),
    "bge_base_en_v15": ("Seconds", 3.9178e-05),
    "bge_large_en_v15": ("Seconds", 3.9178e-05),
    "bge_small_en_v15": ("Seconds", 3.9178e-05),
    "embedding_gemma_300m": ("Seconds", 3.9178e-05),
    "gemini_embedding_001": ("Tokens", 1.5e-07),
    "granite_embedding_r2": ("Seconds", 3.9178e-05),
    "granite_embedding_small_r2": ("Seconds", 3.9178e-05),
    "text_embedding_3_large": ("Tokens", 1.3e-07),
    "text_embedding_3_small": ("Tokens", 2e-08),
    "titan_embed_text_v2": ("Tokens", 2e-08),
    "voyage_35": ("Tokens", 6e-08),
    "voyage_35_lite": ("Tokens", 2e-08),
    "voyage_code_3": ("Tokens", 1.8e-07),
    "voyage_finance_2": ("Tokens", 1.2e-07),
    "voyage_law_2": ("Tokens", 1.2e-07),
}


def estimate_cost(model: str, usage: dict) -> tuple:
    """(cost, basis) for a run's embedding usage, or (None, reason).

    `usage` is the EmbeddingClient's own tally: calls, tokens, run_time.
    """
    priced = PRICES.get(str(model or "").strip())
    if not priced:
        return None, f"{model} is not priced in this rag_core copy of the fact sheet"
    cost_type, unit = priced
    if cost_type == "Tokens":
        tokens = int((usage or {}).get("tokens") or 0)
        return tokens * unit, f"{tokens} tokens x ${unit:.11f}/token"
    seconds = float((usage or {}).get("run_time") or 0.0)
    # 3 decimals: a short run embeds in tens of milliseconds, and "0.0
    # embedding seconds" alongside a non-zero cost reads as a bug.
    return seconds * unit, f"{seconds:.3f} embedding seconds x ${unit:.9f}/s"


def log_cost(model: str, usage: dict, log=print) -> None:
    """Print what this run's embeddings cost, or why that is not known."""
    calls = int((usage or {}).get("calls") or 0)
    cost, basis = estimate_cost(model, usage)
    if cost is None:
        log(f"rag cost: {calls} embedding calls - cost unknown ({basis})")
        return
    # Six decimals: an ingestion of a few hundred chunks on a local container
    # lands in the thousandths of a cent, and rounding it to $0.00 would read
    # as "free" when the point of printing it is that it is not.
    log(f"rag cost: {calls} embedding calls, ${cost:.6f} for this run "
        f"({basis}). Authoritative totals: RAG_RUN_COST.")


def _regenerate(csv_path: str) -> str:
    """Re-emit the PRICES literal from the fact sheet (developer utility)."""
    import csv

    lines = ["PRICES = {"]
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            cost_type = (row.get("cost_type") or "").strip()
            raw = (row.get("input_token_price") if cost_type == "Tokens"
                   else row.get("second_cost")) or ""
            raw = raw.strip()
            if cost_type not in ("Tokens", "Seconds") or raw in ("", "."):
                continue
            lines.append(f'    "{row["model_id"]}": ("{cost_type}", {float(raw)!r}),')
    lines.append("}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    if "--from" in sys.argv:
        print(_regenerate(sys.argv[sys.argv.index("--from") + 1]))
    else:
        print(__doc__)
