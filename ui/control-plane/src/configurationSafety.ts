import type {
  ConfigurationApplyRequest,
  ConfigurationChange,
  ConfigurationPlanResponse,
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
  draft: string | boolean,
): unknown {
  if (item.type === 'boolean') {
    if (typeof draft !== 'boolean') throw new Error('boolean configuration requires a boolean value');
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
  if (item.type === 'string' || item.type === 'url' || item.type === 'enum') {
    return draft;
  }
  throw new Error(`Console에서 지원하지 않는 editable configuration type입니다: ${item.type}`);
}
