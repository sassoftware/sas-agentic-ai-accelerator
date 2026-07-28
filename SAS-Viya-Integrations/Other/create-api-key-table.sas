/******************************************************************************
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Create the API-key table for the standalone LLM Prompt Builder.
 *
 * The Prompt Builder (embedded as a Data-Driven Content object in SAS Visual
 * Analytics) receives its API key(s) through the object's ASSIGNED DATA rather
 * than the URL, so secrets never appear in the report definition or a link.
 *
 * Table contract (see LLM-Prompt-Builder/src/va/ddc.ts):
 *   - Column 1 = key NAME   -> must match the API_KEY.default value referenced
 *                              by an LLM's options.json (e.g. Anthropic, OpenAI,
 *                              Google).
 *   - Column 2 = key VALUE  -> the actual API key.
 *   - One provider per row.
 *
 * When you add this table to the DDC object in VA, assign the roles in that
 * order: the NAME column first, the VALUE column second.
 *
 * >>> REPLACE the placeholder values below with your real API keys. <<<
 * >>> REPLACE the casuser with your target CAS library. <<<
 ******************************************************************************/
* Start a CAS Session and assign the target library;
cas mySess;

data work.LLM_API_KEYS;
    length KeyName $ 64 KeyValue $ 512;
    label KeyName  = "Key Name"
          KeyValue = "API Key";
    infile datalines dlm='|' truncover;
    input KeyName $ KeyValue $;
    datalines;
Anthropic|REPLACE_WITH_YOUR_ANTHROPIC_API_KEY
OpenAI|REPLACE_WITH_YOUR_OPENAI_API_KEY
Google|REPLACE_WITH_YOUR_GOOGLE_API_KEY
;
run;

proc casUtil inCASLib='casuser' outCASLib='casuser';
    dropTable casData='LLM_API_KEYS' quiet;
    load data=work.LLM_API_KEYS casOut='LLM_API_KEYS';
    promote casData='LLM_API_KEYS' casOut='LLM_API_KEYS';
    save casData='LLM_API_KEYS' casOut='LLM_API_KEYS' replace;
quit;

cas mySess terminate;