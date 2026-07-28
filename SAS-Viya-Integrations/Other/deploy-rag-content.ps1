# Copyright (c) 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Deploy the accelerator's RAG content bundle to SAS Content.
#
# Mirrors the repository's RAG runtime into the governed SAS Content folder
# the ingestion job and Studio custom steps bootstrap from:
#
#   SAS-Viya-Integrations/RAG/rag_core/*.py   -> <root>/rag_core/...
#   SAS-Viya-Integrations/RAG-Ingestion/*.sas -> <root>/jobs/...
#
# The repository checkout (or an offline copy of it) is the only source -
# nothing is pulled from the internet, so the same script works in air-gapped
# deployments. Re-run after every change; files are replaced, and SAS Content
# files that no longer exist in the repository are reported (not deleted).
#
# Prerequisites: sas-viya CLI installed and signed in (./sas-viya auth login).
# The runner needs write permission on the target folder (the setup guide
# makes it read-only for regular users).
#
# Usage:
#   ./deploy-rag-content.ps1                       # deploy from this repo
#   ./deploy-rag-content.ps1 -ContentRoot /SAS Agentic AI Accelerator/RAG

param(
    [string]$SourceRoot = (Join-Path $PSScriptRoot '..' | Join-Path -ChildPath '..'),
    [string]$ContentRoot = '/SAS Agentic AI Accelerator/RAG',
    [string]$CliProfile = 'Default',
    [switch]$Insecure
)

$ErrorActionPreference = 'Stop'

# ---- sas-viya CLI session (token + endpoint) -------------------------------
$sasDir = Join-Path $HOME '.sas'
$credentials = Get-Content (Join-Path $sasDir 'credentials.json') -Raw | ConvertFrom-Json
$config = Get-Content (Join-Path $sasDir 'config.json') -Raw | ConvertFrom-Json
$token = $credentials.$CliProfile.'access-token'
$endpoint = ($config.$CliProfile.'sas-endpoint').TrimEnd('/')
if (-not $token -or -not $endpoint) {
    throw "No sas-viya CLI session for profile '$CliProfile' - run: sas-viya auth login"
}
if ($Insecure) {
    try { [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true } } catch {}
}
$headers = @{ Authorization = "Bearer $token" }

# ---- what to deploy --------------------------------------------------------
$sourceRoot = (Resolve-Path $SourceRoot).Path
$bundle = @(
    @{ Local = Join-Path $sourceRoot 'SAS-Viya-Integrations\RAG\rag_core'; Remote = 'rag_core'; Filter = '*.py' },
    @{ Local = Join-Path $sourceRoot 'SAS-Viya-Integrations\RAG-Ingestion'; Remote = 'jobs'; Filter = '*.sas' },
    # retrieval model template (manifested per RAG Setup) - top level only,
    # rag_core/ and tests/ must not be re-included here
    @{ Local = Join-Path $sourceRoot 'SAS-Viya-Integrations\RAG'; Remote = 'models'; Filter = '*.py'; NoRecurse = $true },
    # the five RAG custom steps - SAS Studio picks .step files up from SAS
    # Content, so deploying them here makes them usable in flows
    @{ Local = Join-Path $sourceRoot 'SAS-Viya-Integrations\Custom-Steps'; Remote = 'steps'; Filter = 'RAG - *.step'; NoRecurse = $true }
)

# ---- folder + file helpers -------------------------------------------------
$folderIds = @{}
function Get-FolderId([string]$path) {
    if ($folderIds.ContainsKey($path)) { return $folderIds[$path] }
    try {
        $found = Invoke-RestMethod -Method Get -Headers $headers `
            -Uri "$endpoint/folders/folders/@item?path=$([uri]::EscapeDataString($path))"
        $folderIds[$path] = $found.id
        return $found.id
    } catch {
        $parentPath = Split-Path $path -Parent
        $parentPath = $parentPath.Replace('\', '/')
        $name = Split-Path $path -Leaf
        $parentId = if ($parentPath -and $parentPath -ne '/') { Get-FolderId $parentPath } else { $null }
        $uri = "$endpoint/folders/folders"
        if ($parentId) { $uri += "?parentFolderUri=/folders/folders/$parentId" }
        $made = Invoke-RestMethod -Method Post -Headers $headers -ContentType 'application/json' `
            -Uri $uri -Body (@{ name = $name } | ConvertTo-Json)
        $folderIds[$path] = $made.id
        return $made.id
    }
}

function Publish-File([string]$folderId, [System.IO.FileInfo]$file) {
    # Custom steps are dataFlows SERVICE resources, not plain Content files -
    # a raw-uploaded .step renders as an empty step editor (verified live).
    # Register through the service; overwrite=true replaces on redeploy.
    if ($file.Extension -eq '.step') {
        $stepHeaders = $headers + @{ Accept = 'application/vnd.sas.data.flow.step+json' }
        $uri = "$endpoint/dataFlows/steps?parentFolderUri=/folders/folders/$folderId&overwrite=true"
        Invoke-RestMethod -Method Post -Headers $stepHeaders -Uri $uri `
            -ContentType 'application/json' -InFile $file.FullName | Out-Null
        return
    }
    $members = Invoke-RestMethod -Method Get -Headers $headers `
        -Uri "$endpoint/folders/folders/$folderId/members?limit=200"
    foreach ($member in @($members.items)) {
        if ($member.name -eq $file.Name -and $member.uri -like '*/files/files/*') {
            Invoke-RestMethod -Method Delete -Headers $headers -Uri "$endpoint$($member.uri)" | Out-Null
        }
    }
    $uri = "$endpoint/files/files?parentFolderUri=/folders/folders/$folderId" +
           "&filename=$([uri]::EscapeDataString($file.Name))"
    # Raw uploads are named by the Content-Disposition header; without it the
    # files service mints a FileResource<timestamp> name (verified live).
    $uploadHeaders = $headers + @{
        'Content-Disposition' = "attachment; filename=""$($file.Name)"""
    }
    Invoke-RestMethod -Method Post -Headers $uploadHeaders -Uri $uri `
        -ContentType 'application/octet-stream' -InFile $file.FullName | Out-Null
}

# ---- deploy ----------------------------------------------------------------
$uploaded = 0
foreach ($part in $bundle) {
    if (-not (Test-Path $part.Local)) { Write-Warning "missing: $($part.Local)"; continue }
    $files = Get-ChildItem $part.Local -Recurse:(-not $part.NoRecurse) -File -Filter $part.Filter |
        Where-Object { $_.FullName -notmatch '__pycache__' }
    foreach ($file in $files) {
        $relativeDir = [System.IO.Path]::GetDirectoryName(
            $file.FullName.Substring($part.Local.Length).TrimStart('\'))
        $remotePath = ("$ContentRoot/$($part.Remote)" +
            $(if ($relativeDir) { '/' + $relativeDir.Replace('\', '/') } else { '' }))
        Publish-File (Get-FolderId $remotePath) $file
        $uploaded++
        Write-Host "  $remotePath/$($file.Name)"
    }
}
Write-Host "Deployed $uploaded files to $ContentRoot on $endpoint."
