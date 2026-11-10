import { useEffect, useEffectEvent, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { MAX_PODS, driftOffset, networkLayout, type Point } from './orbit';
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
  /** distance from the orb's centre up to the first ring of computers, in px (the arc is wider when there is room) */
  radius?: number;
  /** how many computers sit on the first ring around the orb; the rest form outer rings, tied to a computer inside */
  ringSize?: number;
  /** the orb itself */
  children: ReactNode;
}

const Monitor = () => (
  <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
    <rect x="3" y="4" width="18" height="12" rx="2" fill="none" stroke="currentColor" strokeWidth="1.8" />
    <path d="M8 20h8M12 16v4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
  </svg>
);

const STAGGER = 1.2; // with four or more computers on a ring, every second one sits this much further out
const ORB_R = 100; // the orb and its ring, as seen from the centre

/** A line of the network: from a computer (or the orb, -1) to a computer. "mesh" lines tie neighbours on the first ring. */
interface Link {
  from: number;
  to: number;
  mesh?: boolean;
}

/**
 * The orb with the person's computers around it, drawn as a small network. Each computer is a pod you can press to
 * choose where the next task runs. The first ring is tied to the orb; more computers grow outwards, each tied to a
 * computer on the ring inside. A chosen computer's path to the orb lights up, and sending a task sends a spark out.
 */
export function DeviceOrbit({ devices, selected, onToggle, onConnect, mood = 'idle', pulse = 0, radius = 168, ringSize = MAX_PODS, children }: Props) {
  const n = devices.length || 1;
  const inner = Math.min(n, Math.max(1, Math.min(ringSize, MAX_PODS)));

  // How wide the arcs may be depends on the room there is: measured, so pods and their names never leave the screen.
  const box = useRef<HTMLDivElement>(null);
  const [room, setRoom] = useState(0);
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver !== 'function') return;
    const ro = new ResizeObserver(([e]) => setRoom(Math.round(e?.contentRect.width ?? 0)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const compact = room > 0 && room < 600; // a phone: shorter names
  const label = compact ? 44 : 64; // half the widest name, plus a little air
  const stagger = inner >= 4 ? STAGGER : 1;
  const widest = radius * (inner >= 4 ? 1.65 : 1.35);
  const edge = room > 0 ? room / 2 - label - 8 : Infinity; // the furthest a pod's centre may sit from the middle
  const rx = Math.round(Math.max(radius * 0.8, Math.min(widest, edge / stagger)));
  const outerRx = Math.round(Math.max(rx, Math.min(rx * 1.45, edge)));
  const nodes = networkLayout(n, rx, radius, inner, outerRx, STAGGER, compact ? inner : inner + 2, compact ? 1.2 : 0.85);

  const top = Math.max(radius, ...nodes.map((p) => -p.y));
  const far = Math.max(rx, ...nodes.map((p) => Math.abs(p.x)));
  const w = Math.max(room, 2 * (far + label + 8));
  const half = top + 64; // room above the centre for the highest pod and its name
  const h = 2 * half;
  const cx = w / 2;

  const links: Link[] = devices.length === 0 ? [] : nodes.map((p, i) => ({ from: p.parent, to: i }));
  if (devices.length >= 3) for (let i = 1; i < inner; i++) links.push({ from: i - 1, to: i, mesh: true });
  // a chosen computer lights up its whole path back to the orb
  const lit = new Set<number>();
  devices.forEach((d, i) => {
    if (!selected.includes(d.device_id)) return;
    for (let j = i; j >= 0; j = nodes[j]?.parent ?? -1) lit.add(j);
  });

  // The computers drift slowly around their places, like nodes in a network. Positions are written straight to the
  // elements (no re-render per frame); every line follows the pods at both of its ends. Crowded rings drift less.
  const amp = n >= 4 ? 6 : 9;
  const pods = useRef<(HTMLElement | null)[]>([]);
  const wires = useRef<(SVGLineElement | null)[]>([]);
  const move = useEffectEvent((t: number) => {
    const at: Point[] = nodes.map((p, i) => {
      const o = driftOffset(i, t, amp);
      pods.current[i]?.style.setProperty('--dx', `${o.x}px`);
      pods.current[i]?.style.setProperty('--dy', `${o.y}px`);
      return { x: cx + p.x + o.x, y: half + p.y + o.y };
    });
    links.forEach((l, k) => {
      const line = wires.current[k];
      const a = l.from < 0 ? { x: cx, y: half } : at[l.from];
      const b = at[l.to];
      if (!line || !a || !b) return;
      line.setAttribute('x1', String(a.x));
      line.setAttribute('y1', String(a.y));
      line.setAttribute('x2', String(b.x));
      line.setAttribute('y2', String(b.y));
    });
  });
  const count = devices.length;
  useEffect(() => {
    if (count === 0 || (typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches)) return;
    const start = performance.now();
    let frame = 0;
    const tick = (now: number) => {
      if (!document.hidden) move((now - start) / 1000);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [count]);

  const end = (i: number) => (i < 0 ? { x: cx, y: half } : { x: cx + (nodes[i]?.x ?? 0), y: half + (nodes[i]?.y ?? 0) });

  return (
    <div ref={box} className={styles.room} style={{ marginBottom: -(half - ORB_R - 20) }}>
      <div className={styles.stage} style={{ '--h': `${h}px` } as CSSProperties} data-mood={mood} data-compact={compact ? '1' : undefined} role="group" aria-label="Your computers">
        <svg className={styles.wires} viewBox={`0 0 ${w} ${h}`} width={w} height={h} aria-hidden="true">
          {links.map((l, k) => {
            const a = end(l.from), b = end(l.to);
            const on = !l.mesh && lit.has(l.to);
            return (
              <line
                key={`${l.from}-${l.to}-${l.mesh ? 'm' : 'p'}`}
                ref={(el) => {
                  wires.current[k] = el;
                }}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                className={l.mesh ? styles.mesh : on ? styles.wireOn : styles.wire}
              />
            );
          })}
          <circle cx={cx} cy={half} r={86} className={styles.ring} />
        </svg>

        <div className={styles.core}>{children}</div>

        {pulse > 0 &&
          devices.map((d, i) => {
            const p = nodes[i];
            if (!p || !selected.includes(d.device_id)) return null;
            return <span key={`${d.device_id}-${pulse}`} className={styles.spark} style={{ '--tx': `${p.x}px`, '--ty': `${p.y}px` } as CSSProperties} />;
          })}

        {devices.length === 0 ? (
          <button type="button" className={`${styles.pod} ${styles.empty}`} style={podStyle(nodes[0])} onClick={onConnect}>
            <span className={styles.disc}>
              <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                <path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
            </span>
            <span className={styles.name}>Connect a computer</span>
          </button>
        ) : (
          devices.map((d, i) => {
            const on = selected.includes(d.device_id);
            return (
              <button
                key={d.device_id}
                ref={(el) => {
                  pods.current[i] = el;
                }}
                type="button"
                className={styles.pod}
                style={podStyle(nodes[i])}
                data-online={d.online ? '1' : '0'}
                aria-pressed={on}
                aria-label={`${d.name}, ${d.online ? 'online' : 'offline'}${on ? ', chosen' : ''}`}
                title={d.online ? `Run tasks on ${d.name}` : `${d.name} is offline`}
                onClick={() => onToggle(d.device_id)}
              >
                <span className={styles.disc}>
                  <Monitor />
                  <i className={styles.dot} aria-hidden="true" />
                </span>
                <span className={styles.name}>{d.name}</span>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}

function podStyle(p: Point | undefined): CSSProperties {
  return { '--x': `${p?.x ?? 0}px`, '--y': `${p?.y ?? 0}px` } as CSSProperties;
}
