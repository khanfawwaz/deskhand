import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { TaskView } from '../../api/types';
import { useSession } from '../../state/session';
import { useUi } from '../../state/ui';
import { openStream, renderApp } from '../../test/render';
import { server } from '../../test/server';
import { useChat } from './chatStore';

// the live feed pushes updates from the network, not from a click
beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = false;
  HTMLCanvasElement.prototype.getContext = (() => null) as typeof HTMLCanvasElement.prototype.getContext; // jsdom has no canvas: the orb draws nothing
});

const LAPTOP = {
  device_id: 'd1',
  name: 'Alex-Laptop',
  online: true,
  last_seen: 1,
  extension: false,
};
const OFFICE = {
  device_id: 'd2',
  name: 'Office-PC',
  online: true,
  last_seen: 1,
  extension: false,
};
const SLEEPY = {
  device_id: 'd3',
  name: 'Old-Tablet',
  online: false,
  last_seen: 1,
  extension: false,
};

let seq = 1;
function task(over: Partial<TaskView> = {}): TaskView {
  const id = over.id ?? 't' + seq++;
  return {
    id,
    goal: 'Open Notepad',
    device_id: 'd1',
    status: 'completed',
    created_at: 1000,
    updated_at: 1010,
    result: null,
    error: null,
    terminal: true,
    pending_approval: null,
    thread: id,
    ...over,
  };
}
const running = (over: Partial<TaskView> = {}) => task({ status: 'running', terminal: false, ...over });

interface Setup {
  tasks?: TaskView[];
  devices?: (typeof LAPTOP)[];
}

/** A signed-in person with a fake server: the list, single tasks, the live feed, and a record of what the app posts. */
function setup({ tasks = [], devices = [LAPTOP] }: Setup = {}) {
  useSession.setState({ token: 'tok' });
  const store = new Map(tasks.map((t) => [t.id, t]));
  const posted: { path: string; body: unknown }[] = [];
  const streams: ReturnType<typeof openStream>[] = [];
  const me = {
    client_id: 'c1',
    name: 'Alex',
    workspace: { id: 'w', name: 'Alex Home' },
    devices,
    extension_url: '',
  };
  server.use(
    http.get('*/v1/me', () => HttpResponse.json(me)),
    http.post('*/v1/add-device', () =>
      HttpResponse.json({
        code: 'ABCD',
        expires_in: 900,
        command: 'install-command',
      }),
    ), // the connect dialog offers itself when there is no computer
    http.get('*/v1/events', () => {
      const s = openStream();
      streams.push(s);
      return s.response;
    }),
    http.get('*/v1/tasks', () => HttpResponse.json([...store.values()].map((t) => ({ ...t, steps: undefined })))),
    http.get('*/v1/tasks/:id', ({ params }) => {
      const t = store.get(String(params.id));
      return t ? HttpResponse.json(t) : HttpResponse.json({ detail: 'unknown task' }, { status: 404 });
    }),
    http.post('*/v1/tasks', async ({ request }) => {
      const body = (await request.json()) as {
        goal: string;
        device_id: string;
        continue_from?: string;
      };
      posted.push({ path: '/v1/tasks', body });
      const cf = body.continue_from ? store.get(body.continue_from) : undefined;
      const t = running({
        id: 'new' + posted.length,
        goal: body.goal,
        device_id: body.device_id,
        created_at: 2000 + posted.length,
        thread: cf?.thread ?? undefined,
      });
      if (!cf) t.thread = t.id;
      store.set(t.id, t);
      return HttpResponse.json({ task_id: t.id, device_id: body.device_id });
    }),
    http.post('*/v1/tasks/:id/:action', async ({ request, params }) => {
      posted.push({
        path: `/v1/tasks/${String(params.id)}/${String(params.action)}`,
        body: await request.json().catch(() => null),
      });
      return HttpResponse.json({ answered: true });
    }),
  );
  return {
    store,
    posted,
    /** push a task to the page the way the server does */
    push: (t: TaskView) => streams.forEach((s) => s.send('event: task\ndata: ' + JSON.stringify(t) + '\n\n')),
  };
}

beforeEach(() => {
  seq = 1;
  useChat.setState({ qa: {}, targets: [], bootDone: false, composerFocus: 0 });
  useUi.setState({ connectOpen: false, settingsOpen: false });
});
afterEach(() => {
  vi.restoreAllMocks();
  useUi.setState({ connectOpen: false, settingsOpen: false });
});

const box = () => screen.getByRole('textbox', { name: 'Command' });

describe('the start screen', () => {
  it('shows the orb with each computer as a pod, examples, and fills the box from an example', async () => {
    setup({ devices: [LAPTOP, OFFICE] });
    renderApp('/');
    expect(await screen.findByRole('heading', { name: /what should i do/i })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Your computers' })).toHaveAttribute('data-mood', 'idle');
    expect(screen.getByRole('button', { name: 'Alex-Laptop, online, chosen' })).toBeInTheDocument(); // the first online one is chosen
    await userEvent.click(screen.getByRole('button', { name: 'Open Calculator and add 7 and 5' }));
    expect(box()).toHaveValue('Open Calculator and add 7 and 5');
  });

  it('the empty box types example requests, and goes quiet when focused', async () => {
    setup();
    renderApp('/');
    await screen.findByRole('heading', { name: /what should i do/i });
    await waitFor(() => expect(box().getAttribute('placeholder')).not.toBe('Ask Deskhand to do something…'), { timeout: 3000 });
    await userEvent.click(box());
    await waitFor(() => expect(box()).toHaveAttribute('placeholder', 'Ask Deskhand to do something…'));
  });

  it('with no computer offers to connect one (and hides the message box)', async () => {
    setup({ devices: [] });
    renderApp('/');
    expect(await screen.findByText('No computer connected yet')).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: 'Command' })).not.toBeInTheDocument();
  });

  it('opens straight into a task that is running', async () => {
    setup({ tasks: [running({ id: 'r1', goal: 'Find the weather' })] });
    renderApp('/');
    expect(await screen.findByText('Working for', { exact: false })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Stop the task' })).toBeInTheDocument();
  });
});

describe('sending a task', () => {
  it('Enter sends to the chosen computer, then shows the chat with Stop in place of Send; Shift+Enter is a new line', async () => {
    const s = setup();
    renderApp('/');
    await screen.findByRole('heading', { name: /what should i do/i });
    await userEvent.type(box(), 'Open Notepad{Shift>}{Enter}{/Shift}and type hi');
    expect(box()).toHaveValue('Open Notepad\nand type hi');
    await userEvent.clear(box());
    await userEvent.type(box(), 'Open Notepad{Enter}');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Stop the task' })).toBeInTheDocument());
    expect(s.posted).toEqual([{ path: '/v1/tasks', body: { goal: 'Open Notepad', device_id: 'd1' } }]); // a fresh chat: no continue_from
    expect(screen.queryByRole('button', { name: 'Run' })).not.toBeInTheDocument();
    expect(screen.getAllByText('Open Notepad').length).toBeGreaterThan(0);
    expect(box()).toHaveValue('');
  });

  it('Stop replaces Send while a task runs, stops it, and Enter says it is still working', async () => {
    const s = setup({ tasks: [running({ id: 'r1', goal: 'Slow job' })] });
    renderApp('/chat/r1');
    const stop = await screen.findByRole('button', { name: 'Stop the task' });
    expect(screen.queryByRole('button', { name: 'Run' })).not.toBeInTheDocument();
    await userEvent.type(box(), 'another thing{Enter}');
    expect(await screen.findByText(/still working/i)).toBeInTheDocument();
    expect(s.posted).toEqual([]);
    await userEvent.click(stop);
    await waitFor(() => expect(s.posted).toEqual([{ path: '/v1/tasks/r1/cancel', body: null }]));
  });

  it('continues a finished chat: the follow-up carries continue_from', async () => {
    const s = setup({
      tasks: [task({ id: 'done1', goal: 'Open Notepad', result: 'Opened it.' })],
    });
    renderApp('/chat/done1');
    expect(await screen.findByText('Opened it.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Run' })).toBeInTheDocument();
    await userEvent.type(box(), 'Now write a poem{Enter}');
    await waitFor(() => expect(s.posted).toHaveLength(1));
    expect(s.posted[0]).toEqual({
      path: '/v1/tasks',
      body: {
        goal: 'Now write a poem',
        device_id: 'd1',
        continue_from: 'done1',
      },
    });
    // both turns are in the one chat: the first above, the new one live
    expect(await screen.findByTestId('earlier')).toHaveTextContent('Opened it.');
    expect(screen.getAllByText('Now write a poem').length).toBeGreaterThan(0);
  });

  it('New task starts a fresh chat (no continue_from)', async () => {
    const s = setup({ tasks: [task({ id: 'done1', goal: 'Open Notepad' })] });
    renderApp('/chat/done1');
    await screen.findByRole('button', { name: 'Run' });
    await userEvent.click(screen.getByRole('button', { name: 'New task' }));
    await screen.findByRole('heading', { name: /what should i do/i });
    await userEvent.type(box(), 'Something new{Enter}');
    await waitFor(() => expect(s.posted).toHaveLength(1));
    expect(s.posted[0]?.body).toEqual({
      goal: 'Something new',
      device_id: 'd1',
    });
  });
});

describe('questions', () => {
  const asking = (pa: NonNullable<TaskView['pending_approval']>) =>
    running({
      id: 'q1',
      goal: 'Play music',
      status: 'waiting_for_confirmation',
      pending_approval: pa,
    });

  it('answers a question with a button, and the answer stays in the conversation', async () => {
    const s = setup({
      tasks: [
        asking({
          kind: 'question',
          question: 'Which browser?',
          options: [
            { id: 'chrome', label: 'Chrome' },
            { id: 'edge', label: 'Edge' },
          ],
        }),
      ],
    });
    renderApp('/chat/q1');
    expect(await screen.findByText('Deskhand has a question')).toBeInTheDocument();
    expect(screen.getByText('Which browser?')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Edge' }));
    await waitFor(() => expect(s.posted).toEqual([{ path: '/v1/tasks/q1/answer', body: { choice: 'edge' } }]));
    expect(await screen.findByText('Edge', { selector: 'p' })).toBeInTheDocument(); // the person's reply bubble
  });

  it('answers a question by typing', async () => {
    const s = setup({
      tasks: [
        asking({
          kind: 'question',
          question: 'Which folder?',
          options: [{ id: 'docs', label: 'Documents' }],
          allow_text: true,
        }),
      ],
    });
    renderApp('/chat/q1');
    await userEvent.type(await screen.findByRole('textbox', { name: 'Your own answer' }), 'Desktop');
    await userEvent.click(screen.getByRole('button', { name: 'Send answer' }));
    await waitFor(() => expect(s.posted).toEqual([{ path: '/v1/tasks/q1/answer', body: { text: 'Desktop' } }]));
    expect(await screen.findByText('Desktop', { selector: 'p' })).toBeInTheDocument();
  });

  it('shows an access card with its three choices, and a confirm card with Approve and Deny', async () => {
    const s = setup({
      tasks: [
        asking({
          kind: 'access',
          question: 'May Deskhand use Spotify?',
          options: [
            { id: 'once', label: 'Allow this time' },
            { id: 'always', label: 'Always allow on this computer' },
            { id: 'no', label: 'Not now' },
          ],
        }),
      ],
    });
    const { unmount } = renderApp('/chat/q1');
    await screen.findByText('May Deskhand use Spotify?');
    for (const label of ['Allow this time', 'Always allow on this computer', 'Not now'])
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument();
    expect(screen.getByText(/you stay in control/i)).toBeInTheDocument();
    unmount();

    s.store.set(
      'q1',
      asking({
        tool: 'ui.click',
        params: { name: 'Send' },
        reason: 'sends a message',
      }),
    );
    renderApp('/chat/q1');
    expect(await screen.findByText('Deskhand needs your OK')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }));
    await waitFor(() =>
      expect(s.posted.at(-1)).toEqual({
        path: '/v1/tasks/q1/approve',
        body: { approved: true },
      }),
    );
  });
});

describe('the live feed', () => {
  it('a task event updates the open chat and the Recents dot', async () => {
    const s = setup({
      tasks: [running({ id: 'r1', goal: 'Find the weather' })],
    });
    renderApp('/chat/r1');
    await screen.findByRole('button', { name: 'Stop the task' });
    await waitFor(() => expect(screen.getAllByRole('status').some((e) => e.textContent === 'online')).toBe(true));
    s.push(
      task({
        id: 'r1',
        goal: 'Find the weather',
        result: 'Sunny, 24 degrees.',
        steps: [{ tool: 'web.search', status: 'completed' }],
        updated_at: 1030,
      }),
    );
    expect(await screen.findByText('Sunny, 24 degrees.')).toBeInTheDocument();
    expect(screen.getByText('Done')).toBeInTheDocument();
    expect(screen.getByText('1 step')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Run' })).toBeInTheDocument(); // Stop is Send again
    const side = screen.getByRole('complementary', { name: /deskhand/i });
    expect(within(side).getByTitle('Done')).toBeInTheDocument();
  });

  it('a question event pops into view', async () => {
    const s = setup({
      tasks: [running({ id: 'r1', goal: 'Find the weather' })],
    });
    renderApp('/chat/r1');
    await screen.findByRole('button', { name: 'Stop the task' });
    await waitFor(() => expect(screen.getAllByRole('status').some((e) => e.textContent === 'online')).toBe(true));
    s.push(
      running({
        id: 'r1',
        goal: 'Find the weather',
        status: 'waiting_for_confirmation',
        pending_approval: {
          kind: 'question',
          question: 'Which city?',
          allow_text: true,
        },
      }),
    );
    expect(await screen.findByText('Which city?')).toBeInTheDocument();
  });
});

describe('Recents and deleting', () => {
  const grouped = () => [
    task({ id: 'a1', goal: 'Plan a trip', created_at: 100, result: 'Done A1' }),
    task({
      id: 'a2',
      goal: 'And book hotels',
      created_at: 200,
      thread: 'a1',
      result: 'Done A2',
    }),
    task({
      id: 'b1',
      goal: 'Check the news',
      created_at: 300,
      status: 'failed',
      error: 'cannot_proceed: no internet',
    }),
  ];

  it('groups follow-ups into one row per chat, newest first, titled by the first message', async () => {
    setup({ tasks: grouped() });
    renderApp('/');
    const side = await screen.findByRole('complementary', {
      name: /deskhand/i,
    });
    await within(side).findByText('Check the news');
    const rows = within(side)
      .getAllByRole('listitem')
      .filter((li) => li.textContent === 'Plan a trip' || li.textContent === 'Check the news');
    expect(rows.map((r) => r.textContent)).toEqual(['Check the news', 'Plan a trip']);
    expect(within(side).queryByText('And book hotels')).not.toBeInTheDocument();
    await userEvent.click(within(side).getByText('Plan a trip'));
    expect(await screen.findByText('Done A2')).toBeInTheDocument();
    expect(screen.getByTestId('earlier')).toHaveTextContent('Done A1');
  });

  it('shows a friendly error for a failed task', async () => {
    setup({ tasks: grouped() });
    renderApp('/chat/b1');
    expect(await screen.findByText('no internet')).toBeInTheDocument();
    expect(screen.getByText('Could not finish')).toBeInTheDocument();
  });

  it('deletes a chat after asking, and says so', async () => {
    const s = setup({ tasks: grouped() });
    const sent: string[] = [];
    server.use(
      http.delete('*/v1/chats/:id', ({ params }) => {
        sent.push(String(params.id));
        s.store.delete('b1');
        return HttpResponse.json({ deleted: 1, ids: ['b1'] });
      }),
    );
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderApp('/chat/b1');
    const side = await screen.findByRole('complementary', {
      name: /deskhand/i,
    });
    await within(side).findByText('Check the news');
    const row = within(side).getByText('Check the news').closest('li') as HTMLElement;
    await userEvent.click(within(row).getByRole('button', { name: 'Delete this chat' }));
    expect(confirm).toHaveBeenCalledOnce();
    await waitFor(() => expect(sent).toEqual(['b1']));
    expect(await screen.findByText('Chat deleted.')).toBeInTheDocument();
    expect(within(side).queryByText('Check the news')).not.toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: /what should i do/i })).toBeInTheDocument(); // the open chat is gone
  });

  it('does not delete when the person says no', async () => {
    setup({ tasks: grouped() });
    const sent = vi.fn();
    server.use(http.delete('*/v1/chats/:id', () => (sent(), HttpResponse.json({ deleted: 1, ids: [] }))));
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    renderApp('/');
    const side = await screen.findByRole('complementary', {
      name: /deskhand/i,
    });
    const row = (await within(side).findByText('Check the news')).closest('li') as HTMLElement;
    await userEvent.click(within(row).getByRole('button', { name: 'Delete this chat' }));
    expect(sent).not.toHaveBeenCalled();
    expect(within(side).getByText('Check the news')).toBeInTheDocument();
  });

  it('shows the server message when a chat is still running (409) and keeps the chat', async () => {
    setup({ tasks: [running({ id: 'r1', goal: 'Slow job' })] });
    server.use(
      http.delete('*/v1/chats/:id', () =>
        HttpResponse.json(
          {
            detail: 'This chat is still running. Press Stop first, then delete it.',
          },
          { status: 409 },
        ),
      ),
    );
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    renderApp('/');
    const side = await screen.findByRole('complementary', {
      name: /deskhand/i,
    });
    const row = (await within(side).findByText('Slow job')).closest('li') as HTMLElement;
    await userEvent.click(within(row).getByRole('button', { name: 'Delete this chat' }));
    expect(await screen.findByText(/still running\. press stop first/i)).toBeInTheDocument();
    expect(within(side).getByText('Slow job')).toBeInTheDocument();
  });
});

describe('choosing computers', () => {
  it('pressing a pod on the orb chooses it as a target, so one task goes to every chosen computer', async () => {
    const s = setup({ devices: [LAPTOP, OFFICE] });
    renderApp('/');
    await userEvent.click(await screen.findByRole('button', { name: 'Office-PC, online' }));
    expect(screen.getByRole('button', { name: 'Office-PC, online, chosen' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Choose which computer' })).toHaveTextContent('2 computers'); // the composer shares the choice
    await userEvent.type(box(), 'Say hello{Enter}');
    await waitFor(() => expect(s.posted).toHaveLength(2));
    expect(s.posted.map((p) => (p.body as { device_id: string }).device_id).sort()).toEqual(['d1', 'd2']);
    expect(await screen.findByText(/sent to 2 computers/i)).toBeInTheDocument();
  });

  it('the picker lists computers, notes the offline ones, and will not send to them', async () => {
    const s = setup({ devices: [SLEEPY] });
    renderApp('/');
    await userEvent.click(await screen.findByRole('button', { name: 'Choose which computer' }));
    expect(screen.getByRole('button', { name: /Old-Tablet \(offline\)/ })).toHaveAttribute('aria-pressed', 'true'); // the only computer is chosen
    expect(screen.getByText(/Old-Tablet is offline\. On that computer open Deskhand/)).toBeInTheDocument();
    await userEvent.keyboard('{Escape}');
    await userEvent.type(box(), 'Hello{Enter}');
    expect(await screen.findByText(/Old-Tablet is offline: open Deskhand on it first/)).toBeInTheDocument();
    expect(s.posted).toEqual([]);
  });

  it('is told to choose a computer when none is chosen', async () => {
    setup({ devices: [LAPTOP] });
    renderApp('/');
    await userEvent.click(
      await screen.findByRole('button', {
        name: 'Alex-Laptop, online, chosen',
      }),
    ); // un-choose it
    await userEvent.type(box(), 'Hello{Enter}');
    expect(await screen.findByText('Choose a computer to run this on.')).toBeInTheDocument();
  });
});

describe('voice', () => {
  afterEach(() => {
    delete (window as { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition;
  });

  it('without speech recognition there is no mic button and the line says typing works everywhere', async () => {
    setup();
    renderApp('/');
    await screen.findByRole('heading', { name: /what should i do/i });
    expect(screen.queryByRole('button', { name: 'Speak a command' })).not.toBeInTheDocument();
    expect(screen.getByText(/voice input needs chrome/i)).toBeInTheDocument();
  });

  it('a spoken task fills the box and starts the countdown, which Cancel stops', async () => {
    class FakeRecognition {
      static last: FakeRecognition | undefined;
      onstart: (() => void) | null = null;
      onresult: ((e: unknown) => void) | null = null;
      onend: (() => void) | null = null;
      onerror = null;
      constructor() {
        FakeRecognition.last = this;
      }
      start() {
        this.onstart?.();
      }
      stop() {
        this.onend?.();
      }
      abort() {}
    }
    (window as unknown as { webkitSpeechRecognition: unknown }).webkitSpeechRecognition = FakeRecognition;
    const s = setup();
    renderApp('/');
    const mic = await screen.findByRole('button', { name: 'Speak a command' });
    await userEvent.click(mic);
    expect(await screen.findByText('Listening…')).toBeInTheDocument();
    expect(mic).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('group', { name: 'Your computers' })).toHaveAttribute('data-mood', 'listening');
    FakeRecognition.last?.onresult?.({
      resultIndex: 0,
      results: [{ isFinal: true, 0: { transcript: 'open the calculator' } }],
    });
    FakeRecognition.last?.onend?.();
    expect(await screen.findByText(/running in 2/i)).toBeInTheDocument();
    expect(box()).toHaveValue('Open the calculator');
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(await screen.findByText(/cancelled/i)).toBeInTheDocument();
    expect(s.posted).toEqual([]);
  });
});
