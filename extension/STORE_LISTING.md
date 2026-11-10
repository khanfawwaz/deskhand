# Chrome Web Store listing: Deskhand

Upload `dist/extension/deskhand-extension-1.0.0.zip` (build it with `python scripts/build_extension.py`).
Images are in `dist/extension/store/`.

## Store listing tab
- **Name:** Deskhand
- **Summary (max 132):** Lets the Deskhand app on your computer carry out web tasks you ask for, in your own signed-in Chrome.
- **Category:** Productivity
- **Language:** English
- **Icon 128x128:** `extension/icons/icon128.png`
- **Screenshots (1280x800):** the three `screenshot-*.png`
- **Small promo tile (440x280):** `promo-tile-440x280.png`
- **Marquee (1400x560, optional):** `marquee-1400x560.png`
- **Privacy policy URL:** https://deskhand.fawwazkhan.dev/privacy

### Description
Deskhand is an assistant that works on your own computer. Ask it for something, such as "play the latest video from my
subscriptions" or "open Netflix and play something", and it does it in the Chrome you are already signed in to, so it
uses your accounts without you sharing a password with anyone.

This extension is the bridge between Chrome and the Deskhand app that runs on your computer. Install the Deskhand app
first (deskhand.fawwazkhan.dev), then press "Connect to Deskhand" in the extension. A window on your computer asks you to
approve the connection once.

Private by design
- Talks only to the Deskhand app on your own computer. No requests to the internet, no remote code.
- Does not request cookie access, and Deskhand does not read or type your passwords or payment details: the Deskhand app only
  uses the extension to read links and to click and type on the tab it opened for your task. If a site asks you to sign in,
  Deskhand asks you.
- Does nothing until you approve the connection, and you can disconnect from the popup at any time.
- Chrome shows its standard "is debugging this browser" bar while a task is running.

## Privacy practices tab
- **Single purpose:** Let the Deskhand app on the user's own computer carry out web tasks the user requests (open a page, read its
  links, click, type into a search box) in the user's signed-in Chrome.
- **Permission justifications**
  - `debugger`: Required to read page content and perform clicks and typing in the tab the user's task is working in, using the
    Chrome DevTools Protocol. Used only while a user-started task runs; Chrome shows its standard debugging bar.
  - `tabs`: Required to find the tab a task is working in and read its address and title, and to open/activate a tab for the task.
  - `alarms`: Keeps the extension's service worker awake so it can stay connected to the local Deskhand app.
  - `storage`: Stores the pairing token that records the user approved the connection.
- **Remote code:** No, the extension does not use remote code.
- **Data usage (what the extension handles):** Website content and the address/title of pages the user's task works on, passed
  only to the Deskhand app on the same computer. It does not collect personally identifiable information, health,
  financial or authentication information on purpose, and it does not request cookie access. See docs/CHROME_STORE_PRIVACY.md for
  the exact answers to paste into the dashboard (including the Data usage checkboxes).
- **Certify:** data is not sold to third parties; not used for purposes unrelated to the single purpose; not used for
  creditworthiness or lending.

## Distribution tab
- **Visibility:** Unlisted (anyone with the link can install) is a good start; make it Public when ready.
- **Regions:** all.

## Notes for the reviewer (Test instructions)
The extension needs the Deskhand desktop app running on the same computer to do anything. Without it the popup shows
"Not connected". To see the full flow, install the Deskhand app from https://deskhand.fawwazkhan.dev, sign in, then open the
extension popup and press "Connect to Deskhand"; approve the Yes/No window. The `debugger` permission is used only to read
and operate the single tab a user-started task is working in.
