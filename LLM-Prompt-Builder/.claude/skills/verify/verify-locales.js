/**
 * Checks the two locale files against each other and against the sources:
 * English and German hold the same keys, every text is filled in, a
 * placeholder the code replaces is present in both languages, every key is
 * read by some source file, and German addresses the reader formally.
 *
 * It needs no mock, no browser and no copy: `node verify-locales.js` runs it
 * where it lies, as a module or as CommonJS (the only import is a dynamic
 * one). A copy elsewhere takes the project folder as its argument:
 * `node verify-locales.cjs <path-to>/LLM-Prompt-Builder`.
 */
// Texts whose braces are an example for the reader, not a placeholder the code replaces.
const SAMPLE_BRACES = [
  'promptBuilderDecisionExplainer', 'promptBuilderDecisionStatePlaceholder',
  'promptBuilderManifestNoInputsWarning', 'promptBuilderManifestDecisionNoInputsWarning',
];
// Keys assembled at run time: `<prefix>${...}` in the sources.
const KEY_TEMPLATE = /[`'"]((?:promptBuilder|ragBuilder)[A-Za-z0-9_]*)\$\{/g;
// The Prompt Builder texts that address the reader with "du". Every other
// German text is held to the formal address; a text leaves this list when it
// is reworded.
const INFORMAL_ADDRESS = [
  'promptBuilderDescription', 'promptBuilderProjectInstructions', 'promptExperimentLegendBest', 'promptBuilderPromptingHeader',
  'promptBulderPromptingExplainer', 'promptExperimentSelectModelsAlert', 'promptExperimentSaveModelsPromptAlert',
  'promptExperimentSaveModelsExperimentAlert', 'promptExperimentSaveFailureResponse', 'promptBuilderManifestNoOutputs',
  'promptBuilderDeleteFailedResponse', 'promptBuilderJudgeSectionDescription', 'promptBuilderOptimizeSectionDescription',
  'promptBuilderOptimizeDatasetTrackerInfo', 'promptBuilderOptimizeCompareIntro', 'promptBuilderOptimizeDatasetInfo',
  'promptBuilderOptimizeOptimizerInfo',
];
const INFORMAL = /(^|[^A-Za-zÄÖÜäöüß])(du|dich|dir|dein|deine[mnrs]?|euch|euer|eure[mnrs]?)(?![A-Za-zÄÖÜäöüß])/i;

Promise.all([import('node:fs'), import('node:path')]).then(([fs, path]) => {
  const project = path.resolve(process.argv[2] || path.join(path.dirname(process.argv[1]), '..', '..', '..'));
  const read = (locale) => JSON.parse(fs.readFileSync(path.join(project, 'src', 'i18n', 'locales', `${locale}.json`), 'utf8'));
  // "section.key" (and "section.key.inner" for the texts of a dialog) -> text
  const flatten = (value, name, into) => {
    if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
      for (const [key, inner] of Object.entries(value)) flatten(inner, name ? `${name}.${key}` : key, into);
    } else into.set(name, value);
    return into;
  };
  const en = flatten(read('en'), '', new Map());
  const de = flatten(read('de'), '', new Map());
  const sources = [];
  (function walk(folder) {
    for (const entry of fs.readdirSync(folder, { withFileTypes: true })) {
      const file = path.join(folder, entry.name);
      if (entry.isDirectory()) walk(file);
      else if (entry.name.endsWith('.ts')) sources.push(fs.readFileSync(file, 'utf8'));
    }
  })(path.join(project, 'src'));
  const code = sources.join('\n');
  const words = new Set(code.match(/[A-Za-z_][A-Za-z0-9_]*/g));
  const prefixes = [...code.matchAll(KEY_TEMPLATE)].map((match) => match[1]);
  const keyOf = (name) => name.split('.')[1];
  const placeholders = (text) => (String(text).match(/\{[A-Za-z][A-Za-z0-9_]*\}/g) || []).sort().join(' ');

  let failed = 0;
  let checks = 0;
  const check = (offenders, label) => {
    const ok = offenders.length === 0;
    checks += 1;
    if (!ok) failed += 1;
    console.log(`${ok ? 'OK ' : 'FAIL'}  ${label}${ok ? '' : ': ' + offenders.join(', ')}`);
  };
  const names = [...en.keys()];
  check(names.filter((name) => !de.has(name)), `German has every one of the ${names.length} English keys`);
  check([...de.keys()].filter((name) => !en.has(name)), 'German has no key that English lacks');
  check([...en, ...de].filter(([, text]) => typeof text !== 'string' || text.trim() === '').map(([name]) => name), 'every text is a string that says something');
  check(
    names.filter((name) => de.has(name) && !SAMPLE_BRACES.includes(keyOf(name)) && placeholders(en.get(name)) !== placeholders(de.get(name))),
    'both languages carry the same {placeholders}'
  );
  check(
    names.filter((name) => !words.has(keyOf(name)) && !prefixes.some((prefix) => keyOf(name).startsWith(prefix))),
    'every key is read by a source file'
  );
  check(
    [...de].filter(([name, text]) => INFORMAL.test(String(text)) !== INFORMAL_ADDRESS.includes(keyOf(name))).map(([name]) => name),
    `German addresses the reader formally outside the ${INFORMAL_ADDRESS.length} listed texts, and each of those still says "du"`
  );
  // Manifest of a decision template takes the most recent run ticked in the
  // box whose label is promptExperimentModelCorrect, whatever its answers. Every
  // text the sources choose for the decision side (`decision ? ...`,
  // `runIsDecision ? ...` or the second key of `manifestText(...)`) that
  // speaks of an experiment run names that box by its label, in both
  // languages.
  const DECISION_TEXT = /\b(?:decision|runIsDecision)\s*\?\s*\(?\s*promptBuilderInterfaceText\??\.([A-Za-z0-9_]+)/g;
  const MANIFEST_TEXT = /\bmanifestText\(\s*'[A-Za-z0-9_]+'\s*,\s*'([A-Za-z0-9_]+)'\s*\)/g;
  const decisionKeys = [...new Set([...code.matchAll(DECISION_TEXT), ...code.matchAll(MANIFEST_TEXT)].map((match) => match[1]))];
  // The owner named the box (2026-10-15): it documents the choice of the best
  // decision model, which is the one Manifest takes.
  const fullNameOf = (key) => names.find((name) => keyOf(name) === key);
  const BOX = { en: 'Best Decision Model', de: 'Bestes Entscheidungsmodell' };
  check(
    Object.entries(BOX)
      .filter(([locale, label]) => (locale === 'en' ? en : de).get(fullNameOf('promptExperimentModelCorrect')) !== label)
      .map(([locale, label]) => `${locale} "${(locale === 'en' ? en : de).get(fullNameOf('promptExperimentModelCorrect'))}" (not "${label}")`),
    `the box of a decision run reads "${BOX.en}" and "${BOX.de}"`
  );
  // No text of the decision side - the ones chosen for it above and every
  // key that names a decision - speaks of a best prompt or a best response:
  // that is the box of an LLM run.
  const BEST_OF_LLM = /\bbest(?:e[nmrs]?)?\s+(?:prompt|response|antwort)/i;
  const decisionSide = names.filter((name) => decisionKeys.includes(keyOf(name)) || /Decision/.test(keyOf(name)));
  check(
    [['en', en], ['de', de]].flatMap(([locale, texts]) =>
      decisionSide.filter((name) => BEST_OF_LLM.test(String(texts.get(name)))).map((name) => `${locale} ${keyOf(name)}`)
    ),
    `none of the ${decisionSide.length} texts of the decision side speaks of a best prompt or a best response`
  );
  const RUN = [['en', en, /experiment run/i], ['de', de, /Experimentlauf/]];
  const fullName = (key) => names.find((name) => keyOf(name) === key);
  const naming = [];
  const unnamed = [];
  for (const [locale, texts, run] of RUN) {
    const label = String(texts.get(fullName('promptExperimentModelCorrect')));
    for (const key of decisionKeys) {
      const text = String(texts.get(fullName(key)) ?? '');
      if (!run.test(text)) continue;
      naming.push(`${locale} ${key}`);
      if (!text.includes(label)) unnamed.push(`${locale} ${key} (no "${label}")`);
    }
  }
  check(
    naming.length === 0 ? ['no text of the decision side speaks of an experiment run'] : unnamed,
    `each of the ${decisionKeys.length} texts the sources choose for a decision template that speaks of an experiment run (${naming.join(', ')}) names the box Manifest reads by its label`
  );
  console.log(`${checks - failed} checks passed, ${failed} failed`);
  process.exitCode = failed > 0 ? 1 : 0;
});
