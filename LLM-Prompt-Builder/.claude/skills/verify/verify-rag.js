/**
 * Drives the RAG kind of the built Prompt Builder (served by mock-server.js):
 * the kind is offered only when it is switched on, choosing it swaps the page
 * to the RAG Builder's four steps and back without losing the prompt side's
 * state, and a RAG setup is created, saved, manifested, ingested and asked a
 * question. Run against a FRESH mock instance.
 *
 * The mock is found at VERIFY_BASE (default http://localhost:4173). It serves
 * the RAG runtime (the ingestion job, the retrieval probe, the retrieval model
 * template and the custom steps) from the repository; start it with
 * MOCK_REPO_ROOT set to the repository root when it runs as a copy elsewhere.
 * The manifested retrieve_context.py is compiled with a real Python
 * interpreter (python-check.cjs, which has to sit next to this file).
 *
 * The suite is a list of sections, each on a page of its own:
 *   offered   the RAG kind exists only with enableRag=true
 *   shell     the two flows: steps, hidden areas, state kept, one heading
 *   flow      project, setup, save, manifest, ingestion, retrieval test, delete
 *   usage     what the two delete dialogs say about decisions that use a setup
 *   selection a delete acts on what was selected at the click; a project that
 *             was created although its listing failed
 *   withheld  a stored setup on a vector database the deployment withholds
 *   failure   a RAG project listing that fails stays inside the RAG area
 *   gates     credentialDomain=none; the ingestion compute context
 *   ragonly   a page without the prompt side shows the RAG flow alone
 * VERIFY_RAG_SECTIONS (comma separated) runs a subset; the summary names the
 * sections that ran, so a partial run cannot pass for a full one.
 */
const { chromium } = require('playwright');
const { resolvePython, compilePython } = require('./python-check.cjs');

const BASE = (process.env.VERIFY_BASE || 'http://localhost:4173').replace(/\/+$/, '');
const WANTED = (process.env.VERIFY_RAG_SECTIONS || '').split(',').map((name) => name.trim()).filter(Boolean);
const results = [];
const sectionsRun = [];

function step(ok, label) {
  results.push(`${ok ? 'OK ' : 'FAIL'}  ${label}`);
  console.log(`${ok ? 'OK ' : 'FAIL'}  ${label}`);
  if (!ok) process.exitCode = 1;
}
function assert(cond, label) {
  step(Boolean(cond), label);
  if (!cond) throw new Error('assertion failed: ' + label);
}
const getLog = async () => (await fetch(BASE + '/__log')).json();
const resetLog = async () => fetch(BASE + '/__reset', { method: 'POST' });
const ragState = async () => (await fetch(BASE + '/__ragstate')).json();
const armFailure = (failures) =>
  fetch(BASE + '/__ragfail', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(failures) });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitUntil(fn, label, timeout = 8000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await fn()) return;
    await sleep(100);
  }
  throw new Error('timeout waiting for: ' + label);
}
// The one file of a multipart upload, cut at the boundary: a Python or SAS
// file may itself hold a line that starts with two dashes.
function multipartFile(body) {
  const boundary = (/^(--[^\r\n]+)\r\n/.exec(body) || [])[1];
  const head = /filename="([^"]*)"\r\n(?:Content-Type: [^\r\n]*\r\n)?\r\n/.exec(body);
  if (!boundary || !head) return '';
  const start = head.index + head[0].length;
  const end = body.indexOf('\r\n' + boundary, start);
  return body.slice(start, end < 0 ? undefined : end);
}
// Every upload of a file name onto a model, oldest first.
const uploads = (log, modelId, name) =>
  log.filter(
    (e) => e.method === 'POST' && e.url.startsWith(`/modelRepository/models/${modelId}/contents?`) && e.body.includes(`filename="${name}"`)
  );
const lastUpload = (log, modelId, name) => uploads(log, modelId, name).pop();
// What only the RAG Builder asks SAS Model Manager for: its projects, the
// embedding models and the prompts the enrichment stage can call.
const isRagRequest = (e) => e.url.startsWith('/modelRepository/') && /RAG-Engineering|emb-proj|Output-Parsing/.test(decodeURIComponent(e.url));
const lastLine = (message) => String(message || '').trim().split('\n').pop();

// The page with the prompt side configured, and the options that switch RAG on.
const PROMPT = `modelRepositoryID=repo-1&llmProjectID=llm-proj&SCREndpoint=${BASE}/scr`;
const RAG = 'enableRag=true&embeddingProjectID=emb-proj';
const RAG_CONTEXT = 'ragComputeContext=rag-ctx';
const APP_URL = `${BASE}/?${PROMPT}&${RAG}&${RAG_CONTEXT}`;
const CONTENT_ROOT = '/SAS Agentic AI Accelerator/RAG';
const rgb = (suffix) => `#app-obj-RGB-${suffix}`;
// The part of the Setup step that only shows while a setup is open.
const EDITOR = `${rgb('pane-setup')} .pb-doc-section`;

(async () => {
  // Without an interpreter the manifested retrieval model would go unchecked: a failure, not a skip.
  const python = resolvePython();
  assert(python, `a Python interpreter is available for the score code check (${python ? `${python.command} ${python.args.join(' ')} = ${python.version}` : 'none found - set VERIFY_PYTHON'})`);

  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const pageErrors = [];
  const newPage = async () => {
    const page = await browser.newPage({ viewport: { width: 1500, height: 1100 } });
    page.on('pageerror', (e) => {
      pageErrors.push(e.message);
      console.log('[pageerror]', e.message);
    });
    page.on('console', (m) => {
      if (m.type() === 'error') console.log('[console.error]', m.text());
    });
    return page;
  };
  const section = async (name, run) => {
    if (WANTED.length && !WANTED.includes(name)) {
      console.log(`---- section ${name}: skipped (VERIFY_RAG_SECTIONS)`);
      return;
    }
    console.log(`---- section ${name}`);
    sectionsRun.push(name);
    const page = await newPage();
    try {
      await run(page);
    } finally {
      await page.close();
    }
  };

  // ---- helpers that act on one page -------------------------------------------
  // Every RAG selector is scoped to the RAG Builder (#app-obj-RGB...): while the
  // RAG flow shows, the Prompt Builder is still in the document, hidden.
  const value = (page, suffix) => page.inputValue(rgb(suffix));
  const optionValues = (page, suffix) => page.$$eval(`${rgb(suffix)} option`, (els) => els.map((o) => o.value));
  const visibleSteps = (page, id) =>
    page.$$eval(`#app-obj-${id}-stepper .pb-step`, (els, prefix) =>
      els.filter((e) => !e.hidden).map((e) => e.querySelector('.pb-step-button').id.replace(prefix, '')).join(','),
      `app-obj-${id}-step-`
    );
  const goRagStep = async (page, key) => {
    await page.click(rgb(`step-${key}`));
    await page.waitForSelector(`${rgb(`pane-${key}`)}:not([hidden])`);
  };
  // Open the page and show the RAG flow. With a prompt side the flow is one
  // choice away (the RAG Builder is not even built before that); a page without
  // one opens on it.
  const openRag = async (page, url = APP_URL) => {
    await page.goto(url);
    await page.waitForSelector('#LPB-kind-rag, #app-obj-RGB-stepper', { state: 'attached', timeout: 15000 });
    if (!(await page.$('#app-obj-RGB-stepper'))) await page.click('#LPB-kind-rag');
    await page.waitForSelector('#app-obj-RGB-stepper', { state: 'visible', timeout: 15000 });
  };
  const selectRagProject = async (page, projectId) => {
    await waitUntil(async () => (await optionValues(page, 'project')).includes(projectId), `RAG project ${projectId} listed`);
    await page.selectOption(rgb('project'), projectId);
  };
  // Choose a setup and wait until `settled` holds: the editor shows defaults
  // first and the stored values a round trip later.
  const openSetup = async (page, setupId, settled, label) => {
    await waitUntil(async () => (await optionValues(page, 'setup')).includes(setupId), `RAG setup ${setupId} listed`);
    await page.selectOption(rgb('setup'), setupId);
    await waitUntil(settled, label || `setup ${setupId} opened`);
  };
  const saveResult = (page, kind) => page.$(`${rgb('save-result')} .alert-${kind}`);
  // What the relationships query answers for a model, and a service without a session (see the mock).
  const setUsage = (model, answer = {}) => fetch(BASE + '/__usage', { method: 'POST', body: JSON.stringify({ model, ...answer }) });
  const noSession = (service, how = {}) => fetch(BASE + '/__nosession', { method: 'POST', body: JSON.stringify({ service, ...how }) });
  // The delete dialog that is open: its title, its text and the links in it.
  const shownDeleteDialog = async (page) => {
    await page.waitForSelector('.modal.show .modal-body');
    return {
      title: (await page.textContent('.modal.show .modal-title')).trim(),
      body: (await page.textContent('.modal.show .modal-body')).trim(),
      links: (await page.$$eval('.modal.show .modal-body a', (els) => els.map((a) => `${a.textContent} -> ${a.getAttribute('href')}`))).join(', '),
    };
  };
  const cancelDeleteDialogOf = async (page) => {
    await page.click('.modal.show .btn-secondary');
    await page.waitForSelector('.modal.show', { state: 'detached' });
  };

  // ---- offered: the RAG kind exists only when it is switched on ----------------
  await section('offered', async (page) => {
    await resetLog();
    await page.goto(`${BASE}/?${PROMPT}`);
    await page.waitForSelector('#LPB-project-dropdown', { timeout: 15000 });
    assert(!(await page.$('#LPB-kind-rag')), 'without enableRag the Setup card offers no RAG kind');
    assert((await page.$$('#LPB-kind-options .pb-kind-option')).length === 2, 'without enableRag the two prompt kinds are the choice');
    assert(!(await page.$('#app-obj-RGB')), 'without enableRag no RAG Builder is in the page');
    // The options of a switched-off feature may still be in the URL (Visual
    // Analytics keeps stored values): they must not bring it back.
    await page.goto(`${BASE}/?${PROMPT}&enableRag=false&embeddingProjectID=emb-proj&${RAG_CONTEXT}`);
    await page.waitForSelector('#LPB-project-dropdown', { timeout: 15000 });
    assert(!(await page.$('#LPB-kind-rag')), 'enableRag=false leaves the RAG kind out although RAG options are set');
    const requested = (await getLog()).filter(isRagRequest);
    assert(requested.length === 0, `a page without the RAG kind makes no RAG request (got: ${requested.map((e) => e.url).join(' | ')})`);
  });

  // ---- shell: one page, two flows ----------------------------------------------
  await section('shell', async (page) => {
    await resetLog();
    await page.goto(APP_URL);
    await page.waitForSelector('#LPB-project-dropdown', { timeout: 15000 });
    const promptKinds = await page.$$eval('#LPB-kind-options input[type=radio]', (els) => els.map((e) => e.id).join(','));
    assert(promptKinds === 'LPB-kind-llm,LPB-kind-decision,LPB-kind-rag', `with enableRag the Setup card offers three kinds (got: ${promptKinds})`);
    assert(await page.isChecked('#LPB-kind-llm'), 'the page opens on the LLM kind');
    assert((await visibleSteps(page, 'LPB')) === 'setup,build,finalize', 'the prompt steps show first');
    assert(!(await page.$('#app-obj-RGB')), 'the RAG Builder is not built before it is chosen');
    const early = (await getLog()).filter(isRagRequest);
    assert(early.length === 0, `no RAG request is made before the kind is chosen (got: ${early.map((e) => e.url).join(' | ')})`);

    // Something to lose on the prompt side: a project, a prompt and its runs.
    await page.selectOption('#LPB-project-dropdown', 'proj-1');
    await waitUntil(async () => (await page.$$('#LPB-prompt-dropdown option')).length === 4, 'the prompts of the project are listed');
    await page.selectOption('#LPB-prompt-dropdown', 'model-used');
    const promptRuns = () => page.$$eval('#app-obj-LPB-pet > .accordion > .accordion-item', (els) => els.length);
    await waitUntil(async () => (await promptRuns()) === 3, 'the saved runs of the prompt are loaded');

    // With the keyboard an arrow key moves the focus from card to card and
    // chooses nothing: on the way from the first card to the third the decision
    // kind would otherwise be chosen in passing, and a change of prompt kind
    // drops the project, the prompt-test and its runs.
    const focused = () => page.evaluate(() => document.activeElement && document.activeElement.id);
    const checkedKind = (prefix) => page.$eval(`input[name="${prefix}-kind"]:checked`, (radio) => radio.value);
    await page.focus('#LPB-kind-llm');
    await page.keyboard.press('ArrowDown');
    assert((await focused()) === 'LPB-kind-decision' && (await checkedKind('LPB')) === 'llm', `an arrow key moves the focus to the next card and leaves the choice alone (focus: ${await focused()}, chosen: ${await checkedKind('LPB')})`);
    await page.keyboard.press('ArrowDown');
    assert((await focused()) === 'LPB-kind-rag' && (await checkedKind('LPB')) === 'llm', `a second one reaches the RAG card, still without choosing (focus: ${await focused()}, chosen: ${await checkedKind('LPB')})`);
    assert(
      (await page.inputValue('#LPB-project-dropdown')) === 'proj-1' && (await page.inputValue('#LPB-prompt-dropdown')) === 'model-used' && (await promptRuns()) === 3,
      'the project, the prompt and its runs are untouched on the way'
    );
    assert(!(await page.$('#app-obj-RGB')) && (await visibleSteps(page, 'LPB')) === 'setup,build,finalize', 'neither the kind nor the flow changed');
    await page.keyboard.press('ArrowRight');
    assert((await focused()) === 'LPB-kind-llm', 'the focus wraps from the last card to the first');
    await page.keyboard.press('ArrowLeft');
    await page.keyboard.press('ArrowUp');
    assert((await focused()) === 'LPB-kind-decision' && (await checkedKind('LPB')) === 'llm', 'and moves backwards the same way');

    // Choosing RAG builds it and swaps the flow. While it is being built the
    // prompt flow stays on screen and cannot be used: a dialog opened now
    // would be hidden with the flow and leave its backdrop over the RAG steps.
    const embeddingModels = '**/modelRepository/projects/emb-proj/models*';
    await page.route(embeddingModels, async (route) => {
      await sleep(2500);
      await route.continue().catch(() => {});
    });
    await page.click('#LPB-kind-rag');
    await waitUntil(async () => await page.isDisabled('#LPB-kind-llm'), 'the cards are busy while the RAG Builder loads');
    const whileLoading = await page.$eval('#app-area-prompt', (area) => ({ inert: area.inert === true, busy: area.getAttribute('aria-busy'), hidden: area.hidden }));
    assert(whileLoading.inert && whileLoading.busy === 'true' && !whileLoading.hidden, `the prompt flow stays on screen and takes no input meanwhile (got: ${JSON.stringify(whileLoading)})`);
    await page.click('[data-bs-target="#promptBuilderCreateProjectModal"]', { force: true, timeout: 1000 }).catch(() => {});
    await sleep(400);
    assert(!(await page.$('.modal.show')) && !(await page.$('#app-obj-RGB-stepper')), 'a click on Create a new Project opens no dialog while the RAG Builder loads');
    await page.waitForSelector('#app-obj-RGB-stepper', { state: 'visible', timeout: 15000 });
    await page.unroute(embeddingModels);
    step(true, 'choosing the RAG kind shows the RAG flow');
    const afterSwap = await page.evaluate(() => ({
      backdrops: document.querySelectorAll('.modal-backdrop').length,
      modalOpen: document.body.classList.contains('modal-open'),
      inert: document.getElementById('app-area-prompt').inert,
      busy: document.getElementById('app-area-prompt').hasAttribute('aria-busy'),
    }));
    assert(
      afterSwap.backdrops === 0 && !afterSwap.modalOpen && !afterSwap.inert && !afterSwap.busy,
      `nothing of the prompt flow lies over the RAG flow, and the prompt flow is released (got: ${JSON.stringify(afterSwap)})`
    );
    assert((await visibleSteps(page, 'RGB')) === 'setup,documents,store,run', `the RAG flow has exactly its four steps (got: ${await visibleSteps(page, 'RGB')})`);
    const shownSteps = await page.$$eval('.pb-step-button', (els) => els.filter((e) => e.getClientRects().length > 0).map((e) => e.id).join(','));
    assert(
      shownSteps === ['setup', 'documents', 'store', 'run'].map((key) => `app-obj-RGB-step-${key}`).join(','),
      `no step of the prompt flow shows beside them (got: ${shownSteps})`
    );
    assert(await page.$('#app-obj-LPB'), 'the Prompt Builder stays in the document');
    assert(!(await page.isVisible('#app-obj-LPB')), 'the Prompt Builder is hidden while the RAG flow shows');
    assert(!(await page.isVisible('#LPB-project-dropdown-combobox')), 'the prompt project picker is hidden with it');
    assert(await page.isVisible(rgb('pane-setup')), 'the RAG flow opens on its Setup step');
    assert((await page.$$('h1')).length === 1, 'the page still has exactly one h1');

    // The same selector, on the RAG Setup card, above the project controls.
    const ragKinds = await page.$$eval('#RGB-kind-options input[type=radio]', (els) => els.map((e) => e.id).join(','));
    assert(ragKinds === 'RGB-kind-llm,RGB-kind-decision,RGB-kind-rag', `the RAG Setup card offers the same three kinds (got: ${ragKinds})`);
    assert(await page.isChecked('#RGB-kind-rag'), 'the RAG kind is the chosen one there');
    const placement = await page.evaluate(() => {
      const kinds = document.getElementById('RGB-kind-options');
      const project = document.getElementById('app-obj-RGB-project');
      return {
        inSetupPane: Boolean(kinds.closest('#app-obj-RGB-pane-setup')),
        sameColumn: kinds.closest('.pb-card-controls') === project.closest('.pb-card-controls'),
        before: Boolean(kinds.compareDocumentPosition(project) & Node.DOCUMENT_POSITION_FOLLOWING),
      };
    });
    assert(placement.inSetupPane && placement.sameColumn && placement.before, `the selector opens the controls of the RAG Setup card (got: ${JSON.stringify(placement)})`);
    assert((await page.evaluate(() => document.activeElement && document.activeElement.id)) === 'RGB-kind-rag', 'the focus is on the chosen card of the visible selector');
    const listings = (log) => log.filter((e) => e.method === 'GET' && e.url.startsWith('/modelRepository/projects?') && decodeURIComponent(e.url).includes('RAG-Engineering')).length;
    assert(listings(await getLog()) === 1, 'the RAG projects were listed once, when the kind was chosen');

    // Something to lose on the RAG side as well: an open setup.
    await selectRagProject(page, 'rag-proj');
    await openSetup(page, 'rag-setup-1', async () => (await value(page, 'collection')) === 'policy_handbook', 'a RAG setup is opened');
    await goRagStep(page, 'store');
    assert(!(await page.isVisible('#RGB-kind-options')), 'the kind is chosen on the Setup step and nowhere else');
    await goRagStep(page, 'setup');

    // Back to the prompt flow: nothing of it was lost.
    await page.click('#RGB-kind-llm');
    await page.waitForSelector('#app-obj-LPB-stepper', { state: 'visible' });
    assert(await page.$('#app-obj-RGB'), 'the RAG Builder stays in the document');
    assert(!(await page.isVisible('#app-obj-RGB')), 'the RAG Builder is hidden while the prompt flow shows');
    assert(await page.isChecked('#LPB-kind-llm'), 'the prompt selector shows the LLM kind again');
    assert((await visibleSteps(page, 'LPB')) === 'setup,build,finalize', 'the prompt steps are back');
    assert(await page.isVisible('#app-obj-LPB-pane-setup'), 'the prompt flow shows its Setup step');
    assert((await page.inputValue('#LPB-project-dropdown')) === 'proj-1', 'the prompt project is still selected');
    assert((await page.inputValue('#LPB-prompt-dropdown')) === 'model-used', 'the prompt is still selected');
    assert((await promptRuns()) === 3, 'its runs are still loaded');
    step(true, 'switching back keeps the state of the prompt side');

    // And to RAG again, with the keyboard: two arrow keys to its card, Space
    // to choose it. The same builder, as it was left.
    await resetLog();
    assert((await focused()) === 'LPB-kind-llm', 'back in the prompt flow the focus is on its chosen card');
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('ArrowDown');
    assert((await focused()) === 'LPB-kind-rag' && (await page.isVisible('#app-obj-LPB-stepper')) && (await checkedKind('LPB')) === 'llm', 'the arrow keys reach the RAG card without leaving the prompt flow');
    await page.keyboard.press('Space');
    await page.waitForSelector('#app-obj-RGB-stepper', { state: 'visible' });
    step(true, 'Space chooses the card that has the focus');
    assert((await page.inputValue('#LPB-project-dropdown')) === 'proj-1' && (await page.inputValue('#LPB-prompt-dropdown')) === 'model-used' && (await promptRuns()) === 3, 'the prompt side kept its project, its prompt and its runs on the way by keyboard');
    // In the RAG flow an arrow key does not leave the flow either.
    await page.keyboard.press('ArrowUp');
    assert((await focused()) === 'RGB-kind-decision' && (await page.isVisible('#app-obj-RGB-stepper')) && (await checkedKind('LPB')) === 'llm', 'an arrow key on the RAG card moves the focus and keeps the RAG flow and the prompt kind');
    assert(await page.isVisible(rgb('pane-setup')), 'returning to the RAG flow shows its Setup step');
    assert((await page.$$('#app-obj-RGB')).length === 1, 'there is still one RAG Builder');
    assert(listings(await getLog()) === 0, 'returning does not build the RAG Builder a second time');
    assert((await value(page, 'setup')) === 'rag-setup-1' && (await value(page, 'collection')) === 'policy_handbook', 'the RAG setup is still open');
    assert(await page.isChecked('#LPB-kind-llm'), 'the hidden prompt selector keeps showing its own kind');

    // The other prompt kind, chosen from the RAG flow.
    await page.click('#RGB-kind-decision');
    await page.waitForSelector('#app-obj-LPB-stepper', { state: 'visible' });
    assert(await page.isChecked('#LPB-kind-decision'), 'choosing the decision kind in the RAG flow opens the prompt flow in that kind');
    assert((await visibleSteps(page, 'LPB')) === 'setup,build,evaluate,finalize', `with its steps (got: ${await visibleSteps(page, 'LPB')})`);
    assert(await page.isChecked('#RGB-kind-rag'), 'the hidden RAG selector keeps showing RAG');
  });

  // ---- flow: a RAG setup from nothing to a tested corpus ------------------------
  await section('flow', async (page) => {
    // What the content root serves is what a manifest has to take over.
    const ingestSource = await (await fetch(`${BASE}/files/files/rag-src-ingest/content`)).text();
    const probeSource = await (await fetch(`${BASE}/files/files/rag-src-test/content`)).text();
    const retrieveTemplate = await (await fetch(`${BASE}/files/files/rag-src-retrieve/content`)).text();
    const declared = (source) => [...source.matchAll(/^%_rag_default\((\w+)\s*,/gm)].map((match) => match[1]);
    assert(declared(ingestSource).length > 20 && declared(probeSource).includes('question') && retrieveTemplate.includes('# ---- MANIFEST'),
      'the mock serves the repository\'s ingestion job, retrieval probe and retrieval model template');

    await openRag(page);
    await waitUntil(async () => (await optionValues(page, 'project')).includes('rag-proj'), 'the RAG projects are listed');
    assert(!(await page.isVisible(EDITOR)), 'no editor shows before a setup is selected');

    // -- a project ---------------------------------------------------------------
    await resetLog();
    await page.click(`[data-bs-target="${rgb('create-projectModal')}"]`);
    await page.waitForSelector(`${rgb('create-projectModal')}.show`);
    await page.fill(rgb('create-projectName'), 'ZZ Verify RAG');
    await page.fill(rgb('create-projectDescription'), 'Created by verify-rag');
    await page.click(`${rgb('create-projectModal')} .btn-primary`);
    await waitUntil(async () => (await value(page, 'project')).startsWith('rag-proj-new-'), 'the new RAG project is selected');
    const projectId = await value(page, 'project');
    const projectPost = (await getLog()).find((e) => e.method === 'POST' && e.url === '/modelRepository/projects');
    const projectBody = JSON.parse(projectPost.body);
    assert(
      projectBody.name === 'ZZ Verify RAG' && projectBody.function === 'RAG' && projectBody.repositoryId === 'repo-1' && projectBody.folderId === 'folder-repo-1',
      `the project is created as a RAG project in the configured repository (got: ${projectPost.body.slice(0, 200)})`
    );
    assert(projectBody.tags.includes('RAG-Engineering') && projectBody.tags.includes('LLM'), 'it carries the tags the RAG view lists by');
    await page.waitForSelector('.modal.show', { state: 'detached' });

    // -- a setup -----------------------------------------------------------------
    await resetLog();
    await page.click(`[data-bs-target="${rgb('create-setupModal')}"]`);
    await page.waitForSelector(`${rgb('create-setupModal')}.show`);
    await page.fill(rgb('create-setupName'), 'ZZ Verify Setup');
    await page.fill(rgb('create-setupDescription'), 'A corpus made by the verify suite');
    await page.click(`${rgb('create-setupModal')} .btn-primary`);
    // The id comes from the service; the picker's placeholder has a value of
    // its own, so "something is selected" would not tell the two apart.
    const createdSetup = async () => (await ragState()).models.find((model) => model.name === 'ZZ Verify Setup');
    await waitUntil(async () => Boolean(await createdSetup()), 'the RAG setup is created in Model Manager');
    const setupId = (await createdSetup()).id;
    await waitUntil(async () => (await value(page, 'setup')) === setupId, 'the new RAG setup is selected');
    const setupBody = JSON.parse((await getLog()).find((e) => e.method === 'POST' && e.url === '/modelRepository/models').body);
    assert(
      setupBody.name === 'ZZ Verify Setup' && setupBody.function === 'RAG' && setupBody.algorithm === 'RAG' && setupBody.projectId === projectId && setupBody.scoreCodeType === 'python',
      `the setup is created as a RAG model in that project (got: ${JSON.stringify(setupBody).slice(0, 260)})`
    );
    assert(setupBody.modeler === 'tester', `the setup records who made it (got modeler: ${setupBody.modeler})`);
    await page.waitForSelector('.modal.show', { state: 'detached' });
    await page.waitForSelector(EDITOR, { state: 'visible' });
    step(true, 'the editor opens for the new setup');

    // -- nothing is decided for a new setup --------------------------------------
    await goRagStep(page, 'documents');
    assert((await value(page, 'embed-model')) === '', 'a new setup preselects no embedding model');
    assert((await optionValues(page, 'embed-model')).join(',') === ',bge_small_en_v15,custom_embedder,text_embedding_3_small', 'the registered embedding models are offered');
    await goRagStep(page, 'store');
    assert((await value(page, 'backend')) === '', 'a new setup preselects no vector database');
    const backends = await page.$$eval(`${rgb('backend')} option`, (els) => els.map((o) => `${o.value}${o.disabled ? '(disabled)' : ''}`).join(','));
    assert(backends === ',pgvector,singlestore(disabled)', `the offered stores are listed, one this user holds no credential for disabled (got: ${backends})`);
    assert((await optionValues(page, 'tables-caslib')).join(',') === ',Public', 'the caslib picker leaves the personal caslib out');
    await resetLog();
    await page.click(rgb('save'));
    await page.waitForSelector(`${rgb('save-result')} .alert-danger li`);
    const problems = (await page.$$(`${rgb('save-result')} .alert-danger li`)).length;
    assert(problems >= 6, `an empty setup is refused with every problem named (got ${problems})`);
    assert(uploads(await getLog(), setupId, 'rag-setup.json').length === 0, 'and nothing is written');
    await goRagStep(page, 'run');
    assert(await page.isDisabled(rgb('launch')), 'Launch ingestion is disabled before a manifest');
    assert(await page.isDisabled(rgb('test')), 'Test retrieval is disabled before a manifest');

    // -- author and save ---------------------------------------------------------
    await goRagStep(page, 'documents');
    await page.fill(rgb('source-path'), '/mnt/data/zz verify');
    await page.selectOption(rgb('embed-model'), 'bge_small_en_v15');
    assert((await value(page, 'embed-dims')) === '384', 'the vector width follows the embedding model');
    await goRagStep(page, 'store');
    await page.selectOption(rgb('backend'), 'pgvector');
    await page.fill(rgb('collection'), 'zz_verify_v1');
    await page.fill(rgb('tables-prefix'), 'zzv');
    await page.selectOption(rgb('tables-caslib'), 'Public');
    await resetLog();
    await page.click(rgb('save'));
    await page.waitForSelector(`${rgb('save-result')} .alert-success`);
    let log = await getLog();
    const savedSetup = JSON.parse(multipartFile(lastUpload(log, setupId, 'rag-setup.json').body));
    assert(lastUpload(log, setupId, 'rag-setup.json').url.includes('role=documentation'), 'Save writes rag-setup.json onto the model');
    assert(
      savedSetup.source.path === 'sasserver:/mnt/data/zz verify' && savedSetup.embedding.model === 'bge_small_en_v15' && savedSetup.embedding.dims === 384,
      `rag-setup.json holds the documents and the embedding (got: ${JSON.stringify({ source: savedSetup.source, embedding: savedSetup.embedding })})`
    );
    assert(
      savedSetup.store.backend === 'pgvector' && savedSetup.store.host === 'pg.mock.local' && savedSetup.store.port === 5432 && savedSetup.store.database === 'ragdb' && savedSetup.store.collection === 'zz_verify_v1',
      `the store is recorded with the location resolved from the credential domain (got: ${JSON.stringify(savedSetup.store)})`
    );
    assert(savedSetup.tables.prefix === 'ZZV' && savedSetup.tables.caslib === 'Public', 'the table prefix is stored in upper case, as CAS will show it');
    assert(savedSetup.credentialDomain === 'agentic-ai-keys' && savedSetup.policies && savedSetup.policies.recordHistory === true, 'the credential domain and the policies of the deployment are recorded on the setup');
    assert(!savedSetup.job, 'a setup that was only saved names no ingestion job');
    const pipelineYaml = multipartFile(lastUpload(log, setupId, 'pipeline.yaml').body);
    assert(pipelineYaml.includes('backend: "pgvector"') && pipelineYaml.includes('collection: "zz_verify_v1"') && pipelineYaml.includes('prefix: "ZZV"'), 'Save writes pipeline.yaml with the same values');
    const modelPuts = log.filter((e) => e.method === 'PUT' && e.url === `/modelRepository/models/${setupId}`).map((e) => JSON.parse(e.body));
    const registration = modelPuts.find((body) => body.trainTable);
    assert(
      registration && registration.trainTable === 'cas-shared-default/Public/ZZV_LEDGER' && registration.inputVariables.length === 5 && registration.outputVariables.length === 4,
      'the model gets the ledger reference and the retrieval variables'
    );
    const tags = (modelPuts[modelPuts.length - 1] || {}).tags || [];
    assert(['LLM', 'RAG', 'bge_small_en_v15', 'pgvector'].every((tag) => tags.includes(tag)), `and tags for the embedding model and the vector database (got: ${tags.join(', ')})`);
    step(true, 'the setup is saved');

    // The ledger does not exist before the first run.
    await goRagStep(page, 'run');
    await page.click(rgb('ledger'));
    await page.waitForSelector(rgb('ledger-panel'), { state: 'visible' });
    await waitUntil(async () => (await page.textContent(rgb('ledger-panel'))).length > 40, 'the ledger panel says why it is empty');
    assert((await page.$$(`${rgb('ledger-panel')} table`)).length === 0, 'Browse ledger shows a note, not a table, before the first run');

    // -- manifest ----------------------------------------------------------------
    await goRagStep(page, 'store');
    await resetLog();
    await page.click(rgb('manifest'));
    await waitUntil(async () => (await getLog()).some((e) => e.method === 'POST' && e.url === `/modelRepository/models/${setupId}/modelVersions`), 'the manifest records a model version', 30000);
    await waitUntil(async () => !(await page.isDisabled(rgb('manifest'))) && (await saveResult(page, 'success')), 'the manifest reports success');
    assert(!(await saveResult(page, 'danger')), 'the manifest reports no problem');
    log = await getLog();

    const folderPosts = log.filter((e) => e.method === 'POST' && e.url.startsWith('/folders/folders?'));
    assert(
      folderPosts.length === 1 && folderPosts[0].url.endsWith('/folders/folders/rag-folder-content') && JSON.parse(folderPosts[0].body).name === 'generated',
      `the folder for the generated artifacts is created once, below the content root (got: ${folderPosts.map((e) => e.url + ' ' + e.body).join(' | ')})`
    );
    const generated = (await ragState()).folders.find((folder) => folder.path === `${CONTENT_ROOT}/generated`);

    const definitionPosts = log.filter((e) => e.method === 'POST' && e.url.startsWith('/jobDefinitions/definitions'));
    assert(definitionPosts.length === 1 && definitionPosts[0].url.endsWith(`parentFolderUri=/folders/folders/${generated.id}`), 'one job definition is created, in that folder');
    const definition = JSON.parse(definitionPosts[0].body);
    assert(definition.name === 'RAG Ingest - ZZ Verify Setup' && definition.type === 'Compute', `it is the ingestion job of the setup (got: ${definition.name})`);
    assert(definition.code === ingestSource, 'its code is the deployed Ingest-Documents.sas, unchanged');
    const parameter = (name) => (definition.parameters.find((entry) => entry.name === name) || {}).defaultValue;
    const parameterNames = definition.parameters.map((entry) => entry.name);
    const undeclared = parameterNames.filter((name) => name !== '_contextName' && !declared(ingestSource).includes(name));
    assert(undeclared.length === 0, `every parameter is one the job declares (not declared: ${undeclared.join(', ') || 'none'})`);
    const expectedParameters = {
      sourcePath: 'sasserver:/mnt/data/zz verify', includeCode: '0', collection: 'zz_verify_v1', backend: 'pgvector',
      storeHost: 'pg.mock.local', storePort: '5432', storeDb: 'ragdb', storeSslmode: 'prefer', credentialDomain: 'agentic-ai-keys',
      scrEndpoint: `${BASE}/scr`, embedModel: 'bge_small_en_v15', deploymentType: 'k8s', inputTokenLimit: '256', chunker: 'recursive',
      overlapTokens: '30', enrichSteps: '', pipelineVersion: 'v1', ledgerCaslib: 'Public', ledgerTable: 'ZZV_LEDGER',
      ragCorePath: `${CONTENT_ROOT}/rag_core`, deletedPolicy: 'retire', retainDays: '0', replicas: '1', recordHistory: '1',
      _contextName: 'rag-ctx',
    };
    const wrongParameters = Object.entries(expectedParameters).filter(([name, expected]) => parameter(name) !== expected);
    assert(wrongParameters.length === 0, `the parameters carry the values of the setup (differs: ${wrongParameters.map(([name, expected]) => `${name}=${parameter(name)} not ${expected}`).join('; ') || 'none'})`);
    assert(definition.parameters.every((entry) => entry.type === 'character' && entry.required === false), 'every parameter is an optional character value');
    const definitionId = (await ragState()).jobDefinitions.find((entry) => entry.name === definition.name && entry.folderId === generated.id).id;
    const withJob = JSON.parse(multipartFile(lastUpload(log, setupId, 'rag-setup.json').body));
    assert(withJob.job && withJob.job.definitionUri === `/jobDefinitions/definitions/${definitionId}`, 'rag-setup.json now names the generated job');

    const flowPosts = log.filter((e) => e.method === 'POST' && e.url.startsWith('/dataFlows/dataFlows?'));
    assert(flowPosts.length === 1 && flowPosts[0].url.endsWith(`parentFolderUri=/folders/folders/${generated.id}`), 'one Studio flow is registered, in the same folder');
    const flow = JSON.parse(flowPosts[0].body);
    const nodes = Object.values(flow.nodes).sort((left, right) => left.priority - right.priority);
    assert(flow.name === 'RAG Ingest - ZZ Verify Setup' && flow.id === undefined, 'the flow is named after the setup and leaves its id to the service');
    assert(flow.createdBy === 'tester' && flow.modifiedBy === 'tester', `the flow names the signed-in user as its author (got: ${flow.createdBy} / ${flow.modifiedBy})`);
    assert(
      nodes.map((node) => node.name).join(' > ') === 'RAG - List Documents > RAG - Extract Text > RAG - Chunk Documents > RAG - Embed Chunks > RAG - Load Vector Store',
      `the flow chains the five ingestion steps (got: ${nodes.map((node) => node.name).join(' > ')})`
    );
    const stepsRead = log.filter((e) => e.method === 'GET' && e.url.startsWith('/dataFlows/steps/')).map((e) => e.url);
    assert(
      nodes.every((node) => stepsRead.includes(node.stepReference.path)) && flow.connections.length === 4,
      'every node references a registered step that was read, and the five are wired in a row'
    );
    const argumentsOf = (name) => nodes.find((node) => node.name === name).arguments;
    assert(
      argumentsOf('RAG - List Documents')._rgls_sourcePath === 'sasserver:/mnt/data/zz verify' && argumentsOf('RAG - List Documents')._rgls_ragProject === 'ZZV' && argumentsOf('RAG - List Documents')._rgls_tablesCaslib === 'Public',
      'the List step gets the document folder and the pipeline tables'
    );
    assert(argumentsOf('RAG - Embed Chunks')._rgem_embedModel === 'bge_small_en_v15', 'the Embed step gets the embedding model');
    const load = argumentsOf('RAG - Load Vector Store');
    assert(
      load._rgld_backend === 'pgvector' && load._rgld_collection === 'zz_verify_v1' && load._rgld_storeHost === 'pg.mock.local' && load._rgld_storeDb === 'ragdb',
      'the Load step gets the vector store'
    );
    assert(uploads(log, setupId, 'RAG Ingest - ZZ Verify Setup.flw').length === 1, 'the flow is also attached to the model');

    const scoreUpload = lastUpload(log, setupId, 'retrieve_context.py');
    assert(scoreUpload && scoreUpload.url.includes('role=score'), 'retrieve_context.py is uploaded as the score code of the setup');
    const scoreCode = multipartFile(scoreUpload.body);
    const manifestLines = ['BACKEND = "pgvector"', 'COLLECTION = "zz_verify_v1"', 'EMBED_MODEL = "bge_small_en_v15"', 'STORE_HOST = "pg.mock.local"', 'STORE_PORT = "5432"', 'STORE_DB = "ragdb"', 'CREDENTIAL_DOMAIN = "agentic-ai-keys"'];
    const missingLines = manifestLines.filter((line) => !scoreCode.split(/\r?\n/).includes(line));
    assert(missingLines.length === 0, `its MANIFEST block carries the setup (missing: ${missingLines.join(' ; ') || 'none'})`);
    // The retrieval model embeds the question where the ingestion job embedded
    // the chunks: the SCR endpoint of the object, which is not <host>/llm here.
    const embedEndpoint = (/^EMBED_ENDPOINT = "([^"]*)"$/m.exec(scoreCode) || [])[1];
    assert(
      embedEndpoint === parameter('scrEndpoint') && embedEndpoint === `${BASE}/scr`,
      `the retrieval model calls the embedding endpoint the ingestion job calls (model: ${embedEndpoint}, job: ${parameter('scrEndpoint')})`
    );
    assert(!/your-database-host|your-database-name|your-sas-viya-host/.test(scoreCode), 'no placeholder of the template is left in it');
    const outsideBlock = (code) => code.replace(/# ---- MANIFEST[\s\S]*?\n# -{20,}\r?\n/, '');
    assert(outsideBlock(scoreCode) === outsideBlock(retrieveTemplate) && outsideBlock(scoreCode) !== scoreCode, 'everything outside the MANIFEST block is the deployed template');
    const compiled = compilePython(scoreCode, 'retrieve_context.py');
    assert(compiled.ok, `the manifested retrieve_context.py is valid Python (${compiled.python}${compiled.ok ? '' : ': ' + lastLine(compiled.error)})`);
    step(true, 'the setup is manifested');

    // -- ingestion ---------------------------------------------------------------
    await goRagStep(page, 'run');
    assert(!(await page.isDisabled(rgb('launch'))) && !(await page.isDisabled(rgb('test'))), 'a manifested setup can be launched and tested');
    await resetLog();
    await page.click(rgb('launch'));
    await page.waitForSelector(rgb('ingestion-run'), { state: 'visible' });
    await waitUntil(async () => (await page.textContent(rgb('run-state'))) === 'running', 'the run is reported as running');
    await waitUntil(async () => (await page.$$(`${rgb('run-milestones')} li`)).length === 3, 'the live log shows the first milestones');
    assert((await page.textContent(rgb('run-milestones'))).includes('connected: pgvector + bge_small_en_v15'), 'the milestones come from the job that was launched with the values of the setup');
    await waitUntil(async () => (await page.textContent(rgb('run-state'))) === 'completed', 'the run is polled to completion', 20000);
    assert((await page.getAttribute(rgb('run-state'), 'class')).includes('text-bg-success'), 'a completed run is marked as a success');
    const finalMilestones = await page.textContent(rgb('run-milestones'));
    assert(finalMilestones.includes('23 chunks  documents ingested=2'), 'the final milestones are read from the log file');
    // Every step of a clean run reports "0 failed": the word alone must not
    // turn the run red.
    assert(
      finalMilestones.includes('2 extracted, 1 skipped, 0 failed') && !(await page.$(`${rgb('status')} .alert-danger`)) && !(await page.$(`${rgb('status')} .alert-warning`)),
      'the steps that report "0 failed" leave a clean run a success, with no alert on the page'
    );
    log = await getLog();
    const launch = JSON.parse(log.find((e) => e.method === 'POST' && e.url === '/jobExecution/jobs').body);
    assert(launch.jobDefinitionUri === `/jobDefinitions/definitions/${definitionId}` && launch.name === 'RAG Ingest - ZZ Verify Setup', 'the generated job definition is what was launched');
    assert(launch.arguments._contextName === 'rag-ctx' && launch.arguments.collection === 'zz_verify_v1' && launch.arguments.ledgerTable === 'ZZV_LEDGER', 'in the ingestion compute context, with the arguments of the setup');
    assert(log.filter((e) => e.method === 'GET' && /^\/jobExecution\/jobs\/ragrun-/.test(e.url)).length >= 2, 'the job was polled more than once');
    await waitUntil(async () => (await page.$$(`${rgb('ledger-panel')} table tbody tr`)).length === 3, 'the ledger shows on its own once the run has finished');
    assert(!(await page.isDisabled(rgb('launch'))), 'Launch ingestion is available again');
    step(true, 'an ingestion is launched and followed to its end');

    // A run that did its work and could not ingest one document: completed,
    // with a warning that names the number, and the ledger that names the document.
    await armFailure({ documents: 1 });
    await page.click(rgb('launch'));
    await waitUntil(async () => (await page.getAttribute(rgb('run-state'), 'class')).includes('text-bg-warning') && (await page.textContent(rgb('run-state'))) === 'completed', 'a completed run with a failed document is marked as completed with a warning', 20000);
    assert((await page.textContent(rgb('run-milestones'))).includes('documents failed=1, ingested=2'), 'its summary counts the failed document');
    const failedDocumentsAlert = ((await page.textContent(`${rgb('status')} .alert-warning`).catch(() => '')) || '').trim();
    assert(
      failedDocumentsAlert.includes('completed') && failedDocumentsAlert.includes('1 document(s) failed') && !(await page.$(`${rgb('status')} .alert-danger`)),
      `the page says that the run completed and how many documents failed, and does not call the run failed (got: ${failedDocumentsAlert})`
    );
    await waitUntil(
      async () => (await page.$$eval(`${rgb('ledger-panel')} table tbody tr`, (rows) => rows.map((row) => row.textContent).filter((row) => row.includes('failed')).length)) === 1,
      'the ledger of that run shows on its own and marks the one failed document'
    );
    assert(!(await page.isDisabled(rgb('launch'))), 'Launch ingestion is available after it');

    // A run the job reports as failed: the job completes, the log says how it went.
    await armFailure({ run: 'OperationalError: the vector store refused the connection' });
    await page.click(rgb('launch'));
    await waitUntil(async () => (await page.getAttribute(rgb('run-state'), 'class')).includes('text-bg-danger') && (await page.textContent(rgb('run-state'))) === 'completed', 'a run whose log reports a failure is marked as failed', 20000);
    assert(await page.$(`${rgb('status')} .alert-danger`), 'and the page says the run did not succeed');

    // -- retrieval test ----------------------------------------------------------
    await page.click(rgb('test'));
    await page.waitForSelector(rgb('test-panel'), { state: 'visible' });
    await resetLog();
    await page.click(rgb('test-run'));
    await waitUntil(async () => (await page.textContent(rgb('test-status'))).trim() !== '', 'a test without a question asks for one');
    assert(!(await getLog()).some((e) => e.method === 'POST'), 'and starts nothing');
    await page.fill(rgb('test-question'), 'How many days of leave do I get; and can I keep 100% of them & carry them over?');
    await page.fill(rgb('test-topk'), '3');
    await page.click(rgb('test-run'));
    await waitUntil(async () => (await page.$$(`${rgb('test-results')} tbody tr`)).length === 3, 'the retrieval test shows the returned chunks', 20000);
    const firstHit = await page.$$eval(`${rgb('test-results')} tbody tr:first-child td`, (cells) => cells.map((cell) => cell.textContent));
    assert(firstHit[0] === '1' && firstHit[1] === '0.8731' && firstHit[2] === 'leave.pdf', `a hit shows its rank, score and source file (got: ${firstHit.slice(0, 3).join(' | ')})`);
    const chunk = firstHit[firstHit.length - 1];
    assert(
      chunk.startsWith('Employees accrue 2.5 days of annual leave per month & may carry over up to 50% of the unused days') && chunk.endsWith('by the end of March.'),
      `a chunk that wrapped over several log lines, with % and & in it, is shown whole (got: ${chunk})`
    );
    assert((await page.textContent(rgb('test-status'))).includes('rag cost:'), 'the cost line of the probe is shown');
    // The definition goes after the chunks are on the page.
    await waitUntil(async () => (await getLog()).some((e) => e.method === 'DELETE' && e.url.startsWith('/jobDefinitions/definitions/')), 'the probe definition is deleted when the test ends');
    log = await getLog();
    const probePosts = log.filter((e) => e.method === 'POST' && e.url.startsWith('/jobDefinitions/definitions'));
    assert(probePosts.length === 1 && probePosts[0].url === '/jobDefinitions/definitions', 'the probe is one job definition that belongs to no folder');
    const probe = JSON.parse(probePosts[0].body);
    assert(probe.code === probeSource, 'its code is the deployed Test-Retrieval.sas');
    const undeclaredProbe = probe.parameters.map((entry) => entry.name).filter((name) => name !== '_contextName' && !declared(probeSource).includes(name));
    assert(undeclaredProbe.length === 0, `every parameter is one the probe declares (not declared: ${undeclaredProbe.join(', ') || 'none'})`);
    const probeLaunch = JSON.parse(log.find((e) => e.method === 'POST' && e.url === '/jobExecution/jobs').body);
    assert(
      probeLaunch.arguments.topK === '3' && probeLaunch.arguments.collection === 'zz_verify_v1' && probeLaunch.arguments._contextName === 'rag-ctx',
      'the probe is launched with the store of the setup, the number of chunks asked for and the ingestion compute context'
    );
    assert(
      !/[;&%]/.test(probeLaunch.arguments.question) && probeLaunch.arguments.question.startsWith('How many days of leave do I get'),
      `the question travels without the characters a macro variable cannot carry (got: ${probeLaunch.arguments.question})`
    );
    const probeDeletes = log.filter((e) => e.method === 'DELETE' && e.url.startsWith('/jobDefinitions/definitions/'));
    assert(probeDeletes.length === 1, 'it is deleted once');
    let state = await ragState();
    assert(
      !state.jobDefinitions.some((entry) => String(entry.name).startsWith('RAG Test retrieval')) && !state.folders.some((folder) => folder.members.some((member) => String(member.name).startsWith('RAG Test retrieval'))),
      'nothing of the test is left behind'
    );
    // A probe that fails says why, and still cleans up.
    await armFailure({ retrieval: 'OperationalError: the vector store refused the connection' });
    await resetLog();
    await page.click(rgb('test-run'));
    await waitUntil(async () => (await page.textContent(rgb('test-status'))).includes('the vector store refused the connection'), 'a failed probe shows its reason', 20000);
    assert((await page.$$(`${rgb('test-results')} tbody tr`)).length === 0, 'and no chunks');
    await waitUntil(async () => (await getLog()).some((e) => e.method === 'DELETE' && e.url.startsWith('/jobDefinitions/definitions/')), 'its definition is deleted as well');
    step(true, 'retrieval is tested without leaving anything behind');

    // -- the setup is what was saved ---------------------------------------------
    await openRag(page);
    await selectRagProject(page, projectId);
    await openSetup(page, setupId, async () => (await value(page, 'collection')) === 'zz_verify_v1', 'the saved setup reopens with its values');
    assert(
      (await value(page, 'backend')) === 'pgvector' && (await value(page, 'embed-model')) === 'bge_small_en_v15' && (await value(page, 'tables-prefix')) === 'ZZV' && (await value(page, 'source-path')) === '/mnt/data/zz verify',
      'every step shows what was saved'
    );
    await goRagStep(page, 'run');
    assert(!(await page.isDisabled(rgb('launch'))), 'the reopened setup still knows its ingestion job');

    // -- enrichment: a prompt called once per chunk -----------------------------------
    // The blocks of the chain are rebuilt whenever a setup is opened, so their
    // controls are found by the end of their id below the list.
    await goRagStep(page, 'documents');
    const enrich = (suffix) => `${rgb('enrich-list')} [id$="-${suffix}"]`;
    const enrichOptions = (suffix) => page.$$eval(`${enrich(suffix)} option`, (els) => els.map((o) => o.value));
    assert((await enrichOptions('project')).join(',') === ',proj-1', `the enrichment stage offers the projects holding a prompt with output parsing (got: ${await enrichOptions('project')})`);
    await page.selectOption(enrich('project'), 'proj-1');
    await waitUntil(async () => (await enrichOptions('prompt')).includes('model-parse'), 'the prompts of that project are listed');
    await page.selectOption(enrich('prompt'), 'model-parse');
    const mappedInputs = () => page.$$eval(`${rgb('enrich-list')} .input-group select`, (els) => els.map((e) => e.value).join(','));
    await waitUntil(async () => (await mappedInputs()) === 'chunk,document', 'the inputs of the prompt are read from its score code and mapped by their names');
    assert((await enrichOptions('version')).join(',') === ',ver-2,ver-1', `its versions can be pinned, newest first (got: ${await enrichOptions('version')})`);
    assert(await page.isDisabled(enrich('column-context_header')), 'an output named like a column of the chunk table cannot be stored as a column');
    await page.selectOption(enrich('header'), 'context_header');
    await page.check(enrich('column-topic'));

    // A second manifest updates the job in place and replaces the flow.
    await goRagStep(page, 'store');
    await page.fill(rgb('collection'), 'zz_verify_v2');
    await resetLog();
    await page.click(rgb('manifest'));
    await waitUntil(async () => (await getLog()).some((e) => e.method === 'POST' && e.url === `/modelRepository/models/${setupId}/modelVersions`), 'the second manifest records a model version', 30000);
    await waitUntil(async () => !(await page.isDisabled(rgb('manifest'))), 'the second manifest ends');
    assert(!(await saveResult(page, 'danger')), 'the second manifest reports no problem');
    log = await getLog();
    const definitionPut = log.find((e) => e.method === 'PUT' && e.url === `/jobDefinitions/definitions/${definitionId}`);
    assert(definitionPut && definitionPut.ifMatch, 'the existing job definition is updated, guarded by its ETag');
    const updatedParameter = (name) => JSON.parse(definitionPut.body).parameters.find((entry) => entry.name === name).defaultValue;
    assert(updatedParameter('collection') === 'zz_verify_v2', 'with the changed value');
    assert(!log.some((e) => e.method === 'POST' && e.url.startsWith('/jobDefinitions/definitions')), 'no second job definition is created');
    state = await ragState();
    const flows = state.flows.filter((entry) => entry.name === 'RAG Ingest - ZZ Verify Setup');
    assert(flows.length === 1 && log.some((e) => e.method === 'DELETE' && e.url.startsWith('/dataFlows/dataFlows/')), 'the flow of the same name is replaced, not duplicated');
    assert(!log.some((e) => e.method === 'POST' && e.url.startsWith('/folders/folders?')), 'the folder that exists is used as it is');

    // The enrichment reaches the setup, the job and the flow alike.
    const enrichedSetup = JSON.parse(multipartFile(lastUpload(log, setupId, 'rag-setup.json').body));
    const enrichStep = (enrichedSetup.enrich || [])[0] || {};
    assert(
      (enrichedSetup.enrich || []).length === 1 && enrichStep.promptModelId === 'model-parse' && enrichStep.promptProjectId === 'proj-1' && enrichStep.headerOutput === 'context_header' &&
        (enrichStep.columnOutputs || []).join(',') === 'topic' && JSON.stringify(enrichStep.mapping) === JSON.stringify({ chunk: 'chunk', document: 'document' }),
      `rag-setup.json records the prompt, its mapping and what is kept of its answer (got: ${JSON.stringify(enrichedSetup.enrich)})`
    );
    assert(updatedParameter('enrichSteps') === 'model-parse||chunk=chunk;document=document|context_header|topic|4', `the job gets the chain in its one parameter (got: ${updatedParameter('enrichSteps')})`);
    const enrichedFlow = flows[0];
    const enrichedNodes = Object.values(enrichedFlow.nodes).sort((left, right) => left.priority - right.priority);
    assert(
      enrichedNodes.map((node) => node.name).join(' > ') === 'RAG - List Documents > RAG - Extract Text > RAG - Chunk Documents > RAG - Enrich Chunks > RAG - Embed Chunks > RAG - Load Vector Store' && enrichedFlow.connections.length === 5,
      `the flow gains the Enrich step between chunking and embedding (got: ${enrichedNodes.map((node) => node.name).join(' > ')})`
    );
    const enrichArguments = enrichedNodes[3].arguments;
    assert(
      enrichArguments._rgen_promptModel === 'model-parse' && enrichArguments._rgen_mapping === 'chunk=chunk;document=document' && enrichArguments._rgen_headerOutput === 'context_header' && enrichArguments._rgen_columns === 'topic',
      'the Enrich step gets the prompt, the mapping and the outputs to keep'
    );
    assert(enrichedNodes[5].arguments._rgld_collection === 'zz_verify_v2', 'and the Load step the changed collection');

    // -- delete ------------------------------------------------------------------
    await goRagStep(page, 'setup');
    await resetLog();
    await page.click(rgb('delete-setup'));
    await page.waitForSelector('.modal.show .btn-danger');
    const beforeConfirm = await getLog();
    assert(
      beforeConfirm.some((e) => e.method === 'POST' && e.url.startsWith('/relationships/relationships') && e.body.includes(`/modelRepository/models/${setupId}`)) && !beforeConfirm.some((e) => e.method === 'DELETE'),
      'what depends on the setup is asked before the confirmation, and nothing is deleted yet'
    );
    const setupDialog = (await page.textContent('.modal.show .modal-body')).trim();
    assert(setupDialog.includes('No decision references this.'), `the dialog says that no decision uses the new setup (got: ${setupDialog})`);
    await page.click('.modal.show .btn-danger');
    await page.waitForSelector('.modal.show', { state: 'detached' });
    await waitUntil(async () => (await getLog()).some((e) => e.method === 'DELETE' && e.url === `/modelRepository/models/${setupId}`), 'the setup is deleted in Model Manager');
    await waitUntil(async () => !(await optionValues(page, 'setup')).includes(setupId), 'the deleted setup leaves the list');
    assert(!(await page.isVisible(EDITOR)), 'the editor closes with it');
    await page.click(rgb('delete-project'));
    await page.waitForSelector('.modal.show .btn-danger');
    const emptyProjectDialog = (await page.textContent('.modal.show .modal-body')).trim();
    assert(
      emptyProjectDialog.includes('This project holds no setups.') && !/decision/i.test(emptyProjectDialog),
      `the dialog of a project without setups says so and carries no usage note (got: ${emptyProjectDialog})`
    );
    await page.click('.modal.show .btn-danger');
    await page.waitForSelector('.modal.show', { state: 'detached' });
    await waitUntil(async () => (await getLog()).some((e) => e.method === 'DELETE' && e.url === `/modelRepository/projects/${projectId}`), 'the project is deleted in Model Manager');
    await waitUntil(async () => !(await optionValues(page, 'project')).includes(projectId), 'the deleted project leaves the list');
    step(true, 'the setup and its project are deleted');
  });

  // ---- usage: what the delete dialogs say about decisions ------------------------
  // A manifested setup is a model a decision flow can call. Both delete dialogs
  // ask the relationships service first and say what they learned: the
  // decisions, that there are none, or that the check failed. Every dialog here
  // is cancelled, so the fixture project keeps its three setups.
  await section('usage', async (page) => {
    const openDeleteDialog = async (button) => {
      await page.click(rgb(button));
      return shownDeleteDialog(page);
    };
    const cancelDeleteDialog = () => cancelDeleteDialogOf(page);
    const usageRequests = async () =>
      (await getLog())
        .filter((e) => e.url.startsWith('/relationships/'))
        .map((e) => `${e.method} ${e.url.split('?')[0]}${e.xhr ? '' : ' (plain)'} ${e.status}`);
    const BOTH_DECISIONS = `Loan Approval Decision -> ${BASE}/SASDecisionManager/decisions/flow-A, Fraud Check Decision -> ${BASE}/SASDecisionManager/decisions/flow-B`;
    const USED = '2 decision(s) reference this and will break:';
    const UNUSED = 'No decision references this.';
    const UNKNOWN = 'Whether anything still uses this could not be checked';

    // One setup two decisions use, one nothing uses, one whose check fails.
    await setUsage('rag-setup-1', { flows: ['flow-A', 'flow-B'] });
    await setUsage('rag-setup-2', { flows: [] });
    await setUsage('rag-setup-ss', { status: 503 });
    await openRag(page);
    await selectRagProject(page, 'rag-proj');

    // -- Delete setup ------------------------------------------------------------
    await openSetup(page, 'rag-setup-1', async () => (await value(page, 'collection')) === 'policy_handbook', 'the setup two decisions use opens');
    await resetLog();
    let shown = await openDeleteDialog('delete-setup');
    assert(shown.title === 'Delete setup: Policy Handbook', `Delete setup names the setup in the title (got: ${shown.title})`);
    assert(
      shown.body.includes(USED) && shown.links === BOTH_DECISIONS,
      `Delete setup: a setup two decisions use lists them with their links (got: ${shown.body} / ${shown.links})`
    );
    assert(!shown.body.includes(UNUSED) && !shown.body.includes(UNKNOWN), 'and carries neither of the other two notes');
    // In a report the page sits in a sandboxed iframe that may open no tab:
    // the link then copies itself and says so, as the Prompt Builder's does.
    await page.evaluate(() => {
      window.__opened = [];
      window.open = (url) => {
        window.__opened.push(String(url));
        return null;
      };
    });
    await page.click('.modal.show .modal-body a >> nth=0');
    await waitUntil(async () => /link copied/i.test(await page.textContent('.modal.show .modal-body a >> nth=0')), 'a decision link that cannot open a tab says that the link was copied');
    assert(
      (await page.evaluate(() => window.__opened.join(','))) === `${BASE}/SASDecisionManager/decisions/flow-A` && page.context().pages().length === 1,
      'the link asked for a new tab with the decision and opened nothing by itself'
    );
    await waitUntil(async () => (await page.textContent('.modal.show .modal-body a >> nth=0')) === 'Loan Approval Decision', 'and shows its name again afterwards');
    await cancelDeleteDialog();
    await page.click(rgb('open-in-mm'));
    await waitUntil(async () => /link copied/i.test(await page.textContent(rgb('open-in-mm'))), 'Open in SAS Model Manager does the same when no tab can be opened');
    assert((await page.evaluate(() => window.__opened[1])) === `${BASE}/SASModelManager/models/rag-setup-1/files`, 'it asked for the files of the open setup');
    await waitUntil(async () => !/link copied/i.test(await page.textContent(rgb('open-in-mm'))), 'and shows its label again afterwards');
    await openSetup(page, 'rag-setup-2', async () => (await page.inputValue(rgb('setup-doc-modelPurpose'))) === 'Documented before anything else was decided.', 'the setup nothing uses opens');
    shown = await openDeleteDialog('delete-setup');
    assert(shown.body.includes(UNUSED) && shown.links === '', `Delete setup: a setup no decision uses says so (got: ${shown.body})`);
    await cancelDeleteDialog();
    await openSetup(page, 'rag-setup-ss', async () => (await value(page, 'collection')) === 'legacy_manuals', 'the setup whose check fails opens');
    shown = await openDeleteDialog('delete-setup');
    assert(
      shown.body.includes(UNKNOWN) && !shown.body.includes(UNUSED) && shown.links === '',
      `Delete setup: a failed check says that the usage is unknown, not that there is none (got: ${shown.body})`
    );
    await cancelDeleteDialog();
    assert(!(await getLog()).some((e) => e.method === 'DELETE'), 'cancelling the three dialogs deleted nothing');

    // -- Delete project ----------------------------------------------------------
    // The project asks about its three setups at once. Without a session each
    // of the three requests is answered with 449: they share one warm-up.
    await resetLog();
    await noSession('relationships');
    shown = await openDeleteDialog('delete-project');
    assert(shown.title === 'Delete project: RAG Demo Project', `Delete project names the project in the title (got: ${shown.title})`);
    assert(shown.body.includes('This deletes the project and the 3 setup(s) it holds'), `it says how many setups go with it (got: ${shown.body})`);
    assert(
      shown.body.includes('1 of these setup(s) are referenced by decisions, which will break:') &&
        shown.body.includes('Policy Handbook: Loan Approval Decision, Fraud Check Decision'),
      `Delete project: the setup that is used is listed with its decisions by name (got: ${shown.body})`
    );
    assert(shown.links === BOTH_DECISIONS, `Delete project: each of those decisions is a link, as in the dialog of one setup (got: ${shown.links})`);
    assert(
      shown.body.includes('Usage could not be checked for 1 setup(s)') && !shown.body.includes(UNUSED),
      'Delete project: the setup whose check failed is counted, and the dialog does not say that nothing is used'
    );
    const projectRequests = await usageRequests();
    assert(
      projectRequests.slice(0, 3).join(' | ') === Array(3).fill('POST /relationships/relationships 449').join(' | ') &&
        projectRequests[3] === 'GET /relationships/ (plain) 200' &&
        projectRequests.slice(4).sort().join(' | ') === 'POST /relationships/relationships 200 | POST /relationships/relationships 200 | POST /relationships/relationships 503',
      `three requests answered with 449 share one plain GET and are each sent again (got: ${projectRequests.join(' | ')})`
    );
    await cancelDeleteDialog();
    await noSession();

    await setUsage('rag-setup-1', { flows: [] });
    await setUsage('rag-setup-ss', { flows: [] });
    shown = await openDeleteDialog('delete-project');
    assert(
      shown.body.includes(UNUSED) && !shown.body.includes('referenced by decisions') && !shown.body.includes('could not be checked'),
      `Delete project: a project whose setups no decision uses says so (got: ${shown.body})`
    );
    await cancelDeleteDialog();
    for (const setup of ['rag-setup-1', 'rag-setup-2', 'rag-setup-ss']) await setUsage(setup, { status: 503 });
    shown = await openDeleteDialog('delete-project');
    assert(
      shown.body.includes('Usage could not be checked for 3 setup(s)') && !shown.body.includes(UNUSED) && !shown.body.includes('referenced by decisions'),
      `Delete project: when every check failed the dialog says so for all three setups (got: ${shown.body})`
    );
    await cancelDeleteDialog();
    await setUsage();
    assert(!(await getLog()).some((e) => e.method === 'DELETE'), 'cancelling the project dialogs deleted nothing');
    const kept = (await ragState()).models.filter((model) => model.projectId === 'rag-proj').length;
    assert(kept === 3 && (await optionValues(page, 'project')).includes('rag-proj'), `the project and its three setups are still there (got ${kept} setups)`);
  });

  // ---- selection: a delete is about what was selected at the click -----------------
  // The setup list of a project arrives a round trip after the project is
  // chosen, and the usage of a setup a round trip after Delete is clicked.
  // Neither delete may act on what the page held before, or holds afterwards.
  await section('selection', async (page) => {
    const deletes = async () => (await getLog()).filter((e) => e.method === 'DELETE').map((e) => e.url);
    const usageAsked = async () => (await getLog()).filter((e) => e.url.startsWith('/relationships/relationships')).length;
    const selectInPage = (suffix, optionValue, thenClick) =>
      page.evaluate(
        ([selectId, wanted, buttonId]) => {
          const select = document.getElementById(selectId);
          select.value = wanted;
          select.dispatchEvent(new Event('change', { bubbles: true }));
          if (buttonId) document.getElementById(buttonId).click();
        },
        [`app-obj-RGB-${suffix}`, optionValue, thenClick ? `app-obj-RGB-${thenClick}` : '']
      );
    // A second project without setups, beside the fixture project with three.
    const second = await (
      await fetch(BASE + '/modelRepository/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: 'Second RAG project', function: 'RAG', tags: ['LLM', 'RAG-Engineering'] }),
      })
    ).json();
    const secondModels = `**/modelRepository/projects/${second.id}/models?*`;
    await openRag(page);
    await selectRagProject(page, 'rag-proj');
    await waitUntil(async () => (await optionValues(page, 'setup')).length === 4, 'the three setups of the first project are listed');

    // -- Delete project, clicked before the setups of the new selection arrived ---
    await resetLog();
    await selectInPage('project', second.id, 'delete-project');
    let shown = await shownDeleteDialog(page);
    assert(shown.title === 'Delete project: Second RAG project', `the dialog names the project that was selected at the click (got: ${shown.title})`);
    assert(
      shown.body.includes('This project holds no setups.') && !/setup\(s\)/.test(shown.body),
      `it counts the setups of that project, not those the page still listed (got: ${shown.body})`
    );
    assert((await usageAsked()) === 0, 'no setup of the project shown before is asked about');
    await page.click('.modal.show .btn-danger');
    await page.waitForSelector('.modal.show', { state: 'detached' });
    await waitUntil(async () => (await deletes()).length > 0, 'the confirmed delete reaches Model Manager');
    await waitUntil(async () => !(await optionValues(page, 'project')).includes(second.id), 'the deleted project leaves the list');
    assert((await deletes()).join(' | ') === `/modelRepository/projects/${second.id}`, `only the empty project is deleted (got: ${(await deletes()).join(' | ')})`);
    const keptSetups = (await ragState()).models.filter((model) => model.projectId === 'rag-proj').length;
    assert(keptSetups === 3, `the three setups of the other project are still there (got ${keptSetups})`);

    // -- a setup listing that fails ------------------------------------------------
    const third = await (
      await fetch(BASE + '/modelRepository/projects', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: 'Third RAG project', function: 'RAG', tags: ['LLM', 'RAG-Engineering'] }),
      })
    ).json();
    const thirdModels = `**/modelRepository/projects/${third.id}/models?*`;
    await openRag(page);
    await selectRagProject(page, 'rag-proj');
    await waitUntil(async () => (await optionValues(page, 'setup')).length === 4, 'the three setups are listed again');
    await page.route(thirdModels, (route) => route.fulfill({ status: 500, contentType: 'application/json', body: '{"message":"listing failed"}' }));
    await selectRagProject(page, third.id);
    await page.waitForSelector(`${rgb('status')} .alert-danger`);
    assert((await page.textContent(`${rgb('status')} .alert-danger`)).includes('could not be listed'), 'a setup listing that fails is reported on the page');
    assert((await optionValues(page, 'setup')).length === 1, `the setup picker holds its placeholder only, not the setups of the project shown before (got: ${await optionValues(page, 'setup')})`);
    await resetLog();
    await page.click(rgb('delete-project'));
    await waitUntil(async () => ((await page.textContent(`${rgb('status')} .alert-danger`).catch(() => '')) || '').includes('nothing was deleted'), 'Delete project says that nothing was deleted while the setups cannot be listed');
    assert(!(await page.$('.modal.show')) && (await usageAsked()) === 0 && (await deletes()).length === 0, 'it opens no dialog, asks about no setup and deletes nothing');
    await page.unroute(thirdModels);

    // -- two listings that answer out of order --------------------------------------
    // The listing of the third project is held back; the first project is
    // chosen again meanwhile and answers first.
    await page.route(thirdModels, async (route) => {
      await sleep(1200);
      await route.continue().catch(() => {});
    });
    await selectInPage('project', 'rag-proj');
    await waitUntil(async () => (await optionValues(page, 'setup')).length === 4, 'the first project lists its setups again');
    await selectInPage('project', third.id);
    await selectInPage('project', 'rag-proj');
    await waitUntil(async () => (await optionValues(page, 'setup')).length === 4, 'its setups are listed');
    await sleep(1800);
    assert(
      (await value(page, 'project')) === 'rag-proj' && (await optionValues(page, 'setup')).length === 4,
      `a listing that answers late for a project no longer selected is dropped (got: ${await optionValues(page, 'setup')})`
    );
    await page.unroute(thirdModels);

    // -- Delete setup, and another setup chosen while the usage is asked ------------
    await setUsage('rag-setup-1', { flows: [] });
    await setUsage('rag-setup-2', { flows: ['flow-A'] });
    await openSetup(page, 'rag-setup-1', async () => (await value(page, 'collection')) === 'policy_handbook', 'the setup nothing uses opens');
    await resetLog();
    await page.evaluate(() => {
      document.getElementById('app-obj-RGB-delete-setup').click();
      const select = document.getElementById('app-obj-RGB-setup');
      select.value = 'rag-setup-2';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await waitUntil(async () => (await usageAsked()) >= 1, 'the usage of the setup the click was made on is asked');
    await waitUntil(async () => !(await page.isDisabled(rgb('delete-setup'))), 'Delete setup is available again');
    await sleep(600);
    const strayDialog = (await page.$('.modal.show')) ? (await shownDeleteDialog(page)) : null;
    assert(
      !strayDialog,
      `no dialog is shown about a setup that is no longer the selected one (got: ${strayDialog ? `${strayDialog.title} / ${strayDialog.body}` : 'none'})`
    );
    assert((await deletes()).length === 0, 'and nothing is deleted');
    assert((await value(page, 'setup')) === 'rag-setup-2', 'the setup chosen meanwhile stays selected');
    await page.click(rgb('delete-setup'));
    shown = await shownDeleteDialog(page);
    assert(
      shown.title === 'Delete setup: Empty Setup' && shown.body.includes('1 decision(s) reference this and will break:') && shown.links === `Loan Approval Decision -> ${BASE}/SASDecisionManager/decisions/flow-A`,
      `Delete setup on that setup names the decision that uses it (got: ${shown.title} / ${shown.body} / ${shown.links})`
    );
    await cancelDeleteDialogOf(page);
    await setUsage();

    // -- a setup SAS Model Manager refuses -------------------------------------------
    // A name the project already has: the answer is an error body without an id.
    await page.click(`[data-bs-target="${rgb('create-setupModal')}"]`);
    await page.waitForSelector(`${rgb('create-setupModal')}.show`);
    await page.fill(rgb('create-setupName'), 'Policy Handbook');
    await resetLog();
    await page.click(`${rgb('create-setupModal')} .btn-primary`);
    await waitUntil(async () => (await getLog()).some((e) => e.method === 'POST' && e.url === '/modelRepository/models' && e.status === 409), 'the creation of a second setup of that name is refused by the service');
    const statusAlert = async (kind = 'danger') => ((await page.textContent(`${rgb('status')} .alert-${kind}`, { timeout: 500 }).catch(() => '')) || '').trim();
    await waitUntil(async () => (await statusAlert()).includes('Creating the RAG setup failed'), 'a refused setup is reported as not created');
    const createdToasts = (await page.$$eval('.toast-body', (els) => els.map((e) => e.textContent || ''))).filter((text) => text.includes('RAG setup created'));
    assert(createdToasts.length === 0 && (await value(page, 'setup')) === 'rag-setup-2', 'nothing says "created", and the selection is what it was');
    assert((await ragState()).models.filter((model) => model.name === 'Policy Handbook').length === 1, 'there is still one setup of that name');
    await page.click(`${rgb('create-setupModal')} .btn-secondary`);
    await page.waitForSelector('.modal.show', { state: 'detached' });

    // -- a project that was created although the listing after it failed -------------
    const projectListing = '**/modelRepository/projects?*';
    await page.route(projectListing, (route) =>
      route.request().method() === 'GET'
        ? route.fulfill({ status: 503, contentType: 'application/json', body: '{"message":"the model repository does not answer"}' })
        : route.continue()
    );
    await resetLog();
    await page.click(`[data-bs-target="${rgb('create-projectModal')}"]`);
    await page.waitForSelector(`${rgb('create-projectModal')}.show`);
    await page.fill(rgb('create-projectName'), 'ZZ Listed Late');
    await page.click(`${rgb('create-projectModal')} .btn-primary`);
    await waitUntil(async () => (await ragState()).projects.some((project) => project.name === 'ZZ Listed Late'), 'the project is created in Model Manager');
    await waitUntil(async () => (await statusAlert('warning')).includes('ZZ Listed Late'), 'the page reports on the new project, as a warning');
    const createdNote = await statusAlert('warning');
    assert(!(await page.$(`${rgb('status')} .alert-danger`)), 'no failure is reported beside it');
    assert(
      createdNote.includes('"ZZ Listed Late" was created') && createdNote.includes('could not be refreshed') && !createdNote.includes('Creating the RAG project failed'),
      `the page says that the project exists and the list could not be refreshed, not that creating failed (got: ${createdNote})`
    );
    await page.waitForSelector('.modal.show', { state: 'detached' });
    const late = (await ragState()).projects.find((project) => project.name === 'ZZ Listed Late');
    assert((await value(page, 'project')) === late.id && (await optionValues(page, 'setup')).length === 1, 'the new project is selected, with no setup listed');
    assert(!(await page.isVisible(EDITOR)), 'the setup that was open before is closed');
    await page.unroute(projectListing);
    await page.click(rgb('retry-projects'));
    await waitUntil(async () => (await page.$$(`${rgb('status')} .alert`)).length === 0, 'trying again lists the projects and the alert goes');
    assert((await value(page, 'project')) === late.id, 'the new project is still the selected one');
    assert((await ragState()).projects.filter((project) => project.name === 'ZZ Listed Late').length === 1, 'the project was created once');
  });

  // ---- withheld: a setup on a vector database the deployment no longer offers ---
  await section('withheld', async (page) => {
    const backendOptions = () =>
      page.$$eval(`${rgb('backend')} option`, (els) => els.map((o) => `${o.value}${o.dataset.unlisted ? '(kept)' : ''}${o.disabled ? '(disabled)' : ''}`).join(','));
    // all-keys holds the entries of both stores; SingleStore is switched off.
    await openRag(page, `${APP_URL}&credentialDomain=all-keys&enable_singlestore=0`);
    await selectRagProject(page, 'rag-proj');
    await openSetup(page, 'rag-setup-2', async () => (await page.inputValue(rgb('setup-doc-modelPurpose'))) !== '', 'a setup that was never saved opens with its documentation');
    assert((await backendOptions()) === ',pgvector', `a withheld vector database is not offered (got: ${await backendOptions()})`);
    assert((await value(page, 'backend')) === '', 'a setup that was never saved selects no vector database');

    await openSetup(page, 'rag-setup-ss', async () => (await value(page, 'collection')) === 'legacy_manuals', 'the setup on the withheld database opens');
    assert((await value(page, 'backend')) === 'singlestore', 'it keeps naming its vector database');
    assert((await backendOptions()) === ',singlestore(kept),pgvector', `the database is listed for this setup, marked, below the placeholder (got: ${await backendOptions()})`);
    const keptLabel = await page.$eval(`${rgb('backend')} option[data-unlisted]`, (option) => option.textContent);
    assert(keptLabel.startsWith('SingleStore — ') && keptLabel.length > 'SingleStore — '.length, `the option says by name that the database is no longer offered (got: ${keptLabel})`);
    await goRagStep(page, 'run');
    assert(!(await page.isDisabled(rgb('launch'))), 'the setup can still be launched');
    await goRagStep(page, 'store');
    // The note is only worth something if the closed dropdown shows it: the
    // chosen text has to fit between the paddings of the control.
    const [textWidth, room] = await page.$eval(rgb('backend'), (select) => {
      const style = getComputedStyle(select);
      const pen = document.createElement('canvas').getContext('2d');
      pen.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
      return [
        Math.ceil(pen.measureText(select.selectedOptions[0].textContent).width),
        Math.floor(select.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)),
      ];
    });
    assert(textWidth <= room, `the closed dropdown shows the name and the note uncut (${textWidth}px of text in ${room}px)`);
    await resetLog();
    await page.click(rgb('save'));
    await page.waitForSelector(`${rgb('save-result')} .alert-success`);
    const log = await getLog();
    const saved = JSON.parse(multipartFile(lastUpload(log, 'rag-setup-ss', 'rag-setup.json').body));
    assert(
      saved.store.backend === 'singlestore' && saved.store.host === 's2.mock.local' && saved.store.port === 3306 && saved.store.database === 'ragdb_legacy',
      `saving keeps the setup on its database, with that store's location and default port (got: ${JSON.stringify(saved.store)})`
    );
    const savedTags = log.filter((e) => e.method === 'PUT' && e.url === '/modelRepository/models/rag-setup-ss').map((e) => JSON.parse(e.body).tags).pop() || [];
    assert(savedTags.includes('singlestore') && !savedTags.includes('pgvector'), `and its tag (got: ${savedTags.join(', ')})`);
    assert(log.filter((e) => e.method === 'PUT' && e.url === '/modelRepository/models/rag-setup-ss').every((e) => e.ifMatch), 'the model is updated under its ETag');

    await goRagStep(page, 'setup');
    await openSetup(page, 'rag-setup-1', async () => (await value(page, 'collection')) === 'policy_handbook', 'another setup opens');
    assert((await value(page, 'backend')) === 'pgvector' && (await backendOptions()) === ',pgvector', `the kept database is gone from the list again (got: ${await backendOptions()})`);
    assert((await page.$$('#app-obj-RGB option[data-unlisted]')).length === 0, 'no picker carries a leftover of the setup shown before');

    // The same setup for a user whose credential domain does not hold that store.
    await openRag(page, `${APP_URL}&enable_singlestore=0`);
    await selectRagProject(page, 'rag-proj');
    await openSetup(page, 'rag-setup-ss', async () => (await value(page, 'collection')) === 'legacy_manuals', 'the setup opens without the store entries too');
    assert((await value(page, 'backend')) === 'singlestore', 'it still names its vector database');
    await goRagStep(page, 'store');
    await resetLog();
    await page.click(rgb('save'));
    await page.waitForSelector(`${rgb('save-result')} .alert-danger li`);
    assert(uploads(await getLog(), 'rag-setup-ss', 'rag-setup.json').length === 0, 'saving is refused while the credential domain cannot say where that store lives');
  });

  // ---- failure: a RAG project listing that fails ---------------------------------
  await section('failure', async (page) => {
    await armFailure({ projects: 500 });
    await openRag(page);
    await page.waitForSelector(`${rgb('status')} .alert-danger`);
    step(true, 'a failed RAG project listing is reported in the RAG flow');
    assert(await page.isVisible(rgb('retry-projects')), 'the alert offers to try again');
    assert((await visibleSteps(page, 'RGB')) === 'setup,documents,store,run', 'the RAG flow is complete all the same');
    assert((await optionValues(page, 'project')).length === 1, 'its project list holds the placeholder only');
    const alertsOutside = await page.$$eval('.alert-danger', (els) => els.filter((e) => e.getClientRects().length > 0 && !e.closest('#app-obj-RGB')).length);
    assert(alertsOutside === 0, 'the failure is reported inside the RAG Builder and nowhere else');
    if (await page.$('#app-obj-LPB')) {
      // The prompt side is untouched and one choice away.
      await page.click('#RGB-kind-llm');
      await page.waitForSelector('#app-obj-LPB-stepper', { state: 'visible' });
      await page.selectOption('#LPB-project-dropdown', 'proj-1');
      await waitUntil(async () => (await page.$$('#LPB-prompt-dropdown option')).length === 4, 'the prompt side lists its prompts');
      step(true, 'the prompt flow keeps working beside a RAG flow that could not list its projects');
      await page.click('#LPB-kind-rag');
      await page.waitForSelector('#app-obj-RGB-stepper', { state: 'visible' });
      assert(await page.isVisible(rgb('retry-projects')), 'the alert is still there on return');
    }
    const projectListings = async () => (await getLog()).filter((e) => e.method === 'GET' && isRagRequest(e) && e.url.startsWith('/modelRepository/projects?')).length;
    await resetLog();
    await page.click(rgb('retry-projects'));
    await waitUntil(async () => (await projectListings()) === 1, 'trying again asks for the projects again');
    await sleep(300);
    assert(await page.isVisible(rgb('retry-projects')), 'while the listing still fails the alert stays');
    await armFailure({});
    await page.click(rgb('retry-projects'));
    await waitUntil(async () => (await optionValues(page, 'project')).includes('rag-proj'), 'trying again lists the projects once the service answers');
    await waitUntil(async () => (await page.$$(`${rgb('status')} .alert`)).length === 0, 'and the alert goes');
    await selectRagProject(page, 'rag-proj');
    await openSetup(page, 'rag-setup-1', async () => (await value(page, 'collection')) === 'policy_handbook', 'a setup opens after the retry');
  });

  // ---- gates: what RAG needs before it can be used -------------------------------
  await section('gates', async (page) => {
    // RAG needs a real credential domain: with none, the card is there and cannot be chosen.
    await resetLog();
    await page.goto(`${APP_URL}&credentialDomain=none`);
    await page.waitForSelector('#LPB-project-dropdown', { timeout: 15000 });
    assert(await page.$('#LPB-kind-rag'), 'with credentialDomain=none the RAG card is still shown');
    assert(await page.isDisabled('#LPB-kind-rag'), 'and cannot be chosen');
    assert(((await page.textContent('#LPB-kind-rag-reason')) || '').trim().length > 20, 'it says why');
    await page.click('label[for="LPB-kind-rag"]', { force: true });
    await sleep(400);
    assert(await page.isChecked('#LPB-kind-llm') && !(await page.$('#app-obj-RGB')), 'a click on it builds nothing');
    const requested = (await getLog()).filter((e) => isRagRequest(e) || e.url.startsWith('/credentials/'));
    assert(requested.length === 0, `no credential and no RAG request is made (got: ${requested.map((e) => e.url).join(' | ')})`);

    // The ingestion compute context is its own option: the context of the
    // optimization job does not stand in for it.
    await openRag(page, `${BASE}/?${PROMPT}&${RAG}&computeContext=opt-ctx`);
    await selectRagProject(page, 'rag-proj');
    await openSetup(page, 'rag-setup-1', async () => (await value(page, 'collection')) === 'policy_handbook', 'a manifested setup opens');
    await goRagStep(page, 'run');
    assert(!(await page.isDisabled(rgb('launch'))), 'the setup has an ingestion job to launch');
    await resetLog();
    await page.click(rgb('launch'));
    await page.waitForSelector(`${rgb('status')} .alert-danger`);
    await page.click(rgb('test'));
    await page.fill(rgb('test-question'), 'What does the handbook say about leave?');
    await page.click(rgb('test-run'));
    await waitUntil(async () => (await page.textContent(rgb('test-status'))).trim().length > 20, 'the retrieval test names the missing compute context');
    const started = (await getLog()).filter((e) => e.method === 'POST' && (e.url === '/jobExecution/jobs' || e.url.startsWith('/jobDefinitions/')));
    assert(started.length === 0, 'without ragComputeContext nothing is launched, whatever computeContext says');
  });

  // ---- ragonly: a page without the prompt side ----------------------------------
  await section('ragonly', async (page) => {
    await resetLog();
    await page.goto(`${BASE}/?modelRepositoryID=repo-1&${RAG}&${RAG_CONTEXT}`);
    await page.waitForSelector('#app-obj-RGB-stepper', { state: 'visible', timeout: 15000 });
    step(true, 'a page with RAG configured and no LLM project opens on the RAG flow');
    assert((await visibleSteps(page, 'RGB')) === 'setup,documents,store,run', 'with its four steps');
    assert(!(await page.$('#app-obj-LPB')), 'no Prompt Builder is built');
    assert(!(await page.isVisible('#RGB-kind-options')), 'with one kind there is no kind to choose');
    assert((await page.$$('h1')).length === 1, 'the page has exactly one h1');
    await waitUntil(async () => (await optionValues(page, 'project')).includes('rag-proj'), 'the RAG projects are listed');
    const promptRequests = (await getLog()).filter((e) => /llm-proj|Prompt-Engineering|Decision/.test(decodeURIComponent(e.url)) || e.url.startsWith('/scr/'));
    assert(promptRequests.length === 0, `no request of the prompt side is made (got: ${promptRequests.map((e) => e.url).join(' | ')})`);
  });

  assert(pageErrors.length === 0, `no uncaught page errors (got: ${pageErrors.join(' | ')})`);
  await browser.close();
  console.log('\n===== SUMMARY =====');
  console.log(results.join('\n'));
  console.log(`sections run: ${sectionsRun.join(', ')}${WANTED.length ? ' (a subset - VERIFY_RAG_SECTIONS is set)' : ''}`);
  console.log(`${results.filter((r) => r.startsWith('OK')).length} checks passed, ${results.filter((r) => r.startsWith('FAIL')).length} failed`);
})().catch((e) => {
  console.error('VERIFY FAILED:', e.message);
  process.exit(1);
});
