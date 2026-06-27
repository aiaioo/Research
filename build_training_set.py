#!/usr/bin/env python3
"""
build_training_set.py

Scrapes paper lists from GitHub repos and Google Scholar author profiles,
then writes a category-labelled training-set TSV in the same format as
seen_papers_YYYYMM.tsv.

Sources are read from:   papers/<category>_paper_sources.txt
Output is written to:    papers/<category>_papers_YYYYMM.tsv

Usage:
    python build_training_set.py <category>

Example:
    python build_training_set.py memory
    python build_training_set.py safety

Source-file format (one entry per line, # lines are comments):
    <name>  <url>
Name is optional; if omitted the repo slug or Scholar user-id is used.
Supported URL types:
    https://github.com/<owner>/<repo>
    https://scholar.google.com/citations?user=<id>&...
    https://www.semanticscholar.org/author/<id>
    https://export.arxiv.org/api/query?search_query=<query>&max_results=<n>
"""

import argparse
import csv
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from paper_searcher import (
    SESSION,
    SEEN_FIELDS,
    ARXIV_RE,
    OPENREVIEW_RE,
    ACL_RE,
    extract_papers,
    openreview_metadata,
    _citation_meta,
    _acl_venue,
    arxiv_canonical,
)

ROOT      = Path(__file__).parent
PAPERS_DIR = ROOT / "papers"
TODAY     = datetime.now().strftime("%Y%m%d")
MONTH     = datetime.now().strftime("%Y%m")

ARXIV_BATCH_SIZE       = 50
ARXIV_BATCH_DELAY      = 10    # seconds between arXiv batch calls
S2_BATCH_SIZE          = 500
SCHOLAR_PROFILE_DELAY  = 0.8   # seconds between profile-page fetches
SCHOLAR_CITATION_DELAY = 2.5   # seconds between individual citation pages (more rate-limited)
SCHOLAR_429_WAIT       = 20    # seconds to wait after a 429 before one retry

SCHOLAR_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# ── Sources ────────────────────────────────────────────────────────────────────

def load_sources(category: str) -> list[tuple[str, str]]:
    path = PAPERS_DIR / f"{category}_paper_sources.txt"
    if not path.exists():
        print(f"[!] Sources file not found: {path}", file=sys.stderr)
        sys.exit(1)
    sources = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) == 1:
            url  = parts[0]
            name = url.rstrip("/").split("/")[-1]
        else:
            name = parts[0]
            url  = parts[1]
        sources.append((name, url))
    return sources

# ── GitHub scraper ─────────────────────────────────────────────────────────────

def _fetch_raw(repo: str, path: str) -> str:
    for branch in ("main", "master"):
        try:
            r = SESSION.get(
                f"https://raw.githubusercontent.com/{repo}/{branch}/{path}",
                timeout=15,
            )
            if r.status_code == 200:
                return r.text
        except Exception:
            pass
    return ""


_INLINE_ARXIV = re.compile(r"(?<![/\w])arXiv[:\s]+(\d{4}\.\d{4,5}(?:v\d+)?)", re.I)

def _extract_from_md(text: str) -> list[dict]:
    """Extract paper records, normalising doi.org shortlinks and bare arXiv:XXXX.NNNNN refs."""
    doi_arxiv = re.compile(
        r"https?://doi\.org/10\.48550/arXiv\.(\d{4}\.\d{4,5}(?:v\d+)?)"
    )
    text = doi_arxiv.sub(lambda m: arxiv_canonical(m.group(1)), text)
    text = _INLINE_ARXIV.sub(lambda m: arxiv_canonical(m.group(1)), text)
    return extract_papers(text)


def scrape_github(name: str, url: str) -> list[dict]:
    # Handle blob URLs pointing to a specific file (e.g. /blob/main/Paper_List/foo.md)
    blob_m = re.match(
        r"https://github\.com/([^/?#]+/[^/?#]+)/blob/([^/]+)/(.+)", url
    )
    if blob_m:
        repo, branch, file_path = blob_m.group(1), blob_m.group(2), blob_m.group(3)
        raw_url = f"https://raw.githubusercontent.com/{repo}/{branch}/{file_path}"
        try:
            r = SESSION.get(raw_url, timeout=15)
            content = r.text if r.status_code == 200 else ""
        except Exception:
            content = ""
        if not content:
            print(f"  [!] Could not fetch {raw_url}", file=sys.stderr)
            return []
        papers = _extract_from_md(content)
        for p in papers:
            p.setdefault("source_name", name)
            p.setdefault("source_url",  url)
        print(f"  {name}: {len(papers)} paper URLs extracted from {file_path}", flush=True)
        return papers

    m = re.match(r"https://github\.com/([^/?#]+/[^/?#]+)", url)
    if not m:
        print(f"  [!] Cannot parse GitHub URL: {url}", file=sys.stderr)
        return []
    repo = m.group(1)

    readme = _fetch_raw(repo, "README.md")
    if not readme:
        print(f"  [!] Could not fetch README for {repo}", file=sys.stderr)
        return []

    papers = _extract_from_md(readme)

    fetched = {"README.md"}
    for link in re.findall(r"\[.*?\]\(([^)]+\.md[^)]*)\)", readme)[:3]:
        link = link.split("#")[0].strip()
        if link.startswith("http") or link in fetched:
            continue
        fetched.add(link)
        content = _fetch_raw(repo, link)
        if content:
            papers.extend(_extract_from_md(content))
            time.sleep(0.3)

    for p in papers:
        p.setdefault("source_name", name)
        p.setdefault("source_url",  url)

    print(f"  {name}: {len(papers)} paper URLs extracted", flush=True)
    return papers

# ── Google Scholar scraper ─────────────────────────────────────────────────────

_SCHOLAR_ARXIV = re.compile(r"[Aa]r[Xx]iv[:\s]+(\d{4}\.\d{4,5})", re.I)
_SCHOLAR_HEADERS = {"User-Agent": SCHOLAR_UA}


def _scholar_get(url: str, *, retry: bool = True) -> "requests.Response | None":
    try:
        r = SESSION.get(url, headers=_SCHOLAR_HEADERS, timeout=15)
        if r.status_code == 200:
            return r
        if r.status_code == 429 and retry:
            print(f"    [!] Scholar 429 — sleeping {SCHOLAR_429_WAIT}s then retrying",
                  file=sys.stderr)
            time.sleep(SCHOLAR_429_WAIT)
            return _scholar_get(url, retry=False)
        print(f"    [!] Scholar returned {r.status_code} for {url}", file=sys.stderr)
    except Exception as e:
        print(f"    [!] Scholar fetch error: {e}", file=sys.stderr)
    return None


def _paper_url_from_citation_page(href: str) -> str:
    """Follow a Scholar /citations?view_op=view_citation... page and return the best paper URL."""
    full = "https://scholar.google.com" + href if href.startswith("/") else href
    r = _scholar_get(full)
    if not r:
        return ""
    time.sleep(SCHOLAR_CITATION_DELAY)

    # Use extract_papers() to catch arXiv, OpenReview, ACL, PMLR URLs
    found = extract_papers(r.text)
    if found:
        return found[0]["paper_url"]

    # Fallback: return first external non-Google link on the page
    soup = BeautifulSoup(r.text, "html.parser")
    for a in soup.find_all("a", href=True):
        href2 = a["href"]
        if href2.startswith("http") and "google" not in href2:
            return href2
    return ""


def scrape_scholar(name: str, url: str) -> list[dict]:
    """
    Scrape an author's Google Scholar profile.

    Strategy:
    1. Fetch all paginated rows (title, abbreviated authors, venue text, citation href).
    2. Extract inline arXiv IDs from venue text where present.
    3. For remaining papers, follow individual citation pages to find the paper URL.
    4. Return paper dicts; metadata enrichment happens later via S2/arXiv.
    """
    m = re.search(r"user=([\w-]+)", url)
    if not m:
        print(f"  [!] Cannot parse Scholar user ID from: {url}", file=sys.stderr)
        return []
    user_id = m.group(1)

    # --- Step 1: fetch all paginated profile rows ---
    raw_rows = []
    for cstart in range(0, 5000, 100):
        page_url = (
            f"https://scholar.google.com/citations"
            f"?user={user_id}&hl=en&cstart={cstart}&pagesize=100"
        )
        r = _scholar_get(page_url)
        if not r:
            break
        soup  = BeautifulSoup(r.text, "html.parser")
        batch = soup.select("tr.gsc_a_tr")
        raw_rows.extend(batch)
        if len(batch) < 100:
            break
        time.sleep(SCHOLAR_PROFILE_DELAY)

    print(f"  {name}: {len(raw_rows)} papers on Scholar profile", flush=True)

    # --- Step 2: parse each row ---
    papers = []
    needs_followup: list[tuple[int, str]] = []  # (index, citation_href)

    for row in raw_rows:
        title_a = row.select_one("a.gsc_a_at")
        if not title_a:
            continue
        title         = title_a.get_text(strip=True)
        citation_href = title_a.get("href", "")

        divs    = row.select("div.gs_gray")
        authors = divs[0].get_text(strip=True) if divs else ""
        venue   = divs[1].get_text(strip=True) if len(divs) > 1 else ""

        year_el  = row.select_one("span.gsc_a_h")
        pub_year = year_el.get_text(strip=True) if year_el else ""
        pub_date = f"{pub_year}-01-01" if pub_year and pub_year.isdigit() else ""

        # Try to get arXiv ID from the inline venue text
        m_ax = _SCHOLAR_ARXIV.search(venue)
        if m_ax:
            paper_url = arxiv_canonical(m_ax.group(1))
        else:
            paper_url = ""

        idx = len(papers)
        papers.append({
            "paper_url":   paper_url,
            "title":       title,
            "authors":     authors,   # abbreviated; enrichment fills in full list
            "pub_date":    pub_date,
            "source_name": name,
            "source_url":  url,
        })
        if not paper_url and citation_href:
            needs_followup.append((idx, citation_href))

    # --- Step 3: follow citation pages for papers without inline arXiv IDs ---
    if needs_followup:
        print(f"  {name}: following {len(needs_followup)} citation pages …", flush=True)
        for idx, href in needs_followup:
            found_url = _paper_url_from_citation_page(href)
            if found_url:
                papers[idx]["paper_url"] = found_url
            else:
                # Use the Scholar citation page itself as the URL so the row isn't lost
                papers[idx]["paper_url"] = (
                    "https://scholar.google.com" + href
                    if href.startswith("/") else href
                )

    # Drop rows with no usable URL
    papers = [p for p in papers if p.get("paper_url")]
    print(f"  {name}: {len(papers)} papers with URLs", flush=True)
    return papers

# ── Semantic Scholar author scraper ───────────────────────────────────────────

def scrape_s2_author(name: str, url: str) -> list[dict]:
    """
    Semantic Scholar author page (semanticscholar.org/author/<id>).
    Fetches all papers for the author via the S2 graph API.
    """
    m = re.search(r"/author/(\d+)", url)
    if not m:
        print(f"  [!] Cannot parse S2 author ID from: {url}", file=sys.stderr)
        return []
    author_id = m.group(1)

    try:
        r = SESSION.get(
            f"https://api.semanticscholar.org/graph/v1/author/{author_id}/papers",
            params={"fields": "title,authors,abstract,publicationDate,venue,externalIds",
                    "limit": 1000},
            timeout=30,
        )
        if r.status_code == 429:
            print("    [!] S2 author API 429 — sleeping 30s then retrying", file=sys.stderr)
            time.sleep(30)
            r = SESSION.get(
                f"https://api.semanticscholar.org/graph/v1/author/{author_id}/papers",
                params={"fields": "title,authors,abstract,publicationDate,venue,externalIds",
                        "limit": 1000},
                timeout=30,
            )
        r.raise_for_status()
    except Exception as e:
        print(f"  [!] S2 author fetch failed: {e}", file=sys.stderr)
        return []

    papers = []
    for item in r.json().get("data", []):
        ext       = item.get("externalIds") or {}
        arxiv_id  = ext.get("ArXiv", "")
        doi       = ext.get("DOI", "")
        paper_id  = item.get("paperId", "")

        if arxiv_id:
            paper_url = arxiv_canonical(arxiv_id)
        elif doi:
            paper_url = f"https://doi.org/{doi}"
        elif paper_id:
            paper_url = f"https://www.semanticscholar.org/paper/{paper_id}"
        else:
            continue

        papers.append({
            "paper_url":   paper_url,
            "title":       item.get("title")    or "",
            "authors":     ", ".join(a.get("name", "") for a in (item.get("authors") or [])),
            "abstract":    item.get("abstract") or "",
            "pub_date":    (item.get("publicationDate") or "")[:10],
            "place":       item.get("venue")    or "",
            "source_name": name,
            "source_url":  url,
        })

    print(f"  {name}: {len(papers)} papers from S2 author API", flush=True)
    return papers

# ── arXiv API query scraper ────────────────────────────────────────────────────

def scrape_arxiv_query(name: str, url: str) -> list[dict]:
    """
    Generic arXiv API query URL, e.g.:
      https://export.arxiv.org/api/query?search_query=au:Wang_Longbiao&max_results=200

    Fetches all matching entries (paginates if needed) and returns paper dicts
    with full metadata already populated so enrichment is skipped.
    """
    try:
        r = SESSION.get(url, timeout=30)
        if r.status_code == 429:
            print("    [!] arXiv 429 — sleeping 60s then retrying", file=sys.stderr)
            time.sleep(60)
            r = SESSION.get(url, timeout=30)
        r.raise_for_status()
    except Exception as e:
        print(f"  [!] arXiv query failed: {e}", file=sys.stderr)
        return []

    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    try:
        root = ET.fromstring(r.text)
    except ET.ParseError as e:
        print(f"  [!] arXiv parse error: {e}", file=sys.stderr)
        return []

    papers = []
    for entry in root.findall("a:entry", ns):
        id_el = entry.find("a:id", ns)
        if id_el is None:
            continue
        raw_id   = id_el.text.strip().split("/abs/")[-1]
        arxiv_id = re.sub(r"v[0-9]+$", "", raw_id)

        title_el   = entry.find("a:title",   ns)
        summary_el = entry.find("a:summary", ns)
        pub_el     = entry.find("a:published", ns)
        jr_el      = entry.find("arxiv:journal_ref", ns)

        title    = (title_el.text   or "").strip().replace("\n", " ")
        abstract = (summary_el.text or "").strip().replace("\n", " ")
        pub_date = (pub_el.text or "")[:10]
        place    = (jr_el.text or "").strip() if jr_el is not None else ""
        authors  = ", ".join(
            el.find("a:name", ns).text
            for el in entry.findall("a:author", ns)
            if el.find("a:name", ns) is not None
        )
        cats     = {el.get("term", "") for el in entry.findall("a:category", ns)}
        keywords = ", ".join(sorted(cats))

        papers.append({
            "paper_url":   arxiv_canonical(arxiv_id),
            "title":       title,
            "authors":     authors,
            "abstract":    abstract,
            "keywords":    keywords,
            "pub_date":    pub_date,
            "place":       place,
            "source_name": name,
            "source_url":  url,
        })

    print(f"  {name}: {len(papers)} papers from arXiv query", flush=True)
    return papers

# ── Dispatch ───────────────────────────────────────────────────────────────────

def scrape_source(name: str, url: str) -> list[dict]:
    if "github.com" in url:
        return scrape_github(name, url)
    if "scholar.google.com" in url:
        return scrape_scholar(name, url)
    if "semanticscholar.org/author/" in url:
        return scrape_s2_author(name, url)
    if "export.arxiv.org/api/query" in url or "arxiv.org/api/query" in url:
        return scrape_arxiv_query(name, url)
    print(f"  [!] Unsupported source URL: {url}", file=sys.stderr)
    return []

# ── Metadata enrichment ────────────────────────────────────────────────────────

def arxiv_batch_metadata(ids: list[str]) -> dict[str, dict]:
    clean_ids = [re.sub(r"v[0-9]+$", "", i) for i in ids]
    id_list   = ",".join(clean_ids)
    try:
        r = SESSION.get(
            f"https://export.arxiv.org/api/query?id_list={id_list}&max_results={len(ids)}",
            timeout=30,
        )
        if r.status_code == 429:
            print("    [!] arXiv 429 — sleeping 60 s then retrying", file=sys.stderr)
            time.sleep(60)
            r = SESSION.get(
                f"https://export.arxiv.org/api/query?id_list={id_list}&max_results={len(ids)}",
                timeout=30,
            )
        r.raise_for_status()
    except Exception as e:
        print(f"    [!] arXiv batch failed: {e}", file=sys.stderr)
        return {}

    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    result: dict[str, dict] = {}
    try:
        root = ET.fromstring(r.text)
    except ET.ParseError as e:
        print(f"    [!] arXiv parse error: {e}", file=sys.stderr)
        return {}

    for entry in root.findall("a:entry", ns):
        id_el = entry.find("a:id", ns)
        if id_el is None:
            continue
        arxiv_id = re.sub(r"v[0-9]+$", "", id_el.text.strip().split("/abs/")[-1])

        title_el   = entry.find("a:title",   ns)
        summary_el = entry.find("a:summary", ns)
        pub_el     = entry.find("a:published", ns)
        jr_el      = entry.find("arxiv:journal_ref", ns)

        result[arxiv_id] = {
            "title":       (title_el.text   or "").strip().replace("\n", " "),
            "authors":     ", ".join(
                el.find("a:name", ns).text
                for el in entry.findall("a:author", ns)
                if el.find("a:name", ns) is not None
            ),
            "abstract":    (summary_el.text or "").strip().replace("\n", " "),
            "categories":  {el.get("term", "") for el in entry.findall("a:category", ns)},
            "pub_date":    (pub_el.text or "")[:10],
            "journal_ref": (jr_el.text     or "").strip() if jr_el is not None else "",
        }
    return result


def s2_batch_metadata(arxiv_ids: list[str]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for start in range(0, len(arxiv_ids), S2_BATCH_SIZE):
        batch = arxiv_ids[start:start + S2_BATCH_SIZE]
        try:
            r = SESSION.post(
                "https://api.semanticscholar.org/graph/v1/paper/batch",
                params={"fields": "title,authors,abstract,publicationDate,venue,externalIds"},
                json={"ids": [f"arxiv:{i}" for i in batch]},
                timeout=30,
            )
            if r.status_code == 429:
                print("    [!] S2 rate limited — sleeping 30 s", file=sys.stderr)
                time.sleep(30)
                r = SESSION.post(
                    "https://api.semanticscholar.org/graph/v1/paper/batch",
                    params={"fields": "title,authors,abstract,publicationDate,venue,externalIds"},
                    json={"ids": [f"arxiv:{i}" for i in batch]},
                    timeout=30,
                )
            r.raise_for_status()
        except Exception as e:
            print(f"    [!] S2 batch failed: {e}", file=sys.stderr)
            continue

        for item in r.json():
            if not item:
                continue
            ext = item.get("externalIds") or {}
            aid = re.sub(r"v[0-9]+$", "", ext.get("ArXiv", ""))
            if not aid:
                continue
            result[aid] = {
                "title":    item.get("title")    or "",
                "authors":  ", ".join(a.get("name", "") for a in (item.get("authors") or [])),
                "abstract": item.get("abstract") or "",
                "pub_date": (item.get("publicationDate") or "")[:10],
                "place":    item.get("venue")    or "",
            }
        if start + S2_BATCH_SIZE < len(arxiv_ids):
            time.sleep(1)
    return result


def enrich_all(papers: list[dict]) -> list[dict]:
    """Batch-enrich arXiv papers via S2 then arXiv API; handle non-arXiv individually."""
    # Collect arXiv IDs
    arxiv_index: dict[str, int] = {}
    for i, p in enumerate(papers):
        m = ARXIV_RE.search(p.get("paper_url", ""))
        if m:
            arxiv_index[re.sub(r"v[0-9]+$", "", m.group(1))] = i

    ids = list(arxiv_index.keys())
    meta: dict[str, dict] = {}

    if ids:
        print(f"  S2: fetching {len(ids)} arXiv papers …", flush=True)
        meta = s2_batch_metadata(ids)
        print(f"    → {len(meta)} found via S2", flush=True)

        missed = [i for i in ids if i not in meta]
        if missed:
            print(f"  arXiv API: fetching {len(missed)} S2 misses …", flush=True)
            for start in range(0, len(missed), ARXIV_BATCH_SIZE):
                batch = missed[start:start + ARXIV_BATCH_SIZE]
                print(f"    batch {start // ARXIV_BATCH_SIZE + 1}: {len(batch)} IDs", flush=True)
                meta.update(arxiv_batch_metadata(batch))
                if start + ARXIV_BATCH_SIZE < len(missed):
                    time.sleep(ARXIV_BATCH_DELAY)

    for arxiv_id, idx in arxiv_index.items():
        m2  = meta.get(arxiv_id, {})
        p   = papers[idx]
        p["title"]    = p.get("title")    or m2.get("title",    "")
        p["authors"]  = p.get("authors")  or m2.get("authors",  "")
        p["abstract"] = p.get("abstract") or m2.get("abstract", "")
        kw = p.get("keywords") or ""
        if not kw and m2.get("categories"):
            kw = ", ".join(sorted(m2["categories"]))
        p["keywords"]   = kw
        p["pub_date"]   = p.get("pub_date") or m2.get("pub_date", "")
        p["place"]      = p.get("place")    or m2.get("place", "") or m2.get("journal_ref", "")
        p["categories"] = m2.get("categories", set())
        papers[idx] = p

    # Non-arXiv papers: try S2 title search for Scholar-URL papers to find real paper URLs
    scholar_url_papers = [
        p for p in papers
        if "scholar.google.com" in p.get("paper_url", "") and p.get("title")
    ]
    if scholar_url_papers:
        print(f"  S2 title search: resolving {len(scholar_url_papers)} Scholar-URL papers …",
              flush=True)
        for p in scholar_url_papers:
            try:
                r = SESSION.get(
                    "https://api.semanticscholar.org/graph/v1/paper/search",
                    params={
                        "query":  p["title"],
                        "fields": "title,authors,abstract,publicationDate,venue,externalIds",
                        "limit":  1,
                    },
                    timeout=15,
                )
                time.sleep(0.3)
                if r.status_code != 200:
                    continue
                hits = r.json().get("data", [])
                if not hits:
                    continue
                hit = hits[0]
                # Only accept if title is a close match (avoid false positives)
                if hit.get("title", "").lower()[:40] != p["title"].lower()[:40]:
                    continue
                ext = hit.get("externalIds") or {}
                if ext.get("ArXiv"):
                    p["paper_url"] = arxiv_canonical(ext["ArXiv"])
                p["title"]    = p.get("title")    or hit.get("title",    "")
                p["authors"]  = p.get("authors")  or ", ".join(
                    a.get("name", "") for a in (hit.get("authors") or []))
                p["abstract"] = p.get("abstract") or hit.get("abstract", "") or ""
                p["pub_date"] = p.get("pub_date") or (hit.get("publicationDate") or "")[:10]
                p["place"]    = p.get("place")    or hit.get("venue", "")
            except Exception as e:
                print(f"    [!] S2 title search failed for '{p['title'][:40]}': {e}",
                      file=sys.stderr)

    # Non-arXiv papers (OpenReview, ACL, PMLR, …)
    print("  Fetching non-arXiv metadata …", flush=True)
    for p in papers:
        url = p.get("paper_url", "")
        if ARXIV_RE.search(url):
            continue
        if "openreview.net" in url:
            m3 = re.search(r"id=([\w\-]+)", url)
            if m3:
                m4 = openreview_metadata(m3.group(1))
                time.sleep(0.2)
                for k in ("title", "authors", "abstract", "keywords", "pub_date", "place"):
                    p[k] = p.get(k) or m4.get(k, "")
        elif any(h in url for h in ("proceedings.mlr.press", "ojs.aaai.org",
                                     "ijcai.org/proceedings", "ecva.net/papers",
                                     "aclanthology.org")):
            m5 = _citation_meta(url)
            time.sleep(0.3)
            for k in ("title", "authors", "abstract", "pub_date"):
                p[k] = p.get(k) or m5.get(k, "")
            if not p.get("place") and m5.get("place"):
                p["place"] = m5["place"]
            if not p.get("place") and "aclanthology.org" in url:
                acl_m = ACL_RE.search(url)
                if acl_m:
                    p["place"] = _acl_venue(acl_m.group(1))

    return papers

# ── Persistence ────────────────────────────────────────────────────────────────

def load_existing(out_file: Path) -> dict[str, dict]:
    if not out_file.exists():
        return {}
    with out_file.open(newline="", encoding="utf-8") as f:
        return {r["paper_url"]: r for r in csv.DictReader(f, delimiter="\t")}

# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("category", help="Category label, e.g. memory or safety")
    args = parser.parse_args()
    category = args.category.lower().strip()

    out_file = PAPERS_DIR / f"{category}_papers_{MONTH}.tsv"
    PAPERS_DIR.mkdir(exist_ok=True)

    existing = load_existing(out_file)
    if existing:
        print(f"Loaded {len(existing)} existing rows from {out_file.name}", flush=True)

    all_papers: list[dict] = []
    seen_urls:  set[str]   = set()

    for name, url in load_sources(category):
        print(f"\nScraping {name} …", flush=True)
        fresh = scrape_source(name, url)
        if not fresh:
            # Scraping failed (rate-limited, network error, etc.) — keep existing rows
            # for this source so we don't lose previously fetched data.
            print(f"  [!] No papers returned for {name} — preserving existing data.",
                  file=sys.stderr)
            fresh = [v for v in existing.values()
                     if v.get("source_name") == name or v.get("source_url") == url]
        for p in fresh:
            key = p.get("paper_url", "")
            if not key or key in seen_urls:
                continue
            seen_urls.add(key)
            if key in existing:
                saved = existing[key]
                p.update({k: v for k, v in saved.items() if v and not p.get(k)})
            all_papers.append(p)

    # Safety net: if we ended up with nothing but had existing data, restore it entirely.
    if not all_papers and existing:
        print("  [!] Scraping returned nothing — restoring all existing rows.",
              file=sys.stderr)
        all_papers = list(existing.values())
        for p in all_papers:
            seen_urls.add(p.get("paper_url", ""))

    to_enrich  = [p for p in all_papers if not p.get("title") or not p.get("abstract")]
    have_meta  = len(all_papers) - len(to_enrich)
    print(f"\n{len(all_papers)} unique papers; {have_meta} already complete, "
          f"{len(to_enrich)} need enrichment.", flush=True)

    if to_enrich:
        to_enrich = enrich_all(to_enrich)
    enriched_by_url = {p["paper_url"]: p for p in to_enrich}

    _ws = re.compile(r"[\r\n\t]+")
    rows: list[dict] = []
    for p in all_papers:
        p   = enriched_by_url.get(p.get("paper_url", ""), p)
        row = {f: p.get(f, "") for f in SEEN_FIELDS}
        row["date_seen"] = row.get("date_seen") or TODAY
        row["category"]  = category
        if not row.get("keywords") and p.get("categories"):
            row["keywords"] = ", ".join(sorted(p["categories"]))
        for key in ("title", "authors", "abstract", "keywords", "place"):
            if row.get(key):
                row[key] = _ws.sub(" ", row[key]).strip()
        rows.append(row)

    with out_file.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SEEN_FIELDS, delimiter="\t",
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    filled = sum(1 for r in rows if r.get("title"))
    print(f"\nWrote {len(rows)} rows ({filled} with titles) → {out_file}", flush=True)
    if filled < len(rows):
        print(f"  {len(rows) - filled} missing titles — re-run to retry.", flush=True)


if __name__ == "__main__":
    main()
