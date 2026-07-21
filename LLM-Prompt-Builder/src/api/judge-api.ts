/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * LLM-as-a-Judge: rank a run's candidate responses with a user-selected judge
 * model. This is deliberately a thin layer over `callSCRLLM` — a judge is just
 * another SCR chat-completion call. The judging technique follows the current
 * research consensus for "which of these is best" tasks:
 *   - a single N-way COMPARATIVE ranking call (LLMs compare better than they
 *     score in the absolute),
 *   - REASONING BEFORE the verdict,
 *   - candidates shown in RANDOMISED order under opaque labels (position /
 *     brand bias mitigation),
 *   - the judge's own response optionally EXCLUDED (self-preference bias).
 * The verdict is parsed defensively (models wrap JSON in prose / code fences
 * even when told not to) with a single retry.
 */

import { callSCRLLM } from './scr-api';

export interface JudgeCandidate {
  /** The model that produced the response (used to map the verdict back). */
  modelName: string;
  /** The response text to be judged. */
  response: string;
}

export type JudgeConfidence = 'high' | 'medium' | 'low' | 'unknown';

export interface JudgeVerdict {
  /** 'ok' = a usable ranking; 'error' = call/precondition failed; 'unparseable'
   *  = the judge replied but we could not read a verdict out of it. */
  status: 'ok' | 'error' | 'unparseable';
  /** Winning model name (status 'ok'). */
  best?: string;
  /** Model names, best first (status 'ok'). */
  ranking?: string[];
  confidence?: JudgeConfidence;
  /** The judge's step-by-step rationale, when it returned one. */
  reasoning?: string;
  /** Human-readable reason for a non-'ok' status. */
  error?: string;
  /** Raw judge response, kept for display when the verdict was unparseable. */
  raw?: string;
}

/**
 * One council member's ballot: a JudgeVerdict tagged with the judge that
 * produced it, plus whether that judge's own response was excluded from it.
 */
export interface JudgeBallot {
  judgeModel: string;
  status: 'ok' | 'error' | 'unparseable';
  ranking?: string[];
  confidence?: JudgeConfidence;
  reasoning?: string;
  excludedSelf?: boolean;
  error?: string;
}

export interface CouncilAgreement {
  /** How many successful ballots ranked the aggregate winner first. */
  firstChoiceForWinner: number;
  /** Successful ballots counted in the aggregate. */
  total: number;
}

export interface CouncilResult {
  method: 'borda';
  /** Aggregate ranking, best first. */
  ranking: string[];
  /** Aggregate winner, or null on a tie. */
  best: string | null;
  tie: boolean;
  /** The tied winners (length > 1) when `tie`; empty otherwise. */
  tiedBest: string[];
  agreement: CouncilAgreement;
  /** Agreement-tier confidence: unanimous → high, majority → medium, else low. */
  confidence: JudgeConfidence;
  /** Borda score per candidate model (kept for display/debug). */
  scores: Record<string, number>;
}

/**
 * Aggregate a panel's ballots into a council result with a Borda count.
 *
 * Each ballot ranks the candidates that judge was eligible to judge (after
 * self-exclusion), so ballots can be partial. For a ballot ranking `m`
 * candidates, the candidate at 0-indexed position `p` scores `m - 1 - p`
 * (top gets `m-1`, last gets 0); a candidate a ballot didn't rank scores
 * nothing from it. The winner is the highest total; equal top totals are a
 * tie (no winner). Confidence is the agreement tier (how many judges ranked
 * the winner first). Pure and deterministic — no LLM calls, no randomness.
 */
export function aggregateBallots(
  candidateModels: string[],
  ballots: JudgeBallot[]
): CouncilResult {
  const scores: Record<string, number> = {};
  const firstChoice: Record<string, number> = {};
  candidateModels.forEach((model) => {
    scores[model] = 0;
    firstChoice[model] = 0;
  });

  const okBallots = ballots.filter(
    (ballot) => ballot.status === 'ok' && Array.isArray(ballot.ranking) && ballot.ranking.length > 0
  );

  for (const ballot of okBallots) {
    const ranking = ballot.ranking!.filter((model) => candidateModels.includes(model));
    const m = ranking.length;
    ranking.forEach((model, position) => {
      scores[model] += m - 1 - position;
    });
    if (ranking.length > 0) firstChoice[ranking[0]] += 1;
  }

  // Aggregate ranking: score desc, then model name asc for a stable order.
  const ranking = [...candidateModels].sort((a, b) => {
    const delta = scores[b] - scores[a];
    return delta !== 0 ? delta : a.localeCompare(b);
  });

  const total = okBallots.length;
  const topScore = ranking.length ? scores[ranking[0]] : 0;
  const tiedTop = candidateModels.filter((model) => scores[model] === topScore);
  const tie = total > 0 && tiedTop.length > 1;
  const best = total > 0 && !tie ? ranking[0] : null;
  const firstChoiceForWinner = best ? firstChoice[best] : 0;

  let confidence: JudgeConfidence;
  if (!best) confidence = 'low';
  else if (firstChoiceForWinner === total) confidence = 'high';
  else if (firstChoiceForWinner > total / 2) confidence = 'medium';
  else confidence = 'low';

  return {
    method: 'borda',
    ranking,
    best,
    tie,
    tiedBest: tie ? tiedTop : [],
    agreement: { firstChoiceForWinner, total },
    confidence,
    scores,
  };
}

export interface JudgeRunParams {
  scrEndpoint: string;
  deploymentType: string;
  /** The judge LLM name (from the LLM project). */
  judgeModel: string;
  /** Resolved judge options (API_KEY, temperature, …) for the SCR call. */
  judgeOptions: Record<string, unknown>;
  /** The run's resolved prompts (variables already substituted). */
  systemPrompt: string;
  userPrompt: string;
  candidates: JudgeCandidate[];
  /** When false, a candidate whose model equals the judge is dropped. */
  includeSelf: boolean;
  /** Injectable shuffle (indices 0..n-1) — defaults to a Fisher–Yates. */
  shuffle?: (n: number) => number[];
}

const JUDGE_SYSTEM_PROMPT = [
  'You are an impartial evaluator. You will see a task (a system prompt and a',
  'user prompt that were given to several AI assistants) and several candidate',
  'responses, each identified only by a letter. Judge only the quality of the',
  'responses for the given task. Ignore response length and formatting except',
  'where they affect quality. Do not assume any letter is better because of its',
  'position.',
  '',
  "Think step by step about each candidate's accuracy, relevance to the task,",
  'completeness, and clarity. THEN choose the single best candidate.',
  '',
  'Return ONLY a JSON object, no prose outside it, with this exact shape:',
  '{',
  '  "reasoning": "<your step-by-step comparison>",',
  '  "ranking": ["<letters, best first>"],',
  '  "best": "<the single best letter>",',
  '  "confidence": "high" | "medium" | "low"',
  '}',
].join('\n');

/** Column label for the i-th candidate: A, B, … Z, AA, AB, … */
export function candidateLabel(i: number): string {
  let label = '';
  let n = i;
  do {
    label = String.fromCharCode(65 + (n % 26)) + label;
    n = Math.floor(n / 26) - 1;
  } while (n >= 0);
  return label;
}

function defaultShuffle(n: number): number[] {
  const order = Array.from({ length: n }, (_, i) => i);
  for (let i = n - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [order[i], order[j]] = [order[j], order[i]];
  }
  return order;
}

/**
 * Pull the first balanced `{ … }` object out of a text blob (handles code
 * fences and leading/trailing prose). Returns null when none is found.
 */
export function extractJsonObject(text: string): string | null {
  if (!text) return null;
  const start = text.indexOf('{');
  if (start === -1) return null;
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let i = start; i < text.length; i++) {
    const ch = text[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (ch === '\\') escaped = true;
      else if (ch === '"') inString = false;
    } else if (ch === '"') {
      inString = true;
    } else if (ch === '{') {
      depth++;
    } else if (ch === '}') {
      depth--;
      if (depth === 0) return text.slice(start, i + 1);
    }
  }
  return null;
}

function normaliseConfidence(value: unknown): JudgeConfidence {
  const v = String(value ?? '').trim().toLowerCase();
  return v === 'high' || v === 'medium' || v === 'low' ? v : 'unknown';
}

/**
 * Build the judge user prompt from the labelled, ordered candidates.
 */
function buildJudgeUserPrompt(
  systemPrompt: string,
  userPrompt: string,
  labelled: { label: string; response: string }[]
): string {
  const blocks = labelled
    .map((c) => `[${c.label}]\n${c.response}`)
    .join('\n\n');
  return [
    '== TASK: SYSTEM PROMPT ==',
    systemPrompt || '(none)',
    '',
    '== TASK: USER PROMPT ==',
    userPrompt || '(none)',
    '',
    '== CANDIDATE RESPONSES ==',
    blocks,
    '',
    'Return the JSON object now.',
  ].join('\n');
}

/**
 * Parse a raw judge reply into the label-space verdict. Returns null when no
 * usable verdict can be read (so the caller can retry once).
 */
function parseVerdict(
  raw: string,
  validLabels: Set<string>
): { best: string; ranking: string[]; confidence: JudgeConfidence; reasoning: string } | null {
  const jsonText = extractJsonObject(raw);
  if (!jsonText) return null;
  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(jsonText) as Record<string, unknown>;
  } catch {
    return null;
  }

  const rankingRaw = Array.isArray(parsed.ranking)
    ? (parsed.ranking as unknown[]).map((l) => String(l).trim().toUpperCase())
    : [];
  const ranking = rankingRaw.filter((l) => validLabels.has(l));

  let best = String(parsed.best ?? '').trim().toUpperCase();
  if (!validLabels.has(best)) best = ranking[0] ?? '';
  if (!validLabels.has(best)) return null;

  // Ensure a complete ranking: start from what the judge gave, then append any
  // labels it omitted (best first is what matters for the winner + rank icons).
  const seen = new Set(ranking);
  const fullRanking = [...ranking];
  if (!seen.has(best)) {
    fullRanking.unshift(best);
    seen.add(best);
  }
  validLabels.forEach((label) => {
    if (!seen.has(label)) fullRanking.push(label);
  });

  return {
    best,
    ranking: fullRanking,
    confidence: normaliseConfidence(parsed.confidence),
    reasoning: String(parsed.reasoning ?? ''),
  };
}

/**
 * Judge a run's candidate responses and return a model-space verdict.
 */
export async function judgeRun(params: JudgeRunParams): Promise<JudgeVerdict> {
  const {
    scrEndpoint,
    deploymentType,
    judgeModel,
    judgeOptions,
    systemPrompt,
    userPrompt,
    includeSelf,
  } = params;

  const candidates = params.candidates.filter(
    (c) => includeSelf || c.modelName !== judgeModel
  );
  if (candidates.length < 2) {
    return { status: 'error', error: 'not-enough-candidates' };
  }

  // Randomise order and assign opaque labels.
  const shuffle = params.shuffle ?? defaultShuffle;
  const order = shuffle(candidates.length);
  const labelled = order.map((candidateIndex, position) => ({
    label: candidateLabel(position),
    modelName: candidates[candidateIndex].modelName,
    response: candidates[candidateIndex].response,
  }));
  const labelToModel = new Map(labelled.map((c) => [c.label, c.modelName]));
  const validLabels = new Set(labelled.map((c) => c.label));

  const judgeUserPrompt = buildJudgeUserPrompt(systemPrompt, userPrompt, labelled);

  let lastRaw = '';
  for (let attempt = 0; attempt < 2; attempt++) {
    const result = (await callSCRLLM(
      scrEndpoint,
      judgeModel,
      JUDGE_SYSTEM_PROMPT,
      judgeUserPrompt,
      judgeOptions,
      deploymentType
    )) as { response?: string; error?: string };

    if (result?.error) {
      return { status: 'error', error: result.error };
    }
    lastRaw = String(result?.response ?? '');
    const verdict = parseVerdict(lastRaw, validLabels);
    if (verdict) {
      return {
        status: 'ok',
        best: labelToModel.get(verdict.best),
        ranking: verdict.ranking
          .map((label) => labelToModel.get(label))
          .filter((m): m is string => Boolean(m)),
        confidence: verdict.confidence,
        reasoning: verdict.reasoning,
      };
    }
  }

  return { status: 'unparseable', raw: lastRaw };
}
