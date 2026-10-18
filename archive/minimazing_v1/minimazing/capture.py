"""Capture a fresh backup from a connected device.

iMazing does this by linking Apple's private MobileDevice.framework and driving
lockdownd -> com.apple.mobilebackup2 itself (its binary references
AppleMobileBackup, AMDevice*, lockdown, usbmuxd and a full mobilebackup2 state
machine). We don't reimplement that wire protocol; instead this wraps the
open-source equivalent, libimobiledevice's `idevicebackup2`, which speaks the
same lockdownd/mobilebackup2 services. The resulting folder is byte-for-byte a
standard backup that backup.Backup can open.

Install the CLI once:  brew install libimobiledevice   (macOS)
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class CaptureError(RuntimeError):
    pass


def _tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise CaptureError(
            f"'{name}' not found. Install libimobiledevice "
            "(brew install libimobiledevice) to capture from a device.")
    return path


def paired_devices() -> list[str]:
    out = subprocess.run([_tool("idevice_id"), "-l"], capture_output=True, text=True)
    return [u.strip() for u in out.stdout.splitlines() if u.strip()]


def device_info(udid: str | None = None) -> dict:
    cmd = [_tool("ideviceinfo")]
    if udid:
        cmd += ["-u", udid]
    out = subprocess.run(cmd, capture_output=True, text=True)
    info = {}
    for line in out.stdout.splitlines():
        if ": " in line:
            k, v = line.split(": ", 1)
            info[k] = v
    return info


def set_encryption(password: str, udid: str | None = None, on: bool = True) -> None:
    """Enable/disable backup encryption on the device (iOS performs the crypto)."""
    cmd = [_tool("idevicebackup2")]
    if udid:
        cmd += ["-u", udid]
    cmd += ["encryption", "on" if on else "off", password]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise CaptureError(r.stderr.strip() or "Failed to change encryption")


def backup(dest_root: str | Path, udid: str | None = None,
           full: bool = False, progress=print) -> Path:
    """Run a mobilebackup2 backup into dest_root/<udid>/. Device must be unlocked
    and trusted. Returns the backup folder path."""
    dest_root = Path(dest_root).expanduser()
    dest_root.mkdir(parents=True, exist_ok=True)
    cmd = [_tool("idevicebackup2")]
    if udid:
        cmd += ["-u", udid]
    if full:
        cmd.append("--full")
    cmd += ["backup", str(dest_root)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in proc.stdout:  # mobilebackup2 streams progress here
        progress(line.rstrip())
    if proc.wait():
        raise CaptureError("Backup failed; see output above.")
    target = dest_root / (udid or "")
    return target if target.exists() else dest_root
