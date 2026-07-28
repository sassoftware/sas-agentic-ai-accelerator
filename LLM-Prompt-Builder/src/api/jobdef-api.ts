/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * SAS Job Definitions access for the RAG Builder: the generated ingestion job
 * is a jobDefinition whose code is the deployed Ingest-Documents.sas with the
 * setup's values as parameter defaults.
 */

import { viyaFetch } from './http-client';

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

/** Create a definition inside a SAS Content folder. */
export async function createJobDefinition(
  definition: JobDefinition,
  parentFolderId: string
): Promise<JobDefinition> {
  const response = await viyaFetch(
    `/jobDefinitions/definitions?parentFolderUri=/folders/folders/${parentFolderId}`,
    {
      method: 'POST',
      body: JSON.stringify(definition),
      contentType: DEFINITION_TYPE,
      accept: DEFINITION_TYPE,
    }
  );
  if (!response.ok) throw new Error(`Creating the job definition failed (HTTP ${response.status})`);
  return (await response.json()) as JobDefinition;
}

/** Full-replacement update of an existing definition (ETag-guarded). */
export async function updateJobDefinition(
  definitionUri: string,
  body: JobDefinition,
  etag: string | null
): Promise<JobDefinition> {
  const headers: Record<string, string> = {};
  if (etag) headers['If-Match'] = etag;
  const response = await viyaFetch(definitionUri, {
    method: 'PUT',
    body: JSON.stringify(body),
    contentType: DEFINITION_TYPE,
    accept: DEFINITION_TYPE,
    headers,
  });
  if (!response.ok) throw new Error(`Updating the job definition failed (HTTP ${response.status})`);
  return (await response.json()) as JobDefinition;
}
