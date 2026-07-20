# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""One parametrized register/update/publish path for both definition kinds.

Creation mirrors register-LLMs.py / register-Embedding.py byte-for-byte in
its load-bearing conventions (content roles incl. requirements.json as
'python pickle', endPoint = {scr}/{id}/{id}, costPerCall enrichment, tag PUT
with ETag). The update path uses the Prompt Builder's production-proven
pattern: a new minor model version, then POST contents?onConflict=update per
file. The definition.yaml manifest is stored as model content so registered
models carry their source of truth.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..core.generator import effective_score_file
from ..core.manifest import MANIFEST_FILENAME, ModelManifest

KIND_PROJECT = {"llm": "LLM Model Project", "embedding": "Embedding Model Project"}
REPOSITORY = "LLM Repository"
REPOSITORY_DESCRIPTION = (
    "This repository is used to register LLM deployment instructions to, build, "
    "monitor and deploy use cases that take advantage of LLMs"
)
# Per-kind project metadata. mdb setup is the canonical way to create these; the
# values are the framework's project contract (they were previously kept in sync
# with the retired Model-Manager-Setup.py).
PROJECT_META = {
    "llm": {
        "project": "LLM Model Project",
        "function": "LLM",
        # The LLM scorer's primary output variable.
        "target_variable": "response",
        "description": (
            "This project stores all LLMs that are available to be used in use cases. "
            "It is possible to grant access to these models on a per model basis. Along "
            "side the availability this also documents on how to deploy/call the models."
        ),
        "tags": ["LLM-Models", "SCR-Definitions", "Python"],
    },
    "embedding": {
        "project": "Embedding Model Project",
        "function": "Embedding",
        # Embedding models emit 'embedding', not 'response' (which does not exist
        # in the embedding variable set); pointing the project target at a real
        # variable keeps project-level monitoring/KPIs coherent.
        "target_variable": "embedding",
        "description": (
            "This project stores all Embedding models that are available to be used in "
            "use cases. It is possible to grant access to these models on a per model "
            "basis. Along side the availability this also documents on how to deploy/call "
            "the models."
        ),
        "tags": ["Embedding-Models", "SCR-Definitions", "Python"],
    },
}
TOKENIZER_FILES = ("tokenizer_config.json", "special_tokens_map.json", "tokenizer.json")


def project_variables(core, kind: str) -> list[dict]:
    """Project input/output variables for a kind, built from the shared
    definition-core var files (the setup script reads them from a _Base_Definition
    folder; mdb owns the same JSON centrally)."""
    input_json, output_json = core.var_files[kind]
    variables = [dict(var, role="input") for var in json.loads(input_json)]
    variables += [dict(var, role="output") for var in json.loads(output_json)]
    return variables


@dataclass
class EnsureResult:
    created: list[str]  # human-readable labels of what was created (empty if all existed)
    repository_id: Optional[str] = None
    repository_folder_id: Optional[str] = None
    project_id: Optional[str] = None  # the project for the requested kind


def _attr(obj, name):
    """RestObj supports attribute and dict access; try both."""
    if obj is None:
        return None
    value = getattr(obj, name, None)
    if value is None and hasattr(obj, "get"):
        try:
            return obj.get(name)
        except Exception:
            return None
    return value


def ensure_repository_and_project(session, kind: str, core, responsible_party: str) -> EnsureResult:
    """Create the LLM Repository and the kind's Model Project if they do not
    exist yet. Idempotent - the returned EnsureResult.created lists whatever was
    created (empty when everything already existed) and carries the repository
    and project ids for the bootstrap-file generation in mdb setup.

    Creates the repository and project if absent; the authorization-group rules
    and builder seed files are produced separately by
    mdb setup (see authorization_rules_text / builder_seed)."""
    from sasctl.services import model_repository as mr

    created: list[str] = []
    repo_id = repo_folder = None
    existing_repo = mr.get_repository(REPOSITORY)
    if existing_repo is None:
        response = session.post(
            "/modelRepository/repositories",
            data=json.dumps({
                "name": REPOSITORY,
                "description": REPOSITORY_DESCRIPTION,
                "defaultRepository": False,
                "version": 2,
            }),
            headers={
                "Content-Type": "application/vnd.sas.models.repository+json",
                "Accept": "application/vnd.sas.models.repository+json",
            },
        )
        if response.status_code >= 300:
            # Lost a create race, or a genuine rights problem - re-fetch to tell
            # them apart before blaming permissions.
            existing_repo = mr.get_repository(REPOSITORY)
            if existing_repo is None:
                raise RuntimeError(
                    f"Could not create the '{REPOSITORY}' repository: HTTP {response.status_code} "
                    f"{response.text[:200]} - you may need a SAS administrator to grant repository rights."
                )
        else:
            created.append(f"repository '{REPOSITORY}'")
            body = response.json()
            repo_id, repo_folder = body.get("id"), body.get("folderId")
    if repo_id is None:
        repo_id, repo_folder = _attr(existing_repo, "id"), _attr(existing_repo, "folderId")

    meta = PROJECT_META[kind]
    existing_project = mr.get_project(meta["project"])
    if existing_project is None:
        project = mr.create_project(
            project=meta["project"],
            description=meta["description"],
            repository=REPOSITORY,
            variables=project_variables(core, kind),
            targetLevel="NOMINAL",
            targetVariable=meta["target_variable"],
            function=meta["function"],
            modelResponsibleParty=responsible_party,
            tags=meta["tags"],
        )
        created.append(f"project '{meta['project']}'")
        project_id = _attr(project, "id")
    else:
        project_id = _attr(existing_project, "id")
    return EnsureResult(created=created, repository_id=repo_id,
                        repository_folder_id=repo_folder, project_id=project_id)


def authorization_rules_text(repository_id: Optional[str], repository_folder_id: Optional[str]) -> str:
    """The sas-viya-cli-commands.txt content (LLM Consumers / Prompt Engineers
    groups plus the folder/repository authorization rules). The ids are filled
    in when known."""
    folder = repository_folder_id or "<repository-folder-id>"
    repo = repository_id or "<repository-id>"
    return (
        "# This script is written for Windows, update the commands accordingly\n"
        "# Each command comes with a description, please read it and the documentation before running anything\n\n"
        "# First a Custom Group is created called LLM Consumers - if you do not want use this group, skip this step and replace the name in subsequent commands\n"
        'sas-viya identities create-group --id LLMConsumers --name "LLM Consumers" --description "This group enables a general access to the LLM repository. This group is meant for anybody that requires access to it."\n'
        "# Add members to the LLM Consumers group\n"
        "sas-viya identities add-member --group-id LLMConsumers --group-member-id GroupYouWantToAdd\n\n"
        "# Second a Custom Group is created called Prompt Engineers - if you do not want use this group, skip this step and replace the name in subsequent commands\n"
        'sas-viya identities create-group --id PromptEngineers --name "Prompt Engineers" --description "This group enables its members to create, update and delete Prompt Engineering projects in the LLM repository"\n'
        "# Add members to the Prompt Engineers group\n"
        "sas-viya identities add-member --group-id PromptEngineers --group-member-id GroupYouWantToAdd\n\n"
        "# Create two rules that open up access to the LLM Repository for the LLM Consumers\n"
        f'sas-viya authorization create-rule -o /folders/folders/{folder} -g LLMConsumers -p Read,Add,Remove -d "Enables the LLM Consumers to interact with the LLM repository" --reason "You are not part of the LLM Consumers group"\n'
        f'sas-viya authorization create-rule --container-uri /folders/folders/{folder} -g LLMConsumers -p Read,Add,Update,Remove,Delete -d "Enables the LLM Consumers to interact with the LLM repository" --reason "You are not part of the LLM Consumers group"\n\n'
        "# Create a rule to enable the Prompt Engineers to create new projects in the LLM repository\n"
        f'sas-viya authorization create-rule -o /modelRepository/repositories/{repo} -g PromptEngineers -p Read,Add,Create,Update,Remove,Delete -d "Enables the group to create prompt engineering projects in the LLM repository" --reason "You are not part of the prompt engineering group"\n'
    )


def builder_seed(kind: str, repository_id: Optional[str], project_id: Optional[str],
                 scr_endpoint: str, deployment_type: str) -> dict:
    """The Prompt Builder / RAG Builder quick-start seed JSON
    (llm-prompt-builder.json for llm, rag-builder.json for embedding)."""
    if kind == "llm":
        return {
            "name": "LLM Prompt Builder", "id": "LPB", "width": 0, "type": "promptBuilder",
            "modelRepositoryID": repository_id or "", "llmProjectID": project_id or "",
            "SCREndpoint": scr_endpoint,
            "API_KEYS": {"Anthropic": "key-value", "OpenAI": "key-value", "Google": "key-value"},
            "deploymentType": deployment_type,
        }
    return {
        "name": "RAG Builder", "id": "RBO", "width": 0, "type": "ragBuilder",
        "modelRepositoryID": repository_id or "", "embeddingProjectID": project_id or "",
        "SCREndpoint": scr_endpoint, "deploymentType": deployment_type,
    }


@dataclass
class RegisterResult:
    action: str  # created | updated | skipped
    model_id: str
    url: str


def _num(value, default=0.0) -> float:
    try:
        if value in (None, "", "."):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def build_model_attributes(manifest: ModelManifest, folder: Path,
                           fact_row: dict | None, scr_endpoint: str) -> dict:
    """Model attributes: modelConfiguration.json plus the enrichment the
    register scripts add (llmModelType, provider, endPoint, costPerCall)."""
    attributes = json.loads((folder / "modelConfiguration.json").read_text(encoding="utf-8"))
    row = fact_row or {}
    attributes["llmModelType"] = attributes.get("llmModelType", "GPT")
    attributes["provider"] = row.get("provider", manifest.tags.provider_tag)
    attributes["endPoint"] = f"{scr_endpoint}/{manifest.model_id}/{manifest.model_id}"
    cost_type = row.get("cost_type", manifest.metadata.pricing.cost_type)
    if cost_type == "Seconds":
        attributes["costPerCall"] = _num(row.get("second_cost"))
    else:
        attributes["costPerCall"] = (
            _num(row.get("input_token_price")) + _num(row.get("output_token_price"))
        ) / 2
    return attributes


def content_files(manifest: ModelManifest, folder: Path) -> list[tuple[Path, str, Optional[str]]]:
    """(path, upload name, role) for every model content, in upload order."""
    files: list[tuple[Path, str, Optional[str]]] = [
        (folder / effective_score_file(manifest), f"{manifest.model_id}.py", "score"),
        (folder / "requirements.json", "requirements.json", "python pickle"),
        (folder / "outputVar.json", "outputVar.json", None),
        (folder / "inputVar.json", "inputVar.json", None),
        (folder / "options.json", "options.json", "documentation"),
        (folder / MANIFEST_FILENAME, MANIFEST_FILENAME, "documentation"),
    ]
    if (folder / "Model-Card.pdf").is_file():
        files.append((folder / "Model-Card.pdf", "Model-Card.pdf", "documentation"))
    elif (folder / "Model-Card.md").is_file():
        files.append((folder / "Model-Card.md", "Model-Card.md", "documentation"))
    for name in TOKENIZER_FILES:
        if (folder / name).is_file():
            files.append((folder / name, name, "documentation"))
    return [(path, name, role) for path, name, role in files if path.is_file()]


def _put_tags(session, model_id: str, tags: list[str]) -> None:
    from sasctl.services import model_repository as mr

    details = mr.get_model_details(model_id)
    body = dict(details.items())
    body["tags"] = tags
    session.put(
        f"/modelRepository/models/{details.id}",
        data=json.dumps(body),
        headers={
            "Content-Type": "application/vnd.sas.models.model+json",
            "Accept": "application/vnd.sas.models.model+json",
            "If-Match": details._headers["ETag"],
        },
    )


def register_model(session, manifest: ModelManifest, folder: Path, fact_row: dict | None,
                   scr_endpoint: str, update: bool = False) -> RegisterResult:
    from sasctl.services import model_repository as mr

    project = KIND_PROJECT[manifest.kind]
    # The repository and project are ensured by the caller (mdb register/setup run
    # ensure_repository_and_project first); this stays a safety net for direct callers.
    if mr.get_repository(REPOSITORY) is None:
        raise RuntimeError(f"SAS Model Manager repository '{REPOSITORY}' does not exist - run 'mdb setup' first.")
    if mr.get_project(project) is None:
        raise RuntimeError(f"SAS Model Manager project '{project}' does not exist - run 'mdb setup' first.")

    attributes = build_model_attributes(manifest, folder, fact_row, scr_endpoint)
    existing = mr.get_model(manifest.model_id)

    def _link(model_id: str) -> str:
        return f"/SASModelManager/models/{model_id}"

    if existing is None:
        model = mr.create_model(model=attributes, project=project)
        time.sleep(1)
        for path, name, role in content_files(manifest, folder):
            with path.open("rb") as handle:
                if role:
                    mr.add_model_content(model, handle, name=name, role=role)
                else:
                    mr.add_model_content(model, handle, name=name)
        _put_tags(session, model.id, attributes["tags"])
        return RegisterResult("created", manifest.model_id, _link(model.id))

    if not update:
        return RegisterResult("skipped", manifest.model_id, _link(existing.id))

    # Update path (Prompt Builder pattern): new minor version, then replace
    # each content in place, then refresh attributes + tags via ETag PUT.
    model_id = existing.id
    version = session.post(
        f"/modelRepository/models/{model_id}/modelVersions",
        data=json.dumps({"option": "minor"}),
        headers={"Content-Type": "application/vnd.sas.models.model.version+json"},
    )
    if version.status_code >= 300:
        # Non-fatal: some releases version implicitly on content change
        print(f"note: model version bump returned HTTP {version.status_code} - continuing")
    for path, name, role in content_files(manifest, folder):
        query = f"name={name}&onConflict=update" + (f"&role={role}" if role else "")
        response = session.post(
            f"/modelRepository/models/{model_id}/contents?{query}",
            files={"files": (name, path.read_bytes())},
        )
        if response.status_code >= 300:
            raise RuntimeError(f"Updating content {name} failed: HTTP {response.status_code} {response.text[:300]}")
    details = mr.get_model_details(model_id)
    body = dict(details.items())
    body.update(attributes)
    response = session.put(
        f"/modelRepository/models/{model_id}",
        data=json.dumps(body),
        headers={
            "Content-Type": "application/vnd.sas.models.model+json",
            "Accept": "application/vnd.sas.models.model+json",
            "If-Match": details._headers["ETag"],
        },
    )
    if response.status_code >= 300:
        raise RuntimeError(f"Updating model attributes failed: HTTP {response.status_code} {response.text[:300]}")
    return RegisterResult("updated", manifest.model_id, _link(model_id))


def unregister_model(session, model_id: str) -> str:
    """Delete a registered model from SAS Model Manager. Returns 'deleted' or
    'absent' (nothing registered). The local definition folder is untouched.

    Note: this removes only the Model Manager registration. A container image
    already published to an SCR destination is NOT removed and must be deleted
    separately at the publishing destination."""
    from sasctl.services import model_repository as mr

    existing = mr.get_model(model_id)
    if existing is None:
        return "absent"
    mr.delete_model(existing.id)
    return "deleted"


SCR_DESTINATION_TYPES = {"azure", "aws", "gcp", "privatedocker", "AWS", "GCP", "privateDocker"}


def publish_model(session, model_name: str, destination: str,
                  wait: bool = False, timeout_s: int = 1200) -> str:
    """Async publish; returns the terminal state ('requested' when not waiting)."""
    from sasctl.services import model_publish as mp
    from sasctl.services import model_repository as mr

    dest = mp.get_destination(destination)
    if dest is None:
        raise RuntimeError(f"Publishing destination '{destination}' does not exist.")
    if dest.destinationType not in SCR_DESTINATION_TYPES:
        raise RuntimeError(f"'{destination}' ({dest.destinationType}) is not an SCR container destination.")
    details = mr.get_model_details(model_name)
    sizing = (set(details["tags"]) & {"small", "medium", "large"} or {"small"}).pop()
    payload = json.dumps({
        "destinationName": destination,
        "modelContents": [{
            "modelName": model_name,
            "publishLevel": "model",
            "sourceUri": f"/modelRepository/models/{details['id']}",
        }],
        "name": model_name,
        "notes": "Published by LLM Framework",
        "tags": [sizing],
    })
    response = session.post(
        "/modelManagement/publish?force=true",
        data=payload,
        headers={
            "Content-Type": "application/vnd.sas.models.publishing.request.asynchronous+json",
            "Accept": "application/vnd.sas.models.publishing.publish+json",
        },
    )
    if response.status_code != 201:
        raise RuntimeError(f"Publish request failed: HTTP {response.status_code} {response.text[:300]}")
    if not wait:
        return "requested"
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(15)
        jobs = session.get("/modelPublish/models?limit=20&sortBy=creationTimeStamp:descending").json()
        for item in jobs.get("items", []):
            if item.get("name") == model_name:
                state = item.get("state", "")
                if state in ("completed", "failed", "cancelled"):
                    return state
                break
    return "timeout"
