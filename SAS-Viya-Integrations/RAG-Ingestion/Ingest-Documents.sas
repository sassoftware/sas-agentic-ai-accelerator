/******************************************************************************
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * RAG document ingestion - the schedulable golden path.
 *
 * Crawls a document source, extracts/chunks/embeds new and changed documents
 * through the governed SCR embedding container, loads them into the vector
 * store, and maintains the incremental-ingestion ledger. The same rag_core
 * runtime powers the SAS Studio custom steps; this job is the artifact
 * scheduled re-ingestion depends on.
 *
 * Import as a SAS Job Execution job definition (like Optimize-Prompt-DSPy)
 * or run in SAS Studio. The compute context's Python needs the packages from
 * the Administration Guide (requests, pandas, pypdfium2, markitdown, plus the
 * driver of the backend in use: psycopg2-binary for pgvector, singlestoredb
 * for SingleStore); rag_core itself is downloaded from SAS Content at run time.
 * If you %include this program, do it at TOP LEVEL - a %include nested
 * inside a macro prevents the proc python submit block from executing
 * (verified live).
 *
 * Parameters (all optional unless noted; defaults below):
 *   sourcePath       - REQUIRED. Where the documents are. A bare path or a
 *                      sasserver:/path is the compute file system; a
 *                      sascontent:/path is a SAS Content folder. (visible from this compute
 *                      context) whose documents are ingested
 *   collection       - REQUIRED. Vector-store collection (lowercase
 *                      identifier, e.g. rag_hr_policies_v1)
 *   backend          - vector store backend: pgvector or singlestore
 *   storeHost/storePort/storeDb/storeSslmode - vector store connection
 *                      CONFIGURATION (not secrets; secrets come from the
 *                      credential domain)
 *   deletedPolicy    - a document that vanished from the source: retire
 *                      (default - its chunks stay as unretrievable history)
 *                      or purge (its chunks are removed for good)
 *   retainDays       - drop retired chunk generations older than this many
 *                      days after loading; 0 (default) keeps them forever
 *   credentialDomain - credential domain holding <BACKEND>_RAG_USER/_RAG_PW
 *                      (default agentic-ai-keys, the accelerator standard)
 *   scrEndpoint      - SCR base URL hosting the embedding container
 *                      (default: <viya-host>/llm)
 *   embedModel       - embedding model name (default all_minilm_l6_v2)
 *   deploymentType   - k8s (default) or aca
 *   inputTokenLimit  - the chunk window in tokens; the chunker uses ~85% of
 *                      it as its budget, and it may not exceed the embedding
 *                      model's own limit (default 256)
 *   includeCode      - 1 to ingest source files (.py, .sas, .r, .js, ...) as
 *                      plain text. Default 0: they are listed as SKIPPED with
 *                      the reason, never silently dropped
 *   chunker          - recursive (default) or paragraph
 *   overlapTokens    - chunk overlap for the recursive chunker (default 30)
 *   enrichSteps      - the enrichment stages, run in the order given, one
 *                      LLM call per chunk EACH. Packed as
 *                      model|version|mapping|header|columns|workers with the
 *                      records separated by ~; see rag_core.enrich
 *                      .parse_steps. Blank (default) = no enrichment.
 *                      Within one record: model is a manifested Prompt
 *                      Builder prompt; version pins it (blank follows the
 *                      model, so re-manifesting changes what the next run
 *                      writes); mapping is input=field;input=field over the
 *                      fields chunk, document, neighbours, heading, filename,
 *                      source, position; header is the output stored as the
 *                      chunk's context header, which the Embed step prepends
 *                      before embedding; columns are the outputs stored as
 *                      their own columns on the chunk table, comma separated
 *                      (a column added later is NOT backfilled and one no
 *                      longer produced is not dropped); workers is the
 *                      parallel LLM calls for that step. Only ONE step may
 *                      write the context header, and two steps may not store
 *                      the same column
 *   enrichPromptModel/enrichPromptVersion/enrichMapping/enrichHeaderOutput/
 *   enrichColumns/enrichWorkers - the one-prompt shorthand for the above,
 *                      read only when enrichSteps is blank, so a job or flow
 *                      written before enrichment could chain still runs
 *   pipelineVersion  - bump to force a full re-embed (default v1)
 *   configHash       - config fingerprint stamped into the ledger; a run
 *                      whose value differs from the ledger's last run fails
 *                      fast (drift guard). Blank = computed from parameters
 *   ledgerCaslib/ledgerTable - the incremental ledger location
 *                      (default casuser.RAG_INGESTION_LEDGER)
 *   recordHistory    - 1 (default) records the run in rag_runs,
 *                      rag_doc_events and rag_configs beside the collection
 *                      and publishes the first two to CAS for reporting, as
 *                      <prefix>_RUNS and <prefix>_DOC_EVENTS in ledgerCaslib,
 *                      promoted and saved - the tables RAG - Load Vector
 *                      Store publishes. <prefix> is ledgerTable without its
 *                      ending _LEDGER. 0 records nothing and leaves the two
 *                      tables as they are
 *   ragCorePath      - SAS Content folder holding rag_core
 *
 * Contract: the program ALWAYS runs to its last statement. A raised SAS
 * error would leave the session stopped without Job Execution ever receiving
 * its completion handshake - the same lesson the optimize job learned live.
 * The outcome lives in the log (the milestone "Ingestion failed: <reason>"
 * and the last line, NOTE or ERROR) and in the ledger. A run that failed
 * also sets SYSCC as its last statement, so that SAS Job Execution reports
 * the job as failed and not as completed - the way a job generated from a
 * flow ends (JOB_EPILOGUE in rag_core/registration.py).
 ******************************************************************************/

%macro _rag_default(name, value);
    %if not %symexist(&name.) %then %do;
        %global &name.;
        %let &name. = &value.;
    %end;
%mend _rag_default;

%_rag_default(sourcePath, );
%_rag_default(collection, );
%_rag_default(backend, pgvector);
%_rag_default(storeHost, );
%_rag_default(storePort, );          /* blank = the backend's default port */
%_rag_default(storeDb, );
%_rag_default(storeSslmode, prefer);
%_rag_default(credentialDomain, agentic-ai-keys);
%_rag_default(scrEndpoint, );
%_rag_default(embedModel, all_minilm_l6_v2);
%_rag_default(deploymentType, k8s);
%_rag_default(inputTokenLimit, 256);
%_rag_default(chunker, recursive);
%_rag_default(overlapTokens, 30);
%_rag_default(enrichSteps, );          /* blank = no Enrich stage */
%_rag_default(enrichPromptModel, );    /* the one-prompt shorthand, read */
%_rag_default(enrichMapping, );        /* only when enrichSteps is blank */
%_rag_default(enrichPromptVersion, );  /* blank = follow the model */
%_rag_default(enrichHeaderOutput, );
%_rag_default(enrichColumns, );        /* comma separated */
%_rag_default(enrichWorkers, 4);
%_rag_default(deletedPolicy, retire);   /* retire (keep history) | purge */
%_rag_default(retainDays, 0);           /* 0 = keep retired chunks forever */
%_rag_default(replicas, 1);             /* embedding container replicas */
%_rag_default(includeCode, 0);        /* 1 = ingest .py/.sas/... as text */
%_rag_default(recordHistory, 1);        /* 1 = write rag_runs / rag_doc_events */
%_rag_default(pipelineVersion, v1);
%_rag_default(configHash, );
%_rag_default(ledgerCaslib, casuser);
%_rag_default(ledgerTable, RAG_INGESTION_LEDGER);
%_rag_default(ragCorePath, /SAS Agentic AI Accelerator/RAG/rag_core);

%let _rag_viyaHost = %sysfunc(getoption(SERVICESBASEURL));
%let _rag_rc = 1;
%let _rag_error = The Python program did not run.;
%let _rag_summary = ;
/* run until something before the Python program says otherwise: fail makes
   the program return at once, and _rag_error then holds the reason */
%let _rag_state = run;
/* the run lock: the id of this run, which its lock row carries; the newest
   lock row another run left in the ledger; and whether this run appended a
   row of its own, which is what it has to hand back when it fails */
%let _rag_runId = ;
%let _rag_lockHeld = ;
%let _rag_lockTaken = 0;
/* the run history for CAS: off until the Python program has read it from the
   vector store (read) or could not (failed, with the reason); then, for the
   runs and for the document events, the rows it wrote to WORK, their columns
   as name:type and the labels of the columns as name=label, split by a bar */
%let _rag_histState = off;
%let _rag_histError = ;
%let _rag_histRuns = 0;
%let _rag_histRunVars = ;
%let _rag_histRunLabels = ;
%let _rag_histEvents = 0;
%let _rag_histEventVars = ;
%let _rag_histEventLabels = ;

/* A pipeline table that was saved but is not in memory - which is how a CAS
   server restart leaves every one of them - is loaded back before anything
   looks at it. The code below only ever tests the in-memory table, so a
   saved table that is not loaded would read as no previous run at all:
   every document new, the whole corpus embedded again, and the saved file
   overwritten by the result.
   Nothing is done for a table that is in memory or was never saved. A saved
   file that cannot be loaded FAILS the run, through the caller's state and
   error variables, because carrying on would destroy what the file holds. */
%macro _rag_reload_saved(sessref, caslib, table, statevar, errorvar);
    %local _rag_reload_outcome _rag_reload_syscc;
    %if %superq(&statevar.) NE run %then %return;
    %let _rag_reload_outcome = unknown;
    %let _rag_reload_syscc = &syscc.;
    proc cas;
        session &sessref.;
        outcome = "memory";
        table.tableExists result = found / caslib = "&caslib." name = "&table.";
        if found.exists == 0 then do;
            /* The saved file carries the table name as it was typed and a
               path-based caslib is case-sensitive, while CAS table names
               are not - so the file is looked up in the listing: the exact
               name when there is one, otherwise the same name in another
               case. */
            outcome = "absent";
            saved = "";
            table.fileInfo result = listing status = listed / caslib = "&caslib.";
            if listed.severity == 0 then do;
                if exists(listing, "FileInfo") then do;
                    do entry over listing.FileInfo;
                        if entry.Name == "&table..sashdat" then
                            saved = entry.Name;
                        else if saved == "" and upcase(entry.Name) == upcase("&table..sashdat") then
                            saved = entry.Name;
                    end;
                end;
            end;
            else outcome = "unlisted";
            if saved != "" then do;
                table.loadTable result = ignored status = loaded / caslib = "&caslib." path = saved
                    casOut = {caslib = "&caslib.", name = "&table.", promote = true};
                /* A concurrent session may have loaded the table in the
                   meantime. That refuses this load, and is just as good. */
                table.tableExists result = found / caslib = "&caslib." name = "&table.";
                if found.exists != 0 then outcome = "loaded";
                else outcome = "failed";
            end;
        end;
        /* "L": the macro variable is local to this macro, and without a
           scope CASL writes to the global symbol table */
        call symputx("_rag_reload_outcome", outcome, "L");
    quit;
    /* Looking is not part of the run and never decides its condition code:
       not a load that was refused because another session got there first,
       and not a load that failed either - that one is reported through the
       caller's state, the way the caller reports every other failure. */
    %let syscc = &_rag_reload_syscc.;
    %if &_rag_reload_outcome. EQ failed %then %do;
        %let &statevar. = fail;
        /* one line: the caller writes it to the log as its ERROR line */
        %let &errorvar. = the saved table &caslib..&table. could not be loaded into memory (CAS messages above) - stopped, because an empty table would re-embed the corpus and overwrite the saved file;
        %return;
    %end;
    /* The run goes on in the two cases that are left, and says what that
       means. unlisted: the table is not in memory and the files of the caslib
       could not be listed, so no saved copy was looked for - the case the
       reload is there for, without the reload. Anything else: PROC CAS ended
       before it handed back an outcome, and not even that much is known. */
    %if &_rag_reload_outcome. EQ loaded %then
        %put NOTE: RAG: &caslib..&table. was saved but not in memory and has been loaded from its saved file.;
    %else %if &_rag_reload_outcome. EQ unlisted %then %do;
        %put WARNING: RAG: &caslib..&table. is not in memory, and the files of the caslib &caslib. could not be listed (CAS messages above) - a saved copy could not be looked for.;
        %put WARNING: RAG: the run continues without the table and saves over a copy that may be there. A ledger that is not in memory means that every document counts as new and is embedded again.;
    %end;
    %else %if &_rag_reload_outcome. NE memory and &_rag_reload_outcome. NE absent %then %do;
        %put WARNING: RAG: could not look for &caslib..&table. - PROC CAS ended before it said whether the table is in memory or saved (messages above). The run continues with what is in memory.;
        %put WARNING: RAG: if the table is not in memory, the run saves over a copy that may be there. A ledger that is not in memory means that every document counts as new and is embedded again.;
    %end;
%mend _rag_reload_saved;

/* ---- One ledger, two writers ----------------------------------------------
   The ledger is written by the RAG steps and by the single-program job
   Ingest-Documents.sas, and the two do not leave it alike: the Load and Purge
   steps upload it through SWAT, which makes its text columns VARCHAR, and
   the job loads it from a SAS data set, which makes them CHAR. CAS refuses
   to append a row whose column is the one where the table has the other
   (The variable doc_id has been specified with incompatible data types -
   found live, in both directions), so a run lock declared one way could
   only be written to a ledger the same writer had left.
   The table is asked what it holds and the lock row is declared to match.
   doc_id answers for all text columns, because each writer creates all of
   them the same way. An answer that is neither char nor varchar falls back
   to what this writer leaves itself, which is what it declared before.
   The same two macros stand in RAG - List Documents, RAG - Purge Documents
   and Ingest-Documents.sas; only the prefix of their names differs. */
%macro _rag_ledger_types(session, caslib, table, fallback);
    %global _rag_ledgerTypes;
    %let _rag_ledgerTypes = ;
    %if %sysfunc(exist(work._rag_ledger_ci)) %then %do;
        proc delete data=work._rag_ledger_ci;
        run;
    %end;
    proc cas;
        session &session.;
        table.columnInfo result=_rag_ci /
            table={caslib="&caslib.", name="&table."};
        saveresult _rag_ci dataout=work._rag_ledger_ci;
    quit;
    %if %sysfunc(exist(work._rag_ledger_ci)) %then %do;
        data _null_;
            set work._rag_ledger_ci;
            if lowcase(strip(Column)) = "doc_id" then
                call symputx("_rag_ledgerTypes", lowcase(strip(Type)), "G");
        run;
        proc delete data=work._rag_ledger_ci;
        run;
    %end;
    %if &_rag_ledgerTypes. NE char %then %if &_rag_ledgerTypes. NE varchar %then %do;
        data _null_;
            putLog "NOTE: the column types of the ledger &caslib..&table. could not be read - the run lock is written as &fallback.";
        run;
        %let _rag_ledgerTypes = &fallback.;
        %return;
    %end;
    data _null_;
        putLog "NOTE: the ledger &caslib..&table. holds its text columns as &_rag_ledgerTypes. - the run lock is written the same way";
    run;
%mend _rag_ledger_types;
/* The LENGTH statement of a ledger row, spelled the way the table holds its
   text. The char lengths are the ones the job gives its ledger. */
%macro _rag_ledger_length;
    %if &_rag_ledgerTypes. EQ char %then %do;
        length doc_id $ 40 source_uri $ 1024 source_kind $ 16
               content_hash $ 64 mtime $ 32 status $ 12 error_text $ 512
               pipeline_version $ 32 config_hash $ 32 chunk_count 8
               run_id $ 32 updated_at $ 24;
    %end;
    %else %do;
        length doc_id varchar(40) source_uri varchar(1024)
               source_kind varchar(16) content_hash varchar(64)
               mtime varchar(32) status varchar(12)
               error_text varchar(512) pipeline_version varchar(32)
               config_hash varchar(32) chunk_count 8
               run_id varchar(32) updated_at varchar(24);
    %end;
%mend _rag_ledger_length;

/* ---- No export of an earlier run -------------------------------------------
   The two files the Python program reads the ledger and its lock from stay
   in WORK. A job started by SAS Job Execution has a WORK of its own; in a SAS
   session that ran this program before, they are the files of that run. A
   run that finds no ledger in memory exports none, and the Python program
   then read the ledger of the EARLIER run as the ledger of this one: its
   documents as unchanged, and its lock as the lock that was written.
   Both files are deleted before anything is exported, so a file that is
   there afterwards is one this run wrote. A file that is there and cannot be
   deleted stops the run, the way a saved ledger that cannot be loaded does. */
data _null_;
    length file $ 32 path $ 1024 ref $ 8 line $ 200;
    do file = "rag_ledger_in.json", "rag_ledger_lock.json";
        path = cats("%sysfunc(pathname(work))/", file);
        if fileexist(path) then do;
            ref = "_ragold";
            rc = filename(ref, path);
            if rc = 0 then rc = fdelete(ref);
            if rc = 0 then do;
                line = "NOTE: RAG ingestion: " || strip(file) || " of an earlier run of this SAS session was deleted from WORK";
                putLog line;
            end;
            else do;
                call symputx("_rag_state", "fail", "G");
                call symputx("_rag_error", strip(file) || " of an earlier run of this SAS session could not be deleted from WORK - stopped, because the run would read the ledger of that run as its own", "G");
            end;
            rc = filename(ref);
        end;
    end;
run;

/* ---- Export the existing ledger to WORK, then take the run lock ----------
   The export happens BEFORE the lock append, so this run's own lock never
   appears in its snapshot - the Python side aborts only on a fresh lock left
   by ANOTHER run (stale locks past 30 minutes are ignored). The final ledger
   store rewrites the table without a lock row, which releases it, and a run
   that failed hands its lock back itself (_rag_release, behind the Python
   program).
   The lock row carries the id of the run, as the lock rows of the RAG steps
   do: that is what tells the row of this run from the row of any other. The
   id is made here, the way RAG - List Documents makes it, and the Python
   program takes it over as the id of the run. A lock row of the job as it
   was before has an empty run_id and is a row of another run to every
   reader.
   A run that finds the fresh lock of another run takes none. The job used to
   append its row first and left it to the Python program to look for the
   other lock: a run that was refused then left a lock of its own behind, and
   so did every run that failed later, because nothing took the row out
   again. The next run was answered that another ingestion run appears
   active, for 30 minutes, with nothing running. The newest lock row decides,
   because a ledger can hold the row of a run that crashed beside the row of
   a run that is at work.
   A lock that failed to write is not a lock. After the append the ledger is
   exported a second time, the same way as the first, and the Python side
   looks in that export for a lock row the first one does not hold. Without
   one it refuses to ingest unguarded, as RAG - List Documents does when it
   cannot read its own lock back: CAS refuses a row whose column types
   differ from the table, and SAS goes on after that ERROR.
   The order is: load a saved ledger back, export it, look for the lock of
   another run, read its column types, append the lock, export again. The
   reload stands first because everything after it asks the in-memory table
   - a ledger that is only on disk would be exported as none, typed as none
   and get no lock. */
%macro _rag_export_ledger;
    %local _rag_now _rag_uuid _rag_digits;
    /* run-<epoch>-<ten digits>: the first eight hexadecimal digits of a
       UUID, written as a number, and &sysjobid where UUIDGEN returns none -
       the lines RAG - List Documents makes the id of a flow run with */
    %let _rag_now = %sysevalf(%sysfunc(datetime()) - 315619200);
    %let _rag_digits = &sysjobid.;
    %let _rag_uuid = %sysfunc(uuidgen());
    %if %length(&_rag_uuid.) GE 8 %then
        %let _rag_digits = %sysfunc(inputn(%substr(&_rag_uuid., 1, 8), hex8.), z10.);
    %let _rag_runId = run-%sysfunc(compress(%sysfunc(putn(&_rag_now., 12.))))-&_rag_digits.;
    cas _ragcas;
    libname _ragcl cas caslib="&ledgerCaslib." sessref=_ragcas;
    %_rag_reload_saved(_ragcas, &ledgerCaslib., &ledgerTable., _rag_state, _rag_error);
    %if %sysfunc(exist(_ragcl.&ledgerTable.)) %then %do;
        filename _ragled "%sysfunc(pathname(work))/rag_ledger_in.json";
        proc json out=_ragled noSASTags;
            export _ragcl.&ledgerTable.;
        run; quit;
        filename _ragled clear;

        /* the newest lock row in the ledger, whoever wrote it */
        data _null_;
            set _ragcl.&ledgerTable.(where=(doc_id = "__run_lock__"));
            retain newest;
            taken = input(mtime, ?? best32.);
            if taken > newest then do;
                newest = taken;
                call symputx("_rag_lockHeld", newest, "G");
            end;
        run;
        %if %length(&_rag_lockHeld.) GT 0 %then %if %sysevalf(&_rag_now. - &_rag_lockHeld. < 1800) %then %do;
            %let _rag_state = fail;
            /* one line: the Python program writes it as the milestone of a
               failed run, and the last line of the log holds it as well */
            %let _rag_error = another ingestion run appears active for this ledger (run lock held) - retry later;
        %end;
    %end;
    %if &_rag_state. EQ run %then %if %sysfunc(exist(_ragcl.&ledgerTable.)) %then %do;
        %_rag_ledger_types(_ragcas, &ledgerCaslib., &ledgerTable., char);
        data _ragcl.&ledgerTable.(append=yes);
            %_rag_ledger_length
            doc_id = '__run_lock__';
            source_uri = '';
            source_kind = '';
            content_hash = '';
            /* unix epoch seconds, so the Python age check is direct */
            mtime = strip(put(datetime() - 315619200, best20.));
            status = 'lock';
            error_text = '';
            pipeline_version = '';
            config_hash = '';
            chunk_count = 0;
            run_id = "&_rag_runId.";
            updated_at = '';
        run;
        %let _rag_lockTaken = 1;
        filename _raglck "%sysfunc(pathname(work))/rag_ledger_lock.json";
        proc json out=_raglck noSASTags;
            export _ragcl.&ledgerTable.;
        run; quit;
        filename _raglck clear;
    %end;
    libname _ragcl clear;
    cas _ragcas terminate;
%mend _rag_export_ledger;
%_rag_export_ledger;

proc python restart;
submit;
def main():
    import io
    import json
    import os
    import sys
    import tempfile
    import time
    import traceback

    def sas_safe(text):
        """Text passed to SAS.logMessage/SAS.symput is embedded into generated
        SAS statements - quotes and macro characters corrupt the code stream
        (verified live by the optimize job). EVERY string crossing that
        boundary goes through here."""
        # Each such character becomes one that reads the same, as in the
        # logging programs: the setup`s window, 85pct, R+D. A blank in its
        # place lost what the message said. The two comment marks are put
        # together from their halves, so that this block holds neither.
        cleaned = str(text)
        for bad, good in (("'", "`"), ('"', "`"), ("%", "pct "), ("&", "+"),
                          (";", ","), ("/" + "*", "/ *"), ("*" + "/", "* /")):
            cleaned = cleaned.replace(bad, good)
        return cleaned

    def M(msg):
        SAS.logMessage("RAGINGEST " + sas_safe(msg))

    MESSAGE_LIMIT = 900
    NOT_FOR_SAS = (("'", '`'), ('"', '`'), ('%', 'pct '), ('&', '+'), (';', ','), ('/' + '*', '/ *'), ('*' + '/', '* /'))

    def sas_text(value, limit=MESSAGE_LIMIT):
        """The escaping function of the logging programs (sas_safe of
        Link-Logs-To-Models.sas, statement for statement), for a text this
        program did not write itself: on one line, without a character that
        is no text, without a quote, a macro trigger, a semicolon or a
        comment mark, not longer than the limit and with its parentheses as
        brackets when they do not pair up. The two comment marks are built
        from their halves, so that this block holds neither."""
        cleaned = ' '.join(str(value).split())
        cleaned = ''.join(character if character.isprintable() else ' ' for character in cleaned)
        for bad, replacement in NOT_FOR_SAS:
            cleaned = cleaned.replace(bad, replacement)
        cleaned = ' '.join(cleaned.split())
        if len(cleaned) > limit:
            cleaned = cleaned[:limit - 3].rstrip() + '...'
        depth = 0
        for character in cleaned:
            depth += 1 if character == '(' else -1 if character == ')' else 0
            if depth < 0:
                break
        if depth != 0:
            cleaned = cleaned.replace('(', '[').replace(')', ']')
        return cleaned

    # The saved ledger could not be loaded. Running on would read an empty
    # ledger, ingest every document as new and store the result over the
    # saved file; _rag_error already says what happened.
    # It is said as the milestone every failure of this program is said as:
    # the Prompt Builder reads the outcome of a run from the milestones, and
    # a run that returned here without one was shown as finished.
    if str(SAS.symget("_rag_state") or "run").strip() != "run":
        M("Ingestion failed: "
          + sas_safe(str(SAS.symget("_rag_error") or "").strip()))
        return

    try:
        import requests
    except Exception:
        missing = ("The Python environment of this compute context lacks the "
                   "requests package - see the RAG administration guide.")
        M("Ingestion failed: " + missing)
        SAS.symput("_rag_error", missing)
        return

    BASE = str(SAS.symget("_rag_viyaHost") or "").rstrip("/")
    TOKEN = os.environ.get("SAS_SERVICES_TOKEN", "")
    AUTH = {"Authorization": "Bearer " + TOKEN}
    VERIFY = os.environ.get("SSLCALISTLOC") or os.environ.get("CAS_CLIENT_SSL_CA_LIST") or True
    WORKPATH = SAS.workpath if SAS.workpath.endswith(os.sep) else SAS.workpath + os.sep

    P = {name: str(SAS.symget(name) or "").strip() for name in [
        "sourcePath", "collection", "backend", "storeHost", "storePort",
        "storeDb", "storeSslmode", "credentialDomain", "scrEndpoint",
        "embedModel", "deploymentType", "inputTokenLimit", "chunker",
        "overlapTokens", "pipelineVersion", "configHash", "ragCorePath",
        "deletedPolicy", "retainDays", "replicas", "recordHistory",
        "includeCode", "enrichSteps", "enrichPromptModel",
        "enrichPromptVersion", "enrichMapping", "enrichHeaderOutput",
        "enrichColumns", "enrichWorkers",
    ]}

    try:
        # ---- rag_core bootstrap: download the governed SAS Content folder --
        def get_json(endpoint, **params):
            response = requests.get(BASE + endpoint, params=params, headers=AUTH,
                                    verify=VERIFY, timeout=(5, 30))
            response.raise_for_status()
            return response.json()

        def download_folder(folder_id, local_dir):
            os.makedirs(local_dir, exist_ok=True)
            count = 0
            members = get_json(f"/folders/folders/{folder_id}/members", limit=200)
            for member in members.get("items", []):
                uri = member.get("uri", "")
                if "/files/files/" in uri:
                    content = requests.get(BASE + uri + "/content", headers=AUTH,
                                           verify=VERIFY, timeout=(5, 60))
                    content.raise_for_status()
                    with open(os.path.join(local_dir, member["name"]), "wb") as fh:
                        fh.write(content.content)
                    count += 1
                elif "/folders/folders/" in uri:
                    count += download_folder(uri.rsplit("/", 1)[-1],
                                             os.path.join(local_dir, member["name"]))
            return count

        root = requests.get(BASE + "/folders/folders/@item",
                            params={"path": P["ragCorePath"]}, headers=AUTH,
                            verify=VERIFY, timeout=(5, 30))
        if root.status_code != 200:
            raise RuntimeError(f"rag_core folder {P['ragCorePath']} not found "
                               f"(HTTP {root.status_code}) - deploy it per the "
                               "administration guide")
        staging = tempfile.mkdtemp(prefix="rag_core_")
        n_files = download_folder(root.json()["id"], os.path.join(staging, "rag_core"))
        sys.path.insert(0, staging)
        import rag_core
        M(f"rag_core {rag_core.RAG_CORE_VERSION} bootstrapped ({n_files} files)")

        from rag_core.adapters import get_adapter
        from rag_core.credentials import fetch_secrets, store_config_from_secrets
        from rag_core.enrich import (PromptModel, attribute_schema,
                                     merge_attribute_schemas, parse_steps,
                                     run_enrich, validate_pipeline,
                                     validate_selection)
        from rag_core.extractors import ExtractorRegistry
        from rag_core.scr import EmbeddingClient
        from rag_core.providers import api_key_for, llm_api_key_for
        from rag_core.pricing import log_cost, log_enrich_cost
        from rag_core.sources import make_source
        from rag_core.steps import (record_history, LEDGER_COLUMNS, config_hash, merge_ledger,
                                    run_chunk, run_embed, run_extract, run_list,
                                    run_load)

        # ---- parameter validation (fails through the standard path) --------
        for required in ("sourcePath", "collection", "storeHost", "storeDb"):
            if not P[required]:
                raise RuntimeError(f"required parameter {required} is missing")
        registry = ExtractorRegistry()
        missing = registry.catalog()["unavailable"]
        if missing:
            M(f"extractors unavailable (packages missing): {missing}")

        # ---- the Enrich stages, when this setup has any ---------------------
        # Loaded here rather than where they are used: a prompt that has been
        # deleted, was manifested for the Call LLM node, or whose inputs the
        # mapping does not match is a five-second failure at this point and a
        # twenty-minute one after the corpus has been crawled and chunked.
        # ALL of them load before ANY runs, for the same reason - a broken
        # second prompt should not be discovered after the first has already
        # been paid for once per chunk.
        specs = parse_steps(P["enrichSteps"])
        if not specs and P["enrichPromptModel"]:
            specs = parse_steps([{"model_id": P["enrichPromptModel"],
                                  "version_id": P["enrichPromptVersion"],
                                  "mapping": P["enrichMapping"],
                                  "header_output": P["enrichHeaderOutput"],
                                  "column_outputs": P["enrichColumns"],
                                  "workers": P["enrichWorkers"]}])
        stages = []
        for position, spec in enumerate(specs):
            loaded = PromptModel.from_model_manager(
                BASE, TOKEN, spec["model_id"],
                version_id=spec["version_id"], verify=VERIFY)
            problems = validate_selection(loaded, spec["mapping"],
                                          spec["header_output"],
                                          spec["column_outputs"])
            if problems:
                raise RuntimeError(f"enrichment step {position + 1} cannot use "
                                   + (loaded.name or spec["model_id"])
                                   + ": " + "; ".join(problems))
            stages.append(dict(spec, prompt=loaded))
            M(f"enrich step {position + 1} of {len(specs)}: " + loaded.describe()
              + (" [pinned]" if spec["version_id"] else " [latest]"))
        # and the problems that only exist BETWEEN steps: two prompts writing
        # the context header, or both claiming the same column
        problems = validate_pipeline(stages)
        if problems:
            raise RuntimeError("these enrichment steps cannot run together: "
                               + "; ".join(problems))

        def enrich_total(key):
            """One measurement summed over every prompt this run called."""
            return sum(float(stage["prompt"].usage.get(key) or 0)
                       for stage in stages)

        # Enrichment is deliberately NOT part of the fingerprint (owner
        # decision 2026-08-03). Changing the prompt applies from the next run
        # onward instead of demanding a full re-ingest: a corpus may hold
        # headers from two prompt versions, and every enriched chunk records
        # which one wrote it (enrich_version) so the mix is visible rather
        # than merely true. The guard still covers what would make the
        # collection unreadable - the chunker, the window and the embedding
        # model, which change the vectors themselves.
        # The keys are rag_core's canonical ones, not this job's parameter
        # names: config_hash canonicalises what it is given, and a key it does
        # not recognise is a value that silently does not count. The Studio
        # flow reaches the same dict a step at a time, which is the point -
        # both paths write this fingerprint to the SAME ledger.
        cfg_hash = P["configHash"] or config_hash({
            "backend": P["backend"], "collection": P["collection"],
            "chunker": P["chunker"],
            "input_token_limit": P["inputTokenLimit"],
            "overlap_tokens": P["overlapTokens"],
            "embed_model": P["embedModel"],
            "pipeline_version": P["pipelineVersion"],
        })

        # ---- previous ledger + drift guard + run lock ----------------------
        ledger = []
        ledger_path = WORKPATH + "rag_ledger_in.json"
        if os.path.exists(ledger_path):
            with open(ledger_path, "r", encoding="utf-8") as fh:
                rows = json.load(fh)
            for row in rows if isinstance(rows, list) else []:
                lowered = {str(k).lower(): v for k, v in row.items()}
                ledger.append({column: lowered.get(column.lower(), "")
                               for column in LEDGER_COLUMNS})
        real_rows = [r for r in ledger if r.get("doc_id") != "__run_lock__"]
        lock_rows = [r for r in ledger if r.get("doc_id") == "__run_lock__"]
        last_hashes = {r.get("config_hash") for r in real_rows if r.get("config_hash")}
        if last_hashes and cfg_hash not in last_hashes:
            raise RuntimeError(
                "configuration drift: this run's parameters differ from the "
                "ledger's last ingestion (config hash mismatch). Re-register "
                "the setup or bump pipelineVersion for a full re-embed.")
        if lock_rows:
            # The newest lock decides: the row of a run that crashed can
            # stand beside the row of a run that is at work, and the first
            # row of the export is whichever CAS returned first. SAS has
            # looked the same way before it appended the lock of this run,
            # and a run it refused never gets here.
            held_for = 1e9
            for held in lock_rows:
                try:
                    held_for = min(held_for,
                                   time.time() - float(held.get("mtime") or 0))
                except Exception:
                    pass
            if held_for < 1800:
                raise RuntimeError("another ingestion run appears active for "
                                   "this ledger (run lock held) - retry later")
            M("stale run lock ignored (held > 30 minutes)")
        # ---- the lock of THIS run, read back -------------------------------
        # SAS appended the lock after the export read above and exported the
        # ledger a second time. A lock row the second export holds and the
        # first does not is the lock of this run. Where the ledger did not
        # exist there is no first export, and no lock was taken. The check
        # sees only that the row is not there: the append was refused, the
        # second export failed, or another writer replaced the table between
        # the two - the message names where to look, not one cause.
        if os.path.exists(ledger_path):
            written = []
            lock_path = WORKPATH + "rag_ledger_lock.json"
            if os.path.exists(lock_path):
                with open(lock_path, "r", encoding="utf-8") as fh:
                    rows = json.load(fh)
                for row in rows if isinstance(rows, list) else []:
                    lowered = {str(k).lower(): v for k, v in row.items()}
                    if str(lowered.get("doc_id") or "").strip() == "__run_lock__":
                        written.append(str(lowered.get("mtime") or "").strip())
            for held in lock_rows:
                seen = str(held.get("mtime") or "").strip()
                if seen in written:
                    written.remove(seen)
            if not written:
                raise RuntimeError(
                    "the run lock could not be written to the ledger "
                    + str(SAS.symget("ledgerCaslib") or "").strip() + "."
                    + str(SAS.symget("ledgerTable") or "").strip()
                    + " - refusing to ingest unguarded. The lock row of "
                      "this run was not in the ledger when it was read "
                      "back: look in the SAS log above for an ERROR of the "
                      "append, or of the export after it")
        # run-<epoch>-<digits>: the epoch alone collided when two projects
        # sharing a database started in the same second. The digits were the
        # process id, and that is nearly the same number in every compute
        # server (400 to 408 in the runs measured live), so they are 32
        # bits that differ from run to run, written as ten digits like the
        # run id of a flow.
        # The id is the one SAS made before it appended the run lock, from
        # a UUID, so that the lock row carries it: the ledger then says
        # whose lock it holds, and a run that fails takes out its own row
        # and no other. Only where SAS handed none over is the id made
        # here, from 32 random bits.
        run_id = str(SAS.symget("_rag_runId") or "").strip()
        if not run_id:
            run_id = (f"run-{int(time.time())}-"
                      f"{int.from_bytes(os.urandom(4), 'big'):010d}")
        M(f"run {run_id}: ledger has {len(real_rows)} documents, config {cfg_hash}")

        # ---- connections ----------------------------------------------------
        secrets = fetch_secrets(BASE, TOKEN, P["credentialDomain"], verify=VERIFY)
        if not secrets:
            raise RuntimeError(f"no credential resolved from domain "
                               f"{P['credentialDomain']} for this user - see "
                               "the Managing Credentials administration guide")
        store_config = store_config_from_secrets(
            secrets, P["backend"], P["storeHost"], P["storePort"],
            P["storeDb"], P["storeSslmode"])
        adapter = get_adapter(P["backend"])
        adapter.connect(store_config)
        # a model that forwards to a hosted API needs that provider's key,
        # out of the same credential domain the store credentials came from
        client = EmbeddingClient(
            P["scrEndpoint"] or BASE + "/llm", P["embedModel"],
            deployment_type=P["deploymentType"] or "k8s", verify_ssl=VERIFY,
            api_key=api_key_for(P["embedModel"], secrets))
        dims = client.smoke()
        M(f"connected: {P['backend']} + {P['embedModel']} ({dims} dims)")
        # An enrichment prompt that calls a hosted LLM needs that provider's
        # key out of the same domain; one served by a local container takes no
        # API_KEY parameter at all, and its signature is what says which.
        # Each step is asked separately: two prompts may call two providers.
        for stage in stages:
            stage["api_key"] = (llm_api_key_for(stage["prompt"].llm, secrets)
                                if stage["prompt"].needs_api_key else "")

        # ---- the pipeline ---------------------------------------------------
        # sourcePath may name the compute file system or SAS Content -
        # make_source reads the sasserver:/sascontent: prefix a SAS Studio
        # path selector emits, and a bare path stays a filesystem path. The
        # same source object is handed to the extract step, because a SAS
        # Content document is fetched over the Files API rather than opened.
        source = make_source(P["sourcePath"], BASE, TOKEN, VERIFY)
        M(f"source: {source.describe()}")
        inventory = run_list(source, real_rows, run_id,
                             P["pipelineVersion"], cfg_hash,
                             include_code=(P["includeCode"] == "1"), log=M)
        elements, inventory = run_extract(inventory, registry, source=source,
                                          log=M)
        chunks = run_chunk(elements, inventory, P["chunker"] or "recursive",
                           int(float(P["inputTokenLimit"] or 256)),
                           P["pipelineVersion"],
                           overlap_tokens=int(float(P["overlapTokens"] or 30)), log=M)
        # The Enrich stage sits here, between chunking and embedding, because
        # the header it writes is embedded WITH the chunk - after this point
        # the text is already a vector and nothing can be added to it.
        # One stage after the other, each seeing what the last one wrote.
        enrich_failures = []
        for stage in stages:
            chunks, failures = run_enrich(
                chunks, stage["prompt"], stage["mapping"],
                header_output=stage["header_output"],
                column_outputs=stage["column_outputs"],
                api_key=stage["api_key"],
                max_workers=stage["workers"], log=M)
            enrich_failures.extend(failures)
            # what those calls cost, in the same place the embedding cost is
            log_enrich_cost(stage["prompt"].llm, stage["prompt"].usage, log=M)
        # four parallel calls per replica, as the Embed step sizes it
        workers = max(1, int(float(P["replicas"] or 1)) * 4)
        embedded, embed_failures = run_embed(chunks, client,
                                             max_workers=workers, log=M)
        # what those calls cost, while the run is still on screen
        log_cost(P["embedModel"], client.usage, log=M)
        discovered = [dict(row) for row in inventory]   # before run_load
        load_stats = {}
        # run_id/config_id/embed_model stamp the lineage on every chunk row
        # and on every tombstone. Without them the job wrote blanks: the
        # retrieval datagrid could not say which run produced a chunk, and
        # restore(collection, "") - the documented rollback after a bad run -
        # matched every chunk the job had ever written, all at once.
        inventory = run_load(embedded, inventory, adapter, P["collection"], dims,
                             P["pipelineVersion"],
                             run_id=run_id, config_id=cfg_hash,
                             embed_model=P["embedModel"], embed_dims=dims,
                             deleted_policy=P["deletedPolicy"] or "retire",
                             retain_days=int(float(P["retainDays"] or 0)),
                             attributes=merge_attribute_schemas(
                                 [attribute_schema(stage["prompt"],
                                                   stage["column_outputs"])
                                  for stage in stages]) or None,
                             stats=load_stats, log=M)
        new_ledger = merge_ledger(real_rows, inventory)
        for row in new_ledger:
            row["config_hash"] = cfg_hash
        total = adapter.count(P["collection"])
        if P["recordHistory"] != "0":
            record_history(
                adapter, inventory, real_rows, run_id, P["collection"],
                config_id=cfg_hash, discovery=discovered,
                settings={"chunker": P["chunker"] or "recursive",
                          "input_token_limit": int(float(P["inputTokenLimit"] or 256)),
                          "overlap_tokens": int(float(P["overlapTokens"] or 30)),
                          "pipeline_version": P["pipelineVersion"],
                          "embed_model": P["embedModel"],
                          # recorded, not hashed: which prompt a run used is
                          # history, and no longer part of what a chunk IS
                          **({"enrich_prompt": ",".join(
                                  stage["prompt"].stamp for stage in stages),
                              "enrich_header": next(
                                  (stage["header_output"] for stage in stages
                                   if stage["header_output"]), ""),
                              "enrich_columns": ",".join(
                                  name for stage in stages
                                  for name in stage["column_outputs"])}
                             if stages else {})},
                metrics={"backend": P["backend"], "collection_chunks": total,
                         "embed_dims": dims, **load_stats,
                         "embed_calls": int(client.usage.get("calls") or 0),
                         "embed_tokens": int(client.usage.get("tokens") or 0),
                         "embed_seconds": float(client.usage.get("run_time") or 0),
                         **({"enrich_model": ",".join(dict.fromkeys(
                                 stage["prompt"].llm for stage in stages
                                 if stage["prompt"].llm)),
                             "enrich_calls": int(enrich_total("calls")),
                             "enrich_input_tokens": int(enrich_total("input_tokens")),
                             "enrich_output_tokens": int(enrich_total("output_tokens")),
                             "enrich_seconds": enrich_total("run_time"),
                             "enrich_failed": int(enrich_total("failed"))}
                            if stages else {})},
                log=M)
            # ---- the run history, read back for CAS -------------------------
            # The store holds the history. <prefix>_RUNS and
            # <prefix>_DOC_EVENTS in CAS are the copy a report reads, and the
            # step RAG - Load Vector Store was the only writer of them: a
            # setup this job ingested had none, and nothing in CAS said which
            # run had embedded a document.
            # What is read is what the step publishes - every run the store
            # holds for the collection (the newest 500) and their document
            # events (the newest 5000), not the row of this run alone - so
            # that one run brings the runs before it into CAS, also a run
            # that found nothing to do. The rows are read here, over the
            # connection the run still holds, and written to WORK as the
            # files SAS uploads behind this program, once the ledger is
            # stored. A column name and a label reach SAS inside a statement:
            # a name that is no plain name stops the hand-over, a label that
            # is not plain text is left out.
            # Never fails the run, as recording the history does not: the
            # reason is handed over and SAS writes it as a WARNING.
            try:
                from rag_core.history import (EVENT_COLUMNS, EVENT_NUMERIC,
                                              RUN_COLUMNS, RUN_NUMERIC,
                                              History, cas_types,
                                              write_cas_file)
                from rag_core.steps import column_labels
                reader = History(adapter.raw_connection(),
                                 getattr(adapter, "HISTORY_DIALECT", "postgres"))
                handed = []
                for kind, filename, rows, columns, numeric in (
                        ("Run", "rag_runs_out.csv",
                         reader.runs(P["collection"]),
                         RUN_COLUMNS, RUN_NUMERIC),
                        ("Event", "rag_doc_events_out.csv",
                         reader.doc_events(P["collection"]),
                         EVENT_COLUMNS, EVENT_NUMERIC)):
                    for column in columns:
                        if not (column.isascii() and column.isidentifier()):
                            raise RuntimeError(
                                f"the history column {column} is no name a "
                                "SAS statement can hold")
                    count = write_cas_file(WORKPATH + filename,
                                           [dict(row) for row in rows],
                                           columns, numeric)
                    labels = "|".join(
                        f"{column}={label}"
                        for column, label in column_labels(columns).items()
                        if label.strip() and all(
                            ch.isascii() and (ch.isalnum() or ch in " _.-")
                            for ch in label))
                    handed.append((kind, count, " ".join(
                        f"{column}:{kind_of}"
                        for column, kind_of in cas_types(columns, numeric)),
                        labels))
                for kind, count, variables, labels in handed:
                    SAS.symput(f"_rag_hist{kind}s", str(count))
                    SAS.symput(f"_rag_hist{kind}Vars", variables)
                    if labels:
                        SAS.symput(f"_rag_hist{kind}Labels", labels)
                SAS.symput("_rag_histState", "read")
                M(f"rag history: read for CAS, runs {handed[0][1]} and "
                  f"document events {handed[1][1]} of the collection")
            except Exception as exc:
                reason = sas_text(f"{type(exc).__name__}: {exc}", 200)
                SAS.symput("_rag_histState", "failed")
                SAS.symput("_rag_histError", reason or "no reason given")
                M(f"rag history: not read for CAS ({reason}) - the "
                  "ingestion itself is unaffected")
        adapter.close()

        # ---- hand the new ledger back to SAS -------------------------------
        with open(WORKPATH + "rag_ledger_out.json", "w", encoding="utf-8") as fh:
            json.dump([{column: row.get(column, "") for column in LEDGER_COLUMNS}
                       for row in new_ledger], fh)
        statuses = {}
        for row in new_ledger:
            statuses[row["status"]] = statuses.get(row["status"], 0) + 1
        summary = (f"collection {P['collection']}: {total} chunks; documents "
                   + ", ".join(f"{k}={v}" for k, v in sorted(statuses.items()))
                   + (f"; embed failures {len(embed_failures)}" if embed_failures else "")
                   # named in the summary, not just the detail: chunks that
                   # went in without a header retrieve differently from the
                   # ones that did not, and that is a property of the corpus
                   + (f"; {len(enrich_failures)} chunks embedded without a "
                      "context header" if enrich_failures else ""))
        M(summary)
        SAS.symput("_rag_summary", sas_safe(summary)[:400])
        SAS.symput("_rag_rc", "0")
    except Exception as error:
        error_text = f"{type(error).__name__}: {error}"
        M("Ingestion failed: " + sas_safe(error_text))
        print(traceback.format_exc())
        SAS.symput("_rag_error", sas_safe(error_text)[:500])


main()
endsubmit;
run; quit;
proc python terminate;
run; quit;

/* ---- Persist the updated ledger (promote AND save - survives restarts) ----
   Only a run that went through stores a ledger. rag_ledger_out.json stays in
   WORK, so in a SAS session that ran the job before, a later run that
   stopped early - at a saved ledger that could not be loaded, at the lock of
   another run - found the file of the EARLIER run here and stored that
   ledger again: over the saved file, and without the lock of the other run.
   The Python program writes the file and then reports success, so a run
   that reports success always stores its own. */
%macro _rag_store_ledger;
    %if &_rag_rc. NE 0 %then %return;
    %if %sysfunc(fileexist(%sysfunc(pathname(work))/rag_ledger_out.json)) %then %do;
        filename _ragout "%sysfunc(pathname(work))/rag_ledger_out.json";
        libname _ragjson json fileref=_ragout;
        data work._rag_ledger_new;
            length doc_id $ 40 source_uri $ 1024 source_kind $ 16
                   content_hash $ 64 mtime $ 32 status $ 12 error_text $ 512
                   pipeline_version $ 32 config_hash $ 32 chunk_count 8
                   run_id $ 32 updated_at $ 24;
            set _ragjson.root(drop=ordinal_root);
        run;
        libname _ragjson clear;
        filename _ragout clear;

        cas _ragcas2;
        proc casutil sessref=_ragcas2 incaslib="&ledgerCaslib." outcaslib="&ledgerCaslib.";
            droptable casdata="&ledgerTable." quiet;
            load data=work._rag_ledger_new casout="&ledgerTable.";
            promote casdata="&ledgerTable." casout="&ledgerTable.";
            save casdata="&ledgerTable." casout="&ledgerTable." replace;
        quit;
        cas _ragcas2 terminate;
    %end;
%mend _rag_store_ledger;
%_rag_store_ledger;

/* ---- A run that failed hands its lock back ---------------------------------
   A run that went through has stored a ledger without lock rows. A run that
   failed stores nothing, and the lock row it appended stayed in the ledger:
   every run of the next 30 minutes was answered that another ingestion run
   appears active, with nothing running - and since a failed run is shown as
   failed, the retry is what follows it.
   The run takes out its own row and no other, the way the RAG steps do: the
   ledger is copied without the lock row that carries the id of this run,
   dropped, and the copy promoted and saved. The copy declares nothing, so
   the ledger keeps the column types it had.
   Nothing is looked for when this run appended no row - a saved ledger that
   could not be loaded, the fresh lock of another run, a first run without a
   ledger - and nothing is rewritten when the row is not there: the append
   was refused, or another writer has replaced the table since. The lock of
   another run, and a lock row of the job as it was before (its run_id is
   empty), are never the row asked for. */
%macro _rag_release;
    %local held;
    %if &_rag_rc. EQ 0 %then %return;
    %if &_rag_lockTaken. NE 1 %then %return;
    %if %length(%superq(_rag_runId)) EQ 0 %then %return;
    cas _ragcas3;
    libname _ragcl cas caslib="&ledgerCaslib." sessref=_ragcas3;
    %if %sysfunc(exist(_ragcl.&ledgerTable.)) %then %do;
        %let held = ;
        data _null_;
            set _ragcl.&ledgerTable.(where=(doc_id = "__run_lock__" and run_id = "&_rag_runId."));
            call symputx("held", run_id, "L");
        run;
        %if %length(&held.) GT 0 %then %do;
            data _ragcl.&ledgerTable._R(replace=yes);
                set _ragcl.&ledgerTable.(where=(not (doc_id = "__run_lock__"
                                         and run_id = "&_rag_runId.")));
            run;
            proc casutil sessref=_ragcas3 incaslib="&ledgerCaslib." outcaslib="&ledgerCaslib.";
                droptable casdata="&ledgerTable." quiet;
                promote casdata="&ledgerTable._R" casout="&ledgerTable.";
                save casdata="&ledgerTable." casout="&ledgerTable." replace;
            quit;
            data _null_;
                putLog "NOTE: RAG ingestion: the run lock was released so the run can be retried";
            run;
        %end;
    %end;
    libname _ragcl clear;
    cas _ragcas3 terminate;
%mend _rag_release;
%_rag_release;

/* ---- The run history, published to CAS -------------------------------------
   The run history is kept in the vector store (rag_runs, rag_doc_events).
   The step RAG - Load Vector Store publishes it to CAS, as <project>_RUNS
   and <project>_DOC_EVENTS beside the ledger, and was the only one that
   did: a setup this job ingested had a ledger in CAS and no run table, so a
   report had no runs for it and nothing in CAS said which run had embedded
   a document.
   The job publishes the same two tables, under the same names, with the
   same columns and types. <project> is the ledger table without its ending
   _LEDGER - the Prompt Builder names the ledger <prefix>_LEDGER - and the
   whole name of a ledger that does not end that way.
   What is published is what the Python program read from the store before
   the run stored its ledger, so a run that took a lock still held it: every
   run the store holds for the collection and their document events, as the
   step does it, so that one run brings the runs before it into CAS. Each
   table is uploaded beside the one in CAS with the options the step names
   and the locale SWAT adds to them, gets the labels of its columns, and
   takes the place of the table: the table is dropped, the upload promoted
   under its name and saved. An upload that does not hold every row that
   was written takes the place of no table.
   Two things the step does are not done here. It drops a table the store
   holds no rows for and deletes its saved file, and does the same to both
   tables when the history is switched off; the job leaves such a table as
   it is.
   The tables are written behind the ledger store and the release of a
   failed run, so the run is over when they are: its ledger is stored and
   its lock is gone, whatever happens here. A table that could not be
   published is a WARNING and never a failed run - the store keeps the
   history, and the next run publishes all of it again. The condition code
   of the session is put back for that reason, as the reload of a saved
   table does it: what CAS answered here does not decide the state of the
   job. And an error here must not leave a batch session checking syntax
   only, which is what it does behind a step that ended in an error: the
   last line of the log is written by a DATA step behind this macro. The
   option that decides it is switched off while the tables are written and
   put back to what it was. The lines of the publication are %put
   statements, which are written whatever state the session is in.
   One table: kind is Run or Event, the middle of the names the Python
   program handed its rows, columns and labels over under. */
%macro _rag_history_table(kind, suffix, file);
    %local _rag_ht _rag_hrows _rag_hvars _rag_hlabels _rag_hi _rag_hn _rag_hv
           _rag_hl _rag_houtcome _rag_hstaged;
    %let _rag_ht = &_rag_histPrefix.&suffix.;
    %let _rag_hrows = &&_rag_hist&kind.s;
    %let _rag_hvars = &&_rag_hist&kind.Vars;
    %let _rag_hlabels = &&_rag_hist&kind.Labels;
    %if %length(&_rag_hrows.) EQ 0 %then %let _rag_hrows = 0;
    %if &_rag_hrows. LE 0 %then %do;
        %put NOTE: RAG ingestion: the vector store holds no rows of this collection for &ledgerCaslib..&_rag_ht., the table was left as it is.;
        %return;
    %end;
    %let _rag_houtcome = unknown;
    %let _rag_hstaged = -1;
    %if %length(%superq(_rag_hvars)) GT 0 %then %do;
        %let _rag_hn = %sysfunc(countw(&_rag_hvars., %str( )));
        proc cas;
            session _ragcas4;
            outcome = "upload";
            staged = -1;
            upload path = "%sysfunc(pathname(work))/&file."
                casOut = {caslib = "&ledgerCaslib.", name = "&_rag_ht._S", replace = true}
                importOptions = {fileType = "csv", allowEmbeddedNewLines = true,
                                 locale = "EN-us", vars = {
                    %do _rag_hi = 1 %to &_rag_hn.;
                        %let _rag_hv = %scan(&_rag_hvars., &_rag_hi., %str( ));
                        %if &_rag_hi. GT 1 %then %str(,);
                        {name = "%scan(&_rag_hv., 1, %str(:))", type = "%scan(&_rag_hv., 2, %str(:))"}
                    %end;
                }};
            /* the rows the upload holds, against the rows that were written */
            table.tableExists result = found / caslib = "&ledgerCaslib." name = "&_rag_ht._S";
            if found.exists != 0 then do;
                staged = 0;
                table.tableInfo result = listing / caslib = "&ledgerCaslib.";
                if exists(listing, "TableInfo") then do;
                    do entry over listing.TableInfo;
                        if upcase(entry.Name) == upcase("&_rag_ht._S") then staged = entry.Rows;
                    end;
                end;
                outcome = "rows";
            end;
            if staged == &_rag_hrows. then do;
                outcome = "replace";
                %if %length(%superq(_rag_hlabels)) GT 0 %then %do;
                    table.alterTable status = labelled / caslib = "&ledgerCaslib." name = "&_rag_ht._S"
                        columns = {
                        %do _rag_hi = 1 %to %sysfunc(countw(&_rag_hlabels., %str(|)));
                            %let _rag_hl = %scan(&_rag_hlabels., &_rag_hi., %str(|));
                            %if &_rag_hi. GT 1 %then %str(,);
                            {name = "%scan(&_rag_hl., 1, %str(=))", label = "%scan(&_rag_hl., 2, %str(=))"}
                        %end;
                        };
                %end;
                table.dropTable status = dropped / caslib = "&ledgerCaslib." name = "&_rag_ht." quiet = true;
                table.promote status = promoted / caslib = "&ledgerCaslib." name = "&_rag_ht._S"
                    targetLib = "&ledgerCaslib." target = "&_rag_ht.";
                if promoted.severity == 0 then do;
                    outcome = "save";
                    table.save status = stored / table = {caslib = "&ledgerCaslib.", name = "&_rag_ht."}
                        caslib = "&ledgerCaslib." name = "&_rag_ht." replace = true;
                    if stored.severity == 0 then outcome = "published";
                end;
            end;
            /* "L": local to this macro, as in the reload of a saved table */
            call symputx("_rag_houtcome", outcome, "L");
            call symputx("_rag_hstaged", staged, "L");
        quit;
    %end;
    %if &_rag_houtcome. EQ published %then %do;
        %if %length(%superq(_rag_histDone)) GT 0 %then %let _rag_histDone = &_rag_histDone. and;
        %let _rag_histDone = &_rag_histDone. &ledgerCaslib..&_rag_ht. with &_rag_hrows. rows;
    %end;
    %else %if &_rag_houtcome. EQ upload %then
        %put WARNING: RAG ingestion: the run history table &ledgerCaslib..&_rag_ht. was not published - its &_rag_hrows. rows could not be uploaded to CAS (messages above). The table was left as it is, the ingestion itself went through and the next run publishes the history again.;
    %else %if &_rag_houtcome. EQ rows %then
        %put WARNING: RAG ingestion: the run history table &ledgerCaslib..&_rag_ht. was not published - the upload holds &_rag_hstaged. rows where the vector store gave &_rag_hrows. (messages above). The table was left as it is, the ingestion itself went through and the next run publishes the history again.;
    %else %if &_rag_houtcome. EQ replace %then
        %put WARNING: RAG ingestion: the run history table &ledgerCaslib..&_rag_ht. was not published - the upload could not take the place of the table (messages above). It holds what it held before or is no longer in memory, the ingestion itself went through and the next run publishes the history again.;
    %else %if &_rag_houtcome. EQ save %then
        %put WARNING: RAG ingestion: the run history table &ledgerCaslib..&_rag_ht. holds its &_rag_hrows. rows in memory and could not be saved (messages above). The ingestion itself went through and the next run saves the history again.;
    %else
        %put WARNING: RAG ingestion: the run history table &ledgerCaslib..&_rag_ht. may not be published - nothing said what became of its upload (messages above). The ingestion itself went through and the next run publishes the history again.;
%mend _rag_history_table;

%macro _rag_publish_history;
    %local _rag_histPrefix _rag_histDone _rag_hist_syscc _rag_hist_check;
    /* a run that failed publishes nothing, as it stores no ledger; off: the
       history is switched off, or the run never got as far as reading it */
    %if &_rag_rc. NE 0 %then %return;
    %if &_rag_histState. EQ off %then %return;
    %let _rag_histPrefix = &ledgerTable.;
    %if %length(&ledgerTable.) GT 7 %then
    %if %upcase(%substr(&ledgerTable., %eval(%length(&ledgerTable.) - 6))) EQ _LEDGER %then
        %let _rag_histPrefix = %substr(&ledgerTable., 1, %eval(%length(&ledgerTable.) - 7));
    %if &_rag_histState. NE read %then %do;
        %put WARNING: RAG ingestion: the run history was not published to CAS - it could not be read from the vector store (%superq(_rag_histError)). &ledgerCaslib..&_rag_histPrefix._RUNS and &ledgerCaslib..&_rag_histPrefix._DOC_EVENTS were left as they are, the ingestion itself went through and the next run publishes the history again.;
        %return;
    %end;
    %let _rag_hist_syscc = &syscc.;
    %let _rag_hist_check = %sysfunc(getoption(syntaxcheck));
    options nosyntaxcheck;
    cas _ragcas4;
    %_rag_history_table(Run, _RUNS, rag_runs_out.csv);
    %_rag_history_table(Event, _DOC_EVENTS, rag_doc_events_out.csv);
    cas _ragcas4 terminate;
    options &_rag_hist_check.;
    %let syscc = &_rag_hist_syscc.;
    %if %length(%superq(_rag_histDone)) GT 0 %then
        %put NOTE: RAG ingestion: the run history was published to CAS, promoted and saved - &_rag_histDone..;
%mend _rag_publish_history;
%_rag_publish_history;

/* ---- Propagate the outcome without ever raising a SAS error ---------------
   The message and the summary are read with symget and the line is put
   together in a variable. Written into the program as a quoted string, a
   text that took the line beyond 262 bytes made SAS write the NOTE that
   suspects unbalanced quotation marks above it - and the message of a run
   lock that was not read back is that long with any table name. 2000 bytes
   hold the 500 characters the Python program hands over at most. */
data _null_;
    length text $ 2000 line $ 2100;
    if "&_rag_rc." ne "0" then do;
        text = symget("_rag_error");
        line = "ERROR: RAG ingestion failed: " || text;
    end;
    else do;
        text = symget("_rag_summary");
        line = "NOTE: RAG ingestion succeeded: " || text;
    end;
    putLog line;
run;

/* ---- A run that failed is a failed job -------------------------------------
   Nothing above raises a SAS error, so the program ends normally also when
   the run failed - which SAS Job Execution reports as a completed job. Who
   reads only the state of the job, a schedule for one, then takes the run
   for one that went through, and so does the Prompt Builder for a run that
   left no milestone. SYSCC is what makes it a failed job, set here as the
   last statement of the program: nothing follows that it could stop.
   This is also where a saved ledger that could not be loaded fails the job.
   The reload restores SYSCC after it has looked, because a load that another
   session won is no failure of the run - so what PROC CAS left behind does
   not decide the state of the job, and _rag_rc does.
   A macro, because the test is a conditional and has to hold in any SAS
   release the job lands on. */
%macro _rag_job_state;
    %if &_rag_rc. NE 0 %then %let SYSCC = 1012;
%mend _rag_job_state;
%_rag_job_state;