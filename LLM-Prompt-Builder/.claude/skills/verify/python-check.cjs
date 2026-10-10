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

function run(script, files) {
  const python = resolvePython();
  if (!python) return { ok: false, python: null, stdout: '', error: 'no Python interpreter found (set VERIFY_PYTHON)' };
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'pb-verify-'));
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
    });
    const ok = result.status === 0;
    const error = ok ? '' : String(result.stderr || result.error || `exit ${result.status}`).trim();
    return { ok, python: `${python.command} ${python.args.join(' ')}`.trim() + ` (${python.version})`, stdout: String(result.stdout || ''), error };
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
// return statement without a network or the requests package.
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
 * request the code made ({ url, body }), empty when it made none.
 */
function callScoreModel(code, args, response = {}, name = 'score.py') {
  const result = run(CALL_SCRIPT, { [name]: code, 'call.json': JSON.stringify({ args, response }) });
  if (!result.ok) return { ok: false, python: result.python, error: result.error, returned: [], request: {} };
  try {
    return { ok: true, python: result.python, error: '', ...JSON.parse(result.stdout) };
  } catch (e) {
    return { ok: false, python: result.python, error: `unreadable output: ${result.stdout.slice(0, 200)}`, returned: [], request: {} };
  }
}

module.exports = { resolvePython, compilePython, callScoreModel };
