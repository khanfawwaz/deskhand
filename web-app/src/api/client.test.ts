import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { useSession } from '../state/session';
import { server } from '../test/server';
import { api, ApiError, errorMessage } from './client';

describe('errorMessage', () => {
  it('prefers the server\'s own words', () => {
    expect(errorMessage({ detail: 'that code is wrong' }, 'Bad Request')).toBe('that code is wrong');
    expect(errorMessage({ detail: { problems: ['a', 'b'] } }, 'x')).toBe('a; b');
    expect(errorMessage({}, 'Bad Request')).toBe('Bad Request');
    expect(errorMessage(null, 'Oops')).toBe('Oops');
  });
});

describe('api client', () => {
  it('sends the device token and returns the JSON', async () => {
    useSession.setState({ token: 'tok123' });
    let auth = '';
    server.use(http.get('*/v1/me', ({ request }) => ((auth = request.headers.get('authorization') ?? ''), HttpResponse.json({ name: 'Alex' }))));
    await expect(api<{ name: string }>('/v1/me')).resolves.toEqual({ name: 'Alex' });
    expect(auth).toBe('Bearer tok123');
  });

  it('sends no Authorization header when signed out', async () => {
    let auth: string | null = 'unset';
    server.use(http.get('*/v1/auth/config', ({ request }) => ((auth = request.headers.get('authorization')), HttpResponse.json({ email_login: true }))));
    await api('/v1/auth/config');
    expect(auth).toBeNull();
  });

  it('posts JSON bodies with the right content type', async () => {
    let seen: { type: string | null; body: unknown } = { type: null, body: null };
    server.use(
      http.post('*/v1/auth/email', async ({ request }) => {
        seen = { type: request.headers.get('content-type'), body: await request.json() };
        return HttpResponse.json({ sent: true });
      }),
    );
    await api('/v1/auth/email', { method: 'POST', body: { email: 'a@b.c' } });
    expect(seen).toEqual({ type: 'application/json', body: { email: 'a@b.c' } });
  });

  it('turns an error answer into an ApiError carrying the server\'s message and status', async () => {
    server.use(http.post('*/v1/pair', () => HttpResponse.json({ detail: 'that code is wrong or has expired' }, { status: 403 })));
    const err = await api('/v1/pair', { method: 'POST', body: {} }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(403);
    expect((err as ApiError).message).toBe('that code is wrong or has expired');
  });

  it('signs the device out on 401 while signed in, with a plain message', async () => {
    useSession.setState({ token: 'revoked' });
    server.use(http.get('*/v1/me', () => new HttpResponse(null, { status: 401 })));
    await expect(api('/v1/me')).rejects.toMatchObject({ status: 401 });
    expect(useSession.getState().token).toBeNull();
    expect(useSession.getState().notice).toMatch(/signed out/i);
  });

  it('does not treat a 401 from the sign-in screen as being signed out (nobody is signed in yet)', async () => {
    server.use(http.post('*/v1/auth/verify', () => HttpResponse.json({ detail: 'wrong code' }, { status: 401 })));
    await expect(api('/v1/auth/verify', { method: 'POST', body: {} })).rejects.toMatchObject({ message: 'wrong code' });
    expect(useSession.getState().notice).toBe('');
  });

  it('says so in plain words when the server cannot be reached', async () => {
    server.use(http.get('*/v1/me', () => HttpResponse.error()));
    await expect(api('/v1/me')).rejects.toMatchObject({ status: 0, message: expect.stringMatching(/could not reach/i) });
  });

  it('lets a cancelled request go quietly', async () => {
    const controller = new AbortController();
    server.use(http.get('*/v1/me', async () => new Promise<Response>(() => undefined)));
    const pending = api('/v1/me', { signal: controller.signal });
    controller.abort();
    const err = await pending.catch((e: unknown) => e);
    expect(err).not.toBeInstanceOf(ApiError);
  });
});
