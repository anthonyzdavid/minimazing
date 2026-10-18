"""Turn extractor records into files on disk: CSV/JSON for tabular datasets,
an HTML transcript for messages, and original image/video files for photos.
"""
from __future__ import annotations

import csv
import html
import json
import re
from pathlib import Path

from .backup import Backup


def _safe(name: str) -> str:
    return (re.sub(r"[^\w.@+ -]", "_", name or "unknown").strip() or "unknown")[:120]


def to_json(records: list[dict], dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(records, indent=2, ensure_ascii=False))
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
            w.writerow({k: (v if not isinstance(v, (list, dict)) else json.dumps(v))
                        for k, v in r.items()})
    return dest


def messages_html(records: list[dict], dest: Path) -> Path:
    """Group messages by chat into a simple bubble transcript."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    chats: dict[str, list[dict]] = {}
    for m in records:
        chats.setdefault(m.get("chat") or "(unknown)", []).append(m)
    parts = ["<!doctype html><meta charset=utf-8><title>Messages</title>",
             "<style>body{font:15px -apple-system,sans-serif;max-width:720px;margin:2rem auto;"
             "background:#f2f2f7}h2{margin-top:2rem}.b{padding:.5rem .8rem;border-radius:1rem;"
             "margin:.25rem 0;max-width:75%;white-space:pre-wrap}.me{background:#3b82f6;color:#fff;"
             "margin-left:auto}.them{background:#e5e5ea}.t{font-size:11px;color:#8e8e93;margin:.1rem 0}"
             "</style>"]
    for chat, msgs in chats.items():
        parts.append(f"<h2>{html.escape(str(chat))}</h2>")
        for m in msgs:
            cls = "me" if m.get("from_me") else "them"
            parts.append(f"<div class=t>{html.escape(str(m.get('date') or ''))}</div>")
            parts.append(f"<div class='b {cls}'>{html.escape(str(m.get('text') or ''))}</div>")
    dest.write_text("\n".join(parts), encoding="utf-8")
    return dest


def photos_files(b: Backup, records: list[dict], dest_dir: Path) -> int:
    """Write the original image/video bytes out of the backup."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for rec in records:
        if not rec.get("file_id"):
            continue
        f = b.get(rec["file_id"])
        if f:
            b.extract(f, dest_dir / _safe(rec["filename"]))
            n += 1
    return n
