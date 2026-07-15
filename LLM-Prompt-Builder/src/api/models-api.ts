/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { viyaFetch, viyaGet, viyaPost } from './http-client';
import type {
  ModelProject,
  Model,
  ModelContent,
  ModelVariable,
  ModelRepository,
  ModelVersion,
  SasApiCollection,
  DropdownOption,
} from '../types/models';

/**
 * Get model projects with optional filter and pagination, shaped for dropdown options.
 */
export async function getModelProjects(
  query: string = '',
  start: number = 0,
  limit: number = 50
): Promise<DropdownOption[]> {
  const data = await viyaGet<SasApiCollection<ModelProject>>(
    `/modelRepository/projects?start=${start}&limit=${limit}&filter=${query}`
  );

  let options: DropdownOption[] = [];
  if (data?.items) {
    for (const item of data.items) {
      options.push({ value: item.id, innerHTML: item.name });
    }
  }

  if (data?.items && data.items.length === limit) {
    const more = await getModelProjects(query, start + limit, limit);
    options = options.concat(more);
  }

  return options;
}

/**
 * Get models within a project.
 */
export async function getModelProjectModels(
  projectID: string,
  query: string = '',
  start: number = 0,
  limit: number = 50
): Promise<DropdownOption[]> {
  const data = await viyaGet<SasApiCollection<Model>>(
    `/modelRepository/projects/${projectID}/models?start=${start}&limit=${limit}&filter=${query}`
  );

  let options: DropdownOption[] = [];
  if (data?.items) {
    for (const item of data.items) {
      options.push({ value: item.id, innerHTML: item.name });
    }
  }

  if (data?.items && data.items.length === limit) {
    const more = await getModelProjectModels(
      projectID,
      query,
      start + limit,
      limit
    );
    options = options.concat(more);
  }

  return options;
}

/**
 * Create a new model project.
 */
export async function createModelProject(
  projectDefinition: Record<string, unknown>
): Promise<ModelProject> {
  return viyaPost<ModelProject>(
    '/modelRepository/projects',
    projectDefinition
  );
}

/**
 * Get model repository information.
 */
export async function getModelRepositoryInformation(
  modelRepositoryID: string
): Promise<ModelRepository> {
  return viyaGet<ModelRepository>(
    `/modelRepository/repositories/${modelRepositoryID}`
  );
}

/**
 * Get all model repositories, shaped for dropdown options.
 */
export async function getAllModelRepositories(
  placeholderText: string,
  start: number = 0,
  limit: number = 20,
  first: boolean = true
): Promise<DropdownOption[]> {
  const data = await viyaGet<SasApiCollection<ModelRepository>>(
    `/modelRepository/repositories?start=${start}&limit=${limit}`
  );

  let options: DropdownOption[] = [];
  if (first) {
    options.push({ value: '', innerHTML: placeholderText });
  }

  if (data?.items) {
    for (const item of data.items) {
      options.push({ value: item.id, innerHTML: item.name });
    }
  }

  if (data?.items && data.items.length === limit) {
    const more = await getAllModelRepositories(
      placeholderText,
      start + limit,
      limit,
      false
    );
    options = options.concat(more);
  }

  return options;
}

/**
 * Create a new model.
 */
export async function createModel(
  modelDefinition: Record<string, unknown>
): Promise<Model> {
  const response = await viyaFetch('/modelRepository/models', {
    method: 'POST',
    body: JSON.stringify(modelDefinition),
    contentType: 'application/vnd.sas.models.model+json',
  });
  return response.json();
}

/**
 * Get contents of a model.
 */
export async function getModelContents(
  modelID: string,
  start: number = 0,
  limit: number = 100
): Promise<ModelContent[]> {
  const data = await viyaGet<SasApiCollection<ModelContent>>(
    `/modelRepository/models/${modelID}/contents?start=${start}&limit=${limit}`
  );
  return data?.items ?? [];
}

/**
 * Create model content (file upload to a model).
 */
export async function createModelContent(
  modelID: string,
  modelContent: unknown,
  modelContentFileName: string,
  modelContentRole: string = 'documentation',
  contentType: string = 'application/json'
): Promise<{ response: unknown; status_code: number }> {
  const formData = new FormData();

  if (contentType === 'multipart/form-data' && modelContent instanceof Uint8Array) {
    formData.append(
      'files',
      new Blob([modelContent as BlobPart], { type: 'application/octet-stream' }),
      modelContentFileName
    );
  } else if (modelContent instanceof Blob) {
    formData.append(
      'files',
      modelContent,
      modelContentFileName
    );
  } else if (contentType === 'text/x-python' || contentType === 'text/plain' || contentType === 'text/markdown') {
    formData.append(
      'files',
      new Blob([modelContent as string], { type: contentType }),
      modelContentFileName
    );
  } else {
    formData.append(
      'files',
      new Blob([JSON.stringify(modelContent)], {
        type: 'application/json',
      }),
      modelContentFileName
    );
  }

  const response = await viyaFetch(
    `/modelRepository/models/${modelID}/contents?onConflict=update&role=${modelContentRole}`,
    {
      method: 'POST',
      body: formData,
      contentType: undefined,
    }
  );

  const responseJson = await response.json();
  return { response: responseJson, status_code: response.status };
}

/**
 * Delete model content.
 */
export async function deleteModelContent(
  modelID: string,
  contentID: string
): Promise<number> {
  const response = await viyaFetch(
    `/modelRepository/models/${modelID}/contents/${contentID}`,
    { method: 'DELETE' }
  );
  return response.status;
}

/**
 * Get model variables.
 */
export async function getModelVariables(
  modelID: string,
  start: number = 0,
  limit: number = 1000
): Promise<ModelVariable[]> {
  const data = await viyaGet<SasApiCollection<ModelVariable>>(
    `/modelRepository/models/${modelID}/variables?start=${start}&limit=${limit}`
  );
  return data?.items ?? [];
}

/**
 * Delete a model variable.
 */
export async function deleteModelVariable(
  modelID: string,
  variableID: string
): Promise<number> {
  const response = await viyaFetch(
    `/modelRepository/models/${modelID}/variables/${variableID}`,
    { method: 'DELETE' }
  );
  return response.status;
}

/**
 * Delete a model.
 */
export async function deleteModel(modelID: string): Promise<number> {
  const response = await viyaFetch(`/modelRepository/models/${modelID}`, {
    method: 'DELETE',
  });
  return response.status;
}

/**
 * Delete a model project.
 */
export async function deleteModelProject(projectID: string): Promise<number> {
  const response = await viyaFetch(`/modelRepository/projects/${projectID}`, {
    method: 'DELETE',
  });
  return response.status;
}

/**
 * Create a new model version.
 */
export async function createModelVersion(
  modelID: string,
  versionUpdateType: string = 'minor'
): Promise<ModelVersion> {
  const response = await viyaFetch(
    `/modelRepository/models/${modelID}/modelVersions`,
    {
      method: 'POST',
      body: JSON.stringify({ Option: versionUpdateType }),
      contentType: 'application/vnd.sas.models.model.version+json',
    }
  );
  return response.json();
}
