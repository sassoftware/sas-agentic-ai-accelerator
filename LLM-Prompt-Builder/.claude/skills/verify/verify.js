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
const crypto = require('crypto');
const { chromium } = require('playwright');
const fetch = require('./mock-fetch.cjs');
const { trackPagesOf, quiet, dialogShown, clickInDialog, dialogsGone, gate } = require('./page-waits.cjs');
const { resolvePython, compilePython, callScoreModel, withoutTlsDefault, expectedTlsVerify, tlsVerifyOf } = require('./python-check.cjs');

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
// A score code names the SCR endpoint of the page, and so the port of the
// mock; with that replaced its hash is the same on every port.
const sha256 = (text) => crypto.createHash('sha256').update(text, 'utf8').digest('hex');
const portless = (code) => code.split(BASE).join('http://mock');
// The integrated score code of the run this suite manifests (variables
// customer and amount, demo_llm), as the bundle of commit ccae261 wrote it -
// the last one before the option "Verify TLS certificates in manifested
// models". Recorded by running this suite against that bundle.
const INTEGRATED_CODE_BEFORE_THE_TLS_OPTION = '009acb271cff755ba2cdeb39e1e8bf67a400c070fc652592c0ec478e39d27a11';
// The text a link shows when it is left alone. It is never read from the link
// at the moment of a check: a check before may have left the "link copied"
// note in it, and a check that took the note for the text of the link would
// pass on a link that shows the note for good. The links to a saved
// prompt-test show their own address, exactly as their href attribute holds
// it; a link with a label of its own is given that label by the test (`own`).
const ownLinkText = async (target, selector, own) => own ?? (await target.$eval(selector, (link) => link.getAttribute('href')));
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
// What a link that leaves the page does when no tab can be opened: it shows
// for a moment that the link was copied. `window.open` is made to answer the
// way the sandboxed iframe of a report does.
async function assertLinkCopies(target, selector, label, own) {
  await target.evaluate(() => {
    window.__verifyOpened = [];
    window.__verifyRealOpen = window.__verifyRealOpen || window.open;
    window.open = (url) => {
      window.__verifyOpened.push(String(url));
      return null;
    };
  });
  const href = await target.$eval(selector, (link) => link.href);
  const shown = await ownLinkText(target, selector, own);
  const tabs = target.context().pages().length;
  await target.click(selector);
  await waitUntil(async () => /link copied/i.test(await target.textContent(selector)), `${label}: says that the link was copied when no tab can be opened`, 60000);
  const opened = await target.evaluate(() => window.__verifyOpened);
  assert(
    opened.length === 1 && opened[0] === href && target.context().pages().length === tabs,
    `${label}: asks for a new tab once, through window.open, and follows no plain target="_blank" link (asked for: ${opened.join(', ') || 'nothing'})`
  );
  await waitUntil(async () => (await target.textContent(selector)).trim() === shown, `${label}: shows its text again afterwards`, 60000);
  await target.evaluate(() => {
    window.open = window.__verifyRealOpen;
  });
}
// The same link where a tab CAN open - the job URL in a browser tab, or a
// report whose sandbox allows popups. `window.open` is made to answer the way
// a browser does there: with a window, and with null when the features hold
// "noopener" (the specification asks for null then, although the tab opens).
// The link has to open its tab and do nothing else: no note that the link was
// copied, nothing written to the clipboard, and the new window cut off from
// this page.
async function assertLinkOpensTab(target, selector, label, own) {
  await target.evaluate(() => {
    window.__verifyOpened = [];
    window.__verifyCopied = [];
    window.__verifyTab = { opener: window };
    window.__verifyRealOpen = window.__verifyRealOpen || window.open;
    window.open = (url, name, features) => {
      window.__verifyOpened.push({ url: String(url), features: features === undefined ? '' : String(features) });
      return /noopener/i.test(String(features || '')) ? null : window.__verifyTab;
    };
    window.__verifyRealExec = window.__verifyRealExec || document.execCommand;
    document.execCommand = (command) => {
      if (command === 'copy') window.__verifyCopied.push('execCommand');
      return true;
    };
    if (navigator.clipboard) {
      window.__verifyRealWrite = window.__verifyRealWrite || navigator.clipboard.writeText;
      navigator.clipboard.writeText = (text) => {
        window.__verifyCopied.push(String(text));
        return Promise.resolve();
      };
    }
  });
  const href = await target.$eval(selector, (link) => link.href);
  const shown = await ownLinkText(target, selector, own);
  await target.click(selector);
  // The click asks for the tab, or does not, before it returns.
  await target.waitForFunction(() => window.__verifyOpened.length > 0, null, { timeout: 30000 }).catch(() => {});
  const seen = await target.evaluate(() => ({
    opened: window.__verifyOpened,
    copied: window.__verifyCopied,
    cutOff: window.__verifyTab.opener === null,
  }));
  const text = (await target.textContent(selector)).trim();
  await target.evaluate(() => {
    window.open = window.__verifyRealOpen;
    document.execCommand = window.__verifyRealExec;
    if (navigator.clipboard) navigator.clipboard.writeText = window.__verifyRealWrite;
  });
  assert(
    seen.opened.length === 1 && seen.opened[0].url === href && !/noopener/i.test(seen.opened[0].features),
    `${label}: asks for a new tab once, without the "noopener" feature that makes window.open answer null where the tab did open (features: ${seen.opened.map((call) => call.features || 'none').join(' | ') || 'not asked'})`
  );
  assert(
    text === shown && seen.copied.length === 0,
    `${label}: where the tab opens, the link keeps its text and nothing is copied over the clipboard (text: ${text}; copied: ${seen.copied.join(', ') || 'nothing'})`
  );
  assert(seen.cutOff, `${label}: the tab that opened is cut off from this page - its opener is cleared`);
}
// And with nothing stubbed: a real click on a page where a tab can open. The
// tab asks for a page of SAS Model Manager, which the mock does not serve and
// would list under GET /__direct; the browser context answers it instead, and
// reads the Referer the tab was sent with. SAS Model Manager is on the origin
// of the page, and the referrer policy of the page ("same-origin") keeps the
// Referer for it.
async function assertLinkOpensRealTab(target, selector, label, own) {
  const context = target.context();
  const otherApplication = '**/SASModelManager/**';
  let referer = null;
  await context.route(otherApplication, (route) => {
    if (referer === null) referer = route.request().headers().referer || '';
    return route.fulfill({ status: 200, contentType: 'text/html', body: '<title>SAS Model Manager</title>' });
  });
  const href = await target.$eval(selector, (link) => link.href);
  const shown = await ownLinkText(target, selector, own);
  const [tab] = await Promise.all([context.waitForEvent('page', { timeout: 10000 }), target.click(selector)]);
  await tab.waitForLoadState('domcontentloaded');
  const tabUrl = tab.url();
  const cutOff = await tab.evaluate(() => window.opener === null);
  // The click decided on the note before it returned; the tab has loaded since.
  const text = (await target.textContent(selector)).trim();
  await tab.close();
  await context.unroute(otherApplication);
  await target.bringToFront();
  assert(tabUrl === href && cutOff, `${label}: a real click opens the link in a new tab that has no opener (tab: ${tabUrl}, opener cleared: ${cutOff})`);
  assert(text === shown, `${label}: and the page that opened the tab does not claim that popups are blocked (text: ${text})`);
  assert(
    String(referer).startsWith(`${BASE}/`),
    `${label}: SAS Model Manager is on the origin of the page, so its tab is still told where the click came from (Referer: ${referer || 'none'})`
  );
}
// A link to ANOTHER site - the user guide. Its tab must not be told where the
// click came from: the Referer would name the SAS Viya host to a third party.
// The link is opened through window.open without "noreferrer" (with it the
// answer is null where the tab did open), so it is the referrer policy of the
// page that keeps the Referer back. A real click with nothing stubbed; the
// browser context answers the request of the tab, so that nothing leaves the
// machine, and reads its headers.
async function assertLinkTellsAnotherSiteNothing(target, selector, label) {
  const context = target.context();
  const href = await target.$eval(selector, (link) => link.href);
  const site = `${new URL(href).origin}/**`;
  const requests = [];
  await context.route(site, (route) => {
    requests.push({ url: route.request().url(), referer: route.request().headers().referer || '' });
    return route.fulfill({ status: 200, contentType: 'text/html', body: '<title>user guide</title>' });
  });
  const [tab] = await Promise.all([context.waitForEvent('page', { timeout: 10000 }), target.click(selector)]);
  await tab.waitForLoadState('domcontentloaded');
  const inTab = await tab.evaluate(() => ({ referrer: document.referrer, cutOff: window.opener === null }));
  await tab.close();
  await context.unroute(site);
  await target.bringToFront();
  assert(
    new URL(href).origin !== new URL(BASE).origin && requests.length >= 1 && requests[0].url === href.split('#')[0] && inTab.cutOff,
    `${label}: a real click opens the link to the other site in a new tab that has no opener (asked for: ${requests.map((request) => request.url).join(', ') || 'nothing'}; opener cleared: ${inTab.cutOff})`
  );
  assert(
    requests.length >= 1 && requests.every((request) => request.referer === '') && inTab.referrer === '',
    `${label}: the other site is not told where the click came from - no Referer in the request of the tab, none in its document (Referer: ${requests.map((request) => request.referer || 'none').join(', ') || 'no request'}; document.referrer: ${inTab.referrer || 'empty'})`
  );
  const rel = String(await target.getAttribute(selector, 'rel')).split(/\s+/);
  assert(
    rel.includes('noreferrer') && rel.includes('noopener'),
    `${label}: opened without the click handler (the middle mouse button, "Open link in new tab"), the plain link says rel="noopener noreferrer" (got: ${rel.join(' ')})`
  );
}
// Two clicks in a row where no tab can open. Nothing visible happens on the
// first click but the note, so a second one is likely - and the link must
// still get its own text back, not the note for good.
async function assertLinkTextSurvivesSecondClick(target, selector, label, own) {
  await target.evaluate(() => {
    window.__verifyRealOpen = window.__verifyRealOpen || window.open;
    window.open = () => null;
  });
  const shown = await ownLinkText(target, selector, own);
  await target.click(selector);
  // The second click comes while the note of the first one is shown.
  await waitUntil(async () => (await target.textContent(selector)).trim() !== shown, `${label}: the note of the first click is shown`, 30000).catch(() => {});
  await target.click(selector);
  // The second click starts the 2.5 s of its note anew; then the link has its
  // own text back - not the note for good.
  await waitUntil(async () => (await target.textContent(selector)).trim() === shown, `${label}: the link shows its own text again`, 30000).catch(() => {});
  const text = (await target.textContent(selector)).trim();
  await target.evaluate(() => {
    window.open = window.__verifyRealOpen;
  });
  assert(
    !/link copied/i.test(shown) && text === shown,
    `${label}: after two clicks in a row the link shows its own text again, not the note for good (own text: ${shown}; got: ${text})`
  );
}
// A create dialog that is open: each field is its label above an input as
// wide as the dialog body.
async function assertCreateDialogLayout(target, modalSelector, label) {
  const fields = await target.$$eval(`${modalSelector} .modal-body input`, (inputs) =>
    inputs.map((input) => {
      const body = input.closest('.modal-body');
      const caption = body.querySelector(`label[for="${input.id}"]`);
      const style = getComputedStyle(body);
      const inner = body.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
      return {
        id: input.id,
        width: Math.round(input.getBoundingClientRect().width),
        inner: Math.round(inner),
        labelled: Boolean(caption),
        above: Boolean(caption) && caption.getBoundingClientRect().bottom <= input.getBoundingClientRect().top + 1,
      };
    })
  );
  assert(
    fields.length === 2 && fields.every((field) => field.labelled && field.above && field.width >= field.inner - 2),
    `${label}: name and description each have their label above an input as wide as the dialog (got: ${fields.map((field) => `${field.id} ${field.width}px of ${field.inner}px, label ${field.labelled ? (field.above ? 'above' : 'beside') : 'missing'}`).join('; ')})`
  );
}

(async () => {
  // Without an interpreter the generated score code would go unchecked: a failure, not a skip.
  const python = resolvePython();
  assert(python, `a Python interpreter is available for the score code checks (${python ? `${python.command} ${python.args.join(' ')} = ${python.version}` : 'none found - set VERIFY_PYTHON'})`);

  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  trackPagesOf(browser);
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
  // The user guide link of this step is the one link that leaves SAS Viya.
  const referrerPolicy = await optimizePage.evaluate(() => (document.querySelector('meta[name="referrer"]') || {}).content || '');
  assert(
    referrerPolicy === 'same-origin',
    `the page carries the referrer policy "same-origin": what it asks of another site does not name the SAS Viya host (got: ${referrerPolicy || 'no meta element'})`
  );
  await assertLinkTellsAnotherSiteNothing(
    optimizePage,
    '#app-obj-LPB-pane-optimize a[href^="https://sassoftware.github.io/"]',
    'the user guide link of the Optimize step'
  );
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
  await dialogShown(page, '.modal.show');
  const createModalBody = await page.textContent('.modal.show .modal-body');
  assert(
    createModalBody.includes('saved into the currently selected project'),
    'create-prompt explanation moved into the modal body'
  );
  assert(
    (await page.textContent('.modal.show .modal-title')).trim() === 'Create a new Prompt',
    'create-prompt button/title shortened'
  );
  await assertCreateDialogLayout(page, '#promptBuilderCreatePromptModal', 'Create a new Prompt');
  await page.click('.modal.show .btn-close');
  await dialogsGone(page);
  await page.click('#LPB-modal-button-container button[data-bs-target="#promptBuilderCreateProjectModal"]');
  await dialogShown(page, '#promptBuilderCreateProjectModal');
  await assertCreateDialogLayout(page, '#promptBuilderCreateProjectModal', 'Create a new Project');
  await page.click('.modal.show .btn-close');
  await dialogsGone(page);

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
  // A variable becomes an input of the manifested model: a Python keyword and
  // the names the score code assigns before it reads its inputs are refused,
  // with the reason. Python tells the case apart, and a name the code only
  // assigns after reading its inputs works, so neither of those is refused.
  const variableNameState = async (name) => {
    await varRows.nth(2).locator('.pb-var-name').fill(name);
    return varRows.nth(2).evaluate((row) => ({
      invalid: row.querySelector('.pb-var-name').classList.contains('is-invalid'),
      reason: row.querySelector('.pb-var-name').nextElementSibling.textContent,
    }));
  };
  for (const name of ['options', 'class', 'llm', 'json', 'API_KEY', 'sslVerifyDefault']) {
    const state = await variableNameState(name);
    assert(state.invalid && state.reason.includes('score code'), `a variable named ${name} is refused with the reason (got: ${JSON.stringify(state)})`);
  }
  for (const name of ['Options', 'response', 'state']) {
    const state = await variableNameState(name);
    assert(!state.invalid, `a variable named ${name} is accepted (got: ${JSON.stringify(state)})`);
  }
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
  await waitUntil(async () => !(await page.isDisabled('#app-obj-LPB-pet-create-model-button')), 'the manifest has ended');
  await assertLinkCopies(page, '#app-obj-LPB-pet-save-result a', 'the link to the saved prompt-test in the manifest box');
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
  assert(
    !/sslVerify|LLMCONTAINERSSLVERIFY|requests/.test(scoreCodeOf(pyBody)),
    'the Call-LLM-node score code makes no call and holds nothing about TLS verification'
  );
  assertCompiles(scoreCodeOf(pyBody), 'the Call-LLM-node score code');
  const tagPut = aLog.find((e) => e.method === 'PUT' && e.url === '/modelRepository/models/model-used');
  assert(tagPut, 'manifest PUTs the model to update its tags');
  assert(tagPut.ifMatch === '"abc123"', `tag update sends the model ETag as If-Match (got: ${tagPut.ifMatch})`);
  const putTags = JSON.parse(tagPut.body).tags;
  assert(
    JSON.stringify(putTags) === JSON.stringify(['LLM', 'Prompt-Template', 'Custom-Tag', 'demo_llm']),
    `LLM tag added, stale manifest tags (other_llm, LLM-Call-Included, TLS-Verification-Off) removed, custom tags kept (got: ${putTags})`
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

  // ---- TLS verification in the manifested model: the option ticked ------------
  // This page says nothing about the option, so it is ticked. The score code
  // is then what it was before the option existed plus six lines, and it
  // behaves as before for every value of LLMCONTAINERSSLVERIFY.
  const TLS_WARNING = '#app-obj-LPB-pet-manifest-tls-warning';
  const TLS_TAG = 'TLS-Verification-Off';
  const LLM_ANSWER = { data: { response: 'fine', run_time: 1, prompt_length: 2, output_length: 3 } };
  const describeVerify = (values) => values.map((value) => JSON.stringify(value)).join(', ');
  const assertTlsBehaviour = (code, args, verifyByDefault, label) => {
    for (const bundleExists of [false, true]) {
      const got = tlsVerifyOf(code, args, LLM_ANSWER, bundleExists);
      const expected = expectedTlsVerify(verifyByDefault, bundleExists);
      assert(
        JSON.stringify(got) === JSON.stringify(expected),
        `${label}, ${bundleExists ? 'with' : 'without'} a CA bundle: requests.post gets verify = ${describeVerify(expected)} for LLMCONTAINERSSLVERIFY unset, "false", "true" and "garbage" (got: ${describeVerify(got)})`
      );
    }
  };
  const integratedCode = scoreCodeOf(pyIntegrated);
  const integratedBefore = withoutTlsDefault(integratedCode, true);
  assert(
    integratedBefore.problem === '',
    `option ticked: the integrated score code holds the six lines of the TLS default, with sslVerifyDefault = True (${integratedBefore.problem || 'as documented'})`
  );
  assert(
    sha256(portless(integratedBefore.code)) === INTEGRATED_CODE_BEFORE_THE_TLS_OPTION,
    `option ticked: without those six lines the integrated score code is byte for byte what it was before the option (sha256 ${sha256(portless(integratedBefore.code))})`
  );
  assertTlsBehaviour(integratedCode, ['ACME Corp', 42], true, 'option ticked');
  const integratedTags = JSON.parse(aLog.find((e) => e.method === 'PUT' && e.url === '/modelRepository/models/model-used').body).tags;
  assert(
    integratedTags.includes('LLM-Call-Included') && !integratedTags.includes(TLS_TAG),
    `option ticked: the model is tagged LLM-Call-Included and not ${TLS_TAG}, which an earlier manifest left on it (got: ${integratedTags})`
  );
  assert(!(await page.isVisible(TLS_WARNING)), 'option ticked: the Finalize step shows no TLS warning');

  // ---- TLS verification in the manifested model: the option unticked ----------
  // A page of its own with sslVerify=false, on the saved runs of the fixture.
  const tlsPage = await browser.newPage({ viewport: { width: 1500, height: 1100 } });
  const tlsPageErrors = [];
  tlsPage.on('pageerror', (e) => tlsPageErrors.push(e.message));
  await tlsPage.goto(`${APP_URL}&sslVerify=false`);
  await tlsPage.waitForSelector('#LPB-project-dropdown', { timeout: 15000 });
  await tlsPage.selectOption('#LPB-project-dropdown', 'proj-1');
  await waitUntil(async () => (await tlsPage.$$('#LPB-prompt-dropdown option')).length === 4, 'prompts listed on the page with the option unticked');
  await tlsPage.selectOption('#LPB-prompt-dropdown', 'model-used');
  await waitUntil(async () => (await tlsPage.$$('.pet-run-delete')).length === 3, 'the saved runs are loaded on the page with the option unticked');
  await tlsPage.click('#app-obj-LPB-step-finalize');
  await tlsPage.waitForSelector('#app-obj-LPB-pane-finalize:not([hidden])');
  const manifestOn = async (target, wanted) => {
    await resetLog();
    await target.click('#app-obj-LPB-pet-create-model-button');
    await waitUntil(async () => (await getLog()).some((e) => e.method === 'POST' && e.body.includes('def scoreModel(')), `${wanted}: score code uploaded`);
    await waitUntil(async () => (await getLog()).some((e) => e.method === 'PUT' && e.url === '/modelRepository/models/model-used'), `${wanted}: tags written`);
    await waitUntil(async () => !(await target.isDisabled('#app-obj-LPB-pet-create-model-button')), `${wanted}: the manifest has ended`);
    const entries = await getLog();
    return {
      code: scoreCodeOf(entries.find((e) => e.method === 'POST' && e.body.includes('def scoreModel(')).body),
      // The tags are written first, the attributes of the LLM after them.
      tags: JSON.parse(entries.find((e) => e.method === 'PUT' && e.url === '/modelRepository/models/model-used').body).tags,
    };
  };
  // The Call LLM node variant makes no call: the option changes nothing there.
  await tlsPage.uncheck('#app-obj-LPB-pet-manifest-integrated');
  assert(!(await tlsPage.isVisible(TLS_WARNING)), 'option unticked, Call LLM node variant: no TLS warning, because the model makes no call');
  const nodeOff = await manifestOn(tlsPage, 'option unticked, Call LLM node variant');
  assert(
    !/sslVerify|LLMCONTAINERSSLVERIFY/.test(nodeOff.code) && !nodeOff.tags.includes(TLS_TAG),
    `option unticked, Call LLM node variant: nothing about TLS in the score code and no ${TLS_TAG} tag (tags: ${nodeOff.tags})`
  );
  // With the LLM call included the warning is there before the click.
  await tlsPage.check('#app-obj-LPB-pet-manifest-integrated');
  assert(await tlsPage.isVisible(TLS_WARNING), 'option unticked, LLM call included: the Finalize step shows a TLS warning before the click');
  const tlsWarningText = (await tlsPage.textContent(TLS_WARNING)).trim();
  assert(
    tlsWarningText.includes('Verify TLS certificates in manifested models') && tlsWarningText.includes('LLMCONTAINERSSLVERIFY=true') && tlsWarningText.includes(TLS_TAG),
    `the warning names the option, the tag and the environment variable that turns verification back on (got: ${tlsWarningText})`
  );
  const warningAboveButton = await tlsPage.evaluate(
    ([warning, button]) => document.querySelector(warning).getBoundingClientRect().bottom <= document.querySelector(button).getBoundingClientRect().top,
    [TLS_WARNING, '#app-obj-LPB-pet-create-model-button']
  );
  assert(warningAboveButton, 'the warning stands above the Manifest button');
  const integratedOff = await manifestOn(tlsPage, 'option unticked, LLM call included');
  const offBefore = withoutTlsDefault(integratedOff.code, false);
  assert(
    offBefore.problem === '',
    `option unticked: the integrated score code holds the same six lines with sslVerifyDefault = False (${offBefore.problem || 'as documented'})`
  );
  const tlsLinesOf = (code) => code.slice(code.indexOf('    # TLS verification'), code.indexOf('    # Call the LLM container'));
  assert(
    tlsLinesOf(integratedOff.code).length > 400 && tlsLinesOf(integratedOff.code) === tlsLinesOf(integratedCode).replace('sslVerifyDefault = True', 'sslVerifyDefault = False'),
    'option unticked: the TLS lines differ from those of a ticked manifest in the value of sslVerifyDefault and in nothing else'
  );
  assertCompiles(integratedOff.code, 'the integrated score code manifested with the option unticked');
  const offArguments = /def scoreModel\(([^)]*)\)/.exec(integratedOff.code)[1].split(',').map((name) => name.trim()).filter(Boolean).map(() => 'x');
  assertTlsBehaviour(integratedOff.code, offArguments, false, 'option unticked');
  assert(
    integratedOff.tags.includes(TLS_TAG) && integratedOff.tags.includes('LLM-Call-Included'),
    `option unticked: the manifested model carries the tag ${TLS_TAG} (got: ${integratedOff.tags})`
  );
  assert(!(await tlsPage.$('.pet-manifest-tls-tag-failed')), 'the tag was written, so the manifest box does not ask for it');
  // The page itself does what it does with the option ticked: an LLM run
  // calls the container the same way.
  await tlsPage.click('#app-obj-LPB-step-build');
  await tlsPage.waitForSelector('#app-obj-LPB-pane-build:not([hidden])');
  await resetLog();
  await tlsPage.click('#app-obj-LPB-run-experiment');
  await waitUntil(async () => (await tlsPage.$$('.pet-run-delete')).length === 4, 'a run on the page with the option unticked');
  const offRunCalls = (await getLog()).filter((e) => e.method === 'POST' && e.url.startsWith('/scr/'));
  assert(
    offRunCalls.length >= 1 && offRunCalls.every((e) => e.status === 200 && !/sslVerify|verify/i.test(e.url + e.body)),
    `option unticked: a run in the browser calls the model container as before, and nothing of the option travels with it (${offRunCalls.length} call(s))`
  );
  assert(tlsPageErrors.length === 0, `no uncaught error on the page with the option unticked (got: ${tlsPageErrors.join(' | ')})`);
  await tlsPage.close();

  // ---- the tag follows what was stored, and a failed tag update is said ---------
  // TLS-Verification-Off is what an administrator filters on. It has to say
  // what the model HOLDS: a score code that was not stored changes no tag and
  // is no manifest, and a tag update that did not go through is reported -
  // when the tag was to be written, and when it was to be taken away. The
  // fixture model carries the tag, left by an earlier manifest.
  const MANIFEST_BUTTON = '#app-obj-LPB-pet-create-model-button';
  const MANIFEST_BOX = '#app-obj-LPB-pet-save-result';
  const SAVE_BUTTON = '#app-obj-LPB-pet-save-button';
  // What a write without a usable answer says once it was read back and SAS
  // Model Manager does not hold what it wrote.
  const NOT_CARRIED_OUT = 'A request to SAS Viya got no usable answer, and reading back what SAS Viya holds shows that it was not carried out.';
  // What an ingress answers in place of the service: no JSON, so the page throws.
  const errorPage = { status: 502, contentType: 'text/html', body: '<html><head><title>502 Bad Gateway</title></head><body><center><h1>502 Bad Gateway</h1></center></body></html>' };
  const faultyManifest = async (url, fault, label) => {
    const target = await browser.newPage({ viewport: { width: 1500, height: 1100 } });
    const errors = [];
    target.on('pageerror', (e) => errors.push(e.message));
    await target.goto(url);
    await target.waitForSelector('#LPB-project-dropdown', { timeout: 15000 });
    await target.selectOption('#LPB-project-dropdown', 'proj-1');
    await waitUntil(async () => (await target.$$('#LPB-prompt-dropdown option')).length === 4, `${label}: prompts listed`);
    await target.selectOption('#LPB-prompt-dropdown', 'model-used');
    await waitUntil(async () => (await target.$$('.pet-run-delete')).length === 3, `${label}: the saved runs are loaded`);
    await target.click('#app-obj-LPB-step-finalize');
    await target.waitForSelector('#app-obj-LPB-pane-finalize:not([hidden])');
    await target.check('#app-obj-LPB-pet-manifest-integrated');
    let scoreCodeSeen = false;
    let injected = 0;
    // After the upload of the score code, nothing of the model can be read.
    let readsFail = false;
    // The mock keeps no file of a fixture model: the score code the service
    // took is listed and served here, as SAS Model Manager would.
    let storedScore = null;
    await target.route('**/files/files/score-taken/content', (route) => route.fulfill({ status: 200, contentType: 'text/x-python', body: storedScore ? storedScore.text : '' }));
    await target.route('**/modelRepository/models/model-used/contents?*', async (route) => {
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
        // and the answer never reached the page; the page reads it back -
        // or cannot, when nothing of the model can be read after it.
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
      if (fault === 'tracker, no network' && uploadOf('Prompt-Experiment-Tracker.json')) {
        injected += 1;
        return route.abort('failed');
      }
      if (fault === 'tracker, refused' && uploadOf('Prompt-Experiment-Tracker.json')) {
        injected += 1;
        return route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ errorCode: 1003, message: 'Forbidden', httpStatusCode: 403 }) });
      }
      if (fault === 'requirements.json, error page' && uploadOf('requirements.json')) {
        injected += 1;
        return route.fulfill({ status: 502, contentType: 'text/html', body: '<html><head><title>502 Bad Gateway</title></head><body><center><h1>502 Bad Gateway</h1></center></body></html>' });
      }
      if (fault === 'requirements.json, no network' && uploadOf('requirements.json')) {
        injected += 1;
        return route.abort('failed');
      }
      if (fault === 'outputVar.json, error page' && uploadOf('outputVar.json')) {
        injected += 1;
        return route.fulfill({ status: 502, contentType: 'text/html', body: '<html><head><title>502 Bad Gateway</title></head><body><center><h1>502 Bad Gateway</h1></center></body></html>' });
      }
      return route.continue();
    });
    // The first write of the model after the score code is the tag update.
    await target.route('**/modelRepository/models/model-used', (route) => {
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
    await settles(async () => !(await target.isDisabled(SAVE_BUTTON)) && (await target.textContent(SAVE_BUTTON)).trim() === 'Save Experiments', 60000);
    await quiet(target);
    const entries = await getLog();
    const modelWrite = entries.find((e) => e.method === 'PUT' && e.url === '/modelRepository/models/model-used');
    const toasts = await target.$$eval('.toast-body', (els) => els.map((e) => e.textContent || ''));
    const outcome = {
      // The tags of the first write of the model, which is the tag update.
      tags: modelWrite ? JSON.parse(modelWrite.body).tags || [] : null,
      scoreCodes: entries.filter((e) => e.method === 'POST' && e.body.includes('def scoreModel(')).length,
      // What the service took and stored, whatever the page was told.
      scoreCodesStored: entries.filter((e) => e.method === 'POST' && e.body.includes('def scoreModel(') && e.status < 300).length,
      modelWrites: entries.filter((e) => e.method === 'PUT' && e.url === '/modelRepository/models/model-used').length,
      // The first requests of the manifest itself, after the save.
      manifestRequests: entries.filter((e) => (e.method === 'GET' && /\/models\/model-used\/variables/.test(e.url)) || (e.method === 'POST' && e.body.includes('filename="inputVar.json"'))).length,
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
    notStored.modelWrites === 0,
    `a score code SAS Model Manager did not store changes no tag: the model is not written, and keeps ${TLS_TAG} (writes of the model: ${notStored.modelWrites})`
  );
  assert(
    notStored.manifestedToasts === 0 && notStored.notManifested.length === 1 && notStored.notManifested[0].startsWith('Not manifested: the score code could not be stored'),
    `and it is reported as a manifest that failed, beside the button, with no toast saying "manifested" (box: ${notStored.notManifested.join(' | ') || 'nothing'}; toasts: ${notStored.manifestedToasts})`
  );
  assert(notStored.tagLines.length === 0 && notStored.errors.length === 0, `nothing is said about a tag update that was never tried, and no error is thrown (got: ${[...notStored.tagLines, ...notStored.errors].join(' | ') || 'nothing'})`);
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

  // ---- a request that throws while the manifest runs -----------------------------
  // A refusal with a JSON body comes back as a status. An answer that is no
  // JSON (the 502 page of an ingress) and a request that gets no answer THROW
  // in the page, and the manifest used to end where it stood: the button
  // stayed disabled on "Saving..." for good, nothing was said, and - after
  // the score code - the tags were never updated, so TLS-Verification-Off did
  // not say what the stored score code does. The model carries the tag here.
  const describe = (outcome) =>
    `tags: ${outcome.tags ? outcome.tags.join(', ') : 'not written'}; box: ${outcome.notManifested.join(' | ') || 'nothing'}; toasts: ${outcome.manifestedToasts}; errors: ${outcome.errors.join(' | ') || 'none'}`;
  const errorPageAfter = await faultyManifest(APP_URL, 'requirements.json, error page', 'option ticked, requirements.json answered by an error page');
  assert(
    errorPageAfter.scoreCodes === 1 && errorPageAfter.tags !== null && !errorPageAfter.tags.includes(TLS_TAG) && errorPageAfter.tags.includes('LLM-Call-Included'),
    `option ticked: the tags are updated directly after the score code is stored, so a request that throws after it leaves no ${TLS_TAG} on a model whose stored code verifies (${describe(errorPageAfter)})`
  );
  assert(
    errorPageAfter.manifestedToasts === 0 && errorPageAfter.notManifested.length === 1 && errorPageAfter.notManifested[0].startsWith('The manifest was not completed: the score code of this run is stored'),
    `and the box beside the button says that the manifest was not completed, with no toast saying "manifested" (${describe(errorPageAfter)})`
  );
  assert(errorPageAfter.tagLines.length === 0 && errorPageAfter.errors.length === 0, `nothing is said about the tags, which were written, and no error is left uncaught (${describe(errorPageAfter)})`);
  const noNetworkAfter = await faultyManifest(`${APP_URL}&sslVerify=false`, 'requirements.json, no network', 'option unticked, requirements.json gets no answer');
  assert(
    noNetworkAfter.scoreCodes === 1 && noNetworkAfter.tags !== null && noNetworkAfter.tags.includes(TLS_TAG),
    `option unticked: a request that gets no answer after the score code leaves the model tagged ${TLS_TAG}, as its stored code does not verify (${describe(noNetworkAfter)})`
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
    `and the box says that nothing was manifested and that the model keeps the score code and the tags it had (${describe(errorPageBefore)})`
  );

  // ---- the upload of the score code gets no usable answer ------------------------
  // A refusal comes back as a status and is certain: the model keeps the score
  // code it had. An upload that THROWS is read back: the model holds this
  // score code (the manifest goes on), or it does not (the model keeps the one
  // it had). Only where nothing can be read back is it not known, and the box
  // says exactly that.
  assert(
    notStored.unknown === 0 && notStored.notManifested[0].includes('The model keeps the score code and the tags it had'),
    `a score code SAS Model Manager refused with an answer keeps the certain wording: the model keeps the score code and the tags it had (box: ${notStored.notManifested.join(' | ') || 'nothing'})`
  );
  const errorPageScore = await faultyManifest(APP_URL, 'score code, error page', 'the score code answered by an error page');
  assert(
    errorPageScore.notManifested.length === 1 && errorPageScore.unknown === 0 &&
      errorPageScore.notManifested[0].startsWith('Not manifested: the upload of the score code got no usable answer from SAS Model Manager, and reading it back shows that the model does not hold the score code of this run') &&
      errorPageScore.scoreCodesStored === 0 && errorPageScore.modelWrites === 0 && errorPageScore.manifestedToasts === 0 && errorPageScore.tagLines.length === 0 && errorPageScore.errors.length === 0,
    `the score code answered by an error page, which stored nothing: the page reads the score code back, and the box says that the model does not hold it - no tag is written, no toast says "manifested" (score codes stored: ${errorPageScore.scoreCodesStored}; writes of the model: ${errorPageScore.modelWrites}; ${describe(errorPageScore)})`
  );
  const lostScore = await faultyManifest(`${APP_URL}&sslVerify=false`, 'score code, answer lost', 'the score code stored, its answer lost');
  assert(
    lostScore.scoreCodesStored === 1 && lostScore.notManifested.length === 0 && lostScore.manifestedToasts === 1 && lostScore.tags !== null && lostScore.tags.includes(TLS_TAG) && lostScore.errors.length === 0,
    `the score code stored and its answer lost: the page reads it back, finds it stored, and the manifest goes on - the tags say what the stored code does, and the toast says "manifested" (score codes stored: ${lostScore.scoreCodesStored}; ${describe(lostScore)})`
  );
  const unknownScore = await faultyManifest(`${APP_URL}&sslVerify=false`, 'score code, answer lost, nothing read back', 'the score code stored, its answer lost, nothing read back');
  assert(
    unknownScore.notManifested.length === 1 && unknownScore.unknown === 1 && unknownScore.notManifested[0].startsWith('The manifest was not completed, and it is not known whether the score code of this run is stored') && unknownScore.notManifested[0].includes('Manifest again') && !unknownScore.notManifested[0].includes('keeps the score code'),
    `the score code stored, its answer lost and nothing read back: the box says that it is not known whether the score code was stored, and to manifest again - not that the model keeps the score code it had (${describe(unknownScore)})`
  );
  assert(
    unknownScore.scoreCodesStored === 1 && unknownScore.modelWrites === 0 && unknownScore.manifestedToasts === 0 && unknownScore.tagLines.length === 0 && unknownScore.errors.length === 0,
    `and the manifest stops there - no tag is written, no toast says "manifested", no error is left uncaught - while the service did store the score code (score codes stored: ${unknownScore.scoreCodesStored}; writes of the model: ${unknownScore.modelWrites}; ${describe(unknownScore)})`
  );

  // ---- the save of the experiments fails outright ---------------------------------
  // A click on Manifest saves the experiments first, and the save had no
  // try/finally around its button: a request that threw in it left "Save
  // Experiments" disabled on "Saving..." for good, nothing was said, and the
  // error stayed uncaught. A save SAS Model Manager refused with an answer
  // was followed by the manifest all the same.
  const describeSave = (outcome) =>
    `Save button: ${outcome.saveButton}; box: ${outcome.box || 'nothing'}; "saved" toasts: ${outcome.savedToasts}; "manifested" toasts: ${outcome.manifestedToasts}; errors: ${outcome.errors.join(' | ') || 'none'}`;
  for (const [fault, what] of [
    ['tracker, error page', 'a Manifest click whose save is answered by an error page'],
    ['tracker, no network', 'a Manifest click whose save gets no answer'],
  ]) {
    const outcome = await faultyManifest(APP_URL, fault, what);
    assert(outcome.saveButton === 'Save Experiments', `${what}: Save Experiments is usable again (${describeSave(outcome)})`);
    // The upload was read back, and SAS Model Manager does not hold it.
    assert(
      outcome.saveLines.length === 1 && outcome.saveLines[0].startsWith(`Save Experiments: ${NOT_CARRIED_OUT}`) && outcome.notManifested.length === 1 && outcome.notManifested[0].startsWith('Not manifested: a manifest saves the experiments first'),
      `${what}: the box beside the Manifest button says that the save was read back and not carried out, and that nothing was manifested (${describeSave(outcome)})`
    );
    assert(
      outcome.savedToasts === 0 && outcome.manifestedToasts === 0 && outcome.errors.length === 0,
      `${what}: no toast says "saved" or "manifested", and no error is left uncaught (${describeSave(outcome)})`
    );
    assert(
      outcome.manifestRequests === 0 && outcome.scoreCodes === 0 && outcome.modelWrites === 0,
      `${what}: the manifest does not go on - nothing of it is asked of SAS Model Manager (requests of the manifest: ${outcome.manifestRequests}, score codes: ${outcome.scoreCodes}, writes of the model: ${outcome.modelWrites})`
    );
  }
  const saveRefused = await faultyManifest(APP_URL, 'tracker, refused', 'a Manifest click whose save is refused');
  assert(
    saveRefused.manifestRequests === 0 && saveRefused.scoreCodes === 0 && saveRefused.modelWrites === 0 && saveRefused.manifestedToasts === 0,
    `a Manifest click whose save SAS Model Manager refuses: the manifest does not go on, and no toast says "manifested" (requests of the manifest: ${saveRefused.manifestRequests}, score codes: ${saveRefused.scoreCodes}, writes of the model: ${saveRefused.modelWrites}; ${describeSave(saveRefused)})`
  );
  assert(
    saveRefused.saveButton === 'Save Experiments' && saveRefused.box.includes("The experiment couldn't be saved") && saveRefused.notManifested.length === 1 && saveRefused.notManifested[0].startsWith('Not manifested: a manifest saves the experiments first') && saveRefused.savedToasts === 0 && saveRefused.errors.length === 0,
    `and the box says both: the experiments could not be saved, and nothing was manifested (${describeSave(saveRefused)})`
  );
  // The pages below open the prompt-test of this section at a step of their own.
  const openPromptTest = async (label, stepName) => {
    const target = await browser.newPage({ viewport: { width: 1500, height: 1100 } });
    const errors = [];
    target.on('pageerror', (e) => errors.push(e.message));
    await target.goto(APP_URL);
    await target.waitForSelector('#LPB-project-dropdown', { timeout: 15000 });
    await target.selectOption('#LPB-project-dropdown', 'proj-1');
    await waitUntil(async () => (await target.$$('#LPB-prompt-dropdown option')).length === 4, `${label}: prompts listed`);
    await target.selectOption('#LPB-prompt-dropdown', 'model-used');
    await waitUntil(async () => (await target.$$('.pet-run-delete')).length === 3, `${label}: the saved runs are loaded`);
    await target.click(`#app-obj-LPB-step-${stepName}`);
    await target.waitForSelector(`#app-obj-LPB-pane-${stepName}:not([hidden])`);
    return { target, errors };
  };
  const trackerUpload = (request) => request.method() === 'POST' && (request.postData() || '').includes('filename="Prompt-Experiment-Tracker.json"');
  // The Save button itself, with the same request failing.
  const SAVE_BOX = '#app-obj-LPB-pet-save-only-result';
  for (const [fault, what] of [
    ['error page', 'Save Experiments answered by an error page'],
    ['no network', 'Save Experiments without an answer'],
  ]) {
    const { target, errors } = await openPromptTest(what, 'build');
    let injected = 0;
    await target.route('**/modelRepository/models/model-used/contents?*', (route) => {
      if (!trackerUpload(route.request())) return route.continue();
      injected += 1;
      return fault === 'error page' ? route.fulfill(errorPage) : route.abort('failed');
    });
    await target.click(SAVE_BUTTON);
    await waitUntil(async () => injected === 1, `${what}: the upload of the experiments fails`, 60000);
    await settles(async () => !(await target.isDisabled(SAVE_BUTTON)) && (await target.textContent(SAVE_BUTTON)).trim() === 'Save Experiments', 60000);
    await quiet(target);
    const saveButton = `${(await target.textContent(SAVE_BUTTON)).trim()}${(await target.isDisabled(SAVE_BUTTON)) ? ', disabled' : ''}`;
    const saveLines = await target.$$eval(`${SAVE_BOX} .pet-save-failed`, (els) => els.map((e) => e.textContent.trim()));
    const savedToasts = (await target.$$eval('.toast-body', (els) => els.map((e) => e.textContent || ''))).filter((text) => text.includes('Experiments saved')).length;
    const seen = `button: ${saveButton}; beside it: ${saveLines.join(' | ') || 'nothing'}; "saved" toasts: ${savedToasts}; errors: ${errors.join(' | ') || 'none'}`;
    assert(saveButton === 'Save Experiments', `${what}: the button is usable again (${seen})`);
    // The upload was read back, and SAS Model Manager does not hold it.
    assert(
      saveLines.length === 1 && saveLines[0].startsWith(`Save Experiments: ${NOT_CARRIED_OUT}`) && (await target.isVisible(`${SAVE_BOX} .pet-save-failed`)),
      `${what}: a line beside the button says that the upload was read back and not carried out (${seen})`
    );
    assert(savedToasts === 0 && errors.length === 0, `${what}: no toast says "saved", and no error is left uncaught (${seen})`);
    await target.close();
  }

  // ---- one manifest per click ---------------------------------------------------------
  // The Manifest button was disabled inside the manifest function only, after
  // the save had been waited for: a double click ran two manifests at once,
  // and the failure line of one could describe the other. The save is made to
  // take its time here, so both clicks land while it is out.
  {
    const { target, errors } = await openPromptTest('a double click on Manifest', 'finalize');
    await target.check('#app-obj-LPB-pet-manifest-integrated');
    // The upload of the save is answered once both clicks have landed.
    const clicked = gate();
    await target.route('**/modelRepository/models/model-used/contents?*', async (route) => {
      if (trackerUpload(route.request())) await clicked.pass();
      await route.continue().catch(() => {});
    });
    await resetLog();
    await target.dblclick(MANIFEST_BUTTON);
    const disabledAtOnce = await target.isDisabled(MANIFEST_BUTTON);
    clicked.open();
    const manifestedToasts = async () => (await target.$$eval('.toast-body', (els) => els.map((e) => e.textContent || ''))).filter((text) => text.includes('has been manifested')).length;
    await waitUntil(async () => (await manifestedToasts()) >= 1, 'a double click on Manifest: a manifest goes through', 20000);
    await settles(async () => !(await target.isDisabled(MANIFEST_BUTTON)) && (await target.textContent(MANIFEST_BUTTON)).trim() === 'Manifest Best Prompt', 12000);
    // A second run, where there is one, is a request of the page.
    await quiet(target);
    const entries = await getLog();
    const trackers = entries.filter((e) => e.method === 'POST' && e.body.includes('filename="Prompt-Experiment-Tracker.json"')).length;
    const scoreCodes = entries.filter((e) => e.method === 'POST' && e.body.includes('def scoreModel(')).length;
    assert(disabledAtOnce, `a click on Manifest takes the button at once, while the experiments are still being saved (disabled after the click: ${disabledAtOnce})`);
    assert(
      trackers === 1 && scoreCodes === 1 && (await manifestedToasts()) === 1 && errors.length === 0,
      `a double click on Manifest runs one manifest: the experiments are saved once, one score code is stored, one toast says "manifested" (saves: ${trackers}, score codes: ${scoreCodes}, toasts: ${await manifestedToasts()}, errors: ${errors.join(' | ') || 'none'})`
    );
    assert(
      !(await target.isDisabled(MANIFEST_BUTTON)) && (await target.textContent(MANIFEST_BUTTON)).trim() === 'Manifest Best Prompt',
      'and the button is usable again when that manifest has ended'
    );
    await target.close();
  }

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
  // A Python keyword and a module the score code imports: each would be
  // assigned in scoreModel. Both rows stay in the panel for the manifest
  // below, which has to leave them out.
  await page.click('text="Add Output Variable"');
  await page.click('text="Add Output Variable"');
  await outRows.nth(2).locator('.pb-outvar-name').fill('class');
  await outRows.nth(3).locator('.pb-outvar-name').fill('json');
  const refusedOutputs = await page.$$eval('.pb-outvar-row .pb-outvar-name', (inputs) =>
    inputs.filter((input) => input.classList.contains('is-invalid')).map((input) => `${input.value}: ${input.nextElementSibling.textContent}`)
  );
  assert(
    refusedOutputs.length === 2 && refusedOutputs[0].startsWith('class: ') && refusedOutputs[1].startsWith('json: ') && refusedOutputs.every((text) => text.includes('score code')),
    `output variables named class and json are refused with the reason (got: ${refusedOutputs.join(' | ')})`
  );
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
    `deselected defaults dropped; parsed outputs + parse_status added, the refused rows left out (got: ${JSON.stringify(outputVarsParsing.map((v) => v.name))})`
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
  assert((await page.locator('.pb-outvar-row').count()) === 2, 'output variable rows restored by load, without the two refused ones');
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
  const HOSTILE_USER = 'It\'s {{ customer }} asking: "why is \\n no line break?"\t{{unknown}} }{ \'\'\'"""';
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
  // The Call LLM node posts llmBody as it is: it has to be JSON by itself and
  // carry what the experiment sent - backslashes, the tab and the apostrophes.
  let nodeInputs = null;
  try {
    nodeInputs = JSON.parse(hostileNode.returned[0]).inputs;
  } catch (e) {
    nodeInputs = null;
  }
  assert(Array.isArray(nodeInputs), `the llmBody of the Call-LLM-node variant is valid JSON (got: ${String(hostileNode.returned[0]).slice(0, 160)})`);
  const nodeInput = (name) => (nodeInputs.find((i) => i.name === name) || {}).value;
  assert(nodeInput('systemPrompt') === hostileSystemSent, `its system prompt is the one the experiment sent (got: ${nodeInput('systemPrompt')})`);
  assert(nodeInput('userPrompt') === hostileUserSent && hostileUserSent.includes('\t') && hostileUserSent.includes("It's"), `its user prompt is the one the experiment sent, tab and apostrophe included (got: ${nodeInput('userPrompt')})`);
  assert(nodeInput('options') === hostileSent.find((i) => i.name === 'options').value, `and its options are those of the experiment (got: ${nodeInput('options')})`);
  assert(hostileNode.returned[1] === `${BASE}/scr/demo_llm/demo_llm`, `llmURL is the container the experiment called (got: ${hostileNode.returned[1]})`);

  // ---- saved runs the manifest cannot take over as they are -------------------
  // The manifest is generated from the saved run, not from the workbench, and
  // a run may be older than the name checks. The tracker of the prompt is
  // served from here for these cases.
  const servedTracker = { rows: null };
  await page.route('**/files/files/trk-1/content', (route) =>
    servedTracker.rows ? route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(servedTracker.rows) }) : route.continue()
  );
  const trackerHeader = (system, user, variables) => ({
    runId: 1, systemPrompt: system, userPrompt: user, model: '', options: '', response: '', run_time: null, prompt_length: null,
    output_length: null, best_prompt: null, fastest_prompt: null, fewest_tokens_prompt: null, variables,
  });
  const trackerAnswer = (model, options) => ({
    runId: 1, systemPrompt: '', userPrompt: '', model, options, response: 'An answer', run_time: 1.2, prompt_length: 10,
    output_length: 20, best_prompt: 1, fastest_prompt: true, fewest_tokens_prompt: true,
  });
  const openServedTracker = async (rows) => {
    servedTracker.rows = rows;
    await goStep('setup');
    await page.selectOption('#LPB-prompt-dropdown', 'Select an existing Prompt-Test');
    await page.selectOption('#LPB-prompt-dropdown', 'model-used');
    await waitUntil(async () => (await page.$$('.pet-run-delete')).length === 1, 'the served run is loaded');
    await goStep('finalize');
  };
  const manifestBox = async () => ((await page.textContent('#app-obj-LPB-pet-save-result')) || '').trim();
  for (const name of ['options', 'class']) {
    await openServedTracker([
      trackerHeader(`Classify: {{${name}}}`, 'Text', [{ name, description: '', type: 'string', value: 'CUSTOMER TEXT' }]),
      trackerAnswer('demo_llm', '{temperature:0.7,API_KEY:OpenAI}'),
    ]);
    await resetLog();
    await page.click('#app-obj-LPB-pet-create-model-button');
    await waitUntil(async () => (await manifestBox()).startsWith('Not manifested'), `the manifest of a saved run with the variable ${name} is refused`);
    const refusal = await manifestBox();
    assert(refusal.includes(`"${name}"`) && refusal.includes('score code'), `the manifest box names the variable ${name} and says why (got: ${refusal})`);
    await waitUntil(async () => !(await page.isDisabled('#app-obj-LPB-pet-create-model-button')), 'the Manifest button is usable again');
    const written = (await getLog()).filter((e) => e.method === 'POST' && /def scoreModel\(|filename="inputVar\.json"|filename="outputVar\.json"/.test(e.body));
    assert(written.length === 0, `no score code and no variable file is uploaded for it (got ${written.length} uploads)`);
  }
  // An option value with backslashes, as a string option with quotes in it is
  // stored, and a model whose name holds a double quote: both are inserted as
  // literals, so the code compiles and sends the stored text.
  await openServedTracker([
    trackerHeader('Classify: {{topic}}', 'Text', [{ name: 'topic', description: '', type: 'string', value: 'CUSTOMER TEXT' }]),
    trackerAnswer('odd"llm', '{stop:say \\hi\\,temperature:0.7}'),
  ]);
  await page.check('#app-obj-LPB-pet-manifest-integrated');
  await resetLog();
  await page.click('#app-obj-LPB-pet-create-model-button');
  await waitUntil(async () => (await getLog()).some((e) => e.method === 'POST' && e.body.includes('def scoreModel(')), 'the score code of the run with the odd option value is uploaded');
  aLog = await getLog();
  const oddCode = scoreCodeOf(aLog.find((e) => e.method === 'POST' && e.body.includes('def scoreModel(')).body);
  assertCompiles(oddCode, 'the score code with a backslash in an option value and a quote in the model name');
  const oddStored = multipartJson(aLog.find((e) => e.method === 'POST' && e.body.includes('filename="Prompt-Experiment-Tracker.json"')).body).find((r) => r.model === 'odd"llm').options;
  const oddCall = callScoreModel(oddCode, ['CUSTOMER TEXT'], { data: { response: 'fine', run_time: 1, prompt_length: 2, output_length: 3 } });
  assert(oddCall.ok, `it runs (${oddCall.ok ? 'scoreModel returned' : lastLine(oddCall.error)})`);
  const oddInput = (name) => (oddCall.request.body.inputs.find((i) => i.name === name) || {}).value;
  assert(oddStored.includes('\\') && oddInput('options') === oddStored, `the manifested model sends the options the tracker stores for the run (sent: ${oddInput('options')}, stored: ${oddStored})`);
  assert(oddInput('systemPrompt') === 'Classify: CUSTOMER TEXT', `and the input where the template has its variable (got: ${oddInput('systemPrompt')})`);
  assert(oddCall.request.url === `${BASE}/scr/odd"llm/odd"llm`, `the model name reaches the URL as it is stored (got: ${oddCall.request.url})`);
  await page.uncheck('#app-obj-LPB-pet-manifest-integrated');
  servedTracker.rows = null;
  await page.unroute('**/files/files/trk-1/content');

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
  // The stored tracker is replaced by the upload; deleting it first lost it
  // when the upload failed.
  assert(
    saveSeq.includes('POST /modelRepository/models/model-used/modelVersions') &&
      !saveSeq.some((request) => request.startsWith('DELETE ')),
    'save created a model version and deleted nothing (the upload replaces the stored tracker)'
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
  await assertLinkCopies(page, '#app-obj-LPB-pet-save-only-result a', 'the link to the saved prompt-test beside Save');
  await assertLinkOpensRealTab(page, '#app-obj-LPB-pet-save-only-result a', 'the link to the saved prompt-test beside Save');
  await assertLinkOpensTab(page, '#app-obj-LPB-pet-save-only-result a', 'the link to the saved prompt-test beside Save');
  await assertLinkTextSurvivesSecondClick(page, '#app-obj-LPB-pet-save-only-result a', 'the link to the saved prompt-test beside Save');
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
    await dialogsGone(page);
    await waitUntil(async () => !(await page.isDisabled('#LPB-delete-prompt-button')), 'Delete Prompt enabled again');
  };
  await goStep('setup');
  await resetLog();
  await noSession('relationships');
  await page.click('#LPB-delete-prompt-button');
  await dialogShown(page, '.modal.show');
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
  await dialogShown(page, '.modal.show');
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
  await dialogShown(page, '.modal.show');
  modalBody = await page.textContent('.modal.show .modal-body');
  assert(
    modalBody.includes('could not be verified') && !modalBody.includes('Loan Approval Decision'),
    `a service that keeps answering 449 leaves the dialog with the could-not-be-verified note (got: ${modalBody.trim()})`
  );
  const stuckLog = ['POST /relationships/relationships 449', 'GET /relationships/ (plain) 200', 'POST /relationships/relationships 449'];
  usageLog = await usageRequests();
  assert(JSON.stringify(usageLog) === JSON.stringify(stuckLog), `the request is sent twice and the session asked for once (got: ${usageLog.join(' | ')})`);
  await quiet(page);
  usageLog = await usageRequests();
  assert(JSON.stringify(usageLog) === JSON.stringify(stuckLog), `and nothing more is sent while the dialog is open (got: ${usageLog.join(' | ')})`);
  await cancelDeletePrompt();
  await noSession();

  // ---- prompt deletion: usage found, cancel then confirm ---------------------
  await resetLog();
  await page.click('#LPB-delete-prompt-button');
  await dialogShown(page, '.modal.show');
  modalBody = await page.textContent('.modal.show .modal-body');
  assert(modalBody.includes('2 decision(s) currently use this prompt'), `usage count shown (got: ${modalBody.trim()})`);
  const linkA = await page.$('.modal.show a[href$="/SASDecisionManager/decisions/flow-A"]');
  const linkB = await page.$('.modal.show a[href$="/SASDecisionManager/decisions/flow-B"]');
  assert(linkA && linkB, 'deep links for both (deduped) decisions present');
  assert((await linkA.textContent()) === 'Loan Approval Decision', 'decision link shows the decision name');
  await page.screenshot({ path: 'shot-03-delete-prompt-usage.png', fullPage: true });
  await page.click('.modal.show .btn-secondary');
  await dialogsGone(page);
  log = await getLog();
  assert(!log.some((e) => e.method === 'DELETE'), 'cancel issued no DELETE');
  assert((await page.$$('#LPB-prompt-dropdown option[value="model-used"]')).length === 1, 'prompt still listed after cancel');
  await waitUntil(
    async () => !(await page.isDisabled('#LPB-delete-prompt-button')),
    'Delete Prompt re-enabled after cancel'
  );
  step(true, 'Delete Prompt re-enabled after cancel');

  await page.click('#LPB-delete-prompt-button');
  await dialogShown(page, '.modal.show');
  await page.click('.modal.show .btn-danger');
  await dialogsGone(page);
  await waitUntil(
    async () => (await getLog()).some((e) => e.method === 'DELETE' && e.url === '/modelRepository/models/model-used'),
    'model deleted'
  );
  assert((await page.$$('#LPB-prompt-dropdown option[value="model-used"]')).length === 0, 'deleted prompt removed from dropdown');
  assert(await page.isDisabled('#LPB-delete-prompt-button'), 'Delete Prompt disabled again (selection reset)');
  assert(
    await page.isDisabled('#app-obj-LPB .pb-doc-section > button'),
    'Save documentation is off after the prompt-test was deleted: there is nothing left to write it to'
  );
  assert(
    (await page.getAttribute('#LPB-openInMMButton', 'class')).includes('disabled'),
    'Open in SAS Model Manager deactivated after prompt deletion'
  );

  // ---- prompt deletion: no usage / usage check failed ------------------------
  await page.selectOption('#LPB-prompt-dropdown', 'model-free');
  await quiet(page);
  await page.click('#LPB-delete-prompt-button');
  await dialogShown(page, '.modal.show');
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
  await dialogsGone(page);

  await page.selectOption('#LPB-prompt-dropdown', 'model-err');
  await quiet(page);
  await resetLog();
  await page.click('#LPB-delete-prompt-button');
  await dialogShown(page, '.modal.show');
  modalBody = await page.textContent('.modal.show .modal-body');
  assert(modalBody.includes('could not be verified'), 'fail-open warning shown when the relationships call fails');
  await waitUntil(
    async () => page.evaluate(() => document.activeElement?.classList?.contains('modal')),
    'modal focus trap active (fade-in finished)'
  );
  await page.keyboard.press('Escape'); // probe: Esc counts as cancel
  await dialogsGone(page);
  await quiet(page);
  log = await getLog();
  assert(!log.some((e) => e.method === 'DELETE'), 'Esc dismissal issued no DELETE');

  // ---- project deletion: cancel mid-sequence deletes nothing -----------------
  await resetLog();
  await page.click('#LPB-delete-project-button');
  await dialogShown(page, '.modal.show');
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
  await dialogsGone(page);
  await quiet(page);
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
    await clickInDialog(page, '.btn-danger');
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
  // Two models answered, so the run can be judged - at once, not only after
  // the prompt-test was opened again.
  const judgeDisabled = (runIndex) => page.isDisabled(`#app-obj-LPB-pet-${runIndex}-judge`);
  assert(!(await judgeDisabled(0)), 'Judge this run is usable as soon as a run with two answers has ended');

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
  assert(!(await judgeDisabled(0)) && !(await judgeDisabled(1)), 'after the second run both runs can be judged');

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

  // -- a judgment that comes back while a run is out -------------------------------
  // A judgment redraws the whole tracker. When it comes back while the calls
  // of a run are out, the run is drawn without its answers and every Judge
  // button is drawn disabled; the run that ends afterwards has to be drawn
  // with its answers, and the buttons have to come back.
  await page.selectOption('#app-obj-LPB-judge-model', 'second_llm');
  // The judgment is answered once the calls of the run are out, and they
  // once the judgment is shown.
  const timedCalls = '**/scr/**';
  let runCalls = gate();
  await page.route(timedCalls, async (route) => {
    const judgment = (route.request().postData() || '').includes('impartial evaluator');
    if (judgment) await waitUntil(async () => runCalls.held > 0, 'the calls of the run are out', 120000).catch(() => {});
    else await runCalls.pass();
    await route.continue().catch(() => {});
  });
  await page.click('#app-obj-LPB-pet-0-judge');
  assert(await judgeDisabled(0), 'the Judge button of a run is disabled while its judgment is out');
  await page.click('#app-obj-LPB-run-experiment');
  await waitUntil(async () => Boolean(await page.$('#app-obj-LPB-pet-0 .pb-judge-banner')), 'the judgment of the first run is shown', 60000);
  assert(await page.isDisabled('#app-obj-LPB-run-experiment'), 'the calls of the run are still out when the judgment is back');
  runCalls.open();
  await waitUntil(async () => !(await page.isDisabled('#app-obj-LPB-run-experiment')), 'the run has ended', 60000);
  await quiet(page);
  runResults = await runModels(2);
  assert(
    runResults.map((r) => r.model).join(',') === 'demo_llm,second_llm,contrarian_judge',
    `the run that ended after the judgment came back is drawn with its answers (got: ${runResults.map((r) => r.model).join(',') || 'no model'})`
  );
  assert((await page.$$('.pet-run-delete')).length === 3, `the tracker shows three runs, each once (got ${(await page.$$('.pet-run-delete')).length})`);
  assert(
    !(await judgeDisabled(0)) && !(await judgeDisabled(1)) && !(await judgeDisabled(2)),
    'and every run can be judged, the one that was judged meanwhile included'
  );
  await page.unroute(timedCalls);

  // -- two judgments out, the first one back ---------------------------------------
  // The judgment that comes back redraws every run. The Judge button of the
  // run whose judgment is still out is drawn anew: it has to show that it is
  // waiting, not stand there disabled with its icon and no hint.
  // The first judgment is answered once both buttons were looked at, the
  // second once the first one is back and looked at.
  let judgmentsAsked = 0;
  const firstJudgment = gate();
  const secondJudgment = gate();
  await page.route(timedCalls, async (route) => {
    if ((route.request().postData() || '').includes('impartial evaluator')) {
      judgmentsAsked += 1;
      await (judgmentsAsked === 1 ? firstJudgment : secondJudgment).pass();
    }
    await route.continue().catch(() => {});
  });
  const judgeButton = (runIndex) =>
    page.$eval(`#app-obj-LPB-pet-${runIndex}-judge`, (button) => ({ disabled: button.disabled, spinner: Boolean(button.querySelector('.spinner-border')) }));
  await page.click('#app-obj-LPB-pet-0-judge');
  await page.click('#app-obj-LPB-pet-2-judge');
  assert(
    (await judgeButton(0)).spinner && (await judgeButton(2)).spinner,
    'two runs are being judged: both Judge buttons show a spinner'
  );
  firstJudgment.open();
  await waitUntil(async () => !(await judgeDisabled(0)), 'the judgment of the first of them is back', 60000);
  const waiting = await judgeButton(2);
  assert(
    waiting.disabled && waiting.spinner,
    `the Judge button of the run whose judgment is still out keeps its spinner through the redraw (disabled: ${waiting.disabled}, spinner: ${waiting.spinner})`
  );
  secondJudgment.open();
  await waitUntil(async () => !(await judgeDisabled(2)), 'the second judgment is back', 60000);
  assert(
    !(await judgeButton(2)).spinner && Boolean(await page.$('#app-obj-LPB-pet-2 .pb-judge-banner')),
    'when it is back the run shows its judgment and the button its icon again'
  );
  await page.unroute(timedCalls);

  // -- a judgment that comes back while a run is out that nobody answers -------------
  // The redraw draws the run that is out, empty, and counts it. No model
  // answers, the run is taken back - and must not stay on the page.
  runCalls = gate();
  await page.route(timedCalls, async (route) => {
    if ((route.request().postData() || '').includes('impartial evaluator')) {
      await waitUntil(async () => runCalls.held > 0, 'the calls of the run are out', 120000).catch(() => {});
      return route.continue().catch(() => {});
    }
    await runCalls.pass();
    return route.fulfill({ status: 503, contentType: 'application/json', body: '{"message":"no container answers"}' }).catch(() => {});
  });
  await page.click('#app-obj-LPB-pet-0-judge');
  await page.click('#app-obj-LPB-run-experiment');
  await waitUntil(async () => (await page.$$('.pet-run-delete')).length === 4, 'the judgment came back while the run was out, and the redraw drew the run that is out', 60000);
  assert(await page.isDisabled('#app-obj-LPB-run-experiment'), 'the calls of that run are still out');
  runCalls.open();
  await waitUntil(async () => !(await page.isDisabled('#app-obj-LPB-run-experiment')), 'the run nobody answered has ended', 60000);
  await quiet(page);
  assert((await runErrorLines()).length === 3, `every model of the run is named with its failure (got: ${(await runErrorLines()).join(' | ')})`);
  const runsShown = await runHeaderTexts();
  assert(
    runsShown.length === 3,
    `a run nobody answered leaves no empty run on the page, also when it was drawn meanwhile (shown: ${runsShown.join(' | ')})`
  );
  assert(
    !(await judgeDisabled(0)) && !(await judgeDisabled(1)) && !(await judgeDisabled(2)),
    'and the three runs that are there can be judged'
  );
  await page.unroute(timedCalls);

  // -- a run in flight and another prompt-test opened meanwhile ------------------
  // The calls of a run take their time (a slow or dead container is exactly
  // when someone moves on). Their answers belong to the prompt-test the run
  // was started on: they must neither take a run from the tracker of the
  // prompt-test that is open by then, nor be written into it.
  // ... answered once the other prompt-test was opened and looked at.
  const slowCalls = '**/scr/**';
  const lateCalls = gate();
  await page.route(slowCalls, async (route) => {
    await lateCalls.pass();
    await route.continue().catch(() => {});
  });
  // The calls of the run the mock has answered so far (a held call is not in its log yet).
  const answeredCalls = async () => (await getLog()).filter((e) => e.method === 'POST' && e.url.startsWith('/scr/') && e.status !== null).length;
  const openPromptDuringRun = async (promptId, runs) => {
    // Opening a run selects its models, so the number of calls is read here.
    const calls = await page.$$eval('input[type=checkbox]', (boxes) => boxes.filter((box) => /^model\d+$/.test(box.id) && box.checked).length);
    await resetLog();
    lateCalls.close();
    await page.click('#app-obj-LPB-run-experiment');
    await goStep('setup');
    await page.selectOption('#LPB-prompt-dropdown', promptId);
    await waitUntil(async () => (await page.$$('.pet-run-delete')).length === runs, `the ${runs} saved runs of the other prompt-test are loaded while the calls are out`);
    await goStep('build');
    assert((await answeredCalls()) === 0, 'the calls of the run are still out when the other prompt-test is open');
    assert(await page.isDisabled('#app-obj-LPB-run-experiment'), 'Run Experiments stays disabled on the other prompt-test while they are');
    lateCalls.open();
    // What follows is about the page AFTER the answers have come in.
    await waitUntil(async () => calls > 0 && (await answeredCalls()) === calls, `the ${calls} call(s) of the run have been answered`, 60000);
    await waitUntil(async () => !(await page.isDisabled('#app-obj-LPB-run-experiment')), 'the Run button is released when the calls have ended', 60000);
    await quiet(page);
  };
  // Every model fails, on a prompt-test that is no longer the open one.
  for (const name of ['demo_llm', 'second_llm', 'contrarian_judge']) await failModel(name, 503);
  await openPromptDuringRun('model-used', 3);
  assert((await runErrorLines()).length === 0, `the failures of the run are not reported on the page of another prompt-test (got: ${(await runErrorLines()).join(' | ')})`);
  assert((await page.textContent('#app-obj-LPB-run-experiment')).trim() === 'Run Experiments', 'the Run button reads Run Experiments again');
  // The runs of this prompt-test were drawn while the calls were out. Its
  // third run has two answers: it can be judged now that the calls have ended.
  assert(!(await judgeDisabled(2)), 'a run of the prompt-test that was opened while a run was out can be judged once that run has ended');
  await failModel();
  // The manifest writes the tracker from the runs the page holds now (a plain
  // Save would write the rows it made when the prompt-test was opened).
  await goStep('finalize');
  await resetLog();
  await page.click('#app-obj-LPB-pet-create-model-button');
  await waitUntil(
    async () => (await getLog()).some((e) => e.method === 'POST' && /\/models\/model-used\/contents\?/.test(e.url) && e.body.includes('Prompt-Experiment-Tracker.json')),
    'the tracker of the open prompt-test is saved'
  );
  await waitUntil(async () => !(await page.isDisabled('#app-obj-LPB-pet-create-model-button')), 'the manifest of the open prompt-test has ended', 20000);
  const keptRows = multipartJson(
    (await getLog()).find((e) => e.method === 'POST' && /\/models\/model-used\/contents\?/.test(e.url) && e.body.includes('Prompt-Experiment-Tracker.json')).body
  ).map((r) => `${r.runId}:${r.model}`);
  assert(
    JSON.stringify(keptRows) === JSON.stringify(['1:', '1:demo_llm', '2:', '2:demo_llm', '3:', '3:demo_llm', '3:other_llm']),
    `the failed run of another prompt-test took no run from this one: all three are saved (got: ${keptRows.join(', ')})`
  );
  // The models answer, late, for a prompt-test that is no longer the open one.
  await goStep('setup');
  await page.selectOption('#LPB-prompt-dropdown', 'model-free');
  await waitUntil(async () => (await page.$$('.pet-run-delete')).length === 0, 'a prompt-test without saved runs is open');
  await goStep('build');
  // A prompt-test without a run brings no LLM, and keeps none of the one that
  // was open before it (issue #47): the run that follows needs one ticked.
  assert(
    !(await page.isChecked('#model0')) && (await page.isDisabled('#app-obj-LPB-run-experiment')),
    'a prompt-test without saved runs opens with no LLM ticked, whatever the prompt-test before it had selected'
  );
  await page.check('#model0');
  const errorsBefore = pageErrors.length;
  await openPromptDuringRun('model-used', 3);
  await quiet(page);
  assert(pageErrors.length === errorsBefore, `late answers for another prompt-test raise no error (got: ${pageErrors.slice(errorsBefore).join(' | ')})`);
  assert((await page.textContent('#app-obj-LPB-run-experiment')).trim() === 'Run Experiments', 'the Run button is not left on Running Experiments');
  assert((await page.$$('.pet-run-delete')).length === 3, 'and the open prompt-test keeps exactly its three runs');
  await page.unroute(slowCalls);

  // ============ PHASE D: creating a prompt-test (fresh page) ============
  await page.goto(APP_URL);
  await page.waitForSelector('#LPB-project-dropdown', { timeout: 15000 });
  await page.selectOption('#LPB-project-dropdown', 'proj-1');
  await waitUntil(async () => (await page.$$('#LPB-prompt-dropdown option')).length === 4, 'prompts listed before the create');
  await resetLog();
  await page.click('#LPB-modal-button-container button[data-bs-target="#promptBuilderCreatePromptModal"]');
  await dialogShown(page, '#promptBuilderCreatePromptModal');
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
  await dialogsGone(page);
  await waitUntil(
    async () => (await page.inputValue('#LPB-prompt-dropdown')) === 'model-new-1',
    'the new prompt-test is selected'
  );
  step(true, 'the created prompt-test is listed and selected');
  // A second prompt-test of the same name: SAS Model Manager refuses it. The
  // dialog stays open and says so; nothing called "undefined" is listed.
  await resetLog();
  await page.click('#LPB-modal-button-container button[data-bs-target="#promptBuilderCreatePromptModal"]');
  await dialogShown(page, '#promptBuilderCreatePromptModal');
  await page.fill('#promptBuilderCreatePromptName', 'Created Prompt');
  await page.click('#promptBuilderCreatePromptModal .btn-primary');
  await waitUntil(
    async () => (await getLog()).some((e) => e.method === 'POST' && e.url === '/modelRepository/models' && e.status === 409),
    'the second creation is refused by the service'
  );
  await waitUntil(
    async () => (await page.$$eval('.toast-body', (els) => els.map((e) => e.textContent || ''))).some((t) => t.includes('The prompt-test was not created')),
    'a toast says that the prompt-test was not created'
  );
  step(true, 'a refused creation is reported');
  assert(
    (await page.isVisible('#promptBuilderCreatePromptModal.show #promptBuilderCreatePromptError')) && (await page.textContent('#promptBuilderCreatePromptError')).includes('The prompt-test was not created'),
    'and the dialog itself says so, where the person is looking'
  );
  await waitUntil(async () => !(await page.isDisabled('#promptBuilderCreatePromptModal .btn-primary')), 'the Create button of the dialog is usable again');
  assert(await page.isVisible('#promptBuilderCreatePromptModal.show'), 'the dialog stays open after a refusal');
  const promptsAfterRefusal = await page.$$eval('#LPB-prompt-dropdown option', (els) => els.map((e) => `${e.value}=${e.textContent}`));
  assert(
    promptsAfterRefusal.length === 5 && !promptsAfterRefusal.some((entry) => entry.includes('undefined')) && (await page.inputValue('#LPB-prompt-dropdown')) === 'model-new-1',
    `no prompt-test "undefined" is listed, and the selection is what it was (got: ${promptsAfterRefusal.join(', ')})`
  );
  const strayRequests = (await getLog()).filter((e) => e.url.includes('/models/undefined'));
  assert(strayRequests.length === 0, `no request goes to a model "undefined" (got: ${strayRequests.map((e) => `${e.method} ${e.url}`).join(' | ')})`);
  await page.click('#promptBuilderCreatePromptModal .btn-secondary');
  await dialogsGone(page);
  // A dialog whose name is empty, or blanks only, asks SAS Model Manager for
  // nothing. Sent on, the empty name was refused there, and the dialog said
  // that a project of this name may already exist. The creation request is
  // answered here the way the service answers an empty name, so that a page
  // that does send it shows what it then says.
  // A name that shows nothing is no name either: a zero-width space got past
  // trim(), and the thing was created under a name nobody can see.
  // The toasts of the refusals above are still on the page and go on their
  // own: they are marked as seen, and only a toast that comes after counts.
  const markToastsSeen = () => page.$$eval('.toast-body', (els) => els.forEach((e) => e.setAttribute('data-verify-seen', '1')));
  const newNotCreatedToasts = async () =>
    (await page.$$eval('.toast-body:not([data-verify-seen])', (els) => els.map((e) => e.textContent || ''))).filter((text) => text.includes('was not created')).length;
  const assertNameIsNeeded = async (dialog, creation, message, label) => {
    const sent = [];
    await page.route(creation, (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      sent.push(route.request().postData() || '');
      return route.fulfill({ status: 400, contentType: 'application/json', body: '{"errorCode":0,"message":"The name must not be empty.","httpStatusCode":400}' });
    });
    await markToastsSeen();
    await page.click(`#LPB-modal-button-container button[data-bs-target="#${dialog}Modal"]`);
    await dialogShown(page, `#${dialog}Modal`);
    const state = () =>
      page.evaluate((id) => {
        const error = document.getElementById(`${id}Error`);
        const name = document.getElementById(`${id}Name`);
        return {
          error: error && !error.hidden ? error.textContent.trim() : '',
          focus: document.activeElement ? document.activeElement.id : '',
          marked: name.getAttribute('aria-invalid') === 'true' && name.classList.contains('is-invalid'),
          open: document.getElementById(`${id}Modal`).classList.contains('show'),
          createOff: document.querySelector(`#${id}Modal .btn-primary`).disabled,
        };
      }, dialog);
    for (const [name, what] of [
      ['', 'no name'],
      ['\u200B', 'a zero-width space as its name'],
      [' \u200B\u200C\u200D\u2060\uFEFF\t\u00AD', 'a name of white space and characters that are not displayed'],
      ['   ', 'a name of blanks only'],
    ]) {
      // Each name is judged by what it sends and says itself.
      sent.length = 0;
      await markToastsSeen();
      await page.fill(`#${dialog}Name`, name);
      await page.fill(`#${dialog}Description`, 'described, but not named');
      await page.click(`#${dialog}Modal .btn-primary`);
      // The dialog answers at the click; a request would be the page's.
      await quiet(page);
      const seen = await state();
      assert(sent.length === 0, `${label} with ${what}: no creation request is sent to SAS Model Manager (sent: ${sent.length})`);
      assert(
        seen.error === message && (await newNotCreatedToasts()) === 0,
        `${label} with ${what}: the dialog says that a name is needed, and nothing says "not created" or blames a name that exists already (dialog: ${seen.error || 'nothing'}; new toasts: ${await newNotCreatedToasts()})`
      );
      assert(
        seen.open && seen.focus === `${dialog}Name` && seen.marked && !seen.createOff,
        `${label} with ${what}: the dialog stays open, the name field is marked and has the cursor, and the button is usable (open: ${seen.open}, focus: ${seen.focus || 'nowhere'}, marked: ${seen.marked}, button disabled: ${seen.createOff})`
      );
    }
    // The cursor is in the field: what is typed now is the name.
    await page.keyboard.type('n');
    const typed = await state();
    assert(
      (await page.inputValue(`#${dialog}Name`)).trim() === 'n' && !typed.marked,
      `${label}: typing goes into the name field, and the mark of the missing name is gone with the first letter (name: "${await page.inputValue(`#${dialog}Name`)}", marked: ${typed.marked})`
    );
    await page.fill(`#${dialog}Name`, '');
    await page.fill(`#${dialog}Description`, '');
    await page.unroute(creation);
    await page.click(`#${dialog}Modal .btn-secondary`);
    await dialogsGone(page);
  };
  await assertNameIsNeeded('promptBuilderCreateProject', '**/modelRepository/projects', 'A project name is required.', 'Create a new Project');
  await assertNameIsNeeded('promptBuilderCreatePrompt', '**/modelRepository/models', 'A prompt name is required.', 'Create a new Prompt');
  // The same for a project the service refuses (no permission to write).
  const projectCreation = '**/modelRepository/projects';
  await page.route(projectCreation, (route) =>
    route.request().method() === 'POST'
      ? route.fulfill({ status: 403, contentType: 'application/json', body: '{"errorCode":1003,"message":"Forbidden","httpStatusCode":403}' })
      : route.continue()
  );
  const projectsBefore = await page.$$eval('#LPB-project-dropdown option', (els) => els.map((e) => e.value).join(','));
  await page.click('#LPB-modal-button-container button[data-bs-target="#promptBuilderCreateProjectModal"]');
  await dialogShown(page, '#promptBuilderCreateProjectModal');
  await page.fill('#promptBuilderCreateProjectName', 'Refused Project');
  await page.click('#promptBuilderCreateProjectModal .btn-primary');
  await waitUntil(
    async () => (await page.$$eval('.toast-body', (els) => els.map((e) => e.textContent || ''))).some((t) => t.includes('The project was not created')),
    'a toast says that the project was not created'
  );
  await waitUntil(async () => !(await page.isDisabled('#promptBuilderCreateProjectModal .btn-primary')), 'the Create button of the project dialog is usable again');
  assert(
    (await page.isVisible('#promptBuilderCreateProjectModal.show')) && (await page.$$eval('#LPB-project-dropdown option', (els) => els.map((e) => e.value).join(','))) === projectsBefore && (await page.inputValue('#LPB-project-dropdown')) === 'proj-1',
    'a refused project leaves the dialog open, the project list as it was and the project selected'
  );
  assert(
    (await page.isVisible('#promptBuilderCreateProjectModal.show #promptBuilderCreateProjectError')) && (await page.textContent('#promptBuilderCreateProjectError')).includes('The project was not created'),
    'the project dialog says inside it that the project was not created'
  );
  await page.unroute(projectCreation);
  await page.click('#promptBuilderCreateProjectModal .btn-secondary');
  await dialogsGone(page);
  // Opened again, the dialog does not repeat the message of the last attempt.
  await page.click('#LPB-modal-button-container button[data-bs-target="#promptBuilderCreateProjectModal"]');
  await dialogShown(page, '#promptBuilderCreateProjectModal');
  assert(!(await page.isVisible('#promptBuilderCreateProjectError')), 'a create dialog that is opened again shows no message of the attempt before');
  await page.click('#promptBuilderCreateProjectModal .btn-secondary');
  await dialogsGone(page);
  // What the dialog checked at the click is what is created. Create a new
  // Project asks for the repository first, and read the name field again
  // after that answer: a name cleared in between was sent as "". The dialog
  // now hands the name and the description of the click to the creation, and
  // its fields cannot be edited while the creation is out. `slow` is the
  // request that takes its time - the repository, or the creation itself.
  const assertCreatesWhatWasChecked = async (dialog, creation, slow, label) => {
    const sent = [];
    // The request that takes its time is answered once the dialog was looked
    // at while the creation is out.
    const held = { out: 0, looked: false };
    const hold = async () => {
      held.out += 1;
      await waitUntil(async () => held.looked, 'the dialog is looked at while the creation is out', 120000).catch(() => {});
    };
    await page.route(creation, async (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      sent.push(JSON.parse(route.request().postData() || '{}'));
      if (!slow) await hold();
      return route.fulfill({ status: 403, contentType: 'application/json', body: '{"errorCode":1003,"message":"Forbidden","httpStatusCode":403}' });
    });
    if (slow) {
      await page.route(slow, async (route) => {
        await hold();
        await route.continue().catch(() => {});
      });
    }
    await page.click(`#LPB-modal-button-container button[data-bs-target="#${dialog}Modal"]`);
    await dialogShown(page, `#${dialog}Modal`);
    await page.fill(`#${dialog}Name`, 'ZZ Named At The Click');
    await page.fill(`#${dialog}Description`, 'described at the click');
    await page.click(`#${dialog}Modal .btn-primary`);
    await waitUntil(async () => held.out > 0, `${label}: the creation is out`, 60000).catch(() => {});
    // What a person could type now a script can always set: the fields are
    // emptied, whether or not they can be edited.
    const pending = await page.evaluate((id) => {
      const name = document.getElementById(`${id}Name`);
      const description = document.getElementById(`${id}Description`);
      const state = { name: name.disabled || name.readOnly, description: description.disabled || description.readOnly, create: document.querySelector(`#${id}Modal .btn-primary`).disabled };
      name.value = '';
      description.value = '';
      return state;
    }, dialog);
    held.looked = true;
    await waitUntil(async () => sent.length >= 1 && !(await page.isDisabled(`#${dialog}Modal .btn-primary`)), `${label}: the creation request is sent and answered`);
    const after = await page.evaluate((id) => ({ name: document.getElementById(`${id}Name`).disabled, description: document.getElementById(`${id}Description`).disabled }), dialog);
    assert(
      pending.name && pending.description && pending.create,
      `${label}: while the creation is out, neither the name nor the description can be edited, and the button is disabled (name: ${pending.name}, description: ${pending.description}, button: ${pending.create})`
    );
    assert(
      sent.length === 1 && sent[0].name === 'ZZ Named At The Click' && sent[0].description === 'described at the click',
      `${label}: what is created is what stood in the dialog at the click, also with the fields emptied while the creation is out (requests: ${sent.length}; name sent: "${(sent[0] || {}).name}"; description sent: "${(sent[0] || {}).description}")`
    );
    assert(!after.name && !after.description, `${label}: when the creation has ended the fields can be edited again (name disabled: ${after.name}, description disabled: ${after.description})`);
    await page.unroute(creation);
    if (slow) await page.unroute(slow);
    await page.click(`#${dialog}Modal .btn-secondary`);
    await dialogsGone(page);
  };
  await assertCreatesWhatWasChecked('promptBuilderCreateProject', '**/modelRepository/projects', '**/modelRepository/repositories/**', 'Create a new Project');
  await assertCreatesWhatWasChecked('promptBuilderCreatePrompt', '**/modelRepository/models', null, 'Create a new Prompt');
  // ---- the create dialogs: what only looks like a name, a request that fails
  // on the way to a creation, and the keyboard -----------------------------------
  const createDialogState = (dialog) =>
    page.evaluate((id) => {
      const modal = document.getElementById(`${id}Modal`);
      const error = document.getElementById(`${id}Error`);
      const name = document.getElementById(`${id}Name`);
      const create = modal.querySelector('.btn-primary');
      const active = document.activeElement;
      return {
        open: modal.classList.contains('show'),
        error: error && !error.hidden ? error.textContent.trim() : '',
        focus: active === create ? 'Create' : active === modal ? 'dialog' : active === name ? 'name' : active === document.body ? 'body' : active ? active.id || active.tagName : 'nowhere',
        marked: name.getAttribute('aria-invalid') === 'true' && name.classList.contains('is-invalid'),
        createOff: create.disabled,
        fieldsOff: name.disabled || document.getElementById(`${id}Description`).disabled,
        closersOff: modal.querySelector('.btn-secondary').disabled && modal.querySelector('.btn-close').disabled,
      };
    }, dialog);
  const openCreateDialog = async (dialog) => {
    if (!(await page.isVisible(`#${dialog}Modal.show`))) {
      await page.click(`#LPB-modal-button-container button[data-bs-target="#${dialog}Modal"]`);
    }
    // Faded in: Bootstrap has put the focus on the dialog.
    await dialogShown(page, `#${dialog}Modal`);
  };
  const closeCreateDialog = async (dialog) => {
    if (await page.isVisible(`#${dialog}Modal.show`)) await page.click(`#${dialog}Modal .btn-secondary`);
    await dialogsGone(page);
  };
  const refusal = (status, message) => ({ status, contentType: 'application/json', body: JSON.stringify({ errorCode: 0, message, httpStatusCode: status }) });
  // A name is a name by what it shows. White space and the characters of
  // Default_Ignorable_Code_Point were refused already; the braille pattern
  // blank, a control character and two characters defined as an empty shape
  // are drawn as nothing as well and were sent on. The last entry is the
  // reverse check: fillers and marks that the property does cover.
  const BLANK_LOOKING = [
    ['\u2800', 'the braille pattern blank (U+2800) as its name'],
    ['\u2800 \u200B\u3164\u2800', 'a name of braille blanks, a blank, a zero-width space and a Hangul filler'],
    ['\u0001\u007F\u0085', 'a name of control characters (U+0001, U+007F, U+0085)'],
    ['\u{1D159}\u{16FE4}', 'the musical null notehead (U+1D159) and the Khitan filler (U+16FE4) as its name'],
    [
      '\u3164\uFFA0\u115F\u1160\u00AD\u034F\u180E\u061C\u{E0020}\u{E0041}\uFE0F\u{E0100}',
      'a name of Hangul fillers, the soft hyphen, the grapheme joiner, the Mongolian vowel separator, the Arabic letter mark, tag characters and variation selectors',
    ],
  ];
  // One visible character makes a name, whatever stands beside it, and the
  // name is sent as it was typed.
  const VISIBLE_NAMES = [
    ['\u2800x\u200B', 'a letter between a braille blank and a zero-width space'],
    ['7', 'a digit'],
    ['-', 'a hyphen'],
    ['\u4E2D', 'a CJK letter'],
    ['\u0639', 'an Arabic letter'],
    ['\u2764\uFE0F', 'a heart with its variation selector'],
    ['\u{1F468}\u200D\u{1F469}\u200D\u{1F467}', 'an emoji joined by zero-width joiners'],
    ['\u2801', 'a braille pattern with one dot'],
  ];
  const assertNamesByWhatShows = async (dialog, creation, message, label) => {
    const sent = [];
    await page.route(creation, (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      sent.push(JSON.parse(route.request().postData() || '{}').name);
      return route.fulfill(refusal(400, 'The name must not be empty.'));
    });
    await openCreateDialog(dialog);
    for (const [name, what] of BLANK_LOOKING) {
      sent.length = 0;
      await page.fill(`#${dialog}Name`, name);
      await page.click(`#${dialog}Modal .btn-primary`);
      await quiet(page);
      const seen = await createDialogState(dialog);
      assert(
        sent.length === 0 && seen.error === message && seen.focus === 'name' && seen.marked && !seen.createOff,
        `${label} with ${what}: nothing is sent to SAS Model Manager, the dialog says that a name is required, and the name field is marked and has the cursor (sent: ${sent.length}; dialog: ${seen.error || 'nothing'}; focus: ${seen.focus}; marked: ${seen.marked})`
      );
    }
    const notAsTyped = [];
    for (const [name, what] of VISIBLE_NAMES) {
      sent.length = 0;
      await page.fill(`#${dialog}Name`, name);
      await page.click(`#${dialog}Modal .btn-primary`);
      for (let attempt = 0; attempt < 600 && (sent.length === 0 || (await page.isDisabled(`#${dialog}Modal .btn-primary`))); attempt++) await sleep(100);
      if (sent.length !== 1 || sent[0] !== name) notAsTyped.push(`${what} (requests: ${sent.length})`);
    }
    assert(
      notAsTyped.length === 0,
      `${label}: a name with one visible character is sent, as it was typed - ${VISIBLE_NAMES.map((entry) => entry[1]).join('; ')} (not so: ${notAsTyped.join(', ') || 'none'})`
    );
    await page.unroute(creation);
    await page.fill(`#${dialog}Name`, '');
    await closeCreateDialog(dialog);
  };
  await assertNamesByWhatShows('promptBuilderCreateProject', '**/modelRepository/projects', 'A project name is required.', 'Create a new Project');
  await assertNamesByWhatShows('promptBuilderCreatePrompt', '**/modelRepository/models', 'A prompt name is required.', 'Create a new Prompt');
  // Create a new Project asks for the repository before it creates. When
  // that request threw or was refused the click ended without a word and
  // with an error on the page. It ends in the dialog now: nothing was
  // created, the button and the fields are usable, the focus is back on the
  // button, and nothing says "created".
  const repositoryRequest = '**/modelRepository/repositories/**';
  for (const [what, answer] of [
    ['is cut off', (route) => route.abort('failed')],
    ['is answered with an error page', (route) => route.fulfill({ status: 502, contentType: 'text/html', body: '<html><body>Bad Gateway</body></html>' })],
    ['is answered with a page where JSON was expected', (route) => route.fulfill({ status: 200, contentType: 'text/html', body: '<html><body>Sign in</body></html>' })],
    ['is refused', (route) => route.fulfill(refusal(403, 'Forbidden'))],
  ]) {
    let creations = 0;
    await page.route(projectCreation, (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      creations += 1;
      return route.fulfill(refusal(403, 'Forbidden'));
    });
    await page.route(repositoryRequest, answer);
    await openCreateDialog('promptBuilderCreateProject');
    await markToastsSeen();
    const errorsBefore = pageErrors.length;
    await page.fill('#promptBuilderCreateProjectName', 'ZZ No Repository');
    await page.click('#promptBuilderCreateProjectModal .btn-primary');
    await waitUntil(async () => {
      const state = await createDialogState('promptBuilderCreateProject');
      return state.error !== '' && !state.createOff;
    }, 'the creation has ended', 60000).catch(() => {});
    await quiet(page);
    const seen = await createDialogState('promptBuilderCreateProject');
    const newToasts = (await page.$$('.toast-body:not([data-verify-seen])')).length;
    await page.unroute(repositoryRequest);
    await page.unroute(projectCreation);
    assert(
      creations === 0 && seen.open && seen.error.includes('the model repository could not be read') && pageErrors.length === errorsBefore,
      `Create a new Project while the request for the repository ${what}: the dialog stays open and says that nothing was created because the repository could not be read, and no error reaches the page (creation requests: ${creations}; open: ${seen.open}; dialog: ${seen.error || 'nothing'}; page errors: ${pageErrors.slice(errorsBefore).join(' | ') || 'none'})`
    );
    assert(
      !seen.createOff && !seen.fieldsOff && seen.focus === 'Create' && newToasts === 0,
      `Create a new Project while the request for the repository ${what}: its button and its fields are usable again, the focus is back on the button, and no toast says anything (button disabled: ${seen.createOff}; fields disabled: ${seen.fieldsOff}; focus: ${seen.focus}; toasts: ${newToasts})`
    );
  }
  await page.fill('#promptBuilderCreateProjectName', '');
  await closeCreateDialog('promptBuilderCreateProject');
  // An error nobody expected ends in the dialog as well. The project picker
  // is left without a selected entry here, which Create a new Prompt reads
  // without asking; the dialog catches the error and says what to do.
  {
    let creations = 0;
    await page.route('**/modelRepository/models', (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      creations += 1;
      return route.fulfill(refusal(403, 'Forbidden'));
    });
    await openCreateDialog('promptBuilderCreatePrompt');
    const errorsBefore = pageErrors.length;
    await page.evaluate(() => {
      document.getElementById('LPB-project-dropdown').selectedIndex = -1;
    });
    await page.fill('#promptBuilderCreatePromptName', 'ZZ Unexpected');
    await page.click('#promptBuilderCreatePromptModal .btn-primary');
    await waitUntil(async () => {
      const state = await createDialogState('promptBuilderCreatePrompt');
      return state.error !== '' && !state.createOff;
    }, 'the creation has ended', 60000).catch(() => {});
    await quiet(page);
    const seen = await createDialogState('promptBuilderCreatePrompt');
    await page.evaluate(() => {
      document.getElementById('LPB-project-dropdown').value = 'proj-1';
    });
    await page.unroute('**/modelRepository/models');
    assert(
      creations === 0 && seen.open && seen.error.includes('The creation did not finish') && !seen.createOff && !seen.fieldsOff && pageErrors.length === errorsBefore,
      `Create a new Prompt that ends with an error nobody expected: the dialog stays open and says that the creation did not finish, its button and its fields are usable, and no error reaches the page (dialog: ${seen.error || 'nothing'}; button disabled: ${seen.createOff}; page errors: ${pageErrors.slice(errorsBefore).join(' | ') || 'none'})`
    );
    await page.fill('#promptBuilderCreatePromptName', '');
    await closeCreateDialog('promptBuilderCreatePrompt');
  }
  // Create by keyboard. Disabling the button that has the focus dropped the
  // focus to <body>, where it stayed after a refusal; Escape then closed
  // nothing. The focus waits on the dialog while the creation is out and is
  // back on the button afterwards. While the creation is out the dialog
  // cannot be closed - by Escape, by its two closing buttons, by a click
  // beside it - and it can whenever none is, wherever the focus is.
  const assertCreateByKeyboard = async (dialog, creation, label) => {
    // The creation is refused once the dialog was looked at while it is out.
    const held = { out: 0, looked: false };
    await page.route(creation, async (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      held.out += 1;
      await waitUntil(async () => held.looked, 'the dialog is looked at while the creation is out', 120000).catch(() => {});
      return route.fulfill(refusal(409, 'exists'));
    });
    for (const [key, keyName] of [['Enter', 'Enter'], ['Space', 'the space bar']]) {
      await openCreateDialog(dialog);
      await page.fill(`#${dialog}Name`, 'ZZ By Keyboard');
      await page.focus(`#${dialog}Modal .btn-primary`);
      held.looked = false;
      const before = held.out;
      await page.keyboard.press(key);
      await waitUntil(async () => held.out > before, `${label}: the creation is out`, 60000).catch(() => {});
      const pending = await createDialogState(dialog);
      // Escape and a click beside the dialog act at once: Bootstrap takes the
      // dialog's show class when it starts to hide it.
      await page.keyboard.press('Escape');
      await page.mouse.click(5, 5);
      const openWhilePending = (await createDialogState(dialog)).open;
      held.looked = true;
      let after = await createDialogState(dialog);
      for (let attempt = 0; attempt < 600 && (after.createOff || !after.error); attempt++) {
        await sleep(100);
        after = await createDialogState(dialog);
      }
      await page.keyboard.press('Escape');
      await dialogsGone(page).catch(() => {});
      const openAfterEscape = (await createDialogState(dialog)).open;
      assert(
        pending.createOff && pending.fieldsOff && pending.closersOff && pending.focus === 'dialog' && openWhilePending,
        `${label}, ${keyName} on its button: while the creation is out the focus waits on the dialog, Cancel and the X are disabled, and neither Escape nor a click beside the dialog closes it (focus: ${pending.focus}; closing buttons disabled: ${pending.closersOff}; open: ${openWhilePending})`
      );
      assert(
        after.open && after.error !== '' && !after.createOff && after.focus === 'Create' && !openAfterEscape,
        `${label}, ${keyName} on its button: after the refusal the focus is back on the button, and Escape closes the dialog (open: ${after.open}; dialog: ${after.error ? 'a message' : 'nothing'}; focus: ${after.focus}; open after Escape: ${openAfterEscape})`
      );
    }
    await openCreateDialog(dialog);
    await page.evaluate(() => {
      if (document.activeElement) document.activeElement.blur();
    });
    const where = (await createDialogState(dialog)).focus;
    await page.keyboard.press('Escape');
    await dialogsGone(page).catch(() => {});
    const openAfterEscape = (await createDialogState(dialog)).open;
    assert(
      where === 'body' && !openAfterEscape,
      `${label}: Escape closes the dialog also when the focus is on the page behind it (focus: ${where}; open after Escape: ${openAfterEscape})`
    );
    await page.unroute(creation);
    await openCreateDialog(dialog);
    await page.fill(`#${dialog}Name`, '');
    await closeCreateDialog(dialog);
  };
  await assertCreateByKeyboard('promptBuilderCreateProject', '**/modelRepository/projects', 'Create a new Project');
  await assertCreateByKeyboard('promptBuilderCreatePrompt', '**/modelRepository/models', 'Create a new Prompt');
  // After a reload the listing of the project is what brings it back.
  await page.goto(APP_URL);
  await page.waitForSelector('#LPB-project-dropdown', { timeout: 15000 });
  await page.selectOption('#LPB-project-dropdown', 'proj-1');
  await waitUntil(async () => (await page.$$('#LPB-prompt-dropdown option')).length === 5, 'created prompt listed after a reload');
  const promptsAfterReload = await page.$$eval('#LPB-prompt-dropdown option', (els) => els.map((e) => e.textContent));
  assert(promptsAfterReload.includes('Created Prompt'), `the created prompt-test is listed after a reload (got: ${promptsAfterReload})`);

  // ---- the first entry of a picker is a placeholder, not an id ------------------
  await page.selectOption('#LPB-prompt-dropdown', 'model-used');
  await waitUntil(async () => (await page.$$('.pet-run-delete')).length === 3, 'a prompt-test is open before the placeholder is chosen');
  const viyaRequests = async () => (await getLog()).filter((e) => !e.url.startsWith('/__') && !e.url.startsWith('/scr/')).map((e) => `${e.method} ${decodeURIComponent(e.url)}`);
  await resetLog();
  await page.selectOption('#LPB-prompt-dropdown', { index: 0 });
  await waitUntil(async () => (await page.$$('.pet-run-delete')).length === 0, 'choosing the placeholder of the prompt picker empties the tracker');
  await quiet(page);
  let placeholderRequests = await viyaRequests();
  assert(
    placeholderRequests.length === 0,
    `choosing the placeholder of the prompt picker asks SAS Viya for nothing - its text is not a model id (asked: ${placeholderRequests.join(' | ') || 'nothing'})`
  );
  assert(
    (await page.isDisabled('#LPB-delete-prompt-button')) && (await page.getAttribute('#LPB-openInMMButton', 'href')) === null && (await page.getAttribute('#LPB-openInMMButton', 'aria-disabled')) === 'true',
    'with the placeholder chosen Delete Prompt and Open in SAS Model Manager are off, and the link leads nowhere'
  );
  await resetLog();
  await page.selectOption('#LPB-project-dropdown', { index: 0 });
  await waitUntil(async () => (await page.$$('#LPB-prompt-dropdown option')).length === 1, 'choosing the placeholder of the project picker empties the prompt list');
  await quiet(page);
  placeholderRequests = await viyaRequests();
  assert(
    placeholderRequests.length === 0,
    `choosing the placeholder of the project picker asks SAS Viya for nothing - its text is not a project id (asked: ${placeholderRequests.join(' | ') || 'nothing'})`
  );
  assert(await page.isDisabled('#LPB-delete-project-button'), 'with the placeholder chosen Delete Project is off');

  // ---- an entry that is still loading when another one is chosen ------------------
  // Every request to SAS Model Manager and to the files service takes 300 ms
  // here, which a real SAS Viya easily does. Opening a prompt-test is three
  // requests in a row; an arrow key in the picker chooses an entry per press.
  // What is drawn has to belong to the entry chosen LAST - with the runs of
  // another prompt-test on the page, Save would write them to the one the
  // picker names.
  const slowViya = async (route) => {
    await sleep(300);
    await route.continue().catch(() => {});
  };
  await page.route('**/modelRepository/**', slowViya);
  await page.route('**/files/**', slowViya);
  const DOC_SAVE = '#app-obj-LPB .pb-doc-section > button';
  const openState = async () => ({
    picked: await page.inputValue('#LPB-prompt-dropdown'),
    runs: (await page.$$('.pet-run-delete')).length,
    link: await page.getAttribute('#LPB-openInMMButton', 'href'),
    deleteOff: await page.isDisabled('#LPB-delete-prompt-button'),
    documentationOff: await page.isDisabled(DOC_SAVE),
  });
  const promptsListed = async () => (await page.$$eval('#LPB-prompt-dropdown option', (els) => els.map((e) => e.value))).includes('model-used');
  // Choose an entry, and come back once its loading is out.
  const chooseWhileOut = async (select, value, path) => {
    const out = page.waitForRequest((request) => request.url().includes(path), { timeout: 60000 });
    await page.selectOption(select, value);
    await out.catch(() => {});
  };
  await page.selectOption('#LPB-project-dropdown', 'proj-1');
  await waitUntil(promptsListed, 'the prompt-tests of the project are listed, slowly');
  // Another prompt-test is chosen while the first one is still loading.
  await chooseWhileOut('#LPB-prompt-dropdown', 'model-used', '/modelRepository/models/model-used');
  await page.selectOption('#LPB-prompt-dropdown', 'model-free');
  await waitUntil(async () => (await page.getAttribute('#LPB-openInMMButton', 'href')) !== null, 'the prompt-test chosen last is open');
  await quiet(page);
  let open = await openState();
  assert(
    open.picked === 'model-free' && open.runs === 0 && String(open.link).endsWith('/SASModelManager/models/model-free/files'),
    `a prompt-test that was still loading when another one was chosen is not drawn: the page shows the one the picker names (picker: ${open.picked}, runs drawn: ${open.runs}, link: ${open.link})`
  );
  // The placeholder is chosen while a prompt-test is still loading.
  await chooseWhileOut('#LPB-prompt-dropdown', 'model-used', '/modelRepository/models/model-used');
  await page.selectOption('#LPB-prompt-dropdown', { index: 0 });
  await quiet(page);
  open = await openState();
  assert(
    open.runs === 0 && open.link === null && open.deleteOff && open.documentationOff,
    `nor after the placeholder was chosen: no runs, no link, Delete Prompt and Save documentation off (runs drawn: ${open.runs}, link: ${open.link}, Delete off: ${open.deleteOff}, Save documentation off: ${open.documentationOff})`
  );
  // The same prompt-test chosen again while its first loading is out: drawn once.
  await chooseWhileOut('#LPB-prompt-dropdown', 'model-used', '/modelRepository/models/model-used');
  await page.selectOption('#LPB-prompt-dropdown', { index: 0 });
  await page.selectOption('#LPB-prompt-dropdown', 'model-used');
  await waitUntil(async () => (await page.$$('.pet-run-delete')).length === 3, 'chosen, left and chosen again, the prompt-test opens');
  await quiet(page);
  open = await openState();
  assert(
    open.picked === 'model-used' && open.runs === 3 && !open.deleteOff && !open.documentationOff,
    `chosen, left and chosen again within a moment, the prompt-test is open with its three runs, each once (runs drawn: ${open.runs})`
  );
  // The documentation section belongs to the prompt-test that is open. With
  // another project (or none) chosen it belongs to nothing: Save documentation
  // would write what is typed now to the prompt-test that was open before.
  await resetLog();
  await page.selectOption('#LPB-project-dropdown', { index: 0 });
  await quiet(page);
  assert(
    (await page.isDisabled(DOC_SAVE)) && (await page.$$('.pet-run-delete')).length === 0,
    'with the placeholder of the project picker chosen, Save documentation is off and its fields no longer belong to the prompt-test that was open'
  );
  // A project whose prompt-tests are still being listed when the placeholder is chosen.
  await chooseWhileOut('#LPB-project-dropdown', 'proj-1', '/modelRepository/projects/proj-1/models');
  await page.selectOption('#LPB-project-dropdown', { index: 0 });
  await quiet(page);
  const listedUnderPlaceholder = await page.$$eval('#LPB-prompt-dropdown option', (els) => els.map((e) => e.value));
  assert(
    listedUnderPlaceholder.length === 1,
    `the prompt-tests of a project whose listing was still out are not listed once the placeholder is chosen (listed: ${listedUnderPlaceholder.join(', ')})`
  );
  // The other kind chosen with a prompt-test open: the same for the documentation.
  await page.selectOption('#LPB-project-dropdown', 'proj-1');
  await waitUntil(promptsListed, 'the prompt-tests of the project are listed again');
  await page.selectOption('#LPB-prompt-dropdown', 'model-used');
  await waitUntil(async () => (await page.$$('.pet-run-delete')).length === 3 && !(await page.isDisabled(DOC_SAVE)), 'a prompt-test is open and its documentation can be saved');
  await page.click('#LPB-kind-decision');
  await waitUntil(async () => (await page.$$('.pet-run-delete')).length === 0, 'the other kind is shown, with nothing open');
  await quiet(page);
  assert(await page.isDisabled(DOC_SAVE), 'after a change of kind Save documentation is off as well');
  await page.click('#LPB-kind-llm');
  await page.unroute('**/modelRepository/**', slowViya);
  await page.unroute('**/files/**', slowViya);

  // ---- an optimization does not start over experiments that are not saved ----------
  // Run optimization saves the experiments first, so that the job reads what
  // the page shows. A save that threw ended the launch with the error of the
  // browser and left "Save Experiments" on "Saving..."; a save that was
  // refused was followed by the job all the same. These pages come last: a
  // job that is launched stays launched in the mock.
  for (const [fault, what] of [
    ['refused', 'Run optimization whose save is refused'],
    ['no network', 'Run optimization whose save gets no answer'],
  ]) {
    const target = await browser.newPage({ viewport: { width: 1500, height: 1100 } });
    const errors = [];
    target.on('pageerror', (e) => errors.push(e.message));
    await target.goto(`${APP_URL}&enableOptimization=true&computeContext=ctx&optimizeJobProgram=/Public/Jobs/Optimize-Prompt-DSPy&minOptimizeSamples=1`);
    await target.waitForSelector('#LPB-project-dropdown', { timeout: 15000 });
    await target.selectOption('#LPB-project-dropdown', 'proj-1');
    await waitUntil(async () => (await target.$$('#LPB-prompt-dropdown option')).length >= 4, `${what}: prompts listed`);
    await target.selectOption('#LPB-prompt-dropdown', 'model-used');
    await waitUntil(async () => (await target.$$('.pet-run-delete')).length === 3, `${what}: the saved runs are loaded`);
    await target.click('#app-obj-LPB-step-optimize');
    await target.waitForSelector('#app-obj-LPB-pane-optimize:not([hidden])');
    await target.selectOption('#app-obj-LPB-optimize-target', 'demo_llm');
    await waitUntil(async () => !(await target.isDisabled('#app-obj-LPB-optimize-run')), `${what}: the optimization can be started`);
    let injected = 0;
    await target.route('**/modelRepository/models/model-used/contents?*', (route) => {
      const request = route.request();
      if (!(request.method() === 'POST' && (request.postData() || '').includes('filename="Prompt-Experiment-Tracker.json"'))) return route.continue();
      injected += 1;
      return fault === 'refused'
        ? route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ errorCode: 1003, message: 'Forbidden', httpStatusCode: 403 }) })
        : route.abort('failed');
    });
    await resetLog();
    await target.click('#app-obj-LPB-optimize-run');
    await waitUntil(async () => injected === 1, `${what}: the upload of the experiments fails`, 60000);
    await settles(async () => !(await target.isDisabled('#app-obj-LPB-pet-save-button')) && (await target.textContent('#app-obj-LPB-pet-save-button')).trim() === 'Save Experiments', 60000);
    // A job launched after the failed save would be a request of the page.
    await quiet(target);
    const launches = (await getLog()).filter((e) => e.method === 'POST' && e.url === '/jobExecution/jobs').length;
    const said = ((await target.textContent('#app-obj-LPB-optimize-result')) || '').trim();
    const saveButton = `${(await target.textContent('#app-obj-LPB-pet-save-button')).trim()}${(await target.isDisabled('#app-obj-LPB-pet-save-button')) ? ', disabled' : ''}`;
    assert(
      launches === 0 && said.startsWith('Could not launch the optimization job:') && said.includes('the save of the experiments before it was not confirmed'),
      `${what}: no job is launched, and the Optimize step says that the experiments were not saved first (jobs launched: ${launches}; said: ${said || 'nothing'})`
    );
    assert(
      saveButton === 'Save Experiments' && !(await target.isDisabled('#app-obj-LPB-optimize-run')) && errors.length === 0,
      `${what}: Save Experiments and Run optimization are usable again, and no error is left uncaught (Save button: ${saveButton}; errors: ${errors.join(' | ') || 'none'})`
    );
    await target.close();
  }

  // ---- every request to SAS Viya went through the HTTP client of the app -------
  const directRequests = await (await fetch(BASE + '/__direct')).json();
  assert(
    directRequests.length === 0,
    `no request to a SAS Viya service went around the HTTP client, which handles HTTP 449 and the CSRF token (direct: ${directRequests.join(' | ') || 'none'})`
  );

  assert(pageErrors.length === 0, `no uncaught page errors during the whole session (got: ${pageErrors.join(' | ')})`);

  await browser.close();
  console.log('\n===== SUMMARY =====');
  console.log(results.join('\n'));
  console.log(`dialogs seen: ${dialogs.length ? dialogs.join(' | ') : 'none'}`);
})().catch((e) => {
  console.error('VERIFY FAILED:', e.message);
  process.exit(1);
});
