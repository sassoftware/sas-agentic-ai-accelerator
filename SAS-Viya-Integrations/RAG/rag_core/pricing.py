# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""What a run's embedding and enrichment calls cost, for the log.

The AUTHORITATIVE cost is RAG_RUN_COST (Logging-Monitoring/Build-RAG-Cost-View
.sas), which joins the run history to EMBEDDING_FACT_SHEET in CAS. That view
stays the number to report on, because the fact sheet is where prices are
maintained and a price can change after a run.

This module exists for one narrower job: printing a cost in the run log while
the run is happening. A person reading a log should not have to open a report
to learn whether the last ten minutes cost a cent or a hundred dollars. Doing
that needs prices in the compute session, and the fact sheet is a CAS table the
Python side of the job does not have a client for.

So this is a deliberate, small COPY of the price columns, and it is a copy
that must be regenerated when either fact sheet changes:

    python -m rag_core.pricing --from ../../Embedding-Definitions/embedding_fact_sheet.csv
    python -m rag_core.pricing --llm-from ../../LLM-Definitions/llm_fact_sheet.csv

A model missing here yields no cost line at all rather than a zero, on the same
principle as the view: an unpriced model must read as unknown, never as free.

The LLM table exists for the Enrich stage, which spends real money per chunk -
one call for every chunk of the corpus, repeated whenever the corpus is
re-chunked. That is the number nobody should have to leave the log to find.
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


#: LLM model_id -> (cost_type, unit_in, unit_out)
#: Tokens  -> USD per input / output token   Seconds -> USD per second, in unit_in
#: Generated from llm_fact_sheet.csv; see the module docstring.
LLM_PRICES = {
    "claude_sonnet_4_5": ("Tokens", 3e-06, 1.5e-05),
    "free_models_router": ("Tokens", 0.0, 0.0),
    "gemini_flash_25": ("Tokens", 3e-07, 2.5e-06),
    "google_gemma_4_31b_free": ("Tokens", 0.0, 0.0),
    "gpt_41_mini": ("Tokens", 4e-07, 1.6e-06),
    "gpt_4o_2024_05_13": ("Tokens", 1.25e-06, 1e-05),
    "gpt_4o_mini_2024_07_18": ("Tokens", 1.5e-07, 6e-07),
    "gpt_4o_mini_2025_01_01": ("Tokens", 1.5e-07, 6e-07),
    "gpt_4o_mini_az_2024_07_18": ("Tokens", 1.5e-07, 6e-07),
    "gpt_56_sol": ("Tokens", 5e-06, 3e-05),
    "gpt_5_mini": ("Tokens", 2.5e-07, 2e-06),
    "ling_3_0_flash_free": ("Tokens", 0.0, 0.0),
    "llama_31_405b": ("Tokens", 3e-06, 3e-06),
    "llama_32_1b": ("Seconds", 3.9178e-05, 0.0),
    "llama_32_3b": ("Seconds", 6.9178e-05, 0.0),
    "llama_33_70b": ("Tokens", 1.3e-07, 4e-07),
    "mistral_nemo": ("Seconds", 6.9178e-05, 0.0),
    "mistral_small_32": ("Tokens", 1e-07, 3e-07),
    "moonshotai_kimi_k3": ("Tokens", 3e-06, 1.5e-05),
    "nvidia_nemotron_3_ultra_free": ("Tokens", 0.0, 0.0),
    "phi_35_mini": ("Seconds", 3.9178e-05, 0.0),
    "phi_3_mini_4k": ("Seconds", 3.9178e-05, 0.0),
    "poolside_laguna_s_2_1_free": ("Tokens", 0.0, 0.0),
    "qwen_25_05b": ("Seconds", 3.9178e-05, 0.0),
    "qwen_25_15b": ("Seconds", 3.9178e-05, 0.0),
    "qwen_25_7b": ("Seconds", 6.9178e-05, 0.0),
    "smollm_135m": ("Seconds", 3.9178e-05, 0.0),
    "smollm_17b": ("Seconds", 3.9178e-05, 0.0),
    "smollm_360m": ("Seconds", 3.9178e-05, 0.0),
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


def estimate_enrich_cost(model: str, usage: dict) -> tuple:
    """(cost, basis) for a run's enrichment calls, or (None, reason).

    `usage` is the PromptModel's tally: calls, input_tokens, output_tokens,
    run_time, failed. Input and output tokens are priced separately because
    an LLM charges them separately - and for contextual headers the ratio is
    lopsided (a whole document in, two sentences out), so collapsing them
    would misstate the bill in the expensive direction.

    A prompt manifested WITHOUT the prompt_length/output_length outputs
    returns no token counts, so a token-priced model has nothing to multiply.
    That reads as unknown, and says which outputs would fix it.
    """
    priced = LLM_PRICES.get(str(model or "").strip())
    if not priced:
        return None, (f"{model} is not priced in this rag_core copy of the LLM "
                      "fact sheet" if model else
                      "the prompt does not say which LLM it calls")
    cost_type, unit_in, unit_out = priced
    if cost_type == "Tokens":
        tokens_in = int((usage or {}).get("input_tokens") or 0)
        tokens_out = int((usage or {}).get("output_tokens") or 0)
        if not tokens_in and not tokens_out:
            return None, ("the prompt returns no token counts - re-manifest it "
                          "with the prompt_length and output_length outputs "
                          "selected to see what enrichment costs")
        return (tokens_in * unit_in + tokens_out * unit_out,
                f"{tokens_in} input tokens x ${unit_in:.11f} + {tokens_out} "
                f"output tokens x ${unit_out:.11f}")
    seconds = float((usage or {}).get("run_time") or 0.0)
    return seconds * unit_in, f"{seconds:.3f} LLM seconds x ${unit_in:.9f}/s"


def log_enrich_cost(model: str, usage: dict, log=print) -> None:
    """Print what this run's enrichment cost, or why that is not known."""
    calls = int((usage or {}).get("calls") or 0)
    failed = int((usage or {}).get("failed") or 0)
    tail = f", {failed} failed" if failed else ""
    cost, basis = estimate_enrich_cost(model, usage)
    if cost is None:
        log(f"rag cost: {calls} enrichment calls{tail} - cost unknown ({basis})")
        return
    log(f"rag cost: {calls} enrichment calls{tail}, ${cost:.6f} for this run "
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


def _regenerate_llm(csv_path: str) -> str:
    """Re-emit LLM_PRICES from the LLM fact sheet (developer utility).

    A token-priced model needs BOTH prices to be usable, so one missing
    column drops the row - an LLM priced on its input alone would report a
    cost that is quietly too low rather than an honest unknown.
    """
    import csv

    def number(raw):
        raw = str(raw or "").strip()
        try:
            return float(raw) if raw not in ("", ".") else None
        except ValueError:
            return None

    lines = ["LLM_PRICES = {"]
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        for row in sorted(csv.DictReader(fh), key=lambda r: r.get("model_id") or ""):
            cost_type = (row.get("cost_type") or "").strip()
            if cost_type == "Tokens":
                unit_in = number(row.get("input_token_price"))
                unit_out = number(row.get("output_token_price"))
                if unit_in is None or unit_out is None:
                    continue
            elif cost_type == "Seconds":
                unit_in, unit_out = number(row.get("second_cost")), 0.0
                if unit_in is None:
                    continue
            else:
                continue
            lines.append(f'    "{row["model_id"]}": ("{cost_type}", '
                         f"{unit_in!r}, {unit_out!r}),")
    lines.append("}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    if "--from" in sys.argv:
        print(_regenerate(sys.argv[sys.argv.index("--from") + 1]))
    elif "--llm-from" in sys.argv:
        print(_regenerate_llm(sys.argv[sys.argv.index("--llm-from") + 1]))
    else:
        print(__doc__)
