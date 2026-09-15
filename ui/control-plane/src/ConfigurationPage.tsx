import { useEffect, useMemo, useState } from 'react';
import { Alert, Button, Card, CardBody, CardTitle, Label, Spinner } from '@patternfly/react-core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  ApiError,
  applyConfigurationChange,
  fetchConfigurationEffective,
  fetchConfigurationSchema,
  planConfigurationChange,
  type ConfigurationApplyResponse,
  type ConfigurationChange,
  type ConfigurationEffectiveItem,
  type ConfigurationPlanResponse,
  type ConfigurationSchemaItem,
} from './api';
import {
  configurationApplyRequest,
  configurationItemApplies,
  configurationResetChange,
  configurationSetChange,
  parseConfigurationDraft,
} from './configurationSafety';

type ConfigurationPageProps = {
  token: string | null;
  onUnauthorized: () => void;
  deploymentFeatures: readonly string[];
};

type ReviewedConfiguration = {
  plan: ConfigurationPlanResponse;
  changes: ConfigurationChange[];
  etag: string;
};

type PlanIntent = {
  baseRevision: number;
  etag: string;
  changes: ConfigurationChange[];
};

function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

function isRevisionConflict(error: unknown): boolean {
  return error instanceof ApiError
    && error.status === 412
    && error.code === 'CONFIG_REVISION_CONFLICT';
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError && isRevisionConflict(error)) {
    return `${error.message} Configuration 상태가 검토 시점과 달라졌으므로 새 상태를 조회한 뒤 다시 Plan을 검토하세요.`;
  }
  if (error instanceof ApiError && error.code === 'CONFIGURATION_APPLY_FAILED') {
    const details = typeof error.details === 'object' && error.details !== null
      ? error.details as Record<string, unknown>
      : null;
    const operationId = typeof details?.operation_id === 'string' ? details.operation_id : null;
    return operationId
      ? `${error.message} operation: ${operationId}. 현재 상태를 다시 조회한 뒤 새 Plan을 검토하세요.`
      : `${error.message} 현재 상태를 다시 조회한 뒤 새 Plan을 검토하세요.`;
  }
  if (error instanceof ApiError) {
    return error.code ? `${error.code}: ${error.message}` : error.message;
  }
  return error instanceof Error ? error.message : '요청에 실패했습니다.';
}

function displayValue(item: ConfigurationEffectiveItem | null): string {
  if (item === null) return '—';
  if (item.sensitive) return item.configured ? 'Configured (value hidden)' : 'Not configured';
  const value = item.effective_value;
  if (value === null || value === undefined) return '—';
  if (Array.isArray(value)) return value.join(', ');
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function displayOperatorValue(item: ConfigurationEffectiveItem | null): string {
  if (item === null || item.operator_value === null || item.operator_value === undefined) return '—';
  if (Array.isArray(item.operator_value)) return item.operator_value.join(', ');
  if (typeof item.operator_value === 'object') return JSON.stringify(item.operator_value);
  return String(item.operator_value);
}

function draftFromEffective(
  metadata: ConfigurationSchemaItem,
  effective: ConfigurationEffectiveItem,
): string | boolean {
  const value = effective.operator_value ?? effective.effective_value;
  if (metadata.type === 'boolean') return Boolean(value);
  return value === null || value === undefined ? '' : String(value);
}

function riskColor(risk: string): 'grey' | 'blue' | 'orange' | 'red' {
  if (risk === 'critical' || risk === 'high') return 'red';
  if (risk === 'medium') return 'orange';
  if (risk === 'low') return 'blue';
  return 'grey';
}

export function ConfigurationPage({ token, onUnauthorized, deploymentFeatures }: ConfigurationPageProps) {
  const queryClient = useQueryClient();
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [draft, setDraft] = useState<string | boolean>('');
  const [review, setReview] = useState<ReviewedConfiguration | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [lastApply, setLastApply] = useState<ConfigurationApplyResponse | null>(null);
  const authClass = token === null ? 'anonymous' : 'authenticated';

  const schemaQuery = useQuery({
    queryKey: ['configuration', 'schema', authClass],
    queryFn: () => fetchConfigurationSchema(token),
    retry: false,
    staleTime: 10_000,
  });
  const effectiveQuery = useQuery({
    queryKey: ['configuration', 'effective', authClass],
    queryFn: () => fetchConfigurationEffective(token),
    retry: false,
    staleTime: 10_000,
  });

  useEffect(() => {
    if (isUnauthorized(schemaQuery.error) || isUnauthorized(effectiveQuery.error)) onUnauthorized();
  }, [effectiveQuery.error, onUnauthorized, schemaQuery.error]);

  const refresh = async () => {
    setReview(null);
    setSelectedKey(null);
    await Promise.all([schemaQuery.refetch(), effectiveQuery.refetch()]);
  };

  const planMutation = useMutation({
    mutationFn: (intent: PlanIntent) => planConfigurationChange(token, {
      base_revision: intent.baseRevision,
      changes: intent.changes,
    }),
    retry: false,
    onMutate: () => {
      setActionError(null);
      setLastApply(null);
      setReview(null);
    },
    onSuccess: (plan, intent) => {
      setReview({ plan, changes: intent.changes, etag: intent.etag });
    },
    onError: async (error) => {
      if (isUnauthorized(error)) {
        onUnauthorized();
        return;
      }
      setActionError(errorMessage(error));
      if (isRevisionConflict(error)) {
        setSelectedKey(null);
        await queryClient.invalidateQueries({ queryKey: ['configuration'] });
      }
    },
  });

  const applyMutation = useMutation({
    mutationFn: (reviewed: ReviewedConfiguration) => applyConfigurationChange(
      token,
      reviewed.etag,
      configurationApplyRequest(reviewed.plan, reviewed.changes),
    ),
    retry: false,
    onMutate: () => setActionError(null),
    onSuccess: async (result) => {
      setLastApply(result);
      setReview(null);
      setSelectedKey(null);
      await queryClient.invalidateQueries({ queryKey: ['configuration'] });
    },
    onError: async (error) => {
      if (isUnauthorized(error)) {
        onUnauthorized();
        return;
      }
      setActionError(errorMessage(error));
      // Apply failure는 요청 도달 여부나 persist 단계가 불명확할 수 있다. 같은 review를
      // 재사용하지 않고 canonical state를 다시 읽은 뒤 새 Plan을 요구한다.
      setReview(null);
      setSelectedKey(null);
      await queryClient.invalidateQueries({ queryKey: ['configuration'] });
    },
  });

  const schemaItems = useMemo(
    () => (schemaQuery.data?.items ?? []).filter((item) => item.control_surface === 'configuration'),
    [schemaQuery.data],
  );
  const effectiveByKey = useMemo(() => new Map(
    (effectiveQuery.data?.data.items ?? []).map((item) => [item.key, item]),
  ), [effectiveQuery.data]);
  const selectedMetadata = schemaItems.find((item) => item.key === selectedKey) ?? null;
  const selectedEffective = selectedKey === null ? null : effectiveByKey.get(selectedKey) ?? null;

  if (schemaQuery.isPending || effectiveQuery.isPending) {
    return <div className="inline-loading"><Spinner size="lg" aria-label="Configuration loading" /> Configuration을 불러오는 중입니다.</div>;
  }
  if (schemaQuery.isError) {
    return <Alert isInline variant="danger" title="Configuration metadata를 불러오지 못했습니다.">{errorMessage(schemaQuery.error)}</Alert>;
  }
  if (effectiveQuery.isError) {
    return <Alert isInline variant="danger" title="Effective configuration을 불러오지 못했습니다.">{errorMessage(effectiveQuery.error)}</Alert>;
  }

  const schema = schemaQuery.data;
  const effectiveRead = effectiveQuery.data;
  const effective = effectiveRead.data;
  if (schema.version !== effective.version) {
    return (
      <Alert isInline variant="danger" title="Configuration 계약 version이 일치하지 않습니다.">
        metadata version {schema.version}, effective version {effective.version}. 안전을 위해 mutation을 잠급니다.
      </Alert>
    );
  }

  const writeStatus = effective.write_status;
  const actionPending = planMutation.isPending || applyMutation.isPending;

  const submitPlan = (changes: ConfigurationChange[]) => {
    planMutation.mutate({
      baseRevision: effective.revision,
      etag: effectiveRead.etag,
      changes,
    });
  };

  const planSelectedValue = () => {
    if (selectedMetadata === null) return;
    try {
      submitPlan([configurationSetChange(
        selectedMetadata.key,
        parseConfigurationDraft(selectedMetadata, draft),
      )]);
    } catch (error) {
      setActionError(errorMessage(error));
    }
  };

  return (
    <section className="runtime-page">
      <div className="page-heading">
        <div>
          <h1>Configuration</h1>
          <p>Operator-owned runtime 설정을 Edit → Plan → Review → Apply → Verify 순서로 변경합니다.</p>
        </div>
        <Button variant="secondary" onClick={() => void refresh()} isDisabled={schemaQuery.isFetching || effectiveQuery.isFetching || actionPending}>
          {schemaQuery.isFetching || effectiveQuery.isFetching ? '새로고침 중…' : '새로고침'}
        </Button>
      </div>

      {!writeStatus.available ? (
        <Alert isInline variant="warning" title="Configuration write plane을 사용할 수 없습니다.">
          reason: {writeStatus.reason ?? 'unknown'} · resolver revision: {writeStatus.resolver_revision} · runtime revision: {writeStatus.runtime_revision}
          {writeStatus.pending_operations !== null ? ` · pending operations: ${writeStatus.pending_operations}` : ''}
        </Alert>
      ) : null}
      {actionError ? <Alert isInline variant="danger" title="Configuration operation을 완료하지 못했습니다.">{actionError}</Alert> : null}

      <Card>
        <CardTitle>Current revision</CardTitle>
        <CardBody>
          <dl className="facts compact-facts">
            <dt>Revision</dt><dd>{effective.revision}</dd>
            <dt>Store revision</dt><dd>{writeStatus.store_revision ?? '—'}</dd>
            <dt>Resolver revision</dt><dd>{writeStatus.resolver_revision}</dd>
            <dt>Runtime revision</dt><dd>{writeStatus.runtime_revision}</dd>
            <dt>Synchronized</dt><dd>{writeStatus.synchronized ? 'Yes' : 'No'}</dd>
          </dl>
        </CardBody>
      </Card>

      <div className="table-scroll">
        <table className="runtime-table">
          <thead>
            <tr><th>Setting</th><th>Effective</th><th>Source</th><th>Operator override</th><th>Risk</th><th>Action</th></tr>
          </thead>
          <tbody>
            {schemaItems.map((metadata) => {
              const value = effectiveByKey.get(metadata.key) ?? null;
              const applicable = configurationItemApplies(metadata, deploymentFeatures);
              const editable = writeStatus.available && metadata.editable && applicable;
              return (
                <tr key={metadata.key}>
                  <td>
                    <strong>{metadata.label}</strong>
                    <small>{metadata.key}{metadata.unit ? ` · ${metadata.unit}` : ''}</small>
                  </td>
                  <td>{displayValue(value)}</td>
                  <td>
                    {value?.effective_source ?? '—'}
                    {value?.operator_override_shadowed ? <small>operator override shadowed</small> : null}
                  </td>
                  <td>{displayOperatorValue(value)}</td>
                  <td><Label color={riskColor(metadata.risk)}>{metadata.risk}</Label></td>
                  <td>
                    {applicable ? (
                      <Button
                        size="sm"
                        variant="secondary"
                        isDisabled={!editable || actionPending || value === null}
                        onClick={() => {
                          if (value === null) return;
                          setSelectedKey(metadata.key);
                          setDraft(draftFromEffective(metadata, value));
                          setReview(null);
                          setActionError(null);
                        }}
                      >편집</Button>
                    ) : <Label color="grey">not applicable</Label>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {selectedMetadata && selectedEffective ? (
        <Card className="review-card">
          <CardTitle>Edit — {selectedMetadata.label}</CardTitle>
          <CardBody>
            <dl className="facts compact-facts">
              <dt>Key</dt><dd><code>{selectedMetadata.key}</code></dd>
              <dt>Effective</dt><dd>{displayValue(selectedEffective)}</dd>
              <dt>Source</dt><dd>{selectedEffective.effective_source}</dd>
              <dt>Operator override</dt><dd>{displayOperatorValue(selectedEffective)}</dd>
              <dt>Apply mode</dt><dd>{selectedMetadata.apply_mode}</dd>
              <dt>Risk</dt><dd>{selectedMetadata.risk}</dd>
            </dl>
            <p className="configuration-help">{selectedMetadata.help}</p>
            <div className="configuration-editor">
              <label htmlFor="configuration-value">새 operator value</label>
              {selectedMetadata.type === 'boolean' ? (
                <label className="configuration-boolean">
                  <input
                    id="configuration-value"
                    type="checkbox"
                    checked={typeof draft === 'boolean' ? draft : false}
                    onChange={(event) => setDraft(event.currentTarget.checked)}
                  />{' '}Enabled
                </label>
              ) : selectedMetadata.type === 'enum' ? (
                <select
                  id="configuration-value"
                  value={typeof draft === 'string' ? draft : ''}
                  onChange={(event) => setDraft(event.currentTarget.value)}
                >
                  {(selectedMetadata.enum ?? []).map((value) => <option key={String(value)} value={String(value)}>{String(value)}</option>)}
                </select>
              ) : (
                <input
                  id="configuration-value"
                  type={selectedMetadata.type === 'integer' || selectedMetadata.type === 'number' ? 'number' : 'text'}
                  min={selectedMetadata.minimum}
                  max={selectedMetadata.maximum}
                  step={selectedMetadata.type === 'integer' ? 1 : 'any'}
                  value={typeof draft === 'string' ? draft : ''}
                  onChange={(event) => setDraft(event.currentTarget.value)}
                />
              )}
            </div>
            <div className="review-actions">
              <Button variant="secondary" onClick={() => { setSelectedKey(null); setReview(null); }} isDisabled={actionPending}>취소</Button>
              <Button
                variant="secondary"
                isDanger
                isDisabled={actionPending || selectedEffective.operator_value === null || selectedEffective.operator_value === undefined}
                onClick={() => submitPlan([configurationResetChange(selectedMetadata.key)])}
              >Override reset Plan</Button>
              <Button variant="primary" isDisabled={actionPending} onClick={planSelectedValue}>
                {planMutation.isPending ? 'Plan 계산 중…' : '변경 Plan 검토'}
              </Button>
            </div>
          </CardBody>
        </Card>
      ) : null}

      {review ? (
        <Card className="review-card">
          <CardTitle>Configuration change review</CardTitle>
          <CardBody>
            <dl className="facts compact-facts">
              <dt>Base revision</dt><dd>{review.plan.base_revision}</dd>
              <dt>Candidate revision</dt><dd>{review.plan.candidate_revision}</dd>
              <dt>Would change</dt><dd>{review.plan.would_change ? 'Yes' : 'No'}</dd>
              <dt>Plan digest</dt><dd><code>{review.plan.plan_digest}</code></dd>
            </dl>
            <div className="table-scroll configuration-review-table">
              <table className="runtime-table">
                <thead><tr><th>Key</th><th>Operation</th><th>Effective before</th><th>Effective after</th><th>Source after</th><th>Risk</th></tr></thead>
                <tbody>
                  {review.plan.changes.map((change) => (
                    <tr key={change.key}>
                      <td><code>{change.key}</code></td>
                      <td>{change.operation}</td>
                      <td>{String(change.effective_before ?? '—')}</td>
                      <td>{String(change.effective_after ?? '—')}</td>
                      <td>
                        {change.effective_source_after}
                        {change.operator_override_shadowed_after ? <small>operator override shadowed</small> : null}
                      </td>
                      <td><Label color={riskColor(change.risk)}>{change.risk}</Label></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!review.plan.would_change ? (
              <Alert isInline variant="info" title="적용할 변경이 없습니다.">현재 operator state와 같은 Plan이므로 Apply를 실행하지 않습니다.</Alert>
            ) : null}
            <div className="review-actions">
              <Button variant="secondary" onClick={() => setReview(null)} isDisabled={actionPending}>Plan 닫기</Button>
              <Button
                variant="primary"
                isDisabled={!review.plan.would_change || actionPending}
                onClick={() => applyMutation.mutate(review)}
              >{applyMutation.isPending ? '적용·검증 중…' : '검토한 Plan 적용'}</Button>
            </div>
          </CardBody>
        </Card>
      ) : null}

      {lastApply ? (
        <Card>
          <CardTitle>Apply verification</CardTitle>
          <CardBody>
            <dl className="facts operation-facts">
              <dt>Operation</dt><dd><code>{lastApply.operation_id}</code></dd>
              <dt>Status</dt><dd>{lastApply.status}</dd>
              <dt>Changed</dt><dd>{lastApply.changed ? 'Yes' : 'No'}</dd>
              <dt>Revision</dt><dd>{lastApply.revision}</dd>
              <dt>Store revision</dt><dd>{lastApply.verification.store_revision ?? '—'}</dd>
              <dt>Resolver revision</dt><dd>{lastApply.verification.resolver_revision}</dd>
              <dt>Runtime revision</dt><dd>{lastApply.verification.runtime_revision}</dd>
              <dt>Synchronized</dt><dd>{lastApply.verification.synchronized ? 'Yes' : 'No'}</dd>
            </dl>
          </CardBody>
        </Card>
      ) : null}
    </section>
  );
}
