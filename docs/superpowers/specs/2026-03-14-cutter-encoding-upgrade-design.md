# Cutter Encoding Upgrade — Design Spec

## Problem

The cutter's re-encoding settings are basic: a single global audio codec, single audio track selection, no quality preservation, and no preselection from the source file. Users need per-track audio control, source quality matching, and intelligent defaults.

## Goals

1. **Keep Source Quality** — toggle to match source bitrate when re-encoding (approximate — see Limitations)
2. **Per-track audio control** — passthru, re-encode (with codec choice), or remove each audio track independently. Available in both stream copy and re-encode modes.
3. **Preselect from source** — video codec and container auto-set from source file metadata. Audio tracks default to passthru.
4. **Quality loss awareness** — inform users that re-encoding causes generation loss

## Non-Goals

- Manual bitrate/CRF input (future work)
- Video track selection (single video stream assumed)
- Subtitle track management

## Limitations

- "Keep Source Quality" uses `-b:v`/`-b:a` to match the source bitrate. This is approximate — the same bitrate with a different codec or encoder preset can produce different perceptual quality. The UI notes this.
- When probe returns `bit_rate=0` (unknown), the bitrate flag is omitted for that stream (encoder defaults apply).

---

## Data Model

### New Type: `AudioTrackConfig`

```ts
// Frontend — serialized to JSON with snake_case keys for the backend
interface AudioTrackConfig {
  streamIndex: number          // ffprobe absolute stream index
  mode: 'passthru' | 'reencode' | 'remove'
  codec: string                // used when mode === 'reencode', e.g. 'aac'
}
```

Frontend serializes to JSON as `{ "index": streamIndex, "mode": ..., "codec": ... }` (snake_case `index` to match backend expectations). The `streamIndex` field name is frontend-only for camelCase consistency.

```python
# Backend (dict, not a dataclass — matches existing patterns)
{
  "index": int,      # ffprobe absolute stream index
  "mode": str,       # "passthru" | "reencode" | "remove"
  "codec": str|None  # encoder name when mode == "reencode"
}
```

### Modified Types

**`ProbeResult`** (frontend) / `probe_file` return (backend):
- Each audio stream gains `bitrate: number` (from ffprobe per-stream `bit_rate`)
- Top-level gains `video_bitrate: number | null` (from video stream `bit_rate`)

**`CutterForm`** (frontend):
- Remove: `audioCodec`, `audioStreamIndex`
- Add: `audioTracks: AudioTrackConfig[]`, `keepQuality: boolean`

**`CutJobSettings`** (frontend + backend metadata):
- Remove: `audio_codec`, `audio_stream_index`
- Add: `audio_tracks: {index, mode, codec}[]`, `keep_quality: boolean`

---

## Backend

### `cutter.py` — `probe_file`

Add to each audio stream dict:
```python
"bit_rate": int(s.get("bit_rate", 0))
```

Add to top-level info dict:
```python
"video_bitrate": int(video_stream.get("bit_rate", 0)) if video_stream else None
```

### `cutter.py` — `_audio_relative_index`

Refactor to accept an `audio_streams` list parameter instead of calling `probe_file` internally. This avoids N+1 probe calls when mapping multiple tracks. The caller (either `cut_file` or the endpoint) probes once and passes the stream list through.

```python
def _audio_relative_index(audio_streams: list[dict], absolute_index: int) -> int:
```

### `cutter.py` — `cut_file`

**Signature change:**
```python
def cut_file(
    filepath: str,
    in_point: float,
    out_point: float,
    output_path: str,
    stream_copy: bool,
    codec: Optional[str],
    audio_tracks: list[dict] | None,   # replaces audio_codec + audio_stream_index
    container: Optional[str],
    progress_cb: Callable[[str], None],
    keep_quality: bool = False,
    source_video_bitrate: int | None = None,
    source_audio_bitrates: dict[int, int] | None = None,
    audio_streams: list[dict] | None = None,  # from probe, for _audio_relative_index
    job_id: str | None = None,
    cancel_event: Optional[threading.Event] = None,
) -> str:
```

**Stream copy + per-track interaction:**

When `stream_copy=True`, the global `-c copy` is **not** emitted. Instead:
- Video: `-c:v copy`
- Each non-removed audio track gets `-c:a:{out_idx} copy` (passthru) or `-c:a:{out_idx} {encoder}` (reencode)
- This allows stream copy for video while selectively re-encoding audio tracks
- The frontend allows all three audio modes (passthru/reencode/remove) in stream copy mode
- "Stream Copy" label in the UI refers primarily to the video stream; the encoding toggle label updates to reflect this: "Stream Copy (video)" when any audio track is set to reencode

**Audio-only files:**

When `isVideo=False` (no video stream):
- `-map 0:v?` still included (harmless — the `?` means "if present")
- `codec` parameter is ignored (no video to encode)
- Audio encoding is entirely driven by `audio_tracks` per-track settings
- Each track's mode/codec applies as normal

**Mapping logic:**
1. Always: `-map 0:v?` (map video if present)
2. For each track in `audio_tracks` where `mode != "remove"`:
   - Compute relative audio index via `_audio_relative_index(audio_streams, track["index"])`
   - Add `-map 0:a:{relative_index}`
3. Video codec:
   - If `stream_copy`: `-c:v copy`
   - Else if `codec`: `-c:v {encoder}` (+ `-b:v {source_video_bitrate}` when `keep_quality` and bitrate > 0)
4. Per-stream audio codec using output audio index (0, 1, 2...):
   - `passthru` → `-c:a:{out_idx} copy`
   - `reencode` → `-c:a:{out_idx} {encoder_name}`
     - If `keep_quality` and source bitrate > 0 for that stream: add `-b:a:{out_idx} {bitrate}`
5. If `audio_tracks` is `None`, fall back to current behavior for backwards compatibility

**FLAC override:**

The existing FLAC stream-copy override (forces re-encode for FLAC to avoid stale duration metadata) is updated: when triggered, it sets all `passthru` audio tracks to `reencode` with codec `"flac"` instead of setting the global `codec` parameter.

**Example ffmpeg command** (3-track file: track 0 passthru, track 1 reencode AAC, track 2 removed):
```
ffmpeg -ss 10 -t 30 -i input.mkv \
  -map 0:v? -map 0:a:0 -map 0:a:1 \
  -c:v copy \
  -c:a:0 copy \
  -c:a:1 aac -b:a:1 192000 \
  -f matroska -y output.mkv
```

### `main.py` — `/cutter/cut` endpoint

**New form parameters:**
- `audio_tracks: str = Form("[]")` — JSON-encoded list of `{index, mode, codec}` dicts
- `keep_quality: bool = Form(False)`

**Removed parameters:**
- `audio_codec: str` — now per-track
- `audio_stream: int | None` — now embedded in audio_tracks

**Validation:**
- Parse `audio_tracks` JSON, validate each entry has valid `mode` and `codec`
- Codec values must be in the audio codec allowlist
- All audio tracks may be removed (legitimate use case: silent video output)
- Reuse the existing probe call (already done for duration validation) to extract bitrates — do not probe twice

**Bitrate passthrough:**
- From the existing probe result, extract `video_bitrate` and per-track audio bitrates
- Build `source_audio_bitrates` dict: `{stream_index: bit_rate}` from probe audio streams
- Pass both to `cut_file`

### `main.py` — Job metadata

Update `cut_settings` stored in job metadata to use `audio_tracks` and `keep_quality` instead of `audio_codec` and `audio_stream_index`.

---

## Frontend

### `OutputSettings.tsx` — Full Rework

Props change:
```ts
interface OutputSettingsProps {
  outputName: string
  streamCopy: boolean
  codec: string
  container: string
  keepQuality: boolean
  audioTracks: AudioTrackConfig[]
  audioStreams: AudioStreamInfo[]    // from probe
  isVideo: boolean
  sourceVideoBitrate: number | null  // for display
  onOutputNameChange: (name: string) => void
  onStreamCopyChange: (value: boolean) => void
  onCodecChange: (codec: string) => void
  onContainerChange: (container: string) => void
  onKeepQualityChange: (value: boolean) => void
  onAudioTracksChange: (tracks: AudioTrackConfig[]) => void
}
```

**Sections rendered (top to bottom):**

1. **Output filename** — unchanged
2. **Encoding** — Stream Copy / Re-encode toggle (unchanged)
3. **Keep Source Quality** — toggle, only when re-encoding. Shows source bitrate label (e.g. "Source: 8.2 Mbps video"). Includes note: "Matches source bitrate — approximate, not lossless"
4. **Video Codec** — segmented control, only when re-encoding + isVideo. Preselected from source.
5. **Container** — segmented control, **always visible** (both modes). Preselected from source extension.
6. **Audio Tracks** — always visible when `audioStreams.length > 0`. Each row:
   - Label: `Track {n}: {CODEC} {channels}ch ({lang}) — {title}`
   - Mode dropdown: Passthru / Re-encode / Remove
   - Inline codec selector when mode is "reencode": AAC, AC3, FLAC, Opus, MP3

### `CutterPanel.tsx` — Preselection & State

**On probe completion:**
- Initialize `audioTracks`: one entry per `audio_stream`, all mode `"passthru"`, codec `"aac"`
- Map source `video_codec` to encoder:
  - `h264` → `libx264`
  - `hevc` / `h265` → `libx265`
  - `vp9` → `libvpx-vp9`
  - `av1` → `libaom-av1`
  - fallback → `libx264`
- Map source file extension to container: `.mkv` → `mkv`, `.mp4` → `mp4`, etc.
- Store `keepQuality: false` as initial state

**On cut submit:**
- Serialize `audioTracks` as JSON string with backend keys: `{ index, mode, codec }`
- Send `keep_quality` boolean
- Remove old `audio_codec` and `audio_stream` fields

### `AudioTrackSelect.tsx` — Deleted

Replaced entirely by the inline track list in `OutputSettings`. No other components import this file (only `CutterPanel` used it).

### `types.ts`

- Add `AudioTrackConfig` interface
- Add `bitrate` to `AudioStreamInfo`
- Add `video_bitrate` to `ProbeResult`
- Update `CutterForm`: remove `audioCodec`, `audioStreamIndex`; add `audioTracks`, `keepQuality`
- Update `CutJobSettings`: remove `audio_codec`, `audio_stream_index`; add `audio_tracks`, `keep_quality`

### `lib/api.ts`

Update the cut request function to send `audio_tracks` (JSON string) and `keep_quality` (boolean) instead of `audio_codec` and `audio_stream`.

---

## Tests

### Backend (`test_cutter.py`, `test_main.py`)
- Update `cut_file` call sites to use new `audio_tracks` parameter
- Test multi-track mapping: 3 tracks with passthru/reencode/remove produces correct `-map` and `-c:a:N` args
- Test `keep_quality` adds `-b:v` and `-b:a:N` flags, skips when bitrate is 0
- Test backwards compat: `audio_tracks=None` falls back to current behavior
- Test `/cutter/cut` endpoint with JSON `audio_tracks` param
- Test validation: invalid codec in audio_tracks rejected
- Test all-tracks-removed produces video-only output
- Test audio-only file with per-track settings

### Frontend (`mediaCompatibility.test.ts`)
- Existing tests unaffected (they test codec detection, not output settings)
- No new component tests required for this iteration (matching current coverage level)

---

## Migration / Backwards Compatibility

- Existing jobs with old `cut_settings` (containing `audio_codec` + `audio_stream_index`) continue to display in the job manager — they just show historical data
- The backend `cut_file` accepts `audio_tracks=None` and falls back to legacy single-track behavior
- No database migration needed — job metadata is file-based JSON
- `_audio_relative_index` signature change is internal — no external callers outside `cutter.py`
