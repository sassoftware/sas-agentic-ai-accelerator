# Decision Definitions

This folder contains information on how to add decision models to the repository in the SAS Model Manager. Each model is packaged so that it can be deployed using the SAS Container Runtime (SCR).

More on the SCR in the [SAS Documentation](https://go.documentation.sas.com/doc/en/mascrtcdc/default/mascrtag/titlepage.htm).

A decision model (a *System One* model, after TypeSafe's Jev) does not generate text. It takes a **state** - a ticket, a record, a document - and one or more **typed questions**, and returns a calibrated answer per question:

| Question type | Asks | Answer |
| --- | --- | --- |
| `choice` | Which one of these options? | the chosen option, a probability per option, a confidence |
| `noul` | Does this condition hold? | the probability that it does |
| `score` | Where on this ordered scale? | the probability-weighted position, a probability per level, a confidence |

That is the shape a SAS Intelligent Decisioning branch node wants: a value to branch on and a confidence to gate it with. Every provider in this family speaks the same wire format (`POST .../v1/systemone`), so one score template covers TypeSafe's own API, OpenRouter (which hosts Jev) and self-hosted reimplementations; Von additionally runs inside the container with its checkpoint baked into the image.

Each subfolder here contains the definition for one decision model - the name of the folder is the model id. A folder is generated from its `definition.yaml` by the [Model Definition Builder](../Model-Definition-Builder/README.md); the other files are derived from it and are not edited by hand.

## The scoring contract

Inputs: `state` (text, or a JSON object or array), `questions` (the question map, in the System One shape, as a JSON string) and `options` (the usual `{key:value,...}` string; `API_KEY` for a hosted model).

Outputs: `answers` (the whole answer map as JSON), `answer` and `confidence` (the **first** question's headline answer - the chosen option, the score, or `yes`/`no` for a noul - so a single-question call needs no parsing downstream), `run_time`, `prompt_length` and `output_length` (always 0: nothing is generated).

A call through the SCR endpoint, the same envelope every other container of the framework uses:

```json
{"inputs": [
  {"name": "state", "value": "Hi, I was charged twice for my March invoice. Please refund one payment."},
  {"name": "questions", "value": "{\"team\": {\"type\": \"choice\", \"instructions\": \"Which team handles this?\", \"criteria\": {\"billing\": \"payments and refunds\", \"technical\": \"bugs and outages\", \"sales\": \"pricing and upgrades\"}}, \"escalate\": {\"type\": \"noul\", \"instructions\": \"The customer threatens to leave\"}}"},
  {"name": "options", "value": "{API_KEY:sk-...}"}
]}
```

## Adding a new decision model

```bash
mdb add openrouter typesafe/jev-1.13 --kind decision --id jev_1_13 --yes   # Jev through OpenRouter
mdb add openrouter cloudflare/clef-flash --kind decision --id clef_flash --yes   # any other decision model OpenRouter hosts
mdb add typesafe jev-latest --id jev_latest --yes                           # Jev through TypeSafe's own API
mdb add von --id von_1_2 --yes                                              # Von, self-hosted in the container
```

OpenRouter's public model listing omits its decision models, so they come from the bundled catalog snapshot (Jev, Clef and Clef-flash, Liquid D1, Perplexity Decider, Kev, Tev1, Solar Decide); `--kind decision` names the contract. After adding, `mdb validate <id> --live` makes one real call, `mdb test <id>` runs the generated scorer on a support ticket with all three question types, and `mdb register` puts the model into the *Decision Model Project* of the LLM repository.

## The shipped definitions

| Definition | Model | Where it runs | Key |
| --- | --- | --- | --- |
| `jev_1_13` | Jev 1.13 by TypeSafe | OpenRouter | the `OpenRouter` entry of the credential domain |
| `clef_flash` | Clef-flash by Cloudflare (Apache-2.0) - the most accurate measured on many-option intent sets | OpenRouter | the `OpenRouter` entry |
| `liquid_d1` | Liquid D1 by Liquid AI - the cheapest hosted decision model measured | OpenRouter | the `OpenRouter` entry |
| `pplx_decider_27b` | Perplexity Decider 27B - a 256k context | OpenRouter | the `OpenRouter` entry |
| `von_1_2` | Von 1.2 (Apache-2.0, ModernBERT-large) | inside the container | none |

Every model card has an **Evaluation** section with what was measured for the model (accuracy, calibration error, latency, cost per 1,000 calls, one caution) - a shortlist, not a ranking; measure on your own labelled cases before you set a threshold. Two manifest fields say what a model accepts: `metadata.max_options` (255 unless the model takes less) and `metadata.question_types` (the subset of `choice`, `noul`, `score` it answers). They land in the fact sheet and, at registration, as the custom properties `contextLength`, `maxOptions` and `questionTypes` of the Model Manager model, which the Prompt Builder reads for its model cards and its fit check. `mdb load-facts` and `Load-Fact-Sheets.sas` both load the sheet as `DECISION_FACT_SHEET`.

## Von

`von_1_2` runs [Von 1.2](https://huggingface.co/wfzyx/von) (Apache-2.0, a ModernBERT-large encoder with an option-marker head, 395M parameters) inside the container. Its checkpoint (3 GB) is staged once on the shared `llm-weights` volume (`weights_source: mounted`, see the Administration Guide page *Serving Open-Weight Models*) and loaded from disk at start-up; the container never contacts the Hugging Face Hub at run time. The `von-sdk` package declares Python 3.12 while the SCR image ships 3.11; its code runs there, so the build lifts the floor with `--ignore-requires-python`. Inference runs on the CPU (about a second for three questions on one core); `VON_DEVICE` selects another device where the node offers one.
