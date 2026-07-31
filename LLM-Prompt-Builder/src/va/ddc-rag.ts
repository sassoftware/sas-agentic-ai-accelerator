/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * SAS Visual Analytics DDC integration for the RAG Builder — same lifecycle as
 * the Prompt Builder's (src/va/ddc.ts): one `message` listener; when VA sends a
 * `resultName`, the options group is posted back and, with `urlOption: true`,
 * VA mirrors the author's values into the iframe URL for config-rag.ts to read
 * on the next load. Vector-store credentials are NOT part of this contract —
 * they live in the SAS Viya credential domain.
 */

import type { RagRuntimeConfig } from '../config-rag';
import { RAG_BACKENDS, backendOptionKey } from '../objects/rag-backends';

interface OptionField {
  name: string;
  label: string;
  type: string;
  tooltip?: string;
  value?: unknown;
  placeholder?: string;
  dataProvider?: Array<{ key: string; text: string }>;
}

export function initRagVaIntegration(config: RagRuntimeConfig): void {
  const targets = getPostTargets();
  if (targets.length === 0) return;

  const optionsConfig = buildOptionsConfig(config);
  const postedFor = new Set<string>();

  function postOptions(resultName: string): void {
    const envelope = { resultName, optionsConfig };
    for (const target of targets) {
      try {
        target.postMessage(envelope, '*');
      } catch {
        /* cross-origin post rejected — ignore and try the next target */
      }
    }
  }

  function handle(event: MessageEvent): void {
    let msg: Record<string, unknown> | null;
    if (typeof event.data === 'string') {
      try {
        msg = JSON.parse(event.data) as Record<string, unknown>;
      } catch {
        return;
      }
    } else if (event.data && typeof event.data === 'object') {
      msg = event.data as Record<string, unknown>;
    } else {
      return;
    }

    const resultName = typeof msg.resultName === 'string' ? msg.resultName : '';
    if (resultName && !postedFor.has(resultName)) {
      postedFor.add(resultName);
      postOptions(resultName);
    }
  }

  window.addEventListener('message', handle, false);
}

function getPostTargets(): Window[] {
  const targets: Window[] = [];
  if (window.parent && window.parent !== window) targets.push(window.parent);
  if (window.top && window.top !== window && window.top !== window.parent) {
    targets.push(window.top);
  }
  return targets;
}

function buildOptionsConfig(config: RagRuntimeConfig): Record<string, unknown> {
  const rb = config.ragBuilder;
  return {
    version: 1,
    urlOption: true,
    name: 'RagBuilderOptions',
    label: 'RAG Builder configuration',
    fields: [
      textField(
        'viyaHost',
        'SAS Viya host',
        config.viyaHost,
        'Base URL of SAS Viya. Defaults to the embedding origin when left blank.'
      ),
      textField(
        'modelRepositoryID',
        'Model Manager repository ID',
        rb.modelRepositoryID,
        'SAS Model Manager repository new RAG projects are created in.'
      ),
      textField(
        'embeddingProjectID',
        'Embedding model project ID',
        rb.embeddingProjectID,
        'SAS Model Manager project holding the registered embedding models. The Builder lists that project instead of asking users to type a model name - a name with no container behind it fails at the first embed call, long after the crawl and chunking have run. Leave blank only if you have no such project; the Builder then falls back to free text.'
      ),
      textField(
        'SCREndpoint',
        'SCR endpoint',
        rb.SCREndpoint,
        'Base URL of the SCR endpoint hosting the embedding containers. Blank = <SAS Viya host>/llm.'
      ),
      {
        name: 'deploymentType',
        label: 'Deployment type',
        type: 'String',
        value: rb.deploymentType ?? 'k8s',
        tooltip: 'How the embedding containers are deployed.',
        dataProvider: [
          { key: 'k8s', text: 'Kubernetes (k8s)' },
          { key: 'aca', text: 'Azure Container Apps (aca)' },
        ],
      } as OptionField,
      textField(
        'credentialDomain',
        'Credential domain',
        rb.credentialDomain,
        'SAS Viya credential domain the ingestion job and retrieval resolve vector-store credentials from (<BACKEND>_RAG_USER / <BACKEND>_RAG_PW entries). Defaults to agentic-ai-keys — see the Managing Credentials administration guide.'
      ),
      textField(
        'contentRoot',
        'RAG content root',
        rb.contentRoot,
        'SAS Content folder the deploy-rag-content scripts populated (rag_core, jobs, models). Generated job definitions are written to its "generated" subfolder.'
      ),
      textField(
        'casServer',
        'CAS server',
        rb.casServer,
        'CAS server used for the ingestion ledger and pipeline tables.'
      ),
      textField(
        'computeContext',
        'Ingestion compute context',
        rb.computeContext,
        'SAS Compute context the ingestion job runs in (passed to Job Execution as _contextName). It MUST run as the requesting user: the stock "SAS Job Execution compute context" runs its servers as a service account, and the ingestion steps cannot reuse the CAS session they need there. "SAS Studio compute context" works. Its Python also needs the packages from the RAG administration guide.'
      ),
      {
        name: 'storeSslmode',
        label: 'Vector store TLS',
        type: 'String',
        value: rb.storeSslmode ?? 'prefer',
        tooltip:
          'How the ingestion connects to the vector store. The six values are a PostgreSQL concept; other stores read "disable" as no TLS and anything else as TLS on. Set here rather than per setup, because a value that does not mean what it says is worse than no choice.',
        dataProvider: [
          { key: 'prefer', text: 'prefer' },
          { key: 'require', text: 'require' },
          { key: 'verify-ca', text: 'verify-ca' },
          { key: 'verify-full', text: 'verify-full' },
          { key: 'allow', text: 'allow' },
          { key: 'disable', text: 'disable (no TLS)' },
        ],
      } as OptionField,
      {
        name: 'deletedPolicy',
        label: 'When a document disappears from the source',
        type: 'String',
        value: rb.deletedPolicy ?? 'retire',
        tooltip:
          'Retire keeps its chunks as unretrievable history, so the collection can still be read as of an earlier date and the run rolled back. Purge removes them for good.',
        dataProvider: [
          { key: 'retire', text: 'Keep its chunks as history' },
          { key: 'purge', text: 'Remove its chunks permanently' },
        ],
      } as OptionField,
      textField(
        'retainDays',
        'Keep retired chunks for (days)',
        rb.retainDays,
        'Retired chunk generations older than this are dropped after each load. 0 keeps them forever. Live rows are never touched, so retrieval cannot change - only how far back an as-of read can reach. Matters more on SingleStore, where a vector index cannot be limited to live rows.'
      ),
      {
        name: 'recordHistory',
        label: 'Record run history',
        type: 'String',
        value: rb.recordHistory ?? '1',
        tooltip:
          'Writes rag_runs, rag_doc_events and rag_configs beside the collection and publishes the first two to CAS for reporting. Without it there is no record of what a run did.',
        dataProvider: [
          { key: '1', text: 'Record each run' },
          { key: '0', text: 'Do not record' },
        ],
      } as OptionField,
      textField(
        'embedReplicas',
        'Embedding container replicas',
        rb.embedReplicas,
        'How many replicas of the embedding container this deployment runs. The ingestion sizes its parallel calls from this, so setting it to what you actually run is the difference between saturating the container and leaving it idle.'
      ),
      {
        name: 'persistElements',
        label: 'Save the element table to disk',
        type: 'String',
        value: rb.persistElements ?? '1',
        tooltip:
          'The <prefix>_ELEMENTS table is rebuilt from the documents on the next run, so it need not survive a restart, and on a large corpus it is one of the biggest things the pipeline writes. Applies to the Studio Flow path - the generated ingestion job does not build this table.',
        dataProvider: [
          { key: '1', text: 'Save to disk' },
          { key: '0', text: 'Keep in memory only' },
        ],
      } as OptionField,
      {
        name: 'persistChunks',
        label: 'Save the chunk table to disk',
        type: 'String',
        value: rb.persistChunks ?? '1',
        tooltip:
          'As above for <prefix>_CHUNKS, and likewise applies to the Studio Flow path. The ledger and the embedded chunks are always saved - the incremental diff and the embedding checkpoint depend on them.',
        dataProvider: [
          { key: '1', text: 'Save to disk' },
          { key: '0', text: 'Keep in memory only' },
        ],
      } as OptionField,
      // one enable/disable field per backend, generated from RAG_BACKENDS -
      // a comma-separated list meant typing store names by hand, and a typo
      // silently offered nothing
      ...RAG_BACKENDS.map(
        (backend) =>
          ({
            name: backendOptionKey(backend),
            label: `Offer ${backend.label}`,
            type: 'String',
            value: rb[backendOptionKey(backend)] ?? '1',
            tooltip: `Whether end users may choose ${backend.label} for a RAG setup. Independent of credentials: this is what the deployment offers, and a user who holds no ${backend.entries[0]} / ${backend.entries[1]} entry sees it disabled with the missing entry named.`,
            dataProvider: [
              { key: '1', text: 'Yes' },
              { key: '0', text: 'No' },
            ],
          }) as OptionField
      ),
    ],
    groups: [],
  };
}

function textField(
  name: string,
  label: string,
  value: unknown,
  tooltip: string
): OptionField {
  return { name, label, type: 'String', value: value ?? '', tooltip };
}
