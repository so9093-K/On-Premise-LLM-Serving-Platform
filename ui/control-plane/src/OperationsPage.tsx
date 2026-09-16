import { useEffect } from 'react';
import { Alert, Button, Card, CardBody, CardTitle, Label, Spinner } from '@patternfly/react-core';
import { useQuery } from '@tanstack/react-query';

import {
  ApiError,
  fetchConfigurationHistory,
  fetchMainModelOperations,
  fetchRuntimeOperations,
  type ConfigurationHistoryItem,
  type MainModelOperation,
  type RuntimeOperation,
} from './api';

type OperationsPageProps = {
  token: string | null;
  onUnauthorized: () => void;
  deploymentFeatures: readonly string[];
};

type LabelColor = 'blue' | 'green' | 'orange' | 'red' | 'grey';

function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.code ? `${error.code}: ${error.message}` : error.message;
  }
  return error instanceof Error ? error.message : '요청에 실패했습니다.';
}

function formatTimestamp(value: number): string {
  return new Date(value * 1000).toLocaleString('ko-KR');
}

function runtimeStatusColor(status: RuntimeOperation['status']): LabelColor {
  if (status === 'verified') return 'green';
  if (status === 'noop') return 'grey';
  if (status === 'rejected') return 'orange';
  if (status === 'pending') return 'blue';
  return 'red';
}

function mainModelStatusColor(status: MainModelOperation['status']): LabelColor {
  if (status === 'completed') return 'green';
  if (status === 'failed') return 'orange';
  if (status === 'rollback_failed') return 'red';
  return 'blue';
}

function configurationStatusColor(status: ConfigurationHistoryItem['status']): LabelColor {
  if (status === 'verified') return 'green';
  if (status === 'noop') return 'grey';
  if (status === 'rejected') return 'orange';
  if (status === 'recovered_after_restart') return 'blue';
  if (status === 'pending') return 'blue';
  return 'red';
}

function runtimeVerification(value: unknown): string {
  if (typeof value !== 'object' || value === null) return '—';
  const converged = (value as Record<string, unknown>).converged;
  if (converged === true) return 'converged';
  if (converged === false) return 'not converged';
  return 'recorded';
}

function mainModelFailure(operation: MainModelOperation): string {
  if (operation.error && operation.rollback_error) {
    return `${operation.error} · rollback: ${operation.rollback_error}`;
  }
  if (operation.error) return operation.error;
  if (operation.rollback_error) return `rollback: ${operation.rollback_error}`;
  return '—';
}

function configurationVerification(item: ConfigurationHistoryItem): string {
  if (item.verification === null) return '—';
  return item.verification.synchronized ? 'synchronized' : 'not synchronized';
}

function SourceLoading({ label }: { label: string }) {
  return <div className="inline-loading"><Spinner size="md" aria-label={`${label} operations loading`} /> {label} operations를 불러오는 중입니다.</div>;
}

export function OperationsPage({ token, onUnauthorized, deploymentFeatures }: OperationsPageProps) {
  const authClass = token === null ? 'anonymous' : 'authenticated';
  const runtimeEnabled = deploymentFeatures.includes('runtime_control');
  const mainModelEnabled = deploymentFeatures.includes('model_switching');
  const pollWhileVisible = () => document.visibilityState === 'visible' ? 10_000 : false;

  const runtimeQuery = useQuery({
    queryKey: ['operations', 'runtime', authClass],
    queryFn: () => fetchRuntimeOperations(token),
    enabled: runtimeEnabled,
    retry: false,
    refetchInterval: pollWhileVisible,
    refetchIntervalInBackground: false,
  });
  const mainModelQuery = useQuery({
    queryKey: ['operations', 'main-model', authClass],
    queryFn: () => fetchMainModelOperations(token),
    enabled: mainModelEnabled,
    retry: false,
    refetchInterval: pollWhileVisible,
    refetchIntervalInBackground: false,
  });
  const configurationQuery = useQuery({
    queryKey: ['operations', 'configuration', authClass],
    queryFn: () => fetchConfigurationHistory(token),
    retry: false,
    refetchInterval: pollWhileVisible,
    refetchIntervalInBackground: false,
  });

  useEffect(() => {
    if (
      isUnauthorized(runtimeQuery.error)
      || isUnauthorized(mainModelQuery.error)
      || isUnauthorized(configurationQuery.error)
    ) {
      onUnauthorized();
    }
  }, [configurationQuery.error, mainModelQuery.error, onUnauthorized, runtimeQuery.error]);

  const isRefreshing = (
    (runtimeEnabled && runtimeQuery.isFetching)
    || (mainModelEnabled && mainModelQuery.isFetching)
    || configurationQuery.isFetching
  );

  return (
    <section className="runtime-page">
      <div className="page-heading">
        <div>
          <h1>Operations</h1>
          <p>각 control domain이 소유하는 최근 operation evidence를 한곳에서 조회합니다. 이 화면은 mutation이나 새로운 global operation ledger를 소유하지 않습니다.</p>
        </div>
        <Button
          variant="secondary"
          isDisabled={isRefreshing}
          onClick={() => {
            if (runtimeEnabled) void runtimeQuery.refetch();
            if (mainModelEnabled) void mainModelQuery.refetch();
            void configurationQuery.refetch();
          }}
        >
          {isRefreshing ? '새로고침 중…' : '새로고침'}
        </Button>
      </div>

      <Card>
        <CardTitle>Runtime transitions</CardTitle>
        <CardBody>
          <p className="configuration-help">Runtime start/stop operation journal의 최근 50개를 보여줍니다. 각 record의 Durability가 persistent evidence 여부를 명시하며, 더 오래된 기록은 Runtime history contract가 계속 보존합니다.</p>
          {!runtimeEnabled ? (
            <Alert isInline variant="info" title="이 배포는 Runtime Control을 제공하지 않습니다.">
              Bootstrap capability에 `runtime_control`이 없어 Runtime operation API를 호출하지 않습니다.
            </Alert>
          ) : runtimeQuery.isPending ? (
            <SourceLoading label="Runtime" />
          ) : runtimeQuery.isError ? (
            <Alert isInline variant="danger" title="Runtime operations를 불러오지 못했습니다.">{errorMessage(runtimeQuery.error)}</Alert>
          ) : runtimeQuery.data.items.length === 0 ? (
            <p>기록된 Runtime transition operation이 없습니다.</p>
          ) : (
            <>
              <div className="table-scroll">
                <table className="runtime-table">
                  <thead><tr><th>Updated</th><th>Runtime</th><th>Intent</th><th>Status</th><th>Verification</th><th>Durability</th></tr></thead>
                  <tbody>
                    {runtimeQuery.data.items.map((operation) => (
                      <tr key={operation.operation_id}>
                        <td>{formatTimestamp(operation.updated_at)}<small><code>{operation.operation_id}</code></small></td>
                        <td><strong>{operation.service_key}</strong></td>
                        <td>{operation.desired_state}{operation.force ? ' · force' : ''}</td>
                        <td><Label color={runtimeStatusColor(operation.status)}>{operation.status}</Label><small>{operation.phase}</small></td>
                        <td>{runtimeVerification(operation.verification)}</td>
                        <td>{operation.durable ? 'durable' : 'in-memory'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {runtimeQuery.data.next_cursor ? <p className="configuration-help">이 화면은 recent operations만 표시합니다. 더 오래된 Runtime evidence는 server cursor 뒤에 남아 있습니다.</p> : null}
            </>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardTitle>Main Model profile switches</CardTitle>
        <CardBody>
          <p className="configuration-help">Main Model state가 보존하는 bounded recent projection입니다. 장기 audit history가 아닙니다.</p>
          {!mainModelEnabled ? (
            <Alert isInline variant="info" title="이 배포는 Main Model switching을 제공하지 않습니다.">
              Bootstrap capability에 `model_switching`이 없어 Main Model operation API를 호출하지 않습니다.
            </Alert>
          ) : mainModelQuery.isPending ? (
            <SourceLoading label="Main Model" />
          ) : mainModelQuery.isError ? (
            <Alert isInline variant="danger" title="Main Model operations를 불러오지 못했습니다.">{errorMessage(mainModelQuery.error)}</Alert>
          ) : mainModelQuery.data.items.length === 0 ? (
            <p>기록된 Main Model switch operation이 없습니다.</p>
          ) : (
            <div className="table-scroll">
              <table className="runtime-table">
                <thead><tr><th>Updated</th><th>Requested profile</th><th>Previous</th><th>Status</th><th>Stage</th><th>Failure</th><th>Recovery</th></tr></thead>
                <tbody>
                  {mainModelQuery.data.items.map((operation) => (
                    <tr key={operation.id}>
                      <td>{formatTimestamp(operation.updated_at)}<small><code>{operation.id}</code></small></td>
                      <td><strong>{operation.requested_profile}</strong></td>
                      <td>{operation.previous_profile ?? '—'}</td>
                      <td><Label color={mainModelStatusColor(operation.status)}>{operation.status}</Label></td>
                      <td>{operation.stage}</td>
                      <td>{mainModelFailure(operation)}</td>
                      <td>{operation.recovered_after_restart ? 'recovered after restart' : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardTitle>Configuration mutations</CardTitle>
        <CardBody>
          <p className="configuration-help">Configuration durable journal의 최근 page를 읽기 전용으로 보여줍니다. Rollback은 Configuration 화면이 계속 소유합니다.</p>
          {configurationQuery.isPending ? (
            <SourceLoading label="Configuration" />
          ) : configurationQuery.isError ? (
            <Alert isInline variant="danger" title="Configuration operations를 불러오지 못했습니다.">{errorMessage(configurationQuery.error)}</Alert>
          ) : configurationQuery.data.items.length === 0 ? (
            <p>기록된 Configuration mutation이 없습니다.</p>
          ) : (
            <>
              <div className="table-scroll">
                <table className="runtime-table">
                  <thead><tr><th>Updated</th><th>Operation</th><th>Revision</th><th>Status</th><th>Changes</th><th>Verification</th></tr></thead>
                  <tbody>
                    {configurationQuery.data.items.map((operation) => (
                      <tr key={operation.operation_id}>
                        <td>{formatTimestamp(operation.updated_at)}<small><code>{operation.operation_id}</code></small></td>
                        <td><strong>{operation.kind === 'configuration_rollback' ? 'Rollback' : 'Apply'}</strong>{operation.target_revision === null ? null : <small>target revision {operation.target_revision}</small>}</td>
                        <td>{operation.base_revision} → {operation.applied_revision ?? operation.candidate_revision}</td>
                        <td><Label color={configurationStatusColor(operation.status)}>{operation.status}</Label><small>{operation.phase}</small></td>
                        <td>{operation.changes.length}</td>
                        <td>{configurationVerification(operation)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {configurationQuery.data.next_cursor ? <p className="configuration-help">이 화면은 recent operations만 표시합니다. 전체 Configuration audit navigation은 별도 History surface가 소유합니다.</p> : null}
            </>
          )}
        </CardBody>
      </Card>
    </section>
  );
}
