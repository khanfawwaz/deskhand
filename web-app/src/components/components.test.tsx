import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Banner } from './Banner';
import { Dialog } from './Dialog';
import { StatusDot } from './StatusDot';
import { Toaster } from './Toaster';
import { toast, useToasts } from './toast';

afterEach(() => {
  useToasts.setState({ items: [] });
  vi.useRealTimers();
});

describe('Toast', () => {
  it('shows a message, goes away by itself and can be dismissed with a click', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<Toaster />);
    act(() => toast('Chat deleted.', 1000));
    expect(screen.getByText('Chat deleted.')).toBeInTheDocument();
    act(() => void vi.advanceTimersByTime(1100));
    expect(screen.queryByText('Chat deleted.')).toBeNull();
    act(() => toast('Saved.'));
    await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).click(screen.getByText('Saved.'));
    expect(screen.queryByText('Saved.')).toBeNull();
  });

  it('keeps at most three at once', () => {
    render(<Toaster />);
    act(() => ['a', 'b', 'c', 'd'].forEach((m) => toast(m)));
    expect(screen.queryByText('a')).toBeNull();
    expect(screen.getByText('d')).toBeInTheDocument();
  });
});

describe('Banner', () => {
  it('has a labelled Dismiss button and an action', async () => {
    const onDismiss = vi.fn();
    render(
      <Banner title="Tip" onDismiss={onDismiss} action={<span>Soon</span>}>
        Something useful.
      </Banner>,
    );
    expect(screen.getByRole('complementary', { name: 'Tip' })).toHaveTextContent('Something useful.');
    await userEvent.setup().click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(onDismiss).toHaveBeenCalledOnce();
  });
});

describe('StatusDot', () => {
  it('is silent without a label and says its state with one', () => {
    const { rerender } = render(<StatusDot on />);
    expect(screen.queryByRole('img')).toBeNull();
    rerender(<StatusDot on={false} label="Offline" />);
    expect(screen.getByRole('img', { name: 'Offline' })).toHaveAttribute('data-on', '0');
  });
});

describe('Dialog', () => {
  function Demo() {
    const [open, setOpen] = useState(false);
    return (
      <>
        <button onClick={() => setOpen(true)}>open</button>
        <Dialog open={open} onClose={() => setOpen(false)} labelledBy="t">
          <h2 id="t">Hello</h2>
        </Dialog>
      </>
    );
  }
  it('has its contents only while open', async () => {
    render(<Demo />);
    expect(screen.queryByText('Hello')).toBeNull();
    await userEvent.setup().click(screen.getByText('open'));
    expect(screen.getByText('Hello')).toBeInTheDocument();
  });
});
