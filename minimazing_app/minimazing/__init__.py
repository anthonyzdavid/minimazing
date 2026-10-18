"""minimazing - a tiny iMazing-style iOS backup reader.

Capture (optional, needs libimobiledevice) -> open & decrypt a backup ->
read datasets (messages, calls, contacts, calendar, notes, photos, whatsapp) ->
export to CSV/JSON/HTML/original files.
"""
from .backup import Backup, discover
from .crypto import BackupPasswordError

__all__ = ["Backup", "discover", "BackupPasswordError"]
__version__ = "0.1.0"
