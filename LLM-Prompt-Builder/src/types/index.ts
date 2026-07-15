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
  /** Map of API-key name (as referenced by an LLM's options.json) to key value. */
  API_KEYS?: Record<string, string>;
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
