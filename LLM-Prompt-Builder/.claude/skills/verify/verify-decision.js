/**
 * Drives the built Prompt Builder (served by mock-server.js) through the
 * decision mode: a decision template is opened, its saved run and designer
 * restored, a run made against a decision model, a question added, the runs
 * saved, the evaluation run over the saved runs and over a CAS table, the
 * template manifested with typed outputs, and the page switched back to an
 * LLM prompt. Run against a FRESH mock instance.
 *
 * The mock is found at VERIFY_BASE (default http://localhost:4173). Every
 * score code a manifest uploads is compiled and run with a real Python
 * interpreter (see python-check.cjs, which has to sit next to this file).
 */
const { chromium } = require('playwright');
const { resolvePython, compilePython, callScoreModel } = require('./python-check.cjs');

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
    page.$$eval('.pb-step', (els) =>
      els.filter((e) => !e.hidden).map((e) => e.querySelector('.pb-step-button').id.replace('app-obj-LPB-step-', '')).join(',')
    );
  const stepNumbers = () =>
    page.$$eval('.pb-step', (els) => els.filter((e) => !e.hidden).map((e) => e.querySelector('.pb-step-marker').textContent).join(','));

  // Ask the mock for its decision fixtures (a second project holding the decision template).
  await fetch(BASE + '/__decision', { method: 'POST' });
  const APP_URL = `${BASE}/?modelRepositoryID=repo-1&llmProjectID=llm-proj&SCREndpoint=${BASE}/scr`;
  await page.goto(APP_URL);
  await page.waitForSelector('#LPB-project-dropdown', { timeout: 15000 });
  step(true, 'app booted against the mock');

  // ---- an LLM page: the Evaluate step is there but hidden ---------------------
  assert((await visibleSteps()) === 'setup,build,finalize', `LLM mode shows three steps (got ${await visibleSteps()})`);
  assert(await page.$('#app-obj-LPB-step-evaluate'), 'the Evaluate step exists in the row');
  assert((await page.$$('.pb-kind-option')).length === 2, 'the Setup card offers the two kinds of template');
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
  const bestLabel = await page.textContent('label[for="best-prompt-0-jev_mock"]');
  assert(bestLabel.includes('Correct'), `the checkbox reads Correct in decision mode (got ${bestLabel})`);
  assert((await page.$$('#app-obj-LPB-pet-0 .pb-prob-line')).length >= 4, 'probability bars are drawn per option');
  const legendText = await page.textContent('#app-obj-LPB-pet-legend');
  assert(legendText.includes('Correct') && !legendText.includes('Judge the run'), 'the legend reads for a decision template');

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
  await resetLog();
  await page.click('#app-obj-LPB-evaluate-run');
  await waitUntil(async () => (await page.$$('#app-obj-LPB-evaluate-results .pb-evaluation')).length >= 1, 'CAS evaluation rendered', 15000);
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

  // ---- back to an LLM prompt: everything switches back --------------------------------
  await goStep('setup');
  // A project created in the decision view carries the decision tag.
  await resetLog();
  await page.click('[data-bs-target="#promptBuilderCreateProjectModal"]');
  await page.waitForSelector('#promptBuilderCreateProjectModal.show');
  await page.fill('#promptBuilderCreateProjectName', 'Routing Decisions');
  await page.click('#promptBuilderCreateProjectModal .btn-primary');
  await waitUntil(async () => (await getLog()).some((e) => e.method === 'POST' && e.url === '/modelRepository/projects'), 'project created');
  const projectPost = (await getLog()).find((e) => e.method === 'POST' && e.url === '/modelRepository/projects');
  assert(JSON.parse(projectPost.body).tags.includes('Decision-Engineering'), 'a project made in the decision view is tagged Decision-Engineering');
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

  assert(pageErrors.length === 0, `no uncaught page errors (got: ${pageErrors.join(' | ')})`);
  await browser.close();
  console.log('\n===== SUMMARY =====');
  console.log(results.join('\n'));
  console.log(`${results.filter((r) => r.startsWith('OK')).length} checks passed, ${results.filter((r) => r.startsWith('FAIL')).length} failed`);
})().catch((e) => {
  console.error('VERIFY FAILED:', e.message);
  process.exit(1);
});
