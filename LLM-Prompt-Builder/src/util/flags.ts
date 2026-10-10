/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

const ON_WORDS = ['true', '1', 'yes', 'on'];
const OFF_WORDS = ['false', '0', 'no', 'off'];

/**
 * Read a feature switch (decision templates, prompt optimization, RAG).
 *
 * Strict in both directions: only a value that plainly says on switches a
 * feature on, only one that plainly says off switches it off, and everything
 * else - absent, blank, a typo, the text "undefined" a template left behind -
 * is the fallback. A switch decides whether a whole part of the page exists
 * and whether its requests are sent, so an unreadable value must never be
 * taken as a yes, and a report saved before a switch existed (which carries
 * no value for it) must get that switch's default.
 *
 * The words are matched without regard to case or surrounding blanks, because
 * the value arrives as URL text that a person may have typed.
 */
export function featureFlag(value: unknown, fallback: boolean): boolean {
  if (typeof value === 'boolean') return value;
  if (value === undefined || value === null) return fallback;
  const word = String(value).trim().toLowerCase();
  if (ON_WORDS.includes(word)) return true;
  if (OFF_WORDS.includes(word)) return false;
  return fallback;
}
