"""The collection script of the administration guide (Logging & Monitoring): it reads the
model pods only, it writes nothing that was not redacted, and the parser reads what it
writes exactly like the unredacted log.

The redaction is run for real: the sed expressions are taken from the guide and handed
to sed. That needs GNU sed, as the script itself does; without it these tests are skipped.

The guide also says where the parser reads the collected file. That part is held against
the program it describes.
"""
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
    llm_block,
    log_line,
    parse,
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


def redaction_expressions():
    """The sed expressions of the script's redact_llm_log function, as the shell hands them to sed."""
    match = re.search(r"^redact_llm_log\(\) \{\n(.*?)\n\}$", collection_script(), re.DOTALL | re.MULTILINE)
    assert match, "the collection script no longer defines redact_llm_log"
    command = shlex.split(match.group(1).replace("\\\n", " "))
    assert command[:2] == ["sed", "-E"]
    options, expressions = command[2::2], command[3::2]
    assert options == ["-e"] * len(expressions)
    return expressions


def gnu_sed():
    sed = shutil.which("sed")
    if not sed:
        return False
    version = subprocess.run([sed, "--version"], capture_output=True, text=True)
    return version.returncode == 0 and "GNU" in version.stdout


needs_gnu_sed = pytest.mark.skipif(not gnu_sed(), reason="the redaction of the collection script needs GNU sed")


def redact(text, tmp_path):
    # The expressions reach sed in a script file, which keeps their quotes out of the command line
    script = tmp_path / "redact_llm_log.sed"
    script.write_text("".join(expression + "\n" for expression in redaction_expressions()), encoding="utf-8", newline="\n")
    result = subprocess.run(["sed", "-E", "-f", str(script)], input=text.encode("utf-8"), capture_output=True, check=True)
    return result.stdout.decode("utf-8")


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
# Where the parser reads the file
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
    default = re.search(r"^%let _log_path = sasserver:\S+;$", code_file_program(), re.MULTILINE).group(0)
    assert f"`{default}`" in section


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
