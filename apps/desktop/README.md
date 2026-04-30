# Kora Desktop

Local-first Electron desktop app for the Kora Life OS. Talks to the Python `kora_v2` daemon on `127.0.0.1` over authenticated REST + WebSocket. The renderer never touches SQLite, memory files, lockfiles, tokens, `.env`, or process state directly.

The GUI is a working client alongside the Rich CLI. It currently covers Today, Calendar, Medication, Routines, Memory, Repair, Autonomous, Integrations, Settings, Runtime, and a global chat panel backed by the same daemon WebSocket as the CLI.

## Quick start

From the repo root, make sure the Python side works first:

```bash
.venv/bin/python -m pytest tests/unit/test_desktop_api.py -q
```

Then in `apps/desktop/`:

```bash
npm install            # install pinned deps (one-time)
npm run dev            # start Vite renderer at http://127.0.0.1:5173
```

For browser-based GUI development, open `http://127.0.0.1:5173/`. Vite serves a local-only development bridge at `/__kora_dev/connection` and `/__kora_dev/probe` so the browser renderer can discover the daemon without Electron preload.

To run the real Electron shell instead:

```bash
npm run dev:all        # start Vite + Electron together
```

`dev:all` runs Vite at `http://127.0.0.1:5173` and waits for that port before launching Electron with the dev URL. The Electron main process reads `data/.api_token` and `data/kora.lock` from the repo root, so you must have a Kora daemon running before opening the app.

To start the daemon manually:

```bash
.venv/bin/kora        # CLI launcher; daemon comes up if not already running
```

## Available scripts

| Script | What it does |
| --- | --- |
| `npm run dev` | Start Vite renderer only (browser-friendly) |
| `npm run dev:electron` | Start Electron (waits for Vite at 5173) |
| `npm run dev:all` | Both, in parallel, color-coded |
| `npm run build` | Type-check + Vite build + electron-builder |
| `npm run build:renderer` | Renderer only |
| `npm run build:electron` | Electron main/preload only |
| `npm run typecheck` | `tsc -b --noEmit` |
| `npm run lint` | ESLint, zero-warning policy |
| `npm run format` | Prettier write |
| `npm run test` | Vitest |

## Architecture

```
Electron main process
  -> reads data/.api_token + data/kora.lock (REST host/port/token)
  -> probes/starts/stops the local Python daemon
  -> exposes a narrow contextBridge to the renderer (window.kora)

React renderer
  -> @tanstack/react-query for all REST view-models
  -> WSChatClient for /api/v1/ws (streaming tokens, tools, artifacts, decisions)
  -> browser dev bridge for daemon discovery when Electron preload is absent
  -> Tailwind v4 + CSS variables for theming
  -> Radix primitives wrapped into Kora components in src/components/ui/

Python daemon (kora_v2)
  -> exposes /api/v1/desktop/* view-model routes
  -> remains the single source of truth for memory, calendar, life management
```

## Security model

- `contextIsolation: true`, `sandbox: true`, `nodeIntegration: false`, `webSecurity: true`.
- Every IPC handler validates `event.senderFrame.url`.
- CSP locks `connect-src` to `127.0.0.1:*`.
- Renderer never reads filesystem, tokens, or daemon process state.
- All HTTP/WebSocket calls authenticated with the bearer token from `data/.api_token`.

## Design language

The full Visual Design Language lives in `Documentation/plans/desktop-frontend/kora-electron-ui-implementation-plan.md`. Quick reference:

- **Themes** — six families switched via `[data-theme]` on `<html>`: `warm-neutral` (default), `quiet-dark`, `low-stimulation`, `high-contrast`, `soft-color`, `compact-focus`. All values live in `src/styles/themes.css` as OKLCH.
- **Density** — `cozy | balanced | compact` via `[data-density]`.
- **Motion** — `normal | reduced | none` via `[data-motion]`. Honors `prefers-reduced-motion` on first paint.
- **Typography** — Inter Variable (UI), Fraunces (narrative), JetBrains Mono Variable (data). Self-hosted via `@fontsource-variable/*` packages, imported in `src/styles/globals.css`.
- **Colors** — never hardcode. Always `var(--bg)`, `var(--accent)`, `var(--provenance-local)`, etc.
- **Provenance** — 4px left rule + 6px dot. Never saturated full-bleed.
- **Status** — shape-coded (`circle = ok`, `triangle = warn`, `square = degraded`, `diamond = unknown`).
- **Anti-patterns** — no generic shadcn dashboard, no glassmorphism on cards, no neon, no pure-black, no spinners (use skeletons).

## Repository layout

```
apps/desktop/
  electron/
    main.ts            # secure window + IPC
    preload.ts         # narrow contextBridge
    daemon/
      lifecycle.ts     # probe/start/stop daemon
      lockfile.ts      # read data/kora.lock + data/.api_token
      health.ts        # GET /api/v1/health
      logs.ts          # tail data/daemon.log
  src/
    main.tsx           # React entry
    app/               # App, providers, routes, query-client
    components/
      ui/              # Kora primitives (button, card, pill, …)
      shell/           # AppShell, Sidebar, ChatPanel, CommandBar, CommandPalette
      chat/            # streaming chat surface
      artifacts/       # typed artifact renderers
    features/          # one folder per top-level screen
      today/
      calendar/
      medication/
      routines/
      repair/
      memory/
      autonomous/
      integrations/
      settings/
      runtime/
    lib/
      api/             # typed REST client + TanStack Query hooks
      ws/              # WebSocket client + chat store
      theme/           # provider + theme types
      shortcuts/       # global keyboard shortcuts (Cmd-K, etc.)
      dates/           # Intl-based date helpers
      utils.ts         # cn() helper
    styles/
      globals.css      # tailwind + font imports
      tokens.css       # radii, shadows, motion, density steps
      themes.css       # the six theme palettes (OKLCH)
      calendar/
        theme.css      # FullCalendar visual override
```

## Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| `Cmd/Ctrl-K` | Open command palette |
| `Cmd/Ctrl-/` | Toggle right chat panel |
| `Cmd/Ctrl-1..9` | Jump to nav routes |
| `?` (in Calendar) | Show calendar shortcuts |
| `D / W / M / A` (in Calendar) | Switch to Day/Week/Month/Agenda |
| `T` (in Calendar) | Jump to today |

## Troubleshooting

- **"Daemon not reachable"** — start the daemon (`.venv/bin/kora`), then refresh.
- **Browser dev says disconnected** — confirm the daemon is running, then check `http://127.0.0.1:5173/__kora_dev/connection`.
- **Chat sends once but stays disabled** — the renderer expects daemon `response_complete` or `turn_complete` events; verify the WebSocket event stream in DevTools.
- **Theme changes don't persist** — clear `localStorage` keys under `kora-desktop:*`.
- **Calendar looks like default FullCalendar** — verify `src/styles/calendar/theme.css` is imported via `globals.css`.

## Status

This app is now a working GUI client for Kora alongside the CLI. Browser dev mode has been route-swept across the main screens, and chat has been smoke-tested through the actual textarea/send flow against the daemon WebSocket. Packaging/distribution remains a separate hardening step.
