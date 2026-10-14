/**
 * What a write answered is what the page says, and nothing of one item, one
 * write or one dialog stays behind for the next. Run against a FRESH mock
 * instance.
 *
 * The roads, each on a page of its own:
 *
 *   - Writes SAS Model Manager refuses with a status are writes that failed:
 *     the uploads, deletes and the last attribute update of both manifests of
 *     the Prompt Builder, the model version and the delete of the stored
 *     tracker of Save Experiments, the writes of the delete of an
 *     optimization run, and in the RAG Builder the job reference and the
 *     Studio flow copy of Manifest setup and its model version.
 *   - A delete whose request gets no answer says so; a Delete prompt-test or
 *     Delete project opens no dialog when another item was opened while the
 *     usage was asked.
 *   - While a write is out, a change of a picker sent by a script (as the
 *     search field of a list sends one) is undone and reaches no handler; a
 *     write that never gets an answer is given up after two minutes (the
 *     clock of the page is moved on), and the selection is usable again.
 *   - The Manifest button of the RAG Builder is off until the last request of
 *     the manifest has ended.
 *   - What a setup showed - the line naming its generated flow, the banner of
 *     its failed run - goes with it; a create dialog opens empty after a
 *     creation; the menu of a search field holds nothing once it is closed.
 *   - An ingestion that runs is shown when its setup is opened on a page that
 *     was loaded after the launch, and one whose state cannot be read three
 *     times in a row is no longer followed: Launch is offered again.
 *
 * The suite goes on after a check that failed and after a road that stopped.
 * The mock is found at VERIFY_BASE (default http://localhost:4173).
 */
const { chromium } = require('playwright');

const BASE = (process.env.VERIFY_BASE || 'http://localhost:4173').replace(/\/+$/, '');
const results = [];

function step(ok, label) {
  results.push(`${ok ? 'OK ' : 'FAIL'}  ${label}`);
  console.log(`${ok ? 'OK ' : 'FAIL'}  ${label}`);
  if (!ok) process.exitCode = 1;
}
const getLog = async () => (await fetch(BASE + '/__log')).json();
const resetLog = async () => fetch(BASE + '/__reset', { method: 'POST' });
const post = (route, body) => fetch(BASE + route, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitUntil(fn, label, timeout = 15000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await fn()) return;
    await sleep(100);
  }
  throw new Error('timeout waiting for: ' + label);
}
const clip = (value) => (String(value).length > 140 ? `${String(value).slice(0, 140)}...` : String(value));
const refuse = (route) =>
  route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ httpStatusCode: 500, message: 'refused by the suite' }) });

const APP_URL =
  `${BASE}/?modelRepositoryID=repo-1&llmProjectID=llm-proj&SCREndpoint=${BASE}/scr` +
  '&enableOptimization=true&computeContext=ctx&optimizeJobProgram=/Public/Jobs/Optimize-Prompt-DSPy&minOptimizeSamples=1' +
  '&enableRag=true&embeddingProjectID=emb-proj&ragComputeContext=rag-ctx';
const lpb = (suffix) => `#app-obj-LPB-${suffix}`;
const rgb = (suffix) => `#app-obj-RGB-${suffix}`;
const MANIFEST_BOX = lpb('pet-save-result');

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  let pageErrors = [];
  const newPage = async ({ clock = false } = {}) => {
    const page = await browser.newPage({ viewport: { width: 1500, height: 1100 } });
    page.on('pageerror', (e) => {
      pageErrors.push(e.message);
      console.log('[pageerror]', e.message);
    });
    page.on('dialog', (d) => d.accept().catch(() => {}));
    // Every toast that is shown, also one that is gone again.
    await page.addInitScript(() => {
      window.__toasts = [];
      new MutationObserver((records) => {
        for (const record of records) {
          for (const node of record.addedNodes) {
            if (node.nodeType === 1 && node.classList.contains('toast')) window.__toasts.push(node.textContent.trim());
          }
        }
      }).observe(document, { childList: true, subtree: true });
    });
    if (clock) await page.clock.install();
    return page;
  };
  const road = async (name, run, options) => {
    console.log(`---- ${name}`);
    pageErrors = [];
    const page = await newPage(options);
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
  const textOf = async (page, selector) => ((await page.textContent(selector)) || '').replace(/\s+/g, ' ').trim();
  const toasts = (page) => page.evaluate(() => window.__toasts.slice());
  const listed = async (page, select, value) => (await page.$$eval(`${select} option`, (options) => options.map((option) => option.value))).includes(value);
  const choose = async (page, prefix, select, value) => {
    await goStep(page, prefix, 'setup');
    await waitUntil(() => listed(page, select, value), `${value} is listed in ${select}`);
    await page.selectOption(select, value);
    await atRest();
  };
  const openPage = async (page, kind = 'llm') => {
    await page.goto(APP_URL);
    await page.waitForSelector('#LPB-project-dropdown', { timeout: 20000 });
    await atRest();
    if (kind === 'decision') {
      await page.click('#LPB-kind-decision');
      await atRest();
    }
    if (kind === 'rag') {
      await page.click('#LPB-kind-rag');
      await page.waitForSelector('#app-area-rag:not([hidden]) #app-obj-RGB-project', { timeout: 20000 });
      await atRest();
    }
  };
  const openPromptTest = async (page, project, model) => {
    await choose(page, 'LPB', '#LPB-project-dropdown', project);
    await choose(page, 'LPB', '#LPB-prompt-dropdown', model);
  };
  const openSetup = async (page, setup) => {
    await choose(page, 'RGB', rgb('project'), 'rag-proj');
    await choose(page, 'RGB', rgb('setup'), setup);
  };
  // As a person chooses an entry: the search field, then the entry of its menu.
  const pickAsAPerson = async (page, select, value) => {
    await page.click(`${select}-combobox`);
    await page.click(`${select}-combobox-list .dropdown-item[data-value="${value}"]`);
  };
  // As the search field does it: the value of the list set, and `change` sent.
  const pickByScript = (page, select, value) =>
    page.$eval(
      select,
      (list, chosen) => {
        list.value = chosen;
        list.dispatchEvent(new Event('change'));
      },
      value
    );
  const createInDialog = async (page, prefix, picker, modal, name, description = '') => {
    await goStep(page, prefix, 'setup');
    const before = await page.inputValue(picker);
    await page.click(`button[data-bs-target="#${modal}Modal"]`);
    await page.waitForSelector(`#${modal}Modal.show`);
    await page.waitForTimeout(400);
    const opened = { name: await page.inputValue(`#${modal}Name`), description: await page.inputValue(`#${modal}Description`) };
    await page.fill(`#${modal}Name`, name);
    await page.fill(`#${modal}Description`, description);
    await page.click(`#${modal}Modal .btn-primary`);
    await page.waitForSelector('.modal.show', { state: 'detached', timeout: 15000 });
    await waitUntil(async () => (await page.inputValue(picker)) !== before, `${name} is selected`);
    await atRest();
    return { id: await page.inputValue(picker), opened };
  };
  const confirmDialog = async (page) => {
    await page.waitForSelector('.modal.show .btn-danger', { timeout: 15000 });
    await page.waitForTimeout(450);
    await page.click('.modal.show .btn-danger');
  };

  // ============================================================================
  // The Prompt Builder
  // ============================================================================
  await post('/__decision');

  await road('a create dialog after a creation', async (page) => {
    await openPage(page);
    await choose(page, 'LPB', '#LPB-project-dropdown', 'proj-1');
    const first = await createInDialog(page, 'LPB', '#LPB-prompt-dropdown', 'promptBuilderCreatePrompt', 'Outcome with text', 'Scores the tone of a complaint');
    await resetLog();
    const second = await createInDialog(page, 'LPB', '#LPB-prompt-dropdown', 'promptBuilderCreatePrompt', 'Outcome name only');
    const created = (await getLog()).filter((entry) => entry.method === 'POST' && entry.url.startsWith('/modelRepository/models') && !entry.url.includes('/contents'));
    const sent = created.length ? JSON.parse(created[0].body).description : null;
    step(
      first.id !== second.id && second.opened.name === '' && second.opened.description === '' && sent === '',
      `Create prompt-test: after a creation the dialog opens empty, and the next prompt-test is created with the description typed for it (opened with: ${JSON.stringify(second.opened)}; description sent: ${JSON.stringify(sent)})`
    );
  });

  // The manifest of a prompt template whose writes are refused one at a time.
  const manifestWithRefusal = async (page, label, refuseIt) => {
    await openPromptTest(page, 'proj-1', 'model-used');
    await goStep(page, 'LPB', 'finalize');
    let refused = 0;
    let scoreCode = false;
    let puts = 0;
    await page.route('**/modelRepository/models/model-used**', (route) => {
      const request = route.request();
      const body = request.postData() || '';
      if (request.method() === 'POST' && body.includes('def scoreModel(')) scoreCode = true;
      if (request.method() === 'PUT' && scoreCode) puts += 1;
      if (refuseIt({ request, body, scoreCode, puts }) && refused === 0) {
        refused += 1;
        return refuse(route);
      }
      return route.fallback();
    });
    await resetLog();
    await page.click(lpb('pet-create-model-button'));
    await waitUntil(async () => refused === 1, `${label}: the write is refused`, 30000);
    await waitUntil(
      async () => !(await page.isDisabled(lpb('pet-create-model-button'))) && (await textOf(page, MANIFEST_BOX)) !== '',
      `${label}: the manifest has ended`,
      30000
    );
    await atRest();
    await page.unroute('**/modelRepository/models/model-used**');
    return {
      manifested: (await toasts(page)).filter((text) => text.includes('has been manifested')).length,
      lines: await page.$$eval(`${MANIFEST_BOX} .pet-manifest-failed`, (els) => els.map((e) => `${[...e.classList].join('.')}: ${e.textContent.trim().slice(0, 70)}`)),
    };
  };
  const uploadOf = (file) => ({ request, body }) => request.method() === 'POST' && body.includes(`filename="${file}"`);
  for (const [what, refuseIt, kind] of [
    ['inputVar.json', uploadOf('inputVar.json'), 'pet-manifest-stopped'],
    ['outputVar.json', uploadOf('outputVar.json'), 'pet-manifest-stopped'],
    ['the delete of a variable', ({ request }) => request.method() === 'DELETE' && request.url().includes('/variables/'), 'pet-manifest-stopped'],
    ['requirements.json', uploadOf('requirements.json'), 'pet-manifest-incomplete'],
    // The first PUT after the score code is the tag update, the second one the attributes.
    ['the update of the attributes', ({ request, puts }) => request.method() === 'PUT' && puts === 2, 'pet-manifest-incomplete'],
  ]) {
    await road(`a manifest of a prompt template, ${what} refused`, async (page) => {
      await post('/__manifested', { on: true });
      await openPage(page);
      // The fixture model has no variables; here it has one from an earlier
      // manifest, which the manifest deletes first.
      await page.route('**/modelRepository/models/model-used/variables?*', (route) =>
        route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [{ id: 'var-earlier', name: 'earlier' }] }) })
      );
      const outcome = await manifestWithRefusal(page, what, refuseIt);
      step(
        outcome.manifested === 0 && outcome.lines.length === 1 && outcome.lines[0].includes(kind),
        `Manifest, ${what} answered HTTP 500: no toast says "manifested", and the box says ${kind === 'pet-manifest-stopped' ? 'that nothing was manifested' : 'that the manifest was not completed'} (toasts "manifested": ${outcome.manifested}; box: ${outcome.lines.join(' | ') || 'nothing'})`
      );
    });
  }
  await post('/__manifested', { on: false });

  await road('a manifest of a prompt template without the LLM call, the delete of requirements.json refused', async (page) => {
    await openPage(page);
    const outcome = await manifestWithRefusal(page, 'the delete of requirements.json', ({ request }) => request.method() === 'DELETE' && request.url().includes('/contents/c-req'));
    step(
      outcome.manifested === 0 && outcome.lines.length === 1 && outcome.lines[0].includes('pet-manifest-incomplete'),
      `Manifest, the delete of the stale requirements.json answered HTTP 500: no toast says "manifested", the box says that the manifest was not completed (toasts: ${outcome.manifested}; box: ${outcome.lines.join(' | ') || 'nothing'})`
    );
  });

  await road('a manifest of a decision template, outputVar.json refused', async (page) => {
    await openPage(page, 'decision');
    await choose(page, 'LPB', '#LPB-project-dropdown', 'proj-2');
    await choose(page, 'LPB', '#LPB-prompt-dropdown', 'model-dec');
    await waitUntil(async () => (await page.$$('.pet-run-delete')).length >= 1, 'the saved run is loaded');
    await goStep(page, 'LPB', 'finalize');
    let refused = 0;
    await page.route('**/modelRepository/models/model-dec/contents?*', (route) => {
      if (route.request().method() === 'POST' && (route.request().postData() || '').includes('filename="outputVar.json"') && refused === 0) {
        refused += 1;
        return refuse(route);
      }
      return route.continue();
    });
    await page.click(lpb('pet-create-model-button'));
    await waitUntil(async () => refused === 1, 'the upload is refused', 30000);
    await waitUntil(async () => !(await page.isDisabled(lpb('pet-create-model-button'))) && (await textOf(page, MANIFEST_BOX)) !== '', 'the manifest has ended', 30000);
    await atRest();
    const manifested = (await toasts(page)).filter((text) => text.includes('has been manifested')).length;
    const stopped = (await page.$$(`${MANIFEST_BOX} .pet-manifest-failed.pet-manifest-stopped`)).length;
    step(manifested === 0 && stopped === 1, `Manifest of a decision template, outputVar.json answered HTTP 500: no toast says "manifested", the box says that nothing was manifested (toasts: ${manifested}; lines: ${stopped})`);
  });

  for (const [what, refuseIt] of [
    ['the new model version', (request) => request.method() === 'POST' && request.url().includes('/modelVersions')],
    ['the delete of the stored tracker', (request) => request.method() === 'DELETE' && request.url().includes('/contents/c-trk')],
  ]) {
    await road(`Save Experiments, ${what} refused`, async (page) => {
      await openPage(page);
      await openPromptTest(page, 'proj-1', 'model-used');
      await goStep(page, 'LPB', 'build');
      await page.route('**/modelRepository/models/model-used/**', (route) => (refuseIt(route.request()) ? refuse(route) : route.continue()));
      await page.click(lpb('pet-save-button'));
      await waitUntil(async () => (await textOf(page, lpb('pet-save-only-result'))) !== '', 'the save has ended', 20000);
      await atRest();
      const saved = (await toasts(page)).filter((text) => /saved/i.test(text)).length;
      const failed = (await page.$$(`${lpb('pet-save-only-result')} .pet-save-failed`)).length;
      step(saved === 0 && failed === 1, `Save Experiments, ${what} answered HTTP 500: no toast says "saved", the line beside the button says that the save did not finish (toasts: ${saved}; line: ${failed}; note: "${clip(await textOf(page, lpb('pet-save-only-result')))}")`);
    });
  }

  await road('the delete of an optimization run, refused', async (page) => {
    await openPage(page);
    await openPromptTest(page, 'proj-1', 'model-used');
    await goStep(page, 'LPB', 'optimize');
    const entries = `${lpb('pane-optimize')} .pb-optimize-history-entry`;
    await page.waitForSelector(entries, { timeout: 15000 });
    const before = await page.$$eval(entries, (e) => e.length);
    await page.click(`${entries} summary`);
    await page.route('**/modelRepository/models/model-used/**', (route) => {
      const request = route.request();
      return request.method() === 'DELETE' || (request.method() === 'POST' && request.url().includes('/modelVersions')) ? refuse(route) : route.continue();
    });
    await page.click(`${lpb('pane-optimize')} .pb-optimize-history-delete`);
    await confirmDialog(page);
    await waitUntil(async () => (await toasts(page)).length > 0, 'a toast', 15000);
    await atRest();
    const said = await toasts(page);
    const after = await page.$$eval(entries, (e) => e.length);
    step(
      said.some((text) => text.startsWith('The deletion failed')) && !said.some((text) => text.includes('Optimization run deleted')) && after === before && pageErrors.length === 0,
      `the delete of an optimization run whose model version and deletes are refused says that the deletion failed, and the history still lists the run (toasts: ${JSON.stringify(said)}; entries before ${before}, after ${after}; page errors: ${pageErrors.length})`
    );
  });

  await road('Delete prompt-test while another one is opened', async (page) => {
    await openPage(page);
    await choose(page, 'LPB', '#LPB-project-dropdown', 'proj-1');
    const victim = (await createInDialog(page, 'LPB', '#LPB-prompt-dropdown', 'promptBuilderCreatePrompt', 'Outcome victim')).id;
    await page.route('**/relationships/relationships**', async (route) => {
      await sleep(3000);
      await route.continue().catch(() => {});
    });
    await resetLog();
    await page.click('#LPB-delete-prompt-button');
    await page.waitForTimeout(300);
    await pickAsAPerson(page, '#LPB-prompt-dropdown', 'model-free');
    await page.waitForTimeout(4000);
    await atRest();
    const dialog = (await page.$$('.modal.show')).length;
    const deletes = (await getLog()).filter((entry) => entry.method === 'DELETE').length;
    step(
      dialog === 0 && deletes === 0 && (await page.inputValue('#LPB-prompt-dropdown')) === 'model-free',
      `Delete prompt-test: when another prompt-test was opened while the usage was asked, no dialog comes up, nothing is deleted and the other one stays open (dialog: ${dialog}; deletes: ${deletes}; open: ${await page.inputValue('#LPB-prompt-dropdown')}; victim ${victim})`
    );
  });

  await road('Delete project while another one is chosen', async (page) => {
    await openPage(page);
    const project = (await createInDialog(page, 'LPB', '#LPB-project-dropdown', 'promptBuilderCreateProject', 'Outcome project')).id;
    await page.route(`**/modelRepository/projects/${project}/models**`, async (route) => {
      await sleep(3000);
      await route.continue().catch(() => {});
    });
    await resetLog();
    await page.click('#LPB-delete-project-button');
    await page.waitForTimeout(300);
    await pickAsAPerson(page, '#LPB-project-dropdown', 'proj-1');
    await page.waitForTimeout(4000);
    await atRest();
    const dialog = (await page.$$('.modal.show')).length;
    const deletes = (await getLog()).filter((entry) => entry.method === 'DELETE').length;
    step(
      dialog === 0 && deletes === 0 && (await page.inputValue('#LPB-project-dropdown')) === 'proj-1',
      `Delete project: when another project was chosen while its prompt-tests were listed, no dialog comes up and nothing is deleted (dialog: ${dialog}; deletes: ${deletes}; open: ${await page.inputValue('#LPB-project-dropdown')})`
    );
  });

  for (const what of ['prompt-test', 'project']) {
    await road(`Delete ${what} whose request gets no answer`, async (page) => {
      await openPage(page);
      let id;
      if (what === 'project') {
        id = (await createInDialog(page, 'LPB', '#LPB-project-dropdown', 'promptBuilderCreateProject', 'Outcome cut project')).id;
      } else {
        await choose(page, 'LPB', '#LPB-project-dropdown', 'proj-1');
        id = (await createInDialog(page, 'LPB', '#LPB-prompt-dropdown', 'promptBuilderCreatePrompt', 'Outcome cut')).id;
      }
      await page.route(`**/modelRepository/${what === 'project' ? 'projects' : 'models'}/${id}`, (route) =>
        route.request().method() === 'DELETE' ? route.abort('failed') : route.continue()
      );
      await page.click(what === 'project' ? '#LPB-delete-project-button' : '#LPB-delete-prompt-button');
      await confirmDialog(page);
      await waitUntil(async () => (await toasts(page)).length > 0, 'a toast', 15000).catch(() => {});
      await atRest();
      await page.waitForTimeout(500);
      const said = await toasts(page);
      step(
        said.some((text) => text.startsWith('The deletion failed')) && pageErrors.length === 0,
        `Delete ${what}: a delete whose request gets no answer says that the deletion failed, and no error is left uncaught (toasts: ${JSON.stringify(said)}; page errors: ${JSON.stringify(pageErrors)})`
      );
    });
  }

  await road('a change of the prompt-test list sent by a script while Save Experiments is out', async (page) => {
    await openPage(page);
    await openPromptTest(page, 'proj-1', 'model-used');
    const stored = (await (await fetch(BASE + '/files/files/trk-1/content')).json()).length;
    await goStep(page, 'LPB', 'build');
    await page.route('**/modelRepository/models/model-used/**', async (route) => {
      if (route.request().method() !== 'GET') await sleep(2500);
      await route.continue().catch(() => {});
    });
    await resetLog();
    await page.click(lpb('pet-save-button'));
    await page.waitForTimeout(400);
    await pickByScript(page, '#LPB-prompt-dropdown', 'model-free');
    const during = await page.inputValue('#LPB-prompt-dropdown');
    await waitUntil(async () => (await textOf(page, lpb('pet-save-only-result'))) !== '', 'the save has ended', 30000);
    await atRest();
    const uploads = (await getLog()).filter((entry) => entry.method === 'POST' && entry.body.includes('filename="Prompt-Experiment-Tracker.json"'));
    const rows = uploads.map((entry) => {
      const m = entry.body.match(/\r\n\r\n([\s\S]*?)\r\n--/);
      return `${entry.url.split('?')[0]} rows=${m ? JSON.parse(m[1]).length : '?'}`;
    });
    step(
      during === 'model-used' && (await page.inputValue('#LPB-prompt-dropdown')) === 'model-used' && rows.length === 1 && rows[0] === `/modelRepository/models/model-used/contents rows=${stored}`,
      `Save Experiments: a change of the list sent by a script while the save is out is undone, and the tracker with its ${stored} runs goes to the prompt-test of the click (list right after the change: ${during}; uploads: ${JSON.stringify(rows)})`
    );
    await pickByScript(page, '#LPB-prompt-dropdown', 'model-free');
    await atRest();
    step((await page.inputValue('#LPB-prompt-dropdown')) === 'model-free', 'and once the save has ended, the same change opens the other prompt-test');
  });

  await road('a write that never gets an answer', async (page) => {
    await openPage(page);
    await openPromptTest(page, 'proj-1', 'model-used');
    await goStep(page, 'LPB', 'build');
    let held = 0;
    await page.route('**/modelRepository/models/model-used/**', (route) => {
      if (route.request().method() === 'GET') return route.continue();
      held += 1; // never answered
      return undefined;
    });
    await page.click(lpb('pet-save-button'));
    await waitUntil(async () => held > 0, 'the first write is out');
    await page.waitForTimeout(1000);
    const lockedBefore = await page.$eval('#LPB-selection', (block) => block.disabled);
    await page.clock.fastForward(121000);
    await waitUntil(async () => !(await page.$eval('#LPB-selection', (block) => block.disabled)), 'the selection is usable again', 15000).catch(() => {});
    const lockedAfter = await page.$eval('#LPB-selection', (block) => block.disabled);
    const failed = (await page.$$(`${lpb('pet-save-only-result')} .pet-save-failed`)).length;
    step(
      lockedBefore && !lockedAfter && failed === 1 && !(await page.isDisabled(lpb('pet-save-button'))),
      `Save Experiments: a write that gets no answer is given up after two minutes - the selection is usable again and the line says that the save did not finish (locked while out: ${lockedBefore}; locked after: ${lockedAfter}; line: ${failed})`
    );
  }, { clock: true });

  await road('the menu of a search field once it is closed', async (page) => {
    await openPage(page);
    await goStep(page, 'LPB', 'setup');
    await waitUntil(() => listed(page, '#LPB-project-dropdown', 'proj-1'), 'proj-1 is listed');
    await pickAsAPerson(page, '#LPB-project-dropdown', 'proj-1');
    await atRest();
    const entries = await page.$$eval('#LPB-project-dropdown-combobox-list .dropdown-item', (items) => items.length);
    const shown = await page.$eval('#LPB-project-dropdown-combobox-list', (menu) => menu.classList.contains('show'));
    await page.click('#LPB-project-dropdown-combobox');
    const reopened = await page.$$eval('#LPB-project-dropdown-combobox-list .dropdown-item', (items) => items.length);
    step(
      !shown && entries === 0 && reopened > 0,
      `a search field: once an entry is picked its menu is closed and holds no entries, and it is built again when it opens (entries when closed: ${entries}; when opened again: ${reopened})`
    );
  });

  // ============================================================================
  // The RAG Builder
  // ============================================================================
  const runState = (page) =>
    page.evaluate(() => {
      const card = document.getElementById('app-obj-RGB-ingestion-run');
      const launch = document.getElementById('app-obj-RGB-launch');
      return {
        shown: Boolean(card) && getComputedStyle(card).display !== 'none',
        text: card ? card.textContent.replace(/\s+/g, ' ').trim() : '',
        launchOff: Boolean(launch && launch.disabled),
      };
    });
  const manifestEnded = async (page) =>
    !(await page.isDisabled(rgb('manifest'))) && (await textOf(page, rgb('manifest'))) === 'Manifest setup' && (await textOf(page, rgb('save-result'))) !== '';
  const keepRunning = async (route) => {
    const id = route.request().url().split('/').pop();
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ id, state: 'running', results: { COMPUTE_SESSION: `rag-cs-${id} (rag-ctx)`, COMPUTE_JOB: `rag-cj-${id}` } }),
    });
  };

  await road('the line under the SAS Content folder after a manifest', async (page) => {
    await openPage(page, 'rag');
    await openSetup(page, 'rag-setup-1');
    await goStep(page, 'RGB', 'store');
    await page.click(rgb('manifest'));
    await waitUntil(() => manifestEnded(page), 'the manifest has ended', 60000);
    await atRest();
    const line = /Studio flow: RAG Ingest - Policy Handbook/;
    const own = await textOf(page, rgb('pane-store'));
    await choose(page, 'RGB', rgb('setup'), 'rag-setup-ss');
    const other = await textOf(page, rgb('pane-store'));
    await createInDialog(page, 'RGB', rgb('setup'), 'app-obj-RGB-create-setup', 'Outcome setup');
    const created = await textOf(page, rgb('pane-store'));
    step(
      line.test(own) && !/Studio flow: RAG Ingest/.test(other) && !/Studio flow: RAG Ingest/.test(created),
      `Store & output: the line that names the flow of a manifest stands on its setup only - not on another one opened next, nor on a new one (own: ${line.test(own)}; another: ${/Studio flow: RAG Ingest/.test(other)}; new: ${/Studio flow: RAG Ingest/.test(created)})`
    );
  });

  await road('a create dialog of the RAG Builder after a creation', async (page) => {
    await openPage(page, 'rag');
    await choose(page, 'RGB', rgb('project'), 'rag-proj');
    await createInDialog(page, 'RGB', rgb('setup'), 'app-obj-RGB-create-setup', 'Outcome described', 'Handbook of the works council');
    await resetLog();
    const second = await createInDialog(page, 'RGB', rgb('setup'), 'app-obj-RGB-create-setup', 'Outcome plain');
    const created = (await getLog()).filter((entry) => entry.method === 'POST' && entry.url.startsWith('/modelRepository/models') && !entry.url.includes('/contents'));
    const sent = created.length ? JSON.parse(created[0].body).description : null;
    step(
      second.opened.name === '' && second.opened.description === '' && sent === '',
      `Create setup: after a creation the dialog opens empty, and the next setup is created with the description typed for it (opened with: ${JSON.stringify(second.opened)}; description sent: ${JSON.stringify(sent)})`
    );
  });

  await road('the banner of a failed run on the setup opened next', async (page) => {
    await openPage(page, 'rag');
    await openSetup(page, 'rag-setup-1');
    await post('/__ragfail', { run: 'made to fail by the suite' });
    await goStep(page, 'RGB', 'run');
    await page.click(rgb('launch'));
    await waitUntil(async () => /did not succeed/.test(await textOf(page, rgb('status'))), 'the banner of the failed run', 30000);
    await atRest();
    await choose(page, 'RGB', rgb('setup'), 'rag-setup-ss');
    const banner = await textOf(page, rgb('status'));
    step(banner === '', `the banner of a run that failed goes with its setup: the setup opened next shows none (banner: "${clip(banner)}")`);
    await post('/__ragfail');
  });

  await road('the Manifest button while the model version is asked for', async (page) => {
    await openPage(page, 'rag');
    await openSetup(page, 'rag-setup-1');
    await goStep(page, 'RGB', 'store');
    let versions = 0;
    let release;
    const released = new Promise((resolve) => (release = resolve));
    await page.route('**/modelRepository/models/rag-setup-1/modelVersions', async (route) => {
      versions += 1;
      if (versions === 1) await released;
      await route.continue().catch(() => {});
    });
    await page.click(rgb('manifest'));
    await waitUntil(async () => versions === 1, 'the model version is asked for', 60000);
    const off = await page.isDisabled(rgb('manifest'));
    await page.click(rgb('manifest'), { timeout: 800 }).catch(() => {});
    await page.waitForTimeout(800);
    release();
    await waitUntil(() => manifestEnded(page), 'the manifest has ended', 60000);
    await atRest();
    step(off && versions === 1, `Manifest setup: the button is off while the model version is asked for, and a click then starts no second manifest (off: ${off}; model versions asked for: ${versions})`);
  });

  for (const [what, stage, refuseIt] of [
    ['the job reference', 'generating the ingestion job', (request, state) => state.jobWritten && /filename="rag-setup\.json"/.test(request.postData() || '')],
    ['the copy of the Studio flow', 'generating the Studio flow', (request) => /filename="[^"]+\.flw"/.test(request.postData() || '')],
  ]) {
    await road(`Manifest setup, ${what} refused`, async (page) => {
      await openPage(page, 'rag');
      await openSetup(page, 'rag-setup-1');
      await goStep(page, 'RGB', 'store');
      const state = { jobWritten: false, refused: 0 };
      await page.route('**/jobDefinitions/definitions**', (route) => {
        if (route.request().method() !== 'GET') state.jobWritten = true;
        return route.continue();
      });
      await page.route('**/modelRepository/models/rag-setup-1/contents**', (route) => {
        if (route.request().method() === 'POST' && state.refused === 0 && refuseIt(route.request(), state)) {
          state.refused += 1;
          return refuse(route);
        }
        return route.continue();
      });
      await page.click(rgb('manifest'));
      await waitUntil(() => manifestEnded(page), 'the manifest has ended', 60000);
      await atRest();
      const note = await textOf(page, rgb('save-result'));
      step(
        state.refused === 1 && note.includes(`The manifest stopped while ${stage}`) && !/Setup manifested/.test(note),
        `Manifest setup, ${what} answered HTTP 500: the manifest stops while ${stage} and does not say "manifested" (note: "${clip(note)}")`
      );
    });
  }

  await road('Manifest setup, the model version refused', async (page) => {
    await openPage(page, 'rag');
    await openSetup(page, 'rag-setup-1');
    await goStep(page, 'RGB', 'store');
    await page.route('**/modelRepository/models/rag-setup-1/modelVersions', (route) => refuse(route));
    await page.click(rgb('manifest'));
    await waitUntil(() => manifestEnded(page), 'the manifest has ended', 60000);
    await atRest();
    const note = await textOf(page, rgb('save-result'));
    step(
      /recording a new model version failed/.test(note) && !/a new model version recorded/.test(note),
      `Manifest setup, the model version answered HTTP 500: the note says that recording the version failed (note: "${clip(note)}")`
    );
  });

  await road('a change of the setup list sent by a script while Save setup is out', async (page) => {
    await openPage(page, 'rag');
    await openSetup(page, 'rag-setup-1');
    await goStep(page, 'RGB', 'store');
    await page.route('**/modelRepository/models/**', async (route) => {
      if (route.request().method() !== 'GET') await sleep(1500);
      await route.continue().catch(() => {});
    });
    await resetLog();
    await page.click(rgb('save'));
    await page.waitForTimeout(300);
    await pickByScript(page, rgb('setup'), 'rag-setup-ss');
    const during = await page.inputValue(rgb('setup'));
    await waitUntil(async () => !(await page.isDisabled(rgb('save'))) && !(await page.$eval(rgb('selection'), (block) => block.disabled)), 'the save has ended', 30000);
    await atRest();
    const writtenTo = [...new Set((await getLog()).filter((entry) => entry.method !== 'GET').map((entry) => (/^\/modelRepository\/models\/([^/?]+)/.exec(entry.url) || [])[1]).filter(Boolean))];
    step(
      during === 'rag-setup-1' && (await page.inputValue(rgb('setup'))) === 'rag-setup-1' && writtenTo.length === 1 && writtenTo[0] === 'rag-setup-1',
      `Save setup: a change of the setup list sent by a script while the save is out is undone, and every write goes to the setup of the click (list right after the change: ${during}; written to: ${writtenTo.join(', ')})`
    );
  });

  await road('a running ingestion on a page loaded after its launch', async (page) => {
    await openPage(page, 'rag');
    await openSetup(page, 'rag-setup-1');
    await goStep(page, 'RGB', 'run');
    await page.route('**/jobExecution/jobs/ragrun-*', keepRunning);
    await page.click(rgb('launch'));
    await waitUntil(async () => /running/.test((await runState(page)).text), 'the ingestion runs', 30000);
    await atRest();
    // The same page loaded again: it knows nothing of the launch.
    await page.goto('about:blank');
    await page.goto(APP_URL);
    await page.waitForSelector('#LPB-project-dropdown', { timeout: 20000 });
    await page.click('#LPB-kind-rag');
    await page.waitForSelector('#app-area-rag:not([hidden]) #app-obj-RGB-project', { timeout: 20000 });
    await atRest();
    await openSetup(page, 'rag-setup-1');
    await goStep(page, 'RGB', 'run');
    await waitUntil(async () => (await runState(page)).shown, 'the run is shown', 10000).catch(() => {});
    const reloaded = await runState(page);
    step(
      reloaded.shown && /running/.test(reloaded.text) && reloaded.launchOff,
      `after the page is loaded again, the setup whose ingestion still runs shows the run, and Launch is off (${JSON.stringify(reloaded).slice(0, 220)})`
    );
    await page.unroute('**/jobExecution/jobs/ragrun-*', keepRunning);
    await waitUntil(async () => /completed/.test((await runState(page)).text), 'the run has ended', 40000).catch(() => {});
    await atRest();
    const ended = await runState(page);
    step(/completed/.test(ended.text) && !ended.launchOff, `and when the job has ended there, the card says so and Launch can be used again (${JSON.stringify(ended).slice(0, 220)})`);
  });

  await road('an ingestion whose state cannot be read', async (page) => {
    await openPage(page, 'rag');
    await openSetup(page, 'rag-setup-1');
    await goStep(page, 'RGB', 'run');
    let polls = 0;
    await page.route('**/jobExecution/jobs/ragrun-*', (route) => {
      polls += 1;
      return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ httpStatusCode: 404, message: 'no such job' }) });
    });
    await page.click(rgb('launch'));
    await waitUntil(async () => polls >= 3, 'three polls', 30000);
    await page.waitForTimeout(800);
    const card = await runState(page);
    const pollsThen = polls;
    await page.waitForTimeout(6000);
    step(
      !card.launchOff && /no longer followed/.test(card.text) && polls === pollsThen,
      `an ingestion whose state cannot be read three times in a row is no longer followed: the card says so, Launch is offered again and no further poll is made (${JSON.stringify(card).slice(0, 260)}; polls: ${pollsThen} then ${polls})`
    );
    // The job of the mock is left running; the next road that opens this
    // setup would pick it up. Moved on so that it has ended.
    const jobs = (await (await fetch(BASE + '/__ragstate')).json()).jobs.filter((job) => job.kind === 'ingestion');
    const id = jobs[jobs.length - 1].id;
    await fetch(`${BASE}/jobExecution/jobs/${id}`);
    await fetch(`${BASE}/jobExecution/jobs/${id}`);
  });

  await browser.close();

  // The page made no request around its HTTP client.
  const direct = await (await fetch(BASE + '/__direct')).json();
  step(direct.length === 0, `no request of the browser to a SAS Viya service went around viyaFetch (${JSON.stringify(direct).slice(0, 200)})`);

  console.log('\n===== SUMMARY =====');
  results.forEach((line) => console.log(line));
  const failed = results.filter((line) => line.startsWith('FAIL')).length;
  console.log(`\n${results.length - failed} checks passed, ${failed} failed`);
})().catch((error) => {
  console.log('VERIFY FAILED', error);
  process.exit(1);
});
