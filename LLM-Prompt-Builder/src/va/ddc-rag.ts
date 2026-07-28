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
        'SAS Compute context the ingestion job runs in (passed to Job Execution as _contextName; blank = the Job Execution default). Its Python needs the packages from the RAG administration guide. If the context runs its servers as a service account, that account needs the credential — see Managing Credentials.'
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
