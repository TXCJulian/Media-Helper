# Audio Master Extraction — Design Spec

**Date:** 2026-03-21
**Branch:** `feat/hw_acceleration`

## Problem

Audio-only transcoding from large source files (10GB+) takes ~13 minutes per track because ffmpeg must sequentially demux the entire container to find interleaved audio packets. Each per-track transcode re-reads the full source. For files with multiple audio tracks, this multiplies the I/O cost.

## Solution

Introduce a two-step audio transcode pipeline with a cached intermediate file:

1. **Extract:** Stream-copy all audio streams from the source into a small intermediate MKV file (~200MB-2GB). Pure I/O, no decode/encode. Happens once per source file (lazy, on first audio transcode request).
2. **Transcode:** Encode the requested track from the small intermediate file to AAC MP4. Reads ~200MB instead of 10GB+ — completes in under a minute.

## New Function: `get_or_create_audio_master()`

**File:** `backend/app/cutter.py`

**Purpose:** Extract all audio streams from source into a cached intermediate file.

**ffmpeg command:**
```bash
ffmpeg -nostdin -loglevel warning -stats -y \
  -i source.mkv \
  -map 0:a \
  -vn \
  -c copy \
  -f matroska \
  audio_master.mka
```

**Container choice:** MKV (Matroska) — supports all audio codecs without transcoding constraints, no moov atom / faststart issues.

**Cache path:** `{job_dir}/preview_{suffix}_audio_master.mka`

**Atomic write pattern:** Write to a temporary file (`{audio_master_path}.{uuid}.tmp.mka`), then `os.replace()` to the final path on success. On failure/cancellation, clean up the temp file. This prevents corrupt cached files from interrupted extractions.

**Locking:** Uses the existing `_get_preview_build_lock(audio_master_path)` pattern to prevent duplicate extractions when multiple track requests arrive concurrently.

**Cancellation:** Receives and checks the caller's existing `cancel_event` but does NOT call `_begin_job_operation` / `_end_job_operation` — the caller (`transcode_audio_track_from_source`) already owns that registration.

**Parameters:**
- `filepath: str` — source file path
- `job_id: str` — job identifier
- `cancel_event: threading.Event` — shared cancellation event from caller
- `status_key: str` — preview status key for progress reporting
- `start_ts: float` — monotonic timestamp for elapsed time tracking
- `duration: float` — source file duration for progress calculation

**Returns:** `tuple[str, bool]` — `(audio_master_path, was_extracted)` where `was_extracted` is `True` when the file was freshly created and `False` on cache hit.

**Progress reporting:** Reports extraction progress via `_set_preview_status()` during the I/O phase:
- Parses ffmpeg stderr for time progress (same `_seconds_from_ffmpeg_time` helper used elsewhere)
- State: `"running"`, message: `"Extracting audio streams..."`
- Progress percentage: 0% → 90% (extraction is ~90% of total wall time)
- When audio master already exists (cache hit), skip to transcode phase at 0%

## Modified: `transcode_audio_track_from_source()`

**Current behavior:** Reads the entire source file, decodes audio, encodes to AAC.

**New behavior:**
1. Call `get_or_create_audio_master()` to get/create the intermediate file
2. Transcode the requested track from the audio master: `ffmpeg -i audio_master.mka -map 0:a:{rel} -c:a aac -b:a 192k output.mp4`

**Progress scaling:**
- When extraction was needed: extraction 0% → 90%, transcode 90% → 100%
- When audio master already cached: transcode 0% → 100% (full range)

**Stream index mapping:** Compute `rel = _audio_relative_index(source_audio_streams, audio_stream_index)` once using the **source** probe data. This relative index IS the correct `-map 0:a:{rel}` index for the audio master, because the audio master preserves all source audio streams in their original order. Do NOT probe the audio master or attempt to look up absolute indices — the source-derived relative index maps directly.

**Channel count / metadata:** Stream metadata (channel count for the `channels > 6` downmix check) must still come from the **source** probe data, which is unchanged since stream copy preserves metadata.

## Caching Behavior

- Audio master file is created lazily on first audio transcode request
- Subsequent track transcodes for the same source file skip extraction entirely (file exists check)
- Audio master is cleaned up with the job (lives in `{job_dir}/`)
- No separate TTL — inherits job TTL
- Audio master files contribute to job disk usage but are small relative to video previews

## Files Changed

| File | Change |
|------|--------|
| `backend/app/cutter.py` | Add `get_or_create_audio_master()`, modify `transcode_audio_track_from_source()` |

## Performance Impact

**Before (per track):** Read 10GB+ source → decode DTS → encode AAC → ~13 min
**After (first track):** Read 10GB+ source → copy audio (~5 min I/O) → read 200MB → encode AAC (~30s) → ~6 min
**After (subsequent tracks):** Read 200MB → encode AAC → ~30s

For multi-track scenarios, improvement is dramatic: 3 tracks goes from ~39 min to ~7 min.

## Testing

- Existing backend tests should pass (no API changes)
- Manual verification with large files on Docker volume mounts
