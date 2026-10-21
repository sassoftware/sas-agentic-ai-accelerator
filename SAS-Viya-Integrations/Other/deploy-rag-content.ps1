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
# A RAG content root that is not there is created only with -Create: a
# mistyped path does not become a new folder (at the root of SAS Content,
# for one). Without it the script ends with mdb's line and writes nothing.
#
# Prerequisites: the Model Definition Builder (pip install -e
# "Model-Definition-Builder/cli[viya]") and a sign-in it can use: SAS_VIYA_TOKEN,
# SAS_VIYA_USER + SAS_VIYA_PASSWORD, or the sas-viya CLI's login
# (sas-viya auth login). The runner needs write permission on the target
# folder.
#
# What mdb writes to the error stream (a warning, such as urllib3's
# InsecureRequestWarning with -Insecure) is passed on and does not stop the
# script, also when its error stream is redirected (*> or 2>&1). The exit
# code is mdb's.
#
# Usage:
#   ./deploy-rag-content.ps1                       # deploy from this repo
#   ./deploy-rag-content.ps1 -ContentRoot '/Team/Retrieval'
#   ./deploy-rag-content.ps1 -ContentRoot '/Team/Retrieval' -Create   # a new folder
#   ./deploy-rag-content.ps1 -DryRun               # say what would change

param(
    [string]$SourceRoot = (Join-Path $PSScriptRoot '..' | Join-Path -ChildPath '..'),
    [string]$ContentRoot = '/SAS Agentic AI Accelerator/RAG',
    [string]$CliProfile = '',
    [switch]$Insecure,
    [switch]$Create,
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
if ($Create) { $arguments += '--create' }
if ($DryRun) { $arguments += '--dry-run' }
# Windows PowerShell 5.1 turns a line a native command writes to stderr into
# an error record when the error stream is redirected, and 'Stop' would end
# the script there - before mdb's own lines and its exit code. The exit code
# of mdb is what tells success from failure.
$ErrorActionPreference = 'Continue'
& mdb @arguments
exit $LASTEXITCODE
