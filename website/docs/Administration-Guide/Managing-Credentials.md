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
  at once — and nobody else. **The credential has to go to a group the people
  who open the Prompt Builder are members of.** A credential for a group they
  are not in is written without an error and `mdb credentials-report` shows
  the group holding it, and those users still find the models of the provider
  disabled in the Prompt Builder, with this note:

  `No <provider> key available in the <domain> credential domain - ask your administrator for access.`

  The examples on this page use `LLMConsumers` for those people: it is the
  group for everybody who needs access to the LLM repository, and `mdb setup`
  writes the commands that create it into `sas-viya-cli-commands.txt` (see
  [Setup SAS Model Manager](./Setup-SAS-Model-Manager.md)). A site that
  equips another group names that group instead — what counts is the
  membership, not the name.
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
mdb credentials-init                        # write a starter manifest
mdb credentials-add group LLMConsumers      # add an identity to it
mdb credentials-apply --dry-run             # see the plan
mdb credentials-apply                       # do it
mdb credentials-report                      # who holds one
mdb credentials-remove group FormerTeam     # mark a credential for removal
```

`credentials-init` reads your `.env` and writes a manifest listing the entries
it actually carries — names only, never a value — so you can see what is
available to hand out before deciding who gets it. All five commands default
to `credentials.yaml` in the working directory; `--file` (for
`credentials-init`) and `--manifest` (for the other four) put it anywhere you
keep deployment records. The manifest is the one place a rollout is managed
from: identities are added to it, narrowed in it and marked for removal in it,
and `credentials-apply` carries out what it says.

The manifest says **who** gets keys and **where those keys come from**. It
never contains a key, so unlike the `.env` it is meant to be committed and
reviewed in a diff — who may call which provider is a decision worth having a
history of. A fuller example is
[`credentials.example.yaml`](https://github.com/sassoftware/sas-agentic-ai-accelerator/tree/main/SAS-Viya-Integrations/Other/credentials.example.yaml):

```yaml
domain: agentic-ai-keys
source: ../../.env          # relative to the manifest

identities:
  - {type: group, id: LLMConsumers}
  - {type: group, id: RAGEngineers, only: [PGVECTOR_RAG_USER, PGVECTOR_RAG_PW]}
  - {type: user,  id: sas-be-sa}
  - {type: group, id: FraudAnalytics, source: ../../envs/production.env}
  - {type: group, id: FormerTeam, state: absent}
```

`LLMConsumers` stands for the people who open the Prompt Builder. Name the
group **your** users are members of: a credential for another group is
written, reported as held, and leaves them with the note *No … key available
in the … credential domain* (see
[Who resolves what](#the-model-one-domain-named-entries-per-identity-credentials)).
After a run that plans or writes a group credential, `credentials-apply`
prints one line to that effect: *A group credential reaches only the members
of that group, and a user's own credential replaces it for that user.*

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
mdb credentials-apply --identity-type group --identity LLMConsumers --dry-run
mdb credentials-report --identity <user id> --identity-type user
```

### Adding an identity: `mdb credentials-add`

`mdb credentials-add <type> <id>` adds a group or a user to the manifest, so
the file need not be edited by hand. The type is `group` or `user`:

```bash
mdb credentials-add group LLMConsumers
mdb credentials-add group RAGEngineers --only PGVECTOR_RAG_USER,PGVECTOR_RAG_PW
mdb credentials-add group FraudAnalytics --source envs/production.env
mdb credentials-add user sas-be-sa --manifest deployment/credentials.yaml
```

The command edits the manifest and makes no call to SAS Viya; the credential
is written by the next `mdb credentials-apply`, and the command prints that
as its next step (`mdb credentials-apply --manifest <file> --dry-run`).

| Option | Meaning |
| --- | --- |
| `--only` | The entries this identity gets, named the way the domain spells them. Repeat the option or separate the names with commas. |
| `--source` | This identity's own key file. The path is the one you type in the shell, relative to the working directory; it is written into the manifest relative to the manifest. |
| `--manifest`, `-m` | The manifest to edit. Default: `credentials.yaml` in the working directory. |
| `--env-file` | The `.env` a **new** manifest points at. Default: `.env` in the working directory. Not read when the manifest exists. |

What it does with the file:

- **Only that identity changes.** Comments, blank lines, the order and the
  other identities stay byte for byte as they are. A new identity is written
  in block style (`- type: group` / `id: …`) after the last one; an identity
  that is written `{type: group, id: …}` keeps that style when it is updated.
- **The placeholder is replaced.** A manifest from `credentials-init` names
  the identity `CHANGE-ME-to-a-real-group`; the first identity that is added
  takes its place.
- **No manifest yet:** it is written the way `credentials-init` writes it,
  from the `.env`, with the identity in place of the placeholder. Without a
  `.env` nothing is written.
- **The identity is already listed:** no second entry is written, and the
  command says so and ends with exit code 0. With `--only` or `--source` the
  entry that is there is updated and the change is printed. An identity is
  the same one when type and id are equal, the id letter for letter.
- **A name the source does not carry is refused.** An `--only` name that is
  not among the entries of the source, or a `--source` file that is not
  there, ends the command with exit code 2 and the message
  `credentials-apply` gives for it, and nothing is written.

The edit is made in the text of the file, because the YAML library `mdb` uses
would drop every comment on a rewrite. Before the file is written, the result
is read back and has to be the manifest that was there plus the one change.
Two layouts are refused with a message, and the file is left alone: an
`identities` list that is not written one `- ` line per identity (for example
`identities: [{…}, {…}]`), and an identity that is listed more than once.

### Removing a credential: `state: absent`

A credential is removed through the manifest as well. An identity may carry
`state: absent`:

```yaml
identities:
  - type: group
    id: FormerTeam
    state: absent
```

`mdb credentials-remove <type> <id>` writes that line — on the identity that
is listed, or as a new entry when the manifest does not list it — under the
same rule as `credentials-add`: nothing else in the file changes. It does not
contact SAS Viya. The credential is deleted by the next
`mdb credentials-apply`:

```bash
mdb credentials-remove group FormerTeam
mdb credentials-apply --dry-run         # the plan shows the action "remove"
mdb credentials-apply                   # deletes the credential
mdb credentials-report                  # shows it as absent
```

- `credentials-apply` plans the action `remove` for such an identity. A dry
  run shows it and removes nothing. A credential that is already gone is not
  an error: the run says that there was none to remove.
- An identity with `state: absent` needs no source and no entries, so a
  credential can be removed after its key file is gone. A run that only
  removes does not create or update the domain.
- `credentials-report` shows such an identity as
  `absent (as the manifest says)` once its credential is gone, and as
  `STILL THERE (manifest: absent)` while it exists.
- `state: present` is the default and may be written. Any other value is
  refused when the manifest is read, with exit code 2, before anything is
  written or removed.
- `mdb credentials-add` on an identity that is marked absent sets it back to
  present and says so: the `state` line is taken out again, or rewritten to
  `state: present` where it is the first line of a block entry.

**Nothing is removed because a line is missing from the manifest.** The
service cannot list who holds a credential, so `mdb` cannot know what a
deleted line once said. Leave the entry in the file with `state: absent`
until the report shows the credential as absent.

The removal was run against a stand-in for the credentials service in the
test suite of `mdb`, not against a SAS Viya server.

:::note A dry run cannot show you a diff
Reading a credential returns its metadata — who wrote it, and when — and
**never its contents, not even the entry names**. So a dry run tells you an
identity will be created, replaced or removed, but not what it holds today.
That is a privacy property rather than a gap: no identity can ever see
another's keys.

For the same reason there is no way to list who holds a credential. Both
commands report on the identities you **name**, which is why passing the same
manifest you applied gives you the after-picture of that rollout — and why a
credential is removed by marking its identity `state: absent`, never by
deleting its line.
:::

## Inspecting and deleting: CLI and Environment Manager

A credential that is managed with a manifest is deleted through the manifest:
`mdb credentials-remove`, then `mdb credentials-apply` (see
[Removing a credential](#removing-a-credential-state-absent)). The standard
tools delete as well — one credential, or the whole domain, which `mdb` never
deletes — and they show the domain. Authoring the multi-entry map is the job
of the scripts and of `mdb credentials-apply`:

```bash
./sas-viya credentials domains list
./sas-viya credentials domains show-info --domain-id "agentic-ai-keys"
# show-info never returns secret values
./sas-viya credentials users delete  --domain-id "agentic-ai-keys" --identity-id thatuser
./sas-viya credentials groups delete --domain-id "agentic-ai-keys" --identity-id LLMConsumers
./sas-viya credentials domains delete --domain-id "agentic-ai-keys"
```

A credential that is deleted with these tools while a manifest still lists
its identity as present is written again by the next `mdb credentials-apply`:
the apply writes every identity the manifest does not mark `state: absent`.

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
retrieval model returned chunks in both places the same day.

The two variables as environment variables were measured for SAS Micro
Analytic Service, also on 2026-10-11: `PGVECTOR_RAG_USER` and
`PGVECTOR_RAG_PW` were put into a Kubernetes secret and set on the
containers of the deployment from it,

```bash
kubectl -n <namespace> set env deployment/sas-microanalytic-score --containers=* --from=secret/<name>
```

the pod restarted, and a call with `{}` as the `options` of the retrieval
model returned `retrieval_status` *ok* and three hits. Not measured: the same
variables for the test of SAS Intelligent Decisioning, which runs in CAS, and
for a SAS Container Runtime container.
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
