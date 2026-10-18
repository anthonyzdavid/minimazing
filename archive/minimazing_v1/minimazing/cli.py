"""Command line: list backups, inspect one, export datasets, or launch the web UI.

Examples:
    python -m minimazing list
    python -m minimazing info  <backup_path> [--password PW]
    python -m minimazing export <backup_path> messages --password PW --out ./out
    python -m minimazing web   <backup_path> --password PW
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from . import Backup, discover
from .crypto import BackupPasswordError
from .extractors import READERS
from . import exporters as ex


def _open(args) -> Backup:
    b = Backup(args.path)
    pw = args.password
    if b.encrypted and pw is None:
        pw = getpass.getpass("Backup password: ")
    try:
        return b.open(pw)
    except BackupPasswordError as e:
        sys.exit(f"error: {e}")


def cmd_list(_):
    for p in discover():
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
    for key, (domain, rel) in __import__("minimazing.extractors", fromlist=["SOURCES"]).SOURCES.items():
        print(f"    {key:<10} {'yes' if b.find(domain, rel) else '-'}")
    b.close()


def cmd_export(args):
    b = _open(args)
    out = Path(args.out)
    ds = args.dataset
    if ds not in READERS:
        sys.exit(f"unknown dataset {ds}; choose from {', '.join(READERS)}")
    records = READERS[ds](b)
    print(f"{ds}: {len(records)} records")
    ex.to_json(records, out / f"{ds}.json")
    ex.to_csv(records, out / f"{ds}.csv")
    if ds in ("messages", "whatsapp"):
        ex.messages_html(records, out / f"{ds}.html")
    if ds == "photos" and args.files:
        n = ex.photos_files(b, records, out / "photos")
        print(f"  wrote {n} original photo/video files")
    print(f"written to {out.resolve()}")
    b.close()


def cmd_web(args):
    from .web import serve
    serve(_open(args), host=args.host, port=args.port)


def main(argv=None):
    p = argparse.ArgumentParser(prog="minimazing")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list").set_defaults(func=cmd_list)

    for name in ("info", "export", "web"):
        sp = sub.add_parser(name)
        sp.add_argument("path")
        sp.add_argument("--password", "-p", default=None)
        if name == "export":
            sp.add_argument("dataset")
            sp.add_argument("--out", "-o", default="./minimazing-out")
            sp.add_argument("--files", action="store_true", help="also write original photo files")
        if name == "web":
            sp.add_argument("--host", default="127.0.0.1")
            sp.add_argument("--port", type=int, default=8777)
        sp.set_defaults(func={"info": cmd_info, "export": cmd_export, "web": cmd_web}[name])

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
