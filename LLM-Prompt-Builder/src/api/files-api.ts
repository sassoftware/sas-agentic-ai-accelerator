/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { viyaGetRaw } from './http-client';

/**
 * Get the content of a file by its URI. Returns the raw Response for the caller
 * to parse (e.g. `.json()`).
 *
 * Throws when the file could not be read - no answer, or an answer that is no
 * success - so that a read that failed is never taken for the file: as a file
 * without runs, a setup with the defaults, or a job whose code is empty or
 * the text of an error page.
 */
export async function getFileContent(
  fileURI: string,
  contentType: string = 'application/json'
): Promise<Response> {
  const response = await viyaGetRaw(`${fileURI}/content`, contentType);
  if (!response.ok) throw new Error(`The read of ${fileURI}/content was answered with HTTP ${response.status}`);
  return response;
}
