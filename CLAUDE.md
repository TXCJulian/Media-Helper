# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Full-stack microservices app for renaming TV show episodes (via TMDB) and music files (via ID3 tags), with optional lyrics transcription through a separate GPU-powered service. Three feature modules toggled via `ENABLED_FEATURES` env var: `episodes`, `music`, `lyrics`.

## Commands

### Frontend (from `frontend/`)
```bash
npm run dev          # Vite dev server (proxies API to backend on localhost:8000)
npm run build        # tsc + vite build
npm run test         # Vitest single run
npm run test:watch   # Vitest watch mode
npm run format       # Prettier
```

### Backend (from `backend/`)
```bash
pip install -r requirements.txt
uvicorn app.main:app --reload   # Runs on :8000 (matches Vite proxy); Docker uses :3332
pytest               # Run backend tests
```

### Docker
```bash
docker compose up --build              # Local dev (builds from source)
docker compose --profile gpu up --build # With lyric-transcriber GPU service
docker compose -f deploy.yml up -d     # Production (pre-built images)
```

## Architecture

### Backend (Python 3.12, FastAPI)

- **`app/main.py`** — FastAPI app with lifespan-managed watchdog observer for filesystem monitoring. All API routes defined here. Path validation prevents directory traversal.
- **`app/config.py`** — Central config loading from environment variables (base paths, API keys, extensions, feature toggles).
- **`app/get_dirs.py`** — LRU-cached directory scanning. Watchdog events clear caches automatically.
- **`app/rename_episodes.py`** — TMDB search → episode matching via `SequenceMatcher` → rename to `S01E01 Title.ext`. Supports language fallback (fetch English if translation missing), umlaut transliteration, accent stripping, and configurable match threshold.
- **`app/rename_music.py`** — Mutagen metadata extraction (supports FLAC, WAV, MP3, Ogg, AIFF, ASF, Musepack) → rename to `DD-TT Title.ext`. Handles mojibake encoding repair and filename collision avoidance.
- **`app/transcribe_lyrics.py`** — SSE proxy to external `lyric-transcriber` service. Upload → poll job → download results → save `.lrc`/`.txt` alongside audio. 30-minute polling timeout.
- **`app/fs_utils.py`** — `fsync()` directory flushing (important for network shares) and collision-safe path generation.

### Frontend (React 19, TypeScript, Vite, Tailwind CSS 4)

- **`src/App.tsx`** — Root component managing per-panel state (logs, errors, loading). Simple view navigation: Landing → EpisodePanel/MusicPanel/LyricsPanel.
- **`src/lib/api.ts`** — HTTP utilities (`fetchJson`, `postForm`) with timeout and error extraction.
- **`src/lib/sse.ts`** — Manual SSE parser using fetch + AbortController (not EventSource, because POST bodies are needed). Events: `progress`, `error_msg`, `done`.
- **`src/components/PanelLayout.tsx`** — Shared layout wrapper for all panels (back button, title, glassmorphism).
- **`src/components/`** — Panel views: `EpisodePanel`, `MusicPanel`, `LyricsPanel`, `Landing`, plus `LogPanel` (streaming log display) and `ErrorBoundary`.
- **`src/types.ts`** — Shared TypeScript interfaces for all form state and API types.
- **`src/components/ui/`** — Reusable form components: `DirectorySelect` (keyboard-navigable dropdown), `ToggleSwitch`, `SegmentedControl`, `FormSection`.
- **`src/hooks/useDebounce.ts`** — 500ms debounce for search inputs to reduce API calls.

State management is simple prop drilling from App.tsx — no external state library.

### Infrastructure

- **Nginx** (`frontend/nginx-app.conf`) — Reverse proxy routes `/rename/`, `/directories/`, `/config`, `/health`, `/transcribe/` to backend. SSE routes have buffering disabled and 1800s timeout.
- **Docker Compose** — Bridge network `renamer-network` connects frontend and backend. Media directory mounted as `/media:rw` volume.
- **`deploy.yml`** — Production variant pulling pre-built images from Docker Hub (`bosscock/media-renamer:backend`/`:frontend`).

## Tests

- **Backend**: `backend/tests/` — pytest with fixtures in `conftest.py`. Covers main routes, directory scanning, path validation, episode renaming, music renaming.
- **Frontend**: `frontend/src/__tests__/` — Vitest with jsdom. Covers API utilities and hooks.

## Environment Variables

Backend reads from `backend/dependencies/.env` (see `.env.example`):
- `TMDB_API_KEY` — Required for episode renaming
- `BASE_PATH` — Media root (default `/media`)
- `TVSHOW_FOLDER_NAME` / `MUSIC_FOLDER_NAME` — Subdirectory names under base path
- `ENABLED_FEATURES` — Comma-separated: `episodes,music,lyrics`
- `TRANSCRIBER_URL` — Optional, enables lyrics feature
- `ALLOWED_ORIGINS` — CORS origins

## Key Patterns

- **SSE streaming**: Backend streams rename/transcription progress as Server-Sent Events. Frontend parses manually (buffer-based) since POST requests can't use the EventSource API.
- **Directory caching**: LRU cache invalidated by watchdog filesystem events (create/delete/move). Manual refresh via `POST /directories/refresh`.
- **Path security**: All user paths validated against base directories to prevent traversal.
- **Encoding resilience**: Music renaming handles mojibake, multi-codec byte decoding (UTF-8, cp1252, latin-1), and umlaut transliteration.
- **Feature guards**: Each module can be independently disabled. Backend returns 404 for disabled features; frontend hides disabled panels.
- **Path alias**: `@` maps to `src/` (configured in `vite.config.ts` and `tsconfig.app.json`).

## Styling

Dark theme with glassmorphism. Three accent colors by module:
- Episodes: blue (`--accent`)
- Music: indigo (`--accent-2`)
- Lyrics: rose (`--accent-3`)

Self-hosted fonts: Geist (UI) and JetBrains Mono (log output) in `frontend/public/fonts/`.
