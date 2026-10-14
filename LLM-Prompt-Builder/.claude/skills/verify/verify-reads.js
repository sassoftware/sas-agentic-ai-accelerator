/**
 * A read that failed is not taken for what is stored. Run against a FRESH
 * mock instance.
 *
 * A prompt-test whose tracker could not be read was drawn as one without
 * runs, and Save Experiments then replaced the stored runs with the new one;
 * a RAG setup whose contents could not be listed was drawn with the
 * defaults, and Save documentation wrote them over it; the delete of one
 * optimization run deleted every run when the tracker could not be read; a
 * job source answered with an error became the code of a job definition.
 *
 * The mechanism: getFileContent throws for a file it could not read (no
 * answer, or no success), and every caller of a read of stored content says
 * what a failure does. The census below enumerates those calls from the
 * sources - every call of getModelContents, getFileContent, getModelDetails,
 * readOptimizationEntries and readStoredSetup in src/objects, by the function
 * it stands in - and names, for each, what a failed read leads to. A call
 * that is added, moved or gone fails the census until it is named here.
 * (verify-outcomes.js has the roads of the delete of an optimization run.)
 *
 * The roads, each on a page of its own, make one read fail - refused, or
 * without an answer - and look at the page and at what it writes:
 *   - a prompt-test whose saved runs cannot be read says so, and Save
 *     Experiments uploads nothing over them; opened again, its runs are back;
 *   - the same for a decision template;
 *   - a RAG setup whose contents, rag-setup.json or (never saved) attributes
 *     cannot be read says so and keeps Save setup, Manifest setup and Save
 *     documentation off; opened again, they can be used;
 *   - Save documentation whose read of the stored setup fails writes nothing;
 *   - a manifest whose ingestion job source cannot be read stops before it
 *     writes the job definition.
 *
 * The suite goes on after a check that failed and after a road that stopped.
 * The mock is found at VERIFY_BASE (default http://localhost:4173); the
 * sources at VERIFY_PROJECT (default: the project around this folder), read
 * with the parser of rolldown from its node_modules (`npm ci` first).
 */
const { chromium } = require('playwright');
const fetch = require('./mock-fetch.cjs');
const fs = require('fs');
const path = require('path');
const { createRequire } = require('module');

const BASE = (process.env.VERIFY_BASE || 'http://localhost:4173').replace(/\/+$/, '');
const PROJECT = process.env.VERIFY_PROJECT || path.resolve(__dirname, '..', '..', '..');
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
const clip = (value) => (String(value).length > 140 ? `${String(value).slice(0, 140)}...` : String(value));

const APP_URL =
  `${BASE}/?modelRepositoryID=repo-1&llmProjectID=llm-proj&SCREndpoint=${BASE}/scr` +
  '&enableOptimization=true&computeContext=ctx&optimizeJobProgram=/Public/Jobs/Optimize-Prompt-DSPy&minOptimizeSamples=1' +
  '&enableRag=true&embeddingProjectID=emb-proj&ragComputeContext=rag-ctx';
const lpb = (suffix) => `#app-obj-LPB-${suffix}`;
const rgb = (suffix) => `#app-obj-RGB-${suffix}`;

// ============================================================================
// The census: every read of stored content, and what its failure does
// ============================================================================
const READS = ['getModelContents', 'getFileContent', 'getModelDetails', 'readOptimizationEntries', 'readStoredSetup'];
const CENSUS = {
  'prompt-builder.ts': {
    'loadPromptDocumentation -> getModelDetails': [1, 'null or a throw: the documentation section stays empty, and its Save documentation off'],
    'promptBuilderPromptSelectorDropdown.onchange -> getModelContents': [1, 'the runs of the prompt-test are not read: the page says so, and Save Experiments (with what saves first) writes nothing (road below)'],
    'promptBuilderPromptSelectorDropdown.onchange -> getFileContent': [1, 'as the listing (road below)'],
    'buildPromptBuilder -> getModelContents': [2, 'an LLM or a decision model is offered without its options; nothing stored is written from them'],
    'buildPromptBuilder -> getFileContent': [2, 'as the listing'],
    'buildPromptBuilder -> getModelDetails': [1, 'a decision model card without the attributes of its registration; nothing is written from them'],
    'ensureLLMCostAttributes -> getModelDetails': [1, 'the LLM has no cost attributes: a manifest copies none, and writes no attribute empty'],
    'promptBuilderSaveExperiments -> getModelContents': [1, 'throws: the save stops with its line, before the upload'],
    'promptBulderCreateBestPromptModel -> getModelContents': [1, 'throws: the manifest stops with its line'],
    'readOptimizationEntries -> getModelContents': [1, 'null - never an empty list, which is a model without a tracker'],
    'readOptimizationEntries -> getFileContent': [1, 'as the listing'],
    'readOptimizationEntry -> readOptimizationEntries': [1, 'null: no entry of the job is drawn; nothing is written'],
    'refreshOptimizationHistory -> readOptimizationEntries': [1, 'null: the history lists no run; nothing is written from it'],
    'deleteOptimizationRun -> readOptimizationEntries': [1, 'null: the delete writes nothing and says it failed (verify-outcomes.js)'],
    'deleteOptimizationRun -> getModelContents': [1, 'throws: the delete stops before the tracker is written'],
  },
  'rag-builder.ts': {
    'loadPromptContract -> getModelContents': [1, 'the prompt has no contract: the enrichment says so, and the validation refuses a save that uses it'],
    'loadPromptContract -> getFileContent': [1, 'as the listing'],
    'saveDocumentation -> readStoredSetup': [1, 'throws: nothing is written, and the banner says the save failed (road below)'],
    'removeStaleDocumentation -> getModelContents': [1, 'throws: the obsolete documentation.md is not deleted; nothing else'],
    'readStoredSetup -> getModelContents': [1, 'throws to its caller'],
    'readStoredSetup -> getFileContent': [1, 'throws to its caller'],
    'loadSetup -> getModelContents': [1, 'the setup is not read: the page says so, and Save setup, Manifest setup and Save documentation stay off (road below)'],
    'loadSetup -> getFileContent': [1, 'as the listing (road below)'],
    'loadSetup -> getModelDetails': [1, 'as the listing, for a setup never saved (road below)'],
    'generateIngestionJob -> getFileContent': [1, 'throws: the manifest stops before the job definition is written (road below)'],
    'registerRetrievalModel -> getFileContent': [1, 'throws: the manifest stops before the score code is written'],
    'runRetrievalTest -> getFileContent': [1, 'throws: the test says it could not be run, and no job definition is written'],
  },
};

/** The calls of READS in a source, as "<function> -> <read>" with a count. */
function readsInSource(file, text) {
  const projectRequire = createRequire(path.join(PROJECT, 'package.json'));
  const { parseSync } = projectRequire('rolldown/utils');
  const { program, errors } = parseSync(file, text, { lang: 'ts' });
  if (errors.length) throw new Error(`${file} does not parse: ${errors[0].message}`);
  const source = (node) => text.slice(node.start, node.end);
  const isFunction = (node) => node && /^(FunctionExpression|ArrowFunctionExpression)$/.test(node.type);
  // The name a node gives the code inside it, if it is a named function.
  const nameOf = (node) => {
    if (node.type === 'FunctionDeclaration' && node.id) return node.id.name;
    if (node.type === 'VariableDeclarator' && isFunction(node.init) && node.id.type === 'Identifier') return node.id.name;
    if (node.type === 'AssignmentExpression' && isFunction(node.right)) return source(node.left);
    if ((node.type === 'Property' || node.type === 'MethodDefinition') && isFunction(node.value) && node.key.type === 'Identifier') return node.key.name;
    if (
      node.type === 'CallExpression' && node.callee.type === 'MemberExpression' && node.callee.property.name === 'addEventListener' &&
      node.arguments[0] && node.arguments[0].type === 'Literal' && isFunction(node.arguments[1])
    ) {
      return `${source(node.callee.object)}:${node.arguments[0].value}`;
    }
    return null;
  };
  const counts = {};
  const walk = (node, name) => {
    if (!node || typeof node.type !== 'string') return;
    const here = nameOf(node) || name;
    if (node.type === 'CallExpression' && node.callee.type === 'Identifier' && READS.includes(node.callee.name)) {
      const key = `${here || '(module)'} -> ${node.callee.name}`;
      counts[key] = (counts[key] || 0) + 1;
    }
    for (const [key, value] of Object.entries(node)) {
      if (key === 'parent') continue;
      if (Array.isArray(value)) value.forEach((child) => walk(child, here));
      else if (value && typeof value === 'object') walk(value, here);
    }
  };
  walk(program, null);
  return counts;
}

function census() {
  for (const [file, known] of Object.entries(CENSUS)) {
    let counts = null;
    let problem = '';
    try {
      counts = readsInSource(file, fs.readFileSync(path.join(PROJECT, 'src', 'objects', file), 'utf8'));
    } catch (error) {
      problem = error.message;
    }
    if (!counts) {
      step(false, `${file}: the reads of stored content are known to this suite (${problem}; set VERIFY_PROJECT, run npm ci)`);
      continue;
    }
    const unknown = Object.keys(counts).filter((key) => !known[key]);
    const gone = Object.keys(known).filter((key) => !counts[key]);
    const miscounted = Object.keys(known).filter((key) => counts[key] && counts[key] !== known[key][0]).map((key) => `${key}: ${counts[key]}, not ${known[key][0]}`);
    step(
      unknown.length === 0 && gone.length === 0 && miscounted.length === 0,
      `${file}: every read of stored content is named here with what its failure does (${Object.keys(counts).length} calls; not named here: ${unknown.join('; ') || 'none'}; named but not found: ${gone.join('; ') || 'none'}; counted otherwise: ${miscounted.join('; ') || 'none'})`
    );
  }
  // getFileContent hands back no failed read: the one mechanism the census
  // leans on, read from its source.
  const api = fs.readFileSync(path.join(PROJECT, 'src', 'api', 'files-api.ts'), 'utf8');
  step(
    /if \(!response\.ok\) throw /.test(api) && !/new Response\(/.test(api),
    'getFileContent throws for a file it could not read, and makes up no answer of its own'
  );
}

(async () => {
  census();

  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  let pageErrors = [];
  const newPage = async () => {
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
    return page;
  };
  const road = async (name, run) => {
    console.log(`---- ${name}`);
    pageErrors = [];
    const page = await newPage();
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
  // One request that matches `match` fails - refused, or without an answer;
  // the requests after it go through.
  const failOnce = async (page, match, how) => {
    let failed = false;
    await page.route(
      (url) => match(new URL(url)),
      (route) => {
        if (failed || route.request().method() !== 'GET') return route.continue();
        failed = true;
        if (how === 'abort') return route.abort('failed');
        return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ httpStatusCode: 500, message: 'refused by the suite' }) });
      }
    );
  };
  const savedRuns = (page) => page.$$eval(`${lpb('pet')} .pet-run-delete`, (buttons) => buttons.length);
  const UNREAD_RUNS = 'The saved runs of this prompt-test could not be read from SAS Model Manager';
  const UNREAD_SETUP = 'The stored setup could not be read from SAS Model Manager';
  const trackerUploads = (log) =>
    log.filter((entry) => entry.method === 'POST' && /\/contents(\?|$)/.test(entry.url) && entry.body.includes('filename="Prompt-Experiment-Tracker.json"'));

  // ==========================================================================
  // The Prompt Builder: a prompt-test whose saved runs cannot be read
  // ==========================================================================
  for (const how of ['refused', 'abort']) {
    await road(`a prompt-test whose saved runs cannot be read (${how})`, async (page) => {
      await openPage(page);
      await choose(page, 'LPB', '#LPB-project-dropdown', 'proj-1');
      await failOnce(page, (url) => url.pathname === '/files/files/trk-1/content', how);
      await choose(page, 'LPB', '#LPB-prompt-dropdown', 'model-used');
      await waitUntil(async () => (await toasts(page)).length > 0, 'a toast', 15000).catch(() => {});
      const line = await textOf(page, lpb('pet-save-only-result'));
      const said = await toasts(page);
      step(
        line.startsWith(UNREAD_RUNS) && said.some((text) => text.startsWith(UNREAD_RUNS)) && (await savedRuns(page)) === 0,
        `a prompt-test whose saved runs cannot be read (${how}) says so beside Save Experiments and in a toast, and shows no run (line: "${clip(line)}"; toasts: ${JSON.stringify(said)}; runs shown: ${await savedRuns(page)})`
      );
      // A run, and the save a person then clicks.
      await goStep(page, 'LPB', 'build');
      await page.fill(lpb('user-prompt'), 'A question typed after the failed read');
      await page.check('#model0');
      await page.click(lpb('run-experiment'));
      await waitUntil(async () => (await page.$$(`${lpb('pet')} .accordion-item`)).length > 0, 'the run is drawn', 30000);
      await waitUntil(async () => (await textOf(page, lpb('run-experiment'))) === 'Run Experiments', 'the run has ended', 30000);
      await atRest();
      await resetLog();
      await page.click(lpb('pet-save-button'));
      await waitUntil(async () => (await textOf(page, lpb('pet-save-button'))) === 'Save Experiments', 'the save has ended', 30000);
      await atRest();
      const log = await getLog();
      const writes = log.filter((entry) => entry.method !== 'GET').map((entry) => `${entry.method} ${entry.url}`);
      const after = await textOf(page, lpb('pet-save-only-result'));
      step(
        trackerUploads(log).length === 0 && writes.length === 0 && after.startsWith(UNREAD_RUNS),
        `Save Experiments on a prompt-test whose saved runs were not read writes nothing over them and says why (writes: ${JSON.stringify(writes)}; line: "${clip(after)}")`
      );
      // Opened again, the runs are read.
      await choose(page, 'LPB', '#LPB-prompt-dropdown', 'model-free');
      await choose(page, 'LPB', '#LPB-prompt-dropdown', 'model-used');
      await waitUntil(async () => (await savedRuns(page)) > 0, 'the runs are drawn', 15000).catch(() => {});
      const again = await textOf(page, lpb('pet-save-only-result'));
      step(
        (await savedRuns(page)) > 0 && again === '',
        `the prompt-test opened again shows its saved runs, and no line that they were not read (runs: ${await savedRuns(page)}; line: "${clip(again)}")`
      );
      step(pageErrors.length === 0, `a prompt-test whose saved runs cannot be read (${how}): no uncaught page errors (got: ${pageErrors.join(' | ') || 'none'})`);
    });
  }

  await fetch(BASE + '/__decision', { method: 'POST' });
  await road('a decision template whose saved runs cannot be read', async (page) => {
    await openPage(page, 'decision');
    await choose(page, 'LPB', '#LPB-project-dropdown', 'proj-2');
    await failOnce(page, (url) => url.pathname === '/files/files/dtrk-1/content', 'refused');
    await choose(page, 'LPB', '#LPB-prompt-dropdown', 'model-dec');
    await waitUntil(async () => (await toasts(page)).length > 0, 'a toast', 15000).catch(() => {});
    const line = await textOf(page, lpb('pet-save-only-result'));
    step(
      line.startsWith(UNREAD_RUNS) && (await toasts(page)).some((text) => text.startsWith(UNREAD_RUNS)) && (await savedRuns(page)) === 0,
      `a decision template whose saved runs cannot be read says so and shows no run (line: "${clip(line)}"; runs shown: ${await savedRuns(page)})`
    );
  });

  // ==========================================================================
  // The RAG Builder: a setup that cannot be read
  // ==========================================================================
  const SETUP_WRITES = [['Save setup', rgb('save')], ['Manifest setup', rgb('manifest')], ['Save documentation', rgb('save-documentation')]];
  const usableWrites = async (page) => {
    const usable = [];
    for (const [name, selector] of SETUP_WRITES) if (!(await page.isDisabled(selector))) usable.push(name);
    return usable;
  };
  const banner = (page) => textOf(page, rgb('status'));
  const ragSetupFile = async (setup) =>
    ((await (await fetch(`${BASE}/modelRepository/models/${setup}/contents`)).json()).items || []).find((item) => item.name === 'rag-setup.json').fileUri;
  const unreadRoads = [
    ['the contents of the setup cannot be listed', 'rag-setup-1', async () => (url) => url.pathname === '/modelRepository/models/rag-setup-1/contents'],
    ['its rag-setup.json cannot be read', 'rag-setup-1', async () => { const file = await ragSetupFile('rag-setup-1'); return (url) => url.pathname === `${file}/content`; }],
    ['the attributes of a setup never saved cannot be read', 'rag-setup-2', async () => (url) => url.pathname === '/modelRepository/models/rag-setup-2'],
  ];
  for (const [what, setup, matcher] of unreadRoads) {
    for (const how of ['refused', 'abort']) {
      await road(`a RAG setup: ${what} (${how})`, async (page) => {
        await openPage(page, 'rag');
        await choose(page, 'RGB', rgb('project'), 'rag-proj');
        await failOnce(page, await matcher(), how);
        await choose(page, 'RGB', rgb('setup'), setup);
        await waitUntil(async () => (await banner(page)) !== '', 'the banner', 15000).catch(() => {});
        // The buttons are given back a moment after the reading - as late as
        // that, they have to be off still.
        await page.waitForTimeout(1500);
        const usable = await usableWrites(page);
        const said = await banner(page);
        const kind = await page.$$eval(`${rgb('status')} .alert`, (alerts) => alerts.map((alert) => [...alert.classList].find((name) => /^alert-/.test(name))).join(','));
        step(
          usable.length === 0 && said.startsWith(UNREAD_SETUP) && kind === 'alert-danger' && pageErrors.length === 0,
          `a RAG setup that cannot be read (${what}, ${how}) says so and keeps the buttons that write it off (usable: ${usable.join(', ') || 'none'}; banner: ${kind} "${clip(said)}"; page errors: ${pageErrors.join(' | ') || 'none'})`
        );
        await choose(page, 'RGB', rgb('setup'), setup === 'rag-setup-1' ? 'rag-setup-2' : 'rag-setup-1');
        await choose(page, 'RGB', rgb('setup'), setup);
        await waitUntil(async () => (await usableWrites(page)).length === 3, 'the buttons are given back', 15000).catch(() => {});
        step(
          (await usableWrites(page)).length === 3 && !(await banner(page)).startsWith(UNREAD_SETUP),
          `the RAG setup opened again is read, and its buttons can be used (usable: ${(await usableWrites(page)).join(', ') || 'none'}; banner: "${clip(await banner(page))}")`
        );
      });
    }
  }

  await road('Save documentation whose read of the stored setup fails', async (page) => {
    await openPage(page, 'rag');
    await choose(page, 'RGB', rgb('project'), 'rag-proj');
    await choose(page, 'RGB', rgb('setup'), 'rag-setup-1');
    await waitUntil(async () => !(await page.isDisabled(rgb('save-documentation'))), 'Save documentation can be used', 15000);
    const section = `${rgb('pane-setup')} .pb-doc-section`;
    if (!(await page.$eval(section, (details) => details.open))) await page.click(`${section} > summary`);
    await page.fill(rgb('setup-doc-modelPurpose'), 'Typed before the failed read');
    const file = await ragSetupFile('rag-setup-1');
    await failOnce(page, (url) => url.pathname === `${file}/content`, 'refused');
    await resetLog();
    await page.click(rgb('save-documentation'));
    await page.waitForTimeout(300);
    await waitUntil(async () => !(await page.$(`${rgb('save-documentation')} .spinner-border`)), 'the save has ended', 30000);
    await atRest();
    const writes = (await getLog()).filter((entry) => entry.method !== 'GET').map((entry) => `${entry.method} ${entry.url}`);
    const said = await banner(page);
    step(
      writes.length === 0 && said.startsWith('Saving the documentation failed'),
      `Save documentation whose read of the stored rag-setup.json fails writes neither the file nor the attributes, and says it failed (writes: ${JSON.stringify(writes)}; banner: "${clip(said)}")`
    );
  });

  await road('a manifest whose ingestion job source cannot be read', async (page) => {
    await openPage(page, 'rag');
    await choose(page, 'RGB', rgb('project'), 'rag-proj');
    await choose(page, 'RGB', rgb('setup'), 'rag-setup-1');
    await failOnce(page, (url) => url.pathname === '/files/files/rag-src-ingest/content', 'refused');
    await goStep(page, 'RGB', 'store');
    await resetLog();
    await page.click(rgb('manifest'));
    await waitUntil(
      async () => !(await page.isDisabled(rgb('manifest'))) && (await textOf(page, rgb('manifest'))) === 'Manifest setup' && (await textOf(page, rgb('save-result'))) !== '',
      'the manifest has ended',
      60000
    );
    await atRest();
    const jobWrites = (await getLog()).filter((entry) => entry.method !== 'GET' && entry.url.startsWith('/jobDefinitions/')).map((entry) => `${entry.method} ${entry.url}`);
    const said = await textOf(page, rgb('save-result'));
    step(
      jobWrites.length === 0 && said.includes('The manifest stopped while generating the ingestion job'),
      `a manifest whose ingestion job source cannot be read stops before it writes a job definition (job definition writes: ${JSON.stringify(jobWrites)}; result: "${clip(said)}")`
    );
  });

  // ---- every request to SAS Viya went through the HTTP client of the app -------
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
