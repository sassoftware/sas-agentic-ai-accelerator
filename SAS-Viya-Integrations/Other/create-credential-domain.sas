/******************************************************************************
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Create or update the accelerator's credential domain and one credential.
 *
 * ONE domain holds every key the accelerator needs. A credential belongs to
 * a user or a group and carries a map of named secrets:
 *
 *   OpenAI, Anthropic, Google, ...     LLM provider API keys (the names the
 *                                      LLM options.json files reference)
 *   pgvector_user, pgvector_password   vector-store credentials, prefixed
 *   singlestore_user, ...              with the backend name
 *
 * A user credential overrides a group credential in the same domain. Run
 * this script per identity you want to equip (e.g. once for the LLMConsumers
 * group, and once per user who brings a personal key).
 *
 * Requirements: run in SAS Studio. Creating the DOMAIN and any GROUP
 * credential requires SAS administrator rights; a user may (re)create their
 * OWN user credential in an existing domain.
 *
 * Notes:
 *   - The credential is fully REPLACED on every run (service semantics) -
 *     list every entry the identity should have, not just new ones.
 *   - Set migrateFromKeyTable=1 to merge the rows of an existing
 *     LLM_API_KEYS table (create-api-key-table.sas) into the map, so moving
 *     off the table pattern is one run of this script.
 *   - Inspect/delete later with the sas-viya CLI:
 *       sas-viya credentials domains list / show-info / delete
 *       sas-viya credentials users delete --domain-id ... --identity-id ...
 *
 * >>> EDIT the parameters and the SECRETS map below, then run. <<<
 ******************************************************************************/

/* The domain name - the same value goes into the app's
   "Credential domain" Option */
%let credentialDomain = agentic-ai-keys;

/* Who this credential belongs to: identityType user|group, and the
   user id or custom-group id */
%let identityType = group;
%let identityId   = LLMConsumers;

/* Merge rows from an existing LLM_API_KEYS CAS table into the map (1/0) */
%let migrateFromKeyTable = 0;
%let keyTableCaslib = casuser;
%let keyTableName   = LLM_API_KEYS;

%let _ccd_viyaHost = %sysfunc(getoption(SERVICESBASEURL));

%macro _ccd_export_table;
    %if &migrateFromKeyTable. = 1 %then %do;
        cas _ccdcas;
        caslib _all_ assign sessref=_ccdcas;
        %if %sysfunc(exist(&keyTableCaslib..&keyTableName.)) %then %do;
            filename _ccdkeys "%sysfunc(pathname(work))/ccd_keys.json";
            proc json out=_ccdkeys noSASTags;
                export &keyTableCaslib..&keyTableName.;
            run; quit;
            filename _ccdkeys clear;
        %end;
        %else %do;
            data _null_;
                putLog "WARNING: &keyTableCaslib..&keyTableName. not found - nothing to migrate.";
            run;
        %end;
        cas _ccdcas terminate;
    %end;
%mend _ccd_export_table;
%_ccd_export_table;

proc python restart;
submit;
import base64
import json
import os

import requests

# =========================================================================
# >>> THE SECRETS MAP - replace the placeholders; remove entries you do
# >>> not need, add any the identity should have.
# =========================================================================
SECRETS = {
    "OpenAI":             "REPLACE_WITH_YOUR_OPENAI_API_KEY",
    "Anthropic":          "REPLACE_WITH_YOUR_ANTHROPIC_API_KEY",
    "Google":             "REPLACE_WITH_YOUR_GOOGLE_API_KEY",
    "pgvector_user":      "REPLACE_WITH_YOUR_DATABASE_USER",
    "pgvector_password":  "REPLACE_WITH_YOUR_DATABASE_PASSWORD",
}
# =========================================================================

BASE = str(SAS.symget("_ccd_viyaHost") or "").rstrip("/")
TOKEN = os.environ.get("SAS_SERVICES_TOKEN", "")
VERIFY = os.environ.get("SSLCALISTLOC") or True
AUTH = {"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"}
DOMAIN = str(SAS.symget("credentialDomain")).strip()
IDENTITY_TYPE = str(SAS.symget("identityType")).strip().lower()
IDENTITY_ID = str(SAS.symget("identityId")).strip()

# Merge an exported LLM_API_KEYS table (KeyName/KeyValue or name/value rows)
work_json = (SAS.workpath if SAS.workpath.endswith(os.sep) else SAS.workpath + os.sep) + "ccd_keys.json"
if os.path.exists(work_json):
    with open(work_json, "r", encoding="utf-8") as fh:
        rows = json.load(fh)
    migrated = 0
    for row in rows if isinstance(rows, list) else []:
        lowered = {str(k).lower(): v for k, v in row.items()}
        name = lowered.get("keyname") or lowered.get("name")
        value = lowered.get("keyvalue") or lowered.get("value")
        if name and value and not str(value).startswith("REPLACE_WITH"):
            SECRETS[str(name).strip()] = str(value).strip()
            migrated += 1
    SAS.logMessage(f"credential domain: merged {migrated} entries from the key table")

# Drop untouched placeholders so they never become "keys"
SECRETS = {name: value for name, value in SECRETS.items()
           if value and not str(value).startswith("REPLACE_WITH")}
if not SECRETS:
    SAS.logMessage("No secrets to store - edit the SECRETS map first.", messageType="ERROR")
else:
    # 1. Domain (idempotent PUT; requires admin the first time)
    response = requests.put(
        f"{BASE}/credentials/domains/{DOMAIN}",
        json={"id": DOMAIN, "type": "base64",
              "description": "Keys for the SAS Agentic AI Accelerator "
                             "(LLM providers and RAG vector stores)."},
        headers=AUTH, verify=VERIFY, timeout=60)
    if response.status_code >= 400:
        SAS.logMessage(f"domain create/update failed: HTTP {response.status_code} "
                       f"{response.text[:200]}", messageType="ERROR")
    else:
        # 2. The credential with the full secrets map (PUT = full replacement)
        encoded = {name: base64.b64encode(str(value).encode("utf-8")).decode("ascii")
                   for name, value in SECRETS.items()}
        kind = "users" if IDENTITY_TYPE == "user" else "groups"
        response = requests.put(
            f"{BASE}/credentials/domains/{DOMAIN}/{kind}/{IDENTITY_ID}",
            json={"domainId": DOMAIN, "domainType": "base64",
                  "identityType": IDENTITY_TYPE, "identityId": IDENTITY_ID,
                  "properties": {}, "secrets": encoded},
            headers=AUTH, verify=VERIFY, timeout=60)
        if response.status_code >= 400:
            SAS.logMessage(f"credential create/update failed: HTTP {response.status_code} "
                           f"{response.text[:200]}", messageType="ERROR")
        else:
            SAS.logMessage(f"credential domain '{DOMAIN}': stored "
                           f"{len(SECRETS)} entries for {IDENTITY_TYPE} "
                           f"'{IDENTITY_ID}' ({sorted(SECRETS)})")
endsubmit;
run; quit;
proc python terminate;
run; quit;