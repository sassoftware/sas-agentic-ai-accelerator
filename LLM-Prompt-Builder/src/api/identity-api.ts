/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { viyaGet } from './http-client';
import type { UserInfo } from '../types/models';

/**
 * Get information about the currently authenticated SAS Viya user. Used to stamp
 * the creator on newly created prompts. Returns undefined on failure so the app
 * can continue without a user id.
 */
export async function getUserInfo(): Promise<UserInfo | undefined> {
  try {
    return await viyaGet<UserInfo>('/identities/users/@currentUser');
  } catch {
    console.log('Unable to retrieve user information');
    return undefined;
  }
}
