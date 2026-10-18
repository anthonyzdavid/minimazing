"""Generate a synthetic *encrypted* iOS backup to exercise the reader end-to-end.
Builds a real keybag (double PBKDF2 + AES key-wrap), wraps per-file keys, encrypts
Manifest.db and each file with AES-256-CBC, and writes the ab/ blob layout.
"""
import gzip
import hashlib
import os
import plistlib
import sqlite3
import struct
import sys
from pathlib import Path

from cryptography.hazmat.primitives.keywrap import aes_key_wrap

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from minimazing.crypto import aes_cbc_encrypt  # noqa: E402

PASSWORD = b"test-pass-123"
CLASS = 3  # NSFileProtectionCompleteUntilFirstUserAuthentication-ish


def _tlv(tag, val):
    return tag + struct.pack(">I", len(val)) + val


def build_keybag(class_key):
    salt, dpsl = os.urandom(20), os.urandom(20)
    iters, dpic = 10000, 10000
    derived = hashlib.pbkdf2_hmac("sha256", PASSWORD, dpsl, dpic, 32)
    passcode_key = hashlib.pbkdf2_hmac("sha1", derived, salt, iters, 32)
    wrapped = aes_key_wrap(passcode_key, class_key)
    blob = b"".join([
        _tlv(b"VERS", struct.pack(">I", 4)),
        _tlv(b"TYPE", struct.pack(">I", 0)),
        _tlv(b"UUID", os.urandom(16)),
        _tlv(b"HMCK", os.urandom(40)),
        _tlv(b"SALT", salt),
        _tlv(b"ITER", struct.pack(">I", iters)),
        _tlv(b"DPSL", dpsl),
        _tlv(b"DPIC", struct.pack(">I", dpic)),
        # one protection-class block
        _tlv(b"UUID", os.urandom(16)),
        _tlv(b"CLAS", struct.pack(">I", CLASS)),
        _tlv(b"WRAP", struct.pack(">I", 2)),
        _tlv(b"KTYP", struct.pack(">I", 0)),
        _tlv(b"WPKY", wrapped),
    ])
    return blob


def mbfile(size, wrapped_key):
    """Minimal NSKeyedArchiver MBFile with Size + ProtectionClass + EncryptionKey."""
    objs = ["$null",
            {"Size": size, "ProtectionClass": CLASS,
             "EncryptionKey": plistlib.UID(2),
             "$class": plistlib.UID(3)},
            {"NS.data": struct.pack("<I", CLASS) + wrapped_key,
             "$class": plistlib.UID(4)},
            {"$classname": "MBFile", "$classes": ["MBFile", "NSObject"]},
            {"$classname": "NSMutableData", "$classes": ["NSMutableData", "NSData", "NSObject"]}]
    return plistlib.dumps({"$version": 100000, "$archiver": "NSKeyedArchiver",
                           "$top": {"root": plistlib.UID(1)}, "$objects": objs},
                          fmt=plistlib.FMT_BINARY)


def sms_db(path):
    c = sqlite3.connect(path); c.executescript("""
        CREATE TABLE handle(ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE chat(ROWID INTEGER PRIMARY KEY, chat_identifier TEXT);
        CREATE TABLE message(ROWID INTEGER PRIMARY KEY, text TEXT, handle_id INT,
                             is_from_me INT, date INT, cache_has_attachments INT,
                             attributedBody BLOB);
        CREATE TABLE chat_message_join(chat_id INT, message_id INT);
        CREATE TABLE attachment(ROWID INTEGER PRIMARY KEY, filename TEXT, mime_type TEXT,
                                transfer_name TEXT);
        CREATE TABLE message_attachment_join(message_id INT, attachment_id INT);
        INSERT INTO handle VALUES(1,'+15551234567');
        INSERT INTO chat VALUES(1,'+15551234567');
        INSERT INTO message VALUES(1,'Hey, this is a test',1,0,700000000000000000,0,NULL);
        INSERT INTO message VALUES(2,'Reply from me',1,1,700000001000000000,0,NULL);
        INSERT INTO message VALUES(3,NULL,1,0,700000002000000000,1,NULL);
        INSERT INTO chat_message_join VALUES(1,1),(1,2),(1,3),(1,4);
        INSERT INTO attachment VALUES(1,'~/Library/SMS/Attachments/ab/01/X/beach.jpg',
                                      'image/jpeg','beach.jpg');
        INSERT INTO message_attachment_join VALUES(3,1);
    """)
    # iOS 16+: text lives only in an NSArchiver 'streamtyped' attributedBody
    body = ("Only in attributedBody " + "x" * 140).encode()
    blob = (b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString"
            b"\x00\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+"
            + b"\x81" + len(body).to_bytes(2, "little") + body + b"\x86\x84\x02iI\x01")
    c.execute("INSERT INTO message VALUES(4,NULL,1,0,700000003000000000,0,?)", (blob,))
    c.commit(); c.close()


def calls_db(path):
    c = sqlite3.connect(path); c.executescript("""
        CREATE TABLE ZCALLRECORD(Z_PK INTEGER PRIMARY KEY, ZADDRESS TEXT, ZDATE REAL,
                                  ZDURATION REAL, ZORIGINATED INT, ZANSWERED INT);
        INSERT INTO ZCALLRECORD VALUES(1,'+15559876543',699000000,125,1,1);
        INSERT INTO ZCALLRECORD VALUES(2,'+15551234567',699100000,0,0,0);
    """); c.commit(); c.close()


def contacts_db(path):
    c = sqlite3.connect(path); c.executescript("""
        CREATE TABLE ABPerson(ROWID INTEGER PRIMARY KEY, First TEXT, Last TEXT, Organization TEXT);
        CREATE TABLE ABMultiValue(UID INTEGER PRIMARY KEY, record_id INT, property INT, value TEXT);
        INSERT INTO ABPerson VALUES(1,'Ada','Lovelace',NULL);
        INSERT INTO ABMultiValue VALUES(1,1,3,'+15559876543');
        INSERT INTO ABMultiValue VALUES(2,1,4,'ada@example.com');
    """); c.commit(); c.close()


def calendar_db(path):
    c = sqlite3.connect(path); c.executescript("""
        CREATE TABLE CalendarItem(ROWID INTEGER PRIMARY KEY, summary TEXT,
                                   start_date REAL, end_date REAL, all_day INT);
        INSERT INTO CalendarItem VALUES(1,'Team sync',699500000,699503600,0);
    """); c.commit(); c.close()


def _pb(num, payload):
    """One length-delimited protobuf field (lengths here stay < 128)."""
    return bytes([(num << 3) | 2, len(payload)]) + payload


def notes_db(path):
    c = sqlite3.connect(path); c.executescript("""
        CREATE TABLE ZICCLOUDSYNCINGOBJECT(Z_PK INTEGER PRIMARY KEY, ZTITLE1 TEXT,
                                            ZSNIPPET TEXT, ZMODIFICATIONDATE1 REAL,
                                            ZMARKEDFORDELETION INT, ZTITLE2 TEXT, ZFOLDER INT);
        CREATE TABLE ZICNOTEDATA(Z_PK INTEGER PRIMARY KEY, ZNOTE INT, ZDATA BLOB);
        INSERT INTO ZICCLOUDSYNCINGOBJECT VALUES(1,'Groceries','Milk, eggs, bread',699600000,0,NULL,3);
        INSERT INTO ZICCLOUDSYNCINGOBJECT VALUES(2,'Old note','gone',699500000,1,NULL,3);
        INSERT INTO ZICCLOUDSYNCINGOBJECT VALUES(3,NULL,NULL,NULL,0,'Shopping',NULL);
    """)
    proto = _pb(2, _pb(3, _pb(2, "Groceries\nMilk, eggs, bread\nand coffee".encode())))
    c.execute("INSERT INTO ZICNOTEDATA VALUES(1,1,?)", (gzip.compress(proto),))
    c.commit(); c.close()


def safari_db(path):
    c = sqlite3.connect(path); c.executescript("""
        CREATE TABLE history_items(id INTEGER PRIMARY KEY, url TEXT);
        CREATE TABLE history_visits(id INTEGER PRIMARY KEY, history_item INT,
                                     title TEXT, visit_time REAL);
        INSERT INTO history_items VALUES(1,'https://example.com/');
        INSERT INTO history_visits VALUES(1,1,'Example Domain',699700000);
    """); c.commit(); c.close()


def photos_db(path):
    c = sqlite3.connect(path); c.executescript("""
        CREATE TABLE ZASSET(Z_PK INTEGER PRIMARY KEY, ZDIRECTORY TEXT, ZFILENAME TEXT,
                             ZDATECREATED REAL, ZFAVORITE INT, ZKIND INT);
        INSERT INTO ZASSET VALUES(1,'DCIM/100APPLE','IMG_0001.JPG',699800000,1,0);
        INSERT INTO ZASSET VALUES(2,'DCIM/100APPLE','IMG_0002.JPG',699800100,0,0);
        INSERT INTO ZASSET VALUES(3,'DCIM/100APPLE','IMG_0003.HEIC',699800200,0,0);
    """); c.commit(); c.close()


def voicememos_db(path):
    c = sqlite3.connect(path); c.executescript("""
        CREATE TABLE ZCLOUDRECORDING(Z_PK INTEGER PRIMARY KEY, ZCUSTOMLABEL TEXT,
                                      ZENCRYPTEDTITLE TEXT, ZDATE REAL, ZDURATION REAL, ZPATH TEXT);
        INSERT INTO ZCLOUDRECORDING VALUES(1,'Band practice',NULL,699900000,42.5,'20230101 101010.m4a');
    """); c.commit(); c.close()


def voicemail_db(path):
    c = sqlite3.connect(path); c.executescript("""
        CREATE TABLE voicemail(ROWID INTEGER PRIMARY KEY, date INT, sender TEXT, duration INT,
                               trashed_date INT);
        INSERT INTO voicemail VALUES(7,1700000000,'+15559876543',12,0);
    """); c.commit(); c.close()


def main(root):
    root = Path(root); udid = "0000111122223333444455556666777788889999"
    bdir = root / udid; bdir.mkdir(parents=True, exist_ok=True)
    class_key = os.urandom(32)

    tmp = root / "_src"; tmp.mkdir(exist_ok=True)
    sms_path = tmp / "sms.db"; sms_db(sms_path)
    calls_path = tmp / "calls.db"; calls_db(calls_path)
    contacts_path = tmp / "contacts.db"; contacts_db(contacts_path)
    cal_path = tmp / "cal.db"; calendar_db(cal_path)
    notes_path = tmp / "notes.db"; notes_db(notes_path)
    safari_path = tmp / "safari.db"; safari_db(safari_path)
    photo = b"\xff\xd8\xff\xe0JPEGDATA" + os.urandom(200)
    photo2 = b"\xff\xd8\xff\xe0JPEGDATA" + os.urandom(150)
    photos_path = tmp / "photos.db"; photos_db(photos_path)
    memos_path = tmp / "memos.db"; voicememos_db(memos_path)
    vm_path = tmp / "vm.db"; voicemail_db(vm_path)
    thumb3 = b"\xff\xd8\xff\xe0THUMB" + os.urandom(90)  # IMG_0003's original is gone
    attach = b"\xff\xd8\xff\xe0ATTACH" + os.urandom(64)
    audio = os.urandom(100_003)  # odd size, many AES blocks: exercises ranged decrypt

    entries = [
        ("HomeDomain", "Library/SMS/sms.db", sms_path.read_bytes()),
        ("HomeDomain", "Library/CallHistoryDB/CallHistory.storedata", calls_path.read_bytes()),
        ("HomeDomain", "Library/AddressBook/AddressBook.sqlitedb", contacts_path.read_bytes()),
        ("HomeDomain", "Library/Calendar/Calendar.sqlitedb", cal_path.read_bytes()),
        ("AppDomainGroup-group.com.apple.notes", "NoteStore.sqlite", notes_path.read_bytes()),
        ("AppDomainGroup-group.com.apple.mobilesafari", "Library/Safari/History.db",
         safari_path.read_bytes()),
        ("CameraRollDomain", "Media/DCIM/100APPLE/IMG_0001.JPG", photo),
        ("CameraRollDomain", "Media/DCIM/100APPLE/IMG_0002.JPG", photo2),
        ("CameraRollDomain", "Media/PhotoData/Photos.sqlite", photos_path.read_bytes()),
        ("CameraRollDomain", "Media/PhotoData/Thumbnails/V2/DCIM/100APPLE/IMG_0003.HEIC/5005.JPG", thumb3),
        ("MediaDomain", "Library/SMS/Attachments/ab/01/X/beach.jpg", attach),
        ("AppDomainGroup-group.com.apple.VoiceMemos.shared", "Recordings/CloudRecordings.db",
         memos_path.read_bytes()),
        ("AppDomainGroup-group.com.apple.VoiceMemos.shared", "Recordings/20230101 101010.m4a", audio),
        ("HomeDomain", "Library/Voicemail/voicemail.db", vm_path.read_bytes()),
        ("HomeDomain", "Library/Voicemail/7.amr", b"#!AMR\n" + os.urandom(40)),
        ("AppDomain-com.example.todo", "Library/Preferences/com.example.todo.plist", os.urandom(500)),
    ]

    # encrypted Manifest.db (its own key wrapped under the class key)
    man = root / "_manifest.db"
    mc = sqlite3.connect(man)
    mc.execute("CREATE TABLE Files(fileID TEXT PRIMARY KEY, domain TEXT, "
               "relativePath TEXT, flags INT, file BLOB)")
    for dom, rel, data in entries:
        fid = hashlib.sha1(f"{dom}-{rel}".encode()).hexdigest()
        fkey = os.urandom(32)
        wrapped = aes_key_wrap(class_key, fkey)
        enc = aes_cbc_encrypt(fkey, data)
        (bdir / fid[:2]).mkdir(exist_ok=True)
        (bdir / fid[:2] / fid).write_bytes(enc)
        mc.execute("INSERT INTO Files VALUES(?,?,?,?,?)",
                   (fid, dom, rel, 1, mbfile(len(data), wrapped)))
    mc.commit(); mc.close()

    man_key = os.urandom(32)
    (bdir / "Manifest.db").write_bytes(aes_cbc_encrypt(man_key, man.read_bytes()))
    keybag = build_keybag(class_key)

    itunes = plistlib.dumps({"itemName": "Todo Pro", "artistName": "Example Inc.",
                             "bundleShortVersionString": "2.1", "genre": "Productivity"})
    icon = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                         "1f15c4890000000d4944415478da63f8cfc0f01f0005000201a5e9a7a60000000049454e44ae426082")
    plistlib.dump({"Device Name": "Test iPhone", "Product Type": "iPhone15,2",
                   "Product Version": "17.4", "Unique Identifier": udid,
                   "Installed Applications": ["com.example.todo"],
                   "Applications": {"com.example.todo": {"iTunesMetadata": itunes,
                                                         "PlaceholderIcon": icon}}},
                  open(bdir / "Info.plist", "wb"))
    plistlib.dump({"IsEncrypted": True, "Version": "10.0", "BackupKeyBag": keybag,
                   "ManifestKey": struct.pack("<I", CLASS) + aes_key_wrap(class_key, man_key)},
                  open(bdir / "Manifest.plist", "wb"))
    plistlib.dump({"SnapshotState": "finished", "IsFullBackup": False, "Version": "3.3"},
                  open(bdir / "Status.plist", "wb"))
    print(bdir)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "./synthetic")
