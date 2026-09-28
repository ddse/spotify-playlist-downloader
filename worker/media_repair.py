import shutil
import subprocess
from pathlib import Path

try:
    from .media_validation import validate_media_file
except ImportError:  # pragma: no cover
    from media_validation import validate_media_file


class MediaRepairError(RuntimeError):
    pass


def _run_ffmpeg(args, timeout=300):
    if shutil.which("ffmpeg") is None:
        raise MediaRepairError("ffmpeg is not installed")
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaRepairError("ffmpeg repair timed out") from exc
    except OSError as exc:
        raise MediaRepairError(f"ffmpeg execution failed: {exc}") from exc
    if result.returncode != 0:
        raise MediaRepairError(result.stderr.strip() or f"ffmpeg exited {result.returncode}")


def repair_media_file(source, destination, media_type="audio", audio_format="mp3", bitrate="320k"):
    source = Path(source)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    remux = destination.with_name(destination.stem + ".remux" + destination.suffix)
    try:
        _run_ffmpeg(["-i", str(source), "-map", "0", "-c", "copy", str(remux)])
        result = validate_media_file(remux, media_type)
        if result.valid:
            remux.replace(destination)
            return destination
    except Exception:
        # Any remux failure (including mocked/process-level failures) should fall back to transcode.
        pass
    finally:
        if remux.exists():
            remux.unlink(missing_ok=True)

    if media_type == "audio":
        ext = audio_format or destination.suffix.lstrip(".") or "mp3"
        target = destination.with_suffix("." + ext)
        _run_ffmpeg(["-i", str(source), "-vn", "-c:a", "libmp3lame", "-b:a", str(bitrate), str(target)])
    else:
        target = destination
        _run_ffmpeg(["-i", str(source), "-c:v", "libx264", "-c:a", "aac", str(target)])
    result = validate_media_file(target, media_type)
    if not result.valid:
        target.unlink(missing_ok=True)
        raise MediaRepairError(result.reason or "repaired media failed validation")
    if target != destination:
        target.replace(destination)
    return destination
