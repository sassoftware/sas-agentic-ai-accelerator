/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Render an LLM markdown response to a safe, self-contained DOM element.
 *
 * The original portal used the `<zero-md>` web component loaded from a CDN. To
 * keep the single-file build fully self-contained (no external network), we
 * parse the markdown with `marked` and sanitize the resulting HTML with
 * DOMPurify before inserting it — so a response containing raw HTML/scripts
 * cannot execute.
 */

import { marked } from 'marked';
import DOMPurify from 'dompurify';

// GitHub-style line breaks match how chat responses are usually authored.
marked.setOptions({ breaks: true, gfm: true });

/**
 * Parse `markdown` and return a `<div class="markdown-body">` containing the
 * sanitized HTML.
 */
export function renderMarkdown(markdown: string): HTMLElement {
  const container = document.createElement('div');
  container.className = 'markdown-body';
  const rawHtml = marked.parse(markdown ?? '', { async: false }) as string;
  container.innerHTML = DOMPurify.sanitize(rawHtml);
  return container;
}
