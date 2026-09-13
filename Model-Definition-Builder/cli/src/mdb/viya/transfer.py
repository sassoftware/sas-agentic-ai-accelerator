# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The transfer service: export a SAS Content folder to a package, import one.

What `sas-viya transfer export` / `upload` / `import` do, over the same REST
surface, so `mdb package-export` and `mdb builders-import` need no CLI binary
and can do the two things the CLI cannot on its own:

* rewrite the import MAPPING before the job starts - the package's data-source
  connector (`server=...;library=...;table=...`) is retargeted at the release
  table this deployment loads, and the Data-Driven Content URL's placeholder
  host becomes the real one - so a report lands bound and pointing home;
* strip the exporting environment's hostname from a fresh export before it is
  written into the repository (core/packages.py does the rewriting).

Request shapes are the ones the service records on its own jobs
(`GET /transfer/importJobs` shows `request`): export request version 1 with
`items` and `options`, import request version 2 with `packageUri` and an
inline `mapping` of the shape `GET /transfer/packages/{id}/mapping` returns.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote, urlparse

from ..core.packages import PLACEHOLDER_HOST

EXPORT_REQUEST = "application/vnd.sas.transfer.export.request+json"
IMPORT_REQUEST = "application/vnd.sas.transfer.import.request+json"
PACKAGE_MEDIA = "application/vnd.sas.transfer.package+json"
EXPORT_OPTIONS = {"includeDependencies": "true", "includeRules": "true"}
RUNNING_STATES = ("pending", "queued", "running")
DEFAULT_TIMEOUT = 600


def _failure(what: str, response) -> RuntimeError:
    return RuntimeError(f"{what}: HTTP {response.status_code} {response.text[:300]}")


# ---------------------------------------------------------------------------
# folders
# ---------------------------------------------------------------------------
def folder_by_path(session, path: str) -> dict:
    """The SAS Content folder at `path` (e.g. '/SAS Agentic AI Accelerator/Prompt Builder')."""
    response = session.get("/folders/folders/@item", params={"path": path})
    if response.status_code == 404:
        raise RuntimeError(f"no SAS Content folder at {path}")
    if not response.ok:
        raise _failure(f"Looking up the folder {path} failed", response)
    return response.json()


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------
def wait_for_job(session, job_uri: str, timeout: float = DEFAULT_TIMEOUT, poll: float = 2.0,
                 sleep: Callable[[float], None] = time.sleep) -> dict:
    """Poll a transfer job until it leaves the running states; returns the job."""
    deadline = time.monotonic() + timeout
    while True:
        response = session.get(job_uri)
        if not response.ok:
            raise _failure(f"Reading the job {job_uri} failed", response)
        job = response.json()
        if str(job.get("state", "")).lower() not in RUNNING_STATES:
            return job
        if time.monotonic() >= deadline:
            raise RuntimeError(f"the transfer job {job_uri} is still {job.get('state')} after {int(timeout)}s")
        sleep(poll)


def job_messages(session, job: dict) -> list[str]:
    """One line per task of a finished job that has something to say."""
    self_uri = next((l.get("href") for l in job.get("links", []) if l.get("rel") == "self"), "")
    if not self_uri:
        return []
    response = session.get(f"{self_uri}/tasks", params={"limit": 200})
    if not response.ok:
        return []
    lines = []
    for task in response.json().get("items", []):
        state = str(task.get("state", "")).lower()
        message = task.get("message") or ""
        if state and state != "completed" or message:
            lines.append(f"{task.get('name') or task.get('resourceName') or task.get('id')}: {state} {message}".strip())
    return lines


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------
def start_export(session, name: str, item_uris: list[str], description: str = "") -> dict:
    body = {"version": 1, "name": name, "description": description,
            "items": list(item_uris), "options": dict(EXPORT_OPTIONS)}
    response = session.post("/transfer/exportJobs", data=json.dumps(body),
                            headers={"Content-Type": EXPORT_REQUEST, "Accept": "application/json"})
    if not response.ok:
        raise _failure("Starting the export failed", response)
    return response.json()


def download_package(session, package_uri: str) -> str:
    """The full package as the CLI writes it: pretty JSON, two-space indent."""
    response = session.get(package_uri, headers={"Accept": PACKAGE_MEDIA})
    if not response.ok:
        raise _failure(f"Downloading the package {package_uri} failed", response)
    return json.dumps(response.json(), indent=2, ensure_ascii=False) + "\n"


def delete_package(session, package_uri: str) -> None:
    session.delete(package_uri)


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------
def upload_package(session, path: Path) -> dict:
    """Upload a package file; returns the package summary (id, name)."""
    response = session.post(
        "/transfer/packages",
        files={"file": (path.name, path.read_bytes(), "application/json")},
        headers={"Accept": "application/json"},
    )
    if not response.ok:
        raise _failure(f"Uploading {path.name} failed", response)
    return response.json()


def get_mapping(session, package_id: str) -> dict:
    response = session.get(f"/transfer/packages/{quote(package_id, safe='')}/mapping")
    if not response.ok:
        raise _failure("Reading the import mapping failed", response)
    return response.json()


def parse_table_spec(spec: str) -> dict:
    """'server=cas-shared-default;library=Public;table=X' -> {'server':..., 'library':..., 'table':...}"""
    return dict(part.split("=", 1) for part in spec.split(";") if "=" in part)


def table_spec(server: str, library: str, table: str) -> str:
    return f"server={server};library={library};table={table}"


def host_of(url_or_host: str) -> str:
    parsed = urlparse(url_or_host if "://" in url_or_host else f"https://{url_or_host}")
    return parsed.netloc or url_or_host


def rehost_mapping(mapping: dict, host: str, releases_from: str, releases_to: str,
                   placeholder: str = PLACEHOLDER_HOST) -> tuple[dict, list[str]]:
    """The import mapping this deployment needs: the placeholder host in every
    substitution becomes `host`, and each table connector whose source names
    the release table (any server or library) is retargeted at `releases_to`.

    Returns (mapping, changes) - the changes as sentences for the console. Other
    table connectors are left as exported and reported, because retargeting a
    report at a table with different columns breaks its data items.
    """
    changes: list[str] = []
    for entry in mapping.get("substitutions") or []:
        for prop in entry.get("properties") or []:
            value = str(prop.get("value") or "")
            if placeholder in value:
                prop["value"] = value.replace(placeholder, host)
                changes.append(f"{entry.get('resourceName') or entry.get('resourceId')}: "
                               f"{prop.get('name')} -> host {host}")
    for connector in (mapping.get("connectors") or {}).get("table") or []:
        source = parse_table_spec(str(connector.get("source") or ""))
        if source.get("table", "").upper() == releases_from.upper():
            if connector.get("target") != releases_to:
                connector["target"] = releases_to
                changes.append(f"data source {connector.get('source')} -> {releases_to}")
        else:
            changes.append(f"data source {connector.get('source')} left as exported "
                           "(not the release table)")
    return mapping, changes


def start_import(session, name: str, package_uri: str, mapping: Optional[dict] = None,
                 description: str = "") -> dict:
    body: dict = {"version": 2, "name": name, "description": description,
                  "packageUri": package_uri, "options": {}}
    if mapping is not None:
        body["mapping"] = mapping
    response = session.post("/transfer/importJobs", data=json.dumps(body),
                            headers={"Content-Type": IMPORT_REQUEST, "Accept": "application/json"})
    if not response.ok:
        raise _failure("Starting the import failed", response)
    return response.json()


def job_uri(job: dict) -> str:
    for link in job.get("links", []):
        if link.get("rel") == "self":
            return link["href"]
    return f"/transfer/importJobs/{job.get('id')}"
