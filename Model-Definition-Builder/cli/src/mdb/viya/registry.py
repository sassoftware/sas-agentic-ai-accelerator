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
TOKENIZER_FILES = ("tokenizer_config.json", "special_tokens_map.json", "tokenizer.json")


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
    if mr.get_repository(REPOSITORY) is None:
        raise RuntimeError(f"SAS Model Manager repository '{REPOSITORY}' does not exist - run the setup first.")
    if mr.get_project(project) is None:
        raise RuntimeError(f"SAS Model Manager project '{project}' does not exist - run the setup first.")

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
