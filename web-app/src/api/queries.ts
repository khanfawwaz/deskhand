import { QueryClient, useQuery } from '@tanstack/react-query';
import { api, ApiError } from './client';
import type { AuthConfig, Me } from './types';
import { useSession } from '../state/session';

/** Sensible defaults: no retry for "you cannot do that" answers (4xx), a couple of retries for network trouble. */
export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 5_000,
        refetchOnWindowFocus: false,
        retry: (count, err) => !(err instanceof ApiError && err.status >= 400 && err.status < 500) && count < 2,
      },
    },
  });
}

export const queryKeys = {
  me: ['me'] as const,
  authConfig: ['auth-config'] as const,
};

/** Who is signed in, their workspace and their computers. Only runs while there is a token. */
export function useMe() {
  const token = useSession((s) => s.token);
  return useQuery({ queryKey: queryKeys.me, queryFn: () => api<Me>('/v1/me'), enabled: !!token });
}

/** What the sign-in screen should offer. An old server without this route means "pairing code only". */
export function useAuthConfig() {
  return useQuery({
    queryKey: queryKeys.authConfig,
    queryFn: () => api<AuthConfig>('/v1/auth/config').catch((): AuthConfig => ({ email_login: false })),
    staleTime: Infinity,
  });
}
