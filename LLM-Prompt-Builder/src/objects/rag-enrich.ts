// Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * Reading a manifested prompt, in the browser.
 *
 * This is a deliberate second implementation of what `rag_core.enrich
 * .PromptModel` does in Python, and the duplication is the same trade the
 * manifest module already makes: the Python one runs where the ingestion runs
 * and is the authority; this one exists so the RAG Builder can show a user
 * which inputs a prompt takes and which outputs it returns WITHOUT running a
 * job to find out. The browser cannot exec Python, so it reads the same two
 * declarations out of the score code that Python reads off the function -
 * the parameter list and the `"Output: ..."` docstring.
 *
 * The contract is narrow and stable because the Prompt Builder generates it
 * (see the manifest block in prompt-builder.ts). If it ever changes, it
 * changes in `rag_core/enrich.py` first: that copy is the one an ingestion
 * actually uses, and a mismatch there is silent.
 */

/**
 * What a prompt input can be filled with, mirroring `enrich.CHUNK_FIELDS`.
 *
 * A closed vocabulary: every entry is something the pipeline already holds
 * for a chunk, so no mapping can ask the ingestion to go and fetch something
 * per chunk. The keys must match the Python side exactly - they travel to the
 * job as `input=field` pairs.
 */
export const CHUNK_FIELDS = [
  { key: 'chunk', label: 'Chunk text' },
  { key: 'document', label: 'Whole document (capped at 20,000 characters)' },
  { key: 'neighbours', label: 'Previous and next chunk' },
  { key: 'heading', label: 'Heading path' },
  { key: 'filename', label: 'File name' },
  { key: 'source', label: 'Full source location' },
  { key: 'position', label: "Position, as 'chunk 3 of 42'" },
] as const;

export const CHUNK_FIELD_KEYS = CHUNK_FIELDS.map((field) => field.key) as readonly string[];

/** What the Builder could learn about a prompt from its score code. */
export interface PromptContract {
  inputs: string[];
  outputs: string[];
  /** The LLM the prompt calls, for the cost estimate. '' when unreadable. */
  llm: string;
  /**
   * Whether the prompt makes the LLM call itself.
   *
   * A prompt manifested for the Call LLM node of SAS Intelligent Decisioning
   * returns `llmBody`/`llmURL` — the request to make, not its answer — so
   * there is nothing for an ingestion to store. Detected here so the Builder
   * can say that in the form rather than letting a run discover it.
   */
  integrated: boolean;
}

const SCORE_SIGNATURE = /def\s+scoreModel\s*\(([^)]*)\)/;
const OUTPUT_DOC = /^\s*["']?\s*Output\s*:\s*(.+?)["']?\s*$/im;
const LLM_LITERAL = /^\s*llm\s*=\s*["']([^"']+)["']/m;

/**
 * Read a manifested prompt's contract. Returns null when the file is not one.
 */
export function readPromptContract(code: string): PromptContract | null {
  const signature = SCORE_SIGNATURE.exec(code);
  if (!signature) return null;
  const inputs = signature[1]
    .split(',')
    .map((name) => name.trim())
    .filter(Boolean);
  const declared = OUTPUT_DOC.exec(code);
  if (!declared) return null;
  const outputs = declared[1]
    .split(',')
    .map((name) => name.trim())
    .filter(Boolean);
  return {
    inputs,
    outputs,
    llm: LLM_LITERAL.exec(code)?.[1] ?? '',
    integrated: !outputs.includes('llmBody') && !outputs.includes('llmURL'),
  };
}

/** The inputs a setup has to map — API_KEY is resolved, never mapped. */
export function mappableInputs(contract: PromptContract): string[] {
  return contract.inputs.filter((name) => name !== 'API_KEY');
}

/**
 * A first mapping for a prompt nobody has mapped yet.
 *
 * Guessing by name is worth doing because prompt authors name variables after
 * what they are — a `{{chunk}}` really is the chunk — and a form that opens
 * with the obvious answer already filled in is the difference between
 * enrichment being tried and not. Anything unrecognised stays BLANK rather
 * than defaulting to the chunk text: a silently wrong mapping would produce a
 * whole corpus of confident nonsense.
 */
const FIELD_BY_NAME: Record<string, string> = {
  chunk: 'chunk',
  text: 'chunk',
  content: 'chunk',
  passage: 'chunk',
  excerpt: 'chunk',
  document: 'document',
  doc: 'document',
  context: 'document',
  wholedocument: 'document',
  neighbours: 'neighbours',
  neighbors: 'neighbours',
  surrounding: 'neighbours',
  heading: 'heading',
  headings: 'heading',
  section: 'heading',
  title: 'heading',
  filename: 'filename',
  file: 'filename',
  filepath: 'source',
  source: 'source',
  uri: 'source',
  path: 'source',
  position: 'position',
  index: 'position',
};

export function defaultMapping(contract: PromptContract): Record<string, string> {
  const mapping: Record<string, string> = {};
  for (const name of mappableInputs(contract)) {
    const key = name.toLowerCase().replace(/[^a-z]/g, '');
    if (FIELD_BY_NAME[key]) mapping[name] = FIELD_BY_NAME[key];
  }
  return mapping;
}

/**
 * `input=field;input=field` — how the mapping travels as a job parameter.
 *
 * Deliberately not JSON: this value crosses into a SAS macro variable, and a
 * form with no quotes or braces is one fewer thing that can arrive mangled.
 * Sorted, so the same mapping always produces the same string and therefore
 * the same configuration fingerprint.
 */
export function renderMapping(mapping: Record<string, string>): string {
  return Object.keys(mapping)
    .filter((name) => mapping[name])
    .sort()
    .map((name) => `${name}=${mapping[name]}`)
    .join(';');
}

export function parseMapping(raw: string): Record<string, string> {
  const mapping: Record<string, string> = {};
  for (const pair of String(raw || '').split(';')) {
    const [name, field] = pair.split('=');
    if (name?.trim() && field?.trim()) mapping[name.trim()] = field.trim();
  }
  return mapping;
}

/**
 * Outputs a prompt returns that the LLM itself produced, rather than the
 * plumbing around it.
 *
 * `parse_status` is a diagnostic and `run_time`/`prompt_length`/
 * `output_length` are measurements — storing one as a chunk's context header
 * would be a mistake nobody makes on purpose, so they are not offered as one.
 * They stay available as tags, where a per-chunk latency is a fair thing to
 * keep.
 */
const MEASUREMENTS = new Set(['run_time', 'prompt_length', 'output_length', 'parse_status']);

export function headerCandidates(contract: PromptContract): string[] {
  return contract.outputs.filter((name) => !MEASUREMENTS.has(name));
}

/**
 * Everything wrong with a setup's use of a prompt, in the user's words.
 *
 * Mirrors `enrich.validate_selection`, which is the authority — this copy
 * exists so the Builder can refuse to save a setup that the ingestion would
 * refuse to run, at the point where the fields are still on screen.
 */
export function validateEnrichment(
  contract: PromptContract | null,
  mapping: Record<string, string>,
  headerOutput: string,
  tagOutputs: string[],
  text: (key: string, fallback: string) => string
): string[] {
  const problems: string[] = [];
  if (!contract) {
    problems.push(
      text(
        'ragBuilderEnrichValidateContract',
        'The selected prompt carries no readable score code, so nothing can be called. Manifest it in the Prompt Builder first.'
      )
    );
    return problems;
  }
  if (!contract.integrated) {
    problems.push(
      text(
        'ragBuilderEnrichValidateIntegrated',
        'This prompt was manifested for the Call LLM node of SAS Intelligent Decisioning: it returns the request to make rather than the answer, so an ingestion has nothing to store. Re-manifest it with the integrated LLM call.'
      )
    );
  }
  const unmapped = mappableInputs(contract).filter((name) => !mapping[name]);
  if (unmapped.length > 0) {
    problems.push(
      text(
        'ragBuilderEnrichValidateMapping',
        'Choose what fills the prompt input(s) {inputs}.'
      ).replace('{inputs}', unmapped.join(', '))
    );
  }
  const wanted = (headerOutput ? [headerOutput] : []).concat(tagOutputs);
  const missing = wanted.filter((name) => !contract.outputs.includes(name));
  if (missing.length > 0) {
    problems.push(
      text(
        'ragBuilderEnrichValidateOutputs',
        'This prompt does not return {outputs} - it returns {available}.'
      )
        .replace('{outputs}', missing.join(', '))
        .replace('{available}', contract.outputs.join(', ') || '—')
    );
  }
  if (!headerOutput && tagOutputs.length === 0) {
    problems.push(
      text(
        'ragBuilderEnrichValidateNothing',
        'Nothing would be stored: choose the output that becomes the context header, or at least one output to keep as a tag.'
      )
    );
  }
  return problems;
}
