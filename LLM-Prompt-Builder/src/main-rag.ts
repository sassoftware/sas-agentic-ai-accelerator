/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Entry point for the standalone RAG Builder (design §14).
 *
 * Like the Prompt Builder, this build targets a SAS Visual Analytics embed
 * served through SAS Job Execution: it runs inside an already-authenticated
 * SAS Viya session and relies on the ambient session cookies.
 */

import 'bootstrap/dist/css/bootstrap.min.css';
import 'bootstrap/js/dist/modal';
import 'bootstrap/js/dist/collapse';
import './styles.css';

import { getRagConfig, isRagConfigured } from './config-rag';
import { initAppState, getAppState } from './state/app-state';
import { getUserInfo } from './api/identity-api';
import { getInterfaceLanguage } from './i18n/i18n';
import { buildRagBuilder } from './objects/rag-builder';
import { initRagVaIntegration } from './va/ddc-rag';
import type { RagBuilderText } from './types/rag';

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

  const { viyaHost, ragBuilder } = getRagConfig();
  initAppState({ viyaHost });

  // Drive the VA Properties panel; a no-op outside a VA embed.
  initRagVaIntegration({ viyaHost, ragBuilder });

  const interfaceText = getInterfaceLanguage();
  const text = (interfaceText.ragBuilder ?? {}) as RagBuilderText;
  const str = (key: string, fallback: string): string => {
    const value = text[key];
    return typeof value === 'string' ? value : fallback;
  };

  if (!isRagConfigured(ragBuilder)) {
    showAlert(
      root,
      'info',
      str('ragBuilderConfigNeededHeading', 'Configuration required'),
      str(
        'ragBuilderConfigNeededMessage',
        "Set the SAS Viya host and Model Manager repository ID in this object's Options pane (or append them as URL parameters), then the RAG Builder will load."
      )
    );
    return;
  }

  try {
    const user = await getUserInfo();
    getAppState().userName = user?.id ?? null;
  } catch {
    getAppState().userName = null;
  }

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
    const element = await buildRagBuilder(ragBuilder, 'app', interfaceText);
    root.replaceChildren(element);
  } catch (error) {
    console.error('Failed to build the RAG Builder.', error);
    showAlert(
      root,
      'danger',
      str('ragBuilderLoadErrorHeading', 'Could not load the RAG Builder'),
      str(
        'ragBuilderLoadErrorMessage',
        'Check that the configured Model Manager repository exists and is reachable in this environment, and that you are signed in to SAS Viya.'
      )
    );
  }
}

main();
