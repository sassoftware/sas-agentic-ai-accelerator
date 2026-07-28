#!/usr/bin/env bash
# Copyright (c) 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Create or update the accelerator's credential domain and one credential,
# using the sas-viya CLI session for authentication. See the PowerShell twin
# (create-credential-domain.ps1) for the full description; usage:
#
#   ./create-credential-domain.sh -t group -i LLMConsumers -k keys.env
#   ./create-credential-domain.sh -t user -i myuser -k my-keys.env [-d domain] [-p profile] [-K]
#
# The keys file is NAME=VALUE lines (# comments), e.g. OpenAI=sk-...
# Prerequisites: sas-viya CLI signed in (sas-viya auth login), curl, python3.

set -euo pipefail

DOMAIN='agentic-ai-keys'
IDENTITY_TYPE='group'
IDENTITY_ID=''
KEYS_FILE=''
PROFILE='Default'
CURL_OPTS=()

while getopts 'd:t:i:k:p:K' opt; do
  case "$opt" in
    d) DOMAIN="$OPTARG" ;;
    t) IDENTITY_TYPE="$OPTARG" ;;
    i) IDENTITY_ID="$OPTARG" ;;
    k) KEYS_FILE="$OPTARG" ;;
    p) PROFILE="$OPTARG" ;;
    K) CURL_OPTS+=(-k) ;;
    *) echo "usage: $0 [-d domain] -t user|group -i identity -k keysfile [-p profile] [-K]" >&2; exit 1 ;;
  esac
done
[ -n "$IDENTITY_ID" ] && [ -n "$KEYS_FILE" ] || {
  echo "usage: $0 [-d domain] -t user|group -i identity -k keysfile [-p profile] [-K]" >&2; exit 1
}
case "$IDENTITY_TYPE" in user|group) ;; *) echo "identity type must be user or group" >&2; exit 1 ;; esac

# ---- sas-viya CLI session (token + endpoint) -------------------------------
TOKEN=$(python3 -c "import json,sys;print(json.load(open('$HOME/.sas/credentials.json'))['$PROFILE']['access-token'])") \
  || { echo "No sas-viya CLI session for profile '$PROFILE' - run: sas-viya auth login" >&2; exit 1; }
ENDPOINT=$(python3 -c "import json;print(json.load(open('$HOME/.sas/config.json'))['$PROFILE']['sas-endpoint'].rstrip('/'))")

# ---- build the request bodies (values never printed) -----------------------
BODY_FILE=$(mktemp)
trap 'rm -f "$BODY_FILE"' EXIT
python3 - "$KEYS_FILE" "$DOMAIN" "$IDENTITY_TYPE" "$IDENTITY_ID" > "$BODY_FILE" <<'PYEOF'
import base64, json, sys
keys_file, domain, identity_type, identity_id = sys.argv[1:5]
secrets = {}
with open(keys_file, encoding="utf-8") as fh:
    for line in fh:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() and value:
            secrets[name.strip()] = base64.b64encode(value.encode()).decode()
if not secrets:
    sys.exit("No entries found in " + keys_file)
print(json.dumps({"id": domain, "type": "base64",
                  "description": "Keys for the SAS Agentic AI Accelerator "
                                 "(LLM providers and RAG vector stores)."}))
print(json.dumps({"domainId": domain, "domainType": "base64",
                  "identityType": identity_type, "identityId": identity_id,
                  "properties": {}, "secrets": secrets}))
print(json.dumps(sorted(secrets)))
PYEOF

DOMAIN_BODY=$(sed -n '1p' "$BODY_FILE")
CRED_BODY=$(sed -n '2p' "$BODY_FILE")
ENTRIES=$(sed -n '3p' "$BODY_FILE")

# ---- 1. the domain (idempotent PUT) ----------------------------------------
curl -fsS "${CURL_OPTS[@]}" -X PUT "$ENDPOINT/credentials/domains/$DOMAIN" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "$DOMAIN_BODY" > /dev/null
echo "Domain '$DOMAIN' created/updated."

# ---- 2. the credential with the full secrets map (PUT = full replacement) --
KIND=$([ "$IDENTITY_TYPE" = user ] && echo users || echo groups)
curl -fsS "${CURL_OPTS[@]}" -X PUT "$ENDPOINT/credentials/domains/$DOMAIN/$KIND/$IDENTITY_ID" \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "$CRED_BODY" > /dev/null
echo "Credential for $IDENTITY_TYPE '$IDENTITY_ID' stored: $ENTRIES"
