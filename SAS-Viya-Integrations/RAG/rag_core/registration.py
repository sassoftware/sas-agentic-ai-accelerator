# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Register Setup's server side: Model Manager, SAS Content, job definitions.

Three jobs, all idempotent so re-registering a setup updates rather than
duplicates:

* put the manifested retrieval model and the governance artifacts into SAS
  Content, under the setup's own folder;
* register that model in SAS Model Manager as a scoreable Python model with
  typed variables, stack tags and a `trainTable` pointer to the ledger;
* generate the SCHEDULED JOB from the Studio Flow that produced the corpus -
  `POST /studioDevelopment/code` then a job definition carrying
  `DeployedResourceName`, so the job stays tied to the flow it came from and
  can be regenerated when the flow changes.

Contracts learned live and encoded here rather than rediscovered:
`reference.path` for code generation is the flow's SAS CONTENT path, not its
`/dataFlows/dataFlows/<uuid>` service URI; an existing resource is UPDATED in
place, because re-creating one mints a new id and orphans everything that
referenced the old one; every PUT carries the resource id in the BODY as well
as the URL, or the service reports the resource as missing or mismatched; and
Model Manager drops `tags` on create, keeping them only on a later update, so
registration always finishes with one.

One tag is not part of that definition: TLS-Verification-Off says what the
STORED score code does, so it is set or taken away only after the score code
has been stored (`set_tls_tag`), never by the update that precedes the upload.
"""
from __future__ import annotations

import json

TIMEOUT = (10, 120)

#: On the model exactly when the score code it holds has TLS verification off
#: (`SSL_VERIFY = False` in the MANIFEST block). The Prompt Builder sets the
#: same tag for the same reason (TLS_VERIFICATION_OFF_TAG in
#: prompt-decision.ts); a filter on it lists every model that does not verify.
TLS_VERIFICATION_OFF_TAG = "TLS-Verification-Off"

#: The retrieval model's variables: (name, type, length, description).
#:
#: EVERY variable carries a length. SAS Model Manager stores 99 for a string
#: variable registered without one, and SAS Intelligent Decisioning declares
#: the model in the code it generates for a decision with what is stored
#: (`in_out varchar(100)`). The lengths say what the model takes and returns;
#: nothing was found to depend on them. Measured live on a model whose two
#: context outputs were set back to 99: an envelope of 1969 characters came
#: back complete from SAS Micro Analytic Service, and a test of SAS
#: Intelligent Decisioning returned the longer values complete as well - a
#: longer value neither failed the execution nor was it cut.
#:
#: The Builder holds the same two lists (INPUT_VARIABLES and OUTPUT_VARIABLES
#: in rag-builder.ts), and test_manifest_parity.py compares them entry by
#: entry: a model is the same model whichever of the two registered it last.
RETRIEVAL_INPUTS = [
    ("question", "string", 32000, "The user question to retrieve context for"),
    ("k", "decimal", 8, "Number of chunks to return (0 = the manifested default)"),
    ("filter_json", "string", 4000,
     "Optional JSON object of allow-listed column equality filters"),
    ("retrieval_mode", "string", 32,
     "vector (hybrid arrives with a later release)"),
    ("options", "string", 4000,
     "Optional JSON overrides: connection settings, and API_KEY for an "
     "API-backed embedding model (a decision has no session to resolve one)"),
]
RETRIEVAL_OUTPUTS = [
    ("context_dg", "string", 32000,
     "Retrieved context as a datagrid (JSON string)"),
    ("context_envelope", "string", 32000,
     "JSON envelope: query, hits[], graph_context, retrieval_mode, "
     "collection, ingestion_run_id"),
    ("retrieval_status", "string", 512,
     "ok or the failure message - retrieval never raises"),
    ("run_time", "decimal", 8, "Total seconds spent"),
]

#: What `register_setup` says about the score code when it fails on the way
#: (the attribute `registration` of the exception, key `score_code`).
SCORE_CODE_STORED = "stored"
SCORE_CODE_NOT_STORED = "not stored"
SCORE_CODE_UNKNOWN = "unknown"


class UnexpectedStatus(RuntimeError):
    """A request was answered with a status the caller did not expect.

    `status_code` is that status. `from_service` says whether the body was a
    JSON object, which is how a SAS Viya service words a refusal; an ingress
    or a proxy that answers in its place sends a page of its own.
    """

    def __init__(self, message: str, status_code: int, from_service: bool):
        super().__init__(message)
        self.status_code = status_code
        self.from_service = from_service


def name_filter(name: str) -> str:
    """`eq(name,…)` with the value safely delimited.

    A name is a person's free text - "Bob's policies" is an ordinary thing to
    call a RAG setup - and the filter grammar ends a string literal at the
    first matching quote. Interpolated raw, that name either makes the service
    answer 400 (an opaque failure, long after the corpus was ingested) or
    parses as a different expression that matches nothing, which is worse:
    `register_model` reads "no such model" as "create one", so every
    re-registration adds another duplicate instead of updating the model that
    is already there.

    The grammar accepts either quote as the delimiter, so a name containing
    one is wrapped in the other. Doubling the quote is NOT relied on - it is
    not part of the documented grammar, and a wrong guess here fails in
    exactly the silent way this function exists to prevent.
    """
    text = str(name)
    if "'" not in text:
        return f"eq(name,'{text}')"
    if '"' not in text:
        return f'eq(name,"{text}")'
    raise ValueError(
        f"{text!r} cannot be looked up: a name containing both ' and \" has no "
        "safe form in a filter expression. Rename the project or setup.")


class ViyaClient:
    """The few REST calls Register Setup needs, with readable failures."""

    def __init__(self, base_url: str, token: str, verify=True, session=None):
        import requests

        self.base = str(base_url or "").rstrip("/")
        self.verify = verify
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": "Bearer " + str(token or "")})
        #: Set by `register_model`: True when it wrote the tag
        #: TLS-Verification-Off without knowing whether the model carried it.
        self.tls_tag_assumed = False

    def request(self, method: str, endpoint: str, expect=(200, 201, 204), **kwargs):
        kwargs.setdefault("timeout", TIMEOUT)
        kwargs.setdefault("verify", self.verify)
        response = self.session.request(method, self.base + endpoint, **kwargs)
        if response.status_code not in expect:
            try:
                from_service = isinstance(response.json(), dict)
            except Exception:
                from_service = False
            raise UnexpectedStatus(
                f"{method} {endpoint} returned "
                f"{response.status_code}: {response.text[:300]}",
                response.status_code, from_service)
        return response

    def json(self, method: str, endpoint: str, expect=(200, 201), **kwargs):
        response = self.request(method, endpoint, expect=expect, **kwargs)
        return response.json() if response.content else {}

    @staticmethod
    def created_id(payload: dict) -> str:
        """The id of a just-created resource.

        Model Manager answers a model POST with a COLLECTION wrapper -
        {count: 1, items: [{id: ...}]} - rather than the model itself
        (verified live), while the folders and job-definition services return
        the resource. Accept both instead of guessing per service.
        """
        if payload.get("id"):
            return str(payload["id"])
        items = payload.get("items") or []
        if items and items[0].get("id"):
            return str(items[0]["id"])
        raise RuntimeError("the service did not return the id of the resource "
                           "it created: " + json.dumps(payload)[:300])

    # -- SAS Content --------------------------------------------------------
    def folder_id(self, path: str, create: bool = False) -> str:
        response = self.request("GET", "/folders/folders/@item",
                                params={"path": path}, expect=(200, 404))
        if response.status_code == 200:
            return response.json()["id"]
        if not create:
            raise ValueError(f"SAS Content folder not found: {path}")
        parent, _, name = path.rstrip("/").rpartition("/")
        parent_id = self.folder_id(parent or "/", create=True) if parent else ""
        params = {"parentFolderUri": "/folders/folders/" + parent_id} if parent_id else {}
        return self.created_id(self.json("POST", "/folders/folders",
                                         params=params, json={"name": name}))

    def put_file(self, folder_id: str, name: str, content: str) -> str:
        """Write a text file, REPLACING the content of one already there.

        The files service enforces one name per folder and answers 409 on a
        second POST, so an existing artifact has its content replaced rather
        than being deleted and re-created - which also keeps the file's id, so
        anything referencing it still resolves.
        """
        members = self.json("GET", f"/folders/folders/{folder_id}/members",
                            params={"limit": 500}).get("items", [])
        for member in members:
            if member.get("name") == name and "/files/files/" in str(member.get("uri")):
                file_id = str(member["uri"]).rsplit("/", 1)[-1]
                self.request("PUT", f"/files/files/{file_id}/content",
                             expect=(200, 201, 204),
                             data=content.encode("utf-8"),
                             headers={"Content-Type": "application/octet-stream",
                                      "If-Match": "*"})
                return file_id
        created = self.json(
            "POST", "/files/files",
            params={"parentFolderUri": "/folders/folders/" + folder_id,
                    "filename": name},
            data=content.encode("utf-8"),
            headers={"Content-Type": "application/octet-stream",
                     "Content-Disposition": f'attachment; filename="{name}"'})
        return created.get("id", "")

    # -- Model Manager ------------------------------------------------------
    def ensure_project(self, name: str) -> str:
        found = self.json("GET", "/modelRepository/projects",
                          params={"filter": name_filter(name), "limit": 20})
        # the name is compared here too: the filter narrows the page, it is
        # not the match, and "no result" is the answer that creates a duplicate
        items = [item for item in (found.get("items") or [])
                 if item.get("name") == name]
        if items:
            return items[0]["id"]
        repository = self.project_repository(name)
        project = self.json(
            "POST", "/modelRepository/projects",
            headers={"Content-Type": "application/vnd.sas.models.project+json"},
            json={"name": name, "function": "RAG",
                  "repositoryId": repository["id"],
                  # the service refuses a project without it: 400, "You must
                  # specify a value for the "folderId" parameter" (found live)
                  "folderId": repository["folderId"],
                  "description": "RAG setups registered by the SAS Agentic AI "
                                 "Accelerator",
                  "tags": ["LLM", "RAG-Engineering"]})
        return self.created_id(project)

    def project_repository(self, project: str) -> dict:
        """The repository a project that does not exist yet is created in.

        The DEFAULT repository - the one SAS Model Manager marks with
        `defaultRepository` - and, where none is marked, the first one the
        service lists. The choice is made here rather than left to the order
        of a one-item page: a site can hold many repositories (nine on the
        system this was found on), and "the first" is whichever the service
        happens to return first.

        Returns its `id` and its `folderId`. Both go into the project: the
        service places a project in a folder, and answers 400 when the request
        names the repository alone. A repository that does not name its folder
        is refused here, by name, instead of surfacing as a KeyError.
        """
        repositories = self.json("GET", "/modelRepository/repositories",
                                 params={"limit": 100}).get("items") or []
        if not repositories:
            raise RuntimeError("no model repository exists to create the "
                               f"project {project!r} in")
        chosen = next((item for item in repositories
                       if str(item.get("defaultRepository")).lower() == "true"),
                      repositories[0])
        label = str(chosen.get("name") or chosen.get("id") or "")
        if not chosen.get("id") or not chosen.get("folderId"):
            raise RuntimeError(
                f"the project {project!r} cannot be created: the model "
                f"repository {label!r} does not name its "
                + ("id" if not chosen.get("id") else "folder (folderId)")
                + ". Create the project in SAS Model Manager and run this "
                  "again - an existing project is used as it is")
        return {"id": str(chosen["id"]), "folderId": str(chosen["folderId"]),
                "name": label}

    def project_version_id(self, project_id: str) -> str:
        """The project version a model belongs to.

        A model without one cannot be updated: the service answers 500 with
        "the model has to belong to either a project version or folder"
        (verified live). The project resource names its latest version but
        does not give its id, so the versions collection is the source.
        """
        project = self.json("GET", f"/modelRepository/projects/{project_id}")
        versions = self.json("GET",
                             f"/modelRepository/projects/{project_id}/projectVersions",
                             params={"limit": 20}).get("items") or []
        if not versions:
            raise RuntimeError(f"project {project_id} has no version to "
                               "register a model into")
        latest = str(project.get("latestVersion") or "")
        for version in versions:
            if str(version.get("name")) == latest:
                return str(version["id"])
        return str(versions[-1]["id"])

    def register_model(self, project_id: str, name: str, settings: dict,
                       description: str, train_table: str) -> str:
        """Create or update the scoreable retrieval model."""
        found = self.json("GET", "/modelRepository/models",
                          params={"filter": name_filter(name), "limit": 20})
        existing = [m for m in (found.get("items") or [])
                    if m.get("name") == name and m.get("projectId") == project_id]
        version_id = self.project_version_id(project_id)
        body = {
            "name": name,
            "projectId": project_id,
            "projectVersionId": version_id,
            "description": description[:1024],
            "function": "RAG",
            "algorithm": "RAG",
            "scoreCodeType": "python",
            "trainTable": train_table,
            "tags": ["LLM", "RAG", str(settings.get("EMBED_MODEL") or ""),
                     str(settings.get("BACKEND") or "")],
            "properties": [
                {"name": "collection", "value": str(settings.get("COLLECTION") or "")},
                {"name": "pipelineVersion",
                 "value": str(settings.get("pipeline_version") or "")},
                {"name": "configuration", "value": str(settings.get("config_id") or "")},
                {"name": "ingestionRunId",
                 "value": str(settings.get("INGESTION_RUN_ID") or "")},
            ],
            "inputVariables": [
                {"name": n, "role": "input", "type": t, "length": length,
                 "description": d} for n, t, length, d in RETRIEVAL_INPUTS],
            "outputVariables": [
                {"name": n, "role": "output", "type": t, "length": length,
                 "description": d} for n, t, length, d in RETRIEVAL_OUTPUTS],
        }
        media = "application/vnd.sas.models.model+json"
        headers = {"Content-Type": media, "Accept": media}
        if existing:
            model_id = existing[0]["id"]
        else:
            model_id = self.created_id(self.json("POST", "/modelRepository/models",
                                                 headers=headers, json=body))
        # ALWAYS finish with an update, even on a fresh model: Model Manager
        # silently drops `tags` on create and only keeps them on a subsequent
        # update (properties do persist on create) - verified live. GET, merge,
        # PUT: the id must be in the BODY as well as the URL, or the service
        # answers 404 "A model with the ID '' could not be found", and whatever
        # the service set on the model has to survive the update.
        merged = dict(body)
        etag = "*"
        # Whether the model carries TLS-Verification-Off: True, False, or None
        # while nothing has said so. A model created a moment ago holds no
        # score code and no tag. For a model that was found, the entry of the
        # listing is the first word, where it has one: the listing of the
        # models of a project carries their tags (`tags: null` for a model
        # without any) - the Builder tells its kinds of model apart by them.
        # For the listing asked above, /modelRepository/models with a filter
        # on the name, that was not measured; an entry without the key says
        # nothing, and the tag is then kept (see below).
        tagged = None
        if not existing:
            tagged = False
        elif "tags" in existing[0]:
            tagged = TLS_VERIFICATION_OFF_TAG in (existing[0].get("tags") or [])
        try:
            current = self.request("GET", f"/modelRepository/models/{model_id}",
                                   headers={"Accept": media})
            held = dict(current.json())
            merged = dict(held)
            merged.update(body)
            tagged = TLS_VERIFICATION_OFF_TAG in (held.get("tags") or [])
            etag = current.headers.get("ETag") or "*"
        except Exception:
            # a model an interrupted registration left in a bad state cannot
            # even be READ; the complete definition still updates it
            pass
        # TLS-Verification-Off says what the STORED score code does, and this
        # update stores none: the tag stays as the model has it, and
        # set_tls_tag settles it once the new score code is stored. Taking it
        # away here would leave a model that does not verify untagged whenever
        # the upload that follows fails. That holds for a model that could
        # not be read as well: the tag stays when the listing showed it, and
        # also when nothing said whether the model carries it - a tag too
        # many is put right by the next registration that goes through, a
        # model that does not verify and is not tagged is found by nobody.
        # In that last case the tag is WRITTEN here, not kept: the caller is
        # told (tls_tag_assumed), because a registration that fails at the
        # upload then leaves a tag that this run put there.
        self.tls_tag_assumed = tagged is None
        if tagged is not False:
            merged["tags"] = list(body["tags"]) + [TLS_VERIFICATION_OFF_TAG]
        merged["id"] = model_id
        self.json("PUT", f"/modelRepository/models/{model_id}",
                  headers=dict(headers, **{"If-Match": etag}), json=merged)
        return model_id

    def set_tls_tag(self, model_id: str, verification_off: bool) -> bool:
        """Put TLS-Verification-Off on the model, or take it away.

        Called AFTER the score code is stored, with what that code does: the
        tag is on the model exactly when the code has verification off, so a
        re-registration with verification on removes it. Every other tag
        stays. GET, change the one tag, PUT - with the id in the body, as for
        every model update - and no PUT at all when the tag already is as it
        should be. Returns whether the model was updated.
        """
        media = "application/vnd.sas.models.model+json"
        current = self.request("GET", f"/modelRepository/models/{model_id}",
                               headers={"Accept": media})
        model = dict(current.json())
        held = list(model.get("tags") or [])
        tags = [tag for tag in held if tag != TLS_VERIFICATION_OFF_TAG]
        if (len(tags) != len(held)) == bool(verification_off):
            return False
        if verification_off:
            tags.append(TLS_VERIFICATION_OFF_TAG)
        model["tags"] = tags
        model["id"] = model_id
        self.json("PUT", f"/modelRepository/models/{model_id}",
                  headers={"Content-Type": media, "Accept": media,
                           "If-Match": current.headers.get("ETag") or "*"},
                  json=model)
        return True

    def put_model_content(self, model_id: str, name: str, content: str,
                          role: str = "") -> None:
        """Attach a file to the model; the same name replaces rather than piles up."""
        params = {"onConflict": "update"}
        if role:
            params["role"] = role
        self.request("POST", f"/modelRepository/models/{model_id}/contents",
                     params=params,
                     files={"files": (name, content.encode("utf-8"),
                                      "application/octet-stream")})

    # -- the scheduled job, generated FROM the flow -------------------------
    def flow_reference(self, flow_path: str) -> dict:
        """Resolve a .flw's content path to its service uri and id."""
        folder, _, name = flow_path.rstrip("/").rpartition("/")
        members = self.json("GET",
                            f"/folders/folders/{self.folder_id(folder)}/members",
                            params={"limit": 500}).get("items", [])
        for member in members:
            if member.get("name") == name:
                uri = str(member.get("uri") or "")
                return {"path": flow_path, "uri": uri,
                        "id": uri.rsplit("/", 1)[-1]}
        raise ValueError(f"no flow named {name!r} in {folder}")

    def generate_flow_code(self, flow_path: str) -> str:
        """SAS for a Studio Flow.

        `reference.path` is the flow's SAS CONTENT path. The service URI form
        is accepted by the validator and then fails generation with a 500,
        and a `sascontent:` prefix is rejected outright (both verified live).
        """
        body = {"reference": {"type": "content",
                              "mediaType": "application/vnd.sas.dataflow",
                              "path": flow_path}}
        code = self.json("POST", "/studioDevelopment/code",
                         headers={"Content-Type": "application/json"},
                         json=body).get("code") or ""
        if not code.strip():
            raise RuntimeError(f"code generation returned nothing for {flow_path}")
        return code

    def put_job_definition(self, folder_id: str, name: str, code: str,
                           flow_uri: str, description: str = "",
                           compute_context: str = "") -> str:
        """Create or UPDATE the job definition for a flow.

        Updating matters: a new definition means a new URI, and anything that
        scheduled or launched the old one is pointing at a resource that no
        longer exists.
        """
        body = {
            "version": 2,
            "name": name,
            "type": "Compute",
            "description": description[:1024],
            "code": code,
            "parameters": [
                # NOT blank by default: an empty value lands the job in the
                # stock SAS Job Execution context, which runs the server as a
                # service account and cannot reuse the CAS session the steps
                # need - so every generated job failed on its first step
                {"version": 1, "name": "_contextName", "type": "CHARACTER",
                 "label": "Compute context", "required": False,
                 "defaultValue": compute_context or DEFAULT_COMPUTE_CONTEXT},
            ],
            "properties": [
                # ties the job to the flow it was generated from, so it can be
                # regenerated when the flow changes
                {"name": "DeployedResourceName",
                 "value": "sascontent:" + flow_uri},
            ],
        }
        media = "application/vnd.sas.job.definition+json"
        members = self.json("GET", f"/folders/folders/{folder_id}/members",
                            params={"limit": 500}).get("items", [])
        for member in members:
            if (member.get("name") == name
                    and "/jobDefinitions/definitions/" in str(member.get("uri"))):
                job_id = str(member["uri"]).rsplit("/", 1)[-1]
                # the id belongs in the BODY as well as the URL, exactly as for
                # a model - here the service says "Job definition IDs do not
                # match on update: <id> and ." (verified live)
                self.json("PUT", f"/jobDefinitions/definitions/{job_id}",
                          headers={"Content-Type": media, "Accept": media,
                                   "If-Match": "*"}, json=dict(body, id=job_id))
                return job_id
        created = self.json(
            "POST", "/jobDefinitions/definitions",
            params={"parentFolderUri": "/folders/folders/" + folder_id},
            headers={"Content-Type": media, "Accept": media}, json=body)
        return self.created_id(created)


#: a context that runs as the REQUESTING USER; the stock Job Execution
#: context does not, and the steps cannot run there
DEFAULT_COMPUTE_CONTEXT = "SAS Studio compute context"

#: The first statements of a job generated from a flow. The steps default
#: RAG_ABORT_ON_ERROR to 1, which is right for a flow run interactively in
#: SAS Studio: `abort 42` stops it at the failing step. In a job the same
#: statement ends the session before SAS Job Execution gets its completion
#: handshake, so the job reports running indefinitely and its dead compute
#: server keeps counting against the context's server limit. Every step
#: leaves a value that is already set alone, so setting it first decides it
#: for the whole run.
#: RAG_JOB_FAILED is what the epilogue tests. It starts at 0 here, so a
#: session that ran a failing job before does not fail this one, and no step
#: ever sets it back.
JOB_PROLOGUE = ("%let RAG_ABORT_ON_ERROR = 0;\n"
                "%global RAG_JOB_FAILED;\n"
                "%let RAG_JOB_FAILED = 0;\n")

#: The last statements of a job generated from a flow. With the abort off, a
#: failing step writes its ERROR line, sets RAG_PIPELINE_FAILED and
#: RAG_JOB_FAILED and returns; the steps after it skip, and the program ends
#: normally - which SAS Job Execution reports as a completed job. SYSCC is
#: what makes it a failed one. The test is on RAG_JOB_FAILED first:
#: List Documents, Purge Documents and Retrieve Context each start a chain
#: and clear RAG_PIPELINE_FAILED, so a Retrieve Context node that runs after
#: a failed ingestion chain, or a second chain in the same flow, leaves that
#: one at 0 at the end of a run that failed. RAG_PIPELINE_FAILED is still
#: read as well: it is all a step deployed before RAG_JOB_FAILED existed
#: sets.
#: A macro, because the test is a conditional and has to hold in any SAS
#: release the job lands on.
JOB_EPILOGUE = """
/* The outcome SAS Job Execution reports for this job: a step that failed set
   RAG_JOB_FAILED and the steps after it in its chain skipped, so without
   this the job would end as completed. */
%macro _rag_job_outcome;
    %local failed;
    %let failed = 0;
    %if %symexist(RAG_JOB_FAILED) %then
        %if %superq(RAG_JOB_FAILED) EQ 1 %then %let failed = 1;
    %if %symexist(RAG_PIPELINE_FAILED) %then
        %if %superq(RAG_PIPELINE_FAILED) EQ 1 %then %let failed = 1;
    %if &failed. NE 1 %then %return;
    %put ERROR: RAG ingestion failed - a step of this run reported an error (its ERROR line is above) and the steps after it were skipped.;
    %let SYSCC = 1012;
%mend _rag_job_outcome;
%_rag_job_outcome;
"""


def scheduled_job_code(flow_code: str) -> str:
    """The code of a job definition, from the code generated for its flow.

    The generated code is the flow as SAS Studio would run it; a job needs it
    not to abort and to report a failure as one (see JOB_PROLOGUE and
    JOB_EPILOGUE). Both are added HERE and are not part of the flow, so
    regenerating the job from SAS Studio's own deployment of the flow drops
    them - Register Setup is what regenerates this job.
    """
    return JOB_PROLOGUE + flow_code.rstrip("\r\n") + "\n" + JOB_EPILOGUE


def register_setup(client: ViyaClient, settings: dict, template: str,
                   ledger_rows: list, content_root: str, setup_name: str,
                   mm_project: str = "RAG Engineering", ddl: str = "",
                   cas_server: str = "cas-shared-default", flow_path: str = "",
                   compute_context: str = "",
                   log=print) -> dict:
    """Everything Register Setup does, in one call. Returns what it produced.

    `settings["SSL_VERIFY"]` set to the literal False asks for a retrieval
    model that does not verify TLS certificates; anything else, or no value,
    keeps verification on. The result says what was stored rather than what
    was asked for:

    * `ssl_verify` - whether the stored score code verifies by default;
    * `ssl_verify_in_template` - whether the template's MANIFEST block holds
      SSL_VERIFY. False means the template predates the setting: nothing was
      written, and the model verifies whatever was asked for;
    * `tls_tag_error` - empty, or why the tag TLS-Verification-Off could not
      be set or removed after the score code was stored.

    A registration that fails AFTER the model exists raises what failed, as
    before, and the exception carries the attribute `registration`: a dict
    with `model_id`, `model_name`, `ssl_verify`, `ssl_verify_in_template`,
    `tls_tag_error` and `score_code`, one of

    * "stored" - the model holds the new score code, and `ssl_verify` and
      `tls_tag_error` say what that code does and whether the tag follows it.
      A model that does not verify stays one when a later request fails (the
      flow of the scheduled job does not exist, say), so the caller has to
      be able to say so in the failing run;
    * "not stored" - SAS Model Manager refused the upload with an answer of
      its own: the model keeps the score code it had, and the tag it had
      unless `tls_tag_assumed` says otherwise;
    * "unknown" - the upload got no usable answer (the connection dropped, a
      time-out, the page of an ingress in place of the service). The service
      may have stored the file all the same. Nothing is read back and the
      tag is left as it is - it may not say what the stored score code does
      until the registration is run again. The Prompt Builder words the same
      case the same way (ragBuilderScoreCodeUnknown).

    `tls_tag_assumed` is in the dict as well: True when the model could not
    be read before it was updated and its entry in the listing did not show
    its tags, so that the update WROTE the tag TLS-Verification-Off without
    knowing whether the model carried it. Once the score code is stored the
    tag is settled after it and this no longer matters; for "not stored" and
    "unknown" it means the model may carry a tag this run put there.
    """
    from .manifest import (collection_manifest, ingestion_manifest,
                           manifest_block_has, manifested_ssl_verify,
                           pipeline_yaml, render_retrieval_model)

    model_source = render_retrieval_model(template, settings)
    # read back from the text that will be stored, so the tag and the report
    # follow the score code and not the request
    ssl_verify = manifested_ssl_verify(model_source)
    ssl_verify_in_template = manifest_block_has(template, "SSL_VERIFY")
    artifacts = {
        "retrieve_context.py": model_source,
        "pipeline.yaml": pipeline_yaml(dict(settings, setup=setup_name)),
        "ingestion-manifest.json": ingestion_manifest(
            dict(settings, setup=setup_name), ledger_rows),
        "collection-manifest.json": collection_manifest(
            dict(settings, setup=setup_name), ddl),
    }
    if ddl:
        artifacts["vector-store-ddl.sql"] = ddl

    folder = f"{content_root.rstrip('/')}/generated/{setup_name}"
    folder_id = client.folder_id(folder, create=True)
    for name, content in artifacts.items():
        client.put_file(folder_id, name, content)
    log(f"artifacts written to {folder}")

    project_id = client.ensure_project(mm_project)
    train_table = (f"{cas_server}/{settings.get('tables_caslib')}/"
                   f"{settings.get('rag_project')}_LEDGER")
    model_name = f"RAG Retrieval - {setup_name}"
    model_id = client.register_model(
        project_id, model_name, settings,
        description=(f"Retrieval for the {setup_name} RAG setup: collection "
                     f"{settings.get('COLLECTION')} in "
                     f"{settings.get('BACKEND')}, embedded with "
                     f"{settings.get('EMBED_MODEL')}. Registered from "
                     f"ingestion run {settings.get('INGESTION_RUN_ID')}."),
        train_table=train_table)
    # From here on the model exists, and a failure leaves it as far as the
    # registration got. `reached` goes onto the exception, so that the caller
    # can say in the failing run what the model holds. The upload of the
    # score code is the next request, and until it has an answer nothing is
    # known about it.
    assumed = getattr(client, "tls_tag_assumed", False) is True
    reached = {"model_id": model_id, "model_name": model_name,
               "score_code": SCORE_CODE_UNKNOWN, "ssl_verify": ssl_verify,
               "ssl_verify_in_template": ssl_verify_in_template,
               "tls_tag_error": "", "tls_tag_assumed": assumed}
    try:
        try:
            client.put_model_content(model_id, "retrieve_context.py",
                                     model_source, role="score")
        except Exception as error:
            # An answer of the service itself is certain: nothing was stored.
            # Everything else is not - see "unknown" in the docstring.
            refused = (isinstance(error, UnexpectedStatus)
                       and error.from_service)
            reached["score_code"] = (SCORE_CODE_NOT_STORED if refused
                                     else SCORE_CODE_UNKNOWN)
            reason = f"{type(error).__name__}: {error}"[:300]
            if refused and not assumed:
                log("SAS Model Manager did not store retrieve_context.py - "
                    "the model keeps the score code and the tag "
                    f"{TLS_VERIFICATION_OFF_TAG} as it had them: {reason}")
            elif refused:
                log("SAS Model Manager did not store retrieve_context.py - "
                    f"the model keeps the score code it had: {reason}")
            else:
                log("it is not known whether SAS Model Manager stored "
                    "retrieve_context.py: the upload failed or got no usable "
                    f"answer ({reason}), and the service may have stored the "
                    f"file all the same. The tag {TLS_VERIFICATION_OFF_TAG} "
                    + ("was not updated" if not assumed else
                       "was not updated after the upload")
                    + " and may not say what the stored score "
                    "code does - run the registration again")
            if assumed:
                # the tag is not as the model had it: this run wrote it
                log("the model could not be read before it was updated, and "
                    "its entry in the listing did not show its tags: the tag "
                    f"{TLS_VERIFICATION_OFF_TAG} was written to it without "
                    "knowing whether it carried it, and may not say what the "
                    "model does - run the registration again")
            raise
        reached["score_code"] = SCORE_CODE_STORED
        # The score code is stored: from here on the tag has to say what it
        # does. A tag that cannot be updated does not undo the registration -
        # it is reported, so that the caller can say which way the tag is
        # now wrong.
        tls_tag_error = ""
        try:
            client.set_tls_tag(model_id, not ssl_verify)
        except Exception as error:
            tls_tag_error = f"{type(error).__name__}: {error}"[:300]
            log(f"the tag {TLS_VERIFICATION_OFF_TAG} could not be "
                f"{'removed from' if ssl_verify else 'written to'} the model: "
                f"{tls_tag_error}")
        reached["tls_tag_error"] = tls_tag_error
        log("TLS certificate verification in the stored score code: "
            + ("on" if ssl_verify else "OFF"))
        for name in ("pipeline.yaml", "ingestion-manifest.json",
                     "collection-manifest.json", "vector-store-ddl.sql"):
            if name in artifacts:
                client.put_model_content(model_id, name, artifacts[name])
        log(f"model {model_name} registered in {mm_project} ({model_id})")

        result = {"folder": folder, "model_id": model_id,
                  "model_name": model_name,
                  "project_id": project_id, "artifacts": sorted(artifacts),
                  "job_id": "", "job_name": "",
                  "ssl_verify": ssl_verify,
                  "ssl_verify_in_template": ssl_verify_in_template,
                  "tls_tag_error": tls_tag_error}

        if flow_path:
            reference = client.flow_reference(flow_path)
            code = scheduled_job_code(client.generate_flow_code(flow_path))
            job_name = f"RAG Ingest - {setup_name}"
            job_id = client.put_job_definition(
                client.folder_id(f"{content_root.rstrip('/')}/generated",
                                 create=True),
                job_name, code, reference["uri"],
                description=(f"Scheduled ingestion for the {setup_name} RAG "
                             f"setup, generated from {flow_path}."),
                compute_context=compute_context)
            result.update({"job_id": job_id, "job_name": job_name,
                           "flow": reference["path"]})
            log(f"job definition {job_name} generated from {flow_path} "
                f"({job_id})")
        return result
    except Exception as error:
        try:
            error.registration = dict(reached)
        except Exception:
            # an exception that takes no attribute is raised as it is
            pass
        raise
