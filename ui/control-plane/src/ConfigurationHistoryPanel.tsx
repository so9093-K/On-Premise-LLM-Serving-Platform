import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Card, CardBody, CardTitle, Label, Spinner } from '@patternfly/react-core';
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query';

import {
  ApiError,
  applyConfigurationRollback,
  fetchConfigurationHistory,
  planConfigurationRollback,
  type ConfigurationHistoryItem,
  type ConfigurationRollbackApplyResponse,
  type ConfigurationRollbackPlanResponse,
} from './api';
import { apiErrorMessage, isUnauthorized } from './apiFeedback';
import {
  configurationRollbackApplyRequest,
  configurationRollbackTargetRevision,
} from './configurationSafety';

type ConfigurationHistoryPanelProps = {
  token: string | null;
  onUnauthorized: () => void;
  currentRevision: number;
  etag: string;
  writeAvailable: boolean;
  locked: boolean;
  onReviewActiveChange: (active: boolean) => void;
};

type RollbackIntent = {
  baseRevision: number;
  targetRevision: number;
  etag: string;
};

type ReviewedRollback = {
  plan: ConfigurationRollbackPlanResponse;
  etag: string;
};

function isRevisionConflict(error: unknown): boolean {
  return error instanceof ApiError
    && error.status === 412
    && error.code === 'CONFIG_REVISION_CONFLICT';
}

function errorMessage(error: unknown): string {
  if (isRevisionConflict(error)) {
    return 'Configuration revision이 변경되어 기존 rollback 검토를 사용할 수 없습니다. 최신 상태에서 새 Plan을 검토하세요.';
  }
  return apiErrorMessage(error);
}

function formatTimestamp(seconds: number): string {
  return new Date(seconds * 1000).toLocaleString();
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function statusColor(status: string): 'grey' | 'green' | 'orange' | 'red' | 'blue' {
  if (status === 'verified' || status === 'noop' || status === 'recovered_after_restart') return 'green';
  if (status === 'pending') return 'blue';
  if (status.includes('failed') || status.includes('interrupted')) return 'red';
  if (status === 'rejected') return 'orange';
  return 'grey';
}

function revisionLabel(item: ConfigurationHistoryItem): string {
  const applied = item.applied_revision === null ? '—' : `r${item.applied_revision}`;
  return `r${item.base_revision} → ${applied}`;
}

export function ConfigurationHistoryPanel({
  token,
  onUnauthorized,
  currentRevision,
  etag,
  writeAvailable,
  locked,
  onReviewActiveChange,
}: ConfigurationHistoryPanelProps) {
  const queryClient = useQueryClient();
  const [review, setReview] = useState<ReviewedRollback | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [lastRollback, setLastRollback] = useState<ConfigurationRollbackApplyResponse | null>(null);
  const authClass = token === null ? 'anonymous' : 'authenticated';

  const historyQuery = useInfiniteQuery({
    queryKey: ['configuration', 'history', authClass],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => fetchConfigurationHistory(token, pageParam as string | null),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    retry: false,
    staleTime: 5_000,
  });

  useEffect(() => {
    if (isUnauthorized(historyQuery.error)) onUnauthorized();
  }, [historyQuery.error, onUnauthorized]);

  const historyItems = useMemo(
    () => historyQuery.data?.pages.flatMap((page) => page.items) ?? [],
    [historyQuery.data],
  );

  const planMutation = useMutation({
    mutationFn: (intent: RollbackIntent) => planConfigurationRollback(token, {
      base_revision: intent.baseRevision,
      target_revision: intent.targetRevision,
    }),
    retry: false,
    onMutate: () => {
      setActionError(null);
      setLastRollback(null);
      setReview(null);
      onReviewActiveChange(true);
    },
    onSuccess: (plan, intent) => setReview({ plan, etag: intent.etag }),
    onError: async (error) => {
      onReviewActiveChange(false);
      if (isUnauthorized(error)) {
        onUnauthorized();
        return;
      }
      setActionError(errorMessage(error));
      if (isRevisionConflict(error)) {
        await queryClient.invalidateQueries({ queryKey: ['configuration'] });
      }
    },
  });

  const applyMutation = useMutation({
    mutationFn: (reviewed: ReviewedRollback) => applyConfigurationRollback(
      token,
      reviewed.etag,
      configurationRollbackApplyRequest(reviewed.plan),
    ),
    retry: false,
    onMutate: () => setActionError(null),
    onSuccess: async (result) => {
      setLastRollback(result);
      setReview(null);
      onReviewActiveChange(false);
      await queryClient.invalidateQueries({ queryKey: ['configuration'] });
    },
    onError: async (error) => {
      setReview(null);
      onReviewActiveChange(false);
      if (isUnauthorized(error)) {
        onUnauthorized();
        return;
      }
      setActionError(errorMessage(error));
      await queryClient.invalidateQueries({ queryKey: ['configuration'] });
    },
  });

  const rollbackPending = planMutation.isPending || applyMutation.isPending;

  return (
    <>
      <Card>
        <CardTitle>Configuration history</CardTitle>
        <CardBody>
          <p>Durable Configuration journal의 운영자 projection입니다. Rollback은 과거 revision으로 번호를 되돌리지 않고 현재 revision에서 새 mutation을 만듭니다.</p>
          {historyQuery.isPending ? (
            <div className="inline-loading"><Spinner size="md" aria-label="Configuration history loading" /> History를 불러오는 중입니다.</div>
          ) : historyQuery.isError ? (
            <Alert isInline variant="danger" title="Configuration history를 불러오지 못했습니다.">{errorMessage(historyQuery.error)}</Alert>
          ) : historyItems.length === 0 ? (
            <Alert isInline variant="info" title="Configuration history가 없습니다.">아직 기록된 Configuration mutation이 없습니다.</Alert>
          ) : (
            <div className="table-scroll">
              <table className="runtime-table">
                <thead>
                  <tr><th>Time</th><th>Kind</th><th>Status</th><th>Revision</th><th>Changes</th><th>Verification</th><th>Action</th></tr>
                </thead>
                <tbody>
                  {historyItems.map((item) => {
                    const rollbackRevision = configurationRollbackTargetRevision(item, currentRevision);
                    const canRollback = rollbackRevision !== null
                      && writeAvailable
                      && !locked
                      && !rollbackPending
                      && review === null;
                    return (
                      <tr key={item.operation_id}>
                        <td>{formatTimestamp(item.created_at)}</td>
                        <td>{item.kind}</td>
                        <td><Label color={statusColor(item.status)}>{item.status}</Label></td>
                        <td>
                          {revisionLabel(item)}
                          {item.target_revision !== null ? <small>target r{item.target_revision}</small> : null}
                        </td>
                        <td>
                          {item.changes.length}
                          <small>{item.changes.map((change) => change.key).join(', ')}</small>
                        </td>
                        <td>{item.verification ? (item.verification.synchronized ? 'Synchronized' : 'Not synchronized') : '—'}</td>
                        <td>
                          {rollbackRevision !== null ? (
                            <Button
                              size="sm"
                              variant="secondary"
                              isDisabled={!canRollback}
                              onClick={() => planMutation.mutate({
                                baseRevision: currentRevision,
                                targetRevision: rollbackRevision,
                                etag,
                              })}
                            >Rollback to r{rollbackRevision}</Button>
                          ) : '—'}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {historyQuery.hasNextPage ? (
            <div className="review-actions">
              <Button
                variant="secondary"
                isDisabled={historyQuery.isFetchingNextPage}
                onClick={() => void historyQuery.fetchNextPage()}
              >{historyQuery.isFetchingNextPage ? '불러오는 중…' : '이전 기록 더 보기'}</Button>
            </div>
          ) : null}
        </CardBody>
      </Card>

      {actionError ? <Alert isInline variant="danger" title="Configuration rollback을 완료하지 못했습니다.">{actionError}</Alert> : null}

      {review ? (
        <Card className="review-card">
          <CardTitle>Configuration rollback review</CardTitle>
          <CardBody>
            <Alert isInline variant="warning" title={`Revision ${review.plan.target_revision} 상태를 목표로 새 mutation을 생성합니다.`}>
              Revision counter는 과거 번호로 돌아가지 않습니다. 적용에 성공하면 새 revision {review.plan.candidate_revision}이 생성됩니다.
            </Alert>
            <dl className="facts compact-facts">
              <dt>Base revision</dt><dd>{review.plan.base_revision}</dd>
              <dt>Target revision</dt><dd>{review.plan.target_revision}</dd>
              <dt>Candidate revision</dt><dd>{review.plan.candidate_revision}</dd>
              <dt>Would change</dt><dd>{review.plan.would_change ? 'Yes' : 'No'}</dd>
              <dt>Plan digest</dt><dd><code>{review.plan.plan_digest}</code></dd>
            </dl>
            <div className="table-scroll configuration-review-table">
              <table className="runtime-table">
                <thead><tr><th>Key</th><th>Operation</th><th>Operator before</th><th>Operator after</th><th>Effective after</th><th>Risk</th></tr></thead>
                <tbody>
                  {review.plan.changes.map((change) => (
                    <tr key={change.key}>
                      <td><code>{change.key}</code></td>
                      <td>{change.operation}</td>
                      <td>{formatValue(change.operator_before)}</td>
                      <td>{formatValue(change.operator_after)}</td>
                      <td>{formatValue(change.effective_after)}</td>
                      <td>{change.risk}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!review.plan.would_change ? (
              <Alert isInline variant="info" title="Rollback으로 변경되는 operator state가 없습니다.">Apply를 실행하지 않습니다.</Alert>
            ) : null}
            <div className="review-actions">
              <Button
                variant="secondary"
                isDisabled={rollbackPending}
                onClick={() => {
                  setReview(null);
                  onReviewActiveChange(false);
                }}
              >Rollback Plan 닫기</Button>
              <Button
                variant="primary"
                isDanger
                isDisabled={!review.plan.would_change || rollbackPending}
                onClick={() => applyMutation.mutate(review)}
              >{applyMutation.isPending ? 'Rollback 적용·검증 중…' : '검토한 Rollback 적용'}</Button>
            </div>
          </CardBody>
        </Card>
      ) : null}

      {lastRollback ? (
        <Card>
          <CardTitle>Rollback verification</CardTitle>
          <CardBody>
            <dl className="facts operation-facts">
              <dt>Operation</dt><dd><code>{lastRollback.operation_id}</code></dd>
              <dt>Status</dt><dd>{lastRollback.status}</dd>
              <dt>Target revision</dt><dd>{lastRollback.target_revision}</dd>
              <dt>New revision</dt><dd>{lastRollback.revision}</dd>
              <dt>Changed</dt><dd>{lastRollback.changed ? 'Yes' : 'No'}</dd>
              <dt>Store revision</dt><dd>{lastRollback.verification.store_revision ?? '—'}</dd>
              <dt>Resolver revision</dt><dd>{lastRollback.verification.resolver_revision}</dd>
              <dt>Runtime revision</dt><dd>{lastRollback.verification.runtime_revision}</dd>
              <dt>Synchronized</dt><dd>{lastRollback.verification.synchronized ? 'Yes' : 'No'}</dd>
            </dl>
          </CardBody>
        </Card>
      ) : null}
    </>
  );
}
