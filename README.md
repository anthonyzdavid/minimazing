# mazingClone

| Folder / file | What it is |
|---|---|
| `minimazing_app/` | **The app.** iMazing-style iOS backup reader (CLI + local web UI). Start with its `README.md`; the plan for resuming work is `minimazing_app/PLAN.md`. |
| `backups/<UDID>/` | Real encrypted iPhone 12 Pro backup (iOS 17.1.2, Feb 2024, 62 GB). Read-only input, so never write into it. |
| `envtest` | The backup password in plain text. Keep it out of git and out of any upload. |
| `reference/iMazing.app` | The original app, kept for reverse-engineering reference (it was analyzed with the REA tools in `../rea`). |
| `archive/` | Earlier versions: `minimazing_v1/` (+ tarball), and `minimazing_app_before_2026-10-17.tar.gz`, a snapshot taken before the 2026-10-17 work session. Safe to delete once no longer needed. |

Launch the web UI on the real backup (`<UDID>` is the backup's folder name inside `backups/`):

```bash
cd minimazing_app
.venv/bin/python -m minimazing web ../backups/<UDID> --password-file ../envtest
```
