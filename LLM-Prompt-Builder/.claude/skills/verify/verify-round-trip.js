/**
 * Every stored JSON file the page reads is read back as each of its writers
 * writes it (round 25). Run against a FRESH mock instance.
 *
 * A check that refuses a file is only right if every writer of that file
 * passes it. The page refused trackers it had written itself - a model
 * registered as `constructor` or `toString` was run, saved, and opened again
 * as a tracker that "holds no runs this page can read"; one registered as
 * `mode` overwrote that field of its run -, and it took a rag-setup.json with
 * a documentation field that held an object for a setup and wrote
 * "[object Object]" back.
 *
 * The censuses (from the code, so a writer that is added fails them until it
 * is named here and has its round trip):
 *   - PAGE_WRITERS: every call of createModelContent in a .ts file under src,
 *     by the function it stands in and the name of the file it uploads; each
 *     that uploads a name of STORED_JSON_FILES (src/api/stored-json.ts) has a
 *     road below that writes it and reads it back;
 *   - OTHER_CODE: every file of program code of the repository outside the
 *     page (.sas, .py, .step, .ps1, .sh, .js, .cjs, .ts; not the tests, not
 *     the page, not node_modules nor a folder whose name starts with a dot)
 *     that names one of those files in quotes, with what it does with it; a
 *     writer has its round trip below;
 *   - every name of STORED_JSON_FILES has a writer, and every writer's round
 *     trip ran.
 * The round trips, each written by its writer and read back by the check of
 * the page (on the page served by the mock, and with src/api/stored-json.ts
 * itself):
 *   - Prompt-Experiment-Tracker.json, Save Experiments: a run of an LLM of an
 *     ordinary name and of one named like every field Object.prototype has
 *     (one of them answering nothing, judged by a council), saved, opened
 *     again - the tracker is taken, every model's result is drawn - and saved
 *     again; the same for a decision model named constructor. A model named
 *     like a key the page reads as a field of a run (TRACKER_META_KEYS) or
 *     __proto__ is not offered: its box is off, with a note that gives the
 *     reason true for each of these names, and it is offered nowhere else;
 *   - rag-setup.json, Save setup, Save documentation and Manifest setup: a
 *     setup whose every field the form holds was changed, with an enrichment
 *     of two prompts, and a stored setup with null in every field that is not
 *     required (and in every text, number and box), each saved, read back by
 *     the check and opened again;
 *   - Prompt-Optimization-Tracker.json: the delete of an optimization run by
 *     the page, and the optimization job (Optimize-Prompt-DSPy.sas, run by
 *     optimize-job-writer.py with stand-ins for SAS, SAS Viya and dspy) in
 *     every way it ends;
 *   - options.json: every options.json of LLM-Definitions and
 *     Decision-Definitions, which mdb generated and uploads.
 *
 * The suite goes on after a check that failed and after a road that stopped.
 * The mock is found at VERIFY_BASE (default http://localhost:4173); the
 * sources at VERIFY_PROJECT (default: the project around this folder), read
 * with the parser of rolldown from its node_modules (`npm ci` first); the
 * repository at MOCK_REPO_ROOT (default: the folder above the project).
 */
const { chromium } = require('playwright');
const fetch = require('./mock-fetch.cjs');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');
const { PROJECT, parse, walk, sourceFiles, loadStoredJson } = require('./source-census.cjs');
const { resolvePython } = require('./python-check.cjs');

const BASE = (process.env.VERIFY_BASE || 'http://localhost:4173').replace(/\/+$/, '');
const REPOSITORY = path.resolve(process.env.MOCK_REPO_ROOT || path.join(PROJECT, '..'));
const results = [];

function step(ok, label) {
  results.push(`${ok ? 'OK ' : 'FAIL'}  ${label}`);
  console.log(`${ok ? 'OK ' : 'FAIL'}  ${label}`);
  if (!ok) process.exitCode = 1;
}
const getLog = async () => (await fetch(BASE + '/__log')).json();
const resetLog = async () => fetch(BASE + '/__reset', { method: 'POST' });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitUntil(fn, label, timeout = 15000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await fn()) return;
    await sleep(100);
  }
  throw new Error('timeout waiting for: ' + label);
}
const clip = (value) => (String(value).length > 160 ? `${String(value).slice(0, 160)}...` : String(value));

const APP_URL =
  `${BASE}/?modelRepositoryID=repo-1&llmProjectID=llm-proj&SCREndpoint=${BASE}/scr` +
  '&enableOptimization=true&computeContext=ctx&optimizeJobProgram=/Public/Jobs/Optimize-Prompt-DSPy&minOptimizeSamples=1' +
  '&enableRag=true&embeddingProjectID=emb-proj&ragComputeContext=rag-ctx';
const lpb = (suffix) => `#app-obj-LPB-${suffix}`;
const rgb = (suffix) => `#app-obj-RGB-${suffix}`;

/** The file of a multipart upload the page sent: its name and its text. */
function multipartFile(body) {
  const boundary = (/^(--[^\r\n]+)\r\n/.exec(body || '') || [])[1];
  const head = /filename="([^"]*)"\r\n(?:Content-Type: [^\r\n]*\r\n)?\r\n/.exec(body || '');
  if (!boundary || !head) return null;
  const start = head.index + head[0].length;
  const end = body.indexOf('\r\n' + boundary, start);
  return { name: head[1], text: body.slice(start, end < 0 ? undefined : end) };
}
const uploadsOf = (log, model, name) =>
  log
    .filter((entry) => entry.method === 'POST' && entry.url.startsWith(`/modelRepository/models/${model}/contents`) && entry.status !== null && entry.status < 300)
    .map((entry) => multipartFile(entry.body))
    .filter((file) => file && file.name === name);
const parsed = (text) => {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
};

// ============================================================================
// The writers, from the code
// ============================================================================
/**
 * Every call of createModelContent in src, by "<file> <function>", with the
 * names of the files it uploads. A name that is no text the census can read
 * (a variable) is "(computed)" and has to be named here with what it is.
 */
const PAGE_WRITERS = {
  'objects/prompt-builder.ts storeManifestScoreCode': { '(computed)': 'the score code of a manifested model (<name>.py), no JSON file' },
  'objects/prompt-builder.ts promptBuilderSaveExperiments': { 'Prompt-Experiment-Tracker.json': 'Save Experiments - the roads of the names of the models' },
  'objects/prompt-builder.ts promptBulderCreateBestPromptModel': { 'inputVar.json': 'not read by the page', 'outputVar.json': 'not read by the page', 'requirements.json': 'not read by the page' },
  'objects/prompt-builder.ts promptBuilderCreateDecisionModel': { 'inputVar.json': 'not read by the page', 'outputVar.json': 'not read by the page', 'requirements.json': 'not read by the page' },
  'objects/prompt-builder.ts deleteOptimizationRun': { 'Prompt-Optimization-Tracker.json': 'the delete of an optimization run - its road' },
  'objects/rag-builder.ts saveDocumentation': { 'rag-setup.json': 'Save documentation - the roads of a setup' },
  'objects/rag-builder.ts saveSetup': { 'rag-setup.json': 'Save setup - the roads of a setup', 'pipeline.yaml': 'not read by the page' },
  'objects/rag-builder.ts generateIngestionJob': { 'rag-setup.json': 'Manifest setup - the road of a manifest' },
  'objects/rag-builder.ts generateStudioFlow': { '(computed)': 'the Studio flow (<name>.flw), not read by the page' },
  'objects/rag-builder.ts registerRetrievalModel': { 'retrieve_context.py': 'not read by the page' },
};

/**
 * Every file of program code outside the page that names a stored file in
 * quotes, with what it does with each: 'writes' has a round trip below.
 */
const OTHER_CODE = {
  'SAS-Viya-Integrations/Prompt-Optimization/Optimize-Prompt-DSPy.sas': {
    'Prompt-Optimization-Tracker.json': ['writes', 'the optimization job appends the entry of every run - the road of the job'],
    'Prompt-Experiment-Tracker.json': ['reads', 'the dataset of the job, and the name it records as the source of the dataset'],
    'options.json': ['reads', 'the options of the target and of the judge'],
  },
  'Model-Definition-Builder/cli/src/mdb/core/generator.py': { 'options.json': ['writes', 'renders the options.json of a definition - every options.json of the repository'] },
  'Model-Definition-Builder/cli/src/mdb/viya/registry.py': { 'options.json': ['writes', 'uploads the options.json of a definition folder - every options.json of the repository'] },
  'Model-Definition-Builder/cli/src/mdb/core/importer.py': { 'options.json': ['reads', 'imports a definition folder'] },
  'Model-Definition-Builder/cli/src/mdb/core/releases.py': { 'options.json': ['names', 'a word that sorts a change into a part of the release notes'] },
  'SAS-Viya-Integrations/Logging-Monitoring/Get-All-Prompts.sas': {
    'Prompt-Experiment-Tracker.json': ['reads', 'the prompts table'],
    'Prompt-Optimization-Tracker.json': ['reads', 'the optimizations table'],
    'rag-setup.json': ['reads', 'the RAG setups table'],
  },
  'SAS-Viya-Integrations/Custom-Steps/LLM - Get All Prompts.step': {
    'Prompt-Experiment-Tracker.json': ['reads', 'the program of Get-All-Prompts.sas'],
    'Prompt-Optimization-Tracker.json': ['reads', 'the program of Get-All-Prompts.sas'],
    'rag-setup.json': ['reads', 'the program of Get-All-Prompts.sas'],
  },
  'SAS-Viya-Integrations/Custom-Steps/LLM - Get Prompt Experiments.step': { 'Prompt-Experiment-Tracker.json': ['reads', 'into a table'] },
  'SAS-Viya-Integrations/Custom-Steps/LLM - List Available LLMs.step': { 'options.json': ['reads', 'into a table'] },
  'SAS-Viya-Integrations/SAS-Code-LLM-Calls/Get-List-of-available-LLMs.sas': { 'options.json': ['reads', 'into a table'] },
  'SAS-Viya-Integrations/SAS-Code-LLM-Calls/Get-Prompt-Experiments.sas': { 'Prompt-Experiment-Tracker.json': ['reads', 'into a table'] },
  'SAS-Viya-Integrations/SAS-Code-LLM-Calls/Track-Prompt-Experiments.sas': {
    'Prompt-Experiment-Tracker.json': ['reads', 'reads it and deletes it; what it uploads is named Prompt-Example-Tracker.json, a file the page does not read'],
  },
  'SAS-Viya-Integrations/RAG/rag_core/providers.py': { 'options.json': ['reads', 'the provider key of an embedding model'] },
};
const CODE_EXTENSIONS = /\.(sas|py|step|ps1|sh|js|cjs|ts)$/;
const SKIPPED_FOLDERS = new Set(['node_modules', 'tests', 'LLM-Prompt-Builder', 'website', 'dist', 'site-packages']);

function codeFiles(root) {
  const found = [];
  (function into(folder) {
    for (const entry of fs.readdirSync(folder, { withFileTypes: true })) {
      if (entry.isDirectory()) {
        if (!entry.name.startsWith('.') && !SKIPPED_FOLDERS.has(entry.name)) into(path.join(folder, entry.name));
      } else if (CODE_EXTENSIONS.test(entry.name)) {
        found.push(path.join(folder, entry.name));
      }
    }
  })(root);
  return found;
}

/** What ran of each writer's round trip: "<file> <writer>" -> true when it ran and was read back. */
const roundTrips = new Map();
const tripped = (key, ok) => roundTrips.set(key, Boolean(ok) && roundTrips.get(key) !== false);

function writersCensus(storedNames) {
  // The page.
  const found = {};
  for (const [file, text] of Object.entries(sourceFiles())) {
    const { program, source } = parse(file, text);
    const texts = {};
    walk(program, source, (node) => {
      if (node.type === 'VariableDeclarator' && node.id.type === 'Identifier' && node.init && node.init.type === 'Literal' && typeof node.init.value === 'string') {
        texts[node.id.name] = node.init.value;
      }
    });
    walk(program, source, (node, name) => {
      if (node.type !== 'CallExpression' || node.callee.type !== 'Identifier' || node.callee.name !== 'createModelContent') return;
      const argument = node.arguments[2];
      const fileName =
        argument && argument.type === 'Literal' && typeof argument.value === 'string' ? argument.value
          : argument && argument.type === 'Identifier' && texts[argument.name] ? texts[argument.name]
            : '(computed)';
      const key = `${file} ${name || '(module)'}`;
      (found[key] = found[key] || new Set()).add(fileName);
    });
  }
  const differences = [];
  for (const [key, names] of Object.entries(found)) {
    for (const fileName of names) if (!PAGE_WRITERS[key] || !PAGE_WRITERS[key][fileName]) differences.push(`${key} uploads ${fileName}, not named here`);
  }
  for (const [key, names] of Object.entries(PAGE_WRITERS)) {
    for (const fileName of Object.keys(names)) if (!found[key] || !found[key].has(fileName)) differences.push(`${key} is named with ${fileName}, which it does not upload`);
  }
  const pageWriters = [];
  for (const [key, names] of Object.entries(PAGE_WRITERS)) {
    for (const fileName of Object.keys(names)) if (storedNames.includes(fileName)) pageWriters.push({ key: `${fileName} ${key}`, fileName });
  }
  step(
    Object.keys(found).length > 0 && differences.length === 0,
    `every upload of the page is named with the file it writes (${Object.keys(found).length} functions in the ${Object.keys(sourceFiles()).length} source files; stored JSON files among them: ${pageWriters.map((writer) => writer.key).join('; ')}; differences: ${differences.join('; ') || 'none'})`
  );

  // The rest of the repository.
  const named = {};
  const files = codeFiles(REPOSITORY);
  for (const file of files) {
    const text = fs.readFileSync(file, 'utf8');
    const names = storedNames.filter((fileName) => new RegExp(`\\\\?['"]${fileName.replace(/[.]/g, '\\.')}\\\\?['"]`).test(text));
    if (names.length) named[path.relative(REPOSITORY, file).split(path.sep).join('/')] = names;
  }
  const otherDifferences = [];
  for (const [file, names] of Object.entries(named)) {
    for (const fileName of names) if (!OTHER_CODE[file] || !OTHER_CODE[file][fileName]) otherDifferences.push(`${file} names ${fileName}, not named here`);
  }
  for (const [file, names] of Object.entries(OTHER_CODE)) {
    for (const fileName of Object.keys(names)) if (!named[file] || !named[file].includes(fileName)) otherDifferences.push(`${file} is named with ${fileName}, which it does not name`);
  }
  const otherWriters = [];
  for (const [file, names] of Object.entries(OTHER_CODE)) {
    for (const [fileName, [role]] of Object.entries(names)) if (role === 'writes') otherWriters.push({ key: `${fileName} ${file}`, fileName });
  }
  step(
    files.length > 0 && otherDifferences.length === 0,
    `every file of program code of the repository outside the page that names a stored JSON file is named with what it does with it (${files.length} files read, ${Object.keys(named).length} name one; writers: ${otherWriters.map((writer) => writer.key).join('; ')}; differences: ${otherDifferences.join('; ') || 'none'})`
  );
  const writers = [...pageWriters, ...otherWriters];
  const without = storedNames.filter((fileName) => !writers.some((writer) => writer.fileName === fileName));
  step(without.length === 0, `every JSON file the page reads has a writer (files: ${storedNames.join(', ')}; without one: ${without.join(', ') || 'none'})`);
  return writers;
}

(async () => {
  let stored = null;
  try {
    stored = await loadStoredJson();
  } catch (error) {
    step(false, `the checks of src/api/stored-json.ts can be read (${error.message}; run npm ci)`);
  }
  const storedNames = stored ? Object.keys(stored.STORED_JSON_FILES) : [];
  const check = (fileName, value) => Boolean(stored && stored.STORED_JSON_FILES[fileName](value));
  let writers = [];
  try {
    writers = writersCensus(storedNames);
  } catch (error) {
    step(false, `the writers of the stored files are known to this suite (${error.message}; set VERIFY_PROJECT, run npm ci)`);
  }

  // ==========================================================================
  // options.json: what mdb generated and uploads
  // ==========================================================================
  {
    const read = [];
    const refused = [];
    for (const folder of ['LLM-Definitions', 'Decision-Definitions']) {
      const root = path.join(REPOSITORY, folder);
      if (!fs.existsSync(root)) continue;
      for (const definition of fs.readdirSync(root)) {
        const file = path.join(root, definition, 'options.json');
        if (!fs.existsSync(file)) continue;
        read.push(`${folder}/${definition}`);
        if (!check('options.json', parsed(fs.readFileSync(file, 'utf8')))) refused.push(`${folder}/${definition}`);
      }
    }
    const ok = read.length > 0 && refused.length === 0;
    for (const writer of ['Model-Definition-Builder/cli/src/mdb/core/generator.py', 'Model-Definition-Builder/cli/src/mdb/viya/registry.py']) tripped(`options.json ${writer}`, ok);
    step(ok, `every options.json mdb generated for the repository - and uploads as it is - passes the check of the page (${read.length} read; refused: ${refused.join(', ') || 'none'})`);
  }

  // ==========================================================================
  // Prompt-Optimization-Tracker.json: the optimization job
  // ==========================================================================
  let jobTracker = null;
  {
    const writer = 'Prompt-Optimization-Tracker.json SAS-Viya-Integrations/Prompt-Optimization/Optimize-Prompt-DSPy.sas';
    const python = resolvePython();
    const job = path.join(REPOSITORY, 'SAS-Viya-Integrations', 'Prompt-Optimization', 'Optimize-Prompt-DSPy.sas');
    const script = path.join(PROJECT, '.claude', 'skills', 'verify', 'optimize-job-writer.py');
    const out = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'optimize-job-')), 'out.json');
    const run = python
      ? spawnSync(python.command, [...python.args, '-I', script, job, out], { encoding: 'utf8', timeout: 120000 })
      : { status: null, stderr: 'no Python interpreter found (set VERIFY_PYTHON)' };
    const runs = run.status === 0 ? (parsed(fs.readFileSync(out, 'utf8')) || {}).runs || [] : [];
    const wrong = runs.filter((entry) => entry.outcome.raised || !Array.isArray(entry.tracker) || !check('Prompt-Optimization-Tracker.json', entry.tracker));
    const last = runs.length ? runs[runs.length - 1].tracker : null;
    const ended = runs.map((entry) => `${entry.name}: ${entry.outcome.rc === '0' ? 'succeeded' : `failed (${clip(entry.outcome.error)})`}`);
    const ok = run.status === 0 && runs.length >= 10 && wrong.length === 0 && Array.isArray(last) && last.length === runs.length;
    tripped(writer, ok);
    if (ok) jobTracker = last;
    step(
      ok,
      `the optimization job writes a Prompt-Optimization-Tracker.json the check of the page takes after every way it ends - ${runs.length} runs appended to one tracker (${ended.join('; ') || clip(run.stderr || run.error || 'it did not run')}; refused: ${wrong.map((entry) => entry.name).join('; ') || 'none'})`
    );
  }

  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  let pageErrors = [];
  const road = async (name, run) => {
    console.log(`---- ${name}`);
    pageErrors = [];
    const page = await browser.newPage({ viewport: { width: 1500, height: 1100 } });
    page.setDefaultNavigationTimeout(180000);
    page.on('pageerror', (e) => {
      pageErrors.push(e.message);
      console.log('[pageerror]', e.message);
    });
    page.on('dialog', (d) => d.accept().catch(() => {}));
    try {
      await run(page);
    } catch (error) {
      step(false, `${name}: the road stopped (${String(error.message).split('\n')[0]})`);
    } finally {
      await page.close();
    }
  };
  const atRest = async () => {
    let seen = -1;
    for (let round = 0; round < 80; round++) {
      const log = await getLog();
      const open = log.filter((entry) => entry.status === null).length;
      if (open === 0 && log.length === seen) return;
      seen = log.length;
      await sleep(250);
    }
    throw new Error('the page did not come to rest');
  };
  const goStep = async (page, prefix, key) => {
    await page.click(`#app-obj-${prefix}-step-${key}`);
    await page.waitForSelector(`#app-obj-${prefix}-pane-${key}:not([hidden])`);
  };
  const textOf = async (page, selector) => ((await page.textContent(selector)) || '').replace(/\s+/g, ' ').trim();
  const listed = async (page, select, value) => (await page.$$eval(`${select} option`, (options) => options.map((option) => option.value))).includes(value);
  const choose = async (page, prefix, select, value) => {
    await goStep(page, prefix, 'setup');
    await waitUntil(() => listed(page, select, value), `${value} is listed in ${select}`);
    await page.selectOption(select, value);
    await atRest();
  };
  const openPage = async (page, kind = 'llm') => {
    await page.goto(APP_URL);
    await page.waitForSelector('#LPB-project-dropdown', { timeout: 20000 });
    await atRest();
    if (kind === 'decision') {
      await page.click('#LPB-kind-decision');
      await atRest();
    }
    if (kind === 'rag') {
      await page.click('#LPB-kind-rag');
      await page.waitForSelector('#app-area-rag:not([hidden]) #app-obj-RGB-project', { timeout: 20000 });
      await atRest();
    }
  };
  const savedRuns = (page) => page.$$eval(`${lpb('pet')} .pet-run-delete`, (buttons) => buttons.length);
  const saveExperiments = async (page) => {
    await goStep(page, 'LPB', 'build');
    await resetLog();
    await page.click(lpb('pet-save-button'));
    await page.waitForTimeout(300);
    await waitUntil(async () => (await textOf(page, lpb('pet-save-button'))) === 'Save Experiments', 'the save has ended', 30000);
    await atRest();
    return getLog();
  };

  // Models added to the listing of a project of models, each with an
  // options.json of its own.
  const addModels = async (page, project, models, options = '{"temperature":{"default":0.5}}') => {
    await page.route(`**/modelRepository/projects/${project}/models**`, async (route) => {
      if (route.request().method() !== 'GET') return route.continue();
      const answer = await route.fetch();
      const body = await answer.json();
      if (!(new URL(route.request().url()).searchParams.get('filter') || '').includes('deprecated')) {
        body.items = [...(body.items || []), ...models.map(([id, name]) => ({ id, name }))];
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    });
    await page.route('**/modelRepository/models/rt-*/contents**', (route) =>
      route.request().method() === 'GET'
        ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [{ id: 'c-rt-opt', name: 'options.json', fileUri: '/files/files/rt-opt' }] }) })
        : route.continue()
    );
    await page.route('**/files/files/rt-opt/content', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: options }));
  };
  // The containers of the models `names` answer "answer <n> of the run", n the
  // place of the name in `names` - a response that holds __lookupGetter__ is
  // drawn as markdown -, and those in `silent` answer nothing.
  const answerOf = (names, name) => `answer ${names.indexOf(name)} of the run`;
  const answerModels = async (page, names, silent = []) => {
    await page.route('**/scr/**', (route) => {
      const model = decodeURIComponent(new URL(route.request().url()).pathname.split('/')[2] || '');
      if (!names.includes(model)) return route.continue();
      const response = silent.includes(model) ? '' : answerOf(names, model);
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ data: { response, run_time: 1.2, prompt_length: 10, output_length: 3 } }) });
    });
  };
  const boxOf = (page, container, name) =>
    page.evaluate(([where, model]) => {
      const box = [...document.querySelectorAll(`${where} input[type=checkbox]`)].find((input) => input.value === model);
      const holder = box && (box.closest('.pb-model-card') || box.parentElement);
      return box ? { id: box.id, off: box.disabled, note: (holder.querySelector('.pb-name-note')?.textContent || '').trim() } : null;
    }, [container, name]);
  // The note on a model a run cannot keep a result under, with the reason
  // that is true for every such name (author is no field any run holds, and
  // __proto__ is no field of a run at all).
  const NAME_NOTE = 'This model cannot be used here: a run keeps the result of each model under the name of the model';
  const nameNote = (name) => `${NAME_NOTE}, and a run cannot keep the result of a model under the name ${name}. Register the model under another name to use it here.`;

  // ==========================================================================
  // Prompt-Experiment-Tracker.json: Save Experiments, every class of name
  // ==========================================================================
  const prototypeNames = Object.getOwnPropertyNames(Object.prototype).filter((name) => name !== '__proto__');
  const runNames = ['ordinary_llm', ...prototypeNames];
  // A model the council does not rank - it answered nothing - named like a
  // field every object inherits.
  const SILENT = 'valueOf';
  {
    const writer = 'Prompt-Experiment-Tracker.json objects/prompt-builder.ts promptBuilderSaveExperiments';
    await road('a run of LLMs named like every field an object inherits, saved and opened again', async (page) => {
      await addModels(page, 'llm-proj', runNames.map((name, index) => [`rt-${index}`, name]));
      await answerModels(page, runNames, [SILENT]);
      await openPage(page);
      await choose(page, 'LPB', '#LPB-project-dropdown', 'proj-1');
      await choose(page, 'LPB', '#LPB-prompt-dropdown', 'model-free');
      await goStep(page, 'LPB', 'build');
      await page.fill(lpb('user-prompt'), 'A question for every name');
      const boxes = [];
      for (const name of runNames) boxes.push([name, await boxOf(page, '#LPB-model-selector-container', name)]);
      const offered = boxes.filter(([, box]) => box && !box.off);
      step(
        offered.length === runNames.length,
        `an LLM of an ordinary name and one named like each field an object inherits are offered (${offered.length} of ${runNames.length}; off or missing: ${boxes.filter(([, box]) => !box || box.off).map(([name]) => name).join(', ') || 'none'})`
      );
      for (const [, box] of offered) await page.check(`#${box.id}`);
      await page.click(lpb('run-experiment'));
      await waitUntil(async () => (await page.$$(`${lpb('pet')} .accordion-item`)).length > 0, 'the run is drawn', 60000);
      await waitUntil(async () => (await textOf(page, lpb('run-experiment'))) === 'Run Experiments', 'the run has ended', 60000);
      await atRest();
      const drawn = async () =>
        page.evaluate((names) => names.map((name) => {
          const nested = [...document.querySelectorAll('[id$="-run-nested-' + name + '"]')].find((element) => /-pet-\d+-run-nested-/.test(element.id));
          return [name, nested ? nested.textContent : null];
        }), runNames);
      const missing = (pairs) => pairs.filter(([name, text]) => text === null || (name !== SILENT && !text.includes(answerOf(runNames, name)))).map(([name]) => name);
      const first = await drawn();
      step(missing(first).length === 0, `the result of every model of the run is drawn (not drawn: ${missing(first).join(', ') || 'none'})`);

      // A council judges the run; the model that answered nothing has no rank.
      await page.selectOption(lpb('judge-model'), 'demo_llm');
      await page.check(lpb('judge-council-toggle'));
      for (const [id, member] of await page.$$eval(`${lpb('judge-panel')} input.pb-judge-panel-item`, (inputs) => inputs.map((input) => [input.id, input.value]))) {
        await page.setChecked(`#${id}`, ['demo_llm', 'second_llm'].includes(member));
      }
      await page.click(lpb('pet-0-judge'));
      await waitUntil(async () => Boolean(await page.$(`${lpb('pet-0')} .pb-judge-banner`)), 'the judgment is shown', 30000);
      await atRest();
      const silentText = (await drawn()).find(([name]) => name === SILENT)[1] || '';

      const log = await saveExperiments(page);
      const upload = uploadsOf(log, 'model-free', 'Prompt-Experiment-Tracker.json').pop();
      const rows = upload ? parsed(upload.text) : undefined;
      const rowOf = (name) => (rows || []).find((row) => row.model === name);
      const unsaved = runNames.filter((name) => !rowOf(name) || rowOf(name).response !== (name === SILENT ? '' : answerOf(runNames, name)));
      const silentRow = rowOf(SILENT);
      step(
        Array.isArray(rows) && check('Prompt-Experiment-Tracker.json', rows) && unsaved.length === 0,
        `Save Experiments writes a tracker the check of the page takes, with the result of every model (rows: ${Array.isArray(rows) ? rows.length : 'none uploaded'}; taken by the check: ${check('Prompt-Experiment-Tracker.json', rows)}; models without their result: ${unsaved.join(', ') || 'none'})`
      );
      step(
        Boolean(silentRow) && Object.prototype.hasOwnProperty.call(silentRow, 'judge_rank') && silentRow.judge_rank === null && !/function|native code/.test(silentText),
        `the model named ${SILENT} that answered nothing has no rank of the council: drawn without one, saved with judge_rank null (saved: ${silentRow ? JSON.stringify(silentRow.judge_rank) : 'no row'}${silentRow && !Object.prototype.hasOwnProperty.call(silentRow, 'judge_rank') ? ' (left out)' : ''}; drawn: "${clip(silentText.replace(/\s+/g, ' '))}")`
      );

      // Opened again, with the file that was written.
      await page.route('**/modelRepository/models/model-free/contents**', (route) =>
        route.request().method() === 'GET'
          ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [{ id: 'c-rt-trk', name: 'Prompt-Experiment-Tracker.json', fileUri: '/files/files/rt-trk' }] }) })
          : route.continue()
      );
      await page.route('**/files/files/rt-trk/content', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: upload ? upload.text : '[]' }));
      await choose(page, 'LPB', '#LPB-prompt-dropdown', 'model-used');
      await choose(page, 'LPB', '#LPB-prompt-dropdown', 'model-free');
      await waitUntil(async () => (await savedRuns(page)) > 0, 'the run is drawn', 15000).catch(() => {});
      const line = await textOf(page, lpb('pet-save-only-result'));
      const again = await drawn();
      step(
        (await savedRuns(page)) === 1 && line === '' && missing(again).length === 0 && Boolean(await page.$(`${lpb('pet-0')} .pb-judge-banner`)),
        `opened again, the tracker is taken and the result of every model is drawn, with the judgment (runs: ${await savedRuns(page)}; line: "${clip(line)}"; not drawn: ${missing(again).join(', ') || 'none'})`
      );
      const log2 = await saveExperiments(page);
      const upload2 = uploadsOf(log2, 'model-free', 'Prompt-Experiment-Tracker.json').pop();
      const rows2 = upload2 ? parsed(upload2.text) : undefined;
      const unsaved2 = runNames.filter((name) => !(rows2 || []).some((row) => row.model === name));
      const ok = Array.isArray(rows2) && check('Prompt-Experiment-Tracker.json', rows2) && unsaved2.length === 0 && pageErrors.length === 0;
      step(ok, `saved again, the tracker is written with every model of the run (rows: ${Array.isArray(rows2) ? rows2.length : 'none uploaded'}; models left out: ${unsaved2.join(', ') || 'none'}; page errors: ${pageErrors.join(' | ') || 'none'})`);
      tripped(writer, ok && unsaved.length === 0 && missing(again).length === 0);
    });

    // A decision model named like a field every object inherits.
    await fetch(BASE + '/__decision', { method: 'POST' });
    await road('a decision run of a model named constructor, saved and opened again', async (page) => {
      await addModels(page, 'dec-proj', [['rt-dec-1', 'constructor'], ['rt-dec-2', 'mode']], '{}');
      await openPage(page, 'decision');
      await choose(page, 'LPB', '#LPB-project-dropdown', 'proj-2');
      await choose(page, 'LPB', '#LPB-prompt-dropdown', 'model-dec');
      await goStep(page, 'LPB', 'build');
      const box = await boxOf(page, '#LPB-decision-model-selector-container', 'constructor');
      const reserved = await boxOf(page, '#LPB-decision-model-selector-container', 'mode');
      step(
        Boolean(box) && !box.off && Boolean(reserved) && reserved.off && reserved.note === nameNote('mode'),
        `a decision model named constructor is offered, one named mode is not, with a note that says why (constructor: ${box ? (box.off ? 'off' : 'on') : 'missing'}; mode: ${reserved ? `${reserved.off ? 'off' : 'on'}, note "${clip(reserved.note)}"` : 'missing'})`
      );
      const before = await savedRuns(page);
      if (box) await page.check(`#${box.id}`);
      await page.click(lpb('run-experiment'));
      await waitUntil(async () => (await savedRuns(page)) > before, 'the run is drawn', 60000);
      await waitUntil(async () => !(await page.isDisabled(lpb('run-experiment'))), 'the run has ended', 60000);
      await atRest();
      const drawnHere = () => page.evaluate(() => [...document.querySelectorAll('[id$="-run-nested-constructor"]')].filter((element) => /-pet-\d+-run-nested-constructor$/.test(element.id)).length);
      const drawnFirst = await drawnHere();
      const log = await saveExperiments(page);
      const upload = uploadsOf(log, 'model-dec', 'Prompt-Experiment-Tracker.json').pop();
      const rows = upload ? parsed(upload.text) : undefined;
      const saved = (rows || []).filter((row) => row.model === 'constructor' && row.response);
      step(
        drawnFirst === 1 && Array.isArray(rows) && check('Prompt-Experiment-Tracker.json', rows) && saved.length === 1,
        `the result of the decision model named constructor is drawn and saved, in a tracker the check of the page takes (drawn: ${drawnFirst}; rows of it saved: ${saved.length}; taken by the check: ${check('Prompt-Experiment-Tracker.json', rows)})`
      );
      await page.route('**/files/files/dtrk-1/content', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: upload ? upload.text : '[]' }));
      await page.selectOption('#LPB-prompt-dropdown', { index: 0 }).catch(() => {});
      await atRest();
      await choose(page, 'LPB', '#LPB-prompt-dropdown', 'model-dec');
      await waitUntil(async () => (await drawnHere()) === 1, 'the run of constructor is drawn', 15000).catch(() => {});
      const line = await textOf(page, lpb('pet-save-only-result'));
      const ok = (await drawnHere()) === 1 && line === '' && pageErrors.length === 0;
      step(ok, `opened again, the decision tracker is taken and the result of constructor is drawn (drawn: ${await drawnHere()}; line: "${clip(line)}"; page errors: ${pageErrors.join(' | ') || 'none'})`);
      tripped(writer, ok && saved.length === 1);
    });
  }

  // A run cannot keep a result under a name the page reads as a field of a
  // run, nor under __proto__: such a model is not offered (isReservedModelName).
  await road('LLMs named like a key the page reads as a field of a run, or __proto__', async (page) => {
    const reserved = [...(stored ? stored.TRACKER_META_KEYS : []), '__proto__'];
    await addModels(page, 'llm-proj', reserved.map((name, index) => [`rt-${index}`, name]));
    await openPage(page);
    await goStep(page, 'LPB', 'build');
    const seen = [];
    for (const name of reserved) seen.push([name, await boxOf(page, '#LPB-model-selector-container', name)]);
    const wrong = seen.filter(([name, box]) => !box || !box.off || box.note !== nameNote(name)).map(([name, box]) => `${name}: ${box ? `${box.off ? 'off' : 'on'}, note "${clip(box.note)}"` : 'missing'}`);
    const lists = await page.evaluate(() => [
      ...[...document.querySelectorAll('#app-obj-LPB-judge-model option')].map((option) => option.value),
      ...[...document.querySelectorAll('#app-obj-LPB-judge-panel input')].map((input) => input.value),
      ...[...document.querySelectorAll('#app-obj-LPB-judge-chairman-model option')].map((option) => option.value),
      ...[...document.querySelectorAll('#app-obj-LPB-optimize-target option')].map((option) => option.value),
    ]);
    const elsewhere = lists.filter((value) => reserved.includes(value));
    step(
      reserved.length > 1 && wrong.length === 0 && elsewhere.length === 0 && lists.includes('demo_llm') && pageErrors.length === 0,
      `an LLM named like a key the page reads as a field of a run, or __proto__, is shown with its box off and a note that says why - a run cannot keep the result of a model under that name -, and offered nowhere else (names: ${reserved.join(', ')}; otherwise: ${wrong.join('; ') || 'none'}; offered as judge, member, chairman or target: ${elsewhere.join(', ') || 'none'}; page errors: ${pageErrors.join(' | ') || 'none'})`
    );
  });

  // ==========================================================================
  // rag-setup.json: Save setup, Save documentation, Manifest setup
  // ==========================================================================
  const banner = (page) => textOf(page, rgb('status'));
  const SETUP_WRITES = [rgb('save'), rgb('manifest'), rgb('save-documentation')];
  const usable = async (page) => {
    let count = 0;
    for (const selector of SETUP_WRITES) if (!(await page.isDisabled(selector))) count++;
    return count;
  };
  const storedSetup = async () => {
    const items = (await (await fetch(`${BASE}/modelRepository/models/rag-setup-1/contents`)).json()).items || [];
    const item = items.find((entry) => entry.name === 'rag-setup.json');
    return item ? parsed(await (await fetch(`${BASE}${item.fileUri}/content`)).text()) : undefined;
  };
  const storeSetup = async (setup) => {
    const boundary = '----verify-round-trip';
    const body = `--${boundary}\r\nContent-Disposition: form-data; name="files"; filename="rag-setup.json"\r\nContent-Type: application/json\r\n\r\n${JSON.stringify(setup)}\r\n--${boundary}--\r\n`;
    await fetch(`${BASE}/modelRepository/models/rag-setup-1/contents?role=documentation`, { method: 'POST', headers: { 'Content-Type': `multipart/form-data; boundary=${boundary}` }, body });
  };
  const openSetup = async (page) => {
    await choose(page, 'RGB', rgb('setup'), 'rag-setup-2');
    await choose(page, 'RGB', rgb('setup'), 'rag-setup-1');
    await waitUntil(async () => (await usable(page)) === 3 || (await banner(page)) !== '', 'the setup is read', 20000).catch(() => {});
  };
  const docSection = async (page) => {
    await goStep(page, 'RGB', 'setup');
    const section = `${rgb('pane-setup')} .pb-doc-section`;
    if (!(await page.$eval(section, (details) => details.open))) await page.click(`${section} > summary`);
  };
  // A setup that was written (`written`: the write went through) is read
  // back: by the check of the page in stored-json.ts, and by the page itself,
  // which opens it again.
  const readBack = async (page, what, written, expect = () => true) => {
    if (!written) {
      step(false, `${what}: nothing was written (banner: "${clip(await banner(page))}"; result: "${clip(await textOf(page, rgb('save-result')))}")`);
      return false;
    }
    const setup = await storedSetup();
    const taken = check('rag-setup.json', setup);
    await openSetup(page);
    const said = await banner(page);
    const buttons = await usable(page);
    const shown = await expect(page, setup);
    const ok = taken && buttons === 3 && !said.startsWith('The stored setup cannot be used') && shown === true && pageErrors.length === 0;
    step(
      ok,
      `${what}: the rag-setup.json written is taken by the check of the page, and opened again the setup can be used and shows what was written (taken by the check: ${taken}; buttons that write it usable: ${buttons} of 3; banner: "${clip(said)}"; shown: ${shown === true ? 'yes' : clip(shown)}; page errors: ${pageErrors.join(' | ') || 'none'})`
    );
    return ok;
  };
  const saveSetup = async (page) => {
    await goStep(page, 'RGB', 'store');
    await resetLog();
    await page.click(rgb('save'));
    await page.waitForTimeout(300);
    await waitUntil(
      async () => !(await page.isDisabled(rgb('save'))) && Boolean(await page.$(`${rgb('save-result')} .alert-success, ${rgb('save-result')} .alert-danger`)),
      'the save has ended',
      30000
    );
    await atRest();
    return (await page.$(`${rgb('save-result')} .alert-success`)) !== null && uploadsOf(await getLog(), 'rag-setup-1', 'rag-setup.json').length === 1;
  };
  const saveDocumentation = async (page, text) => {
    await docSection(page);
    await page.fill(rgb('setup-doc-intendedUse'), text);
    await resetLog();
    await page.click(rgb('save-documentation'));
    await page.waitForTimeout(300);
    await waitUntil(async () => !(await page.$(`${rgb('save-documentation')} .spinner-border`)), 'the save has ended', 30000);
    await atRest();
    // A save that went through says so in a toast; one that failed, in the banner.
    return uploadsOf(await getLog(), 'rag-setup-1', 'rag-setup.json').length === 1 && (await banner(page)) === '';
  };

  // Every field the form holds, changed; an enrichment of two prompts.
  const TYPED = {
    modelPurpose: 'Answers "quoted" {braces} and [object Object] as text\nover two lines - ü ß',
    path: '/mnt/data/round trip',
    tokenLimit: '384',
    overlap: '0',
    collection: 'round_trip_v2',
    prefix: 'rtrip',
    folder: '/Public/Round Trip/generated',
  };
  await road('rag-setup.json: every field of the form changed, Save setup, Save documentation, Manifest setup', async (page) => {
    await fetch(BASE + '/__ragenrich', { method: 'POST', body: JSON.stringify({ on: true }) });
    await openPage(page, 'rag');
    await choose(page, 'RGB', rgb('project'), 'rag-proj');
    await openSetup(page);
    await docSection(page);
    await page.fill(rgb('setup-doc-modelPurpose'), TYPED.modelPurpose);
    await goStep(page, 'RGB', 'documents');
    await page.selectOption(rgb('source-kind'), 'sascontent');
    await page.fill(rgb('source-path'), TYPED.path);
    await page.setChecked(rgb('include-code'), true);
    const extractors = await page.$$eval(`${rgb('extractor')} option`, (options) => options.map((option) => option.value));
    await page.selectOption(rgb('extractor'), extractors[extractors.length - 1]);
    await page.selectOption(rgb('embed-model'), 'custom_embedder');
    // A number the field cannot hold: what the field makes of it is what Save
    // setup reads.
    await page.$eval(rgb('embed-dims'), (input) => {
      input.value = '1e309';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await page.fill(rgb('token-limit'), TYPED.tokenLimit);
    await page.fill(rgb('overlap'), TYPED.overlap);
    await goStep(page, 'RGB', 'store');
    await page.fill(rgb('collection'), TYPED.collection);
    await page.fill(rgb('tables-prefix'), TYPED.prefix);
    await page.fill(rgb('artifacts-folder'), TYPED.folder);
    // A width the number field cannot hold is refused before anything is written.
    const savedOverflow = await saveSetup(page);
    const overflowUploads = uploadsOf(await getLog(), 'rag-setup-1', 'rag-setup.json').length;
    step(
      !savedOverflow ? overflowUploads === 0 : check('rag-setup.json', await storedSetup()),
      `a vector width of 1e309 is refused before Save setup writes, or written as a setup the check takes (saved: ${savedOverflow}; uploads: ${overflowUploads})`
    );
    await goStep(page, 'RGB', 'documents');
    await page.fill(rgb('embed-dims'), '768');
    const saved = await saveSetup(page);
    const shows = async (target, setup) => {
      await docSection(target);
      const purpose = await target.inputValue(rgb('setup-doc-modelPurpose'));
      await goStep(target, 'RGB', 'documents');
      const fields = {
        purpose: purpose === TYPED.modelPurpose,
        path: (await target.inputValue(rgb('source-path'))) === TYPED.path,
        includeCode: await target.isChecked(rgb('include-code')),
        dims: (await target.inputValue(rgb('embed-dims'))) === '768',
        tokenLimit: (await target.inputValue(rgb('token-limit'))) === TYPED.tokenLimit,
        enrichment: (await target.$$(`${rgb('enrich-list')} > *`)).length === 2,
        stored: Boolean(setup && setup.documentation && setup.documentation.modelPurpose === TYPED.modelPurpose && setup.chunking.overlapTokens === 0 && Array.isArray(setup.enrich) && setup.enrich.length === 2),
      };
      await goStep(target, 'RGB', 'store');
      fields.collection = (await target.inputValue(rgb('collection'))) === TYPED.collection;
      fields.prefix = (await target.inputValue(rgb('tables-prefix'))) === TYPED.prefix.toUpperCase();
      const differ = Object.entries(fields).filter(([, same]) => !same).map(([name]) => name);
      return differ.length === 0 ? true : `differs in ${differ.join(', ')}`;
    };
    const afterSave = await readBack(page, 'Save setup of a setup whose every field was changed', saved, shows);
    tripped('rag-setup.json objects/rag-builder.ts saveSetup', afterSave);
    const documented = await saveDocumentation(page, 'Written by Save documentation');
    const afterDocumentation = await readBack(page, 'Save documentation of that setup', documented, async (target, setup) => {
      const same = await shows(target, setup);
      return same === true && setup.documentation.intendedUse === 'Written by Save documentation' ? true : `${same}; intended use stored: ${setup && setup.documentation && setup.documentation.intendedUse}`;
    });
    tripped('rag-setup.json objects/rag-builder.ts saveDocumentation', afterDocumentation);
    await goStep(page, 'RGB', 'store');
    await resetLog();
    await page.click(rgb('manifest'));
    await page.waitForTimeout(300);
    await waitUntil(
      async () => !(await page.isDisabled(rgb('manifest'))) && (await textOf(page, rgb('manifest'))) === 'Manifest setup' && (await textOf(page, rgb('save-result'))) !== '',
      'the manifest has ended',
      90000
    );
    await atRest();
    const manifested = uploadsOf(await getLog(), 'rag-setup-1', 'rag-setup.json').length > 0;
    const afterManifest = await readBack(page, 'Manifest setup of that setup', manifested, async (target, setup) => {
      const same = await shows(target, setup);
      return same === true && setup.job && typeof setup.job.definitionUri === 'string' ? true : `${same}; job: ${JSON.stringify(setup && setup.job)}`;
    });
    tripped('rag-setup.json objects/rag-builder.ts generateIngestionJob', afterManifest);
    await fetch(BASE + '/__ragenrich', { method: 'POST', body: JSON.stringify({ on: false }) });
  });

  // A stored setup with null where the check takes it: opened, saved by Save
  // setup and by Save documentation, and opened again.
  if (stored) {
    const shape = stored.STORED_SHAPES.RAG_SETUP;
    const filled = (field, empty) =>
      typeof field === 'string'
        ? ({ string: 'x', number: 1, boolean: true, flag: 1 })[field]
        : field.list ? [filled(field.list, empty)] : field.map ? { key: filled(field.map, empty) }
          : Object.fromEntries(Object.entries(field.fields).map(([name, inner]) => [
            name,
            (empty === 'all' || (empty === 'values' && typeof inner === 'string')) && !(field.required || []).includes(name) ? null : filled(inner, empty),
          ]));
    await fetch(BASE + '/__ragenrich', { method: 'POST', body: JSON.stringify({ on: false }) });
    const fixture = await storedSetup();
    for (const empty of ['values', 'all']) {
      const how = { values: 'null in every text, number and box that is not required', all: 'null in every field that is not required' }[empty];
      await road(`rag-setup.json with ${how}, saved and opened again`, async (page) => {
        // What the page shows has to be what the mock serves: the setup of the
        // fixture, with the nulls where the shape allows them.
        const blank = filled(shape, empty);
        const setup = { ...blank, version: 1, source: { ...blank.source, path: fixture.source.path }, extraction: { ...blank.extraction, extractor: '' },
          chunking: fixture.chunking, embedding: { ...blank.embedding, model: fixture.embedding.model, dims: fixture.embedding.dims },
          store: { ...blank.store, backend: fixture.store.backend, collection: fixture.store.collection }, tables: fixture.tables,
          enrich: blank.enrich === null ? null : [] };
        await storeSetup(setup);
        await openPage(page, 'rag');
        await choose(page, 'RGB', rgb('project'), 'rag-proj');
        await openSetup(page);
        const said = await banner(page);
        step(
          check('rag-setup.json', setup) && (await usable(page)) === 3 && !said.startsWith('The stored setup cannot be used') && pageErrors.length === 0,
          `a rag-setup.json with ${how} is taken and opens as a setup (taken by the check: ${check('rag-setup.json', setup)}; buttons usable: ${await usable(page)} of 3; banner: "${clip(said)}"; page errors: ${pageErrors.join(' | ') || 'none'})`
        );
        const documented = await saveDocumentation(page, `Documented over ${empty}`);
        const afterDocumentation = await readBack(page, `Save documentation over a setup with ${how}`, documented);
        tripped('rag-setup.json objects/rag-builder.ts saveDocumentation', afterDocumentation);
        const saved = await saveSetup(page);
        const afterSave = await readBack(page, `Save setup over a setup with ${how}`, saved);
        tripped('rag-setup.json objects/rag-builder.ts saveSetup', afterSave);
        await storeSetup(fixture);
      });
    }
  }

  // ==========================================================================
  // Prompt-Optimization-Tracker.json: the page deletes a run; the job's file
  // ==========================================================================
  await road('the optimization tracker the page writes at the delete of a run, and the one the job wrote', async (page) => {
    await openPage(page);
    await choose(page, 'LPB', '#LPB-project-dropdown', 'proj-1');
    // The tracker the job wrote, served for model-used: drawn, and a run of it
    // deleted by the page.
    let served = jobTracker ? JSON.stringify(jobTracker) : '[]';
    await page.route('**/files/files/opttrk-1/content', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: served }));
    await page.route('**/modelRepository/models/model-used/contents**', async (route) => {
      const request = route.request();
      if (request.method() === 'POST' && (request.postData() || '').includes('filename="Prompt-Optimization-Tracker.json"')) {
        const file = multipartFile(request.postData());
        if (file) served = file.text;
      }
      return route.continue();
    });
    await choose(page, 'LPB', '#LPB-prompt-dropdown', 'model-used');
    await goStep(page, 'LPB', 'optimize');
    const entries = `${lpb('pane-optimize')} .pb-optimize-history-entry`;
    await waitUntil(async () => (await page.$$(entries)).length > 0, 'the history is drawn', 15000).catch(() => {});
    const drawn = (await page.$$(entries)).length;
    const unread = (await page.$$(`${lpb('pane-optimize')} .pb-optimize-history-unread`)).length;
    const text = await textOf(page, lpb('pane-optimize'));
    step(
      Array.isArray(jobTracker) && drawn === jobTracker.length && unread === 0 && !text.includes('[object Object]') && pageErrors.length === 0,
      `the tracker the job wrote is drawn: every run of it, with no line that it cannot be shown (runs: ${Array.isArray(jobTracker) ? jobTracker.length : 'none written'}; drawn: ${drawn}; line: ${unread}; [object Object] drawn: ${text.includes('[object Object]')}; page errors: ${pageErrors.join(' | ') || 'none'})`
    );
    await resetLog();
    await page.click(`${entries} summary`).catch(() => {});
    await page.click(`${lpb('pane-optimize')} .pb-optimize-history-delete`);
    await page.waitForSelector('.modal.show .btn-danger', { timeout: 15000 });
    await page.waitForTimeout(450);
    await page.click('.modal.show .btn-danger');
    await waitUntil(async () => (await page.$$(entries)).length === drawn - 1, 'the run is gone from the history', 30000).catch(() => {});
    await atRest();
    const upload = uploadsOf(await getLog(), 'model-used', 'Prompt-Optimization-Tracker.json').pop();
    const remaining = upload ? parsed(upload.text) : undefined;
    const taken = check('Prompt-Optimization-Tracker.json', remaining);
    await choose(page, 'LPB', '#LPB-prompt-dropdown', 'model-free');
    await choose(page, 'LPB', '#LPB-prompt-dropdown', 'model-used');
    await goStep(page, 'LPB', 'optimize');
    await waitUntil(async () => (await page.$$(entries)).length > 0, 'the history is drawn again', 15000).catch(() => {});
    const again = (await page.$$(entries)).length;
    const unreadAgain = (await page.$$(`${lpb('pane-optimize')} .pb-optimize-history-unread`)).length;
    const ok = Array.isArray(remaining) && remaining.length === drawn - 1 && taken && again === drawn - 1 && unreadAgain === 0 && pageErrors.length === 0;
    tripped('Prompt-Optimization-Tracker.json objects/prompt-builder.ts deleteOptimizationRun', ok);
    step(
      ok,
      `the delete of a run writes the other runs as a tracker the check of the page takes, and opened again they are drawn (written: ${Array.isArray(remaining) ? remaining.length : 'nothing'}; taken by the check: ${taken}; drawn again: ${again}; line: ${unreadAgain}; page errors: ${pageErrors.join(' | ') || 'none'})`
    );
  });

  // ==========================================================================
  // Every writer had its round trip
  // ==========================================================================
  const notRun = writers.filter((writer) => !roundTrips.has(writer.key)).map((writer) => writer.key);
  const failed = writers.filter((writer) => roundTrips.get(writer.key) === false).map((writer) => writer.key);
  step(
    writers.length > 0 && notRun.length === 0 && failed.length === 0,
    `every writer of a stored JSON file had its round trip, read back by the check of the page (writers: ${writers.length}; without a round trip: ${notRun.join('; ') || 'none'}; read back otherwise: ${failed.join('; ') || 'none'})`
  );

  const directRequests = await (await fetch(BASE + '/__direct')).json();
  step(
    directRequests.length === 0,
    `no request to a SAS Viya service went around the HTTP client, which handles HTTP 449 and the CSRF token (direct: ${directRequests.join(' | ') || 'none'})`
  );
  await browser.close();
  console.log('\n===== SUMMARY =====');
  console.log(results.join('\n'));
  console.log(`${results.filter((r) => r.startsWith('OK')).length} checks passed, ${results.filter((r) => r.startsWith('FAIL')).length} failed`);
})().catch((e) => {
  console.error('VERIFY FAILED:', e.message);
  process.exit(1);
});
