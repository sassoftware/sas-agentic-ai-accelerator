"""Follows the text of a SAS macro the way the macro processor does, for the part of the macro
language the macros of this folder are written in.

SAS can not run here. What can be followed without it is the macro logic: which statements a macro
generates for a value of a setting, and which lines it writes to the log. This module knows

    %let, %local, %put, %return
    %if ... %then %do / %else %if ... %then %do / %else %do / %end, and %if ... %then %let
    %do <variable> = <from> %to <to>
    &name. references, %superq, %str, %length, %scan, %eval, %sysevalf and %sysfunc with the
    functions it is handed (countw and max are built in)
    the automatic macro variable SYSCC, 0 where a test sets none
    conditions with GT, LT, GE, LE, EQ, NE (and > < =), joined by and

and nothing else: a statement of the macro language it does not know stops it with an
AssertionError, so that a macro is never followed half. A line that is no macro statement is text
the macro generates - a part of a DATA step or of a procedure - and is handed back with its
references resolved; it is not run.

THE ONE RULE THAT MATTERS HERE is how a macro function gets its arguments: its text is resolved
first and then split at every comma that is not masked. A value that comes from an &name.
reference is not masked, so a comma inside it ends an argument; a value that comes from %superq
or %str is, so it is one argument whatever it holds. A masked comma or blank is kept as a
character of its own until the text leaves the macro processor.
"""
import re

COMMA, BLANK = chr(0xE000), chr(0xE001)      # two characters of the private use area: no text holds them
_MASK = str.maketrans({",": COMMA, " ": BLANK})
_UNMASK = str.maketrans({COMMA: ",", BLANK: " "})
# What ends a word for %scan when the call names no delimiters (ASCII systems)
DEFAULT_DELIMITERS = " .<(+&!$*);^-/,%|"

IF_DO = re.compile(r"%if (.*) %then %do;")
IF_LET = re.compile(r"%if (.*) %then %let (\w+) = (.*);")
ELSE_IF = re.compile(r"%else %if (.*) %then %do;")
ELSE_DO = re.compile(r"%else %do;")
DO_LOOP = re.compile(r"%do (\w+) = (.*) %to (.*);")
LET = re.compile(r"%let (\w+) = ?(.*);")
PUT = re.compile(r"%put (.*);")
CALL = re.compile(r"%(\w+)\(")
REFERENCE = re.compile(r"&(\w+)\.?")
COMPARISON = re.compile(r"\s*(.*?)\s+(GT|LT|GE|LE|EQ|NE|>|<|=)\s+(.*?)\s*", flags=re.IGNORECASE)
INTEGER = re.compile(r"[+-]?\d+")


class MacroError(Exception):
    """An ERROR line of the macro processor. `operand` is the text it could not take."""

    def __init__(self, message, operand=""):
        super().__init__(message)
        self.operand = operand


class _Return(Exception):
    pass


def masked(text):
    """A text in which no comma and no blank is read by the macro processor."""
    return text.translate(_MASK)


def plain(text):
    """A text as it leaves the macro processor."""
    return text.translate(_UNMASK)


def arguments(text):
    """The arguments of a macro function: the text is split at every comma that is not masked
    and not inside parentheses. Blanks around an argument are no part of it."""
    parts, depth, start = [], 0, 0
    for position, character in enumerate(text):
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif character == "," and depth == 0:
            parts.append(text[start:position])
            start = position + 1
    parts.append(text[start:])
    return [part.strip(" ") for part in parts]


def words(text, delimiters):
    """The words of a text: what stands between the delimiters. Delimiters that follow each
    other end one word, and a delimiter at the start or at the end ends none."""
    pattern = "[" + re.escape(plain(delimiters)) + "]+"
    return [word for word in re.split(pattern, plain(text)) if word]


def scan(args):
    """%scan(text, n <, delimiters>)."""
    if len(args) < 2:
        raise MacroError("Macro function %SCAN has too few arguments.")
    if not INTEGER.fullmatch(args[1]):
        raise MacroError("Argument 2 to macro function %SCAN is not a number.", plain(args[1]))
    found = words(args[0], args[2] if len(args) > 2 else DEFAULT_DELIMITERS)
    position = int(args[1])
    return found[position - 1] if 1 <= position <= len(found) else ""


def countw(args):
    """countw(text <, delimiters>)."""
    return len(words(args[0], args[1] if len(args) > 1 else DEFAULT_DELIMITERS))


def _closing(text, opened):
    depth = 0
    for position in range(opened, len(text)):
        depth += (text[position] == "(") - (text[position] == ")")
        if depth == 0:
            return position
    raise AssertionError("a parenthesis is not closed: " + text)


def _number(text, whole):
    if not INTEGER.fullmatch(text.strip()):
        raise MacroError("A character operand was found in the %EVAL function or %IF condition where a numeric operand is required. "
                         "The condition was: " + plain(whole).strip(), plain(text).strip())
    return int(text)


def _compare(text):
    """One comparison, or one value that is true when it is a number other than 0."""
    match = COMPARISON.fullmatch(text)
    if not match:
        return _number(text, text) != 0
    left, operator, right = match.group(1), match.group(2).upper(), match.group(3)
    if INTEGER.fullmatch(left) and INTEGER.fullmatch(right):
        left, right = int(left), int(right)
    return {"GT": left > right, ">": left > right, "LT": left < right, "<": left < right, "GE": left >= right, "LE": left <= right,
            "EQ": left == right, "=": left == right, "NE": left != right}[operator]


class Macros:
    """The symbol table and the functions %sysfunc may call. Names of macro variables are
    taken without their case, as SAS takes them."""

    def __init__(self, symbols=None, functions=None):
        self.symbols = {name.lower(): str(value) for name, value in (symbols or {}).items()}
        # SYSCC is there in every SAS session: 0 until a step ends with a warning or an error
        self.symbols.setdefault("syscc", "0")
        self.functions = dict({"countw": countw, "max": lambda args: max(int(plain(arg)) for arg in args)}, **(functions or {}))

    def __getitem__(self, name):
        return plain(self.symbols[name.lower()])

    def __contains__(self, name):
        return name.lower() in self.symbols

    def resolve(self, text):
        """A text with its references and its macro functions resolved, masked characters kept."""
        resolved, position = [], 0
        while position < len(text):
            reference = REFERENCE.match(text, position) if text[position] == "&" else None
            call = CALL.match(text, position) if text[position] == "%" else None
            if reference:
                if reference.group(1).lower() not in self.symbols:
                    raise MacroError(f"Apparent symbolic reference {reference.group(1).upper()} not resolved.")
                resolved.append(self.symbols[reference.group(1).lower()])
                position = reference.end()
            elif call:
                closing = _closing(text, call.end() - 1)
                resolved.append(self.call(call.group(1).lower(), text[call.end():closing]))
                position = closing + 1
            else:
                resolved.append(text[position])
                position += 1
        return "".join(resolved)

    def call(self, name, inner):
        if name == "superq":
            return masked(self.symbols[inner.strip().lower()])
        if name == "str":
            return masked(inner)
        if name == "length":
            return str(len(self.resolve(inner).strip(" ")))
        if name == "scan":
            return scan(arguments(self.resolve(inner)))
        if name in ("eval", "sysevalf"):
            return str(int(self.true(inner)))
        if name == "sysfunc":
            resolved = self.resolve(inner)
            function = re.fullmatch(r"\s*(\w+)\((.*)\)\s*", resolved, flags=re.DOTALL)
            assert function and function.group(1).lower() in self.functions, "a function this harness was not handed: " + plain(resolved)
            return str(self.functions[function.group(1).lower()](arguments(function.group(2))))
        raise AssertionError("a macro function this harness does not know: %" + name)

    def true(self, condition):
        resolved = self.resolve(condition)
        assert not re.search(r"\s(or|not)\s", resolved, flags=re.IGNORECASE), "a condition this harness does not know: " + condition
        return all(_compare(part) for part in re.split(r"\s+and\s+", resolved, flags=re.IGNORECASE))

    def run(self, program, name, on_text=None):
        """Follows the macro `name` of a program. Returns (the text the macro generated, line by
        line; the lines it wrote with %put). on_text(line, macros) is called for every line of
        generated text, which is how a test stands in for the step that would run it."""
        start = program.index(f"%macro {name};") + len(f"%macro {name};")
        body = re.sub(r"/\*.*?\*/", "", program[start:program.index(f"%mend {name};")], flags=re.DOTALL)
        lines = [line.strip() for line in body.split("\n") if line.strip()]
        generated, log = [], []

        def block_end(first):
            depth = 0
            for position in range(first, len(lines)):
                depth += len(re.findall(r"%do\b", lines[position])) - len(re.findall(r"%end\b", lines[position]))
                if depth == 0:
                    return position
            raise AssertionError("a %do without its %end: " + lines[first])

        def execute(first, stop):
            position = first
            while position < stop:
                line = lines[position]
                if IF_DO.fullmatch(line):
                    taken = False
                    # the block of the %if and the %else blocks behind it: the first whose condition holds is followed
                    while True:
                        end = block_end(position)
                        head = lines[position]
                        condition = (IF_DO.fullmatch(head) or ELSE_IF.fullmatch(head))
                        if not taken and (condition is None or self.true(condition.group(1))):
                            taken = True
                            execute(position + 1, end)
                        position = end + 1
                        if position >= stop or not (ELSE_IF.fullmatch(lines[position]) or ELSE_DO.fullmatch(lines[position])):
                            break
                    continue
                if IF_LET.fullmatch(line):
                    condition, variable, value = IF_LET.fullmatch(line).groups()
                    if self.true(condition):
                        self.symbols[variable.lower()] = self.resolve(value).strip(" ")
                elif DO_LOOP.fullmatch(line):
                    variable, low, high = DO_LOOP.fullmatch(line).groups()
                    end = block_end(position)
                    for value in range(int(self.resolve(low)), int(self.resolve(high)) + 1):
                        self.symbols[variable.lower()] = str(value)
                        execute(position + 1, end)
                    position = end
                elif line.startswith("%local "):
                    pass
                elif LET.fullmatch(line):
                    variable, value = LET.fullmatch(line).groups()
                    self.symbols[variable.lower()] = self.resolve(value).strip(" ")
                elif PUT.fullmatch(line):
                    log.append(plain(self.resolve(PUT.fullmatch(line).group(1))))
                elif line == "%return;":
                    raise _Return()
                else:
                    assert not line.startswith("%"), "a statement of the macro language this harness does not know: " + line
                    text = plain(self.resolve(line))
                    generated.append(text)
                    if on_text:
                        on_text(text, self)
                position += 1

        try:
            execute(0, len(lines))
        except _Return:
            pass
        return generated, log
