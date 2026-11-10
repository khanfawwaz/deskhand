import type { Chat } from '../../lib/chats';
import type { ThemeMode } from '../../state/theme';
import type { PaletteAction } from '../delight/orbit';

/** What the palette needs from the rest of the app. Plain functions, so the list can be built (and tested) anywhere. */
export interface PaletteDeps {
  chats: Pick<Chat, 'thread' | 'title'>[];
  newTask: () => void;
  openChat: (thread: string) => void;
  openSettings: () => void;
  openConnect: () => void;
  openPage: (path: '/privacy' | '/terms') => void;
  setTheme: (mode: ThemeMode) => void;
}

const MAX_CHATS = 8;

/**
 * Every action the Ctrl+K palette offers. There is deliberately no "delete" action here: nothing in the palette
 * destroys data, that stays behind its own confirmation in Settings and in the chat list.
 */
export function buildPaletteActions(d: PaletteDeps): PaletteAction[] {
  const actions: PaletteAction[] = [
    { id: 'new-task', label: 'New task', hint: 'Start', keywords: 'chat start home', run: d.newTask },
    { id: 'settings', label: 'Open settings', hint: 'Settings', keywords: 'preferences account usage data', run: d.openSettings },
    { id: 'connect', label: 'Connect a computer', hint: 'Settings', keywords: 'add device install pair', run: d.openConnect },
    { id: 'theme-light', label: 'Switch to light theme', hint: 'Theme', keywords: 'appearance day colours', run: () => d.setTheme('light') },
    { id: 'theme-dark', label: 'Switch to dark theme', hint: 'Theme', keywords: 'appearance night colours', run: () => d.setTheme('dark') },
    { id: 'theme-auto', label: 'Switch to system theme', hint: 'Theme', keywords: 'appearance auto follow device colours', run: () => d.setTheme('auto') },
  ];
  for (const c of d.chats.slice(0, MAX_CHATS)) {
    actions.push({ id: `chat-${c.thread}`, label: c.title || 'Untitled chat', hint: 'Chat', keywords: 'open recent history', run: () => d.openChat(c.thread) });
  }
  actions.push(
    { id: 'privacy', label: 'Open privacy policy', hint: 'Page', keywords: 'legal data', run: () => d.openPage('/privacy') },
    { id: 'terms', label: 'Open terms of use', hint: 'Page', keywords: 'legal conditions', run: () => d.openPage('/terms') },
  );
  return actions;
}
