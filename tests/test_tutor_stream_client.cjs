// Offline client protocol regression: no browser, server or external requests.
const assert = require('node:assert/strict');
const path = require('node:path');
const { createRequire } = require('node:module');
const req = createRequire(path.resolve('frontend/package.json'));
const tick = () => new Promise(setImmediate);
(async () => {
  const result = await req('esbuild').build({
    entryPoints: [path.resolve('frontend/src/api/tutor.ts')], bundle: true,
    platform: 'node', format: 'cjs', write: false,
    plugins: [{ name: 'offline-auth', setup(build) {
      build.onResolve({ filter: /^\.\/axios$/ }, () => ({ path: 'axios', namespace: 'fake' }));
      build.onLoad({ filter: /.*/, namespace: 'fake' }, () => ({ contents:
        'export const authHeaders=(x)=>x; export default globalThis.__apiClient;', loader: 'js' }));
    } }],
  });
  globalThis.__apiClient = { get: async () => ({ data: { turns: [] } }) };
  const mod = { exports: {} };
  new Function('module', 'exports', 'require', result.outputFiles[0].text)(mod, mod.exports, require);
  const api = mod.exports;
  let fetches = 0, controller;
  globalThis.fetch = async () => {
    fetches++;
    return { ok: true, body: new ReadableStream({ start(c) { controller = c; } }) };
  };
  const events = [], errors = [];
  const abort = api.streamTutorTurn({ subject_id: 'test', message: 'test' }, {
    onEvent: e => events.push(e), onError: e => errors.push(e),
  });
  await tick();
  controller.enqueue(new TextEncoder().encode('data: {"type":"delta","content":"partial"}\n\n'));
  await tick();
  abort.abort();
  controller.enqueue(new TextEncoder().encode('data: {"type":"done","answer":"late"}\n\n'));
  controller.close(); await tick();
  assert.deepEqual(events.map(e => e.type), ['delta']);
  assert.equal(errors.length, 0);
  assert.equal(fetches, 1);
  const incomplete = [];
  api.streamTutorTurn({ subject_id: 'test', message: 'test' }, {
    onEvent: () => {}, onError: e => incomplete.push(e),
  });
  await tick(); controller.close(); await tick();
  assert.equal(incomplete.length, 1);
  const beforeRestore = fetches;
  await api.listTurns('test'); // history restore never calls the paid SSE POST
  assert.equal(fetches, beforeRestore);
  console.log('PASS: partial retained, late done rejected, EOF reported, history read does not resend');
})().catch(e => { console.error(e); process.exitCode = 1; });
