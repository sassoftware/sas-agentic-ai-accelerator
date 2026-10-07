---
name: verify
description: Verify LLM Prompt Builder changes end-to-end by serving the built dist/index.html from a mock SAS Viya server and driving it with Playwright against the system Edge browser. Use after changing LLM-Prompt-Builder source to observe the real UI instead of only building.
---

# Verify the LLM Prompt Builder without a live SAS Viya

The app is a single-file browser bundle that calls SAS Viya REST APIs on its
own origin (`viyaHost` defaults to `window.location.origin`). That makes it
fully drivable against a local mock: serve `dist/index.html` from a Node
server that also implements the handful of Viya endpoints the app calls.

## Recipe

1. Build: `npm run build` in `LLM-Prompt-Builder/` (runs `tsc --noEmit` first;
   the committed `dist/index.html` must match a fresh build or the
   `verify-prompt-builder.yml` CI check fails).
2. Start the mock (records every request; `GET /__log` returns them,
   `POST /__reset` clears):
   `node mock-server.js <abs-path-to>/LLM-Prompt-Builder/dist/index.html`
   (serves on http://localhost:4173 — see `mock-server.js` next to this file).
   The mock is CommonJS but `LLM-Prompt-Builder/package.json` declares
   `"type": "module"`, so running it in place fails — copy it to a scratch
   directory as `mock-server.cjs` first.
3. Drive with Playwright using the system Edge (no browser download needed):
   `npm i playwright` in a scratch dir, then
   `chromium.launch({ channel: 'msedge', headless: true })`.
   `verify.js` next to this file covers the deletion flows end-to-end and is a
   good template for new flows.
4. Open the app with config in the URL (no VA embed needed):
   `http://localhost:4173/?modelRepositoryID=repo-1&llmProjectID=llm-proj&SCREndpoint=http://localhost:9/scr`

## Endpoints the app needs mocked at minimum

- `GET /identities/users/@currentUser`
- `GET /modelRepository/projects` (project dropdown, filter `Prompt-Engineering`)
- `GET /modelRepository/projects/{llmProjectID}/models` (plus the same URL with
  `filter=eq(tags,'deprecated')` returning empty) and per-LLM
  `GET /modelRepository/models/{id}/contents` → an `options.json` entry whose
  `fileUri` + `/content` returns e.g. `{ "temperature": { "default": 0.7 } }` —
  without this the whole builder fails to load
- `GET /modelRepository/projects/{id}/models` for the selected project
- `GET /modelRepository/models/{id}/contents` → `Prompt-Experiment-Tracker.json`
  entry; its `{fileUri}/content` returns the PETRow[] fixture
- `POST /relationships/relationships`, `GET /decisions/flows/{id}`
- Save flow: `POST .../modelVersions` (200 JSON), `DELETE .../contents/{id}`
  (204), `POST .../contents?...` (201 JSON, multipart body)
- Optimize flow (enable with URL params `enableOptimization=true&computeContext=...`
  `&optimizeJobProgram=/Public/Jobs/Optimize-Prompt-DSPy&minOptimizeSamples=1`):
  `GET /folders/folders/@item?path=...` → job-definition URI,
  `POST /jobExecution/jobs` + `GET /jobExecution/jobs/{id}` (the mock job
  completes on the second poll), the job log at `/files/files/joblog-1/content`
  with `NOTE: Python-Subprocess - ...` milestone lines, and — after completion —
  a `Prompt-Optimization-Tracker.json` entry on the source model

## Gotchas

- Element ids are prefixed with the config `id` (default `LPB`) and the pane id
  `app`: dropdowns are `#LPB-project-dropdown` / `#LPB-prompt-dropdown`, the
  tracker container is `#app-obj-LPB-pet`, run accordions `#app-obj-LPB-pet-{i}`
  (newest run first in the DOM).
- The page is a flow of steps and only the current step's pane is visible, so
  Playwright cannot act on a control of another step: open its step first with
  `#app-obj-LPB-step-{setup|build|optimize|finalize}` (pane:
  `#app-obj-LPB-pane-{key}`, Back/Continue: `#app-obj-LPB-stepper-back` /
  `-continue`). Project and prompt live on `setup`; LLMs, judge controls,
  variables, prompts, Run Experiments, the tracker and Save Experiments on
  `build`; manifest on `finalize`. The `optimize` step and its pane exist only
  with `enableOptimization=true`, between `build` and `finalize` - three steps
  otherwise. Only the current step is highlighted (`.pb-step.is-current`);
  there is no done state. Running and loading a
  run stay on `build`; loading an optimized prompt moves from `optimize` to
  `build`. The RAG Builder uses the same shell with the `RGB` id and the keys
  `setup`, `documents`, `store`, `run`.
- Bootstrap modals: the `.show` class disappears at the START of the hide
  transition, but promise resolution / button re-enabling happens on
  `hidden.bs.modal` (~300ms later) — poll instead of asserting immediately.
  Escape only dismisses after the fade-in finished (focus trap active); wait for
  `document.activeElement` to be the modal before pressing it.
- The run-header system prompt element id contains a pre-existing typo:
  `...-run-systenPrompt`.
- `verify-decision.js` next to this file drives the decision mode: after
  `POST /__decision` the mock lists a second project `proj-2` whose
  `Ticket Routing` prompt (`model-dec`, tagged `Decision-Template`) opens with a
  saved run, the decision models come from `dec-proj` (`jev_mock` needs the
  OpenAI entry, `von_mock` no key), a POST to `/scr/<model>/<model>` whose inputs
  carry `state` answers in the System One shape, and `Public.DEC_CASES` is a
  labelled table (`expected_team`, `expected_escalate`). The kind is chosen on
  the Setup card (`#LPB-kind-llm` / `#LPB-kind-decision`); each view lists its
  own projects (`proj-2` answers the `Decision-Engineering` tag filter, a POST
  to `/modelRepository/projects` creates `proj-new`) and its own prompt-tests
  (the mock answers the `Decision-Template` tag filter per project). The Evaluate step
  (`#app-obj-LPB-step-evaluate`, pane `-pane-evaluate`) exists on every page
  but is hidden (`li[hidden]`) for an LLM prompt; the page therefore has seven
  `.pb-section` cards. Designer ids: `#app-obj-LPB-decision-state`,
  `#app-obj-LPB-decision-add-question`, rows `.pb-question-row` with
  `.pb-question-id/-type/-instructions/-criteria/-expected`, each with a
  `.pb-question-lint` (items `.pb-lint-item.is-fix/-warn/-tip`, the escape
  fix `.pb-question-add-escape`); the checklist is
  `#app-obj-LPB-decision-checklist`, the template picker
  `#app-obj-LPB-decision-template` + `-apply`, help toggles `.pb-help-toggle`
  reveal `.pb-help`. Decision models are cards (`.pb-model-card` with
  `-meta`, `-price`, `.pb-model-fit.is-ok/.is-no`) whose checkboxes stay
  `#decision-model{i}`; the mock's `dec-1`/`dec-2` details carry the
  `properties` (contextLength, maxOptions, questionTypes) and tags the cards
  read - `von_mock` takes choice and noul only, so a score question makes it
  unfit and skipped. A run's answers carry `.pb-answer-reading` and a
  `.pb-gate.is-auto/-escalate/-abstain`; a run with two models gets
  `#app-obj-LPB-pet-{i}-run-compare`. The mock answers a choice with its
  escape option when no keyword matches (the fourth `DEC_CASES` row), and
  the evaluation shows `.pb-reliability` with ECE and Brier.
- Run each verify script against a FRESHLY started mock instance: the optimize
  flow mutates mock state (job polls, the optimization tracker appearing in
  the model contents) that can break other suites run against the same
  instance.
