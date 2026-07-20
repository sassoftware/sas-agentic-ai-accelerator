# Changelog

This changelog documents all the different updates that occur for this framework.

## [1.3.0] - 2026-07-20

This release consolidates the framework onto the `mdb` CLI as the single way to register and publish. The standalone `Model-Manager-Setup.py`, `utility/prompt-builder-json.py` and the four `register-*.py` / `publish-*.py` scripts are removed in favour of `mdb setup` / `register` / `publish` / `ship`, which cover both LLM and Embedding definitions from one place and do more besides (`--all` across the fleet, in-place `--update`, build-completion `--wait`). Install the CLI once with `pip install -e Model-Definition-Builder/cli[viya]`; the Administration and User guides are updated throughout.

**Action needed for the provider fix:** every self-hosted definition's `provider` tag previously held the model's *license* rather than its provider. Twenty definitions are corrected — **re-register the affected models with `mdb register --update`** to refresh the provider recorded in SAS Model Manager. No re-publish is required, since no score code changed.

The prompt-monitoring `PROMPT_EXPERIMENTS` table also gains columns for the newer typed scoring options and for per-run variable / output-variable / integrated-call metadata, so nothing the Prompt Builder records is dropped from the report anymore.

### Changed

- **`Get-All-Prompts.sas` now surfaces the newer experiment metadata in the `PROMPT_EXPERIMENTS` table.** Its option parser was a fixed allow-list (`temperature`, `top_p`, `top_k`, `max_tokens`) that silently dropped every other option, so the typed options the Prompt Builder has emitted since 1.1.0 — `reasoning_effort`, `thinking_budget`, `max_completion_tokens`, `seed`, the penalties, and the embedding options `input_type` / `dimensions` / `normalize` — never reached the report. Each now has its own column. The table also gains `variables_count` (how many custom input variables a run used), `integrated_llm_call` (whether the run combined the LLM call into the manifested model) and `output_variables_count` (how many output variables it parses), read from the run's `variables` and `manifest` structures in the tracker. Older trackers that predate these fields are handled gracefully — every nested read is guarded, so a run without them simply reports zero and never breaks the script. Verified end-to-end against a live SAS session across new-style, old-style and partial trackers

### Removed

- **`Model-Manager-Setup.py` and `utility/prompt-builder-json.py` are removed**, superseded by `mdb setup` (added in 1.1.0). `mdb setup` creates the `LLM Repository` and both model projects, writes the `sas-viya-cli-commands.txt` authorization rules and the `llm-prompt-builder.json` / `rag-builder.json` builder seeds, and is idempotent — everything the two scripts did. Install the CLI with `pip install -e Model-Definition-Builder/cli[viya]` and run `mdb setup`; connection details come from the same `.env` the scripts used. This also retires the unused `-dt/--deployment_type` flag that `prompt-builder-json.py` accepted but never wrote to its output. The Administration Guide's "Setup SAS Model Manager" chapter and the related references now document `mdb setup`
- **The `register-LLMs.py`, `publish-LLMs.py`, `register-Embedding.py` and `publish-Embedding.py` scripts are removed**, superseded by `mdb register`, `mdb publish` and `mdb ship` — one path for both LLM and Embedding definitions. Every shipped definition is on a manifest, so `mdb register --all` / `mdb publish --all` cover the whole fleet in one go, `mdb register --update` updates a model in place (new minor version + content replacement), and `mdb publish --wait` polls the image build to completion — none of which the scripts did. The generated definition READMEs, both "Register & Publish" guide chapters, the Model Definition Builder pages and `.env.example` now document the `mdb` commands. As a bonus, the generated embedding READMEs previously printed the wrong `register-LLMs.py` command (the README template is shared across kinds); they now correctly show `mdb register <id>`, which works for either kind

### Fixed

- **Every self-hosted definition recorded a license as its provider.** `tags.provider_tag` carried the license and the real provider sat in `tags.extra`, so SAS Model Manager stored `smollm_135m` as provider `Apache-2`, the Qwen models as `Apache-2`, the Phi models as `MIT-License`, and so on — only the hosted API models (OpenAI, Anthropic, Google, Mistral, AWS Bedrock, Voyage.ai) were correct. Twenty definitions are corrected: twelve are straight swaps where the two values trade places (llama → `Meta`, `mistral_nemo` → `Mistral`, phi → `Microsoft`, qwen → `Alibaba-Cloud`, smollm → `HuggingFace`) and keep a byte-identical tag set; seven had no provider tag at all and now gain one (bge → `BAAI`, granite → `IBM`, `all_minilm_l6_v2` → `HuggingFace`, `embedding_gemma_300m` → `Google`), with the license moving to `extra` so nothing is lost; and `voyage_code_3` is normalized from `Voyage` to `Voyage.ai` to match its four siblings. Both fact sheets are synced along with the manifests, because `mdb register` reads `fact_row["provider"]` before falling back to `manifest.tags.provider_tag`. **Re-register with `mdb register --update`** to refresh the attribute on already-registered models — no re-publish is needed, since no score code changed
- `SAS-Viya-Integrations/SAS-Code-LLM-Calls/Test-DS2-Scoring-from-SAS-Studio.sas` sent an option named `max_options`, which is not part of the scoring vocabulary and was silently ignored, so the smoke test never actually capped output. Corrected to `max_tokens`
- `SAS-Viya-Integrations/SAS-Code-Model-Manager-Interaction/MM-Get-Models-Information.sas` shipped with a real-looking example model id hardcoded in the `_mgi_model_id` macro variable; it is now an empty placeholder, matching the sibling `MM-Get-*` scripts, so nobody accidentally queries a stale id

## [1.2.0] - 2026-07-20

Open-weight models can now keep their weights **outside** the container image. A new `runtime.weights_source: mounted` setting stages the weights once into a shared `ReadWriteMany` volume that every model container reads at run time, so a 7B model no longer puts ~14 GB into the image, the registry and every node that pulls it — and one staged copy serves every model, replica and republish instead of the data existing in several places. Publishes get correspondingly faster, because the weight download stops being part of the build. Existing definitions are unaffected until you opt in: the default stays `baked`.

The Administration Guide is also updated for the SAS Viya platform release 2025.10, in which `sas-decisions-runtime` was merged into `sas-model-publish` and the Build Kit paths moved.

### Added

- `runtime.weights_source` in `definition.yaml` (`baked` | `mounted`, default `baked`). With `mounted`, `mdb generate` drops the weight download from `requirements.json` and points the score code at `/pybox/model/mount/<model_id>`; the published image then carries only score code and Python dependencies
- `SCR-LLM-Deployment-YAML/llm-weights-pvc-template.yaml` — the shared `ReadWriteMany` weight store, applied once per namespace. Every self-hosted model Deployment mounts this same claim read-only at `/pybox/model/mount`
- `SCR-LLM-Deployment-YAML/stage-weights-job-template.yaml` — a one-off Job that downloads a model into the shared volume. This is also where a Hugging Face token is supplied for gated repositories, so the token is used by an ordinary Kubernetes Job you control rather than by a container build
- Administration Guide page "Serving Open-Weight Models" covering both approaches and when staging is worth the extra setup

### Fixed

- **`deploy-modelName-PV-template.yaml` could not be deployed.** It mounted a volume named `llm-pv` but never declared it, so the API server rejected every Deployment rendered from it: `spec.template.spec.containers[0].volumeMounts[0].name: Not found: "llm-pv"`. Since `mdb deploy` selects this template automatically for every self-hosted model, that affected all of them. The template now declares the volume and binds it to the `llm-weights` claim. Note that a client-side `kubectl apply --dry-run` accepts the broken manifest — the schema is valid and only the cross-reference is wrong — so use `--dry-run=server` when validating rendered manifests
- `mdb generate` no longer emits `hf login --token $(cat /etc/secret-volume/huggingfacetoken)` for gated repositories. That step could not authenticate, so a gated definition now requires `weights_source: mounted` and `mdb generate` reports a clear error instead of producing a definition whose build dies at the download step. No shipped definition is gated, so the fleet is unaffected

### Changed

- The Administration Guide page "Configuration for Publishing" is updated for the SAS Viya platform release **2025.10**, in which `sas-decisions-runtime` was merged into `sas-model-publish` (reported in [#7](https://github.com/sassoftware/sas-agentic-ai-accelerator/issues/7), thanks @cjguerrap). The page referenced `sas-bases/examples/sas-decisions-runtime/buildkit/README.md`, a path that no longer exists — the assets are now at `sas-bases/examples/sas-model-publish/buildkit/` (configuration) and `sas-bases/overlays/sas-model-publish/buildkit/` (pod templates). Verified against the 2026.03 LTS deployment assets
- Build Kit resource sizing no longer works by editing a pod template. The page now documents the supported path — `configuration.env` (`buildkitCpuRequest`, `buildkitMemoryRequest`, `buildkitMemoryLimit`, `buildkitMaxReplicas`, storage size/class) plus the `buildkit-transformer.yaml` entry — and notes the `ReadWriteMany` PVC requirement, the transformer ordering needed when HA is enabled, and the `buildkit-remove-limits.yaml` overlay. The YAML snippet the page previously showed was kaniko-era (container `buildkitd` with `--dockerfile`/`--context`/`--ignore-path` args) and no longer matches the shipped templates, which run `buildctl` against a separate daemon
- The page explains the new two-component build architecture, because it changes what customization is even possible: image builds now execute on the long-lived `sas-buildkitd` Deployment, while the per-publish job only runs a `buildctl` client that ships the build context over `tcp://sas-buildkitd:1234`
- The Build Kit page no longer documents mounting a Hugging Face token into the publish pod. Open-weight models — gated or not — get their weights from the shared volume instead, so the container build needs no Hugging Face credentials

## [1.1.0] - 2026-07-20

To pick up the Prompt Builder option-control update, re-upload the prebuilt `LLM-Prompt-Builder/dist/index.html` to your SAS Job Execution definition. The transfer package `SAS-Viya-Integrations/SAS-Agentic-AI-Accelerator-Prompt-Builder.json` has been re-exported with the same update for new deployments.

The new **Model Definition Builder** (`Model-Definition-Builder/`) takes the chore out of creating and maintaining LLM and Embedding model definitions. A single `definition.yaml` per model becomes the source of truth and the `mdb` CLI generates every framework asset from it (score script, inputVar/outputVar, modelConfiguration, options, requirements, model card, README and the fact-sheet row). The entire definition fleet has been migrated onto manifests and regenerated (see the Changed section — deployed SCR containers keep running their old score code until re-published), and everything continues to work with the established register/publish scripts.

### Added

- Model Definition Builder CLI (`mdb`, package `sas-mdb`) with the commands `add` (interactive wizard and non-interactive flags), `generate` (incl. `--check` drift gate), `validate` (incl. `--live` provider smoke test), `sync` (fact-sheet upsert), `import` (reverse-engineer an existing definition folder into a manifest) and `test` (local scoreModel invocation)
- `definition-core/`: versioned manifest JSON Schema with a typed option vocabulary, score-script templates per provider family, the shared static assets (inputVar/outputVar, modelConfiguration boilerplate, tag taxonomy) and static provider catalog fallback tables for offline use
- Provider adapters: OpenAI-compatible (OpenAI, OpenRouter, Azure AI Foundry v1, Mistral), Anthropic, AWS Bedrock (Converse API with a Bedrock API key by default or a boto3/SigV4 variant), Google Gemini, Voyage AI and self-hosted Hugging Face (`transformers` and `sentence-transformers`); third-party adapters can be added via Python entry points
- Self-hosted OpenAI-compatible providers **Ollama** and **vLLM** (`mdb add ollama` / `mdb add vllm`), each serving both chat (LLM) and embedding models. Definitions are environment-neutral: the server base URL resolves at scoring time via a per-call option, the `OLLAMA_BASE_URL` / `VLLM_BASE_URL` container environment variable, or a localhost default baked in, and an optional bearer token is read from `OLLAMA_API_KEY` / `VLLM_API_KEY` — so one published image can point at any inference server without a rebuild. The model weights stay on the server, so the container only needs the thin api-wrapper requirements. Options include `top_k` (for vLLM sampling), and a `--license` flag records the served model's license instead of assuming Open-Source
- Embedding definitions are fully supported: generated embedding scorers return all three declared outputs and embed the complete vector (fixing two long-standing bugs in several hand-written definitions), and `embedding_fact_sheet.csv` rows are kept in sync
- Open-source CPU embedding definitions informed by the RTEB retrieval leaderboard: IBM `granite_embedding_small_r2` (47M parameters, 384-dim) and `granite_embedding_r2` (149M, 768-dim) — Apache-2.0 ModernBERT bi-encoders with an 8192-token context — join the existing `all_minilm_l6_v2`, the BGE family and `embedding_gemma_300m`. The generated `sentence-transformers` scorer was verified end-to-end on CPU (384-dim vector, correct per-mode token counts, distinct query/document embeddings)
- Environment-neutral definitions: Azure resources and AWS Bedrock regions resolve at scoring time via per-call options or the `AZURE_OPENAI_RESOURCE` / `AWS_BEDROCK_REGION` container environment variables, so one published image serves multiple subscriptions/projects/regions
- Typed scoring options: the option vocabulary covers `reasoning_effort`, `thinking_budget`, `max_completion_tokens`, `seed`, penalties, and the embedding options `input_type`, `dimensions` and `normalize`; `options.json` carries additive `type`/`values` fields for non-numeric options so UIs can render proper controls (numeric options keep the established shape)
- Generated definitions as working examples: `claude_sonnet_4_5`, `gpt_41_mini`, `gpt_5_mini` (reasoning model, live-verified), `claude_haiku_4_5_bedrock` and `titan_embed_text_v2`
- Documentation: Administration Guide page "Model Definition Builder" and a README in `Model-Definition-Builder/`
- CI safeguard `verify-model-definitions.yml`: re-renders every managed definition on pull requests and fails when committed assets drift from their manifest
- The Prompt Builder now renders a control for **every** option of an LLM instead of a fixed set: typed options from the Model Definition Builder appear as segmented selectors (`enum` with up to five values, e.g. the normalized Reasoning Effort scale), checkboxes (`bool`) or text inputs (`string`), unknown numeric options as number inputs — previously options like `reasoning_effort` or an Azure resource override were silently ignored and the score-code defaults applied
- Custom (non-vocabulary) options are supported with an inline `type` in `definition.yaml`: they pass through to the provider under their own name and `mdb generate`/`mdb validate` warn (V010) that they render with their raw name and get no standardized label or cross-provider translation
- Deprecation radar: `mdb radar [--probe]` checks every managed model against its provider's live surface (a 1-token probe is the ground truth — retired models can stay listed, as both Gemini 2.5 and `text-embedding-ada-002` demonstrated); `mdb retire` tags a definition deprecated and regenerates it
- `mdb deploy` renders ready-to-apply SCR Kubernetes YAML with every placeholder of the `SCR-LLM-Deployment-YAML` templates filled, auto-selecting the persistent-volume variant for self-hosted models; `Model-Definition-Builder/ci-recipes/` ships thin GitHub Actions pipelines (model lifecycle + weekly radar) where each step is one mdb verb
- New template families: `hf_onnx` (onnxruntime-genai self-hosted LLMs, `--runtime onnx`) and `emb_bedrock_cohere` (Cohere Embed via Bedrock, with the normalized `input_type` translated to `search_document`/`search_query`)
- Third-party adapter kit: `mdb provider scaffold <name>` generates an entry-point-wired pip package skeleton and `mdb provider check <id>` runs the conformance suite against any installed adapter
- Fleet curation backed by radar evidence: 13 provider-retired definitions removed (Claude 2.x/3.x, the Gemini 1.5 family, Gemini 2.5 Pro/Flash-Lite, `text-embedding-ada-002`); `gemini-2.5-flash` verified still serving and kept — every removed definition remains in git history and is a quick `mdb add` away if a provider revives it
- Viya lifecycle verbs (`pip install sas-mdb[viya]`): `mdb register [--update]` creates or replaces a registered model in place (new minor version, content replacement via `contents?onConflict=update`, refreshed attributes and tags — no more delete-and-re-register), `mdb publish --wait` polls the SCR image build to completion, `mdb ship` chains validate/register/publish, and `mdb endpoints` emits the SCR endpoint manifest; one implementation covers LLM and Embedding models, and every registered model stores its `definition.yaml` as model content
- `mdb setup` creates the SAS Model Manager repository (`LLM Repository`) and the LLM/Embedding Model Projects if they do not exist yet (idempotent, matching `Model-Manager-Setup.py`), and `mdb register` runs the same check automatically for the kind it registers — so a fresh environment can be bootstrapped entirely from the CLI without a separate setup step. `mdb setup` also writes the authorization-group rules (`sas-viya-cli-commands.txt` — the LLM Consumers / Prompt Engineers groups and folder/repository access rules) and the `llm-prompt-builder.json` / `rag-builder.json` builder seed files, so it fully replaces `Model-Manager-Setup.py`
- `mdb unregister <model_id>` deletes a registered model from SAS Model Manager (confirmation prompt, `--yes` to skip); the local definition folder is left untouched, so it can be re-registered any time

### Changed

- **The entire definition fleet (42 legacy folders) is migrated onto `definition.yaml` manifests.** Every folder regenerates deterministically; after the curation below, the CI drift gate covers all 37 migrated definitions (39 in total, including the two new Granite embedding models above). The migration applies these normalizations uniformly: the canonical options parser (fixing the `isinstance(options, str)` bug that silently ignored user options in several scorers), provider usage-based token counting where the API reports it (Anthropic, OpenAI, Gemini), the embedding return-arity and full-vector fixes, the `requirements.json` standardization from PR #6 (pip upgrade step first, `huggingface-hub>=0.18.0`), `toolVersion: "3.11"` (fixes the publish 400 on current SAS Viya releases), and the central `inputVar.json` typo fix. Legacy score filenames are preserved (`generation.score_code_file`) so registered models keep their scoreCodeFile. Six definitions with hand-written runtimes (`llama_31_405b`, `llama_32_1b/3b`, `mistral_nemo`, `phi_3_mini_4k`, `phi_35_mini` — onnxruntime-genai, mistral-inference, pipeline and hosted-endpoint scorers) keep their scorers hand-maintained via `generation.overrides` while gaining managed metadata and the publish fix
- **Re-publish guidance:** deployed SCR containers of migrated models still run the old score code until re-published — republish with `mdb publish <id>` (or the classic scripts) when you want the fixes in production; `mdb register --update` refreshes registered model contents first
- The Azure OpenAI definition's `API_KEY` default is normalized from the `ProviderName` placeholder to `AzureOpenAI`, and the Voyage adapter uses the fleet's established `VoyageAI` KeyName
- The generator emits `toolVersion: "3.11"` — newer SAS Viya releases reject the legacy `3.11-5` format when publishing; the `_Base_Definition` templates are updated as well
- Self-hosted Hugging Face requirements are much leaner so the SCR image builds stay under the publish timeout: PyTorch installs from the CPU-only index (`--extra-index-url https://download.pytorch.org/whl/cpu`, so PyPI still resolves torch's transitive dependencies) instead of the default wheel that bundles ~2 GB of unused CUDA libraries, and the vestigial `git-lfs` install steps are removed because `hf download` uses the huggingface-hub HTTP API rather than git. Applies to the `transformers`, `sentence-transformers` and `onnx` requirements profiles (the `onnx` profile already used no torch); the six hand-maintained runtime folders keep their own requirements. Re-register (`mdb register --update`) and re-publish affected self-hosted models to pick this up

### Fixed

- **Anthropic scorers no longer send `temperature` and `top_p` in the same request.** Claude 4.5 and newer reject a request carrying both (`invalid_request_error: "temperature and top_p cannot both be specified for this model"`), which made *every* call to the generated `claude_sonnet_4_5` scorer fail with HTTP 400 — the provider smoke test missed it because it sends neither. The option vocabulary can now mark options as mutually exclusive per template family (`exclusive_group`), and the generated scorer picks one at scoring time: whichever the caller set explicitly, preferring `temperature` when both or neither are given. Extended thinking additionally drops `top_p` outright, since it pins `temperature` to 1. Verified live against `claude-sonnet-4-5-20250929` across all five paths (neither/temperature/top_p/both set, and `thinking_budget > 0`). **Re-register and re-publish `claude_sonnet_4_5` to pick this up** — the registered model and any published SCR container still carry the broken score code
- Pre-merge review hardening of the self-hosted and Viya-lifecycle additions: `mdb validate --live` now resolves the self-hosted smoke-test server from the `OLLAMA_BASE_URL` / `VLLM_BASE_URL` environment variable (it was pinned to the baked localhost default); the `--kind` and Hugging Face `--runtime` flags are validated (an unrecognized value used to silently produce an LLM/transformers definition); `sentence-transformers` is pinned `>=5.1` to match the 5.x APIs the generated scorer calls; the embedding token count derives the special-token overhead from the tokenizer instead of assuming two; the Embedding Model Project is created with `targetVariable: embedding` (was the nonexistent `response`) in both `mdb` and `Model-Manager-Setup.py`; `mdb register` / `mdb setup` no longer abort a whole batch on one bad folder or a repository-create race; `mdb register <id>` on a folder with no manifest is reported instead of silently succeeding; misdirected `mdb add` flags are surfaced; a new V011 validation flags a self-hosted definition with no base URL; and `mdb unregister` notes that an already-published SCR container is not removed
- Fact-sheet corrections adopted from PR #6 (thanks @bteleuca): the `gemini_falsh_15_002` model_id typo that broke the fact-sheet join for that model, `Propietary` → `Proprietary` (16 LLM + 8 embedding rows), `GPT-4ois` → `GPT-4o is` (3 rows), one grammar fix, and the missing `gpt_4o_mini_az_2024_07_18` row

## [1.0.0] - 2026-07-15

The standalone LLM Prompt Builder now supports deleting prompt experiment runs, prompts and whole projects. To get the update, rebuild the [`LLM-Prompt-Builder`](./LLM-Prompt-Builder) (or use the prebuilt `dist/index.html`) and re-upload it to your SAS Job Execution definition.

### Added

- Prompt experiment runs can now be deleted from the experiment tracker; the remaining runs are automatically renumbered and the change is persisted with the next "Save Experiments"
- Prompts can now be deleted; before deletion the SAS Relationships service is queried and the confirmation dialog lists every SAS Intelligent Decisioning decision that uses the prompt (with a deep link to each decision), or notes that no decisions were found
- Projects can now be deleted; every prompt in the project goes through the same decision-usage confirmation first and a single cancel aborts the whole operation without deleting anything
- Prompt variables: define variables (name, description, string/decimal data type and a value) above the prompt fields and reference them in the system or user prompt with the `{{variableName}}` syntax — right-clicking inside a prompt field opens a menu to insert them. The values are substituted when experiments run, every run stores a snapshot of its variable setup and values in the experiment tracker, and a manifested best prompt turns the referenced variables into the documented inputs of the generated Python score code
- Experiment runs can be loaded back into the workbench — a load button on every run, and selecting a prompt automatically loads its most recent run with a selected best response. Loading restores the prompts, the variables, the LLM selection and the LLM option values, and a notification lists any LLMs of the run that are no longer available
- "Include the LLM call in the manifested model" option: when checked, the manifested Python model calls the LLM container directly (via the `requests` package) and returns the same outputs as the LLM models themselves. Unchecked keeps the previous behavior of returning `llmBody` and `llmURL` for the Call LLM node in SAS Intelligent Decisioning
- With the LLM call included, the default outputs (`response`, `run_time`, `prompt_length`, `output_length`) can be individually selected, and the LLM response can be parsed into user-defined output variables (name, description, string/decimal data type and an optional default value). This expects the LLM to respond with JSON only — a fenced ```json block is unwrapped automatically — and adds a `parse_status` output that returns 1 when every output variable was extracted and 0 otherwise
- The manifest configuration (LLM call included, selected default outputs and output variable definitions) is stored with the run in the experiment tracker, so loading a run also restores it
- Manifesting with the LLM call included also stores a `requirements.json` with the model (same format and role as the LLM definitions), so publishing destinations that build a Python environment install the required `requests` package; it is removed again when a later manifest no longer includes the call
- The integrated LLM call in the generated score code verifies TLS against the CA bundle SAS Viya mounts into its pods (`/security/trustedcerts.pem`) and supports three new environment variables: `LLMCONTAINERCABUNDLE` (alternative CA bundle path), `LLMCONTAINERSSLVERIFY=false` (disable TLS verification) and `LLMCONTAINERTIMEOUT` (call timeout in seconds, default 600)
- Manifesting tags the model in SAS Model Manager with the LLM of the best prompt plus `LLM-Call-Included` and `Output-Parsing` mode tags; tags from an earlier manifest are replaced while custom tags are kept
- The project and prompt selection lists can be filtered by name and by the user who created or last modified an entry (long lists stay searchable; the active selection always remains visible)
- New reusable confirmation modal (`src/ui/confirm-modal.ts`), toast notifications (`src/ui/toast.ts`) and SAS Relationships API wrapper (`src/api/relationships-api.ts`) in the standalone Prompt Builder

### Changed

- The `variableName:variableValue;...` user-prompt syntax is replaced by the `{{variableName}}` variables described above; prompts saved with the old syntax still load and manifest through the previous parsing
- Reworked page layout: the page is grouped into five visual sections (project & prompt, LLMs, prompt workbench, experiment tracker, manifest) with a proper heading hierarchy, and manifesting is its own section with the configuration above the action button
- Saving and manifesting now confirm success via toast notifications (deletion errors report through toasts as well), "Run Experiments" stays disabled (with a hint) until at least one LLM is selected, "Manifest Best Prompt" stays disabled until a run has a selected best response, and the create-prompt explanation moved from the button label into the dialog
- "Save Experiments" and "Manifest Best Prompt" are real buttons now, so they are keyboard-accessible and properly disabled while busy
- The experiment tracker shows an empty-state hint until a prompt with runs is selected, the destructive delete buttons sit right-aligned away from the other actions, and the LLM option explanations are Bootstrap tooltips (keyboard- and touch-accessible)
- A SAS-blue accent theme (buttons, checkboxes, headings, white section cards on a light background) replaces the stock Bootstrap look, and a loading spinner is shown while the app fetches its metadata
- The LLM definitions and their options are now loaded in parallel instead of one after another, which speeds up the initial load considerably for environments with many LLMs
- The Vite dev-server proxy now also forwards `/relationships` and `/decisions` calls to the configured SAS Viya host

### Fixed

- Loading a prompt whose saved experiment tracker contains non-consecutive run numbers (runs whose experiments all failed leave gaps) no longer fails with a console error and unresponsive buttons; the runs load normally and are renumbered contiguously on the next save
- Selecting a "Best Response" checkbox now also updates the in-memory experiment tracker, so unsaved selections survive a re-render of the tracker
- Loading an existing prompt now rebuilds the saveable experiment rows from the loaded runs (previously they were rebuilt from stale state, so a "Best Response" selected right after loading could not be saved)
- Switching the project or prompt selection now fully resets the in-memory experiment state, so experiments from a previously selected prompt can no longer be saved to a different prompt
- A failed LLM call in a manifested model (network, TLS or a non-200 response) no longer raises an exception — which aborted the whole scoring or SAS Intelligent Decisioning run with a pymas execute error — but reports the failure through the `response` output while the parsed output variables keep their defaults and `parse_status` returns 0
- The integrated LLM call builds its request body with `json.dumps` now, so prompts and input values containing apostrophes, quotes, newlines or backslashes are transmitted correctly (the previous manual escaping replaced apostrophes with double quotes and produced an invalid request body for backslashes)

## [0.1.35] - 2026-07-13

The Prompt Builder is now also available as a **standalone application** in the [`LLM-Prompt-Builder`](./LLM-Prompt-Builder) directory. It has no dependency on the [SAS Portal Framework for SAS Viya](https://github.com/sassoftware/sas-portal-framework-for-sas-viya) and can be extended independently. It ships as a single self-contained HTML file that is embedded in a SAS Visual Analytics report via SAS Job Execution.

### Added

- Standalone LLM Prompt Builder (`LLM-Prompt-Builder/`): a no-code prompt-engineering UI (Vite + TypeScript) that builds to a single-file `index.html` for embedding in SAS Visual Analytics via SAS Job Execution
- Configuration is delivered through the Visual Analytics properties panel (Data-Driven Content options), while API keys are delivered through the object's assigned data — keeping secrets out of the URL and the report definition
- Multi-language UI (English and German included)
- `create-api-key-table.sas` helper to create the API-key table consumed by the object's assigned data

### Changed

- None

### Fixed

- None

## [0.1.34] - 2025-12-18

No changes are required at this time.

### Added

- None

### Changed

- As the SCR endpoint isn't currently used in the register-LLMs.py script it has been moved to optional

### Fixed

- Implement fix for no longer requiring the fact sheet entry for LLMs

## [0.1.33] - 2025-12-16

In order for these changes to take effect you need to update the source code of the [SAS Portalframework for SAS Viya](https://github.com/sassoftware/sas-portal-framework-for-sas-viya). If you have deployed the LLM containers in kubernetes no additional changes are required, if you have deployed them as an Azure Container App or Azure Container Instance please utilize the [prompt-builder-json.py](./utility/prompt-builder-json.py) utility script with the additional option -dt aca in order to enable the prompt builder for this as well.

### Added

- Ability for the prompt builder to also communicate with LLM containers deployed as Azure Container Apps or Azure Container Instances
- Prompt Templates now include a check for an environment variable called LLMCONTAINERPATH that can be set in order to provide an environment independant path to the endpoint where the LLMs are hosted.
- Documentation on how to set environment variable

### Changed

- None

### Fixed

- None

## [0.1.32] - 2025-12-07

No changes are required at this time.

### Added

- Utility script for recreating the Prompt Builder JSON

### Changed

- None

### Fixed

- None

## [0.1.31] - 2025-10-23

No changes are required at this time.

### Added

- Improve documentation

### Changed

- None

### Fixed

- None

## [0.1.30] - 2025-10-14

No changes are required at this time.

### Added

- None

### Changed

- None

### Fixed

- The verify_ssl couldn't previously be set to false for the Python scripts

## [0.1.29] - 2025-10-13

No changes are required at this time.

### Added

- None

### Changed

- The register-LLMs.py script does no longer require an entry in the LLM Fact Sheet.

### Fixed

- None

## [0.1.29] - 2025-10-10

No changes are required at this time.

### Added

- Expanded documentation pages by a lot
- Optional argument for the Model-Manager-Setup.py script to provide the ability to change to deployment: k8s or aca
- Ignore .venv added to the .gitignore

### Changed

- The Model-Manager-Setup.py script now provides bash commands, instead of PowerShell

### Fixed

- None

## [0.1.28] - 2025-09-25

No changes are required at this time.

### Added

- None

### Changed

- None

### Fixed

- Fix argument error in publish-Embedding script and tag value in register-LLMs

## [0.1.27] - 2025-09-10

No changes are required at this time.

### Added

- SECURITY.md and CONTRIBUTING.md
- Source code headers for copyright and license information added

### Changed

- None

### Fixed

- None

## [0.1.26] - 2025-09-08

No changes are required at this time.

### Added

- BGE Small, Base and Large EN v1.5
- Attribution note in the main README
- The *register-LLMs.py* now supports the additional new metadata items provided by SAS Model Manager 2025.08+

### Changed

- The *register-LLMs.py* now requires an additional parameter -e which is the same as for the *publish-LLMs.py* to know the endpoint, as this will enable further metadata integration with the SAS Model Manager

### Fixed

- Typo in the Gemma Embedding model folder path

## [0.1.25] - 2025-09-07

No changes are required at this time.

### Added

- All MiniLLM L6 v2, an open-weight embedding model
- Embedding Gemma 300M, an open-weight embedding model

### Changed

- Change model function from classification to text generation to make use of the new features within SAS Model Manager as off 2025.08

### Fixed

- None

## [0.1.24] - 2025-09-06

No changes are required at this time.

### Added

- None

### Changed

- None

### Fixed

- Fix in the _Base_Definition_ baseScore.py LLM code

## [0.1.23] - 2025-09-02

No changes are required at this time.

### Added

- Two images added as preperation for upcoming documentation enhancements

### Changed

- Update main README.md

### Fixed

- Updated two minor versions in the Changelog as they were stuck on 0.1.20

## [0.1.22] - 2025-09-01

No changes are required at this time.

### Added

- README for Tools

### Changed

- None

### Fixed

- Wrongly formatted API_KEYS in embedding models

## [0.1.21] - 2025-08-29

No changes are required at this time.

### Added

- New Tools folder and the first tool contribution for websearch

### Changed

- None

### Fixed

- None

## [0.1.20] - 2025-08-26

Run the *./SAS-Viya-Tool-Integrations/SAS-Intelligent-Decisioning-Integration/Update-Custom-SAS-Intelligent-Decisioning-Node.sas* in your environment to get the update. The script has been validated to not require any changes to your existing messages - but it has expanded the supported character limits of both the llmBody and the llmGenerated variables to the maximum length of 10,485,760 characters.

### Added

- *Update-Custom-SAS-Intelligent-Decisioning-Node.sas* is available as an update script of existing Call LLM nodes to support the increased character limit.
- In the *Troubleshooting-Guide.md* a new line that explains Duplicate Variable Error.

### Changed

- Renamed *Non-SAS-Viya-Tool-Integrations* and *SAS-Viya-Tool-Integrations* to *Non-SAS-Viya-Integrations* and *SAS-Viya-Integrations* to better reflect that these aren't tools themselves but rather integration points. The documentation has been updated accordingly.

### Fixed

- *Create-Custom-SAS-Intelligent-Decisioning-Node.sas* now provides a character limit for the *llmBody* and *llmGenerated* variables of 10,485,760 characters (the maximum supported by all publishing destinations).

## [0.1.19] - 2025-08-14

No changes are required at this time.

### Added

- *Token-Calculator.html* now has an additional input field were you can change to estimate how often a prompt is run per day

### Changed

- None

### Fixed

- None

## [0.1.18] - 2025-08-13

No changes are required at this time.

### Added

- Gemini Flash Lite 2.5, Flash 2.5 and Pro 2.5 have been added
- The *llm_facht_sheet.csv* was updated alongside the introduced models
- Implemented [Issue 30](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/30) - requires update of the Portal Framework, by switching to the aaia branch
- Tag documentation for LLM Definitions

### Changed

- Gemini Flash 1.5 001 and 002 are deprecated by Google and have received that tag accordingly
- Claude 2.0 and 2.1 used a Legacy tag, this has been changed to make use of the deprecated feature

### Fixed

- None

## [0.1.17] - 2025-08-11

The *Token-Calculator.html* and *LLM-Details-Page.html* need to be uploaded to a webserver (e.g. the one used for the Prompt Builder UI or where the customers stores Data Driven Content object sources).

### Added

- *Token-Calculator.html*, in report utility to calculate the tokens used up by a prompt and then multiplies it with the pricing data from the *llm_fact_sheet.csv*
- *LLM-Details-Page.html*, in report utility to display the information from the *llm_fact_sheet.csv* as a type of super light weight model card

### Changed

- In *Load-Fact-Sheets.sas* the default path has been updated.
- *LLM - Get All Prompts.step* has been updated to remove warning as the macro variable was spelled incorrectly.

### Fixed

- Implemented fix suggested by [Issue 29](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues?show=eyJpaWQiOiIyOSIsImZ1bGxfcGF0aCI6IkRhdmlkLldlaWsvc2FzLWxsbS11Y2YiLCJpZCI6NjE0OTh9)
- Fix typo for phi_35_mini model id in *llm_fact_sheet.csv*

## [0.1.16] - 2025-08-10

No changes are required at this time.

### Added

- Update the LLM Fact Sheet to include all current models
- Add Load-Fact-Sheets.sas programm to load the data to CAS

### Changed

- Removed tiktoken dependency for OpenAI models, as tokens are included in the response. This will improve the total processing time

### Fixed

- None

## [0.1.15] - 2025-08-09

No changes are required at this time.

### Added

- Get-All-Prompts.sas retrieves all prompting projects, models and their experiments and turns it into a table for reporting
- LLM - Get ALL Prompts custom step introduced, that does the same as the script, just wrapped in a custom step
- LLM Fact Sheet entries for all Anthropic and Google models

### Changed

- None

### Fixed

- None

## [0.1.14] - 2025-08-05

No changes are required at this time.

### Added

- register-Embedding.py to register Embedding models to SAS Model Manager
- publish-Embedding.py to publish Embedding models to SCR
- Model-Manger-Setup.py now also creates the Embedding Model Project
- .gitignore now ignores the rag-builer.json which is used for the RAG Builder UI
- README.md was updated to refelect these changes
- Added additional embedding models from Voyage.ai

### Changed

- The LLM base example was called _Base-Definitions for consistency this name was update to \_Base\_Definitions

### Fixed

- None

## [0.1.13] - 2025-08-04

This update requires you to update requires you to switch from the prompt builder provider here to https://github.com/sassoftware/sas-portal-framework-for-sas-viya

### Added

- Add Embedding Definitions

### Changed

- Removed LLM Prompt Builder content from this repository and moved it to https://github.com/sassoftware/sas-portal-framework-for-sas-viya
- Leading and Trailing blanks are now removed from the variables in the manifested prompts

### Fixed

- The name of the manifested prompt was based on the name of the prompt, this has now been fixed to adhere to proper Python package names

## [0.1.12] - 2025-07-14

This update requires you to update the ./js/objects/add-prompt-builder.js file and add the two lines at the end of the ./language/de.json and ./language/en.json files (maybe best to update the whole prompt builder section) - make sure to also empty your browser cache.

### Added

- New button that provides a link to the model, if one is selected
- A Troubleshooting-Guide.md was added

### Changed

- None

### Fixed

- None

### Removed

- The README.md chapter **Modifying the SAS Portal Framework for SAS Viya** has been removed as the Prompt Builder is now part of the main repository

## [0.1.11] - 2025-06-06

No updating required as this update is a design phase.

### Added

- Added Claude 2.0 as a first test

### Changed

- None

### Fixed

- None

### Removed

- Evals from the fact sheets - mabye an idea for the future in a different sheet


## [0.1.10] - 2025-06-05

No updating required as this update is a design phase.

### Added

- Base attributes for all default included models

### Changed

- Added two additional attributes to the llm_fact_sheet.csv model_id and deployment_type

### Fixed

- None

### Removed

- None

## [0.1.9] - 2025-06-04

No updating required as this update is a design phase.

### Added

- Start desgining the LLM fact sheet which will be the new base for further reporting

### Changed

- None

### Fixed

- None

### Removed

- None

## [0.1.8] - 2025-06-03

This update requires you to update the ./js/objects/add-prompt-builder.js file and add the two lines at the end of the ./language/de.json and ./language/en.json files (maybe best to update the whole prompt builder section) - make sure to also empty your browser cache.

### Added

- One new line in the language file to explain best prompt
- Icon is displayed next to the model name if for best response + with hover text

### Changed

- None

### Fixed

- None

### Removed

- None

## [0.1.7] - 2025-06-02

This update requires you to update the ./js/objects/add-prompt-builder.js file and add the two lines at the end of the ./language/de.json and ./language/en.json files (maybe best to update the whole prompt builder section) - make sure to also empty your browser cache.

### Added

- Two new lines in the language file to explain fastest and fewest token prompts
- Icons are displayed next to the model name if they had the fastest and/or fewest token prompts
- Icons display an hover text to explain themselves

### Changed

- None

### Fixed

- None

### Removed

- None

## [0.1.6] - 2025-06-01

This update requires you to update the ./js/objects/add-prompt-builder.js file - make sure to also empty your browser cache.

### Added

- Base implemention for fastest prompt and fewest token prompt has been added (no UI support yet)

### Changed

- [Change display order of Prompt Experiments](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/7)

### Fixed

- None

### Removed

- None

## [0.1.5] - 2025-05-31

This update requires you to update the ./js/objects/add-prompt-builder.js file - make sure to also empty your browser cache.

### Added

- None

### Changed

- LLM calls are now done in parallel, instead of in sequence - this should lead to a big performance uplift for prompt engineers
- No more leading and trailing new lines in the manifested model

### Fixed

- Added missing semi-colons
- Fix hardcoded model in the model variable deletion
- [Having to escape special characters e.g. \\n](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/3)

### Removed

- None

## [0.1.4] - 2025-05-30

This update requires you to update the ./js/objects/add-prompt-builder.js file - make sure to also empty your browser cache.

### Added

- Model Responses are now renderd as Markdown instead of plain text if the model response contains Markdown syntax.

### Changed

- None

### Fixed

- None

### Removed

- None

## [0.1.3] - 2025-05-29

### Added

- [Explain HF token](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/10)
- Documentation was added on how to add Proprietary models
- A template for gpt_4o_mini_az_2024_07_18 was added showcasing how to deploy GPT models using Azure Cognitive Services
- Add default transfer package to implement [Logging and Monitoring Assets](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/21)

### Changed

- Updated the Base-Definition options.json to be a collection of all options that are currently used across the models
- Improved the perfomance and robustness of the log parser code and custom step by moving to Python for processing
- Moved the createLLMRepository.sas script into the MM specific subfolder along with its documentation
- Moved the LLM - Log Parser.step into the Custom Step repository to be more consistent

### Fixed

- Typos in documentation

### Removed

- None

## [0.1.2] - 2025-05-28

### Added

- Two new utility functions have been added to the [main portal-framework](https://github.com/sassoftware/sas-portal-framework-for-sas-viya/blob/main/js/utility/create-model-content.js) - get-model-variables.js and delete-model-variable.js
- New utitlity function has been added to the [main portal-framework](https://github.com/sassoftware/sas-portal-framework-for-sas-viya/blob/main/js/utility/create-model-content.js) - validate-ds2-variable-name.js

### Changed

- The userPrompt variable now has a description
- When using the prompt variable functionality in the userPrompt the variable is now checked for DS2 variable name compliance

### Fixed

- [Unneccessary API_KEY in userPrompt](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/2)
- The prompt experiment tracker file was called Prompt-Example-Tracker.json - it has been renamed to Prompt-Experiment-Tracker.json
- Fixed an issue in the create-model-content.js utility function via the [main portal-framework](https://github.com/sassoftware/sas-portal-framework-for-sas-viya/blob/main/js/utility/create-model-content.js)
- [Missing comma after top_p](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/1)
- When you manifest a prompt a couple of times it would lead to the creation of duplicate variables, this has been fixed
- Fixed an issue where if only one input variable was provided it was not added
- Fixed an issue where if the user used a semi-colon for the last variable it created an empty input variable
- [Prompt Expierments stay when changing/creating projects/prompts](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/11)
- [Catch and Return API errors](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/6)
- [Ensure valid variable names](https://gitlab.sas.com/David.Weik/sas-llm-ucf/-/issues/14)

### Removed

- None

## [0.1.1] - 2025-05-27

### Added

- Added CHANGELOG.md to the repository to communicated updates better in the future
- Added rules to .gitignore to ignore the SAS Viya CLI if present in the repository
- Added rule to .gitignore to ignore the SAS Viya CLI setup commands
- Added documentation on setting up the SAS Viya CLI
- Added documentation on the SAS Viya CLI setup commands
- Added the generation of the SAS VIYA CLI setup commands to the Model-Manager-Setup.py script
- Added additional error messages to all Python scripts
- Added gpt-4o-mini-2025-01-01-preview, as an example for using Azure AI Foundry

### Changed

- None

### Fixed

- None

### Removed

- None