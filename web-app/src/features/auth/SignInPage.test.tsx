import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useSession } from '../../state/session';
import { server } from '../../test/server';
import { openStream, renderApp } from '../../test/render';

const ME = { client_id: 'c1', name: 'Alex', workspace: { id: 'main', name: 'Alex' }, devices: [], extension_url: '' };

/** The calls the app makes once somebody is signed in (so a successful sign-in can finish rendering). */
function signedInRoutes() {
  server.use(http.get('*/v1/me', () => HttpResponse.json(ME)), http.get('*/v1/events', () => openStream().response));
}

function config(emailLogin: boolean) {
  server.use(http.get('*/v1/auth/config', () => HttpResponse.json({ email_login: emailLogin, signup: 'open' })));
}

afterEach(() => {
  window.location.hash = '';
});

describe('sign in by email', () => {
  it('asks for an email, sends the link, then takes the code and signs in', async () => {
    config(true);
    signedInRoutes();
    let emailBody: unknown;
    let verifyBody: unknown;
    server.use(
      http.post('*/v1/auth/email', async ({ request }) => ((emailBody = await request.json()), HttpResponse.json({ sent: true }))),
      http.post('*/v1/auth/verify', async ({ request }) => ((verifyBody = await request.json()), HttpResponse.json({ token: 'new-token', name: 'x' }))),
    );
    const user = userEvent.setup();
    renderApp('/');
    await user.type(await screen.findByLabelText(/your email/i), '  alex@example.com ');
    await user.click(screen.getByRole('button', { name: /email me a sign-in link/i }));
    expect(emailBody).toEqual({ email: 'alex@example.com' });
    expect(await screen.findByText(/we emailed alex@example.com/i)).toBeInTheDocument();

    const code = screen.getByLabelText(/code from the email/i);
    await user.type(code, 'abcd1234');
    expect(code).toHaveValue('ABCD-1234'); // typed in any case, shown tidy
    await user.click(screen.getByRole('button', { name: /^sign in$/i }));

    expect(await screen.findByText(/what should i do/i)).toBeInTheDocument(); // the app opened
    expect(verifyBody).toMatchObject({ code: 'ABCD1234' });
    expect(useSession.getState().token).toBe('new-token');
    expect(localStorage.getItem('pilot.token')).toBe('new-token');
  });

  it('shows the server\'s message when the code is wrong, and stays on the code box', async () => {
    config(true);
    server.use(
      http.post('*/v1/auth/email', () => HttpResponse.json({ sent: true })),
      http.post('*/v1/auth/verify', () => HttpResponse.json({ detail: 'that code is wrong, already used, or expired: ask for a new one' }, { status: 403 })),
    );
    const user = userEvent.setup();
    renderApp('/');
    await user.type(await screen.findByLabelText(/your email/i), 'a@b.co');
    await user.click(screen.getByRole('button', { name: /email me/i }));
    await user.type(await screen.findByLabelText(/code from the email/i), 'zzzzzzzz');
    await user.click(screen.getByRole('button', { name: /^sign in$/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/wrong, already used, or expired/i);
    expect(useSession.getState().token).toBeNull();
    expect(screen.getByLabelText(/code from the email/i)).toBeInTheDocument();
  });

  it('explains when the server cannot send email', async () => {
    config(true);
    server.use(http.post('*/v1/auth/email', () => HttpResponse.json({ sent: false, reason: 'no mail' })));
    const user = userEvent.setup();
    renderApp('/');
    await user.type(await screen.findByLabelText(/your email/i), 'a@b.co');
    await user.click(screen.getByRole('button', { name: /email me/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/cannot send email yet/i);
    expect(screen.queryByLabelText(/code from the email/i)).not.toBeInTheDocument();
  });

  it('can go back to a different email', async () => {
    config(true);
    server.use(http.post('*/v1/auth/email', () => HttpResponse.json({ sent: true })));
    const user = userEvent.setup();
    renderApp('/');
    await user.type(await screen.findByLabelText(/your email/i), 'a@b.co');
    await user.click(screen.getByRole('button', { name: /email me/i }));
    await user.click(await screen.findByRole('button', { name: /use a different email/i }));
    expect(screen.getByLabelText(/your email/i)).toBeInTheDocument();
  });

  it('signs in by itself from the link in the email (#code=...)', async () => {
    config(true);
    signedInRoutes();
    const verify = vi.fn();
    server.use(http.post('*/v1/auth/verify', async ({ request }) => (verify(await request.json()), HttpResponse.json({ token: 'from-link', name: 'x' }))));
    window.location.hash = '#code=ABCD-EFGH';
    renderApp('/');
    expect(await screen.findByText(/what should i do/i)).toBeInTheDocument();
    expect(verify).toHaveBeenCalledWith(expect.objectContaining({ code: 'ABCDEFGH' }));
    expect(useSession.getState().token).toBe('from-link');
    expect(window.location.hash).toBe(''); // the used code is taken out of the address bar
  });

  it('shows why a sign-in link failed', async () => {
    config(true);
    server.use(http.post('*/v1/auth/verify', () => HttpResponse.json({ detail: 'that code is wrong, already used, or expired: ask for a new one' }, { status: 403 })));
    window.location.hash = '#code=OLDOLDOL';
    renderApp('/');
    expect(await screen.findByRole('alert')).toHaveTextContent(/expired/i);
    expect(screen.getByLabelText(/your email/i)).toBeInTheDocument();
  });

  it('tells a person who was signed out why', async () => {
    config(true);
    useSession.getState().signOut('You were signed out of this device. Please sign in again.');
    renderApp('/');
    expect(await screen.findByRole('alert')).toHaveTextContent(/you were signed out/i);
  });
});

describe('the small print', () => {
  it('links the terms and the privacy policy (opened in a new tab so a half-typed email survives)', async () => {
    config(true);
    renderApp('/');
    const terms = await screen.findByRole('link', { name: 'terms' });
    expect(terms).toHaveAttribute('href', '/terms');
    expect(terms).toHaveAttribute('target', '_blank');
    expect(screen.getByRole('link', { name: 'privacy policy' })).toHaveAttribute('href', '/privacy');
  });
});

describe('connect with a code', () => {
  it('is offered when the server has no email sign-in, with the reason', async () => {
    config(false);
    renderApp('/');
    expect(await screen.findByText(/sign-in by email is not switched on/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /email instead/i })).not.toBeInTheDocument();
  });

  it('pairs with a code and a device name', async () => {
    config(false);
    signedInRoutes();
    let body: unknown;
    server.use(http.post('*/v1/pair', async ({ request }) => ((body = await request.json()), HttpResponse.json({ token: 'paired-token', name: 'Phone' }))));
    const user = userEvent.setup();
    renderApp('/');
    const code = await screen.findByLabelText(/^code$/i);
    await user.type(code, 'abcdefgh');
    const name = screen.getByLabelText(/name this device/i);
    await user.clear(name);
    await user.type(name, 'My phone');
    await user.click(screen.getByRole('button', { name: /^connect$/i }));
    expect(await screen.findByText(/what should i do/i)).toBeInTheDocument();
    expect(body).toEqual({ code: 'ABCDEFGH', name: 'My phone' });
    expect(useSession.getState().token).toBe('paired-token');
  });

  it('shows the server\'s message for a wrong code', async () => {
    config(false);
    server.use(http.post('*/v1/pair', () => HttpResponse.json({ detail: 'that code is wrong or has expired; ask for a new one' }, { status: 403 })));
    const user = userEvent.setup();
    renderApp('/');
    await user.type(await screen.findByLabelText(/^code$/i), 'abcdefgh');
    await user.click(screen.getByRole('button', { name: /^connect$/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/wrong or has expired/i);
  });

  it('switches between the email card and the code card when both are possible', async () => {
    config(true);
    const user = userEvent.setup();
    renderApp('/');
    await user.click(await screen.findByRole('button', { name: /i have a pairing code instead/i }));
    expect(screen.getByLabelText(/name this device/i)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /sign in with email instead/i }));
    expect(screen.getByLabelText(/your email/i)).toBeInTheDocument();
  });

  it('copes with an old server that has no auth config route', async () => {
    server.use(http.get('*/v1/auth/config', () => new HttpResponse(null, { status: 404 })));
    renderApp('/');
    await waitFor(() => expect(screen.getByLabelText(/^code$/i)).toBeInTheDocument());
  });
});
