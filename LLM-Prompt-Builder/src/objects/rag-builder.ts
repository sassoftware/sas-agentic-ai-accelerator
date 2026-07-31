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
  createModelContent,
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
import { RAG_BACKENDS, backendOptionKey, type RagBackend } from './rag-backends';
import { embeddingDimensions, embeddingTokenLimit } from './embedding-models';
import { ensureChildFolder, getFolderByPath, getFolderMembers } from '../api/folders-api';
import {
  createJobDefinition,
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
    (backend) => String(config[backendOptionKey(backend)] ?? '1') !== '0'
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
      description: '', modelPurpose: '', intendedUse: '', expectedBenefit: '',
      outOfScopeUseCases: '', limitations: '',
    },
    source: { path: '' },
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
    pipelineVersion: 'v1',
    credentialDomain: config.credentialDomain || 'agentic-ai-keys',
    policies: policiesFrom(config),
  };
}

/** The deployment's operational policy, recorded onto the setup so the
 * generated artifacts say what THIS corpus does rather than deferring to a
 * central setting that may since have changed. */
function policiesFrom(config: RagBuilderConfig): RagSetup['policies'] {
  const flag = (value: string, fallback: boolean): boolean =>
    value === '' || value === undefined ? fallback : value !== '0';
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
    '## Description',
    '',
    setup.documentation.description || '_Not documented yet._',
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
  const documentationRow = document.createElement('div');
  documentationRow.className = 'row g-3';
  const descriptionField = textArea(2, str('ragBuilderDocDescriptionPlaceholder', 'What does this RAG setup contain?'));
  labeled(documentationRow, idOf('doc-description'), str('ragBuilderDocDescriptionLabel', 'Description:'), descriptionField, 'col-12');
  documentationBody.appendChild(documentationRow);
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
  const sourcePathField = textInput('', '/data/documents');
  labeled(documentsRow, idOf('source-path'), str('ragBuilderSourcePathLabel', 'Document folder (compute-context path):'), sourcePathField, 'col-md-8',
    str('ragBuilderSourcePathInfo', 'A path on the SAS Compute server, not on your workstation. The ingestion walks it recursively and treats every readable file as a candidate document. If the compute context cannot see this path, the run finds nothing rather than failing loudly.'), true);
  const extractorField = selectInput(EXTRACTORS, '', { '': str('ragBuilderExtractorAuto', 'Automatic (by file format)') });
  labeled(documentsRow, idOf('extractor'), str('ragBuilderExtractorLabel', 'Extractor:'), extractorField, 'col-md-4',
    str('ragBuilderExtractorInfo', 'How text is pulled out of each file. Automatic picks per file format and is almost always right; forcing one applies it to EVERY file, so a PDF read as plain text yields nonsense rather than an error. Some formats need optional Python packages — see the administration guide.'));
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
  const capTokenWindow = (): void => {
    const ceiling = embeddingTokenLimit(embedModelField.value);
    if (ceiling > 0) {
      tokenLimitField.max = String(ceiling);
      if (Number(tokenLimitField.value) > ceiling) tokenLimitField.value = String(ceiling);
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
  const followChunker = (): void => {
    const uses = CHUNKERS_WITH_OVERLAP.has(chunkerField.value);
    overlapColumn.style.display = uses ? '' : 'none';
    if (!uses) overlapField.value = '0';
  };
  chunkerField.addEventListener('change', followChunker);
  followChunker();
  // The embedding card is built first, so the model's ceiling is wired here,
  // where the window field it caps exists. Changing the model re-applies it.
  embedModelField.addEventListener('change', capTokenWindow);
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
  const storeLocation = document.createElement('div');
  storeLocation.className = 'col-md-9 d-flex align-items-end';
  const storeLocationText = document.createElement('p');
  storeLocationText.className = 'form-text mb-2';
  storeLocation.appendChild(storeLocationText);
  storeRow.appendChild(storeLocation);
  const showStoreLocation = (): void => {
    if (!backendField.value) {
      storeLocationText.className = 'form-text mb-2';
      storeLocationText.textContent = str(
        'ragBuilderStoreChooseFirst',
        'Choose a vector database to see where this setup would ingest.'
      );
      return;
    }
    const { host, port, database } = resolvedStore();
    storeLocationText.className = host && database ? 'form-text mb-2' : 'form-text text-danger mb-2';
    storeLocationText.textContent =
      host && database
        ? str('ragBuilderStoreResolved', 'Ingests into {host}:{port}/{database}, from the {domain} credential domain.')
            .replace('{host}', host)
            .replace('{port}', String(port))
            .replace('{database}', database)
        : str(
            'ragBuilderStoreUnresolved',
            'No host or database for this store in the {domain} credential domain. Ask your administrator to add {prefix}_HOST and {prefix}_DB.'
          )
            .replace(/\{domain\}/g, credentialDomain)
            .replace(/\{prefix\}/g, backendField.value.toUpperCase());
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
  const saveButton = actionButton(str('ragBuilderSaveButton', 'Save setup'), 'btn-primary');
  const generateJobButton = actionButton(str('ragBuilderGenerateJobButton', 'Generate ingestion job'));
  const launchButton = actionButton(str('ragBuilderLaunchButton', 'Launch ingestion'));
  launchButton.disabled = true;
  launchButton.title = str('ragBuilderLaunchNeedsJob', 'Generate the ingestion job first.');
  const ledgerButton = actionButton(str('ragBuilderLedgerButton', 'Browse ledger'));
  const generateFlowButton = actionButton(str('ragBuilderGenerateFlowButton', 'Generate Studio Flow'));
  generateFlowButton.disabled = true;
  generateFlowButton.title = str('ragBuilderComingSoon', 'Arrives with Register Setup (P2)');
  const testButton = actionButton(str('ragBuilderTestButton', 'Test retrieval'));
  testButton.disabled = true;
  testButton.title = str('ragBuilderComingSoon', 'Arrives with Register Setup (P2)');
  editor.appendChild(actions);

  // ---- ingestion run panel --------------------------------------------------
  const [runCard, runBody] = card(str('ragBuilderRunHeading', 'Ingestion run'));
  runCard.style.display = 'none';
  const runState = document.createElement('p');
  runState.className = 'fw-bold mb-2';
  const runMilestones = document.createElement('ul');
  runMilestones.className = 'small mb-0';
  runBody.appendChild(runState);
  runBody.appendChild(runMilestones);
  editor.appendChild(runCard);

  // ---- ledger panel ---------------------------------------------------------
  const [ledgerCard, ledgerBody] = card(str('ragBuilderLedgerHeading', 'Ingestion ledger'));
  ledgerCard.style.display = 'none';
  const ledgerContent = document.createElement('div');
  ledgerContent.className = 'table-responsive';
  ledgerBody.appendChild(ledgerContent);
  editor.appendChild(ledgerCard);

  // ---- data plumbing --------------------------------------------------------

  const applySetup = (setup: RagSetup): void => {
    descriptionField.value = setup.documentation.description;
    ragDoc.setValues(setup.documentation);
    sourcePathField.value = setup.source.path;
    extractorField.value = setup.extraction.extractor;
    chunkerField.value = setup.chunking.chunker;
    tokenLimitField.value = String(setup.chunking.inputTokenLimit);
    overlapField.value = String(setup.chunking.overlapTokens);
    // A saved value the listing does not carry is kept and marked, never
    // dropped: silently retargeting a corpus at whatever the dropdown happens
    // to show first is how a setup starts writing somewhere else.
    keepUnlisted(embedModelField, setup.embedding.model, str('ragBuilderUnlistedModel', 'not registered'));
    embedDimsField.value = String(setup.embedding.dims);
    backendField.value = setup.store.backend;
    // a setup saved before policies existed carries none
    currentPolicies = setup.policies ?? policiesFrom(config);
    showStoreLocation();

    collectionField.value = setup.store.collection;
    prefixField.value = setup.tables.prefix;
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
  };

  const stopPolling = (): void => {
    if (pollTimer !== null) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  };

  /** The documentation block as the form currently has it. */
  const collectDocumentation = (): RagSetup['documentation'] => ({
    description: descriptionField.value.trim(),
    ...ragDoc.values(),
  });

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
        description: documentation.description.slice(0, 1000),
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
    source: { path: sourcePathField.value.trim() },
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
      prefix: prefixField.value.trim(),
      caslib: caslibField.value.trim(),
    },
    pipelineVersion: 'v1',
    credentialDomain: config.credentialDomain || 'agentic-ai-keys',
    ...(currentJobUri ? { job: { definitionUri: currentJobUri } } : {}),
  });

  const validateSetup = (setup: RagSetup): string | null => {
    if (!setup.source.path) return str('ragBuilderValidateSource', 'The document folder path is required.');
    if (!setup.embedding.model)
      return str(
        'ragBuilderValidateEmbedModel',
        'Choose an embedding model. It decides the vector width and cannot be changed later without re-embedding the whole corpus.'
      );
    if (!(setup.embedding.dims > 0))
      return str('ragBuilderValidateEmbedDims', 'The embedding dimensions must be a positive number.');
    if (!setup.store.backend)
      return str('ragBuilderValidateBackend', 'Choose a vector database.');
    if (!setup.tables.caslib)
      return str('ragBuilderValidateCaslib', 'Choose a caslib for the pipeline tables.');
    // A window below the floor, or an overlap that meets it, never advances
    // through a document - the chunker would emit the same opening forever.
    if (!(setup.chunking.inputTokenLimit >= 16))
      return str('ragBuilderValidateTokenLimit', 'The embedding token window must be at least 16 tokens.');
    const ceiling = embeddingTokenLimit(setup.embedding.model);
    if (ceiling > 0 && setup.chunking.inputTokenLimit > ceiling)
      return str(
        'ragBuilderValidateTokenCeiling',
        'The embedding token window exceeds what {model} accepts ({max} tokens). Text beyond a model window is dropped silently, so the excess would never reach the vector.'
      )
        .replace('{model}', setup.embedding.model)
        .replace('{max}', String(ceiling));
    if (CHUNKERS_WITH_OVERLAP.has(setup.chunking.chunker) && setup.chunking.overlapTokens < 0)
      return str('ragBuilderValidateOverlapNegative', 'The chunk overlap cannot be negative.');
    if (
      CHUNKERS_WITH_OVERLAP.has(setup.chunking.chunker) &&
      setup.chunking.overlapTokens >= setup.chunking.inputTokenLimit
    )
      return str(
        'ragBuilderValidateOverlap',
        'The chunk overlap must be smaller than the embedding token window - at or above it, chunking would never move forward through a document.'
      );
    if (!setup.store.host || !setup.store.database)
      return str(
        'ragBuilderValidateStore',
        'This store has no host or database in the credential domain, so a setup saved here cannot ingest.'
      );
    if (!COLLECTION_PATTERN.test(setup.store.collection))
      return str('ragBuilderValidateCollection', 'The collection must be a lowercase identifier (letters, digits, underscores; starts with a letter).');
    if (!PREFIX_PATTERN.test(setup.tables.prefix))
      return str(
        'ragBuilderValidatePrefix',
        'The table prefix must be 1-20 characters — letters, digits and underscores only, starting with a letter or an underscore — so every generated table name stays within the 32-character CAS limit.'
      );
    return null;
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
        if (details) {
          ragDoc.setValues(details as Record<string, unknown>);
          const description = (details as Record<string, unknown>).description;
          if (typeof description === 'string') descriptionField.value = description;
        }
      } catch (error) {
        console.debug('RAG Builder: reading documentation attributes failed', error);
      }
    }
  }

  async function saveSetup(): Promise<boolean> {
    clearStatus();
    const setup = collectSetup();
    const problem = validateSetup(setup);
    if (problem) {
      showStatus('danger', problem);
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
        description: setup.documentation.description.slice(0, 1000),
        // the model card, under the same attribute names a prompt uses, so
        // one governance query reads both kinds of artifact
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
      showStatus('success', str('ragBuilderSaveSuccess', 'RAG setup saved to Model Manager (rag-setup.json, pipeline.yaml, documentation.md, tags, ledger reference and variable definitions).'));
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
  async function generateIngestionJob(): Promise<void> {
    if (!(await saveSetup())) return;
    const setup = collectSetup();
    generateJobButton.disabled = true;
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
        return;
      }
      const source = await (await getFileContent(String(jobFile.uri), 'text/plain')).text();

      const parameters = [
        ...Object.entries(jobArguments(setup)).map(([name, value]) => jobParameter(name, value)),
        jobParameter('_contextName', config.computeContext || '', 'Compute context'),
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
        const generatedFolder = await ensureChildFolder(config.contentRoot, 'generated');
        if (!generatedFolder) {
          showStatus('danger', str('ragBuilderJobFolderError', 'The generated-artifacts folder under the content root could not be created - check your permissions on it.'));
          return;
        }
        const created = await createJobDefinition(definition, generatedFolder.id);
        definitionUri = `/jobDefinitions/definitions/${created.id}`;
      }

      currentJobUri = definitionUri;
      updateLaunchState();
      // persist the job reference with the setup
      await createModelContent(selectedSetupID, collectSetup(), SETUP_FILE, 'documentation');
      showStatus('success', str('ragBuilderJobGenerated', 'Ingestion job generated in the content root (generated folder) and linked to this setup.'));
    } catch (error) {
      console.error('Generating the ingestion job failed.', error);
      showStatus('danger', str('ragBuilderJobGenerateError', 'Generating the ingestion job failed - check the browser console and your permissions on the content root.'));
    } finally {
      generateJobButton.disabled = false;
    }
  }

  /** Launch the generated job and poll its state + milestones until done. */
  async function launchIngestion(): Promise<void> {
    if (!currentJobUri) return;
    clearStatus();
    stopPolling();
    const setup = collectSetup();
    const args: Record<string, string> = { ...jobArguments(setup) };
    if (config.computeContext) args._contextName = config.computeContext;
    launchButton.disabled = true;
    runCard.style.display = '';
    runState.textContent = str('ragBuilderRunLaunching', 'Launching…');
    runMilestones.replaceChildren();
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
          runState.textContent = `${str('ragBuilderRunStateLabel', 'State:')} ${state}`;
          const progress = await getJobProgressMessages(current);
          if (progress.messages.length > 0) renderMilestones(progress.messages);
          else if (progress.liveStatus !== 'ok') {
            runState.textContent += ` (${str('ragBuilderRunLogPending', 'full log at completion')})`;
          }
          if (isTerminalJobState(state as never)) {
            stopPolling();
            launchButton.disabled = false;
            const failed = progress.messages.some((message) => message.toLowerCase().includes('failed'));
            showStatus(
              state === 'completed' && !failed ? 'success' : 'danger',
              state === 'completed' && !failed
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
      launchButton.disabled = false;
      showStatus('danger', str('ragBuilderRunLaunchError', 'Launching the ingestion job failed - check the browser console.'));
    }
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
        ledgerContent.textContent = str('ragBuilderLedgerEmpty', 'The ledger has no rows yet - run an ingestion first.');
      }
    } catch (error) {
      console.debug('RAG Builder: ledger read failed', error);
      ledgerContent.textContent = str('ragBuilderLedgerMissing', 'No loaded ledger table was found for this setup - it appears after the first ingestion run (a saved ledger is loaded by the run itself).');
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
  generateJobButton.addEventListener('click', () => void generateIngestionJob());
  launchButton.addEventListener('click', () => void launchIngestion());
  ledgerButton.addEventListener('click', () => void browseLedger());

  // ---- initial load ---------------------------------------------------------
  await refreshProjects();
  await refreshSetups();

  return container;
}
