/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * The folders of the compute server for the folder picker of a RAG setup.
 *
 * SAS Viya lists a directory of the compute server through a compute
 * session: GET /compute/sessions/{id}/files/{path}/members, the path written
 * with `~fs~` in place of each slash. No other service lists that file
 * system - the Data Sources service lists compute contexts and libraries, CAS
 * lists the files of a caslib on its own server, which the ingestion job does
 * not read. Measured on SAS Viya 2026.09 (2026-10-24): a session took 13 s to
 * be created and 21 s to be idle, a listing then 0.14 to 0.28 s.
 *
 * So the session is started early and kept: `openFolderSession` starts it as
 * soon as the RAG part of the page is shown, in the compute context the
 * ingestion job runs in (option `ragComputeContext`) - a user is shown the
 * folders the ingestion can read, as that user. A ping every minute keeps it,
 * `closeFolderSession` deletes it when the page shows a prompt or decision
 * template again or is left (`pagehide`, sent with `keepalive`). A session
 * whose page stops pinging - a closed laptop, a lost network, a page that
 * crashed - ends by itself five minutes after the last ping: it is created
 * with the attribute sessionInactiveTimeout, and a GET of its state counts as
 * activity (both measured on 2026-10-24: a session of 60 s that was not
 * pinged was gone after 101 s, one pinged every 20 s was idle).
 */

import { viyaFetch, viyaGetAll, ViyaRefused } from './http-client';
import { oneAtATime } from '../util/one-at-a-time';

/** The name the session has in SAS Environment Manager. */
const SESSION_NAME = 'Prompt Builder - RAG folder picker';
/** Seconds of no request after which SAS Viya ends the session by itself. */
export const SESSION_IDLE_SECONDS = 300;
/** How often the page tells SAS Viya that the session is still wanted. */
export const PING_INTERVAL_MS = 60_000;
/** How long a new session may take to be ready. */
const READY_WITHIN_MS = 120_000;

/** Why the folders of the compute server could not be listed. */
export class FolderSessionError extends Error {
  constructor(
    readonly reason: 'noContext' | 'unknownContext' | 'notStarted' | 'unavailable',
    detail = ''
  ) {
    super(detail || reason);
  }
}

interface ComputeFile {
  name?: string;
  isDirectory?: boolean;
}

interface FolderSession {
  context: string;
  /** The id of the session, once it is ready. */
  id: Promise<string>;
  /** The id once known, for the DELETE at the end. */
  known: string | null;
  ping: number | undefined;
  stopped: boolean;
}

let current: FolderSession | null = null;
let exitHooked = false;

/**
 * A path of the compute server as the files API takes it: `/data/my docs`
 * is `~fs~data~fs~my%20docs`, `/` is `~fs~`.
 */
export function computeFilePath(path: string): string {
  const segments = path.split('/').filter((segment) => segment !== '');
  return segments.length === 0 ? '~fs~' : segments.map((segment) => `~fs~${encodeURIComponent(segment)}`).join('');
}

async function contextId(contextName: string): Promise<string> {
  const filter = encodeURIComponent(`eq(name,'${contextName.replace(/'/g, "''")}')`);
  const found = await viyaGetAll<{ id?: string; name?: string }>(`/compute/contexts?filter=${filter}&limit=10`, { upTo: 10 });
  const context = found.find((item) => item.name === contextName && item.id);
  if (!context?.id) throw new FolderSessionError('unknownContext', contextName);
  return context.id;
}

async function startSession(contextName: string, session: FolderSession): Promise<string> {
  const id = await contextId(contextName);
  const response = await viyaFetch(`/compute/contexts/${encodeURIComponent(id)}/sessions`, {
    method: 'POST',
    body: JSON.stringify({ name: SESSION_NAME, attributes: { sessionInactiveTimeout: SESSION_IDLE_SECONDS } }),
    contentType: 'application/json',
  });
  if (!response.ok) throw new FolderSessionError('notStarted', `HTTP ${response.status}`);
  const created = (await response.json()) as { id?: string };
  if (!created.id) throw new FolderSessionError('notStarted', 'no session id');
  session.known = created.id;
  // The state answers 404 for a moment after the creation (measured), and
  // 'pending' while the server starts.
  const until = Date.now() + READY_WITHIN_MS;
  for (;;) {
    if (session.stopped) throw new FolderSessionError('notStarted', 'closed');
    const state = await viyaFetch(`/compute/sessions/${encodeURIComponent(created.id)}/state`, { accept: 'text/plain' });
    const text = state.ok ? (await state.text()).trim() : '';
    if (text === 'idle' || text === 'running') return created.id;
    if (text === 'error' || text === 'failed' || Date.now() > until) {
      throw new FolderSessionError('notStarted', text || `HTTP ${state.status}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
}

function deleteSession(id: string, leaving = false): void {
  void viyaFetch(`/compute/sessions/${encodeURIComponent(id)}`, { method: 'DELETE', keepalive: leaving }).catch(() => undefined);
}

/**
 * Start the session of the folder picker in `contextName`, unless one of
 * that context is there already. Nothing is awaited: the session is ready
 * when the picker asks for its first folder, or the picker waits for it.
 */
export function openFolderSession(contextName: string): void {
  const name = contextName.trim();
  if (!name) return;
  if (current && !current.stopped && current.context === name) return;
  closeFolderSession();
  const session: FolderSession = { context: name, id: Promise.resolve(''), known: null, ping: undefined, stopped: false };
  session.id = startSession(name, session);
  session.id.then(
    () => {
      if (session.stopped) return;
      const ping = oneAtATime(async () => {
        const state = await viyaFetch(`/compute/sessions/${encodeURIComponent(session.known ?? '')}/state`, {
          accept: 'text/plain',
        }).catch(() => null);
        // A session that is gone - ended by SAS Viya, deleted elsewhere - is
        // started again by the next listing.
        if (state?.status === 404 && current === session) closeFolderSession();
      });
      session.ping = window.setInterval(() => void ping(), PING_INTERVAL_MS);
    },
    () => {
      if (current === session) current = null;
    }
  );
  current = session;
  if (!exitHooked) {
    exitHooked = true;
    window.addEventListener('pagehide', () => closeFolderSession(true));
  }
}

/** Delete the session of the folder picker, if there is one. */
export function closeFolderSession(leaving = false): void {
  const session = current;
  current = null;
  if (!session) return;
  session.stopped = true;
  if (session.ping !== undefined) window.clearInterval(session.ping);
  if (session.known) deleteSession(session.known, leaving);
  else void session.id.then((id) => deleteSession(id), () => undefined);
}

/**
 * The folders directly below `path` on the compute server, by name, as the
 * user of the session may list them. A folder that is not there, or one the
 * user may not read, is `unavailable` (SAS Viya answers 404 for both).
 */
export async function listServerFolders(contextName: string, path: string, again = true): Promise<string[]> {
  if (!contextName.trim()) throw new FolderSessionError('noContext');
  openFolderSession(contextName);
  const session = current!;
  const id = await session.id;
  let items: ComputeFile[];
  try {
    items = await viyaGetAll<ComputeFile>(
      `/compute/sessions/${encodeURIComponent(id)}/files/${computeFilePath(path)}/members?limit=1000`
    );
  } catch (error) {
    if (error instanceof ViyaRefused && error.status === 404) {
      // the session, or the folder - a session that is gone is started again
      const alive = await viyaFetch(`/compute/sessions/${encodeURIComponent(id)}/state`, { accept: 'text/plain' });
      if (alive.status === 404 && again) {
        if (current === session) closeFolderSession();
        return listServerFolders(contextName, path, false);
      }
      throw new FolderSessionError('unavailable', path);
    }
    throw error;
  }
  return items
    .filter((item) => item.isDirectory === true && typeof item.name === 'string' && item.name !== '')
    .map((item) => item.name as string)
    .sort((a, b) => a.localeCompare(b));
}
