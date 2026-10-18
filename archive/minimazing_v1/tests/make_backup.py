"""Generate a synthetic *encrypted* iOS backup to exercise the reader end-to-end.
Builds a real keybag (double PBKDF2 + AES key-wrap), wraps per-file keys, encrypts
Manifest.db and each file with AES-256-CBC, and writes the ab/ blob layout.
"""
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
                             is_from_me INT, date INT);
        CREATE TABLE chat_message_join(chat_id INT, message_id INT);
        INSERT INTO handle VALUES(1,'+15551234567');
        INSERT INTO chat VALUES(1,'+15551234567');
        INSERT INTO message VALUES(1,'Hey, this is a test',1,0,700000000000000000);
        INSERT INTO message VALUES(2,'Reply from me',1,1,700000001000000000);
        INSERT INTO chat_message_join VALUES(1,1),(1,2);
    """); c.commit(); c.close()


def main(root):
    root = Path(root); udid = "0000111122223333444455556666777788889999"
    bdir = root / udid; bdir.mkdir(parents=True, exist_ok=True)
    class_key = os.urandom(32)

    # files to include: Manifest.db payload + sms.db + a photo
    tmp = root / "_src"; tmp.mkdir(exist_ok=True)
    sms_path = tmp / "sms.db"; sms_db(sms_path)
    photo = b"\xff\xd8\xff\xe0JPEGDATA" + os.urandom(200)

    entries = [
        ("HomeDomain", "Library/SMS/sms.db", sms_path.read_bytes()),
        ("CameraRollDomain", "Media/DCIM/100APPLE/IMG_0001.JPG", photo),
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

    plistlib.dump({"Device Name": "Test iPhone", "Product Type": "iPhone15,2",
                   "Product Version": "17.4", "Unique Identifier": udid},
                  open(bdir / "Info.plist", "wb"))
    plistlib.dump({"IsEncrypted": True, "Version": "10.0", "BackupKeyBag": keybag,
                   "ManifestKey": struct.pack("<I", CLASS) + aes_key_wrap(class_key, man_key)},
                  open(bdir / "Manifest.plist", "wb"))
    plistlib.dump({"SnapshotState": "finished", "IsFullBackup": False, "Version": "3.3"},
                  open(bdir / "Status.plist", "wb"))
    print(bdir)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "./synthetic")
