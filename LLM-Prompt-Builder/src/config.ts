/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Standalone configuration for the LLM Prompt Builder.
 *
 * In the SAS Visual Analytics embed the report author configures everything
 * below (except the API key) from the object's Properties panel: the app
 * publishes a DDC "options group" (see `src/va/ddc.ts`) and VA mirrors the
 * chosen values into the iframe URL, which this module reads back as query
 * parameters. The values here are the build-time defaults / initial panel
 * values:
 *
 *   ...?viyaHost=https://viya.example.com
 *      &modelRepositoryID=<uuid>
 *      &llmProjectID=<uuid>
 *      &SCREndpoint=https://viya.example.com/llm
 *      &deploymentType=k8s
 *
 * The API key(s) are intentionally NOT set here and are NOT URL-overridable (a
 * secret must not appear in a shareable link). They are supplied at runtime via
 * the object's assigned DDC data table — see `src/va/ddc.ts` and the README.
 */

import type { PromptBuilderConfig } from './types';

export interface RuntimeConfig {
  /** Base URL of the SAS Viya deployment used for Model Manager API calls. */
  viyaHost: string;
  /** Prompt Builder instance configuration. */
  promptBuilder: PromptBuilderConfig;
}

/**
 * Build-time defaults. In a SAS Visual Analytics embed the app is served from
 * the same origin as SAS Viya, so `window.location.origin` is the correct host.
 */
const DEFAULTS: RuntimeConfig = {
  viyaHost: window.location.origin,
  promptBuilder: {
    id: 'LPB',
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
    // API keys are supplied at runtime from the object's assigned DDC data table
    // (see src/va/ddc.ts), so they start empty here. Map is keyed by the name an
    // LLM's options.json references via API_KEY.default (e.g. "Anthropic").
    API_KEYS: {},
  },
};

/** URL-overridable keys of the Prompt Builder config (excludes API_KEYS). */
const URL_OVERRIDABLE = [
  'modelRepositoryID',
  'llmProjectID',
  'SCREndpoint',
  'deploymentType',
  'judgeModel',
  'modelCardReportURI',
  'id',
] as const;

/**
 * Resolve the effective configuration by layering URL query parameters over the
 * build-time defaults.
 */
export function getConfig(): RuntimeConfig {
  const params = new URLSearchParams(window.location.search);

  const promptBuilder: PromptBuilderConfig = { ...DEFAULTS.promptBuilder };
  for (const key of URL_OVERRIDABLE) {
    const value = params.get(key);
    if (value) {
      promptBuilder[key] = value;
    }
  }

  // Treat a blank viyaHost parameter as "use the embedding origin".
  const viyaHostParam = params.get('viyaHost');
  const viyaHost =
    viyaHostParam && viyaHostParam.trim() ? viyaHostParam : DEFAULTS.viyaHost;

  return { viyaHost, promptBuilder };
}

/**
 * Whether the Prompt Builder has the environment-specific settings it needs to
 * talk to SAS Viya. Until these are provided (via the VA Options pane or URL
 * parameters) the app must NOT call Viya — it shows a "configure me" state.
 */
export function isConfigured(promptBuilder: PromptBuilderConfig): boolean {
  return Boolean(
    promptBuilder.modelRepositoryID &&
      promptBuilder.llmProjectID &&
      promptBuilder.SCREndpoint
  );
}
