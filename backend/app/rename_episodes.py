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

    assignments = []
    unused = remaining[:]

    for f in files:
        n = normalize_string(f)
        best_idx, best_score = best_match(n, [e["title_norm"] for e in unused])
        if best_idx >= 0 and best_score >= threshold:
            ep = unused.pop(best_idx)
            assignments.append((f, ep["num"], ep["title"], best_score))
        else:
            assignments.append((f, None, None, best_score))

    if assign_seq:
        leftovers = [e for e in unused]
        for i, (f, num, title, score) in enumerate(assignments):
            if num is None and leftovers:
                ep = leftovers.pop(0)
                assignments[i] = (f, ep["num"], ep["title"], score)

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

        if os.path.abspath(src) == os.path.abspath(dst):
            logs.append(f"[  OK  ]\t'{f}' already correct")
            already_correct_count += 1
            continue
        else:
            dst = collision_safe_path(dst)
            if not dry_run:
                logs.append(
                    f"[RENAME]\t'{f}' -> {os.path.basename(dst)}  (match={score:.2f})"
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
                    f"[DRYRUN]\tWould rename '{f}' -> {os.path.basename(dst)}  (match={score:.2f})"
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
