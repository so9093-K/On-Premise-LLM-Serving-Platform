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

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string | null = null,
    readonly details: unknown = null,
    readonly requestId: string | null = null,
  ) {
    super(message);
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
