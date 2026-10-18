"""Turn extractor records into files on disk: CSV/JSON for tabular datasets,
an HTML transcript for messages (with attachments), and original
image/video/audio files for media datasets.
"""
from __future__ import annotations

import csv
import html
import json
import re
from pathlib import Path

from .backup import Backup

_IMG_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic")
_VID_EXT = (".mov", ".mp4", ".m4v")
_AUD_EXT = (".m4a", ".caf", ".mp3", ".amr", ".aac")


def _safe(name: str) -> str:
    return (re.sub(r"[^\w.@+ -]", "_", name or "unknown").strip() or "unknown")[:120]


def to_json(records: list[dict], dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    return dest


def to_csv(records: list[dict], dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not records:
        dest.write_text("")
        return dest
    cols = list({k: None for r in records for k in r})
    with open(dest, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in records:
            w.writerow({k: (v if not isinstance(v, (list, dict)) else json.dumps(v, ensure_ascii=False))
                        for k, v in r.items()})
    return dest


def _attachment_html(a: dict, href: str | None) -> str:
    name = html.escape(a.get("name") or "attachment")
    if not href:
        return f"<div class=att>📎 {name}</div>"
    src = html.escape(href, quote=True)
    low = (a.get("name") or "").lower()
    if low.endswith(_IMG_EXT) and not low.endswith(".heic"):
        return f"<a href='{src}'><img class=att-img src='{src}' alt='{name}'></a>"
    if low.endswith(_VID_EXT):
        return f"<video class=att-img controls src='{src}'></video>"
    if low.endswith(_AUD_EXT):
        return f"<audio controls src='{src}'></audio>"
    return f"<div class=att>📎 <a href='{src}'>{name}</a></div>"


def messages_html_string(records: list[dict], attachment_href=None) -> str:
    """Group messages by chat into a simple bubble transcript. attachment_href
    maps an attachment dict to a link target (or None for name-only)."""
    chats: dict[str, list[dict]] = {}
    for m in records:
        chats.setdefault(m.get("chat") or "(unknown)", []).append(m)
    parts = ["<!doctype html><meta charset=utf-8><title>Messages</title>",
             "<style>body{font:15px -apple-system,sans-serif;max-width:720px;margin:2rem auto;"
             "background:#f2f2f7;padding:0 1rem}h2{margin-top:2rem}.b{padding:.5rem .8rem;border-radius:1rem;"
             "margin:.25rem 0;max-width:75%;width:fit-content;white-space:pre-wrap}.me{background:#3b82f6;color:#fff;"
             "margin-left:auto}.them{background:#e5e5ea}.t{font-size:11px;color:#8e8e93;margin:.1rem 0}"
             ".me+.t,.t.me{text-align:right}.s{font-size:11px;color:#8e8e93}.att-img{max-width:100%;"
             "max-height:320px;border-radius:.6rem;display:block;margin:.2rem 0}.me a{color:#fff}</style>"]
    for chat, msgs in chats.items():
        parts.append(f"<h2>{html.escape(str(chat))}</h2>")
        for m in msgs:
            cls = "me" if m.get("from_me") else "them"
            who = "" if m.get("from_me") or not m.get("sender") else \
                f"{html.escape(str(m['sender']))} · "
            parts.append(f"<div class='t {cls}'>{who}{html.escape(str(m.get('date') or ''))}</div>")
            body = html.escape(str(m.get("text") or ""))
            for a in m.get("attachments") or []:
                body += _attachment_html(a, attachment_href(a) if attachment_href else None)
            parts.append(f"<div class='b {cls}'>{body}</div>")
    return "\n".join(parts)


def messages_html(records: list[dict], dest: Path, attachment_href=None) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(messages_html_string(records, attachment_href), encoding="utf-8")
    return dest


def attachments_files(b: Backup, records: list[dict], dest_dir: Path) -> dict[str, str]:
    """Copy every message attachment out; returns file_id -> path relative to
    dest_dir.parent so the HTML transcript can link to them."""
    written: dict[str, str] = {}
    for m in records:
        for a in m.get("attachments") or []:
            fid = a.get("file_id")
            if not fid or fid in written:
                continue
            f = b.get(fid)
            if not f:
                continue
            name = f"{fid[:8]}-{_safe(a.get('name') or f.name)}"
            b.extract(f, dest_dir / name)
            written[fid] = f"{dest_dir.name}/{name}"
    return written


def media_files(b: Backup, records: list[dict], dest_dir: Path, id_key="file_id",
                name_key="filename") -> int:
    """Write the original bytes of each record's file out of the backup."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    seen: set[str] = set()
    for rec in records:
        fid = rec.get(id_key)
        if not fid:
            continue
        f = b.get(fid)
        if not f:
            continue
        name = _safe(rec.get(name_key) or f.name)
        if name in seen:  # e.g. two voicemails from the same caller
            name = f"{fid[:8]}-{name}"
        seen.add(name)
        b.extract(f, dest_dir / name)
        n += 1
    return n


def photos_files(b: Backup, records: list[dict], dest_dir: Path) -> int:
    """Write each photo/video at the best quality the backup holds: the
    original when present, otherwise the largest preview iOS kept for it."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for rec in records:
        fid = rec.get("file_id") or rec.get("best_id")
        if not fid:
            continue
        f = b.get(fid)
        if not f:
            continue
        name = _safe(rec["filename"])
        if not rec.get("file_id"):  # a preview, not the original: say so in the name
            name = f"{name.rsplit('.', 1)[0]} ({rec.get('quality') or 'preview'}){Path(f.name).suffix}"
        b.extract(f, dest_dir / name)
        n += 1
    return n
