import { renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { useSession } from '../state/session';
import { server } from '../test/server';
import { openStream } from '../test/render';
import { useEventStream } from './useEventStream';

// the stream pushes state updates from the network, not from a click: tell React this file is not a click-driven test
beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = false;
});

describe('useEventStream', () => {
  it('connects with the token, delivers events, and reports the state', async () => {
    useSession.setState({ token: 'tok' });
    const stream = openStream();
    let auth = '';
    server.use(
      http.get('*/v1/events', ({ request }) => {
        auth = request.headers.get('authorization') ?? '';
        return stream.response;
      }),
    );
    const seen: unknown[] = [];
    const { result } = renderHook(() => useEventStream({ onEvent: (e) => seen.push(e) }));
    await waitFor(() => expect(result.current).toBe('online'));
    expect(auth).toBe('Bearer tok');
    stream.send('event: devices\ndata: [{"device_id":"d1"}]\n\n');
    await waitFor(() => expect(seen).toEqual([{ event: 'devices', data: [{ device_id: 'd1' }] }]));
  });

  it('always calls the newest handler without reconnecting', async () => {
    useSession.setState({ token: 'tok' });
    const stream = openStream();
    const connections = vi.fn();
    server.use(
      http.get('*/v1/events', () => {
        connections();
        return stream.response;
      }),
    );
    const first = vi.fn();
    const second = vi.fn();
    const { result, rerender } = renderHook(({ fn }) => useEventStream({ onEvent: fn }), { initialProps: { fn: first } });
    await waitFor(() => expect(result.current).toBe('online'));
    rerender({ fn: second });
    stream.send('data: 1\n\n');
    await waitFor(() => expect(second).toHaveBeenCalledWith({ event: 'message', data: 1 }));
    expect(first).not.toHaveBeenCalled();
    expect(connections).toHaveBeenCalledTimes(1);
  });

  it('stays offline and makes no request when there is no token or it is disabled', async () => {
    const hit = vi.fn();
    server.use(http.get('*/v1/events', () => (hit(), new HttpResponse(null, { status: 500 }))));
    const noToken = renderHook(() => useEventStream({ onEvent: () => undefined }));
    expect(noToken.result.current).toBe('offline');
    noToken.unmount(); // (a mounted one would rightly connect as soon as a token appears)
    useSession.setState({ token: 'tok' });
    const off = renderHook(() => useEventStream({ onEvent: () => undefined, enabled: false }));
    expect(off.result.current).toBe('offline');
    await new Promise((r) => setTimeout(r, 30));
    expect(hit).not.toHaveBeenCalled();
  });

  it('signs the device out when the server says 401', async () => {
    useSession.setState({ token: 'old' });
    server.use(http.get('*/v1/events', () => new HttpResponse(null, { status: 401 })));
    renderHook(() => useEventStream({ onEvent: () => undefined }));
    await waitFor(() => expect(useSession.getState().token).toBeNull());
    expect(useSession.getState().notice).toMatch(/no longer paired/i);
  });

  it('closes the connection when the component goes away', async () => {
    useSession.setState({ token: 'tok' });
    const stream = openStream();
    let aborted = false;
    server.use(
      http.get('*/v1/events', ({ request }) => {
        request.signal.addEventListener('abort', () => (aborted = true));
        return stream.response;
      }),
    );
    const { result, unmount } = renderHook(() => useEventStream({ onEvent: () => undefined }));
    await waitFor(() => expect(result.current).toBe('online'));
    unmount();
    await waitFor(() => expect(aborted).toBe(true));
  });
});
