import os
import time
import zipfile
import io
import logging
import requests
from typing import Optional, Callable

logger = logging.getLogger(__name__)

_LOG_TAGS: dict[str, str] = {
    "UPLOAD": "[UPLOAD]\t\t",
    "PROCESS": "[PROCESS]\t\t",
    "SEPARATE": "[SEPARATE]\t\t",
    "TRANSCRIBE": "[TRANSCRIBE]\t",
    "CORRECT": "[CORRECT]\t\t",
    "DOWNLOAD": "[DOWNLOAD]\t\t",
    "SUCCESS": "[SUCCESS]\t\t",
    "SAVE": "[SAVE]\t\t\t",
    "RETRY": "[RETRY]\t\t\t",
    "WARN": "[WARN]\t\t\t",
}


def _tag_for_progress(progress: str) -> str | None:
    """Map a transcriber progress string to a log tag. Returns None to suppress."""
    lower = progress.lower()
    if "complete" in lower or "writing output" in lower:
        return None
    if "separat" in lower or "skipping vocal" in lower:
        return "SEPARATE"
    if "transcrib" in lower or "detected language" in lower:
        return "TRANSCRIBE"
    if (
        "correct" in lower
        or "genius" in lower
        or "fetching" in lower
        or "applying" in lower
    ):
        return "CORRECT"
    return "PROCESS"


def check_transcriber_health(transcriber_url: str) -> dict:
    """Check if the transcriber service is reachable and has CUDA."""
    try:
        resp = requests.get(f"{transcriber_url}/health", timeout=5)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"status": "unreachable", "error": str(e)}


def get_music_files(directory: str, valid_extensions: set) -> list[str]:
    """Return sorted list of music file paths in the directory."""
    files = []
    for f in os.listdir(directory):
        if any(f.lower().endswith(ext) for ext in valid_extensions):
            filepath = os.path.join(directory, f)
            if os.path.isfile(filepath):
                files.append(filepath)
    return sorted(files)


def get_file_lyrics_status(filepath: str) -> dict:
    """Check which lyrics files exist for an audio file."""
    stem = os.path.splitext(filepath)[0]
    return {
        "name": os.path.basename(filepath),
        "has_lrc": os.path.isfile(stem + ".lrc"),
        "has_txt": os.path.isfile(stem + ".txt"),
    }


def check_existing_lyrics(filepath: str, requested_format: str) -> Optional[str]:
    """Determine which format actually needs to be transcribed.

    Returns the effective format to request, or None if everything exists already.
    - "lrc": skip if .lrc exists
    - "txt": skip if .txt exists
    - "all": check both, return "lrc", "txt", "all", or None
    """
    stem = os.path.splitext(filepath)[0]
    has_lrc = os.path.isfile(stem + ".lrc")
    has_txt = os.path.isfile(stem + ".txt")

    if requested_format == "lrc":
        return None if has_lrc else "lrc"
    elif requested_format == "txt":
        return None if has_txt else "txt"
    elif requested_format == "all":
        need_lrc = not has_lrc
        need_txt = not has_txt
        if need_lrc and need_txt:
            return "all"
        elif need_lrc:
            return "lrc"
        elif need_txt:
            return "txt"
        else:
            return None

    return requested_format


def transcribe_file(
    filepath: str,
    transcriber_url: str,
    output_format: str = "lrc",
    no_separation: bool = False,
    whisper_model: str = "large-v3-turbo",
    language: str | None = None,
    artist: str | None = None,
    title: str | None = None,
    no_correction: bool = False,
    progress_callback: Callable[[str], None] | None = None,
) -> tuple[list[str], Optional[str]]:
    """Transcribe a single audio file via the remote transcriber API.

    1. Upload the file to POST /transcribe
    2. Poll GET /jobs/{job_id} until completed/failed
    3. Download result from GET /jobs/{job_id}/result
    4. Save .lrc/.txt alongside the original audio file
    5. DELETE /jobs/{job_id} to clean up

    Returns (logs, error).
    """
    logs: list[str] = []
    filename = os.path.basename(filepath)

    def report(msg: str, tag: str = "PROCESS"):
        formatted = _LOG_TAGS[tag] + msg
        logs.append(formatted)
        if progress_callback:
            progress_callback(formatted)

    # Step 1: Upload
    report(f"Uploading {filename}", "UPLOAD")
    try:
        with open(filepath, "rb") as f:
            form_data: dict[str, str] = {
                "format": output_format,
                "no_separation": str(no_separation).lower(),
                "whisper_model": whisper_model,
                "no_correction": str(no_correction).lower(),
            }
            if language:
                form_data["language"] = language
            if artist:
                form_data["artist"] = artist
            if title:
                form_data["title"] = title

            resp = requests.post(
                f"{transcriber_url}/transcribe",
                files={"file": (filename, f)},
                data=form_data,
                timeout=120,
            )
        resp.raise_for_status()
        job_id = resp.json()["job_id"]
    except Exception as e:
        return logs, f"Upload failed for {filename}: {e}"

    # Step 2: Poll for completion
    report(f"Processing {filename} (job {job_id})", "PROCESS")
    poll_interval = 2
    max_poll_time = 1800  # 30 minutes
    start_time = time.monotonic()
    last_progress = ""
    last_status_data: dict = {}
    consecutive_errors = 0
    max_consecutive_errors = 5
    not_found_count = 0
    max_not_found = 3

    while (time.monotonic() - start_time) < max_poll_time:
        time.sleep(poll_interval)
        try:
            status_resp = requests.get(f"{transcriber_url}/jobs/{job_id}", timeout=10)
            # Handle 404 specifically (job lost after container restart)
            if status_resp.status_code == 404:
                not_found_count += 1
                if not_found_count >= max_not_found:
                    return logs, (
                        f"Job not found on transcriber for {filename} "
                        f"(expired or service restarted)"
                    )
                report(
                    f"Job not found (retry {not_found_count}/{max_not_found})", "RETRY"
                )
                continue

            status_resp.raise_for_status()
            status_data = status_resp.json()
        except requests.exceptions.HTTPError:
            # Re-raise after status code handling above
            consecutive_errors += 1
            if consecutive_errors >= max_consecutive_errors:
                return logs, (
                    f"Transcriber unreachable for {filename} "
                    f"after {max_consecutive_errors} consecutive errors"
                )
            report(
                f"Poll error (retry {consecutive_errors}/{max_consecutive_errors})",
                "RETRY",
            )
            continue
        except Exception as e:
            consecutive_errors += 1
            if consecutive_errors >= max_consecutive_errors:
                return logs, (
                    f"Transcriber unreachable for {filename} "
                    f"after {max_consecutive_errors} consecutive errors: {e}"
                )
            report(
                f"Poll error (retry {consecutive_errors}/{max_consecutive_errors})",
                "RETRY",
            )
            continue

        # Successful poll — reset error counters
        consecutive_errors = 0
        not_found_count = 0
        last_status_data = status_data

        status = status_data["status"]
        progress = status_data.get("progress", "")

        if progress and progress != last_progress:
            tag = _tag_for_progress(progress)
            if tag is not None:
                report(f"{filename}: {progress}", tag)
            last_progress = progress

        if status == "completed":
            break
        elif status == "failed":
            error_msg = status_data.get("error", "Unknown error")
            _cleanup_job(transcriber_url, job_id)
            return logs, f"Transcription failed for {filename}: {error_msg}"
    else:
        _cleanup_job(transcriber_url, job_id)
        return logs, f"Transcription timed out for {filename}"

    # Step 3: Download results
    report(f"Downloading results for {filename}", "DOWNLOAD")
    try:
        result_resp = requests.get(
            f"{transcriber_url}/jobs/{job_id}/result", timeout=30
        )
    except Exception as e:
        _cleanup_job(transcriber_url, job_id)
        return logs, f"Download failed for {filename}: {e}"

    if result_resp.status_code == 204:
        warning = last_status_data.get("warning", "No vocals detected in audio")
        report(f"{filename}: {warning} – no lyrics file created", "SUCCESS")
        _cleanup_job(transcriber_url, job_id)
        return logs, None

    try:
        result_resp.raise_for_status()
    except Exception as e:
        _cleanup_job(transcriber_url, job_id)
        return logs, f"Download failed for {filename}: {e}"

    # Step 4: Save alongside original file
    stem = os.path.splitext(filepath)[0]
    content_type = result_resp.headers.get("content-type", "")

    valid_result_ext = {".lrc", ".txt"}

    if "application/zip" in content_type:
        z = zipfile.ZipFile(io.BytesIO(result_resp.content))
        for name in z.namelist():
            ext = os.path.splitext(name)[1].lower()
            if ext not in valid_result_ext:
                report(f"Skipping unexpected file type: {name}", "WARN")
                continue
            out_path = stem + ext
            with open(out_path, "wb") as out_f:
                out_f.write(z.read(name))
            report(f"Saved {os.path.basename(out_path)}", "SAVE")
    else:
        try:
            info_resp = requests.get(
                f"{transcriber_url}/jobs/{job_id}/result/info", timeout=10
            )
            info_resp.raise_for_status()
            files_info = info_resp.json().get("files", [])
            ext = "." + files_info[0]["format"] if files_info else f".{output_format}"
        except Exception:
            ext = f".{output_format}"

        out_path = stem + ext
        with open(out_path, "wb") as out_f:
            out_f.write(result_resp.content)
        report(f"Saved {os.path.basename(out_path)}", "SAVE")

    # Step 5: Cleanup remote job
    _cleanup_job(transcriber_url, job_id)
    report(f"{filename} transcribed successfully", "SUCCESS")

    return logs, None


def _cleanup_job(transcriber_url: str, job_id: str):
    """Best-effort cleanup of a remote job."""
    try:
        requests.delete(f"{transcriber_url}/jobs/{job_id}", timeout=5)
    except Exception:
        pass
