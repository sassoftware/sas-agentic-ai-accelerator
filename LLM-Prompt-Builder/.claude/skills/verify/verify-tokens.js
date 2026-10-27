/**
 * Scans the built page for text SAS Job Execution would take for a token.
 *
 * The page is stored as the form of a job definition, and SAS Job Execution
 * substitutes tokens in a form before it serves it: text between two `$`
 * characters on one line that reads like a name (`$name$`) is replaced by the
 * value of that parameter - by nothing when there is none. Measured on a live
 * system: such text is gone from the served page. Inside code that cuts the
 * code; the script and the stylesheet are safe because the build stores them
 * gzipped and base64-encoded (no `$` in that alphabet), so what is left to
 * look at is the HTML and the small loader that unpacks them.
 *
 * It needs no mock, no browser and no copy: `node verify-tokens.js` runs it
 * where it lies, as a module or as CommonJS (the only import is a dynamic
 * one). A copy elsewhere takes the project folder as its argument, and
 * VERIFY_DIST names another bundle: `node verify-tokens.cjs <path-to>/LLM-Prompt-Builder`.
 *
 * A match inside a comment is reported as a NOTE and does not fail: losing
 * the text between the two `$` leaves a comment a comment. Every other match
 * fails - it is text of the page or of a style rule that would be cut.
 */
const TOKEN = /\$[A-Za-z_][A-Za-z0-9_]*\$/g;

Promise.all([import('node:fs'), import('node:path')]).then(([fs, path]) => {
  const project = path.resolve(process.argv[2] || path.join(path.dirname(process.argv[1]), '..', '..', '..'));
  const file = path.resolve(process.env.VERIFY_DIST || path.join(project, 'dist', 'index.html'));
  const page = fs.readFileSync(file, 'utf8');

  // Where the comments are: <!-- --> in the HTML, /* */ in the stylesheet.
  // Offsets into the whole file, so a token is placed whatever line it is on.
  const comments = [];
  for (const match of page.matchAll(/<!--[\s\S]*?-->|\/\*[\s\S]*?\*\//g)) comments.push([match.index, match.index + match[0].length]);
  const inComment = (start, end) => comments.some(([from, to]) => start >= from && end <= to);

  const found = [];
  let offset = 0;
  page.split('\n').forEach((line, index) => {
    for (const match of line.matchAll(TOKEN)) {
      const start = offset + match.index;
      found.push({
        token: match[0],
        line: index + 1,
        column: match.index + 1,
        comment: inComment(start, start + match[0].length),
        context: line.slice(Math.max(0, match.index - 30), match.index + match[0].length + 30),
      });
    }
    offset += line.length + 1;
  });

  const dollars = (page.match(/\$/g) || []).length;
  // Every inline script is either packed (gzip + base64, of a type no browser
  // runs) or the loader, which unpacks the packed ones and is plain text.
  const inline = [...page.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/gi)].filter(
    ([, attrs, body]) => body.trim() && !/\bsrc\s*=/i.test(attrs)
  );
  const packed = inline.filter(([, attrs]) => attrs.includes('type="text/x-pb-gzip"'));
  const loaders = inline.filter(([, attrs, body]) => !attrs.trim() && body.includes('DecompressionStream'));
  const harmful = found.filter((entry) => !entry.comment);
  let failed = 0;
  const check = (ok, label) => {
    if (!ok) failed += 1;
    console.log(`${ok ? 'OK ' : 'FAIL'}  ${label}`);
  };
  check(
    packed.length >= 2 &&
      packed.every(([, , body]) => /^[A-Za-z0-9+/=]+$/.test(body)) &&
      loaders.length === 1 &&
      packed.length + loaders.length === inline.length,
    `the script and the stylesheet of ${path.basename(file)} are packed (${packed.length}, base64 only) and the one other inline script is the loader (${loaders.length}), so no code text can hold a token`
  );
  check(
    harmful.length === 0,
    `no $name$ outside a comment (${dollars} "$" characters in the file)${harmful.length ? ': ' + harmful.map((entry) => `${entry.token} at ${entry.line}:${entry.column} in "${entry.context}"`).join(' ; ') : ''}`
  );
  found
    .filter((entry) => entry.comment)
    .forEach((entry) => console.log(`NOTE  ${entry.token} at ${entry.line}:${entry.column}, inside a comment ("${entry.context.trim()}"): SAS Job Execution removes it, the comment stays a comment`));
  console.log(`${2 - failed} checks passed, ${failed} failed`);
  process.exitCode = failed > 0 ? 1 : 0;
});
