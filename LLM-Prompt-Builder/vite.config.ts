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
 *   - goTemplateSafeScripts() then base64-encodes every inline <script> body so
 *     the SAS Job Execution Go template engine does not choke on the `{{`/`}}`
 *     sequences that a minified bundle inevitably contains, and removes the
 *     bundler's `$vite$` marker comment from the stylesheet, which SAS Job
 *     Execution would take for a parameter token.
 *
 * That single file is uploaded as a SAS Job Execution definition and embedded in
 * a SAS Visual Analytics report. Requires `'unsafe-inline'` and `'unsafe-eval'`
 * in the Job Execution CSP (see README).
 */
import { defineConfig, type Plugin } from 'vite';

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
 * Base64-encode every inline <script> so its content survives Go template
 * processing, then decode-and-run it on DOMContentLoaded via `new Function`.
 * Scripts with a `src` attribute (there should be none after inlining) and
 * empty scripts are left untouched.
 */
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

function goTemplateSafeScripts(): Plugin {
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
          // /*$vite$:1*/. SAS Job Execution serves this page as the form of a
          // job definition and replaces text between two dollar signs that
          // reads like a name by the value of that parameter - by nothing
          // when there is none. Inside a comment that cuts no code, but the
          // served page then differs from the built one, and the token scan
          // of the verify suites would have to make an exception for it. The
          // marker has no use once everything is inlined, so it is removed -
          // from the stylesheets only; the scripts are left as they are.
          html = html.replace(
            /<style([^>]*)>([\s\S]*?)<\/style>/gi,
            (_match, attrs: string, css: string) =>
              `<style${attrs}>${css.replace(/\/\*\$vite\$:\d+\*\//g, '')}</style>`
          );
          html = html.replace(
            /<script([^>]*)>([\s\S]*?)<\/script>/gi,
            (match, attrs: string, code: string) => {
              if (!code.trim() || /\bsrc\s*=/i.test(attrs)) return match;
              const encoded = Buffer.from(code, 'utf-8').toString('base64');
              // Decode base64 -> bytes -> UTF-8 text so non-ASCII source (the
              // German locale's umlauts, em dashes, etc.) is reconstructed
              // correctly. A bare atob() maps each byte to a Latin-1 code point
              // and mangles any multi-byte UTF-8 sequence.
              return `<script>document.addEventListener("DOMContentLoaded",function(){new Function(new TextDecoder().decode(Uint8Array.from(atob("${encoded}"),function(c){return c.charCodeAt(0)})))()});</script>`;
            }
          );
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
