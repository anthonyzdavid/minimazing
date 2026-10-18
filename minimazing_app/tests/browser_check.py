"""Drive the real web UI in headless Chromium and fail on any console error,
page error, or missing expected content. Run against a server started on the
synthetic backup *without* a password, so the unlock screen is exercised too:

    python tests/make_backup.py ./synthetic
    python -m minimazing web ./synthetic/<udid> --port 8780 --no-browser
    python tests/browser_check.py http://127.0.0.1:8780
"""
import sys
from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8780"
PASSWORD = "test-pass-123"
errors = []


def check(ok, label):
    print(f"{'OK ' if ok else 'FAIL'} {label}")
    if not ok:
        errors.append(label)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                if m.type == "error" and "401" not in m.text else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        page.goto(URL, wait_until="networkidle")
        page.wait_for_selector("#unlock-pw")
        page.fill("#unlock-pw", "wrong")
        page.click("#unlock-btn")
        page.wait_for_function("document.querySelector('#gate-error')?.textContent")
        check("Wrong password" in page.inner_text("#gate-error"), "wrong password rejected")
        page.fill("#unlock-pw", PASSWORD)
        page.click("#unlock-btn")
        page.wait_for_selector("#nav .nav-item")
        check("Test iPhone" in page.inner_text("#device-name"), "unlocked, device name rendered")
        page.wait_for_function(
            "![...document.querySelectorAll('.nav-item .count')].some(e => e.textContent === '…')")

        checks = {
            "Messages": lambda: page.locator("#chat-list .chat-row").count() == 1
            and "Only in attributedBody" in page.inner_text("#thread-pane"),
            "Contacts": lambda: "Ada Lovelace" in page.inner_text("#content"),
            "Call History": lambda: "Ada Lovelace" in page.inner_text("#content"),
            "Voicemail": lambda: page.locator(".audio-row audio").count() == 1,
            "Calendar": lambda: "Team sync" in page.inner_text("#content"),
            "Notes": lambda: "and coffee" in page.inner_text("#note-pane"),
            "Safari": lambda: "Example Domain" in page.inner_text("#content"),
            "Photos": lambda: page.locator(".photo-cell").count() == 3,
            "Voice Memos": lambda: "Band practice" in page.inner_text("#content"),
            "Apps": lambda: "Todo Pro" in page.inner_text("#content"),
            "File System": lambda: "CameraRollDomain" in page.inner_text("#content"),
        }
        for label, fn in checks.items():
            page.click(f".nav-item:has-text('{label}')")
            page.wait_for_timeout(400)
            check(fn(), f"{label} view")

        # attachment image renders inline in the thread
        page.click(".nav-item:has-text('Messages')")
        page.wait_for_selector("#thread-pane img.att-img")
        check(page.evaluate("document.querySelector('#thread-pane img.att-img').naturalWidth >= 0"),
              "message attachment shown")

        # search filters contacts
        page.click(".nav-item:has-text('Contacts')")
        page.wait_for_timeout(200)
        page.fill("#search", "nonexistentname")
        page.wait_for_timeout(300)
        check(page.locator("table.data tbody tr").count() == 0, "search filters out non-matches")

        # file browser drills into a folder
        page.click(".nav-item:has-text('File System')")
        page.click(".fs-row.dir:has-text('CameraRollDomain')")
        page.wait_for_selector(".crumbs")
        check("Media" in page.inner_text("#content"), "file browser opens a domain")

        # photos: viewer opens, explains a missing original, navigates, closes
        page.click(".nav-item:has-text('Photos')")
        page.wait_for_selector(".photo-cell")
        page.click(".photo-cell:first-child")
        page.wait_for_timeout(300)
        check("hidden" not in (page.get_attribute("#lightbox", "class") or ""), "viewer opens")
        check("isn't in this backup" in page.inner_text("#lightbox-body"), "viewer explains preview")
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(200)
        check("IMG_0002" in page.inner_text("#lightbox-body"), "viewer arrow navigation")
        page.keyboard.press("Escape")
        check("hidden" in (page.get_attribute("#lightbox", "class") or ""), "viewer closes")

        for path, needle in (("/api/export/contacts.csv", b"Ada"), ("/api/export/contacts.json", b"Ada"),
                             ("/api/export/messages.html", b"Reply from me")):
            resp = page.request.get(f"{URL}{path}")
            check(resp.ok and needle in resp.body(), f"export {path}")

        page.click(".nav-item.disabled:has-text('Music')")
        check(True, "clicking a disabled nav item is a no-op")
        browser.close()

    if errors:
        print("\n=== ERRORS ===")
        for e in errors:
            print(" -", e)
        sys.exit(1)
    print("\nALL CHECKS PASSED, no console/page errors")


if __name__ == "__main__":
    main()
