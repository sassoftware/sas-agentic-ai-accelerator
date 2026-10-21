# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Custom node types of SAS Intelligent Decisioning: the Call LLM node under
one id on every site, and the move of a site's decisions to it.

WHY ONE ID. The content folder ships a decision that holds only the Call LLM
node (`Decisions/Call LLM Node`), so the transfer package carries the node
type as a dependency of that decision - with its id. An import creates the
node type under that id where the site has none, and overwrites the one of
that id in place. A site that created its Call LLM node with the SAS program
of the repository has it under an id of its own, and there the import fails
for the node type (a name is unique: 409) and skips the decision of the
package without a word, while the job ends `completed`.

WHAT WAS MEASURED on viya4-s2 on 2026-10-20 (ZZ_ objects only), and what
the move below is built on:

* a node type cannot be given another id: PUT ignores a changed `id`. POST
  /decisions/decisionNodeTypes with `"id"` in the body creates one under that
  id. A POST with an id that EXISTS answered 400 and had renamed that node
  type all the same - so the id is asked for (404) right before the POST;
* names are unique (409, on POST and on an import);
* a step of a decision names its node type only by its address
  (`customObject.uri`); the code is read from there at every test, code
  generation and publish;
* deleting a node type that decisions use is not refused: it clears the
  address in every version of every decision that uses it, and creating the
  id again does not bring it back;
* PUT changes the current version of a decision only (a version: 405). A
  transfer RE-IMPORT of the decision over itself, with a `dntStatic`
  connector mapping from the old address to the new one, rewrites EVERY
  version - locked ones included - and keeps the version ids. Published
  modules (MAS, SCR) hold compiled code and are not touched;
* an import of a package whose node type the site has under that id
  overwrites it in place even where the site made its content (another
  content id than the package's - a node created by the Create program or
  by the move): the content keeps the site's content id and gets the code
  and the terms of the package, and the decision of the package runs with
  it (measured on 2026-10-21).

THE MOVE (`mdb decision-node-migrate`): find the site's node type by its
name; stop where it has the id already; refuse where the id is taken; list
every decision with a version that uses the old node type; then - only with
--apply - rename the old node type to "<name> (before <first 8 of the id>)",
create the node type under the id with the name and the content of the old
one, move every decision by a re-import over itself, and read every version
back. The old node type is kept unless --delete-old is given AND no version
of any decision uses it any more. A run that stopped is run again: what is
done is found done (the renamed node type says where the first run got to).

EVERY RUN reads every decision and lists every version that calls a node
type LIKE the target (`similar_node_types`): a COPY - its code is the
target's, or the code the target gets from the old node type - or a node
type whose name ALONE starts with the node's name (any case) while its code
is another. Decisions left on a copy make a run with --apply end with exit
code 1; a name alone is a warning only, since another node type may be named
like it ("Call LLM Streaming"). Where the target is there already - the
package or the Create program made it after the old node type was renamed
by hand to free its name - `--from-id <its id>` moves the decisions of one
of them to the target and creates nothing; it warns where that node type is
no copy. A version whose node lost its address (a node type deleted
earlier) is a warning: no mapping moves it.

Every request goes through a session with `get` / `post` / `put` /
`delete` that take a path, and every call of the transfer service through
the module `transfer`, so the tests run this against fakes.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..core.packages import NODE_TYPE_URI, _reduce, custom_objects, locate, node_code, node_type_uris
from . import transfer
from .answer import answer, one_line

#: The Call LLM node of the accelerator: its name and the one id it has on
#: every site (the id of the node type on viya4-s2, so that system needs no move).
CALL_LLM_NODE_NAME = "Call LLM"
CALL_LLM_NODE_ID = "f9efcbb7-f57b-4a62-b56d-1a89595e2039"

NODE_TYPES = "/decisions/decisionNodeTypes"
NODE_MEDIA = "application/vnd.sas.decision.node.type+json"
CONTENT_MEDIA = "application/vnd.sas.decision.node.type.content+json"

#: What a new node type takes over from the old one, beside its name.
NODE_FIELDS = ("description", "hasProperties", "hasInputs", "hasOutputs", "inputDatagridMappable",
               "inputDecisionTermMappable", "outputDatagridMappable", "outputDecisionTermMappable",
               "independentMappings", "themeId", "type")
#: What a signature term of the content takes over.
TERM_FIELDS = ("name", "dataType", "length", "defaultValue", "direction", "generateDataGridColumns",
               "dataGridExtension")

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class NodeError(RuntimeError):
    """A request about node types or decisions was refused or got no answer."""


def node_uri(node_id: str) -> str:
    return f"{NODE_TYPE_URI}{node_id}"


def renamed_name(name: str, target_id: str) -> str:
    """The name the old node type is given: the name it had, and which id
    took its place."""
    return f"{name} (before {target_id[:8]})"


def _call(what: str, call):
    try:
        return call()
    except NodeError:
        raise
    except Exception as exc:                                   # a timeout, a dropped connection
        raise NodeError(f"{what}: no answer ({type(exc).__name__}: {one_line(str(exc))})") from exc


def _ok(what: str, response, allowed=()):
    if response.status_code in allowed:
        return response
    if not response.ok:
        raise NodeError(f"{what}: {answer(response)}")
    return response


def _items(session, path: str, what: str, params: Optional[dict] = None, missing_ok: bool = False):
    """Every item of a collection, page by page; None where the collection
    answers 404 and `missing_ok` says that means 'not on this site'."""
    found, start = [], 0
    while True:
        response = _call(what, lambda: session.get(path, params={**(params or {}), "start": start, "limit": 100}))
        if response.status_code == 404 and missing_ok and start == 0:
            return None
        page = _ok(what, response).json()
        items = page.get("items") or []
        found.extend(items)
        start += len(items)
        if not items or start >= int(page.get("count") or 0):
            return found


# ---------------------------------------------------------------------------
# node types
# ---------------------------------------------------------------------------
def list_node_types(session, missing_ok: bool = False):
    """Every custom node type of the site; None where SAS Intelligent
    Decisioning does not answer (404) and `missing_ok` allows it."""
    return _items(session, NODE_TYPES, "Listing the custom node types failed", missing_ok=missing_ok)


def get_node_type(session, node_id: str):
    """(node type, ETag), or None where there is none of that id."""
    response = _call(f"Reading the node type {node_id} failed", lambda: session.get(f"{NODE_TYPES}/{node_id}"))
    if response.status_code == 404:
        return None
    _ok(f"Reading the node type {node_id} failed", response)
    return response.json(), (getattr(response, "headers", None) or {}).get("ETag") or ""


def get_node_content(session, node_id: str):
    """The content of a node type (its code and signature terms), or None."""
    response = _call(f"Reading the content of the node type {node_id} failed",
                     lambda: session.get(f"{NODE_TYPES}/{node_id}/content"))
    if response.status_code == 404:
        return None
    return _ok(f"Reading the content of the node type {node_id} failed", response).json()


def rename_node_type(session, node_id: str, new_name: str) -> None:
    """Give a node type another name - under the ETag of the read (If-Match)."""
    found = get_node_type(session, node_id)
    if found is None:
        raise NodeError(f"Renaming the node type {node_id} failed: it is not there")
    node, etag = found
    body = {key: value for key, value in node.items() if key != "links"}
    body["name"] = new_name
    _ok(f"Renaming the node type {node_id} to '{new_name}' failed", _call(
        f"Renaming the node type {node_id} failed", lambda: session.put(
            f"{NODE_TYPES}/{node_id}", data=json.dumps(body),
            headers={"Content-Type": NODE_MEDIA, "Accept": NODE_MEDIA, "If-Match": etag or "*"})))


def node_body(node: dict, name: str, node_id: str) -> dict:
    """The body that creates a node type under `node_id` with the settings of `node`."""
    body = {key: node[key] for key in NODE_FIELDS if key in node}
    body.update({"id": node_id, "name": name})
    return body


def content_body(content: dict) -> dict:
    """The body that gives a node type the code and the terms of `content`."""
    return {"contentType": content.get("contentType") or "DS2", "staticContent": content.get("staticContent") or "",
            "nodeTypeSignatureTerms": [{key: term[key] for key in TERM_FIELDS if key in term}
                                       for term in content.get("nodeTypeSignatureTerms") or []
                                       if isinstance(term, dict)]}


def create_node_type(session, node: dict, name: str, node_id: str) -> None:
    """POST a node type under a chosen id. The caller has asked for the id
    and got 404: a POST with an id that exists renames that node type."""
    _ok(f"Creating the node type '{name}' under {node_id} failed", _call(
        f"Creating the node type '{name}' failed", lambda: session.post(
            NODE_TYPES, data=json.dumps(node_body(node, name, node_id)),
            headers={"Content-Type": NODE_MEDIA, "Accept": NODE_MEDIA})))


def set_node_content(session, node_id: str, content: dict) -> None:
    _ok(f"Setting the content of the node type {node_id} failed", _call(
        f"Setting the content of the node type {node_id} failed", lambda: session.post(
            f"{NODE_TYPES}/{node_id}/content", data=json.dumps(content_body(content)),
            headers={"Content-Type": CONTENT_MEDIA, "Accept": CONTENT_MEDIA})))


def delete_node_type(session, node_id: str) -> None:
    _ok(f"Deleting the node type {node_id} failed",
        _call(f"Deleting the node type {node_id} failed", lambda: session.delete(f"{NODE_TYPES}/{node_id}")))


# ---------------------------------------------------------------------------
# decisions and their versions
# ---------------------------------------------------------------------------
@dataclass
class Version:
    id: str
    label: str
    current: bool
    #: The node types the steps of this version call, by address.
    uris: list = field(default_factory=list)
    #: The names of the custom nodes a step calls without an address (a deleted node type).
    unnamed: list = field(default_factory=list)

    @property
    def state(self) -> str:
        return "current" if self.current else "locked"

    @property
    def shown(self) -> str:
        return f"{self.label} ({self.state})"


@dataclass
class DecisionUse:
    id: str
    name: str
    versions: list = field(default_factory=list)

    @property
    def uri(self) -> str:
        return f"/decisions/flows/{self.id}"

    def using(self, uri: str) -> list:
        return [version for version in self.versions if uri in version.uris]


def is_node(custom: dict) -> bool:
    """Whether a step's `customObject` is a custom node (`dntStatic`, or a
    REST node) - a code file step has one too (`decisionPythonFile`), and its
    address is that of the code file."""
    return str(custom.get("type") or "").startswith("dnt") or bool(custom.get("isRestDNT"))


def versions_of(session, decision_id: str) -> list:
    """Every version of a decision with the node types its steps call. The
    current version is the newest: a version is locked by making a new one."""
    listed = _items(session, f"/decisions/flows/{decision_id}/revisions",
                    f"Listing the versions of the decision {decision_id} failed")
    listed = sorted(listed, key=lambda r: (int(r.get("majorRevision") or 0), int(r.get("minorRevision") or 0)))
    versions = []
    for index, summary in enumerate(listed):
        path = f"/decisions/flows/{decision_id}/revisions/{summary['id']}"
        body = _ok(f"Reading the version {path} failed",
                   _call(f"Reading the version {path} failed", lambda: session.get(path))).json()
        versions.append(Version(
            id=summary["id"], label=f"{summary.get('majorRevision', '?')}.{summary.get('minorRevision', '?')}",
            current=index == len(listed) - 1, uris=node_type_uris(body),
            unnamed=[str(c.get("name")) for c in custom_objects(body) if is_node(c) and not c.get("uri")]))
    return versions


def scan_decisions(session, progress: Optional[Callable[[int], None]] = None) -> list:
    """Every decision of the site with every version of it: [DecisionUse].
    Each decision and each of its versions is read - a locked version keeps
    the node type it was locked with."""
    flows = _items(session, "/decisions/flows", "Listing the decisions failed")
    if progress:
        progress(len(flows))
    return [DecisionUse(id=flow["id"], name=str(flow.get("name") or ""), versions=versions_of(session, flow["id"]))
            for flow in flows]


def decisions_using(session, uri: str, progress: Optional[Callable[[int], None]] = None) -> list:
    """Every decision of the site with a version whose steps call the node
    type at `uri`: [DecisionUse] with every version of each."""
    return [use for use in scan_decisions(session, progress) if use.using(uri)]


# ---------------------------------------------------------------------------
# a decision, moved by a re-import over itself
# ---------------------------------------------------------------------------
def _kind(task: dict) -> str:
    return str(task.get("resourceType") or "").rsplit(".", 1)[-1].replace("+json", "")


def missing_tasks(tasks: list, expected) -> list:
    """The (kind, name) of `expected` that no task of an import job ended
    `completed` for: an object the job left out without a failed task. The
    transfer service was measured to skip a decision that way, in a job that
    ended `completed`, when the node type of its package failed."""
    done = {(_kind(task), str(task.get("name") or "")) for task in tasks
            if str(task.get("state") or "").lower() == "completed"}
    return [(kind, name) for kind, name in expected if (kind, name) not in done]


def retarget(session, decision: DecisionUse, old_uri: str, new_uri: str, timeout: int = transfer.DEFAULT_TIMEOUT) -> tuple:
    """Move every version of a decision from one node type to another: export
    the decision, keep its own object, upload that and import it over the
    decision with a `dntStatic` mapping old -> new. Returns (problems,
    leftovers): sentences of what went wrong, and of what the transfer service
    still holds that could not be deleted. The export package, the record of
    the export job and the uploaded package are deleted; the record of the
    import job is kept - it is the record of the change."""
    problems: list = []
    leftovers: list = []
    name = f"mdb decision-node-migrate {decision.id}"
    export_job = package_uri = uploaded_uri = None
    try:
        job = transfer.start_export(session, name, [decision.uri],
                                    description=f"move from {old_uri} to {new_uri}")
        export_job = transfer.job_uri(job).replace("/importJobs/", "/exportJobs/")
        job = transfer.wait_for_job(session, export_job, timeout=timeout)
        package_uri = job.get("packageUri")
        if str(job.get("state", "")).lower() != "completed" or not package_uri:
            return [f"the export ended {job.get('state')}: "
                    + ("; ".join(transfer.job_messages(session, job)) or "no task says why")], leftovers
        package = json.loads(transfer.download_package(session, package_uri))
        _root, items = locate(package)
        mine = [item for item in items if item.kind == "decision" and item.uri == decision.uri]
        if len(mine) != 1:
            return [f"the export holds {len(mine)} objects for {decision.uri}"], leftovers
        connector = next((c for c in mine[0].obj.get("connectors") or []
                          if isinstance(c, dict) and c.get("type") == "dntStatic" and c.get("uri") == old_uri), None)
        if connector is None:
            return [f"the export of the decision names no dntStatic connector to {old_uri}, so no mapping can move "
                    "it"], leftovers
        _reduce(package, items, mine)
        uploaded = transfer.upload_package_bytes(session, f"{decision.id}.json",
                                                 json.dumps(package, ensure_ascii=False).encode("utf-8"))
        uploaded_uri = f"/transfer/packages/{uploaded['id']}"
        mapping = transfer.get_mapping(session, uploaded["id"]) or {}
        mapping.setdefault("version", 1)
        connectors = mapping.get("connectors") if isinstance(mapping.get("connectors"), dict) else {}
        mapping["connectors"] = connectors
        offered = [c for c in connectors.get("dntStatic") or [] if isinstance(c, dict) and c.get("source") != old_uri]
        connectors["dntStatic"] = offered + [{"connectorId": connector.get("id"),
                                              "resourceName": connector.get("name") or "",
                                              "source": old_uri, "target": new_uri}]
        job = transfer.start_import(session, name, uploaded_uri, mapping)
        job = transfer.wait_for_job(session, transfer.job_uri(job), timeout=timeout)
        if str(job.get("state", "")).lower() != "completed" or job.get("errorCount"):
            problems.append(f"the import ended {job.get('state')} with {job.get('errorCount', 0)} error(s): "
                            + ("; ".join(transfer.job_messages(session, job)) or "no task says why"))
        else:
            tasks = transfer.job_tasks(session, job)
            problems += [f"import task {line}" for line in transfer.task_problems(tasks)]
            problems += [f"the import job ended completed and has no completed task for the {kind} '{what}' - "
                         "it was not imported" for kind, what in missing_tasks(tasks, [("decision", decision.name)])]
    except (RuntimeError, ValueError, KeyError) as exc:
        problems.append(one_line(str(exc)))
    finally:
        for uri, what, delete in ((uploaded_uri, "package", transfer.delete_package),
                                  (package_uri, "package", transfer.delete_package),
                                  (export_job, "export job record", transfer.delete_job)):
            if not uri:
                continue
            try:
                delete(session, uri)
            except RuntimeError as exc:
                leftovers.append(f"{transfer.leftover_line(uri, what)} ({one_line(str(exc))})")
    return problems, leftovers


# ---------------------------------------------------------------------------
# the move
# ---------------------------------------------------------------------------
#: What a line of the command is: (text, style) - style None, "red", "yellow" or "green".
Say = Callable[[str, Optional[str]], None]


def _versions_text(versions: list) -> str:
    return ", ".join(version.shown for version in versions) or "none"


def _code(content: Optional[dict]) -> str:
    """The code of a node type's content - line ends, and blanks at the end
    of a line, aside."""
    return node_code((content or {}).get("staticContent"))


def _content_or_none(session, node_id: str) -> Optional[dict]:
    """The content of a node type, or None where it is not there or cannot be read."""
    try:
        return get_node_content(session, node_id)
    except NodeError:
        return None


def similar_node_types(session, nodes: list, name: str, target_id: str, target_content: Optional[dict],
                       skip=(), old_content: Optional[dict] = None) -> list:
    """[(node type, why, copy)] of the node types other than the target (and
    `skip`) that are like it.

    A COPY (`copy` True): its code is the code of the target (line ends and
    blanks at the end of a line aside), or its name is the
    "<name> (before <id8>)" this command gives the old node type. A copy
    whose decisions are left makes a run with --apply end with exit code 1.

    A NAME ALONE (`copy` False): its name starts with `name`, in any case -
    a node renamed by hand to free the name, or another node type named like
    it ("Call LLM Streaming") - and its code is another, or cannot be
    compared. It is listed with its decisions as a warning, and never counts
    as left: a node type with other code is not moved on a name.

    `old_content` is the content of the old node type this run moves from
    (the renamed one, or --from-id's): a node type with that code is a copy
    as well, so a copy is still found after the target got other code (the
    release's, from builders-import or the Update program)."""
    code = _code(target_content)
    old_code = _code(old_content)
    found = []
    for node in nodes:
        node_id = str(node.get("id") or "")
        if node_id == target_id or node_id in skip:
            continue
        node_name = str(node.get("name") or "")
        by_name = node_name.lower().startswith(name.lower())
        if not by_name and not code and not old_code:
            continue
        try:
            content = get_node_content(session, node_id)
        except NodeError:
            # a node type whose content cannot be read (a REST node type, a refusal) is not taken for a copy
            content = None
            if not by_name:
                continue
        same = bool(code) and content is not None and _code(content) == code
        same_old = not same and bool(old_code) and content is not None and _code(content) == old_code
        if same or same_old:
            found.append((node, (f"its name starts with '{name}' and it" if by_name else "it")
                          + (f" has the code of {target_id}" if same else " has the code of the old node type"), True))
        elif by_name:
            why = (f"{target_id} has no code to compare" if not code else "its code cannot be read"
                   if content is None else f"its code is not the code of {target_id}")
            found.append((node, f"its name starts with '{name}', but {why}", node_name == renamed_name(name, target_id)))
    return found


def _named_alike(node: dict, name: str) -> bool:
    return str(node.get("name") or "").lower().startswith(name.lower())


def _broken_lines(broken: list, say: Say) -> None:
    say(f"Warning: {len(broken)} decision version(s) call a node whose node type is gone - it was deleted, which "
        "cleared the address of the node in every version that called it. They cannot run, and this command cannot "
        "move them (there is no address to map):", "yellow")
    for use, version in broken:
        say(f"  '{use.name}' ({use.id}) version {version.shown}: step(s) "
            + ", ".join(f"'{step}'" for step in version.unnamed), "yellow")


def migrate(session, name: str, target_id: str, apply: bool, delete_old: bool, say: Say,
            timeout: int = transfer.DEFAULT_TIMEOUT, from_id: Optional[str] = None,
            command: Optional[Callable[[str], str]] = None) -> int:
    """`mdb decision-node-migrate`: the exit code - 0 where the site is as it
    has to be (or a dry run found the way there), 1 where something was
    refused or failed, or where a run with --apply leaves decisions on a node
    type like the target that it did not move. Every line goes through `say`.

    `from_id` names a node type whose decisions are moved to the target,
    which has to be there: nothing is created or renamed. `command(id)` is
    the command line that moves the decisions of the node type `id` (for the
    line that names it)."""
    target_uri = node_uri(target_id)
    old_name = renamed_name(name, target_id)
    nodes = list_node_types(session, missing_ok=True)
    if nodes is None:
        say("This site has no SAS Intelligent Decisioning (GET /decisions/decisionNodeTypes answers 404): there is no "
            "node type and no decision to migrate.", "green")
        return 0
    by_id = {str(node.get("id")): node for node in nodes}
    named = [node for node in nodes if node.get("name") == name]
    renamed = [node for node in nodes if node.get("name") == old_name]
    target_found = get_node_type(session, target_id)
    target = target_found[0] if target_found else None
    need_rename = need_create = need_content = False
    old = None
    moves = command or (lambda node_id: f"mdb decision-node-migrate --from-id {node_id} --apply")

    if from_id is not None:
        if target is None:
            say(f"Nothing was changed: --from-id moves the decisions of a node type to the node type {target_id}, and "
                f"there is none of that id on this site. Run the command without --from-id - it creates {target_id} "
                f"from the node type named '{name}' -, or import the package of the accelerator (`mdb "
                "builders-import`), which brings it.", "red")
            return 1
        old = by_id.get(from_id)
        if old is None:
            found = get_node_type(session, from_id)
            old = found[0] if found else None
        if old is None:
            say(f"The node type {from_id} (--from-id) is not on this site. Nothing was changed.", "red")
            return 1

    target_content = None
    if target is not None and (from_id is not None or (named and named[0].get("id") == target_id)):
        say(f"Node type '{target.get('name')}': it has the id {target_id} already.", None)
        target_content = get_node_content(session, target_id)
        if old is not None:
            say(f"--from-id: the decisions that call the node type '{old.get('name')}' ({from_id}) are moved to "
                f"{target_id}; no node type is created or renamed.", None)
            if not any(copy for _node, _reason, copy in
                       similar_node_types(session, [old], name, target_id, target_content)):
                say(f"  '{old.get('name')}' does not have the code of {target_id}"
                    + (f", though its name starts with '{name}'" if _named_alike(old, name) else "")
                    + ": check that it is a copy of that node type - its decisions get the code and the terms of "
                    f"{target_id}" + (", and --delete-old then deletes it." if delete_old else "."), "yellow")
        elif renamed:
            old = renamed[0]
            say(f"The old node type '{old_name}' ({old.get('id')}) is still there - an earlier run renamed it.", None)
        need_content = old is not None and target_content is None
        if target_content is None and old is None:
            say(f"The node type {target_id} has no content (code and terms): a decision that calls it cannot run. "
                "Update-Custom-SAS-Intelligent-Decisioning-Node.sas gives it the content of the release.", "yellow")
    elif named:
        old = named[0]
        say(f"Node type '{name}': id {old.get('id')}, not {target_id}"
            + (" - the id the package of the accelerator gives it." if target_id == CALL_LLM_NODE_ID else "."), None)
        if target is not None:
            say(f"The id {target_id} is taken: the node type '{target.get('name')}' has it. Nothing was changed. A "
                "POST of an id that exists renames that node type, so this command never sends one - pick the id of "
                "a node type that is not there (--target-id), or ask why that node type has it.", "red")
            return 1
        if renamed:
            say(f"A node type named '{old_name}' is there already (id {renamed[0].get('id')}): the old node type "
                "cannot take that name. Nothing was changed. Rename or delete that node type first.", "red")
            return 1
        need_rename = need_create = need_content = True
    else:
        if target is not None:
            say(f"No node type is named '{name}', and the id {target_id} belongs to the node type "
                f"'{target.get('name')}'. Nothing was changed: this command moves the node type named '{name}' to "
                f"that id, and there is none - check --node-name and --target-id; --from-id <the id of a node type> "
                f"moves the decisions of that node type to {target_id}.", "red")
            return 1
        if not renamed:
            alike = [node for node in nodes if str(node.get("name") or "").lower().startswith(name.lower())]
            say(f"No node type named '{name}' is on this site, and none under the id {target_id}: there is no node "
                f"type to give that id. The package of the accelerator brings it (`mdb builders-import`).", "green")
            if alike:
                say(f"Node type(s) whose name starts with '{name}': "
                    + "; ".join(f"'{node.get('name')}' ({node.get('id')})" for node in alike)
                    + f". Decisions that call one of them keep calling it. Once {target_id} is there, a run of this "
                    "command says which of them has its code, and which decisions call them.", "yellow")
            return 0
        old = renamed[0]
        say(f"No node type is named '{name}', and the old node type '{old_name}' ({old.get('id')}) is there: an "
            f"earlier run renamed it and did not create the node type under {target_id}.", None)
        need_create = need_content = True

    old_id = str(old.get("id")) if old is not None else None
    old_uri = node_uri(old_id) if old_id else None
    old_label = old_name if need_rename else (str(old.get("name")) if old is not None else "")
    old_content = get_node_content(session, old_id) if need_content else None
    if need_content and old_content is None:
        say(f"The node type {old_id} has no content (code and terms) to copy. Nothing was changed.", "red")
        return 1
    # the node types like the target that this run does not move: compared with the code the target has, or - where
    # this run creates it or gives it its content - the code it gets
    # and with the code of the old node type this run moves from
    others = similar_node_types(session, nodes, name, target_id,
                                target_content if target_content is not None else old_content,
                                skip={old_id} if old_id else (),
                                old_content=(old_content if old_content is not None
                                             else _content_or_none(session, old_id) if old_id else None))

    say("Looking for every decision with a version that calls "
        + (f"{old_uri}" if old_uri else "a node type like it") + " - every version of every decision is read ...",
        None)
    every = scan_decisions(session, lambda n: say(f"  {n} decision(s) on this site.", None))
    uses = [use for use in every if old_uri and use.using(old_uri)]
    if old_uri and uses:
        say(f"{len(uses)} decision(s) call the old node type:", None)
        for use in uses:
            say(f"  '{use.name}' ({use.id}): version(s) {_versions_text(use.using(old_uri))}", None)
    elif old_uri:
        say("No decision calls the old node type.", None)
    #: the copies of the target that decisions still call - a run with --apply that leaves them ends with exit code 1
    left = []
    later = f"Once {target_id} is there (this command with --apply), " if target is None else ""
    for node, reason, copy in others:
        uri = node_uri(str(node.get("id")))
        calling = [use for use in every if use.using(uri)]
        if calling:
            if copy:
                left.append(node)
            say(f"The node type '{node.get('name')}' ({node.get('id')}) - {reason} - is called by {len(calling)} "
                + ("decision(s), which this run does not move:" if copy else
                   f"decision(s). It is not taken for a copy of {target_id}, and this run does not move them:"),
                "yellow")
            for use in calling:
                say(f"  '{use.name}' ({use.id}): version(s) {_versions_text(use.using(uri))}", "yellow")
            if copy:
                say(f"  {later}`{moves(str(node.get('id')))}` moves them to {target_id}. Do not delete that node type "
                    "while a version calls it.", "yellow")
            else:
                say(f"  {later}{'if' if later else 'If'} it is an older version of that node type all the same, "
                    f"`{moves(str(node.get('id')))}` moves them to {target_id} - they then run its code and its "
                    "terms. Do not delete that node type while a version calls it.", "yellow")
        else:
            say(f"The node type '{node.get('name')}' ({node.get('id')}) - {reason} - is called by no version of any "
                "decision.", None)
    broken = [(use, version) for use in every for version in use.versions if version.unnamed]
    if broken:
        _broken_lines(broken, say)

    if old is None:
        # the target is there, and this run names no node type to move from
        if delete_old:
            say("--delete-old: nothing is deleted - it deletes only a node type this run moves the decisions of "
                "(--from-id, or the one an earlier run renamed)." if others else
                "--delete-old: there is no old node type to delete.", None)
        if left:
            if not apply:
                say("Dry run - nothing was changed.", "green")
                return 0
            say(f"Not everything is done: {len(left)} node type(s) like {target_id} are still called by decisions "
                "(above). Move them with --from-id.", "red")
            return 1
        say("Nothing to do.", "green")
        return 0

    steps = []
    if need_rename:
        steps.append(f"rename the node type {old_id} from '{name}' to '{old_name}'")
    if need_create:
        steps.append(f"create the node type '{name}' under the id {target_id} with the settings of {old_id}")
    if need_content:
        steps.append(f"give {target_id} the code and the terms of {old_id}")
    if uses:
        steps.append(f"move {len(uses)} decision(s) to {target_uri}: each is exported and imported over itself with "
                     f"the mapping {old_uri} -> {target_uri}, which rewrites every version - locked ones included - "
                     "and keeps the version ids; published modules are not touched")
    steps.append(f"delete the old node type {old_id} once no version of any decision calls it" if delete_old else
                 f"keep the old node type {old_id} ('{old_label}') - --delete-old deletes it once no version calls it")
    if not apply:
        say("A run with --apply would:", None)
        for number, step in enumerate(steps, start=1):
            say(f"  {number}. {step}", None)
        say("Dry run - nothing was changed. Run the command again with --apply.", "green")
        return 0

    # ---- apply ----
    try:
        if need_rename:
            rename_node_type(session, old_id, old_name)
            say(f"renamed     {old_id}: '{name}' -> '{old_name}'", None)
        if need_create:
            # asked again right before the POST: a POST of an id that exists renames that node type
            if get_node_type(session, target_id) is not None:
                say(f"The id {target_id} was taken in the meantime. Stopped: no node type was created.", "red")
                return 1
            # the settings are read from the node type itself: a listing gives its id and name only
            found = get_node_type(session, old_id)
            if found is None:
                raise NodeError(f"Reading the node type {old_id} failed: it is not there")
            create_node_type(session, found[0], name, target_id)
            say(f"created     {target_id}: '{name}', with the settings of {old_id}", None)
        if need_content:
            set_node_content(session, target_id, old_content)
            say(f"content     {target_id}: the code and the terms of {old_id}", None)
    except NodeError as exc:
        say(f"FAILED      {one_line(str(exc))}", "red")
        say("Stopped. Fix what is reported and run the command again: what was done is found done.", "red")
        return 1

    failed = 0
    for use in uses:
        problems, leftovers = retarget(session, use, old_uri, target_uri, timeout)
        for line in leftovers:
            say(f"            {line}", "yellow")
        if not problems:
            # read back: every version that called the old node type calls the new one, and none the old
            after = {version.id: version for version in versions_of(session, use.id)}
            for version in use.using(old_uri):
                now = after.get(version.id)
                if now is None:
                    problems.append(f"version {version.shown} ({version.id}) is gone after the import")
                elif old_uri in now.uris or target_uri not in now.uris:
                    problems.append(f"version {version.shown} calls {', '.join(now.uris) or 'no node type'} after "
                                    "the import")
        if problems:
            failed += 1
            say(f"FAILED      decision '{use.name}' ({use.id}):", "red")
            for problem in problems:
                say(f"              {problem}", "red")
        else:
            say(f"moved       decision '{use.name}' ({use.id}): version(s) {_versions_text(use.using(old_uri))} "
                f"call {target_uri} - read back", None)

    if delete_old:
        if failed:
            say(f"not deleted {old_id}: {failed} decision(s) were not moved and still call it.", "yellow")
        else:
            # read again where decisions were moved: one made meanwhile with the old node type counts too
            still = decisions_using(session, old_uri) if uses else []
            if still:
                say(f"not deleted {old_id}: {len(still)} decision(s) still call it - "
                    + "; ".join(f"'{use.name}' {_versions_text(use.using(old_uri))}" for use in still)
                    + ". Deleting it would empty the node of those versions for good.", "yellow")
                failed += 1
            else:
                try:
                    delete_node_type(session, old_id)
                    say(f"deleted     {old_id} ('{old_label}'): no version of any decision calls it", None)
                except NodeError as exc:
                    say(f"FAILED      {one_line(str(exc))}", "red")
                    failed += 1
    if broken:
        say(f"Warning: the {len(broken)} decision version(s) listed above whose node type is gone still cannot run.",
            "yellow")
    if failed:
        say("Not everything was done. Fix what is reported above and run the command again with --apply: what is "
            "done is found done.", "red")
        return 1
    say(f"Done. Every decision that called the old node type calls '{target.get('name') if target else name}' "
        f"under {target_id}.", "green")
    if not delete_old:
        say(f"The old node type '{old_label}' ({old_id}) is kept; published modules do not need it. Delete it with "
            "--delete-old once you are sure: a node type that is deleted cannot be brought back for the versions "
            "that call it.", None)
    if left:
        say(f"Not everything is done: {len(left)} other node type(s) like {target_id} are still called by decisions "
            "(above). Move them with --from-id.", "red")
        return 1
    return 0


# ---------------------------------------------------------------------------
# before an import: does the site take the node types of a package?
# ---------------------------------------------------------------------------
CREATE, OVERWRITE, CLASH, NO_DECISIONING = "create", "overwrite", "clash", "no decisioning"


def site_check(session, nodes) -> list:
    """For each (name, id) of a package: (name, id, verdict, what the site
    has). CREATE - neither the id nor the name is there; OVERWRITE - the id
    is there (the import writes the package's node type over it, its code
    included); CLASH - the name is there under another id (the import would
    fail for the node type and skip the decisions of the package);
    NO_DECISIONING - SAS Intelligent Decisioning does not answer on the site."""
    listed = list_node_types(session, missing_ok=True)
    if listed is None:
        return [(name, node_id, NO_DECISIONING, None) for name, node_id in nodes]
    verdicts = []
    for name, node_id in nodes:
        same_name = [node for node in listed if node.get("name") == name and node.get("id") != node_id]
        same_id = next((node for node in listed if node.get("id") == node_id), None)
        if same_id is None:
            found = get_node_type(session, node_id)
            same_id = found[0] if found else None
        if same_name:
            verdicts.append((name, node_id, CLASH, same_name[0]))
        elif same_id is not None:
            verdicts.append((name, node_id, OVERWRITE, same_id))
        else:
            verdicts.append((name, node_id, CREATE, None))
    return verdicts
