/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * SAS Job Definitions access for the RAG Builder: the generated ingestion job
 * is a jobDefinition whose code is the deployed Ingest-Documents.sas with the
 * setup's values as parameter defaults.
 */

import { statusOf, viyaFetch, viyaGetAll, ViyaRefused, writeOrReadBack } from './http-client';

const DEFINITION_TYPE = 'application/vnd.sas.job.definition+json';

export interface JobDefinitionParameter {
  version: 1;
  name: string;
  defaultValue: string;
  type: 'character';
  label?: string;
  required: false;
}

export interface JobDefinition {
  id?: string;
  name: string;
  type: 'Compute';
  code: string;
  parameters: JobDefinitionParameter[];
  description?: string;
  [key: string]: unknown;
}

export function jobParameter(
  name: string,
  defaultValue: string,
  label?: string
): JobDefinitionParameter {
  return { version: 1, name, defaultValue, type: 'character', label, required: false };
}

/** Read a definition by its URI (/jobDefinitions/definitions/<id>). */
export async function getJobDefinition(
  definitionUri: string
): Promise<{ body: JobDefinition; etag: string | null } | null> {
  const response = await viyaFetch(definitionUri, { accept: DEFINITION_TYPE });
  if (!response.ok) return null;
  return {
    body: (await response.json()) as JobDefinition,
    etag: response.headers.get('ETag'),
  };
}

/**
 * The definition at `definitionUri` as it is stored, or undefined when it is
 * not there: the read back of the writes below. Another answer leaves the
 * write unknown.
 */
async function storedDefinition(definitionUri: string): Promise<JobDefinition | undefined> {
  const response = await viyaFetch(definitionUri, { accept: DEFINITION_TYPE });
  if (response.status === 404) return undefined;
  if (!response.ok) throw new ViyaRefused(definitionUri, response.status);
  return (await response.json()) as JobDefinition;
}

/**
 * Whether a stored definition holds the name, the code and the parameter
 * values that were sent (the service may add fields of its own).
 */
const sameDefinition = (stored: JobDefinition, sent: JobDefinition): boolean =>
  stored.name === sent.name &&
  stored.code === sent.code &&
  (sent.parameters ?? []).every((parameter) =>
    (stored.parameters ?? []).some((kept) => kept.name === parameter.name && kept.defaultValue === parameter.defaultValue)
  );

/**
 * The definition that holds what `sent` holds, in a folder - or, for one that
 * belongs to no folder, among the definitions of its name - or undefined.
 */
async function definitionLike(sent: JobDefinition, parentFolderId: string | null): Promise<JobDefinition | undefined> {
  let uris: string[];
  if (parentFolderId === null) {
    // A quote in the name cannot be written into the filter.
    if (/['"\\]/.test(sent.name)) throw new Error('the definition cannot be looked for by its name');
    const named = await viyaGetAll<{ id?: string }>(
      `/jobDefinitions/definitions?limit=100&filter=${encodeURIComponent(`eq(name,'${sent.name}')`)}`
    );
    uris = named.map((definition) => `/jobDefinitions/definitions/${definition.id}`);
  } else {
    const members = await viyaGetAll<{ name?: string; uri?: string }>(`/folders/folders/${parentFolderId}/members?limit=200`);
    uris = members
      .filter((member) => member.name === sent.name && String(member.uri ?? '').startsWith('/jobDefinitions/definitions/'))
      .map((member) => String(member.uri));
  }
  for (const uri of uris) {
    const stored = await storedDefinition(uri);
    if (stored && sameDefinition(stored, sent)) return stored;
  }
  return undefined;
}

/**
 * Create a definition inside a SAS Content folder. When the creation gets no
 * answer, a definition that holds what was sent is looked for in the folder
 * (see writeOrReadBack).
 */
export async function createJobDefinition(
  definition: JobDefinition,
  parentFolderId: string
): Promise<JobDefinition> {
  return writeOrReadBack(
    async () => {
      const response = await viyaFetch(
        `/jobDefinitions/definitions?parentFolderUri=/folders/folders/${parentFolderId}`,
        {
          method: 'POST',
          body: JSON.stringify(definition),
          contentType: DEFINITION_TYPE,
          accept: DEFINITION_TYPE,
        }
      );
      if (!response.ok) throw new ViyaRefused('/jobDefinitions/definitions', response.status, `Creating the job definition failed (HTTP ${response.status})`);
      return (await response.json()) as JobDefinition;
    },
    () => definitionLike(definition, parentFolderId)
  );
}

/**
 * Create a definition that belongs to no folder.
 *
 * Omitting `parentFolderUri` is what makes the retrieval test leave nothing
 * behind: an unfiled definition never appears anywhere in SAS Content, and
 * deleting it afterwards removes the last trace. Verified against a live SAS
 * Viya - the job it launched runs normally, and its job record and log stay
 * readable after the definition is gone, so reading and deleting can happen in
 * either order.
 *
 * The apparent shortcut - passing the code inline in the job request instead
 * of creating anything - is a dead end: the request is accepted (201) but the
 * run dies with "unable to create a session" (500/5101), because no compute
 * context is resolved on that path.
 */
export async function createTransientJobDefinition(
  definition: JobDefinition
): Promise<JobDefinition> {
  return writeOrReadBack(
    async () => {
      const response = await viyaFetch('/jobDefinitions/definitions', {
        method: 'POST',
        body: JSON.stringify(definition),
        contentType: DEFINITION_TYPE,
        accept: DEFINITION_TYPE,
      });
      if (!response.ok) throw new ViyaRefused('/jobDefinitions/definitions', response.status, `Creating the job definition failed (HTTP ${response.status})`);
      return (await response.json()) as JobDefinition;
    },
    () => definitionLike(definition, null)
  );
}

/** Delete a definition. Returns the status so a caller can be quiet about a
 *  404 (already gone) while still noticing a 403. When the delete gets no
 *  answer, the definition is read back (see writeOrReadBack). */
export async function deleteJobDefinition(definitionUri: string): Promise<number> {
  return writeOrReadBack(
    async () => statusOf(await viyaFetch(definitionUri, { method: 'DELETE' }), definitionUri),
    async () => ((await storedDefinition(definitionUri)) ? undefined : 204)
  );
}

/** Full-replacement update of an existing definition (ETag-guarded). When the
 *  update gets no answer, the definition is read back (see writeOrReadBack). */
export async function updateJobDefinition(
  definitionUri: string,
  body: JobDefinition,
  etag: string | null
): Promise<JobDefinition> {
  const headers: Record<string, string> = {};
  if (etag) headers['If-Match'] = etag;
  return writeOrReadBack(
    async () => {
      const response = await viyaFetch(definitionUri, {
        method: 'PUT',
        body: JSON.stringify(body),
        contentType: DEFINITION_TYPE,
        accept: DEFINITION_TYPE,
        headers,
      });
      if (!response.ok) throw new ViyaRefused(definitionUri, response.status, `Updating the job definition failed (HTTP ${response.status})`);
      return (await response.json()) as JobDefinition;
    },
    async () => {
      const stored = await storedDefinition(definitionUri);
      return stored && sameDefinition(stored, body) ? stored : undefined;
    }
  );
}
