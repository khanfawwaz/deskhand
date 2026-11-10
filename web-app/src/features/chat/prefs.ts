import { KEYS, storage } from '../../lib/storage';

/** The person's switches, read fresh each time (the Settings dialog writes them into 'pilot.settings'). */
export interface ChatPrefs {
  /** voice: run the task by itself after a short countdown */
  auto: boolean;
  /** read finished results aloud */
  speak: boolean;
  /** browser notifications while the tab is hidden */
  notify: boolean;
}

export function readPrefs(): ChatPrefs {
  let raw: Record<string, unknown> = {};
  try {
    const v: unknown = JSON.parse(storage.get(KEYS.settings) || '{}');
    if (v && typeof v === 'object' && !Array.isArray(v)) raw = v as Record<string, unknown>;
  } catch {
    /* unreadable settings: use the defaults */
  }
  return {
    auto: raw.auto !== false,
    speak: raw.speak === true,
    notify: raw.notify === true,
  };
}

export function speak(text: string): void {
  try {
    speechSynthesis.cancel();
    speechSynthesis.speak(new SpeechSynthesisUtterance(String(text).slice(0, 700)));
  } catch {
    /* unsupported */
  }
}

export function notify(title: string, body: string): void {
  if (!readPrefs().notify || typeof Notification === 'undefined' || Notification.permission !== 'granted' || !document.hidden) return;
  try {
    new Notification(title, { body, icon: 'icons/icon-192.png' });
  } catch {
    /* ignore */
  }
}
