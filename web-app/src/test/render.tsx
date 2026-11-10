import { QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';
import { makeQueryClient } from '../api/queries';
import { routes } from '../routes';

/** Render the whole app at an address, with a fresh query cache that never retries (tests want failures at once). */
export function renderApp(at = '/') {
  const client = makeQueryClient();
  client.setDefaultOptions({ queries: { retry: false, staleTime: 0, refetchOnWindowFocus: false } });
  const router = createMemoryRouter(routes, { initialEntries: [at] });
  return { client, router, ...render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>) };
}

/** An event-stream response that stays open until the test closes it (like the real /v1/events). */
export function openStream() {
  const enc = new TextEncoder();
  let ctl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({ start: (c) => void (ctl = c) });
  return {
    response: new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }),
    send: (text: string) => ctl.enqueue(enc.encode(text)),
    close: () => ctl.close(),
  };
}
