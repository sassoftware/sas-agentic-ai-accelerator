#!/usr/bin/env bash
# Copyright (c) 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Create or update the accelerator's credential domain and one credential,
# using the sas-viya CLI session for authentication. See the PowerShell twin
# (create-credential-domain.ps1) for the full description.
#
# By default the entries come from the accelerator's git-ignored .env file:
# provider key variables (OPENAI_API_KEY, ANTHROPIC_API_KEY, ...) map onto
# their provider entry names (OpenAI, Anthropic, ...), and every
# <BACKEND>_RAG_USER / <BACKEND>_RAG_PW variable is carried over verbatim
# (uppercased) so one domain serves several vector stores. Use -e to point
# at a different .env (multiple environments); use -k for a raw NAME=VALUE
# file stored verbatim without any mapping.
#
#   ./create-credential-domain.sh -t user -i myuser
#   ./create-credential-domain.sh -t group -i LLMConsumers -e /path/prod.env
#   ./create-credential-domain.sh -t user -i myuser -k my-keys.env [-d domain] [-p profile] [-K]
#
# Prerequisites: sas-viya CLI signed in (sas-viya auth login), curl, python3.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DOMAIN='agentic-ai-keys'
IDENTITY_TYPE='group'
IDENTITY_ID=''
ENV_FILE="$SCRIPT_DIR/../../.env"
KEYS_FILE=''
PROFILE='Default'
CURL_OPTS=()

while getopts 'd:t:i:e:k:p:K' opt; do
  case "$opt" in
    d) DOMAIN="$OPTARG" ;;
    t) IDENTITY_TYPE="$OPTARG" ;;
    i) IDENTITY_ID="$OPTARG" ;;
    e) ENV_FILE="$OPTARG" ;;
    k) KEYS_FILE="$OPTARG" ;;
    p) PROFILE="$OPTARG" ;;
    K) CURL_OPTS+=(-k) ;;
    *) echo "usage: $0 [-d domain] -t user|group -i identity [-e envfile | -k keysfile] [-p profile] [-K]" >&2; exit 1 ;;
  esac
done
[ -n "$IDENTITY_ID" ] || {
  echo "usage: $0 [-d domain] -t user|group -i identity [-e envfile | -k keysfile] [-p profile] [-K]" >&2; exit 1
}
case "$IDENTITY_TYPE" in user|group) ;; *) echo "identity type must be user or group" >&2; exit 1 ;; esac

# ---- sas-viya CLI session (token + endpoint) -------------------------------
TOKEN=$(python3 -c "import json,sys;print(json.load(open('$HOME/.sas/credentials.json'))['$PROFILE']['access-token'])") \
  || { echo "No sas-viya CLI session for profile '$PROFILE' - run: sas-viya auth login" >&2; exit 1; }
ENDPOINT=$(python3 -c "import json;print(json.load(open('$HOME/.sas/config.json'))['$PROFILE']['sas-endpoint'].rstrip('/'))")

# ---- build the request bodies (values never printed) -----------------------
BODY_FILE=$(mktemp)
trap 'rm -f "$BODY_FILE"' EXIT
python3 - "$KEYS_FILE" "$ENV_FILE" "$DOMAIN" "$IDENTITY_TYPE" "$IDENTITY_ID" > "$BODY_FILE" <<'PYEOF'
import base64, json, os, re, sys
keys_file, env_file, domain, identity_type, identity_id = sys.argv[1:6]

PROVIDER_MAP = {
    "OPENAI_API_KEY": "OpenAI",
    "ANTHROPIC_API_KEY": "Anthropic",
    "GEMINI_API_KEY": "Google",
    "OPENROUTER_API_KEY": "OpenRouter",
    "AZURE_OPENAI_API_KEY": "Azure OpenAI",
    "MISTRAL_API_KEY": "Mistral",
    "VOYAGE_API_KEY": "Voyage.ai",
    "HUGGINGFACE_API_KEY": "HuggingFace",
    "AWS_BEDROCK_API_KEY": "AWS Bedrock",
}
RAG_ENTRY = re.compile(r"^[A-Za-z][A-Za-z0-9]*_RAG_(USER|PW)$")

def read_pairs(path):
    pairs = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            name, value = name.strip(), value.strip().strip('"').strip("'")
            if name and value:
                pairs[name] = value
    return pairs

secrets = {}
if keys_file:
    # raw mode: entries are stored verbatim
    for name, value in read_pairs(keys_file).items():
        secrets[name] = base64.b64encode(value.encode()).decode()
    if not secrets:
        sys.exit("No entries found in " + keys_file)
else:
    if not os.path.exists(env_file):
        sys.exit(".env file not found at '" + env_file + "' - pass -e (or -k for a raw file)")
    for name, value in read_pairs(env_file).items():
        if name in PROVIDER_MAP:
            secrets[PROVIDER_MAP[name]] = base64.b64encode(value.encode()).decode()
        elif RAG_ENTRY.match(name):
            secrets[name.upper()] = base64.b64encode(value.encode()).decode()
    if not secrets:
        sys.exit("No credential entries recognized in '" + env_file + "' - expected "
                 "provider keys (OPENAI_API_KEY, ...) and/or <BACKEND>_RAG_USER/_PW pairs")

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
