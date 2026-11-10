import { expect, test, type Page } from '@playwright/test';
import { BASE, connectComputer, resetServer, signedIn } from './helpers';

test.beforeEach(async ({ request }) => {
  await resetServer(request);
  await connectComputer(request);
});

const theme = (page: Page) => page.evaluate(() => document.documentElement.getAttribute('data-theme') ?? 'auto');

test('pulling the orb far enough flips light and dark', async ({ page }) => {
  await signedIn(page);
  await page.addInitScript(() => localStorage.setItem('pilot.settings', JSON.stringify({ theme: 'light' })));
  await page.goto(BASE);
  const orb = page.getByTestId('orb');
  await expect(orb).toBeVisible();
  const box = (await orb.boundingBox())!;
  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;
  expect(await theme(page)).toBe('light');
  await page.mouse.move(cx + 20, cy);
  await page.mouse.down();
  // drag away to the right in steps: the drop stretches, then lets go
  for (let i = 1; i <= 24; i++) await page.mouse.move(cx + 20 + i * 14, cy, { steps: 2 });
  await page.waitForTimeout(500);
  await page.mouse.up();
  await expect.poll(() => theme(page)).toBe('dark');
  // no text got selected while pulling
  expect(await page.evaluate(() => window.getSelection()?.toString() ?? '')).toBe('');
});

test('Ctrl+K opens the command palette, which switches the theme', async ({ page }) => {
  await signedIn(page);
  await page.goto(BASE);
  await expect(page.getByRole('heading', { name: 'What should I do?' })).toBeVisible();
  await page.keyboard.press('Control+k');
  const box = page.getByRole('combobox');
  await expect(box).toBeFocused();
  await box.fill('dark');
  await page.keyboard.press('Enter');
  await expect(box).toHaveCount(0);
  expect(await theme(page)).toBe('dark');
  await page.keyboard.press('Control+k');
  await page.getByRole('combobox').fill('privacy');
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/privacy$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Privacy policy' })).toBeVisible();
});

test('the legal pages are clean addresses with no file names or raw addresses', async ({ page }) => {
  for (const [path, title] of [
    ['privacy', 'Privacy policy'],
    ['terms', 'Terms of use'],
  ] as const) {
    await page.goto(`${BASE}${path}`);
    await expect(page.getByRole('heading', { level: 1, name: title })).toBeVisible();
    const html = await page.content();
    expect(html).not.toMatch(/\.html/);
    expect(html).not.toMatch(/\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b/);
  }
});

test.describe('on a phone', () => {
  test.use({ viewport: { width: 390, height: 800 } });

  test('the sidebar is a drawer: the menu button opens it, Escape and the dimmed area close it', async ({ page }) => {
    await signedIn(page);
    await page.goto(BASE);
    const menu = page.getByRole('button', { name: 'Open the menu' });
    const side = page.getByRole('complementary', { name: 'Deskhand navigation' });
    await expect(side).toBeHidden();
    await menu.click();
    await expect(side).toBeVisible();
    await expect(menu).toHaveAttribute('aria-expanded', 'true');
    await page.keyboard.press('Escape');
    await expect(side).toBeHidden();
    await menu.click();
    await expect(side).toBeVisible();
    await page.getByTestId('drawer-scrim').click({ position: { x: 370, y: 400 } });
    await expect(side).toBeHidden();
  });
});

test.describe('the app shell (service worker)', () => {
  test('is installed in a production build, covers the app and keeps the Google file and icons served', async ({ page, request }) => {
    await page.goto(BASE);
    const scope = await page.evaluate(async () => (await navigator.serviceWorker.ready).scope);
    expect(scope).toMatch(/\/app\/$/);
    const keys = await page.evaluate(async () => (await caches.keys()).filter((k) => k.startsWith('deskhand-app-')));
    expect(keys).toHaveLength(1);
    const cached = await page.evaluate(async () => {
      const name = (await caches.keys())[0]!;
      return (await (await caches.open(name)).keys()).map((r) => new URL(r.url).pathname);
    });
    expect(cached).toContain('/app/index.html');
    expect(cached.some((p) => /\/assets\/index-.*\.js$/.test(p))).toBe(true);
    expect((await request.get(`${BASE}googleaa05301828e8707d.html`)).status()).toBe(200);
    expect((await request.get(`${BASE}icons/icon-192.png`)).status()).toBe(200);
    const manifest = (await (await request.get(`${BASE}manifest.webmanifest`)).json()) as { scope: string };
    expect(manifest.scope).toBe('./');
  });

  test('opens from the saved shell when the network is gone, and goes to the network first when it is back', async ({ page, context, request }) => {
    await page.goto(BASE);
    await page.evaluate(async () => void (await navigator.serviceWorker.ready));
    await page.reload(); // now controlled by the worker
    await context.setOffline(true);
    await page.goto(BASE);
    // the app itself opens (its sign-in screen: the server cannot be asked what to offer while offline)
    await expect(page.getByRole('heading', { level: 1, name: 'Deskhand' })).toBeVisible();
    await context.setOffline(false);
    // network first: with the network back, the page address is asked of the server again (never answered from the saved copy)
    const count = async () => ((await (await request.get('/__test/hits')).json()) as Record<string, number>)['/app/privacy'] ?? 0;
    const before = await count();
    await page.goto(`${BASE}privacy`);
    await expect(page.getByRole('heading', { level: 1, name: 'Privacy policy' })).toBeVisible();
    expect(await count()).toBeGreaterThan(before);
  });
});
