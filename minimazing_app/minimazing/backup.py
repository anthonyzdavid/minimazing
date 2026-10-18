"""Open an iOS backup folder (Finder/iTunes or iMazing), decrypt it if needed,
and give random access to any file by (domain, relativePath).

Backup folder layout (iOS 10+):
    <UDID>/Info.plist       device name, model, iOS version, last backup date
    <UDID>/Status.plist     snapshot state, IsFullBackup, Version
    <UDID>/Manifest.plist   IsEncrypted, BackupKeyBag, ManifestKey, Applications
    <UDID>/Manifest.db      SQLite: Files(fileID, domain, relativePath, flags, file)
    <UDID>/ab/abcdef...     file contents, named by fileID = sha1("domain-relativePath")
"""
from __future__ import annotations

import hashlib
import os
import plistlib
import shutil
import sqlite3
import struct
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .crypto import Keybag, aes_cbc_decrypt, strip_pkcs7

FLAG_FILE, FLAG_DIR, FLAG_SYMLINK = 1, 2, 4

DEFAULT_LOCATIONS = [
    Path.home() / "Library/Application Support/MobileSync/Backup",  # Finder / iTunes (macOS)
    Path.home() / "Library/Application Support/iMazing/Backups",  # iMazing (snapshots live alongside)
    Path(os.environ.get("APPDATA", "")) / "Apple Computer/MobileSync/Backup",  # iTunes (Windows)
]


@dataclass
class BackupFile:
    file_id: str
    domain: str
    relative_path: str
    flags: int
    size: int = 0
    mtime: int = 0
    protection_class: int | None = None
    wrapped_key: bytes | None = None

    @property
    def name(self) -> str:
        return self.relative_path.rsplit("/", 1)[-1]

    @property
    def is_file(self) -> bool:
        return self.flags == FLAG_FILE


def file_id_for(domain: str, relative_path: str) -> str:
    return hashlib.sha1(f"{domain}-{relative_path}".encode()).hexdigest()


def _parse_mbfile(blob: bytes | None) -> dict:
    """Decode the NSKeyedArchiver 'MBFile' record stored in Files.file."""
    if not blob:
        return {}
    arc = plistlib.loads(blob)
    objs = arc["$objects"]
    top = arc["$top"]["root"]
    root = objs[top.data if isinstance(top, plistlib.UID) else top]
    out = {
        "size": root.get("Size", 0),
        "mtime": root.get("LastModified", 0),
        "protection_class": root.get("ProtectionClass"),
    }
    ek = root.get("EncryptionKey")
    if ek is not None:
        val = objs[ek.data] if isinstance(ek, plistlib.UID) else ek
        if isinstance(val, dict):
            val = val.get("NS.data")
        if isinstance(val, (bytes, bytearray)) and len(val) >= 44:
            out["wrapped_key"] = bytes(val[4:])  # first 4 bytes repeat the class
    return out


class Backup:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.info = self._plist("Info.plist")
        self.status = self._plist("Status.plist")
        self.manifest = self._plist("Manifest.plist")
        self.encrypted = bool(self.manifest.get("IsEncrypted"))
        self.keybag: Keybag | None = None
        self._db: sqlite3.Connection | None = None
        self._tmp = tempfile.mkdtemp(prefix="minimazing-")
        self._storage_cache: list[dict] | None = None
        # The web UI serves requests from several threads; one lock guards the
        # shared Manifest.db connection, and memo() keeps one result per key.
        self._lock = threading.RLock()
        self._memo: dict = {}
        self._memo_locks: dict[str, threading.Lock] = {}
        self._sqlite_cache: dict[tuple[str, str], Path] = {}

    # ---------- metadata ----------
    def _plist(self, name: str) -> dict:
        p = self.path / name
        if not p.exists():
            return {}
        with open(p, "rb") as fh:
            return plistlib.load(fh)

    @property
    def udid(self) -> str:
        return self.info.get("Unique Identifier") or self.path.name

    def summary(self) -> dict:
        i = self.info
        return {
            "udid": self.udid,
            "path": str(self.path),
            "device_name": i.get("Device Name") or i.get("Display Name"),
            "product_type": i.get("Product Type"),
            "ios_version": i.get("Product Version"),
            "last_backup": str(i.get("Last Backup Date", "")),
            "encrypted": self.encrypted,
            "unlocked": self._db is not None,
            "full_backup": self.status.get("IsFullBackup"),
            "snapshot_state": self.status.get("SnapshotState"),
        }

    # ---------- unlock / manifest ----------
    def open(self, password: str | None = None) -> "Backup":
        db_path = self.path / "Manifest.db"
        if self.encrypted:
            if password is None:
                raise ValueError("This backup is encrypted; a password is required.")
            self.keybag = Keybag.parse(self.manifest["BackupKeyBag"])
            self.keybag.unlock(password)
            mk = self.manifest["ManifestKey"]
            (mclass,) = struct.unpack("<I", mk[:4])
            key = self.keybag.unwrap_key(mclass, mk[4:])
            plain = strip_pkcs7(aes_cbc_decrypt(key, db_path.read_bytes()))
            db_path = Path(self._tmp) / "Manifest.db"
            db_path.write_bytes(plain)
        else:
            # copy so we never touch the original (it may be mid-backup / read-only)
            tmp = Path(self._tmp) / "Manifest.db"
            shutil.copyfile(db_path, tmp)
            db_path = tmp
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        return self

    @property
    def tmp(self) -> Path:
        return Path(self._tmp)

    def close(self) -> None:
        if self._db:
            self._db.close()
        shutil.rmtree(self._tmp, ignore_errors=True)

    @property
    def db(self) -> sqlite3.Connection:
        if self._db is None:
            raise RuntimeError("Call open() first")
        return self._db

    def _row_to_file(self, row) -> BackupFile:
        meta = _parse_mbfile(row["file"])
        return BackupFile(
            file_id=row["fileID"],
            domain=row["domain"],
            relative_path=row["relativePath"],
            flags=row["flags"],
            size=meta.get("size", 0),
            mtime=meta.get("mtime", 0),
            protection_class=meta.get("protection_class"),
            wrapped_key=meta.get("wrapped_key"),
        )

    def _query(self, sql: str, args=()) -> list:
        with self._lock:
            return self.db.execute(sql, args).fetchall()

    def memo(self, key: str, fn):
        """Compute fn() once per backup per key, even under concurrent requests."""
        with self._lock:
            if key in self._memo:
                return self._memo[key]
            lock = self._memo_locks.setdefault(key, threading.Lock())
        with lock:
            if key not in self._memo:
                self._memo[key] = fn()
            return self._memo[key]

    # ---------- queries ----------
    def domains(self) -> list[tuple[str, int]]:
        q = "SELECT domain, COUNT(*) n FROM Files WHERE flags=1 GROUP BY domain ORDER BY domain"
        return [(r["domain"], r["n"]) for r in self._query(q)]

    def files(self, domain: str | None = None, like: str | None = None,
              limit: int | None = None) -> Iterator[BackupFile]:
        q = "SELECT * FROM Files WHERE flags=1"
        args: list = []
        if domain:
            q += " AND domain = ?"
            args.append(domain)
        if like:
            q += " AND relativePath LIKE ?"
            args.append(like)
        q += " ORDER BY domain, relativePath"
        if limit:
            q += f" LIMIT {int(limit)}"
        for row in self._query(q, args):
            yield self._row_to_file(row)

    def find(self, domain: str, relative_path: str) -> BackupFile | None:
        rows = self._query(
            "SELECT * FROM Files WHERE domain=? AND relativePath=?", (domain, relative_path))
        return self._row_to_file(rows[0]) if rows else None

    def get(self, file_id: str) -> BackupFile | None:
        rows = self._query("SELECT * FROM Files WHERE fileID=?", (file_id,))
        return self._row_to_file(rows[0]) if rows else None

    def index(self, domain: str) -> dict[str, "BackupFile"]:
        """relativePath -> BackupFile for every file in one domain (cached), for
        readers that resolve thousands of paths (photos, attachments, media)."""
        return self.memo(f"_index:{domain}",
                         lambda: {f.relative_path: f for f in self.files(domain)})

    def list_dir(self, domain: str, path: str = "") -> dict:
        """Immediate children of a folder inside one domain, for the file browser."""
        prefix = f"{path.rstrip('/')}/" if path else ""
        esc = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = self._query(
            "SELECT * FROM Files WHERE domain=? AND relativePath LIKE ? ESCAPE '\\' "
            "AND relativePath != ''", (domain, esc + "%"))
        dirs: dict[str, int] = {}
        files = []
        for row in rows:
            rest = row["relativePath"][len(prefix):]
            if not rest:
                continue
            head, sep, _ = rest.partition("/")
            if sep:
                if row["flags"] == FLAG_FILE:
                    dirs[head] = dirs.get(head, 0) + 1
                else:
                    dirs.setdefault(head, 0)
            elif row["flags"] == FLAG_DIR:
                dirs.setdefault(head, 0)
            elif row["flags"] == FLAG_FILE:
                f = self._row_to_file(row)
                files.append({"name": f.name, "file_id": f.file_id, "size": f.size,
                              "mtime": f.mtime})
        return {
            "domain": domain, "path": path.rstrip("/"),
            "dirs": [{"name": k, "files": v} for k, v in sorted(dirs.items(), key=lambda x: x[0].lower())],
            "files": sorted(files, key=lambda x: x["name"].lower()),
        }

    # ---------- content ----------
    def _blob_path(self, f: BackupFile) -> Path:
        p = self.path / f.file_id[:2] / f.file_id
        return p if p.exists() else self.path / f.file_id  # pre-iOS 10 flat layout

    def read(self, f: BackupFile) -> bytes:
        data = self._blob_path(f).read_bytes()
        if self.encrypted and f.wrapped_key:
            key = self.keybag.unwrap_key(f.protection_class, f.wrapped_key)
            data = aes_cbc_decrypt(key, data)
            data = data[: f.size] if f.size else strip_pkcs7(data)
        return data

    def iter_range(self, f: BackupFile, start: int = 0, end: int | None = None,
                   chunk: int = 1 << 20) -> Iterator[bytes]:
        """Yield plaintext bytes [start, end) without reading the whole file, so a
        multi-GB video can be streamed. AES-CBC decrypts any block on its own:
        plain[i] = D(cipher[i]) XOR cipher[i-1], so seek one block back for the IV."""
        path = self._blob_path(f)
        size = f.size or path.stat().st_size
        end = size if end is None else min(end, size)
        if start >= end:
            return
        encrypted = self.encrypted and f.wrapped_key
        key = self.keybag.unwrap_key(f.protection_class, f.wrapped_key) if encrypted else None
        with open(path, "rb") as fh:
            if not encrypted:
                fh.seek(start)
                left = end - start
                while left > 0:
                    data = fh.read(min(chunk, left))
                    if not data:
                        return
                    left -= len(data)
                    yield data
                return
            block = start // 16
            iv = b"\0" * 16
            if block:
                fh.seek((block - 1) * 16)
                iv = fh.read(16)
            pos = block * 16
            fh.seek(pos)
            chunk -= chunk % 16
            while pos < end:
                ct = fh.read(chunk)
                if not ct:
                    return
                ct = ct[: len(ct) - len(ct) % 16]
                if not ct:
                    return
                pt = aes_cbc_decrypt(key, ct, iv)
                iv = ct[-16:]
                lo = max(start - pos, 0)
                hi = min(end - pos, len(pt))
                pos += len(ct)
                yield pt[lo:hi]

    def extract(self, f: BackupFile, dest: str | Path) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if f.size > 64 << 20:  # stream big media instead of holding it in memory
            with open(dest, "wb") as out:
                for part in self.iter_range(f):
                    out.write(part)
        else:
            dest.write_bytes(self.read(f))
        if f.mtime:
            os.utime(dest, (f.mtime, f.mtime))
        return dest

    def domain_sizes(self) -> dict[str, int]:
        """Bytes per domain (one pass over every file record, cached). SMS
        attachments live in MediaDomain, so they're reported as 'MediaDomain/SMS'."""
        def build():
            sizes: dict[str, int] = {}
            for f in self.files():
                d = f.domain
                if d in ("HomeDomain", "MediaDomain") and f.relative_path.startswith("Library/SMS"):
                    d = "MediaDomain/SMS"
                sizes[d] = sizes.get(d, 0) + f.size
            return sizes
        return self.memo("_domain_sizes", build)

    def storage_breakdown(self) -> list[dict]:
        """Approximate 'how big is each category' like the real app's storage bar,
        by summing recorded file sizes per domain bucket."""
        if self._storage_cache is not None:
            return self._storage_cache
        buckets = {"Photos": 0, "Messages": 0, "WhatsApp": 0, "App Data": 0, "Other": 0}
        for domain, size in self.domain_sizes().items():
            if domain == "CameraRollDomain":
                buckets["Photos"] += size
            elif domain == "MediaDomain/SMS":
                buckets["Messages"] += size
            elif "whatsapp" in domain.lower():
                buckets["WhatsApp"] += size
            elif domain.startswith("AppDomain"):
                buckets["App Data"] += size
            else:
                buckets["Other"] += size
        total = sum(buckets.values()) or 1
        self._storage_cache = [
            {"label": k, "bytes": v, "pct": round(v / total * 100, 1)}
            for k, v in buckets.items() if v
        ]
        return self._storage_cache

    def sqlite(self, domain: str, relative_path: str) -> sqlite3.Connection | None:
        """Materialise a (decrypted) SQLite DB + its -wal/-shm and open it read-only."""
        with self._lock:
            path = self._sqlite_cache.get((domain, relative_path))
            if path is None:
                f = self.find(domain, relative_path)
                if not f:
                    return None
                out = Path(self._tmp) / "db" / f.file_id
                self.extract(f, out / f.name)
                for suffix in ("-wal", "-shm"):
                    side = self.find(domain, relative_path + suffix)
                    if side:
                        self.extract(side, out / (f.name + suffix))
                path = self._sqlite_cache[(domain, relative_path)] = out / f.name
        con = sqlite3.connect(path, check_same_thread=False)
        con.row_factory = sqlite3.Row
        return con


def discover(extra: list[str | Path] | None = None) -> list[Path]:
    """Find backup folders (any dir containing Manifest.plist/Info.plist)."""
    roots = [Path(p).expanduser() for p in (extra or [])] + DEFAULT_LOCATIONS
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        if (root / "Manifest.plist").exists():
            found.append(root)
            continue
        for child in sorted(root.iterdir()):
            if child.is_dir() and (child / "Manifest.plist").exists():
                found.append(child)
    return found
