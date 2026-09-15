import type { paths } from './generated/openapi';

export type BootstrapResponse =
  paths['/admin/control-plane/bootstrap']['get']['responses'][200]['content']['application/json'];

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function responseMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { error?: { message?: unknown } };
    const message = body.error?.message;
    return typeof message === 'string' && message ? message : `HTTP ${response.status}`;
  } catch {
    return `HTTP ${response.status}`;
  }
}

export async function fetchBootstrap(): Promise<BootstrapResponse> {
  const response = await fetch('/admin/control-plane/bootstrap', {
    cache: 'no-store',
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) {
    throw new ApiError(await responseMessage(response), response.status);
  }
  return (await response.json()) as BootstrapResponse;
}

export async function verifyAdminToken(token: string): Promise<void> {
  const response = await fetch('/admin/config/schema', {
    cache: 'no-store',
    headers: {
      Accept: 'application/json',
      Authorization: `Bearer ${token}`,
    },
  });
  if (!response.ok) {
    throw new ApiError(await responseMessage(response), response.status);
  }
}
