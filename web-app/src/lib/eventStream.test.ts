import { describe, expect, it, vi } from 'vitest';
import { parseEventBlock, runEventStream, StreamParser } from './eventStream';
import type { StreamEvent, StreamStatus } from './eventStream';

const enc = new TextEncoder();

/** A finished event-stream response made of the given chunks. */
function sse(...chunks: (string | Uint8Array)[]): Response {
  return new Response(
    new ReadableStream({
      start(c) {
        for (const ch of chunks) c.enqueue(typeof ch === 'string' ? enc.encode(ch) : ch);
        c.close();
      },
    }),
    { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
  );
}

describe('parseEventBlock', () => {
  it('reads the event name and the JSON data', () => {
    expect(parseEventBlock('event: devices\ndata: [{"id":1}]')).toEqual({ event: 'devices', data: [{ id: 1 }] });
  });
  it('calls an unnamed event "message"', () => {
    expect(parseEventBlock('data: {"a":1}')).toEqual({ event: 'message', data: { a: 1 } });
  });
  it('ignores comments (the server sends ": ping" to keep the line open)', () => {
    expect(parseEventBlock(': ping')).toBeNull();
  });
  it('ignores a block with no data, or data that is not JSON', () => {
    expect(parseEventBlock('event: task')).toBeNull();
    expect(parseEventBlock('event: task\ndata: not json')).toBeNull();
  });
  it('joins data split over several lines', () => {
    expect(parseEventBlock('data: {"a":\ndata: 1}')).toEqual({ event: 'message', data: { a: 1 } });
  });
});

describe('StreamParser', () => {
  it('gives whole events only, however the text is cut', () => {
    const p = new StreamParser();
    expect(p.push('event: devices\nda')).toEqual([]);
    expect(p.push('ta: {"a":1}\n')).toEqual([]);
    expect(p.push('\nevent: task\ndata: 2\n\n')).toEqual([
      { event: 'devices', data: { a: 1 } },
      { event: 'task', data: 2 },
    ]);
  });
  it('handles Windows line endings, even when the pair is split between chunks', () => {
    const p = new StreamParser();
    expect(p.push('data: 1\r')).toEqual([]);
    expect(p.push('\n\r\ndata: 2\r\n\r\n')).toEqual([
      { event: 'message', data: 1 },
      { event: 'message', data: 2 },
    ]);
  });
  it('skips pings and bad blocks without losing the good ones around them', () => {
    const p = new StreamParser();
    expect(p.push('data: 1\n\n: ping\n\ndata: oops\n\ndata: 3\n\n')).toEqual([
      { event: 'message', data: 1 },
      { event: 'message', data: 3 },
    ]);
  });
});

/** Run the stream with a fake clock: sleeping just records the wait and aborts after `stopAfter` waits. */
async function run(opts: {
  responses: (Response | Error)[];
  stopAfter?: number;
  token?: () => string | null;
  initialDelayMs?: number;
  maxDelayMs?: number;
}) {
  const controller = new AbortController();
  const events: StreamEvent[] = [];
  const statuses: StreamStatus[] = [];
  const delays: number[] = [];
  const onUnauthorized = vi.fn();
  const queue = [...opts.responses];
  const fetchFn = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => {
    const next = queue.shift();
    if (!next) {
      controller.abort();
      throw new Error('no more responses');
    }
    if (next instanceof Error) throw next;
    return next;
  });
  await runEventStream({
    path: '/v1/events',
    signal: controller.signal,
    getToken: opts.token ?? (() => 'tok'),
    onEvent: (e) => events.push(e),
    onStatus: (s) => statuses.push(s),
    onUnauthorized,
    fetchFn: fetchFn as unknown as typeof fetch,
    sleep: async (ms) => {
      delays.push(ms);
      if (delays.length >= (opts.stopAfter ?? 99)) controller.abort();
    },
    initialDelayMs: opts.initialDelayMs,
    maxDelayMs: opts.maxDelayMs,
  });
  return { events, statuses, delays, onUnauthorized, fetchFn };
}

describe('runEventStream', () => {
  it('sends the token, delivers events in order, and reports connecting, online, offline', async () => {
    const r = await run({
      responses: [sse('event: devices\ndata: [1,2]\n\n: ping\n\nevent: task\ndata: {"id":"t"}\n\n')],
      stopAfter: 1,
    });
    expect(r.fetchFn).toHaveBeenCalledTimes(1);
    const init = r.fetchFn.mock.calls[0]?.[1] as RequestInit;
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok');
    expect(r.events).toEqual([
      { event: 'devices', data: [1, 2] },
      { event: 'task', data: { id: 't' } },
    ]);
    expect(r.statuses).toEqual(['connecting', 'online', 'offline']);
  });

  it('puts events together when the network cuts them in odd places, including inside a character', async () => {
    const whole = enc.encode('event: task\ndata: {"name":"café"}\n\n');
    const cut = whole.indexOf(0xc3) + 1; // between the two bytes of the e-acute
    const r = await run({ responses: [sse(whole.slice(0, cut), whole.slice(cut))], stopAfter: 1 });
    expect(r.events).toEqual([{ event: 'task', data: { name: 'café' } }]);
  });

  it('reconnects with a growing wait: 1 s, 2 s, 4 s, 8 s, then never more than 15 s', async () => {
    const down = () => new Error('network down');
    const r = await run({ responses: [down(), down(), down(), down(), down(), down()], stopAfter: 6 });
    expect(r.delays).toEqual([1000, 2000, 4000, 8000, 15000, 15000]);
    expect(r.statuses.filter((s) => s === 'offline')).toHaveLength(6);
  });

  it('starts the wait over after a good connection', async () => {
    const r = await run({ responses: [new Error('x'), new Error('x'), sse('data: 1\n\n'), new Error('x')], stopAfter: 4 });
    expect(r.delays).toEqual([1000, 2000, 1000, 2000]);
    expect(r.events).toEqual([{ event: 'message', data: 1 }]);
  });

  it('treats a server error like a dropped line and tries again', async () => {
    const r = await run({ responses: [new Response('boom', { status: 500 }), sse('data: 7\n\n')], stopAfter: 2 });
    expect(r.delays).toEqual([1000, 1000]);
    expect(r.events).toEqual([{ event: 'message', data: 7 }]);
  });

  it('stops for good on 401 and tells the app once', async () => {
    const r = await run({ responses: [new Response(null, { status: 401 })] });
    expect(r.onUnauthorized).toHaveBeenCalledTimes(1);
    expect(r.fetchFn).toHaveBeenCalledTimes(1);
    expect(r.delays).toEqual([]);
    expect(r.statuses).toEqual(['connecting']);
  });

  it('does nothing without a token, and stops when the token goes away', async () => {
    const none = await run({ responses: [sse('data: 1\n\n')], token: () => null });
    expect(none.fetchFn).not.toHaveBeenCalled();

    let calls = 0;
    const gone = await run({ responses: [sse('data: 1\n\n'), sse('data: 2\n\n')], token: () => (calls++ === 0 ? 'tok' : null) });
    expect(gone.fetchFn).toHaveBeenCalledTimes(1);
    expect(gone.events).toEqual([{ event: 'message', data: 1 }]);
  });

  it('stops quietly when aborted in the middle of reading', async () => {
    const controller = new AbortController();
    const statuses: StreamStatus[] = [];
    const fetchFn = vi.fn(async (_u: RequestInfo | URL, init?: RequestInit) => {
      const body = new ReadableStream({
        start(c) {
          init?.signal?.addEventListener('abort', () => c.error(new DOMException('aborted', 'AbortError')));
        },
      });
      return new Response(body, { status: 200 });
    });
    const done = runEventStream({
      path: '/v1/events',
      signal: controller.signal,
      getToken: () => 'tok',
      onEvent: () => undefined,
      onStatus: (s) => statuses.push(s),
      fetchFn: fetchFn as unknown as typeof fetch,
    });
    await vi.waitFor(() => expect(statuses).toContain('online'));
    controller.abort();
    await done;
    expect(statuses).toEqual(['connecting', 'online']); // no "offline" flash on a deliberate stop
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });
});
