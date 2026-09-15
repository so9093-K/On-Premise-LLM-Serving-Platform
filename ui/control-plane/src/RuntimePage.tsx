import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Card, CardBody, CardTitle, Label, Spinner } from '@patternfly/react-core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  ApiError,
  applyRuntimeTransition,
  fetchRuntimeOperation,
  fetchRuntimes,
  planRuntimeTransition,
  type RuntimeOperationResponse,
  type RuntimePlanResponse,
} from './api';

type DesiredState = 'active' | 'stopped';

type RuntimePageProps = {
  token: string | null;
  onUnauthorized: () => void;
};

function displayNumber(value: number | null | undefined): string {
  return typeof value === 'number' ? value.toFixed(2) : '—';
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 409) {
      return `${error.message} 현재 상태가 검토 시점과 달라졌을 수 있으므로 새 Plan을 확인하세요.`;
    }
    return error.code ? `${error.code}: ${error.message}` : error.message;
  }
  return error instanceof Error ? error.message : '요청에 실패했습니다.';
}

function operationIdFromError(error: unknown): string | null {
  if (!(error instanceof ApiError) || typeof error.details !== 'object' || error.details === null) {
    return null;
  }
  const operationId = (error.details as Record<string, unknown>).operation_id;
  return typeof operationId === 'string' ? operationId : null;
}

function verificationSummary(operation: RuntimeOperationResponse): string {
  const verification = operation.verification;
  if (typeof verification !== 'object' || verification === null) {
    return '관측 정보 없음';
  }
  const converged = (verification as Record<string, unknown>).converged;
  if (converged === true) return 'Observed state converged';
  if (converged === false) return 'Observed state did not converge';
  return 'Observed convergence not asserted';
}

export function RuntimePage({ token, onUnauthorized }: RuntimePageProps) {
  const queryClient = useQueryClient();
  const [review, setReview] = useState<RuntimePlanResponse | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [operationId, setOperationId] = useState<string | null>(null);

  const authClass = token === null ? 'anonymous' : 'authenticated';
  const runtimesQuery = useQuery({
    queryKey: ['runtime-control', 'runtimes', authClass],
    queryFn: () => fetchRuntimes(token),
    retry: false,
    refetchInterval: () => document.visibilityState === 'visible' ? 10_000 : false,
    refetchIntervalInBackground: false,
  });

  useEffect(() => {
    if (runtimesQuery.error instanceof ApiError && runtimesQuery.error.status === 401) {
      onUnauthorized();
    }
  }, [onUnauthorized, runtimesQuery.error]);

  const operationQuery = useQuery({
    queryKey: ['runtime-control', 'operation', operationId, authClass],
    queryFn: () => {
      if (operationId === null) throw new Error('operation id is required');
      return fetchRuntimeOperation(token, operationId);
    },
    enabled: operationId !== null,
    retry: false,
  });

  useEffect(() => {
    if (operationQuery.error instanceof ApiError && operationQuery.error.status === 401) {
      onUnauthorized();
    }
  }, [onUnauthorized, operationQuery.error]);

  const planMutation = useMutation({
    mutationFn: ({ serviceKey, desiredState, force }: {
      serviceKey: string;
      desiredState: DesiredState;
      force: boolean;
    }) => planRuntimeTransition(token, serviceKey, { desired_state: desiredState, force }),
    retry: false,
    onMutate: () => {
      setActionError(null);
      setOperationId(null);
    },
    onSuccess: (plan) => setReview(plan),
    onError: (error) => {
      if (error instanceof ApiError && error.status === 401) {
        onUnauthorized();
        return;
      }
      setActionError(errorMessage(error));
    },
  });

  const applyMutation = useMutation({
    mutationFn: (plan: RuntimePlanResponse) => applyRuntimeTransition(token, plan.service_key, {
      desired_state: plan.desired_state,
      force: plan.force,
      plan_digest: plan.plan_digest,
    }),
    retry: false,
    onMutate: () => setActionError(null),
    onSuccess: async (result) => {
      setOperationId(result.operation_id);
      setReview(null);
      await queryClient.invalidateQueries({ queryKey: ['runtime-control', 'runtimes'] });
    },
    onError: async (error) => {
      if (error instanceof ApiError && error.status === 401) {
        onUnauthorized();
        return;
      }
      const failedOperationId = operationIdFromError(error);
      if (failedOperationId !== null) setOperationId(failedOperationId);
      if (error instanceof ApiError && error.status === 409) setReview(null);
      setActionError(errorMessage(error));
      await queryClient.invalidateQueries({ queryKey: ['runtime-control', 'runtimes'] });
    },
  });

  const selectedRuntime = useMemo(() => {
    if (review === null || runtimesQuery.data === undefined) return null;
    return runtimesQuery.data.runtimes.find((runtime) => runtime.service_key === review.service_key) ?? null;
  }, [review, runtimesQuery.data]);

  if (runtimesQuery.isPending) {
    return <div className="inline-loading"><Spinner size="lg" aria-label="Runtime 상태 loading" /> Runtime 상태를 불러오는 중입니다.</div>;
  }

  if (runtimesQuery.isError) {
    return <Alert isInline variant="danger" title="Runtime 상태를 불러오지 못했습니다.">{errorMessage(runtimesQuery.error)}</Alert>;
  }

  const { runtimes, budget } = runtimesQuery.data;
  const actionPending = planMutation.isPending || applyMutation.isPending;

  return (
    <section className="runtime-page">
      <div className="page-heading">
        <div>
          <h1>Runtimes</h1>
          <p>서버가 계산한 Plan을 검토한 뒤 동일한 digest로 적용하고 observed verification evidence를 확인합니다.</p>
        </div>
        <Button variant="secondary" onClick={() => runtimesQuery.refetch()} isDisabled={runtimesQuery.isFetching}>
          {runtimesQuery.isFetching ? '새로고침 중…' : '새로고침'}
        </Button>
      </div>

      {budget ? (
        <Card>
          <CardTitle>GPU budget</CardTitle>
          <CardBody>
            <dl className="facts compact-facts">
              <dt>Ceiling</dt><dd>{displayNumber(budget.ceiling)}</dd>
              <dt>Used</dt><dd>{displayNumber(budget.used)}</dd>
              <dt>Free</dt><dd>{displayNumber(budget.free)}</dd>
            </dl>
          </CardBody>
        </Card>
      ) : (
        <Alert isInline variant="warning" title="GPU budget 관측을 사용할 수 없습니다.">
          Runtime 상태는 표시하지만 resource impact는 Plan 응답을 기준으로 판단하세요.
        </Alert>
      )}

      {actionError ? <Alert isInline variant="danger" title="Runtime operation을 완료하지 못했습니다.">{actionError}</Alert> : null}

      <div className="table-scroll">
        <table className="runtime-table">
          <thead>
            <tr>
              <th>Runtime</th>
              <th>Desired</th>
              <th>Observed container</th>
              <th>VRAM</th>
              <th>Criticality</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {runtimes.map((runtime) => (
              <tr key={runtime.service_key}>
                <td>
                  <strong>{runtime.service_key}</strong>
                  {runtime.active_profile ? <small>{runtime.active_profile}</small> : null}
                </td>
                <td><Label>{runtime.state}</Label></td>
                <td>{runtime.container_status}</td>
                <td>{displayNumber(runtime.vram_fraction)}</td>
                <td>{runtime.criticality ?? '—'}</td>
                <td className="runtime-actions">
                  <Button
                    size="sm"
                    variant="secondary"
                    isDisabled={actionPending || runtime.state === 'starting'}
                    onClick={() => planMutation.mutate({ serviceKey: runtime.service_key, desiredState: 'active', force: false })}
                  >
                    Start plan
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    isDanger
                    isDisabled={actionPending || runtime.state === 'starting'}
                    onClick={() => planMutation.mutate({ serviceKey: runtime.service_key, desiredState: 'stopped', force: false })}
                  >
                    Stop plan
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {planMutation.isPending ? <div className="inline-loading"><Spinner size="md" aria-label="Runtime Plan 계산 중" /> Plan을 계산하는 중입니다.</div> : null}

      {review ? (
        <Card className="review-card">
          <CardTitle>Transition review — {review.service_key}</CardTitle>
          <CardBody>
            <div className="review-grid">
              <dl className="facts compact-facts">
                <dt>Desired before</dt><dd>{review.current_state}</dd>
                <dt>Desired after</dt><dd>{review.desired_state}</dd>
                <dt>Observed</dt><dd>{selectedRuntime?.container_status ?? '—'}</dd>
                <dt>No-op</dt><dd>{review.no_op ? 'Yes' : 'No'}</dd>
                <dt>Admissible</dt><dd>{review.admissible ? 'Yes' : 'No'}</dd>
                <dt>Force reviewed</dt><dd>{review.force ? 'Yes' : 'No'}</dd>
                <dt>Requires force</dt><dd>{review.requires_force ? 'Yes' : 'No'}</dd>
              </dl>
              <dl className="facts compact-facts">
                <dt>GPU before</dt><dd>{displayNumber(review.budget.before.used)} / {displayNumber(review.budget.before.ceiling)}</dd>
                <dt>GPU after</dt><dd>{displayNumber(review.budget.after.used)} / {displayNumber(review.budget.after.ceiling)}</dd>
                <dt>Start</dt><dd>{review.start.length ? review.start.join(', ') : '—'}</dd>
                <dt>Stop</dt><dd>{review.stop.length ? review.stop.join(', ') : '—'}</dd>
                <dt>Prerequisites</dt><dd>{review.prerequisites.length ? review.prerequisites.join(', ') : '—'}</dd>
              </dl>
            </div>

            {review.impact.length ? (
              <div className="impact-block">
                <strong>Public impact</strong>
                <ul>
                  {review.impact.map((item) => (
                    <li key={`${item.service_key}:${item.action}`}>{item.service_key}: {item.action} ({item.criticality ?? 'unspecified'})</li>
                  ))}
                </ul>
              </div>
            ) : null}

            {review.reason ? <Alert isInline variant={review.admissible ? 'info' : 'warning'} title="Plan 판단">{review.reason}</Alert> : null}

            <div className="review-actions">
              <Button variant="secondary" onClick={() => setReview(null)} isDisabled={actionPending}>취소</Button>
              {review.requires_force && !review.force ? (
                <Button
                  variant="warning"
                  isDisabled={actionPending}
                  onClick={() => planMutation.mutate({
                    serviceKey: review.service_key,
                    desiredState: review.desired_state,
                    force: true,
                  })}
                >
                  Force 포함 Plan 다시 계산
                </Button>
              ) : null}
              <Button
                variant="primary"
                isDisabled={!review.admissible || actionPending}
                onClick={() => applyMutation.mutate(review)}
              >
                {applyMutation.isPending ? '적용·검증 중…' : '검토한 Plan 적용'}
              </Button>
            </div>
          </CardBody>
        </Card>
      ) : null}

      {operationId !== null ? (
        <Card>
          <CardTitle>Operation evidence</CardTitle>
          <CardBody>
            {operationQuery.isPending ? <div className="inline-loading"><Spinner size="md" aria-label="Operation evidence loading" /> Durable operation evidence를 불러오는 중입니다.</div> : null}
            {operationQuery.isError ? <Alert isInline variant="danger" title="Operation evidence를 불러오지 못했습니다.">{errorMessage(operationQuery.error)}</Alert> : null}
            {operationQuery.data ? (
              <>
                <dl className="facts operation-facts">
                  <dt>Operation</dt><dd>{operationQuery.data.operation_id}</dd>
                  <dt>Status</dt><dd>{operationQuery.data.status}</dd>
                  <dt>Phase</dt><dd>{operationQuery.data.phase}</dd>
                  <dt>Runtime</dt><dd>{operationQuery.data.service_key}</dd>
                  <dt>Desired state</dt><dd>{operationQuery.data.desired_state}</dd>
                  <dt>Reviewed plan</dt><dd>{operationQuery.data.reviewed_plan ? 'Yes' : 'No'}</dd>
                  <dt>Actor</dt><dd>{operationQuery.data.actor.actor_id}</dd>
                  <dt>Request ID</dt><dd>{operationQuery.data.request_id}</dd>
                  <dt>Verification</dt><dd>{verificationSummary(operationQuery.data)}</dd>
                  <dt>Durable</dt><dd>{operationQuery.data.durable ? 'Yes' : 'No'}</dd>
                </dl>
                {operationQuery.data.verification ? <pre className="evidence-json">{JSON.stringify(operationQuery.data.verification, null, 2)}</pre> : null}
              </>
            ) : null}
          </CardBody>
        </Card>
      ) : null}
    </section>
  );
}
