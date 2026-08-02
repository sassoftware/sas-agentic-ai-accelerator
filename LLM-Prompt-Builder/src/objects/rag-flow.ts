// Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * Build a SAS Studio flow (.flw) for one RAG Setup.
 *
 * The flow is the visual twin of the generated ingestion job: the same steps,
 * the same values, wired List -> Extract -> Chunk -> [Enrich ->] Embed ->
 * Load. The JOB is what a schedule runs; the FLOW is what someone opens to see
 * what the pipeline does and to edit it by hand. Both are generated from the
 * setup, so a break in this file degrades visual editing and never ingestion.
 *
 * The .flw format is an internal, undocumented contract (owner risk accepted
 * 2026-07-27). Everything encoded here was established by building flows
 * against a live SAS Studio and checking that /studioDevelopment/code would
 * generate from them - the comments record what failed, because the failure
 * mode throughout is a bare HTTP 500 with no diagnostic.
 */
import type { RagSetup } from '../types/rag';
import type { StepPorts } from '../api/dataflows-api';
import { renderMapping } from './rag-enrich';

/** The steps of the ingestion chain, in wiring order. */
export const INGESTION_STEPS = [
  'RAG - List Documents',
  'RAG - Extract Text',
  'RAG - Chunk Documents',
  'RAG - Embed Chunks',
  'RAG - Load Vector Store',
] as const;

/**
 * The chain THIS setup implies.
 *
 * Enrich only joins it when the setup has a prompt, for two reasons: a flow
 * that carries a step doing nothing invites someone to fill it in without the
 * setup knowing, and a deployment that has not redeployed its custom steps
 * does not have the Enrich step registered at all — generating a flow that
 * names it would fail for every setup rather than only the enriching ones.
 */
export function ingestionSteps(setup: RagSetup): string[] {
  const steps: string[] = [...INGESTION_STEPS];
  if (setup.enrich?.promptModelId) {
    steps.splice(steps.indexOf('RAG - Embed Chunks'), 0, 'RAG - Enrich Chunks');
  }
  return steps;
}

export type StepValues = Record<string, string | number>;

/**
 * The value each step carries, keyed by step display name.
 *
 * Only the FIRST step names the project and the caslib: every later step
 * inherits them from the inventory table travelling down the chain, which is
 * why they have no such control to set.
 */
export function ingestionChain(setup: RagSetup, corePath: string): Record<string, StepValues> {
  const project = setup.tables.prefix;
  return {
    'RAG - List Documents': {
      _rgls_sourcePath: setup.source.path,
      _rgls_pipelineVersion: setup.pipelineVersion || 'v1',
      _rgls_ragProject: project,
      _rgls_tablesCaslib: setup.tables.caslib,
      _rgls_ragCorePath: corePath,
    },
    'RAG - Extract Text': {
      _rgex_extractor: setup.extraction.extractor || 'auto',
      // memory only: the element table is rebuilt from the documents, so
      // persisting it doubles the storage for a table nobody reads twice
      _rgex_persist: '0',
      _rgex_ragCorePath: corePath,
    },
    'RAG - Chunk Documents': {
      _rgch_chunker: setup.chunking.chunker,
      _rgch_inputTokenLimit: setup.chunking.inputTokenLimit,
      _rgch_overlapTokens: setup.chunking.overlapTokens,
      _rgch_ragCorePath: corePath,
    },
    'RAG - Enrich Chunks': {
      _rgen_promptModel: setup.enrich?.promptModelId ?? '',
      _rgen_mapping: renderMapping(setup.enrich?.mapping ?? {}),
      _rgen_headerOutput: setup.enrich?.headerOutput ?? '',
      _rgen_tagOutputs: (setup.enrich?.tagOutputs ?? []).join(','),
      _rgen_workers: setup.enrich?.workers ?? 4,
      _rgen_credentialDomain: setup.credentialDomain,
      // the same table the Chunk step wrote, so the two must agree on whether
      // it survives a restart
      _rgen_persist: setup.policies?.persistChunks === false ? '0' : '1',
      _rgen_ragCorePath: corePath,
    },
    'RAG - Embed Chunks': {
      _rgem_embedModel: setup.embedding.model,
      _rgem_scrEndpoint: setup.embedding.scrEndpoint || '',
      _rgem_deploymentType: setup.embedding.deploymentType || 'k8s',
      _rgem_replicas: setup.policies?.embedReplicas ?? 1,
      // an API-backed embedding model resolves its provider key from here
      _rgem_credentialDomain: setup.credentialDomain,
      _rgem_ragCorePath: corePath,
    },
    'RAG - Load Vector Store': {
      _rgld_backend: setup.store.backend,
      _rgld_collection: setup.store.collection,
      _rgld_storeHost: setup.store.host,
      _rgld_storePort: String(setup.store.port ?? ''),
      _rgld_storeDb: setup.store.database,
      _rgld_storeSslmode: setup.store.sslmode,
      _rgld_deletedPolicy: setup.policies?.deletedPolicy || 'retire',
      _rgld_retainDays: setup.policies?.retainDays ?? 0,
      _rgld_credentialDomain: setup.credentialDomain,
      // 0 = take the width from the embedding container's own answer, which
      // is the only number that cannot disagree with the vectors
      _rgld_embeddingDims: 0,
      _rgld_ragCorePath: corePath,
    },
  };
}

interface FlowNode {
  id: string;
  name: string;
  nodeType: string;
  priority: number;
  properties: Record<string, string>;
  portMappings: unknown[];
  arguments: Record<string, unknown>;
  stepReference: { type: string; path: string };
}

/** RFC 4122 v4, from the browser's crypto rather than Math.random. */
function uuid(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/** One node: its arguments, its port references and the canvas properties. */
function buildNode(spec: StepPorts, stepId: string, values: StepValues, order: number): FlowNode {
  const args: Record<string, unknown> = {};
  const metadata: Record<string, { type: string }> = {};
  for (const control of Object.keys(spec.controls).sort()) {
    metadata[control] = { type: spec.controls[control] };
  }
  for (const port of spec.inputs) {
    args[port] = { portIndex: 0, portName: port, referenceType: 'inputPort' };
    metadata[port] ??= { type: 'inputtable' };
  }
  for (const port of spec.outputs) {
    args[port] = { arguments: {}, portIndex: 0, portName: port, referenceType: 'outputPort' };
    metadata[port] ??= { type: 'outputtable' };
  }
  // A value for a control the deployed step does not declare would be
  // silently ignored by Studio and silently missing from the run - so it is
  // an error here, where the name is still visible.
  const unknown = Object.keys(values).filter(
    (key) => !(key in spec.controls) && !spec.inputs.includes(key) && !spec.outputs.includes(key)
  );
  if (unknown.length > 0) {
    throw new Error(`${spec.name}: no such control(s): ${unknown.join(', ')}`);
  }
  for (const [control, value] of Object.entries(values)) args[control] = value;
  args._promptMetadata = metadata;

  const properties: Record<string, string> = {
    UI_PROP_IS_INPUT_EXPANDED: 'false',
    UI_PROP_IS_OUTPUT_EXPANDED: 'false',
    UI_PROP_LOCATION: `${(60 + order * 190).toFixed(4)} ${(60).toFixed(4)}`,
  };
  for (const port of spec.inputs) properties[`UI_PROP_INPUT_PORT|${port}|0`] = `|${port}|`;
  for (const port of spec.outputs) properties[`UI_PROP_OUTPUT_PORT|${port}|0`] = `|${port}|`;

  return {
    id: uuid(),
    name: spec.name,
    nodeType: 'step',
    priority: order,
    properties,
    portMappings: [],
    arguments: args,
    stepReference: { type: 'uri', path: `/dataFlows/steps/${stepId}` },
  };
}

/**
 * Assemble the flow document.
 *
 * `timestamp` and `author` are passed in rather than read here so the caller
 * owns the clock - the same reason rag_core stamps runs from the run id.
 */
export function buildFlow(
  specs: StepPorts[],
  stepIds: Record<string, string>,
  values: Record<string, StepValues>,
  flowName: string,
  author: string,
  timestamp: string
): Record<string, unknown> {
  const nodes: Record<string, FlowNode> = {};
  const ordered: { node: FlowNode; spec: StepPorts }[] = [];
  specs.forEach((spec, order) => {
    const stepId = stepIds[spec.name];
    if (!stepId) throw new Error(`no registered step id for '${spec.name}'`);
    const node = buildNode(spec, stepId, values[spec.name] ?? {}, order);
    nodes[node.id] = node;
    ordered.push({ node, spec });
  });

  const connections: {
    sourcePort: { node: string; portName: string; index: number };
    targetPort: { node: string; portName: string; index: number };
  }[] = [];
  for (let index = 0; index + 1 < ordered.length; index += 1) {
    const left = ordered[index];
    const right = ordered[index + 1];
    if (left.spec.outputs.length === 0 || right.spec.inputs.length === 0) continue;
    connections.push({
      sourcePort: { node: left.node.id, portName: left.spec.outputs[0], index: 0 },
      targetPort: { node: right.node.id, portName: right.spec.inputs[0], index: 0 },
    });
  }

  // An input port that nothing is wired into must NOT carry a reference:
  // code generation answers a bare HTTP 500 if it does, whether or not the
  // port is optional (minEntries 0). Established by elimination against a
  // live Studio - a step with no input ports generated, the same step with
  // an unwired reference did not, and stripping the reference fixed it.
  const wired = new Set(connections.map((c) => `${c.targetPort.node}|${c.targetPort.portName}`));
  for (const node of Object.values(nodes)) {
    for (const [name, value] of Object.entries(node.arguments)) {
      const reference = value as { referenceType?: string; portName?: string };
      if (
        reference?.referenceType === 'inputPort' &&
        !wired.has(`${node.id}|${reference.portName}`)
      ) {
        delete node.arguments[name];
        delete (node.arguments._promptMetadata as Record<string, unknown>)[name];
        delete node.properties[`UI_PROP_INPUT_PORT|${name}|0`];
      }
    }
  }

  return {
    creationTimeStamp: timestamp,
    createdBy: author,
    modifiedTimeStamp: timestamp,
    modifiedBy: author,
    id: uuid(),
    name: flowName,
    properties: {
      UI_PROP_DF_EXECUTION_ORDERED: 'false',
      UI_PROP_DF_EXECUTION_ORDER_BADGES: 'false',
      UI_PROP_DF_OPTIMIZE: 'false',
    },
    version: 4,
    sourceVersion: 2,
    nodes,
    connections,
  };
}
