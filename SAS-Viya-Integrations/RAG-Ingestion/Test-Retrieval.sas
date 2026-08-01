/******************************************************************************
 * Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Ask a RAG collection one question and print what comes back.
 *
 * This is the RAG Builder's "Test retrieval" - a read-only probe of a live
 * collection, run as the person who asked. It answers "is my corpus returning
 * sensible chunks", which is the question someone iterating on chunk size or
 * embedding model asks many times a day.
 *
 * IT LEAVES NOTHING BEHIND (owner requirement 2026-08-01). The Builder
 * creates a job definition WITHOUT a parent folder - so it never appears in
 * SAS Content - submits it, reads the results out of the log, and deletes the
 * definition. The finished job's own record and log survive the delete, which
 * is what makes reading-then-deleting safe in either order.
 *
 * Results travel in the LOG, not in a table, for the same reason: a CAS
 * output table would itself be an artifact of a test that is supposed to have
 * no side effects. Retrieval returns a handful of rows, so the log carries
 * them comfortably.
 *
 * WHAT THIS DOES NOT TEST: the manifested retrieval model. It calls rag_core
 * directly, the same way the ingestion does. A registered score code that is
 * broken would still pass here - verifying that needs the published model.
 *
 * Parameters:
 *   question         REQUIRED. The question to ask
 *   topK             How many chunks to return (default 5)
 *   collection       REQUIRED. The collection to search
 *   backend / storeHost / storePort / storeDb / storeSslmode
 *   credentialDomain / scrEndpoint / embedModel / deploymentType
 *   ragCorePath      SAS Content folder holding rag_core
 *
 * Contract: the job ALWAYS completes; the outcome is in the log.
 ******************************************************************************/

%macro _rag_default(name, value);
    %if not %symexist(&name.) %then %do;
        %global &name.;
        %let &name. = &value.;
    %end;
%mend _rag_default;

%_rag_default(question, );
%_rag_default(topK, 5);
%_rag_default(collection, );
%_rag_default(backend, pgvector);
%_rag_default(storeHost, );
%_rag_default(storePort, );
%_rag_default(storeDb, );
%_rag_default(storeSslmode, prefer);
%_rag_default(credentialDomain, agentic-ai-keys);
%_rag_default(scrEndpoint, );
%_rag_default(embedModel, all_minilm_l6_v2);
%_rag_default(deploymentType, k8s);
%_rag_default(ragCorePath, /SAS Agentic AI Accelerator/RAG/rag_core);

%let _rag_viyaHost = %sysfunc(getoption(SERVICESBASEURL));

proc python restart;
submit;
def main():
    import json
    import os
    import sys
    import tempfile
    import traceback

    def sas_safe(text):
        cleaned = str(text)
        for ch in ("'", '"', "%", "&", ";"):
            cleaned = cleaned.replace(ch, " ")
        return cleaned

    def M(msg):
        SAS.logMessage("RAGRETRIEVE " + sas_safe(msg))

    def M_row(payload):
        """Emit one machine-read row.

        sas_safe() would strip the quotes out of the JSON and leave the
        Builder nothing to parse, so rows take this path instead. What the
        stripping is FOR - keeping macro triggers out of a log line - is done
        without losing anything: % and & are re-encoded as their JSON escapes,
        which decode back to the exact same characters in the browser. Both
        appear only inside string values (JSON syntax uses neither), so
        rewriting them on the finished document is safe.
        """
        armed = payload.replace("%", "\\u0025").replace("&", "\\u0026")
        SAS.logMessage("RAGRETRIEVE ROW " + armed)

    try:
        import requests
    except Exception:
        M("the Python environment of this compute context lacks requests")
        return

    BASE = str(SAS.symget("_rag_viyaHost") or "").rstrip("/")
    TOKEN = os.environ.get("SAS_SERVICES_TOKEN", "")
    AUTH = {"Authorization": "Bearer " + TOKEN}
    VERIFY = os.environ.get("SSLCALISTLOC") or os.environ.get("CAS_CLIENT_SSL_CA_LIST") or True

    P = {name: str(SAS.symget(name) or "").strip() for name in [
        "question", "topK", "collection", "backend", "storeHost", "storePort",
        "storeDb", "storeSslmode", "credentialDomain", "scrEndpoint",
        "embedModel", "deploymentType", "ragCorePath",
    ]}

    try:
        def get_json(endpoint, **params):
            response = requests.get(BASE + endpoint, params=params, headers=AUTH,
                                    verify=VERIFY, timeout=(5, 30))
            response.raise_for_status()
            return response.json()

        def download_folder(folder_id, local_dir):
            os.makedirs(local_dir, exist_ok=True)
            count = 0
            for member in get_json(f"/folders/folders/{folder_id}/members",
                                   limit=200).get("items", []):
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
                               f"(HTTP {root.status_code})")
        staging = tempfile.mkdtemp(prefix="rag_core_")
        download_folder(root.json()["id"], os.path.join(staging, "rag_core"))
        sys.path.insert(0, staging)

        from rag_core.adapters import get_adapter
        from rag_core.credentials import fetch_secrets, store_config_from_secrets
        from rag_core.pricing import log_cost
        from rag_core.providers import api_key_for
        from rag_core.scr import EmbeddingClient
        from rag_core.steps import run_retrieve

        for required in ("question", "collection", "storeHost", "storeDb"):
            if not P[required]:
                raise RuntimeError(f"required parameter {required} is missing")

        secrets = fetch_secrets(BASE, TOKEN, P["credentialDomain"], verify=VERIFY)
        if not secrets:
            raise RuntimeError(f"no credential resolved from domain "
                               f"{P['credentialDomain']} for this user")
        store_config = store_config_from_secrets(
            secrets, P["backend"], P["storeHost"], P["storePort"],
            P["storeDb"], P["storeSslmode"])
        adapter = get_adapter(P["backend"])
        adapter.connect(store_config)
        client = EmbeddingClient(
            P["scrEndpoint"] or BASE + "/llm", P["embedModel"],
            deployment_type=P["deploymentType"] or "k8s", verify_ssl=VERIFY,
            api_key=api_key_for(P["embedModel"], secrets))

        top_k = max(1, int(float(P["topK"] or 5)))
        rows = run_retrieve([P["question"]], client, adapter, P["collection"],
                            k=top_k, log=M)
        # One JSON object per row on its own line: the Builder parses these
        # out of the live compute log. Keeping it to one line per row means a
        # truncated log costs the last rows rather than corrupting all of them.
        for row in rows:
            M_row(json.dumps({
                "rank": row.get("rank"),
                "score": round(float(row.get("score") or 0), 4),
                "distance": round(float(row.get("distance") or 0), 4),
                "source": str(row.get("source_uri") or ""),
                "heading": str(row.get("heading_path") or ""),
                "page": row.get("page"),
                "content": str(row.get("content") or "")[:600],
                "error": str(row.get("error_text") or ""),
            }, ensure_ascii=False))
        log_cost(P["embedModel"], client.usage, log=M)
        M(f"DONE {len(rows)} row(s) for '{P['question'][:80]}' from "
          f"collection {P['collection']}")
        adapter.close()
    except Exception as error:
        M("FAILED " + sas_safe(f"{type(error).__name__}: {error}")[:400])
        print(traceback.format_exc())


main()
endsubmit;
run; quit;
proc python terminate;
run; quit;
