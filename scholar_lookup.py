"""
scholar_lookup.py

For the top N researchers in RESEARCHERS_FREQUENCY.tsv, searches Google Scholar
for their author profile, then extracts: scholar_url, affiliation, citations.
Skips rows that already have a scholar_url. Writes the TSV after each lookup.

Rate limiting: random delay of 15–30 s between Scholar requests.
"""

import asyncio
import csv
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential, retry_if_exception_type
from playwright.async_api import async_playwright, TimeoutError as PWTimeout, Error as PWError

PEOPLE_DIR = Path(__file__).parent / "people"
_YYYYMM = datetime.now().strftime("%Y%m")
TSV_IN = PEOPLE_DIR / f"RESEARCHERS_FREQUENCY_{_YYYYMM}.tsv"

TOP_N = 10460
MIN_DELAY = 15   # seconds
MAX_DELAY = 30   # seconds
FAILED_MARKER = "NOT_FOUND"  # sentinel: lookup was attempted but no profile found

# ── helpers ─────────────────────────────────────────────────────────────────

def load_tsv() -> tuple[list[str], list[dict]]:
    rows = []
    with open(TSV_IN, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        fieldnames = reader.fieldnames or []
        for row in reader:
            rows.append(dict(row))
    return list(fieldnames), rows


def write_tsv(fieldnames: list[str], rows: list[dict]) -> None:
    with open(TSV_IN, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, delimiter="\t",
                                extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def needs_lookup(row: dict) -> bool:
    """True only for rows that have never been attempted (blank scholar_url)."""
    return row.get("scholar_url", "") == ""


def find_resume_index(rows: list[dict]) -> int:
    """Return the index of the row after the last successfully found URL, or 0."""
    for i in range(len(rows) - 1, -1, -1):
        url = rows[i].get("scholar_url", "")
        if url and url != FAILED_MARKER:
            return i + 1
    return 0


def fmt_citations(text: str) -> str:
    """Strip non-numeric characters and return plain integer string."""
    digits = re.sub(r"[^\d]", "", text)
    return digits if digits else ""


# ── Scholar scraping ─────────────────────────────────────────────────────────

SEARCH_URL = (
    "https://scholar.google.com/citations"
    "?view_op=search_authors&mauthors={query}&hl=en"
)


async def search_author(page, name: str) -> dict | None:
    """
    Search Scholar for `name` and return the best-matching profile dict,
    or None if nothing found.
    """
    query = name.replace(" ", "+")
    url = SEARCH_URL.format(query=query)

    try:
        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type((PWTimeout, PWError)),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=2, min=30, max=120),
            reraise=True,
        ):
            with attempt:
                if attempt.retry_state.attempt_number > 1:
                    print(f"  [retry {attempt.retry_state.attempt_number}/3] {name}")
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(2_000)   # let JS settle
    except (PWTimeout, PWError) as exc:
        print(f"  [failed after retries] {name}: {exc}")
        return None

    # Detect CAPTCHA / unusual traffic page
    content = await page.content()
    if "unusual traffic" in content.lower() or "captcha" in content.lower():
        print(f"  [captcha] {name} — pausing 120 s")
        await asyncio.sleep(120)
        # retry once
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(2_000)
            content = await page.content()
        except PWTimeout:
            return None
        if "unusual traffic" in content.lower():
            print("  [captcha again] giving up on this author")
            return None

    # Each profile card: div.gs_ai_chpr
    cards = await page.query_selector_all("div.gs_ai_chpr")
    if not cards:
        # Try alternative selector
        cards = await page.query_selector_all(".gsc_1usr")

    if not cards:
        return None

    # Score each card by name similarity; pick the best match
    name_parts = name.lower().split()
    best_score = -1
    best_result = None

    for card in cards:
        try:
            # Name / link
            link_el = await card.query_selector("h3.gs_ai_name a, a.gs_ai_name")
            if not link_el:
                link_el = await card.query_selector("a")
            if not link_el:
                continue

            card_name = (await link_el.inner_text()).strip()
            href = await link_el.get_attribute("href") or ""

            # Affiliation
            aff_el = await card.query_selector(".gs_ai_aff, .gsc_1usr_aff")
            affiliation = (await aff_el.inner_text()).strip() if aff_el else ""

            # Citations (shows "Cited by N")
            cit_el = await card.query_selector(".gs_ai_cby, .gsc_1usr_cby")
            citations_raw = (await cit_el.inner_text()).strip() if cit_el else ""
            citations = fmt_citations(citations_raw)

            # Build absolute Scholar URL
            if href.startswith("/"):
                scholar_url = "https://scholar.google.com" + href
            elif href.startswith("http"):
                scholar_url = href
            else:
                continue

            # Keep only citations URLs that have a user= parameter
            if "citations?" not in scholar_url or "user=" not in scholar_url:
                continue

            # Simple match score: how many name tokens appear in card_name
            card_lower = card_name.lower()
            score = sum(1 for part in name_parts if part in card_lower)

            if score > best_score:
                best_score = score
                best_result = {
                    "scholar_name": card_name,
                    "scholar_url": scholar_url,
                    "affiliation": affiliation,
                    "citations": citations,
                }
        except Exception:
            continue

    # Require at least last-name match
    if best_score >= 1 and best_result:
        return best_result
    return None


# ── main ─────────────────────────────────────────────────────────────────────

async def main():
    fieldnames, rows = load_tsv()

    # Ensure new columns exist
    new_cols = ["scholar_url", "affiliation", "citations"]
    for col in new_cols:
        if col not in fieldnames:
            fieldnames.append(col)
            for row in rows:
                row.setdefault(col, "")

    top_rows = rows[:TOP_N]

    start_idx = find_resume_index(top_rows)
    todo = [r for r in top_rows[start_idx:] if needs_lookup(r)]
    already_attempted = start_idx + sum(1 for r in top_rows[start_idx:] if not needs_lookup(r))
    print(f"Resuming from row {start_idx + 1}: {already_attempted} already processed, {len(todo)} remaining")

    async with async_playwright() as pw:
        # Use the real system Chrome (not the "Google Chrome for Testing" binary)
        # so no "Test" badge appears and the user can stay logged into Google.
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

        lookup_count = 0
        for i, row in enumerate(top_rows[start_idx:], start=start_idx):
            if not needs_lookup(row):
                continue

            name = row["author"]
            print(f"[row {i + 1}/{TOP_N}, lookup #{lookup_count + 1}/{len(todo)}] Searching: {name}")
            result = await search_author(page, name)

            if result:
                row["scholar_url"] = result["scholar_url"]
                row["affiliation"] = result["affiliation"]
                row["citations"] = result["citations"]
                print(f"  → {result['scholar_url']}")
                print(f"     {result['affiliation']} | cited {result['citations']}")
            else:
                row["scholar_url"] = FAILED_MARKER
                row["affiliation"] = ""
                row["citations"] = ""
                print(f"  → not found")

            lookup_count += 1
            write_tsv(fieldnames, rows)

            # Rate-limit: skip delay after the very last entry
            remaining = [r for r in top_rows[i + 1:] if needs_lookup(r)]
            if remaining:
                delay = random.uniform(MIN_DELAY, MAX_DELAY)
                print(f"  waiting {delay:.1f} s …")
                await asyncio.sleep(delay)

        await browser.close()

    print(f"\nDone. Results written to {TSV_IN}")


if __name__ == "__main__":
    asyncio.run(main())
