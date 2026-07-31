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
import { getCasTableRows } from '../api/cas-api';
import { getAppState } from '../state/app-state';
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
/**
 * Vector-store backends the runtime supports, with the credential-domain
 * entries each one needs. Mirrors rag_core.adapters.REGISTRY — a backend the
 * runtime cannot load must never be offered here.
 */
const BACKENDS: ReadonlyArray<{ key: string; label: string; entries: string[] }> = [
  { key: 'pgvector', label: 'pgvector (PostgreSQL)',
    entries: ['PGVECTOR_RAG_USER', 'PGVECTOR_RAG_PW'] },
  { key: 'singlestore', label: 'SingleStore',
    entries: ['SINGLESTORE_RAG_USER', 'SINGLESTORE_RAG_PW'] },
];

/**
 * The backends this deployment offers, in the order they were configured.
 * Blank (the default) offers all of them. An unknown name is ignored rather
 * than shown, so a typo cannot conjure a backend the runtime has no adapter
 * for. If the setting names nothing recognisable we fall back to everything,
 * because presenting an empty list would leave the user unable to proceed
 * with no explanation.
 */
function offeredBackends(enabled: string): typeof BACKENDS {
  const wanted = String(enabled ?? '')
    .split(',')
    .map((name) => name.trim().toLowerCase())
    .filter(Boolean);
  if (!wanted.length) return BACKENDS;
  const offered = BACKENDS.filter((backend) => wanted.includes(backend.key));
  return offered.length ? offered : BACKENDS;
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
    documentation: { description: '', intendedUse: '', limitations: '' },
    source: { path: '' },
    extraction: { extractor: '' },
    chunking: { chunker: 'recursive', inputTokenLimit: 256, overlapTokens: 30 },
    embedding: {
      model: 'all_minilm_l6_v2',
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
    tables: { prefix: '', caslib: 'casuser' },
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

function renderDocumentationMarkdown(setup: RagSetup, setupName: string): string {
  return [
    `# ${setupName}`,
    '',
    '## Description',
    '',
    setup.documentation.description || '_Not documented yet._',
    '',
    '## Intended use',
    '',
    setup.documentation.intendedUse || '_Not documented yet._',
    '',
    '## Limitations',
    '',
    setup.documentation.limitations || '_Not documented yet._',
    '',
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
  const showStatus = (variant: 'success' | 'danger' | 'info', message: string): void => {
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
  const [selectionCard, selectionBody] = card(
    str('ragBuilderSelectionHeading', 'Project and setup'),
    str(
      'ragBuilderSelectionHint',
      'A RAG project groups related setups in SAS Model Manager; a setup is one document corpus wired to one vector-store collection.'
    )
  );
  const selectionRow = document.createElement('div');
  selectionRow.className = 'row g-3 align-items-end';

  const projectSelect = selectInput([], '');
  labeled(selectionRow, idOf('project'), str('ragBuilderProjectLabel', 'RAG project:'), projectSelect);
  const newProjectName = textInput('', str('ragBuilderNewProjectPlaceholder', 'New project name'));
  const newProjectButton = document.createElement('button');
  newProjectButton.type = 'button';
  newProjectButton.className = 'btn btn-secondary';
  newProjectButton.textContent = str('ragBuilderNewProjectButton', 'Create project');
  {
    const column = document.createElement('div');
    column.className = 'col-md-4';
    const group = document.createElement('div');
    group.className = 'input-group';
    group.appendChild(newProjectName);
    group.appendChild(newProjectButton);
    column.appendChild(group);
    selectionRow.appendChild(column);
  }

  const setupSelect = selectInput([], '');
  labeled(selectionRow, idOf('setup'), str('ragBuilderSetupLabel', 'RAG setup:'), setupSelect);
  const newSetupName = textInput('', str('ragBuilderNewSetupPlaceholder', 'New setup name'));
  const newSetupButton = document.createElement('button');
  newSetupButton.type = 'button';
  newSetupButton.className = 'btn btn-secondary';
  newSetupButton.textContent = str('ragBuilderNewSetupButton', 'Create setup');
  {
    const column = document.createElement('div');
    column.className = 'col-md-4';
    const group = document.createElement('div');
    group.className = 'input-group';
    group.appendChild(newSetupName);
    group.appendChild(newSetupButton);
    column.appendChild(group);
    selectionRow.appendChild(column);
  }
  selectionBody.appendChild(selectionRow);
  container.appendChild(selectionCard);

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
  const intendedUseField = textArea(2, str('ragBuilderDocIntendedUsePlaceholder', 'Which questions/decisions should it serve?'));
  labeled(documentationRow, idOf('doc-intended'), str('ragBuilderDocIntendedUseLabel', 'Intended use:'), intendedUseField, 'col-md-6');
  const limitationsField = textArea(2, str('ragBuilderDocLimitationsPlaceholder', 'Known gaps: coverage, freshness, languages, document quality…'));
  labeled(documentationRow, idOf('doc-limitations'), str('ragBuilderDocLimitationsLabel', 'Limitations:'), limitationsField, 'col-md-6');
  documentationBody.appendChild(documentationRow);
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
  const embedModelField = textInput('all_minilm_l6_v2');
  labeled(pipelineRow, idOf('embed-model'), str('ragBuilderEmbedModelLabel', 'Embedding model:'), embedModelField, 'col-md-3');
  const embedDimsField = numberInput(384, 1);
  labeled(pipelineRow, idOf('embed-dims'), str('ragBuilderEmbedDimsLabel', 'Embedding dimensions:'), embedDimsField, 'col-md-3');
  pipelineBody.appendChild(pipelineRow);
  editor.appendChild(pipelineCard);

  // Vector store + pipeline tables
  const [storeCard, storeBody] = card(
    str('ragBuilderStoreHeading', 'Vector store and pipeline tables'),
    str(
      'ragBuilderStoreHint',
      'Connection settings only — the store user and password are resolved server-side from the credential domain (<BACKEND>_RAG_USER / <BACKEND>_RAG_PW) and never enter this browser.'
    )
  );
  const storeRow = document.createElement('div');
  storeRow.className = 'row g-3';
  // Which stores this deployment offers, and which of those THIS user holds
  // credentials for. Two separate questions: the admin decides what the site
  // runs, the credential domain decides who may use it. A backend the user
  // cannot reach stays visible but disabled, naming the missing entry — a
  // hidden option looks like the feature does not exist.
  const offered = offeredBackends(config.enabledBackends);
  const credentialDomain = String(config.credentialDomain || 'agentic-ai-keys').trim();
  const heldEntries = credentialDomain
    ? await resolveDomainSecrets(credentialDomain)
    : {};
  const backendReachable = (backend: (typeof BACKENDS)[number]): boolean =>
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
  const hostField = textInput('', 'db.example.com');
  labeled(storeRow, idOf('store-host'), str('ragBuilderStoreHostLabel', 'Host:'), hostField, 'col-md-4');
  const portField = numberInput(5432, 1);
  labeled(storeRow, idOf('store-port'), str('ragBuilderStorePortLabel', 'Port:'), portField, 'col-md-2');
  const databaseField = textInput('');
  labeled(storeRow, idOf('store-db'), str('ragBuilderStoreDbLabel', 'Database:'), databaseField, 'col-md-3');
  // the port follows the backend unless the user typed one, matching the
  // Load step's blank-port behaviour (5432 pgvector, 3306 SingleStore)
  const DEFAULT_PORTS: Record<string, number> = { pgvector: 5432, singlestore: 3306 };
  backendField.addEventListener('change', () => {
    const previous = Object.values(DEFAULT_PORTS).map(String);
    if (!portField.value || previous.includes(portField.value)) {
      portField.value = String(DEFAULT_PORTS[backendField.value] ?? 5432);
    }
  });
  // TLS is deliberately NOT offered here - see RagBuilderConfig.storeSslmode
  const collectionField = textInput('', 'rag_hr_policies_v1');
  labeled(storeRow, idOf('collection'), str('ragBuilderCollectionLabel', 'Collection (lowercase identifier):'), collectionField, 'col-md-4');
  const prefixField = textInput('', 'RAG_HR');
  labeled(storeRow, idOf('tables-prefix'), str('ragBuilderTablesPrefixLabel', 'Pipeline table prefix (max 20 chars):'), prefixField, 'col-md-3');
  const caslibField = textInput('casuser');
  labeled(storeRow, idOf('tables-caslib'), str('ragBuilderTablesCaslibLabel', 'Tables caslib:'), caslibField, 'col-md-2');
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
  const fillSelect = (select: HTMLSelectElement, options: DropdownOption[], placeholder: string): void => {
    select.replaceChildren();
    const empty = document.createElement('option');
    empty.value = '';
    empty.textContent = placeholder;
    select.appendChild(empty);
    for (const option of options) {
      const entry = document.createElement('option');
      entry.value = option.value;
      entry.textContent = option.innerHTML;
      select.appendChild(entry);
    }
  };

  const applySetup = (setup: RagSetup): void => {
    descriptionField.value = setup.documentation.description;
    intendedUseField.value = setup.documentation.intendedUse;
    limitationsField.value = setup.documentation.limitations;
    sourcePathField.value = setup.source.path;
    extractorField.value = setup.extraction.extractor;
    chunkerField.value = setup.chunking.chunker;
    tokenLimitField.value = String(setup.chunking.inputTokenLimit);
    overlapField.value = String(setup.chunking.overlapTokens);
    embedModelField.value = setup.embedding.model;
    embedDimsField.value = String(setup.embedding.dims);
    backendField.value = setup.store.backend;
    // a setup saved before policies existed carries none
    currentPolicies = setup.policies ?? policiesFrom(config);
    hostField.value = setup.store.host;
    portField.value = String(setup.store.port);
    databaseField.value = setup.store.database;

    collectionField.value = setup.store.collection;
    prefixField.value = setup.tables.prefix;
    caslibField.value = setup.tables.caslib;
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
      intendedUse: intendedUseField.value.trim(),
      limitations: limitationsField.value.trim(),
    },
    source: { path: sourcePathField.value.trim() },
    extraction: { extractor: extractorField.value },
    chunking: {
      chunker: chunkerField.value,
      inputTokenLimit: Math.max(16, Number(tokenLimitField.value) || 256),
      overlapTokens: Math.max(0, Number(overlapField.value) || 0),
    },
    embedding: {
      model: embedModelField.value.trim() || 'all_minilm_l6_v2',
      dims: Math.max(1, Number(embedDimsField.value) || 384),
      deploymentType: config.deploymentType || 'k8s',
      scrEndpoint: '',
    },
    store: {
      backend: backendField.value,
      host: hostField.value.trim(),
      port: Math.max(1, Number(portField.value) || 5432),
      database: databaseField.value.trim(),
      // admin-set, carried through unchanged
      sslmode: config.storeSslmode || 'prefer',
      collection: collectionField.value.trim(),
    },
    tables: {
      prefix: prefixField.value.trim(),
      caslib: caslibField.value.trim() || 'casuser',
    },
    pipelineVersion: 'v1',
    credentialDomain: config.credentialDomain || 'agentic-ai-keys',
    ...(currentJobUri ? { job: { definitionUri: currentJobUri } } : {}),
  });

  const validateSetup = (setup: RagSetup): string | null => {
    if (!setup.source.path) return str('ragBuilderValidateSource', 'The document folder path is required.');
    if (!setup.store.host || !setup.store.database)
      return str('ragBuilderValidateStore', 'Vector store host and database are required.');
    if (!COLLECTION_PATTERN.test(setup.store.collection))
      return str('ragBuilderValidateCollection', 'The collection must be a lowercase identifier (letters, digits, underscores; starts with a letter).');
    if (!PREFIX_PATTERN.test(setup.tables.prefix))
      return str('ragBuilderValidatePrefix', 'The table prefix must be 1-20 characters (letters, digits, underscores; starts with a letter) so every table name stays within 32 characters.');
    return null;
  };

  async function refreshProjects(): Promise<void> {
    const projects = await getModelProjects("contains(tags,'RAG-Engineering')");
    fillSelect(projectSelect, projects, str('ragBuilderProjectPlaceholder', 'Select a RAG project…'));
    projectSelect.value = selectedProjectID;
  }

  async function refreshSetups(): Promise<void> {
    if (!selectedProjectID) {
      fillSelect(setupSelect, [], str('ragBuilderSetupPlaceholder', 'Select a RAG setup…'));
      return;
    }
    const setups = await getModelProjectModels(selectedProjectID);
    fillSelect(setupSelect, setups, str('ragBuilderSetupPlaceholder', 'Select a RAG setup…'));
    setupSelect.value = selectedSetupID;
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
