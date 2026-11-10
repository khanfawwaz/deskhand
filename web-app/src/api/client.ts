import { useSession } from '../state/session';

/** An error from the server, carrying its HTTP status and the plain-English `detail` it sent. */
export class ApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export interface ApiOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE';
  body?: unknown;
  signal?: AbortSignal;
}

/** The message a person should read: the server's `detail` string, or its list of problems, or the status text. */
export function errorMessage(data: unknown, fallback: string): string {
  const detail = (data as { detail?: unknown } | null)?.detail;
  if (typeof detail === 'string') return detail;
  const problems = (detail as { problems?: unknown } | null)?.problems;
  if (Array.isArray(problems)) return problems.join('; ');
  return fallback;
}

/**
 * Call the Deskhand server. Sends the device's token, returns parsed JSON, and turns failures into ApiError.
 * A 401 while signed in means the token was revoked: sign out here, once, for the whole app.
 */
export async function api<T>(path: string, { method = 'GET', body, signal }: ApiOptions = {}): Promise<T> {
  const { token, signOut } = useSession.getState();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  let res: Response;
  try {
    res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), signal });
  } catch (e) {
    if (signal?.aborted) throw e; // a cancelled request is not an error to show
    throw new ApiError('Could not reach Deskhand. Check your connection and try again.', 0);
  }
  if (res.status === 401 && token) {
    signOut('You were signed out of this device. Please sign in again.');
    throw new ApiError('unauthorized', 401);
  }
  const data: unknown = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(errorMessage(data, res.statusText || 'Something went wrong'), res.status);
  return data as T;
}
