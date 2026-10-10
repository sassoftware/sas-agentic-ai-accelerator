/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Links that leave the page (SAS Model Manager, SAS Intelligent Decisioning).
 */

/**
 * Handle a click on a link to another SAS Viya application. Tries to open a
 * new browser tab; when running inside VA's sandboxed DDC iframe (no
 * 'allow-popups'), window.open is blocked, so the URL is copied to the
 * clipboard and `copiedText` is shown in the link for a moment instead. The
 * link keeps its href, so the browser's own right-click "Open link in new
 * tab" always works regardless.
 */
export function openExternalLink(event: MouseEvent, anchor: HTMLAnchorElement, copiedText: string): void {
  event.preventDefault();
  const url = anchor.href;

  const opened = window.open(url, '_blank', 'noopener,noreferrer');
  if (opened) return;

  // Popup blocked by the sandbox — copy the link and let the user open it.
  copyToClipboard(url);
  const original = anchor.innerHTML;
  anchor.textContent = copiedText;
  window.setTimeout(() => {
    anchor.innerHTML = original;
  }, 2500);
}

/** Copy text to the clipboard, falling back to a hidden-textarea + execCommand. */
function copyToClipboard(text: string): void {
  try {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      navigator.clipboard.writeText(text).catch(() => legacyCopyToClipboard(text));
      return;
    }
  } catch {
    /* fall through to the legacy path */
  }
  legacyCopyToClipboard(text);
}

function legacyCopyToClipboard(text: string): void {
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  try {
    document.execCommand('copy');
  } catch {
    /* clipboard unavailable — nothing more we can do */
  }
  document.body.removeChild(textarea);
}
