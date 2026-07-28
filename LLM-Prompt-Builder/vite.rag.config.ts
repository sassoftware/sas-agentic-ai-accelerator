/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Vite build for the standalone RAG Builder (design §14).
 *
 * `npm run build:rag` produces a single self-contained `dist-rag/rag.html`
 * with the same treatment as the Prompt Builder build: vite-plugin-singlefile
 * inlines all JS and CSS, then every inline <script> body is base64-encoded so
 * the SAS Job Execution Go template engine does not choke on `{{`/`}}`
 * sequences in the minified bundle.
 *
 * The two plugins are intentionally duplicated from vite.config.ts for now —
 * extracting them into a shared module would touch the Prompt Builder build,
 * whose committed dist is byte-compared in CI. Dedup when both builds change
 * together anyway.
 */
import { resolve } from 'node:path';
import { defineConfig, type Plugin } from 'vite';
import { viteSingleFile } from 'vite-plugin-singlefile';

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
          html = html.replace(/\r\n?/g, '\n');
          html = html.replace(
            /<script([^>]*)>([\s\S]*?)<\/script>/gi,
            (match, attrs: string, code: string) => {
              if (!code.trim() || /\bsrc\s*=/i.test(attrs)) return match;
              const encoded = Buffer.from(code, 'utf-8').toString('base64');
              return `<script>document.addEventListener("DOMContentLoaded",function(){new Function(new TextDecoder().decode(Uint8Array.from(atob("${encoded}"),function(c){return c.charCodeAt(0)})))()});</script>`;
            }
          );
          file.source = html;
        }
      }
    },
  };
}

const DEV_VIYA_HOST = 'https://your-viya-host.com';
const proxyPaths = [
  '/modelRepository',
  '/modelManagement',
  '/identities',
  '/files',
  '/folders',
  '/credentials',
  '/jobExecution',
  '/casManagement',
];

export default defineConfig({
  base: './',
  publicDir: false,
  plugins: [normalizeTemplateEol(), viteSingleFile(), goTemplateSafeScripts()],
  build: {
    outDir: 'dist-rag',
    assetsInlineLimit: 100_000_000,
    cssCodeSplit: false,
    rollupOptions: {
      input: resolve(__dirname, 'rag.html'),
    },
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
