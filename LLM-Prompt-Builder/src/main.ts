/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Entry point for the standalone LLM Prompt Builder.
 *
 * This build targets a SAS Visual Analytics embed (served through SAS Job
 * Execution). It runs inside an already-authenticated SAS Viya session and
 * relies on the ambient session cookies — there is no login flow and no
 * dependency on the SAS Auth Browser SDK.
 */

// Bundle Bootstrap CSS + the two components the Prompt Builder uses. Importing
// the component modules also registers their data-api (data-bs-toggle) click
// handlers, so accordions and modals work declaratively.
import 'bootstrap/dist/css/bootstrap.min.css';
import 'bootstrap/js/dist/modal';
import 'bootstrap/js/dist/collapse';
import './styles.css';

import { getConfig, isConfigured } from './config';
import { initAppState, getAppState } from './state/app-state';
import { getUserInfo } from './api/identity-api';
import { getInterfaceLanguage } from './i18n/i18n';
import { buildPromptBuilder } from './objects/prompt-builder';
import { initVaIntegration } from './va/ddc';
import type { PromptBuilderText } from './types';

/** Render a Bootstrap alert (heading + message) as the whole app body. */
function showAlert(
  root: HTMLElement,
  variant: 'info' | 'danger',
  heading: string,
  message: string
): void {
  const box = document.createElement('div');
  box.className = `alert alert-${variant} m-3`;
  box.setAttribute('role', 'alert');

  const title = document.createElement('h4');
  title.className = 'alert-heading';
  title.textContent = heading;

  const body = document.createElement('p');
  body.className = 'mb-0';
  body.textContent = message;

  box.appendChild(title);
  box.appendChild(body);
  root.replaceChildren(box);
}

async function main(): Promise<void> {
  const root = document.getElementById('app');
  if (!root) return;

  const { viyaHost, promptBuilder } = getConfig();
  initAppState({ viyaHost });

  // Provider keys resolve from the configured credential domain during
  // buildPromptBuilder; this map is their in-memory store.
  promptBuilder.API_KEYS = promptBuilder.API_KEYS ?? {};

  const interfaceText = getInterfaceLanguage();
  const text = (interfaceText.promptBuilder ?? {}) as PromptBuilderText;
  const str = (key: string, fallback: string): string => {
    const value = text[key];
    return typeof value === 'string' ? value : fallback;
  };

  // Drive the VA Properties panel; a no-op outside a VA embed.
  initVaIntegration({ viyaHost, promptBuilder });

  // Do NOT call SAS Viya until the object has been configured. When the author
  // sets the values in the Options pane, VA mirrors them into the iframe URL and
  // reloads us, so this runs again with the configuration present.
  if (!isConfigured(promptBuilder)) {
    showAlert(
      root,
      'info',
      str('promptBuilderConfigNeededHeading', 'Configuration required'),
      str(
        'promptBuilderConfigNeededMessage',
        "Set the SAS Viya host, Model Manager repository ID, LLM project ID, and SCR endpoint in this object's Options pane (or append them as URL parameters), then the Prompt Builder will load."
      )
    );
    return;
  }

  // Best-effort: stamp the current user on newly created prompts.
  try {
    const user = await getUserInfo();
    getAppState().userName = user?.id ?? null;
  } catch {
    getAppState().userName = null;
  }

  // Show a loading indicator while the builder fetches its metadata
  const loadingIndicator = document.createElement('div');
  loadingIndicator.className = 'd-flex justify-content-center p-5';
  const loadingSpinner = document.createElement('div');
  loadingSpinner.className = 'spinner-border';
  loadingSpinner.style.color = 'var(--pb-primary)';
  loadingSpinner.setAttribute('role', 'status');
  const loadingLabel = document.createElement('span');
  loadingLabel.className = 'visually-hidden';
  loadingLabel.textContent = 'Loading...';
  loadingSpinner.appendChild(loadingLabel);
  loadingIndicator.appendChild(loadingSpinner);
  root.replaceChildren(loadingIndicator);

  try {
    const element = await buildPromptBuilder(promptBuilder, 'app', interfaceText);
    root.replaceChildren(element);
  } catch (error) {
    console.error('Failed to build the Prompt Builder.', error);
    showAlert(
      root,
      'danger',
      str('promptBuilderLoadErrorHeading', 'Could not load the Prompt Builder'),
      str(
        'promptBuilderLoadErrorMessage',
        'Check that the configured Model Manager repository, LLM project, and SCR endpoint exist and are reachable in this environment, and that you are signed in to SAS Viya.'
      )
    );
  }
}

main();
