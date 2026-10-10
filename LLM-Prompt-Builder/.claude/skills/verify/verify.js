/**
 * Drives the built Prompt Builder (served by mock-server.js) through the new
 * deletion flows with Playwright + system Edge and asserts both the UI state
 * and the HTTP requests the app issued against the mock.
 *
 * The mock is found at VERIFY_BASE (default http://localhost:4173). Every
 * score code a manifest uploads is compiled, and a hostile one is run, with a
 * real Python interpreter (see python-check.cjs, which has to sit next to
 * this file).
 */
const { chromium } = require('playwright');
const { resolvePython, compilePython, callScoreModel } = require('./python-check.cjs');

const BASE = (process.env.VERIFY_BASE || 'http://localhost:4173').replace(/\/+$/, '');
const results = [];
const dialogs = [];

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
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitUntil(fn, label, timeout = 8000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await fn()) return;
    await sleep(100);
  }
  throw new Error('timeout waiting for: ' + label);
}

function multipartJson(body) {
  const m = body.match(/\r\n\r\n([\s\S]*?)\r\n--/);
  return m ? JSON.parse(m[1]) : null;
}
// The score code of a manifest upload, without its multipart wrapping.
const scoreCodeOf = (body) => body.match(/import os[\s\S]*?(?=\r\n--)/)[0];
// The last line of an interpreter message is the one that names the error.
const lastLine = (message) => String(message || '').trim().split('\n').pop();
function assertCompiles(code, label) {
  const compiled = compilePython(code, 'llm_manifest.py');
  assert(compiled.ok, `${label} compiles without a warning (${compiled.python}${compiled.ok ? '' : ': ' + lastLine(compiled.error)})`);
}

(async () => {
  // Without an interpreter the generated score code would go unchecked: a failure, not a skip.
  const python = resolvePython();
  assert(python, `a Python interpreter is available for the score code checks (${python ? `${python.command} ${python.args.join(' ')} = ${python.version}` : 'none found - set VERIFY_PYTHON'})`);

  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await browser.newPage({ viewport: { width: 1500, height: 1100 } });
  const pageErrors = [];
  page.on('pageerror', (e) => {
    pageErrors.push(e.message);
    console.log('[pageerror]', e.message);
  });
  page.on('console', (m) => {
    if (m.type() === 'error') console.log('[console.error]', m.text());
  });
  page.on('dialog', (d) => {
    dialogs.push(d.message());
    d.accept();
  });

  const runHeaderTexts = () =>
    page.$$eval('#app-obj-LPB-pet > .accordion > .accordion-item > h2 > .accordion-button', (els) =>
      els.map((e) => e.textContent.trim())
    );

  // The page is a flow of steps (setup, build, finalize - plus optimize when
  // the deployment enables it); only the current step's pane is visible, so a
  // flow opens the step it acts on.
  const goStep = async (key) => {
    await page.click(`#app-obj-LPB-step-${key}`);
    await page.waitForSelector(`#app-obj-LPB-pane-${key}:not([hidden])`);
  };
  const currentStep = () =>
    page.$eval('#app-obj-LPB-stepper .pb-step.is-current .pb-step-button', (el) => el.id.replace('app-obj-LPB-step-', ''));

  const APP_URL = `${BASE}/?modelRepositoryID=repo-1&llmProjectID=llm-proj&SCREndpoint=${BASE}/scr`;
  await page.goto(APP_URL);
  await page.waitForSelector('#LPB-project-dropdown', { timeout: 15000 });
  step(true, 'app booted against the mock (project dropdown rendered)');

  // ---- UX: heading hierarchy, sections, gated run button, modal copy --------
  assert((await page.$$('h1')).length === 1, 'exactly one h1 on the page');
  assert((await page.$$('#app-obj-LPB .pb-section')).length === 7, 'the Prompt Builder is grouped into seven visual sections (incl. Judging and Evaluate)');
  assert(
    !(await page.$('#app-obj-RGB')) && (await page.$$('.pb-section')).length === 7,
    'nothing of the RAG Builder is on a page that has not switched RAG on'
  );
  assert(await page.isDisabled('#app-obj-LPB-run-experiment'), 'Run Experiments disabled until an LLM is selected');
  assert((await currentStep()) === 'setup', 'the flow opens on the Setup step');
  const stepKeys = (target) =>
    // Only the visible steps of the Prompt Builder's own row: the Evaluate
    // step is in it whenever decision templates are switched on and hidden
    // unless one is open, and a page that has opened the RAG flow holds a
    // second row.
    target.$$eval('#app-obj-LPB-stepper .pb-step-button', (els) =>
      els.filter((e) => !e.closest('.pb-step').hidden).map((e) => e.id.replace('app-obj-LPB-step-', '')).join(',')
    );
  assert(
    (await stepKeys(page)) === 'setup,build,finalize',
    `three steps while optimization is disabled (got: ${await stepKeys(page)})`
  );
  assert(await page.isHidden('#app-obj-LPB-stepper-back'), 'no Back on the first step');
  assert((await page.$$('#app-obj-LPB .pb-instructions')).length === 7, 'every card carries an Instructions box');
  await goStep('build');
  assert(await page.isVisible('#app-obj-LPB-pet-empty'), 'tracker shows an empty-state hint');
  assert(await page.isVisible('#model0'), 'the LLM selection is part of Build & Test');
  assert(await page.isVisible('#app-obj-LPB-judge-model'), 'the judge settings are part of Build & Test');
  assert(!(await page.isVisible('#LPB-project-dropdown')), 'the Setup pane is hidden while building');
  await goStep('setup');
  // Optimize is a step of its own, and only exists when the deployment enables it.
  const optimizePage = await browser.newPage({ viewport: { width: 1500, height: 1100 } });
  await optimizePage.goto(
    `${APP_URL}&enableOptimization=true&computeContext=ctx&optimizeJobProgram=/Public/Jobs/Optimize-Prompt-DSPy`
  );
  await optimizePage.waitForSelector('#LPB-project-dropdown', { timeout: 15000 });
  assert(
    (await stepKeys(optimizePage)) === 'setup,build,optimize,finalize',
    `Optimize becomes step three when enabled, Finalize stays last (got: ${await stepKeys(optimizePage)})`
  );
  await optimizePage.click('#app-obj-LPB-step-optimize');
  assert(await optimizePage.isVisible('#app-obj-LPB-optimize-run'), 'the optimize card sits on its own step');
  assert(!(await optimizePage.isVisible('#app-obj-LPB-pet-create-model-button')), 'Manifest stays on Finalize');
  await optimizePage.close();
  // Decision templates are a switch of the object. Off, nothing of them is
  // built, and with one kind left there is no kind to choose.
  const plainPage = await browser.newPage({ viewport: { width: 1500, height: 1100 } });
  await plainPage.goto(`${APP_URL}&enableDecision=false`);
  await plainPage.waitForSelector('#LPB-project-dropdown', { timeout: 15000 });
  assert(
    (await stepKeys(plainPage)) === 'setup,build,finalize',
    `the same three steps with decision templates switched off (got: ${await stepKeys(plainPage)})`
  );
  assert(
    !(await plainPage.$('#app-obj-LPB-step-evaluate')) && !(await plainPage.$('#app-obj-LPB-pane-evaluate')),
    'switched off, the Evaluate step and its pane are not built at all'
  );
  assert(
    (await plainPage.$$('#app-obj-LPB .pb-section')).length === 6 &&
      (await plainPage.$$('#app-obj-LPB .pb-instructions')).length === 6,
    'which leaves six cards, each with its Instructions box'
  );
  assert(
    (await plainPage.$$('#LPB-kind-options .pb-kind-option')).length === 1 &&
      !(await plainPage.$('#LPB-kind-decision')) &&
      !(await plainPage.isVisible('#LPB-kind-options')),
    'the one kind that is left is not offered as a choice'
  );
  await plainPage.close();
  // With RAG switched on the page can hold two builders at once. Everything
  // above counts inside the Prompt Builder, and still holds beside the other.
  const mergedPage = await browser.newPage({ viewport: { width: 1500, height: 1100 } });
  await mergedPage.goto(`${APP_URL}&enableRag=true&embeddingProjectID=emb-proj`);
  await mergedPage.waitForSelector('#LPB-project-dropdown', { timeout: 15000 });
  const kindIds = await mergedPage.$$eval('#LPB-kind-options .pb-kind-option input', (els) => els.map((e) => e.id).join(','));
  assert(
    kindIds === 'LPB-kind-llm,LPB-kind-decision,LPB-kind-rag',
    `with RAG switched on the Setup card offers three kinds (got: ${kindIds})`
  );
  assert(!(await mergedPage.$('#app-obj-RGB')), 'the RAG Builder is not built before it is chosen');
  await mergedPage.click('#LPB-kind-rag');
  await mergedPage.waitForSelector('#app-obj-RGB-stepper', { state: 'visible', timeout: 15000 });
  assert(
    (await mergedPage.$$('#app-obj-LPB .pb-section')).length === 7 && (await mergedPage.$$('.pb-section')).length > 7,
    'beside the RAG Builder the Prompt Builder still has its seven cards'
  );
  assert(
    (await stepKeys(mergedPage)) === 'setup,build,finalize' &&
      (await mergedPage.$$('#app-obj-LPB-stepper .pb-step.is-current')).length === 1 &&
      (await mergedPage.$$('#app-obj-RGB-stepper .pb-step.is-current')).length === 1,
    `each flow keeps a step row of its own with one current step (prompt steps: ${await stepKeys(mergedPage)})`
  );
  assert((await mergedPage.$$('h1')).length === 1, 'two builders on the page, still exactly one h1');
  await mergedPage.close();
  assert(
    await page.isDisabled('#app-obj-LPB-pet-create-model-button'),
    'Manifest disabled until a best response exists'
  );
  assert(
    (await page.getAttribute('#app-obj-LPB-pet-create-model-button', 'title')).length > 0,
    'disabled Manifest button carries a hint'
  );
  const brandColor = await page.evaluate(
    () => getComputedStyle(document.getElementById('app-obj-LPB-run-experiment')).backgroundColor
  );
  assert(brandColor === 'rgb(7, 102, 209)', `SAS-blue accent applied to primary buttons (got: ${brandColor})`);
  const deleteRightAligned = await page.evaluate(() => {
    const buttonRow = document.getElementById('LPB-modal-button-container');
    const deleteProject = document.getElementById('LPB-delete-project-button');
    return buttonRow.getBoundingClientRect().right - deleteProject.getBoundingClientRect().right < 40;
  });
  assert(deleteRightAligned, 'destructive actions right-aligned in the button row');
  assert(
    (await page.getAttribute('#app-obj-LPB-run-experiment', 'title')).length > 0,
    'disabled Run Experiments carries a hint'
  );
  await page.click('#LPB-modal-button-container button[data-bs-target="#promptBuilderCreatePromptModal"]');
  await page.waitForSelector('.modal.show .modal-body');
  const createModalBody = await page.textContent('.modal.show .modal-body');
  assert(
    createModalBody.includes('saved into the currently selected project'),
    'create-prompt explanation moved into the modal body'
  );
  assert(
    (await page.textContent('.modal.show .modal-title')).trim() === 'Create a new Prompt',
    'create-prompt button/title shortened'
  );
  await page.click('.modal.show .btn-close');
  await page.waitForSelector('.modal.show', { state: 'detached' });

  // ---- load a prompt with 3 saved runs -------------------------------------
  await page.selectOption('#LPB-project-dropdown', 'proj-1');
  await waitUntil(
    async () => (await page.$$('#LPB-prompt-dropdown option')).length === 4,
    'prompt dropdown filled'
  );
  assert(!(await page.isDisabled('#LPB-delete-project-button')), 'Delete Project enabled after project selection');
  assert(await page.isDisabled('#LPB-delete-prompt-button'), 'Delete Prompt still disabled (no prompt selected)');

  // ---- name/user filters on the selection lists ------------------------------
  const promptUserOptions = await page.$$eval('#LPB-prompt-filter-user option', (els) => els.map((e) => e.value));
  assert(
    JSON.stringify(promptUserOptions) === JSON.stringify(['', 'anna', 'ben', 'carla']),
    `user filter lists the distinct createdBy/modifiedBy users (got: ${promptUserOptions})`
  );
  await page.fill('#LPB-prompt-filter-name', 'score');
  await waitUntil(
    async () => (await page.$$('#LPB-prompt-dropdown option')).length === 2,
    'name filter narrows the prompt list'
  );
  step(true, 'prompt list filtered by name ("score" leaves one match)');
  await page.fill('#LPB-prompt-filter-name', '');
  await page.selectOption('#LPB-prompt-filter-user', 'carla');
  await waitUntil(
    async () => (await page.$$('#LPB-prompt-dropdown option')).length === 2,
    'user filter narrows the prompt list'
  );
  const carlaOptions = await page.$$eval('#LPB-prompt-dropdown option', (els) => els.map((e) => e.textContent));
  assert(carlaOptions.includes('Error Prompt'), `filtering by user keeps only their prompts (got: ${carlaOptions})`);
  await page.selectOption('#LPB-prompt-filter-user', '');
  await waitUntil(
    async () => (await page.$$('#LPB-prompt-dropdown option')).length === 4,
    'clearing the user filter restores the list'
  );
  assert((await page.$$('#LPB-project-filter-name')).length === 1, 'project list has its own filter row');

  await page.selectOption('#LPB-prompt-dropdown', 'model-used');
  await waitUntil(async () => (await page.$$('.pet-run-delete')).length === 3, '3 runs rendered');
  step(true, 'tracker with GAPPED runIds (1,2,5) and a two-model run loaded without errors');
  // probe: the active selection survives a non-matching filter
  await page.fill('#LPB-prompt-filter-name', 'zzz');
  await waitUntil(
    async () => (await page.$$('#LPB-prompt-dropdown option')).length === 2,
    'filter narrowed the list around the selection'
  );
  assert(
    (await page.inputValue('#LPB-prompt-dropdown')) === 'model-used',
    'active selection stays visible and selected under a non-matching filter'
  );
  await page.fill('#LPB-prompt-filter-name', '');
  await waitUntil(async () => (await page.$$('#LPB-prompt-dropdown option')).length === 4, 'filter cleared');
  let headers = await runHeaderTexts();
  assert(
    headers.join('|') === 'Prompt Experiment Run #3|Prompt Experiment Run #2|Prompt Experiment Run #1',
    `3 runs numbered 1..3 newest-first (got: ${headers.join('|')})`
  );
  assert(!(await page.isDisabled('#LPB-delete-prompt-button')), 'Delete Prompt enabled after prompt selection');
  // The link is styled as a button, so it keeps the spacing real buttons get
  // (1em of the step shell's font size).
  const [mmGap, buttonGap] = await page.evaluate(() => [
    getComputedStyle(document.getElementById('LPB-openInMMButton')).marginRight,
    getComputedStyle(document.querySelector('#LPB-modal-button-container button')).marginRight,
  ]);
  assert(
    parseFloat(mmGap) > 0 && mmGap === buttonGap,
    `gap between Open-in-MM link and Delete Prompt button (margin-right: ${mmGap}, buttons: ${buttonGap})`
  );
  await page.screenshot({ path: 'shot-01-three-runs.png', fullPage: true });

  // ============ PHASE A: variables manager + load-run features ============

  // ---- most recent best run auto-loads on prompt selection -----------------
  await waitUntil(
    async () => (await page.inputValue('#app-obj-LPB-system-prompt')) === 'Sys 2',
    'best run auto-loaded'
  );
  step(true, 'most recent best run (fixture run 2) auto-loaded on prompt selection');
  assert(await page.isChecked('#model0'), 'auto-load reselected the best run LLM');
  assert(
    !(await page.isDisabled('#app-obj-LPB-run-experiment')),
    'Run Experiments enabled once an LLM is selected'
  );
  assert(
    await page.$eval('#app-obj-LPB-step-setup', (el) => el.parentElement.classList.contains('is-current')),
    'selecting a prompt stays on Setup'
  );
  await page.click('#app-obj-LPB-stepper-continue');
  assert((await currentStep()) === 'build', 'Continue opens Build & Test');
  assert(
    (await page.$$('#app-obj-LPB-stepper .pb-step.is-current')).length === 1 &&
      (await page.$$('#app-obj-LPB-stepper .pb-step-marker svg')).length === 0,
    'only the current step is highlighted - no step is ever marked as done'
  );
  assert(
    (await page.textContent('#app-obj-LPB-pet-summary')).includes('3'),
    `tracker summary counts the loaded runs (got: ${await page.textContent('#app-obj-LPB-pet-summary')})`
  );
  assert((await page.$$('#app-obj-LPB-pet-legend li')).length === 8, 'tracker legend explains all eight icons');
  assert(!(await page.isVisible('#app-obj-LPB-pet-empty')), 'empty-state hint hidden once runs exist');
  assert(
    !(await page.isDisabled('#app-obj-LPB-pet-create-model-button')),
    'Manifest enabled (loaded tracker has a best response)'
  );
  // probe: option explanations are keyboard/touch-friendly Bootstrap tooltips
  await page.hover('#options0 .info-icon');
  await page.waitForSelector('.tooltip');
  const tooltipText = await page.textContent('.tooltip');
  assert(tooltipText.includes('Temperature is a parameter'), 'option info shown as Bootstrap tooltip on hover');
  await page.mouse.move(0, 0);

  // ---- load an experiment run back into the workbench ----------------------
  await page.click('#app-obj-LPB-pet-2 .pet-run-load');
  await waitUntil(
    async () => (await page.inputValue('#app-obj-LPB-system-prompt')) === 'Sys 3',
    'system prompt loaded from run'
  );
  step(true, 'per-run Load restored the system prompt');
  assert((await currentStep()) === 'build', 'loading a run stays on Build & Test');
  assert((await page.inputValue('#app-obj-LPB-user-prompt')) === 'User 3', 'user prompt loaded from run');
  assert(await page.isChecked('#model0'), 'demo_llm reselected by loading the run');
  assert((await page.inputValue('#temperature0')) === '0.9', `run's non-default temperature restored (got: ${await page.inputValue('#temperature0')})`);
  await page.waitForSelector('.toast-body');
  const toastText = await page.textContent('.toast-body');
  assert(toastText.includes('other_llm'), `toast reports the unavailable LLM (got: ${toastText})`);
  await page.screenshot({ path: 'shot-07-load-run-toast.png', fullPage: true });

  // ---- variables manager: define, validate, insert via context menu --------
  await page.click('text="Add Variable"');
  await page.click('text="Add Variable"');
  const varRows = page.locator('.pb-variable-row');
  await varRows.nth(0).locator('.pb-var-name').fill('customer');
  await varRows.nth(0).locator('.pb-var-description').fill('Customer name');
  await varRows.nth(0).locator('.pb-var-value').fill('ACME Corp');
  await varRows.nth(1).locator('.pb-var-name').fill('amount');
  await varRows.nth(1).locator('.pb-var-type').selectOption('decimal');
  await varRows.nth(1).locator('.pb-var-value').fill('42');
  step(true, 'defined a string and a decimal variable');
  // probe: invalid name gets flagged and is excluded
  await page.click('text="Add Variable"');
  await varRows.nth(2).locator('.pb-var-name').fill('9bad');
  assert(
    await varRows.nth(2).locator('.pb-var-name').evaluate((el) => el.classList.contains('is-invalid')),
    'invalid variable name (starts with a digit) is flagged'
  );
  await varRows.nth(2).locator('.pb-var-remove').click();

  await page.fill('#app-obj-LPB-system-prompt', 'Hello {{customer}} with {json} braces');
  await page.fill('#app-obj-LPB-user-prompt', 'Value: ');
  await page.focus('#app-obj-LPB-user-prompt');
  await page.keyboard.press('Control+End');
  await page.click('#app-obj-LPB-user-prompt', { button: 'right' });
  await page.waitForSelector('.pb-variable-menu');
  await page.screenshot({ path: 'shot-09-context-menu.png' });
  await page.click('.pb-variable-menu .dropdown-item:has-text("amount")');
  await waitUntil(
    async () => (await page.inputValue('#app-obj-LPB-user-prompt')) === 'Value: {{amount}}',
    'context menu inserted the token'
  );
  step(true, 'right-click context menu inserted {{amount}} at the cursor');
  await page.focus('#app-obj-LPB-user-prompt');
  await page.keyboard.press('Control+End');
  await page.keyboard.type(' and {{unknown}} stays');

  // ---- run an experiment: values substituted, snapshot stored --------------
  await resetLog();
  await page.click('#app-obj-LPB-run-experiment');
  await waitUntil(async () => (await page.$$('.pet-run-delete')).length === 4, 'new experiment run rendered');
  assert((await currentStep()) === 'build', 'a finished run stays on Build & Test, above its tracker');
  let aLog = await getLog();
  const scrCall = aLog.find((e) => e.method === 'POST' && e.url === '/scr/demo_llm/demo_llm');
  assert(scrCall, 'SCR LLM endpoint was called');
  const scrInputs = JSON.parse(scrCall.body).inputs;
  const scrSystem = scrInputs.find((i) => i.name === 'systemPrompt').value;
  const scrUser = scrInputs.find((i) => i.name === 'userPrompt').value;
  assert(scrSystem === 'Hello ACME Corp with {json} braces', `system prompt substituted for the LLM (got: ${scrSystem})`);
  assert(scrUser === 'Value: 42 and {{unknown}} stays', `user prompt substituted; unknown token kept literal (got: ${scrUser})`);
  const varLine = await page.textContent('#app-obj-LPB-pet-3-run-variables');
  assert(
    varLine.includes('customer (string): ACME Corp') && varLine.includes('amount (decimal): 42'),
    `new run lists its variable snapshot (got: ${varLine.trim()})`
  );

  // ---- manifest: variables become the model inputs and f-string slots ------
  await page.click('#app-obj-LPB-pet-3 > .accordion-item > h2 > .accordion-button');
  await page.click('#app-obj-LPB-pet-3-run-nested-demo_llm .accordion-button');
  await page.check('#best-prompt-3-demo_llm');
  await goStep('finalize');
  assert(await page.isHidden('#app-obj-LPB-stepper-continue'), 'no Continue on the last step');
  await resetLog();
  await page.click('#app-obj-LPB-pet-create-model-button');
  await waitUntil(
    async () => (await getLog()).some((e) => e.method === 'POST' && e.body.includes('def scoreModel(')),
    'score code uploaded'
  );
  await waitUntil(async () => {
    const toastTexts = await page.$$eval('.toast-body', (els) => els.map((e) => e.textContent || ''));
    return toastTexts.some((t) => t.includes('manifested')) && toastTexts.some((t) => t.includes('saved'));
  }, 'save + manifest toasts');
  step(true, 'save and manifest success reported via toasts');
  // Manifesting shows the link in the manifest box (not next to Save).
  await waitUntil(async () => Boolean(await page.$('#app-obj-LPB-pet-save-result a')), 'manifest link in the manifest box');
  step(true, 'manifest shows the Model Manager link in the manifest box');
  aLog = await getLog();
  const findPart = (name) => aLog.find((e) => e.method === 'POST' && e.body.includes(`filename="${name}"`));
  const savedTracker = multipartJson(findPart('Prompt-Experiment-Tracker.json').body);
  const run4Header = savedTracker.find((r) => r.runId === 4 && r.model === '');
  assert(
    Array.isArray(run4Header.variables) && run4Header.variables.length === 2 && run4Header.variables[0].name === 'customer',
    'tracker header row persists the variable definitions'
  );
  const inputVars = multipartJson(findPart('inputVar.json').body);
  assert(
    JSON.stringify(inputVars.map((v) => [v.name, v.type, v.length, v.level])) ===
      JSON.stringify([['customer', 'string', 10000000, 'nominal'], ['amount', 'decimal', 8, 'interval']]),
    `manifest inputs derived from the referenced variables, strings 10M long (got: ${JSON.stringify(inputVars)})`
  );
  assert(inputVars[0].description === 'Customer name', 'variable description carried into the model input');
  const pyBody = aLog.find((e) => e.method === 'POST' && e.body.includes('def scoreModel(')).body;
  assert(pyBody.includes('def scoreModel(customer, amount):'), 'score function signature built from the variables');
  assert(
    pyBody.includes('systemPrompt = f"""Hello {str(customer).strip()} with {{json}} braces"""'),
    'system prompt manifested as f-string with escaped literal braces'
  );
  assert(
    pyBody.includes('userPrompt = f"""Value: {str(amount).strip()} and {{{{unknown}}}} stays"""'),
    'user prompt manifested as f-string; unknown tokens stay literal'
  );
  assert(pyBody.includes('return llmBody, llmURL'), 'default manifest returns llmBody/llmURL for the Call LLM node');
  assertCompiles(scoreCodeOf(pyBody), 'the Call-LLM-node score code');
  const tagPut = aLog.find((e) => e.method === 'PUT' && e.url === '/modelRepository/models/model-used');
  assert(tagPut, 'manifest PUTs the model to update its tags');
  assert(tagPut.ifMatch === '"abc123"', `tag update sends the model ETag as If-Match (got: ${tagPut.ifMatch})`);
  const putTags = JSON.parse(tagPut.body).tags;
  assert(
    JSON.stringify(putTags) === JSON.stringify(['LLM', 'Prompt-Template', 'Custom-Tag', 'demo_llm']),
    `LLM tag added, stale manifest tags (other_llm, LLM-Call-Included) removed, custom tags kept (got: ${putTags})`
  );
  const outputVarsDefault = multipartJson(findPart('outputVar.json').body);
  assert(
    JSON.stringify(outputVarsDefault.map((v) => v.name)) === JSON.stringify(['llmBody', 'llmURL']),
    `default manifest outputs llmBody/llmURL (got: ${outputVarsDefault.map((v) => v.name)})`
  );
  assert(
    aLog.some((e) => e.method === 'DELETE' && e.url === '/modelRepository/models/model-used/contents/c-req'),
    'stale requirements.json from an earlier integrated manifest is removed by a Call-LLM-node manifest'
  );
  assert(!findPart('requirements.json'), 'Call-LLM-node manifest does not upload a requirements.json');

  // ---- manifest with the LLM call integrated into the model -----------------
  assert(
    !(await page.isVisible('#app-obj-LPB-pet-manifest-options')),
    'manifest options stay hidden until the LLM call is integrated'
  );
  await page.check('#app-obj-LPB-pet-manifest-integrated');
  assert(
    await page.isVisible('#app-obj-LPB-pet-manifest-options'),
    'manifest options revealed by checking the integrated-call box'
  );
  await resetLog();
  await page.click('#app-obj-LPB-pet-create-model-button');
  await waitUntil(
    async () => (await getLog()).some((e) => e.method === 'POST' && e.body.includes('def scoreModel(')),
    'integrated score code uploaded'
  );
  await waitUntil(
    async () => (await getLog()).some((e) => e.method === 'POST' && e.body.includes('filename="requirements.json"')),
    'requirements.json uploaded with the integrated manifest'
  );
  aLog = await getLog();
  const findPart2 = (name) => aLog.find((e) => e.method === 'POST' && e.body.includes(`filename="${name}"`));
  const reqUpload = findPart2('requirements.json');
  assert(
    reqUpload.url.includes('role=python%20pickle'),
    `requirements.json registered with the python pickle role like the LLM definitions (got: ${reqUpload.url})`
  );
  const reqJson = multipartJson(reqUpload.body);
  assert(
    Array.isArray(reqJson) && reqJson.length === 1 && reqJson[0].command === 'pip3 -q install requests',
    `requirements.json installs the requests package (got: ${JSON.stringify(reqJson)})`
  );
  const outputVarsIntegrated = multipartJson(findPart2('outputVar.json').body);
  assert(
    JSON.stringify(outputVarsIntegrated.map((v) => [v.name, v.type])) ===
      JSON.stringify([
        ['response', 'string'],
        ['run_time', 'decimal'],
        ['prompt_length', 'decimal'],
        ['output_length', 'decimal'],
      ]),
    `integrated manifest outputs mirror the SCR LLM contract (got: ${JSON.stringify(outputVarsIntegrated.map((v) => v.name))})`
  );
  const pyIntegrated = aLog.find((e) => e.method === 'POST' && e.body.includes('def scoreModel(')).body;
  assert(pyIntegrated.includes('import requests'), 'integrated score code imports requests');
  assert(
    pyIntegrated.includes('"Output: response, run_time, prompt_length, output_length"'),
    'integrated score code declares the SCR-style outputs'
  );
  assert(pyIntegrated.includes('requests.post('), 'integrated score code performs the LLM call');
  assert(
    pyIntegrated.includes('llmData = llmJson.get("data", llmJson)'),
    'integrated score code unwraps the SCR data envelope'
  );
  assert(
    pyIntegrated.includes('return response, run_time, prompt_length, output_length'),
    'integrated score code returns the LLM results'
  );
  assert(pyIntegrated.includes('def scoreModel(customer, amount):'), 'integrated call keeps the variable inputs');
  assert(!pyIntegrated.includes('return llmBody, llmURL'), 'integrated call no longer returns llmBody/llmURL');
  assert(pyIntegrated.includes('verify=sslVerify'), 'integrated call verifies TLS via the configurable CA bundle');
  assert(
    pyIntegrated.includes('os.getenv("LLMCONTAINERCABUNDLE", "/security/trustedcerts.pem")'),
    'CA bundle defaults to the one SAS Viya mounts into its pods'
  );
  assert(
    pyIntegrated.includes('timeout=float(os.getenv("LLMCONTAINERTIMEOUT", "600"))'),
    'integrated call applies a configurable timeout'
  );
  assert(
    pyIntegrated.includes('except Exception as error:') && pyIntegrated.includes('response = f"LLM call failed: {error}"'),
    'a failed LLM call is reported through the response output instead of raising'
  );
  assert(pyIntegrated.includes('llmBody = json.dumps('), 'integrated request body is built with json.dumps');
  assert(
    !pyIntegrated.includes(`.replace("'", '"')`),
    'integrated prompts are not run through the legacy escape chain'
  );
  assertCompiles(scoreCodeOf(pyIntegrated), 'the integrated score code');

  // ---- parse the LLM response into output variables --------------------------
  await page.uncheck('#app-obj-LPB-pet-out-run_time');
  await page.uncheck('#app-obj-LPB-pet-out-prompt_length');
  await page.click('text="Add Output Variable"');
  await page.click('text="Add Output Variable"');
  const outRows = page.locator('.pb-outvar-row');
  await outRows.nth(0).locator('.pb-outvar-name').fill('sentiment');
  await outRows.nth(0).locator('.pb-outvar-description').fill('Detected sentiment');
  await outRows.nth(0).locator('.pb-outvar-default').fill('neutral');
  await outRows.nth(1).locator('.pb-outvar-name').fill('score');
  await outRows.nth(1).locator('.pb-outvar-type').selectOption('decimal');
  await outRows.nth(1).locator('.pb-outvar-default').fill('0.5');
  step(true, 'defined string and decimal output variables with defaults');
  // probe: reserved names are rejected
  await page.click('text="Add Output Variable"');
  await outRows.nth(2).locator('.pb-outvar-name').fill('response');
  assert(
    await outRows.nth(2).locator('.pb-outvar-name').evaluate((el) => el.classList.contains('is-invalid')),
    'reserved output name (response) is flagged'
  );
  await outRows.nth(2).locator('.pb-outvar-remove').click();
  await page.screenshot({ path: 'shot-10-output-parsing.png', fullPage: true });

  await resetLog();
  await page.click('#app-obj-LPB-pet-create-model-button');
  await waitUntil(
    async () => {
      const l = await getLog();
      // Wait for the tag PUT too — it lands AFTER the content uploads, and
      // the assertions below read it from this same log snapshot.
      return (
        l.some((e) => e.method === 'POST' && e.body.includes('def scoreModel(')) &&
        l.some((e) => e.method === 'POST' && e.body.includes('filename="outputVar.json"')) &&
        l.some((e) => e.method === 'PUT' && e.url === '/modelRepository/models/model-used')
      );
    },
    'parsing score code + outputVar + tag update uploaded'
  );
  aLog = await getLog();
  const findPart3 = (name) => aLog.find((e) => e.method === 'POST' && e.body.includes(`filename="${name}"`));
  const outputVarsParsing = multipartJson(findPart3('outputVar.json').body);
  assert(
    JSON.stringify(outputVarsParsing.map((v) => [v.name, v.type])) ===
      JSON.stringify([
        ['response', 'string'],
        ['output_length', 'decimal'],
        ['sentiment', 'string'],
        ['score', 'decimal'],
        ['parse_status', 'decimal'],
      ]),
    `deselected defaults dropped; parsed outputs + parse_status added (got: ${JSON.stringify(outputVarsParsing.map((v) => v.name))})`
  );
  assert(
    outputVarsParsing.find((v) => v.name === 'sentiment').length === 10000000 &&
      outputVarsParsing.find((v) => v.name === 'sentiment').description === 'Detected sentiment',
    'parsed string output carries 10M length and its description'
  );
  const pyParsing = aLog.find((e) => e.method === 'POST' && e.body.includes('def scoreModel(')).body;
  assert(pyParsing.includes('import json'), 'parsing score code imports json');
  assert(
    pyParsing.includes('"Output: response, output_length, sentiment, score, parse_status"'),
    'parsing score code declares the selected + parsed outputs'
  );
  assert(pyParsing.includes('sentiment = "neutral"') && pyParsing.includes('score = 0.5'), 'defaults initialized');
  assert(pyParsing.includes('cleaned.startswith("```")'), 'fenced ```json responses are unwrapped');
  assert(pyParsing.includes('float(parsed["score"])'), 'decimal outputs are coerced with float()');
  assert(
    pyParsing.includes('return response, output_length, sentiment, score, parse_status'),
    'parsing score code returns the chosen output tuple'
  );
  assertCompiles(scoreCodeOf(pyParsing), 'the parsing score code');
  // Run it: the fenced JSON an LLM answers with comes back as the output variables.
  const parsingCall = callScoreModel(scoreCodeOf(pyParsing), ['ACME Corp', 42], {
    data: { response: '```json\n{"sentiment": "positive", "score": 0.9}\n```', run_time: 1.5, prompt_length: 42, output_length: 7 },
  });
  assert(parsingCall.ok, `the parsing score code runs (${parsingCall.ok ? 'scoreModel returned' : lastLine(parsingCall.error)})`);
  assert(
    JSON.stringify(parsingCall.returned.slice(1)) === JSON.stringify([7, 'positive', 0.9, 1]),
    `scoreModel returns the parsed outputs in the declared order (got: ${JSON.stringify(parsingCall.returned)})`
  );
  assert(
    parsingCall.request.body.inputs.find((i) => i.name === 'systemPrompt').value === 'Hello ACME Corp with {json} braces',
    'the manifested model sends the system prompt the experiment sent'
  );
  const tagPutParsing = aLog.find((e) => e.method === 'PUT' && e.url === '/modelRepository/models/model-used');
  const parsingTags = JSON.parse(tagPutParsing.body).tags;
  assert(
    parsingTags.includes('demo_llm') && parsingTags.includes('LLM-Call-Included') && parsingTags.includes('Output-Parsing'),
    `integrated + parsing manifest adds the mode tags (got: ${parsingTags})`
  );

  // ---- manifest config persisted with the run and restored by loading it ----
  const run4HeaderParsing = multipartJson(findPart3('Prompt-Experiment-Tracker.json').body).find(
    (r) => r.runId === 4 && r.model === ''
  );
  assert(
    run4HeaderParsing.manifest && run4HeaderParsing.manifest.integratedLLMCall === true,
    'manifest config (integrated flag) persisted on the manifested run'
  );
  assert(
    JSON.stringify(run4HeaderParsing.manifest.selectedOutputs) === '["response","output_length"]',
    `selected default outputs persisted (got: ${JSON.stringify(run4HeaderParsing.manifest.selectedOutputs)})`
  );
  assert(
    run4HeaderParsing.manifest.outputVariables.length === 2 &&
      run4HeaderParsing.manifest.outputVariables[0].name === 'sentiment' &&
      run4HeaderParsing.manifest.outputVariables[0].defaultValue === 'neutral',
    'output variable definitions persisted with the run'
  );
  // scramble the panel, then load run #4 to restore the whole configuration
  await page.uncheck('#app-obj-LPB-pet-manifest-integrated');
  await goStep('build');
  await page.click('#app-obj-LPB-pet-3 .pet-run-load');
  await waitUntil(
    async () => page.isChecked('#app-obj-LPB-pet-manifest-integrated'),
    'integrated flag restored by loading the run'
  );
  await goStep('finalize');
  step(true, 'loading the run restored the integrated-call setting');
  assert(await page.isVisible('#app-obj-LPB-pet-manifest-options'), 'options panel visible again after load');
  assert(!(await page.isChecked('#app-obj-LPB-pet-out-run_time')), 'deselected default output restored by load');
  assert(await page.isChecked('#app-obj-LPB-pet-out-response'), 'selected default output restored by load');
  assert((await page.locator('.pb-outvar-row').count()) === 2, 'output variable rows restored by load');
  assert(
    (await page.locator('.pb-outvar-row').nth(0).locator('.pb-outvar-default').inputValue()) === 'neutral',
    'output variable default value restored by load'
  );

  // ---- switching to a prompt WITHOUT a tracker also resets the panel --------
  assert(await page.isChecked('#app-obj-LPB-pet-manifest-integrated'), 'panel configured before the switch');
  await goStep('setup');
  await page.selectOption('#LPB-prompt-dropdown', 'model-free');
  await waitUntil(
    async () => !(await page.isChecked('#app-obj-LPB-pet-manifest-integrated')),
    'manifest panel reset on switching to a tracker-less prompt'
  );
  step(true, 'output/manifest panel reset when switching to a prompt without runs');
  assert((await page.locator('.pb-outvar-row').count()) === 0, 'output variable rows cleared by the switch');
  assert(await page.isChecked('#app-obj-LPB-pet-out-run_time'), 'default outputs reselected by the switch');
  await goStep('finalize');
  assert(!(await page.isVisible('#app-obj-LPB-pet-manifest-options')), 'options panel hidden again after the switch');
  await goStep('setup');
  await page.selectOption('#LPB-prompt-dropdown', 'model-used');
  await waitUntil(async () => (await page.$$('.pet-run-delete')).length === 3, 'switched back to the tracked prompt');

  // ---- auto-load re-applies the best prompt on re-selection -----------------
  await goStep('build');
  await page.fill('#app-obj-LPB-system-prompt', 'scratch');
  await goStep('setup');
  await page.selectOption('#LPB-prompt-dropdown', 'Select an existing Prompt-Test');
  await page.selectOption('#LPB-prompt-dropdown', 'model-used');
  await waitUntil(
    async () => (await page.inputValue('#app-obj-LPB-system-prompt')) === 'Sys 2',
    'auto-load after re-selecting the prompt'
  );
  step(true, 'best prompt auto-loads again after re-selecting the prompt');
  assert(
    (await page.locator('.pb-variable-row').count()) === 0,
    'variables menu reset to match the auto-loaded run (which has none)'
  );
  assert(
    !(await page.isChecked('#app-obj-LPB-pet-manifest-integrated')),
    'manifest config reset when the loaded run has none'
  );
  assert(
    (await page.locator('.pb-outvar-row').count()) === 0 && (await page.isChecked('#app-obj-LPB-pet-out-run_time')),
    'output variables cleared and default outputs reselected on reset'
  );
  await page.screenshot({ path: 'shot-08-variables-workbench.png', fullPage: true });

  // ---- prompts that are hostile to a Python literal ---------------------------
  // Backslashes that read as escape sequences, JSON braces, three quotes in a
  // row and a quote as the last character: the manifested model has to send
  // exactly what the experiment sent.
  const HOSTILE_SYSTEM = 'Reply as JSON {"ok": true} for {{customer}}; never write """ or C:\\Users\\new\\d+ and end with "';
  const HOSTILE_USER = 'It\'s {{ customer }} asking: "why is \\n no line break?" {{unknown}} }{ \'\'\'"""';
  await goStep('build');
  await page.click('text="Add Variable"');
  await page.locator('.pb-variable-row').nth(0).locator('.pb-var-name').fill('customer');
  await page.locator('.pb-variable-row').nth(0).locator('.pb-var-value').fill('ACME Corp');
  await page.fill('#app-obj-LPB-system-prompt', HOSTILE_SYSTEM);
  await page.fill('#app-obj-LPB-user-prompt', HOSTILE_USER);
  await resetLog();
  await page.click('#app-obj-LPB-run-experiment');
  await waitUntil(async () => (await page.$$('.pet-run-delete')).length === 4, 'hostile run rendered');
  const hostileSent = JSON.parse((await getLog()).find((e) => e.method === 'POST' && e.url === '/scr/demo_llm/demo_llm').body).inputs;
  const hostileSystemSent = hostileSent.find((i) => i.name === 'systemPrompt').value;
  const hostileUserSent = hostileSent.find((i) => i.name === 'userPrompt').value;
  assert(
    hostileSystemSent === HOSTILE_SYSTEM.replace('{{customer}}', 'ACME Corp') && hostileUserSent.endsWith('\'\'\'"""'),
    `the experiment sent the hostile prompts as typed (got: ${hostileSystemSent})`
  );
  await page.click('#app-obj-LPB-pet-3 > .accordion-item > h2 > .accordion-button');
  await page.click('#app-obj-LPB-pet-3-run-nested-demo_llm .accordion-button');
  await page.check('#best-prompt-3-demo_llm');
  await goStep('finalize');
  const manifestHostile = async (label) => {
    await resetLog();
    await page.click('#app-obj-LPB-pet-create-model-button');
    await waitUntil(async () => {
      const l = await getLog();
      // The tag PUT lands after the content uploads.
      return (
        l.some((e) => e.method === 'POST' && e.body.includes('def scoreModel(')) &&
        l.some((e) => e.method === 'PUT' && e.url === '/modelRepository/models/model-used')
      );
    }, `${label} uploaded`);
    const code = scoreCodeOf((await getLog()).find((e) => e.method === 'POST' && e.body.includes('def scoreModel(')).body);
    assertCompiles(code, label);
    return code;
  };
  await page.check('#app-obj-LPB-pet-manifest-integrated');
  const hostileIntegrated = callScoreModel(await manifestHostile('the integrated score code of the hostile prompts'), ['ACME Corp'], {
    data: { response: 'fine', run_time: 1.5, prompt_length: 42, output_length: 7 },
  });
  assert(hostileIntegrated.ok, `the hostile integrated score code runs (${hostileIntegrated.ok ? 'scoreModel returned' : lastLine(hostileIntegrated.error)})`);
  assert(
    JSON.stringify(hostileIntegrated.returned) === JSON.stringify(['fine', 1.5, 42, 7]),
    `the hostile integrated score code returns the LLM results (got: ${JSON.stringify(hostileIntegrated.returned)})`
  );
  const hostileManifested = hostileIntegrated.request.body.inputs;
  assert(
    hostileManifested.find((i) => i.name === 'systemPrompt').value === hostileSystemSent,
    `the manifested system prompt is the one the experiment sent (got: ${hostileManifested.find((i) => i.name === 'systemPrompt').value})`
  );
  assert(
    hostileManifested.find((i) => i.name === 'userPrompt').value === hostileUserSent,
    `the manifested user prompt is the one the experiment sent (got: ${hostileManifested.find((i) => i.name === 'userPrompt').value})`
  );
  await page.uncheck('#app-obj-LPB-pet-manifest-integrated');
  const hostileNode = callScoreModel(await manifestHostile('the Call-LLM-node score code of the hostile prompts'), ['ACME Corp']);
  assert(
    hostileNode.ok && hostileNode.returned.length === 2,
    `the hostile Call-LLM-node score code runs and returns llmBody and llmURL (${hostileNode.ok ? hostileNode.returned.length + ' values' : lastLine(hostileNode.error)})`
  );

  // ============ PHASE B: deletion features (fresh page) ============
  await page.goto(APP_URL);
  await page.waitForSelector('#LPB-project-dropdown', { timeout: 15000 });
  await page.selectOption('#LPB-project-dropdown', 'proj-1');
  await waitUntil(
    async () => (await page.$$('#LPB-prompt-dropdown option')).length === 4,
    'prompt dropdown refilled'
  );
  await page.selectOption('#LPB-prompt-dropdown', 'model-used');
  await waitUntil(async () => (await page.$$('.pet-run-delete')).length === 3, '3 runs re-rendered after reload');

  // Tick "Best Response" in run #1 (expand run accordion, then the model accordion)
  await goStep('build');
  await page.click('#app-obj-LPB-pet-0 > .accordion-item > h2 > .accordion-button');
  await page.click('#app-obj-LPB-pet-0-run-nested-demo_llm .accordion-button');
  await page.check('#best-prompt-0-demo_llm');
  step(true, 'ticked Best Response on run #1');

  // ---- delete the middle run ------------------------------------------------
  await page.click('#app-obj-LPB-pet-1 .pet-run-delete');
  await waitUntil(async () => (await page.$$('.pet-run-delete')).length === 2, 'run deleted');
  headers = await runHeaderTexts();
  assert(
    headers.join('|') === 'Prompt Experiment Run #2|Prompt Experiment Run #1',
    `runs renumbered to 1..2 after deleting the middle run (got: ${headers.join('|')})`
  );
  const run2Sys = await page.textContent('#app-obj-LPB-pet-1-run-systenPrompt');
  assert(run2Sys.includes('Sys 3'), `former run 3 now renders as run #2 (got: ${run2Sys.trim()})`);
  assert(await page.isChecked('#best-prompt-0-demo_llm'), 'Best Response tick on run #1 survived the re-render');
  assert(
    (await page.$$('#app-obj-LPB-pet-0-run-nested-demo_llm-header svg.bestPrompt')).length === 1,
    'best-prompt star icon still shown on run #1 after re-render'
  );
  await page.screenshot({ path: 'shot-02-after-run-delete.png', fullPage: true });

  // ---- save: persisted rows must be renumbered and carry the tick ----------
  await resetLog();
  await page.click('#app-obj-LPB-pet-save-button');
  await waitUntil(
    async () => (await getLog()).some((e) => e.method === 'POST' && /\/models\/model-used\/contents/.test(e.url)),
    'tracker saved'
  );
  let log = await getLog();
  const saveSeq = log.map((e) => `${e.method} ${e.url.split('?')[0]}`);
  assert(
    saveSeq.includes('POST /modelRepository/models/model-used/modelVersions') &&
      saveSeq.includes('DELETE /modelRepository/models/model-used/contents/c-trk'),
    'save created a model version and deleted the old tracker first'
  );
  const savedRows = multipartJson(log.find((e) => e.method === 'POST' && /contents\?/.test(e.url)).body);
  assert(
    JSON.stringify(savedRows.map((r) => r.runId)) === '[1,1,2,2,2]',
    `saved rows renumbered contiguously despite the runId gap (got runIds: ${savedRows.map((r) => r.runId)})`
  );
  assert(savedRows[1].best_prompt === true || savedRows[1].best_prompt === 1, 'saved run #1 row carries best_prompt');
  assert(savedRows[2].systemPrompt === 'Sys 3', 'saved run #2 header row holds the former run-3 prompt');
  // A plain Save shows the Model Manager link next to the Save button, not in
  // the manifest box.
  const saveOnlyLink = await page.$('#app-obj-LPB-pet-save-only-result a');
  assert(saveOnlyLink, 'plain Save shows the link next to the Save button');
  assert(
    !(await page.$('#app-obj-LPB-pet-save-result a')),
    'plain Save does not put the link in the manifest box'
  );

  // ---- delete ALL runs, then save an empty tracker --------------------------
  await page.click('#app-obj-LPB-pet-0 .pet-run-delete'); // DOM order: pet-1 first, but ids are stable per index
  await waitUntil(async () => (await page.$$('.pet-run-delete')).length === 1, 'second run deleted');
  await page.click('#app-obj-LPB-pet-0 .pet-run-delete');
  await waitUntil(async () => (await page.$$('.pet-run-delete')).length === 0, 'all runs deleted');
  assert(await page.isVisible('#app-obj-LPB-pet-empty'), 'empty-state hint returns when all runs are deleted');
  assert(
    await page.isDisabled('#app-obj-LPB-pet-create-model-button'),
    'Manifest disabled again once the best run is gone'
  );
  const dialogsBefore = dialogs.length;
  await resetLog();
  await page.click('#app-obj-LPB-pet-save-button');
  await waitUntil(
    async () => (await getLog()).some((e) => e.method === 'POST' && /contents\?/.test(e.url)),
    'empty tracker saved'
  );
  log = await getLog();
  const emptyRows = multipartJson(log.find((e) => e.method === 'POST' && /contents\?/.test(e.url)).body);
  assert(Array.isArray(emptyRows) && emptyRows.length === 0, 'an emptied tracker persists as []');
  assert(dialogs.length === dialogsBefore, 'no "run at least one experiment" alert blocked the empty save');

  // ---- the first request to a service of this session: HTTP 449 --------------
  // A SAS Viya service the browser has no session with answers a request of
  // the app's HTTP client with 449. The client establishes the session with a
  // plain GET to the service and sends the request again, so the delete dialog
  // names the decisions all the same.
  const noSession = (service, how = {}) =>
    fetch(BASE + '/__nosession', { method: 'POST', body: JSON.stringify({ service, ...how }) });
  // The requests to the relationships service, as "METHOD path status"; the
  // one that was not sent by the app's HTTP client is marked plain.
  const usageRequests = async () =>
    (await getLog())
      .filter((e) => e.url.startsWith('/relationships/'))
      .map((e) => `${e.method} ${e.url.split('?')[0]}${e.xhr ? '' : ' (plain)'} ${e.status}`);
  const cancelDeletePrompt = async () => {
    await page.click('.modal.show .btn-secondary');
    await page.waitForSelector('.modal.show', { state: 'detached' });
    await waitUntil(async () => !(await page.isDisabled('#LPB-delete-prompt-button')), 'Delete Prompt enabled again');
  };
  await goStep('setup');
  await resetLog();
  await noSession('relationships');
  await page.click('#LPB-delete-prompt-button');
  await page.waitForSelector('.modal.show .modal-body');
  let modalBody = await page.textContent('.modal.show .modal-body');
  assert(
    modalBody.includes('2 decision(s) currently use this prompt') && modalBody.includes('Loan Approval Decision') && modalBody.includes('Fraud Check Decision'),
    `a first request answered with 449 still ends in a dialog that names the decisions (got: ${modalBody.trim()})`
  );
  let usageLog = await usageRequests();
  assert(
    JSON.stringify(usageLog) ===
      JSON.stringify(['POST /relationships/relationships 449', 'GET /relationships/ (plain) 200', 'POST /relationships/relationships 200']),
    `the 449 is followed by one plain GET to the service and the same request again (got: ${usageLog.join(' | ')})`
  );
  const usageBodies = (await getLog()).filter((e) => e.method === 'POST' && e.url.startsWith('/relationships/')).map((e) => e.body);
  assert(
    usageBodies.length === 2 && usageBodies[0] === usageBodies[1] && usageBodies[0].includes('/modelRepository/models/model-used'),
    'the request is repeated with the body it had'
  );
  await cancelDeletePrompt();
  assert(!(await getLog()).some((e) => e.method === 'DELETE'), 'cancelling that dialog deleted nothing');

  // The repeated request is the first one the service answers, and a first
  // write is answered with the CSRF 403: both recoveries happen in a row.
  await resetLog();
  await noSession('relationships', { csrf: true });
  await page.click('#LPB-delete-prompt-button');
  await page.waitForSelector('.modal.show .modal-body');
  modalBody = await page.textContent('.modal.show .modal-body');
  usageLog = await usageRequests();
  assert(
    JSON.stringify(usageLog) ===
      JSON.stringify([
        'POST /relationships/relationships 449',
        'GET /relationships/ (plain) 200',
        'POST /relationships/relationships 403',
        'POST /relationships/relationships 200',
      ]),
    `a 449 and then a CSRF 403 are each answered with one more attempt (got: ${usageLog.join(' | ')})`
  );
  const csrfSent = (await getLog()).filter((e) => e.method === 'POST' && e.url.startsWith('/relationships/')).map((e) => e.csrf);
  assert(
    csrfSent[2] === 'mock-csrf-token' && !csrfSent.slice(0, 2).includes('mock-csrf-token'),
    `the last attempt carries the token the 403 handed out (got: ${csrfSent.join(', ')})`
  );
  assert(modalBody.includes('2 decision(s) currently use this prompt'), 'and the dialog names the decisions');
  await cancelDeletePrompt();

  // A session that cannot be established: one warm-up, one more attempt, and
  // the dialog says that the usage is unknown - as it did before.
  await resetLog();
  await noSession('relationships', { stuck: true });
  await page.click('#LPB-delete-prompt-button');
  await page.waitForSelector('.modal.show .modal-body');
  modalBody = await page.textContent('.modal.show .modal-body');
  assert(
    modalBody.includes('could not be verified') && !modalBody.includes('Loan Approval Decision'),
    `a service that keeps answering 449 leaves the dialog with the could-not-be-verified note (got: ${modalBody.trim()})`
  );
  const stuckLog = ['POST /relationships/relationships 449', 'GET /relationships/ (plain) 200', 'POST /relationships/relationships 449'];
  usageLog = await usageRequests();
  assert(JSON.stringify(usageLog) === JSON.stringify(stuckLog), `the request is sent twice and the session asked for once (got: ${usageLog.join(' | ')})`);
  await sleep(600);
  usageLog = await usageRequests();
  assert(JSON.stringify(usageLog) === JSON.stringify(stuckLog), `and nothing more is sent while the dialog is open (got: ${usageLog.join(' | ')})`);
  await cancelDeletePrompt();
  await noSession();

  // ---- prompt deletion: usage found, cancel then confirm ---------------------
  await resetLog();
  await page.click('#LPB-delete-prompt-button');
  await page.waitForSelector('.modal.show .modal-body');
  modalBody = await page.textContent('.modal.show .modal-body');
  assert(modalBody.includes('2 decision(s) currently use this prompt'), `usage count shown (got: ${modalBody.trim()})`);
  const linkA = await page.$('.modal.show a[href$="/SASDecisionManager/decisions/flow-A"]');
  const linkB = await page.$('.modal.show a[href$="/SASDecisionManager/decisions/flow-B"]');
  assert(linkA && linkB, 'deep links for both (deduped) decisions present');
  assert((await linkA.textContent()) === 'Loan Approval Decision', 'decision link shows the decision name');
  await page.screenshot({ path: 'shot-03-delete-prompt-usage.png', fullPage: true });
  await page.click('.modal.show .btn-secondary');
  await page.waitForSelector('.modal.show', { state: 'detached' });
  log = await getLog();
  assert(!log.some((e) => e.method === 'DELETE'), 'cancel issued no DELETE');
  assert((await page.$$('#LPB-prompt-dropdown option[value="model-used"]')).length === 1, 'prompt still listed after cancel');
  await waitUntil(
    async () => !(await page.isDisabled('#LPB-delete-prompt-button')),
    'Delete Prompt re-enabled after cancel'
  );
  step(true, 'Delete Prompt re-enabled after cancel');

  await page.click('#LPB-delete-prompt-button');
  await page.waitForSelector('.modal.show .btn-danger');
  await page.click('.modal.show .btn-danger');
  await page.waitForSelector('.modal.show', { state: 'detached' });
  await waitUntil(
    async () => (await getLog()).some((e) => e.method === 'DELETE' && e.url === '/modelRepository/models/model-used'),
    'model deleted'
  );
  assert((await page.$$('#LPB-prompt-dropdown option[value="model-used"]')).length === 0, 'deleted prompt removed from dropdown');
  assert(await page.isDisabled('#LPB-delete-prompt-button'), 'Delete Prompt disabled again (selection reset)');
  assert(
    (await page.getAttribute('#LPB-openInMMButton', 'class')).includes('disabled'),
    'Open in SAS Model Manager deactivated after prompt deletion'
  );

  // ---- prompt deletion: no usage / usage check failed ------------------------
  await page.selectOption('#LPB-prompt-dropdown', 'model-free');
  await sleep(300);
  await page.click('#LPB-delete-prompt-button');
  await page.waitForSelector('.modal.show .modal-body');
  modalBody = await page.textContent('.modal.show .modal-body');
  assert(modalBody.includes('No decisions were found using this prompt.'), 'no-usage note shown');
  const noOverflow = await page.evaluate(() => {
    const content = document.querySelector('.modal.show .modal-content');
    const title = document.querySelector('.modal.show .modal-title');
    return content.scrollWidth <= content.clientWidth + 1 && title.scrollWidth <= title.clientWidth + 1;
  });
  assert(noOverflow, 'long prompt name (score_metric_answer_relevancy) wraps inside the modal');
  await page.screenshot({ path: 'shot-04-delete-prompt-nousage.png', fullPage: true });
  await page.click('.modal.show .btn-secondary');
  await page.waitForSelector('.modal.show', { state: 'detached' });

  await page.selectOption('#LPB-prompt-dropdown', 'model-err');
  await sleep(300);
  await resetLog();
  await page.click('#LPB-delete-prompt-button');
  await page.waitForSelector('.modal.show .modal-body');
  modalBody = await page.textContent('.modal.show .modal-body');
  assert(modalBody.includes('could not be verified'), 'fail-open warning shown when the relationships call fails');
  await waitUntil(
    async () => page.evaluate(() => document.activeElement?.classList?.contains('modal')),
    'modal focus trap active (fade-in finished)'
  );
  await page.keyboard.press('Escape'); // probe: Esc counts as cancel
  await page.waitForSelector('.modal.show', { state: 'detached' });
  await sleep(200);
  log = await getLog();
  assert(!log.some((e) => e.method === 'DELETE'), 'Esc dismissal issued no DELETE');

  // ---- project deletion: cancel mid-sequence deletes nothing -----------------
  await resetLog();
  await page.click('#LPB-delete-project-button');
  await page.waitForSelector('.modal.show .modal-title');
  let title = await page.textContent('.modal.show .modal-title');
  assert(title.includes('Used Prompt (1/3)'), `sequential confirm shows progress counter (got: ${title.trim()})`);
  // Every confirmation of a project deletion carries the usage note of its
  // prompt: the decisions with their links, that there are none, or that the
  // check failed.
  const projectUsageNote = async (n) => {
    const body = (await page.textContent('.modal.show .modal-body')).trim();
    const links = await page.$$eval('.modal.show .modal-body a', (els) => els.map((a) => `${a.textContent} -> ${a.getAttribute('href')}`));
    if (n === '(1/3)') {
      assert(
        body.includes('2 decision(s) currently use this prompt') &&
          links.join(', ') ===
            `Loan Approval Decision -> ${BASE}/SASDecisionManager/decisions/flow-A, Fraud Check Decision -> ${BASE}/SASDecisionManager/decisions/flow-B`,
        `project deletion ${n}: the used prompt lists its two decisions with their links (got: ${body} / ${links.join(', ')})`
      );
    } else if (n === '(2/3)') {
      assert(
        body.includes('No decisions were found using this prompt.') && links.length === 0,
        `project deletion ${n}: the prompt no decision uses says so (got: ${body})`
      );
    } else {
      assert(
        body.includes('could not be verified') && links.length === 0,
        `project deletion ${n}: the prompt whose check failed says that its usage is unknown (got: ${body})`
      );
    }
  };
  await projectUsageNote('(1/3)');
  await page.screenshot({ path: 'shot-05-delete-project-step1.png', fullPage: true });
  await page.click('.modal.show .btn-danger'); // confirm prompt 1
  await waitUntil(async () => {
    const t = await page.textContent('.modal.show .modal-title').catch(() => '');
    return t && t.includes('(2/3)');
  }, 'second confirmation modal');
  await page.click('.modal.show .btn-secondary'); // cancel at prompt 2
  await page.waitForSelector('.modal.show', { state: 'detached' });
  await sleep(200);
  log = await getLog();
  assert(!log.some((e) => e.method === 'DELETE'), 'cancelling mid-sequence deleted nothing');
  await waitUntil(
    async () => !(await page.isDisabled('#LPB-delete-project-button')),
    'Delete Project re-enabled after abort'
  );
  step(true, 'Delete Project re-enabled after abort');

  // ---- project deletion: confirm all -> models first, then the project -------
  await resetLog();
  await page.click('#LPB-delete-project-button');
  for (const n of ['(1/3)', '(2/3)', '(3/3)']) {
    await waitUntil(async () => {
      const t = await page.textContent('.modal.show .modal-title').catch(() => '');
      return t && t.includes(n);
    }, `confirmation modal ${n}`);
    await projectUsageNote(n);
    await page.click('.modal.show .btn-danger');
  }
  await waitUntil(
    async () => (await getLog()).some((e) => e.method === 'DELETE' && e.url === '/modelRepository/projects/proj-1'),
    'project deleted'
  );
  log = await getLog();
  const deletes = log.filter((e) => e.method === 'DELETE').map((e) => e.url);
  assert(
    JSON.stringify(deletes) ===
      JSON.stringify([
        '/modelRepository/models/model-used',
        '/modelRepository/models/model-free',
        '/modelRepository/models/model-err',
        '/modelRepository/projects/proj-1',
      ]),
    `models deleted first, project last (got: ${deletes.join(', ')})`
  );
  assert((await page.$$('#LPB-project-dropdown option')).length === 1, 'project removed from dropdown');
  assert((await page.$$('#LPB-prompt-dropdown option')).length === 1, 'prompt dropdown reset to placeholder');
  assert(await page.isDisabled('#LPB-delete-project-button'), 'Delete Project disabled after deletion');
  assert(await page.isDisabled('#LPB-delete-prompt-button'), 'Delete Prompt disabled after deletion');
  await page.screenshot({ path: 'shot-06-after-project-delete.png', fullPage: true });

  // ============ PHASE C: an LLM run in which models fail (fresh page) ============
  // The mock keeps its fixtures whatever the page deleted, so the project is
  // back. The all-keys domain makes all three LLMs selectable.
  const failModel = (name, status) =>
    fetch(BASE + '/__failmodel', { method: 'POST', body: JSON.stringify({ name, status }) });
  const runErrorLines = async () =>
    (await page.innerText('#app-obj-LPB-run-error')).split('\n').map((line) => line.trim()).filter(Boolean);
  const runModels = (runIndex) =>
    page.$$eval(`[id^="app-obj-LPB-pet-${runIndex}-run-nested-"][id$="-header"]`, (els) =>
      els.map((el) => ({
        model: el.id.replace(/^app-obj-LPB-pet-\d+-run-nested-/, '').replace(/-header$/, ''),
        flags: Array.from(el.querySelectorAll('svg title')).map((title) => title.textContent),
      }))
    );
  const FASTEST = 'fastest response time';
  const FEWEST = 'fewest output tokens';
  const NO_CONTAINER = 'the model is registered, but no container answers at this endpoint';
  await page.goto(`${APP_URL}&credentialDomain=all-keys`);
  await page.waitForSelector('#LPB-project-dropdown', { timeout: 15000 });
  await page.selectOption('#LPB-project-dropdown', 'proj-1');
  await waitUntil(async () => (await page.$$('#LPB-prompt-dropdown option')).length === 4, 'prompts listed for the failing-model runs');
  await page.selectOption('#LPB-prompt-dropdown', 'model-free');
  await goStep('build');
  for (const index of [0, 1, 2]) await page.check(`#model${index}`);
  await page.fill('#app-obj-LPB-system-prompt', 'Answer briefly.');
  await page.fill('#app-obj-LPB-user-prompt', 'Is this a test?');

  // -- the model in the middle of three fails: the other two keep their results
  await failModel('second_llm', 503);
  await resetLog();
  await page.click('#app-obj-LPB-run-experiment');
  await waitUntil(async () => (await page.$$('.pet-run-delete')).length === 1, 'run with a failing model rendered');
  log = await getLog();
  assert(
    ['demo_llm', 'second_llm', 'contrarian_judge'].every((name) => log.some((e) => e.method === 'POST' && e.url === `/scr/${name}/${name}`)),
    'all three selected LLMs were called'
  );
  let runResults = await runModels(0);
  assert(
    runResults.map((r) => r.model).join(',') === 'demo_llm,contrarian_judge',
    `the run keeps the two models that answered, the one after the failure included (got: ${runResults.map((r) => r.model).join(',')})`
  );
  let errorLines = await runErrorLines();
  assert(
    errorLines.length === 1 && errorLines[0] === `second_llm: LLM call failed with status 503 - ${NO_CONTAINER} (it has to be published and running)`,
    `the error line names the failed model with a hint (got: ${errorLines.join(' | ')})`
  );
  assert(
    runResults.filter((r) => r.flags.some((f) => f.includes(FASTEST))).length === 1 &&
      runResults.filter((r) => r.flags.some((f) => f.includes(FEWEST))).length === 1,
    `fastest and fewest-tokens sit on a model that answered (got: ${JSON.stringify(runResults)})`
  );
  await waitUntil(
    async () => (await page.$$eval('.toast-body', (els) => els.map((e) => e.textContent || ''))).some((t) => t.includes('1 of 3 models did not answer')),
    'toast for the partial failure'
  );
  step(true, 'a toast reports that one of three models did not answer');
  assert(!(await page.isDisabled('#app-obj-LPB-run-experiment')), 'Run Experiments is enabled again after a partial failure');

  // -- the FIRST model fails: the flags must still land on models that answered
  await failModel('second_llm', null);
  await failModel('demo_llm', 404);
  await page.click('#app-obj-LPB-run-experiment');
  await waitUntil(async () => (await page.$$('.pet-run-delete')).length === 2, 'run with the first model failing rendered');
  runResults = await runModels(1);
  assert(
    runResults.map((r) => r.model).join(',') === 'second_llm,contrarian_judge',
    `the run keeps the models after the failed first one (got: ${runResults.map((r) => r.model).join(',')})`
  );
  assert(
    runResults.filter((r) => r.flags.some((f) => f.includes(FASTEST))).length === 1 &&
      runResults.filter((r) => r.flags.some((f) => f.includes(FEWEST))).length === 1,
    `with the first model failing the flags still sit on a model that answered (got: ${JSON.stringify(runResults)})`
  );
  errorLines = await runErrorLines();
  assert(
    errorLines.length === 1 && errorLines[0].startsWith('demo_llm: LLM call failed with status 404') && errorLines[0].includes(NO_CONTAINER),
    `a 404 gets the same hint, and the earlier error is gone (got: ${errorLines.join(' | ')})`
  );

  // -- every model fails: no run is added, every failure is listed
  await failModel('second_llm', 429);
  await failModel('contrarian_judge', 'drop');
  await page.click('#app-obj-LPB-run-experiment');
  await waitUntil(async () => (await runErrorLines()).length === 3, 'three error lines');
  await waitUntil(async () => !(await page.isDisabled('#app-obj-LPB-run-experiment')), 'Run Experiments enabled after the failed run');
  errorLines = await runErrorLines();
  assert(
    errorLines[0].startsWith('demo_llm:') && errorLines[1].startsWith('second_llm:') && errorLines[2].startsWith('contrarian_judge:'),
    `one error line per failed model, in the order of the selection (got: ${errorLines.join(' | ')})`
  );
  assert(errorLines[1].includes('rate-limited'), `the provider hints are kept (got: ${errorLines[1]})`);
  assert(
    errorLines[2].includes('the endpoint is unreachable'),
    `a call without any response is reported as an unreachable endpoint (got: ${errorLines[2]})`
  );
  assert((await page.$$('.pet-run-delete')).length === 2, 'a run in which no model answered adds no run to the tracker');
  await failModel();
  // Saving shows what the tracker holds: two runs with two results each, no empty third.
  await resetLog();
  await page.click('#app-obj-LPB-pet-save-button');
  await waitUntil(
    async () => (await getLog()).some((e) => e.method === 'POST' && /\/models\/model-free\/contents\?/.test(e.url)),
    'tracker with the partial runs saved'
  );
  const partialRows = multipartJson(
    (await getLog()).find((e) => e.method === 'POST' && /\/models\/model-free\/contents\?/.test(e.url)).body
  );
  assert(
    JSON.stringify(partialRows.map((r) => `${r.runId}:${r.model}`)) ===
      JSON.stringify(['1:', '1:demo_llm', '1:contrarian_judge', '2:', '2:second_llm', '2:contrarian_judge']),
    `the saved tracker holds two results per run and no empty run (got: ${partialRows.map((r) => `${r.runId}:${r.model}`).join(', ')})`
  );
  assert(
    partialRows.filter((r) => r.runId === 2 && r.fastest_prompt).length === 1 && partialRows.filter((r) => r.runId === 2 && r.fewest_tokens_prompt).length === 1,
    'the saved flags of the run with the failing first model sit on a model that answered'
  );

  // ============ PHASE D: creating a prompt-test (fresh page) ============
  await page.goto(APP_URL);
  await page.waitForSelector('#LPB-project-dropdown', { timeout: 15000 });
  await page.selectOption('#LPB-project-dropdown', 'proj-1');
  await waitUntil(async () => (await page.$$('#LPB-prompt-dropdown option')).length === 4, 'prompts listed before the create');
  await resetLog();
  await page.click('#LPB-modal-button-container button[data-bs-target="#promptBuilderCreatePromptModal"]');
  await page.waitForSelector('#promptBuilderCreatePromptModal.show');
  await page.fill('#promptBuilderCreatePromptName', 'Created Prompt');
  await page.fill('#promptBuilderCreatePromptDescription', 'made by the verify suite');
  await page.click('#promptBuilderCreatePromptModal .btn-primary');
  await waitUntil(
    async () => (await getLog()).some((e) => e.method === 'POST' && e.url === '/modelRepository/models'),
    'prompt-test created'
  );
  const createBody = JSON.parse((await getLog()).find((e) => e.method === 'POST' && e.url === '/modelRepository/models').body);
  assert(createBody.modeler === 'tester', `a new prompt-test records the signed-in user as its modeler (got: ${createBody.modeler})`);
  assert(!('modelere' in createBody), 'the create request carries no misspelt modeler key');
  assert(
    createBody.name === 'Created Prompt' && createBody.projectId === 'proj-1' && createBody.algorithm === 'Prompt-Template',
    `the create request names the prompt, its project and its kind (got: ${createBody.name}, ${createBody.projectId}, ${createBody.algorithm})`
  );
  await page.waitForSelector('.modal.show', { state: 'detached' });
  await waitUntil(
    async () => (await page.inputValue('#LPB-prompt-dropdown')) === 'model-new-1',
    'the new prompt-test is selected'
  );
  step(true, 'the created prompt-test is listed and selected');
  // After a reload the listing of the project is what brings it back.
  await page.goto(APP_URL);
  await page.waitForSelector('#LPB-project-dropdown', { timeout: 15000 });
  await page.selectOption('#LPB-project-dropdown', 'proj-1');
  await waitUntil(async () => (await page.$$('#LPB-prompt-dropdown option')).length === 5, 'created prompt listed after a reload');
  const promptsAfterReload = await page.$$eval('#LPB-prompt-dropdown option', (els) => els.map((e) => e.textContent));
  assert(promptsAfterReload.includes('Created Prompt'), `the created prompt-test is listed after a reload (got: ${promptsAfterReload})`);

  assert(pageErrors.length === 0, `no uncaught page errors during the whole session (got: ${pageErrors.join(' | ')})`);

  await browser.close();
  console.log('\n===== SUMMARY =====');
  console.log(results.join('\n'));
  console.log(`dialogs seen: ${dialogs.length ? dialogs.join(' | ') : 'none'}`);
})().catch((e) => {
  console.error('VERIFY FAILED:', e.message);
  process.exit(1);
});
