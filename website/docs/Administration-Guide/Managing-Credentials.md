---
sidebar_position: 11.7
---

# Managing Credentials

The accelerator can resolve every external secret it needs — LLM provider API
keys and RAG vector-store passwords — from **SAS Viya credential domains**
instead of governed tables or report-assigned data. Credentials are then
centrally administered, per-user or per-group, audited, and never stored in a
CAS table or report definition. The Prompt Builder additionally uses them to
show each user only the models they actually hold a key for.

## The naming convention

One credential domain per external secret, named `{prefix}{name}`:

- **`{prefix}`** is chosen once per deployment and configured in the app
  Options (for example `agentic-ai-`).
- **`{name}`** is the key name the accelerator already uses today:
  - for LLM providers, the `API_KEY.default` value from the model's
    `options.json` — `OpenAI`, `Anthropic`, `Google`, …
  - for RAG vector stores, the backend name — `pgvector`, `singlestore`,
    `weaviate`, `qdrant`, `opensearch`, `chroma`.

Every domain uses the standard **password** type, so both SAS Environment
Manager and the sas-viya CLI can administer it:

- the **password secret** holds the API key (LLM domains) or the database
  password (vector-store domains);
- the **user ID** field holds the database user for vector-store domains
  (for LLM API keys it is unused — set a placeholder such as `apikey`).

Connection *configuration* — host, port, database name — is not a secret and
stays in the RAG setup, never in the credential.

## Who sees which credential

A credential in a domain belongs to a **user** or a **group**:

- A **group credential** shares one key with every member (for example the
  `LLMConsumers` custom group). Members resolve it automatically.
- A **user credential** belongs to one person and **takes precedence** over
  any group credential in the same domain — a natural personal-override.
- A user with neither sees that provider greyed out in the Prompt Builder,
  with a note naming the missing domain — access to keys becomes an
  identity-management decision, not an application setting.

## Administering with the sas-viya CLI

Creating a domain requires SAS administrator rights; users may create their
own user credential in an existing domain.

```bash
# 1. One domain per provider key (admin)
./sas-viya credentials domains create --domain-id "agentic-ai-OpenAI" \
    --type password --label "OpenAI API key" \
    --description "OpenAI API key for the SAS Agentic AI Accelerator"

# 2a. Share a key with a group (admin)
./sas-viya credentials groups create --domain-id "agentic-ai-OpenAI" \
    --identity-id LLMConsumers --user apikey --password "sk-..."

# 2b. Or give one user a personal key (the user themselves, or an admin)
./sas-viya credentials users create --domain-id "agentic-ai-OpenAI" \
    --identity-id thatuser --user apikey --password "sk-..."

# RAG vector store: the user field is the DATABASE user
./sas-viya credentials domains create --domain-id "agentic-ai-pgvector" \
    --type password --label "RAG pgvector" \
    --description "Vector store credential for RAG ingestion and retrieval"
./sas-viya credentials groups create --domain-id "agentic-ai-pgvector" \
    --identity-id RAGEngineers --user rag_ingest --password "..."

# Inspect and maintain
./sas-viya credentials domains list
./sas-viya credentials domains show-info --domain-id "agentic-ai-OpenAI"
./sas-viya credentials users delete --domain-id "agentic-ai-OpenAI" --identity-id thatuser
./sas-viya credentials domains delete --domain-id "agentic-ai-OpenAI"
```

`show-info` never returns the secret part of a credential.

## Administering with SAS Environment Manager

1. Open **SAS Environment Manager** as an administrator and select
   **Security → Domains**.
2. Choose **New Domain → Authentication Domain**; enter the domain id
   following the naming convention (for example `agentic-ai-Anthropic`) and a
   description.
3. Select the domain, open its **Credentials** view and choose **New
   Credential**.
4. Enter the **Identities** (users and/or groups) that may use the
   credential, the **User ID** (`apikey` for LLM keys; the database user for
   vector stores) and the **Password** (the API key or database password).
5. Save. Members of the listed identities can resolve the secret immediately;
   everyone else keeps seeing the provider as unavailable.

## How the applications use the domains

- The **Prompt Builder** Options pane gets a *Credential domain prefix*
  setting. When set, the Builder resolves each provider key from
  `{prefix}{provider}` at load time and disables models whose domain returns
  no credential for the current user (a tooltip names the domain to request
  access to). When the prefix is empty, the existing report-assigned-data
  key table keeps working unchanged.
- The **prompt-optimization job** accepts the same prefix and resolves keys
  server-side under the identity of the user who launched the run — the
  governed key-table parameters remain supported for existing deployments.
- **RAG ingestion and retrieval** resolve `{prefix}{backend}` the same way;
  ingestion (SAS Studio / Job Execution) and retrieval (Intelligent
  Decisioning) both run under the calling user, so access to the vector
  store follows the same identity rules end to end.

## Where domains apply — and where they cannot

Resolving a credential requires a SAS Viya session: the browser (the
signed-in user) and every compute-session runtime (SAS Studio steps, Job
Execution jobs, Intelligent Decisioning test scoring) have one. A **SAS
Container Runtime (SCR) container does not** — a decision published to SCR
(or MAS) runs outside the Viya session context. For those destinations the
secrets are supplied as **container environment variables at deployment
time** (for a vector store: `RAGSTORE_USER` / `RAGSTORE_PW`), exactly like
the LLM containers receive their configuration today. The manifested
retrieval code tries the credential domain first and falls back to the
environment variables, so the same artifact runs on every destination.
