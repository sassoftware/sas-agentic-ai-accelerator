/**
 * Every fixed-time wait of the harness, from the code (round 26).
 *
 * A suite that waits a fixed time for a state - or on a signal that can come
 * before it - gives two answers for one bundle on a loaded machine: a road
 * waited 30 s for a save that was still out, a click landed on the Save
 * button of a create dialog that was still moving in. The suites wait on the
 * thing itself instead (page-waits.js: a request answered, a dialog fully
 * shown or gone, a gate a held answer waits at until the suite has looked).
 *
 * This check reads every .js and .cjs file of this folder with the parser of
 * rolldown and finds every call of sleep, setTimeout and waitForTimeout. Each
 * has to be one of the kinds that guard no state:
 *   - the definition of a sleep (a setTimeout inside `new Promise`);
 *   - a poll: inside a for, while or do-while loop, which looks again until
 *     what it waits for is there;
 *   - an answer a route holds back for a time that is itself the condition a
 *     check runs under - named in HELD with why.
 * Any other one fails the check, and so does a wait on the `show` class of a
 * dialog (set when it starts to fade in, taken when it starts to fade out),
 * where dialogShown and dialogsGone wait on the dialog itself.
 *
 * It needs no mock and no browser: `node verify-waits.js` runs it where it
 * lies (`npm ci` in LLM-Prompt-Builder first, for the parser). A copy
 * elsewhere takes the project folder as its argument.
 */
/** The answers a route holds back for a fixed time, by "<file> <function>", with why. */
const HELD = {
  'verify.js slowViya':
    'every request to SAS Model Manager and the files service takes 300 ms, as a real SAS Viya easily does: the time is the condition the checks of an entry chosen while another loads run under, and they wait on what they look at',
};

Promise.all([import('node:fs'), import('node:path'), import('node:module')]).then(([fs, path, { createRequire }]) => {
  const PROJECT = path.resolve(process.argv[2] || path.join(path.dirname(process.argv[1]), '..', '..', '..'));
  const SKILL = path.join(PROJECT, '.claude', 'skills', 'verify');
  const results = [];
  function step(ok, label) {
    results.push(`${ok ? 'OK ' : 'FAIL'}  ${label}`);
    console.log(`${ok ? 'OK ' : 'FAIL'}  ${label}`);
    if (!ok) process.exitCode = 1;
  }

  let parseSync;
  try {
    ({ parseSync } = createRequire(path.join(PROJECT, 'package.json'))('rolldown/utils'));
  } catch (error) {
    step(false, `the parser of rolldown can be loaded from the project (${error.message}; run npm ci)`);
  }

  const WAITS = ['sleep', 'setTimeout', 'waitForTimeout'];
  const LOOPS = /^(ForStatement|WhileStatement|DoWhileStatement)$/;
  const FUNCTIONS = /^(FunctionExpression|ArrowFunctionExpression|FunctionDeclaration)$/;
  const found = { definition: [], poll: [], held: [], other: [] };
  const heldSeen = new Set();
  const showWaits = [];
  const files = parseSync ? fs.readdirSync(SKILL).filter((name) => /\.c?js$/.test(name)).sort() : [];
  for (const file of files) {
    const text = fs.readFileSync(path.join(SKILL, file), 'utf8');
    const { program, errors } = parseSync(file, text, { lang: 'js' });
    if (errors.length) {
      step(false, `${file} parses (${errors[0].message})`);
      continue;
    }
    const lineOf = (position) => text.slice(0, position).split('\n').length;
    // The name a function is known by: its declaration, the variable or the
    // property that holds it.
    const nameOf = (node, parent) => {
      if (node.type === 'FunctionDeclaration' && node.id) return node.id.name;
      if (parent && parent.type === 'VariableDeclarator' && parent.id.type === 'Identifier') return parent.id.name;
      if (parent && parent.type === 'AssignmentExpression') return text.slice(parent.left.start, parent.left.end);
      if (parent && parent.type === 'Property' && parent.key.type === 'Identifier') return parent.key.name;
      return null;
    };
    const visit = (node, parents) => {
      if (!node || typeof node.type !== 'string') return;
      if (node.type === 'CallExpression') {
        const callee = node.callee;
        const name = callee.type === 'Identifier' ? callee.name : callee.type === 'MemberExpression' && !callee.computed ? callee.property.name : null;
        if (WAITS.includes(name)) {
          const where = `${file}:${lineOf(node.start)} ${text.slice(node.start, node.end).slice(0, 70)}`;
          let kind = 'other';
          for (let index = parents.length - 1; index >= 0; index--) {
            const parent = parents[index];
            if (name === 'setTimeout' && parent.type === 'NewExpression' && parent.callee.type === 'Identifier' && parent.callee.name === 'Promise') {
              kind = 'definition';
              break;
            }
            if (LOOPS.test(parent.type)) {
              kind = 'poll';
              break;
            }
            // The handler of a route: a function whose first parameter is the route.
            if (FUNCTIONS.test(parent.type) && parent.params[0] && parent.params[0].type === 'Identifier' && parent.params[0].name === 'route') {
              const handler = nameOf(parent, parents[index - 1]) || '(an anonymous route handler)';
              const key = `${file} ${handler}`;
              kind = HELD[key] ? 'held' : 'other';
              if (HELD[key]) heldSeen.add(key);
              break;
            }
          }
          found[kind].push(where);
        }
        // A wait on the show class of a dialog.
        if (name === 'waitForSelector' && node.arguments[0]) {
          const argument = text.slice(node.arguments[0].start, node.arguments[0].end);
          if (/\.show\b/.test(argument) || /'\.modal\.show'/.test(argument)) showWaits.push(`${file}:${lineOf(node.start)} ${text.slice(node.start, node.end).slice(0, 90)}`);
        }
      }
      const below = [...parents, node];
      for (const [key, value] of Object.entries(node)) {
        if (key === 'parent') continue;
        if (Array.isArray(value)) value.forEach((child) => visit(child, below));
        else if (value && typeof value === 'object') visit(value, below);
      }
    };
    visit(program, []);
  }

  const total = found.definition.length + found.poll.length + found.held.length + found.other.length;
  step(
    files.length > 0 && found.other.length === 0,
    `every fixed-time wait of the harness guards no state: the definition of a sleep, a poll, or an answer held back for a time that is a condition of its own (${files.length} files, ${total} waits: ${found.definition.length} definitions, ${found.poll.length} polls, ${found.held.length} held answers; any other: ${found.other.length}${found.other.length ? ` - ${found.other.join('; ')}` : ''})`
  );
  const unseen = Object.keys(HELD).filter((key) => !heldSeen.has(key));
  step(unseen.length === 0, `every held answer named here is in the harness (named and not found: ${unseen.join('; ') || 'none'})`);
  step(
    files.length > 0 && showWaits.length === 0,
    `no suite waits on the show class of a dialog - dialogShown and dialogsGone wait on the dialog itself (${showWaits.length ? showWaits.join('; ') : 'none found'})`
  );

  console.log('\n===== SUMMARY =====');
  console.log(results.join('\n'));
  console.log(`${results.filter((r) => r.startsWith('OK')).length} checks passed, ${results.filter((r) => r.startsWith('FAIL')).length} failed`);
});
