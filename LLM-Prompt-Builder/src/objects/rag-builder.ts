/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * RAG Builder (design §14) — first slice: author a RAG Setup as a governed
 * Model Manager artifact.
 *
 * What this slice does:
 *   - pick or create the RAG project (tags LLM/RAG-Engineering, function RAG —
 *     read-compatible with the portal RAG builder's conventions);
 *   - pick or create a RAG Setup model inside it;
 *   - author the setup: documentation (description, intended use,
 *     LIMITATIONS — owner requirement 2026-07-28), source, extraction,
 *     chunking, embedding, vector store and pipeline-table settings;
 *   - save: writes rag-setup.json (round-trip state), pipeline.yaml
 *     (generated governance artifact) and documentation.md onto the model,
 *     and sets the model's registration metadata — description, tags
 *     (LLM/RAG + embedding model + vector database), trainTable (the
 *     ingestion ledger reference) and the retrieval variable definitions.
 *
 * Later phases (buttons visible but disabled, so the roadmap is honest):
 * generate the Studio Flow + Job Execution job from the yaml, launch and
 * monitor ingestion, browse the ledger, cut over collection versions, and
 * test retrieval through the manifested model. The browser NEVER sees
 * vector-store credentials and never runs ingestion itself.
 */

import {
  createModel,
  deleteModel,
  deleteModelProject,
  createModelVersion, createModelContent,
  createModelProject,
  getModelContents,
  getModelDetails,
  getModelProjectModels,
  getModelProjects,
  getModelRepositoryInformation,
  updateModelAttributes,
  updateModelTags,
} from '../api/models-api';
import { getFileContent } from '../api/files-api';
import { resolveDomainSecrets } from '../api/credentials-api';
import { RAG_BACKENDS, backendEnabled, type RagBackend } from './rag-backends';
import { optionFlag } from './rag-options';
import { embeddingDimensions, embeddingTokenLimit } from './embedding-models';
import { ensureFolderPath, getFolderByPath, getFolderMembers } from '../api/folders-api';
import {
  createJobDefinition,
  createTransientJobDefinition,
  deleteJobDefinition,
  getJobDefinition,
  jobParameter,
  updateJobDefinition,
  type JobDefinition,
} from '../api/jobdef-api';
import {
  getJob,
  getJobProgressMessages,
  isTerminalJobState,
  launchJob,
} from '../api/jobexec-api';
import { getCasTableRows, getCaslibs } from '../api/cas-api';
import Modal from 'bootstrap/js/dist/modal';
import { getAppState } from '../state/app-state';
import { showToast } from '../ui/toast';
import { attachCombobox } from '../ui/combobox';
import { createListFilter, renderFilteredOptions } from '../ui/list-filter';
import { MODEL_CARD_FIELDS, createDocSection, createInfoIcon } from '../ui/doc-section';
import { manifestSettings, renderRetrievalModel } from './rag-manifest';
import { macroSafeQuestion, parseRetrievalLog, type RetrievalTestLog } from './rag-retrieval-log';
import { INGESTION_STEPS, buildFlow, ingestionChain } from './rag-flow';
import { readStepSpec, registerFlow, resolveStepIds } from '../api/dataflows-api';
import { createCreateModal } from '../ui/create-modal';
import { showConfirmModal } from '../ui/confirm-modal';
import type { InterfaceText } from '../types';
import type { DropdownOption } from '../types/models';
import type { RagBuilderConfig, RagBuilderText, RagSetup } from '../types/rag';

const SETUP_FILE = 'rag-setup.json';
const PIPELINE_FILE = 'pipeline.yaml';
const DOCUMENTATION_FILE = 'documentation.md';

/**
 * A pipeline table prefix: a SAS name, capped at 20 characters.
 *
 * The generated names are <prefix>_DOC_EVENTS and friends, and CAS stops at
 * 32 characters — 20 is what leaves room for the longest suffix. Leading
 * underscore is allowed because SAS names allow it; a leading digit is not,
 * for the same reason.
 */
const PREFIX_MAX = 20;
const PREFIX_PATTERN = /^[A-Za-z_][A-Za-z0-9_]{0,19}$/;
const COLLECTION_PATTERN = /^[a-z][a-z0-9_]{0,62}$/;

const EXTRACTORS = ['', 'plaintext', 'markdown', 'csv_json', 'html', 'pdf-text'];
const CHUNKERS = ['recursive', 'paragraph'];
/**
 * Chunkers that take an overlap.
 *
 * Mirrors run_chunk, which passes overlap_tokens to the recursive chunker
 * only — paragraph_chunks has no such parameter, so a value set for it is
 * silently discarded. Adding a chunker means deciding which list it joins.
 */
const CHUNKERS_WITH_OVERLAP = new Set(['recursive']);
/**
 * Above this share of the token window, the overlap is flagged as expensive.
 *
 * 10-20% of the chunk size is the commonly published starting band for RAG
 * chunking. Past it every boundary duplicates text that is embedded twice,
 * stored twice and retrieved twice, so the warning names the cost rather
 * than forbidding the choice - some corpora genuinely need it.
 */
const OVERLAP_WARN_SHARE = 0.2;
/**
 * A caslib nobody but its owner can read.
 *
 * CAS presents the caller's personal library as CASUSER, and as
 * CASUSER(<username>) once resolved. Either name is a dead end for a RAG
 * corpus: the ledger, the element and chunk tables and the run history all
 * land there, so a scheduled run under another identity — or a colleague
 * reopening the setup — finds nothing, and the failure looks like a lost
 * corpus rather than a permissions choice made months earlier.
 */
function isPersonalCaslib(name: string): boolean {
  return /^casuser(\s*\(.*\))?$/i.test(String(name || '').trim());
}

/**
 * Set a picker to a saved value, keeping it even when the live listing does
 * not carry it (a model retired from the project, a caslib the user can no
 * longer see). The alternative is a select silently snapping to its first
 * option, which is how a saved corpus starts writing somewhere else.
 */
function keepUnlisted(
  field: HTMLSelectElement | HTMLInputElement,
  value: string,
  missingNote: string
): void {
  if (field instanceof HTMLSelectElement && value) {
    const listed = Array.from(field.options).some((option) => option.value === value);
    if (!listed) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = `${value} — ${missingNote}`;
      field.insertBefore(option, field.firstChild);
    }
  }
  field.value = value;
}
/**
 * The backends this deployment offers, in list order. Each has its own
 * Options flag; '0' withholds it. If an administrator turns every one off we
 * fall back to offering all of them, because an empty dropdown leaves the
 * user unable to proceed with no explanation of why.
 */
function offeredBackends(config: RagBuilderConfig): ReadonlyArray<RagBackend> {
  const offered = RAG_BACKENDS.filter(
    (backend) => backendEnabled(config as unknown as Record<string, unknown>, backend)
  );
  return offered.length ? offered : RAG_BACKENDS;
}

/** The retrieval model's fixed in/out contract (owner requirement: the model
 * page must show clear variable definitions like manifested prompts do). */
const INPUT_VARIABLES = [
  { name: 'question', role: 'input', type: 'string', length: 32000, description: 'The user question to retrieve context for' },
  { name: 'k', role: 'input', type: 'decimal', description: 'Number of chunks to return (0 = manifested default)' },
  { name: 'filter_json', role: 'input', type: 'string', length: 4000, description: 'Optional JSON object of allow-listed column equality filters' },
  { name: 'retrieval_mode', role: 'input', type: 'string', length: 32, description: 'vector (hybrid arrives with a later release)' },
  { name: 'options', role: 'input', type: 'string', length: 4000, description: 'Optional JSON connection overrides for callers managing their own secrets' },
];
const OUTPUT_VARIABLES = [
  { name: 'context_dg', role: 'output', type: 'string', description: 'Context datagrid as JSON: document_id, chunk_id, filename, ingestion_timestamp, distance, document' },
  { name: 'context_envelope', role: 'output', type: 'string', description: 'JSON envelope: query, hits[], graph_context, retrieval_mode, collection, ingestion_run_id' },
  { name: 'retrieval_status', role: 'output', type: 'string', length: 512, description: 'ok or the failure message - retrieval never raises' },
  { name: 'run_time', role: 'output', type: 'decimal', description: 'Total seconds spent' },
];

function defaultSetup(config: RagBuilderConfig): RagSetup {
  return {
    version: 1,
    documentation: {
      modelPurpose: '', intendedUse: '', expectedBenefit: '',
      outOfScopeUseCases: '', limitations: '',
    },
    source: { path: '', includeCode: false },
    extraction: { extractor: '' },
    chunking: { chunker: 'recursive', inputTokenLimit: 256, overlapTokens: 30 },
    embedding: {
      // No model and no width until one is chosen. A default here would put a
      // model name and a vector width onto a corpus nobody selected them for,
      // and the width cannot be changed once the collection exists.
      model: '',
      dims: 0,
      deploymentType: config.deploymentType || 'k8s',
      scrEndpoint: '',
    },
    store: {
      backend: 'pgvector',
      host: '',
      port: 5432,
      database: '',
      // admin-set: see RagBuilderConfig.storeSslmode
      sslmode: config.storeSslmode || 'prefer',
      collection: '',
    },
    tables: { prefix: '', caslib: '' },
    artifactsFolder: '',
    pipelineVersion: 'v1',
    credentialDomain: config.credentialDomain || 'agentic-ai-keys',
    policies: policiesFrom(config),
  };
}

/** The deployment's operational policy, recorded onto the setup so the
 * generated artifacts say what THIS corpus does rather than deferring to a
 * central setting that may since have changed. */
function policiesFrom(config: RagBuilderConfig): RagSetup['policies'] {
  // optionFlag, not `value !== '0'`: recordHistory is a checkbox now and
  // stores a real false, which the old test read as true.
  const flag = optionFlag;
  const count = (value: string, fallback: number): number => {
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback;
  };
  return {
    deletedPolicy: config.deletedPolicy === 'purge' ? 'purge' : 'retire',
    retainDays: count(config.retainDays, 0),
    recordHistory: flag(config.recordHistory, true),
    embedReplicas: Math.max(1, count(config.embedReplicas, 1)),
    persistElements: flag(config.persistElements, true),
    persistChunks: flag(config.persistChunks, true),
  };
}

/** Flat, ordered YAML rendering of the setup — the governance artifact the
 * executables are generated from in P2 (values are JSON-encoded, which is
 * valid YAML for scalars and keeps quoting/escaping exact). */
function renderPipelineYaml(setup: RagSetup): string {
  const scalar = (value: string | number): string => JSON.stringify(value);
  const lines = [
    '# pipeline.yaml - generated by the RAG Builder; the RAG Setup source of',
    '# truth. The Studio Flow and the Ingest-Documents job are generated FROM',
    '# this file (design section 2 source-of-truth rule). Regenerate through',
    '# the RAG Builder instead of editing by hand.',
    'version: 1',
    'source:',
    `  path: ${scalar(setup.source.path)}`,
    `  includeCode: ${setup.source.includeCode === true}`,
    'extraction:',
    `  extractor: ${scalar(setup.extraction.extractor || 'auto')}`,
    'chunking:',
    `  chunker: ${scalar(setup.chunking.chunker)}`,
    `  inputTokenLimit: ${setup.chunking.inputTokenLimit}`,
    `  overlapTokens: ${setup.chunking.overlapTokens}`,
    'embedding:',
    `  model: ${scalar(setup.embedding.model)}`,
    `  dims: ${setup.embedding.dims}`,
    `  deploymentType: ${scalar(setup.embedding.deploymentType)}`,
    `  scrEndpoint: ${scalar(setup.embedding.scrEndpoint || '')}`,
    'store:',
    `  backend: ${scalar(setup.store.backend)}`,
    `  host: ${scalar(setup.store.host)}`,
    `  port: ${setup.store.port}`,
    `  database: ${scalar(setup.store.database)}`,
    `  sslmode: ${scalar(setup.store.sslmode)}`,
    `  collection: ${scalar(setup.store.collection)}`,
    'tables:',
    `  prefix: ${scalar(setup.tables.prefix)}`,
    `  caslib: ${scalar(setup.tables.caslib)}`,
    `pipelineVersion: ${scalar(setup.pipelineVersion)}`,
    `credentialDomain: ${scalar(setup.credentialDomain)}`,
    'policies:',
    `  deletedPolicy: ${scalar(setup.policies?.deletedPolicy ?? 'retire')}`,
    `  retainDays: ${setup.policies?.retainDays ?? 0}`,
    `  recordHistory: ${setup.policies?.recordHistory !== false}`,
    `  embedReplicas: ${setup.policies?.embedReplicas ?? 1}`,
    `  persistElements: ${setup.policies?.persistElements !== false}`,
    `  persistChunks: ${setup.policies?.persistChunks !== false}`,
  ];
  return lines.join('\n') + '\n';
}

/** Section headings for documentation.md, in MODEL_CARD_FIELDS order. */
const MODEL_CARD_HEADINGS: Record<(typeof MODEL_CARD_FIELDS)[number], string> = {
  modelPurpose: 'Purpose',
  intendedUse: 'Intended use',
  expectedBenefit: 'Expected benefit',
  outOfScopeUseCases: 'Out-of-scope uses',
  limitations: 'Limitations',
};

function renderDocumentationMarkdown(setup: RagSetup, setupName: string): string {
  return [
    `# ${setupName}`,
    '',
    ...MODEL_CARD_FIELDS.flatMap((field) => [
      `## ${MODEL_CARD_HEADINGS[field]}`,
      '',
      setup.documentation[field] || '_Not documented yet._',
      '',
    ]),
  ].join('\n');
}

export async function buildRagBuilder(
  config: RagBuilderConfig,
  paneID: string,
  interfaceText: InterfaceText
): Promise<HTMLElement> {
  const text = (interfaceText.ragBuilder ?? {}) as RagBuilderText;
  const str = (key: string, fallback: string): string => {
    const value = text[key];
    return typeof value === 'string' ? value : fallback;
  };
  const idOf = (suffix: string): string => `${paneID}-obj-${config.id}-${suffix}`;

  let selectedProjectID = '';
  let selectedSetupID = '';
  let selectedSetupName = '';
  /** Tags of the loaded setup that this builder manages (removed on re-save). */
  let managedTags: string[] = [];
  /** URI of the setup's generated ingestion job definition ('' = none yet). */
  let currentJobUri = '';
  // the operational policy of the setup currently open. Authored centrally in
  // the Options, but carried on the setup so re-saving an existing corpus does
  // not silently re-baseline it onto a policy that changed since.
  let currentPolicies: RagSetup['policies'] | null = null;
  /** Poll timer of a running ingestion launch. */
  let pollTimer: number | null = null;
  /**
   * The live-run clock.
   *
   * An ingestion embeds one chunk per call, so a few hundred documents is
   * minutes, not seconds. Without a visible elapsed time a running job and a
   * finished one look identical, and the reasonable next move - open the
   * ledger - then reports an empty corpus that is merely not written yet.
   * These three carry "is a run in flight, and for how long" to whoever asks.
   */
  let runTicker: number | null = null;
  let runStartedAt = 0;
  let runActive = false;

  const container = document.createElement('div');
  container.className = 'container-fluid py-3';

  // ---- header ---------------------------------------------------------------
  const heading = document.createElement('h2');
  heading.textContent = str('ragBuilderHeading', 'RAG Builder');
  const subtitle = document.createElement('p');
  subtitle.className = 'text-muted';
  subtitle.textContent = str(
    'ragBuilderDescription',
    'Author a governed RAG setup: documents in, an incremental ingestion pipeline, and a retrieval model out. Everything is saved to SAS Model Manager; vector-store credentials stay in the SAS Viya credential domain and never enter this browser.'
  );
  container.appendChild(heading);
  container.appendChild(subtitle);

  // ---- status area ----------------------------------------------------------
  const status = document.createElement('div');
  status.id = idOf('status');
  container.appendChild(status);
  // Success is transient, failure is not. A confirmation the user has already
  // seen should not sit on the page competing with the next one, so it goes
  // through the same toast the Prompt Builder uses; anything the user still
  // has to act on stays in the inline alert until it is resolved.
  const showStatus = (variant: 'success' | 'danger' | 'info', message: string): void => {
    if (variant === 'success') {
      status.replaceChildren();
      showToast(message);
      return;
    }
    const alert = document.createElement('div');
    alert.className = `alert alert-${variant} py-2`;
    alert.setAttribute('role', 'alert');
    alert.textContent = message;
    status.replaceChildren(alert);
  };
  const clearStatus = (): void => status.replaceChildren();

  // ---- small form helpers ---------------------------------------------------
  const card = (titleText: string, hint?: string): [HTMLElement, HTMLElement] => {
    const wrapper = document.createElement('div');
    wrapper.className = 'card mb-3';
    const body = document.createElement('div');
    body.className = 'card-body';
    const title = document.createElement('h5');
    title.className = 'card-title';
    title.textContent = titleText;
    body.appendChild(title);
    if (hint) {
      const hintEl = document.createElement('p');
      hintEl.className = 'text-muted small';
      hintEl.textContent = hint;
      body.appendChild(hintEl);
    }
    wrapper.appendChild(body);
    return [wrapper, body];
  };

  /**
   * A labelled control.
   *
   * `info` adds the ⓘ whose tooltip explains what the setting actually does —
   * most of these choices are irreversible once a collection is built, and a
   * bare label cannot say that. `required` marks the field both visually and
   * to assistive technology; validateSetup still enforces it, because the
   * browser's own required handling never runs (nothing here is a <form>).
   */
  const labeled = (
    parent: HTMLElement,
    id: string,
    labelText: string,
    element: HTMLElement,
    columns = 'col-md-4',
    info = '',
    required = false
  ): HTMLDivElement => {
    const column = document.createElement('div');
    column.className = columns;
    const label = document.createElement('label');
    label.className = 'form-label fw-bold mb-1';
    label.htmlFor = id;
    label.textContent = labelText;
    if (required) {
      const mark = document.createElement('span');
      mark.className = 'text-danger ms-1';
      mark.textContent = '*';
      mark.setAttribute('aria-hidden', 'true');
      label.appendChild(mark);
      element.setAttribute('required', '');
      element.setAttribute('aria-required', 'true');
    }
    element.id = id;
    if (info) {
      const holder = document.createElement('div');
      holder.className = 'info-container';
      holder.appendChild(label);
      holder.appendChild(createInfoIcon(labelText, info));
      column.appendChild(holder);
    } else {
      column.appendChild(label);
    }
    column.appendChild(element);
    parent.appendChild(column);
    return column;
  };

  const textInput = (value = '', placeholder = ''): HTMLInputElement => {
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'form-control';
    input.value = value;
    input.placeholder = placeholder;
    return input;
  };

  const numberInput = (value: number, min: number): HTMLInputElement => {
    const input = document.createElement('input');
    input.type = 'number';
    input.className = 'form-control';
    input.value = String(value);
    input.min = String(min);
    return input;
  };

  const selectInput = (options: string[], value: string, labels?: Record<string, string>): HTMLSelectElement => {
    const select = document.createElement('select');
    select.className = 'form-select';
    for (const option of options) {
      const entry = document.createElement('option');
      entry.value = option;
      entry.textContent = labels?.[option] ?? (option !== '' ? option : (labels?.[''] ?? option));
      select.appendChild(entry);
    }
    select.value = value;
    return select;
  };

  const textArea = (rows: number, placeholder: string): HTMLTextAreaElement => {
    const area = document.createElement('textarea');
    area.className = 'form-control';
    area.rows = rows;
    area.placeholder = placeholder;
    return area;
  };

  // ---- project + setup selection -------------------------------------------
  // Laid out as the Prompt Builder's project section: a `pb-section` block
  // with h2/h3 headings and full-width pickers stacked in the order the work
  // happens, each preceded by its name/creator filter and searchable by
  // typing. Model Manager projects accumulate — a deployment with two hundred
  // of them turns a bare dropdown into a scroll hunt, and it is the same list
  // in both builders, so it should not be two different controls.
  const selectionSection = document.createElement('div');
  selectionSection.className = 'pb-section';
  const selectionHeading = document.createElement('h2');
  selectionHeading.textContent = str('ragBuilderSelectionHeading', 'Project and setup');
  const selectionHint = document.createElement('p');
  selectionHint.textContent = str(
    'ragBuilderSelectionHint',
    'A RAG project groups related setups in SAS Model Manager; a setup is one document corpus wired to one vector-store collection.'
  );
  selectionSection.appendChild(selectionHeading);
  selectionSection.appendChild(selectionHint);

  const filterLabels = {
    namePlaceholder: str('ragBuilderFilterNamePlaceholder', 'Filter by name…'),
    userLabel: str('ragBuilderFilterUserLabel', 'Filter by user'),
    userAll: str('ragBuilderFilterUserAll', 'All users'),
  };

  const projectHeading = document.createElement('h3');
  projectHeading.textContent = str('ragBuilderProjectLabel', 'RAG project:');
  const projectSelect = selectInput([], '');
  projectSelect.id = idOf('project');
  const projectFilter = createListFilter(idOf('project-filter'), filterLabels, () =>
    renderProjectOptions()
  );
  selectionSection.appendChild(projectHeading);
  selectionSection.appendChild(projectFilter.filterRow);
  selectionSection.appendChild(projectSelect);
  selectionSection.appendChild(document.createElement('br'));

  const setupHeading = document.createElement('h3');
  setupHeading.textContent = str('ragBuilderSetupLabel', 'RAG setup:');
  const setupSelect = selectInput([], '');
  setupSelect.id = idOf('setup');
  const setupFilter = createListFilter(idOf('setup-filter'), filterLabels, () =>
    renderSetupOptions()
  );
  selectionSection.appendChild(setupHeading);
  selectionSection.appendChild(setupFilter.filterRow);
  selectionSection.appendChild(setupSelect);
  selectionSection.appendChild(document.createElement('br'));

  // Creating sits below the pickers in one button row, as it does in the
  // Prompt Builder — an input box wedged beside a dropdown reads as part of
  // the selection rather than as a separate act.
  const selectionButtons = document.createElement('div');
  selectionButtons.className = 'd-flex flex-wrap align-items-center gap-2';
  createCreateModal(
    selectionButtons,
    `${idOf('create-project')}`,
    {
      modalTitle: str('ragBuilderNewProjectButton', 'Create project'),
      modalDescription: str(
        'ragBuilderNewProjectModalHint',
        'A RAG project groups related setups in SAS Model Manager. Its description is what a governance reviewer reads first.'
      ),
      nameLabel: str('ragBuilderNewProjectNameLabel', 'Project name'),
      descriptionLabel: str('ragBuilderNewProjectDescriptionLabel', 'Description'),
      closeButtonText: str('ragBuilderModalClose', 'Cancel'),
      saveButtonText: str('ragBuilderModalSave', 'Create'),
    },
    () => void createProjectFromModal()
  );
  createCreateModal(
    selectionButtons,
    `${idOf('create-setup')}`,
    {
      modalTitle: str('ragBuilderNewSetupButton', 'Create setup'),
      modalDescription: str(
        'ragBuilderNewSetupModalHint',
        'A setup is one document corpus wired to one vector-store collection. Choose a project first.'
      ),
      nameLabel: str('ragBuilderNewSetupNameLabel', 'Setup name'),
      descriptionLabel: str('ragBuilderNewSetupDescriptionLabel', 'Description'),
      closeButtonText: str('ragBuilderModalClose', 'Cancel'),
      saveButtonText: str('ragBuilderModalSave', 'Create'),
    },
    () => void createSetupFromModal()
  );

  // Open in SAS Model Manager: the setup IS a Model Manager model, and its
  // versions, history and permissions live there rather than here.
  const openInMMButton = document.createElement('a');
  openInMMButton.className = 'btn btn-outline-secondary disabled';
  openInMMButton.target = '_blank';
  openInMMButton.rel = 'noopener noreferrer';
  openInMMButton.setAttribute('aria-disabled', 'true');
  openInMMButton.textContent = str('ragBuilderOpenInMM', 'Open in SAS Model Manager');
  selectionButtons.appendChild(openInMMButton);

  // Destructive actions sit apart from the rest, right-aligned.
  const destructive = document.createElement('div');
  destructive.className = 'd-flex flex-wrap align-items-center gap-2 ms-auto';
  const deleteSetupButton = document.createElement('button');
  deleteSetupButton.type = 'button';
  deleteSetupButton.className = 'btn btn-danger';
  deleteSetupButton.disabled = true;
  deleteSetupButton.textContent = str('ragBuilderDeleteSetupButton', 'Delete setup');
  const deleteProjectButton = document.createElement('button');
  deleteProjectButton.type = 'button';
  deleteProjectButton.className = 'btn btn-danger';
  deleteProjectButton.disabled = true;
  deleteProjectButton.textContent = str('ragBuilderDeleteProjectButton', 'Delete project');
  destructive.appendChild(deleteSetupButton);
  destructive.appendChild(deleteProjectButton);
  selectionButtons.appendChild(destructive);
  selectionSection.appendChild(selectionButtons);
  container.appendChild(selectionSection);
  // Typing in either picker narrows the open list live; the underlying
  // selects stay scriptable, so the load/refresh paths are unchanged.
  attachCombobox(projectSelect);
  attachCombobox(setupSelect);

  // ---- the setup editor (hidden until a setup is selected) ------------------
  const editor = document.createElement('div');
  editor.style.display = 'none';
  container.appendChild(editor);

  // Documentation — owner requirement: the author documents description,
  // intended use and LIMITATIONS up front, like the Prompt Builder's top
  // section; saved as the model description + documentation.md.
  const [documentationCard, documentationBody] = card(
    str('ragBuilderDocumentationHeading', 'Documentation'),
    str(
      'ragBuilderDocumentationHint',
      'Written by the setup author, shown to every consumer: what this corpus is, what it should be used for, and where its limits are. Saved onto the Model Manager model so governance reviews read your own caveats.'
    )
  );
  // The five mdb model-card fields, in the Prompt Builder's collapsible block
  // with the same info tooltips. Optional and collapsed on purpose: the
  // description is what a consumer needs to recognise the corpus, and the
  // model card is what a governance review asks for later.
  const ragDoc = createDocSection(idOf('setup'), {
    sectionLabel: str('ragBuilderDocSectionLabel', 'Optional documentation'),
    sectionHint: str(
      'ragBuilderDocSectionHint',
      'Model-card fields, saved onto the Model Manager model as attributes so they appear wherever the setup is opened. Every field is optional — a blank one honestly reads as not stated.'
    ),
    fields: {
      modelPurpose: {
        label: str('ragBuilderDocModelPurpose', 'Purpose'),
        info: str(
          'ragBuilderDocModelPurposeInfo',
          'Why this corpus exists and the decision it supports. A reviewer reads this first, to judge whether everything else is proportionate.'
        ),
      },
      intendedUse: {
        label: str('ragBuilderDocIntendedUse', 'Intended use'),
        info: str(
          'ragBuilderDocIntendedUseInfo',
          'Which questions this collection should be asked, and by whom. Retrieval will happily answer questions the corpus cannot support — this is where you say which those are.'
        ),
      },
      expectedBenefit: {
        label: str('ragBuilderDocExpectedBenefit', 'Expected benefit'),
        info: str(
          'ragBuilderDocExpectedBenefitInfo',
          'What improves because this corpus exists — faster answers, fewer escalations, wider coverage — and how you would know.'
        ),
      },
      outOfScopeUseCases: {
        label: str('ragBuilderDocOutOfScope', 'Out-of-scope uses'),
        info: str(
          'ragBuilderDocOutOfScopeInfo',
          'Uses this collection must NOT be put to, even though it would return something. Naming them is what makes a later misuse a deviation rather than a surprise.'
        ),
      },
      limitations: {
        label: str('ragBuilderDocLimitations', 'Limitations'),
        info: str(
          'ragBuilderDocLimitationsInfo',
          'Known gaps: coverage, freshness, languages, document quality, anything the extractor or the chunker handles badly. Consumers inherit these whether or not they are written down.'
        ),
      },
    },
  });
  // Its own save, like the Prompt Builder's: documentation is worth writing
  // down long before a setup is complete enough to pass validation, and
  // making someone finish choosing a vector database first is how the
  // governance fields end up empty.
  const docSaveButton = document.createElement('button');
  docSaveButton.type = 'button';
  docSaveButton.classList.add('btn', 'btn-outline-primary', 'btn-sm');
  docSaveButton.textContent = str('ragBuilderSaveDocumentationButton', 'Save documentation');
  docSaveButton.disabled = true;
  ragDoc.section.appendChild(docSaveButton);
  documentationBody.appendChild(ragDoc.section);
  editor.appendChild(documentationCard);

  // Pipeline
  // The ingestion is four stages, and they are grouped as four, in the order
  // the data moves: documents are found, cut into chunks, turned into vectors,
  // and landed in a store. One long row of unrelated controls hid which
  // setting affected which stage — and the settings interact within a stage
  // (overlap with the token window, dimensions with the model) far more than
  // across them.
  const [documentsCard, documentsBody] = card(
    str('ragBuilderDocumentsHeading', '1. Documents'),
    str(
      'ragBuilderDocumentsHint',
      'Where the corpus comes from and how text is read out of it. The ingestion walks this folder on every run and compares what it finds against the ledger, so adding or removing files there is how a corpus changes.'
    )
  );
  const documentsRow = document.createElement('div');
  documentsRow.className = 'row g-3';
  // rag_core.sources takes the scheme a SAS Studio path selector emits -
  // sasserver: for the compute file system, sascontent: for SAS Content - so
  // both have always been readable. Only the Builder never offered the
  // choice, which made SAS Content look unsupported.
  const sourceKindField = selectInput(['sasserver', 'sascontent'], 'sasserver', {
    sasserver: str('ragBuilderSourceKindServer', 'Compute server file system'),
    sascontent: str('ragBuilderSourceKindContent', 'SAS Content'),
  });
  labeled(documentsRow, idOf('source-kind'), str('ragBuilderSourceKindLabel', 'Document location:'), sourceKindField, 'col-md-4',
    str('ragBuilderSourceKindInfo', 'Where the corpus lives. The compute server file system is a path on the SAS Compute server (not your workstation) and suits large corpora already staged on disk. SAS Content is the governed folder tree you see in SAS Drive, read over the Files API - convenient, permission-aware, and slower per document.'), true);
  const sourcePathField = textInput('', '/data/documents');
  labeled(documentsRow, idOf('source-path'), str('ragBuilderSourcePathLabel', 'Document folder:'), sourcePathField, 'col-md-8',
    str('ragBuilderSourcePathInfo', 'The folder to crawl, walked recursively, with every readable file treated as a candidate document. A path the ingestion cannot see yields a run that finds nothing rather than one that fails loudly, so check it against the location type on the left.'), true);
  const sourcePlaceholder = (): void => {
    sourcePathField.placeholder =
      sourceKindField.value === 'sascontent' ? '/Public/Policies' : '/data/documents';
  };
  sourceKindField.addEventListener('change', sourcePlaceholder);
  sourcePlaceholder();
  const extractorField = selectInput(EXTRACTORS, '', { '': str('ragBuilderExtractorAuto', 'Automatic (by file format)') });
  labeled(documentsRow, idOf('extractor'), str('ragBuilderExtractorLabel', 'Extractor:'), extractorField, 'col-md-4',
    str('ragBuilderExtractorInfo', 'How text is pulled out of each file. Automatic picks per file format and is almost always right; forcing one applies it to EVERY file, so a PDF read as plain text yields nonsense rather than an error. Some formats need optional Python packages — see the administration guide.'));
  // Source files are their own decision, not an extractor choice: the
  // question is whether they belong in the corpus at all, and the answer is
  // usually no. Off by default, and never a silent exclusion - a skipped file
  // is listed in the ledger and the run log with its reason.
  const includeCodeWrap = document.createElement('div');
  includeCodeWrap.className = 'col-md-8 d-flex align-items-center';
  const includeCodeCheck = document.createElement('div');
  includeCodeCheck.className = 'form-check mb-0';
  const includeCodeField = document.createElement('input');
  includeCodeField.type = 'checkbox';
  includeCodeField.className = 'form-check-input';
  includeCodeField.id = idOf('include-code');
  const includeCodeLabel = document.createElement('label');
  includeCodeLabel.className = 'form-check-label';
  includeCodeLabel.htmlFor = includeCodeField.id;
  includeCodeLabel.textContent = str('ragBuilderIncludeCodeLabel', 'Ingest source-code files as plain text');
  includeCodeCheck.append(includeCodeField, includeCodeLabel);
  includeCodeWrap.appendChild(includeCodeCheck);
  includeCodeWrap.appendChild(
    createInfoIcon(
      str('ragBuilderIncludeCodeLabel', 'Ingest source-code files as plain text'),
      str(
        'ragBuilderIncludeCodeInfo',
        'Off by default. Source files (.py, .sas, .r, .js, .ts, .sql and ~30 more) are skipped, because a documents folder that sits inside a project would otherwise fill the collection with build scripts that answer no business question. Skipped files are always listed in the ledger and the run log with the reason, never dropped silently. Turn this on for a corpus that is ABOUT code: each file is then indexed as plain text, with its file name kept so a hit can be traced back.'
      )
    )
  );
  documentsRow.appendChild(includeCodeWrap);
  documentsBody.appendChild(documentsRow);
  editor.appendChild(documentsCard);

  const [embeddingCard, embeddingBody] = card(
    str('ragBuilderEmbeddingHeading', '2. Embedding'),
    str(
      'ragBuilderEmbeddingHint',
      'The model that turns each chunk into a vector, run as a governed SCR container inside your deployment. Both settings here are fixed for the life of the collection: changing either means rebuilding it.'
    )
  );
  const embeddingRow = document.createElement('div');
  embeddingRow.className = 'row g-3';

  // The embedding model is LISTED, and never defaulted. Only a model
  // registered in the embedding project has a container behind it, and a name
  // with nothing behind it does not fail until the first embed call — after
  // the crawl and the chunking have already run, reported as an HTTP 404
  // rather than as a wrong choice. Preselecting one would also silently
  // decide the vector width for a corpus nobody had chosen a model for, so
  // the picker starts empty and the setup will not validate until it is set.
  const embeddingProject = String(config.embeddingProjectID || '').trim();
  let registeredEmbeddings: string[] = [];
  if (embeddingProject) {
    try {
      registeredEmbeddings = (await getModelProjectModels(embeddingProject))
        .map((option) => String(option.innerHTML ?? '').trim())
        .filter(Boolean)
        .sort((left, right) => left.localeCompare(right));
    } catch (error) {
      console.debug('RAG Builder: embedding model listing failed', error);
    }
  }
  const embedModelField = selectInput(['', ...registeredEmbeddings], '', {
    '': str('ragBuilderEmbedModelPlaceholder', 'Select an embedding model…'),
  });
  labeled(embeddingRow, idOf('embed-model'), str('ragBuilderEmbedModelLabel', 'Embedding model:'), embedModelField, 'col-md-6',
    str('ragBuilderEmbedModelInfo', 'The model that turns each chunk into a vector, listed from the deployment\'s embedding model project. It cannot be changed later without re-embedding the whole corpus: a collection can only be searched with the model that built it, because vectors from two models are not comparable.'), true);
  const embedDimsField = numberInput(384, 1);
  labeled(embeddingRow, idOf('embed-dims'), str('ragBuilderEmbedDimsLabel', 'Embedding dimensions:'), embedDimsField, 'col-md-3',
    str('ragBuilderEmbedDimsInfo', 'The width of the vector column, taken from the chosen model — it is a property of the model, not a setting, so it is read-only whenever the model publishes one. It stays editable only for a model registered outside the shipped set, where no width is published. Getting it wrong makes the collection unusable and it cannot be widened afterwards.'), true);
  // The vector column is created at this width and cannot be widened
  // afterwards, so the dimension follows the model wherever the model's fact
  // sheet publishes one. It stays editable: a model registered outside the
  // shipped set publishes no width, and guessing one is worse than asking.
  const followEmbeddingModel = (): void => {
    const dims = embeddingDimensions(embedModelField.value);
    // Read-only rather than merely prefilled: the width belongs to the model.
    // A field that looks editable invites a value the store will accept and
    // the embedding container will then contradict, one run later.
    embedDimsField.readOnly = dims > 0;
    embedDimsField.value = dims > 0 ? String(dims) : '';
  };
  embedModelField.addEventListener('change', followEmbeddingModel);
  followEmbeddingModel();
  if (!registeredEmbeddings.length) {
    const note = document.createElement('div');
    note.className = 'alert alert-danger py-2 px-3 mt-2 mb-0';
    note.textContent = embeddingProject
      ? str(
          'ragBuilderNoEmbeddingModels',
          'No embedding models could be listed from the configured project, so no setup can be saved. Check that the project holds registered embedding models and that you can read it.'
        )
      : str(
          'ragBuilderNoEmbeddingProject',
          'No embedding model project is configured, so no embedding model can be chosen and no setup can be saved. Ask your administrator to set the "Embedding model project ID" option.'
        );
    embeddingRow.appendChild(note);
  }
  embeddingBody.appendChild(embeddingRow);
  editor.appendChild(embeddingCard);

  const [chunkingCard, chunkingBody] = card(
    str('ragBuilderChunkingHeading', '3. Chunking'),
    str(
      'ragBuilderChunkingHint',
      'How each document is cut into the pieces that get embedded and retrieved. A chunk is the unit an answer is built from, so these settings decide how much context a single hit carries — within whatever the embedding model above can accept.'
    )
  );
  const chunkingRow = document.createElement('div');
  chunkingRow.className = 'row g-3';
  const chunkerField = selectInput(CHUNKERS, 'recursive');
  labeled(chunkingRow, idOf('chunker'), str('ragBuilderChunkerLabel', 'Chunker:'), chunkerField, 'col-md-4',
    str('ragBuilderChunkerInfo', 'How a document is cut into retrievable pieces. Recursive splits on the largest natural boundary that fits the window (sections, then paragraphs, then sentences); paragraph keeps paragraphs whole and is better for short, well-structured documents.'));
  const tokenLimitField = numberInput(256, 16);
  const tokenWindowColumn = labeled(chunkingRow, idOf('token-limit'), str('ragBuilderTokenLimitLabel', 'Embedding token window:'), tokenLimitField, 'col-md-4',
    str('ragBuilderTokenLimitInfo', 'The largest chunk, in tokens. Capped by the embedding model chosen above: text beyond a model window is silently dropped rather than rejected, so an oversized chunk would be embedded from its opening only, and retrieval would then match on text the answer never sees. The ingestion applies a further safety margin below this, because token counts are estimates.'), true);
  const overlapField = numberInput(30, 0);
  const tokenWindowNote = document.createElement('div');
  tokenWindowNote.className = 'form-text';
  tokenWindowColumn.appendChild(tokenWindowNote);
  const overlapColumn = labeled(chunkingRow, idOf('overlap'), str('ragBuilderOverlapLabel', 'Chunk overlap (tokens):'), overlapField, 'col-md-4',
    str('ragBuilderOverlapInfo', 'How much text each chunk repeats from the previous one, so a sentence split across a boundary is still retrievable whole. Must be smaller than the token window — an overlap at or above it would never advance through the document. Larger overlap means more chunks, more embedding cost and more near-duplicate hits.'), true);
  // Overlap is bounded by the window it overlaps within. Bounding the input
  // as the window changes means the browser's own stepper cannot walk into an
  // impossible value; validateSetup still checks, because typing bypasses it.
  const boundOverlap = (): void => {
    const window = Math.max(16, Number(tokenLimitField.value) || 0);
    overlapField.max = String(Math.max(0, window - 1));
    if (Number(overlapField.value) >= window) overlapField.value = String(window - 1);
  };
  tokenLimitField.addEventListener('change', boundOverlap);
  tokenLimitField.addEventListener('input', boundOverlap);
  boundOverlap();

  // The window a model can actually read is a property OF the model, which
  // is why the embedding card sits above this one: choosing the model first
  // means the ceiling is known before anyone picks a number under it. An
  // unknown model publishes no ceiling, and then nothing is imposed.
  const capTokenWindow = (clamp = true): void => {
    const ceiling = embeddingTokenLimit(embedModelField.value);
    if (ceiling > 0) {
      tokenLimitField.max = String(ceiling);
      // Clamp only when someone picked a different model. On LOAD the saved
      // number is what the collection was actually built with: quietly
      // lowering it here would hide a setup that is already broken, where
      // validateSetup names it instead.
      if (clamp && Number(tokenLimitField.value) > ceiling) tokenLimitField.value = String(ceiling);
      tokenWindowNote.textContent = str(
        'ragBuilderTokenLimitCeiling',
        'This model accepts at most {max} tokens per chunk.'
      ).replace('{max}', String(ceiling));
    } else {
      tokenLimitField.removeAttribute('max');
      tokenWindowNote.textContent = embedModelField.value
        ? str(
            'ragBuilderTokenLimitUnknown',
            'This model publishes no token limit, so the window is not capped here - check the model card.'
          )
        : '';
    }
    boundOverlap();
  };

  // Overlap is a property of the recursive chunker alone — run_chunk passes
  // overlap_tokens only to that one, and paragraph_chunks does not even
  // accept it. Leaving the field visible for a chunker that ignores it
  // invites tuning a number that does nothing.
  const followChunker = (reset = true): void => {
    const uses = CHUNKERS_WITH_OVERLAP.has(chunkerField.value);
    overlapColumn.style.display = uses ? '' : 'none';
    if (!uses && reset) overlapField.value = '0';
  };
  chunkerField.addEventListener('change', () => followChunker(true));
  followChunker();

  // Overlap feedback lands when focus leaves the field, not at save time.
  // A number that cannot work should be said while the user is still looking
  // at it. Two levels: impossible (blocks the save) and expensive (does not).
  //
  // The 10-20% band is the widely published starting range for RAG chunking;
  // above it the duplicated text is embedded and stored twice for every
  // chunk boundary, which is paid for in embedding cost, table size and
  // near-duplicate hits at retrieval.
  const overlapNote = document.createElement('div');
  overlapNote.className = 'form-text';
  overlapColumn.appendChild(overlapNote);
  const judgeOverlap = (): void => {
    if (!CHUNKERS_WITH_OVERLAP.has(chunkerField.value)) {
      overlapNote.textContent = '';
      overlapNote.className = 'form-text';
      return;
    }
    const window = Number(tokenLimitField.value) || 0;
    const overlap = Number(overlapField.value);
    if (!Number.isFinite(overlap) || overlap < 0) {
      overlapNote.className = 'form-text text-danger';
      overlapNote.textContent = str('ragBuilderOverlapNegativeNote', 'The overlap cannot be negative.');
      return;
    }
    if (window > 0 && overlap >= window) {
      overlapNote.className = 'form-text text-danger';
      overlapNote.textContent = str(
        'ragBuilderOverlapTooLargeNote',
        'The overlap must stay below the {window}-token window - at or above it, chunking never moves forward through a document.'
      ).replace('{window}', String(window));
      return;
    }
    const share = window > 0 ? overlap / window : 0;
    if (share > OVERLAP_WARN_SHARE) {
      overlapNote.className = 'form-text text-warning';
      overlapNote.textContent = str(
        'ragBuilderOverlapHighNote',
        '{percent}% of the window. Usual practice is 10-20%; above that the repeated text is embedded and stored twice at every boundary, and retrieval returns more near-duplicates.'
      ).replace('{percent}', String(Math.round(share * 100)));
      return;
    }
    overlapNote.className = 'form-text';
    overlapNote.textContent =
      window > 0 && overlap > 0
        ? str('ragBuilderOverlapShareNote', '{percent}% of the window.').replace(
            '{percent}',
            String(Math.round(share * 100))
          )
        : '';
  };
  overlapField.addEventListener('blur', judgeOverlap);
  overlapField.addEventListener('change', judgeOverlap);
  tokenLimitField.addEventListener('blur', judgeOverlap);
  tokenLimitField.addEventListener('change', judgeOverlap);
  judgeOverlap();
  // The embedding card is built first, so the model's ceiling is wired here,
  // where the window field it caps exists. Changing the model re-applies it.
  embedModelField.addEventListener('change', () => capTokenWindow(true));
  capTokenWindow();

  chunkingBody.appendChild(chunkingRow);
  editor.appendChild(chunkingCard);



  // Vector store + pipeline tables
  const [storeCard, storeBody] = card(
    str('ragBuilderStoreHeading', '4. Vector store'),
    str(
      'ragBuilderStoreHint',
      'Where the store lives and who may reach it both come from the credential domain — the connection is resolved server-side and never enters this browser as a secret.'
    )
  );
  const storeRow = document.createElement('div');
  storeRow.className = 'row g-3';
  // The CAS working tables are a separate concern from the vector store: they
  // are this pipeline's scratch space and audit trail, they live in CAS
  // rather than in the database, and they are named independently of the
  // collection. Grouping them with the store made two unrelated naming
  // decisions look like one.
  const [tablesCard, tablesBody] = card(
    str('ragBuilderTablesHeading', '5. Pipeline tables'),
    str(
      'ragBuilderTablesHint',
      'The CAS tables this pipeline writes as it runs — the ingestion ledger, the element and chunk tables and the run history. They are rebuilt by each run and are what you read to see what a run did.'
    )
  );
  const tablesRow = document.createElement('div');
  tablesRow.className = 'row g-3';
  // Which stores this deployment offers, and which of those THIS user holds
  // credentials for. Two separate questions: the admin decides what the site
  // runs, the credential domain decides who may use it. A backend the user
  // cannot reach stays visible but disabled, naming the missing entry — a
  // hidden option looks like the feature does not exist.
  const offered = offeredBackends(config);
  const credentialDomain = String(config.credentialDomain || 'agentic-ai-keys').trim();
  const heldEntries = credentialDomain
    ? await resolveDomainSecrets(credentialDomain)
    : {};
  const backendReachable = (backend: RagBackend): boolean =>
    !credentialDomain || backend.entries.every((entry) => Boolean(heldEntries[entry]));
  const usable = offered.filter(backendReachable);

  // Nothing is preselected. Which store a corpus lands in is not a detail to
  // inherit from list order — it decides where the data physically lives, and
  // the two backends do not behave identically (see the administration
  // guide's ANN warning). The user says which.
  const backendField = selectInput(
    ['', ...offered.map((backend) => backend.key)],
    '',
    {
      '': str('ragBuilderBackendPlaceholder', 'Select a vector database…'),
      ...Object.fromEntries(offered.map((backend) => [backend.key, backend.label])),
    }
  );
  for (const backend of offered) {
    if (backendReachable(backend)) continue;
    const option = Array.from(backendField.options).find((o) => o.value === backend.key);
    if (!option) continue;
    option.disabled = true;
    const missing = backend.entries.filter((entry) => !heldEntries[entry]);
    option.textContent = `${backend.label} — ${str(
      'ragBuilderBackendNoCredential',
      'no credential'
    )}`;
    option.title = str(
      'ragBuilderBackendNoCredentialNote',
      'No {entries} in the {domain} credential domain - ask your administrator for access.'
    )
      .replace('{entries}', missing.join(' / '))
      .replace('{domain}', credentialDomain);
  }
  labeled(storeRow, idOf('backend'), str('ragBuilderBackendLabel', 'Vector database:'), backendField, 'col-md-3',
    str('ragBuilderBackendInfo', 'Where the chunks and their vectors are stored. Both backends carry the same features, but they are not interchangeable once built — moving a collection means re-ingesting it. A database you hold no credentials for is shown disabled rather than hidden, so you can see it exists and ask for access.'), true);
  if (!usable.length) {
    const warning = document.createElement('div');
    warning.className = 'alert alert-warning py-2 px-3 mt-2 mb-0';
    warning.textContent = str(
      'ragBuilderNoBackendCredential',
      'You hold no vector-store credentials in the {domain} credential domain, so a setup saved here cannot ingest. Ask your administrator to add the entries for the database you need.'
    ).replace('{domain}', credentialDomain);
    storeRow.appendChild(warning);
  }
  // WHERE a store lives is deployment configuration, not something a person
  // building a corpus knows or should have to retype per setup. It is carried
  // in the same credential domain that already holds the store's user and
  // password (<BACKEND>_HOST / _PORT / _DB, falling back to the unprefixed
  // RAGSTORE_* names — the precedence rag_core.env already uses), so the
  // Builder resolves it instead of asking. Shown read-only so the setup is
  // still legible: a form that silently targets an unnamed database is worse
  // than one that asks.
  const DEFAULT_PORTS: Record<string, number> = { pgvector: 5432, singlestore: 3306 };
  const storeSetting = (backend: string, setting: string): string => {
    const prefix = backend.toUpperCase();
    return String(heldEntries[`${prefix}_${setting}`] ?? heldEntries[`RAGSTORE_${setting}`] ?? '').trim();
  };
  const resolvedStore = (): { host: string; port: number; database: string } => {
    const backend = backendField.value;
    return {
      host: storeSetting(backend, 'HOST'),
      port: Number(storeSetting(backend, 'PORT')) || DEFAULT_PORTS[backend] || 5432,
      database: storeSetting(backend, 'DB'),
    };
  };
  // The resolved connection is deliberately NOT shown. Hostnames and ports
  // are infrastructure detail a corpus author neither needs nor should be
  // handed - the credential domain supplies them server-side. Only the
  // absence of one is worth saying, because that is a blocker the user has
  // to take to an administrator.
  const storeLocation = document.createElement('div');
  storeLocation.className = 'col-12';
  const storeLocationText = document.createElement('p');
  storeLocationText.className = 'form-text text-danger mb-0';
  storeLocation.appendChild(storeLocationText);
  storeRow.appendChild(storeLocation);
  const showStoreLocation = (): void => {
    const { host, database } = backendField.value
      ? resolvedStore()
      : { host: 'x', database: 'x' };
    storeLocationText.textContent =
      host && database
        ? ''
        : str(
            'ragBuilderStoreUnresolved',
            'This vector database is not configured in the {domain} credential domain, so a setup saved here cannot ingest. Ask your administrator to add its connection entries.'
          ).replace('{domain}', credentialDomain);
  };
  backendField.addEventListener('change', showStoreLocation);
  showStoreLocation();
  // TLS is deliberately NOT offered here - see RagBuilderConfig.storeSslmode
  const collectionField = textInput('', 'rag_hr_policies_v1');
  labeled(storeRow, idOf('collection'), str('ragBuilderCollectionLabel', 'Collection (lowercase identifier):'), collectionField, 'col-md-5',
    str('ragBuilderCollectionInfo', 'The table this corpus lives in inside the vector database. Two setups pointing at the same collection write into each other, so give each corpus its own — and a version suffix (…_v1) makes it possible to rebuild alongside the live one and cut over. Lowercase letters, digits and underscores, starting with a letter.'), true);
  const prefixField = textInput('', 'RAG_HR');
  // Bounded in the field itself: discovering a 20-character limit only when
  // the save is rejected wastes the whole form-filling effort.
  prefixField.maxLength = PREFIX_MAX;
  prefixField.pattern = '[A-Za-z_][A-Za-z0-9_]*';
  prefixField.style.textTransform = 'uppercase';
  prefixField.addEventListener('blur', () => {
    prefixField.value = prefixField.value.trim().toUpperCase();
  });
  labeled(tablesRow, idOf('tables-prefix'), str('ragBuilderTablesPrefixLabel', 'Pipeline table prefix (max 20 chars):'), prefixField, 'col-md-4',
    str('ragBuilderTablesPrefixInfo', 'Prefix for the CAS working tables this pipeline creates — the ledger, the element and chunk tables, the run history. Kept to 20 characters so every generated name stays inside the 32-character CAS limit. Two setups sharing a prefix overwrite each other\'s ledger.'), true);
  // Caslib picker over the CAS Management listing, the same interactive
  // selection the Prompt Builder's dataset picker uses. Only the caslib is
  // chosen here: the CAS SERVER is admin-set in the Options, so there is one
  // fewer question to answer and no way to name a server the ingestion will
  // not use. Degrades to free text if the listing cannot be read, rather than
  // to an empty dropdown nobody can get past.
  let caslibs: string[] = [];
  try {
    caslibs = (await getCaslibs(config.casServer || 'cas-shared-default'))
      .filter((name) => !isPersonalCaslib(name))
      .sort((left, right) => left.localeCompare(right));
  } catch (error) {
    console.debug('RAG Builder: caslib listing failed', error);
  }
  const caslibField = selectInput(['', ...caslibs], '', {
    '': str('ragBuilderCaslibPlaceholder', 'Select a caslib…'),
  });
  labeled(tablesRow, idOf('tables-caslib'), str('ragBuilderTablesCaslibLabel', 'Tables caslib:'), caslibField, 'col-md-4',
    str('ragBuilderTablesCaslibInfo', 'The caslib holding this pipeline\'s working tables. It has to be one other people and scheduled jobs can reach: a personal caslib is not offered, because a corpus whose ledger only its author can see cannot be rerun by anyone else or by a schedule.'), true);
  const domainNote = document.createElement('p');
  domainNote.className = 'text-muted small mb-0 mt-2';
  domainNote.textContent = `${str('ragBuilderDomainNote', 'Credential domain:')} ${config.credentialDomain}`;
  storeBody.appendChild(storeRow);
  storeBody.appendChild(domainNote);
  editor.appendChild(storeCard);

  tablesBody.appendChild(tablesRow);
  editor.appendChild(tablesCard);

  // ---- where the generated artifacts go -------------------------------------
  const [artifactsCard, artifactsBody] = card(
    str('ragBuilderArtifactsHeading', '6. Generated artifacts'),
    str(
      'ragBuilderArtifactsHint',
      'Where the RAG Builder writes the executables it generates from this setup. They are regenerated from the setup on every Generate, so this folder is safe to treat as output rather than source.'
    )
  );
  const artifactsRow = document.createElement('div');
  artifactsRow.className = 'row g-3';
  const artifactsFolderField = textInput('', `${config.contentRoot}/generated`);
  labeled(artifactsRow, idOf('artifacts-folder'), str('ragBuilderArtifactsFolderLabel', 'SAS Content folder:'),
    artifactsFolderField, 'col-md-8',
    str('ragBuilderArtifactsFolderInfo', 'The SAS Content folder receiving the generated ingestion job. Missing folders below the first level are created for you. Put it where the people who will schedule and run this pipeline already look - the default sits under the accelerator content root, which is convenient for the author and invisible to everyone else.'));
  const artifactsNote = document.createElement('p');
  artifactsNote.className = 'small text-body-secondary mb-0 mt-2';
  artifactsBody.appendChild(artifactsRow);
  artifactsBody.appendChild(artifactsNote);
  editor.appendChild(artifactsCard);

  /** The destination the user asked for, or the deployment default. */
  const artifactsFolder = (): string =>
    artifactsFolderField.value.trim().replace(/\/+$/, '') || `${config.contentRoot}/generated`;

  // ---- actions --------------------------------------------------------------
  const actions = document.createElement('div');
  actions.className = 'd-flex gap-2 flex-wrap mb-3';
  const actionButton = (label: string, style = 'btn-outline-secondary'): HTMLButtonElement => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `btn ${style}`;
    button.textContent = label;
    actions.appendChild(button);
    return button;
  };
  const saveButton = actionButton(str('ragBuilderSaveButton', 'Save setup'));
  // ONE button for "make this setup real", because the four it replaces had
  // to be pressed in an order the UI never stated: a flow generated against a
  // setup whose score code was never registered is a half-built thing, and
  // nothing said so. Save stays separate for the still-editing case.
  const manifestButton = actionButton(str('ragBuilderManifestButton', 'Manifest setup'), 'btn-primary');
  manifestButton.title = str(
    'ragBuilderManifestTitle',
    'Save the setup, generate the ingestion job and the Studio flow, register the retrieval model, and record a new model version.'
  );
  const launchButton = actionButton(str('ragBuilderLaunchButton', 'Launch ingestion'));
  launchButton.disabled = true;
  launchButton.title = str('ragBuilderLaunchNeedsJob', 'Manifest the setup first.');
  const ledgerButton = actionButton(str('ragBuilderLedgerButton', 'Browse ledger'));
  const testButton = actionButton(str('ragBuilderTestButton', 'Test retrieval'));
  testButton.disabled = true;
  testButton.title = str('ragBuilderTestNeedsManifest', 'Manifest the setup first.');
  editor.appendChild(actions);

  // Save feedback belongs beside the button that caused it. An alert at the
  // top of a long editor is off-screen by the time anyone reaches Save, so
  // the message arrives where the user is not looking.
  const saveResult = document.createElement('div');
  saveResult.className = 'mb-3';
  editor.appendChild(saveResult);
  const clearSaveResult = (): void => saveResult.replaceChildren();
  const showSaveErrors = (problems: string[]): void => {
    const alert = document.createElement('div');
    alert.className = 'alert alert-danger py-2 px-3 mb-0';
    alert.setAttribute('role', 'alert');
    const heading = document.createElement('div');
    heading.className = 'fw-semibold mb-1';
    heading.textContent = problems.length === 1
      ? str('ragBuilderSaveBlocked1', 'This setup cannot be saved yet:')
      : str('ragBuilderSaveBlocked', 'This setup cannot be saved yet - {count} things need attention:')
          .replace('{count}', String(problems.length));
    alert.appendChild(heading);
    const list = document.createElement('ul');
    list.className = 'mb-0 ps-3';
    for (const problem of problems) {
      const item = document.createElement('li');
      item.textContent = problem;
      list.appendChild(item);
    }
    alert.appendChild(list);
    saveResult.replaceChildren(alert);
    saveResult.scrollIntoView({ block: 'nearest' });
  };
  const showSaveSuccess = (message: string): void => {
    const alert = document.createElement('div');
    alert.className = 'alert alert-success py-2 px-3 mb-0';
    alert.setAttribute('role', 'status');
    alert.append(`${message} `);
    // The saved artifact is a Model Manager model, and its versions, history
    // and permissions live there - so say where it went, as the Prompt
    // Builder does after saving a prompt.
    if (selectedSetupID) {
      const link = document.createElement('a');
      link.href = `${viyaHost()}/SASModelManager/models/${selectedSetupID}/files`;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = str('ragBuilderOpenInMM', 'Open in SAS Model Manager');
      alert.appendChild(link);
    }
    saveResult.replaceChildren(alert);
    saveResult.scrollIntoView({ block: 'nearest' });
  };

  // ---- ingestion run panel --------------------------------------------------
  const [runCard, runBody] = card(str('ragBuilderRunHeading', 'Ingestion run'));
  runCard.style.display = 'none';
  const runState = document.createElement('p');
  runState.className = 'fw-bold mb-1 d-flex align-items-center gap-2';
  const runBadge = document.createElement('span');
  runBadge.className = 'badge text-bg-secondary';
  const runStateText = document.createElement('span');
  const runClock = document.createElement('span');
  runClock.className = 'text-body-secondary fw-normal small ms-auto';
  runState.append(runBadge, runStateText, runClock);
  // Why the milestone list is empty, when it is. An empty box reads as "the
  // job is doing nothing"; the truthful reading is usually "the log has not
  // been flushed yet", and those call for opposite reactions from the user.
  const runNote = document.createElement('p');
  runNote.className = 'small text-body-secondary mb-2';
  const runMilestones = document.createElement('ul');
  runMilestones.className = 'small mb-0';
  runBody.appendChild(runState);
  runBody.appendChild(runNote);
  runBody.appendChild(runMilestones);
  editor.appendChild(runCard);

  // ---- ledger panel ---------------------------------------------------------
  const [ledgerCard, ledgerBody] = card(str('ragBuilderLedgerHeading', 'Ingestion ledger'));
  ledgerCard.style.display = 'none';
  const ledgerContent = document.createElement('div');
  ledgerContent.className = 'table-responsive';
  ledgerBody.appendChild(ledgerContent);
  editor.appendChild(ledgerCard);

  // ---- retrieval test panel -------------------------------------------------
  // Hidden until asked for: it is a question box, and a question box sitting
  // permanently under a long editor invites answering it before there is a
  // corpus to answer from.
  const [testCard, testBody] = card(
    str('ragBuilderTestHeading', 'Test retrieval'),
    str(
      'ragBuilderTestHint',
      'Ask the live collection a question and see the chunks it returns. This is how you tell whether the chunk size and embedding model suit the corpus - the chunks below are exactly what a retrieval would hand an LLM.'
    )
  );
  testCard.style.display = 'none';
  const testRow = document.createElement('div');
  testRow.className = 'row g-3 align-items-end';
  const testQuestionField = textInput(
    '',
    str('ragBuilderTestQuestionPlaceholder', 'What does the corpus say about…?')
  );
  labeled(testRow, idOf('test-question'), str('ragBuilderTestQuestionLabel', 'Question:'),
    testQuestionField, 'col-md-7',
    str('ragBuilderTestQuestionInfo', 'Asked the way a user would ask it. The question is embedded with the same model the corpus was built with and matched against the collection - so a question phrased unlike the documents is itself a finding.'), true);
  const testTopKField = numberInput(5, 1);
  testTopKField.max = '25';
  labeled(testRow, idOf('test-topk'), str('ragBuilderTestTopKLabel', 'Chunks to return:'),
    testTopKField, 'col-md-3',
    str('ragBuilderTestTopKInfo', 'How many chunks come back, highest score first. Ask for more than the retrieval model will use when you are judging a corpus: the chunks just below the cut-off say more about the chunking than the ones above it.'));
  const testRunColumn = document.createElement('div');
  testRunColumn.className = 'col-md-2 d-grid';
  const testRunButton = document.createElement('button');
  testRunButton.type = 'button';
  testRunButton.className = 'btn btn-primary';
  testRunButton.textContent = str('ragBuilderTestRunButton', 'Run test');
  testRunColumn.appendChild(testRunButton);
  testRow.appendChild(testRunColumn);
  const testStatus = document.createElement('p');
  testStatus.className = 'small text-body-secondary mb-2 mt-3';
  const testResults = document.createElement('div');
  testResults.className = 'table-responsive';
  testBody.appendChild(testRow);
  testBody.appendChild(testStatus);
  testBody.appendChild(testResults);
  editor.appendChild(testCard);

  // ---- data plumbing --------------------------------------------------------

  const applySetup = (setup: RagSetup): void => {
    ragDoc.setValues(setup.documentation);
    const storedPath = String(setup.source.path || '');
    const scheme = storedPath.toLowerCase().startsWith('sascontent:')
      ? 'sascontent'
      : 'sasserver';
    sourceKindField.value = scheme;
    // a setup saved before the location type existed carries a bare path,
    // which the runtime has always read as a compute-server path
    sourcePathField.value = storedPath.replace(/^sas(server|content):/i, '');
    sourcePlaceholder();
    extractorField.value = setup.extraction.extractor;
    includeCodeField.checked = setup.source.includeCode === true;
    chunkerField.value = setup.chunking.chunker;
    tokenLimitField.value = String(setup.chunking.inputTokenLimit);
    overlapField.value = String(setup.chunking.overlapTokens);
    // A saved value the listing does not carry is kept and marked, never
    // dropped: silently retargeting a corpus at whatever the dropdown happens
    // to show first is how a setup starts writing somewhere else.
    keepUnlisted(embedModelField, setup.embedding.model, str('ragBuilderUnlistedModel', 'not registered'));
    embedDimsField.value = String(setup.embedding.dims);
    // Setting a field's value in script fires no change event, so everything
    // derived from the model and the chunker has to be re-run by hand here -
    // otherwise a loaded setup inherits the PREVIOUS setup's token ceiling,
    // overlap bound and overlap visibility. Nothing is rewritten: the saved
    // numbers stand, and validateSetup reports them if they no longer fit.
    embedDimsField.readOnly = embeddingDimensions(setup.embedding.model) > 0;
    capTokenWindow(false);
    followChunker(false);
    boundOverlap();
    backendField.value = setup.store.backend;
    // a setup saved before policies existed carries none
    currentPolicies = setup.policies ?? policiesFrom(config);
    showStoreLocation();

    collectionField.value = setup.store.collection;
    prefixField.value = (setup.tables.prefix || '').toUpperCase();
    artifactsFolderField.value = setup.artifactsFolder || `${config.contentRoot}/generated`;
    keepUnlisted(caslibField, setup.tables.caslib, str('ragBuilderUnlistedCaslib', 'not found'));
    managedTags = [setup.embedding.model, setup.store.backend].filter(Boolean);
    currentJobUri = setup.job?.definitionUri ?? '';
    updateLaunchState();
  };

  const updateLaunchState = (): void => {
    launchButton.disabled = !currentJobUri;
    launchButton.title = currentJobUri
      ? ''
      : str('ragBuilderLaunchNeedsJob', 'Generate the ingestion job first.');
    // Test retrieval reads the COLLECTION, so what it truly needs is an
    // ingested corpus - which nothing in the browser can prove exists. A
    // manifested setup is the honest proxy: it is the point from which the
    // values the probe runs with are the ones on the server.
    testButton.disabled = !currentJobUri;
    testButton.title = currentJobUri
      ? str(
          'ragBuilderTestTitle',
          'Ask this collection a question and see the chunks that come back. It queries the collection through rag_core, not the registered score code, so a broken retrieve_context.py would still pass here. Nothing is left behind: the job definition it runs is unfiled and deleted afterwards.'
        )
      : str('ragBuilderTestNeedsManifest', 'Manifest the setup first.');
  };

  const stopPolling = (): void => {
    if (pollTimer !== null) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  };

  /** "4m 12s" - the shape a person reads a run duration in. */
  const formatElapsed = (ms: number): string => {
    const total = Math.max(0, Math.round(ms / 1000));
    const minutes = Math.floor(total / 60);
    return minutes > 0 ? `${minutes}m ${String(total % 60).padStart(2, '0')}s` : `${total}s`;
  };

  const paintClock = (): void => {
    if (!runStartedAt) return;
    runClock.textContent = str('ragBuilderRunElapsed', 'running for {time}').replace(
      '{time}',
      formatElapsed(Date.now() - runStartedAt)
    );
  };

  const startRunClock = (): void => {
    stopRunClock();
    runStartedAt = Date.now();
    runActive = true;
    paintClock();
    // One second, because this is the only moving thing on the page while a
    // ten-minute job runs - it is what says "still alive" without the user
    // having to trust that the poll is working.
    runTicker = window.setInterval(paintClock, 1000);
  };

  const stopRunClock = (): void => {
    if (runTicker !== null) {
      window.clearInterval(runTicker);
      runTicker = null;
    }
    runActive = false;
  };

  const setRunBadge = (state: string, failed = false): void => {
    const done = isTerminalJobState(state as never);
    runBadge.className = `badge text-bg-${!done ? 'primary' : failed || state !== 'completed' ? 'danger' : 'success'}`;
    runBadge.textContent = state;
  };

  /** The documentation block as the form currently has it. */
  const collectDocumentation = (): RagSetup['documentation'] => ({ ...ragDoc.values() });

  /**
   * Save the documentation ALONE.
   *
   * The model-card attributes are written, documentation.md is regenerated,
   * and the documentation block inside the stored rag-setup.json is merged -
   * that last part matters: rag-setup.json is what loadSetup reads back, so
   * writing only the attributes would make the edit reappear as lost the next
   * time the setup was opened. Everything else in the stored file is left
   * exactly as the last full save left it, so this never persists a
   * half-finished editor.
   */
  async function saveDocumentation(): Promise<void> {
    if (!selectedSetupID) return;
    const documentation = collectDocumentation();
    clearStatus();
    const previous = docSaveButton.textContent;
    docSaveButton.disabled = true;
    docSaveButton.innerHTML =
      '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';
    try {
      const stored = await readStoredSetup();
      if (stored) {
        await createModelContent(
          selectedSetupID,
          { ...stored, documentation },
          SETUP_FILE,
          'documentation'
        );
      }
      await createModelContent(
        selectedSetupID,
        renderDocumentationMarkdown({ ...(stored ?? defaultSetup(config)), documentation }, selectedSetupName),
        DOCUMENTATION_FILE,
        'documentation',
        'text/markdown'
      );
      await updateModelAttributes(selectedSetupID, {
        ...Object.fromEntries(MODEL_CARD_FIELDS.map((field) => [field, documentation[field]])),
      });
      showStatus('success', str('ragBuilderDocSaved', 'Documentation saved to the Model Manager model.'));
    } catch (error) {
      console.error('Saving the RAG documentation failed.', error);
      showStatus('danger', str('ragBuilderDocSaveFailed', 'Saving the documentation failed - check your Model Manager permissions.'));
    } finally {
      docSaveButton.textContent = previous;
      docSaveButton.disabled = !selectedSetupID;
    }
  }

  /** The setup as it is stored on the model, or null when never saved. */
  async function readStoredSetup(): Promise<RagSetup | null> {
    const contents = await getModelContents(selectedSetupID);
    const setupItem = contents.find((item) => item.name === SETUP_FILE);
    if (!setupItem?.fileUri) return null;
    const response = await getFileContent(setupItem.fileUri);
    if (!response.ok) return null;
    return { ...defaultSetup(config), ...(await response.json()) } as RagSetup;
  }

  const collectSetup = (): RagSetup => ({
    version: 1,
    // authored centrally in the Options; a setup keeps the values it was
    // created with, so re-saving does not silently re-baseline an existing
    // corpus onto a policy that changed after it was built
    policies: currentPolicies ?? policiesFrom(config),
    documentation: collectDocumentation(),
    source: {
      path: `${sourceKindField.value}:${sourcePathField.value.trim()}`,
      includeCode: includeCodeField.checked,
    },
    extraction: { extractor: extractorField.value },
    chunking: {
      chunker: chunkerField.value,
      // Read as typed. Clamping here would let an impossible pair save as a
      // silently different one, and the author would never learn what ran.
      inputTokenLimit: Number(tokenLimitField.value),
      overlapTokens: Number(overlapField.value),
    },
    embedding: {
      model: embedModelField.value.trim(),
      dims: Number(embedDimsField.value),
      deploymentType: config.deploymentType || 'k8s',
      scrEndpoint: '',
    },
    store: {
      backend: backendField.value,
      // resolved from the credential domain, then RECORDED on the setup: a
      // setup that names no store cannot be read back or audited, and the
      // runtime prefers an explicit value over the domain anyway, so a
      // recorded one keeps working if the domain later changes.
      ...resolvedStore(),
      // admin-set, carried through unchanged
      sslmode: config.storeSslmode || 'prefer',
      collection: collectionField.value.trim(),
    },
    tables: {
      // CAS upper-cases table names when it stores them, so a setup that
      // held 'liti' produced LITI_LEDGER on the server and liti_LEDGER in
      // every label, link and job parameter here. Normalising at the one
      // place the value is read keeps the UI showing what CAS will show.
      prefix: prefixField.value.trim().toUpperCase(),
      caslib: caslibField.value.trim(),
    },
    artifactsFolder: artifactsFolder(),
    pipelineVersion: 'v1',
    credentialDomain: config.credentialDomain || 'agentic-ai-keys',
    ...(currentJobUri ? { job: { definitionUri: currentJobUri } } : {}),
  });

  /**
   * Every problem with the setup, not just the first.
   *
   * Fixing one field, saving, and being told about the next is a queue the
   * user walks one round trip at a time. The whole list is cheaper to read
   * and cheaper to act on.
   */
  const validateSetup = (setup: RagSetup): string[] => {
    const errors: string[] = [];
    if (!sourcePathField.value.trim())
      errors.push(str('ragBuilderValidateSource', 'The document folder path is required.'));
    if (!setup.embedding.model)
      errors.push(
        str(
          'ragBuilderValidateEmbedModel',
          'Choose an embedding model. It decides the vector width and cannot be changed later without re-embedding the whole corpus.'
        )
      );
    if (!(setup.embedding.dims > 0))
      errors.push(str('ragBuilderValidateEmbedDims', 'The embedding dimensions must be a positive number.'));
    if (!setup.store.backend) errors.push(str('ragBuilderValidateBackend', 'Choose a vector database.'));
    if (!setup.tables.caslib)
      errors.push(str('ragBuilderValidateCaslib', 'Choose a caslib for the pipeline tables.'));
    // A window below the floor, or an overlap that meets it, never advances
    // through a document - the chunker would emit the same opening forever.
    if (!(setup.chunking.inputTokenLimit >= 16))
      errors.push(str('ragBuilderValidateTokenLimit', 'The embedding token window must be at least 16 tokens.'));
    const ceiling = embeddingTokenLimit(setup.embedding.model);
    if (ceiling > 0 && setup.chunking.inputTokenLimit > ceiling)
      errors.push(
        str(
          'ragBuilderValidateTokenCeiling',
          'The embedding token window exceeds what {model} accepts ({max} tokens). Text beyond a model window is dropped silently, so the excess would never reach the vector.'
        )
          .replace('{model}', setup.embedding.model)
          .replace('{max}', String(ceiling))
      );
    if (CHUNKERS_WITH_OVERLAP.has(setup.chunking.chunker)) {
      if (!(setup.chunking.overlapTokens >= 0))
        errors.push(str('ragBuilderValidateOverlapNegative', 'The chunk overlap cannot be negative.'));
      else if (setup.chunking.overlapTokens >= setup.chunking.inputTokenLimit)
        errors.push(
          str(
            'ragBuilderValidateOverlap',
            'The chunk overlap must be smaller than the embedding token window - at or above it, chunking would never move forward through a document.'
          )
        );
    }
    if (!setup.store.host || !setup.store.database)
      errors.push(
        str(
          'ragBuilderValidateStore',
          'This store has no host or database in the credential domain, so a setup saved here cannot ingest.'
        )
      );
    if (!COLLECTION_PATTERN.test(setup.store.collection))
      errors.push(
        str(
          'ragBuilderValidateCollection',
          'The collection must be a lowercase identifier (letters, digits, underscores; starts with a letter).'
        )
      );
    if (!PREFIX_PATTERN.test(setup.tables.prefix))
      errors.push(
        str(
          'ragBuilderValidatePrefix',
          'The table prefix must be 1-20 characters - letters, digits and underscores only, starting with a letter or an underscore - so every generated table name stays within the 32-character CAS limit.'
        )
      );
    return errors;
  };

  // The full lists; the pickers render a filtered view of these, so narrowing
  // never costs a round trip and never loses an entry the server already sent.
  let allProjects: DropdownOption[] = [];
  let allSetups: DropdownOption[] = [];

  function renderProjectOptions(): void {
    renderFilteredOptions(
      projectSelect,
      allProjects,
      projectFilter.nameInput,
      projectFilter.userSelect,
      str('ragBuilderProjectPlaceholder', 'Select a RAG project…')
    );
  }

  function renderSetupOptions(): void {
    renderFilteredOptions(
      setupSelect,
      allSetups,
      setupFilter.nameInput,
      setupFilter.userSelect,
      str('ragBuilderSetupPlaceholder', 'Select a RAG setup…')
    );
  }

  /** Base URL for links out to SAS Model Manager. */
  const viyaHost = (): string =>
    (getAppState().config?.viyaHost as string | undefined) || window.location.origin;

  /** Dismiss a Bootstrap modal we opened declaratively. */
  const hideModal = (id: string): void => {
    const element = document.getElementById(id);
    if (element) Modal.getInstance(element)?.hide();
  };

  /** Reflect the current selection on the buttons that act on it. */
  function updateSelectionActions(): void {
    deleteProjectButton.disabled = !selectedProjectID;
    deleteSetupButton.disabled = !selectedSetupID;
    if (selectedSetupID) {
      openInMMButton.href = `${viyaHost()}/SASModelManager/models/${selectedSetupID}/files`;
      openInMMButton.classList.remove('disabled');
      openInMMButton.removeAttribute('aria-disabled');
    } else {
      openInMMButton.removeAttribute('href');
      openInMMButton.classList.add('disabled');
      openInMMButton.setAttribute('aria-disabled', 'true');
    }
  }

  async function createProjectFromModal(): Promise<void> {
    const name = (document.getElementById(`${idOf('create-project')}Name`) as HTMLInputElement | null)?.value.trim() ?? '';
    const description = (document.getElementById(`${idOf('create-project')}Description`) as HTMLInputElement | null)?.value.trim() ?? '';
    if (!name) {
      showStatus('danger', str('ragBuilderProjectNameRequired', 'A project name is required.'));
      return;
    }
    try {
      const repository = await getModelRepositoryInformation(config.modelRepositoryID);
      const project = await createModelProject({
        name,
        description: description || str('ragBuilderProjectDescription', 'RAG setups of the SAS Agentic AI Accelerator'),
        function: 'RAG',
        repositoryId: config.modelRepositoryID,
        folderId: (repository as { folderId?: string })?.folderId,
        properties: [{ name: 'Origin', value: 'SAS Agentic AI Accelerator', type: 'string' }],
        tags: ['LLM', 'RAG-Engineering'],
      });
      selectedProjectID = project?.id ?? '';
      selectedSetupID = '';
      await refreshProjects();
      await refreshSetups();
      await loadSetup();
      hideModal(`${idOf('create-project')}Modal`);
      showStatus('success', str('ragBuilderProjectCreated', 'RAG project created.'));
    } catch (error) {
      console.error('Creating the RAG project failed.', error);
      showStatus('danger', str('ragBuilderProjectCreateFailed', 'Creating the RAG project failed - check your Model Manager permissions.'));
    }
  }

  async function createSetupFromModal(): Promise<void> {
    const name = (document.getElementById(`${idOf('create-setup')}Name`) as HTMLInputElement | null)?.value.trim() ?? '';
    const description = (document.getElementById(`${idOf('create-setup')}Description`) as HTMLInputElement | null)?.value.trim() ?? '';
    if (!selectedProjectID) {
      showStatus('danger', str('ragBuilderSetupNeedsProject', 'Select a RAG project before creating a setup.'));
      return;
    }
    if (!name) {
      showStatus('danger', str('ragBuilderSetupNameRequired', 'A setup name is required.'));
      return;
    }
    try {
      const created = (await createModel({
        name,
        description,
        function: 'RAG',
        algorithm: 'RAG',
        tool: 'SAS Agentic AI Accelerator RAG Builder',
        modeler: getAppState().userName ?? '',
        projectId: selectedProjectID,
        scoreCodeType: 'python',
        trainCodeType: 'python',
        tags: ['LLM', 'RAG'],
      })) as unknown as { id?: string; items?: Array<{ id?: string; name?: string }> };
      // createModel answers with either the model or a collection holding it,
      // depending on the SAS Viya release - taking only `.id` silently yields
      // an empty selection on the releases that wrap it.
      selectedSetupID = created?.items?.[0]?.id ?? created?.id ?? '';
      selectedSetupName = name;
      await refreshSetups();
      await loadSetup();
      hideModal(`${idOf('create-setup')}Modal`);
      showStatus('success', str('ragBuilderSetupCreated', 'RAG setup created. Author it below and save.'));
    } catch (error) {
      console.error('Creating the RAG setup failed.', error);
      showStatus('danger', str('ragBuilderSetupCreateFailed', 'Creating the RAG setup failed - check your Model Manager permissions.'));
    }
  }

  async function refreshProjects(): Promise<void> {
    allProjects = await getModelProjects("contains(tags,'RAG-Engineering')");
    projectFilter.setUsers(allProjects);
    renderProjectOptions();
    if (selectedProjectID) projectSelect.value = selectedProjectID;
  }

  async function refreshSetups(): Promise<void> {
    allSetups = selectedProjectID ? await getModelProjectModels(selectedProjectID) : [];
    setupFilter.setUsers(allSetups);
    renderSetupOptions();
    if (selectedSetupID) setupSelect.value = selectedSetupID;
    updateSelectionActions();
  }

  async function loadSetup(): Promise<void> {
    stopPolling();
    runCard.style.display = 'none';
    ledgerCard.style.display = 'none';
    editor.style.display = selectedSetupID ? '' : 'none';
    // set before the early return: deselecting must disable the button too
    docSaveButton.disabled = !selectedSetupID;
    if (!selectedSetupID) return;
    applySetup(defaultSetup(config));
    const contents = await getModelContents(selectedSetupID);
    const setupItem = contents.find((item) => item.name === SETUP_FILE);
    if (setupItem?.fileUri) {
      try {
        const response = await getFileContent(setupItem.fileUri);
        if (response.ok) {
          applySetup({ ...defaultSetup(config), ...(await response.json()) } as RagSetup);
        }
      } catch {
        showStatus('danger', str('ragBuilderLoadSetupError', 'The stored rag-setup.json could not be read - starting from defaults.'));
      }
    } else {
      // No stored setup yet, but documentation may already have been saved on
      // its own. The attributes are then the only copy, so read them back
      // rather than showing empty fields over text that exists.
      try {
        const details = await getModelDetails(selectedSetupID);
        if (details) ragDoc.setValues(details as Record<string, unknown>);
      } catch (error) {
        console.debug('RAG Builder: reading documentation attributes failed', error);
      }
    }
  }

  async function saveSetup(): Promise<boolean> {
    clearStatus();
    clearSaveResult();
    const setup = collectSetup();
    const problems = validateSetup(setup);
    if (problems.length) {
      showSaveErrors(problems);
      return false;
    }
    saveButton.disabled = true;
    try {
      // 1. the three artifacts (onConflict=update keeps one copy each)
      await createModelContent(selectedSetupID, setup, SETUP_FILE, 'documentation');
      await createModelContent(selectedSetupID, renderPipelineYaml(setup), PIPELINE_FILE, 'documentation', 'text/plain');
      await createModelContent(
        selectedSetupID,
        renderDocumentationMarkdown(setup, selectedSetupName),
        DOCUMENTATION_FILE,
        'documentation',
        'text/markdown'
      );
      // 2. registration metadata (owner requirements 2026-07-28): description,
      //    trainTable = the ingestion ledger, and the retrieval in/out contract
      await updateModelAttributes(selectedSetupID, {
        // the model card, under the same attribute names a prompt uses, so
        // one governance query reads both kinds of artifact. The model's own
        // description is authored in the create dialog and left alone here.
        ...Object.fromEntries(
          MODEL_CARD_FIELDS.map((field) => [field, setup.documentation[field]])
        ),
        // No CASUSER special case: the picker does not offer a personal
        // caslib, so the name here is always one others can resolve too.
        trainTable: `${config.casServer}/${setup.tables.caslib}/${setup.tables.prefix}_LEDGER`,
        inputVariables: INPUT_VARIABLES,
        outputVariables: OUTPUT_VARIABLES,
        scoreCodeType: 'python',
        trainCodeType: 'python',
      });
      // 3. tags: LLM/RAG plus one for the embedding model and one for the
      //    vector database (previously managed stack tags are replaced)
      await updateModelTags(selectedSetupID, managedTags, [
        'LLM',
        'RAG',
        setup.embedding.model,
        setup.store.backend,
      ]);
      managedTags = [setup.embedding.model, setup.store.backend];
      showSaveSuccess(
        str('ragBuilderSaveSuccess', 'RAG setup saved to Model Manager (rag-setup.json, pipeline.yaml, documentation.md, tags, ledger reference and variable definitions).')
      );
      return true;
    } catch (error) {
      console.error('Saving the RAG setup failed.', error);
      showStatus('danger', str('ragBuilderSaveError', 'Saving the RAG setup failed - check the browser console and your Model Manager permissions.'));
      return false;
    } finally {
      saveButton.disabled = false;
    }
  }

  /** The launch/parameter values of a setup, shared by the generated job
   *  definition's parameter defaults and the launch-time arguments. */
  const jobArguments = (setup: RagSetup): Record<string, string> => ({
    sourcePath: setup.source.path,
    includeCode: setup.source.includeCode === true ? '1' : '0',
    collection: setup.store.collection,
    backend: setup.store.backend,
    storeHost: setup.store.host,
    storePort: String(setup.store.port),
    storeDb: setup.store.database,
    storeSslmode: setup.store.sslmode,
    credentialDomain: setup.credentialDomain,
    scrEndpoint: config.SCREndpoint || '',
    embedModel: setup.embedding.model,
    deploymentType: setup.embedding.deploymentType,
    inputTokenLimit: String(setup.chunking.inputTokenLimit),
    chunker: setup.chunking.chunker,
    overlapTokens: String(setup.chunking.overlapTokens),
    pipelineVersion: setup.pipelineVersion,
    ledgerCaslib: setup.tables.caslib,
    ledgerTable: `${setup.tables.prefix}_LEDGER`,
    ragCorePath: `${config.contentRoot}/rag_core`,
    // the operational policy has to reach the JOB, not just pipeline.yaml -
    // a setting recorded in the governance artifact and ignored at run time
    // is worse than no setting at all
    deletedPolicy: setup.policies?.deletedPolicy ?? 'retire',
    retainDays: String(setup.policies?.retainDays ?? 0),
    replicas: String(setup.policies?.embedReplicas ?? 1),
    recordHistory: setup.policies?.recordHistory === false ? '0' : '1',
  });

  /**
   * Generate (or refresh) the Job Execution definition for this setup: the
   * deployed Ingest-Documents.sas from the content root becomes the code, the
   * setup's values become the parameter defaults, and the definition lands in
   * the content root's `generated` subfolder. The definition URI is stored in
   * rag-setup.json so a later save/generate updates in place.
   */
  async function generateIngestionJob(): Promise<boolean> {
    // Kept for the direct call: the job is generated FROM the saved setup, so
    // generating without saving would emit a job for a setup that does not
    // exist. Within manifestSetup the save has already run, and saveSetup is
    // idempotent, so the second call costs one round trip and keeps this
    // function correct on its own.
    if (!(await saveSetup())) return false;
    const setup = collectSetup();
    manifestButton.disabled = true;
    try {
      // the golden-path job source, exactly as deployed
      const jobsFolder = await getFolderByPath(`${config.contentRoot}/jobs`);
      const jobFile = jobsFolder
        ? (await getFolderMembers(jobsFolder.id)).find(
            (member) => member.name === 'Ingest-Documents.sas'
          )
        : undefined;
      if (!jobFile?.uri) {
        showStatus('danger', str('ragBuilderJobSourceMissing', 'Ingest-Documents.sas was not found under the content root - run the deploy-rag-content script first.'));
        return false;
      }
      const source = await (await getFileContent(String(jobFile.uri), 'text/plain')).text();

      const parameters = [
        ...Object.entries(jobArguments(setup)).map(([name, value]) => jobParameter(name, value)),
        jobParameter('_contextName', String(config.computeContext || ''), 'Compute context'),
      ];
      const definition: JobDefinition = {
        name: `RAG Ingest - ${selectedSetupName}`,
        type: 'Compute',
        code: source,
        parameters,
        description: `Generated by the RAG Builder from the RAG setup ${selectedSetupName}. Regenerate through the RAG Builder instead of editing.`,
      };

      let definitionUri = '';
      if (currentJobUri) {
        const existing = await getJobDefinition(currentJobUri);
        if (existing) {
          const body = { ...existing.body, ...definition, id: existing.body.id };
          await updateJobDefinition(currentJobUri, body, existing.etag);
          definitionUri = currentJobUri;
        }
      }
      if (!definitionUri) {
        const destination = artifactsFolder();
        const generatedFolder = await ensureFolderPath(destination);
        if (!generatedFolder) {
          showStatus(
            'danger',
            str('ragBuilderJobFolderError', 'The folder {folder} could not be created or reached - check the path and your permissions on it. The first level of the path has to exist already.').replace(
              '{folder}',
              destination
            )
          );
          return false;
        }
        const created = await createJobDefinition(definition, generatedFolder.id);
        definitionUri = `/jobDefinitions/definitions/${created.id}`;
      }

      currentJobUri = definitionUri;
      updateLaunchState();
      // persist the job reference with the setup
      await createModelContent(selectedSetupID, collectSetup(), SETUP_FILE, 'documentation');
      // Naming the destination matters more than it looks: this job is the
      // artifact a schedule points at, and "somewhere under the content root"
      // is not something a person can act on a month later.
      artifactsNote.textContent = str('ragBuilderJobGeneratedAt', 'Ingestion job: {name} in {folder}')
        .replace('{name}', `RAG Ingest - ${selectedSetupName}`)
        .replace('{folder}', artifactsFolder());
      showStatus(
        'success',
        str('ragBuilderJobGenerated', 'Ingestion job generated in {folder} and linked to this setup.').replace(
          '{folder}',
          artifactsFolder()
        )
      );
      return true;
    } catch (error) {
      console.error('Generating the ingestion job failed.', error);
      showStatus('danger', str('ragBuilderJobGenerateError', 'Generating the ingestion job failed - check the browser console and your permissions on the content root.'));
      return false;
    } finally {
      manifestButton.disabled = false;
    }
  }

  /**
   * Make the setup real: save it, generate what runs it, register what serves
   * it, and record a version.
   *
   * Ordered, and it STOPS at the first failure rather than pressing on. Each
   * stage depends on the one before - the job and the flow are generated from
   * the saved setup, the score code is manifested from the same values - so
   * continuing after a failure produces artifacts that disagree with each
   * other, which is worse than producing none.
   */
  async function manifestSetup(): Promise<void> {
    if (!selectedSetupID) {
      showStatus('danger', str('ragBuilderRegisterNeedsSetup', 'Select or create a RAG setup first.'));
      return;
    }
    manifestButton.disabled = true;
    try {
      const stages: Array<[string, () => Promise<boolean>]> = [
        [str('ragBuilderStageSave', 'saving the setup'), saveSetup],
        [str('ragBuilderStageJob', 'generating the ingestion job'), generateIngestionJob],
        [str('ragBuilderStageFlow', 'generating the Studio flow'), generateStudioFlow],
        [str('ragBuilderStageRegister', 'registering the retrieval model'), registerRetrievalModel],
      ];
      for (const [label, run] of stages) {
        manifestButton.textContent = `${str('ragBuilderManifestBusy', 'Manifesting')} — ${label}…`;
        if (!(await run())) {
          // The stage that failed has already said why, in the panel beside
          // this button; repeating it would only push its detail off screen.
          manifestButton.textContent = str('ragBuilderManifestButton', 'Manifest setup');
          return;
        }
      }
      // A new MINOR version, as manifesting a prompt does. Without it every
      // manifest overwrote the same version and the model carried no history
      // of what it used to be - which is most of the point of registering it
      // in Model Manager rather than writing a file somewhere.
      try {
        await createModelVersion(selectedSetupID);
      } catch (error) {
        // The artifacts are written and correct; only the version marker is
        // missing. Say so rather than reporting the manifest as failed.
        console.error('Creating the model version failed.', error);
        showStatus('info', str('ragBuilderVersionFailed', 'Everything was generated and registered, but recording a new model version failed - check your permissions on the setup model.'));
        return;
      }
      showSaveSuccess(
        str('ragBuilderManifested', 'Setup manifested: saved, ingestion job and Studio flow generated in {folder}, retrieval model registered, and a new model version recorded.').replace(
          '{folder}',
          artifactsFolder()
        )
      );
    } finally {
      manifestButton.textContent = str('ragBuilderManifestButton', 'Manifest setup');
      manifestButton.disabled = false;
    }
  }

  /** Launch the generated job and poll its state + milestones until done. */
  async function launchIngestion(): Promise<void> {
    if (!currentJobUri) return;
    clearStatus();
    // Same precondition as the retrieval test: without a compute context the
    // job is refused before it starts, and the run panel would show a failed
    // state with no milestones and no log to explain it.
    if (!config.computeContext) {
      showStatus('danger', str('ragBuilderNeedsComputeContext', 'No ingestion compute context is configured, and SAS Job Execution refuses a job that does not name one. Set it in the Options pane of this report.'));
      return;
    }
    stopPolling();
    const setup = collectSetup();
    const args: Record<string, string> = { ...jobArguments(setup) };
    if (config.computeContext) args._contextName = String(config.computeContext);
    launchButton.disabled = true;
    runCard.style.display = '';
    setRunBadge('launching');
    runStateText.textContent = str('ragBuilderRunLaunching', 'Launching…');
    runNote.textContent = '';
    runClock.textContent = '';
    runMilestones.replaceChildren();
    startRunClock();
    try {
      const job = await launchJob(currentJobUri, `RAG Ingest - ${selectedSetupName}`, args);
      const jobId = String(job.id ?? '');
      const renderMilestones = (messages: string[]): void => {
        runMilestones.replaceChildren();
        for (const message of messages) {
          const item = document.createElement('li');
          item.textContent = message.replace(/^RAGINGEST\s+/, '');
          runMilestones.appendChild(item);
        }
      };
      const poll = async (): Promise<void> => {
        try {
          const current = await getJob(jobId);
          const state = String(current.state ?? 'running');
          setRunBadge(state);
          runStateText.textContent = `${str('ragBuilderRunStateLabel', 'State:')} ${state}`;
          const progress = await getJobProgressMessages(current);
          if (progress.messages.length > 0) {
            renderMilestones(progress.messages);
            // The last milestone is the answer to "what is it doing right
            // now", so it belongs where the eye already is.
            runNote.textContent = str('ragBuilderRunLatest', 'Latest: {message}').replace(
              '{message}',
              progress.messages[progress.messages.length - 1].replace(/^RAGINGEST\s+/, '')
            );
          } else if (!isTerminalJobState(state as never)) {
            runNote.textContent =
              progress.liveStatus === 'no-milestones' || progress.liveStatus === 'no-session-refs'
                ? str('ragBuilderRunLogWaiting', 'The job has started but has not reported a milestone yet. Embedding runs one call per chunk, so a large corpus can take several minutes before the first line appears.')
                : str('ragBuilderRunLogPending', 'Live log streaming is unavailable here ({status}) - the full log appears when the run finishes.').replace(
                    '{status}',
                    String(progress.liveStatus)
                  );
          }
          if (isTerminalJobState(state as never)) {
            stopPolling();
            stopRunClock();
            launchButton.disabled = false;
            const failed = progress.messages.some((message) => message.toLowerCase().includes('failed'));
            const ok = state === 'completed' && !failed;
            setRunBadge(state, failed);
            runClock.textContent = str('ragBuilderRunTook', 'took {time}').replace(
              '{time}',
              formatElapsed(Date.now() - runStartedAt)
            );
            if (ok) {
              // The ledger is written by the run's last step, so the moment
              // it becomes readable is exactly now. Showing it unprompted
              // spares the round trip that started this whole confusion.
              runNote.textContent = str('ragBuilderRunDoneNote', 'Run finished. The ingestion ledger is below.');
              void browseLedger();
            }
            showStatus(
              ok ? 'success' : 'danger',
              ok
                ? str('ragBuilderRunDone', 'Ingestion run completed - see the milestones and browse the ledger.')
                : str('ragBuilderRunFailed', 'The ingestion run did not succeed - see the milestones/log for the reason.')
            );
          }
        } catch (error) {
          console.debug('RAG Builder: ingestion poll failed', error);
        }
      };
      pollTimer = window.setInterval(() => void poll(), 5000);
      void poll();
    } catch (error) {
      console.error('Launching the ingestion job failed.', error);
      stopRunClock();
      runClock.textContent = '';
      setRunBadge('failed', true);
      launchButton.disabled = false;
      showStatus('danger', str('ragBuilderRunLaunchError', 'Launching the ingestion job failed - check the browser console.'));
    }
  }

  /**
   * Generate the SAS Studio flow for this setup.
   *
   * The visual twin of the generated job: same five steps, same values,
   * wired in order. The job is what a schedule runs, so a break here costs
   * visual editing and never ingestion - and the flow is regenerable from
   * the setup at any time, so it is output rather than source.
   */
  async function generateStudioFlow(): Promise<boolean> {
    clearStatus();
    clearSaveResult();
    const setup = collectSetup();
    const problems = validateSetup(setup);
    if (problems.length > 0) {
      showSaveErrors(problems);
      return false;
    }
    manifestButton.disabled = true;
    try {
      // Resolved fresh: a redeploy of the custom steps mints new ids, and a
      // flow holding a stale one fails at code generation, far from the cause.
      const stepIds = await resolveStepIds(`${config.contentRoot}/steps`);
      const missing = INGESTION_STEPS.filter((name) => !stepIds[name]);
      if (missing.length > 0) {
        showSaveErrors([
          str('ragBuilderFlowStepsMissing', 'These custom steps are not registered under the content root: {steps}. Run the deploy-rag-content script first.').replace(
            '{steps}',
            missing.join(', ')
          ),
        ]);
        return false;
      }
      const specs = [];
      for (const name of INGESTION_STEPS) specs.push(await readStepSpec(name, stepIds[name]));
      const destination = artifactsFolder();
      if (!(await ensureFolderPath(destination))) {
        showSaveErrors([
          str('ragBuilderJobFolderError', 'The folder {folder} could not be created or reached - check the path and your permissions on it. The first level of the path has to exist already.').replace(
            '{folder}',
            destination
          ),
        ]);
        return false;
      }
      const flowName = `RAG Ingest - ${selectedSetupName}`;
      const flow = buildFlow(
        specs,
        stepIds,
        ingestionChain(setup, `${config.contentRoot}/rag_core`),
        flowName,
        String(config.userName || 'RAG Builder'),
        new Date().toISOString()
      );
      await registerFlow(destination, flow);
      artifactsNote.textContent = str('ragBuilderFlowGeneratedAt', 'Studio flow: {name} in {folder}')
        .replace('{name}', flowName)
        .replace('{folder}', destination);
      showSaveSuccess(
        str('ragBuilderFlowGenerated', 'Studio flow generated in {folder}. Open it in SAS Studio to see or edit the pipeline; regenerate here after changing the setup.').replace(
          '{folder}',
          destination
        )
      );
      return true;
    } catch (error) {
      console.error('Generating the Studio flow failed.', error);
      showSaveErrors([
        str('ragBuilderFlowFailed', 'Generating the Studio flow failed: {reason}').replace(
          '{reason}',
          error instanceof Error ? error.message : String(error)
        ),
      ]);
      return false;
    } finally {
      manifestButton.disabled = false;
    }
  }

  /**
   * Write the manifested retrieval score code onto the RAG Setup model.
   *
   * Until this runs the saved model carries its configuration but nothing
   * executable, so it cannot be scored, published or tested - which is why a
   * setup that saved cleanly still could not retrieve anything. The template
   * is the deployed retrieve_context.py, so what is registered is the same
   * code the Studio step would register, with this setup's values in it.
   */
  async function registerRetrievalModel(): Promise<boolean> {
    if (!selectedSetupID) {
      showStatus('danger', str('ragBuilderRegisterNeedsSetup', 'Select or create a RAG setup first.'));
      return false;
    }
    clearStatus();
    clearSaveResult();
    const setup = collectSetup();
    const problems = validateSetup(setup);
    if (problems.length > 0) {
      showSaveErrors(problems);
      return false;
    }
    manifestButton.disabled = true;
    try {
      const modelsFolder = await getFolderByPath(`${config.contentRoot}/models`);
      const templateFile = modelsFolder
        ? (await getFolderMembers(modelsFolder.id)).find(
            (member) => member.name === 'retrieve_context.py'
          )
        : undefined;
      if (!templateFile?.uri) {
        showStatus('danger', str('ragBuilderTemplateMissing', 'retrieve_context.py was not found under the content root - run the deploy-rag-content script first.'));
        return false;
      }
      const template = await (
        await getFileContent(String(templateFile.uri), 'text/plain')
      ).text();
      const endpoint = setup.embedding.scrEndpoint || `${viyaHost()}/llm`;
      const code = renderRetrievalModel(template, manifestSettings(setup, endpoint));
      await createModelContent(selectedSetupID, code, 'retrieve_context.py', 'score', 'text/x-python');
      showSaveSuccess(
        str('ragBuilderRegistered', 'Retrieval model registered: retrieve_context.py written as the score code of this setup, with its collection, backend and embedding model baked in.')
      );
      return true;
    } catch (error) {
      console.error('Registering the retrieval model failed.', error);
      // renderRetrievalModel throws with the reason (a missing required
      // value, a template that is not ours), and that reason is the useful
      // half of the message - a generic failure would send the reader to the
      // console for something the UI already knows.
      showSaveErrors([
        str('ragBuilderRegisterFailed', 'Registering the retrieval model failed: {reason}').replace(
          '{reason}',
          error instanceof Error ? error.message : String(error)
        ),
      ]);
      return false;
    } finally {
      manifestButton.disabled = false;
    }
  }

  /** What the ledger means while a run is still in flight. */
  function runningLedgerNote(): string {
    return str(
      'ragBuilderLedgerRunning',
      'An ingestion has been running for {time}. The ledger is written by the final step of the run, so it shows the PREVIOUS state until this run finishes - it will refresh here automatically.'
    ).replace('{time}', formatElapsed(Date.now() - runStartedAt));
  }

  /** Show the promoted ledger table of this setup (page of rows). */
  async function browseLedger(): Promise<void> {
    clearStatus();
    const setup = collectSetup();
    ledgerCard.style.display = '';
    ledgerContent.textContent = '…';
    try {
      const table = `${setup.tables.prefix}_LEDGER`;
      const data = await getCasTableRows(setup.tables.caslib, table, config.casServer);
      const preferred = ['doc_id', 'status', 'chunk_count', 'error_text', 'run_id', 'updated_at', 'source_uri'];
      const order = preferred
        .map((name) => data.columns.findIndex((column) => column.toLowerCase() === name))
        .filter((index) => index >= 0);
      const tableEl = document.createElement('table');
      tableEl.className = 'table table-sm table-striped align-middle';
      const head = document.createElement('thead');
      const headRow = document.createElement('tr');
      for (const index of order) {
        const cell = document.createElement('th');
        cell.textContent = data.columns[index];
        headRow.appendChild(cell);
      }
      head.appendChild(headRow);
      const body = document.createElement('tbody');
      for (const row of data.rows) {
        const rowEl = document.createElement('tr');
        for (const index of order) {
          const cell = document.createElement('td');
          cell.textContent = String(row[index] ?? '');
          rowEl.appendChild(cell);
        }
        body.appendChild(rowEl);
      }
      tableEl.appendChild(head);
      tableEl.appendChild(body);
      ledgerContent.replaceChildren(tableEl);
      if (data.rows.length === 0) {
        ledgerContent.textContent = runActive
          ? runningLedgerNote()
          : str('ragBuilderLedgerEmpty', 'The ledger has no rows yet - run an ingestion first.');
      } else {
        // A ledger read DURING a run shows the previous run's rows, and
        // nothing distinguishes them from this run's. Say which they are.
        const caption = document.createElement('p');
        caption.className = 'small text-body-secondary mb-2';
        caption.textContent = runActive
          ? runningLedgerNote()
          : str('ragBuilderLedgerCount', '{count} documents in {table}.')
              .replace('{count}', String(data.rows.length))
              .replace('{table}', `${setup.tables.caslib}.${table}`);
        ledgerContent.prepend(caption);
      }
    } catch (error) {
      console.debug('RAG Builder: ledger read failed', error);
      // The commonest cause is not a missing table but an unfinished run:
      // the ledger is written by the run's LAST step, so it does not exist
      // until then. Saying "no table was found" here sent the reader looking
      // for a broken pipeline when the pipeline was simply still working.
      ledgerContent.textContent = runActive
        ? runningLedgerNote()
        : str('ragBuilderLedgerMissing', 'No loaded ledger table was found for this setup - it appears after the first ingestion run (a saved ledger is loaded by the run itself).');
    }
  }

  // ---- retrieval test -------------------------------------------------------

  /** The probe's parameters: the store and embedding half of the ingestion
   *  arguments - it reads the collection and writes nothing - plus the ask. */
  const retrievalArguments = (
    setup: RagSetup,
    question: string,
    topK: number
  ): Record<string, string> => ({
    question,
    topK: String(topK),
    collection: setup.store.collection,
    backend: setup.store.backend,
    storeHost: setup.store.host,
    storePort: String(setup.store.port),
    storeDb: setup.store.database,
    storeSslmode: setup.store.sslmode,
    credentialDomain: setup.credentialDomain,
    scrEndpoint: config.SCREndpoint || '',
    embedModel: setup.embedding.model,
    deploymentType: setup.embedding.deploymentType,
    ragCorePath: `${config.contentRoot}/rag_core`,
  });

  const pause = (ms: number): Promise<void> =>
    new Promise((resolve) => window.setTimeout(resolve, ms));

  /** Show the returned chunks - or say, precisely, why there are none. */
  const renderRetrievalHits = (parsed: RetrievalTestLog, elapsed: number): void => {
    testResults.replaceChildren();
    // A rank-0 row is not a hit: it is the probe reporting that the question
    // matched nothing, or that the search itself failed. Showing it as an
    // empty row of a results table would read as a bad chunk.
    const hits = parsed.hits.filter((hit) => hit.rank > 0);
    const notes = parsed.hits.filter((hit) => hit.rank === 0 && hit.error);
    if (hits.length === 0) {
      const empty = document.createElement('p');
      empty.className = 'mb-0';
      empty.textContent = notes.length > 0
        ? notes[0].error
        : str('ragBuilderTestNoHits', 'The probe returned no chunks. If the collection has just been ingested, check the ledger first - an empty collection and an unmatched question look the same from here.');
      testResults.appendChild(empty);
    } else {
      const table = document.createElement('table');
      table.className = 'table table-sm table-striped align-middle';
      const head = document.createElement('thead');
      const headRow = document.createElement('tr');
      for (const column of [
        str('ragBuilderTestColRank', '#'),
        str('ragBuilderTestColScore', 'Score'),
        str('ragBuilderTestColSource', 'Source'),
        str('ragBuilderTestColHeading', 'Heading'),
        str('ragBuilderTestColPage', 'Page'),
        str('ragBuilderTestColContent', 'Chunk'),
      ]) {
        const cell = document.createElement('th');
        cell.textContent = column;
        headRow.appendChild(cell);
      }
      head.appendChild(headRow);
      const body = document.createElement('tbody');
      for (const hit of hits) {
        const row = document.createElement('tr');
        // The chunk text is the point of this table, so it gets the room:
        // everything else is one short cell, and the source shows its file
        // name with the full path on hover.
        const cells = [
          String(hit.rank),
          hit.score.toFixed(4),
          hit.source.replace(/\\/g, '/').split('/').pop() ?? hit.source,
          hit.heading,
          hit.page,
          hit.content,
        ];
        cells.forEach((value, index) => {
          const cell = document.createElement('td');
          cell.textContent = value;
          if (index === 2) cell.title = hit.source;
          if (index === 5) cell.className = 'small';
          row.appendChild(cell);
        });
        body.appendChild(row);
      }
      table.appendChild(head);
      table.appendChild(body);
      testResults.appendChild(table);
    }
    const summary: string[] = [
      str('ragBuilderTestSummary', '{count} chunk(s) in {time}.')
        .replace('{count}', String(hits.length))
        .replace('{time}', formatElapsed(elapsed)),
    ];
    // The cost of one probe is a fraction of a cent, and saying so is the
    // point: it is what makes iterating on a corpus something a person does
    // without asking permission first.
    if (parsed.cost) summary.push(parsed.cost);
    testStatus.textContent = summary.join(' ');
  };

  /**
   * Ask the collection a question through a job that does not outlive the
   * asking.
   *
   * The whole sequence - create an unfiled definition, submit it, read the
   * hits out of the log, delete the definition - exists to satisfy one
   * requirement: testing a corpus must leave NOTHING behind (owner, 2026-08-01).
   * That is also why the hits travel in the log instead of a CAS table, which
   * would itself be an artifact of a test that is supposed to have none.
   */
  async function runRetrievalTest(): Promise<void> {
    const asked = macroSafeQuestion(testQuestionField.value);
    if (!asked.value) {
      testStatus.textContent = str('ragBuilderTestNeedsQuestion', 'Type a question first.');
      testQuestionField.focus();
      return;
    }
    const setup = collectSetup();
    const problems = validateSetup(setup);
    if (problems.length > 0) {
      showSaveErrors(problems);
      return;
    }
    // Job Execution routes on _contextName and rejects a job that carries
    // none - "Job routing failure", with no log and nothing else to go on
    // (verified live). Saying which setting is blank beats that by a mile.
    if (!config.computeContext) {
      testStatus.textContent = str('ragBuilderNeedsComputeContext', 'No ingestion compute context is configured, and SAS Job Execution refuses a job that does not name one. Set it in the Options pane of this report.');
      return;
    }
    clearStatus();
    testRunButton.disabled = true;
    testResults.replaceChildren();
    testStatus.textContent = asked.changed
      ? str('ragBuilderTestQuestionAdjusted', 'Asking without the ; & % characters - they cannot survive the trip to the job as a parameter.')
      : str('ragBuilderTestStarting', 'Starting…');
    const startedAt = Date.now();
    let definitionUri = '';
    try {
      const jobsFolder = await getFolderByPath(`${config.contentRoot}/jobs`);
      const jobFile = jobsFolder
        ? (await getFolderMembers(jobsFolder.id)).find(
            (member) => member.name === 'Test-Retrieval.sas'
          )
        : undefined;
      if (!jobFile?.uri) {
        testStatus.textContent = str('ragBuilderTestSourceMissing', 'Test-Retrieval.sas was not found under the content root - run the deploy-rag-content script first.');
        return;
      }
      const source = await (await getFileContent(String(jobFile.uri), 'text/plain')).text();
      const topK = Math.min(25, Math.max(1, Math.round(Number(testTopKField.value) || 5)));
      const args = retrievalArguments(setup, asked.value, topK);
      const jobName = `RAG Test retrieval - ${selectedSetupName}`;
      const created = await createTransientJobDefinition({
        name: jobName,
        type: 'Compute',
        code: source,
        parameters: [
          ...Object.entries(args).map(([name, value]) => jobParameter(name, value)),
          jobParameter('_contextName', String(config.computeContext || ''), 'Compute context'),
        ],
        description: 'Transient: created by the RAG Builder for one retrieval test and deleted when it ends. Nothing references it.',
      });
      definitionUri = `/jobDefinitions/definitions/${created.id}`;
      const launchArgs: Record<string, string> = { ...args };
      if (config.computeContext) launchArgs._contextName = String(config.computeContext);
      const job = await launchJob(definitionUri, jobName, launchArgs);
      const jobId = String(job.id ?? '');

      const deadline = startedAt + 5 * 60 * 1000;
      let parsed = parseRetrievalLog([]);
      for (;;) {
        const current = await getJob(jobId);
        const state = String(current.state ?? 'running');
        parsed = parseRetrievalLog((await getJobProgressMessages(current)).messages);
        if (isTerminalJobState(state as never)) break;
        if (Date.now() > deadline) {
          // The definition still goes, in the finally. The run itself is
          // read-only, so leaving it to finish unobserved costs nothing.
          testStatus.textContent = str('ragBuilderTestTimeout', 'The probe is still running after five minutes, which is far longer than a retrieval takes - stopping the wait. Check the compute context and the vector store.');
          return;
        }
        testStatus.textContent = `${str('ragBuilderTestRunning', 'Asking the collection…')} (${state}, ${formatElapsed(Date.now() - startedAt)})`;
        await pause(3000);
      }
      if (parsed.failure) {
        testResults.replaceChildren();
        testStatus.textContent = str('ragBuilderTestProbeFailed', 'The probe failed: {reason}').replace(
          '{reason}',
          parsed.failure
        );
        return;
      }
      renderRetrievalHits(parsed, Date.now() - startedAt);
    } catch (error) {
      console.error('The retrieval test failed.', error);
      testStatus.textContent = str('ragBuilderTestFailed', 'The retrieval test could not be run - check the browser console and your permissions on the compute context.');
    } finally {
      // Unconditional: a failed, timed-out or abandoned test has to clean up
      // exactly as a successful one does, or "leaves nothing behind" holds
      // only on the happy path. The finished job's own record and log survive
      // this, so nothing already read is lost by deleting now.
      if (definitionUri) {
        try {
          const status = await deleteJobDefinition(definitionUri);
          if (status !== 204 && status !== 404) {
            console.warn(`RAG Builder: the transient test job definition was not deleted (HTTP ${status}).`);
          }
        } catch (error) {
          console.warn('RAG Builder: deleting the transient test job definition failed.', error);
        }
      }
      testRunButton.disabled = false;
    }
  }

  // ---- events ---------------------------------------------------------------
  projectSelect.addEventListener('change', () => {
    selectedProjectID = projectSelect.value;
    updateSelectionActions();
    selectedSetupID = '';
    selectedSetupName = '';
    void refreshSetups();
    void loadSetup();
  });

  setupSelect.addEventListener('change', () => {
    selectedSetupID = setupSelect.value;
    selectedSetupName = setupSelect.selectedOptions[0]?.textContent ?? '';
    updateSelectionActions();
    void loadSetup();
  });

  deleteSetupButton.addEventListener('click', () => {
    void (async () => {
      if (!selectedSetupID) return;
      const confirmed = await showConfirmModal({
        title: `${str('ragBuilderDeleteSetupButton', 'Delete setup')}: ${selectedSetupName}`,
        body: [
          str(
            'ragBuilderDeleteSetupNote',
            'This deletes the RAG setup and its generated artifacts from SAS Model Manager. The vector-store collection and its CAS tables are NOT touched - the data stays where it is, and the record of what built it is what disappears.'
          ),
        ],
        confirmText: str('ragBuilderDeleteConfirm', 'Delete'),
        cancelText: str('ragBuilderDeleteCancel', 'Cancel'),
      });
      if (!confirmed) return;
      clearStatus();
      deleteSetupButton.disabled = true;
      try {
        const status = await deleteModel(selectedSetupID);
        if (status !== 204) throw new Error(`HTTP ${status}`);
        selectedSetupID = '';
        selectedSetupName = '';
        await refreshSetups();
        await loadSetup();
        showStatus('success', str('ragBuilderSetupDeleted', 'RAG setup deleted.'));
      } catch (error) {
        console.error('Deleting the RAG setup failed.', error);
        showStatus('danger', str('ragBuilderDeleteFailed', 'Deleting failed - check your Model Manager permissions.'));
      } finally {
        updateSelectionActions();
      }
    })();
  });

  deleteProjectButton.addEventListener('click', () => {
    void (async () => {
      if (!selectedProjectID) return;
      // Every setup goes first, explicitly: whether a project DELETE cascades
      // to its models varies by SAS Viya release, and one at a time is
      // deterministic. The count is named so nobody deletes twelve corpora
      // believing they are deleting an empty project.
      const setups = allSetups;
      const confirmed = await showConfirmModal({
        title: `${str('ragBuilderDeleteProjectButton', 'Delete project')}: ${projectSelect.selectedOptions[0]?.textContent ?? ''}`,
        body: [
          setups.length
            ? str(
                'ragBuilderDeleteProjectNote',
                'This deletes the project and the {count} setup(s) it holds from SAS Model Manager. Vector-store collections and CAS tables are NOT touched.'
              ).replace('{count}', String(setups.length))
            : str(
                'ragBuilderDeleteProjectEmptyNote',
                'This project holds no setups. Deleting it removes the project from SAS Model Manager.'
              ),
        ],
        confirmText: str('ragBuilderDeleteConfirm', 'Delete'),
        cancelText: str('ragBuilderDeleteCancel', 'Cancel'),
      });
      if (!confirmed) return;
      clearStatus();
      deleteProjectButton.disabled = true;
      try {
        for (const setup of setups) {
          const status = await deleteModel(setup.value);
          if (status !== 204) throw new Error(`setup ${setup.innerHTML}: HTTP ${status}`);
        }
        const status = await deleteModelProject(selectedProjectID);
        if (status !== 204) throw new Error(`project: HTTP ${status}`);
        selectedProjectID = '';
        selectedSetupID = '';
        selectedSetupName = '';
        await refreshProjects();
        await refreshSetups();
        await loadSetup();
        showStatus('success', str('ragBuilderProjectDeleted', 'RAG project deleted.'));
      } catch (error) {
        console.error('Deleting the RAG project failed.', error);
        showStatus('danger', str('ragBuilderDeleteFailed', 'Deleting failed - check your Model Manager permissions.'));
      } finally {
        updateSelectionActions();
      }
    })();
  });

  docSaveButton.addEventListener('click', () => void saveDocumentation());
  saveButton.addEventListener('click', () => void saveSetup());
  manifestButton.addEventListener('click', () => void manifestSetup());
  launchButton.addEventListener('click', () => void launchIngestion());
  ledgerButton.addEventListener('click', () => void browseLedger());
  // The toolbar button opens the panel and puts the cursor in the question;
  // running it is the panel's own button, so the question is always visible
  // beside the chunks it produced.
  testButton.addEventListener('click', () => {
    testCard.style.display = '';
    testCard.scrollIntoView({ block: 'nearest' });
    testQuestionField.focus();
  });
  testRunButton.addEventListener('click', () => void runRetrievalTest());
  testQuestionField.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      void runRetrievalTest();
    }
  });

  // ---- initial load ---------------------------------------------------------
  await refreshProjects();
  await refreshSetups();

  return container;
}
