"""The collection script of the administration guide (Logging & Monitoring): it reads the
model pods only, it writes nothing that was not redacted, and the parser reads what it
writes exactly like the unredacted log.

The redaction is run for real: the sed expressions are taken from the guide and handed
to sed. That needs GNU sed, as the script itself does; without it these tests are skipped.
On Windows, run the Logging suite from Git Bash (or put Git's usr/bin first on PATH):
from PowerShell `bash` is the WSL launcher and there is no sed, so 33 tests of this file skip.

The guide also says where the parser reads the collected file and what it writes to the
SAS log. Those parts are held against the program they describe.
"""
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from log_parser_harness import (
    DECISION_ANSWERS,
    DECISION_QUESTIONS,
    as_log,
    code_file_program,
    decision_block,
    embedding_block,
    failed_block,
    llm_block,
    log_line,
    parse,
    step_definition,
)

REPOSITORY = Path(__file__).resolve().parents[3]
GUIDE = REPOSITORY / "website" / "docs" / "Administration-Guide" / "Logging-&-Monitoring.md"

# Made-up secrets in the shapes the containers log them
PROVIDER_KEY = "sk-made-up-A1b2C3d4E5f6G7h8"
OTHER_KEY = "AIzaMadeUp_0123456789-abcdef"
COOKIE = "sessionid=made-up-cookie-value; consent=yes"
BEARER = "Bearer made-up-bearer-token"
CSRF = "made-up-csrf-token"
SECRETS = [PROVIDER_KEY, OTHER_KEY, "made-up-cookie-value", "made-up-bearer-token", CSRF]


def collection_script():
    """The script as the guide prints it."""
    guide = GUIDE.read_text(encoding="utf-8").replace("\r\n", "\n")
    match = re.search(r"```bash\n(# Set the script variables for your environment\n.*?)```", guide, re.DOTALL)
    assert match, "the guide no longer holds the collection script"
    return match.group(1)


def redaction_command():
    """The command of the script's redact_llm_log function, as the shell reads it: (the variables it sets for
    sed in front of it, the sed expressions)."""
    match = re.search(r"^redact_llm_log\(\) \{\n(.*?)\n\}$", collection_script(), re.DOTALL | re.MULTILINE)
    assert match, "the collection script no longer defines redact_llm_log"
    command = shlex.split(match.group(1).replace("\\\n", " "))
    assert "sed" in command
    environment, command = command[:command.index("sed")], command[command.index("sed"):]
    assert all(re.fullmatch(r"[A-Z_]+=\S*", word) for word in environment), environment
    assert command[:2] == ["sed", "-E"]
    options, expressions = command[2::2], command[3::2]
    assert options == ["-e"] * len(expressions)
    return dict(word.split("=", 1) for word in environment), expressions


def redaction_expressions():
    """The sed expressions of the script's redact_llm_log function, as the shell hands them to sed."""
    return redaction_command()[1]


def gnu_sed():
    sed = shutil.which("sed")
    if not sed:
        return False
    version = subprocess.run([sed, "--version"], capture_output=True, text=True)
    return version.returncode == 0 and "GNU" in version.stdout


needs_gnu_sed = pytest.mark.skipif(not gnu_sed(), reason="the redaction of the collection script needs GNU sed")
needs_bash = pytest.mark.skipif(not shutil.which("bash") or not gnu_sed(), reason="the collection script needs bash and GNU sed")


def missing(value):
    return value is None or value != value


def redact_bytes(data, tmp_path, locale=None):
    """What the redaction of the script makes of the bytes of a log: sed with the expressions of the script and with
    the variables the script sets in front of it - or, with `locale`, in that locale in their place."""
    # The expressions reach sed in a script file, which keeps their quotes out of the command line
    script = tmp_path / "redact_llm_log.sed"
    environment, expressions = redaction_command()
    script.write_text("".join(expression + "\n" for expression in expressions), encoding="utf-8", newline="\n")
    settings = {name: value for name, value in os.environ.items() if name != "LC_ALL" and not name.startswith("LC_")}
    settings.update({"LC_ALL": locale} if locale else environment)
    return subprocess.run(["sed", "-E", "-f", str(script)], input=data, capture_output=True, check=True, env=settings).stdout


def redact(text, tmp_path):
    return redact_bytes(text.encode("utf-8"), tmp_path).decode("utf-8")


def unredacted_log():
    """Requests of all three kinds with a provider key in their options and secrets in their headers."""
    browser_headers = (f"Cookie: {COOKIE}", f"Authorization: {BEARER}", f"X-Csrf-Token: {CSRF}")
    # A header can be the first one, which the containers log on the 'Request Headers:' line itself
    cookie_first = llm_block(timestamp="2026-01-05 09:40:00", options=f"{{API_KEY:{PROVIDER_KEY}}}")
    cookie_first[1] = log_line("2026-01-05 09:40:00", "api", f"Request Headers: Cookie: {COOKIE}")
    return as_log(
        llm_block(options=f"{{temperature:0.7,API_KEY:{PROVIDER_KEY},max_tokens:60}}", headers=browser_headers,
                  user_prompt="What does the header Cookie: do, and what is an Authorization: header?"),
        # A value with an apostrophe makes the containers quote the options with double quotes
        llm_block(timestamp="2026-01-05 09:16:00", system_prompt="Don't guess.", options=f"{{API_KEY:{OTHER_KEY}}}",
                  headers=(f"cookie: {COOKIE}", f"AUTHORIZATION: {BEARER}", f"X-CSRF-TOKEN: {CSRF}")),
        decision_block(options=f"{{API_KEY:{PROVIDER_KEY}}}", headers=browser_headers),
        embedding_block(options=f"{{Embedding_Mode:query,API_KEY:{OTHER_KEY}}}"),
        cookie_first,
    )


# ---------------------------------------------------------------------------
# What the script reads and where it writes
# ---------------------------------------------------------------------------

def test_script_reads_the_model_pods_only():
    script = collection_script()
    pod_listings = [line for line in script.split("\n") if "kubectl get pods" in line]
    assert len(pod_listings) == 1
    assert '-l "$llm_pod_selector"' in pod_listings[0]
    assert re.search(r'^llm_pod_selector="app"$', script, re.MULTILINE)


def test_every_line_the_script_writes_went_through_the_redaction():
    script = collection_script()
    writes = [line for line in script.split("\n") if ">" in line and not line.lstrip().startswith("#")]
    assert writes, "the script writes no file"
    for line in writes:
        assert re.search(r"\| redact_llm_log >> \"\$llm_log_path\"$", line), line


# ---------------------------------------------------------------------------
# The line in front of the lines of every container (release 2.3.0)
#
# The tests take the script from the guide, as the ones above do: the bash block that starts with
# "# Set the script variables for your environment". They hold the script that keeps the lines of a
# container apart from its own line (escape_llm_log), which the guide carries from the commit that
# puts it there.
# ---------------------------------------------------------------------------

def collection_marker():
    """The pattern the parser takes the line by, from the code file."""
    found = re.search(r"^COLLECTION_MARKER = re\.compile\(r'(.*)'\)$", code_file_program(), re.MULTILINE)
    assert found, "the parser no longer defines COLLECTION_MARKER"
    return re.compile(found.group(1))


def marker_format():
    """The format of the printf that writes the line, as the shell hands it to printf."""
    found = re.search(r"^\s*printf (.*)$", collection_script(), re.MULTILINE)
    assert found, "the collection script writes no line in front of the lines of a container"
    words = shlex.split(found.group(1))
    return words[0], words[1:]


def test_the_script_writes_one_line_in_front_of_the_lines_of_every_container():
    script = collection_script()
    fmt, arguments = marker_format()

    assert fmt == "#=# llm-log-collection pod=%s container=%s collected=%s #=#\\n"
    assert arguments == ["${pod#pod/}", "$container", "$llm_collected_at"]
    # one time of collection for the run, in UTC, taken before the first container
    assert re.search(r'^llm_collected_at="\$\(date -u \+%Y-%m-%dT%H:%M:%SZ\)"$', script, re.MULTILINE)
    assert script.index("llm_collected_at=") < script.index("for pod in")
    # every container of every pod on its own, the line in front of its lines, both through the redaction
    loop = script[script.index("for pod in"):]
    assert "for container in $(kubectl get -n \"$llm_namespace\" \"$pod\" -o jsonpath='{.spec.containers[*].name}'); do" in loop
    # the lines of the container reach the file through escape_llm_log, the line of the script does not; kubectl is
    # called without --timestamps and without --prefix, so a line arrives as the container wrote it, from its start
    assert re.search(r"\{\n\s*printf .*\n\s*kubectl logs -n \"\$llm_namespace\" \"\$pod\" -c \"\$container\" --since=\"\$llm_logging_interval\" \| escape_llm_log\n"
                     r"\s*\} \| redact_llm_log >> \"\$llm_log_path\"\n", loop)
    assert script.count("escape_llm_log") == 2 and "--timestamps" not in script and "--prefix" not in script
    assert "--all-containers" not in script
    # kubectl only reads
    for command in re.findall(r"kubectl (\w+)", script):
        assert command in ("get", "logs"), command


def test_the_line_is_the_one_the_parser_takes():
    fmt, _ = marker_format()
    line = fmt.replace("\\n", "") % ("demo-llm-5d8f7c9b6-abcde", "demo-llm", "2026-10-13T09:00:00Z")

    assert collection_marker().fullmatch(line)
    # a log line starts with its time and is never taken for it; a line of a container that starts like it is another
    # matter, which escape_llm_log is for
    assert not line[:4].isdigit() and not collection_marker().fullmatch(log_line("2026-01-05 09:00:00", "api", line))


# ---------------------------------------------------------------------------
# A line of a container that looks like the line of the script
# ---------------------------------------------------------------------------

LINE_START = "#=# llm-log-collection "


def escape_expressions():
    """The sed expressions of the script's escape_llm_log function, as the shell hands them to sed."""
    match = re.search(r"^escape_llm_log\(\) \{\n(.*?)\n\}$", collection_script(), re.DOTALL | re.MULTILINE)
    assert match, "the collection script does not define escape_llm_log"
    command = shlex.split(match.group(1).replace("\\\n", " "))
    assert command[:2] == ["sed", "-E"]
    options, expressions = command[2::2], command[3::2]
    assert options == ["-e"] * len(expressions)
    return expressions


def escaped_line():
    """The pattern the parser takes the backslash off by, from the code file."""
    found = re.search(r"^ESCAPED_LINE = re\.compile\(r'(.*)'\)$", code_file_program(), re.MULTILINE)
    assert found, "the parser no longer defines ESCAPED_LINE"
    return re.compile(found.group(1))


def test_the_script_the_marker_and_the_parser_name_one_start_of_a_line():
    """What makes a line of the shape certain in a file the script wrote: every line the parser can take for the line
    of the script starts with LINE_START, the script puts a backslash in front of every line of a container that starts
    with it - at the start of a line and behind a carriage return, where the parser begins a line as well, and with or
    without backslashes in front, so that no two lines of a container become the same line - and the parser takes one
    backslash off such a line."""
    fmt, _ = marker_format()
    assert fmt.startswith(LINE_START)
    assert collection_marker().pattern.startswith(LINE_START) and re.escape(LINE_START).replace("\\", "") == LINE_START
    expressions = escape_expressions()
    assert expressions == ["s/(^|\\r)(\\\\*" + LINE_START + ")/\\1\\\\\\2/g", "$a\\"]
    assert escaped_line().pattern == "\\\\+" + LINE_START
    # the parser reads a file with universal newlines: a line begins at the start of the file, behind a line feed
    # and behind a carriage return, and nowhere else
    program = code_file_program()
    assert "with open(file_path, 'r', encoding='utf-8', errors='replace') as f:" in program
    assert "COLLECTION_MARKER.fullmatch(line.rstrip('\\r\\n'))" in program and program.count("COLLECTION_MARKER.") == 1


def test_the_guide_says_what_the_script_does_with_a_line_that_looks_like_its_own(needs_the_script_of_this_release):
    """Review-19: the guide said that no container writes a line that starts like the line of the script, and that a line
    of that shape with an older time of collection is text. Neither is what the script and the parser do."""
    guide = " ".join(GUIDE.read_text(encoding="utf-8").split())

    assert "No container writes a line that starts like it" not in guide
    assert "whose time of collection is older than" not in guide
    assert "puts a backslash in front of every line of a container that starts like the line of the script" in guide
    assert "takes the backslash off again" in guide
    assert "has to put the backslash in front as the script does" in guide


def collect(tmp_path, logs, collected_at="2026-10-13T09:00:00Z"):
    """Runs the script as the guide prints it with a kubectl of the test that answers from files, and returns the text
    of the file it wrote. logs is {(pod, container): the bytes 'kubectl logs' prints for it}, in the order the pods and
    their containers are listed. kubectl and date are shell functions in front of the script: a function is found
    before any command of the PATH, so no kubectl of the machine is called, and the time of collection is known."""
    folder = tmp_path / "kubectl"
    folder.mkdir()
    pods = {}
    for (pod, container), content in logs.items():
        pods.setdefault(pod, []).append(container)
        (folder / f"{pod}.{container}.log").write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    stub = ("kubectl() {\n"
            '  if [ "$1" = get ] && [ "$2" = pods ]; then printf "' + "".join(f"pod/{pod}\\n" for pod in pods) + '"; return 0; fi\n'
            '  if [ "$1" = get ]; then case "$4" in ' + " ".join(f'pod/{pod}) echo "{" ".join(names)}";;' for pod, names in pods.items())
            + ' esac; return 0; fi\n'
            '  if [ "$1" = logs ]; then cat "' + folder.as_posix() + '/${4#pod/}.$6.log"; return 0; fi\n'
            "  return 1\n"
            "}\n"
            f"date() {{ echo {collected_at}; }}\n")
    collected = tmp_path / "llms.log"
    script = re.sub(r'^llm_log_path=".*"$', f'llm_log_path="{collected.as_posix()}"', collection_script(), flags=re.MULTILINE)
    assert script.count(collected.as_posix()) == 1
    (tmp_path / "collect.sh").write_text(stub + script, encoding="utf-8", newline="\n")
    subprocess.run(["bash", str(tmp_path / "collect.sh")], check=True, capture_output=True)
    return collected.read_bytes().decode("utf-8")


def script_lines(text):
    return [line for line in re.split(r"\r\n|\r|\n", text) if collection_marker().fullmatch(line)]


RUN_TIME = "2026-10-13T09:00:00Z"
# Lines of the shape as a model can write them into a response, with the time of the run that collects them,
# an earlier and a later one - the parser of version 2.6 took the later one for the end of a part and every
# line of the script behind it for text
LOOK_ALIKES = [
    f"#=# llm-log-collection pod=made-up container=scr collected={RUN_TIME} #=#",
    "#=# llm-log-collection pod=made-up container=scr collected=2020-01-01T00:00:00Z #=#",
    "#=# llm-log-collection pod=made-up container=scr collected=2099-12-31T23:59:59Z #=#",
]


@pytest.fixture
def needs_the_script_of_this_release():
    assert "escape_llm_log" in collection_script(), (
        "the guide holds the collection script of an earlier round: the script with escape_llm_log is handed over with "
        "this round and stands in the guide from the commit that puts it there")


@pytest.fixture
def needs_the_redaction_of_this_release():
    assert "LC_ALL=C sed -E" in collection_script(), (
        "the guide holds the collection script of an earlier round: the script whose redaction runs sed in the C locale is "
        "handed over with this round and stands in the guide from the commit that puts it there")


def _response_lines_cases():
    """The lines a response is made of, by case: lines of the shape in the places and spellings the round names."""
    earlier, same, later = LOOK_ALIKES[1], LOOK_ALIKES[0], LOOK_ALIKES[2]
    return {
        "one with an earlier time": ["Here you go:", earlier, "line two"],
        "one with the time of the run": ["Here you go:", same, "line two"],
        "one with a later time": ["Here you go:", later, "line two"],
        "two in a row": ["Here you go:", same, later, "line two"],
        "the last line of the response": ["Here you go:", later],
        "on the response line itself": [later, "line two"],
        "with a blank in front": ["Here you go:", " " + later, "line two"],
        "with a backslash in front": ["Here you go:", "\\" + later, "\\\\" + same, "line two"],
        "behind a carriage return": ["Here you go:", "abc\r" + later + "\r" + same, "line two"],
    }


@needs_bash
@pytest.mark.parametrize("case", list(_response_lines_cases()))
@pytest.mark.parametrize("line_end", ["\n", "\r\n"], ids=["LF", "CRLF"])
def test_a_container_line_that_looks_like_the_line_of_the_script_ends_no_part(needs_the_script_of_this_release, monkeypatch, tmp_path, case, line_end):
    """Review-19 B1. Two pods; the response of the first request of the first one holds lines that are, character for
    character, lines the script could have written, and a request is running where the lines of that pod end. The
    file the script writes has two lines of the shape - the two the script wrote -, and the parser gives the rows it
    gives for the same logs without the look-alike lines: only the response differs, and it holds those lines as the
    container wrote them."""
    lines = _response_lines_cases()[case]
    look_alikes = [line for line in lines if LINE_START in line]

    def logs(response_lines):
        first = llm_block(timestamp="2026-01-05 09:00:00", user_prompt="first", response="\n".join(response_lines))
        running = llm_block(timestamp="2026-01-05 09:00:10", user_prompt="still running")
        running = running[:next(index for index, line in enumerate(running) if " - scoreModel - INFO - prompt_length: " in line) + 1]
        other_pod = llm_block(timestamp="2026-01-05 09:00:30", user_prompt="other pod")
        return {("demo-llm-1", "scr"): as_log(first, running).replace("\n", line_end),
                ("demo-llm-2", "scr"): as_log(other_pod).replace("\n", line_end)}

    (tmp_path / "with").mkdir()
    (tmp_path / "without").mkdir()
    with_lines = collect(tmp_path / "with", logs(lines))
    without = collect(tmp_path / "without", logs([line for line in lines if line not in look_alikes]))

    assert [collection_marker().fullmatch(line).group(1) for line in script_lines(with_lines)] == ["demo-llm-1", "demo-llm-2"]
    marked = parse(monkeypatch, tmp_path / "with", with_lines)
    plain = parse(monkeypatch, tmp_path / "without", without)
    assert list(marked["user_prompt"]) == ["first", "still running", "other pod"]
    assert marked.drop(columns="response").equals(plain.drop(columns="response"))
    assert list(marked["response"])[1:] == list(plain["response"])[1:]
    # the response as the container wrote it: its lines without the blanks around them, joined by one blank
    assert marked.loc[0, "response"] == " ".join(part.strip() for line in lines for part in line.split("\r"))
    assert (marked.loc[0, "status_code"], marked.loc[0, "failed"]) == (200, 0)
    assert missing(marked.loc[1, "failed"]) and (marked.loc[2, "status_code"], marked.loc[2, "failed"]) == (200, 0)


@needs_bash
@pytest.mark.parametrize("ending", ["\n", ""], ids=["with its line feed", "without a line feed"])
def test_a_look_alike_as_the_last_line_of_a_pod_leaves_the_next_line_of_the_script_alone(needs_the_script_of_this_release, monkeypatch, tmp_path, ending):
    """The last line 'kubectl logs' prints for a pod is a line of the shape, and with 'without a line feed' the output
    ends in the middle of a line. The line of the script for the next pod still starts at the start of a line: the
    script ends the last line of a container where it is not ended."""
    first = as_log(llm_block(timestamp="2026-01-05 09:00:00", user_prompt="first", response="Here you go:")) + LOOK_ALIKES[2] + ending
    second = as_log(llm_block(timestamp="2026-01-05 09:00:30", user_prompt="other pod"))
    text = collect(tmp_path, {("demo-llm-1", "scr"): first, ("demo-llm-2", "scr"): second})

    assert [collection_marker().fullmatch(line).group(1) for line in script_lines(text)] == ["demo-llm-1", "demo-llm-2"]
    assert text.endswith("\n") and "\\" + LOOK_ALIKES[2] + "\n" in text
    frame = parse(monkeypatch, tmp_path, text)
    assert list(frame["user_prompt"]) == ["first", "other pod"] and list(frame["failed"]) == [0, 0]


@needs_bash
def test_look_alikes_outside_of_a_response_end_no_part_either(needs_the_script_of_this_release, monkeypatch, tmp_path):
    """A line of the shape between two requests, in a traceback, and as the only thing a pod printed."""
    failing = failed_block("2026-01-05 09:00:20", "demo_llm", [("systemPrompt", "s"), ("userPrompt", "failing"), ("options", "{}")],
                           error=None, exception=LOOK_ALIKES[2])
    logs = {("demo-llm-1", "scr"): as_log(llm_block(timestamp="2026-01-05 09:00:00", user_prompt="first"), [LOOK_ALIKES[0]],
                                          llm_block(timestamp="2026-01-05 09:00:10", user_prompt="second"), failing),
            ("demo-llm-2", "scr"): LOOK_ALIKES[2] + "\n",
            ("demo-llm-3", "scr"): as_log(llm_block(timestamp="2026-01-05 09:00:30", user_prompt="other pod"))}
    text = collect(tmp_path, logs)

    assert [collection_marker().fullmatch(line).group(1) for line in script_lines(text)] == ["demo-llm-1", "demo-llm-2", "demo-llm-3"]
    frame = parse(monkeypatch, tmp_path, text)
    assert list(frame["user_prompt"]) == ["first", "second", "failing", "other pod"]
    assert list(frame["failed"]) == [0, 0, 1, 0]
    # the last line of the traceback, as the container wrote it
    assert frame.loc[2, "error_text"].endswith(LOOK_ALIKES[2])


@needs_gnu_sed
def test_the_redaction_makes_no_line_of_the_shape(tmp_path):
    """The lines of a container are escaped first and redacted then, together with the line of the script. The
    redaction changes what follows API_KEY: and what follows the name of three headers, and replaces it by REDACTED:
    it puts nothing in front of a line, and where it takes a backslash away it takes the look-alike behind it away
    as well or puts text in its place. So it makes no line a line of the shape. Held with lines made for it, and
    said by the About tab of the step and the README of the folder."""
    escaped = "\\" + LOOK_ALIKES[2]
    for line in (escaped,
                 "API_KEY:made-up-key," + "\r" + escaped,
                 "API_KEY:made-up-key\r" + escaped,
                 "Cookie: made-up\r" + escaped,
                 "x API_KEY:" + escaped):
        redacted = redact(line + "\n", tmp_path)
        assert script_lines(redacted) == [], line
        assert all(not part.startswith(LINE_START) for part in re.split(r"\r\n|\r|\n", redacted)), line
    # and the line of the script itself passes as it is (the pod and the container are names of Kubernetes objects)
    assert redact(LOOK_ALIKES[0] + "\n", tmp_path) == LOOK_ALIKES[0] + "\n"
    sentence = "The redaction leaves the line of the script as it is and makes no other line a line of that shape"
    about = next(control["text"] for control in _controls(json.loads(step_definition()["ui"])["pages"]) if control["id"] == "textColumns")
    assert sentence in about and sentence in " ".join((REPOSITORY / "SAS-Viya-Integrations" / "Logging-Monitoring" / "README.md").read_text(encoding="utf-8").split())


def _controls(children):
    for child in children:
        yield child
        yield from _controls(child.get("children", []))


@needs_gnu_sed
def test_the_line_passes_the_redaction_as_it_is(tmp_path):
    fmt, _ = marker_format()
    line = fmt.replace("\\n", "") % ("demo-llm-5d8f7c9b6-abcde", "demo-llm", "2026-10-13T09:00:00Z")
    assert redact(line + "\n", tmp_path) == line + "\n"


@needs_bash
def test_a_collection_with_a_kubectl_that_answers_from_files_is_read_in_its_parts(monkeypatch, tmp_path):
    """The script as the guide prints it, run with a kubectl of the test that knows two pods - one with two containers
    - and answers 'kubectl logs' with a request block each. The file has one line in front of each container, the
    parser reads one row per request, and the same lines without those three lines are read the same."""
    blocks = {("demo-llm-1", "scr"): llm_block(timestamp="2026-01-05 09:00:00", user_prompt="first"),
              ("demo-llm-2", "scr"): llm_block(timestamp="2026-01-05 08:59:59", user_prompt="second"),
              ("demo-llm-2", "sidecar"): llm_block(timestamp="2026-01-05 09:00:05", user_prompt="third")}
    text = collect(tmp_path, {where: as_log(block) for where, block in blocks.items()})
    markers = [line for line in text.split("\n") if collection_marker().fullmatch(line)]
    assert [collection_marker().fullmatch(line).group(1, 2) for line in markers] == [("demo-llm-1", "scr"), ("demo-llm-2", "scr"),
                                                                                  ("demo-llm-2", "sidecar")]
    assert len({collection_marker().fullmatch(line).group(3) for line in markers}) == 1
    (tmp_path / "marked").mkdir()
    (tmp_path / "plain").mkdir()
    marked = parse(monkeypatch, tmp_path / "marked", text)
    plain = parse(monkeypatch, tmp_path / "plain", "".join(line + "\n" for line in text.split("\n")[:-1] if line not in markers))
    assert list(marked["user_prompt"]) == ["first", "second", "third"] and marked.equals(plain)


def test_the_shipped_deployment_templates_label_their_pods_app():
    """The selector of the script is the label the deployment templates give every model pod."""
    templates = sorted((REPOSITORY / "SCR-LLM-Deployment-YAML").glob("deploy-*.yaml"))
    assert templates
    for template in templates:
        text = template.read_text(encoding="utf-8")
        assert re.search(r"template:\s*\n\s*metadata:\s*\n\s*labels:\s*\n\s*app: ", text), template.name


# ---------------------------------------------------------------------------
# The redaction
# ---------------------------------------------------------------------------

@needs_gnu_sed
def test_no_secret_is_left_after_the_redaction(tmp_path):
    original = unredacted_log()
    assert all(secret in original for secret in SECRETS)

    redacted = redact(original, tmp_path)

    for secret in SECRETS:
        assert secret not in redacted, secret
    assert redacted.count("API_KEY:REDACTED") == original.count("API_KEY:")
    # One header line per secret header, in the spelling the container used
    for header in ("Cookie: REDACTED", "cookie: REDACTED", "Authorization: REDACTED", "AUTHORIZATION: REDACTED",
                   "X-Csrf-Token: REDACTED", "X-CSRF-TOKEN: REDACTED", "Request Headers: Cookie: REDACTED"):
        assert header in redacted, header


@needs_gnu_sed
def test_the_redaction_changes_nothing_else(tmp_path):
    original = unredacted_log().split("\n")
    redacted = redact(unredacted_log(), tmp_path).split("\n")

    assert len(redacted) == len(original)
    for before, after in zip(original, redacted):
        if any(secret in before for secret in SECRETS):
            assert after != before
        else:
            assert after == before
    # Redacting a redacted log changes nothing
    assert redact("\n".join(redacted), tmp_path) == "\n".join(redacted)


# A byte that is no UTF-8: the containers log what a client sends, and a header or a key is no text they have checked
NO_UTF8 = b"\xff"
BROKEN = [
    (b"Cookie: abc" + NO_UTF8 + b"made-up-cookie-value\n", b"Cookie: REDACTED\n"),
    (b"x API_KEY:sk-abc" + NO_UTF8 + b"made-up-key, y\n", b"x API_KEY:REDACTED, y\n"),
    (b"Authorization: Bearer " + NO_UTF8 + NO_UTF8 + b"made-up-bearer-token\n", b"Authorization: REDACTED\n"),
    # the byte in front of the name of the header, in the line the first header stands on
    (b"2026-01-05 09:40:00 - a" + NO_UTF8 + b"pi - INFO - Request Headers: X-CSRF-TOKEN: made-up-csrf-token\n",
     b"2026-01-05 09:40:00 - a" + NO_UTF8 + b"pi - INFO - Request Headers: X-CSRF-TOKEN: REDACTED\n"),
]


def test_the_redaction_runs_sed_in_the_c_locale(needs_the_redaction_of_this_release):
    """Review-20. In a UTF-8 locale GNU sed matches a byte that is no UTF-8 with neither '.' nor a bracket
    expression, so the redaction stopped at such a byte: what stood behind it in a Cookie header or behind API_KEY:
    reached the file in clear text. In the C locale every byte is a character. The script sets the locale for the
    one sed that redacts; the sed that escapes names every character it matches and needs none."""
    environment, expressions = redaction_command()

    assert environment == {"LC_ALL": "C"} and len(expressions) == 4
    script = collection_script()
    assert script.count("LC_ALL=C sed -E \\\n") == 1 and script.count("sed -E \\\n") == 2
    assert script.index("redact_llm_log() {\n  LC_ALL=C sed -E \\\n") < script.index("escape_llm_log() {\n  sed -E \\\n")
    # the guide says why
    guide = " ".join(GUIDE.read_text(encoding="utf-8").split())
    assert "`redact_llm_log` runs `sed` in the C locale (`LC_ALL=C`)" in guide


@needs_gnu_sed
@pytest.mark.parametrize("raw, redacted", BROKEN, ids=["a cookie", "a provider key", "a bearer token", "in front of the header"])
def test_a_byte_that_is_no_utf8_does_not_end_the_redaction(needs_the_redaction_of_this_release, tmp_path, raw, redacted):
    assert redact_bytes(raw, tmp_path) == redacted
    # the same expressions in a UTF-8 locale: where sed knows the locale, the secret behind the byte is left - which
    # is what was measured with GNU sed 4.9 - and where it does not, sed reads bytes as in the C locale
    for locale in ("en_US.UTF-8", "C.UTF-8"):
        left = redact_bytes(raw, tmp_path, locale=locale)
        assert left == redacted or b"made-up" in left, (locale, left)
    # a line without such a byte is redacted the same in both
    plain = raw.replace(NO_UTF8, b"")
    assert redact_bytes(plain, tmp_path) == redact_bytes(plain, tmp_path, locale="en_US.UTF-8") == redacted.replace(NO_UTF8, b"")


@needs_bash
def test_the_script_redacts_a_header_with_a_byte_that_is_no_utf8(needs_the_redaction_of_this_release, monkeypatch, tmp_path):
    """The script as the guide prints it, run in a shell whose locale is UTF-8: the file it writes holds no byte of
    the secrets, and the parser reads it."""
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.delenv("LC_ALL", raising=False)
    block = llm_block(timestamp="2026-01-05 09:00:00", user_prompt="first", options="{API_KEY:sk-abcBREAKmade-up-key}",
                      headers=("Cookie: abcBREAKmade-up-cookie-value",))
    raw = as_log(block).encode("utf-8").replace(b"BREAK", NO_UTF8)
    assert raw.count(NO_UTF8) == 2
    text = collect(tmp_path, {("demo-llm-1", "scr"): raw})

    assert "made-up" not in text and "Cookie: REDACTED" in text and "API_KEY:REDACTED" in text
    frame = parse(monkeypatch, tmp_path, text)
    assert list(frame["user_prompt"]) == ["first"] and list(frame["failed"]) == [0]


def test_the_guide_says_which_lines_the_redaction_expects():
    """The guide is read by sites that collect the logs with a process of their own."""
    guide = " ".join(GUIDE.read_text(encoding="utf-8").split())
    assert "works on any stream" not in guide
    warning = guide[guide.index(":::danger"):guide.index("## Simplified Logging via a Script")]
    assert "without `--timestamps` or `--prefix`" in warning
    assert "at the start of a line" in warning


@needs_gnu_sed
def test_a_header_behind_a_line_prefix_is_not_redacted(tmp_path):
    """The three header expressions match a header where the containers write it, at the
    start of a line. A log pipeline that puts something in front of every line gets its
    headers through unredacted - the guide says so, and this test holds the two together:
    whoever widens the expressions changes the guide with them."""
    as_written = f"Cookie: {COOKIE}\nAuthorization: {BEARER}\nX-Csrf-Token: {CSRF}\n"
    assert redact(as_written, tmp_path) == "Cookie: REDACTED\nAuthorization: REDACTED\nX-Csrf-Token: REDACTED\n"

    for prefix in ("2026-01-05T09:15:00.123456789Z ",                    # kubectl logs --timestamps
                   "[pod/demo-llm-5d8f7c9b6-abcde/demo-llm] ",            # kubectl logs --prefix
                   "2026-01-05T09:15:00.123456789+00:00 stdout F "):      # the log file of the container runtime
        prefixed = "".join(prefix + line + "\n" for line in as_written.splitlines())
        assert redact(prefixed, tmp_path) == prefixed, prefix

        # The provider key is found wherever it stands
        with_key = prefix + log_line("2026-01-05 09:15:00", "api", f"Request Data: {{'value': '{{API_KEY:{PROVIDER_KEY}}}'}}") + "\n"
        assert PROVIDER_KEY not in redact(with_key, tmp_path), prefix


@needs_gnu_sed
def test_the_parser_reads_a_redacted_log_like_the_unredacted_one(monkeypatch, tmp_path):
    (tmp_path / "plain").mkdir()
    (tmp_path / "redacted").mkdir()
    plain = parse(monkeypatch, tmp_path / "plain", unredacted_log())
    redacted = parse(monkeypatch, tmp_path / "redacted", redact(unredacted_log(), tmp_path))

    assert len(redacted) == 5
    assert redacted.equals(plain)
    # The inputs are read from the 'Request Data:' line that held the key
    assert list(redacted["model"]) == ["demo_llm", "demo_llm", "demo_decider", "demo_embedder", "demo_llm"]
    assert redacted.loc[0, "user_prompt"] == "What does the header Cookie: do, and what is an Authorization: header?"
    assert (redacted.loc[0, "temperature"], redacted.loc[0, "max_tokens"]) == ("0.7", "60")
    assert redacted.loc[1, "system_prompt"] == "Don't guess."
    assert (redacted.loc[2, "system_prompt"], redacted.loc[2, "response"]) == (DECISION_QUESTIONS, DECISION_ANSWERS)
    assert redacted.loc[3, "prompt_length"] == 17


# ---------------------------------------------------------------------------
# Where the parser reads the file, and what it says in the SAS log
# ---------------------------------------------------------------------------

def guide_section(heading, next_heading):
    """A section of the guide on one line, from its heading to the next one named."""
    guide = " ".join(GUIDE.read_text(encoding="utf-8").split())
    return guide[guide.index(heading):guide.index(next_heading)]


def copy_from_sas_content():
    """The SAS statements of the guide that bring the collected file from SAS Content to the compute server."""
    guide = GUIDE.read_text(encoding="utf-8").replace("\r\n", "\n")
    match = re.search(r"```SAS\n(/\* Copy the collected file from SAS Content to the SAS compute server \*/\n.*?)```", guide, re.DOTALL)
    assert match, "the guide no longer holds the copy from SAS Content"
    return match.group(1)


def test_the_guide_calls_its_paths_examples_and_names_the_ones_in_use():
    """The path the script writes to and the path the parser reads are the same file only
    where both hosts mount the same storage. The guide says so about the two paths that
    the script and the code file really hold."""
    section = guide_section("## Getting the File to the SAS Compute Server", "### Without a Shared Mount")

    assert "site specific" in section
    script_path = re.search(r'^llm_log_path="([^"]+)"$', collection_script(), re.MULTILINE).group(1)
    assert f"`{script_path}`" in section
    # The code file takes the path from this line where _log_path is not set; the guide shows it as the
    # statement a site sets the path with
    default = re.search(r"^%_log_default\(_log_path, (sasserver:\S+)\);$", code_file_program(), re.MULTILINE).group(1)
    assert f"`%let _log_path = {default};`" in section


def test_the_copy_from_sas_content_ends_where_the_parser_reads():
    """The parser takes a file on the SAS server only, named with sasserver: in front."""
    block = copy_from_sas_content()

    assert re.search(r"^filename llmsrc filesrvc folderpath='[^']+' filename='[^']+' recfm=n lrecl=32767;$", block, re.MULTILINE)
    target = re.search(r"^filename llmdst '([^']+)' recfm=n lrecl=32767;$", block, re.MULTILINE).group(1)
    assert "rc = fcopy('llmsrc', 'llmdst');" in block
    assert f"%let _log_path = sasserver:{target};" in block
    assert "if lowCase(locationType) ne 'sasserver' then do;" in code_file_program()


def test_the_copy_from_sas_content_names_no_system():
    block = copy_from_sas_content()

    assert "folderpath='/Users/<user>/My Folder/<folder>'" in block
    assert "://" not in block and ".com" not in block


def test_the_guide_quotes_the_messages_the_parser_writes():
    section = guide_section("### Reading the Job Log", "## What Was Measured")
    program = code_file_program()

    table = "&_log_target_table_lib..&_log_target_table_name."
    assert f"%put NOTE: The log contains no requests, the table {table} was left as it is.;" in program
    assert "`NOTE: The log contains no requests, the table <caslib>.<table> was left as it is.`" in section
    assert f"%put ERROR: The log could not be parsed, the table {table} was left as it is.;" in program
    assert "`ERROR: The log could not be parsed, the table <caslib>.<table> was left as it is.`" in section
    assert "No request was read: 'Request: POST' was found in {unmatched_requests} line(s) that do not start as a log line" in program
    assert "No request was read: 'Request: POST' was found in <n> line(s) that do not start as a log line" in section
    for message in ("The saved table &_log_target_table_lib..&_log_target_table_name. could not be loaded, nothing was appended and the saved file was left as it is.",
                    "The new table could not be created, the table &_log_target_table_lib..&_log_target_table_name. was left as it is."):
        assert f'print (error) "{message}";' in program
        assert "`" + message.replace(table, "<caslib>.<table>") + "`" in section
    # A system can write lines of its own before the program starts
    assert "lines before the first source line are not the parser's" in section
