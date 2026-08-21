# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Token estimation without a tokenizer dependency.

The chunkers cap chunk size in TOKENS because the embedding model silently
truncates past its window (all-MiniLM-L6-v2: 256 word pieces — design §2b).
Without shipping a tokenizer we estimate conservatively: wordpiece counts run
above whitespace word counts (subword splits) and English averages ~4 chars
per token, so we take the max of both signals and the chunkers additionally
apply a safety margin (default 0.85) to the model's Input_Token_Limit.
"""
from __future__ import annotations


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    words = len(text.split())
    return max(int(words * 1.33) + 1, len(text) // 4)


def token_budget(input_token_limit: int, margin: float = 0.85) -> int:
    """Effective chunk budget for a model window, with safety margin."""
    return max(16, int(input_token_limit * margin))
