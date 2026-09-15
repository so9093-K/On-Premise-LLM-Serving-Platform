import assert from 'node:assert/strict';
import test from 'node:test';

import {
  applyConfigurationChange,
  applyConfigurationRollback,
  fetchConfigurationEffective,
  fetchConfigurationHistory,
  fetchConfigurationSchema,
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


test('Configuration reads fail closed on unsupported contract versions', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input) => new Response(JSON.stringify({
    version: 3,
    revision: 7,
    items: [],
    write_status: {},
  }), {
    status: 200,
    headers: {
      'Content-Type': 'application/json',
      ...(input === '/admin/config/effective' ? { ETag: '"config-7"' } : {}),
    },
  });

  try {
    await assert.rejects(
      fetchConfigurationSchema('admin-token'),
      /Unsupported Configuration contract version/,
    );
    await assert.rejects(
      fetchConfigurationEffective('admin-token'),
      /Unsupported Configuration contract version/,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});


test('Configuration History forwards the server cursor as an opaque query value', async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (input, init = {}) => {
    calls.push({ input, init });
    return new Response(JSON.stringify({ items: [], next_cursor: null }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  try {
    await fetchConfigurationHistory('admin-token', 'v1:cursor+/=');
    assert.equal(calls.length, 1);
    assert.equal(calls[0].input, '/admin/config/history?cursor=v1%3Acursor%2B%2F%3D');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('Configuration Rollback Apply reuses the reviewed ETag, target revision, and digest', async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (input, init = {}) => {
    calls.push({ input, init });
    return new Response(JSON.stringify({
      operation_id: `cfg_${'2'.repeat(32)}`,
      status: 'verified',
      changed: true,
      revision: 8,
      target_revision: 4,
      verification: {
        store_revision: 8,
        resolver_revision: 8,
        runtime_revision: 8,
        synchronized: true,
      },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };

  try {
    await applyConfigurationRollback('admin-token', '"config-7"', {
      target_revision: 4,
      plan_digest: 'd'.repeat(64),
    });

    assert.equal(calls.length, 1);
    assert.equal(calls[0].input, '/admin/config/rollbacks');
    assert.equal(calls[0].init.method, 'POST');
    assert.equal(calls[0].init.headers['If-Match'], '"config-7"');
    assert.deepEqual(JSON.parse(calls[0].init.body), {
      target_revision: 4,
      plan_digest: 'd'.repeat(64),
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});
