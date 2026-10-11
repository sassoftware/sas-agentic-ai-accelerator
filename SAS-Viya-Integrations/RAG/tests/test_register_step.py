# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""RAG - Register Setup, the step file itself: the TLS verification checkbox.

The checkbox "Verify TLS certificates in the registered retrieval model" is
three things that have to agree and live in three places of one JSON file: a
control in the UI definition, its default in `values`, and the code that reads
the macro variable SAS Studio makes of it. Nothing parses a .step file before
SAS Studio does, so a control id that the code spells differently is a setting
that silently does nothing - here, a model that keeps verifying, or worse, one
that stops.

The tests read the SHIPPED file. The Python between `submit;` and `endsubmit;`
is executed as it is, against a SAS object and a SAS Viya that remember what
they were told; the SAS around it is checked as text, because only SAS runs
SAS.

The one fact these tests take on trust is SAS Studio's own: a ticked checkbox
reaches the code as the macro variable value 1 and an unticked one as 0. The
other steps of this folder that carry a checkbox read it that way.
"""
import json
import pathlib
import re
import sys
import tempfile
import types

import pytest

RAG = pathlib.Path(__file__).resolve().parents[1]
STEPS = RAG.parent / "Custom-Steps"
STEP = STEPS / "RAG - Register Setup.step"
TEMPLATE = RAG / "retrieve_context.py"

CONTROL = "_rgrg_sslVerify"
LABEL = "Verify TLS certificates in the registered retrieval model"
TLS_TAG = "TLS-Verification-Off"
BASE = "https://viya.example"
MODEL = "/modelRepository/models/model-1"
STACK_TAGS = ["LLM", "RAG", "all_minilm_l6_v2", "pgvector"]


# ---------------------------------------------------------------------------
# the step file, taken apart
# ---------------------------------------------------------------------------
def _step():
    return json.loads(STEP.read_text(encoding="utf-8"))


def _ui():
    return json.loads(_step()["ui"])


def _sas():
    return _step()["templates"]["SAS"]


def _python():
    """The program PROC PYTHON runs: what stands between submit; and
    endsubmit; of the SAS template."""
    return _sas().split("\nsubmit;\n", 1)[1].split("\nendsubmit;", 1)[0]


def _controls(node, found=None):
    found = [] if found is None else found
    if isinstance(node, dict):
        if "id" in node and "type" in node:
            found.append(node)
        for value in node.values():
            _controls(value, found)
    elif isinstance(node, list):
        for value in node:
            _controls(value, found)
    return found


def _control(control_id):
    return next(c for c in _controls(_ui()) if c["id"] == control_id)


def _studio_value(value):
    """What SAS Studio makes of a control's value: 1/0 for a checkbox, the
    `value` of a dropdown item, the text of everything else."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, dict):
        return str(value.get("value", ""))
    return str(value)


# ---------------------------------------------------------------------------
# the file's own conventions
# ---------------------------------------------------------------------------
def test_the_step_file_is_one_line_of_json_in_its_canonical_form():
    """One line, ASCII only, keys in the order they are in: the form every
    step of the folder is in, and the form a deploy sends unchanged."""
    raw = STEP.read_bytes().decode("utf-8")
    assert "\n" not in raw and "\r" not in raw
    assert raw.isascii()
    step = json.loads(raw)
    assert json.dumps(step) == raw
    assert list(step) == ["description", "displayName", "flowMetadata",
                          "metadataVersion", "name", "properties", "templates",
                          "type", "ui"]
    ui = json.loads(step["ui"])
    assert json.dumps(ui, indent="\t", ensure_ascii=False) == step["ui"]


def test_the_embedded_python_is_a_program():
    compile(_python(), "RAG - Register Setup.step", "exec")


# ---------------------------------------------------------------------------
# the control and its default
# ---------------------------------------------------------------------------
def test_the_checkbox_is_in_the_runtime_section_and_ticked_by_default():
    ui = _ui()
    definition = next(page for page in ui["pages"] if page["id"] == "pageDefinition")
    runtime = next(child for child in definition["children"]
                   if child["type"] == "section" and child["label"] == "Runtime")
    control = next(child for child in runtime["children"] if child["id"] == CONTROL)
    assert control == {"id": CONTROL, "type": "checkbox", "label": LABEL,
                       "visible": ""}
    # the JSON boolean, as the checkboxes of the other steps hold it
    assert ui["values"][CONTROL] is True


def test_the_checkbox_is_spelled_like_the_checkboxes_of_the_other_steps():
    """`checkbox` and a boolean default are not this step's invention: the
    control is compared with the ones SAS Studio already renders."""
    others = []
    for path in sorted(STEPS.glob("*.step")):
        if path == STEP:
            continue
        ui = json.loads(json.loads(path.read_text(encoding="utf-8"))["ui"])
        for control in _controls(ui):
            if control["type"] == "checkbox":
                others.append((set(control), type(ui["values"][control["id"]])))
    assert others, "no other step carries a checkbox any more"
    control = _control(CONTROL)
    assert (set(control), type(_ui()["values"][CONTROL])) in others


def test_every_control_has_one_id_and_every_value_a_control():
    ui = _ui()
    ids = [control["id"] for control in _controls(ui)]
    assert len(ids) == len(set(ids))
    assert set(ui["values"]) <= set(ids)


# ---------------------------------------------------------------------------
# the code reads the control it defines
# ---------------------------------------------------------------------------
def test_the_code_reads_the_control_under_the_id_the_ui_gives_it():
    assert _python().count('SAS.symget("%s")' % CONTROL) == 1
    # and nowhere under a name that only looks like it
    names = set(re.findall(r"_rgrg_ssl\w*|_rgrg_tls\w*|_rgrg_verify\w*", _sas(),
                           re.IGNORECASE))
    # two macro variables for what the Python found, and the macro that
    # says it in the log
    assert names == {CONTROL, "_rgrg_tlsState", "_rgrg_tlsTag",
                     "_rgrg_tls_warnings"}


def test_a_missing_or_blank_control_reads_as_ticked_in_sas():
    """A flow saved before the control existed carries no value for it. The
    setup macro gives it the safe one before anything reads it."""
    sas = _sas()
    setup = sas.split("%macro _rgrg_setup;", 1)[1].split("%mend _rgrg_setup;", 1)[0]
    assert "%%if not %%symexist(%s) %%then %%global %s;" % (CONTROL, CONTROL) in setup
    assert ("%%if %%length(%%superq(%s)) EQ 0 %%then %%let %s = 1;"
            % (CONTROL, CONTROL)) in setup
    assert sas.index("%_rgrg_setup;") < sas.index("proc python")


def _verify_requested(macro_value, missing=False):
    """The step's own `verify_requested`, cut out of the embedded program and
    called with a SAS that holds one macro variable."""
    import ast

    tree = ast.parse(_python())
    main = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "main")
    function = next(node for node in main.body
                    if isinstance(node, ast.FunctionDef)
                    and node.name == "verify_requested")

    class _Sas:
        @staticmethod
        def symget(name):
            assert name == CONTROL
            if missing:
                raise RuntimeError("macro variable not found")
            return macro_value

    scope = {"SAS": _Sas}
    exec(compile(ast.Module(body=[function], type_ignores=[]),
                 "verify_requested", "exec"), scope)
    return scope["verify_requested"]()


@pytest.mark.parametrize("macro_value", ["0", " 0 ", "0.0", 0, 0.0, "false",
                                         "FALSE", " False ", "no", "No", False])
def test_an_unticked_box_asks_for_verification_off(macro_value):
    assert _verify_requested(macro_value) is False


@pytest.mark.parametrize("macro_value", ["1", " 1 ", "1.0", 1, 1.0, True, "true",
                                         "yes", "", "   ", None, "off", "on",
                                         "garbage", "2", "-1", "nan"])
def test_everything_else_keeps_verification_on(macro_value):
    """A blank, a missing or an unknown value is not a request to stop
    verifying: every way of misreading the control fails the safe way."""
    assert _verify_requested(macro_value) is True


def test_a_macro_variable_that_cannot_be_read_keeps_verification_on():
    assert _verify_requested(None, missing=True) is True


def test_the_default_of_the_control_is_read_as_verification_on():
    """The control's default, as SAS Studio hands it over, through the code
    that reads it: the two cannot drift apart without this failing."""
    assert _verify_requested(_studio_value(_ui()["values"][CONTROL])) is True
    assert _verify_requested(_studio_value(False)) is False


# ---------------------------------------------------------------------------
# the embedded program, run
# ---------------------------------------------------------------------------
class _Reply:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.content = b"x"
        self.text = text
        self.headers = {"ETag": 'W/"stub"'}

    def json(self):
        return self._payload

    def raise_for_status(self):
        assert self.status_code < 400


class _Viya:
    """The SAS Viya the step talks to, remembering its one model.

    Two doors, as in the step: the plain `requests.get` calls that find the
    rag_core folder and the template, and the session rag_core's ViyaClient
    registers through. The rag_core folder is empty on purpose - the step
    then imports the rag_core of this repository, which is the one under
    test.
    """

    def __init__(self, template, tags=None, refuse_tag_update=False, upload="",
                 unreadable=False, listing="tags"):
        self.template = template
        self.headers = {}
        self.model = None
        if tags is not None:
            self.model = {"id": "model-1", "name": "RAG Retrieval - HR",
                          "projectId": "proj-1", "projectVersionId": "ver-1",
                          "tags": list(tags)}
        self.score = None
        self.files = {}
        self.refuse_tag_update = refuse_tag_update
        # the upload of the score code: "" (stored), "lost" (stored, and the
        # answer never arrives) or "refused" (the service says 409)
        self.upload = upload
        # a model that cannot be read: every GET of it is answered 500
        self.unreadable = unreadable
        # "absent": the entry of the model in the listing has no `tags` key
        self.listing = listing
        self.model_puts = 0

    # -- requests.get -------------------------------------------------------
    def get(self, url, **kwargs):
        assert url.startswith(BASE), url
        return self.request("GET", url, **kwargs)

    # -- requests.Session().request ----------------------------------------
    def request(self, method, url, **kwargs):
        assert url.startswith(BASE), url
        endpoint = url[len(BASE):]
        params = kwargs.get("params") or {}
        body = kwargs.get("json")
        if endpoint == "/folders/folders/@item":
            return _Reply(200, {"id": "folder:" + str(params.get("path"))})
        if endpoint.endswith("/members"):
            if endpoint.endswith("/RAG/models/members"):
                return _Reply(200, {"items": [{"name": "retrieve_context.py",
                                               "uri": "/files/files/template"}]})
            return _Reply(200, {"items": []})
        if endpoint == "/files/files/template/content":
            return _Reply(200, text=self.template)
        if endpoint == "/files/files":
            self.files[params["filename"]] = kwargs["data"].decode("utf-8")
            return _Reply(201, {"id": "file-1"})
        if "/projectVersions" in endpoint:
            return _Reply(200, {"items": [{"id": "ver-1", "name": "Version 1"}]})
        if endpoint == "/modelRepository/projects/proj-1":
            return _Reply(200, {"id": "proj-1", "latestVersion": "Version 1"})
        if endpoint == "/modelRepository/projects":
            return _Reply(200, {"items": [{"id": "proj-1",
                                           "name": "RAG Engineering"}]})
        if endpoint == MODEL + "/contents":
            if params.get("role") == "score":
                if self.upload == "refused":
                    return _Reply(409, {"httpStatusCode": 409}, text="stub refused")
                self.score = kwargs["files"]["files"][1].decode("utf-8")
                if self.upload == "lost":
                    raise TimeoutError("read timed out")
            return _Reply(201, {})
        if endpoint == MODEL:
            if method == "GET":
                if self.unreadable:
                    return _Reply(500, text="stub refused")
                return _Reply(200, dict(self.model, tags=list(self.model["tags"])))
            self.model_puts += 1
            if self.refuse_tag_update and self.model_puts > 1:
                return _Reply(500, text="stub refused")
            self.model = dict(body)
            return _Reply(200, dict(self.model))
        if endpoint == "/modelRepository/models":
            if method == "GET":
                listed = dict(self.model) if self.model else None
                if listed and self.listing == "absent":
                    listed.pop("tags")
                return _Reply(200, {"items": [listed] if listed else []})
            # like the service: a create drops the tags
            self.model = dict(body, id="model-1", tags=[])
            return _Reply(201, {"count": 1, "items": [{"id": "model-1"}]})
        raise AssertionError("the step made a request this test does not know: "
                             + method + " " + endpoint)


class _Sas:
    """PROC PYTHON's SAS object: macro variables, the log, the work path."""

    def __init__(self, workpath, macros):
        self.workpath = str(workpath)
        self.macros = dict(macros)
        self.log = []

    def symget(self, name):
        return self.macros.get(name, "")

    def symput(self, name, value):
        self.macros[name] = value

    def logMessage(self, message, *_):
        self.log.append(message)


def _run_step(monkeypatch, tmp_path, checkbox="default", template=None,
              old_rag_core=False, flow="", **service_options):
    """Run the step's Python the way SAS Studio would: every control at the
    default the step file gives it, the required ones filled in, and the
    checkbox as the test sets it ("default" = as shipped, None = no macro
    variable value at all). `flow` is the path in the control Studio Flow to
    generate the scheduled job from; the stub holds no flow, so a path there
    is a flow that does not exist."""
    import requests
    import rag_core.adapters
    import rag_core.credentials
    import rag_core.registration

    service = _Viya(TEMPLATE.read_text(encoding="utf-8")
                    if template is None else template, **service_options)

    macros = {name: _studio_value(value)
              for name, value in _ui()["values"].items()}
    macros.update({
        "_rgrg_state": "run",
        "_rgrg_viyaHost": BASE,
        "_rgrg_setupName": "HR",
        "_rgrg_storeHost": "db.example",
        "_rgrg_storeDb": "postgres",
        "_rgrg_ragProject": "RAG_HR",
        "_rgrg_tablesCaslib": "casuser",
        "_rgrg_cashost": "cas.example",
        "_rgrg_casport": "5570",
        "_rgrg_casuuid": "session-1",
        # path controls arrive scheme-prefixed
        "_rgrg_contentRoot": "sascontent:" + macros["_rgrg_contentRoot"],
        "_rgrg_modelTemplate": "sascontent:" + macros["_rgrg_modelTemplate"],
        "_rgrg_ragCorePath": "sascontent:" + macros["_rgrg_ragCorePath"],
    })
    if checkbox is None:
        macros[CONTROL] = ""
    elif checkbox != "default":
        macros[CONTROL] = checkbox
    if flow:
        macros["_rgrg_flowPath"] = "sascontent:" + flow
    # as the SAS around the program sets them before PROC PYTHON
    macros["_rgrg_tlsState"] = "unknown"
    macros["_rgrg_tlsTag"] = "ok"
    sas = _Sas(tmp_path, macros)

    (tmp_path / "rgrg_inventory_in.json").write_text(json.dumps([{
        "doc_id": "d1", "rag_project": "RAG_HR", "tables_caslib": "casuser",
        "pipeline_version": "v1", "config_hash": "cfg-1", "run_id": "run-1",
        "config_json": json.dumps({"embed_model": "all_minilm_l6_v2",
                                   "backend": "pgvector",
                                   "collection": "rag_hr_policies_v1"})}]),
        encoding="utf-8")

    # no CAS: the ledger table is simply not there
    class _Cas:
        def __init__(self, **_):
            self.table = types.SimpleNamespace(
                tableExists=lambda **_: types.SimpleNamespace(exists=False))

        def close(self, **_):
            pass

    monkeypatch.setitem(sys.modules, "swat", types.SimpleNamespace(CAS=_Cas))
    # no network: both doors of `requests` lead to the stub
    monkeypatch.setattr(requests, "get", service.get)
    monkeypatch.setattr(requests, "Session", lambda: service)

    # no vector store: the step registers without the DDL artifact then
    def _no_store(*_, **__):
        raise RuntimeError("no vector store in this test")

    monkeypatch.setattr(rag_core.credentials, "fetch_secrets", _no_store)
    monkeypatch.setattr(rag_core.adapters, "get_adapter", _no_store)
    # the step puts its download of rag_core first on sys.path
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(tempfile, "mkdtemp",
                        lambda **_: str(tmp_path / "rag_core_staging"))
    if old_rag_core:
        # a rag_core deployed before the setting: its register_setup returns
        # none of the keys that say what the stored code does, and puts
        # nothing on an exception
        current = rag_core.registration.register_setup

        def _before_the_setting(*args, **kwargs):
            try:
                outcome = current(*args, **kwargs)
            except Exception as error:
                raise type(error)(str(error)) from None
            for key in ("ssl_verify", "ssl_verify_in_template", "tls_tag_error"):
                outcome.pop(key)
            return outcome

        monkeypatch.setattr(rag_core.registration, "register_setup",
                            _before_the_setting)

    exec(compile(_python(), "RAG - Register Setup.step", "exec"), {"SAS": sas})

    report = []
    if (tmp_path / "rgrg_report_out.json").exists():
        report = json.loads((tmp_path / "rgrg_report_out.json").read_text(
            encoding="utf-8"))
    return types.SimpleNamespace(sas=sas, service=service, report=report)


def _model_row(run):
    return next(row for row in run.report if row["registered"] == "model")


def _old_template():
    """The template of a content root deployed before the setting."""
    template = TEMPLATE.read_text(encoding="utf-8")
    assert template.count("\nSSL_VERIFY = True\n") == 1
    return template.replace("\nSSL_VERIFY = True\n", "\n")


def test_with_the_box_as_shipped_the_model_verifies_and_is_not_tagged(
        monkeypatch, tmp_path):
    run = _run_step(monkeypatch, tmp_path)
    assert run.sas.macros["_rgrg_rc"] == "0", run.sas.macros.get("_rgrg_error")
    assert "SSL_VERIFY = True" in run.service.score.splitlines()
    assert run.service.model["tags"] == STACK_TAGS
    assert run.sas.macros["_rgrg_tlsState"] == "on"
    assert run.sas.macros["_rgrg_tlsTag"] == "ok"
    assert _model_row(run)["detail"] == (
        "scoreable python model, score file manifested, "
        "TLS certificate verification on")
    assert ("retrieval model: TLS certificate verification on"
            in run.sas.macros["_rgrg_summary"])


def test_with_the_box_unticked_the_model_does_not_verify_and_is_tagged(
        monkeypatch, tmp_path):
    run = _run_step(monkeypatch, tmp_path, checkbox="0")
    assert run.sas.macros["_rgrg_rc"] == "0", run.sas.macros.get("_rgrg_error")
    assert "SSL_VERIFY = False" in run.service.score.splitlines()
    # the copy written to SAS Content is the copy that was registered
    assert run.service.files["retrieve_context.py"] == run.service.score
    assert run.service.model["tags"] == STACK_TAGS + [TLS_TAG]
    assert run.sas.macros["_rgrg_tlsState"] == "off"
    assert run.sas.macros["_rgrg_tlsTag"] == "ok"
    assert _model_row(run)["detail"] == (
        "scoreable python model, score file manifested, "
        "TLS certificate verification OFF (tag TLS-Verification-Off)")
    assert ("retrieval model: TLS certificate verification OFF"
            in run.sas.macros["_rgrg_summary"])
    assert any("unticked" in line for line in run.sas.log)


def test_a_blank_checkbox_value_registers_a_model_that_verifies(
        monkeypatch, tmp_path):
    run = _run_step(monkeypatch, tmp_path, checkbox=None)
    assert "SSL_VERIFY = True" in run.service.score.splitlines()
    assert TLS_TAG not in run.service.model["tags"]
    assert run.sas.macros["_rgrg_tlsState"] == "on"


def test_ticking_the_box_again_removes_the_tag(monkeypatch, tmp_path):
    run = _run_step(monkeypatch, tmp_path, checkbox="1",
                    tags=STACK_TAGS + [TLS_TAG])
    assert "SSL_VERIFY = True" in run.service.score.splitlines()
    assert run.service.model["tags"] == STACK_TAGS
    assert run.sas.macros["_rgrg_tlsState"] == "on"
    assert run.sas.macros["_rgrg_tlsTag"] == "ok"


def test_an_old_template_with_the_box_unticked_is_reported_as_still_verifying(
        monkeypatch, tmp_path):
    """The same meaning as the Builder's ragBuilderTlsTemplateOld: the
    registration goes through, the model verifies and is not tagged, and the
    step says that the RAG content has to be deployed again."""
    run = _run_step(monkeypatch, tmp_path, checkbox="0", template=_old_template())
    assert run.sas.macros["_rgrg_rc"] == "0", run.sas.macros.get("_rgrg_error")
    assert not [line for line in run.service.score.splitlines()
                if line.startswith("SSL_VERIFY")]
    assert TLS_TAG not in run.service.model["tags"]
    assert run.sas.macros["_rgrg_tlsState"] == "stale"
    assert _model_row(run)["detail"] == (
        "scoreable python model, score file manifested, TLS certificate "
        "verification still ON - the retrieval model template predates the "
        "setting, deploy the RAG content again")


def test_an_old_template_with_the_box_ticked_is_nothing_to_report(
        monkeypatch, tmp_path):
    run = _run_step(monkeypatch, tmp_path, template=_old_template())
    assert run.sas.macros["_rgrg_tlsState"] == "on"
    assert TLS_TAG not in run.service.model["tags"]


def test_a_rag_core_older_than_the_step_is_reported_the_same_way(
        monkeypatch, tmp_path):
    """The step and rag_core are deployed by one script, but they are two
    things in SAS Content. A step that is newer than the rag_core it downloads
    must not report a setting as applied that nothing applied."""
    run = _run_step(monkeypatch, tmp_path, checkbox="0", old_rag_core=True,
                    template=_old_template())
    assert run.sas.macros["_rgrg_rc"] == "0", run.sas.macros.get("_rgrg_error")
    assert run.sas.macros["_rgrg_tlsState"] == "stale"
    assert "the deployed rag_core predates the setting" in _model_row(run)["detail"]


@pytest.mark.parametrize("checkbox,tags,state,said", [
    ("0", STACK_TAGS, "off", "the tag TLS-Verification-Off could not be written"),
    ("1", STACK_TAGS + [TLS_TAG], "on",
     "the tag TLS-Verification-Off could not be removed"),
])
def test_a_tag_that_could_not_be_updated_reaches_the_log_and_the_report(
        monkeypatch, tmp_path, checkbox, tags, state, said):
    run = _run_step(monkeypatch, tmp_path, checkbox=checkbox, tags=tags,
                    refuse_tag_update=True)
    assert run.sas.macros["_rgrg_rc"] == "0", run.sas.macros.get("_rgrg_error")
    assert run.sas.macros["_rgrg_tlsState"] == state
    assert run.sas.macros["_rgrg_tlsTag"] == "failed"
    assert said in _model_row(run)["detail"]
    # and the report does not claim a tag the model does not carry
    assert "(tag TLS-Verification-Off)" not in _model_row(run)["detail"]


def test_the_report_detail_fits_the_column_sas_reads_it_into(monkeypatch, tmp_path):
    """The DATA step that builds the report declares `detail $ 256`."""
    declared = int(re.search(r"detail \$ (\d+)", _sas()).group(1))
    longest = 0
    for options in ({"checkbox": "0"},
                    {"checkbox": "0", "template": _old_template()},
                    {"checkbox": "0", "template": _old_template(),
                     "old_rag_core": True},
                    {"checkbox": "0", "tags": STACK_TAGS, "refuse_tag_update": True},
                    {"checkbox": "1", "tags": STACK_TAGS + [TLS_TAG],
                     "refuse_tag_update": True}):
        with monkeypatch.context() as patch:
            run = _run_step(patch, tmp_path, **options)
        longest = max(longest, len(_model_row(run)["detail"]))
    assert 0 < longest <= declared


# ---------------------------------------------------------------------------
# a run that fails AFTER the score code was stored
# ---------------------------------------------------------------------------
MISSING_FLOW = "/Flows/No Such Flow.flw"


def test_a_missing_flow_with_the_box_unticked_still_says_the_model_does_not_verify(
        monkeypatch, tmp_path):
    """The flow named in the step does not exist. The step fails - and the
    model in SAS Model Manager holds score code that does not verify. Before,
    the failing run said nothing about it and left the state unknown."""
    run = _run_step(monkeypatch, tmp_path, checkbox="0", flow=MISSING_FLOW)
    assert run.sas.macros.get("_rgrg_rc") != "0"
    assert "no flow named" in run.sas.macros["_rgrg_error"]
    # the model is what the log has to describe
    assert "SSL_VERIFY = False" in run.service.score.splitlines()
    assert run.service.model["tags"] == STACK_TAGS + [TLS_TAG]
    assert run.sas.macros["_rgrg_tlsState"] == "off"
    assert run.sas.macros["_rgrg_tlsTag"] == "ok"
    assert any("the step failed after the score code was stored" in line
               and "TLS certificate verification OFF (tag TLS-Verification-Off)"
               in line for line in run.sas.log)
    assert run.report == []


def test_a_missing_flow_with_a_tag_that_did_not_follow_says_that_too(
        monkeypatch, tmp_path):
    run = _run_step(monkeypatch, tmp_path, checkbox="0", flow=MISSING_FLOW,
                    tags=STACK_TAGS, refuse_tag_update=True)
    assert run.sas.macros.get("_rgrg_rc") != "0"
    assert run.sas.macros["_rgrg_tlsState"] == "off"
    assert run.sas.macros["_rgrg_tlsTag"] == "failed"
    assert TLS_TAG not in run.service.model["tags"]


def test_a_missing_flow_with_the_box_ticked_says_the_model_verifies(
        monkeypatch, tmp_path):
    run = _run_step(monkeypatch, tmp_path, flow=MISSING_FLOW)
    assert run.sas.macros.get("_rgrg_rc") != "0"
    assert "SSL_VERIFY = True" in run.service.score.splitlines()
    assert run.sas.macros["_rgrg_tlsState"] == "on"
    assert run.sas.macros["_rgrg_tlsTag"] == "ok"


def test_a_missing_flow_with_an_old_template_is_still_the_stale_state(
        monkeypatch, tmp_path):
    run = _run_step(monkeypatch, tmp_path, checkbox="0", flow=MISSING_FLOW,
                    template=_old_template())
    assert run.sas.macros.get("_rgrg_rc") != "0"
    assert run.sas.macros["_rgrg_tlsState"] == "stale"


@pytest.mark.parametrize("checkbox,tags", [
    ("0", STACK_TAGS), ("1", STACK_TAGS + [TLS_TAG])])
def test_an_upload_without_an_answer_is_said_as_not_known(
        monkeypatch, tmp_path, checkbox, tags):
    """The score code may be stored, and nothing came back. The step neither
    claims the state of the new code nor the old: it says that it does not
    know, and the tag is as it was."""
    run = _run_step(monkeypatch, tmp_path, checkbox=checkbox, tags=tags,
                    upload="lost")
    assert run.sas.macros.get("_rgrg_rc") != "0"
    assert "TimeoutError" in run.sas.macros["_rgrg_error"]
    assert run.sas.macros["_rgrg_tlsState"] == "unsure"
    assert run.sas.macros["_rgrg_tlsTag"] == "ok"
    assert run.service.model["tags"] == tags
    assert run.service.model_puts == 1
    assert any("it is not known whether the score code was stored" in line
               for line in run.sas.log)


def test_an_upload_the_service_refused_leaves_the_state_unknown(
        monkeypatch, tmp_path):
    """Nothing was stored: the model is what it was, and the step has nothing
    to say about a score code it did not write."""
    run = _run_step(monkeypatch, tmp_path, checkbox="0",
                    tags=STACK_TAGS + [TLS_TAG], upload="refused")
    assert run.sas.macros.get("_rgrg_rc") != "0"
    assert "contents returned 409" in run.sas.macros["_rgrg_error"]
    assert run.service.score is None
    assert run.sas.macros["_rgrg_tlsState"] == "unknown"
    assert run.service.model["tags"] == STACK_TAGS + [TLS_TAG]


@pytest.mark.parametrize("upload,state", [("refused", "unknown"),
                                          ("lost", "unsure")])
def test_a_tag_written_to_a_model_nothing_was_known_about_is_said(
        monkeypatch, tmp_path, upload, state):
    """The model cannot be read and its entry in the listing shows no tags:
    rag_core writes the tag before the upload. When the score code is then
    not stored, or not known to be, the model carries a tag this run put
    there - over code that verifies, in the refused case - and the step says
    so instead of nothing."""
    run = _run_step(monkeypatch, tmp_path, checkbox="1", tags=STACK_TAGS,
                    upload=upload, unreadable=True, listing="absent")
    assert run.sas.macros.get("_rgrg_rc") != "0"
    assert run.service.model["tags"] == STACK_TAGS + [TLS_TAG]
    assert run.sas.macros["_rgrg_tlsState"] == state
    assert run.sas.macros["_rgrg_tlsTag"] == "assumed"
    assert any("was written to it unseen" in line for line in run.sas.log)


def test_a_tag_the_listing_showed_is_not_an_assumed_one(monkeypatch, tmp_path):
    run = _run_step(monkeypatch, tmp_path, checkbox="1", tags=STACK_TAGS,
                    upload="refused", unreadable=True)
    assert run.sas.macros.get("_rgrg_rc") != "0"
    assert run.service.model["tags"] == STACK_TAGS
    assert run.sas.macros["_rgrg_tlsTag"] == "ok"
    assert not any("unseen" in line for line in run.sas.log)


def test_a_stored_score_code_settles_what_was_assumed(monkeypatch, tmp_path):
    """The upload goes through and a later request fails: the tag was to be
    settled after the score code, so the step reports that (here: it could
    not be, the model cannot be read) and not the assumption before it."""
    run = _run_step(monkeypatch, tmp_path, checkbox="1", tags=STACK_TAGS,
                    unreadable=True, listing="absent", flow=MISSING_FLOW)
    assert run.sas.macros.get("_rgrg_rc") != "0"
    assert run.sas.macros["_rgrg_tlsState"] == "on"
    assert run.sas.macros["_rgrg_tlsTag"] == "failed"


def test_a_rag_core_older_than_the_step_leaves_a_failing_run_unknown(
        monkeypatch, tmp_path):
    """An older rag_core puts nothing on the exception. The step then knows
    as little as before, and says nothing it cannot know."""
    run = _run_step(monkeypatch, tmp_path, checkbox="0", flow=MISSING_FLOW,
                    old_rag_core=True)
    assert run.sas.macros.get("_rgrg_rc") != "0"
    assert run.sas.macros["_rgrg_tlsState"] == "unknown"


def test_a_failure_before_the_registration_leaves_the_state_unknown(
        monkeypatch, tmp_path):
    run = _run_step(monkeypatch, tmp_path, checkbox="0",
                    template="print('not the template')\n")
    assert run.sas.macros.get("_rgrg_rc") != "0"
    assert run.service.model is None
    assert run.sas.macros["_rgrg_tlsState"] == "unknown"


# ---------------------------------------------------------------------------
# the SAS log: what the step says for each state the Python can set
# ---------------------------------------------------------------------------
def _status_macro():
    return _sas().split("%macro _rgrg_status;", 1)[1].split(
        "%mend _rgrg_status;", 1)[0]


def _tls_macro():
    """The macro that writes the WARNING lines about TLS verification."""
    return _sas().split("%macro _rgrg_tls_warnings;", 1)[1].split(
        "%mend _rgrg_tls_warnings;", 1)[0]


def _warnings(condition):
    """The WARNING lines of the first %do group that follows a condition."""
    after = _tls_macro().split(condition, 1)[1]
    group = after.split("run;", 1)[0]
    return re.findall(r'putLog "(WARNING: [^"]*)";', group)


def test_the_states_the_python_sets_are_the_states_the_sas_reads():
    python, status = _python(), _tls_macro()
    assert set(re.findall(r"tls_state = '(\w+)'", python)) == {"on", "off", "stale"}
    assert "SAS.symput('_rgrg_tlsState', tls_state)" in python
    assert "SAS.symput('_rgrg_tlsTag', 'failed' if tls_tag_failed else 'ok')" in python
    # the one state that is not about a stored score code
    assert re.findall(r"SAS\.symput\('_rgrg_tlsState', '(\w+)'\)", python) == ["unsure"]
    assert "%if &_rgrg_tlsState. EQ off %then %do;" in status
    assert "%if &_rgrg_tlsState. EQ stale %then %do;" in status
    assert "%if &_rgrg_tlsState. EQ unsure %then %do;" in status
    assert status.count("%if &_rgrg_tlsTag. EQ failed %then %do;") == 2
    # every state SAS compares with is one the Python sets, or the start value
    compared = set(re.findall(r"&_rgrg_tlsState\. EQ (\w+)", _sas()))
    assert compared == {"on", "off", "stale", "unsure"}
    # both are defined before the Python can be skipped or fail
    sas = _sas()
    assert sas.index("%let _rgrg_tlsState = unknown;") < sas.index("proc python")
    assert sas.index("%let _rgrg_tlsTag = ok;") < sas.index("proc python")


def test_verification_off_is_a_warning_that_names_the_calls_and_the_way_back():
    lines = _warnings("%if &_rgrg_tlsState. EQ off %then %do;")
    text = " ".join(lines)
    assert "does NOT verify TLS certificates" in text
    assert "embedding container" in text
    assert "SAS Viya credentials service" in text
    assert "RAGEMBED_SSLVERIFY=true" in text and "RAGSTORE_SSLVERIFY=true" in text
    assert "publishing destination" in text
    assert LABEL in text


def test_an_old_template_is_a_warning_that_says_on_and_deploy_again():
    lines = _warnings("%if &_rgrg_tlsState. EQ stale %then %do;")
    text = " ".join(lines)
    assert "TLS certificate verification is still ON" in text
    assert LABEL + " is unticked" in text
    assert "no SSL_VERIFY line" in text
    assert "deploy the RAG content again (deploy-rag-content)" in text


def test_a_tag_that_could_not_be_updated_is_a_warning_in_both_directions():
    status = _tls_macro()
    assert ("the tag TLS-Verification-Off could not be written to the model"
            in status)
    assert ("the tag TLS-Verification-Off could not be removed from the model"
            in status)
    # the tag is only claimed where the update went through
    off = status.split("%if &_rgrg_tlsState. EQ off %then %do;", 1)[1]
    failed, carried = off.split("%else %do;", 1)
    assert "could not be written" in failed.split(
        "%if &_rgrg_tlsTag. EQ failed %then %do;", 1)[1]
    assert "the model carries the tag TLS-Verification-Off" in carried.split(
        "%end;", 1)[0]


def test_every_warning_is_a_line_sas_prints_as_a_warning_without_complaining():
    """A line is coloured as a warning when it starts with `WARNING: `. A
    quoted string of more than 262 characters makes SAS write a warning of
    its own, about a quotation mark that may be unbalanced. And inside a
    macro, % and & in the text would be read as macro triggers."""
    literals = re.findall(r'putLog "([^"]*)";', _tls_macro())
    warnings = [text for text in literals if text.startswith("WARNING")]
    # off: three lines, then the tag written or not; stale: three lines; not
    # known whether the score code was stored: two lines; the tag written to
    # a model that could not be read: three lines; and the tag that could
    # not be removed
    assert len(warnings) == 14
    # and one in the status macro, for the run that failed after the upload
    literals = re.findall(r'putLog "([^"]*)";', _status_macro())
    failed = [text for text in literals if text.startswith("WARNING")]
    assert len(failed) == 1
    for text in warnings + failed:
        assert text.startswith("WARNING: RAG - Register Setup: "), text
        assert len(text) <= 262, len(text)
        assert not set(text) & set("%&;'"), text


def test_an_upload_without_an_answer_is_a_warning_that_says_run_again():
    lines = _warnings("%if &_rgrg_tlsState. EQ unsure %then %do;")
    text = " ".join(lines)
    assert len(lines) == 2
    assert "it is not known whether SAS Model Manager stored the score code" in text
    assert "the file may have been stored all the same" in text
    assert ("the tag TLS-Verification-Off was not updated and may not say what "
            "the stored score code does") in text
    assert "Run this step again" in text


def test_a_tag_written_unseen_is_a_warning_of_its_own():
    """Not in the branch of a model that does not verify: that state is set
    only once the score code is stored, and the tag is settled after it."""
    macro = _tls_macro()
    condition = "%if &_rgrg_tlsTag. EQ assumed %then %do;"
    assert macro.count(condition) == 1
    assert macro.index(condition) > macro.index(
        "%if &_rgrg_tlsState. EQ unsure %then %do;")
    lines = _warnings(condition)
    text = " ".join(lines)
    assert len(lines) == 3
    assert "the model could not be read before this run updated it" in text
    assert "the listing of the models did not show its tags" in text
    assert ("wrote the tag TLS-Verification-Off to the model, without knowing "
            "whether the model carried it") in text
    assert "the tag may not say what the model does. Run this step again" in text
    # set by the Python only for a run that did not store its score code
    python = _python()
    assert python.count("SAS.symput('_rgrg_tlsTag', 'assumed')") == 1
    assert ('if stored != "stored" and reached.get("tls_tag_assumed") is True:'
            in python)


def test_a_failed_run_writes_the_warnings_before_it_aborts():
    """The fail branch of the status macro: the ERROR line, the line that
    says the score code is stored, the WARNING lines of the model, and only
    then the abort. Before, the branch returned without them."""
    status = _status_macro()
    branch = status.split("%if &_rgrg_state. EQ fail %then %do;", 1)[1].split(
        "%return;", 1)[0]
    error = branch.index('putLog "ERROR: RAG - Register Setup failed: '
                         '%superq(_rgrg_error)";')
    stored = branch.index("the step failed after the score code of the "
                          "retrieval model was stored")
    warnings = branch.index("%_rgrg_tls_warnings;")
    abort = branch.index("abort 42;")
    assert error < stored < warnings < abort
    # the line about the stored score code is written for the three states
    # that mean a stored score code, and for no other
    for state in ("on", "off", "stale"):
        assert "%%if &_rgrg_tlsState. EQ %s %%then %%let stored = 1;" % state in branch
    assert branch.count("%let stored = 1;") == 3
    assert "%let stored = 0;" in branch and "%if &stored. EQ 1 %then %do;" in branch
    assert "%local stored;" in status
    # a run that went through writes the same lines, after its summary
    after = status.split("%return;")[-1]
    assert after.index('putLog "NOTE: ----- RAG - Register Setup -----";') \
        < after.index("%_rgrg_tls_warnings;")
    assert status.count("%_rgrg_tls_warnings;") == 2
    # and the macro is defined before the one that calls it
    sas = _sas()
    assert sas.index("%mend _rgrg_tls_warnings;") < sas.index("%macro _rgrg_status;")


@pytest.mark.parametrize("macro", ["status", "tls"])
def test_the_status_macro_closes_every_group_it_opens(macro):
    """Nothing here runs SAS. What can be counted is counted: a %do without
    its %end swallows the rest of the step, and the log lines with it."""
    status = _status_macro() if macro == "status" else _tls_macro()
    assert status.count("%do;") == status.count("%end;")
    assert status.count("data _null_;") == len(re.findall(r"^\s*run;$", status,
                                                          re.MULTILINE))
    assert status.count('"') % 2 == 0


# ---------------------------------------------------------------------------
# the About page
# ---------------------------------------------------------------------------
def test_the_about_page_documents_the_checkbox_and_its_changelog_entry():
    documentation = _control("textDocumentation")["text"]
    assert LABEL in documentation
    for said in ("SSL_VERIFY = False", TLS_TAG, "RAGEMBED_SSLVERIFY",
                 "RAGSTORE_SSLVERIFY", "embedding container",
                 "SAS Viya credentials service", "deploy-rag-content"):
        assert said in documentation, said
    # a run that fails after the upload, and one that does not know
    assert "in the log of the failing run as well, below its ERROR line" in documentation
    assert "the tag may then not say what the stored score code does" in documentation
    changelog = _control("textChangelog")["text"]
    newest = changelog.split("\n* Version: ")[0]
    assert newest.startswith("* Version: 1.3 (11OCT2026)\n      - ")
    assert LABEL in newest and TLS_TAG in newest
    assert "A run that fails after the score code was stored still says" in newest
    versions = re.findall(r"^\* Version: (\d+\.\d+) ", changelog, re.MULTILINE)
    assert versions == sorted(versions, reverse=True) and versions[0] == "1.3"
