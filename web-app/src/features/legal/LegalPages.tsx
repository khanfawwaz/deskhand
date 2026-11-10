/**
 * The privacy policy and the terms of use, as pages of the app (clean addresses: /privacy and /terms).
 *
 * The words are NOT written here. They live once, in the server's pages (src/taskpilot/web/privacy.html and terms.html),
 * and `node scripts/sync-legal.mjs` copies them into ./content/. A unit test fails when the copies fall out of step.
 */
import { useEffect } from 'react';
import type { MouseEvent } from 'react';
import { Link, NavLink, useLocation, useNavigate } from 'react-router-dom';
import '../../styles/legal.css';
import privacyHtml from './content/privacy.html?raw';
import termsHtml from './content/terms.html?raw';

/** Links inside the text that point at other pages of this app (as the server's pages name them). */
const IN_APP: Record<string, string> = { '/privacy': '/privacy', '/terms': '/terms', '/app/': '/' };

function LegalPage({ title, html }: { title: string; html: string }) {
  const navigate = useNavigate();
  const { hash } = useLocation();

  useEffect(() => {
    document.title = `${title} - Deskhand`;
    return () => {
      document.title = 'Deskhand';
    };
  }, [title]);

  // opened with #section in the address: go there once the text is on the page
  useEffect(() => {
    if (!hash) return;
    let el: HTMLElement | null = null;
    try {
      el = document.getElementById(decodeURIComponent(hash.slice(1)));
    } catch {
      /* a badly formed address: stay at the top */
    }
    el?.scrollIntoView();
  }, [hash, html]);

  // the text is plain HTML: links to other pages of the app go through the router instead of reloading
  const onClick = (e: MouseEvent<HTMLElement>) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = (e.target as HTMLElement).closest('a');
    const to = a ? IN_APP[a.getAttribute('href') ?? ''] : undefined;
    if (!to) return;
    e.preventDefault();
    void navigate(to);
    window.scrollTo(0, 0);
  };

  return (
    <div className="legal">
      <header className="legal-top">
        <Link className="legal-brand" to="/">
          <span className="logo" aria-hidden="true" />
          <span className="legal-name">Deskhand</span>
        </Link>
        <nav aria-label="Legal pages">
          <NavLink to="/privacy">Privacy</NavLink>
          <NavLink to="/terms">Terms</NavLink>
          <Link to="/">Open Deskhand</Link>
        </nav>
      </header>
      {/* our own static text, copied at build time from the server's pages (see the comment at the top) */}
      <main className="legal-main" onClick={onClick} dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  );
}

export const PrivacyPage = () => <LegalPage title="Privacy policy" html={privacyHtml} />;
export const TermsPage = () => <LegalPage title="Terms of use" html={termsHtml} />;
