type ApiErrorLike = {
  status: number;
  code?: string | null;
  message: string;
};

function isApiErrorLike(error: unknown): error is ApiErrorLike {
  return typeof error === 'object'
    && error !== null
    && typeof (error as Record<string, unknown>).status === 'number'
    && typeof (error as Record<string, unknown>).message === 'string';
}

export function isUnauthorized(error: unknown): boolean {
  return isApiErrorLike(error) && error.status === 401;
}

export function apiErrorMessage(error: unknown, fallback = '요청에 실패했습니다.'): string {
  if (isApiErrorLike(error)) {
    return typeof error.code === 'string' && error.code
      ? `${error.code}: ${error.message}`
      : error.message;
  }
  return error instanceof Error ? error.message : fallback;
}
