import os
import re
import logging
import unicodedata
from mutagen.flac import FLAC
from mutagen.wave import WAVE
from mutagen.mp3 import MP3
from mutagen.oggvorbis import OggVorbis
from mutagen.oggopus import OggOpus
from mutagen.aiff import AIFF
from mutagen.asf import ASF
from mutagen.musepack import Musepack
from mutagen._util import MutagenError
from typing import Optional, Any
from app.config import VALID_MUSIC_EXT
from app.fs_utils import flush_directory, collision_safe_path
from app.get_dirs import has_valid_files

logger = logging.getLogger(__name__)

DISALLOWED_RE = re.compile(r'[\x00-\x1F<>:"/\\|?*]')

# Lyrics files the transcriber writes next to an audio file.
LYRIC_EXTENSIONS = (".txt", ".lrc")
# What to do with them when their audio file is renamed.
LYRICS_ACTIONS = frozenset({"rename", "delete"})


def try_decode_bytes(b: bytes) -> str:
    """Try multiple decodings in order, return str."""
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return b.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return b.decode("utf-8", errors="replace")


def fix_mojibake_if_needed(s: str) -> str:
    suspicious = any(x in s for x in ("�", "Ã", "Â"))
    if not suspicious:
        return s

    best = s
    best_repl = s.count("�")

    candidates = [
        ("cp1252", "utf-8"),
        ("latin-1", "utf-8"),
        ("utf-8", "cp1252"),
    ]

    for enc_from, enc_to in candidates:
        try:
            cand = s.encode(enc_from, errors="replace").decode(enc_to, errors="replace")
            cand_repl = cand.count("�")
            if cand_repl < best_repl:
                best = cand
                best_repl = cand_repl
        except (UnicodeDecodeError, UnicodeEncodeError):
            continue

    return best


def sanitize_tag_value(value) -> str:
    if value is None:
        return ""

    if isinstance(value, bytes):
        s = try_decode_bytes(value)
    else:
        s = str(value)

    s = fix_mojibake_if_needed(s)
    s = unicodedata.normalize("NFC", s)
    s = DISALLOWED_RE.sub("", s)
    s = s.strip()

    return s


def leading_int(raw: Any) -> int:
    """Leading integer of a disc/track tag, or 0 if there is none.

    Must run on the *raw* tag value: these tags commonly use the "7/12"
    (number-of-total) form, and sanitize_tag_value() strips the '/' as a path
    separator, which would silently turn 7/12 into 712.
    """
    m = re.match(r"\s*(\d+)", str(raw if raw is not None else ""))
    return int(m.group(1)) if m else 0


def get_first_tag_value(audio: Any, tag_name: str) -> Optional[str]:
    try:
        vals: Any = audio.get(tag_name)
    except (KeyError, MutagenError):
        return None

    if vals is None:
        return None

    if isinstance(vals, (list, tuple)):
        if not vals:
            return None
        vals = vals[0]

    if isinstance(vals, (bytes, bytearray)):
        try:
            return try_decode_bytes(bytes(vals))
        except (UnicodeDecodeError, ValueError):
            return None

    try:
        return str(vals)
    except (ValueError, TypeError):
        return None


def load_audio_file(filepath: str) -> Optional[Any]:
    _, ext = os.path.splitext(filepath)
    ext_lower = ext.lower()

    try:
        if ext_lower == ".flac":
            return FLAC(filepath)
        elif ext_lower == ".wav":
            return WAVE(filepath)
        elif ext_lower == ".mp3":
            return MP3(filepath)
        elif ext_lower == ".ogg":
            return OggVorbis(filepath)
        elif ext_lower == ".opus":
            return OggOpus(filepath)
        elif ext_lower in (".aiff", ".aif"):
            return AIFF(filepath)
        elif ext_lower in (".wma", ".asf"):
            return ASF(filepath)
        elif ext_lower in (".mpc", ".mp+", ".mpp"):
            return Musepack(filepath)
        else:
            return None
    except MutagenError as e:
        logger.warning("Failed to load audio file %s: %s", filepath, e)
        return None


def handle_sidecar_lyrics(
    old_audio_path: str,
    new_audio_path: str,
    lyrics_action: str,
    dry_run: bool,
    logs: list[str],
) -> None:
    """Rename or delete the .lrc/.txt files sitting next to a renamed audio file.

    `new_audio_path` is the *final* path the audio landed on, so the lyrics keep
    matching it even when collision_safe_path() appended a suffix.
    """
    old_stem = os.path.splitext(old_audio_path)[0]
    new_stem = os.path.splitext(new_audio_path)[0]

    for lyric_ext in LYRIC_EXTENSIONS:
        old_lyric = old_stem + lyric_ext
        if not os.path.exists(old_lyric):
            continue

        if lyrics_action == "delete":
            if dry_run:
                logs.append(
                    f"[DELETE]\tWould remove lyric file: {os.path.basename(old_lyric)}"
                )
            else:
                try:
                    os.remove(old_lyric)
                    logs.append(
                        f"[DELETE]\tLyric file removed: {os.path.basename(old_lyric)}"
                    )
                except OSError as e:
                    logs.append(f"[!]\t{lyric_ext} deletion failed: {e}")
            continue

        # "rename" — keep the lyrics, following the audio file's new name.
        new_lyric = new_stem + lyric_ext
        if dry_run:
            logs.append(
                f"[LYRICS]\tWould rename '{os.path.basename(old_lyric)}' -> "
                f"{os.path.basename(new_lyric)}"
            )
            continue

        # An unrelated file may already occupy the destination; never clobber it.
        safe_lyric = collision_safe_path(new_lyric)
        try:
            os.rename(old_lyric, safe_lyric)
            logs.append(
                f"[LYRICS]\t'{os.path.basename(old_lyric)}' -> "
                f"{os.path.basename(safe_lyric)}"
            )
        except OSError as e:
            logs.append(f"[!]\t{lyric_ext} rename failed: {e}")


def rename_music(
    directory: str, dry_run: bool = False, lyrics_action: str = "rename"
) -> tuple[list[str], Optional[str]]:

    if lyrics_action not in LYRICS_ACTIONS:
        raise ValueError(
            f"Invalid lyrics_action '{lyrics_action}'. "
            f"Must be one of: {', '.join(sorted(LYRICS_ACTIONS))}"
        )

    logs: list[str] = []
    error: Optional[str] = None

    if not os.path.isdir(directory):
        error = f"Directory not found: {directory}"
        return logs, error

    if not has_valid_files(directory, VALID_MUSIC_EXT):
        error = f"No valid music files found (Extensions: {VALID_MUSIC_EXT})"
        return logs, error

    renamed_count = 0
    already_correct_count = 0
    skipped_files = []
    skipped_count = 0

    for filename in os.listdir(directory):
        if not any(filename.lower().endswith(ext.lower()) for ext in VALID_MUSIC_EXT):
            continue

        filepath = os.path.join(directory, filename)
        if os.path.isdir(filepath):
            continue

        audio = load_audio_file(filepath)
        if audio is None:
            skipped_files.append((filename, "File could not be loaded"))
            continue

        raw_title = get_first_tag_value(audio, "title")
        raw_track = get_first_tag_value(audio, "tracknumber") or get_first_tag_value(
            audio, "track"
        )
        raw_disk = get_first_tag_value(audio, "discnumber") or get_first_tag_value(
            audio, "disc"
        )

        if not raw_title or not raw_track or not raw_disk:
            missing = []
            if not raw_title:
                missing.append("title")
            if not raw_track:
                missing.append("track")
            if not raw_disk:
                missing.append("disc")
            skipped_files.append((filename, f"Missing tags: {', '.join(missing)}"))
            continue

        title = sanitize_tag_value(raw_title)
        disk_num = leading_int(raw_disk)
        track_num = leading_int(raw_track)

        if not title:
            skipped_files.append((filename, "Title tag is empty"))
            continue

        _, ext = os.path.splitext(filename)
        new_name_base = f"D{disk_num:02d}T{track_num:02d} {title}{ext}"
        new_name_base = new_name_base.strip()
        new_path = os.path.join(directory, new_name_base)

        if os.path.abspath(filepath) == os.path.abspath(new_path):
            logs.append(f"[  OK  ]\t'{filename}' already correct")
            already_correct_count += 1
            continue

        new_path = collision_safe_path(new_path)

        if not dry_run:
            try:
                os.rename(filepath, new_path)
            except OSError as e:
                skipped_files.append((filename, f"Error renaming: {str(e)}"))
                continue
            logs.append(f"[RENAME]\t'{filename}' -> {os.path.basename(new_path)}")
            # Only after the audio moved, so the lyrics follow its final name.
            handle_sidecar_lyrics(filepath, new_path, lyrics_action, False, logs)
        else:
            logs.append(
                f"[DRYRUN]\tWould rename '{filename}' -> {os.path.basename(new_path)}"
            )
            handle_sidecar_lyrics(filepath, new_path, lyrics_action, True, logs)

        # Counted for both modes, so a dry run previews the real run's summary.
        renamed_count += 1

    if not dry_run and renamed_count > 0:
        flush_directory(directory)

    for fname, reason in skipped_files:
        logs.append(f"[ SKIP ]\t'{fname}' - {reason}")
    skipped_count = len(skipped_files)

    if dry_run:
        logs.append(
            f"\nSummary: {renamed_count} files would be renamed, "
            f"{already_correct_count} already correct, {skipped_count} skipped"
        )
    else:
        logs.append(
            f"\nSummary: {renamed_count} files successfully renamed, {already_correct_count} already correct, {skipped_count} skipped"
        )

    return logs, None
