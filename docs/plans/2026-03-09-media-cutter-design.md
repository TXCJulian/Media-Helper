# Media Cutter Feature — Design Document

## Context

The Jellyfin_Media-Renamer app has three feature panels (Episodes, Music, Lyrics). This adds a fourth: **Media Cutter** — a tool for trimming audio and video files with visual waveform preview, playback, and broad codec support.

## Requirements

- **Trim mode**: Single in-point / out-point to keep one segment
- **File source**: Browse server directories OR upload from browser
- **Preview**: Video files → large video player + small waveform bar underneath. Audio files → large waveform (same size as video player)
- **Playback**: Play/pause with cut preview (plays only the kept segment)
- **Cut mode**: Default stream copy (fast), toggle to re-encode (precise). User chooses.
- **Output format**: Preserves original by default. When re-encode active, user can pick output codec/container.
- **Output naming**: User sets custom output filename. If not set, uses original name with collision avoidance.
- **Codec support**: AAC, AC3, FLAC, Opus, DTS, MP3 — live transcoding to AAC for browser playback of unsupported codecs
- **Accent color**: Emerald/teal (`--accent-4: #34d399`)

## Architecture

**Monolithic backend** — FFmpeg installed directly in the backend Docker image. No sidecar service. Subprocess calls for waveform generation, live transcoding, and cutting. Matches the existing pattern where each feature module is a Python file with subprocess/library calls.

### Backend (`backend/app/cutter.py`)
- `probe_file()` — ffprobe wrapper returning file metadata
- `generate_waveform()` — PCM extraction via ffmpeg, bucketed into normalized peaks, LRU-cached
- `needs_transcoding()` — codec compatibility check for browser playback
- `transcode_for_preview()` — live transcode to fragmented MP4 with AAC audio
- `cut_file()` — ffmpeg trim with stream copy or re-encode, progress parsing

### API Routes (in `main.py`)
- `GET /cutter/files` — list cuttable files in a directory
- `GET /cutter/probe` — file metadata
- `GET /cutter/waveform` — waveform peak data
- `GET /cutter/stream/{file_id}` — playback with range requests + live transcoding
- `POST /cutter/upload` — file upload with size/extension validation
- `POST /cutter/cut` — SSE-streamed cut execution

### Frontend Components
- `CutterPanel` — main orchestrator (source selection, file browsing/upload, cut execution)
- `MediaPlayer` — video/audio player with cut preview and bidirectional time sync
- `WaveformBar` — canvas-based waveform with draggable trim handles
- `TrimControls` — time input fields for in/out points
- `OutputSettings` — filename, stream copy toggle, codec/container selection

## Key Decisions

1. Raw subprocess over ffmpeg-python library — simpler, fewer deps
2. Base64 file IDs for stream URLs — stateless, no database
3. Fragmented MP4 for transcoded preview — enables streaming without moov atom seek
4. LRU-cached waveform keyed on (filepath, mtime) — automatic invalidation
5. Upload cleanup via asyncio background task — no external scheduler
6. 2GB upload size limit enforced at both nginx and application level
