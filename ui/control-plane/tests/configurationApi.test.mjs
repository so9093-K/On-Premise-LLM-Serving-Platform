import assert from 'node:assert/strict';
import test from 'node:test';

import {
  applyConfigurationChange,
  fetchConfigurationEffective,
} from '../src/api.ts';

test('Configuration Apply reuses the exact ETag returned by effective read', async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (input, init = {}) => {
    calls.push({ input, init });
    if (calls.length === 1) {
      return new Response(JSON.stringify({
        version: 2,
        revision: 7,
        items: [],
        write_status: {},
      }), {
        status: 200,
        headers: {
          'Content-Type': 'application/json',
          ETag: '"config-7"',
        },
      });
    }
    return new Response(JSON.stringify({
      operation_id: `cfg_${'1'.repeat(32)}`,
      status: 'verified',
      changed: true,
      revision: 8,
      verification: {
        store_revision: 8,
        resolver_revision: 8,
        runtime_revision: 8,
        synchronized: true,
      },
    }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  try {
    const effective = await fetchConfigurationEffective('admin-token');
    const changes = [{ key: 'streaming.max_chunks', op: 'set', value: 2048 }];
    await applyConfigurationChange('admin-token', effective.etag, {
      plan_digest: 'b'.repeat(64),
      changes,
    });

    assert.equal(calls.length, 2);
    assert.equal(calls[0].input, '/admin/config/effective');
    assert.equal(effective.etag, '"config-7"');

    assert.equal(calls[1].input, '/admin/config');
    assert.equal(calls[1].init.method, 'PATCH');
    assert.equal(calls[1].init.headers['If-Match'], '"config-7"');
    assert.deepEqual(JSON.parse(calls[1].init.body), {
      plan_digest: 'b'.repeat(64),
      changes,
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});
