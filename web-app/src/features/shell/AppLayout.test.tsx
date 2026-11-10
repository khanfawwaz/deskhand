import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { beforeAll, describe, expect, it } from 'vitest';
import { useSession } from '../../state/session';
import { server } from '../../test/server';
import { openStream, renderApp } from '../../test/render';

// the live feed pushes updates from the network, not from a click
beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = false;
});

const ME = {
  client_id: 'c1',
  name: 'Alex',
  workspace: { id: 'main', name: 'Alex Home' },
  devices: [{ device_id: 'd1', name: 'Alex-Laptop', online: true, last_seen: 1, extension: false }],
  extension_url: '',
};

function signedIn(me = ME) {
  useSession.setState({ token: 'tok' });
  const stream = openStream();
  server.use(http.get('*/v1/me', () => HttpResponse.json(me)), http.get('*/v1/events', () => stream.response));
  return stream;
}

describe('the signed-in app', () => {
  it('shows who and what: workspace, computers, the welcome and the preview note', async () => {
    signedIn();
    renderApp('/');
    expect(await screen.findByRole('heading', { name: /what should i do/i })).toBeInTheDocument();
    const side = screen.getByRole('complementary', { name: /deskhand/i });
    expect(within(side).getByText('Alex Home')).toBeInTheDocument();
    expect(within(side).getByText('Alex-Laptop')).toBeInTheDocument();
    expect(screen.getByRole('note')).toHaveTextContent(/preview of the new deskhand app/i);
  });

  it('goes online, and keeps the list of computers current from the live feed', async () => {
    const stream = signedIn();
    renderApp('/');
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('online'));
    stream.send(
      'event: devices\ndata: ' +
        JSON.stringify([
          { device_id: 'd1', name: 'Alex-Laptop', online: false, last_seen: 2, extension: false },
          { device_id: 'd2', name: 'Office-PC', online: true, last_seen: 3, extension: true },
        ]) +
        '\n\n',
    );
    expect(await screen.findByText('Office-PC')).toBeInTheDocument();
    expect(screen.getByTitle('Offline')).toHaveTextContent('Alex-Laptop');
  });

  it('says there are no computers yet', async () => {
    signedIn({ ...ME, devices: [] });
    renderApp('/');
    expect(await screen.findByText(/none connected yet/i)).toBeInTheDocument();
  });

  it('opens a chat from its address', async () => {
    signedIn();
    renderApp('/chat/abc123');
    expect(await screen.findByRole('heading', { name: 'Chat abc123' })).toBeInTheDocument();
  });

  it('signs out from the sidebar and goes back to the sign-in screen', async () => {
    signedIn();
    server.use(http.get('*/v1/auth/config', () => HttpResponse.json({ email_login: true })));
    const user = userEvent.setup();
    renderApp('/');
    await user.click(await screen.findByRole('button', { name: /sign out/i }));
    expect(await screen.findByLabelText(/your email/i)).toBeInTheDocument();
    expect(useSession.getState().token).toBeNull();
  });

  it('shows the problem, with a way to retry, when the server cannot be reached', async () => {
    useSession.setState({ token: 'tok' });
    server.use(http.get('*/v1/me', () => HttpResponse.json({ detail: 'the server is busy' }, { status: 503 })), http.get('*/v1/events', () => openStream().response));
    renderApp('/');
    expect(await screen.findByText(/the server is busy/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument();
  });

  it('signs out by itself when the server stops accepting the token', async () => {
    useSession.setState({ token: 'revoked' });
    server.use(
      http.get('*/v1/me', () => new HttpResponse(null, { status: 401 })),
      http.get('*/v1/auth/config', () => HttpResponse.json({ email_login: true })),
    );
    renderApp('/');
    expect(await screen.findByLabelText(/your email/i)).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(/signed out/i);
  });
});

describe('the other routes', () => {
  it('shows the privacy and terms pages without signing in', async () => {
    renderApp('/privacy');
    expect(await screen.findByRole('heading', { name: /privacy policy/i })).toBeInTheDocument();
    renderApp('/terms');
    expect(await screen.findByRole('heading', { name: /terms of use/i })).toBeInTheDocument();
  });

  it('shows a plain "not found" for an unknown address', async () => {
    renderApp('/nothing/here');
    expect(await screen.findByRole('heading', { name: /page not found/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /back to deskhand/i })).toHaveAttribute('href', '/');
  });
});
