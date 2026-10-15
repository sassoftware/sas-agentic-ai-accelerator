/**
 * Minimal mock of the SAS Viya endpoints the LLM Prompt Builder calls, plus a
 * static route serving the built dist/index.html same-origin (the app defaults
 * viyaHost to window.location.origin). Every request is recorded and exposed
 * at GET /__log for assertions; POST /__reset clears the log. An entry holds
 * the method, the URL, the body, the If-Match header, whether the request
 * carried X-Requested-With: XMLHttpRequest (xhr - every request of the app's
 * HTTP client does), its X-CSRF-TOKEN (csrf) and the status it was answered
 * with.
 *
 * GET /__direct lists the requests a BROWSER made to a SAS Viya service
 * without X-Requested-With: XMLHttpRequest - a request that went around the
 * app's HTTP client and so around its handling of HTTP 449 and of the CSRF
 * token. The page itself, the model containers under /scr/ and the plain GET
 * to /<service>/ with which the client establishes a session are not listed.
 * POST /__reset does not clear this list: a suite reads it once, at its end.
 *
 * The RAG kind of the Builder reads the deployed RAG runtime from SAS Content.
 * The mock serves those files from the repository, which it finds from the
 * served bundle or the working directory, or is told through MOCK_REPO_ROOT.
 *
 * The port is MOCK_PORT (default 4173), so several instances can serve
 * several suites at the same time; a suite finds its instance through
 * VERIFY_BASE.
 */
const http = require('http');
const fs = require('fs');
const path = require('path');

const DIST_HTML = process.argv[2];
const PORT = Number(process.env.MOCK_PORT) || 4173;

const log = [];
// See GET /__direct in the header.
const directRequests = [];
let decisionProject = false;
// POST /__failmodel { name, status } makes every later SCR call of that model
// fail: a number is answered as that HTTP status (503 or 404 is what the
// ingress answers for a registered model without a container), "drop" closes
// the connection without an answer (the browser sees a network failure).
// Without a status the model answers again; without a name all of them do.
const failingModels = {};
// POST /__nosession { service, stuck, csrf } takes the browser's session with
// one service (the first path segment, relationships for example) away. Every
// request to it that carries X-Requested-With: XMLHttpRequest is then answered
// with HTTP 449, until a plain GET to /<service>/ was seen: what a SAS Viya
// service answers a browser it has no session with, and the request that
// follows the logon redirect there. stuck: true keeps answering 449 after that
// GET (a session that cannot be established). csrf: true makes the service ask
// for a CSRF token once the session exists, the way a first write is answered
// on SAS Viya: 403 with x-forbidden-reason: CSRF and the token to repeat the
// request with. Without a service every session is back.
const noSession = {};
const MOCK_CSRF_TOKEN = 'mock-csrf-token';
// POST /__usage { model, flows, status } decides what the relationships query
// answers for one model. flows (ids of decision flows, flow-A and flow-B have
// a name) makes these decisions depend on it, each reported as the flow and
// once more as a revision of it; an empty list is a model nothing uses.
// status makes the query fail with that HTTP status. Without both the model
// answers as its fixture does; without a model all of them do.
const usageFor = {};
// Models created through POST /modelRepository/models, kept so the listing of
// their project, their own GET and their contents answer for them afterwards.
const createdModels = [];
// The models deleted through the mock: a GET of one answers 404, as SAS Model
// Manager does - what a delete without an answer reads back. An id that is
// given out again to a new model leaves the set.
const deletedModels = new Set();
// The created models of a project a listing filter lets through. Only the two
// filters the real service was seen to honour on these fields are applied:
// contains(tags,'X') and eq(algorithm,'X').
function createdModelsFor(projectId, filter) {
  const tag = (/contains\(tags,'([^']+)'\)/.exec(filter) || [])[1];
  const algorithm = (/eq\(algorithm,'([^']+)'\)/.exec(filter) || [])[1];
  return createdModels.filter(
    (model) =>
      model.projectId === projectId &&
      (!tag || (model.tags || []).includes(tag)) &&
      (!algorithm || model.algorithm === algorithm)
  );
}

function headerRow(runId, sys, user) {
  return {
    runId, systemPrompt: sys, userPrompt: user, model: '', options: '', response: '',
    run_time: null, prompt_length: null, output_length: null,
    best_prompt: null, fastest_prompt: null, fewest_tokens_prompt: null,
  };
}
function modelRow(runId, response, model = 'demo_llm', options = '{temperature:0.7}') {
  return {
    runId, systemPrompt: '', userPrompt: '', model,
    options, response,
    run_time: 1.2, prompt_length: 10, output_length: 20,
    best_prompt: 0, fastest_prompt: true, fewest_tokens_prompt: true,
  };
}
// runIds intentionally have a gap (1, 2, 5) — runs whose experiments all
// failed persist no rows, so real trackers contain gaps. The last run has two
// model results (one of them from an LLM that is no longer available) and a
// non-default temperature to prove option restoration on load.
const trackerRows = [
  headerRow(1, 'Sys 1', 'User 1'), modelRow(1, 'Response one'),
  headerRow(2, 'Sys 2', 'User 2'), modelRow(2, 'Response two'),
  headerRow(5, 'Sys 3', 'User 3'), modelRow(5, 'Response three', 'demo_llm', '{temperature:0.9,reasoning_effort:medium}'),
  modelRow(5, 'Response other', 'other_llm'),
];
// Run 2 carries a selected best response so prompt selection auto-loads it.
trackerRows[3].best_prompt = 1;
// Run 5 (the two-model run) carries a completed judge verdict + config so the
// reload path (banner reasoning, ranks, restored judge controls) is exercised.
trackerRows[4].judge_model = 'demo_llm';
trackerRows[4].judge_confidence = 'high';
trackerRows[4].judge_reasoning = 'demo_llm answered more precisely than other_llm.';
trackerRows[4].judge_include_self = 1;
trackerRows[4].judge_auto = 1;
trackerRows[5].judge_rank = 1;
trackerRows[5].judge_best = 1;
trackerRows[6].judge_rank = 2;
trackerRows[6].judge_best = 0;

// POST /__manifested { on } swaps the tracker of `Used Prompt` (model-used)
// for the one of a prompt-test that was manifested, so that opening it fills
// every part of the later steps a saved run can fill: two variables in both
// prompts, two LLMs with temperatures that are not their defaults, a council
// of two judges with a chairman, self-judging and auto-judge, and the manifest
// configuration of the best run (the LLM call included, two of the four
// outputs, one output variable). A switch and not a fixture of its own: the
// prompt-tests of proj-1 are counted by other checks. `on: false` serves the
// plain tracker again.
let manifestedTracker = false;
const manifestedTrackerRows = [
  headerRow(1, 'Sys 1', 'User 1'), modelRow(1, 'Response one'),
  {
    ...headerRow(2, 'You answer for {{customer}}.', 'Rate the order of {{amount}} EUR.'),
    variables: [
      { name: 'customer', description: 'Who asks', type: 'string', value: 'Ada' },
      { name: 'amount', description: 'Order value', type: 'decimal', value: '12.5' },
    ],
    manifest: {
      integratedLLMCall: true,
      selectedOutputs: ['response', 'run_time'],
      outputVariables: [{ name: 'sentiment', description: 'Tone of the answer', type: 'string', defaultValue: 'neutral' }],
    },
    judge_mode: 'council',
    judge_panel: 'demo_llm,second_llm',
    judge_ballots: [
      { judgeModel: 'demo_llm', status: 'ok', ranking: ['demo_llm', 'second_llm'], confidence: 'high', reasoning: 'demo_llm is the more precise answer.' },
      { judgeModel: 'second_llm', status: 'ok', ranking: ['demo_llm', 'second_llm'], confidence: 'high', reasoning: 'demo_llm answers the question that was asked.' },
    ],
    // A chairman the page can use: contrarian_judge has no key in the default
    // credential domain, and a model that cannot be used is chosen nowhere.
    judge_chairman_model: 'demo_llm',
    judge_chairman_reasoning: 'No tie to break.',
    judge_confidence: 'high',
    judge_reasoning: 'Both judges put demo_llm first.',
    judge_include_self: 1,
    judge_auto: 1,
  },
  { ...modelRow(2, 'Manifested answer', 'demo_llm', '{temperature:0.9}'), best_prompt: 1, judge_rank: 1, judge_best: 1 },
  { ...modelRow(2, 'Second answer', 'second_llm', '{temperature:0.2,API_KEY:OpenAI}'), judge_rank: 2, judge_best: 0 },
];

// model-used: two distinct dependent decision flows, one of them reported
// twice (flow + revision) to exercise the dedupe. model-free: no dependents.
// model-err: the relationships query itself fails (fail-open path).
// A decision template's saved run: the question map is the header's system
// prompt, the state template its user prompt; the answers are the response.
const DECISION_QUESTIONS = JSON.stringify({
  team: { type: 'choice', instructions: 'Which team handles this?', criteria: { billing: 'payments', technical: 'bugs', sales: 'pricing' } },
  escalate: { type: 'noul', instructions: 'The customer threatens to leave' },
});
const decisionTrackerRows = [
  {
    ...headerRow(1, DECISION_QUESTIONS, 'Subject: {{subject}}\nMessage: {{body}}'),
    mode: 'decision',
    expected: { team: 'billing', escalate: 'no' },
    variables: [
      { name: 'subject', description: '', type: 'string', value: 'Charged twice' },
      { name: 'body', description: '', type: 'string', value: 'Please refund one payment.' },
    ],
  },
  {
    ...modelRow(1, JSON.stringify({
      team: { type: 'choice', choice: 'billing', probabilities: { billing: 0.9, technical: 0.05, sales: 0.05 }, confidence: 0.85 },
      escalate: { type: 'noul', noul: 0.1 },
    }), 'jev_mock', '{API_KEY:OpenAI}'),
    best_prompt: 1,
    output_length: 0,
  },
];

const relationshipsFor = {
  'model-used': {
    items: [
      { type: 'Parent', resourceUri: '/modelRepository/projects/proj-1' },
      { type: 'Associated', resourceUri: '/folders/folders/f-1' },
      { type: 'Dependent', resourceUri: '/decisions/flows/flow-A' },
      { type: 'Dependent', resourceUri: '/decisions/flows/flow-A/revisions/rev-1' },
      { type: 'Dependent', resourceUri: '/decisions/flows/flow-B' },
    ],
  },
  'model-free': {
    items: [
      { type: 'Parent', resourceUri: '/modelRepository/projects/proj-1' },
      { type: 'Associated', resourceUri: '/folders/folders/f-1' },
    ],
  },
};

// --- Prompt optimization (Phase 3) -----------------------------------------
// The Optimize flow resolves the job definition path, launches a Job Execution
// job and polls it. The mock job runs for one poll cycle, then completes; on
// completion the source model gains a Prompt-Optimization-Tracker.json whose
// entry the app renders (metrics, produced model link, load button).
let jobPolls = 0;
let jobLaunched = false;
// POST /__failjob arms a failure: the NEXT launched job fails after one poll,
// logging the job's dependency-preflight error (missing dspy) and appending a
// failed tracker entry — mirroring the shipped job's failure path.
let failNextJob = false;
let failJobPolls = 0;
// Set when the most recent launch was a compare-mode run (Phase 4a): job-1
// then completes into a comparison entry echoing the requested candidates.
let compareArgs = null;
// The Builder can delete optimization runs: it removes the tracker content
// and re-uploads the remaining entries, which the mock mirrors statefully.
let optTrackerDeleted = false;
function multipartJson(rawBody) {
  const m = rawBody.match(/\r\n\r\n([\s\S]*?)\r\n--/);
  try {
    return m ? JSON.parse(m[1]) : null;
  } catch {
    return null;
  }
}
const DSPY_MISSING_ERROR =
  'The Python environment of this compute context lacks the dspy package - install the packages in Prompt-Optimization/requirements.txt into that Python environment, or point the computeContext Option at a prepared context.';
const optimizedPrompt = {
  systemPrompt: 'Optimised system prompt.\n\nFollow the pattern of these examples:\n\nuserPrompt: User 2\nresponse: Response two',
  userPrompt: 'User 2',
  variables: [],
  demos: [{ userPrompt: 'User 2', response: 'Response two' }],
};
// Seeded with one LEGACY entry (earlier releases created a separate
// prompt-test, recorded as producedPromptModelId) so the history view's
// backward compatibility is exercised; runs append new-shape entries.
const optimizationTracker = [
  {
    optimizationId: 1,
    startedAt: '2026-07-20T09:00:00Z',
    finishedAt: '2026-07-20T09:05:00Z',
    status: 'succeeded',
    jobId: 'job-legacy',
    targetModel: 'demo_llm',
    datasetSource: 'tracker',
    sampleCount: 1,
    optimizer: 'bootstrap',
    metric: 'exact',
    metricBefore: 0.4,
    metricAfter: 0.6,
    optimizedPrompt,
    producedPromptModelId: 'model-opt-legacy',
    datasetSnapshot: 'Prompt-Optimization-Dataset-1.json',
    // Exercises the baseline-perfect info note the history renders when the
    // job skipped the optimization phase.
    skippedReason: 'baseline-perfect',
    error: null,
  },
];
const successEntry = {
  optimizationId: 2,
  startedAt: '2026-07-23T09:00:00Z',
  finishedAt: '2026-07-23T09:05:00Z',
  status: 'succeeded',
  jobId: 'job-1',
  targetModel: 'demo_llm',
  datasetSource: 'tracker',
  datasetRef: 'Prompt-Experiment-Tracker.json',
  sampleCount: 1,
  optimizer: 'bootstrap',
  metric: 'overlap',
  judgeModel: null,
  metricBefore: 0.5,
  metricAfter: 0.833,
  baselinePrompt: { systemPrompt: 'Sys 2', userPrompt: 'User 2' },
  trainSize: 7,
  validationSize: 3,
  // Overlap-metric entry: per-example partial-credit scores accompany the
  // correctness flags (the UI shows the score next to ✓/✗ when it is not 0/1).
  evaluations: [
    { inputs: { userPrompt: 'User 1' }, expected: 'Response one', baselineResponse: 'wrong', baselineCorrect: false, baselineScore: 0, optimizedResponse: 'Response one', optimizedCorrect: true, optimizedScore: 1 },
    { inputs: { userPrompt: 'User 2' }, expected: 'Response two', baselineResponse: 'Response two', baselineCorrect: true, baselineScore: 1, optimizedResponse: 'Response two', optimizedCorrect: true, optimizedScore: 1 },
    { inputs: { userPrompt: 'User 3' }, expected: 'Response three', baselineResponse: 'nope', baselineCorrect: false, baselineScore: 0.25, optimizedResponse: 'still nope', optimizedCorrect: false, optimizedScore: 0.5 },
  ],
  optimizedPrompt,
  datasetSnapshot: 'Prompt-Optimization-Dataset-2.json',
  // Per-role call accounting the job records (the Builder shows calls +
  // token totals and prices them when the model carries cost attributes).
  usage: {
    target: { calls: 13, promptTokens: 5200, outputTokens: 640, runTime: 38.5 },
    judge: { calls: 0, promptTokens: 0, outputTokens: 0, runTime: 0 },
  },
  error: null,
};

function json(res, status, body) {
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(body));
}
function text(res, status, body, type = 'text/plain; charset=utf-8') {
  res.writeHead(status, { 'Content-Type': type });
  res.end(body);
}

// --- RAG Builder -------------------------------------------------------------
// The RAG kind of the Builder authors RAG setups: Model Manager models of the
// function RAG in projects tagged RAG-Engineering. Manifesting one reads the
// deployed runtime from the content root (the ingestion job, the retrieval
// probe, the retrieval model template and the custom steps), writes a job
// definition and a Studio flow into SAS Content and launches Job Execution
// jobs. Everything below keeps state, so a setup that was saved, manifested
// and run answers like one afterwards. GET /__ragstate returns that state.
//
// The runtime files are served from the REPOSITORY, so a manifest runs
// against the templates that ship. The mock usually runs as a copy in a
// scratch folder: the repository is looked for from MOCK_REPO_ROOT, from this
// file, from the served bundle and from the working directory.
const RAG_REPO_MARKER = path.join('SAS-Viya-Integrations', 'RAG', 'retrieve_context.py');
let ragRepoRoot;
function repoRoot() {
  if (ragRepoRoot !== undefined) return ragRepoRoot;
  ragRepoRoot = '';
  const starts = [process.env.MOCK_REPO_ROOT, __dirname, DIST_HTML && path.dirname(path.resolve(DIST_HTML)), process.cwd()];
  for (const start of starts.filter(Boolean)) {
    for (let folder = path.resolve(start); ; folder = path.dirname(folder)) {
      if (fs.existsSync(path.join(folder, RAG_REPO_MARKER))) { ragRepoRoot = folder; break; }
      if (path.dirname(folder) === folder) break;
    }
    if (ragRepoRoot) break;
  }
  if (!ragRepoRoot) console.log('RAG runtime files not found - set MOCK_REPO_ROOT to the repository root');
  return ragRepoRoot;
}
// The files of the content root that a manifest and a retrieval test read.
const RAG_SOURCES = {
  'rag-src-ingest': { folder: 'jobs', name: 'Ingest-Documents.sas', file: 'SAS-Viya-Integrations/RAG-Ingestion/Ingest-Documents.sas' },
  'rag-src-test': { folder: 'jobs', name: 'Test-Retrieval.sas', file: 'SAS-Viya-Integrations/RAG-Ingestion/Test-Retrieval.sas' },
  'rag-src-retrieve': { folder: 'models', name: 'retrieve_context.py', file: 'SAS-Viya-Integrations/RAG/retrieve_context.py' },
};
// The registered custom steps: every "RAG - *.step" of the repository, under
// an id derived from its name (ragstep-list-documents, ...).
function ragSteps() {
  const folder = repoRoot() && path.join(repoRoot(), 'SAS-Viya-Integrations', 'Custom-Steps');
  if (!folder || !fs.existsSync(folder)) return [];
  return fs
    .readdirSync(folder)
    .filter((name) => /^RAG - .+\.step$/.test(name))
    .sort()
    .map((name) => ({
      name,
      id: 'ragstep-' + name.replace(/^RAG - |\.step$/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '-'),
      file: path.join(folder, name),
    }));
}

// The retrieval model template comes in two variants. By default it is the
// one that ships now: its MANIFEST block holds SSL_VERIFY = True (the line is
// added when the file of the repository does not have it). POST
// /__ragtemplate { tls: false } serves the template of a content root that
// was deployed before the option "Verify TLS certificates in manifested
// models" existed: the same file without that line. An empty body, or
// { tls: true }, serves the current one again.
let ragTemplateHasTls = true;
function ragRetrievalTemplate() {
  const lines = fs.readFileSync(path.join(repoRoot(), RAG_SOURCES['rag-src-retrieve'].file), 'utf8').split('\n');
  const start = lines.findIndex((line) => line.startsWith('# ---- MANIFEST'));
  const end = lines.findIndex((line, index) => index > start && line.startsWith('# ----------------------------------------'));
  if (start < 0 || end < 0) return lines.join('\n');
  const block = lines.slice(start + 1, end).filter((line) => !/^SSL_VERIFY\s*=/.test(line));
  if (ragTemplateHasTls) block.push('SSL_VERIFY = True');
  return [...lines.slice(0, start + 1), ...block, ...lines.slice(end)].join('\n');
}

// POST /__norag hides the fixture project (a deployment before its first RAG
// project), POST /__rag brings it back and clears the failures below.
let ragFixtures = true;
// POST /__ragfail arms failures, an empty body clears them all:
//   { projects: 500 }    the RAG project listing answers that status
//   { run: 'reason' }    the next ingestion job reports "Ingestion failed: reason"
//   { documents: 1 }     the next ingestion job does its work and counts that
//                        many failed documents in its summary and its ledger
//   { retrieval: 'reason' } the next retrieval probe reports "FAILED reason"
//   { jobDefinition: 500 }  the next request that writes a job definition
//                        (POST or PUT) answers that status, once
const ragFailures = {};
const ragProjects = [{ id: 'rag-proj', name: 'RAG Demo Project', createdBy: 'anna', modifiedBy: 'anna' }];
let ragProjectCount = 0;
const RAG_SETUP = {
  version: 1,
  documentation: {
    modelPurpose: 'Answer questions about the HR policy handbook.',
    intendedUse: 'Internal HR assistant; retrieval context for the policy prompt.',
    expectedBenefit: 'Fewer tickets to HR for questions the handbook already answers.',
    outOfScopeUseCases: 'Legal advice, individual contract questions.',
    limitations: 'English documents only; scanned images are skipped.',
  },
  artifactsFolder: '/SAS Agentic AI Accelerator/RAG/generated',
  source: { path: 'sasserver:/mnt/data/policies', includeCode: false },
  extraction: { extractor: '' },
  chunking: { chunker: 'recursive', inputTokenLimit: 256, overlapTokens: 30 },
  embedding: { model: 'bge_small_en_v15', dims: 384, deploymentType: 'k8s', scrEndpoint: '' },
  store: { backend: 'pgvector', host: 'pg.mock.local', port: 5432, database: 'ragdb', sslmode: 'prefer', collection: 'policy_handbook' },
  tables: { prefix: 'RAGDEMO', caslib: 'Public' },
  pipelineVersion: 'v1',
  credentialDomain: 'agentic-ai-keys',
  policies: { deletedPolicy: 'retire', retainDays: 0, recordHistory: true, embedReplicas: 1, persistElements: true, persistChunks: true },
  job: { definitionUri: '/jobDefinitions/definitions/ragjob-1' },
};
// A corpus built on SingleStore, for a deployment that has since withheld that
// store (enable_singlestore=0): the setup must keep naming it.
const RAG_SETUP_SINGLESTORE = {
  ...RAG_SETUP,
  documentation: { ...RAG_SETUP.documentation, modelPurpose: 'Answer questions about the legacy product manuals.' },
  source: { path: 'sascontent:/Public/Legacy Manuals', includeCode: false },
  store: { backend: 'singlestore', host: 's2.mock.local', port: 3306, database: 'ragdb_legacy', sslmode: 'prefer', collection: 'legacy_manuals' },
  tables: { prefix: 'RAGLEGACY', caslib: 'Public' },
  job: { definitionUri: '/jobDefinitions/definitions/ragjob-2' },
};
const ragSetupModel = (id, name, createdBy, modifiedBy, extra) => ({
  id, name, projectId: 'rag-proj', function: 'RAG', algorithm: 'RAG',
  tool: 'SAS Agentic AI Accelerator RAG Builder', modeler: createdBy, createdBy, modifiedBy, ...extra,
});
// The fixture setups. rag-setup-2 was created and never saved: it carries no
// rag-setup.json, only documentation written onto the model.
const ragModels = {
  'rag-setup-1': ragSetupModel('rag-setup-1', 'Policy Handbook', 'anna', 'ben', {
    tags: ['LLM', 'RAG', 'bge_small_en_v15', 'pgvector'],
    modelPurpose: RAG_SETUP.documentation.modelPurpose,
  }),
  'rag-setup-2': ragSetupModel('rag-setup-2', 'Empty Setup', 'ben', 'ben', {
    tags: ['LLM', 'RAG'],
    modelPurpose: 'Documented before anything else was decided.',
  }),
  'rag-setup-ss': ragSetupModel('rag-setup-ss', 'Legacy Manuals', 'carla', 'carla', {
    tags: ['LLM', 'RAG', 'bge_small_en_v15', 'singlestore'],
    modelPurpose: RAG_SETUP_SINGLESTORE.documentation.modelPurpose,
  }),
};
const ragModelRevisions = {};
const ragModelEtag = (id) => `"rag-${id}-${ragModelRevisions[id] || 1}"`;
// A RAG setup is a fixture above or a model created with the function RAG.
const findRagModel = (id) =>
  ragModels[id] || createdModels.find((model) => model.id === id && model.function === 'RAG');
// The files on each setup model: modelId -> [{ id, name, role, type, body }].
const ragContents = {};
let ragContentCount = 0;
function putRagContent(modelId, name, body, role, type) {
  const items = ragContents[modelId] || (ragContents[modelId] = []);
  let item = items.find((candidate) => candidate.name === name);
  if (!item) {
    item = { id: `ragc-${++ragContentCount}`, name };
    items.push(item);
  }
  Object.assign(item, { body, role, type });
  return item;
}
const ragContentView = (item) => ({ id: item.id, name: item.name, role: item.role, fileUri: `/files/files/${item.id}` });
putRagContent('rag-setup-1', 'rag-setup.json', JSON.stringify(RAG_SETUP), 'documentation', 'application/json');
putRagContent('rag-setup-1', 'pipeline.yaml', 'version: 1\n', 'documentation', 'text/plain');
putRagContent('rag-setup-ss', 'rag-setup.json', JSON.stringify(RAG_SETUP_SINGLESTORE), 'documentation', 'application/json');
// POST /__ragenrich { on } stores rag-setup-1 as a setup that enriches its
// chunks with a chain of TWO prompts (both steps call the one fixture prompt,
// model-parse; the second pins its version 1.0 and stores a column). The
// Builder reads the prompts of a stored chain back one after the other, so a
// second step is what there is to draw after the first one was read. `on:
// false` stores the plain fixture again; both replace what a suite has saved
// on rag-setup-1 meanwhile.
const RAG_ENRICH_CHAIN = [
  {
    promptProjectId: 'proj-1', promptModelId: 'model-parse', promptModelName: 'Summarise Chunk',
    promptVersionId: '', promptVersionLabel: '',
    mapping: { chunk: 'chunk', document: 'document' }, headerOutput: 'context_header', columnOutputs: [], workers: 4,
  },
  {
    promptProjectId: 'proj-1', promptModelId: 'model-parse', promptModelName: 'Summarise Chunk',
    promptVersionId: 'ver-1', promptVersionLabel: '1.0 — 2026-08-01',
    mapping: { chunk: 'chunk', document: 'neighbours' }, headerOutput: '', columnOutputs: ['topic'], workers: 2,
  },
];
// One uploaded file out of a multipart body, cut at the boundary so a file
// that itself holds a line starting with two dashes stays whole.
function multipartFile(rawBody) {
  const boundary = (/^(--[^\r\n]+)\r\n/.exec(rawBody) || [])[1];
  const head = /Content-Disposition: form-data; name="[^"]*"; filename="([^"]*)"\r\n(?:Content-Type: ([^\r\n]*)\r\n)?\r\n/.exec(rawBody);
  if (!boundary || !head) return null;
  const start = head.index + head[0].length;
  const end = rawBody.indexOf('\r\n' + boundary, start);
  return { name: head[1], type: head[2] || '', body: rawBody.slice(start, end < 0 ? undefined : end) };
}

// SAS Content below the content root. jobs, models and steps list the runtime
// files; every other folder lists what was created in it. The folder that
// receives the generated artifacts (<content root>/generated by default) does
// not exist until a manifest creates it.
const RAG_CONTENT_ROOT = '/SAS Agentic AI Accelerator/RAG';
const ragFolders = {};
let ragFolderCount = 0;
function addRagFolder(folderPath, id) {
  const folder = { id: id || `rag-folder-new-${++ragFolderCount}`, name: folderPath.split('/').pop(), path: folderPath, members: [] };
  ragFolders[folderPath] = folder;
  return folder;
}
addRagFolder('/SAS Agentic AI Accelerator', 'rag-folder-root');
addRagFolder(RAG_CONTENT_ROOT, 'rag-folder-content');
['jobs', 'models', 'steps', 'rag_core'].forEach((name) => addRagFolder(`${RAG_CONTENT_ROOT}/${name}`, `rag-folder-${name}`));
addRagFolder('/Public', 'folder-public');
const ragFolderById = (id) => Object.values(ragFolders).find((folder) => folder.id === id);
function ragFolderMembers(folder) {
  const kind = folder.path.startsWith(RAG_CONTENT_ROOT + '/') ? folder.path.slice(RAG_CONTENT_ROOT.length + 1) : '';
  if (kind === 'steps') {
    return ragSteps().map((step) => ({ name: step.name, uri: `/dataFlows/steps/${step.id}`, contentType: 'dataFlowStep' }));
  }
  if (kind === 'jobs' || kind === 'models') {
    return Object.entries(RAG_SOURCES)
      .filter(([, source]) => source.folder === kind)
      .map(([id, source]) => ({ name: source.name, uri: `/files/files/${id}`, contentType: 'file' }));
  }
  return folder.members;
}
const dropRagMember = (uri) =>
  Object.values(ragFolders).forEach((folder) => {
    folder.members = folder.members.filter((member) => member.uri !== uri);
  });

// Job definitions (the generated ingestion job, the transient retrieval probe)
// and Studio flows. The two fixture definitions belong to the fixture setups.
const ragJobDefinitions = {};
let ragJobDefinitionCount = 0;
['ragjob-1', 'ragjob-2'].forEach((id, index) => {
  ragJobDefinitions[id] = {
    revision: 1,
    folderId: null,
    body: {
      id, name: `RAG Ingest - ${index === 0 ? 'Policy Handbook' : 'Legacy Manuals'}`, type: 'Compute',
      code: '/* generated by an earlier manifest */',
      parameters: [{ version: 1, name: 'sourcePath', defaultValue: '', type: 'character', required: false }],
    },
  };
});
const ragJobDefinitionEtag = (id) => `"ragjobdef-${id}-${ragJobDefinitions[id].revision}"`;
const ragFlows = {};
let ragFlowCount = 0;

// Jobs. An ingestion run and a retrieval probe both report "running" on the
// first poll and "completed" from the second on - like the real jobs, which
// always complete and say in the log how it went. While a job runs its log is
// read from the compute session; afterwards from the log file.
const ragJobs = {};
let ragJobCount = 0;
const RAG_HITS = [
  { rank: 1, score: 0.8731, distance: 0.1269, source: '/mnt/data/policies/leave.pdf', heading: 'Leave > Annual leave', header: '', page: 3,
    content: 'Employees accrue 2.5 days of annual leave per month & may carry over up to 50% of the unused days into the next year, provided the request is filed before the end of December and approved by the line manager. Days that are carried over have to be taken by the end of March.' },
  { rank: 2, score: 0.8112, distance: 0.1888, source: '/mnt/data/policies/leave.pdf', heading: 'Leave > Sick leave', header: '', page: 5,
    content: 'A medical certificate is required from the third day of absence.' },
  { rank: 3, score: 0.7405, distance: 0.2595, source: '/mnt/data/policies/travel.docx', heading: 'Travel > Booking', header: '', page: null,
    content: 'Trips are booked through the travel portal at least fourteen days ahead.' },
  { rank: 4, score: 0.6933, distance: 0.3067, source: '/mnt/data/policies/travel.docx', heading: 'Travel > Expenses', header: '', page: null,
    content: 'Receipts are submitted within thirty days of the return date.' },
];
// The milestones a job has logged so far (SAS.logMessage lines, without the
// NOTE prefix). They echo the launch arguments, so a suite can tell that the
// values of the setup reached the job.
function ragJobMessages(job) {
  const args = job.args;
  if (job.kind === 'retrieval') {
    const lines = ['RAGRETRIEVE rag_core bootstrapped (31 files)'];
    if (job.polls < 2) return lines;
    if (job.failure) return [...lines, `RAGRETRIEVE FAILED ${job.failure}`];
    const hits = RAG_HITS.slice(0, Math.max(0, Number(args.topK) || 5));
    return [
      ...lines,
      // Like the probe, each hit is one JSON document in which % and & travel
      // as their JSON escapes, so no log line carries a SAS macro trigger.
      ...hits.map((hit) => 'RAGRETRIEVE ROW ' + JSON.stringify(hit).replace(/%/g, '\\u0025').replace(/&/g, '\\u0026')),
      `RAGRETRIEVE rag cost: 1 call to ${args.embedModel}, 12 tokens, about 0.0000002 USD`,
      `RAGRETRIEVE DONE ${hits.length} row(s) for '${args.question}' from collection ${args.collection}`,
    ];
  }
  const lines = [
    'RAGINGEST rag_core bootstrapped (31 files)',
    `RAGINGEST run ${job.id}: ledger has 0 documents, config 5f2c9a1e`,
    `RAGINGEST connected: ${args.backend} + ${args.embedModel} (384 dims)`,
  ];
  if (job.polls < 2) return lines;
  if (job.failure) return [...lines, `RAGINGEST Ingestion failed: ${job.failure}`];
  // A job whose pipeline ran logs one line per step, each with its own count
  // of failures ("0 failed" on a clean run), and its summary last: the chunk
  // total and one count per ledger status, for the ledger rows this run
  // publishes (see ragLedgerRows). The lines are written as they arrive in a
  // SAS log: the job blanks ; % & and quotes in every message, so the
  // semicolon its code puts before "documents" is a second space here.
  const failed = job.failedDocuments || 0;
  return [
    ...lines,
    `RAGINGEST source: ${args.sourcePath} (3 files)`,
    `RAGINGEST rag extract: 2 extracted, ${failed ? 0 : 1} skipped, ${failed} failed`,
    'RAGINGEST rag embed: 23 embedded, 0 failed, usage { calls : 23,  tokens : 4100}',
    `RAGINGEST rag load: 2 docs loaded, 0 removed (retire), ${failed} failed -> collection  ${args.collection}  now 23 chunks`,
    `RAGINGEST collection ${args.collection}: 23 chunks  documents ${failed ? `failed=${failed}, ingested=2` : 'ingested=2, skipped=1'}`,
  ];
}
// A SAS log wraps at its line size: a long milestone continues on unprefixed
// lines, broken after a blank. The echo of the %put that wrote it comes first.
function ragLogLines(messages) {
  const lines = [{ type: 'note', line: 'NOTE: PROC PYTHON started.' }];
  messages.forEach((message, index) => {
    lines.push({ type: 'source', line: `${200 + index}  %put NOTE: Python-Subprocess - ${message.slice(0, 60)};` });
    let rest = `NOTE: Python-Subprocess - ${message}`;
    while (rest.length > 132) {
      const cut = rest.lastIndexOf(' ', 131) + 1 || 132;
      lines.push({ type: 'note', line: rest.slice(0, cut) });
      rest = rest.slice(cut);
    }
    lines.push({ type: 'note', line: rest });
  });
  return lines;
}
// The promoted ledger tables: the fixture's, and that of every setup whose
// ingestion has completed since. The fixture's third document FAILED in its
// run; a run of the mock skips it, unless the run was armed with failed
// documents (see /__ragfail).
const RAG_LEDGER_COLUMNS = ['doc_id', 'source_uri', 'status', 'chunk_count', 'error_text', 'run_id', 'updated_at'];
const ragLedgerRows = (runId, thirdStatus) => [
  { cells: ['d-001', '/mnt/data/policies/leave.pdf', 'ingested', 14, '', runId, '2026-10-01T08:00:00Z'] },
  { cells: ['d-002', '/mnt/data/policies/travel.docx', 'ingested', 9, '', runId, '2026-10-01T08:00:04Z'] },
  { cells: ['d-003', '/mnt/data/policies/scan.tiff', thirdStatus, 0, 'No extractor for .tiff', runId, '2026-10-01T08:00:05Z'] },
];
const ragLedgers = { 'Public/RAGDEMO_LEDGER': ragLedgerRows('run-3', 'failed') };
// A manifested prompt with output parsing, as the enrichment stage reads it:
// the signature names its inputs, the docstring its outputs.
const RAG_ENRICH_SCORE_CODE = [
  'import requests',
  '',
  'def scoreModel(chunk, document):',
  '    "Output: context_header, topic, run_time"',
  '    llm = "demo_llm"',
  '    return "", "", 0.0',
  '',
].join('\n');

http
  .createServer((req, res) => {
    // Decoded once, from the whole body: decoding chunk by chunk would break a
    // character whose bytes arrive in two chunks, and the uploads a suite
    // compares byte for byte (job sources, score code) are not pure ASCII.
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8');
      const u = new URL(req.url, 'http://localhost');
      const p = u.pathname;
      const xhr = req.headers['x-requested-with'] === 'XMLHttpRequest';
      if (p !== '/__log' && p !== '/__reset') {
        const entry = {
          method: req.method, url: req.url, body: raw, ifMatch: req.headers['if-match'] || null,
          xhr, csrf: req.headers['x-csrf-token'] || null, status: null,
        };
        log.push(entry);
        res.on('finish', () => { entry.status = res.statusCode; });
        // A browser (the suites themselves ask from Node) that asks a SAS Viya
        // service without the header of the app's HTTP client.
        const fromBrowser = /Mozilla/.test(req.headers['user-agent'] || '');
        const pageItself = p === '/' || p === '/index.html' || p === '/favicon.ico';
        const sessionWarmUp = req.method === 'GET' && /^\/[^/]+\/$/.test(p);
        if (fromBrowser && !xhr && !pageItself && !sessionWarmUp && !p.startsWith('/__') && !p.startsWith('/scr/')) {
          directRequests.push(`${req.method} ${req.url}`);
        }
      }

      if (p === '/__log') return json(res, 200, log);
      if (p === '/__direct') return json(res, 200, directRequests);
      if (p === '/__reset') { log.length = 0; return json(res, 200, {}); }
      if (p === '/__failjob') { failNextJob = true; return json(res, 200, {}); }
      if (p === '/__failmodel') {
        let body = {};
        try { body = JSON.parse(raw); } catch {}
        if (!body.name) Object.keys(failingModels).forEach((name) => delete failingModels[name]);
        else if (body.status) failingModels[body.name] = body.status;
        else delete failingModels[body.name];
        return json(res, 200, failingModels);
      }
      if (p === '/__nosession') {
        let body = {};
        try { body = JSON.parse(raw); } catch {}
        if (!body.service) Object.keys(noSession).forEach((name) => delete noSession[name]);
        else noSession[body.service] = { stuck: Boolean(body.stuck), csrf: Boolean(body.csrf), established: false };
        return json(res, 200, noSession);
      }
      if (p === '/__usage') {
        let body = {};
        try { body = JSON.parse(raw); } catch {}
        if (!body.model) Object.keys(usageFor).forEach((name) => delete usageFor[name]);
        else if (body.status || Array.isArray(body.flows)) usageFor[body.model] = { status: body.status || null, flows: body.flows || [] };
        else delete usageFor[body.model];
        return json(res, 200, usageFor);
      }
      // The decision fixtures live in a second project that only exists once a
      // suite asks for it, so the regression suite's project counts stay put.
      if (p === '/__decision') { decisionProject = true; return json(res, 200, {}); }
      // The tracker of a manifested prompt-test for model-used (see above).
      if (p === '/__manifested') {
        let body = {};
        try { body = JSON.parse(raw); } catch {}
        manifestedTracker = body.on !== false;
        return json(res, 200, { manifested: manifestedTracker });
      }

      // ===== RAG Builder =======================================================
      // These routes come first: the RAG project listing is the projects route
      // with another tag filter, a RAG setup is a model like a prompt-test,
      // and its jobs share /jobExecution with the optimization job.
      if (p === '/__norag') { ragFixtures = false; return json(res, 200, {}); }
      if (p === '/__rag') {
        ragFixtures = true;
        Object.keys(ragFailures).forEach((name) => delete ragFailures[name]);
        return json(res, 200, {});
      }
      if (p === '/__ragtemplate') {
        let body = {};
        try { body = JSON.parse(raw); } catch {}
        ragTemplateHasTls = body.tls !== false;
        return json(res, 200, { tls: ragTemplateHasTls });
      }
      if (p === '/__ragenrich') {
        let body = {};
        try { body = JSON.parse(raw); } catch {}
        const enriched = body.on !== false;
        putRagContent(
          'rag-setup-1', 'rag-setup.json',
          JSON.stringify(enriched ? { ...RAG_SETUP, enrich: RAG_ENRICH_CHAIN } : RAG_SETUP),
          'documentation', 'application/json'
        );
        return json(res, 200, { enriched });
      }
      if (p === '/__ragfail') {
        let body = {};
        try { body = JSON.parse(raw); } catch {}
        if (!Object.keys(body).length) Object.keys(ragFailures).forEach((name) => delete ragFailures[name]);
        Object.entries(body).forEach(([name, value]) => {
          if (value) ragFailures[name] = value;
          else delete ragFailures[name];
        });
        return json(res, 200, ragFailures);
      }
      if (p === '/__ragstate') {
        return json(res, 200, {
          projects: ragProjects,
          models: [...Object.values(ragModels), ...createdModels.filter((model) => model.function === 'RAG')],
          contents: Object.fromEntries(Object.entries(ragContents).map(([id, items]) => [id, items.map((item) => item.name)])),
          folders: Object.values(ragFolders).map((folder) => ({ id: folder.id, path: folder.path, members: ragFolderMembers(folder) })),
          jobDefinitions: Object.values(ragJobDefinitions).map((definition) => ({ ...definition.body, folderId: definition.folderId })),
          flows: Object.values(ragFlows),
          jobs: Object.values(ragJobs),
          ledgers: Object.keys(ragLedgers),
        });
      }
      if (p === '/favicon.ico') { res.writeHead(204); return res.end(); }

      // A service a suite took the session away from (see /__nosession). The
      // plain GET to its root is the request that establishes the session.
      const sessionService = p.split('/')[1];
      const sessionless = noSession[sessionService];
      if (sessionless) {
        if (!xhr && req.method === 'GET' && p === `/${sessionService}/`) {
          if (!sessionless.stuck) sessionless.established = true;
          return json(res, 200, { version: 1, links: [] });
        }
        if (xhr && !sessionless.established) { res.writeHead(449); return res.end(); }
        if (xhr && sessionless.csrf && req.method !== 'GET' && req.headers['x-csrf-token'] !== MOCK_CSRF_TOKEN) {
          res.writeHead(403, { 'x-forbidden-reason': 'CSRF', 'x-csrf-token': MOCK_CSRF_TOKEN });
          return res.end();
        }
      }

      // -- SAS Model Manager: projects, setups and what a setup lists -----------
      if (p === '/modelRepository/projects' && req.method === 'GET' &&
          (u.searchParams.get('filter') || '').includes('RAG-Engineering')) {
        if (ragFailures.projects) return json(res, Number(ragFailures.projects), { message: 'the model repository does not answer' });
        return json(res, 200, { items: ragProjects.filter((project) => ragFixtures || project.id !== 'rag-proj') });
      }
      if (p === '/modelRepository/projects' && req.method === 'POST') {
        let body = {};
        try { body = JSON.parse(raw); } catch {}
        if ((body.tags || []).includes('RAG-Engineering')) {
          const project = {
            id: `rag-proj-new-${++ragProjectCount}`, name: body.name || 'New RAG Project', description: body.description || '',
            function: body.function, tags: body.tags, createdBy: 'tester', modifiedBy: 'tester',
          };
          ragProjects.push(project);
          return json(res, 201, project);
        }
      }
      const ragProjectRoute = /^\/modelRepository\/projects\/([^/]+)(\/models)?$/.exec(p);
      const ragProject = ragProjectRoute && ragProjects.find((project) => project.id === ragProjectRoute[1]);
      if (ragProject && ragProjectRoute[2] && req.method === 'GET') {
        return json(res, 200, {
          items: [
            ...Object.values(ragModels).filter((model) => model.projectId === ragProject.id),
            ...createdModelsFor(ragProject.id, u.searchParams.get('filter') || ''),
          ],
        });
      }
      // A project is there until it is deleted: what a delete without an
      // answer reads back.
      if (ragProject && !ragProjectRoute[2] && req.method === 'GET') return json(res, 200, ragProject);
      if (ragProject && !ragProjectRoute[2] && req.method === 'DELETE') {
        ragProjects.splice(ragProjects.indexOf(ragProject), 1);
        res.writeHead(204);
        return res.end();
      }
      // The registered embedding models (embeddingProjectID=emb-proj). One name
      // is outside the shipped fact sheet, so its dimension stays editable.
      if (p === '/modelRepository/projects/emb-proj/models') {
        return json(res, 200, { items: [
          { id: 'emb-1', name: 'bge_small_en_v15' },
          { id: 'emb-2', name: 'text_embedding_3_small' },
          { id: 'emb-3', name: 'custom_embedder' },
        ] });
      }
      // The enrichment stage lists the prompts manifested with output parsing
      // across ALL projects in one request; each item carries its project.
      if (p === '/modelRepository/models' && req.method === 'GET') {
        const filter = u.searchParams.get('filter') || '';
        return json(res, 200, { items: filter.includes('Output-Parsing')
          ? [{ id: 'model-parse', name: 'Summarise Chunk', projectId: 'proj-1', projectName: 'Demo Project' }]
          : [] });
      }
      if (p === '/modelRepository/models/model-parse/modelVersions' && req.method === 'GET') {
        return json(res, 200, { items: [
          { id: 'ver-1', modelVersionName: '1.0', creationTimeStamp: '2026-08-01T10:00:00Z' },
          { id: 'ver-2', modelVersionName: '1.1', creationTimeStamp: '2026-08-05T10:00:00Z' },
        ] });
      }
      if (p === '/modelRepository/models/model-parse/contents' && req.method === 'GET') {
        return json(res, 200, { items: [{ id: 'c-parse', name: 'Summarise_Chunk.py', role: 'score', fileUri: '/files/files/parse-score' }] });
      }
      if (p === '/files/files/parse-score/content') return text(res, 200, RAG_ENRICH_SCORE_CODE);

      // A setup's files are kept as uploaded, so a saved setup reopens with
      // what was saved. A fixture setup also keeps its own attributes and tags
      // (ETag-guarded); a created one does that through the routes for created
      // models further down.
      const ragModelRoute = /^\/modelRepository\/models\/([^/]+)(?:\/(contents)(?:\/([^/]+))?)?$/.exec(p);
      const ragModel = ragModelRoute && findRagModel(ragModelRoute[1]);
      if (ragModel) {
        const [, modelId, contents, contentId] = ragModelRoute;
        const items = ragContents[modelId] || [];
        if (contents && !contentId && req.method === 'GET') return json(res, 200, { items: items.map(ragContentView) });
        if (contents && !contentId && req.method === 'POST') {
          const file = multipartFile(raw);
          if (!file) return json(res, 400, { message: 'no file in the request' });
          const item = putRagContent(modelId, file.name, file.body, u.searchParams.get('role') || '', file.type);
          return json(res, 201, { items: [ragContentView(item)] });
        }
        if (contents && contentId && req.method === 'DELETE') {
          ragContents[modelId] = items.filter((item) => item.id !== contentId);
          res.writeHead(204);
          return res.end();
        }
        if (!contents && ragModels[modelId]) {
          if (req.method === 'GET') {
            res.writeHead(200, { 'Content-Type': 'application/json', ETag: ragModelEtag(modelId) });
            return res.end(JSON.stringify(ragModel));
          }
          if (req.method === 'PUT') {
            if (req.headers['if-match'] !== ragModelEtag(modelId)) return json(res, 412, { message: 'the model changed since it was read' });
            try { Object.assign(ragModel, JSON.parse(raw), { id: modelId }); } catch {}
            ragModelRevisions[modelId] = (ragModelRevisions[modelId] || 1) + 1;
            res.writeHead(200, { 'Content-Type': 'application/json', ETag: ragModelEtag(modelId) });
            return res.end(JSON.stringify(ragModel));
          }
          if (req.method === 'DELETE') {
            delete ragModels[modelId];
            deletedModels.add(modelId);
            res.writeHead(204);
            return res.end();
          }
        }
      }
      const ragFile = /^\/files\/files\/(ragc-\d+)\/content$/.exec(p);
      if (ragFile) {
        const item = Object.values(ragContents).flat().find((candidate) => candidate.id === ragFile[1]);
        if (!item) return json(res, 404, { message: 'no such file' });
        return text(res, 200, item.body, item.type || 'application/octet-stream');
      }

      // -- SAS Content: the deployed runtime and the generated artifacts ---------
      const ragSource = /^\/files\/files\/(rag-src-[a-z]+)\/content$/.exec(p);
      if (ragSource && RAG_SOURCES[ragSource[1]]) {
        if (!repoRoot()) return json(res, 404, { message: 'the repository was not found - set MOCK_REPO_ROOT' });
        if (ragSource[1] === 'rag-src-retrieve') return text(res, 200, ragRetrievalTemplate());
        return text(res, 200, fs.readFileSync(path.join(repoRoot(), RAG_SOURCES[ragSource[1]].file)));
      }
      if (p === '/folders/folders/@item') {
        const folder = ragFolders[(u.searchParams.get('path') || '').replace(/\/+$/, '')];
        if (folder) return json(res, 200, { id: folder.id, name: folder.name, uri: `/folders/folders/${folder.id}` });
      }
      const ragMembersRoute = /^\/folders\/folders\/([^/]+)\/members$/.exec(p);
      if (ragMembersRoute && ragFolderById(ragMembersRoute[1])) {
        return json(res, 200, { items: ragFolderMembers(ragFolderById(ragMembersRoute[1])) });
      }
      if (p === '/folders/folders' && req.method === 'POST') {
        const parent = ragFolderById((u.searchParams.get('parentFolderUri') || '').split('/').pop());
        let body = {};
        try { body = JSON.parse(raw); } catch {}
        if (!parent || !body.name) return json(res, 404, { message: 'no such parent folder' });
        const folder = addRagFolder(`${parent.path}/${body.name}`);
        parent.members.push({ name: folder.name, uri: `/folders/folders/${folder.id}`, contentType: 'folder' });
        return json(res, 201, { id: folder.id, name: folder.name, uri: `/folders/folders/${folder.id}` });
      }
      const ragStepRoute = /^\/dataFlows\/steps\/([^/]+)$/.exec(p);
      if (ragStepRoute) {
        const step = ragSteps().find((candidate) => candidate.id === ragStepRoute[1]);
        if (!step) return json(res, 404, { message: 'no such step' });
        return json(res, 200, { ...JSON.parse(fs.readFileSync(step.file, 'utf8')), id: step.id });
      }
      if (p === '/dataFlows/dataFlows' && req.method === 'POST') {
        const parent = ragFolderById((u.searchParams.get('parentFolderUri') || '').split('/').pop());
        let body = null;
        try { body = JSON.parse(raw); } catch {}
        if (!parent || !body) return json(res, 400, { message: 'a flow needs a parent folder and a body' });
        const flow = { ...body, id: `ragflow-${++ragFlowCount}`, folderId: parent.id };
        ragFlows[flow.id] = flow;
        parent.members.push({ name: flow.name, uri: `/dataFlows/dataFlows/${flow.id}`, contentType: 'dataFlow' });
        return json(res, 201, { id: flow.id, name: flow.name });
      }
      const ragFlowRoute = /^\/dataFlows\/dataFlows\/([^/]+)$/.exec(p);
      if (ragFlowRoute && ragFlows[ragFlowRoute[1]]) {
        if (req.method === 'GET') return json(res, 200, ragFlows[ragFlowRoute[1]]);
        if (req.method === 'DELETE') {
          delete ragFlows[ragFlowRoute[1]];
          dropRagMember(p);
          res.writeHead(204);
          return res.end();
        }
      }
      // An armed failure of the next write of a job definition (see /__ragfail).
      if (ragFailures.jobDefinition && /^\/jobDefinitions\/definitions(\/[^/]+)?$/.test(p) && (req.method === 'POST' || req.method === 'PUT')) {
        const status = Number(ragFailures.jobDefinition);
        delete ragFailures.jobDefinition;
        return json(res, status, { message: 'the job definition service does not answer' });
      }
      // Without a parent folder a definition is unfiled: it exists and can be
      // launched, and no folder lists it (the transient retrieval probe).
      if (p === '/jobDefinitions/definitions' && req.method === 'POST') {
        let body = null;
        try { body = JSON.parse(raw); } catch {}
        const parentUri = u.searchParams.get('parentFolderUri');
        const parent = parentUri ? ragFolderById(parentUri.split('/').pop()) : null;
        if (!body || (parentUri && !parent)) return json(res, 400, { message: 'a definition needs a body and, when filed, an existing folder' });
        const id = `ragjobdef-${++ragJobDefinitionCount}`;
        ragJobDefinitions[id] = { revision: 1, folderId: parent ? parent.id : null, body: { ...body, id } };
        if (parent) parent.members.push({ name: body.name, uri: `/jobDefinitions/definitions/${id}`, contentType: 'jobDefinition' });
        res.writeHead(201, { 'Content-Type': 'application/vnd.sas.job.definition+json', ETag: ragJobDefinitionEtag(id) });
        return res.end(JSON.stringify(ragJobDefinitions[id].body));
      }
      const ragDefinitionRoute = /^\/jobDefinitions\/definitions\/([^/]+)$/.exec(p);
      if (ragDefinitionRoute) {
        const id = ragDefinitionRoute[1];
        const definition = ragJobDefinitions[id];
        if (!definition) return json(res, 404, { message: 'no such job definition' });
        if (req.method === 'PUT') {
          if (req.headers['if-match'] !== ragJobDefinitionEtag(id)) return json(res, 412, { message: 'the definition changed since it was read' });
          try { definition.body = { ...JSON.parse(raw), id }; } catch {}
          definition.revision += 1;
        }
        if (req.method === 'DELETE') {
          delete ragJobDefinitions[id];
          dropRagMember(p);
          res.writeHead(204);
          return res.end();
        }
        res.writeHead(200, { 'Content-Type': 'application/vnd.sas.job.definition+json', ETag: ragJobDefinitionEtag(id) });
        return res.end(JSON.stringify(definition.body));
      }

      // -- Job Execution: the ingestion run and the retrieval probe --------------
      if (p === '/jobExecution/jobs' && req.method === 'POST') {
        let request = {};
        try { request = JSON.parse(raw); } catch {}
        const definition = ragJobDefinitions[String(request.jobDefinitionUri || '').split('/').pop()];
        if (definition) {
          // A probe is the definition that takes a question.
          const kind = (definition.body.parameters || []).some((parameter) => parameter.name === 'question') ? 'retrieval' : 'ingestion';
          const id = `${kind === 'retrieval' ? 'ragtest' : 'ragrun'}-${++ragJobCount}`;
          const armed = kind === 'retrieval' ? 'retrieval' : 'run';
          ragJobs[id] = { id, kind, name: request.name, definitionId: definition.body.id, args: request.arguments || {}, polls: 0, failure: ragFailures[armed] || '', created: new Date().toISOString() };
          delete ragFailures[armed];
          if (kind === 'ingestion') {
            ragJobs[id].failedDocuments = Number(ragFailures.documents) || 0;
            delete ragFailures.documents;
          }
          return json(res, 201, { id, state: 'pending' });
        }
      }
      // The jobs that have not ended, newest first, as a page that was loaded
      // again asks for them (a filter on the state). Listing a job is no poll:
      // it does not move the job on.
      if (p === '/jobExecution/jobs' && req.method === 'GET' && /state/.test(u.searchParams.get('filter') || '')) {
        const items = Object.values(ragJobs)
          .filter((job) => job.polls < 2)
          .reverse()
          .map((job) => ({
            id: job.id,
            state: 'running',
            creationTimeStamp: job.created,
            jobRequest: { name: job.name, jobDefinitionUri: `/jobDefinitions/definitions/${job.definitionId}`, arguments: job.args },
          }));
        return json(res, 200, { count: items.length, items });
      }
      const ragJobRoute = /^\/jobExecution\/jobs\/(rag(?:run|test)-\d+)$/.exec(p);
      if (ragJobRoute && ragJobs[ragJobRoute[1]] && req.method === 'GET') {
        const job = ragJobs[ragJobRoute[1]];
        job.polls += 1;
        if (job.polls < 2) {
          return json(res, 200, { id: job.id, state: 'running', results: { COMPUTE_SESSION: `rag-cs-${job.id} (${job.args._contextName || ''})`, COMPUTE_JOB: `rag-cj-${job.id}` } });
        }
        if (job.kind === 'ingestion' && !job.failure) {
          ragLedgers[`${job.args.ledgerCaslib}/${job.args.ledgerTable}`] = ragLedgerRows(job.id, job.failedDocuments ? 'failed' : 'skipped');
        }
        return json(res, 200, { id: job.id, state: 'completed', logLocation: `/files/files/raglog-${job.id}` });
      }
      // The live log: plain text, as a compute server returns it; gone with
      // the session once the job has ended.
      const ragLiveLog = /^\/compute\/sessions\/rag-cs-(rag(?:run|test)-\d+)\/jobs\/[^/]+\/log\/content$/.exec(p);
      if (ragLiveLog) {
        const job = ragJobs[ragLiveLog[1]];
        if (!job || job.polls >= 2) return json(res, 404, { message: 'session not found' });
        return text(res, 200, ragLogLines(ragJobMessages(job)).map((entry) => entry.line).join('\r\n'));
      }
      const ragLogFile = /^\/files\/files\/raglog-(rag(?:run|test)-\d+)\/content$/.exec(p);
      if (ragLogFile && ragJobs[ragLogFile[1]]) {
        return json(res, 200, {
          version: 2,
          name: 'items',
          accept: 'application/vnd.sas.compute.log.line',
          items: [...ragLogLines(ragJobMessages(ragJobs[ragLogFile[1]])), { type: 'note', line: 'NOTE: PROC PYTHON ended.' }]
            .map((entry) => ({ version: 1, ...entry })),
        });
      }
      if (p === '/compute/contexts') return json(res, 200, { items: [] });

      // -- CAS: the promoted ledger (columns and rows are two services) ----------
      const ragLedgerRoute = /^\/(casManagement|casRowSets)\/servers\/cas-shared-default\/caslibs\/([^/]+)\/tables\/([^/]+_LEDGER)\/(columns|rows)$/.exec(p);
      if (ragLedgerRoute) {
        const rows = ragLedgers[`${decodeURIComponent(ragLedgerRoute[2])}/${decodeURIComponent(ragLedgerRoute[3])}`];
        if (!rows) return json(res, 404, { message: 'table not found' });
        if (ragLedgerRoute[1] === 'casManagement' && ragLedgerRoute[4] === 'columns') {
          return json(res, 200, { items: RAG_LEDGER_COLUMNS.map((name) => ({ name })) });
        }
        if (ragLedgerRoute[1] === 'casRowSets' && ragLedgerRoute[4] === 'rows') return json(res, 200, { items: rows });
      }
      // ===== end of the RAG Builder routes =======================================

      if (p === '/' || p === '/index.html') {
        res.writeHead(200, { 'Content-Type': 'text/html' });
        return res.end(fs.readFileSync(DIST_HTML));
      }
      if (p === '/identities/users/@currentUser') {
        return json(res, 200, { id: 'tester', name: 'Tester' });
      }
      if (p === '/modelRepository/projects' && req.method === 'GET') {
        const filter = u.searchParams.get('filter') || '';
        if (filter.includes('Decision Model Project')) {
          return json(res, 200, { items: [{ id: 'dec-proj', name: 'Decision Model Project' }] });
        }
        // proj-2 is a decision project: it carries the Decision-Engineering tag
        // (and Prompt-Engineering, like every project of the Builder).
        if (filter.includes('Decision-Engineering')) {
          return json(res, 200, { items: decisionProject ? [{ id: 'proj-2', name: 'Decision Prompts', createdBy: 'anna', modifiedBy: 'anna' }] : [] });
        }
        return json(res, 200, {
          items: [
            { id: 'proj-1', name: 'Demo Project', createdBy: 'anna', modifiedBy: 'anna' },
            ...(decisionProject ? [{ id: 'proj-2', name: 'Decision Prompts', createdBy: 'anna', modifiedBy: 'anna' }] : []),
          ],
        });
      }
      // Creating a project reads the repository first (its folder id).
      if (p === '/modelRepository/repositories/repo-1') {
        return json(res, 200, { id: 'repo-1', name: 'LLM Repository', folderId: 'folder-repo-1' });
      }
      if (p === '/modelRepository/projects' && req.method === 'POST') {
        let body = {};
        try { body = JSON.parse(raw); } catch {}
        return json(res, 201, { id: 'proj-new', name: body.name || 'New Project', tags: body.tags || [] });
      }
      // Creating a model (a prompt-test). Like the real service the mock keeps
      // the fields it knows and DROPS the tags sent at creation: the answer and
      // every later listing carry tags: null until a PUT writes some. A name
      // that a model of the same project already has is refused with 409 and
      // an error body, which has no id.
      if (p === '/modelRepository/models' && req.method === 'POST') {
        let body = {};
        try { body = JSON.parse(raw); } catch {}
        const sameName = (model) => model.projectId === body.projectId && model.name === body.name;
        if (createdModels.some(sameName) || Object.values(ragModels).some(sameName)) {
          return json(res, 409, { errorCode: 21505, message: `A model named "${body.name}" already exists in the project.`, httpStatusCode: 409 });
        }
        const model = { id: `model-new-${createdModels.length + 1}`, tags: null, createdBy: 'tester', modifiedBy: 'tester' };
        ['name', 'description', 'function', 'tool', 'modeler', 'projectId', 'algorithm', 'scoreCodeType'].forEach((field) => {
          if (body[field] !== undefined) model[field] = body[field];
        });
        createdModels.push(model);
        deletedModels.delete(model.id);
        return json(res, 201, { items: [model] });
      }
      const createdRoute = /^\/modelRepository\/models\/([^/]+)(\/contents)?$/.exec(p);
      const createdModel = createdRoute && createdModels.find((model) => model.id === createdRoute[1]);
      if (createdModel && createdRoute[2] && req.method === 'GET') return json(res, 200, { items: [] });
      if (createdModel && !createdRoute[2]) {
        if (req.method === 'GET') {
          res.writeHead(200, { 'Content-Type': 'application/json', ETag: '"created1"' });
          return res.end(JSON.stringify(createdModel));
        }
        if (req.method === 'PUT') {
          // An update is where tags do stick.
          try { Object.assign(createdModel, JSON.parse(raw), { id: createdModel.id }); } catch {}
          return json(res, 200, createdModel);
        }
        if (req.method === 'DELETE') {
          createdModels.splice(createdModels.indexOf(createdModel), 1);
          deletedModels.add(createdModel.id);
          // Its files go with it. The id is given out again to the next model
          // that is created, which must not open with the rag-setup.json - and
          // the generated job - of a setup that was deleted.
          delete ragContents[createdModel.id];
          res.writeHead(204);
          return res.end();
        }
      }
      if (p === '/modelRepository/projects/proj-new/models') {
        return json(res, 200, { items: createdModelsFor('proj-new', u.searchParams.get('filter') || '') });
      }
      if (p === '/modelRepository/projects/proj-2/models') {
        return json(res, 200, {
          items: [
            // A listing item carries the model's algorithm and tags, which is
            // how the Builder tells a decision template from an LLM prompt.
            {
              id: 'model-dec', name: 'Ticket Routing', createdBy: 'anna', modifiedBy: 'anna',
              algorithm: 'Decision-Template', tags: ['Decision', 'Prompt-Template', 'Decision-Template'],
            },
            ...createdModelsFor('proj-2', u.searchParams.get('filter') || ''),
          ],
        });
      }
      if (p === '/modelRepository/projects/dec-proj/models') {
        const filter = u.searchParams.get('filter') || '';
        if (filter.includes('deprecated')) return json(res, 200, { items: [] });
        return json(res, 200, { items: [{ id: 'dec-1', name: 'jev_mock' }, { id: 'dec-2', name: 'von_mock' }] });
      }
      if (p === '/modelRepository/models/dec-1/contents') {
        return json(res, 200, { items: [{ id: 'c-dopt1', name: 'options.json', fileUri: '/files/files/dopt-1' }] });
      }
      if (p === '/modelRepository/models/dec-2/contents') {
        return json(res, 200, { items: [{ id: 'c-dopt2', name: 'options.json', fileUri: '/files/files/dopt-2' }] });
      }
      if (p === '/files/files/dopt-1/content') return json(res, 200, { API_KEY: { default: 'OpenAI' } });
      if (p === '/files/files/dopt-2/content') return json(res, 200, {});
      if (p === '/modelRepository/projects/llm-proj/models') {
        const filter = u.searchParams.get('filter') || '';
        if (filter.includes('deprecated')) return json(res, 200, { items: [] });
        return json(res, 200, {
          items: [
            { id: 'llm-1', name: 'demo_llm' },
            { id: 'llm-2', name: 'second_llm' },
            { id: 'llm-3', name: 'contrarian_judge' },
          ],
        });
      }
      if (p === '/modelRepository/projects/proj-1/models') {
        // No decision templates among the fixtures: the tag filter answers
        // with created models only, and only once a PUT gave them the tag.
        const filter = u.searchParams.get('filter') || '';
        if (filter.includes('Decision-Template')) return json(res, 200, { items: createdModelsFor('proj-1', filter) });
        return json(res, 200, {
          items: [
            { id: 'model-used', name: 'Used Prompt', createdBy: 'anna', modifiedBy: 'ben' },
            { id: 'model-free', name: 'score_metric_answer_relevancy', createdBy: 'ben', modifiedBy: 'ben' },
            { id: 'model-err', name: 'Error Prompt', createdBy: 'carla', modifiedBy: 'anna' },
            ...createdModelsFor('proj-1', filter),
          ],
        });
      }
      if (p === '/modelRepository/models/model-dec/contents' && req.method === 'GET') {
        return json(res, 200, { items: [{ id: 'c-dtrk', name: 'Prompt-Experiment-Tracker.json', fileUri: '/files/files/dtrk-1' }] });
      }
      if (p === '/files/files/dtrk-1/content') return json(res, 200, decisionTrackerRows);
      if (p === '/modelRepository/models/llm-1/contents') {
        return json(res, 200, {
          items: [{ id: 'c-opt', name: 'options.json', fileUri: '/files/files/opt-1' }],
        });
      }
      if (p === '/modelRepository/models/llm-2/contents') {
        return json(res, 200, {
          items: [{ id: 'c-opt2', name: 'options.json', fileUri: '/files/files/opt-2' }],
        });
      }
      if (p === '/modelRepository/models/llm-3/contents') {
        return json(res, 200, {
          items: [{ id: 'c-opt3', name: 'options.json', fileUri: '/files/files/opt-3' }],
        });
      }
      if (p === '/files/files/opt-1/content') {
        return json(res, 200, { temperature: { default: 0.7 } });
      }
      if (p === '/files/files/opt-2/content') {
        // second_llm needs an OpenAI key — its credential domain EXISTS below.
        return json(res, 200, { temperature: { default: 0.5 }, API_KEY: { default: 'OpenAI' } });
      }
      if (p === '/files/files/opt-3/content') {
        // contrarian_judge needs an Anthropic key — its domain 404s below, so
        // with a credentialDomainPrefix configured it must render disabled.
        return json(res, 200, { temperature: { default: 0.3 }, API_KEY: { default: 'Anthropic' } });
      }
      // The single credential domain: one secrets map, entries named per
      // provider. OpenAI has a key; Anthropic deliberately has none, so with
      // credentialDomain configured the model needing it renders disabled.
      // The same domain says who may reach a vector store (<BACKEND>_RAG_USER
      // and _RAG_PW) and where it lives (<BACKEND>_HOST, _PORT, _DB): pgvector
      // is complete here, SingleStore is absent - so the RAG Builder offers it
      // disabled, as a store this user holds no credential for.
      const encoded = (entries) =>
        Object.fromEntries(Object.entries(entries).map(([name, value]) => [name, Buffer.from(value).toString('base64')]));
      const pgvectorEntries = {
        PGVECTOR_RAG_USER: 'rag_ingest', PGVECTOR_RAG_PW: 'mock-db-pw',
        PGVECTOR_HOST: 'pg.mock.local', PGVECTOR_PORT: '5432', PGVECTOR_DB: 'ragdb',
      };
      if (p === '/credentials/domains/agentic-ai-keys/secrets') {
        return json(res, 200, {
          domainId: 'agentic-ai-keys',
          secrets: encoded({ OpenAI: 'sk-mock-openai-key', ...pgvectorEntries }),
        });
      }
      // A second domain with a key for every provider of the fixtures and the
      // entries of both vector stores, so a suite that needs all three LLMs
      // selectable, or SingleStore reachable, opens the app with
      // credentialDomain=all-keys. SingleStore carries no port: the store's
      // default applies.
      if (p === '/credentials/domains/all-keys/secrets') {
        return json(res, 200, {
          domainId: 'all-keys',
          secrets: encoded({
            OpenAI: 'sk-mock-openai-key',
            Anthropic: 'sk-mock-anthropic-key',
            ...pgvectorEntries,
            SINGLESTORE_RAG_USER: 'rag_ingest', SINGLESTORE_RAG_PW: 'mock-s2-pw',
            SINGLESTORE_HOST: 's2.mock.local', SINGLESTORE_DB: 'ragdb_legacy',
          }),
        });
      }
      if (p.startsWith('/credentials/domains/') && p.endsWith('/secrets')) {
        return json(res, 404, { message: 'The requested credential does not exist.' });
      }
      if (p === '/modelRepository/models/model-used/contents' && req.method === 'GET') {
        // The requirements.json entry simulates a leftover from an earlier
        // integrated-call manifest: a manifest without the LLM call must
        // remove it. The optimization tracker is present from the start (the
        // prompt has a legacy run) and grows as mock jobs finish.
        const items = [
          { id: 'c-trk', name: 'Prompt-Experiment-Tracker.json', fileUri: '/files/files/trk-1' },
          { id: 'c-req', name: 'requirements.json', fileUri: '/files/files/req-1' },
        ];
        if (!optTrackerDeleted) {
          items.push({ id: 'c-opttrk', name: 'Prompt-Optimization-Tracker.json', fileUri: '/files/files/opttrk-1' });
        }
        return json(res, 200, { items });
      }
      // @item resolves FOLDERS only — it 404s for jobDefinition members on a
      // real SAS Viya, so the app resolves the parent folder, then finds the
      // definition among the folder's members by name.
      if (p === '/folders/folders/@item') {
        const path = u.searchParams.get('path') || '';
        if (path === '/Public/Jobs') {
          return json(res, 200, { id: 'folder-jobs', uri: '/folders/folders/folder-jobs' });
        }
        return json(res, 404, { message: 'no such content item: ' + path });
      }
      if (p === '/folders/folders/folder-jobs/members') {
        const filter = u.searchParams.get('filter') || '';
        const items = filter.includes("'Optimize-Prompt-DSPy'")
          ? [{ name: 'Optimize-Prompt-DSPy', uri: '/jobDefinitions/definitions/jobdef-1', contentType: 'jobDefinition' }]
          : [];
        return json(res, 200, { items });
      }
      // CAS Management: the optimize panel builds its server → caslib → table
      // dropdowns from these listings, then probes the chosen table (info +
      // columns) before launching a run with the CAS dataset source. GHOST is
      // listed but its probe 404s — a stale listing (table dropped between
      // browse and launch).
      if (p === '/casManagement/servers') {
        return json(res, 200, { items: [{ name: 'cas-shared-default' }] });
      }
      if (p === '/casManagement/servers/cas-shared-default/caslibs') {
        return json(res, 200, { items: [{ name: 'Public' }, { name: 'casuser' }] });
      }
      if (p === '/casManagement/servers/cas-shared-default/caslibs/Public/tables') {
        return json(res, 200, { items: [{ name: 'OPT_DATA' }, { name: 'BIG_DATA' }, { name: 'BAD_COLS' }, { name: 'GHOST' }, { name: 'DEC_CASES' }] });
      }
      // BIG_DATA clears the compare-mode floor of 10 rows (OPT_DATA's 5 rows
      // exercise the too-small path).
      if (p === '/casManagement/servers/cas-shared-default/caslibs/Public/tables/BIG_DATA') {
        return json(res, 200, { name: 'BIG_DATA', rowCount: 12 });
      }
      if (p === '/casManagement/servers/cas-shared-default/caslibs/Public/tables/BIG_DATA/columns') {
        return json(res, 200, { items: [{ name: 'userPrompt' }, { name: 'response' }] });
      }
      if (p === '/casManagement/servers/cas-shared-default/caslibs/casuser/tables') {
        return json(res, 200, { items: [] });
      }
      if (p === '/casManagement/servers/cas-shared-default/caslibs/Public/tables/OPT_DATA') {
        return json(res, 200, { name: 'OPT_DATA', rowCount: 5 });
      }
      if (p === '/casManagement/servers/cas-shared-default/caslibs/Public/tables/OPT_DATA/columns') {
        return json(res, 200, { items: [{ name: 'userPrompt' }, { name: 'response' }] });
      }
      if (p === '/casManagement/servers/cas-shared-default/caslibs/Public/tables/BAD_COLS') {
        return json(res, 200, { name: 'BAD_COLS', rowCount: 5 });
      }
      if (p === '/casManagement/servers/cas-shared-default/caslibs/Public/tables/BAD_COLS/columns') {
        return json(res, 200, { items: [{ name: 'question' }, { name: 'answer' }] });
      }
      if (p === '/casManagement/servers/cas-shared-default/caslibs/Public/tables/DEC_CASES') {
        return json(res, 200, { name: 'DEC_CASES', rowCount: 4 });
      }
      if (p === '/casManagement/servers/cas-shared-default/caslibs/Public/tables/DEC_CASES/columns') {
        return json(res, 200, { items: [{ name: 'subject' }, { name: 'body' }, { name: 'expected_team' }, { name: 'expected_escalate' }] });
      }
      if (p === '/casRowSets/servers/cas-shared-default/caslibs/Public/tables/DEC_CASES/rows') {
        return json(res, 200, { items: [
          { cells: ['Charged twice', 'Please refund one payment.', 'billing', 'no'] },
          { cells: ['Error 502', 'Nothing loads, fix it NOW or we cancel.', 'technical', 'yes'] },
          { cells: ['Quote', 'Price for 50 more seats?', 'sales', 'no'] },
          { cells: ['Hello', 'Just saying hi, no request.', 'unknown', 'no'] },
        ] });
      }
      if (p.startsWith('/casManagement/')) {
        return json(res, 404, { message: 'table not found' });
      }
      // The app primes the Job Execution service session with a GET before its
      // first POST (a first-contact POST 449s on a real Viya's SSO handshake).
      if (p === '/jobExecution/jobs' && req.method === 'GET') {
        return json(res, 200, { items: [] });
      }
      if (p === '/jobExecution/jobs' && req.method === 'POST') {
        if (failNextJob) {
          failNextJob = false;
          failJobPolls = 0;
          return json(res, 201, { id: 'job-fail', state: 'pending' });
        }
        jobLaunched = true;
        jobPolls = 0;
        // Echo the launch configuration into the success entry, like the real
        // job records exactly what it was launched with (lets a verify script
        // assert e.g. a GEPA run's history row shows gepa + its judge model).
        compareArgs = null;
        try {
          const args = JSON.parse(raw).arguments || {};
          if (args.mode === 'compare' || args.mode === 'sweep') compareArgs = args;
          if (args.optimizer) successEntry.optimizer = args.optimizer;
          if (args.metric) successEntry.metric = args.metric;
          successEntry.judgeModel = args.judgeModelName || null;
        } catch {
          /* keep the defaults */
        }
        return json(res, 201, { id: 'job-1', state: 'pending' });
      }
      if (p === '/jobExecution/jobs/job-1' && req.method === 'GET') {
        jobPolls += 1;
        // Real JES jobs expose the compute session/job ids in `results`; the
        // session's log streams LIVE while running (the Files logLocation only
        // fills in at completion).
        const computeResults = { COMPUTE_SESSION: 'cs-1 (SAS Job Execution compute context)', COMPUTE_JOB: 'cj-1' };
        if (jobPolls < 4) return json(res, 200, { id: 'job-1', state: 'running', results: computeResults });
        // The run's tracker entry appears exactly once, like the real job writes it.
        if (!optimizationTracker.some((entry) => entry.jobId === 'job-1')) {
          if (compareArgs) {
            // A compare-mode launch completes into a comparison entry whose
            // candidates echo the request. Qualities are assigned so the
            // SECOND selected name wins - a verify script can assert the
            // ranked table reorders rows instead of keeping request order.
            const names = String(compareArgs.targetModelNames || '')
              .split(',')
              .map((n) => n.trim())
              .filter(Boolean);
            optimizationTracker.push({
              optimizationId: optimizationTracker.length + 1,
              startedAt: '2026-07-25T09:00:00Z',
              finishedAt: '2026-07-25T09:03:00Z',
              status: 'succeeded',
              jobId: 'job-1',
              type: 'comparison',
              targetModel: names.join(', '),
              datasetSource: compareArgs.datasetSource || 'tracker',
              sampleCount: 1,
              // A screening carries no optimizer; a sweep names the one it ran.
              optimizer: compareArgs.mode === 'sweep' ? compareArgs.optimizer || 'bootstrap' : null,
              metric: compareArgs.metric || 'exact',
              judgeModel: compareArgs.judgeModelName || null,
              metricBefore: null,
              metricAfter: null,
              baselinePrompt: { systemPrompt: 'Sys 2', userPrompt: 'User 2' },
              targets: names.map((name, index) => ({
                model: name,
                status: 'scored',
                quality: index === 1 ? 0.9 : 0.4,
                avgLatency: index === 1 ? 1.8 : 0.7,
                usage: { calls: 2, promptTokens: 220, outputTokens: 30, runTime: index === 1 ? 3.6 : 1.4 },
                // Sweep rows additionally carry the per-candidate run — and
                // their evaluations use the before/after shape the real job
                // writes for sweeps (screening rows use the flat shape).
                ...(compareArgs.mode === 'sweep'
                  ? {
                      metricBefore: 0.3,
                      metricAfter: index === 1 ? 0.9 : 0.4,
                      optimizedPrompt: {
                        systemPrompt: `Optimised for ${name}.`,
                        userPrompt: 'User 2',
                        variables: [],
                        demos: [],
                      },
                      evaluations: [
                        {
                          inputs: { userPrompt: 'User 2' },
                          expected: 'Response two',
                          baselineResponse: 'wrong',
                          baselineCorrect: false,
                          baselineScore: 0.3,
                          optimizedResponse: 'Response two',
                          optimizedCorrect: true,
                          optimizedScore: 1,
                        },
                      ],
                    }
                  : {
                      evaluations: [
                        { inputs: { userPrompt: 'User 2' }, expected: 'Response two', response: 'Response two', correct: true, score: 1 },
                      ],
                    }),
                error: null,
              })),
              usage: {
                target: { calls: 2 * names.length, promptTokens: 440, outputTokens: 60, runTime: 5.0 },
                judge: { calls: 0, promptTokens: 0, outputTokens: 0, runTime: 0 },
              },
              error: null,
            });
          } else {
            optimizationTracker.push(successEntry);
          }
        }
        return json(res, 200, { id: 'job-1', state: 'completed', logLocation: '/files/files/joblog-1', results: computeResults });
      }
      if (p === '/compute/sessions/cs-1/jobs/cj-1/log/content') {
        // Live only while the job runs; the session is deleted afterwards, so
        // the app must fall back to the logLocation file. Plain-text CRLF
        // shape as returned by a real compute server (source echo + NOTE per
        // milestone, growing as the job progresses).
        if (jobPolls >= 4) return json(res, 404, { message: 'session not found' });
        res.writeHead(200, { 'Content-Type': 'text/plain; charset=utf-8' });
        const liveLines = [
          '663  %put NOTE: Python-Subprocess - Loading the experiment tracker dataset;',
          'NOTE: Python-Subprocess - Loading the experiment tracker dataset',
          '664  %put NOTE: Python-Subprocess - Dataset loaded (1 examples);',
          'NOTE: Python-Subprocess - Dataset loaded (1 examples)',
          '666  %put NOTE: Python-Subprocess - Scoring the baseline prompt;',
          'NOTE: Python-Subprocess - Scoring the baseline prompt',
        ];
        return res.end(liveLines.slice(0, Math.min(liveLines.length, jobPolls * 2 + 2)).join('\r\n'));
      }
      if (p === '/jobExecution/jobs/job-fail' && req.method === 'GET') {
        failJobPolls += 1;
        if (failJobPolls < 2) return json(res, 200, { id: 'job-fail', state: 'running' });
        // The failed run's tracker entry appears exactly once, like the real job writes it.
        if (!optimizationTracker.some((entry) => entry.jobId === 'job-fail')) {
          optimizationTracker.push({
            optimizationId: optimizationTracker.length + 1,
            startedAt: '2026-07-23T10:00:00Z',
            finishedAt: '2026-07-23T10:00:30Z',
            status: 'failed',
            jobId: 'job-fail',
            targetModel: 'demo_llm',
            datasetSource: 'tracker',
            sampleCount: 0,
            optimizer: 'bootstrap',
            metric: 'exact',
            metricBefore: null,
            metricAfter: null,
            optimizedPrompt: null,
            producedPromptModelId: null,
            error: DSPY_MISSING_ERROR,
          });
        }
        return json(res, 200, {
          id: 'job-fail',
          state: 'failed',
          logLocation: '/files/files/joblog-fail',
          error: { message: 'The job request failed.' },
        });
      }
      if (p === '/files/files/joblog-fail/content') {
        return json(res, 200, {
          version: 2,
          name: 'items',
          accept: 'application/vnd.sas.compute.log.line',
          items: [
            { version: 1, type: 'source', line: '597  %put NOTE: Python-Subprocess - Optimization failed: ' + DSPY_MISSING_ERROR + ';' },
            { version: 1, type: 'note', line: 'NOTE: Python-Subprocess - Optimization failed: ' + DSPY_MISSING_ERROR },
            { version: 1, type: 'error', line: 'ERROR: Prompt optimization failed: ' + DSPY_MISSING_ERROR },
          ],
        });
      }
      if (p === '/files/files/joblog-1/content') {
        // Real shape (verified live): a vnd.sas.compute.log.line collection —
        // one JSON document with items[].line, each milestone preceded by its
        // `%put` source-echo line, which the app must NOT show twice.
        return json(res, 200, {
          version: 2,
          name: 'items',
          accept: 'application/vnd.sas.compute.log.line',
          items: [
            { version: 1, type: 'note', line: 'NOTE: PROC PYTHON started.' },
            { version: 1, type: 'source', line: '597  %put NOTE: Python-Subprocess - Dataset loaded (1 examples);' },
            { version: 1, type: 'note', line: 'NOTE: Python-Subprocess - Dataset loaded (1 examples)' },
            { version: 1, type: 'source', line: '601  %put NOTE: Python-Subprocess - Baseline metric: 0.500;' },
            { version: 1, type: 'note', line: 'NOTE: Python-Subprocess - Baseline metric: 0.500' },
            { version: 1, type: 'source', line: '607  %put NOTE: Python-Subprocess - Done - metric 0.500 -> 0.833, run recorded on the prompt;' },
            { version: 1, type: 'note', line: 'NOTE: Python-Subprocess - Done - metric 0.500 -> 0.833, run recorded on the prompt' },
            { version: 1, type: 'note', line: 'NOTE: PROC PYTHON ended.' },
          ],
        });
      }
      if (p === '/files/files/opttrk-1/content') return json(res, 200, optimizationTracker);
      if (/^\/modelRepository\/models\/(model-free|model-err)\/contents$/.test(p) && req.method === 'GET') {
        return json(res, 200, { items: [] });
      }
      if (p === '/files/files/trk-1/content') return json(res, 200, manifestedTracker ? manifestedTrackerRows : trackerRows);
      if (p === '/relationships/relationships' && req.method === 'POST') {
        let modelId = '';
        try {
          modelId = (JSON.parse(raw).resourceURI[0] || '').split('/').pop();
        } catch {}
        const usage = usageFor[modelId];
        if (usage && usage.status) return json(res, Number(usage.status), { message: 'the relationships service does not answer' });
        if (usage) {
          return json(res, 200, {
            items: [
              { type: 'Parent', resourceUri: '/modelRepository/projects/proj-1' },
              ...usage.flows.flatMap((flow) => [
                { type: 'Dependent', resourceUri: `/decisions/flows/${flow}` },
                { type: 'Dependent', resourceUri: `/decisions/flows/${flow}/revisions/rev-1` },
              ]),
            ],
          });
        }
        if (modelId === 'model-err') return json(res, 500, { message: 'boom' });
        return json(res, 200, relationshipsFor[modelId] || { items: [] });
      }
      if (p === '/decisions/flows/flow-A') return json(res, 200, { id: 'flow-A', name: 'Loan Approval Decision' });
      if (p === '/decisions/flows/flow-B') return json(res, 200, { id: 'flow-B', name: 'Fraud Check Decision' });
      if (/^\/scr\//.test(p) && req.method === 'POST') {
        // A model a suite switched off through /__failmodel does not answer.
        // Its own entry only: a model may be named like a field every object
        // has (constructor, toString - verify-round-trip.js).
        const container = p.split('/')[2];
        const failure = Object.prototype.hasOwnProperty.call(failingModels, container) ? failingModels[container] : undefined;
        if (failure === 'drop') return req.socket.destroy();
        if (failure) return json(res, Number(failure), { message: 'no container answers for ' + p.split('/')[2] });
        // Detect the LLM-as-a-Judge call by its distinctive system prompt.
        let inputs = [];
        try { inputs = JSON.parse(raw).inputs || []; } catch {}
        const stateInput = inputs.find((i) => i.name === 'state');
        if (stateInput) {
          // A decision call: route by the state's words so the expected
          // answers of the fixtures come out right (and one case wrong).
          const state = String(stateInput.value).toLowerCase();
          let questions = {};
          try { questions = JSON.parse((inputs.find((i) => i.name === 'questions') || {}).value || '{}'); } catch {}
          const answers = {};
          Object.entries(questions).forEach(([id, q]) => {
            if (q.type === 'choice') {
              const keys = Object.keys(q.criteria || {});
              const escape = keys.find((k) => ['unknown', 'none', 'other'].includes(k.toLowerCase()));
              const pick = state.includes('refund') || state.includes('charged') ? 'billing'
                : state.includes('error') || state.includes('crash') ? 'technical'
                : state.includes('price') || state.includes('quote') || state.includes('seats') ? 'sales'
                : escape || keys[keys.length - 1];
              const choice = keys.includes(pick) ? pick : keys[0];
              const probabilities = {};
              keys.forEach((k) => { probabilities[k] = k === choice ? 0.9 : 0.1 / Math.max(1, keys.length - 1); });
              answers[id] = { type: 'choice', choice, probabilities, confidence: 0.88 };
            } else if (q.type === 'noul') {
              answers[id] = { type: 'noul', noul: state.includes('cancel') || state.includes('now') ? 0.95 : 0.08 };
            } else {
              const levels = q.criteria || [];
              const probabilities = {};
              levels.forEach((_, i) => { probabilities[String(i)] = i === 1 ? 0.7 : 0.3 / Math.max(1, levels.length - 1); });
              const legend = {};
              levels.forEach((l, i) => { legend[String(i)] = l; });
              answers[id] = { type: 'score', score: 1.1, confidence: 0.6, legend, probabilities };
            }
          });
          const first = Object.values(answers)[0] || {};
          return json(res, 200, {
            data: {
              answers: JSON.stringify(answers),
              answer: first.choice || (first.noul !== undefined ? (first.noul >= 0.5 ? 'yes' : 'no') : String(first.score || '')),
              confidence: first.confidence || (first.noul !== undefined ? Math.max(first.noul, 1 - first.noul) : 0),
              run_time: 0.4, prompt_length: 60, output_length: 0,
            },
          });
        }
        const sys = inputs.find((i) => i.name === 'systemPrompt');
        const sysText = sys ? String(sys.value) : '';
        const isChairman = sysText.includes('chairman of a panel');
        const isJudge = sysText.includes('impartial evaluator') || isChairman;
        if (isChairman) {
          // Break the tie deterministically: pick the tied response whose text
          // sorts first.
          const userInput = String((inputs.find((i) => i.name === 'userPrompt') || {}).value || '');
          const blocks = [
            ...userInput.matchAll(/\[([A-Z]+)\]\n([\s\S]*?)(?=\n\n\[[A-Z]+\]|\n\n==|\n\nReturn the JSON object now\.|$)/g),
          ].map((m) => ({ label: m[1], text: m[2].trim() }));
          blocks.sort((a, b) => a.text.localeCompare(b.text));
          const best = blocks.length ? blocks[0].label : 'A';
          return json(res, 200, {
            data: {
              response: '```json\n' + JSON.stringify({ reasoning: 'Chairman pick by content.', best }) + '\n```',
              run_time: 0.5, prompt_length: 60, output_length: 15,
            },
          });
        }
        if (isJudge) {
          // Deterministic verdict: rank the labelled candidates by response
          // text. A "contrarian" judge reverses the order, so a council that
          // includes it splits (used to exercise the tie/disagreement path).
          const judgeModel = p.split('/')[2] || '';
          const userInput = String((inputs.find((i) => i.name === 'userPrompt') || {}).value || '');
          const blocks = [
            ...userInput.matchAll(/\[([A-Z]+)\]\n([\s\S]*?)(?=\n\n\[[A-Z]+\]|\n\nReturn the JSON object now\.|$)/g),
          ].map((m) => ({ label: m[1], text: m[2].trim() }));
          blocks.sort((a, b) => a.text.localeCompare(b.text));
          if (judgeModel.includes('contrarian')) blocks.reverse();
          const ranking = blocks.map((b) => b.label);
          const verdict = {
            reasoning: `Ranked by response content (${judgeModel}).`,
            ranking,
            best: ranking[0],
            confidence: 'high',
          };
          return json(res, 200, {
            data: {
              response: '```json\n' + JSON.stringify(verdict) + '\n```',
              run_time: 0.8, prompt_length: 120, output_length: 30,
            },
          });
        }
        return json(res, 200, {
          data: {
            response: '```json\n{"sentiment": "positive", "score": 0.9}\n```',
            run_time: 1.5, prompt_length: 42, output_length: 7,
          },
        });
      }
      if (/^\/modelRepository\/models\/[^/]+\/variables$/.test(p) && req.method === 'GET') {
        return json(res, 200, { items: [] });
      }
      if (/^\/modelRepository\/models\/[^/]+$/.test(p) && req.method === 'GET') {
        // Tags include leftovers from an "earlier manifest" to prove cleanup
        const modelId = p.split('/').pop();
        if (deletedModels.has(modelId)) return json(res, 404, { message: 'no such model' });
        res.writeHead(200, { 'Content-Type': 'application/json', ETag: '"abc123"' });
        if (modelId === 'model-dec') {
          // TLS-Verification-Off: left by an earlier manifest with the option
          // off; a manifest with verification on has to take it away.
          return res.end(JSON.stringify({ id: modelId, name: 'Ticket Routing', algorithm: 'Decision-Template',
            tags: ['Decision', 'Prompt-Template', 'Decision-Template', 'TLS-Verification-Off'] }));
        }
        if (modelId === 'dec-1') {
          return res.end(JSON.stringify({ id: modelId, name: 'jev_mock', provider: 'OpenRouter', inputTokenCount: 0.000000042,
            tags: ['Decision', 'Proprietary', 'OpenRouter', 'small'],
            properties: [
              { name: 'contextLength', value: '32000', type: 'string' },
              { name: 'maxOptions', value: '255', type: 'string' },
              { name: 'questionTypes', value: 'choice,noul,score', type: 'string' },
            ] }));
        }
        if (modelId === 'dec-2') {
          return res.end(JSON.stringify({ id: modelId, name: 'von_mock', provider: 'Von', hostingCosts: 0.0001,
            tags: ['Decision', 'Open-Source', 'Apache-2', 'Von', 'medium'],
            properties: [
              { name: 'contextLength', value: '8192', type: 'string' },
              { name: 'maxOptions', value: '6', type: 'string' },
              { name: 'questionTypes', value: 'choice,noul', type: 'string' },
            ] }));
        }
        return res.end(
          JSON.stringify({
            id: modelId,
            name: 'Used Prompt',
            tags: ['LLM', 'Prompt-Template', 'Custom-Tag', 'other_llm', 'LLM-Call-Included', 'TLS-Verification-Off'],
          })
        );
      }
      if (/^\/modelRepository\/models\/[^/]+$/.test(p) && req.method === 'PUT') {
        return json(res, 200, {});
      }
      if (p === '/modelRepository/models/model-used/contents/c-opttrk' && req.method === 'DELETE') {
        optTrackerDeleted = true;
        res.writeHead(204);
        return res.end();
      }
      if (req.method === 'DELETE') { res.writeHead(204); return res.end(); }
      if (/^\/modelRepository\/models\/[^/]+\/modelVersions$/.test(p) && req.method === 'POST') {
        return json(res, 200, { id: 'v2' });
      }
      if (/^\/modelRepository\/models\/[^/]+\/contents$/.test(p) && req.method === 'POST') {
        // A re-uploaded optimization tracker (run deletion) replaces the state.
        if (raw.includes('filename="Prompt-Optimization-Tracker.json"')) {
          const replacement = multipartJson(raw);
          if (Array.isArray(replacement)) {
            optimizationTracker.length = 0;
            optimizationTracker.push(...replacement);
            optTrackerDeleted = false;
          }
        }
        return json(res, 201, { id: 'c-new' });
      }
      console.log('not mocked: ' + req.method + ' ' + req.url);
      json(res, 404, { message: 'not mocked: ' + req.method + ' ' + p });
    });
  })
  .listen(PORT, () => console.log('mock listening on ' + PORT));
