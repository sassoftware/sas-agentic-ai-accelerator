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
