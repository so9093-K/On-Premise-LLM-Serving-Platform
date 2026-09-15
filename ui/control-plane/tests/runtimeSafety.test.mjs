import assert from 'node:assert/strict';
import test from 'node:test';

import {
  isRuntimePlanChanged,
  isUnauthorized,
  runtimeApplyRequest,
  runtimePlanRequiresForceReview,
} from '../src/runtimeSafety.ts';

function plan(overrides = {}) {
  return {
    service_key: 'main',
    current_state: 'stopped',
    desired_state: 'active',
    force: false,
    no_op: false,
    admissible: true,
    requires_force: false,
    reason: null,
    prerequisites: [],
    start: ['main'],
    stop: [],
    impact: [],
    budget: {
      before: { ceiling: 0.9, used: 0.4, free: 0.5 },
      after: { ceiling: 0.9, used: 0.8, free: 0.1 },
    },
    plan_digest: 'a'.repeat(64),
    ...overrides,
  };
}

test('reviewed plan digest is forwarded unchanged to Apply', () => {
  assert.deepEqual(runtimeApplyRequest(plan({ plan_digest: 'b'.repeat(64) })), {
    desired_state: 'active',
    force: false,
    plan_digest: 'b'.repeat(64),
  });
});

test('only RUNTIME_PLAN_CHANGED conflict invalidates a reviewed plan', () => {
  assert.equal(isRuntimePlanChanged({
    status: 409,
    details: { reason: 'RUNTIME_PLAN_CHANGED' },
  }), true);

  assert.equal(isRuntimePlanChanged({
    status: 409,
    details: { reason: 'OTHER_CONFLICT' },
  }), false);

  assert.equal(isRuntimePlanChanged({
    status: 401,
    details: { reason: 'RUNTIME_PLAN_CHANGED' },
  }), false);
});

test('401 responses cross the reauthentication boundary', () => {
  assert.equal(isUnauthorized({ status: 401 }), true);
  assert.equal(isUnauthorized({ status: 403 }), false);
  assert.equal(isUnauthorized(new Error('network failure')), false);
});

test('force-required plan requires a new force review before Apply', () => {
  assert.equal(runtimePlanRequiresForceReview(plan({ requires_force: true, force: false })), true);
  assert.equal(runtimePlanRequiresForceReview(plan({ requires_force: true, force: true })), false);
  assert.equal(runtimePlanRequiresForceReview(plan({ requires_force: false, force: false })), false);
});
