import assert from 'node:assert/strict';
import test from 'node:test';

import {
  fetchMainModelOperations,
  fetchRuntimeOperations,
} from '../src/api.ts';

test('Runtime operation list preserves the opaque server cursor', async (t) => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (input, init = {}) => {
    calls.push({ url: String(input), init });
    return new Response(JSON.stringify({ items: [], next_cursor: null }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };
  t.after(() => { globalThis.fetch = originalFetch; });

  await fetchRuntimeOperations('admin-secret', 'v1:cursor+/=', 25);

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/admin/runtimes/operations?limit=25&cursor=v1%3Acursor%2B%2F%3D');
  assert.equal(calls[0].init.headers.Authorization, 'Bearer admin-secret');
  assert.equal(calls[0].init.method, undefined);
});

test('Main Model operation list reads the backend-owned bounded collection', async (t) => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (input, init = {}) => {
    calls.push({ url: String(input), init });
    return new Response(JSON.stringify({ items: [] }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };
  t.after(() => { globalThis.fetch = originalFetch; });

  const response = await fetchMainModelOperations(null);

  assert.deepEqual(response.items, []);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/admin/main-model/operations');
  assert.equal(calls[0].init.headers.Authorization, undefined);
  assert.equal(calls[0].init.method, undefined);
});
