# Audio Master Extraction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Speed up audio-only transcoding by extracting all audio streams into a cached intermediate file first, then transcoding individual tracks from that small file instead of the 10GB+ source.

**Architecture:** Add `get_or_create_audio_master()` that stream-copies all audio into a cached MKA file. Modify `transcode_audio_track_from_source()` to use the audio master as input instead of the source file. Progress reporting spans both phases (extraction 0-90%, transcode 90-100%).

**Tech Stack:** Python 3.12, ffmpeg, threading

**Spec:** `docs/superpowers/specs/2026-03-21-audio-master-extraction-design.md`

---

### Task 1: Add `get_or_create_audio_master()` function

**Files:**
- Modify: `backend/app/cutter.py` (insert new function before `transcode_audio_track_from_source` at line 1255)

- [ ] **Step 1: Add the `get_or_create_audio_master()` function**

Insert the following function before `transcode_audio_track_from_source()` (before line 1255) in `backend/app/cutter.py`:

```python
def get_or_create_audio_master(
    filepath: str,
    job_id: str,
    cancel_event: threading.Event,
    status_key: str,
    start_ts: float,
    duration: float,
) -> tuple[str, bool]:
    """Extract all audio streams from source into a cached MKA file.

    Uses stream copy (no decode/encode) — pure I/O, much faster than
    transcoding.  The result is cached per source file in the job directory.
    Callers should NOT call ``_begin_job_operation`` — this function uses
    the caller's existing ``cancel_event``.

    Returns ``(audio_master_path, was_extracted)`` where *was_extracted*
    is ``True`` when the file was freshly created and ``False`` on cache hit.
    """
    suffix = _preview_cache_key(filepath)
    job_dir = os.path.join(CUTTER_JOBS_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    audio_master_path = os.path.join(
        job_dir, f"preview_{suffix}_audio_master.mka"
    )

    if os.path.isfile(audio_master_path):
        return audio_master_path, False

    with _get_preview_build_lock(audio_master_path):
        if os.path.isfile(audio_master_path):
            return audio_master_path, False
        if cancel_event.is_set():
            raise RuntimeError(
                f"Audio master extraction cancelled for job {job_id}"
            )

        tmp_path = f"{audio_master_path}.{uuid.uuid4().hex}.tmp.mka"
        cmd = [
            "ffmpeg", "-nostdin", "-loglevel", "warning", "-stats", "-y",
            "-i", filepath,
            "-map", "0:a",
            "-vn",
            "-c", "copy",
            "-f", "matroska",
            tmp_path,
        ]

        _set_preview_status(
            status_key,
            {
                "state": "running",
                "ready": False,
                "percent": 0.0,
                "eta_seconds": None,
                "elapsed_seconds": 0.0,
                "message": "Extracting audio streams...",
                "updated_at": time.time(),
            },
        )

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
        )
        _register_job_process(job_id, proc)

        stderr_lines: list[str] = []

        try:
            while True:
                if cancel_event.is_set() and proc.poll() is None:
                    proc.terminate()
                line = proc.stderr.readline() if proc.stderr else ""
                if not line:
                    if proc.poll() is not None:
                        break
                    continue
                stderr_lines.append(line.rstrip())
                out_seconds = _seconds_from_ffmpeg_time(line)
                if out_seconds is None or duration <= 0:
                    continue
                elapsed = max(0.001, time.monotonic() - start_ts)
                speed = out_seconds / elapsed
                progress_ratio = max(0.0, min(1.0, out_seconds / duration))
                # Extraction phase: 0% → 90%
                percent = max(0.0, min(89.9, progress_ratio * 90.0))
                remaining = max(0.0, duration - out_seconds)
                eta_seconds = remaining / speed if speed > 0 else None

                _set_preview_status(
                    status_key,
                    {
                        "state": "running",
                        "ready": False,
                        "percent": percent,
                        "eta_seconds": eta_seconds,
                        "elapsed_seconds": elapsed,
                        "message": "Extracting audio streams...",
                        "updated_at": time.time(),
                    },
                )
            if proc.stderr:
                remaining_stderr = proc.stderr.read()
                if remaining_stderr:
                    stderr_lines.append(remaining_stderr.rstrip())
            proc.wait(timeout=300)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            _safe_remove_file(tmp_path)
            message = f"Audio extraction timed out: {exc}"
            _set_preview_status(
                status_key,
                {
                    "state": "error",
                    "ready": False,
                    "percent": 0.0,
                    "eta_seconds": None,
                    "elapsed_seconds": time.monotonic() - start_ts,
                    "message": message,
                    "updated_at": time.time(),
                },
            )
            raise RuntimeError(message) from exc
        finally:
            _unregister_job_process(job_id, proc)
            _close_pipe(proc.stdout)
            _close_pipe(proc.stderr)

        if cancel_event.is_set():
            _safe_remove_file(tmp_path)
            message = f"Audio extraction cancelled because job {job_id} is being deleted"
            _set_preview_status(
                status_key,
                {
                    "state": "error",
                    "ready": False,
                    "percent": 0.0,
                    "eta_seconds": None,
                    "elapsed_seconds": time.monotonic() - start_ts,
                    "message": message,
                    "updated_at": time.time(),
                },
            )
            raise RuntimeError(message)

        if proc.returncode != 0:
            _safe_remove_file(tmp_path)
            detail = _compact_process_error("\n".join(stderr_lines), "")
            message = f"Audio extraction failed (exit {proc.returncode}): {detail}"
            logger.error(
                "Audio extraction failed (exit %d): %s\nCommand: %s",
                proc.returncode,
                detail,
                subprocess.list2cmdline(cmd),
            )
            _set_preview_status(
                status_key,
                {
                    "state": "error",
                    "ready": False,
                    "percent": 0.0,
                    "eta_seconds": None,
                    "elapsed_seconds": time.monotonic() - start_ts,
                    "message": message,
                    "updated_at": time.time(),
                },
            )
            raise RuntimeError(message)

        for attempt in range(5):
            try:
                os.replace(tmp_path, audio_master_path)
                break
            except PermissionError as exc:
                if os.path.isfile(audio_master_path):
                    _safe_remove_file(tmp_path)
                    break
                if attempt == 4:
                    _safe_remove_file(tmp_path)
                    message = f"Audio master finalize failed for {audio_master_path}: {exc}"
                    _set_preview_status(
                        status_key,
                        {
                            "state": "error",
                            "ready": False,
                            "percent": 0.0,
                            "eta_seconds": None,
                            "elapsed_seconds": time.monotonic() - start_ts,
                            "message": message,
                            "updated_at": time.time(),
                        },
                    )
                    raise RuntimeError(message) from exc
                time.sleep(0.1 * (attempt + 1))

        return audio_master_path, True
```

- [ ] **Step 2: Run tests**

Run: `cd e:/Repos/Jellyfin_Media-Renamer/backend && python -m pytest tests/ -v`
Expected: All tests pass (new function has no callers yet).

- [ ] **Step 3: Commit**

```bash
git add backend/app/cutter.py
git commit -m "feat: add get_or_create_audio_master() for cached audio extraction"
```

---

### Task 2: Modify `transcode_audio_track_from_source()` to use audio master

**Files:**
- Modify: `backend/app/cutter.py:1255-1510` (the `transcode_audio_track_from_source` function — line numbers will have shifted after Task 1)

- [ ] **Step 1: Add audio master extraction call and move start_ts earlier**

In `transcode_audio_track_from_source()`, find the block from the lock acquisition through the ffmpeg command construction and the `start_ts` / initial status message (currently lines 1304-1337). We need to:
1. Move `start_ts` before the `get_or_create_audio_master()` call (it needs the timestamp)
2. Insert the audio master extraction call
3. Make the "Starting audio transcode..." status conditional to avoid regressing progress from ~90% back to 0%

Find this block inside `transcode_audio_track_from_source()`:

```python
        with _get_preview_build_lock(audio_path):
            if os.path.isfile(audio_path):
                return audio_path
            if cancel_event.is_set():
                raise RuntimeError(
                    f"Audio transcode cancelled for job {job_id}"
                )

            cmd = [
                "ffmpeg", "-nostdin", "-loglevel", "warning", "-stats", "-y",
                "-i", filepath,
                "-map", f"0:a:{rel}",
                "-vn",
                "-c:a", "aac", "-b:a", "192k",
            ]
            if channels > 6:
                cmd += ["-ac", "2"]

            tmp_path = f"{audio_path}.{uuid.uuid4().hex}.tmp"
            cmd += ["-f", "mp4", "-movflags", "+faststart", tmp_path]

            start_ts = time.monotonic()
            _set_preview_status(
                status_key,
                {
                    "state": "running",
                    "ready": False,
                    "percent": 0.0,
                    "eta_seconds": None,
                    "elapsed_seconds": 0.0,
                    "message": "Starting audio transcode...",
                    "updated_at": time.time(),
                },
            )
```

Replace with:

```python
        with _get_preview_build_lock(audio_path):
            if os.path.isfile(audio_path):
                return audio_path
            if cancel_event.is_set():
                raise RuntimeError(
                    f"Audio transcode cancelled for job {job_id}"
                )

            # Assign start_ts BEFORE extraction so progress tracking works.
            start_ts = time.monotonic()

            # Step 1: Extract all audio streams into a cached intermediate file.
            # This reads the full source once (stream copy, no decode) and
            # produces a small MKA with all audio tracks.
            audio_master, extraction_needed = get_or_create_audio_master(
                filepath, job_id, cancel_event, status_key, start_ts, duration,
            )

            cmd = [
                "ffmpeg", "-nostdin", "-loglevel", "warning", "-stats", "-y",
                "-i", audio_master,
                "-map", f"0:a:{rel}",
                "-vn",
                "-c:a", "aac", "-b:a", "192k",
            ]
            if channels > 6:
                cmd += ["-ac", "2"]

            tmp_path = f"{audio_path}.{uuid.uuid4().hex}.tmp"
            cmd += ["-f", "mp4", "-movflags", "+faststart", tmp_path]

            # After extraction, set status for transcode phase.
            # When extraction ran, progress continues from 90%; on cache hit, starts at 0%.
            _set_preview_status(
                status_key,
                {
                    "state": "running",
                    "ready": False,
                    "percent": 90.0 if extraction_needed else 0.0,
                    "eta_seconds": None,
                    "elapsed_seconds": time.monotonic() - start_ts,
                    "message": "Transcoding audio track...",
                    "updated_at": time.time(),
                },
            )
```

Key changes vs. original:
- `start_ts` moved before `get_or_create_audio_master()` (was after the ffmpeg command, causing use-before-assignment)
- `extraction_needed` is a boolean returned by `get_or_create_audio_master()` (not a fragile time-based heuristic)
- Initial percent is `90.0` when extraction ran (continuing from extraction phase), `0.0` on cache hit (no regression)

- [ ] **Step 2: Update progress reporting to scale correctly**

Find the progress calculation block in the ffmpeg stderr reading loop (the section that computes `percent`, `eta_seconds`, and `message`):

```python
                    if progress_ratio >= 0.995:
                        eta_seconds = None
                        percent = 99.0
                        message = "Finalizing audio file"
                    else:
                        remaining = max(0.0, duration - out_seconds)
                        eta_seconds = remaining / speed if speed > 0 else None
                        percent = max(0.0, min(98.9, progress_ratio * 100.0))
                        message = "Transcoding audio"
```

Replace with:

```python
                    # When extraction was needed, transcode phase is 90-100%.
                    # On cache hit, transcode phase is 0-100%.
                    if extraction_needed:
                        pct_lo, pct_hi = 90.0, 100.0
                    else:
                        pct_lo, pct_hi = 0.0, 100.0

                    if progress_ratio >= 0.995:
                        eta_seconds = None
                        percent = pct_hi - 0.1
                        message = "Finalizing audio file"
                    else:
                        remaining = max(0.0, duration - out_seconds)
                        eta_seconds = remaining / speed if speed > 0 else None
                        percent = max(pct_lo, min(pct_hi - 0.1, pct_lo + progress_ratio * (pct_hi - pct_lo)))
                        message = "Transcoding audio"
```

- [ ] **Step 3: Run tests**

Run: `cd e:/Repos/Jellyfin_Media-Renamer/backend && python -m pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/cutter.py
git commit -m "feat: use audio master as input for per-track audio transcode"
```
