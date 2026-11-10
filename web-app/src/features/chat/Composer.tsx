import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import type { DeviceView } from '../../api/types';
import { useTypewriter } from '../delight/useTypewriter';
import { useChat } from './chatStore';
import styles from './Composer.module.css';

export const EXAMPLES = [
  'Open Notepad and write a shopping list',
  'Open Calculator and add 7 and 5',
  "What's the latest AI news today?",
  'Open YouTube and play something relaxing',
];

const DEFAULT_PLACEHOLDER = 'Ask Deskhand to do something…';

interface Props {
  goal: string;
  onGoal: (text: string) => void;
  /** the text was typed by hand (so a voice countdown is cancelled) */
  onTyped: () => void;
  devices: DeviceView[];
  /** a task is running: Stop takes the place of Send */
  running: boolean;
  sending: boolean;
  onSend: () => void;
  onStop: () => void;
  mic: { supported: boolean; listening: boolean; toggle: () => void };
  /** the line under the box: voice status, notes about what was sent, the countdown */
  status: ReactNode;
}

/** The message box: grows with the text, Enter sends and Shift+Enter is a new line; target picker, mic, Stop in place of Send. */
export function Composer({ goal, onGoal, onTyped, devices, running, sending, onSend, onStop, mic, status }: Props) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [focused, setFocused] = useState(false);
  const focusTick = useChat((s) => s.composerFocus);
  const typed = useTypewriter(EXAMPLES, !goal && !focused && !running);

  // grow with the text, up to about six lines
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 160) + 'px';
  }, [goal]);

  useEffect(() => {
    if (focusTick > 0) ref.current?.focus();
  }, [focusTick]);

  return (
    <div className={styles.wrap}>
      <form
        className={styles.composer}
        autoComplete="off"
        onSubmit={(e) => {
          e.preventDefault();
          onSend();
        }}
      >
        <textarea
          ref={ref}
          id="goal"
          rows={1}
          maxLength={4000}
          value={goal}
          placeholder={typed || DEFAULT_PLACEHOLDER}
          aria-label="Command"
          onChange={(e) => {
            onTyped();
            onGoal(e.target.value);
          }}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              onSend(); // Enter sends, Shift+Enter is a new line
            }
          }}
        />
        <div className={styles.row}>
          <TargetPicker devices={devices} />
          <span className={styles.spacer} />
          {mic.supported && (
            <button
              type="button"
              className={`${styles.round} ${styles.mic}`}
              aria-pressed={mic.listening}
              aria-label="Speak a command"
              onClick={mic.toggle}
            >
              <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
                <rect x="9" y="3" width="6" height="11" rx="3" fill="none" stroke="currentColor" strokeWidth="1.8" />
                <path d="M5 11a7 7 0 0014 0M12 18v3" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              </svg>
            </button>
          )}
          {/* while a task is running this takes the place of the send button */}
          {running ? (
            <button type="button" className={`${styles.round} ${styles.stop}`} aria-label="Stop the task" title="Stop" onClick={onStop}>
              <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                <rect x="6" y="6" width="12" height="12" rx="2.5" fill="currentColor" />
              </svg>
            </button>
          ) : (
            <button type="submit" className={`${styles.round} ${styles.send}`} aria-label="Run" disabled={sending}>
              <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
                <path
                  d="M12 19V5M6 11l6-6 6 6"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </button>
          )}
        </div>
      </form>
      <p className={`hint ${styles.voiceLine}`} aria-live="polite">
        {status}
      </p>
    </div>
  );
}

/** "Run on": one computer or several (the same task goes to every chosen one); says which are offline. */
function TargetPicker({ devices }: { devices: DeviceView[] }) {
  const targets = useChat((s) => s.targets);
  const toggle = useChat((s) => s.toggleTarget);
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const labelId = useId();

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('click', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('click', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const chosen = devices.filter((d) => targets.includes(d.device_id));
  const off = chosen.filter((d) => !d.online);
  const label = !chosen.length ? 'Choose a computer' : chosen.length === 1 ? (chosen[0]?.name ?? '') : chosen.length + ' computers';
  const note = !chosen.length
    ? 'Tap a computer to choose where this runs. Pick several to send the same task to all of them.'
    : off.length
      ? off.map((d) => d.name).join(', ') +
        (off.length > 1 ? ' are' : ' is') +
        ' offline. On that computer open Deskhand from the Start menu (or press Ctrl+Alt+P) to bring it online.'
      : chosen.length > 1
        ? 'Sending to ' + chosen.length + ' computers at once.'
        : '';

  return (
    <div className={styles.targetWrap} ref={wrap}>
      <button
        type="button"
        className={styles.targetBtn}
        aria-haspopup="true"
        aria-expanded={open}
        aria-label="Choose which computer"
        onClick={() => setOpen((o) => !o)}
      >
        <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
          <rect x="3" y="4" width="18" height="12" rx="2" fill="none" stroke="currentColor" strokeWidth="1.8" />
          <path d="M8 20h8M12 16v4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
        </svg>
        <span>{label}</span>
      </button>
      {open && (
        <div className={styles.pop}>
          <span className={styles.lbl} id={labelId}>
            Run on
          </span>
          <div className={styles.targets} role="group" aria-labelledby={labelId}>
            {devices.map((d) => (
              <button
                key={d.device_id}
                type="button"
                className={styles.tchip}
                data-online={d.online ? '1' : '0'}
                aria-pressed={targets.includes(d.device_id)}
                onClick={() => toggle(d.device_id)}
              >
                <span className={styles.dot} data-on={d.online ? '1' : '0'} />
                <span>{d.name + (d.online ? '' : ' (offline)')}</span>
              </button>
            ))}
          </div>
          <p className="hint" aria-live="polite">
            {note}
          </p>
        </div>
      )}
    </div>
  );
}
