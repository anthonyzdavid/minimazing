# minimazing: plan for resuming development

Written 2026-10-18. Two goals for the next round of work:

1. **Complete the test suite and preserve photo albums.** Every reader, the web API, the CLI and the UI are covered and run with one command. Photos are browsable and exportable in the same albums and folders as on the iPhone.
2. **Open backups that aren't on this disk**: an S3 (or S3-compatible) bucket, or another machine reachable over SSH (a Linux Raspberry Pi).

Milestone 1 comes first, because Milestone 2 changes how every byte is read and needs those tests as a safety net.

---

## 0. Where things stand

**Works today, verified against the real backup** (`../backups/<UDID>`, iPhone13,3, iOS 17.1.2, 191,077 files, 62 GB):

| Area | Status |
|---|---|
| Unlock and decrypt | Keybag unlock, Manifest.db decrypt, per-file AES-CBC, random-access ranged decrypt for multi-GB video (`Backup.iter_range`) |
| Messages | 156,737 messages / 986 chats; `attributedBody` text recovery; contact names; group names; attachments (6,612 of 6,648 resolved) |
| WhatsApp | 17,326 messages; media resolved 1,549 of 1,609 |
| Photos | 12,960 assets; originals only for 126 (the rest were deleted from iCloud). For the rest, the best surviving copy is chosen: edited render > 5005 thumbnail (360×640) > poster frame > .THM. About 1,000 shared-album items have no image in the backup at all |
| Other readers | Contacts, calls (Phone/FaceTime/WhatsApp), voicemail (AMR→M4A), calendar, notes (full body from the gzip protobuf), voice memos, ringtones, apps (icons + data size), file-system browser |
| Web UI | Backup picker, in-browser unlock, Range streaming, HEIC→JPEG via `sips`, localhost-only (Host + Origin checks) |
| CLI | `list`, `info`, `export <ds>/all [--files]`, `web [path]`, `--password-file` |

**Gaps:**
- Safari is empty in this backup, because `History.db` has 0 rows. The reader is untested on real data.
- Music isn't in iOS backups; the nav item is intentionally disabled.

**Resume in 2 minutes:**

```bash
cd ~/Desktop/Code/mazingClone/minimazing_app
git init && git add -A && git commit -m "minimazing baseline"      # not a repo yet; do this first
.venv/bin/python -m pytest -q                                       # 8 tests, ~2 s
.venv/bin/python -m minimazing web ../backups/<UDID> --password-file ../envtest
```

If `.venv` is gone: `python3 -m venv .venv && .venv/bin/pip install cryptography pytest playwright && .venv/bin/python -m playwright install chromium`.

**Code map** (all in `minimazing/`):
- `backup.py`: folder access, decrypt, `memo()`, `index()`, `list_dir()`, `iter_range()`
- `extractors.py`: one reader per dataset, plus the decoders
- `web.py`: HTTP API
- `static/app.js`: UI
- `media.py`: conversions
- `exporters.py`, `cli.py`
- `tests/make_backup.py`: builds a real *encrypted* synthetic backup

---

## Milestone 1: Complete the tests and preserve photo albums

**Done when:**
- `pytest -q` runs everything in one command (unit, API, CLI, browser), green on macOS.
- Line coverage is ≥ 85% for `backup.py`, `crypto.py`, `extractors.py`, `web.py`, `exporters.py`, `cli.py`.
- An opt-in smoke test passes against the real backup.
- Photos show the iPhone's album and folder structure in the UI, and `export photos --files` writes that structure as folders (section 1.9).

### 1.1 Project hygiene (do first, ~1 h)
- Add `pyproject.toml`:
  - Package metadata and a `minimazing` console script.
  - Extras: `dev = [pytest, pytest-cov, playwright]`.
  - `[tool.pytest.ini_options]` with markers `e2e`, `real`, `remote`.
- Move the synthetic fixture into `tests/conftest.py`: one session-scoped encrypted backup plus the variants below, each built once.
- Optional: a GitHub Actions workflow on `macos-latest`. macOS is needed for the `sips`/`afconvert` paths; Ubuntu covers everything else.

### 1.2 Fill the synthetic fixture (`tests/make_backup.py`)
Each item adds rows and files to the generator, plus assertions:

| Missing today | Add |
|---|---|
| WhatsApp | `ChatStorage.sqlite`:<br>• `ZWACHATSESSION`, `ZWAMESSAGE`<br>• `ZWAGROUPMEMBER` (group sender names)<br>• `ZWAMEDIAITEM` with a `Message/Media/...` file, plus one missing file |
| Group iMessage | `chat.display_name`, `chat_handle_join` with 2 handles (name built from participants), a tapback row (`associated_message_type=2000`) that must be skipped, a `.pluginPayloadAttachment` that must be skipped, an attachment whose file is absent |
| `attributedBody` | Short (1-byte length), `0x81` and `0x82` length forms, empty string, truncated or garbage blob (must return `None`, not raise) |
| Calendar | `Calendar` + `Location` tables, an all-day event |
| Ringtones | `TonesDomain/Media/Purchases/Ringtones.plist` + `.m4r` |
| Photos | • An edited render larger than the thumbnail (must win)<br>• A video with only a poster frame<br>• A UBF "Shared with You" item<br>• A trashed asset (excluded)<br>• An asset with no image at all |
| Notes | Locked note (body must stay hidden), folder rows, zlib (non-gzip) body, corrupt body |
| Backup variants | **Unencrypted** backup; **pre-iOS-10 flat layout** (blobs in the root, not `ab/`); legacy schemas: `call_history.db` (`call` table), `ZRECORDING` voice memos, Notes without `ZICNOTEDATA` |

### 1.3 Unit tests (pure functions)
- **`crypto`:**
  - Keybag with several classes, including a device-only class (`WRAP=1`) that must be skipped.
  - Wrong password.
  - `strip_pkcs7` edge cases.
  - `aes_cbc_decrypt` with a non-zero IV.
- **`extractors`:**
  - `attributed_text` and `note_body` (table above).
  - `_t`: seconds vs nanoseconds, `0`, overflow → `None`.
  - `_norm_handle`: `+1 (555) …` vs `5551234567`, email case.
  - `_clean_label`.

### 1.4 Backup layer
- **`iter_range`** property test: random `(start, end, chunk)` against a full `read()`, for both encrypted and unencrypted, including `start` at block boundaries and `end == size`.
- **`extract`** streaming path: monkeypatch the 64 MB threshold down so it's exercised.
- **`memo()`**: 8 threads call the same key; `fn` runs once.
- **`list_dir`**: names containing `%`, `_` and `\`; directory rows vs implied directories.
- **`domain_sizes`**: the SMS bucket.

### 1.5 Web API (`tests/test_web.py`, server fixture already exists)
- **Gate flow:**
  - `/api/open` with a good path, a bad path, and an unencrypted backup (auto-opens).
  - `/api/close` returns to `pick`.
  - `/api/backups` with `--search`.
- **Data endpoints:** `/api/fs`, `/api/domains`, `/api/count/files`, `/api/dataset?q=&offset=&limit=`, `/api/export/<ds>.html?chat=` (filename and contents), 404s.
- **Media:**
  - `/api/thumb` and `/api/view` with a fake `sips` on `PATH` (a tiny shell script that copies input to output), so tests stay hermetic.
  - With no `sips`, the raw bytes are served.
- **Range edge cases:**
  - `bytes=-100`, `bytes=500-`, beyond EOF, multiple ranges.
  - **Decide and test** an unsatisfiable range. Today it falls back to a full 200; RFC 9110 says 416.
- **Security:**
  - Static path traversal (`/../backup.py`, `%2e%2e`).
  - Forbidden Host and cross-origin POST (already tested).
  - `HEAD`.
  - 20 concurrent `/api/count/*` requests on a cold cache (no deadlock, each reader runs once).

### 1.6 CLI (`tests/test_cli.py`, call `cli.main([...])`)
- `list` (monkeypatch `DEFAULT_LOCATIONS`), `info`.
- `export all --files` into `tmp_path`: assert the JSON/CSV/HTML files exist, attachments are linked from the HTML, and photo previews are named `IMG_x (thumbnail).JPG`.
- `--password-file`; a wrong password exits non-zero.

### 1.7 Browser e2e (marker `e2e`)
- Fold `tests/browser_check.py` into pytest: a fixture starts `web` on a free port with no password, and Playwright drives it.
- Add:
  - The picker flow (start with no backup + `--search`).
  - The WhatsApp thread.
  - A chat search that jumps to the match.
  - Photo filters (Videos / Favorites / Originals).
  - The notes search.
  - The apps sort.
  - File-browser breadcrumbs.
  - "Load earlier messages" (fixture chat with more than 400 messages).

### 1.8 Real-backup smoke test (marker `real`, skipped by default)
```bash
MINIMAZING_REAL_BACKUP=../backups/<UDID> \
MINIMAZING_PASSWORD_FILE=../envtest pytest -m real
```
- Asserts each reader returns without error and with count > 0 (except Safari).
- Asserts photo `quality` counts are non-zero for `original` and `thumbnail`.
- Asserts `/api/view` works on one HEIC.
- **Never print record contents**: the backup is private. Assert on counts and types only.
- Asserts albums are found: 37 user albums, 7 shared albums and 3,788 memberships on this backup (section 1.9).

### 1.9 Photo albums, organized like the iPhone (new feature)
Today Photos is one flat grid. The iPhone, and iMazing, show albums inside folders. All of that is in the backup's `Photos.sqlite`, so it can be rebuilt.

**What's in the real backup** (checked 2026-10-18, structure only):
- **`ZGENERICALBUM`**, one row per album or folder:
  - `ZTITLE`, `ZKIND`, `ZUUID`.
  - `ZPARENTFOLDER`, the parent folder's `Z_PK` (this is the folder tree).
  - `ZCUSTOMSORTKEY` / `ZCUSTOMSORTASCENDING`, `ZTRASHEDSTATE`, `ZCLOUDGUID`.
  - Cached counts.
- **Kinds seen:**

  | `ZKIND` | Count | Meaning |
  |---|---|---|
  | `2` | 37 | User albums, all with a parent folder |
  | `1505` | 7 | Shared albums |
  | `3999` | 1 | Root folder |
  | `4000`–`4006` | 1 each | Folder kinds |
  | `1506`, `1509`, `1510`, `1552`, `1600`–`1639`, `3571`–`3573`, `3998` | — | System, smart and import rows |

  **Verify every meaning before relying on it**: create a test album or folder on a phone, back it up, and see which row appears.
- **Membership** is a join table, here `Z_28ASSETS(Z_28ALBUMS → album, Z_3ASSETS → asset, Z_FOK_3ASSETS → order)`, with 3,788 rows. **The numbers `28` and `3` are Core Data entity IDs that change between iOS versions.** Resolve them at runtime from `Z_PRIMARYKEY` (`Z_NAME` = `GenericAlbum` / `Asset`, giving `Z_ENT`), never hard-code them. iMazing does the same: its binary joins `Z_PRIMARYKEY` on `Z_ENT` for both tables.
- **iMazing's album query** also skips `ZKIND` 1501 and 1504 and trashed albums (`ZTRASHEDDATE`), ordered by `Z_PK`. Start from the same filter.

**Build:**
1. **Reader** `albums(b)` in `extractors.py`. It returns a tree:
   - Each node: `{uuid, title, kind, parent, children, asset_ids (in Z_FOK order), count, cover}`.
   - Trashed albums excluded.
   - Sorted the way Photos sorts: custom sort key when set, else title.

   Add `albums: [uuid…]` to each photo record, so the grid can filter without a second query.
2. **iPhone-style sections**, matching the Albums tab:

   | Section | Source |
   |---|---|
   | **Library / Recents** | All assets (today's grid) |
   | **Favorites** | `ZFAVORITE` |
   | **My Albums** | Folder tree under the root folder; user albums and folders nested to any depth (this backup is only 1 level deep, so test deeper nesting synthetically) |
   | **Shared Albums** | Kind `1505`. Their files are under `PhotoData/PhotoCloudSharingData/<id>/<album GUID>/`; match on `ZCLOUDGUID` |
   | **Media Types** | Derived from `ZASSET`, not stored as albums: Videos (`ZKIND=1`), Live Photos, Portrait, Panoramas, Slo-mo, Time-lapse, Screenshots, Screen Recordings via `ZKINDSUBTYPE` / `ZPLAYBACKSTYLE` / `ZDEPTHTYPE`. Pin each mapping with a test; iMazing selects these same columns |
   | **Utilities** | Hidden (`ZHIDDEN`); Recently Deleted (`ZTRASHEDSTATE=1`, excluded today); Imports (kind `1506`) |
3. **API:**
   - `/api/albums` returns the tree with counts and a cover `thumb_id`.
   - `/api/dataset/photos?album=<uuid>` returns one album, in album order.
4. **UI**: Photos gets a left album column, like Photos.app:
   - Folders expand and collapse.
   - Selecting an album filters the grid, with a breadcrumb `My Albums › Folder › Album`.
   - The lightbox arrows stay within the album.
   - Search applies within the selected album.
5. **Export** with `export photos --files --albums`. Layout:

   ```
   photos/
     Library/                       every asset (best available copy, as today)
     Albums/<Folder>/<Sub-folder>/<Album>/IMG_…
     Shared Albums/<Album>/…
     Media Types/Videos/…  (opt-in: --media-types)
   ```

   - One photo can be in several albums. Hard-link the album copies to the `Library/` file (same disk, so no extra space); fall back to copying when hard links aren't possible.
   - Keep album order with an optional `--numbered` prefix (`0001 IMG_….HEIC`).
   - Make folder names filesystem-safe: replace `/` and `:`, and handle duplicate names with ` (2)`.
   - Photos whose only copy is a preview keep the `IMG_x (thumbnail).JPG` naming.
   - Add an `albums.json` sidecar with the full tree, so a later re-import has the structure.
6. **Tests:**
   - Synthetic `Photos.sqlite` gets:
     - `Z_PRIMARYKEY`.
     - `ZGENERICALBUM` with a root folder, a folder containing a sub-folder containing an album, a top-level album, a shared album and a trashed album (must be excluded).
     - A membership table with **deliberately non-default entity numbers** (e.g. `Z_26ASSETS` / `Z_26ALBUMS` / `Z_5ASSETS`), to prove runtime lookup.
     - One asset in two albums.
     - `Z_FOK` ordering that differs from date order.
   - Assert:
     - The tree shape and the counts.
     - Album order.
     - The `/api/albums` response.
     - Export creates the folder tree, with hard links where possible (`os.stat().st_ino` equal).
     - Name sanitizing and duplicate handling.
   - e2e: click folder › sub-folder › album, check the grid count and breadcrumb, and that arrows stay in the album.
   - Real smoke: the album counts quoted above.

---

## Milestone 2: Open backups from S3 or another machine

### 2.0 Already possible, zero code: run it where the backup lives
If the other machine has Python, run minimazing *there* and tunnel the UI:
```bash
ssh -L 8777:127.0.0.1:8777 user@backup-host \
  'cd minimazing_app && .venv/bin/python -m minimazing web /path/to/<UDID> --no-browser'
# then open http://127.0.0.1:8777 locally
```
Only what you look at crosses the network, and the server stays localhost-only on both ends. Document this in the README; the rest of this milestone covers S3 and reading a remote disk directly.

**For the Raspberry Pi specifically, prefer SFTP from the Mac (2.1) over running minimazing on the Pi.** The tunnel works, but the Pi would have to do the heavy parts:
- **Unlock is CPU-bound.** This backup's keybag uses 10,000,000 PBKDF2-SHA256 rounds. This Mac does 1 M rounds in 0.12 s (about 1.2 s to unlock); expect a Pi to be roughly 10× slower or worse. Measure with `python3 -c "import hashlib,time;t=time.time();hashlib.pbkdf2_hmac('sha256',b'x',b's',1_000_000);print(time.time()-t)"`.
- **No `sips` / `afconvert` on Linux**, so HEIC photos and AMR voicemail wouldn't convert. If the tunnel route is ever used, add Linux fallbacks to `media.py` (`pillow-heif` for HEIC, `ffmpeg` for AMR); that's worth doing anyway.

With SFTP the Pi only serves encrypted bytes; decryption, conversion and the UI stay on the Mac, and the password never reaches the Pi. The Pi needs nothing beyond its standard OpenSSH server.

### 2.1 Design: a storage seam
A backup is a folder of immutable-per-snapshot files read by relative path, so all I/O can go through one small interface.

New module `minimazing/storage.py`:

```python
class BackupStore(Protocol):
    uri: str                                       # "file:///…", "s3://bucket/prefix/<UDID>", "sftp://host/path"
    def exists(self, rel: str) -> bool: ...
    def size(self, rel: str) -> int: ...
    def read_bytes(self, rel: str) -> bytes: ...
    def read_range(self, rel: str, start: int, end: int) -> bytes: ...   # [start, end)
    def fetch_to(self, rel: str, dest: Path) -> Path: ...                 # whole-file download, resumable
    def children(self) -> list[str]: ...                                  # for discovery

def open_store(location: str | Path) -> BackupStore: ...   # picks Local / Fsspec by scheme
```

- **`LocalStore`**: today's behavior (Path-based). It must stay the zero-dependency default.
- **`FsspecStore`**: one implementation for every remote, via [fsspec](https://filesystem-spec.readthedocs.io/). It supports `s3://` (s3fs), `sftp://`/`ssh://` (sshfs), `gs://`, `az://` and `https://`, all with ranged reads. Ship them as optional extras: `pip install minimazing[s3]`, `[ssh]`.

**Touchpoints to route through the store** (all I/O is already concentrated here):

| File | What changes |
|---|---|
| `backup.py` `Backup.__init__` / `_plist` (≈ l.85–107) | `self.path` becomes `self.store`; plists via `store.read_bytes` |
| `backup.py` `open()` (≈ l.130–145) | Manifest.db via `store.fetch_to(cache)` instead of `read_bytes` / `copyfile` |
| `backup.py` `_blob_path` / `read` / `iter_range` (≈ l.262–330) | `_blob_rel()` returns `ab/abcd…` or the flat name; reads use `store.read_range` with the existing block-IV logic unchanged |
| `backup.py` `discover()` (≈ l.389) | Accepts URIs; lists `prefix/*/Manifest.plist` |
| `web.py` `_send_backup_file` (≈ l.162), `/api/open` (≈ l.225) | Size from the store; accept URIs in the picker |

`iter_range` already reads one block before `start` for the CBC IV. That works unchanged with ranged GETs: request `[(block-1)*16, end rounded up to 16)`.

### 2.2 Caching (needed for a usable UI)
- **Manifest.db**: download once. It is 203 MB on the real backup, so about 16 s at 100 Mbps.
  - Location: `~/Library/Caches/minimazing/<sha1(uri)>/`.
  - Revalidate by size + ETag/mtime, and against `Status.plist` (`UUID`, `Date`), which changes with every new backup snapshot.
- **Blob chunks**: an LRU disk cache of fixed 1 MiB aligned ciphertext chunks.
  - Key: `(fileID, chunk#, snapshot UUID)`.
  - Default cap **2 GB** (agreed 2026-10-18), changeable with `--cache-size`; "Clear cache" control in the UI. Manifest.db (203 MB here) counts toward the cap but is evicted last.
- **Cache ciphertext, never plaintext.** iOS backup encryption is the at-rest protection, so the cache is no more sensitive than the bucket. Decrypted SQLite copies keep living in the per-session temp dir, deleted on close, as today.
- App databases (`sms.db` and the others) are read whole by `b.sqlite()`. That's fine, and they go through the same chunk cache.

### 2.3 Latency and progress
- **Photo grid**: 240 thumbnails per screen is about 240 GETs.
  - Add a small thread pool (8–16) that prefetches the next screen's `thumb_id`s once the grid renders.
  - Reuse HTTP connections (fsspec/s3fs pools them).
- **Progress**: add `/api/progress` (or SSE) for long phases ("Downloading Manifest.db 45%", "Decrypting…"), shown on the unlock screen.
- **Target**: on a warm cache, the photo grid's first screen in < 3 s and chat open in < 1 s.

### 2.4 Credentials and security
- **S3**:
  - The standard AWS credential chain (env vars, `~/.aws` profiles, SSO, instance role), plus `--profile`, `--region`, `--endpoint-url` (MinIO, Cloudflare R2, Backblaze B2).
  - Ship a read-only IAM policy snippet (`s3:GetObject`, `s3:ListBucket` on the prefix).
- **SSH**: honor `~/.ssh/config` and ssh-agent, with strict `known_hosts`. No password prompts in the web UI in v1.
- **Never** accept secrets inside URIs, and never log a URI that contains userinfo. Allowlist schemes: `file`, `s3`, `sftp`, `ssh`; others only behind a flag.
- The backup password never leaves the machine; decryption is always local. **Unencrypted backups stored remotely** get a visible warning in the UI.
- While touching passwords: add `--password-keychain <service>` (macOS `security find-generic-password`) so the plain-text `envtest` file can go away.

### 2.5 "A backup file" (archives)
Both forms are needed: remote locations may hold a plain backup folder, an archive file, or both.

- **`.zip` of a backup folder**: supported through fsspec chaining (`zip::s3://bucket/backup.zip`). Zip has a central directory, so random access still works with ranged reads. Test it.
- **`.tar` / `.tar.gz`**: no random access. Offer `minimazing fetch <uri> <dest>` (resumable download + extract) and open locally.
- **iMazing's own export formats**: unknown. Inspect `reference/iMazing.app` with REA if one turns up.

### 2.6 UI and CLI
- `info`, `export` and `web` accept URIs. `--search` accepts `s3://bucket/prefix/`.
- **Picker**:
  - A "Remote location" field.
  - Remote backups listed with a cloud badge.
  - The sidebar shows the source.
  - Cache size and a clear button in Overview.
- New `minimazing fetch <uri> <dest>`: mirror a remote backup locally, resumable. It's the fallback for slow links and the way to handle tarballs.

### 2.7 Tests for remote storage (marker `remote`)
- **Unit**: `FsspecStore` over fsspec's `memory://` filesystem, with the synthetic backup copied in.
- **S3**: moto's `ThreadedMotoServer` + s3fs. Upload the synthetic backup and run **the existing reader/API suite parametrized over `[local, s3]`**.
- **SFTP**: a local `asyncssh` SFTP server fixture, or a docker `atmoz/sftp` container behind a marker. Same parametrized suite.
- **The real Pi** (marker `pi`, opt-in): `MINIMAZING_PI_URI=sftp://pi.local/path/to/<UDID>`. Open, unlock, read one chat, and load one photo-grid screen. Record timings for the 2.3 targets.
- **`LatencyStore`** wrapper (adds a 50 ms sleep per call, counts requests). Assert the cache and prefetch cut repeat requests to ~0, and that a ranged read fetches only the needed chunks.
- **Correctness**: remote `iter_range` == local for random ranges. A cache entry is invalidated when `Status.plist` changes.
- **Security**: URIs with credentials are rejected; schemes outside the allowlist are refused by `/api/open`.

**Milestone 2 done when:**
- The synthetic backup passes the full suite from local, S3 (moto) and SFTP.
- The real backup opens from the Pi over SFTP, and from a real bucket once one is chosen (open question 1).
- The latency targets in 2.3 are met on a warm cache.

---

## Suggested order and rough size

| Step | Size |
|---|---|
| 1.1 hygiene + `git init` | ½ day |
| 1.2–1.6 fixture + unit/API/CLI tests | 2–3 days |
| 1.7–1.8 e2e + real smoke | 1 day |
| 1.9 photo albums: reader, UI album column, folder export, tests (after 1.2, so the fixture exists) | 2–3 days |
| 2.0 document the SSH-tunnel option | 1 h |
| 2.1 storage seam with `LocalStore` only (pure refactor; the Milestone 1 suite must stay green) | 1 day |
| 2.2–2.3 `FsspecStore`, cache, prefetch, progress | 2–3 days |
| 2.4–2.7 credentials, archives, UI/CLI, remote tests | 2–3 days |

## Decisions and open questions

**Answered 2026-10-18:**
- **Other machine:** a Linux Raspberry Pi reachable over SSH. The plan reads it over SFTP from the Mac (see 2.0).
- **What's stored remotely:** plain backup folders and/or archive files. Both are supported (2.5).
- **Cache:** caching ciphertext locally is fine; the cap is 2 GB.

**Still open:**
1. **Which cloud storage?** AWS S3, or an S3-compatible service (R2, MinIO, B2)? Is there a bucket to test against? Until this is answered, build and test S3 against moto only. The Pi path doesn't depend on it.
2. **Archive format:** `.zip`, `.tar`, `.tar.gz`, or something else? Zip can be browsed in place; tar has to be downloaded and unpacked first (2.5).
3. **On the Pi:** the backup's path, and whether it's on the SD card or a USB disk. This only affects speed expectations.

## Backlog (after both milestones)
- **iMazing data:** `iMazing.Versions` snapshots, and contact photos (`AddressBookImages.sqlitedb`).
- **Safari:** bookmarks and open tabs.
- **Photos:** Live Photo pairing (play the `.MOV` half), People/faces albums, Memories. (Albums and folders moved into Milestone 1, section 1.9.)
- **Export formats:** PDF/Excel export.
- **Packaging:** a `minimazing` entry point on PATH, a `.app` wrapper.
