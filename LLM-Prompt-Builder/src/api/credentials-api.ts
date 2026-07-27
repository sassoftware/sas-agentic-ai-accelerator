/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Provider-key resolution through a SAS Viya credential domain.
 *
 * Convention: ONE domain (its name configured in the Options pane) holds
 * per-identity credentials whose `secrets` map carries every key under the
 * names the accelerator already uses (`OpenAI`, `Anthropic`, …; vector-store
 * entries are prefixed with the backend name, e.g. `pgvector_password`). A
 * user credential overrides a group credential (`lookupInGroup=true`
 * searches groups only when the signed-in user has none). The multi-key map
 * is authored with the create-credential-domain.sas admin script — see the
 * Managing Credentials administration guide.
 */

import { viyaFetch } from './http-client';

interface CredentialPayload {
  secrets?: Record<string, string>;
}

/**
 * Fetch and decode the signed-in user's secrets map from the domain.
 * Returns an empty map when the user has no credential (404); other failures
 * also resolve to an empty map but are logged, so a credentials-service
 * outage degrades to "no keys" instead of breaking the builder.
 */
export async function resolveDomainSecrets(
  domain: string
): Promise<Record<string, string>> {
  try {
    const response = await viyaFetch(
      `/credentials/domains/${encodeURIComponent(domain)}/secrets?lookupInGroup=true`
    );
    if (response.status === 404) return {};
    if (!response.ok) {
      console.error(
        `Credential lookup for domain ${domain} failed with HTTP ${response.status}.`
      );
      return {};
    }
    const payload = (await response.json()) as CredentialPayload;
    const decoded: Record<string, string> = {};
    for (const [name, value] of Object.entries(payload.secrets ?? {})) {
      try {
        // Secrets travel Base64-encoded; keys are ASCII so atob suffices.
        decoded[name] = window.atob(value);
      } catch {
        console.error(`Credential entry ${name} in ${domain} is not valid Base64.`);
      }
    }
    return decoded;
  } catch (error) {
    console.error(`Credential lookup for domain ${domain} failed.`, error);
    return {};
  }
}
