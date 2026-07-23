# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Load the fact-sheet CSVs into CAS via the casManagement REST API.

The Python equivalent of Load-Fact-Sheets.sas: each sheet is uploaded with
global scope (which promotes it, so every session sees it) and then saved to the
caslib's data source on disk. Any table of the same name is dropped first so the
reload is clean. The table names match what the SAS Visual Analytics monitoring
report binds to (LLM_FACT_SHEET / EMBEDDING_FACT_SHEET).

Only the REST surface sas-mas-scorer uses is needed here - no SWAT/CAS binary
connection - so the same sasctl session the register/publish commands use works.
"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_SERVER = "cas-shared-default"
DEFAULT_CASLIB = "Public"
# Table names the monitoring report already binds to (uppercase, per the SAS script).
TABLE_BY_KIND = {"llm": "LLM_FACT_SHEET", "embedding": "EMBEDDING_FACT_SHEET"}


def resolve_server(session, server: str | None) -> str:
    """Return the CAS server to target: the explicit choice, else the default
    (cas-shared-default) if present, else the first server the platform lists."""
    if server:
        return server
    response = session.get("/casManagement/servers?limit=100")
    if response.status_code < 300:
        names = [item.get("name") for item in response.json().get("items", []) if item.get("name")]
        if DEFAULT_SERVER in names:
            return DEFAULT_SERVER
        if names:
            return names[0]
    return DEFAULT_SERVER


def _base(server: str, caslib: str) -> str:
    return f"/casManagement/servers/{server}/caslibs/{caslib}/tables"


def _drop_table(session, server: str, caslib: str, table: str) -> bool:
    """Unload an existing loaded table so the reload does not hit a
    "table already exists" (LOADTABLE_EXISTS) conflict. The saved source on disk,
    if any, is overwritten by the later save-with-replace - so a plain unload is
    the right "drop". Returns True if a loaded table was unloaded, False if there
    was nothing loaded (a 404 from state update is not an error)."""
    response = session.put(f"{_base(server, caslib)}/{table}/state?value=unloaded")
    return response.status_code < 300


def _upload_table(session, server: str, caslib: str, table: str, csv_path: Path) -> None:
    """Upload the CSV as a global-scope (promoted) table. The file field goes
    last, per the casManagement multipart contract; requests sets the multipart
    Content-Type/boundary when files= is passed (as the content upload does)."""
    response = session.post(
        _base(server, caslib),
        data={
            "tableName": table,
            "format": "csv",
            "containsHeaderRow": "true",
            "scope": "global",
        },
        files={"file": (f"{table}.csv", csv_path.read_bytes(), "text/csv")},
    )
    if response.status_code >= 300:
        raise RuntimeError(
            f"Uploading {table} to CAS failed: HTTP {response.status_code} {response.text[:300]}"
        )


def _save_table(session, server: str, caslib: str, table: str) -> None:
    """Persist the loaded table to the caslib's data source on disk (replace)."""
    response = session.post(
        f"{_base(server, caslib)}/{table}",
        data=json.dumps({"replace": True, "format": "sashdat"}),
        headers={
            "Content-Type": "application/vnd.sas.cas.table.save.request+json",
            "Accept": "application/json",
        },
    )
    if response.status_code >= 300:
        raise RuntimeError(
            f"Saving {table} to disk failed: HTTP {response.status_code} {response.text[:300]}"
        )


def load_fact_sheet(session, csv_path: Path, kind: str, caslib: str, server: str) -> dict:
    """Drop any existing table, upload+promote the CSV, and save it to disk.
    Returns {'table': name, 'dropped': bool}."""
    table = TABLE_BY_KIND[kind]
    dropped = _drop_table(session, server, caslib, table)
    _upload_table(session, server, caslib, table, csv_path)
    _save_table(session, server, caslib, table)
    return {"table": table, "dropped": dropped}
