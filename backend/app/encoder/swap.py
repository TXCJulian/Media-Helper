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
from dataclasses import dataclass

logger = logging.getLogger(__name__)

OUTPUT_PREFIX = ".hbenc-"
"""Fixed convention shared with the encoder service.

Matches both shapes it produces: ``.hbenc-<job_id>.<ext>``, the published
name, and ``.hbenc-<job_id>-<token>.<ext>``, the staging name it writes during
the encode. A crash mid-encode leaves the latter, so a sweep that only knew
the former would leave partials behind forever.
"""

_ORPHAN_RE = re.compile(
    r"^\.hbenc-(?P<job_id>[A-Za-z0-9]+)(?:-[A-Za-z0-9]+)?\.[A-Za-z0-9]+$"
)


class SwapError(RuntimeError):
    """Raised when an encode cannot be published. The source is untouched."""


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

    _copy_ownership(source_stat, encoded)

    # The container can differ from the source's: a preset may transcode mkv
    # to mp4. Publishing under the old extension would leave a file whose name
    # lies about its contents.
    final_path = os.path.splitext(source)[0] + os.path.splitext(encoded)[1]

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

    base = os.path.basename(source)
    target = os.path.join(holding_dir, f"{int(time.time())}-{base}")
    try:
        shutil.move(source, target)
    except OSError as exc:
        # Retention was asked for and could not be honoured. Abort with the
        # library untouched rather than proceed and destroy the original
        # anyway -- silently downgrading to "delete" is the one behaviour a
        # user who set a TTL would never want.
        raise SwapError(f"Could not preserve the original {source}: {exc}") from exc
    return target


def sweep_orphans(directory: str, active_job_ids: set[str]) -> list[str]:
    """Delete `.hbenc-` partials in *directory* not belonging to a live job.

    Run at startup: a crash mid-encode leaves a partial no one will collect,
    and it is invisible to the user because the name is dot-prefixed.
    """
    removed: list[str] = []
    try:
        entries = os.listdir(directory)
    except OSError:
        return removed

    for name in entries:
        match = _ORPHAN_RE.match(name)
        if not match or match.group("job_id") in active_job_ids:
            continue
        path = os.path.join(directory, name)
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
