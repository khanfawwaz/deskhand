/**
 * "Add to home screen": the browser offers the install prompt once, early, so it is caught for the whole visit here
 * (this file is imported when the app starts) and used later from the Preferences box.
 */
export interface InstallEvent extends Event {
  prompt: () => Promise<void>;
}

let saved: InstallEvent | null = null;
const listeners = new Set<() => void>();

if (typeof window !== 'undefined') {
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    saved = e as InstallEvent;
    listeners.forEach((l) => l());
  });
}

export const canInstall = (): boolean => saved !== null;

export function onInstallAvailable(fn: () => void): () => void {
  listeners.add(fn);
  return () => void listeners.delete(fn);
}

export async function promptInstall(): Promise<void> {
  const e = saved;
  saved = null;
  listeners.forEach((l) => l());
  if (e) await e.prompt();
}

/** Adding to the home screen only makes sense on a phone or tablet; on a computer the real app is the Windows helper. */
export const isTouchDevice = (): boolean => typeof matchMedia === 'function' && matchMedia('(pointer: coarse)').matches;
export const isApple = (): boolean => /iphone|ipad/i.test(navigator.userAgent);

/** For tests. */
export function setInstallEventForTests(e: InstallEvent | null): void {
  saved = e;
  listeners.forEach((l) => l());
}
