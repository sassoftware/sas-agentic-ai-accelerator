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
import { getFileContent } from '../api/files-api';
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
import { callSCRLLM } from '../api/scr-api';
import { judgeRun, aggregateBallots, chairmanBreakTie, type JudgeConfidence, type JudgeBallot, type JudgeUsage } from '../api/judge-api';
import {
  resolveJobDefinitionUri,
  launchJob,
  getJob,
  getJobProgressMessages,
  isTerminalJobState,
  type JobExecutionJob,
} from '../api/jobexec-api';
import { createAccordionItem } from '../ui/accordion';
import { showConfirmModal } from '../ui/confirm-modal';
import { showToast } from '../ui/toast';
import { escapeHtml } from '../ui/dom-helpers';
import { renderMarkdown } from '../ui/markdown';
import { isValidDS2VariableName, validateAndCorrectPackageName } from '../util/validation';
import { createTypedOptionControl, optionDisplayLabel, syncSegmentedControl } from '../ui/option-controls';
import Modal from 'bootstrap/js/dist/modal';
import Tooltip from 'bootstrap/js/dist/tooltip';

interface ModelOption {
  default: unknown;
  /** Optional typed-option fields (Model Definition Builder emits these for
   *  non-numeric options): 'enum' renders a segmented selector (or dropdown)
   *  of `values`, 'bool' a checkbox, 'string' a text input. Absent = numeric
   *  input (legacy shape). `label` overrides the displayed option name. */
  type?: 'enum' | 'bool' | 'string';
  values?: string[];
  label?: string;
  description?: string;
  [key: string]: unknown;
}

interface AvailableLLM {
  id: string;
  name: string;
  fileURI?: string;
  options?: Record<string, ModelOption>;
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
  [key: string]: unknown;
}

/** The cost/governance attributes copied from an LLM's Model Manager
 *  registration. Mirrors the fields mdb writes on registered models. */
interface LLMCostAttributes {
  inputTokenCount?: number | null;
  outputTokenCount?: number | null;
  hostingCosts?: number | null;
  llmodelType?: string | null;
  provider?: string | null;
  deploymentId?: string | null;
  endPoint?: string | null;
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
  [modelName: string]: unknown;
}

/** Entry keys that are metadata rather than per-model experiment results. */
const TRACKER_META_KEYS = ['systemPrompt', 'userPrompt', 'author', 'variables', 'manifest', 'judge'];

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
 * the optimization job is the sole writer.
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
  optimizedPrompt?: {
    systemPrompt?: string;
    userPrompt?: string;
    variables?: PromptVariable[];
  } | null;
  producedPromptModelId?: string | null;
  datasetSnapshot?: string | null;
  error?: string | null;
}

/** The outputs an integrated LLM call can return (mirroring the SCR contract). */
const DEFAULT_LLM_OUTPUTS = ['response', 'run_time', 'prompt_length', 'output_length'];

/** The model `function` value for a Prompt Builder prompt. Migrated from the
 *  legacy 'Prompting' value, which is still recognised when listing prompts. */
const PROMPT_FUNCTION = 'prompt template';
const LEGACY_PROMPT_FUNCTION = 'Prompting';
/** Server-side filter that surfaces prompt models of either function value. */
const PROMPT_FUNCTION_FILTER = `or(eq(function,'${PROMPT_FUNCTION}'),eq(function,'${LEGACY_PROMPT_FUNCTION}'))`;
/** Documentation attributes captured per prompt, mirroring mdb model-card keys. */
const PROMPT_DOC_FIELDS = [
  'modelPurpose', 'intendedUse', 'expectedBenefit', 'outOfScopeUseCases', 'limitations',
] as const;
type PromptDocField = (typeof PROMPT_DOC_FIELDS)[number];
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
  return {
    inputTokenCount: numOrNull(body.inputTokenCount),
    outputTokenCount: numOrNull(body.outputTokenCount),
    hostingCosts: numOrNull(body.hostingCosts),
    llmodelType: str(body.llmodelType),
    provider: str(body.provider),
    deploymentId: str(body.deploymentId),
    endPoint: str(body.endPoint),
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
}

/**
 * Handle a click on the "Open in SAS Model Manager" link. Tries to open a new
 * browser tab; when running inside VA's sandboxed DDC iframe (no 'allow-popups'),
 * window.open is blocked, so we copy the URL to the clipboard and briefly show a
 * hint instead. The link keeps its href, so the browser's own right-click
 * "Open link in new tab" always works regardless.
 */
function openModelManagerLink(
  event: MouseEvent,
  anchor: HTMLAnchorElement,
  interfaceText: PromptBuilderText
): void {
  event.preventDefault();
  const url = anchor.href;

  const opened = window.open(url, '_blank', 'noopener,noreferrer');
  if (opened) return;

  // Popup blocked by the sandbox — copy the link and let the user open it.
  copyToClipboard(url);
  const original = anchor.innerHTML;
  anchor.textContent =
    (interfaceText?.promptBuilderOpenInMMCopied as string) ??
    'Link copied — open it in a new tab';
  window.setTimeout(() => {
    anchor.innerHTML = original;
  }, 2500);
}

/** Copy text to the clipboard, falling back to a hidden-textarea + execCommand. */
function copyToClipboard(text: string): void {
  try {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      navigator.clipboard.writeText(text).catch(() => legacyCopyToClipboard(text));
      return;
    }
  } catch {
    /* fall through to the legacy path */
  }
  legacyCopyToClipboard(text);
}

function legacyCopyToClipboard(text: string): void {
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  try {
    document.execCommand('copy');
  } catch {
    /* clipboard unavailable — nothing more we can do */
  }
  document.body.removeChild(textarea);
}

/**
 * Parse an SCR options string (`{key:value,key:value}`, unquoted) back into an
 * object. Values are coerced to number/boolean when they look like one, else
 * kept as strings — so non-numeric options (`reasoning_effort:medium`,
 * `API_KEY:OpenAI`, `normalize:true`) round-trip without breaking parsing.
 */
function parseScrOptionsString(optionsStr: string | null | undefined): Record<string, unknown> {
  const result: Record<string, unknown> = {};
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

export async function buildPromptBuilder(
  definition: PromptBuilderConfig,
  paneID: string,
  interfaceText?: InterfaceText
): Promise<HTMLElement> {
    const promptBuilderObject = definition;
    const promptBuilderInterfaceText = (interfaceText?.promptBuilder ?? {}) as PromptBuilderText;
    const VIYA = getAppState().config.viyaHost;

    // Experiment-tracker rows for THIS object instance. Kept in the closure
    // (not on window) so two prompt-builder panes don't clobber each other.
    let petRows: PETRow[] = [];

    const promptBuilderContainer = document.createElement('div');
    promptBuilderContainer.setAttribute('id', `${paneID}-obj-${promptBuilderObject?.id}`);

    // Add the intro piece to the Prompt Builder
    const promptBuilderHeader = document.createElement('h1');
    promptBuilderHeader.innerText = promptBuilderInterfaceText?.promptBuilderHeading as string;
    const promptBuilderDescription = document.createElement('p');
    promptBuilderDescription.innerText = promptBuilderInterfaceText?.promptBuilderDescription as string;

    // Add the project selection/creation
    const promptBuilderProjectHeader = document.createElement('h2');
    promptBuilderProjectHeader.innerText = promptBuilderInterfaceText?.promptBuilderProjectHeader as string;
    // Full project/prompt lists with their metadata; the dropdowns render a
    // filtered view of these, so long lists stay searchable.
    let promptBuilderAllProjects: DropdownOption[] = [];
    let promptBuilderProjectPrompts: DropdownOption[] = [];

    // Select from existing projects
    const promptBuilderProjectSelectorHeader = document.createElement('h3');
    promptBuilderProjectSelectorHeader.innerText = `${promptBuilderInterfaceText?.projectSelect}:`;
    const promptBuilderProjectSelectorDropdown = document.createElement('select');
    promptBuilderProjectSelectorDropdown.setAttribute('class', 'form-select');
    promptBuilderProjectSelectorDropdown.setAttribute('id', `${promptBuilderObject?.id}-project-dropdown`);
    promptBuilderProjectSelectorDropdown.onchange = async function () {
      const self = this as unknown as HTMLSelectElement;
      // Reset the in-memory experiment state of the previously selected prompt
      resetExperimentTrackerState();
      // Reset the prompt list and its filters
      promptBuilderProjectPrompts = [];
      promptFilter.nameInput.value = '';
      renderPromptOptions();

      // Get the prompts from the selected projects
      const currentProject = self.options[self.selectedIndex].value;
      // Enable project deletion only for a real project selection
      deleteProjectButton.disabled = currentProject === `${promptBuilderInterfaceText?.projectSelect}`;
      try {
        promptBuilderProjectPrompts = await getModelProjectModels(currentProject, PROMPT_FUNCTION_FILTER);
      } catch (error) {
        console.error('Failed to load prompts for the selected project.', error);
        promptBuilderProjectPrompts = [];
      }
      updateUserFilterOptions(promptFilter.userSelect, promptBuilderProjectPrompts);
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
    const promptDocSection = document.createElement('details');
    promptDocSection.classList.add('pb-doc-section', 'mt-2', 'mb-2');
    const promptDocSummary = document.createElement('summary');
    promptDocSummary.classList.add('fw-semibold');
    promptDocSummary.innerText = promptBuilderInterfaceText?.promptBuilderDocSectionLabel as string;
    promptDocSection.appendChild(promptDocSummary);
    const promptDocHint = document.createElement('p');
    promptDocHint.classList.add('small', 'text-muted', 'mt-1', 'mb-2');
    promptDocHint.innerText = promptBuilderInterfaceText?.promptBuilderDocSectionHint as string;
    promptDocSection.appendChild(promptDocHint);
    const DOC_FIELD_I18N: Record<PromptDocField, { label: string; info: string }> = {
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
    };
    const promptDocFieldEls = {} as Record<PromptDocField, HTMLTextAreaElement>;
    for (const field of PROMPT_DOC_FIELDS) {
      const wrap = document.createElement('div');
      wrap.classList.add('mb-2');
      wrap.appendChild(createOptionLabel(DOC_FIELD_I18N[field].label, DOC_FIELD_I18N[field].info));
      const textarea = document.createElement('textarea');
      textarea.classList.add('form-control');
      textarea.rows = 2;
      textarea.id = `${promptBuilderObject?.id}-doc-${field}`;
      promptDocFieldEls[field] = textarea;
      wrap.appendChild(textarea);
      promptDocSection.appendChild(wrap);
    }
    const promptDocSaveButton = document.createElement('button');
    promptDocSaveButton.type = 'button';
    promptDocSaveButton.classList.add('btn', 'btn-outline-primary', 'btn-sm');
    promptDocSaveButton.innerText = promptBuilderInterfaceText?.promptBuilderSaveDocumentationButton as string;
    promptDocSaveButton.disabled = true;
    promptDocSaveButton.onclick = async () => {
      if (!currentDocPromptId) return;
      const attrs: Record<string, unknown> = {};
      for (const field of PROMPT_DOC_FIELDS) attrs[field] = promptDocFieldEls[field].value;
      const previousLabel = promptDocSaveButton.innerText;
      promptDocSaveButton.disabled = true;
      promptDocSaveButton.innerHTML = `<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>`;
      let status = 500;
      try {
        status = await updateModelAttributes(currentDocPromptId, attrs);
      } catch (error) {
        console.error('Failed to save prompt documentation.', error);
      }
      promptDocSaveButton.innerText = previousLabel;
      promptDocSaveButton.disabled = false;
      showToast(
        status < 300
          ? (promptBuilderInterfaceText?.promptBuilderDocSaved as string)
          : (promptBuilderInterfaceText?.promptBuilderDocSaveFailed as string)
      );
    };
    promptDocSection.appendChild(promptDocSaveButton);

    // Load the selected prompt's documentation into the fields (and migrate a
    // legacy `function` value to the current one, for the user). Best-effort:
    // a fetch failure just leaves the section empty and disabled.
    async function loadPromptDocumentation(promptId: string): Promise<void> {
      currentDocPromptId = '';
      for (const field of PROMPT_DOC_FIELDS) promptDocFieldEls[field].value = '';
      promptDocSaveButton.disabled = true;
      if (!promptId || promptId === `${promptBuilderInterfaceText?.promptSelect}`) return;
      let details: Record<string, unknown> | null = null;
      try {
        details = await getModelDetails(promptId);
      } catch (error) {
        console.error('Failed to load prompt documentation.', error);
        return;
      }
      if (!details) return;
      for (const field of PROMPT_DOC_FIELDS) {
        const value = details[field];
        promptDocFieldEls[field].value = value == null ? '' : String(value);
      }
      currentDocPromptId = promptId;
      promptDocSaveButton.disabled = false;
      if (details.function === LEGACY_PROMPT_FUNCTION) {
        try {
          await updateModelAttributes(promptId, { function: PROMPT_FUNCTION });
        } catch {
          /* best-effort migration */
        }
      }
    }

    promptBuilderPromptSelectorDropdown.onchange = async function () {
      const self = this as unknown as HTMLSelectElement;
      // Reset the in-memory experiment state of the previously selected prompt
      resetExperimentTrackerState();
      const promptBuilderPromptSelectedModelID = self.options[self.selectedIndex].value;
      // Load the prompt's documentation (and migrate a legacy function value)
      await loadPromptDocumentation(promptBuilderPromptSelectedModelID);
      // Get the ID of a previously created Prompt Experiment Tracker and delete it
      let promptBuilderAvailablePTE: Awaited<ReturnType<typeof getModelContents>> = [];
      try {
        promptBuilderAvailablePTE = await getModelContents(promptBuilderPromptSelectedModelID);
      } catch (error) {
        console.error('Failed to load model contents for the selected prompt.', error);
      }
      for (const promptBuilderAvailablepte in promptBuilderAvailablePTE) {
        if (promptBuilderAvailablePTE[promptBuilderAvailablepte]?.name === 'Prompt-Experiment-Tracker.json') {
          // A tracker that cannot be parsed must not abort the handler — the
          // Model Manager link and delete buttons below still have to work.
          try {
            // Reset the prompt tracker to nothing
            promptExperimentTrackerRunID = 0;
            const promptBuilderCurrentPTE = await getFileContent(promptBuilderAvailablePTE[promptBuilderAvailablepte].fileUri!);
            const promptBuilderCurrentPTEContent: PETRow[] = await promptBuilderCurrentPTE.json();
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
                (promptBuilderPreviousExperiment[promptBuilderPreviousExperiment.length - 1] as Record<string, unknown>)[value?.model] = {
                  best_prompt: value?.best_prompt,
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
            // Bring the most recent best prompt straight into the workbench
            loadMostRecentBestRun();
          } catch (error) {
            console.error('Failed to load the Prompt-Experiment-Tracker for the selected prompt.', error);
          }
        }
      }
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
    function createListFilter(
      filterIdPrefix: string,
      onFilterChange: () => void
    ): { filterRow: HTMLDivElement; nameInput: HTMLInputElement; userSelect: HTMLSelectElement } {
      const filterRow = document.createElement('div');
      filterRow.classList.add('row', 'g-2', 'mb-2', 'pb-list-filter');
      const nameColumn = document.createElement('div');
      nameColumn.classList.add('col-md-8');
      const nameInput = document.createElement('input');
      nameInput.type = 'search';
      nameInput.id = `${filterIdPrefix}-name`;
      nameInput.classList.add('form-control', 'form-control-sm');
      nameInput.placeholder = `${promptBuilderInterfaceText?.promptBuilderFilterNamePlaceholder}`;
      nameInput.setAttribute('aria-label', `${promptBuilderInterfaceText?.promptBuilderFilterNamePlaceholder}`);
      nameInput.oninput = onFilterChange;
      nameColumn.appendChild(nameInput);
      const userColumn = document.createElement('div');
      userColumn.classList.add('col-md-4');
      const userSelect = document.createElement('select');
      userSelect.id = `${filterIdPrefix}-user`;
      userSelect.classList.add('form-select', 'form-select-sm');
      userSelect.setAttribute('aria-label', `${promptBuilderInterfaceText?.promptBuilderFilterUserLabel}`);
      userSelect.onchange = onFilterChange;
      userColumn.appendChild(userSelect);
      filterRow.appendChild(nameColumn);
      filterRow.appendChild(userColumn);
      updateUserFilterOptions(userSelect, []);
      return { filterRow, nameInput, userSelect };
    }

    // Rebuild a user filter from the distinct createdBy/modifiedBy values
    function updateUserFilterOptions(userSelect: HTMLSelectElement, items: DropdownOption[]): void {
      const previousUser = userSelect.value;
      const users = new Set<string>();
      items.forEach((item) => {
        if (typeof item.createdBy === 'string' && item.createdBy) users.add(item.createdBy);
        if (typeof item.modifiedBy === 'string' && item.modifiedBy) users.add(item.modifiedBy);
      });
      userSelect.innerHTML = '';
      const allUsersOption = document.createElement('option');
      allUsersOption.value = '';
      allUsersOption.innerText = `${promptBuilderInterfaceText?.promptBuilderFilterUserAll}`;
      userSelect.appendChild(allUsersOption);
      [...users].sort().forEach((user) => {
        const userOption = document.createElement('option');
        userOption.value = user;
        userOption.innerText = user;
        userSelect.appendChild(userOption);
      });
      userSelect.value = users.has(previousUser) ? previousUser : '';
    }

    function renderFilteredOptions(
      dropdown: HTMLSelectElement,
      items: DropdownOption[],
      nameInput: HTMLInputElement,
      userSelect: HTMLSelectElement,
      placeholderText: string
    ): void {
      const selectedValue = dropdown.value;
      const nameFilter = nameInput.value.trim().toLowerCase();
      const userFilter = userSelect.value;
      dropdown.innerHTML = '';
      const placeholderOption = document.createElement('option');
      placeholderOption.value = placeholderText;
      placeholderOption.innerHTML = placeholderText;
      dropdown.appendChild(placeholderOption);
      items
        .filter(
          (item) =>
            item.value === selectedValue ||
            (String(item.innerHTML ?? '').toLowerCase().includes(nameFilter) &&
              (userFilter === '' || item.createdBy === userFilter || item.modifiedBy === userFilter))
        )
        .forEach((item) => {
          const listOption = document.createElement('option');
          listOption.value = item.value;
          listOption.innerHTML = item.innerHTML;
          dropdown.appendChild(listOption);
        });
      dropdown.value = [...dropdown.options].some((option) => option.value === selectedValue)
        ? selectedValue
        : placeholderText;
    }

    const projectFilter = createListFilter(`${promptBuilderObject?.id}-project-filter`, () => renderProjectOptions());
    const promptFilter = createListFilter(`${promptBuilderObject?.id}-prompt-filter`, () => renderPromptOptions());
    function renderProjectOptions(): void {
      renderFilteredOptions(
        promptBuilderProjectSelectorDropdown,
        promptBuilderAllProjects,
        projectFilter.nameInput,
        projectFilter.userSelect,
        `${promptBuilderInterfaceText?.projectSelect}`
      );
    }
    function renderPromptOptions(): void {
      renderFilteredOptions(
        promptBuilderPromptSelectorDropdown,
        promptBuilderProjectPrompts,
        promptFilter.nameInput,
        promptFilter.userSelect,
        `${promptBuilderInterfaceText?.promptSelect}`
      );
    }

    // Get all projects in the specified repository and render the filterable list
    promptBuilderAllProjects = await getModelProjects(`contains(tags,'Prompt-Engineering')`);
    updateUserFilterOptions(projectFilter.userSelect, promptBuilderAllProjects);
    renderProjectOptions();
    renderPromptOptions();

    // Add the creation prompt buttons and modals. The row is a flex container
    // so the destructive actions can sit right-aligned, away from the rest.
    const promptBuilderModalButtonContainer = document.createElement('div');
    promptBuilderModalButtonContainer.setAttribute('id', `${promptBuilderObject?.id}-modal-button-container`);
    promptBuilderModalButtonContainer.classList.add('d-flex', 'flex-wrap', 'align-items-center');

    // Function to call when creating a new project
    async function promptBuilderCreateProject(): Promise<void> {
      const modal = document.getElementById('promptBuilderCreateProjectModal');
      if (modal) {
        const btn = (modal.lastChild as HTMLElement)?.lastChild?.lastChild?.lastChild as HTMLButtonElement | null;
        if (btn) btn.disabled = true;
      }
      const promptBuilderRepositoryInformation = await getModelRepositoryInformation(promptBuilderObject?.modelRepositoryID as string);
      const promptBuilderNewProjectDefinition = {
        name: (document.getElementById('promptBuilderCreateProjectName') as HTMLInputElement).value,
        description: (document.getElementById('promptBuilderCreateProjectDescription') as HTMLInputElement).value,
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
        tags: ['LLM', 'Prompt-Engineering'],
      };
      const promptBuilderNewProjectObject = await createModelProject(promptBuilderNewProjectDefinition);
      promptBuilderAllProjects.push({
        value: `${promptBuilderNewProjectObject?.id}`,
        innerHTML: `${promptBuilderNewProjectObject?.name}`,
        createdBy: promptBuilderNewProjectObject?.createdBy ?? getAppState().userName ?? undefined,
        modifiedBy: promptBuilderNewProjectObject?.modifiedBy ?? getAppState().userName ?? undefined,
      });
      // Clear the filters so the new project is visible, then select it
      projectFilter.nameInput.value = '';
      projectFilter.userSelect.value = '';
      updateUserFilterOptions(projectFilter.userSelect, promptBuilderAllProjects);
      renderProjectOptions();
      // Set the newly created project as the currently selected project
      promptBuilderProjectSelectorDropdown.value = `${promptBuilderNewProjectObject?.id}`;
      promptBuilderProjectSelectorDropdown.dispatchEvent(new Event('change'));
      if (modal) {
        const btn = (modal.lastChild as HTMLElement)?.lastChild?.lastChild?.lastChild as HTMLButtonElement | null;
        if (btn) btn.disabled = false;
      }
      const modalInstance = Modal.getInstance(document.getElementById('promptBuilderCreateProjectModal')!);
      if (modalInstance) modalInstance.hide();
    }

    // Function to call when creating a new prompt
    async function promptBuilderCreatePrompt(): Promise<void> {
      const modal = document.getElementById('promptBuilderCreatePromptModal');
      if (modal) {
        const btn = (modal.lastChild as HTMLElement)?.lastChild?.lastChild?.lastChild as HTMLButtonElement | null;
        if (btn) btn.disabled = true;
      }
      const promptBuilderNewPromptDefinition = {
        name: (document.getElementById('promptBuilderCreatePromptName') as HTMLInputElement).value,
        description: (document.getElementById('promptBuilderCreatePromptDescription') as HTMLInputElement).value,
        function: PROMPT_FUNCTION,
        tool: 'Prompt-Builder',
        modelere: getAppState().userName,
        projectId: promptBuilderProjectSelectorDropdown.options[promptBuilderProjectSelectorDropdown.selectedIndex].value,
        algorithm: 'Prompt-Template',
        tags: ['LLM', 'Prompt-Template'],
        scoreCodeType: 'python',
      };
      const promptBuilderNewPromptObject = await createModel(promptBuilderNewPromptDefinition);
      promptBuilderProjectPrompts.push({
        value: `${promptBuilderNewPromptObject?.items?.[0]?.id}`,
        innerHTML: `${promptBuilderNewPromptObject?.items?.[0]?.name}`,
        createdBy: (promptBuilderNewPromptObject?.items?.[0]?.createdBy as string | undefined) ?? getAppState().userName ?? undefined,
        modifiedBy: (promptBuilderNewPromptObject?.items?.[0]?.modifiedBy as string | undefined) ?? getAppState().userName ?? undefined,
      });
      // Clear the filters so the new prompt is visible, then select it
      promptFilter.nameInput.value = '';
      promptFilter.userSelect.value = '';
      updateUserFilterOptions(promptFilter.userSelect, promptBuilderProjectPrompts);
      renderPromptOptions();
      // Set the newly created project as the currently selected project
      promptBuilderPromptSelectorDropdown.value = `${promptBuilderNewPromptObject?.items?.[0]?.id}`;
      promptBuilderPromptSelectorDropdown.dispatchEvent(new Event('change'));
      if (modal) {
        const btn = (modal.lastChild as HTMLElement)?.lastChild?.lastChild?.lastChild as HTMLButtonElement | null;
        if (btn) btn.disabled = false;
      }
      const modalInstance = Modal.getInstance(document.getElementById('promptBuilderCreatePromptModal')!);
      if (modalInstance) modalInstance.hide();
    }

    // Clear all in-memory experiment state and deactivate the prompt-bound
    // actions. Used when the project/prompt selection changes and after a
    // prompt or project was deleted.
    function resetExperimentTrackerState(): void {
      const prommpExperimentTargetContainer = document.getElementById(`${paneID}-obj-${promptBuilderObject?.id}-pet`);
      if (prommpExperimentTargetContainer) prommpExperimentTargetContainer.innerHTML = '';
      promptExperimentTracker = [];
      promptExperimentTrackerRunID = 0;
      petRows = [];
      experimentsModified = false;
      promptExperimentResultContainer.innerHTML = '';
      promptExperimentSaveResultContainer.innerHTML = '';
      // Reset the manifest panel to its defaults; loading a run afterwards
      // re-applies that run's stored configuration
      applyManifestConfig(null);
      updateTrackerEmptyState();
      updateManifestButtonState();
      openInMMButton.classList.add('disabled');
      openInMMButton.setAttribute('aria-disabled', 'true');
      openInMMButton.removeAttribute('href');
      openInMMButton.onclick = null;
      deletePromptButton.disabled = true;
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
        const confirmed = await showConfirmModal({
          title: `${promptBuilderInterfaceText?.promptBuilderDeletePromptTitle} ${promptModelName}`,
          body: buildUsageBody(decisions),
          confirmText: `${promptBuilderInterfaceText?.promptBuilderDeleteConfirmButton}`,
          cancelText: `${promptBuilderInterfaceText?.promptBuilderDeleteCancelButton}`,
        });
        if (!confirmed) return;
        const deleteStatus = await deleteModel(promptModelID);
        if (deleteStatus === 204) {
          promptBuilderProjectPrompts = promptBuilderProjectPrompts.filter((item) => item.value !== promptModelID);
          updateUserFilterOptions(promptFilter.userSelect, promptBuilderProjectPrompts);
          promptBuilderPromptSelectorDropdown.value = `${promptBuilderInterfaceText?.promptSelect}`;
          renderPromptOptions();
          resetExperimentTrackerState();
        } else {
          showToast(`${promptBuilderInterfaceText?.promptBuilderDeleteFailedResponse}`);
        }
      } finally {
        deletePromptButton.innerText = `${promptBuilderInterfaceText?.promptBuilderDeletePromptButton}`;
        deletePromptButton.disabled =
          promptBuilderPromptSelectorDropdown.value === `${promptBuilderInterfaceText?.promptSelect}`;
      }
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
        for (const projectPrompt of projectPrompts) {
          const modelDeleteStatus = await deleteModel(projectPrompt.value);
          if (modelDeleteStatus !== 204) {
            showToast(`${promptBuilderInterfaceText?.promptBuilderDeleteFailedResponse}`);
            return;
          }
        }
        const projectDeleteStatus = await deleteModelProject(projectID);
        if (projectDeleteStatus === 204) {
          promptBuilderAllProjects = promptBuilderAllProjects.filter((item) => item.value !== projectID);
          updateUserFilterOptions(projectFilter.userSelect, promptBuilderAllProjects);
          promptBuilderProjectSelectorDropdown.value = `${promptBuilderInterfaceText?.projectSelect}`;
          renderProjectOptions();
          // Reset the prompt selector to the placeholder-only state
          promptBuilderProjectPrompts = [];
          updateUserFilterOptions(promptFilter.userSelect, promptBuilderProjectPrompts);
          promptBuilderPromptSelectorDropdown.value = `${promptBuilderInterfaceText?.promptSelect}`;
          renderPromptOptions();
          resetExperimentTrackerState();
        } else {
          showToast(`${promptBuilderInterfaceText?.promptBuilderDeleteFailedResponse}`);
        }
      } finally {
        deleteProjectButton.innerText = `${promptBuilderInterfaceText?.promptBuilderDeleteProjectButton}`;
        deleteProjectButton.disabled =
          promptBuilderProjectSelectorDropdown.value === `${promptBuilderInterfaceText?.projectSelect}`;
      }
    }

    function promptBuilderCreateModal(
      tmpModalContainer: HTMLElement,
      tmpPrefix: string,
      tmpModalText: ModalText,
      tmpActionFunction: () => void
    ): void {
      // Create the button that triggers the modal
      const createModalButtonToggle = document.createElement('button');
      createModalButtonToggle.type = 'button';
      createModalButtonToggle.classList.add('btn', 'btn-primary');
      createModalButtonToggle.setAttribute('data-bs-toggle', 'modal');
      createModalButtonToggle.setAttribute('data-bs-target', `#${tmpPrefix}Modal`);
      createModalButtonToggle.innerHTML = tmpModalText?.modalTitle ?? '';
      // Create the modal wrapper
      const createModalWrapper = document.createElement('div');
      createModalWrapper.classList.add('modal', 'fade');
      createModalWrapper.setAttribute('id', `${tmpPrefix}Modal`);
      createModalWrapper.setAttribute('tabindex', '-1');
      // Create the modal dialog
      const createModalModalDialog = document.createElement('div');
      createModalModalDialog.classList.add('modal-dialog');
      // Create the modal content
      const createModalModalContent = document.createElement('div');
      createModalModalContent.classList.add('modal-content');
      // Create the modal header
      const createModalModalHeader = document.createElement('div');
      createModalModalHeader.classList.add('modal-header');
      // Create the modal title
      const createModalModalTitle = document.createElement('h2');
      createModalModalTitle.classList.add('modal-title', 'fs-5');
      createModalModalTitle.innerHTML = tmpModalText?.modalTitle ?? '';
      // Create the modal close button
      const createModalModalCloseButton = document.createElement('button');
      createModalModalCloseButton.type = 'button';
      createModalModalCloseButton.classList.add('btn-close');
      createModalModalCloseButton.setAttribute('data-bs-dismiss', 'modal');
      createModalModalCloseButton.setAttribute('aria-label', 'Close');
      // Create the modal body
      const createModalModalBody = document.createElement('div');
      createModalModalBody.classList.add('modal-body');
      // Optional explanatory description shown above the inputs
      if (tmpModalText?.modalDescription) {
        const createModalModalDescription = document.createElement('p');
        createModalModalDescription.innerText = tmpModalText.modalDescription;
        createModalModalBody.appendChild(createModalModalDescription);
      }
      // Create the first modal input
      const createModalBodyInput1Text = document.createElement('span');
      createModalBodyInput1Text.innerHTML = `${tmpModalText?.nameLabel}:`;
      const createModalBodyInput1 = document.createElement('input');
      createModalBodyInput1.setAttribute('type', 'text');
      createModalBodyInput1.setAttribute('placeholder', tmpModalText?.nameLabel ?? '');
      createModalBodyInput1.setAttribute('id', `${tmpPrefix}Name`);
      // Create the second modal input
      const createModalBodyInput2Text = document.createElement('span');
      createModalBodyInput2Text.innerHTML = `${tmpModalText?.descriptionLabel}:`;
      const createModalBodyInput2 = document.createElement('input');
      createModalBodyInput2.setAttribute('type', 'text');
      createModalBodyInput2.setAttribute('placeholder', tmpModalText?.descriptionLabel ?? '');
      createModalBodyInput2.setAttribute('id', `${tmpPrefix}Description`);
      // Create the modal footer
      const createModalModalFooter = document.createElement('div');
      createModalModalFooter.classList.add('modal-footer');
      // Create the modal footer close button
      const createModalModalFooterButton = document.createElement('button');
      createModalModalFooterButton.type = 'button';
      createModalModalFooterButton.classList.add('btn', 'btn-secondary');
      createModalModalFooterButton.setAttribute('data-bs-dismiss', 'modal');
      createModalModalFooterButton.innerHTML = tmpModalText?.closeButtonText ?? '';
      // Create the modal footer save button
      const createModalModalFooterButton2 = document.createElement('button');
      createModalModalFooterButton2.type = 'button';
      createModalModalFooterButton2.classList.add('btn', 'btn-primary');
      createModalModalFooterButton2.innerHTML = tmpModalText?.saveButtonText ?? '';
      createModalModalFooterButton2.onclick = () => {
        tmpActionFunction();
      };
      // Append elements together
      createModalModalHeader.appendChild(createModalModalTitle);
      createModalModalHeader.appendChild(createModalModalCloseButton);
      createModalModalContent.appendChild(createModalModalHeader);
      createModalModalBody.appendChild(createModalBodyInput1Text);
      createModalModalBody.appendChild(createModalBodyInput1);
      createModalModalBody.appendChild(document.createElement('br'));
      createModalModalBody.appendChild(createModalBodyInput2Text);
      createModalModalBody.appendChild(createModalBodyInput2);
      createModalModalContent.appendChild(createModalModalBody);
      createModalModalFooter.appendChild(createModalModalFooterButton);
      createModalModalFooter.appendChild(createModalModalFooterButton2);
      createModalModalContent.appendChild(createModalModalFooter);
      createModalModalDialog.appendChild(createModalModalContent);
      createModalWrapper.appendChild(createModalModalDialog);

      // Add to the modal container
      tmpModalContainer.appendChild(createModalButtonToggle);
      tmpModalContainer.appendChild(createModalWrapper);
    }

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
    openInMMButton.classList.add('btn', 'btn-primary', 'disabled');
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
    function createOptionLabel(labelText: string, infoHtml: string): HTMLDivElement {
      const labelContainer = document.createElement('div');
      labelContainer.classList.add('info-container');
      labelContainer.append(`${labelText}: `);
      const infoIcon = document.createElement('span');
      infoIcon.classList.add('info-icon');
      infoIcon.innerHTML = '&#x2139;&#xFE0F;';
      infoIcon.setAttribute('tabindex', '0');
      infoIcon.setAttribute('role', 'button');
      infoIcon.setAttribute('aria-label', labelText);
      infoIcon.setAttribute('data-bs-toggle', 'tooltip');
      new Tooltip(infoIcon, { title: infoHtml, html: true, container: 'body' });
      labelContainer.appendChild(infoIcon);
      return labelContainer;
    }

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

        if (model?.options?.temperature) {
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

        if (model?.options?.top_p) {
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

        if (model?.options?.top_k) {
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

        if (model?.options?.max_length) {
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

        if (model?.options?.max_tokens) {
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
        if (model?.options?.max_completion_tokens) {
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

        if (model?.options?.max_new_tokens) {
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
        const legacyRenderedOptions = new Set([
          'API_KEY', 'temperature', 'top_p', 'top_k', 'max_length', 'max_tokens',
          'max_new_tokens', 'max_completion_tokens',
        ]);
        Object.entries(model?.options ?? {}).forEach(([optionKey, optionMeta]) => {
          if (legacyRenderedOptions.has(optionKey)) return;
          const tooltipText = String(optionMeta?.description ?? optionKey);
          optionsDiv.appendChild(createOptionLabel(optionDisplayLabel(optionKey, optionMeta), tooltipText));
          optionsDiv.appendChild(createTypedOptionControl(optionKey, optionMeta, `${optionKey}${index}`));
        });

        modelDiv.appendChild(checkbox);
        modelDiv.appendChild(label);
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
              const currentOptions = await getFileContent(availableLLM.fileURI!);
              availableLLM.options = await currentOptions.json();
            }
          }
        } catch (error) {
          console.error(`Failed to load options for LLM ${availableLLM.name}.`, error);
        }
      })
    );
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

    // Add the prompting inputs
    const promptBuilderPromptingHeader = document.createElement('h2');
    promptBuilderPromptingHeader.innerText = promptBuilderInterfaceText?.promptBuilderPromptingHeader as string;
    const promptBulderPromptingExplainer = document.createElement('p');
    promptBulderPromptingExplainer.innerHTML = promptBuilderInterfaceText?.promptBulderPromptingExplainer as string;

    // Variables manager: define name/description/type/value rows whose values
    // are substituted into the prompts via the {{variableName}} syntax.
    const promptBuilderVariablesHeader = document.createElement('h3');
    promptBuilderVariablesHeader.innerText = `${promptBuilderInterfaceText?.promptBuilderVariablesHeading}`;
    const promptBuilderVariablesDescription = document.createElement('p');
    promptBuilderVariablesDescription.innerText = `${promptBuilderInterfaceText?.promptBuilderVariablesDescription}`;
    const promptBuilderVariablesContainer = document.createElement('div');
    promptBuilderVariablesContainer.id = `${paneID}-obj-${promptBuilderObject?.id}-variables`;
    const promptBuilderVariablesAddButton = document.createElement('button');
    promptBuilderVariablesAddButton.type = 'button';
    promptBuilderVariablesAddButton.classList.add('btn', 'btn-secondary');
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
    // empty or duplicate name are highlighted by validation and skipped here).
    function collectPromptVariables(): PromptVariable[] {
      validatePromptVariableRows();
      const variables: PromptVariable[] = [];
      const seenNames = new Set<string>();
      promptBuilderVariablesContainer.querySelectorAll('.pb-variable-row').forEach((row) => {
        const name = (row.querySelector('.pb-var-name') as HTMLInputElement).value.trim();
        if (!isValidDS2VariableName(name) || seenNames.has(name)) return;
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

    // Start running experiments
    const promptBuilderRunExperimentsButton = document.createElement('button');
    promptBuilderRunExperimentsButton.setAttribute('type', 'button');
    promptBuilderRunExperimentsButton.setAttribute('class', 'btn btn-primary');
    promptBuilderRunExperimentsButton.id = `${paneID}-obj-${promptBuilderObject?.id}-run-experiment`;
    promptBuilderRunExperimentsButton.innerText = `${promptBuilderInterfaceText?.promptBuilderRunExperimentsButton}`;
    promptBuilderRunExperimentsButton.onclick = async function () {
      promptBuilderRunExperiment();
    };
    // Disabled (with a hint) until at least one LLM is selected
    function updateRunExperimentsButtonState(): void {
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
    // Blocks run deletion while an experiment is in flight (the run indices
    // would shift under the running experiment otherwise).
    let experimentRunning = false;
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
            options: {},
          };
          Object.keys(promptBuilderCurrentLLM.options ?? {}).forEach((key) => {
            if (key !== 'API_KEY') {
              const optionMeta = promptBuilderCurrentLLM.options![key];
              const control = document.getElementById(`${key}${index}`) as
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
              } else {
                const numeric = parseFloat(control.value);
                // A non-numeric value in a legacy-shaped option passes through as text
                currentlySelectedModel.options[`${key}`] = Number.isNaN(numeric) ? control.value : numeric;
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
      promptExperimentTracker.push({
        systemPrompt: systemPrompt,
        userPrompt: userPrompt,
        variables: promptVariables,
        manifest: collectManifestConfig(),
      });

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
      // Load the (cached) cost attributes of just the models that ran, so cost +
      // cheapest can be computed. Guarded internally — never blocks a run.
      await Promise.all(results.map((result) => ensureLLMCostAttributes(result.modelName)));
      // Identify fastest prompt and fewest tokens used prompt
      annotatePrompts(results);
      for (const { modelName, data, options } of results) {
        if (data?.error) {
          if (promptBuilderRunExperimentErrorText) {
            promptBuilderRunExperimentErrorText.innerText = data.error;
          }
          promptBuilderRunExperimentTargetButton.disabled = false;
          promptBuilderRunExperimentTargetButton.innerText = `${promptBuilderInterfaceText?.promptBuilderRunExperimentsButton}`;
          break;
        } else {
          try {
            const trackerEntry = promptExperimentTracker[promptExperimentTrackerRunID] as Record<string, unknown>;
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
          } catch {
            const trackerEntry = promptExperimentTracker[promptExperimentTrackerRunID] as Record<string, unknown>;
            trackerEntry[`${modelName}`] = {
              best_prompt: null,
              fastest_prompt: null,
              fewest_tokens_prompt: null,
              cheapest_prompt: null,
              judge_rank: null,
              judge_best: null,
              output_length: null,
              prompt_length: null,
              run_time: null,
              cost: null,
              options: null,
              response: promptBuilderInterfaceText?.promptBuilderModelInferenceFailed as string,
            } as ModelExperimentData;
          }
        }
      }

      createPromptExperimentTracker(promptExperimentTracker, systemPrompt, userPrompt);

      promptBuilderRunExperimentTargetButton.disabled = false;
      promptBuilderRunExperimentTargetButton.innerText = `${promptBuilderInterfaceText?.promptBuilderRunExperimentsButton}`;
      experimentRunning = false;

      // Auto-judge the run just rendered, when the user opted in and a judge
      // model is selected. Silent: preconditions that don't hold (no judge
      // model, < 2 judgeable responses) simply skip without a toast.
      if (promptBuilderJudgeAuto.checked && getSelectedJudgeModels().length > 0) {
        await promptBuilderJudgeRun(promptExperimentTrackerRunID - 1, true);
      }
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
            loadRunButton.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" height="20px" viewBox="0 -960 960 960" width="20px" fill="currentColor"><title>${promptBuilderInterfaceText.promptExperimentLoadRunButton}</title><path d="M440-320v-326L336-542l-56-58 200-200 200 200-56 58-104-104v326h-80ZM240-160q-33 0-56.5-23.5T160-240v-120h80v120h480v-120h80v120q0 33-23.5 56.5T680-160H240Z"/></svg>`;
            loadRunButton.onclick = () => loadExperimentRun(index);
            promptExperimentRunHeader.appendChild(loadRunButton);
            const deleteRunButton = document.createElement('button');
            deleteRunButton.type = 'button';
            deleteRunButton.classList.add('btn', 'btn-outline-danger', 'btn-sm', 'pet-run-delete');
            deleteRunButton.title = `${promptBuilderInterfaceText.promptExperimentDeleteRunButton}`;
            deleteRunButton.setAttribute(
              'aria-label',
              `${promptBuilderInterfaceText.promptExperimentDeleteRunButton} ${index + 1}`
            );
            deleteRunButton.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" height="20px" viewBox="0 -960 960 960" width="20px" fill="currentColor"><title>${promptBuilderInterfaceText.promptExperimentDeleteRunButton}</title><path d="M280-120q-33 0-56.5-23.5T200-200v-520h-40v-80h200v-40h240v40h200v80h-40v520q0 33-23.5 56.5T680-120H280Zm400-600H280v520h400v-520ZM360-280h80v-360h-80v360Zm160 0h80v-360h-80v360ZM280-720v520-520Z"/></svg>`;
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
            judgeRunButton.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" height="20px" viewBox="0 -960 960 960" width="20px" fill="currentColor"><title>${promptBuilderInterfaceText.promptBuilderJudgeRunButton}</title><path d="M480-80v-520q-18-7-31.5-19T427-640H320l120 280q0 50-41 85t-99 35q-58 0-99-35t-41-85l120-280h-40v-80h227q11-19 29-31.5t42-14.5v-38h80v38q24 2 42 14.5t29 31.5h227v80h-40l120 280q0 50-41 85t-99 35q-58 0-99-35t-41-85l120-280H533q-8 9-21.5 21T480-600v520h160v80H320v-80h160ZM212-440h176l-88-205-88 205Zm360 0h176l-88-205-88 205ZM480-680q17 0 28.5-11.5T520-720q0-17-11.5-28.5T480-760q-17 0-28.5 11.5T440-720q0 17 11.5 28.5T480-680Z"/></svg>`;
            judgeRunButton.onclick = () => promptBuilderJudgeRun(index);
            // Judging compares responses, so it needs at least two successful
            // ones in the run. When it can't (e.g. only one model was included),
            // disable it with a hint on hover rather than fail silently on click.
            const judgeFailedText = promptBuilderInterfaceText?.promptBuilderModelInferenceFailed as string;
            const judgeableResponses = Object.keys(promptExperimentTrackerRunResult)
              .filter((key) => !TRACKER_META_KEYS.includes(key))
              .map((key) => promptExperimentTrackerRunResult[key] as ModelExperimentData)
              .filter(
                (modelData) =>
                  modelData &&
                  typeof modelData.response === 'string' &&
                  modelData.response !== '' &&
                  modelData.response !== judgeFailedText
              ).length;
            const judgeRunButtonWrapper = wrapForHint(judgeRunButton);
            // Align with the sibling load/delete buttons in the flex header
            // (the wrapper span would otherwise sit on the text baseline).
            judgeRunButtonWrapper.classList.add('d-inline-flex', 'align-items-center');
            setDisabledHint(
              judgeRunButton,
              judgeRunButtonWrapper,
              experimentRunning || judgeableResponses < 2,
              judgeableResponses < 2 ? `${promptBuilderInterfaceText?.promptBuilderJudgeNeedsTwoResponses}` : ''
            );
            promptExperimentRunHeader.appendChild(judgeRunButtonWrapper);
          }
          const promptExperimentRunContainerItemBody = document.createElement('div');
          promptExperimentRunContainerItemBody.className = 'accordion-body';
          // Add the System Prompt to the main run body
          const promptExperimentRunContainerItemBodySystemPrompt = document.createElement('p');
          promptExperimentRunContainerItemBodySystemPrompt.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-${index}-run-systenPrompt`;
          promptExperimentRunContainerItemBodySystemPrompt.innerHTML = `<b>${promptBuilderInterfaceText.promptExperimentTrackerSystemPrompt}</b> ${systemPrompt}`;
          // Add the User Prompt to the main run body
          const promptExperimentRunContainerItemBodyUserPrompt = document.createElement('p');
          promptExperimentRunContainerItemBodyUserPrompt.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-${index}-run-userPrompt`;
          promptExperimentRunContainerItemBodyUserPrompt.innerHTML = `<b>${promptBuilderInterfaceText.promptExperimentTrackerUserPrompt}</b> ${userPrompt}`;
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
                promptExperimentContainerModelContainerAccordionItemButton.innerHTML = `<svg class="bestPrompt" xmlns="http://www.w3.org/2000/svg" height="24px" viewBox="0 -960 960 960" width="24px" fill="#1f1f1f"><title>${promptBuilderInterfaceText.promptBuilderBestPrompt}</title><path d="M200-160v-80h560v80H200Zm0-140-51-321q-2 0-4.5.5t-4.5.5q-25 0-42.5-17.5T80-680q0-25 17.5-42.5T140-740q25 0 42.5 17.5T200-680q0 7-1.5 13t-3.5 11l125 56 125-171q-11-8-18-21t-7-28q0-25 17.5-42.5T480-880q25 0 42.5 17.5T540-820q0 15-7 28t-18 21l125 171 125-56q-2-5-3.5-11t-1.5-13q0-25 17.5-42.5T820-740q25 0 42.5 17.5T880-680q0 25-17.5 42.5T820-620q-2 0-4.5-.5t-4.5-.5l-51 321H200Zm68-80h424l26-167-105 46-133-183-133 183-105-46 26 167Zm212 0Z"/></svg> `;
              }
              if (modelData?.fastest_prompt) {
                promptExperimentContainerModelContainerAccordionItemButton.innerHTML += `<svg xmlns="http://www.w3.org/2000/svg" height="24px" viewBox="0 -960 960 960" width="24px" fill="#1f1f1f"><title>${promptBuilderInterfaceText.promptBuilderFastestPrompt}</title><path d="m422-232 207-248H469l29-227-185 267h139l-30 208ZM320-80l40-280H160l360-520h80l-40 320h240L400-80h-80Zm151-390Z"/></svg> `;
              }
              if (modelData?.fewest_tokens_prompt) {
                promptExperimentContainerModelContainerAccordionItemButton.innerHTML += `<svg xmlns="http://www.w3.org/2000/svg" height="24px" viewBox="0 -960 960 960" width="24px" fill="#1f1f1f"><title>${promptBuilderInterfaceText.promptBuilderFewestTokensPrompt}</title><path d="M480-83 240-323l56-56 184 183 184-183 56 56L480-83Zm0-238L240-561l56-56 184 183 184-183 56 56-240 240Zm0-238L240-799l56-56 184 183 184-183 56 56-240 240Z"/></svg> `;
              }
              // Cheapest response — a coin icon, peer to fastest/fewest-tokens
              if (modelData?.cheapest_prompt) {
                promptExperimentContainerModelContainerAccordionItemButton.innerHTML += `<svg class="cheapestPrompt" xmlns="http://www.w3.org/2000/svg" height="24px" viewBox="0 -960 960 960" width="24px" fill="#1f1f1f"><title>${promptBuilderInterfaceText.promptBuilderCheapestPrompt}</title><path d="M480-80q-83 0-156-31.5T197-197q-54-54-85.5-127T80-480q0-83 31.5-156T197-763q54-54 127-85.5T480-880q83 0 156 31.5T763-763q54 54 85.5 127T880-480q0 83-31.5 156T763-197q-54 54-127 85.5T480-80Zm-38-152h68v-42q42-7 72-30t30-66q0-42-24-66t-84-45q-51-18-70.5-32T404-506q0-20 17-33t45-13q26 0 42 12.5t22 32.5l62-25q-9-28-32.5-49T508-611v-41h-68v41q-40 8-63.5 33T353-518q0 39 22.5 61.5T450-416q49 18 68.5 33.5T538-343q0 26-21 40t-49 14q-32 0-56-17.5T379-354l-64 26q13 44 40 69.5t87 33.5v41Z"/></svg> `;
              }
              // Judge's best response — a fifth icon, peer to the four above
              if (modelData?.judge_best) {
                promptExperimentContainerModelContainerAccordionItemButton.innerHTML += `<svg class="judgeBest" xmlns="http://www.w3.org/2000/svg" height="24px" viewBox="0 -960 960 960" width="24px" fill="#1f1f1f"><title>${promptBuilderInterfaceText.promptBuilderJudgeBestPrompt}</title><path d="M280-120v-80h160v-124q-49-11-87.5-41.5T296-442q-75-9-125.5-65.5T120-640v-40q0-33 23.5-56.5T200-760h80v-80h400v80h80q33 0 56.5 23.5T840-680v40q0 76-50.5 132.5T664-442q-18 56-56.5 86.5T520-324v124h160v80H280Zm0-408v-152h-80v40q0 38 22 68.5t58 43.5Zm200 128q50 0 85-35t35-85v-240H360v240q0 50 35 85t85 35Zm200-128q36-13 58-43.5t22-68.5v-40h-80v152Zm-200-52Z"/></svg> `;
              }
              promptExperimentContainerModelContainerAccordionItemButton.innerHTML += `${promptBuilderInterfaceText.promptExperimentModel} ${promptExperimentRunModelKey}`;
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
                          `<svg class="bestPrompt" xmlns="http://www.w3.org/2000/svg" height="24px" viewBox="0 -960 960 960" width="24px" fill="#1f1f1f"><title>${promptBuilderInterfaceText.promptBuilderBestPrompt}</title><path d="M200-160v-80h560v80H200Zm0-140-51-321q-2 0-4.5.5t-4.5.5q-25 0-42.5-17.5T80-680q0-25 17.5-42.5T140-740q25 0 42.5 17.5T200-680q0 7-1.5 13t-3.5 11l125 56 125-171q-11-8-18-21t-7-28q0-25 17.5-42.5T480-880q25 0 42.5 17.5T540-820q0 15-7 28t-18 21l125 171 125-56q-2-5-3.5-11t-1.5-13q0-25 17.5-42.5T820-740q25 0 42.5 17.5T880-680q0 25-17.5 42.5T820-620q-2 0-4.5-.5t-4.5-.5l-51 321H200Zm68-80h424l26-167-105 46-133-183-133 183-105-46 26 167Zm212 0Z"/></svg> `
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
                    updateManifestButtonState();
                  });

                  const bestPromptLabel = document.createElement('label');
                  bestPromptLabel.className = 'form-check-label';
                  bestPromptLabel.htmlFor = `best-prompt-${index}-${promptExperimentRunModelKey}`;
                  bestPromptLabel.innerText = promptBuilderInterfaceText.promptExperimentModelPromptBest as string;
                  bestPromptDiv.appendChild(bestPromptCheckbox);
                  bestPromptDiv.appendChild(bestPromptLabel);
                  promptExperimentContainerModelContainerAccordionItemBodyContainerBodyLine.appendChild(bestPromptDiv);
                } else if (promptExperimentRunModelKeyAttribute === 'prompt_length') {
                  promptExperimentContainerModelContainerAccordionItemBodyContainerBodyLine.innerHTML = `<b>${promptBuilderInterfaceText.promptExperimentModelPromptLength}</b> ${escapeHtml(promptExperimentRunModelKeyValue)}`;
                } else if (promptExperimentRunModelKeyAttribute === 'output_length') {
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
                    const apiKeyDefault = promptBuilderAvailableLLMs.find(
                      (obj) => obj['name'] === promptExperimentRunModelKey
                    )?.options?.API_KEY?.default;
                    (modelData as unknown as Record<string, unknown>)[promptExperimentRunModelKeyAttribute] = {
                      ...(optionsVal as Record<string, unknown>),
                      API_KEY: apiKeyDefault,
                    };
                    (optionsVal as Record<string, unknown>)['API_KEY'] = apiKeyDefault;
                  }
                  promptExperimentContainerModelContainerAccordionItemBodyContainerBodyLine.innerHTML = `<b>${promptBuilderInterfaceText.promptExperimentModelOptions}</b> ${escapeHtml(JSON.stringify(promptExperimentRunModelKeyValue))}`;
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
            modelData.judge_rank = ranks[modelName] ?? null;
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
      if (judgeButton) {
        judgeButton.disabled = true;
        judgeButton.innerHTML = `<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>`;
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
      if (systemPromptInput) systemPromptInput.value = trackerEntry.systemPrompt ?? '';
      if (userPromptInput) userPromptInput.value = trackerEntry.userPrompt ?? '';
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
          promptBuilderAvailableLLMs.some((llm) => llm.name === name)
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
          chairmanModel && promptBuilderAvailableLLMs.some((llm) => llm.name === chairmanModel)
        );
        promptBuilderJudgeChairmanToggle.checked = chairmanAvailable;
        if (chairmanAvailable) promptBuilderJudgeChairmanModel.value = chairmanModel;
        judgeChairmanModelRow.classList.toggle('d-none', !chairmanAvailable);
      }
      // Reselect the run's LLMs and restore their option values
      const runModels = Object.keys(trackerEntry).filter((key) => !TRACKER_META_KEYS.includes(key));
      promptBuilderAvailableLLMs.forEach((availableLLM, llmIndex) => {
        const llmCheckbox = document.getElementById(`model${llmIndex}`) as HTMLInputElement | null;
        if (!llmCheckbox) return;
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
            const optionInput = document.getElementById(`${optionKey}${llmIndex}`) as
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
      const missingLLMs = runModels.filter(
        (modelName) => !promptBuilderAvailableLLMs.some((availableLLM) => availableLLM.name === modelName)
      );
      if (missingLLMs.length > 0) {
        showToast(`${promptBuilderInterfaceText?.promptBuilderLoadMissingLLMs} ${missingLLMs.join(', ')}`);
      }
    }

    // Load the most recent run that has a best response selected. Runs
    // automatically after a prompt's tracker is loaded, so it stays silent
    // when no best response has been selected yet.
    function loadMostRecentBestRun(): void {
      for (let index = promptExperimentTracker.length - 1; index >= 0; index--) {
        const trackerEntry = promptExperimentTracker[index];
        const hasBestPrompt = Object.keys(trackerEntry).some(
          (key) =>
            !TRACKER_META_KEYS.includes(key) && (trackerEntry[key] as ModelExperimentData)?.best_prompt
        );
        if (hasBestPrompt) {
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
    promptExperimentSaveButton.setAttribute('class', 'btn btn-primary');
    promptExperimentSaveButton.onclick = async function () {
      promptBuilderSaveExperiments();
    };

    // Save the prompt run and turn the best prompt into a model
    const promptExperimentCreateModelButton = document.createElement('button');
    promptExperimentCreateModelButton.id = `${paneID}-obj-${promptBuilderObject?.id}-pet-create-model-button`;
    promptExperimentCreateModelButton.innerText = `${promptBuilderInterfaceText?.promptBuilderCreateModelButton}`;
    promptExperimentCreateModelButton.setAttribute('type', 'button');
    promptExperimentCreateModelButton.setAttribute('class', 'btn btn-primary');
    promptExperimentCreateModelButton.onclick = async function () {
      // Persist the manifest configuration with the manifested run so loading
      // the run later restores it. The save link goes to the manifest box here.
      stampManifestConfigOnBestRun();
      await promptBuilderSaveExperiments(promptExperimentResultContainer);
      await promptBulderCreateBestPromptModel();
    };
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
      setDisabledHint(
        promptExperimentCreateModelButton,
        promptExperimentCreateModelButtonWrapper,
        !hasBestPrompt,
        `${promptBuilderInterfaceText?.promptBuilderCreateModelNoBestPrompt}`
      );
      // The optimize panel gates on the same tracker state (its dataset is the
      // runs with a Best Response), so refresh it wherever the manifest button
      // refreshes. No-op until the optimize section exists / when disabled.
      updateOptimizeState();
    }
    updateManifestButtonState();
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
    });
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
    outputVariablesAddButton.classList.add('btn', 'btn-secondary');
    outputVariablesAddButton.innerText = `${promptBuilderInterfaceText?.promptBuilderOutputVariablesAddButton}`;
    outputVariablesAddButton.onclick = () => createOutputVariableRow();
    promptExperimentManifestOptions.appendChild(outputVariablesLabel);
    promptExperimentManifestOptions.appendChild(outputVariablesDescription);
    promptExperimentManifestOptions.appendChild(promptBuilderOutputVariablesContainer);
    promptExperimentManifestOptions.appendChild(outputVariablesAddButton);

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
        } else if (name !== '' && RESERVED_OUTPUT_NAMES.includes(name)) {
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
        if (!isValidDS2VariableName(name) || RESERVED_OUTPUT_NAMES.includes(name) || seenNames.has(name)) return;
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

    // Save the experiments to the SAS Model Manager. The success/failure
    // message goes to `resultContainer` — next to Save for a plain save, or the
    // manifest box when called as the first step of manifesting.
    async function promptBuilderSaveExperiments(
      resultContainer: HTMLElement = promptExperimentSaveResultContainer
    ): Promise<void> {
      // Add spinner to save button
      const promptExperimentSaveTargetButton = document.getElementById(
        `${paneID}-obj-${promptBuilderObject?.id}-pet-save-button`
      ) as HTMLButtonElement;
      promptExperimentSaveTargetButton.disabled = true;
      promptExperimentSaveTargetButton.innerHTML = `<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> ${promptBuilderInterfaceText.promptBuilderSaveExperimentsButtonStatus}`;
      const promptExperimentRunModel = (
        document.getElementById(`${promptBuilderObject?.id}-prompt-dropdown`) as HTMLSelectElement
      ).value;
      // Check if an experiment was run (deleting runs also counts as a change,
      // so an emptied tracker can still be saved)
      if (petRows.length === 0 && !experimentsModified) {
        promptExperimentSaveTargetButton.disabled = false;
        promptExperimentSaveTargetButton.innerText = `${promptBuilderInterfaceText?.promptBuilderSaveExperimentsButton}`;
        alert(promptBuilderInterfaceText.promptExperimentSaveModelsExperimentAlert);
        return;
      }
      // Check if a prompt test was selected
      if (promptExperimentRunModel === promptBuilderInterfaceText.promptSelect) {
        promptExperimentSaveTargetButton.disabled = false;
        promptExperimentSaveTargetButton.innerText = `${promptBuilderInterfaceText?.promptBuilderSaveExperimentsButton}`;
        alert(promptBuilderInterfaceText.promptExperimentSaveModelsPromptAlert);
        return;
      } else {
        // Get the ID of a previously created Prompt Experiment Tracker and delete it
        const promptBuilderAvailablePTE = await getModelContents(promptExperimentRunModel);
        for (const promptBuilderAvailablepte in promptBuilderAvailablePTE) {
          if (promptBuilderAvailablePTE[promptBuilderAvailablepte]?.name === 'Prompt-Experiment-Tracker.json') {
            await createModelVersion(promptExperimentRunModel);
            await deleteModelContent(promptExperimentRunModel, promptBuilderAvailablePTE[promptBuilderAvailablepte]?.id ?? '');
          }
        }
      }
      // Create the new Prompt Experiment Tracker
      const promptExperimentPromptResponseObject = await createModelContent(
        promptExperimentRunModel,
        petRows,
        'Prompt-Experiment-Tracker.json'
      );
      if (promptExperimentPromptResponseObject.status_code === 201) {
        experimentsModified = false;
        showToast(`${promptBuilderInterfaceText?.promptBuilderSaveToast}`);
        resultContainer.innerHTML = `<p>${promptBuilderInterfaceText.promptExperimentSaveSucessResponse} <a target="_blank" rel="noopener noreferrer" href="${VIYA}/SASModelManager/models/${promptExperimentRunModel}">${VIYA}/SASModelManager/models/${promptExperimentRunModel}</a></p>`;
      } else {
        showToast(`${promptBuilderInterfaceText.promptExperimentSaveFailureResponse}`);
        resultContainer.innerHTML = `<p>${promptBuilderInterfaceText.promptExperimentSaveFailureResponse}</p>`;
      }

      // Re-enable the save button
      promptExperimentSaveTargetButton.disabled = false;
      promptExperimentSaveTargetButton.innerText = `${promptBuilderInterfaceText?.promptBuilderSaveExperimentsButton}`;
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
        // from the stored templates: literal braces are escaped for the
        // f-string and each referenced {{variable}} becomes a score-function
        // input that is inserted at its position in the template.
        const promptTemplateToPythonFString = (template: string): string => {
          let fString = template.replace(/\{/g, '{{').replace(/\}/g, '}}');
          referencedVariables.forEach((variable) => {
            fString = fString.replace(
              new RegExp(`\\{\\{\\{\\{\\s*${variable.name}\\s*\\}\\}\\}\\}`, 'g'),
              `{str(${variable.name}).strip()}`
            );
          });
          return fString;
        };
        const scoreCodeSystemPromptLiteral = runVariables
          ? `f"""${promptTemplateToPythonFString(basePrompt!.systemPrompt)}"""`
          : `"""${basePrompt!.systemPrompt}"""`;
        const scoreCodeUserPromptLiteral = runVariables
          ? `f"""${promptTemplateToPythonFString(basePrompt!.userPrompt)}"""`
          : `f"${scoreCodeUserPrompt}"`;
        // Create the options string for the score code
        let scoreCodeOptions = '';
        for (let i = 0; i < bestPromptOptionsList.length; i++) {
          if (i !== 0) {
            scoreCodeOptions += ',';
          }
          scoreCodeOptions += bestPromptOptionsList[i];
        }
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
    sslVerify = os.getenv("LLMCONTAINERCABUNDLE", "/security/trustedcerts.pem")
    if not os.path.isfile(sslVerify):
        sslVerify = True
    if os.getenv("LLMCONTAINERSSLVERIFY", "").strip().lower() in ("false", "no", "0"):
        sslVerify = False
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
        // With the integrated call the request body is built with json.dumps,
        // which escapes the prompt texts and runtime input values correctly.
        // The Call LLM node path keeps the legacy manual escaping that the
        // node applies when it embeds llmBody into its own request.
        const scoreCodeBodyBlock = integratedLLMCall
          ? `    # This is the system prompt that was selected as the best one by the prompt engineer
    systemPrompt = ${scoreCodeSystemPromptLiteral}
    # Here the user prompt will be created from the inputs of the call
    userPrompt = ${scoreCodeUserPromptLiteral}
    # The request body for the LLM container in the SCR input format
    llmBody = json.dumps({"inputs": [{"name": "systemPrompt", "value": systemPrompt}, {"name": "userPrompt", "value": userPrompt}, {"name": "options", "value": options}]})`
          : `    # This is the system prompt that was selected as the best one by the prompt engineer
    systemPrompt = ${scoreCodeSystemPromptLiteral}.replace('\\n', "\\\\n").replace("'", '"').replace('"', '\\\\"')
    # Here the user prompt will be created from the inputs of the call
    userPrompt = ${scoreCodeUserPromptLiteral}.replace('\\n', "\\\\n").replace("'", '"').replace('"', '\\\\"')
    llmBody = '{"inputs":[{"name":"systemPrompt","value":"' + systemPrompt + '"},{"name":"userPrompt","value":"' + userPrompt + '"},{"name":"options","value":"' + options + '"}]}'`;
        const scoreCode = `import os
${integratedLLMCall ? 'import requests\nimport json\n' : ''}
def scoreModel(${scoreCodeInput}):
    "Output: ${scoreCodeOutputList}"
    # The llm and the target endpoint
    llm = "${(bestPromptItem as PETRow).model}"
    # Retrieves the endpoint where the LLM containers are hosted - e.g. https://example.com/llm
    # If an environment variable called LLMCONTAINERPATH is set, it will use that instead of the one stored in the prompt builder object
    endpoint = os.getenv("LLMCONTAINERPATH", "${promptBuilderObject?.SCREndpoint}")
    llmURL = f"""${llmEndpoint}"""
    # These are the options that were set for the best prompt
    options = f"{{${scoreCodeOptions}}}"
${scoreCodeBodyBlock}
${scoreCodeReturn}`;
        const mainfestPromptScoreCodeBlob = new Blob([scoreCode], { type: 'text/x-python' });
        // Clean up previous variables first
        const modelVariables = await getModelVariables(promptExperimentRunModel);
        for (let i = 0; i < modelVariables.length; i++) {
          await deleteModelVariable(promptExperimentRunModel, modelVariables[i]!.id!);
        }
        const validatedModelName = validateAndCorrectPackageName(promptExperimentRunModelName);
        await createModelContent(promptExperimentRunModel, promptInputs, 'inputVar.json', 'inputVariables');
        await createModelContent(promptExperimentRunModel, outputVars, 'outputVar.json', 'outputVariables');
        await createModelContent(
          promptExperimentRunModel,
          mainfestPromptScoreCodeBlob,
          `${validatedModelName.correctedName}.py`,
          'score',
          'text/x-python'
        );
        // The integrated call imports the requests package at score time: ship
        // a requirements.json (same format and role as the LLM definitions) so
        // publishing destinations that build a Python environment install it.
        // A stale one from an earlier manifest is removed when the LLM call is
        // no longer included.
        if (integratedLLMCall) {
          await createModelContent(
            promptExperimentRunModel,
            [{ step: 'install requests', command: 'pip3 -q install requests' }],
            'requirements.json',
            'python pickle'
          );
        } else {
          const manifestModelContents = await getModelContents(promptExperimentRunModel);
          const staleRequirements = manifestModelContents.find(
            (modelContent) => modelContent.name === 'requirements.json'
          );
          if (staleRequirements?.id) {
            await deleteModelContent(promptExperimentRunModel, staleRequirements.id);
          }
        }
        // Tag the model with the manifested LLM and the chosen manifest mode.
        // Tags from an earlier manifest (other LLM names, mode flags) are
        // removed first so re-manifesting does not leave stale tags behind.
        const manifestTags = [
          (bestPromptItem as PETRow).model,
          ...(integratedLLMCall ? ['LLM-Call-Included'] : []),
          ...(parseOutputs ? ['Output-Parsing'] : []),
        ];
        const staleManifestTags = [
          'LLM-Call-Included',
          'Output-Parsing',
          ...promptBuilderAvailableLLMs.map((availableLLM) => availableLLM.name),
          ...promptExperimentTracker.flatMap((trackerEntry) =>
            Object.keys(trackerEntry).filter((key) => !TRACKER_META_KEYS.includes(key))
          ),
        ];
        try {
          await updateModelTags(promptExperimentRunModel, staleManifestTags, manifestTags);
        } catch (error) {
          console.error('Failed to update the tags of the manifested model.', error);
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
        try {
          await updateModelAttributes(promptExperimentRunModel, manifestAttributes);
        } catch (error) {
          console.error('Failed to copy the LLM attributes onto the manifested model.', error);
        }
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

    // --- DSPy prompt optimization (Phase 3) --------------------------------
    // The Optimize section saves the prompt, launches the shipped Job
    // Execution job (proc python + DSPy, see SAS-Viya-Integrations/
    // Prompt-Optimization/), polls it with live log milestones, and offers the
    // optimised prompt back. Gated by the enableOptimization Option; the job
    // is the sole writer of Prompt-Optimization-Tracker.json — the browser
    // only launches, polls and reads.
    const optimizationEnabled = String(promptBuilderObject?.enableOptimization ?? '') === 'true';
    // Hoisting-safe handle for updateOptimizeState(), which manifest/tracker
    // refreshes call before this section is built (`var` so early calls see
    // `undefined` instead of a TDZ error and no-op).
    // eslint-disable-next-line no-var
    var optimizeUI:
      | {
          targetSelect: HTMLSelectElement;
          metricSelect: HTMLSelectElement;
          judgeRow: HTMLElement;
          judgeSelect: HTMLSelectElement;
          maxDemosInput: HTMLInputElement;
          samplesHint: HTMLElement;
          estimateLine: HTMLElement;
          runButton: HTMLButtonElement;
          runWrapper: HTMLSpanElement;
          statusLine: HTMLElement;
          resultBox: HTMLElement;
        }
      | undefined;
    // The prompt-test the running job was launched for (the dropdown may
    // change while the job runs) and the launched job's id.
    let optimizeSourceModelId = '';
    let optimizeJobId = '';

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
      const samples = countOptimizeSamples();
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
      // the bootstrap teacher pass — kept deliberately coarse.
      const estimatedCalls = samples * 3 + 10;
      const estimateParts = [
        `${promptBuilderInterfaceText?.promptBuilderOptimizeEstimate}`.replace('{calls}', String(estimatedCalls)),
      ];
      if (
        optimizeUI.metricSelect.value === 'judge' &&
        optimizeUI.judgeSelect.value !== '' &&
        optimizeUI.judgeSelect.value === optimizeUI.targetSelect.value
      ) {
        estimateParts.push(`${promptBuilderInterfaceText?.promptBuilderOptimizeJudgeSelfWarning}`);
      }
      optimizeUI.estimateLine.innerText = estimateParts.join(' ');
      optimizeUI.judgeRow.classList.toggle('d-none', optimizeUI.metricSelect.value !== 'judge');

      const promptSelected =
        promptBuilderPromptSelectorDropdown.value !== '' &&
        promptBuilderPromptSelectorDropdown.value !== `${promptBuilderInterfaceText?.promptSelect}`;
      const configReady = Boolean(promptBuilderObject?.computeContext && promptBuilderObject?.optimizeJobProgram);
      let disabledHint = '';
      if (optimizeJobActive) disabledHint = `${promptBuilderInterfaceText?.promptBuilderOptimizeAlreadyRunning}`;
      else if (!configReady) disabledHint = `${promptBuilderInterfaceText?.promptBuilderOptimizeNotConfigured}`;
      else if (!promptSelected) disabledHint = `${promptBuilderInterfaceText?.promptBuilderOptimizeNoPrompt}`;
      else if (samples < minSamples)
        disabledHint = `${promptBuilderInterfaceText?.promptBuilderOptimizeSamplesBelowMin}`.replace(
          '{min}',
          String(minSamples)
        );
      setDisabledHint(optimizeUI.runButton, optimizeUI.runWrapper, disabledHint !== '', disabledHint);
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
      optimizeUI.runButton.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeRunButton}`;
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
      const progressMessages = await getJobProgressMessages(job);
      const latestMilestone = progressMessages.length > 0 ? ` — ${progressMessages[progressMessages.length - 1]}` : '';
      optimizeUI.statusLine.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeJobState} ${job.state}${latestMilestone}`;
      if (!isTerminalJobState(job.state)) {
        // The job records its outcome in the optimization tracker BEFORE Job
        // Execution's state turns terminal — and a hard-killed compute session
        // can leave the state 'running' indefinitely (seen live). When this
        // job's entry exists, finish from it instead of polling forever.
        const earlyEntry = await readOptimizationEntry(true);
        if (!earlyEntry) return;
        stopOptimizePolling();
        if (earlyEntry.status === 'succeeded') {
          await showOptimizeResult();
        } else {
          const earlyError = earlyEntry.error ? ` ${earlyEntry.error}` : '';
          optimizeUI.resultBox.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeJobFailed}${earlyError}`;
          showToast(`${promptBuilderInterfaceText?.promptBuilderOptimizeJobFailed}`);
        }
        resetOptimizeRunButton();
        return;
      }
      stopOptimizePolling();
      if (job.state === 'completed' || job.state === 'completedWithWarnings') {
        await showOptimizeResult();
      } else {
        // The job records its failure reason in the optimization tracker
        // (e.g. "the compute context lacks the dspy package") — prefer that
        // over Job Execution's generic error message.
        const failedEntry = await readOptimizationEntry();
        const failureDetail = failedEntry?.error
          ? ` ${failedEntry.error}`
          : job.error?.message
            ? ` ${job.error.message}`
            : '';
        optimizeUI.resultBox.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeJobFailed}${failureDetail}`;
        showToast(`${promptBuilderInterfaceText?.promptBuilderOptimizeJobFailed}`);
      }
      resetOptimizeRunButton();
    }

    /**
     * Read the launched job's entry from the job-written optimization tracker
     * on the source prompt-test, matched by job id. With `requireJobIdMatch`
     * only an exact match counts (used while the job still reports running);
     * otherwise the newest entry is the fallback. Null when the tracker or
     * entry is not (yet) readable.
     */
    async function readOptimizationEntry(requireJobIdMatch = false): Promise<OptimizationTrackerEntry | null> {
      try {
        const sourceContents = await getModelContents(optimizeSourceModelId);
        const trackerContent = sourceContents.find(
          (modelContent) => modelContent.name === 'Prompt-Optimization-Tracker.json'
        );
        if (!trackerContent?.fileUri) return null;
        const response = await getFileContent(String(trackerContent.fileUri));
        const trackerEntries = (await response.json()) as OptimizationTrackerEntry[];
        if (!Array.isArray(trackerEntries)) return null;
        const matched = trackerEntries.find((candidate) => candidate.jobId === optimizeJobId) ?? null;
        if (requireJobIdMatch) return matched;
        return matched ?? trackerEntries[trackerEntries.length - 1] ?? null;
      } catch {
        return null;
      }
    }

    /**
     * After a completed job: read the job-written optimization tracker from
     * the source prompt-test and render the outcome (metric before/after, a
     * link to the produced prompt-test, and a load-into-workbench button).
     */
    async function showOptimizeResult(): Promise<void> {
      if (!optimizeUI) return;
      optimizeUI.resultBox.innerHTML = '';
      const entry = await readOptimizationEntry();
      if (!entry || entry.status !== 'succeeded') {
        // The job completed but its tracker entry is missing/failed — surface
        // whatever error the entry carries.
        const entryError = entry?.error ? ` ${entry.error}` : '';
        optimizeUI.resultBox.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeJobFailed}${entryError}`;
        return;
      }

      const resultHeading = document.createElement('p');
      resultHeading.classList.add('fw-bold', 'mb-1');
      resultHeading.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeDone}`;
      optimizeUI.resultBox.appendChild(resultHeading);
      const metricLine = document.createElement('p');
      metricLine.classList.add('mb-1');
      const formatMetric = (value: number | null | undefined): string =>
        value === null || value === undefined || !Number.isFinite(Number(value))
          ? '—'
          : String(Math.round(Number(value) * 1000) / 1000);
      metricLine.innerText =
        `${promptBuilderInterfaceText?.promptBuilderOptimizeMetricBefore} ${formatMetric(entry.metricBefore)}` +
        ` → ${promptBuilderInterfaceText?.promptBuilderOptimizeMetricAfter} ${formatMetric(entry.metricAfter)}`;
      optimizeUI.resultBox.appendChild(metricLine);
      if (entry.producedPromptModelId) {
        const producedLink = document.createElement('p');
        producedLink.classList.add('mb-1');
        const anchor = document.createElement('a');
        anchor.target = '_blank';
        anchor.rel = 'noopener noreferrer';
        anchor.href = `${VIYA}/SASModelManager/models/${entry.producedPromptModelId}`;
        anchor.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeOpenProduced}`;
        producedLink.appendChild(anchor);
        optimizeUI.resultBox.appendChild(producedLink);
      }
      const optimizedPrompt = entry.optimizedPrompt;
      if (optimizedPrompt && (optimizedPrompt.systemPrompt || optimizedPrompt.userPrompt)) {
        const loadButton = document.createElement('button');
        loadButton.type = 'button';
        loadButton.classList.add('btn', 'btn-secondary', 'btn-sm');
        loadButton.id = `${paneID}-obj-${promptBuilderObject?.id}-optimize-load`;
        loadButton.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeLoadButton}`;
        loadButton.onclick = () => {
          const systemPromptArea = document.getElementById(
            `${paneID}-obj-${promptBuilderObject?.id}-system-prompt`
          ) as HTMLTextAreaElement | null;
          const userPromptArea = document.getElementById(
            `${paneID}-obj-${promptBuilderObject?.id}-user-prompt`
          ) as HTMLTextAreaElement | null;
          if (systemPromptArea) systemPromptArea.value = String(optimizedPrompt.systemPrompt ?? '');
          if (userPromptArea) userPromptArea.value = String(optimizedPrompt.userPrompt ?? '');
          if (Array.isArray(optimizedPrompt.variables)) setPromptVariables(optimizedPrompt.variables);
          showToast(`${promptBuilderInterfaceText?.promptBuilderOptimizeLoadedToast}`);
        };
        optimizeUI.resultBox.appendChild(loadButton);
      }
      showToast(`${promptBuilderInterfaceText?.promptBuilderOptimizeDone}`);
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
      const metric = optimizeUI.metricSelect.value === 'judge' ? 'judge' : 'exact';
      const judgeModelName = metric === 'judge' ? optimizeUI.judgeSelect.value : '';
      if (metric === 'judge' && judgeModelName === '') {
        showToast(`${promptBuilderInterfaceText?.promptBuilderJudgeSelectModelToast}`);
        return;
      }
      const targetLLM = promptBuilderAvailableLLMs.find((availableLLM) => availableLLM.name === targetModelName);

      optimizeJobActive = true;
      optimizeSourceModelId = promptModelId;
      optimizeJobId = '';
      updateOptimizeState();
      optimizeUI.resultBox.innerHTML = '';
      optimizeUI.statusLine.innerText = '';
      optimizeUI.runButton.innerHTML = `<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> ${promptBuilderInterfaceText?.promptBuilderOptimizeRunButtonStatus}`;

      try {
        // Save first so the job reads the exact prompt (and tracker) the user
        // sees — the dataset gate guarantees there is something to save.
        if (petRows.length > 0 || experimentsModified) {
          await promptBuilderSaveExperiments();
        }
        const jobDefinitionUri = await resolveJobDefinitionUri(String(promptBuilderObject?.optimizeJobProgram ?? ''));
        const maxDemos = Math.max(0, Math.min(16, Number(optimizeUI.maxDemosInput.value) || 4));
        // Only names and ids travel in the request — provider keys stay in the
        // governed library/table the job reads server-side.
        const job = await launchJob(jobDefinitionUri, `Optimize ${promptName}`, {
          _contextName: String(promptBuilderObject?.computeContext ?? ''),
          promptModelId,
          promptName,
          targetModelId: String(targetLLM?.id ?? ''),
          targetModelName,
          scrEndpoint: String(promptBuilderObject?.SCREndpoint ?? ''),
          deploymentType: String(promptBuilderObject?.deploymentType ?? 'k8s'),
          datasetSource: 'tracker',
          metric,
          judgeModelName,
          optimizer: 'bootstrap',
          maxDemos: String(maxDemos),
          minSamples: String(optimizeMinSamples()),
          keyLibrary: String(promptBuilderObject?.optimizeKeyLibrary ?? ''),
          keyTable: String(promptBuilderObject?.optimizeKeyTable ?? ''),
        });
        optimizeJobId = String(job.id ?? '');
        if (optimizeJobId === '') {
          throw new Error('Job Execution returned no job id.');
        }
        optimizeUI.statusLine.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeJobState} ${job.state ?? 'pending'}`;
        optimizePollHandle = window.setInterval(() => {
          void pollOptimizeJob();
        }, 5000);
      } catch (error) {
        stopOptimizePolling();
        optimizeUI.resultBox.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeLaunchFailed} ${String(
          (error as Error)?.message ?? error
        )}`;
        showToast(`${promptBuilderInterfaceText?.promptBuilderOptimizeLaunchFailed}`);
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

      const optimizeControls = document.createElement('div');
      optimizeControls.classList.add('pb-optimize-controls', 'd-flex', 'flex-column', 'gap-2', 'mt-2');

      // Target LLM.
      const targetRow = document.createElement('div');
      targetRow.classList.add('d-flex', 'align-items-center', 'gap-2', 'flex-wrap');
      const targetLabel = document.createElement('label');
      targetLabel.classList.add('form-label', 'mb-0');
      targetLabel.htmlFor = `${paneID}-obj-${promptBuilderObject?.id}-optimize-target`;
      targetLabel.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeTargetLabel}`;
      const optimizeTargetSelect = document.createElement('select');
      optimizeTargetSelect.id = `${paneID}-obj-${promptBuilderObject?.id}-optimize-target`;
      optimizeTargetSelect.classList.add('form-select', 'form-select-sm');
      optimizeTargetSelect.style.width = 'auto';
      const targetPlaceholder = document.createElement('option');
      targetPlaceholder.value = '';
      targetPlaceholder.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeTargetPlaceholder}`;
      optimizeTargetSelect.appendChild(targetPlaceholder);
      promptBuilderAvailableLLMs.forEach((availableLLM) => {
        const targetOption = document.createElement('option');
        targetOption.value = availableLLM.name;
        targetOption.innerText = availableLLM.name;
        optimizeTargetSelect.appendChild(targetOption);
      });
      targetRow.appendChild(targetLabel);
      targetRow.appendChild(optimizeTargetSelect);

      // Dataset: this prompt's experiments (Phase 3a's only source), with the
      // assumed-correct notice and the live sample count.
      const datasetBlock = document.createElement('div');
      const datasetLabel = document.createElement('p');
      datasetLabel.classList.add('fw-bold', 'mb-1');
      datasetLabel.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeDatasetLabel}`;
      const datasetText = document.createElement('p');
      datasetText.classList.add('mb-1');
      datasetText.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeDatasetTracker}`;
      const datasetNotice = document.createElement('small');
      datasetNotice.classList.add('text-muted', 'd-block');
      datasetNotice.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeDatasetTrackerInfo}`;
      const optimizeSamplesHint = document.createElement('small');
      optimizeSamplesHint.id = `${paneID}-obj-${promptBuilderObject?.id}-optimize-samples-hint`;
      optimizeSamplesHint.classList.add('text-muted', 'd-block');
      datasetBlock.appendChild(datasetLabel);
      datasetBlock.appendChild(datasetText);
      datasetBlock.appendChild(datasetNotice);
      datasetBlock.appendChild(optimizeSamplesHint);

      // Metric (+ judge model when the metric is the judge).
      const metricRow = document.createElement('div');
      metricRow.classList.add('d-flex', 'align-items-center', 'gap-2', 'flex-wrap');
      const metricLabel = document.createElement('label');
      metricLabel.classList.add('form-label', 'mb-0');
      metricLabel.htmlFor = `${paneID}-obj-${promptBuilderObject?.id}-optimize-metric`;
      metricLabel.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeMetricLabel}`;
      const optimizeMetricSelect = document.createElement('select');
      optimizeMetricSelect.id = `${paneID}-obj-${promptBuilderObject?.id}-optimize-metric`;
      optimizeMetricSelect.classList.add('form-select', 'form-select-sm');
      optimizeMetricSelect.style.width = 'auto';
      const exactOption = document.createElement('option');
      exactOption.value = 'exact';
      exactOption.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeMetricExact}`;
      const judgeMetricOption = document.createElement('option');
      judgeMetricOption.value = 'judge';
      judgeMetricOption.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeMetricJudge}`;
      optimizeMetricSelect.appendChild(exactOption);
      optimizeMetricSelect.appendChild(judgeMetricOption);
      metricRow.appendChild(metricLabel);
      metricRow.appendChild(optimizeMetricSelect);

      const optimizeJudgeRow = document.createElement('div');
      optimizeJudgeRow.id = `${paneID}-obj-${promptBuilderObject?.id}-optimize-judge-row`;
      optimizeJudgeRow.classList.add('d-flex', 'align-items-center', 'gap-2', 'ms-4', 'd-none');
      const optimizeJudgeLabel = document.createElement('label');
      optimizeJudgeLabel.classList.add('form-label', 'mb-0');
      optimizeJudgeLabel.htmlFor = `${paneID}-obj-${promptBuilderObject?.id}-optimize-judge-model`;
      optimizeJudgeLabel.innerText = `${promptBuilderInterfaceText?.promptBuilderJudgeModelLabel}`;
      const optimizeJudgeSelect = document.createElement('select');
      optimizeJudgeSelect.id = `${paneID}-obj-${promptBuilderObject?.id}-optimize-judge-model`;
      optimizeJudgeSelect.classList.add('form-select', 'form-select-sm');
      optimizeJudgeSelect.style.width = 'auto';
      const optimizeJudgePlaceholder = document.createElement('option');
      optimizeJudgePlaceholder.value = '';
      optimizeJudgePlaceholder.innerText = `${promptBuilderInterfaceText?.promptBuilderJudgeSelectPlaceholder}`;
      optimizeJudgeSelect.appendChild(optimizeJudgePlaceholder);
      promptBuilderAvailableLLMs.forEach((availableLLM) => {
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

      // Optimizer (bootstrap only in 3a) + max few-shot demos.
      const optimizerRow = document.createElement('div');
      optimizerRow.classList.add('d-flex', 'align-items-center', 'gap-2', 'flex-wrap');
      const optimizerLabel = document.createElement('label');
      optimizerLabel.classList.add('form-label', 'mb-0');
      optimizerLabel.htmlFor = `${paneID}-obj-${promptBuilderObject?.id}-optimize-optimizer`;
      optimizerLabel.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeOptimizerLabel}`;
      const optimizeOptimizerSelect = document.createElement('select');
      optimizeOptimizerSelect.id = `${paneID}-obj-${promptBuilderObject?.id}-optimize-optimizer`;
      optimizeOptimizerSelect.classList.add('form-select', 'form-select-sm');
      optimizeOptimizerSelect.style.width = 'auto';
      const bootstrapOption = document.createElement('option');
      bootstrapOption.value = 'bootstrap';
      bootstrapOption.innerText = `${promptBuilderInterfaceText?.promptBuilderOptimizeOptimizerBootstrap}`;
      optimizeOptimizerSelect.appendChild(bootstrapOption);
      const maxDemosLabel = document.createElement('label');
      maxDemosLabel.classList.add('form-label', 'mb-0', 'ms-3');
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
        void promptBuilderRunOptimization();
      };
      const optimizeRunWrapper = wrapForHint(optimizeRunButton);
      const optimizeStatusLine = document.createElement('p');
      optimizeStatusLine.id = `${paneID}-obj-${promptBuilderObject?.id}-optimize-status`;
      optimizeStatusLine.classList.add('mb-1');
      const optimizeResultBox = document.createElement('div');
      optimizeResultBox.id = `${paneID}-obj-${promptBuilderObject?.id}-optimize-result`;

      optimizeControls.appendChild(targetRow);
      optimizeControls.appendChild(datasetBlock);
      optimizeControls.appendChild(metricRow);
      optimizeControls.appendChild(optimizeJudgeRow);
      optimizeControls.appendChild(optimizerRow);
      optimizeControls.appendChild(optimizeEstimateLine);
      optimizeControls.appendChild(optimizeRunWrapper);
      optimizeControls.appendChild(optimizeStatusLine);
      optimizeControls.appendChild(optimizeResultBox);

      optimizeUI = {
        targetSelect: optimizeTargetSelect,
        metricSelect: optimizeMetricSelect,
        judgeRow: optimizeJudgeRow,
        judgeSelect: optimizeJudgeSelect,
        maxDemosInput: optimizeMaxDemosInput,
        samplesHint: optimizeSamplesHint,
        estimateLine: optimizeEstimateLine,
        runButton: optimizeRunButton,
        runWrapper: optimizeRunWrapper,
        statusLine: optimizeStatusLine,
        resultBox: optimizeResultBox,
      };
      optimizeTargetSelect.addEventListener('change', updateOptimizeState);
      optimizeMetricSelect.addEventListener('change', updateOptimizeState);
      optimizeJudgeSelect.addEventListener('change', updateOptimizeState);

      optimizeSection = document.createElement('div');
      optimizeSection.classList.add('pb-section');
      optimizeSection.appendChild(optimizeHeader);
      optimizeSection.appendChild(optimizeDescription);
      optimizeSection.appendChild(optimizeControls);
      updateOptimizeState();
    }

    // Assemble the page into four visual sections: project & prompt selection,
    // LLM selection, the prompt workbench, and the experiment tracker/manifest.
    const createPageSection = (): HTMLDivElement => {
      const pageSection = document.createElement('div');
      pageSection.classList.add('pb-section');
      return pageSection;
    };

    promptBuilderContainer.appendChild(promptBuilderHeader);
    promptBuilderContainer.appendChild(promptBuilderDescription);

    const projectSection = createPageSection();
    projectSection.appendChild(promptBuilderProjectHeader);
    projectSection.appendChild(promptBuilderProjectSelectorHeader);
    projectSection.appendChild(projectFilter.filterRow);
    projectSection.appendChild(promptBuilderProjectSelectorDropdown);
    projectSection.appendChild(document.createElement('br'));
    projectSection.appendChild(promptBuilderPromptHeader);
    projectSection.appendChild(promptFilter.filterRow);
    projectSection.appendChild(promptBuilderPromptSelectorDropdown);
    projectSection.appendChild(promptDocSection);
    projectSection.appendChild(document.createElement('br'));
    projectSection.appendChild(promptBuilderModalButtonContainer);
    promptBuilderContainer.appendChild(projectSection);

    const llmSection = createPageSection();
    llmSection.appendChild(promptBuilderModelSelectorHeader);
    llmSection.appendChild(promptBuilderModelSelectorContainer);
    promptBuilderContainer.appendChild(llmSection);

    const workbenchSection = createPageSection();
    workbenchSection.appendChild(promptBuilderPromptingHeader);
    workbenchSection.appendChild(promptBulderPromptingExplainer);
    workbenchSection.appendChild(promptBuilderVariablesHeader);
    workbenchSection.appendChild(promptBuilderVariablesDescription);
    workbenchSection.appendChild(promptBuilderVariablesContainer);
    workbenchSection.appendChild(promptBuilderVariablesAddButton);
    workbenchSection.appendChild(document.createElement('br'));
    workbenchSection.appendChild(document.createElement('br'));
    workbenchSection.appendChild(promptBuilderPromptingContainer);
    workbenchSection.appendChild(document.createElement('br'));
    workbenchSection.appendChild(promptBuilderRunExperimentsButton);
    workbenchSection.appendChild(promptBuilderRunExperimentError);
    promptBuilderContainer.appendChild(workbenchSection);

    // Judging: its own section between running and the tracker. Configuration
    // for how responses are judged lives here (future council/jury options fold
    // in here too); the per-run verdicts and Judge buttons live in the tracker.
    const judgeSection = createPageSection();
    const promptBuilderJudgeHeader = document.createElement('h2');
    promptBuilderJudgeHeader.innerText = `${promptBuilderInterfaceText?.promptBuilderJudgeSectionHeading}`;
    const promptBuilderJudgeDescription = document.createElement('p');
    promptBuilderJudgeDescription.innerText = `${promptBuilderInterfaceText?.promptBuilderJudgeSectionDescription}`;
    judgeSection.appendChild(promptBuilderJudgeHeader);
    judgeSection.appendChild(promptBuilderJudgeDescription);
    judgeSection.appendChild(promptBuilderJudgeControls);
    promptBuilderContainer.appendChild(judgeSection);

    const trackerSection = createPageSection();
    trackerSection.appendChild(promptExperimentTrackerHeader);
    trackerSection.appendChild(promptExperimentEmptyHint);
    trackerSection.appendChild(promptExperimentContainer);
    trackerSection.appendChild(document.createElement('br'));
    trackerSection.appendChild(promptExperimentSaveButton);
    trackerSection.appendChild(promptExperimentSaveResultContainer);
    promptBuilderContainer.appendChild(trackerSection);

    // Manifest: configuration first, the action button below it
    const manifestSection = createPageSection();
    manifestSection.appendChild(promptExperimentManifestHeader);
    manifestSection.appendChild(promptExperimentManifestDescription);
    manifestSection.appendChild(promptExperimentIntegratedCallDiv);
    manifestSection.appendChild(promptExperimentManifestOptions);
    manifestSection.appendChild(document.createElement('br'));
    manifestSection.appendChild(promptExperimentCreateModelButtonWrapper);
    manifestSection.appendChild(document.createElement('br'));
    manifestSection.appendChild(document.createElement('br'));
    manifestSection.appendChild(promptExperimentResultContainer);
    promptBuilderContainer.appendChild(manifestSection);

    // Optimize (only when the deployment enables it): after the manifest, as
    // the closing step of the judge → optimise → judge-again loop.
    if (optimizeSection) {
      promptBuilderContainer.appendChild(optimizeSection);
    }

    return promptBuilderContainer;
}
