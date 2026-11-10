/** The Chrome extension lets Deskhand work in the person's own Chrome. Shared words and rules for every place that mentions it. */

/** Only a plain https link is ever shown as "Add to Chrome"; anything else (empty, odd schemes) means "not published yet". */
export function extensionLink(url: string | null | undefined): string {
  return url && /^https:\/\//i.test(url) ? url : '';
}

export const EXTENSION_WHY =
  'Install the Deskhand Chrome extension so Deskhand can work in the Chrome you already use, where you are signed in to ' +
  'YouTube, Netflix and more. Without it Deskhand uses a separate browser profile and you would have to sign in to each site again.';
