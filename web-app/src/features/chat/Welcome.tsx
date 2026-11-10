import { useSyncExternalStore } from 'react';
import type { DeviceView } from '../../api/types';
import type { Mood } from '../../lib/chats';
import { useUi } from '../../state/ui';
import { DeviceOrbit } from '../delight/DeviceOrbit';
import { Orb } from '../orb/Orb';
import { useChat } from './chatStore';
import { EXAMPLES } from './Composer';
import styles from './Welcome.module.css';

const NARROW = '(max-width: 520px)';
function subscribe(cb: () => void): () => void {
  const mq = window.matchMedia?.(NARROW);
  mq?.addEventListener('change', cb);
  return () => mq?.removeEventListener('change', cb);
}
const isNarrow = (): boolean => window.matchMedia?.(NARROW).matches ?? false;

interface Props {
  devices: DeviceView[];
  mood: Mood;
  /** goes up by one each time a task is sent (a spark runs from the orb to the chosen computers) */
  pulse: number;
  onExample: (text: string) => void;
}

/** The start screen: the orb with the person's computers around it (press one to choose where the next task runs), and examples. */
export function Welcome({ devices, mood, pulse, onExample }: Props) {
  const targets = useChat((s) => s.targets);
  const toggle = useChat((s) => s.toggleTarget);
  const narrow = useSyncExternalStore(subscribe, isNarrow, () => false);
  const none = devices.length === 0;

  return (
    <section className={styles.welcome}>
      <div className={styles.orbit}>
        <DeviceOrbit
          devices={devices}
          selected={targets}
          onToggle={toggle}
          onConnect={() => useUi.getState().openConnect()}
          mood={mood}
          pulse={pulse}
          radius={narrow ? 104 : 140}
          ringSize={narrow ? 4 : 6}
        >
          <Orb />
        </DeviceOrbit>
      </div>
      <h2>What should I do?</h2>
      <p className="hint">Tell Deskhand a task in your own words. It works on your computer and asks you when it needs to.</p>
      {!none && (
        <div className={styles.chips} aria-label="Examples">
          {EXAMPLES.map((t) => (
            <button key={t} type="button" onClick={() => onExample(t)}>
              {t}
            </button>
          ))}
        </div>
      )}
      {none && (
        <div className={styles.setup}>
          <h3>No computer connected yet</h3>
          <p className="lead">Deskhand does its work on a Windows computer. Connect one to get started. It takes about a minute.</p>
          <button className="primary" type="button" onClick={() => useUi.getState().openConnect()}>
            Connect a computer
          </button>
        </div>
      )}
    </section>
  );
}
