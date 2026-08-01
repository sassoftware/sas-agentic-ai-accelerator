// Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * Manifest retrieve_context.py for one RAG Setup, in the browser.
 *
 * This is a deliberate second implementation of `rag_core.manifest
 * .render_retrieval_model`, and the duplication is the point: the Python one
 * serves the SAS Studio "RAG - Register Setup" step, which runs where Python
 * runs; the Builder has no Python and would otherwise need a job round trip
 * to write a file it already has all the values for. The Prompt Builder
 * manifests its prompts from the browser for the same reason.
 *
 * The contract both share is narrow and stable — the MANIFEST block markers
 * and the names inside them — so keep the two in step. If a new setting joins
 * the block, it joins in `rag_core/manifest.py` first: that copy is the one a
 * hand-built Studio flow uses, and a mismatch there is silent.
 *
 * Like the Python, this REFUSES rather than guesses. A retrieval model
 * deployed with `your-database-host` still in it looks fine until a decision
 * runs in production and cannot reach a database nobody owns.
 */
import type { RagSetup } from '../types/rag';

const MANIFEST_START = '# ---- MANIFEST: rewritten per RAG Setup';
const MANIFEST_END = '# ----------------------------------------';

/** Written as `NAME = "value"`, in this order, for readability only. */
const MANIFEST_STRINGS = [
  'BACKEND',
  'COLLECTION',
  'EMBED_MODEL',
  'EMBED_ENDPOINT',
  'STORE_HOST',
  'STORE_PORT',
  'STORE_DB',
  'STORE_SSLMODE',
  'CREDENTIAL_DOMAIN',
  'INGESTION_RUN_ID',
] as const;

/** Written unquoted as `NAME = 4`. */
const MANIFEST_NUMBERS = ['DEFAULT_K'] as const;

/** Without these the model cannot reach anything; see the module note. */
const REQUIRED = ['BACKEND', 'COLLECTION', 'EMBED_MODEL', 'STORE_HOST', 'STORE_DB'] as const;

export type ManifestSettings = Record<string, string | number>;

/**
 * The manifest values a setup implies.
 *
 * `runId` stamps corpus-version lineage onto the model, so a retrieved chunk
 * can be traced to the ingestion that wrote it. It is empty until a run has
 * happened, which is honest: nothing has been ingested yet.
 */
export function manifestSettings(
  setup: RagSetup,
  embedEndpoint: string,
  runId = ''
): ManifestSettings {
  return {
    BACKEND: setup.store.backend,
    COLLECTION: setup.store.collection,
    EMBED_MODEL: setup.embedding.model,
    EMBED_ENDPOINT: embedEndpoint,
    STORE_HOST: setup.store.host,
    STORE_PORT: String(setup.store.port ?? ''),
    STORE_DB: setup.store.database,
    STORE_SSLMODE: setup.store.sslmode,
    CREDENTIAL_DOMAIN: setup.credentialDomain,
    INGESTION_RUN_ID: runId,
    // k is a per-CALL argument of the retrieval model, so this is only the
    // fallback a caller that passes nothing gets.
    DEFAULT_K: 4,
  };
}

/**
 * Rewrite the template's MANIFEST block. Throws with a readable reason.
 */
export function renderRetrievalModel(template: string, settings: ManifestSettings): string {
  const missing = REQUIRED.filter((key) => !String(settings[key] ?? '').trim());
  if (missing.length > 0) {
    throw new Error(`cannot manifest the retrieval model without ${missing.join(', ')}`);
  }
  const lines = template.split('\n');
  let start = -1;
  let end = -1;
  for (let index = 0; index < lines.length; index += 1) {
    if (start < 0 && lines[index].startsWith(MANIFEST_START)) start = index;
    else if (start >= 0 && lines[index].startsWith(MANIFEST_END)) {
      end = index;
      break;
    }
  }
  if (start < 0 || end < 0) {
    throw new Error(
      "the retrieval template has no MANIFEST block - it is not the accelerator's retrieve_context.py"
    );
  }

  const block = [lines[start]];
  for (const key of MANIFEST_STRINGS) {
    const value = String(settings[key] ?? '');
    // A quote or a newline here does not produce a bad value, it produces a
    // syntactically broken Python module - so it is refused at the source
    // rather than escaped into something that merely parses.
    if (value.includes('"') || value.includes('\n')) {
      throw new Error(`${key} may not contain quotes or newlines: ${JSON.stringify(value)}`);
    }
    block.push(`${key} = "${value}"`);
  }
  for (const key of MANIFEST_NUMBERS) {
    const parsed = Number(settings[key]);
    block.push(`${key} = ${Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : 4}`);
  }
  block.push(lines[end]);
  return [...lines.slice(0, start), ...block, ...lines.slice(end + 1)].join('\n');
}
