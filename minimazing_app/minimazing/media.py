"""Make backup media viewable in any browser.

Browsers other than Safari can't show HEIC, and none can play the AMR audio
iOS uses for voicemail. On macOS the built-in `sips` and `afconvert` tools
convert those; elsewhere (or if conversion fails) the original bytes are
served and the browser does what it can. Results are cached per backup.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .backup import Backup, BackupFile

THUMB_PX = 640                # grid thumbnails: long edge in pixels
SMALL_JPEG = 400 * 1024       # JPEGs under this are already thumbnail-sized

_IMAGE_CONVERT = (".heic", ".heif", ".tif", ".tiff", ".dng")
_AUDIO_CONVERT = (".amr", ".awb")
_JPEG_LIKE = (".jpg", ".jpeg", ".thm")


def _run(cmd: list[str]) -> bool:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=120).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _convert(b: Backup, f: BackupFile, kind: str, suffix: str, build) -> Path | None:
    def work():
        out_dir = b.tmp / "media"
        src = b.extract(f, out_dir / "src" / f"{f.file_id}{Path(f.name).suffix}")
        dest = out_dir / kind / f"{f.file_id}{suffix}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        ok = build(src, dest) and dest.exists() and dest.stat().st_size > 0
        return dest if ok else None
    return b.memo(f"media:{kind}:{f.file_id}", work)


def viewable(b: Backup, f: BackupFile) -> tuple[Path, str] | None:
    """A browser-friendly full-quality copy of f, or None to serve f as-is."""
    ext = Path(f.name).suffix.lower()
    if ext in _IMAGE_CONVERT and shutil.which("sips"):
        p = _convert(b, f, "view", ".jpg", lambda s, d: _run(
            ["sips", "-s", "format", "jpeg", "-s", "formatOptions", "95", str(s), "--out", str(d)]))
        return (p, "image/jpeg") if p else None
    if ext in _AUDIO_CONVERT and shutil.which("afconvert"):
        p = _convert(b, f, "view", ".m4a", lambda s, d: _run(
            ["afconvert", "-f", "m4af", "-d", "aac", str(s), str(d)]))
        return (p, "audio/mp4") if p else None
    return None


def thumbnail(b: Backup, f: BackupFile) -> tuple[Path, str] | None:
    """A small JPEG for grids. Thumbnails the phone already made are served
    untouched (re-encoding would only lose quality)."""
    ext = Path(f.name).suffix.lower()
    if ext in _JPEG_LIKE and f.size <= SMALL_JPEG:
        return None
    if not shutil.which("sips"):
        return None
    p = _convert(b, f, "thumb", ".jpg", lambda s, d: _run(
        ["sips", "-s", "format", "jpeg", "-s", "formatOptions", "90",
         "-Z", str(THUMB_PX), str(s), "--out", str(d)]))
    return (p, "image/jpeg") if p else None
