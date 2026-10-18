"""pytest: build a synthetic encrypted backup and verify the full pipeline."""
import http.client
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from minimazing import Backup, BackupPasswordError  # noqa: E402
from minimazing.extractors import records  # noqa: E402
from minimazing import web  # noqa: E402
import tests.make_backup as mk  # noqa: E402

UDID = "0000111122223333444455556666777788889999"
PW = "test-pass-123"


@pytest.fixture(scope="module")
def backup_dir(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("bk")
    mk.main(tmp)
    return tmp / UDID


@pytest.fixture(scope="module")
def b(backup_dir):
    bk = Backup(backup_dir).open(PW)
    yield bk
    bk.close()


def test_wrong_password_rejected(backup_dir):
    with pytest.raises(BackupPasswordError):
        Backup(backup_dir).open("nope")


def test_messages(b):
    msgs = records(b, "messages")
    assert [m["text"] for m in msgs][:3] == ["Hey, this is a test", "Reply from me", None]
    assert msgs[0]["from_me"] is False and msgs[1]["from_me"] is True
    assert msgs[2]["has_attachment"] is True
    att = msgs[2]["attachments"][0]
    assert att["name"] == "beach.jpg" and att["file_id"]
    assert b.read(b.get(att["file_id"]))[:4] == b"\xff\xd8\xff\xe0"
    # text recovered from attributedBody (2-byte length prefix path)
    assert msgs[3]["text"].startswith("Only in attributedBody xxx") and len(msgs[3]["text"]) == 163
    assert msgs[0]["sender"] == "+15551234567"  # no contact for this number


def test_photos_best_quality(b):
    ph = {p["filename"]: p for p in records(b, "photos")}
    f = b.find("CameraRollDomain", "Media/DCIM/100APPLE/IMG_0001.JPG")
    data = b.read(f)
    assert data[:4] == b"\xff\xd8\xff\xe0" and len(data) == f.size
    assert ph["IMG_0001.JPG"]["quality"] == "original"
    assert ph["IMG_0001.JPG"]["best_id"] == f.file_id
    gone = ph["IMG_0003.HEIC"]  # original deleted, thumbnail survives
    assert gone["file_id"] is None and gone["quality"] == "thumbnail"
    assert gone["best_id"] and gone["thumb_id"] == gone["best_id"]
    assert b.read(b.get(gone["best_id"]))[:9] == b"\xff\xd8\xff\xe0THUMB"


def test_other_readers(b):
    notes = records(b, "notes")
    assert [n["title"] for n in notes] == ["Groceries"]  # deleted note skipped
    assert notes[0]["body"] == "Groceries\nMilk, eggs, bread\nand coffee"
    assert notes[0]["folder"] == "Shopping"
    c = records(b, "contacts")[0]
    assert c["phones"] == ["+15559876543"] and c["emails"] == ["ada@example.com"]
    calls = records(b, "calls")
    assert any(x["name"] == "Ada Lovelace" for x in calls)  # resolved via contacts
    memo = records(b, "voicememos")[0]
    assert memo["title"] == "Band practice" and memo["file_id"] and memo["duration_s"] == 42.5
    vm = records(b, "voicemail")[0]
    assert vm["title"] == "Ada Lovelace" and vm["file_id"]
    app = records(b, "apps")[0]
    assert app["name"] == "Todo Pro" and app["developer"] == "Example Inc."
    assert app["data_size"] == 500 and app["has_icon"]


def test_ranged_decrypt_matches_full_read(b):
    f = next(x for x in b.files(domain="AppDomainGroup-group.com.apple.VoiceMemos.shared")
             if x.name.endswith(".m4a"))
    full = b.read(f)
    assert len(full) == f.size == 100_003
    for start, end in [(0, 16), (5, 37), (16, 4096), (99_990, 100_003), (12_345, 87_654)]:
        assert b"".join(b.iter_range(f, start, end, chunk=4096)) == full[start:end]


def test_list_dir(b):
    root = b.list_dir("CameraRollDomain")
    assert root["dirs"][0]["name"] == "Media"
    d = b.list_dir("CameraRollDomain", "Media/DCIM/100APPLE")
    assert [x["name"] for x in d["files"]] == ["IMG_0001.JPG", "IMG_0002.JPG"]


# ---------------------------------------------------------------- web API

@pytest.fixture()
def server(backup_dir):
    web.Handler.app = web.App(Backup(backup_dir), [str(backup_dir.parent)])
    srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    web.Handler.app.backup.close()


def _req(port, method, path, body=None, headers=None):
    con = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    con.request(method, path, body=json.dumps(body) if body is not None else None,
                headers={"Content-Type": "application/json", **(headers or {})})
    r = con.getresponse()
    data = r.read()
    return r.status, dict(r.getheaders()), data


def test_web_unlock_flow_and_api(server):
    port = server
    st, _, data = _req(port, "GET", "/api/state")
    assert json.loads(data)["stage"] == "locked"
    assert _req(port, "GET", "/api/datasets")[0] == 409
    assert _req(port, "POST", "/api/unlock", {"password": "bad"})[0] == 401
    assert json.loads(_req(port, "POST", "/api/unlock", {"password": PW})[2])["stage"] == "ready"

    chats = json.loads(_req(port, "GET", "/api/chats/messages")[2])
    assert len(chats) == 1 and chats[0]["count"] == 4
    thread = json.loads(_req(port, "GET", f"/api/chat/messages/{chats[0]['chat_id']}")[2])
    assert len(thread["messages"]) == 4
    assert json.loads(_req(port, "GET", "/api/chats/messages?q=nomatch")[2]) == []

    st, _, data = _req(port, "GET", "/api/export/messages.html")
    assert st == 200 and b"Reply from me" in data

    memo = json.loads(_req(port, "GET", "/api/dataset/voicememos")[2])["records"][0]
    st, h, part = _req(port, "GET", f"/api/file/{memo['file_id']}", headers={"Range": "bytes=100-299"})
    assert st == 206 and len(part) == 200 and h["Content-Range"] == "bytes 100-299/100003"

    assert _req(port, "GET", "/api/appicon/com.example.todo")[2][:4] == b"\x89PNG"


def test_web_rejects_foreign_host_and_origin(server):
    port = server
    assert _req(port, "GET", "/api/state", headers={"Host": "evil.example:80"})[0] == 403
    st = _req(port, "POST", "/api/unlock", {"password": PW},
              headers={"Origin": "http://evil.example"})[0]
    assert st == 403
