/**
 * A new item starts as a freshly loaded page starts one (issue #47): nothing
 * of the prompt-test, decision template or RAG setup that was open before is
 * left in the steps after Setup. Run against a FRESH mock instance.
 *
 * Every kind is driven the same way. A reference is taken first - a page that
 * was just loaded, a project chosen, a new item created - and then the page
 * is brought to a new item by other roads:
 *
 *   existing   an existing item with a saved configuration is opened, then a
 *              new one is created
 *   new        a new item is filled by hand in every later step, then another
 *              new one is created
 *   project    an existing item is opened, another project is created and
 *              chosen, and the new item is created there
 *   kind       (prompt and decision templates) an item of the other kind is
 *              opened, the kind is changed, a new item is created
 *
 * After each road the page is read as a whole and compared with the
 * reference; every field that differs is printed with both values. Read are,
 * per step: every input, text area and select with its value (and whether it
 * is disabled) whether it is shown or not, the text a person can read in the
 * step (which holds the runs of the tracker, results and notes), which
 * elements are hidden or disabled, and the row of steps. Left out are the two pickers
 * and the fields of the create dialogs, which name the item, and the entries
 * a list offers - the chosen entry is read.
 *
 * The other direction is checked as well, so that a reset cannot pass by
 * emptying too much: an existing item that is opened again shows exactly what
 * it showed the first time; an item with a saved configuration shows it; a
 * create dialog that is closed without creating changes nothing; and what was
 * typed into a new item is still there after a walk through the steps.
 *
 * What is typed while no prompt-test is open belongs to none: it is kept for
 * the prompt-test that is created for it, and makes way for a prompt-test
 * that brings a run of its own. (A RAG setup has no such state - its later
 * steps show nothing before a setup is open.)
 *
 * The suite goes on after a comparison that failed, so one run lists the
 * fields of every road; a line "[http 4xx/5xx]" names a request the mock
 * refused (the model that is switched off for one run, a ledger that does not
 * exist yet, the log of a job). The mock is found at VERIFY_BASE (default
 * http://localhost:4173).
 */
const { chromium } = require('playwright');

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
async function waitUntil(fn, label, timeout = 10000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await fn()) return;
    await sleep(100);
  }
  throw new Error('timeout waiting for: ' + label);
}

// The page with every part switched on, so that every step exists.
const APP_URL =
  `${BASE}/?modelRepositoryID=repo-1&llmProjectID=llm-proj&SCREndpoint=${BASE}/scr` +
  '&enableOptimization=true&computeContext=ctx&optimizeJobProgram=/Public/Jobs/Optimize-Prompt-DSPy&minOptimizeSamples=1' +
  '&enableRag=true&embeddingProjectID=emb-proj&ragComputeContext=rag-ctx';

// The names of the items this suite creates all begin alike, so that a text
// that names the item reads the same for each of them.
const NAME = 'Issue47';
const named = (text) => text.replace(/issue47[ _-]?[a-z0-9]+/gi, '<item>');

/**
 * Everything a builder shows, step by step. Runs in the page.
 *
 * controls  "<step> #<id>" (or the class and position of a control without
 *           an id) -> value, " [disabled]" appended
 * text      "<step>" -> the text a person can read in the pane (nothing that
 *           is hidden), white space folded
 * state     "<step> <path>" -> hidden / disabled / is-invalid / readonly, for
 *           the elements that are one of them (not for what a hidden
 *           element holds)
 * steps     the visible steps and the current one
 */
function readBuilder(prefix) {
  const root = document.getElementById(`app-obj-${prefix}`);
  const panePrefix = `app-obj-${prefix}-pane-`;
  const paneOf = (element) => {
    const pane = element.closest(`[id^="${panePrefix}"]`);
    return pane ? pane.id.slice(panePrefix.length) : '';
  };
  // The pickers and the create dialogs name the item that is open.
  const namesTheItem = (element) =>
    /(-dropdown|-combobox|-filter-name|-filter-user|Name|Description)$/.test(element.id) ||
    element.id === `app-obj-${prefix}-project` ||
    element.id === `app-obj-${prefix}-setup` ||
    Boolean(element.closest('.modal'));
  // An enrichment block of the RAG Builder carries a running number in its
  // ids, which goes on counting with every setup that is opened: the blocks
  // are named by their place instead.
  const blocks = [];
  const placed = (id) =>
    id.replace(/-enrich-(\d+)-/, (whole, number) => {
      if (!blocks.includes(number)) blocks.push(number);
      return `-enrich-${blocks.indexOf(number) + 1}-`;
    });
  const controls = {};
  const unnamed = {};
  root.querySelectorAll('input, textarea, select').forEach((element) => {
    const pane = paneOf(element);
    if (!pane || namesTheItem(element)) return;
    let name = element.id ? `#${placed(element.id)}` : '';
    if (!name) {
      const kind = `${element.tagName.toLowerCase()}.${[...element.classList].join('.')}`;
      unnamed[`${pane} ${kind}`] = (unnamed[`${pane} ${kind}`] || 0) + 1;
      name = `${kind} (${unnamed[`${pane} ${kind}`]})`;
    }
    const value = element.type === 'checkbox' || element.type === 'radio' ? String(element.checked) : element.value;
    controls[`${pane} ${name}`] = `${value}${element.disabled ? ' [disabled]' : ''}`;
  });
  const text = {};
  const state = {};
  root.querySelectorAll(`[id^="${panePrefix}"]`).forEach((pane) => {
    const key = pane.id.slice(panePrefix.length);
    // The Setup step names the item in its pickers, its buttons and its
    // messages; its fields are read above.
    if (key === 'setup') return;
    // What a person can read in the step: the text of everything that is
    // not hidden. (The pane itself is not asked - only one pane shows at a
    // time.) The entries a list offers are not read, the chosen one is
    // (above): the CAS pickers list what the site has, once they were opened.
    const shownText = (element) =>
      [...element.childNodes]
        .map((node) => {
          if (node.nodeType === Node.TEXT_NODE) return node.textContent;
          if (node.nodeType !== Node.ELEMENT_NODE || node.tagName === 'OPTION') return '';
          return getComputedStyle(node).display === 'none' ? '' : shownText(node);
        })
        .join('');
    text[key] = shownText(pane).replace(/\s+/g, ' ').trim();
    const walk = (element, path) => {
      const flags = [];
      // Its own display, whatever hides it: the attribute, a class or a style.
      // (The pane around it is not asked - only one pane shows at a time.)
      if (getComputedStyle(element).display === 'none') flags.push('hidden');
      if (element.disabled) flags.push('disabled');
      if (element.classList.contains('is-invalid')) flags.push('is-invalid');
      if (element.readOnly) flags.push('readonly');
      if (flags.length) state[`${key} ${path}${element.id ? `#${placed(element.id)}` : ''}`] = flags.join(' ');
      // What a hidden element holds is hidden with it.
      if (flags.includes('hidden')) return;
      [...element.children].forEach((child, index) => walk(child, `${path}/${child.tagName.toLowerCase()}[${index}]`));
    };
    [...pane.children].forEach((child, index) => walk(child, `${child.tagName.toLowerCase()}[${index}]`));
  });
  const steps = [...root.querySelectorAll(`#app-obj-${prefix}-stepper .pb-step`)]
    .filter((item) => !item.hidden)
    .map((item) => {
      const button = item.querySelector('.pb-step-button');
      return `${button.id.replace(`app-obj-${prefix}-step-`, '')}${item.classList.contains('is-current') ? '*' : ''}`;
    })
    .join(',');
  return { controls, text, state, steps };
}

const clip = (value) => (value.length > 110 ? `${value.slice(0, 110)}...` : value);
/** Where two texts part: what each holds between their common start and end. */
function textDifference(got, wanted) {
  let start = 0;
  while (start < got.length && start < wanted.length && got[start] === wanted[start]) start += 1;
  let end = 0;
  while (end < got.length - start && end < wanted.length - start && got[got.length - 1 - end] === wanted[wanted.length - 1 - end]) end += 1;
  return { got: got.slice(start, got.length - end), wanted: wanted.slice(start, wanted.length - end) };
}
/** The fields of `got` that are not as in `wanted`, one line each. */
function differences(got, wanted) {
  const lines = [];
  if (got.steps !== wanted.steps) lines.push(`steps: "${got.steps}" (expected "${wanted.steps}")`);
  for (const key of new Set([...Object.keys(got.controls), ...Object.keys(wanted.controls)])) {
    const a = got.controls[key];
    const b = wanted.controls[key];
    if (a === b) continue;
    if (b === undefined) lines.push(`field ${key}: "${clip(a)}" (expected no such field)`);
    else if (a === undefined) lines.push(`field ${key}: missing (expected "${clip(b)}")`);
    else lines.push(`field ${key}: "${clip(a)}" (expected "${clip(b)}")`);
  }
  for (const key of new Set([...Object.keys(got.text), ...Object.keys(wanted.text)])) {
    const a = named(got.text[key] || '');
    const b = named(wanted.text[key] || '');
    if (a === b) continue;
    const parted = textDifference(a, b);
    lines.push(`text of step ${key}: "${clip(parted.got)}" (expected "${clip(parted.wanted)}")`);
  }
  for (const key of new Set([...Object.keys(got.state), ...Object.keys(wanted.state)])) {
    const a = got.state[key] || 'shown';
    const b = wanted.state[key] || 'shown';
    if (a !== b) lines.push(`state ${key}: ${a} (expected ${b})`);
  }
  return lines;
}
/**
 * One comparison: a line of its own, and below it every field that differs.
 * `fieldsOnly` compares the fields and leaves the texts and the states out.
 */
function same(got, wanted, label, fieldsOnly = false) {
  const lines = differences(got, wanted).filter((line) => !fieldsOnly || line.startsWith('field '));
  step(lines.length === 0, `${label}${lines.length ? ` - ${lines.length} difference(s):` : ''}`);
  lines.forEach((line) => console.log(`      ${line}`));
  return lines.length === 0;
}

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const pageErrors = [];
  const dialogs = [];
  const newPage = async () => {
    const page = await browser.newPage({ viewport: { width: 1500, height: 1100 } });
    page.on('pageerror', (e) => {
      pageErrors.push(e.message);
      console.log('[pageerror]', e.message);
    });
    page.on('console', (m) => {
      if (m.type() === 'error') console.log('[console.error]', m.text());
    });
    page.on('response', (response) => {
      if (response.status() >= 400) console.log(`[http ${response.status()}]`, response.request().method(), response.url().replace(BASE, ''));
    });
    page.on('dialog', (d) => {
      dialogs.push(d.message());
      d.accept();
    });
    return page;
  };
  // Each road starts on a page of its own, as a person who opens the report.
  const road = async (name, run) => {
    console.log(`---- ${name}`);
    const page = await newPage();
    try {
      await run(page);
    } finally {
      await page.close();
    }
  };
  const itemCount = { n: 0 };
  const nextName = () => `${NAME} ${String.fromCharCode(65 + (itemCount.n++ % 26))}${itemCount.n > 26 ? itemCount.n : ''}`;
  // The requests of a page that were answered: a page is at rest when no
  // further one arrives for a moment.
  const atRest = async () => {
    let seen = -1;
    for (let round = 0; round < 60; round++) {
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
  const visibleSteps = (page, prefix) =>
    page.$$eval(
      `#app-obj-${prefix}-stepper .pb-step`,
      (items, start) => items.filter((item) => !item.hidden).map((item) => item.querySelector('.pb-step-button').id.replace(start, '')),
      `app-obj-${prefix}-step-`
    );
  /**
   * The page as a person finds it who walks through every step and comes
   * back to Setup: a step may fill itself only when it is opened.
   */
  const read = async (page, prefix) => {
    for (const key of await visibleSteps(page, prefix)) {
      await goStep(page, prefix, key);
      await atRest();
    }
    await goStep(page, prefix, 'setup');
    await atRest();
    return page.evaluate(readBuilder, prefix);
  };

  // ============================================================================
  // Prompt templates and decision templates (the Prompt Builder, LPB)
  // ============================================================================
  await fetch(BASE + '/__decision', { method: 'POST' });
  await fetch(BASE + '/__manifested', { method: 'POST', body: JSON.stringify({ on: true }) });

  const openPromptBuilder = async (page, kind) => {
    await page.goto(APP_URL);
    await page.waitForSelector('#LPB-project-dropdown', { timeout: 15000 });
    if (kind === 'decision') await page.click('#LPB-kind-decision');
    await atRest();
  };
  const chooseKind = async (page, kind) => {
    await goStep(page, 'LPB', 'setup');
    await page.click(`#LPB-kind-${kind}`);
    await atRest();
  };
  const choosePromptProject = async (page, projectId) => {
    await goStep(page, 'LPB', 'setup');
    await waitUntil(
      async () => (await page.$$eval('#LPB-project-dropdown option', (options) => options.map((option) => option.value))).includes(projectId),
      `project ${projectId} is listed`
    );
    await page.selectOption('#LPB-project-dropdown', projectId);
    await atRest();
  };
  const openPromptTest = async (page, modelId) => {
    await goStep(page, 'LPB', 'setup');
    await waitUntil(
      async () => (await page.$$eval('#LPB-prompt-dropdown option', (options) => options.map((option) => option.value))).includes(modelId),
      `prompt-test ${modelId} is listed`
    );
    await page.selectOption('#LPB-prompt-dropdown', modelId);
    await atRest();
  };
  const createPromptTest = async (page) => {
    const name = nextName();
    await goStep(page, 'LPB', 'setup');
    const before = await page.inputValue('#LPB-prompt-dropdown');
    await page.click('#LPB-modal-button-container button[data-bs-target="#promptBuilderCreatePromptModal"]');
    await page.waitForSelector('#promptBuilderCreatePromptModal.show');
    await page.fill('#promptBuilderCreatePromptName', name);
    await page.click('#promptBuilderCreatePromptModal .btn-primary');
    await page.waitForSelector('.modal.show', { state: 'detached' });
    await waitUntil(async () => {
      const now = await page.inputValue('#LPB-prompt-dropdown');
      return now !== before && now.startsWith('model-new-');
    }, `the new prompt-test ${name} is selected`);
    await atRest();
    return page.inputValue('#LPB-prompt-dropdown');
  };
  const createPromptProject = async (page) => {
    await goStep(page, 'LPB', 'setup');
    await page.click('#LPB-modal-button-container button[data-bs-target="#promptBuilderCreateProjectModal"]');
    await page.waitForSelector('#promptBuilderCreateProjectModal.show');
    await page.fill('#promptBuilderCreateProjectName', nextName());
    await page.click('#promptBuilderCreateProjectModal .btn-primary');
    await page.waitForSelector('.modal.show', { state: 'detached' });
    await waitUntil(async () => (await page.inputValue('#LPB-project-dropdown')) === 'proj-new', 'the new project is selected');
    await atRest();
  };
  // A create dialog that is opened, filled and closed again without creating.
  const cancelCreate = async (page, modal, nameField) => {
    await goStep(page, modal.includes('RGB') ? 'RGB' : 'LPB', 'setup');
    await page.click(`button[data-bs-target="${modal}"]`);
    await page.waitForSelector(`${modal}.show`);
    await page.fill(nameField, 'never created');
    await page.click(`${modal} .btn-secondary`);
    await page.waitForSelector('.modal.show', { state: 'detached' });
    await page.fill(nameField, '');
  };
  const lpb = (suffix) => `#app-obj-LPB-${suffix}`;

  // ---- prompt templates ----------------------------------------------------------
  let promptReference;
  // The manifested prompt-test as a fresh page shows it.
  let manifestedReference;
  await road('prompt template: the reference - a fresh page, a new prompt-test', async (page) => {
    await openPromptBuilder(page, 'llm');
    const untouched = await read(page, 'LPB');
    await choosePromptProject(page, 'proj-1');
    await createPromptTest(page);
    promptReference = await read(page, 'LPB');
    assert(promptReference.steps === 'setup*,build,optimize,finalize', `a prompt template has the steps Setup, Build & Test, Optimize and Finalize (got: ${promptReference.steps})`);
    assert(
      promptReference.controls['build #app-obj-LPB-system-prompt'] === '' && promptReference.controls['build #temperature0'] === '0.7' && promptReference.controls['finalize #app-obj-LPB-pet-out-run_time'] === 'true',
      'a new prompt-test on a fresh page has empty prompts and the defaults where there are some (temperature 0.7, every output ticked)'
    );
    // The texts differ by nature: the Optimize step says what is missing.
    same(promptReference, untouched, 'every field of the later steps holds for a new prompt-test on a fresh page what it holds before anything is selected', true);
  });

  // What opening the manifested prompt-test has to put on the page.
  const manifestedIsShown = (shown) =>
    shown.controls['build #app-obj-LPB-system-prompt'] === 'You answer for {{customer}}.' &&
    shown.controls['build #app-obj-LPB-user-prompt'] === 'Rate the order of {{amount}} EUR.' &&
    shown.controls['build input.form-control.pb-var-name (2)'] === 'amount' &&
    shown.controls['build input.form-control.pb-var-value (2)'] === '12.5' &&
    shown.controls['build #model0'] === 'true' &&
    shown.controls['build #temperature0'] === '0.9' &&
    shown.controls['build #model1'] === 'true' &&
    shown.controls['build #temperature1'] === '0.2' &&
    shown.controls['build #app-obj-LPB-judge-council-toggle'] === 'true' &&
    shown.controls['build #app-obj-LPB-judge-panel-1'] === 'true' &&
    shown.controls['build #app-obj-LPB-judge-chairman-model'] === 'contrarian_judge' &&
    shown.controls['build #app-obj-LPB-judge-include-self'] === 'true' &&
    shown.controls['build #app-obj-LPB-judge-auto'] === 'true' &&
    shown.controls['finalize #app-obj-LPB-pet-manifest-integrated'] === 'true' &&
    shown.controls['finalize #app-obj-LPB-pet-out-prompt_length'] === 'false' &&
    shown.controls['finalize input.form-control.pb-outvar-name (1)'] === 'sentiment';
  // The saved runs in the tracker of the Prompt Builder.
  const savedRuns = (page) => page.$$eval('#app-obj-LPB-pet .pet-run-delete', (buttons) => buttons.length);

  await road('prompt template: an existing prompt-test, then a new one', async (page) => {
    await openPromptBuilder(page, 'llm');
    await choosePromptProject(page, 'proj-1');
    await openPromptTest(page, 'model-used');
    const existing = await read(page, 'LPB');
    manifestedReference = existing;
    assert(manifestedIsShown(existing) && (await savedRuns(page)) === 2, `opening the manifested prompt-test fills Build & Test and Finalize with its best run: both prompts, two variables, two LLMs with their temperatures, the council with its chairman, the manifest configuration and the two saved runs (runs: ${await savedRuns(page)})`);

    // A dialog that is closed without creating leaves the open prompt-test alone.
    await cancelCreate(page, '#promptBuilderCreatePromptModal', '#promptBuilderCreatePromptName');
    same(await read(page, 'LPB'), existing, 'a create dialog that is closed without creating changes nothing of the open prompt-test');

    await resetLog();
    const created = await createPromptTest(page);
    same(await read(page, 'LPB'), promptReference, 'existing -> new: the new prompt-test starts as on a fresh page');
    // Nothing of the other prompt-test is written to the new one either.
    await goStep(page, 'LPB', 'build');
    dialogs.length = 0;
    await page.click(lpb('pet-save-button'));
    await waitUntil(async () => dialogs.length > 0, 'Save Experiments answers on a new prompt-test');
    await atRest();
    const written = (await getLog()).filter((entry) => entry.method === 'POST' && entry.url.startsWith(`/modelRepository/models/${created}/contents`));
    step(written.length === 0, `Save Experiments on the new prompt-test saves no run of the one that was open before (dialog: "${dialogs[0]}", uploads: ${written.length})`);

    // And back: the existing one shows what it showed before.
    await openPromptTest(page, 'model-used');
    same(await read(page, 'LPB'), existing, 'existing -> new -> the existing one again: it shows what it showed the first time');
    // An existing prompt-test without a saved run has nothing to put there.
    await openPromptTest(page, 'model-free');
    const empty = await read(page, 'LPB');
    same(empty, promptReference, 'an existing prompt-test without a saved run, opened after one with a configuration, shows none of it');
    await openPromptTest(page, 'model-used');
    same(await read(page, 'LPB'), existing, 'and the one with the configuration shows it again afterwards');
    // No prompt-test at all.
    await page.selectOption('#LPB-prompt-dropdown', { index: 0 });
    await atRest();
    const none = await read(page, 'LPB');
    step(
      none.controls['build #app-obj-LPB-system-prompt'] === '' && none.controls['build #model0'] === 'false' && none.controls['finalize #app-obj-LPB-pet-manifest-integrated'] === 'false' && !('build input.form-control.pb-var-name (1)' in none.controls),
      `the placeholder entry, chosen after a prompt-test with a configuration, leaves none of it (system prompt: "${none.controls['build #app-obj-LPB-system-prompt']}", first LLM ticked: ${none.controls['build #model0']}, LLM call included: ${none.controls['finalize #app-obj-LPB-pet-manifest-integrated']}, variables: ${Object.keys(none.controls).filter((key) => key.includes('pb-var-name')).length})`
    );
  });

  // Fill every later step of an open prompt template by hand.
  const fillPromptTemplate = async (page) => {
    await goStep(page, 'LPB', 'build');
    await page.fill(lpb('system-prompt'), 'Typed system prompt for {{topic}}');
    await page.fill(lpb('user-prompt'), 'Typed user prompt');
    await page.click(`${lpb('pane-build')} button:has-text("Add Variable")`);
    await page.fill(`${lpb('variables')} .pb-var-name`, 'topic');
    await page.fill(`${lpb('variables')} .pb-var-description`, 'What it is about');
    await page.fill(`${lpb('variables')} .pb-var-value`, 'typed value');
    await page.check('#model0');
    await page.fill('#temperature0', '0.1');
    await page.check('#model1');
    await page.selectOption(lpb('judge-model'), 'demo_llm');
    await page.check(lpb('judge-council-toggle'));
    await page.check(lpb('judge-panel-1'));
    await page.check(lpb('judge-chairman-toggle'));
    await page.selectOption(lpb('judge-chairman-model'), 'second_llm');
    await page.check(lpb('judge-include-self'));
    // One of the two LLMs does not answer: the run leaves a line that says so.
    await fetch(BASE + '/__failmodel', { method: 'POST', body: JSON.stringify({ name: 'second_llm', status: 503 }) });
    await page.click(lpb('run-experiment'));
    await waitUntil(async () => (await page.$$(`${lpb('pet')} .accordion-item`)).length > 0, 'the typed prompt was run');
    await waitUntil(async () => (await page.textContent(lpb('run-experiment'))).trim() === 'Run Experiments', 'the run has ended');
    await fetch(BASE + '/__failmodel', { method: 'POST', body: '{}' });
    await atRest();
    await goStep(page, 'LPB', 'optimize');
    await page.selectOption(lpb('optimize-target'), { index: 1 });
    await page.selectOption(lpb('optimize-metric'), 'judge');
    await page.selectOption(lpb('optimize-judge-model'), { index: 1 });
    await page.selectOption(lpb('optimize-optimizer'), { index: 1 });
    await page.fill(lpb('optimize-max-demos'), '7');
    await page.check(lpb('optimize-dataset-source-cas'));
    await atRest();
    await goStep(page, 'LPB', 'finalize');
    await page.check(lpb('pet-manifest-integrated'));
    await page.uncheck(lpb('pet-out-run_time'));
    await page.click(`${lpb('pane-finalize')} button:has-text("Add Output Variable")`);
    await page.fill(`${lpb('pet-outvars')} .pb-outvar-name`, 'typed_output');
    await page.fill(`${lpb('pet-outvars')} .pb-outvar-default`, 'typed default');
    await atRest();
  };
  const typedPromptIsShown = (shown) =>
    shown.controls['build #app-obj-LPB-system-prompt'] === 'Typed system prompt for {{topic}}' &&
    shown.controls['build #app-obj-LPB-user-prompt'] === 'Typed user prompt' &&
    shown.controls['build input.form-control.pb-var-value (1)'] === 'typed value' &&
    shown.controls['build #temperature0'] === '0.1' &&
    shown.controls['build #app-obj-LPB-judge-chairman-model'] === 'second_llm' &&
    shown.controls['optimize #app-obj-LPB-optimize-max-demos'].startsWith('7') &&
    shown.controls['optimize #app-obj-LPB-optimize-dataset-source-cas'] === 'true' &&
    shown.controls['finalize #app-obj-LPB-pet-out-run_time'] === 'false' &&
    shown.controls['finalize input.form-control.pb-outvar-name (1)'] === 'typed_output';

  await road('prompt template: a new prompt-test right after a new one', async (page) => {
    await openPromptBuilder(page, 'llm');
    await choosePromptProject(page, 'proj-1');
    await createPromptTest(page);
    await fillPromptTemplate(page);
    // What is typed into a prompt-test that is being made stays while the
    // person only moves between the steps, or opens a dialog and closes it.
    const typed = await read(page, 'LPB');
    const failedLine = ((await page.textContent(lpb('run-error'))) || '').trim();
    assert(
      typedPromptIsShown(typed) && /second_llm/.test(failedLine) && typed.text.build.includes(failedLine),
      `what was typed into the new prompt-test is on the page after a walk through every step: prompts, variable, LLM options, judges, the optimization settings, the manifest configuration and the line about the LLM that failed in its run (line: "${failedLine}")`
    );
    await cancelCreate(page, '#promptBuilderCreatePromptModal', '#promptBuilderCreatePromptName');
    same(await read(page, 'LPB'), typed, 'a second walk through the steps and a create dialog that is closed without creating lose nothing of it');
    await createPromptTest(page);
    same(await read(page, 'LPB'), promptReference, 'new -> new: the second new prompt-test starts as on a fresh page');
  });

  await road('prompt template: a new prompt-test in another project than the one that was open', async (page) => {
    await openPromptBuilder(page, 'llm');
    await choosePromptProject(page, 'proj-1');
    await openPromptTest(page, 'model-used');
    await atRest();
    await createPromptProject(page);
    await createPromptTest(page);
    same(await read(page, 'LPB'), promptReference, 'existing -> another project -> new: the new prompt-test starts as on a fresh page');
  });

  // What is typed into Build & Test while no prompt-test is open belongs to
  // none. It is kept for the prompt-test that is created for it, and it makes
  // way for a prompt-test that brings a run of its own.
  const typeWithoutPromptTest = async (page) => {
    await goStep(page, 'LPB', 'build');
    await page.fill(lpb('system-prompt'), 'Typed before a prompt-test existed');
    await page.fill(lpb('user-prompt'), 'Typed user prompt');
    await page.click(`${lpb('pane-build')} button:has-text("Add Variable")`);
    await page.fill(`${lpb('variables')} .pb-var-name`, 'early');
    await page.check('#model1');
    await page.fill('#temperature1', '0.4');
    await page.selectOption(lpb('judge-model'), 'second_llm');
    await page.check(lpb('judge-auto'));
    // Something no saved run holds, so that a run cannot be what takes it away.
    await goStep(page, 'LPB', 'optimize');
    await page.fill(lpb('optimize-max-demos'), '9');
  };
  await road('prompt template: typed before a prompt-test was open', async (page) => {
    await openPromptBuilder(page, 'llm');
    await choosePromptProject(page, 'proj-1');
    await typeWithoutPromptTest(page);
    await createPromptTest(page);
    const kept = await read(page, 'LPB');
    step(
      kept.controls['build #app-obj-LPB-system-prompt'] === 'Typed before a prompt-test existed' &&
        kept.controls['build input.form-control.pb-var-name (1)'] === 'early' &&
        kept.controls['build #model1'] === 'true' &&
        kept.controls['build #temperature1'] === '0.4' &&
        kept.controls['build #app-obj-LPB-judge-model'] === 'second_llm' &&
        kept.controls['build #app-obj-LPB-judge-auto'] === 'true' &&
        kept.controls['optimize #app-obj-LPB-optimize-max-demos'] === '9',
      `nothing open -> typed -> new: what was typed is kept for the prompt-test that is created for it (system prompt: "${kept.controls['build #app-obj-LPB-system-prompt']}", variable: ${kept.controls['build input.form-control.pb-var-name (1)']}, second LLM ticked: ${kept.controls['build #model1']} at ${kept.controls['build #temperature1']}, judge: ${kept.controls['build #app-obj-LPB-judge-model']}, examples of an optimization: ${kept.controls['optimize #app-obj-LPB-optimize-max-demos']})`
    );
    // From here on it belongs to that prompt-test, and goes with it.
    await createPromptTest(page);
    same(await read(page, 'LPB'), promptReference, 'and the next new prompt-test starts as on a fresh page');
  });
  await road('prompt template: typed before a prompt-test with a run of its own was opened', async (page) => {
    await openPromptBuilder(page, 'llm');
    await choosePromptProject(page, 'proj-1');
    await typeWithoutPromptTest(page);
    await openPromptTest(page, 'model-used');
    same(await read(page, 'LPB'), manifestedReference, 'nothing open -> typed -> an existing prompt-test with a best run: it shows what it shows on a fresh page, not its run mixed with what was typed');
  });

  // ---- decision templates --------------------------------------------------------
  let decisionReference;
  await road('decision template: the reference - a fresh page, a new decision template', async (page) => {
    await openPromptBuilder(page, 'decision');
    const untouched = await read(page, 'LPB');
    await choosePromptProject(page, 'proj-2');
    await createPromptTest(page);
    decisionReference = await read(page, 'LPB');
    assert(decisionReference.steps === 'setup*,build,evaluate,finalize', `a decision template has the steps Setup, Build & Test, Evaluate and Finalize (got: ${decisionReference.steps})`);
    assert(
      decisionReference.controls['build #app-obj-LPB-decision-state'] === '' && decisionReference.controls['build #decision-model0'] === 'false' && decisionReference.controls['evaluate #app-obj-LPB-evaluate-max-rows'] === '100',
      'a new decision template on a fresh page has an empty state template, no decision model ticked and the defaults where there are some (100 rows to evaluate)'
    );
    same(decisionReference, untouched, 'every field of the later steps holds for a new decision template on a fresh page what it holds before anything is selected', true);
  });

  const ticketRoutingIsShown = (shown) =>
    shown.controls['build #app-obj-LPB-decision-state'].startsWith('Subject: {{subject}}') &&
    shown.controls['build input.form-control.pb-var-name (2)'] === 'body' &&
    shown.controls['build #decision-model0'] === 'true' &&
    Object.keys(shown.controls).filter((key) => key.includes('pb-question-id')).length === 2;

  await road('decision template: an existing template, then a new one', async (page) => {
    await openPromptBuilder(page, 'decision');
    await choosePromptProject(page, 'proj-2');
    await openPromptTest(page, 'model-dec');
    // The evaluation of the saved runs: a result of this template.
    await goStep(page, 'LPB', 'evaluate');
    await page.click(lpb('evaluate-run'));
    await waitUntil(async () => ((await page.textContent(lpb('evaluate-results'))) || '').trim().length > 0, 'the saved runs were evaluated');
    await page.check(lpb('evaluate-source-cas'));
    await page.fill(lpb('evaluate-max-rows'), '7');
    await atRest();
    await goStep(page, 'LPB', 'finalize');
    await page.uncheck(lpb('pet-decout-run_time'));
    const existing = await read(page, 'LPB');
    assert(ticketRoutingIsShown(existing) && (await savedRuns(page)) === 1, `opening the decision template fills Build & Test with its saved run: the state template, two variables, two questions, the decision model and the run itself (runs: ${await savedRuns(page)})`);
    await cancelCreate(page, '#promptBuilderCreatePromptModal', '#promptBuilderCreatePromptName');
    same(await read(page, 'LPB'), existing, 'a create dialog that is closed without creating changes nothing of the open decision template');

    await createPromptTest(page);
    same(await read(page, 'LPB'), decisionReference, 'existing -> new: the new decision template starts as on a fresh page');
    await openPromptTest(page, 'model-dec');
    const again = await read(page, 'LPB');
    step(ticketRoutingIsShown(again) && (await savedRuns(page)) === 1, 'existing -> new -> the existing one again: its saved run is back on the page');
  });

  const fillDecisionTemplate = async (page) => {
    await goStep(page, 'LPB', 'build');
    await page.fill(lpb('decision-state'), 'Typed state about {{topic}}');
    await page.click(`${lpb('pane-build')} button:has-text("Add Variable")`);
    await page.fill(`${lpb('variables')} .pb-var-name`, 'topic');
    await page.fill(`${lpb('variables')} .pb-var-value`, 'typed value');
    await page.click(lpb('decision-add-question'));
    const row = page.locator('.pb-question-row').last();
    await row.locator('.pb-question-id').fill('typed_question');
    await row.locator('.pb-question-type').selectOption('noul');
    await row.locator('.pb-question-instructions').fill('Is the text about an invoice that was paid twice?');
    await row.locator('.pb-question-expected').fill('no');
    await page.check('#decision-model1');
    await page.click(lpb('run-experiment'));
    await waitUntil(async () => (await page.$$(`${lpb('pet')} .accordion-item`)).length > 0, 'the typed decision template was run');
    await waitUntil(async () => (await page.textContent(lpb('run-experiment'))).trim() === 'Run Experiments', 'the run has ended');
    await atRest();
    await goStep(page, 'LPB', 'evaluate');
    await page.click(lpb('evaluate-run'));
    await waitUntil(async () => ((await page.textContent(lpb('evaluate-results'))) || '').trim().length > 0, 'the run was evaluated');
    await page.check(lpb('evaluate-source-cas'));
    await page.fill(lpb('evaluate-max-rows'), '7');
    await atRest();
    await goStep(page, 'LPB', 'finalize');
    await page.uncheck(lpb('pet-decout-confidence'));
    await atRest();
  };
  const typedDecisionIsShown = (shown) =>
    shown.controls['build #app-obj-LPB-decision-state'] === 'Typed state about {{topic}}' &&
    shown.controls['build input.form-control.pb-var-value (1)'] === 'typed value' &&
    Object.values(shown.controls).includes('typed_question') &&
    shown.controls['build #decision-model1'] === 'true' &&
    shown.controls['evaluate #app-obj-LPB-evaluate-max-rows'] === '7' &&
    shown.controls['finalize #app-obj-LPB-pet-decout-confidence'] === 'false';

  await road('decision template: a new template right after a new one', async (page) => {
    await openPromptBuilder(page, 'decision');
    await choosePromptProject(page, 'proj-2');
    await createPromptTest(page);
    await fillDecisionTemplate(page);
    const typed = await read(page, 'LPB');
    assert(typedDecisionIsShown(typed), 'what was typed into the new decision template is on the page after a walk through every step: state template, variable, question, decision model, the evaluation settings and the outputs');
    await cancelCreate(page, '#promptBuilderCreatePromptModal', '#promptBuilderCreatePromptName');
    same(await read(page, 'LPB'), typed, 'a second walk through the steps and a create dialog that is closed without creating lose nothing of it');
    await createPromptTest(page);
    same(await read(page, 'LPB'), decisionReference, 'new -> new: the second new decision template starts as on a fresh page');
  });

  await road('decision template: a new template in another project than the one that was open', async (page) => {
    await openPromptBuilder(page, 'decision');
    await choosePromptProject(page, 'proj-2');
    await openPromptTest(page, 'model-dec');
    await createPromptProject(page);
    await createPromptTest(page);
    same(await read(page, 'LPB'), decisionReference, 'existing -> another project -> new: the new decision template starts as on a fresh page');
  });

  // ---- from one kind to the other ------------------------------------------------
  await road('a prompt-test of one kind, then a new one of the other kind', async (page) => {
    await openPromptBuilder(page, 'llm');
    await choosePromptProject(page, 'proj-1');
    await openPromptTest(page, 'model-used');
    await chooseKind(page, 'decision');
    await choosePromptProject(page, 'proj-2');
    await createPromptTest(page);
    same(await read(page, 'LPB'), decisionReference, 'an existing prompt template -> the decision kind -> new: the new decision template starts as on a fresh page');
    await openPromptTest(page, 'model-dec');
    await chooseKind(page, 'llm');
    await choosePromptProject(page, 'proj-1');
    await createPromptTest(page);
    same(await read(page, 'LPB'), promptReference, 'an existing decision template -> the LLM kind -> new: the new prompt template starts as on a fresh page');
  });

  // ============================================================================
  // RAG setups (the RAG Builder, RGB)
  // ============================================================================
  const rgb = (suffix) => `#app-obj-RGB-${suffix}`;
  const ragState = async () => (await fetch(BASE + '/__ragstate')).json();
  const openRagBuilder = async (page) => {
    await page.goto(APP_URL);
    await page.waitForSelector('#LPB-kind-rag', { state: 'attached', timeout: 15000 });
    await page.click('#LPB-kind-rag');
    await page.waitForSelector('#app-obj-RGB-stepper', { state: 'visible', timeout: 15000 });
    await atRest();
  };
  const chooseRagProject = async (page, projectId) => {
    await goStep(page, 'RGB', 'setup');
    await waitUntil(async () => (await page.$$eval(`${rgb('project')} option`, (options) => options.map((option) => option.value))).includes(projectId), `RAG project ${projectId} is listed`);
    await page.selectOption(rgb('project'), projectId);
    await atRest();
  };
  const openRagSetup = async (page, setupId) => {
    await goStep(page, 'RGB', 'setup');
    await waitUntil(async () => (await page.$$eval(`${rgb('setup')} option`, (options) => options.map((option) => option.value))).includes(setupId), `RAG setup ${setupId} is listed`);
    await page.selectOption(rgb('setup'), setupId);
    await atRest();
  };
  const createRagSetup = async (page) => {
    const name = nextName();
    await goStep(page, 'RGB', 'setup');
    await page.click(`[data-bs-target="${rgb('create-setupModal')}"]`);
    await page.waitForSelector(`${rgb('create-setupModal')}.show`);
    await page.fill(rgb('create-setupName'), name);
    await page.click(`${rgb('create-setupModal')} .btn-primary`);
    await page.waitForSelector('.modal.show', { state: 'detached' });
    await waitUntil(async () => {
      const made = (await ragState()).models.find((model) => model.name === name);
      return Boolean(made) && (await page.inputValue(rgb('setup'))) === made.id;
    }, `the new RAG setup ${name} is selected`);
    await atRest();
  };
  const createRagProject = async (page) => {
    await goStep(page, 'RGB', 'setup');
    await page.click(`[data-bs-target="${rgb('create-projectModal')}"]`);
    await page.waitForSelector(`${rgb('create-projectModal')}.show`);
    await page.fill(rgb('create-projectName'), nextName());
    await page.click(`${rgb('create-projectModal')} .btn-primary`);
    await page.waitForSelector('.modal.show', { state: 'detached' });
    await waitUntil(async () => (await page.inputValue(rgb('project'))).startsWith('rag-proj-new-'), 'the new RAG project is selected');
    await atRest();
  };

  let ragReference;
  await road('RAG setup: the reference - a fresh page, a new setup', async (page) => {
    await openRagBuilder(page);
    await chooseRagProject(page, 'rag-proj');
    await createRagSetup(page);
    ragReference = await read(page, 'RGB');
    assert(ragReference.steps === 'setup*,documents,store,run', `a RAG setup has the steps Setup, Documents & chunks, Store & output and Run & test (got: ${ragReference.steps})`);
    assert(
      ragReference.controls['documents #app-obj-RGB-source-path'] === '' && ragReference.controls['documents #app-obj-RGB-token-limit'] === '256' && ragReference.controls['run #app-obj-RGB-test-topk'] === '5',
      'a new setup on a fresh page has no document folder and the defaults where there are some (a window of 256 tokens, five chunks per question)'
    );
  });

  const handbookIsShown = (shown) =>
    shown.controls['store #app-obj-RGB-tables-prefix'] === 'RAGDEMO' &&
    shown.controls['store #app-obj-RGB-backend'] === 'pgvector' &&
    shown.controls['documents #app-obj-RGB-source-path'] !== '' &&
    shown.controls['documents #app-obj-RGB-embed-model'] !== '';

  await road('RAG setup: an existing setup, then a new one', async (page) => {
    await openRagBuilder(page);
    await chooseRagProject(page, 'rag-proj');
    await openRagSetup(page, 'rag-setup-1');
    // What the last step holds of this setup: its ledger, a question and the
    // chunks that came back for it.
    await goStep(page, 'RGB', 'store');
    await page.click(rgb('save'));
    await waitUntil(async () => Boolean(await page.$(`${rgb('save-result')} .alert`)), 'the setup was saved and the step says so');
    await atRest();
    await goStep(page, 'RGB', 'run');
    await page.click(rgb('launch'));
    await waitUntil(async () => (await page.textContent(rgb('run-state'))) === 'completed', 'an ingestion of the setup has run', 30000);
    await page.click(rgb('ledger'));
    await waitUntil(async () => page.isVisible(rgb('ledger-panel')), 'the ledger of the setup is shown');
    await atRest();
    await page.click(rgb('test'));
    await page.waitForSelector(rgb('test-question'), { state: 'visible' });
    await page.fill(rgb('test-question'), 'What does the handbook say about travel?');
    await page.fill(rgb('test-topk'), '3');
    await page.click(rgb('test-run'));
    await waitUntil(async () => ((await page.textContent(rgb('test-results'))) || '').trim().length > 0, 'the question was answered with chunks', 30000);
    await atRest();
    const existing = await read(page, 'RGB');
    assert(handbookIsShown(existing), 'opening the saved setup fills the later steps: document folder, embedding model, vector database and table prefix');
    await goStep(page, 'RGB', 'run');
    const testPanelOpen = await page.isVisible(rgb('test-panel'));
    step(
      /saved/i.test(existing.text.store) && /RAGDEMO_LEDGER/.test(existing.text.run) && /completed/.test(existing.text.run) && existing.controls['run #app-obj-RGB-test-question'] !== '' && testPanelOpen,
      'and it holds what was done with it: the note of the save, the ingestion run, the ledger, the question and the chunks that came back'
    );
    await cancelCreate(page, rgb('create-setupModal'), rgb('create-setupName'));
    same(await read(page, 'RGB'), existing, 'a create dialog that is closed without creating changes nothing of the open setup');

    await createRagSetup(page);
    same(await read(page, 'RGB'), ragReference, 'existing -> new: the new setup starts as on a fresh page');
    await openRagSetup(page, 'rag-setup-1');
    step(handbookIsShown(await read(page, 'RGB')), 'existing -> new -> the existing one again: its saved configuration is back on the page');
  });

  const fillRagSetup = async (page) => {
    await goStep(page, 'RGB', 'documents');
    await page.fill(rgb('source-path'), '/typed/folder');
    await page.check(rgb('include-code'));
    await page.selectOption(rgb('embed-model'), { index: 1 });
    await page.selectOption(rgb('chunker'), { index: 1 });
    await page.fill(rgb('token-limit'), '128');
    await page.dispatchEvent(rgb('token-limit'), 'change');
    await goStep(page, 'RGB', 'store');
    await page.selectOption(rgb('backend'), 'pgvector');
    await page.fill(rgb('collection'), 'typed_collection');
    await page.fill(rgb('tables-prefix'), 'TYPED');
    await page.selectOption(rgb('tables-caslib'), { index: 1 });
    await goStep(page, 'RGB', 'run');
    await page.click(rgb('ledger'));
    await atRest();
  };
  const typedRagIsShown = (shown) =>
    shown.controls['documents #app-obj-RGB-source-path'] === '/typed/folder' &&
    shown.controls['documents #app-obj-RGB-include-code'] === 'true' &&
    shown.controls['documents #app-obj-RGB-token-limit'] === '128' &&
    shown.controls['store #app-obj-RGB-collection'] === 'typed_collection' &&
    shown.controls['store #app-obj-RGB-tables-prefix'] === 'TYPED';

  await road('RAG setup: a new setup right after a new one', async (page) => {
    await openRagBuilder(page);
    await chooseRagProject(page, 'rag-proj');
    await createRagSetup(page);
    await fillRagSetup(page);
    const typed = await read(page, 'RGB');
    assert(typedRagIsShown(typed), 'what was typed into the new setup is on the page after a walk through every step: document folder, chunking, vector database, collection and table prefix');
    await cancelCreate(page, rgb('create-setupModal'), rgb('create-setupName'));
    same(await read(page, 'RGB'), typed, 'a second walk through the steps and a create dialog that is closed without creating lose nothing of it');
    await createRagSetup(page);
    same(await read(page, 'RGB'), ragReference, 'new -> new: the second new setup starts as on a fresh page');
  });

  await road('RAG setup: a new setup in another project than the one that was open', async (page) => {
    await openRagBuilder(page);
    await chooseRagProject(page, 'rag-proj');
    await openRagSetup(page, 'rag-setup-1');
    await goStep(page, 'RGB', 'run');
    await page.click(rgb('ledger'));
    await atRest();
    await createRagProject(page);
    await createRagSetup(page);
    same(await read(page, 'RGB'), ragReference, 'existing -> another project -> new: the new setup starts as on a fresh page');
  });

  // ============================================================================
  // The result of an optimization (last: a job the mock has launched stays
  // launched)
  // ============================================================================
  await road('prompt template: an optimization was run for the prompt-test that was open', async (page) => {
    await openPromptBuilder(page, 'llm');
    await choosePromptProject(page, 'proj-1');
    await openPromptTest(page, 'model-used');
    await goStep(page, 'LPB', 'optimize');
    await page.selectOption(lpb('optimize-target'), 'demo_llm');
    await waitUntil(async () => !(await page.isDisabled(lpb('optimize-run'))), 'the optimization can be started');
    await page.click(lpb('optimize-run'));
    await waitUntil(async () => /Load|optimi/i.test((await page.textContent(lpb('optimize-result'))) || '') && !(await page.isDisabled(lpb('optimize-run'))), 'the optimization has ended and shows its result', 60000);
    await atRest();
    const result = ((await page.textContent(lpb('optimize-result'))) || '').replace(/\s+/g, ' ').trim();
    assert(result.length > 0 && (await page.isVisible(lpb('optimize-log'))), `the Optimize step shows the result and the log of the run (result: ${clip(result)})`);
    await createPromptTest(page);
    same(await read(page, 'LPB'), promptReference, 'an optimization of the existing prompt-test -> new: the new prompt-test starts as on a fresh page, without the result and the log of that run');
  });

  // ---- every request to SAS Viya went through the HTTP client of the app -------
  const directRequests = await (await fetch(BASE + '/__direct')).json();
  step(
    directRequests.length === 0,
    `no request to a SAS Viya service went around the HTTP client, which handles HTTP 449 and the CSRF token (direct: ${directRequests.join(' | ') || 'none'})`
  );

  step(pageErrors.length === 0, `no uncaught page errors (got: ${pageErrors.join(' | ') || 'none'})`);
  await browser.close();
  console.log('\n===== SUMMARY =====');
  console.log(results.join('\n'));
  console.log(`${results.filter((r) => r.startsWith('OK')).length} checks passed, ${results.filter((r) => r.startsWith('FAIL')).length} failed`);
})().catch((e) => {
  console.error('VERIFY FAILED:', e.message);
  process.exit(1);
});
