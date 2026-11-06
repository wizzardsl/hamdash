/* The problem-report service (tools/report-service/worker.js), under
 * `node --test`.
 *
 *   node --test 'tests/js/*.test.js'
 *
 * The Worker is the one piece of hamdash that runs on somebody else's
 * computer, so what it accepts and what it writes is pinned here with
 * GitHub's API stubbed out: a zip goes through to the release asset byte
 * for byte, anything else is refused before GitHub is asked anything, and
 * the issue says what the report is without carrying more than it should.
 */
'use strict';
const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');

const load = () => import(path.join(__dirname, '../../tools/report-service/worker.js'));

const ZIP = new Uint8Array([0x50, 0x4b, 0x03, 0x04, 1, 2, 3, 4, 5]);

function stubGithub() {
  const calls = [];
  const fake = async (url, init = {}) => {
    let body = init.body;
    if (body && typeof body.getReader === 'function') {
      const chunks = [];
      const r = body.getReader();
      for (;;) { const { done, value } = await r.read(); if (done) break; chunks.push(...value); }
      body = new Uint8Array(chunks);
    }
    calls.push({ url: String(url), method: init.method || 'GET', headers: init.headers || {}, body });
    const json = (status, obj) => new Response(JSON.stringify(obj), { status });
    if (url.includes('/releases/tags/')) return json(404, {});
    if (url.endsWith('/releases') && init.method === 'POST') return json(201, { id: 42 });
    if (url.includes('uploads.github.com')) return json(201, { browser_download_url: 'https://github.com/x/asset.zip' });
    if (url.endsWith('/issues')) return json(201, { number: 1 });
    return json(500, {});
  };
  return { calls, fake };
}

function post(body, headers = {}) {
  return new Request('https://w.example/report', {
    method: 'POST', body, duplex: 'half',
    headers: { 'Content-Length': String(body.length), 'X-Hamdash-Version': '1.4.0',
               'X-Hamdash-Platform': 'Windows%2011', 'X-Hamdash-Radio': 'FT-900%20via%20flrig',
               ...headers },
  });
}

test('a reference is short, readable and unambiguous', async () => {
  const w = await load();
  for (let i = 0; i < 50; i++) assert.match(w.reference(), /^HD-[2-9A-HJKMNP-TV-Z]{5}$/);
  assert.equal(w.reference(new Uint8Array([0, 1, 2, 3, 4])), 'HD-23456');
});

test('reports are grouped in one release a month', async () => {
  const w = await load();
  assert.equal(w.releaseTag(new Date('2026-11-05T01:02:03Z')), 'reports-2026-11');
});

test('header text is decoded, cleaned and capped', async () => {
  const w = await load();
  assert.equal(w.field('Windows%2011', 40), 'Windows 11');
  assert.equal(w.field('a\u0000b\u0007c', 40), 'a b c');
  assert.equal(w.field('x'.repeat(100), 10).length, 10);
  assert.equal(w.field('%E0%A4%A', 40), '%E0%A4%A', 'bad encoding kept as sent');
});

test('the issue says what the report is, and quotes the note', async () => {
  const w = await load();
  const t = w.issueText('HD-7F3KQ', { version: '1.4.0', platform: 'Windows 11',
    radio: 'FT-900 via flrig', note: 'froze\nafter a reload', tag: 'reports-2026-11', repo: 'o/r' },
  'https://x/HD-7F3KQ.zip', 51200, new Date('2026-11-05T01:02:03.456Z'));
  assert.equal(t.title, 'HD-7F3KQ · hamdash 1.4.0 · Windows 11 · FT-900 via flrig');
  assert.match(t.body, /> froze\n> after a reload/);
  assert.match(t.body, /\(50 kB\)/);
  assert.match(t.body, /gh release download reports-2026-11 -R o\/r -p HD-7F3KQ\.zip/);
  assert.match(w.issueText('HD-1', { tag: 't', repo: 'r' }, 'u', 1).body, /nothing written/);
});

test('only reports older than 90 days are deleted', async () => {
  const w = await load();
  const now = new Date('2026-11-05T00:00:00Z');
  const old = { id: 1, created_at: '2026-08-01T00:00:00Z' };
  const fresh = { id: 2, created_at: '2026-10-01T00:00:00Z' };
  assert.deepEqual(w.expired([old, fresh], now).map((a) => a.id), [1]);
});

test('a zip goes through untouched and comes back as a reference', async () => {
  const w = await load();
  const { calls, fake } = stubGithub();
  const real = globalThis.fetch;
  globalThis.fetch = fake;
  try {
    const r = await w.default.fetch(post(ZIP, { 'X-Hamdash-Note': 'it%20froze' }),
      { GITHUB_TOKEN: 't0ken', REPO: 'wizzardsl/hamdash-reports' });
    const out = await r.json();
    assert.equal(r.status, 200);
    assert.match(out.reference, /^HD-/);
    const up = calls.find((c) => c.url.includes('uploads.github.com'));
    assert.deepEqual([...up.body], [...ZIP], 'the zip reaches GitHub byte for byte');
    assert.equal(up.headers['Content-Length'], String(ZIP.length));
    const issue = JSON.parse(calls.find((c) => c.url.endsWith('/issues')).body);
    assert.match(issue.title, /^HD-\w{5} · hamdash 1\.4\.0 · Windows 11 · FT-900 via flrig$/);
    assert.match(issue.body, /> it froze/);
    assert.ok(calls.every((c) => !c.url.includes('t0ken')), 'the key is never in a URL');
  } finally { globalThis.fetch = real; }
});

test('refused before GitHub is asked anything', async () => {
  const w = await load();
  const { calls, fake } = stubGithub();
  const real = globalThis.fetch;
  globalThis.fetch = fake;
  try {
    const env = { GITHUB_TOKEN: 't', REPO: 'o/r' };
    const notZip = await w.default.fetch(post(new Uint8Array([1, 2, 3, 4, 5])), env);
    assert.equal(notZip.status, 415);
    const big = await w.default.fetch(post(ZIP, { 'Content-Length': String(w.MAX_BYTES + 1) }), env);
    assert.equal(big.status, 413);
    const noKey = await w.default.fetch(post(ZIP), { REPO: 'o/r' });
    assert.equal(noKey.status, 503);
    assert.match((await noKey.json()).error, /not set up/);
    const limited = await w.default.fetch(post(ZIP),
      { ...env, LIMITER: { limit: async () => ({ success: false }) } });
    assert.equal(limited.status, 429);
    assert.equal(calls.length, 0);
  } finally { globalThis.fetch = real; }
});

test('anything else is not here', async () => {
  const w = await load();
  const r = await w.default.fetch(new Request('https://w.example/admin'), {});
  assert.equal(r.status, 404);
  const ok = await w.default.fetch(new Request('https://w.example/'), {});
  assert.equal((await ok.json()).service, 'hamdash problem reports');
});
