
import os
import sys

from browserbase import Browserbase
from playwright.sync_api import sync_playwright


URL = "https://www.airtel.in/plans/broadband"


def main():
    api_key = os.getenv("BROWSERBASE_API_KEY")
    project_id = os.getenv("BROWSERBASE_PROJECT_ID")

    if not api_key or not project_id:
        raise RuntimeError(
            "Set BROWSERBASE_API_KEY and "
            "BROWSERBASE_PROJECT_ID in PowerShell."
        )

    client = Browserbase(api_key=api_key)
    browser = None

    try:
        session = client.sessions.create(
            project_id=project_id,
        )

        print("Browserbase session created:", session.id)

        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(
                session.connect_url
            )

            if not browser.contexts:
                raise RuntimeError(
                    "Browserbase returned no browser context."
                )

            context = browser.contexts[0]
            page = (
                context.pages[0]
                if context.pages
                else context.new_page()
            )

            print("Opening:", URL)

            response = page.goto(
                URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            print(
                "HTTP status:",
                response.status if response else "Unavailable",
            )
            print("Page title:", page.title())
            print("Final URL:", page.url)

            # Diagnostic only: do not save these values to SQLite.
            page.wait_for_timeout(3000)
            text = page.locator("body").inner_text(timeout=15000)

            print("\n--- Rendered page text ---")
            print(text[:12000])

            print("\n--- Candidate price and speed text ---")
            for line in text.splitlines():
                line = line.strip()

                if (
                    line
                    and any(symbol in line for symbol in ("₹", "Rs.", "Rs "))
                    and any(
                        unit in line.lower()
                        for unit in ("mbps", "gbps")
                    )
                ):
                    print(line)

    finally:
        # Disconnect and release the browser resources.
        if browser is not None:
            try:
                browser.close()
            except Exception as error:
                print("Browser cleanup warning:", error)

        client.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Browserbase inspection failed: {error}")
        sys.exit(1)
