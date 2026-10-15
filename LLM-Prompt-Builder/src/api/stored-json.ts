/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { getFileContent } from './files-api';

/**
 * The JSON files the page reads from SAS Model Manager, and for each the
 * check of the fields the page needs from it.
 *
 * A file that is JSON is not yet the file the page takes it for: a list of
 * objects without the fields of a run was drawn as a run, and Save
 * Experiments then wrote a tracker without the stored rows; an optimization
 * tracker of such entries drew a run whose Delete removed the stored file;
 * an object that is no setup was shown with the defaults, which Save setup
 * wrote over it. Every stored JSON file is read through readStoredJson, which
 * checks it with the function of its name: a file that fails is a
 * StoredFileUnusable, which the page names as a file it cannot use, and
 * writes nothing over and deletes nothing of.
 */

const isObject = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

/**
 * Prompt-Experiment-Tracker.json: the rows of the runs, as the page writes
 * them and reads them back - a run starts with a row of its own (a numeric
 * runId, no model, its two prompts as texts) and goes on with a row per
 * model (the same runId, the name of the model).
 */
export function isExperimentTracker(value: unknown): boolean {
  if (!Array.isArray(value)) return false;
  // As the reading of the page groups the rows: a row whose runId differs
  // from the row before starts a run.
  let run = 0;
  return value.every((row) => {
    if (!isObject(row) || typeof row.runId !== 'number' || typeof row.model !== 'string') return false;
    if (row.runId !== run) {
      run = row.runId;
      return row.model === '' && typeof row.systemPrompt === 'string' && typeof row.userPrompt === 'string';
    }
    return run !== 0 && row.model !== '';
  });
}

/**
 * Prompt-Optimization-Tracker.json: the runs the optimization job wrote, each
 * with the number the page draws and deletes it by, and its status.
 */
export function isOptimizationTracker(value: unknown): boolean {
  return (
    Array.isArray(value) &&
    value.every((entry) => isObject(entry) && typeof entry.optimizationId === 'number' && typeof entry.status === 'string')
  );
}

/**
 * rag-setup.json: a setup of version 1 with the sections and the fields the
 * page shows - and writes back - from it. What a setup saved before it
 * existed lacks (artifactsFolder, documentation, policies, enrich, job) may
 * be absent - the defaults stand in for the first three, and no enrichment
 * and no job are what their absence means; where present, each is of its
 * kind.
 */
export function isRagSetup(value: unknown): boolean {
  if (!isObject(value) || value.version !== 1) return false;
  const { source, extraction, chunking, embedding, store, tables } = value;
  return (
    isObject(source) && typeof source.path === 'string' &&
    isObject(extraction) && typeof extraction.extractor === 'string' &&
    isObject(chunking) && typeof chunking.chunker === 'string' &&
    typeof chunking.inputTokenLimit === 'number' && typeof chunking.overlapTokens === 'number' &&
    isObject(embedding) && typeof embedding.model === 'string' && typeof embedding.dims === 'number' &&
    isObject(store) && typeof store.backend === 'string' && typeof store.collection === 'string' &&
    isObject(tables) && typeof tables.prefix === 'string' && typeof tables.caslib === 'string' &&
    (value.artifactsFolder === undefined || typeof value.artifactsFolder === 'string') &&
    (value.documentation === undefined || isObject(value.documentation)) &&
    (value.policies === undefined || isObject(value.policies)) &&
    (value.enrich === undefined || Array.isArray(value.enrich)) &&
    (value.job === undefined || (isObject(value.job) && typeof value.job.definitionUri === 'string'))
  );
}

/**
 * options.json of an LLM or a decision model: every option the model takes,
 * each described by an object that holds its default.
 */
export function isModelOptions(value: unknown): boolean {
  return isObject(value) && Object.values(value).every((option) => isObject(option) && 'default' in option);
}

/** The JSON files the page reads, by their name, with the check of each. */
export const STORED_JSON_FILES = {
  'Prompt-Experiment-Tracker.json': isExperimentTracker,
  'Prompt-Optimization-Tracker.json': isOptimizationTracker,
  'rag-setup.json': isRagSetup,
  'options.json': isModelOptions,
} as const;

export type StoredJsonName = keyof typeof STORED_JSON_FILES;

/** A stored JSON file that was read and holds nothing this page can use. */
export class StoredFileUnusable extends Error {
  constructor(readonly fileName: StoredJsonName) {
    super(`${fileName} holds nothing this page can read`);
  }
}

/**
 * Read a stored JSON file and check it against the fields the page needs
 * from a file of its name. Throws as getFileContent does when the file could
 * not be read, and StoredFileUnusable when it was read and is no JSON or not
 * that file - which reading it again does not change.
 */
export async function readStoredJson<T>(fileURI: string, fileName: StoredJsonName): Promise<T> {
  const text = await (await getFileContent(fileURI)).text();
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new StoredFileUnusable(fileName);
  }
  if (!STORED_JSON_FILES[fileName](value)) throw new StoredFileUnusable(fileName);
  return value as T;
}
