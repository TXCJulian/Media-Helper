# Audio-Only Transcode Mode for Cutter Preview

**Date:** 2026-03-21
**Branch:** feat/hw_acceleration

## Problem

When a video file has a browser-compatible video codec (e.g., h264) but unsupported audio codecs (e.g., TrueHD, EAC3) in an MKV container, the current "Transcoded Preview" mode copies the entire multi-GB video stream into a new MP4 container alongside transcoded audio tracks. This is slow — the bottleneck is the video stream copy, not the audio transcode.

## Solution

Add a second transcode mode — **Audio-Only Transcode** — that skips the video copy entirely. The browser plays the original video file directly (muted `<video>` element) while a separately transcoded AAC audio track plays in sync via a `<audio>` element. This leverages the existing dual-mode playback pattern already implemented in MediaPlayer.

**Browser compatibility note:** MKV container support varies across browsers. Chrome generally plays MKV with h264; Firefox and Safari may not. The "Audio-Only Transcode" button is offered regardless of container issues — the user decides what works in their browser. If it doesn't work, they can fall back to "Full Transcode."

## Three Playback Modes

1. **Original Playback** (`'off'`) — serve the file as-is (current default)
2. **Audio-Only Transcode** (`'audio_only'`) — serve original file for video (muted), transcode only the selected audio track to AAC on-demand (~10-30s for a 2-hour TrueHD track)
3. **Full Transcode** (`'full'`) — current behavior: copy video + transcode all audio into MP4 master preview

The user chooses the mode via buttons in the compatibility warning UI.

### When to show each button

The "Transcode Audio Only" button is shown when **all** of these are true:

- The file has a video stream (`isVideo`)
- The video codec is browser-compatible (`compatibilityReport.videoIssue === null`)
- The file needs transcoding (`probe.needs_transcoding`)

The button is shown regardless of `containerIssue` — the user decides if their browser handles the container. If the video codec itself is unsupported, or the file is audio-only, only the "Full Transcode" button is shown.

## Backend Changes

### New function: `transcode_audio_track_from_source`

**File:** `backend/app/cutter.py`

Transcodes a single audio track directly from the source file (not from a master preview) to an audio-only MP4 with AAC codec.

- **Input:** `filepath`, `audio_stream_index` (absolute index from probe), `job_id`
- **Output:** Path to cached audio-only MP4 file
- **Cache key:** `preview_{hash}_srcaudio{abs_index}.mp4` in the job directory (distinct from `_audioabs{index}` used by `get_audio_track_preview` which extracts from the master). Both file types live in the same job directory and are cleaned up together by `delete_job` / TTL pruning.
- **Index handling:** The `audio_stream_index` parameter is the absolute stream index (as returned by probe). Internally, the function converts to a relative audio index using the existing `_audio_relative_index` helper for the FFmpeg `-map 0:a:{rel}` argument.
- **FFmpeg command:** `-i source.mkv -map 0:a:{rel} -vn -c:a aac -b:a 192k -f mp4 -movflags +faststart output.mp4`
- **Atmos downmix:** When channels > 6, add `-ac 2` (same as existing behavior)
- **Concurrency:** Same locking pattern as existing preview functions (`_get_preview_build_lock`)
- **Cancellation:** Same `_begin_job_operation` / `_end_job_operation` pattern
- **Progress reporting:** Uses the progress-parsing pattern from `get_or_transcode_preview` — parse FFmpeg's `time=` output from stderr against the source file's total duration to compute percent and ETA. Reports via `_set_preview_status` with a **track-specific status key** (see Status Key section below). This differs from `get_audio_track_preview` which does not report progress (it uses blocking `proc.communicate()`).

### New function: `start_background_audio_transcode`

**File:** `backend/app/cutter.py`

A new background transcode launcher specifically for audio-only mode, analogous to `start_background_transcode` but calling `transcode_audio_track_from_source` instead of `get_or_transcode_preview`.

- Uses the audio-only cache file path as the dedup key in `_transcode_locks` (not the master preview path)
- Does NOT change job metadata status to "transcoding" / "ready" (audio-only transcodes are lightweight and shouldn't block the job status)
- Uses the same `_transcode_semaphore` to limit concurrent FFmpeg processes

### Status key separation

The current `_preview_status_key` returns `{job_id}:{hash}` — shared across all preview operations for a file. Audio-only transcode needs a **separate status key** to avoid collisions with full transcode status.

New function: `_audio_transcode_status_key(filepath, job_id, audio_stream_index)` returns `{job_id}:{hash}:srcaudio{abs_index}`.

The frontend preview-status polling endpoint needs a way to query the right status:

- **New query parameter on `GET /cutter/preview-status/{file_id}`:** `audio_transcode_stream: int | None = Query(None)`
- When `audio_transcode_stream` is set: return status for the audio-only transcode of that track (using the track-specific key), bypassing the `get_preview_path_if_ready` check that would incorrectly report "done" if a master preview exists
- When absent: existing behavior (full transcode status)

### Modified stream endpoint

**File:** `backend/app/main.py`, route `GET /cutter/stream/{file_id}`

New query parameter: `transcode_audio_only: bool = Query(False)`

This is a **new parameter alongside** the existing `transcode` and `audio_only` parameters. The semantics:

- `transcode=true` — existing full transcode mode (master preview pipeline)
- `transcode_audio_only=true` — new mode: transcode a single audio track directly from the source file, skip master preview entirely
- `audio_only=true` — existing parameter: extract audio-only from the master preview (used in `'full'` mode)

**Conflicting parameters:** If `transcode_audio_only=true` is combined with `transcode=true` or `audio_only=true`, return 400 Bad Request.

`transcode_audio_only` is an independent code path — it branches **before** the existing `if transcode and needs_tx` block:

```python
if transcode_audio_only:
    # New: transcode single audio track from source
    if not audio_stream:
        raise HTTPException(400, "audio_stream required for audio-only transcode")
    if not job_id:
        raise HTTPException(400, "job_id required for audio-only transcode")
    start_background_audio_transcode(filepath, audio_stream, job_id)
    # Wait for completion, then serve the file
    ...
elif transcode and needs_tx:
    # Existing: full master preview pipeline
    ...
```

The video is served via a separate request with no transcode flags (original file).

### No changes to `needs_transcoding`

The function stays as-is. The frontend decides which transcode mode to use based on user choice.

## Frontend Changes

### State change in CutterPanel

**File:** `frontend/src/components/CutterPanel.tsx`

Replace `transcodePreviewEnabled: boolean` with `transcodeMode: 'off' | 'audio_only' | 'full'`

All existing references to `transcodePreviewEnabled` update to check against the new state:

- `transcodePreviewEnabled` → `transcodeMode !== 'off'`
- `transcodePreviewEnabled && isVideo` → `transcodeMode === 'full' && isVideo` (for full transcode video stream URL)

### Muting in audio-only mode

When `transcodeMode === 'audio_only'`, the `<video>` element must be explicitly muted to prevent the browser from playing the original file's (potentially garbled) default audio track. The MediaPlayer already mutes the video element in dual mode (`isDualMode` sets `mediaRef.current.muted = true`), so this is handled automatically when `audioUrl` is provided.

### Stream URL logic and `api.ts` changes

**File:** `frontend/src/lib/api.ts`

Add a new URL builder for audio-only transcode mode:

```typescript
export function getAudioOnlyTranscodeUrl(
  fileId: string,
  audioStreamIndex: number,
): string {
  const base = `/cutter/stream/${encodeURIComponent(fileId)}`
  const params = new URLSearchParams()
  params.set('audio_stream', String(audioStreamIndex))
  params.set('transcode_audio_only', 'true')
  return `${base}?${params}`
}
```

Stream URL mapping:

- **`'off'`:** `streamUrl` = `getStreamUrl(fileId, streamAudioIndex, false)`, no `audioUrl`
- **`'audio_only'`:** `streamUrl` = `getStreamUrl(fileId, null, false)` (original file, no transcode), `audioUrl` = `getAudioOnlyTranscodeUrl(fileId, selectedAudioIndex)`
- **`'full'`:** unchanged from current behavior (uses existing `getStreamUrl` with `transcode=true` and `getAudioStreamUrl` with `audio_only=true`)

The existing `getAudioStreamUrl` function remains unchanged for the `'full'` mode path.

### Preview status polling

The preview status polling effect needs to pass the audio stream index when in `'audio_only'` mode, so the backend returns the correct per-track status:

- `'full'` mode: `fetchPreviewStatus(fileId)` (unchanged)
- `'audio_only'` mode: `fetchPreviewStatus(fileId, { audioTranscodeStream: selectedAudioIndex })`

The `fetchPreviewStatus` function in `api.ts` needs a new optional parameter for `audio_transcode_stream`.

### UI buttons

In the compatibility warning section, replace the single toggle with two action buttons:

When mode is `'off'`:

- **"Transcode Audio Only"** button — sets mode to `'audio_only'` (only shown when conditions from "When to show each button" are met)
- **"Full Transcode"** button — sets mode to `'full'`

When mode is `'audio_only'` or `'full'`:

- **"Use Original Playback"** button — sets mode back to `'off'`

### Progress overlay

Reuse the existing transcoding progress overlay (spinner, percentage, ETA) for both `'audio_only'` and `'full'` modes. The backend reports progress identically for both via the same `_set_preview_status` mechanism (just with different status keys).

### Audio track switching

In `'audio_only'` mode, when the user switches audio tracks:

- Update the `audioUrl` to point to the new track's `transcode_audio_only` endpoint
- The status polling switches to the new track's status key
- The backend transcodes on-demand (first request triggers transcode, subsequent requests serve cached file)
- Progress overlay shows during the ~10-30s transcode

## What Does NOT Change

- `needs_transcoding()` function
- `get_or_transcode_preview()` (still used for `'full'` mode)
- `get_track_preview()` / `get_audio_track_preview()` (still used for `'full'` mode track extraction from master — distinct from `transcode_audio_track_from_source` which works directly on the source file)
- MediaPlayer component (already supports dual-mode `<video>` + `<audio>` playback)
- Cut/export functionality (unrelated to preview mode)
- `hwaccel.py` module
- Job cleanup (`delete_job`, TTL pruning) — audio-only cache files are in the job directory and get cleaned up automatically
