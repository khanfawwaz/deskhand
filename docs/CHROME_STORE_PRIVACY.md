# Chrome Web Store: privacy answers for Deskhand

Copy these into the **Privacy practices** tab of the Developer Dashboard. Every statement was checked against
`extension/background.js`, `extension/manifest.json` and `extension/popup.js`. Privacy policy URL:
**https://deskhand.fawwazkhan.dev/privacy** (the page covers the extension in the section "Your computer and the Chrome extension").
Support URL: https://github.com/khanfawwaz/deskhand/issues

## Single purpose
> Let the Deskhand app on the user's own computer carry out web tasks the user asks for (open a page, read its links, click, type into
> a search box) in the user's signed-in Chrome.

## Permission justifications
Host permissions: **none requested.**

| Permission | Justification (paste) |
|---|---|
| `debugger` | The Deskhand app on the same computer asks the extension to read a page and to click and type in the tab it opened for a task the user started, using the Chrome DevTools Protocol. It is used only while a user-started task runs, and Chrome shows its standard "is debugging this browser" bar. No other API can click and type in a page reliably. |
| `tabs` | To open or reuse the task's own browser window, find the tab a task is working in, and read its address and title so the app can confirm the page it reached. |
| `alarms` | Wakes the extension's service worker every few seconds so it stays connected to the Deskhand app on the same computer. |
| `storage` | Stores the pairing token that records the user approved the connection, and the agent window id in session storage. |

## Remote code
**No, I am not using remote code.** All code is in the package; nothing is fetched or evaluated from the network. The only
connection is to the Deskhand app on the same computer, never over the internet.

## Data usage (what the extension handles)
Tick the categories the extension can handle while a user-started task runs:

| Category | Tick? | Why |
|---|---|---|
| Website content | **Yes** | text, links and structure of the page the task is working on are passed to the local app |
| Web history | **Yes** | the address and title of the task's tab (and the list of open tabs, so the app can find its own) are passed to the local app |
| Personally identifiable information | No | not collected on purpose |
| Health information | No | |
| Financial and payment information | No | |
| Authentication information | No | passwords are never read on purpose or typed; if a site asks to sign in, the app asks the user |
| Personal communications | No (see note) | not collected on purpose |
| Location | No | |
| User activity (clicks, keystrokes, scrolling) | No | the extension performs actions it is told to; it does not record the user's own activity |

**Note (owner decision):** the extension is generic. If a user asks Deskhand to work in a webmail or chat page, that page's
text is "personal communications" content passed to the local app. The conservative option is to also tick *Personal
communications* and say so in the justification. All of it goes only to the app on the same computer, never to the developer.

**Certifications** (all three can be ticked, they are true):
- I do not sell or transfer user data to third parties, outside of the approved use cases.
- I do not use or transfer user data for purposes unrelated to the item's single purpose.
- I do not use or transfer user data to determine creditworthiness or for lending purposes.

Statement for the "how data is handled" boxes: data handled by the extension never leaves the user's computer through the extension;
the Deskhand app on the same computer may send what it needs for a task to its AI model provider, as the privacy policy explains.

## Notes for the reviewer (Test instructions field)
> The extension is a bridge to the Deskhand app on the same Windows computer and shows "Not connected" without it. To see the
> full flow: install the Deskhand app from https://deskhand.fawwazkhan.dev (sign in with an email link), open the extension popup
> and press "Connect to Deskhand", then approve the Yes/No window shown by the app (No is selected by default). Ask Deskhand for a
> web task such as "open YouTube and play the latest video from NetworkChuck": it works in its own window, and Chrome shows its
> debugging bar. A short screen recording of this flow is attached/linked. The `debugger` permission is used only to read and
> operate the tab the user-started task works in. The extension makes no network requests other than to the Deskhand app on the same computer.

Attach the review video (`demo-video/out/deskhand-chrome-review.mp4`) or a link to it, plus the images in `dist/extension/store-images/`.

## What is honest to say, and what is not
- Say: "does not request cookie access", "talks only to the app on this computer", "does nothing until you approve".
- Do **not** say "cannot read your cookies" or "never sees passwords" as a technical guarantee: the extension forwards whichever
  DevTools commands the local app sends, so those limits are enforced by the app's behaviour and policy, not by the extension.
  (The store listing text in `extension/STORE_LISTING.md` and the popup now use that wording.)
