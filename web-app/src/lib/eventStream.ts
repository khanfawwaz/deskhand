/**
 * Server-sent events over fetch.
 *
 * The browser's own EventSource cannot send an Authorization header, and Deskhand authenticates with a bearer token,
 * so the stream is read by hand: fetch, read the body in chunks, split it into events. Everything here is plain
 * TypeScript with no React in it, so it can be tested without a browser (see eventStream.test.ts).
 */

export interface StreamEvent {
  /** the `event:` name, `message` when the server sent none */
  event: string;
  /** the parsed JSON of the `data:` lines */
  data: unknown;
}

export type StreamStatus = 'connecting' | 'online' | 'offline';

/** One event block ("event: x\ndata: {...}") to an event. Comments (": ping") and blocks without JSON data give null. */
export function parseEventBlock(block: string): StreamEvent | null {
  let event = 'message';
  let data = '';
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) data += line.slice(5).trim();
  }
  if (!data) return null;
  try {
    return { event, data: JSON.parse(data) as unknown };
  } catch {
    return null;
  }
}

/** Collects text as it arrives in arbitrary chunks and hands back whole events. */
export class StreamParser {
  private buffer = '';

  push(chunk: string): StreamEvent[] {
    this.buffer = (this.buffer + chunk).replace(/\r\n/g, '\n');
    const out: StreamEvent[] = [];
    let i: number;
    while ((i = this.buffer.indexOf('\n\n')) >= 0) {
      const ev = parseEventBlock(this.buffer.slice(0, i));
      this.buffer = this.buffer.slice(i + 2);
      if (ev) out.push(ev);
    }
    return out;
  }
}

/** Waits `ms`, or less if the signal aborts (it resolves either way; the caller checks the signal). */
export function abortableSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const done = () => {
      clearTimeout(t);
      signal.removeEventListener('abort', done);
      resolve();
    };
    const t = setTimeout(done, ms);
    signal.addEventListener('abort', done, { once: true });
  });
}

export interface StreamOptions {
  path: string;
  signal: AbortSignal;
  getToken: () => string | null;
  onEvent: (e: StreamEvent) => void;
  onStatus?: (s: StreamStatus) => void;
  /** the server said 401: this device is no longer signed in. The loop stops. */
  onUnauthorized?: () => void;
  /** injectable for tests */
  fetchFn?: typeof fetch;
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
  /** first wait before reconnecting, doubled after each failure up to maxDelayMs; reset after a good connection */
  initialDelayMs?: number;
  maxDelayMs?: number;
}

/**
 * Keep the stream open until aborted: connect, read events, and when it drops wait (1 s, 2 s, 4 s ... up to 15 s)
 * and connect again. Stops for good on abort, on 401, or when there is no token.
 */
export async function runEventStream(o: StreamOptions): Promise<void> {
  const fetchFn: typeof fetch = o.fetchFn ?? ((input, init) => fetch(input, init));
  const sleep = o.sleep ?? abortableSleep;
  const initial = o.initialDelayMs ?? 1000;
  const max = o.maxDelayMs ?? 15000;
  let delay = initial;

  while (!o.signal.aborted) {
    const token = o.getToken();
    if (!token) return;
    try {
      o.onStatus?.('connecting');
      const res = await fetchFn(o.path, { headers: { Authorization: `Bearer ${token}` }, signal: o.signal });
      if (res.status === 401) {
        o.onUnauthorized?.();
        return;
      }
      if (!res.ok || !res.body) throw new Error('stream failed');
      o.onStatus?.('online');
      delay = initial;
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      const parser = new StreamParser();
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        for (const ev of parser.push(decoder.decode(value, { stream: true }))) o.onEvent(ev);
      }
    } catch {
      /* dropped or refused: fall through to the reconnect wait below */
    }
    if (o.signal.aborted) return;
    o.onStatus?.('offline');
    await sleep(delay, o.signal);
    delay = Math.min(delay * 2, max);
  }
}
