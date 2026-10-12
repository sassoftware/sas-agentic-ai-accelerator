# Copyright (c) 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Deploy the accelerator's RAG runtime to SAS Content.
#
# This script calls `mdb content-deploy --only RAG`: the Model Definition
# Builder brings the RAG folder of the content root to the state of this
# checkout, from the list in SAS-Viya-Integrations/package-manifest.yaml -
#
#   SAS-Viya-Integrations/RAG/rag_core/**.py           -> <root>/rag_core/...
#   SAS-Viya-Integrations/RAG-Ingestion/*.sas          -> <root>/jobs/...
#   SAS-Viya-Integrations/RAG/retrieve_context.py      -> <root>/models/...
#   SAS-Viya-Integrations/Custom-Steps/RAG - *.step    -> <root>/steps/...
#
# A file is replaced when it differs, a custom step is updated in place and
# keeps its id (saved flows reference a step by id), nothing is removed but
# the old copy of a replaced file, and what is in the folder and not in the
# list is named and left alone. The checkout is the only source - nothing is
# pulled from the internet, so the same script works in air-gapped
# deployments.
#
# `mdb content-deploy` without --only deploys the whole content folder; run
# it directly for that, and for a dry run of everything.
#
# Prerequisites: the Model Definition Builder (pip install -e
# "Model-Definition-Builder/cli[viya]") and a sign-in it can use: SAS_VIYA_TOKEN,
# SAS_VIYA_USER + SAS_VIYA_PASSWORD, or the sas-viya CLI's login
# (sas-viya auth login). The runner needs write permission on the target
# folder.
#
# Usage:
#   ./deploy-rag-content.ps1                       # deploy from this repo
#   ./deploy-rag-content.ps1 -ContentRoot '/SAS Agentic AI Accelerator/RAG'
#   ./deploy-rag-content.ps1 -DryRun               # say what would change

param(
    [string]$SourceRoot = (Join-Path $PSScriptRoot '..' | Join-Path -ChildPath '..'),
    [string]$ContentRoot = '/SAS Agentic AI Accelerator/RAG',
    [string]$CliProfile = '',
    [switch]$Insecure,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

# The manifest places the RAG runtime in the folder RAG of the content root,
# so the RAG content root this script is given has to be a folder of that name.
$ragRoot = $ContentRoot.TrimEnd('/')
if ($ragRoot -notmatch '^(/.+)/RAG$') {
    throw "The RAG content root '$ContentRoot' has to be a folder named RAG (as in '/SAS Agentic AI Accelerator/RAG'): mdb deploys the RAG runtime into the folder RAG of a content root."
}
$root = $Matches[1]

if (-not (Get-Command mdb -ErrorAction SilentlyContinue)) {
    throw 'mdb is not installed - run: pip install -e "Model-Definition-Builder/cli[viya]"'
}

$env:MDB_REPO = (Resolve-Path $SourceRoot).Path
if ($CliProfile) { $env:SAS_CLI_PROFILE = $CliProfile }
if ($Insecure) { $env:SAS_VIYA_VERIFY_SSL = 'false' }

$arguments = @('content-deploy', '--content-root', $root, '--only', 'RAG')
if ($DryRun) { $arguments += '--dry-run' }
& mdb @arguments
exit $LASTEXITCODE
