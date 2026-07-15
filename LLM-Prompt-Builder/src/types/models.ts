/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Minimal SAS Model Manager / Files API response shapes used by the
 * Prompt Builder. Only the fields that are actually read are typed; a permissive
 * index signature keeps the rest available without over-constraining.
 */

/** A generic SAS API paged collection. */
export interface SasApiCollection<T> {
  items?: T[];
  count?: number;
  start?: number;
  limit?: number;
  [key: string]: unknown;
}

/** A `<select>` option shape ({ value, innerHTML }) plus filter metadata. */
export interface DropdownOption {
  value: string;
  innerHTML: string;
  createdBy?: string;
  modifiedBy?: string;
  [key: string]: unknown;
}

export interface ModelProject {
  id: string;
  name: string;
  createdBy?: string;
  modifiedBy?: string;
  [key: string]: unknown;
}

export interface Model {
  id: string;
  name: string;
  createdBy?: string;
  modifiedBy?: string;
  items?: Array<{ id: string; name: string; [key: string]: unknown }>;
  [key: string]: unknown;
}

export interface ModelContent {
  id?: string;
  name?: string;
  fileUri?: string;
  [key: string]: unknown;
}

export interface ModelVariable {
  id?: string;
  name?: string;
  [key: string]: unknown;
}

export interface ModelRepository {
  id: string;
  name: string;
  folderId?: string;
  [key: string]: unknown;
}

export interface ModelVersion {
  id?: string;
  [key: string]: unknown;
}

/** Currently authenticated user (subset). */
export interface UserInfo {
  id?: string;
  name?: string;
  [key: string]: unknown;
}
