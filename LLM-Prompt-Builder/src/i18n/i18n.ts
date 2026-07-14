/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Resolve interface text from the bundled locale files based on the browser
 * language. Locales are imported (not fetched) so everything inlines into the
 * single-file build. Falls back to English for unsupported languages.
 *
 * To add a language: create `src/i18n/locales/<lang>.json` with the same shape
 * and add it to the LOCALES map below.
 */

import type { InterfaceText } from '../types';
import en from './locales/en.json';
import de from './locales/de.json';

const LOCALES: Record<string, unknown> = { en, de };

export function getInterfaceLanguage(): InterfaceText {
  const browserLanguage = navigator.language.split('-')[0] ?? 'en';
  const locale = LOCALES[browserLanguage] ?? LOCALES.en;
  return locale as InterfaceText;
}
