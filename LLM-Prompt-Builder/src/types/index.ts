/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Shared types for the standalone LLM Prompt Builder.
 */

/**
 * Configuration for a single Prompt Builder instance. In the original SAS
 * Portal Framework these fields came from the portal object definition; here
 * they come from `src/config.ts` (build-time defaults, optionally overridden
 * by URL query parameters at runtime).
 */
export interface PromptBuilderConfig {
  /** DOM id prefix used to namespace generated elements. */
  id: string;
  /** Optional display name. */
  name?: string;
  /** SAS Model Manager repository ID new projects are created in. */
  modelRepositoryID: string;
  /** SAS Model Manager project ID that holds the available LLM definitions. */
  llmProjectID: string;
  /** Base URL of the SAS Container Runtime endpoint hosting the LLM containers. */
  SCREndpoint: string;
  /** LLM container deployment type — 'k8s' (default) or 'aca'. */
  deploymentType?: string;
  /**
   * Optional deployment default for the LLM-as-a-Judge model, given as an LLM
   * name from the `llmProjectID` project. Not a secret, so it is delivered via
   * the VA Options pane / URL like the other IDs. The in-app judge selector
   * always overrides it.
   */
  judgeModel?: string;
  /**
   * Optional SAS Visual Analytics report URI (the `/reports/reports/<uuid>`
   * path). When set, manifesting the best prompt embeds that report on the
   * model card as its custom chart (`modelCardCustomChartReport` +
   * `modelCardCustomChartEnabled`), using the configured `viyaHost` as the
   * report host — mirroring how mdb populates the same attributes.
   */
  modelCardReportURI?: string;
  /** Map of API-key name (as referenced by an LLM's options.json) to key value. */
  API_KEYS?: Record<string, string>;
  /**
   * Master toggle for DSPy prompt optimization ('true' enables it). When off
   * (the default) the Optimize section is hidden and none of the optimize
   * settings below are used.
   */
  enableOptimization?: string;
  /**
   * SAS Compute context the optimization job runs in, passed to Job Execution
   * as `_contextName`. Its Python environment must have `dspy` installed (see
   * the "Enabling Prompt Optimization" administration guide).
   */
  computeContext?: string;
  /**
   * SAS Content path of the deployed optimize Job Definition (the `_program`
   * the Builder launches), e.g. `/Public/Jobs/Optimize-Prompt-DSPy`.
   */
  optimizeJobProgram?: string;
  /** Minimum dataset rows before an optimization run is allowed (default 30). */
  minOptimizeSamples?: string;
  /**
   * Governed SAS library + table holding provider API keys (name → value) that
   * the optimization job reads. Only the names travel in the job request —
   * never the keys themselves.
   */
  optimizeKeyLibrary?: string;
  optimizeKeyTable?: string;
  /**
   * Credential domain provider API keys resolve from under the signed-in
   * user's identity (user credential overrides group credential; the
   * credential's secrets map holds one entry per provider name); models
   * without an entry are disabled with a note. Defaults to the
   * create-credential-domain.sas default (agentic-ai-keys) — a missing
   * domain 404s harmlessly and assigned-data keys still apply. 'none'
   * disables credential lookups entirely.
   */
  credentialDomain?: string;
  [key: string]: unknown;
}

/** Text for a create-project / create-prompt modal. */
export interface ModalText {
  modalTitle?: string;
  modalDescription?: string;
  nameLabel?: string;
  descriptionLabel?: string;
  closeButtonText?: string;
  saveButtonText?: string;
}

/** Interface text for the Prompt Builder — permissive to allow nested modal text. */
export interface PromptBuilderText {
  [key: string]: string | string[] | ModalText;
}

/** Root interface-text object loaded from a locale file. */
export interface InterfaceText {
  promptBuilder: PromptBuilderText;
  [key: string]: unknown;
}
