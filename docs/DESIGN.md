# TaskPilot — system design

## 1. Machines and what runs where

```
                         Tailscale private network (encrypted, no public ports)
 ┌───────────────────────── Linux server (12 GB, CPU only) ─────────────────────┐
 │  taskpilot-server  (FastAPI, systemd)                                        │
 │   ├─ Task API / CLI / dashboard      ← you                                   │
 │   ├─ Orchestrator (state machine, retries, cancel, completion gate)          │
 │   ├─ Policy engine #1 (allowlists, hotkeys, commands, dirs)                  │
 │   ├─ Model runtime ──────────────────────────────► NVIDIA API (HTTPS, key in │
 │   │    (OpenAICompat | Ollama)                      env var, text-only by    │
 │   │                                                 default)                 │
 │   ├─ Device hub  ◄══ WebSocket (agent dials OUT, per-device token) ═╗        │
 │   └─ Store ──► MongoDB (docker, 127.0.0.1)   or   SQLite file       ║        │
 └─────────────────────────────────────────────────────────────────────║────────┘
                                                                       ║
 ┌──────────────── Windows laptop (RTX 4080, unlocked, logged in) ─────╨────────┐
 │  taskpilot-agent  (runs in YOUR desktop session; Task Scheduler "at logon")   │
 │   ├─ Policy engine #2 (same rules re-checked locally; server is not trusted)  │
 │   ├─ Executor (dedup by action_id, cancel, e-stop)                            │
 │   ├─ Backend: screenshot (mss), mouse/keyboard (pyautogui), processes (psutil)│
 │   ├─ Command runner (no shell, allowlist, cwd jail, timeout, output cap)      │
 │   └─ [next] App tools + UI Automation                                         │
 └───────────────────────────────────────────────────────────────────────────────┘
```

The laptop never runs a model and needs no GPU. With a hosted model the server does no inference either, so it
is just an orchestrator + database and has spare RAM/CPU for other work.

## 2. Request lifecycle

`taskpilot submit` → `POST /tasks` (returns task id immediately) → orchestrator loop:
observe → reason (model proposes ONE JSON step) → validate (schema + policy; risky → wait for approval) →
dispatch to agent (agent re-checks policy) → verify (process/window, exit code, next observation) → repeat,
until verified `done`, failure, timeout, action limit or cancel.

Guarantees: model output is data, never authority; a lost reply is reconciled by `action_id` before any resend;
uncertain actions block completion; `done` needs cited evidence + passing deterministic checks.

## 3. Tiered control (cheapest and most reliable first)

| Tier | How | Needs screenshot? | Status |
|---|---|---|---|
| 1. App tools | typed tools: `files.*`, `git.*`, `browser.open_url`, `vscode.open`, `windows.*`, `clipboard.*` | no | **built** (13 tools) |
| 2. UI Automation | text list of window controls; click by name | no | planned |
| 3. Vision | screenshot → model → coordinates | yes | built |

Each app = tool schemas + policy rule + agent handler + deterministic verifier + a short playbook the model
reads only when the task mentions that app. This requires turning the hardcoded action list into a tool registry.

## 4. Storage: MongoDB (optional) vs SQLite (default)

SQLite is entirely sufficient for one user; Mongo is here to learn it and it fits the data reasonably well.
Select with `--db` / `TASKPILOT_DB`: a `mongodb://` URI → Mongo, anything else → SQLite file. Both pass the same
contract tests (`tests/test_store_contract.py`).

| Collection | `_id` | Shape / why Mongo fits |
|---|---|---|
| `tasks` | task id | status, goal, limits, result/error and **embedded `transitions[]`** — a state change and its history are one atomic write |
| `actions` | action id | `params` and `result` stored as real sub-documents; tool results have different shapes per tool |
| `devices` | device id | token **hash** only, `revoked`, `capabilities[]`, `last_seen` |
| `metrics` | auto | latency samples; **TTL index** expires them after 30 days |

Notable details: transitions use optimistic concurrency (`update_one` filtered on the status we validated);
creation timestamps are strictly increasing; the server needs `auth` enabled, localhost-only binding and a capped
WiredTiger cache (`deploy/docker-compose.mongo.yml`: 0.5 GB cache, 1 GB container limit) or it will take ~50% of RAM.
Costs vs SQLite: one more service to run, secure and back up (`mongodump`); sync PyMongo calls block the event loop
for ~ms (fine at this scale; switch to `AsyncMongoClient` if it ever matters).

## 5. Security model
- Private network only; server binds to the Tailscale IP. Admin API behind `X-API-Key`.
- Per-device random token, only its SHA-256 stored; revocation drops the live socket.
- Two independent policy checks (server + agent). Agent runs as a normal user, never elevated.
- Hosted model: screenshots are sent only with `--allow-cloud-images`; API key only in env/`/etc/taskpilot.env`.
- Screen text, files and command output are untrusted; typed text and screenshots are not persisted by default.
- Emergency stop: `taskpilot estop`; pyautogui fail-safe (mouse to a corner) aborts local input.

## 6. Build order
1. ✅ Core engine, policy, agent, store, tests (64 passing)
2. ✅ Benchmark harness; ✅ hosted model backend; ✅ MongoDB backend
3. ▶ Re-run the benchmark (23 cases, tools tier) on NVIDIA models → choose the model
4. ▶ Live desktop test of click/type/launch on the real laptop
5. ✅ Tool registry + 13 app tools, generated prompt, lenient parser, deterministic guards (127 tests)
6. UI Automation tier; observation becomes text-first, screenshot on demand
7. Server deployment (Tailscale, Mongo compose, systemd), real network-drop test, install docs

## 7. Controller app (any device) - built
Roles: **controller** (the app: phone/laptop/tablet) and **target** (a device with the agent). The app talks only to the server (`/v1`, SSE for live progress); the server's agent dials out to targets. Pairing: admin makes a one-time code -> device redeems it -> per-device revocable token (hash stored). Voice = browser speech recognition (privacy trade-off documented; server Whisper planned). Targets: Windows only for now (macOS/Linux/Android need new agent backends; iPhone can only be a controller). Demo mode (`--demo`) simulates a laptop for trying/testing the app. Code: `client_api.py`, `demo.py`, `web/`.
