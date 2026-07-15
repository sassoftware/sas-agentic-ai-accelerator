/**
 * Drives the built Prompt Builder (served by mock-server.js) through the new
 * deletion flows with Playwright + system Edge and asserts both the UI state
 * and the HTTP requests the app issued against the mock.
 */
const { chromium } = require('playwright');

const BASE = 'http://localhost:4173';
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

(async () => {
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

  const APP_URL = `${BASE}/?modelRepositoryID=repo-1&llmProjectID=llm-proj&SCREndpoint=${BASE}/scr`;
  await page.goto(APP_URL);
  await page.waitForSelector('#LPB-project-dropdown', { timeout: 15000 });
  step(true, 'app booted against the mock (project dropdown rendered)');

  // ---- UX: heading hierarchy, sections, gated run button, modal copy --------
  assert((await page.$$('h1')).length === 1, 'exactly one h1 on the page');
  assert((await page.$$('.pb-section')).length === 5, 'page grouped into five visual sections');
  assert(await page.isDisabled('#app-obj-LPB-run-experiment'), 'Run Experiments disabled until an LLM is selected');
  assert(await page.isVisible('#app-obj-LPB-pet-empty'), 'tracker shows an empty-state hint');
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
  const mmGap = await page.evaluate(
    () => getComputedStyle(document.getElementById('LPB-openInMMButton')).marginRight
  );
  assert(mmGap === '16px', `gap between Open-in-MM link and Delete Prompt button (margin-right: ${mmGap})`);
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
  const outputVarsDefault = multipartJson(findPart('outputVar.json').body);
  assert(
    JSON.stringify(outputVarsDefault.map((v) => v.name)) === JSON.stringify(['llmBody', 'llmURL']),
    `default manifest outputs llmBody/llmURL (got: ${outputVarsDefault.map((v) => v.name)})`
  );

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
  aLog = await getLog();
  const findPart2 = (name) => aLog.find((e) => e.method === 'POST' && e.body.includes(`filename="${name}"`));
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
  // probe: syntax-check the generated python
  require('fs').writeFileSync('manifested-integrated.py', pyIntegrated.match(/import os[\s\S]*?(?=\r\n--)/)[0]);

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
    async () => (await getLog()).some((e) => e.method === 'POST' && e.body.includes('def scoreModel(')),
    'parsing score code uploaded'
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
  require('fs').writeFileSync('manifested-parsing.py', pyParsing.match(/import os[\s\S]*?(?=\r\n--)/)[0]);

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
  await page.click('#app-obj-LPB-pet-3 .pet-run-load');
  await waitUntil(
    async () => page.isChecked('#app-obj-LPB-pet-manifest-integrated'),
    'integrated flag restored by loading the run'
  );
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
  await page.selectOption('#LPB-prompt-dropdown', 'model-free');
  await waitUntil(
    async () => !(await page.isChecked('#app-obj-LPB-pet-manifest-integrated')),
    'manifest panel reset on switching to a tracker-less prompt'
  );
  step(true, 'output/manifest panel reset when switching to a prompt without runs');
  assert((await page.locator('.pb-outvar-row').count()) === 0, 'output variable rows cleared by the switch');
  assert(await page.isChecked('#app-obj-LPB-pet-out-run_time'), 'default outputs reselected by the switch');
  assert(!(await page.isVisible('#app-obj-LPB-pet-manifest-options')), 'options panel hidden again after the switch');
  await page.selectOption('#LPB-prompt-dropdown', 'model-used');
  await waitUntil(async () => (await page.$$('.pet-run-delete')).length === 3, 'switched back to the tracked prompt');

  // ---- auto-load re-applies the best prompt on re-selection -----------------
  await page.fill('#app-obj-LPB-system-prompt', 'scratch');
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

  // ---- prompt deletion: usage found, cancel then confirm ---------------------
  await resetLog();
  await page.click('#LPB-delete-prompt-button');
  await page.waitForSelector('.modal.show .modal-body');
  let modalBody = await page.textContent('.modal.show .modal-body');
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

  assert(pageErrors.length === 0, `no uncaught page errors during the whole session (got: ${pageErrors.join(' | ')})`);

  await browser.close();
  console.log('\n===== SUMMARY =====');
  console.log(results.join('\n'));
  console.log(`dialogs seen: ${dialogs.length ? dialogs.join(' | ') : 'none'}`);
})().catch((e) => {
  console.error('VERIFY FAILED:', e.message);
  process.exit(1);
});
