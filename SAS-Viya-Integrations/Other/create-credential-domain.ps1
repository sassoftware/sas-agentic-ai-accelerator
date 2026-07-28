# Copyright (c) 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Create or update the accelerator's credential domain and one credential,
# using the sas-viya CLI session for authentication.
#
# ONE domain holds every key the accelerator needs. A credential belongs to a
# user or a group and carries a map of named secrets:
#
#   OpenAI, Anthropic, Google, ...     LLM provider API keys (the names the
#                                      LLM options.json files reference)
#   pgvector_user, pgvector_password   vector-store credentials, prefixed
#   singlestore_user, ...              with the backend name
#
# A user credential overrides a group credential. Run once per identity you
# want to equip. The credential is fully REPLACED on every run - list every
# entry the identity should have.
#
# Prerequisites: sas-viya CLI installed and signed in (./sas-viya auth login).
# Creating the domain or a GROUP credential requires SAS administrator
# rights; users may (re)create their OWN user credential.
#
# The keys file is a plain NAME=VALUE file (one entry per line, # comments),
# e.g.:
#   OpenAI=sk-...
#   Anthropic=sk-ant-...
#   pgvector_user=rag_ingest
#   pgvector_password=...
#
# Usage:
#   ./create-credential-domain.ps1 -IdentityType group -IdentityId LLMConsumers -KeysFile keys.env
#   ./create-credential-domain.ps1 -IdentityType user -IdentityId myuser -KeysFile my-keys.env
#
# Inspect / delete afterwards with the CLI:
#   ./sas-viya credentials domains list | show-info | delete
#   ./sas-viya credentials users delete --domain-id ... --identity-id ...

param(
    [string]$Domain = 'agentic-ai-keys',
    [ValidateSet('user', 'group')]
    [string]$IdentityType = 'group',
    [Parameter(Mandatory = $true)]
    [string]$IdentityId,
    [Parameter(Mandatory = $true)]
    [string]$KeysFile,
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

# ---- read the keys file (values never printed) -----------------------------
$secrets = @{}
foreach ($line in Get-Content $KeysFile) {
    $trimmed = $line.Trim()
    if ($trimmed -eq '' -or $trimmed.StartsWith('#') -or -not $trimmed.Contains('=')) { continue }
    $name, $value = $trimmed.Split('=', 2)
    if ($name.Trim() -and $value) {
        $secrets[$name.Trim()] = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($value))
    }
}
if ($secrets.Count -eq 0) { throw "No entries found in $KeysFile" }

$headers = @{ Authorization = "Bearer $token"; 'Content-Type' = 'application/json' }

# ---- 1. the domain (idempotent PUT) ----------------------------------------
$domainBody = @{
    id          = $Domain
    type        = 'base64'
    description = 'Keys for the SAS Agentic AI Accelerator (LLM providers and RAG vector stores).'
} | ConvertTo-Json
Invoke-RestMethod -Method Put -Uri "$endpoint/credentials/domains/$Domain" `
    -Headers $headers -Body $domainBody | Out-Null
Write-Host "Domain '$Domain' created/updated."

# ---- 2. the credential with the full secrets map (PUT = full replacement) --
$kind = if ($IdentityType -eq 'user') { 'users' } else { 'groups' }
$credentialBody = @{
    domainId     = $Domain
    domainType   = 'base64'
    identityType = $IdentityType
    identityId   = $IdentityId
    properties   = @{}
    secrets      = $secrets
} | ConvertTo-Json
Invoke-RestMethod -Method Put -Uri "$endpoint/credentials/domains/$Domain/$kind/$IdentityId" `
    -Headers $headers -Body $credentialBody | Out-Null
Write-Host "Credential for $IdentityType '$IdentityId' stored with $($secrets.Count) entries: $($secrets.Keys -join ', ')."
