/** Thin fetch wrapper: same-origin cookies, CSRF double-submit header, RFC 9457 problem errors. */
import type { Reason } from './types';

export interface FieldError {
  field: string;
  code: string;
  params?: Record<string, string | number | null>;
}

export interface ProblemBody {
  code: string;
  detail?: string;
  errors?: FieldError[];
  request_id?: string;
  params?: Record<string, string | number | null>;
  reasons?: Record<string, Reason[]>;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly body: ProblemBody;

  constructor(status: number, body: ProblemBody) {
    super(body.detail ?? body.code);
    this.status = status;
    this.code = body.code;
    this.body = body;
  }
}

export const API_BASE = '/api/v1';
const SAFE = new Set(['GET', 'HEAD', 'OPTIONS']);

function csrfToken(): string {
  const match = /(?:^|;\s*)ao_csrf=([^;]+)/.exec(document.cookie);
  return match?.[1] ? decodeURIComponent(match[1]) : '';
}

export type Query = Record<string, string | number | boolean | null | undefined>;

export function buildUrl(path: string, query?: Query): string {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v !== null && v !== undefined && v !== '') params.set(k, String(v));
  }
  const qs = params.toString();
  return `${API_BASE}${path}${qs ? `?${qs}` : ''}`;
}

export async function api<T>(path: string, opts: { method?: string; body?: unknown; query?: Query } = {}): Promise<T> {
  const method = opts.method ?? 'GET';
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
  if (!SAFE.has(method)) headers['X-CSRF-Token'] = csrfToken();
  let response: Response;
  try {
    response = await fetch(buildUrl(path, opts.query), {
      method,
      headers,
      credentials: 'same-origin',
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    });
  } catch {
    throw new ApiError(0, { code: 'NETWORK' });
  }
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  const data: unknown = text ? JSON.parse(text) : undefined;
  if (!response.ok) {
    const body = (data && typeof data === 'object' ? data : { code: 'INTERNAL_ERROR' }) as ProblemBody;
    throw new ApiError(response.status, body);
  }
  return data as T;
}
