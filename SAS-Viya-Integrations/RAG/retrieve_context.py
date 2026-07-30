# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Manifested RAG retrieval model (design §5) — one copy per RAG Setup.

The MANIFEST block below is rewritten per setup (collection, backend,
embedding model, defaults); everything else is generic. The file is
self-contained — no rag_core import — because it runs on retrieval
destinations (Intelligent Decisioning test scoring, MAS, SCR) where the
governed SAS Content folder may not be reachable. Register it as a Python
model in the RAG Model Manager project and as a code file in Intelligent
Decisioning.

Signature (a decision maps these to terms):
    execute(question, k, filter_json, retrieval_mode, options)
      -> (context_dg, context_envelope, retrieval_status, run_time)

  question       - the user question to retrieve context for
  k              - number of chunks (0/blank -> the manifested default)
  filter_json    - optional JSON object of column equality filters,
                   e.g. {"doc_id": "..."} (allow-listed columns only)
  retrieval_mode - "vector" (hybrid arrives with a later release; any
                   other value falls back to vector and says so in
                   retrieval_status)
  options        - optional JSON object overriding connection settings for
                   callers that manage their own secrets: host, port,
                   dbname, sslmode, user, password, scr_endpoint, model

  context_dg       - the portal-compatible datagrid as a JSON string:
                     [{"metadata": [...]}, {"data": [...]}] with columns
                     document_id, chunk_id, filename, source_uri,
                     heading_path, page, span_start, span_end,
                     ingestion_timestamp, corpus_run_id, distance, document
                     (chunk_id is a string here — ids are deterministic
                     hashes, not indexes). source_uri plus page/span make a
                     citation openable at the right place; corpus_run_id
                     attributes the answer to the ingestion state that
                     produced it. Only LIVE chunks are retrieved: retired
                     generations stay in the collection for point-in-time
                     reads but never reach a caller.
  context_envelope - JSON string {query, hits[], graph_context: null,
                     retrieval_mode} — the KG-forward-compatible contract
  retrieval_status - "ok" / "ok (...note...)" or the failure message;
                     failures NEVER raise, the datagrid is just empty
  run_time         - total seconds spent

Connection resolution (design §4 destination boundary), per value:
  1. the per-call options input (external callers with their own secrets)
  2. secrets from the SAS Viya credentials service when a session token is
     present (compute sessions): entries {BACKEND}_RAG_USER and
     {BACKEND}_RAG_PW in the credential domain (the backend prefix lets one
     domain serve several vector stores)
  3. environment variables (SCR/MAS deploy-time injection; local .env):
     {BACKEND}_RAG_USER/{BACKEND}_RAG_PW for the secrets,
     {BACKEND}_HOST/PORT/DB/SSLMODE — or the shared RAGSTORE_* fallback — for
     connection config, RAGEMBED_* for the embedder
  4. the manifested constants below
A decision definition never stores a secret.

Two backends are supported, and BACKEND above decides which SQL dialect and
driver this copy uses. SingleStore has no cosine metric, so vectors are stored
and queried L2-normalized and ranked by dot product; `distance` therefore
means the same number on both backends.
"""
import json
import math
import os
import time

# ---- MANIFEST: rewritten per RAG Setup --------------------------------------
BACKEND = "pgvector"
COLLECTION = "rag_p1_job_v1"
EMBED_MODEL = "all_minilm_l6_v2"
EMBED_ENDPOINT = "https://your-sas-viya-host/llm"
STORE_HOST = "your-database-host"
STORE_PORT = "5432"
STORE_DB = "your-database-name"
STORE_SSLMODE = "prefer"
CREDENTIAL_DOMAIN = "agentic-ai-keys"
DEFAULT_K = 4
INGESTION_RUN_ID = ""  # corpus-version lineage stamp (set by Register Setup)
# -----------------------------------------------------------------------------

_FILTER_COLUMNS = {"doc_id", "source_uri", "content_hash", "extractor",
                   "pipeline_version", "heading_path"}

_SSLMODE_BOOLEANS = {"false": "disable", "off": "disable", "no": "disable",
                     "true": "require", "yes": "require", "on": "require"}

# SingleStore has no NULL tombstone: a live row carries this valid_to instead
_SENTINEL = "9999-12-31 00:00:00"
_DEFAULT_PORTS = {"pgvector": 5432, "singlestore": 3306}

# Datagrid v2: a cited chunk must be openable at the right place and
# attributable to the corpus state that produced it, so the grid carries the
# source location, the position inside the document, and the corpus version.
_DATAGRID_METADATA = [{"document_id": "string"}, {"chunk_id": "string"},
                      {"filename": "string"}, {"source_uri": "string"},
                      {"heading_path": "string"}, {"page": "decimal"},
                      {"span_start": "decimal"}, {"span_end": "decimal"},
                      {"ingestion_timestamp": "string"},
                      {"corpus_run_id": "string"}, {"distance": "decimal"},
                      {"document": "string"}]


def _ssl_verify(prefix):
    """TLS verification for in-cluster calls: the CA bundle SAS Viya mounts
    into every pod, an explicit bundle, or disabled via *_SSLVERIFY=false."""
    bundle = os.getenv(prefix + "_CABUNDLE", "/security/trustedcerts.pem")
    verify = bundle if os.path.isfile(bundle) else True
    if os.getenv(prefix + "_SSLVERIFY", "").strip().lower() in ("false", "no", "0"):
        verify = False
    return verify


def _domain_secrets():
    """Secrets-map lookup under the calling user's session token; {} when no
    token is present or the lookup fails (the chain then falls through)."""
    import base64
    import requests

    token = os.getenv("SAS_SERVICES_TOKEN", "")
    base = (os.getenv("RAGSTORE_CREDENTIALS_URL")
            or os.getenv("SAS_SERVICES_URL", "")).rstrip("/")
    if not token or not base:
        return {}
    try:
        response = requests.get(
            base + "/credentials/domains/" + CREDENTIAL_DOMAIN + "/secrets",
            params={"lookupInGroup": "true"},
            headers={"Authorization": "Bearer " + token},
            verify=_ssl_verify("RAGSTORE"), timeout=15)
        if response.status_code != 200:
            return {}
        return {key: base64.b64decode(value).decode("utf-8")
                for key, value in (response.json().get("secrets") or {}).items()}
    except Exception:
        return {}


def _setting(name):
    """A connection setting: this backend's own variable, then the shared one."""
    return (os.getenv(BACKEND.upper() + "_" + name)
            or os.getenv("RAGSTORE_" + name) or "")


def _store_config(options):
    prefix = BACKEND.upper()
    user = options.get("user") or os.getenv(prefix + "_RAG_USER", "")
    password = options.get("password") or os.getenv(prefix + "_RAG_PW", "")
    if not (user and password):
        secrets = _domain_secrets()
        user = user or secrets.get(prefix + "_RAG_USER", "")
        password = password or secrets.get(prefix + "_RAG_PW", "")
    if not (user and password):
        raise RuntimeError(
            "no vector-store credentials resolved: pass user/password in "
            "options, grant a " + CREDENTIAL_DOMAIN + " credential holding "
            + prefix + "_RAG_USER/" + prefix + "_RAG_PW, or set those "
            "environment variables on the destination")
    sslmode = str(options.get("sslmode") or _setting("SSLMODE")
                  or STORE_SSLMODE).lower()
    return {
        "host": options.get("host") or _setting("HOST") or STORE_HOST,
        "port": int(options.get("port") or _setting("PORT") or STORE_PORT
                    or _DEFAULT_PORTS.get(BACKEND, 5432)),
        "dbname": options.get("dbname") or _setting("DB") or STORE_DB,
        "user": user, "password": password,
        "sslmode": _SSLMODE_BOOLEANS.get(sslmode, sslmode),
    }


def _connect(store):
    """The driver this backend needs — imported here so a destination only
    installs the one it uses."""
    if BACKEND == "singlestore":
        import singlestoredb

        return singlestoredb.connect(
            host=store["host"], port=store["port"], database=store["dbname"],
            user=store["user"], password=store["password"],
            ssl_disabled=str(store["sslmode"]).lower()
            in ("disable", "false", "off", "no", "0"),
            connect_timeout=10)
    import psycopg2

    return psycopg2.connect(connect_timeout=10, **store)


def _embed_query(question, options):
    """Embed the question through the SCR embedding container (query mode)."""
    import requests

    endpoint = (options.get("scr_endpoint") or os.getenv("RAGEMBED_ENDPOINT")
                or (os.getenv("SAS_SERVICES_URL", "").rstrip("/") + "/llm"
                    if os.getenv("SAS_SERVICES_URL") else "")
                or EMBED_ENDPOINT).rstrip("/")
    model = options.get("model") or os.getenv("RAGEMBED_MODEL") or EMBED_MODEL
    if os.getenv("RAGEMBED_DEPLOYMENT_TYPE", "k8s") == "aca":
        host = endpoint.replace("https://", "").replace("http://", "")
        url = "https://" + model.replace("_", "-") + "." + host + "/" + model
    else:
        url = endpoint + "/" + model + "/" + model
    body = {"inputs": [
        {"name": "document", "value": question},
        {"name": "options", "value": "{Embedding_Mode:query}"},
        {"name": "project", "value": "rag"},
    ]}
    response = requests.post(
        url, json=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        verify=_ssl_verify("RAGEMBED"),
        timeout=float(os.getenv("RAGEMBED_TIMEOUT", "60")))
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    return json.loads(data["embedding"])


def _quote(identifier):
    if BACKEND == "singlestore":
        return "`" + identifier + "`"
    return '"' + identifier + '"'


def _compile_filter(filter_json):
    """Equality-only allow-listed filter -> (condition, params); always bound."""
    condition, params = "TRUE", []
    if not filter_json:
        return condition, params
    parsed = json.loads(filter_json) if isinstance(filter_json, str) else filter_json
    if not isinstance(parsed, dict):
        raise ValueError("filter_json must be a JSON object of column: value")
    for column, value in parsed.items():
        if column not in _FILTER_COLUMNS:
            raise ValueError("unsupported filter column " + str(column)
                             + " (allowed: " + ", ".join(sorted(_FILTER_COLUMNS)) + ")")
        condition += " AND " + _quote(column) + " = %s"
        params.append(value)
    return condition, params


def _live_clause(connection):
    """Retired chunk generations must never be retrieved.

    The collection keeps previous generations so it can be read as of an
    earlier date and rolled back (design §2b); a collection created before
    lineage existed has no valid_to column, hence the probe rather than an
    assumption. pgvector marks a live row with NULL; SingleStore cannot (no
    UNIQUE NULLS NOT DISTINCT) and uses the sentinel instead.
    """
    schema = "DATABASE()" if BACKEND == "singlestore" else "current_schema()"
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = " + schema + " AND table_name = %s "
            "AND column_name = 'valid_to'", [COLLECTION])
        if not int(cursor.fetchone()[0]):
            return ""
    if BACKEND == "singlestore":
        return " AND valid_to = '" + _SENTINEL + "'"
    return " AND valid_to IS NULL"


def _rank(vector):
    """(select expression, ordering, query-vector literal, distance conversion).

    SingleStore offers no cosine metric and its vector index rejects
    metric_type COSINE, so the collection stores unit vectors and cosine
    similarity is their dot product — which makes 1 - similarity the same
    number pgvector's `<=>` returns.
    """
    values = [float(v) for v in vector]
    if BACKEND == "singlestore":
        norm = math.sqrt(sum(v * v for v in values))
        if norm:
            values = [v / norm for v in values]
        literal = "[" + ",".join(repr(v) for v in values) + "]"
        return ("DOT_PRODUCT(embedding, %s :> VECTOR(" + str(len(values)) + "))",
                "DESC", literal, lambda score: 1.0 - score)
    literal = "[" + ",".join(repr(v) for v in values) + "]"
    return "(embedding <=> %s::vector)", "ASC", literal, lambda distance: distance


def _search(vector, k, filter_json, store):
    """KNN against the live slice of the collection; higher score = better."""
    condition, params = _compile_filter(filter_json)
    expression, ordering, literal, to_distance = _rank(vector)
    connection = _connect(store)
    try:
        condition += _live_clause(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT doc_id, chunk_id, source_uri, ingested_at, content, "
                "heading_path, span, run_id, " + expression + " AS ranked "
                "FROM " + _quote(COLLECTION) + " WHERE " + condition
                + " ORDER BY ranked " + ordering + " LIMIT %s",
                [literal, *params, int(k)])
            rows = cursor.fetchall()
        connection.commit()
    finally:
        connection.close()
    hits = []
    for (doc_id, chunk_id, source_uri, ingested_at, content, heading_path,
         span, run_id, ranked) in rows:
        distance = to_distance(float(ranked))
        if isinstance(span, (str, bytes)):
            try:
                span = json.loads(span)
            except (ValueError, TypeError):
                span = None
        location = span if isinstance(span, dict) else {}
        hits.append({
            "chunk_id": chunk_id, "doc_id": doc_id, "source_uri": source_uri,
            "filename": str(source_uri or "").replace("\\", "/").rsplit("/", 1)[-1],
            "ingestion_timestamp": str(ingested_at or ""),
            "distance": float(distance), "score": 1.0 - float(distance),
            "content": content, "heading_path": heading_path,
            "page": location.get("page"), "span_start": location.get("start"),
            "span_end": location.get("end"),
            # the corpus state this answer came from: the run that wrote the
            # chunk, falling back to the version stamped at registration
            "corpus_run_id": run_id or INGESTION_RUN_ID,
        })
    return hits


def execute(question, k, filter_json, retrieval_mode, options):
    "Output: context_dg, context_envelope, retrieval_status, run_time"
    started = time.time()
    mode = str(retrieval_mode or "vector").strip().lower()
    note = ""
    hits = []
    status = "ok"
    try:
        if not str(question or "").strip():
            raise ValueError("question is empty")
        if mode != "vector":
            note = " (retrieval_mode " + mode + " not available yet - used vector)"
            mode = "vector"
        parsed_options = {}
        if options and str(options).strip():
            parsed_options = json.loads(options) if isinstance(options, str) else dict(options)
        top_k = int(float(k or 0)) or DEFAULT_K
        store = _store_config(parsed_options)
        vector = _embed_query(str(question), parsed_options)
        hits = _search(vector, top_k, filter_json, store)
        status = "ok" + note if hits else "ok - no chunks matched" + note
    except Exception as error:
        # the manifested-model degradation contract: report, never raise
        status = "retrieval failed: " + type(error).__name__ + ": " + str(error)[:400]
        hits = []
    def cell(value):
        return "" if value is None else value

    context_dg = json.dumps([
        {"metadata": _DATAGRID_METADATA},
        {"data": [[h["doc_id"], h["chunk_id"], h["filename"],
                   cell(h["source_uri"]), cell(h["heading_path"]),
                   cell(h["page"]) or 0, cell(h["span_start"]) or 0,
                   cell(h["span_end"]) or 0, h["ingestion_timestamp"],
                   cell(h["corpus_run_id"]), h["distance"], h["content"]]
                  for h in hits]},
    ])
    context_envelope = json.dumps({
        "query": str(question or ""),
        "hits": hits,
        "graph_context": None,
        "retrieval_mode": mode,
        "collection": COLLECTION,
        "ingestion_run_id": INGESTION_RUN_ID,
    })
    return context_dg, context_envelope, status, round(time.time() - started, 3)
