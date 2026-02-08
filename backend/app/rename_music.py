import os
import re
import ast
import unicodedata
from mutagen.flac import FLAC
from mutagen.wave import WAVE
from mutagen.mp3 import MP3
from mutagen.oggvorbis import OggVorbis
from mutagen.oggopus import OggOpus
from mutagen.aiff import AIFF
from mutagen.asf import ASF
from mutagen.musepack import Musepack
from dotenv import load_dotenv
from typing import Optional, Any, Tuple

load_dotenv("dependencies/.env")

VALID_MUSIC_EXT = set(
    ast.literal_eval(os.getenv("VALID_MUSIC_EXT", "{'.mp3', '.flac', '.m4a', '.wav'}"))
)

DISALLOWED_RE = re.compile(r'[\x00-\x1F<>:"/\\|?*]')


def try_decode_bytes(b: bytes) -> str:
    """Try multiple decodings in order, return str."""
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return b.decode(enc)
        except Exception:
            pass
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
        except Exception:
            pass

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


def get_first_tag_value(audio: FLAC, tag_name: str) -> Optional[str]:
    try:
        vals: Any = audio.get(tag_name)
    except Exception:
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
        except Exception:
            return None

    try:
        return str(vals)
    except Exception:
        return None


def has_valid_music_files(folder: str) -> bool:
    for _, _, files in os.walk(folder):
        for f in files:
            if any(f.lower().endswith(ext.lower()) for ext in VALID_MUSIC_EXT):
                return True
    return False


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
    except Exception:
        return None


def rename_music(
    directory: str, dry_run: bool = False
) -> Tuple[list[str], Optional[str]]:

    logs: list[str] = []
    error: Optional[str] = None

    if not os.path.isdir(directory):
        error = f"Directory not found: {directory}"
        return logs, error

    if not has_valid_music_files(directory):
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
        track_s = sanitize_tag_value(raw_track)

        disk_num = 0
        try:
            if raw_disk:
                m = re.search(r"\d", str(raw_disk))
                disk_num = int(m.group(0)) if m else 0
        except Exception:
            disk_num = 0

        m2 = re.match(r"\s*(\d+)", track_s)
        try:
            track_num = int(m2.group(1)) if m2 else 0
        except Exception:
            track_num = 0

        if not title:
            skipped_files.append((filename, "Title tag is empty"))
            continue

        _, ext = os.path.splitext(filename)
        new_name_base = f"{disk_num:02d}-{track_num:02d} {title}{ext}"
        new_name_base = new_name_base.strip()
        new_path = os.path.join(directory, new_name_base)

        if os.path.abspath(filepath) == os.path.abspath(new_path):
            logs.append(f"[  OK  ]\t'{filename}' already correct")
            already_correct_count += 1
            continue

        if os.path.exists(new_path):
            base, file_ext = os.path.splitext(new_name_base)
            i = 1
            while True:
                candidate = f"{base} ({i}){file_ext}"
                candidate_path = os.path.join(directory, candidate)
                if not os.path.exists(candidate_path):
                    new_path = candidate_path
                    break
                i += 1

        if not dry_run:
            try:
                os.rename(filepath, new_path)
                base_name = os.path.splitext(filepath)[0]
                for lyric_ext in [".txt", ".lrc"]:
                    old_lyric = base_name + lyric_ext
                    if os.path.exists(old_lyric):
                        try:
                            os.remove(old_lyric)
                            logs.append(
                                f"\t[DELETE] Lyric file removed: {os.path.basename(old_lyric)}"
                            )
                        except Exception as e:
                            logs.append(f"\t[!] {lyric_ext} deletion failed: {e}")
                logs.append(f"[RENAME]\t'{filename}' -> {os.path.basename(new_path)}")
                renamed_count += 1
            except Exception as e:
                skipped_files.append((filename, f"Error renaming: {str(e)}"))
                continue
            # try to flush directory metadata so mount clients (SMB/CIFS) notice the change
            try:
                if hasattr(os, "O_DIRECTORY"):
                    dir_flag = getattr(os, "O_DIRECTORY", 0)
                    dir_fd = os.open(directory, dir_flag | os.O_RDONLY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                else:
                    sync_fn = getattr(os, "sync", None)
                    if sync_fn:
                        sync_fn()
            except Exception:
                try:
                    sync_fn = getattr(os, "sync", None)
                    if sync_fn:
                        sync_fn()
                except Exception:
                    pass
        else:
            base_name = os.path.splitext(filepath)[0]
            for lyric_ext in [".txt", ".lrc"]:
                old_lyric = base_name + lyric_ext
                if os.path.exists(old_lyric):
                    logs.append(
                        f"\t[DELETE] Would remove lyric file: {os.path.basename(old_lyric)}"
                    )
            logs.append(
                f"[DRYRUN]\tWould rename '{filename}' -> {os.path.basename(new_path)}"
            )

    for fname, reason in skipped_files:
        logs.append(f"[ SKIP ]\t'{fname}' - {reason}")
    skipped_count = len(skipped_files)

    if dry_run:
        logs.append(
            f"\nSummary: {renamed_count} files would be renamed, {skipped_count} skipped"
        )
    else:
        logs.append(
            f"\nSummary: {renamed_count} files successfully renamed, {already_correct_count} already correct, {skipped_count} skipped"
        )

    return logs, None
