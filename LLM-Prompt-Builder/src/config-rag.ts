/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Standalone configuration for the RAG Builder (design §14).
 *
 * Mirrors the Prompt Builder pattern: build-time defaults, layered with URL
 * query parameters (which a SAS Visual Analytics embed mirrors from the
 * object's Options pane):
 *
 *   ...?viyaHost=https://viya.example.com
 *      &modelRepositoryID=<uuid>
 *      &SCREndpoint=https://viya.example.com/llm
 *      &credentialDomain=agentic-ai-keys
 *
 * Vector-store credentials never appear here or in the URL: ingestion and
 * retrieval resolve them server-side from the credential domain (entries
 * <BACKEND>_RAG_USER / <BACKEND>_RAG_PW) — the browser only ever handles
 * connection CONFIG (host/port/database).
 */

import type { RagBuilderConfig } from './types/rag';

export interface RagRuntimeConfig {
  /** Base URL of the SAS Viya deployment used for API calls. */
  viyaHost: string;
  /** RAG Builder instance configuration. */
  ragBuilder: RagBuilderConfig;
}

const DEFAULTS: RagRuntimeConfig = {
  viyaHost: window.location.origin,
  ragBuilder: {
    id: 'RGB',
    name: 'RAG Builder',
    // Environment-specific and MUST be supplied per deployment — left blank so
    // the app never calls SAS Viya with someone else's IDs (see isRagConfigured).
    modelRepositoryID: '',
    // Blank = <viyaHost>/llm at runtime.
    SCREndpoint: '',
    deploymentType: 'k8s',
    // Matches the create-credential-domain scripts' default.
    credentialDomain: 'agentic-ai-keys',
    // Where deploy-rag-content.ps1/.sh put the runtime.
    contentRoot: '/SAS Agentic AI Accelerator/RAG',
    casServer: 'cas-shared-default',
    // Compute context the ingestion job runs in (Job Execution _contextName);
    // blank = the Job Execution default context. If the context runs its
    // servers under a service account, THAT identity needs the credential —
    // see the Managing Credentials guide's service-account caveat.
    computeContext: '',
    // Blank = every backend the runtime supports. Set to e.g. 'pgvector' on a
    // site that operates only one store, so the others never appear.
    enabledBackends: '',
  },
};

/** URL-overridable keys of the RAG Builder config. */
const URL_OVERRIDABLE = [
  'modelRepositoryID',
  'SCREndpoint',
  'deploymentType',
  'credentialDomain',
  'contentRoot',
  'casServer',
  'computeContext',
  'enabledBackends',
  'id',
] as const;

export function getRagConfig(): RagRuntimeConfig {
  const params = new URLSearchParams(window.location.search);

  const ragBuilder: RagBuilderConfig = { ...DEFAULTS.ragBuilder };
  for (const key of URL_OVERRIDABLE) {
    const value = params.get(key);
    if (value) {
      ragBuilder[key] = value;
    }
  }

  const viyaHostParam = params.get('viyaHost');
  const viyaHost =
    viyaHostParam && viyaHostParam.trim() ? viyaHostParam : DEFAULTS.viyaHost;

  return { viyaHost, ragBuilder };
}

/**
 * Whether the RAG Builder has what it needs to talk to SAS Viya. Until then
 * the app must NOT call Viya — it shows a "configure me" state.
 */
export function isRagConfigured(ragBuilder: RagBuilderConfig): boolean {
  return Boolean(ragBuilder.modelRepositoryID);
}
