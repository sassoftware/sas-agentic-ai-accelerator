"""Follows a macro that holds a PROC CAS step: the macro logic with macro_harness, the CASL of the
step against a CAS library that is made up.

SAS and CAS can not run here. What can be followed is which statements a macro generates for a
state of the session, what its CASL then does with the tables and the saved files of a CAS
library, and which lines it writes to the log. The CASL is turned into Python statement by
statement and run. This module knows the part of CASL the programs it is used for are written in

    name = expression;                     || == != and or, "text", numbers, true / false, a.b
    if ... then do; ... end;  else do; ... end;  if ... then <statement>;  else if ... then <statement>;
    do <name> over <result>.<table>; ... end;
    <action> result = <name> status = <name> / <parameters>;      and the UPLOAD statement
    call symputx("name", value, "L");
    the functions exists, upcase and index

and nothing else: a statement it does not know stops it with an AssertionError, so that a step
is never followed half.

WHAT THE CAS LIBRARY IS HERE (FakeCAS): tables in memory, of the session or global, and saved
files. A table of the session hides a global table of its name. tableExists answers 0, 1 (session)
or 2 (global) and sees no file. An action that is refused ends with severity 2 and writes an ERROR
line, as an action of PROC CAS does; a test names the actions it wants refused. Loading a file
that is not there is such an error; listing the files of a library that has none is not.
This is a model: it says what the CASL does IF CAS behaves this way.
"""
import re
from types import SimpleNamespace

import macro_harness


class Macros(macro_harness.Macros):
    """macro_harness.Macros with %upcase, which the release table macro uses."""

    def call(self, name, inner):
        if name == "upcase":
            return self.resolve(inner).upper()
        return super().call(name, inner)


# ---------------------------------------------------------------------------
# A macro in the forms macro_harness reads
# ---------------------------------------------------------------------------

# What stands in front of the call of another macro in the text a followed macro generates
CALLED = "MACRO CALL: "

def in_blocks(program, name, parameters=None):
    """The program with the macro `name` written in the forms macro_harness follows: a statement
    behind %then or %else gets a %do group of its own, the call of another macro becomes a line of
    text (CALLED + '<macro>(<arguments>);') a test answers, and a macro with parameters loses them -
    the test sets them as macro variables."""
    start = program.index(f"%macro {name}")
    stop = program.index(f"%mend {name};")
    head_end = program.index(";", start)
    body = re.sub(r"/\*.*?\*/", "", program[head_end + 1:stop], flags=re.DOTALL)
    lines, pending = [], None
    for line in body.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if pending is not None:
            # the statement of a %then or %else that stood at the end of the line in front
            lines += [pending + " %do;", stripped, "%end;"]
            pending = None
            continue
        one_line = re.fullmatch(r"(%if .* %then|%else) (%return;|%put .*;|%str\((.*)\);)", stripped)
        if one_line:
            statement = one_line.group(3) if one_line.group(3) is not None else one_line.group(2)
            lines += [one_line.group(1) + " %do;", statement, "%end;"]
        elif re.fullmatch(r"(%if .* %then|%else %if .* %then|%else)", stripped):
            pending = stripped
        elif re.fullmatch(r"%_\w+(\(.*\))?;", stripped):
            lines.append(CALLED + stripped[1:])
        else:
            lines.append(stripped)
    assert pending is None
    return f"%macro {name};\n" + "\n".join(lines) + f"\n%mend {name};\n"


def follow(program, name, symbols, cas=None, functions=None, called=None, data_step=None, on_line=None):
    """Follows the macro `name` of a program: (the lines of SAS code it generates, the lines it writes with %put,
    the macro variables as the macro leaves them).

    symbols     the macro variables the macro finds, its parameters among them
    cas         a FakeCAS: each PROC CAS step the macro generates is run against it when its QUIT is
                reached, and the macro variables the step sets are there for the lines behind it
    functions   what %sysfunc may call, as {name: function of the list of arguments}
    called      called(text, macros) for the call of another macro, the text being '<macro>(<arguments>);'
    data_step   data_step(lines, macros) for a DATA step, at its RUN statement: what it leaves in macro variables
    on_line     on_line(line, macros) for every line of SAS code the macro generates, in front of everything else:
                how a test stands in for a step of another procedure"""
    macros = Macros(symbols, functions)
    step = {"cas": None, "data": None}

    def on_text(line, macros):
        if on_line is not None:
            on_line(line, macros)
        if line.startswith(CALLED):
            assert called is not None, line
            called(line[len(CALLED):], macros)
        elif line == "proc cas;":
            step["cas"] = [line]
        elif step["cas"] is not None:
            step["cas"].append(line)
            if line in ("quit;", "run; quit;"):
                assert cas is not None, "a PROC CAS step and no CAS library to run it against"
                for variable, value in cas.run("\n".join(step["cas"])).items():
                    macros.symbols[variable.lower()] = value
                step["cas"] = None
        elif re.match(r"data\s", line):
            step["data"] = [line]
        elif step["data"] is not None:
            step["data"].append(line)
            if line == "run;":
                if data_step is not None:
                    data_step(step["data"], macros)
                step["data"] = None

    generated, log = macros.run(in_blocks(program, name), name, on_text)
    assert step["cas"] is None, "a PROC CAS step without its QUIT"
    return generated, log, macros


# ---------------------------------------------------------------------------
# CASL as Python
# ---------------------------------------------------------------------------

def _statements(text):
    """The statements of a CASL text: split at every semicolon that stands in no text and in no braces."""
    found, depth, quote, start = [], 0, "", 0
    for position, character in enumerate(text):
        if quote:
            quote = "" if character == quote else quote
        elif character in "\"'":
            quote = character
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
        elif character == ";" and depth == 0:
            found.append(" ".join(text[start:position].split()))
            start = position + 1
    assert not text[start:].strip(), "a statement without its semicolon: " + text[start:]
    return [statement for statement in found if statement]


def _expression(text):
    """A CASL expression as a Python expression. Texts between quotation marks are kept as they are."""
    parts = re.split(r'("[^"]*"|\'[^\']*\')', text)
    for position in range(0, len(parts), 2):
        part = parts[position].replace("||", "+")
        part = re.sub(r"\btrue\b", "True", part)
        part = re.sub(r"\bfalse\b", "False", part)
        assert not re.search(r"[{};]|\b(then|do|over)\b", part), "an expression this harness does not know: " + text
        parts[position] = part
    return "".join(parts).strip()


def _split(text, separator):
    """A text split at a separator that stands in no text and in no braces."""
    found, depth, quote, start = [], 0, "", 0
    for position, character in enumerate(text):
        if quote:
            quote = "" if character == quote else quote
        elif character in "\"'":
            quote = character
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
        elif character == separator and depth == 0:
            found.append(text[start:position])
            start = position + 1
    return found + [text[start:]]


def _value(text):
    """One value of a parameter: a text, a number, a name, a group of parameters or a list, in braces."""
    text = text.strip()
    if text.startswith("{"):
        assert text.endswith("}"), text
        entries = [entry.strip() for entry in _split(text[1:-1], ",") if entry.strip()]
        if entries and all(re.match(r"\w+\s*=[^=]", entry) for entry in entries):
            return "{" + ", ".join(f'"{entry.split("=", 1)[0].strip()}": {_value(entry.split("=", 1)[1])}' for entry in entries) + "}"
        return "[" + ", ".join(_value(entry) for entry in entries) + "]"
    return _expression(text)


def _parameters(text):
    """The parameters of an action - name = value, one after the other - as the source of a Python dict."""
    found, position = [], 0
    text = text.strip()
    while position < len(text):
        name = re.compile(r"\s*(\w+)\s*=\s*").match(text, position)
        assert name, "parameters this harness does not know: " + text[position:]
        position = name.end()
        if text[position] == "{":
            depth, end = 0, position
            while True:
                depth += (text[end] == "{") - (text[end] == "}")
                end += 1
                if depth == 0:
                    break
        elif text[position] == '"':
            end = text.index('"', position + 1) + 1
        else:
            end = re.compile(r"[\w.]+").match(text, position).end()
        found.append(f'"{name.group(1)}": {_value(text[position:end])}')
        position = end
    return "{" + ", ".join(found) + "}"


ACTION = re.compile(r"(table\.\w+)\s*(.*?)\s*/\s*(.*)", flags=re.DOTALL)


def _simple(statement):
    """One statement that opens no group, as lines of Python."""
    action = ACTION.fullmatch(statement)
    if action:
        names = dict(re.findall(r"(result|status)\s*=\s*(\w+)", action.group(2)))
        assert not re.sub(r"(result|status)\s*=\s*\w+", "", action.group(2)).strip(), statement
        lines = [f'_result, _status = _cas("{action.group(1)}", {_parameters(action.group(3))})']
        lines += [f"{names[kind]} = _{kind}" for kind in ("result", "status") if kind in names]
        return lines
    if statement.startswith("upload "):
        return [f'_result, _status = _cas("upload", {_parameters(statement[len("upload "):])})']
    if statement.startswith("call symputx("):
        return ["_symputx(" + _expression(statement[len("call symputx("):-1]) + ")"]
    assignment = re.fullmatch(r"(\w+)\s*=\s*(.*)", statement, flags=re.DOTALL)
    assert assignment and not statement.startswith(("if ", "else", "do ", "end")), "a statement this harness does not know: " + statement
    return [f"{assignment.group(1)} = {_expression(assignment.group(2))}"]


def as_python(casl):
    """The CASL between 'proc cas;' and its 'quit;' as Python source."""
    lines, depth = [], 0

    def emit(text):
        lines.append("    " * depth + text)

    for statement in _statements(casl):
        if statement in ("proc cas", "quit", "run"):
            continue
        if statement == "end":
            depth -= 1
            continue
        opened = re.fullmatch(r"(else )?if (.*) then do", statement, flags=re.DOTALL)
        single = re.fullmatch(r"(else )?if (.*?) then (.*)", statement, flags=re.DOTALL)
        loop = re.fullmatch(r"do (\w+) over ([\w.]+)", statement)
        if statement == "else do":
            emit("else:")
            depth += 1
            emit("pass")
        elif opened:
            emit(("elif " if opened.group(1) else "if ") + _expression(opened.group(2)) + ":")
            depth += 1
            emit("pass")
        elif single:
            emit(("elif " if single.group(1) else "if ") + _expression(single.group(2)) + ":")
            for line in _simple(single.group(3)):
                lines.append("    " * (depth + 1) + line)
        elif loop:
            emit(f"for {loop.group(1)} in _rows({loop.group(2)}):")
            depth += 1
            emit("pass")
        else:
            for line in _simple(statement):
                emit(line)
    assert depth == 0, "a group that is not closed"
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# The CAS library
# ---------------------------------------------------------------------------

class FakeCAS:
    """Tables in memory and saved files of CAS libraries. A table is {'columns': [...], 'rows': n, 'of': what it
    holds}; `refuse` holds the names of actions that end with an error, as 'table.promote' or, for one table only,
    ('table.dropTable', 'LLM_FACT_SHEET')."""

    def __init__(self, refuse=(), upload_rows=None, lists_no_table_when_empty=True):
        self.session, self.promoted, self.files = {}, {}, {}
        self.refuse = set(refuse)
        self.upload_rows = upload_rows
        self.lists_no_table_when_empty = lists_no_table_when_empty
        self.calls, self.errors, self.symbols = [], [], {}

    # -- what a test builds -------------------------------------------------
    @staticmethod
    def key(caslib, name):
        return (str(caslib).lower(), str(name).upper())

    def table(self, caslib, name, columns, rows=1, of="", scope="global"):
        (self.promoted if scope == "global" else self.session)[self.key(caslib, name)] = {"columns": list(columns), "rows": rows, "of": of}

    def file(self, caslib, name, columns, rows=1, of="", loadable=True):
        self.files[(str(caslib).lower(), name)] = {"columns": list(columns), "rows": rows, "of": of, "loadable": loadable}

    def exists(self, caslib, name):
        key = self.key(caslib, name)
        return 1 if key in self.session else 2 if key in self.promoted else 0

    # -- the actions ----------------------------------------------------------
    def _refused(self, action, name=""):
        return action in self.refuse or (action, str(name).upper()) in self.refuse

    def _end(self, action, result=None, error=""):
        if error:
            self.errors.append(f"ERROR: {action}: {error}")
        return SimpleNamespace(**(result or {})), SimpleNamespace(severity=2 if error else 0)

    def __call__(self, action, parameters):
        self.calls.append((action, parameters))
        caslib = parameters.get("caslib", "")
        name = parameters.get("name", "")
        if action == "upload":
            out = parameters["casOut"]
            if self._refused(action, out["name"]):
                return self._end(action, error="the file could not be uploaded")
            columns = [entry["name"] for entry in parameters["importOptions"]["vars"]]
            self.session[self.key(out["caslib"], out["name"])] = {"columns": columns, "rows": self.upload_rows, "of": "upload"}
            return self._end(action)
        if action == "table.tableExists":
            return self._end(action, {"exists": self.exists(caslib, name)})
        if action == "table.tableInfo":
            listed = dict(self.promoted)
            listed.update(self.session)
            rows = [SimpleNamespace(Name=key[1], Rows=table["rows"]) for key, table in listed.items() if key[0] == str(caslib).lower()]
            return self._end(action, {"TableInfo": rows} if rows else {})
        if action == "table.columnInfo":
            key = self.key(parameters["table"]["caslib"], parameters["table"]["name"])
            table = self.session.get(key) or self.promoted.get(key)
            if table is None:
                return self._end(action, error=f"the table {key[1]} was not found")
            return self._end(action, {"ColumnInfo": [SimpleNamespace(Column=column) for column in table["columns"]]})
        if action == "table.fileInfo":
            if self._refused(action):
                return self._end(action, error="the files of the CAS library could not be listed")
            rows = [SimpleNamespace(Name=key[1]) for key in self.files if key[0] == str(caslib).lower()]
            return self._end(action, {} if not rows and self.lists_no_table_when_empty else {"FileInfo": rows})
        if action == "table.loadTable":
            out = parameters["casOut"]
            source = self.files.get((str(caslib).lower(), parameters["path"]))
            target = self.key(out["caslib"], out["name"])
            if source is None or not source["loadable"] or self._refused(action, out["name"]):
                return self._end(action, error=f"the file {parameters['path']} could not be loaded")
            if out.get("promote"):
                if target in self.promoted:
                    return self._end(action, error=f"a global table {target[1]} exists")
                self.promoted[target] = {key: source[key] for key in ("columns", "rows", "of")}
            else:
                self.session[target] = {key: source[key] for key in ("columns", "rows", "of")}
            return self._end(action)
        if action == "table.dropTable":
            key = self.key(caslib, name)
            if self._refused(action, name):
                return self._end(action, error=f"the table {key[1]} could not be dropped")
            if key in self.session:
                del self.session[key]
            elif key in self.promoted:
                del self.promoted[key]
            elif not parameters.get("quiet"):
                return self._end(action, error=f"the table {key[1]} was not found")
            return self._end(action)
        if action == "table.promote":
            source, target = self.key(caslib, name), self.key(parameters["targetLib"], parameters["target"])
            if self._refused(action, parameters["target"]) or source not in self.session or target in self.promoted:
                return self._end(action, error=f"the table {source[1]} could not be promoted as {target[1]}")
            self.promoted[target] = self.session.pop(source)
            return self._end(action)
        if action == "table.save":
            key = self.key(parameters["table"]["caslib"], parameters["table"]["name"])
            table = self.session.get(key) or self.promoted.get(key)
            file_key = (str(caslib).lower(), name + ".sashdat")
            if self._refused(action, name) or table is None or (file_key in self.files and not parameters.get("replace")):
                return self._end(action, error=f"the table {key[1]} could not be saved")
            self.files[file_key] = dict(table, loadable=True)
            return self._end(action)
        raise AssertionError("an action this harness does not know: " + action)

    # -- running a step -------------------------------------------------------
    def run(self, casl):
        """Runs the CASL of one PROC CAS step. Returns the macro variables it set with CALL SYMPUTX."""
        def index(text, part):
            return str(text).find(str(part)) + 1

        def symputx(name, value, scope=""):
            self.symbols[name] = str(value)

        def rows(table):
            return list(table)

        namespace = {"_cas": self, "_symputx": symputx, "_rows": rows, "exists": lambda result, name: hasattr(result, name),
                     "upcase": lambda text: str(text).upper(), "index": index}
        exec(compile(as_python(casl), "CASL", "exec"), namespace)
        return dict(self.symbols)
