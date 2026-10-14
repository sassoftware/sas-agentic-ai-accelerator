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
 *   in flight  something of the item that is left is still running: an
 *              evaluation over a CAS table, an optimization job, a retrieval
 *              test, an ingestion, the reading of the item itself, the
 *              reading of the prompts of its enrichment. Its answer arrives
 *              after the next item is on the page
 *
 * After each road the page is read as a whole and compared with the
 * reference; every field that differs is printed with both values. Read are,
 * per step: every input, text area and select with its value (and whether it
 * is disabled) whether it is shown or not, the entries every list offers, the
 * text a person can read in the step (which holds the runs of the tracker,
 * results and notes), every text the step holds in what is hidden (a result
 * that waits in a closed panel is shown by the next click), which elements
 * are hidden, disabled or folded open - inside a part that is hidden itself
 * as well -, what every element carries beside its text (its classes, the
 * styles set on it apart from its display, its other attributes), and the
 * row of steps. Left out are the two pickers and the fields of the create
 * dialogs, which name the item.
 *
 * The page with no item open is compared the same way, with a fresh page on
 * which a project is chosen and nothing else: after another project or kind
 * was chosen, after the placeholder entry of a picker, after a delete. A
 * project that is chosen draws nothing into the later steps, so whatever is
 * there then was left by the item before.
 *
 * A comparison only sees a field that a road has changed. So the roads that
 * fill an item by hand are checked themselves: every field of the later
 * steps has to be changed by one of them (the list of the fields that none
 * changes is part of a check), or a field that is added to a step and not
 * put back for a new item would pass. One LLM of the fixtures cannot be
 * selected on the page of these roads - the credential domain holds no key
 * for it - so a road of its own ticks it on a page whose domain does.
 *
 * The other direction is checked as well, so that a reset cannot pass by
 * emptying too much: an existing item that is opened again shows exactly what
 * it showed the first time; an item with a saved configuration shows it; a
 * create dialog that is closed without creating changes nothing; and what was
 * typed into a new item is still there after a walk through the steps.
 *
 * What is typed while no prompt-test is open belongs to none: it is kept for
 * the prompt-test that is created for it, and makes way for a prompt-test
 * that brings a run of its own - also when two prompt-tests are chosen one
 * right after the other and the first is never drawn. (A RAG setup has no
 * such state - its later steps show nothing before a setup is open.)
 *
 * A job that is still running is another matter than its leftovers: one
 * optimization runs at a time, so a prompt-test opened meanwhile says that
 * one is running, and the prompt-test the job runs for shows its state and
 * its log again when it is opened again.
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
 * lists     "<step> #<id>" -> the entries a select offers, whether it is
 *           shown or not
 * text      "<step>" -> the text a person can read in the pane (nothing that
 *           is hidden), white space folded
 * held      "<step>" -> every text the pane holds, also in what is hidden: a
 *           result that waits in a closed panel is shown by the next click
 * state     "<step> <path>" -> hidden / disabled / is-invalid / readonly /
 *           open (a details element), for the elements that are one of them,
 *           also inside an element that is hidden itself
 * marks     "<step> <path>" -> what an element carries beside its text and
 *           its value: its classes, the styles set on it apart from its
 *           display, its further attributes
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
  const lists = {};
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
    if (element.tagName === 'SELECT') {
      lists[`${pane} ${name}`] = [...element.options].map((option) => `${option.value}${option.disabled ? ' [disabled]' : ''}`).join(' | ');
    }
  });
  const text = {};
  const held = {};
  const state = {};
  const marks = {};
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
    // Everything the pane holds, shown or not (the entries of the lists are
    // read above): what waits in a closed panel is shown by the next click.
    const heldText = (element) =>
      [...element.childNodes]
        .map((node) => {
          if (node.nodeType === Node.TEXT_NODE) return node.textContent;
          if (node.nodeType !== Node.ELEMENT_NODE || /^(OPTION|SCRIPT|STYLE)$/.test(node.tagName)) return '';
          return heldText(node);
        })
        .join(' ');
    held[key] = heldText(pane).replace(/\s+/g, ' ').trim();
    const walk = (element, path) => {
      const flags = [];
      // Its own display, whatever hides it: the attribute, a class or a style.
      // (The pane around it is not asked - only one pane shows at a time.)
      if (getComputedStyle(element).display === 'none') flags.push('hidden');
      if (element.disabled) flags.push('disabled');
      if (element.classList.contains('is-invalid')) flags.push('is-invalid');
      if (element.readOnly) flags.push('readonly');
      if (element.tagName === 'DETAILS' && element.open) flags.push('open');
      if (flags.length) state[`${key} ${path}${element.id ? `#${placed(element.id)}` : ''}`] = flags.join(' ');
      // What the element carries beside its text and its value: its classes
      // (a badge is coloured by one), the styles set on it apart from its
      // display (read above), and every further attribute - a title, a role,
      // a data attribute. Left out are the attributes that name another
      // element or the item: ids go on counting, a link holds the item's id.
      const carried = [...element.attributes]
        .filter((attribute) => !/^(id|class|style|value|for|name|href|aria-describedby|aria-labelledby|aria-controls|data-bs-target|data-bs-parent)$/.test(attribute.name))
        .map((attribute) => `${attribute.name}="${attribute.value}"`);
      if (element.classList.length) carried.push(`class="${[...element.classList].sort().join(' ')}"`);
      const styles = [...element.style]
        .filter((property) => property !== 'display')
        .map((property) => `${property}: ${element.style.getPropertyValue(property)}`);
      if (styles.length) carried.push(`style="${styles.join('; ')}"`);
      if (carried.length) marks[`${key} ${path}${element.id ? `#${placed(element.id)}` : ''}`] = carried.sort().join(' ');
      // What a hidden element holds is read like everything else: it is on
      // the page again as soon as the element is shown.
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
  return { controls, lists, text, held, state, marks, steps };
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
  for (const key of new Set([...Object.keys(got.lists), ...Object.keys(wanted.lists)])) {
    const a = got.lists[key];
    const b = wanted.lists[key];
    // A select that only one of the two has is reported as a field above.
    if (a === b || a === undefined || b === undefined) continue;
    lines.push(`list ${key}: "${clip(a)}" (expected "${clip(b)}")`);
  }
  for (const key of new Set([...Object.keys(got.held), ...Object.keys(wanted.held)])) {
    // What differs in the text that is shown was reported above.
    if (named(got.text[key] || '') !== named(wanted.text[key] || '')) continue;
    const a = named(got.held[key] || '');
    const b = named(wanted.held[key] || '');
    if (a === b) continue;
    const parted = textDifference(a, b);
    lines.push(`hidden in step ${key}: "${clip(parted.got)}" (expected "${clip(parted.wanted)}")`);
  }
  for (const key of new Set([...Object.keys(got.state), ...Object.keys(wanted.state)])) {
    const a = got.state[key] || 'shown';
    const b = wanted.state[key] || 'shown';
    if (a !== b) lines.push(`state ${key}: ${a} (expected ${b})`);
  }
  for (const key of new Set([...Object.keys(got.marks), ...Object.keys(wanted.marks)])) {
    const a = named(got.marks[key] || '');
    const b = named(wanted.marks[key] || '');
    if (a !== b) lines.push(`marks ${key}: ${clip(a) || 'none'} (expected ${clip(b) || 'none'})`);
  }
  return lines;
}
/** The fields of the later steps that hold in `filled` what they hold in `reference`. */
const leftAlone = (filled, reference) =>
  Object.keys(reference.controls)
    .filter((key) => !key.startsWith('setup ') && filled.controls[key] === reference.controls[key])
    .sort();
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
  // Choose an entry of a list that a search field stands in for on the page:
  // the list itself is not shown, so it is set the way that field sets it.
  const choose = (page, selector, value) =>
    page.$eval(
      selector,
      (select, wanted) => {
        select.value = wanted;
        select.dispatchEvent(new Event('change', { bubbles: true }));
      },
      value
    );

  // ---- prompt templates ----------------------------------------------------------
  let promptReference;
  // The page with a project chosen and no prompt-test open.
  let promptIdle;
  // The manifested prompt-test as a fresh page shows it.
  let manifestedReference;
  await road('prompt template: the reference - a fresh page, a new prompt-test', async (page) => {
    await openPromptBuilder(page, 'llm');
    const untouched = await read(page, 'LPB');
    await choosePromptProject(page, 'proj-1');
    promptIdle = await read(page, 'LPB');
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
    same(none, promptIdle, 'and the whole page is what a fresh page shows with no prompt-test open');
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
    await page.check(lpb('judge-auto'));
    await page.fill('#temperature1', '0.3');
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
    // The server is chosen by the page; the caslib and the table by hand.
    await waitUntil(async () => (await page.$$(`${lpb('optimize-cas-lib')} option`)).length >= 2, 'the caslibs of the CAS server are listed');
    await choose(page, lpb('optimize-cas-lib'), 'Public');
    await waitUntil(async () => (await page.$$(`${lpb('optimize-cas-table')} option`)).length >= 2, 'the tables of the caslib are listed');
    await choose(page, lpb('optimize-cas-table'), 'OPT_DATA');
    // An explanation beside the pickers, folded away.
    await page.locator(`${lpb('pane-optimize')} details.pb-guide summary`).first().click();
    await atRest();
    await goStep(page, 'LPB', 'finalize');
    await page.check(lpb('pet-manifest-integrated'));
    for (const output of ['response', 'run_time', 'prompt_length', 'output_length']) await page.uncheck(lpb(`pet-out-${output}`));
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
    shown.controls['optimize #app-obj-LPB-optimize-cas-table'] === 'OPT_DATA' &&
    shown.controls['finalize #app-obj-LPB-pet-out-run_time'] === 'false' &&
    shown.controls['finalize input.form-control.pb-outvar-name (1)'] === 'typed_output';
  // The fields no road of a kind changes, by road: what one road leaves alone
  // another one has to change, or a field that is added to a later step and
  // is not put back for a new item would pass every comparison below.
  const leftAloneBy = { prompt: [], decision: [], ragNew: [], ragExisting: [] };
  // The fields the road on the page with every key changes (see below).
  let changedWithEveryKey = [];

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
    leftAloneBy.prompt = leftAlone(typed, promptReference);
    await cancelCreate(page, '#promptBuilderCreatePromptModal', '#promptBuilderCreatePromptName');
    same(await read(page, 'LPB'), typed, 'a second walk through the steps and a create dialog that is closed without creating lose nothing of it');
    await createPromptTest(page);
    same(await read(page, 'LPB'), promptReference, 'new -> new: the second new prompt-test starts as on a fresh page');
  });

  // The third LLM of the fixtures needs a key of a provider the default
  // credential domain of the mock holds none for: on the pages above its tick
  // box is disabled, and no road can change it or its option. The domain
  // all-keys holds that key. There the LLM is a field like the others - with
  // a reference of its own, because a tick box that can be used reads
  // differently from one that cannot.
  await road('prompt template: the LLM that needs a key the default credential domain does not hold', async (page) => {
    await page.goto(`${APP_URL}&credentialDomain=all-keys`);
    await page.waitForSelector('#LPB-project-dropdown', { timeout: 15000 });
    await atRest();
    await choosePromptProject(page, 'proj-1');
    await createPromptTest(page);
    const reference = await read(page, 'LPB');
    await goStep(page, 'LPB', 'build');
    await page.check('#model2');
    await page.fill('#temperature2', '0.9');
    const typed = await read(page, 'LPB');
    assert(
      promptReference.controls['build #model2'] === 'false [disabled]' && reference.controls['build #model2'] === 'false' && typed.controls['build #model2'] === 'true' && typed.controls['build #temperature2'] === '0.9',
      `with a key for its provider in the credential domain the third LLM can be ticked and its option changed (default domain: ${promptReference.controls['build #model2']}; all-keys, new prompt-test: ${reference.controls['build #model2']}; after the tick: ${typed.controls['build #model2']} at ${typed.controls['build #temperature2']})`
    );
    const untouched = leftAlone(typed, reference);
    changedWithEveryKey = Object.keys(reference.controls).filter((key) => !key.startsWith('setup ') && !untouched.includes(key));
    await createPromptTest(page);
    same(await read(page, 'LPB'), reference, 'new, with the third LLM ticked -> new: the second new prompt-test starts as on a fresh page of that credential domain');
  });

  await road('prompt template: a new prompt-test in another project than the one that was open', async (page) => {
    await openPromptBuilder(page, 'llm');
    await choosePromptProject(page, 'proj-1');
    await openPromptTest(page, 'model-used');
    await atRest();
    await createPromptProject(page);
    // Before anything is created there, the steps show no prompt-test: the
    // Optimize step lists no run of the prompt-test of the other project,
    // whose "Load as experiment" would fill Build & Test with its prompt.
    const idle = await read(page, 'LPB');
    await goStep(page, 'LPB', 'optimize');
    const offered = {
      banner: ((await page.textContent(lpb('optimize-banner'))) || '').trim(),
      history: ((await page.textContent(lpb('optimize-history'))) || '').replace(/\s+/g, ' ').trim(),
      compare: !(await page.isDisabled(lpb('optimize-compare'))),
    };
    step(
      offered.banner === 'Select a prompt-test first.' && offered.history === '' && !offered.compare,
      `existing -> another project, nothing open yet: the Optimize step asks for a prompt-test and shows no optimization history of the one that was open (banner: "${offered.banner}", history: "${clip(offered.history)}", Compare targets usable: ${offered.compare})`
    );
    same(idle, promptIdle, 'and the whole page is what a fresh page shows with no prompt-test open');
    await createPromptTest(page);
    same(await read(page, 'LPB'), promptReference, 'existing -> another project -> new: the new prompt-test starts as on a fresh page');
  });

  await road('prompt template: a prompt-test that was filled by hand is deleted', async (page) => {
    await openPromptBuilder(page, 'llm');
    await choosePromptProject(page, 'proj-1');
    await createPromptTest(page);
    await fillPromptTemplate(page);
    await goStep(page, 'LPB', 'setup');
    await page.click('#LPB-delete-prompt-button');
    await page.waitForSelector('.modal.show .btn-danger');
    await page.click('.modal.show .btn-danger');
    await page.waitForSelector('.modal.show', { state: 'detached' });
    await waitUntil(async () => !(await page.inputValue('#LPB-prompt-dropdown')).startsWith('model-new-'), 'the deleted prompt-test has left the picker');
    await atRest();
    same(await read(page, 'LPB'), promptIdle, 'new, filled by hand -> deleted: the page is what a fresh page shows with no prompt-test open');
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
  await road('prompt template: typed before two prompt-tests were chosen one right after the other', async (page) => {
    await openPromptBuilder(page, 'llm');
    await choosePromptProject(page, 'proj-1');
    await typeWithoutPromptTest(page);
    await goStep(page, 'LPB', 'setup');
    // The first one, which has a best run, is still being read when the
    // second one, which has none, is chosen: nothing of the first was drawn,
    // so what was typed is still nobody's and is kept for the second.
    await page.route('**/modelRepository/models/model-used', async (route) => {
      await sleep(1500);
      await route.continue();
    });
    await page.selectOption('#LPB-prompt-dropdown', 'model-used');
    await page.selectOption('#LPB-prompt-dropdown', 'model-free');
    await sleep(2500);
    await atRest();
    await page.unroute('**/modelRepository/models/model-used');
    const kept = await read(page, 'LPB');
    step(
      (await page.inputValue('#LPB-prompt-dropdown')) === 'model-free' &&
        kept.controls['build #app-obj-LPB-system-prompt'] === 'Typed before a prompt-test existed' &&
        kept.controls['build input.form-control.pb-var-name (1)'] === 'early' &&
        kept.controls['build #model1'] === 'true' &&
        kept.controls['build #temperature1'] === '0.4' &&
        kept.controls['optimize #app-obj-LPB-optimize-max-demos'] === '9' &&
        (await savedRuns(page)) === 0,
      `nothing open -> typed -> a prompt-test with a best run and at once one without: what was typed is kept for the one that is open, and no run of the other is shown (system prompt: "${kept.controls['build #app-obj-LPB-system-prompt']}", variable: ${kept.controls['build input.form-control.pb-var-name (1)']}, second LLM ticked: ${kept.controls['build #model1']} at ${kept.controls['build #temperature1']}, examples of an optimization: ${kept.controls['optimize #app-obj-LPB-optimize-max-demos']}, runs: ${await savedRuns(page)})`
    );
    // It belongs to that prompt-test now, and goes with it.
    await openPromptTest(page, 'model-used');
    same(await read(page, 'LPB'), manifestedReference, 'and the prompt-test with the best run, opened after it, shows what it shows on a fresh page');
  });

  // ---- decision templates --------------------------------------------------------
  let decisionReference;
  // The page with a decision project chosen and no template open.
  let decisionIdle;
  await road('decision template: the reference - a fresh page, a new decision template', async (page) => {
    await openPromptBuilder(page, 'decision');
    const untouched = await read(page, 'LPB');
    await choosePromptProject(page, 'proj-2');
    decisionIdle = await read(page, 'LPB');
    await createPromptTest(page);
    decisionReference = await read(page, 'LPB');
    assert(decisionReference.steps === 'setup*,build,evaluate,finalize', `a decision template has the steps Setup, Build & Test, Evaluate and Finalize (got: ${decisionReference.steps})`);
    assert(
      decisionReference.controls['build #app-obj-LPB-decision-state'] === '' && decisionReference.controls['build #decision-model0'] === 'false' && decisionReference.controls['evaluate #app-obj-LPB-evaluate-max-rows'] === '100',
      'a new decision template on a fresh page has an empty state template, no decision model ticked and the defaults where there are some (100 rows to evaluate)'
    );
    same(decisionReference, untouched, 'every field of the later steps holds for a new decision template on a fresh page what it holds before anything is selected', true);
  });

  // The labelled cases of the Evaluate step: server, caslib and table.
  const chooseEvaluationTable = async (page) => {
    await waitUntil(async () => (await page.$$(`${lpb('evaluate-cas-server')} option`)).length >= 2, 'the CAS servers are listed');
    await page.selectOption(lpb('evaluate-cas-server'), 'cas-shared-default');
    await waitUntil(async () => (await page.$$(`${lpb('evaluate-cas-lib')} option`)).length >= 2, 'the caslibs are listed');
    await page.selectOption(lpb('evaluate-cas-lib'), 'Public');
    await waitUntil(async () => (await page.$$(`${lpb('evaluate-cas-table')} option`)).length >= 2, 'the tables are listed');
    await page.selectOption(lpb('evaluate-cas-table'), 'DEC_CASES');
  };
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
    // A template that is looked at and not applied: the line beside the list
    // says what it is good for.
    await page.selectOption(lpb('decision-template'), { index: 1 });
    await page.click(`${lpb('pane-build')} button:has-text("Add Variable")`);
    await page.fill(`${lpb('variables')} .pb-var-name`, 'topic');
    await page.fill(`${lpb('variables')} .pb-var-value`, 'typed value');
    await page.click(lpb('decision-add-question'));
    const row = page.locator('.pb-question-row').last();
    await row.locator('.pb-question-id').fill('typed_question');
    await row.locator('.pb-question-type').selectOption('noul');
    await row.locator('.pb-question-instructions').fill('Is the text about an invoice that was paid twice?');
    await row.locator('.pb-question-expected').fill('no');
    // Every "?" beside a label opens a sentence of help under it.
    for (const toggle of await page.$$(`${lpb('pane-build')} .pb-help-toggle`)) {
      if (await toggle.isVisible()) await toggle.click();
    }
    await page.check('#decision-model0');
    await page.check('#decision-model1');
    await page.click(lpb('run-experiment'));
    await waitUntil(async () => (await page.$$(`${lpb('pet')} .accordion-item`)).length > 0, 'the typed decision template was run');
    await waitUntil(async () => (await page.textContent(lpb('run-experiment'))).trim() === 'Run Experiments', 'the run has ended');
    await atRest();
    await goStep(page, 'LPB', 'evaluate');
    await page.click(lpb('evaluate-run'));
    await waitUntil(async () => ((await page.textContent(lpb('evaluate-results'))) || '').trim().length > 0, 'the run was evaluated');
    await page.check(lpb('evaluate-source-cas'));
    await chooseEvaluationTable(page);
    await page.fill(lpb('evaluate-max-rows'), '7');
    await atRest();
    await goStep(page, 'LPB', 'finalize');
    for (const output of ['answers', 'answer', 'confidence', 'run_time', 'prompt_length']) await page.uncheck(lpb(`pet-decout-${output}`));
    await atRest();
  };
  const typedDecisionIsShown = (shown) =>
    // the opened help of the state template, and what the template is good for
    shown.text.build.includes('The text or JSON the model reads') &&
    shown.controls['build #app-obj-LPB-decision-template'] !== '' &&
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
    leftAloneBy.decision = leftAlone(typed, decisionReference);
    // The LLM that cannot be selected here - no credential for it in the
    // domain of this page - has a tick box and an option nobody can change
    // on this page; the road on the page with every key changes those two.
    const neither = leftAloneBy.prompt.filter((key) => leftAloneBy.decision.includes(key));
    const never = neither.filter((key) => !changedWithEveryKey.includes(key));
    step(
      never.length === 0 && neither.join(' ; ') === 'build #model2 ; build #temperature2',
      `every field of the steps after Setup is changed by one of the roads that fill a prompt-test by hand, so each of them is compared with its value on a fresh page afterwards: by one of the two roads on this page, or - the LLM that cannot be selected here - by the road on the page whose credential domain holds its key (not changed by the two roads of this page: ${neither.join(' ; ') || 'none'}; not changed by any road: ${never.join(' ; ') || 'none'})`
    );
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
    same(await read(page, 'LPB'), decisionIdle, 'existing -> another project, nothing open yet: the page is what a fresh page shows with no decision template open');
    await createPromptTest(page);
    same(await read(page, 'LPB'), decisionReference, 'existing -> another project -> new: the new decision template starts as on a fresh page');
  });

  await road('decision template: an evaluation over a CAS table is still running when a new template is created', async (page) => {
    await openPromptBuilder(page, 'decision');
    await choosePromptProject(page, 'proj-2');
    await openPromptTest(page, 'model-dec');
    // Two decision models: the second one is asked after the first.
    await goStep(page, 'LPB', 'build');
    await page.check('#decision-model1');
    await goStep(page, 'LPB', 'evaluate');
    await page.check(lpb('evaluate-source-cas'));
    await chooseEvaluationTable(page);
    // Every answer of a decision model takes six seconds from here on: the
    // four cases of the first model are still out when the new template is
    // created, and the four of the second model have not been asked yet.
    let asked = 0;
    await page.route('**/scr/**', async (route) => {
      asked += 1;
      await sleep(6000);
      await route.continue();
    });
    const firstCase = page.waitForRequest('**/scr/**');
    await page.click(lpb('evaluate-run'));
    await firstCase;
    await createPromptTest(page);
    const stillOut = await page.isDisabled(lpb('evaluate-run'));
    step(stillOut, 'the new decision template was created while the answers of the evaluation were still out, and Evaluate stays disabled until they are in');
    await waitUntil(async () => !(await page.isDisabled(lpb('evaluate-run'))), 'the calls of the evaluation have ended', 40000);
    await page.unroute('**/scr/**');
    await atRest();
    step(asked === 4, `the evaluation of the template that was left asks no further case: the four that were out are all (asked: ${asked} of 8)`);
    same(await read(page, 'LPB'), decisionReference, 'an evaluation in flight -> new: the new decision template starts as on a fresh page, without the result of that evaluation');
  });

  // ---- from one kind to the other ------------------------------------------------
  await road('a prompt-test of one kind, then a new one of the other kind', async (page) => {
    await openPromptBuilder(page, 'llm');
    await choosePromptProject(page, 'proj-1');
    await openPromptTest(page, 'model-used');
    await chooseKind(page, 'decision');
    same(await read(page, 'LPB'), decisionIdle, 'an existing prompt template -> the decision kind, nothing open yet: the page is what a fresh page shows with no decision template open');
    await choosePromptProject(page, 'proj-2');
    await createPromptTest(page);
    same(await read(page, 'LPB'), decisionReference, 'an existing prompt template -> the decision kind -> new: the new decision template starts as on a fresh page');
    await openPromptTest(page, 'model-dec');
    await chooseKind(page, 'llm');
    same(await read(page, 'LPB'), promptIdle, 'an existing decision template -> the LLM kind, nothing open yet: the page is what a fresh page shows with no prompt-test open');
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
  // The page with a RAG project chosen and no setup open, and the two fixture
  // setups this suite opens beside the handbook, each as a fresh page shows it.
  let ragIdle;
  let emptySetupReference;
  let legacyReference;
  await road('RAG setup: the reference - a fresh page, a new setup', async (page) => {
    await openRagBuilder(page);
    await chooseRagProject(page, 'rag-proj');
    ragIdle = await read(page, 'RGB');
    await openRagSetup(page, 'rag-setup-2');
    emptySetupReference = await read(page, 'RGB');
    await page.goto('about:blank');
    await openRagBuilder(page);
    await chooseRagProject(page, 'rag-proj');
    await openRagSetup(page, 'rag-setup-ss');
    legacyReference = await read(page, 'RGB');
    await page.goto('about:blank');
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
    leftAloneBy.ragExisting = leftAlone(existing, ragReference);
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
    await page.selectOption(rgb('source-kind'), 'sascontent');
    await page.fill(rgb('source-path'), '/typed/folder');
    await page.selectOption(rgb('extractor'), { index: 1 });
    await page.check(rgb('include-code'));
    await page.selectOption(rgb('embed-model'), { index: 1 });
    await page.selectOption(rgb('chunker'), { index: 1 });
    await page.fill(rgb('token-limit'), '128');
    await page.dispatchEvent(rgb('token-limit'), 'change');
    // A prompt that is called once per chunk. The blocks of the chain are
    // rebuilt for every setup, so their fields are found by the end of their id.
    const enrich = (suffix) => `${rgb('enrich-list')} [id$="-${suffix}"]`;
    await page.selectOption(enrich('project'), 'proj-1');
    await waitUntil(async () => (await page.$$eval(`${enrich('prompt')} option`, (options) => options.map((option) => option.value))).includes('model-parse'), 'the prompts of the project are listed');
    await page.selectOption(enrich('prompt'), 'model-parse');
    await waitUntil(async () => (await page.$$(`${enrich('version')} option`)).length >= 2, 'the versions of the prompt are listed');
    await page.selectOption(enrich('version'), { index: 1 });
    await page.selectOption(enrich('header'), 'context_header');
    await page.fill(enrich('workers'), '3');
    await atRest();
    await goStep(page, 'RGB', 'store');
    await page.selectOption(rgb('backend'), 'pgvector');
    await page.fill(rgb('collection'), 'typed_collection');
    await page.fill(rgb('tables-prefix'), 'TYPED');
    await page.selectOption(rgb('tables-caslib'), { index: 1 });
    await page.fill(rgb('artifacts-folder'), '/typed/generated');
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
    leftAloneBy.ragNew = leftAlone(typed, ragReference);
    const neverRag = leftAloneBy.ragNew.filter((key) => leftAloneBy.ragExisting.includes(key));
    step(
      neverRag.length === 0,
      `every field of the steps after Setup is changed by one of the two roads that fill a RAG setup, so each of them is compared with its value on a fresh page afterwards (not changed by either: ${neverRag.join(' ; ') || 'none'})`
    );
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
    same(await read(page, 'RGB'), ragIdle, 'existing -> another project, nothing open yet: the page is what a fresh page shows with no setup open');
    await createRagSetup(page);
    same(await read(page, 'RGB'), ragReference, 'existing -> another project -> new: the new setup starts as on a fresh page');
  });

  // The requests that carry the text of a picker's placeholder as an id.
  const placeholderRequests = async () =>
    (await getLog()).map((entry) => decodeURIComponent(entry.url)).filter((url) => /Select a RAG (setup|project)/.test(url));
  await road('RAG setup: the placeholder entries of the two pickers', async (page) => {
    await openRagBuilder(page);
    await chooseRagProject(page, 'rag-proj');
    await openRagSetup(page, 'rag-setup-1');
    await goStep(page, 'RGB', 'setup');
    await resetLog();
    let errorsBefore = pageErrors.length;
    await page.selectOption(rgb('setup'), { index: 0 });
    await atRest();
    let asked = await placeholderRequests();
    step(
      asked.length === 0 && pageErrors.length === errorsBefore && (await page.isDisabled(rgb('delete-setup'))),
      `the placeholder entry of the setup picker is no setup: nothing is asked of SAS Model Manager under its text, nothing fails, and Delete setup is off (requests: ${asked.join(' | ') || 'none'}, page errors: ${pageErrors.length - errorsBefore})`
    );
    same(await read(page, 'RGB'), ragIdle, 'an existing setup -> the placeholder entry: the page is what a fresh page shows with no setup open');
    await openRagSetup(page, 'rag-setup-1');
    await goStep(page, 'RGB', 'setup');
    await resetLog();
    errorsBefore = pageErrors.length;
    await page.selectOption(rgb('project'), { index: 0 });
    await atRest();
    asked = await placeholderRequests();
    const setups = await page.$$eval(`${rgb('setup')} option`, (options) => options.length);
    step(
      asked.length === 0 && pageErrors.length === errorsBefore && setups === 1 && (await page.isDisabled(rgb('delete-project'))),
      `the placeholder entry of the project picker is no project: nothing is asked under its text, nothing fails, the setup picker holds its placeholder only and Delete project is off (requests: ${asked.join(' | ') || 'none'}, page errors: ${pageErrors.length - errorsBefore}, entries of the setup picker: ${setups})`
    );
    same(await read(page, 'RGB'), ragIdle, 'an existing setup -> the placeholder entry of the project picker: the later steps are as with no setup open');
  });

  // What the test panel of the last step holds, shown or not.
  const testPanel = async (page) => ({
    shown: await page.isVisible(rgb('test-panel')),
    question: await page.inputValue(rgb('test-question')),
    status: ((await page.textContent(rgb('test-status'))) || '').trim(),
    chunks: ((await page.textContent(rgb('test-results'))) || '').replace(/\s+/g, ' ').trim(),
  });
  const probeEnded = async () => (await getLog()).some((entry) => entry.method === 'DELETE' && entry.url.startsWith('/jobDefinitions/definitions/') && entry.status !== null);
  const askHandbook = async (page) => {
    await goStep(page, 'RGB', 'run');
    await page.click(rgb('test'));
    await page.waitForSelector(rgb('test-question'), { state: 'visible' });
    await page.fill(rgb('test-question'), 'What does the handbook say about travel?');
    await resetLog();
    await page.click(rgb('test-run'));
    await waitUntil(async () => (await getLog()).some((entry) => entry.method === 'POST' && entry.url.startsWith('/jobExecution/jobs')), 'the probe was launched');
  };
  await road('RAG setup: a retrieval test is still running when another setup is opened', async (page) => {
    await openRagBuilder(page);
    await chooseRagProject(page, 'rag-proj');
    await openRagSetup(page, 'rag-setup-1');
    await askHandbook(page);
    // Another existing setup, with a collection of its own.
    await goStep(page, 'RGB', 'setup');
    await page.selectOption(rgb('setup'), 'rag-setup-ss');
    const outAtTheChange = !(await probeEnded());
    await waitUntil(probeEnded, 'the probe of the setup that was left has ended', 40000);
    await atRest();
    let panel = await testPanel(page);
    step(
      outAtTheChange && !panel.shown && panel.status === '' && panel.chunks === '',
      `a retrieval test in flight -> another setup: the chunks of the setup that was left do not arrive in the test panel of the one that is open (still out at the change: ${outAtTheChange}, panel shown: ${panel.shown}, status: "${clip(panel.status)}", chunks: "${clip(panel.chunks)}")`
    );
    same(await read(page, 'RGB'), legacyReference, 'and that setup shows what it shows on a fresh page');
    await goStep(page, 'RGB', 'run');
    await page.click(rgb('test'));
    await page.waitForSelector(rgb('test-question'), { state: 'visible' });
    panel = await testPanel(page);
    step(
      panel.question === '' && panel.status === '' && panel.chunks === '' && !(await page.isDisabled(rgb('test-run'))),
      `its test panel opens empty and ready to ask (question: "${panel.question}", status: "${clip(panel.status)}", chunks: "${clip(panel.chunks)}", Run test disabled: ${await page.isDisabled(rgb('test-run'))})`
    );
    // And a new setup.
    await openRagSetup(page, 'rag-setup-1');
    await askHandbook(page);
    await createRagSetup(page);
    await waitUntil(probeEnded, 'the probe of the setup that was left has ended', 40000);
    await atRest();
    same(await read(page, 'RGB'), ragReference, 'a retrieval test in flight -> new: the new setup starts as on a fresh page, with nothing waiting in its closed test panel');
  });

  await road('RAG setup: an ingestion is still running when another setup is opened', async (page) => {
    await openRagBuilder(page);
    await chooseRagProject(page, 'rag-proj');
    await openRagSetup(page, 'rag-setup-1');
    await goStep(page, 'RGB', 'run');
    // Every answer about the state of the job takes three seconds.
    let polls = 0;
    await page.route('**/jobExecution/jobs/ragrun-*', async (route) => {
      polls += 1;
      await sleep(3000);
      await route.continue();
    });
    await page.click(rgb('launch'));
    // The second poll is the one that finds the job completed: it is out
    // when the other setup is chosen, and comes back after that.
    await waitUntil(async () => polls >= 2, 'the second poll of the ingestion is out', 30000);
    await goStep(page, 'RGB', 'setup');
    await page.selectOption(rgb('setup'), 'rag-setup-2');
    await sleep(5000);
    await atRest();
    await page.unroute('**/jobExecution/jobs/ragrun-*');
    const banner = ((await page.textContent(rgb('status'))) || '').replace(/\s+/g, ' ').trim();
    await goStep(page, 'RGB', 'run');
    const ledgerShown = await page.isVisible(rgb('ledger-panel'));
    step(
      !ledgerShown && !/ingestion/i.test(banner),
      `an ingestion in flight -> another setup: the run of the setup that was left opens no ledger and reports nothing on the one that is open (ledger shown: ${ledgerShown}, banner: "${clip(banner)}")`
    );
    same(await read(page, 'RGB'), emptySetupReference, 'and that setup shows what it shows on a fresh page');
    // Its own ledger does not speak of a run either.
    await goStep(page, 'RGB', 'run');
    await page.click(rgb('ledger'));
    await waitUntil(async () => page.isVisible(rgb('ledger-panel')), 'the ledger of the open setup is shown');
    await atRest();
    const ledger = ((await page.textContent(rgb('ledger-panel'))) || '').replace(/\s+/g, ' ').trim();
    step(!/has been running/.test(ledger), `the ledger of the open setup does not say that an ingestion is running (ledger: "${clip(ledger)}")`);
  });

  await road('RAG setup: a setup is still being read when another one is chosen', async (page) => {
    await openRagBuilder(page);
    await chooseRagProject(page, 'rag-proj');
    await goStep(page, 'RGB', 'setup');
    await page.route('**/modelRepository/models/rag-setup-1/contents**', async (route) => {
      await sleep(2000);
      await route.continue();
    });
    await page.selectOption(rgb('setup'), 'rag-setup-1');
    await page.selectOption(rgb('setup'), 'rag-setup-2');
    await sleep(3000);
    await atRest();
    await page.unroute('**/modelRepository/models/rag-setup-1/contents**');
    same(await read(page, 'RGB'), emptySetupReference, 'a saved setup that answers late -> a setup that was never saved: the one that is open shows what it shows on a fresh page, not the stored configuration of the other');
  });

  // The prompts of a stored enrichment are read back one after the other,
  // and each one takes a round trip. A setup that is left meanwhile must not
  // put the rest of its chain into the form of the setup that is open by
  // then - which a later Save setup would store there.
  const enrichmentChain = (page) =>
    page.$$eval(`${rgb('enrich-list')} select[id$="-prompt"]`, (prompts) => prompts.map((prompt) => prompt.value || 'none'));
  await road('RAG setup: a stored enrichment is still being read back when another setup is chosen', async (page) => {
    await fetch(BASE + '/__ragenrich', { method: 'POST', body: JSON.stringify({ on: true }) });
    try {
      await openRagBuilder(page);
      await chooseRagProject(page, 'rag-proj');
      await openRagSetup(page, 'rag-setup-1');
      const chain = await enrichmentChain(page);
      const second = {
        version: await page.$eval(`${rgb('enrich-list')} > div:nth-child(2) select[id$="-version"]`, (list) => list.value),
        workers: await page.$eval(`${rgb('enrich-list')} > div:nth-child(2) input[id$="-workers"]`, (field) => field.value),
        columns: await page.$$eval(`${rgb('enrich-list')} > div:nth-child(2) input[type="checkbox"]:checked`, (boxes) => boxes.map((box) => box.value).join(',')),
      };
      assert(
        chain.join(',') === 'model-parse,model-parse' && second.version === 'ver-1' && second.workers === '2' && second.columns === 'topic',
        `a setup that stores an enrichment of two prompts opens with both of them (prompts: ${chain.join(', ')}; the second one - version: ${second.version}, parallel calls: ${second.workers}, columns: ${second.columns})`
      );
      await openRagSetup(page, 'rag-setup-2');
      same(await read(page, 'RGB'), emptySetupReference, 'a setup with an enrichment -> a setup that was never saved: it shows what it shows on a fresh page, without a prompt of the other');

      // The versions of the first prompt answer after two and a half seconds:
      // the other setup is chosen while that answer is out.
      await goStep(page, 'RGB', 'setup');
      let asked = 0;
      await page.route('**/modelRepository/models/model-parse/modelVersions**', async (route) => {
        asked += 1;
        await sleep(2500);
        // (A request that is still held when the road ends has no page left.)
        await route.continue().catch(() => {});
      });
      await page.selectOption(rgb('setup'), 'rag-setup-1');
      await waitUntil(async () => asked >= 1, 'the first prompt of the stored chain is being read back');
      await page.selectOption(rgb('setup'), 'rag-setup-2');
      // Long enough for a second prompt to be asked for and drawn, if it is.
      await sleep(6500);
      await atRest();
      await page.unroute('**/modelRepository/models/model-parse/modelVersions**');
      const left = await enrichmentChain(page);
      step(
        asked === 1 && left.join(',') === 'none',
        `an enrichment that is read back late -> a setup that was never saved: the form of the open setup holds its one empty prompt, and the second prompt of the other setup is neither asked for nor drawn (prompts in the form: ${left.join(', ')}; versions asked for: ${asked})`
      );
      same(await read(page, 'RGB'), emptySetupReference, 'and that setup shows what it shows on a fresh page');
    } finally {
      await fetch(BASE + '/__ragenrich', { method: 'POST', body: JSON.stringify({ on: false }) });
    }
  });

  // ============================================================================
  // Optimizations (last: a job the mock has launched stays launched). The
  // first job of a mock runs for four polls, twenty seconds; its entry in the
  // optimization tracker then stands, and every later job is found finished
  // on its first poll.
  // ============================================================================
  // What the Optimize step says about a job.
  const optimizeRun = async (page) => ({
    log: await page.isVisible(lpb('optimize-log')),
    status: ((await page.textContent(lpb('optimize-status'))) || '').trim(),
    result: ((await page.textContent(lpb('optimize-result'))) || '').replace(/\s+/g, ' ').trim(),
    banner: ((await page.textContent(lpb('optimize-banner'))) || '').trim(),
    button: ((await page.textContent(lpb('optimize-run'))) || '').trim(),
    disabled: await page.isDisabled(lpb('optimize-run')),
  });
  const jobPolls = async () => (await getLog()).filter((entry) => entry.method === 'GET' && entry.url === '/jobExecution/jobs/job-1' && entry.status !== null).length;
  const launchOptimization = async (page) => {
    await goStep(page, 'LPB', 'optimize');
    await page.selectOption(lpb('optimize-target'), 'demo_llm');
    await waitUntil(async () => !(await page.isDisabled(lpb('optimize-run'))), 'the optimization can be started');
    await resetLog();
    await page.click(lpb('optimize-run'));
    await waitUntil(async () => (await getLog()).some((entry) => entry.method === 'POST' && entry.url === '/jobExecution/jobs' && entry.status !== null), 'the optimization job was launched');
    await page.waitForSelector(lpb('optimize-log'), { state: 'visible' });
  };
  // The job has ended when it was asked for at least once and the page no
  // longer says that an optimization is running - on the button of the
  // prompt-test the job runs for, in the banner of any other.
  const jobEnded = async (page) => {
    await waitUntil(
      async () => {
        const shown = await optimizeRun(page);
        return (await jobPolls()) >= 1 && shown.button === 'Run optimization' && shown.banner !== 'An optimization is already running.';
      },
      'the optimization job has ended',
      90000
    );
    await atRest();
    await sleep(500);
    await atRest();
  };
  await road('prompt template: an optimization is still running when another prompt-test is opened', async (page) => {
    await openPromptBuilder(page, 'llm');
    await choosePromptProject(page, 'proj-1');
    await openPromptTest(page, 'model-used');
    // The first job of this mock: twenty seconds.
    await launchOptimization(page);
    const own = await optimizeRun(page);
    await createPromptTest(page);
    await goStep(page, 'LPB', 'optimize');
    const during = await optimizeRun(page);
    const outAtTheChange = (await jobPolls()) < 4;
    step(
      outAtTheChange && own.log && own.button === 'Optimising...' && !during.log && during.status === '' && during.result === '' && during.disabled && during.button === 'Run optimization' && during.banner === 'An optimization is already running.',
      `an optimization in flight -> new: the Optimize step of the new prompt-test shows neither the state nor the log of the job of the other one; it says that an optimization is running, which is why none can be started (job still running: ${outAtTheChange}; on its own prompt-test - log shown: ${own.log}, button: "${own.button}"; on the new one - log shown: ${during.log}, status: "${clip(during.status)}", result: "${clip(during.result)}", button: "${during.button}"${during.disabled ? ' [disabled]' : ''}, banner: "${during.banner}")`
    );
    // The prompt-test the job runs for shows it again while it runs.
    await openPromptTest(page, 'model-used');
    await goStep(page, 'LPB', 'optimize');
    let back = await optimizeRun(page);
    const stillRunning = (await jobPolls()) < 4;
    try {
      await waitUntil(async () => {
        back = await optimizeRun(page);
        return back.log && /running|pending/.test(back.status);
      }, 'the job is shown again', 8000);
    } catch {
      /* said by the check below */
    }
    step(
      stillRunning && back.log && /running|pending/.test(back.status) && back.disabled && back.button === 'Optimising...',
      `an optimization in flight -> new -> the prompt-test the job runs for: it shows the state and the log of its job again (job still running: ${stillRunning}, log shown: ${back.log}, status: "${clip(back.status)}", button: "${back.button}"${back.disabled ? ' [disabled]' : ''})`
    );
    await jobEnded(page);
    const ended = await optimizeRun(page);
    step(
      /Optimization finished/.test(ended.result) && ended.status === '' && !ended.disabled && ended.button === 'Run optimization',
      `and the result of the job, when it ends there (result: "${clip(ended.result)}", button: "${ended.button}"${ended.disabled ? ' [disabled]' : ''})`
    );

    // A job that ends while another prompt-test is open. Its first poll is
    // answered after four seconds, so that it cannot end before the new
    // prompt-test is there.
    await page.route('**/jobExecution/jobs/job-1', async (route) => {
      await sleep(4000);
      await route.continue();
    });
    await launchOptimization(page);
    await createPromptTest(page);
    const outAtTheCreation = (await jobPolls()) === 0;
    await goStep(page, 'LPB', 'optimize');
    await jobEnded(page);
    await page.unroute('**/jobExecution/jobs/job-1');
    step(outAtTheCreation, 'a second job was still running when the next new prompt-test was created');
    same(await read(page, 'LPB'), promptReference, 'an optimization that ends after the new prompt-test was created: the new prompt-test is as on a fresh page, without the result and the log of that run');
  });


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
