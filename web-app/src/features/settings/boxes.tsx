import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState, useSyncExternalStore } from 'react';
import { queryKeys } from '../../api/queries';
import {
  createPairCode,
  deletePreference,
  forgetSkill,
  getSkill,
  removeDevice,
  removeMember,
  settingsKeys,
  useMembers,
  usePreferences,
  useSkills,
  useUsage,
  useWorkspace,
} from '../../api/settings';
import type { Me } from '../../api/types';
import { useTheme } from '../../state/theme';
import type { ThemeMode } from '../../state/theme';
import { useUi } from '../../state/ui';
import { extensionLink } from '../connect/extension';
import { canInstall, isApple, isTouchDevice, onInstallAvailable, promptInstall } from './installPrompt';
import { readPrefs, writePref } from './prefs';
import type { LocalPrefs } from './prefs';
import styles from './dialog.module.css';

const n = (x: number | undefined) => Number(x || 0).toLocaleString();

// ------------------------------------------------------------------ Your computers
export function ComputersBox({ me }: { me: Me }) {
  const qc = useQueryClient();
  const [err, setErr] = useState('');
  const remove = useMutation({
    mutationFn: removeDevice,
    onSuccess: (_r, id) => {
      qc.setQueryData<Me>(queryKeys.me, (old) => (old ? { ...old, devices: old.devices.filter((d) => d.device_id !== id) } : old));
      void qc.invalidateQueries({ queryKey: queryKeys.me });
    },
    onError: (e) => setErr(e.message),
  });
  return (
    <section className={styles.box} aria-labelledby="set-computers">
      <h3 id="set-computers">Your computers</h3>
      <p className="hint">Windows computers that Deskhand works on.</p>
      <ul className={styles.list}>
        {me.devices.length === 0 ? <li className={styles.sub}>None yet.</li> : null}
        {me.devices.map((d) => (
          <li key={d.device_id}>
            <span className={styles.dot} data-on={d.online ? '1' : '0'} aria-hidden="true" />
            <span className={styles.name}>{d.name}</span>
            <span className={styles.sub}>{d.online ? 'online' : 'offline'}</span>
            <button
              type="button"
              aria-label={`Remove ${d.name}`}
              onClick={() => {
                setErr('');
                if (window.confirm(`Remove ${d.name}? Deskhand will stop controlling it.`)) remove.mutate(d.device_id);
              }}
            >
              Remove
            </button>
          </li>
        ))}
      </ul>
      {err ? (
        <p className="err" role="alert">
          {err}
        </p>
      ) : null}
      <button type="button" className="wide primary" onClick={() => useUi.getState().openConnect()}>
        Connect a computer
      </button>
    </section>
  );
}

// ------------------------------------------------------------------ Chrome extension
export function ExtensionBox({ me }: { me: Me }) {
  const url = extensionLink(me.extension_url);
  const need = me.devices.some((d) => !d.extension);
  return (
    <section className={styles.box} aria-labelledby="set-ext">
      <h3 id="set-ext">Chrome extension</h3>
      <p className="hint">
        Lets Deskhand work in your own Chrome, where you are already signed in to YouTube, Netflix and more. Without it Deskhand uses a separate
        browser profile and you would have to sign in to each site again.
      </p>
      <ul className={styles.list} aria-label="Extension status per computer">
        {me.devices.map((d) => (
          <li key={d.device_id}>
            <span className={styles.dot} data-on={d.extension ? '1' : '0'} aria-hidden="true" />
            <span className={styles.name}>{d.name}</span>
            <span className={styles.sub}>{!d.online ? 'computer offline' : d.extension ? 'Connected' : 'Not set up'}</span>
          </li>
        ))}
      </ul>
      {need && !url ? <p className={styles.extsoon}>Coming soon</p> : null}
      {need && url ? (
        <a className={styles.extlink} href={url} target="_blank" rel="noopener noreferrer">
          Add to Chrome
        </a>
      ) : null}
    </section>
  );
}

// ------------------------------------------------------------------ Use Deskhand from other devices
export function DevicesBox() {
  const qc = useQueryClient();
  const ws = useWorkspace();
  const members = useMembers();
  const [code, setCode] = useState('');
  const [err, setErr] = useState('');
  const make = useMutation({
    mutationFn: createPairCode,
    onSuccess: (r) => {
      setErr('');
      setCode(r.code);
    },
    onError: (e) => setErr(`Could not create a code: ${e.message}`),
  });
  const remove = useMutation({
    mutationFn: removeMember,
    onSuccess: () => qc.invalidateQueries({ queryKey: settingsKeys.members }),
    onError: (e) => setErr(e.message),
  });
  const line = ws.data
    ? `Workspace: ${ws.data.name} (${ws.data.tasks_24h} of ${ws.data.max_tasks_per_day} tasks used in the last 24 hours)`
    : 'Open this site on another phone or computer and enter a code to give commands from there.';
  return (
    <section className={styles.box} aria-labelledby="set-devices">
      <h3 id="set-devices">Use Deskhand from other devices</h3>
      <p className="hint">{line}</p>
      <ul className={styles.list}>
        {(members.data ?? []).map((m) => (
          <li key={m.client_id}>
            <span className={styles.name}>{m.name + (m.you ? ' (this device)' : '')}</span>
            {m.you ? null : (
              <button
                type="button"
                aria-label={`Remove ${m.name}`}
                onClick={() => {
                  if (window.confirm(`Remove ${m.name}? It will need a new code to come back.`)) remove.mutate(m.client_id);
                }}
              >
                Remove
              </button>
            )}
          </li>
        ))}
      </ul>
      <button type="button" className="wide" disabled={make.isPending} onClick={() => make.mutate()}>
        Get a code for another device
      </button>
      {code ? (
        <div>
          <p className="hint">Open Deskhand on that device and enter this code. It works once, for 10 minutes.</p>
          <code className={`${styles.cmd} ${styles.big}`} data-testid="pair-code">
            {code}
          </code>
        </div>
      ) : null}
      {err ? (
        <p className="err" role="alert">
          {err}
        </p>
      ) : null}
    </section>
  );
}

// ------------------------------------------------------------------ Appearance
const THEMES: { mode: ThemeMode; label: string }[] = [
  { mode: 'auto', label: 'Match system' },
  { mode: 'light', label: 'Light' },
  { mode: 'dark', label: 'Dark' },
];

export function AppearanceBox() {
  const mode = useTheme((s) => s.mode);
  const setMode = useTheme((s) => s.setMode);
  return (
    <section className={styles.box} aria-labelledby="set-look">
      <h3 id="set-look">Appearance</h3>
      <div className={styles.seg} role="group" aria-label="Theme">
        {THEMES.map((t) => (
          <button key={t.mode} type="button" aria-pressed={mode === t.mode} onClick={() => setMode(t.mode)}>
            {t.label}
          </button>
        ))}
      </div>
    </section>
  );
}

// ------------------------------------------------------------------ Preferences (this device)
export function PreferencesBox() {
  const [prefs, setPrefs] = useState<LocalPrefs>(() => {
    const p = readPrefs();
    // an alert preference only counts while the browser still allows notifications
    return { ...p, notify: p.notify && typeof Notification !== 'undefined' && Notification.permission === 'granted' };
  });
  const installable = useSyncExternalStore(onInstallAvailable, canInstall);

  const set = (key: keyof LocalPrefs, value: boolean) => {
    writePref(key, value);
    setPrefs((p) => ({ ...p, [key]: value }));
  };
  const toggleNotify = async (on: boolean) => {
    let granted = on;
    if (on && typeof Notification !== 'undefined') granted = (await Notification.requestPermission()) === 'granted';
    set('notify', granted);
  };

  return (
    <section className={styles.box} aria-labelledby="set-prefs">
      <h3 id="set-prefs">Preferences</h3>
      <label className={styles.check}>
        <input type="checkbox" checked={prefs.auto} onChange={(e) => set('auto', e.target.checked)} /> Send voice commands automatically (2 s to
        cancel)
      </label>
      <label className={styles.check}>
        <input type="checkbox" checked={prefs.speak} onChange={(e) => set('speak', e.target.checked)} /> Read results aloud
      </label>
      <label className={styles.check}>
        <input type="checkbox" checked={prefs.notify} onChange={(e) => void toggleNotify(e.target.checked)} /> Alert me when Deskhand needs
        approval
      </label>
      {installable && isTouchDevice() ? (
        <button type="button" className="wide" onClick={() => void promptInstall()}>
          Add Deskhand to this device&apos;s home screen
        </button>
      ) : null}
      {isApple() ? <p className="hint">On iPhone/iPad: tap Share, then &quot;Add to Home Screen&quot;.</p> : null}
    </section>
  );
}

// ------------------------------------------------------------------ Usage today
export function UsageBox() {
  const q = useUsage();
  let text = 'Loading…';
  if (q.isError) text = 'Usage is not available.';
  else if (q.data) {
    const u = q.data;
    text = [
      `Tasks: ${u.tasks_24h} of ${u.max_tasks_per_day} in the last 24 hours`,
      `Model calls: ${n(u.model_calls_today)}`,
      `Tokens: ${n(u.tokens_today)}${u.token_limit ? ` of ${n(u.token_limit)}` : ''}`,
    ].join(' · ');
    if (u.queue?.waiting) text += `. Busy right now: ${u.queue.waiting} waiting in line.`;
    if (u.paused_for_budget) text += ' Paused: the service has used up its model budget for today and resets at midnight UTC.';
  }
  return (
    <section className={styles.box} aria-labelledby="set-usage">
      <h3 id="set-usage">Usage today</h3>
      <p className="hint" data-testid="usage-line">
        {text}
      </p>
    </section>
  );
}

// ------------------------------------------------------------------ Your preferences (rules Deskhand remembers)
export function SavedPreferencesBox() {
  const qc = useQueryClient();
  const q = usePreferences();
  const del = useMutation({
    mutationFn: deletePreference,
    onSettled: () => qc.invalidateQueries({ queryKey: settingsKeys.preferences }), // gone either way: show the truth
  });
  const list = q.data ?? [];
  return (
    <section className={styles.box} aria-labelledby="set-saved">
      <h3 id="set-saved">Your preferences</h3>
      <p className="hint">
        Rules you asked Deskhand to remember, like “when I say calming music, play lofi”. Add one by saying “set it as a preference” in a message.
        Deskhand saves it and does nothing else.
      </p>
      <ul className={styles.list}>
        {list.map((p) => (
          <li key={p.id}>
            <span className={styles.name}>{p.text}</span>
            <button type="button" aria-label={`Delete preference: ${p.text}`} onClick={() => del.mutate(p.id)}>
              Delete
            </button>
          </li>
        ))}
      </ul>
      {q.isSuccess && list.length === 0 ? <p className="hint">None yet.</p> : null}
    </section>
  );
}

// ------------------------------------------------------------------ What Deskhand has learned
function SkillRow({ id, title, worked }: { id: string; title: string; worked: number }) {
  const qc = useQueryClient();
  const [text, setText] = useState<string | null>(null);
  const [err, setErr] = useState('');
  const view = async () => {
    if (text !== null) return setText(null);
    try {
      setText((await getSkill(id)).text);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'could not open this');
    }
  };
  const forget = async () => {
    if (!window.confirm(`Forget what Deskhand learned about ${title}?`)) return;
    try {
      await forgetSkill(id);
    } catch {
      /* already gone */
    }
    void qc.invalidateQueries({ queryKey: settingsKeys.skills });
  };
  return (
    <li className={styles.wrap}>
      <span className={styles.name}>{title}</span>
      <span className={styles.sub}>{worked} worked</span>
      <button type="button" aria-expanded={text !== null} aria-label={`View notes about ${title}`} onClick={() => void view()}>
        View
      </button>
      <button type="button" aria-label={`Forget ${title}`} onClick={() => void forget()}>
        Forget
      </button>
      {text !== null ? <pre className={styles.notes}>{text}</pre> : null}
      {err ? (
        <span className="err" role="alert">
          {err}
        </span>
      ) : null}
    </li>
  );
}

export function SkillsBox({ forceOpen }: { forceOpen: boolean }) {
  const q = useSkills();
  const [open, setOpen] = useState(forceOpen);
  const list = q.data ?? [];
  return (
    <details className={styles.box} id="set-skills" open={open} onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary>
        <h3>What Deskhand has learned</h3>
      </summary>
      <p className="hint">After each task it keeps notes on how each app or site worked, and uses them next time.</p>
      <ul className={styles.list}>
        {q.isSuccess && list.length === 0 ? <li className={styles.sub}>Nothing yet. It learns as you use it.</li> : null}
        {list.map((s) => (
          <SkillRow key={s.id} id={s.id} title={s.title || 'Untitled'} worked={s.runs.ok || 0} />
        ))}
      </ul>
    </details>
  );
}
