# Deskhand

Tell your computer what to do, by voice or text, from any device. (Internal code name: TaskPilot; the Python package is `taskpilot`.)

## What is in this repository

| Part | Runs on | What it is |
|---|---|---|
| **Server** (`taskpilot.server`) | one Linux box | the brain: sign-in, tasks, the model, safety checks, hands out updates, serves the web app |
| **Web app** (`src/taskpilot/web`) | any phone or computer browser | where you type or speak commands; served by the server, nothing to install |
| **Agent** (`taskpilot.agent`) | each Windows computer you want to control | a small background helper that does the clicking and typing; installed with one pasted line |

# Engineering notes: the automation engine

Linux server (model + orchestrator) drives a Windows agent: **observe → reason → validate → act → verify**.
The model only *proposes* JSON; `policy.py` + `orchestrator.py` decide, and the agent re-checks policy locally.

```
src/taskpilot/
  protocol.py      versioned action/result schemas (strict, discriminated union)
  policy.py        allowlists, hotkey/command/dir rules (no model input)
  states.py        task state machine         store.py   SQLite: tasks, transitions, actions, devices, metrics
  orchestrator.py  loop, retries, reconciliation, cancel, completion gate
  model.py         ModelRuntime (Ollama vision, scripted test runtime)
  devices.py       hub + WebSocket connection   server.py  FastAPI API + /agent/ws   cli.py  `taskpilot`
  agent/           executor (idempotent, e-stop), runner (no shell), backend (mss/pyautogui/psutil), main
```

## Run

```bash
pip install -e ".[server,dev]"                  # server (Linux); add ".[agent]" on the Windows laptop
cp server_policy.example.json server_policy.json   # edit approved apps / dirs / commands
export TASKPILOT_ADMIN_KEY=...; ollama pull qwen2.5vl:7b
taskpilot-server --host 100.x.y.z               # bind to the Tailscale address, not 0.0.0.0
taskpilot enroll rtx-laptop                      # prints the device token ONCE
```

Windows (token via env, never in the file) with `agent.json` per `agent/main.py`:

```powershell
$env:TASKPILOT_TOKEN="..."; taskpilot-agent --config agent.json
```

```bash
taskpilot submit rtx-laptop "Open Notepad and type hello"
taskpilot status <id> | actions <id> | cancel <id> | approve <id> [--deny] | estop | metrics | revoke <device>
```

## Guarantees implemented (and tested)

- Unknown tools / bad params / unapproved apps / non-allowlisted commands / bad dirs / blocked hotkeys are rejected on the server **and** the agent.
- `done` needs cited evidence, no uncertain actions, and passing launch/command verification (process **and** visible window; exit code + no timeout).
- Lost replies are reconciled by `action_id` query; an action is never blindly repeated. Unresolvable outcomes are `uncertain` and block completion.
- Cancel stops dispatch and kills TaskPilot-started processes; e-stop halts the agent; revoked tokens are rejected, live sessions dropped.
- Typed text / command output / screenshots are not persisted unless `--debug-logging`.

## Known gaps vs. the PRD (not done yet)

- **Model benchmark (§13):** the harness exists (below) but no real model has been scored; `qwen2.5vl:7b` is a placeholder. `OllamaRuntime` is untested against a live model.
- **Real Windows desktop:** `WindowsBackend` screenshot, active-window and process-status were smoke-tested on this laptop; launch, click, type, hotkeys, key press and the approval flow were live-tested on this laptop with `scripts/live_desktop_test.py` (Notepad; scroll and double-click were not), and the automated suite uses a fake backend. A launched app does not always get focus, so input needs a focus check (the live test has one; the agent itself does not yet). Accessibility (UI Automation) is not implemented — coordinate control only.
- **Verification** of click/type/menu navigation relies on the model reading the next screenshot (there is no OCR/UIA check); only launch and command have deterministic checks.
- Reconnect recovery is tested with an in-process link plus one clean real-WebSocket run, not a real network drop.
- TLS/Tailscale is assumed; the server does not terminate TLS itself. The dashboard is minimal.

## Model benchmark (PRD §13)

Fixed suite of 13 cases (launch, input, visual targeting, verification, uncertainty, injection, terminal, refusal) rendered deterministically — no desktop needed. Scored per case: valid JSON → schema → policy-accepted → correct (click inside target box, etc.); plus median/p95 latency and Ollama RAM.

```bash
pip install -e ".[bench]"
python -m taskpilot.bench.run --models qwen2.5vl:3b qwen2.5vl:7b --repeats 3   # smallest first
```

Writes `bench_results/bench-*.md|json`. A model passes at schema ≥95%, correct ≥80%, safety 100%, p95 ≤30 s; the recommendation is the smallest passing model. Run it **on the Linux server** — latency and RAM are only meaningful there. The harness itself is verified with oracle/hostile/garbage runtimes (`tests/test_bench.py`); no real model has been scored yet.

## Hosted models (NVIDIA API catalog, Groq, xAI, any OpenAI-compatible API)

```bash
export NVIDIA_API_KEY=...                      # env var only; never in a file
taskpilot-server --provider openai --model <model-id-from-build.nvidia.com>   # text-only by default
taskpilot-server --provider openai --model <vision-model-id> --allow-cloud-images
python -m taskpilot.bench.run --provider openai --models <id> --allow-cloud-images --pause 1.6
```

Screenshots are sent to the provider **only** with `--allow-cloud-images`. Rate limits (HTTP 429/5xx) are retried with backoff honoring `Retry-After`. Check the provider's current free-tier terms (limits, data use, prototype-only clauses) before relying on it. Model IDs/availability are not verified here.

## Tools first, screenshots last

The model is weakest at pixels, so most work goes through the **tool registry** (`src/taskpilot/tools/`):
`files.read/write/list/exists`, `git.status/log/diff`, `browser.open_url`, `windows.list/focus`,
`clipboard.get/set`, `vscode.open`. Each tool = args schema + policy check (server **and** agent) + agent handler +
server-side verifier (e.g. `files.write` re-reads the file and compares SHA-256). The model prompt is generated from the registry.

Weaknesses are handled in code, not by trusting the model:
- **Screenshots only when needed** (`Config.screenshot_mode="auto"`): attached after a visual action or on `capture_screen`; a click with no screenshot is refused.
- **Lenient parsing** (`normalize.py`): repairs shape slips (`"type"`, flat JSON, `"coordinate":[x,y]`, `"ctrl+c"`), then strict validation + policy still decide.
- **Uncertain-outcome gate:** after an unknown outcome only observation is allowed. **Risk screen:** goals like "shut down" need approval before the model is even asked.
- File contents, clipboard and window titles reach the model only as `untrusted_output`; file text is never persisted.

Add a tool in one place:

```python
registry.register(Tool("notes.add", "Append a line to the notes file.", AddArgs, handler,
                       check=policy_check, verify=server_side_check, idempotent=False))
```

## New apps: UI Automation, zoom, and saved recipes

Order of preference for any app: **recipe/tool -> `ui.*` (controls by NAME) -> pixels**.

- `ui.snapshot` lists a window's controls (role, name, id, value); `ui.click` / `ui.type` / `ui.read` / `ui.wait` act on them by name or id through UIA patterns (no mouse, no focus needed). `ui.read` is the deterministic way to verify results; `ui.type` reads the field back.
- `screen.zoom{x,y,width,height}` shows an enlarged crop for tiny targets; the next click is mapped back to real display coordinates.
- **Gates (agent-side, per call):** a window may be automated only if its exe is an approved app, or its title contains an approved app id / `ui_allowed_windows` entry (Store apps live in `ApplicationFrameHost.exe`, so the title matters). Shells, task manager, credential stores, UAC and Windows Security are always refused. `restrict_input_to_approved: true` also confines raw click/type/hotkeys to approved apps. Password fields are never typed into or read.
- **Pixel clicks are a last resort (`Config.prefer_ui`):** refused until the app was probed with `ui.snapshot`, and refused for apps that expose controls.
- **Recipes:** run with `taskpilot-server --record-recipes`; each successful `ui.*` task leaves a *draft* (it holds the text typed, so it is opt-in). Review and approve:

```bash
taskpilot recipes                                   # list
taskpilot recipe-show draft_ab12cd34                # steps + problems
taskpilot recipe-approve draft_ab12cd34 --as calc_add --description "Add two numbers"    --param a=Seven --param b=Five --check '{"step": {"tool": "ui.read", "window": "Calculator", "id": "CalculatorResults"}, "contains": "Display is"}'
taskpilot recipe-run calc_add my-laptop --arg a=Nine --arg b=Five     # replay, no model
```

An approved recipe also appears to the model as the tool `recipe.calc_add`. Every replayed step is re-validated, policy-checked and verified; the first failure stops it. Recipes may only contain `ui.*`, `windows.focus`, `launch_application`, `press_key`, `hotkey`, `wait` (no files, commands or pixel coordinates). Live checks: `scripts/live_ui_test.py [--fresh]`.

## The Pilot app (control from any device)

An installable web app (PWA): **text + voice** from any phone/laptop/tablet; the server runs the task and its agent acts on the target laptop. Controllers never talk to a target directly.

```bash
taskpilot-server --host 100.x.y.z            # prints the app URL and a one-time pairing code
taskpilot-server --demo --db :memory:        # try the whole app with a SIMULATED laptop: no API key, no risk
taskpilot pair | clients | unpair <id>       # more codes / list / revoke devices (admin key)
```

1. Open `https://<server>/app/` on the new device, enter the pairing code (single use, 10 min, wrong guesses rate-limited). The device gets its own revocable token (only a hash is stored).
2. Type or tap the mic. Pending approvals appear as a card (Approve / Deny, or say "approve" / "deny"); "stop" cancels; "emergency stop" halts everything. Settings: auto-send voice after 2 s, read results aloud, approval alerts, install, forget device.
3. **HTTPS is required** for the microphone, the service worker and "Install": use Tailscale's HTTPS for the server (e.g. `tailscale serve`; check the syntax for your Tailscale version). Plain `http://<tailscale-ip>` works for typing but not for voice/install.

Voice uses the browser's own speech recognition (Chrome/Edge/Safari): free and instant, but the audio goes to the browser vendor's speech service. Server-side Whisper is the planned private option. API: `POST /v1/pair`, `GET /v1/me|devices|tasks|tasks/{id}|events` (SSE), `POST /v1/tasks`, `/v1/tasks/{id}/cancel|approve`, `/v1/estop`. Controller tokens cannot enroll devices, mint codes or see admin data.

## Setup in three steps, and automatic updates

1. **Server (once, Linux):** clone the repo, then `sudo bash deploy/install_server.sh`. It starts Pilot at boot and checks
   GitHub every 2 minutes; when you push, the server pulls and restarts. Add your `NVIDIA_API_KEY` to `/etc/deskhand.env`, then
   `sudo tailscale serve --bg 8765` for HTTPS (voice and "install app" need it).
2. **Controller (any device):** open the server's address, enter the pairing code printed by the server.
3. **A laptop to control (Windows):** in the app, Settings > *Add a Windows laptop to control*, paste the one line it shows
   into PowerShell on that laptop. It installs per user (no admin), starts at sign-in, runs hidden at low priority, and
   adds Start-menu shortcuts (Ctrl+Alt+P start, Ctrl+Alt+O open the app).

**Updates:** push to GitHub, the server pulls, `/agent/version` changes, and each laptop downloads the new agent (checksum
verified), waits until it is idle, restarts into it, and rolls back by itself if the new version keeps crashing. Laptops only
talk to your server, never to GitHub. Uninstall a laptop with `scripts/uninstall_agent.ps1`.

## Friends and separate ecosystems (workspaces)

One server can host several people who each get a private **workspace**: their own controllers, laptops, tasks and learned
experience. Nobody can see or command another workspace (asking for something in another workspace answers 404, the same
as "does not exist"); emergency stop, the live event stream and experience memory are all scoped too.

- **You (server owner):** `POST /invites` with your admin key (`X-API-Key`) returns a one-time code to send a friend.
  `GET /workspaces` lists everyone with counts; `PATCH /workspaces/{id}` sets `max_tasks_per_day`, `disabled` or `name`.
- **Your friend:** opens the app, enters the invite code. That creates their workspace and their first controller.
  From Settings they add their own phones/computers (*Add another phone or computer*) and laptops (*Add a Windows
  laptop*); both kinds of code carry the workspace, so nothing they add can land in yours.
- **Limits:** each workspace has a daily task cap (default 100) so one friend cannot use up your model key.
- **Trust:** a workspace is isolated from other friends, but as the server owner you can technically read the database.
  For someone who needs privacy from you, they should run their own server (same code, one workspace).
- Recipes (the saved UI flows) are still server-wide and admin-only; they are not per workspace.
- Invites are held in memory (a restart forgets unused ones); the workspaces themselves are stored in the database.

## Self-service sign-in by email (no invites needed)

People open the app, type their email, tap the link that arrives, and they are in with their own private workspace.
No passwords. Returning users get a new controller in their existing workspace. Your friends never need you.

**One-time setup on the server** (put these in `/etc/deskhand.env`, never in the repo):

| Variable | Value |
|---|---|
| `TASKPILOT_SMTP_USER` | your Gmail address, e.g. `you@gmail.com` |
| `TASKPILOT_SMTP_PASSWORD` | a Google **app password** (Google Account > Security > 2-Step Verification > App passwords). Not your real password |
| `TASKPILOT_PUBLIC_URL` | the address people use, e.g. `https://pilot.your-tailnet.ts.net` (required: it is the link in the email) |
| `TASKPILOT_SIGNUP` | `open` (default once email works), or `closed` so only existing users can sign in |
| `TASKPILOT_SIGNUP_DOMAINS` | optional, e.g. `school.edu,gmail.com`: only these domains may sign up |
| `TASKPILOT_MAX_WORKSPACES` | default 25: the server refuses new sign-ups beyond this |
| `TASKPILOT_SIGNUP_TASKS_PER_DAY` | default 30 tasks/day for each self-service workspace |

The server sends the email itself over SMTP as your mailbox. Pilot does not operate your Gmail website. Gmail allows
about 500 emails a day and mail from a personal address can land in spam; for a bigger audience, point
`TASKPILOT_SMTP_HOST/PORT` at a transactional provider instead.

Safeguards: per-address and per-email rate limits, a cap on total workspaces, optional domain allow-list, small default
quotas, Gmail aliases (`a.b+x@gmail.com`) counted as one person, one-time 15-minute codes, the sign-in code travels in the
link's `#fragment` (never sent to any server log), and the link address comes only from `TASKPILOT_PUBLIC_URL` so a forged
Host header cannot turn the email into a phishing link. You can still switch any workspace off or change its quota with
`PATCH /workspaces/{id}`.
