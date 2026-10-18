# minimazing

A tiny, readable iMazing-style reader for iOS backups. It opens a backup folder
(Finder/iTunes or iMazing), decrypts it if encrypted, and lets you browse and
export messages (with attachments), WhatsApp, contacts, calls, voicemail,
calendar, notes (full text), Safari, photos/videos, voice memos, ringtones,
installed apps and the raw file system — from the command line or a local
browser UI.

This is a teaching-sized clone of the parts of iMazing that read a backup. It is
not affiliated with DigiDNA. Use it only on backups of devices you own.

## Why it works

An iOS backup is a documented format. Each file's contents live in `ab/abcdef…`
named by `sha1("domain-relativePath")`, indexed by `Manifest.db`
(`Files(fileID, domain, relativePath, flags, file)`). When the backup is
encrypted, iOS (not the computer) does the crypto with a keybag stored in
`Manifest.plist`:

```
password ─PBKDF2-SHA256(DPSL,DPIC)→ ─PBKDF2-SHA1(SALT,ITER)→ passcode key
passcode key ─AES-unwrap(RFC 3394)→ class keys
class key    ─AES-unwrap→ Manifest.db key / per-file key
file bytes   ─AES-256-CBC(IV=0)→ plaintext (truncate to recorded Size)
```

`minimazing/crypto.py` implements exactly that; `minimazing/backup.py` gives
random access to any file; `extractors.py` runs one query per dataset against
the Apple app DBs (same table/paths iMazing hard-codes, e.g.
`HomeDomain Library/SMS/sms.db`).

## Install

```
pip install cryptography          # the only hard dependency
# optional, only to capture a new backup from a device:
brew install libimobiledevice
```

## Use

```
python -m minimazing list                         # find backups on this machine
python -m minimazing info  <backup_path> -p PW     # device + which datasets exist
python -m minimazing export <backup_path> messages -p PW -o ./out
python -m minimazing export <backup_path> photos  -p PW -o ./out --files
python -m minimazing export <backup_path> all -p PW -o ./out --files   # everything
python -m minimazing web                           # pick a backup + unlock in the browser
python -m minimazing web   <backup_path>           # browser asks for the password
python -m minimazing web   <backup_path> --password-file ./pw.txt
```

The web UI runs at http://127.0.0.1:8777 and only answers to localhost.

`<backup_path>` is the per-device folder (the one holding `Manifest.plist`).
Omit `-p` and you'll be prompted; plain (unencrypted) backups need no password.

## Photos when the original is gone

With iCloud Photos (or after deleting originals) a backup often holds only a
fraction of the originals. minimazing then shows the best image the backup
still kept for each asset, in order: the original, an edited render, the
`Thumbnails/V2/.../5005.JPG` preview, a video poster frame, then the tiny
`.THM`. The viewer says which one you're looking at; `export photos --files`
writes the same best copy (named `IMG_1234 (thumbnail).JPG` when it isn't the
original). On macOS, HEIC images and AMR voicemail are converted on the fly
with the built-in `sips` / `afconvert` so any browser can show them.

## Capture a fresh backup (optional)

```python
from minimazing import capture, Backup
udid = capture.paired_devices()[0]
folder = capture.backup("~/minimazing-backups", udid)   # drives mobilebackup2
Backup(folder).open("your-password")
```

iMazing drives Apple's private `MobileDevice.framework` directly; this wraps the
open-source `idevicebackup2`, which speaks the same lockdownd/mobilebackup2
services and produces an identical folder.

## Test

```
pip install pytest playwright && playwright install chromium
python -m pytest -q                                # unit + API tests on a synthetic backup
python tests/make_backup.py ./synthetic           # builds a real encrypted backup
python -m minimazing web ./synthetic/<udid> --port 8780 --no-browser &
python tests/browser_check.py http://127.0.0.1:8780   # drives the UI headlessly
```

## Layout

```
minimazing/
  crypto.py       keybag parse + unlock, AES key-unwrap, AES-CBC
  backup.py       open/decrypt a backup, random file access, SQLite materialise
  extractors.py   one reader per dataset (+ attributedBody / Notes protobuf decoders)
  exporters.py    CSV, JSON, HTML transcript with attachments, media files
  media.py        HEIC->JPEG, AMR->M4A, grid thumbnails (macOS sips/afconvert)
  capture.py      optional device capture via libimobiledevice
  web.py          stdlib local browser UI (picker, unlock, Range streaming)
  cli.py          command line
tests/make_backup.py   synthetic encrypted backup generator
```

## What this intentionally leaves out vs iMazing

Restoring to a device; incremental snapshots with hard-links
(`iMazing.Versions`); the music library (it isn't in backups — iMazing reads it
from a connected device); Photos albums/Live Photos pairing; PDF/Excel/RSMF export; signed-PDF checksums;
Wi-Fi sync; spyware (MVT) scanning; the Configurator/MDM side.
