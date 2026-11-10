import { api } from '../../api/client';
import type { components } from '../../api/schema';
import type { EmailSent, TokenResponse } from '../../api/types';
import { deviceName, normalizeCode } from '../../lib/codes';
import { useSession } from '../../state/session';

/** The sign-in calls, exactly as the original app made them. Each returns once the device is signed in (or throws ApiError). */

function finishLogin(token: string): void {
  useSession.getState().setToken(token);
  // the sign-in link carries its code in the #fragment: take it out of the address bar once used
  window.history.replaceState(null, '', window.location.pathname + window.location.search);
}

// request bodies come from the generated schema (src/api/schema.d.ts): if the server changes one, this stops compiling
type Schemas = components['schemas'];

/** Ask the server to email a sign-in link and code. */
export function sendSignInEmail(email: string): Promise<EmailSent> {
  const body: Schemas['EmailIn'] = { email: email.trim() };
  return api<EmailSent>('/v1/auth/email', { method: 'POST', body });
}

/** Redeem the code from the email (typed in, or taken from the link). */
export async function verifyEmailCode(code: string): Promise<void> {
  const body: Schemas['VerifyIn'] = { code: normalizeCode(code), name: deviceName() };
  const r = await api<TokenResponse>('/v1/auth/verify', { method: 'POST', body });
  finishLogin(r.token);
}

/** Pair this device with a code shown on a device that is already set up (or an invite code). */
export async function pairWithCode(code: string, name: string): Promise<void> {
  const body: Schemas['PairIn'] = { code: normalizeCode(code), name: name.trim() };
  const r = await api<TokenResponse>('/v1/pair', { method: 'POST', body });
  finishLogin(r.token);
}

/** The code in a sign-in link, if the address has one (`#code=ABCD-EFGH`). */
export function codeFromHash(hash: string): string | null {
  const m = /[#&]code=([A-Za-z0-9-]+)/.exec(hash);
  return m?.[1] ?? null;
}
