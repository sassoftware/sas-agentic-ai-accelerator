/**
 * Regression harness for the Options contract of the Prompt Builder: the one
 * flat options group it posts to SAS Visual Analytics, and the three switches
 * in it (enableDecision, enableOptimization, enableRag) that decide which
 * parts of the page exist and which requests are sent. A fourth checkbox,
 * sslVerify, is the last shared option: it decides nothing on the page and
 * no request, only what a manifest writes (the three manifest suites check
 * that); here it is checked as an option - posted, mirrored, read strictly.
 *
 * WHAT THIS PROVES, AND WHAT IT DOES NOT
 *   The suite plays Visual Analytics itself: a stand-in host page embeds the
 *   built app in an iframe, posts the data message carrying a `resultName`,
 *   collects the `{ resultName, optionsConfig }` envelopes that come back and
 *   reloads the iframe with the stored option values mirrored into its URL,
 *   as `urlOption: true` asks for. That proves the APP's side of the
 *   contract only. The stand-in behaves the way Visual Analytics was measured
 *   to behave on 2026-10-10, with a probe page as the data-driven content
 *   object of a real report:
 *     - A 'Boolean' option is a checkbox. It is mirrored into the iframe URL
 *       as the word true or false, also when it is unticked, and it shows
 *       the right state after every reload of the object.
 *     - A report that stored the word "true" for an option while that option
 *       was a dropdown shows it ticked once the option is a checkbox.
 *     - A 'String' dropdown whose dataProvider keys are the words 'true' and
 *       'false' is NOT restored: after every reload the Options pane shows
 *       its first entry, whatever is stored, while the URL still carries the
 *       stored value. Keys like on/off, 0/1 or yes/no are restored. The page
 *       cannot notice this (its URL is right), so the suite refuses such a
 *       dropdown wherever it reads an options group.
 *     - The message carrying the `resultName` is posted on Visual Analytics'
 *       own load of the object only, which is why the stand-in posts it at
 *       the load event and the app must answer that one.
 *   What no stand-in shows is the Options pane itself. After a change to the
 *   group a look at a real Visual Analytics report (each switch set both
 *   ways, the report saved and reopened) is still the only check of what the
 *   pane displays; a green run of this file does not replace it.
 *
 * HOW TO RUN
 *   1. Build the app and start a mock of its own for this suite (the request
 *      log of the mock is what the "no request" checks read, so no other
 *      suite may talk to the same instance):
 *        MOCK_PORT=4175 node mock-server.cjs <dist>/index.html
 *   2. Copy this file next to the mock as verify-options.cjs (the package is
 *      an ES module, the suites are CommonJS) and run it where `playwright`
 *      resolves:
 *        VERIFY_BASE=http://localhost:4175 node verify-options.cjs
 *   Every line is `OK  ...` or `FAIL  ...`; the summary repeats them and the
 *   exit code is 1 as soon as one check failed. A `NOTE  ...` line is not a
 *   check: it says that the rest of a scenario was skipped because it depends
 *   on a check that failed just above. VERIFY_VERBOSE=1 also prints the
 *   browser console errors. The run takes about a minute and needs no Python.
 *
 * HOW IT IS BUILT
 *   - The host page (/__vahost) and, for the nested case, a page around it
 *     (/__vatop) are served by Playwright routes on the mock's origin, so the
 *     mock itself needs no route for them and never sees them.
 *   - Every scenario gets a fresh browser page and stands alone: a failed
 *     check is recorded and the scenario goes on; only a check the rest of a
 *     scenario depends on stops that scenario, never the run.
 *   - A URL is always built from a set of STORED values, the way a report
 *     holds them: every option the report knows is mirrored, also the ones
 *     whose switch is off, labels the app no longer posts, and the Job
 *     Execution parameters _program and _action. A checkbox is mirrored as
 *     the word true or false (see `mirrored`).
 *   - "No request" is read from the mock's GET /__log after the page went
 *     quiet. The suite sets no mock fixture and writes nothing, so the mock
 *     needs no RAG routes for it: a request the mock does not know is logged
 *     all the same, and the RAG Builder copes with the 404.
 *   - A failing RAG load is injected, because no failed request makes the RAG
 *     Builder give up: while the fault is armed, giving an element the id
 *     `app-obj-RGB-stepper` (the first thing the RAG Builder does) throws.
 *
 * WHAT IT RELIES ON (ids, parameter names, mock fixtures)
 *   - Element ids: `app-obj-LPB-stepper` / `app-obj-RGB-stepper` with steps
 *     `-step-<key>` and panes `-pane-<key>`; the kind cards `LPB-kind-<kind>`
 *     and `RGB-kind-<kind>` inside `LPB-kind-options` / `RGB-kind-options`;
 *     `LPB-project-dropdown`, `LPB-prompt-dropdown`; the decision parts
 *     `decision-model0`, `app-obj-LPB-decision-state`,
 *     `app-obj-LPB-pet-decout-answers`; toasts in `pb-toast-container`.
 *   - Step keys: setup, build, optimize, evaluate, finalize for the prompt
 *     flow and setup, documents, store, run for the RAG flow.
 *   - Option names: the nine shared ones, the three switches, the three
 *     optimization settings and the RAG settings listed below, with
 *     `ragComputeContext` for the RAG compute context.
 *   - Mock fixtures: repository `repo-1`, LLM project `llm-proj`, prompt
 *     project `proj-1`, the decision template `model-dec` (its registration
 *     says `Decision-Template`), the decision model project `dec-proj` with
 *     `dec-1` / `dec-2`, the credential domains `agentic-ai-keys` and
 *     `all-keys`.
 */
const { chromium } = require('playwright');
const fetch = require('./mock-fetch.cjs');
const { trackPagesOf, quiet } = require('./page-waits.cjs');

const BASE = (process.env.VERIFY_BASE || 'http://localhost:4173').replace(/\/+$/, '');
const VERBOSE = process.env.VERIFY_VERBOSE === '1';
const results = [];

function step(ok, label) {
  results.push(`${ok ? 'OK ' : 'FAIL'}  ${label}`);
  console.log(`${ok ? 'OK ' : 'FAIL'}  ${label}`);
  if (!ok) process.exitCode = 1;
  return ok;
}
// Not a check: says why checks are missing from the count.
function note(text) {
  results.push(`NOTE  ${text}`);
  console.log(`NOTE  ${text}`);
}
const getLog = async () => (await fetch(BASE + '/__log')).json();
const resetLog = async () => fetch(BASE + '/__reset', { method: 'POST' });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const firstLine = (message) => String(message || '').trim().split('\n')[0];
const sameList = (left, right) => left.length === right.length && left.every((value, index) => value === right[index]);
const onOff = (flag) => (flag ? 'on' : 'off');

/** Poll until `probe` holds; false when it never did. Never throws, so a check can report it. */
async function eventually(probe, timeout = 8000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    try {
      if (await probe()) return true;
    } catch {
      /* the frame is between two documents - ask again */
    }
    await sleep(100);
  }
  return false;
}

// ---------------------------------------------------------------------------
// The contract, as this suite expects it
// ---------------------------------------------------------------------------

/** Options every kind shares; they come first in the group. */
const SHARED_OPTIONS = [
  'viyaHost',
  'modelRepositoryID',
  'llmProjectID',
  'SCREndpoint',
  'deploymentType',
  'judgeModel',
  'modelCardReportURI',
  'credentialDomain',
  'sslVerify',
];
/**
 * The checkbox for TLS verification in manifested models: its label, and how
 * its URL value has to be read - only a clear "off" unticks it, everything
 * else (absent, blank, a typo) leaves verification on.
 */
const SSL_VERIFY = { name: 'sslVerify', label: 'Verify TLS certificates in manifested models', fallback: true };
const OPTIMIZATION_OPTIONS = ['computeContext', 'optimizeJobProgram', 'minOptimizeSamples'];
const RAG_OPTIONS = [
  'embeddingProjectID',
  'contentRoot',
  'casServer',
  'ragComputeContext',
  'ragServerFolder',
  'ragServerBrowseAbove',
  'ragContentFolder',
  'ragContentBrowseAbove',
  'storeSslmode',
  'deletedPolicy',
  'retainDays',
  'recordHistory',
  'embedReplicas',
  'persistElements',
  'persistChunks',
];
/** One switch per vector store the RAG runtime supports; more may be added without touching this file. */
const KNOWN_BACKEND_SWITCHES = ['enable_pgvector', 'enable_singlestore'];
const BACKEND_SWITCH_PATTERN = /^enable_[a-z0-9]+$/;

/**
 * The three switches: what an unreadable value falls back to, which options
 * only exist while the switch is on, and how the labels of those start.
 */
const SWITCHES = {
  enableDecision: { fallback: true, dependents: [], labelPrefix: '' },
  enableOptimization: { fallback: false, dependents: OPTIMIZATION_OPTIONS, labelPrefix: 'Optimization: ' },
  enableRag: {
    fallback: false,
    dependents: [...RAG_OPTIONS, ...KNOWN_BACKEND_SWITCHES],
    furtherDependents: BACKEND_SWITCH_PATTERN,
    labelPrefix: 'RAG: ',
  },
};
const SWITCH_NAMES = Object.keys(SWITCHES);

/** How a switch value in the URL has to be read: strict in both directions. */
function expectedFlag(raw, fallback) {
  if (raw === undefined || raw === null) return fallback;
  const word = String(raw).trim().toLowerCase();
  if (['true', '1', 'yes', 'on'].includes(word)) return true;
  if (['false', '0', 'no', 'off'].includes(word)) return false;
  return fallback;
}
const flagsOf = (stored) => ({
  decision: expectedFlag(stored.enableDecision, SWITCHES.enableDecision.fallback),
  optimization: expectedFlag(stored.enableOptimization, SWITCHES.enableOptimization.fallback),
  rag: expectedFlag(stored.enableRag, SWITCHES.enableRag.fallback),
});
const describeFlags = (flags) => `decision ${onOff(flags.decision)}, optimization ${onOff(flags.optimization)}, RAG ${onOff(flags.rag)}`;

// ---------------------------------------------------------------------------
// Stored option values (what a report holds and Visual Analytics mirrors)
// ---------------------------------------------------------------------------

// Visual Analytics opens the object through SAS Job Execution, so these two
// are in every URL the app ever sees there.
const JOB_EXECUTION_PARAMETERS = {
  _program: '/SAS Agentic AI Accelerator/Prompt Builder/SAS-LLM-Prompt-Builder-UI',
  _action: 'form',
};
/** What the prompt side needs before it calls SAS Viya. */
const promptSide = () => ({ modelRepositoryID: 'repo-1', llmProjectID: 'llm-proj', SCREndpoint: `${BASE}/scr` });
const OPTIMIZATION_SETTINGS = {
  computeContext: 'Optimization context ZZ',
  optimizeJobProgram: '/Public/Jobs/Optimize-Prompt-DSPy',
  minOptimizeSamples: '7',
};
// Values no default has, so a request made for RAG is recognisable by them
// and an echo in the options group cannot be a default.
const RAG_SETTINGS = {
  embeddingProjectID: 'emb-proj',
  contentRoot: '/ZZ Verify Options/RAG',
  casServer: 'cas-zz-verify',
  ragComputeContext: 'RAG context ZZ',
  ragServerFolder: '/zz verify/documents',
  ragServerBrowseAbove: 'true',
  ragContentFolder: '/ZZ Verify Options/Documents',
  ragContentBrowseAbove: 'false',
  storeSslmode: 'require',
  deletedPolicy: 'purge',
  retainDays: '9',
  recordHistory: 'false',
  embedReplicas: '3',
  persistElements: '0',
  persistChunks: '0',
  enable_pgvector: 'true',
  enable_singlestore: 'false',
};
/**
 * A report that was saved once with every switch on: it holds a value for
 * every option, and keeps holding the settings of a switch that is off now.
 */
function storedReport(switches, overrides = {}) {
  return {
    viyaHost: '',
    ...promptSide(),
    deploymentType: 'k8s',
    judgeModel: '',
    modelCardReportURI: '',
    credentialDomain: 'agentic-ai-keys',
    sslVerify: 'true',
    ...switches,
    ...OPTIMIZATION_SETTINGS,
    ...RAG_SETTINGS,
    ...overrides,
  };
}
/** The three checkboxes as the Options pane holds them: ticked or not. */
const switchValues = (flags) => ({
  enableDecision: flags.decision,
  enableOptimization: flags.optimization,
  enableRag: flags.rag,
});
/** What the Options pane holds after a group was posted: every field by its name, a checkbox as a boolean. */
const paneValues = (fields) => Object.fromEntries(fields.map((field) => [field.name, field.value]));

/**
 * One option value as Visual Analytics writes it into the iframe URL. A
 * checkbox ('Boolean' option) becomes the word true or false - an unticked
 * one is not left out and not blank. Everything else is the stored text.
 */
const mirrored = (value) => (typeof value === 'boolean' ? (value ? 'true' : 'false') : String(value));

/** The iframe URL for a set of stored values; an undefined value is a label the report does not have. */
function appUrl(stored) {
  const query = new URLSearchParams();
  Object.entries({ ...JOB_EXECUTION_PARAMETERS, ...stored }).forEach(([name, value]) => {
    if (value !== undefined && value !== null) query.append(name, mirrored(value));
  });
  return `${BASE}/?${query.toString()}`;
}

let resultNameCounter = 0;
const nextResultName = () => `dd${(resultNameCounter += 1)}`;
/** The data message Visual Analytics sends to a Data-Driven Content object. */
const dataMessage = (resultName) => ({
  version: '1',
  resultName,
  rowCount: 1,
  availableRowCount: 1,
  data: [['x']],
  columns: [{ name: 'bi1', label: 'Component', type: 'string' }],
});

// ---------------------------------------------------------------------------
// The pages that stand in for Visual Analytics
// ---------------------------------------------------------------------------

const HOST_PATH = '/__vahost';
const TOP_PATH = '/__vatop';

// The report page: one Data-Driven Content object. It records everything the
// object posts and can post to it - at the load event of the iframe, which is
// the earliest moment a host can, and at any time after.
const HOST_HTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Visual Analytics stand-in</title>
<style>html, body { margin: 0; height: 100%; } iframe { border: 0; width: 100%; height: 100%; }</style>
</head>
<body>
<iframe id="va-ddc" name="va-ddc" title="Data-driven content"></iframe>
<script>
(function () {
  var frame = document.getElementById('va-ddc');
  var received = [];
  window.addEventListener('message', function (event) {
    received.push({ fromApp: event.source === frame.contentWindow, kind: typeof event.data, data: event.data });
  });
  window.vaHost = {
    load: function (url, messageAtLoad) {
      return new Promise(function (resolve) {
        frame.addEventListener('load', function () {
          if (messageAtLoad !== undefined && messageAtLoad !== null) frame.contentWindow.postMessage(messageAtLoad, '*');
          resolve();
        }, { once: true });
        frame.src = url;
      });
    },
    send: function (message) { frame.contentWindow.postMessage(message, '*'); },
    received: function () { return received.slice(); },
    clear: function () { received.length = 0; }
  };
})();
</script>
</body>
</html>`;

// A page around the report page, as when the report itself is embedded: the
// object then has a parent and a different top window.
const TOP_HTML = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Page embedding the report</title>
<style>html, body { margin: 0; height: 100%; } iframe { border: 0; width: 100%; height: 100%; }</style>
</head>
<body>
<iframe id="va-report" name="va-report" title="Report" src="${HOST_PATH}"></iframe>
<script>
(function () {
  var received = [];
  window.addEventListener('message', function (event) {
    received.push({ kind: typeof event.data, data: event.data });
  });
  window.vaTop = {
    received: function () { return received.slice(); },
    clear: function () { received.length = 0; }
  };
})();
</script>
</body>
</html>`;

const RAG_STEPPER_ID = 'app-obj-RGB-stepper';
const FAIL_RAG_PARAMETER = 'verifyFailRagBuild';

/**
 * Runs in every document before its own scripts. While the fault is armed -
 * by `window.__verifyFailRagBuild = true`, or from the first script on by the
 * URL parameter - naming an element `app-obj-RGB-stepper` throws. That is the
 * first thing the RAG Builder does, so its build fails whatever the network
 * answers; the prompt side never uses that id.
 */
function installRagBuildFault({ stepperId, parameter }) {
  const armedByUrl = new RegExp(`[?&]${parameter}=1(&|$)`).test(window.location.search);
  const guard = (value) => {
    if ((armedByUrl || window.__verifyFailRagBuild === true) && String(value) === stepperId) {
      window.__verifyRagBuildFailures = (window.__verifyRagBuildFailures || 0) + 1;
      throw new Error('verify-options: injected failure while building the RAG area');
    }
  };
  const idProperty = Object.getOwnPropertyDescriptor(Element.prototype, 'id');
  Object.defineProperty(Element.prototype, 'id', {
    configurable: true,
    enumerable: idProperty.enumerable,
    get() {
      return idProperty.get.call(this);
    },
    set(value) {
      guard(value);
      idProperty.set.call(this, value);
    },
  });
  const setAttribute = Element.prototype.setAttribute;
  Element.prototype.setAttribute = function (name, value) {
    if (String(name).toLowerCase() === 'id') guard(value);
    return setAttribute.call(this, name, value);
  };
}

// ---------------------------------------------------------------------------
// Reading the request log of the mock
// ---------------------------------------------------------------------------

const pathOf = (entry) => new URL(entry.url, BASE).pathname;
function decoded(entry) {
  try {
    return decodeURIComponent(entry.url.replace(/\+/g, ' '));
  } catch {
    return entry.url;
  }
}
/** A request the app made to SAS Viya: not the page itself and not a control route of the mock. */
function isAppRequest(entry) {
  const path = pathOf(entry);
  return path !== '/' && path !== '/index.html' && path !== '/favicon.ico' && !path.startsWith('/__');
}
const appRequests = async () => (await getLog()).filter(isAppRequest);
const signature = (entry) => `${entry.method} ${decoded(entry)}`;
const signatures = (entries) => entries.map(signature).sort();
const listed = (entries) => (entries.length ? entries.map(signature).join(' ; ') : 'none');

/** What only the decision side asks for: its model project, its models, a decision call. */
function decisionRequests(entries) {
  return entries.filter((entry) => {
    const path = pathOf(entry);
    if (decoded(entry).includes('Decision Model Project')) return true;
    if (path.startsWith('/modelRepository/projects/dec-proj/')) return true;
    if (/^\/modelRepository\/models\/dec-\d/.test(path)) return true;
    if (/^\/files\/files\/dopt-/.test(path)) return true;
    if (entry.method === 'POST' && /^\/scr\//.test(path)) {
      return /^\/scr\/(jev|von)_mock\//.test(path) || /"name"\s*:\s*"(state|questions)"/.test(entry.body || '');
    }
    return false;
  });
}

/**
 * What only the RAG side asks for: the embedding project, the RAG projects,
 * the prompts it can enrich with, the caslibs of its CAS server, anything
 * under its content root, data flows and job definitions. The suite never
 * opens a CAS table picker of the prompt side, so a caslib listing is RAG's.
 */
function ragRequests(entries, stored = {}) {
  const embeddingProject = stored.embeddingProjectID || RAG_SETTINGS.embeddingProjectID;
  const contentRoots = ['/SAS Agentic AI Accelerator/RAG', RAG_SETTINGS.contentRoot, stored.contentRoot].filter(Boolean);
  return entries.filter((entry) => {
    const path = pathOf(entry);
    const url = decoded(entry);
    if (url.includes('RAG-Engineering') || url.includes('Output-Parsing')) return true;
    if (path.startsWith(`/modelRepository/projects/${embeddingProject}/`)) return true;
    if (/^\/casManagement\/servers\/[^/]+\/caslibs$/.test(path)) return true;
    if (contentRoots.some((root) => url.includes(root))) return true;
    return /^\/(dataFlows|jobDefinitions)\//.test(path);
  });
}
const credentialRequests = (entries) => entries.filter((entry) => pathOf(entry).startsWith('/credentials/'));
/** What only the prompt side asks for (a blank LLM project id shows as `projects//models`). */
function promptSideRequests(entries) {
  return entries.filter((entry) => {
    const path = pathOf(entry);
    const url = decoded(entry);
    if (url.includes('Prompt-Engineering') || url.includes('Decision-Engineering')) return true;
    if (/^\/modelRepository\/projects\/(llm-proj)?\/models/.test(path)) return true;
    if (/^\/modelRepository\/models\/llm-\d/.test(path)) return true;
    return decisionRequests([entry]).length > 0;
  });
}
const optimizationRequests = (entries) =>
  entries.filter((entry) => /^\/(jobExecution|jobDefinitions|folders)\//.test(pathOf(entry)));


// ---------------------------------------------------------------------------
// Reading the options group
// ---------------------------------------------------------------------------

const fieldsOf = (optionsConfig) => (optionsConfig && Array.isArray(optionsConfig.fields) ? optionsConfig.fields : null);
const fieldNamed = (fields, name) => (fields || []).find((field) => field && field.name === name);

/** One group, flat: the top-level object is the group and nothing is nested in it. */
function flatGroupProblems(optionsConfig) {
  if (!optionsConfig || typeof optionsConfig !== 'object') return ['there is no optionsConfig object'];
  const problems = [];
  if (optionsConfig.version !== 1) problems.push(`version is ${JSON.stringify(optionsConfig.version)}, not 1`);
  if (optionsConfig.urlOption !== true) problems.push('urlOption is not true');
  if (typeof optionsConfig.name !== 'string' || !optionsConfig.name) problems.push('the group has no name');
  if (typeof optionsConfig.label !== 'string' || !optionsConfig.label) problems.push('the group has no label');
  if (!Array.isArray(optionsConfig.groups) || optionsConfig.groups.length > 0) problems.push('groups is not an empty array');
  if (!Array.isArray(optionsConfig.fields) || optionsConfig.fields.length === 0) problems.push('fields is not a filled array');
  return problems;
}
function duplicateNames(fields) {
  const names = fields.map((field) => field.name);
  return [...new Set(names.filter((name, index) => names.indexOf(name) !== index))];
}
/** Visual Analytics knows 'String' and 'Boolean', spelled exactly so. */
const wrongTypes = (fields) =>
  fields.filter((field) => field.type !== 'String' && field.type !== 'Boolean').map((field) => `${field.name} (${JSON.stringify(field.type)})`);
/** A field Visual Analytics can render and mirror: named, labelled, with a value of its type. */
function fieldProblems(fields) {
  const problems = [];
  fields.forEach((field) => {
    const name = field && typeof field.name === 'string' && field.name ? field.name : '';
    if (!name) {
      problems.push(`a field has no name (${JSON.stringify(field).slice(0, 60)})`);
      return;
    }
    if (typeof field.label !== 'string' || !field.label) problems.push(`${name} has no label`);
    if (field.type === 'Boolean' && typeof field.value !== 'boolean') problems.push(`${name} is Boolean but its value is ${JSON.stringify(field.value)}`);
    if (field.type === 'String' && typeof field.value !== 'string') problems.push(`${name} is String but its value is ${JSON.stringify(field.value)}`);
    if (field.dataProvider !== undefined) {
      const entries = Array.isArray(field.dataProvider) ? field.dataProvider : [];
      const keys = entries.map((entry) => entry && entry.key);
      if (entries.length === 0) problems.push(`${name} has an empty dataProvider`);
      if (keys.some((key) => typeof key !== 'string')) problems.push(`${name} has a dataProvider key that is not a string`);
      if (new Set(keys).size !== keys.length) problems.push(`${name} has a dataProvider key twice`);
      if (entries.some((entry) => !entry || typeof entry.text !== 'string' || !entry.text)) problems.push(`${name} has a dataProvider entry without text`);
      if (field.type === 'String' && !keys.includes(field.value)) problems.push(`${name} has the value ${JSON.stringify(field.value)}, which is none of its keys`);
    }
  });
  return problems;
}
/**
 * Dropdowns Visual Analytics does not restore: a 'String' option whose
 * dataProvider has the word 'true' or 'false' as a key. After a reload the
 * Options pane shows the first entry of such a dropdown whatever the report
 * stores (measured, see the header), so an on/off option has to be a
 * 'Boolean' option, or a dropdown with other keys such as '1' / '0'.
 */
const wordKeyDropdowns = (fields) =>
  fields
    .filter(
      (field) =>
        field &&
        field.type === 'String' &&
        Array.isArray(field.dataProvider) &&
        field.dataProvider.some((entry) => entry && ['true', 'false'].includes(String(entry.key).trim().toLowerCase()))
    )
    .map((field) => field.name);
const groupProblems = (optionsConfig) => {
  const fields = fieldsOf(optionsConfig) || [];
  const duplicates = duplicateNames(fields);
  const types = wrongTypes(fields);
  const wordKeys = wordKeyDropdowns(fields);
  return [
    ...flatGroupProblems(optionsConfig),
    ...(duplicates.length ? [`names used twice: ${duplicates.join(', ')}`] : []),
    ...(types.length ? [`types other than String and Boolean: ${types.join(', ')}`] : []),
    ...(wordKeys.length ? [`dropdowns with the key 'true' or 'false', which Visual Analytics does not restore: ${wordKeys.join(', ')}`] : []),
    ...fieldProblems(fields),
  ];
};

/** The shared options, and for each switch the fields between it and the next switch. */
function optionLayout(fields) {
  const names = fields.map((field) => field.name);
  const order = SWITCH_NAMES.filter((name) => names.includes(name)).sort((left, right) => names.indexOf(left) - names.indexOf(right));
  const firstSwitch = order.length ? names.indexOf(order[0]) : names.length;
  const blocks = {};
  order.forEach((switchName, position) => {
    const start = names.indexOf(switchName) + 1;
    const end = position + 1 < order.length ? names.indexOf(order[position + 1]) : names.length;
    blocks[switchName] = fields.slice(start, end);
  });
  return { shared: fields.slice(0, firstSwitch), blocks };
}
function sharedProblems(fields) {
  const shared = optionLayout(fields).shared.map((field) => field.name);
  const missing = SHARED_OPTIONS.filter((name) => !shared.includes(name));
  const foreign = shared.filter((name) => !SHARED_OPTIONS.includes(name));
  return [
    ...(missing.length ? [`not ahead of the switches: ${missing.join(', ')}`] : []),
    ...(foreign.length ? [`ahead of the switches but not a shared option: ${foreign.join(', ')}`] : []),
  ];
}
/** A switch is a checkbox showing what the URL says: a 'Boolean' option with a boolean value and no dataProvider. */
function switchProblems(fields, switchName, expectedOn) {
  const field = fieldNamed(fields, switchName);
  if (!field) return [`${switchName} is not in the group`];
  const problems = [];
  if (field.type !== 'Boolean') problems.push(`its type is ${JSON.stringify(field.type)}, not 'Boolean'`);
  if ('dataProvider' in field) problems.push(`it has a dataProvider (${JSON.stringify(field.dataProvider).slice(0, 80)}), which a checkbox does not`);
  if (typeof field.value !== 'boolean') problems.push(`its value is ${JSON.stringify(field.value)}, not a boolean`);
  else if (field.value !== expectedOn) problems.push(`its value is ${field.value}, not ${expectedOn}`);
  return problems;
}
const tickedOrNot = (on) => (on ? 'ticked' : 'unticked');
/** The settings of a switch: all of them directly after it while it is on, none of them anywhere while it is off. */
function dependentProblems(fields, switchName, on) {
  const { blocks } = optionLayout(fields);
  if (!(switchName in blocks)) return [`${switchName} is not in the group`];
  const specification = SWITCHES[switchName];
  const isDependent = (name) =>
    specification.dependents.includes(name) || Boolean(specification.furtherDependents && specification.furtherDependents.test(name));
  const block = blocks[switchName];
  const blockNames = block.map((field) => field.name);
  const problems = [];
  if (!on) {
    const offered = fields.map((field) => field.name).filter(isDependent);
    if (offered.length) problems.push(`offered although the switch is off: ${offered.join(', ')}`);
    const following = blockNames.filter((name) => !isDependent(name));
    if (following.length) problems.push(`listed after the switched-off switch: ${following.join(', ')}`);
    return problems;
  }
  const missing = specification.dependents.filter((name) => !blockNames.includes(name));
  if (missing.length) problems.push(`not directly after the switch: ${missing.join(', ')}`);
  const foreign = blockNames.filter((name) => !isDependent(name));
  if (foreign.length) problems.push(`listed after the switch but not one of its settings: ${foreign.join(', ')}`);
  const unprefixed = block
    .filter((field) => isDependent(field.name) && !String(field.label).startsWith(specification.labelPrefix))
    .map((field) => `${field.name} ("${field.label}")`);
  if (unprefixed.length) problems.push(`label does not start with "${specification.labelPrefix}": ${unprefixed.join(', ')}`);
  return problems;
}
/** Every posted value says what the URL says (a blank or missing URL value leaves the default, which is not compared). */
function mirrorProblems(fields, stored) {
  const problems = [];
  fields.forEach((field) => {
    const raw = stored[field.name];
    if (raw === undefined || raw === null || String(raw) === '') return;
    let expected = mirrored(raw);
    // A checkbox reads the word; a switch that cannot read it shows its default.
    if (SWITCH_NAMES.includes(field.name)) expected = expectedFlag(raw, SWITCHES[field.name].fallback);
    else if (field.name === SSL_VERIFY.name) expected = expectedFlag(raw, SSL_VERIFY.fallback);
    else if (field.type === 'Boolean') expected = expectedFlag(raw, null);
    if (expected !== null && field.value !== expected) {
      problems.push(`${field.name}: the URL says ${JSON.stringify(String(raw))}, the group says ${JSON.stringify(field.value)}`);
    }
  });
  return problems;
}

// ---------------------------------------------------------------------------
// Reading the page
// ---------------------------------------------------------------------------

/** Everything the checks look at, read in one go so the parts belong to the same moment. */
const snapshot = (app) =>
  app.evaluate(() => {
    const isVisible = (element) =>
      Boolean(element) && element.getClientRects().length > 0 && getComputedStyle(element).visibility !== 'hidden';
    const textOf = (element) => (element ? (element.innerText || element.textContent || '').replace(/\s+/g, ' ').trim() : '');
    const present = (selector) => Boolean(document.querySelector(selector));

    const flow = (prefix) => {
      const stepper = document.getElementById(`app-obj-${prefix}-stepper`);
      if (!stepper) return null;
      const currentPane = [...stepper.querySelectorAll('.pb-step-pane')].find((pane) => !pane.hidden);
      return {
        visible: isVisible(stepper),
        hiddenByAttribute: Boolean(stepper.closest('[hidden]')),
        steps: [...stepper.querySelectorAll('.pb-step')].map((item) => ({
          key: ((item.querySelector('.pb-step-button') || {}).id || '').replace(`app-obj-${prefix}-step-`, ''),
          hidden: item.hidden,
        })),
        currentPane: currentPane ? currentPane.id.replace(`app-obj-${prefix}-pane-`, '') : '',
        mark: stepper.dataset.verifyMark || '',
      };
    };

    const kindSelector = (prefix) => {
      const options = document.getElementById(`${prefix}-kind-options`);
      const cards = {};
      ['llm', 'decision', 'rag'].forEach((kind) => {
        const radio = document.getElementById(`${prefix}-kind-${kind}`);
        if (!radio) return;
        const card = radio.closest('.pb-kind-option') || radio.closest('label') || radio.parentElement;
        const described = [radio, card]
          .map((element) => element.getAttribute('aria-describedby') || '')
          .join(' ')
          .split(/\s+/)
          .filter(Boolean)
          .map((id) => textOf(document.getElementById(id)));
        cards[kind] = {
          checked: radio.checked,
          disabled: radio.disabled,
          visible: isVisible(card),
          focused: document.activeElement === radio,
          // Everything the card tells a person: its text, a tooltip, a description it points to.
          says: [...new Set([textOf(card), card.title, radio.title, ...described].filter(Boolean))].join(' | '),
        };
      });
      return {
        visible: isVisible(options),
        cardCount: options ? options.querySelectorAll('.pb-kind-option').length : 0,
        cards,
      };
    };

    const visibleAlerts = [...new Set(document.querySelectorAll('.alert, [role="alert"]'))].filter(isVisible);
    const promptKindOptions = document.getElementById('LPB-kind-options');
    const promptSetupPane = document.getElementById('app-obj-LPB-pane-setup');
    return {
      lpb: flow('LPB'),
      rgb: flow('RGB'),
      kinds: { LPB: kindSelector('LPB'), RGB: kindSelector('RGB') },
      // The alert a page shows instead of a builder: it has a heading of its own.
      pageAlerts: visibleAlerts
        .filter((alert) => alert.querySelector('.alert-heading'))
        .map((alert) => ({ danger: alert.classList.contains('alert-danger'), text: textOf(alert) })),
      // An alert on the prompt Setup step that comes with or after the kind cards.
      alertsUnderPromptKinds: visibleAlerts
        .filter(
          (alert) =>
            promptKindOptions &&
            promptSetupPane &&
            promptSetupPane.contains(alert) &&
            !alert.contains(promptKindOptions) &&
            Boolean(promptKindOptions.compareDocumentPosition(alert) & Node.DOCUMENT_POSITION_FOLLOWING)
        )
        .map(textOf),
      built: {
        optimizeStep: present('#app-obj-LPB-step-optimize'),
        optimizePane: present('#app-obj-LPB-pane-optimize'),
        evaluateStep: present('#app-obj-LPB-step-evaluate'),
        evaluatePane: present('#app-obj-LPB-pane-evaluate'),
        decisionModels: present('#decision-model0'),
        decisionDesigner: present('#app-obj-LPB-decision-state'),
        decisionManifest: present('#app-obj-LPB-pet-decout-answers'),
        projectDropdown: present('#LPB-project-dropdown'),
      },
      visibleRagElements: [...document.querySelectorAll('[id^="RGB-"], [id^="app-obj-RGB"]')].filter(isVisible).map((element) => element.id),
    };
  });

const shownSteps = (flow) => (flow ? flow.steps.filter((entry) => !entry.hidden).map((entry) => entry.key) : []);
const PROMPT_STEPS_DECISION = ['setup', 'build', 'evaluate', 'finalize'];
const promptStepsLlm = (flags) => ['setup', 'build', ...(flags.optimization ? ['optimize'] : []), 'finalize'];
const RAG_STEPS = ['setup', 'documents', 'store', 'run'];
const offeredKinds = (flags) => ['llm', ...(flags.decision ? ['decision'] : []), ...(flags.rag ? ['rag'] : [])];

/**
 * The app is past its loading state: a builder is on the page (whatever its
 * ids are), or the alert that stands in for one.
 */
const waitBooted = (app) =>
  app.waitForFunction(() => Boolean(document.querySelector('.pb-stepper, .alert .alert-heading')), null, { timeout: 15000 });

// ---------------------------------------------------------------------------
// Sessions and scenarios
// ---------------------------------------------------------------------------

const allPageErrors = [];

/** A browser page showing the stand-in report (inside a page of its own when `nested`). */
async function openSession(context, { nested = false } = {}) {
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (error) => {
    pageErrors.push(error.message);
    console.log('[pageerror]', error.message);
  });
  page.on('console', (message) => {
    if (VERBOSE && message.type() === 'error') console.log('[console.error]', message.text());
  });
  page.on('dialog', (dialog) => dialog.accept());
  await page.goto(`${BASE}${nested ? TOP_PATH : HOST_PATH}`);
  const frameNamed = (name) => page.frames().find((frame) => frame.name() === name);
  const host = nested ? frameNamed('va-report') : page.mainFrame();
  if (!host) throw new Error('the stand-in report page did not load');
  await host.waitForFunction(() => Boolean(window.vaHost));

  const session = {
    page,
    pageErrors,
    app: () => frameNamed('va-ddc'),
    send: (message) => host.evaluate((sent) => window.vaHost.send(sent), message),
    received: () => host.evaluate(() => window.vaHost.received()),
    receivedByTop: () => (nested ? page.evaluate(() => window.vaTop.received()) : Promise.resolve([])),
    /** The envelopes the report page got for one resultName. */
    async envelopes(resultName) {
      return (await session.received()).filter(
        (message) => message.kind === 'object' && message.data && message.data.resultName === resultName && 'optionsConfig' in message.data
      );
    },
    async waitEnvelopes(resultName, timeout) {
      await eventually(async () => (await session.envelopes(resultName)).length > 0, timeout);
      return session.envelopes(resultName);
    },
    /**
     * A message of a new resultName, answered: the object answers its
     * messages in the order they came, so every message sent before it has
     * been answered - or not - by then. Returns the name.
     */
    async marker() {
      const name = nextResultName();
      await session.send(dataMessage(name));
      await session.waitEnvelopes(name, 30000);
      if (nested) await eventually(async () => (await session.receivedByTop()).some((message) => message.data && message.data.resultName === name), 30000);
      return name;
    },
    /**
     * Reload the object the way Visual Analytics does after an option changed:
     * the stored values go into the URL, and the data message follows at the
     * load event. Returns what came back and the frame to look at.
     */
    async load(stored, { settle = true } = {}) {
      // An empty document first, so requests of the previous one have reached
      // the mock before its log is cleared.
      await host.evaluate(() => window.vaHost.load('about:blank'));
      await quiet(page);
      await resetLog();
      await host.evaluate(() => window.vaHost.clear());
      if (nested) await page.evaluate(() => window.vaTop.clear());
      const url = appUrl(stored);
      const resultName = nextResultName();
      await host.evaluate(({ target, message }) => window.vaHost.load(target, message), { target: url, message: dataMessage(resultName) });
      const app = session.app();
      let answeredName = resultName;
      // The answer is posted from the message handler, so it is there within
      // milliseconds or not at all; the wait only covers a busy machine.
      let envelopes = await session.waitEnvelopes(resultName, 30000);
      await waitBooted(app);
      const answeredAtLoad = envelopes.length > 0;
      if (!answeredAtLoad) {
        // Tell "listens too late" from "does not listen": ask again now that the page is up.
        answeredName = nextResultName();
        await session.send(dataMessage(answeredName));
        envelopes = await session.waitEnvelopes(answeredName, 30000);
      }
      // Every request of the app answered, and none sent since.
      if (settle) await quiet(page);
      const optionsConfig = envelopes.length ? envelopes[0].data.optionsConfig : null;
      return {
        app,
        url,
        stored,
        answeredName,
        answeredAtLoad,
        answeredLate: !answeredAtLoad && envelopes.length > 0,
        optionsConfig,
        fields: fieldsOf(optionsConfig),
      };
    },
    close: () => page.close(),
  };
  return session;
}

class ScenarioStopped extends Error {}

/**
 * One scenario: its own page, its own checks. `check` records and goes on;
 * `need` records and, when it failed, skips the rest of this scenario only.
 */
async function scenario(context, name, body, sessionOptions) {
  const check = (condition, label) => step(Boolean(condition), `${name}: ${label}`);
  const need = (condition, label) => {
    if (!check(condition, label)) throw new ScenarioStopped(label);
  };
  let session = null;
  try {
    session = await openSession(context, sessionOptions);
    await body({ session, check, need });
  } catch (error) {
    if (error instanceof ScenarioStopped) note(`${name}: the rest of this scenario was skipped, it depends on the failed check above`);
    else step(false, `${name}: stopped by an error - ${firstLine(error.message)}`);
  } finally {
    if (session) {
      allPageErrors.push(...session.pageErrors.map((message) => `${name}: ${message}`));
      await session.close().catch(() => {});
    }
  }
}

/** The envelope of a load: answered at the load event, and once. Returns the fields, or null without an answer. */
async function answered(session, view, check) {
  check(
    view.answeredAtLoad,
    `the options message posted at the load event is answered${view.answeredLate ? ' (only a later message was: the listener is registered too late)' : ''}`
  );
  if (view.optionsConfig) {
    const count = (await session.envelopes(view.answeredName)).length;
    check(count === 1, `exactly one envelope came back for the resultName (got ${count})`);
  }
  return view.fields;
}

/** The options group of a load says what the stored values say: well-formed, the switches, their settings. */
function checkGroup(check, view, flags) {
  if (!view.fields) return;
  const problems = groupProblems(view.optionsConfig);
  check(problems.length === 0, `the options group is one flat, well-formed group${problems.length ? ` (${problems.join('; ')})` : ''}`);
  const shared = sharedProblems(view.fields);
  check(shared.length === 0, `the shared options come first${shared.length ? ` (${shared.join('; ')})` : ''}`);
  [
    ['enableDecision', flags.decision],
    ['enableOptimization', flags.optimization],
    ['enableRag', flags.rag],
  ].forEach(([switchName, on]) => {
    const switchIssues = switchProblems(view.fields, switchName, on);
    check(switchIssues.length === 0, `${switchName} is a checkbox, ${tickedOrNot(on)}${switchIssues.length ? ` (${switchIssues.join('; ')})` : ''}`);
    const dependentIssues = dependentProblems(view.fields, switchName, on);
    const expectation = on
      ? SWITCHES[switchName].dependents.length
        ? 'its settings follow it directly, with prefixed labels'
        : 'nothing of its own follows it'
      : 'none of its settings is offered';
    check(dependentIssues.length === 0, `${switchName} ${onOff(on)}: ${expectation}${dependentIssues.length ? ` (${dependentIssues.join('; ')})` : ''}`);
  });
}

/** The prompt flow as it has to look for a set of switches, before anything was clicked. */
function checkPromptFlow(check, snap, flags) {
  const kinds = offeredKinds(flags);
  check(snap.lpb && snap.lpb.visible, 'the prompt flow is shown');
  check(snap.pageAlerts.length === 0, `no alert stands in for the page${snap.pageAlerts.length ? ` (got: ${snap.pageAlerts[0].text.slice(0, 80)})` : ''}`);
  const steps = shownSteps(snap.lpb);
  check(sameList(steps, promptStepsLlm(flags)), `the steps are ${promptStepsLlm(flags).join(', ')} (got ${steps.join(', ') || 'none'})`);

  const cards = snap.kinds.LPB.cards;
  if (kinds.length < 2) {
    check(!snap.kinds.LPB.visible, 'one kind is no choice: the kind cards are not shown');
    check(!cards.decision && !cards.rag, `no card of a switched-off kind exists (found: ${['decision', 'rag'].filter((kind) => cards[kind]).join(', ') || 'none'})`);
  } else {
    const existing = ['llm', 'decision', 'rag'].filter((kind) => cards[kind]);
    check(sameList(existing, kinds), `the kind cards are ${kinds.join(', ')} (got ${existing.join(', ') || 'none'})`);
    check(snap.kinds.LPB.cardCount === kinds.length, `the kind block holds ${kinds.length} cards (got ${snap.kinds.LPB.cardCount})`);
    check(kinds.every((kind) => cards[kind] && cards[kind].visible), 'every offered card is visible');
    check(cards.llm && cards.llm.checked, 'the LLM kind is the one chosen');
  }

  check(snap.built.optimizeStep === flags.optimization && snap.built.optimizePane === flags.optimization,
    `the Optimize step and its pane ${flags.optimization ? 'exist' : 'do not exist'} (step ${snap.built.optimizeStep}, pane ${snap.built.optimizePane})`);
  check(snap.built.evaluateStep === flags.decision && snap.built.evaluatePane === flags.decision,
    `the Evaluate step and its pane ${flags.decision ? 'exist' : 'do not exist'} (step ${snap.built.evaluateStep}, pane ${snap.built.evaluatePane})`);
  const decisionParts = ['decisionModels', 'decisionDesigner', 'decisionManifest'];
  const builtDecisionParts = decisionParts.filter((part) => snap.built[part]);
  check(
    flags.decision ? builtDecisionParts.length === decisionParts.length : builtDecisionParts.length === 0,
    `the decision models, the question designer and the decision manifest options ${flags.decision ? 'are built' : 'are not built'} (found: ${builtDecisionParts.join(', ') || 'none'})`
  );
  check(!snap.rgb, 'the RAG flow is not built before it is chosen');
  check(snap.visibleRagElements.length === 0, `nothing of the RAG flow is visible (found: ${snap.visibleRagElements.join(', ') || 'none'})`);
}

/** The requests a page made while loading, held against its switches. Returns them for comparisons. */
async function checkBootRequests(check, stored, flags) {
  const requests = await appRequests();
  const decision = decisionRequests(requests);
  if (flags.decision) {
    check(decision.some((entry) => decoded(entry).includes('Decision Model Project')), 'decisions on: the Decision Model Project is looked up');
  } else {
    check(decision.length === 0, `decisions off: no Decision Model Project lookup and no decision model request (made: ${listed(decision)})`);
  }
  const rag = ragRequests(requests, stored);
  check(
    rag.length === 0,
    `${flags.rag ? 'RAG on but not chosen' : 'RAG off'}: no request for the embedding project, RAG projects, content root or caslibs (made: ${listed(rag)})`
  );
  const credentials = credentialRequests(requests);
  check(credentials.length <= 1, `the credential domain is read once at most before RAG is chosen (${credentials.length} requests)`);
  if (!flags.optimization) {
    const optimization = optimizationRequests(requests);
    check(optimization.length === 0, `optimization off: no job or folder request (made: ${listed(optimization)})`);
  }
  return requests;
}

/**
 * Choose RAG on the prompt Setup card and come back: the RAG flow replaces
 * the prompt flow, asks for its data only now, and neither flow is built twice.
 */
async function checkRagRoundTrip({ check, need }, app, stored, flags) {
  await app.evaluate(() => {
    document.getElementById('app-obj-LPB-stepper').dataset.verifyMark = 'prompt flow as first built';
  });
  await app.click('#LPB-kind-rag');
  need(
    await eventually(async () => {
      const snap = await snapshot(app);
      return Boolean(snap.rgb && snap.rgb.visible);
    }, 20000),
    'choosing RAG on the Setup card shows the RAG flow'
  );
  await quiet(app.page());
  let snap = await snapshot(app);
  check(sameList(shownSteps(snap.rgb), RAG_STEPS), `the RAG flow has the steps ${RAG_STEPS.join(', ')} (got ${shownSteps(snap.rgb).join(', ') || 'none'})`);
  check(snap.rgb.currentPane === 'setup', `the RAG flow opens on its Setup step (got ${snap.rgb.currentPane || 'none'})`);
  check(snap.lpb && !snap.lpb.visible && snap.lpb.hiddenByAttribute, 'the prompt flow stays in the page, hidden with the hidden attribute');
  const ragCards = snap.kinds.RGB.cards;
  const existing = ['llm', 'decision', 'rag'].filter((kind) => ragCards[kind]);
  check(snap.kinds.RGB.visible && sameList(existing, offeredKinds(flags)), `the RAG Setup card offers the same kinds (got ${existing.join(', ') || 'none'})`);
  check(ragCards.rag && ragCards.rag.checked, 'on the RAG Setup card the RAG kind is the one chosen');
  check(ragCards.rag && ragCards.rag.focused, 'the chosen card has the keyboard focus after the switch');
  // Read with the classifier the "no RAG request" checks use, so these two
  // lines also show that it recognises RAG traffic when there is some.
  const rag = ragRequests(await appRequests(), stored);
  check(rag.some((entry) => decoded(entry).includes('RAG-Engineering')), `only now the RAG projects are requested (RAG requests: ${rag.length})`);
  check(
    rag.some((entry) => pathOf(entry).startsWith(`/modelRepository/projects/${stored.embeddingProjectID}/`)),
    'only now the embedding project is requested'
  );
  await app.evaluate(() => {
    document.getElementById('app-obj-RGB-stepper').dataset.verifyMark = 'RAG flow as first built';
  });

  await app.click('#RGB-kind-llm');
  need(
    await eventually(async () => {
      const back = await snapshot(app);
      return Boolean(back.lpb && back.lpb.visible);
    }),
    'choosing the LLM kind on the RAG Setup card shows the prompt flow again'
  );
  snap = await snapshot(app);
  check(snap.lpb.mark === 'prompt flow as first built', 'the prompt flow is the one built at the start, not a new one');
  check(snap.lpb.currentPane === 'setup', `the prompt flow shows its Setup step (got ${snap.lpb.currentPane || 'none'})`);
  check(snap.rgb && !snap.rgb.visible && snap.rgb.hiddenByAttribute, 'the RAG flow stays in the page, hidden with the hidden attribute');
  const promptCards = snap.kinds.LPB.cards;
  check(promptCards.llm && promptCards.llm.checked && promptCards.rag && !promptCards.rag.checked, 'both Setup cards agree: LLM is chosen, RAG is not');
  check(promptCards.llm && promptCards.llm.focused, 'the chosen card has the keyboard focus after the switch back');
  check(sameList(shownSteps(snap.lpb), promptStepsLlm(flags)), `the prompt steps are unchanged (got ${shownSteps(snap.lpb).join(', ')})`);

  if (flags.decision) {
    await app.click('#LPB-kind-rag');
    need(
      await eventually(async () => {
        const again = await snapshot(app);
        return Boolean(again.rgb && again.rgb.visible);
      }),
      'RAG can be chosen a second time'
    );
    snap = await snapshot(app);
    check(snap.rgb.mark === 'RAG flow as first built', 'the RAG flow is the one built the first time, not a new one');
    await app.click('#RGB-kind-decision');
    const inDecision = await eventually(async () => {
      const decision = await snapshot(app);
      return Boolean(decision.lpb && decision.lpb.visible && sameList(shownSteps(decision.lpb), PROMPT_STEPS_DECISION));
    });
    snap = await snapshot(app);
    check(inDecision, `choosing the decision kind on the RAG Setup card opens the prompt flow with the Evaluate step (got ${shownSteps(snap.lpb).join(', ')})`);
    check(snap.kinds.LPB.cards.decision && snap.kinds.LPB.cards.decision.checked, 'the prompt Setup card shows the decision kind as chosen');
  }
}

// ---------------------------------------------------------------------------
// The run
// ---------------------------------------------------------------------------

(async () => {
  const mockAnswers = await fetch(BASE + '/__log').then(
    (response) => response.ok,
    () => false
  );
  if (!step(mockAnswers, `the mock answers at ${BASE} (start mock-server.js with the built index.html first)`)) {
    throw new Error('no mock to run against');
  }

  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  trackPagesOf(browser);
  // English, because a few checks read a sentence of the page.
  const context = await browser.newContext({ viewport: { width: 1500, height: 1100 }, locale: 'en-US' });
  const mockOrigin = new URL(BASE).origin;
  await context.route(
    (url) => url.origin === mockOrigin && url.pathname === HOST_PATH,
    (route) => route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: HOST_HTML })
  );
  await context.route(
    (url) => url.origin === mockOrigin && url.pathname === TOP_PATH,
    (route) => route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: TOP_HTML })
  );
  await context.addInitScript(installRagBuildFault, { stepperId: RAG_STEPPER_ID, parameter: FAIL_RAG_PARAMETER });
  const run = (name, body, sessionOptions) => scenario(context, name, body, sessionOptions);

  // ---- the message protocol ---------------------------------------------------
  await run('message protocol', async ({ session, check, need }) => {
    const view = await session.load(promptSide(), { settle: false });
    check(
      view.answeredAtLoad,
      `a message carrying a resultName, posted at the load event, is answered${view.answeredLate ? ' (only a later message was: the listener is registered too late)' : ''}`
    );
    need(view.optionsConfig, 'the answer is an envelope { resultName, optionsConfig }');
    const name = view.answeredName;
    const first = (await session.envelopes(name))[0];
    check(first.fromApp && first.kind === 'object', 'the envelope is an object posted by the embedded page');
    check(
      sameList(Object.keys(first.data).sort(), ['optionsConfig', 'resultName']),
      `the envelope carries resultName and optionsConfig, nothing else (got ${Object.keys(first.data).sort().join(', ')})`
    );

    await session.send(dataMessage(name));
    await session.marker();
    check((await session.envelopes(name)).length === 1, 'the same resultName a second time, as an object: still one envelope');
    await session.send(JSON.stringify(dataMessage(name)));
    await session.marker();
    check((await session.envelopes(name)).length === 1, 'the same resultName again, as a JSON string: still one envelope');

    const second = nextResultName();
    await session.send(JSON.stringify(dataMessage(second)));
    const secondEnvelopes = await session.waitEnvelopes(second, 30000);
    await session.marker();
    check((await session.envelopes(second)).length === 1, `a new resultName sent as a JSON string is answered once (got ${(await session.envelopes(second)).length})`);
    check(
      secondEnvelopes.length > 0 && JSON.stringify(secondEnvelopes[0].data.optionsConfig) === JSON.stringify(first.data.optionsConfig),
      'both answers carry the same options group'
    );

    // Whatever else arrives in a message event is not for the app.
    const beforeNoise = (await session.received()).length;
    const errorsBeforeNoise = session.pageErrors.length;
    const noise = ['not json at all', '{"broken":', 42, true, null, { hello: 'world' }, JSON.stringify({ hello: 'world' }), { resultName: 7 }, { resultName: '' }, [], '[]', '"text"', '7'];
    for (const message of noise) await session.send(message);
    const markers = [await session.marker()];
    // What came back, apart from the answers to the markers.
    const receivedBesides = async () => (await session.received()).filter((message) => !(message.data && markers.includes(message.data.resultName))).length;
    check((await receivedBesides()) === beforeNoise, 'messages without a usable resultName are not answered');
    check(session.pageErrors.length === errorsBeforeNoise, 'and none of them raises an error');
    await session.send('null');
    markers.push(await session.marker());
    check(
      (await receivedBesides()) === beforeNoise && session.pageErrors.length === errorsBeforeNoise,
      'a JSON string that parses to null is ignored without an error'
    );
    const strangers = (await session.received()).filter((message) => !(message.kind === 'object' && message.data && 'optionsConfig' in message.data));
    check(strangers.length === 0, `the app posts nothing but envelopes (${strangers.length} other messages)`);

    const flat = flatGroupProblems(view.optionsConfig);
    check(flat.length === 0, `the options are ONE flat group: version 1, urlOption true, a name, a label, no nested groups${flat.length ? ` (${flat.join('; ')})` : ''}`);
    need(view.fields, 'the group has a list of fields');
    const duplicates = duplicateNames(view.fields);
    check(duplicates.length === 0, `every field name is used once${duplicates.length ? ` (twice: ${duplicates.join(', ')})` : ''}`);
    const types = wrongTypes(view.fields);
    check(types.length === 0, `every type is exactly 'String' or 'Boolean'${types.length ? ` (others: ${types.join(', ')})` : ''}`);
    const fieldIssues = fieldProblems(view.fields);
    check(fieldIssues.length === 0, `every field has a name, a label and a value of its type${fieldIssues.length ? ` (${fieldIssues.join('; ')})` : ''}`);
    // A page that was never configured beyond its ids shows the defaults.
    checkGroup(check, view, { decision: true, optimization: false, rag: false });
    const names = view.fields.map((field) => field.name);
    check(
      sameList(names.slice().sort(), [...SHARED_OPTIONS, ...SWITCH_NAMES].sort()),
      `with the defaults the group is the shared options and the three switches (got ${names.join(', ')})`
    );
    const stray = names.filter((fieldName) => fieldName === 'id' || fieldName.startsWith('_'));
    check(stray.length === 0, `neither the element id nor a Job Execution parameter is offered as an option${stray.length ? ` (found ${stray.join(', ')})` : ''}`);
  });

  await run(
    'report embedded in another page',
    async ({ session, check, need }) => {
      const view = await session.load(promptSide(), { settle: false });
      need(view.optionsConfig, 'the options message is answered when the object has a parent and a different top window');
      await session.marker();
      const toParent = (await session.envelopes(view.answeredName)).length;
      const toTop = (await session.receivedByTop()).filter(
        (message) => message.kind === 'object' && message.data && message.data.resultName === view.answeredName && 'optionsConfig' in message.data
      ).length;
      check(toParent === 1, `the parent window gets exactly one envelope (got ${toParent})`);
      check(toTop === 1, `the top window gets exactly one envelope (got ${toTop})`);
    },
    { nested: true }
  );

  // ---- every option, switched on, echoes the URL --------------------------------
  await run('all options', async ({ session, check }) => {
    const stored = {
      viyaHost: BASE,
      ...promptSide(),
      deploymentType: 'aca',
      judgeModel: 'demo_llm',
      modelCardReportURI: '/reports/reports/zz-verify-options',
      credentialDomain: 'all-keys',
      // Unticked, so that it differs from its default like the others.
      sslVerify: 'false',
      enableDecision: 'true',
      enableOptimization: 'true',
      enableRag: 'true',
      ...OPTIMIZATION_SETTINGS,
      ...RAG_SETTINGS,
      // Off like the other on/off settings, so that every one of them differs
      // from its default and an echo proves the value was read.
      enable_pgvector: 'false',
    };
    const view = await session.load(stored, { settle: false });
    const fields = await answered(session, view, check);
    if (!fields) return;
    checkGroup(check, view, { decision: true, optimization: true, rag: true });
    const names = fields.map((field) => field.name);
    const notOffered = Object.keys(stored).filter((name) => !names.includes(name));
    check(notOffered.length === 0, `with every switch on, every option of the URL is in the group${notOffered.length ? ` (missing: ${notOffered.join(', ')})` : ''}`);
    const mirror = mirrorProblems(fields, stored);
    check(mirror.length === 0, `every posted value says what the URL says${mirror.length ? ` (${mirror.join('; ')})` : ''}`);
    // The regression guard for the dropdown Visual Analytics does not restore,
    // read where every option of the page is in the group.
    const wordKeys = wordKeyDropdowns(fields);
    const dropdowns = fields.filter((field) => field.type === 'String' && Array.isArray(field.dataProvider)).map((field) => field.name);
    check(
      wordKeys.length === 0 && dropdowns.length > 0,
      `no dropdown has the word 'true' or 'false' as a key (${dropdowns.length} dropdowns: ${dropdowns.join(', ') || 'none'}${wordKeys.length ? `; with such a key: ${wordKeys.join(', ')}` : ''})`
    );
    const checkboxes = fields.filter((field) => field.type === 'Boolean');
    const notPlain = checkboxes.filter((field) => typeof field.value !== 'boolean' || 'dataProvider' in field).map((field) => field.name);
    check(
      SWITCH_NAMES.every((name) => checkboxes.some((field) => field.name === name)) && notPlain.length === 0,
      `the three switches are among the ${checkboxes.length} checkboxes, and every checkbox has a boolean value and no dataProvider${notPlain.length ? ` (not so: ${notPlain.join(', ')})` : ''}`
    );
    const tls = fieldNamed(fields, SSL_VERIFY.name);
    check(
      tls && tls.type === 'Boolean' && tls.value === false && !('dataProvider' in tls),
      `sslVerify=false in the URL shows the checkbox unticked (got ${JSON.stringify(tls && { type: tls.type, value: tls.value })})`
    );
    const expectedCount = SHARED_OPTIONS.length + SWITCH_NAMES.length + OPTIMIZATION_OPTIONS.length + RAG_OPTIONS.length;
    const backendSwitches = names.filter((name) => BACKEND_SWITCH_PATTERN.test(name));
    check(
      fields.length === expectedCount + backendSwitches.length && backendSwitches.length >= KNOWN_BACKEND_SWITCHES.length,
      `the group holds ${expectedCount} options plus one switch per vector store (got ${fields.length}, vector stores: ${backendSwitches.join(', ') || 'none'})`
    );
    const optimizationContext = fieldNamed(fields, 'computeContext');
    const ragContext = fieldNamed(fields, 'ragComputeContext');
    check(
      optimizationContext && optimizationContext.value === OPTIMIZATION_SETTINGS.computeContext && String(optimizationContext.label).startsWith('Optimization: '),
      `computeContext is the optimization compute context (label "${optimizationContext ? optimizationContext.label : 'missing'}", value ${JSON.stringify(optimizationContext && optimizationContext.value)})`
    );
    check(
      ragContext && ragContext.value === RAG_SETTINGS.ragComputeContext && String(ragContext.label).startsWith('RAG: '),
      `ragComputeContext is the RAG compute context (label "${ragContext ? ragContext.label : 'missing'}", value ${JSON.stringify(ragContext && ragContext.value)})`
    );

    // Each context is read from its own parameter and from nowhere else.
    const onlyOptimization = await session.load({ ...stored, computeContext: 'ONLY-OPTIMIZATION', ragComputeContext: undefined }, { settle: false });
    const leakedIntoRag = fieldNamed(onlyOptimization.fields, 'ragComputeContext');
    check(
      leakedIntoRag && leakedIntoRag.value !== 'ONLY-OPTIMIZATION' && (fieldNamed(onlyOptimization.fields, 'computeContext') || {}).value === 'ONLY-OPTIMIZATION',
      `computeContext alone in the URL does not set the RAG compute context (ragComputeContext shows ${JSON.stringify(leakedIntoRag && leakedIntoRag.value)})`
    );
    const onlyRag = await session.load({ ...stored, computeContext: undefined, ragComputeContext: 'ONLY-RAG' }, { settle: false });
    const leakedIntoOptimization = fieldNamed(onlyRag.fields, 'computeContext');
    check(
      leakedIntoOptimization && leakedIntoOptimization.value !== 'ONLY-RAG' && (fieldNamed(onlyRag.fields, 'ragComputeContext') || {}).value === 'ONLY-RAG',
      `ragComputeContext alone in the URL does not set the optimization compute context (computeContext shows ${JSON.stringify(leakedIntoOptimization && leakedIntoOptimization.value)})`
    );
  });

  // ---- the checkbox for TLS verification in manifested models -------------------
  await run('sslVerify', async ({ session, check, need }) => {
    // A page that was never told: the option is posted, ticked.
    const bare = await session.load(promptSide());
    need(bare.fields, 'the options message is answered');
    const names = bare.fields.map((field) => field.name);
    const field = fieldNamed(bare.fields, SSL_VERIFY.name);
    need(field, 'sslVerify is in the options group');
    check(
      field.type === 'Boolean' && field.value === true && !('dataProvider' in field),
      `it is posted as a 'Boolean' option - a checkbox - and ticked by default (got ${JSON.stringify({ type: field.type, value: field.value, dataProvider: 'dataProvider' in field })})`
    );
    check(field.label === SSL_VERIFY.label, `its label is "${SSL_VERIFY.label}" (got "${field.label}")`);
    check(typeof field.tooltip === 'string' && field.tooltip.includes('LLMCONTAINERSSLVERIFY') && field.tooltip.includes('TLS-Verification-Off'), 'its tooltip names the tag and the environment variable that overrides it');
    check(
      names.indexOf(SSL_VERIFY.name) === names.indexOf('credentialDomain') + 1 && names.indexOf(SSL_VERIFY.name) < Math.min(...SWITCH_NAMES.map((name) => names.indexOf(name))),
      `it stands directly after credentialDomain, ahead of the switches (order: ${names.join(', ')})`
    );
    const bareRequests = signatures(await appRequests());
    const bareGroup = JSON.stringify(bare.fields.filter((entry) => entry.name !== SSL_VERIFY.name));

    // What Visual Analytics mirrors for a checkbox: the word true or false.
    const unticked = await session.load({ ...promptSide(), sslVerify: false });
    const untickedField = fieldNamed(unticked.fields, SSL_VERIFY.name);
    check(untickedField && untickedField.value === false && untickedField.type === 'Boolean', `sslVerify=false: the checkbox is unticked after the reload (got ${JSON.stringify(untickedField && untickedField.value)})`);
    check(unticked.url.includes('sslVerify=false'), 'an unticked checkbox is in the URL as the word false, not left out');
    // No leak: the option is a manifest-time default. It changes no other
    // option, no request of the page and nothing that is built.
    check(
      JSON.stringify((unticked.fields || []).filter((entry) => entry.name !== SSL_VERIFY.name)) === bareGroup,
      'unticked, every other option of the group is what it is when ticked'
    );
    const untickedRequests = signatures(await appRequests());
    check(
      sameList(untickedRequests, bareRequests),
      `unticked, the page asks SAS Viya for exactly what it asks when ticked (${untickedRequests.length} requests against ${bareRequests.length})`
    );
    check(!untickedRequests.some((request) => /sslVerify/i.test(request)), 'and the option is in none of its requests');
    const untickedSnap = await snapshot(unticked.app);
    checkPromptFlow(check, untickedSnap, { decision: true, optimization: false, rag: false });
    const ticked = await session.load({ ...promptSide(), sslVerify: true });
    check((fieldNamed(ticked.fields, SSL_VERIFY.name) || {}).value === true && ticked.url.includes('sslVerify=true'), 'sslVerify=true: the checkbox is ticked after the reload');

    // Read strictly, and the failure direction is the safe one: only a clear
    // "off" unticks it.
    for (const [raw, expected] of [
      ['FALSE', false], [' false ', false], ['0', false], ['no', false], ['off', false],
      ['TRUE', true], ['1', true], ['yes', true],
      ['', true], ['undefined', true], ['null', true], ['disabled', true], ['fals', true], ['2', true],
    ]) {
      const view = await session.load({ ...promptSide(), sslVerify: raw }, { settle: false });
      const value = (fieldNamed(view.fields, SSL_VERIFY.name) || {}).value;
      check(value === expected, `sslVerify=${JSON.stringify(raw)} in the URL: verification is ${expected ? 'ON' : 'off'} (the checkbox shows ${JSON.stringify(value)})`);
    }

    // With RAG on it is still one shared option, once in the group (what it
    // does to a retrieval model is checked by verify-rag.js).
    const withRag = await session.load(storedReport({ enableDecision: true, enableOptimization: true, enableRag: true }, { sslVerify: false }), { settle: false });
    const ragNames = (withRag.fields || []).map((entry) => entry.name);
    check(
      ragNames.filter((name) => name === SSL_VERIFY.name).length === 1 && ragNames.indexOf(SSL_VERIFY.name) === ragNames.indexOf('credentialDomain') + 1,
      'with every switch on the option is in the group once, still directly after credentialDomain'
    );
    check(!ragNames.some((name) => /ssl/i.test(name) && name !== SSL_VERIFY.name && name !== 'storeSslmode'), 'and no second option about TLS verification is posted for RAG');
    check(session.pageErrors.length === 0, `no uncaught error on the way (uncaught: ${session.pageErrors.join(' | ') || 'none'})`);
  });

  // ---- the eight combinations of the three switches -----------------------------
  const bootRequestsOf = new Map();
  for (const decision of [true, false]) {
    for (const optimization of [true, false]) {
      for (const rag of [true, false]) {
        const flags = { decision, optimization, rag };
        await run(describeFlags(flags), async ({ session, check, need }) => {
          const stored = storedReport(switchValues(flags));
          const view = await session.load(stored);
          const app = view.app;
          await answered(session, view, check);
          checkGroup(check, view, flags);
          const snap = await snapshot(app);
          checkPromptFlow(check, snap, flags);
          const requests = await checkBootRequests(check, stored, flags);
          bootRequestsOf.set(describeFlags(flags), signatures(requests));

          if (flags.decision && snap.kinds.LPB.cards.decision) {
            await app.click('#LPB-kind-decision');
            const switched = await eventually(async () => sameList(shownSteps((await snapshot(app)).lpb), PROMPT_STEPS_DECISION), 5000);
            check(switched, `choosing the decision kind shows ${PROMPT_STEPS_DECISION.join(', ')} (got ${shownSteps((await snapshot(app)).lpb).join(', ')})`);
            await app.click('#LPB-kind-llm');
            const restored = await eventually(async () => sameList(shownSteps((await snapshot(app)).lpb), promptStepsLlm(flags)), 5000);
            check(restored, `back on the LLM kind the steps are ${promptStepsLlm(flags).join(', ')} again (got ${shownSteps((await snapshot(app)).lpb).join(', ')})`);
          }
          if (flags.rag) {
            need(snap.kinds.LPB.cards.rag && !snap.kinds.LPB.cards.rag.disabled, 'the RAG kind can be chosen');
            await resetLog();
            await checkRagRoundTrip({ check, need }, app, stored, flags);
          }
        });
      }
    }
  }
  // Switching RAG on must not change what a page asks for until RAG is chosen:
  // also not a second read of the credential domain.
  for (const decision of [true, false]) {
    for (const optimization of [true, false]) {
      const withRag = bootRequestsOf.get(describeFlags({ decision, optimization, rag: true }));
      const withoutRag = bootRequestsOf.get(describeFlags({ decision, optimization, rag: false }));
      const label = `decision ${onOff(decision)}, optimization ${onOff(optimization)}: RAG on but not chosen asks for exactly what RAG off asks for`;
      if (!withRag || !withoutRag) {
        step(false, `${label} (one of the two pages did not load)`);
      } else {
        const extra = withRag.filter((entry) => !withoutRag.includes(entry));
        const missing = withoutRag.filter((entry) => !withRag.includes(entry));
        step(
          sameList(withRag, withoutRag),
          `${label}${sameList(withRag, withoutRag) ? ` (${withRag.length} requests)` : ` (only with RAG on: ${extra.join(' ; ') || 'none'}; only with RAG off: ${missing.join(' ; ') || 'none'})`}`
        );
      }
    }
  }

  // ---- how a switch value may be written -----------------------------------------
  // One load per spelling, all three switches written that way. `undefined`
  // stands for a report that has no value for the switch.
  const SPELLINGS = [
    ['true', 'true'],
    ['false', 'false'],
    ['1', '1'],
    ['0', '0'],
    ['TRUE', 'TRUE'],
    ['False', 'False'],
    ['yes', 'yes'],
    ['no', 'no'],
    ['on', 'on'],
    ['off', 'off'],
    [' true ', 'true with blanks around it'],
    [undefined, 'absent'],
    ['', 'blank'],
    ['maybe', 'the junk value maybe'],
    ['undefined', 'the junk value undefined'],
    ['2', 'the junk value 2'],
  ];
  await run('switch spellings', async ({ session, check }) => {
    for (const [raw, spelling] of SPELLINGS) {
      const stored = storedReport({ enableDecision: raw, enableOptimization: raw, enableRag: raw });
      const flags = flagsOf(stored);
      let view;
      try {
        view = await session.load(stored, { settle: false });
      } catch (error) {
        check(false, `${spelling}: the page loads (${firstLine(error.message)})`);
        continue;
      }
      const snap = await snapshot(view.app);
      const shown = {
        enableDecision: Boolean(snap.kinds.LPB.cards.decision),
        enableOptimization: snap.built.optimizeStep,
        enableRag: Boolean(snap.kinds.LPB.cards.rag),
      };
      [
        ['enableDecision', flags.decision, 'the decision kind'],
        ['enableOptimization', flags.optimization, 'the Optimize step'],
        ['enableRag', flags.rag, 'the RAG kind'],
      ].forEach(([switchName, on, part]) => {
        const posted = (fieldNamed(view.fields, switchName) || {}).value;
        const reading = raw === undefined || expectedFlag(raw, null) === null ? `the default, ${onOff(on)}` : onOff(on);
        check(
          shown[switchName] === on && posted === on,
          `${switchName} ${spelling} reads as ${reading}: ${part} ${on ? 'exists' : 'does not exist'} and the checkbox is ${tickedOrNot(on)} (page: ${shown[switchName] ? 'exists' : 'missing'}, option: ${JSON.stringify(posted)})`
        );
      });
    }
  });

  // ---- the checkboxes, ticked and unticked the way Visual Analytics does it --------
  // The pane holds what the app posted; a click on a checkbox changes that one
  // value, the report keeps every value it ever stored, all of it is mirrored
  // into the URL and the object is reloaded.
  await run('checkbox round trip', async ({ session, check, need }) => {
    let view = await session.load(promptSide(), { settle: false });
    need(view.fields, 'the options group of an unchanged page is posted');
    let stored = paneValues(view.fields);
    const clicks = [
      ['enableOptimization', true, { decision: true, optimization: true, rag: false }],
      ['enableRag', true, { decision: true, optimization: true, rag: true }],
      // The switch that is on by default: unticked must not read as "never set".
      ['enableDecision', false, { decision: false, optimization: true, rag: true }],
      ['enableOptimization', false, { decision: false, optimization: false, rag: true }],
      ['enableRag', false, { decision: false, optimization: false, rag: false }],
      ['enableDecision', true, { decision: true, optimization: false, rag: false }],
    ];
    for (const [switchName, ticked, flags] of clicks) {
      const action = `${switchName} ${tickedOrNot(ticked)}`;
      stored = { ...stored, [switchName]: ticked };
      view = await session.load(stored, { settle: false });
      const word = new URL(view.url).searchParams.get(switchName);
      check(word === String(ticked), `${action}: the URL carries ${switchName}=${ticked} (got ${JSON.stringify(word)})`);
      need(view.fields, `${action}: the reloaded page posts its options group`);
      const issues = Object.entries(switchValues(flags)).flatMap(([name, on]) =>
        [...switchProblems(view.fields, name, on), ...dependentProblems(view.fields, name, on)].map((issue) => `${name}: ${issue}`)
      );
      check(issues.length === 0, `${action}: the three checkboxes show ${describeFlags(flags)}, each with its settings or without${issues.length ? ` (${issues.join('; ')})` : ''}`);
      const snap = await snapshot(view.app);
      const shown = { decision: Boolean(snap.kinds.LPB.cards.decision), optimization: snap.built.optimizeStep, rag: Boolean(snap.kinds.LPB.cards.rag) };
      check(
        shown.decision === flags.decision && shown.optimization === flags.optimization && shown.rag === flags.rag,
        `${action}: the page follows (${describeFlags(shown)})`
      );
      // Settings a switch brought stay in the report after it is unticked.
      stored = { ...stored, ...paneValues(view.fields) };
    }
  });

  // ---- settings of a switched-off switch that are still in the URL ----------------
  await run('stale settings', async ({ session, check }) => {
    const flags = { decision: true, optimization: false, rag: false };
    const clean = await session.load({ ...promptSide(), ...switchValues(flags) });
    const cleanRequests = signatures(await appRequests());
    const stale = storedReport(switchValues(flags));
    const view = await session.load(stale);
    await answered(session, view, check);
    checkGroup(check, view, flags);
    checkPromptFlow(check, await snapshot(view.app), flags);
    const staleRequests = signatures(await appRequests());
    const extra = staleRequests.filter((entry) => !cleanRequests.includes(entry));
    check(
      sameList(staleRequests, cleanRequests),
      `the optimization and RAG settings of the URL change no request while their switches are off${extra.length ? ` (additional: ${extra.join(' ; ')})` : ''}`
    );
    check(
      clean.fields && view.fields && sameList(clean.fields.map((field) => field.name), view.fields.map((field) => field.name)),
      'and add no option to the group'
    );
  });

  // ---- a report saved before the switches existed --------------------------------
  await run('report saved with 2.2.0, optimization on', async ({ session, check }) => {
    // What such a report holds: the optimization switch as the word it always
    // was, no decision and no RAG switch, and labels nothing posts any more.
    const stored = {
      ...promptSide(),
      enableOptimization: 'true',
      computeContext: 'ctx',
      optimizeKeyLibrary: 'Public',
      optimizeKeyTable: 'OPTIMIZE_KEYS',
      enabledBackends: 'pgvector',
    };
    const flags = { decision: true, optimization: true, rag: false };
    const view = await session.load(stored);
    const fields = await answered(session, view, check);
    checkGroup(check, view, flags);
    checkPromptFlow(check, await snapshot(view.app), flags);
    const requests = await checkBootRequests(check, stored, flags);
    if (fields) {
      check((fieldNamed(fields, 'computeContext') || {}).value === 'ctx', `computeContext still sets the optimization compute context (got ${JSON.stringify((fieldNamed(fields, 'computeContext') || {}).value)})`);
      const dead = fields.map((field) => field.name).filter((name) => ['optimizeKeyLibrary', 'optimizeKeyTable', 'enabledBackends', '_program', '_action', 'id'].includes(name));
      check(dead.length === 0, `retired labels and Job Execution parameters are not offered as options${dead.length ? ` (found ${dead.join(', ')})` : ''}`);
    }
    const projects = await view.app.$$eval('#LPB-project-dropdown option', (options) => options.map((option) => option.value));
    check(projects.includes('proj-1'), `the prompt projects are listed (got ${projects.join(', ')})`);

    const plain = await session.load({ ...promptSide(), enableOptimization: 'true', computeContext: 'ctx' });
    const plainRequests = signatures(await appRequests());
    check(
      plain.fields && fields && JSON.stringify(plain.fields) === JSON.stringify(fields) && sameList(plainRequests, signatures(requests)),
      'the retired labels change neither the options group nor a request'
    );
  });
  await run('report saved with 2.2.0, as shipped', async ({ session, check }) => {
    const stored = {
      viyaHost: '',
      ...promptSide(),
      deploymentType: 'k8s',
      judgeModel: '',
      modelCardReportURI: '',
      credentialDomain: 'agentic-ai-keys',
      enableOptimization: 'false',
      computeContext: 'SAS Job Execution compute context',
      optimizeJobProgram: '/Public/Jobs/Optimize-Prompt-DSPy',
      minOptimizeSamples: '30',
      optimizeKeyLibrary: '',
      optimizeKeyTable: '',
    };
    const flags = { decision: true, optimization: false, rag: false };
    const view = await session.load(stored);
    await answered(session, view, check);
    checkGroup(check, view, flags);
    checkPromptFlow(check, await snapshot(view.app), flags);
    await checkBootRequests(check, stored, flags);
  });

  // ---- id is not an option -----------------------------------------------------
  await run('id in the URL', async ({ session, check, need }) => {
    const flags = { decision: true, optimization: true, rag: true };
    const stored = storedReport(switchValues(flags), { id: 'ZZTOP' });
    const view = await session.load(stored);
    const app = view.app;
    const idsNamedAfterIt = () => app.$$eval('[id*="ZZTOP"]', (elements) => elements.map((element) => element.id));
    let snap = await snapshot(app);
    check(snap.lpb && snap.built.projectDropdown, 'the prompt flow keeps its ids (app-obj-LPB-stepper, LPB-project-dropdown)');
    check((await idsNamedAfterIt()).length === 0, `no element id is built from the parameter (found ${(await idsNamedAfterIt()).join(', ') || 'none'})`);
    check(view.fields && !fieldNamed(view.fields, 'id'), 'id is not offered as an option');
    need(snap.kinds.LPB.cards.rag, 'the kind cards keep their ids (LPB-kind-rag)');
    await app.click('#LPB-kind-rag');
    const shown = await eventually(async () => {
      const now = await snapshot(app);
      return Boolean(now.rgb && now.rgb.visible);
    }, 20000);
    snap = await snapshot(app);
    check(shown && snap.kinds.RGB.cards.rag, 'the RAG flow keeps its ids (app-obj-RGB-stepper, RGB-kind-rag)');
    check((await idsNamedAfterIt()).length === 0, `also with the RAG flow built no element id comes from the parameter (found ${(await idsNamedAfterIt()).join(', ') || 'none'})`);
  });

  // ---- RAG needs a credential domain ---------------------------------------------
  await run('credentialDomain=none with RAG on', async ({ session, check, need }) => {
    const flags = { decision: true, optimization: false, rag: true };
    const usable = await session.load(storedReport(switchValues(flags)), { settle: false });
    const usableCard = (await snapshot(usable.app)).kinds.LPB.cards.rag;
    const stored = storedReport(switchValues(flags), { credentialDomain: 'none' });
    const view = await session.load(stored);
    const app = view.app;
    const fields = await answered(session, view, check);
    checkGroup(check, view, flags);
    if (fields) check((fieldNamed(fields, 'credentialDomain') || {}).value === 'none', `the option keeps showing none (got ${JSON.stringify((fieldNamed(fields, 'credentialDomain') || {}).value)})`);
    const snap = await snapshot(app);
    const card = snap.kinds.LPB.cards.rag;
    need(card && card.visible, 'the RAG card is shown');
    check(card.disabled && !card.checked, 'the RAG card cannot be chosen');
    check(
      usableCard && card.says !== usableCard.says && card.says.length > usableCard.says.length && /credential/i.test(card.says),
      `the card says why: a sentence about the credential domain that a usable card does not carry (card: "${card.says.slice(0, 160)}")`
    );
    check(sameList(shownSteps(snap.lpb), promptStepsLlm(flags)) && snap.kinds.LPB.cards.llm.checked, 'the prompt flow is shown as usual');
    const requests = await appRequests();
    check(ragRequests(requests, stored).length === 0, `no RAG request is made (made: ${listed(ragRequests(requests, stored))})`);
    check(credentialRequests(requests).length === 0, `no credential domain is read at all (made: ${listed(credentialRequests(requests))})`);
    // A click that gets past the disabled state must still change nothing.
    await app.click('label[for="LPB-kind-rag"]', { force: true, timeout: 3000 }).catch(() => {});
    await app.evaluate(() => {
      const radio = document.getElementById('LPB-kind-rag');
      if (radio) radio.click();
    });
    await quiet(session.page);
    const afterClick = await snapshot(app);
    check(
      !afterClick.rgb && afterClick.lpb.visible && afterClick.kinds.LPB.cards.llm.checked && ragRequests(await appRequests(), stored).length === 0,
      'clicking the disabled card builds nothing and requests nothing'
    );
  });

  // ---- a decision template reached although decisions are off --------------------
  // The listing of proj-1 is made to carry the decision template WITHOUT its
  // kind, as a listing can: the kind then only shows in the registration the
  // page reads when the template is opened.
  const listDecisionTemplateWithoutKind = (page) =>
    page.route(
      (url) => url.pathname === '/modelRepository/projects/proj-1/models',
      async (route) => {
        const filter = new URL(route.request().url()).searchParams.get('filter') || '';
        if (filter.includes('Decision-Template')) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }) });
        const response = await route.fetch();
        let listing = { items: [] };
        try {
          listing = await response.json();
        } catch {
          /* keep the empty listing */
        }
        const items = [...(listing.items || []), { id: 'model-dec', name: 'Ticket Routing', createdBy: 'anna', modifiedBy: 'anna' }];
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...listing, items }) });
      }
    );
  const openDecisionTemplate = async (app, need) => {
    await app.selectOption('#LPB-project-dropdown', 'proj-1');
    need(
      await eventually(async () => (await app.$$eval('#LPB-prompt-dropdown option', (options) => options.map((option) => option.value))).includes('model-dec')),
      'the template is listed among the prompts of the project'
    );
    await resetLog();
    await app.selectOption('#LPB-prompt-dropdown', 'model-dec');
  };
  await run('decision template opened, decisions on', async ({ session, check, need }) => {
    // The control for the next scenario: with decisions on, opening this
    // template is what switches the page to the decision kind.
    await listDecisionTemplateWithoutKind(session.page);
    const view = await session.load({ ...promptSide(), enableDecision: 'true' });
    await openDecisionTemplate(view.app, need);
    const switched = await eventually(async () => sameList(shownSteps((await snapshot(view.app)).lpb), PROMPT_STEPS_DECISION));
    check(switched, 'the registration says decision template, and the page follows: the Evaluate step appears');
  });
  await run('decision template opened, decisions off', async ({ session, check, need }) => {
    await listDecisionTemplateWithoutKind(session.page);
    const flags = { decision: false, optimization: false, rag: false };
    const view = await session.load({ ...promptSide(), enableDecision: 'false' });
    const app = view.app;
    await openDecisionTemplate(app, need);
    const toast = await eventually(async () => (await app.$$eval('#pb-toast-container .toast', (toasts) => toasts.map((element) => element.textContent.trim()))).some(Boolean), 4000);
    const toastText = (await app.$$eval('#pb-toast-container .toast', (toasts) => toasts.map((element) => element.textContent.trim()))).join(' | ');
    check(toast, `opening it is refused with a toast (toast: "${toastText.slice(0, 140)}")`);
    const backOnPlaceholder = await eventually(() => app.$eval('#LPB-prompt-dropdown', (dropdown) => dropdown.selectedIndex === 0), 4000);
    check(backOnPlaceholder, 'the prompt dropdown is back on its placeholder');
    await quiet(session.page);
    const snap = await snapshot(app);
    check(sameList(shownSteps(snap.lpb), promptStepsLlm(flags)), `the page stays an LLM page: ${promptStepsLlm(flags).join(', ')} (got ${shownSteps(snap.lpb).join(', ')})`);
    check(!snap.built.evaluateStep && !snap.built.decisionModels && !snap.built.decisionDesigner, 'nothing of the decision side was built for it');
    check(!(await app.$('#app-obj-LPB-pet-0')), 'none of its saved runs is rendered');
    const requests = await appRequests();
    check(decisionRequests(requests).length === 0, `no decision model request follows (made: ${listed(decisionRequests(requests))})`);
  });

  // ---- a page without its ids ------------------------------------------------------
  for (const [name, stored, flags] of [
    ['nothing configured', {}, { decision: true, optimization: false, rag: false }],
    ['RAG switched on without a repository', { enableRag: 'true', embeddingProjectID: 'emb-proj' }, { decision: true, optimization: false, rag: true }],
    ['prompt side without its SCR endpoint, RAG off', { modelRepositoryID: 'repo-1', llmProjectID: 'llm-proj' }, { decision: true, optimization: false, rag: false }],
  ]) {
    await run(`unconfigured (${name})`, async ({ session, check }) => {
      const view = await session.load(stored);
      await answered(session, view, check);
      checkGroup(check, view, flags);
      const snap = await snapshot(view.app);
      check(snap.pageAlerts.length === 1 && !snap.pageAlerts[0].danger, `the configuration alert is shown (${snap.pageAlerts.map((alert) => alert.text.slice(0, 60)).join(' | ') || 'no alert'})`);
      check(!snap.lpb && !snap.rgb, 'no builder is on the page');
      const requests = await appRequests();
      check(requests.length === 0, `SAS Viya is not called (made: ${listed(requests)})`);
    });
  }

  // ---- RAG without the prompt side ---------------------------------------------------
  const ragOnly = { modelRepositoryID: 'repo-1', enableRag: 'true', embeddingProjectID: 'emb-proj' };
  await run('RAG-only page', async ({ session, check, need }) => {
    const flags = { decision: true, optimization: false, rag: true };
    const view = await session.load(ragOnly);
    await answered(session, view, check);
    checkGroup(check, view, flags);
    const snap = await snapshot(view.app);
    need(snap.rgb && snap.rgb.visible, `the RAG flow is shown${snap.pageAlerts.length ? ` (the page shows: ${snap.pageAlerts[0].text.slice(0, 80)})` : ''}`);
    check(sameList(shownSteps(snap.rgb), RAG_STEPS) && snap.rgb.currentPane === 'setup', `with its four steps, on Setup (got ${shownSteps(snap.rgb).join(', ')}, pane ${snap.rgb.currentPane || 'none'})`);
    check(!snap.lpb && !snap.built.projectDropdown, 'the prompt flow is not built');
    check(snap.pageAlerts.length === 0, 'no alert stands in for the page');
    check(!snap.kinds.RGB.visible && !snap.kinds.LPB.visible, 'one kind is no choice: the kind cards are not shown');
    const requests = await appRequests();
    check(promptSideRequests(requests).length === 0, `no LLM project, prompt project or decision request is made (made: ${listed(promptSideRequests(requests))})`);
    check(requests.some((entry) => decoded(entry).includes('RAG-Engineering')), 'the RAG projects are requested right away');
  });
  await run('RAG-only page whose build fails', async ({ session, check }) => {
    const view = await session.load({ ...ragOnly, [FAIL_RAG_PARAMETER]: '1' });
    const snap = await snapshot(view.app);
    const failures = await view.app.evaluate(() => window.__verifyRagBuildFailures || 0);
    check(failures >= 1, 'the injected failure was reached (the RAG flow is built right away)');
    check(snap.pageAlerts.length === 1 && snap.pageAlerts[0].danger, `the page shows the load error alert (${snap.pageAlerts.map((alert) => alert.text.slice(0, 60)).join(' | ') || 'no alert'})`);
    check(!(snap.rgb && snap.rgb.visible) && !snap.lpb, 'no builder is shown');
    check(session.pageErrors.length === 0, `the failure is caught (uncaught: ${session.pageErrors.join(' | ') || 'none'})`);
    check(view.optionsConfig, 'the options message is answered all the same');
  });

  // ---- RAG chosen, and its load fails ----------------------------------------------
  await run('failing RAG load', async ({ session, check, need }) => {
    const flags = { decision: true, optimization: false, rag: true };
    const stored = storedReport(switchValues(flags));
    const view = await session.load(stored);
    const app = view.app;
    const before = await snapshot(app);
    need(before.kinds.LPB.cards.rag && !before.kinds.LPB.cards.rag.disabled, 'the RAG kind can be chosen');
    const failures = () => app.evaluate(() => window.__verifyRagBuildFailures || 0);

    await app.evaluate(() => {
      window.__verifyFailRagBuild = true;
    });
    await app.click('#LPB-kind-rag');
    need(await eventually(async () => (await failures()) >= 1, 15000), 'choosing RAG starts its build (the injected failure was reached)');
    // Reported, and the cards are usable again with the earlier kind chosen.
    const recovered = await eventually(async () => {
      const cards = (await snapshot(app)).kinds.LPB.cards;
      return cards.llm.checked && !cards.llm.disabled && !cards.rag.checked && !cards.rag.disabled;
    });
    const after = await snapshot(app);
    check(recovered, 'the kind that was chosen before is chosen again, and every card can be clicked');
    // The prompt flow is made inert while RAG loads; a failed load has to give it back.
    const released = await app.evaluate(() => {
      const area = document.getElementById('app-area-prompt');
      return Boolean(area) && area.inert === false && !area.hasAttribute('aria-busy');
    });
    check(released, 'the prompt flow can be used again after the failed attempt (its area is neither inert nor busy)');
    check(after.lpb.visible && sameList(shownSteps(after.lpb), promptStepsLlm(flags)), `the prompt flow stays visible with its steps (got ${shownSteps(after.lpb).join(', ')})`);
    check(!(after.rgb && after.rgb.visible), 'the RAG flow is not shown');
    const newAlerts = after.alertsUnderPromptKinds.filter((text) => !before.alertsUnderPromptKinds.includes(text));
    check(newAlerts.length === 1 && newAlerts[0].length > 0, `an alert under the kind cards says so (${newAlerts.map((text) => text.slice(0, 100)).join(' | ') || 'no new alert on the Setup step'})`);
    check(after.pageAlerts.length === 0, 'no alert replaces the page');
    check(session.pageErrors.length === 0, `the failure is caught (uncaught: ${session.pageErrors.join(' | ') || 'none'})`);

    // Still failing: the second click is a new attempt, not the remembered failure.
    const attemptsBefore = await failures();
    await app.click('#LPB-kind-rag');
    const triedAgain = await eventually(async () => (await failures()) > attemptsBefore, 15000);
    check(triedAgain, 'a second click tries the build again');
    await eventually(async () => {
      const cards = (await snapshot(app)).kinds.LPB.cards;
      return cards.llm.checked && !cards.rag.disabled;
    });

    // The cause is gone: the next click opens the RAG flow.
    await app.evaluate(() => {
      window.__verifyFailRagBuild = false;
    });
    await resetLog();
    await app.click('#LPB-kind-rag');
    const opened = await eventually(async () => {
      const now = await snapshot(app);
      return Boolean(now.rgb && now.rgb.visible);
    }, 20000);
    const final = await snapshot(app);
    check(opened && sameList(shownSteps(final.rgb), RAG_STEPS), `once the cause is gone a click opens the RAG flow with its four steps (got ${shownSteps(final.rgb).join(', ') || 'none'})`);
    check(opened && final.kinds.RGB.cards.rag && final.kinds.RGB.cards.rag.checked, 'with the RAG kind chosen on its Setup card');
    if (opened && final.kinds.RGB.cards.llm) {
      // Back on the prompt Setup card nothing may still report the old failure.
      await app.click('#RGB-kind-llm');
      await eventually(async () => (await snapshot(app)).lpb.visible);
      const back = await snapshot(app);
      check(
        back.lpb.visible && back.alertsUnderPromptKinds.length === before.alertsUnderPromptKinds.length,
        `back in the prompt flow the alert of the failed attempts is gone (${back.alertsUnderPromptKinds.map((text) => text.slice(0, 60)).join(' | ') || 'no alert'})`
      );
    }
    check(session.pageErrors.length === 0, `no uncaught error on the way (uncaught: ${session.pageErrors.join(' | ') || 'none'})`);
  });

  step(allPageErrors.length === 0, `no uncaught page errors in any scenario (got: ${allPageErrors.join(' | ') || 'none'})`);
  await browser.close();
  console.log('\n===== SUMMARY =====');
  console.log(results.join('\n'));
  console.log(`${results.filter((r) => r.startsWith('OK')).length} checks passed, ${results.filter((r) => r.startsWith('FAIL')).length} failed`);
})().catch((e) => {
  console.error('VERIFY FAILED:', e.message);
  process.exit(1);
});
