import assert from 'node:assert/strict';
import test from 'node:test';

import {
  isMainModelOperationTerminal,
  mainModelProfileRequiresConfirmation,
  mainModelProfileSwitchable,
  mainModelSwitchRequest,
} from '../src/mainModelSafety.ts';

function profile(
  technicalStatus = 'compatible',
  qualificationStatus = 'verified',
  overrides = {},
) {
  return {
    id: 'candidate',
    display_name: 'Candidate',
    served_model_name: 'local-main',
    upstream_model_id: 'org/model',
    revision: 'a'.repeat(40),
    compatibility: { status: technicalStatus },
    qualification: { status: qualificationStatus },
    capabilities: { deployed_input: ['text'] },
    gateway_policy: {},
    runtime_image: 'registry.example/model@sha256:' + 'b'.repeat(64),
    vram_fraction: 0.5,
    active: false,
    ...overrides,
  };
}

test('qualification alone controls explicit confirmation', () => {
  assert.equal(mainModelProfileRequiresConfirmation(profile('compatible', 'verified')), false);
  assert.equal(mainModelProfileRequiresConfirmation(profile('compatible', 'unverified')), true);
  assert.equal(mainModelProfileRequiresConfirmation(profile('unknown', 'unverified')), true);
});

test('compatibility and active state control switchability', () => {
  assert.equal(mainModelProfileSwitchable(profile('incompatible', 'unverified')), false);
  assert.equal(
    mainModelProfileSwitchable(profile('compatible', 'verified', { active: true })),
    false,
  );
  assert.equal(mainModelProfileSwitchable(profile('unknown', 'unverified')), true);
  assert.throws(() => mainModelSwitchRequest(profile('incompatible', 'unverified'), true));
});

test('switch request carries qualification confirmation and terminal states', () => {
  assert.deepEqual(mainModelSwitchRequest(profile('compatible', 'unverified'), true), {
    profile: 'candidate',
    confirm_unverified: true,
  });
  assert.throws(() => mainModelSwitchRequest(profile('unknown', 'unverified'), false));
  assert.deepEqual(mainModelSwitchRequest(profile('unknown', 'unverified'), true), {
    profile: 'candidate',
    confirm_unverified: true,
  });
  assert.deepEqual(mainModelSwitchRequest(profile('compatible', 'verified'), false), {
    profile: 'candidate',
    confirm_unverified: false,
  });
  assert.equal(isMainModelOperationTerminal({ status: 'validating' }), false);
  assert.equal(isMainModelOperationTerminal({ status: 'completed' }), true);
  assert.equal(isMainModelOperationTerminal({ status: 'failed' }), true);
  assert.equal(isMainModelOperationTerminal({ status: 'rollback_failed' }), true);
});
