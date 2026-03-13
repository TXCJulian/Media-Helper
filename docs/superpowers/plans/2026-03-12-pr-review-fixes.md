# PR Review Fixes — Media Cutter

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all critical/important issues identified during PR #5 review of the media cutter feature.

**Architecture:** Targeted fixes across backend Python and frontend TypeScript — no structural refactors. Each task addresses a coherent group of related issues.

**Tech Stack:** Python 3.12 / FastAPI, React 19 / TypeScript, ffmpeg subprocess calls

---

## Chunk 1: Backend Safety & Error Handling

### Task 1: Add `--` separator to all ffmpeg/ffprobe subprocess calls

Prevents filenames starting with `-` from being interpreted as ffmpeg options.

**Files:**
- Modify: `backend/app/cutter.py`

- [ ] **Step 1: Add `--` before filepath in `probe_file`**

In `cutter.py:145`, change:
```python
        filepath,
```
to:
```python
        "--", filepath,
```

The full cmd list (lines 139-146) becomes:
```python
    cmd = [
        "ffprobe",
        "-loglevel", "warning",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        "--", filepath,
    ]
```

- [ ] **Step 2: Add `--` before filepath in `_extract_window`**

In `cutter.py:67`, the `-i filepath` argument. Since ffmpeg uses `-i` flag, the safe approach is to prefix the path with `./` when it starts with `-`. However, since these are list-based subprocess calls (not shell), and `-i` takes the next argument as its value, ffmpeg will interpret the next token as the filename regardless. The real risk is in commands where filepath appears without `-i`.

For consistency and defense-in-depth, change the `-i` argument across all ffmpeg calls. In `_extract_window` (line 62-72):
```python
    cmd = [
        "ffmpeg",
        "-loglevel", "error",
        "-ss", str(position),
        "-t", str(window_secs),
        "-i", filepath,
        "-ac", "1",
        "-ar", "8000",
        "-f", "f32le",
        "pipe:1",
    ]
```
No change needed here — `-i` safely consumes the next argument. But for `probe_file` and `generate_thumbnail_strip` where filepath appears without `-i`, the `--` is necessary.

Actually, `ffprobe` is the only command where filepath is a positional arg (no `-i`). All `ffmpeg` calls use `-i filepath` which is safe. Focus the fix on `probe_file` only.

Revised: Only fix `probe_file` at line 145.

- [ ] **Step 3: Add `--` before filepath in `generate_thumbnail_strip`**

In `cutter.py:550`, each `-i filepath` in the loop is safe because `-i` consumes the next arg. However, if `filepath` starts with `-`, ffmpeg *could* still misparse in edge cases with multiple inputs.

For defense-in-depth, no change needed — the `-i` flag protects us. Skip this step.

- [ ] **Step 4: Verify backend starts**

Run: `cd backend && python -c "from app.cutter import probe_file; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add backend/app/cutter.py
git commit -m "fix: add -- separator in ffprobe to prevent option injection"
```

---

### Task 2: Add logging to silent backend fallbacks

**Files:**
- Modify: `backend/app/cutter.py`

- [ ] **Step 1: Log warning in `_audio_relative_index` fallback**

At `cutter.py:43`, change:
```python
    return 0  # fallback to first audio
```
to:
```python
    logger.warning(
        "Audio stream index %d not found in %s (available: %s), falling back to first",
        absolute_index, filepath, [s["index"] for s in audio_streams],
    )
    return 0
```

- [ ] **Step 2: Log warning in `_extract_window` on failure**

At `cutter.py:74-75`, change:
```python
    if result.returncode != 0:
        return b""  # Skip failed windows gracefully
```
to:
```python
    if result.returncode != 0:
        logger.warning(
            "Waveform window extraction failed at %.1fs for %s: %s",
            position, filepath, result.stderr.decode(errors="replace")[:200],
        )
        return b""
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/cutter.py
git commit -m "fix: add logging to silent fallbacks in cutter waveform/audio index"
```

---

### Task 3: Fix race condition in preview transcoding

**Files:**
- Modify: `backend/app/cutter.py`

- [ ] **Step 1: Write to temp file and atomically rename in `get_or_transcode_preview`**

At `cutter.py:328`, change:
```python
    cmd += ["-sn", "-f", "mp4", "-movflags", "+faststart", preview_path]

    result = subprocess.run(cmd, capture_output=True, timeout=600)
    if result.returncode != 0:
        # Clean up partial file
        if os.path.isfile(preview_path):
            os.remove(preview_path)
        raise RuntimeError(
            f"Preview transcode failed: {result.stderr.decode(errors='replace')}"
        )

    return preview_path
```
to:
```python
    tmp_path = preview_path + ".tmp"
    cmd += ["-sn", "-f", "mp4", "-movflags", "+faststart", tmp_path]

    result = subprocess.run(cmd, capture_output=True, timeout=600)
    if result.returncode != 0:
        if os.path.isfile(tmp_path):
            os.remove(tmp_path)
        raise RuntimeError(
            f"Preview transcode failed: {result.stderr.decode(errors='replace')}"
        )

    os.replace(tmp_path, preview_path)
    return preview_path
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/cutter.py
git commit -m "fix: use atomic rename for preview transcode to prevent race condition"
```

---

### Task 4: Harden job metadata and deletion

**Files:**
- Modify: `backend/app/cutter.py`

- [ ] **Step 1: Add JSONDecodeError handling to `load_job_metadata`**

At `cutter.py:626-627`, change:
```python
    with open(meta_path) as f:
        return json.load(f)
```
to:
```python
    try:
        with open(meta_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read job metadata for %s: %s", job_id, e)
        return None
```

- [ ] **Step 2: Add error callback to `delete_job` rmtree**

At `cutter.py:645-648`, change:
```python
def delete_job(job_id: str) -> None:
    """Delete a job and all its files."""
    job_dir = get_job_dir(job_id)
    shutil.rmtree(job_dir, ignore_errors=True)
```
to:
```python
def _rmtree_onerror(func, path, exc_info):
    logger.warning("Failed to remove %s during job cleanup: %s", path, exc_info[1])


def delete_job(job_id: str) -> None:
    """Delete a job and all its files."""
    job_dir = get_job_dir(job_id)
    shutil.rmtree(job_dir, onerror=_rmtree_onerror)
```

- [ ] **Step 3: Use same error handler in `cleanup_old_jobs`**

At `cutter.py:667`, change:
```python
                    shutil.rmtree(job_dir, ignore_errors=True)
```
to:
```python
                    shutil.rmtree(job_dir, onerror=_rmtree_onerror)
```

And at `cutter.py:673`, change:
```python
                    shutil.rmtree(job_dir, ignore_errors=True)
```
to:
```python
                    shutil.rmtree(job_dir, onerror=_rmtree_onerror)
```

- [ ] **Step 4: Wrap `list_jobs` metadata loading**

At `cutter.py:638-640`, change:
```python
        meta = load_job_metadata(name)
        if meta:
            jobs.append(meta)
```
to:
```python
        try:
            meta = load_job_metadata(name)
            if meta:
                jobs.append(meta)
        except Exception:
            logger.warning("Skipping corrupt job %s", name, exc_info=True)
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/cutter.py
git commit -m "fix: harden job metadata loading, deletion logging"
```

---

### Task 5: Add error handling to `cutter_save_to_source` and remove dead config

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/config.py`

- [ ] **Step 1: Wrap `shutil.copy2` in save-to-source endpoint**

Find the `shutil.copy2(src_file, dest_path)` line in `main.py` (around line 909) and wrap it:
```python
    try:
        shutil.copy2(src_file, dest_path)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {e}")
```

- [ ] **Step 2: Remove dead `CUTTER_UPLOAD_DIR` from config**

In `config.py:24`, delete the line:
```python
CUTTER_UPLOAD_DIR = os.getenv("CUTTER_UPLOAD_DIR", "/tmp/cutter-uploads")  # deprecated
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/main.py backend/app/config.py
git commit -m "fix: add error handling to save-to-source, remove dead CUTTER_UPLOAD_DIR config"
```

---

## Chunk 2: Frontend Error Handling & Bug Fixes

### Task 6: Fix JobManager silent catch blocks

**Files:**
- Modify: `frontend/src/components/cutter/JobManager.tsx`

- [ ] **Step 1: Add error state to component**

After line 27 (`const [savingFile, setSavingFile] = ...`), add:
```typescript
  const [error, setError] = useState<string | null>(null)
```

- [ ] **Step 2: Fix refresh catch block**

At lines 34-35, change:
```typescript
    } catch {
      // silently fail
```
to:
```typescript
    } catch (err) {
      console.error('[JobManager] Failed to load jobs:', err)
      setError('Failed to load jobs')
```

- [ ] **Step 3: Fix handleDelete catch block**

At lines 49-50, change:
```typescript
    } catch {
      // silently fail
```
to:
```typescript
    } catch (err) {
      console.error('[JobManager] Failed to delete job:', err)
      setError('Failed to delete job')
      void refresh()
```

- [ ] **Step 4: Fix saveToSource catch block**

At lines 119-121, change:
```typescript
                                saveToSource(job.job_id, file)
                                  .catch(() => {})
                                  .finally(() => setSavingFile(null))
```
to:
```typescript
                                saveToSource(job.job_id, file)
                                  .catch((err) => {
                                    console.error('[JobManager] Save to source failed:', err)
                                    setError(`Failed to save ${file}`)
                                  })
                                  .finally(() => setSavingFile(null))
```

- [ ] **Step 5: Add error display in the UI**

After the opening `{open && (` block (line 77), before the `jobs.length === 0` check, add an error banner:
```tsx
          {error && (
            <div className="flex items-center justify-between bg-red-500/10 px-4 py-2 text-[0.72rem] text-red-300">
              <span>{error}</span>
              <button type="button" onClick={() => setError(null)} className="ml-2 text-red-400 hover:text-red-200">&times;</button>
            </div>
          )}
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/cutter/JobManager.tsx
git commit -m "fix: replace silent catch blocks with error feedback in JobManager"
```

---

### Task 7: Fix handleFileSelect not passing job_id to loadFileData

**Files:**
- Modify: `frontend/src/components/CutterPanel.tsx`

- [ ] **Step 1: Pass job_id to loadFileData**

At `CutterPanel.tsx:247`, change:
```typescript
        await loadFileData(path, 'server')
```
to:
```typescript
        await loadFileData(path, 'server', job_id)
```

This enables background transcoding to start early for server files that need it.

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/CutterPanel.tsx
git commit -m "fix: pass job_id to loadFileData for early background transcode"
```

---

### Task 8: Fix file list fetch error and add error feedback

**Files:**
- Modify: `frontend/src/components/CutterPanel.tsx`

- [ ] **Step 1: Show error on file fetch failure**

At `CutterPanel.tsx:202-204`, change:
```typescript
      .catch(() => {
        if (signal.cancelled) return
        setSource({ files: [] })
      })
```
to:
```typescript
      .catch((err) => {
        if (signal.cancelled) return
        console.error('[CutterPanel] Failed to load files:', err)
        setSource({ files: [] })
        onError(`Failed to load files: ${err instanceof Error ? err.message : String(err)}`)
      })
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/CutterPanel.tsx
git commit -m "fix: show error message when file list fetch fails"
```

---

### Task 9: Fix ThumbnailStrip missing onerror and MediaPlayer NaN volume

**Files:**
- Modify: `frontend/src/components/cutter/ThumbnailStrip.tsx`
- Modify: `frontend/src/components/cutter/MediaPlayer.tsx`

- [ ] **Step 1: Add onerror handler to ThumbnailStrip**

At `ThumbnailStrip.tsx:43-44`, after the `img.onload` line, add onerror:
```typescript
    img.onload = () => setSpriteImg(img)
    img.onerror = () => console.error('[ThumbnailStrip] Failed to load sprite:', thumbnailUrl)
    img.src = thumbnailUrl
```

Update the cleanup at line 46 to also clear onerror:
```typescript
    return () => {
      img.onload = null
      img.onerror = null
    }
```

- [ ] **Step 2: Guard against NaN volume in MediaPlayer**

At `MediaPlayer.tsx:41-44`, change:
```typescript
  const [volume, setVolume] = useState(() => {
    const saved = localStorage.getItem('cutter-volume')
    return saved != null ? parseFloat(saved) : 1
  })
```
to:
```typescript
  const [volume, setVolume] = useState(() => {
    const saved = localStorage.getItem('cutter-volume')
    const parsed = saved != null ? parseFloat(saved) : 1
    return Number.isFinite(parsed) ? parsed : 1
  })
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/cutter/ThumbnailStrip.tsx frontend/src/components/cutter/MediaPlayer.tsx
git commit -m "fix: add ThumbnailStrip error handler, guard MediaPlayer volume NaN"
```

---

### Task 10: Capture error details in SSE catch blocks

**Files:**
- Modify: `frontend/src/lib/sse.ts`

- [ ] **Step 1: Fix first catch block**

At `sse.ts:31-35`, change:
```typescript
    } catch {
      if (!controller.signal.aborted) {
        callbacks.onError('Connection failed')
      }
      return
    }
```
to:
```typescript
    } catch (err) {
      if (!controller.signal.aborted) {
        callbacks.onError(`Connection failed: ${err instanceof Error ? err.message : String(err)}`)
      }
      return
    }
```

- [ ] **Step 2: Fix second catch block**

At `sse.ts:102-105`, change:
```typescript
    } catch {
      if (!controller.signal.aborted) {
        callbacks.onError('Connection lost')
      }
    }
```
to:
```typescript
    } catch (err) {
      if (!controller.signal.aborted) {
        callbacks.onError(`Connection lost: ${err instanceof Error ? err.message : String(err)}`)
      }
    }
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/sse.ts
git commit -m "fix: capture error details in SSE catch blocks"
```

---

## Chunk 3: Type Safety & Documentation

### Task 11: Add literal union types for CutterJob

**Files:**
- Modify: `frontend/src/types.ts`

- [ ] **Step 1: Add type aliases and update CutterJob**

At `types.ts`, before the `CutterJob` interface (line 113), add:
```typescript
export type CutterJobStatus = 'ready' | 'cutting' | 'done' | 'error'
export type CutterSource = 'server' | 'upload'
```

Then update `CutterJob` (lines 113-120):
```typescript
export interface CutterJob {
  job_id: string
  source: CutterSource
  original_name: string
  created_at: string
  status: CutterJobStatus
  output_files: string[]
}
```

- [ ] **Step 2: Update STATUS_COLORS in JobManager to use the type**

At `JobManager.tsx:16`, change:
```typescript
const STATUS_COLORS: Record<string, string> = {
```
to:
```typescript
import type { CutterJobStatus } from '@/types'
```

And:
```typescript
const STATUS_COLORS: Record<CutterJobStatus, string> = {
```

Remove the import of `CutterJob` from line 3 and combine it with the new import if needed.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types.ts frontend/src/components/cutter/JobManager.tsx
git commit -m "feat: add literal union types for CutterJob status and source"
```

---

### Task 12: Fix documentation inaccuracies

**Files:**
- Modify: `docs/plans/2026-03-09-media-cutter-design.md`
- Modify: `backend/app/cutter.py`

- [ ] **Step 1: Fix upload limit in design doc**

At `docs/plans/2026-03-09-media-cutter-design.md:52`, change:
```
6. 2GB upload size limit enforced at both nginx and application level
```
to:
```
6. 50GB upload size limit enforced at both nginx and application level
```

- [ ] **Step 2: Fix `generate_waveform` docstring**

At `cutter.py:189-196`, change:
```python
def generate_waveform(filepath: str, num_peaks: int = 2000) -> list[float]:
    """Generate a normalized waveform peak list from an audio/video file.

    Uses ffmpeg to extract mono PCM f32le audio at 8kHz (capped at 1 hour),
    then buckets samples and takes the max absolute value per bucket. Results
    are cached (bounded LRU, max 50 entries) by (filepath, mtime) to avoid
    regeneration.
    """
```
to:
```python
def generate_waveform(filepath: str, num_peaks: int = 2000) -> list[float]:
    """Generate a normalized waveform peak list from an audio/video file.

    For short files (<=2 min), decodes the entire file. For longer files,
    samples 20 evenly-spaced 5-second windows. Results are cached (bounded
    LRU, max 50 entries) by (filepath, mtime) to avoid regeneration.
    """
```

- [ ] **Step 3: Add missing `audio_stream_index` to `cut_file` docstring**

At `cutter.py:445` (after `progress_cb` line), add:
```python
        audio_stream_index: Absolute stream index for the audio track (None = default).
```

- [ ] **Step 4: Commit**

```bash
git add docs/plans/2026-03-09-media-cutter-design.md backend/app/cutter.py
git commit -m "docs: fix inaccurate waveform docstring, upload limit, and missing param"
```

---

## Summary

| Task | Category | Issues Fixed |
|------|----------|-------------|
| 1 | Security | ffprobe option injection |
| 2 | Error handling | Silent waveform/audio fallbacks |
| 3 | Race condition | Preview transcode file write |
| 4 | Error handling | Job metadata, deletion logging |
| 5 | Error handling + cleanup | save-to-source, dead config |
| 6 | Frontend errors | JobManager 3 catch blocks |
| 7 | Bug fix | Missing job_id in loadFileData |
| 8 | Frontend errors | File list fetch error |
| 9 | Frontend errors | ThumbnailStrip onerror, NaN volume |
| 10 | Frontend errors | SSE catch block details |
| 11 | Type safety | CutterJob literal unions |
| 12 | Documentation | Docstrings, design doc |
