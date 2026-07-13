/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Minimal global application state. In the SAS Visual Analytics embed the app
 * runs inside an authenticated SAS Viya session, so there is no login flow —
 * only the resolved Viya host, a CSRF token cache, and the current user id.
 */

export interface AppConfig {
  /** Base URL of the SAS Viya deployment (usually window.location.origin). */
  viyaHost: string;
}

export interface AppState {
  config: AppConfig;
  csrfToken: string | null;
  userName: string | null;
}

let state: AppState | null = null;

export function initAppState(config: AppConfig): void {
  state = {
    config,
    csrfToken: null,
    userName: null,
  };
}

export function getAppState(): AppState {
  if (!state) {
    throw new Error('AppState not initialized. Call initAppState() first.');
  }
  return state;
}
