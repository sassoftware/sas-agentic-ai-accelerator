# Copyright (c) 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Deploy the accelerator's RAG runtime to SAS Content.
#
# This script calls `mdb content-deploy --only RAG --to <RAG content root>`:
# the Model Definition Builder brings the RAG content root to the state of
# this checkout, from the list in SAS-Viya-Integrations/package-manifest.yaml -
#
#   SAS-Viya-Integrations/RAG/rag_core/**.py           -> <root>/rag_core/...
#   SAS-Viya-Integrations/RAG-Ingestion/*.sas          -> <root>/jobs/...
#   SAS-Viya-Integrations/RAG/retrieve_context.py      -> <root>/models/...
#   SAS-Viya-Integrations/Custom-Steps/RAG - *.step    -> <root>/steps/...
#
# The RAG content root is the folder the Prompt Builder option "RAG: content
# root" names - '/SAS Agentic AI Accelerator/RAG' unless a site moved it, and
# then a folder of any name. Everything happens in place: a file that differs
# gets its content replaced and stays the file it was, a custom step is
# updated and keeps its id (saved flows reference a step by id), nothing is
# deleted, and what is in the folder and not in the list is named and left
# alone. The run stops at the first write that fails and lists what it did
# not attempt. The checkout is the only source - nothing is pulled from the
# internet, so the same script works in air-gapped deployments.
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
#   ./deploy-rag-content.ps1 -ContentRoot '/Team/Retrieval'
#   ./deploy-rag-content.ps1 -DryRun               # say what would change

param(
    [string]$SourceRoot = (Join-Path $PSScriptRoot '..' | Join-Path -ChildPath '..'),
    [string]$ContentRoot = '/SAS Agentic AI Accelerator/RAG',
    [string]$CliProfile = '',
    [switch]$Insecure,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$root = $ContentRoot.TrimEnd('/')
if (-not $root.StartsWith('/')) {
    throw "The RAG content root '$ContentRoot' has to be a SAS Content path, starting with a slash (as in '/SAS Agentic AI Accelerator/RAG')."
}

if (-not (Get-Command mdb -ErrorAction SilentlyContinue)) {
    throw 'mdb is not installed - run: pip install -e "Model-Definition-Builder/cli[viya]"'
}

$env:MDB_REPO = (Resolve-Path $SourceRoot).Path
if ($CliProfile) { $env:SAS_CLI_PROFILE = $CliProfile }
if ($Insecure) { $env:SAS_VIYA_VERIFY_SSL = 'false' }

$arguments = @('content-deploy', '--only', 'RAG', '--to', $root)
if ($DryRun) { $arguments += '--dry-run' }
& mdb @arguments
exit $LASTEXITCODE
