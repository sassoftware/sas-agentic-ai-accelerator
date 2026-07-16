/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Minimal SAS Relationships service response shapes used by the Prompt
 * Builder. Only the fields that are actually read are typed; a permissive
 * index signature keeps the rest available without over-constraining.
 */

/** One relationship record returned by POST /relationships/relationships. */
export interface RelationshipItem {
  type?: string;
  resourceUri?: string;
  relatedResourceUri?: string;
  [key: string]: unknown;
}

/** A decision flow that depends on a model, deduplicated by flow id. */
export interface DependentDecision {
  id: string;
  name: string;
}
