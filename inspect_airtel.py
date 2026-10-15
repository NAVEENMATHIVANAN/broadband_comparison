
import os
import re
import sys

import requests
from bs4 import BeautifulSoup


URL = "https://www.airtel.in/plans/broadband"


def inspect_html(html):
    """Inspect static HTML without assuming a particular page layout."""
    soup = BeautifulSoup(html, "html.parser")

    print("\n--- HTML inspection ---")
    print("Page title:", soup.title.get_text(strip=True) if soup.title else "Not found")
    print("HTML tables:", len(soup.find_all("table")))
    print("HTML size:", len(html), "characters")

    text = soup.get_text(" ", strip=True)

    # Print relevant snippets for diagnosis, not as verified plans.
    pattern = re.compile(
        r"broadband|fiber|fibre|Mbps|Gbps|₹|Rs\.?|monthly|plan",
        re.IGNORECASE,
    )

    matches = pattern.finditer(text)
    snippets = []
    for match in matches:
        start = max(0, match.start() - 90)
        end = min(len(text), match.end() + 150)
        snippet = text[start:end]

        if snippet not in snippets:
            snippets.append(snippet)

        if len(snippets) >= 12:
            break

    print("\nRelevant text snippets:")
    if snippets:
        for snippet in snippets:
            print("-", snippet)
    else:
        print("No obvious plan-related text found.")

    return text


def inspect_with_requests():
    print("Checking Airtel with Requests...")

    response = requests.get(
        URL,
        headers={
            "User-Agent": "BroadbandComparisonLearningProject/1.0"
        },
        timeout=25,
    )

    print("HTTP status:", response.status_code)
    print("Final URL:", response.url)
    response.raise_for_status()

    return inspect_html(response.text)


def inspect_with_steel():
    """Inspect the rendered page using a Steel-hosted browser."""
    api_key = os.getenv("STEEL_API_KEY")

    if not api_key:
        raise RuntimeError(
            "STEEL_API_KEY is missing. Set it in PowerShell first."
        )

    from steel import Steel
    from playwright.sync_api import sync_playwright

    client = Steel(steel_api_key=api_key)
    session = None
    browser = None
    playwright = None

    try:
        print("\nStarting Steel browser session...")
        session = client.sessions.create()

        if getattr(session, "session_viewer_url", None):
            print("Browser viewer:", session.session_viewer_url)

        playwright = sync_playwright().start()

        browser = playwright.chromium.connect_over_cdp(
            f"{session.websocket_url}&apiKey={api_key}"
        )

        context = browser.contexts[0]
        pages = context.pages

        page = pages[0] if pages else context.new_page()

        print("Opening Airtel page...")
        page.goto(
            URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        # Allow a short, bounded wait for page rendering.
        page.wait_for_timeout(4000)

        print("Rendered title:", page.title())
        print("Rendered URL:", page.url)

        rendered_text = page.locator("body").inner_text(timeout=15000)

        print("\n--- Rendered page text (first 12000 characters) ---")
        print(rendered_text[:12000])

        return rendered_text

    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass

        if playwright is not None:
            playwright.stop()

        if session is not None:
            client.sessions.release(session.id)
            print("\nSteel session released.")



if __name__ == "__main__":
    use_steel = "--steel" in sys.argv

    try:
        inspect_html(inspect_with_requests())
    except requests.RequestException as error:
        print("Static HTTP inspection failed:", error)

        if not use_steel:
            sys.exit(1)

    if use_steel:
        try:
            inspect_with_steel()
        except Exception as error:
            print(f"Steel inspection failed: {type(error).__name__}: {error}")
            sys.exit(1)
    else:
        print(
            "\nStatic inspection complete. "
            "Run with --steel if browser rendering is required."
        )

