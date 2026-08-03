/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Types for the RAG Builder (design §14): a Prompt Builder-style DDC object
 * that authors RAG setups as governed Model Manager artifacts. The UI never
 * sees vector-store credentials and never runs ingestion itself — it
 * configures, saves, and (in later phases) launches jobs and reads trackers.
 */

/** Interface text for the RAG Builder (resolved from the locale files). */
export interface RagBuilderText {
  [key: string]: string;
}

/** Configuration of the RAG Builder instance (Options pane / URL params). */
export interface RagBuilderConfig {
  /** Short identifier used in DOM ids. */
  id: string;
  /** Display name. */
  name: string;
  /** SAS Model Manager repository that new RAG projects are created in. */
  modelRepositoryID: string;
  /**
   * SAS Model Manager project holding the REGISTERED embedding models.
   *
   * The Builder lists this project rather than asking for a model name,
   * because only a registered model has a container behind it: a typed name
   * that is not deployed fails at the first embed call, after the crawl and
   * the chunking have already run.
   */
  embeddingProjectID: string;
  /** SAS Container Runtime endpoint hosting the embedding containers. */
  SCREndpoint: string;
  /** 'k8s' (default) or 'aca' (Azure Container Apps). */
  deploymentType: string;
  /** Credential domain holding <BACKEND>_RAG_USER/_RAG_PW entries. */
  credentialDomain: string;
  /** SAS Content root of the deployed RAG runtime (rag_core, jobs, models). */
  contentRoot: string;
  /** CAS server name used when building table references. */
  casServer: string;
  /**
   * Whether the deployment offers a given backend is carried as one
   * `enable_<key>` entry per backend (see rag-backends.ts), reached through
   * the index signature below. Independent of credentials: this is what the
   * DEPLOYMENT offers, the credential domain decides what a given USER can
   * reach.
   */
  /**
   * TLS to the vector store. Admin-set, not offered per setup: the six
   * PostgreSQL sslmode values are a Postgres concept, and offering them for a
   * store that only knows encrypted-or-not shows the user a setting that does
   * not mean what it says.
   */
  storeSslmode: string;
  /** A document that vanished from the source: 'retire' (keep as history)
   * or 'purge' (remove its chunks for good). */
  deletedPolicy: string;
  /** Drop retired chunk generations older than this many days; 0 = keep. */
  retainDays: string;
  /**
   * Whether each run is recorded in rag_runs / rag_doc_events.
   *
   * A checkbox option, so VA stores a real boolean; a URL override and any
   * report configured while this was a Yes/No dropdown arrive as strings.
   * Read it through `optionFlag`, never with `!== '0'` - that test reads an
   * unticked box as ON.
   */
  recordHistory: string | boolean;
  /** Replicas of the embedding container, so the step can size its
   * parallelism to what the deployment actually runs. */
  embedReplicas: string;
  /** '1' saves the <prefix>_ELEMENTS table to disk, '0' keeps it in memory. */
  persistElements: string;
  /** '1' saves the <prefix>_CHUNKS table to disk, '0' keeps it in memory. */
  persistChunks: string;
  /**
   * The `enable_<backend>` flags and anything else the options pane adds.
   * Checkbox options store booleans, everything else strings - so an
   * index-signature read must narrow before use (see `optionFlag`).
   */
  [key: string]: string | boolean;
}

/**
 * The RAG Setup as the UI round-trips it (`rag-setup.json` on the RAG Setup
 * model). `pipeline.yaml` is GENERATED from this on every save — the yaml is
 * the governance artifact, this JSON is the editor state. Version the shape.
 */
export interface RagSetup {
  version: 1;
  /**
   * User-authored documentation (also rendered to documentation.md and
   * written onto the model as SAS Model Manager attributes).
   *
   * The mdb model-card keys the Prompt Builder already captures per prompt -
   * a RAG setup is the same kind of governed artifact and answers the same
   * questions, so it answers them under the same names.
   *
   * The model's own DESCRIPTION is not here: it is authored in the create
   * dialog and owned by the Model Manager model, so the Builder neither
   * duplicates the field nor overwrites it on every save.
   */
  documentation: {
    modelPurpose: string;
    intendedUse: string;
    expectedBenefit: string;
    outOfScopeUseCases: string;
    limitations: string;
  };
  /**
   * SAS Content folder receiving the generated executables.
   *
   * Optional: a setup saved before this existed falls back to the
   * deployment's `<contentRoot>/generated`, which is where those artifacts
   * actually are.
   */
  artifactsFolder?: string;
  source: {
    /** Filesystem path visible from the ingestion compute context. */
    path: string;
    /**
     * Ingest source-code files (.py, .sas, .r, .js, ...) as plain text.
     *
     * Optional so a setup saved before this existed still loads; absent
     * reads as false, which is also the default for a new setup.
     */
    includeCode?: boolean;
  };
  extraction: {
    /** '' = choose by file format. */
    extractor: string;
  };
  chunking: {
    chunker: string;
    inputTokenLimit: number;
    overlapTokens: number;
  };
  /**
   * The Enrich stage: an LLM call per chunk between chunking and embedding.
   *
   * Optional so every setup saved before it existed still loads, and absent
   * reads as off — which is also the default, because the stage costs one
   * LLM call for every chunk of the corpus.
   *
   * The prompt is a manifested Prompt Builder model rather than a literal
   * here (owner decision OQ14): it is then a governed artifact with its own
   * documentation, versions and permissions, and improving it does not mean
   * editing a RAG setup.
   */
  enrich?: {
    /** The prompt project the model lives in, so the picker can reopen it. */
    promptProjectId: string;
    /** Model Manager id of the manifested prompt. '' = do not enrich. */
    promptModelId: string;
    /** Its name at the time of saving, for display when the id resolves late. */
    promptModelName: string;
    /**
     * Version to read the prompt from. '' follows the model, so re-manifesting
     * the prompt changes what the next run writes; an id pins this setup to
     * exactly the prompt that version carried.
     */
    promptVersionId?: string;
    /** Its major.minor, for display — version labels are not unique. */
    promptVersionLabel?: string;
    /** Prompt input name -> one of rag-enrich.ts's CHUNK_FIELDS keys. */
    mapping: Record<string, string>;
    /** Which output becomes the chunk's context_header. '' = none. */
    headerOutput: string;
    /**
     * Which outputs are stored as their own COLUMNS on the chunk table.
     *
     * A column added later is not backfilled, and one no longer produced is
     * not dropped — the run log says so each time either happens.
     */
    columnOutputs: string[];
    /** Parallel LLM calls during the stage. */
    workers: number;
  };
  embedding: {
    model: string;
    dims: number;
    deploymentType: string;
    /** '' = the configured SCR endpoint. */
    scrEndpoint: string;
  };
  store: {
    backend: string;
    host: string;
    port: number;
    database: string;
    sslmode: string;
    collection: string;
  };
  tables: {
    /** Prefix of the pipeline CAS tables (<prefix>_ELEMENTS, ...), max 20 chars. */
    prefix: string;
    caslib: string;
  };
  pipelineVersion: string;
  credentialDomain: string;
  /**
   * Operational policy for this setup, seeded from the deployment Options at
   * creation. Recorded per setup so pipeline.yaml and the generated job say
   * what this corpus actually does, rather than deferring to a central
   * setting that may have changed since.
   */
  policies: {
    deletedPolicy: string;
    retainDays: number;
    recordHistory: boolean;
    embedReplicas: number;
    persistElements: boolean;
    persistChunks: boolean;
  };
  /** Set once the ingestion job has been generated for this setup. */
  job?: {
    /** URI of the generated Job Execution definition. */
    definitionUri: string;
  };
}
