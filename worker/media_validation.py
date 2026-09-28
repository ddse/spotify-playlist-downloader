import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

CURRENT_VALIDATION_VERSION = 1
FFPROBE_TIMEOUT = int(os.getenv("MEDIA_FFPROBE_TIMEOUT", "30"))


@dataclass
class MediaValidationResult:
    valid: bool
    reason: str | None = None
    container: str | None = None
    duration: float | None = None
    streams: list[dict] = field(default_factory=list)
    codec: str | None = None
    infrastructure_error: bool = False


class MediaValidationError(RuntimeError):
    pass


def _run_ffprobe(path: Path) -> dict:
    if shutil.which("ffprobe") is None:
        raise MediaValidationError("ffprobe is not installed")
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=FFPROBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaValidationError(f"ffprobe timed out after {FFPROBE_TIMEOUT}s") from exc
    except OSError as exc:
        raise MediaValidationError(f"ffprobe execution failed: {exc}") from exc
    if result.returncode != 0:
        raise MediaValidationError(result.stderr.strip() or f"ffprobe exited {result.returncode}")
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise MediaValidationError("ffprobe returned invalid JSON") from exc


def validate_media_file(path, media_type="audio") -> MediaValidationResult:
    path = Path(path)
    if not path.is_file():
        return MediaValidationResult(False, "media file does not exist")
    if path.stat().st_size <= 0:
        return MediaValidationResult(False, "media file is empty")
    try:
        data = _run_ffprobe(path)
    except MediaValidationError as exc:
        msg = str(exc)
        infra = "not installed" in msg or "execution failed" in msg
        return MediaValidationResult(False, msg, infrastructure_error=infra)

    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    duration = float(fmt.get("duration") or 0)
    wanted = "video" if media_type == "video" else "audio"
    matching = [s for s in streams if s.get("codec_type") == wanted]
    if not matching:
        return MediaValidationResult(False, f"no {wanted} stream found", fmt.get("format_name"), duration, streams)
    stream = matching[0]
    codec = stream.get("codec_name")
    if not codec:
        return MediaValidationResult(False, f"{wanted} stream has no codec", fmt.get("format_name"), duration, streams)
    if duration <= 0:
        return MediaValidationResult(False, "media duration is missing or zero", fmt.get("format_name"), duration, streams, codec)
    return MediaValidationResult(True, None, fmt.get("format_name"), duration, streams, codec)


def file_fingerprint(path):
    stat = Path(path).stat()
    return stat.st_size, stat.st_mtime_ns


def needs_validation(row, current_version=CURRENT_VALIDATION_VERSION):
    path = Path(row["file_path"] or "")
    if row["media_validation_status"] != "valid":
        return True
    if int(row["media_validation_version"] or 0) != current_version:
        return True
    if not path.is_file():
        return True
    size, mtime_ns = file_fingerprint(path)
    return (
        row["media_validation_size"] is None
        or row["media_validation_mtime_ns"] is None
        or int(row["media_validation_size"]) != size
        or int(row["media_validation_mtime_ns"]) != mtime_ns
    )
