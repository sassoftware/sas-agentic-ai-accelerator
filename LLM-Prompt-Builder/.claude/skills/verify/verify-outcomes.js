/**
 * What a write answered is what the page says, and nothing of one item, one
 * write or one dialog stays behind for the next. Run against a FRESH mock
 * instance.
 *
 * Before the roads, two censuses read the sources (source-census.js): every
 * write of src/api reads back what is stored when it gets no usable answer
 * (API_REQUESTS names the requests that do not, with why), every call of a
 * write in src/objects is named in WRITES with what it comes to then, and
 * every listener of a key in src is named in KEYS with what it starts.
 *
 * The roads, each on a page of its own:
 *
 *   - Writes SAS Model Manager refuses with a status are writes that failed:
 *     the uploads, deletes and the last attribute update of both manifests of
 *     the Prompt Builder, the model version and the upload of the tracker of
 *     Save Experiments, the writes of the delete of an optimization run, and
 *     in the RAG Builder the job reference and the Studio flow copy of
 *     Manifest setup and its model version. Nothing is deleted to be
 *     written back: a refused upload loses no run. The delete of an
 *     optimization run whose tracker cannot be read writes nothing and says
 *     what failed; one whose upload was stored without an answer reads the
 *     tracker back and says the run was deleted, one whose upload was not
 *     stored says the tracker was not changed.
 *   - A write whose request gets no answer is read back: Delete prompt-test,
 *     Delete project and Delete setup that were carried out end as after a
 *     204, and ones that were not say so without a word of permissions; Save
 *     Experiments and Save setup whose upload was stored are saves; a
 *     prompt-test whose creation was stored is listed and open. A Delete
 *     prompt-test or Delete project opens no dialog when another item was
 *     opened while the usage was asked. A Delete project that stops part of
 *     the way shows what is left, in both builders, and says what was
 *     deleted.
 *   - A decision run: its box reads "Use for Manifest", also where it is
 *     ticked over answers that did not match, and the note that every
 *     expected answer matched stands on the run where they did.
 *   - A setup opened twice while its question about a running ingestion is
 *     out keeps Launch off until the last question is answered; a setup
 *     created and not read back says that its buttons stay off, and Try
 *     again reads it; Enter in the question of a retrieval test that runs
 *     starts no second test, and a click a script sends to a control that is
 *     off reaches no listener (at two moments, every such control).
 *   - Save setup, Manifest setup and Save documentation cannot be used while
 *     the stored setup is read.
 *   - While a write is out, a change of a picker sent by a script (as the
 *     search field of a list sends one) is undone and reaches no handler; a
 *     write that never gets an answer is given up after two minutes (the
 *     clock of the page is moved on), and the selection is usable again.
 *   - The Manifest button of the RAG Builder is off until the last request of
 *     the manifest has ended.
 *   - What a setup showed - the line naming its generated flow, the banner of
 *     its failed run - goes with it; a create dialog opens empty after a
 *     creation; the menu of a search field holds nothing once it is closed.
 *   - An ingestion that runs is shown when its setup is opened on a page that
 *     was loaded after the launch, and one whose state cannot be read three
 *     times in a row is no longer followed: Launch is offered again.
 *
 * The suite goes on after a check that failed and after a road that stopped.
 * The mock is found at VERIFY_BASE (default http://localhost:4173).
 */
const { chromium } = require('playwright');
const fetch = require('./mock-fetch.cjs');
const { parse, walk, callsInSource, sourceFiles } = require('./source-census.cjs');

const BASE = (process.env.VERIFY_BASE || 'http://localhost:4173').replace(/\/+$/, '');
const results = [];

function step(ok, label) {
  results.push(`${ok ? 'OK ' : 'FAIL'}  ${label}`);
  console.log(`${ok ? 'OK ' : 'FAIL'}  ${label}`);
  if (!ok) process.exitCode = 1;
}
const getLog = async () => (await fetch(BASE + '/__log')).json();
const resetLog = async () => fetch(BASE + '/__reset', { method: 'POST' });
const post = (route, body) => fetch(BASE + route, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitUntil(fn, label, timeout = 15000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await fn()) return;
    await sleep(100);
  }
  throw new Error('timeout waiting for: ' + label);
}
const clip = (value) => (String(value).length > 140 ? `${String(value).slice(0, 140)}...` : String(value));
const refuse = (route) =>
  route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ httpStatusCode: 500, message: 'refused by the suite' }) });

const APP_URL =
  `${BASE}/?modelRepositoryID=repo-1&llmProjectID=llm-proj&SCREndpoint=${BASE}/scr` +
  '&enableOptimization=true&computeContext=ctx&optimizeJobProgram=/Public/Jobs/Optimize-Prompt-DSPy&minOptimizeSamples=1' +
  '&enableRag=true&embeddingProjectID=emb-proj&ragComputeContext=rag-ctx';
const lpb = (suffix) => `#app-obj-LPB-${suffix}`;
const rgb = (suffix) => `#app-obj-RGB-${suffix}`;
const MANIFEST_BOX = lpb('pet-save-result');

// ============================================================================
// The censuses: every write, and every way to an action that is not a click
// ============================================================================
// A write whose answer was lost was taken for one that failed: a Delete
// prompt-test that SAS Model Manager carried out said "The deletion failed"
// and kept the deleted prompt-test open. Every function of src/api that
// sends a write reads back what is stored when its request gets no usable
// answer (writeOrReadBack in src/api/http-client.ts): API_REQUESTS names the
// requests that do not, with why. WRITES names every call of those functions
// in src/objects, by the function it stands in, with what a write that got
// no answer comes to there once it was read back.
const WRITE_FUNCTIONS = [
  'createModelProject', 'createModel', 'createModelContent', 'deleteModelContent', 'deleteModelVariable', 'updateModelTags', 'updateModelAttributes',
  'deleteModel', 'deleteModelProject', 'createModelVersion', 'createJobDefinition', 'createTransientJobDefinition', 'updateJobDefinition',
  'deleteJobDefinition', 'launchJob', 'ensureFolderPath', 'ensureChildFolder', 'registerFlow',
];
const SENDERS = ['viyaPost', 'viyaPut', 'viyaDelete', 'viyaPatch', 'viyaPostRaw'];
const API_REQUESTS = {
  'api/http-client.ts viyaPost': 'a sender: every call of it in src/api is in this census',
  'api/http-client.ts viyaPostRaw': 'a sender: every call of it in src/api is in this census',
  'api/http-client.ts viyaPut': 'a sender: every call of it in src/api is in this census',
  'api/http-client.ts viyaDelete': 'a sender: every call of it in src/api is in this census',
  'api/http-client.ts viyaPatch': 'a sender: every call of it in src/api is in this census',
  'api/relationships-api.ts read': 'the relationships query: a read that is sent with POST',
  'api/scr-api.ts scoreSCR': 'a call of a model container: it stores nothing, and a call without an answer is a call that failed',
  'api/scr-api.ts callSCRDecision': 'a call of a model container: it stores nothing, and a call without an answer is a call that failed',
  'api/scr-api.ts callSCRLLM': 'a call of a model container: it stores nothing, and a call without an answer is a call that failed',
};
const READ_BACK = 'read back (see writeOrReadBack)';
const WRITES = {
  'objects/prompt-builder.ts': {
    'promptDocSaveButton.onclick -> updateModelAttributes': [1, `${READ_BACK}: the toast says that the request was not carried out, or that this is not known`],
    'loadPromptDocumentation -> updateModelAttributes': [1, `${READ_BACK}: the one fixed value of an earlier function name, written again at the next opening; nothing is said`],
    'promptBuilderCreateProject -> createModelProject': [1, `${READ_BACK} by its name: a project that is there is created and selected; otherwise the dialog says what the read back found`],
    'promptBuilderCreatePrompt -> createModel': [1, `${READ_BACK} by its name: a prompt-test that is there is created and selected (road below); otherwise the dialog says what the read back found`],
    'promptBuilderCreatePrompt -> updateModelTags': [1, `${READ_BACK}: the tags are best effort, the console says it`],
    'promptBuilderDeletePrompt -> deleteModel': [1, `${READ_BACK}: a prompt-test that is gone leaves the page as after a 204; otherwise the toast says what the read back found, without a word of permissions (road below)`],
    'promptBuilderDeleteProject -> deleteModel': [1, `${READ_BACK}: one that is gone counts as deleted; otherwise the delete stops and the page shows what is left of the project (road below)`],
    'promptBuilderDeleteProject -> deleteModelProject': [1, `${READ_BACK}: a project that is gone is deleted (road below); otherwise as above`],
    'storeManifestScoreCode -> createModelContent': [1, `${READ_BACK}: a score code that is stored lets the manifest go on; one that is not stops it with "the model does not hold"; one that cannot be read back stops it with "not known" (verify.js)`],
    'promptBuilderSaveExperiments -> createModelVersion': [1, `${READ_BACK} by the versions listed before: the save stops before the upload, and its line says what the read back found`],
    'promptBuilderSaveExperiments -> createModelContent': [1, `${READ_BACK}: a tracker that holds what was sent is saved (road below); otherwise the line beside the button says what the read back found (verify.js)`],
    'promptBulderCreateBestPromptModel -> deleteModelVariable': [1, `${READ_BACK}: a variable that is gone lets the manifest go on; otherwise it stops with its line`],
    'promptBulderCreateBestPromptModel -> createModelContent': [3, `${READ_BACK}: a file that holds what was sent lets the manifest go on; otherwise it stops with its line`],
    'promptBulderCreateBestPromptModel -> updateModelTags': [1, `${READ_BACK}: tags that were not written are said in a line of their own, and the manifest goes on`],
    'promptBulderCreateBestPromptModel -> deleteModelContent': [1, `${READ_BACK}: a file that is gone lets the manifest go on; otherwise it stops with its line`],
    'promptBulderCreateBestPromptModel -> updateModelAttributes': [1, `${READ_BACK}: otherwise the manifest ends with the line that it was not completed`],
    'promptBuilderCreateDecisionModel -> deleteModelVariable': [1, `${READ_BACK}: as in the manifest of a prompt template`],
    'promptBuilderCreateDecisionModel -> createModelContent': [3, `${READ_BACK}: as in the manifest of a prompt template`],
    'promptBuilderCreateDecisionModel -> updateModelTags': [1, `${READ_BACK}: as in the manifest of a prompt template`],
    'promptBuilderCreateDecisionModel -> updateModelAttributes': [1, `${READ_BACK}: as in the manifest of a prompt template`],
    'deleteOptimizationRun -> createModelVersion': [1, `${READ_BACK} by the versions listed before: the delete stops before the tracker is written and says that the tracker was not changed`],
    'deleteOptimizationRun -> createModelContent': [1, `${READ_BACK}: a tracker that holds what was sent is the delete carried out; one that does not says that the tracker was not changed; one that cannot be read back says the run may have been deleted or not; the history is read again (roads above)`],
    'deleteOptimizationRun -> deleteModelContent': [2, `${READ_BACK}: the delete of the tracker as its upload; a snapshot that may be left is said`],
    'promptBuilderRunOptimization -> launchJob': [1, `${READ_BACK} from the jobs that are running: the job of this launch is followed; otherwise the result box and a toast say that it is not known whether it was launched`],
    'promptBuilderRunComparison -> launchJob': [1, `${READ_BACK}: as the optimization`],
  },
  'objects/rag-builder.ts': {
    'saveDocumentation -> createModelContent': [1, `${READ_BACK}: otherwise the banner says what the read back found`],
    'saveDocumentation -> updateModelAttributes': [1, `${READ_BACK}: as the upload`],
    'removeStaleDocumentation -> deleteModelContent': [1, `${READ_BACK}: an obsolete copy that stays is not said (console)`],
    'createProjectFromModal -> createModelProject': [1, `${READ_BACK} by its name: a project that is there is created and selected; otherwise the dialog says what the read back found`],
    'createSetupFromModal -> createModel': [1, `${READ_BACK} by its name: as the project`],
    'saveSetup -> createModelContent': [2, `${READ_BACK}: a file that holds what was sent lets the save go on (road below); otherwise the banner and the line beside the button say what the read back found`],
    'saveSetup -> updateModelAttributes': [1, `${READ_BACK}: as the upload`],
    'saveSetup -> updateModelTags': [1, `${READ_BACK}: as the upload`],
    'generateIngestionJob -> updateJobDefinition': [1, `${READ_BACK}: a definition that holds what was sent lets the manifest go on; otherwise it stops, and the banner says what the read back found`],
    'generateIngestionJob -> ensureFolderPath': [1, `${READ_BACK}: a folder that is there lets the manifest go on; otherwise as above`],
    'generateIngestionJob -> createJobDefinition': [1, `${READ_BACK}: as the update`],
    'generateIngestionJob -> createModelContent': [1, `${READ_BACK}: as the update`],
    'manifestSetup -> createModelVersion': [1, `${READ_BACK} by the versions listed before: the line says that everything else was done, and what the read back found`],
    'launchIngestion -> launchJob': [1, `${READ_BACK} from the jobs that are running: the job of this launch is followed; otherwise no run is shown, and the banner says that it is not known whether it was launched`],
    'generateStudioFlow -> ensureFolderPath': [1, `${READ_BACK}: as in the ingestion job`],
    'generateStudioFlow -> registerFlow': [1, `${READ_BACK}: a flow of its name lets the manifest go on; otherwise it stops with what the read back found`],
    'generateStudioFlow -> createModelContent': [1, `${READ_BACK}: as the flow`],
    'registerRetrievalModel -> createModelContent': [1, `${READ_BACK}: a score code that is stored lets the stage go on; one that is not stops it with "the setup does not hold"; one that cannot be read back stops it with "not known" (verify-rag.js)`],
    'registerRetrievalModel -> updateModelTags': [1, `${READ_BACK}: tags that were not written are said in the outcome line`],
    'runRetrievalTest -> createTransientJobDefinition': [1, `${READ_BACK} among the definitions of its name: one that holds what was sent is used; otherwise the panel says what the read back found`],
    'runRetrievalTest -> launchJob': [1, `${READ_BACK} from the running jobs of the definition; otherwise the panel says that it is not known whether it was launched, and the definition is deleted`],
    'runRetrievalTest -> deleteJobDefinition': [1, `${READ_BACK}: a definition that may be left is said on the panel`],
    'deleteSetupButton:click -> deleteModel': [1, `${READ_BACK}: a setup that is gone is deleted, as after a 204; otherwise the banner says what the read back found, without a word of permissions (road below)`],
    'deleteProjectButton:click -> deleteModel': [1, `${READ_BACK}: otherwise the delete stops and the page shows what is left of the project (road below)`],
    'deleteProjectButton:click -> deleteModelProject': [1, `${READ_BACK}: as the setups`],
  },
};
// Every listener of a key in src, by "<file> <function> <object>:<event>":
// what it starts. An action whose button is off while it runs is refused by
// the same condition when a key starts it (and a click a script sends to a
// button that is off reaches no listener: src/ui/disabled-guard.ts).
const KEYS = {
  'objects/prompt-builder.ts buildPromptBuilder document:keydown': 'Escape hides the menu of variables of a prompt field; it starts nothing',
  'objects/rag-builder.ts buildRagBuilder testQuestionField:keydown': 'Enter runs the retrieval test: runRetrievalTest refuses it by testRunRefused, the condition Run test is off for (road below)',
  'ui/combobox.ts attachCombobox input:keydown': 'arrows move in the menu; Enter picks an entry, which changes the list as a click does - while a write is out the selection lock undoes that (verify-outcomes.js); Escape closes the menu',
  'ui/create-modal.ts wrapper:show.bs.modal document:keydown': 'Escape closes the dialog, which it does not while a creation is out (verify.js, verify-rag.js)',
  'ui/kind-selector.ts createKindSelector radio:keydown': 'arrows move the focus to the neighbouring card and choose nothing',
};

function writesCensus() {
  let files;
  try {
    files = sourceFiles();
  } catch (error) {
    step(false, `every write reads back what is stored (the sources could not be read: ${error.message}; set VERIFY_PROJECT, run npm ci)`);
    return;
  }
  // The requests of src/api that write.
  const found = {};
  const keys = {};
  for (const [file, text] of Object.entries(files)) {
    let parsed;
    try {
      parsed = parse(file, text);
    } catch (error) {
      step(false, `${file}: the sources are known to this suite (${error.message})`);
      return;
    }
    walk(parsed.program, parsed.source, (node, name, parents, outer) => {
      if (node.type !== 'CallExpression') return;
      if (node.callee.type === 'MemberExpression' && node.callee.property.name === 'addEventListener' && node.arguments[0] && node.arguments[0].type === 'Literal' && /^key/.test(String(node.arguments[0].value))) {
        const key = `${file} ${outer || '(module)'} ${parsed.source(node.callee.object)}:${node.arguments[0].value}`;
        keys[key] = (keys[key] || 0) + 1;
      }
      if (node.callee.type !== 'Identifier') return;
      let sends = SENDERS.includes(node.callee.name);
      if (['viyaFetch', 'fetch', 'fetchWithin'].includes(node.callee.name)) {
        const options = node.arguments[1];
        const method = options && options.type === 'ObjectExpression' && options.properties.find((property) => property.key && property.key.name === 'method');
        sends = Boolean(method) && /'(POST|PUT|DELETE|PATCH)'/.test(parsed.source(method.value));
      }
      if (!sends) return;
      const where = `${file} ${name || '(module)'}`;
      if (!file.startsWith('api/')) {
        found[where] = 'sends a write outside src/api';
        return;
      }
      const readBack = parents.some((parent, index) => parent.type === 'CallExpression' && parent.callee.type === 'Identifier' && parent.callee.name === 'writeOrReadBack' && parents[index + 1] === parent.arguments[0]);
      if (!readBack) found[where] = found[where] || 'no read back';
    });
  }
  const strangers = Object.keys(found).filter((where) => !API_REQUESTS[where]);
  const gone = Object.keys(API_REQUESTS).filter((where) => !found[where]);
  step(
    strangers.length === 0 && gone.length === 0,
    `every write of src/api reads back what is stored when it gets no usable answer, or is named here with why it does not (not named here: ${strangers.map((where) => `${where} (${found[where]})`).join('; ') || 'none'}; named but not found: ${gone.join('; ') || 'none'})`
  );
  for (const [file, known] of Object.entries(WRITES)) {
    const counts = callsInSource(file, files[file] || '', WRITE_FUNCTIONS);
    const unknown = Object.keys(counts).filter((key) => !known[key]);
    const missing = Object.keys(known).filter((key) => !counts[key]);
    const miscounted = Object.keys(known).filter((key) => counts[key] && counts[key] !== known[key][0]).map((key) => `${key}: ${counts[key]}, not ${known[key][0]}`);
    step(
      unknown.length === 0 && missing.length === 0 && miscounted.length === 0,
      `${file}: every write is named here with what a write that got no answer comes to once it was read back (${Object.keys(counts).length} calls; not named here: ${unknown.join('; ') || 'none'}; named but not found: ${missing.join('; ') || 'none'}; counted otherwise: ${miscounted.join('; ') || 'none'})`
    );
  }
  const otherFiles = Object.keys(files).filter((file) => !WRITES[file] && !file.startsWith('api/') && Object.keys(callsInSource(file, files[file], WRITE_FUNCTIONS)).length > 0);
  step(otherFiles.length === 0, `no other file outside src/api writes (got: ${otherFiles.join(', ') || 'none'})`);
  // Every listener of a key, and the one that starts an action.
  const unknownKeys = Object.keys(keys).filter((key) => !KEYS[key]);
  const goneKeys = Object.keys(KEYS).filter((key) => !keys[key]);
  const rag = files['objects/rag-builder.ts'] || '';
  const refusedAlike = rag.includes('if (testRunRefused()) return;') && (rag.match(/testRunButton\.disabled = testRunRefused\(\);/g) || []).length === 2 && !/testRunButton\.disabled = (?!testRunRefused\(\))/.test(rag.replace('testRunButton.disabled = true;', ''));
  const guarded = (files['main.ts'] || '').indexOf('refuseClicksOnDisabledControls();') >= 0 && (files['main.ts'] || '').indexOf('refuseClicksOnDisabledControls();') < (files['main.ts'] || '').indexOf('mountBuilderShell(root)');
  step(
    unknownKeys.length === 0 && goneKeys.length === 0 && refusedAlike && guarded,
    `every way to an action that is not a click is known: each listener of a key in src is named here with what it starts (not named here: ${unknownKeys.join('; ') || 'none'}; named but not found: ${goneKeys.join('; ') || 'none'}); the one that starts one, Enter in the question of the retrieval test, is refused by the condition its button is off for (${refusedAlike}); and a click a script sends to a control that is off is stopped before the builders are built (${guarded})`
  );
}

(async () => {
  writesCensus();
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  let pageErrors = [];
  const newPage = async ({ clock = false } = {}) => {
    const page = await browser.newPage({ viewport: { width: 1500, height: 1100 } });
    // A loaded machine can take longer than Playwright's 30 s to load the page.
    page.setDefaultNavigationTimeout(180000);
    page.on('pageerror', (e) => {
      pageErrors.push(e.message);
      console.log('[pageerror]', e.message);
    });
    page.on('dialog', (d) => d.accept().catch(() => {}));
    // Every toast that is shown, also one that is gone again.
    await page.addInitScript(() => {
      window.__toasts = [];
      new MutationObserver((records) => {
        for (const record of records) {
          for (const node of record.addedNodes) {
            if (node.nodeType === 1 && node.classList.contains('toast')) window.__toasts.push(node.textContent.trim());
          }
        }
      }).observe(document, { childList: true, subtree: true });
    });
    if (clock) await page.clock.install();
    return page;
  };
  const road = async (name, run, options) => {
    console.log(`---- ${name}`);
    pageErrors = [];
    const page = await newPage(options);
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
  const toasts = (page) => page.evaluate(() => window.__toasts.slice());
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
  const openPromptTest = async (page, project, model) => {
    await choose(page, 'LPB', '#LPB-project-dropdown', project);
    await choose(page, 'LPB', '#LPB-prompt-dropdown', model);
  };
  const openSetup = async (page, setup) => {
    await choose(page, 'RGB', rgb('project'), 'rag-proj');
    await choose(page, 'RGB', rgb('setup'), setup);
  };
  // As a person chooses an entry: the search field, then the entry of its menu.
  const pickAsAPerson = async (page, select, value) => {
    await page.click(`${select}-combobox`);
    await page.click(`${select}-combobox-list .dropdown-item[data-value="${value}"]`);
  };
  // As the search field does it: the value of the list set, and `change` sent.
  const pickByScript = (page, select, value) =>
    page.$eval(
      select,
      (list, chosen) => {
        list.value = chosen;
        list.dispatchEvent(new Event('change'));
      },
      value
    );
  const createInDialog = async (page, prefix, picker, modal, name, description = '') => {
    await goStep(page, prefix, 'setup');
    const before = await page.inputValue(picker);
    await page.click(`button[data-bs-target="#${modal}Modal"]`);
    await page.waitForSelector(`#${modal}Modal.show`);
    await page.waitForTimeout(400);
    const opened = { name: await page.inputValue(`#${modal}Name`), description: await page.inputValue(`#${modal}Description`) };
    await page.fill(`#${modal}Name`, name);
    await page.fill(`#${modal}Description`, description);
    await page.click(`#${modal}Modal .btn-primary`);
    await page.waitForSelector('.modal.show', { state: 'detached', timeout: 15000 });
    await waitUntil(async () => (await page.inputValue(picker)) !== before, `${name} is selected`);
    await atRest();
    return { id: await page.inputValue(picker), opened };
  };
  const confirmDialog = async (page) => {
    await page.waitForSelector('.modal.show .btn-danger', { timeout: 15000 });
    await page.waitForTimeout(450);
    await page.click('.modal.show .btn-danger');
  };

  // ============================================================================
  // The Prompt Builder
  // ============================================================================
  await post('/__decision');

  await road('a create dialog after a creation', async (page) => {
    await openPage(page);
    await choose(page, 'LPB', '#LPB-project-dropdown', 'proj-1');
    const first = await createInDialog(page, 'LPB', '#LPB-prompt-dropdown', 'promptBuilderCreatePrompt', 'Outcome with text', 'Scores the tone of a complaint');
    await resetLog();
    const second = await createInDialog(page, 'LPB', '#LPB-prompt-dropdown', 'promptBuilderCreatePrompt', 'Outcome name only');
    const created = (await getLog()).filter((entry) => entry.method === 'POST' && entry.url.startsWith('/modelRepository/models') && !entry.url.includes('/contents'));
    const sent = created.length ? JSON.parse(created[0].body).description : null;
    step(
      first.id !== second.id && second.opened.name === '' && second.opened.description === '' && sent === '',
      `Create prompt-test: after a creation the dialog opens empty, and the next prompt-test is created with the description typed for it (opened with: ${JSON.stringify(second.opened)}; description sent: ${JSON.stringify(sent)})`
    );
  });

  // The manifest of a prompt template whose writes are refused one at a time.
  const manifestWithRefusal = async (page, label, refuseIt) => {
    await openPromptTest(page, 'proj-1', 'model-used');
    await goStep(page, 'LPB', 'finalize');
    let refused = 0;
    let scoreCode = false;
    let puts = 0;
    await page.route('**/modelRepository/models/model-used**', (route) => {
      const request = route.request();
      const body = request.postData() || '';
      if (request.method() === 'POST' && body.includes('def scoreModel(')) scoreCode = true;
      if (request.method() === 'PUT' && scoreCode) puts += 1;
      if (refuseIt({ request, body, scoreCode, puts }) && refused === 0) {
        refused += 1;
        return refuse(route);
      }
      return route.fallback();
    });
    await resetLog();
    await page.click(lpb('pet-create-model-button'));
    await waitUntil(async () => refused === 1, `${label}: the write is refused`, 30000);
    await waitUntil(
      async () => !(await page.isDisabled(lpb('pet-create-model-button'))) && (await textOf(page, MANIFEST_BOX)) !== '',
      `${label}: the manifest has ended`,
      30000
    );
    await atRest();
    await page.unroute('**/modelRepository/models/model-used**');
    return {
      manifested: (await toasts(page)).filter((text) => text.includes('has been manifested')).length,
      lines: await page.$$eval(`${MANIFEST_BOX} .pet-manifest-failed`, (els) => els.map((e) => `${[...e.classList].join('.')}: ${e.textContent.trim().slice(0, 70)}`)),
    };
  };
  const uploadOf = (file) => ({ request, body }) => request.method() === 'POST' && body.includes(`filename="${file}"`);
  for (const [what, refuseIt, kind] of [
    ['inputVar.json', uploadOf('inputVar.json'), 'pet-manifest-stopped'],
    ['outputVar.json', uploadOf('outputVar.json'), 'pet-manifest-stopped'],
    ['the delete of a variable', ({ request }) => request.method() === 'DELETE' && request.url().includes('/variables/'), 'pet-manifest-stopped'],
    ['requirements.json', uploadOf('requirements.json'), 'pet-manifest-incomplete'],
    // The first PUT after the score code is the tag update, the second one the attributes.
    ['the update of the attributes', ({ request, puts }) => request.method() === 'PUT' && puts === 2, 'pet-manifest-incomplete'],
  ]) {
    await road(`a manifest of a prompt template, ${what} refused`, async (page) => {
      await post('/__manifested', { on: true });
      await openPage(page);
      // The fixture model has no variables; here it has one from an earlier
      // manifest, which the manifest deletes first.
      await page.route('**/modelRepository/models/model-used/variables?*', (route) =>
        route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [{ id: 'var-earlier', name: 'earlier' }] }) })
      );
      const outcome = await manifestWithRefusal(page, what, refuseIt);
      step(
        outcome.manifested === 0 && outcome.lines.length === 1 && outcome.lines[0].includes(kind),
        `Manifest, ${what} answered HTTP 500: no toast says "manifested", and the box says ${kind === 'pet-manifest-stopped' ? 'that nothing was manifested' : 'that the manifest was not completed'} (toasts "manifested": ${outcome.manifested}; box: ${outcome.lines.join(' | ') || 'nothing'})`
      );
      // The variables are deleted one by one before the two uploads: a stop
      // in between leaves the earlier ones, a part of them, none, or a part
      // of those of this run. The line names every one of those states.
      if (kind === 'pet-manifest-stopped') {
        const said = await page.$eval(`${MANIFEST_BOX} .pet-manifest-stopped`, (line) => line.textContent).catch(() => '');
        step(
          said.includes('may be the earlier ones, those of this run, a part of either, or none'),
          `Manifest, ${what} answered HTTP 500: the line says that the variables of the model may be the earlier ones, those of this run, a part of either or none (line: "${clip(said)}")`
        );
      }
    });
  }
  await post('/__manifested', { on: false });

  await road('a manifest of a prompt template without the LLM call, the delete of requirements.json refused', async (page) => {
    await openPage(page);
    const outcome = await manifestWithRefusal(page, 'the delete of requirements.json', ({ request }) => request.method() === 'DELETE' && request.url().includes('/contents/c-req'));
    step(
      outcome.manifested === 0 && outcome.lines.length === 1 && outcome.lines[0].includes('pet-manifest-incomplete'),
      `Manifest, the delete of the stale requirements.json answered HTTP 500: no toast says "manifested", the box says that the manifest was not completed (toasts: ${outcome.manifested}; box: ${outcome.lines.join(' | ') || 'nothing'})`
    );
  });

  await road('a manifest of a decision template, outputVar.json refused', async (page) => {
    await openPage(page, 'decision');
    await choose(page, 'LPB', '#LPB-project-dropdown', 'proj-2');
    await choose(page, 'LPB', '#LPB-prompt-dropdown', 'model-dec');
    await waitUntil(async () => (await page.$$('.pet-run-delete')).length >= 1, 'the saved run is loaded');
    await goStep(page, 'LPB', 'finalize');
    let refused = 0;
    await page.route('**/modelRepository/models/model-dec/contents?*', (route) => {
      if (route.request().method() === 'POST' && (route.request().postData() || '').includes('filename="outputVar.json"') && refused === 0) {
        refused += 1;
        return refuse(route);
      }
      return route.continue();
    });
    await page.click(lpb('pet-create-model-button'));
    await waitUntil(async () => refused === 1, 'the upload is refused', 30000);
    await waitUntil(async () => !(await page.isDisabled(lpb('pet-create-model-button'))) && (await textOf(page, MANIFEST_BOX)) !== '', 'the manifest has ended', 30000);
    await atRest();
    const manifested = (await toasts(page)).filter((text) => text.includes('has been manifested')).length;
    const stopped = (await page.$$(`${MANIFEST_BOX} .pet-manifest-failed.pet-manifest-stopped`)).length;
    step(manifested === 0 && stopped === 1, `Manifest of a decision template, outputVar.json answered HTTP 500: no toast says "manifested", the box says that nothing was manifested (toasts: ${manifested}; lines: ${stopped})`);
  });

  await road('Save Experiments, the new model version refused', async (page) => {
    await openPage(page);
    await openPromptTest(page, 'proj-1', 'model-used');
    await goStep(page, 'LPB', 'build');
    await page.route('**/modelRepository/models/model-used/**', (route) =>
      route.request().method() === 'POST' && route.request().url().includes('/modelVersions') ? refuse(route) : route.continue()
    );
    await page.click(lpb('pet-save-button'));
    await waitUntil(async () => (await textOf(page, lpb('pet-save-only-result'))) !== '', 'the save has ended', 20000);
    await atRest();
    const saved = (await toasts(page)).filter((text) => /saved/i.test(text)).length;
    const failed = (await page.$$(`${lpb('pet-save-only-result')} .pet-save-failed`)).length;
    step(saved === 0 && failed === 1, `Save Experiments, the new model version answered HTTP 500: no toast says "saved", the line beside the button says that the save did not finish (toasts: ${saved}; line: ${failed}; note: "${clip(await textOf(page, lpb('pet-save-only-result')))}")`);
  });

  // The stored tracker is replaced in one upload, never deleted first: a
  // refused upload leaves the runs that were stored.
  await road('Save Experiments, the upload of the tracker refused', async (page) => {
    await openPage(page);
    await openPromptTest(page, 'proj-1', 'model-used');
    await goStep(page, 'LPB', 'build');
    await page.route('**/modelRepository/models/model-used/**', (route) => {
      const request = route.request();
      return request.method() === 'POST' && (request.postData() || '').includes('filename="Prompt-Experiment-Tracker.json"') ? refuse(route) : route.continue();
    });
    await resetLog();
    await page.click(lpb('pet-save-button'));
    await waitUntil(async () => (await textOf(page, lpb('pet-save-only-result'))) !== '', 'the save has ended', 20000);
    await atRest();
    const saved = (await toasts(page)).filter((text) => /^Experiments saved/.test(text)).length;
    const note = await textOf(page, lpb('pet-save-only-result'));
    const deletes = (await getLog()).filter((entry) => entry.method === 'DELETE').map((entry) => entry.url);
    step(
      saved === 0 && /couldn't be saved/.test(note) && deletes.length === 0,
      `Save Experiments, the upload of the tracker answered HTTP 500: nothing of the prompt-test was deleted before it, no toast says "saved", and the line says that the experiment could not be saved (deletes: ${JSON.stringify(deletes)}; toasts "saved": ${saved}; note: "${clip(note)}")`
    );
  });

  await road('the delete of an optimization run, refused', async (page) => {
    await openPage(page);
    await openPromptTest(page, 'proj-1', 'model-used');
    await goStep(page, 'LPB', 'optimize');
    const entries = `${lpb('pane-optimize')} .pb-optimize-history-entry`;
    await page.waitForSelector(entries, { timeout: 15000 });
    const before = await page.$$eval(entries, (e) => e.length);
    await page.click(`${entries} summary`);
    await page.route('**/modelRepository/models/model-used/**', (route) => {
      const request = route.request();
      return request.method() === 'DELETE' || (request.method() === 'POST' && request.url().includes('/modelVersions')) ? refuse(route) : route.continue();
    });
    await page.click(`${lpb('pane-optimize')} .pb-optimize-history-delete`);
    await confirmDialog(page);
    await waitUntil(async () => (await toasts(page)).length > 0, 'a toast', 15000);
    await atRest();
    const said = await toasts(page);
    const after = await page.$$eval(entries, (e) => e.length);
    step(
      said.some((text) => text.startsWith('The deletion failed')) && !said.some((text) => text.includes('Optimization run deleted')) && after === before && pageErrors.length === 0,
      `the delete of an optimization run whose model version and deletes are refused says that the deletion failed, and the history still lists the run (toasts: ${JSON.stringify(said)}; entries before ${before}, after ${after}; page errors: ${pageErrors.length})`
    );
  });

  // The tracker of model-used as this suite serves it: the run of the mock
  // and a second one, and - where asked - the dataset snapshot of the first
  // in the contents. Writes to it are answered here, not by the mock.
  // `state.failNextRead` ('refused', 'abort' or 'html') makes the next read
  // of the tracker fail; `storeThenNoAnswer` stores an upload of the tracker
  // and then gives the page no answer.
  const servedTracker = async (page, { snapshot = false, refuseUpload = false, refuseSnapshot = false, storeThenNoAnswer = false } = {}) => {
    const first = (await (await fetch(BASE + '/files/files/opttrk-1/content')).json())[0];
    const state = { entries: [first, { ...first, optimizationId: 2, jobId: 'job-second', datasetSnapshot: null }], writes: [], failNextRead: null };
    await page.route('**/files/files/opttrk-1/content', (route) => {
      const failure = state.failNextRead;
      state.failNextRead = null;
      if (failure === 'abort') return route.abort('failed');
      if (failure === 'html') return route.fulfill({ status: 200, contentType: 'text/html', body: '<html><body>Bad gateway</body></html>' });
      if (failure) return refuse(route);
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(state.entries) });
    });
    await page.route('**/modelRepository/models/model-used/**', async (route) => {
      const request = route.request();
      const url = request.url();
      const body = request.postData() || '';
      if (request.method() === 'GET' && /\/contents(\?|$)/.test(url)) {
        const listing = await (await route.fetch()).json();
        if (snapshot) listing.items.push({ id: 'c-snap', name: first.datasetSnapshot, fileUri: '/files/files/snap-1' });
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(listing) });
      }
      if (request.method() === 'GET') return route.continue();
      state.writes.push(`${request.method()} ${new URL(url).pathname}${body.includes('filename="Prompt-Optimization-Tracker.json"') ? ' (tracker)' : ''}`);
      if (request.method() === 'POST' && body.includes('filename="Prompt-Optimization-Tracker.json"')) {
        if (refuseUpload) return refuse(route);
        const m = body.match(/\r\n\r\n([\s\S]*?)\r\n--/);
        state.entries = m ? JSON.parse(m[1]) : state.entries;
        if (storeThenNoAnswer) return route.abort('failed');
        return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ id: 'c-opttrk' }) });
      }
      if (request.method() === 'DELETE' && url.endsWith('/contents/c-opttrk')) {
        state.entries = [];
        return route.fulfill({ status: 204, body: '' });
      }
      if (request.method() === 'DELETE' && url.endsWith('/contents/c-snap')) {
        return refuseSnapshot ? refuse(route) : route.fulfill({ status: 204, body: '' });
      }
      return route.continue();
    });
    return state;
  };
  const deleteFirstRun = async (page) => {
    const entries = `${lpb('pane-optimize')} .pb-optimize-history-entry`;
    await page.waitForSelector(entries, { timeout: 15000 });
    await waitUntil(async () => (await page.$$eval(entries, (e) => e.length)) === 2, 'the history lists two runs');
    await page.click(`${lpb('pane-optimize')} .pb-optimize-history-delete[aria-label$="#1"]`);
    await confirmDialog(page);
    await waitUntil(async () => (await toasts(page)).length > 0, 'a toast', 15000);
    await atRest();
    await page.waitForTimeout(500);
    return { said: await toasts(page), after: await page.$$eval(entries, (e) => e.length) };
  };

  await road('the delete of an optimization run whose upload of the other runs is refused', async (page) => {
    await openPage(page);
    const state = await servedTracker(page, { refuseUpload: true });
    await openPromptTest(page, 'proj-1', 'model-used');
    await goStep(page, 'LPB', 'optimize');
    const { said, after } = await deleteFirstRun(page);
    step(
      said.some((text) => text.startsWith('The deletion failed')) && after === 2 && state.entries.length === 2 && !state.writes.some((write) => write.startsWith('DELETE')),
      `the delete of an optimization run whose upload of the other runs is refused deletes nothing: the tracker still holds both runs and the history lists them (toasts: ${JSON.stringify(said)}; entries shown: ${after}; stored: ${state.entries.length}; writes: ${JSON.stringify(state.writes)})`
    );
  });

  await road('the delete of an optimization run whose dataset snapshot cannot be deleted', async (page) => {
    await openPage(page);
    const state = await servedTracker(page, { snapshot: true, refuseSnapshot: true });
    await openPromptTest(page, 'proj-1', 'model-used');
    await goStep(page, 'LPB', 'optimize');
    const { said, after } = await deleteFirstRun(page);
    step(
      said.some((text) => text.startsWith('The run was removed from the optimization history, but the delete of its dataset snapshot Prompt-Optimization-Dataset-1.json was refused or got no answer: the snapshot may still be stored')) &&
        !said.some((text) => text.startsWith('Optimization run deleted')) &&
        after === 1 && state.entries.length === 1 && state.entries[0].optimizationId === 2,
      `the delete of an optimization run whose snapshot is refused says that the run left the history and that its snapshot may still be stored, and the history lists the other run (toasts: ${JSON.stringify(said)}; entries shown: ${after}; stored: ${JSON.stringify(state.entries.map((entry) => entry.optimizationId))})`
    );
  });

  // The tracker cannot be read at the delete: its other runs are not known,
  // and nothing is written - before, an empty list stood in for them and the
  // tracker was deleted with every run. The toast says what failed: the read
  // (refused or without an answer), or a tracker that is no list of runs -
  // not that permissions are missing.
  for (const [failure, said] of [
    ['refused', 'The run was not deleted: the optimization tracker of this prompt-test could not be read from SAS Model Manager'],
    ['abort', 'The run was not deleted: the optimization tracker of this prompt-test could not be read from SAS Model Manager'],
    ['html', 'The run was not deleted: SAS Model Manager answered with a Prompt-Optimization-Tracker.json that holds no runs this page can read'],
  ]) {
    await road(`the delete of an optimization run whose tracker cannot be read (${failure})`, async (page) => {
      await openPage(page);
      const state = await servedTracker(page);
      await openPromptTest(page, 'proj-1', 'model-used');
      await goStep(page, 'LPB', 'optimize');
      await page.waitForSelector(`${lpb('pane-optimize')} .pb-optimize-history-entry`, { timeout: 15000 });
      state.failNextRead = failure;
      const { said: toastsSaid, after } = await deleteFirstRun(page);
      step(
        toastsSaid.some((text) => text.startsWith(said)) && !toastsSaid.some((text) => text.includes('permissions') || text.startsWith('Optimization run deleted')) &&
          after === 2 && state.entries.length === 2 && state.writes.length === 0,
        `the delete of an optimization run whose tracker cannot be read (${failure}) writes nothing and says what failed, without a word of permissions: both runs are stored and listed (toasts: ${JSON.stringify(toastsSaid)}; entries shown: ${after}; stored: ${state.entries.length}; writes: ${JSON.stringify(state.writes)})`
      );
    });
  }

  // The upload of the other runs is stored, and its answer is lost: the page
  // reads the tracker back, finds the run gone, and the delete goes on.
  await road('the delete of an optimization run whose upload is stored without an answer', async (page) => {
    await openPage(page);
    const state = await servedTracker(page, { storeThenNoAnswer: true });
    await openPromptTest(page, 'proj-1', 'model-used');
    await goStep(page, 'LPB', 'optimize');
    const { said, after } = await deleteFirstRun(page);
    step(
      said.some((text) => text.startsWith('Optimization run deleted')) &&
        !said.some((text) => text.startsWith('The deletion failed') || text.includes('may have been deleted or not')) &&
        after === 1 && state.entries.length === 1 && state.entries[0].optimizationId === 2,
      `the delete of an optimization run whose upload is stored without an answer reads the tracker back and says the run was deleted, under a history that lists the other run (toasts: ${JSON.stringify(said)}; entries shown: ${after}; stored: ${JSON.stringify(state.entries.map((entry) => entry.optimizationId))})`
    );
  });

  // The upload gets no answer and was not stored: read back, the tracker
  // still holds the run, and the toast says that nothing of it was changed.
  await road('the delete of an optimization run whose upload gets no answer and is not stored', async (page) => {
    await openPage(page);
    const state = await servedTracker(page);
    await page.route('**/modelRepository/models/model-used/contents?*', (route) =>
      route.request().method() === 'POST' && (route.request().postData() || '').includes('filename="Prompt-Optimization-Tracker.json"') ? route.abort('failed') : route.fallback()
    );
    await openPromptTest(page, 'proj-1', 'model-used');
    await goStep(page, 'LPB', 'optimize');
    const { said, after } = await deleteFirstRun(page);
    step(
      said.some((text) => text.startsWith('The run was not deleted: a request to SAS Model Manager failed or got no answer, and the optimization tracker was not changed.')) &&
        !said.some((text) => text.includes('permissions') || text.startsWith('Optimization run deleted')) && after === 2 && state.entries.length === 2,
      `the delete of an optimization run whose upload gets no answer and is not stored reads the tracker back and says the run was not deleted, without a word of permissions, under a history that lists both runs (toasts: ${JSON.stringify(said)}; entries shown: ${after}; stored: ${state.entries.length})`
    );
  });

  await road('the delete of the last optimization run', async (page) => {
    await openPage(page);
    const state = await servedTracker(page);
    state.entries = state.entries.slice(0, 1);
    await openPromptTest(page, 'proj-1', 'model-used');
    await goStep(page, 'LPB', 'optimize');
    const entries = `${lpb('pane-optimize')} .pb-optimize-history-entry`;
    await page.waitForSelector(entries, { timeout: 15000 });
    await page.click(`${lpb('pane-optimize')} .pb-optimize-history-delete[aria-label$="#1"]`);
    await confirmDialog(page);
    await waitUntil(async () => (await toasts(page)).length > 0, 'a toast', 15000);
    await atRest();
    const said = await toasts(page);
    step(
      said.some((text) => text.startsWith('Optimization run deleted')) && JSON.stringify(state.writes.filter((write) => !write.includes('/modelVersions'))) === JSON.stringify(['DELETE /modelRepository/models/model-used/contents/c-opttrk']),
      `the delete of the last optimization run deletes the tracker and writes nothing else (toasts: ${JSON.stringify(said)}; writes: ${JSON.stringify(state.writes)})`
    );
  });

  await road('Delete prompt-test while another one is opened', async (page) => {
    await openPage(page);
    await choose(page, 'LPB', '#LPB-project-dropdown', 'proj-1');
    const victim = (await createInDialog(page, 'LPB', '#LPB-prompt-dropdown', 'promptBuilderCreatePrompt', 'Outcome victim')).id;
    await page.route('**/relationships/relationships**', async (route) => {
      await sleep(3000);
      await route.continue().catch(() => {});
    });
    await resetLog();
    await page.click('#LPB-delete-prompt-button');
    await page.waitForTimeout(300);
    await pickAsAPerson(page, '#LPB-prompt-dropdown', 'model-free');
    await page.waitForTimeout(4000);
    await atRest();
    const dialog = (await page.$$('.modal.show')).length;
    const deletes = (await getLog()).filter((entry) => entry.method === 'DELETE').length;
    step(
      dialog === 0 && deletes === 0 && (await page.inputValue('#LPB-prompt-dropdown')) === 'model-free',
      `Delete prompt-test: when another prompt-test was opened while the usage was asked, no dialog comes up, nothing is deleted and the other one stays open (dialog: ${dialog}; deletes: ${deletes}; open: ${await page.inputValue('#LPB-prompt-dropdown')}; victim ${victim})`
    );
  });

  await road('Delete project while another one is chosen', async (page) => {
    await openPage(page);
    const project = (await createInDialog(page, 'LPB', '#LPB-project-dropdown', 'promptBuilderCreateProject', 'Outcome project')).id;
    await page.route(`**/modelRepository/projects/${project}/models**`, async (route) => {
      await sleep(3000);
      await route.continue().catch(() => {});
    });
    await resetLog();
    await page.click('#LPB-delete-project-button');
    await page.waitForTimeout(300);
    await pickAsAPerson(page, '#LPB-project-dropdown', 'proj-1');
    await page.waitForTimeout(4000);
    await atRest();
    const dialog = (await page.$$('.modal.show')).length;
    const deletes = (await getLog()).filter((entry) => entry.method === 'DELETE').length;
    step(
      dialog === 0 && deletes === 0 && (await page.inputValue('#LPB-project-dropdown')) === 'proj-1',
      `Delete project: when another project was chosen while its prompt-tests were listed, no dialog comes up and nothing is deleted (dialog: ${dialog}; deletes: ${deletes}; open: ${await page.inputValue('#LPB-project-dropdown')})`
    );
  });

  // The mock lists no prompt project it created, SAS Model Manager does: a
  // delete that stops reads the projects back and finds it there.
  const listCreatedProject = (page, id, name) =>
    page.route('**/modelRepository/projects?*', async (route) => {
      const filter = new URL(route.request().url()).searchParams.get('filter') || '';
      if (route.request().method() !== 'GET' || !filter.includes('Prompt-Engineering')) return route.continue();
      const listing = await (await route.fetch()).json();
      listing.items.push({ id, name, createdBy: 'tester', modifiedBy: 'tester' });
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(listing) });
    });
  // A delete whose request gets no answer is read back. Where SAS Model
  // Manager still holds what was to be deleted, the toast says that the
  // request was not carried out - not that permissions are missing - and the
  // item stays. Where the delete was carried out and only its answer was
  // lost, the page goes on as after a 204: the item leaves the list and is
  // open no longer, and no toast says the deletion failed.
  const NOT_CARRIED_OUT = 'A request to SAS Viya got no usable answer, and reading back what SAS Viya holds shows that it was not carried out.';
  for (const what of ['prompt-test', 'project']) {
    for (const lost of ['not carried out', 'carried out']) {
      await road(`Delete ${what} whose request gets no answer (${lost})`, async (page) => {
        await openPage(page);
        let id;
        const picker = what === 'project' ? '#LPB-project-dropdown' : '#LPB-prompt-dropdown';
        // SAS Model Manager holds the project until the delete reaches it; the
        // mock holds no prompt project of its own, so the page is told so here.
        let projectHeld = true;
        if (what === 'project') {
          id = (await createInDialog(page, 'LPB', '#LPB-project-dropdown', 'promptBuilderCreateProject', `Outcome cut project ${lost}`)).id;
          await page.route('**/modelRepository/projects?*', async (route) => {
            const filter = new URL(route.request().url()).searchParams.get('filter') || '';
            if (route.request().method() !== 'GET' || !filter.includes('Prompt-Engineering')) return route.continue();
            const listing = await (await route.fetch()).json();
            if (projectHeld) listing.items.push({ id, name: `Outcome cut project ${lost}`, createdBy: 'tester', modifiedBy: 'tester' });
            return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(listing) });
          });
        } else {
          await choose(page, 'LPB', '#LPB-project-dropdown', 'proj-1');
          id = (await createInDialog(page, 'LPB', '#LPB-prompt-dropdown', 'promptBuilderCreatePrompt', `Outcome cut ${lost}`)).id;
        }
        await page.route(`**/modelRepository/${what === 'project' ? 'projects' : 'models'}/${id}`, async (route) => {
          const method = route.request().method();
          if (what === 'project' && method === 'GET') {
            return projectHeld ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ id }) }) : route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
          }
          if (method !== 'DELETE') return route.continue();
          if (lost === 'carried out') {
            await route.fetch();
            projectHeld = false;
          }
          return route.abort('failed');
        });
        await page.click(what === 'project' ? '#LPB-delete-project-button' : '#LPB-delete-prompt-button');
        await confirmDialog(page);
        if (lost === 'not carried out') await waitUntil(async () => (await toasts(page)).length > 0, 'a toast', 15000).catch(() => {});
        await atRest();
        await page.waitForTimeout(800);
        const said = await toasts(page);
        const listedNow = await page.$$eval(`${picker} option`, (options) => options.map((option) => option.value));
        const open = await page.inputValue(picker);
        const seen = `toasts: ${JSON.stringify(said)}; listed: ${listedNow.includes(id)}; open: ${open}; page errors: ${JSON.stringify(pageErrors)}`;
        if (lost === 'not carried out') {
          step(
            said.some((text) => text.includes(NOT_CARRIED_OUT)) && !said.some((text) => text.includes('permissions')) && listedNow.includes(id) && open === id && pageErrors.length === 0,
            `Delete ${what}: a delete whose request gets no answer and was not carried out is read back: the toast says so, without a word of permissions, and the ${what} stays listed and open (${seen})`
          );
        } else {
          step(
            !said.some((text) => text.startsWith('The deletion failed') || text.includes('got no usable answer')) && !listedNow.includes(id) && open !== id && pageErrors.length === 0,
            `Delete ${what}: a delete that was carried out and whose answer was lost is read back: the ${what} leaves the list and is open no longer, and no toast says the deletion failed (${seen})`
          );
        }
      });
    }
  }

  // A Delete project that stops part of the way: the first prompt-test is
  // deleted, the second one is refused or gets no answer. The page reads back
  // what SAS Model Manager holds and says what was deleted and what was not.
  const notDeleted = [
    ['refused', (route) => route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ httpStatusCode: 403, message: 'refused by the suite' }) })],
    ['without an answer', (route) => route.abort('failed')],
  ];
  for (const [how, fail] of notDeleted) {
    await road(`Delete project whose second prompt-test is not deleted (${how})`, async (page) => {
      await openPage(page);
      const projectName = `Outcome partial ${how}`;
      const project = (await createInDialog(page, 'LPB', '#LPB-project-dropdown', 'promptBuilderCreateProject', projectName)).id;
      await listCreatedProject(page, project, projectName);
      const first = (await createInDialog(page, 'LPB', '#LPB-prompt-dropdown', 'promptBuilderCreatePrompt', `VP one ${how}`)).id;
      const second = (await createInDialog(page, 'LPB', '#LPB-prompt-dropdown', 'promptBuilderCreatePrompt', `VP two ${how}`)).id;
      // The prompt-test that is open is the one that gets deleted.
      await choose(page, 'LPB', '#LPB-prompt-dropdown', first);
      await page.route(`**/modelRepository/models/${second}`, (route) => (route.request().method() === 'DELETE' ? fail(route) : route.continue()));
      await page.click('#LPB-delete-project-button');
      // One dialog per prompt-test of the project.
      const asked = (await (await fetch(`${BASE}/modelRepository/projects/${project}/models`)).json()).items.length;
      for (let dialog = 0; dialog < asked; dialog++) {
        await confirmDialog(page);
        await page.waitForSelector('.modal.show', { state: 'detached', timeout: 15000 });
      }
      await waitUntil(async () => (await toasts(page)).length > 0, 'a toast', 20000);
      await atRest();
      const said = (await toasts(page)).join(' | ');
      const listedNow = await page.$$eval('#LPB-prompt-dropdown option', (options) => options.map((option) => option.value));
      const open = await page.inputValue('#LPB-prompt-dropdown');
      // A refusal says the deletion failed; a delete without an answer that
      // reading back shows not carried out says that, with no word of
      // permissions.
      const firstLine = how === 'refused' ? `The deletion failed: the project ${projectName} is still in SAS Model Manager.` : `Delete Project: ${NOT_CARRIED_OUT}`;
      step(
        !listedNow.includes(first) && listedNow.includes(second) && open !== first && (await page.inputValue('#LPB-project-dropdown')) === project &&
          said.includes(firstLine) && (how === 'refused' || !said.includes('permissions')) &&
          new RegExp(`Deleted from it: [^|]*VP one ${how}`).test(said) && new RegExp(`Still in it: [^|]*VP two ${how}`).test(said) && !/Still in it: [^|]*VP one/.test(said),
        `Delete project whose second prompt-test is ${how}: the list drops the prompt-test that was deleted, which was open and is open no longer, and the toast names what was deleted and what is still in the project (listed: ${JSON.stringify(listedNow)}; open: ${open}; toasts: ${said.slice(0, 400)})`
      );
      // The mock gives the id of a deleted model to the next one it creates:
      // the one that is left goes, or the next road would get its id twice.
      await fetch(`${BASE}/modelRepository/models/${second}`, { method: 'DELETE' });
    });
  }

  // A save whose upload SAS Model Manager stored and whose answer was lost:
  // read back, the tracker holds what was sent, and the save is a save.
  await road('Save Experiments whose upload is stored and its answer lost', async (page) => {
    await openPage(page);
    let stored = null;
    await page.route('**/files/files/trk-1/content', (route) => (stored ? route.fulfill({ status: 200, contentType: 'application/json', body: stored }) : route.continue()));
    await openPromptTest(page, 'proj-1', 'model-used');
    await page.route('**/modelRepository/models/model-used/contents?*', async (route) => {
      const body = route.request().postData() || '';
      if (route.request().method() !== 'POST' || !body.includes('filename="Prompt-Experiment-Tracker.json"')) return route.continue();
      await route.fetch();
      stored = (body.match(/\r\n\r\n([\s\S]*?)\r\n--/) || [])[1] || null;
      return route.abort('failed');
    });
    await goStep(page, 'LPB', 'build');
    await page.click(lpb('pet-save-button'));
    await waitUntil(async () => stored !== null, 'the upload was stored', 20000);
    await waitUntil(async () => (await textOf(page, lpb('pet-save-button'))) === 'Save Experiments', 'the save has ended', 30000);
    await atRest();
    const said = await toasts(page);
    const line = await textOf(page, lpb('pet-save-only-result'));
    step(
      said.some((text) => text.startsWith('Experiments saved')) && !(await page.$(`${lpb('pet-save-only-result')} .pet-save-failed`)) && pageErrors.length === 0,
      `Save Experiments whose upload is stored and its answer lost reads the tracker back, finds what it sent, and says the experiments are saved (toasts: ${JSON.stringify(said)}; line: "${clip(line)}"; page errors: ${JSON.stringify(pageErrors)})`
    );
  });

  // A creation SAS Model Manager carried out and whose answer was lost: read
  // back, the prompt-test of that name is there, and it is listed and open.
  await road('Create prompt-test whose creation is stored and its answer lost', async (page) => {
    await openPage(page);
    await choose(page, 'LPB', '#LPB-project-dropdown', 'proj-1');
    const name = 'Outcome created without an answer';
    await page.route('**/modelRepository/models', async (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      await route.fetch();
      return route.abort('failed');
    });
    await goStep(page, 'LPB', 'setup');
    await page.click('button[data-bs-target="#promptBuilderCreatePromptModal"]');
    await page.waitForSelector('#promptBuilderCreatePromptModal.show');
    await page.waitForTimeout(400);
    await page.fill('#promptBuilderCreatePromptName', name);
    await page.click('#promptBuilderCreatePromptModal .btn-primary');
    await page.waitForSelector('.modal.show', { state: 'detached', timeout: 15000 }).catch(() => {});
    await atRest();
    const created = ((await (await fetch(`${BASE}/modelRepository/projects/proj-1/models`)).json()).items || []).find((model) => model.name === name);
    const open = await page.inputValue('#LPB-prompt-dropdown');
    const dialogOpen = (await page.$$('#promptBuilderCreatePromptModal.show')).length > 0;
    const error = dialogOpen ? await textOf(page, '#promptBuilderCreatePromptError') : '';
    step(
      Boolean(created) && open === created.id && !dialogOpen,
      `Create prompt-test whose creation is stored and its answer lost reads the project back, finds the prompt-test, closes the dialog and opens it (created: ${created ? created.id : 'none'}; open: ${open}; dialog open: ${dialogOpen}; "${clip(error)}")`
    );
    if (created) await fetch(`${BASE}/modelRepository/models/${created.id}`, { method: 'DELETE' });
  });

  // A decision run: the box is what Manifest takes. Ticked on a run whose
  // answers did not match, it says nothing untrue; the note that the answers
  // matched stands on the run where they did, and only there.
  await road('the box of a decision run that Manifest takes, ticked on a run whose answers did not match', async (page) => {
    await openPage(page, 'decision');
    const rows = await (await fetch(BASE + '/files/files/dtrk-1/content')).json();
    const header = rows.find((row) => row.model === '' || row.model === undefined);
    const answer = rows.find((row) => row !== header);
    const missed = JSON.parse(answer.response);
    missed.team = { ...missed.team, choice: 'technical' };
    const second = [
      { ...header, runId: 2 },
      { ...answer, runId: 2, best_prompt: 1, response: JSON.stringify(missed) },
    ];
    await page.route('**/files/files/dtrk-1/content', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([...rows, ...second]) }));
    await openPromptTest(page, 'proj-2', 'model-dec');
    await waitUntil(async () => (await page.$$(`${lpb('pet')} .pet-run-delete`)).length === 2, 'both runs are drawn', 15000);
    const runs = await page.$$eval(`${lpb('pet')} [id^="best-prompt-"]`, (boxes) =>
      boxes.map((box) => {
        const body = box.closest('.accordion-body');
        return {
          id: box.id,
          ticked: box.checked,
          label: (document.querySelector(`label[for="${box.id}"]`)?.textContent || '').trim(),
          note: (body?.querySelector('.pb-expected-all-matched')?.textContent || '').trim(),
          matched: body ? body.querySelectorAll('.pb-answer-expected.is-match').length : 0,
          missed: body ? body.querySelectorAll('.pb-answer-expected.is-miss').length : 0,
        };
      })
    );
    const legend = await textOf(page, lpb('pet-legend'));
    const ok =
      runs.length === 2 && runs.every((run) => run.ticked && run.label === 'Use for Manifest') &&
      runs.filter((run) => run.missed === 0).every((run) => run.note === 'All expected answers matched') &&
      runs.filter((run) => run.missed > 0).length === 1 && runs.filter((run) => run.missed > 0).every((run) => run.note === '') &&
      legend.includes('Use for Manifest') && !legend.includes('Correct');
    step(
      ok,
      `a decision run: the box reads "Use for Manifest" also where it is ticked over answers that did not match, the note "All expected answers matched" stands on the run whose answers all matched and not on the other, and the legend says what the box does (runs: ${JSON.stringify(runs)}; legend: "${clip(legend)}")`
    );
  });

  await road('a change of the prompt-test list sent by a script while Save Experiments is out', async (page) => {
    await openPage(page);
    await openPromptTest(page, 'proj-1', 'model-used');
    const stored = (await (await fetch(BASE + '/files/files/trk-1/content')).json()).length;
    await goStep(page, 'LPB', 'build');
    await page.route('**/modelRepository/models/model-used/**', async (route) => {
      if (route.request().method() !== 'GET') await sleep(2500);
      await route.continue().catch(() => {});
    });
    await resetLog();
    await page.click(lpb('pet-save-button'));
    await page.waitForTimeout(400);
    await pickByScript(page, '#LPB-prompt-dropdown', 'model-free');
    const during = await page.inputValue('#LPB-prompt-dropdown');
    await waitUntil(async () => (await textOf(page, lpb('pet-save-only-result'))) !== '', 'the save has ended', 30000);
    await atRest();
    const uploads = (await getLog()).filter((entry) => entry.method === 'POST' && entry.body.includes('filename="Prompt-Experiment-Tracker.json"'));
    const rows = uploads.map((entry) => {
      const m = entry.body.match(/\r\n\r\n([\s\S]*?)\r\n--/);
      return `${entry.url.split('?')[0]} rows=${m ? JSON.parse(m[1]).length : '?'}`;
    });
    step(
      during === 'model-used' && (await page.inputValue('#LPB-prompt-dropdown')) === 'model-used' && rows.length === 1 && rows[0] === `/modelRepository/models/model-used/contents rows=${stored}`,
      `Save Experiments: a change of the list sent by a script while the save is out is undone, and the tracker with its ${stored} runs goes to the prompt-test of the click (list right after the change: ${during}; uploads: ${JSON.stringify(rows)})`
    );
    await pickByScript(page, '#LPB-prompt-dropdown', 'model-free');
    await atRest();
    step((await page.inputValue('#LPB-prompt-dropdown')) === 'model-free', 'and once the save has ended, the same change opens the other prompt-test');
  });

  await road('a write that never gets an answer', async (page) => {
    await openPage(page);
    await openPromptTest(page, 'proj-1', 'model-used');
    await goStep(page, 'LPB', 'build');
    let held = 0;
    await page.route('**/modelRepository/models/model-used/**', (route) => {
      if (route.request().method() === 'GET') return route.continue();
      held += 1; // never answered
      return undefined;
    });
    await page.click(lpb('pet-save-button'));
    await waitUntil(async () => held > 0, 'the first write is out');
    await page.waitForTimeout(1000);
    const lockedBefore = await page.$eval('#LPB-selection', (block) => block.disabled);
    await page.clock.fastForward(121000);
    await waitUntil(async () => !(await page.$eval('#LPB-selection', (block) => block.disabled)), 'the selection is usable again', 15000).catch(() => {});
    const lockedAfter = await page.$eval('#LPB-selection', (block) => block.disabled);
    const failed = (await page.$$(`${lpb('pet-save-only-result')} .pet-save-failed`)).length;
    step(
      lockedBefore && !lockedAfter && failed === 1 && !(await page.isDisabled(lpb('pet-save-button'))),
      `Save Experiments: a write that gets no answer is given up after two minutes - the selection is usable again and the line says that the save did not finish (locked while out: ${lockedBefore}; locked after: ${lockedAfter}; line: ${failed})`
    );
  }, { clock: true });

  await road('the menu of a search field once it is closed', async (page) => {
    await openPage(page);
    await goStep(page, 'LPB', 'setup');
    await waitUntil(() => listed(page, '#LPB-project-dropdown', 'proj-1'), 'proj-1 is listed');
    await pickAsAPerson(page, '#LPB-project-dropdown', 'proj-1');
    await atRest();
    const entries = await page.$$eval('#LPB-project-dropdown-combobox-list .dropdown-item', (items) => items.length);
    const shown = await page.$eval('#LPB-project-dropdown-combobox-list', (menu) => menu.classList.contains('show'));
    await page.click('#LPB-project-dropdown-combobox');
    const reopened = await page.$$eval('#LPB-project-dropdown-combobox-list .dropdown-item', (items) => items.length);
    step(
      !shown && entries === 0 && reopened > 0,
      `a search field: once an entry is picked its menu is closed and holds no entries, and it is built again when it opens (entries when closed: ${entries}; when opened again: ${reopened})`
    );
  });

  // ============================================================================
  // The RAG Builder
  // ============================================================================
  const runState = (page) =>
    page.evaluate(() => {
      const card = document.getElementById('app-obj-RGB-ingestion-run');
      const launch = document.getElementById('app-obj-RGB-launch');
      return {
        shown: Boolean(card) && getComputedStyle(card).display !== 'none',
        text: card ? card.textContent.replace(/\s+/g, ' ').trim() : '',
        launchOff: Boolean(launch && launch.disabled),
      };
    });
  const manifestEnded = async (page) =>
    !(await page.isDisabled(rgb('manifest'))) && (await textOf(page, rgb('manifest'))) === 'Manifest setup' && (await textOf(page, rgb('save-result'))) !== '';
  const keepRunning = async (route) => {
    const id = route.request().url().split('/').pop();
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ id, state: 'running', results: { COMPUTE_SESSION: `rag-cs-${id} (rag-ctx)`, COMPUTE_JOB: `rag-cj-${id}` } }),
    });
  };

  await road('the line under the SAS Content folder after a manifest', async (page) => {
    await openPage(page, 'rag');
    await openSetup(page, 'rag-setup-1');
    await goStep(page, 'RGB', 'store');
    // Manifest setup is off until the stored setup is read, and page.click
    // waits for that: a click before it manifested the defaults, which the
    // validation refused, and the flow line was missing on its own setup.
    await page.click(rgb('manifest'));
    await waitUntil(() => manifestEnded(page), 'the manifest has ended', 60000);
    await atRest();
    const said = await textOf(page, rgb('save-result'));
    const line = /Studio flow: RAG Ingest - Policy Handbook/;
    const own = await textOf(page, rgb('pane-store'));
    await choose(page, 'RGB', rgb('setup'), 'rag-setup-ss');
    const other = await textOf(page, rgb('pane-store'));
    await createInDialog(page, 'RGB', rgb('setup'), 'app-obj-RGB-create-setup', 'Outcome setup');
    const created = await textOf(page, rgb('pane-store'));
    step(
      line.test(own) && !/Studio flow: RAG Ingest/.test(other) && !/Studio flow: RAG Ingest/.test(created),
      `Store & output: the line that names the flow of a manifest stands on its setup only - not on another one opened next, nor on a new one (own: ${line.test(own)}; another: ${/Studio flow: RAG Ingest/.test(other)}; new: ${/Studio flow: RAG Ingest/.test(created)}; the manifest said: "${clip(said)}")`
    );
  });

  await road('a create dialog of the RAG Builder after a creation', async (page) => {
    await openPage(page, 'rag');
    await choose(page, 'RGB', rgb('project'), 'rag-proj');
    await createInDialog(page, 'RGB', rgb('setup'), 'app-obj-RGB-create-setup', 'Outcome described', 'Handbook of the works council');
    await resetLog();
    const second = await createInDialog(page, 'RGB', rgb('setup'), 'app-obj-RGB-create-setup', 'Outcome plain');
    const created = (await getLog()).filter((entry) => entry.method === 'POST' && entry.url.startsWith('/modelRepository/models') && !entry.url.includes('/contents'));
    const sent = created.length ? JSON.parse(created[0].body).description : null;
    step(
      second.opened.name === '' && second.opened.description === '' && sent === '',
      `Create setup: after a creation the dialog opens empty, and the next setup is created with the description typed for it (opened with: ${JSON.stringify(second.opened)}; description sent: ${JSON.stringify(sent)})`
    );
  });

  await road('the banner of a failed run on the setup opened next', async (page) => {
    await openPage(page, 'rag');
    await openSetup(page, 'rag-setup-1');
    await post('/__ragfail', { run: 'made to fail by the suite' });
    await goStep(page, 'RGB', 'run');
    await page.click(rgb('launch'));
    await waitUntil(async () => /did not succeed/.test(await textOf(page, rgb('status'))), 'the banner of the failed run', 30000);
    await atRest();
    await choose(page, 'RGB', rgb('setup'), 'rag-setup-ss');
    const banner = await textOf(page, rgb('status'));
    step(banner === '', `the banner of a run that failed goes with its setup: the setup opened next shows none (banner: "${clip(banner)}")`);
    await post('/__ragfail');
  });

  // While the stored setup is read, the fields hold the defaults of a new
  // setup: a save or a manifest then would write those over it.
  await road('the buttons that write a setup while it is read', async (page) => {
    await openPage(page, 'rag');
    await choose(page, 'RGB', rgb('project'), 'rag-proj');
    let release;
    const released = new Promise((resolve) => (release = resolve));
    let held = 0;
    await page.route('**/modelRepository/models/rag-setup-1/contents**', async (route) => {
      if (route.request().method() === 'GET') {
        held += 1;
        await released;
      }
      await route.continue().catch(() => {});
    });
    await resetLog();
    await page.selectOption(rgb('setup'), 'rag-setup-1');
    await waitUntil(async () => held > 0, 'the setup is read');
    await goStep(page, 'RGB', 'store');
    const off = async () => ({
      save: await page.isDisabled(rgb('save')),
      manifest: await page.isDisabled(rgb('manifest')),
      documentation: await page.isDisabled(rgb('save-documentation')),
    });
    const whileRead = await off();
    await page.click(rgb('manifest'), { timeout: 800, force: true }).catch(() => {});
    await page.click(rgb('save'), { timeout: 800, force: true }).catch(() => {});
    await page.waitForTimeout(500);
    const writesWhileRead = (await getLog()).filter((entry) => entry.method !== 'GET').length;
    release();
    await waitUntil(async () => !(await page.isDisabled(rgb('manifest'))), 'the setup is read', 15000).catch(() => {});
    await atRest();
    const afterRead = await off();
    step(
      Object.values(whileRead).every(Boolean) && writesWhileRead === 0 && !Object.values(afterRead).some(Boolean),
      `Save setup, Manifest setup and Save documentation are off while the stored setup is read, a click then writes nothing, and they can be used once it is drawn (while read: ${JSON.stringify(whileRead)}; writes: ${writesWhileRead}; afterwards off: ${JSON.stringify(afterRead)})`
    );
  });

  await road('the Manifest button while the model version is asked for', async (page) => {
    await openPage(page, 'rag');
    await openSetup(page, 'rag-setup-1');
    await goStep(page, 'RGB', 'store');
    let versions = 0;
    let release;
    const released = new Promise((resolve) => (release = resolve));
    await page.route('**/modelRepository/models/rag-setup-1/modelVersions', async (route) => {
      versions += 1;
      if (versions === 1) await released;
      await route.continue().catch(() => {});
    });
    await page.click(rgb('manifest'));
    await waitUntil(async () => versions === 1, 'the model version is asked for', 60000);
    const off = await page.isDisabled(rgb('manifest'));
    await page.click(rgb('manifest'), { timeout: 800 }).catch(() => {});
    await page.waitForTimeout(800);
    release();
    await waitUntil(() => manifestEnded(page), 'the manifest has ended', 60000);
    await atRest();
    step(off && versions === 1, `Manifest setup: the button is off while the model version is asked for, and a click then starts no second manifest (off: ${off}; model versions asked for: ${versions})`);
  });

  for (const [what, stage, refuseIt] of [
    ['the job reference', 'generating the ingestion job', (request, state) => state.jobWritten && /filename="rag-setup\.json"/.test(request.postData() || '')],
    ['the copy of the Studio flow', 'generating the Studio flow', (request) => /filename="[^"]+\.flw"/.test(request.postData() || '')],
  ]) {
    await road(`Manifest setup, ${what} refused`, async (page) => {
      await openPage(page, 'rag');
      await openSetup(page, 'rag-setup-1');
      await goStep(page, 'RGB', 'store');
      const state = { jobWritten: false, refused: 0 };
      await page.route('**/jobDefinitions/definitions**', (route) => {
        if (route.request().method() !== 'GET') state.jobWritten = true;
        return route.continue();
      });
      await page.route('**/modelRepository/models/rag-setup-1/contents**', (route) => {
        if (route.request().method() === 'POST' && state.refused === 0 && refuseIt(route.request(), state)) {
          state.refused += 1;
          return refuse(route);
        }
        return route.continue();
      });
      await page.click(rgb('manifest'));
      await waitUntil(() => manifestEnded(page), 'the manifest has ended', 60000);
      await atRest();
      const note = await textOf(page, rgb('save-result'));
      step(
        state.refused === 1 && note.includes(`The manifest stopped while ${stage}`) && !/Setup manifested/.test(note),
        `Manifest setup, ${what} answered HTTP 500: the manifest stops while ${stage} and does not say "manifested" (note: "${clip(note)}")`
      );
    });
  }

  await road('Manifest setup, the model version refused', async (page) => {
    await openPage(page, 'rag');
    await openSetup(page, 'rag-setup-1');
    await goStep(page, 'RGB', 'store');
    await page.route('**/modelRepository/models/rag-setup-1/modelVersions', (route) => refuse(route));
    await page.click(rgb('manifest'));
    await waitUntil(() => manifestEnded(page), 'the manifest has ended', 60000);
    await atRest();
    const note = await textOf(page, rgb('save-result'));
    step(
      /recording a new model version failed/.test(note) && !/a new model version recorded/.test(note),
      `Manifest setup, the model version answered HTTP 500: the note says that recording the version failed (note: "${clip(note)}")`
    );
  });

  await road('a change of the setup list sent by a script while Save setup is out', async (page) => {
    await openPage(page, 'rag');
    await openSetup(page, 'rag-setup-1');
    await goStep(page, 'RGB', 'store');
    await page.route('**/modelRepository/models/**', async (route) => {
      if (route.request().method() !== 'GET') await sleep(1500);
      await route.continue().catch(() => {});
    });
    await resetLog();
    await page.click(rgb('save'));
    await page.waitForTimeout(300);
    await pickByScript(page, rgb('setup'), 'rag-setup-ss');
    const during = await page.inputValue(rgb('setup'));
    await waitUntil(async () => !(await page.isDisabled(rgb('save'))) && !(await page.$eval(rgb('selection'), (block) => block.disabled)), 'the save has ended', 30000);
    await atRest();
    const writtenTo = [...new Set((await getLog()).filter((entry) => entry.method !== 'GET').map((entry) => (/^\/modelRepository\/models\/([^/?]+)/.exec(entry.url) || [])[1]).filter(Boolean))];
    step(
      during === 'rag-setup-1' && (await page.inputValue(rgb('setup'))) === 'rag-setup-1' && writtenTo.length === 1 && writtenTo[0] === 'rag-setup-1',
      `Save setup: a change of the setup list sent by a script while the save is out is undone, and every write goes to the setup of the click (list right after the change: ${during}; written to: ${writtenTo.join(', ')})`
    );
  });

  for (const [how, fail] of notDeleted) {
    await road(`Delete project of the RAG Builder whose second setup is not deleted (${how})`, async (page) => {
      await openPage(page, 'rag');
      const projectName = `RAG partial ${how}`;
      const project = (await createInDialog(page, 'RGB', rgb('project'), 'app-obj-RGB-create-project', projectName)).id;
      const first = (await createInDialog(page, 'RGB', rgb('setup'), 'app-obj-RGB-create-setup', `RS one ${how}`)).id;
      const second = (await createInDialog(page, 'RGB', rgb('setup'), 'app-obj-RGB-create-setup', `RS two ${how}`)).id;
      // The setup that is open is the one that gets deleted.
      await choose(page, 'RGB', rgb('setup'), first);
      await page.route(`**/modelRepository/models/${second}`, (route) => (route.request().method() === 'DELETE' ? fail(route) : route.continue()));
      await page.click(rgb('delete-project'));
      await confirmDialog(page);
      await waitUntil(async () => /Deleting failed|got no usable answer/.test(await textOf(page, rgb('status'))), 'the banner', 20000);
      await atRest();
      const banner = await textOf(page, rgb('status'));
      const listedNow = await page.$$eval(`${rgb('setup')} option`, (options) => options.map((option) => option.value));
      const open = await page.inputValue(rgb('setup'));
      // A refusal says the deletion failed; a delete without an answer that
      // reading back shows not carried out says that, with no word of
      // permissions.
      const said = how === 'refused' ? banner.includes(`The project ${projectName} is still there.`) : banner.includes(`Delete project: ${NOT_CARRIED_OUT}`) && !banner.includes('permissions');
      step(
        !listedNow.includes(first) && listedNow.includes(second) && open !== first && (await page.inputValue(rgb('project'))) === project &&
          said && banner.includes(`Deleted from it: RS one ${how}.`) && banner.includes(`Still in it: RS two ${how}.`),
        `RAG Builder, Delete project whose second setup is ${how}: the list drops the setup that was deleted, which was open and is open no longer, and the banner names what was deleted and what is still in the project (listed: ${JSON.stringify(listedNow)}; open: ${open}; banner: "${banner.slice(0, 400)}")`
      );
      // The mock gives the id of a deleted model to the next one it creates:
      // the one that is left goes, or the next road would get its id twice.
      await fetch(`${BASE}/modelRepository/models/${second}`, { method: 'DELETE' });
    });
  }

  // Delete setup whose request gets no answer: read back as Delete
  // prompt-test is.
  for (const lost of ['not carried out', 'carried out']) {
    await road(`Delete setup whose request gets no answer (${lost})`, async (page) => {
      await openPage(page, 'rag');
      await choose(page, 'RGB', rgb('project'), 'rag-proj');
      const id = (await createInDialog(page, 'RGB', rgb('setup'), 'app-obj-RGB-create-setup', `RS cut ${lost}`)).id;
      await page.route(`**/modelRepository/models/${id}`, async (route) => {
        if (route.request().method() !== 'DELETE') return route.continue();
        if (lost === 'carried out') await route.fetch();
        return route.abort('failed');
      });
      // The toast of the creation is not one of the delete.
      const before = (await toasts(page)).length;
      await page.click(rgb('delete-setup'));
      await confirmDialog(page);
      // A delete that went through is said in a toast, one that did not in the banner.
      await waitUntil(async () => (await textOf(page, rgb('status'))) !== '' || (await toasts(page)).length > before, 'the banner or a toast', 20000).catch(() => {});
      await atRest();
      const banner = await textOf(page, rgb('status'));
      const said = (await toasts(page)).slice(before);
      const listedNow = await page.$$eval(`${rgb('setup')} option`, (options) => options.map((option) => option.value));
      const open = await page.inputValue(rgb('setup'));
      const seen = `banner: "${clip(banner)}"; toasts: ${JSON.stringify(said)}; listed: ${listedNow.includes(id)}; open: ${open}; page errors: ${JSON.stringify(pageErrors)}`;
      if (lost === 'not carried out') {
        step(
          banner.includes(`Delete setup: ${NOT_CARRIED_OUT}`) && !banner.includes('permissions') && listedNow.includes(id) && open === id,
          `Delete setup whose request gets no answer and was not carried out is read back: the banner says so, without a word of permissions, and the setup stays listed and open (${seen})`
        );
        await fetch(`${BASE}/modelRepository/models/${id}`, { method: 'DELETE' });
      } else {
        step(
          said.some((text) => text.startsWith('RAG setup deleted')) && banner === '' && !listedNow.includes(id) && open !== id && pageErrors.length === 0,
          `Delete setup that was carried out and whose answer was lost is read back: the setup leaves the list and is open no longer, and a toast says it was deleted (${seen})`
        );
      }
    });
  }

  // Save setup whose upload SAS Model Manager stored and whose answer was
  // lost: read back, the setup is saved.
  await road('Save setup whose upload of rag-setup.json is stored and its answer lost', async (page) => {
    await openPage(page, 'rag');
    await openSetup(page, 'rag-setup-1');
    let lostOnce = false;
    await page.route('**/modelRepository/models/rag-setup-1/contents?*', async (route) => {
      const body = route.request().postData() || '';
      if (lostOnce || route.request().method() !== 'POST' || !body.includes('filename="rag-setup.json"')) return route.continue();
      lostOnce = true;
      await route.fetch();
      return route.abort('failed');
    });
    await goStep(page, 'RGB', 'store');
    await page.click(rgb('save'));
    await waitUntil(async () => Boolean(await page.$(`${rgb('save-result')} .alert`)), 'the save has ended', 30000);
    await atRest();
    const result = await textOf(page, rgb('save-result'));
    step(
      lostOnce && result.startsWith('RAG setup saved') && (await textOf(page, rgb('status'))) === '',
      `Save setup whose upload of rag-setup.json is stored and its answer lost reads it back, finds what it sent, and says the setup is saved (lost: ${lostOnce}; result: "${clip(result)}")`
    );
  });

  // Launch waits for the question whether an ingestion of the setup runs -
  // for every one that is out. Opened, left and opened again while the first
  // question was out, the setup has two out, and the answer to the first
  // must not let Launch go while the second is still out.
  await road('a setup opened twice while its question about a running ingestion is out', async (page) => {
    await openPage(page, 'rag');
    await choose(page, 'RGB', rgb('project'), 'rag-proj');
    const asked = [];
    const answered = [];
    await page.route('**/jobExecution/jobs?*', async (route) => {
      const url = route.request().url();
      if (!/filter=/.test(url)) return route.continue();
      const index = asked.length;
      asked.push(Date.now());
      await sleep(index === 0 ? 4000 : 12000);
      await route.continue().catch(() => {});
      answered.push(index);
    });
    await goStep(page, 'RGB', 'setup');
    await page.selectOption(rgb('setup'), 'rag-setup-1');
    await waitUntil(async () => asked.length === 1, 'the first question is out', 15000);
    await page.selectOption(rgb('setup'), 'rag-setup-2');
    await page.waitForTimeout(500);
    await page.selectOption(rgb('setup'), 'rag-setup-1');
    await waitUntil(async () => asked.length === 2, 'two questions are out', 15000);
    await waitUntil(async () => answered.includes(0), 'the first question is answered', 15000);
    // A change of the lock asks the Launch button again.
    await waitUntil(async () => !(await page.isDisabled(rgb('save'))), 'Save setup can be used', 15000);
    await goStep(page, 'RGB', 'store');
    await page.click(rgb('save'));
    await waitUntil(async () => Boolean(await page.$(`${rgb('save-result')} .alert`)), 'the save has ended', 30000);
    const offWhileAsked = !answered.includes(1) && (await page.isDisabled(rgb('launch')));
    const secondOut = !answered.includes(1);
    await waitUntil(async () => answered.includes(1), 'the second question is answered', 20000);
    await page.waitForTimeout(500);
    const onAfter = !(await page.isDisabled(rgb('launch')));
    step(
      secondOut && offWhileAsked && onAfter,
      `a setup opened twice: after the first question about a running ingestion is answered, and a save has ended, Launch is still off while the second question is out, and usable once it is answered (second still out at the look: ${secondOut}; off then: ${offWhileAsked}; usable after: ${onAfter})`
    );
  });

  // A setup created and not read back: the warning says that its buttons
  // stay off, and Try again reads the setup, which turns them on.
  await road('a setup created whose reading after the creation fails, and Try again', async (page) => {
    await openPage(page, 'rag');
    await choose(page, 'RGB', rgb('project'), 'rag-proj');
    let created = false;
    let failed = false;
    await page.route('**/modelRepository/models', (route) => {
      if (route.request().method() === 'POST') created = true;
      return route.continue();
    });
    await page.route('**/modelRepository/models/*/contents?*', (route) => {
      if (!created || failed || route.request().method() !== 'GET') return route.continue();
      failed = true;
      return route.fulfill({ status: 500, contentType: 'application/json', body: '{"message":"refused by the suite"}' });
    });
    const { id } = await createInDialog(page, 'RGB', rgb('setup'), 'app-obj-RGB-create-setup', 'RS read late');
    await page.waitForTimeout(1500);
    const usable = async () => {
      const names = [];
      for (const [name, selector] of [['Save setup', rgb('save')], ['Manifest setup', rgb('manifest')], ['Save documentation', rgb('save-documentation')]]) {
        if (!(await page.isDisabled(selector))) names.push(name);
      }
      return names;
    };
    const warning = await textOf(page, rgb('status'));
    const before = await usable();
    step(
      failed && before.length === 0 && warning.includes('could not be read back') && warning.includes('Save setup, Manifest setup and Save documentation stay off until the setup is read: Try again reads it.'),
      `a setup created whose reading fails: its three buttons are off, and the warning says so and that Try again reads it (usable: ${before.join(', ') || 'none'}; warning: "${clip(warning)}")`
    );
    await page.click(rgb('retry-projects'));
    await waitUntil(async () => (await usable()).length === 3, 'the buttons are given back', 15000).catch(() => {});
    await atRest();
    const after = await usable();
    step(
      after.length === 3 && (await page.inputValue(rgb('setup'))) === id && !/could not be read/.test(await textOf(page, rgb('status'))),
      `and Try again reads the setup: its buttons can be used, and nothing says it could not be read (usable: ${after.join(', ') || 'none'}; open: ${await page.inputValue(rgb('setup'))}; banner: "${clip(await textOf(page, rgb('status')))}")`
    );
    await fetch(`${BASE}/modelRepository/models/${id}`, { method: 'DELETE' });
  });

  // An action whose button is off while it runs starts once - whichever way
  // it is asked for. Enter in the question started a second and a third
  // retrieval test, each with its job definition and its job, while Run test
  // was off; a click a script sends to a control that is off reached its
  // listeners. Every control that is off at two moments - a test running, a
  // save of the setup out - is sent such a click: none reaches a listener.
  const scriptedClicks = (page) =>
    page.evaluate(() => {
      const controls = [...document.querySelectorAll('button, input, select, textarea')].filter((control) => control.matches(':disabled'));
      const reached = [];
      for (const control of controls) {
        const mark = () => reached.push(control.id || control.textContent.trim().slice(0, 30) || control.tagName);
        control.addEventListener('click', mark);
        control.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
        control.removeEventListener('click', mark);
      }
      return { off: controls.length, reached };
    });
  await road('a retrieval test asked for by Enter and by a script while one runs', async (page) => {
    await openPage(page, 'rag');
    await openSetup(page, 'rag-setup-1');
    await goStep(page, 'RGB', 'run');
    await page.click(rgb('test'));
    await page.waitForSelector(rgb('test-question'), { state: 'visible' });
    await page.fill(rgb('test-question'), 'What does the handbook say about travel?');
    await page.route('**/jobExecution/jobs/ragtest-*', async (route) => {
      await sleep(2500);
      await route.continue().catch(() => {});
    });
    await resetLog();
    await page.focus(rgb('test-question'));
    await page.keyboard.press('Enter');
    await page.keyboard.press('Enter');
    await page.keyboard.press('Enter');
    await waitUntil(async () => page.isDisabled(rgb('test-run')), 'Run test is off', 5000);
    const during = await scriptedClicks(page);
    await page.keyboard.press('Enter');
    await waitUntil(async () => (await getLog()).some((entry) => entry.method === 'DELETE' && entry.url.startsWith('/jobDefinitions/definitions/') && entry.status !== null), 'the test has ended', 60000);
    await atRest();
    const log = await getLog();
    const definitions = log.filter((entry) => entry.method === 'POST' && entry.url.startsWith('/jobDefinitions/definitions')).length;
    const jobs = log.filter((entry) => entry.method === 'POST' && entry.url.startsWith('/jobExecution/jobs')).length;
    step(
      definitions === 1 && jobs === 1,
      `Enter in the question while a retrieval test runs starts no second test: one question, one job definition and one job (job definitions written: ${definitions}; jobs launched: ${jobs})`
    );
    step(
      during.off > 0 && during.reached.length === 0,
      `while the test runs, a click a script sends to any of the ${during.off} controls that are off reaches none of their listeners (reached: ${JSON.stringify(during.reached)})`
    );
    // While a save of the setup is out the selection and every button that
    // writes are off.
    await page.route('**/modelRepository/models/rag-setup-1/contents?*', async (route) => {
      if (route.request().method() === 'POST') await sleep(2500);
      await route.continue().catch(() => {});
    });
    await goStep(page, 'RGB', 'store');
    await page.click(rgb('save'));
    await waitUntil(async () => page.isDisabled(rgb('project')), 'the selection is locked', 5000);
    const writing = await scriptedClicks(page);
    await waitUntil(async () => Boolean(await page.$(`${rgb('save-result')} .alert`)), 'the save has ended', 30000);
    await atRest();
    step(
      writing.off > 0 && writing.reached.length === 0,
      `while a save is out, a click a script sends to any of the ${writing.off} controls that are off reaches none of their listeners (reached: ${JSON.stringify(writing.reached)})`
    );
  });

  // A launch whose answer is lost after Job Execution took it: the read back
  // finds the job among those that are running - the name, the definition
  // and the arguments are members of its request (`jobRequest`), as the mock
  // and Job Execution list them - and the page follows it.
  const loseLaunchAnswer = async (page) => {
    let lost = 0;
    await page.route('**/jobExecution/jobs', async (route) => {
      if (route.request().method() !== 'POST' || lost > 0) return route.continue();
      lost += 1;
      await route.fetch();
      return route.abort('failed');
    });
    return () => lost;
  };
  await road('Launch ingestion whose answer is lost after the job was launched', async (page) => {
    await openPage(page, 'rag');
    await openSetup(page, 'rag-setup-1');
    await goStep(page, 'RGB', 'run');
    const lost = await loseLaunchAnswer(page);
    await page.click(rgb('launch'));
    await waitUntil(async () => /completed/.test((await runState(page)).text) || (await textOf(page, rgb('status'))) !== '', 'the run card or the banner', 40000).catch(() => {});
    await atRest();
    const card = await runState(page);
    const banner = await textOf(page, rgb('status'));
    step(
      lost() === 1 && card.shown && /completed/.test(card.text) && banner === '' && !card.launchOff,
      `Launch ingestion whose answer is lost after Job Execution launched the job reads it back among the running jobs and follows it to its end (answers lost: ${lost()}; card: ${JSON.stringify(card).slice(0, 220)}; banner: "${clip(banner)}")`
    );
  });
  await road('Run test whose answer is lost after the job was launched', async (page) => {
    await openPage(page, 'rag');
    await openSetup(page, 'rag-setup-1');
    await goStep(page, 'RGB', 'run');
    await page.click(rgb('test'));
    await page.waitForSelector(rgb('test-question'), { state: 'visible' });
    await page.fill(rgb('test-question'), 'What does the handbook say about leave?');
    const lost = await loseLaunchAnswer(page);
    await page.click(rgb('test-run'));
    await waitUntil(async () => (await page.$$(`${rgb('test-results')} tbody tr`)).length > 0 || /not known/.test(await textOf(page, rgb('test-status'))), 'the chunks or the line that it is not known', 40000).catch(() => {});
    await atRest();
    const rows = (await page.$$(`${rgb('test-results')} tbody tr`)).length;
    const status = await textOf(page, rgb('test-status'));
    step(
      lost() === 1 && rows > 0 && !/not known/.test(status),
      `Run test whose answer is lost after Job Execution launched the job reads it back among the running jobs and shows what it returned (answers lost: ${lost()}; chunks: ${rows}; status: "${clip(status)}")`
    );
  });

  await road('a running ingestion on a page loaded after its launch', async (page) => {
    await openPage(page, 'rag');
    await openSetup(page, 'rag-setup-1');
    await goStep(page, 'RGB', 'run');
    await page.route('**/jobExecution/jobs/ragrun-*', keepRunning);
    await page.click(rgb('launch'));
    await waitUntil(async () => /running/.test((await runState(page)).text), 'the ingestion runs', 30000);
    await atRest();
    // The same page loaded again: it knows nothing of the launch.
    await page.goto('about:blank');
    await page.goto(APP_URL);
    await page.waitForSelector('#LPB-project-dropdown', { timeout: 20000 });
    await page.click('#LPB-kind-rag');
    await page.waitForSelector('#app-area-rag:not([hidden]) #app-obj-RGB-project', { timeout: 20000 });
    await atRest();
    await openSetup(page, 'rag-setup-1');
    await goStep(page, 'RGB', 'run');
    await waitUntil(async () => (await runState(page)).shown, 'the run is shown', 10000).catch(() => {});
    const reloaded = await runState(page);
    step(
      reloaded.shown && /running/.test(reloaded.text) && reloaded.launchOff,
      `after the page is loaded again, the setup whose ingestion still runs shows the run, and Launch is off (${JSON.stringify(reloaded).slice(0, 220)})`
    );
    await page.unroute('**/jobExecution/jobs/ragrun-*', keepRunning);
    await waitUntil(async () => /completed/.test((await runState(page)).text), 'the run has ended', 40000).catch(() => {});
    await atRest();
    const ended = await runState(page);
    step(/completed/.test(ended.text) && !ended.launchOff, `and when the job has ended there, the card says so and Launch can be used again (${JSON.stringify(ended).slice(0, 220)})`);
  });

  await road('an ingestion whose state cannot be read', async (page) => {
    await openPage(page, 'rag');
    await openSetup(page, 'rag-setup-1');
    await goStep(page, 'RGB', 'run');
    let polls = 0;
    await page.route('**/jobExecution/jobs/ragrun-*', (route) => {
      polls += 1;
      return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ httpStatusCode: 404, message: 'no such job' }) });
    });
    await page.click(rgb('launch'));
    await waitUntil(async () => polls >= 3, 'three polls', 30000);
    await page.waitForTimeout(800);
    const card = await runState(page);
    const pollsThen = polls;
    await page.waitForTimeout(6000);
    step(
      !card.launchOff && /no longer followed/.test(card.text) && polls === pollsThen,
      `an ingestion whose state cannot be read three times in a row is no longer followed: the card says so, Launch is offered again and no further poll is made (${JSON.stringify(card).slice(0, 260)}; polls: ${pollsThen} then ${polls})`
    );
    // The job of the mock is left running; the next road that opens this
    // setup would pick it up. Moved on so that it has ended.
    const jobs = (await (await fetch(BASE + '/__ragstate')).json()).jobs.filter((job) => job.kind === 'ingestion');
    const id = jobs[jobs.length - 1].id;
    await fetch(`${BASE}/jobExecution/jobs/${id}`);
    await fetch(`${BASE}/jobExecution/jobs/${id}`);
  });

  await browser.close();

  // The page made no request around its HTTP client.
  const direct = await (await fetch(BASE + '/__direct')).json();
  step(direct.length === 0, `no request of the browser to a SAS Viya service went around viyaFetch (${JSON.stringify(direct).slice(0, 200)})`);

  console.log('\n===== SUMMARY =====');
  results.forEach((line) => console.log(line));
  const failed = results.filter((line) => line.startsWith('FAIL')).length;
  console.log(`\n${results.length - failed} checks passed, ${failed} failed`);
})().catch((error) => {
  console.log('VERIFY FAILED', error);
  process.exit(1);
});
