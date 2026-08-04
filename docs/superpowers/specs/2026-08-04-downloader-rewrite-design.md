# Downloader Rewrite — Design

**Date:** 2026-08-04
**Status:** Approved, ready for implementation planning
**Supersedes:** the `feature/downloader` branch (`app/download.py`, `frontend/src/components/DownloaderPanel.tsx`)

## Why rewrite

The existing downloader is not failing on details. Three structural decisions are wrong, and each one is load-bearing:

1. **Jobs are owned by the HTTP request.** The worker thread is spawned inside the SSE handler, so a closed tab or reload orphans a running download with no way to reattach.
2. **There is no queue.** `Semaphore(5)` is acquired with `blocking=False`, so the sixth concurrent download returns HTTP 429 instead of waiting. Bulk paste fires every URL at once and reliably fails most of them.
3. **The schema assumes one job = one file.** `filename` / `output_path` are scalars, but playlists and chapter splitting produce N files. `_extract_final_path` takes `requested_downloads[0]`, so multi-output jobs report one arbitrary file.

Everything downstream — progress reporting, cancellation, history — inherits these. Patching them individually means rewriting ~80% of the module against code whose assumptions contradict the fix. A clean rebuild is less work and leaves a correct foundation.

Secondary defects the rewrite also resolves:

- `_save_meta` is a read-modify-write over a JSON file; the lock guards the I/O, not the transaction. `delete_job` writing "Cancelled by user" is silently overwritten by the still-running hook thread.
- Cancellation is only checked inside yt-dlp hooks, so during an ffmpeg re-encode nothing fires, the 15s deadline expires, and the user gets a 409.
- Cancel and delete are the same operation; cancelled jobs land in `error` state.
- Selecting a codec silently triggers a full transcode with no progress feedback.
- Audio exposes two overlapping dropdowns (Codec and Format) for one decision, with silent backend precedence.
- `overwrites: True` means two videos with the same title clobber each other.
- The frontend runs a 5s poll *and* per-job SSE into the same `mergeJob` reducer, so stale poll data overwrites fresher stream data.

## Scope

**In:** single-URL downloads, bulk multi-URL downloads, video/audio/thumbnail, quality and container selection, optional re-encode to a chosen codec, output into a selected media library directory, cookies, cancel, history.

**Out (deliberately):** chapter splitting (`split_chapters` is dropped; the multi-item schema means it can be added later without a data migration). Elaborate playlist management UI — a pasted playlist URL is handled correctly and reported honestly, but is not a primary flow.

**Primary flows:** one URL pasted into the main field; and a multi-line list of URLs added at once.

## Architecture

New code lives in `backend/app/downloader/` as a package rather than one 750-line module:

| Module | Responsibility |
| --- | --- |
| `store.py` | SQLite-backed job + item persistence. The only writer of state. |
| `queue.py` | Worker pool, job lifecycle, cancellation. |
| `ydl.py` | yt-dlp option building and the download stage. |
| `transcode.py` | The optional ffmpeg re-encode stage. |
| `routes.py` | FastAPI endpoints. |

`app/download.py` is deleted.

### Job ownership

A worker pool started at application lifespan drains a persistent queue. `POST /download` creates and enqueues the job, then returns immediately. **No work is ever started inside a request handler.** Closing the tab, reloading, or restarting the backend cannot orphan a job; queued jobs resume after a restart.

Concurrency is a configurable cap (`DOWNLOADER_WORKERS`, default 3). Jobs beyond the cap **wait in the queue** — they are never rejected. This is the single most important behavioural change.

### Stages

```text
queued → downloading → [transcoding] → done
                     ↘ cancelled
                     ↘ error
```

`transcoding` exists only when a codec re-encode was requested. It is a distinct stage with its own progress percentage, so a long re-encode is visible rather than presenting as a frozen 100% bar.

### Data model

```text
job
  id            uuid
  url           text
  options       json
  stage         queued|downloading|transcoding|done|cancelled|error
  error         text|null
  created_at    timestamp
  updated_at    timestamp

item  (FK job_id, ordered by index)
  index         int
  title         text
  path          text|null
  size          int|null
  progress      float
  stage         same enum
  error         text|null
```

A single video produces one item; a playlist produces N. The UI collapses the single-item case so the common flow is unchanged, and expands multi-item jobs into per-item rows.

**State lives in SQLite** (`/data/downloader.db`, path configurable). Status updates become single `UPDATE` statements, which eliminates the read-modify-write race outright. No directory of JSON blobs, no global metadata lock.

### Cancellation

`POST /download/jobs/{id}/cancel` is its own operation. It sets a cancel event checked by the yt-dlp progress hook *and* kills the ffmpeg child process if the job is in the transcoding stage — reusing the process-registration and `_stop_process` pattern already proven in `app/cutter.py`. The job lands in `cancelled`, and partial output files are removed.

`DELETE /download/jobs/{id}` removes the record. If the job is active it cancels first, then removes. There is no fixed wait-and-hope deadline.

### Transcoding

The re-encode is a **second stage**, not a yt-dlp postprocessor. yt-dlp downloads and merges into the requested container; if a codec was requested, `transcode.py` then runs ffmpeg directly.

This reuses existing house machinery rather than inventing a parallel path:

- `app/hwaccel.py` — `build_video_encode_args`, `get_hwaccel_input_args`
- `app/cutter.py` conventions — `_seconds_from_ffmpeg_time` for parsing `time=` progress against the known duration, `_CODEC_TO_ENCODER`, `_CONTAINER_TO_FFMPEG_FORMAT`, `_compact_process_error`

Progress is real (elapsed encode time ÷ duration), and the process is killable. Shared helpers that both cutter and downloader need are lifted into a common module rather than duplicated.

## API

```text
GET    /download/status                        yt-dlp version, cookies present, dirs, queue depth
POST   /download                               create job(s); accepts 1..N urls → { job_ids: [...] }
GET    /download/jobs                          list, newest first, with items
GET    /download/jobs/{id}                     single job + items
GET    /download/events                        SSE — all jobs, single stream for the panel
GET    /download/jobs/{id}/events              SSE — single job
POST   /download/jobs/{id}/start               enqueue a job created with auto_start=false
POST   /download/jobs/{id}/cancel              cancel running or queued job
DELETE /download/jobs/{id}                     remove record (cancels first if active)
GET    /download/jobs/{id}/items/{n}/file      serve one output file
POST   /download/cookies                       unchanged from current implementation
DELETE /download/cookies                       unchanged from current implementation
```

Two changes carry weight:

- **`POST /download` accepts a list.** Bulk is one request, not N racing ones.
- **The event stream is a `GET` you can reconnect to.** It does not start work. On connect it emits current state for all jobs, then deltas. Reloading mid-download resumes the display exactly where it is.

All endpoints stay behind `require_feature("download")`.

### Options

| Option | Meaning |
| --- | --- |
| `type` | video / audio / thumbnail |
| `quality` | best, 2160p…480p, worst (video); best, 320…96kbps, worst (audio) |
| `format` | **container** only — mp4/mkv/webm/mov, m4a/opus/flac/wav, jpg/png/webp |
| `codec` | **re-encode target**, optional; empty means no re-encode |
| `base` + `output_dir` | media library destination |
| `sub_folder` | relative subdirectory |
| `custom_prefix`, `custom_filename` | naming |
| `item_limit` | playlist cap |
| `auto_start` | enqueue immediately vs. create in `queued` for manual start |

`codec` and `format` are now orthogonal: format is the container, codec is the optional re-encode. For audio the two collapse into a single control — the current duplicate MP3/FLAC/AAC in both dropdowns is gone. `codec` does not apply to `type=thumbnail` and is ignored there; the UI hides the control for that type.

**Name collisions get a numeric suffix.** `overwrites: True` is removed; an existing file is never silently destroyed.

## Errors

Every failure attaches to the job or the specific item with an ANSI-stripped message. Cases that must be handled explicitly rather than swallowed:

- extractor failure / unsupported URL
- geo-blocked, age-gated, or login-required → surfaced as "needs cookies", not a raw traceback
- no format matching the requested quality
- ffmpeg/ffprobe missing from PATH
- disk full or destination not writable
- output path escaping the allowed roots (`DOWNLOADS_DIR` + `BASE_PATHS`)
- cancelled mid-stage → `cancelled`, never `error`

Path validation keeps the current `realpath`-based containment check, which is sound.

## Frontend

`DownloaderPanel.tsx` is rebuilt, not ported.

**State layer replaced.** `mergeJob`, `parseDownloadEvent`, the 5s poll, and the `sseAbortRef` set are all deleted, replaced by one `useDownloadStream()` hook holding a single reconnecting `EventSource` on `/download/events` with backoff. One writer, one source of truth. Most of the current file's complexity exists to reconcile two competing sources.

**Cards show stages.** Each job renders a stage strip — Downloading → Transcoding → Done — with the active stage's own bar. Multi-item jobs expand into per-item rows.

**Cancel is a distinct button** beside Delete, not the same action relabelled.

**Bulk is promoted out of the modal.** The URL field accepts multi-line paste, detects the count ("3 URLs detected"), and submits in one request. The bulk modal is removed.

**Options reorganised.** Container and quality stay in the quick row. "Re-encode to codec" moves into Advanced as its own labelled group carrying a slow-operation warning, so it is opt-in rather than something a user trips over.

**Reused unchanged:** `PanelLayout`, `StyledSelect`, `ToggleSwitch`, `DirectorySelect`, the glass card styling, the status footer.

### Colour

The feature moves off amber `#f59e0b`. New accent is **cyan `#22d3ee`**, added as `--accent-6` / `--accent-6-glow` with `.btn-cyan` and `.input-cyan` classes following the existing pattern in `index.css`. Cyan stays distinct from the blue `#3b82f6` and emerald `#34d399` it sits between, and holds contrast on the dark glass background.

`--accent-5` (amber) is freed for future use. The downloader's entry in `Landing.tsx` updates to the new accent.

## Testing

`backend/tests/test_download.py` is rebuilt against the new model. yt-dlp and ffmpeg are mocked — no network, no encoding in tests.

- queue ordering and the concurrency cap (job N+1 waits, never 429s)
- cancel from each stage: queued, downloading, transcoding
- restart recovery — queued jobs resume, in-flight jobs are marked appropriately
- a playlist URL producing N items with independent progress
- path traversal rejection for `sub_folder`, `custom_filename`, `custom_prefix`
- name-collision suffixing instead of overwrite
- concurrent status updates without lost writes
- error mapping for the cases listed above

Frontend tests cover the stream reducer: reconnect replays state without duplicating jobs, and stage transitions render correctly.

## Migration

No data migration. Existing download jobs are disposable; the old `/data/download-jobs` directory can be deleted. `feature/downloader` is dropped once this spec is committed.

## Config

| Variable | Default | Purpose |
| --- | --- | --- |
| `DOWNLOADS_DIR` | `/downloads` | fallback output root |
| `DOWNLOADER_DB` | `/data/downloader.db` | job state |
| `DOWNLOADER_WORKERS` | `3` | concurrency cap |
| `DOWNLOADER_JOB_TTL` | `604800` | history retention |
| `DOWNLOADER_DATA_DIR` | `/data/downloader` | cookie file and scratch space |
| `YT_DLP_COOKIES` | `""` | cookie file path override; defaults to `DOWNLOADER_DATA_DIR/cookies.txt` |

`DOWNLOADER_JOBS_DIR` is retired — job state moves to SQLite, and the cookie file (previously stored alongside job directories) moves to `DOWNLOADER_DATA_DIR`.
