import '@testing-library/jest-dom/vitest';
import { afterAll, afterEach, beforeAll } from 'vitest';
import { useSession } from '../state/session';
import { useTheme } from '../state/theme';
import { server } from './server';

beforeAll(() => {
  server.listen({ onUnhandledRequest: 'error' });
  // Node's fetch needs absolute addresses, a browser's does not. Let the app call '/v1/me' like it does in a browser.
  const patched = globalThis.fetch;
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) =>
    patched(typeof input === 'string' && input.startsWith('/') ? new URL(input, window.location.origin) : input, init)) as typeof fetch;
});

afterEach(() => {
  server.resetHandlers();
  localStorage.clear();
  useSession.setState({ token: null, notice: '' });
  useTheme.setState({ mode: 'auto' });
  document.documentElement.removeAttribute('data-theme');
});

afterAll(() => server.close());
