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
  uri?: string;
  [key: string]: unknown;
}

/**
 * Resolve a SAS Content path (e.g. /Public/Jobs/Optimize-Prompt-DSPy) to the
 * job definition URI the Job Execution service accepts. Throws when the path
 * does not exist or is not a job definition.
 */
export async function resolveJobDefinitionUri(programPath: string): Promise<string> {
  const item = await viyaGet<FolderItem>(
    `/folders/folders/@item?path=${encodeURIComponent(programPath)}`
  );
  const uri = String(item?.uri ?? '');
  if (!uri.startsWith('/jobDefinitions/definitions/')) {
    throw new Error(`The path ${programPath} is not a Job Execution job definition.`);
  }
  return uri;
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

/**
 * Pull the SAS.logMessage() milestone lines out of a SAS log. Handles both the
 * plain-text log and the line-JSON form ({"line":"NOTE: ..."} per line) the
 * file service returns for compute logs.
 */
export function extractProgressMessages(logText: string): string[] {
  const messages: string[] = [];
  for (const rawLine of logText.split('\n')) {
    let line = rawLine;
    // Line-JSON compute log: {"type":"note","line":"NOTE: ..."}
    if (line.trimStart().startsWith('{')) {
      try {
        const parsed = JSON.parse(line) as { line?: string };
        if (typeof parsed.line === 'string') line = parsed.line;
      } catch {
        /* not JSON — treat as plain text */
      }
    }
    const match = PROGRESS_PREFIX.exec(line);
    if (match && match[1].trim() !== '') messages.push(match[1].trim());
  }
  return messages;
}
