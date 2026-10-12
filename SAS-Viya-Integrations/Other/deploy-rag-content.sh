#!/usr/bin/env bash
# Copyright (c) 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Deploy the accelerator's RAG runtime to SAS Content - the Linux/macOS twin
# of deploy-rag-content.ps1 (see its header for the full description).
#
#   ./deploy-rag-content.sh [-s repo-root] [-r '/SAS Agentic AI Accelerator/RAG'] [-p profile] [-K] [-n]
#
# The script calls `mdb content-deploy --only RAG --to <RAG content root>`:
# rag_core, the two job programs, the retrieval model template and the RAG
# custom steps are brought to the state of the checkout, in place. -r is the
# RAG content root, a folder of any name. A custom step is updated and keeps
# its id, a file that differs gets its content replaced, and nothing is
# deleted. -n is a dry run. The checkout is the only source - nothing is
# pulled from the internet.
# Prerequisites: the Model Definition Builder (pip install -e
# "Model-Definition-Builder/cli[viya]") and a sign-in it can use: SAS_VIYA_TOKEN,
# SAS_VIYA_USER + SAS_VIYA_PASSWORD, or the sas-viya CLI's login.

set -euo pipefail

SOURCE_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CONTENT_ROOT='/SAS Agentic AI Accelerator/RAG'
PROFILE=''
DRY_RUN=''
INSECURE=''

while getopts 's:r:p:Kn' opt; do
  case "$opt" in
    s) SOURCE_ROOT="$OPTARG" ;;
    r) CONTENT_ROOT="$OPTARG" ;;
    p) PROFILE="$OPTARG" ;;
    K) INSECURE=1 ;;
    n) DRY_RUN=1 ;;
    *) echo "usage: $0 [-s repo-root] [-r content-root] [-p profile] [-K] [-n]" >&2; exit 1 ;;
  esac
done

ROOT="${CONTENT_ROOT%/}"
case "$ROOT" in
  /?*) ;;
  *) echo "The RAG content root '$CONTENT_ROOT' has to be a SAS Content path, starting with a slash (as in '/SAS Agentic AI Accelerator/RAG')." >&2
     exit 1 ;;
esac

command -v mdb > /dev/null 2>&1 \
  || { echo 'mdb is not installed - run: pip install -e "Model-Definition-Builder/cli[viya]"' >&2; exit 1; }

export MDB_REPO="$SOURCE_ROOT"
[ -n "$PROFILE" ] && export SAS_CLI_PROFILE="$PROFILE"
[ -n "$INSECURE" ] && export SAS_VIYA_VERIFY_SSL=false

ARGS=(content-deploy --only RAG --to "$ROOT")
[ -n "$DRY_RUN" ] && ARGS+=(--dry-run)
exec mdb "${ARGS[@]}"
