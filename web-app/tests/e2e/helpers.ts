import type { APIRequestContext, Page } from '@playwright/test';

export const BASE = '/app-next/';
export const TEST_CODE = 'ABCD-EFGH';
export const TOKEN = 'e2e-token';

/** A clean fake server: no computers, no tasks. */
export async function resetServer(request: APIRequestContext): Promise<void> {
  await request.post('/__test/reset');
}

/** A computer arrives (it shows up in the list and in the live feed). */
export async function connectComputer(request: APIRequestContext, name = 'Alex-Laptop', extension = false): Promise<void> {
  await request.post('/__test/connect', { data: { name, extension } });
}

/** Skip the sign-in screen: the same token the sign-in flow would have stored. */
export async function signedIn(page: Page): Promise<void> {
  await page.addInitScript((token) => localStorage.setItem('pilot.token', token), TOKEN);
}
