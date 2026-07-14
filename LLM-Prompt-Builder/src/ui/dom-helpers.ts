/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Common DOM helper utilities, including escaping primitives used to safely
 * interpolate untrusted (author/user-supplied) values into innerHTML.
 */

/**
 * Escape a value for safe interpolation into an HTML text or attribute context.
 * Always use this when building markup from data that originates outside the
 * application (e.g. data-products.json, prompt text, LLM responses).
 */
export function escapeHtml(value: unknown): string {
  const str = value === null || value === undefined ? '' : String(value);
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/**
 * Return a URL only if it uses a safe scheme (http, https, mailto) or is a
 * relative/protocol-relative reference. Otherwise return '#'. Blocks
 * javascript:, data:, vbscript: and similar script-bearing schemes.
 */
export function sanitizeUrl(value: unknown): string {
  const url = value === null || value === undefined ? '' : String(value).trim();
  if (url === '') return '#';
  // Reject control characters / whitespace tricks (e.g. "java\tscript:").
  if (/[\x00-\x1f\x7f]/.test(url)) return '#';
  // If the URL declares a scheme, only allow an explicit allow-list.
  if (/^[a-z][a-z0-9+.-]*:/i.test(url)) {
    return /^(?:https?|mailto):/i.test(url) ? url : '#';
  }
  // No scheme: relative or protocol-relative reference — safe.
  return url;
}

/**
 * Derive an HTML-id/selector-safe slug from an arbitrary string so that
 * untrusted names cannot break out of id/attribute/selector contexts.
 */
export function safeId(value: string): string {
  return value.replace(/[^a-zA-Z0-9_-]/g, '-');
}
