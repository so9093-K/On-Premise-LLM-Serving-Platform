import assert from 'node:assert/strict';
import test from 'node:test';

import { ApiError } from '../src/api.ts';
import { apiErrorMessage, isUnauthorized } from '../src/apiFeedback.ts';

test('401 API errors cross the shared reauthentication boundary', () => {
  assert.equal(isUnauthorized(new ApiError('login required', 401, 'UNAUTHORIZED')), true);
  assert.equal(isUnauthorized(new ApiError('forbidden', 403, 'FORBIDDEN')), false);
  assert.equal(isUnauthorized(new Error('network failure')), false);
});

test('API error presentation keeps public code and message together', () => {
  assert.equal(
    apiErrorMessage(new ApiError('runtime unavailable', 503, 'MODEL_UNAVAILABLE')),
    'MODEL_UNAVAILABLE: runtime unavailable',
  );
  assert.equal(apiErrorMessage(new ApiError('plain failure', 500)), 'plain failure');
  assert.equal(apiErrorMessage(new Error('network failure')), 'network failure');
  assert.equal(apiErrorMessage('unexpected', 'fallback message'), 'fallback message');
});
