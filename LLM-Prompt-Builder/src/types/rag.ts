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
  [key: string]: string;
}

/**
 * The RAG Setup as the UI round-trips it (`rag-setup.json` on the RAG Setup
 * model). `pipeline.yaml` is GENERATED from this on every save — the yaml is
 * the governance artifact, this JSON is the editor state. Version the shape.
 */
export interface RagSetup {
  version: 1;
  /** User-authored documentation (also rendered to documentation.md). */
  documentation: {
    description: string;
    intendedUse: string;
    limitations: string;
  };
  source: {
    /** Filesystem path visible from the ingestion compute context. */
    path: string;
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
  /** Set once the ingestion job has been generated for this setup. */
  job?: {
    /** URI of the generated Job Execution definition. */
    definitionUri: string;
  };
}
