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


def find_report(session, name: str) -> Optional[dict]:
    """The report with this exact name, or None.

    Filtered server-side and matched exactly here: `contains` would also
    return "RAG Builder (old)" and picking the first hit would silently
    configure the wrong report.
    """
    response = session.get(
        "/reports/reports",
        params={"filter": f'eq(name,"{name}")', "limit": 20},
    )
    if not response.ok:
        return None
    for item in response.json().get("items", []):
        if item.get("name") == name:
            return item
    return None


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
