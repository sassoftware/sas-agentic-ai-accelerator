// Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * Reading an on/off VA option, whichever way it was stored.
 *
 * A VA `boolean` option stores a real `true`/`false`; the Yes/No dropdowns
 * these replaced stored `'1'`/`'0'`; and a URL override arrives as a string
 * whatever the option type says. All three reach the same config object.
 *
 * The reason this is a shared function rather than an inline test: the
 * obvious `value !== '0'` reads an unticked checkbox (`false`) as TRUE, so
 * every naive check fails in the permissive direction — silently enabling
 * something the deployment switched off. Getting that wrong once is easy;
 * getting it wrong in five places is what a helper prevents.
 */
export function optionFlag(value: unknown, fallback: boolean): boolean {
  if (value === undefined || value === null || value === '') return fallback;
  if (typeof value === 'boolean') return value;
  const text = String(value).trim().toLowerCase();
  return text !== '0' && text !== 'false' && text !== 'no';
}
