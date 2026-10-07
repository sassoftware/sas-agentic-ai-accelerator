---
sidebar_position: 7.5
title: Decision Definitions
---

The folder *Decision-Definitions* contains a definition per decision model, each packaged so that it can be deployed using the SAS Container Runtime (SCR). More on the SCR in the [SAS Documentation](https://go.documentation.sas.com/doc/en/mascrtcdc/default/mascrtag/titlepage.htm).

## What a decision model is

A decision model (a *System One* model, the category TypeSafe's **Jev** established) does not generate text. It takes a **state** - a ticket, a record, a document, as text or as a JSON object - and one or more **typed questions**, and returns a calibrated answer per question:

| Question type | Asks | Answer |
| --- | --- | --- |
| `choice` | Which one of these options? | the chosen option, a probability per option, a confidence |
| `noul` | Does this condition hold? | the probability that it does |
| `score` | Where on this ordered scale? | the probability-weighted position, a probability per level, a confidence |

Every question in one call is evaluated against the same state, so ten questions cost one state upload. A call takes around a second and, for a hosted model, a fraction of the token price of a chat completion. The model returns no reasoning: where a human needs an explanation, pair it with an LLM. It is also weak on numbers and dates - keep those in rules.

This is the shape a SAS Intelligent Decisioning branch node wants: a value to branch on and a confidence to gate it with. Typical uses are ticket and document routing, escalation and risk flags, screening an input for prompt injection before an LLM call, and reranking retrieved chunks.

## The scoring contract

Inputs: `state`, `questions` (the question map in the System One shape, as a JSON string) and `options` (`{key:value,...}`; `API_KEY` for a hosted model).

Outputs: `answers` (the whole answer map as JSON), `answer` and `confidence` (the **first** question's headline answer - the chosen option, the score, or `yes`/`no` for a noul), `run_time`, `prompt_length` and `output_length` (always 0).

A single-question call therefore needs no parsing in the decision flow: branch on `answer`, and route the case to a person or an LLM when `confidence` is below the threshold you validated on your own labelled cases. Several questions come back together in `answers`.

```json
{"inputs": [
  {"name": "state", "value": "Hi, I was charged twice for my March invoice. Please refund one payment."},
  {"name": "questions", "value": "{\"team\": {\"type\": \"choice\", \"instructions\": \"Which team handles this?\", \"criteria\": {\"billing\": \"payments and refunds\", \"technical\": \"bugs and outages\", \"sales\": \"pricing and upgrades\"}}}"},
  {"name": "options", "value": "{API_KEY:sk-...}"}
]}
```

## The shipped definitions

| Definition | Model | Where it runs | Key |
| --- | --- | --- | --- |
| `jev_1_13` | Jev 1.13 by TypeSafe | OpenRouter (`typesafe/jev-1.13`) | the `OpenRouter` entry of the credential domain |
| `clef_flash` | Clef-flash by Cloudflare (Apache-2.0, open weights) - the most accurate measured on many-option intent sets | OpenRouter (`cloudflare/clef-flash`) | the `OpenRouter` entry |
| `liquid_d1` | Liquid D1 by Liquid AI - the cheapest hosted decision model measured | OpenRouter (`liquid/d1`) | the `OpenRouter` entry |
| `pplx_decider_27b` | Perplexity Decider 27B - a 256k context | OpenRouter (`perplexity/pplx-decider-v1-27b`) | the `OpenRouter` entry |
| `von_1_2` | Von 1.2 (Apache-2.0, ModernBERT-large) | inside the container, checkpoint staged on the shared `llm-weights` volume | none |

Each model card carries an **Evaluation** section with what was measured for the model - accuracy on hand-written cases, public datasets and the JevBench tasks, calibration error, latency, cost per 1,000 calls and one caution - from a benchmark of about 50 examples per dataset: a shortlist to start from, then measure on your own labelled cases. A definition's `metadata.evaluation` is that paragraph; leave it out and the card has no such section.

Two more fields of a decision manifest say what the model accepts: `metadata.max_options` (the most options a choice question may carry; 255 unless the model takes less - Tev1 24, Solar Decide 26) and `metadata.question_types` (the subset of `choice`, `noul`, `score` it answers; Tev1 answers choices only). Both default to everything, land in the fact sheet as `max_options` and `question_types`, and are registered as the custom properties `contextLength`, `maxOptions` and `questionTypes` of the Model Manager model, which the Prompt Builder reads to draw its model cards and to grey out a model that cannot take the current template.

Every provider in this family speaks the same wire format (`POST .../v1/systemone`), so the one `dec_systemone_api` score template also covers TypeSafe's own API (`mdb add typesafe jev-latest`, key entry `TypeSafe` from `TYPESAFE_API_KEY`) and any self-hosted server that copies it. Von's `dec_von` template loads the checkpoint from disk at start-up; the container never contacts the Hugging Face Hub at run time, and inference runs on the CPU. The checkpoint is 3 GB - too large for the image build - so `von_1_2` ships with `weights_source: mounted`: create the `llm-weights` volume and run the staging job once as described in [Serving Open-Weight Models](./Serving-Open-Weight-Models.md), and `mdb deploy` renders the persistent-volume variant for it.

## Register and publish

Decision models are registered and published with the same **`mdb`** command line as the LLMs - see [Register & Publish LLMs](./Register-&-Publish-LLMs.md) for `register`, `publish`, `--update`, `--wait` and `ship`. They land in the *Decision Model Project* of the LLM repository, which `mdb register` (or `mdb setup`) creates when it is missing.

```bash
mdb register jev_1_13 von_1_2
mdb publish jev_1_13 von_1_2 --wait
mdb deploy jev_1_13 von_1_2 --registry <your-registry>   # the SCR deployment YAML, then kubectl apply
```

`mdb add openrouter <model> --kind decision`, `mdb add typesafe jev-latest` and `mdb add von` create new decision definitions; OpenRouter's public model listing omits its decision models, so the bundled catalog snapshot supplies them (Jev, Clef and Clef-flash, Liquid D1, Perplexity Decider, Kev, Tev1, Solar Decide). `mdb test <id>` runs the generated scorer on a support ticket with all three question types, and `mdb load-facts` - or the `Load-Fact-Sheets.sas` program - loads the `DECISION_FACT_SHEET` table beside the LLM and embedding fact sheets.

In the Prompt Builder a **decision template** (chosen when a prompt-test is created) lists the models of the Decision Model Project, replaces the two prompts with a state template and a question designer, checks runs against expected answers, evaluates them over labelled cases and manifests typed outputs - see [Decision templates](../User-Guide/Prompt-Builder.md#decision-templates) in the User Guide. The project is found by its name, so no report option is needed.
