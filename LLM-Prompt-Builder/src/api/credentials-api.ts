/**
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Provider-key resolution through SAS Viya credential domains.
 *
 * Convention: one password-type domain per provider key, named
 * `${prefix}${provider}` (e.g. "agentic-ai-OpenAI"); the API key is the
 * credential's password secret. A user credential overrides a group
 * credential (`lookupInGroup=true` searches groups only when the signed-in
 * user has none), so who can use which provider is an identity decision
 * administered in SAS Environment Manager or the sas-viya credentials CLI.
 */

import { viyaFetch } from './http-client';

interface CredentialPayload {
  secrets?: Record<string, string>;
  properties?: Record<string, string>;
}

function secretsPath(prefix: string, provider: string): string {
  return `/credentials/domains/${encodeURIComponent(
    `${prefix}${provider}`
  )}/secrets?lookupInGroup=true`;
}

/**
 * Resolve the signed-in user's key for a provider from its credential domain.
 * Returns null when no credential is available (404) — the caller renders the
 * provider as unavailable; other failures also resolve to null but are logged,
 * so a credentials-service outage degrades to "no keys" instead of breaking
 * the builder.
 */
export async function resolveProviderKey(
  prefix: string,
  provider: string
): Promise<string | null> {
  try {
    const response = await viyaFetch(secretsPath(prefix, provider));
    if (response.status === 404) return null;
    if (!response.ok) {
      console.error(
        `Credential lookup for ${prefix}${provider} failed with HTTP ${response.status}.`
      );
      return null;
    }
    const payload = (await response.json()) as CredentialPayload;
    const encoded = payload.secrets?.password;
    if (!encoded) return null;
    try {
      // Secrets travel Base64-encoded; keys are ASCII so atob suffices.
      return window.atob(encoded);
    } catch {
      console.error(`Credential for ${prefix}${provider} is not valid Base64.`);
      return null;
    }
  } catch (error) {
    console.error(`Credential lookup for ${prefix}${provider} failed.`, error);
    return null;
  }
}

/**
 * Resolve keys for a set of providers in parallel.
 * Returns the resolved keys plus the providers that yielded none.
 */
export async function resolveProviderKeys(
  prefix: string,
  providers: string[]
): Promise<{ keys: Record<string, string>; unavailable: string[] }> {
  const keys: Record<string, string> = {};
  const unavailable: string[] = [];
  await Promise.all(
    providers.map(async (provider) => {
      const key = await resolveProviderKey(prefix, provider);
      if (key) {
        keys[provider] = key;
      } else {
        unavailable.push(provider);
      }
    })
  );
  return { keys, unavailable };
}
