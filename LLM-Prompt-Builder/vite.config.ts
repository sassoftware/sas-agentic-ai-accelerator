/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Vite build for the standalone LLM Prompt Builder.
 *
 * `npm run build` produces a single self-contained `dist/index.html`:
 *   - vite-plugin-singlefile inlines all JS and CSS into the one HTML file.
 *   - goTemplateSafeScripts() then base64-encodes every inline <script> body so
 *     the SAS Job Execution Go template engine does not choke on the `{{`/`}}`
 *     sequences that a minified bundle inevitably contains.
 *
 * That single file is uploaded as a SAS Job Execution definition and embedded in
 * a SAS Visual Analytics report. Requires `'unsafe-inline'` and `'unsafe-eval'`
 * in the Job Execution CSP (see README).
 */
import { defineConfig, type Plugin } from 'vite';
import { viteSingleFile } from 'vite-plugin-singlefile';

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

// Point this at your SAS Viya host so `npm run dev` can proxy Model Manager /
// identities API calls (the SCR endpoint is an absolute URL and is not proxied).
const DEV_VIYA_HOST = 'https://your-viya-host.com';
const proxyPaths = [
  '/modelRepository',
  '/modelManagement',
  '/identities',
  '/files',
  '/relationships',
  '/decisions',
];

export default defineConfig({
  base: './',
  // No public directory: a single-file build inlines everything (locales are
  // imported as modules, not fetched at runtime).
  publicDir: false,
  plugins: [normalizeTemplateEol(), viteSingleFile(), goTemplateSafeScripts()],
  build: {
    outDir: 'dist',
    // Inline every asset regardless of size so nothing is emitted as a
    // separate file alongside the HTML.
    assetsInlineLimit: 100_000_000,
    cssCodeSplit: false,
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
