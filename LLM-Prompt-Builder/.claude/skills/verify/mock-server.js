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
function modelRow(runId, response) {
  return {
    runId, systemPrompt: '', userPrompt: '', model: 'demo_llm',
    options: '{temperature:0.7}', response,
    run_time: 1.2, prompt_length: 10, output_length: 20,
    best_prompt: 0, fastest_prompt: true, fewest_tokens_prompt: true,
  };
}
const trackerRows = [
  headerRow(1, 'Sys 1', 'User 1'), modelRow(1, 'Response one'),
  headerRow(2, 'Sys 2', 'User 2'), modelRow(2, 'Response two'),
  headerRow(3, 'Sys 3', 'User 3'), modelRow(3, 'Response three'),
];

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
        log.push({ method: req.method, url: req.url, body: raw });
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
        return json(res, 200, { items: [{ id: 'proj-1', name: 'Demo Project' }] });
      }
      if (p === '/modelRepository/projects/llm-proj/models') {
        const filter = u.searchParams.get('filter') || '';
        if (filter.includes('deprecated')) return json(res, 200, { items: [] });
        return json(res, 200, { items: [{ id: 'llm-1', name: 'demo_llm' }] });
      }
      if (p === '/modelRepository/projects/proj-1/models') {
        return json(res, 200, {
          items: [
            { id: 'model-used', name: 'Used Prompt' },
            { id: 'model-free', name: 'Free Prompt' },
            { id: 'model-err', name: 'Error Prompt' },
          ],
        });
      }
      if (p === '/modelRepository/models/llm-1/contents') {
        return json(res, 200, {
          items: [{ id: 'c-opt', name: 'options.json', fileUri: '/files/files/opt-1' }],
        });
      }
      if (p === '/files/files/opt-1/content') {
        return json(res, 200, { temperature: { default: 0.7 } });
      }
      if (p === '/modelRepository/models/model-used/contents' && req.method === 'GET') {
        return json(res, 200, {
          items: [{ id: 'c-trk', name: 'Prompt-Experiment-Tracker.json', fileUri: '/files/files/trk-1' }],
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
