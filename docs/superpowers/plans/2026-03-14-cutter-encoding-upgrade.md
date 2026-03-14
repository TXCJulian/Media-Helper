# Cutter Encoding Upgrade Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the media cutter's encoding settings with per-track audio control, source quality matching, intelligent preselection, and quality loss awareness.

**Architecture:** Backend `probe_file` is extended with per-stream bitrate data. `cut_file` is refactored to accept a list of audio track configurations instead of a single codec/stream index, building per-stream `-map` and `-c:a:N` ffmpeg args. Frontend replaces the single audio track dropdown with an inline track list in `OutputSettings`, preselects codec/container from probe data, and adds a keep-quality toggle.

**Tech Stack:** Python 3.12 / FastAPI (backend), React 19 / TypeScript / Tailwind CSS 4 (frontend), ffmpeg/ffprobe (media processing), Vitest (frontend tests), pytest (backend tests)

**Spec:** `docs/superpowers/specs/2026-03-14-cutter-encoding-upgrade-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/app/cutter.py` | Modify | Add bitrate to probe, refactor `_audio_relative_index`, rework `cut_file` for multi-track + keep-quality |
| `backend/app/main.py` | Modify | Update `/cutter/cut` endpoint params, validation, bitrate passthrough |
| `backend/tests/test_cutter.py` | Modify | Update `_audio_relative_index` tests, add multi-track and keep-quality tests |
| `backend/tests/test_main.py` | Modify | Update cut endpoint tests for new params |
| `frontend/src/types.ts` | Modify | Add `AudioTrackConfig`, update `CutterForm`, `CutJobSettings`, `ProbeResult`, `AudioStreamInfo` |
| `frontend/src/components/cutter/OutputSettings.tsx` | Rewrite | Add keep-quality toggle, per-track audio list, always-visible container |
| `frontend/src/components/CutterPanel.tsx` | Modify | Preselection logic, audio track init, new serialization, remove AudioTrackSelect |
| `frontend/src/App.tsx` | Modify | Update `INITIAL_CUTTER_STATE` for new form fields |
| `frontend/src/components/cutter/AudioTrackSelect.tsx` | Delete | Replaced by inline track list in OutputSettings |

---

## Chunk 1: Backend — Probe & Audio Index Refactor

### Task 1: Add bitrate fields to `probe_file`

**Files:**
- Modify: `backend/app/cutter.py:314-348` (probe_file return dict)
- Test: `backend/tests/test_cutter.py`

- [ ] **Step 1: Write failing test for per-stream audio bitrate**

Add to `backend/tests/test_cutter.py`:

```python
def test_probe_file_includes_audio_bitrate(monkeypatch):
    """probe_file should include bit_rate in each audio stream dict."""
    fake_output = json.dumps({
        "format": {"duration": "60.0", "bit_rate": "1000000", "format_name": "matroska"},
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
            {"index": 1, "codec_type": "audio", "codec_name": "aac", "channels": 2,
             "sample_rate": "48000", "bit_rate": "192000", "tags": {}},
            {"index": 2, "codec_type": "audio", "codec_name": "ac3", "channels": 6,
             "sample_rate": "48000", "bit_rate": "384000", "tags": {"language": "ger"}},
        ],
    })
    monkeypatch.setattr(
        cutter.subprocess, "run",
        lambda *a, **kw: types.SimpleNamespace(returncode=0, stdout=fake_output, stderr=""),
    )

    info = cutter.probe_file("/fake/file.mkv")
    assert info["audio_streams"][0]["bit_rate"] == 192000
    assert info["audio_streams"][1]["bit_rate"] == 384000
    assert info["video_bitrate"] == 0  # video stream bit_rate not always present
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_cutter.py::test_probe_file_includes_audio_bitrate -v`
Expected: FAIL — `KeyError: 'bit_rate'` or `KeyError: 'video_bitrate'`

- [ ] **Step 3: Implement bitrate fields in probe_file**

In `backend/app/cutter.py`, modify the audio streams list comprehension (line ~314-325) to add `"bit_rate"`:

```python
audio_streams = [
    {
        "index": int(s["index"]),
        "codec": s.get("codec_name", "unknown"),
        "channels": int(s.get("channels", 0)),
        "sample_rate": int(s.get("sample_rate", 0)),
        "bit_rate": int(s.get("bit_rate", 0)),
        "language": s.get("tags", {}).get("language", ""),
        "title": s.get("tags", {}).get("title", ""),
    }
    for s in streams
    if s.get("codec_type") == "audio"
]
```

Add `video_bitrate` to the info dict (after line ~344):

```python
"video_bitrate": int(video_stream.get("bit_rate", 0)) if video_stream else None,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_cutter.py::test_probe_file_includes_audio_bitrate -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/cutter.py backend/tests/test_cutter.py
git commit -m "feat(cutter): add per-stream bitrate to probe_file output"
```

---

### Task 2: Refactor `_audio_relative_index` to accept stream list

**Files:**
- Modify: `backend/app/cutter.py:82-97` (`_audio_relative_index`)
- Modify: `backend/tests/test_cutter.py:11-24` (existing test)

- [ ] **Step 1: Update existing test to use new signature**

In `backend/tests/test_cutter.py`, update `test_audio_relative_index_raises_for_unknown_stream`:

```python
def test_audio_relative_index_raises_for_unknown_stream():
    audio_streams = [{"index": 1}, {"index": 3}]
    with pytest.raises(RuntimeError, match="Audio stream index 2 not found"):
        cutter._audio_relative_index(audio_streams, 2)
```

Add a passing case test:

```python
def test_audio_relative_index_returns_correct_relative_index():
    audio_streams = [{"index": 1}, {"index": 3}, {"index": 5}]
    assert cutter._audio_relative_index(audio_streams, 1) == 0
    assert cutter._audio_relative_index(audio_streams, 3) == 1
    assert cutter._audio_relative_index(audio_streams, 5) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_cutter.py::test_audio_relative_index_raises_for_unknown_stream tests/test_cutter.py::test_audio_relative_index_returns_correct_relative_index -v`
Expected: FAIL — signature mismatch

- [ ] **Step 3: Refactor `_audio_relative_index`**

In `backend/app/cutter.py`, change the function (line ~82) from:

```python
def _audio_relative_index(filepath: str, absolute_index: int) -> int:
    info = probe_file(filepath)
    audio_streams = info.get("audio_streams", [])
    ...
```

To:

```python
def _audio_relative_index(audio_streams: list[dict], absolute_index: int) -> int:
    for i, s in enumerate(audio_streams):
        if int(s.get("index", -1)) == absolute_index:
            return i
    raise RuntimeError(
        f"Audio stream index {absolute_index} not found in "
        f"{[s.get('index') for s in audio_streams]}"
    )
```

Then update all internal callers of `_audio_relative_index` to pass `audio_streams` instead of `filepath`. Search for existing call sites:
- In `get_track_preview` (~line 910): change `_audio_relative_index(filepath, audio_stream_index)` to pass the audio streams from probe data
- In `get_track_remux` (~line 1000): same change
- In `start_background_transcode._run` (~line 1147): change `_audio_relative_index(filepath, audio_stream_index)` to pass audio streams from probe data
- In `cut_file` (~line 1487): will be reworked in Task 4, but update existing code for now

For `get_track_preview`, `get_track_remux`, and `start_background_transcode._run`, these functions receive `filepath` and can probe once at the start. Add `info = probe_file(filepath)` if not already present, then use `info["audio_streams"]`.

Also update the monkeypatch in `test_cutter.py` for `test_get_track_preview_uses_mp4_output_and_absolute_track_cache_key` (line ~34): change `_audio_relative_index` mock to match new signature: `lambda _streams, _idx: 1`.

- [ ] **Step 4: Run all cutter tests**

Run: `cd backend && python -m pytest tests/test_cutter.py -v`
Expected: All pass

- [ ] **Step 5: Run full test suite to check nothing broke**

Run: `cd backend && python -m pytest tests/ -v`
Expected: All 101+ tests pass

- [ ] **Step 6: Commit**

```bash
git add backend/app/cutter.py backend/tests/test_cutter.py
git commit -m "refactor(cutter): _audio_relative_index accepts stream list instead of probing"
```

---

## Chunk 2: Backend — Multi-Track `cut_file` & Endpoint

### Task 3: Rework `cut_file` for multi-track audio

**Files:**
- Modify: `backend/app/cutter.py:1412-1530` (`cut_file`)

- [ ] **Step 1: Write failing test for multi-track mapping**

Add to `backend/tests/test_cutter.py`:

```python
def test_cut_file_multi_track_mapping(tmp_path, monkeypatch):
    """cut_file should build correct -map and -c:a:N args for multi-track audio."""
    monkeypatch.setattr(cutter, "CUTTER_JOBS_DIR", str(tmp_path))
    monkeypatch.setattr(cutter, "collision_safe_path", lambda p: p)

    captured_cmd = {}

    class FakeProc:
        returncode = 0
        stderr = iter([])
        stdout = None
        def wait(self, timeout=None):
            pass
        def poll(self):
            return 0

    def fake_popen(cmd, **kw):
        captured_cmd["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(cutter.subprocess, "Popen", fake_popen)

    audio_streams = [
        {"index": 1, "bit_rate": 192000},
        {"index": 3, "bit_rate": 384000},
        {"index": 5, "bit_rate": 128000},
    ]
    audio_tracks = [
        {"index": 1, "mode": "passthru", "codec": None},
        {"index": 3, "mode": "reencode", "codec": "aac"},
        {"index": 5, "mode": "remove", "codec": None},
    ]

    out = tmp_path / "output.mkv"
    out.touch()

    cutter.cut_file(
        filepath="/fake/input.mkv",
        in_point=10.0,
        out_point=40.0,
        output_path=str(out),
        stream_copy=True,
        codec=None,
        audio_tracks=audio_tracks,
        container="mkv",
        progress_cb=lambda msg: None,
        audio_streams=audio_streams,
    )

    cmd = captured_cmd["cmd"]

    # Should map video + 2 audio tracks (track 3 at index 5 is removed)
    assert "-map" in cmd
    map_args = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-map"]
    assert "0:v?" in map_args
    assert "0:a:0" in map_args   # stream index 1 -> relative 0
    assert "0:a:1" in map_args   # stream index 3 -> relative 1
    assert "0:a:2" not in map_args  # stream index 5 removed

    # Video should be copy (stream_copy=True)
    v_idx = cmd.index("-c:v")
    assert cmd[v_idx + 1] == "copy"

    # First output audio track: passthru -> copy
    a0_idx = cmd.index("-c:a:0")
    assert cmd[a0_idx + 1] == "copy"

    # Second output audio track: reencode -> aac
    a1_idx = cmd.index("-c:a:1")
    assert cmd[a1_idx + 1] == "aac"
```

- [ ] **Step 2: Write failing test for keep_quality bitrate flags**

```python
def test_cut_file_keep_quality_adds_bitrate_flags(tmp_path, monkeypatch):
    """keep_quality should add -b:v and -b:a:N flags when bitrates are known."""
    monkeypatch.setattr(cutter, "CUTTER_JOBS_DIR", str(tmp_path))
    monkeypatch.setattr(cutter, "collision_safe_path", lambda p: p)

    captured_cmd = {}

    class FakeProc:
        returncode = 0
        stderr = iter([])
        stdout = None
        def wait(self, timeout=None):
            pass
        def poll(self):
            return 0

    def fake_popen(cmd, **kw):
        captured_cmd["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(cutter.subprocess, "Popen", fake_popen)

    audio_streams = [{"index": 1, "bit_rate": 192000}]
    audio_tracks = [{"index": 1, "mode": "reencode", "codec": "aac"}]

    out = tmp_path / "output.mp4"
    out.touch()

    cutter.cut_file(
        filepath="/fake/input.mp4",
        in_point=0.0,
        out_point=30.0,
        output_path=str(out),
        stream_copy=False,
        codec="libx264",
        audio_tracks=audio_tracks,
        container="mp4",
        progress_cb=lambda msg: None,
        keep_quality=True,
        source_video_bitrate=5000000,
        source_audio_bitrates={1: 192000},
        audio_streams=audio_streams,
    )

    cmd = captured_cmd["cmd"]
    # Video bitrate
    bv_idx = cmd.index("-b:v")
    assert cmd[bv_idx + 1] == "5000000"
    # Audio bitrate for output track 0
    ba_idx = cmd.index("-b:a:0")
    assert cmd[ba_idx + 1] == "192000"


def test_cut_file_keep_quality_skips_zero_bitrate(tmp_path, monkeypatch):
    """keep_quality should skip -b:v/-b:a when bitrate is 0 (unknown)."""
    monkeypatch.setattr(cutter, "CUTTER_JOBS_DIR", str(tmp_path))
    monkeypatch.setattr(cutter, "collision_safe_path", lambda p: p)

    captured_cmd = {}

    class FakeProc:
        returncode = 0
        stderr = iter([])
        stdout = None
        def wait(self, timeout=None):
            pass
        def poll(self):
            return 0

    def fake_popen(cmd, **kw):
        captured_cmd["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(cutter.subprocess, "Popen", fake_popen)

    audio_streams = [{"index": 1, "bit_rate": 0}]
    audio_tracks = [{"index": 1, "mode": "reencode", "codec": "aac"}]

    out = tmp_path / "output.mp4"
    out.touch()

    cutter.cut_file(
        filepath="/fake/input.mp4",
        in_point=0.0,
        out_point=30.0,
        output_path=str(out),
        stream_copy=False,
        codec="libx264",
        audio_tracks=audio_tracks,
        container="mp4",
        progress_cb=lambda msg: None,
        keep_quality=True,
        source_video_bitrate=0,
        source_audio_bitrates={1: 0},
        audio_streams=audio_streams,
    )

    cmd = captured_cmd["cmd"]
    assert "-b:v" not in cmd
    assert "-b:a:0" not in cmd
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_cutter.py::test_cut_file_multi_track_mapping tests/test_cutter.py::test_cut_file_keep_quality_adds_bitrate_flags tests/test_cutter.py::test_cut_file_keep_quality_skips_zero_bitrate -v`
Expected: FAIL — signature mismatch or missing params

- [ ] **Step 4: Implement the new `cut_file` logic**

In `backend/app/cutter.py`, rework `cut_file` (line ~1412):

**Signature:** Replace `audio_codec: Optional[str]` and `audio_stream_index: int | None` with:
```python
audio_tracks: list[dict] | None,
...
keep_quality: bool = False,
source_video_bitrate: int | None = None,
source_audio_bitrates: dict[int, int] | None = None,
audio_streams: list[dict] | None = None,
```

**Mapping logic** — replace the current `-map` and codec section (lines ~1482-1517) with:

```python
    if audio_tracks is not None:
        # Per-track audio mapping
        cmd += ["-map", "0:v?"]

        included_tracks = [t for t in audio_tracks if t["mode"] != "remove"]
        probe_streams = audio_streams or []

        for t in included_tracks:
            rel_idx = _audio_relative_index(probe_streams, t["index"])
            cmd += ["-map", f"0:a:{rel_idx}"]

        # Video codec
        if stream_copy:
            cmd += ["-c:v", "copy"]
        elif codec:
            encoder = _CODEC_TO_ENCODER.get(codec, codec)
            if encoder in _VIDEO_ENCODERS:
                cmd += ["-c:v", encoder]
                if keep_quality and source_video_bitrate and source_video_bitrate > 0:
                    cmd += ["-b:v", str(source_video_bitrate)]

        # Per-track audio codec
        bitrates = source_audio_bitrates or {}
        for out_idx, t in enumerate(included_tracks):
            if t["mode"] == "passthru":
                cmd += [f"-c:a:{out_idx}", "copy"]
            elif t["mode"] == "reencode":
                enc = _CODEC_TO_ENCODER.get(t.get("codec", "aac"), t.get("codec", "aac"))
                cmd += [f"-c:a:{out_idx}", enc]
                if keep_quality:
                    br = bitrates.get(t["index"], 0)
                    if br > 0:
                        cmd += [f"-b:a:{out_idx}", str(br)]
    else:
        # Legacy single-track fallback (backwards compat)
        # ... keep existing logic unchanged ...
```

**FLAC override** — update the existing FLAC stream-copy override (line ~1455-1462) to also handle `audio_tracks`:

```python
    if stream_copy and ext == ".flac":
        stream_copy = False
        codec = "flac"
        if not container:
            container = "flac"
        # Update audio tracks to reencode with flac
        if audio_tracks:
            audio_tracks = [
                {**t, "mode": "reencode", "codec": "flac"}
                if t["mode"] == "passthru" else t
                for t in audio_tracks
            ]
```

- [ ] **Step 5: Write test for all-tracks-removed (video-only output)**

```python
def test_cut_file_all_tracks_removed(tmp_path, monkeypatch):
    """When all audio tracks are removed, output should have no audio maps."""
    monkeypatch.setattr(cutter, "CUTTER_JOBS_DIR", str(tmp_path))
    monkeypatch.setattr(cutter, "collision_safe_path", lambda p: p)

    captured_cmd = {}

    class FakeProc:
        returncode = 0
        stderr = iter([])
        stdout = None
        def wait(self, timeout=None):
            pass
        def poll(self):
            return 0

    def fake_popen(cmd, **kw):
        captured_cmd["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(cutter.subprocess, "Popen", fake_popen)

    audio_streams = [{"index": 1, "bit_rate": 192000}]
    audio_tracks = [{"index": 1, "mode": "remove", "codec": None}]

    out = tmp_path / "output.mkv"
    out.touch()

    cutter.cut_file(
        filepath="/fake/input.mkv",
        in_point=0.0,
        out_point=30.0,
        output_path=str(out),
        stream_copy=True,
        codec=None,
        audio_tracks=audio_tracks,
        container="mkv",
        progress_cb=lambda msg: None,
        audio_streams=audio_streams,
    )

    cmd = captured_cmd["cmd"]
    map_args = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-map"]
    assert "0:v?" in map_args
    # No audio maps
    assert not any(a.startswith("0:a:") for a in map_args)
```

- [ ] **Step 6: Write test for audio-only file with per-track settings**

```python
def test_cut_file_audio_only_per_track(tmp_path, monkeypatch):
    """Audio-only file should use per-track settings without video codec args."""
    monkeypatch.setattr(cutter, "CUTTER_JOBS_DIR", str(tmp_path))
    monkeypatch.setattr(cutter, "collision_safe_path", lambda p: p)

    captured_cmd = {}

    class FakeProc:
        returncode = 0
        stderr = iter([])
        stdout = None
        def wait(self, timeout=None):
            pass
        def poll(self):
            return 0

    def fake_popen(cmd, **kw):
        captured_cmd["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(cutter.subprocess, "Popen", fake_popen)

    audio_streams = [{"index": 0, "bit_rate": 320000}]
    audio_tracks = [{"index": 0, "mode": "reencode", "codec": "aac"}]

    out = tmp_path / "output.m4a"
    out.touch()

    cutter.cut_file(
        filepath="/fake/input.flac",
        in_point=0.0,
        out_point=60.0,
        output_path=str(out),
        stream_copy=False,
        codec=None,  # No video codec for audio-only
        audio_tracks=audio_tracks,
        container="m4a",
        progress_cb=lambda msg: None,
        audio_streams=audio_streams,
    )

    cmd = captured_cmd["cmd"]
    # Video map present (harmless with ?)
    map_args = [cmd[i + 1] for i, v in enumerate(cmd) if v == "-map"]
    assert "0:v?" in map_args
    # Audio re-encoded
    a0_idx = cmd.index("-c:a:0")
    assert cmd[a0_idx + 1] == "aac"
    # No -c:v flag (no video to encode)
    assert "-c:v" not in cmd
```

- [ ] **Step 7: Write test for backwards compat (audio_tracks=None)**

```python
def test_cut_file_backwards_compat_no_audio_tracks(tmp_path, monkeypatch):
    """audio_tracks=None should fall back to legacy single-track behavior."""
    monkeypatch.setattr(cutter, "CUTTER_JOBS_DIR", str(tmp_path))
    monkeypatch.setattr(cutter, "collision_safe_path", lambda p: p)

    captured_cmd = {}

    class FakeProc:
        returncode = 0
        stderr = iter([])
        stdout = None
        def wait(self, timeout=None):
            pass
        def poll(self):
            return 0

    def fake_popen(cmd, **kw):
        captured_cmd["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(cutter.subprocess, "Popen", fake_popen)

    out = tmp_path / "output.mp4"
    out.touch()

    cutter.cut_file(
        filepath="/fake/input.mp4",
        in_point=0.0,
        out_point=30.0,
        output_path=str(out),
        stream_copy=True,
        codec=None,
        audio_tracks=None,  # Legacy — no per-track config
        container="mp4",
        progress_cb=lambda msg: None,
    )

    cmd = captured_cmd["cmd"]
    # Legacy path should use -c copy (global copy)
    assert "-c" in cmd and cmd[cmd.index("-c") + 1] == "copy"
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_cutter.py -v`
Expected: All pass

- [ ] **Step 9: Commit**

```bash
git add backend/app/cutter.py backend/tests/test_cutter.py
git commit -m "feat(cutter): multi-track audio mapping and keep-quality in cut_file"
```

---

### Task 4: Update `/cutter/cut` endpoint

**Files:**
- Modify: `backend/app/main.py:1071-1238` (`cutter_cut` endpoint)
- Modify: `backend/tests/test_main.py`

- [ ] **Step 1: Update endpoint parameters**

In `backend/app/main.py`, modify the `cutter_cut` function signature (line ~1072):

Remove:
```python
audio_codec: str = Form("", max_length=20),
audio_stream: int | None = Form(None),
```

Add:
```python
audio_tracks_json: str = Form("[]", alias="audio_tracks", max_length=5000),
keep_quality: bool = Form(False),
```

Add `import json` at the top if not already imported.

- [ ] **Step 2: Add audio_tracks parsing and validation**

After the existing codec/container validation block (line ~1155), add:

```python
    # Parse and validate audio tracks
    try:
        audio_tracks_parsed = json.loads(audio_tracks_json)
        if not isinstance(audio_tracks_parsed, list):
            raise ValueError("audio_tracks must be a JSON array")
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid audio_tracks: {exc}")

    valid_modes = {"passthru", "reencode", "remove"}
    for track in audio_tracks_parsed:
        if not isinstance(track, dict):
            raise HTTPException(status_code=422, detail="Each audio track must be an object")
        if track.get("mode") not in valid_modes:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid audio track mode: {track.get('mode')}",
            )
        if track["mode"] == "reencode":
            tc = track.get("codec", "")
            if tc and tc not in valid_audio_codecs:
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid audio track codec: {tc}",
                )
```

- [ ] **Step 3: Extract bitrates from existing probe and pass to cut_file**

The probe is already done at line ~1161 for duration validation. After that block, add:

```python
    # Extract bitrates for keep_quality
    source_video_bitrate = file_info.get("video_bitrate") if keep_quality else None
    source_audio_bitrates = {}
    probe_audio_streams = file_info.get("audio_streams", [])
    if keep_quality:
        for s in probe_audio_streams:
            source_audio_bitrates[s["index"]] = s.get("bit_rate", 0)
```

- [ ] **Step 4: Update cut_file call and job metadata**

Update the `cut_file` call (line ~1225) to use new params:

```python
final_path = cut_file(
    filepath=resolved,
    in_point=in_point,
    out_point=out_point,
    output_path=output_path,
    stream_copy=stream_copy,
    codec=codec or None,
    audio_tracks=audio_tracks_parsed if audio_tracks_parsed else None,
    container=container or None,
    progress_cb=progress_cb,
    keep_quality=keep_quality,
    source_video_bitrate=source_video_bitrate,
    source_audio_bitrates=source_audio_bitrates if source_audio_bitrates else None,
    audio_streams=probe_audio_streams,
    job_id=job_id,
    cancel_event=cancel_event,
)
```

Update the `cut_settings` metadata (line ~1200):

```python
meta["cut_settings"] = {
    "in_point": in_point,
    "out_point": out_point,
    "stream_copy": stream_copy,
    "codec": codec or None,
    "container": container or None,
    "audio_tracks": audio_tracks_parsed,
    "keep_quality": keep_quality,
    "output_name": output_name or None,
}
```

Also remove the old `audio_codec` cleanup line: `audio_codec = "" if audio_codec == "copy" else audio_codec` and remove `audio_codec`/`audio_stream` from `valid_audio_codecs` usage (keep the set itself for track validation).

- [ ] **Step 5: Add endpoint validation test for invalid audio codec**

Add to `backend/tests/test_main.py`:

```python
def test_cutter_cut_rejects_invalid_audio_track_codec(client, tmp_path, monkeypatch):
    """Endpoint should reject audio tracks with invalid codec values."""
    monkeypatch.setattr("app.main.CUTTER_MEDIA_DIR", str(tmp_path))
    (tmp_path / "test.mp4").touch()
    audio_tracks = json.dumps([{"index": 1, "mode": "reencode", "codec": "evil_codec"}])
    response = client.post(
        "/cutter/cut",
        data={
            "path": "test.mp4",
            "source": "server",
            "in_point": "0",
            "out_point": "30",
            "stream_copy": "false",
            "codec": "libx264",
            "container": "mp4",
            "audio_tracks": audio_tracks,
            "keep_quality": "false",
        },
    )
    assert response.status_code == 422
    assert "Invalid audio track codec" in response.json()["detail"]
```

Also verify existing tests still pass — the stream/preview tests don't touch cut params.

- [ ] **Step 6: Run full test suite**

Run: `cd backend && python -m pytest tests/ -v`
Expected: All pass

- [ ] **Step 7: Run ruff check**

Run: `cd backend && python -m ruff check app/`
Expected: All checks passed

- [ ] **Step 8: Commit**

```bash
git add backend/app/main.py backend/tests/test_main.py
git commit -m "feat(cutter): update /cutter/cut endpoint for multi-track audio and keep-quality"
```

---

## Chunk 3: Frontend — Types, State & API

### Task 5: Update TypeScript types

**Files:**
- Modify: `frontend/src/types.ts:59-132`

- [ ] **Step 1: Update types**

In `frontend/src/types.ts`:

Add `AudioTrackConfig` after `AudioStreamInfo`:

```ts
export interface AudioTrackConfig {
  streamIndex: number
  mode: 'passthru' | 'reencode' | 'remove'
  codec: string
}
```

Add `bit_rate` to `AudioStreamInfo` (matches the snake_case key from the backend JSON):

```ts
export interface AudioStreamInfo {
  index: number
  codec: string
  channels: number
  sample_rate: number
  bit_rate: number
  language: string
  title: string
}
```

**Important:** The field is `bit_rate` (snake_case) to match the backend JSON key. The existing `bitrate` field on `ProbeResult` is the overall file bitrate and stays as-is.

Add `video_bitrate` to `ProbeResult`:

```ts
export interface ProbeResult {
  duration: number
  video_codec: string | null
  audio_codec: string
  container: string
  bitrate: number
  video_bitrate: number | null
  width: number | null
  height: number | null
  display_aspect_ratio: string | null
  sample_rate: number
  needs_transcoding: boolean
  audio_streams: AudioStreamInfo[]
}
```

Update `CutterForm` — replace `audioCodec` and `audioStreamIndex` with new fields:

```ts
export interface CutterForm {
  source: 'server' | 'upload'
  directory: string
  filename: string
  inPoint: number
  outPoint: number
  outputName: string
  streamCopy: boolean
  codec: string
  container: string
  audioTracks: AudioTrackConfig[]
  keepQuality: boolean
}
```

Update `CutJobSettings`:

```ts
export interface CutJobSettings {
  in_point: number
  out_point: number
  stream_copy: boolean
  codec: string | null
  container: string | null
  audio_tracks: { index: number; mode: string; codec: string | null }[]
  keep_quality: boolean
  output_name: string | null
}
```

- [ ] **Step 2: Verify build compiles (expect errors — dependents not yet updated)**

Run: `cd frontend && npx tsc --noEmit 2>&1 | head -30`
Expected: Type errors in CutterPanel, OutputSettings, App.tsx (these will be fixed in subsequent tasks)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types.ts
git commit -m "feat(cutter): update TypeScript types for multi-track audio and keep-quality"
```

---

### Task 6: Update App.tsx initial state

**Files:**
- Modify: `frontend/src/App.tsx:23-41`

- [ ] **Step 1: Update INITIAL_CUTTER_STATE**

Replace the form object in `INITIAL_CUTTER_STATE`:

```ts
const INITIAL_CUTTER_STATE: CutterPersistedState = {
  form: {
    source: 'server',
    directory: '',
    filename: '',
    inPoint: 0,
    outPoint: 0,
    outputName: '',
    streamCopy: true,
    codec: 'libx264',
    container: 'mp4',
    audioTracks: [],
    keepQuality: false,
  },
  directories: [],
  search: '',
  serverState: { ...EMPTY_SOURCE_STATE },
  uploadState: { ...EMPTY_SOURCE_STATE },
}
```

Note: `codec` default changes from `'aac'` to `'libx264'` (video encoder, since preselection will override on probe anyway). `audioTracks` starts empty — populated on probe.

- [ ] **Step 2: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat(cutter): update initial cutter state for new form fields"
```

---

### Task 7: Rewrite OutputSettings component

**Files:**
- Rewrite: `frontend/src/components/cutter/OutputSettings.tsx`

- [ ] **Step 1: Rewrite OutputSettings**

Replace the entire file with:

```tsx
import FormSection from '@/components/ui/FormSection'
import ToggleSwitch from '@/components/ui/ToggleSwitch'
import SegmentedControl from '@/components/ui/SegmentedControl'
import type { AudioTrackConfig, AudioStreamInfo } from '@/types'

interface OutputSettingsProps {
  outputName: string
  streamCopy: boolean
  codec: string
  container: string
  keepQuality: boolean
  audioTracks: AudioTrackConfig[]
  audioStreams: AudioStreamInfo[]
  isVideo: boolean
  sourceVideoBitrate: number | null
  onOutputNameChange: (name: string) => void
  onStreamCopyChange: (value: boolean) => void
  onCodecChange: (codec: string) => void
  onContainerChange: (container: string) => void
  onKeepQualityChange: (value: boolean) => void
  onAudioTracksChange: (tracks: AudioTrackConfig[]) => void
}

const videoCodecOptions = [
  { label: 'H.264', value: 'libx264' },
  { label: 'H.265', value: 'libx265' },
  { label: 'VP9', value: 'libvpx-vp9' },
  { label: 'AV1', value: 'libaom-av1' },
]

const audioCodecOptions = [
  { label: 'AAC', value: 'aac' },
  { label: 'AC3', value: 'ac3' },
  { label: 'FLAC', value: 'flac' },
  { label: 'Opus', value: 'opus' },
  { label: 'MP3', value: 'mp3' },
]

const videoContainerOptions = [
  { label: 'MP4', value: 'mp4' },
  { label: 'MKV', value: 'mkv' },
  { label: 'WebM', value: 'webm' },
  { label: 'MOV', value: 'mov' },
]

const audioContainerOptions = [
  { label: 'MP4', value: 'mp4' },
  { label: 'MKA', value: 'mka' },
  { label: 'FLAC', value: 'flac' },
  { label: 'OGG', value: 'ogg' },
  { label: 'MP3', value: 'mp3' },
]

const modeOptions = [
  { label: 'Passthru', value: 'passthru' },
  { label: 'Re-encode', value: 'reencode' },
  { label: 'Remove', value: 'remove' },
]

function formatBitrate(bps: number): string {
  if (bps >= 1_000_000) return `${(bps / 1_000_000).toFixed(1)} Mbps`
  if (bps >= 1_000) return `${(bps / 1_000).toFixed(0)} kbps`
  return `${bps} bps`
}

function formatTrackLabel(stream: AudioStreamInfo, i: number): string {
  let label = `Track ${i + 1}: ${stream.codec.toUpperCase()} ${stream.channels}ch`
  if (stream.language) label += ` (${stream.language})`
  if (stream.title) label += ` — ${stream.title}`
  if (stream.bit_rate > 0) label += ` · ${formatBitrate(stream.bit_rate)}`
  return label
}

export default function OutputSettings({
  outputName,
  streamCopy,
  codec,
  container,
  keepQuality,
  audioTracks,
  audioStreams,
  isVideo,
  sourceVideoBitrate,
  onOutputNameChange,
  onStreamCopyChange,
  onCodecChange,
  onContainerChange,
  onKeepQualityChange,
  onAudioTracksChange,
}: OutputSettingsProps) {
  const containerOptions = isVideo ? videoContainerOptions : audioContainerOptions

  const updateTrack = (streamIndex: number, updates: Partial<AudioTrackConfig>) => {
    onAudioTracksChange(
      audioTracks.map((t) => (t.streamIndex === streamIndex ? { ...t, ...updates } : t)),
    )
  }

  const hasReencode = !streamCopy || audioTracks.some((t) => t.mode === 'reencode')

  return (
    <div className="space-y-2">
      <FormSection label="Output filename">
        <input
          type="text"
          className="input-field input-emerald"
          value={outputName}
          onChange={(e) => onOutputNameChange(e.target.value)}
          placeholder="Same as original"
        />
      </FormSection>

      <FormSection label="Encoding">
        <ToggleSwitch
          checked={streamCopy}
          onChange={onStreamCopyChange}
          color="emerald"
          label={
            streamCopy
              ? hasReencode
                ? 'Stream Copy (video)'
                : 'Stream Copy (fast, lossless)'
              : 'Re-encode (precise, lossy)'
          }
        />
      </FormSection>

      {hasReencode && (
        <FormSection label="Match Source Quality">
          <ToggleSwitch
            checked={keepQuality}
            onChange={onKeepQualityChange}
            color="emerald"
            label={keepQuality ? 'On — matching source bitrate' : 'Off — encoder defaults'}
          />
          {keepQuality && sourceVideoBitrate != null && sourceVideoBitrate > 0 && isVideo && (
            <p className="mt-1 text-[0.68rem] text-white/35">
              Source video: {formatBitrate(sourceVideoBitrate)}
            </p>
          )}
          <p className="mt-1 text-[0.68rem] text-white/25">
            Re-encoding always causes some quality loss vs stream copy
          </p>
        </FormSection>
      )}

      {!streamCopy && isVideo && (
        <FormSection label="Video Codec">
          <SegmentedControl
            options={videoCodecOptions}
            value={codec}
            onChange={onCodecChange}
            color="emerald"
          />
        </FormSection>
      )}

      <FormSection label="Container">
        <SegmentedControl
          options={containerOptions}
          value={container}
          onChange={onContainerChange}
          color="emerald"
        />
      </FormSection>

      {audioStreams.length > 0 && (
        <FormSection label="Audio Tracks">
          <div className="space-y-2">
            {audioStreams.map((stream, i) => {
              const track = audioTracks.find((t) => t.streamIndex === stream.index)
              const mode = track?.mode ?? 'passthru'
              const trackCodec = track?.codec ?? 'aac'

              return (
                <div
                  key={stream.index}
                  className={`rounded-lg border px-3 py-2 transition-colors ${
                    mode === 'remove'
                      ? 'border-red-500/15 bg-red-500/5 opacity-60'
                      : 'border-[var(--glass-border)] bg-[var(--glass-bg)]'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-[0.75rem] text-white/70">
                      {formatTrackLabel(stream, i)}
                    </span>
                    <select
                      value={mode}
                      onChange={(e) =>
                        updateTrack(stream.index, {
                          mode: e.target.value as AudioTrackConfig['mode'],
                        })
                      }
                      className="shrink-0 cursor-pointer rounded-md border border-[var(--border)] bg-[var(--bg-input)] px-2 py-1 text-[0.72rem] text-[var(--text-primary)] outline-none transition hover:border-[var(--glass-border-hover)]"
                    >
                      {modeOptions.map((opt) => (
                        <option key={opt.value} value={opt.value}>
                          {opt.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  {mode === 'reencode' && (
                    <div className="mt-2">
                      <SegmentedControl
                        options={audioCodecOptions}
                        value={trackCodec}
                        onChange={(v) => updateTrack(stream.index, { codec: v })}
                        color="emerald"
                      />
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </FormSection>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/cutter/OutputSettings.tsx
git commit -m "feat(cutter): rewrite OutputSettings with per-track audio and keep-quality"
```

---

### Task 8: Update CutterPanel — preselection, state, serialization

**Files:**
- Modify: `frontend/src/components/CutterPanel.tsx`
- Delete: `frontend/src/components/cutter/AudioTrackSelect.tsx`

- [ ] **Step 1: Add codec-to-encoder mapping constant**

At the top of `CutterPanel.tsx` (module level, near other constants), add:

```ts
const SOURCE_CODEC_TO_ENCODER: Record<string, string> = {
  h264: 'libx264',
  hevc: 'libx265',
  h265: 'libx265',
  vp9: 'libvpx-vp9',
  av1: 'libaom-av1',
}

const EXT_TO_CONTAINER: Record<string, string> = {
  '.mp4': 'mp4',
  '.mkv': 'mkv',
  '.webm': 'webm',
  '.mov': 'mov',
  '.mka': 'mka',
  '.flac': 'flac',
  '.ogg': 'ogg',
  '.mp3': 'mp3',
  '.m4a': 'mp4',
}
```

- [ ] **Step 2: Remove AudioTrackSelect import and usage**

Remove line 7: `import AudioTrackSelect from '@/components/cutter/AudioTrackSelect'`

Remove the `AudioTrackSelect` JSX block (lines ~836-845):
```tsx
{probe.audio_streams && probe.audio_streams.length > 1 && (
  <FormSection label="Audio Track">
    <AudioTrackSelect ... />
  </FormSection>
)}
```

- [ ] **Step 3: Add preselection logic in `loadFileData`**

All probe completions go through `loadFileData` (line ~235). Add preselection there, in the success path after `setSource({ probe: probeData, ... })` (line ~244). Replace the existing `setPersisted` call (line ~250-252) with:

```ts
const ext = path.substring(path.lastIndexOf('.')).toLowerCase()
const sourceVideoCodec = probeData.video_codec?.toLowerCase() ?? ''
setPersisted((prev) => ({
  form: {
    ...prev.form,
    inPoint: 0,
    outPoint: probeData.duration,
    codec: SOURCE_CODEC_TO_ENCODER[sourceVideoCodec] ?? 'libx264',
    container: EXT_TO_CONTAINER[ext] ?? 'mp4',
    audioTracks: (probeData.audio_streams ?? []).map((s) => ({
      streamIndex: s.index,
      mode: 'passthru' as const,
      codec: 'aac',
    })),
    keepQuality: false,
  },
}))
```

This is the **single place** where preselection happens — no duplication needed since `loadFileData` is called for both server file select and upload.

For the job reopen handler (~line 330), the existing form restoration code will populate `audioTracks` from saved `cut_settings` — see Step 7.

Import `AudioTrackConfig` type from `@/types` if not already imported.

- [ ] **Step 4: Update OutputSettings props**

Replace the `OutputSettings` JSX (lines ~857-869) with:

```tsx
<OutputSettings
  outputName={form.outputName}
  streamCopy={form.streamCopy}
  codec={form.codec}
  container={form.container}
  keepQuality={form.keepQuality}
  audioTracks={form.audioTracks}
  audioStreams={probe.audio_streams ?? []}
  isVideo={isVideo}
  sourceVideoBitrate={probe.video_bitrate ?? null}
  onOutputNameChange={(v) => update('outputName', v)}
  onStreamCopyChange={(v) => update('streamCopy', v)}
  onCodecChange={(v) => update('codec', v)}
  onContainerChange={(v) => update('container', v)}
  onKeepQualityChange={(v) => update('keepQuality', v)}
  onAudioTracksChange={(v) => update('audioTracks', v)}
/>
```

- [ ] **Step 5: Update handleCut serialization**

In `handleCut` (line ~485), update the params construction:

```ts
const params: Record<string, string> = {
  path: filePath,
  source: form.source,
  job_id: jobId,
  in_point: String(form.inPoint),
  out_point: String(form.outPoint),
  stream_copy: String(form.streamCopy),
}
if (form.outputName) params.output_name = form.outputName
if (!form.streamCopy && form.codec) {
  params.codec = form.codec
}
params.container = form.container
params.keep_quality = String(form.keepQuality)

// Serialize audio tracks with backend field names
params.audio_tracks = JSON.stringify(
  form.audioTracks.map((t) => ({
    index: t.streamIndex,
    mode: t.mode,
    codec: t.mode === 'reencode' ? t.codec : null,
  })),
)
```

Remove the old `audioCodec`, `audio_codec`, and `audio_stream` lines.

- [ ] **Step 6: Remove selectedAudioStreamIndex logic**

Remove or simplify the `selectedAudioStreamIndex` and `streamAudioIndex` computed values (lines ~549-566). The `streamAudioIndex` is still used for the preview stream URL — keep it but derive from audio tracks:

```ts
const streamAudioIndex = (() => {
  if (!probe?.audio_streams?.length) return null
  // Use first non-removed track for preview
  const firstActive = form.audioTracks.find((t) => t.mode !== 'remove')
  if (!firstActive) return null
  const defaultIdx = probe.audio_streams[0]?.index ?? null
  if (!transcodePreviewEnabled && firstActive.streamIndex === defaultIdx) return null
  return firstActive.streamIndex
})()
```

- [ ] **Step 7: Clean up audioStreamIndex resets**

Search for `audioStreamIndex` in the file. Remove/update any `update('audioStreamIndex', ...)` calls (lines ~251, 274, 287, 425). These are no longer needed since `audioTracks` is initialized from probe data.

Also update the job reopen handler (line ~335) — replace `audioCodec: settings?.audio_codec ?? 'copy'` and `audioStreamIndex` with the new fields:

```ts
audioTracks: (settings?.audio_tracks ?? []).map(
  (t: { index: number; mode: string; codec: string | null }) => ({
    streamIndex: t.index,
    mode: t.mode as AudioTrackConfig['mode'],
    codec: t.codec ?? 'aac',
  }),
),
keepQuality: settings?.keep_quality ?? false,
```

- [ ] **Step 8: Delete AudioTrackSelect.tsx**

```bash
rm frontend/src/components/cutter/AudioTrackSelect.tsx
```

- [ ] **Step 9: Build and test**

Run: `cd frontend && npm run build`
Expected: Build succeeds

Run: `cd frontend && npm run test`
Expected: All tests pass

- [ ] **Step 10: Run full backend tests too**

Run: `cd backend && python -m pytest tests/ -v`
Expected: All pass

- [ ] **Step 11: Commit**

```bash
git add frontend/src/components/CutterPanel.tsx frontend/src/App.tsx frontend/src/types.ts
git rm frontend/src/components/cutter/AudioTrackSelect.tsx
git commit -m "feat(cutter): per-track audio control, preselection, and keep-quality toggle"
```

---

## Chunk 4: Verification & Cleanup

### Task 9: End-to-end verification

- [ ] **Step 1: Full build check**

Run: `cd frontend && npm run build`
Expected: Build succeeds with no errors

- [ ] **Step 2: Full test suite**

Run: `cd frontend && npm run test`
Expected: All tests pass

Run: `cd backend && python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 3: Lint check**

Run: `cd backend && python -m ruff check app/`
Expected: All checks passed

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 4: Format**

Run: `cd frontend && npm run format`
Expected: Files formatted
