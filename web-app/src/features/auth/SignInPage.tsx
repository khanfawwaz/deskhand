import { useEffect, useState } from 'react';
import { useAuthConfig } from '../../api/queries';
import { useSession } from '../../state/session';
import { codeFromHash, verifyEmailCode } from './auth';
import { EmailCard } from './EmailCard';
import { PairCard } from './PairCard';

/** Shown whenever there is no token. Offers email sign-in when the server has it, a pairing code otherwise. */
export function SignInPage() {
  const { data: cfg } = useAuthConfig();
  const notice = useSession((s) => s.notice);
  const [showCode, setShowCode] = useState(false);
  const [linkError, setLinkError] = useState('');

  // The sign-in link in the email carries its code in the #fragment: use it straight away,
  // also if the app was already open when the link was tapped.
  useEffect(() => {
    const redeemLink = async () => {
      const code = codeFromHash(window.location.hash);
      if (!code || useSession.getState().token) return;
      try {
        await verifyEmailCode(code);
      } catch (e) {
        setLinkError(e instanceof Error ? e.message : String(e));
        setShowCode(false);
      }
    };
    void redeemLink();
    window.addEventListener('hashchange', redeemLink);
    return () => window.removeEventListener('hashchange', redeemLink);
  }, []);

  const emailLogin = !!cfg?.email_login;
  const message = linkError || notice;

  return (
    <>
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden="true" />
          <h1>Deskhand</h1>
        </div>
      </header>
      <main className="pair">
        {cfg !== undefined && (
          <>
            {emailLogin && !showCode ? (
              <EmailCard message={message} onHaveCode={() => setShowCode(true)} />
            ) : (
              <PairCard emailLogin={emailLogin} message={message} onUseEmail={() => setShowCode(false)} />
            )}
            <p className="legal-links">
              By signing in you agree to the{' '}
              <a href="/terms" target="_blank" rel="noopener">
                terms
              </a>
              . Read how your data is handled in the{' '}
              <a href="/privacy" target="_blank" rel="noopener">
                privacy policy
              </a>
              .
            </p>
          </>
        )}
      </main>
    </>
  );
}
