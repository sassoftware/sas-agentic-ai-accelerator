# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Reading and writing VA report content, for builder options (core/options.py)."""
from __future__ import annotations

from typing import Optional

#: Report content travels as BIRD XML on both legs - core/options.py rewrites
#: XML. The service serves the same report as JSON when asked, so the read pins
#: the representation instead of trusting the server default, and the write
#: declares the same one: declaring +json for XML bytes is a 400 ("the content
#: is invalid, possibly in the wrong format"), which every options-restore hit.
CONTENT_MEDIA = "application/vnd.sas.report.content+xml"


def _failure(what: str, response) -> RuntimeError:
    # A SAS REST error body carries errorCode / message / details - the part
    # that turns "400 Bad Request" into a diagnosis, so it travels with the error.
    return RuntimeError(f"{what}: HTTP {response.status_code} {response.text[:300]}")


def find_report(session, name: str, strict: bool = False) -> Optional[dict]:
    """The report with this exact name anywhere on the system, or None.
    The Builder commands ask `find_report_at` instead: the report at its
    place in the content folder.

    Filtered server-side and matched exactly here: `contains` would also
    return "Prompt Builder (old)" and picking the first hit would silently
    configure the wrong report.

    A lookup the service does not answer gives None as well - unless
    `strict`: then it is an error, for the caller that must not take "could
    not ask" for "there is no such report" (`mdb builders-import`, before it
    replaces a report).
    """
    response = session.get(
        "/reports/reports",
        params={"filter": f'eq(name,"{name}")', "limit": 20},
    )
    if not response.ok:
        if strict:
            raise _failure(f"Looking up the report '{name}' failed", response)
        return None
    for item in response.json().get("items", []):
        if item.get("name") == name:
            return item
    return None


def find_report_at(session, folder_path: str, name: str, strict: bool = False) -> Optional[dict]:
    """The report called `name` in the SAS Content folder `folder_path`, or
    None - {"id", "name", "uri", "path"}.

    The Builder's report is the one at its place in the content folder, not
    the first report of that name anywhere on the system: a copy somebody
    saved in their own folder is not read from and not written to. A member
    that is a reference is not the report. A folder or a lookup the service
    does not answer gives None - unless `strict`, for the caller that must
    not take "could not ask" for "there is no such report"; two reports of
    the name in the folder are an error then too.
    """
    path = f"{folder_path.rstrip('/')}/{name}"
    response = session.get("/folders/folders/@item", params={"path": folder_path})
    if response.status_code == 404:
        return None
    if not response.ok:
        if strict:
            raise _failure(f"Looking up the folder {folder_path} of the report '{name}' failed", response)
        return None
    folder_id = response.json().get("id")
    members, start = [], 0
    while True:
        page = session.get(f"/folders/folders/{folder_id}/members", params={"limit": 200, "start": start})
        if not page.ok:
            if strict:
                raise _failure(f"Listing the folder {folder_path} of the report '{name}' failed", page)
            return None
        items = page.json().get("items") or []
        members.extend(items)
        start += len(items)
        if not items or start >= int(page.json().get("count") or 0):
            break
    found = [m for m in members if m.get("name") == name and str(m.get("uri") or "").startswith("/reports/reports/")
             and str(m.get("type") or "child").lower() != "reference"]
    if len(found) > 1 and strict:
        raise RuntimeError(f"{path}: {len(found)} reports of that name are in the folder - nothing says which one "
                           "holds the options")
    if not found:
        return None
    uri = str(found[0]["uri"])
    return {"id": uri.rstrip("/").rsplit("/", 1)[-1], "name": name, "uri": uri, "path": path}


def get_content(session, report_id: str) -> tuple:
    """(content_text, etag). The ETag guards the write against a concurrent edit."""
    response = session.get(
        f"/reports/reports/{report_id}/content",
        headers={"Accept": CONTENT_MEDIA},
    )
    if not response.ok:
        raise _failure("Reading the report content failed", response)
    return response.text, response.headers.get("ETag", "")


def put_content(session, report_id: str, content: str, etag: str = "") -> None:
    """Replace a report's content.

    If-Match when the read gave us an ETag: someone editing the report in VA
    while an admin restores options is rare, but silently discarding their
    work would be unrecoverable, and a 412 is a message we can pass on.
    """
    headers = {
        "Content-Type": CONTENT_MEDIA,
        "Accept": "application/json, text/plain",
    }
    if etag:
        headers["If-Match"] = etag
    response = session.put(
        f"/reports/reports/{report_id}/content", data=content.encode("utf-8"),
        headers=headers,
    )
    if not response.ok:
        raise _failure("Writing the report content failed", response)
