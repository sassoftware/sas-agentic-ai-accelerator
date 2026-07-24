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
 * Secrets never travel in the request: the job receives only the names of the
 * governed library/table it reads provider API keys from.
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
 * Returns [] when the log is not (yet) readable; while the job is still
 * running the log resource may not exist.
 */
export async function getJobProgressMessages(job: JobExecutionJob): Promise<string[]> {
  const logLocation = String(job.logLocation ?? '');
  if (!logLocation) return [];
  try {
    const response = await viyaFetch(`${logLocation}/content`, { accept: 'text/plain' });
    if (!response.ok) return [];
    const text = await response.text();
    return extractProgressMessages(text);
  } catch {
    return [];
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

/**
 * Pull the SAS.logMessage() milestone lines out of a SAS log. Source-echo
 * lines (the `%put NOTE: Python-Subprocess - ...;` statements the log echoes
 * before each NOTE) are skipped so every milestone appears exactly once.
 */
export function extractProgressMessages(logText: string): string[] {
  const messages: string[] = [];
  for (const { line, type } of readLogLines(logText)) {
    if (type === 'source' || line.includes('%put')) continue;
    const match = PROGRESS_PREFIX.exec(line);
    if (match && match[1].trim() !== '') messages.push(match[1].trim());
  }
  return messages;
}
