# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Full-stack microservices app for renaming TV show episodes (via TMDB) and music files (via ID3 tags), cutting/trimming media with ffmpeg, downloading media via yt-dlp, and optional lyrics transcription through a separate GPU-powered service. Five feature modules toggled via `ENABLED_FEATURES` env var: `episodes`, `music`, `lyrics`, `cutter`, `download`.

## Commands

### Frontend (from `frontend/`)

```bash
npm run dev              # Vite dev server (proxies API to backend on localhost:8000)
npm run build            # tsc + vite build
npm run test             # Vitest single run
npm run test:watch       # Vitest watch mode
npm run format           # Prettier
npx vitest run src/__tests__/api.test.ts        # Single test file
npx vitest run -t "fetchJson"                   # Tests matching pattern
```

### Backend (from `backend/`)

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload                   # Runs on :8000 (matches Vite proxy); Docker uses :3332
python -m pytest                                # All tests
python -m pytest tests/test_cutter.py -v        # Single test file
python -m pytest tests/test_cutter.py -k "transcode" -v  # Tests matching pattern
python -m ruff check app/                       # Lint
```

### Docker

```bash
docker compose up --build                # Local dev (builds from source)
docker compose --profile gpu up --build  # With lyric-transcriber GPU service
docker compose -f deploy.yml up -d      # Production (pre-built images)
```

## Architecture

### Backend (Python 3.12, FastAPI)

- **`app/main.py`** - FastAPI app with lifespan-managed watchdog observer for filesystem monitoring. All API routes defined here. Path validation prevents directory traversal.
- **`app/config.py`** - Central config loading from environment variables (base paths, API keys, extensions, feature toggles).
- **`app/get_dirs.py`** - LRU-cached directory scanning. Watchdog events clear caches automatically.
- **`app/rename_episodes.py`** - TMDB search → episode matching via `SequenceMatcher` → rename to `S01E01 Title.ext`. Supports language fallback (fetch English if translation missing), umlaut transliteration, accent stripping, and configurable match threshold.
- **`app/rename_music.py`** - Mutagen metadata extraction (supports FLAC, WAV, MP3, Ogg, AIFF, ASF, Musepack) → rename to `DD-TT Title.ext`. Handles mojibake encoding repair and filename collision avoidance.
- **`app/cutter.py`** - Media cutting/trimming with ffmpeg. Probes files with ffprobe, generates waveforms, determines codec/container compatibility, supports stream-copy (lossless instant cut) and full transcode. Per-track codec selection for audio (AAC, FLAC, Opus, AC3, MP3, etc.) and video (H.264, H.265, VP9, AV1). Job-based architecture with persistent state in `/data/cutter-jobs`, 50 GB upload limit.
- **`app/hwaccel.py`** - GPU encoder auto-detection (NVIDIA NVENC, Intel QSV, AMD AMF, VAAPI). Transparently substitutes GPU for CPU encoders with graceful CPU fallback. Configurable via `HWACCEL` env var.
- **`app/transcribe_lyrics.py`** - SSE proxy to external `lyric-transcriber` service. Upload → poll job → download results → save `.lrc`/`.txt` alongside audio. 30-minute polling timeout.
- **`app/fs_utils.py`** - `fsync()` directory flushing (important for network shares) and collision-safe path generation.

### Frontend (React 19, TypeScript, Vite, Tailwind CSS 4)

- **`src/App.tsx`** - Root component managing per-panel state (logs, errors, loading). Simple view navigation: Landing → EpisodePanel/MusicPanel/LyricsPanel/CutterPanel.
- **`src/lib/api.ts`** - HTTP utilities (`fetchJson`, `postForm`) with timeout and error extraction.
- **`src/lib/sse.ts`** - Manual SSE parser using fetch + AbortController (not EventSource, because POST bodies are needed). Events: `progress`, `error_msg`, `done`.
- **`src/components/PanelLayout.tsx`** - Shared layout wrapper for all panels (back button, title, glassmorphism).
- **`src/components/ui/`** - Reusable form components: `DirectorySelect` (keyboard-navigable dropdown), `ToggleSwitch`, `SegmentedControl`, `FormSection`.
- **`src/hooks/useDebounce.ts`** - 500ms debounce for search inputs to reduce API calls.
- **`src/types.ts`** - Shared TypeScript interfaces for all form state and API types.

State management is simple prop drilling from App.tsx - no external state library.

### Infrastructure

- **Nginx** (`frontend/nginx-app.conf`) - Reverse proxy routes `/rename/`, `/directories/`, `/config`, `/health`, `/transcribe/`, `/cutter/`, `/download/` to backend. SSE routes have buffering disabled and 1800s timeout. 50 GB upload limit.
- **Docker Compose** - Bridge network `renamer-network`. Backend uses Jellyfin's pre-built ffmpeg7 (amd64) or standard ffmpeg (ARM). Volumes: media (`/media:rw`), `cutter-jobs` (persistent job state).
- **`deploy.yml`** - Production variant pulling pre-built images from Docker Hub (`bosscock/media-renamer:backend`/`:frontend`).

## Tests

- **Backend**: `backend/tests/` - pytest with fixtures in `conftest.py`. Uses monkeypatching for env vars with module reloads. Covers routes, directory scanning, path validation, episode/music renaming, cutter operations, hardware acceleration.
- **Frontend**: `frontend/src/__tests__/` - Vitest with jsdom. Covers API utilities, hooks, codec compatibility, cutter file IDs, media player.

## Code Style

- **Frontend**: Prettier with no semicolons, single quotes, 100-char width. Tailwind class sorting via `prettier-plugin-tailwindcss`.
- **Backend**: Ruff for linting (no explicit config file - uses defaults).
- **Path alias**: `@` maps to `src/` (configured in `vite.config.ts` and `tsconfig.app.json`).
- **Node version**: `^20.19.0 || >=22.12.0` (enforced in `package.json` engines).

## Environment Variables

Backend reads from `backend/dependencies/.env` (see `.env.example`):

- `TMDB_API_KEY` - Required for episode renaming
- `BASE_PATH` / `BASE_PATHS` - Media root(s), supports multiple comma-separated paths
- `TVSHOW_FOLDER_NAME` / `MUSIC_FOLDER_NAME` - Subdirectory names under base path
- `ENABLED_FEATURES` - Comma-separated: `episodes,music,lyrics,cutter,download`
- `TRANSCRIBER_URL` - Optional, enables lyrics feature
- `ALLOWED_ORIGINS` - CORS origins
- `HWACCEL` - GPU acceleration: `off` or auto-detect (default)
- `VAAPI_DEVICE` - Override VAAPI render device path
- `CUTTER_JOB_TTL` - Job expiry in seconds (default 86400 / 24h)
- `CUTTER_MAX_DIRECT_REMUX_BYTES` - Max file size for direct remux
- `VALID_CUTTER_EXT` - Allowed file extensions for cutter

## Key Patterns

- **SSE streaming**: Backend streams rename/transcription/cutter progress as Server-Sent Events. Frontend parses manually (buffer-based) since POST requests can't use the EventSource API.
- **Directory caching**: LRU cache invalidated by watchdog filesystem events (create/delete/move). Manual refresh via `POST /directories/refresh`.
- **Path security**: All user paths validated against base directories to prevent traversal.
- **Encoding resilience**: Music renaming handles mojibake, multi-codec byte decoding (UTF-8, cp1252, latin-1), and umlaut transliteration.
- **Feature guards**: Each module can be independently disabled. Backend returns 404 for disabled features; frontend hides disabled panels.
- **GPU fallback**: Hardware acceleration auto-detects available encoders at startup; blacklists failing encoders and retries with CPU.
- **Cutter job lifecycle**: Upload → probe → optional preview → cut with progress SSE → download. Jobs auto-expire after TTL.

## Styling

Dark theme with glassmorphism. Three accent colors by module:

- Episodes: blue (`--accent`)
- Music: indigo (`--accent-2`)
- Lyrics: rose (`--accent-3`)

Self-hosted fonts: Geist (UI) and JetBrains Mono (log output) in `frontend/public/fonts/`.
