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
 * VA posts a message to the iframe that carries a `resultName`. We use that
 * message to render the Options pane: post the options group back to VA
 * wrapped in the envelope `{ resultName, optionsConfig }`. `resultName` is
 * ONLY available from an inbound VA message, so the options MUST be posted in
 * response to one — not on load. With `urlOption: true`, VA mirrors the
 * author's values into the iframe URL (keyed by each field's `name`), which
 * `config.ts` reads back as query parameters on the next (re)load.
 *
 * There is ONE options group for the whole page - the prompt side, the RAG
 * side and the three switches between them - and it is flat: the shared
 * options first, then each switch directly followed by the options that only
 * matter while it is on.
 *
 * No secret is part of this contract: provider API keys and vector-store
 * credentials resolve from the configured SAS Viya credential domain (see
 * src/api/credentials-api.ts and the Managing Credentials guide).
 *
 * See also the "Data-Driven Content (DDC) - Option Groups" reference and
 * https://davidweik.substack.com/p/creating-data-driven-content-in-sas-visual-analytics
 */

import type { RuntimeConfig } from '../config';
import { RAG_BACKENDS, backendEnabled, backendOptionKey } from '../objects/rag-backends';
import { optionFlag } from '../objects/rag-options';

/**
 * Options-group field descriptor (subset of the DDC option contract).
 *
 * `type` is written capitalised ('String', 'Boolean'): VA does not reject a
 * lower-case name, it silently renders a plain text input instead.
 */
interface OptionField {
  name: string;
  label: string;
  type: 'String' | 'Boolean';
  tooltip?: string;
  value?: unknown;
  placeholder?: string;
  dataProvider?: Array<{ key: string; text: string }>;
}

/**
 * Wire up the VA DDC integration: one `message` listener that renders the
 * Options pane. No-op when the page is not embedded in a parent frame (e.g.
 * standalone dev / preview).
 *
 * Call it before anything that can wait or fail: an object that is not
 * configured yet, or whose data cannot be loaded, needs its Options pane more
 * than any other, and VA sends the message carrying the `resultName` once.
 */
export function initVaIntegration(config: RuntimeConfig): void {
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
    // JSON.parse also yields null, numbers and strings - none carries a name.
    if (!msg || typeof msg !== 'object') return;

    // Render/refresh the Options pane once VA gives us a resultName.
    const resultName = typeof msg.resultName === 'string' ? msg.resultName : '';
    if (resultName && !postedFor.has(resultName)) {
      postedFor.add(resultName);
      postOptions(resultName);
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
 * Build the options group. Each field's `name` is BOTH the pane identifier and
 * the URL query-parameter key VA populates (so it must match what config.ts
 * reads). `urlOption: true` turns that URL mirroring on.
 */
function buildOptionsConfig(config: RuntimeConfig): Record<string, unknown> {
  const pb = config.promptBuilder;
  const rb = config.ragBuilder;
  const features = config.features;
  // A single flat options group: the top-level object IS the group, with its
  // fields directly (no nested group) so VA renders exactly one pane section.
  // Because the list is flat, the label of a dependent option starts with the
  // name of the switch it belongs to ("Optimization: ...", "RAG: ..."), which
  // is what makes the list read as grouped.
  return {
    version: 1,
    urlOption: true,
    name: 'PromptBuilderOptions',
    label: 'Prompt Builder configuration',
    fields: [
      // --- shared by every kind -------------------------------------------
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
        'SAS Model Manager repository that new prompt projects, decision projects and RAG projects are created in. Required by every kind.'
      ),
      textField(
        'llmProjectID',
        'LLM project ID',
        pb.llmProjectID,
        'Model Manager project holding the available LLM definitions. Required for prompt and decision templates; a page that only builds RAG setups can leave it blank.'
      ),
      textField(
        'SCREndpoint',
        'SCR endpoint',
        pb.SCREndpoint,
        'Base URL of the SCR endpoint hosting the LLM and decision model containers and, for RAG, the embedding containers. Required for prompt and decision templates; RAG reads a blank value as <SAS Viya host>/llm.'
      ),
      {
        name: 'deploymentType',
        label: 'Deployment type',
        type: 'String',
        value: pb.deploymentType ?? 'k8s',
        tooltip: 'How the model containers (LLM, decision and embedding models) are deployed.',
        dataProvider: [
          { key: 'k8s', text: 'Kubernetes (k8s)' },
          { key: 'aca', text: 'Azure Container Apps (aca)' },
        ],
      } satisfies OptionField,
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
        // The prompt side is handed a blank for "no lookup" (see config.ts).
        // The pane must keep showing the word the author entered, or the next
        // save would store a blank, which reads as "use the default domain".
        pb.credentialDomain || 'none',
        'SAS Viya credential domain used by every kind. Provider API keys are resolved from it under the signed-in user; models without a key entry are disabled with a note. For RAG, the ingestion job and the retrieval take the vector-store credentials and location from it (<BACKEND>_RAG_USER / <BACKEND>_RAG_PW entries). Defaults to agentic-ai-keys (the default of the create-credential-domain scripts and mdb credentials-apply); a domain that does not exist resolves to no keys. Enter none to disable credential lookups for prompt and decision templates - RAG needs a domain and cannot be used with none. See the Managing Credentials administration guide.'
      ),

      // --- the switches, each followed by what it brings ---------------------
      // Progressive disclosure: a switch is always shown; its settings only
      // join the pane once it is on (VA mirrors the changed value into the
      // URL and reloads the iframe, which rebuilds this group).
      switchField(
        'enableDecision',
        'Enable decision templates',
        features.decision,
        'Offers decision templates for decision models (Jev, Von) beside prompt templates for LLMs, with the question designer, the Evaluate step and the typed manifest. Disabled: the kind is not offered, no decision model is requested and existing decision projects and templates are not listed; they stay untouched in SAS Model Manager.'
      ),
      switchField(
        'enableOptimization',
        'Enable prompt optimization (DSPy)',
        features.optimization,
        'Adds an Optimize step that improves the selected prompt with DSPy in a SAS Job Execution job. Requires the settings that appear when this is enabled — see the "Enabling Prompt Optimization" administration guide.'
      ),
      ...(features.optimization
        ? [
            textField(
              'computeContext',
              'Optimization: compute context',
              pb.computeContext,
              'SAS Compute context the optimization job runs in (passed to Job Execution as _contextName). Its Python environment must have dspy installed. Not used by RAG, which has its own ingestion compute context.'
            ),
            textField(
              'optimizeJobProgram',
              'Optimization: job path',
              pb.optimizeJobProgram,
              'SAS Content path of the deployed optimize Job Definition, e.g. /Public/Jobs/Optimize-Prompt-DSPy.'
            ),
            textField(
              'minOptimizeSamples',
              'Optimization: minimum samples',
              pb.minOptimizeSamples ?? '30',
              'Minimum dataset rows before an optimization run is allowed. Default 30; the Optimize panel warns below 50.'
            ),
          ]
        : []),
      switchField(
        'enableRag',
        'Enable RAG setups',
        features.rag,
        'Offers RAG setups as a third kind: the RAG Builder with its own steps (documents and chunks, vector store, ingestion, test retrieval). Requires the deployed RAG runtime and the settings that appear when this is enabled — see the RAG administration guide. Needs a credential domain other than none.'
      ),
      ...(features.rag
        ? [
            textField(
              'embeddingProjectID',
              'RAG: embedding model project ID',
              rb.embeddingProjectID,
              'SAS Model Manager project holding the registered embedding models. The Builder lists that project instead of asking users to type a model name - a name with no container behind it fails at the first embed call, long after the crawl and chunking have run. Leave blank only if you have no such project; the Builder then falls back to free text.'
            ),
            textField(
              'contentRoot',
              'RAG: content root',
              rb.contentRoot,
              'SAS Content folder the deploy-rag-content scripts populated (rag_core, jobs, models). Generated job definitions are written to its "generated" subfolder.'
            ),
            textField(
              'casServer',
              'RAG: CAS server',
              rb.casServer,
              'CAS server used for the ingestion ledger and pipeline tables.'
            ),
            textField(
              'ragComputeContext',
              'RAG: ingestion compute context',
              rb.computeContext,
              'SAS Compute context the ingestion job runs in (passed to Job Execution as _contextName). It MUST run as the requesting user: the stock "SAS Job Execution compute context" runs its servers as a service account, and the ingestion steps cannot reuse the CAS session they need there. "SAS Studio compute context" works. Its Python also needs the packages from the RAG administration guide. Separate from the optimization compute context.'
            ),
            {
              name: 'storeSslmode',
              label: 'RAG: vector store TLS',
              type: 'String',
              value: rb.storeSslmode ?? 'prefer',
              tooltip:
                'How the ingestion connects to the vector store. The six values are a PostgreSQL concept; other stores read "disable" as no TLS and anything else as TLS on. Set here rather than per setup, because a value that does not mean what it says is worse than no choice.',
              dataProvider: [
                { key: 'prefer', text: 'prefer' },
                { key: 'require', text: 'require' },
                { key: 'verify-ca', text: 'verify-ca' },
                { key: 'verify-full', text: 'verify-full' },
                { key: 'allow', text: 'allow' },
                { key: 'disable', text: 'disable (no TLS)' },
              ],
            } satisfies OptionField,
            {
              name: 'deletedPolicy',
              label: 'RAG: when a document disappears from the source',
              type: 'String',
              value: rb.deletedPolicy ?? 'retire',
              tooltip:
                'Retire keeps its chunks as unretrievable history, so the collection can still be read as of an earlier date and the run rolled back. Purge removes them for good.',
              dataProvider: [
                { key: 'retire', text: 'Keep its chunks as history' },
                { key: 'purge', text: 'Remove its chunks permanently' },
              ],
            } satisfies OptionField,
            textField(
              'retainDays',
              'RAG: keep retired chunks for (days)',
              rb.retainDays,
              'Retired chunk generations older than this are dropped after each load. 0 keeps them forever. Live rows are never touched, so retrieval cannot change - only how far back an as-of read can reach. Matters more on SingleStore, where a vector index cannot be limited to live rows.'
            ),
            {
              // A checkbox: one on/off decision, unlike the persist options
              // below, whose two labels ("Save to disk" / "Keep in memory
              // only") say more than a tick would.
              name: 'recordHistory',
              label: 'RAG: record run history',
              type: 'Boolean',
              value: optionFlag(rb.recordHistory, true),
              tooltip:
                'Writes rag_runs, rag_doc_events and rag_configs beside the collection and publishes the first two to CAS for reporting. Without it there is no record of what a run did.',
            } satisfies OptionField,
            textField(
              'embedReplicas',
              'RAG: embedding container replicas',
              rb.embedReplicas,
              'How many replicas of the embedding container this deployment runs. The ingestion sizes its parallel calls from this, so setting it to what you actually run is the difference between saturating the container and leaving it idle.'
            ),
            {
              name: 'persistElements',
              label: 'RAG: save the element table to disk',
              type: 'String',
              value: rb.persistElements ?? '1',
              tooltip:
                'The <prefix>_ELEMENTS table is rebuilt from the documents on the next run, so it need not survive a restart, and on a large corpus it is one of the biggest things the pipeline writes. Applies to the Studio Flow path - the generated ingestion job does not build this table.',
              dataProvider: [
                { key: '1', text: 'Save to disk' },
                { key: '0', text: 'Keep in memory only' },
              ],
            } satisfies OptionField,
            {
              name: 'persistChunks',
              label: 'RAG: save the chunk table to disk',
              type: 'String',
              value: rb.persistChunks ?? '1',
              tooltip:
                'As above for <prefix>_CHUNKS, and likewise applies to the Studio Flow path. The ledger and the embedded chunks are always saved - the incremental diff and the embedding checkpoint depend on them.',
              dataProvider: [
                { key: '1', text: 'Save to disk' },
                { key: '0', text: 'Keep in memory only' },
              ],
            } satisfies OptionField,
            // One enable/disable field per backend, generated from
            // RAG_BACKENDS - a comma-separated list meant typing store names
            // by hand, and a typo silently offered nothing.
            //
            // A CHECKBOX rather than a Yes/No dropdown: this is one on/off
            // decision per store, which is what a checkbox is for, and it
            // reads at a glance as a list of what the deployment offers.
            // `backendEnabled` also accepts the '1'/'0' strings a dropdown
            // writes, so a report that stores the setting as text keeps it.
            ...RAG_BACKENDS.map(
              (backend) =>
                ({
                  name: backendOptionKey(backend),
                  label: `RAG: offer ${backend.label}`,
                  type: 'Boolean',
                  value: backendEnabled(rb, backend),
                  tooltip: `Whether end users may choose ${backend.label} for a RAG setup. Independent of credentials: this is what the deployment offers, and a user who holds no ${backend.entries[0]} / ${backend.entries[1]} entry sees it disabled with the missing entry named.`,
                }) satisfies OptionField
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
 * One of the three feature switches: a dropdown with the keys 'true' and
 * 'false'.
 *
 * A dropdown of two words rather than a checkbox, because the value has to
 * survive a round trip through the report, and this is the form in which a
 * stored value is known to come back as the word 'true' or 'false'. How VA
 * writes an unticked checkbox into the URL has not been observed; if it came
 * back blank or missing, a switch that defaults to on could not tell "off"
 * from "never set".
 */
function switchField(name: string, label: string, enabled: boolean, tooltip: string): OptionField {
  return {
    name,
    label,
    type: 'String',
    value: enabled ? 'true' : 'false',
    tooltip,
    dataProvider: [
      { key: 'false', text: 'Disabled' },
      { key: 'true', text: 'Enabled' },
    ],
  };
}
