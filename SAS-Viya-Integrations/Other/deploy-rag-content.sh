#!/usr/bin/env bash
# Copyright (c) 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Deploy the accelerator's RAG content bundle to SAS Content - the Linux/macOS
# twin of deploy-rag-content.ps1 (see its header for the full description).
#
#   ./deploy-rag-content.sh [-s repo-root] [-r /SAS Agentic AI Accelerator/RAG] [-p profile] [-K]
#
# The repository checkout is the only source - nothing is pulled from the
# internet, so the same script works in air-gapped deployments.
# Prerequisites: sas-viya CLI signed in (sas-viya auth login), curl, python3.

set -euo pipefail

SOURCE_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CONTENT_ROOT='/SAS Agentic AI Accelerator/RAG'
PROFILE='Default'
CURL_OPTS=()

while getopts 's:r:p:K' opt; do
  case "$opt" in
    s) SOURCE_ROOT="$OPTARG" ;;
    r) CONTENT_ROOT="$OPTARG" ;;
    p) PROFILE="$OPTARG" ;;
    K) CURL_OPTS+=(-k) ;;
    *) echo "usage: $0 [-s repo-root] [-r content-root] [-p profile] [-K]" >&2; exit 1 ;;
  esac
done

TOKEN=$(python3 -c "import json;print(json.load(open('$HOME/.sas/credentials.json'))['$PROFILE']['access-token'])") \
  || { echo "No sas-viya CLI session for profile '$PROFILE' - run: sas-viya auth login" >&2; exit 1; }
ENDPOINT=$(python3 -c "import json;print(json.load(open('$HOME/.sas/config.json'))['$PROFILE']['sas-endpoint'].rstrip('/'))")

folder_id() {  # folder_id <content-path> -> id (creates the chain as needed)
  local path="$1"
  local id
  id=$(curl -fsS "${CURL_OPTS[@]}" -H "Authorization: Bearer $TOKEN" \
        --get --data-urlencode "path=$path" "$ENDPOINT/folders/folders/@item" 2>/dev/null \
        | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])" 2>/dev/null) || true
  if [ -z "$id" ]; then
    local parent_path="${path%/*}"
    local name="${path##*/}"
    local parent_arg=''
    if [ -n "$parent_path" ]; then
      parent_arg="?parentFolderUri=/folders/folders/$(folder_id "$parent_path")"
    fi
    id=$(curl -fsS "${CURL_OPTS[@]}" -X POST -H "Authorization: Bearer $TOKEN" \
          -H 'Content-Type: application/json' -d "{\"name\": \"$name\"}" \
          "$ENDPOINT/folders/folders$parent_arg" \
          | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")
  fi
  echo "$id"
}

publish_file() {  # publish_file <folder-id> <local-file> <name>
  local fid="$1" file="$2" name="$3"
  curl -fsS "${CURL_OPTS[@]}" -H "Authorization: Bearer $TOKEN" \
    "$ENDPOINT/folders/folders/$fid/members?limit=200" \
    | python3 -c "
import json, sys
for m in json.load(sys.stdin).get('items', []):
    if m.get('name') == '$name' and '/files/files/' in m.get('uri', ''):
        print(m['uri'])" \
    | while read -r uri; do
        curl -fsS "${CURL_OPTS[@]}" -X DELETE -H "Authorization: Bearer $TOKEN" "$ENDPOINT$uri" > /dev/null
      done
  # Raw uploads are named by the Content-Disposition header; without it the
  # files service mints a FileResource<timestamp> name (verified live).
  curl -fsS "${CURL_OPTS[@]}" -X POST -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/octet-stream' \
    -H "Content-Disposition: attachment; filename=\"$name\"" \
    --data-binary "@$file" \
    "$ENDPOINT/files/files?parentFolderUri=/folders/folders/$fid&filename=$name" > /dev/null
}

uploaded=0
deploy_tree() {  # deploy_tree <local-dir> <remote-subdir> <extension> [maxdepth]
  local local_dir="$1" remote_subdir="$2" ext="$3" depth="${4:-}"
  local depth_args=()
  [ -n "$depth" ] && depth_args=(-maxdepth "$depth")
  [ -d "$local_dir" ] || { echo "missing: $local_dir" >&2; return; }
  while IFS= read -r -d '' file; do
    local rel="${file#"$local_dir"/}"
    local rel_dir=''
    case "$rel" in */*) rel_dir="/${rel%/*}" ;; esac
    local remote_path="$CONTENT_ROOT/$remote_subdir$rel_dir"
    publish_file "$(folder_id "$remote_path")" "$file" "${file##*/}"
    uploaded=$((uploaded + 1))
    echo "  $remote_path/${file##*/}"
  done < <(find "$local_dir" "${depth_args[@]}" -type f -name "$ext" -not -path '*__pycache__*' -print0)
}

deploy_tree "$SOURCE_ROOT/SAS-Viya-Integrations/RAG/rag_core" rag_core '*.py'
deploy_tree "$SOURCE_ROOT/SAS-Viya-Integrations/RAG-Ingestion" jobs '*.sas'
# retrieval model template (manifested per RAG Setup) - top level only
deploy_tree "$SOURCE_ROOT/SAS-Viya-Integrations/RAG" models '*.py' 1
echo "Deployed $uploaded files to $CONTENT_ROOT on $ENDPOINT."
