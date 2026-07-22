# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""One parametrized register/update/publish path for both definition kinds.

Creation preserves the load-bearing Model Manager conventions the framework
has always used (content roles incl. requirements.json as 'python pickle',
endPoint = {scr}/{id}/{id}, costPerCall enrichment, tag PUT with ETag). The
update path uses the Prompt Builder's production-proven
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


def _refresh_project_responsible(session, project, responsible_party: str) -> None:
    """Set an existing project's responsible party (create_project only sets it
    at creation). Best-effort: a failure here never blocks register/setup."""
    project_id = _attr(project, "id")
    if not project_id:
        return
    got = session.get(f"/modelRepository/projects/{project_id}")
    if got.status_code >= 300:
        return
    body = got.json()
    if body.get("modelResponsibleParty") == responsible_party:
        return
    body["modelResponsibleParty"] = responsible_party
    session.put(
        f"/modelRepository/projects/{project_id}",
        data=json.dumps(body),
        headers={
            "Content-Type": "application/vnd.sas.models.project+json",
            "Accept": "application/vnd.sas.models.project+json",
            "If-Match": got.headers.get("ETag", ""),
        },
    )


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
        # The responsible party is the project-level owner that actually takes
        # effect; refresh it on an already-existing project too (best-effort).
        if responsible_party:
            try:
                _refresh_project_responsible(session, existing_project, responsible_party)
            except Exception:
                pass
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


# Substrings (checked in order) that map a model to its LLM family. First match
# wins; falls back to the provider tag. Covers the shipped fleet and is easy to
# extend as new families are added.
_FAMILY_TOKENS: list[tuple[str, str]] = [
    ("claude", "Claude"), ("gpt", "GPT"), ("gemini", "Gemini"), ("gemma", "Gemma"),
    ("phi", "Phi"), ("qwen", "Qwen"), ("llama", "Llama"), ("mixtral", "Mistral"),
    ("mistral", "Mistral"), ("nemo", "Mistral"), ("smollm", "SmolLM"),
    ("granite", "Granite"), ("bge", "BGE"), ("minilm", "MiniLM"), ("voyage", "Voyage"),
    ("titan", "Titan"), ("nova", "Nova"), ("text-embedding", "OpenAI"),
    ("text_embedding", "OpenAI"),
]


def _llm_model_type(manifest: ModelManifest) -> str:
    """The model family (Claude/GPT/Gemini/Phi/…), derived from the model id and
    provider model string; falls back to the provider tag."""
    haystack = " ".join(
        part for part in (manifest.model_id, manifest.provider.model_version, manifest.display_name)
        if part
    ).lower()
    for token, family in _FAMILY_TOKENS:
        if token in haystack:
            return family
    return manifest.tags.provider_tag or "Other"


def _model_card_chart(kind: str) -> Optional[str]:
    """The <sas-report> embed for the model card's custom chart, when configured.
    The report URI is environment-specific and per-kind, so it comes from the
    environment: SAS_LLM_MODEL_CARD_REPORT_URI for llm models and
    SAS_EMBEDDING_MODEL_CARD_REPORT_URI for embedding models (the legacy
    SAS_MODEL_CARD_REPORT_URI is still honored as a fallback). The host defaults
    to SAS_VIYA_URL."""
    import os

    kind_var = {
        "llm": "SAS_LLM_MODEL_CARD_REPORT_URI",
        "embedding": "SAS_EMBEDDING_MODEL_CARD_REPORT_URI",
    }.get(kind)
    uri = (os.environ.get(kind_var) if kind_var else None) or os.environ.get("SAS_MODEL_CARD_REPORT_URI")
    if not uri:
        return None
    host = (os.environ.get("SAS_VIYA_URL", "") or "https://<sas-viya-host>").rstrip("/")
    return f'<sas-report url="{host}" reportUri="{uri}"></sas-report>'


def _price(preferred: Optional[float], raw) -> Optional[float]:
    """Prefer the manifest's per-token/second price; fall back to the fact-sheet
    value; None when neither is set (so the attribute is simply absent-of-value)."""
    if preferred is not None:
        return preferred
    if raw in (None, "", "."):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def build_model_attributes(manifest: ModelManifest, folder: Path,
                           fact_row: dict | None, scr_endpoint: str) -> dict:
    """Model attributes: modelConfiguration.json plus the register-time
    enrichment (family, provider, deployment id, SCR endpoint, per-token/second
    costs, the response probability variable, and the optional model-card chart).
    Lifecycle attributes (modelStatus/approvalState) are set by the caller so an
    --update does not reset a model that is already deployed."""
    attributes = json.loads((folder / "modelConfiguration.json").read_text(encoding="utf-8"))
    row = fact_row or {}
    pricing = manifest.metadata.pricing
    # SAS Model Manager's field is spelled "llmodelType" (lowercase m); writing
    # the camelCase "llmModelType" is silently dropped on the model PUT.
    attributes["llmodelType"] = _llm_model_type(manifest)
    attributes["provider"] = row.get("provider", manifest.tags.provider_tag)
    attributes["deploymentId"] = manifest.provider.model_version
    attributes["endPoint"] = f"{scr_endpoint}/{manifest.model_id}/{manifest.model_id}"
    # Precise per-token / per-second costs (the lossy averaged costPerCall is kept
    # for continuity with existing cost-monitoring reports).
    attributes["inputTokenCount"] = _price(pricing.input_token_price, row.get("input_token_price"))
    attributes["outputTokenCount"] = _price(pricing.output_token_price, row.get("output_token_price"))
    attributes["hostingCosts"] = _price(pricing.second_cost, row.get("second_cost"))
    cost_type = row.get("cost_type", pricing.cost_type)
    if cost_type == "Seconds":
        attributes["costPerCall"] = _num(row.get("second_cost"), pricing.second_cost or 0.0)
    else:
        attributes["costPerCall"] = (
            _num(row.get("input_token_price"), pricing.input_token_price or 0.0)
            + _num(row.get("output_token_price"), pricing.output_token_price or 0.0)
        ) / 2
    # The event/probability output variable mirrors the model's target variable.
    attributes["eventProbVar"] = attributes.get("targetVariable", "response")
    chart = _model_card_chart(manifest.kind)
    if chart:
        attributes["modelCardCustomChartReport"] = chart
        attributes["modelCardCustomChartEnabled"] = True
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


def _delete_model_variables(session, model_id: str) -> None:
    """Delete every input/output variable currently on a model, so a subsequent
    inputVar/outputVar re-import does not duplicate them."""
    response = session.get(f"/modelRepository/models/{model_id}/variables?start=0&limit=10000")
    if response.status_code >= 300:
        return
    for variable in response.json().get("items", []):
        variable_id = variable.get("id")
        if variable_id:
            session.delete(f"/modelRepository/models/{model_id}/variables/{variable_id}")


def ensure_model_lifecycle(session, model_name: str,
                           model_status: Optional[str] = None,
                           approval_state: Optional[str] = None) -> bool:
    """Set a registered model's modelStatus / approvalState if they differ.
    Returns True when a change was written, False when the model is not
    registered or already in the requested state."""
    from sasctl.services import model_repository as mr

    existing = mr.get_model(model_name)
    if existing is None:
        return False
    details = mr.get_model_details(_attr(existing, "id"))
    body = dict(details.items())
    changed = False
    if model_status and body.get("modelStatus") != model_status:
        body["modelStatus"] = model_status
        changed = True
    if approval_state and body.get("approvalState") != approval_state:
        body["approvalState"] = approval_state
        changed = True
    if not changed:
        return False
    response = session.put(
        f"/modelRepository/models/{_attr(details, 'id')}",
        data=json.dumps(body),
        headers={
            "Content-Type": "application/vnd.sas.models.model+json",
            "Accept": "application/vnd.sas.models.model+json",
            "If-Match": details._headers["ETag"],
        },
    )
    if response.status_code >= 300:
        raise RuntimeError(f"Updating model lifecycle failed: HTTP {response.status_code} {response.text[:200]}")
    return True


def list_registered_models(session, kind: str) -> list[dict]:
    """Registered models in a kind's project, with the lifecycle/enrichment
    attributes for a status overview. Returns [] when the project is absent.

    The model-list summary is inconsistent about custom attributes (it omits
    llmodelType entirely and returns deploymentId/endPoint only for some
    models), so each model's full detail is fetched to populate the columns
    reliably."""
    from sasctl.services import model_repository as mr

    project = mr.get_project(KIND_PROJECT[kind])
    if project is None:
        return []
    project_id = _attr(project, "id")
    response = session.get(
        f"/modelRepository/models?filter=eq(projectId,'{project_id}')&limit=1000"
    )
    if response.status_code >= 300:
        return []
    rows: list[dict] = []
    for item in response.json().get("items", []):
        model_id = item.get("id")
        detail = item
        if model_id:
            got = session.get(f"/modelRepository/models/{model_id}")
            if got.status_code < 300:
                detail = got.json()
        rows.append({
            "model_id": detail.get("name", item.get("name", "")),
            "kind": kind,
            "provider": detail.get("provider", ""),
            "llmModelType": detail.get("llmodelType", ""),
            "deploymentId": detail.get("deploymentId", ""),
            "modelStatus": detail.get("modelStatus", ""),
            "approvalState": detail.get("approvalState", ""),
            "endPoint": detail.get("endPoint", ""),
        })
    return rows


# The modelConfiguration.json fields, all of which are stored as top-level model
# attributes in SAS Model Manager (so a pulled model can rebuild the file from
# its attributes). scoreCodeFile is derived from the score-role content instead.
_MODEL_CONFIG_KEYS = (
    "name", "description", "toolVersion", "targetVariable", "targetLevel",
    "trainCodeType", "modeler", "function", "algorithm", "tool", "scoreCodeType",
    "tags", "modelPurpose", "intendedUse", "expectedBenefit", "outOfScopeUseCases",
    "limitations",
)


@dataclass
class PullResult:
    model_id: str
    kind: str
    files: list[str]
    had_definition: bool       # definition.yaml was stored as model content
    reconstructed_config: bool  # modelConfiguration.json rebuilt from attributes


def _model_config_from_attributes(body: dict, score_file: str) -> dict:
    """Rebuild modelConfiguration.json from a registered model's attributes."""
    config: dict = {"name": body.get("name"), "scoreCodeFile": score_file}
    for key in _MODEL_CONFIG_KEYS:
        if key == "name":
            continue
        value = body.get(key)
        if value not in (None, ""):
            config[key] = value
    config.setdefault("champion", bool(body.get("champion") or False))
    return config


def pull_model(session, model_id: str, defs_dir_for, force: bool = False) -> PullResult:
    """Recreate a local definition folder from a model registered in SAS Model
    Manager (the reverse of register): download its content and, when the model
    predates mdb (no stored definition.yaml), rebuild modelConfiguration.json
    from its attributes so the folder matches a legacy hand-written definition.
    `defs_dir_for(kind)` returns the definitions directory for the model's kind."""
    from sasctl.services import model_repository as mr

    existing = mr.get_model(model_id)
    if existing is None:
        raise RuntimeError(f"'{model_id}' is not registered in SAS Model Manager.")
    model_ref = _attr(existing, "id")
    body = dict(mr.get_model_details(model_ref).items())
    kind = "embedding" if body.get("function") == "embedding" else "llm"
    folder = defs_dir_for(kind) / model_id

    listing = session.get(f"/modelRepository/models/{model_ref}/contents")
    items = listing.json().get("items", []) if listing.status_code < 300 else []

    if folder.exists() and any(folder.iterdir()) and not force:
        raise RuntimeError(f"{folder} already exists and is not empty - pass --force to overwrite.")
    folder.mkdir(parents=True, exist_ok=True)

    files: list[str] = []
    score_file = None
    had_definition = False
    for item in items:
        name, content_id = item.get("name"), item.get("id")
        if not name or not content_id:
            continue
        if item.get("role") == "score":
            score_file = name
        if name == MANIFEST_FILENAME:
            had_definition = True
        response = session.get(f"/modelRepository/models/{model_ref}/contents/{content_id}/content")
        if response.status_code >= 300:
            continue
        (folder / name).write_bytes(response.content)
        files.append(name)

    reconstructed = False
    if not (folder / "modelConfiguration.json").is_file():
        config = _model_config_from_attributes(body, score_file or f"{model_id}.py")
        (folder / "modelConfiguration.json").write_text(
            json.dumps(config, indent=4) + "\n", encoding="utf-8")
        files.append("modelConfiguration.json")
        reconstructed = True
    return PullResult(model_id, kind, sorted(files), had_definition, reconstructed)


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
        # A freshly registered model enters the lifecycle awaiting validation and
        # approval; publish/retire advance these (build_model_attributes leaves
        # them out so an --update never resets an already-deployed model).
        attributes["modelStatus"] = "ready for validation"
        attributes["approvalState"] = "awaiting approval"
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
    # Re-uploading inputVar.json/outputVar.json makes Model Manager re-import the
    # variables; without clearing the existing ones first they accumulate as
    # duplicates on every --update. Delete them before the content upload
    # re-creates them (the Prompt Builder's manifest flow does the same).
    _delete_model_variables(session, model_id)
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
    # Backfill the lifecycle for models registered before it existed (notably the
    # embedding models, which never carried modelStatus/approvalState). Fill only
    # when absent so an --update never resets a model that is already deployed or
    # retired; publish/retire advance these from the register defaults.
    if not body.get("modelStatus"):
        body["modelStatus"] = "ready for validation"
    if not body.get("approvalState"):
        body["approvalState"] = "awaiting approval"
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
