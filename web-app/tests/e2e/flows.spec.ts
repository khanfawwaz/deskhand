import { expect, test } from '@playwright/test';
import { BASE, connectComputer, resetServer, signedIn, TEST_CODE } from './helpers';

test.beforeEach(async ({ request }) => {
  await resetServer(request);
});

test('sign in with the code from an email', async ({ page }) => {
  await page.goto(BASE);
  await expect(page.getByRole('heading', { name: 'Welcome to Deskhand' })).toBeVisible();
  await page.getByLabel('Your email').fill('alex@example.com');
  await page.getByRole('button', { name: 'Email me a sign-in link' }).click();
  await page.getByLabel('Code from the email').fill(TEST_CODE);
  await page.getByRole('button', { name: 'Sign in' }).click();
  // signed in with no computer yet: the app offers to connect one straight away
  await expect(page.getByRole('dialog', { name: 'Connect your computer' })).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem('pilot.token'))).toBeTruthy();
});

test('a wrong code says so and does not sign in', async ({ page }) => {
  await page.goto(BASE);
  await page.getByLabel('Your email').fill('alex@example.com');
  await page.getByRole('button', { name: 'Email me a sign-in link' }).click();
  await page.getByLabel('Code from the email').fill('WRONG-CODE');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('alert').filter({ hasText: /not right/i })).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem('pilot.token'))).toBeNull();
});

test('connect a computer and reach the Chrome step', async ({ page, request }) => {
  await signedIn(page);
  await page.goto(BASE);
  // no computer yet: the dialog opens by itself with the install command
  const dialog = page.getByRole('dialog', { name: 'Connect your computer' });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByTestId('connect-command')).toContainText('irm ');
  await expect(dialog.getByText('Waiting for your computer')).toBeVisible();
  // the computer arrives
  await connectComputer(request, 'Alex-Laptop');
  await expect(dialog.getByText('Alex-Laptop is connected.')).toBeVisible();
  // ...and the dialog moves on to "Use your own Chrome"
  await expect(page.getByRole('dialog', { name: 'Use your own Chrome' })).toBeVisible();
  await expect(page.getByRole('dialog').getByText('Coming soon')).toBeVisible();
  await page.getByRole('button', { name: 'Done' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByTitle('Online')).toContainText('Alex-Laptop');
});

test('a task asks a question, then a follow-up stays in the same chat', async ({ page, request }) => {
  await connectComputer(request);
  await signedIn(page);
  await page.goto(BASE);
  await page.getByRole('textbox', { name: 'Command' }).fill('Which colour should the button be?');
  await page.getByRole('button', { name: 'Run' }).click();

  const ask = page.getByRole('alertdialog');
  await expect(ask).toContainText('Which one should I use?');
  await ask.getByRole('button', { name: 'The blue one' }).click();
  await expect(page.getByText(/you chose blue/)).toBeVisible();
  await expect(page).toHaveURL(/\/chat\/t1$/);

  // a follow-up continues the same chat: same address, one row in Recents, both messages on screen
  await page.getByRole('textbox', { name: 'Command' }).fill('Now say thanks');
  await page.getByRole('button', { name: 'Run' }).click();
  await expect(page.getByText('Done: Now say thanks')).toBeVisible();
  await expect(page).toHaveURL(/\/chat\/t1$/);
  await expect(page.getByText('Which colour should the button be?').first()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Delete this chat' })).toHaveCount(1);
});

test('delete a chat from Recents', async ({ page, request }) => {
  await connectComputer(request);
  await signedIn(page);
  await page.goto(BASE);
  await page.getByRole('textbox', { name: 'Command' }).fill('Open the weather page');
  await page.getByRole('button', { name: 'Run' }).click();
  await expect(page.getByText('Done: Open the weather page')).toBeVisible();

  page.once('dialog', (d) => void d.accept()); // "Delete this chat? This cannot be undone."
  await page.getByRole('button', { name: 'Delete this chat' }).click({ force: true });
  await expect(page.getByRole('button', { name: 'Delete this chat' })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'What should I do?' })).toBeVisible();
  await expect(page).not.toHaveURL(/\/chat\//);
  const { tasks } = await (await request.get('/__test/state')).json();
  expect(tasks).toBe(0);
});
