# Contributing to Deskhand

Thanks for looking. Deskhand is a small independent project, so a short, friendly process works best.

## Before you start
- Open an issue first for anything bigger than a small fix, so we can agree on the idea before you spend time on it.
- Found a security problem? Please do not open a public issue: see [SECURITY.md](SECURITY.md).
- Be kind. Assume good intent, keep feedback about the code.

## Run it locally
```bash
python -m venv .venv && .venv/Scripts/activate        # on macOS/Linux: source .venv/bin/activate
pip install -e ".[server,dev]"
pytest -q                                              # server and Windows-helper tests

cd web-app
npm ci
npm run typecheck && npm run lint && npm test -- --run && npm run build
```
`python scripts/run_local.py` starts a local server and the helper together on a Windows machine.

## What a good pull request looks like
- One idea per pull request, with a short description of what changed and why.
- Tests for new behaviour (`tests/` for Python, `web-app/src/**/*.test.tsx` for the web app). The same checks run on GitHub for every pull request.
- Plain, readable code. Comments say why, not what. No new dependency without a reason.
- Anything the Deskhand AI model reads (prompts, hints) is written as guidance, not as strict rules. Hard safety limits belong in code (`policy.py`), where they are enforced whatever the model says.
- Never commit secrets, real addresses or personal data. `.env`, databases and the secrets file are ignored on purpose.

## Releases and deployment
Merging to `main` runs the tests and, when they pass, publishes the server image. Maintainers' servers pick it up on their own; see [docs/DEPLOY.md](docs/DEPLOY.md).

## Licence
By contributing you agree that your contribution is released under the [MIT licence](LICENSE).
