/**
 * While a write to SAS Model Manager is out, what is open cannot be changed.
 * Run against a FRESH mock instance.
 *
 * A save, a manifest or a delete is a row of requests, and the builders read
 * the page between them. With another item opened in between, Save
 * Experiments uploaded a tracker without a run to the prompt-test it was
 * clicked for, and Save setup wrote to two setups and said "saved" on the
 * wrong one. So the controls that decide what is open - the kind, the
 * project, the item, Create, Delete - cannot be used while a write is out,
 * and neither can any button that starts a write (WRITE_BUTTONS): a Save
 * setup clicked between two stages of a manifest wrote the setup without the
 * job the manifest had just written into it. Nor, in the RAG Builder, can the
 * two buttons that start a job of the setup (Launch ingestion, Run test): a
 * launch between two stages of a manifest ran the job definition the
 * manifest had not yet rewritten.
 *
 * Every write path of the page is driven the same way (`probe`):
 *
 *   1. The requests that write are answered late: the first one after three
 *      seconds, each further one after a moment (`holdWrites`).
 *   2. The write is started, and the page is looked at again and again until
 *      it has ended: none of those controls may be usable at any of those
 *      moments.
 *   3. Meanwhile a person tries to open something else - through the search
 *      field that stands in for a list, or the card of another kind - and
 *      must not get there.
 *   4. Afterwards the requests are read: what was written, and to which item;
 *      the note of the write stands on the item it was made for; the controls
 *      can be used again. For the two saves that lost or misplaced data and
 *      for the four deletes the attempt is then made a second time, and
 *      opens the other item.
 *
 * The write paths: Save Experiments (also one whose upload fails), Save
 * documentation, Manifest for a prompt template and for a decision template,
 * Delete prompt-test, Delete project, the delete of an optimization run,
 * Compare targets and Run optimization (which save first); in the RAG Builder
 * Save setup (also one that is refused), Save documentation, Manifest setup,
 * Delete setup and Delete project. Two writes are not among them, because
 * they take no lock: a creation, whose dialog lies over the page and cannot
 * be closed while the creation is out (verify.js and verify-rag.js check
 * that), and the one fixed value a prompt-test with the function name of an
 * earlier release gets when it is opened.
 *
 * The suite goes on after a check that failed, and after a road that stopped,
 * so one run says what holds for every write path. The mock is found at
 * VERIFY_BASE (default http://localhost:4173).
 */
const { chromium } = require('playwright');
const fetch = require('./mock-fetch.cjs');

const BASE = (process.env.VERIFY_BASE || 'http://localhost:4173').replace(/\/+$/, '');
const results = [];

function step(ok, label) {
  results.push(`${ok ? 'OK ' : 'FAIL'}  ${label}`);
  console.log(`${ok ? 'OK ' : 'FAIL'}  ${label}`);
  if (!ok) process.exitCode = 1;
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
const clip = (value) => (value.length > 110 ? `${value.slice(0, 110)}...` : value);
// The JSON file of a multipart upload.
function multipartJson(body) {
  const m = body.match(/\r\n\r\n([\s\S]*?)\r\n--/);
  try {
    return m ? JSON.parse(m[1]) : null;
  } catch {
    return null;
  }
}

// The page with every part switched on.
const APP_URL =
  `${BASE}/?modelRepositoryID=repo-1&llmProjectID=llm-proj&SCREndpoint=${BASE}/scr` +
  '&enableOptimization=true&computeContext=ctx&optimizeJobProgram=/Public/Jobs/Optimize-Prompt-DSPy&minOptimizeSamples=1' +
  '&enableRag=true&embeddingProjectID=emb-proj&ragComputeContext=rag-ctx';

// The controls that decide what is open, per builder: [name, selector].
const PROMPT_CONTROLS = [
  ['the card of the LLM kind', '#LPB-kind-llm'],
  ['the card of the decision kind', '#LPB-kind-decision'],
  ['the card of the RAG kind', '#LPB-kind-rag'],
  ['the project list', '#LPB-project-dropdown'],
  ['the search field of the project list', '#LPB-project-dropdown-combobox'],
  ['the prompt-test list', '#LPB-prompt-dropdown'],
  ['the search field of the prompt-test list', '#LPB-prompt-dropdown-combobox'],
  ['Create project', 'button[data-bs-target="#promptBuilderCreateProjectModal"]'],
  ['Create prompt-test', 'button[data-bs-target="#promptBuilderCreatePromptModal"]'],
  ['Delete prompt-test', '#LPB-delete-prompt-button'],
  ['Delete project', '#LPB-delete-project-button'],
];
const RAG_CONTROLS = [
  ['the card of the LLM kind', '#RGB-kind-llm'],
  ['the card of the decision kind', '#RGB-kind-decision'],
  ['the card of the RAG kind', '#RGB-kind-rag'],
  ['the project list', '#app-obj-RGB-project'],
  ['the search field of the project list', '#app-obj-RGB-project-combobox'],
  ['the setup list', '#app-obj-RGB-setup'],
  ['the search field of the setup list', '#app-obj-RGB-setup-combobox'],
  ['Create project', '[data-bs-target="#app-obj-RGB-create-projectModal"]'],
  ['Create setup', '[data-bs-target="#app-obj-RGB-create-setupModal"]'],
  ['Delete setup', '#app-obj-RGB-delete-setup'],
  ['Delete project', '#app-obj-RGB-delete-project'],
  // The two buttons that start a job of the setup: the ingestion runs the job
  // definition a manifest rewrites, and Run test writes one to SAS Content.
  ['Launch ingestion', '#app-obj-RGB-launch'],
  ['Run test', '#app-obj-RGB-test-run'],
];
// What has to be usable again once a write has ended, whatever is open then.
const ALWAYS_USABLE = ['the project list', 'the search field of the project list', 'Create project'];

/**
 * The buttons that start a write, read from the sources: every call of
 * `selectionLock.during(` belongs to the click handler it stands in, or to
 * the function it stands in, whose click handlers are then looked for. They
 * are off while any write is out - a save started between two requests of a
 * manifest wrote over what the manifest had written - so each of them joins
 * the controls every write path checks. WRITE_BUTTONS says where each one is
 * on the page; a button the sources name that is not in it, or an entry of
 * it the sources no longer name, is a failed check.
 */
const WRITE_BUTTONS = {
  'prompt-builder.ts': {
    promptDocSaveButton: ['Save documentation', '#app-obj-LPB .pb-doc-section > button'],
    deletePromptButton: ['Delete prompt-test', '#LPB-delete-prompt-button'],
    deleteProjectButton: ['Delete project', '#LPB-delete-project-button'],
    promptExperimentSaveButton: ['Save Experiments', '#app-obj-LPB-pet-save-button'],
    promptExperimentCreateModelButton: ['Manifest', '#app-obj-LPB-pet-create-model-button'],
    deleteButton: ['the delete of an optimization run', '#app-obj-LPB-pane-optimize .pb-optimize-history-delete'],
    optimizeCompareButton: ['Compare targets', '#app-obj-LPB-optimize-compare'],
    optimizeRunButton: ['Run optimization', '#app-obj-LPB-optimize-run'],
  },
  'rag-builder.ts': {
    deleteSetupButton: ['Delete setup', '#app-obj-RGB-delete-setup'],
    deleteProjectButton: ['Delete project', '#app-obj-RGB-delete-project'],
    docSaveButton: ['Save documentation', '#app-obj-RGB-save-documentation'],
    saveButton: ['Save setup', '#app-obj-RGB-save'],
    manifestButton: ['Manifest setup', '#app-obj-RGB-manifest'],
  },
};
function writeButtonsInSource(text) {
  const lines = text.split('\n');
  const indent = (line) => line.length - line.trimStart().length;
  const CLICK = /^\s*(\w+)\.(?:onclick\s*=|addEventListener\(\s*'click')/;
  const FUNCTION = /^\s*(?:async\s+)?function\s+(\w+)\s*\(|^\s*const\s+(\w+)\s*=\s*(?:async\s*)?(?:function\b|\()/;
  const buttons = new Set();
  const unresolved = [];
  const callersOf = (name) => {
    const found = [];
    lines.forEach((line, index) => {
      const click = CLICK.exec(line);
      if (click && lines.slice(index, index + 6).join('\n').match(new RegExp(`\\b${name}\\b`))) found.push(click[1]);
    });
    return found;
  };
  lines.forEach((line, at) => {
    if (!line.includes('selectionLock.during(')) return;
    const own = CLICK.exec(line);
    if (own) return buttons.add(own[1]);
    let level = indent(line);
    for (let index = at - 1; index >= 0; index--) {
      const above = lines[index];
      if (!above.trim() || indent(above) >= level) continue;
      level = indent(above);
      const click = CLICK.exec(above);
      if (click) return buttons.add(click[1]);
      const fn = FUNCTION.exec(above);
      if (fn) {
        const callers = callersOf(fn[1] || fn[2]);
        callers.forEach((caller) => buttons.add(caller));
        if (!callers.length) unresolved.push(`line ${at + 1} in ${fn[1] || fn[2]}`);
        return;
      }
    }
    unresolved.push(`line ${at + 1}`);
  });
  return { buttons: [...buttons], unresolved };
}

(async () => {
  // The write buttons of the sources join the controls of their builder.
  {
    const fs = require('fs');
    const path = require('path');
    const project = process.env.VERIFY_PROJECT || path.resolve(__dirname, '..', '..', '..');
    for (const [file, known] of Object.entries(WRITE_BUTTONS)) {
      let found = { buttons: [], unresolved: [`${file} not found under ${project} (set VERIFY_PROJECT)`] };
      try {
        found = writeButtonsInSource(fs.readFileSync(path.join(project, 'src', 'objects', file), 'utf8'));
      } catch {
        /* reported below */
      }
      const unknown = found.buttons.filter((name) => !known[name]);
      const gone = Object.keys(known).filter((name) => !found.buttons.includes(name));
      step(
        found.buttons.length > 0 && unknown.length === 0 && gone.length === 0 && found.unresolved.length === 0,
        `${file}: every button whose click starts a write is known to this suite (found: ${found.buttons.join(', ') || 'none'}; not known here: ${unknown.join(', ') || 'none'}; known here but not found: ${gone.join(', ') || 'none'}; writes no button was found for: ${found.unresolved.join(', ') || 'none'})`
      );
      (file === 'rag-builder.ts' ? RAG_CONTROLS : PROMPT_CONTROLS).push(...Object.values(known));
    }
  }
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const pageErrors = [];
  const dialogs = [];
  const newPage = async () => {
    const page = await browser.newPage({ viewport: { width: 1500, height: 1100 } });
    // A loaded machine can take longer than Playwright's 30 s to load the page.
    page.setDefaultNavigationTimeout(180000);
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
    return page;
  };
  // Each road starts on a page of its own. One that stops is a failed check,
  // and the next one runs all the same.
  const road = async (name, run) => {
    console.log(`---- ${name}`);
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
  const valueOf = (page, selector) => page.$eval(selector, (element) => element.value);
  const textOf = async (page, selector) => ((await page.textContent(selector)) || '').replace(/\s+/g, ' ').trim();

  /** Which of the controls a person could use right now. */
  const usable = (page, controls) =>
    page.evaluate(
      (list) =>
        list
          .filter(([, selector]) => {
            const element = document.querySelector(selector);
            return Boolean(element) && !element.matches(':disabled');
          })
          .map(([name]) => name),
      controls
    );

  /**
   * Answer the requests that write late: the first one after `first`
   * milliseconds, every further one after `rest`. `fault(request)` may name
   * what a request gets instead of its answer: 'abort', or a status.
   */
  const holdWrites = async (page, { first = 3000, rest = 150, fault = null } = {}) => {
    const hold = { out: 0, seen: [] };
    hold.handler = async (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      if (request.method() === 'GET' || path.startsWith('/scr/') || path.startsWith('/__')) return route.continue();
      hold.out += 1;
      hold.seen.push(`${request.method()} ${path}`);
      try {
        await sleep(hold.seen.length === 1 ? first : rest);
        const instead = fault ? fault(request) : null;
        if (instead === 'abort') await route.abort('failed');
        else if (instead) await route.fulfill({ status: instead, contentType: 'application/json', body: JSON.stringify({ message: 'refused by the suite' }) });
        else await route.fulfill({ response: await route.fetch() });
      } catch {
        /* the page was closed */
      } finally {
        hold.out -= 1;
      }
    };
    await page.route('**/*', hold.handler);
    return hold;
  };

  /**
   * One write, from its start to its end.
   *
   *   controls  the controls that decide what is open
   *   start     starts the write; has returned when the lock has to be on
   *   attempt   what a person tries meanwhile; answers whether it got there
   *   open      what is open, as a text
   *   idle      whether the write has ended, as far as the page shows it
   *
   * Answers what could be used while the write was out, whether the attempt
   * got anywhere, what was open before and after, and the requests.
   */
  const probe = async (page, { controls, start, attempt, open, idle = async () => true, hold: holdOptions }) => {
    const hold = await holdWrites(page, holdOptions);
    await resetLog();
    const openBefore = await open();
    await start(hold);
    const couldUse = new Set();
    let looks = 0;
    const look = (names) => {
      names.forEach((name) => couldUse.add(name));
      looks += 1;
    };
    look(await usable(page, controls));
    const gotThere = await attempt();
    // Look until the write has ended. A look counts when the write was still
    // out after it; the looks of the last half second, in which nothing was
    // out any more, are not about the write.
    let pending = [];
    let quietSince = null;
    const started = Date.now();
    for (;;) {
      const now = await usable(page, controls);
      const quiet = hold.seen.length > 0 && hold.out === 0 && (await getLog()).every((entry) => entry.status !== null) && (await idle());
      if (!quiet) {
        pending.forEach(look);
        pending = [];
        look(now);
        quietSince = null;
      } else {
        pending.push(now);
        quietSince = quietSince || Date.now();
        if (Date.now() - quietSince >= 600) break;
      }
      // Longer than the two minutes after which the page gives a request up,
      // which a loaded machine can reach.
      if (Date.now() - started > 300000) throw new Error('the write did not end');
      await sleep(60);
    }
    await page.unroute('**/*', hold.handler);
    await atRest();
    return {
      couldUse: [...couldUse],
      looks,
      gotThere,
      openBefore,
      openAfter: await open(),
      usableAfter: await usable(page, controls),
      writes: hold.seen,
      log: await getLog(),
    };
  };
  /** The two checks every write path has in common. */
  const lockedDuring = (seen, what, opening) => {
    step(
      seen.couldUse.length === 0 && seen.looks >= 3,
      `${what}: while the write is out, none of the controls that change what is open or start a write can be used (usable at some moment: ${seen.couldUse.join(', ') || 'none'}; looked ${seen.looks} times; requests that write: ${seen.writes.length})`
    );
    step(
      !seen.gotThere && seen.openAfter === seen.openBefore,
      `${what}: a person who tries ${opening} meanwhile does not get there, and what was open is still open when the write has ended (got there: ${seen.gotThere}; open before: ${seen.openBefore}; open after: ${seen.openAfter})`
    );
  };
  const usableAgain = (seen, what, also = []) => {
    const wanted = [...ALWAYS_USABLE, ...also];
    const missing = wanted.filter((name) => !seen.usableAfter.includes(name));
    step(missing.length === 0, `${what}: when the write has ended the selection can be used again (still locked: ${missing.join(', ') || 'none'})`);
  };
  // The requests of a log that write to a model of SAS Model Manager, by model.
  const modelsWrittenTo = (log) => [
    ...new Set(
      log
        .filter((entry) => entry.method !== 'GET')
        .map((entry) => /^\/modelRepository\/models\/([^/?]+)/.exec(entry.url))
        .filter(Boolean)
        .map((match) => match[1])
    ),
  ];
  // How long an attempt waits for its control. DURING a write a short time:
  // the control is to stay disabled, and Playwright would wait for it to be
  // enabled, i.e. for the write to end. AFTER the write the control is to be
  // usable, and a loaded machine may take seconds to draw it.
  const DURING = 500;
  const AFTER = 30000;
  // As a person chooses an entry: the search field that stands in for the
  // list, then the entry in the list it opens.
  const pickAsAPerson = async (page, select, value, timeout = DURING) => {
    try {
      await page.click(`${select}-combobox`, { timeout });
      await page.click(`${select}-combobox-list .dropdown-item[data-value="${value}"]`, { timeout });
      return true;
    } catch {
      return false;
    }
  };
  // As a person chooses a kind: a click on its card.
  const clickCard = async (page, radio, timeout = DURING) => {
    try {
      await page.click(`label[for="${radio}"]`, { timeout });
    } catch {
      /* the card took no click */
    }
    return page.$eval(`#${radio}`, (element) => element.checked);
  };
  // What a check after a write reads once the page has drawn it - or, when it
  // never does, after `timeout`: the check then fails with what is there.
  const eventually = async (read, timeout = AFTER) => {
    const start = Date.now();
    for (;;) {
      const value = await read();
      if (value || Date.now() - start > timeout) return value;
      await sleep(200);
    }
  };

  // ============================================================================
  // The Prompt Builder: prompt templates and decision templates
  // ============================================================================
  await fetch(BASE + '/__decision', { method: 'POST' });
  const lpb = (suffix) => `#app-obj-LPB-${suffix}`;
  const DOC_SAVE = '#app-obj-LPB .pb-doc-section > button';
  const trackerUploads = (log) =>
    log.filter((entry) => entry.method === 'POST' && /\/contents(\?|$)/.test(entry.url) && entry.body.includes('filename="Prompt-Experiment-Tracker.json"'));
  const savedRuns = (page) => page.$$eval('#app-obj-LPB-pet .pet-run-delete', (buttons) => buttons.length);
  const openPromptBuilder = async (page, kind = 'llm') => {
    await page.goto(APP_URL);
    await page.waitForSelector('#LPB-project-dropdown', { timeout: 15000 });
    if (kind === 'decision') await page.click('#LPB-kind-decision');
    await atRest();
  };
  const listed = async (page, select, value) => (await page.$$eval(`${select} option`, (options) => options.map((option) => option.value))).includes(value);
  const choosePromptProject = async (page, projectId) => {
    await goStep(page, 'LPB', 'setup');
    await waitUntil(() => listed(page, '#LPB-project-dropdown', projectId), `project ${projectId} is listed`);
    await page.selectOption('#LPB-project-dropdown', projectId);
    await atRest();
  };
  const openPromptTest = async (page, modelId) => {
    await goStep(page, 'LPB', 'setup');
    await waitUntil(() => listed(page, '#LPB-prompt-dropdown', modelId), `prompt-test ${modelId} is listed`);
    await page.selectOption('#LPB-prompt-dropdown', modelId);
    await atRest();
  };
  let made = 0;
  const createPromptTest = async (page) => {
    await goStep(page, 'LPB', 'setup');
    const before = await valueOf(page, '#LPB-prompt-dropdown');
    await page.click('button[data-bs-target="#promptBuilderCreatePromptModal"]');
    await page.waitForSelector('#promptBuilderCreatePromptModal.show');
    await page.fill('#promptBuilderCreatePromptName', `Write lock ${++made}`);
    await page.click('#promptBuilderCreatePromptModal .btn-primary');
    await page.waitForSelector('.modal.show', { state: 'detached' });
    await waitUntil(async () => {
      const now = await valueOf(page, '#LPB-prompt-dropdown');
      return now !== before && now.startsWith('model-new-');
    }, 'the new prompt-test is selected');
    await atRest();
    return valueOf(page, '#LPB-prompt-dropdown');
  };
  const openPromptTestIs = (page) => valueOf(page, '#LPB-prompt-dropdown');
  // The other prompt-test a person reaches for while a write is out.
  const reachForAnotherPromptTest = (page, modelId = 'model-free', timeout = DURING) => async () => {
    await goStep(page, 'LPB', 'setup');
    return pickAsAPerson(page, '#LPB-prompt-dropdown', modelId, timeout);
  };
  // The runs SAS Model Manager holds for the fixture prompt-test.
  const storedRows = async () => (await (await fetch(BASE + '/files/files/trk-1/content')).json()).length;

  await road('prompt template: Save Experiments', async (page) => {
    await openPromptBuilder(page);
    await choosePromptProject(page, 'proj-1');
    await openPromptTest(page, 'model-used');
    const rows = await storedRows();
    await goStep(page, 'LPB', 'build');
    const seen = await probe(page, {
      controls: PROMPT_CONTROLS,
      start: () => page.click(lpb('pet-save-button')),
      attempt: reachForAnotherPromptTest(page),
      open: () => openPromptTestIs(page),
      idle: async () => (await textOf(page, lpb('pet-save-button'))) === 'Save Experiments',
    });
    lockedDuring(seen, 'Save Experiments', 'to open another prompt-test');
    const uploads = trackerUploads(seen.log);
    const uploaded = uploads.map((entry) => (multipartJson(entry.body) || []).length);
    step(
      uploads.length === 1 && /\/models\/model-used\//.test(uploads[0].url) && uploaded[0] === rows && rows > 0,
      `Save Experiments: the tracker that is uploaded to the prompt-test holds its runs (uploads: ${uploads.length}, to: ${modelsWrittenTo(seen.log).join(', ') || 'none'}, rows: ${uploaded.join(', ') || 'none'} of ${rows} stored)`
    );
    const note = await textOf(page, lpb('pet-save-only-result'));
    step(
      /has been saved/i.test(note) && note.includes('/models/model-used') && seen.openAfter === 'model-used',
      `Save Experiments: the note that the experiments are saved stands beside the button of the prompt-test they were saved for (open: ${seen.openAfter}, note: "${clip(note)}")`
    );
    usableAgain(seen, 'Save Experiments', ['the prompt-test list', 'Create prompt-test', 'Delete prompt-test']);
    const now = await reachForAnotherPromptTest(page, 'model-free', AFTER)();
    await atRest();
    await eventually(async () => (await openPromptTestIs(page)) === 'model-free');
    step(
      now && (await openPromptTestIs(page)) === 'model-free' && (await textOf(page, lpb('pet-save-only-result'))) === '',
      `Save Experiments: the same attempt then opens the other prompt-test, which shows no note of that save (got there: ${now}, open: ${await openPromptTestIs(page)}, note: "${clip(await textOf(page, lpb('pet-save-only-result')))}")`
    );
  });

  await road('prompt template: Save Experiments whose upload fails', async (page) => {
    await openPromptBuilder(page);
    await choosePromptProject(page, 'proj-1');
    await openPromptTest(page, 'model-used');
    await goStep(page, 'LPB', 'build');
    const seen = await probe(page, {
      controls: PROMPT_CONTROLS,
      start: () => page.click(lpb('pet-save-button')),
      attempt: reachForAnotherPromptTest(page),
      open: () => openPromptTestIs(page),
      idle: async () => (await textOf(page, lpb('pet-save-button'))) === 'Save Experiments',
      hold: { fault: (request) => ((request.postData() || '').includes('filename="Prompt-Experiment-Tracker.json"') ? 'abort' : null) },
    });
    lockedDuring(seen, 'Save Experiments, upload fails', 'to open another prompt-test');
    const failed = await page.$(`${lpb('pet-save-only-result')} .pet-save-failed`);
    step(
      Boolean(failed) && seen.openAfter === 'model-used',
      `Save Experiments, upload fails: the line that the save stopped stands on the prompt-test it was made for (line shown: ${Boolean(failed)}, open: ${seen.openAfter})`
    );
    usableAgain(seen, 'Save Experiments, upload fails', ['the prompt-test list', 'Create prompt-test', 'Delete prompt-test']);
  });

  await road('prompt template: Save documentation', async (page) => {
    await openPromptBuilder(page);
    await choosePromptProject(page, 'proj-1');
    await openPromptTest(page, 'model-used');
    await page.click('#app-obj-LPB .pb-doc-section > summary');
    await page.fill('#LPB-doc-modelPurpose', 'Typed for the write lock');
    const kindAndItem = async () => `${(await page.$eval('#LPB-kind-llm', (radio) => radio.checked)) ? 'LLM' : 'decision'} kind, ${await openPromptTestIs(page)}`;
    const seen = await probe(page, {
      controls: PROMPT_CONTROLS,
      start: () => page.click(DOC_SAVE),
      // The other kind of template: its card empties project and prompt-test.
      attempt: () => clickCard(page, 'LPB-kind-decision'),
      open: kindAndItem,
      idle: async () => !(await page.$(`${DOC_SAVE} .spinner-border`)),
    });
    lockedDuring(seen, 'Save documentation', 'to change to the other kind of template');
    const puts = seen.log.filter((entry) => entry.method === 'PUT' && entry.body.includes('Typed for the write lock'));
    step(
      puts.length === 1 && /\/models\/model-used$/.test(puts[0].url) && modelsWrittenTo(seen.log).join(',') === 'model-used',
      `Save documentation: what was typed is written once, to the prompt-test that is open (requests carrying it: ${puts.length}, written to: ${modelsWrittenTo(seen.log).join(', ') || 'none'})`
    );
    usableAgain(seen, 'Save documentation', ['the prompt-test list', 'the card of the decision kind', 'Delete prompt-test']);
    step(!(await page.isDisabled(DOC_SAVE)) && (await valueOf(page, '#LPB-doc-modelPurpose')) === 'Typed for the write lock', 'Save documentation: its button is usable again and the text is still in its field');
  });

  await road('prompt template: Manifest', async (page) => {
    // A prompt-test with a best run and a manifest configuration.
    await fetch(BASE + '/__manifested', { method: 'POST', body: JSON.stringify({ on: true }) });
    try {
      await openPromptBuilder(page);
      await choosePromptProject(page, 'proj-1');
      await openPromptTest(page, 'model-used');
      const rows = await storedRows();
      await goStep(page, 'LPB', 'finalize');
      const label = await textOf(page, lpb('pet-create-model-button'));
      const seen = await probe(page, {
        controls: PROMPT_CONTROLS,
        start: () => page.click(lpb('pet-create-model-button')),
        attempt: reachForAnotherPromptTest(page),
        open: () => openPromptTestIs(page),
        idle: async () => (await textOf(page, lpb('pet-create-model-button'))) === label,
      });
      lockedDuring(seen, 'Manifest (prompt template)', 'to open another prompt-test');
      const uploaded = trackerUploads(seen.log).map((entry) => (multipartJson(entry.body) || []).length);
      const scoreCode = seen.log.filter((entry) => entry.method === 'POST' && entry.body.includes('def scoreModel('));
      const failed = await page.$$eval(`${lpb('pet-save-result')} .pet-manifest-failed`, (lines) => lines.length);
      step(
        modelsWrittenTo(seen.log).join(',') === 'model-used' && uploaded.join(',') === String(rows) && scoreCode.length === 1 && failed === 0,
        `Manifest (prompt template): the runs are saved and the score code is stored, on the prompt-test that is open and on no other (written to: ${modelsWrittenTo(seen.log).join(', ') || 'none'}, rows of the saved tracker: ${uploaded.join(', ') || 'none'} of ${rows}, score codes: ${scoreCode.length}, lines that say it failed: ${failed})`
      );
      const note = await textOf(page, lpb('pet-save-result'));
      step(note.includes('/models/model-used') && seen.openAfter === 'model-used', `Manifest (prompt template): what it has to say stands on that prompt-test (open: ${seen.openAfter}, note: "${clip(note)}")`);
      usableAgain(seen, 'Manifest (prompt template)', ['the prompt-test list', 'Create prompt-test', 'Delete prompt-test']);
    } finally {
      await fetch(BASE + '/__manifested', { method: 'POST', body: JSON.stringify({ on: false }) });
    }
  });

  await road('decision template: Manifest', async (page) => {
    await openPromptBuilder(page, 'decision');
    await choosePromptProject(page, 'proj-2');
    await openPromptTest(page, 'model-dec');
    // The runs are drawn a moment after the last answer of the opening.
    const runs = await eventually(() => savedRuns(page));
    await goStep(page, 'LPB', 'finalize');
    const label = await textOf(page, lpb('pet-create-model-button'));
    const seen = await probe(page, {
      controls: PROMPT_CONTROLS,
      start: () => page.click(lpb('pet-create-model-button')),
      // The other kind of template: its card closes the decision template.
      attempt: async () => {
        await goStep(page, 'LPB', 'setup');
        return clickCard(page, 'LPB-kind-llm');
      },
      open: async () => `${(await page.$eval('#LPB-kind-decision', (radio) => radio.checked)) ? 'decision' : 'LLM'} kind, ${await openPromptTestIs(page)}`,
      idle: async () => (await textOf(page, lpb('pet-create-model-button'))) === label,
    });
    lockedDuring(seen, 'Manifest (decision template)', 'to change to the other kind of template');
    const uploaded = trackerUploads(seen.log).map((entry) => new Set((multipartJson(entry.body) || []).map((row) => row.runId)).size);
    const scoreCode = seen.log.filter((entry) => entry.method === 'POST' && entry.body.includes('def scoreModel('));
    const failed = await page.$$eval(`${lpb('pet-save-result')} .pet-manifest-failed`, (lines) => lines.length);
    step(
      modelsWrittenTo(seen.log).join(',') === 'model-dec' && uploaded.join(',') === String(runs) && runs > 0 && scoreCode.length === 1 && failed === 0,
      `Manifest (decision template): the runs are saved and the score code is stored, on the template that is open and on no other (written to: ${modelsWrittenTo(seen.log).join(', ') || 'none'}, runs in the saved tracker: ${uploaded.join(', ') || 'none'} of ${runs} on the page, score codes: ${scoreCode.length}, lines that say it failed: ${failed})`
    );
    usableAgain(seen, 'Manifest (decision template)', ['the prompt-test list', 'Create prompt-test', 'Delete prompt-test']);
  });

  await road('prompt template: Delete prompt-test', async (page) => {
    await openPromptBuilder(page);
    await choosePromptProject(page, 'proj-1');
    const created = await createPromptTest(page);
    await page.click('#LPB-delete-prompt-button');
    await page.waitForSelector('.modal.show .btn-danger');
    const seen = await probe(page, {
      controls: PROMPT_CONTROLS,
      start: async (hold) => {
        await page.click('.modal.show .btn-danger');
        await waitUntil(async () => hold.seen.length > 0, 'the delete request is out');
      },
      attempt: reachForAnotherPromptTest(page, 'model-used'),
      // What is open when the delete has ended is nothing: asked for apart.
      open: async () => 'the prompt-test that is being deleted',
      idle: async () => !(await page.$('#LPB-delete-prompt-button .spinner-border')),
    });
    lockedDuring(seen, 'Delete prompt-test', 'to open another prompt-test');
    const deletes = seen.log.filter((entry) => entry.method === 'DELETE').map((entry) => entry.url);
    const after = await openPromptTestIs(page);
    step(
      deletes.join(',') === `/modelRepository/models/${created}` && !after.startsWith('model-') && !(await listed(page, '#LPB-prompt-dropdown', created)) && (await savedRuns(page)) === 0,
      `Delete prompt-test: the prompt-test is deleted and has left the list, and no prompt-test is open (deleted: ${deletes.join(', ') || 'none'}, picker: "${after}", runs on the page: ${await savedRuns(page)})`
    );
    usableAgain(seen, 'Delete prompt-test', ['the prompt-test list', 'Create prompt-test', 'Delete project']);
    const now = await reachForAnotherPromptTest(page, 'model-used', AFTER)();
    await atRest();
    await eventually(async () => (await openPromptTestIs(page)) === 'model-used' && (await savedRuns(page)) > 0);
    step(now && (await openPromptTestIs(page)) === 'model-used' && (await savedRuns(page)) > 0, `Delete prompt-test: the same attempt then opens the other prompt-test with its runs (got there: ${now}, open: ${await openPromptTestIs(page)}, runs: ${await savedRuns(page)})`);
  });

  await road('prompt template: Delete project', async (page) => {
    await openPromptBuilder(page);
    await goStep(page, 'LPB', 'setup');
    await page.click('button[data-bs-target="#promptBuilderCreateProjectModal"]');
    await page.waitForSelector('#promptBuilderCreateProjectModal.show');
    await page.fill('#promptBuilderCreateProjectName', 'Write lock project');
    await page.click('#promptBuilderCreateProjectModal .btn-primary');
    await page.waitForSelector('.modal.show', { state: 'detached' });
    await waitUntil(async () => (await valueOf(page, '#LPB-project-dropdown')) === 'proj-new', 'the new project is selected');
    await atRest();
    const created = await createPromptTest(page);
    await page.click('#LPB-delete-project-button');
    // One dialog per prompt-test of the project; the delete follows the last.
    await page.waitForSelector('.modal.show .btn-danger');
    const openProject = () => valueOf(page, '#LPB-project-dropdown');
    const seen = await probe(page, {
      controls: PROMPT_CONTROLS,
      start: async (hold) => {
        await page.click('.modal.show .btn-danger');
        await waitUntil(async () => hold.seen.length > 0, 'the first delete request is out');
      },
      attempt: async () => {
        await goStep(page, 'LPB', 'setup');
        return pickAsAPerson(page, '#LPB-project-dropdown', 'proj-1');
      },
      open: async () => 'the project that is being deleted',
      idle: async () => !(await page.$('#LPB-delete-project-button .spinner-border')),
    });
    lockedDuring(seen, 'Delete project', 'to choose another project');
    const deletes = seen.log.filter((entry) => entry.method === 'DELETE').map((entry) => entry.url);
    const after = await openProject();
    step(
      deletes.join(',') === `/modelRepository/models/${created},/modelRepository/projects/proj-new` && after !== 'proj-new' && after !== 'proj-1' && !(await listed(page, '#LPB-project-dropdown', 'proj-new')),
      `Delete project: its prompt-test and the project are deleted, the project has left the list and none is chosen (deleted: ${deletes.join(', ') || 'none'}, picker: "${after}")`
    );
    usableAgain(seen, 'Delete project', ['Create prompt-test']);
    const now = await pickAsAPerson(page, '#LPB-project-dropdown', 'proj-1', AFTER);
    await atRest();
    await eventually(async () => (await openProject()) === 'proj-1' && (await listed(page, '#LPB-prompt-dropdown', 'model-used')));
    step(now && (await openProject()) === 'proj-1' && (await listed(page, '#LPB-prompt-dropdown', 'model-used')), `Delete project: the same attempt then chooses the other project and lists its prompt-tests (got there: ${now}, project: ${await openProject()})`);
  });

  await road('prompt template: the delete of an optimization run', async (page) => {
    await openPromptBuilder(page);
    await choosePromptProject(page, 'proj-1');
    await openPromptTest(page, 'model-used');
    await goStep(page, 'LPB', 'optimize');
    await page.waitForSelector(`${lpb('optimize-history')} .pb-optimize-history-delete`);
    const before = await page.$$eval(`${lpb('optimize-history')} .pb-optimize-history-delete`, (buttons) => buttons.length);
    await page.click(`${lpb('optimize-history')} .pb-optimize-history-delete`);
    await page.waitForSelector('.modal.show .btn-danger');
    const seen = await probe(page, {
      controls: PROMPT_CONTROLS,
      start: async (hold) => {
        await page.click('.modal.show .btn-danger');
        await waitUntil(async () => hold.seen.length > 0, 'the first request of the delete is out');
      },
      attempt: reachForAnotherPromptTest(page),
      open: () => openPromptTestIs(page),
    });
    lockedDuring(seen, 'Delete of an optimization run', 'to open another prompt-test');
    await goStep(page, 'LPB', 'optimize');
    await atRest();
    const left = await page.$$eval(`${lpb('optimize-history')} .pb-optimize-history-delete`, (buttons) => buttons.length);
    step(
      modelsWrittenTo(seen.log).join(',') === 'model-used' && left === before - 1,
      `Delete of an optimization run: the run is taken out of the tracker of the prompt-test that is open, whose history then lists one run less (written to: ${modelsWrittenTo(seen.log).join(', ') || 'none'}, runs listed: ${left} after ${before})`
    );
    usableAgain(seen, 'Delete of an optimization run', ['the prompt-test list', 'Create prompt-test', 'Delete prompt-test']);
  });

  // Last of the Prompt Builder: a job the mock has launched stays launched.
  const launches = (log) => log.filter((entry) => entry.method === 'POST' && entry.url === '/jobExecution/jobs');
  const launchedFor = (log) =>
    launches(log).map((entry) => {
      try {
        const args = JSON.parse(entry.body).arguments;
        return `${args.promptModelId}${args.mode ? ` (${args.mode})` : ''}`;
      } catch {
        return '?';
      }
    });
  await road('prompt template: Compare targets, which saves the experiments first', async (page) => {
    await openPromptBuilder(page);
    await choosePromptProject(page, 'proj-1');
    await openPromptTest(page, 'model-used');
    const rows = await storedRows();
    // A comparison needs ten examples: a CAS table that holds twelve.
    await goStep(page, 'LPB', 'optimize');
    await page.check(lpb('optimize-dataset-source-cas'));
    const choose = (selector, value) =>
      page.$eval(
        selector,
        (select, wanted) => {
          select.value = wanted;
          select.dispatchEvent(new Event('change', { bubbles: true }));
        },
        value
      );
    await waitUntil(async () => (await page.$$(`${lpb('optimize-cas-lib')} option`)).length >= 2, 'the caslibs of the CAS server are listed');
    await choose(lpb('optimize-cas-lib'), 'Public');
    await waitUntil(async () => (await page.$$(`${lpb('optimize-cas-table')} option`)).length >= 2, 'the tables of the caslib are listed');
    await choose(lpb('optimize-cas-table'), 'BIG_DATA');
    await atRest();
    await page.click(lpb('optimize-compare'));
    await page.waitForSelector('.modal.show .btn-primary');
    for (const candidate of await page.$$('.modal.show input[type="checkbox"]')) await candidate.check();
    const seen = await probe(page, {
      controls: PROMPT_CONTROLS,
      start: async (hold) => {
        await page.click('.modal.show .btn-primary');
        await waitUntil(async () => hold.seen.length > 0, 'the first request that writes is out');
      },
      attempt: reachForAnotherPromptTest(page),
      open: () => openPromptTestIs(page),
      idle: async () => launches(await getLog()).some((entry) => entry.status !== null),
    });
    lockedDuring(seen, 'Compare targets', 'to open another prompt-test');
    const uploaded = trackerUploads(seen.log).map((entry) => (multipartJson(entry.body) || []).length);
    step(
      uploaded.join(',') === String(rows) && launchedFor(seen.log).join(',') === 'model-used (compare)' && modelsWrittenTo(seen.log).join(',') === 'model-used',
      `Compare targets: the runs of the prompt-test are saved before the comparison is launched for it (rows of the saved tracker: ${uploaded.join(', ') || 'none'} of ${rows}, job launched for: ${launchedFor(seen.log).join(', ') || 'none'}, written to: ${modelsWrittenTo(seen.log).join(', ') || 'none'})`
    );
    usableAgain(seen, 'Compare targets', ['the prompt-test list', 'Create prompt-test']);
  });

  await road('prompt template: Run optimization, which saves the experiments first', async (page) => {
    await openPromptBuilder(page);
    await choosePromptProject(page, 'proj-1');
    await openPromptTest(page, 'model-used');
    const rows = await storedRows();
    await goStep(page, 'LPB', 'optimize');
    await page.selectOption(lpb('optimize-target'), 'demo_llm');
    await waitUntil(async () => !(await page.isDisabled(lpb('optimize-run'))), 'the optimization can be started');
    const seen = await probe(page, {
      controls: PROMPT_CONTROLS,
      start: () => page.click(lpb('optimize-run')),
      attempt: reachForAnotherPromptTest(page),
      open: () => openPromptTestIs(page),
      idle: async () => launches(await getLog()).some((entry) => entry.status !== null),
    });
    lockedDuring(seen, 'Run optimization', 'to open another prompt-test');
    const uploaded = trackerUploads(seen.log).map((entry) => (multipartJson(entry.body) || []).length);
    const launched = launchedFor(seen.log);
    step(
      uploaded.join(',') === String(rows) && launched.join(',') === 'model-used' && modelsWrittenTo(seen.log).join(',') === 'model-used',
      `Run optimization: the runs of the prompt-test are saved before its job is launched (rows of the saved tracker: ${uploaded.join(', ') || 'none'} of ${rows}, job launched for: ${launched.join(', ') || 'none'}, written to: ${modelsWrittenTo(seen.log).join(', ') || 'none'})`
    );
    // The job runs on; what is open may change again.
    usableAgain(seen, 'Run optimization', ['the prompt-test list', 'Create prompt-test']);
  });

  // ============================================================================
  // The RAG Builder
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
    await waitUntil(() => listed(page, rgb('project'), projectId), `RAG project ${projectId} is listed`);
    await page.selectOption(rgb('project'), projectId);
    await atRest();
  };
  const openRagSetup = async (page, setupId) => {
    await goStep(page, 'RGB', 'setup');
    await waitUntil(() => listed(page, rgb('setup'), setupId), `RAG setup ${setupId} is listed`);
    await page.selectOption(rgb('setup'), setupId);
    await atRest();
  };
  const createRagSetup = async (page) => {
    const name = `Write lock setup ${++made}`;
    await goStep(page, 'RGB', 'setup');
    await page.click(`[data-bs-target="${rgb('create-setupModal')}"]`);
    await page.waitForSelector(`${rgb('create-setupModal')}.show`);
    await page.fill(rgb('create-setupName'), name);
    await page.click(`${rgb('create-setupModal')} .btn-primary`);
    await page.waitForSelector('.modal.show', { state: 'detached' });
    let id = '';
    await waitUntil(async () => {
      const model = (await ragState()).models.find((candidate) => candidate.name === name);
      id = model ? model.id : '';
      return Boolean(id) && (await valueOf(page, rgb('setup'))) === id;
    }, `the new RAG setup ${name} is selected`);
    await atRest();
    return id;
  };
  const openSetupIs = (page) => valueOf(page, rgb('setup'));
  const reachForAnotherSetup = (page, setupId = 'rag-setup-2', timeout = DURING) => async () => {
    await goStep(page, 'RGB', 'setup');
    return pickAsAPerson(page, rgb('setup'), setupId, timeout);
  };
  // What the result box beside Save setup and Manifest setup says.
  const saveResult = async (page) => ({
    text: await textOf(page, rgb('save-result')),
    kind: await page.$$eval(`${rgb('save-result')} .alert`, (alerts) => alerts.map((alert) => [...alert.classList].find((name) => /^alert-/.test(name))).join(',')),
  });
  const filesOf = async (setupId) => ((await ragState()).contents[setupId] || []).join(', ');

  await road('RAG setup: Save setup', async (page) => {
    await openRagBuilder(page);
    await chooseRagProject(page, 'rag-proj');
    await openRagSetup(page, 'rag-setup-1');
    const otherBefore = await filesOf('rag-setup-2');
    await goStep(page, 'RGB', 'store');
    const seen = await probe(page, {
      controls: RAG_CONTROLS,
      start: () => page.click(rgb('save')),
      attempt: reachForAnotherSetup(page),
      open: () => openSetupIs(page),
      idle: async () => !(await page.$eval(rgb('save'), (button) => button.disabled)),
    });
    lockedDuring(seen, 'Save setup', 'to open another setup');
    step(
      modelsWrittenTo(seen.log).join(',') === 'rag-setup-1' && (await filesOf('rag-setup-2')) === otherBefore && seen.writes.length >= 4,
      `Save setup: every request of the save goes to the setup that is open, and the other setup holds what it held (written to: ${modelsWrittenTo(seen.log).join(', ') || 'none'}; requests that write: ${seen.writes.length}; files of the other setup: "${await filesOf('rag-setup-2')}", before: "${otherBefore}")`
    );
    const note = await saveResult(page);
    step(
      /saved/i.test(note.text) && note.kind === 'alert-success' && seen.openAfter === 'rag-setup-1',
      `Save setup: the note that the setup is saved stands on the setup that was saved (open: ${seen.openAfter}, note: "${clip(note.text)}")`
    );
    usableAgain(seen, 'Save setup', ['the setup list', 'Create setup', 'Delete setup']);
    const now = await reachForAnotherSetup(page, 'rag-setup-2', AFTER)();
    await atRest();
    await eventually(async () => (await openSetupIs(page)) === 'rag-setup-2');
    step(
      now && (await openSetupIs(page)) === 'rag-setup-2' && (await saveResult(page)).text === '',
      `Save setup: the same attempt then opens the other setup, which shows no note of that save (got there: ${now}, open: ${await openSetupIs(page)}, note: "${clip((await saveResult(page)).text)}")`
    );
  });

  await road('RAG setup: Save setup that is refused', async (page) => {
    await openRagBuilder(page);
    await chooseRagProject(page, 'rag-proj');
    await openRagSetup(page, 'rag-setup-1');
    await goStep(page, 'RGB', 'store');
    const seen = await probe(page, {
      controls: RAG_CONTROLS,
      start: () => page.click(rgb('save')),
      attempt: reachForAnotherSetup(page),
      open: () => openSetupIs(page),
      idle: async () => !(await page.$eval(rgb('save'), (button) => button.disabled)),
      hold: { fault: (request) => (request.method() === 'POST' && /\/contents/.test(request.url()) ? 500 : null) },
    });
    lockedDuring(seen, 'Save setup, refused', 'to open another setup');
    const note = await saveResult(page);
    step(
      note.kind === 'alert-danger' && /failed/i.test(note.text) && seen.openAfter === 'rag-setup-1',
      `Save setup, refused: the note that the save failed stands on the setup it was made for (open: ${seen.openAfter}, note: "${clip(note.text)}")`
    );
    usableAgain(seen, 'Save setup, refused', ['the setup list', 'Create setup', 'Delete setup']);
  });

  await road('RAG setup: Save documentation', async (page) => {
    await openRagBuilder(page);
    await chooseRagProject(page, 'rag-proj');
    await openRagSetup(page, 'rag-setup-1');
    const section = `${rgb('pane-setup')} .pb-doc-section`;
    if (!(await page.$eval(section, (details) => details.open))) await page.click(`${section} > summary`);
    await page.fill(rgb('setup-doc-modelPurpose'), 'Typed for the write lock');
    const flowAndItem = async () => `${(await page.$eval('#app-area-rag', (area) => area.hidden)) ? 'the prompt flow' : 'the RAG flow'}, ${await openSetupIs(page)}`;
    const seen = await probe(page, {
      controls: RAG_CONTROLS,
      start: () => page.click(rgb('save-documentation')),
      // The card of another kind leaves the RAG flow.
      attempt: async () => {
        await clickCard(page, 'RGB-kind-llm');
        return page.$eval('#app-area-rag', (area) => area.hidden);
      },
      open: flowAndItem,
      idle: async () => !(await page.$(`${rgb('save-documentation')} .spinner-border`)),
    });
    lockedDuring(seen, 'Save documentation (RAG)', 'to change to another kind');
    const carrying = seen.log.filter((entry) => entry.method !== 'GET' && entry.body.includes('Typed for the write lock'));
    step(
      modelsWrittenTo(seen.log).join(',') === 'rag-setup-1' && carrying.length >= 1,
      `Save documentation (RAG): what was typed is written to the setup that is open and to no other (written to: ${modelsWrittenTo(seen.log).join(', ') || 'none'}, requests carrying it: ${carrying.length})`
    );
    usableAgain(seen, 'Save documentation (RAG)', ['the setup list', 'the card of the LLM kind', 'Delete setup']);
  });

  await road('RAG setup: Manifest setup', async (page) => {
    await openRagBuilder(page);
    await chooseRagProject(page, 'rag-proj');
    await openRagSetup(page, 'rag-setup-1');
    const otherBefore = await filesOf('rag-setup-2');
    await goStep(page, 'RGB', 'store');
    const label = await textOf(page, rgb('manifest'));
    const seen = await probe(page, {
      controls: RAG_CONTROLS,
      start: () => page.click(rgb('manifest')),
      attempt: reachForAnotherSetup(page),
      open: () => openSetupIs(page),
      idle: async () => (await textOf(page, rgb('manifest'))) === label && !(await page.$eval(rgb('manifest'), (button) => button.disabled)),
    });
    lockedDuring(seen, 'Manifest setup', 'to open another setup');
    const scoreCode = seen.log.filter((entry) => entry.method === 'POST' && entry.body.includes('filename="retrieve_context.py"'));
    step(
      modelsWrittenTo(seen.log).join(',') === 'rag-setup-1' && scoreCode.length === 1 && (await filesOf('rag-setup-2')) === otherBefore,
      `Manifest setup: every stage writes to the setup that is open, the retrieval model included, and the other setup holds what it held (written to: ${modelsWrittenTo(seen.log).join(', ') || 'none'}; requests that write: ${seen.writes.length}; retrieval models stored: ${scoreCode.length}; files of the other setup: "${await filesOf('rag-setup-2')}")`
    );
    const note = await saveResult(page);
    step(
      /manifested/i.test(note.text) && !/stopped/i.test(note.text) && seen.openAfter === 'rag-setup-1',
      `Manifest setup: the note that the setup is manifested stands on that setup (open: ${seen.openAfter}, note: "${clip(note.text)}")`
    );
    usableAgain(seen, 'Manifest setup', ['the setup list', 'Create setup', 'Delete setup']);
  });

  await road('RAG setup: Delete setup', async (page) => {
    await openRagBuilder(page);
    await chooseRagProject(page, 'rag-proj');
    const created = await createRagSetup(page);
    await page.click(rgb('delete-setup'));
    await page.waitForSelector('.modal.show .btn-danger');
    const seen = await probe(page, {
      controls: RAG_CONTROLS,
      start: async (hold) => {
        await page.click('.modal.show .btn-danger');
        await waitUntil(async () => hold.seen.length > 0, 'the delete request is out');
      },
      attempt: reachForAnotherSetup(page, 'rag-setup-1'),
      open: async () => 'the setup that is being deleted',
    });
    lockedDuring(seen, 'Delete setup', 'to open another setup');
    const deletes = seen.log.filter((entry) => entry.method === 'DELETE').map((entry) => entry.url);
    const after = await openSetupIs(page);
    const editorShown = await page.isVisible(rgb('save-documentation'));
    step(
      deletes.join(',') === `/modelRepository/models/${created}` && !/^(rag-setup|model-new)/.test(after) && !(await listed(page, rgb('setup'), created)) && !editorShown,
      `Delete setup: the setup is deleted and has left the list, and no setup is open (deleted: ${deletes.join(', ') || 'none'}, picker: "${after}", editor shown: ${editorShown})`
    );
    usableAgain(seen, 'Delete setup', ['the setup list', 'Create setup', 'Delete project']);
    const now = await reachForAnotherSetup(page, 'rag-setup-1', AFTER)();
    await atRest();
    await eventually(async () => (await openSetupIs(page)) === 'rag-setup-1' && (await valueOf(page, rgb('tables-prefix'))) === 'RAGDEMO');
    step(now && (await openSetupIs(page)) === 'rag-setup-1' && (await valueOf(page, rgb('tables-prefix'))) === 'RAGDEMO', `Delete setup: the same attempt then opens the other setup with its configuration (got there: ${now}, open: ${await openSetupIs(page)}, table prefix: ${await valueOf(page, rgb('tables-prefix'))})`);
  });

  await road('RAG setup: Delete project', async (page) => {
    await openRagBuilder(page);
    await goStep(page, 'RGB', 'setup');
    await page.click(`[data-bs-target="${rgb('create-projectModal')}"]`);
    await page.waitForSelector(`${rgb('create-projectModal')}.show`);
    await page.fill(rgb('create-projectName'), 'Write lock RAG project');
    await page.click(`${rgb('create-projectModal')} .btn-primary`);
    await page.waitForSelector('.modal.show', { state: 'detached' });
    await waitUntil(async () => (await valueOf(page, rgb('project'))).startsWith('rag-proj-new-'), 'the new RAG project is selected');
    await atRest();
    const project = await valueOf(page, rgb('project'));
    const created = await createRagSetup(page);
    await page.click(rgb('delete-project'));
    await page.waitForSelector('.modal.show .btn-danger');
    const openProject = () => valueOf(page, rgb('project'));
    const seen = await probe(page, {
      controls: RAG_CONTROLS,
      start: async (hold) => {
        await page.click('.modal.show .btn-danger');
        await waitUntil(async () => hold.seen.length > 0, 'the first delete request is out');
      },
      attempt: async () => {
        await goStep(page, 'RGB', 'setup');
        return pickAsAPerson(page, rgb('project'), 'rag-proj');
      },
      open: async () => 'the project that is being deleted',
    });
    lockedDuring(seen, 'Delete project (RAG)', 'to choose another project');
    const deletes = seen.log.filter((entry) => entry.method === 'DELETE').map((entry) => entry.url);
    const after = await openProject();
    step(
      deletes.join(',') === `/modelRepository/models/${created},/modelRepository/projects/${project}` && !after.startsWith('rag-proj') && !(await listed(page, rgb('project'), project)),
      `Delete project (RAG): its setup and the project are deleted, the project has left the list and none is chosen (deleted: ${deletes.join(', ') || 'none'}, picker: "${after}")`
    );
    usableAgain(seen, 'Delete project (RAG)', ['Create setup']);
    const now = await pickAsAPerson(page, rgb('project'), 'rag-proj', AFTER);
    await atRest();
    await eventually(async () => (await openProject()) === 'rag-proj' && (await listed(page, rgb('setup'), 'rag-setup-1')));
    step(now && (await openProject()) === 'rag-proj' && (await listed(page, rgb('setup'), 'rag-setup-1')), `Delete project (RAG): the same attempt then chooses the other project and lists its setups (got there: ${now}, project: ${await openProject()})`);
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
