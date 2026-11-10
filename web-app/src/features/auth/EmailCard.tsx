import { useState } from 'react';
import type { FormEvent } from 'react';
import { prettyCode } from '../../lib/codes';
import { sendSignInEmail, verifyEmailCode } from './auth';

interface Props {
  /** shown in the error line until the person does something (e.g. "You were signed out") */
  message: string;
  onHaveCode: () => void;
}

/** Sign in by email: no password, a link and a code are emailed. */
export function EmailCard({ message, onHaveCode }: Props) {
  const [step, setStep] = useState<'email' | 'verify'>('email');
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function send(ev: FormEvent) {
    ev.preventDefault();
    setError('');
    setBusy(true);
    try {
      const r = await sendSignInEmail(email);
      if (!r.sent) setError('This server cannot send email yet. Ask whoever runs it for a pairing code.');
      else setStep('verify');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function verify(ev: FormEvent) {
    ev.preventDefault();
    setError('');
    setBusy(true);
    try {
      await verifyEmailCode(code);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <div className="card hero">
      <h2>Welcome to Deskhand</h2>
      <p className="lead">Tell your computer what to do, by voice or text, from anywhere. It does the clicking and typing for you.</p>
      <ul className="perks">
        <li>Works on your own computer, not in the cloud</li>
        <li>Asks you first before anything risky</li>
        <li>No password: we email you a sign-in link</li>
      </ul>

      {step === 'email' ? (
        <form onSubmit={send} autoComplete="on">
          <label>
            Your email
            <input
              type="email"
              inputMode="email"
              autoComplete="email"
              autoCapitalize="none"
              spellCheck={false}
              placeholder="you@example.com"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          <button className="primary wide" type="submit" disabled={busy}>
            Email me a sign-in link
          </button>
        </form>
      ) : (
        <form onSubmit={verify} autoComplete="off">
          <p className="hint">We emailed {email.trim()}. Tap the link in the email, or type the code from it here.</p>
          <label>
            Code from the email
            <input
              inputMode="text"
              autoCapitalize="characters"
              autoComplete="one-time-code"
              spellCheck={false}
              placeholder="ABCD-EFGH"
              required
              autoFocus
              value={code}
              onChange={(e) => setCode(prettyCode(e.target.value))}
            />
          </label>
          <button className="primary wide" type="submit" disabled={busy}>
            Sign in
          </button>
          <button
            className="link wide"
            type="button"
            onClick={() => {
              setStep('email');
              setError('');
            }}
          >
            Use a different email
          </button>
        </form>
      )}

      <p className="err" role="alert">
        {error || message}
      </p>
      <button className="link" type="button" onClick={onHaveCode}>
        I have a pairing code instead
      </button>
    </div>
  );
}
