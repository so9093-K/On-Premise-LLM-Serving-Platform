import type {
  ConfigurationApplyRequest,
  ConfigurationChange,
  ConfigurationHistoryItem,
  ConfigurationPlanResponse,
  ConfigurationRollbackApplyRequest,
  ConfigurationRollbackPlanResponse,
  ConfigurationSchemaItem,
} from './api';

export function configurationSetChange(key: string, value: unknown): ConfigurationChange {
  return { key, op: 'set', value };
}

export function configurationResetChange(key: string): ConfigurationChange {
  return { key, op: 'reset' };
}

export function configurationApplyRequest(
  plan: ConfigurationPlanResponse,
  changes: ConfigurationChange[],
): ConfigurationApplyRequest {
  return {
    plan_digest: plan.plan_digest,
    changes,
  };
}

export function configurationItemApplies(
  item: ConfigurationSchemaItem,
  deploymentFeatures: readonly string[],
): boolean {
  return item.applicability.features.every((feature) => deploymentFeatures.includes(feature));
}

export function parseConfigurationDraft(
  item: ConfigurationSchemaItem,
  draft: string | number | boolean | null,
): unknown {
  if (item.type === 'boolean') {
    if (typeof draft !== 'boolean') throw new Error('boolean configuration requires a boolean value');
    return draft;
  }
  if (item.type === 'enum') {
    const allowed = item.enum ?? [];
    if (!allowed.some((value) => Object.is(value, draft))) {
      throw new Error('enum configuration requires a declared value');
    }
    return draft;
  }
  if (typeof draft !== 'string') throw new Error('configuration value requires text input');

  if (item.type === 'integer' || item.type === 'number') {
    if (draft.trim() === '') throw new Error('numeric configuration requires a value');
    const value = Number(draft);
    if (item.type === 'integer' && !Number.isInteger(value)) {
      throw new Error('integer configuration requires an integer value');
    }
    if (!Number.isFinite(value)) {
      throw new Error('number configuration requires a finite numeric value');
    }
    return value;
  }
  if (item.type === 'string' || item.type === 'url') {
    return draft;
  }
  throw new Error(`Console에서 지원하지 않는 editable configuration type입니다: ${item.type}`);
}


export function configurationRollbackApplyRequest(
  plan: ConfigurationRollbackPlanResponse,
): ConfigurationRollbackApplyRequest {
  return {
    target_revision: plan.target_revision,
    plan_digest: plan.plan_digest,
  };
}

export function configurationRollbackTargetRevision(
  item: ConfigurationHistoryItem,
  currentRevision: number,
): number | null {
  return item.base_revision < currentRevision ? item.base_revision : null;
}
