import { useEffect, useState } from 'react';
import type { DeviceView, Me } from '../../api/types';
import { storage } from '../../lib/storage';
import { extensionLink } from '../connect/extension';
import styles from './ExtensionTip.module.css';

/** What this browser remembers: computers that have had the extension connected, and computers whose tip was closed. */
export const TIP_KEY = 'pilot.extTip';

interface Memory {
  seen: string[];
  hidden: string[];
}

const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []);

function load(): Memory {
  try {
    const m = JSON.parse(storage.get(TIP_KEY) ?? '{}') as Partial<Memory>;
    return { seen: strings(m.seen), hidden: strings(m.hidden) };
  } catch {
    return { seen: [], hidden: [] };
  }
}

export type Nudge = { kind: 'install' | 'lost'; devices: DeviceView[] } | null;

/**
 * Which pop-up to show, if any. A computer that is online without the extension needs one, unless the person closed it
 * for that computer. "lost" when that computer has had the extension before (it was connected, now it is not).
 * A closed pop-up comes back as soon as the extension connects again and later drops: every new disconnect is news.
 */
export function extensionNudge(devices: DeviceView[], mem: Memory): Nudge {
  const need = devices.filter((d) => d.online && !d.extension && !mem.hidden.includes(d.device_id));
  if (need.length === 0) return null;
  return { kind: need.some((d) => mem.seen.includes(d.device_id)) ? 'lost' : 'install', devices: need };
}

/** The "use your own Chrome" pop-up: floats over the corner of the page (it never pushes the page around). */
export function ExtensionTip({ me }: { me: Pick<Me, 'devices' | 'extension_url'> }) {
  const [mem, setMem] = useState(load);

  // A computer whose extension is connected right now: remember it ever was, and forget a closed tip for it, so the
  // pop-up can come back if the extension drops later. (Adjusting state while rendering: React's pattern for this.)
  const connected = me.devices.filter((d) => d.extension).map((d) => d.device_id);
  if (connected.some((id) => !mem.seen.includes(id) || mem.hidden.includes(id))) {
    setMem((m) => ({ seen: [...new Set([...m.seen, ...connected])], hidden: m.hidden.filter((id) => !connected.includes(id)) }));
  }
  useEffect(() => storage.set(TIP_KEY, JSON.stringify(mem)), [mem]);

  const nudge = extensionNudge(me.devices, mem);
  if (!nudge) return null;
  const url = extensionLink(me.extension_url);
  const lost = nudge.kind === 'lost';
  const names = nudge.devices.map((d) => d.name).join(', ');
  const close = () => setMem((m) => ({ ...m, hidden: [...new Set([...m.hidden, ...nudge.devices.map((d) => d.device_id)])] }));

  return (
    <aside className={styles.pop} data-kind={nudge.kind} aria-label={lost ? 'Chrome extension disconnected' : 'Use your own Chrome'} role="complementary">
      <span className={styles.icon} aria-hidden="true">
        <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M9 4.5a2 2 0 1 1 4 0V6h3a1 1 0 0 1 1 1v3h1.5a2 2 0 1 1 0 4H17v3a1 1 0 0 1-1 1h-3v-1.5a2 2 0 1 0-4 0V18H6a1 1 0 0 1-1-1v-3h1.5a2 2 0 1 0 0-4H5V7a1 1 0 0 1 1-1h3z" />
        </svg>
      </span>
      <div className={styles.body}>
        <b className={styles.title}>{lost ? 'Chrome extension disconnected' : 'Use your own Chrome'}</b>
        <p className={styles.text}>
          {lost
            ? `It stopped talking to Deskhand on ${names}. Open Chrome, click the Deskhand icon and press Connect.`
            : 'Web tasks run in the Chrome you already use, where you are signed in to YouTube, Netflix and more.'}
        </p>
        <div className={styles.actions}>
          {url ? (
            <a className={styles.go} href={url} target="_blank" rel="noopener noreferrer">
              {lost ? 'Open the extension page' : 'Add to Chrome'}
            </a>
          ) : (
            <span className={styles.soon}>Coming soon</span>
          )}
          <button type="button" className={styles.later} onClick={close}>
            Maybe later
          </button>
        </div>
      </div>
      <button type="button" className={styles.x} aria-label="Dismiss" onClick={close}>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
          <path d="M6 6l12 12M18 6L6 18" />
        </svg>
      </button>
    </aside>
  );
}
