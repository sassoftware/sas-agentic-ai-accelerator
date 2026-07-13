/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { viyaFetch } from './http-client';

interface SCRInput {
  name: string;
  value: string | number;
}

/**
 * Get SCR metadata (inputs and outputs) from the OpenAPI endpoint.
 */
export async function getSCRMetadata(
  endpoint: string
): Promise<[Record<string, unknown>, Record<string, unknown>] | [string]> {
  try {
    const response = await fetch(`${endpoint}/apiMeta/api`);
    if (!response.ok) {
      return [
        `Request for ${endpoint} failed with the HTTP code: ${response.status} - please check the SCR endpoint.`,
      ];
    }
    const data = await response.json();
    const definitions = data?.definitions ?? data?.components?.schemas ?? {};
    // Drill down to the field map: definitions.PCRInput.properties.data.properties
    const inputs = definitions?.PCRInput?.properties?.data?.properties ?? {};
    const outputs = definitions?.PCROutput?.properties?.data?.properties ?? {};
    return [inputs, outputs];
  } catch {
    return ['Error fetching SCR metadata'];
  }
}

/**
 * Score using an SCR endpoint.
 */
export async function scoreSCR(
  scrEndpoint: string,
  scrInput: SCRInput[]
): Promise<unknown> {
  const response = await viyaFetch(scrEndpoint, {
    method: 'POST',
    body: JSON.stringify({ inputs: scrInput }),
  });

  if (!response.ok && response.status === 400) {
    const errorText = await response.text();
    window.alert(errorText);
    return {};
  }

  return response.json();
}

/**
 * Call an LLM deployed via SCR.
 */
export async function callSCRLLM(
  scrEndpoint: string,
  model: string,
  systemPrompt: string,
  userPrompt: string,
  options: Record<string, unknown> = {},
  deploymentType: string = 'k8s'
): Promise<unknown> {
  let llmURL: string;
  if (deploymentType === 'aca') {
    llmURL = `https://${model.replaceAll('_', '-')}.${scrEndpoint}/${model}`;
  } else {
    llmURL = `${scrEndpoint}/${model}/${model}`;
  }

  // The LLM containers expect the model parameters as a SINGLE `options` string
  // input in the form `{key:value,key:value}` — no quotes, no spaces — including
  // API_KEY when the model requires one. See the LLM score code (baseScore.py /
  // claudeSonnet35Score.py) and the SCR-LLM-Calls Postman collection in the
  // accelerator for the exact contract.
  const optionsString =
    '{' +
    Object.entries(options)
      .map(([key, value]) => `${key}:${value}`)
      .join(',') +
    '}';

  const body = JSON.stringify({
    inputs: [
      { name: 'systemPrompt', value: systemPrompt },
      { name: 'userPrompt', value: userPrompt },
      { name: 'options', value: optionsString },
    ],
  });

  try {
    const response = await fetch(llmURL, {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      body,
    });

    if (response.status !== 200) {
      return { error: `LLM call failed with status ${response.status}` };
    }

    // SCR wraps the module outputs in a `data` object; unwrap it so callers get
    // { response, run_time, prompt_length, output_length } directly (falling back
    // to the raw body if a future runtime returns the fields at the top level).
    const json = (await response.json()) as Record<string, unknown>;
    return json && typeof json.data === 'object' && json.data !== null
      ? json.data
      : json;
  } catch (e) {
    return { error: String(e) };
  }
}
