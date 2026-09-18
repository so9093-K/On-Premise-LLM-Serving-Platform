import type {
  MainModelOperationResponse,
  MainModelProfile,
  MainModelSwitchRequest,
} from './api';

export function mainModelProfileRequiresConfirmation(profile: MainModelProfile): boolean {
  return profile.qualification.status !== 'verified';
}

export function mainModelProfileSwitchable(profile: MainModelProfile): boolean {
  return profile.active !== true && profile.compatibility.status !== 'incompatible';
}

export function mainModelSwitchRequest(
  profile: MainModelProfile,
  confirmed: boolean,
): MainModelSwitchRequest {
  if (!mainModelProfileSwitchable(profile)) {
    throw new Error('selected profile cannot be switched');
  }
  if (mainModelProfileRequiresConfirmation(profile) && !confirmed) {
    throw new Error('selected profile requires explicit confirmation');
  }
  return {
    profile: profile.id,
    confirm_unverified: mainModelProfileRequiresConfirmation(profile),
  };
}

export function isMainModelOperationTerminal(operation: MainModelOperationResponse): boolean {
  return operation.status === 'completed'
    || operation.status === 'failed'
    || operation.status === 'rollback_failed';
}
