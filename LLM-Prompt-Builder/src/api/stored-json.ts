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
 * An option of options.json that the page draws with a control of its kind,
 * as the page reads it there: its type names that kind, and its label and
 * description are shown as texts. Its default and the values of an enum are
 * read by the rule of its kind (isModelOptions), and are not in the shape.
 */
const MODEL_OPTION: ObjectShape = {
  of: 'ModelOption',
  fields: { type: 'string', label: 'string', description: 'string' },
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
 * The options the page draws in number fields of its own - with a label, a
 * step and a range of the page, and nothing else of the option - when they
 * are numbers (isNumberOption); otherwise as every other option.
 */
export const NUMBER_FIELD_OPTIONS = ['temperature', 'top_p', 'top_k', 'max_length', 'max_tokens', 'max_completion_tokens', 'max_new_tokens'];

/**
 * Whether the page draws an option as a number and sends the number: its
 * type is none of enum, bool and string, and its default is a number. Any
 * other option of no such type is drawn in a text field and sent as text.
 */
export const isNumberOption = (option: { type?: unknown; default?: unknown }): boolean =>
  !['enum', 'bool', 'string'].includes(option.type as string) && typeof option.default === 'number';

/**
 * The id of the control of the option `key` of the LLM drawn at `index`: a
 * number field of the page's own (an option of NUMBER_FIELD_OPTIONS that is a
 * number) is `${key}${index}`; the control of every other option is
 * `llm${index}-option-${key}`, with "%" and "-" of the name written as %25
 * and %2D. So no option, whatever its name (model, options, a-seg-0), shares
 * an id with another option, with the radios of a choice (`<id>-seg-<n>`) or
 * with another element of the page.
 */
export const optionControlId = (index: number, key: string, option: unknown): string =>
  NUMBER_FIELD_OPTIONS.includes(key) && isObject(option) && isNumberOption(option)
    ? `${key}${index}`
    : `llm${index}-option-${key.replace(/[%-]/g, (character) => (character === '%' ? '%25' : '%2D'))}`;

const isKeyName = (value: unknown): boolean => typeof value === 'string' && value !== '';

/**
 * The blanks Python's str.strip() takes off the ends of a text - the
 * characters of str.isspace() -, which the parser of the score code
 * (_options_parser.py.j2) takes off the ends of a name and of a value before
 * the quotes. JavaScript's trim() takes another set (U+FEFF, and not
 * U+001C-U+001F or U+0085).
 */
const PARSER_BLANKS = '\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000';
const BLANKS_AT_ENDS = new RegExp(`^[${PARSER_BLANKS}]+|[${PARSER_BLANKS}]+$`, 'g');
const QUOTES_AT_ENDS = /^["']+|["']+$/g;
/** What int() of Python reads (a sign, decimal digits of any script, a "_" between two), and float() of one with a point. */
const DIGITS = String.raw`\p{Nd}(?:_?\p{Nd})*`;
const INT_TEXT = new RegExp(String.raw`^[+-]?${DIGITS}$`, 'u');
const FLOAT_TEXT = new RegExp(String.raw`^[+-]?(?:${DIGITS}\.(?:${DIGITS})?|\.${DIGITS})(?:[eE][+-]?${DIGITS})?$`, 'u');

/** A name or a value as the parser of the score code reads it: without the blanks and then the quotes at its ends. */
const strippedByScoreCode = (text: string): string => text.replace(BLANKS_AT_ENDS, '').replace(QUOTES_AT_ENDS, '');

/**
 * Whether the score code reads `text`, a value of the options string a run
 * sends, as that text (read_by_score_code of generator.py): it has no blank
 * and no quote at its ends, is not true or false in any case, and is no
 * number to float() (with a point) or int() (without one). "5", "TRUE",
 * " a " and "'a'" are not.
 */
export const isReadAsWritten = (text: string): boolean =>
  strippedByScoreCode(text) === text &&
  !['true', 'false'].includes(text.toLowerCase()) &&
  !(text.includes('.') ? FLOAT_TEXT : INT_TEXT).test(text);

/**
 * Whether the score code reads a text typed into the text field of an option
 * as something else than that text, for an option whose default is a text (a
 * string option): by isReadAsWritten, the rule isModelOptions holds that
 * default to - " a ", 'say "hi"', "TRUE" and "5" are such texts. A run is not
 * sent with one, as with a comma. The text field of an option whose default
 * is a number or true/false is not held to it: a number or true/false typed
 * there is read as what its default is read as.
 */
export const isTypedTextReadOtherwise = (option: { default?: unknown } | undefined, text: string): boolean =>
  typeof option?.default === 'string' && !isReadAsWritten(text);

/**
 * Whether a run sends the number as a text the score code reads as a text
 * (sent_number of generator.py): the page writes a number as String() does,
 * in exponent notation below 0.000001 and from 10^21 on, and one of one
 * significant digit there has no point - 1e-7, 1e+21 -, which float() is not
 * tried on and int() does not read.
 */
export const isSentAsText = (value: number): boolean => {
  const shown = String(value);
  return shown.includes('e') && !shown.includes('.');
};

/**
 * options.json of an LLM: every option as the page draws it and sends it in a
 * run. The rule mdb writes the file by (check_options_json of
 * Model-Definition-Builder/cli/src/mdb/core/generator.py), word for word:
 *
 *     options.json         object: option name -> option
 *     option name          no "," and no ":", and no blank and no quote at
 *                          its ends
 *     option               object that holds "default", and no NaN or
 *                          Infinity (JSON has neither)
 *     option.type          text, where given (absent or null: not given)
 *     option.label         text, where given (absent or null: not given)
 *     option.description   text, where given (absent or null: not given)
 *     type "enum"          values: a non-empty list of texts, none with ",",
 *                          each read as written
 *                          default: one of values
 *     type "bool"          default: true or false
 *     option "API_KEY"     default: a non-empty text
 *     any other option     default: a text without "," read as written, a
 *                          number sent as a number, true or false
 *
 * A run sends the options as one text, {name:value,name:value}, which the
 * score code splits at each "," and each name from its value at the first
 * ":" - so a name holds neither, and a text a run can send of the file (the
 * default of an option, a value of an enum) no ",". The parser of the score
 * code takes the blanks and then the quotes off the ends of a name and of a
 * value, reads true and false in any case as true and false, and a value as
 * a number where float() reads one that holds a point or int() one that does
 * not - so a name has no blank and no quote at its ends, a text the run
 * sends is read as written (isReadAsWritten), and a number is not one the
 * run sends as 1e-7 or 1e+21 (isSentAsText). The default of any
 * other option is drawn as it is written: a number in a
 * number field, sent as a number (isNumberOption), anything else in a text
 * field, sent as text - a default that is null, a list or an object cannot
 * be. The page reads the file by that rule, with these differences only:
 *   - it reads a bool's default "true" or "false" (a text) as the box ticked
 *     or not, as it reads true or false;
 *   - of API_KEY it reads the default alone (the name of the provider key),
 *     and the label and the description of an option it draws in a number
 *     field of its own (NUMBER_FIELD_OPTIONS) not at all.
 * An option the page could draw only as something else - an enum without its
 * values in a number field, a box off for a default of 1, a provider key
 * named by a number - or the score code read only as something else - a
 * default "a,b" read as "a", " a " as "a", "TRUE" as true, 1e-7 as a text,
 * a name " temperature" as temperature - makes the file one the page cannot
 * read.
 */
export function isModelOptions(value: unknown): boolean {
  const sentAsWritten = (item: unknown): boolean =>
    typeof item === 'string' ? !item.includes(',') && isReadAsWritten(item) : typeof item !== 'number' || !isSentAsText(item);
  return (
    isObject(value) &&
    Object.entries(value).every(([key, option]) => {
      if (!isObject(option) || !('default' in option)) return false;
      if (key.includes(',') || key.includes(':') || strippedByScoreCode(key) !== key) return false;
      if (key === 'API_KEY') return isKeyName(option.default);
      if (option.type !== undefined && option.type !== null && typeof option.type !== 'string') return false;
      if (NUMBER_FIELD_OPTIONS.includes(key) && isNumberOption(option)) return sentAsWritten(option.default);
      if (!fits(option, MODEL_OPTION)) return false;
      if (option.type === 'enum') {
        const values = option.values;
        return Array.isArray(values) && values.length > 0 && values.every((item) => typeof item === 'string' && sentAsWritten(item)) && (values as unknown[]).includes(option.default);
      }
      if (option.type === 'bool') return [true, false, 'true', 'false'].includes(option.default as never);
      return ['string', 'number', 'boolean'].includes(typeof option.default) && sentAsWritten(option.default);
    })
  );
}

/**
 * options.json of a decision model: the page reads its API_KEY.default alone,
 * the name of the provider key a call of the model sends - where API_KEY is
 * given, it is an object whose default is a non-empty text. No other field of
 * the file is read, so none is checked.
 */
export function isDecisionModelOptions(value: unknown): boolean {
  if (!isObject(value)) return false;
  const key = Object.prototype.hasOwnProperty.call(value, 'API_KEY') ? value.API_KEY : undefined;
  return key === undefined || key === null || (isObject(key) && isKeyName(key.default));
}

/**
 * The options of a call as the one text it sends them in,
 * {name:value,name:value}, each value as JavaScript writes it as text. A run
 * stores the same text with each model (Save Experiments), and the manifest
 * of a template writes the options of its best run from it. An option whose
 * value is undefined is left out: a call sends none, and a drawn run holds
 * one for the key entry of a model whose key name the page does not know
 * (the tracker keeps no key, only its name).
 */
export const optionsText = (options: Record<string, unknown>): string =>
  `{${Object.entries(options)
    .filter(([, value]) => value !== undefined)
    .map(([name, value]) => `${name}:${value}`)
    .join(',')}}`;

/**
 * Each option of an options text (optionsText) as it was sent: its name and
 * its text. The braces around the text are taken off, the rest is split at
 * each "," and each piece at its first ":", as the score code splits it - but
 * no blank and no quote is taken off a name or a value, and so a text with a
 * quote, a backslash, a brace or a blank at an end comes back as it was sent.
 * A name the page sends holds neither "," nor ":" and a text no "," - the
 * file is refused otherwise (isModelOptions), and a run with a typed comma is
 * not sent -, so no option is split in two. A piece without ":" is no option.
 */
export function optionsTextEntries(text: string | null | undefined): [string, string][] {
  const whole = String(text ?? '').trim();
  const inner = whole.startsWith('{') && whole.endsWith('}') ? whole.slice(1, -1) : whole;
  if (inner === '') return [];
  return inner.split(',').flatMap((piece): [string, string][] => {
    const separator = piece.indexOf(':');
    return separator === -1 ? [] : [[piece.slice(0, separator), piece.slice(separator + 1)]];
  });
}

/**
 * The options of a stored run, each with the value it was sent with: true or
 * false for that word, a number for a text that is the number as JavaScript
 * writes it (`5`, `0.5`, `-1`, `1e-7`), and the text otherwise (`0.50`
 * stays a text). Each value written back as text is the text that was sent,
 * so a run loaded into the fields sends what it sent.
 */
export function optionsFromText(text: string | null | undefined): Record<string, unknown> {
  // No prototype: an option named __proto__ is a field like any other.
  const options: Record<string, unknown> = Object.create(null);
  for (const [name, value] of optionsTextEntries(text)) {
    if (value === 'true' || value === 'false') options[name] = value === 'true';
    else if (value.trim() !== '' && String(Number(value)) === value) options[name] = Number(value);
    else options[name] = value;
  }
  return options;
}

/** The JSON files the page reads, by their name, with the check of each. */
export const STORED_JSON_FILES = {
  'Prompt-Experiment-Tracker.json': isExperimentTracker,
  'Prompt-Optimization-Tracker.json': isOptimizationTracker,
  'rag-setup.json': isRagSetup,
  // Of an LLM; the one of a decision model is read by isDecisionModelOptions.
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
 * from a file of its name - by the function of its name, or by `check` where
 * a reader reads less of it (the options.json of a decision model,
 * isDecisionModelOptions). Throws as getFileContent does when the file could
 * not be read, and StoredFileUnusable when it was read and is no JSON or not
 * that file - which reading it again does not change.
 */
export async function readStoredJson<T>(
  fileURI: string,
  fileName: StoredJsonName,
  check: (value: unknown) => boolean = STORED_JSON_FILES[fileName]
): Promise<T> {
  const text = await (await getFileContent(fileURI)).text();
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new StoredFileUnusable(fileName);
  }
  if (!check(value)) throw new StoredFileUnusable(fileName);
  return value as T;
}
