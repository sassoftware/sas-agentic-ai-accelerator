# Copyright (c) 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Create or update the accelerator's credential domain and one credential,
# using the sas-viya CLI session for authentication.
#
# ONE domain holds every key the accelerator needs. A credential belongs to a
# user or a group and carries a map of named secrets:
#
#   OpenAI, Anthropic, Google, ...       LLM provider API keys (the provider
#                                        names of the model fact sheets)
#   PGVECTOR_RAG_USER, PGVECTOR_RAG_PW   RAG vector-store credentials - the
#   SINGLESTORE_RAG_USER, ...            prefix names the vector DB backend,
#                                        so one domain serves several stores
#
# By default the entries are read from the accelerator's .env file (git-
# ignored - secrets never live inside a script): provider key variables like
# OPENAI_API_KEY map onto their provider entry names, and every *_RAG_USER /
# *_RAG_PW variable is carried over verbatim. Point -EnvFile at any other
# .env to manage multiple environments from separate files. Everything else
# in the .env is ignored.
#
#   .env variable            domain entry
#   ---------------------    -------------
#   OPENAI_API_KEY           OpenAI
#   ANTHROPIC_API_KEY        Anthropic
#   GEMINI_API_KEY           Google
#   OPENROUTER_API_KEY       OpenRouter
#   AZURE_OPENAI_API_KEY     Azure OpenAI
#   MISTRAL_API_KEY          Mistral
#   VOYAGE_API_KEY           Voyage.ai
#   HUGGINGFACE_API_KEY      HuggingFace
#   AWS_BEDROCK_API_KEY      AWS Bedrock
#   <BACKEND>_RAG_USER/_PW   <BACKEND>_RAG_USER/_PW (uppercased)
#
# For full control pass -KeysFile instead: a plain NAME=VALUE file whose
# entries are stored verbatim, no mapping applied.
#
# A user credential overrides a group credential. Run once per identity you
# want to equip. The credential is fully REPLACED on every run - the source
# file must list every entry the identity should have.
#
# Prerequisites: sas-viya CLI installed and signed in (./sas-viya auth login).
# Creating the domain or a GROUP credential requires SAS administrator
# rights; users may (re)create their OWN user credential.
#
# Usage:
#   ./create-credential-domain.ps1 -IdentityType user -IdentityId myuser
#   ./create-credential-domain.ps1 -IdentityType group -IdentityId LLMConsumers -EnvFile C:\envs\prod.env
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
    [string]$EnvFile = '',
    [string]$KeysFile = '',
    [string]$CliProfile = 'Default',
    [switch]$Insecure
)

$ErrorActionPreference = 'Stop'

# default: the repository's git-ignored .env two levels up from this script
# ($PSScriptRoot is not available in parameter defaults on PowerShell 5.1)
if (-not $EnvFile) {
    $EnvFile = Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) '.env'
}

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

# ---- collect the entries (values never printed) ----------------------------
function Read-NameValueFile([string]$path) {
    $entries = [ordered]@{}
    foreach ($line in Get-Content $path) {
        $trimmed = $line.Trim()
        if ($trimmed -eq '' -or $trimmed.StartsWith('#') -or -not $trimmed.Contains('=')) { continue }
        $name, $value = $trimmed.Split('=', 2)
        $name = $name.Trim()
        $value = $value.Trim().Trim('"').Trim("'")
        if ($name -and $value) { $entries[$name] = $value }
    }
    return $entries
}

$providerMap = [ordered]@{
    'OPENAI_API_KEY'       = 'OpenAI'
    'ANTHROPIC_API_KEY'    = 'Anthropic'
    'GEMINI_API_KEY'       = 'Google'
    'OPENROUTER_API_KEY'   = 'OpenRouter'
    'AZURE_OPENAI_API_KEY' = 'Azure OpenAI'
    'MISTRAL_API_KEY'      = 'Mistral'
    'VOYAGE_API_KEY'       = 'Voyage.ai'
    'HUGGINGFACE_API_KEY'  = 'HuggingFace'
    'AWS_BEDROCK_API_KEY'  = 'AWS Bedrock'
}

$secrets = @{}
if ($KeysFile) {
    # raw mode: entries are stored verbatim
    foreach ($entry in (Read-NameValueFile $KeysFile).GetEnumerator()) {
        $secrets[$entry.Key] = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($entry.Value))
    }
    if ($secrets.Count -eq 0) { throw "No entries found in $KeysFile" }
    Write-Host "Read $($secrets.Count) entries from $KeysFile (verbatim)."
}
else {
    if (-not (Test-Path $EnvFile)) {
        throw ".env file not found at '$EnvFile' - pass -EnvFile (or -KeysFile for a raw NAME=VALUE file)"
    }
    foreach ($entry in (Read-NameValueFile $EnvFile).GetEnumerator()) {
        $name = $entry.Key
        if ($providerMap.Contains($name)) {
            $secrets[$providerMap[$name]] = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($entry.Value))
        }
        elseif ($name -match '^[A-Za-z][A-Za-z0-9]*_RAG_(USER|PW)$') {
            $secrets[$name.ToUpper()] = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($entry.Value))
        }
    }
    if ($secrets.Count -eq 0) {
        throw "No credential entries recognized in '$EnvFile' - expected provider keys (OPENAI_API_KEY, ...) and/or <BACKEND>_RAG_USER/<BACKEND>_RAG_PW pairs"
    }
    Write-Host "Mapped $($secrets.Count) entries from $EnvFile."
}

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
