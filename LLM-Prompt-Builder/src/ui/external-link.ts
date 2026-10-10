/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Links that leave the page (SAS Model Manager, SAS Intelligent Decisioning).
 */

/**
 * The links that currently show `copiedText` instead of their own label: the
 * label to put back, and the timer that will do it.
 */
const copiedNotes = new WeakMap<HTMLAnchorElement, { label: string; timer: number }>();

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

  // No features string. With "noopener" in it window.open answers null by
  // specification, also when the tab DID open - every click then looked like
  // a blocked popup, and the link was copied over whatever the clipboard held
  // although the tab was there. Without it the answer says what happened: a
  // window where a tab opened, null where the sandbox refused one. The new
  // tab is cut off from this page by clearing its opener instead.
  let opened: Window | null = null;
  try {
    opened = window.open(url, '_blank');
  } catch {
    opened = null;
  }
  if (opened) {
    try {
      opened.opener = null;
    } catch {
      /* a window that does not let its opener be set has none to clear */
    }
    return;
  }

  // Popup blocked by the sandbox — copy the link and let the user open it.
  copyToClipboard(url);
  // The label is taken once. A second click while the note is shown found
  // the note in the link, kept it as "the label", and wrote it back for good
  // after the first click's timer had restored the real one.
  const shown = copiedNotes.get(anchor);
  if (shown) window.clearTimeout(shown.timer);
  const label = shown ? shown.label : anchor.innerHTML;
  anchor.textContent = copiedText;
  const timer = window.setTimeout(() => {
    copiedNotes.delete(anchor);
    anchor.innerHTML = label;
  }, 2500);
  copiedNotes.set(anchor, { label, timer });
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
