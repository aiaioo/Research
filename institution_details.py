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
FIELDNAMES   = ["institution", "canonical_name", "institution_url", "country"]

MIN_DELAY = 20   # seconds
MAX_DELAY = 60   # seconds


# ── helpers ──────────────────────────────────────────────────────────────────

def load_institutions() -> list[str]:
    """Load from ALL frequency TSVs sorted by filename, deduplicated in first-seen order."""
    files = sorted(PEOPLE_DIR.glob("INSTITUTIONS_FREQUENCY_*.tsv"))
    if not files:
        return []
    seen: set[str] = set()
    institutions: list[str] = []
    for path in files:
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                inst = row.get("institution", "").strip()
                if inst and inst not in seen:
                    seen.add(inst)
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
                                extrasaction="ignore", restval="")
        writer.writeheader()
        for row in details.values():
            writer.writerow(row)


# TLD → country fallback (used when infobox has no country data)
_TLD_COUNTRY: dict[str, str] = {
    "ac.uk": "United Kingdom", "co.uk": "United Kingdom", "uk": "United Kingdom",
    "edu": "United States", "gov": "United States", "us": "United States",
    "ca": "Canada", "au": "Australia", "nz": "New Zealand",
    "de": "Germany", "fr": "France", "it": "Italy", "es": "Spain",
    "nl": "Netherlands", "be": "Belgium", "ch": "Switzerland", "at": "Austria",
    "se": "Sweden", "no": "Norway", "dk": "Denmark", "fi": "Finland",
    "pl": "Poland", "cz": "Czech Republic", "hu": "Hungary", "ro": "Romania",
    "pt": "Portugal", "gr": "Greece", "ru": "Russia", "ua": "Ukraine",
    "jp": "Japan", "cn": "China", "kr": "South Korea", "in": "India",
    "sg": "Singapore", "hk": "Hong Kong", "tw": "Taiwan", "my": "Malaysia",
    "br": "Brazil", "mx": "Mexico", "ar": "Argentina", "cl": "Chile",
    "za": "South Africa", "eg": "Egypt", "ng": "Nigeria", "ke": "Kenya",
    "il": "Israel", "sa": "Saudi Arabia", "ae": "United Arab Emirates",
    "tr": "Turkey", "ir": "Iran", "pk": "Pakistan", "bd": "Bangladesh",
}

_COUNTRY_KEYWORDS = {"country", "nation", "location", "headquarters", "based in"}


async def _extract_country(page, institution_url: str) -> str:
    """Try DDG infobox rows first; fall back to URL TLD."""
    country = ""

    # DDG infobox: key-value table rows
    for sel in [
        "[data-testid='about-result'] tr",
        ".zci__result tr",
        ".ia-modules tr",
        ".c-base__title",            # sometimes wraps entity cards
    ]:
        rows = await page.query_selector_all(sel)
        for row in rows:
            text = (await row.inner_text()).lower()
            if any(kw in text for kw in _COUNTRY_KEYWORDS):
                cells = await row.query_selector_all("td")
                if len(cells) >= 2:
                    val = (await cells[-1].inner_text()).strip()
                    if val:
                        country = val
                        break
        if country:
            break

    # Fallback: infer from URL TLD
    if not country and institution_url:
        from urllib.parse import urlparse
        host = urlparse(institution_url).netloc.lower().lstrip("www.")
        # Check two-part TLDs first (e.g. ac.uk)
        parts = host.split(".")
        two = ".".join(parts[-2:]) if len(parts) >= 2 else ""
        one = parts[-1] if parts else ""
        country = _TLD_COUNTRY.get(two) or _TLD_COUNTRY.get(one, "")

    return country


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

    # ── 3. Country from infobox ───────────────────────────────────────────────
    country = await _extract_country(page, institution_url)

    return {
        "institution":     name,
        "canonical_name":  canonical_name,
        "institution_url": institution_url,
        "country":         country,
    }


# ── main ──────────────────────────────────────────────────────────────────────

async def main():
    institutions = load_institutions()
    if not institutions:
        print("No institutions found in INSTITUTIONS_FREQUENCY_*.tsv")
        return

    details = load_details()
    total   = len(institutions)

    # Resume from the position after the last institution already in details,
    # so previously-written entries are never re-fetched or reordered.
    resume_idx = 0
    for i, inst in enumerate(institutions):
        if inst in details:
            resume_idx = i + 1

    # Within the remaining slice, skip any that were somehow already processed.
    todo       = [inst for inst in institutions[resume_idx:] if inst not in details]
    done_count = len(details)

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
                print(f"  country   : {result['country'] or '(not found)'}")
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
