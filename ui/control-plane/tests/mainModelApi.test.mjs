import assert from 'node:assert/strict';
import test from 'node:test';

import {
  fetchMainModelOperation,
  switchMainModel,
} from '../src/api.ts';

test('one switch request uses the returned operation id for tracking', async (t) => {
  const originalFetch = globalThis.fetch;
  const calls = [];

  globalThis.fetch = async (input, init = {}) => {
    const url = String(input);
    calls.push({ url, init });

    if (url === '/admin/main-model/switch') {
      return new Response(JSON.stringify({ operation_id: '123e4567-e89b-12d3-a456-426614174000' }), {
        status: 202,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    if (url === '/admin/main-model/operations/123e4567-e89b-12d3-a456-426614174000') {
      return new Response(JSON.stringify({
        id: '123e4567-e89b-12d3-a456-426614174000',
        requested_profile: 'candidate',
        previous_profile: 'stable',
        client_request_id: null,
        status: 'completed',
        stage: 'completed',
        error: null,
        rollback_error: null,
        created_at: 1,
        updated_at: 2,
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    throw new Error(`unexpected request: ${url}`);
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
  });

  const result = await switchMainModel('admin-secret', {
    profile: 'candidate',
    confirm_unverified: false,
  });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/admin/main-model/switch');
  assert.equal(calls[0].init.method, 'POST');
  assert.equal(calls[0].init.headers.Authorization, 'Bearer admin-secret');
  assert.deepEqual(JSON.parse(calls[0].init.body), {
    profile: 'candidate',
    confirm_unverified: false,
  });

  const operation = await fetchMainModelOperation('admin-secret', result.operation_id);

  assert.equal(calls.length, 2);
  assert.equal(
    calls[1].url,
    '/admin/main-model/operations/123e4567-e89b-12d3-a456-426614174000',
  );
  assert.equal(operation.id, result.operation_id);
  assert.equal(operation.status, 'completed');
});
