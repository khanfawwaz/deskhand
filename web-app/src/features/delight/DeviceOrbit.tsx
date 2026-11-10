import type { CSSProperties, ReactNode } from 'react';
import { MAX_PODS, orbitPositions } from './orbit';
import styles from './DeviceOrbit.module.css';

/** What Deskhand is doing right now, shown as a ring around the orb. */
export type Mood = 'idle' | 'listening' | 'working' | 'done' | 'stuck';

export interface OrbitDevice {
  device_id: string;
  name: string;
  online: boolean;
}

interface Props {
  devices: OrbitDevice[];
  /** ids of the computers the next task will run on */
  selected: string[];
  onToggle: (deviceId: string) => void;
  /** no computer yet: the single empty pod calls this */
  onConnect: () => void;
  mood?: Mood;
  /** change this number to send a spark from the orb to each chosen computer (a task was just sent) */
  pulse?: number;
  /** distance from the orb's centre to each pod, in px */
  radius?: number;
  /** the orb itself */
  children: ReactNode;
}

const Monitor = () => (
  <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <rect x="3" y="4" width="18" height="12" rx="2" fill="none" stroke="currentColor" strokeWidth="1.8" />
    <path d="M8 20h8M12 16v4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
  </svg>
);

/**
 * The orb with the person's computers around it. Each computer is a pod you can press to choose where the next task
 * runs; a line ties a chosen pod to the orb, and sending a task sends a spark down that line. This is the one place
 * the app shows what it is: one voice, several computers.
 */
export function DeviceOrbit({ devices, selected, onToggle, onConnect, mood = 'idle', pulse = 0, radius = 168, children }: Props) {
  const shown = devices.slice(0, devices.length > MAX_PODS ? MAX_PODS - 1 : MAX_PODS);
  const hidden = devices.length - shown.length;
  const slots = orbitPositions(shown.length + (hidden > 0 ? 1 : 0) || 1, radius);
  const size = radius * 2 + 120;
  const half = size / 2;

  return (
    <div className={styles.stage} style={{ '--stage': `${size}px` } as CSSProperties} data-mood={mood} role="group" aria-label="Your computers">
      <svg className={styles.wires} viewBox={`0 0 ${size} ${size}`} width={size} height={size} aria-hidden="true">
        {shown.map((d, i) => {
          const p = slots[i];
          if (!p) return null;
          const on = selected.includes(d.device_id);
          return <line key={d.device_id} x1={half} y1={half} x2={half + p.x} y2={half + p.y} className={on ? styles.wireOn : styles.wire} />;
        })}
        <circle cx={half} cy={half} r={86} className={styles.ring} />
      </svg>

      <div className={styles.core}>{children}</div>

      {pulse > 0 &&
        shown.map((d, i) => {
          const p = slots[i];
          if (!p || !selected.includes(d.device_id)) return null;
          return <span key={`${d.device_id}-${pulse}`} className={styles.spark} style={{ '--tx': `${p.x}px`, '--ty': `${p.y}px` } as CSSProperties} />;
        })}

      {devices.length === 0 ? (
        <button type="button" className={`${styles.pod} ${styles.empty}`} style={podStyle(slots[0], 0)} onClick={onConnect}>
          <span className={styles.disc}>
            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
              <path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </span>
          <span className={styles.name}>Connect a computer</span>
        </button>
      ) : (
        shown.map((d, i) => {
          const on = selected.includes(d.device_id);
          return (
            <button
              key={d.device_id}
              type="button"
              className={styles.pod}
              style={podStyle(slots[i], i)}
              data-online={d.online ? '1' : '0'}
              aria-pressed={on}
              aria-label={`${d.name}, ${d.online ? 'online' : 'offline'}${on ? ', chosen' : ''}`}
              title={d.online ? `Run tasks on ${d.name}` : `${d.name} is offline`}
              onClick={() => onToggle(d.device_id)}
            >
              <span className={styles.disc}>
                <Monitor />
              </span>
              <span className={styles.name}>{d.name}</span>
            </button>
          );
        })
      )}

      {hidden > 0 && (
        <span className={`${styles.pod} ${styles.more}`} style={podStyle(slots[shown.length], shown.length)}>
          <span className={styles.disc}>+{hidden}</span>
          <span className={styles.name}>more below</span>
        </span>
      )}
    </div>
  );
}

function podStyle(p: { x: number; y: number } | undefined, i: number): CSSProperties {
  return { '--x': `${p?.x ?? 0}px`, '--y': `${p?.y ?? 0}px`, '--i': i } as CSSProperties;
}
