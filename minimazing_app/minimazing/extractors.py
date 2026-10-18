"""Dataset readers.

Each reader opens one Apple app database from inside a Backup and returns
plain Python records. Domain/path naming has drifted across iOS versions
(container IDs especially), so each dataset lists candidate (domain, path)
pairs in newest-first order and uses whichever one is actually present in
*this* backup - real backups are what this gets tested against, not just
the synthetic one, so being permissive here matters more than being terse.
"""
from __future__ import annotations

import datetime as dt
import gzip
import plistlib
import re
import zlib

from .backup import Backup

APPLE_EPOCH = dt.datetime(2001, 1, 1, tzinfo=dt.timezone.utc)

WHATSAPP_DOMAIN = "AppDomainGroup-group.net.whatsapp.WhatsApp.shared"
VOICEMEMOS_DOMAIN = "AppDomainGroup-group.com.apple.VoiceMemos.shared"

# dataset key -> ordered list of (domain, relativePath) candidates
SOURCES = {
    "messages": [("HomeDomain", "Library/SMS/sms.db")],
    "calls": [
        ("HomeDomain", "Library/CallHistoryDB/CallHistory.storedata"),
        ("HomeDomain", "Library/CallHistory/call_history.db"),
    ],
    "contacts": [("HomeDomain", "Library/AddressBook/AddressBook.sqlitedb")],
    "calendar": [("HomeDomain", "Library/Calendar/Calendar.sqlitedb")],
    "notes": [
        ("AppDomainGroup-group.com.apple.notes", "NoteStore.sqlite"),
        ("HomeDomain", "Library/Notes/notes.sqlite"),
    ],
    "safari": [
        ("AppDomainGroup-group.com.apple.mobilesafari", "Library/Safari/History.db"),
        ("AppDomainGroup-group.com.apple.safari", "Library/Safari/History.db"),
        ("HomeDomain", "Library/Safari/History.db"),
    ],
    "whatsapp": [(WHATSAPP_DOMAIN, "ChatStorage.sqlite")],
    "photos": [("CameraRollDomain", "Media/PhotoData/Photos.sqlite")],
    "voicememos": [
        (VOICEMEMOS_DOMAIN, "Recordings/CloudRecordings.db"),
        ("MediaDomain", "Media/Recordings/Recordings.db"),
    ],
    "voicemail": [("HomeDomain", "Library/Voicemail/voicemail.db")],
    "ringtones": [("TonesDomain", "Media/Purchases/Ringtones.plist")],
}

# human label + nav icon key + whether it's implemented at all, for the UI's
# full sidebar (so unimplemented datasets show up greyed-out, not missing).
# "view" tells the frontend which layout to use.
CATALOG = [
    {"key": "messages", "label": "Messages", "icon": "messages", "implemented": True, "view": "threads"},
    {"key": "whatsapp", "label": "WhatsApp", "icon": "whatsapp", "implemented": True, "view": "threads"},
    {"key": "contacts", "label": "Contacts", "icon": "contacts", "implemented": True, "view": "table"},
    {"key": "calls", "label": "Call History", "icon": "calls", "implemented": True, "view": "table"},
    {"key": "voicemail", "label": "Voicemail", "icon": "voicemail", "implemented": True, "view": "audio"},
    {"key": "calendar", "label": "Calendar", "icon": "calendar", "implemented": True, "view": "table"},
    {"key": "notes", "label": "Notes", "icon": "notes", "implemented": True, "view": "notes"},
    {"key": "safari", "label": "Safari", "icon": "safari", "implemented": True, "view": "table"},
    {"key": "photos", "label": "Photos", "icon": "photos", "implemented": True, "view": "photos"},
    {"key": "voicememos", "label": "Voice Memos", "icon": "voicememos", "implemented": True, "view": "audio"},
    {"key": "ringtones", "label": "Ringtones", "icon": "music", "implemented": True, "view": "audio"},
    {"key": "apps", "label": "Apps", "icon": "apps", "implemented": True, "view": "apps"},
    {"key": "files", "label": "File System", "icon": "files", "implemented": True, "view": "files"},
    {"key": "music", "label": "Music", "icon": "music", "implemented": False,
     "reason": "iOS backups don't contain the music library; iMazing reads it from a connected device."},
]


def _t(v):
    if not v:
        return None
    v = float(v)
    if v > 1e12:  # iOS 11+ switched message/etc. timestamps to nanoseconds
        v /= 1e9
    try:
        return (APPLE_EPOCH + dt.timedelta(seconds=v)).isoformat()
    except OverflowError:
        return None


def _unix(v):
    if not v:
        return None
    return dt.datetime.fromtimestamp(float(v), dt.timezone.utc).isoformat()


def _has(con, table):
    return bool(con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _columns(con, table) -> set[str]:
    return {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}


def _open(b: Backup, key: str):
    """Try each candidate path for a dataset; return the first SQLite
    connection that actually opens, or None if none of them exist here."""
    for domain, rel in SOURCES[key]:
        if b.find(domain, rel):
            con = b.sqlite(domain, rel)
            if con:
                return con
    return None


def _source(b: Backup, key: str):
    for domain, rel in SOURCES[key]:
        if b.find(domain, rel):
            return domain, rel
    return None


def _file_ref(f, mime: str | None = None) -> dict:
    return {"file_id": f.file_id, "name": f.name, "size": f.size, "mime": mime}


# ---------------------------------------------------------------- decoders

def attributed_text(blob: bytes | None) -> str | None:
    """Plain text out of message.attributedBody (an NSAttributedString archived
    with NSArchiver's 'streamtyped' format). iOS 16+ often leaves message.text
    NULL and only stores this. The string payload follows the NSString class
    reference as '+' <length> <utf-8 bytes>; lengths >= 0x80 are prefixed by
    0x81 (2-byte LE) or 0x82 (4-byte LE)."""
    if not blob:
        return None
    i = blob.find(b"NSString")
    if i < 0:
        return None
    j = blob.find(b"+", i + 8)
    if j < 0 or j + 1 >= len(blob):
        return None
    j += 1
    n = blob[j]
    j += 1
    if n == 0x81:
        n = int.from_bytes(blob[j:j + 2], "little")
        j += 2
    elif n == 0x82:
        n = int.from_bytes(blob[j:j + 4], "little")
        j += 4
    text = blob[j:j + n].decode("utf-8", "replace")
    return text or None


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = result = 0
    while i < len(buf):
        byte = buf[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            break
        shift += 7
    return result, i


def _pb_field(buf: bytes, number: int) -> bytes | None:
    """First length-delimited value of a protobuf field (enough for Notes)."""
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        wire, num = key & 7, key >> 3
        if wire == 0:
            _, i = _varint(buf, i)
        elif wire == 1:
            i += 8
        elif wire == 5:
            i += 4
        elif wire == 2:
            n, i = _varint(buf, i)
            if num == number:
                return buf[i:i + n]
            i += n
        else:
            return None
    return None


def note_body(data: bytes | None) -> str | None:
    """ZICNOTEDATA.ZDATA is gzip'd protobuf: NoteStoreProto.document(2)
    .note(3).note_text(2). Attachments show up as U+FFFC placeholders."""
    if not data:
        return None
    try:
        raw = gzip.decompress(data) if data[:2] == b"\x1f\x8b" else zlib.decompress(data)
    except (OSError, zlib.error, EOFError):
        return None
    doc = _pb_field(raw, 2)
    note = _pb_field(doc, 3) if doc else None
    text = _pb_field(note, 2) if note else None
    if text is None:
        return None
    return text.decode("utf-8", "replace").replace("￼", "").strip() or None


# ---------------------------------------------------------------- contacts

_LABEL_RE = re.compile(r"^_\$!<(.*)>!\$_$")


def _clean_label(label):
    if not label:
        return None
    m = _LABEL_RE.match(label)
    return m.group(1) if m else label


def contacts(b: Backup) -> list[dict]:
    con = _open(b, "contacts")
    if not con or not _has(con, "ABPerson"):
        return []
    people = con.execute("""
        SELECT ROWID id, First first, Last last, Organization org
        FROM ABPerson ORDER BY Last, First
    """).fetchall()
    labels = {}
    if _has(con, "ABMultiValueLabel"):
        labels = {r["ROWID"]: r["value"] for r in con.execute("SELECT ROWID, value FROM ABMultiValueLabel")}
    has_label = "label" in _columns(con, "ABMultiValue")
    values, phones, emails = {}, {}, {}
    for r in con.execute(
            f"SELECT record_id rid, property, {'label' if has_label else 'NULL'} label, value "
            "FROM ABMultiValue WHERE value IS NOT NULL"):
        v = str(r["value"])
        values.setdefault(r["rid"], []).append(v)
        label = _clean_label(labels.get(r["label"]))
        entry = f"{v} ({label})" if label else v
        if r["property"] == 3:
            phones.setdefault(r["rid"], []).append(entry)
        elif r["property"] == 4:
            emails.setdefault(r["rid"], []).append(entry)
    out = []
    for p in people:
        name = " ".join(x for x in (p["first"], p["last"]) if x) or p["org"]
        out.append({"id": p["id"], "name": name or "(no name)",
                    "organization": p["org"],
                    "phones": phones.get(p["id"], []), "emails": emails.get(p["id"], []),
                    "values": values.get(p["id"], [])})
    return out


def _norm_handle(h: str | None) -> str | None:
    """Phone numbers match on their last 10 digits; emails case-insensitively."""
    if not h:
        return None
    h = h.strip()
    if "@" in h:
        return h.lower()
    digits = re.sub(r"\D", "", h)
    return digits[-10:] if len(digits) >= 7 else h.lower()


def contact_names(b: Backup) -> dict[str, str]:
    """normalized phone/email -> contact display name."""
    def build():
        names = {}
        for c in b.memo("contacts", lambda: contacts(b)):
            if c["name"] == "(no name)":
                continue
            for v in c["values"]:
                k = _norm_handle(v)
                if k:
                    names.setdefault(k, c["name"])
        return names
    return b.memo("_contact_names", build)


def _name_for(b: Backup, handle: str | None) -> str | None:
    return contact_names(b).get(_norm_handle(handle)) if handle else None


# ---------------------------------------------------------------- messages

_SMS_TAPBACK = range(2000, 4000)  # associated_message_type for reactions


def messages(b: Backup) -> list[dict]:
    con = _open(b, "messages")
    if not con or not _has(con, "message"):
        return []
    mcols = _columns(con, "message")
    ccols = _columns(con, "chat") if _has(con, "chat") else set()

    def col(name, table_cols=mcols, alias="m"):
        return f"{alias}.{name}" if name in table_cols else "NULL"

    has_attach_col = "cache_has_attachments" in mcols
    rows = con.execute(f"""
        SELECT m.ROWID id, cmj.chat_id chat_id, c.chat_identifier chat_ident,
               {col('display_name', ccols, 'c')} chat_display, h.id contact,
               m.is_from_me from_me, m.date, m.text, {col('attributedBody')} body,
               {col('service')} service, {col('associated_message_type')} assoc,
               {"m.cache_has_attachments" if has_attach_col else "0"} has_attach
        FROM message m
        LEFT JOIN handle h ON h.ROWID = m.handle_id
        LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        LEFT JOIN chat c ON c.ROWID = cmj.chat_id
        ORDER BY m.date
    """).fetchall()

    # group chats without a display name are named after their participants
    members: dict[int, list[str]] = {}
    if _has(con, "chat_handle_join"):
        for r in con.execute("""SELECT chj.chat_id cid, h.id hid FROM chat_handle_join chj
                                JOIN handle h ON h.ROWID = chj.handle_id"""):
            members.setdefault(r["cid"], []).append(_name_for(b, r["hid"]) or r["hid"])

    attachments: dict[int, list[dict]] = {}
    if _has(con, "attachment") and _has(con, "message_attachment_join"):
        media = b.index("MediaDomain")
        acols = _columns(con, "attachment")
        for r in con.execute(f"""
                SELECT maj.message_id mid, a.filename fn, a.mime_type mime,
                       {col('transfer_name', acols, 'a')} tname
                FROM message_attachment_join maj JOIN attachment a ON a.ROWID = maj.attachment_id"""):
            fn = r["fn"] or ""
            if fn.endswith(".pluginPayloadAttachment"):  # link-preview payloads
                continue
            rel = re.sub(r"^(~|/var/mobile)/", "", fn)
            f = media.get(rel)
            ref = _file_ref(f, r["mime"]) if f else {
                "file_id": None, "name": r["tname"] or fn.rsplit("/", 1)[-1], "size": 0, "mime": r["mime"]}
            ref["name"] = r["tname"] or ref["name"]
            attachments.setdefault(r["mid"], []).append(ref)

    chat_names: dict = {}
    out = []
    for r in rows:
        if r["assoc"] in _SMS_TAPBACK:
            continue
        cid = r["chat_id"]
        if cid not in chat_names:
            ident = r["chat_ident"]
            name = r["chat_display"]
            if not name and cid in members and len(members[cid]) > 1:
                name = ", ".join(members[cid])
            if not name:
                name = _name_for(b, ident or r["contact"]) or ident or r["contact"] or "Unknown"
            chat_names[cid] = name
        text = r["text"] or attributed_text(r["body"])
        sender = None if r["from_me"] else (_name_for(b, r["contact"]) or r["contact"])
        att = attachments.get(r["id"], [])
        out.append({"id": r["id"], "chat_id": cid, "chat": chat_names[cid],
                    "contact": r["contact"], "sender": sender,
                    "from_me": bool(r["from_me"]), "date": _t(r["date"]), "text": text,
                    "service": r["service"], "has_attachment": bool(r["has_attach"]) or bool(att),
                    "attachments": att})
    return out


def whatsapp(b: Backup) -> list[dict]:
    con = _open(b, "whatsapp")
    if not con or not _has(con, "ZWAMESSAGE"):
        return []
    mcols = _columns(con, "ZWAMESSAGE")
    has_media = _has(con, "ZWAMEDIAITEM") and "ZMEDIAITEM" in mcols
    has_members = _has(con, "ZWAGROUPMEMBER") and "ZGROUPMEMBER" in mcols
    rows = con.execute(f"""
        SELECT m.Z_PK id, m.ZCHATSESSION chat_id, s.ZPARTNERNAME chat, m.ZISFROMME from_me,
               m.ZTEXT text, m.ZMESSAGEDATE date,
               {"gm.ZCONTACTNAME" if has_members else "NULL"} member,
               {"mi.ZMEDIALOCALPATH" if has_media else "NULL"} media,
               {"mi.ZTITLE" if has_media else "NULL"} media_title
        FROM ZWAMESSAGE m
        LEFT JOIN ZWACHATSESSION s ON s.Z_PK = m.ZCHATSESSION
        {"LEFT JOIN ZWAGROUPMEMBER gm ON gm.Z_PK = m.ZGROUPMEMBER" if has_members else ""}
        {"LEFT JOIN ZWAMEDIAITEM mi ON mi.Z_PK = m.ZMEDIAITEM" if has_media else ""}
        ORDER BY m.ZMESSAGEDATE
    """).fetchall()
    idx = b.index(WHATSAPP_DOMAIN) if has_media else {}
    out = []
    for r in rows:
        att = []
        if r["media"]:
            local = r["media"].lstrip("/")
            f = idx.get(f"Message/{local}") or idx.get(local)
            att.append(_file_ref(f) if f else {"file_id": None, "name": local.rsplit("/", 1)[-1],
                                                 "size": 0, "mime": None})
        sender = None if r["from_me"] else (r["member"] or r["chat"])
        out.append({"id": r["id"], "chat_id": r["chat_id"], "chat": r["chat"] or "Unknown",
                    "sender": sender, "from_me": bool(r["from_me"]),
                    "text": r["text"] or r["media_title"], "date": _t(r["date"]),
                    "has_attachment": bool(att), "attachments": att})
    return out


# ---------------------------------------------------------------- calls / calendar / notes / safari

_CALL_SERVICES = {"com.apple.Telephony": "Phone", "com.apple.FaceTime": "FaceTime"}


def calls(b: Backup) -> list[dict]:
    con = _open(b, "calls")
    if not con:
        return []
    if _has(con, "ZCALLRECORD"):
        cols = _columns(con, "ZCALLRECORD")
        name = "ZNAME" if "ZNAME" in cols else "NULL"
        svc = "ZSERVICE_PROVIDER" if "ZSERVICE_PROVIDER" in cols else "NULL"
        rows = con.execute(f"""
            SELECT ZADDRESS addr, ZDATE date, ZDURATION dur, ZORIGINATED out, ZANSWERED ans,
                   {name} name, {svc} svc
            FROM ZCALLRECORD ORDER BY ZDATE DESC
        """).fetchall()
    elif _has(con, "call"):
        rows = con.execute(
            "SELECT address addr, date, duration dur, 0 out, 1 ans, NULL name, NULL svc "
            "FROM call ORDER BY date DESC"
        ).fetchall()
    else:
        return []
    out = []
    for r in rows:
        addr = r["addr"]
        if isinstance(addr, (bytes, bytearray)):
            addr = addr.decode("utf-8", "replace")
        svc = r["svc"] or ""
        service = _CALL_SERVICES.get(svc) or ("WhatsApp" if "whatsapp" in svc.lower() else svc or "Phone")
        out.append({"name": _name_for(b, addr) or r["name"], "number": addr,
                    "date": _t(r["date"]), "duration_s": int(r["dur"] or 0),
                    "direction": "outgoing" if r["out"] else "incoming",
                    "answered": bool(r["ans"]), "service": service})
    return out


def calendar(b: Backup) -> list[dict]:
    con = _open(b, "calendar")
    if not con or not _has(con, "CalendarItem"):
        return []
    cols = _columns(con, "CalendarItem")
    cal_join = "LEFT JOIN Calendar c ON c.ROWID = ci.calendar_id" if (
        _has(con, "Calendar") and "calendar_id" in cols) else ""
    loc_join = "LEFT JOIN Location l ON l.ROWID = ci.location_id" if (
        _has(con, "Location") and "location_id" in cols) else ""
    rows = con.execute(f"""
        SELECT ci.summary, ci.start_date, ci.end_date, ci.all_day,
               {"c.title" if cal_join else "NULL"} cal,
               {"l.title" if loc_join else "NULL"} loc,
               {"ci.description" if "description" in cols else "NULL"} notes
        FROM CalendarItem ci {cal_join} {loc_join} ORDER BY ci.start_date DESC
    """).fetchall()
    return [{"title": r["summary"], "start": _t(r["start_date"]), "end": _t(r["end_date"]),
             "all_day": bool(r["all_day"]), "calendar": r["cal"], "location": r["loc"],
             "notes": r["notes"]} for r in rows]


def notes(b: Backup) -> list[dict]:
    con = _open(b, "notes")
    if not con or not _has(con, "ZICCLOUDSYNCINGOBJECT"):
        return []
    # Notes' column names have shifted across iOS releases (ZTITLE1 vs ZTITLE,
    # ZSNIPPET vs ZSUMMARY, ZMODIFICATIONDATE1 vs ZMODIFICATIONDATE) - pick
    # whichever variant is actually present rather than assuming one.
    cols = _columns(con, "ZICCLOUDSYNCINGOBJECT")

    def pick(*names):
        return next((n for n in names if n in cols), None)

    title_col = pick("ZTITLE1", "ZTITLE")
    if not title_col:
        return []
    snippet_col = pick("ZSNIPPET", "ZSUMMARY")
    mod_col = pick("ZMODIFICATIONDATE1", "ZMODIFICATIONDATE")
    created_col = pick("ZCREATIONDATE3", "ZCREATIONDATE1", "ZCREATIONDATE")
    has_body = _has(con, "ZICNOTEDATA") and "ZNOTE" in _columns(con, "ZICNOTEDATA")
    has_folder = "ZFOLDER" in cols and "ZTITLE2" in cols
    sel = ", ".join([
        f"n.{title_col} title", f"n.{snippet_col} snippet" if snippet_col else "NULL snippet",
        f"n.{mod_col} mod" if mod_col else "NULL mod",
        f"n.{created_col} created" if created_col else "NULL created",
        "n.ZISPASSWORDPROTECTED locked" if "ZISPASSWORDPROTECTED" in cols else "0 locked",
        "d.ZDATA body" if has_body else "NULL body",
        "f.ZTITLE2 folder" if has_folder else "NULL folder",
    ])
    where = f"n.{title_col} IS NOT NULL"
    if "ZMARKEDFORDELETION" in cols:
        where += " AND COALESCE(n.ZMARKEDFORDELETION, 0) = 0"
    rows = con.execute(f"""
        SELECT {sel} FROM ZICCLOUDSYNCINGOBJECT n
        {"LEFT JOIN ZICNOTEDATA d ON d.ZNOTE = n.Z_PK" if has_body else ""}
        {"LEFT JOIN ZICCLOUDSYNCINGOBJECT f ON f.Z_PK = n.ZFOLDER" if has_folder else ""}
        WHERE {where} {f"ORDER BY n.{mod_col} DESC" if mod_col else ""}
    """).fetchall()
    out = []
    for r in rows:
        locked = bool(r["locked"])
        body = None if locked else note_body(r["body"])
        out.append({"title": r["title"], "snippet": r["snippet"], "folder": r["folder"],
                    "created": _t(r["created"]), "modified": _t(r["mod"]),
                    "locked": locked, "body": body})
    return out


def safari(b: Backup) -> list[dict]:
    con = _open(b, "safari")
    if not con or not _has(con, "history_items"):
        return []
    rows = con.execute("""
        SELECT hi.url url, hv.title title, hv.visit_time vt
        FROM history_items hi JOIN history_visits hv ON hv.history_item = hi.id
        ORDER BY hv.visit_time DESC
    """).fetchall()
    return [{"title": r["title"] or r["url"], "url": r["url"], "visited": _t(r["vt"])}
            for r in rows]


# ---------------------------------------------------------------- photos

_VIDEO_EXT = (".mov", ".mp4", ".m4v")


def photos(b: Backup) -> list[dict]:
    """Camera-roll assets, each with the best image this backup actually holds.

    When iCloud Photos has offloaded (or deleted) an original, the backup
    still keeps what the device had on disk for it. Ranked best-first:
      original                 Media/<dir>/<name>
      edited render            Media/PhotoData/Mutations/<dir>/<stem>/Adjustments/*.jpg
      thumbnail                Media/PhotoData/Thumbnails/V2/<dir>/<name>/5005.JPG
      preview / thumbnail      Media/PhotoData/UBF/.../derivatives/... ("Shared with You")
      poster frame             Media/PhotoData/Metadata/<dir>/<stem>.JPG
      tiny thumbnail           Media/PhotoData/Metadata/<dir>/<stem>.THM
    Every non-original candidate is a JPEG, so among those the biggest file is
    the most detailed one.
    """
    con = _open(b, "photos")
    if not con or not _has(con, "ZASSET"):
        return []
    cols = _columns(con, "ZASSET")

    def c(name, default="NULL"):
        return name if name in cols else default

    date_col = "ZDATECREATED"
    where = "WHERE COALESCE(ZTRASHEDSTATE, 0) = 0" if "ZTRASHEDSTATE" in cols else ""
    rows = con.execute(f"""
        SELECT ZDIRECTORY dir, ZFILENAME name, {date_col} created, {c('ZFAVORITE', '0')} fav,
               ZKIND kind, {c('ZHIDDEN', '0')} hidden, {c('ZWIDTH')} w, {c('ZHEIGHT')} h,
               {c('ZDURATION')} dur
        FROM ZASSET {where} ORDER BY {date_col} DESC
    """).fetchall()
    idx = b.index("CameraRollDomain")

    # one pass to group edited renders by their asset folder
    renders: dict[str, list] = {}
    for rel, f in idx.items():
        if rel.startswith("Media/PhotoData/Mutations/") and "/Adjustments/" in rel \
                and rel.lower().endswith((".jpg", ".jpeg", ".heic")):
            renders.setdefault(rel.split("/Adjustments/", 1)[0], []).append(f)

    out = []
    for r in rows:
        if not r["dir"] or not r["name"]:
            continue
        d, name = r["dir"], r["name"]
        stem = name.rsplit(".", 1)[0]
        rel = f"Media/{d}/{name}"
        is_video = r["kind"] == 1 or name.lower().endswith(_VIDEO_EXT)
        original = idx.get(rel)
        derived = [(f, "edited render") for f in renders.get(f"Media/PhotoData/Mutations/{d}/{stem}", [])]
        ubf = "Media/PhotoData/UBF/scopes/syndication/resources/derivatives"  # "Shared with You"
        for path, label in ((f"Media/PhotoData/Thumbnails/V2/{d}/{name}/5005.JPG", "thumbnail"),
                            (f"{ubf}/{d}/{stem}_1_102_o.jpeg", "preview"),
                            (f"{ubf}/masters/{d}/{stem}_4_5005_c.jpeg", "thumbnail"),
                            (f"Media/PhotoData/Metadata/{d}/{stem}.JPG", "poster frame"),
                            (f"Media/PhotoData/Metadata/{d}/{stem}.THM", "tiny thumbnail")):
            if path in idx:
                derived.append((idx[path], label))
        derived.sort(key=lambda x: x[0].size, reverse=True)
        thumb = next((f for f, label in derived if label == "thumbnail"), None) \
            or (derived[0][0] if derived else None)

        if original and not is_video:
            best, quality = original, "original"
        elif derived:
            best, quality = derived[0]
        else:
            best, quality = None, None
        out.append({
            "filename": name, "path": rel, "created": _t(r["created"]),
            "favorite": bool(r["fav"]), "hidden": bool(r["hidden"]), "is_video": is_video,
            "width": r["w"], "height": r["h"],
            "duration_s": round(r["dur"], 1) if r["dur"] else None,
            "size": original.size if original else 0,
            "file_id": original.file_id if original else None,      # the original, if present
            "best_id": best.file_id if best else None,              # best still image available
            "best_size": best.size if best else 0,
            "quality": "original" if original else quality,
            "thumb_id": thumb.file_id if thumb else (
                original.file_id if original and not is_video else None),
        })
    return out


# ---------------------------------------------------------------- audio: memos / voicemail / ringtones

def voicememos(b: Backup) -> list[dict]:
    src = _source(b, "voicememos")
    if not src:
        return []
    domain, _ = src
    con = b.sqlite(*src)
    table = "ZCLOUDRECORDING" if _has(con, "ZCLOUDRECORDING") else "ZRECORDING" if _has(con, "ZRECORDING") else None
    if not table:
        return []
    cols = _columns(con, table)
    title = "COALESCE(ZCUSTOMLABEL, ZENCRYPTEDTITLE)" if "ZENCRYPTEDTITLE" in cols else "ZCUSTOMLABEL"
    rows = con.execute(f"SELECT {title} title, ZDATE date, ZDURATION dur, ZPATH path "
                       f"FROM {table} ORDER BY ZDATE DESC").fetchall()
    idx = b.index(domain)
    out = []
    for r in rows:
        p = r["path"] or ""
        fname = p.rsplit("/", 1)[-1]
        f = idx.get(f"Recordings/{fname}") or idx.get(f"Media/Recordings/{fname}")
        out.append({"title": r["title"] or fname or "Recording", "date": _t(r["date"]),
                    "duration_s": round(r["dur"] or 0, 1), "filename": fname,
                    "size": f.size if f else 0, "file_id": f.file_id if f else None})
    return out


def voicemail(b: Backup) -> list[dict]:
    con = _open(b, "voicemail")
    if not con or not _has(con, "voicemail"):
        return []
    cols = _columns(con, "voicemail")
    trashed = "AND COALESCE(trashed_date, 0) = 0" if "trashed_date" in cols else ""
    rows = con.execute(f"SELECT ROWID id, date, sender, duration FROM voicemail "
                       f"WHERE 1 {trashed} ORDER BY date DESC").fetchall()
    idx = b.index("HomeDomain")
    out = []
    for r in rows:
        f = idx.get(f"Library/Voicemail/{r['id']}.amr")
        sender = r["sender"]
        out.append({"title": _name_for(b, sender) or sender or "Unknown", "number": sender,
                    "date": _unix(r["date"]), "duration_s": r["duration"] or 0,
                    "filename": f.name if f else None,
                    "size": f.size if f else 0, "file_id": f.file_id if f else None})
    return out


def ringtones(b: Backup) -> list[dict]:
    idx = b.index("TonesDomain")
    names = {}
    meta = idx.get("Media/Purchases/Ringtones.plist")
    if meta:
        try:
            for fname, info in plistlib.loads(b.read(meta)).get("Ringtones", {}).items():
                names[fname] = info.get("Name")
        except Exception:
            pass
    out = []
    for rel, f in sorted(idx.items()):
        if rel.lower().endswith((".m4r", ".m4a", ".mp3", ".caf")):
            out.append({"title": names.get(f.name) or f.name, "filename": f.name,
                        "size": f.size, "file_id": f.file_id, "date": _unix(f.mtime)})
    return out


# ---------------------------------------------------------------- apps

def apps(b: Backup) -> list[dict]:
    """Installed apps from Info.plist (name, developer, version, icon) joined
    with the per-app data size recorded in the backup."""
    info_apps = b.info.get("Applications") or {}
    installed = b.info.get("Installed Applications") or list(info_apps)
    manifest_apps = b.manifest.get("Applications") or {}
    sizes = b.domain_sizes()
    out = []
    for bid in installed:
        entry = info_apps.get(bid, {})
        md = {}
        if entry.get("iTunesMetadata"):
            try:
                md = plistlib.loads(entry["iTunesMetadata"])
            except Exception:
                md = {}
        out.append({
            "bundle_id": bid, "name": md.get("itemName") or md.get("bundleDisplayName") or bid,
            "developer": md.get("artistName"), "genre": md.get("genre"),
            "version": md.get("bundleShortVersionString") or manifest_apps.get(bid, {}).get("CFBundleVersion"),
            "data_size": sizes.get(f"AppDomain-{bid}", 0),
            "has_icon": bool(entry.get("PlaceholderIcon")),
            "offloaded": bool(entry.get("IsDemotedApp")),
        })
    out.sort(key=lambda a: a["name"].lower())
    return out


def app_icon(b: Backup, bundle_id: str) -> bytes | None:
    return ((b.info.get("Applications") or {}).get(bundle_id) or {}).get("PlaceholderIcon")


READERS = {
    "messages": messages, "calls": calls, "contacts": contacts,
    "calendar": calendar, "notes": notes, "safari": safari,
    "photos": photos, "whatsapp": whatsapp, "voicememos": voicememos,
    "voicemail": voicemail, "ringtones": ringtones, "apps": apps,
}


def records(b: Backup, key: str) -> list[dict]:
    """Memoized reader call - every caller (web, CLI, other readers) shares one result."""
    return b.memo(key, lambda: READERS[key](b))


def available(b: Backup, key: str) -> bool:
    """Cheap presence check (no query execution) used to grey out nav items."""
    if key == "apps":
        return bool(b.info.get("Installed Applications") or b.info.get("Applications"))
    if key == "files":
        return True
    if key == "ringtones":
        return any(rel.lower().endswith((".m4r", ".m4a", ".mp3", ".caf")) for rel in b.index("TonesDomain"))
    return any(b.find(domain, rel) for domain, rel in SOURCES.get(key, []))
