"""Command line: list backups, inspect one, export datasets, or launch the web UI.

Examples:
    python -m minimazing list
    python -m minimazing info  <backup_path> [--password PW]
    python -m minimazing export <backup_path> messages --password PW --out ./out
    python -m minimazing export <backup_path> all --password PW --out ./out --files
    python -m minimazing web   [<backup_path>] [--password PW]
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from . import Backup, discover
from .crypto import BackupPasswordError
from .extractors import READERS, CATALOG, available, records
from . import exporters as ex

MEDIA_FILES = {  # dataset -> (subfolder, record key holding the file id)
    "voicememos": ("voicememos", "file_id"),
    "voicemail": ("voicemail", "file_id"),
    "ringtones": ("ringtones", "file_id"),
}


def _password(args) -> str | None:
    pw = args.password
    if pw is None and getattr(args, "password_file", None):
        pw = Path(args.password_file).expanduser().read_text().strip()
    return pw


def _open(args) -> Backup:
    b = Backup(args.path)
    pw = _password(args)
    if b.encrypted and pw is None:
        pw = getpass.getpass("Backup password: ")
    try:
        return b.open(pw)
    except BackupPasswordError as e:
        sys.exit(f"error: {e}")


def cmd_list(_):
    found = discover()
    if not found:
        print("no backups found in the default Finder/iTunes/iMazing locations")
    for p in found:
        try:
            s = Backup(p).summary()
            lock = "encrypted" if s["encrypted"] else "plain"
            print(f"{s['device_name'] or '?':<22} {s['ios_version'] or '':<8} {lock:<10} {p}")
        except Exception as e:
            print(f"(could not read {p}: {e})")


def cmd_info(args):
    b = _open(args)
    s = b.summary()
    for k, v in s.items():
        print(f"{k:>16}: {v}")
    print("\n  datasets present:")
    for d in CATALOG:
        if not d["implemented"]:
            mark = "(not available from backups)"
        else:
            mark = "yes" if available(b, d["key"]) else "-"
        print(f"    {d['key']:<11} {mark}")
    b.close()


def _export_one(b: Backup, ds: str, out: Path, files: bool):
    recs = records(b, ds)
    print(f"{ds}: {len(recs)} records")
    ex.to_json(recs, out / f"{ds}.json")
    ex.to_csv(recs, out / f"{ds}.csv")
    if ds in ("messages", "whatsapp"):
        href = None
        if files:
            written = ex.attachments_files(b, recs, out / f"{ds}-attachments")
            print(f"  wrote {len(written)} attachments")
            href = lambda a: written.get(a.get("file_id"))  # noqa: E731
        ex.messages_html(recs, out / f"{ds}.html", href)
    if files and ds == "photos":
        n = ex.photos_files(b, recs, out / "photos")
        print(f"  wrote {n} photo/video files (originals where present, else best preview)")
    if files and ds in MEDIA_FILES:
        folder, key = MEDIA_FILES[ds]
        n = ex.media_files(b, recs, out / folder, id_key=key)
        print(f"  wrote {n} audio files")


def cmd_export(args):
    b = _open(args)
    out = Path(args.out)
    ds = args.dataset
    keys = list(READERS) if ds == "all" else [ds]
    for k in keys:
        if k not in READERS:
            sys.exit(f"unknown dataset {k}; choose from all, {', '.join(READERS)}")
    for k in keys:
        _export_one(b, k, out, args.files)
    print(f"written to {out.resolve()}")
    b.close()


def cmd_web(args):
    from .web import serve
    backup = None
    if args.path:
        backup = Backup(args.path)
        pw = _password(args)
        if not backup.encrypted:
            backup.open()
        elif pw is not None:  # otherwise the browser asks for it
            try:
                backup.open(pw)
            except BackupPasswordError as e:
                sys.exit(f"error: {e}")
    serve(backup, host=args.host, port=args.port, open_browser=not args.no_browser,
          extra_roots=args.search or [])


def main(argv=None):
    p = argparse.ArgumentParser(prog="minimazing")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list").set_defaults(func=cmd_list)

    for name in ("info", "export", "web"):
        sp = sub.add_parser(name)
        sp.add_argument("path", nargs="?" if name == "web" else None)
        sp.add_argument("--password", "-p", default=None)
        sp.add_argument("--password-file", default=None,
                        help="read the backup password from this file")
        if name == "export":
            sp.add_argument("dataset", help=f"all, {', '.join(READERS)}")
            sp.add_argument("--out", "-o", default="./minimazing-out")
            sp.add_argument("--files", action="store_true",
                            help="also write photo/video, audio and attachment files")
        if name == "web":
            sp.add_argument("--host", default="127.0.0.1")
            sp.add_argument("--port", type=int, default=8777)
            sp.add_argument("--no-browser", action="store_true",
                            help="don't auto-open a browser tab")
            sp.add_argument("--search", action="append", metavar="DIR",
                            help="extra folder to look for backups in (repeatable)")
        sp.set_defaults(func={"info": cmd_info, "export": cmd_export, "web": cmd_web}[name])

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
