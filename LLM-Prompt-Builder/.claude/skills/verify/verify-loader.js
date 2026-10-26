/**
 * The loader of the built page, in a browser (round 31).
 *
 * The build stores the script and the stylesheet gzipped and base64-encoded
 * and adds a small loader that unpacks them (vite.config.ts,
 * goTemplateSafeScripts). This suite serves the built page under the
 * Content-Security-Policy SAS Job Execution is documented with - minus
 * `'unsafe-eval'`, which the page no longer needs - and checks what the
 * loader promises:
 *   - the page starts with no policy violation and no page error, and shows
 *     its first text (the page is not configured, so it calls no service);
 *   - the stylesheet is in the document before the script runs;
 *   - the script runs inside a function: nothing it declares lands on window;
 *   - the bytes are decoded as UTF-8: the German text keeps its umlauts;
 *   - a browser without DecompressionStream gets a sentence, not an empty page.
 *
 * It needs a browser but no mock: `node verify-loader.cjs <path-to>/LLM-Prompt-Builder`
 * from a folder where playwright is installed (run-all.cjs does that).
 * VERIFY_DIST names another bundle.
 */
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const project = path.resolve(process.argv[2] || path.join(__dirname, '..', '..', '..'));
const DIST = path.resolve(process.env.VERIFY_DIST || path.join(project, 'dist', 'index.html'));
const PAGE = 'http://loader.test/';
const CSP = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src * blob: data:";
const locale = (name) => JSON.parse(fs.readFileSync(path.join(project, 'src', 'i18n', 'locales', `${name}.json`), 'utf8'));
// The first sentence of the message an unconfigured page shows, in each language.
const firstSentence = (name) => locale(name).promptBuilder.promptBuilderConfigNeededMessage.split(';')[0];

let ok = 0;
let failed = 0;
const check = (condition, label) => {
  if (condition) ok += 1;
  else failed += 1;
  console.log(`${condition ? 'OK ' : 'FAIL'}  ${label}`);
};

async function open(browser, { lang = 'en-US', withoutDecompression = false } = {}) {
  const context = await browser.newContext({ locale: lang });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.addInitScript((strip) => {
    window.__violations = [];
    document.addEventListener('securitypolicyviolation', (event) =>
      window.__violations.push(`${event.violatedDirective} ${event.blockedURI}`)
    );
    // The order the loader adds the stylesheet and the script in.
    window.__added = [];
    new MutationObserver((records) => {
      for (const record of records) {
        for (const node of record.addedNodes) {
          if (node.nodeName === 'STYLE') window.__added.push('style');
          else if (node.nodeName === 'SCRIPT' && !node.type && node.textContent.startsWith('(function () {\n') && !node.textContent.includes('DecompressionStream')) window.__added.push('script');
        }
      }
    }).observe(document, { childList: true, subtree: true });
    window.__baseline = Object.keys(window);
    if (strip) delete window.DecompressionStream;
  }, withoutDecompression);
  const body = fs.readFileSync(DIST, 'utf8');
  await page.route(`${PAGE}**`, (route) =>
    route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', headers: { 'Content-Security-Policy': CSP }, body })
  );
  await page.goto(PAGE);
  return { context, page, errors };
}

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  try {
    // English, under the policy without 'unsafe-eval'.
    {
      const { context, page, errors } = await open(browser);
      const expected = firstSentence('en');
      const shown = await page
        .waitForFunction((text) => document.body.innerText.includes(text), expected, { timeout: 15000 })
        .then(() => true, () => false);
      check(shown, `the page starts under a policy without 'unsafe-eval' and shows "${expected.slice(0, 60)}..."`);
      const violations = await page.evaluate(() => window.__violations);
      check(violations.length === 0, `no Content-Security-Policy violation (${violations.join('; ') || 'none'})`);
      check(errors.length === 0, `no page error (${errors.join('; ') || 'none'})`);
      const order = await page.evaluate(() => ({
        added: window.__added.join(','),
        sheets: document.styleSheets.length,
        font: getComputedStyle(document.body).fontFamily,
      }));
      check(order.added === 'style,script', `the stylesheet is added before the script runs (added: ${order.added})`);
      check(order.sheets >= 1 && !/^"?Times/i.test(order.font), `the stylesheet applies (${order.sheets} sheet(s), body font ${order.font.slice(0, 40)})`);
      const leaked = await page.evaluate(() => Object.keys(window).filter((key) => !window.__baseline.includes(key) && !key.startsWith('__')));
      check(leaked.length === 0, `the script declares nothing on window (${leaked.slice(0, 8).join(', ') || 'nothing'})`);
      const packed = await page.$$eval('script[type="text/x-pb-gzip"]', (nodes) => nodes.map((node) => node.dataset.kind).join(','));
      check(packed === 'script,style', `the page carries the packed script and stylesheet (${packed})`);
      await context.close();
    }
    // German: the umlauts survive the decoding.
    {
      const { context, page, errors } = await open(browser, { lang: 'de-DE' });
      const expected = firstSentence('de');
      const shown = await page
        .waitForFunction((text) => document.body.innerText.includes(text), expected, { timeout: 15000 })
        .then(() => true, () => false);
      check(shown && /[äöüß]/.test(expected), `German text is decoded as UTF-8: "${expected.slice(0, 70)}..."`);
      check(errors.length === 0, `no page error in German (${errors.join('; ') || 'none'})`);
      await context.close();
    }
    // A browser without DecompressionStream.
    {
      const { context, page, errors } = await open(browser, { withoutDecompression: true });
      const shown = await page
        .waitForFunction(() => document.body && document.body.innerText.includes('This page needs a current browser'), null, { timeout: 15000 })
        .then(() => true, () => false);
      check(shown, 'a browser without DecompressionStream gets a sentence instead of an empty page');
      check(errors.length === 0, `no page error there either (${errors.join('; ') || 'none'})`);
      await context.close();
    }
  } finally {
    await browser.close();
  }
  console.log(`${ok} checks passed, ${failed} failed`);
  process.exitCode = failed > 0 ? 1 : 0;
})().catch((error) => {
  console.log(`FAIL  the suite stopped: ${error.message.split('\n')[0]}`);
  process.exitCode = 1;
});
