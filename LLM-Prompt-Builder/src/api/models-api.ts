/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { statusOf, viyaFetch, viyaGet, viyaGetAll, viyaPost, ViyaRefused, writeOrReadBack } from './http-client';
import type {
  ModelProject,
  Model,
  ModelContent,
  ModelVariable,
  ModelRepository,
  ModelVersion,
  DropdownOption,
} from '../types/models';

/**
 * Get model projects with optional filter, shaped for dropdown options. Every
 * page of the listing is read (see viyaGetAll).
 */
export async function getModelProjects(query: string = ''): Promise<DropdownOption[]> {
  const items = await viyaGetAll<ModelProject>(`/modelRepository/projects?limit=50&filter=${query}`);
  return items.map((item) => ({ value: item.id, innerHTML: item.name, createdBy: item.createdBy, modifiedBy: item.modifiedBy }));
}

/**
 * Get models within a project, shaped for dropdown options. Every page of the
 * listing is read (see viyaGetAll).
 *
 * Each option also carries the model's `algorithm` and `tags` as the listing
 * returns them. That is what lets a caller tell kinds of model apart from
 * this one request - the Prompt Builder separates decision templates from
 * prompt templates by it - instead of asking once more per kind. A filter
 * that combines the two (`or(contains(tags,…),eq(algorithm,…))`) is not an
 * alternative: the service answers it with an empty list.
 */
export async function getModelProjectModels(projectID: string, query: string = ''): Promise<DropdownOption[]> {
  const items = await viyaGetAll<Model>(`/modelRepository/projects/${projectID}/models?limit=50&filter=${query}`);
  return items.map((item) => ({
    value: item.id,
    innerHTML: item.name,
    createdBy: item.createdBy,
    modifiedBy: item.modifiedBy,
    algorithm: typeof item.algorithm === 'string' ? item.algorithm : undefined,
    // A model without tags is listed with `tags: null`.
    tags: Array.isArray(item.tags) ? item.tags.map(String) : [],
  }));
}

/**
 * Create a new model project. When the creation gets no answer, the project
 * of that name is looked for in the repository (see writeOrReadBack).
 */
export async function createModelProject(
  projectDefinition: Record<string, unknown>
): Promise<ModelProject> {
  return writeOrReadBack(
    () => viyaPost<ModelProject>('/modelRepository/projects', projectDefinition),
    async () =>
      (await viyaGetAll<ModelProject>('/modelRepository/projects?limit=100')).find(
        (project) => project.name === projectDefinition.name
      )
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
 * Get all model repositories, shaped for dropdown options, the placeholder
 * first. Every page of the listing is read (see viyaGetAll).
 */
export async function getAllModelRepositories(placeholderText: string): Promise<DropdownOption[]> {
  const items = await viyaGetAll<ModelRepository>('/modelRepository/repositories?limit=20');
  return [{ value: '', innerHTML: placeholderText }, ...items.map((item) => ({ value: item.id, innerHTML: item.name }))];
}

/**
 * Create a new model.
 *
 * Throws when the service refuses the creation (a name that exists in the
 * project, no permission to write there): the answer is then an error body,
 * and a caller that read an id from it would go on with the model `undefined`.
 * When the creation gets no answer, the model of that name is looked for in
 * its project (see writeOrReadBack).
 */
export async function createModel(
  modelDefinition: Record<string, unknown>
): Promise<Model> {
  return writeOrReadBack(
    async () => {
      const response = await viyaFetch('/modelRepository/models', {
        method: 'POST',
        body: JSON.stringify(modelDefinition),
        contentType: 'application/vnd.sas.models.model+json',
      });
      if (!response.ok) throw new ViyaRefused('/modelRepository/models', response.status);
      return (await response.json()) as Model;
    },
    async () =>
      (await viyaGetAll<Model>(`/modelRepository/projects/${modelDefinition.projectId}/models?limit=100`)).find(
        (model) => model.name === modelDefinition.name
      )
  );
}

/**
 * Get contents of a model. Every page of the listing is read (see
 * viyaGetAll): beside its tracker a prompt-test can hold many contents, the
 * dataset snapshots of its optimization runs among them.
 */
export async function getModelContents(modelID: string): Promise<ModelContent[]> {
  return viyaGetAll<ModelContent>(`/modelRepository/models/${modelID}/contents?limit=100`);
}

/**
 * Create model content (file upload to a model). When the upload gets no
 * usable answer, the file of that name is read back: the upload was carried
 * out when it holds what was sent (see writeOrReadBack).
 */
export async function createModelContent(
  modelID: string,
  modelContent: unknown,
  modelContentFileName: string,
  modelContentRole: string = 'documentation',
  contentType: string = 'application/json'
): Promise<{ response: unknown; status_code: number }> {
  let file: Blob;
  if (contentType === 'multipart/form-data' && modelContent instanceof Uint8Array) {
    file = new Blob([modelContent as BlobPart], { type: 'application/octet-stream' });
  } else if (modelContent instanceof Blob) {
    file = modelContent;
  } else if (contentType === 'text/x-python' || contentType === 'text/plain' || contentType === 'text/markdown') {
    file = new Blob([modelContent as string], { type: contentType });
  } else {
    file = new Blob([JSON.stringify(modelContent)], { type: 'application/json' });
  }
  const formData = new FormData();
  formData.append('files', file, modelContentFileName);

  return writeOrReadBack(
    async () => {
      const response = await viyaFetch(
        `/modelRepository/models/${modelID}/contents?onConflict=update&role=${modelContentRole}`,
        {
          method: 'POST',
          body: formData,
          contentType: undefined,
        }
      );
      statusOf(response, `/modelRepository/models/${modelID}/contents`);
      const responseJson = await response.json();
      return { response: responseJson, status_code: response.status };
    },
    async () => {
      const stored = (await getModelContents(modelID)).find((content) => content.name === modelContentFileName);
      if (!stored?.fileUri) return undefined;
      const response = await viyaFetch(`${stored.fileUri}/content`, { accept: '*/*' });
      if (!response.ok) throw new ViyaRefused(`${stored.fileUri}/content`, response.status);
      return (await response.text()) === (await file.text()) ? { response: stored, status_code: 200 } : undefined;
    }
  );
}

/**
 * Delete model content. When the delete gets no answer, the contents are
 * read back (see writeOrReadBack).
 */
export async function deleteModelContent(
  modelID: string,
  contentID: string
): Promise<number> {
  return writeOrReadBack(
    async () => statusOf(await viyaFetch(`/modelRepository/models/${modelID}/contents/${contentID}`, { method: 'DELETE' }), `/modelRepository/models/${modelID}/contents/${contentID}`),
    async () => ((await getModelContents(modelID)).some((content) => content.id === contentID) ? undefined : 204)
  );
}

/**
 * Get model variables. Every page of the listing is read (see viyaGetAll).
 */
export async function getModelVariables(modelID: string): Promise<ModelVariable[]> {
  return viyaGetAll<ModelVariable>(`/modelRepository/models/${modelID}/variables?limit=1000`);
}

/**
 * Delete a model variable. When the delete gets no answer, the variables are
 * read back (see writeOrReadBack).
 */
export async function deleteModelVariable(
  modelID: string,
  variableID: string
): Promise<number> {
  return writeOrReadBack(
    async () => statusOf(await viyaFetch(`/modelRepository/models/${modelID}/variables/${variableID}`, { method: 'DELETE' }), `/modelRepository/models/${modelID}/variables/${variableID}`),
    async () => ((await getModelVariables(modelID)).some((variable) => variable.id === variableID) ? undefined : 204)
  );
}

/**
 * The read back of a delete: 204 when `path` is answered with 404, undefined
 * when it is still there; any other answer leaves it unknown.
 */
async function goneAfterDelete(path: string): Promise<number | undefined> {
  const response = await viyaFetch(path);
  if (response.status === 404) return 204;
  if (response.ok) return undefined;
  throw new ViyaRefused(path, response.status);
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
 * Shared by updateModelTags and updateModelAttributes. When the PUT gets no
 * answer, the model is read back: the update was carried out when `mutate`
 * changes nothing of what is stored (see writeOrReadBack).
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
  return writeOrReadBack(
    async () =>
      statusOf(
        await viyaFetch(`/modelRepository/models/${modelID}`, {
          method: 'PUT',
          body: JSON.stringify(model),
          contentType: 'application/vnd.sas.models.model+json',
          headers,
        }),
        `/modelRepository/models/${modelID}`
      ),
    async () => {
      const stored = await viyaGet<Record<string, unknown>>(`/modelRepository/models/${modelID}`, 'application/vnd.sas.models.model+json');
      const updated = JSON.parse(JSON.stringify(stored)) as Record<string, unknown>;
      mutate(updated);
      return JSON.stringify(updated) === JSON.stringify(stored) ? 200 : undefined;
    }
  );
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
 * Delete a model. When the delete gets no answer, the model is read back
 * (see writeOrReadBack).
 */
export async function deleteModel(modelID: string): Promise<number> {
  return writeOrReadBack(
    async () => statusOf(await viyaFetch(`/modelRepository/models/${modelID}`, { method: 'DELETE' }), `/modelRepository/models/${modelID}`),
    () => goneAfterDelete(`/modelRepository/models/${modelID}`)
  );
}

/**
 * Delete a model project. When the delete gets no answer, the project is
 * read back (see writeOrReadBack).
 */
export async function deleteModelProject(projectID: string): Promise<number> {
  return writeOrReadBack(
    async () => statusOf(await viyaFetch(`/modelRepository/projects/${projectID}`, { method: 'DELETE' }), `/modelRepository/projects/${projectID}`),
    () => goneAfterDelete(`/modelRepository/projects/${projectID}`)
  );
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
  const items = await viyaGetAll<Record<string, unknown>>(`/modelRepository/models/${modelID}/modelVersions?limit=100`);
  return items
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
 * an empty result means exactly that and is not a failure to report. Every
 * page of the listing is read (see viyaGetAll).
 */
export async function getTaggedModels(tag: string): Promise<TaggedModel[]> {
  const items = await viyaGetAll<Model>(
    `/modelRepository/models?limit=100&filter=${encodeURIComponent(`contains(tags,'${tag}')`)}`
  );
  return items.map((item) => ({
    id: String(item.id ?? ''),
    name: String(item.name ?? ''),
    projectId: String(item.projectId ?? ''),
    projectName: String(item.projectName ?? ''),
  }));
}

/**
 * Create a new model version. Throws when SAS Model Manager refuses it: the
 * error body of a refusal is JSON as well, and was handed back as if it were
 * the new version. A version has no name of its own to look for: the
 * versions are listed before the request, and when it gets no answer a
 * version that was not listed before is the one it created (see
 * writeOrReadBack). Without the first listing that cannot be told, and a
 * request without an answer is left unknown.
 */
export async function createModelVersion(
  modelID: string,
  versionUpdateType: string = 'minor'
): Promise<ModelVersion> {
  const before = await viyaGetAll<{ id?: string }>(`/modelRepository/models/${modelID}/modelVersions?limit=100`).catch(() => null);
  return writeOrReadBack(
    async () => {
      const response = await viyaFetch(`/modelRepository/models/${modelID}/modelVersions`, {
        method: 'POST',
        body: JSON.stringify({ Option: versionUpdateType }),
        contentType: 'application/vnd.sas.models.model.version+json',
      });
      if (!response.ok) throw new ViyaRefused(`/modelRepository/models/${modelID}/modelVersions`, response.status, `Creating a model version was refused with HTTP ${response.status}`);
      return (await response.json()) as ModelVersion;
    },
    async () => {
      if (!before) throw new Error('the versions before the request are not known');
      const after = await viyaGetAll<{ id?: string }>(`/modelRepository/models/${modelID}/modelVersions?limit=100`);
      return after.find((version) => !before.some((earlier) => earlier.id === version.id)) as ModelVersion | undefined;
    }
  );
}
