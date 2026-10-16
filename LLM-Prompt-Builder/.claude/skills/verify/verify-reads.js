/**
 * A read that failed, or read only a part, is not taken for what is stored.
 * Run against a FRESH mock instance.
 *
 * A prompt-test whose tracker could not be read was drawn as one without
 * runs, and Save Experiments then replaced the stored runs with the new one;
 * a RAG setup whose contents could not be listed was drawn with the
 * defaults, and Save documentation wrote them over it; the delete of one
 * optimization run deleted every run when the tracker could not be read; a
 * job source answered with an error became the code of a job definition; a
 * prompt-test whose tracker was the 101st of its contents opened as one
 * without runs, because the first page of 100 was taken for the listing.
 *
 * The mechanisms: getFileContent throws for a file it could not read (no
 * answer, or no success), and every caller of a read of stored content says
 * what a failure does; and every listing reads every page (viyaGetAll in
 * src/api/http-client.ts). Two censuses read the sources:
 *   - CENSUS: every call of getModelContents, getFileContent, getModelDetails,
 *     readOptimizationEntries and readStoredSetup in src/objects, by the
 *     function it stands in, with what a failed read - and a file that is
 *     read and holds nothing this page can read - leads to;
 *   - PAGES: every text in a .ts file under src that holds `limit=` is the
 *     path of a viyaGetAll call, or is named in PAGES with why it is not;
 *     `start=` is written by viyaGetAll alone, and no other function of
 *     src/api reads the `items` of a page.
 *   - STORED_JSON_FILES: every JSON file the page reads is read by
 *     readStoredJson and checked by a function of its own (see
 *     storedJsonCensus below);
 *   - HTML_SOURCES: every text that goes into markup is a literal, goes
 *     through escapeHtml, or is a text of the page (htmlCensus);
 *   - OFFERS: every use of the lists of the models, with what it does with a
 *     model that cannot be used (offersCensus);
 *   - STORED_SHAPES: every field of a row or an entry of a stored file is of
 *     the kind of the type of the sources it mirrors, and the checks refuse
 *     each field of another kind (shapeCensus).
 * A call that is added, moved or gone fails a census until it is named.
 * (verify-outcomes.js has the roads of the delete of an optimization run.)
 *
 * The roads, each on a page of its own, make one read fail - refused, or
 * without an answer - and look at the page and at what it writes:
 *   - a prompt-test whose saved runs cannot be read says so, and Save
 *     Experiments uploads nothing over them; opened again, its runs are back;
 *   - the same for a decision template;
 *   - a tracker that is read and is no list of runs (an HTML page, an object,
 *     rows that are no rows of a run) says that, and that opening it again
 *     does not help; nothing is written;
 *   - the prompts and the model names of a stored run are drawn as text;
 *   - a tracker and an optimization history whose fields hold a value at
 *     every depth, null in every text, number and box, or null in every
 *     field of a row or an entry, are drawn and loaded;
 *   - a prompt-test whose tracker and optimization tracker are on the second
 *     page of its contents shows its runs and its optimization history;
 *   - an optimization history that cannot be read, or holds no list of runs,
 *     says so where the history stands, with no run and no Delete;
 *   - an LLM whose options.json is not one the page can read (a list, an
 *     option that is no object, an enum whose values are no texts or that has
 *     none, a box whose default is 1, a provider key named by a number) is
 *     shown as one that cannot be used, and offered nowhere; a stored run or
 *     an optimization run that used it does not tick its box; one whose
 *     options hold, in fields the page does not read of them, values of any
 *     kind is usable, each option drawn and sent as it is written; a decision
 *     model is not usable when the page cannot read its API_KEY.default, and
 *     usable with any other field;
 *   - the documentation of a prompt-test that cannot be read says so;
 *   - a RAG setup whose contents, rag-setup.json or (never saved) attributes
 *     cannot be read says so and keeps Save setup, Manifest setup and Save
 *     documentation off; opened again, they can be used; a rag-setup.json
 *     that is read and is no setup (also an object that is not one) says
 *     that;
 *   - Save documentation whose read of the stored setup fails writes nothing;
 *   - a manifest whose ingestion job source cannot be read stops before it
 *     writes the job definition.
 *
 * The suite goes on after a check that failed and after a road that stopped.
 * The mock is found at VERIFY_BASE (default http://localhost:4173); the
 * sources at VERIFY_PROJECT (default: the project around this folder), read
 * with the parser of rolldown from its node_modules (`npm ci` first).
 */
const { chromium } = require('playwright');
const fetch = require('./mock-fetch.cjs');
const { trackPagesOf, quiet } = require('./page-waits.cjs');
const fs = require('fs');
const path = require('path');
const { PROJECT, parse, walk, callsInSource, sourceFiles, loadStoredJson } = require('./source-census.cjs');

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
async function waitUntil(fn, label, timeout = 15000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    if (await fn()) return;
    await sleep(100);
  }
  throw new Error('timeout waiting for: ' + label);
}
const clip = (value) => (String(value).length > 140 ? `${String(value).slice(0, 140)}...` : String(value));

const APP_URL =
  `${BASE}/?modelRepositoryID=repo-1&llmProjectID=llm-proj&SCREndpoint=${BASE}/scr` +
  '&enableOptimization=true&computeContext=ctx&optimizeJobProgram=/Public/Jobs/Optimize-Prompt-DSPy&minOptimizeSamples=1' +
  '&enableRag=true&embeddingProjectID=emb-proj&ragComputeContext=rag-ctx';
const lpb = (suffix) => `#app-obj-LPB-${suffix}`;
const rgb = (suffix) => `#app-obj-RGB-${suffix}`;

// ============================================================================
// The census: every read of stored content, and what its failure does
// ============================================================================
const READS = ['getModelContents', 'getFileContent', 'getModelDetails', 'readOptimizationEntries', 'readStoredSetup', 'readStoredJson'];
const CENSUS = {
  'prompt-builder.ts': {
    'loadPromptDocumentation -> getModelDetails': [1, 'null or a throw: the documentation section stays empty, says that it could not be read, and its Save documentation stays off (road below)'],
    'promptBuilderPromptSelectorDropdown.onchange -> getModelContents': [1, 'the runs of the prompt-test are not read: the page says so, and Save Experiments (with what saves first) writes nothing (road below)'],
    'promptBuilderPromptSelectorDropdown.onchange -> readStoredJson': [1, 'as the listing; a tracker that is read and holds no runs this page can read says that, and that opening it again does not help (road below)'],
    'buildPromptBuilder -> getModelContents': [2, 'an LLM or a decision model is offered without its options; nothing stored is written from them'],
    'buildPromptBuilder -> readStoredJson': [2, 'as the listing; an options.json that is not one this page can read shows the model as one that cannot be used, offered nowhere (road below)'],
    'buildPromptBuilder -> getModelDetails': [1, 'a decision model card without the attributes of its registration; nothing is written from them'],
    'ensureLLMCostAttributes -> getModelDetails': [1, 'the LLM has no cost attributes: a manifest copies none, and writes no attribute empty'],
    'promptBuilderSaveExperiments -> getModelContents': [1, 'throws: the save stops with its line, before the upload'],
    'promptBulderCreateBestPromptModel -> getModelContents': [1, 'throws: the manifest stops with its line'],
    'readOptimizationEntries -> getModelContents': [1, "'unread' - never an empty list, which is a model without a tracker"],
    'readOptimizationEntries -> readStoredJson': [1, "'unread'; a tracker that is read and holds no runs this page can read is 'unusable'"],
    'readOptimizationEntry -> readOptimizationEntries': [1, "'unread' or 'unusable': no entry of the job is drawn, and a job that completed says that its result could not be read; nothing is written"],
    'refreshOptimizationHistory -> readOptimizationEntries': [1, "'unread' or 'unusable': where the history stands, the page says that it could not be read, or holds no runs this page can read (road below); nothing is written from it"],
    'deleteOptimizationRun -> readOptimizationEntries': [1, "'unread' or 'unusable': the delete writes nothing and says what failed (verify-outcomes.js)"],
    'deleteOptimizationRun -> getModelContents': [1, 'throws: the delete stops before the tracker is written'],
  },
  'rag-builder.ts': {
    'loadPromptContract -> getModelContents': [1, 'the prompt has no contract: the enrichment says so, and the validation refuses a save that uses it'],
    'loadPromptContract -> getFileContent': [1, 'as the listing'],
    'saveDocumentation -> readStoredSetup': [1, 'throws: nothing is written, and the banner says the save failed - and, for a rag-setup.json that is read and is no setup, says that (road below)'],
    'removeStaleDocumentation -> getModelContents': [1, 'throws: the obsolete documentation.md is not deleted; nothing else'],
    'readStoredSetup -> getModelContents': [1, 'throws to its caller'],
    'readStoredSetup -> readStoredJson': [1, 'throws to its caller; a file that is read and is no setup throws SetupUnusable'],
    'loadSetup -> getModelContents': [1, 'the setup is not read: the page says so, and Save setup, Manifest setup and Save documentation stay off (road below)'],
    'loadSetup -> readStoredJson': [1, 'as the listing; a rag-setup.json that is read and is no setup says that (road below)'],
    'loadSetup -> getModelDetails': [1, 'as the listing, for a setup never saved (road below)'],
    'generateIngestionJob -> getFileContent': [1, 'throws: the manifest stops before the job definition is written (road below)'],
    'registerRetrievalModel -> getFileContent': [1, 'throws: the manifest stops before the score code is written'],
    'runRetrievalTest -> getFileContent': [1, 'throws: the test says it could not be run, and no job definition is written'],
  },
};

/**
 * Every text in the sources that names a page size (`limit=`) and is not the
 * path of a viyaGetAll call, by "<file> <function>", with the count and why
 * it reads no more than one page.
 */
const PAGES = {
  'api/jobexec-api.ts primeJobExecutionSession': [1, 'the GET that gives the browser its session with Job Execution: its answer is not read'],
  'api/jobexec-api.ts primeComputeService': [1, 'the GET that gives the browser its session with the compute service: its answer is not read'],
  'api/jobexec-api.ts getJobProgressMessages': [2, 'the two logs of a job: the first page is read here, and withLaterPages reads every page after it through viyaGetAll'],
};
// The functions of src/api that read the `items` of an answer themselves.
const ITEM_READERS = {
  'api/http-client.ts viyaGetAll': 'the one reader of the pages of a collection',
  'api/jobexec-api.ts withLaterPages': 'the pages of a log, through viyaGetAll',
  'api/jobexec-api.ts readLogLines': 'the lines of a log that was read whole',
  'api/dataflows-api.ts walk': 'the items of the form of a custom step, which is no collection',
};

function pagesCensus() {
  let files;
  try {
    files = sourceFiles();
  } catch (error) {
    step(false, `every listing reads every page (the sources could not be read: ${error.message})`);
    return;
  }
  const outside = {};
  const starts = [];
  const itemReads = {};
  for (const [file, text] of Object.entries(files)) {
    let parsed;
    try {
      parsed = parse(file, text);
    } catch (error) {
      step(false, `${file}: the sources are known to this suite (${error.message}; set VERIFY_PROJECT, run npm ci)`);
      continue;
    }
    walk(parsed.program, parsed.source, (node, name, parents) => {
      const where = `${file} ${name || '(module)'}`;
      if (node.type === 'MemberExpression' && !node.computed && node.property.name === 'items' && file.startsWith('api/')) {
        itemReads[where] = (itemReads[where] || 0) + 1;
      }
      const textOf =
        node.type === 'Literal' && typeof node.value === 'string'
          ? node.value
          : node.type === 'TemplateLiteral'
            ? node.quasis.map((quasi) => quasi.value.cooked).join('${}')
            : null;
      if (textOf === null) return;
      if (/(^|[?&}])start=/.test(textOf) && where !== 'api/http-client.ts viyaGetAll') starts.push(where);
      if (!/[?&]limit=/.test(textOf)) return;
      const call = [...parents].reverse().find((parent) => parent.type === 'CallExpression');
      const first = call && call.arguments[0];
      const inGetAll = call && call.callee.type === 'Identifier' && call.callee.name === 'viyaGetAll' && first && node.start >= first.start && node.end <= first.end;
      if (!inGetAll) outside[where] = (outside[where] || 0) + 1;
    });
  }
  const unknown = Object.keys(outside).filter((where) => !PAGES[where]);
  const gone = Object.keys(PAGES).filter((where) => !outside[where]);
  const miscounted = Object.keys(PAGES).filter((where) => outside[where] && outside[where] !== PAGES[where][0]).map((where) => `${where}: ${outside[where]}, not ${PAGES[where][0]}`);
  step(
    unknown.length === 0 && gone.length === 0 && miscounted.length === 0,
    `every listing of the page reads every page: each text of the ${Object.keys(files).length} source files that names a page size is the path of a viyaGetAll call, or named here with why it reads one page (not named here: ${unknown.join('; ') || 'none'}; named but not found: ${gone.join('; ') || 'none'}; counted otherwise: ${miscounted.join('; ') || 'none'})`
  );
  const strangers = Object.keys(itemReads).filter((where) => !ITEM_READERS[where]);
  step(
    starts.length === 0 && strangers.length === 0,
    `the pages of a collection are read by viyaGetAll alone: no other text asks for a page by its start, and no other function of src/api reads the items of an answer (start= elsewhere: ${starts.join('; ') || 'none'}; items read in: ${strangers.join('; ') || 'none'})`
  );
}

function census() {
  pagesCensus();
  for (const [file, known] of Object.entries(CENSUS)) {
    let counts = null;
    let problem = '';
    try {
      counts = callsInSource(file, fs.readFileSync(path.join(PROJECT, 'src', 'objects', file), 'utf8'), READS);
    } catch (error) {
      problem = error.message;
    }
    if (!counts) {
      step(false, `${file}: the reads of stored content are known to this suite (${problem}; set VERIFY_PROJECT, run npm ci)`);
      continue;
    }
    const unknown = Object.keys(counts).filter((key) => !known[key]);
    const gone = Object.keys(known).filter((key) => !counts[key]);
    const miscounted = Object.keys(known).filter((key) => counts[key] && counts[key] !== known[key][0]).map((key) => `${key}: ${counts[key]}, not ${known[key][0]}`);
    step(
      unknown.length === 0 && gone.length === 0 && miscounted.length === 0,
      `${file}: every read of stored content is named here with what its failure does (${Object.keys(counts).length} calls; not named here: ${unknown.join('; ') || 'none'}; named but not found: ${gone.join('; ') || 'none'}; counted otherwise: ${miscounted.join('; ') || 'none'})`
    );
  }
  // getFileContent hands back no failed read: the one mechanism the census
  // leans on, read from its source.
  const api = fs.readFileSync(path.join(PROJECT, 'src', 'api', 'files-api.ts'), 'utf8');
  step(
    /if \(!response\.ok\) throw /.test(api) && !/new Response\(/.test(api),
    'getFileContent throws for a file it could not read, and makes up no answer of its own'
  );
  storedJsonCensus();
}

// ============================================================================
// The stored JSON files: each is checked for what it is
// ============================================================================
// A file that is JSON is not yet the file the page takes it for: a list of
// objects without the fields of a run was drawn as a run and replaced by Save
// Experiments, an optimization tracker of such entries drew a run whose
// Delete removed the file. Every JSON file the page reads from SAS Model
// Manager is read by readStoredJson (src/api/stored-json.ts) and checked by
// the function of its name in STORED_JSON_FILES. The census reads, from every
// .ts file under src: the names in STORED_JSON_FILES, each with a road below
// (STORED_ROADS) that answers it with JSON that is not that file; every name
// of a .json file the sources hold, which is one of them or named in
// NOT_READ; every call of getFileContent, which is readStoredJson's or reads
// text; and every parse of JSON outside readStoredJson, named in PARSES with
// what it parses - none of it a stored file.
const STORED_ROADS = {
  'Prompt-Experiment-Tracker.json': 'a tracker that is answered with',
  'Prompt-Optimization-Tracker.json': 'an optimization history whose tracker is',
  'rag-setup.json': 'a rag-setup.json that is',
  'options.json': 'an options.json that is',
};
const NOT_READ = {
  'inputVar.json': 'written by a manifest, never read',
  'outputVar.json': 'written by a manifest, never read',
  'requirements.json': 'written by a manifest, and looked for by its name only, to delete one an earlier manifest left',
};
const PARSES = {
  'api/stored-json.ts readStoredJson': [1, 'a stored JSON file, checked by the function of its name'],
  'api/jobexec-api.ts withLaterPages': [1, 'the first page of the log of a job'],
  'api/jobexec-api.ts readLogLines': [2, 'a log of a job, read whole, and its lines'],
  'api/judge-api.ts parseVerdict': [1, 'the answer of a judge model'],
  'api/judge-api.ts chairmanBreakTie': [1, 'the answer of the chairman of a council'],
  'api/models-api.ts patchModel': [1, 'a copy of the model that was read, to change it'],
  'api/dataflows-api.ts readStepSpec': [1, 'the form of a custom step, as the data flows service answers it'],
  'objects/prompt-decision.ts questionsFromJson': [1, 'the questions of a decision run, a text of the tracker'],
  'objects/prompt-decision.ts parseAnswers': [1, 'the answers of a decision model'],
  'objects/rag-retrieval-log.ts parseRetrievalLog': [1, 'the rows of the log of a retrieval test'],
  'va/ddc.ts handle': [1, 'a message of SAS Visual Analytics'],
};
const storedRoadsRun = new Set();
let storedFileNames = [];

function storedJsonCensus() {
  let files;
  try {
    files = sourceFiles();
  } catch (error) {
    step(false, `every stored JSON file is checked for what it is (the sources could not be read: ${error.message})`);
    return;
  }
  const names = [];
  const literals = {};
  const fileReads = [];
  const parses = {};
  for (const [file, text] of Object.entries(files)) {
    let parsed;
    try {
      parsed = parse(file, text);
    } catch (error) {
      step(false, `${file}: the sources are known to this suite (${error.message}; set VERIFY_PROJECT, run npm ci)`);
      continue;
    }
    walk(parsed.program, parsed.source, (node, name) => {
      const where = `${file} ${name || '(module)'}`;
      if (file === 'api/stored-json.ts' && node.type === 'VariableDeclarator' && node.id.name === 'STORED_JSON_FILES') {
        let object = node.init;
        while (object && object.type !== 'ObjectExpression') object = object.expression;
        for (const property of (object && object.properties) || []) names.push(property.key.value ?? property.key.name);
      }
      const textOf =
        node.type === 'Literal' && typeof node.value === 'string'
          ? node.value
          : node.type === 'TemplateLiteral' && node.expressions.length === 0
            ? node.quasis[0].value.cooked
            : null;
      if (textOf !== null && /^[^\s/]+\.json$/.test(textOf)) literals[textOf] = (literals[textOf] || 0) + 1;
      if (node.type === 'CallExpression' && node.callee.type === 'Identifier' && node.callee.name === 'getFileContent') {
        const kind = node.arguments[1] && node.arguments[1].type === 'Literal' ? node.arguments[1].value : null;
        fileReads.push({ where, kind });
      }
      const callee = node.type === 'CallExpression' && node.callee.type === 'MemberExpression' && !node.callee.computed ? node.callee : null;
      const isParse = callee && callee.property.name === 'parse' && callee.object.type === 'Identifier' && callee.object.name === 'JSON';
      const isJson = callee && callee.property.name === 'json' && node.arguments.length === 0 && !file.startsWith('api/');
      if (isParse || isJson) parses[where] = (parses[where] || 0) + 1;
    });
  }
  storedFileNames = names;
  const withoutRoad = names.filter((name) => !STORED_ROADS[name]);
  const roadWithout = Object.keys(STORED_ROADS).filter((name) => !names.includes(name));
  step(
    names.length > 0 && withoutRoad.length === 0 && roadWithout.length === 0,
    `every JSON file the page reads is checked for what it is by a function of its own, and has a road of a file that is JSON and not that file (read from STORED_JSON_FILES: ${names.join(', ') || 'none'}; without a road: ${withoutRoad.join(', ') || 'none'}; a road for no such file: ${roadWithout.join(', ') || 'none'})`
  );
  const strangers = Object.keys(literals).filter((name) => !names.includes(name) && !NOT_READ[name]);
  step(
    strangers.length === 0,
    `every name of a .json file in the ${Object.keys(files).length} source files is a file the page reads through that check, or one it never reads as JSON (names: ${Object.keys(literals).join(', ')}; neither: ${strangers.join(', ') || 'none'})`
  );
  const otherReads = fileReads.filter((read) => read.where !== 'api/stored-json.ts readStoredJson' && read.kind !== 'text/plain');
  step(
    fileReads.some((read) => read.where === 'api/stored-json.ts readStoredJson') && otherReads.length === 0,
    `every read of a stored file is readStoredJson's or reads text (reads: ${fileReads.length}; others: ${otherReads.map((read) => `${read.where} (${read.kind})`).join('; ') || 'none'})`
  );
  const unknown = Object.keys(parses).filter((where) => !PARSES[where]);
  const gone = Object.keys(PARSES).filter((where) => !parses[where]);
  const miscounted = Object.keys(PARSES).filter((where) => parses[where] && parses[where] !== PARSES[where][0]).map((where) => `${where}: ${parses[where]}, not ${PARSES[where][0]}`);
  step(
    unknown.length === 0 && gone.length === 0 && miscounted.length === 0,
    `every parse of JSON in the sources (JSON.parse, and .json() outside src/api) is named with what it parses, and only readStoredJson parses a stored file (not named here: ${unknown.join('; ') || 'none'}; named but not found: ${gone.join('; ') || 'none'}; counted otherwise: ${miscounted.join('; ') || 'none'})`
  );
}

// --------------------------------------------------------------------------
// Texts that go into markup (round 24): the prompts of a stored run and the
// names of its models were drawn with innerHTML, so a stored tracker ran
// script in the page. The census reads, from every .ts file under src, every
// value given to innerHTML or outerHTML (= and +=) and to insertAdjacentHTML:
// each text put into it is a literal, goes through escapeHtml, or is named
// in HTML_SOURCES with what it is - none of them read from SAS Viya or typed
// by a person.
// --------------------------------------------------------------------------
const HTML_SOURCES = {
  'objects/prompt-builder.ts promptBuilderInterfaceText': 'a text of the locale files of the page',
  'objects/prompt-builder.ts TRACKER_ICON_PATHS': 'the path of an icon, a constant of the page',
  'objects/prompt-builder.ts iconPath': 'the path of an icon of the legend, one of TRACKER_ICON_PATHS',
  'objects/prompt-builder.ts VIYA': 'the address of SAS Viya the page is configured with',
  'objects/prompt-builder.ts promptExperimentRunModel': 'the id of the open prompt-test, as SAS Model Manager gave it',
  'objects/prompt-builder.ts decision': 'a choice between two texts of the locale files',
  'objects/prompt-builder.ts runIsDecision': 'a choice between two texts of the locale files',
  'objects/prompt-builder.ts confidenceBadge': 'markup of a text of the locale files and the confidence of a judge, escaped',
  'objects/prompt-builder.ts JUDGE_BUTTON_SPINNER': 'the markup of a spinner, a constant of the page',
  'objects/prompt-builder.ts guideHtml': 'a guide of the Optimize step, a text of the locale files (makeOptimizeGuide)',
  'objects/prompt-decision.ts Math': 'a number the page computes',
  'objects/prompt-decision.ts count': 'a number the page counts',
  'objects/prompt-decision.ts percent': 'a number the page computes, as a percentage',
  'ui/create-modal.ts text': 'a text of a create dialog, from the locale files',
  'ui/external-link.ts label': 'the markup of the link itself, put back after the note that it was copied',
  'ui/markdown.ts DOMPurify': 'the markdown of a response made markup, sanitized by DOMPurify',
};
function htmlCensus() {
  const files = sourceFiles();
  const unnamed = new Set();
  let sinks = 0;
  for (const [file, text] of Object.entries(files)) {
    const { program, source } = parse(file, text);
    const values = [];
    walk(program, source, (node) => {
      if (node.type === 'AssignmentExpression' && node.left.type === 'MemberExpression' && ['innerHTML', 'outerHTML'].includes(node.left.property.name)) values.push(node.right);
      if (node.type === 'CallExpression' && node.callee.type === 'MemberExpression' && node.callee.property.name === 'insertAdjacentHTML') values.push(node.arguments[1]);
    });
    sinks += values.length;
    // The texts in a value: the expressions of a template, both sides of a
    // choice or of a +, what String() is given, what each item of a list
    // joined into one text is made of, and a value that is none of these.
    const texts = (node) => {
      if (!node || node.type === 'Literal') return [];
      if (node.type === 'ParenthesizedExpression') return texts(node.expression);
      if (node.type === 'TemplateLiteral') return node.expressions.flatMap(texts);
      if (node.type === 'ConditionalExpression') return [...texts(node.consequent), ...texts(node.alternate)];
      if (node.type === 'BinaryExpression' && node.operator === '+') return [...texts(node.left), ...texts(node.right)];
      if (node.type === 'CallExpression' && node.callee.type === 'Identifier' && node.callee.name === 'escapeHtml') return [];
      if (node.type === 'CallExpression' && node.callee.type === 'Identifier' && node.callee.name === 'String') return texts(node.arguments[0]);
      const callee = node.type === 'CallExpression' && node.callee.type === 'MemberExpression' ? node.callee : null;
      if (callee && callee.property.name === 'join' && callee.object.type === 'CallExpression' && callee.object.callee.type === 'MemberExpression' && callee.object.callee.property.name === 'map') {
        const made = callee.object.arguments[0];
        if (made && made.type === 'ArrowFunctionExpression' && made.body.type !== 'BlockStatement') return texts(made.body);
      }
      return [node];
    };
    for (const value of values) {
      for (const node of texts(value)) {
        let root = node;
        while (['MemberExpression', 'ChainExpression', 'TSAsExpression', 'CallExpression', 'LogicalExpression'].includes(root.type)) {
          root = root.type === 'CallExpression' ? root.callee : root.type === 'LogicalExpression' ? root.left : root.object || root.expression;
        }
        if (!(root.type === 'Identifier' && HTML_SOURCES[`${file} ${root.name}`])) unnamed.add(`${file}: ${source(node)}`);
      }
    }
  }
  step(
    sinks > 0 && unnamed.size === 0,
    `every text that goes into markup in the ${Object.keys(files).length} source files is a literal, goes through escapeHtml, or is a text of the page named here (values given to innerHTML, outerHTML and insertAdjacentHTML: ${sinks}; texts not named: ${[...unnamed].join('; ') || 'none'})`
  );
}

// --------------------------------------------------------------------------
// A model that cannot be used is offered nowhere (round 24): the chairman of
// a council listed an LLM whose options.json held no options, and opening a
// run that used it ticked its disabled box, which Run Experiments then
// called. The census reads every use of the lists of the models of the page
// in every .ts file under src, by the function it stands in, and OFFERS names
// each with what it does with a model that cannot be used.
// --------------------------------------------------------------------------
const OFFERS = {
  'promptBuilderAvailableLLMs buildPromptBuilder .map': [2, 'reads the options.json of each LLM, and the provider keys none of them holds'],
  'promptBuilderAvailableLLMs buildPromptBuilder SpreadElement': [1, 'the attributes of every LLM by its name, for the costs of a run; offers nothing'],
  'promptBuilderAvailableLLMs buildPromptBuilder CallExpression': [1, 'generateModelSelection: the box of an LLM that cannot be used is off, with its note'],
  'promptBuilderAvailableLLMs buildPromptBuilder .forEach': [5, 'the judge list, the council members, the chairman list, the target and the judge lists of the optimization: each leaves out an LLM that cannot be used'],
  'promptBuilderAvailableLLMs buildPromptBuilder .some': [2, 'the judge an Option names, set on a list that holds no LLM that cannot be used'],
  'promptBuilderAvailableLLMs updateRunExperimentsButtonState .some': [1, 'whether a box is ticked; the box of an LLM that cannot be used is off and ticked by nothing'],
  'promptBuilderAvailableLLMs judgeUseModelsButton.onclick .forEach': [1, 'ticks council members by the ticked boxes; no member can be an LLM that cannot be used'],
  'promptBuilderAvailableLLMs promptBuilderJudgeCouncilToggle:change .forEach': [1, 'ticks the council member of the chosen judge; no member can be an LLM that cannot be used'],
  'promptBuilderAvailableLLMs promptBuilderRunExperiment .forEach': [1, 'calls the LLMs whose box is ticked'],
  'promptBuilderAvailableLLMs runOneJudge .find': [1, 'the options of a judge chosen from its list'],
  'promptBuilderAvailableLLMs promptBuilderJudgeRun .find': [1, 'the options of the chairman chosen from its list'],
  'promptBuilderAvailableLLMs loadExperimentRun .some': [3, 'the judges and the chairman of a run that are put back leave out an LLM that cannot be used; the models of a run no longer listed'],
  'promptBuilderAvailableLLMs loadExperimentRun .forEach': [2, 'ticks the council members of a run (no LLM that cannot be used is one) and the boxes of its LLMs, but not a box that is off'],
  'promptBuilderAvailableLLMs promptBulderCreateBestPromptModel .map': [1, 'the names of tags a manifest removes'],
  'promptBuilderAvailableLLMs loadOptimizationAsExperiment .findIndex': [1, 'ticks the box of the LLM an optimization ran for, but not a box that is off'],
  'promptBuilderAvailableLLMs promptBuilderRunOptimization .find': [1, 'the options of the target chosen from its list'],
  'promptBuilderAvailableLLMs openCompareTargetsModal .forEach': [1, 'the candidates of Compare targets leave out an LLM that cannot be used'],
  'promptBuilderAvailableDecisionModels buildPromptBuilder AssignmentExpression': [1, 'the decision models of the page'],
  'promptBuilderAvailableDecisionModels buildPromptBuilder .map': [1, 'reads the options.json of each decision model'],
  'promptBuilderAvailableDecisionModels buildPromptBuilder ForOfStatement': [1, 'the attributes by name, and the provider keys none of them holds'],
  'promptBuilderAvailableDecisionModels buildPromptBuilder .length': [1, 'whether there is any'],
  'promptBuilderAvailableDecisionModels buildPromptBuilder .forEach': [1, 'the cards: the box of a decision model that cannot be used is off, with its note'],
  'promptBuilderAvailableDecisionModels selectedDecisionModels .filter': [1, 'the decision models whose box is ticked'],
  'promptBuilderAvailableDecisionModels loadExperimentRun .forEach': [1, 'ticks the boxes of the decision models of a run, but not a box that is off'],
  'promptBuilderAvailableDecisionModels loadExperimentRun .some': [1, 'the models of a run no longer listed'],
  'promptBuilderAvailableDecisionModels promptBuilderCreateDecisionModel .map': [1, 'the names of tags a manifest removes'],
};
function offersCensus() {
  const files = sourceFiles();
  const uses = {};
  for (const [file, text] of Object.entries(files)) {
    const { program, source } = parse(file, text);
    walk(program, source, (node, name, parents) => {
      if (node.type !== 'Identifier' || !['promptBuilderAvailableLLMs', 'promptBuilderAvailableDecisionModels'].includes(node.name)) return;
      const parent = parents[parents.length - 1];
      if (parent.type === 'VariableDeclarator' && parent.id === node) return;
      const use = parent.type === 'MemberExpression' && parent.object === node ? `.${source(parent.property)}` : parent.type;
      const key = `${node.name} ${name || '(module)'} ${use}`;
      uses[key] = (uses[key] || 0) + 1;
    });
  }
  const unknown = Object.keys(uses).filter((key) => !OFFERS[key]);
  const gone = Object.keys(OFFERS).filter((key) => !uses[key]);
  const miscounted = Object.keys(OFFERS).filter((key) => uses[key] && uses[key] !== OFFERS[key][0]).map((key) => `${key}: ${uses[key]}, not ${OFFERS[key][0]}`);
  step(
    Object.keys(uses).length > 0 && unknown.length === 0 && gone.length === 0 && miscounted.length === 0,
    `every use of the lists of the models in the ${Object.keys(files).length} source files is named with what it does with a model that cannot be used (uses: ${Object.values(uses).reduce((a, b) => a + b, 0)}; not named here: ${unknown.join('; ') || 'none'}; named but not found: ${gone.join('; ') || 'none'}; counted otherwise: ${miscounted.join('; ') || 'none'})`
  );
}

// --------------------------------------------------------------------------
// The fields of a stored file (round 24): a tracker whose rows passed the
// check of their runIds and prompts was drawn with a response that was an
// object ("[object Object]"), threw while a manifest configuration that was
// no configuration was put back, or threw while an optimization run whose
// comparison or evaluations were no lists was drawn. src/api/stored-json.ts
// checks every field of a stored row or entry against its shape (STORED_SHAPES:
// PET_ROW, OPTIMIZATION_ENTRY, RAG_SETUP - round 25: a rag-setup.json whose
// documentation held an object was drawn as "[object Object]" and written so
// by Save setup, and a policy flag "no" went into pipeline.yaml as true -,
// MODEL_OPTION: an options.json whose enum values were no texts
// stopped the build of the whole Prompt Builder). A field the type gives no
// kind (`unknown`: the default of an option) is in no shape: the check of an
// options.json reads it by the rule of the option's kind (isModelOptions),
// and the rules below name each kind; so are the values of an enum. The
// census probes the shape of every name of STORED_SHAPES. It reads the types of
// the sources each shape mirrors (`of`: an interface, or a field of one that
// is an object of its own) and compares them field for field - names and
// kinds -, then loads the checks of stored-json.ts and sends each of them, for
// every field at every depth, a value of another kind (refused), null and no
// value (taken, unless the field is required), and the files the rules of a
// run name (a run of its header alone, a model named like each key the page
// reads as a field of a run or __proto__ - refused - and like each other field every object has -
// taken -, a model twice in a run, two runs of one optimizationId). The
// covered sources: every .ts file under src (the types),
// and src/api/stored-json.ts (the checks).
// --------------------------------------------------------------------------
let storedJson = null;

// The rules of an option of the options.json of an LLM, each kind of option
// with a file the page takes and files it refuses: what the page draws as
// written is taken, what it could draw only as something else is refused.
const OPTION_RULES = (numberFields) => {
  const one = (key, option) => ({ [key]: option });
  const rules = [];
  const add = (what, key, option, taken) => rules.push([`${what} (${key}: ${JSON.stringify(option)})`, one(key, option), taken]);
  for (const type of [undefined, 'string']) {
    const typed = (fields) => (type ? { type, ...fields } : fields);
    const kind = type ? 'a string option' : 'an option of no type';
    for (const value of [0, 0.5, 'x', '', true, false]) add(`${kind} whose default is a text, a number or a boolean`, 'my_option', typed({ default: value }), true);
    for (const value of [null, ['x'], { x: 1 }]) add(`${kind} whose default is ${value === null ? 'null' : Array.isArray(value) ? 'a list' : 'an object'}`, 'my_option', typed({ default: value }), false);
    add(`${kind} without its default`, 'my_option', typed({ label: 'x' }), false);
    add(`${kind} with values that are no texts, which are not read`, 'my_option', typed({ default: 'x', values: [1, 2] }), true);
    add(`${kind} whose label is no text`, 'my_option', typed({ default: 'x', label: 5 }), false);
    add(`${kind} whose description is no text`, 'my_option', typed({ default: 'x', description: { a: 1 } }), false);
  }
  add('an option whose type is no text', 'my_option', { type: 5, default: 'x' }, false);
  add('an option of a type the page draws by its default', 'my_option', { type: 'int', default: 3 }, true);
  add('an enum whose default is one of its texts', 'my_option', { type: 'enum', default: 'a', values: ['a', 'b'] }, true);
  add('an enum whose default is none of its values', 'my_option', { type: 'enum', default: 'c', values: ['a', 'b'] }, false);
  add('an enum without its values', 'my_option', { type: 'enum', default: 'a' }, false);
  add('an enum with no values', 'my_option', { type: 'enum', default: 'a', values: [] }, false);
  add('an enum whose values are no texts', 'my_option', { type: 'enum', default: 1, values: [1, 2] }, false);
  add('an enum one of whose values is no text', 'my_option', { type: 'enum', default: 'a', values: ['a', 1] }, false);
  add('an enum whose values are a text', 'my_option', { type: 'enum', default: 'a', values: 'a' }, false);
  for (const value of [true, false, 'true', 'false']) add('a bool whose default is true or false', 'my_option', { type: 'bool', default: value }, true);
  for (const value of [1, 0, 'yes', '', null]) add('a bool whose default is not true or false', 'my_option', { type: 'bool', default: value }, false);
  add('a bool whose label is no text', 'my_option', { type: 'bool', default: true, label: 5 }, false);
  for (const key of numberFields) {
    add('a number of a field of the page, whose label, description and values are not read', key, { default: 0.5, label: 5, description: { a: 1 }, values: [1] }, true);
    add('an option of a field of the page that is no number, drawn as any other', key, { default: 'low', label: 'x' }, true);
    add('an option of a field of the page that is no number, whose label is no text', key, { default: 'low', label: 5 }, false);
    add('an enum of a field of the page', key, { type: 'enum', default: 'a', values: ['a'] }, true);
    add('an enum of a field of the page without its values', key, { type: 'enum', default: 0.5 }, false);
    add('a bool of a field of the page whose default is 1', key, { type: 'bool', default: 1 }, false);
  }
  add('API_KEY whose default is a text', 'API_KEY', { default: 'OpenAI', label: 5, values: [1], type: 7 }, true);
  for (const value of ['', 5, true, ['OpenAI'], { name: 'OpenAI' }, null]) add('API_KEY whose default is no text, or an empty one', 'API_KEY', { default: value }, false);
  add('API_KEY without its default', 'API_KEY', { label: 'x' }, false);
  add('API_KEY that is a text', 'API_KEY', 'OpenAI', false);
  rules.push(['a list of options', [{ default: 0.5 }], false]);
  rules.push(['an option that is no object', { temperature: 0.7 }, false]);
  rules.push(['an option that is null', { temperature: null }, false]);
  rules.push(['no options at all', {}, true]);
  return rules;
};
// The file of a decision model: the page reads its API_KEY.default alone.
const DECISION_OPTION_RULES = [
  ['a file without API_KEY', {}, true],
  ['API_KEY whose default is a text, and other options of any kind', { API_KEY: { default: 'OpenAI', label: 7 }, mode: { type: 'enum', default: 'a', values: [1, 2] }, x: 5, y: null, z: [1] }, true],
  ['API_KEY that is null', { API_KEY: null }, true],
  ['API_KEY whose default is a number', { API_KEY: { default: 5 } }, false],
  ['API_KEY whose default is an empty text', { API_KEY: { default: '' } }, false],
  ['API_KEY whose default is a list', { API_KEY: { default: ['OpenAI'] } }, false],
  ['API_KEY without its default', { API_KEY: { label: 'x' } }, false],
  ['API_KEY that is a text', { API_KEY: 'OpenAI' }, false],
  ['a list', [{ API_KEY: { default: 'OpenAI' } }], false],
  ['a text', 'API_KEY', false],
];

const kindOfShape = (shape) =>
  typeof shape === 'string' ? shape : shape.list ? `list(${kindOfShape(shape.list)})` : shape.map ? `map(${kindOfShape(shape.map)})` : `object(${shape.of})`;

async function shapeCensus() {
  try {
    storedJson = await loadStoredJson();
    await shapeChecks(storedJson);
  } catch (error) {
    step(false, `the shapes and checks of src/api/stored-json.ts can be read (${error.message}; run npm ci)`);
  }
}

async function shapeChecks({ STORED_SHAPES, TRACKER_META_KEYS, NUMBER_FIELD_OPTIONS, fits, isExperimentTracker, isOptimizationTracker, isRagSetup, isModelOptions, isDecisionModelOptions }) {
  if (!STORED_SHAPES) throw new Error('it exports no STORED_SHAPES');
  // The interfaces and type aliases of the sources, by name.
  const types = {};
  const twice = new Set();
  for (const [file, text] of Object.entries(sourceFiles())) {
    const { program } = parse(file, text);
    walk(program, (node) => text.slice(node.start, node.end), (node) => {
      if ((node.type === 'TSInterfaceDeclaration' || node.type === 'TSTypeAliasDeclaration') && node.id) {
        if (types[node.id.name]) twice.add(node.id.name);
        types[node.id.name] = node;
      }
    });
  }
  const membersOf = (node) => (node.type === 'TSInterfaceDeclaration' ? node.body.body : node.type === 'TSTypeLiteral' ? node.members : null);
  const fieldsOf = (node) => {
    const fields = {};
    for (const member of membersOf(node) || []) {
      if (member.type === 'TSPropertySignature' && member.key) fields[member.key.name ?? member.key.value] = member.typeAnnotation.typeAnnotation;
    }
    return fields;
  };
  // Without null and undefined, and the one type left of a union of one.
  const bare = (type) => {
    if (type.type !== 'TSUnionType') return type;
    const kept = type.types.filter((member) => member.type !== 'TSNullKeyword' && member.type !== 'TSUndefinedKeyword');
    return kept.length === 1 ? bare(kept[0]) : { ...type, types: kept };
  };
  // The object type a name stands for: 'Interface' or 'Interface.field'.
  const resolveOf = (of) => {
    const [name, ...path] = of.split('.');
    let node = types[name];
    for (const field of path) {
      if (!node) return null;
      let type = bare(fieldsOf(node)[field] || { type: 'none' });
      while (type.type === 'TSArrayType' || (type.type === 'TSTypeReference' && type.typeName.name === 'Array')) {
        type = bare(type.type === 'TSArrayType' ? type.elementType : type.typeArguments.params[0]);
      }
      node = type.type === 'TSTypeLiteral' ? type : type.type === 'TSTypeReference' ? types[type.typeName.name] : null;
    }
    return node && membersOf(node) ? node : null;
  };
  // The kind of a type of the sources, written as kindOfShape writes a shape.
  const kindOfType = (type, at) => {
    const node = bare(type);
    if (node.type === 'TSStringKeyword' || (node.type === 'TSLiteralType' && typeof node.literal.value === 'string')) return 'string';
    if (node.type === 'TSNumberKeyword' || (node.type === 'TSLiteralType' && typeof node.literal.value === 'number')) return 'number';
    if (node.type === 'TSBooleanKeyword') return 'boolean';
    if (node.type === 'TSUnionType') {
      const kinds = [...new Set(node.types.map((member) => kindOfType(member, at)))].sort().join('|');
      return kinds === 'boolean|number' ? 'flag' : kinds;
    }
    if (node.type === 'TSArrayType') return `list(${kindOfType(node.elementType, at)})`;
    if (node.type === 'TSTypeLiteral') return `object(${at})`;
    if (node.type === 'TSIndexedAccessType') return `object(${node.objectType.typeName.name}.${node.indexType.literal.value})`;
    if (node.type === 'TSTypeReference') {
      const name = node.typeName.name;
      const params = (node.typeArguments && node.typeArguments.params) || [];
      if (name === 'Array') return `list(${kindOfType(params[0], at)})`;
      if (name === 'Record') return `map(${kindOfType(params[1], at)})`;
      const declared = types[name];
      if (declared && declared.type === 'TSTypeAliasDeclaration') return kindOfType(declared.typeAnnotation, at);
      if (declared) return `object(${name})`;
    }
    return `unknown ${node.type}`;
  };
  // Every object shape below the shapes of STORED_SHAPES, each once.
  const objects = new Map();
  const collect = (shape) => {
    if (typeof shape === 'string') return;
    if (shape.list) return collect(shape.list);
    if (shape.map) return collect(shape.map);
    if (objects.has(shape.of)) return;
    objects.set(shape.of, shape);
    Object.values(shape.fields).forEach(collect);
  };
  Object.values(STORED_SHAPES).forEach(collect);
  const differences = [];
  for (const [of, shape] of objects) {
    if (twice.has(of.split('.')[0])) differences.push(`${of}: declared more than once in the sources`);
    const node = resolveOf(of);
    if (!node) {
      differences.push(`${of}: no such type in the sources`);
      continue;
    }
    const declared = fieldsOf(node);
    for (const name of Object.keys(declared)) {
      if (!shape.fields[name] && bare(declared[name]).type !== 'TSUnknownKeyword') differences.push(`${of}.${name}: not in the shape`);
    }
    for (const [name, field] of Object.entries(shape.fields)) {
      if (!declared[name]) {
        differences.push(`${of}.${name}: not in the type`);
        continue;
      }
      const want = kindOfType(declared[name], `${of}.${name}`);
      if (want !== kindOfShape(field)) differences.push(`${of}.${name}: ${kindOfShape(field)}, the type says ${want}`);
    }
  }
  step(
    objects.size > 0 && differences.length === 0,
    `every shape of a stored file holds the fields of the type it mirrors, each of its kind (objects: ${[...objects.keys()].join(', ')}; differences: ${differences.join('; ') || 'none'})`
  );

  // A file of each kind that is taken, every field at every depth there.
  const sample = (shape) =>
    shape === 'string' ? 'x' : shape === 'number' ? 1 : shape === 'boolean' ? true : shape === 'flag' ? 1
      : shape.list ? [sample(shape.list)] : shape.map ? { key: sample(shape.map) }
        : Object.fromEntries(Object.entries(shape.fields).map(([name, field]) => [name, sample(field)]));
  // A setup with every field, version 1, and the enrichment given.
  const setup = (enrich) => ({ ...sample(STORED_SHAPES.RAG_SETUP), version: 1, enrich });
  const enrichStep = () => sample(STORED_SHAPES.RAG_SETUP.fields.enrich.list);
  // The file of each shape: how a row or an entry becomes a file, its check,
  // and a row, an entry or a setup that holds every field.
  const files = {
    PET_ROW: [
      (row) => [{ ...sample(STORED_SHAPES.PET_ROW), runId: 1, model: '', systemPrompt: 's', userPrompt: 'u' }, { ...row }],
      (value) => isExperimentTracker(value),
      { ...sample(STORED_SHAPES.PET_ROW), runId: 1, model: 'demo_llm' },
    ],
    OPTIMIZATION_ENTRY: [(entry) => [entry], (value) => isOptimizationTracker(value), sample(STORED_SHAPES.OPTIMIZATION_ENTRY)],
    RAG_SETUP: [(value) => value, (value) => isRagSetup(value), setup([enrichStep()])],
    MODEL_OPTION: [(option) => ({ reasoning_effort: option }), (value) => isModelOptions(value), { ...sample(STORED_SHAPES.MODEL_OPTION), default: 'x' }],
  };
  const misjudged = [];
  const unprobed = Object.keys(STORED_SHAPES).filter((name) => !files[name]);
  if (unprobed.length) misjudged.push(`no file to probe the shapes ${unprobed.join(', ')} with`);
  // Every field at every depth, by its path, with its shape and whether it is required.
  const leaves = [];
  const into = (shape, at, required) => {
    leaves.push({ at, shape, required });
    if (typeof shape === 'string') return;
    if (shape.list) return into(shape.list, [...at, 0], true);
    if (shape.map) return into(shape.map, [...at, 'key'], true);
    for (const [name, field] of Object.entries(shape.fields)) into(field, [...at, name], (shape.required || []).includes(name));
  };
  const wrong = (shape) => (typeof shape === 'string' ? { not: 'of this kind' } : 'not of this kind');
  const changed = (value, at, to) => {
    const copy = JSON.parse(JSON.stringify(value));
    let holder = copy;
    at.slice(0, -1).forEach((key) => { holder = holder[key]; });
    if (to === undefined) delete holder[at[at.length - 1]];
    else holder[at[at.length - 1]] = to;
    return copy;
  };
  let probes = 0;
  for (const [name, [wrap, check, valid]] of Object.entries(files)) {
    if (!check(wrap(valid))) misjudged.push(`${name}: the file with every field is refused`);
    leaves.length = 0;
    for (const [field, shape] of Object.entries(STORED_SHAPES[name].fields)) into(shape, [field], (STORED_SHAPES[name].required || []).includes(field));
    for (const { at, shape, required } of leaves) {
      const where = `${name}.${at.join('.')}`;
      probes += 3;
      if (check(wrap(changed(valid, at, wrong(shape))))) misjudged.push(`${where} of another kind is taken`);
      // A list item or a map value is no field: it is there, and null is no item.
      const item = typeof at[at.length - 1] === 'number' || at[at.length - 1] === 'key';
      if (item) {
        if (check(wrap(changed(valid, at, null)))) misjudged.push(`${where} null is taken`);
        continue;
      }
      if (check(wrap(changed(valid, at, null))) === required) misjudged.push(`${where} null is ${required ? 'taken' : 'refused'}`);
      if (check(wrap(changed(valid, at, undefined))) === required) misjudged.push(`${where} left out is ${required ? 'taken' : 'refused'}`);
    }
  }
  // The rules of the runs.
  const header = (runId) => ({ runId, model: '', systemPrompt: 's', userPrompt: 'u' });
  const modelRow = (runId, model) => ({ runId, model, response: 'r' });
  const rules = [
    ['a run of its header row alone', [header(1)], false],
    ['a run of its header row alone, before a run with a model', [header(1), header(2), modelRow(2, 'demo_llm')], false],
    // A run cannot keep a result under a name the page reads as a field of a
    // run (TRACKER_META_KEYS), nor under __proto__; under the name of a field every object inherits it can
    // (isReservedModelName), and the page writes such a run (verify-round-trip.js).
    ...(TRACKER_META_KEYS || ['judge']).map((name) => [`a run whose model is named ${name}, a key the page reads as a field of a run`, [header(1), modelRow(1, name)], false]),
    ...Object.getOwnPropertyNames(Object.prototype).map((name) => [
      `a run whose model is named ${name}, a field every object has`, [header(1), modelRow(1, name)], name !== '__proto__',
    ]),
    ['a run with one model twice', [header(1), modelRow(1, 'demo_llm'), modelRow(1, 'demo_llm')], false],
    ['a run with one model twice, named constructor', [header(1), modelRow(1, 'constructor'), modelRow(1, 'constructor')], false],
    ['two runs of one model each', [header(1), modelRow(1, 'demo_llm'), header(2), modelRow(2, 'demo_llm')], true],
    ['a run of two models', [header(1), modelRow(1, 'demo_llm'), modelRow(1, 'second_llm')], true],
    ['a list of no runs', [], true],
  ];
  for (const [what, value, taken] of rules) if (isExperimentTracker(value) !== taken) misjudged.push(`tracker: ${what} is ${taken ? 'refused' : 'taken'}`);
  const run = (optimizationId) => ({ optimizationId, status: 'failed' });
  if (isOptimizationTracker([run(1), run(1), run(2)])) misjudged.push('optimization tracker: two runs of one optimizationId are taken');
  if (!isOptimizationTracker([run(1), run(2)])) misjudged.push('optimization tracker: two runs of their own numbers are refused');
  if (!isRagSetup(setup(enrichStep()))) misjudged.push('rag-setup.json: the one prompt of a setup saved before the chain is refused');
  if (isRagSetup(setup({ ...enrichStep(), workers: 'x' }))) misjudged.push('rag-setup.json: the one prompt of a setup saved before the chain with a field of another kind is taken');
  if (!isRagSetup(setup(undefined))) misjudged.push('rag-setup.json: a setup without enrichment is refused');
  if (isRagSetup(setup([{ foo: 1 }]))) misjudged.push('rag-setup.json: an enrichment of an entry that is no prompt is taken');
  if (isRagSetup(setup('x'))) misjudged.push('rag-setup.json: an enrichment that is a text is taken');
  if (isRagSetup({ ...setup(undefined), version: 2 })) misjudged.push('rag-setup.json: a setup of version 2 is taken');
  // options.json: every rule of an option by its kind, as the page draws it
  // and sends it (isModelOptions), and the one field the page reads of the
  // file of a decision model (isDecisionModelOptions).
  const optionRules = OPTION_RULES(NUMBER_FIELD_OPTIONS || []);
  for (const [what, value, taken] of optionRules) if (isModelOptions(value) !== taken) misjudged.push(`options.json: ${what} is ${taken ? 'refused' : 'taken'}`);
  for (const [what, value, taken] of DECISION_OPTION_RULES) {
    if (typeof isDecisionModelOptions !== 'function' || isDecisionModelOptions(value) !== taken) misjudged.push(`options.json of a decision model: ${what} is ${taken ? 'refused' : 'taken'}`);
  }
  // The number fields of the page's own, from the code: every option whose
  // default the drawing of the LLMs reads by its name is one of
  // NUMBER_FIELD_OPTIONS, drawn there only when it is a number
  // (isNumberOption) - the options the check reads no label of.
  {
    const builder = sourceFiles()['objects/prompt-builder.ts'] || '';
    const named = [...new Set([...builder.matchAll(/model\.options\.(\w+)\.default/g)].map((match) => match[1]))].sort();
    const guarded = [...builder.matchAll(/if \(model\?\.options\?\.(\w+) && isNumberOption\(model\.options\.(\w+)\)\) \{/g)].filter((match) => match[1] === match[2]).map((match) => match[1]).sort();
    const listed = Array.isArray(NUMBER_FIELD_OPTIONS) ? [...NUMBER_FIELD_OPTIONS].sort() : [];
    step(
      listed.length > 0 && JSON.stringify(named) === JSON.stringify(listed) && JSON.stringify(guarded) === JSON.stringify(listed),
      `every option the page draws in a number field of its own is one of NUMBER_FIELD_OPTIONS, and is drawn there only when it is a number (NUMBER_FIELD_OPTIONS: ${listed.join(', ') || 'none'}; read by name: ${named.join(', ') || 'none'}; drawn only as numbers: ${guarded.join(', ') || 'none'})`
    );
  }
  step(
    misjudged.length === 0 && typeof fits === 'function',
    `the checks of the stored files take every field of its kind and refuse it of another, at every depth (${probes} probes of the fields of ${Object.keys(files).join(', ')}, and ${rules.length + 8 + optionRules.length + DECISION_OPTION_RULES.length} files the rules of a run, an optimization tracker, a setup and an options.json name; wrong: ${misjudged.join('; ') || 'none'})`
  );
}

(async () => {
  census();
  htmlCensus();
  offersCensus();
  await shapeCensus();

  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  trackPagesOf(browser);
  let pageErrors = [];
  const newPage = async () => {
    const page = await browser.newPage({ viewport: { width: 1500, height: 1100 } });
    // A loaded machine can take longer than Playwright's 30 s to load the page.
    page.setDefaultNavigationTimeout(180000);
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
    return page;
  };
  const road = async (name, run) => {
    console.log(`---- ${name}`);
    pageErrors = [];
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
    for (let round = 0; round < 480; round++) {
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
  // One request that matches `match` fails - refused, or without an answer;
  // the requests after it go through.
  const failOnce = async (page, match, how) => {
    let failed = false;
    await page.route(
      (url) => match(new URL(url)),
      (route) => {
        if (failed || route.request().method() !== 'GET') return route.continue();
        failed = true;
        if (how === 'abort') return route.abort('failed');
        return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ httpStatusCode: 500, message: 'refused by the suite' }) });
      }
    );
  };
  const savedRuns = (page) => page.$$eval(`${lpb('pet')} .pet-run-delete`, (buttons) => buttons.length);
  const UNREAD_RUNS = 'The saved runs of this prompt-test could not be read from SAS Model Manager';
  const UNREAD_SETUP = 'The stored setup could not be read from SAS Model Manager';
  const trackerUploads = (log) =>
    log.filter((entry) => entry.method === 'POST' && /\/contents(\?|$)/.test(entry.url) && entry.body.includes('filename="Prompt-Experiment-Tracker.json"'));

  // ==========================================================================
  // The Prompt Builder: a prompt-test whose saved runs cannot be read
  // ==========================================================================
  for (const how of ['refused', 'abort']) {
    await road(`a prompt-test whose saved runs cannot be read (${how})`, async (page) => {
      await openPage(page);
      await choose(page, 'LPB', '#LPB-project-dropdown', 'proj-1');
      await failOnce(page, (url) => url.pathname === '/files/files/trk-1/content', how);
      await choose(page, 'LPB', '#LPB-prompt-dropdown', 'model-used');
      await waitUntil(async () => (await toasts(page)).length > 0, 'a toast', 15000).catch(() => {});
      const line = await textOf(page, lpb('pet-save-only-result'));
      const said = await toasts(page);
      step(
        line.startsWith(UNREAD_RUNS) && said.some((text) => text.startsWith(UNREAD_RUNS)) && (await savedRuns(page)) === 0,
        `a prompt-test whose saved runs cannot be read (${how}) says so beside Save Experiments and in a toast, and shows no run (line: "${clip(line)}"; toasts: ${JSON.stringify(said)}; runs shown: ${await savedRuns(page)})`
      );
      // A run, and the save a person then clicks.
      await goStep(page, 'LPB', 'build');
      await page.fill(lpb('user-prompt'), 'A question typed after the failed read');
      await page.check('#model0');
      await page.click(lpb('run-experiment'));
      await waitUntil(async () => (await page.$$(`${lpb('pet')} .accordion-item`)).length > 0, 'the run is drawn', 30000);
      await waitUntil(async () => (await textOf(page, lpb('run-experiment'))) === 'Run Experiments', 'the run has ended', 30000);
      await atRest();
      await resetLog();
      await page.click(lpb('pet-save-button'));
      await waitUntil(async () => (await textOf(page, lpb('pet-save-button'))) === 'Save Experiments', 'the save has ended', 30000);
      await atRest();
      const log = await getLog();
      const writes = log.filter((entry) => entry.method !== 'GET').map((entry) => `${entry.method} ${entry.url}`);
      const after = await textOf(page, lpb('pet-save-only-result'));
      step(
        trackerUploads(log).length === 0 && writes.length === 0 && after.startsWith(UNREAD_RUNS),
        `Save Experiments on a prompt-test whose saved runs were not read writes nothing over them and says why (writes: ${JSON.stringify(writes)}; line: "${clip(after)}")`
      );
      // Opened again, the runs are read.
      await choose(page, 'LPB', '#LPB-prompt-dropdown', 'model-free');
      await choose(page, 'LPB', '#LPB-prompt-dropdown', 'model-used');
      await waitUntil(async () => (await savedRuns(page)) > 0, 'the runs are drawn', 15000).catch(() => {});
      const again = await textOf(page, lpb('pet-save-only-result'));
      step(
        (await savedRuns(page)) > 0 && again === '',
        `the prompt-test opened again shows its saved runs, and no line that they were not read (runs: ${await savedRuns(page)}; line: "${clip(again)}")`
      );
      step(pageErrors.length === 0, `a prompt-test whose saved runs cannot be read (${how}): no uncaught page errors (got: ${pageErrors.join(' | ') || 'none'})`);
    });
  }

  await fetch(BASE + '/__decision', { method: 'POST' });
  await road('a decision template whose saved runs cannot be read', async (page) => {
    await openPage(page, 'decision');
    await choose(page, 'LPB', '#LPB-project-dropdown', 'proj-2');
    await failOnce(page, (url) => url.pathname === '/files/files/dtrk-1/content', 'refused');
    await choose(page, 'LPB', '#LPB-prompt-dropdown', 'model-dec');
    await waitUntil(async () => (await toasts(page)).length > 0, 'a toast', 15000).catch(() => {});
    const line = await textOf(page, lpb('pet-save-only-result'));
    step(
      line.startsWith(UNREAD_RUNS) && (await toasts(page)).some((text) => text.startsWith(UNREAD_RUNS)) && (await savedRuns(page)) === 0,
      `a decision template whose saved runs cannot be read says so and shows no run (line: "${clip(line)}"; runs shown: ${await savedRuns(page)})`
    );
  });

  // ==========================================================================
  // The Prompt Builder: a tracker that is read and is no list of runs
  // ==========================================================================
  // SAS Model Manager answers 200, with a body that is no tracker: what the
  // page says is that, and that opening it again does not help - the line of
  // a read that failed sent the reader to open it again, for good.
  const UNUSABLE_RUNS = 'The saved runs of this prompt-test cannot be used';
  const notATracker = [
    ['an HTML page', 'text/html', '<html><body>Signed out</body></html>'],
    ['an object', 'application/json', '{"rows":[]}'],
    // A list whose entries are not rows of a run: every row the page writes
    // is an object with a numeric runId.
    ['a list of texts', 'application/json', '["a"]'],
    ['a list of objects without a runId', 'application/json', '[{"foo":1}]'],
    ['a list of lists', 'application/json', '[[]]'],
    ['a list of numbers', 'application/json', '[1,2]'],
    ['a list holding null', 'application/json', '[null]'],
    // A run starts with a row of its own - no model, its two prompts - and
    // goes on with a row per model (isExperimentTracker).
    ['rows with a runId and nothing else of a run', 'application/json', '[{"runId":1}]'],
    ['rows with a runId and a field of no run', 'application/json', '[{"runId":1,"foo":1}]'],
    ['a run that starts with the row of a model', 'application/json', '[{"runId":1,"systemPrompt":"","userPrompt":"","model":"demo_llm","response":"x"}]'],
    // A run has a row per model - at least one, each model once, none named
    // like a field of a run or like a field every object has - and every
    // field of a row is of the kind the page reads (PET_ROW): the page writes
    // nothing else, and Save Experiments dropped what it could not read back.
    ['a run of its header row alone', 'application/json', '[{"runId":1,"model":"","systemPrompt":"Kept sys","userPrompt":"Kept user"}]'],
    ['a run whose model is named like a field of a run', 'application/json', '[{"runId":1,"model":"","systemPrompt":"s","userPrompt":"u"},{"runId":1,"model":"demo_llm","response":"x"},{"runId":1,"model":"judge","response":"y"}]'],
    ['a run whose model is named __proto__', 'application/json', '[{"runId":1,"model":"","systemPrompt":"s","userPrompt":"u"},{"runId":1,"model":"demo_llm","response":"x"},{"runId":1,"model":"__proto__","response":"y"}]'],
    ['a run with one model twice', 'application/json', '[{"runId":1,"model":"","systemPrompt":"s","userPrompt":"u"},{"runId":1,"model":"demo_llm","response":"FIRST ANSWER"},{"runId":1,"model":"demo_llm","response":"SECOND ANSWER"}]'],
    ['a row whose response is an object', 'application/json', '[{"runId":1,"model":"","systemPrompt":"s","userPrompt":"u"},{"runId":1,"model":"demo_llm","response":{"a":1}}]'],
    ['a header whose manifest configuration holds no outputs', 'application/json', '[{"runId":1,"model":"","systemPrompt":"s","userPrompt":"u","manifest":{"x":1}},{"runId":1,"model":"demo_llm","response":"x","best_prompt":1}]'],
    ['a header whose manifest configuration is a text', 'application/json', '[{"runId":1,"model":"","systemPrompt":"s","userPrompt":"u","manifest":"x"},{"runId":1,"model":"demo_llm","response":"x","best_prompt":1}]'],
  ];
  for (const [what, type, body] of notATracker) {
    storedRoadsRun.add('Prompt-Experiment-Tracker.json');
    await road(`a tracker that is answered with ${what}`, async (page) => {
      await openPage(page);
      await choose(page, 'LPB', '#LPB-project-dropdown', 'proj-1');
      await page.route('**/files/files/trk-1/content', (route) => route.fulfill({ status: 200, contentType: type, body }));
      await choose(page, 'LPB', '#LPB-prompt-dropdown', 'model-used');
      await waitUntil(async () => (await toasts(page)).length > 0, 'a toast', 15000).catch(() => {});
      const line = await textOf(page, lpb('pet-save-only-result'));
      const said = await toasts(page);
      step(
        line.startsWith(UNUSABLE_RUNS) && line.includes('Repair or remove the file') && said.some((text) => text.startsWith(UNUSABLE_RUNS)) && (await savedRuns(page)) === 0,
        `a tracker answered with ${what} says that it holds no runs this page can read and what helps, beside Save Experiments and in a toast (line: "${clip(line)}"; toasts: ${JSON.stringify(said).slice(0, 300)}; runs shown: ${await savedRuns(page)})`
      );
      await goStep(page, 'LPB', 'build');
      await page.fill(lpb('user-prompt'), 'A question typed over a tracker that is no tracker');
      await page.check('#model0');
      await page.click(lpb('run-experiment'));
      await waitUntil(async () => (await page.$$(`${lpb('pet')} .accordion-item`)).length > 0, 'the run is drawn', 30000);
      await waitUntil(async () => (await textOf(page, lpb('run-experiment'))) === 'Run Experiments', 'the run has ended', 30000);
      await atRest();
      await resetLog();
      await page.click(lpb('pet-save-button'));
      await waitUntil(async () => (await textOf(page, lpb('pet-save-button'))) === 'Save Experiments', 'the save has ended', 30000);
      await atRest();
      const writes = (await getLog()).filter((entry) => entry.method !== 'GET').map((entry) => `${entry.method} ${entry.url}`);
      step(writes.length === 0, `Save Experiments over a tracker answered with ${what} writes nothing (writes: ${JSON.stringify(writes)})`);
      step(pageErrors.length === 0, `a tracker answered with ${what}: no uncaught page errors (got: ${pageErrors.join(' | ') || 'none'})`);
    });
  }

  // ==========================================================================
  // The texts of a stored run are text (round 24)
  // ==========================================================================
  // The prompts of a run and the names of its models were drawn as markup:
  // a stored tracker ran script in the page, and a prompt that held "<" lost
  // what followed it. htmlCensus names every text that goes into markup.
  await road('a tracker whose prompts and model hold markup', async (page) => {
    await openPage(page);
    await choose(page, 'LPB', '#LPB-project-dropdown', 'proj-1');
    const SYSTEM = 'Answer in <b>bold</b> <img src="x" onerror="window.__ran=1">';
    const USER = 'Compare a<b and c>d';
    const MODEL = 'm<i>x</i><img src=y onerror="window.__ran=2">';
    const rows = [{ runId: 1, model: '', systemPrompt: SYSTEM, userPrompt: USER }, { runId: 1, model: MODEL, response: 'r', best_prompt: 0 }];
    await page.route('**/files/files/trk-1/content', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(rows) }));
    await choose(page, 'LPB', '#LPB-prompt-dropdown', 'model-used');
    await waitUntil(async () => (await savedRuns(page)) > 0, 'the run is drawn', 60000).catch(() => {});
    // An image made of the texts would be a request of the page, its error
    // handler run once that request failed.
    await quiet(page);
    const seen = await page.evaluate(() => ({
      ran: window.__ran ?? null,
      system: (document.querySelector('#app-obj-LPB-pet-0-run-systenPrompt')?.textContent || '').trim(),
      user: (document.querySelector('#app-obj-LPB-pet-0-run-userPrompt')?.textContent || '').trim(),
      models: [...document.querySelectorAll('#app-obj-LPB-pet-0 .nested-accordion .accordion-button')].map((button) => button.textContent.trim()),
      markup: document.querySelectorAll('#app-obj-LPB-pet-0 img, #app-obj-LPB-pet-0 .accordion-body b b, #app-obj-LPB-pet-0 .accordion-button i').length,
    }));
    step(
      seen.ran === null && seen.system.endsWith(SYSTEM) && seen.user.endsWith(USER) && seen.models.some((text) => text.endsWith(MODEL)) && seen.markup === 0 && pageErrors.length === 0,
      `the prompts and the name of a model of a stored run are drawn as the text they are, and run nothing (script ran: ${seen.ran}; system prompt: "${clip(seen.system)}"; user prompt: "${clip(seen.user)}"; models: ${JSON.stringify(seen.models)}; elements made of the texts: ${seen.markup}; page errors: ${pageErrors.join(' | ') || 'none'})`
    );
  });

  // ==========================================================================
  // Every field of a stored file, there and empty (round 24)
  // ==========================================================================
  // A file the checks take is one the page draws: built from the shapes of
  // stored-json.ts (STORED_SHAPES), a tracker and an optimization tracker
  // whose rows and entries hold every field at every depth with a value of
  // its kind, or null in every text, number and box at every depth that is
  // not required, or null in every field of the row or the entry itself
  // that is not required, are drawn without a page error, without
  // "[object Object]" and without a line that they cannot be used; the run
  // and the optimization run are then loaded into the workbench.
  if (storedJson && storedJson.STORED_SHAPES) {
    const { STORED_SHAPES } = storedJson;
    // empty: '' fills every field, 'values' leaves a text, a number or a box
    // that is not required null, 'all' every field that is not required.
    const filled = (shape, empty) =>
      typeof shape === 'string'
        ? ({ string: 'x', number: 1, boolean: true, flag: 1 })[shape]
        : shape.list ? [filled(shape.list, empty)] : shape.map ? { key: filled(shape.map, empty) }
          : Object.fromEntries(Object.entries(shape.fields).map(([name, field]) => [
            name,
            (empty === 'all' || (empty === 'values' && typeof field === 'string')) && !(shape.required || []).includes(name) ? null : filled(field, empty),
          ]));
    for (const empty of ['', 'values', 'all']) {
      const how = { '': 'a value in every field', values: 'null in every text, number and box that is not required', all: 'null in every field that is not required' }[empty];
      await road(`a tracker whose rows hold ${how}`, async (page) => {
        await openPage(page);
        await choose(page, 'LPB', '#LPB-project-dropdown', 'proj-1');
        const row = filled(STORED_SHAPES.PET_ROW, empty);
        const rows = [{ ...row, runId: 1, model: '', systemPrompt: 'Every field', userPrompt: 'of a row' }, { ...row, runId: 1, model: 'demo_llm', best_prompt: 1 }];
        await page.route('**/files/files/trk-1/content', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(rows) }));
        await choose(page, 'LPB', '#LPB-prompt-dropdown', 'model-used');
        await waitUntil(async () => (await savedRuns(page)) > 0, 'the run is drawn', 15000).catch(() => {});
        await page.click(`${lpb('pet-0')} .pet-run-load`).catch(() => {});
        await quiet(page);
        const line = await textOf(page, lpb('pet-save-only-result'));
        const text = await textOf(page, lpb('pet'));
        step(
          (await savedRuns(page)) === 1 && line === '' && !text.includes('[object Object]') && pageErrors.length === 0,
          `a tracker whose rows hold ${how} is drawn and loaded (runs: ${await savedRuns(page)}; line: "${clip(line)}"; [object Object] drawn: ${text.includes('[object Object]')}; page errors: ${pageErrors.join(' | ') || 'none'})`
        );
      });
      await road(`an optimization history whose entry holds ${how}`, async (page) => {
        await openPage(page);
        await choose(page, 'LPB', '#LPB-project-dropdown', 'proj-1');
        const entry = { ...filled(STORED_SHAPES.OPTIMIZATION_ENTRY, empty), optimizationId: 1, status: 'succeeded' };
        await page.route('**/files/files/opttrk-1/content', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([entry, { ...entry, optimizationId: 2, type: 'comparison' }]) }));
        await choose(page, 'LPB', '#LPB-prompt-dropdown', 'model-used');
        await goStep(page, 'LPB', 'optimize');
        const entries = `${lpb('pane-optimize')} .pb-optimize-history-entry`;
        await waitUntil(async () => (await page.$$(entries)).length > 0, 'the history is drawn', 15000).catch(() => {});
        const drawn = (await page.$$(entries)).length;
        const text = await textOf(page, lpb('pane-optimize'));
        const unread = (await page.$$(`${lpb('pane-optimize')} .pb-optimize-history-unread`)).length;
        await page.click(`${lpb('pane-optimize')} .pb-optimize-history-load`).catch(() => {});
        await quiet(page);
        step(
          drawn === 2 && unread === 0 && !text.includes('[object Object]') && pageErrors.length === 0,
          `an optimization history whose entry holds ${how} - as a run and as a comparison - is drawn and loaded (entries: ${drawn}; line that it cannot be shown: ${unread}; [object Object] drawn: ${text.includes('[object Object]')}; page errors: ${pageErrors.join(' | ') || 'none'})`
        );
      });
    }
  } else {
    step(false, 'every field of a stored file, there and empty: the shapes of stored-json.ts could not be read');
  }

  // ==========================================================================
  // Every page of the contents of a prompt-test
  // ==========================================================================
  // SAS Model Manager lists the contents of a model a page at a time, and a
  // prompt-test holds a dataset snapshot per optimization run: with 100 of
  // them the tracker and the optimization tracker are on the second page.
  await road('a prompt-test whose trackers are on the second page of its contents', async (page) => {
    await openPage(page);
    await choose(page, 'LPB', '#LPB-project-dropdown', 'proj-1');
    const pages = [];
    await page.route('**/modelRepository/models/model-used/contents?*', async (route) => {
      if (route.request().method() !== 'GET') return route.continue();
      const url = new URL(route.request().url());
      const start = Number(url.searchParams.get('start') || 0);
      const limit = Number(url.searchParams.get('limit') || 100);
      pages.push(`start=${start}`);
      const own = (await (await route.fetch()).json()).items || [];
      const snapshots = Array.from({ length: 100 }, (_, index) => ({ id: `c-snap-${index}`, name: `Prompt-Optimization-Dataset-${index}.json`, fileUri: `/files/files/snap-${index}` }));
      const all = [...snapshots, ...own];
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ start, limit, count: all.length, items: all.slice(start, start + limit) }) });
    });
    await choose(page, 'LPB', '#LPB-prompt-dropdown', 'model-used');
    await waitUntil(async () => (await savedRuns(page)) > 0, 'the runs are drawn', 15000).catch(() => {});
    const line = await textOf(page, lpb('pet-save-only-result'));
    step(
      (await savedRuns(page)) > 0 && line === '',
      `a prompt-test whose tracker is on the second page of its contents shows its saved runs (runs: ${await savedRuns(page)}; line: "${clip(line)}"; pages asked for: ${pages.join(', ')})`
    );
    await goStep(page, 'LPB', 'optimize');
    const entries = `${lpb('pane-optimize')} .pb-optimize-history-entry`;
    await waitUntil(async () => (await page.$$(entries)).length > 0, 'the history is drawn', 15000).catch(() => {});
    step(
      (await page.$$(entries)).length > 0,
      `and its optimization history, whose tracker is on the second page as well (entries: ${(await page.$$(entries)).length})`
    );
  });

  // ==========================================================================
  // An optimization history that cannot be read
  // ==========================================================================
  const HISTORY_UNREAD = 'The optimization history of this prompt-test could not be read from SAS Model Manager';
  const HISTORY_UNUSABLE = 'The optimization history of this prompt-test cannot be shown';
  for (const [how, answer, said] of [
    ['refused', (route) => route.fulfill({ status: 500, contentType: 'application/json', body: '{"message":"refused by the suite"}' }), HISTORY_UNREAD],
    ['without an answer', (route) => route.abort('failed'), HISTORY_UNREAD],
    ['answered with an HTML page', (route) => route.fulfill({ status: 200, contentType: 'text/html', body: '<html><body>Signed out</body></html>' }), HISTORY_UNUSABLE],
    // Each run of the job has the number the page draws and deletes it by,
    // and its status (isOptimizationTracker).
    ['a list of entries that are no runs', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '[{"foo":1}]' }), HISTORY_UNUSABLE],
    ['a list of entries without a status', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '[{"optimizationId":1}]' }), HISTORY_UNUSABLE],
    // The job gives each run a number of its own, and writes every field of
    // an entry of the kind the page reads (OPTIMIZATION_ENTRY).
    ['a list of two runs of one optimizationId', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '[{"optimizationId":1,"status":"succeeded"},{"optimizationId":1,"status":"failed"},{"optimizationId":2,"status":"failed"}]' }), HISTORY_UNUSABLE],
    ['an entry whose candidates are a text', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '[{"optimizationId":2,"status":"succeeded","type":"comparison","targets":"x"}]' }), HISTORY_UNUSABLE],
    ['an entry whose evaluations are a text', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '[{"optimizationId":3,"status":"succeeded","evaluations":"x","optimizedPrompt":"y","usage":5}]' }), HISTORY_UNUSABLE],
  ]) {
    if (said === HISTORY_UNUSABLE) storedRoadsRun.add('Prompt-Optimization-Tracker.json');
    await road(`an optimization history whose tracker is ${how}`, async (page) => {
      await openPage(page);
      await choose(page, 'LPB', '#LPB-project-dropdown', 'proj-1');
      await page.route('**/files/files/opttrk-1/content', answer);
      await choose(page, 'LPB', '#LPB-prompt-dropdown', 'model-used');
      await goStep(page, 'LPB', 'optimize');
      const place = `${lpb('pane-optimize')} .pb-optimize-history-unread`;
      await waitUntil(async () => (await page.$$(place)).length > 0, 'the line of the history', 15000).catch(() => {});
      const line = (await page.$(place)) ? await textOf(page, place) : '';
      const history = await textOf(page, lpb('pane-optimize'));
      const deletes = (await page.$$(`${lpb('pane-optimize')} .pb-optimize-history-delete`)).length;
      step(
        line.startsWith(said) && (await page.$$(`${lpb('pane-optimize')} .pb-optimize-history-entry`)).length === 0 && deletes === 0 && pageErrors.length === 0,
        `an optimization history whose tracker is ${how} says so where the history stands, and lists no run and no Delete (line: "${clip(line)}"; Delete buttons: ${deletes}; the step reads: "${clip(history.slice(-200))}"; page errors: ${pageErrors.join(' | ') || 'none'})`
      );
    });
  }

  // ==========================================================================
  // The documentation of a prompt-test that cannot be read
  // ==========================================================================
  await road('the documentation of a prompt-test that cannot be read', async (page) => {
    await openPage(page);
    await choose(page, 'LPB', '#LPB-project-dropdown', 'proj-1');
    await failOnce(page, (url) => url.pathname === '/modelRepository/models/model-used', 'refused');
    await choose(page, 'LPB', '#LPB-prompt-dropdown', 'model-used');
    const section = `${lpb('pane-setup')} .pb-doc-section`;
    const unread = `${section} .pb-doc-unread`;
    const shown = (await page.$(unread)) ? await page.$eval(unread, (element) => !element.hidden) : false;
    const line = shown ? await textOf(page, unread) : '';
    step(
      shown && line.startsWith('The documentation of this prompt-test could not be read from SAS Model Manager') && (await page.isDisabled(`${section} > button`)),
      `the documentation of a prompt-test that cannot be read says so in its section, and Save documentation stays off (line shown: ${shown}; "${clip(line)}")`
    );
    await choose(page, 'LPB', '#LPB-prompt-dropdown', 'model-free');
    await choose(page, 'LPB', '#LPB-prompt-dropdown', 'model-used');
    const again = (await page.$(unread)) ? await page.$eval(unread, (element) => !element.hidden) : false;
    step(!again && !(await page.isDisabled(`${section} > button`)), `opened again, the documentation is read: no line, and Save documentation can be used (line shown: ${again})`);
  });

  // ==========================================================================
  // The RAG Builder: a setup that cannot be read
  // ==========================================================================
  const SETUP_WRITES = [['Save setup', rgb('save')], ['Manifest setup', rgb('manifest')], ['Save documentation', rgb('save-documentation')]];
  const usableWrites = async (page) => {
    const usable = [];
    for (const [name, selector] of SETUP_WRITES) if (!(await page.isDisabled(selector))) usable.push(name);
    return usable;
  };
  const banner = (page) => textOf(page, rgb('status'));
  const ragSetupFile = async (setup) =>
    ((await (await fetch(`${BASE}/modelRepository/models/${setup}/contents`)).json()).items || []).find((item) => item.name === 'rag-setup.json').fileUri;
  const unreadRoads = [
    ['the contents of the setup cannot be listed', 'rag-setup-1', async () => (url) => url.pathname === '/modelRepository/models/rag-setup-1/contents'],
    ['its rag-setup.json cannot be read', 'rag-setup-1', async () => { const file = await ragSetupFile('rag-setup-1'); return (url) => url.pathname === `${file}/content`; }],
    ['the attributes of a setup never saved cannot be read', 'rag-setup-2', async () => (url) => url.pathname === '/modelRepository/models/rag-setup-2'],
  ];
  for (const [what, setup, matcher] of unreadRoads) {
    for (const how of ['refused', 'abort']) {
      await road(`a RAG setup: ${what} (${how})`, async (page) => {
        await openPage(page, 'rag');
        await choose(page, 'RGB', rgb('project'), 'rag-proj');
        await failOnce(page, await matcher(), how);
        await choose(page, 'RGB', rgb('setup'), setup);
        await waitUntil(async () => (await banner(page)) !== '', 'the banner', 60000).catch(() => {});
        // The buttons are given back once the reading has ended - as late as
        // that, they have to be off still.
        await quiet(page);
        const usable = await usableWrites(page);
        const said = await banner(page);
        const kind = await page.$$eval(`${rgb('status')} .alert`, (alerts) => alerts.map((alert) => [...alert.classList].find((name) => /^alert-/.test(name))).join(','));
        step(
          usable.length === 0 && said.startsWith(UNREAD_SETUP) && kind === 'alert-danger' && pageErrors.length === 0,
          `a RAG setup that cannot be read (${what}, ${how}) says so and keeps the buttons that write it off (usable: ${usable.join(', ') || 'none'}; banner: ${kind} "${clip(said)}"; page errors: ${pageErrors.join(' | ') || 'none'})`
        );
        await choose(page, 'RGB', rgb('setup'), setup === 'rag-setup-1' ? 'rag-setup-2' : 'rag-setup-1');
        await choose(page, 'RGB', rgb('setup'), setup);
        await waitUntil(async () => (await usableWrites(page)).length === 3, 'the buttons are given back', 15000).catch(() => {});
        step(
          (await usableWrites(page)).length === 3 && !(await banner(page)).startsWith(UNREAD_SETUP),
          `the RAG setup opened again is read, and its buttons can be used (usable: ${(await usableWrites(page)).join(', ') || 'none'}; banner: "${clip(await banner(page))}")`
        );
      });
    }
  }

  // A rag-setup.json that is read and is no setup: the banner says that, and
  // what helps - opening it again reads the same file.
  // A setup is of version 1 and has the sections and fields the page shows
  // and writes back (isRagSetup).
  for (const [what, type, body] of [
    ['no JSON', 'application/json', 'not json at all'],
    ['a list', 'application/json', '[]'],
    ['an object that is no setup', 'application/json', '{"foo":1}'],
    ['a setup without its sections', 'application/json', '{"version":1}'],
    // Each prompt of the enrichment is a prompt (RAG_ENRICH_STEP): Save setup
    // drops an entry without one.
    ['a setup whose enrichment holds an entry that is no prompt', 'application/json', (stored) => JSON.stringify({ ...stored, enrich: [{ foo: 1 }] })],
    // Every field of a setup, at every depth, is of the kind the page reads
    // (RAG_SETUP, round 25): an object in a documentation field was drawn as
    // "[object Object]" and written so, a policy flag "no" was written into
    // pipeline.yaml as true.
    ...[
      ['documentation', 'modelPurpose', { a: 1 }],
      ['policies', 'retainDays', 'x'],
      ['policies', 'recordHistory', 'no'],
      ['store', 'port', 'x'],
      ['store', 'host', 5],
      ['embedding', 'deploymentType', 5],
      ['source', 'includeCode', 'yes'],
      [null, 'pipelineVersion', 1],
      [null, 'credentialDomain', true],
    ].map(([section, field, value]) => [
      `a setup whose ${section ? `${section}.` : ''}${field} is ${JSON.stringify(value)}`,
      'application/json',
      (stored) => JSON.stringify(section ? { ...stored, [section]: { ...stored[section], [field]: value } } : { ...stored, [field]: value }),
    ]),
  ]) {
    storedRoadsRun.add('rag-setup.json');
    await road(`a rag-setup.json that is ${what}`, async (page) => {
      await openPage(page, 'rag');
      await choose(page, 'RGB', rgb('project'), 'rag-proj');
      const file = await ragSetupFile('rag-setup-1');
      const served = typeof body === 'function' ? body(await (await fetch(`${BASE}${file}/content`)).json()) : body;
      await page.route(`**${file}/content`, (route) => route.fulfill({ status: 200, contentType: type, body: served }));
      await choose(page, 'RGB', rgb('setup'), 'rag-setup-1');
      await waitUntil(async () => (await banner(page)) !== '', 'the banner', 60000).catch(() => {});
      await quiet(page);
      const usable = await usableWrites(page);
      const said = await banner(page);
      step(
        usable.length === 0 && said.startsWith('The stored setup cannot be used') && said.includes('Repair or remove the file') && pageErrors.length === 0,
        `a rag-setup.json that is ${what} says that it is no setup this page can read and what helps, and keeps the buttons that write it off (usable: ${usable.join(', ') || 'none'}; banner: "${clip(said)}"; page errors: ${pageErrors.join(' | ') || 'none'})`
      );
    });
  }

  // A setup saved before the enrichment could chain holds its one prompt as an
  // object of its own, not as a list: it is a setup, and opens.
  await road('a rag-setup.json whose enrichment is the one prompt of a setup saved before the chain', async (page) => {
    await openPage(page, 'rag');
    await choose(page, 'RGB', rgb('project'), 'rag-proj');
    const file = await ragSetupFile('rag-setup-1');
    const stored = await (await fetch(`${BASE}${file}/content`)).json();
    const enrich = {
      promptProjectId: 'proj-1', promptModelId: 'model-parse', promptModelName: 'Summarise Chunk',
      mapping: { chunk: 'chunk' }, headerOutput: 'context_header', columnOutputs: [], workers: 2,
    };
    await page.route(`**${file}/content`, (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...stored, enrich }) }));
    await choose(page, 'RGB', rgb('setup'), 'rag-setup-1');
    await waitUntil(async () => (await usableWrites(page)).length === 3, 'the buttons are given back', 15000).catch(() => {});
    const said = await banner(page);
    step(
      (await usableWrites(page)).length === 3 && !said.startsWith('The stored setup cannot be used') && pageErrors.length === 0,
      `a rag-setup.json whose enrichment is one prompt, as before the chain, opens as a setup (usable: ${(await usableWrites(page)).join(', ') || 'none'}; banner: "${clip(said)}"; page errors: ${pageErrors.join(' | ') || 'none'})`
    );
  });

  await road('Save documentation whose read of the stored setup fails', async (page) => {
    await openPage(page, 'rag');
    await choose(page, 'RGB', rgb('project'), 'rag-proj');
    await choose(page, 'RGB', rgb('setup'), 'rag-setup-1');
    await waitUntil(async () => !(await page.isDisabled(rgb('save-documentation'))), 'Save documentation can be used', 15000);
    const section = `${rgb('pane-setup')} .pb-doc-section`;
    if (!(await page.$eval(section, (details) => details.open))) await page.click(`${section} > summary`);
    await page.fill(rgb('setup-doc-modelPurpose'), 'Typed before the failed read');
    const file = await ragSetupFile('rag-setup-1');
    await failOnce(page, (url) => url.pathname === `${file}/content`, 'refused');
    await resetLog();
    await page.click(rgb('save-documentation'));
    await waitUntil(async () => !(await page.$(`${rgb('save-documentation')} .spinner-border`)) && (await banner(page)) !== '', 'the save has ended', 60000).catch(() => {});
    await quiet(page);
    await atRest();
    const writes = (await getLog()).filter((entry) => entry.method !== 'GET').map((entry) => `${entry.method} ${entry.url}`);
    const said = await banner(page);
    step(
      writes.length === 0 && said.startsWith('Saving the documentation failed'),
      `Save documentation whose read of the stored rag-setup.json fails writes neither the file nor the attributes, and says it failed (writes: ${JSON.stringify(writes)}; banner: "${clip(said)}")`
    );
  });

  await road('a manifest whose ingestion job source cannot be read', async (page) => {
    await openPage(page, 'rag');
    await choose(page, 'RGB', rgb('project'), 'rag-proj');
    await choose(page, 'RGB', rgb('setup'), 'rag-setup-1');
    await failOnce(page, (url) => url.pathname === '/files/files/rag-src-ingest/content', 'refused');
    await goStep(page, 'RGB', 'store');
    await resetLog();
    await page.click(rgb('manifest'));
    await waitUntil(
      async () => !(await page.isDisabled(rgb('manifest'))) && (await textOf(page, rgb('manifest'))) === 'Manifest setup' && (await textOf(page, rgb('save-result'))) !== '',
      'the manifest has ended',
      60000
    );
    await atRest();
    const jobWrites = (await getLog()).filter((entry) => entry.method !== 'GET' && entry.url.startsWith('/jobDefinitions/')).map((entry) => `${entry.method} ${entry.url}`);
    const said = await textOf(page, rgb('save-result'));
    step(
      jobWrites.length === 0 && said.includes('The manifest stopped while generating the ingestion job'),
      `a manifest whose ingestion job source cannot be read stops before it writes a job definition (job definition writes: ${JSON.stringify(jobWrites)}; result: "${clip(said)}")`
    );
  });

  // ==========================================================================
  // An options.json that is not one the page can read
  // ==========================================================================
  // Every option of an LLM is an object that holds its default, drawn and
  // sent as it is written, by the rule of its kind (isModelOptions; the rules
  // themselves are probed by shapeChecks). A model whose options.json is JSON
  // and no such file - one of its options the page could draw only as
  // something else - is shown as one that cannot be used - its box off, with
  // a note - and offered nowhere else: not as a judge, a member or the
  // chairman of a council, not as a target of an optimization; a stored run
  // or an optimization run that used it, loaded, does not tick its box
  // (offersCensus names every use of the lists of the models).
  const OPTIONS_UNUSABLE = 'The options.json of this model is not one this page can read, so the model cannot be used here.';
  for (const [what, body] of [
    ['a list', '[1,2]'],
    ['an option that is no object', '{"temperature":0.7}'],
    ['an enum whose values are no texts', '{"temperature":{"default":0.7},"reasoning_effort":{"type":"enum","default":"low","values":[1,2]}}'],
    ['an enum without its values', '{"temperature":{"default":0.7},"reasoning_effort":{"type":"enum","default":"low"}}'],
    ['a box whose default is 1', '{"temperature":{"default":0.7},"my_flag":{"type":"bool","default":1}}'],
    ['a provider key named by a number', '{"temperature":{"default":0.7},"API_KEY":{"default":5}}'],
  ]) {
    storedRoadsRun.add('options.json');
    await road(`an options.json that is ${what}`, async (page) => {
      await page.route('**/files/files/opt-1/content', (route) => route.fulfill({ status: 200, contentType: 'application/json', body }));
      await openPage(page);
      await goStep(page, 'LPB', 'build');
      const llm = await page.evaluate(() => {
        const box = document.querySelector('#model0');
        return {
          name: box?.value,
          off: Boolean(box?.disabled),
          note: (box?.closest('.form-check')?.querySelector('.pb-options-note')?.textContent || '').trim(),
          judge: [...document.querySelectorAll('#app-obj-LPB-judge-model option')].map((option) => option.value),
          panel: [...document.querySelectorAll('#app-obj-LPB-judge-panel input')].map((input) => input.value),
          chairman: [...document.querySelectorAll('#app-obj-LPB-judge-chairman-model option')].map((option) => option.value),
          target: [...document.querySelectorAll('#app-obj-LPB-optimize-target option')].map((option) => option.value),
        };
      });
      const offered = [...llm.judge, ...llm.panel, ...llm.chairman, ...llm.target].filter((name) => name === 'demo_llm');
      step(
        llm.name === 'demo_llm' && llm.off && llm.note.startsWith(OPTIONS_UNUSABLE) && offered.length === 0 && llm.chairman.length > 1 && pageErrors.length === 0,
        `an LLM whose options.json is ${what} is shown as one that cannot be used - its box off, with a note - and offered nowhere else (box ${llm.name}: ${llm.off ? 'off' : 'on'}; note: "${clip(llm.note)}"; offered as judge, member, chairman or target: ${offered.length}; chairmen: ${JSON.stringify(llm.chairman)}; page errors: ${pageErrors.join(' | ') || 'none'})`
      );
      // The best run of Used Prompt and its optimization run used demo_llm.
      await choose(page, 'LPB', '#LPB-project-dropdown', 'proj-1');
      await choose(page, 'LPB', '#LPB-prompt-dropdown', 'model-used');
      await waitUntil(async () => (await savedRuns(page)) > 0, 'the runs are drawn', 15000).catch(() => {});
      const afterRun = await page.isChecked('#model0');
      await goStep(page, 'LPB', 'optimize');
      await waitUntil(async () => (await page.$$(`${lpb('pane-optimize')} .pb-optimize-history-load`)).length > 0, 'the history is drawn', 15000).catch(() => {});
      await page.click(`${lpb('pane-optimize')} .pb-optimize-history-load`);
      // Loading an optimized prompt moves to Build & Test once it is loaded.
      await page.waitForSelector(`${lpb('pane-build')}:not([hidden])`, { timeout: 15000 });
      const afterOptimization = await page.isChecked('#model0');
      step(
        !afterRun && !afterOptimization && pageErrors.length === 0,
        `a stored run and an optimization run that used an LLM whose options.json is ${what}, loaded, do not tick its box (ticked after the run: ${afterRun}; after the optimization run: ${afterOptimization}; page errors: ${pageErrors.join(' | ') || 'none'})`
      );
    });
  }
  // An options.json whose options hold, in the fields the page does not read
  // of them, values of any kind: the LLM is usable, each option drawn and
  // sent as it is written.
  storedRoadsRun.add('options.json');
  await road('an options.json whose options hold values of any kind where the page does not read them', async (page) => {
    const body = '{"temperature":{"default":0.7,"label":5,"description":{"a":1},"values":[1,2]},"my_mode":{"default":"fast","values":5},"API_KEY":{"default":"OpenAI","label":7,"type":3}}';
    await page.route('**/files/files/opt-1/content', (route) => route.fulfill({ status: 200, contentType: 'application/json', body }));
    await openPage(page);
    await choose(page, 'LPB', '#LPB-project-dropdown', 'proj-1');
    await choose(page, 'LPB', '#LPB-prompt-dropdown', 'model-free');
    await goStep(page, 'LPB', 'build');
    const llm = await page.evaluate(() => {
      const box = document.querySelector('#model0');
      const control = (id) => {
        const element = document.getElementById(id);
        return element ? `${element.type}=${element.value}` : 'none';
      };
      return {
        name: box?.value,
        off: Boolean(box?.disabled),
        note: (box?.closest('.form-check')?.querySelector('.pb-options-note, .pb-credential-note, .pb-name-note')?.textContent || '').trim(),
        temperature: control('temperature0'),
        mode: control('my_mode0'),
        judge: [...document.querySelectorAll('#app-obj-LPB-judge-model option')].map((option) => option.value),
      };
    });
    step(
      llm.name === 'demo_llm' && !llm.off && llm.note === '' && llm.temperature === 'number=0.7' && llm.mode === 'text=fast' && llm.judge.includes('demo_llm') && pageErrors.length === 0,
      `an LLM whose options hold values of any kind where the page does not read them is usable, each option drawn as it is written (box ${llm.name}: ${llm.off ? 'off' : 'on'}; note: "${clip(llm.note)}"; temperature: ${llm.temperature}; my_mode: ${llm.mode}; offered as judge: ${llm.judge.includes('demo_llm')}; page errors: ${pageErrors.join(' | ') || 'none'})`
    );
    if (llm.off) return;
    await page.check('#model0');
    await page.fill(lpb('user-prompt'), 'A question');
    await resetLog();
    await page.click(lpb('run-experiment'));
    await waitUntil(async () => (await getLog()).some((entry) => entry.url === '/scr/demo_llm/demo_llm' && entry.status !== null), 'the call of demo_llm is answered', 60000);
    const call = (await getLog()).find((entry) => entry.url === '/scr/demo_llm/demo_llm');
    let request = {};
    try {
      request = JSON.parse(call.body);
    } catch {
      request = {};
    }
    const options = (request.inputs || []).find((input) => input.name === 'options');
    const sent = options ? options.value : '';
    step(
      /^\{temperature:0\.7,my_mode:fast,API_KEY:[^,{}]+\}$/.test(sent),
      `a run sends each option of that LLM as it is written, and its provider key (options: "${clip(sent.replace(/API_KEY:[^,}]+/, 'API_KEY:<key>'))}")`
    );
    await waitUntil(async () => (await textOf(page, lpb('run-experiment'))) === 'Run Experiments', 'the run has ended', 60000);
  });

  // The options.json of a decision model: the page reads its API_KEY.default
  // alone (isDecisionModelOptions).
  storedRoadsRun.add('options.json');
  await road('an options.json of a decision model whose provider key is named by a number', async (page) => {
    await page.route('**/files/files/dopt-1/content', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '{"API_KEY":{"default":5}}' }));
    await openPage(page, 'decision');
    await goStep(page, 'LPB', 'build');
    const card = await page.evaluate(() => {
      const box = document.querySelector('#decision-model0');
      const holder = box?.closest('.pb-model-card');
      return { off: Boolean(box?.disabled), name: holder?.textContent || '', note: (holder?.querySelector('.pb-options-note')?.textContent || '').trim() };
    });
    step(
      card.name.includes('jev_mock') && card.off && card.note.startsWith(OPTIONS_UNUSABLE),
      `a decision model whose provider key is named by a number is shown as one that cannot be used, with the note of its options.json (box: ${card.off ? 'off' : 'on'}; note: "${clip(card.note)}")`
    );
  });
  await road('an options.json of a decision model whose other options are of any kind', async (page) => {
    const body = '{"API_KEY":{"default":"OpenAI","label":7},"mode":{"type":"enum","default":"a","values":[1,2]},"x":5,"y":null}';
    await page.route('**/files/files/dopt-1/content', (route) => route.fulfill({ status: 200, contentType: 'application/json', body }));
    await openPage(page, 'decision');
    await goStep(page, 'LPB', 'build');
    const card = await page.evaluate(() => {
      const box = document.querySelector('#decision-model0');
      const holder = box?.closest('.pb-model-card');
      return { off: Boolean(box?.disabled), name: holder?.textContent || '', note: (holder?.querySelector('.pb-options-note, .pb-credential-note, .pb-name-note')?.textContent || '').trim() };
    });
    step(
      card.name.includes('jev_mock') && !card.off && card.note === '' && pageErrors.length === 0,
      `a decision model whose options.json holds fields of any kind beside its provider key is usable - the page reads its API_KEY.default alone (box: ${card.off ? 'off' : 'on'}; note: "${clip(card.note)}"; page errors: ${pageErrors.join(' | ') || 'none'})`
    );
  });
  await road('an options.json of a decision model that is no options file', async (page) => {
    await page.route('**/files/files/dopt-1/content', (route) => route.fulfill({ status: 200, contentType: 'application/json', body: '"API_KEY"' }));
    await openPage(page, 'decision');
    await goStep(page, 'LPB', 'build');
    const card = await page.evaluate(() => {
      const box = document.querySelector('#decision-model0');
      const holder = box?.closest('.pb-model-card');
      return { off: Boolean(box?.disabled), name: holder?.textContent || '', note: (holder?.querySelector('.pb-options-note')?.textContent || '').trim() };
    });
    step(
      card.name.includes('jev_mock') && card.off && card.note.startsWith(OPTIONS_UNUSABLE),
      `a decision model whose options.json is no options file is shown as one that cannot be used (box: ${card.off ? 'off' : 'on'}; note: "${clip(card.note)}")`
    );
  });
  step(
    storedFileNames.length > 0 && storedFileNames.every((name) => storedRoadsRun.has(name)),
    `every JSON file the page reads had its road with JSON that is not that file (files: ${storedFileNames.join(', ') || 'none read'}; roads run: ${[...storedRoadsRun].join(', ') || 'none'})`
  );

  // ---- every request to SAS Viya went through the HTTP client of the app -------
  const directRequests = await (await fetch(BASE + '/__direct')).json();
  step(
    directRequests.length === 0,
    `no request to a SAS Viya service went around the HTTP client, which handles HTTP 449 and the CSRF token (direct: ${directRequests.join(' | ') || 'none'})`
  );
  await browser.close();
  console.log('\n===== SUMMARY =====');
  console.log(results.join('\n'));
  console.log(`${results.filter((r) => r.startsWith('OK')).length} checks passed, ${results.filter((r) => r.startsWith('FAIL')).length} failed`);
})().catch((e) => {
  console.error('VERIFY FAILED:', e.message);
  process.exit(1);
});
