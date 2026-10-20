"""SAS-Intelligent-Decisioning-Integration: the two programs of the Call LLM node and the id they keep it under.

A decision references a custom node by its id (customObject.uri of its step), never by its name, and a transfer
package of a folder with a decision carries the node with its id. The accelerator therefore ships the node under one
id, f9efcbb7-f57b-4a62-b56d-1a89595e2039 - the id the node has on the test system. What the programs do was measured
on that system on 2026-10-20 with nodes of their own (ZZ_NODE_R31), not with these programs:

- POST /decisions/decisionNodeTypes with "id" in its body creates the node under that id (201).
- Node names are unique: a POST of a name that is there answers 409.
- A POST with an id that is there answered 400 - and had renamed the node of that id to the name of the request
  before it did. So the create program asks for the id first and sends the POST only where that request answered 404.

Create-Custom-SAS-Intelligent-Decisioning-Node.sas creates the node under that id. It creates nothing where the
node is under it already (and names the update program), and nothing where a node named Call LLM is there under
another id: it then names the migration `mdb decision-node-migrate`. Update-Custom-SAS-Intelligent-Decisioning-Node.sas
looks for the node under that id first and falls back to the name, with a NOTE that the node of the site is not under
the shipped id yet.

SAS can not run here. The macro of each program is followed with macro_harness against a site that is made up: a
PROC HTTP step is answered by the site the way the test system answered, and a DATA step that leaves a macro
variable is stood in for by the value it leaves.
"""
import re
import sys
from pathlib import Path
from urllib.parse import quote, unquote

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Logging-Monitoring" / "tests"))

import macro_harness  # noqa: E402

REPOSITORY = Path(__file__).resolve().parents[3]
FOLDER = REPOSITORY / "SAS-Viya-Integrations" / "SAS-Intelligent-Decisioning-Integration"
CREATE = FOLDER / "Create-Custom-SAS-Intelligent-Decisioning-Node.sas"
UPDATE = FOLDER / "Update-Custom-SAS-Intelligent-Decisioning-Node.sas"
DOCS = REPOSITORY / "website" / "docs" / "Administration-Guide" / "Deployment-of-Decisions.md"
NODE_ID = "f9efcbb7-f57b-4a62-b56d-1a89595e2039"
OTHER_ID = "a7a55283-4944-4085-a4a2-e712953824a7"
HOST = "https://viya.example.com"
NODES = HOST + "/decisions/decisionNodeTypes"
GUIDE = "https://sassoftware.github.io/sas-agentic-ai-accelerator/Administration-Guide/Deployment-of-Decisions#call-llm-node-uuid"


def read(path):
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


class Site:
    """The node types of a SAS Viya made up for a test: {id: name}, and the ids that have content. It answers as the
    test system answered on 2026-10-20, the POST of an id that is there included."""

    def __init__(self, nodes=None, by_id_status=None):
        self.nodes = dict(nodes or {})
        self.content = set(self.nodes)
        self.by_id_status = by_id_status      # a status the request for one node answers with, whatever the site holds
        self.requests = []

    def answer(self, method, url, body_id=None, body_name=None):
        self.requests.append((method, url))
        path, _, query = url.partition("?")
        assert path.startswith(NODES), url
        rest = path[len(NODES):].strip("/").split("/") if path != NODES else []
        if method == "GET" and not rest:
            name = re.fullmatch(r"filter=eq\(name,'(.*)'\)", unquote(query)).group(1)
            self.found = [node for node, node_name in self.nodes.items() if node_name == name]
            return "200"
        if method == "GET" and len(rest) == 1:
            if self.by_id_status:
                return self.by_id_status
            self.got = rest[0]
            return "200" if rest[0] in self.nodes else "404"
        if method == "GET" and rest[1:] == ["content"]:
            return "200" if rest[0] in self.content else "404"
        if method == "POST" and not rest:
            if body_id in self.nodes:
                # measured: 400, and the node of that id had taken the name of the request
                self.nodes[body_id] = body_name
                return "400"
            if body_name in self.nodes.values():
                return "409"
            self.nodes[body_id] = body_name
            return "201"
        if method == "POST" and rest[1:] == ["content"]:
            self.content.add(rest[0])
            return "201"
        if method == "PUT" and rest[1:] == ["content"]:
            return "200" if rest[0] in self.content else "404"
        raise AssertionError(f"a request the site does not know: {method} {url}")


def settings(text):
    """The macro variables the open code of a program sets in front of its macro; the host is handed in."""
    head = text[:text.index("%macro ")]
    symbols = {name: value for name, value in re.findall(r"^%let (\w+) ?= ?(.*?);$", head, flags=re.MULTILINE)}
    symbols["_dntviyaHost"] = HOST
    return symbols


def follow(path, site):
    """Follows the macro of a program against `site`: (the lines it wrote with %put, the macros at its end)."""
    text = read(path)
    name = re.search(r"^%macro (\w+);$", text, flags=re.MULTILINE).group(1)
    step = {"http": None, "body_id": None, "body_name": None}

    def on_text(line, macros):
        if line.startswith("proc http"):
            step["http"] = [line]
        elif step["http"] is not None:
            step["http"].append(line)
            if line in ("run;", "quit;", "run; quit;"):
                block = " ".join(step["http"])
                url = re.search(r'url = "([^"]*)"', block).group(1)
                method = re.search(r"method = '(\w+)'", block).group(1).upper()
                status = site.answer(method, url, step["body_id"], step["body_name"])
                macros.symbols["sys_prochttp_status_code"] = status
                macros.symbols["sys_prochttp_status_phrase"] = {"200": "OK", "404": "Not Found"}.get(status, "Error")
                step["http"] = None
        elif line.startswith("""id = '"id": "' || \""""):
            step["body_id"] = re.search(r'\|\| "([^"]*)" \|\|', line).group(1)
        elif line.startswith("""name = '"name": "' || \""""):
            step["body_name"] = re.search(r'\|\| "([^"]*)" \|\|', line).group(1)
        elif line.startswith("call symputx('_dntNameFilter', urlencode("):
            # URLENCODE leaves letters, digits and the characters of the filter but the blank
            argument = re.search(r'urlencode\("(.*)"\)\);$', line).group(1)
            macros.symbols["_dntnamefilter"] = quote(argument, safe="(),'")
        elif line == "call symputx('_dntNameIDs', ids);":
            macros.symbols["_dntnameids"] = " ".join(site.found)
        elif line == "call symputx('_dntIdName', Value);":
            macros.symbols["_dntidname"] = site.nodes[site.got]
        elif line == "call symputx('_resource_etag', compress(eTag2));":
            macros.symbols["_resource_etag"] = "'\"etag-1\"'"

    macros = macro_harness.Macros(settings(text))
    _, log = macros.run(text, name, on_text)
    return log, macros


def posts(site):
    return [url for method, url in site.requests if method == "POST"]


# ---------------------------------------------------------------------------
# The id
# ---------------------------------------------------------------------------

def test_both_programs_keep_the_node_under_the_one_id():
    for path in (CREATE, UPDATE):
        text = read(path)
        assert re.findall(r"^%let _dntNodeID = (.*);$", text, flags=re.MULTILINE) == [NODE_ID], path.name
        assert re.findall(r"^%let _dntNodeName = (.*);$", text, flags=re.MULTILINE) == ["Call LLM"], path.name
        # the header names the id and the section of the guide
        header = " ".join(text[:text.index("*/")].split())
        assert NODE_ID in header and GUIDE in header and "mdb decision-node-migrate" in header, path.name
        # every macro variable the program sets is deleted at its end, the macro too
        last = text.rstrip("\n").split("\n")
        assert last[-1].startswith("%symdel ") and last[-1].endswith(" / nowarn;")
        assert last[-2].startswith("%sysmacdelete _dnt")
        deleted = set(last[-1][len("%symdel "):-len(" / nowarn;")].split())
        assert set(re.findall(r"^%let (\w+) ?=", text, flags=re.MULTILINE)) <= deleted, path.name


def test_the_create_request_carries_the_id_and_its_content_goes_to_that_id():
    text = read(CREATE)
    body = text[text.index("* Define the new node type, under its id;"):text.index("* Create a new node type.")]

    assert body.index("""id = '"id": "' || "&_dntNodeID." || '",';""") < body.index("""name = '"name": "' ||""")
    assert '"&_dntviyaHost./decisions/decisionNodeTypes/&_dntNodeID./content"' in text
    # the id is no longer read from the answer of the POST
    assert "call symputx('_dntID'" not in text and "&_dntID." not in text


def test_the_two_programs_write_the_same_content():
    def content(text):
        start = text.index("    * Create the content for the node type;")
        return text[start:text.index("    run;", start)]

    assert content(read(CREATE)) == content(read(UPDATE))
    assert '"nodeTypeSignatureTerms": [' in content(read(CREATE))


# ---------------------------------------------------------------------------
# Create: what it does on four sites
# ---------------------------------------------------------------------------

def test_create_on_a_site_without_the_node_creates_it_under_the_id():
    site = Site()
    log, _ = follow(CREATE, site)

    assert site.requests == [("GET", f"{NODES}/{NODE_ID}"), ("GET", f"{NODES}?filter=eq(name,'Call%20LLM')"),
                             ("POST", NODES), ("POST", f"{NODES}/{NODE_ID}/content")]
    assert site.nodes == {NODE_ID: "Call LLM"} and site.content == {NODE_ID}
    assert log == [f"NOTE: Create-Custom-SAS-Intelligent-Decisioning-Node.sas: the node Call LLM was created under the id {NODE_ID}, "
                   "with its content."]


@pytest.mark.parametrize("name", ["Call LLM", "Call LLM (renamed)"])
def test_create_where_the_id_is_there_sends_no_post_and_names_the_update_program(name):
    """The test system: its node is under the id. A node under the id with another name keeps it - the POST the program
    no longer sends would have given it the name of the request."""
    site = Site({NODE_ID: name})
    log, _ = follow(CREATE, site)

    assert site.requests == [("GET", f"{NODES}/{NODE_ID}")] and posts(site) == []
    assert site.nodes == {NODE_ID: name}
    assert log == [f"NOTE: Create-Custom-SAS-Intelligent-Decisioning-Node.sas: the node {name} is already there under the id {NODE_ID}. "
                   "Nothing was created. To bring its content to the one of this release, run Update-Custom-SAS-Intelligent-Decisioning-Node.sas."]


def test_create_where_call_llm_is_under_another_id_creates_nothing_and_names_the_migration():
    site = Site({OTHER_ID: "Call LLM"})
    log, _ = follow(CREATE, site)

    assert posts(site) == [] and site.nodes == {OTHER_ID: "Call LLM"}
    assert [method for method, _ in site.requests] == ["GET", "GET"]
    assert len(log) == 3 and all(line.startswith("ERROR: ") for line in log)
    assert log[0] == (f"ERROR: Create-Custom-SAS-Intelligent-Decisioning-Node.sas: nothing was created, because a node named Call LLM is "
                      f"already there under the id {OTHER_ID} and not under the id {NODE_ID} that the accelerator ships. Node names are unique.")
    assert "mdb decision-node-migrate, first as a dry run" in log[1] and "locked versions included" in log[1]
    assert "Do not delete that node" in log[1]
    assert log[2].endswith(GUIDE)


@pytest.mark.parametrize("status", ["401", "403", "500"])
def test_create_where_the_request_for_the_id_fails_asks_for_nothing_more(status):
    site = Site(by_id_status=status)
    log, _ = follow(CREATE, site)

    assert site.requests == [("GET", f"{NODES}/{NODE_ID}")] and site.nodes == {}
    assert log == [f"ERROR: Create-Custom-SAS-Intelligent-Decisioning-Node.sas: nothing was created, because SAS Viya did not answer the "
                   f"request for the node type {NODE_ID} with 200 or 404 (status: {status})."]


@pytest.mark.parametrize("nodes", [{}, {NODE_ID: "Call LLM"}, {NODE_ID: "Other"}, {OTHER_ID: "Call LLM"}, {OTHER_ID: "Other"}],
                         ids=["none", "ours", "ours renamed", "clash", "another node"])
def test_create_never_posts_an_id_that_is_there(nodes):
    """The POST of an id that is there renames the node of that id before it fails: the program sends a POST only
    after the request for the id answered 404, and then with that id."""
    site = Site(nodes)
    follow(CREATE, site)

    for position, (method, url) in enumerate(site.requests):
        if (method, url) == ("POST", NODES):
            assert site.requests[:position].count(("GET", f"{NODES}/{NODE_ID}")) == 1 and NODE_ID not in nodes
    assert {node: name for node, name in site.nodes.items() if node in nodes} == nodes


# ---------------------------------------------------------------------------
# Update: the id first, the name second
# ---------------------------------------------------------------------------

def test_update_finds_the_node_under_the_id():
    site = Site({NODE_ID: "Call LLM"})
    log, macros = follow(UPDATE, site)

    assert site.requests == [("GET", f"{NODES}/{NODE_ID}"), ("GET", f"{NODES}/{NODE_ID}/content"), ("PUT", f"{NODES}/{NODE_ID}/content")]
    assert macros["_dntID"] == NODE_ID
    assert log == [f"NOTE: Update-Custom-SAS-Intelligent-Decisioning-Node.sas: the content of the node Call LLM under the id {NODE_ID} was updated."]


def test_update_falls_back_to_the_name_and_says_that_the_node_is_not_under_the_id_yet():
    site = Site({OTHER_ID: "Call LLM"})
    log, macros = follow(UPDATE, site)

    assert site.requests == [("GET", f"{NODES}/{NODE_ID}"), ("GET", f"{NODES}?filter=eq(name,'Call%20LLM')"),
                             ("GET", f"{NODES}/{OTHER_ID}/content"), ("PUT", f"{NODES}/{OTHER_ID}/content")]
    assert macros["_dntID"] == OTHER_ID and posts(site) == []
    assert log[0].startswith(f"NOTE: Update-Custom-SAS-Intelligent-Decisioning-Node.sas: the node Call LLM of this site is under the id "
                             f"{OTHER_ID}, not under the id {NODE_ID} that the accelerator ships. Its content is updated all the same.")
    assert "mdb decision-node-migrate" in log[0] and log[0].endswith(GUIDE)
    assert log[1] == f"NOTE: Update-Custom-SAS-Intelligent-Decisioning-Node.sas: the content of the node Call LLM under the id {OTHER_ID} was updated."


def test_update_without_any_node_updates_nothing_and_names_the_create_program():
    site = Site({OTHER_ID: "Other"})
    log, _ = follow(UPDATE, site)

    assert [method for method, _ in site.requests] == ["GET", "GET"]
    assert log == [f"ERROR: Update-Custom-SAS-Intelligent-Decisioning-Node.sas: nothing was updated, because there is no node under the id "
                   f"{NODE_ID} and none named Call LLM. Run Create-Custom-SAS-Intelligent-Decisioning-Node.sas to create it."]


def test_update_where_the_request_for_the_id_fails_updates_nothing():
    site = Site({NODE_ID: "Call LLM"}, by_id_status="403")
    log, _ = follow(UPDATE, site)

    assert site.requests == [("GET", f"{NODES}/{NODE_ID}")]
    assert log == [f"ERROR: Update-Custom-SAS-Intelligent-Decisioning-Node.sas: nothing was updated, because SAS Viya did not answer the "
                   f"request for the node type {NODE_ID} with 200 or 404 (status: 403)."]


def test_update_without_content_sends_no_put():
    site = Site({NODE_ID: "Call LLM"})
    site.content.clear()
    log, _ = follow(UPDATE, site)

    assert [method for method, _ in site.requests] == ["GET", "GET"]
    assert log == [f"ERROR: Update-Custom-SAS-Intelligent-Decisioning-Node.sas: nothing was updated, because the content of the node {NODE_ID} "
                   "could not be found. The API responded with 404: Not Found."]


# ---------------------------------------------------------------------------
# The guide
# ---------------------------------------------------------------------------

def test_the_guide_has_the_section_the_programs_point_to():
    docs = read(DOCS)
    section = docs[docs.index("## The Call LLM node and its id {#call-llm-node-uuid}"):]
    section = section[:section.index("\n## ", 1)] if "\n## " in section[1:] else section
    flat = " ".join(section.split())

    assert NODE_ID in flat and "mdb decision-node-migrate" in flat
    for said in ("Create-Custom-SAS-Intelligent-Decisioning-Node.sas", "dry run", "locked", "published", "no undo"):
        assert said in flat, said
    readme = " ".join(read(REPOSITORY / "SAS-Viya-Integrations" / "README.md").split())
    assert NODE_ID in readme and GUIDE in readme and "mdb decision-node-migrate" in readme
