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
  headerRow(5, 'Sys 3', 'User 3'), modelRow(5, 'Response three', 'demo_llm', '{temperature:0.9}'),
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
        // remove it.
        return json(res, 200, {
          items: [
            { id: 'c-trk', name: 'Prompt-Experiment-Tracker.json', fileUri: '/files/files/trk-1' },
            { id: 'c-req', name: 'requirements.json', fileUri: '/files/files/req-1' },
          ],
        });
      }
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
        const isJudge = Boolean(sys && String(sys.value).includes('impartial evaluator'));
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
      if (req.method === 'DELETE') { res.writeHead(204); return res.end(); }
      if (/^\/modelRepository\/models\/[^/]+\/modelVersions$/.test(p) && req.method === 'POST') {
        return json(res, 200, { id: 'v2' });
      }
      if (/^\/modelRepository\/models\/[^/]+\/contents$/.test(p) && req.method === 'POST') {
        return json(res, 201, { id: 'c-new' });
      }
      json(res, 404, { message: 'not mocked: ' + req.method + ' ' + p });
    });
  })
  .listen(PORT, () => console.log('mock listening on ' + PORT));
