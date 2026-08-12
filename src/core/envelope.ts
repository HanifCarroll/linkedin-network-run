export type SuccessEnvelope<T> = {
  ok: true;
  data: T;
};

export type ErrorDetail = {
  code: string;
  message: string;
  details?: Record<string, unknown>;
};

export type ErrorEnvelope = {
  ok: false;
  error: ErrorDetail;
};

export function success<T>(data: T): SuccessEnvelope<T> {
  return { ok: true, data };
}

export function failure(error: ErrorDetail): ErrorEnvelope {
  return { ok: false, error };
}
