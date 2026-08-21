import { optionFlag } from './rag-options';
/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * The vector-store backends the RAG runtime supports.
 *
 * ONE list, used by three things that must not drift apart: the Options pane
 * generates an enable/disable field per entry, the config carries a default
 * per entry, and the setup form offers what survives both. Mirrors
 * rag_core.adapters.REGISTRY — a backend the runtime cannot load must never
 * be offered here.
 *
 * Adding a backend is adding a row: its Option field, its config default and
 * its place in the dropdown all follow.
 */

export interface RagBackend {
  /** The value the steps and the ingestion job use. */
  key: string;
  /** What a person sees. */
  label: string;
  /** Credential-domain entries a user needs to reach this store. */
  entries: string[];
}

export const RAG_BACKENDS: ReadonlyArray<RagBackend> = [
  {
    key: 'pgvector',
    label: 'pgvector (PostgreSQL)',
    entries: ['PGVECTOR_RAG_USER', 'PGVECTOR_RAG_PW'],
  },
  {
    key: 'singlestore',
    label: 'SingleStore',
    entries: ['SINGLESTORE_RAG_USER', 'SINGLESTORE_RAG_PW'],
  },
];

/** Config key carrying whether the deployment offers this backend. */
export function backendOptionKey(backend: RagBackend): string {
  return `enable_${backend.key}`;
}

/**
 * Whether the deployment offers this backend.
 *
 * The option is a CHECKBOX (VA `boolean`), so a fresh report stores a real
 * `true`/`false`. It used to be a dropdown storing `'1'`/`'0'`, and reports
 * configured back then still hold those strings - which is why this reads
 * both. It matters more than it looks: `String(false) !== '0'` is true, so a
 * naive check would read an unticked new-style box as ENABLED and offer a
 * store the deployment deliberately withheld.
 *
 * Absent means offered: a deployment that has never opened the options pane
 * gets every backend, which is what the pane's own defaults say.
 */
export function backendEnabled(config: Record<string, unknown>, backend: RagBackend): boolean {
  return optionFlag(config[backendOptionKey(backend)], true);
}
