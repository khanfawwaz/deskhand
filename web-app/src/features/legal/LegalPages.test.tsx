import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { renderApp } from '../../test/render';

const root = path.resolve(__dirname, '..', '..', '..'); // web-app/
const serverPage = (name: string) => path.resolve(root, '..', 'src', 'taskpilot', 'web', `${name}.html`);
const copy = (name: string) => fs.readFileSync(path.join(root, 'src', 'features', 'legal', 'content', `${name}.html`), 'utf8');
const mainOf = (html: string) => (/<main[^>]*>([\s\S]*?)<\/main>/i.exec(html)?.[1] ?? '').replace(/\r\n/g, '\n').trim() + '\n';

describe('the privacy policy and the terms', () => {
  it('shows the privacy policy with its sections, and no file names or raw addresses', async () => {
    const { container } = renderApp('/privacy');
    expect(await screen.findByRole('heading', { level: 1, name: 'Privacy policy' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /what we collect/i })).toBeInTheDocument();
    expect(screen.getByText(/last updated/i, { selector: '.date' })).toBeInTheDocument();
    expect(document.title).toBe('Privacy policy - Deskhand');
    expect(container.innerHTML).not.toMatch(/\.html/);
    expect(container.innerHTML).not.toMatch(/\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b/);
  });

  it('shows the terms, and moves between the two pages without leaving the app', async () => {
    const user = userEvent.setup();
    const { router, container } = renderApp('/terms');
    expect(await screen.findByRole('heading', { level: 1, name: 'Terms of use' })).toBeInTheDocument();
    expect(container.innerHTML).not.toMatch(/\.html/);
    const nav = screen.getByRole('navigation', { name: /legal pages/i });
    expect(within(nav).getByRole('link', { name: 'Terms' })).toHaveAttribute('aria-current', 'page');
    // a link inside the text itself
    await user.click(screen.getAllByRole('link', { name: /privacy policy/i })[0]!);
    expect(router.state.location.pathname).toBe('/privacy');
    expect(await screen.findByRole('heading', { level: 1, name: 'Privacy policy' })).toBeInTheDocument();
    await user.click(within(screen.getByRole('navigation', { name: /legal pages/i })).getByRole('link', { name: 'Open Deskhand' }));
    expect(router.state.location.pathname).toBe('/');
  });

  // The words live once, in the server's pages. When they are edited, `node scripts/sync-legal.mjs` must be run.
  it.each(['privacy', 'terms'])('keeps the %s text in step with the server page', (name) => {
    if (!fs.existsSync(serverPage(name))) return; // the app built on its own (for example in the Docker build stage)
    expect(copy(name)).toBe(mainOf(fs.readFileSync(serverPage(name), 'utf8')));
  });
});
