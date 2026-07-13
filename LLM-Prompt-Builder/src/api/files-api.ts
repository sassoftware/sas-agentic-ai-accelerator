/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { viyaGetRaw } from './http-client';

/**
 * Get the content of a file by its URI. Returns the raw Response for the caller
 * to parse (e.g. `.json()`).
 */
export async function getFileContent(
  fileURI: string,
  contentType: string = 'application/json'
): Promise<Response> {
  try {
    return await viyaGetRaw(`${fileURI}/content`, contentType);
  } catch {
    console.log(`The call to ${fileURI}/content was unsuccessful`);
    return new Response(null, { status: 500 });
  }
}
