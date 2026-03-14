import os
import re
import logging
import unicodedata
import requests
import urllib.parse
from difflib import SequenceMatcher
from typing import Optional
from app.config import TMDB_API_KEY as API_KEY, VALID_VIDEO_EXT
from app.fs_utils import flush_directory, collision_safe_path

logger = logging.getLogger(__name__)


def strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def de_translit(s: str) -> str:
    s = s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    s = s.replace("Ä", "Ae").replace("Ö", "Oe").replace("Ü", "Ue")
    return s


def normalize_string(s: str) -> str:
    base, ext = os.path.splitext(s)
    if ext.lower() in VALID_VIDEO_EXT:
        s = base
    s = re.sub(r"(?i)s\d{1,2}e\d{1,2}", " ", s)
    s = strip_accents(s)
    s = de_translit(s)
    s = s.lower()
    s = re.sub(r"[^a-z0-9\.]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def clean_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()


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


def tmdb_search_show(series_name: str, language: str) -> int:
    url = f"https://api.themoviedb.org/3/search/tv?api_key={API_KEY}&query={urllib.parse.quote(series_name)}&language={language}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data.get("results"):
        raise ValueError(f"Serie '{series_name}' nicht gefunden (TMDB).")
    return data["results"][0]["id"]


def tmdb_get_season(show_id: int, season: int, language: str) -> list[dict]:
    url = f"https://api.themoviedb.org/3/tv/{show_id}/season/{season}?api_key={API_KEY}&language={language}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    data = r.json()
    episodes = data.get("episodes", [])
    # Fallback auf Englisch, wenn Titel fehlen
    if any(not (ep.get("name") or "").strip() for ep in episodes):
        url_en = f"https://api.themoviedb.org/3/tv/{show_id}/season/{season}?api_key={API_KEY}&language=en"
        r2 = requests.get(url_en, timeout=30)
        r2.raise_for_status()
        data_en = r2.json()
        ep_en = {ep["episode_number"]: ep["name"] for ep in data_en.get("episodes", [])}
        for ep in episodes:
            if not (ep.get("name") or "").strip():
                ep["name"] = ep_en.get(
                    ep["episode_number"], f"Episode {ep['episode_number']}"
                )
    return episodes


def best_match(name_norm: str, candidates_norm: list[str]) -> tuple[int, float]:
    best_i, best_score = -1, 0.0
    for i, c in enumerate(candidates_norm):
        score = SequenceMatcher(None, name_norm, c).ratio()
        if score > best_score:
            best_i, best_score = i, score
    return best_i, best_score


def rename_episodes(
    series: str,
    season: int,
    directory: str,
    lang: str = "de",
    dry_run: bool = False,
    threshold: float = 0.6,
    assign_seq: bool = False,
) -> tuple[list[str], Optional[str]]:

    logs: list[str] = []

    if not API_KEY or API_KEY.startswith("DEIN_"):
        return logs, "Please set the TMDB API_KEY in the script."
    if not os.path.isdir(directory):
        return logs, f"Directory not found: {directory}"

    try:
        show_id = tmdb_search_show(series, lang)
    except Exception as e:
        return logs, str(e)

    try:
        season_eps = tmdb_get_season(show_id, season, lang)
    except Exception:
        return logs, f"Season {season} of series '{series}' not found"

    remaining = []
    for ep in season_eps:
        num = ep["episode_number"]
        title = ep.get("name") or f"Episode {num}"
        remaining.append(
            {
                "num": num,
                "title": title,
                "title_norm": normalize_string(title),
            }
        )

    files = [
        f
        for f in os.listdir(directory)
        if os.path.splitext(f)[1].lower() in VALID_VIDEO_EXT
    ]
    files.sort()

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
            assignments.append(
                (f, ep["num"], ep["title"], -1.0)
            )  # -1.0 signals pattern-match
        else:
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
                assignments.append(
                    (f, ep["num"], ep["title"], -2.0)
                )  # -2.0 signals fallback-pattern
            else:
                assignments.append((f, None, None, best_score))

    # Assign sequence for remaining unmatched files (existing behavior)
    if assign_seq:
        leftovers = [e for e in remaining if e["num"] not in used_nums]
        for i, (f, num, title, score) in enumerate(assignments):
            if num is None and leftovers:
                ep = leftovers.pop(0)
                assignments[i] = (
                    f,
                    ep["num"],
                    ep["title"],
                    -3.0,
                )  # -3.0 signals sequential

    renamed_count = 0
    already_correct_count = 0
    skipped_count = 0

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
        elif score == -3.0:
            match_info = "(sequential)"
        else:
            match_info = f"(match={score:.2f})"

        if os.path.abspath(src) == os.path.abspath(dst):
            logs.append(f"[  OK  ]\t'{f}' already correct")
            already_correct_count += 1
            continue
        else:
            dst = collision_safe_path(dst)
            if not dry_run:
                logs.append(f"[RENAME]\t'{f}' -> {os.path.basename(dst)}  {match_info}")
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

    if not dry_run and renamed_count > 0:
        flush_directory(directory)

    if dry_run:
        logs.append(
            f"\nSummary: {renamed_count} files would be renamed, {skipped_count} skipped"
        )
    else:
        logs.append(
            f"\nSummary: {renamed_count} files successfully renamed, {already_correct_count} already correct, {skipped_count} skipped"
        )

    return logs, None
