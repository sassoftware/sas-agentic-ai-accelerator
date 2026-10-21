/**
 * Checks the Python a manifest generates with a real interpreter: the suites
 * compile every score code they see, and can run its scoreModel() against a
 * stand-in for the `requests` package. String assertions alone cannot tell
 * valid Python from invalid Python.
 *
 * The interpreter is VERIFY_PYTHON when set (a command, optionally with
 * arguments: "py -3.11"), else the first of py -3.11, python3.11, python3,
 * python that answers a version probe. 3.11 comes first because it is what
 * SAS Viya's compute Python ships. The probe is what rejects the Windows
 * Store stub, which is on PATH as python3.exe and prints an advertisement.
 *
 * The code under test reads environment variables of the publishing
 * destination (LLMCONTAINERSSLVERIFY, RAGEMBED_CABUNDLE ...). Whatever the
 * shell that started the suite holds under those names is taken out first; a
 * check passes the ones it wants as `env`. In a value, `{folder}` stands for
 * the temporary folder of the call, so a check can name a file that exists
 * there (a CA bundle), and the same word comes back in what the code did with
 * it.
 */
const { spawnSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const VERSION_PROBE = 'import sys; print("%d.%d" % sys.version_info[:2])';

let resolved;

/** The interpreter as { command, args, version }, or null when none answers. */
function resolvePython() {
  if (resolved !== undefined) return resolved;
  const configured = (process.env.VERIFY_PYTHON || '').trim();
  const candidates = configured
    ? [configured.split(/\s+/)]
    : [['py', '-3.11'], ['python3.11'], ['python3'], ['python']];
  resolved = null;
  for (const [command, ...args] of candidates) {
    const probe = spawnSync(command, [...args, '-I', '-c', VERSION_PROBE], { encoding: 'utf8', timeout: 20000 });
    const version = probe.status === 0 ? String(probe.stdout || '').trim() : '';
    if (/^3\.\d+$/.test(version)) {
      resolved = { command, args, version };
      break;
    }
  }
  return resolved;
}

// The variables a manifested model reads on its destination.
const DESTINATION_VARIABLE = /^(LLMCONTAINER|RAGEMBED_|RAGSTORE_)/;

function run(script, files, env = {}) {
  const python = resolvePython();
  if (!python) return { ok: false, python: null, stdout: '', error: 'no Python interpreter found (set VERIFY_PYTHON)' };
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'pb-verify-'));
  const environment = Object.fromEntries(Object.entries(process.env).filter(([name]) => !DESTINATION_VARIABLE.test(name)));
  Object.entries(env).forEach(([name, value]) => {
    if (value !== null && value !== undefined) environment[name] = String(value).split('{folder}').join(folder);
  });
  try {
    const paths = Object.entries(files).map(([name, content]) => {
      const file = path.join(folder, name);
      fs.writeFileSync(file, content, 'utf8');
      return file;
    });
    // -I keeps the user's environment out of the check; -W error turns the
    // SyntaxWarning of an invalid escape sequence into a failure.
    const result = spawnSync(python.command, [...python.args, '-I', '-W', 'error', '-c', script, ...paths], {
      encoding: 'utf8',
      timeout: 30000,
      env: environment,
    });
    const ok = result.status === 0;
    const error = ok ? '' : String(result.stderr || result.error || `exit ${result.status}`).trim();
    // The folder is written the way JSON holds it (a backslash doubled) and as it is.
    const stdout = String(result.stdout || '').split(JSON.stringify(folder).slice(1, -1)).join('{folder}').split(folder).join('{folder}');
    return { ok, python: `${python.command} ${python.args.join(' ')}`.trim() + ` (${python.version})`, stdout, error };
  } finally {
    fs.rmSync(folder, { recursive: true, force: true });
  }
}

// compile() rather than py_compile: nothing is written beside the file. The
// `with` matters because -W error reports a file left open as a failure too.
const COMPILE_SCRIPT = [
  'import sys',
  'with open(sys.argv[1], encoding="utf-8") as source:',
  '    compile(source.read(), sys.argv[1], "exec")',
].join('\n');

/** Compile score code; returns { ok, python, error } - error is the interpreter's message. */
function compilePython(code, name = 'score.py') {
  const { ok, python, error } = run(COMPILE_SCRIPT, { [name]: code });
  return { ok, python, error };
}

// Runs scoreModel() with `requests` replaced by a stand-in that records the
// call and answers 200 with the given JSON, so the generated code runs to its
// return statement without a network or the requests package. Of the options
// of the call it keeps `verify`: False, True or the path of a CA bundle.
const CALL_SCRIPT = [
  'import sys, json, types',
  'with open(sys.argv[2], encoding="utf-8") as handle:',
  '    call = json.load(handle)',
  'request = {}',
  'class _Response:',
  '    status_code = 200',
  '    def json(self):',
  '        return call["response"]',
  'def _post(url, data=None, **options):',
  '    request["url"] = url',
  '    request["body"] = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)',
  '    request["verify"] = options.get("verify", "not passed")',
  '    return _Response()',
  'sys.modules["requests"] = types.SimpleNamespace(post=_post)',
  'namespace = {}',
  'with open(sys.argv[1], encoding="utf-8") as source:',
  '    exec(compile(source.read(), sys.argv[1], "exec"), namespace)',
  'returned = namespace["scoreModel"](*call["args"])',
  'returned = list(returned) if isinstance(returned, tuple) else [returned]',
  'sys.stdout.write(json.dumps({"returned": returned, "request": request}))',
].join('\n');

/**
 * Call the scoreModel() of a score code with the given positional arguments.
 * `response` is the JSON the stand-in container answers with. Returns
 * { ok, error, returned, request }: the returned tuple as an array and the
 * request the code made ({ url, body, verify }), empty when it made none.
 * `env` are the environment variables of the call; `files` are further files
 * of its folder, by name (see `{folder}` in the header).
 */
function callScoreModel(code, args, response = {}, name = 'score.py', env = {}, files = {}) {
  const result = run(CALL_SCRIPT, { [name]: code, 'call.json': JSON.stringify({ args, response }), ...files }, env);
  if (!result.ok) return { ok: false, python: result.python, error: result.error, returned: [], request: {} };
  try {
    return { ok: true, python: result.python, error: '', ...JSON.parse(result.stdout) };
  } catch (e) {
    return { ok: false, python: result.python, error: `unreadable output: ${result.stdout.slice(0, 200)}`, returned: [], request: {} };
  }
}

// Loads a module and evaluates expressions in it, one result per expression.
const EVALUATE_SCRIPT = [
  'import sys, json',
  'with open(sys.argv[2], encoding="utf-8") as handle:',
  '    expressions = json.load(handle)',
  'namespace = {"__name__": "module_under_test"}',
  'with open(sys.argv[1], encoding="utf-8") as source:',
  '    exec(compile(source.read(), sys.argv[1], "exec"), namespace)',
  'sys.stdout.write(json.dumps([eval(expression, namespace) for expression in expressions]))',
].join('\n');

/**
 * Load `code` as a module and evaluate `expressions` in it (the module must
 * import at its top level only what the interpreter ships). Returns
 * { ok, error, values }: one JSON value per expression. `env` and `files` as
 * for callScoreModel.
 */
function evaluateInModule(code, expressions, name = 'module.py', env = {}, files = {}) {
  const result = run(EVALUATE_SCRIPT, { [name]: code, 'expressions.json': JSON.stringify(expressions), ...files }, env);
  if (!result.ok) return { ok: false, python: result.python, error: result.error, values: [] };
  try {
    return { ok: true, python: result.python, error: '', values: JSON.parse(result.stdout) };
  } catch (e) {
    return { ok: false, python: result.python, error: `unreadable output: ${result.stdout.slice(0, 200)}`, values: [] };
  }
}

// ---------------------------------------------------------------------------
// TLS verification in a manifested score code (the prompt template with the
// LLM call included, and the decision template)
// ---------------------------------------------------------------------------

/**
 * The lines the option "Verify TLS certificates in manifested models" added
 * to both score codes, in the order they stand in: three comment lines and
 * the default after the old comment, two lines after the old environment
 * check. Everything else of a score code is what it was before the option.
 */
const tlsAddedLines = (verifyByDefault) => [
  '    # Setting it to true, yes or 1 enables it. When the variable is unset or',
  '    # holds another word, sslVerifyDefault decides: the Options pane setting',
  '    # "Verify TLS certificates in manifested models" when this was manifested.',
  `    sslVerifyDefault = ${verifyByDefault ? 'True' : 'False'}`,
  '    elif not sslVerifyDefault and os.getenv("LLMCONTAINERSSLVERIFY", "").strip().lower() not in ("true", "yes", "1"):',
  '        sslVerify = False',
];

/**
 * Take the added lines out of a score code. Returns { code, problem }: the
 * code as it was manifested before the option existed, and what is wrong
 * with the added lines ('' when they stand exactly once, in a row, where
 * they belong).
 */
function withoutTlsDefault(scoreCode, verifyByDefault) {
  const added = tlsAddedLines(verifyByDefault);
  const lines = scoreCode.split('\n');
  const head = lines.indexOf(added[0]);
  const tail = lines.indexOf(added[4]);
  const problems = [];
  if (head < 0 || added.slice(0, 4).some((line, offset) => lines[head + offset] !== line)) problems.push('the comment and the default line are not there as a block');
  if (tail < 0 || lines[tail + 1] !== added[5]) problems.push('the branch for a default of False is not there');
  if (lines.filter((line) => line.includes('sslVerifyDefault')).length !== 3) problems.push(`sslVerifyDefault is named on ${lines.filter((line) => line.includes('sslVerifyDefault')).length} lines, not 3`);
  if (head >= 0 && lines[head - 1] !== '    # LLMCONTAINERSSLVERIFY=false disables the verification entirely.') problems.push('the block does not follow the old comment');
  if (tail >= 0 && lines[tail - 1] !== '        sslVerify = False') problems.push('the branch does not follow the old environment check');
  if (problems.length) return { code: scoreCode, problem: problems.join('; ') };
  // The later lines first, so the earlier index stays what it was.
  lines.splice(tail, 2);
  lines.splice(head, 4);
  return { code: lines.join('\n'), problem: '' };
}

// The four values of LLMCONTAINERSSLVERIFY a check looks at: not set, off,
// on, and a word that is neither.
const TLS_WORDS = [null, 'false', 'true', 'garbage'];
const TLS_BUNDLE = '{folder}/ca-bundle.pem';

/**
 * What `verify` has to be for each of TLS_WORDS: the variable wins in both
 * directions, the manifest-time default decides the rest. Verification on
 * means the CA bundle when that file exists, else True.
 */
function expectedTlsVerify(verifyByDefault, bundleExists) {
  const on = bundleExists ? TLS_BUNDLE : true;
  return TLS_WORDS.map((word) => (word === 'false' ? false : word === 'true' ? on : verifyByDefault ? on : false));
}

/**
 * Call scoreModel() once per value of TLS_WORDS and return the `verify` its
 * requests.post got each time (or "error: ..." for a call that failed), with
 * LLMCONTAINERCABUNDLE naming a file that exists or one that does not.
 */
function tlsVerifyOf(scoreCode, args, response, bundleExists) {
  return TLS_WORDS.map((word) => {
    const called = callScoreModel(
      scoreCode,
      args,
      response,
      'tls_check.py',
      { LLMCONTAINERSSLVERIFY: word, LLMCONTAINERCABUNDLE: bundleExists ? TLS_BUNDLE : '{folder}/no-such-bundle.pem' },
      { 'ca-bundle.pem': 'a stand-in for a CA bundle\n' }
    );
    if (!called.ok) return `error: ${String(called.error).trim().split('\n').pop()}`;
    return called.request.verify;
  });
}

module.exports = {
  resolvePython,
  compilePython,
  callScoreModel,
  evaluateInModule,
  tlsAddedLines,
  withoutTlsDefault,
  expectedTlsVerify,
  tlsVerifyOf,
  TLS_WORDS,
};
