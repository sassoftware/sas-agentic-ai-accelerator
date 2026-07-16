/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * SAS Relationships service wrapper, used to check whether a model is still
 * referenced by other SAS Viya resources before it is deleted.
 */

import { viyaGet, viyaPost } from './http-client';
import type { SasApiCollection } from '../types/models';
import type { RelationshipItem, DependentDecision } from '../types/relationships';

const DECISION_FLOW_PREFIX = '/decisions/flows/';

/**
 * Find the SAS Intelligent Decisioning decisions that depend on a model.
 *
 * Queries the relationships index for everything related to the model and
 * keeps the `Dependent` entries that point at a decision flow. The same flow
 * can be reported multiple times (`/decisions/flows/<id>` and
 * `/decisions/flows/<id>/revisions/<rev>`), so the results are deduplicated
 * by flow id. A single page of 1000 relationships is assumed to be enough.
 *
 * Throws when the relationship query itself fails, so callers can tell
 * "no usage found" apart from "usage could not be verified".
 */
export async function getModelDependentDecisions(
  modelID: string
): Promise<DependentDecision[]> {
  const data = await viyaPost<SasApiCollection<RelationshipItem>>(
    '/relationships/relationships?limit=1000',
    {
      resourceURI: [`/modelRepository/models/${modelID}`],
      direction: 'from',
    },
    'application/vnd.sas.relationship.query+json'
  );

  const flowIDs = new Set<string>();
  for (const item of data?.items ?? []) {
    if (item?.type !== 'Dependent') continue;
    const resourceUri = item?.resourceUri ?? '';
    if (!resourceUri.startsWith(DECISION_FLOW_PREFIX)) continue;
    const flowID = resourceUri.slice(DECISION_FLOW_PREFIX.length).split('/')[0];
    if (flowID) flowIDs.add(flowID);
  }

  return Promise.all(
    [...flowIDs].map(async (flowID): Promise<DependentDecision> => {
      try {
        const flow = await viyaGet<{ name?: string }>(`${DECISION_FLOW_PREFIX}${flowID}`);
        return { id: flowID, name: flow?.name ?? flowID };
      } catch {
        // The name lookup is cosmetic — fall back to the id.
        return { id: flowID, name: flowID };
      }
    })
  );
}
