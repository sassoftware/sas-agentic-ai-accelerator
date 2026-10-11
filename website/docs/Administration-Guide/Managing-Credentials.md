---
sidebar_position: 11.7
---

# Managing Credentials

The accelerator can resolve every external secret it needs — LLM provider API
keys and RAG vector-store passwords — from a **SAS Viya credential domain**
instead of governed tables or report-assigned data. Credentials are then
centrally administered, per-user or per-group, and never stored in a CAS
table or report definition. The Prompt Builder additionally uses this to show
each user only the models they actually hold a key for.

## The model: one domain, named entries, per-identity credentials

One credential **domain** (for example `agentic-ai-keys`) serves the whole
deployment — its name is the single value configured in the app Options. A
**credential** in the domain belongs to a **user or group** and carries a map
of named secrets:

| Entry name | Holds |
| --- | --- |
| `OpenAI`, `Anthropic`, `Google`, `AzureOpenAI`, … | LLM provider API keys — named after the `KeyName` each model definition's `API_KEY` option references (`key_name` in `definition.yaml`), because the Prompt Builder, its RAG Builder part included, looks a model's key up under exactly that name |
| `PGVECTOR_RAG_USER`, `PGVECTOR_RAG_PW` | RAG vector-store credentials — the prefix names the vector DB backend |
| `SINGLESTORE_RAG_USER`, `SINGLESTORE_RAG_PW`, … | further vector-store backends; one domain serves several stores side by side |

Who resolves what:

- A **group credential** (for example for `LLMConsumers`) equips every member
  at once.
- A **user credential** belongs to one person and **overrides** the group
  credential entirely — a personal set of keys.
- A user with neither, or with a map that lacks a specific entry, sees the
  affected models greyed out in the Prompt Builder with a note naming the
  missing entry and the domain — access to keys is an identity decision, not
  an application setting.

Connection *configuration* — vector-store host, port, database name — is not
a secret and stays in the RAG setup, never in the credential.

## Creating and updating: the CLI workflow

Multi-entry credentials are authored with the shipped scripts
[`create-credential-domain.ps1`](https://github.com/sassoftware/sas-agentic-ai-accelerator/tree/main/SAS-Viya-Integrations/Other/create-credential-domain.ps1)
(Windows) /
[`create-credential-domain.sh`](https://github.com/sassoftware/sas-agentic-ai-accelerator/tree/main/SAS-Viya-Integrations/Other/create-credential-domain.sh)
(Linux/macOS), which authenticate through your **sas-viya CLI session**:

1. Sign in once: `./sas-viya auth login`.
2. Keep your secrets where they already live: the accelerator's git-ignored
   **`.env` file**. The script reads it directly — no second secrets file to
   author or clean up. Provider key variables map onto their provider entry
   names, and every `<BACKEND>_RAG_USER` / `<BACKEND>_RAG_PW` pair is carried
   over verbatim (uppercased); everything else in the `.env` is ignored:

   | `.env` variable | Domain entry |
   | --- | --- |
   | `OPENAI_API_KEY` | `OpenAI` |
   | `ANTHROPIC_API_KEY` | `Anthropic` |
   | `GEMINI_API_KEY` | `Google` |
   | `OPENROUTER_API_KEY` | `OpenRouter` |
   | `AZURE_OPENAI_API_KEY` | `AzureOpenAI` |
   | `MISTRAL_API_KEY` | `Mistral` |
   | `VOYAGE_API_KEY` | `VoyageAI` |
   | `HUGGINGFACE_API_KEY` | `HuggingFace` |
   | `AWS_BEDROCK_API_KEY` | `AWSBedrock` |
   | `PGVECTOR_RAG_USER`, `PGVECTOR_RAG_PW`, `SINGLESTORE_RAG_USER`, … | same names |

3. Run the script per identity you want to equip:

   ```bash
   ./create-credential-domain.ps1 -IdentityType group -IdentityId LLMConsumers
   ./create-credential-domain.sh -t user -i myuser
   ```

   By default the repository's own `.env` is used. Point at a different file
   to manage **multiple environments** from separate `.env` files:

   ```bash
   ./create-credential-domain.ps1 -IdentityType user -IdentityId myuser -EnvFile C:\envs\prod.env
   ./create-credential-domain.sh -t user -i myuser -e /envs/prod.env
   ```

   For full control (custom entry names, no mapping), pass a raw `NAME=VALUE`
   file instead with `-KeysFile my-keys.env` / `-k my-keys.env` — its entries
   are stored verbatim.

The domain is created if needed and the identity's credential is **fully
replaced** with the recognized entries (the source file must list every entry
the identity should keep, not only new ones). Creating the domain and any
**group** credential requires SAS administrator rights; a user can (re)run
the script for their **own** user credential in an existing domain. Secret
values are never printed.

## Equipping many identities at once

The scripts above equip **one** identity per run, which is right for a demo
and wrong for a rollout. `mdb` does a whole deployment from a manifest, and
reports what it did:

```bash
mdb credentials-init                    # write a starter manifest
mdb credentials-apply --dry-run         # see the plan
mdb credentials-apply                   # do it
mdb credentials-report                  # who holds one
```

`credentials-init` reads your `.env` and writes a manifest listing the entries
it actually carries — names only, never a value — so you can see what is
available to hand out before deciding who gets it. All four commands default
to `credentials.yaml` in the working directory; `--file` and `--manifest` put
it anywhere you keep deployment records.

The manifest says **who** gets keys and **where those keys come from**. It
never contains a key, so unlike the `.env` it is meant to be committed and
reviewed in a diff — who may call which provider is a decision worth having a
history of. A fuller example is
[`credentials.example.yaml`](https://github.com/sassoftware/sas-agentic-ai-accelerator/tree/main/SAS-Viya-Integrations/Other/credentials.example.yaml):

```yaml
domain: agentic-ai-keys
source: ../../.env          # relative to the manifest

identities:
  - {type: group, id: PromptEngineers}
  - {type: group, id: RAGEngineers, only: [PGVECTOR_RAG_USER, PGVECTOR_RAG_PW]}
  - {type: user,  id: sas-be-sa}
  - {type: group, id: FraudAnalytics, source: ../../envs/production.env}
```

Paths inside a manifest resolve against **the manifest's own directory**, not
your working directory, so a manifest and the `.env` files it names can be
kept together and moved together. `credentials-init` writes the source path
relative when the two are near each other and absolute when they are not — a
`../../../../../..` chain is correct and unreadable, and breaks the moment
either end moves.

`only` narrows an identity to some of the entries, named the way the **domain**
spells them (`OpenAI`, `PGVECTOR_RAG_PW`) rather than the way the `.env` does
(`OPENAI_API_KEY`); a name the source does not carry is refused rather than
quietly dropped. A single identity needs no manifest at all:

```bash
mdb credentials-apply --identity-type group --identity PromptEngineers --dry-run
mdb credentials-report --identity gerdaw --identity-type user
```

:::note A dry run cannot show you a diff
Reading a credential returns its metadata — who wrote it, and when — and
**never its contents, not even the entry names**. So a dry run tells you an
identity will be created or replaced, but not what it holds today. That is a
privacy property rather than a gap: no identity can ever see another's keys.

For the same reason there is no way to list who holds a credential. Both
commands report on the identities you **name**, which is why passing the same
manifest you applied gives you the after-picture of that rollout.
:::

## Inspecting and deleting: CLI and Environment Manager

Day-2 administration works with the standard tools (they can list and delete,
but authoring the multi-entry map itself is the script's job):

```bash
./sas-viya credentials domains list
./sas-viya credentials domains show-info --domain-id "agentic-ai-keys"
# show-info never returns secret values
./sas-viya credentials users delete  --domain-id "agentic-ai-keys" --identity-id thatuser
./sas-viya credentials groups delete --domain-id "agentic-ai-keys" --identity-id LLMConsumers
./sas-viya credentials domains delete --domain-id "agentic-ai-keys"
```

In **SAS Environment Manager** (as an administrator), **Security → Domains**
lists the domain and its credentials per identity, and supports deleting
them.

## How the applications use the domain

- The **Prompt Builder** Options pane gets a *Credential domain* setting,
  **defaulting to `agentic-ai-keys`** — the same default the admin scripts
  use, so a deployment that runs a script needs no configuration at all. The
  Builder fetches the signed-in user's secrets map once at load time and
  disables models whose provider entry is missing (a note names the entry
  and domain). The domain is the only key source; enter `none` to skip the
  lookup in deployments that use exclusively key-less self-hosted models.
  The setting is **one option for every kind** the Prompt Builder offers:
  prompt templates, decision templates and RAG setups read the same domain.
  `none` works for the two template kinds only. A RAG setup has no key-less
  mode — the vector store's user, password and location come from the
  domain — so with `none` the RAG setup card is shown disabled with that
  reason, and nothing RAG is requested.
- The **prompt-optimization job** accepts the same domain name (`keyDomain`,
  same default) and resolves keys server-side under the identity its compute
  session runs as. **That is only the launching user when the compute context
  runs its servers as the requesting user** — see the service-account caveat
  below.
- **RAG ingestion and retrieval** read the `<BACKEND>_RAG_USER` /
  `<BACKEND>_RAG_PW` entries the same way — the backend prefix (`PGVECTOR_`,
  `SINGLESTORE_`, …) lets one domain hold credentials for several vector
  stores at once. Ingestion (SAS Studio / Job Execution) runs under the
  calling user, so access to the vector store follows the same identity
  rules end to end.

## Where the domain applies — and where it cannot

Resolving a credential requires a SAS Viya session: the browser (the
signed-in user) and compute-session runtimes (SAS Studio steps, Job
Execution jobs) have one. A **SAS Container Runtime (SCR) container does
not**, and neither do the **MAS and CAS scoring runtimes** that published
decisions and Intelligent Decisioning tests execute in. For those
destinations the secrets are supplied as **environment variables at
deployment time**, using the same names as the `.env` and the domain (for a
vector store: `<BACKEND>_RAG_USER` / `<BACKEND>_RAG_PW`, e.g.
`PGVECTOR_RAG_USER`; connection config: the `RAGSTORE_*` variables; the
embedding call: the `RAGEMBED_*` variables), exactly like the LLM containers
receive their configuration today. The manifested retrieval code takes the
user and the password of the vector store from the `options` input of the
call first, then from the environment variables, and asks the credential
domain only for what is still missing after both, so the same artifact runs
on every destination. Where the two variables are set, they are used and the
domain is not asked. The LLM containers themselves are
unaffected either way: they receive the API key per call via the options
payload, resolved by the caller.

Without the two variables of the vector store the retrieval model does not
fail the decision: it answers `retrieval failed: RuntimeError: no
vector-store credentials resolved: …` in `retrieval_status` and returns no
chunk. That is what it answered on 2026-10-11 in a test of SAS Intelligent
Decisioning and in the publishing validation on a SAS Micro Analytic
Service destination, where neither runtime had them. With the user and the
password passed per call instead, in the `options` input of the model, the
retrieval model returned chunks in both places the same day. With the two
variables set on the destination it has not been measured.
[The retrieval model in a decision](./RAG-Ingestion-and-Retrieval.md#the-retrieval-model-in-a-decision)
lists every variable the model reads and where each destination takes
them.

**Service-account compute contexts.** A compute context can be configured to
run its servers under a **shared service account** instead of the requesting
user (the stock *SAS Job Execution compute context* often is). A job running
there resolves the domain as **that service account** — not as the person who
clicked *Optimize* — so a personal credential is never found, and the job's
error names the identity it actually resolved as. Two remedies:

- Grant the credential to the **service account's identity or one of its
  groups** with the admin script. Be deliberate: every job that runs under
  that account can then use those keys.
- Or prepare a **dedicated compute context that runs as the requesting
  user** and point the app's compute-context Option at it — keys then follow
  the per-user/group rules above. The Prompt Builder has two such options,
  one per job: **Optimization: compute context** (`computeContext`) for the
  prompt-optimization job and **RAG: ingestion compute context**
  (`ragComputeContext`) for the RAG ingestion and the retrieval test.
