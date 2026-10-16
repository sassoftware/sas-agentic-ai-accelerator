/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Create a Prompt Builder Object
 */

import type { PromptBuilderConfig, InterfaceText, PromptBuilderText } from '../types';
import type { DropdownOption } from '../types/models';
import type { DependentDecision } from '../types/relationships';
import { getAppState } from '../state/app-state';
import { oneAtATime } from '../util/one-at-a-time';
import {
  isDecisionModelOptions,
  isNumberOption,
  isReservedModelName,
  NUMBER_FIELD_OPTIONS,
  optionControlId,
  readStoredJson,
  StoredFileUnusable,
  TRACKER_META_KEYS,
} from '../api/stored-json';
import { UnansweredWrite, ViyaRefused } from '../api/http-client';
import {
  getModelProjects,
  getModelProjectModels,
  getModelRepositoryInformation,
  createModelProject,
  createModel,
  getModelContents,
  createModelContent,
  createModelVersion,
  deleteModelContent,
  getModelVariables,
  deleteModelVariable,
  deleteModel,
  deleteModelProject,
  updateModelTags,
  updateModelAttributes,
  getModelDetails,
} from '../api/models-api';
import { getModelDependentDecisions } from '../api/relationships-api';
import { callSCRLLM, callSCRDecision } from '../api/scr-api';
import {
  DECISION_PROJECT_NAME,
  DECISION_PROJECT_TAG,
  DECISION_TEMPLATE_ALGORITHM,
  DECISION_TEMPLATE_TAG,
  DEFAULT_DECISION_OUTPUTS,
  MAX_QUESTION_ID_LENGTH,
  SCORE_CODE_TLS_DEFAULT_BRANCH,
  TLS_VERIFICATION_OFF_TAG,
  allExpectedMatch,
  buildDecisionManifest,
  createQuestionDesigner,
  describeFailedCall,
  estimateTokens,
  fitCheck,
  isReservedScoreInput,
  isReservedScoreOutput,
  parseAnswers,
  pythonFStringBody,
  pythonString,
  pythonTripleQuotedBody,
  questionIdProblem,
  questionsFromJson,
  questionsToJson,
  renderAnswers,
  renderComparison,
  renderEvaluation,
  renderQuestionList,
  scoreCodeTlsDefaultComment,
  type DecisionAnswers,
  type DecisionModelCapabilities,
  type DecisionQuestion,
  type DecisionQuestionType,
  type EvaluationCase,
} from './prompt-decision';
import { judgeRun, aggregateBallots, chairmanBreakTie, type JudgeConfidence, type JudgeBallot, type JudgeUsage } from '../api/judge-api';
import {
  resolveJobDefinitionUri,
  launchJob,
  getJob,
  getJobProgressMessages,
  isTerminalJobState,
  type JobExecutionJob,
} from '../api/jobexec-api';
import { getCasServers, getCaslibs, getCasTables, getCasTableInfo, getCasTableRows } from '../api/cas-api';
import { resolveDomainSecrets } from '../api/credentials-api';
import { createAccordionItem } from '../ui/accordion';
import { attachCombobox } from '../ui/combobox';
import { createListFilter, renderFilteredOptions } from '../ui/list-filter';
import { createDocSection, createInfoLabel } from '../ui/doc-section';
import { createCreateModal, type CreateModalDialog, type CreateModalEntry } from '../ui/create-modal';
import { openExternalLink } from '../ui/external-link';
import { showConfirmModal } from '../ui/confirm-modal';
import { showToast } from '../ui/toast';
import { escapeHtml } from '../ui/dom-helpers';
import { renderMarkdown } from '../ui/markdown';
import { createStepper } from '../ui/stepper';
import { createSelectionLock } from '../ui/selection-lock';
import { createInstructionCard } from '../ui/instruction-card';
import { createKindSelector, type KindSelector, type PromptKind } from '../ui/kind-selector';
import { isValidDS2VariableName, validateAndCorrectPackageName } from '../util/validation';
import { createTypedOptionControl, optionDisplayLabel, syncSegmentedControl } from '../ui/option-controls';
import Tooltip from 'bootstrap/js/dist/tooltip';

interface ModelOption {
  default: unknown;
  /** Optional typed-option fields (Model Definition Builder emits these for
   *  non-numeric options): 'enum' renders a segmented selector (or dropdown)
   *  of its `values` (read by isModelOptions with the default), 'bool' a
   *  checkbox, 'string' a text input. Absent = a number input for a number
   *  default, a text input otherwise (isNumberOption). `label` overrides the
   *  displayed option name. */
  type?: 'enum' | 'bool' | 'string';
  label?: string;
  description?: string;
  [key: string]: unknown;
}

interface AvailableLLM {
  id: string;
  name: string;
  fileURI?: string;
  options?: Record<string, ModelOption>;
  /** Its options.json was read and is not one this page can read: the model cannot be used. */
  optionsUnusable?: boolean;
  /** Cost/governance attributes read from the LLM's Model Manager registration
   *  (see loadLLMAttributes). Used for per-call cost estimation and copied onto
   *  the manifested prompt model. Any may be absent (older registrations). */
  inputTokenCount?: number | null;
  outputTokenCount?: number | null;
  hostingCosts?: number | null;
  llmodelType?: string | null;
  provider?: string | null;
  deploymentId?: string | null;
  endPoint?: string | null;
  /** What a decision model accepts and its licence, from its registration (see extractCostAttributes). */
  contextLength?: number | null;
  maxOptions?: number | null;
  questionTypes?: DecisionQuestionType[] | null;
  licenseClass?: string | null;
  [key: string]: unknown;
}

/** The cost/governance attributes copied from an LLM's Model Manager
 *  registration. Mirrors the fields mdb writes on registered models. */
interface LLMCostAttributes extends DecisionModelCapabilities {
  inputTokenCount?: number | null;
  outputTokenCount?: number | null;
  hostingCosts?: number | null;
  llmodelType?: string | null;
  provider?: string | null;
  deploymentId?: string | null;
  endPoint?: string | null;
  /** The licence class tag mdb registers (Open-Source, Proprietary), with Apache-2 when tagged. */
  licenseClass?: string | null;
}

interface ExperimentResult {
  modelName: string;
  data: {
    run_time: number;
    output_length: number;
    prompt_length: number;
    response: string;
    error?: string;
    fastest_prompt?: boolean;
    fewest_tokens_prompt?: boolean;
    cheapest_prompt?: boolean;
    /** Estimated cost of this call; null when the model carries no prices. */
    cost?: number | null;
    [key: string]: unknown;
  };
  options: Record<string, unknown>;
}

/** A user-defined prompt variable, referenced as {{name}} in the prompts. */
interface PromptVariable {
  name: string;
  description: string;
  type: 'string' | 'decimal';
  value: string;
}

/**
 * Run-level summary of an LLM-as-a-Judge evaluation. Kept as tracker metadata
 * (like `manifest`), not a per-model result. `reasoning`/`ranking`/`best` are
 * held in memory for the session; only `judgeModel` and `confidence` are
 * persisted (on the header PETRow), so a reloaded run shows the winner and
 * confidence but not the full rationale.
 */
interface JudgeSummary {
  /** Single-judge mode: the one judge. Council mode: '' (see `panel`). */
  judgeModel: string;
  status: 'ok' | 'error' | 'unparseable';
  best?: string | null;
  ranking?: string[] | null;
  confidence?: JudgeConfidence | null;
  reasoning?: string | null;
  /** True when the judge's own response was excluded from the ranking. */
  excludedSelf?: boolean;
  /** True when the judge's own response was included (bias possible). */
  includedSelf?: boolean;
  /** Raw judge configuration used for the run, restored onto the controls when
   *  the run is loaded: the panel, the include-self toggle and the auto-judge toggle. */
  includeSelf?: boolean;
  autoJudge?: boolean;
  /** 'single' (one judge) or 'council' (a panel). Absent = single (Phase 1). */
  mode?: 'single' | 'council';
  /** Council: the judge model names on the panel. */
  panel?: string[];
  /** Council: one ballot per panel member. */
  ballots?: JudgeBallot[];
  /** Council aggregation method. */
  method?: 'borda';
  /** Council: how many judges ranked the winner first, out of the total counted. */
  agreement?: { firstChoiceForWinner: number; total: number } | null;
  /** Council: true when the panel split and the tie was NOT resolved. */
  tie?: boolean;
  /** Council: the models that tied at the top (kept even after a chairman resolves it). */
  tiedBest?: string[] | null;
  /** Council: set when a chairman broke a tie — the chairman model + its reasoning. */
  chairman?: { model: string; reasoning?: string | null } | null;
  /** Reason text for a non-'ok' status. */
  error?: string | null;
  /** Raw judge reply, kept for display when the verdict was unparseable. */
  raw?: string | null;
  ranAt?: string | null;
  /** Estimated total cost of the judging (all panel members + chairman), when
   *  the judge models carry prices. null when no cost could be computed. */
  judgeCost?: number | null;
}

interface ExperimentTrackerEntry {
  systemPrompt: string;
  userPrompt: string;
  variables?: PromptVariable[];
  manifest?: ManifestConfig;
  judge?: JudgeSummary | null;
  /** 'decision' for a decision-template run: systemPrompt holds the question
   *  map (JSON) and userPrompt the state template; absent = an LLM run. */
  mode?: 'llm' | 'decision';
  /** Decision runs: the expected answer per question id, when given. */
  expected?: Record<string, string>;
  [modelName: string]: unknown;
}

// Path data of the experiment tracker's icons (Material Symbols, 0 -960 960 960
// view box): the three per-run actions and the five per-response markers. Kept
// in one place because the tracker draws them and its legend explains them.
const TRACKER_ICON_PATHS = {
  load:
    'M440-320v-326L336-542l-56-58 200-200 200 200-56 58-104-104v326h-80ZM240-160q-33 0-56.5-23.5T160-240v-120h80v120h480v-120h80v120q0 33-23.5 56.5T680-160H240Z',
  remove:
    'M280-120q-33 0-56.5-23.5T200-200v-520h-40v-80h200v-40h240v40h200v80h-40v520q0 33-23.5 56.5T680-120H280Zm400-600H280v520h400v-520ZM360-280h80v-360h-80v360Zm160 0h80v-360h-80v360ZM280-720v520-520Z',
  judge:
    'M480-80v-520q-18-7-31.5-19T427-640H320l120 280q0 50-41 85t-99 35q-58 0-99-35t-41-85l120-280h-40v-80h227q11-19 29-31.5t42-14.5v-38h80v38q24 2 42 14.5t29 31.5h227v80h-40l120 280q0 50-41 85t-99 35q-58 0-99-35t-41-85l120-280H533q-8 9-21.5 21T480-600v520h160v80H320v-80h160ZM212-440h176l-88-205-88 205Zm360 0h176l-88-205-88 205ZM480-680q17 0 28.5-11.5T520-720q0-17-11.5-28.5T480-760q-17 0-28.5 11.5T440-720q0 17 11.5 28.5T480-680Z',
  best:
    'M200-160v-80h560v80H200Zm0-140-51-321q-2 0-4.5.5t-4.5.5q-25 0-42.5-17.5T80-680q0-25 17.5-42.5T140-740q25 0 42.5 17.5T200-680q0 7-1.5 13t-3.5 11l125 56 125-171q-11-8-18-21t-7-28q0-25 17.5-42.5T480-880q25 0 42.5 17.5T540-820q0 15-7 28t-18 21l125 171 125-56q-2-5-3.5-11t-1.5-13q0-25 17.5-42.5T820-740q25 0 42.5 17.5T880-680q0 25-17.5 42.5T820-620q-2 0-4.5-.5t-4.5-.5l-51 321H200Zm68-80h424l26-167-105 46-133-183-133 183-105-46 26 167Zm212 0Z',
  fastest:
    'm422-232 207-248H469l29-227-185 267h139l-30 208ZM320-80l40-280H160l360-520h80l-40 320h240L400-80h-80Zm151-390Z',
  fewestTokens:
    'M480-83 240-323l56-56 184 183 184-183 56 56L480-83Zm0-238L240-561l56-56 184 183 184-183 56 56-240 240Zm0-238L240-799l56-56 184 183 184-183 56 56-240 240Z',
  cheapest:
    'M480-80q-83 0-156-31.5T197-197q-54-54-85.5-127T80-480q0-83 31.5-156T197-763q54-54 127-85.5T480-880q83 0 156 31.5T763-763q54 54 85.5 127T880-480q0 83-31.5 156T763-197q-54 54-127 85.5T480-80Zm-38-152h68v-42q42-7 72-30t30-66q0-42-24-66t-84-45q-51-18-70.5-32T404-506q0-20 17-33t45-13q26 0 42 12.5t22 32.5l62-25q-9-28-32.5-49T508-611v-41h-68v41q-40 8-63.5 33T353-518q0 39 22.5 61.5T450-416q49 18 68.5 33.5T538-343q0 26-21 40t-49 14q-32 0-56-17.5T379-354l-64 26q13 44 40 69.5t87 33.5v41Z',
  judgeBest:
    'M280-120v-80h160v-124q-49-11-87.5-41.5T296-442q-75-9-125.5-65.5T120-640v-40q0-33 23.5-56.5T200-760h80v-80h400v80h80q33 0 56.5 23.5T840-680v40q0 76-50.5 132.5T664-442q-18 56-56.5 86.5T520-324v124h160v80H280Zm0-408v-152h-80v40q0 38 22 68.5t58 43.5Zm200 128q50 0 85-35t35-85v-240H360v240q0 50 35 85t85 35Zm200-128q36-13 58-43.5t22-68.5v-40h-80v152Zm-200-52Z',
};

/** A user-defined output variable parsed from the LLM's JSON response. */
interface PromptOutputVariable {
  name: string;
  description: string;
  type: 'string' | 'decimal';
  defaultValue: string;
}

/** Manifest configuration captured with a run so loading can restore it. */
interface ManifestConfig {
  integratedLLMCall: boolean;
  selectedOutputs: string[];
  outputVariables: PromptOutputVariable[];
}

/**
 * One entry of the job-owned `Prompt-Optimization-Tracker.json` on a
 * prompt-test model (see the Phase-3 design). The browser only reads it —
 * the optimization job is the sole writer. Every optimization run lives here,
 * next to the prompt's experiment tracker; no extra Model Manager models are
 * created for results.
 */
interface OptimizationTrackerEntry {
  optimizationId?: number;
  startedAt?: string;
  finishedAt?: string;
  status?: 'succeeded' | 'failed' | string;
  jobId?: string;
  targetModel?: string;
  datasetSource?: string;
  sampleCount?: number;
  optimizer?: string;
  metric?: string;
  judgeModel?: string | null;
  metricBefore?: number | null;
  metricAfter?: number | null;
  /** The prompt the optimisation started from. */
  baselinePrompt?: { systemPrompt?: string; userPrompt?: string } | null;
  trainSize?: number | null;
  validationSize?: number | null;
  /** Per-validation-example before/after — the run's evolution detail. */
  evaluations?: OptimizationEvaluation[] | null;
  optimizedPrompt?: {
    systemPrompt?: string;
    userPrompt?: string;
    variables?: PromptVariable[];
    /** The few-shot examples the optimizer selected (one object per demo). */
    demos?: Array<Record<string, string>>;
  } | null;
  /** Set when the job skipped the optimization phase (e.g. baseline-perfect). */
  skippedReason?: string | null;
  /** 'comparison' for a compare-targets run (Phase 4a); absent for optimize runs. */
  type?: string;
  /** Per-candidate results of a compare-targets run. */
  targets?: ComparisonTargetRow[] | null;
  /** Per-role call accounting the job records from the SCR responses. */
  usage?: { target?: OptimizationUsage; judge?: OptimizationUsage } | null;
  /** Legacy (pre-history releases): the separate prompt-test a run created. */
  producedPromptModelId?: string | null;
  datasetSnapshot?: string | null;
  error?: string | null;
}

/** What one role (target or judge LLM) spent during an optimization run. */
interface OptimizationUsage {
  calls?: number;
  promptTokens?: number;
  outputTokens?: number;
  runTime?: number;
}

/** One candidate target's result inside a compare-targets tracker entry. */
interface ComparisonTargetRow {
  model?: string;
  status?: 'scored' | 'unreachable' | string;
  quality?: number | null;
  /** Mean model run time per call in seconds (from the SCR contract). */
  avgLatency?: number | null;
  usage?: OptimizationUsage | null;
  /** Sweep rows (mode=sweep) additionally carry the per-candidate run. */
  metricBefore?: number | null;
  metricAfter?: number | null;
  skippedReason?: string | null;
  optimizedPrompt?: OptimizationTrackerEntry['optimizedPrompt'];
  /** Screening rows use the flat shape (response/correct/score); sweep rows
   *  use the before/after shape the single-run evaluations use. */
  evaluations?: Array<{
    inputs?: Record<string, string>;
    expected?: string;
    response?: string;
    correct?: boolean;
    score?: number;
    baselineResponse?: string;
    baselineCorrect?: boolean;
    baselineScore?: number;
    optimizedResponse?: string;
    optimizedCorrect?: boolean;
    optimizedScore?: number;
  }> | null;
  error?: string | null;
}

/** One validation example's before/after result inside a tracker entry. */
interface OptimizationEvaluation {
  inputs?: Record<string, string>;
  expected?: string;
  baselineResponse?: string;
  baselineCorrect?: boolean;
  baselineScore?: number;
  optimizedResponse?: string;
  optimizedCorrect?: boolean;
  optimizedScore?: number;
}

/** The outputs an integrated LLM call can return (mirroring the SCR contract). */
const DEFAULT_LLM_OUTPUTS = ['response', 'run_time', 'prompt_length', 'output_length'];

/** The model `function` value for a Prompt Builder prompt. Migrated from the
 *  legacy 'Prompting' value, which is still recognised when listing prompts. */
const PROMPT_FUNCTION = 'prompt template';
const LEGACY_PROMPT_FUNCTION = 'Prompting';
/** Server-side filter that surfaces prompt models of either function value. */
const PROMPT_FUNCTION_FILTER = `or(eq(function,'${PROMPT_FUNCTION}'),eq(function,'${LEGACY_PROMPT_FUNCTION}'))`;
/** Names an output variable must not use. */
const RESERVED_OUTPUT_NAMES = [...DEFAULT_LLM_OUTPUTS, 'parse_status'];

/** Coerce an unknown attribute value to a finite number, else null. */
function numOrNull(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/** Pull the cost/governance attributes out of a Model Manager model detail body. */
function extractCostAttributes(body: Record<string, unknown> | null): LLMCostAttributes {
  if (!body) return {};
  const str = (v: unknown) => (v === null || v === undefined || v === '' ? null : String(v));
  // What a decision model accepts travels as custom properties of the
  // registration (mdb writes contextLength, maxOptions and questionTypes);
  // the licence is one of its tags.
  const properties: Record<string, string> = {};
  if (Array.isArray(body.properties)) {
    (body.properties as Array<Record<string, unknown>>).forEach((property) => {
      if (property && typeof property.name === 'string') properties[property.name] = String(property.value ?? '');
    });
  }
  const tags = Array.isArray(body.tags) ? (body.tags as unknown[]).map(String) : [];
  const licence = tags.find((tag) => tag === 'Open-Source' || tag === 'Proprietary') ?? null;
  const questionTypes = properties.questionTypes
    ? (properties.questionTypes
        .split(',')
        .map((type) => type.trim())
        .filter((type): type is DecisionQuestionType => type === 'choice' || type === 'noul' || type === 'score'))
    : null;
  return {
    inputTokenCount: numOrNull(body.inputTokenCount),
    outputTokenCount: numOrNull(body.outputTokenCount),
    hostingCosts: numOrNull(body.hostingCosts),
    llmodelType: str(body.llmodelType),
    provider: str(body.provider),
    deploymentId: str(body.deploymentId),
    endPoint: str(body.endPoint),
    contextLength: numOrNull(properties.contextLength),
    maxOptions: numOrNull(properties.maxOptions),
    questionTypes: questionTypes && questionTypes.length > 0 ? questionTypes : null,
    licenseClass: licence ? (tags.includes('Apache-2') ? `${licence} (Apache-2)` : licence) : null,
  };
}

/**
 * Estimate the cost of one SCR call. Prefers per-token pricing
 * (inputTokenCount/outputTokenCount × token counts); falls back to per-second
 * hosting cost (hostingCosts × run_time), mirroring mdb's costPerCall
 * convention. Returns null when the model carries no usable prices.
 */
function computeCallCost(
  metrics: { prompt_length?: number | null; output_length?: number | null; run_time?: number | null },
  attrs?: LLMCostAttributes | null
): number | null {
  if (!attrs) return null;
  const inPrice = attrs.inputTokenCount;
  const outPrice = attrs.outputTokenCount;
  if (inPrice != null || outPrice != null) {
    const inTokens = Number(metrics.prompt_length) || 0;
    const outTokens = Number(metrics.output_length) || 0;
    return inTokens * (inPrice || 0) + outTokens * (outPrice || 0);
  }
  if (attrs.hostingCosts != null) {
    return (Number(metrics.run_time) || 0) * attrs.hostingCosts;
  }
  return null;
}

/** Format an estimated cost as a compact, unitless number for display. */
function formatCost(cost: number | null | undefined): string {
  if (cost === null || cost === undefined || Number.isNaN(cost)) return '';
  if (cost === 0) return '0';
  const abs = Math.abs(cost);
  const digits = abs >= 1 ? 2 : abs >= 0.01 ? 4 : 6;
  return cost.toFixed(digits).replace(/\.?0+$/, '');
}

/**
 * The `<sas-report>` embed for a model card's custom chart, mirroring mdb's
 * `_model_card_chart`: the host is the SAS Viya host, the reportUri is the
 * configured report path. Returns null when no report URI is configured (so
 * the chart attributes are simply omitted).
 */
function buildModelCardChart(viyaHost: string, reportUri: string | null | undefined): string | null {
  const uri = (reportUri ?? '').trim();
  if (!uri) return null;
  const host = (viyaHost || '').replace(/\/+$/, '');
  return `<sas-report url="${host}" reportUri="${uri}"></sas-report>`;
}

interface ModelExperimentData {
  best_prompt: boolean | null;
  fastest_prompt: boolean | null;
  fewest_tokens_prompt: boolean | null;
  /** Cheapest response in the run (lowest estimated cost); null when unknown. */
  cheapest_prompt: boolean | null;
  /** 1 = judge's best, 2, 3, … ; null = not judged. */
  judge_rank: number | null;
  /** Convenience flag for the judge's winner (judge_rank === 1). */
  judge_best: boolean | null;
  output_length: number | null;
  prompt_length: number | null;
  run_time: number | null;
  /** Estimated cost of this response; null when the model carries no prices. */
  cost: number | null;
  options: Record<string, unknown> | null;
  response: string;
}

interface PETRow {
  runId: number;
  systemPrompt: string;
  userPrompt: string;
  /** Variable definitions of the run; only set on the run's header row. */
  variables?: PromptVariable[] | null;
  /** Manifest configuration of the run; only set on the run's header row. */
  manifest?: ManifestConfig | null;
  /** 'decision' for a decision-template run; header row only. */
  mode?: string | null;
  /** Decision runs: the expected answers by question id; header row only. */
  expected?: Record<string, string> | null;
  model: string;
  options: string;
  response: string;
  run_time: number | null;
  prompt_length: number | null;
  output_length: number | null;
  best_prompt: boolean | number | null;
  fastest_prompt: boolean | null;
  fewest_tokens_prompt: boolean | null;
  /** Cheapest response in the run; per-model. */
  cheapest_prompt?: boolean | null;
  /** Estimated cost of this response; per-model. null when unpriced. */
  cost?: number | null;
  /** Per-model judge rank (1 = best); null when not judged. */
  judge_rank?: number | null;
  /** Per-model judge winner flag (persisted numeric 1/0 like best_prompt). */
  judge_best?: boolean | number | null;
  /** Run-level judge model, carried on the header row only. */
  judge_model?: string | null;
  /** Run-level judge confidence, carried on the header row only. */
  judge_confidence?: string | null;
  /** Run-level judge reasoning, carried on the header row only. */
  judge_reasoning?: string | null;
  /** Run-level judge config (0/1), carried on the header row only. */
  judge_include_self?: boolean | number | null;
  judge_auto?: boolean | number | null;
  /** Council fields, carried on the header row only. */
  judge_mode?: string | null;
  judge_panel?: string | null;
  judge_agreement?: string | null;
  /** Run-level estimated judging cost, carried on the header row only. */
  judge_cost?: number | null;
  /** Chairman that broke a tie (council), on the header row only. */
  judge_chairman_model?: string | null;
  judge_chairman_reasoning?: string | null;
  /** Council: per-judge ballots, nested on the header row (like variables/manifest). */
  judge_ballots?: JudgeBallot[] | null;
}

interface ModalText {
  modalTitle?: string;
  modalDescription?: string;
  nameLabel?: string;
  descriptionLabel?: string;
  closeButtonText?: string;
  saveButtonText?: string;
  nameRequiredText?: string;
  failedText?: string;
}

/**
 * Handle a click on a link that leaves the page - "Open in SAS Model
 * Manager", the link to a saved prompt-test, a link to a decision, the user
 * guide: a new tab where the browser allows one, the copied link where VA's
 * sandboxed iframe does not (see openExternalLink).
 */
function openModelManagerLink(
  event: MouseEvent,
  anchor: HTMLAnchorElement,
  interfaceText: PromptBuilderText
): void {
  openExternalLink(
    event,
    anchor,
    (interfaceText?.promptBuilderOpenInMMCopied as string) ?? 'Link copied — open it in a new tab'
  );
}

/**
 * Parse an SCR options string (`{key:value,key:value}`, unquoted) back into an
 * object. Values are coerced to number/boolean when they look like one, else
 * kept as strings — so non-numeric options (`reasoning_effort:medium`,
 * `API_KEY:OpenAI`, `normalize:true`) round-trip without breaking parsing.
 */
function parseScrOptionsString(optionsStr: string | null | undefined): Record<string, unknown> {
  // No prototype: an option named __proto__ is a field like any other.
  const result: Record<string, unknown> = Object.create(null);
  if (!optionsStr) return result;
  const inner = String(optionsStr).trim().replace(/^\{/, '').replace(/\}$/, '');
  if (inner.trim() === '') return result;
  inner.split(',').forEach((pair) => {
    const sep = pair.indexOf(':');
    if (sep === -1) return;
    const key = pair.slice(0, sep).trim();
    if (!key) return;
    const rawValue = pair.slice(sep + 1).trim().replace(/^["']|["']$/g, '');
    if (/^-?\d+(\.\d+)?$/.test(rawValue)) result[key] = Number(rawValue);
    else if (rawValue === 'true' || rawValue === 'false') result[key] = rawValue === 'true';
    else result[key] = rawValue;
  });
  return result;
}

/**
 * What the page around the Prompt Builder can ask of it once it is built.
 */
export interface PromptBuilderController {
  /**
   * Show the projects, prompt-tests and steps of a kind. Returns false when
   * the change is refused: while a run, an evaluation over a CAS table or an
   * optimization is in flight (a toast says so), or for a kind that is
   * switched off. Asking for the kind
   * that is already shown changes nothing and keeps every selection.
   */
  setKind(kind: PromptKind): boolean;
  currentKind(): PromptKind;
  /** Open the Setup step and bring it into view. */
  showSetup(): void;
}

/**
 * How the page around the Prompt Builder takes part in it.
 */
export interface PromptBuilderHooks {
  /** Which optional parts exist (see `features` in `src/config.ts`). */
  features: { decision: boolean; optimization: boolean };
  /**
   * The "What are you building?" block, placed at the top of the Setup card
   * as it is. Whoever hands it in owns the choice: it calls `setKind` when a
   * kind of template is chosen and is told through `onKindChanged` when the
   * builder changed kind by itself. Left out, the builder makes its own
   * selector for its own kinds.
   */
  kindSelector?: HTMLElement;
  /** The kind on show changed, or was confirmed (e.g. by opening a prompt-test of that kind). */
  onKindChanged?(kind: PromptKind): void;
  /** Called once, when the builder is complete and the controller can be used. */
  onReady?(controller: PromptBuilderController): void;
}

export async function buildPromptBuilder(
  definition: PromptBuilderConfig,
  paneID: string,
  interfaceText?: InterfaceText,
  hooks?: PromptBuilderHooks
): Promise<HTMLElement> {
    const promptBuilderObject = definition;
    const promptBuilderInterfaceText = (interfaceText?.promptBuilder ?? {}) as PromptBuilderText;
    const VIYA = getAppState().config.viyaHost;
    // The two optional parts. Without hooks the switches' own defaults apply.
    // With decision templates off, nothing of that kind is built, requested or
    // listed; with optimization off, the Optimize step does not exist.
    const decisionEnabled = hooks?.features.decision ?? true;
    const optimizationEnabled = hooks?.features.optimization ?? false;
    // Whether a model manifested here verifies the TLS certificate of the
    // container call it makes itself (option "Verify TLS certificates in
    // manifested models"). Only the literal false switches it off. It is
    // written into the score code and decides nothing the page does itself.
    const tlsVerificationOn = promptBuilderObject?.sslVerify !== false;

    // Experiment-tracker rows for THIS object instance. Kept in the closure
    // (not on window) so two prompt-builder panes don't clobber each other.
    let petRows: PETRow[] = [];
    // The runs SAS Model Manager holds for the open prompt-test could not be
    // read when it was opened (its contents or its tracker): the page shows
    // none of them, and a save would replace them with what the page holds.
    let storedRunsUnread = false;
    // Of those: the tracker was read, and is no list of runs this page can
    // read - opened again it is the same file.
    let storedRunsUnusable = false;

    const promptBuilderContainer = document.createElement('div');
    promptBuilderContainer.setAttribute('id', `${paneID}-obj-${promptBuilderObject?.id}`);

    // While a write to SAS Model Manager is out - the experiments, the
    // documentation, a manifest, a delete - nothing that changes which
    // prompt-test is open can be used (see createSelectionLock). Its block
    // holds the controls of the Setup card.
    const selectionLock = createSelectionLock(`${promptBuilderObject?.id}-selection`);

    // The page is a flow of steps; every section below lands in one of these
    // panes (see the assembly at the end). Created first so the functions that
    // move the user between steps can reach it. Setup is visited once, Build &
    // Test is the workbench the whole edit-run-read loop happens in, Finalize
    // manifests and always comes last. Optimize is a step of its own between
    // the two, and only exists when the deployment enables it - so the flow
    // has three steps or four, and Finalize is number three or four.
    const STEP_SETUP = 0;
    const STEP_BUILD = 1;
    const promptBuilderSteps = [
      { key: 'setup', label: `${promptBuilderInterfaceText?.promptBuilderStepSetup}` },
      { key: 'build', label: `${promptBuilderInterfaceText?.promptBuilderStepBuild}` },
    ];
    if (optimizationEnabled) {
      promptBuilderSteps.push({
        key: 'optimize',
        label: `${promptBuilderInterfaceText?.promptBuilderStepOptimize}`,
      });
    }
    // Evaluate replaces Optimize for a decision template: an evaluation over
    // labelled cases instead of DSPy. Both exist in the row; the prompt's kind
    // decides which one is shown (see applyPromptMode). Like Optimize, the
    // step and its pane exist only when their feature is switched on.
    if (decisionEnabled) {
      promptBuilderSteps.push({
        key: 'evaluate',
        label: `${promptBuilderInterfaceText?.promptBuilderStepEvaluate}`,
      });
    }
    promptBuilderSteps.push({
      key: 'finalize',
      label: `${promptBuilderInterfaceText?.promptBuilderStepFinalize}`,
    });
    const promptBuilderStepper = createStepper(
      `${paneID}-obj-${promptBuilderObject?.id}`,
      promptBuilderSteps,
      {
        back: `${promptBuilderInterfaceText?.promptBuilderStepBack}`,
        continue: `${promptBuilderInterfaceText?.promptBuilderStepContinue}`,
        navigation: `${promptBuilderInterfaceText?.promptBuilderStepNavigation}`,
      }
    );
    // -1 for a step that does not exist; every use checks for that.
    const STEP_EVALUATE = promptBuilderSteps.findIndex((step) => step.key === 'evaluate');
    const STEP_OPTIMIZE = promptBuilderSteps.findIndex((step) => step.key === 'optimize');
    if (STEP_EVALUATE >= 0) promptBuilderStepper.setVisible(STEP_EVALUATE, false);
    // The selected prompt-test's kind. An LLM prompt template is the default;
    // a decision template (marked as one in SAS Model Manager) swaps the
    // editors for the question designer, the LLMs for the decision models, the
    // tracker body for answers, Optimize for Evaluate and the manifest for
    // typed outputs. These are the two kinds this builder knows; a third kind
    // of the page (a RAG setup) is another builder and never a value here.
    let promptMode: PromptKind = 'llm';

    // Add the intro piece to the Prompt Builder. The heading stays for the
    // document outline only - the embedding report already titles the page.
    const promptBuilderHeader = document.createElement('h1');
    promptBuilderHeader.classList.add('visually-hidden');
    promptBuilderHeader.innerText = promptBuilderInterfaceText?.promptBuilderHeading as string;
    const promptBuilderDescription = document.createElement('p');
    promptBuilderDescription.innerText = promptBuilderInterfaceText?.promptBuilderDescription as string;

    // --- The kind of template: the first choice on the Setup card ------------
    // One explanation per kind. The choice filters the project and prompt
    // lists to that kind and decides what a new one becomes; the rest of the
    // page follows through applyPromptMode.
    //
    // The block itself comes from the page around this builder, which also
    // offers kinds this builder does not build and therefore owns the choice:
    // it calls setPromptKind (through the controller) and hears about a kind
    // this builder changed by itself through hooks.onKindChanged. Used on its
    // own, the builder makes a selector for its own kinds.
    const ownKindSelector: KindSelector | null = hooks?.kindSelector
      ? null
      : createKindSelector({
          idPrefix: `${promptBuilderObject?.id}`,
          kinds: decisionEnabled ? [{ kind: 'llm' }, { kind: 'decision' }] : [{ kind: 'llm' }],
          checked: promptMode,
          text: promptBuilderInterfaceText,
          onSelect: (kind) => {
            // Its cards are this builder's two kinds; a refused change puts
            // the mark back on the kind that is still shown.
            if (kind !== 'rag' && !setPromptKind(kind)) ownKindSelector?.setChecked(promptMode);
          },
        });
    const promptKindSelectorElement: HTMLElement = hooks?.kindSelector ?? ownKindSelector!.element;

    /**
     * Change to the other kind of template: other projects and other
     * prompt-tests, so both selections are dropped and the lists of the new
     * kind are shown.
     */
    function switchPromptKind(kind: PromptKind): void {
      // Nothing of the kind that is left may arrive late: neither the
      // prompt-tests of its project nor a prompt-test that was being opened.
      projectLoadToken += 1;
      promptBuilderProjectPrompts = [];
      promptBuilderDecisionPromptIds = new Set();
      promptBuilderProjectSelectorDropdown.value = `${promptBuilderInterfaceText?.projectSelect}`;
      deleteProjectButton.disabled = true;
      projectFilter.nameInput.value = '';
      promptFilter.nameInput.value = '';
      promptFilter.setUsers([]);
      // The picker is emptied before the reset: what the reset redraws asks
      // the picker which prompt-test is open (see the project picker).
      renderPromptOptions();
      resetExperimentTrackerState();
      dropPromptSelection();
      applyPromptMode(kind);
      renderProjectOptions();
      renderPromptOptions();
    }

    /**
     * The one way a person's choice of kind reaches this builder. Returns
     * whether the kind is shown afterwards.
     *
     * The kind already shown is left exactly as it is - project, prompt-test
     * and tracker included - so coming back to this builder from another part
     * of the page costs nothing. A change is refused while a run, an
     * evaluation over a CAS table or an optimization is in flight: it empties
     * the tracker and puts the other kind's steps on the page, while the
     * answers that are still on their way belong to what was there before.
     */
    function setPromptKind(kind: PromptKind): boolean {
      if (kind === promptMode) return true;
      if (kind === 'decision' && !decisionEnabled) return false;
      if (experimentRunning || evaluationRunning || optimizeJobActive) {
        showToast(`${promptBuilderInterfaceText?.promptBuilderKindChangeRefused}`);
        return false;
      }
      switchPromptKind(kind);
      return true;
    }

    // Add the project selection/creation
    const promptBuilderProjectHeader = document.createElement('h2');
    promptBuilderProjectHeader.innerText = promptBuilderInterfaceText?.promptBuilderProjectHeader as string;
    // Full project/prompt lists with their metadata; the dropdowns render a
    // filtered view of these, so long lists stay searchable.
    let promptBuilderAllProjects: DropdownOption[] = [];
    // The projects made for decision templates (tagged Decision-Engineering
    // beside Prompt-Engineering); the other view lists the rest.
    let promptBuilderDecisionProjectIds = new Set<string>();
    let promptBuilderProjectPrompts: DropdownOption[] = [];
    // The ids of the project's decision templates (tagged as such), so the
    // prompt list can show one kind at a time - the kind chosen on this card.
    let promptBuilderDecisionPromptIds = new Set<string>();

    // Select from existing projects
    const promptBuilderProjectSelectorHeader = document.createElement('h3');
    promptBuilderProjectSelectorHeader.innerText = `${promptBuilderInterfaceText?.projectSelect}:`;
    const promptBuilderProjectSelectorDropdown = document.createElement('select');
    promptBuilderProjectSelectorDropdown.setAttribute('class', 'form-select');
    promptBuilderProjectSelectorDropdown.setAttribute('id', `${promptBuilderObject?.id}-project-dropdown`);
    // Each choice in one of the two pickers starts a loading of its own, and
    // an earlier one may still be waiting for SAS Model Manager. Its answer
    // belongs to an entry that is no longer the selected one - drawn all the
    // same, the page showed the runs, the link and the Delete button of one
    // prompt-test under the name of another, and Save wrote those runs to the
    // one the picker named. So every loading takes a number and, after each
    // wait, goes on only while that number is still the latest.
    let projectLoadToken = 0;
    let promptLoadToken = 0;
    let promptDocLoadToken = 0;
    /**
     * No prompt-test is selected any more (another project or kind was
     * chosen, the prompt-test or its project was deleted): a prompt-test that
     * is still loading is not drawn, and the documentation section stops
     * belonging to the one that was open - its fields are emptied and Save
     * documentation is switched off, where it would have written what is
     * typed now to that earlier prompt-test.
     */
    function dropPromptSelection(): void {
      promptLoadToken += 1;
      void loadPromptDocumentation('');
    }
    promptBuilderProjectSelectorDropdown.onchange = async function () {
      const self = this as unknown as HTMLSelectElement;
      const loadToken = ++projectLoadToken;
      // Reset the prompt list and its filters. This comes first: the reset
      // below redraws the later steps, and the Optimize step asks the picker
      // which prompt-test is open. While the picker still named the
      // prompt-test of the project that is left, that step kept its
      // optimization history - with "Load as experiment", which filled
      // Build & Test from a prompt-test that was no longer open - and offered
      // Compare targets for it.
      promptBuilderProjectPrompts = [];
      promptFilter.nameInput.value = '';
      renderPromptOptions();
      // Reset the in-memory experiment state of the previously selected prompt
      resetExperimentTrackerState();
      dropPromptSelection();

      // Get the prompts from the selected projects
      const currentProject = self.options[self.selectedIndex].value;
      // Enable project deletion only for a real project selection
      deleteProjectButton.disabled = currentProject === `${promptBuilderInterfaceText?.projectSelect}`;
      // The placeholder entry is no project: its text must not be sent to
      // SAS Model Manager as a project id. The lists are empty by now.
      if (currentProject === `${promptBuilderInterfaceText?.projectSelect}`) {
        promptFilter.setUsers(promptBuilderProjectPrompts);
        return;
      }
      let allPrompts: Awaited<ReturnType<typeof getModelProjectModels>> = [];
      try {
        // One listing answers both questions - which prompt-tests there are
        // and which of them are decision templates - because every item
        // carries its algorithm and its tags.
        allPrompts = await getModelProjectModels(currentProject, PROMPT_FUNCTION_FILTER);
      } catch (error) {
        console.error('Failed to load prompts for the selected project.', error);
      }
      // Another project, the placeholder or the other kind was chosen while
      // the listing was out: these prompt-tests are not the ones to list.
      if (loadToken !== projectLoadToken) return;
      promptBuilderProjectPrompts = allPrompts;
      promptBuilderDecisionPromptIds = new Set(
        allPrompts.filter((prompt) => isDecisionTemplate(prompt)).map((prompt) => prompt.value)
      );
      promptFilter.setUsers(promptBuilderProjectPrompts);
      renderPromptOptions();
    };
    // Add the existing prompt selector
    const promptBuilderPromptHeader = document.createElement('h3');
    promptBuilderPromptHeader.innerText = `${promptBuilderInterfaceText?.promptSelect}:`;
    const promptBuilderPromptSelectorDropdown = document.createElement('select');
    promptBuilderPromptSelectorDropdown.setAttribute('class', 'form-select');
    promptBuilderPromptSelectorDropdown.setAttribute('id', `${promptBuilderObject?.id}-prompt-dropdown`);
    const promptBuilderPromptSelectorItem = document.createElement('option');
    promptBuilderPromptSelectorItem.value = `${promptBuilderInterfaceText?.promptSelect}`;
    promptBuilderPromptSelectorItem.innerHTML = `${promptBuilderInterfaceText?.promptSelect}`;
    promptBuilderPromptSelectorDropdown.append(promptBuilderPromptSelectorItem);

    // --- Optional prompt documentation -------------------------------------
    // Governance metadata (mirrors the mdb model-card fields), stored as the
    // selected prompt's SAS Model Manager attributes and editable for any
    // selected prompt. Collapsed by default; entirely optional.
    let currentDocPromptId = '';
    const promptDoc = createDocSection(`${promptBuilderObject?.id}`, {
      sectionLabel: promptBuilderInterfaceText?.promptBuilderDocSectionLabel as string,
      sectionHint: promptBuilderInterfaceText?.promptBuilderDocSectionHint as string,
      fields: {
        modelPurpose: {
          label: promptBuilderInterfaceText?.promptBuilderDocModelPurpose as string,
          info: promptBuilderInterfaceText?.promptBuilderDocModelPurposeInfo as string,
        },
        intendedUse: {
          label: promptBuilderInterfaceText?.promptBuilderDocIntendedUse as string,
          info: promptBuilderInterfaceText?.promptBuilderDocIntendedUseInfo as string,
        },
        expectedBenefit: {
          label: promptBuilderInterfaceText?.promptBuilderDocExpectedBenefit as string,
          info: promptBuilderInterfaceText?.promptBuilderDocExpectedBenefitInfo as string,
        },
        outOfScopeUseCases: {
          label: promptBuilderInterfaceText?.promptBuilderDocOutOfScope as string,
          info: promptBuilderInterfaceText?.promptBuilderDocOutOfScopeInfo as string,
        },
        limitations: {
          label: promptBuilderInterfaceText?.promptBuilderDocLimitations as string,
          info: promptBuilderInterfaceText?.promptBuilderDocLimitationsInfo as string,
        },
      },
    });
    const promptDocSection = promptDoc.section;
    const promptDocSaveButton = document.createElement('button');
    promptDocSaveButton.type = 'button';
    promptDocSaveButton.classList.add('btn', 'btn-outline-primary', 'btn-sm');
    promptDocSaveButton.innerText = promptBuilderInterfaceText?.promptBuilderSaveDocumentationButton as string;
    promptDocSaveButton.disabled = true;
    promptDocSaveButton.onclick = async () => {
      if (!currentDocPromptId) return;
      const attrs: Record<string, unknown> = { ...promptDoc.values() };
      const previousLabel = promptDocSaveButton.innerText;
      promptDocSaveButton.disabled = true;
      promptDocSaveButton.innerHTML = `<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>`;
      let status = 500;
      let failure: unknown = null;
      try {
        status = await selectionLock.during(() => updateModelAttributes(currentDocPromptId, attrs));
      } catch (error) {
        console.error('Failed to save prompt documentation.', error);
        failure = error;
      }
      promptDocSaveButton.innerText = previousLabel;
      promptDocSaveButton.disabled = false;
      showToast(
        status < 300
          ? (promptBuilderInterfaceText?.promptBuilderDocSaved as string)
          : noAnswerText(failure)
            ? `${promptBuilderInterfaceText?.promptBuilderSaveDocumentationButton}: ${noAnswerText(failure)}`
            : (promptBuilderInterfaceText?.promptBuilderDocSaveFailed as string)
      );
    };
    promptDocSection.appendChild(promptDocSaveButton);
    // Said in the section when the documentation could not be read: the
    // fields are empty then, and Save documentation is off.
    const promptDocUnread = document.createElement('p');
    promptDocUnread.classList.add('text-danger', 'small', 'mt-2', 'mb-0', 'pb-doc-unread');
    promptDocUnread.innerText = `${promptBuilderInterfaceText?.promptBuilderDocUnread}`;
    promptDocUnread.hidden = true;
    promptDocSection.appendChild(promptDocUnread);

    // Load the selected prompt's documentation into the fields (and migrate a
    // legacy `function` value to the current one, for the user). A read that
    // fails leaves the section empty, Save documentation off, and says so.
    async function loadPromptDocumentation(promptId: string): Promise<Record<string, unknown> | null> {
      const loadToken = ++promptDocLoadToken;
      currentDocPromptId = '';
      promptDoc.clear();
      promptDocSaveButton.disabled = true;
      promptDocUnread.hidden = true;
      if (!promptId || promptId === `${promptBuilderInterfaceText?.promptSelect}`) return null;
      let details: Record<string, unknown> | null = null;
      try {
        details = await getModelDetails(promptId);
      } catch (error) {
        console.error('Failed to load prompt documentation.', error);
      }
      // A later call has emptied the section, or is filling it for another
      // prompt-test: this answer must not arm Save documentation for the one
      // that was left.
      if (loadToken !== promptDocLoadToken) return null;
      if (!details) {
        promptDocUnread.hidden = false;
        return null;
      }
      promptDoc.setValues(details);
      currentDocPromptId = promptId;
      promptDocSaveButton.disabled = false;
      if (details.function === LEGACY_PROMPT_FUNCTION) {
        // Not behind the selection lock: it sets one fixed value on the id
        // this call was made for, and reads nothing of the page afterwards.
        try {
          await updateModelAttributes(promptId, { function: PROMPT_FUNCTION });
        } catch {
          /* best-effort migration */
        }
      }
      return details;
    }
    /**
     * A prompt-test is a decision template when its registration says so -
     * read from the model itself or from its entry in a project listing.
     *
     * The algorithm is the mark that counts: SAS Model Manager keeps it from
     * the creation request, while it drops the tags sent with that request.
     * The tag is still honoured for a registration that carries it.
     */
    function isDecisionTemplate(registration: Record<string, unknown> | null): boolean {
      if (!registration) return false;
      const tags = Array.isArray(registration.tags) ? (registration.tags as unknown[]).map(String) : [];
      return registration.algorithm === DECISION_TEMPLATE_ALGORITHM || tags.includes(DECISION_TEMPLATE_TAG);
    }

    promptBuilderPromptSelectorDropdown.onchange = async function () {
      const self = this as unknown as HTMLSelectElement;
      // Whether this is still the latest choice (see promptLoadToken): asked
      // after every wait, before anything of this prompt-test is drawn.
      const loadToken = ++promptLoadToken;
      const superseded = (): boolean => loadToken !== promptLoadToken;
      // Reset the in-memory experiment state of the previously selected
      // prompt, and take that prompt-test out of the later steps.
      const startedAfresh = resetExperimentTrackerState();
      const promptBuilderPromptSelectedModelID = self.options[self.selectedIndex].value;
      // Load the prompt's documentation (and migrate a legacy function value);
      // the same registration says which kind of template this is.
      const promptDetails = await loadPromptDocumentation(promptBuilderPromptSelectedModelID);
      if (superseded()) return;
      // The placeholder entry is no prompt-test: its text must not be sent to
      // SAS Model Manager as a model id, nor become a link. The reset above
      // has emptied the tracker and switched the link and Delete off.
      if (promptBuilderPromptSelectedModelID === `${promptBuilderInterfaceText?.promptSelect}`) return;
      // With decision templates switched off the list does not offer one, so
      // this is a template selected some other way. It is not opened: say why,
      // go back to the placeholder and leave nothing of it on the page.
      if (promptDetails && !decisionEnabled && isDecisionTemplate(promptDetails)) {
        showToast(`${promptBuilderInterfaceText?.promptBuilderDecisionSwitchedOff}`);
        self.value = `${promptBuilderInterfaceText?.promptSelect}`;
        renderPromptOptions();
        await loadPromptDocumentation(self.value);
        if (superseded()) return;
        resetExperimentTrackerState();
        return;
      }
      // The list shows one kind, so this only differs when a prompt was opened
      // some other way; the registration decides, and the selector follows.
      if (promptDetails) applyPromptMode(isDecisionTemplate(promptDetails) ? 'decision' : 'llm');
      // Get the ID of a previously created Prompt Experiment Tracker and delete it
      let promptBuilderAvailablePTE: Awaited<ReturnType<typeof getModelContents>> = [];
      let contentsRead = true;
      try {
        promptBuilderAvailablePTE = await getModelContents(promptBuilderPromptSelectedModelID);
      } catch (error) {
        console.error('Failed to load model contents for the selected prompt.', error);
        contentsRead = false;
      }
      if (superseded()) return;
      storedRunsUnread = !contentsRead;
      for (const promptBuilderAvailablepte in promptBuilderAvailablePTE) {
        if (promptBuilderAvailablePTE[promptBuilderAvailablepte]?.name === 'Prompt-Experiment-Tracker.json') {
          // A tracker that cannot be parsed must not abort the handler — the
          // Model Manager link and delete buttons below still have to work.
          // A tracker that was read and holds no runs this page can read
          // (StoredFileUnusable, see readStoredJson) stays so when it is
          // opened again.
          let trackerRead = false;
          try {
            // Reset the prompt tracker to nothing
            promptExperimentTrackerRunID = 0;
            let promptBuilderCurrentPTEContent: PETRow[];
            try {
              promptBuilderCurrentPTEContent = await readStoredJson<PETRow[]>(
                promptBuilderAvailablePTE[promptBuilderAvailablepte].fileUri!,
                'Prompt-Experiment-Tracker.json'
              );
            } catch (error) {
              trackerRead = error instanceof StoredFileUnusable;
              throw error;
            }
            if (superseded()) return;
            trackerRead = true;
            const promptBuilderPreviousExperiment: ExperimentTrackerEntry[] = [];
            let promptBuilderPreviousRunID = 0;
            promptBuilderCurrentPTEContent.forEach((value) => {
              if (value.runId !== promptBuilderPreviousRunID) {
                const loadedRun: ExperimentTrackerEntry = {
                  systemPrompt: value.systemPrompt,
                  userPrompt: value.userPrompt,
                };
                if (Array.isArray(value.variables)) loadedRun.variables = value.variables;
                if (value.manifest) loadedRun.manifest = value.manifest;
                if (value.mode === 'decision') loadedRun.mode = 'decision';
                if (value.expected && typeof value.expected === 'object') loadedRun.expected = value.expected;
                // Only 'ok' judgments persist judge state on the header row
                // (a single judge sets judge_model; a council sets judge_mode).
                // best/ranking/tie are reconstructed below.
                if (value.judge_model || value.judge_mode === 'council') {
                  const isCouncil = value.judge_mode === 'council';
                  loadedRun.judge = {
                    judgeModel: value.judge_model ?? '',
                    status: 'ok',
                    mode: isCouncil ? 'council' : 'single',
                    confidence: (value.judge_confidence as JudgeConfidence) ?? 'unknown',
                    reasoning: value.judge_reasoning ?? '',
                    includeSelf: Boolean(value.judge_include_self),
                    autoJudge: Boolean(value.judge_auto),
                    judgeCost: value.judge_cost ?? null,
                    ranking: null,
                    best: null,
                  };
                  if (isCouncil) {
                    loadedRun.judge.method = 'borda';
                    loadedRun.judge.panel =
                      typeof value.judge_panel === 'string' && value.judge_panel
                        ? value.judge_panel.split(',').map((name) => name.trim()).filter(Boolean)
                        : [];
                    loadedRun.judge.ballots = Array.isArray(value.judge_ballots) ? value.judge_ballots : [];
                    if (value.judge_chairman_model) {
                      loadedRun.judge.chairman = {
                        model: value.judge_chairman_model,
                        reasoning: value.judge_chairman_reasoning ?? '',
                      };
                    }
                  }
                }
                promptBuilderPreviousExperiment.push(loadedRun);
                promptBuilderPreviousRunID = value.runId;
              } else {
                // Index the last pushed run: persisted runIds can have gaps
                // (a run whose experiments all failed produces no rows), so
                // runId - 1 is not a safe array position.
                const run = promptBuilderPreviousExperiment[promptBuilderPreviousExperiment.length - 1];
                // A decision run has one Best Decision Model: a run stored
                // with more than one keeps the first, the one Manifest took.
                const bestBefore =
                  run.mode === 'decision' &&
                  Object.keys(run).some((key) => !TRACKER_META_KEYS.includes(key) && (run[key] as ModelExperimentData)?.best_prompt);
                (run as Record<string, unknown>)[value?.model] = {
                  best_prompt: bestBefore && value?.best_prompt ? 0 : value?.best_prompt,
                  fastest_prompt: value?.fastest_prompt ?? false,
                  fewest_tokens_prompt: value?.fewest_tokens_prompt ?? false,
                  cheapest_prompt: value?.cheapest_prompt ?? false,
                  judge_rank: value?.judge_rank ?? null,
                  judge_best: value?.judge_best ?? null,
                  output_length: value?.output_length,
                  prompt_length: value?.prompt_length,
                  run_time: value?.run_time,
                  cost: value?.cost ?? null,
                  options: parseScrOptionsString(value?.options),
                  response: value?.response,
                };
              }
            });
            // Reconstruct each judged run's aggregate. A council re-runs the
            // (pure, deterministic) Borda aggregation over its persisted ballots
            // — restoring winner/ranking/tie/agreement/confidence exactly. A
            // single judge rebuilds its ranking from the per-model ranks.
            promptBuilderPreviousExperiment.forEach((loadedRun) => {
              if (!loadedRun.judge) return;
              const modelNames = Object.keys(loadedRun).filter((key) => !TRACKER_META_KEYS.includes(key));
              if (loadedRun.judge.mode === 'council') {
                const okBallots = (loadedRun.judge.ballots ?? []).filter(
                  (ballot) => ballot.status === 'ok' && Array.isArray(ballot.ranking) && ballot.ranking.length > 0
                );
                const result = aggregateBallots(modelNames, okBallots);
                loadedRun.judge.ranking = result.ranking;
                loadedRun.judge.tiedBest = result.tiedBest;
                loadedRun.judge.agreement = result.agreement;
                loadedRun.judge.confidence = result.confidence;
                // A chairman-resolved tie: the persisted per-model judge_best is
                // the chairman's winner, so honour it over the raw aggregate tie.
                const resolvedWinner = modelNames.find(
                  (modelName) => (loadedRun[modelName] as ModelExperimentData)?.judge_best
                );
                if (result.tie && loadedRun.judge.chairman && resolvedWinner) {
                  loadedRun.judge.best = resolvedWinner;
                  loadedRun.judge.tie = false;
                } else {
                  loadedRun.judge.best = result.best;
                  loadedRun.judge.tie = result.tie;
                }
                return;
              }
              const ranked = modelNames
                .map((modelName) => ({
                  modelName,
                  rank: (loadedRun[modelName] as ModelExperimentData)?.judge_rank,
                }))
                .filter((entry): entry is { modelName: string; rank: number } => entry.rank != null)
                .sort((a, b) => a.rank - b.rank);
              loadedRun.judge.ranking = ranked.map((entry) => entry.modelName);
              loadedRun.judge.best = ranked[0]?.modelName ?? null;
              // Rebuild the self-inclusion note flags from the stored config.
              const judgeSelfPresent = modelNames.includes(loadedRun.judge.judgeModel);
              loadedRun.judge.includedSelf = Boolean(loadedRun.judge.includeSelf) && judgeSelfPresent;
              loadedRun.judge.excludedSelf = !loadedRun.judge.includeSelf && judgeSelfPresent;
            });
            // Assign the tracker before rendering so the saveable rows are rebuilt
            // from the freshly loaded runs (the render reads the closure variable).
            promptExperimentTracker = [...promptBuilderPreviousExperiment];
            createPromptExperimentTracker(promptExperimentTracker);
            // Bring the most recent best prompt straight into the workbench.
            // Where nothing was open before, the later steps may hold what
            // was typed there: it is kept for a prompt-test that brings no
            // run, and makes way for the run of one that does.
            loadMostRecentBestRun(!startedAfresh);
          } catch (error) {
            console.error('Failed to load the Prompt-Experiment-Tracker for the selected prompt.', error);
            if (!superseded()) {
              storedRunsUnread = true;
              storedRunsUnusable = trackerRead;
            }
          }
        }
      }
      if (storedRunsUnread && !superseded()) {
        showStoredRunsUnread(promptExperimentSaveResultContainer);
        showToast(storedRunsText());
      }
      // The prompt-test is on the page: from here on the later steps show it
      // - also a new one, which brought nothing and is filled by hand - and
      // are started afresh when it is left. Not earlier: while it was being
      // read nothing of it was drawn, so what the steps held, typed with no
      // prompt-test open, was still nobody's, and a second choice made in
      // that time must not take it away.
      laterStepsShowPromptTest = true;
      // Enable prompt deletion only for a real prompt selection
      deletePromptButton.disabled =
        promptBuilderPromptSelectedModelID === `${promptBuilderInterfaceText?.promptSelect}`;
      // Activate link to SAS Model Manager
      const tmpOpenInMMButton = document.getElementById(`${promptBuilderObject?.id}-openInMMButton`) as HTMLAnchorElement | null;
      if (tmpOpenInMMButton) {
        tmpOpenInMMButton.href = `${VIYA}/SASModelManager/models/${promptBuilderPromptSelectedModelID}/files`;
        tmpOpenInMMButton.classList.remove('disabled');
        tmpOpenInMMButton.removeAttribute('aria-disabled');
        tmpOpenInMMButton.onclick = (event) =>
          openModelManagerLink(event, tmpOpenInMMButton, promptBuilderInterfaceText);
      }
    };

    // Name + user filters for the two selection lists. The lists can get very
    // long, so each dropdown only renders the matching entries — filtering by
    // name and by who created or last modified an entry. The current selection
    // always stays in the list.
    const projectFilter = createListFilter(`${promptBuilderObject?.id}-project-filter`, {
      namePlaceholder: `${promptBuilderInterfaceText?.promptBuilderFilterNamePlaceholder}`,
      userLabel: `${promptBuilderInterfaceText?.promptBuilderFilterUserLabel}`,
      userAll: `${promptBuilderInterfaceText?.promptBuilderFilterUserAll}`,
    }, () => renderProjectOptions());
    const promptFilter = createListFilter(`${promptBuilderObject?.id}-prompt-filter`, {
      namePlaceholder: `${promptBuilderInterfaceText?.promptBuilderFilterNamePlaceholder}`,
      userLabel: `${promptBuilderInterfaceText?.promptBuilderFilterUserLabel}`,
      userAll: `${promptBuilderInterfaceText?.promptBuilderFilterUserAll}`,
    }, () => renderPromptOptions());
    /** The projects of the chosen kind. */
    function projectsOfCurrentKind(): DropdownOption[] {
      return promptBuilderAllProjects.filter(
        (project) => promptBuilderDecisionProjectIds.has(project.value) === (promptMode === 'decision')
      );
    }
    function renderProjectOptions(): void {
      renderFilteredOptions(
        promptBuilderProjectSelectorDropdown,
        projectsOfCurrentKind(),
        projectFilter.nameInput,
        projectFilter.userSelect,
        `${promptBuilderInterfaceText?.projectSelect}`
      );
    }
    /** The project's prompt-tests of the chosen kind. */
    function promptsOfCurrentKind(): DropdownOption[] {
      return promptBuilderProjectPrompts.filter(
        (prompt) => promptBuilderDecisionPromptIds.has(prompt.value) === (promptMode === 'decision')
      );
    }
    function renderPromptOptions(): void {
      renderFilteredOptions(
        promptBuilderPromptSelectorDropdown,
        promptsOfCurrentKind(),
        promptFilter.nameInput,
        promptFilter.userSelect,
        `${promptBuilderInterfaceText?.promptSelect}`
      );
    }

    // Get all projects in the specified repository and render the filterable
    // list. The second listing is asked for even with decision templates
    // switched off: a decision project carries Prompt-Engineering too, and
    // knowing which ones they are is what keeps them out of the LLM list.
    {
      const [allProjects, decisionProjects] = await Promise.all([
        getModelProjects(`contains(tags,'Prompt-Engineering')`),
        getModelProjects(`contains(tags,'${DECISION_PROJECT_TAG}')`),
      ]);
      promptBuilderAllProjects = allProjects;
      promptBuilderDecisionProjectIds = new Set(decisionProjects.map((project) => project.value));
    }
    projectFilter.setUsers(promptBuilderAllProjects);
    renderProjectOptions();
    renderPromptOptions();

    // Add the creation prompt buttons and modals. The row is a flex container
    // so the destructive actions can sit right-aligned, away from the rest;
    // where it wraps (a narrow report object), the row gap keeps the second
    // line of buttons off the first.
    const promptBuilderModalButtonContainer = document.createElement('div');
    promptBuilderModalButtonContainer.setAttribute('id', `${promptBuilderObject?.id}-modal-button-container`);
    promptBuilderModalButtonContainer.classList.add('d-flex', 'flex-wrap', 'align-items-center', 'row-gap-2');

    // Function to call when creating a new project. The dialog keeps its
    // button and its fields disabled until this has ended, and does not call
    // it at all for a name that shows nothing (see createCreateModal). The
    // name and the description are the ones the dialog read at the click:
    // the repository is asked first, and the name field read after that
    // answer could be another one than the dialog had checked - an empty one.
    async function promptBuilderCreateProject(dialog: CreateModalDialog, entry: CreateModalEntry): Promise<void> {
      // The repository names the folder the project goes into. When it cannot
      // be read - no connection, an error page where JSON was expected, a
      // refusal - nothing has been created, and the dialog says so. Left to
      // itself the error ended the click without a word.
      let promptBuilderRepositoryInformation: Awaited<ReturnType<typeof getModelRepositoryInformation>>;
      try {
        promptBuilderRepositoryInformation = await getModelRepositoryInformation(promptBuilderObject?.modelRepositoryID as string);
      } catch (error) {
        console.error('Reading the model repository failed; no project was created.', error);
        dialog.showError(`${promptBuilderInterfaceText?.promptBuilderCreateProjectLookupFailed}`);
        return;
      }
      const promptBuilderNewProjectDefinition = {
        name: entry.name,
        description: entry.description,
        function: 'Prompt',
        repositoryId: promptBuilderObject?.modelRepositoryID as string,
        folderId: promptBuilderRepositoryInformation?.folderId,
        properties: [
          {
            name: 'Origin',
            value: 'Prompt Builder',
            type: 'string',
          },
        ],
        // A decision project keeps Prompt-Engineering too, so everything that
        // scans that tag (reporting, the delete checks) still finds it.
        tags: promptMode === 'decision' ? ['Decision', 'Prompt-Engineering', DECISION_PROJECT_TAG] : ['LLM', 'Prompt-Engineering'],
      };
      // A refusal (a name that exists, no permission to write) ends here: the
      // dialog stays open with its button usable, and nothing is listed.
      let promptBuilderNewProjectObject: Awaited<ReturnType<typeof createModelProject>>;
      try {
        promptBuilderNewProjectObject = await createModelProject(promptBuilderNewProjectDefinition);
        if (typeof promptBuilderNewProjectObject?.id !== 'string' || promptBuilderNewProjectObject.id === '') {
          throw new Error('the service answered without a project id');
        }
      } catch (error) {
        console.error('Creating the project failed.', error);
        // A creation without an answer was read back (see createModelProject).
        const said = noAnswerText(error) || `${promptBuilderInterfaceText?.promptBuilderCreateProjectFailed}`;
        showToast(said);
        dialog.showError(said);
        return;
      }
      if (promptMode === 'decision') promptBuilderDecisionProjectIds.add(`${promptBuilderNewProjectObject?.id}`);
      promptBuilderAllProjects.push({
        value: `${promptBuilderNewProjectObject?.id}`,
        innerHTML: `${promptBuilderNewProjectObject?.name}`,
        createdBy: promptBuilderNewProjectObject?.createdBy ?? getAppState().userName ?? undefined,
        modifiedBy: promptBuilderNewProjectObject?.modifiedBy ?? getAppState().userName ?? undefined,
      });
      // Clear the filters so the new project is visible, then select it
      projectFilter.nameInput.value = '';
      projectFilter.userSelect.value = '';
      projectFilter.setUsers(promptBuilderAllProjects);
      renderProjectOptions();
      // Set the newly created project as the currently selected project
      promptBuilderProjectSelectorDropdown.value = `${promptBuilderNewProjectObject?.id}`;
      promptBuilderProjectSelectorDropdown.dispatchEvent(new Event('change'));
      dialog.close();
    }

    // Function to call when creating a new prompt
    async function promptBuilderCreatePrompt(dialog: CreateModalDialog, entry: CreateModalEntry): Promise<void> {
      const newPromptIsDecision = promptMode === 'decision';
      const newPromptTags = newPromptIsDecision ? ['Decision', 'Prompt-Template', DECISION_TEMPLATE_TAG] : ['LLM', 'Prompt-Template'];
      const promptBuilderNewPromptDefinition = {
        name: entry.name,
        description: entry.description,
        function: PROMPT_FUNCTION,
        tool: 'Prompt-Builder',
        modeler: getAppState().userName,
        projectId: promptBuilderProjectSelectorDropdown.options[promptBuilderProjectSelectorDropdown.selectedIndex].value,
        // The kind chosen on the Setup card is carried by the registration.
        // The algorithm is what any Prompt Builder recognises it by: it is
        // kept from this request and listed with the project's models.
        algorithm: newPromptIsDecision ? DECISION_TEMPLATE_ALGORITHM : 'Prompt-Template',
        tags: newPromptTags,
        scoreCodeType: 'python',
      };
      // A refusal (a name that exists in the project, no permission to write)
      // ends here: the dialog stays open with its button usable, and nothing
      // is listed or selected. An answer without an id is treated the same -
      // going on would list, select and write to a prompt-test "undefined".
      let newPrompt: Record<string, unknown>;
      try {
        const promptBuilderNewPromptObject = await createModel(promptBuilderNewPromptDefinition);
        // The service answers with a collection holding the one new model.
        newPrompt = promptBuilderNewPromptObject?.items?.[0] ?? promptBuilderNewPromptObject;
        if (typeof newPrompt?.id !== 'string' || newPrompt.id === '') {
          throw new Error('the service answered without a model id');
        }
      } catch (error) {
        console.error('Creating the prompt-test failed.', error);
        // A creation without an answer was read back (see createModel).
        const said = noAnswerText(error) || `${promptBuilderInterfaceText?.promptBuilderCreatePromptFailed}`;
        showToast(said);
        dialog.showError(said);
        return;
      }
      const newPromptID = `${newPrompt.id}`;
      // SAS Model Manager drops the tags of a creation request, so they are
      // written again when the answer does not carry them. Best-effort: the
      // tags serve reporting and searching in Model Manager, the kind does
      // not depend on them, so a failure here must not fail the creation.
      const keptTags = Array.isArray(newPrompt?.tags) ? (newPrompt.tags as unknown[]).map(String) : [];
      if (newPromptTags.some((tag) => !keptTags.includes(tag))) {
        try {
          const tagStatus = await updateModelTags(newPromptID, [], newPromptTags);
          if (tagStatus >= 300) console.warn(`The tags of the new prompt-test could not be written (status ${tagStatus}).`);
        } catch (error) {
          console.warn('The tags of the new prompt-test could not be written.', error);
        }
      }
      if (newPromptIsDecision) promptBuilderDecisionPromptIds.add(newPromptID);
      promptBuilderProjectPrompts.push({
        value: newPromptID,
        innerHTML: `${newPrompt?.name}`,
        createdBy: (newPrompt?.createdBy as string | undefined) ?? getAppState().userName ?? undefined,
        modifiedBy: (newPrompt?.modifiedBy as string | undefined) ?? getAppState().userName ?? undefined,
        algorithm: promptBuilderNewPromptDefinition.algorithm,
        tags: newPromptTags,
      });
      // Clear the filters so the new prompt is visible, then select it
      promptFilter.nameInput.value = '';
      promptFilter.userSelect.value = '';
      promptFilter.setUsers(promptBuilderProjectPrompts);
      renderPromptOptions();
      // Set the newly created project as the currently selected project
      promptBuilderPromptSelectorDropdown.value = newPromptID;
      promptBuilderPromptSelectorDropdown.dispatchEvent(new Event('change'));
      dialog.close();
    }

    // Whether the steps after Setup show a prompt-test: set once the one
    // chosen in the picker (or created, which chooses it) has been read and
    // drawn, cleared when the steps are started afresh. While it is not set,
    // what those steps hold was typed on a page with no prompt-test open and
    // belongs to none.
    let laterStepsShowPromptTest = false;

    /**
     * The steps after Setup as the page showed them when it was loaded: every
     * field back to the value it had then (the defaults - a temperature from
     * the model's options, a judge model from the report options - not
     * blanks), every explanation open or folded as it was then, no variable,
     * no question, no result of an evaluation or an optimization.
     *
     * This is the one place that takes a prompt-test out of those steps. The
     * steps keep everything in the DOM, and opening a prompt-test only ever
     * wrote its best run into them (loadExperimentRun): a prompt-test that
     * brought no run - a new one above all - was shown with the prompts, the
     * variables, the models and options, the judges and the questions of the
     * one that had been open before it (issue #47). It is called for both
     * kinds of template, which share these steps, whenever a prompt-test is
     * left (resetExperimentTrackerState) and before the best run of one is
     * shown on steps that hold something typed without a prompt-test.
     *
     * The tracker and the manifest panel are not its business: they are
     * emptied with the experiment state, for every change of the selection.
     */
    function startLaterStepsAfresh(): void {
      laterStepsShowPromptTest = false;
      // Rows are fields that did not exist when the page was built.
      setPromptVariables([]);
      const restored = promptBuilderStepper.restoreFreshState();
      // A tick box or a choice of two shows and hides what depends on it
      // (the options of an LLM, the council, the CAS pickers): each one that
      // changed tells its listeners. The lists are left alone - some of
      // theirs load from the server.
      restored.forEach((field) => {
        if (field instanceof HTMLInputElement && (field.type === 'checkbox' || field.type === 'radio')) {
          field.dispatchEvent(new Event('change'));
        }
      });
      // The judge model is a list: what it shows and hides is redrawn here.
      updateJudgeControlsVisibility();
      // The designer takes out its questions, closes the help a "?" had
      // opened and empties the line beside its list of templates; it redraws
      // its lint and tells the model cards and the Run button.
      decisionDesigner.startAfresh();
      updateRunExperimentsButtonState();
      clearEvaluation();
      clearOptimizeRun();
    }

    // Clear all in-memory experiment state and deactivate the prompt-bound
    // actions. Used when the project/prompt selection changes and after a
    // prompt or project was deleted. A prompt-test that was open is taken out
    // of the later steps as well; the answer says whether that was done.
    function resetExperimentTrackerState(): boolean {
      const prommpExperimentTargetContainer = document.getElementById(`${paneID}-obj-${promptBuilderObject?.id}-pet`);
      if (prommpExperimentTargetContainer) prommpExperimentTargetContainer.innerHTML = '';
      promptExperimentTracker = [];
      promptExperimentTrackerRunID = 0;
      petRows = [];
      storedRunsUnread = false;
      storedRunsUnusable = false;
      experimentsModified = false;
      promptExperimentResultContainer.innerHTML = '';
      promptExperimentSaveResultContainer.innerHTML = '';
      // The line under Run Experiments names the models that failed in, or
      // were left out of, the last run: it goes with the runs.
      promptBuilderRunExperimentError.innerText = '';
      // Reset the manifest panel to its defaults; loading a run afterwards
      // re-applies that run's stored configuration
      applyManifestConfig(null);
      const startedAfresh = laterStepsShowPromptTest;
      if (startedAfresh) startLaterStepsAfresh();
      updateTrackerEmptyState();
      updateManifestButtonState();
      openInMMButton.classList.add('disabled');
      openInMMButton.setAttribute('aria-disabled', 'true');
      openInMMButton.removeAttribute('href');
      openInMMButton.onclick = null;
      deletePromptButton.disabled = true;
      return startedAfresh;
    }

    // Build the confirmation-modal body describing which SAS Intelligent
    // Decisioning decisions use a prompt. null means the check itself failed;
    // the user is warned but can still make an explicit choice.
    function buildUsageBody(decisions: DependentDecision[] | null): (HTMLElement | string)[] {
      if (decisions === null) {
        return [`${promptBuilderInterfaceText?.promptBuilderDeleteUsageCheckFailed}`];
      }
      if (decisions.length === 0) {
        return [`${promptBuilderInterfaceText?.promptBuilderDeleteNoUsage}`];
      }
      const decisionList = document.createElement('ul');
      decisions.forEach((decision) => {
        const decisionListItem = document.createElement('li');
        const decisionLink = document.createElement('a');
        decisionLink.href = `${VIYA}/SASDecisionManager/decisions/${decision.id}`;
        decisionLink.setAttribute('target', '_blank');
        decisionLink.setAttribute('rel', 'noopener noreferrer');
        decisionLink.textContent = decision.name;
        decisionLink.onclick = (event) => openModelManagerLink(event, decisionLink, promptBuilderInterfaceText);
        decisionListItem.appendChild(decisionLink);
        decisionList.appendChild(decisionListItem);
      });
      return [
        `${decisions.length} ${promptBuilderInterfaceText?.promptBuilderDeleteUsageFound}`,
        decisionList,
      ];
    }

    // Check whether any decisions use the model; null signals that the check
    // failed (e.g. the relationships service is unavailable) rather than that
    // no usage was found.
    async function checkModelDecisionUsage(modelID: string): Promise<DependentDecision[] | null> {
      try {
        return await getModelDependentDecisions(modelID);
      } catch (error) {
        console.error('Failed to check decision usage for the prompt.', error);
        return null;
      }
    }

    // Function to call when deleting the selected prompt
    async function promptBuilderDeletePrompt(): Promise<void> {
      const promptSelectedIndex = promptBuilderPromptSelectorDropdown.selectedIndex;
      const promptModelID = promptBuilderPromptSelectorDropdown.value;
      if (promptModelID === `${promptBuilderInterfaceText?.promptSelect}`) return;
      const promptModelName = promptBuilderPromptSelectorDropdown.options[promptSelectedIndex].text;
      deletePromptButton.disabled = true;
      deletePromptButton.innerHTML = `<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> ${promptBuilderInterfaceText?.promptBuilderDeletePromptButton}`;
      try {
        const decisions = await checkModelDecisionUsage(promptModelID);
        // Another prompt-test was opened while the usage was asked: no dialog
        // about one that is no longer on the page (as in the RAG Builder).
        if (promptBuilderPromptSelectorDropdown.value !== promptModelID) return;
        const confirmed = await showConfirmModal({
          title: `${promptBuilderInterfaceText?.promptBuilderDeletePromptTitle} ${promptModelName}`,
          body: buildUsageBody(decisions),
          confirmText: `${promptBuilderInterfaceText?.promptBuilderDeleteConfirmButton}`,
          cancelText: `${promptBuilderInterfaceText?.promptBuilderDeleteCancelButton}`,
        });
        if (!confirmed) return;
        // A delete that gets no answer is read back (see writeOrReadBack):
        // a prompt-test SAS Model Manager no longer holds was deleted, and
        // leaves the page as after a 204. Otherwise the toast says what the
        // read back found; a refusal is a deletion that failed.
        let deleteStatus = 0;
        let failure: unknown = null;
        try {
          deleteStatus = await selectionLock.during(() => deleteModel(promptModelID));
        } catch (error) {
          console.error('Deleting the prompt-test failed.', error);
          failure = error;
        }
        if (deleteStatus === 204) {
          promptBuilderProjectPrompts = promptBuilderProjectPrompts.filter((item) => item.value !== promptModelID);
          promptFilter.setUsers(promptBuilderProjectPrompts);
          promptBuilderPromptSelectorDropdown.value = `${promptBuilderInterfaceText?.promptSelect}`;
          renderPromptOptions();
          resetExperimentTrackerState();
          dropPromptSelection();
        } else if (noAnswerText(failure)) {
          showToast(`${promptBuilderInterfaceText?.promptBuilderDeletePromptButton}: ${noAnswerText(failure)}`);
        } else {
          showToast(`${promptBuilderInterfaceText?.promptBuilderDeleteFailedResponse}`);
        }
      } finally {
        deletePromptButton.innerText = `${promptBuilderInterfaceText?.promptBuilderDeletePromptButton}`;
        deletePromptButton.disabled =
          promptBuilderPromptSelectorDropdown.value === `${promptBuilderInterfaceText?.promptSelect}`;
      }
    }

    /**
     * After a Delete Project that stopped part of the way: the prompt-test
     * list shows what SAS Model Manager holds now, the prompt-test that is
     * open leaves the page when it was deleted, and a toast says what was
     * deleted and what was not. Answers true when the project is gone after
     * all (a request that got no answer may still have been carried out); the
     * caller then ends the delete as one that went through. Where nothing can
     * be read back, the prompt-tests whose delete was answered with 204 leave
     * the list, and the toast says that the rest could not be read.
     */
    async function showWhatIsLeftOfProject(
      projectID: string,
      projectName: string,
      asked: DropdownOption[],
      deletedIds: string[],
      failure: unknown
    ): Promise<boolean> {
      let left: DropdownOption[] | null = null;
      try {
        const projects = await getModelProjects(`contains(tags,'Prompt-Engineering')`);
        if (!projects.some((project) => project.value === projectID)) return true;
        // Listed as for the dialogs of the delete: every model of the project.
        left = await getModelProjectModels(projectID);
      } catch (error) {
        console.error('Reading the project back after a delete failed.', error);
      }
      const isThere = (prompt: DropdownOption): boolean =>
        left ? left.some((kept) => kept.value === prompt.value) : !deletedIds.includes(prompt.value);
      const deleted = asked.filter((prompt) => !isThere(prompt));
      promptBuilderProjectPrompts = promptBuilderProjectPrompts.filter(isThere);
      promptFilter.setUsers(promptBuilderProjectPrompts);
      const open = promptBuilderPromptSelectorDropdown.value;
      if (open !== `${promptBuilderInterfaceText?.promptSelect}` && !promptBuilderProjectPrompts.some((prompt) => prompt.value === open)) {
        promptBuilderPromptSelectorDropdown.value = `${promptBuilderInterfaceText?.promptSelect}`;
        renderPromptOptions();
        resetExperimentTrackerState();
        dropPromptSelection();
      } else {
        renderPromptOptions();
      }
      const names = (list: DropdownOption[]): string => list.map((prompt) => String(prompt.innerHTML ?? prompt.value)).join(', ');
      showToast(
        [
          // Whether the project is still there is known only where it was read.
          // A delete that got no answer was read back: no word of permissions.
          noAnswerText(failure)
            ? `${promptBuilderInterfaceText?.promptBuilderDeleteProjectButton}: ${noAnswerText(failure)}`
            : left === null
              ? `${promptBuilderInterfaceText?.promptBuilderDeleteFailedResponse}`
              : `${promptBuilderInterfaceText?.promptBuilderDeleteProjectStopped}`.replace('{project}', () => projectName),
          ...(deleted.length ? [`${promptBuilderInterfaceText?.promptBuilderDeleteProjectDeletedPart}`.replace('{names}', () => names(deleted))] : []),
          ...(left === null
            ? [`${promptBuilderInterfaceText?.promptBuilderDeleteProjectNotRead}`]
            : left.length
              ? [`${promptBuilderInterfaceText?.promptBuilderDeleteProjectLeftPart}`.replace('{names}', () => names(left as DropdownOption[]))]
              : []),
        ].join(' ')
      );
      return false;
    }

    // Function to call when deleting the selected project
    async function promptBuilderDeleteProject(): Promise<void> {
      const projectSelectedIndex = promptBuilderProjectSelectorDropdown.selectedIndex;
      const projectID = promptBuilderProjectSelectorDropdown.value;
      if (projectID === `${promptBuilderInterfaceText?.projectSelect}`) return;
      const projectName = promptBuilderProjectSelectorDropdown.options[projectSelectedIndex].text;
      deleteProjectButton.disabled = true;
      deleteProjectButton.innerHTML = `<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> ${promptBuilderInterfaceText?.promptBuilderDeleteProjectButton}`;
      try {
        let projectPrompts: Awaited<ReturnType<typeof getModelProjectModels>> = [];
        try {
          projectPrompts = await getModelProjectModels(projectID);
        } catch (error) {
          console.error('Failed to load the prompts of the selected project.', error);
          showToast(`${promptBuilderInterfaceText?.promptBuilderDeleteFailedResponse}`);
          return;
        }
        // Another project was chosen while its prompt-tests or their usage
        // were asked: no dialog about one that is no longer on the page (as in
        // the RAG Builder). Asked after every wait before a dialog.
        const projectLeft = (): boolean => promptBuilderProjectSelectorDropdown.value !== projectID;
        if (projectLeft()) return;
        // Confirm every contained prompt (with its decision usage) one by one
        // BEFORE deleting anything, so a single cancel aborts the whole
        // operation without leaving a partially deleted project behind.
        if (projectPrompts.length === 0) {
          const confirmed = await showConfirmModal({
            title: `${promptBuilderInterfaceText?.promptBuilderDeleteProjectButton}: ${projectName}`,
            body: [`${promptBuilderInterfaceText?.promptBuilderDeleteProjectEmptyNote}`],
            confirmText: `${promptBuilderInterfaceText?.promptBuilderDeleteConfirmButton}`,
            cancelText: `${promptBuilderInterfaceText?.promptBuilderDeleteCancelButton}`,
          });
          if (!confirmed) return;
        }
        for (let i = 0; i < projectPrompts.length; i++) {
          const decisions = await checkModelDecisionUsage(projectPrompts[i].value);
          if (projectLeft()) return;
          const confirmed = await showConfirmModal({
            title: `${promptBuilderInterfaceText?.promptBuilderDeleteProjectTitle} ${projectPrompts[i].innerHTML} (${i + 1}/${projectPrompts.length})`,
            body: buildUsageBody(decisions),
            confirmText: `${promptBuilderInterfaceText?.promptBuilderDeleteConfirmButton}`,
            cancelText: `${promptBuilderInterfaceText?.promptBuilderDeleteCancelButton}`,
          });
          if (!confirmed) return;
        }
        // Delete the confirmed prompts explicitly before the project itself —
        // whether a project DELETE cascades to its models varies by SAS Viya
        // release, deleting them one by one is deterministic.
        // One write, from the first prompt-test to the project. A request that
        // gets no usable answer is a deletion that failed, as a refusal is;
        // one that stops part of the way shows what is left (see
        // showWhatIsLeftOfProject), which may be nothing.
        let projectGone = false;
        try {
          projectGone = await selectionLock.during(async () => {
            // The prompt-tests whose delete SAS Model Manager answered with 204
            // (or which it no longer held when a delete without an answer was
            // read back).
            const deletedIds: string[] = [];
            let failure: unknown = null;
            try {
              for (const projectPrompt of projectPrompts) {
                if ((await deleteModel(projectPrompt.value)) !== 204) throw new Error(`prompt-test ${projectPrompt.value} was not deleted`);
                deletedIds.push(projectPrompt.value);
              }
              if ((await deleteModelProject(projectID)) === 204) return true;
            } catch (error) {
              console.error('Deleting the project failed.', error);
              failure = error;
            }
            return showWhatIsLeftOfProject(projectID, projectName, projectPrompts, deletedIds, failure);
          });
        } catch (error) {
          console.error('Showing what is left of the project failed.', error);
          showToast(`${promptBuilderInterfaceText?.promptBuilderDeleteFailedResponse}`);
        }
        if (projectGone) {
          promptBuilderAllProjects = promptBuilderAllProjects.filter((item) => item.value !== projectID);
          projectFilter.setUsers(promptBuilderAllProjects);
          promptBuilderProjectSelectorDropdown.value = `${promptBuilderInterfaceText?.projectSelect}`;
          renderProjectOptions();
          // Reset the prompt selector to the placeholder-only state
          promptBuilderProjectPrompts = [];
          promptFilter.setUsers(promptBuilderProjectPrompts);
          promptBuilderPromptSelectorDropdown.value = `${promptBuilderInterfaceText?.promptSelect}`;
          renderPromptOptions();
          resetExperimentTrackerState();
          projectLoadToken += 1;
          dropPromptSelection();
        }
      } finally {
        deleteProjectButton.innerText = `${promptBuilderInterfaceText?.promptBuilderDeleteProjectButton}`;
        deleteProjectButton.disabled =
          promptBuilderProjectSelectorDropdown.value === `${promptBuilderInterfaceText?.projectSelect}`;
      }
    }

    // The create dialog is shared with the RAG Builder; kept as a local alias
    // because this file calls it by its original name in several places.
    const promptBuilderCreateModal = createCreateModal;

    // Create the modals for project/prompt creation
    promptBuilderCreateModal(
      promptBuilderModalButtonContainer,
      'promptBuilderCreateProject',
      promptBuilderInterfaceText?.promptBuilderCreateProject as unknown as ModalText,
      promptBuilderCreateProject
    );
    promptBuilderCreateModal(
      promptBuilderModalButtonContainer,
      'promptBuilderCreatePrompt',
      promptBuilderInterfaceText?.promptBuilderCreatePrompt as unknown as ModalText,
      promptBuilderCreatePrompt
    );

    // Add link to SAS Model Manager. This is an <a> (not a <button>) so that,
    // inside VA's sandboxed DDC iframe, the browser's native "Open link in new
    // tab" (right-click / context menu) works. A plain click is handled by
    // openModelManagerLink(), which opens a new tab where the sandbox allows it
    // and otherwise copies the link to the clipboard.
    const openInMMButton = document.createElement('a');
    openInMMButton.id = `${promptBuilderObject?.id}-openInMMButton`;
    openInMMButton.setAttribute('role', 'button');
    openInMMButton.setAttribute('target', '_blank');
    openInMMButton.setAttribute('rel', 'noopener noreferrer');
    openInMMButton.classList.add('btn', 'btn-outline-secondary', 'disabled');
    openInMMButton.setAttribute('aria-disabled', 'true');
    openInMMButton.innerHTML = promptBuilderInterfaceText?.promptBuilderOpenInMMButton as string;
    promptBuilderModalButtonContainer.appendChild(openInMMButton);

    // Delete the selected prompt / project. Both stay disabled until a real
    // selection exists in the corresponding dropdown.
    const deletePromptButton = document.createElement('button');
    deletePromptButton.type = 'button';
    deletePromptButton.id = `${promptBuilderObject?.id}-delete-prompt-button`;
    deletePromptButton.classList.add('btn', 'btn-danger', 'ms-auto');
    deletePromptButton.disabled = true;
    deletePromptButton.innerText = `${promptBuilderInterfaceText?.promptBuilderDeletePromptButton}`;
    deletePromptButton.onclick = async function () {
      promptBuilderDeletePrompt();
    };
    promptBuilderModalButtonContainer.appendChild(deletePromptButton);

    const deleteProjectButton = document.createElement('button');
    deleteProjectButton.type = 'button';
    deleteProjectButton.id = `${promptBuilderObject?.id}-delete-project-button`;
    deleteProjectButton.classList.add('btn', 'btn-danger');
    deleteProjectButton.disabled = true;
    deleteProjectButton.innerText = `${promptBuilderInterfaceText?.promptBuilderDeleteProjectButton}`;
    deleteProjectButton.onclick = async function () {
      promptBuilderDeleteProject();
    };
    promptBuilderModalButtonContainer.appendChild(deleteProjectButton);

    // Label + info icon for an LLM option; the explanation is a Bootstrap
    // tooltip so it also works with keyboard focus and touch.
    // The info-icon label is shared with the RAG Builder; kept as a local
    // alias because it is used throughout this file's option rendering.
    const createOptionLabel = createInfoLabel;

    function generateModelSelection(availableModels: AvailableLLM[]): void {
      availableModels.forEach((model, index) => {
        const modelDiv = document.createElement('div');
        modelDiv.className = 'form-check';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.id = `model${index}`;
        checkbox.className = 'form-check-input';
        checkbox.value = model?.name;
        checkbox.addEventListener('change', () => {
          const optionsDiv = document.getElementById(`options${index}`);
          if (optionsDiv) optionsDiv.style.display = checkbox.checked ? 'flex' : 'none';
          updateRunExperimentsButtonState();
        });

        const label = document.createElement('label');
        label.className = 'form-check-label';
        label.htmlFor = `model${index}`;
        label.innerText = model?.name;

        const optionsDiv = document.createElement('div');
        optionsDiv.classList.add('model-options');
        optionsDiv.id = `options${index}`;

        if (model?.options?.temperature && isNumberOption(model.options.temperature)) {
          const temperatureInput = document.createElement('input');
          temperatureInput.type = 'number';
          temperatureInput.id = `temperature${index}`;
          temperatureInput.value = String(model.options.temperature.default);
          temperatureInput.step = '0.1';
          temperatureInput.min = '0';
          temperatureInput.max = '1';
          optionsDiv.appendChild(
            createOptionLabel('Temperature', String(promptBuilderInterfaceText?.promptBuilderTemperatureInfo))
          );
          optionsDiv.appendChild(temperatureInput);
        }

        if (model?.options?.top_p && isNumberOption(model.options.top_p)) {
          const topPInput = document.createElement('input');
          topPInput.type = 'number';
          topPInput.id = `top_p${index}`;
          topPInput.value = String(model.options.top_p.default);
          topPInput.step = '0.1';
          topPInput.min = '0';
          topPInput.max = '1';
          optionsDiv.appendChild(
            createOptionLabel('Top P', String(promptBuilderInterfaceText?.promptBuilderTop_PInfo))
          );
          optionsDiv.appendChild(topPInput);
        }

        if (model?.options?.top_k && isNumberOption(model.options.top_k)) {
          const topKInput = document.createElement('input');
          topKInput.type = 'number';
          topKInput.id = `top_k${index}`;
          topKInput.value = String(model.options.top_k.default);
          topKInput.step = '1';
          topKInput.min = '1';
          topKInput.max = '100';
          optionsDiv.appendChild(
            createOptionLabel('Top K', String(promptBuilderInterfaceText?.promptBuilderTop_KInfo))
          );
          optionsDiv.appendChild(topKInput);
        }

        if (model?.options?.max_length && isNumberOption(model.options.max_length)) {
          const maxLengthInput = document.createElement('input');
          maxLengthInput.type = 'number';
          maxLengthInput.id = `max_length${index}`;
          maxLengthInput.value = String(model.options.max_length.default);
          maxLengthInput.step = '1';
          maxLengthInput.min = '0';
          maxLengthInput.max = '1000000';
          optionsDiv.appendChild(
            createOptionLabel('Max Length', String(promptBuilderInterfaceText?.promptBuilderMax_LengthInfo))
          );
          optionsDiv.appendChild(maxLengthInput);
        }

        if (model?.options?.max_tokens && isNumberOption(model.options.max_tokens)) {
          const maxTokensInput = document.createElement('input');
          maxTokensInput.type = 'number';
          maxTokensInput.id = `max_tokens${index}`;
          maxTokensInput.value = String(model.options.max_tokens.default);
          maxTokensInput.step = '1';
          maxTokensInput.min = '0';
          maxTokensInput.max = '1000000';
          optionsDiv.appendChild(
            createOptionLabel('Max Tokens', String(promptBuilderInterfaceText?.promptBuilderMax_LengthInfo))
          );
          optionsDiv.appendChild(maxTokensInput);
        }

        // Reasoning models cap output via max_completion_tokens - same user
        // concept as Max Tokens, so it presents identically.
        if (model?.options?.max_completion_tokens && isNumberOption(model.options.max_completion_tokens)) {
          const maxCompletionInput = document.createElement('input');
          maxCompletionInput.type = 'number';
          maxCompletionInput.id = `max_completion_tokens${index}`;
          maxCompletionInput.value = String(model.options.max_completion_tokens.default);
          maxCompletionInput.step = '1';
          maxCompletionInput.min = '0';
          maxCompletionInput.max = '1000000';
          optionsDiv.appendChild(
            createOptionLabel('Max Tokens', String(promptBuilderInterfaceText?.promptBuilderMax_LengthInfo))
          );
          optionsDiv.appendChild(maxCompletionInput);
        }

        if (model?.options?.max_new_tokens && isNumberOption(model.options.max_new_tokens)) {
          const maxNewTokensInput = document.createElement('input');
          maxNewTokensInput.type = 'number';
          maxNewTokensInput.id = `max_new_tokens${index}`;
          maxNewTokensInput.value = String(model.options.max_new_tokens.default);
          maxNewTokensInput.step = '1';
          maxNewTokensInput.min = '0';
          maxNewTokensInput.max = '1000000';
          optionsDiv.appendChild(
            createOptionLabel('Max New Tokens', String(promptBuilderInterfaceText?.promptBuilderMax_LengthInfo))
          );
          optionsDiv.appendChild(maxNewTokensInput);
        }

        // Every remaining option gets a control from the reusable typed-option
        // component (segmented selector for small enums, checkbox for bools,
        // text/number inputs otherwise), so models with e.g. reasoning_effort
        // or an Azure resource override are fully configurable instead of
        // silently running on their score-code defaults.
        // API_KEY has no control, and a number of NUMBER_FIELD_OPTIONS has its
        // field above.
        Object.entries(model?.options ?? {}).forEach(([optionKey, optionMeta]) => {
          if (optionKey === 'API_KEY' || (NUMBER_FIELD_OPTIONS.includes(optionKey) && isNumberOption(optionMeta))) return;
          const tooltipText = String(optionMeta?.description ?? optionKey);
          optionsDiv.appendChild(createOptionLabel(optionDisplayLabel(optionKey, optionMeta), tooltipText));
          optionsDiv.appendChild(createTypedOptionControl(optionKey, optionMeta, optionControlId(index, optionKey, optionMeta)));
        });

        modelDiv.appendChild(checkbox);
        modelDiv.appendChild(label);
        // A model that cannot be used is visible but not selectable, with a
        // note that says why (see llmNotUsableNote).
        const notUsable = llmNotUsableNote(model);
        if (notUsable) {
          checkbox.disabled = true;
          label.classList.add('text-muted');
          const note = document.createElement('small');
          note.classList.add('text-muted', 'd-block', llmNotUsableClass(model));
          note.innerText = notUsable;
          modelDiv.title = note.innerText;
          modelDiv.appendChild(note);
        }
        modelDiv.appendChild(optionsDiv);
        promptBuilderModelSelectorContainer.appendChild(modelDiv);
      });
    }

    // Model Selector
    const promptBuilderModelSelectorHeader = document.createElement('h2');
    promptBuilderModelSelectorHeader.innerText = promptBuilderInterfaceText?.promptBuilderModelSelectorHeading as string;
    const promptBuilderModelSelectorContainer = document.createElement('div');
    promptBuilderModelSelectorContainer.setAttribute('id', `${promptBuilderObject?.id}-model-selector-container`);
    // Load the available and deprecated LLM lists, then each LLM's options.json,
    // all in parallel — done serially this delays the first paint noticeably.
    const [promptBuilderAllLLMOptions, promptBuilderDeprecatedLLMOptions] = await Promise.all([
      getModelProjectModels(promptBuilderObject?.llmProjectID as string),
      getModelProjectModels(promptBuilderObject?.llmProjectID as string, "eq(tags,'deprecated')"),
    ]);
    const promptBuilderDeprecatedLLMs: AvailableLLM[] = promptBuilderDeprecatedLLMOptions.map(o => ({ ...o, id: o.value, name: o.innerHTML }));
    const promptBuilderAvailableLLMs: AvailableLLM[] = promptBuilderAllLLMOptions
      .map(o => ({ ...o, id: o.value, name: o.innerHTML }))
      .filter((obj1) => !promptBuilderDeprecatedLLMs.some((obj2) => obj1.id === obj2.id));
    await Promise.all(
      promptBuilderAvailableLLMs.map(async (availableLLM) => {
        // A single unreachable/slow LLM must not break the whole builder — it
        // just loads without its options (and, later, without a cost estimate).
        try {
          const availableLLMContents = await getModelContents(availableLLM.id);
          for (const availableLLMContent of availableLLMContents) {
            if (availableLLMContent?.name === 'options.json') {
              availableLLM.fileURI = availableLLMContent.fileUri;
              availableLLM.options = await readStoredJson<Record<string, ModelOption>>(availableLLM.fileURI!, 'options.json');
            }
          }
        } catch (error) {
          console.error(`Failed to load options for LLM ${availableLLM.name}.`, error);
          // An options.json this page cannot read is no model without
          // options: the model is shown as one that cannot be used.
          availableLLM.optionsUnusable = error instanceof StoredFileUnusable;
        }
      })
    );
    // Resolve provider keys from the credential domain: a single secrets-map
    // lookup under the signed-in user's identity (user credential overrides
    // group credential). The domain is the ONLY key source — providers
    // without an entry are disabled in every model selector with a note
    // naming the domain, so access to a provider is an identity decision.
    // 'none' skips the lookup (for deployments using only key-less models);
    // with a non-blank default, an empty URL parameter cannot express "off".
    const credentialDomainRaw = String(promptBuilderObject?.credentialDomain ?? '').trim();
    const credentialDomain = credentialDomainRaw.toLowerCase() === 'none' ? '' : credentialDomainRaw;
    const providersWithoutCredential = new Set<string>();
    promptBuilderObject.API_KEYS = credentialDomain
      ? await resolveDomainSecrets(credentialDomain)
      : {};
    {
      const resolvedKeys = promptBuilderObject.API_KEYS as Record<string, string>;
      promptBuilderAvailableLLMs
        .map((llm) => llm.options?.API_KEY?.default as string | undefined)
        .filter((name): name is string => Boolean(name))
        .forEach((name) => {
          if (!resolvedKeys[name]) providersWithoutCredential.add(name);
        });
    }
    function llmMissingCredentialDomain(llm: AvailableLLM): string | null {
      const provider = llm.options?.API_KEY?.default as string | undefined;
      return provider && providersWithoutCredential.has(provider) ? provider : null;
    }
    /**
     * Why a model cannot be used, or null: its options.json is not one this
     * page can read, a run cannot keep its result under its name
     * (isReservedModelName), or its provider has no key for this user (the
     * note names the missing entry and the domain). Such a model is shown,
     * and offered nowhere.
     */
    function llmNotUsableNote(llm: AvailableLLM): string | null {
      if (llm.optionsUnusable) return `${promptBuilderInterfaceText?.promptBuilderModelOptionsUnusable}`;
      if (isReservedModelName(llm.name)) return `${promptBuilderInterfaceText?.promptBuilderModelNameReserved}`.replace('{name}', () => llm.name);
      const missingProvider = llmMissingCredentialDomain(llm);
      if (!missingProvider) return null;
      return String(
        promptBuilderInterfaceText?.promptBuilderCredentialMissing ??
          'No {provider} key available in the {domain} credential domain - ask your administrator for access.'
      )
        .replace('{provider}', missingProvider)
        .replace('{domain}', credentialDomain);
    }
    /** The class of the note llmNotUsableNote gives, by its reason. */
    function llmNotUsableClass(llm: AvailableLLM): string {
      return llm.optionsUnusable ? 'pb-options-note' : isReservedModelName(llm.name) ? 'pb-name-note' : 'pb-credential-note';
    }
    // Cost/governance attributes of a run's LLM (per-token / per-second prices,
    // provider, family, endpoint), resolved by model name. Fetched LAZILY — only
    // for the models actually used (run / judge / manifest) — so the initial load
    // is a fixed number of requests regardless of how many LLMs are registered.
    const llmAttributesByName = new Map<string, AvailableLLM>();
    for (const llm of [...promptBuilderAvailableLLMs, ...promptBuilderDeprecatedLLMs]) {
      llmAttributesByName.set(llm.name, llm);
    }
    const llmAttributesFetched = new Set<string>();
    async function ensureLLMCostAttributes(modelName: string): Promise<void> {
      const llm = llmAttributesByName.get(modelName);
      if (!llm || llmAttributesFetched.has(modelName)) return;
      llmAttributesFetched.add(modelName);
      try {
        Object.assign(llm, extractCostAttributes(await getModelDetails(llm.id)));
      } catch (error) {
        console.error(`Failed to load cost attributes for LLM ${modelName}.`, error);
      }
    }
    generateModelSelection(promptBuilderAvailableLLMs);

    // --- Decision models (kind: decision) ----------------------------------
    // mdb registers them in the Decision Model Project of the same repository,
    // resolved here by its name so no further report option is needed. They
    // carry no sampling options - only, for a hosted model, the key entry.
    //
    // With decision templates switched off, the parts of that kind (this
    // container, the question designer, the decision manifest options) are
    // still created, because the functions below hold on to them, but they
    // are never put on the page (see the assembly at the end) and nothing is
    // requested for them.
    const promptBuilderDecisionModelsContainer = document.createElement('div');
    promptBuilderDecisionModelsContainer.id = `${promptBuilderObject?.id}-decision-model-selector-container`;
    promptBuilderDecisionModelsContainer.style.display = 'none';
    let promptBuilderAvailableDecisionModels: AvailableLLM[] = [];
    // Asked for only while decision templates are switched on - switched off,
    // the page sends no request about decision models at all.
    if (decisionEnabled) {
      try {
        const decisionProjects = await getModelProjects(
          `and(eq(repositoryId,'${promptBuilderObject?.modelRepositoryID}'),eq(name,'${DECISION_PROJECT_NAME}'))`
        );
        if (decisionProjects.length > 0) {
          const decisionProjectID = decisionProjects[0].value;
          const [decisionModelOptions, deprecatedDecisionModels] = await Promise.all([
            getModelProjectModels(decisionProjectID),
            getModelProjectModels(decisionProjectID, "eq(tags,'deprecated')"),
          ]);
          promptBuilderAvailableDecisionModels = decisionModelOptions
            .map((option) => ({ ...option, id: option.value, name: option.innerHTML }))
            .filter((model) => !deprecatedDecisionModels.some((deprecated) => deprecated.value === model.id));
          await Promise.all(
            promptBuilderAvailableDecisionModels.map(async (model) => {
              try {
                const contents = await getModelContents(model.id);
                const optionsContent = contents.find((content) => content?.name === 'options.json');
                if (optionsContent?.fileUri) {
                  model.fileURI = optionsContent.fileUri;
                  model.options = await readStoredJson<Record<string, ModelOption>>(optionsContent.fileUri, 'options.json', isDecisionModelOptions);
                }
              } catch (error) {
                console.error(`Failed to load options for decision model ${model.name}.`, error);
                model.optionsUnusable = error instanceof StoredFileUnusable;
              }
              // The card shows what the registration says the model is and
              // accepts, so the attributes are read up front rather than at
              // the first run.
              try {
                Object.assign(model, extractCostAttributes(await getModelDetails(model.id)));
                llmAttributesFetched.add(model.name);
              } catch (error) {
                console.error(`Failed to load the attributes of decision model ${model.name}.`, error);
              }
            })
          );
        }
      } catch (error) {
        console.error('Failed to load the decision models.', error);
      }
    }
    for (const model of promptBuilderAvailableDecisionModels) {
      llmAttributesByName.set(model.name, model);
      const keyName = model.options?.API_KEY?.default as string | undefined;
      if (keyName && !(promptBuilderObject.API_KEYS as Record<string, string>)[keyName]) {
        providersWithoutCredential.add(keyName);
      }
    }
    if (decisionEnabled && promptBuilderAvailableDecisionModels.length === 0) {
      const noModels = document.createElement('p');
      noModels.classList.add('text-muted');
      noModels.innerText = `${promptBuilderInterfaceText?.promptBuilderDecisionNoModels}`;
      promptBuilderDecisionModelsContainer.appendChild(noModels);
    }
    // One card per model: the checkbox, what the registration says about it
    // (provider, licence, context, option limit, question types), the price
    // of the current request, and whether the model can take that request.
    const decisionModelCards: { model: AvailableLLM; fit: HTMLElement; price: HTMLElement }[] = [];
    const formatDecisionPrice = (model: AvailableLLM, tokens: number): string => {
      if (model.inputTokenCount != null) {
        const perThousand = tokens * model.inputTokenCount * 1000;
        return `${promptBuilderInterfaceText?.promptBuilderDecisionCardPrice}`.replace('{cost}', formatCost(perThousand));
      }
      if (model.hostingCosts != null) {
        return `${promptBuilderInterfaceText?.promptBuilderDecisionCardHosted}`.replace('{cost}', formatCost(model.hostingCosts));
      }
      return '';
    };
    promptBuilderAvailableDecisionModels.forEach((model, index) => {
      const card = document.createElement('div');
      card.classList.add('pb-model-card');
      const check = document.createElement('div');
      check.className = 'form-check';
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.id = `decision-model${index}`;
      checkbox.className = 'form-check-input';
      checkbox.value = model.name;
      checkbox.addEventListener('change', () => {
        decisionDesigner.refresh();
        updateRunExperimentsButtonState();
      });
      const label = document.createElement('label');
      label.className = 'form-check-label pb-model-card-name';
      label.htmlFor = checkbox.id;
      label.innerText = model.name;
      check.appendChild(checkbox);
      check.appendChild(label);
      card.appendChild(check);
      const facts: string[] = [];
      if (model.provider) facts.push(String(model.provider));
      if (model.licenseClass) facts.push(String(model.licenseClass));
      if (model.contextLength) facts.push(`${promptBuilderInterfaceText?.promptBuilderDecisionCardContext}`.replace('{n}', model.contextLength.toLocaleString()));
      if (model.maxOptions && model.maxOptions < 255) facts.push(`${promptBuilderInterfaceText?.promptBuilderDecisionCardMaxOptions}`.replace('{n}', String(model.maxOptions)));
      if (model.questionTypes && model.questionTypes.length < 3) {
        facts.push(`${promptBuilderInterfaceText?.promptBuilderDecisionCardTypes}`.replace('{types}', model.questionTypes.join(', ')));
      }
      const meta = document.createElement('small');
      meta.classList.add('text-muted', 'd-block', 'pb-model-card-meta');
      meta.innerText = facts.join(' · ');
      card.appendChild(meta);
      const price = document.createElement('small');
      price.classList.add('text-muted', 'd-block', 'pb-model-card-price');
      card.appendChild(price);
      const fit = document.createElement('small');
      fit.classList.add('d-block', 'pb-model-fit');
      card.appendChild(fit);
      const notUsable = llmNotUsableNote(model);
      if (notUsable) {
        checkbox.disabled = true;
        label.classList.add('text-muted');
        const note = document.createElement('small');
        note.classList.add('text-muted', 'd-block', llmNotUsableClass(model));
        note.innerText = notUsable;
        card.appendChild(note);
      }
      decisionModelCards.push({ model, fit, price });
      promptBuilderDecisionModelsContainer.appendChild(card);
    });
    const selectedDecisionModels = (): AvailableLLM[] =>
      promptBuilderAvailableDecisionModels.filter(
        (_, index) => (document.getElementById(`decision-model${index}`) as HTMLInputElement | null)?.checked
      );
    /** The smallest context among the selected models, for the state lint. */
    const decisionContextLimit = (): number | null => {
      const limits = selectedDecisionModels()
        .map((model) => model.contextLength)
        .filter((limit): limit is number => typeof limit === 'number' && limit > 0);
      return limits.length > 0 ? Math.min(...limits) : null;
    };
    /** Whether a model can take the current state and questions. */
    const decisionModelFit = (model: AvailableLLM): { status: 'ok' | 'no'; reason: string } =>
      fitCheck(
        decisionDesigner.getQuestions(),
        decisionDesigner.stateInput.value,
        { contextLength: model.contextLength, maxOptions: model.maxOptions, questionTypes: model.questionTypes },
        promptBuilderInterfaceText
      );
    /** The selected models split by whether they can take the current request. */
    const fitDecisionModels = (): { fit: AvailableLLM[]; unfit: AvailableLLM[] } => {
      const fit: AvailableLLM[] = [];
      const unfit: AvailableLLM[] = [];
      selectedDecisionModels().forEach((model) => (decisionModelFit(model).status === 'ok' ? fit : unfit).push(model));
      return { fit, unfit };
    };
    /** Re-read every card's price and fit against the current request. */
    const refreshDecisionModelCards = (): void => {
      const questions = decisionDesigner.getQuestions();
      const tokens = estimateTokens(decisionDesigner.stateInput.value, questions);
      decisionModelCards.forEach(({ model, fit, price }) => {
        price.innerText = formatDecisionPrice(model, tokens);
        const verdict = decisionModelFit(model);
        fit.innerText = `${verdict.status === 'ok' ? '✓' : '✗'} ${verdict.reason}`;
        fit.classList.toggle('is-ok', verdict.status === 'ok');
        fit.classList.toggle('is-no', verdict.status !== 'ok');
      });
    };
    /** The options of a decision call: only the key entry, resolved from the domain. */
    const decisionCallOptions = (model: AvailableLLM): Record<string, unknown> => {
      const keyName = model.options?.API_KEY?.default as string | undefined;
      if (!keyName) return {};
      return { API_KEY: (promptBuilderObject?.API_KEYS as Record<string, string> | undefined)?.[keyName] ?? '' };
    };

    // Add the prompting inputs
    const promptBuilderPromptingHeader = document.createElement('h2');
    promptBuilderPromptingHeader.innerText = promptBuilderInterfaceText?.promptBuilderPromptingHeader as string;
    const promptBulderPromptingExplainer = document.createElement('p');
    promptBulderPromptingExplainer.innerHTML = promptBuilderInterfaceText?.promptBulderPromptingExplainer as string;

    // Variables manager: define name/description/type/value rows whose values
    // are substituted into the prompts via the {{variableName}} syntax.
    const promptBuilderVariablesHeader = document.createElement('h3');
    promptBuilderVariablesHeader.innerText = `${promptBuilderInterfaceText?.promptBuilderVariablesHeading}`;
    const promptBuilderVariablesContainer = document.createElement('div');
    promptBuilderVariablesContainer.id = `${paneID}-obj-${promptBuilderObject?.id}-variables`;
    const promptBuilderVariablesAddButton = document.createElement('button');
    promptBuilderVariablesAddButton.type = 'button';
    promptBuilderVariablesAddButton.classList.add('btn', 'btn-outline-secondary');
    promptBuilderVariablesAddButton.innerText = `${promptBuilderInterfaceText?.promptBuilderVariablesAddButton}`;
    promptBuilderVariablesAddButton.onclick = () => createPromptVariableRow();

    function createPromptVariableRow(variable?: PromptVariable): void {
      const variableRow = document.createElement('div');
      variableRow.classList.add('row', 'g-2', 'align-items-start', 'mb-2', 'pb-variable-row');
      // Name
      const nameColumn = document.createElement('div');
      nameColumn.classList.add('col-md-3');
      const nameInput = document.createElement('input');
      nameInput.type = 'text';
      nameInput.maxLength = 32;
      nameInput.classList.add('form-control', 'pb-var-name');
      nameInput.placeholder = `${promptBuilderInterfaceText?.promptBuilderVariablesNameLabel}`;
      nameInput.setAttribute('aria-label', `${promptBuilderInterfaceText?.promptBuilderVariablesNameLabel}`);
      nameInput.value = variable?.name ?? '';
      nameInput.oninput = () => validatePromptVariableRows();
      const nameFeedback = document.createElement('div');
      nameFeedback.classList.add('invalid-feedback');
      nameColumn.appendChild(nameInput);
      nameColumn.appendChild(nameFeedback);
      // Description
      const descriptionColumn = document.createElement('div');
      descriptionColumn.classList.add('col-md-4');
      const descriptionInput = document.createElement('input');
      descriptionInput.type = 'text';
      descriptionInput.maxLength = 500;
      descriptionInput.classList.add('form-control', 'pb-var-description');
      descriptionInput.placeholder = `${promptBuilderInterfaceText?.promptBuilderVariablesDescriptionLabel}`;
      descriptionInput.setAttribute('aria-label', `${promptBuilderInterfaceText?.promptBuilderVariablesDescriptionLabel}`);
      descriptionInput.value = variable?.description ?? '';
      descriptionColumn.appendChild(descriptionInput);
      // Data type (the 128000-character default string length stays internal)
      const typeColumn = document.createElement('div');
      typeColumn.classList.add('col-md-2');
      const typeSelect = document.createElement('select');
      typeSelect.classList.add('form-select', 'pb-var-type');
      typeSelect.setAttribute('aria-label', `${promptBuilderInterfaceText?.promptBuilderVariablesTypeLabel}`);
      const stringOption = document.createElement('option');
      stringOption.value = 'string';
      stringOption.innerText = `${promptBuilderInterfaceText?.promptBuilderVariablesTypeString}`;
      const decimalOption = document.createElement('option');
      decimalOption.value = 'decimal';
      decimalOption.innerText = `${promptBuilderInterfaceText?.promptBuilderVariablesTypeDecimal}`;
      typeSelect.appendChild(stringOption);
      typeSelect.appendChild(decimalOption);
      typeSelect.value = variable?.type === 'decimal' ? 'decimal' : 'string';
      typeSelect.onchange = () => validatePromptVariableRows();
      typeColumn.appendChild(typeSelect);
      // Value
      const valueColumn = document.createElement('div');
      valueColumn.classList.add('col-md-2');
      const valueInput = document.createElement('input');
      valueInput.type = 'text';
      valueInput.classList.add('form-control', 'pb-var-value');
      valueInput.placeholder = `${promptBuilderInterfaceText?.promptBuilderVariablesValueLabel}`;
      valueInput.setAttribute('aria-label', `${promptBuilderInterfaceText?.promptBuilderVariablesValueLabel}`);
      valueInput.value = variable?.value ?? '';
      valueInput.oninput = () => validatePromptVariableRows();
      const valueFeedback = document.createElement('div');
      valueFeedback.classList.add('invalid-feedback');
      valueFeedback.innerText = `${promptBuilderInterfaceText?.promptBuilderVariablesValueNotNumeric}`;
      valueColumn.appendChild(valueInput);
      valueColumn.appendChild(valueFeedback);
      // Remove
      const removeColumn = document.createElement('div');
      removeColumn.classList.add('col-md-1');
      const removeButton = document.createElement('button');
      removeButton.type = 'button';
      removeButton.classList.add('btn', 'btn-outline-danger', 'pb-var-remove');
      removeButton.innerHTML = '&times;';
      removeButton.title = `${promptBuilderInterfaceText?.promptBuilderVariablesRemoveButton}`;
      removeButton.setAttribute('aria-label', `${promptBuilderInterfaceText?.promptBuilderVariablesRemoveButton}`);
      removeButton.onclick = () => {
        variableRow.remove();
        validatePromptVariableRows();
      };
      removeColumn.appendChild(removeButton);

      variableRow.appendChild(nameColumn);
      variableRow.appendChild(descriptionColumn);
      variableRow.appendChild(typeColumn);
      variableRow.appendChild(valueColumn);
      variableRow.appendChild(removeColumn);
      promptBuilderVariablesContainer.appendChild(variableRow);
    }

    // Flag invalid/duplicate names and non-numeric decimal values on the rows.
    function validatePromptVariableRows(): void {
      const seenNames = new Set<string>();
      promptBuilderVariablesContainer.querySelectorAll('.pb-variable-row').forEach((row) => {
        const nameInput = row.querySelector('.pb-var-name') as HTMLInputElement;
        const nameFeedback = nameInput.nextElementSibling as HTMLElement;
        const typeSelect = row.querySelector('.pb-var-type') as HTMLSelectElement;
        const valueInput = row.querySelector('.pb-var-value') as HTMLInputElement;
        const name = nameInput.value.trim();
        let nameInvalidText = '';
        if (name !== '' && !isValidDS2VariableName(name)) {
          nameInvalidText = `${promptBuilderInterfaceText?.promptBuilderVariablesNameInvalid}`;
        } else if (name !== '' && isReservedScoreInput(name)) {
          // A variable becomes an input of the manifested model, and the score
          // code has names of its own that such an input would run into.
          nameInvalidText = `${promptBuilderInterfaceText?.promptBuilderVariablesNameReserved}`;
        } else if (name !== '' && seenNames.has(name)) {
          nameInvalidText = `${promptBuilderInterfaceText?.promptBuilderVariablesNameDuplicate}`;
        } else if (name !== '') {
          seenNames.add(name);
        }
        nameFeedback.innerText = nameInvalidText;
        nameInput.classList.toggle('is-invalid', nameInvalidText !== '');
        const valueInvalid =
          typeSelect.value === 'decimal' && valueInput.value.trim() !== '' && isNaN(Number(valueInput.value));
        valueInput.classList.toggle('is-invalid', valueInvalid);
      });
    }

    // Collect the currently valid variable definitions (rows with an invalid,
    // reserved, empty or duplicate name are highlighted by validation and
    // skipped here).
    function collectPromptVariables(): PromptVariable[] {
      validatePromptVariableRows();
      const variables: PromptVariable[] = [];
      const seenNames = new Set<string>();
      promptBuilderVariablesContainer.querySelectorAll('.pb-variable-row').forEach((row) => {
        const name = (row.querySelector('.pb-var-name') as HTMLInputElement).value.trim();
        if (!isValidDS2VariableName(name) || isReservedScoreInput(name) || seenNames.has(name)) return;
        seenNames.add(name);
        variables.push({
          name,
          description: (row.querySelector('.pb-var-description') as HTMLInputElement).value.trim(),
          type: (row.querySelector('.pb-var-type') as HTMLSelectElement).value === 'decimal' ? 'decimal' : 'string',
          value: (row.querySelector('.pb-var-value') as HTMLInputElement).value,
        });
      });
      return variables;
    }

    function setPromptVariables(variables: PromptVariable[]): void {
      promptBuilderVariablesContainer.innerHTML = '';
      variables.forEach((variable) => createPromptVariableRow(variable));
      validatePromptVariableRows();
    }

    // Replace {{variableName}} tokens with the variable values. Tokens that do
    // not match a defined variable are left as literal text.
    function substitutePromptVariables(text: string, variables: PromptVariable[]): string {
      let result = text;
      variables.forEach((variable) => {
        result = result.replace(
          new RegExp(`\\{\\{\\s*${variable.name}\\s*\\}\\}`, 'g'),
          () => variable.value
        );
      });
      return result;
    }

    // Right-click menu on the prompt fields to insert a {{variable}} at the
    // cursor. Falls back to the browser menu when no variables are defined.
    let promptVariableInsertMenu: HTMLDivElement | null = null;
    function hidePromptVariableInsertMenu(): void {
      promptVariableInsertMenu?.remove();
      promptVariableInsertMenu = null;
    }
    document.addEventListener('click', hidePromptVariableInsertMenu);
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') hidePromptVariableInsertMenu();
    });
    function attachPromptVariableInsertMenu(promptTextarea: HTMLTextAreaElement): void {
      promptTextarea.addEventListener('contextmenu', (event) => {
        const variables = collectPromptVariables();
        if (variables.length === 0) return;
        event.preventDefault();
        hidePromptVariableInsertMenu();
        const insertMenu = document.createElement('div');
        insertMenu.classList.add('dropdown-menu', 'show', 'pb-variable-menu');
        insertMenu.style.left = `${event.clientX}px`;
        insertMenu.style.top = `${event.clientY}px`;
        const insertMenuHeader = document.createElement('h6');
        insertMenuHeader.classList.add('dropdown-header');
        insertMenuHeader.innerText = `${promptBuilderInterfaceText?.promptBuilderVariablesInsertMenuHeader}`;
        insertMenu.appendChild(insertMenuHeader);
        variables.forEach((variable) => {
          const insertMenuItem = document.createElement('button');
          insertMenuItem.type = 'button';
          insertMenuItem.classList.add('dropdown-item');
          insertMenuItem.innerText = variable.name;
          if (variable.description) insertMenuItem.title = variable.description;
          insertMenuItem.onclick = () => {
            const selectionStart = promptTextarea.selectionStart ?? promptTextarea.value.length;
            const selectionEnd = promptTextarea.selectionEnd ?? selectionStart;
            promptTextarea.setRangeText(`{{${variable.name}}}`, selectionStart, selectionEnd, 'end');
            promptTextarea.focus();
            hidePromptVariableInsertMenu();
          };
          insertMenu.appendChild(insertMenuItem);
        });
        document.body.appendChild(insertMenu);
        promptVariableInsertMenu = insertMenu;
      });
    }

    const promptBuilderPromptingContainer = document.createElement('div');
    promptBuilderPromptingContainer.style.gap = '20px';
    promptBuilderPromptingContainer.style.display = 'flex';
    const promptBuilderSystemPrompt = document.createElement('textarea');
    promptBuilderSystemPrompt.id = `${paneID}-obj-${promptBuilderObject?.id}-system-prompt`;
    promptBuilderSystemPrompt.placeholder = promptBuilderInterfaceText?.promptBuilderSystemPromptPlaceholder as string;
    promptBuilderSystemPrompt.style.width = '100%';
    promptBuilderSystemPrompt.style.height = '200px';
    const promptBuilderUserPrompt = document.createElement('textarea');
    promptBuilderUserPrompt.id = `${paneID}-obj-${promptBuilderObject?.id}-user-prompt`;
    promptBuilderUserPrompt.placeholder = promptBuilderInterfaceText?.promptBuilderUserPromptPlaceholder as string;
    promptBuilderUserPrompt.style.width = '100%';
    promptBuilderUserPrompt.style.height = '200px';
    promptBuilderPromptingContainer.appendChild(promptBuilderSystemPrompt);
    promptBuilderPromptingContainer.appendChild(promptBuilderUserPrompt);
    attachPromptVariableInsertMenu(promptBuilderSystemPrompt);
    attachPromptVariableInsertMenu(promptBuilderUserPrompt);
    // The decision template's editor: the state template and the questions.
    const decisionDesigner = createQuestionDesigner(
      `${paneID}-obj-${promptBuilderObject?.id}`,
      promptBuilderInterfaceText,
      attachPromptVariableInsertMenu,
      decisionContextLimit,
      () => collectPromptVariables().map((variable) => variable.name)
    );
    decisionDesigner.element.style.display = 'none';
    decisionDesigner.onChange(() => {
      refreshDecisionModelCards();
      updateRunExperimentsButtonState();
    });
    refreshDecisionModelCards();

    // Start running experiments
    const promptBuilderRunExperimentsButton = document.createElement('button');
    promptBuilderRunExperimentsButton.setAttribute('type', 'button');
    promptBuilderRunExperimentsButton.setAttribute('class', 'btn btn-primary');
    promptBuilderRunExperimentsButton.id = `${paneID}-obj-${promptBuilderObject?.id}-run-experiment`;
    promptBuilderRunExperimentsButton.innerText = `${promptBuilderInterfaceText?.promptBuilderRunExperimentsButton}`;
    promptBuilderRunExperimentsButton.onclick = async function () {
      if (promptMode === 'decision') promptBuilderRunDecisionExperiment();
      else promptBuilderRunExperiment();
    };
    // Disabled (with a hint) until at least one LLM is selected - or, for a
    // decision template, one decision model and a complete question.
    // Set while the calls of a run are out. It blocks what would pull the
    // tracker from under the run: deleting a run (the run indices would
    // shift), judging, a change of kind - and a second run, see below.
    // A Judge button drawn while it is set is drawn disabled, so it is
    // cleared BEFORE the run that has ended is drawn (see drawFinishedRun),
    // and the buttons drawn meanwhile are put right (refreshJudgeButtons).
    let experimentRunning = false;
    // The runs a judgment is out for. Their Judge button shows a spinner and
    // stays disabled until the judgment is back, whatever else redraws it.
    const judgingRuns = new Set<ExperimentTrackerEntry>();
    const JUDGE_BUTTON_SPINNER = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';
    function updateRunExperimentsButtonState(): void {
      // A run in flight keeps the button as the run left it - disabled, with
      // its spinner - whatever is selected or opened meanwhile. The run puts
      // the button back when its calls have ended.
      if (experimentRunning) return;
      if (promptMode === 'decision') {
        const anyModelSelected = selectedDecisionModels().length > 0;
        const designerReady = decisionDesigner.isValid();
        promptBuilderRunExperimentsButton.disabled = !anyModelSelected || !designerReady;
        promptBuilderRunExperimentsButton.title = !anyModelSelected
          ? `${promptBuilderInterfaceText?.promptBuilderDecisionSelectModelsAlert}`
          : !designerReady
            ? `${promptBuilderInterfaceText?.promptBuilderDecisionNeedsQuestion}`
            : '';
        return;
      }
      const anyLLMSelected = promptBuilderAvailableLLMs.some(
        (_, llmIndex) => (document.getElementById(`model${llmIndex}`) as HTMLInputElement | null)?.checked
      );
      promptBuilderRunExperimentsButton.disabled = !anyLLMSelected;
      promptBuilderRunExperimentsButton.title = anyLLMSelected
        ? ''
        : `${promptBuilderInterfaceText?.promptExperimentSelectModelsAlert}`;
    }
    updateRunExperimentsButtonState();

    // --- LLM-as-a-Judge controls -------------------------------------------
    // Progressive disclosure: pick one judge model (single-judge, as in Phase 1).
    // Once a judge is chosen a "convene a council" question appears; enabling it
    // reveals a vertical list of models to form the panel. One judge = single;
    // a panel of two or more = a council.
    const promptBuilderJudgeControls = document.createElement('div');
    promptBuilderJudgeControls.classList.add('pb-judge-controls', 'd-flex', 'flex-column', 'gap-2', 'mt-2');

    // Row 1 — the single judge model.
    const judgeModelRow = document.createElement('div');
    judgeModelRow.classList.add('d-flex', 'align-items-center', 'gap-2', 'flex-wrap');
    const judgeModelLabel = document.createElement('label');
    judgeModelLabel.classList.add('form-label', 'mb-0');
    judgeModelLabel.htmlFor = `${paneID}-obj-${promptBuilderObject?.id}-judge-model`;
    judgeModelLabel.innerText = `${promptBuilderInterfaceText?.promptBuilderJudgeModelLabel}`;
    const promptBuilderJudgeModelSelect = document.createElement('select');
    promptBuilderJudgeModelSelect.id = `${paneID}-obj-${promptBuilderObject?.id}-judge-model`;
    promptBuilderJudgeModelSelect.classList.add('form-select', 'form-select-sm', 'pb-judge-model');
    promptBuilderJudgeModelSelect.style.width = 'auto';
    const judgePlaceholderOption = document.createElement('option');
    judgePlaceholderOption.value = '';
    judgePlaceholderOption.innerText = `${promptBuilderInterfaceText?.promptBuilderJudgeSelectPlaceholder}`;
    promptBuilderJudgeModelSelect.appendChild(judgePlaceholderOption);
    promptBuilderAvailableLLMs.forEach((availableLLM) => {
      // Models that cannot be used (no resolvable credential, an options.json
      // this page cannot read) cannot judge either.
      if (llmNotUsableNote(availableLLM)) return;
      const judgeOption = document.createElement('option');
      judgeOption.value = availableLLM.name;
      judgeOption.innerText = availableLLM.name;
      promptBuilderJudgeModelSelect.appendChild(judgeOption);
    });
    const configuredJudgeModel = String(promptBuilderObject?.judgeModel ?? '');
    if (configuredJudgeModel && promptBuilderAvailableLLMs.some((llm) => llm.name === configuredJudgeModel)) {
      promptBuilderJudgeModelSelect.value = configuredJudgeModel;
    }
    promptBuilderJudgeModelSelect.addEventListener('change', updateJudgeControlsVisibility);
    judgeModelRow.appendChild(judgeModelLabel);
    judgeModelRow.appendChild(promptBuilderJudgeModelSelect);

    // Row 2 — the council question (hidden until a judge is chosen).
    const judgeCouncilToggleDiv = document.createElement('div');
    judgeCouncilToggleDiv.classList.add('form-check', 'mb-0', 'd-none');
    const promptBuilderJudgeCouncilToggle = document.createElement('input');
    promptBuilderJudgeCouncilToggle.type = 'checkbox';
    promptBuilderJudgeCouncilToggle.classList.add('form-check-input');
    promptBuilderJudgeCouncilToggle.id = `${paneID}-obj-${promptBuilderObject?.id}-judge-council-toggle`;
    const judgeCouncilToggleLabel = document.createElement('label');
    judgeCouncilToggleLabel.classList.add('form-check-label');
    judgeCouncilToggleLabel.htmlFor = promptBuilderJudgeCouncilToggle.id;
    judgeCouncilToggleLabel.innerText = `${promptBuilderInterfaceText?.promptBuilderJudgeCouncilToggleLabel}`;
    // Info icon explaining what a council is, its cost, when to use it, and why
    // an odd number helps — matching the option/include-self tooltips.
    const judgeCouncilInfo = document.createElement('span');
    judgeCouncilInfo.classList.add('info-icon');
    judgeCouncilInfo.style.marginLeft = '4px';
    judgeCouncilInfo.innerHTML = '&#x2139;&#xFE0F;';
    judgeCouncilInfo.setAttribute('tabindex', '0');
    judgeCouncilInfo.setAttribute('role', 'button');
    judgeCouncilInfo.setAttribute('aria-label', `${promptBuilderInterfaceText?.promptBuilderJudgeCouncilToggleLabel}`);
    judgeCouncilInfo.setAttribute('data-bs-toggle', 'tooltip');
    new Tooltip(judgeCouncilInfo, {
      title: String(promptBuilderInterfaceText?.promptBuilderJudgeCouncilInfo),
      html: true,
      container: 'body',
    });
    judgeCouncilToggleDiv.appendChild(promptBuilderJudgeCouncilToggle);
    judgeCouncilToggleDiv.appendChild(judgeCouncilToggleLabel);
    judgeCouncilToggleDiv.appendChild(judgeCouncilInfo);

    // Row 3 — the council members (hidden unless the council question is on),
    // stacked vertically like the model selector above.
    const judgeCouncilSection = document.createElement('div');
    judgeCouncilSection.id = `${paneID}-obj-${promptBuilderObject?.id}-judge-council-section`;
    judgeCouncilSection.classList.add('ms-4', 'd-none', 'd-flex', 'flex-column', 'gap-1');
    const judgeCouncilMembersLabel = document.createElement('label');
    judgeCouncilMembersLabel.classList.add('form-label', 'mb-0');
    judgeCouncilMembersLabel.innerText = `${promptBuilderInterfaceText?.promptBuilderJudgeCouncilMembersLabel}`;
    const promptBuilderJudgePanel = document.createElement('div');
    promptBuilderJudgePanel.id = `${paneID}-obj-${promptBuilderObject?.id}-judge-panel`;
    promptBuilderJudgePanel.classList.add('pb-judge-panel', 'd-flex', 'flex-column', 'gap-1');
    promptBuilderAvailableLLMs.forEach((availableLLM, index) => {
      if (llmNotUsableNote(availableLLM)) return;
      const judgeItem = document.createElement('div');
      judgeItem.classList.add('form-check', 'mb-0');
      const judgeCheckbox = document.createElement('input');
      judgeCheckbox.type = 'checkbox';
      judgeCheckbox.classList.add('form-check-input', 'pb-judge-panel-item');
      judgeCheckbox.id = `${paneID}-obj-${promptBuilderObject?.id}-judge-panel-${index}`;
      judgeCheckbox.value = availableLLM.name;
      judgeCheckbox.addEventListener('change', updateJudgePanelHint);
      const judgeItemLabel = document.createElement('label');
      judgeItemLabel.classList.add('form-check-label');
      judgeItemLabel.htmlFor = judgeCheckbox.id;
      judgeItemLabel.innerText = availableLLM.name;
      judgeItem.appendChild(judgeCheckbox);
      judgeItem.appendChild(judgeItemLabel);
      promptBuilderJudgePanel.appendChild(judgeItem);
    });

    // Fill the panel from the LLMs currently selected for the experiment.
    const judgeUseModelsButton = document.createElement('button');
    judgeUseModelsButton.type = 'button';
    judgeUseModelsButton.id = `${paneID}-obj-${promptBuilderObject?.id}-judge-use-models`;
    judgeUseModelsButton.classList.add('btn', 'btn-outline-secondary', 'btn-sm', 'align-self-start');
    judgeUseModelsButton.innerText = `${promptBuilderInterfaceText?.promptBuilderJudgeUseModelsButton}`;
    judgeUseModelsButton.onclick = () => {
      promptBuilderAvailableLLMs.forEach((_availableLLM, index) => {
        const experimentCheckbox = document.getElementById(`model${index}`) as HTMLInputElement | null;
        const panelCheckbox = document.getElementById(
          `${paneID}-obj-${promptBuilderObject?.id}-judge-panel-${index}`
        ) as HTMLInputElement | null;
        if (panelCheckbox) panelCheckbox.checked = Boolean(experimentCheckbox?.checked);
      });
      updateJudgePanelHint();
    };

    // Inline guidance for the panel (odd / diverse / too-many nudges).
    const judgePanelHint = document.createElement('small');
    judgePanelHint.id = `${paneID}-obj-${promptBuilderObject?.id}-judge-panel-hint`;
    judgePanelHint.classList.add('text-muted');
    // Chairman tiebreaker: optional, only fires when the council ties.
    const judgeChairmanToggleDiv = document.createElement('div');
    judgeChairmanToggleDiv.classList.add('form-check', 'mb-0', 'mt-1');
    const promptBuilderJudgeChairmanToggle = document.createElement('input');
    promptBuilderJudgeChairmanToggle.type = 'checkbox';
    promptBuilderJudgeChairmanToggle.classList.add('form-check-input');
    promptBuilderJudgeChairmanToggle.id = `${paneID}-obj-${promptBuilderObject?.id}-judge-chairman-toggle`;
    const judgeChairmanToggleLabel = document.createElement('label');
    judgeChairmanToggleLabel.classList.add('form-check-label');
    judgeChairmanToggleLabel.htmlFor = promptBuilderJudgeChairmanToggle.id;
    judgeChairmanToggleLabel.innerText = `${promptBuilderInterfaceText?.promptBuilderJudgeChairmanToggleLabel}`;
    const judgeChairmanInfo = document.createElement('span');
    judgeChairmanInfo.classList.add('info-icon');
    judgeChairmanInfo.style.marginLeft = '4px';
    judgeChairmanInfo.innerHTML = '&#x2139;&#xFE0F;';
    judgeChairmanInfo.setAttribute('tabindex', '0');
    judgeChairmanInfo.setAttribute('role', 'button');
    judgeChairmanInfo.setAttribute('aria-label', `${promptBuilderInterfaceText?.promptBuilderJudgeChairmanToggleLabel}`);
    judgeChairmanInfo.setAttribute('data-bs-toggle', 'tooltip');
    new Tooltip(judgeChairmanInfo, {
      title: String(promptBuilderInterfaceText?.promptBuilderJudgeChairmanInfo),
      html: true,
      container: 'body',
    });
    judgeChairmanToggleDiv.appendChild(promptBuilderJudgeChairmanToggle);
    judgeChairmanToggleDiv.appendChild(judgeChairmanToggleLabel);
    judgeChairmanToggleDiv.appendChild(judgeChairmanInfo);

    // Chairman model picker, revealed only when the tiebreaker is on.
    const judgeChairmanModelRow = document.createElement('div');
    judgeChairmanModelRow.id = `${paneID}-obj-${promptBuilderObject?.id}-judge-chairman-row`;
    judgeChairmanModelRow.classList.add('d-flex', 'align-items-center', 'gap-2', 'ms-4', 'd-none');
    const judgeChairmanModelLabel = document.createElement('label');
    judgeChairmanModelLabel.classList.add('form-label', 'mb-0');
    judgeChairmanModelLabel.htmlFor = `${paneID}-obj-${promptBuilderObject?.id}-judge-chairman-model`;
    judgeChairmanModelLabel.innerText = `${promptBuilderInterfaceText?.promptBuilderJudgeChairmanModelLabel}`;
    const promptBuilderJudgeChairmanModel = document.createElement('select');
    promptBuilderJudgeChairmanModel.id = `${paneID}-obj-${promptBuilderObject?.id}-judge-chairman-model`;
    promptBuilderJudgeChairmanModel.classList.add('form-select', 'form-select-sm');
    promptBuilderJudgeChairmanModel.style.width = 'auto';
    const chairmanPlaceholder = document.createElement('option');
    chairmanPlaceholder.value = '';
    chairmanPlaceholder.innerText = `${promptBuilderInterfaceText?.promptBuilderJudgeSelectPlaceholder}`;
    promptBuilderJudgeChairmanModel.appendChild(chairmanPlaceholder);
    promptBuilderAvailableLLMs.forEach((availableLLM) => {
      // A model that cannot judge cannot break a tie either.
      if (llmNotUsableNote(availableLLM)) return;
      const chairmanOption = document.createElement('option');
      chairmanOption.value = availableLLM.name;
      chairmanOption.innerText = availableLLM.name;
      promptBuilderJudgeChairmanModel.appendChild(chairmanOption);
    });
    judgeChairmanModelRow.appendChild(judgeChairmanModelLabel);
    judgeChairmanModelRow.appendChild(promptBuilderJudgeChairmanModel);
    promptBuilderJudgeChairmanToggle.addEventListener('change', () => {
      judgeChairmanModelRow.classList.toggle('d-none', !promptBuilderJudgeChairmanToggle.checked);
    });

    judgeCouncilSection.appendChild(judgeCouncilMembersLabel);
    judgeCouncilSection.appendChild(promptBuilderJudgePanel);
    judgeCouncilSection.appendChild(judgeUseModelsButton);
    judgeCouncilSection.appendChild(judgePanelHint);
    judgeCouncilSection.appendChild(judgeChairmanToggleDiv);
    judgeCouncilSection.appendChild(judgeChairmanModelRow);

    // The judges currently in effect: the panel when a council is on, else the
    // single dropdown selection.
    function getSelectedJudgeModels(): string[] {
      if (promptBuilderJudgeCouncilToggle.checked) {
        return Array.from(promptBuilderJudgePanel.querySelectorAll('.pb-judge-panel-item'))
          .filter((el) => (el as HTMLInputElement).checked)
          .map((el) => (el as HTMLInputElement).value);
      }
      return promptBuilderJudgeModelSelect.value ? [promptBuilderJudgeModelSelect.value] : [];
    }
    function updateJudgePanelHint(): void {
      const count = getSelectedJudgeModels().length;
      if (count < 2) {
        judgePanelHint.innerText = `${promptBuilderInterfaceText?.promptBuilderJudgePanelHintNone}`;
        return;
      }
      const parts = [`${count} ${promptBuilderInterfaceText?.promptBuilderJudgePanelHintCouncil}`];
      if (count % 2 === 0) parts.push(`${promptBuilderInterfaceText?.promptBuilderJudgePanelHintEven}`);
      if (count > 5) parts.push(`${promptBuilderInterfaceText?.promptBuilderJudgePanelHintTooMany}`);
      judgePanelHint.innerText = parts.join(' ');
    }
    // Show the council question only once a judge is chosen; show the member
    // list only when the council question is on; the dropdown is the single
    // judge and is disabled while a council governs the selection.
    function updateJudgeControlsVisibility(): void {
      const hasJudge = Boolean(promptBuilderJudgeModelSelect.value);
      judgeCouncilToggleDiv.classList.toggle('d-none', !hasJudge);
      if (!hasJudge) promptBuilderJudgeCouncilToggle.checked = false;
      const councilOn = hasJudge && promptBuilderJudgeCouncilToggle.checked;
      judgeCouncilSection.classList.toggle('d-none', !councilOn);
      promptBuilderJudgeModelSelect.disabled = councilOn;
      updateJudgePanelHint();
    }
    // Turning the council on seeds the panel with the chosen single judge.
    promptBuilderJudgeCouncilToggle.addEventListener('change', () => {
      if (promptBuilderJudgeCouncilToggle.checked) {
        const primary = promptBuilderJudgeModelSelect.value;
        promptBuilderAvailableLLMs.forEach((availableLLM, index) => {
          const panelCheckbox = document.getElementById(
            `${paneID}-obj-${promptBuilderObject?.id}-judge-panel-${index}`
          ) as HTMLInputElement | null;
          if (panelCheckbox && availableLLM.name === primary) panelCheckbox.checked = true;
        });
      }
      updateJudgeControlsVisibility();
    });

    const judgeIncludeSelfDiv = document.createElement('div');
    judgeIncludeSelfDiv.classList.add('form-check', 'mb-0');
    const promptBuilderJudgeIncludeSelf = document.createElement('input');
    promptBuilderJudgeIncludeSelf.type = 'checkbox';
    promptBuilderJudgeIncludeSelf.classList.add('form-check-input');
    promptBuilderJudgeIncludeSelf.id = `${paneID}-obj-${promptBuilderObject?.id}-judge-include-self`;
    const judgeIncludeSelfLabel = document.createElement('label');
    judgeIncludeSelfLabel.classList.add('form-check-label');
    judgeIncludeSelfLabel.htmlFor = promptBuilderJudgeIncludeSelf.id;
    judgeIncludeSelfLabel.innerText = `${promptBuilderInterfaceText?.promptBuilderJudgeIncludeSelfLabel}`;
    // Info icon explaining self-preference bias, matching the option tooltips.
    const judgeIncludeSelfInfo = document.createElement('span');
    judgeIncludeSelfInfo.classList.add('info-icon');
    judgeIncludeSelfInfo.style.marginLeft = '4px';
    judgeIncludeSelfInfo.innerHTML = '&#x2139;&#xFE0F;';
    judgeIncludeSelfInfo.setAttribute('tabindex', '0');
    judgeIncludeSelfInfo.setAttribute('role', 'button');
    judgeIncludeSelfInfo.setAttribute('aria-label', `${promptBuilderInterfaceText?.promptBuilderJudgeIncludeSelfLabel}`);
    judgeIncludeSelfInfo.setAttribute('data-bs-toggle', 'tooltip');
    new Tooltip(judgeIncludeSelfInfo, {
      title: String(promptBuilderInterfaceText?.promptBuilderJudgeIncludeSelfInfo),
      html: true,
      container: 'body',
    });
    judgeIncludeSelfDiv.appendChild(promptBuilderJudgeIncludeSelf);
    judgeIncludeSelfDiv.appendChild(judgeIncludeSelfLabel);
    judgeIncludeSelfDiv.appendChild(judgeIncludeSelfInfo);

    const judgeAutoDiv = document.createElement('div');
    judgeAutoDiv.classList.add('form-check', 'mb-0');
    const promptBuilderJudgeAuto = document.createElement('input');
    promptBuilderJudgeAuto.type = 'checkbox';
    promptBuilderJudgeAuto.classList.add('form-check-input');
    promptBuilderJudgeAuto.id = `${paneID}-obj-${promptBuilderObject?.id}-judge-auto`;
    const judgeAutoLabel = document.createElement('label');
    judgeAutoLabel.classList.add('form-check-label');
    judgeAutoLabel.htmlFor = promptBuilderJudgeAuto.id;
    judgeAutoLabel.innerText = `${promptBuilderInterfaceText?.promptBuilderJudgeAutoLabel}`;
    judgeAutoDiv.appendChild(promptBuilderJudgeAuto);
    judgeAutoDiv.appendChild(judgeAutoLabel);

    promptBuilderJudgeControls.appendChild(judgeModelRow);
    promptBuilderJudgeControls.appendChild(judgeCouncilToggleDiv);
    promptBuilderJudgeControls.appendChild(judgeCouncilSection);
    promptBuilderJudgeControls.appendChild(judgeIncludeSelfDiv);
    promptBuilderJudgeControls.appendChild(judgeAutoDiv);
    updateJudgeControlsVisibility();

    const promptBuilderRunExperimentError = document.createElement('p');
    promptBuilderRunExperimentError.style.color = 'red';
    promptBuilderRunExperimentError.id = `${paneID}-obj-${promptBuilderObject?.id}-run-error`;
    let promptExperimentTrackerRunID = 0;
    let promptExperimentTracker: ExperimentTrackerEntry[] = [];
    // Set when a run was deleted since the last save/load, so an emptied
    // tracker can still be saved.
    let experimentsModified = false;
    // Set while the Evaluate step asks the decision models about the rows of
    // a CAS table: a loop of calls that, like a run, blocks a change of kind.
    let evaluationRunning = false;
    // One optimization job at a time; the poll handle so a prompt change or a
    // finished job can stop the polling.
    let optimizeJobActive = false;
    let optimizePollHandle: number | null = null;

    // Add prompt evaluations here
    function annotatePrompts(arr: ExperimentResult[]): void {
      if (!Array.isArray(arr) || arr.length === 0) return;

      let fastestIndex = 0;
      let fewestTokensIndex = 0;
      let minRunTime = arr[0]?.data?.run_time;
      let minOutputLength = arr[0]?.data?.output_length;

      for (let i = 1; i < arr.length; i++) {
        const { run_time, output_length } = arr[i]?.data;

        if (run_time < minRunTime) {
          minRunTime = run_time;
          fastestIndex = i;
        }
        if (output_length < minOutputLength) {
          minOutputLength = output_length;
          fewestTokensIndex = i;
        }
      }

      // Estimated per-response cost (when the model carries prices) and the
      // cheapest response — considered only among priced responses.
      let cheapestIndex = -1;
      let minCost = Infinity;
      for (let i = 0; i < arr.length; i++) {
        const cost = computeCallCost(arr[i].data, llmAttributesByName.get(arr[i].modelName));
        arr[i].data.cost = cost;
        if (cost !== null && cost < minCost) {
          minCost = cost;
          cheapestIndex = i;
        }
      }

      for (let i = 0; i < arr.length; i++) {
        arr[i].data.fastest_prompt = i === fastestIndex;
        arr[i].data.fewest_tokens_prompt = i === fewestTokensIndex;
        arr[i].data.cheapest_prompt = i === cheapestIndex;
      }
    }

    // What the toast says after a run in which some of the selected models,
    // but not all, did not answer.
    function partialFailureText(failedCount: number, selectedCount: number): string {
      return `${promptBuilderInterfaceText?.promptBuilderRunPartialFailure}`
        .replace('{failed}', String(failedCount))
        .replace('{total}', String(selectedCount));
    }

    async function promptBuilderRunExperiment(): Promise<void> {
      // Add a spinner to the button
      const promptBuilderRunExperimentTargetButton = document.getElementById(
        `${paneID}-obj-${promptBuilderObject?.id}-run-experiment`
      ) as HTMLButtonElement;
      promptBuilderRunExperimentTargetButton.disabled = true;
      promptBuilderRunExperimentTargetButton.innerHTML = `<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> ${promptBuilderInterfaceText.promptBuilderRunExperimentsButtonRunStatus}`;
      experimentRunning = true;
      // Reset error message
      const promptBuilderRunExperimentErrorText = document.getElementById(
        `${paneID}-obj-${promptBuilderObject?.id}-run-error`
      );
      if (promptBuilderRunExperimentErrorText) promptBuilderRunExperimentErrorText.innerText = '';
      const promptBuilderSelectedModels: { currentlySelectedModel: { name: string; options: Record<string, unknown> } }[] = [];
      promptBuilderAvailableLLMs.forEach((promptBuilderCurrentLLM, index) => {
        const promptBuilderCheckbox = document.getElementById(`model${index}`) as HTMLInputElement;
        if (promptBuilderCheckbox.checked) {
          const currentlySelectedModel: { name: string; options: Record<string, unknown> } = {
            name: promptBuilderCurrentLLM.name,
            // No prototype: an option named __proto__ is a field like any other.
            options: Object.create(null) as Record<string, unknown>,
          };
          Object.keys(promptBuilderCurrentLLM.options ?? {}).forEach((key) => {
            if (key !== 'API_KEY') {
              const optionMeta = promptBuilderCurrentLLM.options![key];
              const control = document.getElementById(optionControlId(index, key, optionMeta)) as
                | HTMLInputElement
                | HTMLSelectElement
                | null;
              if (!control) return; // no control rendered for this option - the score code default applies
              if (optionMeta?.type === 'bool') {
                currentlySelectedModel.options[`${key}`] = (control as HTMLInputElement).checked;
              } else if (optionMeta?.type === 'enum' || optionMeta?.type === 'string') {
                // A blank string option is "not set": omitting it lets the score
                // code fall back to container env vars (AZURE_OPENAI_RESOURCE etc.)
                if (optionMeta?.type === 'string' && control.value === '') return;
                currentlySelectedModel.options[`${key}`] = control.value;
              } else if (isNumberOption(optionMeta)) {
                const numeric = parseFloat(control.value);
                // An emptied number field is sent empty
                currentlySelectedModel.options[`${key}`] = Number.isNaN(numeric) ? control.value : numeric;
              } else {
                // An option of no such type whose default is no number has a text field
                currentlySelectedModel.options[`${key}`] = control.value;
              }
            } else if (key === 'API_KEY') {
              const apiKeys = promptBuilderObject?.API_KEYS as Record<string, string> | undefined;
              currentlySelectedModel.options[`${key}`] =
                apiKeys?.[promptBuilderCurrentLLM.options![key]?.default as string] ?? '';
            }
          });
          promptBuilderSelectedModels.push({ currentlySelectedModel });
        }
      });

      // Catch if the user hasn't selected any LLM
      if (promptBuilderSelectedModels.length === 0) {
        alert(promptBuilderInterfaceText.promptExperimentSelectModelsAlert);
        promptBuilderRunExperimentTargetButton.disabled = false;
        promptBuilderRunExperimentTargetButton.innerText = `${promptBuilderInterfaceText?.promptBuilderRunExperimentsButton}`;
        experimentRunning = false;
        return;
      }

      const systemPrompt = (
        document.getElementById(`${paneID}-obj-${promptBuilderObject?.id}-system-prompt`) as HTMLTextAreaElement
      ).value;
      const userPrompt = (
        document.getElementById(`${paneID}-obj-${promptBuilderObject?.id}-user-prompt`) as HTMLTextAreaElement
      ).value;
      // The tracker stores the templates plus a snapshot of the variables; the
      // LLMs receive the prompts with the {{variable}} values filled in.
      const promptVariables = collectPromptVariables();
      const resolvedSystemPrompt = substitutePromptVariables(systemPrompt, promptVariables);
      const resolvedUserPrompt = substitutePromptVariables(userPrompt, promptVariables);
      // The run is kept as an object, not as a position: the tracker it is
      // pushed into belongs to the prompt-test that is open now, and by the
      // time the models have answered another one may be open (see below).
      const runEntry: ExperimentTrackerEntry = {
        systemPrompt: systemPrompt,
        userPrompt: userPrompt,
        variables: promptVariables,
        manifest: collectManifestConfig(),
      };
      promptExperimentTracker.push(runEntry);
      const restoreRunButton = (): void => {
        experimentRunning = false;
        promptBuilderRunExperimentTargetButton.disabled = false;
        promptBuilderRunExperimentTargetButton.innerText = `${promptBuilderInterfaceText?.promptBuilderRunExperimentsButton}`;
        // What is selected may have changed while the calls were out.
        updateRunExperimentsButtonState();
        // Runs drawn while the calls were out got a disabled Judge button.
        refreshJudgeButtons();
      };
      // Whether the tracker still holds this run. Opening another project or
      // prompt-test, or deleting this one, replaces the tracker while the
      // calls are out; their answers then belong to nothing on the page and
      // are dropped - no result, no error text and nothing taken from the
      // runs of the prompt-test that is open by then.
      const runIsCurrent = (): boolean => promptExperimentTracker.includes(runEntry);

      const allPromises: Promise<ExperimentResult>[] = [];

      for (const modelObj of promptBuilderSelectedModels) {
        const modelName = modelObj.currentlySelectedModel.name;
        const options = modelObj.currentlySelectedModel.options ?? {};

        allPromises.push(
          callSCRLLM(
            promptBuilderObject.SCREndpoint as string,
            modelName,
            resolvedSystemPrompt,
            resolvedUserPrompt,
            options,
            (promptBuilderObject.deploymentType as string) ?? 'k8s'
          ).then((data) => ({ modelName, data: data as ExperimentResult['data'], options }))
        );
      }

      const results = await Promise.all(allPromises);
      if (!runIsCurrent()) {
        restoreRunButton();
        return;
      }
      // A model whose call failed is named with its error and leaves no entry
      // in the run; the models that answered keep their results, so one
      // container that is down does not cost the whole run.
      const answered = results.filter((result) => !result.data?.error);
      const failed = results.filter((result) => result.data?.error);
      if (promptBuilderRunExperimentErrorText) {
        promptBuilderRunExperimentErrorText.innerText = failed
          .map((result) => describeFailedCall(result.modelName, result.data?.error, promptBuilderInterfaceText))
          .join('\n');
      }
      if (answered.length === 0) {
        // No model answered: the run pushed above would render empty and be
        // saved empty, so it is taken back and nothing is rendered or judged.
        promptExperimentTracker.splice(promptExperimentTracker.indexOf(runEntry), 1);
        restoreRunButton();
        eraseWithdrawnRun();
        return;
      }
      // Load the (cached) cost attributes of just the models that answered, so
      // cost + cheapest can be computed. Guarded internally — never blocks a run.
      await Promise.all(answered.map((result) => ensureLLMCostAttributes(result.modelName)));
      if (!runIsCurrent()) {
        restoreRunButton();
        return;
      }
      // Identify fastest prompt and fewest tokens used prompt among the answers
      annotatePrompts(answered);
      const trackerEntry = runEntry as Record<string, unknown>;
      for (const { modelName, data, options } of answered) {
        trackerEntry[`${modelName}`] = {
          best_prompt: null,
          fastest_prompt: data?.fastest_prompt,
          fewest_tokens_prompt: data?.fewest_tokens_prompt,
          cheapest_prompt: data?.cheapest_prompt ?? null,
          judge_rank: null,
          judge_best: null,
          output_length: data?.output_length,
          prompt_length: data?.prompt_length,
          run_time: data?.run_time,
          cost: data?.cost ?? null,
          options: options,
          response: data?.response,
        } as ModelExperimentData;
      }

      // The run has ended: the flag is cleared before the run is drawn. Drawn
      // first, the run got its Judge button disabled ("a run is out") and
      // nothing enabled it again - Judge this run stayed unusable after every
      // run until the prompt-test was opened anew.
      restoreRunButton();
      drawFinishedRun(runEntry, systemPrompt, userPrompt);

      // The new run sits at the top of the tracker, right below the prompts.
      promptExperimentTrackerHeader.scrollIntoView({ behavior: 'smooth', block: 'start' });
      // The page has just moved away from the error text, so a run that lost
      // models says so where it is seen.
      if (failed.length > 0) showToast(partialFailureText(failed.length, results.length));

      // Auto-judge the run just rendered, when the user opted in and a judge
      // model is selected. Silent: preconditions that don't hold (no judge
      // model, < 2 judgeable responses) simply skip without a toast.
      if (promptBuilderJudgeAuto.checked && getSelectedJudgeModels().length > 0) {
        await promptBuilderJudgeRun(promptExperimentTrackerRunID - 1, true);
      }
    }

    /**
     * A decision run: the state (variables filled in) and the questions go to
     * every selected decision model; each answer map lands in the tracker as
     * the model's response, marked correct when it matches the expected
     * answers given in the designer.
     */
    async function promptBuilderRunDecisionExperiment(): Promise<void> {
      const runButton = promptBuilderRunExperimentsButton;
      const restore = (): void => {
        experimentRunning = false;
        runButton.disabled = false;
        runButton.innerText = `${promptBuilderInterfaceText?.promptBuilderRunExperimentsButton}`;
        // What is selected may have changed while the calls were out.
        updateRunExperimentsButtonState();
        refreshJudgeButtons();
      };
      runButton.disabled = true;
      runButton.innerHTML = `<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> ${promptBuilderInterfaceText.promptBuilderRunExperimentsButtonRunStatus}`;
      experimentRunning = true;
      promptBuilderRunExperimentError.innerText = '';
      if (selectedDecisionModels().length === 0) {
        alert(promptBuilderInterfaceText.promptBuilderDecisionSelectModelsAlert);
        restore();
        return;
      }
      // Nothing left to fix in the designer, and only the models that can
      // take this request are asked; the others are named, not failed.
      if (!decisionDesigner.isValid()) {
        alert(promptBuilderInterfaceText.promptBuilderDecisionNeedsQuestion);
        restore();
        return;
      }
      const { fit: models, unfit } = fitDecisionModels();
      const skippedLine =
        unfit.length > 0
          ? `${promptBuilderInterfaceText.promptBuilderDecisionSkippedUnfit}`.replace('{models}', unfit.map((model) => model.name).join(', '))
          : '';
      promptBuilderRunExperimentError.innerText = skippedLine;
      if (models.length === 0) {
        restore();
        return;
      }
      const questions = decisionDesigner.getQuestions();
      const stateTemplate = decisionDesigner.stateInput.value;
      const promptVariables = collectPromptVariables();
      const resolvedState = substitutePromptVariables(stateTemplate, promptVariables);
      const questionsJson = questionsToJson(questions);
      const expected = decisionDesigner.getExpected();
      // The tracker keeps the question map as the run's system prompt and the
      // state template as its user prompt, so the persisted rows, the SAS table
      // and the loaders keep their shape.
      // Kept as an object and looked for again after every wait, as in an LLM
      // run: the tracker may be that of another prompt-test by then.
      const runEntry: ExperimentTrackerEntry = {
        systemPrompt: questionsJson,
        userPrompt: stateTemplate,
        variables: promptVariables,
        manifest: collectManifestConfig(),
        mode: 'decision',
        expected,
      };
      promptExperimentTracker.push(runEntry);
      const runIsCurrent = (): boolean => promptExperimentTracker.includes(runEntry);
      const results = await Promise.all(
        models.map((model) => {
          const options = decisionCallOptions(model);
          return callSCRDecision(
            promptBuilderObject.SCREndpoint as string,
            model.name,
            resolvedState,
            questionsJson,
            options,
            (promptBuilderObject.deploymentType as string) ?? 'k8s'
          ).then((data) => ({ modelName: model.name, data: data as Record<string, unknown>, options }));
        })
      );
      if (!runIsCurrent()) {
        restore();
        return;
      }
      // As in an LLM run, a model whose call failed is named with its error
      // and leaves no entry; its line joins the one naming the skipped models.
      const answered = results.filter((result) => !result.data?.error);
      const failed = results.filter((result) => result.data?.error);
      promptBuilderRunExperimentError.innerText = [
        skippedLine,
        ...failed.map((result) => describeFailedCall(result.modelName, result.data?.error, promptBuilderInterfaceText)),
      ]
        .filter(Boolean)
        .join('\n');
      if (answered.length === 0) {
        // No model answered: the run pushed above is taken back instead of
        // being rendered, and saved, empty.
        promptExperimentTracker.splice(promptExperimentTracker.indexOf(runEntry), 1);
        restore();
        eraseWithdrawnRun();
        return;
      }
      await Promise.all(answered.map((result) => ensureLLMCostAttributes(result.modelName)));
      if (!runIsCurrent()) {
        restore();
        return;
      }
      const trackerEntry = runEntry as Record<string, unknown>;
      // Fastest and cheapest among the answers; there are no output tokens to count.
      const runTimes = answered.map((result) => Number(result.data?.run_time));
      const fastestIndex = runTimes.reduce<number>(
        (best, value, index) => (Number.isFinite(value) && (best < 0 || value < runTimes[best]) ? index : best),
        -1
      );
      const costs = answered.map((result) => computeCallCost(
        { prompt_length: Number(result.data?.prompt_length), output_length: 0, run_time: Number(result.data?.run_time) },
        llmAttributesByName.get(result.modelName)
      ));
      const cheapestIndex = costs.reduce<number>(
        (best, value, index) => (value !== null && (best < 0 || (costs[best] ?? Infinity) > value) ? index : best),
        -1
      );
      // A run has one Best Decision Model: the first result whose answers
      // all match is ticked; another one that matches carries the note only.
      let bestTicked = false;
      answered.forEach(({ modelName, data, options }, index) => {
        const answers = parseAnswers(data?.answers);
        const matched = allExpectedMatch(questions, answers, expected);
        const best = matched === true && !bestTicked;
        if (best) bestTicked = true;
        trackerEntry[modelName] = {
          best_prompt: matched === null ? null : best,
          fastest_prompt: index === fastestIndex,
          fewest_tokens_prompt: null,
          cheapest_prompt: index === cheapestIndex,
          judge_rank: null,
          judge_best: null,
          output_length: Number(data?.output_length) || 0,
          prompt_length: data?.prompt_length == null ? null : Number(data.prompt_length),
          run_time: data?.run_time == null ? null : Number(data.run_time),
          cost: costs[index],
          options,
          response: answers ? JSON.stringify(answers) : String(data?.answers ?? ''),
        } as ModelExperimentData;
      });
      // As in an LLM run: the flag is cleared before the run is drawn.
      restore();
      drawFinishedRun(runEntry, questionsJson, stateTemplate);
      promptExperimentTrackerHeader.scrollIntoView({ behavior: 'smooth', block: 'start' });
      if (failed.length > 0) showToast(partialFailureText(failed.length, results.length));
    }

    const promptExperimentTrackerHeader = document.createElement('h2');
    promptExperimentTrackerHeader.innerText = `${promptBuilderInterfaceText?.promptExperimentTrackerHeading}`;
    // Empty-state hint, shown while no experiment runs exist
    const promptExperimentEmptyHint = document.createElement('p');
    promptExperimentEmptyHint.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-empty`;
    promptExperimentEmptyHint.classList.add('text-muted');
    promptExperimentEmptyHint.innerText = `${promptBuilderInterfaceText?.promptExperimentTrackerEmpty}`;
    function updateTrackerEmptyState(): void {
      promptExperimentEmptyHint.style.display = promptExperimentTracker.length === 0 ? '' : 'none';
    }
    const promptExperimentContainer = document.createElement('div');
    promptExperimentContainer.id = `${paneID}-obj-${promptBuilderObject?.id}-pet`;

    // Beside the instructions: how many runs there are and how many carry a
    // Best Response (what Finalize and Optimize build on), then a legend of
    // the icons a run shows - they are otherwise explained by tooltip only.
    const promptExperimentSummary = document.createElement('p');
    promptExperimentSummary.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-summary`;
    promptExperimentSummary.classList.add('mb-0', 'fw-semibold');
    function updateTrackerSummary(): void {
      const runCount = promptExperimentTracker.length;
      const bestCount = promptExperimentTracker.filter((trackerEntry) =>
        Object.keys(trackerEntry).some(
          (key) => !TRACKER_META_KEYS.includes(key) && (trackerEntry[key] as ModelExperimentData)?.best_prompt
        )
      ).length;
      promptExperimentSummary.style.display = runCount === 0 ? 'none' : '';
      promptExperimentSummary.innerText = `${promptMode === 'decision' ? promptBuilderInterfaceText?.promptExperimentDecisionSummary : promptBuilderInterfaceText?.promptExperimentSummary}`
        .replace('{runs}', String(runCount))
        .replace('{best}', String(bestCount));
    }
    const promptExperimentLegend = document.createElement('div');
    promptExperimentLegend.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-legend`;
    promptExperimentLegend.classList.add('pb-legend');
    const addLegendGroup = (headingText: unknown, entries: Array<[string, unknown]>): void => {
      const group = document.createElement('div');
      const groupHeading = document.createElement('p');
      groupHeading.classList.add('pb-legend-heading');
      groupHeading.innerText = `${headingText}`;
      group.appendChild(groupHeading);
      const list = document.createElement('ul');
      entries.forEach(([iconPath, labelText]) => {
        const item = document.createElement('li');
        item.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" height="18px" viewBox="0 -960 960 960" width="18px" fill="currentColor" aria-hidden="true"><path d="${iconPath}"/></svg>`;
        const label = document.createElement('span');
        label.innerText = `${labelText}`;
        item.appendChild(label);
        list.appendChild(item);
      });
      group.appendChild(list);
      promptExperimentLegend.appendChild(group);
    };
    function renderTrackerLegend(): void {
      promptExperimentLegend.innerHTML = '';
      const decision = promptMode === 'decision';
      addLegendGroup(promptBuilderInterfaceText?.promptExperimentLegendResponses, [
        [TRACKER_ICON_PATHS.best, decision ? promptBuilderInterfaceText?.promptExperimentLegendCorrect : promptBuilderInterfaceText?.promptExperimentLegendBest],
        ...(decision ? [] : [[TRACKER_ICON_PATHS.judgeBest, promptBuilderInterfaceText?.promptExperimentLegendJudgeBest] as [string, unknown]]),
        [TRACKER_ICON_PATHS.fastest, promptBuilderInterfaceText?.promptExperimentLegendFastest],
        ...(decision ? [] : [[TRACKER_ICON_PATHS.fewestTokens, promptBuilderInterfaceText?.promptExperimentLegendFewestTokens] as [string, unknown]]),
        [TRACKER_ICON_PATHS.cheapest, promptBuilderInterfaceText?.promptExperimentLegendCheapest],
      ]);
      addLegendGroup(promptBuilderInterfaceText?.promptExperimentLegendActions, [
        [TRACKER_ICON_PATHS.load, promptBuilderInterfaceText?.promptExperimentLegendLoad],
        [TRACKER_ICON_PATHS.remove, promptBuilderInterfaceText?.promptExperimentLegendDelete],
        ...(decision ? [] : [[TRACKER_ICON_PATHS.judge, promptBuilderInterfaceText?.promptExperimentLegendJudge] as [string, unknown]]),
      ]);
    }
    renderTrackerLegend();

    // A disabled <button> does not fire hover events, so its own `title` never
    // shows as a tooltip. Wrap it in a span that carries the hint and let the
    // pointer fall through to the span while the button is disabled, so the
    // "why is this disabled" tooltip appears on hover.
    function wrapForHint(button: HTMLButtonElement): HTMLSpanElement {
      const wrapper = document.createElement('span');
      wrapper.classList.add('d-inline-block');
      wrapper.appendChild(button);
      return wrapper;
    }
    function setDisabledHint(
      button: HTMLButtonElement,
      wrapper: HTMLElement,
      disabled: boolean,
      hint: string
    ): void {
      button.disabled = disabled;
      button.style.pointerEvents = disabled ? 'none' : '';
      // Keep the hint on the button too (harmless, and read by tests); the
      // wrapper is what actually surfaces it on hover while disabled.
      button.title = disabled ? hint : '';
      wrapper.title = disabled ? hint : '';
    }

    // Localised label for a judge confidence level.
    function judgeConfidenceText(confidence?: string | null): string {
      switch (confidence) {
        case 'high':
          return `${promptBuilderInterfaceText.promptBuilderJudgeConfidenceHigh}`;
        case 'medium':
          return `${promptBuilderInterfaceText.promptBuilderJudgeConfidenceMedium}`;
        case 'low':
          return `${promptBuilderInterfaceText.promptBuilderJudgeConfidenceLow}`;
        default:
          return `${promptBuilderInterfaceText.promptBuilderJudgeConfidenceUnknown}`;
      }
    }

    // Build the judge verdict banner shown in a run's body.
    // A muted "est. judging cost: N" line, shown when the judging cost is known.
    function buildJudgingCostLine(judge: JudgeSummary): HTMLElement | null {
      if (judge.judgeCost == null) return null;
      const costLine = document.createElement('p');
      costLine.classList.add('mb-1', 'small', 'text-muted');
      costLine.innerText = `${promptBuilderInterfaceText.promptBuilderJudgingCostLabel} ${formatCost(judge.judgeCost)}`;
      return costLine;
    }

    function buildJudgeBanner(judge: JudgeSummary): HTMLElement {
      if (judge.mode === 'council') return buildCouncilBanner(judge);
      const banner = document.createElement('div');
      banner.classList.add('alert', 'pb-judge-banner', 'mt-2');
      if (judge.status === 'ok') {
        banner.classList.add('alert-info');
        const winnerLine = document.createElement('p');
        winnerLine.classList.add('mb-1');
        const confidenceBadge = `<span class="badge bg-secondary ms-2">${promptBuilderInterfaceText.promptBuilderJudgeVerdictConfidence} ${escapeHtml(judgeConfidenceText(judge.confidence))}</span>`;
        winnerLine.innerHTML = `<b>${promptBuilderInterfaceText.promptBuilderJudgeVerdictWinner}</b> ${escapeHtml(String(judge.best ?? ''))} ${confidenceBadge}`;
        banner.appendChild(winnerLine);
        if (Array.isArray(judge.ranking) && judge.ranking.length > 0) {
          const rankingLine = document.createElement('p');
          rankingLine.classList.add('mb-1', 'small');
          rankingLine.innerHTML = `<b>${promptBuilderInterfaceText.promptBuilderJudgeVerdictRanking}</b> ${escapeHtml(judge.ranking.join(' > '))}`;
          banner.appendChild(rankingLine);
        }
        const judgedByLine = document.createElement('p');
        judgedByLine.classList.add('mb-1', 'small', 'text-muted');
        judgedByLine.innerText = `${promptBuilderInterfaceText.promptBuilderJudgedBy}: ${judge.judgeModel}`;
        banner.appendChild(judgedByLine);
        const singleCostLine = buildJudgingCostLine(judge);
        if (singleCostLine) banner.appendChild(singleCostLine);
        if (judge.excludedSelf || judge.includedSelf) {
          const selfNote = document.createElement('p');
          selfNote.classList.add('mb-1', 'small', 'text-muted');
          selfNote.innerText = judge.includedSelf
            ? `${promptBuilderInterfaceText.promptBuilderJudgeSelfIncludedNote}`
            : `${promptBuilderInterfaceText.promptBuilderJudgeSelfExcludedNote}`;
          banner.appendChild(selfNote);
        }
        if (judge.reasoning && judge.reasoning.trim() !== '') {
          const details = document.createElement('details');
          const summary = document.createElement('summary');
          summary.innerText = `${promptBuilderInterfaceText.promptBuilderJudgeShowReasoning}`;
          details.appendChild(summary);
          details.appendChild(renderMarkdown(String(judge.reasoning)));
          banner.appendChild(details);
        } else {
          const noReason = document.createElement('p');
          noReason.classList.add('mb-1', 'small', 'text-muted', 'fst-italic');
          noReason.innerText = `${promptBuilderInterfaceText.promptBuilderJudgeReasoningUnavailable}`;
          banner.appendChild(noReason);
        }
        const suggestion = document.createElement('p');
        suggestion.classList.add('mb-0', 'small', 'text-muted');
        suggestion.innerText = `${promptBuilderInterfaceText.promptBuilderJudgeSuggestionNote}`;
        banner.appendChild(suggestion);
      } else {
        banner.classList.add('alert-warning');
        const message = document.createElement('p');
        message.classList.add('mb-0');
        message.innerText =
          judge.status === 'unparseable'
            ? `${promptBuilderInterfaceText.promptBuilderJudgeUnparseable}`
            : `${promptBuilderInterfaceText.promptBuilderJudgeErrorPrefix} ${judge.error ?? ''}`;
        banner.appendChild(message);
      }
      return banner;
    }

    // The per-judge ballot breakdown shown inside a council banner.
    function buildBallotList(judge: JudgeSummary): HTMLElement {
      const wrap = document.createElement('div');
      wrap.classList.add('mt-1');
      const heading = document.createElement('p');
      heading.classList.add('mb-1', 'small');
      heading.innerHTML = `<b>${promptBuilderInterfaceText.promptBuilderCouncilBallots}</b>`;
      wrap.appendChild(heading);
      (judge.ballots ?? []).forEach((ballot) => {
        const item = document.createElement('div');
        item.classList.add('small', 'mb-1', 'pb-judge-ballot');
        if (ballot.status === 'ok' && Array.isArray(ballot.ranking) && ballot.ranking.length > 0) {
          const line = document.createElement('div');
          line.innerHTML = `<b>${escapeHtml(ballot.judgeModel)}</b>: ${escapeHtml(ballot.ranking[0])} <span class="text-muted">(${escapeHtml(judgeConfidenceText(ballot.confidence))})</span>`;
          item.appendChild(line);
          if (ballot.reasoning && ballot.reasoning.trim() !== '') {
            const details = document.createElement('details');
            const summary = document.createElement('summary');
            summary.innerText = `${promptBuilderInterfaceText.promptBuilderJudgeShowReasoning}`;
            details.appendChild(summary);
            details.appendChild(renderMarkdown(String(ballot.reasoning)));
            item.appendChild(details);
          }
        } else {
          const line = document.createElement('div');
          line.classList.add('text-muted');
          line.innerText = `${ballot.judgeModel}: ${promptBuilderInterfaceText.promptBuilderCouncilBallotFailed}`;
          item.appendChild(line);
        }
        wrap.appendChild(item);
      });
      return wrap;
    }

    // Council verdict banner: aggregate result (or "judges disagreed" on a tie),
    // the agreement signal, the ranking, every judge's ballot, and the footer.
    function buildCouncilBanner(judge: JudgeSummary): HTMLElement {
      const banner = document.createElement('div');
      banner.classList.add('alert', 'pb-judge-banner', 'mt-2');
      if (judge.status !== 'ok') {
        banner.classList.add('alert-warning');
        const message = document.createElement('p');
        message.classList.add('mb-1');
        message.innerText = `${promptBuilderInterfaceText.promptBuilderJudgeErrorPrefix} ${judge.error ?? ''}`;
        banner.appendChild(message);
        banner.appendChild(buildBallotList(judge));
        const degradedCostLine = buildJudgingCostLine(judge);
        if (degradedCostLine) banner.appendChild(degradedCostLine);
        return banner;
      }
      const tie = Boolean(judge.tie);
      banner.classList.add(tie ? 'alert-warning' : 'alert-info');

      const header = document.createElement('p');
      header.classList.add('mb-1');
      if (tie) {
        header.innerHTML = `<b>${promptBuilderInterfaceText.promptBuilderCouncilDisagreed}</b> ${escapeHtml((judge.tiedBest ?? []).join(', '))}`;
      } else {
        const confidenceBadge = `<span class="badge bg-secondary ms-2">${promptBuilderInterfaceText.promptBuilderJudgeVerdictConfidence} ${escapeHtml(judgeConfidenceText(judge.confidence))}</span>`;
        header.innerHTML = `<b>${promptBuilderInterfaceText.promptBuilderCouncilWinner}</b> ${escapeHtml(String(judge.best ?? ''))} ${confidenceBadge}`;
      }
      banner.appendChild(header);

      // A chairman-resolved tie: note who broke it, and offer its reasoning.
      if (!tie && judge.chairman) {
        const chairmanNote = document.createElement('p');
        chairmanNote.classList.add('mb-1', 'small');
        chairmanNote.innerText = `${promptBuilderInterfaceText.promptBuilderCouncilChairmanNote}`.replace(
          '{model}',
          String(judge.chairman.model)
        );
        banner.appendChild(chairmanNote);
        if (judge.chairman.reasoning && judge.chairman.reasoning.trim() !== '') {
          const details = document.createElement('details');
          const summary = document.createElement('summary');
          summary.classList.add('small');
          summary.innerText = `${promptBuilderInterfaceText.promptBuilderJudgeShowReasoning}`;
          details.appendChild(summary);
          details.appendChild(renderMarkdown(String(judge.chairman.reasoning)));
          banner.appendChild(details);
        }
      } else if (!tie && judge.agreement && judge.best) {
        const agreementLine = document.createElement('p');
        agreementLine.classList.add('mb-1', 'small');
        agreementLine.innerText = `${promptBuilderInterfaceText.promptBuilderCouncilAgreement}`
          .replace('{k}', String(judge.agreement.firstChoiceForWinner))
          .replace('{n}', String(judge.agreement.total))
          .replace('{model}', String(judge.best));
        banner.appendChild(agreementLine);
      }

      if (Array.isArray(judge.ranking) && judge.ranking.length > 0) {
        const rankingLine = document.createElement('p');
        rankingLine.classList.add('mb-1', 'small');
        rankingLine.innerHTML = `<b>${promptBuilderInterfaceText.promptBuilderJudgeVerdictRanking}</b> ${escapeHtml(judge.ranking.join(' > '))}`;
        banner.appendChild(rankingLine);
      }

      banner.appendChild(buildBallotList(judge));

      const footer = document.createElement('p');
      footer.classList.add('mb-1', 'small', 'text-muted');
      footer.innerText = `${promptBuilderInterfaceText.promptBuilderCouncilFooter}`.replace(
        '{n}',
        String((judge.panel ?? []).length)
      );
      banner.appendChild(footer);

      const councilCostLine = buildJudgingCostLine(judge);
      if (councilCostLine) banner.appendChild(councilCostLine);

      const suggestion = document.createElement('p');
      suggestion.classList.add('mb-0', 'small', 'text-muted');
      suggestion.innerText = `${promptBuilderInterfaceText.promptBuilderJudgeSuggestionNote}`;
      banner.appendChild(suggestion);
      return banner;
    }

    /** How many responses of a run a judge can compare: the ones that are there and did not fail. */
    function judgeableResponseCount(trackerEntry: ExperimentTrackerEntry): number {
      const judgeFailedText = promptBuilderInterfaceText?.promptBuilderModelInferenceFailed as string;
      return Object.keys(trackerEntry)
        .filter((key) => !TRACKER_META_KEYS.includes(key))
        .map((key) => trackerEntry[key] as ModelExperimentData)
        .filter(
          (modelData) =>
            modelData &&
            typeof modelData.response === 'string' &&
            modelData.response !== '' &&
            modelData.response !== judgeFailedText
        ).length;
    }

    /**
     * The state of one Judge button: usable when its run has two responses to
     * compare, no run is out and no judgment of this run is out. With fewer
     * than two responses the hint says so.
     */
    function applyJudgeButtonState(button: HTMLButtonElement, wrapper: HTMLElement, trackerEntry: ExperimentTrackerEntry): void {
      const judgeableResponses = judgeableResponseCount(trackerEntry);
      setDisabledHint(
        button,
        wrapper,
        experimentRunning || judgingRuns.has(trackerEntry) || judgeableResponses < 2,
        judgeableResponses < 2 ? `${promptBuilderInterfaceText?.promptBuilderJudgeNeedsTwoResponses}` : ''
      );
      // A button drawn anew while the judgment of its run is out (another
      // judgment came back, a run was deleted) shows that it is: disabled
      // alone, with its icon and no hint, it looked dead.
      if (judgingRuns.has(trackerEntry)) button.innerHTML = JUDGE_BUTTON_SPINNER;
    }

    /**
     * Put the Judge button of every drawn run into the state that holds now.
     * A button is drawn once, with the state of that moment; the runs drawn
     * while a run was out (another prompt-test opened meanwhile, a judgment
     * that came back) were drawn disabled and are enabled here when the run
     * has ended. A run whose judgment is still out keeps its spinner.
     */
    function refreshJudgeButtons(): void {
      promptExperimentTracker.forEach((trackerEntry, index) => {
        if (judgingRuns.has(trackerEntry)) return;
        const button = document.getElementById(
          `${paneID}-obj-${promptBuilderObject?.id}-pet-${index}-judge`
        ) as HTMLButtonElement | null;
        if (button?.parentElement) applyJudgeButtonState(button, button.parentElement, trackerEntry);
      });
    }

    /**
     * Draw the run that has just ended. The tracker draws its runs one after
     * the other and counts them. When the whole tracker was drawn again while
     * this run was out (a judgment of an earlier run came back), the run was
     * drawn then, without its answers, and counted - drawing "the next run"
     * would draw nothing. In that case everything is drawn again.
     */
    function drawFinishedRun(runEntry: ExperimentTrackerEntry, first: string, second: string): void {
      if (promptExperimentTracker.indexOf(runEntry) < promptExperimentTrackerRunID) renderAllExperimentRuns(true);
      else createPromptExperimentTracker(promptExperimentTracker, first, second);
    }

    /**
     * Take a run that was withdrawn (no model answered) off the page. Like a
     * run that ended with answers, it was drawn - empty - and counted when the
     * whole tracker was drawn again while its calls were out. The tracker no
     * longer holds it, so more runs are drawn than there are: draw them again.
     * Called after the run has ended, so the Judge buttons are drawn usable.
     */
    function eraseWithdrawnRun(): void {
      if (promptExperimentTrackerRunID > promptExperimentTracker.length) renderAllExperimentRuns(true);
    }

    // Add a prompt experiment tracker to the UI
    function createPromptExperimentTracker(
      tracker: ExperimentTrackerEntry[],
      systemPrompt = '',
      userPrompt = ''
    ): void {
      tracker.forEach((promptExperimentTrackerRunResult, index) => {
        if (index === promptExperimentTrackerRunID) {
          if (systemPrompt === '') {
            systemPrompt = promptExperimentTrackerRunResult.systemPrompt;
          }
          if (userPrompt === '') {
            userPrompt = promptExperimentTrackerRunResult.userPrompt;
          }
          const runIsDecision = promptExperimentTrackerRunResult.mode === 'decision';
          const runQuestions: DecisionQuestion[] = runIsDecision ? questionsFromJson(systemPrompt) : [];
          const runExpected: Record<string, string> = (runIsDecision && promptExperimentTrackerRunResult.expected) || {};
          // Add Run Container
          const promptExperimentRunContainer = document.createElement('div');
          promptExperimentRunContainer.className = 'accordion';
          promptExperimentRunContainer.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-${index}`;
          // Add the accordion main item
          createAccordionItem(
            promptExperimentRunContainer,
            `${paneID}-obj-${promptBuilderObject?.id}-pet-${index}`,
            'run',
            `${promptBuilderInterfaceText.promptExperimentTrackerRunHeader}${index + 1}`
          );
          // Add a delete button for the run as a sibling of the accordion
          // toggle (a button nested inside a button would be invalid HTML)
          const promptExperimentRunHeader = promptExperimentRunContainer.querySelector('.accordion-header') as HTMLElement | null;
          if (promptExperimentRunHeader) {
            promptExperimentRunHeader.classList.add('d-flex', 'align-items-center');
            const loadRunButton = document.createElement('button');
            loadRunButton.type = 'button';
            loadRunButton.classList.add('btn', 'btn-outline-primary', 'btn-sm', 'pet-run-load');
            loadRunButton.title = `${promptBuilderInterfaceText.promptExperimentLoadRunButton}`;
            loadRunButton.setAttribute(
              'aria-label',
              `${promptBuilderInterfaceText.promptExperimentLoadRunButton} ${index + 1}`
            );
            loadRunButton.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" height="20px" viewBox="0 -960 960 960" width="20px" fill="currentColor"><title>${promptBuilderInterfaceText.promptExperimentLoadRunButton}</title><path d="${TRACKER_ICON_PATHS.load}"/></svg>`;
            loadRunButton.onclick = () => {
              loadExperimentRun(index);
              promptBuilderPromptingHeader.scrollIntoView({ behavior: 'smooth', block: 'start' });
            };
            promptExperimentRunHeader.appendChild(loadRunButton);
            const deleteRunButton = document.createElement('button');
            deleteRunButton.type = 'button';
            deleteRunButton.classList.add('btn', 'btn-outline-danger', 'btn-sm', 'pet-run-delete');
            deleteRunButton.title = `${promptBuilderInterfaceText.promptExperimentDeleteRunButton}`;
            deleteRunButton.setAttribute(
              'aria-label',
              `${promptBuilderInterfaceText.promptExperimentDeleteRunButton} ${index + 1}`
            );
            deleteRunButton.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" height="20px" viewBox="0 -960 960 960" width="20px" fill="currentColor"><title>${promptBuilderInterfaceText.promptExperimentDeleteRunButton}</title><path d="${TRACKER_ICON_PATHS.remove}"/></svg>`;
            deleteRunButton.onclick = () => deleteExperimentRun(index);
            promptExperimentRunHeader.appendChild(deleteRunButton);
            // Judge this run — LLM-as-a-Judge ranks the run's responses
            const judgeRunButton = document.createElement('button');
            judgeRunButton.type = 'button';
            judgeRunButton.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-${index}-judge`;
            judgeRunButton.classList.add('btn', 'btn-outline-secondary', 'btn-sm', 'pet-run-judge');
            judgeRunButton.title = `${promptBuilderInterfaceText.promptBuilderJudgeRunButton}`;
            judgeRunButton.setAttribute(
              'aria-label',
              `${promptBuilderInterfaceText.promptBuilderJudgeRunButton} ${index + 1}`
            );
            judgeRunButton.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" height="20px" viewBox="0 -960 960 960" width="20px" fill="currentColor"><title>${promptBuilderInterfaceText.promptBuilderJudgeRunButton}</title><path d="${TRACKER_ICON_PATHS.judge}"/></svg>`;
            judgeRunButton.onclick = () => promptBuilderJudgeRun(index);
            // Judging compares responses, so it needs at least two successful
            // ones in the run. When it can't (e.g. only one model was included),
            // disable it with a hint on hover rather than fail silently on click.
            const judgeRunButtonWrapper = wrapForHint(judgeRunButton);
            // Align with the sibling load/delete buttons in the flex header
            // (the wrapper span would otherwise sit on the text baseline).
            judgeRunButtonWrapper.classList.add('d-inline-flex', 'align-items-center');
            applyJudgeButtonState(judgeRunButton, judgeRunButtonWrapper, promptExperimentTrackerRunResult);
            promptExperimentRunHeader.appendChild(judgeRunButtonWrapper);
            // Judging compares free text; a decision run has answers to compare with expected ones instead.
            if (runIsDecision) judgeRunButtonWrapper.classList.add('d-none');
          }
          const promptExperimentRunContainerItemBody = document.createElement('div');
          promptExperimentRunContainerItemBody.className = 'accordion-body';
          // Add the System Prompt to the main run body
          const promptExperimentRunContainerItemBodySystemPrompt = document.createElement('p');
          promptExperimentRunContainerItemBodySystemPrompt.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-${index}-run-systenPrompt`;
          if (runIsDecision) {
            const questionsLabel = document.createElement('b');
            questionsLabel.innerText = `${promptBuilderInterfaceText.promptExperimentTrackerQuestions}`;
            promptExperimentRunContainerItemBodySystemPrompt.appendChild(questionsLabel);
            promptExperimentRunContainerItemBodySystemPrompt.appendChild(
              renderQuestionList(runQuestions, runExpected, promptBuilderInterfaceText)
            );
          } else {
            promptExperimentRunContainerItemBodySystemPrompt.innerHTML = `<b>${promptBuilderInterfaceText.promptExperimentTrackerSystemPrompt}</b> ${escapeHtml(systemPrompt)}`;
          }
          // Add the User Prompt to the main run body
          const promptExperimentRunContainerItemBodyUserPrompt = document.createElement('p');
          promptExperimentRunContainerItemBodyUserPrompt.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-${index}-run-userPrompt`;
          promptExperimentRunContainerItemBodyUserPrompt.innerHTML = runIsDecision
            ? `<b>${promptBuilderInterfaceText.promptExperimentTrackerState}</b> ${escapeHtml(userPrompt)}`
            : `<b>${promptBuilderInterfaceText.promptExperimentTrackerUserPrompt}</b> ${escapeHtml(userPrompt)}`;
          // Append to the container
          promptExperimentRunContainerItemBody.appendChild(promptExperimentRunContainerItemBodySystemPrompt);
          promptExperimentRunContainerItemBody.appendChild(promptExperimentRunContainerItemBodyUserPrompt);
          // List the variable definitions used by the run, if any
          const promptExperimentRunVariables = promptExperimentTrackerRunResult.variables;
          if (Array.isArray(promptExperimentRunVariables) && promptExperimentRunVariables.length > 0) {
            const variablesLine = document.createElement('p');
            variablesLine.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-${index}-run-variables`;
            const variablesLabel = document.createElement('b');
            variablesLabel.innerText = `${promptBuilderInterfaceText.promptExperimentTrackerVariables}`;
            variablesLine.appendChild(variablesLabel);
            const variablesList = document.createElement('ul');
            (promptExperimentRunVariables as PromptVariable[]).forEach((variable) => {
              const variableItem = document.createElement('li');
              variableItem.textContent = `${variable.name} (${variable.type}): ${variable.value}`;
              if (variable.description) variableItem.title = variable.description;
              variablesList.appendChild(variableItem);
            });
            variablesLine.appendChild(variablesList);
            promptExperimentRunContainerItemBody.appendChild(variablesLine);
          }
          // Judge verdict banner, when this run has been judged
          if (promptExperimentTrackerRunResult.judge) {
            promptExperimentRunContainerItemBody.appendChild(
              buildJudgeBanner(promptExperimentTrackerRunResult.judge as JudgeSummary)
            );
          }
          // Several decision models in one run: their top answers side by side
          // before the per-model details, so a disagreement is seen at once.
          if (runIsDecision) {
            const answersByModel: Record<string, DecisionAnswers | null> = {};
            Object.keys(promptExperimentTrackerRunResult)
              .filter((key) => !TRACKER_META_KEYS.includes(key))
              .forEach((modelName) => {
                answersByModel[modelName] = parseAnswers((promptExperimentTrackerRunResult[modelName] as ModelExperimentData)?.response);
              });
            const comparison = renderComparison(runQuestions, answersByModel, promptBuilderInterfaceText);
            if (comparison) {
              comparison.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-${index}-run-compare`;
              promptExperimentRunContainerItemBody.appendChild(comparison);
            }
          }
          (promptExperimentRunContainer.lastChild as HTMLElement)!.lastChild!.appendChild(promptExperimentRunContainerItemBody);
          // Iterate over the models used in the run
          const promptExperimentContainerModelContainer = document.createElement('div');
          promptExperimentContainerModelContainer.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-${index}-run-nested`;
          for (const promptExperimentRunModelKey in promptExperimentTrackerRunResult) {
            if (!TRACKER_META_KEYS.includes(promptExperimentRunModelKey)) {
              const modelData = promptExperimentTrackerRunResult[promptExperimentRunModelKey] as ModelExperimentData;
              // Create the accordion
              const promptExperimentContainerModelContainerAccordion = document.createElement('div');
              promptExperimentContainerModelContainerAccordion.className = 'accordion nested-accordion mt-3';
              promptExperimentContainerModelContainerAccordion.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-${index}-run-nested-${promptExperimentRunModelKey}`;
              // Create the accordion item
              const promptExperimentContainerModelContainerAccordionItem = document.createElement('div');
              promptExperimentContainerModelContainerAccordionItem.className = 'accordion-item';
              // Create the accordion item header
              const promptExperimentContainerModelContainerAccordionItemHeader = document.createElement('h2');
              promptExperimentContainerModelContainerAccordionItemHeader.className = 'accordion-header';
              // Create the accordion button
              const promptExperimentContainerModelContainerAccordionItemButton = document.createElement('button');
              promptExperimentContainerModelContainerAccordionItemButton.className = 'accordion-button collapsed';
              promptExperimentContainerModelContainerAccordionItemButton.type = 'button';
              promptExperimentContainerModelContainerAccordionItemButton.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-${index}-run-nested-${promptExperimentRunModelKey}-header`;
              promptExperimentContainerModelContainerAccordionItemButton.setAttribute('data-bs-toggle', 'collapse');
              promptExperimentContainerModelContainerAccordionItemButton.setAttribute(
                'data-bs-target',
                `#${paneID}-obj-${promptBuilderObject?.id}-pet-${index}-run-nested-${promptExperimentRunModelKey}-body`
              );
              // Add fastest and fewest token prompt icons if applicable
              if (modelData?.best_prompt) {
                promptExperimentContainerModelContainerAccordionItemButton.innerHTML = `<svg class="bestPrompt" xmlns="http://www.w3.org/2000/svg" height="24px" viewBox="0 -960 960 960" width="24px" fill="#1f1f1f"><title>${runIsDecision ? promptBuilderInterfaceText.promptBuilderBestDecisionModel : promptBuilderInterfaceText.promptBuilderBestPrompt}</title><path d="${TRACKER_ICON_PATHS.best}"/></svg> `;
              }
              if (modelData?.fastest_prompt) {
                promptExperimentContainerModelContainerAccordionItemButton.innerHTML += `<svg xmlns="http://www.w3.org/2000/svg" height="24px" viewBox="0 -960 960 960" width="24px" fill="#1f1f1f"><title>${promptBuilderInterfaceText.promptBuilderFastestPrompt}</title><path d="${TRACKER_ICON_PATHS.fastest}"/></svg> `;
              }
              if (modelData?.fewest_tokens_prompt) {
                promptExperimentContainerModelContainerAccordionItemButton.innerHTML += `<svg xmlns="http://www.w3.org/2000/svg" height="24px" viewBox="0 -960 960 960" width="24px" fill="#1f1f1f"><title>${promptBuilderInterfaceText.promptBuilderFewestTokensPrompt}</title><path d="${TRACKER_ICON_PATHS.fewestTokens}"/></svg> `;
              }
              // Cheapest response — a coin icon, peer to fastest/fewest-tokens
              if (modelData?.cheapest_prompt) {
                promptExperimentContainerModelContainerAccordionItemButton.innerHTML += `<svg class="cheapestPrompt" xmlns="http://www.w3.org/2000/svg" height="24px" viewBox="0 -960 960 960" width="24px" fill="#1f1f1f"><title>${promptBuilderInterfaceText.promptBuilderCheapestPrompt}</title><path d="${TRACKER_ICON_PATHS.cheapest}"/></svg> `;
              }
              // Judge's best response — a fifth icon, peer to the four above
              if (modelData?.judge_best) {
                promptExperimentContainerModelContainerAccordionItemButton.innerHTML += `<svg class="judgeBest" xmlns="http://www.w3.org/2000/svg" height="24px" viewBox="0 -960 960 960" width="24px" fill="#1f1f1f"><title>${promptBuilderInterfaceText.promptBuilderJudgeBestPrompt}</title><path d="${TRACKER_ICON_PATHS.judgeBest}"/></svg> `;
              }
              promptExperimentContainerModelContainerAccordionItemButton.innerHTML += `${promptBuilderInterfaceText.promptExperimentModel} ${escapeHtml(promptExperimentRunModelKey)}`;
              // Create the accordion body container
              const promptExperimentContainerModelContainerAccordionItemBodyContainer = document.createElement('div');
              promptExperimentContainerModelContainerAccordionItemBodyContainer.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-${index}-run-nested-${promptExperimentRunModelKey}-body`;
              promptExperimentContainerModelContainerAccordionItemBodyContainer.className = 'accordion-collapse collapse';
              promptExperimentContainerModelContainerAccordionItemBodyContainer.setAttribute(
                'data-bs-parent',
                `#${paneID}-obj-${promptBuilderObject?.id}-pet-${index}-run-nested-${promptExperimentRunModelKey}`
              );
              // Create the accordion body
              const promptExperimentContainerModelContainerAccordionItemBodyContainerBody = document.createElement('div');
              promptExperimentContainerModelContainerAccordionItemBodyContainerBody.className = 'accordion-body';
              // Iterate over the model contents
              for (const promptExperimentRunModelKeyAttribute in modelData) {
                const promptExperimentRunModelKeyValue = (modelData as unknown as Record<string, unknown>)[promptExperimentRunModelKeyAttribute];
                const promptExperimentContainerModelContainerAccordionItemBodyContainerBodyLine = document.createElement('p');
                if (promptExperimentRunModelKeyAttribute === 'best_prompt') {
                  const bestPromptDiv = document.createElement('div');
                  bestPromptDiv.className = 'form-check';
                  const bestPromptCheckbox = document.createElement('input');
                  if (promptExperimentRunModelKeyValue) {
                    bestPromptCheckbox.checked = true;
                  }
                  bestPromptCheckbox.type = 'checkbox';
                  bestPromptCheckbox.id = `best-prompt-${index}-${promptExperimentRunModelKey}`;
                  bestPromptCheckbox.className = 'form-check-input';
                  bestPromptCheckbox.addEventListener('change', () => {
                    const currentHeader = document.getElementById(
                      `${paneID}-obj-${promptBuilderObject?.id}-pet-${index}-run-nested-${promptExperimentRunModelKey}-header`
                    );
                    if (currentHeader) {
                      const hasBestPrompt = currentHeader.querySelector('.bestPrompt');
                      if (bestPromptCheckbox.checked && !hasBestPrompt) {
                        currentHeader.insertAdjacentHTML(
                          'afterbegin',
                          `<svg class="bestPrompt" xmlns="http://www.w3.org/2000/svg" height="24px" viewBox="0 -960 960 960" width="24px" fill="#1f1f1f"><title>${runIsDecision ? promptBuilderInterfaceText.promptBuilderBestDecisionModel : promptBuilderInterfaceText.promptBuilderBestPrompt}</title><path d="${TRACKER_ICON_PATHS.best}"/></svg> `
                        );
                      } else if (!bestPromptCheckbox.checked && hasBestPrompt) {
                        hasBestPrompt.remove();
                      }
                    }
                    petRows.forEach((obj) => {
                      if (obj.runId === index + 1 && obj.model === promptExperimentRunModelKey) {
                        obj.best_prompt = bestPromptCheckbox.checked ? 1 : 0;
                      }
                    });
                    // Keep the tracker in sync so the selection survives a
                    // re-render (e.g. after a run was deleted)
                    modelData.best_prompt = bestPromptCheckbox.checked;
                    // A decision run has one Best Decision Model: ticking
                    // one unticks the one ticked before.
                    if (runIsDecision && bestPromptCheckbox.checked) {
                      Object.keys(promptExperimentTrackerRunResult)
                        .filter((key) => !TRACKER_META_KEYS.includes(key) && key !== promptExperimentRunModelKey)
                        .forEach((otherModel) => {
                          const otherBox = document.getElementById(`best-prompt-${index}-${otherModel}`) as HTMLInputElement | null;
                          if (otherBox?.checked) {
                            otherBox.checked = false;
                            otherBox.dispatchEvent(new Event('change'));
                          }
                        });
                    }
                    updateManifestButtonState();
                  });

                  const bestPromptLabel = document.createElement('label');
                  bestPromptLabel.className = 'form-check-label';
                  bestPromptLabel.htmlFor = `best-prompt-${index}-${promptExperimentRunModelKey}`;
                  // On a decision run the box says what it does - the Best
                  // Decision Model of the run, which Manifest takes - not that
                  // the answers matched: it is ticked on the first result of a
                  // run where they did, and a person may tick it on any result.
                  // That they matched is a note of its own, from the answers and
                  // the expected answers the run was made with.
                  bestPromptLabel.innerText = (runIsDecision
                    ? promptBuilderInterfaceText.promptExperimentModelCorrect
                    : promptBuilderInterfaceText.promptExperimentModelPromptBest) as string;
                  bestPromptDiv.appendChild(bestPromptCheckbox);
                  bestPromptDiv.appendChild(bestPromptLabel);
                  promptExperimentContainerModelContainerAccordionItemBodyContainerBodyLine.appendChild(bestPromptDiv);
                  if (runIsDecision && allExpectedMatch(runQuestions, parseAnswers(modelData.response), runExpected) === true) {
                    const matchedNote = document.createElement('span');
                    matchedNote.classList.add('pb-expected-all-matched', 'small', 'text-success');
                    matchedNote.innerText = `${promptBuilderInterfaceText.promptExperimentAllExpectedMatched}`;
                    promptExperimentContainerModelContainerAccordionItemBodyContainerBodyLine.appendChild(matchedNote);
                  }
                } else if (promptExperimentRunModelKeyAttribute === 'prompt_length') {
                  promptExperimentContainerModelContainerAccordionItemBodyContainerBodyLine.innerHTML = `<b>${promptBuilderInterfaceText.promptExperimentModelPromptLength}</b> ${escapeHtml(promptExperimentRunModelKeyValue)}`;
                } else if (promptExperimentRunModelKeyAttribute === 'output_length') {
                  if (runIsDecision) continue; // a decision model generates nothing
                  promptExperimentContainerModelContainerAccordionItemBodyContainerBodyLine.innerHTML = `<b>${promptBuilderInterfaceText.promptExperimentModelOutputLength}</b> ${escapeHtml(promptExperimentRunModelKeyValue)}`;
                } else if (promptExperimentRunModelKeyAttribute === 'run_time') {
                  promptExperimentContainerModelContainerAccordionItemBodyContainerBodyLine.innerHTML = `<b>${promptBuilderInterfaceText.promptExperimentModelRunTime}</b> ${escapeHtml(promptExperimentRunModelKeyValue)}`;
                } else if (promptExperimentRunModelKeyAttribute === 'cost') {
                  // Only shown when the model carried prices (else the line is skipped).
                  if (promptExperimentRunModelKeyValue == null) continue;
                  promptExperimentContainerModelContainerAccordionItemBodyContainerBodyLine.innerHTML = `<b>${promptBuilderInterfaceText.promptBuilderEstCostLabel}</b> ${escapeHtml(formatCost(promptExperimentRunModelKeyValue as number))}`;
                } else if (promptExperimentRunModelKeyAttribute === 'cheapest_prompt') {
                  // Rendered as the header icon, not a body line.
                  continue;
                } else if (promptExperimentRunModelKeyAttribute === 'judge_rank') {
                  // Skip when this run was never judged; the winner also gets a header icon.
                  if (promptExperimentRunModelKeyValue == null) continue;
                  // On a council tie, the tied-top models share the rank — flag
                  // them so the shared number reads as a tie, not an ordering.
                  const runJudge = promptExperimentTrackerRunResult.judge as JudgeSummary | null | undefined;
                  const tiedNote =
                    runJudge?.mode === 'council' &&
                    Array.isArray(runJudge.tiedBest) &&
                    runJudge.tiedBest.includes(promptExperimentRunModelKey)
                      ? ` ${promptBuilderInterfaceText.promptBuilderCouncilRankTied}`
                      : '';
                  promptExperimentContainerModelContainerAccordionItemBodyContainerBodyLine.innerHTML = `<b>${promptBuilderInterfaceText.promptBuilderJudgeRankLabel}</b> #${escapeHtml(promptExperimentRunModelKeyValue)}${escapeHtml(tiedNote)}`;
                } else if (promptExperimentRunModelKeyAttribute === 'judge_best') {
                  // Rendered as the header icon, not a body line.
                  continue;
                } else if (promptExperimentRunModelKeyAttribute === 'options') {
                  const optionsVal = promptExperimentRunModelKeyValue as Record<string, unknown> | null;
                  if (optionsVal?.API_KEY !== undefined) {
                    // The key entry's name, for an LLM or a decision model alike.
                    const apiKeyDefault = llmAttributesByName.get(promptExperimentRunModelKey)?.options?.API_KEY?.default;
                    (modelData as unknown as Record<string, unknown>)[promptExperimentRunModelKeyAttribute] = {
                      ...(optionsVal as Record<string, unknown>),
                      API_KEY: apiKeyDefault,
                    };
                    (optionsVal as Record<string, unknown>)['API_KEY'] = apiKeyDefault;
                  }
                  promptExperimentContainerModelContainerAccordionItemBodyContainerBodyLine.innerHTML = `<b>${promptBuilderInterfaceText.promptExperimentModelOptions}</b> ${escapeHtml(JSON.stringify(promptExperimentRunModelKeyValue))}`;
                } else if (promptExperimentRunModelKeyAttribute === 'response' && runIsDecision) {
                  // A decision run: the answer map per question, with the expected
                  // answers marked, instead of a text response.
                  const answersLabel = document.createElement('b');
                  answersLabel.innerText = promptBuilderInterfaceText.promptExperimentModelAnswers as string;
                  promptExperimentContainerModelContainerAccordionItemBodyContainerBodyLine.appendChild(answersLabel);
                  promptExperimentContainerModelContainerAccordionItemBodyContainerBodyLine.appendChild(
                    renderAnswers(runQuestions, parseAnswers(promptExperimentRunModelKeyValue), runExpected, promptBuilderInterfaceText)
                  );
                } else if (promptExperimentRunModelKeyAttribute === 'response') {
                  // Render the LLM markdown response through marked + DOMPurify so
                  // a response containing raw HTML/scripts is sanitized and cannot
                  // execute (previously handled by the <zero-md> web component).
                  const responseLabel = document.createElement('b');
                  responseLabel.innerText = promptBuilderInterfaceText.promptExperimentModelResponse as string;
                  const responseMarkdown = renderMarkdown(String(promptExperimentRunModelKeyValue ?? ''));
                  promptExperimentContainerModelContainerAccordionItemBodyContainerBodyLine.appendChild(responseLabel);
                  promptExperimentContainerModelContainerAccordionItemBodyContainerBodyLine.appendChild(document.createTextNode(' '));
                  promptExperimentContainerModelContainerAccordionItemBodyContainerBodyLine.appendChild(responseMarkdown);
                }
                promptExperimentContainerModelContainerAccordionItemBodyContainerBody.appendChild(
                  promptExperimentContainerModelContainerAccordionItemBodyContainerBodyLine
                );
              }

              promptExperimentContainerModelContainerAccordionItemHeader.appendChild(
                promptExperimentContainerModelContainerAccordionItemButton
              );
              promptExperimentContainerModelContainerAccordionItem.appendChild(
                promptExperimentContainerModelContainerAccordionItemHeader
              );
              promptExperimentContainerModelContainerAccordionItemBodyContainer.appendChild(
                promptExperimentContainerModelContainerAccordionItemBodyContainerBody
              );
              promptExperimentContainerModelContainerAccordionItem.appendChild(
                promptExperimentContainerModelContainerAccordionItemBodyContainer
              );
              promptExperimentContainerModelContainerAccordion.appendChild(
                promptExperimentContainerModelContainerAccordionItem
              );
              promptExperimentContainerModelContainer.appendChild(
                promptExperimentContainerModelContainerAccordion
              );
            }
          }
          // Add the model tracker
          (promptExperimentRunContainer.lastChild as HTMLElement)!.lastChild!.lastChild!.appendChild(
            promptExperimentContainerModelContainer
          );
          // Add the finished run tracker
          const prommpExperimentTargetContainer = document.getElementById(
            `${paneID}-obj-${promptBuilderObject?.id}-pet`
          );
          if (prommpExperimentTargetContainer) {
            prommpExperimentTargetContainer.prepend(promptExperimentRunContainer);
          }
          // Reset the prompts for the next loop
          systemPrompt = '';
          userPrompt = '';
          // Increment the run tracker
          promptExperimentTrackerRunID++;
        }
      });
      petRows = promptExperimentTransformData(promptExperimentTracker);
      updateTrackerEmptyState();
      updateManifestButtonState();
    }

    // Delete one experiment run and renumber the remaining ones. The runId is
    // positional (index + 1), so re-rendering from the spliced tracker keeps
    // the headers, checkbox wiring and the persisted rows contiguous at 1..N.
    function deleteExperimentRun(index: number): void {
      if (experimentRunning) return;
      promptExperimentTracker.splice(index, 1);
      experimentsModified = true;
      renderAllExperimentRuns();
    }

    function renderAllExperimentRuns(preserveOpen = false): void {
      const prommpExperimentTargetContainer = document.getElementById(`${paneID}-obj-${promptBuilderObject?.id}-pet`);
      // Snapshot which accordions (run-level and nested model bodies) are open
      // so a re-render can restore them — Bootstrap collapse state lives in the
      // DOM classes, which innerHTML='' would otherwise wipe.
      const openBodyIds =
        preserveOpen && prommpExperimentTargetContainer
          ? Array.from(prommpExperimentTargetContainer.querySelectorAll('.accordion-collapse.show')).map((el) => el.id)
          : [];
      if (prommpExperimentTargetContainer) prommpExperimentTargetContainer.innerHTML = '';
      // createPromptExperimentTracker only renders the entry whose index equals
      // the run counter and then increments it, so start from 0 to render all
      promptExperimentTrackerRunID = 0;
      createPromptExperimentTracker(promptExperimentTracker);
      if (openBodyIds.length > 0 && prommpExperimentTargetContainer) {
        const triggers = Array.from(prommpExperimentTargetContainer.querySelectorAll('[data-bs-target]'));
        openBodyIds.forEach((bodyId) => {
          const body = document.getElementById(bodyId);
          if (body) body.classList.add('show');
          const trigger = triggers.find((el) => el.getAttribute('data-bs-target') === `#${bodyId}`);
          if (trigger) {
            trigger.classList.remove('collapsed');
            trigger.setAttribute('aria-expanded', 'true');
          }
        });
      }
    }

    // Resolve the SCR options for the judge call: its API key (when the judge
    // model requires one) and temperature 0 for reproducibility when supported.
    function resolveJudgeOptions(judgeMeta?: AvailableLLM): Record<string, unknown> {
      const judgeOptions: Record<string, unknown> = {};
      const options = judgeMeta?.options;
      if (!options) return judgeOptions;
      if (options.API_KEY) {
        const apiKeys = promptBuilderObject?.API_KEYS as Record<string, string> | undefined;
        judgeOptions.API_KEY = apiKeys?.[options.API_KEY.default as string] ?? '';
      }
      if (options.temperature) judgeOptions.temperature = 0;
      return judgeOptions;
    }

    // Write a verdict ranking (model names, best first) onto the run's per-model
    // judge_rank/judge_best. On a tie there is no single best.
    function applyRanksToRun(trackerEntry: ExperimentTrackerEntry, ranking: string[], tie: boolean): void {
      Object.keys(trackerEntry)
        .filter((key) => !TRACKER_META_KEYS.includes(key))
        .forEach((modelName) => {
          const modelData = trackerEntry[modelName] as ModelExperimentData;
          if (modelData) {
            modelData.judge_rank = null;
            modelData.judge_best = null;
          }
        });
      ranking.forEach((modelName, rankIndex) => {
        const modelData = trackerEntry[modelName] as ModelExperimentData;
        if (modelData) {
          modelData.judge_rank = rankIndex + 1;
          modelData.judge_best = !tie && rankIndex === 0;
        }
      });
    }

    // Council ranks come from the Borda scores (competition ranking): equal
    // scores share a rank, so a top tie shows as 1, 1, 3, … The winner icon is
    // set only when there is a single winner (no tie).
    function applyCouncilRanks(
      trackerEntry: ExperimentTrackerEntry,
      ranks: Record<string, number>,
      best: string | null
    ): void {
      Object.keys(trackerEntry)
        .filter((key) => !TRACKER_META_KEYS.includes(key))
        .forEach((modelName) => {
          const modelData = trackerEntry[modelName] as ModelExperimentData;
          if (modelData) {
            // Its own field only: a model the council did not rank (no
            // answer) named like a field every object inherits has no rank.
            modelData.judge_rank = Object.prototype.hasOwnProperty.call(ranks, modelName) ? ranks[modelName] : null;
            modelData.judge_best = best != null && modelName === best;
          }
        });
    }

    // Judge a run (LLM-as-a-Judge). One judge on the panel = single-judge mode
    // (Phase 1); two or more = a council whose ballots are aggregated (Borda).
    // Writes per-model ranks + a run-level summary and re-renders. The judge is
    // advisory: it never changes the Best Response. `auto` suppresses the
    // precondition toasts (used by auto-judge-on-finish).
    async function promptBuilderJudgeRun(index: number, auto = false): Promise<void> {
      if (experimentRunning) return;
      const trackerEntry = promptExperimentTracker[index];
      if (!trackerEntry) return;
      const panel = getSelectedJudgeModels();
      if (panel.length === 0) {
        if (!auto) showToast(`${promptBuilderInterfaceText?.promptBuilderJudgeSelectModelToast}`);
        return;
      }
      const includeSelf = promptBuilderJudgeIncludeSelf.checked;
      const autoJudge = promptBuilderJudgeAuto.checked;
      const modelKeys = Object.keys(trackerEntry).filter((key) => !TRACKER_META_KEYS.includes(key));
      const failedText = promptBuilderInterfaceText?.promptBuilderModelInferenceFailed as string;
      const candidates = modelKeys
        .map((modelName) => ({
          modelName,
          response: String((trackerEntry[modelName] as ModelExperimentData)?.response ?? ''),
        }))
        .filter((candidate) => candidate.response !== '' && candidate.response !== failedText);
      const candidateModels = candidates.map((candidate) => candidate.modelName);
      if (candidates.length < 2) {
        if (!auto) showToast(`${promptBuilderInterfaceText?.promptBuilderJudgeNeedsTwoResponses}`);
        return;
      }
      // Single judge that would be self-excluded down to one candidate: bail early.
      if (
        panel.length === 1 &&
        candidates.filter((candidate) => includeSelf || candidate.modelName !== panel[0]).length < 2
      ) {
        if (!auto) showToast(`${promptBuilderInterfaceText?.promptBuilderJudgeNotEnoughCandidates}`);
        return;
      }

      // In-flight state on this run's Judge button.
      const judgeButton = document.getElementById(
        `${paneID}-obj-${promptBuilderObject?.id}-pet-${index}-judge`
      ) as HTMLButtonElement | null;
      judgingRuns.add(trackerEntry);
      if (judgeButton) {
        judgeButton.disabled = true;
        judgeButton.innerHTML = JUDGE_BUTTON_SPINNER;
      }

      const runVariables = Array.isArray(trackerEntry.variables) ? trackerEntry.variables : [];
      const resolvedSystem = substitutePromptVariables(trackerEntry.systemPrompt, runVariables);
      const resolvedUser = substitutePromptVariables(trackerEntry.userPrompt, runVariables);
      const runOneJudge = (judgeModel: string) =>
        judgeRun({
          scrEndpoint: promptBuilderObject.SCREndpoint as string,
          deploymentType: (promptBuilderObject.deploymentType as string) ?? 'k8s',
          judgeModel,
          judgeOptions: resolveJudgeOptions(promptBuilderAvailableLLMs.find((llm) => llm.name === judgeModel)),
          systemPrompt: resolvedSystem,
          userPrompt: resolvedUser,
          candidates,
          includeSelf,
        });
      const now = new Date().toISOString();
      // Load the (cached) cost attributes of the judge models so judging cost can
      // be estimated. Guarded internally — never blocks judging.
      await Promise.all(panel.map(ensureLLMCostAttributes));
      // Estimated cost of one judge/chairman call from its SCR usage + the
      // judge model's prices. null when the model carries no prices.
      const judgeCallCost = (model: string, usage?: JudgeUsage): number | null =>
        usage ? computeCallCost(usage, llmAttributesByName.get(model)) : null;
      // Sum a set of call costs, returning null only when none were priced.
      const sumCosts = (costs: (number | null)[]): number | null => {
        const priced = costs.filter((c): c is number => c !== null);
        return priced.length > 0 ? priced.reduce((a, b) => a + b, 0) : null;
      };

      if (panel.length === 1) {
        // ---- Single judge (Phase 1) ----
        const judgeModel = panel[0];
        const verdict = await runOneJudge(judgeModel);
        const judgeSelfPresent = candidateModels.includes(judgeModel);
        if (verdict.status === 'ok' && verdict.ranking && verdict.best) {
          applyRanksToRun(trackerEntry, verdict.ranking, false);
          trackerEntry.judge = {
            judgeModel,
            status: 'ok',
            mode: 'single',
            best: verdict.best,
            ranking: verdict.ranking,
            confidence: verdict.confidence ?? 'unknown',
            reasoning: verdict.reasoning ?? '',
            excludedSelf: !includeSelf && judgeSelfPresent,
            includedSelf: includeSelf && judgeSelfPresent,
            includeSelf,
            autoJudge,
            judgeCost: judgeCallCost(judgeModel, verdict.usage),
            ranAt: now,
          };
        } else {
          trackerEntry.judge = {
            judgeModel,
            status: verdict.status === 'unparseable' ? 'unparseable' : 'error',
            mode: 'single',
            error:
              verdict.error === 'not-enough-candidates'
                ? `${promptBuilderInterfaceText?.promptBuilderJudgeNotEnoughCandidates}`
                : verdict.error ?? null,
            raw: verdict.raw ?? null,
            includeSelf,
            autoJudge,
            judgeCost: judgeCallCost(judgeModel, verdict.usage),
            ranAt: now,
          };
        }
      } else {
        // ---- Council: run every judge in parallel, then aggregate ----
        const verdicts = await Promise.all(panel.map((judgeModel) => runOneJudge(judgeModel)));
        const ballots: JudgeBallot[] = verdicts.map((verdict, ballotIndex) => ({
          judgeModel: panel[ballotIndex],
          status: verdict.status,
          ranking: verdict.ranking,
          confidence: verdict.confidence,
          reasoning: verdict.reasoning,
          excludedSelf: !includeSelf && candidateModels.includes(panel[ballotIndex]),
          error: verdict.error,
          usage: verdict.usage,
        }));
        // Estimated cost of the panel's calls (each judge that reached its model).
        const panelCosts = ballots.map((ballot) => judgeCallCost(ballot.judgeModel, ballot.usage));
        const okBallots = ballots.filter(
          (ballot) => ballot.status === 'ok' && Array.isArray(ballot.ranking) && ballot.ranking.length > 0
        );
        if (okBallots.length < 2) {
          // Not enough ballots to be a council.
          applyRanksToRun(trackerEntry, [], false);
          trackerEntry.judge = {
            judgeModel: '',
            status: 'error',
            mode: 'council',
            panel,
            ballots,
            error: `${promptBuilderInterfaceText?.promptBuilderCouncilDegraded}`,
            includeSelf,
            autoJudge,
            judgeCost: sumCosts(panelCosts),
            ranAt: now,
          };
        } else {
          const result = aggregateBallots(candidateModels, okBallots);
          let councilBest = result.best;
          let councilTie = result.tie;
          let chairman: { model: string; reasoning?: string | null } | null = null;
          let chairmanCost: number | null = null;
          // Chairman tiebreaker — only when the panel tied and the user opted in.
          if (
            result.tie &&
            promptBuilderJudgeChairmanToggle.checked &&
            promptBuilderJudgeChairmanModel.value &&
            result.tiedBest.length >= 2
          ) {
            const chairmanModel = promptBuilderJudgeChairmanModel.value;
            await ensureLLMCostAttributes(chairmanModel);
            const chairmanVerdict = await chairmanBreakTie({
              scrEndpoint: promptBuilderObject.SCREndpoint as string,
              deploymentType: (promptBuilderObject.deploymentType as string) ?? 'k8s',
              chairmanModel,
              chairmanOptions: resolveJudgeOptions(
                promptBuilderAvailableLLMs.find((llm) => llm.name === chairmanModel)
              ),
              systemPrompt: resolvedSystem,
              userPrompt: resolvedUser,
              tiedCandidates: candidates.filter((candidate) => result.tiedBest.includes(candidate.modelName)),
              panelNotes: okBallots.map((ballot) => ballot.reasoning ?? '').filter(Boolean),
            });
            chairmanCost = judgeCallCost(chairmanModel, chairmanVerdict.usage);
            if (
              chairmanVerdict.status === 'ok' &&
              chairmanVerdict.best &&
              result.tiedBest.includes(chairmanVerdict.best)
            ) {
              councilBest = chairmanVerdict.best;
              councilTie = false;
              chairman = { model: chairmanModel, reasoning: chairmanVerdict.reasoning ?? '' };
            }
          }
          applyCouncilRanks(trackerEntry, result.ranks, councilBest);
          trackerEntry.judge = {
            judgeModel: '',
            status: 'ok',
            mode: 'council',
            panel,
            ballots,
            method: 'borda',
            best: councilBest,
            ranking: result.ranking,
            confidence: result.confidence,
            agreement: result.agreement,
            tie: councilTie,
            tiedBest: result.tiedBest,
            chairman,
            includeSelf,
            autoJudge,
            judgeCost: sumCosts([...panelCosts, chairmanCost]),
            ranAt: now,
          };
        }
      }
      judgingRuns.delete(trackerEntry);
      // Preserve which run/model accordions are open so a judged run the user
      // had expanded doesn't collapse under them on the re-render.
      renderAllExperimentRuns(true);
    }

    // Restore an experiment run into the workbench: prompts, variables, LLM
    // selection and each selected LLM's option values. LLMs of the run that
    // are no longer available are reported in a toast.
    function loadExperimentRun(index: number): void {
      const trackerEntry = promptExperimentTracker[index];
      if (!trackerEntry) return;
      const systemPromptInput = document.getElementById(
        `${paneID}-obj-${promptBuilderObject?.id}-system-prompt`
      ) as HTMLTextAreaElement | null;
      const userPromptInput = document.getElementById(
        `${paneID}-obj-${promptBuilderObject?.id}-user-prompt`
      ) as HTMLTextAreaElement | null;
      const runMode = trackerEntry.mode === 'decision' ? 'decision' : 'llm';
      // A decision run cannot be shown with decision templates switched off:
      // the page stays what it is rather than filling LLM editors with it.
      if (runMode === 'decision' && !decisionEnabled) return;
      if (runMode !== promptMode) applyPromptMode(runMode);
      if (runMode === 'decision') {
        decisionDesigner.stateInput.value = trackerEntry.userPrompt ?? '';
        decisionDesigner.setQuestions(questionsFromJson(trackerEntry.systemPrompt ?? '', trackerEntry.expected ?? {}));
      } else {
        if (systemPromptInput) systemPromptInput.value = trackerEntry.systemPrompt ?? '';
        if (userPromptInput) userPromptInput.value = trackerEntry.userPrompt ?? '';
      }
      setPromptVariables(Array.isArray(trackerEntry.variables) ? trackerEntry.variables : []);
      applyManifestConfig(trackerEntry.manifest);
      // Restore the judge configuration this run was judged with, if any: the
      // judge panel (models still available), the include-self and auto-judge
      // toggles. A run that was never judged leaves the controls untouched.
      if (trackerEntry.judge) {
        const judgeConfig = trackerEntry.judge;
        // The panel is the council's `panel`, or the single judge's model.
        const runPanel = Array.isArray(judgeConfig.panel) && judgeConfig.panel.length > 0
          ? judgeConfig.panel
          : judgeConfig.judgeModel
            ? [judgeConfig.judgeModel]
            : [];
        const availableRunPanel = runPanel.filter((name) =>
          promptBuilderAvailableLLMs.some((llm) => llm.name === name && !llmNotUsableNote(llm))
        );
        if (availableRunPanel.length > 0) {
          // Single judge → the dropdown; a panel → the council toggle + list.
          promptBuilderJudgeModelSelect.value = availableRunPanel[0];
          const isCouncil = availableRunPanel.length >= 2;
          promptBuilderJudgeCouncilToggle.checked = isCouncil;
          promptBuilderAvailableLLMs.forEach((availableLLM, llmIndex) => {
            const panelCheckbox = document.getElementById(
              `${paneID}-obj-${promptBuilderObject?.id}-judge-panel-${llmIndex}`
            ) as HTMLInputElement | null;
            if (panelCheckbox) panelCheckbox.checked = availableRunPanel.includes(availableLLM.name);
          });
          updateJudgeControlsVisibility();
        }
        promptBuilderJudgeIncludeSelf.checked = Boolean(judgeConfig.includeSelf);
        promptBuilderJudgeAuto.checked = Boolean(judgeConfig.autoJudge);
        // Restore the chairman tiebreaker when the run recorded one.
        const chairmanModel = judgeConfig.chairman?.model ?? '';
        const chairmanAvailable = Boolean(
          chairmanModel && promptBuilderAvailableLLMs.some((llm) => llm.name === chairmanModel && !llmNotUsableNote(llm))
        );
        promptBuilderJudgeChairmanToggle.checked = chairmanAvailable;
        if (chairmanAvailable) promptBuilderJudgeChairmanModel.value = chairmanModel;
        judgeChairmanModelRow.classList.toggle('d-none', !chairmanAvailable);
      }
      // Reselect the run's LLMs and restore their option values
      const runModels = Object.keys(trackerEntry).filter((key) => !TRACKER_META_KEYS.includes(key));
      promptBuilderAvailableLLMs.forEach((availableLLM, llmIndex) => {
        const llmCheckbox = document.getElementById(`model${llmIndex}`) as HTMLInputElement | null;
        // A model that cannot be used is not ticked for a run either.
        if (!llmCheckbox || llmCheckbox.disabled) return;
        const selected = runModels.includes(availableLLM.name);
        if (llmCheckbox.checked !== selected) {
          llmCheckbox.checked = selected;
          // Fires the listener that shows/hides the option inputs
          llmCheckbox.dispatchEvent(new Event('change'));
        }
        if (selected) {
          const modelData = trackerEntry[availableLLM.name] as ModelExperimentData;
          Object.entries(modelData?.options ?? {}).forEach(([optionKey, optionValue]) => {
            if (optionKey === 'API_KEY') return;
            const optionInput = document.getElementById(
              optionControlId(llmIndex, optionKey, availableLLM.options?.[optionKey])
            ) as
              | HTMLInputElement
              | HTMLSelectElement
              | null;
            if (!optionInput) return;
            if (optionInput instanceof HTMLInputElement && optionInput.type === 'checkbox') {
              optionInput.checked = optionValue === true || optionValue === 'true';
            } else {
              optionInput.value = String(optionValue);
              if (optionInput instanceof HTMLInputElement) syncSegmentedControl(optionInput);
            }
          });
        }
      });
      promptBuilderAvailableDecisionModels.forEach((model, index) => {
        const checkbox = document.getElementById(`decision-model${index}`) as HTMLInputElement | null;
        if (checkbox && !checkbox.disabled) checkbox.checked = runModels.includes(model.name);
      });
      updateRunExperimentsButtonState();
      const missingLLMs = runModels.filter(
        (modelName) =>
          !promptBuilderAvailableLLMs.some((availableLLM) => availableLLM.name === modelName) &&
          !promptBuilderAvailableDecisionModels.some((model) => model.name === modelName)
      );
      if (missingLLMs.length > 0) {
        showToast(`${promptBuilderInterfaceText?.promptBuilderLoadMissingLLMs} ${missingLLMs.join(', ')}`);
      }
    }

    // Load the most recent run that has a best response selected. Runs
    // automatically after a prompt's tracker is loaded, so it stays silent
    // when no best response has been selected yet. `ontoFreshSteps` starts
    // the later steps afresh before the run is shown, so that a prompt-test
    // with a run shows that run and the defaults, never the run mixed with
    // what the steps held; with no such run the steps are left as they are.
    function loadMostRecentBestRun(ontoFreshSteps = false): void {
      for (let index = promptExperimentTracker.length - 1; index >= 0; index--) {
        const trackerEntry = promptExperimentTracker[index];
        const hasBestPrompt = Object.keys(trackerEntry).some(
          (key) =>
            !TRACKER_META_KEYS.includes(key) && (trackerEntry[key] as ModelExperimentData)?.best_prompt
        );
        if (hasBestPrompt) {
          if (ontoFreshSteps) {
            startLaterStepsAfresh();
            laterStepsShowPromptTest = true;
          }
          loadExperimentRun(index);
          return;
        }
      }
    }

    // Transform the data structure to be saved in SAS Model Manager
    function promptExperimentTransformData(inputArray: ExperimentTrackerEntry[]): PETRow[] {
      return inputArray
        .map((entry, index) => {
          const MODELKEYS = Object.keys(entry).filter(
            (key) => !TRACKER_META_KEYS.includes(key)
          );
          const responseForModel: PETRow[] = [];
          MODELKEYS.forEach((MODELKEY, MODELINDEX) => {
            if (MODELINDEX === 0) {
              responseForModel.push({
                runId: index + 1,
                systemPrompt: entry.systemPrompt,
                userPrompt: entry.userPrompt,
                variables: Array.isArray(entry.variables) ? entry.variables : null,
                manifest: entry.manifest ?? null,
                mode: entry.mode ?? null,
                expected: entry.expected ?? null,
                model: '',
                options: '',
                response: '',
                run_time: null,
                prompt_length: null,
                output_length: null,
                best_prompt: null,
                fastest_prompt: null,
                fewest_tokens_prompt: null,
                judge_rank: null,
                judge_best: null,
                // Only a completed ('ok') judgment is persisted. The reasoning
                // and the judge config (model, include-self, auto-judge) are
                // stored so loading the run restores the whole judge context.
                judge_model: entry.judge?.status === 'ok' ? entry.judge.judgeModel : null,
                judge_confidence: entry.judge?.status === 'ok' ? (entry.judge.confidence ?? null) : null,
                judge_reasoning: entry.judge?.status === 'ok' ? (entry.judge.reasoning ?? null) : null,
                judge_include_self:
                  entry.judge?.status === 'ok' ? (entry.judge.includeSelf ? 1 : 0) : null,
                judge_auto: entry.judge?.status === 'ok' ? (entry.judge.autoJudge ? 1 : 0) : null,
                judge_cost: entry.judge?.status === 'ok' ? (entry.judge.judgeCost ?? null) : null,
                judge_mode: entry.judge?.status === 'ok' ? (entry.judge.mode ?? 'single') : null,
                judge_panel:
                  entry.judge?.status === 'ok' && entry.judge.mode === 'council'
                    ? (entry.judge.panel ?? []).join(', ')
                    : null,
                judge_agreement:
                  entry.judge?.status === 'ok' && entry.judge.mode === 'council' && entry.judge.agreement
                    ? `${entry.judge.agreement.firstChoiceForWinner}/${entry.judge.agreement.total}`
                    : null,
                judge_ballots:
                  entry.judge?.status === 'ok' && entry.judge.mode === 'council'
                    ? (entry.judge.ballots ?? null)
                    : null,
                judge_chairman_model:
                  entry.judge?.status === 'ok' && entry.judge.mode === 'council'
                    ? (entry.judge.chairman?.model ?? null)
                    : null,
                judge_chairman_reasoning:
                  entry.judge?.status === 'ok' && entry.judge.mode === 'council'
                    ? (entry.judge.chairman?.reasoning ?? null)
                    : null,
              });
            }

            const modelEntry = entry[MODELKEY] as ModelExperimentData;
            responseForModel.push({
              runId: index + 1,
              systemPrompt: '',
              userPrompt: '',
              model: MODELKEY,
              options: JSON.stringify(modelEntry.options).replace(/"/g, ''),
              response: modelEntry.response,
              run_time: modelEntry.run_time,
              prompt_length: modelEntry.prompt_length,
              output_length: modelEntry.output_length,
              // Coerce the flags to numeric 1/0/null so the SAS reporting layer
              // (numeric columns) reads them consistently whether they were set
              // by a checkbox (number) or programmatically (boolean).
              best_prompt: modelEntry.best_prompt == null ? null : (modelEntry.best_prompt ? 1 : 0),
              fastest_prompt: modelEntry?.fastest_prompt,
              fewest_tokens_prompt: modelEntry?.fewest_tokens_prompt,
              cheapest_prompt: modelEntry?.cheapest_prompt ?? null,
              cost: modelEntry?.cost ?? null,
              judge_rank: modelEntry?.judge_rank ?? null,
              judge_best: modelEntry?.judge_best == null ? null : (modelEntry.judge_best ? 1 : 0),
            });
          });

          return responseForModel;
        })
        .flat();
    }

    // Save the prompt run to the prompt
    const promptExperimentSaveButton = document.createElement('button');
    promptExperimentSaveButton.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-save-button`;
    promptExperimentSaveButton.innerText = `${promptBuilderInterfaceText?.promptBuilderSaveExperimentsButton}`;
    promptExperimentSaveButton.setAttribute('type', 'button');
    promptExperimentSaveButton.setAttribute('class', 'btn btn-outline-secondary');
    promptExperimentSaveButton.onclick = async function () {
      await selectionLock.during(() => promptBuilderSaveExperiments());
    };

    // Save the prompt run and turn the best prompt into a model. On a
    // decision template the box a person ticks is the Best Decision Model,
    // and the manifest texts name it so.
    const manifestText = (llm: string, decision: string): string =>
      `${promptBuilderInterfaceText?.[promptMode === 'decision' ? decision : llm]}`;
    const manifestButtonText = (): string => manifestText('promptBuilderCreateModelButton', 'promptBuilderCreateDecisionModelButton');
    const promptExperimentCreateModelButton = document.createElement('button');
    promptExperimentCreateModelButton.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-create-model-button`;
    promptExperimentCreateModelButton.innerText = manifestButtonText();
    promptExperimentCreateModelButton.setAttribute('type', 'button');
    promptExperimentCreateModelButton.setAttribute('class', 'btn btn-primary');
    // One manifest per click. The button used to be disabled inside the
    // manifest function only, after the save had been waited for: a double
    // click ran two manifests at once, and the failure line of one could
    // describe the other. The click itself now takes the button, and gives it
    // back when the run has ended - saved or not, manifested or not.
    let manifestClickRunning = false;
    // The save and the manifest are one write: locked from the click to the end.
    promptExperimentCreateModelButton.onclick = () => selectionLock.during(async () => {
      if (manifestClickRunning) return;
      manifestClickRunning = true;
      promptExperimentCreateModelButton.disabled = true;
      promptExperimentCreateModelButton.innerHTML = `<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> ${promptBuilderInterfaceText.promptBuilderSaveExperimentsButtonStatus}`;
      try {
        // Persist the manifest configuration with the manifested run so loading
        // the run later restores it. The save link goes to the manifest box here.
        stampManifestConfigOnBestRun();
        const saved = await promptBuilderSaveExperiments(promptExperimentResultContainer);
        if (!saved) {
          // The save has said in the box what happened to it; the manifest
          // does not go on over experiments that are not saved.
          const line = document.createElement('p');
          line.classList.add('text-danger', 'pet-manifest-failed', 'pet-manifest-not-saved');
          line.innerText = `${promptBuilderInterfaceText?.promptBuilderManifestSaveFailed}`;
          promptExperimentResultContainer.appendChild(line);
          return;
        }
        await runManifest(promptMode === 'decision' ? promptBuilderCreateDecisionModel : promptBulderCreateBestPromptModel);
      } finally {
        manifestClickRunning = false;
        promptExperimentCreateModelButton.innerText = manifestButtonText();
        // Usable again where there is a best response to manifest.
        updateManifestButtonState();
      }
    });
    // Wrapped so the "select a best response first" hint shows on hover even
    // while the button is disabled (a disabled button fires no hover events).
    const promptExperimentCreateModelButtonWrapper = wrapForHint(promptExperimentCreateModelButton);
    // Disabled (with a hint) until a run has a best response selected
    function updateManifestButtonState(): void {
      const hasBestPrompt = promptExperimentTracker.some((trackerEntry) =>
        Object.keys(trackerEntry).some(
          (key) => !TRACKER_META_KEYS.includes(key) && (trackerEntry[key] as ModelExperimentData)?.best_prompt
        )
      );
      // Off as well while a write of the prompt-test is out (see
      // createSelectionLock); the hint is about the best response only.
      setDisabledHint(
        promptExperimentCreateModelButton,
        promptExperimentCreateModelButtonWrapper,
        !hasBestPrompt || selectionLock.busy,
        hasBestPrompt ? '' : manifestText('promptBuilderCreateModelNoBestPrompt', 'promptBuilderCreateModelNoBestDecisionModel')
      );
      // The optimize panel gates on the same tracker state (its dataset is the
      // runs with a Best Response), so refresh it wherever the manifest button
      // refreshes. No-op until the optimize section exists / when disabled.
      updateOptimizeState();
      updateTrackerSummary();
    }
    updateManifestButtonState();
    // The buttons that write the open prompt-test outside the Setup card -
    // Save Experiments, Manifest, Run optimization, Compare targets and the
    // delete of an optimization run - are off while any write is out, their
    // own included: a save started between two requests of a manifest wrote
    // the tracker beside it.
    selectionLock.onChange(() => {
      promptExperimentSaveButton.disabled = selectionLock.busy;
      updateManifestButtonState();
      optimizeUI?.historyContainer
        .querySelectorAll<HTMLButtonElement>('.pb-optimize-history-delete')
        .forEach((button) => {
          button.disabled = selectionLock.busy;
        });
    });
    // Manifest section: configure how the best prompt becomes a model, with
    // the action button below the configuration.
    const promptExperimentManifestHeader = document.createElement('h2');
    promptExperimentManifestHeader.innerText = `${promptBuilderInterfaceText?.promptBuilderManifestHeading}`;
    const promptExperimentManifestDescription = document.createElement('p');
    promptExperimentManifestDescription.innerText = `${promptBuilderInterfaceText?.promptBuilderManifestDescription}`;

    // Choose whether the manifested model performs the LLM call itself
    // (returning the same outputs as the LLM models) or returns the
    // llmBody/llmURL pair for the Call LLM node in SAS Intelligent Decisioning.
    const promptExperimentIntegratedCallDiv = document.createElement('div');
    promptExperimentIntegratedCallDiv.classList.add('form-check', 'pet-manifest-integrated');
    promptExperimentIntegratedCallDiv.title = `${promptBuilderInterfaceText?.promptBuilderManifestIntegratedInfo}`;
    const promptExperimentIntegratedCallCheckbox = document.createElement('input');
    promptExperimentIntegratedCallCheckbox.type = 'checkbox';
    promptExperimentIntegratedCallCheckbox.classList.add('form-check-input');
    promptExperimentIntegratedCallCheckbox.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-manifest-integrated`;
    const promptExperimentIntegratedCallLabel = document.createElement('label');
    promptExperimentIntegratedCallLabel.classList.add('form-check-label');
    promptExperimentIntegratedCallLabel.htmlFor = promptExperimentIntegratedCallCheckbox.id;
    promptExperimentIntegratedCallLabel.innerText = `${promptBuilderInterfaceText?.promptBuilderManifestIntegratedLabel}`;
    promptExperimentIntegratedCallDiv.appendChild(promptExperimentIntegratedCallCheckbox);
    promptExperimentIntegratedCallDiv.appendChild(promptExperimentIntegratedCallLabel);

    // Options of the integrated LLM call, revealed only when the checkbox is
    // ticked: which default outputs to keep, and which output variables to
    // parse from the LLM's JSON response.
    const promptExperimentManifestOptions = document.createElement('div');
    promptExperimentManifestOptions.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-manifest-options`;
    promptExperimentManifestOptions.classList.add('pet-manifest-options');
    promptExperimentManifestOptions.style.display = 'none';
    promptExperimentIntegratedCallCheckbox.addEventListener('change', () => {
      promptExperimentManifestOptions.style.display = promptExperimentIntegratedCallCheckbox.checked ? '' : 'none';
      updateTlsWarning();
    });
    // With the option "Verify TLS certificates in manifested models" unticked
    // the manifested model calls its container without verifying the
    // certificate. That is said above the button, before the click, and only
    // for a manifest whose score code makes the call: a decision template
    // always does, a prompt template when the LLM call is included (the Call
    // LLM node variant leaves the call to the DS2 node).
    const promptExperimentTlsWarning = document.createElement('div');
    promptExperimentTlsWarning.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-manifest-tls-warning`;
    promptExperimentTlsWarning.classList.add('alert', 'alert-warning', 'py-2', 'px-3', 'mt-3', 'mb-0', 'd-none');
    promptExperimentTlsWarning.setAttribute('role', 'note');
    promptExperimentTlsWarning.innerText = `${promptBuilderInterfaceText?.promptBuilderManifestTlsOffWarning}`;
    function manifestMakesContainerCall(): boolean {
      return promptMode === 'decision' || promptExperimentIntegratedCallCheckbox.checked;
    }
    function updateTlsWarning(): void {
      promptExperimentTlsWarning.classList.toggle('d-none', tlsVerificationOn || !manifestMakesContainerCall());
    }
    /**
     * The other tags of a manifest are a convenience; this one is how a model
     * that does not verify TLS is found, so it has to say what the stored
     * score code does. When the tags could not be updated the manifest box
     * says so, in both directions: the tag was to be written (`removal`
     * false) and has to be added by hand, or it was to be taken away and a
     * model that verifies may still carry it.
     */
    function reportTlsTagFailure(removal: boolean): void {
      const line = document.createElement('p');
      line.classList.add('text-danger', 'pet-manifest-tls-tag-failed');
      line.innerText = removal
        ? `${promptBuilderInterfaceText?.promptBuilderManifestTlsTagNotRemoved}`
        : `${promptBuilderInterfaceText?.promptBuilderManifestTlsTagFailed}`;
      promptExperimentResultContainer.appendChild(line);
    }
    /**
     * Store the score code of a manifest; false when the manifest has to stop
     * here. The upload answers with a status instead of throwing, and a
     * manifest that went on regardless set or removed TLS-Verification-Off
     * for a score code the model does not hold, under a toast that said
     * "manifested". A manifest whose score code was not stored has failed:
     * the box says so, and the caller stops before the tags and the toast.
     *
     * Three things are told apart. A refusal SAS Model Manager answered with
     * (a status of 300 or more, with its JSON body) is certain: the model
     * keeps the score code it had. An upload that gets no usable answer - the
     * connection dropped, or an ingress answered with its error page in place
     * of the service - is read back (see createModelContent): the score code
     * the model holds then is this one, and the manifest goes on, or it is
     * not, and the model keeps the one it had. When the read back fails too,
     * the service may have stored the code, and the page cannot know: the
     * line says exactly that, and that the tags were not updated.
     */
    async function storeManifestScoreCode(modelID: string, scoreCode: Blob, fileName: string): Promise<boolean> {
      let outcome: 'refused' | 'not-stored' | 'unknown' = 'refused';
      try {
        const stored = await createModelContent(modelID, scoreCode, fileName, 'score', 'text/x-python');
        if (stored.status_code < 300) {
          manifestScoreCodeStored = true;
          return true;
        }
        console.error(`The score code of the manifest was not stored (status ${stored.status_code}).`, stored.response);
      } catch (error) {
        outcome = error instanceof UnansweredWrite && error.done === false ? 'not-stored' : 'unknown';
        console.error('The upload of the score code got no usable answer.', error);
      }
      const line = document.createElement('p');
      line.classList.add('text-danger', 'pet-manifest-failed');
      if (outcome === 'unknown') line.classList.add('pet-manifest-unknown');
      if (outcome === 'not-stored') line.classList.add('pet-manifest-stopped');
      line.innerText =
        outcome === 'refused'
          ? `${promptBuilderInterfaceText?.promptBuilderManifestScoreCodeFailed}`
          : outcome === 'not-stored'
            ? `${promptBuilderInterfaceText?.promptBuilderManifestScoreCodeNoAnswer}`
            : `${promptBuilderInterfaceText?.promptBuilderManifestScoreCodeUnknown}`;
      promptExperimentResultContainer.appendChild(line);
      return false;
    }
    /**
     * Whether the manifest that is running has stored its score code: what
     * has to be said when a later request fails depends on it.
     */
    let manifestScoreCodeStored = false;
    /**
     * Run a manifest so that it ends whatever happens in it. A request that
     * throws - the network is gone, or the answer is no JSON, such as the
     * error page of an ingress - used to end the function where it stood:
     * the button stayed disabled on "Saving..." for good and nothing was
     * said. Now the box beside the button says that the manifest stopped,
     * and whether the score code of this run is stored, no toast says
     * "manifested", and the button is usable again. (The upload of the score
     * code itself is caught where it is made, see storeManifestScoreCode:
     * when that request throws, whether the code is stored is not known.)
     *
     * The tag TLS-Verification-Off is not this function's business: both
     * manifests update the tags directly after the score code is stored, so
     * the tag says what the stored code does wherever the manifest stops
     * after that.
     */
    async function runManifest(manifest: () => Promise<void>): Promise<void> {
      manifestScoreCodeStored = false;
      try {
        await manifest();
      } catch (error) {
        console.error('The manifest stopped at a request that failed.', error);
        const line = document.createElement('p');
        line.classList.add(
          'text-danger',
          'pet-manifest-failed',
          manifestScoreCodeStored ? 'pet-manifest-incomplete' : 'pet-manifest-stopped'
        );
        line.innerText = manifestScoreCodeStored
          ? `${promptBuilderInterfaceText?.promptBuilderManifestIncomplete}`
          : `${promptBuilderInterfaceText?.promptBuilderManifestStopped}`;
        promptExperimentResultContainer.appendChild(line);
      } finally {
        promptExperimentCreateModelButton.disabled = false;
        promptExperimentCreateModelButton.innerText = manifestButtonText();
      }
    }
    const manifestOutputsLabel = document.createElement('p');
    manifestOutputsLabel.classList.add('fw-bold', 'mb-1');
    manifestOutputsLabel.innerText = `${promptBuilderInterfaceText?.promptBuilderManifestOutputsLabel}`;
    promptExperimentManifestOptions.appendChild(manifestOutputsLabel);
    const manifestOutputsRow = document.createElement('div');
    DEFAULT_LLM_OUTPUTS.forEach((outputName) => {
      const outputDiv = document.createElement('div');
      outputDiv.classList.add('form-check', 'form-check-inline');
      const outputCheckbox = document.createElement('input');
      outputCheckbox.type = 'checkbox';
      outputCheckbox.classList.add('form-check-input');
      outputCheckbox.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-out-${outputName}`;
      outputCheckbox.checked = true;
      const outputLabel = document.createElement('label');
      outputLabel.classList.add('form-check-label');
      outputLabel.htmlFor = outputCheckbox.id;
      outputLabel.innerText = outputName;
      outputDiv.appendChild(outputCheckbox);
      outputDiv.appendChild(outputLabel);
      manifestOutputsRow.appendChild(outputDiv);
    });
    promptExperimentManifestOptions.appendChild(manifestOutputsRow);
    const outputVariablesLabel = document.createElement('p');
    outputVariablesLabel.classList.add('fw-bold', 'mb-1', 'mt-3');
    outputVariablesLabel.innerText = `${promptBuilderInterfaceText?.promptBuilderOutputVariablesHeading}`;
    const outputVariablesDescription = document.createElement('p');
    outputVariablesDescription.innerText = `${promptBuilderInterfaceText?.promptBuilderOutputVariablesDescription}`;
    const promptBuilderOutputVariablesContainer = document.createElement('div');
    promptBuilderOutputVariablesContainer.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-outvars`;
    const outputVariablesAddButton = document.createElement('button');
    outputVariablesAddButton.type = 'button';
    outputVariablesAddButton.classList.add('btn', 'btn-outline-secondary');
    outputVariablesAddButton.innerText = `${promptBuilderInterfaceText?.promptBuilderOutputVariablesAddButton}`;
    outputVariablesAddButton.onclick = () => createOutputVariableRow();
    promptExperimentManifestOptions.appendChild(outputVariablesLabel);
    promptExperimentManifestOptions.appendChild(outputVariablesDescription);
    promptExperimentManifestOptions.appendChild(promptBuilderOutputVariablesContainer);
    promptExperimentManifestOptions.appendChild(outputVariablesAddButton);
    // A decision template always calls its container itself and returns typed
    // outputs: the default ones chosen here, plus one pair per question.
    const promptExperimentDecisionManifestOptions = document.createElement('div');
    promptExperimentDecisionManifestOptions.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-manifest-decision`;
    promptExperimentDecisionManifestOptions.classList.add('pet-manifest-options', 'd-none');
    const decisionOutputsLabel = document.createElement('p');
    decisionOutputsLabel.classList.add('fw-bold', 'mb-1');
    decisionOutputsLabel.innerText = `${promptBuilderInterfaceText?.promptBuilderManifestOutputsLabel}`;
    promptExperimentDecisionManifestOptions.appendChild(decisionOutputsLabel);
    const decisionOutputsRow = document.createElement('div');
    DEFAULT_DECISION_OUTPUTS.forEach((outputName) => {
      const outputDiv = document.createElement('div');
      outputDiv.classList.add('form-check', 'form-check-inline');
      const outputCheckbox = document.createElement('input');
      outputCheckbox.type = 'checkbox';
      outputCheckbox.classList.add('form-check-input');
      outputCheckbox.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-decout-${outputName}`;
      outputCheckbox.checked = true;
      const outputLabel = document.createElement('label');
      outputLabel.classList.add('form-check-label');
      outputLabel.htmlFor = outputCheckbox.id;
      outputLabel.innerText = outputName;
      outputDiv.appendChild(outputCheckbox);
      outputDiv.appendChild(outputLabel);
      decisionOutputsRow.appendChild(outputDiv);
    });
    promptExperimentDecisionManifestOptions.appendChild(decisionOutputsRow);
    const decisionOutputsInfo = document.createElement('p');
    decisionOutputsInfo.classList.add('mt-2', 'mb-0');
    decisionOutputsInfo.innerText = `${promptBuilderInterfaceText?.promptBuilderManifestDecisionOutputsInfo}`;
    promptExperimentDecisionManifestOptions.appendChild(decisionOutputsInfo);

    function createOutputVariableRow(variable?: PromptOutputVariable): void {
      const outputRow = document.createElement('div');
      outputRow.classList.add('row', 'g-2', 'align-items-start', 'mb-2', 'pb-outvar-row');
      // Name
      const nameColumn = document.createElement('div');
      nameColumn.classList.add('col-md-3');
      const nameInput = document.createElement('input');
      nameInput.type = 'text';
      nameInput.maxLength = 32;
      nameInput.classList.add('form-control', 'pb-outvar-name');
      nameInput.placeholder = `${promptBuilderInterfaceText?.promptBuilderVariablesNameLabel}`;
      nameInput.setAttribute('aria-label', `${promptBuilderInterfaceText?.promptBuilderVariablesNameLabel}`);
      nameInput.value = variable?.name ?? '';
      nameInput.oninput = () => validateOutputVariableRows();
      const nameFeedback = document.createElement('div');
      nameFeedback.classList.add('invalid-feedback');
      nameColumn.appendChild(nameInput);
      nameColumn.appendChild(nameFeedback);
      // Description
      const descriptionColumn = document.createElement('div');
      descriptionColumn.classList.add('col-md-4');
      const descriptionInput = document.createElement('input');
      descriptionInput.type = 'text';
      descriptionInput.maxLength = 500;
      descriptionInput.classList.add('form-control', 'pb-outvar-description');
      descriptionInput.placeholder = `${promptBuilderInterfaceText?.promptBuilderVariablesDescriptionLabel}`;
      descriptionInput.setAttribute('aria-label', `${promptBuilderInterfaceText?.promptBuilderVariablesDescriptionLabel}`);
      descriptionInput.value = variable?.description ?? '';
      descriptionColumn.appendChild(descriptionInput);
      // Data type
      const typeColumn = document.createElement('div');
      typeColumn.classList.add('col-md-2');
      const typeSelect = document.createElement('select');
      typeSelect.classList.add('form-select', 'pb-outvar-type');
      typeSelect.setAttribute('aria-label', `${promptBuilderInterfaceText?.promptBuilderVariablesTypeLabel}`);
      const stringOption = document.createElement('option');
      stringOption.value = 'string';
      stringOption.innerText = `${promptBuilderInterfaceText?.promptBuilderVariablesTypeString}`;
      const decimalOption = document.createElement('option');
      decimalOption.value = 'decimal';
      decimalOption.innerText = `${promptBuilderInterfaceText?.promptBuilderVariablesTypeDecimal}`;
      typeSelect.appendChild(stringOption);
      typeSelect.appendChild(decimalOption);
      typeSelect.value = variable?.type === 'decimal' ? 'decimal' : 'string';
      typeSelect.onchange = () => validateOutputVariableRows();
      typeColumn.appendChild(typeSelect);
      // Optional default value, used when the key is missing from the response
      const defaultColumn = document.createElement('div');
      defaultColumn.classList.add('col-md-2');
      const defaultInput = document.createElement('input');
      defaultInput.type = 'text';
      defaultInput.classList.add('form-control', 'pb-outvar-default');
      defaultInput.placeholder = `${promptBuilderInterfaceText?.promptBuilderVariablesDefaultLabel}`;
      defaultInput.setAttribute('aria-label', `${promptBuilderInterfaceText?.promptBuilderVariablesDefaultLabel}`);
      defaultInput.value = variable?.defaultValue ?? '';
      defaultInput.oninput = () => validateOutputVariableRows();
      const defaultFeedback = document.createElement('div');
      defaultFeedback.classList.add('invalid-feedback');
      defaultFeedback.innerText = `${promptBuilderInterfaceText?.promptBuilderVariablesValueNotNumeric}`;
      defaultColumn.appendChild(defaultInput);
      defaultColumn.appendChild(defaultFeedback);
      // Remove
      const removeColumn = document.createElement('div');
      removeColumn.classList.add('col-md-1');
      const removeButton = document.createElement('button');
      removeButton.type = 'button';
      removeButton.classList.add('btn', 'btn-outline-danger', 'pb-outvar-remove');
      removeButton.innerHTML = '&times;';
      removeButton.title = `${promptBuilderInterfaceText?.promptBuilderVariablesRemoveButton}`;
      removeButton.setAttribute('aria-label', `${promptBuilderInterfaceText?.promptBuilderVariablesRemoveButton}`);
      removeButton.onclick = () => {
        outputRow.remove();
        validateOutputVariableRows();
      };
      removeColumn.appendChild(removeButton);

      outputRow.appendChild(nameColumn);
      outputRow.appendChild(descriptionColumn);
      outputRow.appendChild(typeColumn);
      outputRow.appendChild(defaultColumn);
      outputRow.appendChild(removeColumn);
      promptBuilderOutputVariablesContainer.appendChild(outputRow);
    }

    // Flag invalid, duplicate or reserved names and non-numeric decimal defaults.
    function validateOutputVariableRows(): void {
      const seenNames = new Set<string>();
      promptBuilderOutputVariablesContainer.querySelectorAll('.pb-outvar-row').forEach((row) => {
        const nameInput = row.querySelector('.pb-outvar-name') as HTMLInputElement;
        const nameFeedback = nameInput.nextElementSibling as HTMLElement;
        const typeSelect = row.querySelector('.pb-outvar-type') as HTMLSelectElement;
        const defaultInput = row.querySelector('.pb-outvar-default') as HTMLInputElement;
        const name = nameInput.value.trim();
        let nameInvalidText = '';
        if (name !== '' && !isValidDS2VariableName(name)) {
          nameInvalidText = `${promptBuilderInterfaceText?.promptBuilderVariablesNameInvalid}`;
        } else if (name !== '' && (RESERVED_OUTPUT_NAMES.includes(name) || isReservedScoreOutput(name))) {
          nameInvalidText = `${promptBuilderInterfaceText?.promptBuilderOutputVariablesNameReserved}`;
        } else if (name !== '' && seenNames.has(name)) {
          nameInvalidText = `${promptBuilderInterfaceText?.promptBuilderVariablesNameDuplicate}`;
        } else if (name !== '') {
          seenNames.add(name);
        }
        nameFeedback.innerText = nameInvalidText;
        nameInput.classList.toggle('is-invalid', nameInvalidText !== '');
        const defaultInvalid =
          typeSelect.value === 'decimal' && defaultInput.value.trim() !== '' && isNaN(Number(defaultInput.value));
        defaultInput.classList.toggle('is-invalid', defaultInvalid);
      });
    }

    function setPromptOutputVariables(outputVariables: PromptOutputVariable[]): void {
      promptBuilderOutputVariablesContainer.innerHTML = '';
      outputVariables.forEach((variable) => createOutputVariableRow(variable));
      validateOutputVariableRows();
    }

    // Snapshot of the manifest panel, stored with a run so loading restores it.
    function collectManifestConfig(): ManifestConfig {
      return {
        integratedLLMCall: promptExperimentIntegratedCallCheckbox.checked,
        selectedOutputs: DEFAULT_LLM_OUTPUTS.filter(
          (outputName) =>
            (document.getElementById(`${paneID}-obj-${promptBuilderObject?.id}-pet-out-${outputName}`) as HTMLInputElement | null)?.checked
        ),
        outputVariables: collectPromptOutputVariables(),
      };
    }

    // Restore the manifest panel from a run's stored configuration; runs
    // without one reset the panel to its defaults.
    function applyManifestConfig(config?: ManifestConfig | null): void {
      const targetConfig: ManifestConfig = config ?? {
        integratedLLMCall: false,
        selectedOutputs: [...DEFAULT_LLM_OUTPUTS],
        outputVariables: [],
      };
      if (promptExperimentIntegratedCallCheckbox.checked !== targetConfig.integratedLLMCall) {
        promptExperimentIntegratedCallCheckbox.checked = targetConfig.integratedLLMCall;
        // Fires the listener that shows/hides the options panel
        promptExperimentIntegratedCallCheckbox.dispatchEvent(new Event('change'));
      }
      DEFAULT_LLM_OUTPUTS.forEach((outputName) => {
        const outputCheckbox = document.getElementById(
          `${paneID}-obj-${promptBuilderObject?.id}-pet-out-${outputName}`
        ) as HTMLInputElement | null;
        if (outputCheckbox) outputCheckbox.checked = targetConfig.selectedOutputs.includes(outputName);
      });
      setPromptOutputVariables(Array.isArray(targetConfig.outputVariables) ? targetConfig.outputVariables : []);
    }

    // Persist the current manifest configuration with the run that is being
    // manifested (the most recent one with a best response).
    function stampManifestConfigOnBestRun(): void {
      for (let index = promptExperimentTracker.length - 1; index >= 0; index--) {
        const trackerEntry = promptExperimentTracker[index];
        const hasBestPrompt = Object.keys(trackerEntry).some(
          (key) => !TRACKER_META_KEYS.includes(key) && (trackerEntry[key] as ModelExperimentData)?.best_prompt
        );
        if (hasBestPrompt) {
          trackerEntry.manifest = collectManifestConfig();
          // Rebuild the saveable rows so the save that follows persists it
          petRows = promptExperimentTransformData(promptExperimentTracker);
          return;
        }
      }
    }

    // Collect the currently valid output variable definitions.
    function collectPromptOutputVariables(): PromptOutputVariable[] {
      validateOutputVariableRows();
      const outputVariables: PromptOutputVariable[] = [];
      const seenNames = new Set<string>();
      promptBuilderOutputVariablesContainer.querySelectorAll('.pb-outvar-row').forEach((row) => {
        const name = (row.querySelector('.pb-outvar-name') as HTMLInputElement).value.trim();
        if (!isValidDS2VariableName(name) || RESERVED_OUTPUT_NAMES.includes(name) || isReservedScoreOutput(name) || seenNames.has(name)) return;
        seenNames.add(name);
        outputVariables.push({
          name,
          description: (row.querySelector('.pb-outvar-description') as HTMLInputElement).value.trim(),
          type: (row.querySelector('.pb-outvar-type') as HTMLSelectElement).value === 'decimal' ? 'decimal' : 'string',
          defaultValue: (row.querySelector('.pb-outvar-default') as HTMLInputElement).value,
        });
      });
      return outputVariables;
    }

    // Result message for the manifest action (shown in the manifest box).
    const promptExperimentResultContainer = document.createElement('div');
    promptExperimentResultContainer.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-save-result`;
    // Result message for a plain Save (shown next to the Save button).
    const promptExperimentSaveResultContainer = document.createElement('div');
    promptExperimentSaveResultContainer.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-save-only-result`;
    // The line that the stored runs of the open prompt-test were not read - or
    // were read, and are no runs this page can read.
    function storedRunsText(): string {
      return `${storedRunsUnusable ? promptBuilderInterfaceText?.promptBuilderStoredRunsUnusable : promptBuilderInterfaceText?.promptBuilderStoredRunsUnread}`;
    }
    function showStoredRunsUnread(container: HTMLElement): void {
      const line = document.createElement('p');
      line.classList.add('text-danger', 'pet-runs-unread');
      line.innerText = storedRunsText();
      container.replaceChildren(line);
    }

    /**
     * A write SAS Model Manager refused is a write that failed. The calls that
     * upload, delete or update hand the status back instead of throwing; a
     * row of writes that went on over a refusal said "saved", "manifested" or
     * "deleted" for what was not written. This throws, so that the row takes
     * the way it takes when a request throws.
     */
    function written(status: number, what: string): void {
      if (status >= 300) throw new ViyaRefused(what, status, `${what} was refused with HTTP ${status}`);
    }

    /**
     * What a write that got no answer comes to once what SAS Viya holds was
     * read back (see writeOrReadBack): it was not carried out, or that is not
     * known - for a launch whose job is not among the running jobs, or as
     * what SAS Viya holds could not be read back. '' for any other failure -
     * a refusal, which is an answer.
     */
    function noAnswerText(error: unknown): string {
      if (!(error instanceof UnansweredWrite)) return '';
      return `${
        error.done === false
          ? promptBuilderInterfaceText?.promptBuilderNoAnswerNotDone
          : error.jobNotRunning
            ? promptBuilderInterfaceText?.promptBuilderNoAnswerJobNotRunning
            : promptBuilderInterfaceText?.promptBuilderNoAnswerUnknown
      }`;
    }

    // Save the experiments to the SAS Model Manager. The success/failure
    // message goes to `resultContainer` — next to Save for a plain save, or the
    // manifest box when called as the first step of manifesting.
    //
    // The answer is true when the experiments are saved, and false in every
    // other case - nothing to save, a refusal, a request that threw. It does
    // not throw: a request that fails outright (no answer, or an answer that
    // is no JSON, such as the error page of an ingress) used to end the
    // function where it stood, with "Save Experiments" disabled on
    // "Saving..." for good and nothing said. Now the button is given back on
    // every path, and such a failure is a line in `resultContainer`. Whoever
    // saves as a first step (a manifest, an optimization) reads the answer
    // and does not go on over experiments that are not saved.
    async function promptBuilderSaveExperiments(
      resultContainer: HTMLElement = promptExperimentSaveResultContainer
    ): Promise<boolean> {
      // Add spinner to save button
      const promptExperimentSaveTargetButton = document.getElementById(
        `${paneID}-obj-${promptBuilderObject?.id}-pet-save-button`
      ) as HTMLButtonElement;
      promptExperimentSaveTargetButton.disabled = true;
      promptExperimentSaveTargetButton.innerHTML = `<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> ${promptBuilderInterfaceText.promptBuilderSaveExperimentsButtonStatus}`;
      try {
        const promptExperimentRunModel = (
          document.getElementById(`${promptBuilderObject?.id}-prompt-dropdown`) as HTMLSelectElement
        ).value;
        // Check if an experiment was run (deleting runs also counts as a change,
        // so an emptied tracker can still be saved)
        if (petRows.length === 0 && !experimentsModified) {
          alert(promptBuilderInterfaceText.promptExperimentSaveModelsExperimentAlert);
          return false;
        }
        // Check if a prompt test was selected
        if (promptExperimentRunModel === promptBuilderInterfaceText.promptSelect) {
          alert(promptBuilderInterfaceText.promptExperimentSaveModelsPromptAlert);
          return false;
        }
        // The upload replaces the stored tracker: not over runs the page
        // could not read.
        if (storedRunsUnread) {
          showStoredRunsUnread(resultContainer);
          return false;
        }
        // A tracker that is stored already is kept in a new model version, and
        // then replaced in one upload (onConflict=update). It used to be
        // deleted first: an upload that failed after that left the prompt
        // without any tracker.
        const promptBuilderAvailablePTE = await getModelContents(promptExperimentRunModel);
        if (promptBuilderAvailablePTE.some((content) => content?.name === 'Prompt-Experiment-Tracker.json')) {
          await createModelVersion(promptExperimentRunModel);
        }
        const promptExperimentPromptResponseObject = await createModelContent(
          promptExperimentRunModel,
          petRows,
          'Prompt-Experiment-Tracker.json'
        );
        // A status below 300 is a write that went through, as for every other
        // write of this page (see written).
        if (promptExperimentPromptResponseObject.status_code < 300) {
          experimentsModified = false;
          showToast(`${promptBuilderInterfaceText?.promptBuilderSaveToast}`);
          resultContainer.innerHTML = `<p>${promptBuilderInterfaceText.promptExperimentSaveSucessResponse} <a target="_blank" rel="noopener noreferrer" href="${VIYA}/SASModelManager/models/${promptExperimentRunModel}">${VIYA}/SASModelManager/models/${promptExperimentRunModel}</a></p>`;
          // Like every link that leaves the page: a new tab where the browser
          // allows one, the copied link where the sandboxed iframe of a report
          // does not - there a plain target="_blank" link does nothing at all.
          const savedLink = resultContainer.querySelector('a');
          if (savedLink) savedLink.onclick = (event) => openModelManagerLink(event, savedLink, promptBuilderInterfaceText);
          return true;
        }
        showToast(`${promptBuilderInterfaceText.promptExperimentSaveFailureResponse}`);
        resultContainer.innerHTML = `<p>${promptBuilderInterfaceText.promptExperimentSaveFailureResponse}</p>`;
        return false;
      } catch (error) {
        // A write that got no answer was read back (see writeOrReadBack): one
        // that was carried out did not get here, and the line says what the
        // read back found. For any other request that failed, what the
        // service did with it is not known: the prompt holds the experiments
        // it held before, or these. No toast says "saved".
        console.error('Saving the experiments stopped at a request that failed.', error);
        const line = document.createElement('p');
        line.classList.add('text-danger', 'pet-save-failed');
        line.innerText = noAnswerText(error)
          ? `${promptBuilderInterfaceText?.promptBuilderSaveExperimentsButton}: ${noAnswerText(error)}`
          : `${promptBuilderInterfaceText?.promptExperimentSaveStopped}`;
        resultContainer.replaceChildren(line);
        return false;
      } finally {
        // Usable again once no write is out - this save may be the first
        // stage of a manifest or an optimization (see createSelectionLock).
        promptExperimentSaveTargetButton.disabled = selectionLock.busy;
        promptExperimentSaveTargetButton.innerText = `${promptBuilderInterfaceText?.promptBuilderSaveExperimentsButton}`;
      }
    }

    // Turn the best prompt into a model
    async function promptBulderCreateBestPromptModel(): Promise<void> {
      // Disable the create model button
      const promptExperimentCreateModelTargetButton = document.getElementById(
        `${paneID}-obj-${promptBuilderObject?.id}-pet-create-model-button`
      ) as HTMLButtonElement;
      promptExperimentCreateModelTargetButton.disabled = true;
      promptExperimentCreateModelTargetButton.innerHTML = `<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> ${promptBuilderInterfaceText.promptBuilderSaveExperimentsButtonStatus}`;
      // Get target container to display a message to the user
      const promptExperimentResultTargetContainer = document.getElementById(
        `${paneID}-obj-${promptBuilderObject?.id}-pet-save-result`
      );
      // Get the selected model ID & model name
      const promptDropdown = document.getElementById(`${promptBuilderObject?.id}-prompt-dropdown`) as HTMLSelectElement;
      const promptExperimentRunModel = promptDropdown.value;
      const promptExperimentRunModelName = promptDropdown.options[promptDropdown.selectedIndex].text
        .toLowerCase()
        .replace(/[\s-]+/g, '_');
      // Get the latest Prompt with a Best Prompt selected
      let bestPromptItem: PETRow | null = null;
      petRows.forEach((item) => {
        if (item.best_prompt) {
          if (bestPromptItem === null || item.runId > bestPromptItem.runId) {
            bestPromptItem = item;
          }
        }
      });
      // Get the system & user Prompt for the Best Prompt (its run header row)
      let basePrompt: PETRow | null = null;
      if (bestPromptItem !== null) {
        basePrompt = petRows.find(
          (item) => item.runId === (bestPromptItem as PETRow).runId && item.model === ''
        ) ?? null;
        const promptInputs: {
          name: string;
          description: string;
          level: string;
          type: string;
          length: number;
        }[] = [];
        // Runs created with the variables manager carry their definitions: the
        // model inputs are the variables referenced as {{name}} in either
        // prompt template. Runs without stored variables fall back to the
        // legacy variableName:variableValue;... parsing of the user prompt.
        const runVariables: PromptVariable[] | null = Array.isArray(basePrompt?.variables)
          ? (basePrompt!.variables as PromptVariable[])
          : null;
        const referencedVariables: PromptVariable[] = [];
        if (runVariables) {
          runVariables.forEach((variable) => {
            const variableToken = new RegExp(`\\{\\{\\s*${variable.name}\\s*\\}\\}`);
            if (variableToken.test(basePrompt!.systemPrompt) || variableToken.test(basePrompt!.userPrompt)) {
              referencedVariables.push(variable);
              promptInputs.push({
                name: variable.name,
                description: variable.description,
                level: variable.type === 'decimal' ? 'interval' : 'nominal',
                type: variable.type === 'decimal' ? 'decimal' : 'string',
                length: variable.type === 'decimal' ? 8 : 10000000,
              });
            }
          });
        } else {
          let parsedUserPrompt = basePrompt!.userPrompt.trim().split(';');
          // Remove empty items, if the user closed with a semi-colon
          parsedUserPrompt = parsedUserPrompt.filter(Boolean);
          // Parse the input and create the input signature
          if (parsedUserPrompt.length >= 1) {
            parsedUserPrompt.forEach((item) => {
              // Check that the variable name doesn't contain blanks
              const tempInputVar = item.split(':');
              if (tempInputVar.length > 1 && isValidDS2VariableName(tempInputVar[0])) {
                const varType =
                  String(tempInputVar[1]).trim() === '' || isNaN(Number(tempInputVar[1]))
                    ? 'string'
                    : 'decimal';
                const varLevel = varType === 'string' ? 'nominal' : 'interval';
                promptInputs.push({
                  name: tempInputVar[0],
                  description: '',
                  level: varLevel,
                  type: varType,
                  length: varType === 'string' ? 128000 : 8,
                });
              } else {
                if (!promptInputs.some((pi) => pi.name === 'userPrompt')) {
                  promptInputs.push({
                    name: 'userPrompt',
                    description: 'Captures any non-structured inputs for the prompt template',
                    level: 'nominal',
                    type: 'string',
                    length: 128000,
                  });
                }
              }
            });
          } else {
            promptInputs.push({
              name: 'userPrompt',
              description: 'Captures any non-structured inputs for the prompt template',
              level: 'nominal',
              type: 'string',
              length: 128000,
            });
          }
        }
        // The inputs come from the saved run, which may be older than the
        // name check of the variables manager. An input named like a Python
        // keyword or like a name the score code uses itself would be manifested
        // into code that does not compile, or that sends something else than
        // the input: it is refused here, by name, before anything is written.
        const refusedInput = promptInputs.find((input) => isReservedScoreInput(input.name));
        if (refusedInput) {
          if (promptExperimentResultTargetContainer) {
            promptExperimentResultTargetContainer.innerText = `${promptBuilderInterfaceText?.promptBuilderManifestVariableName}`.replace(
              '{name}',
              refusedInput.name
            );
          }
          promptExperimentCreateModelTargetButton.disabled = false;
          promptExperimentCreateModelTargetButton.innerText = `${promptBuilderInterfaceText?.promptBuilderCreateModelButton}`;
          return;
        }
        // Check if the options contains an API-Key
        let requiresAPIKey = false;
        const bestPromptOptionsList = (bestPromptItem as PETRow).options
          .replace(/[{}]/g, '')
          .split(',')
          .map((str) => {
            const idx = str.indexOf('API_KEY');
            if (idx !== -1) {
              requiresAPIKey = true;
              promptInputs.push({
                name: 'API_KEY',
                description: 'This LLM call requires you to input an API-Key',
                level: 'nominal',
                type: 'string',
                length: 256,
              });
            }
            return idx !== -1 ? str.substring(0, idx) : str;
          })
          .filter((str) => str.trim() !== '');

        // Create the input and user input strings for the score code
        let scoreCodeInput = '';
        let scoreCodeUserPrompt = '';
        for (let i = 0; i < promptInputs.length; i++) {
          if (i !== 0) {
            scoreCodeInput += ', ';
            scoreCodeUserPrompt += '; ';
          }
          scoreCodeInput += promptInputs[i].name;
          if (promptInputs[i].name !== 'API_KEY') {
            scoreCodeUserPrompt += `${promptInputs[i].name}: {str(${promptInputs[i].name}).strip()}`;
          }
        }
        // For variable-based runs both prompts become Python f-strings built
        // from the stored templates: the text is escaped for the literal, its
        // braces stay literal braces, and each referenced {{variable}} becomes
        // a score-function input that is inserted at its position in the
        // template. The system prompt of a run stored without variables is a
        // plain literal, escaped the same way. Either way quotes and
        // backslashes in a prompt reach the LLM as they were typed.
        const referencedVariableNames = referencedVariables.map((variable) => variable.name);
        const scoreCodeSystemPromptLiteral = runVariables
          ? `f"""${pythonFStringBody(basePrompt!.systemPrompt, referencedVariableNames)}"""`
          : `"""${pythonTripleQuotedBody(basePrompt!.systemPrompt)}"""`;
        const scoreCodeUserPromptLiteral = runVariables
          ? `f"""${pythonFStringBody(basePrompt!.userPrompt, referencedVariableNames)}"""`
          : `f"${scoreCodeUserPrompt}"`;
        // Create the options string for the score code: the stored text as
        // the body of an f"""...""" literal, escaped like the prompts, so a
        // backslash or a quote in an option value neither ends the literal nor
        // becomes an escape sequence. The key is appended after the escaping,
        // as the one placeholder of the string.
        let scoreCodeOptions = pythonFStringBody(bestPromptOptionsList.join(','), []);
        if (requiresAPIKey) {
          scoreCodeOptions += scoreCodeOptions.length > 0 ? ',API_KEY:{API_KEY}' : 'API_KEY:{API_KEY}';
        }
        // With the integrated call the manifested model calls the LLM container
        // itself and returns the selected default outputs (mirroring how the
        // Prompt Builder consumes the SCR responses) plus any output variables
        // parsed from the LLM's JSON response; otherwise it returns the
        // llmBody/llmURL pair for the Call LLM node in SAS Intelligent Decisioning.
        const integratedLLMCall = promptExperimentIntegratedCallCheckbox.checked;
        const selectedDefaultOutputs = DEFAULT_LLM_OUTPUTS.filter(
          (outputName) =>
            (document.getElementById(`${paneID}-obj-${promptBuilderObject?.id}-pet-out-${outputName}`) as HTMLInputElement | null)?.checked
        );
        const outputVariables = integratedLLMCall ? collectPromptOutputVariables() : [];
        const parseOutputs = outputVariables.length > 0;
        const defaultOutputDefinitions: Record<string, { name: string; description: string; level: string; type: string; length: number }> = {
          response: {
            name: 'response',
            description: 'The response of the LLM to the manifested prompt',
            level: 'nominal',
            type: 'string',
            length: 1000000,
          },
          run_time: {
            name: 'run_time',
            description: 'Time in seconds the LLM call took',
            level: 'interval',
            type: 'decimal',
            length: 8,
          },
          prompt_length: {
            name: 'prompt_length',
            description: 'Number of input tokens',
            level: 'interval',
            type: 'decimal',
            length: 8,
          },
          output_length: {
            name: 'output_length',
            description: 'Number of output tokens',
            level: 'interval',
            type: 'decimal',
            length: 8,
          },
        };
        // Create the output variables definition
        const outputVars = integratedLLMCall
          ? [
              ...selectedDefaultOutputs.map((outputName) => defaultOutputDefinitions[outputName]),
              ...outputVariables.map((variable) => ({
                name: variable.name,
                description: variable.description,
                level: variable.type === 'decimal' ? 'interval' : 'nominal',
                type: variable.type === 'decimal' ? 'decimal' : 'string',
                length: variable.type === 'decimal' ? 8 : 10000000,
              })),
              ...(parseOutputs
                ? [
                    {
                      name: 'parse_status',
                      description:
                        '1 when the LLM response was parsed as JSON and every output variable was extracted, 0 otherwise',
                      level: 'interval',
                      type: 'decimal',
                      length: 8,
                    },
                  ]
                : []),
            ]
          : [
              {
                name: 'llmBody',
                description: 'Contains the structered input for the Call LLM node in SAS Intelligent Decisioning',
                level: 'nominal',
                type: 'string',
                length: 1000000,
              },
              {
                name: 'llmURL',
                description: 'The URL of the LLM container that will be called',
                level: 'nominal',
                type: 'string',
                length: 256,
              },
            ];
        // At least one output has to remain selected or defined
        if (outputVars.length === 0) {
          if (promptExperimentResultTargetContainer) {
            promptExperimentResultTargetContainer.innerText = `${promptBuilderInterfaceText?.promptBuilderManifestNoOutputs}`;
          }
          promptExperimentCreateModelTargetButton.disabled = false;
          promptExperimentCreateModelTargetButton.innerText = `${promptBuilderInterfaceText?.promptBuilderCreateModelButton}`;
          return;
        }
        // Handle the different LLM Container deployment types
        const deploymentTypeHandling = (promptBuilderObject.deploymentType as string) ?? 'k8s';
        let llmEndpoint = '';
        if (deploymentTypeHandling === 'k8s') {
          llmEndpoint = '{endpoint}/{llm}/{llm}';
        } else if (deploymentTypeHandling === 'aca') {
          llmEndpoint = 'https://{llm.replace("_", "-")}.{endpoint}/{llm}';
        }
        // The tail of the score code: either hand the prepared call over to the
        // Call LLM node (llmBody/llmURL) or perform it directly with requests,
        // unwrapping the SCR `data` envelope exactly like the Prompt Builder
        // does, and optionally parsing the JSON response into output variables.
        const pythonDefaultLiteral = (variable: PromptOutputVariable): string => {
          if (variable.type === 'decimal') {
            const numericDefault = Number(variable.defaultValue);
            return variable.defaultValue.trim() !== '' && !isNaN(numericDefault) ? `${numericDefault}` : 'None';
          }
          return JSON.stringify(variable.defaultValue);
        };
        const scoreCodeOutputList = integratedLLMCall
          ? [
              ...selectedDefaultOutputs,
              ...outputVariables.map((variable) => variable.name),
              ...(parseOutputs ? ['parse_status'] : []),
            ].join(', ')
          : 'llmBody, llmURL';
        const parsingBlock = `            # Parse the JSON response into the output variables. A fenced
            # \`\`\`json block is unwrapped first, since LLMs often add one.
            cleaned = str(response).strip()
            if cleaned.startswith("\`\`\`"):
                cleaned = cleaned[cleaned.find("\\n") + 1 :] if "\\n" in cleaned else cleaned[3:]
                if cleaned.rstrip().endswith("\`\`\`"):
                    cleaned = cleaned.rstrip()[:-3]
            try:
                parsed = json.loads(cleaned)
                if not isinstance(parsed, dict):
                    raise ValueError("the response is not a JSON object")
${outputVariables
  .map(
    (variable) =>
      `                if "${variable.name}" in parsed:\n                    ${variable.name} = ${variable.type === 'decimal' ? 'float' : 'str'}(parsed["${variable.name}"])`
  )
  .join('\n')}
                if all(key in parsed for key in [${outputVariables.map((variable) => `"${variable.name}"`).join(', ')}]):
                    parse_status = 1
            except Exception:
                parse_status = 0
`;
        const scoreCodeReturn = integratedLLMCall
          ? `${
              parseOutputs
                ? `    # Defaults for the output variables parsed from the LLM response
${outputVariables.map((variable) => `    ${variable.name} = ${pythonDefaultLiteral(variable)}`).join('\n')}
    # 1 when the response was parsed and every output variable was extracted
    parse_status = 0
`
                : ''
            }    response = ""
    run_time = None
    prompt_length = None
    output_length = None
    # TLS verification of the LLM container call: trust the CA bundle SAS Viya
    # mounts into every pod, or the one LLMCONTAINERCABUNDLE points to. Setting
    # LLMCONTAINERSSLVERIFY=false disables the verification entirely.
${scoreCodeTlsDefaultComment(tlsVerificationOn)}
    sslVerify = os.getenv("LLMCONTAINERCABUNDLE", "/security/trustedcerts.pem")
    if not os.path.isfile(sslVerify):
        sslVerify = True
    if os.getenv("LLMCONTAINERSSLVERIFY", "").strip().lower() in ("false", "no", "0"):
        sslVerify = False
${SCORE_CODE_TLS_DEFAULT_BRANCH}
    # Call the LLM container and unwrap the SCR response envelope. Failures are
    # reported through the response output instead of raising, so a failed call
    # cannot abort a whole scoring or SAS Intelligent Decisioning run.
    try:
        llmCall = requests.post(
            llmURL,
            data=llmBody.encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            verify=sslVerify,
            timeout=float(os.getenv("LLMCONTAINERTIMEOUT", "600")),
        )
        if llmCall.status_code == 200:
            llmJson = llmCall.json()
            llmData = llmJson.get("data", llmJson) if isinstance(llmJson, dict) else {}
            response = llmData.get("response", "")
            run_time = llmData.get("run_time")
            prompt_length = llmData.get("prompt_length")
            output_length = llmData.get("output_length")
${parseOutputs ? parsingBlock : ''}        else:
            response = f"LLM call failed with status {llmCall.status_code}"
    except Exception as error:
        response = f"LLM call failed: {error}"
    return ${scoreCodeOutputList}`
          : `    return llmBody, llmURL`;
        // The request body is built with json.dumps in both variants, which
        // escapes the prompt texts and the runtime input values correctly. The
        // Call LLM node posts llmBody as it is, so the body has to be valid
        // JSON by itself - with a backslash, a tab or an apostrophe in a
        // prompt as much as without. ensure_ascii=False keeps that body the
        // UTF-8 text the node has always posted, instead of growing every
        // character outside ASCII into a six-character escape against the
        // length of the llmBody output.
        const scoreCodeBodyBlock = `    # This is the system prompt that was selected as the best one by the prompt engineer
    systemPrompt = ${scoreCodeSystemPromptLiteral}
    # Here the user prompt will be created from the inputs of the call
    userPrompt = ${scoreCodeUserPromptLiteral}
    # The request body for the LLM container in the SCR input format
    llmBody = json.dumps({"inputs": [{"name": "systemPrompt", "value": systemPrompt}, {"name": "userPrompt", "value": userPrompt}, {"name": "options", "value": options}]}${integratedLLMCall ? '' : ', ensure_ascii=False'})`;
        const scoreCode = `import os
${integratedLLMCall ? 'import requests\n' : ''}import json

def scoreModel(${scoreCodeInput}):
    "Output: ${scoreCodeOutputList}"
    # The llm and the target endpoint
    llm = ${pythonString((bestPromptItem as PETRow).model)}
    # Retrieves the endpoint where the LLM containers are hosted - e.g. https://example.com/llm
    # If an environment variable called LLMCONTAINERPATH is set, it will use that instead of the one stored in the prompt builder object
    endpoint = os.getenv("LLMCONTAINERPATH", ${pythonString(String(promptBuilderObject?.SCREndpoint ?? ''))})
    llmURL = f"""${llmEndpoint}"""
    # These are the options that were set for the best prompt
    options = f"""{{${scoreCodeOptions}}}"""
${scoreCodeBodyBlock}
${scoreCodeReturn}`;
        const mainfestPromptScoreCodeBlob = new Blob([scoreCode], { type: 'text/x-python' });
        // Clean up previous variables first
        const modelVariables = await getModelVariables(promptExperimentRunModel);
        // Every write of the manifest is read (see written): a refusal stops
        // it where a request that throws stops it.
        for (let i = 0; i < modelVariables.length; i++) {
          written(await deleteModelVariable(promptExperimentRunModel, modelVariables[i]!.id!), 'The delete of a variable');
        }
        const validatedModelName = validateAndCorrectPackageName(promptExperimentRunModelName);
        written((await createModelContent(promptExperimentRunModel, promptInputs, 'inputVar.json', 'inputVariables')).status_code, 'inputVar.json');
        written((await createModelContent(promptExperimentRunModel, outputVars, 'outputVar.json', 'outputVariables')).status_code, 'outputVar.json');
        if (
          !(await storeManifestScoreCode(
            promptExperimentRunModel,
            mainfestPromptScoreCodeBlob,
            `${validatedModelName.correctedName}.py`
          ))
        ) {
          promptExperimentCreateModelTargetButton.disabled = false;
          promptExperimentCreateModelTargetButton.innerText = `${promptBuilderInterfaceText?.promptBuilderCreateModelButton}`;
          return;
        }
        // Tag the model with the manifested LLM and the chosen manifest mode.
        // Tags from an earlier manifest (other LLM names, mode flags) are
        // removed first so re-manifesting does not leave stale tags behind.
        // A model whose score code calls the container without verifying the
        // certificate says so with a tag; the Call LLM node variant makes no
        // call and never carries it.
        // This is the first thing done once the score code is stored, before
        // any other request: whatever fails after it, the tag says what the
        // stored score code does.
        const manifestTags = [
          (bestPromptItem as PETRow).model,
          ...(integratedLLMCall ? ['LLM-Call-Included'] : []),
          ...(parseOutputs ? ['Output-Parsing'] : []),
          ...(integratedLLMCall && !tlsVerificationOn ? [TLS_VERIFICATION_OFF_TAG] : []),
        ];
        const staleManifestTags = [
          'LLM-Call-Included',
          'Output-Parsing',
          TLS_VERIFICATION_OFF_TAG,
          ...promptBuilderAvailableLLMs.map((availableLLM) => availableLLM.name),
          ...promptExperimentTracker.flatMap((trackerEntry) =>
            Object.keys(trackerEntry).filter((key) => !TRACKER_META_KEYS.includes(key))
          ),
        ];
        let manifestTagsWritten = false;
        try {
          manifestTagsWritten = (await updateModelTags(promptExperimentRunModel, staleManifestTags, manifestTags)) < 300;
        } catch (error) {
          console.error('Failed to update the tags of the manifested model.', error);
        }
        if (!manifestTagsWritten) reportTlsTagFailure(!manifestTags.includes(TLS_VERIFICATION_OFF_TAG));
        // The integrated call imports the requests package at score time: ship
        // a requirements.json (same format and role as the LLM definitions) so
        // publishing destinations that build a Python environment install it.
        // A stale one from an earlier manifest is removed when the LLM call is
        // no longer included.
        if (integratedLLMCall) {
          written(
            (
              await createModelContent(
                promptExperimentRunModel,
                [{ step: 'install requests', command: 'pip3 -q install requests' }],
                'requirements.json',
                'python pickle'
              )
            ).status_code,
            'requirements.json'
          );
        } else {
          const manifestModelContents = await getModelContents(promptExperimentRunModel);
          const staleRequirements = manifestModelContents.find(
            (modelContent) => modelContent.name === 'requirements.json'
          );
          if (staleRequirements?.id) {
            written(await deleteModelContent(promptExperimentRunModel, staleRequirements.id), 'The delete of requirements.json');
          }
        }
        // Copy the winning LLM's governance/cost attributes onto the manifested
        // prompt model (so a prompt carries the same metadata the mdb-registered
        // LLMs do) and (re)assert the current function value. Best-effort.
        await ensureLLMCostAttributes((bestPromptItem as PETRow).model);
        const bestLLM = llmAttributesByName.get((bestPromptItem as PETRow).model);
        const manifestAttributes: Record<string, unknown> = { function: PROMPT_FUNCTION };
        if (bestLLM) {
          for (const key of [
            'llmodelType', 'provider', 'deploymentId',
            'inputTokenCount', 'outputTokenCount', 'hostingCosts', 'endPoint',
          ] as const) {
            const value = bestLLM[key];
            if (value !== null && value !== undefined) manifestAttributes[key] = value;
          }
        }
        // When a model-card report URI is configured (Options pane), embed it as
        // the model card's custom chart — same attributes/shape mdb writes.
        const modelCardChart = buildModelCardChart(
          getAppState().config.viyaHost,
          promptBuilderObject?.modelCardReportURI as string | undefined
        );
        if (modelCardChart) {
          manifestAttributes.modelCardCustomChartReport = modelCardChart;
          manifestAttributes.modelCardCustomChartEnabled = true;
        }
        // The last write. Refused or thrown, the manifest is not completed:
        // the line of runManifest says that the attributes may be missing.
        written(await updateModelAttributes(promptExperimentRunModel, manifestAttributes), 'The update of the attributes');
        showToast(`${promptBuilderInterfaceText?.promptBuilderManifestToast}`);
      } else {
        if (promptExperimentResultTargetContainer) {
          promptExperimentResultTargetContainer.innerText = `${promptBuilderInterfaceText?.promptBuilderCreateModelNoBestPrompt}`;
        }
      }

      // Re-enable the create model button
      promptExperimentCreateModelTargetButton.disabled = false;
      promptExperimentCreateModelTargetButton.innerText = `${promptBuilderInterfaceText?.promptBuilderCreateModelButton}`;
    }

    /**
     * Turn the best decision run into a model: the state template's inputs in,
     * the question map baked in, one typed output pair per question out. The
     * container is called the way the integrated LLM manifest calls its LLM.
     */
    async function promptBuilderCreateDecisionModel(): Promise<void> {
      const createButton = promptExperimentCreateModelButton;
      createButton.disabled = true;
      createButton.innerHTML = `<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> ${promptBuilderInterfaceText.promptBuilderSaveExperimentsButtonStatus}`;
      const restore = (): void => {
        createButton.disabled = false;
        createButton.innerText = `${promptBuilderInterfaceText?.promptBuilderCreateDecisionModelButton}`;
      };
      const promptDropdown = document.getElementById(`${promptBuilderObject?.id}-prompt-dropdown`) as HTMLSelectElement;
      const promptExperimentRunModel = promptDropdown.value;
      const promptExperimentRunModelName = promptDropdown.options[promptDropdown.selectedIndex].text
        .toLowerCase()
        .replace(/[\s-]+/g, '_');
      let bestItem: PETRow | null = null;
      petRows.forEach((item) => {
        if (item.best_prompt && (bestItem === null || item.runId > bestItem.runId)) bestItem = item;
      });
      const chosen = bestItem as PETRow | null;
      const header = chosen ? (petRows.find((item) => item.runId === chosen.runId && item.model === '') ?? null) : null;
      if (!chosen || !header) {
        promptExperimentResultContainer.innerText = `${promptBuilderInterfaceText?.promptBuilderCreateModelNoBestDecisionModel}`;
        restore();
        return;
      }
      const questions = questionsFromJson(header.systemPrompt ?? '');
      const stateTemplate = header.userPrompt ?? '';
      const runVariables: PromptVariable[] = Array.isArray(header.variables) ? (header.variables as PromptVariable[]) : [];
      const referencedVariables = runVariables.filter((variable) =>
        new RegExp(`\\{\\{\\s*${variable.name}\\s*\\}\\}`).test(stateTemplate)
      );
      // The questions and the variables come from the saved run, which may be
      // older than the name checks of the designer and of the variables
      // manager (a template saved by an earlier release). The checks are made
      // again here, because the code is generated from the run: a name they
      // refuse would be manifested into code that does not compile or that
      // returns an output twice.
      const refusedVariable = referencedVariables.find((variable) => isReservedScoreInput(variable.name));
      if (refusedVariable) {
        promptExperimentResultContainer.innerText = `${promptBuilderInterfaceText?.promptBuilderManifestVariableName}`.replace(
          '{name}',
          refusedVariable.name
        );
        restore();
        return;
      }
      const stateVariableNames = referencedVariables.map((variable) => variable.name);
      for (const question of questions) {
        const problem = questionIdProblem(question, questions, stateVariableNames);
        if (problem === null) continue;
        promptExperimentResultContainer.innerText = `${promptBuilderInterfaceText?.promptBuilderDecisionManifestQuestionName}`
          .replace('{name}', question.id)
          .replace('{reason}', `${promptBuilderInterfaceText?.[problem]}`.replace('{max}', String(MAX_QUESTION_ID_LENGTH)));
        restore();
        return;
      }
      // The options of the winning run, the key entry replaced by an input.
      let requiresAPIKey = false;
      const optionParts = chosen.options
        .replace(/[{}]/g, '')
        .split(',')
        .filter((part) => {
          if (part.includes('API_KEY')) {
            requiresAPIKey = true;
            return false;
          }
          return part.trim() !== '';
        });
      const deploymentTypeHandling = (promptBuilderObject.deploymentType as string) ?? 'k8s';
      const endpointPattern =
        deploymentTypeHandling === 'aca' ? 'https://{llm.replace("_", "-")}.{endpoint}/{llm}' : '{endpoint}/{llm}/{llm}';
      const selectedOutputs = DEFAULT_DECISION_OUTPUTS.filter(
        (outputName) =>
          (document.getElementById(`${paneID}-obj-${promptBuilderObject?.id}-pet-decout-${outputName}`) as HTMLInputElement | null)?.checked
      );
      const manifest = buildDecisionManifest({
        model: chosen.model,
        endpointPattern,
        scrEndpoint: String(promptBuilderObject?.SCREndpoint ?? ''),
        stateTemplate,
        variables: referencedVariables.map((variable) => ({
          name: variable.name,
          description: variable.description,
          type: variable.type === 'decimal' ? 'decimal' : 'string',
        })),
        questions,
        options: optionParts.join(','),
        requiresAPIKey,
        selectedOutputs,
        sslVerify: tlsVerificationOn,
      });
      const modelVariables = await getModelVariables(promptExperimentRunModel);
      // Every write of the manifest is read (see written): a refusal stops it
      // where a request that throws stops it.
      for (const variable of modelVariables) {
        written(await deleteModelVariable(promptExperimentRunModel, variable.id!), 'The delete of a variable');
      }
      const validatedModelName = validateAndCorrectPackageName(promptExperimentRunModelName);
      written((await createModelContent(promptExperimentRunModel, manifest.inputs, 'inputVar.json', 'inputVariables')).status_code, 'inputVar.json');
      written((await createModelContent(promptExperimentRunModel, manifest.outputs, 'outputVar.json', 'outputVariables')).status_code, 'outputVar.json');
      if (
        !(await storeManifestScoreCode(
          promptExperimentRunModel,
          new Blob([manifest.scoreCode], { type: 'text/x-python' }),
          `${validatedModelName.correctedName}.py`
        ))
      ) {
        restore();
        return;
      }
      // The tags first, before any other request: whatever fails after the
      // score code is stored, TLS-Verification-Off says what that code does.
      const staleManifestTags = [
        'LLM-Call-Included',
        'Output-Parsing',
        'Decision-Call-Included',
        TLS_VERIFICATION_OFF_TAG,
        ...promptBuilderAvailableDecisionModels.map((model) => model.name),
        ...promptExperimentTracker.flatMap((trackerEntry) =>
          Object.keys(trackerEntry).filter((key) => !TRACKER_META_KEYS.includes(key))
        ),
      ];
      let manifestTagsWritten = false;
      try {
        manifestTagsWritten =
          (await updateModelTags(promptExperimentRunModel, staleManifestTags, [
            chosen.model,
            'Decision-Call-Included',
            ...(tlsVerificationOn ? [] : [TLS_VERIFICATION_OFF_TAG]),
          ])) < 300;
      } catch (error) {
        console.error('Failed to update the tags of the manifested model.', error);
      }
      if (!manifestTagsWritten) reportTlsTagFailure(tlsVerificationOn);
      written(
        (
          await createModelContent(
            promptExperimentRunModel,
            [{ step: 'install requests', command: 'pip3 -q install requests' }],
            'requirements.json',
            'python pickle'
          )
        ).status_code,
        'requirements.json'
      );
      await ensureLLMCostAttributes(chosen.model);
      const decisionModel = llmAttributesByName.get(chosen.model);
      const manifestAttributes: Record<string, unknown> = { function: PROMPT_FUNCTION };
      if (decisionModel) {
        for (const key of ['llmodelType', 'provider', 'deploymentId', 'inputTokenCount', 'outputTokenCount', 'hostingCosts', 'endPoint'] as const) {
          const value = decisionModel[key];
          if (value !== null && value !== undefined) manifestAttributes[key] = value;
        }
      }
      const modelCardChart = buildModelCardChart(
        getAppState().config.viyaHost,
        promptBuilderObject?.modelCardReportURI as string | undefined
      );
      if (modelCardChart) {
        manifestAttributes.modelCardCustomChartReport = modelCardChart;
        manifestAttributes.modelCardCustomChartEnabled = true;
      }
      // The last write. Refused or thrown, the manifest is not completed: the
      // line of runManifest says that the attributes may be missing.
      written(await updateModelAttributes(promptExperimentRunModel, manifestAttributes), 'The update of the attributes');
      showToast(`${promptBuilderInterfaceText?.promptBuilderManifestDecisionToast}`);
      restore();
    }

    // --- DSPy prompt optimization (Phase 3) --------------------------------
    // The Optimize section saves the prompt, launches the shipped Job
    // Execution job (proc python + DSPy, see SAS-Viya-Integrations/
    // Prompt-Optimization/), polls it with live log milestones, and offers the
    // optimised prompt back. Gated by the enableOptimization Option (see
    // optimizationEnabled at the top); the job is the sole writer of
    // Prompt-Optimization-Tracker.json — the browser only launches, polls and
    // reads.
    // Hoisting-safe handle for updateOptimizeState(), which manifest/tracker
    // refreshes call before this section is built (`var` so early calls see
    // `undefined` instead of a TDZ error and no-op).
    // eslint-disable-next-line no-var
    var optimizeUI:
      | {
          targetSelect: HTMLSelectElement;
          metricSelect: HTMLSelectElement;
          optimizerSelect: HTMLSelectElement;
          datasetTrackerRadio: HTMLInputElement;
          datasetCasRadio: HTMLInputElement;
          datasetTrackerInfo: HTMLElement;
          casRow: HTMLElement;
          casServerSelect: HTMLSelectElement;
          casLibSelect: HTMLSelectElement;
          casTableSelect: HTMLSelectElement;
          judgeRow: HTMLElement;
          judgeSelect: HTMLSelectElement;
          maxDemosInput: HTMLInputElement;
          samplesHint: HTMLElement;
          estimateLine: HTMLElement;
          compareButton: HTMLButtonElement;
          runButton: HTMLButtonElement;
          runWrapper: HTMLSpanElement;
          banner: HTMLDivElement;
          statusLine: HTMLElement;
          resultBox: HTMLElement;
          historyContainer: HTMLElement;
          logDetails: HTMLDetailsElement;
          logRuntime: HTMLElement;
          logList: HTMLElement;
        }
      | undefined;
    // The prompt-test the running job was launched for (the dropdown may
    // change while the job runs) and the launched job's id.
    let optimizeSourceModelId = '';
    let optimizeJobId = '';
    // What the job last reported: its state line, its milestones and why
    // there are none yet. Kept here and not only on the page, because the
    // Optimize step draws them for one prompt-test alone - the one the job
    // runs for (optimizeJobIsShown) - and shows them again when that one is
    // opened again while the job still runs.
    let optimizeJobStatus = '';
    let optimizeJobMessages: string[] = [];
    let optimizeJobLiveStatus: string | undefined;
    // Row count of the currently selected CAS dataset table (fetched lazily
    // when the table is picked) so the panel's call estimate reflects the
    // CAS source instead of the tracker count; null = not (yet) known.
    let optimizeCasRowCount: number | null = null;
    // Launch timestamp for the run-log's runtime display (0 = frozen) and the
    // 1-second browser-side ticker that keeps it smooth between job polls.
    let optimizeJobStartedAt = 0;
    let optimizeRuntimeHandle: number | null = null;

    /** mm:ss for the run-log runtime display. */
    function formatOptimizeRuntime(elapsedMs: number): string {
      const totalSeconds = Math.max(0, Math.round(elapsedMs / 1000));
      return `${Math.floor(totalSeconds / 60)}:${String(totalSeconds % 60).padStart(2, '0')}`;
    }

    /**
     * Whether the Optimize step shows the job: it does for the prompt-test
     * the job was launched for, and for no other.
     *
     * The state line, the run log and the result were written to the step
     * whichever prompt-test was open, so a prompt-test that was opened or
     * created while a job ran - a new one included - showed the log and the
     * result of an optimization of another one (issue #47). The job goes on
     * and is polled all the same; another prompt-test only sees that an
     * optimization is running, which is why it cannot start one.
     */
    function optimizeJobIsShown(): boolean {
      return optimizeSourceModelId !== '' && promptBuilderPromptSelectorDropdown.value === optimizeSourceModelId;
    }

    /** Paint the current elapsed runtime (no-op once frozen). */
    function updateOptimizeRuntimeDisplay(): void {
      if (!optimizeUI || optimizeJobStartedAt === 0 || !optimizeJobIsShown()) return;
      optimizeUI.logRuntime.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeRuntime} ${formatOptimizeRuntime(Date.now() - optimizeJobStartedAt)}`;
    }

    /** The milestones of the job in the run log, or why there are none yet. */
    function paintOptimizeRunLog(finished: boolean): void {
      if (!optimizeUI || !optimizeJobIsShown()) return;
      if (optimizeJobMessages.length > 0) {
        optimizeUI.logList.innerText = optimizeJobMessages.join('\n');
        delete optimizeUI.logList.dataset.pending;
      } else if (!finished && optimizeJobLiveStatus && optimizeJobLiveStatus !== 'ok') {
        // No milestones yet: say WHY instead of showing an empty log.
        // 'no-milestones' just means the job has not reported one yet, any
        // other status pinpoints why live streaming is unavailable (the full
        // log always appears when the run finishes).
        optimizeUI.logList.innerText =
          optimizeJobLiveStatus === 'no-milestones' || optimizeJobLiveStatus === 'no-session-refs'
            ? `${promptBuilderInterfaceText?.promptBuilderOptimizeLogWaiting}`
            : `${promptBuilderInterfaceText?.promptBuilderOptimizeLogUnavailable}`.replace('{status}', optimizeJobLiveStatus);
        optimizeUI.logList.dataset.pending = '1';
      }
    }

    /**
     * Draw what the running job last reported - for the prompt-test it runs
     * for. Called every second while a job is monitored and whenever the
     * Optimize step was emptied, so that prompt-test shows its job again as
     * soon as it is opened again.
     */
    function paintOptimizeJob(): void {
      if (!optimizeUI || !optimizeJobActive || optimizeJobId === '' || !optimizeJobIsShown()) return;
      optimizeUI.logDetails.classList.remove('d-none');
      optimizeUI.statusLine.innerText = optimizeJobStatus;
      updateOptimizeRuntimeDisplay();
      paintOptimizeRunLog(false);
    }

    /** Tick the runtime every second in the browser (smoother than the 5s job polls). */
    function startOptimizeRuntimeTicker(): void {
      stopOptimizeRuntimeTicker();
      optimizeRuntimeHandle = window.setInterval(paintOptimizeJob, 1000);
    }

    function stopOptimizeRuntimeTicker(): void {
      if (optimizeRuntimeHandle !== null) {
        window.clearInterval(optimizeRuntimeHandle);
        optimizeRuntimeHandle = null;
      }
    }

    // CAS dataset picker: cascading server → caslib → table dropdowns over
    // the CAS Management listings (the interactive-selection pattern of the
    // portal framework's prompt controls). Listings are cached per
    // server/caslib; the server list loads at most once, lazily, the first
    // time the CAS source is revealed.
    const casPickerCache = { caslibs: new Map<string, string[]>(), tables: new Map<string, string[]>() };
    let casServersPromise: Promise<void> | null = null;
    // Counts the times the pickers were emptied for another prompt-test: a
    // server list that was asked for before that is not filled in after it.
    let casPickerGeneration = 0;

    /** Rebuild a picker: placeholder + entries, keeping a still-valid selection. */
    function fillCasSelect(select: HTMLSelectElement, placeholderText: unknown, names: string[]): void {
      const previous = select.value;
      select.innerHTML = '';
      const placeholder = document.createElement('option');
      placeholder.value = '';
      placeholder.innerText = `${placeholderText}`;
      placeholder.disabled = true;
      placeholder.selected = true;
      select.appendChild(placeholder);
      names.forEach((name) => {
        const option = document.createElement('option');
        option.value = name;
        option.innerText = name;
        select.appendChild(option);
      });
      if (previous !== '' && names.includes(previous)) select.value = previous;
    }

    /** Load the server list once; preselect the usual single server and cascade. */
    function loadCasServerOptions(): Promise<void> {
      if (!casServersPromise) {
        casServersPromise = (async () => {
          if (!optimizeUI) return;
          const generation = casPickerGeneration;
          try {
            const servers = await getCasServers();
            if (generation !== casPickerGeneration) return;
            fillCasSelect(
              optimizeUI.casServerSelect,
              promptBuilderInterfaceText?.promptBuilderOptimizeCasServerPlaceholder,
              servers
            );
            const preferred = servers.includes('cas-shared-default') ? 'cas-shared-default' : servers[0];
            if (preferred) {
              optimizeUI.casServerSelect.value = preferred;
              await loadCaslibOptions();
            }
          } catch (error) {
            // Retry on the next reveal; the launch-time validation still guards.
            casServersPromise = null;
            console.debug('Prompt Builder: CAS server listing failed', error);
          }
        })();
      }
      return casServersPromise;
    }

    async function loadCaslibOptions(): Promise<void> {
      if (!optimizeUI) return;
      const server = optimizeUI.casServerSelect.value;
      if (server === '') return;
      try {
        let caslibs = casPickerCache.caslibs.get(server);
        if (!caslibs) {
          caslibs = await getCaslibs(server);
          casPickerCache.caslibs.set(server, caslibs);
        }
        fillCasSelect(optimizeUI.casLibSelect, promptBuilderInterfaceText?.promptBuilderOptimizeCasLibPlaceholder, caslibs);
        fillCasSelect(optimizeUI.casTableSelect, promptBuilderInterfaceText?.promptBuilderOptimizeCasTablePlaceholder, []);
        if (optimizeUI.casLibSelect.value !== '') await loadCasTableOptions();
      } catch (error) {
        console.debug('Prompt Builder: caslib listing failed', error);
      }
    }

    async function loadCasTableOptions(): Promise<void> {
      if (!optimizeUI) return;
      const server = optimizeUI.casServerSelect.value;
      const caslib = optimizeUI.casLibSelect.value;
      if (server === '' || caslib === '') return;
      const cacheKey = `${server}/${caslib}`;
      try {
        let tables = casPickerCache.tables.get(cacheKey);
        if (!tables) {
          tables = await getCasTables(server, caslib);
          casPickerCache.tables.set(cacheKey, tables);
        }
        fillCasSelect(optimizeUI.casTableSelect, promptBuilderInterfaceText?.promptBuilderOptimizeCasTablePlaceholder, tables);
      } catch (error) {
        console.debug('Prompt Builder: CAS table listing failed', error);
      }
    }

    /**
     * Refresh the collapsible run log with the job's own milestones (the
     * SAS.logMessage notes, prefix already stripped by extractProgressMessages
     * — never the raw SAS log). `finished` paints the final runtime and
     * freezes it.
     */
    function updateOptimizeRunLog(messages: string[], finished: boolean, liveStatus?: string): void {
      if (!optimizeUI) return;
      updateOptimizeRuntimeDisplay();
      if (finished) {
        stopOptimizeRuntimeTicker();
        optimizeJobStartedAt = 0;
      }
      // Milestones that were reported once stay: a poll can fail transiently
      // and come back without any.
      if (messages.length > 0) optimizeJobMessages = messages;
      optimizeJobLiveStatus = liveStatus;
      paintOptimizeRunLog(finished);
    }

    /** Runs that qualify as optimization examples: those with a Best Response. */
    function countOptimizeSamples(): number {
      return promptExperimentTracker.filter((trackerEntry) =>
        Object.keys(trackerEntry).some(
          (key) => !TRACKER_META_KEYS.includes(key) && (trackerEntry[key] as ModelExperimentData)?.best_prompt
        )
      ).length;
    }

    /** The configured minimum sample count (Option `minOptimizeSamples`). */
    function optimizeMinSamples(): number {
      const configured = Number(promptBuilderObject?.minOptimizeSamples ?? '');
      return Number.isFinite(configured) && configured > 0 ? Math.floor(configured) : 30;
    }

    /** Refresh the optimize panel's gating, sample count and estimate. */
    function updateOptimizeState(): void {
      if (!optimizeUI) return;
      const casSource = optimizeUI.datasetCasRadio.checked;
      // The tracker-specific pieces (assumed-correct notice, sample count and
      // the minimum-samples gate) only apply to the tracker source; a CAS
      // table is validated against its real columns/rows at launch instead.
      optimizeUI.casRow.classList.toggle('d-none', !casSource);
      optimizeUI.datasetTrackerInfo.classList.toggle('d-none', casSource);
      optimizeUI.samplesHint.classList.toggle('d-none', casSource);
      if (casSource) void loadCasServerOptions();
      // The estimate must reflect the SELECTED dataset source: the CAS row
      // count (fetched when the table is picked; null while unknown) rather
      // than the tracker count when the CAS source is active.
      const samples = casSource ? (optimizeCasRowCount ?? 0) : countOptimizeSamples();
      const minSamples = optimizeMinSamples();
      const sampleParts = [
        `${samples} ${promptBuilderInterfaceText?.promptBuilderOptimizeSamplesLabel}`,
      ];
      if (samples < minSamples) {
        sampleParts.push(
          `${promptBuilderInterfaceText?.promptBuilderOptimizeSamplesBelowMin}`.replace('{min}', String(minSamples))
        );
      } else if (samples < 50) {
        sampleParts.push(`${promptBuilderInterfaceText?.promptBuilderOptimizeSamplesLowWarning}`);
      }
      optimizeUI.samplesHint.innerText = sampleParts.join(' — ');

      // Rough call estimate: baseline + after evaluation over the dataset plus
      // the optimizer's own passes (MIPROv2 additionally proposes and trials
      // candidate instructions; GEPA adds reflection rounds on top of its
      // candidate evaluations) — kept deliberately coarse. The GEPA factor is
      // calibrated against a live light-budget run: 10 examples cost ~810
      // calls (751 target + 59 reflection) over ~30 minutes.
      const selectedOptimizer = optimizeUI.optimizerSelect.value;
      const estimatedCalls =
        selectedOptimizer === 'gepa'
          ? samples * 75 + 60
          : selectedOptimizer === 'miprov2'
            ? samples * 8 + 30
            : samples * 3 + 10;
      const estimateParts = [
        `${promptBuilderInterfaceText?.promptBuilderOptimizeEstimate}`.replace(
          '{calls}',
          casSource && optimizeCasRowCount === null ? '?' : String(estimatedCalls)
        ),
      ];
      if (
        optimizeUI.metricSelect.value === 'judge' &&
        optimizeUI.judgeSelect.value !== '' &&
        optimizeUI.judgeSelect.value === optimizeUI.targetSelect.value
      ) {
        estimateParts.push(`${promptBuilderInterfaceText?.promptBuilderOptimizeJudgeSelfWarning}`);
      }
      optimizeUI.estimateLine.innerText = estimateParts.join(' ');
      // The judge model picker also serves GEPA, whose reflection step runs on
      // the judge model regardless of the chosen metric; GEPA selects no
      // few-shot demos, so the max-demos input is moot for it.
      optimizeUI.judgeRow.classList.toggle(
        'd-none',
        optimizeUI.metricSelect.value !== 'judge' && selectedOptimizer !== 'gepa'
      );
      optimizeUI.maxDemosInput.disabled = selectedOptimizer === 'gepa';

      const promptSelected =
        promptBuilderPromptSelectorDropdown.value !== '' &&
        promptBuilderPromptSelectorDropdown.value !== `${promptBuilderInterfaceText?.promptSelect}`;
      const configReady = Boolean(promptBuilderObject?.computeContext && promptBuilderObject?.optimizeJobProgram);
      // The job that runs is the business of the prompt-test it runs for:
      // there the button turns and the state line and the log say the rest.
      // Any other prompt-test has a plain button that is switched off, and
      // the banner says why.
      const jobShown = optimizeJobActive && optimizeJobIsShown();
      if (jobShown !== (optimizeUI.runButton.dataset.running === '1')) {
        if (jobShown) {
          optimizeUI.runButton.dataset.running = '1';
          optimizeUI.runButton.innerHTML = `<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> ${promptBuilderInterfaceText?.promptBuilderOptimizeRunButtonStatus}`;
        } else {
          delete optimizeUI.runButton.dataset.running;
          optimizeUI.runButton.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeRunButton}`;
        }
      }
      let disabledHint = '';
      if (optimizeJobActive) disabledHint = `${promptBuilderInterfaceText?.promptBuilderOptimizeAlreadyRunning}`;
      else if (!configReady) disabledHint = `${promptBuilderInterfaceText?.promptBuilderOptimizeNotConfigured}`;
      else if (!promptSelected) disabledHint = `${promptBuilderInterfaceText?.promptBuilderOptimizeNoPrompt}`;
      else if (!casSource && samples < minSamples)
        disabledHint = `${promptBuilderInterfaceText?.promptBuilderOptimizeSamplesBelowMin}`.replace(
          '{min}',
          String(minSamples)
        );
      else if (selectedOptimizer === 'gepa' && optimizeUI.judgeSelect.value === '')
        disabledHint = `${promptBuilderInterfaceText?.promptBuilderOptimizeGepaNeedsJudge}`;
      // Off as well while a write of the prompt-test is out (see createSelectionLock).
      setDisabledHint(optimizeUI.runButton, optimizeUI.runWrapper, disabledHint !== '' || selectionLock.busy, disabledHint);
      // Say why the run is blocked in the open, not only on hover.
      const showBanner = disabledHint !== '' && !jobShown;
      optimizeUI.banner.classList.toggle('d-none', !showBanner);
      optimizeUI.banner.innerText = showBanner ? disabledHint : '';
      // The compare entry point only needs a prompt and a quiet job slot —
      // its own modal handles candidate/sample validation.
      optimizeUI.compareButton.disabled = optimizeJobActive || !configReady || !promptSelected || selectionLock.busy;
      // Follow the selection: render the selected prompt's optimization
      // history (no-op while the selection is unchanged).
      void refreshOptimizationHistory();
    }

    /**
     * The Optimize step without what the prompt-test that was open left in
     * it. The fields are put back by startLaterStepsAfresh; this is the rest.
     */
    function clearOptimizeRun(): void {
      if (!optimizeUI) return;
      // The CAS pickers were put back to "choose one" with the other fields;
      // the servers, caslibs and tables they listed go as well. The next time
      // the CAS source is chosen the servers are asked for again, which also
      // chooses the usual one and lists its caslibs - as on a page that was
      // just loaded.
      casPickerGeneration += 1;
      casServersPromise = null;
      optimizeCasRowCount = null;
      fillCasSelect(optimizeUI.casServerSelect, promptBuilderInterfaceText?.promptBuilderOptimizeCasServerPlaceholder, []);
      fillCasSelect(optimizeUI.casLibSelect, promptBuilderInterfaceText?.promptBuilderOptimizeCasLibPlaceholder, []);
      fillCasSelect(optimizeUI.casTableSelect, promptBuilderInterfaceText?.promptBuilderOptimizeCasTablePlaceholder, []);
      updateOptimizeState();
      optimizeUI.statusLine.innerText = '';
      optimizeUI.resultBox.innerHTML = '';
      optimizeUI.logDetails.classList.add('d-none');
      optimizeUI.logDetails.open = false;
      optimizeUI.logRuntime.innerText = '';
      optimizeUI.logList.innerText = '';
      delete optimizeUI.logList.dataset.pending;
      // A job that is still running goes on, and is drawn again here when
      // the prompt-test that was opened is the one it runs for.
      paintOptimizeJob();
    }

    /** The state line of the job: kept, and written where the job is shown. */
    function setOptimizeJobStatus(text: string): void {
      optimizeJobStatus = text;
      if (optimizeUI && optimizeJobIsShown()) optimizeUI.statusLine.innerText = text;
    }

    /** What became of the job, in the result box - where the job is shown. */
    function showOptimizeOutcome(text: string): void {
      if (optimizeUI && optimizeJobIsShown()) optimizeUI.resultBox.innerText = text;
    }

    /** Stop polling the optimization job (finished, failed, or abandoned). */
    function stopOptimizePolling(): void {
      if (optimizePollHandle !== null) {
        window.clearInterval(optimizePollHandle);
        optimizePollHandle = null;
      }
    }

    /** Reset the run button back from its spinner state. */
    function resetOptimizeRunButton(): void {
      if (!optimizeUI) return;
      optimizeJobActive = false;
      // Safety net: never leave the runtime ticking (covers launch failures,
      // where updateOptimizeRunLog(…, true) is not reached).
      stopOptimizeRuntimeTicker();
      // The label of the button follows the job there.
      updateOptimizeState();
    }

    /** One poll tick: refresh state + latest milestone; act on terminal states. */
    async function pollOptimizeJob(): Promise<void> {
      if (!optimizeUI || optimizeJobId === '') return;
      let job: JobExecutionJob;
      try {
        job = await getJob(optimizeJobId);
      } catch {
        // Transient poll failure — keep polling.
        return;
      }
      const progress = await getJobProgressMessages(job);
      const progressMessages = progress.messages;
      updateOptimizeRunLog(progressMessages, false, progress.liveStatus);
      const latestMilestone = progressMessages.length > 0 ? ` — ${progressMessages[progressMessages.length - 1]}` : '';
      setOptimizeJobStatus(`${promptBuilderInterfaceText?.promptBuilderOptimizeJobState} ${job.state}${latestMilestone}`);
      if (!isTerminalJobState(job.state)) {
        // The job records its outcome in the optimization tracker BEFORE Job
        // Execution's state turns terminal — and a hard-killed compute session
        // can leave the state 'running' indefinitely (seen live). When this
        // job's entry exists, finish from it instead of polling forever.
        const earlyEntry = await readOptimizationEntry(true);
        // Not found, or the tracker could not be read: the next poll asks again.
        if (!earlyEntry || typeof earlyEntry === 'string') return;
        stopOptimizePolling();
        // Clear the (possibly stale) state line — the run is over; the result
        // box and the run log carry the outcome from here.
        setOptimizeJobStatus('');
        updateOptimizeRunLog(progressMessages, true);
        if (earlyEntry.status === 'succeeded') {
          await showOptimizeResult();
        } else {
          const earlyError = earlyEntry.error ? ` ${earlyEntry.error}` : '';
          showOptimizeOutcome(`${promptBuilderInterfaceText?.promptBuilderOptimizeJobFailed}${earlyError}`);
          showToast(`${promptBuilderInterfaceText?.promptBuilderOptimizeJobFailed}`);
          void refreshOptimizationHistory(true);
        }
        resetOptimizeRunButton();
        return;
      }
      stopOptimizePolling();
      // The run is over: replace the transient state line with nothing — the
      // outcome lives in the result box; runtime + milestones in the run log.
      setOptimizeJobStatus('');
      updateOptimizeRunLog(progressMessages, true);
      if (job.state === 'completed' || job.state === 'completedWithWarnings') {
        await showOptimizeResult();
      } else {
        // The job records its failure reason in the optimization tracker
        // (e.g. "the compute context lacks the dspy package") — prefer that
        // over Job Execution's generic error message.
        const read = await readOptimizationEntry();
        const failedEntry = typeof read === 'string' ? null : read;
        const failureDetail = failedEntry?.error
          ? ` ${failedEntry.error}`
          : job.error?.message
            ? ` ${job.error.message}`
            : '';
        showOptimizeOutcome(`${promptBuilderInterfaceText?.promptBuilderOptimizeJobFailed}${failureDetail}`);
        showToast(`${promptBuilderInterfaceText?.promptBuilderOptimizeJobFailed}`);
        void refreshOptimizationHistory(true);
      }
      resetOptimizeRunButton();
    }

    /**
     * Read a model's job-written optimization tracker. An empty list when the
     * model holds no tracker; 'unread' when the contents or the tracker could
     * not be read, 'unusable' when the tracker was read and holds no runs this
     * page can read (see readStoredJson) - which reading it again does not
     * change.
     */
    async function readOptimizationEntries(modelId: string): Promise<OptimizationTrackerEntry[] | 'unread' | 'unusable'> {
      try {
        const sourceContents = await getModelContents(modelId);
        const trackerContent = sourceContents.find(
          (modelContent) => modelContent.name === 'Prompt-Optimization-Tracker.json'
        );
        if (!trackerContent) return [];
        if (!trackerContent.fileUri) return 'unread';
        return await readStoredJson<OptimizationTrackerEntry[]>(String(trackerContent.fileUri), 'Prompt-Optimization-Tracker.json');
      } catch (error) {
        return error instanceof StoredFileUnusable ? 'unusable' : 'unread';
      }
    }

    /**
     * The launched job's entry from the source prompt's optimization tracker,
     * matched by job id. With `requireJobIdMatch` only an exact match counts
     * (used while the job still reports running); otherwise the newest entry
     * is the fallback. 'unread' or 'unusable' when the tracker could not be
     * read (see readOptimizationEntries).
     */
    async function readOptimizationEntry(requireJobIdMatch = false): Promise<OptimizationTrackerEntry | null | 'unread' | 'unusable'> {
      const trackerEntries = await readOptimizationEntries(optimizeSourceModelId);
      if (!Array.isArray(trackerEntries)) return trackerEntries;
      const matched = trackerEntries.find((candidate) => candidate.jobId === optimizeJobId) ?? null;
      if (requireJobIdMatch) return matched;
      return matched ?? trackerEntries[trackerEntries.length - 1] ?? null;
    }

    /** The model whose optimization history is currently rendered. */
    let optimizeHistoryModelId = '';

    /**
     * (Re)load the optimization history for the currently selected prompt and
     * render it under the Optimize section. Cheap no-op when the selection has
     * not changed (unless `force`, used after a run finishes).
     */
    async function refreshOptimizationHistory(force = false): Promise<void> {
      if (!optimizeUI) return;
      const modelId = promptBuilderPromptSelectorDropdown.value;
      const promptSelected =
        modelId !== '' && modelId !== `${promptBuilderInterfaceText?.promptSelect}`;
      if (!promptSelected) {
        optimizeUI.historyContainer.innerHTML = '';
        optimizeHistoryModelId = '';
        return;
      }
      if (!force && modelId === optimizeHistoryModelId) return;
      optimizeHistoryModelId = modelId;
      const trackerEntries = await readOptimizationEntries(modelId);
      // The selection may have moved on while the tracker was being fetched.
      if (promptBuilderPromptSelectorDropdown.value !== modelId) return;
      // A tracker that could not be read is no history without runs: the
      // place of the history says so.
      if (!Array.isArray(trackerEntries)) {
        const line = document.createElement('p');
        line.classList.add('text-danger', 'mt-3', 'pb-optimize-history-unread');
        line.innerText = `${trackerEntries === 'unusable' ? promptBuilderInterfaceText?.promptBuilderOptimizeHistoryUnusable : promptBuilderInterfaceText?.promptBuilderOptimizeHistoryUnread}`;
        optimizeUI.historyContainer.replaceChildren(line);
        return;
      }
      // Cost estimates in the rows need the price attributes of the models
      // the runs used (fetched lazily) — best effort before rendering.
      const usedModels = new Set<string>();
      trackerEntries.forEach((entry) => {
        if (entry.targetModel && entry.type !== 'comparison') usedModels.add(entry.targetModel);
        if (entry.judgeModel) usedModels.add(entry.judgeModel);
        (entry.targets ?? []).forEach((targetRow) => {
          if (targetRow.model) usedModels.add(targetRow.model);
        });
      });
      try {
        await Promise.all([...usedModels].map((name) => ensureLLMCostAttributes(name)));
      } catch {
        /* rows render without cost estimates */
      }
      if (promptBuilderPromptSelectorDropdown.value !== modelId) return;
      renderOptimizationHistory(trackerEntries);
    }

    /** Format a metric value for the history display. */
    function formatOptimizeMetric(value: number | null | undefined): string {
      return value === null || value === undefined || !Number.isFinite(Number(value))
        ? '—'
        : String(Math.round(Number(value) * 1000) / 1000);
    }

    /**
     * Rank a comparison's candidate rows the way MMDSPy ranks endpoints:
     * best quality first, lower latency breaking ties — with unreachable
     * candidates sorted to the bottom.
     */
    function rankComparisonTargets(targetRows: ComparisonTargetRow[]): ComparisonTargetRow[] {
      return targetRows.slice().sort((a, b) => {
        const aScored = a.status === 'scored' ? 0 : 1;
        const bScored = b.status === 'scored' ? 0 : 1;
        if (aScored !== bScored) return aScored - bScored;
        const qualityDelta = (Number(b.quality) || 0) - (Number(a.quality) || 0);
        if (qualityDelta !== 0) return qualityDelta;
        return (Number(a.avgLatency) || 0) - (Number(b.avgLatency) || 0);
      });
    }

    /** Render the optimization runs (newest first) under the Optimize panel. */
    function renderOptimizationHistory(trackerEntries: OptimizationTrackerEntry[]): void {
      if (!optimizeUI) return;
      const container = optimizeUI.historyContainer;
      container.innerHTML = '';
      if (trackerEntries.length === 0) return;
      const heading = document.createElement('p');
      heading.classList.add('fw-bold', 'mb-1', 'mt-3');
      heading.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeHistoryHeading}`;
      container.appendChild(heading);
      trackerEntries
        .slice()
        .reverse()
        .forEach((entry) => container.appendChild(buildOptimizationHistoryRow(entry)));
    }

    /** One collapsible history row: summary line + evolution details. */
    function buildOptimizationHistoryRow(entry: OptimizationTrackerEntry): HTMLElement {
      const row = document.createElement('details');
      row.classList.add('pb-optimize-history-entry', 'border', 'rounded', 'p-2', 'mb-2');
      const succeeded = entry.status === 'succeeded';

      const summary = document.createElement('summary');
      const statusBadge = document.createElement('span');
      statusBadge.classList.add('badge', succeeded ? 'bg-success' : 'bg-danger', 'me-2');
      statusBadge.innerText = succeeded
        ? `${promptBuilderInterfaceText?.promptBuilderOptimizeStatusSucceeded}`
        : `${promptBuilderInterfaceText?.promptBuilderOptimizeStatusFailed}`;
      summary.appendChild(statusBadge);
      const finished = entry.finishedAt ? new Date(entry.finishedAt) : null;
      const isComparison = entry.type === 'comparison';
      const summaryParts = [
        `#${entry.optimizationId ?? '?'}`,
        finished && !Number.isNaN(finished.getTime()) ? finished.toLocaleString() : '',
        entry.targetModel ?? '',
        (isComparison
          ? // A screening has no optimizer; a sweep names the one it ran.
            [`${promptBuilderInterfaceText?.promptBuilderOptimizeCompareLabel}`, entry.optimizer, entry.metric]
          : [entry.optimizer, entry.metric]
        )
          .filter(Boolean)
          .join(' · '),
        entry.sampleCount != null
          ? `${entry.sampleCount} ${promptBuilderInterfaceText?.promptBuilderOptimizeHistoryExamples}`
          : '',
      ].filter((part) => part !== '');
      summary.appendChild(document.createTextNode(summaryParts.join('  |  ') + '  '));
      if (succeeded && isComparison) {
        // A comparison has no before/after — its headline is the winner.
        const winner = rankComparisonTargets(entry.targets ?? [])[0];
        if (winner?.model && winner.status === 'scored') {
          const winnerSpan = document.createElement('span');
          winnerSpan.classList.add('fw-bold');
          winnerSpan.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeCompareBest} ${winner.model} ${formatOptimizeMetric(winner.quality)}`;
          summary.appendChild(winnerSpan);
        }
      } else if (succeeded) {
        const metricSpan = document.createElement('span');
        metricSpan.classList.add('fw-bold');
        metricSpan.innerText = `${formatOptimizeMetric(entry.metricBefore)} → ${formatOptimizeMetric(entry.metricAfter)}`;
        summary.appendChild(metricSpan);
        const before = Number(entry.metricBefore);
        const after = Number(entry.metricAfter);
        if (Number.isFinite(before) && Number.isFinite(after) && after !== before) {
          const delta = document.createElement('span');
          delta.classList.add('badge', after > before ? 'bg-success' : 'bg-danger', 'ms-2');
          delta.innerText = `${after > before ? '+' : ''}${formatOptimizeMetric(after - before)}`;
          summary.appendChild(delta);
        }
      }
      // Load/delete as icon buttons in the summary line — the same icons the
      // experiment-run headers use. Clicks inside <summary> toggle the
      // details, so both stop the event.
      const summaryActions = document.createElement('span');
      summaryActions.classList.add('ms-2', 'd-inline-flex', 'gap-1');
      if (succeeded && entry.optimizedPrompt) {
        const loadButton = document.createElement('button');
        loadButton.type = 'button';
        loadButton.classList.add('btn', 'btn-outline-primary', 'btn-sm', 'pb-optimize-history-load');
        loadButton.title = `${promptBuilderInterfaceText?.promptBuilderOptimizeHistoryLoadButton}`;
        loadButton.setAttribute('aria-label', `${promptBuilderInterfaceText?.promptBuilderOptimizeHistoryLoadButton} #${entry.optimizationId ?? ''}`);
        loadButton.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" height="20px" viewBox="0 -960 960 960" width="20px" fill="currentColor"><title>${promptBuilderInterfaceText?.promptBuilderOptimizeHistoryLoadButton}</title><path d="${TRACKER_ICON_PATHS.load}"/></svg>`;
        loadButton.onclick = (event) => {
          event.preventDefault();
          event.stopPropagation();
          loadOptimizationAsExperiment(entry);
        };
        summaryActions.appendChild(loadButton);
      }
      const deleteButton = document.createElement('button');
      deleteButton.type = 'button';
      deleteButton.classList.add('btn', 'btn-outline-danger', 'btn-sm', 'pb-optimize-history-delete');
      deleteButton.title = `${promptBuilderInterfaceText?.promptBuilderOptimizeHistoryDeleteButton}`;
      deleteButton.setAttribute('aria-label', `${promptBuilderInterfaceText?.promptBuilderOptimizeHistoryDeleteButton} #${entry.optimizationId ?? ''}`);
      deleteButton.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" height="20px" viewBox="0 -960 960 960" width="20px" fill="currentColor"><title>${promptBuilderInterfaceText?.promptBuilderOptimizeHistoryDeleteButton}</title><path d="${TRACKER_ICON_PATHS.remove}"/></svg>`;
      deleteButton.onclick = (event) => {
        event.preventDefault();
        event.stopPropagation();
        void deleteOptimizationRun(entry);
      };
      // Off while a write of the prompt-test is out (see createSelectionLock).
      deleteButton.disabled = selectionLock.busy;
      summaryActions.appendChild(deleteButton);
      summary.appendChild(summaryActions);
      row.appendChild(summary);

      const body = document.createElement('div');
      body.classList.add('mt-2');
      if (!succeeded && entry.error) {
        const errorLine = document.createElement('p');
        errorLine.classList.add('text-danger');
        errorLine.innerText = entry.error;
        body.appendChild(errorLine);
      }
      // Legacy entries (earlier releases created a separate prompt-test).
      if (entry.producedPromptModelId) {
        const legacyLine = document.createElement('p');
        legacyLine.classList.add('mb-1');
        const legacyAnchor = document.createElement('a');
        legacyAnchor.target = '_blank';
        legacyAnchor.rel = 'noopener noreferrer';
        legacyAnchor.href = `${VIYA}/SASModelManager/models/${entry.producedPromptModelId}`;
        legacyAnchor.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeOpenProduced}`;
        legacyAnchor.onclick = (event) => openModelManagerLink(event, legacyAnchor, promptBuilderInterfaceText);
        legacyLine.appendChild(legacyAnchor);
        body.appendChild(legacyLine);
      }

      const addPromptBlock = (label: unknown, text: string | undefined | null): void => {
        if (!text) return;
        const blockLabel = document.createElement('p');
        blockLabel.classList.add('fw-bold', 'mb-1', 'mt-2');
        blockLabel.innerText = `${label}`;
        const block = document.createElement('pre');
        block.classList.add('pb-optimize-prompt-block', 'border', 'rounded', 'p-2', 'mb-1');
        block.style.whiteSpace = 'pre-wrap';
        block.innerText = text;
        body.appendChild(blockLabel);
        body.appendChild(block);
      };
      // The job skips the search when the baseline already scores perfectly
      // on the validation split (no gradient to climb) — explain that here.
      if (entry.skippedReason === 'baseline-perfect') {
        const skippedNote = document.createElement('p');
        skippedNote.classList.add('alert', 'alert-info', 'py-1', 'px-2', 'mb-1', 'mt-2', 'pb-optimize-skipped');
        skippedNote.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeHistorySkippedPerfect}`;
        body.appendChild(skippedNote);
      }
      addPromptBlock(
        promptBuilderInterfaceText?.promptBuilderOptimizeHistoryBaselineLabel,
        entry.baselinePrompt?.systemPrompt
      );
      addPromptBlock(
        promptBuilderInterfaceText?.promptBuilderOptimizeHistoryOptimizedLabel,
        entry.optimizedPrompt?.systemPrompt
      );
      addPromptBlock(
        promptBuilderInterfaceText?.promptBuilderOptimizeHistoryUserPromptLabel,
        entry.optimizedPrompt?.userPrompt
      );

      // What the run actually spent: per-role calls + token totals from the
      // tracker entry, priced with the same per-token/per-second attributes
      // the run table uses (blank when a model carries no prices).
      if (entry.usage) {
        // For a comparison the per-target spend lives in the ranked table
        // rows; only the judge's extra calls are worth a summary line.
        const roles: Array<[string | null | undefined, OptimizationUsage | null | undefined]> = isComparison
          ? [[entry.judgeModel, entry.usage.judge]]
          : [
              [entry.targetModel, entry.usage.target],
              [entry.judgeModel, entry.usage.judge],
            ];
        const callParts: string[] = [];
        let promptTokens = 0;
        let outputTokens = 0;
        let totalCost = 0;
        let anyCost = false;
        roles.forEach(([modelName, roleUsage]) => {
          if (!roleUsage?.calls) return;
          callParts.push(`${roleUsage.calls} × ${modelName ?? '?'}`);
          promptTokens += Number(roleUsage.promptTokens) || 0;
          outputTokens += Number(roleUsage.outputTokens) || 0;
          const roleCost = computeCallCost(
            {
              prompt_length: roleUsage.promptTokens,
              output_length: roleUsage.outputTokens,
              run_time: roleUsage.runTime,
            },
            modelName ? llmAttributesByName.get(modelName) : undefined
          );
          if (roleCost !== null) {
            totalCost += roleCost;
            anyCost = true;
          }
        });
        if (callParts.length > 0) {
          const usageLine = document.createElement('p');
          usageLine.classList.add('text-muted', 'mb-1', 'mt-2', 'pb-optimize-usage');
          let usageText = `${promptBuilderInterfaceText?.promptBuilderOptimizeHistoryUsage}`
            .replace('{calls}', callParts.join(' + '))
            .replace('{prompt}', String(Math.round(promptTokens)))
            .replace('{output}', String(Math.round(outputTokens)));
          if (anyCost) {
            usageText += ` — ${promptBuilderInterfaceText?.promptBuilderOptimizeHistoryCost} ${formatCost(totalCost)}`;
          }
          usageLine.innerText = usageText;
          body.appendChild(usageLine);
        }
      }

      // Ranked candidate table of a compare-targets run: quality first,
      // latency as the tiebreak (MMDSPy's ordering) plus the cost dimension,
      // with an "optimize this target" handoff into the single-target flow.
      const rankedTargets = isComparison ? rankComparisonTargets(entry.targets ?? []) : [];
      if (rankedTargets.length > 0) {
        const compareTable = document.createElement('table');
        compareTable.classList.add('table', 'table-sm', 'pb-optimize-compare-table', 'mt-2');
        const compareHead = document.createElement('thead');
        const compareHeadRow = document.createElement('tr');
        [
          promptBuilderInterfaceText?.promptBuilderOptimizeCompareModelHeader,
          promptBuilderInterfaceText?.promptBuilderOptimizeCompareQualityHeader,
          promptBuilderInterfaceText?.promptBuilderOptimizeCompareLatencyHeader,
          promptBuilderInterfaceText?.promptBuilderOptimizeCompareCallsHeader,
          promptBuilderInterfaceText?.promptBuilderOptimizeCompareCostHeader,
          '',
        ].forEach((headerText) => {
          const th = document.createElement('th');
          th.innerText = `${headerText}`;
          compareHeadRow.appendChild(th);
        });
        compareHead.appendChild(compareHeadRow);
        compareTable.appendChild(compareHead);
        const compareBody = document.createElement('tbody');
        let winnerMarked = false;
        rankedTargets.forEach((targetRow) => {
          const tr = document.createElement('tr');
          const scored = targetRow.status === 'scored';
          if (scored && !winnerMarked) {
            tr.classList.add('table-success');
            winnerMarked = true;
          }
          if (!scored) tr.classList.add('table-warning');
          const modelCell = document.createElement('td');
          modelCell.innerText = String(targetRow.model ?? '');
          const qualityCell = document.createElement('td');
          if (scored && targetRow.metricBefore != null) {
            // A sweep row shows its own before → after evolution.
            qualityCell.innerText = `${formatOptimizeMetric(targetRow.metricBefore)} → ${formatOptimizeMetric(targetRow.metricAfter)}`;
          } else if (scored) {
            qualityCell.innerText = formatOptimizeMetric(targetRow.quality);
          } else {
            const unreachableBadge = document.createElement('span');
            unreachableBadge.classList.add('badge', 'bg-warning', 'text-dark');
            unreachableBadge.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeCompareUnreachable}`;
            if (targetRow.error) unreachableBadge.title = targetRow.error;
            qualityCell.appendChild(unreachableBadge);
          }
          const latencyCell = document.createElement('td');
          latencyCell.innerText =
            scored && typeof targetRow.avgLatency === 'number' ? `${targetRow.avgLatency.toFixed(2)}s` : '—';
          const callsCell = document.createElement('td');
          callsCell.innerText = String(targetRow.usage?.calls ?? 0);
          const costCell = document.createElement('td');
          const rowCost = computeCallCost(
            {
              prompt_length: targetRow.usage?.promptTokens,
              output_length: targetRow.usage?.outputTokens,
              run_time: targetRow.usage?.runTime,
            },
            targetRow.model ? llmAttributesByName.get(targetRow.model) : undefined
          );
          costCell.innerText = rowCost !== null ? formatCost(rowCost) : '—';
          const actionCell = document.createElement('td');
          if (scored && targetRow.model && targetRow.optimizedPrompt) {
            // Sweep rows carry the candidate's optimised prompt — load it
            // into the workbench like a single-run result.
            const loadRowButton = document.createElement('button');
            loadRowButton.type = 'button';
            loadRowButton.classList.add('btn', 'btn-outline-primary', 'btn-sm', 'pb-optimize-compare-load');
            loadRowButton.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeHistoryLoadButton}`;
            loadRowButton.onclick = (event) => {
              event.preventDefault();
              event.stopPropagation();
              loadOptimizationAsExperiment({
                optimizedPrompt: targetRow.optimizedPrompt,
                targetModel: targetRow.model,
              });
            };
            actionCell.appendChild(loadRowButton);
          } else if (scored && targetRow.model) {
            const pickButton = document.createElement('button');
            pickButton.type = 'button';
            pickButton.classList.add('btn', 'btn-outline-primary', 'btn-sm', 'pb-optimize-compare-pick');
            pickButton.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeCompareOptimizeThis}`;
            pickButton.onclick = (event) => {
              event.preventDefault();
              event.stopPropagation();
              if (!optimizeUI) return;
              optimizeUI.targetSelect.value = String(targetRow.model);
              optimizeUI.targetSelect.dispatchEvent(new Event('change'));
              optimizeUI.targetSelect.scrollIntoView({ behavior: 'smooth', block: 'center' });
              showToast(`${promptBuilderInterfaceText?.promptBuilderOptimizeCompareTargetSetToast}`);
            };
            actionCell.appendChild(pickButton);
          }
          [modelCell, qualityCell, latencyCell, callsCell, costCell, actionCell].forEach((cell) =>
            tr.appendChild(cell)
          );
          compareBody.appendChild(tr);
        });
        compareTable.appendChild(compareBody);
        body.appendChild(compareTable);
      }

      const evaluations = entry.evaluations ?? [];
      if (evaluations.length > 0) {
        const evalLabel = document.createElement('p');
        evalLabel.classList.add('fw-bold', 'mb-1', 'mt-2');
        evalLabel.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeHistoryEvalLabel}`;
        body.appendChild(evalLabel);
        const table = document.createElement('table');
        table.classList.add('table', 'table-sm', 'pb-optimize-eval-table');
        const thead = document.createElement('thead');
        const headRow = document.createElement('tr');
        [
          promptBuilderInterfaceText?.promptBuilderOptimizeHistoryInputHeader,
          promptBuilderInterfaceText?.promptBuilderOptimizeHistoryExpectedHeader,
          promptBuilderInterfaceText?.promptBuilderOptimizeHistoryBeforeHeader,
          promptBuilderInterfaceText?.promptBuilderOptimizeHistoryAfterHeader,
        ].forEach((headerText) => {
          const th = document.createElement('th');
          th.innerText = `${headerText}`;
          headRow.appendChild(th);
        });
        thead.appendChild(headRow);
        table.appendChild(thead);
        const tbody = document.createElement('tbody');
        evaluations.forEach((evaluation) => {
          const tr = document.createElement('tr');
          // Highlight the evolution: green where the optimisation fixed an
          // example, red where it broke one.
          if (!evaluation.baselineCorrect && evaluation.optimizedCorrect) tr.classList.add('table-success');
          if (evaluation.baselineCorrect && !evaluation.optimizedCorrect) tr.classList.add('table-danger');
          const inputsCell = document.createElement('td');
          inputsCell.innerText = Object.entries(evaluation.inputs ?? {})
            .map(([key, value]) => `${key}: ${value}`)
            .join(', ');
          const expectedCell = document.createElement('td');
          expectedCell.innerText = String(evaluation.expected ?? '');
          // Partial-credit metrics (token overlap) also carry a per-example
          // score; show it next to the âœ“/âœ— when it is not simply 0 or 1.
          const evalMark = (correct: boolean | undefined, score: number | undefined): string => {
            const mark = correct ? 'âœ“' : 'âœ—';
            return typeof score === 'number' && score > 0 && score < 1 ? `${mark} ${score.toFixed(2)}` : mark;
          };
          const beforeCell = document.createElement('td');
          beforeCell.innerText = `${evalMark(evaluation.baselineCorrect, evaluation.baselineScore)} ${String(evaluation.baselineResponse ?? '')}`;
          const afterCell = document.createElement('td');
          afterCell.innerText = `${evalMark(evaluation.optimizedCorrect, evaluation.optimizedScore)} ${String(evaluation.optimizedResponse ?? '')}`;
          [inputsCell, expectedCell, beforeCell, afterCell].forEach((cell) => tr.appendChild(cell));
          tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        body.appendChild(table);
      }
      row.appendChild(body);
      return row;
    }

    /**
     * Delete one optimization run from the prompt's tracker (plus its dataset
     * snapshot), after confirmation. The tracker is normally job-written; the
     * delete is guarded against a running optimization so the job's
     * read-append-rewrite cannot race with it. A model version is created
     * first, like the experiment save flow, so the audit trail survives.
     */
    async function deleteOptimizationRun(entry: OptimizationTrackerEntry): Promise<void> {
      if (optimizeJobActive) {
        showToast(`${promptBuilderInterfaceText?.promptBuilderOptimizeAlreadyRunning}`);
        return;
      }
      const modelId = promptBuilderPromptSelectorDropdown.value;
      const confirmed = await showConfirmModal({
        title: `${promptBuilderInterfaceText?.promptBuilderOptimizeHistoryDeleteButton} #${entry.optimizationId ?? ''}`,
        body: [`${promptBuilderInterfaceText?.promptBuilderOptimizeHistoryDeleteConfirm}`],
        confirmText: `${promptBuilderInterfaceText?.promptBuilderDeleteConfirmButton}`,
        cancelText: `${promptBuilderInterfaceText?.promptBuilderDeleteCancelButton}`,
      });
      if (!confirmed) return;
      await selectionLock.during(async () => {
        // Each write is read (see written). A write SAS Model Manager refuses
        // ends the delete with the toast of a deletion that failed; one that
        // gets no answer is read back (see writeOrReadBack) and ends it with
        // what the read back found; a tracker that cannot be read ends it
        // before anything is written, with what failed. The history is read
        // again either way, and shows what is stored.
        // The entries of the other runs are never deleted to be written back:
        // the tracker is replaced in one upload (onConflict=update), and
        // deleted only when no run is left in it. Whichever write fails, the
        // tracker is the earlier one or the new one. A tracker that cannot be
        // read is not written at all: its other runs are not known.
        let entryRemoved = false;
        // The upload or the delete of the tracker is out: when it gets no
        // answer and cannot be read back, the run may have been deleted or not.
        let trackerWriteOut = false;
        // The tracker could not be read, or was read and is no list of runs.
        let trackerUnread: 'unread' | 'unusable' | '' = '';
        try {
          const trackerEntries = await readOptimizationEntries(modelId);
          if (!Array.isArray(trackerEntries)) {
            trackerUnread = trackerEntries;
            throw new Error('The optimization tracker could not be read');
          }
          const remaining = trackerEntries.filter(
            (candidate) => candidate.optimizationId !== entry.optimizationId
          );
          await createModelVersion(modelId);
          const contents = await getModelContents(modelId);
          const trackerContent = contents.find(
            (modelContent) => modelContent.name === 'Prompt-Optimization-Tracker.json'
          );
          if (remaining.length > 0) {
            trackerWriteOut = true;
            const upload = await createModelContent(modelId, remaining, 'Prompt-Optimization-Tracker.json');
            trackerWriteOut = false;
            written(upload.status_code, 'Prompt-Optimization-Tracker.json');
          } else if (trackerContent?.id) {
            trackerWriteOut = true;
            const status = await deleteModelContent(modelId, trackerContent.id);
            trackerWriteOut = false;
            written(status, 'The delete of the optimization tracker');
          }
          entryRemoved = true;
          // Drop the run's dataset snapshot alongside its entry.
          if (entry.datasetSnapshot) {
            const snapshotContent = contents.find(
              (modelContent) => modelContent.name === entry.datasetSnapshot
            );
            if (snapshotContent?.id) written(await deleteModelContent(modelId, snapshotContent.id), 'The delete of the dataset snapshot');
          }
          showToast(`${promptBuilderInterfaceText?.promptBuilderOptimizeHistoryDeletedToast}`);
        } catch (error) {
          console.error('Deleting the optimization run failed.', error);
          // Once the entry is out of the tracker, only the snapshot is left.
          // A request SAS Model Manager refused (ViyaRefused, which `written`
          // throws for a status as well) is a deletion that failed; anything
          // else before the tracker was written - a request without an
          // answer, a tracker that cannot be read - changed nothing of it.
          const refused = error instanceof ViyaRefused;
          showToast(
            entryRemoved
              ? `${promptBuilderInterfaceText?.promptBuilderOptimizeHistorySnapshotLeft}`.replace('{file}', () => String(entry.datasetSnapshot))
              : trackerUnread === 'unread'
                ? `${promptBuilderInterfaceText?.promptBuilderOptimizeHistoryDeleteUnread}`
                : trackerUnread === 'unusable'
                  ? `${promptBuilderInterfaceText?.promptBuilderOptimizeHistoryDeleteUnusable}`
                  : trackerWriteOut && error instanceof UnansweredWrite && error.done === null
                    ? `${promptBuilderInterfaceText?.promptBuilderOptimizeHistoryDeleteUnknown}`
                    : refused
                      ? `${promptBuilderInterfaceText?.promptBuilderDeleteFailedResponse}`
                      : `${promptBuilderInterfaceText?.promptBuilderOptimizeHistoryDeleteStopped}`
          );
        }
        // The history that is read again is the one of this prompt-test.
        await refreshOptimizationHistory(true);
      });
    }

    /**
     * Load a run's optimised prompt into the workbench AS AN EXPERIMENT: the
     * prompts and variables are restored and the run's target LLM selected, so
     * "Run Experiments" is one click away — judge it against the original,
     * pick a Best Response and save/manifest as usual.
     */
    function loadOptimizationAsExperiment(entry: OptimizationTrackerEntry): void {
      const optimizedPrompt = entry.optimizedPrompt;
      if (!optimizedPrompt) return;
      const systemPromptArea = document.getElementById(
        `${paneID}-obj-${promptBuilderObject?.id}-system-prompt`
      ) as HTMLTextAreaElement | null;
      const userPromptArea = document.getElementById(
        `${paneID}-obj-${promptBuilderObject?.id}-user-prompt`
      ) as HTMLTextAreaElement | null;
      if (systemPromptArea) systemPromptArea.value = String(optimizedPrompt.systemPrompt ?? '');
      if (userPromptArea) userPromptArea.value = String(optimizedPrompt.userPrompt ?? '');
      setPromptVariables(Array.isArray(optimizedPrompt.variables) ? optimizedPrompt.variables : []);
      // Pre-select the LLM the prompt was optimised for.
      const targetIndex = promptBuilderAvailableLLMs.findIndex(
        (availableLLM) => availableLLM.name === entry.targetModel
      );
      if (targetIndex >= 0) {
        const targetCheckbox = document.getElementById(`model${targetIndex}`) as HTMLInputElement | null;
        // A model that cannot be used is not ticked for the prompt either.
        if (targetCheckbox && !targetCheckbox.disabled && !targetCheckbox.checked) {
          targetCheckbox.checked = true;
          targetCheckbox.dispatchEvent(new Event('change'));
        }
      }
      updateRunExperimentsButtonState();
      promptBuilderStepper.goTo(STEP_BUILD);
      systemPromptArea?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      showToast(`${promptBuilderInterfaceText?.promptBuilderOptimizeHistoryLoadedToast}`);
    }

    /**
     * After a completed job: read the job-written optimization tracker from
     * the source prompt-test and render the outcome (metric before/after, a
     * link to the produced prompt-test, and a load-into-workbench button).
     */
    async function showOptimizeResult(): Promise<void> {
      if (!optimizeUI) return;
      if (optimizeJobIsShown()) optimizeUI.resultBox.innerHTML = '';
      const entry = await readOptimizationEntry();
      // The result box is that of the prompt-test the job ran for. With
      // another one open the outcome is a toast, and the run is in the
      // history of its own prompt-test the next time that one is opened.
      const shown = optimizeJobIsShown();
      // A tracker that could not be read says nothing about the job: it has
      // ended, and its outcome is not known to the page.
      if (typeof entry === 'string') {
        showOptimizeOutcome(`${promptBuilderInterfaceText?.promptBuilderOptimizeResultUnread}`);
        void refreshOptimizationHistory(true);
        return;
      }
      if (!entry || entry.status !== 'succeeded') {
        // The job completed but its tracker entry is missing/failed — surface
        // whatever error the entry carries.
        const entryError = entry?.error ? ` ${entry.error}` : '';
        showOptimizeOutcome(`${promptBuilderInterfaceText?.promptBuilderOptimizeJobFailed}${entryError}`);
        return;
      }

      if (entry.type === 'comparison') {
        // A comparison has no before/after — point at the ranked table in
        // the history, which refreshOptimizationHistory renders below.
        const compareHeading = document.createElement('p');
        compareHeading.classList.add('fw-bold', 'mb-1');
        compareHeading.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeCompareDone}`;
        if (shown) optimizeUI.resultBox.appendChild(compareHeading);
        void refreshOptimizationHistory(true);
        showToast(`${promptBuilderInterfaceText?.promptBuilderOptimizeCompareDone}`);
        return;
      }

      // Brief summary in the result box; the full outcome — evolution details
      // and the load-as-experiment action — lives in the history below.
      const resultHeading = document.createElement('p');
      resultHeading.classList.add('fw-bold', 'mb-1');
      resultHeading.innerText =
        `${promptBuilderInterfaceText?.promptBuilderOptimizeDone} ` +
        `${promptBuilderInterfaceText?.promptBuilderOptimizeMetricBefore} ${formatOptimizeMetric(entry.metricBefore)}` +
        ` → ${promptBuilderInterfaceText?.promptBuilderOptimizeMetricAfter} ${formatOptimizeMetric(entry.metricAfter)}`;
      if (shown) optimizeUI.resultBox.appendChild(resultHeading);
      void refreshOptimizationHistory(true);
      showToast(`${promptBuilderInterfaceText?.promptBuilderOptimizeDone}`);
    }

    /**
     * Validate the panel's dataset selection for a launch. For the CAS source
     * the table's columns must cover the prompt's variables (or userPrompt)
     * plus response, and its row count must clear `minRows` (§6.3 of the
     * design). Returns the selection to send with the job, or null when
     * validation failed (a toast has been shown).
     */
    async function validateOptimizeDatasetSelection(
      minRows: number
    ): Promise<{ casSource: boolean; casServer: string; casLibrary: string; casTable: string } | null> {
      if (!optimizeUI) return null;
      const casSource = optimizeUI.datasetCasRadio.checked;
      const casServer = casSource ? optimizeUI.casServerSelect.value : '';
      const casLibrary = casSource ? optimizeUI.casLibSelect.value : '';
      const casTable = casSource ? optimizeUI.casTableSelect.value : '';
      if (!casSource) return { casSource, casServer, casLibrary, casTable };
      if (casServer === '' || casLibrary === '' || casTable === '') {
        showToast(`${promptBuilderInterfaceText?.promptBuilderOptimizeCasMissingToast}`);
        return null;
      }
      let casInfo;
      try {
        casInfo = await getCasTableInfo(casLibrary, casTable, casServer);
      } catch {
        showToast(
          `${promptBuilderInterfaceText?.promptBuilderOptimizeCasTableUnavailable}`.replace(
            '{table}',
            `${casLibrary}.${casTable}`
          )
        );
        return null;
      }
      const columnNames = casInfo.columns.map((column) => column.toLowerCase());
      const variableNames = collectPromptVariables().map((variable) => variable.name);
      const expectedColumns = (variableNames.length > 0 ? variableNames : ['userPrompt']).concat('response');
      const missingColumns = expectedColumns.filter((name) => !columnNames.includes(name.toLowerCase()));
      if (missingColumns.length > 0) {
        showToast(
          `${promptBuilderInterfaceText?.promptBuilderOptimizeCasColumnsMissing}`.replace(
            '{columns}',
            missingColumns.join(', ')
          )
        );
        return null;
      }
      if (casInfo.rowCount < minRows) {
        showToast(
          `${promptBuilderInterfaceText?.promptBuilderOptimizeCasRowsBelowMin}`
            .replace('{rows}', String(casInfo.rowCount))
            .replace('{min}', String(minRows))
        );
        return null;
      }
      return { casSource, casServer, casLibrary, casTable };
    }

    /**
     * Shared post-launch wiring for optimize and compare runs: record the job
     * id, reveal + reset the run log, and start the runtime ticker and the
     * poll loop. Throws when Job Execution returned no id.
     */
    function startOptimizeJobMonitor(job: JobExecutionJob): void {
      if (!optimizeUI) return;
      optimizeJobId = String(job.id ?? '');
      if (optimizeJobId === '') {
        throw new Error('Job Execution returned no job id.');
      }
      // Reset + reveal the collapsed run log for this run; the runtime
      // ticks browser-side every second. All of it where the job is shown:
      // the prompt-test may have been left while the job was being launched.
      optimizeJobMessages = [];
      optimizeJobLiveStatus = undefined;
      optimizeJobStartedAt = Date.now();
      optimizeJobStatus = `${promptBuilderInterfaceText?.promptBuilderOptimizeJobState} ${job.state ?? 'pending'}`;
      if (optimizeJobIsShown()) {
        optimizeUI.logList.innerText = '';
        delete optimizeUI.logList.dataset.pending;
      }
      paintOptimizeJob();
      startOptimizeRuntimeTicker();
      // No poll while the one before is out (oneAtATime).
      const pollOptimizeJobOnce = oneAtATime(pollOptimizeJob);
      optimizePollHandle = window.setInterval(() => {
        void pollOptimizeJobOnce();
      }, 5000);
    }

    /** Save the prompt, launch the optimize job and start polling it. */
    async function promptBuilderRunOptimization(): Promise<void> {
      if (!optimizeUI || optimizeJobActive) return;
      const promptModelId = promptBuilderPromptSelectorDropdown.value;
      const promptName =
        promptBuilderPromptSelectorDropdown.options[promptBuilderPromptSelectorDropdown.selectedIndex]?.text ?? '';
      const targetModelName = optimizeUI.targetSelect.value;
      if (targetModelName === '') {
        showToast(`${promptBuilderInterfaceText?.promptBuilderOptimizeNoTarget}`);
        return;
      }
      const metric = ['judge', 'overlap'].includes(optimizeUI.metricSelect.value)
        ? optimizeUI.metricSelect.value
        : 'exact';
      // GEPA reflects with the judge model even when the metric is not the
      // judge, so the name travels along in both cases.
      const optimizer = ['miprov2', 'gepa'].includes(optimizeUI.optimizerSelect.value)
        ? optimizeUI.optimizerSelect.value
        : 'bootstrap';
      const judgeModelName =
        metric === 'judge' || optimizer === 'gepa' ? optimizeUI.judgeSelect.value : '';
      if (metric === 'judge' && judgeModelName === '') {
        showToast(`${promptBuilderInterfaceText?.promptBuilderJudgeSelectModelToast}`);
        return;
      }
      if (optimizer === 'gepa' && judgeModelName === '') {
        showToast(`${promptBuilderInterfaceText?.promptBuilderOptimizeGepaNeedsJudge}`);
        return;
      }
      const datasetSelection = await validateOptimizeDatasetSelection(optimizeMinSamples());
      if (datasetSelection === null) return;
      const { casSource, casServer, casLibrary, casTable } = datasetSelection;
      const targetLLM = promptBuilderAvailableLLMs.find((availableLLM) => availableLLM.name === targetModelName);

      optimizeJobActive = true;
      optimizeSourceModelId = promptModelId;
      optimizeJobId = '';
      updateOptimizeState();
      optimizeUI.resultBox.innerHTML = '';
      optimizeUI.statusLine.innerText = '';
      optimizeJobStatus = '';

      try {
        // Save first so the job reads the exact prompt (and tracker) the user
        // sees — the dataset gate guarantees there is something to save.
        if (petRows.length > 0 || experimentsModified) {
          // The save no longer throws; a launch over experiments that are not
          // saved would have the job read others than the page shows.
          if (!(await promptBuilderSaveExperiments())) {
            throw new Error(`${promptBuilderInterfaceText?.promptBuilderOptimizeSaveFailed}`);
          }
        }
        const jobDefinitionUri = await resolveJobDefinitionUri(String(promptBuilderObject?.optimizeJobProgram ?? ''));
        const maxDemos = Math.max(0, Math.min(16, Number(optimizeUI.maxDemosInput.value) || 4));
        // Only names and ids travel in the request — the job resolves provider
        // keys server-side from the credential domain under the launching user.
        const job = await launchJob(jobDefinitionUri, `Optimize ${promptName}`, {
          _contextName: String(promptBuilderObject?.computeContext ?? ''),
          promptModelId,
          promptName,
          targetModelId: String(targetLLM?.id ?? ''),
          targetModelName,
          scrEndpoint: String(promptBuilderObject?.SCREndpoint ?? ''),
          deploymentType: String(promptBuilderObject?.deploymentType ?? 'k8s'),
          datasetSource: casSource ? 'cas' : 'tracker',
          casServer,
          casLibrary,
          casTable,
          metric,
          judgeModelName,
          optimizer,
          maxDemos: String(maxDemos),
          minSamples: String(optimizeMinSamples()),
          keyDomain: credentialDomain,
        });
        startOptimizeJobMonitor(job);
      } catch (error) {
        stopOptimizePolling();
        // A launch that got no answer was read back (see launchJob): one
        // that is running did not get here.
        const noAnswer = noAnswerText(error);
        showOptimizeOutcome(
          noAnswer
            ? `${promptBuilderInterfaceText?.promptBuilderOptimizeRunButton}: ${noAnswer}`
            : `${promptBuilderInterfaceText?.promptBuilderOptimizeLaunchFailed} ${String((error as Error)?.message ?? error)}`
        );
        showToast(noAnswer ? `${promptBuilderInterfaceText?.promptBuilderOptimizeRunButton}: ${noAnswer}` : `${promptBuilderInterfaceText?.promptBuilderOptimizeLaunchFailed}`);
        resetOptimizeRunButton();
      }
    }

    /** The compare-mode sample floor: nothing is trained, so a comparison may
     *  run on smaller sets than an optimization — with a noise warning. */
    const COMPARE_MIN_SAMPLES = 10;

    /**
     * "Compare targets…" (Phase 4a): pick 2+ candidate LLMs in a modal, then
     * launch the SAME job in compare mode — the baseline prompt is scored on
     * every candidate (no optimizer runs) and the ranked comparison lands in
     * the optimization history, where each row offers the handoff into the
     * normal single-target optimization.
     */
    async function openCompareTargetsModal(): Promise<void> {
      if (!optimizeUI) return;
      if (optimizeJobActive) {
        showToast(`${promptBuilderInterfaceText?.promptBuilderOptimizeAlreadyRunning}`);
        return;
      }
      const promptSelected =
        promptBuilderPromptSelectorDropdown.value !== '' &&
        promptBuilderPromptSelectorDropdown.value !== `${promptBuilderInterfaceText?.promptSelect}`;
      if (!promptSelected) {
        showToast(`${promptBuilderInterfaceText?.promptBuilderOptimizeNoPrompt}`);
        return;
      }
      // Read the metric FRESH wherever it is used: the CAS row-count fetch
      // below awaits before the modal blocks the panel, so a value captured
      // here could go stale if the user changes the select in that window.
      const currentMetric = (): string =>
        ['judge', 'overlap'].includes(optimizeUI?.metricSelect.value ?? '')
          ? String(optimizeUI?.metricSelect.value)
          : 'exact';
      // Sample count for the estimate: the tracker count is known; a CAS
      // table's row count is fetched best-effort when one is fully selected.
      let samples = optimizeUI.datasetCasRadio.checked ? NaN : countOptimizeSamples();
      if (
        optimizeUI.datasetCasRadio.checked &&
        optimizeUI.casServerSelect.value !== '' &&
        optimizeUI.casLibSelect.value !== '' &&
        optimizeUI.casTableSelect.value !== ''
      ) {
        try {
          const casInfo = await getCasTableInfo(
            optimizeUI.casLibSelect.value,
            optimizeUI.casTableSelect.value,
            optimizeUI.casServerSelect.value
          );
          samples = casInfo.rowCount;
        } catch {
          /* estimate renders without a sample count */
        }
      }

      const intro = document.createElement('p');
      intro.classList.add('mb-2');
      intro.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeCompareIntro}`;
      // Screen-only (4a) vs optimize-each (4b): pre-screen cheaply, then
      // sweep only the promising candidates — or sweep directly.
      const modeBlock = document.createElement('div');
      modeBlock.classList.add('mb-2');
      const modeName = `${paneID}-obj-${promptBuilderObject?.id}-optimize-compare-mode`;
      const makeModeRadio = (value: string, labelText: unknown, checked: boolean): HTMLInputElement => {
        const wrapper = document.createElement('div');
        wrapper.classList.add('form-check');
        const radio = document.createElement('input');
        radio.classList.add('form-check-input');
        radio.type = 'radio';
        radio.name = modeName;
        radio.id = `${modeName}-${value}`;
        radio.value = value;
        radio.checked = checked;
        const radioLabel = document.createElement('label');
        radioLabel.classList.add('form-check-label');
        radioLabel.htmlFor = radio.id;
        radioLabel.innerText = `${labelText}`;
        wrapper.appendChild(radio);
        wrapper.appendChild(radioLabel);
        modeBlock.appendChild(wrapper);
        return radio;
      };
      const screenRadio = makeModeRadio(
        'compare',
        promptBuilderInterfaceText?.promptBuilderOptimizeCompareModeScreen,
        true
      );
      const sweepRadio = makeModeRadio(
        'sweep',
        `${promptBuilderInterfaceText?.promptBuilderOptimizeCompareModeSweep}`.replace(
          '{optimizer}',
          optimizeUI.optimizerSelect.value
        ),
        false
      );
      const candidateList = document.createElement('div');
      candidateList.classList.add('mb-2');
      const candidateBoxes: HTMLInputElement[] = [];
      promptBuilderAvailableLLMs.forEach((availableLLM, index) => {
        // The job resolves the same credential domains server-side, so a
        // model this user has no credential for cannot be a compare target.
        if (llmNotUsableNote(availableLLM)) return;
        const wrapper = document.createElement('div');
        wrapper.classList.add('form-check');
        const checkbox = document.createElement('input');
        checkbox.classList.add('form-check-input');
        checkbox.type = 'checkbox';
        checkbox.id = `${paneID}-obj-${promptBuilderObject?.id}-optimize-compare-model-${index}`;
        checkbox.value = availableLLM.name;
        checkbox.checked = availableLLM.name === optimizeUI?.targetSelect.value;
        const checkboxLabel = document.createElement('label');
        checkboxLabel.classList.add('form-check-label');
        checkboxLabel.htmlFor = checkbox.id;
        checkboxLabel.innerText = availableLLM.name;
        wrapper.appendChild(checkbox);
        wrapper.appendChild(checkboxLabel);
        candidateList.appendChild(wrapper);
        candidateBoxes.push(checkbox);
      });
      const estimateLine = document.createElement('small');
      estimateLine.classList.add('text-muted', 'd-block');
      const updateCompareEstimate = (): void => {
        const selectedCount = candidateBoxes.filter((box) => box.checked).length;
        const perTarget = Number.isFinite(samples) ? Number(samples) : 0;
        // Screening: per target one smoke call + one call per example
        // (doubled by the judge metric's extra scoring call per example).
        // Sweep: per target a FULL optimization run — the same per-optimizer
        // factors the panel's estimate uses.
        let perTargetCalls = 1 + perTarget * (currentMetric() === 'judge' ? 2 : 1);
        if (sweepRadio.checked) {
          const sweepOptimizer = optimizeUI?.optimizerSelect.value ?? 'bootstrap';
          perTargetCalls =
            sweepOptimizer === 'gepa'
              ? perTarget * 75 + 60
              : sweepOptimizer === 'miprov2'
                ? perTarget * 8 + 30
                : perTarget * 3 + 10;
        }
        estimateLine.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeCompareEstimate}`
          .replace('{calls}', Number.isFinite(samples) ? String(selectedCount * perTargetCalls) : '?')
          .replace('{targets}', String(selectedCount));
      };
      candidateBoxes.forEach((box) => box.addEventListener('change', updateCompareEstimate));
      screenRadio.addEventListener('change', updateCompareEstimate);
      sweepRadio.addEventListener('change', updateCompareEstimate);
      updateCompareEstimate();
      const modalBody: (HTMLElement | string)[] = [intro, modeBlock, candidateList, estimateLine];
      if (Number.isFinite(samples) && Number(samples) < 30) {
        const noiseWarning = document.createElement('small');
        noiseWarning.classList.add('text-warning', 'd-block');
        noiseWarning.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeCompareNoiseWarning}`;
        modalBody.push(noiseWarning);
      }

      const confirmed = await showConfirmModal({
        title: `${promptBuilderInterfaceText?.promptBuilderOptimizeCompareTitle}`,
        body: modalBody,
        confirmText: `${promptBuilderInterfaceText?.promptBuilderOptimizeCompareConfirm}`,
        cancelText: `${promptBuilderInterfaceText?.promptBuilderDeleteCancelButton}`,
        confirmClass: 'btn-primary',
      });
      if (!confirmed) return;
      const sweepMode = sweepRadio.checked;
      const selectedNames = candidateBoxes.filter((box) => box.checked).map((box) => box.value);
      if (selectedNames.length < 2) {
        showToast(`${promptBuilderInterfaceText?.promptBuilderOptimizeCompareMinTargets}`);
        return;
      }
      // Screening only evaluates (fixed floor of 10); a sweep TRAINS per
      // candidate, so the full configured minimum applies.
      const requiredSamples = sweepMode ? optimizeMinSamples() : COMPARE_MIN_SAMPLES;
      if (Number.isFinite(samples) && Number(samples) < requiredSamples) {
        showToast(
          sweepMode
            ? `${promptBuilderInterfaceText?.promptBuilderOptimizeSamplesBelowMin}`.replace(
                '{min}',
                String(requiredSamples)
              )
            : `${promptBuilderInterfaceText?.promptBuilderOptimizeCompareMinSamples}`
        );
        return;
      }
      await selectionLock.during(() => promptBuilderRunComparison(selectedNames, currentMetric(), sweepMode));
    }

    /** Save the prompt, launch the job in compare/sweep mode and start polling. */
    async function promptBuilderRunComparison(
      targetNames: string[],
      metric: string,
      sweepMode: boolean
    ): Promise<void> {
      if (!optimizeUI || optimizeJobActive) return;
      const promptModelId = promptBuilderPromptSelectorDropdown.value;
      const promptName =
        promptBuilderPromptSelectorDropdown.options[promptBuilderPromptSelectorDropdown.selectedIndex]?.text ?? '';
      const optimizer = ['miprov2', 'gepa'].includes(optimizeUI.optimizerSelect.value)
        ? optimizeUI.optimizerSelect.value
        : 'bootstrap';
      // A sweep runs the panel's optimizer per candidate, so it inherits the
      // optimizer's judge requirement (GEPA reflection) too.
      const judgeModelName =
        metric === 'judge' || (sweepMode && optimizer === 'gepa') ? optimizeUI.judgeSelect.value : '';
      if (metric === 'judge' && judgeModelName === '') {
        showToast(`${promptBuilderInterfaceText?.promptBuilderJudgeSelectModelToast}`);
        return;
      }
      if (sweepMode && optimizer === 'gepa' && judgeModelName === '') {
        showToast(`${promptBuilderInterfaceText?.promptBuilderOptimizeGepaNeedsJudge}`);
        return;
      }
      const datasetSelection = await validateOptimizeDatasetSelection(
        sweepMode ? optimizeMinSamples() : COMPARE_MIN_SAMPLES
      );
      if (datasetSelection === null) return;
      const { casSource, casServer, casLibrary, casTable } = datasetSelection;

      optimizeJobActive = true;
      optimizeSourceModelId = promptModelId;
      optimizeJobId = '';
      updateOptimizeState();
      optimizeUI.resultBox.innerHTML = '';
      optimizeUI.statusLine.innerText = '';
      optimizeJobStatus = '';

      try {
        // Save first so the job reads the exact prompt (and tracker) the user
        // sees — same contract as an optimization launch.
        if (petRows.length > 0 || experimentsModified) {
          // The save no longer throws; a launch over experiments that are not
          // saved would have the job read others than the page shows.
          if (!(await promptBuilderSaveExperiments())) {
            throw new Error(`${promptBuilderInterfaceText?.promptBuilderOptimizeSaveFailed}`);
          }
        }
        const jobDefinitionUri = await resolveJobDefinitionUri(String(promptBuilderObject?.optimizeJobProgram ?? ''));
        const maxDemos = Math.max(0, Math.min(16, Number(optimizeUI.maxDemosInput.value) || 4));
        const job = await launchJob(jobDefinitionUri, `Compare targets for ${promptName}`, {
          _contextName: String(promptBuilderObject?.computeContext ?? ''),
          promptModelId,
          promptName,
          mode: sweepMode ? 'sweep' : 'compare',
          targetModelNames: targetNames.join(','),
          targetModelId: '',
          targetModelName: '',
          scrEndpoint: String(promptBuilderObject?.SCREndpoint ?? ''),
          deploymentType: String(promptBuilderObject?.deploymentType ?? 'k8s'),
          datasetSource: casSource ? 'cas' : 'tracker',
          casServer,
          casLibrary,
          casTable,
          metric,
          judgeModelName,
          optimizer: sweepMode ? optimizer : 'bootstrap',
          maxDemos: sweepMode ? String(maxDemos) : '0',
          minSamples: String(sweepMode ? optimizeMinSamples() : COMPARE_MIN_SAMPLES),
          keyDomain: credentialDomain,
        });
        startOptimizeJobMonitor(job);
      } catch (error) {
        stopOptimizePolling();
        // A launch that got no answer was read back (see launchJob): one
        // that is running did not get here.
        const noAnswer = noAnswerText(error);
        showOptimizeOutcome(
          noAnswer
            ? `${promptBuilderInterfaceText?.promptBuilderOptimizeCompareButton}: ${noAnswer}`
            : `${promptBuilderInterfaceText?.promptBuilderOptimizeLaunchFailed} ${String((error as Error)?.message ?? error)}`
        );
        showToast(noAnswer ? `${promptBuilderInterfaceText?.promptBuilderOptimizeCompareButton}: ${noAnswer}` : `${promptBuilderInterfaceText?.promptBuilderOptimizeLaunchFailed}`);
        resetOptimizeRunButton();
      }
    }

    // Build the section only when the deployment enables optimization.
    let optimizeSection: HTMLDivElement | null = null;
    if (optimizationEnabled) {
      const optimizeHeader = document.createElement('h2');
      optimizeHeader.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeSectionHeading}`;
      const optimizeDescription = document.createElement('p');
      optimizeDescription.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeSectionDescription}`;
      // A deep link into the user guide: when optimization helps, which
      // metric/optimizer to pick, and the CAS dataset schema.
      const optimizeLearnMore = document.createElement('a');
      optimizeLearnMore.href =
        'https://sassoftware.github.io/sas-agentic-ai-accelerator/User-Guide/Prompt-Builder#choosing-the-dataset-metric-and-optimizer';
      optimizeLearnMore.target = '_blank';
      // The one link that leaves SAS Viya. "noreferrer" is for the ways of
      // opening it that do not come through the click handler below (the
      // middle mouse button, "Open link in new tab"); the click itself is
      // kept from telling the other site where SAS Viya runs by the referrer
      // policy of the page (see index.html).
      optimizeLearnMore.rel = 'noopener noreferrer';
      optimizeLearnMore.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeLearnMore}`;
      // The user guide is another site, and the sandboxed iframe blocks its
      // new tab the same way.
      optimizeLearnMore.onclick = (event) => openModelManagerLink(event, optimizeLearnMore, promptBuilderInterfaceText);
      const optimizeLearnMoreLine = document.createElement('p');
      optimizeLearnMoreLine.appendChild(optimizeLearnMore);

      // â„¹ï¸ tooltip helper for the optimize controls (the app's judge toggles
      // use the same pattern: keyboard-focusable Bootstrap tooltip).
      const makeOptimizeInfoIcon = (labelText: unknown, infoHtml: unknown): HTMLSpanElement => {
        const icon = document.createElement('span');
        icon.classList.add('info-icon', 'ms-1');
        icon.innerHTML = '&#x2139;&#xFE0F;';
        icon.setAttribute('tabindex', '0');
        icon.setAttribute('role', 'button');
        icon.setAttribute('aria-label', `${labelText}`);
        icon.setAttribute('data-bs-toggle', 'tooltip');
        new Tooltip(icon, { title: String(infoHtml), html: true, container: 'body' });
        return icon;
      };

      const optimizeControls = document.createElement('div');
      optimizeControls.classList.add('pb-optimize-controls', 'd-flex', 'flex-column', 'gap-2', 'mt-2');

      // Target LLM.
      const targetRow = document.createElement('div');
      targetRow.classList.add('d-flex', 'flex-column', 'align-items-start', 'gap-2');
      const targetLabel = document.createElement('label');
      targetLabel.classList.add('form-label', 'mb-0');
      targetLabel.htmlFor = `${paneID}-obj-${promptBuilderObject?.id}-optimize-target`;
      targetLabel.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeTargetLabel}`;
      const optimizeTargetSelect = document.createElement('select');
      optimizeTargetSelect.id = `${paneID}-obj-${promptBuilderObject?.id}-optimize-target`;
      optimizeTargetSelect.classList.add('form-select', 'form-select-sm');
      const targetPlaceholder = document.createElement('option');
      targetPlaceholder.value = '';
      targetPlaceholder.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeTargetPlaceholder}`;
      optimizeTargetSelect.appendChild(targetPlaceholder);
      promptBuilderAvailableLLMs.forEach((availableLLM) => {
        // The optimize job resolves the same credential domains server-side.
        if (llmNotUsableNote(availableLLM)) return;
        const targetOption = document.createElement('option');
        targetOption.value = availableLLM.name;
        targetOption.innerText = availableLLM.name;
        optimizeTargetSelect.appendChild(targetOption);
      });
      targetRow.appendChild(targetLabel);
      targetRow.appendChild(optimizeTargetSelect);
      // Phase 4a: baseline screening across several candidate targets — an
      // explicit modal entry point so the single-target flow stays untouched.
      const optimizeCompareButton = document.createElement('button');
      optimizeCompareButton.type = 'button';
      optimizeCompareButton.id = `${paneID}-obj-${promptBuilderObject?.id}-optimize-compare`;
      optimizeCompareButton.classList.add('btn', 'btn-outline-secondary', 'btn-sm');
      optimizeCompareButton.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeCompareButton}`;
      optimizeCompareButton.onclick = () => {
        void openCompareTargetsModal();
      };
      targetRow.appendChild(optimizeCompareButton);

      // Dataset source: the prompt's experiments (default), or a governed CAS
      // table built from the shipped Create-Optimization-Dataset.sas template.
      const datasetBlock = document.createElement('div');
      const datasetLabel = document.createElement('p');
      datasetLabel.classList.add('fw-bold', 'mb-1');
      datasetLabel.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeDatasetLabel}`;
      datasetLabel.appendChild(
        makeOptimizeInfoIcon(
          promptBuilderInterfaceText?.promptBuilderOptimizeDatasetLabel,
          promptBuilderInterfaceText?.promptBuilderOptimizeDatasetInfo
        )
      );
      datasetBlock.appendChild(datasetLabel);
      const datasetSourceName = `${paneID}-obj-${promptBuilderObject?.id}-optimize-dataset-source`;
      const makeDatasetRadio = (value: string, labelText: unknown, checked: boolean): HTMLInputElement => {
        const wrapper = document.createElement('div');
        wrapper.classList.add('form-check');
        const radio = document.createElement('input');
        radio.classList.add('form-check-input');
        radio.type = 'radio';
        radio.name = datasetSourceName;
        radio.id = `${datasetSourceName}-${value}`;
        radio.value = value;
        radio.checked = checked;
        const radioLabel = document.createElement('label');
        radioLabel.classList.add('form-check-label');
        radioLabel.htmlFor = radio.id;
        radioLabel.innerText = `${labelText}`;
        wrapper.appendChild(radio);
        wrapper.appendChild(radioLabel);
        datasetBlock.appendChild(wrapper);
        return radio;
      };
      const datasetTrackerRadio = makeDatasetRadio(
        'tracker',
        promptBuilderInterfaceText?.promptBuilderOptimizeDatasetTracker,
        true
      );
      const datasetNotice = document.createElement('small');
      datasetNotice.classList.add('text-muted', 'd-block');
      datasetNotice.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeDatasetTrackerInfo}`;
      const optimizeSamplesHint = document.createElement('small');
      optimizeSamplesHint.id = `${paneID}-obj-${promptBuilderObject?.id}-optimize-samples-hint`;
      optimizeSamplesHint.classList.add('text-muted', 'd-block');
      datasetBlock.appendChild(datasetNotice);
      datasetBlock.appendChild(optimizeSamplesHint);
      const datasetCasRadio = makeDatasetRadio(
        'cas',
        promptBuilderInterfaceText?.promptBuilderOptimizeDatasetCas,
        false
      );
      // Server → caslib → table pickers (revealed for the CAS source) +
      // template pointer. Populated lazily from the CAS Management listings.
      const casRow = document.createElement('div');
      casRow.id = `${paneID}-obj-${promptBuilderObject?.id}-optimize-cas-row`;
      casRow.classList.add('d-none', 'ms-4', 'mb-1');
      const casInputs = document.createElement('div');
      casInputs.classList.add('d-flex', 'align-items-center', 'gap-2', 'flex-wrap');
      const makeCasPicker = (kind: string, placeholderText: unknown, width: string): HTMLSelectElement => {
        const select = document.createElement('select');
        select.id = `${paneID}-obj-${promptBuilderObject?.id}-optimize-cas-${kind}`;
        select.classList.add('form-select', 'form-select-sm');
        select.style.width = width;
        const placeholder = document.createElement('option');
        placeholder.value = '';
        placeholder.innerText = `${placeholderText}`;
        placeholder.disabled = true;
        placeholder.selected = true;
        select.appendChild(placeholder);
        casInputs.appendChild(select);
        return select;
      };
      const optimizeCasServerSelect = makeCasPicker(
        'server',
        promptBuilderInterfaceText?.promptBuilderOptimizeCasServerPlaceholder,
        '12rem'
      );
      const optimizeCasLibSelect = makeCasPicker(
        'lib',
        promptBuilderInterfaceText?.promptBuilderOptimizeCasLibPlaceholder,
        '12rem'
      );
      const optimizeCasTableSelect = makeCasPicker(
        'table',
        promptBuilderInterfaceText?.promptBuilderOptimizeCasTablePlaceholder,
        '15rem'
      );
      // Deployments have MANY caslibs/tables: both pickers become comboboxes,
      // so typing filters the open dropdown itself.
      attachCombobox(optimizeCasLibSelect);
      attachCombobox(optimizeCasTableSelect);
      const casTemplateHint = document.createElement('small');
      casTemplateHint.classList.add('text-muted', 'd-block');
      casTemplateHint.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeCasTemplateHint}`;
      casRow.appendChild(casInputs);
      casRow.appendChild(casTemplateHint);
      datasetBlock.appendChild(casRow);
      optimizeCasServerSelect.addEventListener('change', () => {
        optimizeCasRowCount = null;
        void loadCaslibOptions();
      });
      optimizeCasLibSelect.addEventListener('change', () => {
        optimizeCasRowCount = null;
        void loadCasTableOptions();
      });
      // Fetch the picked table's row count (best effort) so the panel's call
      // estimate reflects the CAS dataset; '?' is shown while unknown.
      optimizeCasTableSelect.addEventListener('change', () => {
        optimizeCasRowCount = null;
        updateOptimizeState();
        const pickedServer = optimizeCasServerSelect.value;
        const pickedLib = optimizeCasLibSelect.value;
        const pickedTable = optimizeCasTableSelect.value;
        if (pickedServer === '' || pickedLib === '' || pickedTable === '') return;
        getCasTableInfo(pickedLib, pickedTable, pickedServer)
          .then((casInfo) => {
            if (optimizeUI && optimizeUI.casTableSelect.value === pickedTable) {
              optimizeCasRowCount = casInfo.rowCount;
              updateOptimizeState();
            }
          })
          .catch(() => {
            /* the estimate keeps showing '?' */
          });
      });

      // Metric (+ judge model when the metric is the judge).
      const metricRow = document.createElement('div');
      metricRow.classList.add('d-flex', 'flex-column', 'gap-1');
      const metricLabel = document.createElement('label');
      metricLabel.classList.add('form-label', 'mb-0');
      metricLabel.htmlFor = `${paneID}-obj-${promptBuilderObject?.id}-optimize-metric`;
      metricLabel.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeMetricLabel}`;
      const optimizeMetricSelect = document.createElement('select');
      optimizeMetricSelect.id = `${paneID}-obj-${promptBuilderObject?.id}-optimize-metric`;
      optimizeMetricSelect.classList.add('form-select', 'form-select-sm');
      const exactOption = document.createElement('option');
      exactOption.value = 'exact';
      exactOption.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeMetricExact}`;
      const overlapMetricOption = document.createElement('option');
      overlapMetricOption.value = 'overlap';
      overlapMetricOption.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeMetricOverlap}`;
      const judgeMetricOption = document.createElement('option');
      judgeMetricOption.value = 'judge';
      judgeMetricOption.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeMetricJudge}`;
      optimizeMetricSelect.appendChild(exactOption);
      optimizeMetricSelect.appendChild(overlapMetricOption);
      optimizeMetricSelect.appendChild(judgeMetricOption);
      metricRow.appendChild(metricLabel);
      metricRow.appendChild(optimizeMetricSelect);

      const optimizeJudgeRow = document.createElement('div');
      optimizeJudgeRow.id = `${paneID}-obj-${promptBuilderObject?.id}-optimize-judge-row`;
      optimizeJudgeRow.classList.add('d-flex', 'flex-column', 'gap-1', 'mt-2', 'd-none');
      const optimizeJudgeLabel = document.createElement('label');
      optimizeJudgeLabel.classList.add('form-label', 'mb-0');
      optimizeJudgeLabel.htmlFor = `${paneID}-obj-${promptBuilderObject?.id}-optimize-judge-model`;
      optimizeJudgeLabel.innerText = `${promptBuilderInterfaceText?.promptBuilderJudgeModelLabel}`;
      const optimizeJudgeSelect = document.createElement('select');
      optimizeJudgeSelect.id = `${paneID}-obj-${promptBuilderObject?.id}-optimize-judge-model`;
      optimizeJudgeSelect.classList.add('form-select', 'form-select-sm');
      const optimizeJudgePlaceholder = document.createElement('option');
      optimizeJudgePlaceholder.value = '';
      optimizeJudgePlaceholder.innerText = `${promptBuilderInterfaceText?.promptBuilderJudgeSelectPlaceholder}`;
      optimizeJudgeSelect.appendChild(optimizeJudgePlaceholder);
      promptBuilderAvailableLLMs.forEach((availableLLM) => {
        if (llmNotUsableNote(availableLLM)) return;
        const judgeOption = document.createElement('option');
        judgeOption.value = availableLLM.name;
        judgeOption.innerText = availableLLM.name;
        optimizeJudgeSelect.appendChild(judgeOption);
      });
      const configuredOptimizeJudge = String(promptBuilderObject?.judgeModel ?? '');
      if (
        configuredOptimizeJudge &&
        promptBuilderAvailableLLMs.some((availableLLM) => availableLLM.name === configuredOptimizeJudge)
      ) {
        optimizeJudgeSelect.value = configuredOptimizeJudge;
      }
      optimizeJudgeRow.appendChild(optimizeJudgeLabel);
      optimizeJudgeRow.appendChild(optimizeJudgeSelect);

      // Optimizer (bootstrap selects demos; MIPROv2 also rewrites the
      // instruction text at the cost of more model calls) + max few-shot demos.
      const optimizerRow = document.createElement('div');
      optimizerRow.classList.add('d-flex', 'flex-column', 'align-items-start', 'gap-1');
      const optimizerLabel = document.createElement('label');
      optimizerLabel.classList.add('form-label', 'mb-0');
      optimizerLabel.htmlFor = `${paneID}-obj-${promptBuilderObject?.id}-optimize-optimizer`;
      optimizerLabel.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeOptimizerLabel}`;
      const optimizeOptimizerSelect = document.createElement('select');
      optimizeOptimizerSelect.id = `${paneID}-obj-${promptBuilderObject?.id}-optimize-optimizer`;
      optimizeOptimizerSelect.classList.add('form-select', 'form-select-sm');
      const bootstrapOption = document.createElement('option');
      bootstrapOption.value = 'bootstrap';
      bootstrapOption.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeOptimizerBootstrap}`;
      optimizeOptimizerSelect.appendChild(bootstrapOption);
      const miprov2Option = document.createElement('option');
      miprov2Option.value = 'miprov2';
      miprov2Option.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeOptimizerMiprov2}`;
      optimizeOptimizerSelect.appendChild(miprov2Option);
      const gepaOption = document.createElement('option');
      gepaOption.value = 'gepa';
      gepaOption.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeOptimizerGepa}`;
      optimizeOptimizerSelect.appendChild(gepaOption);
      const maxDemosLabel = document.createElement('label');
      maxDemosLabel.classList.add('form-label', 'mb-0', 'mt-2');
      maxDemosLabel.htmlFor = `${paneID}-obj-${promptBuilderObject?.id}-optimize-max-demos`;
      maxDemosLabel.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeMaxDemosLabel}`;
      const optimizeMaxDemosInput = document.createElement('input');
      optimizeMaxDemosInput.type = 'number';
      optimizeMaxDemosInput.id = `${paneID}-obj-${promptBuilderObject?.id}-optimize-max-demos`;
      optimizeMaxDemosInput.classList.add('form-control', 'form-control-sm');
      optimizeMaxDemosInput.style.width = '5rem';
      optimizeMaxDemosInput.min = '0';
      optimizeMaxDemosInput.max = '16';
      optimizeMaxDemosInput.value = '4';
      optimizerRow.appendChild(optimizerLabel);
      optimizerRow.appendChild(optimizeOptimizerSelect);
      optimizerRow.appendChild(maxDemosLabel);
      optimizerRow.appendChild(optimizeMaxDemosInput);

      const optimizeEstimateLine = document.createElement('small');
      optimizeEstimateLine.id = `${paneID}-obj-${promptBuilderObject?.id}-optimize-estimate`;
      optimizeEstimateLine.classList.add('text-muted');

      // Run + status + result.
      const optimizeRunButton = document.createElement('button');
      optimizeRunButton.type = 'button';
      optimizeRunButton.id = `${paneID}-obj-${promptBuilderObject?.id}-optimize-run`;
      optimizeRunButton.classList.add('btn', 'btn-primary');
      optimizeRunButton.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeRunButton}`;
      optimizeRunButton.onclick = () => {
        // It saves the experiments first: locked from the click to the launch.
        void selectionLock.during(promptBuilderRunOptimization);
      };
      const optimizeRunWrapper = wrapForHint(optimizeRunButton);
      const optimizeStatusLine = document.createElement('p');
      optimizeStatusLine.id = `${paneID}-obj-${promptBuilderObject?.id}-optimize-status`;
      optimizeStatusLine.classList.add('mb-1');
      // Collapsible run log (collapsed by default, hidden until a run starts):
      // the job's runtime plus its OWN milestones — never the raw SAS log.
      const optimizeLogDetails = document.createElement('details');
      optimizeLogDetails.id = `${paneID}-obj-${promptBuilderObject?.id}-optimize-log`;
      optimizeLogDetails.classList.add('d-none', 'mb-2');
      const optimizeLogSummary = document.createElement('summary');
      optimizeLogSummary.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeLogHeading}`;
      const optimizeLogRuntime = document.createElement('p');
      optimizeLogRuntime.classList.add('text-muted', 'mb-1', 'mt-1');
      const optimizeLogList = document.createElement('pre');
      optimizeLogList.classList.add('pb-optimize-log-list', 'mb-1');
      optimizeLogList.style.whiteSpace = 'pre-wrap';
      optimizeLogDetails.appendChild(optimizeLogSummary);
      optimizeLogDetails.appendChild(optimizeLogRuntime);
      optimizeLogDetails.appendChild(optimizeLogList);
      const optimizeResultBox = document.createElement('div');
      optimizeResultBox.id = `${paneID}-obj-${promptBuilderObject?.id}-optimize-result`;
      // Past runs of the selected prompt, read from its optimization tracker.
      const optimizeHistoryContainer = document.createElement('div');
      optimizeHistoryContainer.id = `${paneID}-obj-${promptBuilderObject?.id}-optimize-history`;

      // Why the run is blocked (too few Best Response runs, no prompt, ...).
      const optimizeBanner = document.createElement('div');
      optimizeBanner.id = `${paneID}-obj-${promptBuilderObject?.id}-optimize-banner`;
      optimizeBanner.classList.add('alert', 'alert-warning', 'py-2', 'px-3', 'mb-0', 'd-none');
      optimizeBanner.setAttribute('role', 'status');

      // The metric and optimizer explanations stay readable beside their
      // pickers instead of hiding in a tooltip.
      const makeOptimizeGuide = (titleText: unknown, guideHtml: unknown): HTMLDetailsElement => {
        const guide = document.createElement('details');
        guide.classList.add('pb-guide');
        guide.open = true;
        const guideSummary = document.createElement('summary');
        guideSummary.innerText = `${titleText}`;
        const guideBody = document.createElement('div');
        guideBody.innerHTML = String(guideHtml);
        guide.appendChild(guideSummary);
        guide.appendChild(guideBody);
        return guide;
      };
      const makeOptimizeFieldBox = (...rows: HTMLElement[]): HTMLDivElement => {
        const fieldBox = document.createElement('div');
        fieldBox.classList.add('pb-field-box');
        rows.forEach((row) => fieldBox.appendChild(row));
        return fieldBox;
      };
      // Three columns: what to optimise for and on which data, how answers
      // are scored, and how the prompt is rewritten.
      const optimizeGrid = document.createElement('div');
      optimizeGrid.classList.add('pb-optimize-grid');
      const optimizeTargetColumn = document.createElement('div');
      optimizeTargetColumn.classList.add('d-flex', 'flex-column', 'gap-3');
      optimizeTargetColumn.appendChild(targetRow);
      optimizeTargetColumn.appendChild(datasetBlock);
      const optimizeMetricColumn = document.createElement('div');
      optimizeMetricColumn.classList.add('d-flex', 'flex-column', 'gap-2');
      optimizeMetricColumn.appendChild(
        makeOptimizeGuide(
          promptBuilderInterfaceText?.promptBuilderOptimizeMetricGuide,
          promptBuilderInterfaceText?.promptBuilderOptimizeMetricInfo
        )
      );
      optimizeMetricColumn.appendChild(makeOptimizeFieldBox(metricRow, optimizeJudgeRow));
      const optimizeOptimizerColumn = document.createElement('div');
      optimizeOptimizerColumn.classList.add('d-flex', 'flex-column', 'gap-2');
      optimizeOptimizerColumn.appendChild(
        makeOptimizeGuide(
          promptBuilderInterfaceText?.promptBuilderOptimizeOptimizerGuide,
          promptBuilderInterfaceText?.promptBuilderOptimizeOptimizerInfo
        )
      );
      optimizeOptimizerColumn.appendChild(makeOptimizeFieldBox(optimizerRow));
      optimizeGrid.appendChild(optimizeTargetColumn);
      optimizeGrid.appendChild(optimizeMetricColumn);
      optimizeGrid.appendChild(optimizeOptimizerColumn);

      optimizeControls.appendChild(optimizeGrid);
      optimizeControls.appendChild(optimizeHistoryContainer);
      // The run itself sits beside the instructions, under the notice.
      const optimizeRunBlock = document.createElement('div');
      optimizeRunBlock.classList.add('d-flex', 'flex-column', 'align-items-start', 'gap-2');
      optimizeBanner.classList.add('align-self-stretch');
      optimizeRunBlock.appendChild(optimizeBanner);
      optimizeRunBlock.appendChild(optimizeRunWrapper);
      optimizeRunBlock.appendChild(optimizeEstimateLine);
      optimizeRunBlock.appendChild(optimizeStatusLine);
      optimizeLogDetails.classList.add('align-self-stretch');
      optimizeRunBlock.appendChild(optimizeLogDetails);
      optimizeResultBox.classList.add('align-self-stretch');
      optimizeRunBlock.appendChild(optimizeResultBox);

      optimizeUI = {
        targetSelect: optimizeTargetSelect,
        compareButton: optimizeCompareButton,
        metricSelect: optimizeMetricSelect,
        optimizerSelect: optimizeOptimizerSelect,
        datasetTrackerRadio,
        datasetCasRadio,
        datasetTrackerInfo: datasetNotice,
        casRow,
        casServerSelect: optimizeCasServerSelect,
        casLibSelect: optimizeCasLibSelect,
        casTableSelect: optimizeCasTableSelect,
        judgeRow: optimizeJudgeRow,
        judgeSelect: optimizeJudgeSelect,
        maxDemosInput: optimizeMaxDemosInput,
        samplesHint: optimizeSamplesHint,
        estimateLine: optimizeEstimateLine,
        runButton: optimizeRunButton,
        runWrapper: optimizeRunWrapper,
        banner: optimizeBanner,
        statusLine: optimizeStatusLine,
        resultBox: optimizeResultBox,
        historyContainer: optimizeHistoryContainer,
        logDetails: optimizeLogDetails,
        logRuntime: optimizeLogRuntime,
        logList: optimizeLogList,
      };
      optimizeTargetSelect.addEventListener('change', updateOptimizeState);
      optimizeMetricSelect.addEventListener('change', updateOptimizeState);
      optimizeOptimizerSelect.addEventListener('change', updateOptimizeState);
      optimizeJudgeSelect.addEventListener('change', updateOptimizeState);
      datasetTrackerRadio.addEventListener('change', updateOptimizeState);
      datasetCasRadio.addEventListener('change', updateOptimizeState);

      // Instructions on the left, the run (with its blocked-run notice) beside
      // them, and the three option columns and the history across the full width.
      const optimizeCard = createInstructionCard(
        optimizeHeader,
        `${promptBuilderInterfaceText?.promptBuilderInstructionsTitle}`,
        [optimizeDescription, optimizeLearnMoreLine]
      );
      optimizeCard.controls.appendChild(optimizeRunBlock);
      optimizeCard.wide.appendChild(optimizeControls);
      optimizeSection = optimizeCard.element;
      updateOptimizeState();
    }

    // Assemble the page: four steps, each a pane of cards. A card carries its
    // explanatory text in an Instructions box on the left and its controls on
    // the right; blocks that need the room span the full width below.
    const instructionsTitle = `${promptBuilderInterfaceText?.promptBuilderInstructionsTitle}`;
    const instructionText = (text: unknown): HTMLParagraphElement => {
      const paragraph = document.createElement('p');
      paragraph.innerText = `${text}`;
      return paragraph;
    };
    const paneOf = (key: string): HTMLDivElement | undefined =>
      promptBuilderStepper.panes[promptBuilderSteps.findIndex((step) => step.key === key)];
    const setupPane = paneOf('setup') as HTMLDivElement;
    const buildPane = paneOf('build') as HTMLDivElement;
    const finalizePane = paneOf('finalize') as HTMLDivElement;
    const optimizePane = paneOf('optimize');

    promptBuilderContainer.appendChild(promptBuilderHeader);
    promptBuilderContainer.appendChild(promptBuilderStepper.element);

    // --- Step 1, Setup: the project and the prompt-test - chosen once.
    const projectCard = createInstructionCard(promptBuilderProjectHeader, instructionsTitle, [
      promptBuilderDescription,
      instructionText(promptBuilderInterfaceText?.promptBuilderProjectInstructions),
    ]);
    // Everything of the card stands in the block of the selection lock.
    const selectionBlock = selectionLock.block;
    projectCard.controls.appendChild(selectionBlock);
    selectionBlock.appendChild(promptKindSelectorElement);
    selectionBlock.appendChild(promptBuilderProjectSelectorHeader);
    selectionBlock.appendChild(projectFilter.filterRow);
    selectionBlock.appendChild(promptBuilderProjectSelectorDropdown);
    promptBuilderPromptHeader.classList.add('mt-3');
    selectionBlock.appendChild(promptBuilderPromptHeader);
    selectionBlock.appendChild(promptFilter.filterRow);
    selectionBlock.appendChild(promptBuilderPromptSelectorDropdown);
    selectionBlock.appendChild(promptDocSection);
    promptBuilderModalButtonContainer.classList.add('mt-3');
    selectionBlock.appendChild(promptBuilderModalButtonContainer);
    setupPane.appendChild(projectCard.element);
    // Merge searching into the dropdowns themselves: typing in the picker
    // filters the open list live (the separate name/user filter row above
    // still narrows by creator). The underlying selects stay scriptable.
    attachCombobox(promptBuilderProjectSelectorDropdown);
    attachCombobox(promptBuilderPromptSelectorDropdown);

    // --- Step 2, Build & Test: the whole edit-run-read loop on one page, in
    // the order a run uses it - who answers, who judges, what is asked, what
    // came back. The two set-once cards come first, so the prompts sit right
    // above the tracker their runs land in.
    const llmCardInstructions = instructionText(promptBuilderInterfaceText?.promptBuilderModelSelectorInstructions);
    const llmCard = createInstructionCard(promptBuilderModelSelectorHeader, instructionsTitle, [llmCardInstructions]);
    llmCard.controls.appendChild(promptBuilderModelSelectorContainer);
    if (decisionEnabled) llmCard.controls.appendChild(promptBuilderDecisionModelsContainer);
    buildPane.appendChild(llmCard.element);

    // Configuration for how responses are judged lives here (future council or
    // jury options fold in here too); the per-run verdicts and Judge buttons
    // live in the tracker.
    const promptBuilderJudgeHeader = document.createElement('h2');
    promptBuilderJudgeHeader.innerText = `${promptBuilderInterfaceText?.promptBuilderJudgeSectionHeading}`;
    const judgeCard = createInstructionCard(promptBuilderJudgeHeader, instructionsTitle, [
      instructionText(promptBuilderInterfaceText?.promptBuilderJudgeSectionDescription),
    ]);
    judgeCard.controls.appendChild(promptBuilderJudgeControls);
    buildPane.appendChild(judgeCard.element);

    const workbenchCard = createInstructionCard(promptBuilderPromptingHeader, instructionsTitle, [
      promptBulderPromptingExplainer,
    ]);
    const workbenchToolbar = document.createElement('div');
    workbenchToolbar.classList.add('pb-card-toolbar');
    workbenchToolbar.appendChild(promptBuilderVariablesHeader);
    workbenchToolbar.appendChild(promptBuilderVariablesAddButton);
    workbenchToolbar.appendChild(promptBuilderRunExperimentsButton);
    workbenchCard.controls.appendChild(workbenchToolbar);
    workbenchCard.controls.appendChild(promptBuilderVariablesContainer);
    promptBuilderPromptingContainer.classList.add('mt-2');
    workbenchCard.controls.appendChild(promptBuilderPromptingContainer);
    if (decisionEnabled) {
      decisionDesigner.element.classList.add('mt-2');
      workbenchCard.controls.appendChild(decisionDesigner.element);
    }
    workbenchCard.controls.appendChild(promptBuilderRunExperimentError);
    buildPane.appendChild(workbenchCard.element);

    const trackerInstructions = instructionText(promptBuilderInterfaceText?.promptExperimentTrackerInstructions);
    const trackerCard = createInstructionCard(promptExperimentTrackerHeader, instructionsTitle, [trackerInstructions]);
    const trackerToolbar = document.createElement('div');
    trackerToolbar.classList.add('d-flex', 'flex-wrap', 'align-items-center', 'gap-3');
    trackerToolbar.appendChild(promptExperimentSaveButton);
    trackerToolbar.appendChild(promptExperimentSummary);
    trackerCard.controls.appendChild(promptExperimentEmptyHint);
    trackerCard.controls.appendChild(trackerToolbar);
    trackerCard.controls.appendChild(promptExperimentSaveResultContainer);
    trackerCard.controls.appendChild(promptExperimentLegend);
    trackerCard.wide.appendChild(promptExperimentContainer);
    buildPane.appendChild(trackerCard.element);

    // --- Step 3, Optimize - only when the deployment enables it, so the step
    // and its pane exist together or not at all.
    if (optimizeSection && optimizePane) {
      optimizePane.appendChild(optimizeSection);
    }

    // --- Last step, Finalize: manifest the best prompt - configuration first,
    // the action button below it.
    const manifestCard = createInstructionCard(promptExperimentManifestHeader, instructionsTitle, [
      promptExperimentManifestDescription,
    ]);
    manifestCard.controls.appendChild(promptExperimentIntegratedCallDiv);
    manifestCard.controls.appendChild(promptExperimentManifestOptions);
    if (decisionEnabled) manifestCard.controls.appendChild(promptExperimentDecisionManifestOptions);
    manifestCard.controls.appendChild(promptExperimentTlsWarning);
    promptExperimentCreateModelButtonWrapper.classList.add('mt-3');
    manifestCard.controls.appendChild(promptExperimentCreateModelButtonWrapper);
    promptExperimentResultContainer.classList.add('mt-2');
    manifestCard.controls.appendChild(promptExperimentResultContainer);
    finalizePane.appendChild(manifestCard.element);

    // --- Evaluate (decision templates): accuracy, confusion and the confidence
    // threshold over labelled cases - the saved runs with expected answers, or
    // a CAS table with one column per variable and expected_<question> per
    // question. Every case is one call per selected decision model. The step
    // belongs to decision templates, so with them switched off neither the
    // pane nor anything in it exists.
    //
    // The evaluation of a template that is no longer open is taken off the
    // step through this handle (see startLaterStepsAfresh); without the step
    // there is nothing to take off.
    let clearEvaluation = (): void => {};
    if (decisionEnabled) {
      // Counts the times the step was emptied for another template. An
      // evaluation over a CAS table is a loop of calls that can take minutes,
      // and a list of servers is asked for when the CAS source is chosen:
      // what comes back after the template was left belongs to that one and
      // is not drawn (issue #47 - the result of the evaluation of the
      // template before filled the Evaluate step of a new one).
      let evaluationGeneration = 0;
      const evaluatePane = paneOf('evaluate') as HTMLDivElement;
      const evaluationHeader = document.createElement('h2');
      evaluationHeader.innerText = `${promptBuilderInterfaceText?.promptBuilderEvaluateHeading}`;
      const evaluationCard = createInstructionCard(evaluationHeader, instructionsTitle, [
        instructionText(promptBuilderInterfaceText?.promptBuilderEvaluateInstructions),
        instructionText(promptBuilderInterfaceText?.promptBuilderEvaluateThresholdHint),
      ]);
      const evaluationPrefix = `${paneID}-obj-${promptBuilderObject?.id}-evaluate`;
      const evaluationSourceName = `${evaluationPrefix}-source`;
      const makeEvaluationRadio = (value: string, labelText: unknown, checked: boolean): HTMLInputElement => {
        const wrapper = document.createElement('div');
        wrapper.classList.add('form-check');
        const radio = document.createElement('input');
        radio.classList.add('form-check-input');
        radio.type = 'radio';
        radio.name = evaluationSourceName;
        radio.id = `${evaluationSourceName}-${value}`;
        radio.value = value;
        radio.checked = checked;
        const radioLabel = document.createElement('label');
        radioLabel.classList.add('form-check-label');
        radioLabel.htmlFor = radio.id;
        radioLabel.innerText = `${labelText}`;
        wrapper.appendChild(radio);
        wrapper.appendChild(radioLabel);
        evaluationCard.controls.appendChild(wrapper);
        return radio;
      };
      const evaluationSourceLabel = document.createElement('p');
      evaluationSourceLabel.classList.add('fw-bold', 'mb-1');
      evaluationSourceLabel.innerText = `${promptBuilderInterfaceText?.promptBuilderEvaluateSourceLabel}`;
      evaluationCard.controls.appendChild(evaluationSourceLabel);
      const evaluationTrackerRadio = makeEvaluationRadio('tracker', promptBuilderInterfaceText?.promptBuilderEvaluateSourceTracker, true);
      const evaluationCasRadio = makeEvaluationRadio('cas', promptBuilderInterfaceText?.promptBuilderEvaluateSourceCas, false);
      const evaluationCasRow = document.createElement('div');
      evaluationCasRow.classList.add('d-none', 'ms-4', 'mb-2', 'd-flex', 'align-items-center', 'gap-2', 'flex-wrap');
      const makeEvaluationPicker = (kind: string, placeholderText: unknown, width: string): HTMLSelectElement => {
        const select = document.createElement('select');
        select.id = `${evaluationPrefix}-cas-${kind}`;
        select.classList.add('form-select', 'form-select-sm');
        select.style.width = width;
        const placeholder = document.createElement('option');
        placeholder.value = '';
        placeholder.innerText = `${placeholderText}`;
        placeholder.disabled = true;
        placeholder.selected = true;
        select.appendChild(placeholder);
        evaluationCasRow.appendChild(select);
        return select;
      };
      const evaluationServerSelect = makeEvaluationPicker('server', promptBuilderInterfaceText?.promptBuilderOptimizeCasServerPlaceholder, '12rem');
      const evaluationLibSelect = makeEvaluationPicker('lib', promptBuilderInterfaceText?.promptBuilderOptimizeCasLibPlaceholder, '12rem');
      const evaluationTableSelect = makeEvaluationPicker('table', promptBuilderInterfaceText?.promptBuilderOptimizeCasTablePlaceholder, '16rem');
      const fillEvaluationSelect = (select: HTMLSelectElement, names: string[]): void => {
        const placeholder = select.options[0];
        select.innerHTML = '';
        select.appendChild(placeholder);
        placeholder.selected = true;
        names.forEach((name) => {
          const option = document.createElement('option');
          option.value = name;
          option.innerText = name;
          select.appendChild(option);
        });
      };
      let evaluationServersLoaded = false;
      const loadEvaluationServers = async (): Promise<void> => {
        if (evaluationServersLoaded) return;
        evaluationServersLoaded = true;
        const generation = evaluationGeneration;
        try {
          const servers = await getCasServers();
          if (generation === evaluationGeneration) fillEvaluationSelect(evaluationServerSelect, servers);
        } catch (error) {
          console.error('Failed to list the CAS servers.', error);
        }
      };
      evaluationServerSelect.addEventListener('change', async () => {
        fillEvaluationSelect(evaluationLibSelect, []);
        fillEvaluationSelect(evaluationTableSelect, []);
        try {
          fillEvaluationSelect(evaluationLibSelect, await getCaslibs(evaluationServerSelect.value));
        } catch (error) {
          console.error('Failed to list the caslibs.', error);
        }
      });
      evaluationLibSelect.addEventListener('change', async () => {
        fillEvaluationSelect(evaluationTableSelect, []);
        try {
          fillEvaluationSelect(evaluationTableSelect, await getCasTables(evaluationServerSelect.value, evaluationLibSelect.value));
        } catch (error) {
          console.error('Failed to list the CAS tables.', error);
        }
      });
      const evaluationMaxRowsLabel = document.createElement('label');
      evaluationMaxRowsLabel.classList.add('form-label', 'mb-0');
      evaluationMaxRowsLabel.innerText = `${promptBuilderInterfaceText?.promptBuilderEvaluateMaxRowsLabel}`;
      const evaluationMaxRows = document.createElement('input');
      evaluationMaxRows.type = 'number';
      evaluationMaxRows.id = `${evaluationPrefix}-max-rows`;
      evaluationMaxRows.classList.add('form-control', 'form-control-sm');
      evaluationMaxRows.style.width = '6rem';
      evaluationMaxRows.min = '1';
      evaluationMaxRows.max = '1000';
      evaluationMaxRows.value = '100';
      evaluationMaxRowsLabel.htmlFor = evaluationMaxRows.id;
      evaluationCasRow.appendChild(evaluationMaxRowsLabel);
      evaluationCasRow.appendChild(evaluationMaxRows);
      evaluationCard.controls.appendChild(evaluationCasRow);
      const syncEvaluationSource = (): void => {
        const cas = evaluationCasRadio.checked;
        evaluationCasRow.classList.toggle('d-none', !cas);
        if (cas) void loadEvaluationServers();
      };
      evaluationTrackerRadio.addEventListener('change', syncEvaluationSource);
      evaluationCasRadio.addEventListener('change', syncEvaluationSource);
      const evaluationButton = document.createElement('button');
      evaluationButton.type = 'button';
      evaluationButton.id = `${evaluationPrefix}-run`;
      evaluationButton.classList.add('btn', 'btn-primary', 'mt-2');
      evaluationButton.innerText = `${promptBuilderInterfaceText?.promptBuilderEvaluateButton}`;
      const evaluationStatus = document.createElement('p');
      evaluationStatus.id = `${evaluationPrefix}-status`;
      evaluationStatus.classList.add('pb-status', 'mt-2', 'mb-0');
      evaluationCard.controls.appendChild(evaluationButton);
      evaluationCard.controls.appendChild(evaluationStatus);
      const evaluationResults = document.createElement('div');
      evaluationResults.id = `${evaluationPrefix}-results`;
      evaluationCard.wide.appendChild(evaluationResults);
      evaluatePane.appendChild(evaluationCard.element);
      clearEvaluation = (): void => {
        // An evaluation that is still running is no longer this step's.
        evaluationGeneration += 1;
        // The results hold the answers of that template, with a threshold
        // slider per question.
        evaluationResults.innerHTML = '';
        evaluationStatus.innerText = '';
        // The pickers were put back to "choose a server": the servers, and
        // the caslibs and tables listed for the one chosen before, go with
        // it. The servers are asked for again when the CAS source is chosen,
        // as on a page that was just loaded.
        evaluationServersLoaded = false;
        fillEvaluationSelect(evaluationServerSelect, []);
        fillEvaluationSelect(evaluationLibSelect, []);
        fillEvaluationSelect(evaluationTableSelect, []);
      };
      /** The cases and answers of the saved decision runs, per model. */
      const trackerEvaluationCases = (): { questions: DecisionQuestion[]; cases: EvaluationCase[]; answers: Map<string, (DecisionAnswers | null)[]> } => {
        const questions: DecisionQuestion[] = [];
        const cases: EvaluationCase[] = [];
        const answers = new Map<string, (DecisionAnswers | null)[]>();
        promptExperimentTracker.forEach((trackerEntry) => {
          if (trackerEntry.mode !== 'decision' || !trackerEntry.expected || Object.keys(trackerEntry.expected).length === 0) return;
          questionsFromJson(trackerEntry.systemPrompt).forEach((question) => {
            if (!questions.some((known) => known.id === question.id)) questions.push(question);
          });
          const inputs: Record<string, string> = {};
          (trackerEntry.variables ?? []).forEach((variable) => {
            inputs[variable.name] = variable.value;
          });
          const caseIndex = cases.push({ inputs, expected: trackerEntry.expected }) - 1;
          Object.keys(trackerEntry)
            .filter((key) => !TRACKER_META_KEYS.includes(key))
            .forEach((modelName) => {
              const perModel = answers.get(modelName) ?? [];
              perModel[caseIndex] = parseAnswers((trackerEntry[modelName] as ModelExperimentData)?.response);
              answers.set(modelName, perModel);
            });
        });
        return { questions, cases, answers };
      };
      evaluationButton.onclick = async () => {
        evaluationResults.innerHTML = '';
        evaluationStatus.innerText = '';
        evaluationButton.disabled = true;
        // Whether the template this evaluation was started for has been left
        // meanwhile: asked after every wait, before anything is written.
        const generation = evaluationGeneration;
        const left = (): boolean => generation !== evaluationGeneration;
        try {
          if (evaluationTrackerRadio.checked) {
            const { questions, cases, answers } = trackerEvaluationCases();
            if (cases.length === 0) {
              evaluationStatus.innerText = `${promptBuilderInterfaceText?.promptBuilderEvaluateNoRows}`;
              return;
            }
            answers.forEach((perModel, modelName) => {
              const padded = cases.map((_, index) => perModel[index] ?? null);
              evaluationResults.appendChild(
                renderEvaluation(modelName, questions, cases, padded, padded.filter((answer) => answer === null).length, promptBuilderInterfaceText, evaluationPrefix)
              );
            });
            return;
          }
          // A CAS table: every row is a case, every selected model that can
          // take the request answers it.
          const questions = decisionDesigner.getQuestions();
          if (questions.length === 0) {
            evaluationStatus.innerText = `${promptBuilderInterfaceText?.promptBuilderEvaluateNoQuestions}`;
            return;
          }
          const { fit: models, unfit } = fitDecisionModels();
          if (models.length === 0) {
            evaluationStatus.innerText =
              unfit.length > 0
                ? `${promptBuilderInterfaceText?.promptBuilderDecisionSkippedUnfit}`.replace('{models}', unfit.map((model) => model.name).join(', '))
                : `${promptBuilderInterfaceText?.promptBuilderEvaluateNoModels}`;
            return;
          }
          if (!evaluationServerSelect.value || !evaluationLibSelect.value || !evaluationTableSelect.value) {
            evaluationStatus.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeCasMissingToast}`;
            return;
          }
          const maxRows = Math.max(1, Math.min(1000, Number(evaluationMaxRows.value) || 100));
          // From here on calls are in flight until the last model has answered
          // its last row.
          evaluationRunning = true;
          const table = await getCasTableRows(evaluationLibSelect.value, evaluationTableSelect.value, evaluationServerSelect.value, maxRows);
          if (left()) return;
          const columnIndex = (name: string): number => table.columns.findIndex((column) => column.toLowerCase() === name.toLowerCase());
          const variables = collectPromptVariables();
          const missingColumns = variables.map((variable) => variable.name).filter((name) => columnIndex(name) < 0);
          const expectedColumns = questions.filter((question) => columnIndex(`expected_${question.id}`) >= 0);
          if (expectedColumns.length === 0) missingColumns.push(...questions.map((question) => `expected_${question.id}`));
          if (missingColumns.length > 0) {
            evaluationStatus.innerText = `${promptBuilderInterfaceText?.promptBuilderEvaluateCasColumnsMissing}`.replace('{columns}', missingColumns.join(', '));
            return;
          }
          const cases: EvaluationCase[] = table.rows.map((row) => {
            const inputs: Record<string, string> = {};
            variables.forEach((variable) => {
              inputs[variable.name] = String(row[columnIndex(variable.name)] ?? '');
            });
            const expected: Record<string, string> = {};
            expectedColumns.forEach((question) => {
              const value = row[columnIndex(`expected_${question.id}`)];
              if (value !== null && value !== undefined && String(value).trim() !== '') expected[question.id] = String(value);
            });
            return { inputs, expected };
          });
          if (cases.length === 0) {
            evaluationStatus.innerText = `${promptBuilderInterfaceText?.promptBuilderEvaluateNoRows}`;
            return;
          }
          const questionsJson = questionsToJson(questions);
          const stateTemplate = decisionDesigner.stateInput.value;
          const total = cases.length * models.length;
          let done = 0;
          for (const model of models) {
            const answers: (DecisionAnswers | null)[] = [];
            let failed = 0;
            // A few calls in flight at a time: fast enough, gentle on the container.
            const queue = cases.map((evaluationCase, index) => async () => {
              const state = substitutePromptVariables(
                stateTemplate,
                variables.map((variable) => ({ ...variable, value: evaluationCase.inputs[variable.name] ?? '' }))
              );
              const data = (await callSCRDecision(
                promptBuilderObject.SCREndpoint as string,
                model.name,
                state,
                questionsJson,
                decisionCallOptions(model),
                (promptBuilderObject.deploymentType as string) ?? 'k8s'
              )) as Record<string, unknown>;
              const parsed = data?.error ? null : parseAnswers(data?.answers);
              if (!parsed) failed += 1;
              answers[index] = parsed;
              done += 1;
              if (left()) return;
              evaluationStatus.innerText = `${promptBuilderInterfaceText?.promptBuilderEvaluateProgress}`
                .replace('{done}', String(done))
                .replace('{total}', String(total));
            });
            // Once the template was left no further case is asked: the calls
            // that are out come back, the rest of the table is not sent.
            const workers = Array.from({ length: Math.min(4, queue.length) }, async () => {
              while (queue.length > 0 && !left()) await queue.shift()!();
            });
            await Promise.all(workers);
            if (left()) return;
            evaluationResults.appendChild(
              renderEvaluation(model.name, questions, cases, cases.map((_, index) => answers[index] ?? null), failed, promptBuilderInterfaceText, evaluationPrefix)
            );
          }
          evaluationStatus.innerText =
            unfit.length > 0
              ? `${promptBuilderInterfaceText?.promptBuilderDecisionSkippedUnfit}`.replace('{models}', unfit.map((model) => model.name).join(', '))
              : '';
        } catch (error) {
          console.error('The evaluation failed.', error);
          if (!left()) evaluationStatus.innerText = String(error);
        } finally {
          evaluationRunning = false;
          evaluationButton.disabled = false;
        }
      };
    }

    /**
     * Switch the page between its two kinds of template. Everything of the other
     * kind stays in the DOM (hidden), so a prompt of either kind can be opened
     * at any time without rebuilding the page.
     */
    function applyPromptMode(mode: PromptKind): void {
      // With decision templates switched off the page never becomes a decision
      // page, whatever asks for it (a saved run of that kind, for instance).
      if (mode === 'decision' && !decisionEnabled) return;
      const changed = promptMode !== mode;
      promptMode = mode;
      const decision = mode === 'decision';
      // The selector shows the kind of the page - also when the kind was not
      // chosen there but followed from the prompt-test that was opened.
      ownKindSelector?.setChecked(mode);
      hooks?.onKindChanged?.(mode);
      if (changed) {
        // The current selections stay listed (the list helper keeps them), so
        // a prompt opened some other way remains reachable.
        renderProjectOptions();
        renderPromptOptions();
      }
      promptBuilderModelSelectorContainer.style.display = decision ? 'none' : '';
      promptBuilderDecisionModelsContainer.style.display = decision ? '' : 'none';
      promptBuilderModelSelectorHeader.innerText = `${decision ? promptBuilderInterfaceText?.promptBuilderDecisionModelSelectorHeading : promptBuilderInterfaceText?.promptBuilderModelSelectorHeading}`;
      llmCardInstructions.innerText = `${decision ? promptBuilderInterfaceText?.promptBuilderDecisionModelSelectorInstructions : promptBuilderInterfaceText?.promptBuilderModelSelectorInstructions}`;
      judgeCard.element.classList.toggle('d-none', decision);
      promptBuilderPromptingHeader.innerText = `${decision ? promptBuilderInterfaceText?.promptBuilderDecisionHeading : promptBuilderInterfaceText?.promptBuilderPromptingHeader}`;
      promptBulderPromptingExplainer.innerHTML = `${decision ? promptBuilderInterfaceText?.promptBuilderDecisionExplainer : promptBuilderInterfaceText?.promptBulderPromptingExplainer}`;
      promptBuilderPromptingContainer.style.display = decision ? 'none' : 'flex';
      decisionDesigner.element.style.display = decision ? '' : 'none';
      if (STEP_OPTIMIZE >= 0) promptBuilderStepper.setVisible(STEP_OPTIMIZE, !decision);
      if (STEP_EVALUATE >= 0) promptBuilderStepper.setVisible(STEP_EVALUATE, decision);
      promptExperimentIntegratedCallDiv.classList.toggle('d-none', decision);
      promptExperimentManifestOptions.style.display = decision || !promptExperimentIntegratedCallCheckbox.checked ? 'none' : '';
      promptExperimentDecisionManifestOptions.classList.toggle('d-none', !decision);
      updateTlsWarning();
      promptExperimentManifestDescription.innerText = `${decision ? promptBuilderInterfaceText?.promptBuilderManifestDecisionDescription : promptBuilderInterfaceText?.promptBuilderManifestDescription}`;
      promptExperimentManifestHeader.innerText = manifestText('promptBuilderManifestHeading', 'promptBuilderManifestDecisionHeading');
      trackerInstructions.innerText = manifestText('promptExperimentTrackerInstructions', 'promptExperimentDecisionTrackerInstructions');
      // The text of the button while no manifest runs; a running one gives
      // it back with the text of the kind when it ends.
      if (!manifestClickRunning) promptExperimentCreateModelButton.innerText = manifestButtonText();
      renderTrackerLegend();
      updateRunExperimentsButtonState();
      updateManifestButtonState();
    }

    // The page is built and nothing is opened yet: this is what the steps
    // after Setup are put back to when a prompt-test is left.
    promptBuilderStepper.rememberFreshState();

    // Handed out last: setKind ends in applyPromptMode, which reads what the
    // assembly above declares, so the controller must not exist before that.
    hooks?.onReady?.({
      setKind: setPromptKind,
      currentKind: () => promptMode,
      showSetup: () => promptBuilderStepper.goTo(STEP_SETUP, { scroll: true }),
    });

    return promptBuilderContainer;
}
