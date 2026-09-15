import type { paths } from './generated/openapi';

export type BootstrapResponse =
  paths['/admin/control-plane/bootstrap']['get']['responses'][200]['content']['application/json'];
export type RuntimeListResponse =
  paths['/admin/runtimes']['get']['responses'][200]['content']['application/json'];
export type RuntimePlanRequest =
  paths['/admin/runtimes/{service_key}/plans']['post']['requestBody']['content']['application/json'];
export type RuntimePlanResponse =
  paths['/admin/runtimes/{service_key}/plans']['post']['responses'][200]['content']['application/json'];
export type RuntimeApplyRequest =
  paths['/admin/runtimes/{service_key}']['patch']['requestBody']['content']['application/json'];
export type RuntimeApplyResponse =
  paths['/admin/runtimes/{service_key}']['patch']['responses'][200]['content']['application/json'];
export type RuntimeOperationResponse =
  paths['/admin/runtimes/operations/{operation_id}']['get']['responses'][200]['content']['application/json'];
export type MainModelStatusResponse =
  paths['/admin/main-model']['get']['responses'][200]['content']['application/json'];
export type MainModelProfilesResponse =
  paths['/admin/main-model/profiles']['get']['responses'][200]['content']['application/json'];
export type MainModelProfile = MainModelProfilesResponse['profiles'][number];
export type MainModelSwitchRequest =
  paths['/admin/main-model/switch']['post']['requestBody']['content']['application/json'];
export type MainModelSwitchResponse =
  paths['/admin/main-model/switch']['post']['responses'][202]['content']['application/json'];
export type MainModelOperationResponse =
  paths['/admin/main-model/operations/{operation_id}']['get']['responses'][200]['content']['application/json'];

export type ConfigurationSchemaResponse =
  paths['/admin/config/schema']['get']['responses'][200]['content']['application/json'];
export type ConfigurationSchemaItem = ConfigurationSchemaResponse['items'][number];
export type ConfigurationEffectiveResponse =
  paths['/admin/config/effective']['get']['responses'][200]['content']['application/json'];
export type ConfigurationEffectiveItem = ConfigurationEffectiveResponse['items'][number];
export type ConfigurationPlanRequest =
  paths['/admin/config/plans']['post']['requestBody']['content']['application/json'];
export type ConfigurationChange = ConfigurationPlanRequest['changes'][number];
export type ConfigurationPlanResponse =
  paths['/admin/config/plans']['post']['responses'][200]['content']['application/json'];
export type ConfigurationApplyRequest =
  paths['/admin/config']['patch']['requestBody']['content']['application/json'];
export type ConfigurationApplyResponse =
  paths['/admin/config']['patch']['responses'][200]['content']['application/json'];
export type ConfigurationEffectiveRead = {
  data: ConfigurationEffectiveResponse;
  etag: string;
};

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly details: unknown;
  readonly requestId: string | null;

  constructor(
    message: string,
    status: number,
    code: string | null = null,
    details: unknown = null,
    requestId: string | null = null,
  ) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }
}

type ErrorEnvelope = {
  error?: {
    code?: unknown;
    message?: unknown;
    details?: unknown;
    request_id?: unknown;
  };
};

async function apiError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as ErrorEnvelope;
    const error = body.error;
    const message = typeof error?.message === 'string' && error.message
      ? error.message
      : `HTTP ${response.status}`;
    return new ApiError(
      message,
      response.status,
      typeof error?.code === 'string' ? error.code : null,
      error?.details ?? null,
      typeof error?.request_id === 'string' ? error.request_id : null,
    );
  } catch {
    return new ApiError(`HTTP ${response.status}`, response.status);
  }
}

function adminHeaders(token: string | null, jsonBody = false): HeadersInit {
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (jsonBody) {
    headers['Content-Type'] = 'application/json';
  }
  if (token !== null) {
    headers.Authorization = `Bearer ${token}`;
  }
  return headers;
}

async function jsonRequest<T>(
  path: string,
  token: string | null,
  init: RequestInit = {},
): Promise<T> {
  const response = await fetch(path, {
    cache: 'no-store',
    ...init,
    headers: {
      ...adminHeaders(token, init.body !== undefined),
      ...init.headers,
    },
  });
  if (!response.ok) {
    throw await apiError(response);
  }
  return (await response.json()) as T;
}

export async function fetchBootstrap(): Promise<BootstrapResponse> {
  const response = await fetch('/admin/control-plane/bootstrap', {
    cache: 'no-store',
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) {
    throw await apiError(response);
  }
  return (await response.json()) as BootstrapResponse;
}

export async function verifyAdminToken(token: string): Promise<void> {
  await jsonRequest<unknown>('/admin/config/schema', token);
}

export async function fetchRuntimes(token: string | null): Promise<RuntimeListResponse> {
  return jsonRequest<RuntimeListResponse>('/admin/runtimes', token);
}

export async function planRuntimeTransition(
  token: string | null,
  serviceKey: string,
  request: RuntimePlanRequest,
): Promise<RuntimePlanResponse> {
  return jsonRequest<RuntimePlanResponse>(
    `/admin/runtimes/${encodeURIComponent(serviceKey)}/plans`,
    token,
    { method: 'POST', body: JSON.stringify(request) },
  );
}

export async function applyRuntimeTransition(
  token: string | null,
  serviceKey: string,
  request: RuntimeApplyRequest,
): Promise<RuntimeApplyResponse> {
  return jsonRequest<RuntimeApplyResponse>(
    `/admin/runtimes/${encodeURIComponent(serviceKey)}`,
    token,
    { method: 'PATCH', body: JSON.stringify(request) },
  );
}

export async function fetchRuntimeOperation(
  token: string | null,
  operationId: string,
): Promise<RuntimeOperationResponse> {
  return jsonRequest<RuntimeOperationResponse>(
    `/admin/runtimes/operations/${encodeURIComponent(operationId)}`,
    token,
  );
}

export async function fetchMainModel(token: string | null): Promise<MainModelStatusResponse> {
  return jsonRequest<MainModelStatusResponse>('/admin/main-model', token);
}

export async function fetchMainModelProfiles(token: string | null): Promise<MainModelProfilesResponse> {
  return jsonRequest<MainModelProfilesResponse>('/admin/main-model/profiles', token);
}

export async function switchMainModel(
  token: string | null,
  request: MainModelSwitchRequest,
): Promise<MainModelSwitchResponse> {
  return jsonRequest<MainModelSwitchResponse>(
    '/admin/main-model/switch',
    token,
    { method: 'POST', body: JSON.stringify(request) },
  );
}

export async function fetchMainModelOperation(
  token: string | null,
  operationId: string,
): Promise<MainModelOperationResponse> {
  return jsonRequest<MainModelOperationResponse>(
    `/admin/main-model/operations/${encodeURIComponent(operationId)}`,
    token,
  );
}

export async function fetchConfigurationSchema(
  token: string | null,
): Promise<ConfigurationSchemaResponse> {
  return jsonRequest<ConfigurationSchemaResponse>('/admin/config/schema', token);
}

export async function fetchConfigurationEffective(
  token: string | null,
): Promise<ConfigurationEffectiveRead> {
  const response = await fetch('/admin/config/effective', {
    cache: 'no-store',
    headers: adminHeaders(token),
  });
  if (!response.ok) {
    throw await apiError(response);
  }
  const etag = response.headers.get('ETag');
  if (etag === null || etag.length === 0) {
    throw new Error('Configuration effective response did not include ETag');
  }
  return {
    data: (await response.json()) as ConfigurationEffectiveResponse,
    etag,
  };
}

export async function planConfigurationChange(
  token: string | null,
  request: ConfigurationPlanRequest,
): Promise<ConfigurationPlanResponse> {
  return jsonRequest<ConfigurationPlanResponse>(
    '/admin/config/plans',
    token,
    { method: 'POST', body: JSON.stringify(request) },
  );
}

export async function applyConfigurationChange(
  token: string | null,
  etag: string,
  request: ConfigurationApplyRequest,
): Promise<ConfigurationApplyResponse> {
  return jsonRequest<ConfigurationApplyResponse>(
    '/admin/config',
    token,
    {
      method: 'PATCH',
      body: JSON.stringify(request),
      headers: { 'If-Match': etag },
    },
  );
}
