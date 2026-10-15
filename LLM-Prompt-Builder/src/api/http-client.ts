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

/**
 * How long a request to SAS Viya may stay without an answer: then it is
 * given up and fails as a request without an answer does. Without a bound a
 * request that never got one held what waited for it for good - a write kept
 * the selection of its builder locked (see createSelectionLock) until the
 * page was reloaded. Two minutes is far beyond what a request of this page
 * takes; the calls to the model containers do not come through here.
 */
const REQUEST_TIMEOUT_MS = 120_000;

/** `fetch`, given up after REQUEST_TIMEOUT_MS - also while the body is read. */
function fetchWithin(url: string, init: RequestInit): Promise<Response> {
  const controller = new AbortController();
  window.setTimeout(() => controller.abort(new Error(`no answer within ${REQUEST_TIMEOUT_MS / 1000} s`)), REQUEST_TIMEOUT_MS);
  return fetch(url, { ...init, signal: controller.signal });
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
    warmUp = fetchWithin(`${viyaHost}/${service}/`, {
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

  let response = await fetchWithin(url, fetchOptions);

  // No session with this service yet: establish it and send the request
  // again, once. A second 449 goes back to the caller as it is. This comes
  // before the CSRF handling, because the repeated request is the first one
  // the service really answers - and that answer may be the CSRF 403.
  if (response.status === 449) {
    await establishServiceSession(state.config.viyaHost, path);
    response = await fetchWithin(url, fetchOptions);
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
      response = await fetchWithin(url, { ...fetchOptions, headers });
    }
  }

  return response;
}

/**
 * A request SAS Viya answered with a status that is no success: a refusal,
 * which is an answer - unlike a request that got none.
 */
export class ViyaRefused extends Error {
  constructor(
    path: string,
    readonly status: number,
    message = `Viya request to ${path} failed with HTTP ${status}`
  ) {
    super(message);
  }
}

/**
 * A write that got no usable answer - no answer at all, or one that could not
 * be read - after what SAS Viya holds was read back. `done` is false when the
 * read back shows that the write was not carried out, and null when it could
 * not be read back either: then it may have been carried out or not.
 */
export class UnansweredWrite extends Error {
  /**
   * `jobNotRunning`: the write was a launch, the jobs that are running were
   * read back, and the job of the launch is not among them - which leaves
   * it unknown too (it may have run and ended), for another reason than a
   * read back that failed.
   */
  constructor(
    readonly done: false | null,
    readonly lost: unknown,
    readonly jobNotRunning = false
  ) {
    super(
      done === false
        ? 'A write got no answer, and reading back shows it was not carried out.'
        : jobNotRunning
          ? 'A launch got no answer, and its job is not among the jobs that are running.'
          : 'A write got no answer, and what is stored could not be read back.'
    );
  }
}

/**
 * The statuses a gateway answers in place of a service that gave none (bad
 * gateway, unavailable, timed out): what was sent may have been carried out
 * all the same.
 */
const NO_ANSWER_STATUSES = [502, 503, 504];

/**
 * The status of the answer to a write, where it is one of the service; one a
 * gateway answered in its place throws, so that writeOrReadBack reads back.
 */
export function statusOf(response: Response, path: string): number {
  if (NO_ANSWER_STATUSES.includes(response.status)) throw new ViyaRefused(path, response.status);
  return response.status;
}

/**
 * Send a write, and when it gets no usable answer, read back what is stored.
 *
 * A write whose answer was lost may have been carried out: SAS Viya stored
 * the file, deleted the model, created the project, and the page said that
 * it had failed and went on showing what was there before. `readBack` reads
 * what the write was to change and answers what the write answers when it
 * was carried out, or undefined when it was not; a read back that fails
 * leaves it unknown. A request without an answer, an answer that cannot be
 * read and a status a gateway answers with in place of the service
 * (NO_ANSWER_STATUSES) are read back; a refusal of the service (ViyaRefused
 * with any other status) is an answer and is not.
 */
export async function writeOrReadBack<T>(
  write: () => Promise<T>,
  readBack: () => Promise<T | undefined>
): Promise<T> {
  try {
    return await write();
  } catch (lost) {
    if (lost instanceof ViyaRefused && !NO_ANSWER_STATUSES.includes(lost.status)) throw lost;
    let found: T | undefined;
    try {
      found = await readBack();
    } catch {
      throw new UnansweredWrite(null, lost);
    }
    if (found === undefined) throw new UnansweredWrite(false, lost);
    return found;
  }
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
  if (!response.ok) throw new ViyaRefused(path, response.status);
  return response.json() as Promise<T>;
}

/** One page of a collection of SAS Viya, as the services answer it. */
export interface CollectionPage<T> {
  items?: T[];
  count?: number;
  limit?: number;
  links?: Array<{ rel?: string }>;
}

/**
 * Every item of a collection: the page `path` asks for and every page after
 * it.
 *
 * The services answer a listing a page at a time (`limit=` asks for the
 * size of a page), and a caller that took the first page for the whole
 * listing took a prompt-test whose tracker was beyond its first 100
 * contents for one without a tracker. The next page is asked for while the page that came
 * says there is one: a link `next`, a `count` beyond the items read, or -
 * with neither - a page as full as its limit. A page that cannot be read
 * fails the whole listing, so a part is never taken for the whole.
 *
 * `path` carries its page size as `limit=` and no `start=`. `upTo` ends the
 * reading once that many items are in. `read` reads one page; a query sent
 * with POST brings its own.
 */
export async function viyaGetAll<T>(
  path: string,
  options: { upTo?: number; read?: (pagePath: string) => Promise<CollectionPage<T>> } = {}
): Promise<T[]> {
  const read = options.read ?? ((pagePath: string) => viyaGet<CollectionPage<T>>(pagePath));
  const upTo = options.upTo ?? Infinity;
  const asked = Number(/[?&]limit=(\d+)/.exec(path)?.[1]);
  const items: T[] = [];
  for (;;) {
    const page = await read(`${path}${path.includes('?') ? '&' : '?'}start=${items.length}`);
    const got = Array.isArray(page?.items) ? page.items : [];
    items.push(...got);
    if (got.length === 0 || items.length >= upTo) break;
    const next = Array.isArray(page.links) && page.links.some((link) => link?.rel === 'next');
    const count = typeof page.count === 'number' && page.count >= 0 ? page.count : null;
    const limit = typeof page.limit === 'number' && page.limit > 0 ? page.limit : asked;
    if (!(next || (count !== null ? items.length < count : got.length >= limit))) break;
  }
  return items.slice(0, upTo);
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
