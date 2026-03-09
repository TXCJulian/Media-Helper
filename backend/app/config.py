import logging
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "dependencies", ".env"))

logger = logging.getLogger(__name__)

BASE_PATH = os.getenv("BASE_PATH") or "/media"
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
        ".mp4,.mkv,.mov,.avi,.mp3,.flac,.m4a,.wav,.aac,.ac3,.dts,.opus,.ogg,.aiff",
    ).split(",")
)
CUTTER_UPLOAD_DIR = os.getenv("CUTTER_UPLOAD_DIR", "/tmp/cutter-uploads")

_VALID_FEATURES = {"episodes", "music", "lyrics", "cutter"}
_features_raw = os.getenv("ENABLED_FEATURES", "episodes,music")
_parsed_features: set[str] = {
    f.strip().lower()
    for f in _features_raw.split(",")
    if f.strip().lower() in _VALID_FEATURES
}
if not _parsed_features:
    logger.warning(
        "No valid ENABLED_FEATURES found in '%s', falling back to all features: %s",
        _features_raw,
        _VALID_FEATURES,
    )
ENABLED_FEATURES: set[str] = _parsed_features or _VALID_FEATURES
