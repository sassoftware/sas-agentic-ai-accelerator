# Copyright © 2026, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Two renderers, one MANIFEST block.

retrieve_context.py is manifested in two places: `rag_core.manifest` serves
the SAS Studio step RAG - Register Setup, and the Prompt Builder's
`rag-manifest.ts` does the same in the browser, where no Python runs. The
duplication is deliberate; the price is that the two can drift, and a drift
is silent - a setup registered through the step would simply behave
differently from the same setup manifested from the Builder.

SSL_VERIFY is where that matters most: it decides whether a deployed model
verifies TLS certificates, and the tag TLS-Verification-Off has to mean the
same thing whichever path set it. So the names, the rule and - where a
Node.js is installed that can run the Builder's TypeScript - the rendered
text itself are compared here.

The VARIABLES of the model are the second thing both sides write: the step
registers them with the model (`RETRIEVAL_INPUTS` / `RETRIEVAL_OUTPUTS` in
rag_core/registration.py), the Builder writes them with every save of a setup
(`INPUT_VARIABLES` / `OUTPUT_VARIABLES` in rag-builder.ts). They drifted, and
it showed on a live system: the Builder wrote `context_dg` and
`context_envelope` without a length, SAS Model Manager stored 99, SAS
Intelligent Decisioning declared them `varchar(100)` in the wrapper of the
model, and a decision published to SAS Micro Analytic Service failed as soon
as chunks came back - HTTP 201, an empty error column, every output empty.
The two lists are compared here entry by entry.
"""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

from rag_core import manifest, registration

RAG = pathlib.Path(__file__).resolve().parents[1]
REPO = RAG.parents[1]
BUILDER = REPO / "LLM-Prompt-Builder"
TS_MANIFEST = BUILDER / "src" / "objects" / "rag-manifest.ts"
TS_BUILDER = BUILDER / "src" / "objects" / "rag-builder.ts"
TS_TAG = BUILDER / "src" / "objects" / "prompt-decision.ts"
TEMPLATE = RAG / "retrieve_context.py"

SETTINGS = {
    "BACKEND": "pgvector", "COLLECTION": "rag_hr_policies_v1",
    "EMBED_MODEL": "all_minilm_l6_v2", "EMBED_ENDPOINT": "https://viya.example/llm",
    "STORE_HOST": "db.example", "STORE_PORT": "5432", "STORE_DB": "postgres",
    "STORE_SSLMODE": "require", "CREDENTIAL_DOMAIN": "agentic-ai-keys",
    "DEFAULT_K": 6, "INGESTION_RUN_ID": "run-123",
}


def _ts():
    return TS_MANIFEST.read_text(encoding="utf-8")


def _ts_string(name):
    found = re.search(r"const %s = '([^']*)';" % name, _ts())
    assert found, f"rag-manifest.ts no longer declares {name} as a string"
    return found.group(1)


def _ts_names(name):
    found = re.search(r"const %s = \[(.*?)\] as const;" % name, _ts(), re.DOTALL)
    assert found, f"rag-manifest.ts no longer declares {name} as a list"
    return tuple(re.findall(r"'([A-Z_]+)'", found.group(1)))


def _old_template():
    template = TEMPLATE.read_text(encoding="utf-8")
    assert template.count("\nSSL_VERIFY = True\n") == 1
    return template.replace("\nSSL_VERIFY = True\n", "\n")


# ---------------------------------------------------------------------------
# the names
# ---------------------------------------------------------------------------
def test_both_renderers_look_for_the_same_block():
    assert _ts_string("MANIFEST_START") == manifest.MANIFEST_START
    assert _ts_string("MANIFEST_END") == manifest.MANIFEST_END


@pytest.mark.parametrize("name", ["MANIFEST_STRINGS", "MANIFEST_NUMBERS",
                                  "MANIFEST_OPTIONAL_BOOLEANS", "REQUIRED"])
def test_both_renderers_write_the_same_names_in_the_same_order(name):
    assert _ts_names(name) == tuple(getattr(manifest, name))


def test_the_template_holds_exactly_the_names_the_renderers_write():
    """A name only the template has would be dropped by a manifest; a name
    only the renderers have would be invented by one."""
    lines = TEMPLATE.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines)
                 if line.startswith(manifest.MANIFEST_START))
    end = next(i for i, line in enumerate(lines)
               if i > start and line.startswith(manifest.MANIFEST_END))
    in_template = {line.split("=")[0].strip() for line in lines[start + 1:end]}
    assert in_template == (set(manifest.MANIFEST_STRINGS)
                           | set(manifest.MANIFEST_NUMBERS)
                           | set(manifest.MANIFEST_OPTIONAL_BOOLEANS))


def test_both_sides_set_the_same_tag():
    found = re.search(r"export const TLS_VERIFICATION_OFF_TAG = '([^']*)';",
                      TS_TAG.read_text(encoding="utf-8"))
    assert found, "prompt-decision.ts no longer declares TLS_VERIFICATION_OFF_TAG"
    assert found.group(1) == registration.TLS_VERIFICATION_OFF_TAG
    assert registration.TLS_VERIFICATION_OFF_TAG == "TLS-Verification-Off"


# ---------------------------------------------------------------------------
# the rule, as each side spells it
# ---------------------------------------------------------------------------
def test_the_builder_writes_false_for_the_literal_false_only():
    """`=== false` is the TypeScript of `is False`. A looser test on either
    side - a truthiness check, a comparison with a word - would let the two
    renderers disagree about what an empty or a mistyped value means."""
    assert "block.push(`${key} = ${settings[key] === false ? 'False' : 'True'}`);" in _ts()
    assert "if (templateBlock.some((line) => assigns(line, key))) {" in _ts()


def test_the_builder_asks_the_same_question_about_an_old_template():
    """The Builder's manifestBlockHas and rag_core's manifest_block_has are
    how each caller finds out that the setting was not written."""
    assert "export function manifestBlockHas(template: string, name: string): boolean" in _ts()
    assert manifest.manifest_block_has(TEMPLATE.read_text(encoding="utf-8"),
                                       "SSL_VERIFY") is True
    assert manifest.manifest_block_has(_old_template(), "SSL_VERIFY") is False


# ---------------------------------------------------------------------------
# the rendered text, where the Builder's renderer can be run
# ---------------------------------------------------------------------------
_RUNNER = r"""
const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');
const nodeModule = require('node:module');
if (typeof nodeModule.stripTypeScriptTypes !== 'function') {
  process.exit(3);
}
// rag-manifest.ts uses only syntax that can be erased (type annotations,
// `import type`, `as const`), so Node.js itself turns it into JavaScript
const source = fs.readFileSync(process.argv[2], 'utf8');
const compiled = path.join(path.dirname(process.argv[1]), 'rag-manifest.mjs');
fs.writeFileSync(compiled, nodeModule.stripTypeScriptTypes(source));
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
import(pathToFileURL(compiled).href).then((loaded) => {
  const results = cases.map((one) => {
    try {
      return {
        text: loaded.renderRetrievalModel(one.template, one.settings),
        has: loaded.manifestBlockHas(one.template, 'SSL_VERIFY'),
      };
    } catch (error) {
      return { error: String(error && error.message) };
    }
  });
  process.stdout.write(JSON.stringify(results));
});
"""

# what a caller may put under SSL_VERIFY; _ABSENT = the key is not there
_ABSENT = object()
_ASKED = [_ABSENT, True, False, None, "", "false", "False", 0, 1]


def _builder_renders(tmp_path, cases):
    """Run rag-manifest.ts itself on the cases, or skip where it cannot run."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed to run the Builder's renderer")
    runner = tmp_path / "run-rag-manifest.cjs"
    runner.write_text(_RUNNER, encoding="utf-8")
    done = subprocess.run(
        [node, str(runner), str(TS_MANIFEST)],
        input=json.dumps(cases).encode("utf-8"), capture_output=True, timeout=120)
    if done.returncode == 3:
        pytest.skip("this Node.js cannot strip TypeScript types "
                    "(module.stripTypeScriptTypes, Node.js 22.13 or later)")
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    return json.loads(done.stdout.decode("utf-8"))


def test_both_renderers_produce_the_same_text(tmp_path):
    """Byte for byte, for the shipped template and for one that predates the
    setting, and for every value a caller may send for it."""
    templates = {"shipped": TEMPLATE.read_text(encoding="utf-8"),
                 "before the setting": _old_template()}
    cases = []
    for label, template in templates.items():
        for asked in _ASKED:
            settings = dict(SETTINGS)
            if asked is not _ABSENT:
                settings["SSL_VERIFY"] = asked
            cases.append({"label": label, "template": template,
                          "settings": settings})
    results = _builder_renders(tmp_path, cases)
    assert len(results) == len(cases)
    for case, result in zip(cases, results):
        said = (case["label"], case["settings"].get("SSL_VERIFY", "absent"))
        assert "error" not in result, (said, result)
        assert result["text"] == manifest.render_retrieval_model(
            case["template"], case["settings"]), said
        assert result["has"] is manifest.manifest_block_has(
            case["template"], "SSL_VERIFY"), said
        # and what the stored text does is what both were asked for
        expected_off = (case["label"] == "shipped"
                        and case["settings"].get("SSL_VERIFY", True) is False)
        assert manifest.manifested_ssl_verify(result["text"]) is not expected_off, said


def test_both_renderers_refuse_the_same_templates_and_settings(tmp_path):
    cases = [
        {"template": "print('not the template')\n", "settings": dict(SETTINGS)},
        {"template": TEMPLATE.read_text(encoding="utf-8"),
         "settings": dict(SETTINGS, COLLECTION="")},
        {"template": TEMPLATE.read_text(encoding="utf-8"),
         "settings": dict(SETTINGS, STORE_HOST='db"example')},
    ]
    results = _builder_renders(tmp_path, cases)
    for case, result in zip(cases, results):
        assert "error" in result, result
        with pytest.raises(ValueError):
            manifest.render_retrieval_model(case["template"], case["settings"])


# ---------------------------------------------------------------------------
# the variables of the retrieval model, as each side registers them
# ---------------------------------------------------------------------------
_TS_FIELD = {
    "name": r"name: '([^']*)'", "role": r"role: '([^']*)'",
    "type": r"type: '([^']*)'", "length": r"length: (\d+)",
    "description": r"description: '([^']*)'",
}


def _ts_variables(name, source=None):
    """One of the Builder's two lists, as dicts. A field an entry does not
    have is simply not in its dict - that is the defect this looks for."""
    source = TS_BUILDER.read_text(encoding="utf-8") if source is None else source
    found = re.search(r"\nconst %s = \[\n(.*?)\n\];\n" % name, source, re.DOTALL)
    assert found, f"rag-builder.ts no longer declares {name} as a list"
    entries = re.findall(r"^  \{ (.*) \},$", found.group(1), re.MULTILINE)
    # every line of the list is an entry: nothing is skipped unread
    assert len(entries) == len(found.group(1).split("\n")), found.group(1)
    variables = []
    for entry in entries:
        variable = {}
        for field, pattern in _TS_FIELD.items():
            value = re.search(r"(?:^|, )" + pattern, entry)
            if value:
                variable[field] = (int(value.group(1)) if field == "length"
                                   else value.group(1))
        variables.append(variable)
    return variables


def _py_variables(role):
    source = (registration.RETRIEVAL_INPUTS if role == "input"
              else registration.RETRIEVAL_OUTPUTS)
    return [{"name": name, "role": role, "type": kind, "length": length,
             "description": text} for name, kind, length, text in source]


_SIDES = [("INPUT_VARIABLES", "input"), ("OUTPUT_VARIABLES", "output")]


@pytest.mark.parametrize("ts_name,role", _SIDES)
def test_both_sides_register_the_same_variables(ts_name, role):
    """Names, order, roles, types, lengths and descriptions: a model is the
    same model whichever of the two wrote its variables last."""
    assert _ts_variables(ts_name) == _py_variables(role)


@pytest.mark.parametrize("ts_name,role", _SIDES)
def test_every_variable_carries_a_length_on_both_sides(ts_name, role):
    """SAS Model Manager stores 99 for a string variable that comes without
    a length. The Builder's two context outputs came without one."""
    for side in (_ts_variables(ts_name), _py_variables(role)):
        assert side, role
        for variable in side:
            assert set(variable) == {"name", "role", "type", "length",
                                     "description"}, variable
            assert variable["role"] == role
            assert variable["type"] in ("string", "decimal"), variable
            assert isinstance(variable["length"], int), variable
            if variable["type"] == "decimal":
                assert variable["length"] == 8, variable
            else:
                assert variable["length"] >= 32, variable


def test_the_context_outputs_hold_what_a_retrieval_returns():
    """About 2,000 characters were measured for an envelope of three hits;
    100 is what the wrapper of the model had for it."""
    for side in (_ts_variables("OUTPUT_VARIABLES"), _py_variables("output")):
        lengths = {variable["name"]: variable["length"] for variable in side}
        assert lengths["context_dg"] == 32000
        assert lengths["context_envelope"] == 32000
        assert lengths["retrieval_status"] == 512


def test_the_variables_are_the_signature_of_the_model():
    """The model's own docstring and its `execute` name the same variables,
    in the same order."""
    template = TEMPLATE.read_text(encoding="utf-8")
    inputs = [variable["name"] for variable in _py_variables("input")]
    outputs = [variable["name"] for variable in _py_variables("output")]
    assert "def execute(%s):" % ", ".join(inputs) in template
    assert '    "Output: %s"' % ", ".join(outputs) in template


def test_the_builder_sends_the_two_lists_as_they_are_declared():
    """The lists reach SAS Model Manager unchanged: the save puts them into
    the model as `inputVariables` and `outputVariables`, and the model is
    sent back whole."""
    source = TS_BUILDER.read_text(encoding="utf-8")
    assert source.count("        inputVariables: INPUT_VARIABLES,\n"
                        "        outputVariables: OUTPUT_VARIABLES,\n") == 1
    assert len(re.findall(r"\bINPUT_VARIABLES\b", source)) == 2
    assert len(re.findall(r"\bOUTPUT_VARIABLES\b", source)) == 2


def test_the_reader_of_the_builder_list_sees_a_missing_length():
    """The check of the check: the entry as the Builder held it before."""
    before = (
        "\nconst OUTPUT_VARIABLES = [\n"
        "  { name: 'context_dg', role: 'output', type: 'string', "
        "description: 'Context datagrid' },\n"
        "  { name: 'run_time', role: 'output', type: 'decimal', "
        "description: 'Total seconds spent' },\n"
        "];\n")
    read = _ts_variables("OUTPUT_VARIABLES", before)
    assert [variable["name"] for variable in read] == ["context_dg", "run_time"]
    assert all("length" not in variable for variable in read)

