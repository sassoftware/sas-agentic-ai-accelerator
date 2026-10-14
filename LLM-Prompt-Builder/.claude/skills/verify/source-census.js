/**
 * Reading the sources of the page for the censuses of the suites: the calls a
 * class of defects can live in are enumerated from the code, so a call that
 * is added, moved or gone fails a check until the suite names it.
 *
 * The sources are parsed with the parser of rolldown from the node_modules of
 * the project (`npm ci` in LLM-Prompt-Builder first). PROJECT is
 * VERIFY_PROJECT, or the project around this folder.
 *
 * Not a suite: run-all.cjs copies it beside the suites as source-census.cjs,
 * and they require it from there.
 */
const fs = require('fs');
const path = require('path');
const { createRequire } = require('module');

const PROJECT = process.env.VERIFY_PROJECT || path.resolve(__dirname, '..', '..', '..');

/** The program of a source file, with a helper that gives the text of a node. */
function parse(file, text) {
  const projectRequire = createRequire(path.join(PROJECT, 'package.json'));
  const { parseSync } = projectRequire('rolldown/utils');
  const { program, errors } = parseSync(file, text, { lang: 'ts' });
  if (errors.length) throw new Error(`${file} does not parse: ${errors[0].message}`);
  return { program, source: (node) => text.slice(node.start, node.end) };
}

const isFunction = (node) => node && /^(FunctionExpression|ArrowFunctionExpression)$/.test(node.type);

/**
 * Walk a program; `visit(node, name, parents, outer)` is called for every
 * node, with the name of the named function it stands in (a declaration, a
 * variable or an assignment that holds a function, a property or method, the
 * handler of addEventListener as "<object>:<event>" - for a call of
 * addEventListener that is the call itself), the chain of its parents, and
 * the name of the function around the node itself.
 */
function walk(program, source, visit) {
  const nameOf = (node) => {
    if (node.type === 'FunctionDeclaration' && node.id) return node.id.name;
    if (node.type === 'VariableDeclarator' && isFunction(node.init) && node.id.type === 'Identifier') return node.id.name;
    if (node.type === 'AssignmentExpression' && isFunction(node.right)) return source(node.left);
    if ((node.type === 'Property' || node.type === 'MethodDefinition') && isFunction(node.value) && node.key.type === 'Identifier') return node.key.name;
    if (
      node.type === 'CallExpression' && node.callee.type === 'MemberExpression' && node.callee.property.name === 'addEventListener' &&
      node.arguments[0] && node.arguments[0].type === 'Literal' && isFunction(node.arguments[1])
    ) {
      return `${source(node.callee.object)}:${node.arguments[0].value}`;
    }
    return null;
  };
  const step = (node, name, parents) => {
    if (!node || typeof node.type !== 'string') return;
    const here = nameOf(node) || name;
    visit(node, here, parents, name);
    const below = [...parents, node];
    for (const [key, value] of Object.entries(node)) {
      if (key === 'parent') continue;
      if (Array.isArray(value)) value.forEach((child) => step(child, here, below));
      else if (value && typeof value === 'object') step(value, here, below);
    }
  };
  step(program, null, []);
}

/** The calls of the functions `names` in a source, as "<function> -> <callee>" with a count. */
function callsInSource(file, text, names) {
  const { program, source } = parse(file, text);
  const counts = {};
  walk(program, source, (node, name) => {
    if (node.type === 'CallExpression' && node.callee.type === 'Identifier' && names.includes(node.callee.name)) {
      const key = `${name || '(module)'} -> ${node.callee.name}`;
      counts[key] = (counts[key] || 0) + 1;
    }
  });
  return counts;
}

/** Every .ts file under src of the project, by its path below src (forward slashes). */
function sourceFiles() {
  const root = path.join(PROJECT, 'src');
  const files = {};
  (function into(folder) {
    for (const entry of fs.readdirSync(folder, { withFileTypes: true })) {
      const file = path.join(folder, entry.name);
      if (entry.isDirectory()) into(file);
      else if (entry.name.endsWith('.ts')) files[path.relative(root, file).split(path.sep).join('/')] = fs.readFileSync(file, 'utf8');
    }
  })(root);
  return files;
}

module.exports = { PROJECT, parse, walk, callsInSource, sourceFiles };
