"""HTTP client for the HandBrake_Video-Encoder service.

The service is remote, optional, and may be restarting. This module's job is to
turn its responses into two clearly separated outcomes: *retry later* and *stop
and tell a human*. Getting that split wrong is costly in both directions --
retrying a bad preset forever hides a configuration fault, while failing a job
because the encoder was mid-restart throws away work for no reason.
"""

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

# The only codes worth retrying. Both mean "healthy service, come back soon":
# queue_full is saturation, service_unavailable is startup or shutdown.
RETRYABLE_CODES = frozenset({"queue_full", "service_unavailable"})

_DEFAULT_RETRY_AFTER = 60

# HandBrake's preset-document format version. Any document it parses must carry
# these; see the note in submit(). The values track the format, not the
# application, and HandBrake upgrades older documents on import -- so a
# slightly stale value here is safe, while omitting them is not.
_PRESET_DOC_VERSION = {"VersionMajor": 72, "VersionMinor": 0, "VersionMicro": 0}


class EncoderUnreachable(RuntimeError):
    """The service could not be contacted at all. Always retryable."""


class EncoderRejected(RuntimeError):
    """The service answered with an error."""

    def __init__(
        self,
        code: str,
        reason: str,
        status: int,
        detail: dict | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason
        self.status = status
        self.detail = detail or {}
        self.retry_after = retry_after


def is_retryable(exc: Exception) -> bool:
    """Whether *exc* should requeue the job rather than fail it."""
    if isinstance(exc, EncoderUnreachable):
        return True
    if isinstance(exc, EncoderRejected):
        return exc.code in RETRYABLE_CODES
    return False


class EncoderClient:
    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def health(self) -> dict:
        """Never raises: the health pill must render 'Offline', not a 500."""
        try:
            response = requests.get(f"{self.base_url}/health", timeout=5)
        except requests.RequestException as exc:
            return {"status": "unreachable", "error": str(exc)}
        if response.status_code != 200:
            return {"status": "unreachable", "error": f"HTTP {response.status_code}"}
        try:
            return response.json()
        except ValueError:
            return {"status": "unreachable", "error": "malformed health response"}

    def submit(self, source_path: str, preset_body: dict, preset_name: str) -> str:
        """Queue an encode, returning the remote job id.

        ``output_path`` is deliberately absent: the encoder derives it, and the
        renamer reads the result back from :meth:`poll`.
        """
        body = {
            "source_path": source_path,
            # The encoder parses a preset *document*, not a bare leaf -- and the
            # version keys are not decoration. Without them HandBrake's
            # presets_do() rejects the whole document ("invalid preset format"),
            # silently falls back to its built-in preset list, and then fails
            # the job with "Invalid preset <name>". Verified against
            # HandBrakeCLI 1.9.2: the identical document imports cleanly with
            # these keys present and is rejected without them.
            "preset_json": {"PresetList": [preset_body], **_PRESET_DOC_VERSION},
            "preset_name": preset_name,
        }
        response = self._request("post", "/jobs", json=body)
        payload = _json_or_empty(response)
        job_id = payload.get("job_id")
        if not job_id:
            raise EncoderRejected(
                "unknown", "202 carried no job_id", response.status_code, payload
            )
        return job_id

    def poll(self, remote_job_id: str) -> dict:
        return _json_or_empty(self._request("get", f"/jobs/{remote_job_id}"))

    def cancel(self, remote_job_id: str) -> bool:
        try:
            response = self._request("delete", f"/jobs/{remote_job_id}")
        except EncoderRejected as exc:
            if exc.status == 404:
                return False
            raise
        return response.status_code in (200, 204)

    def _request(self, method: str, path: str, **kwargs: Any):
        url = f"{self.base_url}{path}"
        try:
            response = getattr(requests, method)(url, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            raise EncoderUnreachable(f"{method.upper()} {url}: {exc}") from exc

        if response.status_code >= 400:
            payload = _json_or_empty(response)
            raise EncoderRejected(
                code=payload.get("code", "unknown"),
                reason=payload.get("reason", f"HTTP {response.status_code}"),
                status=response.status_code,
                detail=payload,
                retry_after=_retry_after(response),
            )
        return response


def _json_or_empty(response: Any) -> dict:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _retry_after(response: Any) -> int | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_RETRY_AFTER
