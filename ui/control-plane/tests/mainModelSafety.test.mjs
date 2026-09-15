import assert from 'node:assert/strict';
import test from 'node:test';

import {
  isMainModelOperationTerminal,
  mainModelProfileRequiresConfirmation,
  mainModelProfileSwitchable,
  mainModelSwitchRequest,
} from '../src/mainModelSafety.ts';

function profile(status, overrides = {}) {
  return {
    id: 'candidate',
    display_name: 'Candidate',
    served_model_name: 'local-main',
    upstream_model_id: 'org/model',
    revision: 'a'.repeat(40),
    compatibility: { status },
    capabilities: { deployed_input: ['text'] },
    gateway_policy: {},
    runtime_image: 'registry.example/model@sha256:' + 'b'.repeat(64),
    vram_fraction: 0.5,
    active: false,
    ...overrides,
  };
}

test('only unverified and unknown profiles require explicit confirmation', () => {
  assert.equal(mainModelProfileRequiresConfirmation(profile('verified')), false);
  assert.equal(mainModelProfileRequiresConfirmation(profile('likely')), false);
  assert.equal(mainModelProfileRequiresConfirmation(profile('unverified')), true);
  assert.equal(mainModelProfileRequiresConfirmation(profile('unknown')), true);
});

test('incompatible and already-active profiles cannot create a switch request', () => {
  assert.equal(mainModelProfileSwitchable(profile('incompatible')), false);
  assert.equal(mainModelProfileSwitchable(profile('verified', { active: true })), false);
  assert.throws(() => mainModelSwitchRequest(profile('incompatible'), true));
});

test('switch request preserves backend confirmation semantics and terminal states', () => {
  assert.deepEqual(mainModelSwitchRequest(profile('likely'), false), {
    profile: 'candidate',
    confirm_unverified: false,
  });
  assert.throws(() => mainModelSwitchRequest(profile('unknown'), false));
  assert.deepEqual(mainModelSwitchRequest(profile('unknown'), true), {
    profile: 'candidate',
    confirm_unverified: true,
  });
  assert.equal(isMainModelOperationTerminal({ status: 'validating' }), false);
  assert.equal(isMainModelOperationTerminal({ status: 'completed' }), true);
  assert.equal(isMainModelOperationTerminal({ status: 'failed' }), true);
  assert.equal(isMainModelOperationTerminal({ status: 'rollback_failed' }), true);
});
