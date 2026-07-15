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

  // ---- load a prompt with 3 saved runs -------------------------------------
  await page.selectOption('#LPB-project-dropdown', 'proj-1');
  await waitUntil(
    async () => (await page.$$('#LPB-prompt-dropdown option')).length === 4,
    'prompt dropdown filled'
  );
  assert(!(await page.isDisabled('#LPB-delete-project-button')), 'Delete Project enabled after project selection');
  assert(await page.isDisabled('#LPB-delete-prompt-button'), 'Delete Prompt still disabled (no prompt selected)');

  await page.selectOption('#LPB-prompt-dropdown', 'model-used');
  await waitUntil(async () => (await page.$$('.pet-run-delete')).length === 3, '3 runs rendered');
  step(true, 'tracker with GAPPED runIds (1,2,5) and a two-model run loaded without errors');
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
      JSON.stringify([['customer', 'string', 128000, 'nominal'], ['amount', 'decimal', 8, 'interval']]),
    `manifest inputs derived from the referenced variables (got: ${JSON.stringify(inputVars)})`
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

  // ---- Load Best Prompt restores the workbench ------------------------------
  await page.fill('#app-obj-LPB-system-prompt', 'scratch');
  await page.fill('#app-obj-LPB-user-prompt', 'scratch');
  await page.click('#app-obj-LPB-pet-load-best-button');
  await waitUntil(
    async () => (await page.inputValue('#app-obj-LPB-system-prompt')) === 'Hello {{customer}} with {json} braces',
    'load best restored the system prompt'
  );
  step(true, 'Load Best Prompt restored the most recent best run');
  assert((await page.locator('.pb-variable-row').count()) === 2, 'variables menu reset from the loaded run');
  assert(
    (await page.locator('.pb-variable-row').nth(0).locator('.pb-var-value').inputValue()) === 'ACME Corp',
    'variable value restored from the loaded run'
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
