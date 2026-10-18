"""Dataset readers.

Each reader opens one Apple app database from inside a Backup (using the same
domain + relative paths iMazing's binary hard-codes) and returns plain Python
records. Rendering/export lives in exporters.py. Kept intentionally small:
one representative query per dataset rather than every column iMazing pulls.
"""
from __future__ import annotations

import datetime as dt

from .backup import Backup

APPLE_EPOCH = dt.datetime(2001, 1, 1, tzinfo=dt.timezone.utc)

# domain, relative path for each dataset (from iMazing's own strings)
SOURCES = {
    "messages":  ("HomeDomain", "Library/SMS/sms.db"),
    "calls":     ("HomeDomain", "Library/CallHistoryDB/CallHistory.storedata"),
    "contacts":  ("HomeDomain", "Library/AddressBook/AddressBook.sqlitedb"),
    "calendar":  ("HomeDomain", "Library/Calendar/Calendar.sqlitedb"),
    "notes":     ("AppDomainGroup-group.com.apple.notes", "NoteStore.sqlite"),
    "safari":    ("AppDomainGroup-group.com.apple.safari", "Library/Safari/History.db"),
    "whatsapp":  ("AppDomainGroup-group.net.whatsapp.WhatsApp.shared", "ChatStorage.sqlite"),
    "photos":    ("CameraRollDomain", "Media/PhotoData/Photos.sqlite"),
}


def _t(v):
    if not v:
        return None
    v = float(v)
    if v > 1e12:
        v /= 1e9
    return (APPLE_EPOCH + dt.timedelta(seconds=v)).isoformat()


def _has(con, table):
    return bool(con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def messages(b: Backup) -> list[dict]:
    con = b.sqlite(*SOURCES["messages"])
    if not con:
        return []
    rows = con.execute("""
        SELECT m.ROWID id, c.chat_identifier chat, h.id contact,
               m.is_from_me from_me, m.date, m.text
        FROM message m
        LEFT JOIN handle h ON h.ROWID = m.handle_id
        LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        LEFT JOIN chat c ON c.ROWID = cmj.chat_id
        ORDER BY m.date
    """).fetchall()
    return [{"id": r["id"], "chat": r["chat"] or r["contact"],
             "contact": r["contact"], "from_me": bool(r["from_me"]),
             "date": _t(r["date"]), "text": r["text"]} for r in rows]


def calls(b: Backup) -> list[dict]:
    con = b.sqlite(*SOURCES["calls"])
    if not con or not _has(con, "ZCALLRECORD"):
        return []
    rows = con.execute("""
        SELECT ZADDRESS addr, ZDATE date, ZDURATION dur, ZORIGINATED out, ZANSWERED ans
        FROM ZCALLRECORD ORDER BY ZDATE
    """).fetchall()
    out = []
    for r in rows:
        addr = r["addr"]
        if isinstance(addr, (bytes, bytearray)):
            addr = addr.decode("utf-8", "replace")
        out.append({"number": addr, "date": _t(r["date"]),
                    "duration_s": int(r["dur"] or 0),
                    "direction": "outgoing" if r["out"] else "incoming",
                    "answered": bool(r["ans"])})
    return out


def contacts(b: Backup) -> list[dict]:
    con = b.sqlite(*SOURCES["contacts"])
    if not con:
        return []
    people = con.execute("""
        SELECT ROWID id, First first, Last last, Organization org
        FROM ABPerson ORDER BY Last, First
    """).fetchall()
    values = {}
    for r in con.execute(
            "SELECT record_id rid, value FROM ABMultiValue WHERE value IS NOT NULL"):
        values.setdefault(r["rid"], []).append(str(r["value"]))
    out = []
    for p in people:
        name = " ".join(x for x in (p["first"], p["last"]) if x) or p["org"]
        out.append({"id": p["id"], "name": name or "(no name)",
                    "organization": p["org"], "values": values.get(p["id"], [])})
    return out


def calendar(b: Backup) -> list[dict]:
    con = b.sqlite(*SOURCES["calendar"])
    if not con or not _has(con, "CalendarItem"):
        return []
    rows = con.execute("""
        SELECT summary, start_date, end_date, all_day FROM CalendarItem ORDER BY start_date
    """).fetchall()
    return [{"title": r["summary"], "start": _t(r["start_date"]),
             "end": _t(r["end_date"]), "all_day": bool(r["all_day"])} for r in rows]


def notes(b: Backup) -> list[dict]:
    con = b.sqlite(*SOURCES["notes"])
    if not con or not _has(con, "ZICCLOUDSYNCINGOBJECT"):
        return []
    rows = con.execute("""
        SELECT ZTITLE1 title, ZSNIPPET snippet, ZMODIFICATIONDATE1 mod
        FROM ZICCLOUDSYNCINGOBJECT WHERE ZTITLE1 IS NOT NULL ORDER BY ZMODIFICATIONDATE1 DESC
    """).fetchall()
    return [{"title": r["title"], "snippet": r["snippet"], "modified": _t(r["mod"])}
            for r in rows]


def photos(b: Backup) -> list[dict]:
    """Return a manifest of camera-roll assets (metadata + backup file id)."""
    con = b.sqlite(*SOURCES["photos"])
    if not con or not _has(con, "ZASSET"):
        return []
    rows = con.execute("""
        SELECT ZDIRECTORY dir, ZFILENAME name, ZDATECREATED created,
               ZFAVORITE fav, ZKIND kind
        FROM ZASSET ORDER BY ZDATECREATED
    """).fetchall()
    out = []
    for r in rows:
        rel = f"Media/{r['dir']}/{r['name']}"
        f = b.find("CameraRollDomain", rel)
        out.append({"filename": r["name"], "path": rel,
                    "created": _t(r["created"]), "favorite": bool(r["fav"]),
                    "is_video": r["kind"] == 1, "size": f.size if f else 0,
                    "file_id": f.file_id if f else None})
    return out


def whatsapp(b: Backup) -> list[dict]:
    con = b.sqlite(*SOURCES["whatsapp"])
    if not con or not _has(con, "ZWAMESSAGE"):
        return []
    rows = con.execute("""
        SELECT s.ZPARTNERNAME chat, m.ZISFROMME from_me, m.ZTEXT text, m.ZMESSAGEDATE date
        FROM ZWAMESSAGE m LEFT JOIN ZWACHATSESSION s ON s.Z_PK = m.ZCHATSESSION
        ORDER BY m.ZMESSAGEDATE
    """).fetchall()
    return [{"chat": r["chat"], "from_me": bool(r["from_me"]),
             "text": r["text"], "date": _t(r["date"])} for r in rows]


READERS = {
    "messages": messages, "calls": calls, "contacts": contacts,
    "calendar": calendar, "notes": notes, "photos": photos, "whatsapp": whatsapp,
}
