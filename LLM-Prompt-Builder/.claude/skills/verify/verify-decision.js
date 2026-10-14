/**
 * Drives the built Prompt Builder (served by mock-server.js) through the
 * decision mode: a decision template is opened, its saved run and designer
 * restored, a run made against a decision model, a question added, the runs
 * saved, the evaluation run over the saved runs and over a CAS table, the
 * template manifested with typed outputs, the page switched back to an LLM
 * prompt, and a decision template and its project deleted with the note on
 * the decisions that still use it. Run against a FRESH mock instance.
 *
 * The mock is found at VERIFY_BASE (default http://localhost:4173). Every
 * score code a manifest uploads is compiled and run with a real Python
 * interpreter (see python-check.cjs, which has to sit next to this file).
 */
const crypto = require('crypto');
const { chromium } = require('playwright');
const fetch = require('./mock-fetch.cjs');
const { resolvePython, compilePython, callScoreModel, withoutTlsDefault, expectedTlsVerify, tlsVerifyOf } = require('./python-check.cjs');

const BASE = (process.env.VERIFY_BASE || 'http://localhost:4173').replace(/\/+$/, '');
const results = [];

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
// A wait that answers instead of throwing: true as soon as `fn` holds, false
// when it did not within `timeout`.
async function settles(fn, timeout = 8000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await fn()) return true;
    await sleep(100);
  }
  return false;
}
function multipartJson(body) {
  const m = body.match(/\r\n\r\n([\s\S]*?)\r\n--/);
  return m ? JSON.parse(m[1]) : null;
}
const findPart = (log, name) => log.find((e) => e.method === 'POST' && e.body.includes(`filename="${name}"`));
const findPartMatching = (log, pattern) => log.find((e) => e.method === 'POST' && pattern.test(e.body));
function multipartText(body) {
  const m = body.match(/\r\n\r\n([\s\S]*?)\r\n--/);
  return m ? m[1] : '';
}
// The last line of an interpreter message is the one that names the error.
const lastLine = (message) => String(message || '').trim().split('\n').pop();
// A score code names the SCR endpoint of the page, and so the port of the
// mock; with that replaced its hash is the same on every port.
const sha256 = (text) => crypto.createHash('sha256').update(text, 'utf8').digest('hex');
const portless = (code) => code.split(BASE).join('http://mock');
// The score code of the decision template this suite manifests (questions
// team, escalate and tone, jev_mock), as the bundle of commit ccae261 wrote
// it - the last one before the option "Verify TLS certificates in manifested
// models". Recorded by running this suite against that bundle.
const DECISION_CODE_BEFORE_THE_TLS_OPTION = '9f57ad8f5b98bac8590df1794b61d32b843842f3c656dc65ed617ed3a18e0f08';

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
  page.on('dialog', (d) => d.accept());

  const goStep = async (key) => {
    await page.click(`#app-obj-LPB-step-${key}`);
    await page.waitForSelector(`#app-obj-LPB-pane-${key}:not([hidden])`);
  };
  const visibleSteps = () =>
    page.$$eval('#app-obj-LPB-stepper .pb-step', (els) =>
      els.filter((e) => !e.hidden).map((e) => e.querySelector('.pb-step-button').id.replace('app-obj-LPB-step-', '')).join(',')
    );
  const stepNumbers = () =>
    page.$$eval('#app-obj-LPB-stepper .pb-step', (els) => els.filter((e) => !e.hidden).map((e) => e.querySelector('.pb-step-marker').textContent).join(','));

  // Ask the mock for its decision fixtures (a second project holding the decision template).
  await fetch(BASE + '/__decision', { method: 'POST' });
  const APP_URL = `${BASE}/?modelRepositoryID=repo-1&llmProjectID=llm-proj&SCREndpoint=${BASE}/scr`;
  await page.goto(APP_URL);
  await page.waitForSelector('#LPB-project-dropdown', { timeout: 15000 });
  step(true, 'app booted against the mock');

  // ---- an LLM page: the Evaluate step is there but hidden ---------------------
  assert((await visibleSteps()) === 'setup,build,finalize', `LLM mode shows three steps (got ${await visibleSteps()})`);
  assert(await page.$('#app-obj-LPB-step-evaluate'), 'the Evaluate step exists in the row');
  assert((await page.$$('#LPB-kind-options .pb-kind-option')).length === 2, 'the Setup card offers the two kinds of template');
  assert(await page.isChecked('#LPB-kind-llm'), 'the LLM kind is the default');
  assert(!(await page.$('#promptBuilderCreatePromptKind')), 'the new-prompt dialog carries no kind field');

  // ---- open the decision template ---------------------------------------------
  const projectValues = () => page.$$eval('#LPB-project-dropdown option', (els) => els.map((e) => e.value).filter((v) => v.startsWith('proj')).join(','));
  assert((await projectValues()) === 'proj-1', `the LLM view lists the LLM projects only (got ${await projectValues()})`);
  await page.click('#LPB-kind-decision');
  await waitUntil(async () => (await projectValues()) === 'proj-2', 'the decision view lists the decision projects only');
  step(true, 'each view lists its own projects');
  await page.selectOption('#LPB-project-dropdown', 'proj-2');
  await waitUntil(async () => (await page.$$('#LPB-prompt-dropdown option')).length === 2, 'the decision template is listed for its kind');
  assert((await visibleSteps()) === 'setup,build,evaluate,finalize', 'choosing the kind already switches the steps');
  await page.selectOption('#LPB-prompt-dropdown', 'model-dec');
  await waitUntil(async () => (await visibleSteps()) === 'setup,build,evaluate,finalize', 'decision mode steps');
  step(true, 'opening a decision template reveals the Evaluate step');
  assert((await stepNumbers()) === '1,2,3,4', `visible steps are numbered without a gap (got ${await stepNumbers()})`);
  await goStep('build');
  assert(await page.isVisible('#decision-model0'), 'the decision models replace the LLMs');
  assert(!(await page.isVisible('#model0')), 'the LLM checkboxes are hidden');
  assert(!(await page.isVisible('#app-obj-LPB-judge-model')), 'the judge card is hidden');
  assert(await page.isVisible('#app-obj-LPB-decision-state'), 'the state template is shown');
  assert(!(await page.isVisible('#app-obj-LPB-system-prompt')), 'the prompt editors are hidden');
  assert(
    (await page.inputValue('#app-obj-LPB-decision-state')).startsWith('Subject: {{subject}}'),
    'the saved run restored the state template'
  );
  assert((await page.$$('.pb-question-row')).length === 2, 'the saved run restored its two questions');
  const questionIds = await page.$$eval('.pb-question-id', (els) => els.map((e) => e.value).join(','));
  assert(questionIds === 'team,escalate', `question names restored (got ${questionIds})`);
  const expectedValues = await page.$$eval('.pb-question-expected', (els) => els.map((e) => e.value).join(','));
  assert(expectedValues === 'billing,no', `expected answers restored (got ${expectedValues})`);
  assert((await page.$$('.pb-variable-row')).length === 2, 'the variables were restored');
  assert(await page.isChecked('#decision-model0'), 'the run\'s decision model is selected');
  assert(!(await page.isChecked('#decision-model1')), 'the other decision model is not');
  assert(!(await page.isDisabled('#app-obj-LPB-run-experiment')), 'Run Experiments is enabled');
  // The model cards: what each registration says, the price of this request, the fit.
  assert((await page.$$('.pb-model-card')).length === 2, 'one card per decision model');
  const cardMeta = await page.$$eval('.pb-model-card-meta', (els) => els.map((e) => e.textContent));
  assert(cardMeta[0].includes('OpenRouter') && cardMeta[0].includes('Proprietary'), `the card names provider and licence (got ${cardMeta[0]})`);
  assert(cardMeta[1].includes('Apache-2') && cardMeta[1].includes('at most 6 options') && cardMeta[1].includes('choice, noul'), `the card shows the option limit and question types (got ${cardMeta[1]})`);
  const cardPrice = await page.textContent('.pb-model-card .pb-model-card-price');
  assert(cardPrice.includes('per 1,000 calls'), `the card prices the current request (got ${cardPrice})`);
  assert((await page.$$('.pb-model-fit.is-ok')).length === 2, 'both models fit the restored request');
  // The lint: the restored choice has no escape option.
  const firstRowLint = await page.textContent('.pb-question-row .pb-question-lint');
  assert(firstRowLint.includes('No escape option'), `the lint flags the missing escape option (got ${firstRowLint.slice(0, 60)})`);
  assert(await page.$('.pb-question-row .pb-question-add-escape'), 'the lint offers the one-click fix');
  const checklistText = await page.textContent('#app-obj-LPB-decision-checklist');
  assert(checklistText.includes('0 to fix') && checklistText.includes('Every question is complete'), `the checklist sums the lint up (got ${checklistText.slice(0, 80)})`);
  assert((await page.textContent('#app-obj-LPB-decision-state-info')).includes('tokens'), 'the state shows its token estimate');
  assert((await page.$$('#app-obj-LPB-decision-template option')).length === 9, 'eight templates are offered');
  assert(!(await page.isVisible('#app-obj-LPB-decision-help-state')), 'help is folded away');
  await page.click('.pb-help-toggle');
  assert(await page.isVisible('#app-obj-LPB-decision-help-state'), 'the help toggle reveals the help text');
  // A toggle beside a text field shares a flex row with it and must not be squeezed.
  const toggleShapes = await page.$$eval('.pb-help-toggle', (els) =>
    els.map((e) => e.getBoundingClientRect()).filter((r) => r.width > 0).map((r) => `${Math.round(r.width)}x${Math.round(r.height)}`)
  );
  assert(
    toggleShapes.length >= 3 && toggleShapes.every((shape) => shape.split('x')[0] === shape.split('x')[1]),
    `every help toggle is a circle, also the ones in a question row (got ${[...new Set(toggleShapes)].join(' ')})`
  );
  // The saved run in the tracker
  assert(await page.isVisible('#app-obj-LPB-pet-0'), 'the saved run is rendered');
  const runBodyText = await page.textContent('#app-obj-LPB-pet-0');
  assert(runBodyText.includes('Questions:') && runBodyText.includes('State:'), 'the run shows its questions and state');
  await page.click('#app-obj-LPB-pet-0 > .accordion-item > h2 > .accordion-button');
  // Bootstrap ignores a toggle while the parent is still animating open.
  await waitUntil(async () => Boolean(await page.$('#app-obj-LPB-pet-0 > .accordion-item > .accordion-collapse.show')), 'run 0 expanded');
  await page.click('#app-obj-LPB-pet-0-run-nested-jev_mock-header');
  await waitUntil(async () => page.isVisible('#app-obj-LPB-pet-0-run-nested-jev_mock-body .pb-answers'), 'answers rendered');
  assert((await page.$$('#app-obj-LPB-pet-0 .pb-answer-expected.is-match')).length === 2, 'both answers match their expected answers');
  assert(await page.$('#app-obj-LPB-pet-0 .bestPrompt'), 'the correct run carries the best icon');
  // The box is what Manifest takes, and says so; that the answers matched is
  // a note of its own (verify-outcomes.js: a run ticked whose answers did not).
  const bestLabel = await page.textContent('label[for="best-prompt-0-jev_mock"]');
  assert(bestLabel.trim() === 'Use for Manifest', `the checkbox reads "Use for Manifest" in decision mode (got ${bestLabel})`);
  const matchedNote = await page.$eval('#app-obj-LPB-pet-0-run-nested-jev_mock-body', (body) => body.querySelector('.pb-expected-all-matched')?.textContent ?? '');
  assert(matchedNote === 'All expected answers matched', `the run whose answers all match carries the note that they do (got "${matchedNote}")`);
  assert((await page.$$('#app-obj-LPB-pet-0 .pb-prob-line')).length >= 4, 'probability bars are drawn per option');
  const legendText = await page.textContent('#app-obj-LPB-pet-legend');
  assert(legendText.includes('Use for Manifest') && !legendText.includes('Judge the run'), 'the legend reads for a decision template');

  // ---- run against the decision model -----------------------------------------
  await resetLog();
  await page.click('#app-obj-LPB-run-experiment');
  await waitUntil(async () => page.isVisible('#app-obj-LPB-pet-1'), 'second run rendered');
  step(true, 'a decision run adds a run to the tracker');
  let log = await getLog();
  const decisionCall = log.find((e) => e.url === '/scr/jev_mock/jev_mock' && e.method === 'POST');
  assert(decisionCall, 'the decision model was called through the SCR endpoint');
  const callInputs = JSON.parse(decisionCall.body).inputs;
  const stateSent = callInputs.find((i) => i.name === 'state').value;
  assert(stateSent.startsWith('Subject: Charged twice'), `the state was sent with its variables filled in (got ${stateSent.slice(0, 22)})`);
  const questionsSent = JSON.parse(callInputs.find((i) => i.name === 'questions').value);
  assert(questionsSent.team.type === 'choice' && questionsSent.escalate.type === 'noul', 'the questions were sent in the System One shape');
  assert(callInputs.find((i) => i.name === 'options').value.includes('API_KEY:sk-mock-openai-key'), 'the provider key travels in the options');
  assert(await page.$('#app-obj-LPB-pet-1 .bestPrompt'), 'the new run is marked correct automatically (answers match)');
  await page.click('#app-obj-LPB-pet-1 > .accordion-item > h2 > .accordion-button');
  await waitUntil(async () => Boolean(await page.$('#app-obj-LPB-pet-1 > .accordion-item > .accordion-collapse.show')), 'run 1 expanded');
  await page.click('#app-obj-LPB-pet-1-run-nested-jev_mock-header');
  await waitUntil(async () => page.isVisible('#app-obj-LPB-pet-1-run-nested-jev_mock-body .pb-answers'), 'run 1 answers');
  const readingText = await page.textContent('#app-obj-LPB-pet-1-run-nested-jev_mock-body .pb-answer-reading');
  assert(readingText.includes('Top answer billing at 90%') && readingText.includes('A clear decision'), `the answer is read in plain language (got ${readingText})`);
  assert((await page.$$('#app-obj-LPB-pet-1-run-nested-jev_mock-body .pb-gate.is-auto')).length === 2, 'both answers would be automated at the default threshold');
  assert(await page.$('#app-obj-LPB-pet-1-run-nested-jev_mock-body .pb-answer-invented'), 'a confident choice without an escape option is flagged');

  // ---- the escape fix, then a run against both models -------------------------
  await page.click('.pb-question-row .pb-question-add-escape');
  const criteriaAfterFix = await page.inputValue('.pb-question-row .pb-question-criteria');
  assert(criteriaAfterFix.includes('unknown:'), 'the fix appends the unknown option');
  assert((await page.$$('.pb-question-row:first-child .pb-lint-item.is-warn')).length === 0, 'the warning is gone');
  await page.check('#decision-model1');
  await resetLog();
  await page.click('#app-obj-LPB-run-experiment');
  await waitUntil(async () => page.isVisible('#app-obj-LPB-pet-2'), 'two-model run rendered');
  log = await getLog();
  assert(log.some((e) => e.url === '/scr/von_mock/von_mock' && e.method === 'POST'), 'the second model was called too');
  await page.click('#app-obj-LPB-pet-2 > .accordion-item > h2 > .accordion-button');
  await waitUntil(async () => Boolean(await page.$('#app-obj-LPB-pet-2 > .accordion-item > .accordion-collapse.show')), 'run 2 expanded');
  const compareText = await page.textContent('#app-obj-LPB-pet-2-run-compare');
  assert(compareText.includes('jev_mock') && compareText.includes('von_mock') && compareText.includes('all agree'), `the models are compared side by side (got ${compareText.slice(0, 80)})`);

  // ---- add a score question and run again --------------------------------------
  await page.click('#app-obj-LPB-decision-add-question');
  const rows = await page.$$('.pb-question-row');
  const newRow = rows[rows.length - 1];
  await (await newRow.$('.pb-question-id')).fill('tone');
  await (await newRow.$('.pb-question-type')).selectOption('score');
  await (await newRow.$('.pb-question-instructions')).fill('How frustrated does the customer sound?');
  await (await newRow.$('.pb-question-criteria')).fill('calm\nannoyed\nangry');
  await (await newRow.$('.pb-question-expected')).fill('1');
  assert((await page.$$('.pb-model-fit.is-no')).length === 1, 'the score question makes the choice-and-noul model unfit');
  await resetLog();
  await page.click('#app-obj-LPB-run-experiment');
  await waitUntil(async () => page.isVisible('#app-obj-LPB-pet-3'), 'fourth run rendered');
  log = await getLog();
  assert(!log.some((e) => e.url === '/scr/von_mock/von_mock' && e.method === 'POST'), 'the unfit model is not called');
  const skippedText = await page.textContent('#app-obj-LPB-run-error');
  assert(skippedText.includes('Skipped') && skippedText.includes('von_mock'), `the skipped model is named (got ${skippedText})`);
  await page.click('#app-obj-LPB-pet-3 > .accordion-item > h2 > .accordion-button');
  // Bootstrap ignores a toggle while the parent is still animating open.
  await waitUntil(async () => Boolean(await page.$('#app-obj-LPB-pet-3 > .accordion-item > .accordion-collapse.show')), 'run 3 expanded');
  await page.click('#app-obj-LPB-pet-3-run-nested-jev_mock-header');
  await waitUntil(async () => page.isVisible('#app-obj-LPB-pet-3-run-nested-jev_mock-body .pb-answers'), 'fourth run answers');
  const thirdRunText = await page.textContent('#app-obj-LPB-pet-3-run-nested-jev_mock-body');
  assert(thirdRunText.includes('tone') && thirdRunText.includes('annoyed'), 'the score question shows its level text');
  assert((await page.$$('#app-obj-LPB-pet-3 .pb-answer-expected.is-match')).length === 3, 'all three expected answers matched');

  // ---- a decision model without a container: named beside the skipped one, no empty run ----
  const failModel = (name, status) => fetch(BASE + '/__failmodel', { method: 'POST', body: JSON.stringify({ name, status }) });
  const runErrorLines = async () =>
    (await page.innerText('#app-obj-LPB-run-error')).split('\n').map((line) => line.trim()).filter(Boolean);
  await failModel('jev_mock', 503);
  await page.click('#app-obj-LPB-run-experiment');
  await waitUntil(async () => (await runErrorLines()).some((line) => line.startsWith('jev_mock:')), 'the failed decision model is reported');
  await waitUntil(async () => !(await page.isDisabled('#app-obj-LPB-run-experiment')), 'Run Experiments enabled after the failed run');
  const failedRunLines = await runErrorLines();
  assert(
    failedRunLines.length === 2 && failedRunLines[0].includes('Skipped') && failedRunLines[0].includes('von_mock'),
    `the line naming the skipped model stays (got ${failedRunLines.join(' | ')})`
  );
  assert(
    failedRunLines[1] ===
      'jev_mock: Decision model call failed with status 503 - the model is registered, but no container answers at this endpoint (it has to be published and running)',
    `the failed model is named with the not-deployed hint (got ${failedRunLines[1]})`
  );
  assert(!(await page.$('#app-obj-LPB-pet-4')), 'a run in which no decision model answered adds no run');
  await failModel();

  // ---- save: the rows carry the kind and the expected answers --------------------
  await resetLog();
  await page.click('#app-obj-LPB-pet-save-button');
  await waitUntil(async () => Boolean(findPart(await getLog(), 'Prompt-Experiment-Tracker.json')), 'tracker saved');
  log = await getLog();
  const savedRows = multipartJson(findPart(log, 'Prompt-Experiment-Tracker.json').body);
  const headers = savedRows.filter((r) => r.model === '');
  assert(headers.length === 4 && headers.every((r) => r.mode === 'decision'), 'every saved header row carries mode=decision');
  assert(headers[3].expected && headers[3].expected.tone === '1', 'the expected answers are saved with the run');
  assert(savedRows.find((r) => r.model === 'jev_mock' && r.runId === 4).response.includes('"tone"'), 'the answers are saved as the response');

  // ---- evaluate over the saved runs, then over a CAS table --------------------------
  await goStep('evaluate');
  assert(await page.isVisible('#app-obj-LPB-evaluate-run'), 'the Evaluate step shows its card');
  await page.click('#app-obj-LPB-evaluate-run');
  await waitUntil(async () => (await page.$$('#app-obj-LPB-evaluate-results .pb-evaluation')).length >= 1, 'tracker evaluation rendered');
  let evaluationText = await page.textContent('#app-obj-LPB-evaluate-results');
  assert(evaluationText.includes('4 cases') && evaluationText.includes('accuracy 100%'), `the saved runs evaluate to 100% (got: ${evaluationText.slice(0, 120)})`);
  assert(evaluationText.includes('Expected calibration error') && evaluationText.includes('Brier score'), 'calibration is reported');
  assert(await page.$('#app-obj-LPB-evaluate-results .pb-reliability'), 'a reliability diagram is drawn');
  assert(await page.$('#app-obj-LPB-evaluate-results .pb-confusion'), 'a confusion table is drawn for the choice');
  assert(await page.$('#app-obj-LPB-evaluate-results .pb-threshold'), 'the confidence threshold slider is drawn');
  await page.click('#app-obj-LPB-evaluate-source-cas');
  await waitUntil(async () => (await page.$$('#app-obj-LPB-evaluate-cas-server option')).length >= 2, 'CAS servers listed');
  await page.selectOption('#app-obj-LPB-evaluate-cas-server', 'cas-shared-default');
  await waitUntil(async () => (await page.$$('#app-obj-LPB-evaluate-cas-lib option')).length >= 2, 'caslibs listed');
  await page.selectOption('#app-obj-LPB-evaluate-cas-lib', 'Public');
  await waitUntil(async () => (await page.$$('#app-obj-LPB-evaluate-cas-table option')).length >= 2, 'tables listed');
  await page.selectOption('#app-obj-LPB-evaluate-cas-table', 'DEC_CASES');
  // While the models are asked about the rows of the table the kind cannot be
  // changed, as during a run: the change would empty the page under the calls.
  const slowDecisionCalls = '**/scr/**';
  await page.route(slowDecisionCalls, async (route) => {
    await sleep(2500);
    await route.continue().catch(() => {});
  });
  await resetLog();
  await page.click('#app-obj-LPB-evaluate-run');
  await goStep('setup');
  await page.click('#LPB-kind-llm');
  await waitUntil(
    async () => (await page.$$eval('.toast-body', (els) => els.map((e) => e.textContent || ''))).some((t) => t.includes('cannot be changed while a run, an evaluation')),
    'a toast says that the kind cannot be changed during an evaluation'
  );
  step(true, 'a change of kind during a CAS evaluation is refused with the reason');
  assert(
    (await page.isChecked('#LPB-kind-decision')) && (await page.inputValue('#LPB-project-dropdown')) === 'proj-2' && (await page.inputValue('#LPB-prompt-dropdown')) === 'model-dec' && (await visibleSteps()) === 'setup,build,evaluate,finalize',
    'the decision kind, its project, its template and its steps stay as they were'
  );
  assert(!(await page.$$eval('#app-obj-LPB-evaluate-results .pb-evaluation', (els) => els.length)), 'the evaluation was still in flight at that moment');
  await goStep('evaluate');
  await waitUntil(async () => (await page.$$('#app-obj-LPB-evaluate-results .pb-evaluation')).length >= 1, 'CAS evaluation rendered', 15000);
  await page.unroute(slowDecisionCalls);
  log = await getLog();
  const casCalls = log.filter((e) => e.url === '/scr/jev_mock/jev_mock' && e.method === 'POST');
  assert(casCalls.length === 4, `one call per table row (got ${casCalls.length})`);
  assert(!log.some((e) => e.url === '/scr/von_mock/von_mock'), 'the unfit model sits the evaluation out');
  assert((await page.textContent('#app-obj-LPB-evaluate-status')).includes('von_mock'), 'the evaluation names the skipped model');
  evaluationText = await page.textContent('#app-obj-LPB-evaluate-results');
  assert(evaluationText.includes('4 cases'), 'the CAS evaluation covers the four rows');
  assert(evaluationText.includes('1 abstained'), `the out-of-scope row counts as abstained (got: ${evaluationText.slice(0, 160)})`);
  assert(evaluationText.includes('team: accuracy 100%') && evaluationText.includes('escalate: accuracy 100%'), `team and escalate evaluate to 100% (got: ${evaluationText.slice(0, 160)})`);
  const thresholdReadout = await page.textContent('#app-obj-LPB-evaluate-results .pb-threshold-readout');
  assert(thresholdReadout.includes('Coverage') && thresholdReadout.includes('Accuracy'), 'the threshold readout shows coverage and accuracy');

  // ---- manifest: typed outputs, no parsing block ---------------------------------------
  await goStep('finalize');
  assert(!(await page.isVisible('#app-obj-LPB-pet-manifest-integrated')), 'the integrated-call switch is hidden');
  assert(await page.isVisible('#app-obj-LPB-pet-decout-answers'), 'the decision outputs are shown');
  assert(!(await page.isDisabled('#app-obj-LPB-pet-create-model-button')), 'Manifest is enabled (a correct run exists)');
  await resetLog();
  await page.click('#app-obj-LPB-pet-create-model-button');
  await waitUntil(async () => Boolean(findPart(await getLog(), 'requirements.json')), 'the manifest was written', 20000);
  await sleep(500);
  log = await getLog();
  const outputVars = multipartJson(findPart(log, 'outputVar.json').body);
  const outputNames = outputVars.map((v) => v.name);
  assert(
    ['answers', 'answer', 'confidence', 'run_time', 'prompt_length', 'team', 'team_confidence', 'escalate', 'escalate_confidence', 'tone', 'tone_confidence'].every((n) => outputNames.includes(n)),
    `typed outputs per question (got ${outputNames.join(',')})`
  );
  assert(outputVars.find((v) => v.name === 'team_confidence').type === 'decimal', 'confidence outputs are decimal');
  const inputVars = multipartJson(findPart(log, 'inputVar.json').body);
  assert(inputVars.map((v) => v.name).join(',') === 'subject,body,API_KEY', `inputs are the state variables plus the key (got ${inputVars.map((v) => v.name).join(',')})`);
  const scoreEntry = findPartMatching(log, /filename="[a-z_]+\.py"/);
  assert(scoreEntry, 'the score code was written');
  const scoreCode = multipartText(scoreEntry.body);
  assert(scoreCode.includes('def scoreModel(subject, body, API_KEY):'), 'the score function takes the state variables');
  assert(scoreCode.includes('"name": "state"') && scoreCode.includes('questions = "{'), 'the score code posts state and baked questions');
  assert(scoreCode.includes('tone, tone_confidence = _headline('), 'every question is read out as an output pair');
  assert(!scoreCode.includes('parse_status'), 'no JSON parsing block');
  // The score code is Python a real interpreter accepts, and it runs: called
  // with the inputs, it posts the state and returns one value per output.
  const compiledNormal = compilePython(scoreCode, 'decision_normal.py');
  assert(compiledNormal.ok, `the manifested score code compiles (${compiledNormal.python}${compiledNormal.ok ? '' : ': ' + lastLine(compiledNormal.error)})`);
  const answersOf = (map) => ({ data: { answers: JSON.stringify(map), answer: 'billing', confidence: 0.9, run_time: 0.4, prompt_length: 60 } });
  const calledNormal = callScoreModel(
    scoreCode,
    ['Charged twice', 'Please refund one payment.', 'sk-test'],
    answersOf({
      team: { type: 'choice', choice: 'billing', confidence: 0.9, probabilities: { billing: 0.9, technical: 0.1 } },
      escalate: { type: 'noul', noul: 0.1 },
      tone: { type: 'score', score: 1.1, confidence: 0.6, probabilities: { 0: 0.2, 1: 0.7, 2: 0.1 } },
    })
  );
  assert(calledNormal.ok, `the manifested score code runs (${calledNormal.ok ? 'scoreModel returned' : lastLine(calledNormal.error)})`);
  assert(calledNormal.returned.length === outputVars.length, `scoreModel returns one value per output (${calledNormal.returned.length} of ${outputVars.length})`);
  const normalOutputs = Object.fromEntries(outputNames.map((name, index) => [name, calledNormal.returned[index]]));
  assert(
    normalOutputs.team === 'billing' && normalOutputs.team_confidence === 0.9 && normalOutputs.escalate === 'no' && normalOutputs.tone === '1',
    `every question comes back as its headline answer (got ${JSON.stringify(normalOutputs)})`
  );
  assert(
    calledNormal.request.body.inputs.find((i) => i.name === 'state').value.startsWith('Subject: Charged twice'),
    'the score code builds the state from its inputs'
  );
  assert(calledNormal.request.body.inputs.find((i) => i.name === 'options').value === '{API_KEY:sk-test}', 'the key input travels in the options');
  const requirementsEntry = findPart(log, 'requirements.json');
  assert(requirementsEntry, 'requirements.json ships with the manifested model');
  const tagPut = log.find((e) => e.method === 'PUT' && e.url === '/modelRepository/models/model-dec');
  assert(tagPut && JSON.parse(tagPut.body).tags.includes('Decision-Call-Included'), 'the manifested model is tagged');

  // ---- TLS verification in the manifested model: the option ticked -----------------------
  // This page says nothing about the option, so it is ticked. The score code
  // is then what it was before the option existed plus six lines, and it
  // behaves as before for every value of LLMCONTAINERSSLVERIFY.
  const TLS_WARNING = '#app-obj-LPB-pet-manifest-tls-warning';
  const TLS_TAG = 'TLS-Verification-Off';
  const describeVerify = (values) => values.map((value) => JSON.stringify(value)).join(', ');
  const assertTlsBehaviour = (code, args, verifyByDefault, label) => {
    for (const bundleExists of [false, true]) {
      const got = tlsVerifyOf(code, args, answersOf({ team: { type: 'choice', choice: 'billing', confidence: 0.9 } }), bundleExists);
      const expected = expectedTlsVerify(verifyByDefault, bundleExists);
      assert(
        JSON.stringify(got) === JSON.stringify(expected),
        `${label}, ${bundleExists ? 'with' : 'without'} a CA bundle: requests.post gets verify = ${describeVerify(expected)} for LLMCONTAINERSSLVERIFY unset, "false", "true" and "garbage" (got: ${describeVerify(got)})`
      );
    }
  };
  const decisionBefore = withoutTlsDefault(scoreCode, true);
  assert(
    decisionBefore.problem === '',
    `option ticked: the decision score code holds the six lines of the TLS default, with sslVerifyDefault = True (${decisionBefore.problem || 'as documented'})`
  );
  assert(
    sha256(portless(decisionBefore.code)) === DECISION_CODE_BEFORE_THE_TLS_OPTION,
    `option ticked: without those six lines the decision score code is byte for byte what it was before the option (sha256 ${sha256(portless(decisionBefore.code))})`
  );
  assertTlsBehaviour(scoreCode, ['Charged twice', 'Please refund one payment.', 'sk-test'], true, 'option ticked');
  assert(
    !JSON.parse(tagPut.body).tags.includes(TLS_TAG),
    `option ticked: the model does not carry ${TLS_TAG}, which an earlier manifest left on it (got: ${JSON.parse(tagPut.body).tags})`
  );
  assert(!(await page.isVisible(TLS_WARNING)), 'option ticked: the Finalize step shows no TLS warning');

  // ---- TLS verification in the manifested model: the option unticked ---------------------
  // A page of its own with sslVerify=false, on the saved run of the fixture.
  const tlsPage = await browser.newPage({ viewport: { width: 1500, height: 1100 } });
  const tlsPageErrors = [];
  tlsPage.on('pageerror', (e) => tlsPageErrors.push(e.message));
  await tlsPage.goto(`${APP_URL}&sslVerify=false`);
  await tlsPage.waitForSelector('#LPB-project-dropdown', { timeout: 15000 });
  await tlsPage.click('#LPB-kind-decision');
  await waitUntil(async () => (await tlsPage.$$eval('#LPB-project-dropdown option', (els) => els.map((e) => e.value))).includes('proj-2'), 'the decision project is listed on the page with the option unticked');
  await tlsPage.selectOption('#LPB-project-dropdown', 'proj-2');
  await waitUntil(async () => (await tlsPage.$$('#LPB-prompt-dropdown option')).length === 2, 'the decision template is listed on the page with the option unticked');
  await tlsPage.selectOption('#LPB-prompt-dropdown', 'model-dec');
  await waitUntil(async () => (await tlsPage.$$('.pet-run-delete')).length >= 1, 'the saved run is loaded on the page with the option unticked');
  await tlsPage.click('#app-obj-LPB-step-finalize');
  await tlsPage.waitForSelector('#app-obj-LPB-pane-finalize:not([hidden])');
  assert(await tlsPage.isVisible(TLS_WARNING), 'option unticked: the Finalize step of a decision template shows a TLS warning before the click');
  const tlsWarningText = (await tlsPage.textContent(TLS_WARNING)).trim();
  assert(
    tlsWarningText.includes('Verify TLS certificates in manifested models') && tlsWarningText.includes('LLMCONTAINERSSLVERIFY=true') && tlsWarningText.includes(TLS_TAG),
    `the warning names the option, the tag and the environment variable that turns verification back on (got: ${tlsWarningText})`
  );
  await resetLog();
  await tlsPage.click('#app-obj-LPB-pet-create-model-button');
  await waitUntil(async () => Boolean(findPart(await getLog(), 'requirements.json')), 'the manifest with the option unticked was written', 20000);
  await waitUntil(async () => (await getLog()).some((e) => e.method === 'PUT' && e.url === '/modelRepository/models/model-dec'), 'its tags were written');
  await waitUntil(async () => !(await tlsPage.isDisabled('#app-obj-LPB-pet-create-model-button')), 'the manifest with the option unticked has ended', 20000);
  const offLog = await getLog();
  const offCode = multipartText(findPartMatching(offLog, /filename="[a-z_]+\.py"/).body);
  const offBefore = withoutTlsDefault(offCode, false);
  assert(
    offBefore.problem === '',
    `option unticked: the decision score code holds the same six lines with sslVerifyDefault = False (${offBefore.problem || 'as documented'})`
  );
  const tlsLinesOf = (code) => code.slice(code.indexOf('    # TLS verification'), code.indexOf('    # Call the decision model container'));
  assert(
    tlsLinesOf(offCode).length > 400 && tlsLinesOf(offCode) === tlsLinesOf(scoreCode).replace('sslVerifyDefault = True', 'sslVerifyDefault = False'),
    'option unticked: the TLS lines differ from those of a ticked manifest in the value of sslVerifyDefault and in nothing else'
  );
  const compiledOff = compilePython(offCode, 'decision_tls_off.py');
  assert(compiledOff.ok, `the score code manifested with the option unticked compiles (${compiledOff.python}${compiledOff.ok ? '' : ': ' + lastLine(compiledOff.error)})`);
  const offArguments = /def scoreModel\(([^)]*)\)/.exec(offCode)[1].split(',').map((name) => name.trim()).filter(Boolean).map(() => 'x');
  assertTlsBehaviour(offCode, offArguments, false, 'option unticked');
  const offTags = JSON.parse(offLog.find((e) => e.method === 'PUT' && e.url === '/modelRepository/models/model-dec').body).tags;
  assert(
    offTags.includes(TLS_TAG) && offTags.includes('Decision-Call-Included'),
    `option unticked: the manifested model carries the tag ${TLS_TAG} (got: ${offTags})`
  );
  assert(tlsPageErrors.length === 0, `no uncaught error on the page with the option unticked (got: ${tlsPageErrors.join(' | ')})`);
  await tlsPage.close();

  // ---- the tag follows what was stored, and a failed tag update is said ------------------
  // As for a prompt template (verify.js): a score code SAS Model Manager did
  // not store changes no tag and is no manifest, and a tag update that failed
  // is reported in both directions. The fixture template carries the tag.
  const MANIFEST_BUTTON = '#app-obj-LPB-pet-create-model-button';
  const MANIFEST_BOX = '#app-obj-LPB-pet-save-result';
  const SAVE_BUTTON = '#app-obj-LPB-pet-save-button';
  // What an ingress answers in place of the service: no JSON, so the page throws.
  const errorPage = { status: 502, contentType: 'text/html', body: '<html><head><title>502 Bad Gateway</title></head><body><center><h1>502 Bad Gateway</h1></center></body></html>' };
  const openTemplate = async (url, label) => {
    const target = await browser.newPage({ viewport: { width: 1500, height: 1100 } });
    const errors = [];
    target.on('pageerror', (e) => errors.push(e.message));
    await target.goto(url);
    await target.waitForSelector('#LPB-project-dropdown', { timeout: 15000 });
    await target.click('#LPB-kind-decision');
    await waitUntil(async () => (await target.$$eval('#LPB-project-dropdown option', (els) => els.map((e) => e.value))).includes('proj-2'), `${label}: the decision project is listed`);
    await target.selectOption('#LPB-project-dropdown', 'proj-2');
    await waitUntil(async () => (await target.$$('#LPB-prompt-dropdown option')).length === 2, `${label}: the decision template is listed`);
    await target.selectOption('#LPB-prompt-dropdown', 'model-dec');
    await waitUntil(async () => (await target.$$('.pet-run-delete')).length >= 1, `${label}: the saved run is loaded`);
    await target.click('#app-obj-LPB-step-finalize');
    await target.waitForSelector('#app-obj-LPB-pane-finalize:not([hidden])');
    return { target, errors };
  };
  const faultyManifest = async (url, fault, label) => {
    const { target, errors } = await openTemplate(url, label);
    let scoreCodeSeen = false;
    let injected = 0;
    // After the upload of the score code, nothing of the template can be read.
    let readsFail = false;
    // The mock keeps no file of a fixture model: the score code the service
    // took is listed and served here, as SAS Model Manager would.
    let storedScore = null;
    await target.route('**/files/files/score-taken/content', (route) => route.fulfill({ status: 200, contentType: 'text/x-python', body: storedScore ? storedScore.text : '' }));
    await target.route('**/modelRepository/models/model-dec/contents?*', async (route) => {
      const request = route.request();
      if (readsFail && request.method() === 'GET') return route.abort('failed');
      if (storedScore && request.method() === 'GET') {
        const listing = await (await route.fetch()).json();
        listing.items = [...(listing.items || []).filter((item) => item.name !== storedScore.name), { id: 'c-score-taken', name: storedScore.name, fileUri: '/files/files/score-taken' }];
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(listing) });
      }
      if (request.method() === 'POST' && (request.postData() || '').includes('def scoreModel(')) {
        scoreCodeSeen = true;
        if (fault === 'score code') {
          injected += 1;
          return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ errorCode: 0, message: 'the files service does not answer', httpStatusCode: 500 }) });
        }
        // The upload of the score code THROWS in the page: an error page in
        // place of an answer, or - the service took the upload and stored it,
        // and the answer never reached the page; the page reads it back - or
        // cannot, when nothing of the template can be read after it.
        if (fault === 'score code, error page') {
          injected += 1;
          return route.fulfill(errorPage);
        }
        if (fault === 'score code, answer lost' || fault === 'score code, answer lost, nothing read back') {
          await route.fetch();
          injected += 1;
          const body = request.postData() || '';
          storedScore = { name: (/filename="([^"]+)"/.exec(body) || [])[1], text: (/\r\n\r\n([\s\S]*?)\r\n--/.exec(body) || [])[1] };
          if (fault === 'score code, answer lost, nothing read back') readsFail = true;
          return route.abort('failed');
        }
      }
      // A request that THROWS in the page: an answer that is no JSON (the
      // error page of an ingress), or no answer at all.
      const uploadOf = (file) => request.method() === 'POST' && (request.postData() || '').includes(`filename="${file}"`);
      // The save of the experiments, which is the first step of the click.
      if (fault === 'tracker, error page' && uploadOf('Prompt-Experiment-Tracker.json')) {
        injected += 1;
        return route.fulfill(errorPage);
      }
      if (fault === 'tracker, refused' && uploadOf('Prompt-Experiment-Tracker.json')) {
        injected += 1;
        return route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ errorCode: 1003, message: 'Forbidden', httpStatusCode: 403 }) });
      }
      if (fault === 'requirements.json, error page' && uploadOf('requirements.json')) {
        injected += 1;
        return route.fulfill(errorPage);
      }
      if (fault === 'requirements.json, no network' && uploadOf('requirements.json')) {
        injected += 1;
        return route.abort('failed');
      }
      if (fault === 'outputVar.json, error page' && uploadOf('outputVar.json')) {
        injected += 1;
        return route.fulfill(errorPage);
      }
      return route.continue();
    });
    // The first write of the model after the score code is the tag update.
    await target.route('**/modelRepository/models/model-dec', (route) => {
      if (route.request().method() === 'PUT' && scoreCodeSeen && fault === 'tags' && injected === 0) {
        injected += 1;
        return route.fulfill({ status: 412, contentType: 'application/json', body: JSON.stringify({ errorCode: 0, message: 'the model changed since it was read', httpStatusCode: 412 }) });
      }
      return route.continue();
    });
    await resetLog();
    await target.click(MANIFEST_BUTTON);
    await waitUntil(async () => injected === 1, `${label}: the ${fault} request is refused`, 20000);
    const ended = await settles(
      async () => !(await target.isDisabled(MANIFEST_BUTTON)) && (await target.textContent(MANIFEST_BUTTON)).trim() === 'Manifest Best Prompt',
      12000
    );
    assert(ended, `${label}: the manifest has ended and its button is usable again (button: ${(await target.textContent(MANIFEST_BUTTON)).trim()}${(await target.isDisabled(MANIFEST_BUTTON)) ? ', disabled' : ''})`);
    // The click saves the experiments first, through the Save button.
    await settles(async () => !(await target.isDisabled(SAVE_BUTTON)) && (await target.textContent(SAVE_BUTTON)).trim() === 'Save Experiments', 3000);
    await sleep(400);
    const entries = await getLog();
    const modelWrite = entries.find((e) => e.method === 'PUT' && e.url === '/modelRepository/models/model-dec');
    const toasts = await target.$$eval('.toast-body', (els) => els.map((e) => e.textContent || ''));
    const outcome = {
      // The tags of the first write of the model, which is the tag update.
      tags: modelWrite ? JSON.parse(modelWrite.body).tags || [] : null,
      scoreCodes: entries.filter((e) => e.method === 'POST' && e.body.includes('def scoreModel(')).length,
      // What the service took and stored, whatever the page was told.
      scoreCodesStored: entries.filter((e) => e.method === 'POST' && e.body.includes('def scoreModel(') && e.status < 300).length,
      modelWrites: entries.filter((e) => e.method === 'PUT' && e.url === '/modelRepository/models/model-dec').length,
      // The first requests of the manifest itself, after the save.
      manifestRequests: entries.filter((e) => (e.method === 'GET' && /\/models\/model-dec\/variables/.test(e.url)) || (e.method === 'POST' && e.body.includes('filename="inputVar.json"'))).length,
      requirements: Boolean(findPart(entries, 'requirements.json')),
      manifestedToasts: toasts.filter((text) => text.includes('has been manifested')).length,
      savedToasts: toasts.filter((text) => text.includes('Experiments saved')).length,
      notManifested: await target.$$eval(`${MANIFEST_BOX} .pet-manifest-failed`, (els) => els.map((e) => e.textContent.trim())),
      unknown: (await target.$$(`${MANIFEST_BOX} .pet-manifest-failed.pet-manifest-unknown`)).length,
      saveLines: await target.$$eval(`${MANIFEST_BOX} .pet-save-failed`, (els) => els.map((e) => e.textContent.trim())),
      box: ((await target.textContent(MANIFEST_BOX)) || '').trim(),
      tagLines: await target.$$eval(`${MANIFEST_BOX} .pet-manifest-tls-tag-failed`, (els) => els.map((e) => e.textContent.trim())),
      saveButton: `${(await target.textContent(SAVE_BUTTON)).trim()}${(await target.isDisabled(SAVE_BUTTON)) ? ', disabled' : ''}`,
      errors,
    };
    await target.close();
    return outcome;
  };
  const notStored = await faultyManifest(APP_URL, 'score code', 'score code refused');
  assert(
    notStored.modelWrites === 0 && !notStored.requirements,
    `a decision score code SAS Model Manager did not store changes no tag: the model is not written and keeps ${TLS_TAG}, and the manifest stops there (writes of the model: ${notStored.modelWrites}, requirements.json uploaded: ${notStored.requirements})`
  );
  assert(
    notStored.manifestedToasts === 0 && notStored.notManifested.length === 1 && notStored.notManifested[0].startsWith('Not manifested: the score code could not be stored') && notStored.errors.length === 0,
    `and it is reported as a manifest that failed, beside the button, with no toast saying "manifested" (box: ${notStored.notManifested.join(' | ') || 'nothing'}; toasts: ${notStored.manifestedToasts}; errors: ${notStored.errors.join(' | ') || 'none'})`
  );
  const notRemoved = await faultyManifest(APP_URL, 'tags', 'option ticked, tag update refused');
  assert(
    notRemoved.tagLines.length === 1 && notRemoved.tagLines[0].includes('could not be updated') && notRemoved.tagLines[0].includes(TLS_TAG) && notRemoved.tagLines[0].includes('remove it in SAS Model Manager'),
    `option ticked: a tag update that failed is reported - the model verifies and may still carry ${TLS_TAG} (got: ${notRemoved.tagLines.join(' | ') || 'nothing'})`
  );
  assert(notRemoved.manifestedToasts === 1 && notRemoved.notManifested.length === 0, 'the score code was stored, so this manifest did happen and says so');
  const notWritten = await faultyManifest(`${APP_URL}&sslVerify=false`, 'tags', 'option unticked, tag update refused');
  assert(
    notWritten.tagLines.length === 1 && notWritten.tagLines[0].includes('could not be written') && notWritten.tagLines[0].includes('add the tag in SAS Model Manager'),
    `option unticked: a tag update that failed is reported the other way round - the model does not verify and the tag has to be added (got: ${notWritten.tagLines.join(' | ') || 'nothing'})`
  );

  // ---- a request that throws while the decision manifest runs --------------------------
  // An answer that is no JSON (the 502 page of an ingress) and a request that
  // gets no answer THROW in the page. The manifest used to end where it
  // stood: the button stayed disabled on "Saving..." for good, nothing was
  // said, and the tags - written after requirements.json - were never
  // updated, so TLS-Verification-Off did not say what the stored score code
  // does. The template carries the tag here.
  const describe = (outcome) =>
    `tags: ${outcome.tags ? outcome.tags.join(', ') : 'not written'}; box: ${outcome.notManifested.join(' | ') || 'nothing'}; toasts: ${outcome.manifestedToasts}; errors: ${outcome.errors.join(' | ') || 'none'}`;
  const errorPageAfter = await faultyManifest(APP_URL, 'requirements.json, error page', 'option ticked, requirements.json answered by an error page');
  assert(
    errorPageAfter.scoreCodes === 1 && errorPageAfter.tags !== null && !errorPageAfter.tags.includes(TLS_TAG) && errorPageAfter.tags.includes('Decision-Call-Included'),
    `option ticked: the tags are updated directly after the score code is stored, so a request that throws after it leaves no ${TLS_TAG} on a template whose stored code verifies (${describe(errorPageAfter)})`
  );
  assert(
    errorPageAfter.manifestedToasts === 0 && errorPageAfter.notManifested.length === 1 && errorPageAfter.notManifested[0].startsWith('The manifest was not completed: the score code of this run is stored'),
    `and the box beside the button says that the manifest was not completed, with no toast saying "manifested" (${describe(errorPageAfter)})`
  );
  assert(errorPageAfter.tagLines.length === 0 && errorPageAfter.errors.length === 0, `nothing is said about the tags, which were written, and no error is left uncaught (${describe(errorPageAfter)})`);
  const noNetworkAfter = await faultyManifest(`${APP_URL}&sslVerify=false`, 'requirements.json, no network', 'option unticked, requirements.json gets no answer');
  assert(
    noNetworkAfter.scoreCodes === 1 && noNetworkAfter.tags !== null && noNetworkAfter.tags.includes(TLS_TAG),
    `option unticked: a request that gets no answer after the score code leaves the template tagged ${TLS_TAG}, as its stored code does not verify (${describe(noNetworkAfter)})`
  );
  assert(
    noNetworkAfter.manifestedToasts === 0 && noNetworkAfter.notManifested.length === 1 && noNetworkAfter.notManifested[0].startsWith('The manifest was not completed') && noNetworkAfter.errors.length === 0,
    `and it is reported the same way: not completed, no "manifested" toast, no uncaught error (${describe(noNetworkAfter)})`
  );
  const errorPageBefore = await faultyManifest(APP_URL, 'outputVar.json, error page', 'the output variables answered by an error page');
  assert(
    errorPageBefore.scoreCodes === 0 && errorPageBefore.modelWrites === 0,
    `a request that throws BEFORE the score code stores no score code and changes no tag (score codes: ${errorPageBefore.scoreCodes}, writes of the model: ${errorPageBefore.modelWrites})`
  );
  assert(
    errorPageBefore.manifestedToasts === 0 && errorPageBefore.notManifested.length === 1 && errorPageBefore.notManifested[0].startsWith('Not manifested: a request to SAS Model Manager failed before the score code was stored') && errorPageBefore.errors.length === 0,
    `and the box says that nothing was manifested and that the template keeps the score code and the tags it had (${describe(errorPageBefore)})`
  );

  // ---- the upload of the decision score code gets no usable answer -----------------------
  // As for a prompt template (verify.js): a refusal with an answer is certain;
  // an upload that THROWS is read back - not held, held (the manifest goes
  // on), or not readable, and only then is it said that this is not known.
  assert(
    notStored.unknown === 0 && notStored.notManifested[0].includes('The model keeps the score code and the tags it had'),
    `a decision score code SAS Model Manager refused with an answer keeps the certain wording: the model keeps the score code and the tags it had (box: ${notStored.notManifested.join(' | ') || 'nothing'})`
  );
  const errorPageScore = await faultyManifest(APP_URL, 'score code, error page', 'the decision score code answered by an error page');
  assert(
    errorPageScore.notManifested.length === 1 && errorPageScore.unknown === 0 &&
      errorPageScore.notManifested[0].startsWith('Not manifested: the upload of the score code got no usable answer from SAS Model Manager, and reading it back shows that the model does not hold the score code of this run') &&
      errorPageScore.scoreCodesStored === 0 && errorPageScore.modelWrites === 0 && !errorPageScore.requirements && errorPageScore.manifestedToasts === 0 && errorPageScore.tagLines.length === 0 && errorPageScore.errors.length === 0,
    `the decision score code answered by an error page, which stored nothing: the page reads it back, and the box says that the template does not hold it - no tag is written, no toast says "manifested" (score codes stored: ${errorPageScore.scoreCodesStored}; writes of the model: ${errorPageScore.modelWrites}; ${describe(errorPageScore)})`
  );
  const lostScore = await faultyManifest(`${APP_URL}&sslVerify=false`, 'score code, answer lost', 'the decision score code stored, its answer lost');
  assert(
    lostScore.scoreCodesStored === 1 && lostScore.notManifested.length === 0 && lostScore.manifestedToasts === 1 && lostScore.errors.length === 0,
    `the decision score code stored and its answer lost: the page reads it back, finds it stored, and the manifest goes on to "manifested" (score codes stored: ${lostScore.scoreCodesStored}; ${describe(lostScore)})`
  );
  const unknownScore = await faultyManifest(`${APP_URL}&sslVerify=false`, 'score code, answer lost, nothing read back', 'the decision score code stored, its answer lost, nothing read back');
  assert(
    unknownScore.notManifested.length === 1 && unknownScore.unknown === 1 && unknownScore.notManifested[0].startsWith('The manifest was not completed, and it is not known whether the score code of this run is stored') && unknownScore.notManifested[0].includes('Manifest again') && !unknownScore.notManifested[0].includes('keeps the score code'),
    `the decision score code stored, its answer lost and nothing read back: the box says that it is not known whether the score code was stored, and to manifest again - not that the template keeps the score code it had (${describe(unknownScore)})`
  );
  assert(
    unknownScore.scoreCodesStored === 1 && unknownScore.modelWrites === 0 && !unknownScore.requirements && unknownScore.manifestedToasts === 0 && unknownScore.tagLines.length === 0 && unknownScore.errors.length === 0,
    `and the manifest stops there - no tag is written, no toast says "manifested", no error is left uncaught - while the service did store the score code (score codes stored: ${unknownScore.scoreCodesStored}; writes of the model: ${unknownScore.modelWrites}; ${describe(unknownScore)})`
  );

  // ---- the save before a decision manifest fails outright --------------------------------
  // The click saves the experiments first. A request that threw in the save
  // left "Save Experiments" disabled on "Saving..." for good and said
  // nothing; a save that was refused was followed by the manifest.
  const describeSave = (outcome) =>
    `Save button: ${outcome.saveButton}; box: ${outcome.box || 'nothing'}; "saved" toasts: ${outcome.savedToasts}; "manifested" toasts: ${outcome.manifestedToasts}; errors: ${outcome.errors.join(' | ') || 'none'}`;
  const saveThrew = await faultyManifest(APP_URL, 'tracker, error page', 'a decision Manifest click whose save is answered by an error page');
  assert(saveThrew.saveButton === 'Save Experiments', `a decision Manifest click whose save is answered by an error page: Save Experiments is usable again (${describeSave(saveThrew)})`);
  assert(
    saveThrew.saveLines.length === 1 && saveThrew.saveLines[0].startsWith('Save Experiments: A request to SAS Viya got no usable answer, and reading back what SAS Viya holds shows that it was not carried out.') && saveThrew.notManifested.length === 1 && saveThrew.notManifested[0].startsWith('Not manifested: a manifest saves the experiments first'),
    `the box beside the Manifest button says that the save was read back and not carried out, and that nothing was manifested (${describeSave(saveThrew)})`
  );
  assert(
    saveThrew.savedToasts === 0 && saveThrew.manifestedToasts === 0 && saveThrew.errors.length === 0 && saveThrew.manifestRequests === 0 && saveThrew.scoreCodes === 0 && saveThrew.modelWrites === 0,
    `no toast says "saved" or "manifested", no error is left uncaught, and the manifest does not go on (requests of the manifest: ${saveThrew.manifestRequests}, score codes: ${saveThrew.scoreCodes}; ${describeSave(saveThrew)})`
  );
  const saveRefused = await faultyManifest(APP_URL, 'tracker, refused', 'a decision Manifest click whose save is refused');
  assert(
    saveRefused.manifestRequests === 0 && saveRefused.scoreCodes === 0 && saveRefused.modelWrites === 0 && saveRefused.manifestedToasts === 0,
    `a decision Manifest click whose save SAS Model Manager refuses: the manifest does not go on, and no toast says "manifested" (requests of the manifest: ${saveRefused.manifestRequests}, score codes: ${saveRefused.scoreCodes}, writes of the model: ${saveRefused.modelWrites}; ${describeSave(saveRefused)})`
  );
  assert(
    saveRefused.saveButton === 'Save Experiments' && saveRefused.box.includes("The experiment couldn't be saved") && saveRefused.notManifested.length === 1 && saveRefused.notManifested[0].startsWith('Not manifested: a manifest saves the experiments first') && saveRefused.errors.length === 0,
    `and the box says both: the experiments could not be saved, and nothing was manifested (${describeSave(saveRefused)})`
  );

  // ---- one decision manifest per click ------------------------------------------------------
  // The save is made to take its time, so both clicks of a double click land
  // while it is out: the button is taken at the click, not after the save.
  {
    const { target, errors } = await openTemplate(APP_URL, 'a double click on Manifest');
    await target.route('**/modelRepository/models/model-dec/contents?*', async (route) => {
      const request = route.request();
      if (request.method() === 'POST' && (request.postData() || '').includes('filename="Prompt-Experiment-Tracker.json"')) await sleep(700);
      await route.continue().catch(() => {});
    });
    await resetLog();
    await target.dblclick(MANIFEST_BUTTON);
    const disabledAtOnce = await target.isDisabled(MANIFEST_BUTTON);
    const manifestedToasts = async () => (await target.$$eval('.toast-body', (els) => els.map((e) => e.textContent || ''))).filter((text) => text.includes('has been manifested')).length;
    await waitUntil(async () => (await manifestedToasts()) >= 1, 'a double click on Manifest: a decision manifest goes through', 20000);
    await settles(async () => !(await target.isDisabled(MANIFEST_BUTTON)) && (await target.textContent(MANIFEST_BUTTON)).trim() === 'Manifest Best Prompt', 12000);
    // A second run, where there is one, ends within this.
    await sleep(2000);
    const entries = await getLog();
    const trackers = entries.filter((e) => e.method === 'POST' && e.body.includes('filename="Prompt-Experiment-Tracker.json"')).length;
    const scoreCodes = entries.filter((e) => e.method === 'POST' && e.body.includes('def scoreModel(')).length;
    assert(disabledAtOnce, `a click on Manifest takes the button at once, while the experiments are still being saved (disabled after the click: ${disabledAtOnce})`);
    assert(
      trackers === 1 && scoreCodes === 1 && (await manifestedToasts()) === 1 && errors.length === 0,
      `a double click on Manifest runs one decision manifest: the experiments are saved once, one score code is stored, one toast says "manifested" (saves: ${trackers}, score codes: ${scoreCodes}, toasts: ${await manifestedToasts()}, errors: ${errors.join(' | ') || 'none'})`
    );
    await target.close();
  }

  // ---- a template to start from ---------------------------------------------------------
  await goStep('build');
  await page.selectOption('#app-obj-LPB-decision-template', 'guardrail');
  await page.click('#app-obj-LPB-decision-template-apply');
  assert((await page.inputValue('#app-obj-LPB-decision-state')).startsWith('Agent wants to run'), 'the template fills the state');
  assert((await page.$$('.pb-question-row')).length === 2, 'the template replaces the questions');
  assert((await page.$$eval('.pb-question-id', (els) => els.map((e) => e.value))).join(',') === 'risk,needs_human', 'the template questions are named');
  assert((await page.$$('.pb-lint-item.is-warn')).length === 0, 'a template passes the lint without warnings');

  // ---- question names the score code cannot carry ---------------------------------------
  // A hostile state: backslashes that read as escape sequences, three quotes in
  // a row, JSON and stray braces, two variables and a quote as the last character.
  const HOSTILE_STATE =
    'Log C:\\Users\\new\\table \\d+ said """stop""" with {"k": [1, {"n": 2}]} for {{subject}} / {{ body }} and a brace } { end "';
  await page.fill('#app-obj-LPB-decision-state', HOSTILE_STATE);
  await page.click('#app-obj-LPB-decision-add-question');
  const nameRow = page.locator('.pb-question-row').last();
  const nameInput = nameRow.locator('.pb-question-id');
  await nameRow.locator('.pb-question-type').selectOption('noul');
  await nameRow.locator('.pb-question-instructions').fill('Does the text mention a file path?');
  const nameFeedback = async (name) => {
    await nameInput.fill(name);
    const invalid = await nameInput.evaluate((el) => el.classList.contains('is-invalid'));
    return invalid ? await nameRow.locator('.invalid-feedback').textContent() : '';
  };
  for (const [name, why] of [
    ['class', 'a Python keyword'],
    ['lambda', 'a Python keyword'],
    ['json', 'a module of the score code'],
    ['answers', 'a local of the score code'],
    ['Str', 'a builtin the score code calls, in another case'],
    ['sslVerifyDefault', 'the line of the score code that holds the TLS default'],
    ['subject', 'a variable of the state'],
    ['risk_confidence', "another question's confidence output"],
  ]) {
    const feedback = await nameFeedback(name);
    assert(feedback.includes('Python keyword') && feedback.includes('different name'), `the name ${name} is refused: ${why} (got ${feedback.slice(0, 40)})`);
  }
  const longFeedback = await nameFeedback('a_question_name_too_long');
  assert(longFeedback.includes('at most 21 characters'), `a name longer than 21 characters is refused (got ${longFeedback.slice(0, 60)})`);
  assert((await page.textContent('#app-obj-LPB-decision-checklist')).includes('1 to fix'), 'a refused name counts as something to fix');
  assert(await page.isDisabled('#app-obj-LPB-run-experiment'), 'Run Experiments waits until the name is fixed');
  assert((await nameFeedback('a_name_of_21_chars_xx')) === '', 'a name of 21 characters is accepted');
  assert((await nameFeedback('mentions_path')) === '', 'an ordinary name is accepted');
  await nameRow.locator('.pb-question-remove').click();
  assert((await page.$$('.pb-question-row')).length === 2, 'the two template questions remain');

  // ---- a hostile state manifests into Python that says the same thing -------------------
  // The run and the manifested model must send the same state: what was
  // tested is what is deployed. One model, so the manifest's inputs are known.
  await page.uncheck('#decision-model1');
  const manifestAndCheck = async (label, runIndex, answers) => {
    await page.locator('.pb-question-expected').nth(0).fill('read_only');
    await page.locator('.pb-question-expected').nth(1).fill('no');
    await resetLog();
    await page.click('#app-obj-LPB-run-experiment');
    await waitUntil(async () => page.isVisible(`#app-obj-LPB-pet-${runIndex}`), `${label}: run rendered`);
    const runCall = (await getLog()).find((e) => e.url === '/scr/jev_mock/jev_mock' && e.method === 'POST');
    const stateRun = JSON.parse(runCall.body).inputs.find((i) => i.name === 'state').value;
    assert(stateRun.includes('"""stop"""') && stateRun.includes('C:\\Users\\new\\table \\d+') && stateRun.endsWith('end "'), `${label}: the run sent the state as typed`);
    assert(await page.$(`#app-obj-LPB-pet-${runIndex} .bestPrompt`), `${label}: the run is marked correct`);
    await goStep('finalize');
    await waitUntil(async () => !(await page.isDisabled('#app-obj-LPB-pet-create-model-button')), `${label}: Manifest enabled`);
    await resetLog();
    await page.click('#app-obj-LPB-pet-create-model-button');
    await waitUntil(async () => Boolean(findPart(await getLog(), 'requirements.json')), `${label}: manifest written`, 20000);
    await sleep(500);
    const manifestLog = await getLog();
    const code = multipartText(findPartMatching(manifestLog, /filename="[a-z_]+\.py"/).body);
    const outputs = multipartJson(findPart(manifestLog, 'outputVar.json').body).map((v) => v.name);
    const compiled = compilePython(code, 'decision_hostile.py');
    assert(compiled.ok, `${label}: the score code compiles without a warning${compiled.ok ? '' : ' (' + lastLine(compiled.error) + ')'}`);
    const called = callScoreModel(code, ['Charged twice', 'Please refund one payment.', 'sk-test'], answersOf(answers));
    assert(called.ok, `${label}: the score code runs${called.ok ? '' : ' (' + lastLine(called.error) + ')'}`);
    assert(called.returned.length === outputs.length, `${label}: one value per output (${called.returned.length} of ${outputs.length})`);
    const stateManifested = called.request.body.inputs.find((i) => i.name === 'state').value;
    assert(stateManifested === stateRun, `${label}: the manifested model sends the state the run sent (got ${stateManifested})`);
    const values = Object.fromEntries(outputs.map((name, index) => [name, called.returned[index]]));
    await goStep('build');
    return values;
  };
  const hostileAnswers = {
    risk: { type: 'choice', choice: 'read_only', confidence: 0.8, probabilities: { read_only: 0.8, destructive: 0.2 } },
    needs_human: { type: 'noul', noul: 0.9 },
    mentions_path: { type: 'noul', noul: 0.2 },
  };
  const twoQuestions = await manifestAndCheck('two questions', 4, hostileAnswers);
  assert(
    twoQuestions.risk === 'read_only' && twoQuestions.needs_human === 'yes' && twoQuestions.needs_human_confidence === 0.9 && !('mentions_path' in twoQuestions),
    `two questions come back as two output pairs (got ${JSON.stringify(twoQuestions)})`
  );
  await page.click('#app-obj-LPB-decision-add-question');
  const thirdRow = page.locator('.pb-question-row').last();
  await thirdRow.locator('.pb-question-id').fill('mentions_path');
  await thirdRow.locator('.pb-question-type').selectOption('noul');
  await thirdRow.locator('.pb-question-instructions').fill('Does the text mention a file path?');
  const threeQuestions = await manifestAndCheck('three questions', 5, hostileAnswers);
  assert(
    threeQuestions.risk === 'read_only' && threeQuestions.mentions_path === 'no' && threeQuestions.mentions_path_confidence === 0.8,
    `three questions come back as three output pairs (got ${JSON.stringify(threeQuestions)})`
  );

  // ---- saved runs the manifest cannot take over as they are ---------------------------
  // The manifest is generated from the saved run. A template saved by an
  // earlier release can carry a question name or a variable name that release
  // accepted and the score code cannot carry: the checks of the designer are
  // made again at the manifest, which names what has to be renamed and writes
  // nothing. The tracker of the template is served from here for these cases.
  const servedTracker = { rows: null };
  const trackerFile = '**/files/files/dtrk-1/content';
  await page.route(trackerFile, (route) =>
    servedTracker.rows ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(servedTracker.rows) }) : route.continue()
  );
  const servedRun = (questions, state, variables, model = 'jev_mock', options = '{API_KEY:OpenAI}') => [
    {
      runId: 1, systemPrompt: JSON.stringify(questions), userPrompt: state, model: '', options: '', response: '', run_time: null, prompt_length: null,
      output_length: null, best_prompt: null, fastest_prompt: null, fewest_tokens_prompt: null, mode: 'decision', expected: {}, variables,
    },
    {
      runId: 1, systemPrompt: '', userPrompt: '', model, options, response: '{}', run_time: 1.2, prompt_length: 10, output_length: 0,
      best_prompt: 1, fastest_prompt: true, fewest_tokens_prompt: null,
    },
  ];
  const openServedRun = async (rows) => {
    servedTracker.rows = rows;
    await goStep('setup');
    await page.selectOption('#LPB-prompt-dropdown', 'Select an existing Prompt-Test');
    await page.selectOption('#LPB-prompt-dropdown', 'model-dec');
    await waitUntil(async () => (await page.$$('#app-obj-LPB-pet > .accordion > .accordion-item')).length === 1, 'the served run is loaded');
    await goStep('finalize');
    await waitUntil(async () => !(await page.isDisabled('#app-obj-LPB-pet-create-model-button')), 'Manifest is enabled for the served run');
  };
  const manifestBox = async () => ((await page.textContent('#app-obj-LPB-pet-save-result')) || '').trim();
  const bodyVariable = [{ name: 'body', description: '', type: 'string', value: 'Please refund one payment.' }];
  const urgent = { urgent: { type: 'noul', instructions: 'Is this urgent?' } };
  for (const [label, rows, named, reason] of [
    ['a question called class', servedRun({ class: { type: 'noul', instructions: 'Is this urgent?' } }, 'Message: {{body}}', bodyVariable), 'the question "class"', 'Python keyword'],
    ['a question called answer', servedRun({ answer: { type: 'noul', instructions: 'Is this urgent?' } }, 'Message: {{body}}', bodyVariable), 'the question "answer"', 'Python keyword'],
    ['a question name of 25 characters', servedRun({ a_question_name_of_25_chr: { type: 'noul', instructions: 'Is this urgent?' } }, 'Message: {{body}}', bodyVariable), 'the question "a_question_name_of_25_chr"', 'at most 21 characters'],
    ['a state variable called options', servedRun(urgent, 'Message: {{options}}', [{ name: 'options', description: '', type: 'string', value: 'CUSTOMER TEXT' }]), 'the variable "options"', 'score code'],
  ]) {
    await openServedRun(rows);
    await resetLog();
    await page.click('#app-obj-LPB-pet-create-model-button');
    await waitUntil(async () => (await manifestBox()).startsWith('Not manifested'), `the manifest of a saved run with ${label} is refused`);
    const refusal = await manifestBox();
    assert(refusal.includes(named) && refusal.includes(reason), `the manifest box names ${named} and says why (got: ${refusal})`);
    await waitUntil(async () => !(await page.isDisabled('#app-obj-LPB-pet-create-model-button')), 'the Manifest button is usable again');
    const written = (await getLog()).filter((e) => e.method === 'POST' && /def scoreModel\(|filename="inputVar\.json"|filename="outputVar\.json"|filename="requirements\.json"/.test(e.body));
    assert(written.length === 0, `nothing of a model is uploaded for ${label} (got ${written.length} uploads)`);
  }
  // An option value with backslashes, as a string option with quotes in it is
  // stored, and a decision model whose name holds a double quote: both are
  // inserted as literals, so the code compiles and sends the stored text. The
  // page knows no key entry for a model of that name, so the run carries none
  // and the manifested model takes the state variable alone.
  await openServedRun(servedRun(urgent, 'Message: {{body}}', bodyVariable, 'odd"dec', '{mode:say \\hi\\,API_KEY:OpenAI}'));
  await resetLog();
  await page.click('#app-obj-LPB-pet-create-model-button');
  await waitUntil(async () => Boolean(findPart(await getLog(), 'requirements.json')), 'the manifest of the run with the odd option value is written', 20000);
  const oddLog = await getLog();
  const oddCode = multipartText(findPartMatching(oddLog, /filename="[a-z_]+\.py"/).body);
  const oddCompiled = compilePython(oddCode, 'decision_options.py');
  assert(oddCompiled.ok, `the score code with a backslash in an option value and a quote in the model name compiles without a warning${oddCompiled.ok ? '' : ' (' + lastLine(oddCompiled.error) + ')'}`);
  const oddStored = multipartJson(findPart(oddLog, 'Prompt-Experiment-Tracker.json').body).find((row) => row.model === 'odd"dec').options;
  const oddCalled = callScoreModel(oddCode, ['Please refund one payment.'], answersOf({ urgent: { type: 'noul', noul: 0.9 } }));
  assert(oddCalled.ok, `it runs${oddCalled.ok ? '' : ' (' + lastLine(oddCalled.error) + ')'}`);
  const oddOptions = oddCalled.request.body.inputs.find((i) => i.name === 'options').value;
  assert(
    /say .*hi/.test(oddStored) && oddStored.includes(String.fromCharCode(92)) && oddOptions === oddStored,
    `the manifested model sends the options the tracker stores for the run (sent: ${oddOptions}, stored: ${oddStored})`
  );
  assert(oddCalled.request.url === `${BASE}/scr/odd"dec/odd"dec`, `the model name reaches the URL as it is stored (got: ${oddCalled.request.url})`);
  servedTracker.rows = null;
  await page.unroute(trackerFile);

  // ---- back to an LLM prompt: everything switches back --------------------------------
  await goStep('setup');
  // A project created in the decision view carries the decision tag.
  await resetLog();
  await page.click('[data-bs-target="#promptBuilderCreateProjectModal"]');
  await page.waitForSelector('#promptBuilderCreateProjectModal.show');
  // Without a name the dialog asks SAS Model Manager for nothing, in this
  // kind as in the other. The request is answered here the way the service
  // answers an empty name, so that a page that does send it creates nothing.
  let unnamedProjects = 0;
  const projectCreation = '**/modelRepository/projects';
  await page.route(projectCreation, (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    unnamedProjects += 1;
    return route.fulfill({ status: 400, contentType: 'application/json', body: '{"errorCode":0,"message":"The name must not be empty.","httpStatusCode":400}' });
  });
  await page.fill('#promptBuilderCreateProjectName', '  ');
  await page.click('#promptBuilderCreateProjectModal .btn-primary');
  await sleep(800);
  const unnamed = await page.evaluate(() => {
    const error = document.getElementById('promptBuilderCreateProjectError');
    return {
      error: error && !error.hidden ? error.textContent.trim() : '',
      focus: document.activeElement ? document.activeElement.id : '',
      createOff: document.querySelector('#promptBuilderCreateProjectModal .btn-primary').disabled,
    };
  });
  assert(unnamedProjects === 0, `a decision project without a name: no creation request is sent to SAS Model Manager (sent: ${unnamedProjects})`);
  assert(
    unnamed.error === 'A project name is required.' && unnamed.focus === 'promptBuilderCreateProjectName' && !unnamed.createOff,
    `the dialog says that a name is needed, puts the cursor into the name field and keeps its button usable (dialog: ${unnamed.error || 'nothing'}; focus: ${unnamed.focus || 'nowhere'}; button disabled: ${unnamed.createOff})`
  );
  // A name that shows nothing is no name either: a zero-width space, with
  // the word joiner and a blank, got past trim().
  await page.fill('#promptBuilderCreateProjectName', '\u200B\u2060 ');
  await page.click('#promptBuilderCreateProjectModal .btn-primary');
  await sleep(800);
  const invisibleError = await page.evaluate(() => {
    const error = document.getElementById('promptBuilderCreateProjectError');
    return error && !error.hidden ? error.textContent.trim() : '';
  });
  await page.unroute(projectCreation);
  assert(
    unnamedProjects === 0 && invisibleError === 'A project name is required.',
    `a decision project whose name is made of characters that are not displayed: no creation request is sent, and the dialog says that a name is needed (sent: ${unnamedProjects}; dialog: ${invisibleError || 'nothing'})`
  );
  // Nor is a name of characters that are drawn as nothing without being white
  // space or set apart by Unicode: the braille pattern blank, alone and with
  // a control character beside it.
  const createProjectDialog = () =>
    page.evaluate(() => {
      const modal = document.getElementById('promptBuilderCreateProjectModal');
      const error = document.getElementById('promptBuilderCreateProjectError');
      const create = modal.querySelector('.btn-primary');
      const active = document.activeElement;
      return {
        open: modal.classList.contains('show'),
        error: error && !error.hidden ? error.textContent.trim() : '',
        focus: active === create ? 'Create' : active === modal ? 'dialog' : active === document.body ? 'body' : active ? active.id || active.tagName : 'nowhere',
        createOff: create.disabled,
        nameOff: document.getElementById('promptBuilderCreateProjectName').disabled,
        closersOff: modal.querySelector('.btn-secondary').disabled && modal.querySelector('.btn-close').disabled,
      };
    });
  let blankLookingProjects = 0;
  await page.route(projectCreation, (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    blankLookingProjects += 1;
    return route.fulfill({ status: 400, contentType: 'application/json', body: '{"errorCode":0,"message":"The name must not be empty.","httpStatusCode":400}' });
  });
  for (const [name, what] of [
    ['\u2800', 'the braille pattern blank (U+2800)'],
    ['\u2800\u007F \u2800', 'braille blanks, a control character and a blank'],
  ]) {
    await page.fill('#promptBuilderCreateProjectName', name);
    await page.click('#promptBuilderCreateProjectModal .btn-primary');
    await sleep(800);
    const blankLooking = await createProjectDialog();
    assert(
      blankLookingProjects === 0 && blankLooking.error === 'A project name is required.' && blankLooking.focus === 'promptBuilderCreateProjectName',
      `a decision project whose name is ${what}: no creation request is sent, and the dialog says that a name is needed (sent: ${blankLookingProjects}; dialog: ${blankLooking.error || 'nothing'}; focus: ${blankLooking.focus})`
    );
    blankLookingProjects = 0;
  }
  // The repository is asked before the project is created. Refused, that
  // request ended the click without a word and with an error on the page; it
  // ends in the dialog, with nothing created.
  const repositoryRefused = '**/modelRepository/repositories/**';
  await page.route(repositoryRefused, (route) => route.fulfill({ status: 403, contentType: 'application/json', body: '{"errorCode":1003,"message":"Forbidden","httpStatusCode":403}' }));
  const errorsBeforeLookup = pageErrors.length;
  await page.fill('#promptBuilderCreateProjectName', 'Routing Decisions');
  await page.click('#promptBuilderCreateProjectModal .btn-primary');
  await sleep(1200);
  const afterLookup = await createProjectDialog();
  await page.unroute(repositoryRefused);
  await page.unroute(projectCreation);
  assert(
    blankLookingProjects === 0 && afterLookup.open && afterLookup.error.includes('the model repository could not be read') && !afterLookup.createOff && !afterLookup.nameOff && pageErrors.length === errorsBeforeLookup,
    `a decision project while the repository cannot be read: nothing is created, the dialog stays open and says so, its button and its name field are usable, and no error reaches the page (creation requests: ${blankLookingProjects}; dialog: ${afterLookup.error || 'nothing'}; button disabled: ${afterLookup.createOff}; page errors: ${pageErrors.slice(errorsBeforeLookup).join(' | ') || 'none'})`
  );
  // Created by keyboard and refused: the focus waits on the dialog while the
  // creation is out - where Escape closes nothing - and is back on the
  // button afterwards, where Escape closes the dialog.
  await page.route(projectCreation, async (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    await sleep(1500);
    return route.fulfill({ status: 409, contentType: 'application/json', body: '{"errorCode":0,"message":"exists","httpStatusCode":409}' });
  });
  await page.focus('#promptBuilderCreateProjectModal .btn-primary');
  await page.keyboard.press('Enter');
  await sleep(250);
  const whileOut = await createProjectDialog();
  await page.keyboard.press('Escape');
  await sleep(250);
  const openWhileOut = (await createProjectDialog()).open;
  let afterRefusal = await createProjectDialog();
  for (let attempt = 0; attempt < 40 && (afterRefusal.createOff || !afterRefusal.error); attempt++) {
    await sleep(100);
    afterRefusal = await createProjectDialog();
  }
  await page.keyboard.press('Escape');
  await sleep(700);
  const openAfterEscape = (await createProjectDialog()).open;
  await page.unroute(projectCreation);
  assert(
    whileOut.focus === 'dialog' && whileOut.closersOff && openWhileOut,
    `a decision project created by Enter on the button: while the creation is out the focus waits on the dialog, Cancel and the X are disabled, and Escape does not close it (focus: ${whileOut.focus}; closing buttons disabled: ${whileOut.closersOff}; open: ${openWhileOut})`
  );
  assert(
    afterRefusal.open && afterRefusal.error !== '' && afterRefusal.focus === 'Create' && !openAfterEscape,
    `a decision project created by Enter on the button: after the refusal the focus is back on the button, and Escape closes the dialog (focus: ${afterRefusal.focus}; open after Escape: ${openAfterEscape})`
  );
  // Open again for the creation below.
  if (!(await page.isVisible('#promptBuilderCreateProjectModal.show'))) {
    await page.click('[data-bs-target="#promptBuilderCreateProjectModal"]');
    await page.waitForSelector('#promptBuilderCreateProjectModal.show');
  }
  // The project is created under the name of the click. The repository is
  // asked first and takes its time here; the fields are emptied meanwhile.
  const repositoryLookup = '**/modelRepository/repositories/**';
  await page.route(repositoryLookup, async (route) => {
    await sleep(900);
    await route.continue().catch(() => {});
  });
  await page.fill('#promptBuilderCreateProjectName', 'Routing Decisions');
  await page.fill('#promptBuilderCreateProjectDescription', 'decisions that route a request');
  await page.click('#promptBuilderCreateProjectModal .btn-primary');
  await sleep(250);
  const pendingFields = await page.evaluate(() => {
    const name = document.getElementById('promptBuilderCreateProjectName');
    const description = document.getElementById('promptBuilderCreateProjectDescription');
    const state = { name: name.disabled || name.readOnly, description: description.disabled || description.readOnly };
    name.value = '';
    description.value = '';
    return state;
  });
  await waitUntil(async () => (await getLog()).some((e) => e.method === 'POST' && e.url === '/modelRepository/projects'), 'project created');
  await page.unroute(repositoryLookup);
  const projectPost = (await getLog()).find((e) => e.method === 'POST' && e.url === '/modelRepository/projects');
  assert(JSON.parse(projectPost.body).tags.includes('Decision-Engineering'), 'a project made in the decision view is tagged Decision-Engineering');
  assert(
    pendingFields.name && pendingFields.description,
    `while the decision project is being created its name and description cannot be edited (name: ${pendingFields.name}, description: ${pendingFields.description})`
  );
  assert(
    JSON.parse(projectPost.body).name === 'Routing Decisions' && JSON.parse(projectPost.body).description === 'decisions that route a request',
    `the decision project is created under the name and the description of the click, also with the fields emptied while the repository is asked (name sent: "${JSON.parse(projectPost.body).name}"; description sent: "${JSON.parse(projectPost.body).description}")`
  );
  await page.waitForSelector('.modal.show', { state: 'detached' });
  await waitUntil(async () => (await projectValues()) === 'proj-2,proj-new', `the new project joins the decision view (got ${await projectValues()})`);
  await page.click('#LPB-kind-llm');
  await waitUntil(async () => (await projectValues()) === 'proj-1', 'back in the LLM view the LLM project is listed');
  await page.selectOption('#LPB-project-dropdown', 'proj-1');
  await waitUntil(async () => (await page.$$('#LPB-prompt-dropdown option')).length === 4, 'LLM prompts listed');
  await page.selectOption('#LPB-prompt-dropdown', 'model-used');
  await waitUntil(async () => (await visibleSteps()) === 'setup,build,finalize', 'LLM steps again');
  await goStep('build');
  assert(await page.isVisible('#model0'), 'the LLMs are back');
  assert(!(await page.isVisible('#app-obj-LPB-decision-state')), 'the designer is hidden again');
  assert(await page.isVisible('#app-obj-LPB-judge-model'), 'the judge card is back');
  step(true, 'switching between kinds leaves the page consistent');

  // ---- the switches of the object ------------------------------------------------
  // The mock keeps its decision projects, the template and the decision models
  // for these pages: the switch alone has to keep all of it off the page.
  const openPage = async (query) => {
    const other = await browser.newPage({ viewport: { width: 1500, height: 1100 } });
    other.on('pageerror', (e) => {
      pageErrors.push(e.message);
      console.log('[pageerror]', e.message);
    });
    await other.goto(`${APP_URL}&${query}`);
    await other.waitForSelector('#LPB-project-dropdown', { timeout: 15000 });
    return other;
  };
  const projectsOf = (target) =>
    target.$$eval('#LPB-project-dropdown option', (els) => els.map((e) => e.value).filter((v) => v.startsWith('proj')).join(','));
  const stepsOf = (target) =>
    target.$$eval('#app-obj-LPB-stepper .pb-step', (els) =>
      els.filter((e) => !e.hidden).map((e) => e.querySelector('.pb-step-button').id.replace('app-obj-LPB-step-', '')).join(',')
    );
  await resetLog();
  const offPage = await openPage('enableDecision=false');
  await waitUntil(async () => (await projectsOf(offPage)) === 'proj-1', 'the LLM project is listed with decision templates switched off');
  step(true, 'switched off, the decision projects still stay out of the project list');
  assert(
    !(await offPage.$('#LPB-kind-decision')) && !(await offPage.isVisible('#LPB-kind-options')),
    'the decision kind is not offered, and with one kind left there is no choice to make'
  );
  assert(
    !(await offPage.$('#app-obj-LPB-step-evaluate')) && !(await offPage.$('#app-obj-LPB-pane-evaluate')),
    'the Evaluate step and its pane are not built'
  );
  assert(
    !(await offPage.$('#decision-model0')) && !(await offPage.$('#app-obj-LPB-decision-state')) && !(await offPage.$('#app-obj-LPB-pet-manifest-decision')),
    'neither are the decision models, the designer and the decision manifest options'
  );
  assert((await stepsOf(offPage)) === 'setup,build,finalize', `the page has the three LLM steps (got ${await stepsOf(offPage)})`);
  const readable = (url) => {
    try {
      return decodeURIComponent(url.replace(/\+/g, ' '));
    } catch {
      return url;
    }
  };
  const askedAboutDecisionModels = (await getLog())
    .map((e) => `${e.method} ${readable(e.url)}`)
    .filter((line) => /Decision Model Project|\/projects\/dec-proj\/|\/models\/dec-\d|\/files\/files\/dopt-/.test(line));
  assert(
    askedAboutDecisionModels.length === 0,
    `and nothing is asked about the decision models (got: ${askedAboutDecisionModels.join(' | ') || 'no such request'})`
  );
  await offPage.close();

  // With RAG switched on the decision kind is the second of three cards, and
  // choosing it is a matter of the Prompt Builder alone.
  const threeKindsPage = await openPage('enableRag=true&embeddingProjectID=emb-proj');
  const kindIds = await threeKindsPage.$$eval('#LPB-kind-options .pb-kind-option input', (els) => els.map((e) => e.id).join(','));
  assert(kindIds === 'LPB-kind-llm,LPB-kind-decision,LPB-kind-rag', `with RAG switched on the Setup card offers three kinds (got ${kindIds})`);
  await threeKindsPage.click('#LPB-kind-decision');
  await waitUntil(async () => (await stepsOf(threeKindsPage)) === 'setup,build,evaluate,finalize', 'the decision steps on the page with three kinds');
  await waitUntil(async () => {
    const listed = (await projectsOf(threeKindsPage)).split(',');
    return listed.includes('proj-2') && !listed.includes('proj-1');
  }, 'the decision projects, and only those, on the page with three kinds');
  step(true, 'on the page with three kinds the decision kind shows its steps and its projects');
  assert(!(await threeKindsPage.$('#app-obj-RGB')), 'choosing the decision kind there builds nothing of the RAG Builder');
  await threeKindsPage.close();

  // ---- deleting a decision template and its project -------------------------------
  // A manifested decision template is a model a decision flow can call, like
  // an LLM prompt, so both delete dialogs say which decisions still use it:
  // the decisions with their links, that there are none, or that the check
  // failed. On a page of its own - the mock keeps its fixtures whatever a page
  // deleted, a page does not.
  const setUsage = (model, answer = {}) => fetch(BASE + '/__usage', { method: 'POST', body: JSON.stringify({ model, ...answer }) });
  const deletePage = await openPage('enableDecision=true');
  await deletePage.click('#LPB-kind-decision');
  await waitUntil(async () => (await projectsOf(deletePage)) === 'proj-2', 'the decision project is listed on the page for the deletions');
  await deletePage.selectOption('#LPB-project-dropdown', 'proj-2');
  await waitUntil(async () => (await deletePage.$$('#LPB-prompt-dropdown option')).length === 2, 'the decision template is listed there');
  await deletePage.selectOption('#LPB-prompt-dropdown', 'model-dec');
  await waitUntil(async () => !(await deletePage.isDisabled('#LPB-delete-prompt-button')), 'Delete Prompt is enabled for the decision template');
  const openDeleteDialog = async (button) => {
    await deletePage.click(button);
    await deletePage.waitForSelector('.modal.show .modal-body');
    return {
      title: (await deletePage.textContent('.modal.show .modal-title')).trim(),
      body: (await deletePage.textContent('.modal.show .modal-body')).trim(),
      links: (await deletePage.$$eval('.modal.show .modal-body a', (els) => els.map((a) => `${a.textContent} -> ${a.getAttribute('href')}`))).join(', '),
    };
  };
  const closeDeleteDialog = async (button, confirm) => {
    await deletePage.click(confirm ? '.modal.show .btn-danger' : '.modal.show .btn-secondary');
    await deletePage.waitForSelector('.modal.show', { state: 'detached' });
    if (!confirm) await waitUntil(async () => !(await deletePage.isDisabled(button)), 'the delete button is enabled again after a cancel');
  };
  const BOTH_DECISIONS = `Loan Approval Decision -> ${BASE}/SASDecisionManager/decisions/flow-A, Fraud Check Decision -> ${BASE}/SASDecisionManager/decisions/flow-B`;
  await resetLog();
  for (const [button, what, title] of [
    ['#LPB-delete-prompt-button', 'Delete Prompt', 'Delete this prompt: Ticket Routing'],
    ['#LPB-delete-project-button', 'Delete Project', 'Ticket Routing (1/1)'],
  ]) {
    await setUsage('model-dec', { flows: ['flow-A', 'flow-B'] });
    let shown = await openDeleteDialog(button);
    assert(shown.title.includes(title), `${what} on a decision template names it in the title (got: ${shown.title})`);
    assert(
      shown.body.includes('2 decision(s) currently use this prompt') && shown.links === BOTH_DECISIONS,
      `${what}: a template two decisions use lists them with their links (got: ${shown.body} / ${shown.links})`
    );
    await closeDeleteDialog(button, false);
    await setUsage('model-dec', { flows: [] });
    shown = await openDeleteDialog(button);
    assert(
      shown.body.includes('No decisions were found using this prompt.') && shown.links === '',
      `${what}: a template no decision uses says so (got: ${shown.body})`
    );
    await closeDeleteDialog(button, false);
    await setUsage('model-dec', { status: 503 });
    shown = await openDeleteDialog(button);
    assert(
      shown.body.includes('could not be verified') && shown.links === '',
      `${what}: a failed check says that the usage is unknown (got: ${shown.body})`
    );
    await closeDeleteDialog(button, false);
  }
  log = await getLog();
  assert(
    log.filter((e) => e.method === 'POST' && e.url.startsWith('/relationships/relationships') && e.body.includes('/modelRepository/models/model-dec')).length === 6,
    'every one of the six dialogs asked the relationships service about the template'
  );
  assert(!log.some((e) => e.method === 'DELETE'), 'and cancelling them deleted nothing');
  // Confirmed, with the decisions named: the template goes, then the project
  // with the template the mock still lists in it.
  await setUsage('model-dec', { flows: ['flow-A', 'flow-B'] });
  await openDeleteDialog('#LPB-delete-prompt-button');
  await closeDeleteDialog('#LPB-delete-prompt-button', true);
  await waitUntil(async () => (await getLog()).some((e) => e.method === 'DELETE' && e.url === '/modelRepository/models/model-dec'), 'the decision template is deleted in Model Manager');
  await waitUntil(async () => (await deletePage.$$('#LPB-prompt-dropdown option[value="model-dec"]')).length === 0, 'the deleted template leaves the list');
  step(true, 'a confirmed Delete Prompt deletes the decision template');
  await resetLog();
  const projectDialog = await openDeleteDialog('#LPB-delete-project-button');
  assert(projectDialog.links === BOTH_DECISIONS, `the project deletion names the decisions again before it deletes (got: ${projectDialog.links})`);
  await closeDeleteDialog('#LPB-delete-project-button', true);
  await waitUntil(async () => (await getLog()).some((e) => e.method === 'DELETE' && e.url === '/modelRepository/projects/proj-2'), 'the decision project is deleted in Model Manager');
  const decisionDeletes = (await getLog()).filter((e) => e.method === 'DELETE').map((e) => e.url).join(', ');
  assert(
    decisionDeletes === '/modelRepository/models/model-dec, /modelRepository/projects/proj-2',
    `the template is deleted first, its project last (got: ${decisionDeletes})`
  );
  await waitUntil(async () => (await projectsOf(deletePage)) === '', 'the deleted project leaves the list');
  step(true, 'a confirmed Delete Project deletes the decision project');
  await setUsage();
  await deletePage.close();

  // ---- every request to SAS Viya went through the HTTP client of the app -------
  const directRequests = await (await fetch(BASE + '/__direct')).json();
  assert(
    directRequests.length === 0,
    `no request to a SAS Viya service went around the HTTP client, which handles HTTP 449 and the CSRF token (direct: ${directRequests.join(' | ') || 'none'})`
  );

  assert(pageErrors.length === 0, `no uncaught page errors (got: ${pageErrors.join(' | ')})`);
  await browser.close();
  console.log('\n===== SUMMARY =====');
  console.log(results.join('\n'));
  console.log(`${results.filter((r) => r.startsWith('OK')).length} checks passed, ${results.filter((r) => r.startsWith('FAIL')).length} failed`);
})().catch((e) => {
  console.error('VERIFY FAILED:', e.message);
  process.exit(1);
});
