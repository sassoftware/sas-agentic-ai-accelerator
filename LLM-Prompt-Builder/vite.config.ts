/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Vite build for the Prompt Builder page (prompt templates, decision
 * templates and RAG setups in one bundle).
 *
 * `npm run build` produces a single self-contained `dist/index.html`:
 *   - inlineIntoHtml() puts the one script and the one stylesheet of the
 *     build into the HTML file.
 *   - goTemplateSafeScripts() then gzips the script and the stylesheet and
 *     stores them base64-encoded, so the SAS Job Execution Go template engine
 *     does not choke on the `{{`/`}}` sequences that a minified bundle
 *     inevitably contains, and no `$name$` reads as a parameter token. A small
 *     loader unpacks them in the browser. SAS Job Execution serves the page
 *     uncompressed, so this also cuts what every load transfers to about a
 *     quarter.
 *
 * That single file is uploaded as a SAS Job Execution definition and embedded in
 * a SAS Visual Analytics report. Requires `'unsafe-inline'` in `script-src` of
 * the Job Execution CSP (see README); no `'unsafe-eval'`.
 */
import { defineConfig, type Plugin } from 'vite';
import { gzipSync } from 'fflate';

/**
 * Put the one script and the one stylesheet of the build into index.html, the
 * way vite-plugin-singlefile 2.3.3 did, which this replaces (its file
 * patterns pulled micromatch and braces into the build): the
 * `<script ... src="...">` of the entry chunk becomes a `<script ...>` that
 * holds its code, the `<link ... href="...">` of the stylesheet a
 * `<style ...>` that holds the CSS, and both files leave the bundle. The
 * build is one chunk (`codeSplitting: false` below) with every asset inlined
 * into it, so anything else in the bundle would be a file the single page
 * does not carry: the build stops instead.
 */
function inlineIntoHtml(): Plugin {
  // A file name as a pattern: its dots and other special characters literal.
  const literal = (text: string): string => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return {
    name: 'inline-into-html',
    enforce: 'post',
    generateBundle(_options, bundle) {
      const files = Object.values(bundle);
      const pages = files.filter((file) => file.type === 'asset' && /\.html?$/.test(file.fileName));
      const chunks = files.filter((file) => file.type === 'chunk');
      const styles = files.filter((file) => file.type === 'asset' && file.fileName.endsWith('.css'));
      const others = files.filter((file) => !pages.includes(file) && !chunks.includes(file) && !styles.includes(file));
      if (pages.length !== 1 || chunks.length !== 1 || styles.length > 1 || others.length > 0) {
        this.error(
          `inline-into-html expects one page, one script and at most one stylesheet; the build has ${files.map((file) => file.fileName).join(', ')}`
        );
      }
      const page = pages[0];
      const chunk = chunks[0];
      if (page.type !== 'asset' || chunk.type !== 'chunk') return;
      let html = typeof page.source === 'string' ? page.source : new TextDecoder().decode(page.source);
      const withScript = html.replace(
        new RegExp(`<script([^>]*?) src="(?:[^"]*?/)?${literal(chunk.fileName)}"([^>]*)></script>`),
        // A function, so that a `$` in the code is not read as a pattern of
        // the replacement. Text inside the script that would end it or open
        // a comment is written as an escape.
        (_match, before: string, after: string) =>
          `<script${before}${after}>${chunk.code
            .replace(/"?__VITE_PRELOAD__"?/g, 'void 0')
            .replace(/<(\/script>|!--)/g, '\\x3C$1')
            .trim()}</script>`
      );
      if (withScript === html) this.error(`inline-into-html found no <script> for ${chunk.fileName}`);
      html = withScript;
      for (const style of styles) {
        if (style.type !== 'asset') continue;
        const css = typeof style.source === 'string' ? style.source : new TextDecoder().decode(style.source);
        const withStyle = html.replace(
          new RegExp(`<link([^>]*?) href="(?:[^"]*?/)?${literal(style.fileName)}"([^>]*)>`),
          (_match, before: string, after: string) => `<style${before}${after}>${css.replace('@charset "UTF-8";', '').trim()}</style>`
        );
        if (withStyle === html) this.error(`inline-into-html found no <link> for ${style.fileName}`);
        html = withStyle;
      }
      page.source = html;
      delete bundle[chunk.fileName];
      for (const style of styles) delete bundle[style.fileName];
    },
  };
}

/**
 * Normalize the entry template's line endings before Vite parses it, so a
 * checkout with CRLF endings (e.g. Windows with autocrlf) produces exactly
 * the same bundle as a LF checkout — CI byte-compares the committed
 * dist/index.html against a fresh Linux build.
 */
function normalizeTemplateEol(): Plugin {
  return {
    name: 'normalize-template-eol',
    enforce: 'pre',
    transformIndexHtml: {
      order: 'pre',
      handler: (html) => html.replace(/\r\n?/g, '\n'),
    },
  };
}

/** The type of the elements that carry a packed script or stylesheet: no browser runs it. */
const PACKED_TYPE = 'text/x-pb-gzip';

/**
 * Unpacks the page in the browser. It reads every packed element in order,
 * gunzips it with the browser's own DecompressionStream and decodes the bytes
 * as UTF-8 (the German texts, the arrows). The stylesheet goes in first; the
 * script runs once the document is parsed, as an inline <script> element - so
 * the page needs `'unsafe-inline'` in `script-src`, and no `'unsafe-eval'`. It
 * runs inside a function, as `new Function` ran it before: nothing it declares
 * lands on `window`. The unpacking is asynchronous, so the script starts
 * after the load event of the frame - when Visual Analytics has already posted
 * the message that asks for the Options pane, once: the loader keeps every
 * message that arrives before the script has run and dispatches it again
 * after. A browser without DecompressionStream (before 2023) gets a sentence
 * instead of an empty page.
 *
 * Plain ES5 text, because it is not bundled: it must hold neither `{{` nor
 * `}}` (Go template actions), nor a `$` (SAS Job Execution parameter tokens).
 */
const LOADER = [
  '(function () {',
  // VA posts the message that carries the resultName once, at the load event
  // of the frame - before the unpacked script has run and registered its
  // listener. The loader keeps what arrives meanwhile and hands it on.
  '  var early = [];',
  '  function keep(event) {',
  '    early.push(event);',
  '  }',
  "  window.addEventListener('message', keep);",
  "  var packed = Array.prototype.slice.call(document.querySelectorAll('script[type=\"" + PACKED_TYPE + "\"]'));",
  '  function say(text) {',
  '    function write() {',
  "      var note = document.createElement('p');",
  "      note.style.cssText = 'font-family: sans-serif; margin: 2rem;';",
  '      note.textContent = text;',
  '      document.body.replaceChildren(note);',
  '    }',
  "    if (document.body) write(); else document.addEventListener('DOMContentLoaded', write);",
  '  }',
  "  if (typeof DecompressionStream !== 'function' || typeof TextDecoder !== 'function') {",
  "    say('This page needs a current browser: Chrome, Edge, Firefox or Safari of 2023 or later.');",
  '    return;',
  '  }',
  '  function unpack(element) {',
  '    var binary = atob(element.textContent);',
  '    var bytes = new Uint8Array(binary.length);',
  '    for (var i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);',
  "    var text = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));",
  '    return new Response(text).arrayBuffer().then(function (buffer) {',
  "      return new TextDecoder('utf-8').decode(buffer);",
  '    });',
  '  }',
  '  var parsed = new Promise(function (resolve) {',
  "    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', resolve); else resolve();",
  '  });',
  '  Promise.all(packed.map(unpack)).then(function (texts) {',
  '    packed.forEach(function (element, index) {',
  "      if (element.getAttribute('data-kind') !== 'style') return;",
  "      var style = document.createElement('style');",
  '      style.textContent = texts[index];',
  '      element.parentNode.insertBefore(style, element);',
  '    });',
  '    return parsed.then(function () {',
  '      packed.forEach(function (element, index) {',
  "        if (element.getAttribute('data-kind') !== 'script') return;",
  "        var script = document.createElement('script');",
  "        script.textContent = '(function () {\\n' + texts[index] + '\\n})();';",
  '        element.parentNode.insertBefore(script, element);',
  '      });',
  "      window.removeEventListener('message', keep);",
  '      early.forEach(function (event) {',
  "        window.dispatchEvent(new MessageEvent('message', {",
  '          data: event.data,',
  '          origin: event.origin,',
  '          lastEventId: event.lastEventId,',
  '          source: event.source,',
  '          ports: Array.prototype.slice.call(event.ports || [])',
  '        }));',
  '      });',
  '    });',
  '  }).catch(function (error) {',
  "    console.error('The page could not be unpacked.', error);",
  "    say('The page could not be unpacked: ' + error);",
  '  });',
  '})();',
].join('\n');

/**
 * Gzip (level 9) and base64-encode the inline script and stylesheet into
 * packed elements, and put the loader after them. Base64 holds no brace and
 * no `$`, so neither the Go template engine of SAS Job Execution nor its
 * `$name$` tokens can touch the code; the build fails if `{{` or `}}` is
 * left anywhere in the page. fflate, not Node's zlib: its output is the same
 * on every platform and Node version, and CI compares the committed file
 * with a fresh build byte for byte (mtime 0 keeps the gzip header fixed).
 */
function goTemplateSafeScripts(): Plugin {
  const pack = (kind: 'script' | 'style', text: string): string =>
    `<script type="${PACKED_TYPE}" data-kind="${kind}">${Buffer.from(
      gzipSync(new TextEncoder().encode(text), { level: 9, mtime: 0 })
    ).toString('base64')}</script>`;
  return {
    name: 'go-template-safe-scripts',
    enforce: 'post',
    generateBundle(_options, bundle) {
      for (const file of Object.values(bundle)) {
        if (file.type === 'asset' && file.fileName.endsWith('.html')) {
          let html =
            typeof file.source === 'string'
              ? file.source
              : new TextDecoder().decode(file.source);
          // Normalize line endings (including lone carriage returns) so the
          // output is byte-identical regardless of the checkout's line endings
          // (CI compares the committed file against a fresh Linux build).
          html = html.replace(/\r\n?/g, '\n');
          // The bundler ends the inlined stylesheet with a marker comment,
          // /*$vite$:1*/, of no use once everything is inlined.
          html = html.replace(/<style[^>]*>([\s\S]*?)<\/style>/gi, (_match, css: string) =>
            pack('style', css.replace(/\/\*\$vite\$:\d+\*\//g, ''))
          );
          html = html.replace(/<script([^>]*)>([\s\S]*?)<\/script>/gi, (match, attrs: string, code: string) => {
            if (!code.trim() || /\bsrc\s*=/i.test(attrs) || attrs.includes(PACKED_TYPE)) return match;
            return pack('script', code);
          });
          html = html.replace('</head>', `<script>\n${LOADER}\n</script>\n</head>`);
          for (const action of ['{{', '}}']) {
            if (html.includes(action)) {
              this.error(`go-template-safe-scripts left "${action}" in ${file.fileName}: SAS Job Execution would take it for a template action`);
            }
          }
          file.source = html;
        }
      }
    },
  };
}

// Point this at your SAS Viya host so `npm run dev` can proxy the SAS Viya
// services the page calls (the SCR endpoint is an absolute URL and is not
// proxied). The list covers the prompt side and the RAG side.
const DEV_VIYA_HOST = 'https://your-viya-host.com';
const proxyPaths = [
  '/modelRepository',
  '/modelManagement',
  '/identities',
  '/files',
  '/folders',
  '/credentials',
  '/relationships',
  '/decisions',
  '/jobExecution',
  '/jobDefinitions',
  '/dataFlows',
  '/compute',
  '/casManagement',
];

export default defineConfig({
  base: './',
  // No public directory: a single-file build inlines everything (locales are
  // imported as modules, not fetched at runtime).
  publicDir: false,
  plugins: [normalizeTemplateEol(), inlineIntoHtml(), goTemplateSafeScripts()],
  build: {
    outDir: 'dist',
    // Inline every asset regardless of size so nothing is emitted as a
    // separate file alongside the HTML.
    assetsInlineLimit: () => true,
    // One stylesheet and one script, which inlineIntoHtml() puts into the page.
    cssCodeSplit: false,
    rolldownOptions: { output: { codeSplitting: false } },
    // The one script is the whole page; its size is no warning.
    chunkSizeWarningLimit: 100_000_000,
  },
  server: {
    proxy: Object.fromEntries(
      proxyPaths.map((path) => [
        path,
        { target: DEV_VIYA_HOST, changeOrigin: true, secure: false },
      ])
    ),
  },
});
