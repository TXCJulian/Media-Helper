import logging
import os
import secrets
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "dependencies", ".env"))

logger = logging.getLogger(__name__)

# --- Base paths ---
_base_paths_raw = os.getenv("BASE_PATHS") or os.getenv("BASE_PATH") or "/media"
if not os.getenv("BASE_PATHS") and os.getenv("BASE_PATH"):
    logger.warning("BASE_PATH is deprecated, use BASE_PATHS (comma-separated) instead")
BASE_PATHS: list[str] = [p.strip() for p in _base_paths_raw.split(",") if p.strip()]
if not BASE_PATHS:
    BASE_PATHS = ["/media"]


def _build_labels(paths: list[str]) -> dict[str, str]:
    """Map each base path to a unique label derived from its last path segment."""
    labels: dict[str, str] = {}
    for path in paths:
        base_label = os.path.basename(path.rstrip("/\\")) or path
        if base_label not in labels:
            labels[base_label] = path
        else:
            suffix = 2
            while f"{base_label}-{suffix}" in labels:
                suffix += 1
            labels[f"{base_label}-{suffix}"] = path
    return labels


BASE_PATH_LABELS: dict[str, str] = _build_labels(BASE_PATHS)


def resolve_base(base_label: str) -> str:
    """Resolve a base label to its full path. Raises ValueError if unknown."""
    path = BASE_PATH_LABELS.get(base_label)
    if path is None:
        raise ValueError(f"Unknown base: '{base_label}'")
    return path


TVSHOW_FOLDER_NAME = os.getenv("TVSHOW_FOLDER_NAME") or "TV Shows"
MUSIC_FOLDER_NAME = os.getenv("MUSIC_FOLDER_NAME") or "Music"
TMDB_API_KEY = os.getenv("TMDB_API_KEY") or "YOUR_TMDB_API_KEY"
VALID_VIDEO_EXT = set(os.getenv("VALID_VIDEO_EXT", ".mp4,.mkv,.mov,.avi").split(","))
VALID_MUSIC_EXT = set(os.getenv("VALID_MUSIC_EXT", ".mp3,.flac,.m4a,.wav").split(","))
TRANSCRIBER_URL = os.getenv("TRANSCRIBER_URL", "")
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3333").split(",")

VALID_CUTTER_EXT = set(
    os.getenv(
        "VALID_CUTTER_EXT",
        ".mp4,.mkv,.mov,.avi,.webm,.mp3,.flac,.m4a,.wav,.aac,.ac3,.dts,.thd,.opus,.ogg,.aiff",
    ).split(",")
)
CUTTER_JOBS_DIR = os.getenv("CUTTER_JOBS_DIR", "/tmp/cutter-jobs")
CUTTER_JOB_TTL = int(os.getenv("CUTTER_JOB_TTL", "86400"))
CUTTER_MAX_DIRECT_REMUX_BYTES = int(
    os.getenv("CUTTER_MAX_DIRECT_REMUX_BYTES", str(1024 * 1024 * 1024))
)
HWACCEL = os.getenv("HWACCEL", "").lower().strip()
VAAPI_DEVICE = os.getenv("VAAPI_DEVICE", "/dev/dri/renderD128")

_VALID_FEATURES = {"episodes", "music", "lyrics", "cutter"}
_features_raw = os.getenv("ENABLED_FEATURES", "episodes,music,cutter")
_parsed_features: list[str] = list(
    dict.fromkeys(
        f.strip().lower()
        for f in _features_raw.split(",")
        if f.strip().lower() in _VALID_FEATURES
    )
)
if not _parsed_features:
    logger.warning(
        "No valid ENABLED_FEATURES found in '%s', falling back to all features: %s",
        _features_raw,
        _VALID_FEATURES,
    )
ENABLED_FEATURES: list[str] = _parsed_features or sorted(_VALID_FEATURES)
ENABLED_FEATURES_SET: set[str] = set(ENABLED_FEATURES)

# --- Authentication (optional) ---
AUTH_USERNAME = os.getenv("AUTH_USERNAME", "").strip()
AUTH_PASSWORD = os.getenv("AUTH_PASSWORD", "").strip()
AUTH_ENABLED = bool(AUTH_USERNAME and AUTH_PASSWORD)

# --- Secret key (for session cookies and file ID signing) ---
_SECRET_KEY_PATH = "/var/lib/media-renamer/.secret_key"

def _load_or_generate_secret_key() -> str:
    env_key = os.getenv("SECRET_KEY", "").strip()
    if env_key:
        return env_key
    # Try loading from persistent storage
    try:
        with open(_SECRET_KEY_PATH) as f:
            stored = f.read().strip()
            if stored:
                return stored
    except FileNotFoundError:
        pass
    # Generate and persist
    key = secrets.token_hex(32)
    try:
        os.makedirs(os.path.dirname(_SECRET_KEY_PATH), exist_ok=True)
        with open(_SECRET_KEY_PATH, "w") as f:
            f.write(key)
        logger.warning(
            "Generated SECRET_KEY and saved to %s. Set SECRET_KEY env var for explicit control.",
            _SECRET_KEY_PATH,
        )
    except OSError as e:
        logger.warning("Could not persist SECRET_KEY to %s: %s", _SECRET_KEY_PATH, e)
    return key

SECRET_KEY = _load_or_generate_secret_key()

# Hash password at startup if auth is enabled
_PASSWORD_HASH: bytes | None = None
if AUTH_ENABLED:
    import bcrypt as _bcrypt
    _PASSWORD_HASH = _bcrypt.hashpw(AUTH_PASSWORD.encode("utf-8"), _bcrypt.gensalt())
    logger.info("Authentication enabled for user '%s'", AUTH_USERNAME)
else:
    logger.info("Authentication disabled (AUTH_USERNAME/AUTH_PASSWORD not set)")
