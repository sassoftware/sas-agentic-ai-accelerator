/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Reading a retrieval probe's output.
 *
 * The RAG Builder's Test retrieval runs Test-Retrieval.sas as a job that is
 * deleted the moment it ends, so its hits come back in the LOG rather than in
 * a table - a table would itself be an artifact of a test that is supposed to
 * leave none. This module is the other half of that decision: it turns the
 * probe's milestone lines back into rows.
 *
 * Kept apart from the Builder so it can be exercised without a browser.
 */

/** One hit of a retrieval test, as Test-Retrieval.sas reports it. */
export interface RetrievalHit {
  rank: number;
  score: number;
  distance: number;
  source: string;
  heading: string;
  /**
   * The context header the Enrich stage wrote, '' when the setup has none.
   *
   * Worth its own field rather than being folded into the content: it is the
   * one thing enrichment changed, it was embedded WITH the chunk, and a
   * hallucinated one is invisible at query time and permanent in the index.
   * Reading a few here is the cheapest check there is.
   */
  header: string;
  page: string;
  content: string;
  error: string;
}

/** Everything the retrieval probe said, sorted into its four kinds of line. */
export interface RetrievalTestLog {
  hits: RetrievalHit[];
  /** The `rag cost:` line, verbatim — it explains itself. */
  cost: string;
  /** The DONE summary, present only once the probe finished its work. */
  done: string;
  /** The FAILED reason, when the probe caught something. */
  failure: string;
}

/**
 * Read a retrieval probe's milestone lines.
 *
 * Every line arrives prefixed RAGRETRIEVE, and hits arrive one JSON object per
 * line — which is what makes a truncated or still-streaming log cost the LAST
 * rows rather than corrupting the read. An unparseable line is therefore
 * dropped on its own instead of failing the whole parse.
 *
 * The probe re-encodes % and & as their JSON escapes before writing a row, so
 * no line it emits can carry a SAS macro trigger; JSON.parse turns them back
 * into the characters the document actually contains.
 */
export function parseRetrievalLog(messages: string[]): RetrievalTestLog {
  const hits: RetrievalHit[] = [];
  let cost = '';
  let done = '';
  let failure = '';
  for (const raw of messages) {
    const message = raw.replace(/^RAGRETRIEVE\s+/, '');
    if (message.startsWith('ROW ')) {
      try {
        const row = JSON.parse(message.slice(4)) as Record<string, unknown>;
        hits.push({
          rank: Number(row.rank ?? 0),
          score: Number(row.score ?? 0),
          distance: Number(row.distance ?? 0),
          source: String(row.source ?? ''),
          heading: String(row.heading ?? ''),
          // absent from a probe deployed before enrichment existed
          header: String(row.header ?? ''),
          page: row.page === null || row.page === undefined ? '' : String(row.page),
          content: String(row.content ?? ''),
          error: String(row.error ?? ''),
        });
      } catch {
        /* a half-written line while the log still streams */
      }
    } else if (message.startsWith('DONE ')) {
      done = message.slice(5).trim();
    } else if (message.startsWith('FAILED ')) {
      failure = message.slice(7).trim();
    } else if (message.startsWith('rag cost:')) {
      cost = message.trim();
    }
  }
  return { hits, cost, done, failure };
}

/**
 * A question that survives becoming a macro variable.
 *
 * Job Execution hands parameters to the program as macro variables, so a `;`
 * would end the assignment early and `&`/`%` would be resolved against
 * whatever happens to be defined. Replacing them costs a character of a
 * question people rarely type; leaving them in costs the run. The caller says
 * so when anything changed rather than quietly asking a different question.
 */
export function macroSafeQuestion(question: string): { value: string; changed: boolean } {
  const value = question.replace(/[;&%]/g, ' ').replace(/\s+/g, ' ').trim();
  return { value, changed: value !== question.trim() };
}
