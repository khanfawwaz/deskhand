import { useEffect, useEffectEvent, useState } from 'react';
import { runEventStream } from '../lib/eventStream';
import type { StreamEvent, StreamStatus } from '../lib/eventStream';
import { useSession } from '../state/session';

export type { StreamEvent, StreamStatus };

interface Options {
  /** defaults to the Deskhand live feed */
  path?: string;
  /** called for every event; the latest function is always used, changing it never reconnects */
  onEvent: (e: StreamEvent) => void;
  /** set false to stay disconnected */
  enabled?: boolean;
}

/**
 * The live feed (`/v1/events`) while signed in: reconnects by itself, closes when the component unmounts,
 * and signs the device out if the server no longer accepts its token. Returns the connection state.
 */
export function useEventStream({ path = '/v1/events', onEvent, enabled = true }: Options): StreamStatus {
  const token = useSession((s) => s.token);
  const [status, setStatus] = useState<StreamStatus>('connecting');
  const active = enabled && !!token;
  // an "effect event" always calls the newest onEvent without making the effect below re-run (no ref juggling)
  const emit = useEffectEvent((e: StreamEvent) => onEvent(e));

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    void runEventStream({
      path,
      signal: controller.signal,
      getToken: () => useSession.getState().token,
      onEvent: (e) => emit(e),
      onStatus: setStatus,
      onUnauthorized: () => useSession.getState().signOut('This device is no longer paired. Please sign in again.'),
    });
    return () => controller.abort();
  }, [path, active]);

  return active ? status : 'offline';
}
