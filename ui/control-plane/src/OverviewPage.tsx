import { useEffect } from 'react';
import { Alert, Button, Card, CardBody, CardTitle, Label, Spinner } from '@patternfly/react-core';
import { useQuery } from '@tanstack/react-query';

import {
  fetchMainModel,
  fetchRuntimes,
  type BootstrapResponse,
  type RuntimeListResponse,
} from './api';
import { apiErrorMessage, isUnauthorized } from './apiFeedback';

type OverviewPageProps = {
  bootstrap: BootstrapResponse;
  token: string | null;
  onUnauthorized: () => void;
};

function displayNumber(value: number | null | undefined): string {
  return typeof value === 'number' ? value.toFixed(2) : '—';
}

function runtimeStateCount(
  runtimes: RuntimeListResponse['runtimes'],
  state: RuntimeListResponse['runtimes'][number]['state'],
): number {
  return runtimes.filter((runtime) => runtime.state === state).length;
}

function implementationStatusLabel(status: string): string {
  if (status === 'implemented') return 'Implemented';
  if (status === 'planned') return 'Planned';
  return status;
}

function qualificationStatusLabel(status: string): string {
  if (status === 'verified') return 'Verified';
  if (status === 'unverified') return 'Unverified';
  return status;
}

function featureLabel(feature: string): string {
  const labels: Record<string, string> = {
    chat: 'Chat',
    embeddings: 'Embeddings',
    retrieval: 'Retrieval',
    risk: 'Risk signals',
    runtime_control: 'Runtime control',
    model_switching: 'Model switching',
    gpu_admission: 'GPU admission',
  };
  return labels[feature] ?? feature;
}

export function OverviewPage({ bootstrap, token, onUnauthorized }: OverviewPageProps) {
  const authClass = token === null ? 'anonymous' : 'authenticated';
  const runtimeControlEnabled = bootstrap.deployment.features.includes('runtime_control');
  const modelSwitchingEnabled = bootstrap.deployment.features.includes('model_switching');

  const runtimesQuery = useQuery({
    queryKey: ['runtime-control', 'runtimes', authClass],
    queryFn: () => fetchRuntimes(token),
    enabled: runtimeControlEnabled,
    retry: false,
    staleTime: 10_000,
    refetchInterval: () => document.visibilityState === 'visible' ? 10_000 : false,
    refetchIntervalInBackground: false,
  });

  const mainModelQuery = useQuery({
    queryKey: ['main-model', 'status', authClass],
    queryFn: () => fetchMainModel(token),
    enabled: modelSwitchingEnabled,
    retry: false,
    staleTime: 10_000,
    refetchInterval: () => document.visibilityState === 'visible' ? 10_000 : false,
    refetchIntervalInBackground: false,
  });

  useEffect(() => {
    if (isUnauthorized(runtimesQuery.error) || isUnauthorized(mainModelQuery.error)) {
      onUnauthorized();
    }
  }, [mainModelQuery.error, onUnauthorized, runtimesQuery.error]);

  const refreshing = runtimesQuery.isFetching || mainModelQuery.isFetching;
  const refresh = () => {
    if (runtimeControlEnabled) void runtimesQuery.refetch();
    if (modelSwitchingEnabled) void mainModelQuery.refetch();
  };

  const release = bootstrap.platform.release_id ?? 'development';
  const runtimes = runtimesQuery.data?.runtimes ?? [];
  const budget = runtimesQuery.data?.budget;
  const mainModel = mainModelQuery.data;

  return (
    <section className="overview-page">
      <div className="page-heading">
        <div>
          <h1>Overview</h1>
          <p>현재 모델, Runtime, Configuration, 접근 방식과 관측 상태를 한곳에서 확인합니다.</p>
        </div>
        <Button variant="secondary" onClick={refresh} isDisabled={refreshing}>
          {refreshing ? '새로고침 중…' : '새로고침'}
        </Button>
      </div>

      {runtimesQuery.isError ? (
        <Alert isInline variant="warning" title="Runtime 상태를 조회하지 못했습니다.">
          {apiErrorMessage(runtimesQuery.error)}
        </Alert>
      ) : null}
      {mainModelQuery.isError ? (
        <Alert isInline variant="warning" title="Main Model 상태를 조회하지 못했습니다.">
          {apiErrorMessage(mainModelQuery.error)}
        </Alert>
      ) : null}

      <div className="page-grid overview-grid">
        <Card>
          <CardTitle>Main Model</CardTitle>
          <CardBody>
            {!modelSwitchingEnabled ? (
              <dl className="facts">
                <dt>Runtime ownership</dt><dd>{bootstrap.deployment.lifecycle_owner}</dd>
                <dt>Control mode</dt><dd>{bootstrap.deployment.control_mode}</dd>
              </dl>
            ) : mainModelQuery.isPending ? (
              <div className="inline-loading"><Spinner size="md" aria-label="Main Model 상태 loading" /> 상태를 불러오는 중입니다.</div>
            ) : mainModel ? (
              <dl className="facts">
                <dt>Public model alias</dt><dd>{mainModel.public_model}</dd>
                <dt>Active profile</dt><dd>{mainModel.active_profile?.display_name ?? '—'}</dd>
                <dt>Gate</dt>
                <dd><Label color={mainModel.gate === 'open' ? 'green' : 'orange'}>{mainModel.gate}</Label></dd>
                <dt>Runtime state</dt><dd>{mainModel.runtime_state}</dd>
                <dt>Observed status</dt><dd>{mainModel.observed_runtime?.status ?? 'unavailable'}</dd>
                <dt>Observed health</dt><dd>{mainModel.observed_runtime?.health ?? '—'}</dd>
              </dl>
            ) : (
              <p className="overview-muted">Main Model 상태를 표시할 수 없습니다.</p>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardTitle>Model Runtimes & GPU</CardTitle>
          <CardBody>
            {!runtimeControlEnabled ? (
              <dl className="facts">
                <dt>Runtime ownership</dt><dd>{bootstrap.deployment.lifecycle_owner}</dd>
                <dt>Control mode</dt><dd>{bootstrap.deployment.control_mode}</dd>
              </dl>
            ) : runtimesQuery.isPending ? (
              <div className="inline-loading"><Spinner size="md" aria-label="Runtime 상태 loading" /> 상태를 불러오는 중입니다.</div>
            ) : (
              <dl className="facts">
                <dt>Active</dt><dd>{runtimeStateCount(runtimes, 'active')}</dd>
                <dt>Starting</dt><dd>{runtimeStateCount(runtimes, 'starting')}</dd>
                <dt>Stopped</dt><dd>{runtimeStateCount(runtimes, 'stopped')}</dd>
                <dt>GPU ceiling</dt><dd>{displayNumber(budget?.ceiling)}</dd>
                <dt>GPU used</dt><dd>{displayNumber(budget?.used)}</dd>
                <dt>GPU free</dt><dd>{displayNumber(budget?.free)}</dd>
              </dl>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardTitle>Configuration</CardTitle>
          <CardBody>
            <dl className="facts">
              <dt>Schema</dt><dd>v{bootstrap.configuration.schema_version}</dd>
              <dt>Revision</dt><dd>{bootstrap.configuration.revision}</dd>
              <dt>Write</dt>
              <dd><Label color={bootstrap.configuration.write_available ? 'green' : 'orange'}>
                {bootstrap.configuration.write_available ? 'available' : 'unavailable'}
              </Label></dd>
            </dl>
          </CardBody>
        </Card>

        <Card>
          <CardTitle>Runtime environment & access</CardTitle>
          <CardBody>
            <dl className="facts">
              <dt>Environment</dt><dd>{bootstrap.deployment.display_name}</dd>
              <dt>Environment ID</dt><dd>{bootstrap.deployment.target}</dd>
              <dt>Runtime backend</dt><dd>{bootstrap.deployment.runtime_backend}</dd>
              <dt>Implementation</dt><dd>{implementationStatusLabel(bootstrap.deployment.implementation_status)}</dd>
              <dt>Qualification</dt><dd>{qualificationStatusLabel(bootstrap.deployment.qualification_status)}</dd>
              <dt>Access profile</dt><dd>{bootstrap.access.profile}</dd>
              <dt>Admin auth</dt><dd>{bootstrap.access.admin_auth_required ? 'required' : 'not required'}</dd>
            </dl>
          </CardBody>
        </Card>

        <Card>
          <CardTitle>Platform & observability</CardTitle>
          <CardBody>
            <dl className="facts">
              <dt>Version</dt><dd>{bootstrap.platform.version}</dd>
              <dt>Release</dt><dd>{release}</dd>
              <dt>Observability</dt><dd>{bootstrap.monitoring.available ? 'available' : 'unavailable'}</dd>
              <dt>Grafana</dt><dd>{bootstrap.monitoring.grafana_available ? 'available' : 'unavailable'}</dd>
            </dl>
          </CardBody>
        </Card>

        <Card>
          <CardTitle>Capabilities</CardTitle>
          <CardBody className="capability-list">
            {bootstrap.deployment.features.map((feature) => <Label key={feature}>{featureLabel(feature)}</Label>)}
          </CardBody>
        </Card>
      </div>
    </section>
  );
}
