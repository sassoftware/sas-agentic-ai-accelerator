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
| `OpenAI`, `Anthropic`, `Google`, … | LLM provider API keys — the `API_KEY.default` names the LLM `options.json` files already reference |
| `pgvector_user`, `pgvector_password` | RAG vector-store credentials, prefixed with the backend name |
| `singlestore_user`, `singlestore_password`, … | further vector-store backends |

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
2. Put the entries in a plain `NAME=VALUE` file (one per line, `#` comments):

   ```text
   OpenAI=sk-...
   Anthropic=sk-ant-...
   pgvector_user=rag_ingest
   pgvector_password=...
   ```

3. Run the script per identity you want to equip:

   ```bash
   ./create-credential-domain.ps1 -IdentityType group -IdentityId LLMConsumers -KeysFile keys.env
   ./create-credential-domain.sh -t user -i myuser -k my-keys.env
   ```

The domain is created if needed and the identity's credential is **fully
replaced** with the file's entries (list every entry the identity should keep,
not only new ones). Delete the keys file afterwards. Creating the domain and
any **group** credential requires SAS administrator rights; a user can (re)run
the script for their **own** user credential in an existing domain. Secret
values are never printed.

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
- The **prompt-optimization job** accepts the same domain name (`keyDomain`,
  same default) and resolves keys server-side under the identity of the user
  who launched the run.
- **RAG ingestion and retrieval** read the `{backend}_user` /
  `{backend}_password` entries the same way; ingestion (SAS Studio / Job
  Execution) and retrieval (Intelligent Decisioning) both run under the
  calling user, so access to the vector store follows the same identity
  rules end to end.

## Where the domain applies — and where it cannot

Resolving a credential requires a SAS Viya session: the browser (the
signed-in user) and every compute-session runtime (SAS Studio steps, Job
Execution jobs, Intelligent Decisioning test scoring) have one. A **SAS
Container Runtime (SCR) container does not** — a decision published to SCR
(or MAS) runs outside the Viya session context. For those destinations the
secrets are supplied as **container environment variables at deployment
time** (for a vector store: `RAGSTORE_USER` / `RAGSTORE_PW`; for the
embedding call: the `RAGEMBED_*` variables), exactly like the LLM containers
receive their configuration today. The manifested retrieval code tries the
credential domain first and falls back to the environment variables, so the
same artifact runs on every destination. The LLM containers themselves are
unaffected either way: they receive the API key per call via the options
payload, resolved by the caller.
