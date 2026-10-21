/**
 * Runs the verify suites one after the other, each against a mock of its own.
 *
 * Start it from a scratch folder in which `playwright` is installed - the
 * suites are copied there as .cjs files (the package around this folder is an
 * ES module, the suites are CommonJS), and their screenshots and logs land
 * there too:
 *
 *   cd <scratch>            (once: npm i playwright)
 *   node <path-to>/LLM-Prompt-Builder/.claude/skills/verify/run-all.cjs
 *   node <path-to>/.../run-all.cjs verify-rag verify-options     (a subset)
 *
 * Every suite gets a FRESH mock: the suites create models, projects and jobs
 * in it, and the options suite reads its request log, so a mock that already
 * served another suite gives wrong results. The complete output of a suite is
 * written to out-<suite>.log; the console shows its FAIL and NOTE lines and
 * the count. The exit code is 1 when any suite failed or stopped early.
 *
 * Environment: VERIFY_DIST (the bundle to serve; default dist/index.html of
 * this project), MOCK_PORT (default 4173), and whatever the suites read
 * themselves (VERIFY_PYTHON, VERIFY_RAG_SECTIONS, VERIFY_VERBOSE).
 */
const fs = require('fs');
const path = require('path');
const http = require('http');
const { spawn, spawnSync } = require('child_process');

const SKILL = __dirname;
const PROJECT = path.resolve(SKILL, '..', '..', '..');
const REPOSITORY = path.resolve(PROJECT, '..');
const WORK = process.cwd();
const DIST = path.resolve(process.env.VERIFY_DIST || path.join(PROJECT, 'dist', 'index.html'));
const PORT = Number(process.env.MOCK_PORT) || 4173;
const BASE = `http://localhost:${PORT}`;
const SUITES = ['verify', 'verify-decision', 'verify-rag', 'verify-new-item', 'verify-write-lock', 'verify-outcomes', 'verify-reads', 'verify-round-trip', 'verify-options', 'verify-locales', 'verify-tokens', 'verify-waits'];
// The locale check, the token scan and the census of the waits read files
// only: no mock, no browser.
const NEEDS_MOCK = (suite) => !['verify-locales', 'verify-tokens', 'verify-waits'].includes(suite);

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const mockAnswers = () =>
  new Promise((resolve) => {
    const request = http.get(`${BASE}/__log`, (response) => {
      response.resume();
      resolve(response.statusCode === 200);
    });
    request.on('error', () => resolve(false));
    request.setTimeout(1000, () => {
      request.destroy();
      resolve(false);
    });
  });
// Until the mock answers, or its process has ended - then it did not start.
// A loaded machine takes its time to start one: the bound is the one of a
// navigation of the suites.
const mockStarted = async (mock) => {
  const start = Date.now();
  while (Date.now() - start < 180000) {
    if (mock.exitCode !== null || mock.signalCode !== null) return false;
    if (await mockAnswers()) return true;
    await sleep(200);
  }
  return false;
};
// Until the process of the mock has ended, which frees its port.
const mockEnded = (mock) => new Promise((resolve) => (mock.exitCode !== null || mock.signalCode !== null ? resolve() : mock.once('exit', resolve)));

(async () => {
  const wanted = process.argv.slice(2).map((name) => name.replace(/\.(c?js)$/, ''));
  const unknown = wanted.filter((name) => !SUITES.includes(name));
  if (unknown.length) throw new Error(`unknown suite: ${unknown.join(', ')} (there are: ${SUITES.join(', ')})`);
  const suites = wanted.length ? wanted : SUITES;
  // A suite that sends requests to its mock from Node takes fetch from
  // mock-fetch.cjs, which sends a request that got no answer again.
  const bare = SUITES.filter((suite) => {
    const text = fs.readFileSync(path.join(SKILL, `${suite}.js`), 'utf8');
    return /(?<![.\w])fetch\(/.test(text) && !text.includes("const fetch = require('./mock-fetch.cjs');");
  });
  if (bare.length) throw new Error(`${bare.join(', ')} send requests to the mock without mock-fetch.cjs - add "const fetch = require('./mock-fetch.cjs');"`);
  if (suites.some(NEEDS_MOCK)) {
    try {
      require.resolve('playwright', { paths: [WORK] });
    } catch {
      throw new Error(`playwright is not installed in ${WORK} - run "npm i playwright" in a scratch folder and start this script from there`);
    }
    if (!fs.existsSync(DIST)) throw new Error(`${DIST} does not exist - run "npm run build" first`);
  }
  // The harness as CommonJS, next to the node_modules that holds playwright.
  for (const file of fs.readdirSync(SKILL)) {
    if (/\.c?js$/.test(file) && file !== path.basename(__filename)) {
      fs.copyFileSync(path.join(SKILL, file), path.join(WORK, file.replace(/\.js$/, '.cjs')));
    }
  }

  const lines = [];
  let failed = false;
  for (const suite of suites) {
    let mock = null;
    if (NEEDS_MOCK(suite)) {
      if (await mockAnswers()) throw new Error(`something already answers on ${BASE} - stop it, or set MOCK_PORT`);
      const mockLog = fs.openSync(path.join(WORK, `mock-${suite}.log`), 'w');
      mock = spawn(process.execPath, [path.join(WORK, 'mock-server.cjs'), DIST], {
        cwd: WORK,
        env: { ...process.env, MOCK_PORT: String(PORT), MOCK_REPO_ROOT: REPOSITORY },
        stdio: ['ignore', mockLog, mockLog],
      });
      if (!(await mockStarted(mock))) throw new Error(`the mock did not start on ${BASE} (see mock-${suite}.log)`);
    }
    const started = Date.now();
    const run = spawnSync(process.execPath, [path.join(WORK, `${suite}.cjs`), ...(NEEDS_MOCK(suite) ? [] : [PROJECT])], {
      cwd: WORK,
      // VERIFY_DIST: the token scan reads the bundle the mocks serve.
      env: { ...process.env, VERIFY_BASE: BASE, VERIFY_DIST: DIST, VERIFY_PROJECT: PROJECT },
      encoding: 'utf8',
      maxBuffer: 1 << 28,
    });
    if (mock) {
      const ended = mockEnded(mock);
      mock.kill();
      await ended;
    }
    const output = `${run.stdout || ''}${run.stderr || ''}`;
    fs.writeFileSync(path.join(WORK, `out-${suite}.log`), output);
    // The browser suites repeat every line in their summary: count above it.
    const summaryAt = output.indexOf('===== SUMMARY =====');
    const body = summaryAt >= 0 ? output.slice(0, summaryAt) : output;
    const ok = (body.match(/^OK /gm) || []).length;
    const fail = (body.match(/^FAIL/gm) || []).length;
    // A browser suite stops at its first failed check; without its summary the
    // checks after that one did not run.
    const stoppedEarly = NEEDS_MOCK(suite) && summaryAt < 0;
    if (run.status !== 0 || fail > 0 || stoppedEarly) failed = true;
    const line = `${suite}: ${ok} OK, ${fail} FAIL${stoppedEarly ? ', STOPPED EARLY' : ''} (${Math.round((Date.now() - started) / 1000)} s)`;
    lines.push(line);
    console.log(line);
    body.split('\n').filter((text) => /^(FAIL|NOTE|VERIFY FAILED|\[pageerror\])/.test(text)).forEach((text) => console.log(`   ${text}`));
    if (stoppedEarly) console.log(`   ${output.trim().split('\n').slice(-3).join('\n   ')}`);
  }
  console.log(`\n${lines.join('\n')}`);
  process.exit(failed ? 1 : 0);
})().catch((error) => {
  console.error(`run-all: ${error.message}`);
  process.exit(2);
});
