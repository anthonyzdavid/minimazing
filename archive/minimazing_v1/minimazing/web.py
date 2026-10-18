"""A tiny local browser UI over one opened Backup, using only the stdlib.
Binds to 127.0.0.1 so nothing leaves the machine. Lets you browse each dataset
and download a file straight from the backup.
"""
from __future__ import annotations

import html
import json
import mimetypes
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .extractors import READERS, SOURCES

PAGE = """<!doctype html><meta charset=utf-8><title>minimazing</title>
<style>body{{font:15px -apple-system,system-ui,sans-serif;margin:0;background:#f2f2f7;color:#1c1c1e}}
header{{background:#fff;padding:1rem 1.5rem;border-bottom:1px solid #d1d1d6}}
nav a{{display:inline-block;margin-right:.75rem;color:#007aff;text-decoration:none}}
main{{padding:1.5rem;max-width:1000px;margin:auto}}
table{{border-collapse:collapse;width:100%;background:#fff;border-radius:8px;overflow:hidden}}
th,td{{text-align:left;padding:.5rem .75rem;border-bottom:1px solid #e5e5ea;font-size:13px;
vertical-align:top;max-width:380px;overflow-wrap:anywhere}}
th{{background:#f8f8fa}}.muted{{color:#8e8e93}}</style>
<header><strong>minimazing</strong> &nbsp;<span class=muted>{device}</span><nav>{nav}</nav></header>
<main>{body}</main>"""


class Handler(BaseHTTPRequestHandler):
    backup = None  # set by serve()

    def _send(self, body, code=200, ctype="text/html; charset=utf-8", raw=False):
        data = body if raw else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):  # quiet
        pass

    def _nav(self):
        b = self.backup
        links = ['<a href="/">overview</a>']
        for k, (dom, rel) in SOURCES.items():
            if b.find(dom, rel):
                links.append(f'<a href="/d/{k}">{k}</a>')
        return " ".join(links)

    def _page(self, body):
        s = self.backup.summary()
        dev = f"{s['device_name'] or '?'} · iOS {s['ios_version'] or '?'}"
        return PAGE.format(device=html.escape(dev), nav=self._nav(), body=body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        b = self.backup
        if not parts:
            s = b.summary()
            rows = "".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(v))}</td></tr>"
                           for k, v in s.items())
            return self._send(self._page(f"<h2>Backup</h2><table>{rows}</table>"))
        if parts[0] == "d" and len(parts) == 2 and parts[1] in READERS:
            return self._dataset(parts[1])
        if parts[0] == "file" and len(parts) == 2:
            return self._file(parts[1])
        self._send(self._page("<p>Not found.</p>"), code=404)

    def _dataset(self, name):
        records = READERS[name](self.backup)[:2000]
        if not records:
            return self._send(self._page(f"<h2>{name}</h2><p class=muted>No records.</p>"))
        cols = list({k: None for r in records for k in r})
        head = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
        body_rows = []
        for r in records:
            cells = []
            for c in cols:
                v = r.get(c)
                if c == "file_id" and v:
                    v = f'<a href="/file/{v}">download</a>'
                else:
                    v = html.escape(str(v)) if v is not None else ""
                cells.append(f"<td>{v}</td>")
            body_rows.append("<tr>" + "".join(cells) + "</tr>")
        table = f"<h2>{name} <span class=muted>({len(records)})</span></h2>" \
                f"<table><tr>{head}</tr>{''.join(body_rows)}</table>"
        self._send(self._page(table))

    def _file(self, file_id):
        f = self.backup.get(file_id)
        if not f:
            return self._send(self._page("<p>No such file.</p>"), code=404)
        data = self.backup.read(f)
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition", f'attachment; filename="{f.name}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve(backup, host="127.0.0.1", port=8777):
    Handler.backup = backup
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"minimazing UI on http://{host}:{port}  (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        backup.close()
