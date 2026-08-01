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
 *   inputTokenLimit  - the embedding model's token window (default 256)
 *   chunker          - recursive (default) or paragraph
 *   overlapTokens    - chunk overlap for the recursive chunker (default 30)
 *   pipelineVersion  - bump to force a full re-embed (default v1)
 *   configHash       - config fingerprint stamped into the ledger; a run
 *                      whose value differs from the ledger's last run fails
 *                      fast (drift guard). Blank = computed from parameters
 *   ledgerCaslib/ledgerTable - the incremental ledger location
 *                      (default casuser.RAG_INGESTION_LEDGER)
 *   ragCorePath      - SAS Content folder holding rag_core
 *
 * Contract: the job ALWAYS completes. The outcome lives in the log summary
 * (NOTE/ERROR) and the ledger; a raised SAS error would leave the session
 * stopped without Job Execution ever receiving its completion handshake -
 * the same lesson the optimize job learned live.
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
%_rag_default(deletedPolicy, retire);   /* retire (keep history) | purge */
%_rag_default(retainDays, 0);           /* 0 = keep retired chunks forever */
%_rag_default(replicas, 1);             /* embedding container replicas */
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

/* ---- Export the existing ledger to WORK, then take the run lock ----------
   The export happens BEFORE the lock append, so this run's own lock never
   appears in its snapshot - the Python side aborts only on a fresh lock left
   by ANOTHER run (stale locks past 30 minutes are ignored; a failed run
   leaves its lock behind and times out the same way). The final ledger store
   rewrites the table without a lock row, which releases it. */
%macro _rag_export_ledger;
    cas _ragcas;
    libname _ragcl cas caslib="&ledgerCaslib." sessref=_ragcas;
    %if %sysfunc(exist(_ragcl.&ledgerTable.)) %then %do;
        filename _ragled "%sysfunc(pathname(work))/rag_ledger_in.json";
        proc json out=_ragled noSASTags;
            export _ragcl.&ledgerTable.;
        run; quit;
        filename _ragled clear;

        data _ragcl.&ledgerTable.(append=yes);
            length doc_id $ 40 source_uri $ 1024 source_kind $ 16
                   content_hash $ 64 mtime $ 32 status $ 12 error_text $ 512
                   pipeline_version $ 32 config_hash $ 32 chunk_count 8
                   run_id $ 32 updated_at $ 24;
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
            run_id = '';
            updated_at = '';
        run;
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
        cleaned = str(text)
        for ch in ("'", '"', "%", "&", ";"):
            cleaned = cleaned.replace(ch, " ")
        return cleaned

    def M(msg):
        SAS.logMessage("RAGINGEST " + sas_safe(msg))

    try:
        import requests
    except Exception:
        SAS.symput("_rag_error", "The Python environment of this compute context "
                                 "lacks the requests package - see the RAG "
                                 "administration guide.")
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
        from rag_core.extractors import ExtractorRegistry
        from rag_core.scr import EmbeddingClient
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

        cfg_hash = P["configHash"] or config_hash({
            "backend": P["backend"], "collection": P["collection"],
            "chunker": P["chunker"], "tokens": P["inputTokenLimit"],
            "overlap": P["overlapTokens"], "embedModel": P["embedModel"],
            "pipelineVersion": P["pipelineVersion"],
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
            try:
                held_for = time.time() - float(lock_rows[0].get("mtime") or 0)
            except Exception:
                held_for = 1e9
            if held_for < 1800:
                raise RuntimeError("another ingestion run appears active for "
                                   "this ledger (run lock held) - retry later")
            M("stale run lock ignored (held > 30 minutes)")
        # run-<epoch>-<pid>: the epoch alone collided when two projects
        # sharing a database started in the same second
        run_id = f"run-{int(time.time())}-{os.getpid()}"
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
        client = EmbeddingClient(
            P["scrEndpoint"] or BASE + "/llm", P["embedModel"],
            deployment_type=P["deploymentType"] or "k8s", verify_ssl=VERIFY)
        dims = client.smoke()
        M(f"connected: {P['backend']} + {P['embedModel']} ({dims} dims)")

        # ---- the pipeline ---------------------------------------------------
        # sourcePath may name the compute file system or SAS Content -
        # make_source reads the sasserver:/sascontent: prefix a SAS Studio
        # path selector emits, and a bare path stays a filesystem path. The
        # same source object is handed to the extract step, because a SAS
        # Content document is fetched over the Files API rather than opened.
        source = make_source(P["sourcePath"], BASE, TOKEN, VERIFY)
        M(f"source: {source.describe()}")
        inventory = run_list(source, real_rows, run_id,
                             P["pipelineVersion"], cfg_hash, log=M)
        elements, inventory = run_extract(inventory, registry, source=source,
                                          log=M)
        chunks = run_chunk(elements, inventory, P["chunker"] or "recursive",
                           int(float(P["inputTokenLimit"] or 256)),
                           P["pipelineVersion"],
                           overlap_tokens=int(float(P["overlapTokens"] or 30)), log=M)
        # four parallel calls per replica, as the Embed step sizes it
        workers = max(1, int(float(P["replicas"] or 1)) * 4)
        embedded, embed_failures = run_embed(chunks, client,
                                             max_workers=workers, log=M)
        discovered = [dict(row) for row in inventory]   # before run_load
        load_stats = {}
        inventory = run_load(embedded, inventory, adapter, P["collection"], dims,
                             P["pipelineVersion"],
                             deleted_policy=P["deletedPolicy"] or "retire",
                             retain_days=int(float(P["retainDays"] or 0)),
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
                          "embed_model": P["embedModel"]},
                metrics={"backend": P["backend"], "collection_chunks": total,
                         "embed_dims": dims, **load_stats,
                         "embed_calls": int(client.usage.get("calls") or 0),
                         "embed_tokens": int(client.usage.get("tokens") or 0),
                         "embed_seconds": float(client.usage.get("run_time") or 0)},
                log=M)
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
                   + (f"; embed failures {len(embed_failures)}" if embed_failures else ""))
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

/* ---- Persist the updated ledger (promote AND save - survives restarts) --- */
%macro _rag_store_ledger;
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

/* ---- Propagate the outcome without ever raising a SAS error --------------- */
data _null_;
    if "&_rag_rc." ne "0" then
        putLog "ERROR: RAG ingestion failed: %superq(_rag_error)";
    else
        putLog "NOTE: RAG ingestion succeeded: %superq(_rag_summary)";
run;