import { useState } from 'react';
import type { FormEvent } from 'react';
import { deviceName, prettyCode } from '../../lib/codes';
import { pairWithCode } from './auth';

interface Props {
  /** sign-in by email is switched on for this server (so a way back to it is offered) */
  emailLogin: boolean;
  message: string;
  onUseEmail: () => void;
}

/** Connect this device with a one-time code from a device that is already set up, or an invite. */
export function PairCard({ emailLogin, message, onUseEmail }: Props) {
  const [code, setCode] = useState('');
  const [name, setName] = useState(deviceName);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function pair(ev: FormEvent) {
    ev.preventDefault();
    setError('');
    setBusy(true);
    try {
      await pairWithCode(code, name);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h2>Connect this device</h2>
      {!emailLogin && (
        <p className="hint">
          Sign-in by email is not switched on for this server yet. If you were given a code, enter it below. Otherwise ask whoever runs
          Deskhand to turn email sign-in on.
        </p>
      )}
      <p className="hint">
        Enter the code shown on a device that is already set up (Settings, then Add a phone or computer), or an invite code you were
        sent. A code works once.
      </p>
      <form onSubmit={pair} autoComplete="off">
        <label>
          Code
          <input
            inputMode="text"
            autoCapitalize="characters"
            autoComplete="one-time-code"
            spellCheck={false}
            placeholder="ABCD-EFGH"
            required
            value={code}
            onChange={(e) => setCode(prettyCode(e.target.value))}
          />
        </label>
        <label>
          Name this device
          <input maxLength={40} placeholder="e.g. My phone" required value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <button className="primary wide" type="submit" disabled={busy}>
          Connect
        </button>
        <p className="err" role="alert">
          {error || message}
        </p>
      </form>
      {emailLogin && (
        <button className="link" type="button" onClick={onUseEmail}>
          Sign in with email instead
        </button>
      )}
    </div>
  );
}
