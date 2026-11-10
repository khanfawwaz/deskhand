# Deskhand web app

The browser app for Deskhand (sign in, connect a computer, give tasks, answer questions, settings), written in
React and TypeScript. It builds to plain static files, and the Deskhand server serves them. It talks to the server
over the same HTTP API the original app uses; nothing on the server had to change for it.

This is the Deskhand app, served at `/app/`. (The original single-file app it replaced is gone; only the privacy and
terms pages are left in `src/taskpilot/web/`, and old `/app-next/` links redirect to `/app/`.)

## Run it

```bash
cd web-app
npm ci                 # install exactly what package-lock.json says
npm run dev            # http://localhost:5173/app/  (needs a Deskhand server on 127.0.0.1:8767, see below)
npm test               # unit and component tests (Vitest + Testing Library + a mock server)
npm run lint
npm run typecheck
npm run build          # type-check, then build to web-app/dist
```

A local server for `npm run dev` to talk to: from the repository root, `python scripts/run_local.py --no-app`
(it starts a Deskhand server on port 8767). The dev server proxies `/v1` and `/agent` to it.

| Script | What it does |
|---|---|
| `npm run dev` | Vite dev server with hot reload and the API proxy |
| `npm run build` | `tsc --noEmit` then `vite build` into `dist/` |
| `npm test` / `npm run test:watch` | Vitest once / in watch mode |
| `npm run lint`, `npm run typecheck` | ESLint (typescript-eslint, react-hooks rules), TypeScript `strict` |
| `npm run test:e2e` | Builds, then runs the Playwright flows in the installed Chrome against a small fake server (`tests/e2e`) |
| `npm run check:bundle` | Gzip size of the first load (scripts + styles) against a 200 KB budget; run after a build |
| `npm run sync:legal` | Copies the words of the privacy policy and terms from the server's pages into the app |
| `npm run gen:api` | Re-generate `src/api/schema.d.ts` from the server's OpenAPI description (needs the project's Python) |

## How it is served

`python scripts/build_web.py` builds the app and copies it to `src/taskpilot/webapp/` (generated, not in git).
`src/taskpilot/spa.py` serves that folder at `/app/`: any page-like address gets `index.html` so the router can
take over (deep links and reloads work), files in `assets/` carry a content hash and are cached for a year,
everything else is re-checked on each load. The Docker image runs the same build in its own Node stage
(`deploy/docker/Dockerfile`), so a server built from the repository always has a fresh app.

The base path is set in one place, `app-base.ts` (`/app/`), kept in step with `MOUNT` in `src/taskpilot/spa.py`.

The server sends a strict content-security policy (no inline scripts), so the theme is applied before first paint by
a tiny external file, `public/theme-init.js`, not by an inline script.

## Map of the code

```
app-base.ts            where the app lives (base path)
src/
  main.tsx             entry: providers, router, theme
  routes.tsx           every route (/, /chat/:id, /privacy, /terms, *)
  api/
    client.ts          typed fetch wrapper: bearer token, error messages from the server, 401 => sign out
    queries.ts         TanStack Query client + shared queries (useMe, useAuthConfig)
    types.ts           shapes of the server's replies
    schema.d.ts        GENERATED from the server's OpenAPI (request bodies, routes); do not edit
  state/               small Zustand stores: session (token), theme, ui (which dialogs are open)
  hooks/               useEventStream (the live feed over fetch, with reconnect)
  lib/                 plain TypeScript with no React: eventStream parser/loop, storage, one-time codes
  styles/              tokens.css (colours, light and dark), base.css (shared element and button styles)
  features/            one folder per area of the product
    auth/              sign in by email link or code, pairing code
    shell/             the signed-in frame (gate, sidebar + main, preview note)
    chat/              sidebar, conversation, composer
    connect/           the "Connect your computer" dialog
    settings/          the Settings dialog and its boxes
    legal/             privacy policy and terms pages
    orb/               the interactive orb
  test/                test setup, the mock server, a render helper
```

## Conventions

- **Server data** goes through TanStack Query (`useQuery`/`useMutation`); small client-only state goes in a Zustand
  store; component-local state stays in the component.
- **Request bodies** are typed from the generated schema, so a server change that breaks a request fails to compile:
  `const body: components['schemas']['PairIn'] = { ... }`.
- **Styling:** global tokens and a few shared primitives in `styles/`; component styles in CSS Modules
  (`Thing.module.css`). Colours and spacing always come from the tokens, so light and dark work everywhere.
- **Tests** sit next to the code (`Thing.test.tsx`). Screens are tested through the real router against a mock server
  (MSW), the way a person uses them; pure logic is tested directly.
- **Add a feature:** create `src/features/<area>/`, put its screens, its queries (`src/api/<area>.ts`) and its tests
  there, add its route in `routes.tsx`, and open it from the layout or a store flag in `state/ui.ts`. Features do not
  import each other: they share data through the API layer and the stores.

## Regenerating the API types

```bash
cd web-app
npm run gen:api    # runs scripts/dump_openapi.py (builds the real app around a throw-away store) then openapi-typescript
```

Commit the changed `src/api/openapi.json` and `src/api/schema.d.ts`.

## Shell, installable app and end-to-end tests

- **Shell** (`features/shell`): the signed-in frame. On phones the sidebar is a drawer (menu button, dimmed area, Escape,
  closes on navigation). **Ctrl+K / Cmd+K** opens the command palette (`paletteActions.ts` lists what it can do; nothing
  in it deletes data). The dismissible "Use your own Chrome" tip lives here too (`ExtensionTip.tsx`; remembered in
  `localStorage` under the same key as the original app).
- **Shared components** (`src/components`): `Dialog`, `Toaster` + `toast('...')`, `Banner`, `StatusDot`.
- **Orb** (`features/orb`): `orbSim.ts` is the physics with no browser in it (tested); `Orb.tsx` draws it on a canvas.
- **Installable app** (`pwa/`): at build time `pwa/plugin.ts` writes `sw.js` with the list of hashed files. Pages are
  fetched network-first (a new release shows at once; the saved shell is only for when offline), files in `assets/` are
  kept, the API is never touched. It is registered in production builds only (`src/pwa/register.ts`).
- **End to end** (`tests/e2e`, `playwright.config.ts`): sign in by code, connect a computer, a task with a question and a
  follow-up in the same chat, delete a chat, the orb, the palette, the phone drawer, the service worker, and an axe-core
  accessibility scan (light and dark). Set `CHROME_PATH` to use another Chrome.
