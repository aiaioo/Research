"""
institution_details.py

For each institution in INSTITUTIONS_FREQUENCY_*.tsv, searches Google for
the institution's canonical name and official website URL.
Skips institutions already recorded in INSTITUTION_DETAILS.tsv.
Writes the TSV after each lookup.

Rate limiting: random delay of 20–60 s between Google requests.

Usage:
    python institution_details.py
"""

import asyncio
import csv
import random
import sys
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

PEOPLE_DIR   = Path(__file__).parent / "people"
DETAILS_FILE = PEOPLE_DIR / "INSTITUTION_DETAILS.tsv"
FIELDNAMES   = ["institution", "canonical_name", "institution_url"]

MIN_DELAY = 20   # seconds
MAX_DELAY = 60   # seconds


# ── helpers ──────────────────────────────────────────────────────────────────

def _latest_institutions_tsv() -> Path | None:
    files = sorted(PEOPLE_DIR.glob("INSTITUTIONS_FREQUENCY_*.tsv"))
    return files[-1] if files else None


def load_institutions() -> list[str]:
    path = _latest_institutions_tsv()
    if not path:
        return []
    institutions: list[str] = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            inst = row.get("institution", "").strip()
            if inst:
                institutions.append(inst)
    return institutions


def load_details() -> dict[str, dict]:
    """Return existing details keyed by institution name."""
    if not DETAILS_FILE.exists():
        return {}
    result: dict[str, dict] = {}
    with DETAILS_FILE.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            inst = row.get("institution", "").strip()
            if inst:
                result[inst] = dict(row)
    return result


def write_details(details: dict[str, dict]) -> None:
    with DETAILS_FILE.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, delimiter="\t",
                                extrasaction="ignore")
        writer.writeheader()
        for row in details.values():
            writer.writerow(row)


# ── Google search ─────────────────────────────────────────────────────────────

async def search_institution(page, name: str) -> dict:
    """
    Search DuckDuckGo for `name` and return canonical name + URL.
    Always returns a dict; fields may be empty if nothing useful found.
    """
    empty = {"institution": name, "canonical_name": "", "institution_url": ""}

    query = name.replace(" ", "+")
    url   = f"https://duckduckgo.com/?q={query}&ia=web"

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        # Wait for organic results to render
        await page.wait_for_selector("article[data-testid='result'], .result__title", timeout=10_000)
    except PWTimeout:
        print(f"  [timeout] {name}")
        return empty
    except Exception:
        # wait_for_selector timed out — page may still have partial results
        pass

    canonical_name  = ""
    institution_url = ""

    # ── 1. First result title → canonical name ────────────────────────────────
    for sel in [
        "article[data-testid='result'] h2",
        "h2.result__title",
        ".result__title",
    ]:
        el = await page.query_selector(sel)
        if el:
            text = (await el.inner_text()).strip()
            if text:
                canonical_name = text
                break

    # ── 2. First result URL ───────────────────────────────────────────────────
    # DDG result links sometimes go through duckduckgo.com/l/?uddg=... redirects;
    # the displayed URL span always has the clean domain.
    for sel in [
        # Modern DDG — direct href on title anchor
        "article[data-testid='result'] a[data-testid='result-title-a']",
        # Displayed URL text (e.g. "www.umontreal.ca") — prepend https://
        "article[data-testid='result'] a[data-testid='result-extras-url-link']",
        # Older DDG layout
        "a.result__a",
    ]:
        el = await page.query_selector(sel)
        if el:
            href = (await el.get_attribute("href") or "").strip()
            # Resolve DDG redirect: /l/?uddg=https%3A%2F%2F...
            if "duckduckgo.com/l/" in href:
                from urllib.parse import urlparse, parse_qs, unquote
                qs = parse_qs(urlparse(href).query)
                href = unquote(qs.get("uddg", [""])[0])
            if href.startswith("http") and "duckduckgo" not in href:
                institution_url = href
                break

    return {
        "institution":    name,
        "canonical_name": canonical_name,
        "institution_url": institution_url,
    }


# ── main ──────────────────────────────────────────────────────────────────────

async def main():
    institutions = load_institutions()
    if not institutions:
        print("No institutions found in INSTITUTIONS_FREQUENCY_*.tsv")
        return

    details    = load_details()
    todo       = [inst for inst in institutions if inst not in details]
    done_count = len(details)
    total      = len(institutions)

    print(f"Institutions total : {total}")
    print(f"Already resolved   : {done_count}")
    print(f"Remaining          : {len(todo)}")

    if not todo:
        print("Nothing to do.")
        return

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            locale="en-US",
            viewport={"width": 1280, "height": 800},
        )
        await context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        page = await context.new_page()

        for i, inst in enumerate(todo):
            print(f"\n[{done_count + 1}/{total}] {inst!r}")
            result = await search_institution(page, inst)

            details[inst] = result
            if result["institution_url"]:
                print(f"  canonical : {result['canonical_name']}")
                print(f"  url       : {result['institution_url']}")
            else:
                print(f"  → not found")

            done_count += 1
            write_details(details)

            if i < len(todo) - 1:
                delay = random.uniform(MIN_DELAY, MAX_DELAY)
                print(f"  waiting {delay:.1f} s …")
                await asyncio.sleep(delay)

        await browser.close()

    print(f"\nDone. {done_count} entries written to {DETAILS_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
