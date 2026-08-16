"""Publishing an encode into the library, and cleaning up after failures.

This is the module that can destroy a movie, so the ordering below is
load-bearing rather than stylistic:

1. Verify the encoded file exists and is non-empty.
2. Copy the source's ownership and mode onto it.
3. Preserve the original (move to holding) if retention was asked for.
4. Publish the encoded file at the final path -- this is the one step that
   is allowed to touch the source's name, because it is the step that
   proves the encode landed.
5. Only once that publish has succeeded do we clean up anything that is
   left of the original.

The governing rule of the whole feature: **the original is only ever destroyed
after a verified-successful encode.** Every failure path here leaves the source
where it was.
"""

import logging
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Holding directories already warned about being on a different filesystem
# than a source they preserved from. Keyed so the warning fires once per
# holding directory rather than on every swap -- the condition is a
# deployment fact, not a per-file surprise, so repeating it would just be
# log noise.
_cross_device_warned: set[str] = set()

# A separate alias for `_warn_if_cross_device`'s stat calls, rather than
# calling `os.stat` directly. `shutil.move` (used by `_preserve` right after)
# relies on the real `os.stat` internally to decide rename-vs-copy and to
# preserve metadata, so a test that needs to fake device ids for the warning
# check must be able to do so without also breaking that call -- patching
# this alias instead of `os.stat` itself keeps the two independent.
_stat = os.stat

OUTPUT_PREFIX = ".hbenc-"
"""Fixed convention shared with the encoder service.

Matches both shapes it produces: ``.hbenc-<job_id>.<ext>``, the published
name, and ``.hbenc-<job_id>-<token>.<ext>``, the staging name it writes during
the encode. A crash mid-encode leaves the latter, so a sweep that only knew
the former would leave partials behind forever.
"""

_ORPHAN_RE = re.compile(r"^\.hbenc-(?P<id_portion>.+)\.[A-Za-z0-9]+$")
"""Matches the `.hbenc-` shape without trying to split the job id from an
optional `-<token>` suffix -- job ids come verbatim from the encoder service
(client.py) and are not guaranteed to be hyphen-free, so a regex that treated
the first hyphen as the id/token boundary could slice a hyphenated id in half
and mistake a live job's partial for an orphan's. See ``_is_protected``.
"""


class SwapError(RuntimeError):
    """Raised when an encode cannot be published. The source is untouched.

    ``kept_path`` is set when the original had already been moved into the
    holding area and could not be moved back after a later failure -- the
    movie survives, but not at ``source``, so callers need the pointer to
    tell the user where it went.
    """

    def __init__(self, message: str, kept_path: str | None = None) -> None:
        super().__init__(message)
        self.kept_path = kept_path


@dataclass(frozen=True)
class SwapResult:
    final_path: str
    kept_path: str | None
    original_size: int
    encoded_size: int


def swap_in(
    source: str, encoded: str, original_ttl: int, holding_dir: str
) -> SwapResult:
    """Publish *encoded* at *source*'s place, preserving the original per TTL.

    ``original_ttl`` of 0 deletes the original once the encode is in place;
    anything higher moves it to *holding_dir* for later purging. One value
    covers both policies, so there is no separate mode flag to disagree with.
    """
    if not os.path.isfile(encoded):
        raise SwapError(f"Encoded file not found: {encoded}")
    encoded_size = os.path.getsize(encoded)
    if encoded_size == 0:
        # A zero-byte output means the encode produced nothing. Publishing it
        # would destroy the movie and report success.
        raise SwapError(f"Encoded file is empty, refusing to publish: {encoded}")

    try:
        source_stat = os.stat(source)
    except OSError as exc:
        raise SwapError(f"Source vanished before the swap: {source}") from exc
    original_size = source_stat.st_size

    # The container can differ from the source's: a preset may transcode mkv
    # to mp4. Publishing under the old extension would leave a file whose name
    # lies about its contents.
    final_path = os.path.splitext(source)[0] + os.path.splitext(encoded)[1]

    if final_path != source and os.path.exists(final_path):
        # final_path is derived purely by extension substitution, so an
        # mkv->mp4 preset could land on a *different* movie that happens to
        # share the title -- e.g. "Film (2019).mkv" and a pre-existing,
        # unrelated "Film (2019).mp4". os.replace() below would silently
        # unlink that file with no retention and a success return, which is
        # exactly the harm this module exists to prevent. Nothing has been
        # touched yet, so aborting here is free. This is a name collision a
        # human should see, not something to route through _preserve and
        # silently retitle -- that file was never part of this job.
        #
        # Checked *before* _copy_ownership below: an aborting job must not
        # chmod/chown the encoded file it is refusing to publish.
        raise SwapError(
            f"Refusing to publish {encoded} over {final_path}: a file "
            f"already exists there and was never part of this job."
        )

    _copy_ownership(source_stat, encoded)

    # Preserve the original *before* publishing, so that if retention was
    # requested we never reach a state where the encode is live but the
    # original was neither kept nor recoverable. Deletion, by contrast, is
    # deferred until after the publish below succeeds: nothing about
    # retention=0 requires destroying the source before we know the encode
    # actually landed, and doing so would leave no way back if the publish
    # step then failed.
    kept_path: str | None = None
    if original_ttl > 0:
        kept_path = _preserve(source, holding_dir)

    try:
        os.replace(encoded, final_path)
    except OSError as exc:
        # The original may already be moved into holding at this point, so
        # put it back rather than leaving the library short of a file. If it
        # was never touched (the ttl<=0 branch above), there is nothing to
        # restore -- it is still sitting exactly where it was.
        if kept_path and os.path.exists(kept_path):
            try:
                shutil.move(kept_path, source)
                kept_path = None
            except OSError:
                logger.exception("Could not restore the original from %s", kept_path)
        if kept_path:
            # The restore above failed (or was never attempted because the
            # move already happened and vanished some other way). The movie
            # is not lost -- it survives at kept_path -- but a log line is
            # not a recovery path: the job record, the UI, and Task 8's
            # error handling all need this pointer to tell the user where
            # their original went.
            raise SwapError(
                f"Could not publish {encoded} to {final_path}: {exc}. The "
                f"original could not be restored from holding; it survives "
                f"at {kept_path}.",
                kept_path=kept_path,
            ) from exc
        raise SwapError(f"Could not publish {encoded} to {final_path}: {exc}") from exc

    if original_ttl <= 0 and final_path != source:
        # The extension changed, so the encoded file was published under a
        # new name and the original -- untouched until now -- is left beside
        # it. The publish already succeeded, so removing this leftover is
        # cleanup, not part of the atomic swap; a failure here does not put
        # the movie at risk and is only logged. When the extension is
        # unchanged, os.replace() above already overwrote the original in a
        # single atomic step and there is nothing left to remove.
        try:
            os.remove(source)
        except OSError:
            logger.warning(
                "Could not remove the superseded original %s", source, exc_info=True
            )

    return SwapResult(
        final_path=final_path,
        kept_path=kept_path,
        original_size=original_size,
        encoded_size=encoded_size,
    )


def _copy_ownership(source_stat: os.stat_result, target: str) -> None:
    """Give *target* the source's uid/gid and mode.

    Without this, a rip owned by whatever the ripping process runs as silently
    becomes appuser-owned, which can break other tooling even while Jellyfin
    still reads it. chown needs privileges the container may not have, so it is
    best-effort and logged; the mode is applied regardless.
    """
    try:
        os.chmod(target, source_stat.st_mode & 0o7777)
    except OSError:
        logger.warning("Could not copy mode onto %s", target, exc_info=True)
    if hasattr(os, "chown"):
        try:
            os.chown(target, source_stat.st_uid, source_stat.st_gid)
        except (OSError, AttributeError):
            logger.warning(
                "Could not copy ownership onto %s (needs CAP_CHOWN); the file "
                "will belong to the service user",
                target,
                exc_info=True,
            )


def _preserve(source: str, holding_dir: str) -> str:
    """Move the original into the holding area, returning its new path."""
    try:
        os.makedirs(holding_dir, exist_ok=True)
    except OSError as exc:
        raise SwapError(f"Could not create the holding area {holding_dir}: {exc}") from exc

    _warn_if_cross_device(source, holding_dir)

    base = os.path.basename(source)
    # A bare `<timestamp>-<base>` collides at one-second granularity: two
    # sources sharing a basename (`Movies/A/movie.mkv`, `Movies/B/movie.mkv`
    # -- common with disc-rip naming) preserved in the same second would have
    # the second shutil.move() silently overwrite the first's *retained
    # original*, destroying it rather than merely losing GPU time. The uuid4
    # suffix makes every preserve's target name unique regardless of timing.
    target = os.path.join(holding_dir, f"{int(time.time())}-{uuid.uuid4().hex[:8]}-{base}")
    try:
        shutil.move(source, target)
    except OSError as exc:
        # Retention was asked for and could not be honoured. Abort with the
        # library untouched rather than proceed and destroy the original
        # anyway -- silently downgrading to "delete" is the one behaviour a
        # user who set a TTL would never want.
        raise SwapError(f"Could not preserve the original {source}: {exc}") from exc
    return target


def _warn_if_cross_device(source: str, holding_dir: str) -> None:
    """Log once if *holding_dir* is on a different filesystem than *source*.

    Cross-device degrades ``shutil.move`` to copy-then-unlink: minutes of I/O
    inside the swap window plus a transient 2x space requirement on
    (typically) the smaller holding volume. Safety is unaffected -- the copy
    completes before the unlink either way -- so this exists purely to make
    the cost visible to whoever configured ``ENCODER_DATA_DIR``, not to
    change behaviour or pick a different default.
    """
    if holding_dir in _cross_device_warned:
        return
    _cross_device_warned.add(holding_dir)
    try:
        source_dev = _stat(os.path.dirname(source) or ".").st_dev
        holding_dev = _stat(holding_dir).st_dev
    except OSError:
        return
    if source_dev != holding_dev:
        logger.warning(
            "Holding directory %s is on a different filesystem than the "
            "media library. Preserving an original there will copy rather "
            "than rename it, costing extra I/O time and requiring roughly "
            "double the disk space during the swap.",
            holding_dir,
        )


def _is_protected(id_portion: str, active_job_ids: set[str]) -> bool:
    """Is *id_portion* -- the text between ``.hbenc-`` and the extension --
    the published or staging name of a job in *active_job_ids*?

    ``id_portion`` is either a bare job id (``.hbenc-<job_id>.<ext>``) or a
    job id followed by ``-<token>`` (the staging shape written mid-encode).
    Job ids are opaque strings from the encoder service and may themselves
    contain hyphens, so this checks candidacy directly against the full
    active ids rather than splitting on the first hyphen.
    """
    if id_portion in active_job_ids:
        return True
    return any(id_portion.startswith(f"{job_id}-") for job_id in active_job_ids)


def sweep_orphans(directory: str, active_job_ids: set[str]) -> list[str]:
    """Delete `.hbenc-` partials under *directory* not belonging to a live job.

    Run at startup: a crash mid-encode leaves a partial no one will collect,
    and it is invisible to the user because the name is dot-prefixed.

    Walks recursively, matching ``EncoderWatcher``'s ``recursive=True`` and
    ``scan_existing()``'s ``os.walk``. A flat ``os.listdir`` here would find
    nothing at all in a normal library layout, where movies live in
    per-title subfolders -- ``os.walk`` on a directory that does not exist
    simply yields nothing, so a missing root still degrades to an empty
    result rather than raising.
    """
    removed: list[str] = []
    for dirpath, _dirs, files in os.walk(directory):
        for name in files:
            match = _ORPHAN_RE.match(name)
            if not match or _is_protected(match.group("id_portion"), active_job_ids):
                continue
            path = os.path.join(dirpath, name)
            try:
                if os.path.isfile(path):
                    os.remove(path)
                    removed.append(path)
            except OSError:
                logger.warning("Could not remove orphaned partial %s", path, exc_info=True)
    return removed


def purge_original(kept_path: str) -> bool:
    """Delete a retained original whose TTL has elapsed."""
    try:
        os.remove(kept_path)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        logger.warning("Could not purge retained original %s", kept_path, exc_info=True)
        return False
