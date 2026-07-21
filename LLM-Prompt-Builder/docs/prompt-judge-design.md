# Design: Automated prompt judging for the Prompt Builder

**Status:** Phase 1 implemented on `feature/prompt-judge-eval` (verified end-to-end via the `LLM-Prompt-Builder:verify` mock + Playwright) · **Owner:** David Weik

## 1. Motivation

Today the Prompt Builder runs a prompt across several LLMs in parallel and lets a
prompt engineer decide which response is best. The only *automated* signals are
two mechanical flags computed in `annotatePrompts()` — `fastest_prompt` (lowest
`run_time`) and `fewest_tokens_prompt` (lowest `output_length`) — neither of
which says anything about **answer quality**. "Best" is a manual checkbox.

We want to add an automated, quality-oriented recommendation using the technique
the current research consensus supports for exactly this task: **LLM-as-a-Judge**.
This document specifies **Phase 1** in enough detail to implement, and sketches
**Phase 2 (LLM Council)** and **Phase 3 (DSPy)** so Phase 1's data model doesn't
paint us into a corner.

### Why this shape (research summary)

- LLMs are **markedly more reliable at relative comparison than at absolute
  scoring**, and the product's real question is "which of these N is best" — a
  comparison, not a 1–10 rating. So Phase 1 is an **N-way comparative ranking in a
  single judge call**, not per-response numeric scoring.
- **Reasoning before the verdict** is the single highest-leverage lever (it cut
  MT-Bench math-grading failures 70%→15%). The judge must think, then decide.
- **Position bias is real** even in N-way settings → present candidates in
  **randomized order** and judge by opaque label, not model name.
- **Self-preference bias is real** → the judge model **must not be one of the
  candidates being ranked**. The user picks a dedicated judge model.
- Keep the **human in the loop**: the judge *suggests* a best response; it never
  overwrites the user's `best_prompt` choice. This also yields free labelled data
  (judge pick vs. human pick) that a future DSPy phase would need.

## 2. Scope

**In scope (Phase 1)**
- A "Judge this run" action that sends all successful responses of one run to a
  user-selected judge model and returns a ranked verdict with rationale.
- Persist the verdict so it survives save/load and reaches the monitoring report.
- Surface the verdict in the existing experiment-tracker accordion; pre-tick the
  suggested best response's `best_prompt` checkbox (user can override).
- English + German UI strings.

**Out of scope (Phase 1)** — deferred to later phases
- Multi-judge panels / voting (Phase 2).
- Any Python / DSPy / prompt-optimization (Phase 3, server-side).
- Streaming, per-dimension rubric scoring, pairwise-with-swap re-judging of close
  calls (possible Phase 1.5 once we have real usage data).

## 3. Architecture fit

The app can only reach an LLM through `callSCRLLM()` (`src/api/scr-api.ts`) → an
SCR container. **A judge is just another `callSCRLLM` call** to a model that is
already deployed in the same `llmProjectID`. No new endpoint, no backend, no
Python. This is why judging fits the browser tool and DSPy does not (see §7).

The judge call uses the same 3-input SCR contract as every other call:
`{ inputs: [{name:'systemPrompt'}, {name:'userPrompt'}, {name:'options'}] }`, and
returns the same `{ response, run_time, prompt_length, output_length }` envelope.
The judge's JSON verdict comes back inside `response` and we parse it defensively.

## 4. Phase 1 detailed design

### 4.1 Judge model selection

Add a **judge-model selector** to the run controls, populated from the same
`promptBuilderAvailableLLMs` list already loaded from `llmProjectID`.

- **Deployment default (O-1, resolved).** A `judgeModel` default can be set per
  deployment via the **DDC options group** in `src/va/ddc.ts` (rendered in VA's
  Properties panel) and mirrored into the iframe URL, so it is also
  **URL-overridable** in `src/config.ts` (`URL_OVERRIDABLE`). It is a model *name*,
  not a secret, so URL/DDC delivery is fine. When present, the selector defaults to
  it; **the in-app selector always wins** — the user can override per session.
- **No default set, none selected** → the "Judge this run" button is disabled with
  a tooltip ("Select a judge model").
- **Self-exclusion, with an opt-in override.** By default, when the judge model is
  also a candidate in the run we **exclude its own response from the set it ranks**
  (mitigates self-preference) and show a one-line note. A **"Include the judge's own
  response" checkbox** next to the selector lets the user override this and let the
  judge rank its own output — off by default; when on, the note flips to warn that
  self-preference bias may inflate that response. If self-exclusion (with the box
  off) would leave < 2 responses to compare, we tell the user to either tick the
  include box or pick a different judge.
- No new secret needed: the judge is invoked with the same API-key resolution path
  as candidates (`API_KEY` pulled from `promptBuilderObject.API_KEYS` by the model's
  `options.json` key).

### 4.2 Judge invocation

New module `src/api/judge-api.ts` (keeps `scr-api.ts` a thin transport):

```
judgeRun(params): Promise<JudgeVerdict>
  params: {
    scrEndpoint, deploymentType,
    judgeModel: string,
    judgeApiKeyOptions: Record<string, unknown>,   // API_KEY etc. for the judge
    systemPrompt, userPrompt,                        // the RESOLVED prompts of the run
    candidates: { label: string; modelName: string; response: string }[]
  }
```

Steps:
1. Take the run's successful results (skip any with `data.error`). Drop the judge
   model's own response **unless** the "include the judge's own response" override
   is on. `params` therefore also carries `includeSelf: boolean`.
2. **Shuffle** the candidates and assign opaque labels `A`, `B`, `C`, … (record
   the label→modelName map locally; never send the model name to the judge).
3. Build the judge prompt (§4.3) and call `callSCRLLM(...)` with the judge model,
   `temperature: 0` (or the lowest the option vocabulary allows) for
   reproducibility, and a generous `max_tokens`.
4. Parse the JSON verdict defensively: strip code fences, regex-extract the first
   `{...}` block, `JSON.parse`, validate against the expected shape; on failure,
   **retry once**; on second failure return `{ status: 'unparseable', raw }`.
5. Map the winning label back to its `modelName`.

### 4.3 Judge prompt (template)

System:
```
You are an impartial evaluator. You will see a task (a system prompt and a user
prompt that were given to several AI assistants) and several candidate responses,
each identified only by a letter. Judge only the quality of the responses for the
given task. Ignore response length and formatting except where they affect quality.
Do not assume any letter is better because of its position.

Think step by step about each candidate's accuracy, relevance to the task,
completeness, and clarity. THEN choose the single best candidate.

Return ONLY a JSON object, no prose outside it, with this exact shape:
{
  "reasoning": "<your step-by-step comparison>",
  "ranking": ["<letters, best first>"],
  "best": "<the single best letter>",
  "confidence": "high" | "medium" | "low"
}
```

User:
```
== TASK: SYSTEM PROMPT ==
{{resolvedSystemPrompt}}

== TASK: USER PROMPT ==
{{resolvedUserPrompt}}

== CANDIDATE RESPONSES ==
[A]
{{responseA}}

[B]
{{responseB}}
... (one block per candidate, in randomized order)

Return the JSON object now.
```

Notes: `reasoning` is required and must come **before** `best` in the schema so the
model reasons first. `confidence: low` is what the UI keys off to flag a weak call
(and is the natural trigger for a Phase-2 panel escalation later).

### 4.4 Data model changes

Add **one optional field** to the per-model result and thread it through the whole
persistence chain. Everything is optional/nullable so **existing trackers keep
loading unchanged**.

`src/objects/prompt-builder.ts`:

1. `ModelExperimentData` (~`:116`): add
   ```
   judge_rank?: number | null;        // 1 = judge's best, 2, 3, … ; null = not judged
   judge_best?: boolean | null;       // convenience flag for the winner
   ```
   (Rank is more future-proof than a bare boolean and lets the report show order.)
2. `ExperimentResult.data` (~`:62`): allow the same optional fields so
   `annotatePrompts`-style code can write them.
3. Store a **run-level** judge summary on the tracker entry (metadata, like
   `manifest`): extend `ExperimentTrackerEntry` (~`:85`) with
   ```
   judge?: {
     judgeModel: string;
     best: string;                 // winning modelName
     ranking: string[];            // modelNames, best first
     confidence: 'high'|'medium'|'low'|'unknown';
     reasoning: string;
     excludedSelf: boolean;
     ranAt: string;                // ISO string (from the SCR run, not Date.now — see note)
   } | null;
   ```
   Add `'judge'` to `TRACKER_META_KEYS` (`:94`) so it is treated as metadata, not a
   model result, by `promptExperimentTransformData`.
4. The run handler (`promptBuilderRunExperiment`, `:1375`) gains a small tail:
   after it renders results, if the **"Auto-judge when the experiment finishes"**
   toggle is on (O-2, resolved: yes) *and* a judge model is selected *and* the run
   has ≥ 2 judgeable responses, it calls `promptBuilderJudgeRun(...)` for the run
   just completed. Guard it so a judge failure never breaks the run flow (the run's
   results are already rendered; the judge failure surfaces only in the verdict
   banner). Manual judging via the per-run button (§4.5) remains available whether
   or not auto-judge is on.
5. New handler `promptBuilderJudgeRun(runIndex)`:
   - Calls `judgeRun(...)` (honoring the include-self override), writes
     `judge_rank`/`judge_best` onto each `trackerEntry[modelName]`, writes the
     run-level `judge` summary, then pre-ticks the winner's `best_prompt` **only if
     the user hasn't already set one in this run** (never override a human choice).
   - Re-renders via `createPromptExperimentTracker(...)` and refreshes `petRows`.
   - Shows an in-flight state on the run (a spinner on the Judge button / banner)
     so an auto-triggered judge is visibly distinct from a finished one.
6. `PETRow` (`:127`): add `judge_rank: number | null` and `judge_best: boolean | null`.
7. `promptExperimentTransformData` (`:1879`):
   - On the **header row** (MODELINDEX 0) emit the run-level judge fields (e.g.
     `judge_model`, `judge_confidence`) — mirrors how `variables`/`manifest` ride
     the header row.
   - On each **model row** emit `judge_rank` / `judge_best`.
8. Loader (the `onchange` at `:281` that rebuilds runs from the tracker JSON): read
   the new fields back with `exist`-style guards so pre-judge trackers load fine.

> **Time note:** `Date.now()` / `new Date()` — fine in the browser app (this
> constraint only applies to Workflow scripts). Use the SCR-reported time or a
> plain `new Date().toISOString()` for `ranAt`.

### 4.5 UI

Run controls (near the judge-model selector, §4.1):
- The **judge-model selector** and the **"Include the judge's own response"**
  checkbox (§4.1, off by default).
- An **"Auto-judge when the experiment finishes"** checkbox (§4.4 point 4). When on,
  a judge call fires automatically after each run completes; the "Judge this run"
  button stays available for manual re-judging.

In `createPromptExperimentTracker` (~`:1532`):
- Add a **"Judge this run"** button to each run's accordion header (next to
  load/delete), disabled while an experiment is running or when no judge model is
  selected / < 2 judgeable responses; shows a spinner while a judge call is in
  flight (including an auto-triggered one).
- On a completed verdict, render a compact **verdict banner** in the run body:
  winner (by model name), the ranking, a `confidence` chip, and the judge's
  `reasoning` behind a "Show reasoning" disclosure (render with the existing
  `renderMarkdown`). Show the "self excluded" note, or — when the include override
  is on — the "self included (bias possible)" warning.
- **Fourth run-icon.** In each model's sub-accordion header, add a
  **"best-judged" icon as a peer of the existing three** (`best_prompt` party,
  `fastest_prompt` lightning, `fewest_tokens_prompt` chevrons) — a distinct
  trophy/medal SVG shown when `judge_best` is true, following the same
  SVG-injection pattern at `:1642` and driven by the same per-model data. Optionally
  annotate non-winners with their `judge_rank` ("#2", "#3"). Add a matching legend/
  tooltip entry alongside the existing three icons' explanations.
- The winner's `best_prompt` checkbox is pre-ticked (revocable). The banner says
  the pick is a suggestion.

### 4.6 i18n

Add keys under `promptBuilder` in `src/i18n/locales/en.json` and `de.json`
(the two are kept in lockstep): judge selector label + placeholder, the
"Include the judge's own response" and "Auto-judge when the experiment finishes"
toggle labels, button label + running status, verdict banner labels (winner /
ranking / confidence high|medium|low / show reasoning / self-excluded note /
self-included-bias warning), the fourth-icon legend/tooltip text, and
error/unparseable messages. Match the existing `promptBuilder*` naming.

### 4.7 Server-side reporting (SAS) — required, not optional

A new field is invisible downstream until the SAS scripts carry it. Two scripts are
**required**; the SAS-code helper scripts are consistency-only.

**Required**
- `SAS-Viya-Integrations/Logging-Monitoring/Get-All-Prompts.sas` — add `judge_rank`
  / `judge_best` (and run-level `judge_model`, `judge_confidence`) to the two
  `length` lists (`:270`, `:324`), the `proc sql` select (~`:348`), and the label
  block (~`:385`); they then flow into `Public.PROMPT_EXPERIMENTS` for VA. Guard
  every nested read with `exist()` exactly like the `variables`/`manifest` reads, so
  older trackers report null/0 and never break the script.
- `SAS-Viya-Integrations/SAS-Code-LLM-Calls/Track-Prompt-Experiments.sas` — extend
  the `length` list (`:333`) and the read/append logic so a server-side append round-
  trips the new columns instead of dropping them.

**Consistency (nice-to-have, same release)**
- `Get-Prompt-Experiments.sas:175`, `LLM-Call-Result-Table.sas:27`,
  `Manual-Prompt-Experiment-Tracker.sas:16` — these define the PETRow column list
  for the SAS-code (non-browser) paths; add the fields so a hand-built tracker and
  the browser-built tracker have identical schemas.

### 4.8 Build / verify / release

- Rebuild the single-file bundle: `npm run build` (CI fails the PR if the committed
  `dist/index.html` is stale — see `.github/workflows/verify-prompt-builder.yml`).
- Drive the built app with the scoped **`LLM-Prompt-Builder:verify`** skill (mock
  SAS Viya server + Playwright) — extend its mock to serve a fake judge response so
  the verdict path is exercised end-to-end, not just built.
- Re-export the transfer package
  `SAS-Viya-Integrations/SAS-Agentic-AI-Accelerator-Prompt-Builder.json` (as prior
  Prompt Builder releases did) so new deployments ship the feature.
- **CHANGELOG.md**: new `Added` entry under a **new minor version** (O-4, resolved),
  per the feature-branch + CHANGELOG workflow, noting the re-upload of
  `dist/index.html` to the Job Execution definition, the re-export of the transfer
  package, and the new `PROMPT_EXPERIMENTS` columns.
- Docs: a short section in `LLM-Prompt-Builder/README.md` and the relevant
  Administration/User guide page under `website/docs/`.

## 5. Failure modes & handling

| Case | Handling |
|---|---|
| Judge call errors / non-200 | Show the SCR error text in the run banner; leave results unjudged; button re-enabled. |
| Verdict JSON unparseable (after 1 retry) | Banner: "Judge returned an unreadable verdict"; optionally show raw text; no `best_prompt` change. |
| Winning label maps to nothing | Treat as unparseable. |
| Only one judgeable response | Disable judging with an explanatory tooltip. |
| Judge is the sole other model and gets self-excluded to < 2 | Prompt the user to either tick "Include the judge's own response" or pick a different judge. |
| Auto-judge on, but no judge model selected / < 2 responses | Skip the auto-judge silently (no error); manual button explains why it's disabled. |
| Run has a failed candidate | Judge only the successful ones; note that N were excluded. |

## 6. Testing

- **Unit**: verdict parser (fenced JSON, extra prose, wrong casing, missing
  `best`, out-of-range label); label shuffling + label→model remap; self-exclusion
  logic; transform round-trip (`ModelExperimentData` → `PETRow` → back) with and
  without judge fields.
- **Backward compat**: load a pre-judge `Prompt-Experiment-Tracker.json` and
  confirm it renders and re-saves with null judge fields.
- **E2E** (`LLM-Prompt-Builder:verify`): select judge → run → judge → verdict
  banner + badge + pre-ticked best; user override persists through save/load.
- **SAS**: run `Get-All-Prompts.sas` against new-style, old-style, and partial
  trackers (mirrors how the 1.3.0 metadata columns were verified).

## 7. Phase 2 & 3 (forward-looking — not built now)

**Phase 2 — LLM Council / jury.** The tool already runs N models, so upgrading the
single judge to a **3-model heterogeneous panel** is natural: reuse selected models
as judges, **exclude each from judging its own response**, keep anonymization,
aggregate by majority/Borda vote, and **surface disagreement honestly** (show the
ballot, label a genuine split "judges disagreed" rather than forcing a winner). A
chairman/tiebreaker call only on disagreement — don't run debate by default (its
gains concentrate on ambiguous cases; returns saturate past ~3–5 judges). The
Phase-1 data model already supports this: `judge` becomes an array of ballots + an
aggregate, and `judge_rank` becomes the aggregated rank. A `confidence: low`
Phase-1 verdict is the natural trigger to offer a panel re-judge.

**Phase 3 — DSPy (server-side only, separate track).** DSPy is Python, offline,
compile-once, and needs a curated 30–300-example dataset + a metric; it **cannot
run in the browser app**. Its right home is an experimental CLI sibling to the
Model Definition Builder (or a SAS Job Execution / SCR-container job) that points
`dspy.LM("openai/<scr-model>", api_base=<SCREndpoint>, ...)` at an SCR endpoint,
runs `BootstrapFewShot`/`MIPROv2`, and "bakes out" the optimized instructions+demos
to plain text stored in Model Manager, which the browser tool then consumes
passively. Its optimization metric would itself be an LLM-as-judge — **so Phase 1
is a prerequisite for Phase 3 either way.** Not scheduled here.

## 8. Open questions — resolved

- **O-1 — RESOLVED.** Support both: a deployment default `judgeModel` via the DDC
  options group (and thus URL-overridable), with the in-app selector always winning.
  See §4.1.
- **O-2 — RESOLVED.** Provide an "Auto-judge when the experiment finishes" toggle
  *and* keep the explicit per-run button. See §4.4 point 4 and §4.5.
- **O-3 — RESOLVED.** Store `judge_rank` per model; the run-level winner is
  `judge_rank === 1` and needs no separate stored field (the run-level `judge`
  summary still records the winner name for convenience/reporting). See §4.4.
- **O-4 — RESOLVED.** Ship as a **new minor version**.

## 9. Decisions incorporated (2026-07-21 review)

- Deployment-default judge model via DDC options, overridable in-app (O-1).
- Self-exclusion of the judge's own response is the default, with an opt-in
  "include the judge's own response" override (with a bias warning when on).
- Auto-judge-on-finish toggle in addition to the manual per-run button (O-2).
- A fourth per-model run-icon for the best-judged response, peer to the existing
  best/fastest/fewest icons.
- `judge_rank` per model; winner derived (O-3). New minor version (O-4).

## 10. Post-implementation refinements (2026-07-21)

Changes made after reviewing the working Phase 1 build; these supersede the
earlier sections where they differ:

- **Dedicated "Judge the responses" section.** The judge controls moved out of
  the workbench into their own `.pb-section` between the workbench and the
  tracker (six sections total). This is the home that Phase 2 (council/jury)
  controls will fold into. Per-run verdict banners and the Judge button stay in
  the tracker (they are per-run results/actions).
- **The judge is advisory — no auto-selection of Best Response.** Judging no
  longer pre-ticks the winner's Best Response; it only sets `judge_rank` + the
  fourth icon + the banner. Best Response stays a purely manual choice (this is
  the whole point of having a separate judge rank). The suggestion note was
  reworded accordingly.
- **Reasoning is persisted.** Contrary to §4.4/§4.5, the judge's `reasoning` IS
  stored on the header row (`judge_reasoning`) so a reloaded run shows the full
  rationale rather than a "not stored" placeholder.
- **Judge config is persisted and restored on load.** The run header also stores
  `judge_include_self` and `judge_auto`; loading a run (via Load or the auto-load
  of the most recent best run) restores the judge model, the include-self toggle
  and the auto-judge toggle onto the controls.
- **Info icon on include-self.** The "Include the judge's own response" toggle
  has an info icon (Bootstrap tooltip, matching the option tooltips) explaining
  self-preference bias.
- **Expanded runs stay expanded after judging.** `renderAllExperimentRuns(true)`
  snapshots open accordion bodies and restores them after the post-judge
  re-render, so a run the user had open doesn't collapse under them.
- **SAS.** `Track-Prompt-Experiments.sas` carries `judge_reasoning`,
  `judge_include_self`, `judge_auto` for round-trip; `Get-All-Prompts.sas` keeps
  `judge_rank`/`judge_best`/`judge_model`/`judge_confidence` (reasoning/config are
  not surfaced in `PROMPT_EXPERIMENTS`).
