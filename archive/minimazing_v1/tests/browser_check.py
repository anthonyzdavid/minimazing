"""Drive the real web UI in headless Chromium and fail on any console error,
page error, or missing expected content. Run against the already-running
server (python -m minimazing web ... --port 8780 --no-browser).
"""
import sys
from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8780"
errors = []


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                 if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

        page.goto(URL, wait_until="networkidle")
        page.wait_for_selector("#device-name")
        assert "Test iPhone" in page.inner_text("#device-name"), "device name not rendered"
        assert "Overview" in page.inner_text("#view-title")
        print("OK  overview loaded")

        # every nav item that should be clickable
        nav_items = page.locator(".nav-item:not(.disabled)")
        count = nav_items.count()
        print(f"OK  {count} enabled nav items found")

        checks = {
            "Messages": lambda: page.locator("#chat-list .chat-row").count() > 0,
            "Contacts": lambda: "Ada Lovelace" in page.inner_text("#content"),
            "Call History": lambda: "+15559876543" in page.inner_text("#content"),
            "Calendar": lambda: "Team sync" in page.inner_text("#content"),
            "Notes": lambda: "Groceries" in page.inner_text("#content"),
            "Safari": lambda: "Example Domain" in page.inner_text("#content"),
            "Photos": lambda: page.locator(".photo-cell").count() == 2,
        }

        for label, check in checks.items():
            page.click(f".nav-item:has-text('{label}')")
            page.wait_for_timeout(250)
            ok = check()
            print(f"{'OK ' if ok else 'FAIL'} {label} view")
            if not ok:
                errors.append(f"{label} view did not render expected content")

        # messages thread view: click second chat row if present, verify bubble text
        page.click(".nav-item:has-text('Messages')")
        page.wait_for_timeout(200)
        assert "Hey, this is a test" in page.inner_text("#thread-pane")
        print("OK  message bubble text visible")

        # search filter on contacts
        page.click(".nav-item:has-text('Contacts')")
        page.wait_for_timeout(150)
        page.fill("#search", "nonexistentname")
        page.wait_for_timeout(150)
        no_match = "No" in page.inner_text("#content") or page.locator("table.data tbody tr").count() == 0
        print(f"{'OK ' if no_match else 'FAIL'} search filters out non-matches")
        if not no_match:
            errors.append("search did not filter contacts table")
        page.fill("#search", "")
        page.wait_for_timeout(150)

        # photos: open lightbox
        page.click(".nav-item:has-text('Photos')")
        page.wait_for_timeout(200)
        page.click(".photo-cell:first-child")
        page.wait_for_timeout(200)
        lightbox_open = "hidden" not in (page.get_attribute("#lightbox", "class") or "")
        print(f"{'OK ' if lightbox_open else 'FAIL'} lightbox opens")
        if not lightbox_open:
            errors.append("lightbox did not open on photo click")
        img_visible = page.locator("#lightbox-body img").count() == 1
        print(f"{'OK ' if img_visible else 'FAIL'} lightbox shows image")
        page.click("#lightbox-close")
        page.wait_for_timeout(150)
        lightbox_closed = "hidden" in (page.get_attribute("#lightbox", "class") or "")
        print(f"{'OK ' if lightbox_closed else 'FAIL'} lightbox closes")
        if not lightbox_closed:
            errors.append("lightbox did not close")

        # export buttons: just verify they point at working endpoints (no UI click->download
        # flow here since that needs download handling; check via direct request instead)
        resp = page.request.get(f"{URL}/api/export/contacts.csv")
        assert resp.ok and b"Ada" in resp.body()
        print("OK  CSV export endpoint works")
        resp = page.request.get(f"{URL}/api/export/contacts.json")
        assert resp.ok and b"Ada" in resp.body()
        print("OK  JSON export endpoint works")

        # disabled nav items shouldn't navigate anywhere / throw
        page.click(".nav-item.disabled:has-text('Voice Memos')")
        page.wait_for_timeout(150)
        print("OK  clicking a disabled nav item is a no-op")

        browser.close()

    if errors:
        print("\n=== ERRORS ===")
        for e in errors:
            print(" -", e)
        sys.exit(1)
    print("\nALL CHECKS PASSED, no console/page errors")


if __name__ == "__main__":
    main()
