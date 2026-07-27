/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * SAS Visual Analytics Data-Driven Content (DDC) integration.
 *
 * Modelled on SAS's own reference implementation, the ArcGIS GeoWebMap provider
 * (github.com/sassoftware/sas-visualanalytics-geowebmap): a single `message`
 * listener handles the whole DDC lifecycle.
 *
 * VA posts a message to the iframe that carries a `resultName` (and, once data
 * is assigned, `columns` + `data`). We use that message to do two things:
 *
 *  1. Properties panel — post the options group back to VA wrapped in the
 *     envelope `{ resultName, optionsConfig }`. VA renders it as the object's
 *     Properties panel. `resultName` is ONLY available from an inbound VA
 *     message, so the options MUST be posted in response to one — not on load.
 *     With `urlOption: true`, VA mirrors the author's values into the iframe URL
 *     (keyed by each field's `name`), which `config.ts` reads back as query
 *     parameters on the next (re)load.
 *
 *  2. API key(s) — parse them from the assigned data table (>= 2 columns:
 *     column 0 = key name, column 1 = key value), keeping secrets out of the URL.
 *
 * See also the "Data-Driven Content (DDC) - Option Groups" reference and
 * https://davidweik.substack.com/p/creating-data-driven-content-in-sas-visual-analytics
 */

import type { PromptBuilderConfig } from '../types';

/** Options-group field descriptor (subset of the DDC option contract). */
interface OptionField {
  name: string;
  label: string;
  type: string;
  tooltip?: string;
  value?: unknown;
  placeholder?: string;
  dataProvider?: Array<{ key: string; text: string }>;
}

interface RuntimeConfig {
  viyaHost: string;
  promptBuilder: PromptBuilderConfig;
}

/**
 * Wire up the VA DDC integration: one `message` listener that renders the
 * Properties panel and extracts the API key(s). No-op when the page is not
 * embedded in a parent frame (e.g. standalone dev / preview).
 */
export function initVaIntegration(
  config: RuntimeConfig,
  onKeys: (keys: Record<string, string>) => void
): void {
  const targets = getPostTargets();
  if (targets.length === 0) return;

  const optionsConfig = buildOptionsConfig(config);
  const postedFor = new Set<string>();

  function postOptions(resultName: string): void {
    const envelope = { resultName, optionsConfig };
    for (const target of targets) {
      try {
        target.postMessage(envelope, '*');
      } catch {
        /* cross-origin post rejected — ignore and try the next target */
      }
    }
  }

  function handle(event: MessageEvent): void {
    let msg: Record<string, unknown> | null;
    if (typeof event.data === 'string') {
      try {
        msg = JSON.parse(event.data) as Record<string, unknown>;
      } catch {
        return;
      }
    } else if (event.data && typeof event.data === 'object') {
      msg = event.data as Record<string, unknown>;
    } else {
      return;
    }

    // (1) Render/refresh the Properties panel once VA gives us a resultName.
    const resultName = typeof msg.resultName === 'string' ? msg.resultName : '';
    if (resultName && !postedFor.has(resultName)) {
      postedFor.add(resultName);
      postOptions(resultName);
    }

    // (2) Extract API key(s) from an assigned data table.
    if (
      Array.isArray(msg.columns) &&
      Array.isArray(msg.data) &&
      msg.columns.length >= 2
    ) {
      const keys = parseKeyTable(msg.data as unknown[][]);
      if (Object.keys(keys).length > 0) onKeys(keys);
    }
  }

  window.addEventListener('message', handle, false);
}

/** Parent (and, if different, top) frames to post the options envelope to. */
function getPostTargets(): Window[] {
  const targets: Window[] = [];
  if (window.parent && window.parent !== window) targets.push(window.parent);
  if (window.top && window.top !== window && window.top !== window.parent) {
    targets.push(window.top);
  }
  return targets;
}

/**
 * Build the options group. Each field's `name` is BOTH the panel identifier and
 * the URL query-parameter key VA populates (so it must match what config.ts
 * reads). `urlOption: true` turns that URL mirroring on.
 */
function buildOptionsConfig(config: RuntimeConfig): Record<string, unknown> {
  const pb = config.promptBuilder;
  // A single flat options group: the top-level object IS the group, with its
  // fields directly (no nested group) so VA renders exactly one panel section.
  return {
    version: 1,
    urlOption: true,
    name: 'PromptBuilderOptions',
    label: 'Prompt Builder configuration',
    fields: [
      textField(
        'viyaHost',
        'SAS Viya host',
        config.viyaHost,
        'Base URL of SAS Viya. Defaults to the embedding origin when left blank.'
      ),
      textField(
        'modelRepositoryID',
        'Model Manager repository ID',
        pb.modelRepositoryID,
        'SAS Model Manager repository new prompt projects are created in.'
      ),
      textField(
        'llmProjectID',
        'LLM project ID',
        pb.llmProjectID,
        'Model Manager project holding the available LLM definitions.'
      ),
      textField(
        'SCREndpoint',
        'SCR endpoint',
        pb.SCREndpoint,
        'Base URL of the SCR endpoint hosting the LLM containers.'
      ),
      {
        name: 'deploymentType',
        label: 'Deployment type',
        type: 'String',
        value: pb.deploymentType ?? 'k8s',
        tooltip: 'How the LLM containers are deployed.',
        dataProvider: [
          { key: 'k8s', text: 'Kubernetes (k8s)' },
          { key: 'aca', text: 'Azure Container Apps (aca)' },
        ],
      } as OptionField,
      textField(
        'judgeModel',
        'Default judge model',
        pb.judgeModel,
        'Optional default LLM (by name, from the LLM project) used to judge which response is best. Users can override it in the app.'
      ),
      textField(
        'modelCardReportURI',
        'Model card report URI',
        pb.modelCardReportURI,
        'Optional SAS Visual Analytics report URI (the /reports/reports/<uuid> path). When set, manifesting the best prompt embeds that report on the model card as its custom chart, using the SAS Viya host above.'
      ),
      textField(
        'credentialDomain',
        'Credential domain',
        pb.credentialDomain,
        'SAS Viya credential domain provider API keys are resolved from under the signed-in user; models without a key entry are disabled with a note. Defaults to agentic-ai-keys (the create-credential-domain.sas default); if the domain does not exist, keys from the assigned data table work as before. Enter none to disable credential lookups. See the Managing Credentials administration guide.'
      ),
      // Progressive disclosure: the optimization master toggle is always shown;
      // its settings only join the panel once it is on (VA mirrors the changed
      // value into the URL and reloads the iframe, which rebuilds this group).
      {
        name: 'enableOptimization',
        label: 'Enable prompt optimization (DSPy)',
        type: 'String',
        value: pb.enableOptimization === 'true' ? 'true' : 'false',
        tooltip:
          'Adds an Optimize section that improves the selected prompt with DSPy in a SAS Job Execution job. Requires the settings that appear when this is enabled — see the "Enabling Prompt Optimization" administration guide.',
        dataProvider: [
          { key: 'false', text: 'Disabled' },
          { key: 'true', text: 'Enabled' },
        ],
      } as OptionField,
      ...(pb.enableOptimization === 'true'
        ? [
            textField(
              'computeContext',
              'Optimization compute context',
              pb.computeContext,
              'SAS Compute context the optimization job runs in (passed to Job Execution as _contextName). Its Python environment must have dspy installed.'
            ),
            textField(
              'optimizeJobProgram',
              'Optimize job path',
              pb.optimizeJobProgram,
              'SAS Content path of the deployed optimize Job Definition, e.g. /Public/Jobs/Optimize-Prompt-DSPy.'
            ),
            textField(
              'minOptimizeSamples',
              'Minimum optimization samples',
              pb.minOptimizeSamples ?? '30',
              'Minimum dataset rows before an optimization run is allowed. Default 30; the Optimize panel warns below 50.'
            ),
            textField(
              'optimizeKeyLibrary',
              'API-key library',
              pb.optimizeKeyLibrary,
              'SAS library holding the governed provider API-key table the job reads. Only the library and table names are sent to the job — never the keys.'
            ),
            textField(
              'optimizeKeyTable',
              'API-key table',
              pb.optimizeKeyTable,
              'Table in the API-key library mapping provider name to key value (same names the LLM options.json files reference).'
            ),
          ]
        : []),
    ],
    groups: [],
  };
}

function textField(
  name: string,
  label: string,
  value: unknown,
  tooltip: string
): OptionField {
  return { name, label, type: 'String', value: value ?? '', tooltip };
}

/**
 * Parse an API-key table: column 0 is the key NAME (matching the `API_KEY.default`
 * value referenced by an LLM's `options.json`, e.g. "Anthropic"); column 1 is the
 * key VALUE. One provider per row.
 */
function parseKeyTable(rows: unknown[][]): Record<string, string> {
  const keys: Record<string, string> = {};
  for (const row of rows) {
    if (!Array.isArray(row) || row.length < 2) continue;
    const name = row[0];
    const value = row[1];
    if (name == null || value == null || String(name).trim() === '') continue;
    keys[String(name)] = String(value);
  }
  return keys;
}
