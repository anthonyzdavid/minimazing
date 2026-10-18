"""Local web app: a JSON API (this file) plus the static frontend in
minimazing/static/. Binds to 127.0.0.1 by default so nothing leaves the
machine. Stdlib only - no Flask/etc. dependency.

The server can start with no backup chosen, or with a locked one: the
browser then shows a backup picker / password prompt (POST /api/open,
POST /api/unlock) before the main UI.
"""
from __future__ import annotations

import csv
import io
import json
import mimetypes
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import media
from .backup import Backup, discover
from .crypto import BackupPasswordError
from .exporters import messages_html_string
from .extractors import CATALOG, READERS, app_icon, available, records

STATIC_DIR = Path(__file__).parent / "static"
THREAD_VIEWS = ("messages", "whatsapp")
mimetypes.add_type("audio/mp4", ".m4a")
mimetypes.add_type("audio/mp4", ".m4r")
mimetypes.add_type("audio/amr", ".amr")
mimetypes.add_type("video/quicktime", ".mov")
mimetypes.add_type("image/heic", ".heic")


def _json_bytes(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def _csv_bytes(records_: list[dict]) -> bytes:
    if not records_:
        return b""
    cols = list({k: None for r in records_ for k in r})
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in records_:
        w.writerow({k: (v if not isinstance(v, (list, dict)) else json.dumps(v, ensure_ascii=False))
                    for k, v in r.items()})
    return buf.getvalue().encode("utf-8")


def _matches(rec: dict, q: str) -> bool:
    for v in rec.values():
        if v is None or isinstance(v, bool):
            continue
        if isinstance(v, list):
            v = " ".join(str(x.get("name", "")) if isinstance(x, dict) else str(x) for x in v)
        if q in str(v).lower():
            return True
    return False


class App:
    """Which backup is open. Shared by all request threads."""

    def __init__(self, backup: Backup | None, extra_roots: list[str] | None = None):
        self.backup = backup
        self.extra_roots = extra_roots or []
        self.lock = threading.Lock()

    def stage(self) -> str:
        if self.backup is None:
            return "pick"
        return "ready" if self.backup.summary()["unlocked"] else "locked"

    def switch(self, backup: Backup | None):
        with self.lock:
            old, self.backup = self.backup, backup
        if old is not None and old is not backup:
            old.close()


class Handler(BaseHTTPRequestHandler):
    app: App = None  # set by serve()
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # quiet access log
        pass

    # ---------- helpers ----------
    def _send_bytes(self, data: bytes, code=200, ctype="application/octet-stream",
                    extra_headers: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _send_json(self, obj, code=200):
        self._send_bytes(_json_bytes(obj), code, "application/json; charset=utf-8",
                         {"Cache-Control": "no-store"})

    def _range(self, size: int) -> tuple[int, int] | None:
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "").strip())
        if not m or size == 0:
            return None
        a, z = m.groups()
        if a == "":
            start, end = max(size - int(z or 0), 0), size
        else:
            start, end = int(a), (int(z) + 1 if z else size)
        end = min(end, size)
        return (start, end) if start < end else None

    def _send_stream(self, size: int, chunks, ctype: str, headers: dict, rng):
        """Send a body produced by chunks(start, end), honoring Range so video and
        audio can seek (Safari won't even start playback without it)."""
        start, end = rng if rng else (0, size)
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start))
        self.send_header("Cache-Control", "private, max-age=3600")
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end - 1}/{size}")
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            for part in chunks(start, end):
                self.wfile.write(part)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the player seeked or closed; normal for media

    def _send_path(self, path: Path, ctype: str, headers: dict | None = None):
        size = path.stat().st_size

        def chunks(start, end):
            with open(path, "rb") as fh:
                fh.seek(start)
                left = end - start
                while left > 0:
                    data = fh.read(min(1 << 20, left))
                    if not data:
                        return
                    left -= len(data)
                    yield data
        self._send_stream(size, chunks, ctype, headers or {}, self._range(size))

    def _send_backup_file(self, b: Backup, f, download=False):
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        headers = {}
        if download:
            headers["Content-Disposition"] = (
                "attachment; filename*=UTF-8''" + urllib.parse.quote(f.name))
        size = f.size or b._blob_path(f).stat().st_size
        self._send_stream(size, lambda s, e: b.iter_range(f, s, e), ctype, headers, self._range(size))

    def _static(self, rel_path: str):
        path = (STATIC_DIR / rel_path).resolve()
        if STATIC_DIR.resolve() not in path.parents or not path.is_file():
            return self._send_json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self._send_bytes(path.read_bytes(), 200, ctype, {"Cache-Control": "no-cache"})

    def _host_ok(self) -> bool:
        """Only answer to our own hostname. Blocks DNS-rebinding pages from
        reading the API through a hostname that resolves to 127.0.0.1."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        bound = self.server.server_address[0]
        return host in ("127.0.0.1", "localhost", "::1", bound)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(min(n, 1 << 16)))
        except ValueError:
            return {}

    # ---------- routing ----------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self._host_ok():
            return self._send_json({"error": "forbidden host"}, 403)
        parsed = urllib.parse.urlparse(self.path)
        parts = [urllib.parse.unquote(p) for p in parsed.path.split("/") if p]
        qs = {k: v[-1] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        try:
            if not parts:
                return self._static("index.html")
            if parts[0] == "api":
                return self._api_get(parts[1:], qs)
            return self._static("/".join(parts))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # never let a stack trace leak to the browser
            self._send_json({"error": str(e)}, 500)

    def do_POST(self):
        if not self._host_ok():
            return self._send_json({"error": "forbidden host"}, 403)
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlparse(origin).netloc != self.headers.get("Host"):
            return self._send_json({"error": "cross-origin request refused"}, 403)
        parts = [p for p in urllib.parse.urlparse(self.path).path.split("/") if p]
        try:
            return self._api_post(parts[1:] if parts[:1] == ["api"] else [], self._body())
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _api_post(self, parts, body):
        app = self.app
        if parts == ["open"]:
            path = Path(body.get("path") or "").expanduser()
            if not (path / "Manifest.plist").exists():
                return self._send_json({"error": "No backup (Manifest.plist) in that folder."}, 400)
            b = Backup(path)
            if not b.encrypted:
                b.open()
            app.switch(b)
            return self._send_json({"stage": app.stage()})
        if parts == ["unlock"]:
            b = app.backup
            if b is None:
                return self._send_json({"error": "no backup selected"}, 409)
            try:
                b.open(body.get("password") or "")
            except BackupPasswordError:
                return self._send_json({"error": "Wrong password"}, 401)
            return self._send_json({"stage": app.stage()})
        if parts == ["close"]:
            app.switch(None)
            return self._send_json({"stage": app.stage()})
        return self._send_json({"error": "not found"}, 404)

    def _api_get(self, parts, qs):
        app = self.app
        if parts == ["state"]:
            b = app.backup
            return self._send_json({"stage": app.stage(), "summary": b.summary() if b else None})
        if parts == ["backups"]:
            out = []
            for p in discover(app.extra_roots):
                try:
                    out.append(Backup(p).summary())
                except Exception as e:
                    out.append({"path": str(p), "error": str(e)})
            return self._send_json(out)

        b = app.backup
        if b is None or app.stage() != "ready":
            return self._send_json({"error": "backup is not open", "stage": app.stage()}, 409)

        if parts == ["summary"]:
            return self._send_json(b.summary())
        if parts == ["storage"]:
            return self._send_json(b.storage_breakdown())

        if parts == ["datasets"]:
            out = []
            for d in CATALOG:
                entry = dict(d)
                entry["available"] = bool(d["implemented"]) and available(b, d["key"])
                out.append(entry)
            return self._send_json(out)

        if len(parts) == 2 and parts[0] == "count":
            key = parts[1]
            if key == "files":
                return self._send_json({"count": sum(n for _, n in b.domains())})
            if key not in READERS:
                return self._send_json({"error": f"unknown dataset {key}"}, 404)
            return self._send_json({"count": len(records(b, key))})

        if len(parts) == 2 and parts[0] == "dataset":
            key = parts[1]
            if key not in READERS:
                return self._send_json({"error": f"unknown dataset {key}"}, 404)
            recs = records(b, key)
            q = (qs.get("q") or "").strip().lower()
            if q:
                recs = [r for r in recs if _matches(r, q)]
            offset = int(qs.get("offset") or 0)
            limit = int(qs.get("limit") or 0) or len(recs)
            return self._send_json({"key": key, "total": len(recs), "offset": offset,
                                    "records": recs[offset:offset + limit]})

        if len(parts) == 2 and parts[0] == "chats" and parts[1] in THREAD_VIEWS:
            return self._send_json(self._chats(b, parts[1], (qs.get("q") or "").strip().lower()))

        if len(parts) == 3 and parts[0] == "chat" and parts[1] in THREAD_VIEWS:
            groups = self._grouped(b, parts[1])
            msgs = groups.get(parts[2])
            if msgs is None:
                return self._send_json({"error": "no such chat"}, 404)
            return self._send_json({"chat": msgs[0]["chat"] if msgs else "", "messages": msgs})

        if len(parts) == 2 and parts[0] == "export":
            key, _, fmt = parts[1].rpartition(".")
            if not key:
                key, fmt = parts[1], "json"
            if key not in READERS:
                return self._send_json({"error": f"unknown dataset {key}"}, 404)
            recs = records(b, key)
            chat = qs.get("chat")
            if chat and key in THREAD_VIEWS:
                recs = self._grouped(b, key).get(chat, [])
            name = key
            if chat:
                name += "-" + re.sub(r"[^\w.-]+", "_", recs[0]["chat"] if recs else chat)
            if fmt == "csv":
                data, ctype = _csv_bytes(recs), "text/csv; charset=utf-8"
            elif fmt == "html" and key in THREAD_VIEWS:
                data, ctype = messages_html_string(recs).encode("utf-8"), "text/html; charset=utf-8"
            else:
                fmt = "json"
                data, ctype = _json_bytes(recs), "application/json"
            headers = {"Content-Disposition": f'attachment; filename="{name}.{fmt}"'}
            return self._send_bytes(data, 200, ctype, headers)

        if len(parts) == 2 and parts[0] in ("file", "view", "thumb"):
            f = b.get(parts[1])
            if not f:
                return self._send_json({"error": "no such file"}, 404)
            if parts[0] != "file" and not qs.get("download"):
                conv = (media.thumbnail if parts[0] == "thumb" else media.viewable)(b, f)
                if conv:
                    return self._send_path(*conv)
            return self._send_backup_file(b, f, download=bool(qs.get("download")))

        if len(parts) == 2 and parts[0] == "appicon":
            png = app_icon(b, parts[1])
            if not png:
                return self._send_json({"error": "no icon"}, 404)
            return self._send_bytes(png, 200, "image/png", {"Cache-Control": "private, max-age=86400"})

        if parts == ["domains"]:
            return self._send_json([{"domain": d, "files": n} for d, n in b.domains()])
        if parts == ["fs"]:
            domain = qs.get("domain")
            if not domain:
                return self._send_json({"error": "domain required"}, 400)
            return self._send_json(b.list_dir(domain, qs.get("path") or ""))

        return self._send_json({"error": "not found"}, 404)

    # ---------- chats ----------
    @staticmethod
    def _grouped(b: Backup, key: str) -> dict[str, list[dict]]:
        def build():
            groups: dict[str, list[dict]] = {}
            for m in records(b, key):
                groups.setdefault(str(m.get("chat_id") if m.get("chat_id") is not None
                                      else m.get("chat")), []).append(m)
            return groups
        return b.memo(f"_grouped:{key}", build)

    def _chats(self, b: Backup, key: str, q: str) -> list[dict]:
        out = []
        for cid, msgs in self._grouped(b, key).items():
            name = msgs[0]["chat"]
            if q and q not in (name or "").lower() and not any(
                    q in (m.get("text") or "").lower() for m in msgs):
                continue
            last = msgs[-1]
            preview = last.get("text") or (
                f"📎 {last['attachments'][0]['name']}" if last.get("attachments") else "")
            out.append({"chat_id": cid, "name": name, "count": len(msgs),
                        "last_date": last.get("date"), "preview": preview[:140]})
        out.sort(key=lambda c: c["last_date"] or "", reverse=True)
        return out


def serve(backup: Backup | None, host="127.0.0.1", port=8777, open_browser=True,
          extra_roots: list[str] | None = None):
    Handler.app = App(backup, extra_roots)
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}"
    print(f"minimazing running at {url}  (Ctrl-C to stop)")
    if open_browser:
        import webbrowser
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        if Handler.app.backup:
            Handler.app.backup.close()
