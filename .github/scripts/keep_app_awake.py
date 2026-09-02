"""Visit the deployed Streamlit app in a real (headless) browser so it never
crosses Streamlit Community Cloud's 12-hour inactivity sleep threshold.

Two things make a naive "just curl the URL" approach not work here:

1. A plain HTTP GET (curl, UptimeRobot, cron-job.org) returns the static
   HTML shell but never runs the JavaScript that opens the WebSocket to
   /_stcore/stream -- and that WebSocket connection is what Community Cloud
   actually counts as activity, and what starts the Python process. Only a
   real (or headless) browser load establishes it.
2. Community Cloud serves the app itself inside a nested iframe
   (URL ending in "/~/+/") within an outer wrapper page (the one with the
   "Fork"/GitHub icons and "Hosted with Streamlit" footer). Checking the
   top-level page's text/selectors only ever sees the wrapper chrome, never
   the app content -- you have to target the inner frame specifically.
"""

from playwright.sync_api import sync_playwright

URL = "https://dataforensics.streamlit.app/"


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        # Streamlit keeps a WebSocket open for the app's whole session, so
        # "networkidle" can stall waiting for a quiet moment that never
        # comes -- "load" (the page's own load event) fires promptly here.
        page.goto(URL, wait_until="load", timeout=60_000)

        wake_button = page.get_by_text("get this app back up", exact=False)
        if wake_button.count() > 0:
            print("App was asleep -- clicking wake-up button")
            wake_button.first.click()
            page.wait_for_timeout(15_000)

        # The app frame attaches a moment after the wrapper page's load
        # event; poll for it rather than assuming it's already there.
        app_frame = None
        for _ in range(20):
            app_frame = next((f for f in page.frames if "/~/+/" in f.url), None)
            if app_frame is not None:
                break
            page.wait_for_timeout(1_000)
        if app_frame is None:
            raise RuntimeError("App iframe never attached -- page structure may have changed.")

        app_frame.wait_for_selector("text=DataForensics", timeout=30_000)
        print("App is awake and rendering.")
        browser.close()


if __name__ == "__main__":
    main()
