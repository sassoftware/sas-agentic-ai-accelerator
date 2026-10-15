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
 * What a field of a stored file holds, as the page reads it: a text, a
 * number, a boolean, a flag (a box the page writes as true/false or 1/0), a
 * list of one kind, a map of names to one kind, or an object with fields of
 * its own. `of` names the type of the sources the object mirrors, field for
 * field (the harness compares the two). A field that is absent or null is
 * not there, which the page reads as such; any other value has to be of its
 * kind, and the fields `required` names have to be there.
 */
export type Shape = 'string' | 'number' | 'boolean' | 'flag' | { list: Shape } | { map: Shape } | ObjectShape;
export interface ObjectShape {
  of: string;
  fields: Record<string, Shape>;
  required?: readonly string[];
}

/** Whether a value is of a shape (see Shape). */
export function fits(value: unknown, shape: Shape): boolean {
  if (shape === 'string' || shape === 'number' || shape === 'boolean') return typeof value === shape;
  if (shape === 'flag') return typeof value === 'boolean' || typeof value === 'number';
  if ('list' in shape) return Array.isArray(value) && value.every((item) => fits(item, shape.list));
  if ('map' in shape) return isObject(value) && Object.values(value).every((item) => fits(item, shape.map));
  if (!isObject(value)) return false;
  return Object.entries(shape.fields).every(([name, field]) => {
    const held = Object.prototype.hasOwnProperty.call(value, name) ? value[name] : undefined;
    return held === undefined || held === null ? !(shape.required ?? []).includes(name) : fits(held, field);
  });
}

const PROMPT_VARIABLE: ObjectShape = {
  of: 'PromptVariable',
  fields: { name: 'string', description: 'string', type: 'string', value: 'string' },
};
const OPTIMIZATION_USAGE: ObjectShape = {
  of: 'OptimizationUsage',
  fields: { calls: 'number', promptTokens: 'number', outputTokens: 'number', runTime: 'number' },
};
const OPTIMIZED_PROMPT: ObjectShape = {
  of: 'OptimizationTrackerEntry.optimizedPrompt',
  fields: { systemPrompt: 'string', userPrompt: 'string', variables: { list: PROMPT_VARIABLE }, demos: { list: { map: 'string' } } },
};

/** A row of Prompt-Experiment-Tracker.json: the header row of a run, or the row of a model. */
const PET_ROW: ObjectShape = {
  of: 'PETRow',
  required: ['runId', 'model'],
  fields: {
    runId: 'number',
    systemPrompt: 'string',
    userPrompt: 'string',
    variables: { list: PROMPT_VARIABLE },
    manifest: {
      of: 'ManifestConfig',
      // Restoring a manifest configuration asks its outputs which to tick.
      required: ['selectedOutputs'],
      fields: {
        integratedLLMCall: 'boolean',
        selectedOutputs: { list: 'string' },
        outputVariables: {
          list: { of: 'PromptOutputVariable', fields: { name: 'string', description: 'string', type: 'string', defaultValue: 'string' } },
        },
      },
    },
    mode: 'string',
    expected: { map: 'string' },
    model: 'string',
    options: 'string',
    response: 'string',
    run_time: 'number',
    prompt_length: 'number',
    output_length: 'number',
    best_prompt: 'flag',
    fastest_prompt: 'boolean',
    fewest_tokens_prompt: 'boolean',
    cheapest_prompt: 'boolean',
    cost: 'number',
    judge_rank: 'number',
    judge_best: 'flag',
    judge_model: 'string',
    judge_confidence: 'string',
    judge_reasoning: 'string',
    judge_include_self: 'flag',
    judge_auto: 'flag',
    judge_mode: 'string',
    judge_panel: 'string',
    judge_agreement: 'string',
    judge_cost: 'number',
    judge_chairman_model: 'string',
    judge_chairman_reasoning: 'string',
    judge_ballots: {
      list: {
        of: 'JudgeBallot',
        fields: {
          judgeModel: 'string',
          status: 'string',
          ranking: { list: 'string' },
          confidence: 'string',
          reasoning: 'string',
          excludedSelf: 'boolean',
          error: 'string',
          usage: { of: 'JudgeUsage', fields: { run_time: 'number', prompt_length: 'number', output_length: 'number' } },
        },
      },
    },
  },
};

/** An entry of Prompt-Optimization-Tracker.json: one run of the optimization job. */
const OPTIMIZATION_ENTRY: ObjectShape = {
  of: 'OptimizationTrackerEntry',
  // The number the page draws and deletes the run by, and its status.
  required: ['optimizationId', 'status'],
  fields: {
    optimizationId: 'number',
    startedAt: 'string',
    finishedAt: 'string',
    status: 'string',
    jobId: 'string',
    targetModel: 'string',
    datasetSource: 'string',
    sampleCount: 'number',
    optimizer: 'string',
    metric: 'string',
    judgeModel: 'string',
    metricBefore: 'number',
    metricAfter: 'number',
    baselinePrompt: { of: 'OptimizationTrackerEntry.baselinePrompt', fields: { systemPrompt: 'string', userPrompt: 'string' } },
    trainSize: 'number',
    validationSize: 'number',
    evaluations: {
      list: {
        of: 'OptimizationEvaluation',
        fields: {
          inputs: { map: 'string' },
          expected: 'string',
          baselineResponse: 'string',
          baselineCorrect: 'boolean',
          baselineScore: 'number',
          optimizedResponse: 'string',
          optimizedCorrect: 'boolean',
          optimizedScore: 'number',
        },
      },
    },
    optimizedPrompt: OPTIMIZED_PROMPT,
    skippedReason: 'string',
    type: 'string',
    targets: {
      list: {
        of: 'ComparisonTargetRow',
        fields: {
          model: 'string',
          status: 'string',
          quality: 'number',
          avgLatency: 'number',
          usage: OPTIMIZATION_USAGE,
          metricBefore: 'number',
          metricAfter: 'number',
          skippedReason: 'string',
          optimizedPrompt: OPTIMIZED_PROMPT,
          evaluations: {
            list: {
              of: 'ComparisonTargetRow.evaluations',
              fields: {
                inputs: { map: 'string' },
                expected: 'string',
                response: 'string',
                correct: 'boolean',
                score: 'number',
                baselineResponse: 'string',
                baselineCorrect: 'boolean',
                baselineScore: 'number',
                optimizedResponse: 'string',
                optimizedCorrect: 'boolean',
                optimizedScore: 'number',
              },
            },
          },
          error: 'string',
        },
      },
    },
    usage: { of: 'OptimizationTrackerEntry.usage', fields: { target: OPTIMIZATION_USAGE, judge: OPTIMIZATION_USAGE } },
    producedPromptModelId: 'string',
    datasetSnapshot: 'string',
    error: 'string',
  },
};

/** A prompt of the enrichment chain of rag-setup.json; one without a prompt is dropped by Save setup. */
const RAG_ENRICH_STEP: ObjectShape = {
  of: 'RagEnrichStep',
  required: ['promptModelId'],
  fields: {
    promptProjectId: 'string',
    promptModelId: 'string',
    promptModelName: 'string',
    promptVersionId: 'string',
    promptVersionLabel: 'string',
    mapping: { map: 'string' },
    headerOutput: 'string',
    columnOutputs: { list: 'string' },
    workers: 'number',
  },
};

/**
 * rag-setup.json: every field the page shows, writes back or hands to the
 * generated job. What a setup saved before it existed lacks (artifactsFolder,
 * documentation, policies, enrich, job) may be absent - the defaults stand in
 * for the first three, and no enrichment and no job are what their absence
 * means.
 */
const RAG_SETUP: ObjectShape = {
  of: 'RagSetup',
  required: ['version', 'source', 'extraction', 'chunking', 'embedding', 'store', 'tables'],
  fields: {
    version: 'number',
    documentation: {
      of: 'RagSetup.documentation',
      fields: { modelPurpose: 'string', intendedUse: 'string', expectedBenefit: 'string', outOfScopeUseCases: 'string', limitations: 'string' },
    },
    artifactsFolder: 'string',
    source: { of: 'RagSetup.source', required: ['path'], fields: { path: 'string', includeCode: 'boolean' } },
    extraction: { of: 'RagSetup.extraction', required: ['extractor'], fields: { extractor: 'string' } },
    chunking: {
      of: 'RagSetup.chunking',
      required: ['chunker', 'inputTokenLimit', 'overlapTokens'],
      fields: { chunker: 'string', inputTokenLimit: 'number', overlapTokens: 'number' },
    },
    enrich: { list: RAG_ENRICH_STEP },
    embedding: {
      of: 'RagSetup.embedding',
      required: ['model', 'dims'],
      fields: { model: 'string', dims: 'number', deploymentType: 'string', scrEndpoint: 'string' },
    },
    store: {
      of: 'RagSetup.store',
      required: ['backend', 'collection'],
      fields: { backend: 'string', host: 'string', port: 'number', database: 'string', sslmode: 'string', collection: 'string' },
    },
    tables: { of: 'RagSetup.tables', required: ['prefix', 'caslib'], fields: { prefix: 'string', caslib: 'string' } },
    pipelineVersion: 'string',
    credentialDomain: 'string',
    policies: {
      of: 'RagSetup.policies',
      fields: {
        deletedPolicy: 'string',
        retainDays: 'number',
        recordHistory: 'boolean',
        embedReplicas: 'number',
        persistElements: 'boolean',
        persistChunks: 'boolean',
      },
    },
    job: { of: 'RagSetup.job', required: ['definitionUri'], fields: { definitionUri: 'string' } },
  },
};

/**
 * An option of options.json, as the page reads it: its default is read as a
 * value of any kind, and is not in the shape (isModelOptions asks for it);
 * the values of an enum are drawn as texts.
 */
const MODEL_OPTION: ObjectShape = {
  of: 'ModelOption',
  fields: { type: 'string', values: { list: 'string' }, label: 'string', description: 'string' },
};

/** The shapes of the stored files, for the harness. */
export const STORED_SHAPES = { PET_ROW, OPTIMIZATION_ENTRY, RAG_SETUP, MODEL_OPTION } as const;

/**
 * The keys of a run of the experiment tracker that the page reads as fields
 * of the run, not as the name of a model: the fields of a run as the page
 * holds it, and `author`, which no run of the page holds and which the page
 * has kept out of the models since its first version. Every other key of a
 * run is the name of a model, so no model can be named so.
 */
export const TRACKER_META_KEYS = ['systemPrompt', 'userPrompt', 'author', 'variables', 'manifest', 'judge', 'mode', 'expected'];

/**
 * Whether a run as the page holds it cannot keep the result of a model of
 * this name: the page reads the name as a field of the run
 * (TRACKER_META_KEYS), or it is `__proto__`, which
 * sets the prototype of the run instead of a field of its own. The page
 * offers no such model, and the tracker check refuses a row of one. Every
 * other name - also one of a field every object inherits, such as
 * `constructor` - becomes a field of the run's own.
 */
export const isReservedModelName = (name: string): boolean => name === '__proto__' || TRACKER_META_KEYS.includes(name);

/**
 * Prompt-Experiment-Tracker.json: the rows of the runs, as the page writes
 * them and reads them back - a run starts with a row of its own (a numeric
 * runId, no model, its two prompts as texts) and goes on with a row per
 * model (the same runId, the name of the model): at least one, each model
 * once, and none of a name a run cannot keep a result under
 * (isReservedModelName). Every field of a row is of the kind the page reads
 * (PET_ROW).
 */
export function isExperimentTracker(value: unknown): boolean {
  if (!Array.isArray(value)) return false;
  // As the reading of the page groups the rows: a row whose runId differs
  // from the row before starts a run.
  let run: number | null = null;
  let models: string[] = [];
  const runHasModels = () => run === null || models.length > 0;
  const rowsFit = value.every((row) => {
    if (!fits(row, PET_ROW)) return false;
    const { runId, model, systemPrompt, userPrompt } = row as { runId: number; model: string; systemPrompt: unknown; userPrompt: unknown };
    if (runId !== run) {
      if (!runHasModels()) return false;
      run = runId;
      models = [];
      return model === '' && typeof systemPrompt === 'string' && typeof userPrompt === 'string';
    }
    if (run === null || model === '' || isReservedModelName(model) || models.includes(model)) return false;
    models.push(model);
    return true;
  });
  return rowsFit && runHasModels();
}

/**
 * Prompt-Optimization-Tracker.json: the runs the optimization job wrote, each
 * with the number the page draws and deletes it by - which the job gives
 * each run once - and its status. Every field of an entry is of the kind the
 * page reads (OPTIMIZATION_ENTRY).
 */
export function isOptimizationTracker(value: unknown): boolean {
  return (
    Array.isArray(value) &&
    value.every((entry) => fits(entry, OPTIMIZATION_ENTRY)) &&
    new Set(value.map((entry: { optimizationId: number }) => entry.optimizationId)).size === value.length
  );
}

/**
 * rag-setup.json: a setup of version 1 whose every field, at every depth, is
 * of the kind the page reads (RAG_SETUP). The enrichment is a list of
 * prompts, or the one prompt a setup saved before the chain held, which is
 * checked as a list of one.
 */
export function isRagSetup(value: unknown): boolean {
  if (!isObject(value) || value.version !== 1) return false;
  return fits(isObject(value.enrich) ? { ...value, enrich: [value.enrich] } : value, RAG_SETUP);
}

/**
 * options.json of an LLM or a decision model: every option the model takes,
 * each described by an object that holds its default, and whose other fields
 * the page reads are of the kind it reads (MODEL_OPTION).
 */
export function isModelOptions(value: unknown): boolean {
  return isObject(value) && Object.values(value).every((option) => fits(option, MODEL_OPTION) && 'default' in (option as object));
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
