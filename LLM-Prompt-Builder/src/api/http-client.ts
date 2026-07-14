/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Centralized HTTP client for SAS Viya API calls.
 * Handles CSRF token management and automatic retry on 403/CSRF errors.
 */

import { getAppState } from '../state/app-state';

export interface ViyaFetchOptions {
  method?: string;
  headers?: Record<string, string>;
  body?: string | FormData | Blob;
  accept?: string;
  contentType?: string;
}

/**
 * Core fetch wrapper that prepends the Viya host, includes credentials,
 * attaches CSRF tokens, and retries once on CSRF 403 errors.
 */
export async function viyaFetch(
  path: string,
  options: ViyaFetchOptions = {}
): Promise<Response> {
  const state = getAppState();
  const url = `${state.config.viyaHost}${path}`;

  const headers: Record<string, string> = {
    'X-Requested-With': 'XMLHttpRequest',
    Accept: options.accept ?? 'application/json',
    ...options.headers,
  };

  if (options.contentType !== undefined) {
    headers['Content-Type'] = options.contentType;
  } else if (options.body && typeof options.body === 'string') {
    headers['Content-Type'] = 'application/json';
  }

  if (state.csrfToken) {
    headers['X-CSRF-TOKEN'] = state.csrfToken;
  }

  const fetchOptions: RequestInit = {
    method: options.method ?? 'GET',
    headers,
    credentials: 'include',
    redirect: 'follow',
  };

  if (options.body !== undefined) {
    fetchOptions.body = options.body;
  }

  let response = await fetch(url, fetchOptions);

  // Handle CSRF token refresh
  if (
    !response.ok &&
    response.status === 403 &&
    response.headers.get('x-forbidden-reason') === 'CSRF'
  ) {
    const newToken = response.headers.get('x-csrf-token');
    if (newToken) {
      state.csrfToken = newToken;
      headers['X-CSRF-TOKEN'] = newToken;
      response = await fetch(url, { ...fetchOptions, headers });
    }
  }

  return response;
}

/**
 * Parse a JSON response, throwing on a non-OK status so callers never read
 * fields off an error body (which previously surfaced as `undefined` ids that
 * poisoned later requests / persisted state).
 */
async function parseJsonResponse<T>(
  response: Response,
  path: string
): Promise<T> {
  if (!response.ok) {
    throw new Error(
      `Viya request to ${path} failed with HTTP ${response.status}`
    );
  }
  return response.json() as Promise<T>;
}

/**
 * GET request that returns parsed JSON.
 */
export async function viyaGet<T>(
  path: string,
  accept?: string
): Promise<T> {
  const response = await viyaFetch(path, { accept });
  return parseJsonResponse<T>(response, path);
}

/**
 * GET request that returns the raw Response (for file content, etc.)
 */
export async function viyaGetRaw(
  path: string,
  accept?: string
): Promise<Response> {
  return viyaFetch(path, { accept });
}

/**
 * POST request with JSON body that returns parsed JSON.
 */
export async function viyaPost<T>(
  path: string,
  body: unknown,
  contentType?: string
): Promise<T> {
  const response = await viyaFetch(path, {
    method: 'POST',
    body: JSON.stringify(body),
    contentType,
  });
  return parseJsonResponse<T>(response, path);
}

/**
 * POST request that returns the raw Response.
 */
export async function viyaPostRaw(
  path: string,
  body: unknown,
  contentType?: string
): Promise<Response> {
  return viyaFetch(path, {
    method: 'POST',
    body: JSON.stringify(body),
    contentType,
  });
}

/**
 * PUT request with JSON body that returns parsed JSON.
 */
export async function viyaPut<T>(
  path: string,
  body: unknown,
  contentType?: string,
  additionalHeaders?: Record<string, string>
): Promise<T> {
  const response = await viyaFetch(path, {
    method: 'PUT',
    body: JSON.stringify(body),
    contentType,
    headers: additionalHeaders,
  });
  return parseJsonResponse<T>(response, path);
}

/**
 * DELETE request.
 */
export async function viyaDelete(path: string): Promise<Response> {
  return viyaFetch(path, { method: 'DELETE' });
}

/**
 * PATCH request with JSON body.
 */
export async function viyaPatch<T>(
  path: string,
  body: unknown,
  contentType?: string,
  additionalHeaders?: Record<string, string>
): Promise<T> {
  const response = await viyaFetch(path, {
    method: 'PATCH',
    body: JSON.stringify(body),
    contentType,
    headers: additionalHeaders,
  });
  return parseJsonResponse<T>(response, path);
}
