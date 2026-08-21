/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Embedding dimensions of the models the accelerator ships definitions for.
 *
 * A vector column has to be created at the model's width, and getting it wrong
 * is not a small error: the collection is unusable and has to be rebuilt. SAS
 * Model Manager records no numeric dimension for a registered embedding model
 * (only prose in the description), so the width cannot be read back from the
 * registration - it comes from the model's own fact sheet.
 *
 * Mirrors the embedding_length column of Embedding-Definitions/
 * embedding_fact_sheet.csv. A model registered outside that set is still
 * selectable; its dimension simply has to be entered by hand.
 */

export const EMBEDDING_DIMENSIONS: Readonly<Record<string, number>> = {
  all_minilm_l6_v2: 384,
  bge_base_en_v15: 768,
  bge_large_en_v15: 1024,
  bge_small_en_v15: 384,
  embedding_gemma_300m: 384,
  gemini_embedding_001: 3072,
  granite_embedding_r2: 768,
  granite_embedding_small_r2: 384,
  text_embedding_3_large: 3072,
  text_embedding_3_small: 1536,
  titan_embed_text_v2: 1024,
  voyage_35: 1024,
  voyage_35_lite: 1024,
  voyage_code_3: 1024,
  voyage_finance_2: 1024,
  voyage_law_2: 1024,
};

/** The published width of an embedding model, or 0 when it is not known. */
export function embeddingDimensions(model: string): number {
  return EMBEDDING_DIMENSIONS[String(model || '').trim()] ?? 0;
}

/**
 * The largest input each embedding model accepts, in tokens.
 *
 * This is a hard ceiling, not a preference: text beyond a model's window is
 * silently dropped by the model rather than rejected, so a chunk built above
 * it is embedded from its opening only. Retrieval then matches on text the
 * answer never sees, and nothing in the run reports a problem. The ingestion
 * applies its own safety margin below this (token_budget), because token
 * counts are estimated rather than exact.
 *
 * Mirrors the max_tokens column of Embedding-Definitions/
 * embedding_fact_sheet.csv, alongside EMBEDDING_DIMENSIONS.
 */
export const EMBEDDING_TOKEN_LIMITS: Readonly<Record<string, number>> = {
  all_minilm_l6_v2: 256,
  bge_base_en_v15: 512,
  bge_large_en_v15: 512,
  bge_small_en_v15: 512,
  embedding_gemma_300m: 256,
  gemini_embedding_001: 2048,
  granite_embedding_r2: 8192,
  granite_embedding_small_r2: 8192,
  text_embedding_3_large: 8192,
  text_embedding_3_small: 8192,
  titan_embed_text_v2: 8192,
  voyage_35: 32000,
  voyage_35_lite: 32000,
  voyage_code_3: 32000,
  voyage_finance_2: 32000,
  voyage_law_2: 16000,
};

/** The largest input an embedding model accepts, or 0 when it is not known. */
export function embeddingTokenLimit(model: string): number {
  return EMBEDDING_TOKEN_LIMITS[String(model || '').trim()] ?? 0;
}
