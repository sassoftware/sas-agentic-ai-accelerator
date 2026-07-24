/**
 * Minimal mock of the SAS Viya endpoints the LLM Prompt Builder calls, plus a
 * static route serving the built dist/index.html same-origin (the app defaults
 * viyaHost to window.location.origin). Every request is recorded and exposed
 * at GET /__log for assertions; POST /__reset clears the log.
 */
const http = require('http');
const fs = require('fs');

const DIST_HTML = process.argv[2];
const PORT = 4173;

const log = [];

function headerRow(runId, sys, user) {
  return {
    runId, systemPrompt: sys, userPrompt: user, model: '', options: '', response: '',
    run_time: null, prompt_length: null, output_length: null,
    best_prompt: null, fastest_prompt: null, fewest_tokens_prompt: null,
  };
}
function modelRow(runId, response, model = 'demo_llm', options = '{temperature:0.7}') {
  return {
    runId, systemPrompt: '', userPrompt: '', model,
    options, response,
    run_time: 1.2, prompt_length: 10, output_length: 20,
    best_prompt: 0, fastest_prompt: true, fewest_tokens_prompt: true,
  };
}
// runIds intentionally have a gap (1, 2, 5) — runs whose experiments all
// failed persist no rows, so real trackers contain gaps. The last run has two
// model results (one of them from an LLM that is no longer available) and a
// non-default temperature to prove option restoration on load.
const trackerRows = [
  headerRow(1, 'Sys 1', 'User 1'), modelRow(1, 'Response one'),
  headerRow(2, 'Sys 2', 'User 2'), modelRow(2, 'Response two'),
  headerRow(5, 'Sys 3', 'User 3'), modelRow(5, 'Response three', 'demo_llm', '{temperature:0.9,reasoning_effort:medium}'),
  modelRow(5, 'Response other', 'other_llm'),
];
// Run 2 carries a selected best response so prompt selection auto-loads it.
trackerRows[3].best_prompt = 1;
// Run 5 (the two-model run) carries a completed judge verdict + config so the
// reload path (banner reasoning, ranks, restored judge controls) is exercised.
trackerRows[4].judge_model = 'demo_llm';
trackerRows[4].judge_confidence = 'high';
trackerRows[4].judge_reasoning = 'demo_llm answered more precisely than other_llm.';
trackerRows[4].judge_include_self = 1;
trackerRows[4].judge_auto = 1;
trackerRows[5].judge_rank = 1;
trackerRows[5].judge_best = 1;
trackerRows[6].judge_rank = 2;
trackerRows[6].judge_best = 0;

// model-used: two distinct dependent decision flows, one of them reported
// twice (flow + revision) to exercise the dedupe. model-free: no dependents.
// model-err: the relationships query itself fails (fail-open path).
const relationshipsFor = {
  'model-used': {
    items: [
      { type: 'Parent', resourceUri: '/modelRepository/projects/proj-1' },
      { type: 'Associated', resourceUri: '/folders/folders/f-1' },
      { type: 'Dependent', resourceUri: '/decisions/flows/flow-A' },
      { type: 'Dependent', resourceUri: '/decisions/flows/flow-A/revisions/rev-1' },
      { type: 'Dependent', resourceUri: '/decisions/flows/flow-B' },
    ],
  },
  'model-free': {
    items: [
      { type: 'Parent', resourceUri: '/modelRepository/projects/proj-1' },
      { type: 'Associated', resourceUri: '/folders/folders/f-1' },
    ],
  },
};

// --- Prompt optimization (Phase 3) -----------------------------------------
// The Optimize flow resolves the job definition path, launches a Job Execution
// job and polls it. The mock job runs for one poll cycle, then completes; on
// completion the source model gains a Prompt-Optimization-Tracker.json whose
// entry the app renders (metrics, produced model link, load button).
let jobPolls = 0;
let jobLaunched = false;
// POST /__failjob arms a failure: the NEXT launched job fails after one poll,
// logging the job's dependency-preflight error (missing dspy) and appending a
// failed tracker entry — mirroring the shipped job's failure path.
let failNextJob = false;
let failJobPolls = 0;
// The Builder can delete optimization runs: it removes the tracker content
// and re-uploads the remaining entries, which the mock mirrors statefully.
let optTrackerDeleted = false;
function multipartJson(rawBody) {
  const m = rawBody.match(/\r\n\r\n([\s\S]*?)\r\n--/);
  try {
    return m ? JSON.parse(m[1]) : null;
  } catch {
    return null;
  }
}
const DSPY_MISSING_ERROR =
  'The Python environment of this compute context lacks the dspy package - install the packages in Prompt-Optimization/requirements.txt into that Python environment, or point the computeContext Option at a prepared context.';
const optimizedPrompt = {
  systemPrompt: 'Optimised system prompt.\n\nFollow the pattern of these examples:\n\nuserPrompt: User 2\nresponse: Response two',
  userPrompt: 'User 2',
  variables: [],
  demos: [{ userPrompt: 'User 2', response: 'Response two' }],
};
// Seeded with one LEGACY entry (earlier releases created a separate
// prompt-test, recorded as producedPromptModelId) so the history view's
// backward compatibility is exercised; runs append new-shape entries.
const optimizationTracker = [
  {
    optimizationId: 1,
    startedAt: '2026-07-20T09:00:00Z',
    finishedAt: '2026-07-20T09:05:00Z',
    status: 'succeeded',
    jobId: 'job-legacy',
    targetModel: 'demo_llm',
    datasetSource: 'tracker',
    sampleCount: 1,
    optimizer: 'bootstrap',
    metric: 'exact',
    metricBefore: 0.4,
    metricAfter: 0.6,
    optimizedPrompt,
    producedPromptModelId: 'model-opt-legacy',
    datasetSnapshot: 'Prompt-Optimization-Dataset-1.json',
    error: null,
  },
];
const successEntry = {
  optimizationId: 2,
  startedAt: '2026-07-23T09:00:00Z',
  finishedAt: '2026-07-23T09:05:00Z',
  status: 'succeeded',
  jobId: 'job-1',
  targetModel: 'demo_llm',
  datasetSource: 'tracker',
  datasetRef: 'Prompt-Experiment-Tracker.json',
  sampleCount: 1,
  optimizer: 'bootstrap',
  metric: 'overlap',
  judgeModel: null,
  metricBefore: 0.5,
  metricAfter: 0.833,
  baselinePrompt: { systemPrompt: 'Sys 2', userPrompt: 'User 2' },
  trainSize: 7,
  validationSize: 3,
  // Overlap-metric entry: per-example partial-credit scores accompany the
  // correctness flags (the UI shows the score next to ✓/✗ when it is not 0/1).
  evaluations: [
    { inputs: { userPrompt: 'User 1' }, expected: 'Response one', baselineResponse: 'wrong', baselineCorrect: false, baselineScore: 0, optimizedResponse: 'Response one', optimizedCorrect: true, optimizedScore: 1 },
    { inputs: { userPrompt: 'User 2' }, expected: 'Response two', baselineResponse: 'Response two', baselineCorrect: true, baselineScore: 1, optimizedResponse: 'Response two', optimizedCorrect: true, optimizedScore: 1 },
    { inputs: { userPrompt: 'User 3' }, expected: 'Response three', baselineResponse: 'nope', baselineCorrect: false, baselineScore: 0.25, optimizedResponse: 'still nope', optimizedCorrect: false, optimizedScore: 0.5 },
  ],
  optimizedPrompt,
  datasetSnapshot: 'Prompt-Optimization-Dataset-2.json',
  // Per-role call accounting the job records (the Builder shows calls +
  // token totals and prices them when the model carries cost attributes).
  usage: {
    target: { calls: 13, promptTokens: 5200, outputTokens: 640, runTime: 38.5 },
    judge: { calls: 0, promptTokens: 0, outputTokens: 0, runTime: 0 },
  },
  error: null,
};

function json(res, status, body) {
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(body));
}

http
  .createServer((req, res) => {
    let raw = '';
    req.on('data', (c) => (raw += c));
    req.on('end', () => {
      const u = new URL(req.url, 'http://localhost');
      const p = u.pathname;
      if (p !== '/__log' && p !== '/__reset') {
        log.push({ method: req.method, url: req.url, body: raw, ifMatch: req.headers['if-match'] || null });
      }

      if (p === '/__log') return json(res, 200, log);
      if (p === '/__reset') { log.length = 0; return json(res, 200, {}); }
      if (p === '/__failjob') { failNextJob = true; return json(res, 200, {}); }

      if (p === '/' || p === '/index.html') {
        res.writeHead(200, { 'Content-Type': 'text/html' });
        return res.end(fs.readFileSync(DIST_HTML));
      }
      if (p === '/identities/users/@currentUser') {
        return json(res, 200, { id: 'tester', name: 'Tester' });
      }
      if (p === '/modelRepository/projects') {
        return json(res, 200, {
          items: [{ id: 'proj-1', name: 'Demo Project', createdBy: 'anna', modifiedBy: 'anna' }],
        });
      }
      if (p === '/modelRepository/projects/llm-proj/models') {
        const filter = u.searchParams.get('filter') || '';
        if (filter.includes('deprecated')) return json(res, 200, { items: [] });
        return json(res, 200, {
          items: [
            { id: 'llm-1', name: 'demo_llm' },
            { id: 'llm-2', name: 'second_llm' },
            { id: 'llm-3', name: 'contrarian_judge' },
          ],
        });
      }
      if (p === '/modelRepository/projects/proj-1/models') {
        return json(res, 200, {
          items: [
            { id: 'model-used', name: 'Used Prompt', createdBy: 'anna', modifiedBy: 'ben' },
            { id: 'model-free', name: 'score_metric_answer_relevancy', createdBy: 'ben', modifiedBy: 'ben' },
            { id: 'model-err', name: 'Error Prompt', createdBy: 'carla', modifiedBy: 'anna' },
          ],
        });
      }
      if (p === '/modelRepository/models/llm-1/contents') {
        return json(res, 200, {
          items: [{ id: 'c-opt', name: 'options.json', fileUri: '/files/files/opt-1' }],
        });
      }
      if (p === '/modelRepository/models/llm-2/contents') {
        return json(res, 200, {
          items: [{ id: 'c-opt2', name: 'options.json', fileUri: '/files/files/opt-2' }],
        });
      }
      if (p === '/modelRepository/models/llm-3/contents') {
        return json(res, 200, {
          items: [{ id: 'c-opt3', name: 'options.json', fileUri: '/files/files/opt-3' }],
        });
      }
      if (p === '/files/files/opt-1/content') {
        return json(res, 200, { temperature: { default: 0.7 } });
      }
      if (p === '/files/files/opt-2/content') {
        return json(res, 200, { temperature: { default: 0.5 } });
      }
      if (p === '/files/files/opt-3/content') {
        return json(res, 200, { temperature: { default: 0.3 } });
      }
      if (p === '/modelRepository/models/model-used/contents' && req.method === 'GET') {
        // The requirements.json entry simulates a leftover from an earlier
        // integrated-call manifest: a manifest without the LLM call must
        // remove it. The optimization tracker is present from the start (the
        // prompt has a legacy run) and grows as mock jobs finish.
        const items = [
          { id: 'c-trk', name: 'Prompt-Experiment-Tracker.json', fileUri: '/files/files/trk-1' },
          { id: 'c-req', name: 'requirements.json', fileUri: '/files/files/req-1' },
        ];
        if (!optTrackerDeleted) {
          items.push({ id: 'c-opttrk', name: 'Prompt-Optimization-Tracker.json', fileUri: '/files/files/opttrk-1' });
        }
        return json(res, 200, { items });
      }
      // @item resolves FOLDERS only — it 404s for jobDefinition members on a
      // real SAS Viya, so the app resolves the parent folder, then finds the
      // definition among the folder's members by name.
      if (p === '/folders/folders/@item') {
        const path = u.searchParams.get('path') || '';
        if (path === '/Public/Jobs') {
          return json(res, 200, { id: 'folder-jobs', uri: '/folders/folders/folder-jobs' });
        }
        return json(res, 404, { message: 'no such content item: ' + path });
      }
      if (p === '/folders/folders/folder-jobs/members') {
        const filter = u.searchParams.get('filter') || '';
        const items = filter.includes("'Optimize-Prompt-DSPy'")
          ? [{ name: 'Optimize-Prompt-DSPy', uri: '/jobDefinitions/definitions/jobdef-1', contentType: 'jobDefinition' }]
          : [];
        return json(res, 200, { items });
      }
      // CAS Management: the optimize panel builds its server → caslib → table
      // dropdowns from these listings, then probes the chosen table (info +
      // columns) before launching a run with the CAS dataset source. GHOST is
      // listed but its probe 404s — a stale listing (table dropped between
      // browse and launch).
      if (p === '/casManagement/servers') {
        return json(res, 200, { items: [{ name: 'cas-shared-default' }] });
      }
      if (p === '/casManagement/servers/cas-shared-default/caslibs') {
        return json(res, 200, { items: [{ name: 'Public' }, { name: 'casuser' }] });
      }
      if (p === '/casManagement/servers/cas-shared-default/caslibs/Public/tables') {
        return json(res, 200, { items: [{ name: 'OPT_DATA' }, { name: 'BAD_COLS' }, { name: 'GHOST' }] });
      }
      if (p === '/casManagement/servers/cas-shared-default/caslibs/casuser/tables') {
        return json(res, 200, { items: [] });
      }
      if (p === '/casManagement/servers/cas-shared-default/caslibs/Public/tables/OPT_DATA') {
        return json(res, 200, { name: 'OPT_DATA', rowCount: 5 });
      }
      if (p === '/casManagement/servers/cas-shared-default/caslibs/Public/tables/OPT_DATA/columns') {
        return json(res, 200, { items: [{ name: 'userPrompt' }, { name: 'response' }] });
      }
      if (p === '/casManagement/servers/cas-shared-default/caslibs/Public/tables/BAD_COLS') {
        return json(res, 200, { name: 'BAD_COLS', rowCount: 5 });
      }
      if (p === '/casManagement/servers/cas-shared-default/caslibs/Public/tables/BAD_COLS/columns') {
        return json(res, 200, { items: [{ name: 'question' }, { name: 'answer' }] });
      }
      if (p.startsWith('/casManagement/')) {
        return json(res, 404, { message: 'table not found' });
      }
      // The app primes the Job Execution service session with a GET before its
      // first POST (a first-contact POST 449s on a real Viya's SSO handshake).
      if (p === '/jobExecution/jobs' && req.method === 'GET') {
        return json(res, 200, { items: [] });
      }
      if (p === '/jobExecution/jobs' && req.method === 'POST') {
        if (failNextJob) {
          failNextJob = false;
          failJobPolls = 0;
          return json(res, 201, { id: 'job-fail', state: 'pending' });
        }
        jobLaunched = true;
        jobPolls = 0;
        return json(res, 201, { id: 'job-1', state: 'pending' });
      }
      if (p === '/jobExecution/jobs/job-1' && req.method === 'GET') {
        jobPolls += 1;
        // Real JES jobs expose the compute session/job ids in `results`; the
        // session's log streams LIVE while running (the Files logLocation only
        // fills in at completion).
        const computeResults = { COMPUTE_SESSION: 'cs-1 (SAS Job Execution compute context)', COMPUTE_JOB: 'cj-1' };
        if (jobPolls < 4) return json(res, 200, { id: 'job-1', state: 'running', results: computeResults });
        // The run's tracker entry appears exactly once, like the real job writes it.
        if (!optimizationTracker.some((entry) => entry.jobId === 'job-1')) {
          optimizationTracker.push(successEntry);
        }
        return json(res, 200, { id: 'job-1', state: 'completed', logLocation: '/files/files/joblog-1', results: computeResults });
      }
      if (p === '/compute/sessions/cs-1/jobs/cj-1/log/content') {
        // Live only while the job runs; the session is deleted afterwards, so
        // the app must fall back to the logLocation file. Plain-text CRLF
        // shape as returned by a real compute server (source echo + NOTE per
        // milestone, growing as the job progresses).
        if (jobPolls >= 4) return json(res, 404, { message: 'session not found' });
        res.writeHead(200, { 'Content-Type': 'text/plain; charset=utf-8' });
        const liveLines = [
          '663  %put NOTE: Python-Subprocess - Loading the experiment tracker dataset;',
          'NOTE: Python-Subprocess - Loading the experiment tracker dataset',
          '664  %put NOTE: Python-Subprocess - Dataset loaded (1 examples);',
          'NOTE: Python-Subprocess - Dataset loaded (1 examples)',
          '666  %put NOTE: Python-Subprocess - Scoring the baseline prompt;',
          'NOTE: Python-Subprocess - Scoring the baseline prompt',
        ];
        return res.end(liveLines.slice(0, Math.min(liveLines.length, jobPolls * 2 + 2)).join('\r\n'));
      }
      if (p === '/jobExecution/jobs/job-fail' && req.method === 'GET') {
        failJobPolls += 1;
        if (failJobPolls < 2) return json(res, 200, { id: 'job-fail', state: 'running' });
        // The failed run's tracker entry appears exactly once, like the real job writes it.
        if (!optimizationTracker.some((entry) => entry.jobId === 'job-fail')) {
          optimizationTracker.push({
            optimizationId: optimizationTracker.length + 1,
            startedAt: '2026-07-23T10:00:00Z',
            finishedAt: '2026-07-23T10:00:30Z',
            status: 'failed',
            jobId: 'job-fail',
            targetModel: 'demo_llm',
            datasetSource: 'tracker',
            sampleCount: 0,
            optimizer: 'bootstrap',
            metric: 'exact',
            metricBefore: null,
            metricAfter: null,
            optimizedPrompt: null,
            producedPromptModelId: null,
            error: DSPY_MISSING_ERROR,
          });
        }
        return json(res, 200, {
          id: 'job-fail',
          state: 'failed',
          logLocation: '/files/files/joblog-fail',
          error: { message: 'The job request failed.' },
        });
      }
      if (p === '/files/files/joblog-fail/content') {
        return json(res, 200, {
          version: 2,
          name: 'items',
          accept: 'application/vnd.sas.compute.log.line',
          items: [
            { version: 1, type: 'source', line: '597  %put NOTE: Python-Subprocess - Optimization failed: ' + DSPY_MISSING_ERROR + ';' },
            { version: 1, type: 'note', line: 'NOTE: Python-Subprocess - Optimization failed: ' + DSPY_MISSING_ERROR },
            { version: 1, type: 'error', line: 'ERROR: Prompt optimization failed: ' + DSPY_MISSING_ERROR },
          ],
        });
      }
      if (p === '/files/files/joblog-1/content') {
        // Real shape (verified live): a vnd.sas.compute.log.line collection —
        // one JSON document with items[].line, each milestone preceded by its
        // `%put` source-echo line, which the app must NOT show twice.
        return json(res, 200, {
          version: 2,
          name: 'items',
          accept: 'application/vnd.sas.compute.log.line',
          items: [
            { version: 1, type: 'note', line: 'NOTE: PROC PYTHON started.' },
            { version: 1, type: 'source', line: '597  %put NOTE: Python-Subprocess - Dataset loaded (1 examples);' },
            { version: 1, type: 'note', line: 'NOTE: Python-Subprocess - Dataset loaded (1 examples)' },
            { version: 1, type: 'source', line: '601  %put NOTE: Python-Subprocess - Baseline metric: 0.500;' },
            { version: 1, type: 'note', line: 'NOTE: Python-Subprocess - Baseline metric: 0.500' },
            { version: 1, type: 'source', line: '607  %put NOTE: Python-Subprocess - Done - metric 0.500 -> 0.833, run recorded on the prompt;' },
            { version: 1, type: 'note', line: 'NOTE: Python-Subprocess - Done - metric 0.500 -> 0.833, run recorded on the prompt' },
            { version: 1, type: 'note', line: 'NOTE: PROC PYTHON ended.' },
          ],
        });
      }
      if (p === '/files/files/opttrk-1/content') return json(res, 200, optimizationTracker);
      if (/^\/modelRepository\/models\/(model-free|model-err)\/contents$/.test(p) && req.method === 'GET') {
        return json(res, 200, { items: [] });
      }
      if (p === '/files/files/trk-1/content') return json(res, 200, trackerRows);
      if (p === '/relationships/relationships' && req.method === 'POST') {
        let modelId = '';
        try {
          modelId = (JSON.parse(raw).resourceURI[0] || '').split('/').pop();
        } catch {}
        if (modelId === 'model-err') return json(res, 500, { message: 'boom' });
        return json(res, 200, relationshipsFor[modelId] || { items: [] });
      }
      if (p === '/decisions/flows/flow-A') return json(res, 200, { id: 'flow-A', name: 'Loan Approval Decision' });
      if (p === '/decisions/flows/flow-B') return json(res, 200, { id: 'flow-B', name: 'Fraud Check Decision' });
      if (/^\/scr\//.test(p) && req.method === 'POST') {
        // Detect the LLM-as-a-Judge call by its distinctive system prompt.
        let inputs = [];
        try { inputs = JSON.parse(raw).inputs || []; } catch {}
        const sys = inputs.find((i) => i.name === 'systemPrompt');
        const sysText = sys ? String(sys.value) : '';
        const isChairman = sysText.includes('chairman of a panel');
        const isJudge = sysText.includes('impartial evaluator') || isChairman;
        if (isChairman) {
          // Break the tie deterministically: pick the tied response whose text
          // sorts first.
          const userInput = String((inputs.find((i) => i.name === 'userPrompt') || {}).value || '');
          const blocks = [
            ...userInput.matchAll(/\[([A-Z]+)\]\n([\s\S]*?)(?=\n\n\[[A-Z]+\]|\n\n==|\n\nReturn the JSON object now\.|$)/g),
          ].map((m) => ({ label: m[1], text: m[2].trim() }));
          blocks.sort((a, b) => a.text.localeCompare(b.text));
          const best = blocks.length ? blocks[0].label : 'A';
          return json(res, 200, {
            data: {
              response: '```json\n' + JSON.stringify({ reasoning: 'Chairman pick by content.', best }) + '\n```',
              run_time: 0.5, prompt_length: 60, output_length: 15,
            },
          });
        }
        if (isJudge) {
          // Deterministic verdict: rank the labelled candidates by response
          // text. A "contrarian" judge reverses the order, so a council that
          // includes it splits (used to exercise the tie/disagreement path).
          const judgeModel = p.split('/')[2] || '';
          const userInput = String((inputs.find((i) => i.name === 'userPrompt') || {}).value || '');
          const blocks = [
            ...userInput.matchAll(/\[([A-Z]+)\]\n([\s\S]*?)(?=\n\n\[[A-Z]+\]|\n\nReturn the JSON object now\.|$)/g),
          ].map((m) => ({ label: m[1], text: m[2].trim() }));
          blocks.sort((a, b) => a.text.localeCompare(b.text));
          if (judgeModel.includes('contrarian')) blocks.reverse();
          const ranking = blocks.map((b) => b.label);
          const verdict = {
            reasoning: `Ranked by response content (${judgeModel}).`,
            ranking,
            best: ranking[0],
            confidence: 'high',
          };
          return json(res, 200, {
            data: {
              response: '```json\n' + JSON.stringify(verdict) + '\n```',
              run_time: 0.8, prompt_length: 120, output_length: 30,
            },
          });
        }
        return json(res, 200, {
          data: {
            response: '```json\n{"sentiment": "positive", "score": 0.9}\n```',
            run_time: 1.5, prompt_length: 42, output_length: 7,
          },
        });
      }
      if (/^\/modelRepository\/models\/[^/]+\/variables$/.test(p) && req.method === 'GET') {
        return json(res, 200, { items: [] });
      }
      if (/^\/modelRepository\/models\/[^/]+$/.test(p) && req.method === 'GET') {
        // Tags include leftovers from an "earlier manifest" to prove cleanup
        res.writeHead(200, { 'Content-Type': 'application/json', ETag: '"abc123"' });
        return res.end(
          JSON.stringify({
            id: p.split('/').pop(),
            name: 'Used Prompt',
            tags: ['LLM', 'Prompt-Template', 'Custom-Tag', 'other_llm', 'LLM-Call-Included'],
          })
        );
      }
      if (/^\/modelRepository\/models\/[^/]+$/.test(p) && req.method === 'PUT') {
        return json(res, 200, {});
      }
      if (p === '/modelRepository/models/model-used/contents/c-opttrk' && req.method === 'DELETE') {
        optTrackerDeleted = true;
        res.writeHead(204);
        return res.end();
      }
      if (req.method === 'DELETE') { res.writeHead(204); return res.end(); }
      if (/^\/modelRepository\/models\/[^/]+\/modelVersions$/.test(p) && req.method === 'POST') {
        return json(res, 200, { id: 'v2' });
      }
      if (/^\/modelRepository\/models\/[^/]+\/contents$/.test(p) && req.method === 'POST') {
        // A re-uploaded optimization tracker (run deletion) replaces the state.
        if (raw.includes('filename="Prompt-Optimization-Tracker.json"')) {
          const replacement = multipartJson(raw);
          if (Array.isArray(replacement)) {
            optimizationTracker.length = 0;
            optimizationTracker.push(...replacement);
            optTrackerDeleted = false;
          }
        }
        return json(res, 201, { id: 'c-new' });
      }
      json(res, 404, { message: 'not mocked: ' + req.method + ' ' + p });
    });
  })
  .listen(PORT, () => console.log('mock listening on ' + PORT));
