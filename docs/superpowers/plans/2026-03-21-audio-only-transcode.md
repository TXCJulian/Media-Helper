# Audio-Only Transcode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an audio-only transcode preview mode that skips the slow video stream copy and only transcodes audio tracks on-demand from the source file.

**Architecture:** New `transcode_audio_track_from_source` function in cutter.py transcodes a single audio track from source to AAC MP4. Separate status key and background launcher avoid collisions with existing full-transcode pipeline. Frontend adds a three-state mode (`'off' | 'audio_only' | 'full'`) and uses existing dual-mode MediaPlayer (muted video + synced audio element).

**Tech Stack:** Python/FastAPI (backend), React/TypeScript (frontend), FFmpeg (transcoding)

**Spec:** `docs/superpowers/specs/2026-03-21-audio-only-transcode-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `backend/app/cutter.py` | Add `_audio_transcode_status_key`, `transcode_audio_track_from_source`, `start_background_audio_transcode`, `get_audio_transcode_status`, `wait_for_audio_transcode` |
| Modify | `backend/app/main.py` | Add `transcode_audio_only` param to stream endpoint, `audio_transcode_stream` param to preview-status endpoint |
| Modify | `frontend/src/lib/api.ts` | Add `getAudioOnlyTranscodeUrl`, update `fetchPreviewStatus` |
| Modify | `frontend/src/components/CutterPanel.tsx` | Replace `transcodePreviewEnabled` with `transcodeMode`, update button UI and stream URL logic |
| Create | `backend/tests/test_audio_only_transcode.py` | Tests for new backend functions |

---

## Task 1: Backend — Status Key and Transcode Function

**Files:**

- Create: `backend/tests/test_audio_only_transcode.py`
- Modify: `backend/app/cutter.py`

- [ ] **Step 1: Write tests for `_audio_transcode_status_key`**

In `backend/tests/test_audio_only_transcode.py`:

```python
"""Tests for audio-only transcode from source."""

from unittest.mock import patch, MagicMock
import subprocess
import os
import threading

import pytest


def _reload_cutter():
    import importlib
    import app.cutter as cutter_mod
    importlib.reload(cutter_mod)
    return cutter_mod


class TestAudioTranscodeStatusKey:

    def test_key_includes_stream_index(self):
        from app.cutter import _audio_transcode_status_key, _preview_cache_key
        key = _audio_transcode_status_key("/media/test.mkv", "job123", 2)
        hash_part = _preview_cache_key("/media/test.mkv")
        assert key == f"job123:{hash_part}:srcaudio2"

    def test_different_streams_produce_different_keys(self):
        from app.cutter import _audio_transcode_status_key
        k1 = _audio_transcode_status_key("/media/test.mkv", "job1", 1)
        k2 = _audio_transcode_status_key("/media/test.mkv", "job1", 3)
        assert k1 != k2

    def test_key_differs_from_preview_status_key(self):
        from app.cutter import _audio_transcode_status_key, _preview_status_key
        audio_key = _audio_transcode_status_key("/media/test.mkv", "job1", 1)
        preview_key = _preview_status_key("/media/test.mkv", "job1")
        assert audio_key != preview_key
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_audio_only_transcode.py::TestAudioTranscodeStatusKey -v`
Expected: ImportError — `_audio_transcode_status_key` does not exist yet

- [ ] **Step 3: Implement `_audio_transcode_status_key`**

In `backend/app/cutter.py`, after the `_preview_status_key` function (around line 492):

```python
def _audio_transcode_status_key(
    filepath: str, job_id: str, audio_stream_index: int
) -> str:
    """Status key for audio-only transcode — distinct from master preview key."""
    return f"{job_id}:{_preview_cache_key(filepath)}:srcaudio{audio_stream_index}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_audio_only_transcode.py::TestAudioTranscodeStatusKey -v`
Expected: All 3 PASS

- [ ] **Step 5: Write tests for `transcode_audio_track_from_source`**

Append to `backend/tests/test_audio_only_transcode.py`:

```python
class TestTranscodeAudioTrackFromSource:

    @patch("app.cutter.probe_file")
    @patch("app.cutter.subprocess.Popen")
    @patch("app.cutter.os.path.isfile", return_value=False)
    @patch("app.cutter.os.makedirs")
    @patch("app.cutter.os.replace")
    @patch("app.cutter._begin_job_operation")
    @patch("app.cutter._end_job_operation")
    @patch("app.cutter._register_job_process")
    @patch("app.cutter._unregister_job_process")
    def test_produces_cached_audio_file(
        self, mock_unreg, mock_reg, mock_end, mock_begin,
        mock_replace, mock_makedirs, mock_isfile, mock_popen, mock_probe,
    ):
        mock_probe.return_value = {
            "duration": 120.0,
            "audio_streams": [
                {"index": 1, "codec": "truehd", "channels": 8},
            ],
        }
        mock_proc = MagicMock()
        mock_proc.poll.side_effect = [None, 0]
        mock_proc.stderr.readline.side_effect = [
            "size=    100kB time=00:01:00.00 bitrate= 100.0kbits/s\n",
            "",
        ]
        mock_proc.stderr.read.return_value = ""
        mock_proc.stdout = MagicMock()
        mock_proc.stdout.read.return_value = ""
        mock_proc.returncode = 0
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc

        from app.cutter import transcode_audio_track_from_source
        result = transcode_audio_track_from_source("/media/test.mkv", 1, "job123")

        assert "srcaudio1" in result
        assert result.endswith(".mp4")

        # Verify FFmpeg was called with correct args
        call_args = mock_popen.call_args[0][0]
        assert "-vn" in call_args
        assert "-c:a" in call_args
        assert "aac" in call_args
        # Channels > 6 should trigger downmix
        assert "-ac" in call_args
        assert "2" in call_args

    @patch("app.cutter.probe_file")
    @patch("app.cutter.os.path.isfile", return_value=True)
    @patch("app.cutter._begin_job_operation")
    @patch("app.cutter._end_job_operation")
    def test_returns_cached_file_if_exists(
        self, mock_end, mock_begin, mock_isfile, mock_probe,
    ):
        mock_probe.return_value = {
            "duration": 60.0,
            "audio_streams": [{"index": 1, "codec": "truehd", "channels": 2}],
        }
        from app.cutter import transcode_audio_track_from_source
        result = transcode_audio_track_from_source("/media/test.mkv", 1, "job123")
        assert "srcaudio1" in result
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_audio_only_transcode.py::TestTranscodeAudioTrackFromSource -v`
Expected: ImportError — `transcode_audio_track_from_source` does not exist yet

- [ ] **Step 7: Implement `transcode_audio_track_from_source`**

In `backend/app/cutter.py`, after `get_audio_track_preview` (around line 1240). This follows the progress-parsing pattern from `get_or_transcode_preview`:

```python
def transcode_audio_track_from_source(
    filepath: str,
    audio_stream_index: int,
    job_id: str,
) -> str:
    """Transcode a single audio track from the source file to AAC MP4.

    Unlike ``get_audio_track_preview`` (which extracts from a master preview),
    this works directly on the source file — no master preview needed.
    Returns the path to the cached audio-only MP4.
    """
    cancel_event = threading.Event()
    _begin_job_operation(job_id, cancel_event)
    try:
        info = probe_file(filepath)
        duration = max(0.0, float(info.get("duration", 0.0) or 0.0))
        audio_streams = info.get("audio_streams", [])
        rel = _audio_relative_index(audio_streams, audio_stream_index)

        # Find channel count for this stream
        channels = 0
        for s in audio_streams:
            if s.get("index") == audio_stream_index:
                channels = s.get("channels", 0)
                break

        suffix = _preview_cache_key(filepath)
        job_dir = os.path.join(CUTTER_JOBS_DIR, job_id)
        os.makedirs(job_dir, exist_ok=True)
        audio_path = os.path.join(
            job_dir, f"preview_{suffix}_srcaudio{audio_stream_index}.mp4"
        )
        status_key = _audio_transcode_status_key(filepath, job_id, audio_stream_index)

        if os.path.isfile(audio_path):
            _set_preview_status(
                status_key,
                {
                    "state": "done",
                    "ready": True,
                    "percent": 100.0,
                    "eta_seconds": 0.0,
                    "elapsed_seconds": 0.0,
                    "message": "",
                    "updated_at": time.time(),
                },
            )
            return audio_path

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

                    if progress_ratio >= 0.995:
                        eta_seconds = None
                        percent = 99.0
                        message = "Finalizing audio file"
                    else:
                        remaining = max(0.0, duration - out_seconds)
                        eta_seconds = remaining / speed if speed > 0 else None
                        percent = max(0.0, min(98.9, progress_ratio * 100.0))
                        message = "Transcoding audio"

                    _set_preview_status(
                        status_key,
                        {
                            "state": "running",
                            "ready": False,
                            "percent": percent,
                            "eta_seconds": eta_seconds,
                            "elapsed_seconds": elapsed,
                            "message": message,
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
                _set_preview_status(
                    status_key,
                    {
                        "state": "error",
                        "ready": False,
                        "percent": 0.0,
                        "eta_seconds": None,
                        "elapsed_seconds": time.monotonic() - start_ts,
                        "message": f"Audio transcode timed out: {exc}",
                        "updated_at": time.time(),
                    },
                )
                _safe_remove_file(tmp_path)
                raise RuntimeError(f"Audio transcode timed out: {exc}") from exc
            finally:
                _unregister_job_process(job_id, proc)
                _close_pipe(proc.stdout)
                _close_pipe(proc.stderr)

            if cancel_event.is_set():
                _safe_remove_file(tmp_path)
                message = f"Audio transcode cancelled because job {job_id} is being deleted"
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
                logger.error(
                    "Audio transcode failed (exit %d): %s\nCommand: %s",
                    proc.returncode,
                    detail,
                    subprocess.list2cmdline(cmd),
                )
                message = f"Audio transcode failed (exit {proc.returncode}): {detail}"
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

            _set_preview_status(
                status_key,
                {
                    "state": "done",
                    "ready": True,
                    "percent": 100.0,
                    "eta_seconds": 0.0,
                    "elapsed_seconds": time.monotonic() - start_ts,
                    "message": "",
                    "updated_at": time.time(),
                },
            )

            for attempt in range(5):
                try:
                    os.replace(tmp_path, audio_path)
                    return audio_path
                except PermissionError as exc:
                    if os.path.isfile(audio_path):
                        _safe_remove_file(tmp_path)
                        return audio_path
                    if attempt == 4:
                        _safe_remove_file(tmp_path)
                        raise RuntimeError(
                            f"Audio transcode finalize failed for {audio_path}: {exc}"
                        ) from exc
                    time.sleep(0.1 * (attempt + 1))

        return audio_path
    finally:
        _end_job_operation(job_id, cancel_event)
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_audio_only_transcode.py -v`
Expected: All 5 PASS

- [ ] **Step 9: Commit**

```bash
git add backend/app/cutter.py backend/tests/test_audio_only_transcode.py
git commit -m "feat(cutter): add transcode_audio_track_from_source and status key"
```

---

## Task 2: Backend — Background Launcher and Status Query

**Files:**

- Modify: `backend/app/cutter.py`
- Modify: `backend/tests/test_audio_only_transcode.py`

- [ ] **Step 1: Write tests for `start_background_audio_transcode` and `get_audio_transcode_status`**

Append to `backend/tests/test_audio_only_transcode.py`:

```python
class TestStartBackgroundAudioTranscode:

    @patch("app.cutter.os.path.isfile", return_value=True)
    def test_skips_if_file_already_exists(self, mock_isfile):
        from app.cutter import start_background_audio_transcode
        # Should not raise or start a thread
        start_background_audio_transcode("/media/test.mkv", 1, "job123")

    @patch("app.cutter.transcode_audio_track_from_source")
    @patch("app.cutter.os.path.isfile", return_value=False)
    def test_starts_background_thread(self, mock_isfile, mock_transcode):
        import time as _time
        from app.cutter import start_background_audio_transcode
        start_background_audio_transcode("/media/test.mkv", 1, "job_bg")
        _time.sleep(0.3)  # Give thread time to start
        # Second call should be a no-op (already in progress or done)
        start_background_audio_transcode("/media/test.mkv", 1, "job_bg")


class TestGetAudioTranscodeStatus:

    def test_returns_idle_when_no_status(self):
        from app.cutter import get_audio_transcode_status
        status = get_audio_transcode_status("/media/nonexistent.mkv", "jobX", 1)
        assert status["state"] == "idle"
        assert status["ready"] is False

    @patch("app.cutter.os.path.isfile", return_value=True)
    def test_returns_done_when_file_exists(self, mock_isfile):
        from app.cutter import get_audio_transcode_status
        status = get_audio_transcode_status("/media/test.mkv", "job123", 1)
        assert status["state"] == "done"
        assert status["ready"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_audio_only_transcode.py::TestStartBackgroundAudioTranscode tests/test_audio_only_transcode.py::TestGetAudioTranscodeStatus -v`
Expected: ImportError — functions don't exist yet

- [ ] **Step 3: Implement `start_background_audio_transcode`**

In `backend/app/cutter.py`, after `start_background_transcode` (around line 1501):

```python
def _audio_transcode_file_path(
    filepath: str, job_id: str, audio_stream_index: int
) -> str:
    suffix = _preview_cache_key(filepath)
    job_dir = os.path.join(CUTTER_JOBS_DIR, job_id)
    return os.path.join(
        job_dir, f"preview_{suffix}_srcaudio{audio_stream_index}.mp4"
    )


def start_background_audio_transcode(
    filepath: str,
    audio_stream_index: int,
    job_id: str,
) -> None:
    """Kick off a background audio-only transcode for a single track."""
    audio_path = _audio_transcode_file_path(filepath, job_id, audio_stream_index)

    if os.path.isfile(audio_path):
        return

    with _transcode_lock_guard:
        if audio_path in _transcode_locks:
            return  # Already in progress
        event = threading.Event()
        _transcode_locks[audio_path] = event

    status_key = _audio_transcode_status_key(filepath, job_id, audio_stream_index)
    _set_preview_status(
        status_key,
        {
            "state": "running",
            "ready": False,
            "percent": 0.0,
            "eta_seconds": None,
            "elapsed_seconds": 0.0,
            "message": "Queued for audio transcode",
            "updated_at": time.time(),
        },
    )

    def _run():
        _transcode_semaphore.acquire()
        try:
            transcode_audio_track_from_source(filepath, audio_stream_index, job_id)
        except Exception as exc:
            _set_preview_status(
                status_key,
                {
                    "state": "error",
                    "ready": False,
                    "percent": 0.0,
                    "eta_seconds": None,
                    "elapsed_seconds": 0.0,
                    "message": str(exc),
                    "updated_at": time.time(),
                },
            )
            logger.exception("Background audio transcode failed")
        finally:
            _transcode_semaphore.release()
            event.set()
            with _transcode_lock_guard:
                _transcode_locks.pop(audio_path, None)

    threading.Thread(target=_run, daemon=True).start()


def get_audio_transcode_status(
    filepath: str, job_id: str, audio_stream_index: int
) -> dict:
    """Return transcode status for a specific audio track from source."""
    status_key = _audio_transcode_status_key(filepath, job_id, audio_stream_index)
    with _preview_status_guard:
        status = dict(_preview_status.get(status_key, {}))

    # Check if the output file already exists (done)
    audio_path = _audio_transcode_file_path(filepath, job_id, audio_stream_index)
    if os.path.isfile(audio_path):
        status.update(
            {
                "state": "done",
                "ready": True,
                "percent": 100.0,
                "eta_seconds": 0.0,
                "message": "",
                "updated_at": time.time(),
            }
        )
    else:
        status.setdefault("state", "idle")
        status.setdefault("ready", False)
        status.setdefault("percent", 0.0)
        status.setdefault("eta_seconds", None)
        status.setdefault("elapsed_seconds", 0.0)
        status.setdefault("message", "")
        status.setdefault("updated_at", time.time())
    return status


def wait_for_audio_transcode(
    filepath: str, job_id: str, audio_stream_index: int, timeout: float = 120
) -> str | None:
    """Wait for a background audio transcode to finish. Returns path or None."""
    audio_path = _audio_transcode_file_path(filepath, job_id, audio_stream_index)

    with _transcode_lock_guard:
        event = _transcode_locks.get(audio_path)

    if event:
        event.wait(timeout=timeout)

    return audio_path if os.path.isfile(audio_path) else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_audio_only_transcode.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/cutter.py backend/tests/test_audio_only_transcode.py
git commit -m "feat(cutter): add background audio transcode launcher and status query"
```

---

## Task 3: Backend — Stream and Preview-Status Endpoint Changes

**Files:**

- Modify: `backend/app/main.py:47-72` (imports)
- Modify: `backend/app/main.py:702-800` (stream endpoint)
- Modify: `backend/app/main.py:875-927` (preview-status endpoint)

- [ ] **Step 1: Update imports in `main.py`**

Add the new functions to the import block at `backend/app/main.py:47`:

Add these to the `from app.cutter import (...)` block:

```python
    start_background_audio_transcode,
    get_audio_transcode_status,
    wait_for_audio_transcode,
    transcode_audio_track_from_source,
```

- [ ] **Step 2: Add `transcode_audio_only` parameter to stream endpoint**

At `backend/app/main.py:702`, modify the function signature to add the new parameter:

```python
@app.get("/cutter/stream/{file_id}")
def cutter_stream(
    file_id: str,
    request: Request,
    audio_stream: int | None = Query(None),
    transcode: bool = Query(False),
    audio_only: bool = Query(False),
    transcode_audio_only: bool = Query(False),
):
```

- [ ] **Step 3: Add parameter conflict check and audio-only code path**

After the `audio_indexes` validation block (around line 736), before `if transcode and needs_tx:` (line 746), insert the new code path:

```python
    # Reject conflicting parameters
    if transcode_audio_only and (transcode or audio_only):
        raise HTTPException(
            status_code=400,
            detail="transcode_audio_only cannot be combined with transcode or audio_only",
        )

    if transcode_audio_only:
        if audio_stream is None:
            raise HTTPException(
                status_code=400,
                detail="audio_stream required for audio-only transcode",
            )
        if not job_id:
            raise HTTPException(
                status_code=400,
                detail="job_id required for audio-only transcode",
            )
        start_background_audio_transcode(resolved, audio_stream, job_id)
        audio_path = wait_for_audio_transcode(
            resolved, job_id, audio_stream, timeout=120
        )
        if not audio_path:
            try:
                audio_path = transcode_audio_track_from_source(
                    resolved, audio_stream, job_id
                )
            except RuntimeError as e:
                raise HTTPException(status_code=500, detail=str(e))
        resolved = audio_path
    elif transcode and needs_tx:
```

The existing `if transcode and needs_tx:` becomes `elif transcode and needs_tx:`.

- [ ] **Step 4: Add `audio_transcode_stream` parameter to preview-status endpoint**

Modify the preview-status endpoint at `backend/app/main.py:875`:

```python
@app.get("/cutter/preview-status/{file_id}")
def cutter_preview_status(
    file_id: str,
    audio_transcode_stream: int | None = Query(None),
):
```

Insert after the file existence check (line 885), before the `_done_status` helper:

```python
    # Audio-only transcode status — uses separate key, bypasses master preview check
    if audio_transcode_stream is not None:
        if not job_id:
            raise HTTPException(
                status_code=400,
                detail="job_id required for audio transcode status",
            )
        return get_audio_transcode_status(resolved, job_id, audio_transcode_stream)
```

- [ ] **Step 5: Run existing backend tests to verify no regressions**

Run: `cd backend && python -m pytest tests/ -v`
Expected: All existing tests PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py
git commit -m "feat(cutter): add audio-only transcode stream and status endpoints"
```

---

## Task 4: Frontend — API Functions

**Files:**

- Modify: `frontend/src/lib/api.ts:162-197`

- [ ] **Step 1: Add `getAudioOnlyTranscodeUrl` function**

After `getAudioStreamUrl` (line 186) in `frontend/src/lib/api.ts`:

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

- [ ] **Step 2: Update `fetchPreviewStatus` to support audio transcode stream param**

Replace the `fetchPreviewStatus` function (line 188-197):

```typescript
export function fetchPreviewStatus(
  fileId: string,
  options?: { audioTranscodeStream?: number },
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<import('@/types').CutterPreviewStatus> {
  const params: Record<string, string> = {}
  if (options?.audioTranscodeStream != null) {
    params.audio_transcode_stream = String(options.audioTranscodeStream)
  }
  return fetchJson<import('@/types').CutterPreviewStatus>(
    `/cutter/preview-status/${encodeURIComponent(fileId)}`,
    Object.keys(params).length > 0 ? params : undefined,
    timeoutMs,
  )
}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/api.ts
git commit -m "feat(cutter): add audio-only transcode URL builder and status polling param"
```

---

## Task 5: Frontend — CutterPanel State and UI

**Files:**

- Modify: `frontend/src/components/CutterPanel.tsx:151` (state)
- Modify: `frontend/src/components/CutterPanel.tsx:620-626` (stream audio index)
- Modify: `frontend/src/components/CutterPanel.tsx:646-691` (polling effect)
- Modify: `frontend/src/components/CutterPanel.tsx:849-907` (UI buttons and MediaPlayer props)

- [ ] **Step 1: Replace `transcodePreviewEnabled` with `transcodeMode`**

At `frontend/src/components/CutterPanel.tsx:151`, replace:

```typescript
const [transcodePreviewEnabled, setTranscodePreviewEnabled] = useState(false)
```

with:

```typescript
const [transcodeMode, setTranscodeMode] = useState<'off' | 'audio_only' | 'full'>('off')
```

- [ ] **Step 2: Add import for new API function**

Update the import from `@/lib/api` to include `getAudioOnlyTranscodeUrl`.

- [ ] **Step 3: Update all references to `transcodePreviewEnabled`**

Search and replace throughout `CutterPanel.tsx`:

- `transcodePreviewEnabled` used as a boolean check → `transcodeMode !== 'off'`
- `setTranscodePreviewEnabled(true)` → `setTranscodeMode('full')` (will be split in UI step)
- `setTranscodePreviewEnabled(false)` → `setTranscodeMode('off')`
- `!transcodePreviewEnabled` → `transcodeMode === 'off'`

Specific locations:

At line 622 (`streamAudioIndex` computed value):
```typescript
if (transcodeMode === 'off' && selectedPreviewAudioStreamIndex === defaultAudioStreamIndex) {
```

At line 639:
```typescript
if (probe.needs_transcoding && transcodeMode === 'off') {
```

At line 647:
```typescript
if (!hasFile || !probe?.needs_transcoding || !fileId || transcodeMode === 'off') {
```

- [ ] **Step 4: Update the preview status polling effect**

At line 646-691, update the polling to pass `audioTranscodeStream` when in `'audio_only'` mode:

```typescript
useEffect(() => {
    if (!hasFile || !probe?.needs_transcoding || !fileId || transcodeMode === 'off') {
      setPreviewStatus(null)
      return
    }

    let cancelled = false
    let timeoutId: ReturnType<typeof setTimeout> | null = null
    let lastState: CutterPreviewStatus['state'] = 'running'

    const poll = async () => {
      try {
        const status = await fetchPreviewStatus(
          fileId,
          transcodeMode === 'audio_only' && selectedPreviewAudioStreamIndex != null
            ? { audioTranscodeStream: selectedPreviewAudioStreamIndex }
            : undefined,
        )
        if (cancelled) return
        setPreviewStatus(status)
        lastState = status.state
        if (status.state === 'error' && status.message) {
          onError(status.message)
        }
      } catch (err) {
        if (cancelled) return
        setPreviewStatus(
          (prev) =>
            prev ?? {
              state: 'error',
              ready: false,
              percent: 0,
              eta_seconds: null,
              elapsed_seconds: 0,
              message: err instanceof Error ? err.message : String(err),
            },
        )
      } finally {
        if (!cancelled && lastState !== 'done' && lastState !== 'error') {
          timeoutId = setTimeout(poll, 1200)
        }
      }
    }

    void poll()

    return () => {
      cancelled = true
      if (timeoutId) clearTimeout(timeoutId)
    }
  }, [hasFile, probe?.needs_transcoding, fileId, transcodeMode, selectedPreviewAudioStreamIndex, onError])
```

- [ ] **Step 5: Update the UI buttons in the compatibility warning**

Replace the button section at line 852-871. Compute whether audio-only should be shown:

```typescript
const showAudioOnlyButton = isVideo && compatibilityReport && !compatibilityReport.videoIssue
```

Then in the JSX:

```tsx
{probe?.needs_transcoding && (
  <div className="mt-2 flex items-center gap-2">
    {transcodeMode === 'off' ? (
      <>
        {showAudioOnlyButton && (
          <button
            type="button"
            onClick={() => setTranscodeMode('audio_only')}
            className="rounded-md border border-amber-300/40 bg-amber-300/12 px-2.5 py-1 text-[0.72rem] font-semibold text-amber-100 transition hover:bg-amber-300/18"
          >
            Transcode Audio Only
          </button>
        )}
        <button
          type="button"
          onClick={() => setTranscodeMode('full')}
          className="rounded-md border border-amber-300/40 bg-amber-300/12 px-2.5 py-1 text-[0.72rem] font-semibold text-amber-100 transition hover:bg-amber-300/18"
        >
          Full Transcode
        </button>
      </>
    ) : (
      <button
        type="button"
        onClick={() => setTranscodeMode('off')}
        className="rounded-md border border-white/20 bg-white/8 px-2.5 py-1 text-[0.72rem] font-semibold text-white/80 transition hover:bg-white/12"
      >
        Use Original Playback
      </button>
    )}
  </div>
)}
```

- [ ] **Step 6: Update MediaPlayer props for stream URLs**

Replace the `streamUrl` and `audioUrl` props at line 878-890:

```tsx
<MediaPlayer
  streamUrl={
    transcodeMode === 'full' && isVideo
      ? getStreamUrl(fileId, null, true)
      : getStreamUrl(
          fileId,
          transcodeMode === 'off' ? streamAudioIndex : null,
          transcodeMode === 'full',
        )
  }
  audioUrl={
    transcodeMode === 'audio_only' && selectedPreviewAudioStreamIndex != null
      ? getAudioOnlyTranscodeUrl(fileId, selectedPreviewAudioStreamIndex)
      : isVideo &&
          transcodeMode === 'full' &&
          selectedPreviewAudioStreamIndex != null &&
          selectedPreviewAudioStreamIndex !== defaultAudioStreamIndex
        ? getAudioStreamUrl(fileId, selectedPreviewAudioStreamIndex, true)
        : undefined
  }
```

Update `needsTranscoding` prop:

```tsx
  needsTranscoding={probe.needs_transcoding && transcodeMode !== 'off'}
```

- [ ] **Step 7: Run frontend build to check for type errors**

Run: `cd frontend && npm run build`
Expected: Build succeeds with no type errors

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/CutterPanel.tsx
git commit -m "feat(cutter): add audio-only transcode mode UI with three-state toggle"
```

---

## Task 6: Integration Testing

**Files:**

- All modified files

- [ ] **Step 1: Run full backend test suite**

Run: `cd backend && python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 2: Run frontend tests and build**

Run: `cd frontend && npm run test && npm run build`
Expected: All PASS, build succeeds

- [ ] **Step 3: Run prettier on modified frontend files**

Run: `cd frontend && npm run format`

- [ ] **Step 4: Commit any formatting fixes**

```bash
git add -u
git commit -m "style: format frontend code"
```
