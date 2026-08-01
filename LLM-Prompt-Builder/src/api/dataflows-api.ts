// Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * SAS Studio flows (.flw) as a SERVICE resource.
 *
 * The trap this module exists to avoid: a .flw uploaded to SAS Content as a
 * plain file lands there, shows the right icon and opens in the tree - and
 * then /studioDevelopment/code answers a bare HTTP 500 with no explanation,
 * because a flow is a `dataFlows` resource, not a file. Registering it
 * through this API is what makes it a flow rather than a document that looks
 * like one. (Same trap as the .step files.)
 */
import { viyaFetch } from './http-client';
import { getFolderByPath, getFolderMembers } from './folders-api';

const FLOW_MEDIA = 'application/vnd.sas.data.flow+json';

export interface StepPorts {
  name: string;
  /** control id -> its UI type, e.g. 'textfield' | 'path' | 'numberfield'. */
  controls: Record<string, string>;
  inputs: string[];
  outputs: string[];
}

/**
 * Display name -> live step id, for every custom step in `stepsFolder`.
 *
 * Resolved fresh every time and never cached into an artifact: a redeploy of
 * the steps mints new ids, and a flow holding a stale one fails at code
 * generation rather than at open, which is a long way from the cause.
 */
export async function resolveStepIds(stepsFolder: string): Promise<Record<string, string>> {
  const folder = await getFolderByPath(stepsFolder);
  if (!folder) return {};
  const found: Record<string, string> = {};
  for (const member of await getFolderMembers(folder.id)) {
    const uri = String(member.uri ?? '');
    if (uri.includes('/dataFlows/steps/')) {
      found[String(member.name ?? '').replace(/\.step$/, '')] = uri.replace(/\/+$/, '').split('/').pop() ?? '';
    }
  }
  return found;
}

/**
 * What a step actually offers, read from the step itself.
 *
 * Deriving this rather than hard-coding it means a step that gains a control
 * does not silently drop it from generated flows - the flow carries whatever
 * the deployed step declares.
 */
export async function readStepSpec(name: string, stepId: string): Promise<StepPorts> {
  const response = await viyaFetch(`/dataFlows/steps/${stepId}`, { accept: 'application/json' });
  if (!response.ok) throw new Error(`could not read the step ${name} (HTTP ${response.status})`);
  const body = (await response.json()) as Record<string, unknown>;
  // `ui` travels as a JSON STRING in a .step file and may arrive either way
  // from the service; both shapes are the same document.
  const rawUi = body.ui;
  const ui = (typeof rawUi === 'string' ? JSON.parse(rawUi) : rawUi ?? {}) as {
    pages?: { children?: UiItem[] }[];
  };
  const controls: Record<string, string> = {};
  // Sections and static text are layout, not arguments: a flow never carries
  // a value for them.
  const layoutOnly = new Set(['text', 'section']);
  const walk = (items: UiItem[] | undefined): void => {
    for (const item of items ?? []) {
      if (item.id && item.type && !layoutOnly.has(item.type)) controls[item.id] = item.type;
      walk(item.children);
    }
  };
  for (const page of ui.pages ?? []) walk(page.children);
  const flowMetadata = (body.flowMetadata ?? {}) as {
    inputPorts?: { name: string }[];
    outputPorts?: { name: string }[];
  };
  return {
    name,
    controls,
    inputs: (flowMetadata.inputPorts ?? []).map((port) => port.name),
    outputs: (flowMetadata.outputPorts ?? []).map((port) => port.name),
  };
}

interface UiItem {
  id?: string;
  type?: string;
  children?: UiItem[];
}

/**
 * Register a flow into `folderPath`, replacing one of the same name.
 *
 * Replacing rather than versioning is deliberate: the flow is generated from
 * the setup and is regenerable at any time, so an older copy is not history,
 * it is a stale duplicate that someone will eventually open by mistake.
 */
export async function registerFlow(
  folderPath: string,
  flow: Record<string, unknown>
): Promise<string> {
  const folder = await getFolderByPath(folderPath);
  if (!folder) throw new Error(`the folder ${folderPath} could not be reached`);
  for (const member of await getFolderMembers(folder.id)) {
    if (member.name === flow.name && String(member.uri ?? '').includes('/dataFlows/')) {
      await viyaFetch(String(member.uri), { method: 'DELETE' });
    }
  }
  // The service mints the id; sending ours is at best ignored and at worst a
  // collision with a flow someone else already registered.
  const body = { ...flow };
  delete body.id;
  const response = await viyaFetch(
    `/dataFlows/dataFlows?parentFolderUri=/folders/folders/${folder.id}`,
    {
      method: 'POST',
      accept: FLOW_MEDIA,
      contentType: FLOW_MEDIA,
      body: JSON.stringify(body),
    }
  );
  if (!response.ok) {
    throw new Error(`registering the flow failed (HTTP ${response.status})`);
  }
  return String(((await response.json()) as { id?: string }).id ?? '');
}
