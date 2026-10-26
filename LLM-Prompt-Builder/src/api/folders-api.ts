/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Minimal SAS Content folders access for the RAG Builder: resolve a folder by
 * path, list its members, and ensure a child folder exists.
 */

import { viyaFetch, viyaGetAll, viyaPost, ViyaRefused, writeOrReadBack } from './http-client';

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

/**
 * List a folder's members (files, subfolders, job definitions, ...). Every
 * page of the listing is read (see viyaGetAll).
 */
export async function getFolderMembers(folderId: string): Promise<FolderMember[]> {
  return viyaGetAll<FolderMember>(`/folders/folders/${folderId}/members?limit=200`);
}

/**
 * Ensure `parentPath/name` exists and return its folder info. The parent must
 * already exist (for the RAG Builder that is the deployed content root). When
 * the creation gets no answer, the folder is looked for (see
 * writeOrReadBack).
 */
export async function ensureChildFolder(
  parentPath: string,
  name: string
): Promise<FolderInfo | null> {
  const existing = await getFolderByPath(`${parentPath}/${name}`);
  if (existing) return existing;
  const parent = await getFolderByPath(parentPath);
  if (!parent) return null;
  return writeOrReadBack(
    () => viyaPost<FolderInfo>(`/folders/folders?parentFolderUri=/folders/folders/${parent.id}`, { name }),
    async () => {
      const path = `/folders/folders/@item?path=${encodeURIComponent(`${parentPath}/${name}`)}`;
      const response = await viyaFetch(path);
      if (response.status === 404) return undefined;
      if (!response.ok) throw new ViyaRefused(path, response.status);
      return (await response.json()) as FolderInfo;
    }
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

/**
 * The folders directly below `path` in SAS Content, by name, for the folder
 * picker of a RAG setup; `/` lists the top folders (rootFolders, without the
 * hidden ones). null when the folder is not there or cannot be read: the
 * Folders service answers 404 for both. Only what the user may read is
 * listed.
 */
export async function listContentFolders(path: string): Promise<string[] | null> {
  const names = (items: { name?: string }[]): string[] =>
    items
      .map((item) => item.name ?? '')
      .filter((name) => name !== '')
      .sort((a, b) => a.localeCompare(b));
  if (path === '/' || path === '') {
    const roots = await viyaGetAll<FolderInfo>('/folders/rootFolders?limit=200');
    return names(roots.filter((root) => root.type !== 'hiddenFolder'));
  }
  const folder = await getFolderByPath(path);
  if (!folder) return null;
  const members = await viyaGetAll<FolderMember>(
    `/folders/folders/${folder.id}/members?filter=${encodeURIComponent("eq(contentType,'folder')")}&limit=200`
  );
  return names(members.filter((member) => member.contentType === 'folder'));
}
