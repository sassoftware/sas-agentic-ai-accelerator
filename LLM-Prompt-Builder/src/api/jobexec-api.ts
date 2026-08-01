/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * SAS Job Execution client for the prompt-optimization job.
 *
 * The Optimize action launches a deployed Job Definition (a `.sas` program
 * imported into SAS Content) through the Job Execution REST API:
 *   1. resolve the definition's Content path to its /jobDefinitions URI
 *      (GET /folders/folders/@item?path=...),
 *   2. POST /jobExecution/jobs with that URI and the run arguments — including
 *      `_contextName`, which selects the Compute context the job runs in,
 *   3. poll GET /jobExecution/jobs/{id} until a terminal state,
 *   4. read the job log and surface the `NOTE: Python-Subprocess - ...` lines
 *      the job emits via SAS.logMessage() as live progress.
 *
 * Secrets never travel in the request: the job resolves provider keys
 * server-side from the credential domain under the launching user's identity.
 */

import { viyaGet, viyaFetch, viyaPost } from './http-client';

/** Job Execution job states (subset we care about). */
export type JobState =
  | 'pending'
  | 'running'
  | 'completed'
  | 'completedWithWarnings'
  | 'failed'
  | 'canceled'
  | 'timedOut'
  | string;

export interface JobExecutionJob {
  id: string;
  state: JobState;
  /** File-service URI of the job log (e.g. /files/files/<id>), when available. */
  logLocation?: string;
  /** Error details for a failed job, when the service provides them. */
  error?: { message?: string; details?: unknown } | null;
  results?: Record<string, unknown>;
  [key: string]: unknown;
}

/** True when the job will make no further progress. */
export function isTerminalJobState(state: JobState): boolean {
  return ['completed', 'completedWithWarnings', 'failed', 'canceled', 'timedOut'].includes(state);
}

interface FolderItem {
  id?: string;
  uri?: string;
  [key: string]: unknown;
}

interface FolderMember {
  name?: string;
  uri?: string;
  contentType?: string;
  [key: string]: unknown;
}

/**
 * Resolve a SAS Content path (e.g. /Public/Jobs/Optimize-Prompt-DSPy) to the
 * job definition URI the Job Execution service accepts. Throws when the path
 * does not exist or is not a job definition.
 *
 * `/folders/folders/@item` resolves FOLDERS but returns 404 for jobDefinition
 * members (verified against a live SAS Viya), so this resolves the parent
 * folder by path and then finds the definition among its members by name.
 */
export async function resolveJobDefinitionUri(programPath: string): Promise<string> {
  const clean = programPath.trim().replace(/\/+$/, '');
  const slash = clean.lastIndexOf('/');
  const name = clean.slice(slash + 1);
  const folderPath = slash > 0 ? clean.slice(0, slash) : '/';
  if (!name) {
    throw new Error(`The path ${programPath} is not a Job Execution job definition.`);
  }
  const folder = await viyaGet<FolderItem>(
    `/folders/folders/@item?path=${encodeURIComponent(folderPath)}`
  );
  const folderId = String(folder?.id ?? '');
  if (!folderId) {
    throw new Error(`The folder ${folderPath} was not found.`);
  }
  const members = await viyaGet<{ items?: FolderMember[] }>(
    `/folders/folders/${folderId}/members?filter=${encodeURIComponent(`eq(name,'${name}')`)}&limit=20`
  );
  const member = (members.items ?? []).find(
    (candidate) =>
      candidate.contentType === 'jobDefinition' &&
      String(candidate.uri ?? '').startsWith('/jobDefinitions/definitions/')
  );
  if (!member) {
    throw new Error(`The path ${programPath} is not a Job Execution job definition.`);
  }
  return String(member.uri);
}

let jobExecutionSessionPrimed = false;

/**
 * The FIRST browser request to the Job Execution service must be a GET: with
 * cookie auth, a first-contact POST triggers the SSO handshake (303 to
 * SASLogon, then a retry at `?sso_retry=POST` that answers HTTP 449) because
 * the redirect cannot replay the POST body — seen live. A cheap GET completes
 * the handshake and establishes the service session, after which POSTs work
 * (a CSRF 403 on the first POST is already handled by viyaFetch's retry).
 */
async function primeJobExecutionSession(): Promise<void> {
  if (jobExecutionSessionPrimed) return;
  await viyaFetch('/jobExecution/jobs?limit=1');
  jobExecutionSessionPrimed = true;
}

/**
 * Launch a job definition asynchronously with the given arguments (all values
 * are sent as strings, mirroring how Job Execution passes request parameters
 * to the SAS program as macro variables).
 */
export async function launchJob(
  jobDefinitionUri: string,
  jobName: string,
  args: Record<string, string>
): Promise<JobExecutionJob> {
  await primeJobExecutionSession();
  return viyaPost<JobExecutionJob>(
    '/jobExecution/jobs',
    { name: jobName, jobDefinitionUri, arguments: args },
    'application/vnd.sas.job.execution.job.request+json'
  );
}

/** Fetch a job's current state. */
export async function getJob(jobId: string): Promise<JobExecutionJob> {
  return viyaGet<JobExecutionJob>(`/jobExecution/jobs/${jobId}`);
}

/**
 * Read the job's log and return the progress messages the job emitted with
 * SAS.logMessage() — they land in the log as `NOTE: Python-Subprocess - ...`.
 *
 * While the job RUNS its log streams live from the COMPUTE session (whose ids
 * Job Execution exposes in the job's `results` as COMPUTE_SESSION and
 * COMPUTE_JOB); the Files-service `logLocation` only fills in at completion —
 * and both content endpoints paginate at 100 lines by default, so everything
 * is requested explicitly. The compute session is deleted after the job ends,
 * so the file is the fallback. Returns [] when neither log is readable.
 */
let computeServicePrimed = false;

/** Same first-contact SSO consideration as the Job Execution service: make
 *  sure the browser session has met the compute service via a harmless GET
 *  before the live-log polling relies on it. Best effort. */
async function primeComputeService(): Promise<void> {
  if (computeServicePrimed) return;
  try {
    await viyaFetch('/compute/contexts?limit=1');
  } catch {
    /* best effort */
  }
  computeServicePrimed = true;
}

/** The outcome of one progress poll: the milestones plus WHY there are none
 *  (`liveStatus`), so the panel can show a diagnostic instead of silence. */
export interface JobProgress {
  messages: string[];
  liveStatus:
    | 'ok'
    | 'no-milestones'
    | 'no-session-refs'
    | 'html-response'
    | 'fetch-failed'
    | `http-${number}`;
}

export async function getJobProgressMessages(job: JobExecutionJob): Promise<JobProgress> {
  const results = (job.results ?? {}) as Record<string, unknown>;
  const computeJob = String(results['COMPUTE_JOB'] ?? '');
  // The COMPUTE_SESSION value can carry the context name after the id.
  const computeSession = String(results['COMPUTE_SESSION'] ?? '').split(/\s/)[0];
  let liveStatus: JobProgress['liveStatus'] = 'no-session-refs';
  if (computeJob && computeSession) {
    try {
      await primeComputeService();
      const response = await viyaFetch(
        `/compute/sessions/${computeSession}/jobs/${computeJob}/log/content?limit=100000`,
        // Prefer the log-line collection; a live server may also answer with
        // plain text — extractProgressMessages handles both shapes.
        { accept: 'application/vnd.sas.compute.log.line.collection+json, application/json;q=0.9, text/plain;q=0.8' }
      );
      if (response.ok) {
        const logText = await response.text();
        if (logText.trimStart().startsWith('<')) {
          // An HTML body on a 200 means the request was answered by an SSO
          // redirect (SASLogon page), not the compute service — the service
          // session is not established. Re-prime on the next poll.
          computeServicePrimed = false;
          liveStatus = 'html-response';
          console.debug('Prompt Builder: live compute log answered with HTML (SSO redirect?) — re-priming');
        } else {
          const messages = extractProgressMessages(logText);
          if (messages.length > 0) return { messages, liveStatus: 'ok' };
          liveStatus = 'no-milestones';
        }
      } else {
        liveStatus = `http-${response.status}`;
        console.debug(`Prompt Builder: live compute log returned HTTP ${response.status}`);
      }
    } catch (error) {
      /* session already gone — fall back to the log file */
      liveStatus = 'fetch-failed';
      console.debug('Prompt Builder: live compute log fetch failed', error);
    }
  }
  const logLocation = String(job.logLocation ?? '');
  if (!logLocation) return { messages: [], liveStatus };
  try {
    const response = await viyaFetch(`${logLocation}/content?limit=100000`, { accept: 'text/plain' });
    if (!response.ok) return { messages: [], liveStatus };
    return { messages: extractProgressMessages(await response.text()), liveStatus };
  } catch {
    return { messages: [], liveStatus };
  }
}

const PROGRESS_PREFIX = /NOTE: Python-Subprocess\s*-\s?(.*)$/;

interface LogLine {
  line: string;
  type?: string;
}

/**
 * A compute job log arrives in one of three shapes (all seen in the wild):
 * a single JSON document ({"items":[{"type":"note","line":"..."}]}, the
 * vnd.sas.compute.log.line collection — what a live SAS Viya returns), one
 * JSON object per line, or plain text. Normalise them all to typed lines.
 */
function readLogLines(logText: string): LogLine[] {
  const trimmed = logText.trim();
  if (trimmed.startsWith('{')) {
    try {
      const doc = JSON.parse(trimmed) as { items?: Array<{ line?: string; type?: string }> };
      if (Array.isArray(doc.items)) {
        return doc.items.map((item) => ({ line: String(item.line ?? ''), type: item.type }));
      }
    } catch {
      /* not a single JSON document — fall through to per-line handling */
    }
  }
  return logText.split('\n').map((rawLine) => {
    if (rawLine.trimStart().startsWith('{')) {
      try {
        const parsed = JSON.parse(rawLine) as { line?: string; type?: string };
        if (typeof parsed.line === 'string') return { line: parsed.line, type: parsed.type };
      } catch {
        /* plain text line */
      }
    }
    return { line: rawLine };
  });
}

/** A log line that opens with a SAS prefix is a line of its own, never the
 *  continuation of the one above it. */
const LOG_PREFIX = /^\s*(NOTE|WARNING|ERROR|INFO|MPRINT|SYMBOLGEN)[:\s]/;
/** `200  %put ...` / `200! "…"` — the log's echo of the program's own source,
 *  numbered, including the continuation lines of a wrapped statement. */
const LINE_NUMBER = /^\s*\d+!?\s/;
/** PROC PYTHON echoes the interpreter prompt around the submitted block. */
const PYTHON_PROMPT = /^>{3,}\s*$/;

/**
 * Pull the SAS.logMessage() milestone lines out of a SAS log.
 *
 * Two things have to be undone to get the message back.
 *
 * Source-echo lines (the `%put NOTE: Python-Subprocess - ...;` statements the
 * log echoes before each NOTE) are skipped, so every milestone appears once.
 *
 * And a SAS log WRAPS at the line size: a milestone longer than ~132
 * characters arrives as a first line carrying the NOTE prefix followed by
 * unprefixed continuation lines. Verified live - a 700-character retrieval
 * row spans eight log lines, and reading only the first fragment yields
 * unparseable JSON, which is exactly how it fails: silently, as "no rows".
 * SAS breaks after a space and keeps it, so plain concatenation restores the
 * text; a log that strips trailing blanks costs one space inside the message
 * and nothing structural.
 */
export function extractProgressMessages(logText: string): string[] {
  const messages: string[] = [];
  let open: string | null = null;
  /** The kind of line the open message started on - a wrapped note continues
   *  as a note, so anything else (PROC PYTHON's `>>>` echo, which the compute
   *  log types as `normal`) ends it. */
  let openType: string | undefined;
  const flush = (): void => {
    if (open !== null && open.trim() !== '') messages.push(open.trim());
    open = null;
  };
  for (const { line, type } of readLogLines(logText)) {
    // A source echo ends whatever message was being assembled: the echo of
    // the NEXT statement is what follows a finished note.
    if (
      type === 'source' ||
      line.includes('%put') ||
      LINE_NUMBER.test(line) ||
      PYTHON_PROMPT.test(line)
    ) {
      flush();
      continue;
    }
    const match = PROGRESS_PREFIX.exec(line);
    if (match) {
      flush();
      open = match[1];
      openType = type;
      continue;
    }
    if (open !== null && type === openType && line.trim() !== '' && !LOG_PREFIX.test(line)) {
      open += line;
      continue;
    }
    flush();
  }
  flush();
  return messages;
}
