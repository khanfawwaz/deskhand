# Security policy

Deskhand is a small independent project. It controls a computer on a person's behalf, so security reports are taken seriously
and are welcome.

## Reporting a vulnerability

**Please do not post vulnerability details in a public issue.**

1. Use GitHub's private reporting: on the repository page open **Security > Report a vulnerability**
   (<https://github.com/khanfawwaz/deskhand/security/advisories/new>).
2. If that is not available to you, open a normal issue titled **"Security: please contact me"** with **no details**, and the
   maintainer will arrange a private channel.

Please include what you found, how to reproduce it, which component it affects (web app, server, Windows app, Chrome extension)
and what you think the impact is. A short proof of concept is plenty.

**What to expect:** an acknowledgement within 7 days, an assessment within 14 days, and a fix or a clear explanation as soon
as it is practical. This is a volunteer project with no paid team, so these are targets, not guarantees. You will be credited
in the fix notes if you want to be.

## Supported versions

Only the latest version is supported: the code on the `main` branch and the live service at
<https://deskhand.fawwazkhan.dev>. The Windows app and its Chrome extension update to the version the server offers.

## In scope

- The Deskhand server and its API (sign-in, tokens, workspace isolation, rate limits, the data export and delete endpoints)
- The web app (`src/taskpilot/web`)
- The Windows app and its installer, including the safety policy that limits which apps, folders and sites it may touch
- The Chrome extension in `extension/` and its local connection to the Windows app
- Anything that lets one person see, change or delete another person's data, or run a task on a computer that is not theirs
- Anything that bypasses the approval step for a risky action, or the emergency stop

## Out of scope

- Findings that need a rooted or already-compromised computer, or physical access to the person's unlocked computer
- Denial of service by volume, spam, or social engineering of the maintainer or users
- Weaknesses in third-party services Deskhand uses (the AI model provider, Gmail, Tailscale, Oracle Cloud, Chrome itself):
  report those to the vendor
- Missing best-practice headers or settings with no demonstrable impact
- AI model mistakes that the safety policy and approvals already catch (a wrong answer is not a vulnerability; a way to make
  the policy let something through is)

## How Deskhand is built to be safe (for reviewers)

- Safety rules are enforced by the computer and server, outside the AI model: approved apps, folders and sites, and
  approval for risky steps.
- No passwords. Email sign-in codes work once, expire, and are rate-limited per address and per IP.
- Sign-in tokens are shown once; only SHA-256 hashes are stored. Each workspace is isolated.
- The Chrome extension talks only to the Deskhand app on the same computer, loads no remote code, and does nothing until the person approves the
  connection in a Yes/No window on their computer.
- People can export and erase their data (Settings > Your data); finished tasks are deleted automatically after 90 days by default.

See [docs/DATA_PROTECTION.md](docs/DATA_PROTECTION.md) for the data inventory and incident steps.

## Safe harbour

Good-faith research that follows this policy (no access to other people's data beyond what proves the issue, no disruption of the
service, private reporting) will not be pursued.
