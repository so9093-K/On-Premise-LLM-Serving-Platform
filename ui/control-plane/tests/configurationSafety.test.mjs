import assert from 'node:assert/strict';
import test from 'node:test';

import {
  configurationApplyRequest,
  configurationItemApplies,
  configurationResetChange,
  parseConfigurationDraft,
} from '../src/configurationSafety.ts';

test('reviewed Configuration digest and changes are forwarded unchanged to Apply', () => {
  const changes = [{ key: 'streaming.max_chunks', op: 'set', value: 2048 }];
  const request = configurationApplyRequest({ plan_digest: 'a'.repeat(64) }, changes);

  assert.equal(request.plan_digest, 'a'.repeat(64));
  assert.equal(request.changes, changes);
});

test('operator override reset remains a reset intent instead of copying a default value', () => {
  assert.deepEqual(configurationResetChange('streaming.max_chunks'), {
    key: 'streaming.max_chunks',
    op: 'reset',
  });
});

test('Configuration draft serialization follows metadata type and applicability', () => {
  assert.equal(parseConfigurationDraft({ type: 'integer' }, '1234'), 1234);
  assert.equal(parseConfigurationDraft({ type: 'number' }, '0.75'), 0.75);
  assert.equal(parseConfigurationDraft({ type: 'boolean' }, true), true);
  assert.throws(() => parseConfigurationDraft({ type: 'integer' }, ''), /requires a value/);
  assert.throws(() => parseConfigurationDraft({ type: 'number' }, '   '), /requires a value/);
  assert.throws(() => parseConfigurationDraft({ type: 'array' }, 'x'), /지원하지 않는 editable configuration type/);

  const metadata = { applicability: { features: ['retrieval'] } };
  assert.equal(configurationItemApplies(metadata, ['retrieval', 'runtime_control']), true);
  assert.equal(configurationItemApplies(metadata, ['runtime_control']), false);
});
