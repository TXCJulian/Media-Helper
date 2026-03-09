# SxxExx Pattern Fallback for Episode Renaming

## Problem

Files from DVDs/Blu-rays often lack episode titles in their filenames (e.g. `S03E28.mkv`, `03.mkv`). The current fuzzy title matching produces empty strings for these after normalization, resulting in no match. The only workaround is "Assign Sequence" which maps files 1:1 in order — but breaks if one file is missing or misordered.

## Approach: Pre-pass extraction with fallback chain

Classify each file, then apply the appropriate matching strategy.

### New functions in `rename_episodes.py`

**`extract_episode_number(filename: str) -> int | None`**
Strips extension, then tries anchored regex patterns in order:
1. `SxxExx` / `SxEx` (case-insensitive) — extracts episode number
2. `Exx` / `Ex` (case-insensitive) — extracts episode number
3. Plain number — extracts as episode number

Returns `None` if no pattern matches.

**`is_pattern_only(filename: str) -> bool`**
Strips extension, removes all recognized patterns and non-alphanumeric chars. Returns `True` if nothing meaningful remains.

### Modified matching flow

```
for each file:
    if is_pattern_only(file):
        extract episode number -> direct TMDB lookup by number
        if episode exists in season -> assign
        else -> skip ("episode N not found in TMDB")
    else:
        fuzzy match (existing logic)
        if above threshold -> assign
        else:
            extract episode number as fallback
            if found -> assign (logged as "fallback-pattern")
            else -> skip (existing behavior)
```

Directly-mapped files are removed from the `unused` pool to prevent double-assignment.

### Recognized patterns

| Pattern | Example | Extracts |
|---------|---------|----------|
| `SxxExx` | `S03E28.mkv`, `s1e3.mkv` | episode 28, 3 |
| `Exx` | `E05.mkv`, `e5.mkv` | episode 5 |
| Plain number | `03.mkv`, `3.mkv` | episode 3 |

Season number always comes from user form input.

### Logging

- `(pattern-match)` — pattern-only file, direct match
- `(match=0.85)` — fuzzy title match (unchanged)
- `(fallback-pattern)` — fuzzy failed, pattern extraction succeeded

### Scope

- Backend only: changes confined to `rename_episodes.py`
- No API changes, no frontend changes
- `assign_seq` remains independent as last resort for unmatched files
- Existing tests updated, new test cases for pattern extraction
