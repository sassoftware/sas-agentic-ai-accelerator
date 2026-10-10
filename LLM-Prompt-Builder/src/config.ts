/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Configuration of the page: the prompt side (prompt templates for LLMs and
 * decision templates for decision models), the RAG side (the RAG Builder) and
 * the three switches that decide which of them exist.
 *
 * In the SAS Visual Analytics embed the report author configures everything
 * below from the object's Options pane: the app publishes ONE Data-Driven
 * Content options group (see `src/va/ddc.ts`) and VA mirrors the chosen values
 * into the iframe URL, which this module reads back as query parameters. The
 * values here are the build-time defaults / initial pane values:
 *
 *   ...?viyaHost=https://viya.example.com
 *      &modelRepositoryID=<uuid>
 *      &llmProjectID=<uuid>
 *      &SCREndpoint=https://viya.example.com/llm
 *      &deploymentType=k8s
 *      &enableRag=true&embeddingProjectID=<uuid>
 *
 * Both sides read the SAME URL. The options they share (repository, SCR
 * endpoint, deployment type, credential domain) are copied to both objects;
 * the one name that meant two things is split: `computeContext` is the
 * context of the prompt optimization job, `ragComputeContext` the context of
 * the RAG ingestion job (it lands in `ragBuilder.computeContext`).
 *
 * No secret appears here or in the URL. Provider API keys resolve at runtime
 * from the configured SAS Viya credential domain under the signed-in user —
 * see `src/api/credentials-api.ts` and the Managing Credentials guide.
 * Vector-store credentials are resolved server-side by the ingestion and
 * retrieval from that same domain (entries <BACKEND>_RAG_USER / _RAG_PW), and
 * where a store LIVES comes from it too (<BACKEND>_HOST / _PORT / _DB, falling
 * back to the unprefixed RAGSTORE_* names), so no one types a hostname into a
 * form.
 */

import { RAG_BACKENDS, backendOptionKey } from './objects/rag-backends';
import { featureFlag } from './util/flags';
import type { PromptBuilderConfig } from './types';
import type { RagBuilderConfig } from './types/rag';

/**
 * Which parts of the page exist. Parsed once, here, from the three switches
 * of the Options pane; everything else reads these booleans.
 */
export interface BuilderFeatures {
  /** Decision templates for decision models (option `enableDecision`). */
  decision: boolean;
  /** DSPy prompt optimization, the Optimize step (option `enableOptimization`). */
  optimization: boolean;
  /** RAG setups, the RAG Builder (option `enableRag`). */
  rag: boolean;
}

/**
 * Why RAG cannot be used although it is switched on, or null when nothing
 * stands in its way.
 *
 * `credentialDomainNone`: the credential domain option says `none`. The
 * prompt side reads that as "no credential lookup"; RAG has no such mode - the
 * ingestion and the retrieval take the store's user, password, host and
 * database from the domain.
 */
export type RagBlockedReason = 'credentialDomainNone' | null;

export interface RuntimeConfig {
  /** Base URL of the SAS Viya deployment used for API calls. */
  viyaHost: string;
  /** Configuration of the prompt side. */
  promptBuilder: PromptBuilderConfig;
  /** Configuration of the RAG side. */
  ragBuilder: RagBuilderConfig;
  features: BuilderFeatures;
  ragBlockedReason: RagBlockedReason;
}

/**
 * The credential domain of the create-credential-domain scripts and of
 * `mdb credentials-apply`. A domain that does not exist answers 404, which
 * resolves to no keys, so a default that is not there does no harm.
 */
const DEFAULT_CREDENTIAL_DOMAIN = 'agentic-ai-keys';

/**
 * The DOM id prefixes of the two builders. Fixed, not options: both builders
 * live in one document and derive every element id from their prefix, so two
 * equal prefixes would give two elements the same id.
 */
const PROMPT_BUILDER_ID = 'LPB';
const RAG_BUILDER_ID = 'RGB';

/**
 * Build-time defaults of the prompt side. In a SAS Visual Analytics embed the
 * app is served from the same origin as SAS Viya, so `window.location.origin`
 * is the correct host.
 */
const PROMPT_DEFAULTS: PromptBuilderConfig = {
  id: PROMPT_BUILDER_ID,
  name: 'LLM Prompt Builder',
  // These are environment-specific and MUST be supplied per deployment (via the
  // VA Options pane or URL parameters). They are intentionally left blank so the
  // app never calls SAS Viya with someone else's IDs — see isConfigured().
  // SAS Model Manager repository that new prompt projects are created in.
  modelRepositoryID: '',
  // SAS Model Manager project holding the available LLM definitions.
  llmProjectID: '',
  // SAS Container Runtime endpoint hosting the LLM containers.
  SCREndpoint: '',
  // 'k8s' (default) or 'aca' (Azure Container Apps).
  deploymentType: 'k8s',
  // Optional deployment default for the LLM-as-a-Judge model (an LLM name from
  // llmProjectID). Blank = no default; the in-app judge selector always wins.
  judgeModel: '',
  // Optional SAS Visual Analytics report URI (/reports/reports/<uuid>). When
  // set, the manifested best prompt embeds that report on its model card as
  // the custom chart (host = viyaHost). Blank = no chart attributes.
  modelCardReportURI: '',
  // DSPy prompt optimization. These three settings only matter while the
  // optimization switch is on (features.optimization).
  // Compute context the optimize job runs in (its Python needs dspy).
  computeContext: '',
  // SAS Content path of the deployed optimize Job Definition.
  optimizeJobProgram: '',
  // Minimum dataset rows before an optimization run is allowed.
  minOptimizeSamples: '30',
  // Credential domain: provider API keys are resolved from this single SAS
  // Viya credential domain under the identity of the signed-in user (the
  // credential's secrets map holds one entry per provider name), and models
  // without an entry are disabled with a note. The option value 'none'
  // disables credential lookups; getConfig() hands that on as a blank.
  credentialDomain: DEFAULT_CREDENTIAL_DOMAIN,
};

/** Build-time defaults of the RAG side. */
const RAG_DEFAULTS: RagBuilderConfig = {
  id: RAG_BUILDER_ID,
  name: 'RAG Builder',
  // Environment-specific and MUST be supplied per deployment — left blank so
  // the app never calls SAS Viya with someone else's IDs (see isRagConfigured).
  modelRepositoryID: '',
  // Model Manager project holding the registered embedding models. Blank =
  // the Builder cannot list them and falls back to a free-text model name.
  embeddingProjectID: '',
  // Blank = <viyaHost>/llm at runtime.
  SCREndpoint: '',
  deploymentType: 'k8s',
  credentialDomain: DEFAULT_CREDENTIAL_DOMAIN,
  // Where deploy-rag-content.ps1/.sh put the runtime.
  contentRoot: '/SAS Agentic AI Accelerator/RAG',
  casServer: 'cas-shared-default',
  // Compute context the ingestion job runs in (Job Execution _contextName);
  // blank = the Job Execution default context. If the context runs its
  // servers under a service account, THAT identity needs the credential —
  // see the Managing Credentials guide's service-account caveat.
  computeContext: '',
  // Admin-set operational policy. Users do not see these per setup; a setup
  // records the values it was created with.
  storeSslmode: 'prefer',
  deletedPolicy: 'retire',
  retainDays: '0',
  recordHistory: true,
  embedReplicas: '1',
  persistElements: '1',
  persistChunks: '1',
  // One enable flag per backend, generated from the single list so adding a
  // backend does not mean remembering to add a default here. A site that
  // operates only one store switches the others off, so they never appear.
  ...Object.fromEntries(
    RAG_BACKENDS.map((backend) => [backendOptionKey(backend), true])
  ),
};

/** URL parameters of the prompt side; each is the config key of the same name. */
const PROMPT_URL_PARAMETERS = [
  'modelRepositoryID',
  'llmProjectID',
  'SCREndpoint',
  'deploymentType',
  'judgeModel',
  'modelCardReportURI',
  'computeContext',
  'optimizeJobProgram',
  'minOptimizeSamples',
  'credentialDomain',
] as const;

/**
 * URL parameters of the RAG side, as [parameter, config key]. The two differ
 * for the ingestion compute context only: the parameter `computeContext`
 * belongs to the prompt optimization job, and the context that job needs is
 * known not to work for an ingestion, so the RAG side must never read it.
 */
const RAG_URL_PARAMETERS: ReadonlyArray<readonly [parameter: string, key: string]> = [
  ['modelRepositoryID', 'modelRepositoryID'],
  ['embeddingProjectID', 'embeddingProjectID'],
  ['SCREndpoint', 'SCREndpoint'],
  ['deploymentType', 'deploymentType'],
  ['credentialDomain', 'credentialDomain'],
  ['contentRoot', 'contentRoot'],
  ['casServer', 'casServer'],
  ['ragComputeContext', 'computeContext'],
  ['storeSslmode', 'storeSslmode'],
  ['deletedPolicy', 'deletedPolicy'],
  ['retainDays', 'retainDays'],
  ['recordHistory', 'recordHistory'],
  ['embedReplicas', 'embedReplicas'],
  ['persistElements', 'persistElements'],
  ['persistChunks', 'persistChunks'],
  ...RAG_BACKENDS.map((backend) => [backendOptionKey(backend), backendOptionKey(backend)] as const),
];

/**
 * Resolve the effective configuration by layering URL query parameters over
 * the build-time defaults.
 */
export function getConfig(): RuntimeConfig {
  const params = new URLSearchParams(window.location.search);
  // A blank parameter means "use the default", the same as an absent one: VA
  // mirrors every option into the URL, including the ones nobody filled in.
  const given = (parameter: string): string | null => {
    const value = params.get(parameter);
    return value && value.trim() ? value : null;
  };

  // Provider keys resolve from the credential domain while the prompt side is
  // built; API_KEYS is their in-memory store, one map per page load.
  const promptBuilder: PromptBuilderConfig = { ...PROMPT_DEFAULTS, API_KEYS: {} };
  for (const parameter of PROMPT_URL_PARAMETERS) {
    const value = given(parameter);
    if (value !== null) promptBuilder[parameter] = value;
  }

  const ragBuilder: RagBuilderConfig = { ...RAG_DEFAULTS };
  for (const [parameter, key] of RAG_URL_PARAMETERS) {
    const value = given(parameter);
    if (value !== null) ragBuilder[key] = value;
  }

  // The credential domain is one option with two readers. 'none' switches the
  // lookup off for the prompt side, which is handed a blank. The RAG side has
  // no such mode, so it keeps a real domain name and is reported as blocked
  // instead (see ragBlockedReason); it is then never built, so the name it
  // keeps is never looked up or saved.
  const credentialDomain = String(promptBuilder.credentialDomain ?? '').trim();
  const credentialLookupOff = credentialDomain.toLowerCase() === 'none';
  promptBuilder.credentialDomain = credentialLookupOff ? '' : credentialDomain;
  ragBuilder.credentialDomain = credentialLookupOff ? DEFAULT_CREDENTIAL_DOMAIN : credentialDomain;

  // The switches are read straight from the URL: an absent or unreadable
  // value is the switch's default, which is what a report saved before the
  // switch existed needs (decision templates were always on, RAG was not part
  // of this page).
  const features: BuilderFeatures = {
    decision: featureFlag(params.get('enableDecision'), true),
    optimization: featureFlag(params.get('enableOptimization'), false),
    rag: featureFlag(params.get('enableRag'), false),
  };

  // Treat a blank viyaHost parameter as "use the embedding origin".
  const viyaHost = given('viyaHost') ?? window.location.origin;

  return {
    viyaHost,
    promptBuilder,
    ragBuilder,
    features,
    ragBlockedReason: features.rag && credentialLookupOff ? 'credentialDomainNone' : null,
  };
}

/**
 * Whether the prompt side has the environment-specific settings it needs to
 * talk to SAS Viya. Until these are provided (via the VA Options pane or URL
 * parameters) it must NOT call Viya.
 */
export function isConfigured(promptBuilder: PromptBuilderConfig): boolean {
  return Boolean(
    promptBuilder.modelRepositoryID &&
      promptBuilder.llmProjectID &&
      promptBuilder.SCREndpoint
  );
}

/**
 * Whether the RAG side has what it needs to talk to SAS Viya. This looks at
 * its settings only; whether RAG is switched on (`features.rag`) and whether
 * something blocks it (`ragBlockedReason`) are separate questions.
 */
export function isRagConfigured(ragBuilder: RagBuilderConfig): boolean {
  return Boolean(ragBuilder.modelRepositoryID);
}
