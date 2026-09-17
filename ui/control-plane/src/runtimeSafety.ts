import type { RuntimeApplyRequest, RuntimePlanResponse } from './api';

type ApiErrorLike = {
  status: number;
  details?: unknown;
};

function isApiErrorLike(error: unknown): error is ApiErrorLike {
  return typeof error === 'object'
    && error !== null
    && typeof (error as Record<string, unknown>).status === 'number';
}

export function runtimeErrorReason(error: unknown): string | null {
  if (!isApiErrorLike(error) || typeof error.details !== 'object' || error.details === null) {
    return null;
  }
  const reason = (error.details as Record<string, unknown>).reason;
  return typeof reason === 'string' ? reason : null;
}

export function isRuntimePlanChanged(error: unknown): boolean {
  return isApiErrorLike(error)
    && error.status === 409
    && runtimeErrorReason(error) === 'RUNTIME_PLAN_CHANGED';
}

export function runtimeApplyRequest(plan: RuntimePlanResponse): RuntimeApplyRequest {
  return {
    desired_state: plan.desired_state,
    force: plan.force,
    plan_digest: plan.plan_digest,
  };
}

export function runtimePlanRequiresForceReview(plan: RuntimePlanResponse): boolean {
  return plan.requires_force && !plan.force;
}
