# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Audit the models registered in SAS Model Manager for the attributes and
lifecycle that mdb 1.6.0 populates, and flag duplicated input/output variables.

Read-only: it performs GETs only, never writes. For each registered model it
fetches the full model detail (the list summary omits custom attributes such as
llmodelType) plus the variable list, and reports what a pre-1.6.0 registration
is missing and the exact remediation command.

Run from inside the repository clone (so the .env is found) after installing
mdb with the [viya] extra:

    python Model-Definition-Builder/.claude/skills/model-audit/audit-models.py
    python .../audit-models.py --json      # machine-readable

Connection settings come from SAS_VIYA_URL / SAS_VIYA_USER / SAS_VIYA_PASSWORD
(env or .env), exactly like the mdb commands.
"""
from __future__ import annotations

import json
import sys

try:
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True))
except ImportError:
    pass

from mdb.viya.registry import KIND_PROJECT
from mdb.viya.session import ViyaConfigError, create_session
from sasctl.services import model_repository as mr

# Any one of these present means the model carries cost information.
COST_KEYS = ("inputTokenCount", "outputTokenCount", "hostingCosts")


def _get(obj, key):
    """RestObj supports attribute and item access; try both."""
    value = getattr(obj, key, None)
    if value is None and hasattr(obj, "get"):
        try:
            return obj.get(key)
        except Exception:
            return None
    return value


def audit() -> list[dict]:
    rows: list[dict] = []
    with create_session() as session:
        for kind, project_name in KIND_PROJECT.items():
            project = mr.get_project(project_name)
            if project is None:
                continue
            project_id = _get(project, "id")
            response = session.get(
                f"/modelRepository/models?filter=eq(projectId,'{project_id}')&limit=1000"
            )
            if response.status_code >= 300:
                continue
            for item in response.json().get("items", []):
                model_ref, name = item.get("id"), item.get("name")
                body = dict(mr.get_model_details(model_ref).items())
                variables = session.get(
                    f"/modelRepository/models/{model_ref}/variables?start=0&limit=10000"
                )
                var_items = variables.json().get("items", []) if variables.status_code < 300 else []
                pairs = [(v.get("name"), v.get("role")) for v in var_items]
                duplicated = len(pairs) != len(set(pairs))

                missing: list[str] = []
                if not body.get("llmodelType"):
                    missing.append("family (llmodelType)")
                if not body.get("deploymentId"):
                    missing.append("version (deploymentId)")
                if not body.get("endPoint"):
                    missing.append("endPoint")
                if all(body.get(key) in (None, "") for key in COST_KEYS):
                    missing.append("cost")
                if not body.get("modelStatus") or not body.get("approvalState"):
                    missing.append("lifecycle")

                rows.append({
                    "model_id": name,
                    "kind": kind,
                    "llmodelType": body.get("llmodelType"),
                    "deploymentId": body.get("deploymentId"),
                    "modelStatus": body.get("modelStatus"),
                    "approvalState": body.get("approvalState"),
                    "variables": len(pairs),
                    "duplicated_variables": duplicated,
                    "missing": missing,
                    "needs_update": bool(missing) or duplicated,
                })
    return rows


def main() -> None:
    as_json = "--json" in sys.argv[1:]
    try:
        rows = audit()
    except ViyaConfigError as exc:
        print(f"SAS Viya is not configured: {exc}", file=sys.stderr)
        sys.exit(2)

    if as_json:
        print(json.dumps(rows, indent=2))
        return

    if not rows:
        print("No registered models found in the LLM / Embedding model projects.")
        return

    print(f"{'model_id':32} {'kind':10} {'family':10} {'status':22} {'vars':7} needs update")
    print("-" * 100)
    for row in rows:
        vtxt = f"{row['variables']}{' dup!' if row['duplicated_variables'] else ''}"
        print(
            f"{(row['model_id'] or '')[:32]:32} {row['kind']:10} "
            f"{(row['llmodelType'] or '-')[:10]:10} {(row['modelStatus'] or '-')[:22]:22} "
            f"{vtxt:7} {'YES' if row['needs_update'] else 'ok'}"
        )

    needing = [r for r in rows if r["needs_update"]]
    if not needing:
        print("\nAll registered models carry the full attribute set and have no duplicated variables.")
        return

    print(f"\n{len(needing)} model(s) need attention:")
    for row in needing:
        detail = ", ".join(row["missing"])
        if row["duplicated_variables"]:
            detail = (detail + "; " if detail else "") + "duplicated variables"
        print(f"  - {row['model_id']} ({row['kind']}): {detail}")
    print(
        "\nBackfill a model that still has its local definition folder:\n"
        "  mdb register <model_id> --update        (or: mdb register --all --update)\n"
        "Recover a model registered by the legacy scripts (no local definition.yaml) first:\n"
        "  mdb pull <model_id> --import   then   mdb register <model_id> --update"
    )


if __name__ == "__main__":
    main()
