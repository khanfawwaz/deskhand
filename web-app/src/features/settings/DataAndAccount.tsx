import { useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import type { FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { DELETE_PHRASE, deleteAccount, deleteAllChats, emergencyStop, fetchExport, normalizePhrase, unpair } from '../../api/settings';
import type { Me } from '../../api/types';
import { useSession } from '../../state/session';
import { useUi } from '../../state/ui';
import { Modal } from './Modal';
import styles from './dialog.module.css';

/** Hand a downloaded blob to the browser as a file. */
function saveAs(blob: Blob, name: string): void {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}

interface Say {
  text: string;
  bad: boolean;
}

// ------------------------------------------------------------------ Your data
export function DataBox() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [say, setSay] = useState<Say>({ text: '', bad: false });
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);

  const download = async () => {
    setSay({ text: 'Preparing your data…', bad: false });
    try {
      saveAs(await fetchExport(), `deskhand-data-${new Date().toISOString().slice(0, 10)}.json`);
      setSay({ text: 'Your data was downloaded as a file.', bad: false });
    } catch (e) {
      if (e instanceof Error && e.message === 'unauthorized') return; // signed out: the app already moved on
      setSay({ text: e instanceof Error ? e.message : 'could not download your data', bad: true });
    }
  };

  const wipeChats = async () => {
    if (!window.confirm('Delete all your chats? Anything still running is kept.')) return;
    setBusy(true);
    try {
      const r = await deleteAllChats();
      void qc.invalidateQueries({ queryKey: ['tasks'] });
      navigate('/');
      setSay({ text: r.deleted ? `Deleted ${r.deleted} ${r.deleted === 1 ? 'task' : 'tasks'} from your chats.` : 'There were no chats to delete.', bad: false });
    } catch (e) {
      setSay({ text: e instanceof Error ? e.message : 'could not delete your chats', bad: true });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className={styles.box} aria-labelledby="set-data">
      <h3 id="set-data">Your data</h3>
      <p className="hint">
        It is yours. Take a copy of everything Deskhand has stored about you, or erase it all. Finished tasks are also deleted automatically after 90
        days.
      </p>
      <button type="button" className="wide" onClick={() => void download()}>
        Download my data
      </button>
      <button type="button" className="wide" disabled={busy} onClick={() => void wipeChats()}>
        Delete all chats
      </button>
      <button type="button" className="danger wide" onClick={() => setAsking(true)}>
        Delete my account and data…
      </button>
      <p className={say.bad ? 'err' : 'hint'} role="status" aria-live="polite">
        {say.text}
      </p>
      <Modal open={asking} onClose={() => setAsking(false)} labelledBy="deleteTitle">
        <DeleteForm onCancel={() => setAsking(false)} />
      </Modal>
    </section>
  );
}

/** Type the phrase to erase everything. The server checks the same phrase the same way. */
function DeleteForm({ onCancel }: { onCancel: () => void }) {
  const [typed, setTyped] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const ok = normalizePhrase(typed) === DELETE_PHRASE;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!ok || busy) return;
    setBusy(true);
    setErr('');
    try {
      await deleteAccount(typed);
      useUi.getState().closeSettings();
      useSession.getState().signOut('Your account and data were deleted.');
    } catch (error) {
      if (error instanceof Error && error.message === 'unauthorized') return;
      setErr(error instanceof Error ? error.message : 'could not delete your data');
      setBusy(false);
    }
  };

  return (
    <form className={`${styles.menu} ${styles.confirm}`} autoComplete="off" onSubmit={(e) => void submit(e)}>
      <h2 id="deleteTitle">Delete your account and data?</h2>
      <p className="lead">This erases everything Deskhand has stored about you, for good:</p>
      <ul className={styles.perks}>
        <li>your tasks, their steps and results</li>
        <li>your connected computers and signed-in devices (they will be signed out)</li>
        <li>what Deskhand has learned, your usage counts and your workspace</li>
      </ul>
      <p className="hint">Anything running is stopped first. It cannot be undone. Tip: download your data first.</p>
      <label>
        Type <b>{DELETE_PHRASE}</b> to confirm
        <input
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder={DELETE_PHRASE}
          autoComplete="off"
          spellCheck={false}
          autoFocus
        />
      </label>
      <p className="err" role="alert">
        {err}
      </p>
      <div className={styles.actions}>
        <button type="button" className="ghost" onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" className="danger" disabled={!ok || busy}>
          Delete everything
        </button>
      </div>
    </form>
  );
}

// ------------------------------------------------------------------ Account and safety
export function AccountBox({ me }: { me: Me }) {
  const [say, setSay] = useState<Say>({ text: '', bad: false });

  const stop = async () => {
    if (!window.confirm('Stop every running task right now?')) return;
    try {
      await emergencyStop();
      setSay({ text: 'Emergency stop sent: all tasks are being stopped.', bad: false });
    } catch (e) {
      setSay({ text: e instanceof Error ? e.message : 'could not send the stop', bad: true });
    }
  };

  const signOut = async () => {
    if (!window.confirm('Forget this device? You will need a new pairing code.')) return;
    try {
      await unpair();
    } catch {
      /* already gone */
    }
    useUi.getState().closeSettings();
    useSession.getState().signOut();
  };

  return (
    <section className={styles.box} aria-labelledby="set-account">
      <h3 id="set-account">Account and safety</h3>
      <p className="hint">Signed in as {me.name} on this device.</p>
      <button type="button" className="danger wide" onClick={() => void stop()}>
        Emergency stop (all tasks)
      </button>
      <button type="button" className="wide" onClick={() => void signOut()}>
        Sign out of this device
      </button>
      <p className={say.bad ? 'err' : 'hint'} role="status" aria-live="polite">
        {say.text}
      </p>
      <p className="hint legal-links">
        <a href="/privacy" target="_blank" rel="noopener">
          Privacy policy
        </a>{' '}
        ·{' '}
        <a href="/terms" target="_blank" rel="noopener">
          Terms
        </a>
      </p>
    </section>
  );
}
