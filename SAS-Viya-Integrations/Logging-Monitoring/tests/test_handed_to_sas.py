"""What the Python of the three programs hands to SAS: Log-Parser-Code.sas, Get-All-Prompts.sas and
Link-Logs-To-Models.sas, and the two custom steps that run the first two.

SAS.logMessage writes its text into a statement of the macro language and SAS.symput into an
assignment. There a semicolon ends the statement and the rest runs as code, a percent sign or an
ampersand starts a macro call, a quote or the start of a comment swallows what follows. The
three programs carry one function that makes a text safe for that way, and every text takes it:
the first tests read the call sites from the text of the programs, the others send names
through each program that would have ended up as code.
"""
import ast

import pandas as pd
import pytest

import get_all_prompts_harness as pull
import link_logs_harness as link
import log_parser_harness as parser

# The Python of the three programs and of the two steps
SOURCES = {
    "Log-Parser-Code.sas": lambda: parser.python_block(parser.code_file_program()),
    "LLM - Log Parser.step": lambda: parser.python_block(parser.step_program()),
    "Get-All-Prompts.sas": lambda: pull.python_block(pull.code_file_program()),
    "LLM - Get All Prompts.step": lambda: pull.python_block(pull.step_program()),
    "Link-Logs-To-Models.sas": lambda: link.python_block(link.code_file_program()),
}

# What of the SAS object hands a text to SAS, with the places of the arguments that are that text,
# and what only reads from SAS. Anything else of the SAS object is a way this file does not know.
HANDS_OVER = {"logMessage": (0,), "symput": (0, 1)}
READS = {"symget", "sasfnc"}
# The one argument of the two that is no text of the program: NOTE, WARNING or ERROR
KEYWORDS = {"logMessage": {"messageType"}, "symput": set()}
ESCAPING = "sas_safe"


def call_sites(source):
    """Every place of a program's Python that hands something to SAS, as (what of the SAS object, function it
    stands in), and why a place does not send its text through the escaping function."""
    tree = ast.parse(source)
    parent_of = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def function_of(node):
        while node in parent_of:
            node = parent_of[node]
            if isinstance(node, ast.FunctionDef):
                return node.name
        return None

    sites, bypasses = [], []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Name) and node.id == "SAS"):
            continue
        attribute = parent_of.get(node)
        if not isinstance(attribute, ast.Attribute):
            bypasses.append(f"line {node.lineno}: the SAS object is handed on as it is")
            continue
        if attribute.attr in READS:
            continue
        if attribute.attr not in HANDS_OVER:
            bypasses.append(f"line {node.lineno}: SAS.{attribute.attr} is no way to SAS this test knows")
            continue
        call = parent_of.get(attribute)
        if not (isinstance(call, ast.Call) and call.func is attribute):
            bypasses.append(f"line {node.lineno}: SAS.{attribute.attr} is handed on and not called")
            continue
        sites.append((attribute.attr, function_of(call)))
        if any(isinstance(argument, ast.Starred) for argument in call.args) or any(keyword.arg is None for keyword in call.keywords):
            bypasses.append(f"line {node.lineno}: SAS.{attribute.attr} is called with arguments that are not written out")
        for keyword in call.keywords:
            if keyword.arg not in KEYWORDS[attribute.attr]:
                bypasses.append(f"line {node.lineno}: SAS.{attribute.attr} is given {keyword.arg} by its name")
        for position in HANDS_OVER[attribute.attr]:
            argument = call.args[position] if position < len(call.args) else None
            if not (isinstance(argument, ast.Call) and isinstance(argument.func, ast.Name) and argument.func.id == ESCAPING):
                written = "nothing" if argument is None else ast.unparse(argument)
                bypasses.append(f"line {node.lineno}: SAS.{attribute.attr} is given {written} and not {ESCAPING}(...)")
    return sorted(sites, key=str), bypasses


def submitted_code(source):
    """The code a program's Python writes for SAS or CAS to run: the arguments named code."""
    return [keyword.value for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call) for keyword in node.keywords if keyword.arg == "code"]


def escaping_text(source):
    """The escaping function of a program as it is written: its two settings and its definition."""
    tree = ast.parse(source)
    parts = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id in ("MESSAGE_LIMIT", "NOT_FOR_SAS"):
            parts[node.targets[0].id] = ast.get_source_segment(source, node)
        if isinstance(node, ast.FunctionDef) and node.name == ESCAPING:
            parts[ESCAPING] = ast.get_source_segment(source, node)
    return parts


# ---------------------------------------------------------------------------
# The call sites, read from the text of the programs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", list(SOURCES))
def test_every_text_python_hands_to_sas_goes_through_the_escaping_function(name):
    sites, bypasses = call_sites(SOURCES[name]())

    assert bypasses == []
    # One place writes to the SAS log and one sets a macro variable, each inside a function of its own
    assert [what for what, _ in sites] == ["logMessage", "symput"]
    assert all(function is not None for _, function in sites)


@pytest.mark.parametrize("name", list(SOURCES))
def test_code_that_python_writes_for_cas_holds_names_as_names(name):
    """The link program writes the code of two DATA steps and hands it to CAS. A name in it has to reach CAS
    as it is, so it is written as a name in quotes and not made safe as a message is. The parser and the
    pull write no code at all."""
    codes = submitted_code(SOURCES[name]())
    if name != "Link-Logs-To-Models.sas":
        assert codes == []
        return
    inserted = sorted({ast.unparse(value.value) for code in codes for value in ast.walk(code) if isinstance(value, ast.FormattedValue)})
    assert len(codes) == 2
    assert inserted == ["' '.join(LINK_COLUMNS)", "named(LINKS_TABLE)", "named(STAGE_TABLE)", "named(log_table)", "named(new_table)", "quoted(log_caslib)"]


@pytest.mark.parametrize("change, reason", [
    (("SAS.logMessage(sas_safe(message), messageType=message_type)", "SAS.logMessage(message, messageType=message_type)"), "is given message and not sas_safe(...)"),
    (("SAS.logMessage(sas_safe(message), messageType=message_type)", "SAS.logMessage(sas_safe(message)[:900], messageType=message_type)"), "not sas_safe(...)"),
    (("SAS.logMessage(sas_safe(message), messageType=message_type)", "SAS.logMessage(message=sas_safe(message))"), "is given message by its name"),
    (("\nimport ", "\nSAS.symput('_made_up', made_up)\nimport "), "is given '_made_up' and not sas_safe(...)"),
    (("\nimport ", "\nSAS.symput(sas_safe('_made_up'), made_up)\nimport "), "is given made_up and not sas_safe(...)"),
    (("\nimport ", "\nSAS.submit('data _null_; run;')\nimport "), "SAS.submit is no way to SAS this test knows"),
    (("\nimport ", "\nwrite = SAS.logMessage\nimport "), "SAS.logMessage is handed on and not called"),
    (("\nimport ", "\nwrite_log(SAS)\nimport "), "the SAS object is handed on as it is"),
], ids=["a message as it is", "a message that is cut behind the function", "a message by its name", "a name of a macro variable as it is",
        "a value of a macro variable as it is", "another way to SAS", "the way to the log under another name", "the SAS object itself"])
@pytest.mark.parametrize("name", ["Log-Parser-Code.sas", "Get-All-Prompts.sas", "Link-Logs-To-Models.sas"])
def test_a_call_that_bypasses_the_escaping_function_is_found(name, change, reason):
    """The test above holds only as long as it finds what it looks for: each of these changes to a program is one."""
    source = SOURCES[name]()
    old, new = change
    assert old in source
    _, bypasses = call_sites(source.replace(old, new, 1))

    assert bypasses and any(reason in bypass for bypass in bypasses), bypasses


def test_the_three_programs_and_the_two_steps_carry_the_same_escaping_function():
    written = {name: escaping_text(read()) for name, read in SOURCES.items()}

    assert set(written["Link-Logs-To-Models.sas"]) == {"MESSAGE_LIMIT", "NOT_FOR_SAS", ESCAPING}
    for name, parts in written.items():
        assert parts == written["Link-Logs-To-Models.sas"], name


# ---------------------------------------------------------------------------
# Names that would have ended up as code
# ---------------------------------------------------------------------------

HOSTILE = {
    "the two marks of a comment": "Billing /" + "* notes *" + "/ and more",
    "the start of a comment": "open /" + "* comment",
    "a parenthesis without its partner": "closed ) only, and ( open",
    "quotes": "it's a \"name\"",
    "a semicolon and a statement": "x; %put ERROR: this ran as SAS code;",
    "a reference to a macro variable": "R&D of &sysuserid. and &&x",
    "a line break": "line one\nline two\r\nline three",
    "a tab": "tab\there",
    "2000 characters": "n" * 2000,
    "all of it": "a'b\"c; %put ERROR: x; &y /" + "* ) \n\t" + "z" * 2000,
}

LIMIT = 900
BREAKS_SAS = {";": "a semicolon", "%": "a percent sign", "&": "an ampersand", "'": "a single quote", '"': "a double quote",
              "/" + "*": "the start of a comment", "*" + "/": "the end of a comment"}


def breaks_a_statement(text, limit=LIMIT):
    """Why a text does not arrive in SAS as text, as a list of reasons. Empty for a text that does."""
    if not isinstance(text, str):
        return ["no text"]
    reasons = [reason for mark, reason in BREAKS_SAS.items() if mark in text]
    if any(not character.isprintable() for character in text):
        reasons.append("a line break, a tab or another character that is no text")
    depth = 0
    for character in text:
        depth += {"(": 1, ")": -1}.get(character, 0)
        if depth < 0:
            break
    if depth != 0:
        reasons.append("parentheses that do not pair up")
    if len(text) > limit:
        reasons.append(f"{len(text)} characters")
    return reasons


def unsafe_messages(messages):
    return [(message[:80], breaks_a_statement(message)) for _, message in messages if breaks_a_statement(message)]


@pytest.mark.parametrize("hostile", list(HOSTILE.values()), ids=list(HOSTILE))
def test_names_are_hostile(hostile):
    """Each of the names below is one SAS would not have taken as text."""
    assert breaks_a_statement(hostile) != []


@pytest.mark.parametrize("name", ["Log-Parser-Code.sas", "Get-All-Prompts.sas", "Link-Logs-To-Models.sas"])
@pytest.mark.parametrize("hostile", list(HOSTILE.values()), ids=list(HOSTILE))
def test_the_escaping_function_of_every_program_makes_a_hostile_name_safe(name, hostile):
    namespace = {}
    parts = escaping_text(SOURCES[name]())
    exec("\n".join(parts[part] for part in ("MESSAGE_LIMIT", "NOT_FOR_SAS", ESCAPING)), namespace)

    assert breaks_a_statement(namespace[ESCAPING](hostile)) == []
    assert breaks_a_statement(namespace[ESCAPING](f"The model {hostile} ({hostile}) could not be read: {hostile}")) == []


class ViyaThatFails(pull.FakeViya):
    """A SAS Model Manager that answers one listing with an error of its own wording."""

    fails_at = None
    reason = ""

    def get(self, url, headers=None, verify=None, timeout=None):
        if self.fails_at and url.split("?")[0].endswith(self.fails_at):
            raise RuntimeError(self.reason)
        return super().get(url, headers=headers, verify=verify, timeout=timeout)


@pytest.mark.parametrize("hostile", list(HOSTILE.values()), ids=list(HOSTILE))
def test_pull_hands_hostile_names_of_model_manager_to_sas_as_text(monkeypatch, tmp_path, hostile):
    """The names the pull writes into its messages come from SAS Model Manager: a project, a model, a RAG
    setup, a file, the reason of an error, and the project a setting names. Before, a name with a comment
    mark, a tab or a single parenthesis reached SAS.logMessage as it was."""
    viya = ViyaThatFails()
    prompts = viya.add_project(hostile, tags=["Prompt-Engineering"])
    viya.add_model(prompts, hostile, files={"Prompt-Experiment-Tracker.json": "{no json " + hostile})
    unlisted = viya.add_model(prompts, hostile + " 2")
    viya.fails_at, viya.reason = f"/modelRepository/models/{unlisted['id']}/contents", hostile
    closed = viya.add_project(hostile + " 2", tags=["Prompt-Engineering"])
    viya.failing.add(f"/modelRepository/projects/{closed['id']}/models")
    setups = viya.add_project(hostile, tags=["RAG-Engineering"])
    viya.add_model(setups, hostile)
    viya.add_model(setups, "RAG Retrieval - " + hostile, algorithm="RAG")
    viya.add_model(setups, hostile + " 3", files={"rag-setup.json": "{no json"})
    run = pull.run_pull(monkeypatch, tmp_path, viya=viya,
                        settings={"_gap_llm_project": hostile + " nowhere", "_gap_embedding_project": "", "_gap_decision_project": ""})

    assert run.rc == "0"
    assert unsafe_messages(run.sas.messages) == []
    # Every kind of message that carries a name was written - a long name leaves no room for what follows it
    warnings, notes = run.sas.of_type("WARNING"), run.sas.of_type("NOTE")
    for start in ("The files of the model ", "The file Prompt-Experiment-Tracker.json of the model ", "The models of the project ",
                  "The file rag-setup.json of the model ", "The RAG setup ", ("No project ", "The project ")):
        assert [warning for warning in warnings if warning.startswith(start)], start
    assert [note for note in notes if note.startswith("The model ") and ("holds no RAG setup" in note or note.endswith("..."))]
    assert run.sas.of_type("ERROR") == []
    # What is data stays what it was: the name in the table is the name of the model, as far as its column goes
    assert {row["name"] for row in run.rows("experiments")} == {hostile[:256], (hostile + " 2")[:256]}
    # Every macro variable: a name of the program, and a value SAS takes as text - a number, or names and lengths
    for variable, value in run.sas.macro_variables.items():
        if variable in pull.DEFAULT_SETTINGS or variable in ("_gap_viyaHost", "_gap_work", "_gap_llm_project", "_gap_embedding_project", "_gap_decision_project"):
            continue
        assert breaks_a_statement(variable) == [] and variable.startswith("_gap_")
        assert breaks_a_statement(value, limit=65534) == [], variable


@pytest.mark.parametrize("hostile", list(HOSTILE.values()), ids=list(HOSTILE))
def test_pull_refuses_a_hostile_table_name_in_a_message_that_is_text(monkeypatch, tmp_path, hostile):
    viya = pull.FakeViya()
    run = pull.run_pull(monkeypatch, tmp_path, viya=viya, settings={"_gap_out_catalog": hostile})

    assert run.rc == "-1" and viya.requests == []
    errors = run.sas.of_type("ERROR")
    assert len(errors) == 1 and errors[0].startswith("The table name ") and len(run.sas.messages) == 1
    assert unsafe_messages(run.sas.messages) == []


@pytest.mark.parametrize("hostile", list(HOSTILE.values()), ids=list(HOSTILE))
def test_link_hands_hostile_names_of_the_catalog_and_the_settings_to_sas_as_text(monkeypatch, hostile):
    """The names the link program writes into its messages come from the catalog and from its settings."""
    deep = "[" * 100000 + "]" * 100000
    catalog = link.catalog_table(
        link.answer_model(hostile, "llm", link.uuid(1)), link.answer_model(hostile, "llm", link.uuid(2)),
        link.prompt_template(link.uuid(21), hostile, deep, "Ticket: {{ticket}}", role="decision"))
    log = link.log_table(link.llm_row(model=" ".join(hostile.split())), link.llm_row(model="another_model"))
    run = link.run_program(monkeypatch, {("Public", "LLM_LOGS"): log, ("Public", "MODEL_CATALOG"): catalog}, _lnk_min_literal=hostile,
                           _lnk_runs_exists="0")

    assert unsafe_messages(run.sas.messages) == []
    notes, warnings = run.sas.lines("NOTE"), run.sas.lines("WARNING")
    assert [note for note in notes if note.startswith("The catalog holds 2 models named ")]
    assert [warning for warning in warnings if warning.startswith("The setting _lnk_min_literal is ")]
    assert [warning for warning in warnings if warning.startswith("The catalog row of ")]
    assert run.sas.lines("ERROR") == []
    # The row of the model with two owners is in the new table without a model id, and the name as it was
    assert run.new_table["model"].tolist()[0] == " ".join(hostile.split())
    assert {name: value for name, value in run.sas.macro_variables.items() if name in ("_lnk_row_count", "_lnk_unchanged", "_lnk_refused")} == {
        "_lnk_row_count": "2", "_lnk_unchanged": "0", "_lnk_refused": "0"}


@pytest.mark.parametrize("hostile", [text for text in HOSTILE.values() if "\n" in text or "\t" in text],
                         ids=[label for label, text in HOSTILE.items() if "\n" in text or "\t" in text])
def test_link_refuses_a_table_name_that_is_no_text_in_a_message_that_is(monkeypatch, hostile):
    catalog = link.catalog_table(link.answer_model("demo_llm"))
    run = link.run_program(monkeypatch, {("Public", hostile): link.log_table(link.llm_row()), ("Public", "MODEL_CATALOG"): catalog}, _lnk_log_table=hostile,
                           _lnk_runs_exists="0")

    errors = run.sas.lines("ERROR")
    assert len(errors) == 1 and errors[0].startswith("The settings name a table or a CAS library with a line break")
    assert unsafe_messages(run.sas.messages) == []
    assert run.sas.macro_variables["_lnk_refused"] == "1" and run.new_table is None


@pytest.mark.parametrize("hostile", list(HOSTILE.values()), ids=list(HOSTILE))
def test_parser_writes_every_message_as_text_whatever_the_log_holds(monkeypatch, tmp_path, hostile):
    """The messages of the parser hold numbers and no name. They take the same way as the messages of the
    other two, and here every one of them is written for a log whose models and prompts carry a hostile name."""
    model = " ".join(hostile.split())[:200]
    first = parser.llm_block(timestamp="2026-01-05 09:00:00", finished="2026-01-05 09:00:03", model=model, user_prompt=hostile)
    second = parser.llm_block(timestamp="2026-01-05 09:00:01", finished="2026-01-05 09:00:04", model=model, system_prompt=hostile)
    (before_first, after_first), (before_second, after_second) = parser.split_at_scoring(first), parser.split_at_scoring(second)
    cut_off = parser.llm_block(timestamp="2026-01-05 09:10:00", model=model, user_prompt=hostile)
    position = next(index for index, line in enumerate(cut_off) if " - Request Data: " in line)
    cut_off[position] = cut_off[position][:cut_off[position].index("Request Data: ") + 40]
    half_a_pair = parser.llm_block(timestamp="2026-01-05 09:20:00", model=model, user_prompt=hostile + " \ud83d")
    running = parser.split_at_scoring(parser.llm_block(timestamp="2026-01-05 09:30:00", model=model, user_prompt=hostile))[0]
    log_path = tmp_path / "llms.log"
    log_path.write_text(parser.as_log(before_first, before_second, after_first, after_second, cut_off, half_a_pair, running), encoding="utf-8", newline="")
    run = parser.run_parser(monkeypatch, log_path)

    assert run.row_count == "5" and run.sas.macro_variables["_log_row_count"].isdigit()
    assert [kind for kind, _ in run.sas.messages] == ["NOTE"] * 4
    said = " | ".join(message for _, message in run.sas.messages)
    for part in ("were running at the same time as another request", "were running where the lines of their container end",
                 "hold a value that could not be read", "hold a character that has no UTF-8 bytes"):
        assert part in said, part
    assert unsafe_messages(run.sas.messages) == []
    # What is data stays what it was
    assert run.uploads[0]["frame"]["model"].tolist() == [model] * 5

    # The WARNING for a log whose lines carry something in front
    prefixed = tmp_path / "prefixed.log"
    prefixed.write_text(parser.as_log(["[pod/" + model + "] " + line for line in first]), encoding="utf-8", newline="")
    run = parser.run_parser(monkeypatch, prefixed)
    assert [kind for kind, _ in run.sas.messages] == ["WARNING"] and run.row_count == "0"
    assert unsafe_messages(run.sas.messages) == []


def test_parser_message_for_missing_packages_is_text(monkeypatch, tmp_path):
    """The one message the parser writes before its functions exist takes the same way."""
    source = SOURCES["Log-Parser-Code.sas"]()
    tree = ast.parse(source)
    handler = next(node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler) and ast.unparse(node.type) == "ImportError")
    assert ast.unparse(handler.body[0]) == "say('Ensure that the SWAT and Pandas package are installed', 'ERROR')"
    defined = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
    first_statement_that_can_fail = next(index for index, node in enumerate(tree.body) if isinstance(node, ast.Try))
    assert {"sas_safe", "say"} <= {node.name for node in tree.body[:first_statement_that_can_fail] if isinstance(node, ast.FunctionDef)}, defined[:5]
