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
import { embeddingDimensions } from './embedding-models';
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
import { getAppState } from '../state/app-state';
import { showToast } from '../ui/toast';
import { attachCombobox } from '../ui/combobox';
import { createListFilter, renderFilteredOptions } from '../ui/list-filter';
import { MODEL_CARD_FIELDS, createDocSection } from '../ui/doc-section';
import type { InterfaceText } from '../types';
import type { DropdownOption } from '../types/models';
import type { RagBuilderConfig, RagBuilderText, RagSetup } from '../types/rag';

const SETUP_FILE = 'rag-setup.json';
const PIPELINE_FILE = 'pipeline.yaml';
const DOCUMENTATION_FILE = 'documentation.md';

const PREFIX_PATTERN = /^[A-Za-z][A-Za-z0-9_]{0,19}$/;
const COLLECTION_PATTERN = /^[a-z][a-z0-9_]{0,62}$/;

const EXTRACTORS = ['', 'plaintext', 'markdown', 'csv_json', 'html', 'pdf-text'];
const CHUNKERS = ['recursive', 'paragraph'];
/** Preselected when the deployment registers it; the accelerator's small,
 * open, CPU-friendly default. Only a preference — the offered list is what
 * the embedding project actually holds. */
const DEFAULT_EMBEDDING = 'all_minilm_l6_v2';
const DEFAULT_CASLIB = 'casuser';

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
      model: DEFAULT_EMBEDDING,
      dims: 384,
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
    tables: { prefix: '', caslib: DEFAULT_CASLIB },
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

  const labeled = (
    parent: HTMLElement,
    id: string,
    labelText: string,
    element: HTMLElement,
    columns = 'col-md-4'
  ): void => {
    const column = document.createElement('div');
    column.className = columns;
    const label = document.createElement('label');
    label.className = 'form-label fw-bold mb-1';
    label.htmlFor = id;
    label.textContent = labelText;
    element.id = id;
    column.appendChild(label);
    column.appendChild(element);
    parent.appendChild(column);
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
  const newProjectName = textInput('', str('ragBuilderNewProjectPlaceholder', 'New project name'));
  const newProjectButton = document.createElement('button');
  newProjectButton.type = 'button';
  newProjectButton.className = 'btn btn-secondary';
  newProjectButton.textContent = str('ragBuilderNewProjectButton', 'Create project');
  const newSetupName = textInput('', str('ragBuilderNewSetupPlaceholder', 'New setup name'));
  const newSetupButton = document.createElement('button');
  newSetupButton.type = 'button';
  newSetupButton.className = 'btn btn-secondary';
  newSetupButton.textContent = str('ragBuilderNewSetupButton', 'Create setup');
  for (const [field, button] of [
    [newProjectName, newProjectButton],
    [newSetupName, newSetupButton],
  ] as const) {
    const group = document.createElement('div');
    group.className = 'input-group w-auto';
    group.appendChild(field);
    group.appendChild(button);
    selectionButtons.appendChild(group);
  }
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
  documentationBody.appendChild(ragDoc.section);
  editor.appendChild(documentationCard);

  // Pipeline
  const [pipelineCard, pipelineBody] = card(
    str('ragBuilderPipelineHeading', 'Ingestion pipeline'),
    str(
      'ragBuilderPipelineHint',
      'What the generated Studio Flow / Job Execution job will run: crawl, extract, chunk and embed through the governed SCR embedding container.'
    )
  );
  const pipelineRow = document.createElement('div');
  pipelineRow.className = 'row g-3';
  const sourcePathField = textInput('', '/data/documents');
  labeled(pipelineRow, idOf('source-path'), str('ragBuilderSourcePathLabel', 'Document folder (compute-context path):'), sourcePathField, 'col-md-6');
  const extractorField = selectInput(EXTRACTORS, '', { '': str('ragBuilderExtractorAuto', 'Automatic (by file format)') });
  labeled(pipelineRow, idOf('extractor'), str('ragBuilderExtractorLabel', 'Extractor:'), extractorField, 'col-md-3');
  const chunkerField = selectInput(CHUNKERS, 'recursive');
  labeled(pipelineRow, idOf('chunker'), str('ragBuilderChunkerLabel', 'Chunker:'), chunkerField, 'col-md-3');
  const tokenLimitField = numberInput(256, 16);
  labeled(pipelineRow, idOf('token-limit'), str('ragBuilderTokenLimitLabel', 'Embedding token window:'), tokenLimitField, 'col-md-3');
  const overlapField = numberInput(30, 0);
  labeled(pipelineRow, idOf('overlap'), str('ragBuilderOverlapLabel', 'Chunk overlap (tokens):'), overlapField, 'col-md-3');
  // The embedding model is LISTED, not typed: only a model registered in the
  // embedding project has a container behind it, and a name with nothing
  // behind it does not fail until the first embed call — after the crawl and
  // the chunking have already run, reported as an HTTP 404 rather than a typo.
  // With no project configured the field degrades to free text rather than to
  // an empty dropdown nobody can get past.
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
  const embedModelField: HTMLSelectElement | HTMLInputElement = registeredEmbeddings.length
    ? selectInput(
        registeredEmbeddings,
        registeredEmbeddings.includes(DEFAULT_EMBEDDING) ? DEFAULT_EMBEDDING : registeredEmbeddings[0]
      )
    : textInput(DEFAULT_EMBEDDING);
  labeled(pipelineRow, idOf('embed-model'), str('ragBuilderEmbedModelLabel', 'Embedding model:'), embedModelField, 'col-md-3');
  const embedDimsField = numberInput(384, 1);
  labeled(pipelineRow, idOf('embed-dims'), str('ragBuilderEmbedDimsLabel', 'Embedding dimensions:'), embedDimsField, 'col-md-3');
  // The vector column is created at this width and cannot be widened
  // afterwards, so the dimension follows the model wherever the model's fact
  // sheet publishes one. It stays editable: a model registered outside the
  // shipped set publishes no width, and guessing one is worse than asking.
  const followEmbeddingModel = (): void => {
    const dims = embeddingDimensions(embedModelField.value);
    if (dims) embedDimsField.value = String(dims);
  };
  embedModelField.addEventListener('change', followEmbeddingModel);
  followEmbeddingModel();
  if (embeddingProject && !registeredEmbeddings.length) {
    const note = document.createElement('div');
    note.className = 'alert alert-warning py-2 px-3 mt-2 mb-0';
    note.textContent = str(
      'ragBuilderNoEmbeddingModels',
      'No embedding models could be listed from the configured project, so the model name has to be typed. Check the "Embedding model project ID" option and that you can read that project.'
    );
    pipelineRow.appendChild(note);
  }
  pipelineBody.appendChild(pipelineRow);
  editor.appendChild(pipelineCard);

  // Vector store + pipeline tables
  const [storeCard, storeBody] = card(
    str('ragBuilderStoreHeading', 'Vector store and pipeline tables'),
    str(
      'ragBuilderStoreHint',
      'Where the store lives and who may reach it both come from the credential domain — the connection is resolved server-side and never enters this browser as a secret.'
    )
  );
  const storeRow = document.createElement('div');
  storeRow.className = 'row g-3';
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

  const backendField = selectInput(
    offered.map((backend) => backend.key),
    (usable[0] ?? offered[0]).key,
    Object.fromEntries(offered.map((backend) => [backend.key, backend.label]))
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
  labeled(storeRow, idOf('backend'), str('ragBuilderBackendLabel', 'Vector database:'), backendField, 'col-md-3');
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
  labeled(storeRow, idOf('collection'), str('ragBuilderCollectionLabel', 'Collection (lowercase identifier):'), collectionField, 'col-md-4');
  const prefixField = textInput('', 'RAG_HR');
  labeled(storeRow, idOf('tables-prefix'), str('ragBuilderTablesPrefixLabel', 'Pipeline table prefix (max 20 chars):'), prefixField, 'col-md-3');
  // Caslib picker over the CAS Management listing, the same interactive
  // selection the Prompt Builder's dataset picker uses. Only the caslib is
  // chosen here: the CAS SERVER is admin-set in the Options, so there is one
  // fewer question to answer and no way to name a server the ingestion will
  // not use. Degrades to free text if the listing cannot be read, rather than
  // to an empty dropdown nobody can get past.
  let caslibs: string[] = [];
  try {
    caslibs = (await getCaslibs(config.casServer || 'cas-shared-default')).sort((left, right) =>
      left.localeCompare(right)
    );
  } catch (error) {
    console.debug('RAG Builder: caslib listing failed', error);
  }
  const caslibField: HTMLSelectElement | HTMLInputElement = caslibs.length
    ? selectInput(caslibs, caslibs.includes(DEFAULT_CASLIB) ? DEFAULT_CASLIB : caslibs[0])
    : textInput(DEFAULT_CASLIB);
  labeled(storeRow, idOf('tables-caslib'), str('ragBuilderTablesCaslibLabel', 'Tables caslib:'), caslibField, 'col-md-3');
  const domainNote = document.createElement('p');
  domainNote.className = 'text-muted small mb-0 mt-2';
  domainNote.textContent = `${str('ragBuilderDomainNote', 'Credential domain:')} ${config.credentialDomain}`;
  storeBody.appendChild(storeRow);
  storeBody.appendChild(domainNote);
  editor.appendChild(storeCard);

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

  const collectSetup = (): RagSetup => ({
    version: 1,
    // authored centrally in the Options; a setup keeps the values it was
    // created with, so re-saving does not silently re-baseline an existing
    // corpus onto a policy that changed after it was built
    policies: currentPolicies ?? policiesFrom(config),
    documentation: {
      description: descriptionField.value.trim(),
      ...ragDoc.values(),
    },
    source: { path: sourcePathField.value.trim() },
    extraction: { extractor: extractorField.value },
    chunking: {
      chunker: chunkerField.value,
      inputTokenLimit: Math.max(16, Number(tokenLimitField.value) || 256),
      overlapTokens: Math.max(0, Number(overlapField.value) || 0),
    },
    embedding: {
      model: embedModelField.value.trim() || DEFAULT_EMBEDDING,
      dims: Math.max(1, Number(embedDimsField.value) || 384),
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
      caslib: caslibField.value.trim() || DEFAULT_CASLIB,
    },
    pipelineVersion: 'v1',
    credentialDomain: config.credentialDomain || 'agentic-ai-keys',
    ...(currentJobUri ? { job: { definitionUri: currentJobUri } } : {}),
  });

  const validateSetup = (setup: RagSetup): string | null => {
    if (!setup.source.path) return str('ragBuilderValidateSource', 'The document folder path is required.');
    if (!setup.store.host || !setup.store.database)
      return str(
        'ragBuilderValidateStore',
        'This store has no host or database in the credential domain, so a setup saved here cannot ingest.'
      );
    if (!COLLECTION_PATTERN.test(setup.store.collection))
      return str('ragBuilderValidateCollection', 'The collection must be a lowercase identifier (letters, digits, underscores; starts with a letter).');
    if (!PREFIX_PATTERN.test(setup.tables.prefix))
      return str('ragBuilderValidatePrefix', 'The table prefix must be 1-20 characters (letters, digits, underscores; starts with a letter) so every table name stays within 32 characters.');
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
  }

  async function loadSetup(): Promise<void> {
    stopPolling();
    runCard.style.display = 'none';
    ledgerCard.style.display = 'none';
    editor.style.display = selectedSetupID ? '' : 'none';
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
        trainTable: `${config.casServer}/${setup.tables.caslib.toUpperCase() === 'CASUSER' ? `CASUSER(${getAppState().userName ?? 'casuser'})` : setup.tables.caslib}/${setup.tables.prefix}_LEDGER`,
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
    selectedSetupID = '';
    selectedSetupName = '';
    void refreshSetups();
    void loadSetup();
  });

  setupSelect.addEventListener('change', () => {
    selectedSetupID = setupSelect.value;
    selectedSetupName = setupSelect.selectedOptions[0]?.textContent ?? '';
    void loadSetup();
  });

  newProjectButton.addEventListener('click', () => {
    void (async () => {
      const name = newProjectName.value.trim();
      if (!name) return;
      clearStatus();
      newProjectButton.disabled = true;
      try {
        const repository = await getModelRepositoryInformation(config.modelRepositoryID);
        const project = await createModelProject({
          name,
          description: str('ragBuilderProjectDescription', 'RAG setups of the SAS Agentic AI Accelerator'),
          function: 'RAG',
          repositoryId: config.modelRepositoryID,
          folderId: (repository as { folderId?: string })?.folderId,
          properties: [{ name: 'Origin', value: 'SAS Agentic AI Accelerator', type: 'string' }],
          tags: ['LLM', 'RAG-Engineering'],
        });
        selectedProjectID = project?.id ?? '';
        newProjectName.value = '';
        await refreshProjects();
        await refreshSetups();
        await loadSetup();
      } catch (error) {
        console.error('Creating the RAG project failed.', error);
        showStatus('danger', str('ragBuilderProjectCreateError', 'Creating the RAG project failed - check your Model Manager permissions.'));
      } finally {
        newProjectButton.disabled = false;
      }
    })();
  });

  newSetupButton.addEventListener('click', () => {
    void (async () => {
      const name = newSetupName.value.trim();
      if (!name || !selectedProjectID) return;
      clearStatus();
      newSetupButton.disabled = true;
      try {
        const created = (await createModel({
          name,
          description: '',
          function: 'RAG',
          algorithm: 'RAG',
          tool: 'SAS Agentic AI Accelerator RAG Builder',
          modeler: getAppState().userName ?? '',
          projectId: selectedProjectID,
          scoreCodeType: 'python',
          trainCodeType: 'python',
          tags: ['LLM', 'RAG'],
        })) as unknown as { id?: string; items?: Array<{ id?: string; name?: string }> };
        selectedSetupID = created?.items?.[0]?.id ?? created?.id ?? '';
        selectedSetupName = name;
        newSetupName.value = '';
        await refreshSetups();
        await loadSetup();
      } catch (error) {
        console.error('Creating the RAG setup failed.', error);
        showStatus('danger', str('ragBuilderSetupCreateError', 'Creating the RAG setup failed - check your Model Manager permissions.'));
      } finally {
        newSetupButton.disabled = false;
      }
    })();
  });

  saveButton.addEventListener('click', () => void saveSetup());
  generateJobButton.addEventListener('click', () => void generateIngestionJob());
  launchButton.addEventListener('click', () => void launchIngestion());
  ledgerButton.addEventListener('click', () => void browseLedger());

  // ---- initial load ---------------------------------------------------------
  await refreshProjects();
  await refreshSetups();

  return container;
}
