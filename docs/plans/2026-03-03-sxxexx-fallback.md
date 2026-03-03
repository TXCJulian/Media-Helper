# SxxExx Pattern Fallback Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add episode number extraction from filenames (SxxExx, Exx, plain number) so DVD/Blu-ray files without episode titles get correctly matched to TMDB episodes.

**Architecture:** Two new pure functions (`extract_episode_number`, `is_pattern_only`) plus a modified matching loop in `rename_episodes()` that classifies files and applies direct pattern matching or fuzzy matching with pattern fallback.

**Tech Stack:** Python 3.12, pytest, regex

---

### Task 1: Test `extract_episode_number()`

**Files:**
- Modify: `backend/tests/test_rename_episodes.py`

**Step 1: Write failing tests**

Add to the end of `backend/tests/test_rename_episodes.py`:

```python
from app.rename_episodes import extract_episode_number


class TestExtractEpisodeNumber:
    def test_sxxexx_standard(self):
        assert extract_episode_number("S01E05.mkv") == 5
        assert extract_episode_number("S03E28.mkv") == 28

    def test_sxxexx_lowercase(self):
        assert extract_episode_number("s01e05.mkv") == 5

    def test_sxxexx_short(self):
        assert extract_episode_number("s1e3.mkv") == 3

    def test_exx_pattern(self):
        assert extract_episode_number("E05.mkv") == 5
        assert extract_episode_number("e5.mkv") == 5

    def test_plain_number(self):
        assert extract_episode_number("03.mkv") == 3
        assert extract_episode_number("3.mkv") == 3

    def test_no_pattern(self):
        assert extract_episode_number("My Episode Title.mkv") is None
        assert extract_episode_number("Some Random Name.mkv") is None

    def test_sxxexx_with_text(self):
        """SxxExx embedded in text still extracts the number."""
        assert extract_episode_number("My.Show.S01E05.Episode.Title.mkv") == 5

    def test_exx_with_text(self):
        assert extract_episode_number("Show E05 Title.mkv") == 5

    def test_plain_number_not_extracted_from_text(self):
        """Plain numbers should NOT be extracted if other text is present."""
        assert extract_episode_number("The 100.mkv") is None
        assert extract_episode_number("Episode 5 Title.mkv") is None
```

**Step 2: Run tests to verify they fail**

Run: `cd e:/Repos/Jellyfin_Media-Renamer/backend && python -m pytest tests/test_rename_episodes.py::TestExtractEpisodeNumber -v`
Expected: FAIL — `ImportError: cannot import name 'extract_episode_number'`

---

### Task 2: Implement `extract_episode_number()`

**Files:**
- Modify: `backend/app/rename_episodes.py` (add after `clean_filename` function, around line 42)

**Step 1: Implement the function**

Add after the `clean_filename` function:

```python
def extract_episode_number(filename: str) -> int | None:
    """Extract episode number from filename using SxxExx, Exx, or plain number patterns.

    Returns the episode number if found, None otherwise.
    Plain numbers only match when they are the entire base filename.
    """
    base = os.path.splitext(filename)[0]

    # Pattern 1: SxxExx anywhere in filename
    m = re.search(r"[Ss]\d{1,2}[Ee](\d{1,3})", base)
    if m:
        return int(m.group(1))

    # Pattern 2: Exx anywhere in filename (but not preceded by alphanumeric)
    m = re.search(r"(?<![a-zA-Z0-9])[Ee](\d{1,3})(?![a-zA-Z0-9])", base)
    if m:
        return int(m.group(1))

    # Pattern 3: Plain number — only if it IS the entire base name
    m = re.fullmatch(r"(\d{1,3})", base.strip())
    if m:
        return int(m.group(1))

    return None
```

**Step 2: Update the import in test file**

The import `from app.rename_episodes import extract_episode_number` was already added in Task 1.

**Step 3: Run tests to verify they pass**

Run: `cd e:/Repos/Jellyfin_Media-Renamer/backend && python -m pytest tests/test_rename_episodes.py::TestExtractEpisodeNumber -v`
Expected: All PASS

**Step 4: Commit**

```
feat: add extract_episode_number() for SxxExx/Exx/plain number parsing
```

---

### Task 3: Test `is_pattern_only()`

**Files:**
- Modify: `backend/tests/test_rename_episodes.py`

**Step 1: Write failing tests**

Add to the end of `backend/tests/test_rename_episodes.py`:

```python
from app.rename_episodes import is_pattern_only


class TestIsPatternOnly:
    def test_sxxexx_only(self):
        assert is_pattern_only("S03E28.mkv") is True
        assert is_pattern_only("s1e3.mkv") is True

    def test_exx_only(self):
        assert is_pattern_only("E05.mkv") is True
        assert is_pattern_only("e5.mkv") is True

    def test_plain_number_only(self):
        assert is_pattern_only("03.mkv") is True
        assert is_pattern_only("3.mkv") is True

    def test_sxxexx_with_text(self):
        assert is_pattern_only("S03E28 Some Title.mkv") is False
        assert is_pattern_only("My Episode S01E03.mkv") is False

    def test_no_pattern(self):
        assert is_pattern_only("My Episode Title.mkv") is False

    def test_sxxexx_with_dashes_dots(self):
        """Separators without text are still pattern-only."""
        assert is_pattern_only("S03E28 - .mkv") is True
        assert is_pattern_only("S03E28..mkv") is True
```

**Step 2: Run tests to verify they fail**

Run: `cd e:/Repos/Jellyfin_Media-Renamer/backend && python -m pytest tests/test_rename_episodes.py::TestIsPatternOnly -v`
Expected: FAIL — `ImportError: cannot import name 'is_pattern_only'`

---

### Task 4: Implement `is_pattern_only()`

**Files:**
- Modify: `backend/app/rename_episodes.py` (add after `extract_episode_number`)

**Step 1: Implement the function**

```python
def is_pattern_only(filename: str) -> bool:
    """Check if filename contains only an episode pattern with no meaningful text."""
    base = os.path.splitext(filename)[0]
    # Strip all recognized patterns
    stripped = re.sub(r"[Ss]\d{1,2}[Ee]\d{1,3}", "", base)
    stripped = re.sub(r"(?<![a-zA-Z0-9])[Ee]\d{1,3}(?![a-zA-Z0-9])", "", stripped)
    stripped = re.sub(r"\d+", "", stripped)
    # Strip non-alphanumeric
    stripped = re.sub(r"[^a-zA-Z]", "", stripped)
    return len(stripped) == 0
```

**Step 2: Run tests to verify they pass**

Run: `cd e:/Repos/Jellyfin_Media-Renamer/backend && python -m pytest tests/test_rename_episodes.py::TestIsPatternOnly -v`
Expected: All PASS

**Step 3: Commit**

```
feat: add is_pattern_only() to classify pattern-only filenames
```

---

### Task 5: Test the modified matching flow in `rename_episodes()`

**Files:**
- Modify: `backend/tests/test_rename_episodes.py`
- Modify: `backend/tests/conftest.py`

**Step 1: Add a conftest fixture for pattern-only files**

Add to `backend/tests/conftest.py`:

```python
@pytest.fixture
def tmp_tvshow_pattern_dir(tmp_media_dir):
    """Create a TV show directory with pattern-only filenames (DVD/Blu-ray style)."""
    show_dir = tmp_media_dir / "TV Shows" / "PatternShow" / "Season 01"
    show_dir.mkdir(parents=True)

    for name in ["S01E01.mkv", "S01E02.mkv", "S01E03.mkv"]:
        (show_dir / name).write_bytes(b"\x00" * 100)

    return show_dir


@pytest.fixture
def tmp_tvshow_mixed_dir(tmp_media_dir):
    """Create a TV show directory with mixed filenames — some with titles, some pattern-only."""
    show_dir = tmp_media_dir / "TV Shows" / "MixedShow" / "Season 01"
    show_dir.mkdir(parents=True)

    for name in ["Episode.One.S01E01.mkv", "S01E02.mkv", "Wrong.Name.S01E03.mkv"]:
        (show_dir / name).write_bytes(b"\x00" * 100)

    return show_dir
```

**Step 2: Write integration tests**

Add to the end of `backend/tests/test_rename_episodes.py`:

```python
from unittest.mock import patch

MOCK_SEASON_EPISODES = [
    {"episode_number": 1, "name": "Episode One"},
    {"episode_number": 2, "name": "Episode Two"},
    {"episode_number": 3, "name": "Episode Three"},
]


class TestRenameEpisodesPatternFallback:
    """Integration tests for the SxxExx pattern fallback matching."""

    @patch("app.rename_episodes.tmdb_get_season", return_value=MOCK_SEASON_EPISODES)
    @patch("app.rename_episodes.tmdb_search_show", return_value=12345)
    def test_pattern_only_files_match_by_number(
        self, mock_search, mock_season, tmp_tvshow_pattern_dir
    ):
        from app.rename_episodes import rename_episodes

        logs, error = rename_episodes(
            series="PatternShow",
            season=1,
            directory=str(tmp_tvshow_pattern_dir),
            dry_run=True,
        )
        assert error is None
        # All 3 files should be matched (renamed or already correct)
        rename_or_ok = [l for l in logs if "[DRYRUN]" in l or "[  OK  ]" in l]
        assert len(rename_or_ok) == 3
        # Should contain pattern-match indicator
        pattern_matches = [l for l in logs if "pattern" in l.lower()]
        assert len(pattern_matches) >= 1

    @patch("app.rename_episodes.tmdb_get_season", return_value=MOCK_SEASON_EPISODES)
    @patch("app.rename_episodes.tmdb_search_show", return_value=12345)
    def test_mixed_files_fuzzy_first_then_fallback(
        self, mock_search, mock_season, tmp_tvshow_mixed_dir
    ):
        from app.rename_episodes import rename_episodes

        logs, error = rename_episodes(
            series="MixedShow",
            season=1,
            directory=str(tmp_tvshow_mixed_dir),
            dry_run=True,
        )
        assert error is None
        # All 3 should match — no skips
        skips = [l for l in logs if "[ SKIP ]" in l]
        assert len(skips) == 0

    @patch("app.rename_episodes.tmdb_get_season", return_value=MOCK_SEASON_EPISODES)
    @patch("app.rename_episodes.tmdb_search_show", return_value=12345)
    def test_pattern_only_nonexistent_episode_skipped(
        self, mock_search, mock_season, tmp_media_dir
    ):
        from app.rename_episodes import rename_episodes

        show_dir = tmp_media_dir / "TV Shows" / "SkipShow" / "Season 01"
        show_dir.mkdir(parents=True)
        (show_dir / "S01E99.mkv").write_bytes(b"\x00" * 100)

        logs, error = rename_episodes(
            series="SkipShow",
            season=1,
            directory=str(show_dir),
            dry_run=True,
        )
        assert error is None
        skips = [l for l in logs if "[ SKIP ]" in l]
        assert len(skips) == 1
```

**Step 3: Run tests to verify they fail**

Run: `cd e:/Repos/Jellyfin_Media-Renamer/backend && python -m pytest tests/test_rename_episodes.py::TestRenameEpisodesPatternFallback -v`
Expected: FAIL — tests fail because `rename_episodes()` doesn't use pattern matching yet

---

### Task 6: Modify `rename_episodes()` matching loop

**Files:**
- Modify: `backend/app/rename_episodes.py` (the main matching loop, lines ~130-148)

**Step 1: Replace the matching loop**

Replace the current matching loop (from `assignments = []` through the `assign_seq` block) with:

```python
    # Build episode lookup by number for direct pattern matching
    ep_by_num = {e["num"]: e for e in remaining}

    assignments = []
    used_nums = set()

    # Phase 1: Handle pattern-only files with direct episode number mapping
    pattern_only_files = []
    text_files = []
    for f in files:
        if is_pattern_only(f):
            pattern_only_files.append(f)
        else:
            text_files.append(f)

    for f in pattern_only_files:
        ep_num = extract_episode_number(f)
        if ep_num is not None and ep_num in ep_by_num and ep_num not in used_nums:
            ep = ep_by_num[ep_num]
            used_nums.add(ep_num)
            assignments.append((f, ep["num"], ep["title"], -1.0))  # -1.0 signals pattern-match
        else:
            reason = f"episode {ep_num} not found in TMDB" if ep_num else "no pattern detected"
            assignments.append((f, None, None, 0.0))

    # Remove used episodes from the fuzzy matching pool
    unused = [e for e in remaining if e["num"] not in used_nums]

    # Phase 2: Fuzzy match text files, with pattern fallback
    for f in text_files:
        n = normalize_string(f)
        best_idx, best_score = best_match(n, [e["title_norm"] for e in unused])
        if best_idx >= 0 and best_score >= threshold:
            ep = unused.pop(best_idx)
            used_nums.add(ep["num"])
            assignments.append((f, ep["num"], ep["title"], best_score))
        else:
            # Fallback: try extracting episode number from filename
            ep_num = extract_episode_number(f)
            if ep_num is not None and ep_num in ep_by_num and ep_num not in used_nums:
                ep = ep_by_num[ep_num]
                used_nums.add(ep_num)
                # Remove from unused pool too
                unused = [e for e in unused if e["num"] != ep_num]
                assignments.append((f, ep["num"], ep["title"], -2.0))  # -2.0 signals fallback-pattern
            else:
                assignments.append((f, None, None, best_score))

    # Assign sequence for remaining unmatched files (existing behavior)
    if assign_seq:
        leftovers = [e for e in remaining if e["num"] not in used_nums]
        for i, (f, num, title, score) in enumerate(assignments):
            if num is None and leftovers:
                ep = leftovers.pop(0)
                assignments[i] = (f, ep["num"], ep["title"], score)
```

**Step 2: Update the logging section**

In the logging loop (the `for f, num, title, score in assignments:` block), update the log messages to distinguish match types. Replace the existing rename/dryrun log lines:

Change the log format lines to use a helper that picks the right match label:

```python
    for f, num, title, score in assignments:
        if num is None:
            reason = "no confident match"
            logs.append(f"[ SKIP ]\t'{f}' {reason} (score={score:.2f})")
            skipped_count += 1
            continue
        ext = os.path.splitext(f)[1]
        safe_title = clean_filename(title)
        new_name = f"S{season:02d}E{num:02d} {safe_title}{ext}"
        src = os.path.join(directory, f)
        dst = os.path.join(directory, new_name)

        # Determine match type label for logging
        if score == -1.0:
            match_info = "(pattern-match)"
        elif score == -2.0:
            match_info = "(fallback-pattern)"
        else:
            match_info = f"(match={score:.2f})"

        if os.path.abspath(src) == os.path.abspath(dst):
            logs.append(f"[  OK  ]\t'{f}' already correct")
            already_correct_count += 1
            continue
        else:
            dst = collision_safe_path(dst)
            if not dry_run:
                logs.append(
                    f"[RENAME]\t'{f}' -> {os.path.basename(dst)}  {match_info}"
                )
                renamed_count += 1
                os.rename(src, dst)
                old_nfo = os.path.splitext(src)[0] + ".nfo"
                if os.path.exists(old_nfo):
                    try:
                        os.remove(old_nfo)
                    except Exception as e:
                        logs.append(f"\t[!] .nfo deletion failed: {e}")
            else:
                logs.append(
                    f"[DRYRUN]\tWould rename '{f}' -> {os.path.basename(dst)}  {match_info}"
                )
                renamed_count += 1
                old_nfo = os.path.splitext(src)[0] + ".nfo"
                if os.path.exists(old_nfo):
                    logs.append(
                        f"[DELETE]\tWould remove .nfo file: {os.path.basename(old_nfo)}"
                    )
```

**Step 3: Run all episode tests**

Run: `cd e:/Repos/Jellyfin_Media-Renamer/backend && python -m pytest tests/test_rename_episodes.py -v`
Expected: All PASS

**Step 4: Commit**

```
feat: add SxxExx/Exx/number pattern fallback to episode matching

Pattern-only files (e.g. S01E03.mkv, 03.mkv) are now directly matched
to TMDB episodes by number. Files with text try fuzzy matching first,
then fall back to pattern extraction if below threshold.
```

---

### Task 7: Run full test suite and verify no regressions

**Files:** None (verification only)

**Step 1: Run all backend tests**

Run: `cd e:/Repos/Jellyfin_Media-Renamer/backend && python -m pytest tests/ -v`
Expected: All PASS, no regressions

**Step 2: Run frontend tests**

Run: `cd e:/Repos/Jellyfin_Media-Renamer/frontend && npm run test`
Expected: All PASS (no frontend changes were made)

**Step 3: Commit if any fixes were needed**

Only if regressions were found and fixed.
