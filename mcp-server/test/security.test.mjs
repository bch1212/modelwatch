import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

const validKey = `mw_${'a'.repeat(43)}`;
const binary = new URL('../dist/index.js', import.meta.url).pathname;
function start(base, key = validKey) {
  return spawnSync(process.execPath, [binary], {
    env: { ...process.env, MODELWATCH_API_KEY: key, MODELWATCH_API_BASE: base },
    input: '', encoding: 'utf8', timeout: 5000,
  });
}

test('rejects credential leaks through invalid or insecure API base', () => {
  for (const base of [
    'http://example.org', 'https://example.org/path',
    'https://example.org/?token=leak', 'https://user:pass@example.org',
    'https://example.org/#fragment', 'not a URL',
  ]) {
    const result = start(base);
    assert.notEqual(result.status, 0, base);
    assert.doesNotMatch(result.stderr, /mw_a{20}/, base);
  }
});

test('permits HTTPS and loopback HTTP while rejecting malformed keys', () => {
  for (const base of ['https://api.modelwatch.app', 'http://localhost:8000', 'http://127.0.0.1:8000']) {
    assert.equal(start(base).status, 0, base);
  }
  assert.notEqual(start('https://api.modelwatch.app', 'invalid').status, 0);
});

test('frontend removes legacy persistent keys on load and never writes one', () => {
  const code = readFileSync(new URL('../../frontend/assets/app.js', import.meta.url), 'utf8');
  const legacy = new Map([['mw_api_key', validKey]]);
  const session = new Map();
  const ctx = {
    window: { location: { hostname: 'modelwatch.app' } },
    document: { getElementById: () => null, querySelectorAll: () => [] },
    localStorage: {
      getItem: key => legacy.get(key),
      removeItem: key => legacy.delete(key),
      setItem: () => { throw new Error('persistent key write'); },
    },
    sessionStorage: {
      getItem: key => session.get(key),
      setItem: (key, value) => session.set(key, value),
      removeItem: key => session.delete(key),
    },
  };
  runInNewContext(code, ctx);
  assert.equal(legacy.has('mw_api_key'), false);
  assert.equal(session.has('mw_api_key'), false);
});
