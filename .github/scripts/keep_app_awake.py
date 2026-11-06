"""Visits the deployed Streamlit app with a real headless browser so it
never crosses Streamlit Community Cloud's 12-hour inactivity sleep
threshold.

A plain HTTP request (curl, requests.get) does not work for this: the
page at the app URL is only a shell, and the app itself runs in an
embedded frame that opens a WebSocket to the server. Streamlit's idle
timer is reset by that connection, not by the page load. So this script
waits until the app's own content has rendered inside the frame, then
stays connected for a while before closing.

If the app is asleep, the shell shows a "Yes, get this app back up!"
button instead (data-testid="wakeup-button-viewer", the selector
Streamlit Cloud's own frontend uses; a text match is the fallback). The
script clicks it and waits for the app to come back.

The script exits non-zero when it can confirm neither state, so a broken
check shows up as a failed run instead of a misleading "awake".
"""
import sys
from contextlib import suppress

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

APP_URL = "https://dataforensics.streamlit.app"
APP_TEXT = "Automated Data Cleaning Tool"  # rendered by the app's home page
WAKE_BUTTON = '[data-testid="wakeup-button-viewer"], button:has-text("get this app back up")'
LOAD_TIMEOUT_MS = 90_000
STAY_CONNECTED_MS = 45_000


def app_rendered(page) -> bool:
    """True once the app's own content is visible inside its frame."""
    for frame in page.frames:
        # A frame can detach mid-check while the app (re)loads; treat that as "not yet".
        with suppress(PlaywrightError):
            if frame.get_by_text(APP_TEXT, exact=False).first.is_visible():
                return True
    return False


def wait_for_state(page, timeout_ms: int) -> str:
    """Poll until the app has rendered ("awake") or the wake button shows ("asleep")."""
    waited = 0
    while waited < timeout_ms:
        if page.locator(WAKE_BUTTON).count() > 0:
            return "asleep"
        if app_rendered(page):
            return "awake"
        page.wait_for_timeout(2_000)
        waited += 2_000
    return "unknown"


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        try:
            page.goto(APP_URL, timeout=LOAD_TIMEOUT_MS)
        except PlaywrightTimeout:
            print("Timed out loading the app URL.")
            return 1

        state = wait_for_state(page, LOAD_TIMEOUT_MS)
        if state == "asleep":
            print("App was asleep -- clicking the wake-up button.")
            page.locator(WAKE_BUTTON).first.click()
            # Waking can take a few minutes while the container restarts.
            state = "awake" if wait_for_state(page, 5 * 60_000) == "awake" else "unknown"
            if state == "awake":
                print("App is back up.")

        if state != "awake":
            print("Could not confirm the app is running (no app content, no wake button).")
            browser.close()
            return 1

        print(f"App is awake -- staying connected {STAY_CONNECTED_MS // 1000}s so the visit counts.")
        page.wait_for_timeout(STAY_CONNECTED_MS)
        browser.close()
        return 0


if __name__ == "__main__":
    sys.exit(main())
