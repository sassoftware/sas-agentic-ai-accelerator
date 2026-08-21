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
      options.push({ value: item.id, innerHTML: item.name, createdBy: item.createdBy, modifiedBy: item.modifiedBy });
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
      options.push({ value: item.id, innerHTML: item.name, createdBy: item.createdBy, modifiedBy: item.modifiedBy });
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
 * Get a model's full detail (all top-level attributes, e.g. function,
 * llmodelType, provider, deploymentId, inputTokenCount, outputTokenCount,
 * hostingCosts, endPoint, modelPurpose, …). Returns null when the model
 * cannot be read.
 */
export async function getModelDetails(
  modelID: string
): Promise<Record<string, unknown> | null> {
  const response = await viyaFetch(`/modelRepository/models/${modelID}`, {
    accept: 'application/vnd.sas.models.model+json',
  });
  if (!response.ok) return null;
  return (await response.json()) as Record<string, unknown>;
}

/**
 * Read a model (for its ETag + body), apply `mutate` to the body in place, then
 * PUT it back with If-Match. Returns the status of the failing GET or of the PUT.
 * Shared by updateModelTags and updateModelAttributes.
 */
async function patchModel(
  modelID: string,
  mutate: (model: Record<string, unknown>) => void
): Promise<number> {
  const getResponse = await viyaFetch(`/modelRepository/models/${modelID}`, {
    accept: 'application/vnd.sas.models.model+json',
  });
  if (!getResponse.ok) return getResponse.status;
  const model = (await getResponse.json()) as Record<string, unknown>;
  const etag = getResponse.headers.get('ETag');
  mutate(model);
  const headers: Record<string, string> = {};
  if (etag) headers['If-Match'] = etag;
  const putResponse = await viyaFetch(`/modelRepository/models/${modelID}`, {
    method: 'PUT',
    body: JSON.stringify(model),
    contentType: 'application/vnd.sas.models.model+json',
    headers,
  });
  return putResponse.status;
}

/**
 * Update a model's tags: reads the model (for its ETag and current tags),
 * removes the given tags, appends the new ones and PUTs the model back.
 * Returns the HTTP status of the failing GET or of the PUT.
 */
export async function updateModelTags(
  modelID: string,
  removeTags: string[],
  addTags: string[]
): Promise<number> {
  return patchModel(modelID, (model) => {
    const currentTags = Array.isArray(model.tags) ? (model.tags as string[]) : [];
    model.tags = [
      ...currentTags.filter((tag) => !removeTags.includes(tag) && !addTags.includes(tag)),
      ...addTags,
    ];
  });
}

/**
 * Shallow-merge a set of top-level attributes onto a model and PUT it back
 * (ETag-guarded). Skips keys whose value is undefined so callers can pass a
 * partial object built from optional fields. Returns the GET/PUT status.
 */
export async function updateModelAttributes(
  modelID: string,
  attributes: Record<string, unknown>
): Promise<number> {
  return patchModel(modelID, (model) => {
    for (const [key, value] of Object.entries(attributes)) {
      if (value !== undefined) model[key] = value;
    }
  });
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
 * A model's versions, oldest first.
 *
 * `modelVersionName` is the major.minor a person recognises — but it is NOT
 * unique: two versions labelled 1.0 were seen on one live model, so the id is
 * the identity and the label is only for display. Reading a version's CONTENT
 * is a different endpoint again (`/models/{id}/history/{versionId}/contents`);
 * `/models/{versionId}/contents` answers 200 with an empty collection, which
 * reads like a version that carries nothing.
 */
export async function getModelVersions(
  modelID: string
): Promise<Array<{ id: string; label: string; created: string }>> {
  const data = await viyaGet<SasApiCollection<Record<string, unknown>>>(
    `/modelRepository/models/${modelID}/modelVersions?limit=100`
  );
  return (data?.items ?? [])
    .map((item) => ({
      id: String(item.id ?? ''),
      label: String(item.modelVersionName ?? ''),
      created: String(item.creationTimeStamp ?? ''),
    }))
    .filter((version) => version.id)
    .sort((left, right) => left.created.localeCompare(right.created));
}

/** A tagged model, with the project it lives in. */
export interface TaggedModel {
  id: string;
  name: string;
  projectId: string;
  projectName: string;
}

/**
 * Every model carrying a tag, across all projects, newest listing order.
 *
 * Probed live 2026-08-03 against 396 models: `/modelRepository/models`
 * accepts the same `contains(tags,'…')` filter the project listing uses, and
 * every item carries `projectId` and `projectName`. So ONE call answers both
 * "which prompts qualify" and "which projects have any" — which is what lets
 * the RAG Builder hide a project holding no usable prompt without asking the
 * server about each project in turn.
 *
 * A tag nothing carries answers 200 with `count: 0` rather than an error, so
 * an empty result means exactly that and is not a failure to report.
 */
export async function getTaggedModels(
  tag: string,
  start: number = 0,
  limit: number = 100
): Promise<TaggedModel[]> {
  const data = await viyaGet<SasApiCollection<Model>>(
    `/modelRepository/models?start=${start}&limit=${limit}` +
      `&filter=${encodeURIComponent(`contains(tags,'${tag}')`)}`
  );

  let models: TaggedModel[] = (data?.items ?? []).map((item) => ({
    id: String(item.id ?? ''),
    name: String(item.name ?? ''),
    projectId: String(item.projectId ?? ''),
    projectName: String(item.projectName ?? ''),
  }));

  if (data?.items && data.items.length === limit) {
    models = models.concat(await getTaggedModels(tag, start + limit, limit));
  }

  return models;
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
