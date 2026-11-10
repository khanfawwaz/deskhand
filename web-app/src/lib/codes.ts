/** One-time codes look like ABCD-EFGH. People type them in any case, with or without the dash. */
export const normalizeCode = (s: string): string => String(s ?? '').toUpperCase().replace(/[^A-Z0-9]/g, '');

export const prettyCode = (s: string): string => {
  const c = normalizeCode(s).slice(0, 8);
  return c.length > 4 ? `${c.slice(0, 4)}-${c.slice(4)}` : c;
};

/** A readable name for "this device" when signing in (shown later in the list of devices). */
export function deviceName(): string {
  const nav = navigator as Navigator & { userAgentData?: { platform?: string } };
  return nav.userAgentData?.platform || navigator.platform || 'My device';
}
