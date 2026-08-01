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

# Windows PowerShell 5.1 still negotiates TLS 1.0/1.1 by default, which a
# current SAS Viya ingress refuses - and the failure surfaces as "the
# underlying connection was closed", which reads like a network or token
# problem and sends the reader to the wrong place entirely. Seen live.
try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch {}

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
    # NOT ServerCertificateValidationCallback: on Windows PowerShell 5.1 the
    # scriptblock is invoked off the PowerShell thread and every request then
    # dies with "the underlying connection was closed" - so -Insecure broke
    # the deploy it was meant to rescue, and did it in language that reads
    # like a network outage (verified live). A certificate policy is the
    # mechanism that works on this runtime.
    if (-not ('SasInsecureCertificatePolicy' -as [type])) {
        Add-Type @'
using System.Net;
using System.Security.Cryptography.X509Certificates;
public class SasInsecureCertificatePolicy : ICertificatePolicy {
    public bool CheckValidationResult(ServicePoint point, X509Certificate certificate,
                                      WebRequest request, int problem) { return true; }
}
'@
    }
    [System.Net.ServicePointManager]::CertificatePolicy = New-Object SasInsecureCertificatePolicy
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
    if ($file.Extension -eq '.step') {
        $stepType = 'application/vnd.sas.data.flow.step+json'
        $stepHeaders = $headers + @{ Accept = $stepType }
        $members = Invoke-RestMethod -Method Get -Headers $headers `
            -Uri "$endpoint/folders/folders/$folderId/members?limit=200"
        $existing = @($members.items) | Where-Object {
            $_.name -eq $file.Name -and $_.uri -like '*/dataFlows/steps/*' }
        if ($existing) {
            # A redeploy MUST keep the step id: saved flows reference
            # /dataFlows/steps/<id>, and POST with overwrite=true mints a new
            # id, which 404s every flow already using the step (verified
            # live). PUT updates in place and leaves those flows working.
            $stepId = ($existing[0].uri -split '/')[-1]
            $definition = Get-Content $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
            $definition | Add-Member -NotePropertyName id -NotePropertyValue $stepId -Force
            $putHeaders = $stepHeaders + @{ 'If-Match' = '*' }
            Invoke-RestMethod -Method Put -Headers $putHeaders `
                -Uri "$endpoint/dataFlows/steps/$stepId" -ContentType $stepType `
                -Body ($definition | ConvertTo-Json -Depth 30 -Compress) | Out-Null
            return
        }
        $uri = "$endpoint/dataFlows/steps?parentFolderUri=/folders/folders/$folderId"
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
