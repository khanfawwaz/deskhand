import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { BASE, connectComputer, resetServer, signedIn } from './helpers';

test.beforeEach(async ({ request }) => {
  await resetServer(request);
});

/** Serious and critical problems fail the test; the message says exactly what and where. */
async function expectNoSeriousProblems(page: Page, label: string) {
  const { violations } = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
  const bad = violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
  expect(bad.map((v) => `${label}: ${v.id} (${v.nodes.map((n) => n.target.join(' ')).join(' | ')})`)).toEqual([]);
}

for (const scheme of ['light', 'dark'] as const) {
  test.describe(`accessibility, ${scheme}`, () => {
    test.use({ colorScheme: scheme });

    test('sign-in, home, palette, privacy', async ({ page, request }) => {
      await page.goto(BASE);
      await expect(page.getByRole('heading', { name: 'Welcome to Deskhand' })).toBeVisible();
      await expectNoSeriousProblems(page, 'sign-in');

      await connectComputer(request);
      await signedIn(page);
      await page.goto(BASE);
      await expect(page.getByRole('heading', { name: 'What should I do?' })).toBeVisible();
      await expectNoSeriousProblems(page, 'home');

      await page.keyboard.press('Control+k');
      await expect(page.getByRole('combobox')).toBeFocused();
      await page.waitForTimeout(400); // the panel fades in: measure the finished colours
      await expectNoSeriousProblems(page, 'palette');
      await page.keyboard.press('Escape');

      await page.goto(`${BASE}privacy`);
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
      await expectNoSeriousProblems(page, 'privacy');
    });
  });
}

test('keyboard only: Tab reaches the composer and the next control shows a focus ring', async ({ page, request }) => {
  await connectComputer(request);
  await signedIn(page);
  await page.goto(BASE);
  await expect(page.getByRole('heading', { name: 'What should I do?' })).toBeVisible();
  let reachedComposer = false;
  for (let i = 0; i < 40 && !reachedComposer; i++) {
    await page.keyboard.press('Tab');
    reachedComposer = await page.evaluate(() => document.activeElement?.getAttribute('aria-label') === 'Command');
  }
  expect(reachedComposer).toBe(true);
  await page.keyboard.press('Tab');
  const outline = await page.evaluate(() => getComputedStyle(document.activeElement!).outlineStyle);
  expect(outline).not.toBe('none');
});
