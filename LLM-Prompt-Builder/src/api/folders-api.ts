/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Minimal SAS Content folders access for the RAG Builder: resolve a folder by
 * path, list its members, and ensure a child folder exists.
 */

import { viyaFetch, viyaGet, viyaPost } from './http-client';

export interface FolderInfo {
  id: string;
  name?: string;
  [key: string]: unknown;
}

export interface FolderMember {
  name?: string;
  uri?: string;
  contentType?: string;
  [key: string]: unknown;
}

/** Resolve a SAS Content folder by its path; null when it does not exist. */
export async function getFolderByPath(path: string): Promise<FolderInfo | null> {
  const response = await viyaFetch(
    `/folders/folders/@item?path=${encodeURIComponent(path)}`
  );
  if (!response.ok) return null;
  return (await response.json()) as FolderInfo;
}

/** List a folder's members (files, subfolders, job definitions, ...). */
export async function getFolderMembers(
  folderId: string,
  limit = 200
): Promise<FolderMember[]> {
  const data = await viyaGet<{ items?: FolderMember[] }>(
    `/folders/folders/${folderId}/members?limit=${limit}`
  );
  return data?.items ?? [];
}

/**
 * Ensure `parentPath/name` exists and return its folder info. The parent must
 * already exist (for the RAG Builder that is the deployed content root).
 */
export async function ensureChildFolder(
  parentPath: string,
  name: string
): Promise<FolderInfo | null> {
  const existing = await getFolderByPath(`${parentPath}/${name}`);
  if (existing) return existing;
  const parent = await getFolderByPath(parentPath);
  if (!parent) return null;
  return viyaPost<FolderInfo>(
    `/folders/folders?parentFolderUri=/folders/folders/${parent.id}`,
    { name }
  );
}

/**
 * Ensure a whole `/a/b/c` path exists, creating the missing tail.
 *
 * `ensureChildFolder` needs its parent to exist already, which is fine for a
 * fixed deployment folder but not for a destination the user types. Walking
 * the path lets someone name a folder two levels below anything that exists
 * without first going to SAS Content to create it by hand.
 *
 * The FIRST segment is never created: it is a top-level SAS Content area
 * (/Public, /Users, an application root) whose creation is an administrative
 * act, and silently creating a sibling of /Public because of a typo would be
 * worse than the error.
 */
export async function ensureFolderPath(path: string): Promise<FolderInfo | null> {
  const segments = String(path || '')
    .split('/')
    .map((segment) => segment.trim())
    .filter(Boolean);
  if (segments.length === 0) return null;
  const rootPath = `/${segments[0]}`;
  let current = await getFolderByPath(rootPath);
  if (!current) return null;
  let walked = rootPath;
  for (const segment of segments.slice(1)) {
    const next = await ensureChildFolder(walked, segment);
    if (!next) return null;
    current = next;
    walked = `${walked}/${segment}`;
  }
  return current;
}
