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
"""
from __future__ import annotations

import json

TIMEOUT = (10, 120)

RETRIEVAL_INPUTS = [
    ("question", "string", 4096, "The user question to retrieve context for"),
    ("k", "decimal", 8, "Number of chunks to return (0 = the setup default)"),
    ("filter_json", "string", 1024, "Optional JSON metadata filter"),
    ("retrieval_mode", "string", 16, "vector (hybrid arrives later)"),
    ("options", "string", 2048, "Optional JSON connection overrides"),
]
RETRIEVAL_OUTPUTS = [
    ("context_dg", "string", 32000, "Retrieved context as a datagrid"),
    ("context_envelope", "string", 32000, "Context envelope, knowledge-graph ready"),
    ("retrieval_status", "string", 512, "ok, or the reason retrieval degraded"),
    ("run_time", "decimal", 8, "Seconds spent retrieving"),
]


class ViyaClient:
    """The few REST calls Register Setup needs, with readable failures."""

    def __init__(self, base_url: str, token: str, verify=True, session=None):
        import requests

        self.base = str(base_url or "").rstrip("/")
        self.verify = verify
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": "Bearer " + str(token or "")})

    def request(self, method: str, endpoint: str, expect=(200, 201, 204), **kwargs):
        kwargs.setdefault("timeout", TIMEOUT)
        kwargs.setdefault("verify", self.verify)
        response = self.session.request(method, self.base + endpoint, **kwargs)
        if response.status_code not in expect:
            raise RuntimeError(f"{method} {endpoint} returned "
                               f"{response.status_code}: {response.text[:300]}")
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
                          params={"filter": f"eq(name,'{name}')", "limit": 1})
        items = found.get("items") or []
        if items:
            return items[0]["id"]
        repositories = self.json("GET", "/modelRepository/repositories",
                                 params={"limit": 1}).get("items") or []
        if not repositories:
            raise RuntimeError("no model repository exists to create the "
                               f"project {name!r} in")
        project = self.json(
            "POST", "/modelRepository/projects",
            headers={"Content-Type": "application/vnd.sas.models.project+json"},
            json={"name": name, "function": "RAG",
                  "repositoryId": repositories[0]["id"],
                  "description": "RAG setups registered by the SAS Agentic AI "
                                 "Accelerator",
                  "tags": ["LLM", "RAG-Engineering"]})
        return self.created_id(project)

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
                          params={"filter": f"eq(name,'{name}')", "limit": 5})
        existing = [m for m in (found.get("items") or [])
                    if m.get("projectId") == project_id]
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
        try:
            current = self.request("GET", f"/modelRepository/models/{model_id}",
                                   headers={"Accept": media})
            merged = dict(current.json())
            merged.update(body)
            etag = current.headers.get("ETag") or "*"
        except Exception:
            # a model an interrupted registration left in a bad state cannot
            # even be READ; the complete definition still updates it
            pass
        merged["id"] = model_id
        self.json("PUT", f"/modelRepository/models/{model_id}",
                  headers=dict(headers, **{"If-Match": etag}), json=merged)
        return model_id

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


def register_setup(client: ViyaClient, settings: dict, template: str,
                   ledger_rows: list, content_root: str, setup_name: str,
                   mm_project: str = "RAG Engineering", ddl: str = "",
                   cas_server: str = "cas-shared-default", flow_path: str = "",
                   compute_context: str = "",
                   log=print) -> dict:
    """Everything Register Setup does, in one call. Returns what it produced."""
    from .manifest import (collection_manifest, ingestion_manifest,
                           pipeline_yaml, render_retrieval_model)

    model_source = render_retrieval_model(template, settings)
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
    client.put_model_content(model_id, "retrieve_context.py", model_source,
                             role="score")
    for name in ("pipeline.yaml", "ingestion-manifest.json",
                 "collection-manifest.json", "vector-store-ddl.sql"):
        if name in artifacts:
            client.put_model_content(model_id, name, artifacts[name])
    log(f"model {model_name} registered in {mm_project} ({model_id})")

    result = {"folder": folder, "model_id": model_id, "model_name": model_name,
              "project_id": project_id, "artifacts": sorted(artifacts),
              "job_id": "", "job_name": ""}

    if flow_path:
        reference = client.flow_reference(flow_path)
        code = client.generate_flow_code(flow_path)
        job_name = f"RAG Ingest - {setup_name}"
        job_id = client.put_job_definition(
            client.folder_id(f"{content_root.rstrip('/')}/generated", create=True),
            job_name, code, reference["uri"],
            description=(f"Scheduled ingestion for the {setup_name} RAG setup, "
                         f"generated from {flow_path}."),
            compute_context=compute_context)
        result.update({"job_id": job_id, "job_name": job_name,
                       "flow": reference["path"]})
        log(f"job definition {job_name} generated from {flow_path} ({job_id})")
    return result
