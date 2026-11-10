# Deskhand: data protection record

A working record of what Deskhand holds, why, who else touches it, for how long, and what to do when something goes wrong.
It backs up the public [privacy policy](../src/taskpilot/web/privacy.html) and must be kept in step with it and with the code.
Last reviewed 9 November 2026. *This is a good-faith engineering record, not legal advice: have a lawyer review it if
Deskhand ever grows beyond a free hobby project.*

## 1. Roles

| Role | Who |
|---|---|
| Controller (decides why and how data is used) | The operator of a Deskhand server. For deskhand.fawwazkhan.dev: the project owner, running it on their own computer. |
| Processors / sub-processors | See section 3. |
| Data subjects | People who sign in; anyone whose data appears on pages a task reads (incidental). |

## 2. Data inventory

Stored in one SQLite file on the server (`/data/deskhand/taskpilot.db`, Docker volume on the owner's computer; MongoDB is also
supported). Workspaces are isolated from each other.

| Data | Fields | Purpose | Basis (GDPR) | Retention | Deleted by |
|---|---|---|---|---|---|
| Email | normalised address (`workspaces.owner_email`) | sign-in, recognise a returning person | contract (provide the service) | until account deletion | Delete account |
| Workspace | id, name, created, daily task limit | separate each person's data | contract | until account deletion | Delete account |
| Signed-in devices ("clients") | name, created, last seen, token hash | keep a phone/browser signed in | contract | until revoked/deleted | Sign out, Delete account |
| Computers ("devices") | name, enrolled, last seen, capabilities, token hash | route tasks | contract | until removed/deleted | Remove computer, Delete account |
| Tasks | goal, status, result, error, timings, policy context | run and show tasks | contract | **90 days** after finishing (`TASKPILOT_RETENTION_DAYS`) | Retention job, Delete account |
| Steps (actions, transitions) | tool, params (typed text and file contents redacted; search words and URLs kept), result, status | show what happened, debugging | contract / legitimate interest | with the task | with the task |
| Experiences | past-task summaries | improve results | legitimate interest | 90 days | Retention job, Delete account |
| Skills ("what Deskhand has learned") | notes per app/site | improve results | legitimate interest | until deleted | Settings > learned, Delete account |
| Usage | per model call: time, workspace, task id, model, tokens, latency, cost | fair use, daily caps, cost control | legitimate interest | **400 days** (`TASKPILOT_USAGE_RETENTION_DAYS`) | Retention job, Delete account |
| Metrics | timings only | performance | legitimate interest | with the task (and 90 days) | Retention job |
| One-time codes | sign-in/pairing codes | authenticate | contract | in server memory, 10-15 minutes | expire / used |
| Logs | proxy: time, request, IP; server: requests, errors | operate, abuse | legitimate interest | proxy 14 days (daily rotation, 14 kept); container logs rotate at 10 MB x 3 | automatic |

Not stored by Deskhand: passwords, audio, screenshots (a screenshot goes to the model for one decision and is not saved),
typed text and file contents inside step records, payment data. The web app sets no cookies; it uses `localStorage` keys
`pilot.token`, `pilot.targets`, `pilot.settings`.

## 3. Processors and other recipients

| Recipient | Data | Purpose | Notes / to verify |
|---|---|---|---|
| NVIDIA (hosted AI model API) | instruction, page/window text, screen description or image, prior steps | choose the next step | Owner: read NVIDIA's current API terms and privacy notice on retention and training use, and confirm they are acceptable. Images can be switched off with `TASKPILOT_ALLOW_IMAGES=0`. |
| Google (Gmail SMTP) | recipient email, message | send sign-in emails | App password in `/data/deskhand/deskhand.env` (mode 600). |
| Browser vendor speech service | voice audio, only when the user uses the mic or Alt+F8 | speech-to-text | Chosen by the user's browser, not by Deskhand; Deskhand receives text only. |
| Oracle Cloud (VM) | all request traffic (TLS ends here), proxy access logs | public front door (nginx) | Content is not stored; logs 14 days. |
| Tailscale | encrypted tunnel traffic; device metadata | link the front door to the home server | Tailscale cannot read tunnel contents. Keep key expiry disabled and the admin console locked down with 2FA. |
| The owner's home server | everything in section 2 | processes and stores | Disk encryption and OS updates are the owner's responsibility (see section 8). |

No data is sold or used for advertising. Extension: collects nothing, sends nothing outside the computer.

## 4. Rights, and how to honour them

| Request | In-app (self-service) | If the person cannot sign in |
|---|---|---|
| Access / portability | Settings > Your data > **Download my data** (`GET /v1/export`: JSON, no tokens, own workspace only) | owner runs the same export for that workspace from the store (`store.export_workspace(<workspace id>)`) after verifying the person by email |
| Erasure | Settings > Your data > **Delete my account and data** (`POST /v1/delete-account`, type-to-confirm) | owner, with the admin key: `DELETE /workspaces/<workspace id>` (only reachable on the server itself / over Tailscale; nginx hides it from the internet) |
| Rectification | rename workspace/computers; delete learned notes | ask via the contact in the policy |
| Objection / withdrawal | stop using, sign out, delete account; voice is opt-in | ask via the contact |

Deadline: reply within 30 days (the policy promises this). Verify identity by sending the email address a message before acting
on a request that arrives by another route. Record each request and date handled (a private note is enough).

Erasure removes: tasks, steps, measurements, experiences, skills, usage rows, computers, signed-in devices and the workspace row,
after stopping anything running. Backups are deleted within 30 days (section 8).

## 5. Retention

A daily job (started with the server, run once at start-up) deletes finished tasks (with steps and measurements) and
experiences older than `TASKPILOT_RETENTION_DAYS` (default 90; 0 = keep forever) and usage rows older than
`TASKPILOT_USAGE_RETENTION_DAYS` (default 400; 0 = keep forever). Skills and running tasks are never touched. The numbers are
printed at start-up and the counts of removed rows are logged.

## 6. Incident / breach response

1. **Contain**: stop the container (`docker compose stop deskhand`) if data may still be leaking; rotate what could be exposed:
   `TASKPILOT_ADMIN_KEY`, the NVIDIA API key, the Gmail app password, Tailscale auth; to sign everyone out, revoke tokens
   (set `revoked=1` in `clients` and `devices`) and ask people to sign in again.
2. **Assess**: what data, whose, how many, since when; keep the logs (proxy logs on the Oracle VM, `docker logs deskhand_server`).
3. **Notify**: people affected, without undue delay, saying what happened, what data, what you did and what they should do.
   If the GDPR applies and there is a risk to people, notify the relevant supervisory authority within 72 hours of becoming aware.
   Chrome Web Store/GitHub need no notice unless the extension or repository was involved.
4. **Fix and learn**: patch, add a test, update this record, say what changed in the repository notes.
5. Report channel for researchers: [SECURITY.md](../SECURITY.md).

## 7. Risks and what reduces them (DPIA-lite)

| Risk | Mitigation | Residual |
|---|---|---|
| The AI acts wrongly on the person's computer or accounts | policy enforced outside the model; approvals for risky steps; approved apps/folders/sites only; emergency stop; step log | AI can still make mistakes: terms say so |
| Sensitive screen content sent to the model provider | only approved windows; privacy-mode policy; images can be disabled; policy tells people | content inside an approved window does leave the computer |
| The Chrome extension is powerful (`debugger`) | local-only connection; Yes/No approval, No by default; can disconnect any time; Chrome's debugging bar; no remote code | the local app decides which DevTools commands it sends: a bug or malware in the app is a risk |
| Another person's data leaks across workspaces | every query is workspace-scoped; "not found" for others; tests | a code bug is possible: report via SECURITY.md |
| Email as the only identity | one-time codes, short expiry, rate limits; no passwords | someone with the mailbox can sign in |
| Single home server: theft, power loss, disk failure | disk encryption (owner), updates, backups with a 30-day delete rule | availability is best-effort (terms) |
| Old data piles up | automatic retention; self-service erase | backups and logs follow their own short schedule |
| Open sign-up abuse | per-address/IP limits, workspace cap, daily task and token caps | small |

## 8. Operational checklist (owner)

- [ ] Add a real contact email to the privacy policy and terms if you want one besides GitHub issues.
- [ ] Decide and add a software licence to the repository (not chosen here).
- [ ] Confirm NVIDIA's API terms on data retention/training and that they suit this use.
- [ ] Enable disk encryption on the home server; keep the OS and Docker updated.
- [ ] Delete leftover database copies made during the move on 9 Nov 2026 within 30 days: Oracle `~/deskhand-backup/`,
      Oracle `/tmp/deskhand-final.db`, and the copy in WSL `/tmp/deskhand-final.db` (they contain user data).
- [ ] Turn on GitHub private vulnerability reporting for the repository (Settings > Security).
- [ ] Tailscale: disable key expiry for the two machines, enable 2FA on the admin account, review Funnel.
- [ ] Re-read this record and the policy whenever a new third party, data field or feature is added.

## 9. Regulation map (where each obligation is met)

| Obligation | Where |
|---|---|
| Transparency (GDPR 12-14, CCPA notice at collection) | public privacy policy; in-app links |
| Lawful basis, purpose limitation, minimisation (Art. 5-6) | section 2; redaction of typed text; no analytics; no ads |
| Access / portability (Art. 15, 20; CCPA right to know) | `GET /v1/export` |
| Rectification (Art. 16) | rename/delete in Settings; contact |
| Erasure (Art. 17; CCPA right to delete) | `POST /v1/delete-account`; admin `DELETE /workspaces/{id}` |
| Restriction / objection (Art. 18, 21) | stop/sign out/delete; contact |
| Storage limitation (Art. 5(1)(e)) | retention job |
| Security (Art. 32) | hashed tokens, TLS, isolation, rate limits, policy outside the model |
| Breach notification (Art. 33-34) | section 6 |
| Children (Art. 8; COPPA) | not for under 13/16; account removed on request |
| "Sale" / "sharing" under CCPA | none: data is not sold or shared for advertising |
| International transfers (Chapter V) | disclosed in the policy; processors are named in section 3 |
| Records of processing (Art. 30) | this document |
