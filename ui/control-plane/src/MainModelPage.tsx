import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Card, CardBody, CardTitle, Label, Spinner } from '@patternfly/react-core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  fetchMainModel,
  fetchMainModelOperation,
  fetchMainModelProfiles,
  switchMainModel,
  type MainModelProfile,
} from './api';
import { apiErrorMessage, isUnauthorized } from './apiFeedback';
import {
  isMainModelOperationTerminal,
  mainModelProfileRequiresConfirmation,
  mainModelProfileSwitchable,
  mainModelSwitchRequest,
} from './mainModelSafety';

type MainModelPageProps = {
  token: string | null;
  onUnauthorized: () => void;
};

function compatibilityVariant(status: string): 'green' | 'orange' | 'red' | 'grey' {
  if (status === 'compatible') return 'green';
  if (status === 'incompatible') return 'red';
  if (status === 'unknown') return 'orange';
  return 'grey';
}

function compatibilityLabel(status: string): string {
  if (status === 'compatible') return 'Compatible';
  if (status === 'incompatible') return 'Incompatible';
  if (status === 'unknown') return 'Unknown';
  return status;
}

function qualificationVariant(status: string): 'green' | 'orange' | 'grey' {
  if (status === 'verified') return 'green';
  if (status === 'unverified') return 'orange';
  return 'grey';
}

function qualificationLabel(status: string): string {
  if (status === 'verified') return 'Verified';
  if (status === 'unverified') return 'Unverified · confirmation required';
  return status;
}

function operationVariant(status: string): 'success' | 'warning' | 'danger' | 'info' {
  if (status === 'completed') return 'success';
  if (status === 'failed') return 'warning';
  if (status === 'rollback_failed') return 'danger';
  return 'info';
}

export function MainModelPage({ token, onUnauthorized }: MainModelPageProps) {
  const queryClient = useQueryClient();
  const [reviewProfileId, setReviewProfileId] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [operationId, setOperationId] = useState<string | null>(null);
  const authClass = token === null ? 'anonymous' : 'authenticated';

  const statusQuery = useQuery({
    queryKey: ['main-model', 'status', authClass],
    queryFn: () => fetchMainModel(token),
    retry: false,
    refetchInterval: () => document.visibilityState === 'visible' ? 10_000 : false,
    refetchIntervalInBackground: false,
  });
  const profilesQuery = useQuery({
    queryKey: ['main-model', 'profiles', authClass],
    queryFn: () => fetchMainModelProfiles(token),
    retry: false,
    staleTime: 10_000,
  });

  useEffect(() => {
    if (isUnauthorized(statusQuery.error) || isUnauthorized(profilesQuery.error)) {
      onUnauthorized();
    }
  }, [onUnauthorized, profilesQuery.error, statusQuery.error]);

  const operationQuery = useQuery({
    queryKey: ['main-model', 'operation', operationId, authClass],
    queryFn: () => {
      if (operationId === null) throw new Error('operation id is required');
      return fetchMainModelOperation(token, operationId);
    },
    enabled: operationId !== null,
    retry: false,
    refetchInterval: (query) => {
      const operation = query.state.data;
      return operation && isMainModelOperationTerminal(operation) ? false : 2_000;
    },
    refetchIntervalInBackground: false,
  });

  useEffect(() => {
    if (isUnauthorized(operationQuery.error)) onUnauthorized();
  }, [onUnauthorized, operationQuery.error]);

  useEffect(() => {
    const operation = operationQuery.data;
    if (!operation || !isMainModelOperationTerminal(operation)) return;
    void queryClient.invalidateQueries({ queryKey: ['main-model', 'status'] });
    void queryClient.invalidateQueries({ queryKey: ['main-model', 'profiles'] });
  }, [operationQuery.data, queryClient]);

  const profiles = profilesQuery.data?.profiles ?? [];
  const reviewProfile = useMemo(
    () => profiles.find((profile) => profile.id === reviewProfileId) ?? null,
    [profiles, reviewProfileId],
  );

  const switchMutation = useMutation({
    mutationFn: ({ profile, accepted }: { profile: MainModelProfile; accepted: boolean }) =>
      switchMainModel(token, mainModelSwitchRequest(profile, accepted)),
    retry: false,
    onMutate: () => setActionError(null),
    onSuccess: async (result) => {
      setOperationId(result.operation_id);
      setReviewProfileId(null);
      setConfirmed(false);
      await queryClient.invalidateQueries({ queryKey: ['main-model', 'status'] });
      await queryClient.invalidateQueries({ queryKey: ['main-model', 'profiles'] });
    },
    onError: (error) => {
      if (isUnauthorized(error)) {
        onUnauthorized();
        return;
      }
      setActionError(apiErrorMessage(error));
    },
  });

  if (statusQuery.isPending || profilesQuery.isPending) {
    return <div className="inline-loading"><Spinner size="lg" aria-label="Main Model 상태 loading" /> Main Model 상태를 불러오는 중입니다.</div>;
  }
  if (statusQuery.isError) {
    return <Alert isInline variant="danger" title="Main Model 상태를 불러오지 못했습니다.">{apiErrorMessage(statusQuery.error)}</Alert>;
  }
  if (profilesQuery.isError) {
    return <Alert isInline variant="danger" title="Main Model 프로필을 불러오지 못했습니다.">{apiErrorMessage(profilesQuery.error)}</Alert>;
  }

  const status = statusQuery.data;
  const active = status.active_profile;
  const observed = status.observed_runtime;
  const locked = status.profile_locked;
  const requiresConfirmation = reviewProfile ? mainModelProfileRequiresConfirmation(reviewProfile) : false;

  return (
    <section className="runtime-page">
      <div className="page-heading">
        <div>
          <h1>Main Model</h1>
          <p>프로필의 검증 상태와 입력 capability를 확인한 뒤 Main Model 전환을 요청하고 진행 상태를 추적합니다. Runtime 시작·정지는 Runtimes 화면에서 수행합니다.</p>
        </div>
        <Button
          variant="secondary"
          onClick={() => {
            void statusQuery.refetch();
            void profilesQuery.refetch();
          }}
          isDisabled={statusQuery.isFetching || profilesQuery.isFetching}
        >
          {statusQuery.isFetching || profilesQuery.isFetching ? '새로고침 중…' : '새로고침'}
        </Button>
      </div>

      {locked ? (
        <Alert isInline variant="warning" title="이 배포는 Main Model profile이 잠겨 있습니다.">
          상태와 profile metadata는 조회할 수 있지만 Console에서 profile switch를 시작할 수 없습니다.
        </Alert>
      ) : null}
      {status.state_recovery_error ? (
        <Alert isInline variant="danger" title="Main Model state recovery 오류가 기록되어 있습니다.">
          {status.state_recovery_error}
        </Alert>
      ) : null}
      {actionError ? <Alert isInline variant="danger" title="Main Model 전환 요청에 실패했습니다.">{actionError}</Alert> : null}

      <Card>
        <CardTitle>Current control state</CardTitle>
        <CardBody>
          <dl className="facts">
            <dt>Public model alias</dt><dd>{status.public_model}</dd>
            <dt>Active profile</dt><dd>{active?.display_name ?? '—'}{active ? ` (${active.id})` : ''}</dd>
            <dt>Gate</dt><dd><Label color={status.gate === 'open' ? 'green' : 'orange'}>{status.gate}</Label></dd>
            <dt>Runtime state</dt><dd>{status.runtime_state}</dd>
            <dt>Boot profile</dt><dd>{status.boot_profile}</dd>
            <dt>Observed runtime</dt><dd>{observed?.status ?? 'unavailable'}</dd>
            <dt>Observed health</dt><dd>{observed?.health ?? '—'}</dd>
            <dt>Observed profile</dt><dd>{observed?.profile_id ?? '—'}</dd>
          </dl>
        </CardBody>
      </Card>

      <Card>
        <CardTitle>Available profiles</CardTitle>
        <CardBody>
          <div className="table-scroll">
            <table className="runtime-table">
              <thead>
                <tr><th>Profile</th><th>Compatibility</th><th>Qualification</th><th>Inputs</th><th>VRAM</th><th>State</th><th>Action</th></tr>
              </thead>
              <tbody>
                {profiles.map((profile) => {
                  const switchable = !locked && mainModelProfileSwitchable(profile);
                  return (
                    <tr key={profile.id}>
                      <td><strong>{profile.display_name}</strong><br /><code>{profile.id}</code></td>
                      <td><Label color={compatibilityVariant(profile.compatibility.status)}>{compatibilityLabel(profile.compatibility.status)}</Label></td>
                      <td><Label color={qualificationVariant(profile.qualification.status)}>{qualificationLabel(profile.qualification.status)}</Label></td>
                      <td>{profile.capabilities.deployed_input.join(', ')}</td>
                      <td>{profile.vram_fraction.toFixed(2)}</td>
                      <td>{profile.active ? <Label color="green">active</Label> : 'available'}</td>
                      <td>
                        <Button
                          size="sm"
                          variant="secondary"
                          isDisabled={!switchable || switchMutation.isPending}
                          onClick={() => {
                            setReviewProfileId(profile.id);
                            setConfirmed(false);
                            setActionError(null);
                          }}
                        >검토</Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </CardBody>
      </Card>

      {reviewProfile ? (
        <Card className="review-card">
          <CardTitle>Profile switch review</CardTitle>
          <CardBody>
            <dl className="facts">
              <dt>Target</dt><dd>{reviewProfile.display_name} ({reviewProfile.id})</dd>
              <dt>Upstream</dt><dd>{reviewProfile.upstream_model_id}</dd>
              <dt>Revision</dt><dd><code>{reviewProfile.revision}</code></dd>
              <dt>Compatibility</dt><dd>{compatibilityLabel(reviewProfile.compatibility.status)}</dd>
              <dt>Qualification</dt><dd>{qualificationLabel(reviewProfile.qualification.status)}</dd>
              <dt>Inputs</dt><dd>{reviewProfile.capabilities.deployed_input.join(', ')}</dd>
              <dt>VRAM fraction</dt><dd>{reviewProfile.vram_fraction.toFixed(2)}</dd>
            </dl>
            {requiresConfirmation ? (
              <Alert isInline variant="warning" title="추가 확인이 필요한 프로필입니다.">
                <label>
                  <input
                    type="checkbox"
                    checked={confirmed}
                    onChange={(event) => setConfirmed(event.currentTarget.checked)}
                  />{' '}
                  이 프로필의 qualification이 현재 배포에서 verified가 아님을 확인했습니다.
                </label>
              </Alert>
            ) : null}
            <div className="review-actions">
              <Button variant="secondary" onClick={() => setReviewProfileId(null)} isDisabled={switchMutation.isPending}>취소</Button>
              <Button
                variant="primary"
                isDisabled={switchMutation.isPending || (requiresConfirmation && !confirmed)}
                onClick={() => switchMutation.mutate({ profile: reviewProfile, accepted: confirmed })}
              >{switchMutation.isPending ? '요청 중…' : '전환 요청'}</Button>
            </div>
          </CardBody>
        </Card>
      ) : null}

      {operationId ? (
        <Card>
          <CardTitle>Switch progress</CardTitle>
          <CardBody>
            {operationQuery.isPending ? (
              <div className="inline-loading"><Spinner size="md" aria-label="Main Model switch loading" /> 전환 상태를 확인하는 중입니다.</div>
            ) : operationQuery.isError ? (
              <Alert isInline variant="danger" title="전환 상태를 불러오지 못했습니다.">{apiErrorMessage(operationQuery.error)}</Alert>
            ) : operationQuery.data ? (
              <>
                <Alert isInline variant={operationVariant(operationQuery.data.status)} title={`Operation ${operationQuery.data.status}`}>
                  stage: {operationQuery.data.stage}
                  {operationQuery.data.error ? ` · ${operationQuery.data.error}` : ''}
                  {operationQuery.data.rollback_error ? ` · rollback: ${operationQuery.data.rollback_error}` : ''}
                </Alert>
                <dl className="facts">
                  <dt>Operation ID</dt><dd><code>{operationQuery.data.id}</code></dd>
                  <dt>Requested profile</dt><dd>{operationQuery.data.requested_profile}</dd>
                  <dt>Previous profile</dt><dd>{operationQuery.data.previous_profile ?? '—'}</dd>
                  <dt>Status</dt><dd>{operationQuery.data.status}</dd>
                  <dt>Stage</dt><dd>{operationQuery.data.stage}</dd>
                </dl>
              </>
            ) : null}
          </CardBody>
        </Card>
      ) : status.last_operation ? (
        <Card>
          <CardTitle>Latest switch</CardTitle>
          <CardBody>
            <dl className="facts">
              <dt>Operation ID</dt><dd><code>{status.last_operation.id}</code></dd>
              <dt>Requested profile</dt><dd>{status.last_operation.requested_profile}</dd>
              <dt>Status</dt><dd>{status.last_operation.status}</dd>
              <dt>Stage</dt><dd>{status.last_operation.stage}</dd>
            </dl>
          </CardBody>
        </Card>
      ) : null}
    </section>
  );
}
