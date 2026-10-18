"""pytest: build a synthetic encrypted backup and verify the full pipeline."""
import subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from minimazing import Backup, BackupPasswordError
from minimazing.extractors import messages
import tests.make_backup as mk

def _make(tmp):
    mk.main(tmp); return Path(tmp)/"0000111122223333444455556666777788889999"

def test_wrong_password_rejected(tmp_path):
    b = Backup(_make(tmp_path))
    try: b.open("nope"); assert False
    except BackupPasswordError: pass

def test_messages_and_photo(tmp_path):
    b = Backup(_make(tmp_path)).open("test-pass-123")
    msgs = messages(b)
    assert [m["text"] for m in msgs] == ["Hey, this is a test", "Reply from me"]
    assert msgs[0]["from_me"] is False and msgs[1]["from_me"] is True
    f = b.find("CameraRollDomain","Media/DCIM/100APPLE/IMG_0001.JPG")
    data = b.read(f)
    assert data[:4] == b"\xff\xd8\xff\xe0" and len(data) == f.size
    b.close()
