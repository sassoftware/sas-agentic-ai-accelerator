"""Follows one run of the log parser from its setting _log_append_table to the output table: the macro
statements with macro_harness, the Python with log_parser_harness, the CASL and the DATA steps of the
macro that handles the output table against a CAS library that is made up.

SAS and CAS can not run here. What can be followed is what the program does IF they behave the way
this module says, and each of its rules is one that SAS documents or that was measured on a SAS Viya
system with the program (2026-10-13, the logs of that pass):

  CAS      tables in memory, of the session or global, and saved files. A table of the session hides
           a global one of its name; tableExists answers 0, 1 or 2 and sees no file. An action that
           fails writes an ERROR line and leaves the condition code 1012 (measured: the loadTable of
           a file that was not there). A table without rows is listed with 0 rows (measured).
  DATA     the statements of the four steps the program runs on CAS tables are turned into Python
           and run: SET, MERGE with BY (the match-merge: within a BY group the rows of each table are
           read one after the other, a table that has no further row keeps the values of its last
           one, and its IN= variable stays 1), FIRST. and LAST., RETAIN, the subsetting IF, IF with
           THEN and ELSE, OUTPUT with and without a table, DROP, LENGTH, and the data set options
           KEEP=, DROP=, RENAME= and IN=. A step that names a table of more than 32 characters
           through a libref does not run (measured: ERROR 307-185), neither does one whose column is
           a number in one table and text in another, or CHAR in one and VARCHAR in the other. Behind
           a step that did not run no further DATA step runs: a job is in syntax check mode
           (measured), the table a step would have replaced stays, and SYSERR is 1012.
           The DATA _NULL_ step that reads the columns of the existing table is not turned into
           Python: what it leaves in macro variables is put together here from its statements.
  CASL     the statements of the PROC CAS steps are turned into Python and run, as casl_harness does
           for the programs that write CASL with == and without commas.

A statement this module does not know stops it with an AssertionError, so a step is never followed half.
"""
import copy
import re
from types import SimpleNamespace

import casl_harness
import log_parser_harness
import macro_harness

FAILED = 1012
NUMBER, VARCHAR = "double", "varchar"


# ---------------------------------------------------------------------------
# The macro language: what the parser uses beyond macro_harness
# ---------------------------------------------------------------------------

class Macros(macro_harness.Macros):
    """macro_harness.Macros with %nrstr, which keeps a text from being run, and with OR in a condition:
    a comparison is evaluated first, then AND, then OR, as SAS does without parentheses."""

    def call(self, name, inner):
        if name == "nrstr":
            return macro_harness.masked(inner)
        return super().call(name, inner)

    def true(self, condition):
        resolved = self.resolve(condition)
        assert not re.search(r"\snot\s|[()]", resolved, flags=re.IGNORECASE), "a condition this harness does not know: " + condition
        return any(all(macro_harness._compare(part) for part in re.split(r"\s+and\s+", alternative, flags=re.IGNORECASE))
                   for alternative in re.split(r"\s+or\s+", resolved, flags=re.IGNORECASE))


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _statements(text):
    """The statements of a text: split at every semicolon that stands in no quoted text and in no braces."""
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


def _outside_quotes(text, change):
    """A text with `change` applied to every part that stands between no quotation marks."""
    parts = re.split(r'("[^"]*"|\'[^\']*\')', text)
    return "".join(change(part) if position % 2 == 0 else part for position, part in enumerate(parts))


def _split(text, separator):
    found, depth, quote, start = [], 0, "", 0
    for position, character in enumerate(text):
        if quote:
            quote = "" if character == quote else quote
        elif character in "\"'":
            quote = character
        elif character in "{(":
            depth += 1
        elif character in "})":
            depth -= 1
        elif character == separator and depth == 0:
            found.append(text[start:position])
            start = position + 1
    return found + [text[start:]]


COMPARISONS = (("EQ", "=="), ("NE", "!="), ("GE", ">="), ("LE", "<="), ("GT", ">"), ("LT", "<"))


# ---------------------------------------------------------------------------
# CASL as Python
# ---------------------------------------------------------------------------

def _casl_expression(text):
    def change(part):
        part = re.sub(r"\(string\)\s*(?=\()", "_string", part)
        part = re.sub(r"\(string\)\s*([\w.]+)", r"_string(\1)", part)
        part = part.replace("||", "+").replace("[1]", "[0]")
        for word, sign in COMPARISONS:
            part = re.sub(rf"\b{word}\b", sign, part)
        assert not re.search(r"[{};]|\b(then|do|over)\b", part), "an expression this harness does not know: " + text
        return part
    return _outside_quotes(text, change).strip()


def _casl_value(text):
    text = text.strip()
    if text.startswith("{"):
        assert text.endswith("}"), text
        return _casl_parameters(text[1:-1])
    return _casl_expression(text)


def _casl_parameters(text):
    """The parameters of an action - name = value, separated by commas - as the source of a Python dict.
    The names are taken without their case, as CAS takes them."""
    found = []
    for entry in _split(text, ","):
        if not entry.strip():
            continue
        name, value = entry.split("=", 1)
        assert re.fullmatch(r"\s*\w+\s*", name), "parameters this harness does not know: " + entry
        found.append(f'"{name.strip().lower()}": {_casl_value(value)}')
    return "{" + ", ".join(found) + "}"


ACTION = re.compile(r"(table\.\w+)\s*((?:(?:result|status)\s*=\s*\w+\s*)*)/\s*(.*)", flags=re.DOTALL)


def _casl_simple(statement):
    action = ACTION.fullmatch(statement)
    if action:
        names = dict(re.findall(r"(result|status)\s*=\s*(\w+)", action.group(2)))
        lines = [f'_result, _status = _cas("{action.group(1)}", {_casl_parameters(action.group(3))})']
        return lines + [f"{names[kind]} = _{kind}" for kind in ("result", "status") if kind in names]
    printed = re.fullmatch(r"print \((error|note)\) (.*)", statement, flags=re.DOTALL)
    if printed:
        return [f'_print("{printed.group(1)}", {_casl_expression(printed.group(2))})']
    saved = re.fullmatch(r"saveresult (\w+) dataout=(\w+)\.(\w+)", statement)
    if saved:
        return [f'_saveresult({saved.group(1)}, "{saved.group(2)}", "{saved.group(3)}")']
    if statement.startswith("call symputx("):
        return ["_symputx(" + _casl_expression(statement[len("call symputx("):-1]) + ")"]
    assignment = re.fullmatch(r"(\w+)\s*=\s*(.*)", statement, flags=re.DOTALL)
    assert assignment and not statement.startswith(("if ", "else", "do ", "end")), "a CASL statement this harness does not know: " + statement
    return [f"{assignment.group(1)} = {_casl_expression(assignment.group(2))}"]


def casl_as_python(casl):
    """The CASL between 'proc cas;' and its 'quit;' as Python source."""
    lines, depth = [], 0
    for statement in _statements(casl):
        if statement in ("proc cas", "quit", "run"):
            continue
        if statement == "end":
            depth -= 1
            continue
        opened = re.fullmatch(r"(else )?if (.*) then do", statement, flags=re.DOTALL)
        single = re.fullmatch(r"(else )?if (.*?) then (.*)", statement, flags=re.DOTALL)
        loop = re.fullmatch(r"do (\w+) over ([\w.]+)", statement)
        otherwise = re.fullmatch(r"else (?!if |do$)(.*)", statement, flags=re.DOTALL)
        if statement == "else do":
            lines += ["    " * depth + "else:", "    " * (depth + 1) + "pass"]
            depth += 1
        elif otherwise:
            # ELSE with one statement behind it
            lines.append("    " * depth + "else:")
            lines += ["    " * (depth + 1) + line for line in _casl_simple(otherwise.group(1))]
        elif opened:
            lines += ["    " * depth + ("elif " if opened.group(1) else "if ") + _casl_expression(opened.group(2)) + ":", "    " * (depth + 1) + "pass"]
            depth += 1
        elif single:
            lines.append("    " * depth + ("elif " if single.group(1) else "if ") + _casl_expression(single.group(2)) + ":")
            lines += ["    " * (depth + 1) + line for line in _casl_simple(single.group(3))]
        elif loop:
            lines += ["    " * depth + f"for {loop.group(1)} in {loop.group(2)}:", "    " * (depth + 1) + "pass"]
            depth += 1
        else:
            lines += ["    " * depth + line for line in _casl_simple(statement)]
    assert depth == 0, "a group that is not closed"
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# The CAS library
# ---------------------------------------------------------------------------

def table_of(columns, rows, label=""):
    """A table: its columns as (name, type) pairs - double, varchar or char(n) - and its rows as dicts.
    A text that is missing is the empty text, a number that is missing is None."""
    names = [name for name, _ in columns]
    kinds = dict(columns)
    cleaned = []
    for row in rows:
        cleaned.append({name: (row.get(name) if kinds[name] == NUMBER else (row.get(name) or "")) for name in names})
    return {"columns": list(columns), "rows": cleaned, "label": label}


class Library:
    """Tables in memory and saved files of CAS libraries, the tables of the WORK library of the SAS session, and
    the lines the actions wrote. `refuse` holds the names of actions that end with an error, `unloadable` the
    names of saved files that can not be loaded, `fail_steps` the names of tables whose DATA step does not run."""

    def __init__(self, refuse=(), unloadable=(), fail_steps=()):
        self.session, self.promoted, self.files, self.work = {}, {}, {}, {}
        self.refuse, self.unloadable, self.fail_steps = set(refuse), set(unloadable), set(fail_steps)
        self.calls, self.lines, self.symbols = [], [], {}
        self.syscc = 0

    # -- what a test builds and looks at ------------------------------------
    @staticmethod
    def key(caslib, name):
        return (str(caslib).lower(), str(name).upper())

    def put(self, caslib, name, table, scope="global"):
        (self.promoted if scope == "global" else self.session)[self.key(caslib, name)] = copy.deepcopy(table)

    def put_file(self, caslib, name, table):
        self.files[(str(caslib).lower(), name)] = copy.deepcopy(table)

    def exists(self, caslib, name):
        key = self.key(caslib, name)
        return 1 if key in self.session else 2 if key in self.promoted else 0

    def get(self, caslib, name):
        key = self.key(caslib, name)
        return self.session.get(key) or self.promoted.get(key)

    def file_of(self, caslib, name):
        return self.files.get((str(caslib).lower(), name))

    def state(self, caslib, name):
        """What a look from outside finds of a table: where it is, its rows, and the rows of its saved file."""
        table, saved = self.get(caslib, name), self.file_of(caslib, name + ".sashdat")
        return {"exists": self.exists(caslib, name), "rows": copy.deepcopy(table["rows"]) if table else None,
                "columns": list(table["columns"]) if table else None, "label": table["label"] if table else None,
                "file": copy.deepcopy(saved["rows"]) if saved else None}

    def tables_named(self, start):
        return sorted(key[1] for key in list(self.session) + list(self.promoted) if key[1].startswith(start.upper()))

    # -- the actions ----------------------------------------------------------
    def _end(self, action, result=None, error="", quiet=False):
        if error and not quiet:
            self.lines.append(("ERROR", error))
            self.lines.append(("ERROR", "The action stopped due to errors."))
            self.syscc = max(self.syscc, FAILED)
        return SimpleNamespace(**(result or {})), SimpleNamespace(severity=2 if error else 0)

    def __call__(self, action, parameters):
        self.calls.append((action, parameters))
        caslib, name = parameters.get("caslib", ""), parameters.get("name", "")
        quiet = bool(parameters.get("quiet"))
        if action in self.refuse:
            return self._end(action, error=f"The action {action} was refused.")
        if action == "table.tableExists":
            return self._end(action, {"exists": self.exists(caslib, name)})
        if action == "table.tableInfo":
            listed = {key: table for key, table in self.promoted.items() if key[0] == str(caslib).lower()}
            listed.update({key: table for key, table in self.session.items() if key[0] == str(caslib).lower()})
            if name:
                listed = {key: table for key, table in listed.items() if key[1] == str(name).upper()}
                if not listed:
                    return self._end(action, error=f"The table {name} could not be located in caslib {caslib}.", quiet=quiet)
            rows = [SimpleNamespace(Name=key[1], Rows=len(table["rows"]), Label=table["label"]) for key, table in listed.items()]
            return self._end(action, {"TableInfo": rows} if rows else {})
        if action == "table.fileInfo":
            rows = [SimpleNamespace(Name=key[1]) for key in self.files if key[0] == str(caslib).lower()]
            return self._end(action, {"FileInfo": rows} if rows else {})
        if action == "table.columnInfo":
            table = self.get(parameters["table"]["caslib"], parameters["table"]["name"])
            if table is None:
                return self._end(action, error=f"The table {parameters['table']['name']} could not be located.")
            return self._end(action, {"ColumnInfo": [SimpleNamespace(Column=column, Type=kind.split("(")[0]) for column, kind in table["columns"]]})
        if action == "table.loadTable":
            out = parameters["casout"]
            source = self.files.get((str(caslib).lower(), parameters["path"]))
            target = self.key(out["caslib"], out["name"])
            if source is None or parameters["path"] in self.unloadable:
                return self._end(action, error=f"The file or path '{parameters['path']}' is not available in the file system.")
            if target in self.session and not out.get("replace"):
                return self._end(action, error=f"The table {out['name']} exists in the session.")
            self.session[target] = copy.deepcopy(source)
            return self._end(action)
        if action == "table.dropTable":
            key = self.key(caslib, name or parameters.get("table", ""))
            if key in self.session:
                del self.session[key]
            elif key in self.promoted:
                del self.promoted[key]
            elif not quiet:
                return self._end(action, error=f"The table {key[1]} could not be located.")
            return self._end(action)
        if action == "table.copyTable":
            source = self.get(parameters["table"]["caslib"], parameters["table"]["name"])
            out = parameters["casout"]
            target = self.key(out["caslib"], out["name"])
            if source is None or (target in self.session and not out.get("replace")):
                return self._end(action, error=f"The table {parameters['table']['name']} could not be copied.")
            self.session[target] = copy.deepcopy(source)
            return self._end(action)
        if action == "table.alterTable":
            key = self.key(caslib, name)
            scope = self.session if key in self.session else self.promoted
            if key not in scope:
                return self._end(action, error=f"The table {name} could not be located.")
            if "label" in parameters:
                scope[key]["label"] = parameters["label"]
            if "rename" in parameters:
                renamed = self.key(caslib, parameters["rename"])
                if renamed in scope:
                    return self._end(action, error=f"A table {parameters['rename']} exists.")
                scope[renamed] = scope.pop(key)
            return self._end(action)
        if action == "table.promote":
            source, target = self.key(caslib, name), self.key(parameters["targetlib"], parameters["target"])
            if source not in self.session or target in self.promoted:
                return self._end(action, error=f"The table {name} could not be promoted.", quiet=quiet)
            self.promoted[target] = self.session.pop(source)
            return self._end(action)
        if action == "table.save":
            table = self.get(parameters["table"]["caslib"], parameters["table"]["name"])
            file_key = (str(caslib).lower(), name + ".sashdat")
            if table is None or (file_key in self.files and not parameters.get("replace")):
                return self._end(action, error=f"The table {name} could not be saved.")
            self.files[file_key] = copy.deepcopy(table)
            return self._end(action)
        raise AssertionError("an action this harness does not know: " + action)

    # -- running a PROC CAS step ----------------------------------------------
    def run(self, casl):
        """Runs the CASL of one PROC CAS step. Returns the macro variables it set with CALL SYMPUTX."""
        self.symbols = {}

        def symputx(name, value, scope=""):
            self.symbols[name] = str(value)

        def printed(kind, text):
            self.lines.append((kind.upper(), text))

        def saveresult(result, libref, name):
            assert libref.lower() == "work"
            rows = getattr(result, "ColumnInfo", None)
            if rows is not None:
                self.work[name.lower()] = [{"Column": row.Column, "Type": row.Type} for row in rows]

        namespace = {"_cas": self, "_symputx": symputx, "_print": printed, "_saveresult": saveresult, "_string": lambda value: str(value),
                     "exists": lambda result, name: hasattr(result, name), "upcase": lambda text: str(text).upper()}
        exec(compile(casl_as_python(casl), "CASL", "exec"), namespace)
        return dict(self.symbols)


# ---------------------------------------------------------------------------
# DATA steps on CAS tables
# ---------------------------------------------------------------------------

class StepError(Exception):
    pass


REFERENCE = re.compile(r'(\w+)\.(?:"([^"]+)"n|(\w+))(?:\(((?:[^()]|\([^()]*\))*)\))?')


def _references(text):
    """The tables a DATA, SET or MERGE statement names: (libref, table, options, quoted)."""
    found, position = [], 0
    text = text.strip()
    while position < len(text):
        match = REFERENCE.match(text, position)
        assert match, "a table reference this harness does not know: " + text[position:]
        options = {}
        rest = match.group(4) or ""
        renamed = re.search(r"rename=\(([^)]*)\)", rest)
        if renamed:
            options["rename"] = dict(pair.split("=") for pair in renamed.group(1).split())
            rest = rest.replace(renamed.group(0), " ")
        for name in ("keep", "drop", "in"):
            option = re.search(rf"\b{name}=([\w ]+?)(?=\s+\w+=|\s*$)", rest)
            if option:
                options[name] = option.group(1).split() if name != "in" else option.group(1).strip()
                rest = rest.replace(option.group(0), " ")
        assert not rest.strip(), "data set options this harness does not know: " + rest
        found.append(SimpleNamespace(libref=match.group(1).lower(), table=match.group(2) or match.group(3), options=options, quoted=bool(match.group(2))))
        position = match.end()
        while position < len(text) and text[position] == " ":
            position += 1
    return found


def _family(kind):
    return "number" if kind == NUMBER else "varchar" if kind == VARCHAR else "char"


def _data_expression(text):
    """An expression of a DATA step as Python: a name is a variable of the program data vector."""
    def change(part):
        part = re.sub(r"\b(first|last)\.(\w+)", r"_P['\1.\2']", part)
        part = re.sub(r"(?<![\w'\]])([A-Za-z_]\w*)\s+NE\s+\.(?!\w)", r"(_P.get('\1') is not None)", part)
        part = re.sub(r"(?<![\w'\]])([A-Za-z_]\w*)\s+EQ\s+\.(?!\w)", r"(_P.get('\1') is None)", part)
        for word, sign in COMPARISONS:
            part = re.sub(rf"\b{word}\b", sign, part)
        part = re.sub(r"(?<![\w'.\[])(?!(?:and|or|not|is|None|trimn|_P)\b)([A-Za-z_]\w*)(?!['\w(])", r"_P.get('\1')", part)
        assert not re.search(r"[{};]", part), "an expression this harness does not know: " + text
        return part
    return _outside_quotes(text, change).strip()


def _data_simple(statement, outputs):
    if statement == "output":
        return ["_output(None)"]
    out = re.fullmatch(r"output (\S+)", statement)
    if out:
        assert out.group(1).lower() in outputs, "OUTPUT names a table the DATA statement does not: " + statement
        return [f'_output("{out.group(1).lower()}")']
    assignment = re.fullmatch(r"(\w+)\s*=\s*(.*)", statement, flags=re.DOTALL)
    assert assignment, "a DATA step statement this harness does not know: " + statement
    return [f"_assign('{assignment.group(1)}', {_data_expression(assignment.group(2))})"]


def run_data_step(lines, library, librefs):
    """Runs one DATA step that reads and writes CAS tables. Raises StepError where SAS would not run it."""
    statements = _statements("\n".join(lines))
    assert statements[0].startswith("data ") and statements[-1] == "run", statements
    outputs = _references(statements[0][len("data "):])
    output_names = [f"{out.libref}.{out.table}".lower() for out in outputs]
    length, retained, dropped, by, source, merged, body = [], [], [], None, None, False, []
    for statement in statements[1:-1]:
        word = statement.split(" ")[0]
        if word == "length":
            words = statement.split()[1:]
            assert len(words) % 2 == 0, statement
            length = [(name, NUMBER if kind == "8" else VARCHAR if kind == "varchar(*)" else kind) for name, kind in zip(words[0::2], words[1::2])]
            assert all(kind in (NUMBER, VARCHAR) for _, kind in length), statement
        elif word in ("set", "merge"):
            assert source is None, "a second SET or MERGE statement"
            source, merged = _references(statement[len(word) + 1:]), word == "merge"
        elif word == "by":
            by = statement.split()[1:]
        elif word == "retain":
            retained += statement.split()[1:]
        elif word == "drop":
            dropped += statement.split()[1:]
        else:
            body.append(statement)
    assert source is not None and (by is not None) == merged, "a step this harness does not know"

    # -- the tables it reads --------------------------------------------------
    tables = []
    for reference in source:
        assert reference.libref in librefs, "a libref that is not assigned: " + reference.libref
        if reference.quoted and len(reference.table) > 32:
            raise StepError("ERROR 307-185: The data set name cannot have more than 32 bytes.")
        table = library.get(librefs[reference.libref], reference.table)
        if table is None:
            raise StepError(f"ERROR: File {reference.libref.upper()}.{reference.table}.DATA does not exist.")
        columns = [(name, kind) for name, kind in table["columns"]]
        if "keep" in reference.options:
            assert all(name in dict(columns) for name in reference.options["keep"]), "KEEP= names a column the table does not have"
            columns = [(name, kind) for name, kind in columns if name in reference.options["keep"]]
        rename = reference.options.get("rename", {})
        assert all(name in dict(columns) for name in rename), "RENAME= names a column the table does not have"
        rows = [{rename.get(name, name): row[name] for name, _ in columns} for row in table["rows"]]
        tables.append(SimpleNamespace(columns=[(rename.get(name, name), kind) for name, kind in columns], rows=rows, flag=reference.options.get("in")))

    # -- the variables, in the order the step meets them ------------------------
    variables = {}
    for name, kind in length + [column for table in tables for column in table.columns]:
        first = variables.setdefault(name, kind)
        if _family(first) != _family(kind):
            raise StepError(f"ERROR: Variable {name} has been defined as both {_family(first)} and {_family(kind)}.")
    assigned = []
    code, depth = [], 0
    for statement in body:
        if statement == "end":
            depth -= 1
            continue
        opened = re.fullmatch(r"(else )?if (.*) then do", statement, flags=re.DOTALL)
        single = re.fullmatch(r"(else )?if (.*?) then (.*)", statement, flags=re.DOTALL)
        subset = re.fullmatch(r"if (.*)", statement, flags=re.DOTALL)
        otherwise = re.fullmatch(r"else (?!if |do$)(.*)", statement, flags=re.DOTALL)
        if statement == "else do":
            code += ["    " * depth + "else:", "    " * (depth + 1) + "pass"]
            depth += 1
        elif otherwise:
            code.append("    " * depth + "else:")
            code += ["    " * (depth + 1) + line for line in _data_simple(otherwise.group(1), output_names)]
            assigned += re.findall(r"^(\w+)\s*=", otherwise.group(1))
        elif opened:
            code += ["    " * depth + ("elif " if opened.group(1) else "if ") + _data_expression(opened.group(2)) + ":", "    " * (depth + 1) + "pass"]
            depth += 1
        elif single:
            code.append("    " * depth + ("elif " if single.group(1) else "if ") + _data_expression(single.group(2)) + ":")
            code += ["    " * (depth + 1) + line for line in _data_simple(single.group(3), output_names)]
            assigned += re.findall(r"^(\w+)\s*=", single.group(3))
        elif subset:
            assert depth == 0, "a subsetting IF inside a group"
            code.append(f"if not ({_data_expression(subset.group(1))}): raise _Next()")
        else:
            code += ["    " * depth + line for line in _data_simple(statement, output_names)]
            assigned += re.findall(r"^(\w+)\s*=", statement)
    assert depth == 0, "a group that is not closed"
    for name in assigned + retained:
        variables.setdefault(name, NUMBER)
    explicit = any("_output(" in line for line in code)
    flags = [table.flag for table in tables if table.flag]

    class _Next(Exception):
        pass

    written = {name: [] for name in output_names}
    kept = {}
    for out, name in zip(outputs, output_names):
        columns = [(column, kind) for column, kind in variables.items() if column not in dropped and column not in flags]
        if "keep" in out.options:
            columns = [(column, kind) for column, kind in columns if column in out.options["keep"]]
        if "drop" in out.options:
            columns = [(column, kind) for column, kind in columns if column not in out.options["drop"]]
        kept[name] = columns

    def missing_of(name):
        return None if _family(variables[name]) == "number" else ""

    program_vector = {}

    def assign(name, value):
        program_vector[name] = int(value) if isinstance(value, bool) else value

    def output(name):
        for target in ([name] if name else output_names):
            written[target].append({column: program_vector.get(column, missing_of(column)) for column, _ in kept[target]})

    namespace = {"_P": program_vector, "_assign": assign, "_output": output, "_Next": _Next,
                 "trimn": lambda value: (value or "").rstrip(" ")}
    compiled = compile("\n".join(code) + "\n", "DATA step", "exec")

    def observation(read, more):
        """One pass of the step: the values that were read, then its statements, then the row it writes."""
        for name in variables:
            if name not in retained and name not in read and name not in more.get("kept", ()):
                program_vector[name] = missing_of(name)
        program_vector.update(read)
        program_vector.update(more.get("flags", {}))
        try:
            exec(compiled, namespace)
        except _Next:
            return
        if not explicit:
            output(None)

    for name in variables:
        program_vector[name] = missing_of(name)
    if not merged:
        for table in tables:
            for row in table.rows:
                observation(row, {"flags": {other.flag: int(other is table) for other in tables if other.flag}})
    else:
        for key in by:
            assert all(key in dict(table.columns) for table in tables), "BY names a column a table does not have"
            if len({_family(dict(table.columns)[key]) for table in tables} | {_family(variables[key])}) != 1:
                raise StepError(f"ERROR: Variable {key} has been defined as both char and varchar.")

        def group_of(row):
            # a text is compared without the blanks behind it
            return tuple(row[key].rstrip(" ") if isinstance(row[key], str) else row[key] for key in by)

        def in_order(group):
            # a missing value stands in front of every other one
            return tuple((0, 0) if part is None or part == "" else (1, part) for part in group)

        values = sorted({group_of(row) for table in tables for row in table.rows}, key=in_order)
        for number, value in enumerate(values):
            groups = [[row for row in table.rows if group_of(row) == value] for table in tables]
            count = max(len(group) for group in groups)
            held = {}
            for position in range(count):
                read = {}
                for group in groups:
                    if position < len(group):
                        read.update(group[position])
                held.update(read)
                marks = {table.flag: int(bool(group)) for table, group in zip(tables, groups) if table.flag}
                for depth, key in enumerate(by):
                    begins = number == 0 or values[number - 1][:depth + 1] != value[:depth + 1]
                    ends = number == len(values) - 1 or values[number + 1][:depth + 1] != value[:depth + 1]
                    marks[f"first.{key}"], marks[f"last.{key}"] = int(position == 0 and begins), int(position == count - 1 and ends)
                # a table without a further row in the BY group keeps the values of its last one
                observation(dict(held), {"flags": marks, "kept": set(held)})
    for out, name in zip(outputs, output_names):
        if out.table.lower() == "_null_":
            continue
        assert out.libref in librefs, "a libref that is not assigned: " + out.libref
        # a table of the session, without a label: a DATA step writes none
        library.session[library.key(librefs[out.libref], out.table)] = {"columns": kept[name], "rows": written[name], "label": ""}


def columns_step(lines, rows):
    """What the DATA _NULL_ step that reads the columns of the existing table leaves in macro variables, by its
    statements: the three texts for the columns the table holds as CHAR, one flag per line of the form
    IF LOWCASE(STRIP(Column)) EQ '<name>' AND LOWCASE(STRIP(Type)) IN (<types>) THEN CALL SYMPUTX('<variable>', '1'),
    and one flag per counter that lines of the form ... THEN <counter> + 1 add to and a line
    IF lastColumn AND <counter> EQ <n> THEN CALL SYMPUTX('<variable>', '1') reads."""
    text = " ".join(" ".join(lines).split())
    for statement in ("set work._log_target_columns end=lastColumn;",
                      "fixedRename = catx(' ', fixedRename, cats(Column, '=_fx_', Column));",
                      "fixedAssign = catx(' ', fixedAssign, cats(Column, '=trimn(_fx_', Column, ');'));",
                      "fixedDrop = catx(' ', fixedDrop, cats('_fx_', Column));",
                      "if lastColumn and fixedRename NE '' then do;",
                      "call symputx('_log_fixed_rename', cats('rename=(', fixedRename, ')'));",
                      "call symputx('_log_fixed_assign', catx(' ', 'if _log_existing_row then do;', fixedAssign, 'end;'));",
                      "call symputx('_log_fixed_drop', catx(' ', 'drop', fixedDrop, ';'));"):
        assert statement in text, statement
    listed = text[text.index("if lowcase(strip(Type)) EQ 'char' and lowcase(strip(Column)) in (") :]
    handed_over = re.findall(r"'(\w+)'", listed[listed.index(" in (") : listed.index(") then do;")])
    flags = re.findall(r"if lowcase\(strip\(Column\)\) EQ '(\w+)' and lowcase\(strip\(Type\)\) in \(([^)]*)\) then call symputx\('(\w+)', '1'\);", text)
    found = {}
    fixed = [row["Column"] for row in rows if row["Type"].strip().lower() == "char" and row["Column"].strip().lower() in handed_over]
    def holds(column, kinds):
        return sum(1 for row in rows if row["Column"].strip().lower() == column and row["Type"].strip().lower() in re.findall(r"'(\w+)'", kinds))

    for column, kinds, variable in flags:
        if holds(column, kinds):
            found[variable] = "1"
    counted = re.findall(r"if lowcase\(strip\(Column\)\) EQ '(\w+)' and lowcase\(strip\(Type\)\) in \(([^)]*)\) then (\w+) \+ 1;", text)
    for counter, number, variable in re.findall(r"if lastColumn and (\w+) EQ (\d+) then call symputx\('(\w+)', '1'\);", text):
        if sum(holds(column, kinds) for column, kinds, name in counted if name == counter) == int(number):
            found[variable] = "1"
    assert len(flags) + len(counted) == text.count("lowcase(strip(Column)) EQ '"), "a line about a column this harness does not know"
    if fixed:
        found["_log_fixed_rename"] = "rename=(" + " ".join(f"{column}=_fx_{column}" for column in fixed) + ")"
        found["_log_fixed_assign"] = " ".join(["if _log_existing_row then do;"] + [f"{column}=trimn(_fx_{column});" for column in fixed] + ["end;"])
        found["_log_fixed_drop"] = " ".join(["drop"] + [f"_fx_{column}" for column in fixed] + [";"])
    return found


# ---------------------------------------------------------------------------
# One run of the program
# ---------------------------------------------------------------------------

class Run:
    """What one run left: the lines of the log as (kind, text), the condition code and the macro variables."""

    def __init__(self, lines, syscc, macros, uploaded):
        self.lines, self.syscc, self.macros, self.uploaded = lines, syscc, macros, uploaded

    def of(self, kind):
        return [text for found, text in self.lines if found == kind]

    @property
    def errors(self):
        return self.of("ERROR")

    @property
    def notes(self):
        return self.of("NOTE")


def _open_code(program, start, stop):
    """Statements of open code - %IF blocks that hold %LET and %PUT, and calls of the macros of the program - as
    the body of a macro, which is the form macro_harness follows."""
    return "%macro _open_code;\n" + program[program.index(start):program.index(stop)] + "%mend _open_code;\n"


def run_program(program, library, monkeypatch, log_path, setting=None, caslib="Public", table="LLM_LOGS", promote="1", save="1"):
    """One run of the program - the code file or the program of the step - with the setting _log_append_table
    (None for a run without a value), against the tables and files of `library`."""
    front = program[:program.index("proc python restart;")]
    symbols = dict(re.findall(r"^%let (\w+) = ?(.*);$", front, flags=re.MULTILINE))
    symbols.update({"_log_append_table": "" if setting is None else setting, "_log_target_table_lib": caslib, "_log_target_table_name": table,
                    "_log_promote_table": promote, "_log_save_table": save, "_log_target_table_engine": "CAS", "syserr": "0"})
    macros = Macros(symbols, {"exist": lambda arguments: int(arguments[0].lower().split(".")[-1] in library.work),
                              "max": lambda arguments: max(int(argument) for argument in arguments)})
    lines = []
    state = {"cas": None, "data": None, "librefs": {"work": "work"}, "stopped": False}

    def data_step(step):
        first = step[0]
        if state["stopped"] or any(name.upper() in first.upper() for name in library.fail_steps):
            # a step that does not run, and every DATA step behind it in a job: the tables stay as they were
            if not state["stopped"]:
                lines.append(("ERROR", "The DATA step did not run."))
            state["stopped"] = True
            macros.symbols["syserr"] = str(FAILED)
            library.syscc = max(library.syscc, FAILED)
            return
        if first == "data _null_;":
            for name, value in columns_step(step, library.work["_log_target_columns"]).items():
                macros.symbols[name.lower()] = value
            macros.symbols["syserr"] = "0"
            return
        try:
            run_data_step(step, library, state["librefs"])
            macros.symbols["syserr"] = "0"
        except StepError as error:
            lines.append(("ERROR", str(error)))
            state["stopped"] = True
            macros.symbols["syserr"] = str(FAILED)
            library.syscc = max(library.syscc, FAILED)

    def on_text(line, macros):
        if not line:
            return
        if line.startswith(casl_harness.CALLED):
            called = line[len(casl_harness.CALLED):].rstrip(";")
            follow(called)
        elif line == "proc cas;":
            state["cas"] = [line]
        elif state["cas"] is not None:
            state["cas"].append(line)
            if line in ("quit;", "run; quit;"):
                before = len(library.lines)
                for variable, value in library.run("\n".join(state["cas"])).items():
                    macros.symbols[variable.lower()] = value
                lines.extend(library.lines[before:])
                state["cas"] = None
        elif re.match(r"data\s", line):
            state["data"] = [line]
        elif state["data"] is not None:
            state["data"].append(line)
            if line == "run;":
                step, state["data"] = state["data"], None
                data_step(step)
        elif line.startswith("libname "):
            assigned = re.fullmatch(r"libname (\w+) cas caslib=[\"'](.*)[\"'];", line)
            cleared = re.fullmatch(r"libname (\w+) clear;", line)
            assert assigned or cleared, line
            if assigned:
                state["librefs"][assigned.group(1).lower()] = assigned.group(2)
            else:
                state["librefs"].pop(cleared.group(1).lower(), None)
        elif line.startswith("proc delete data=work."):
            library.work.pop(line[len("proc delete data=work."):].rstrip(";").lower(), None)
        else:
            assert line == "run;", "a line of SAS code this harness does not know: " + line

    def follow(name, text=None):
        _, written = macros.run(casl_harness.in_blocks(text or program, name), name, on_text)
        for line in written:
            kind = line.split(":")[0] if line.split(":")[0] in ("ERROR", "NOTE", "WARNING") else "PUT"
            lines.append((kind, line[len(kind) + 2:] if kind != "PUT" else line))
        assert state["cas"] is None and state["data"] is None, "a step that is not closed"

    # the setting becomes the mode, and a run without a value stops at a table that holds rows
    follow("_open_code", _open_code(program, "%let _log_table_mode = REFUSED;", "* Get the caslib name of the libname;"))
    follow("_open_code", _open_code(program, "%if &_log_table_mode. EQ START and &_log_ready. EQ 1 %then %do;", "* The number of requests found in the log."))

    # the Python: it reads the log where the run was not refused and uploads its rows to CASUSER
    uploaded = None
    try:
        parsed = log_parser_harness.run_parser(monkeypatch, log_path, python=log_parser_harness.python_block(program),
                                               ready=macros["_log_ready"], table_mode=macros["_log_table_mode"])
        sas = parsed.sas
        for upload in parsed.uploads:
            assert (upload["casout"]["caslib"], upload["casout"]["name"], upload["casout"]["replace"]) == ("CASUSER", "_TEMP_LLM_LOGS", True)
            columns = [(variable["name"], NUMBER if variable["type"] == "double" else VARCHAR) for variable in upload["importoptions"]["vars"]]
            frame = upload["frame"]
            rows = [{name: (None if value != value or value is None else value) for name, value in row.items()} for row in frame.to_dict("records")]
            uploaded = table_of(columns, rows, upload["casout"]["label"])
            library.put("CASUSER", "_TEMP_LLM_LOGS", uploaded, scope="session")
    except FileNotFoundError as error:
        sas = error.sas
        lines.append(("ERROR", "The log file could not be read."))
    lines.extend(sas.messages)
    for name in ("_log_row_count", "_log_row_repeats"):
        if name in sas.macro_variables:
            macros.symbols[name] = sas.macro_variables[name]

    follow("_log_handle_output_table")

    failed = program[program.rindex("%if &_log_row_count. LT 0 or &_log_failed. EQ 1 %then %do;"):]
    assert failed.split("\n")[1].strip() == f"%let SYSCC = {FAILED};"
    if macros.true("&_log_row_count. LT 0 or &_log_failed. EQ 1"):
        library.syscc = FAILED
    syscc, library.syscc = library.syscc, 0
    return Run(lines, syscc, macros, uploaded)
