/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Centralized HTTP client for SAS Viya API calls.
 * Handles CSRF token management, automatic retry on 403/CSRF errors and the
 * first request to a service the browser has no session with yet (HTTP 449).
 */

import { getAppState } from '../state/app-state';

export interface ViyaFetchOptions {
  method?: string;
  headers?: Record<string, string>;
  body?: string | FormData | Blob;
  accept?: string;
  contentType?: string;
}

// The session warm-ups that are under way, by service (the first path
// segment). Requests sent in parallel all get their 449 before the first of
// them has established the session, and then share its warm-up. An entry is
// dropped once its request has ended, so a 449 that comes later warms up
// again.
const sessionWarmUps = new Map<string, Promise<void>>();

/**
 * Establish the browser's session with the service a path belongs to.
 *
 * Every SAS Viya service keeps a session of its own. The first request to a
 * service the browser has not met yet has to go through the SAS Logon
 * redirect, and a request that carries `X-Requested-With: XMLHttpRequest` is
 * not redirected: it is answered with HTTP 449 - seen live on
 * POST /relationships/relationships, where the delete dialog then read
 * "Decision usage could not be verified" for a prompt a decision uses. A plain
 * GET without that header follows the redirect and gets the session cookie;
 * the request that got the 449 is answered once it is sent again.
 *
 * The answer of the GET is of no interest, and neither is a network error: if
 * the session was not established, the repeated request says so.
 */
function establishServiceSession(viyaHost: string, path: string): Promise<void> {
  const service = /^\/([^/?#]+)/.exec(path)?.[1];
  if (!service) return Promise.resolve();
  let warmUp = sessionWarmUps.get(service);
  if (!warmUp) {
    warmUp = fetch(`${viyaHost}/${service}/`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      credentials: 'include',
      redirect: 'follow',
    })
      .then(
        () => undefined,
        () => undefined
      )
      .finally(() => sessionWarmUps.delete(service));
    sessionWarmUps.set(service, warmUp);
  }
  return warmUp;
}

/**
 * Core fetch wrapper that prepends the Viya host, includes credentials,
 * attaches CSRF tokens, establishes the service session after an HTTP 449 and
 * retries once on CSRF 403 errors.
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

  // No session with this service yet: establish it and send the request
  // again, once. A second 449 goes back to the caller as it is. This comes
  // before the CSRF handling, because the repeated request is the first one
  // the service really answers - and that answer may be the CSRF 403.
  if (response.status === 449) {
    await establishServiceSession(state.config.viyaHost, path);
    response = await fetch(url, fetchOptions);
  }

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
