#!/usr/bin/env python3
"""
check_papers.py

Scans all sources listed in TRACKED_SOURCES.md for AI/ML papers not seen before.
Writes new ones to papers/new_papers_YYYYMMDD.tsv and appends them to
papers/seen_papers_YYYYMM.tsv (title, authors, and URL only — not the paper itself).

Usage: python check_papers.py <GROUP> [GROUP ...]

  Groups: curated  uncurated  educational  corporate  all

  At least one group must be specified.  Use 'all' to check every source.
"""

import argparse
import csv
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT        = Path(__file__).parent
PAPERS_DIR  = ROOT / "papers"
TODAY       = datetime.now().strftime("%Y%m%d")
MONTH       = datetime.now().strftime("%Y%m")
SEEN_FILE   = PAPERS_DIR / f"seen_papers_{MONTH}.tsv"
OUT_FILE    = PAPERS_DIR / f"new_papers_{TODAY}.tsv"
SEEN_FIELDS = ["date_seen", "source_name", "source_url", "paper_url", "title", "authors", "abstract", "keywords"]

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
})
# Reddit requires a Reddit-style User-Agent for JSON API access
REDDIT_UA = "script:ai-paper-tracker:v1.0 (by /u/ai_paper_tracker_bot)"

def parse_feed(url: str) -> "feedparser.FeedParserDict":
    """
    Fetch a feed URL via SESSION (with our browser User-Agent) and hand the
    raw content to feedparser. feedparser's own HTTP client gets blocked by
    Substack, WordPress and many lab sites; this bypasses that problem.
    """
    try:
        r = SESSION.get(url, timeout=15)
        r.raise_for_status()
        return feedparser.parse(r.text)
    except Exception:
        return feedparser.parse("")

# ── Paper URL patterns ─────────────────────────────────────────────────────────
ARXIV_RE      = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})(?:v\d+)?")
OPENREVIEW_RE = re.compile(r"https?://openreview\.net/(?:forum|pdf)\?id=([\w\-]+)")
ACL_RE        = re.compile(r"https?://aclanthology\.org/([\w.]+?)(?:\.pdf)?(?:[/?#]|$)")
PMLR_RE       = re.compile(r"https?://proceedings\.mlr\.press/v\d+/([\w\-]+)\.html")

# ── AI/ML arXiv category filter ────────────────────────────────────────────────
AI_ML_CATS = frozenset({
    "cs.AI",   # Artificial Intelligence
    "cs.LG",   # Machine Learning
    "cs.CL",   # Computation and Language (NLP)
    "cs.CV",   # Computer Vision
    "cs.NE",   # Neural and Evolutionary Computing
    "cs.RO",   # Robotics
    "cs.IR",   # Information Retrieval
    "cs.MA",   # Multiagent Systems
    "cs.HC",   # Human-Computer Interaction
    "stat.ML", # Statistics - Machine Learning
    "eess.AS", # Audio and Speech Processing
    "eess.IV", # Image and Video Processing
})

# Sources where every paper is by definition AI/ML — skip category check
TRUSTED_AI_ML_HOSTS = frozenset({
    "huggingface.co",
    "paperswithcode.com",
    "openreview.net",
    "aclanthology.org",
    "proceedings.mlr.press",
    "distill.pub",
    "arxiv-sanity-lite.com",
    "alignmentforum.org",
    "arxiv.org",
})

def is_ai_ml(p: dict, source_url: str = "") -> bool:
    """
    Return True if a paper should be kept.

    For papers from trusted AI/ML venues or curated feeds, always accept.
    For papers found on lab publication pages (university/industry), require that
    the arXiv category list intersects AI_ML_CATS.
    """
    paper_url = p.get("paper_url", "")

    # Non-arXiv AI/ML venues are inherently relevant
    if any(d in paper_url for d in ("openreview.net", "aclanthology.org",
                                     "proceedings.mlr.press", "distill.pub")):
        return True

    # Source is a known curated AI/ML feed — trust it without a category check
    source_host = urlparse(source_url).netloc.lower().lstrip("www.")
    if any(h in source_host for h in TRUSTED_AI_ML_HOSTS):
        return True

    # arXiv paper from a lab page or general source — check categories
    if "arxiv.org" in paper_url:
        cats = p.get("categories", set())
        if cats:
            return bool(frozenset(cats) & AI_ML_CATS)
        # No categories available (enrichment failed) — accept by default
        return True

    return True  # unknown venue: accept

# ── Seen-paper persistence ─────────────────────────────────────────────────────
def load_seen() -> set:
    if not SEEN_FILE.exists():
        return set()
    with SEEN_FILE.open(newline="", encoding="utf-8") as f:
        return {row["paper_url"] for row in csv.DictReader(f, delimiter="\t")}

def append_seen(new_rows: list) -> None:
    PAPERS_DIR.mkdir(exist_ok=True)
    write_header = not SEEN_FILE.exists()
    with SEEN_FILE.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SEEN_FIELDS, delimiter="\t")
        if write_header:
            w.writeheader()
        for row in new_rows:
            w.writerow({**row, "date_seen": TODAY})

# ── Paper URL extraction ───────────────────────────────────────────────────────
def arxiv_canonical(arxiv_id: str) -> str:
    return f"https://arxiv.org/abs/{re.sub(r'v[0-9]+$', '', arxiv_id)}"

def extract_papers(text: str) -> list:
    """
    Extract paper records from raw text (HTML or plain), handling:
    arXiv, OpenReview, ACL Anthology, and PMLR proceedings.
    Returns deduplicated list of dicts with paper_url, title, authors.
    """
    seen_keys, out = set(), []

    for m in ARXIV_RE.finditer(text):
        key = f"arxiv:{m.group(1)}"
        if key not in seen_keys:
            seen_keys.add(key)
            out.append({"paper_url": arxiv_canonical(m.group(1)), "title": "", "authors": ""})

    for m in OPENREVIEW_RE.finditer(text):
        key = f"or:{m.group(1)}"
        if key not in seen_keys:
            seen_keys.add(key)
            out.append({"paper_url": f"https://openreview.net/forum?id={m.group(1)}", "title": "", "authors": ""})

    for m in ACL_RE.finditer(text):
        key = f"acl:{m.group(1)}"
        if key not in seen_keys:
            seen_keys.add(key)
            out.append({"paper_url": f"https://aclanthology.org/{m.group(1)}", "title": "", "authors": ""})

    for m in PMLR_RE.finditer(text):
        key = f"pmlr:{m.group(1)}"
        if key not in seen_keys:
            seen_keys.add(key)
            out.append({"paper_url": m.group(0), "title": "", "authors": ""})

    return out

def rss_blob(entry: dict) -> str:
    blob = entry.get("link", "") + " " + entry.get("summary", "")
    for part in entry.get("content", []):
        blob += " " + part.get("value", "")
    return blob

# ── Metadata enrichment ────────────────────────────────────────────────────────
def arxiv_metadata(arxiv_id: str) -> dict:
    """Fetch title, authors, and category list from the arXiv Atom API."""
    clean = re.sub(r"v[0-9]+$", "", arxiv_id)
    try:
        r = SESSION.get(f"http://export.arxiv.org/api/query?id_list={clean}", timeout=10)
        root = ET.fromstring(r.text)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        entry = root.find("a:entry", ns)
        if entry is None:
            return {}
        title   = entry.find("a:title", ns).text.strip().replace("\n", " ")
        authors = ", ".join(
            el.find("a:name", ns).text
            for el in entry.findall("a:author", ns)
        )
        categories = {
            el.get("term", "")
            for el in entry.findall("a:category", ns)
        }
        summary_el = entry.find("a:summary", ns)
        abstract = summary_el.text.strip().replace("\n", " ") if summary_el is not None else ""
        return {"title": title, "authors": authors, "categories": categories, "abstract": abstract}
    except Exception:
        return {}

def openreview_metadata(paper_id: str) -> dict:
    try:
        r = SESSION.get(f"https://api.openreview.net/notes?id={paper_id}", timeout=10)
        notes = r.json().get("notes", [])
        if not notes:
            return {}
        content = notes[0].get("content", {})
        title   = content.get("title", "")
        if isinstance(title, dict):
            title = title.get("value", "")
        authors = content.get("authors", [])
        if isinstance(authors, dict):
            authors = authors.get("value", [])
        abstract = content.get("abstract", "")
        if isinstance(abstract, dict):
            abstract = abstract.get("value", "")
        raw_kw = None
        for kw_field in ("keywords", "topics", "keyphrases"):
            raw_kw = content.get(kw_field)
            if raw_kw:
                break
        if isinstance(raw_kw, dict):
            raw_kw = raw_kw.get("value", [])
        keywords = ", ".join(raw_kw) if isinstance(raw_kw, list) else (str(raw_kw) if raw_kw else "")
        return {
            "title":    title,
            "authors":  ", ".join(authors),
            "abstract": abstract,
            "keywords": keywords,
        }
    except Exception:
        return {}

def enrich(p: dict) -> dict:
    """Fill in missing title/authors and fetch arXiv categories when not already present."""
    url = p.get("paper_url", "")
    m   = ARXIV_RE.search(url)

    # Always fetch arXiv metadata if we lack categories (needed for AI/ML filter)
    if m and not p.get("categories"):
        meta = arxiv_metadata(m.group(1))
        time.sleep(0.4)  # arXiv API rate limit
        p["title"]      = p.get("title")    or meta.get("title", "")
        p["authors"]    = p.get("authors")  or meta.get("authors", "")
        p["abstract"]   = p.get("abstract") or meta.get("abstract", "")
        p["keywords"]   = p.get("keywords") or meta.get("keywords", "")
        p["categories"] = meta.get("categories", set())
    elif "openreview.net" in url and not (p.get("title") and p.get("authors")):
        m2   = re.search(r"id=([\w\-]+)", url)
        meta = openreview_metadata(m2.group(1)) if m2 else {}
        p["title"]    = p.get("title")    or meta.get("title", "")
        p["authors"]  = p.get("authors")  or meta.get("authors", "")
        p["abstract"] = p.get("abstract") or meta.get("abstract", "")
        p["keywords"] = p.get("keywords") or meta.get("keywords", "")

    # Fall back to arXiv categories when no other keywords are available
    if not p.get("keywords") and p.get("categories"):
        p["keywords"] = ", ".join(sorted(p["categories"]))

    return p

# ── Dedicated scrapers ─────────────────────────────────────────────────────────

def scrape_huggingface(url: str) -> list:
    """HuggingFace daily papers API — returns full metadata directly."""
    try:
        data = SESSION.get("https://huggingface.co/api/daily_papers", timeout=15).json()
        out  = []
        for item in data:
            p   = item.get("paper", {})
            aid = p.get("id", "")
            if not aid:
                continue
            out.append({
                "paper_url":  arxiv_canonical(aid),
                "title":      p.get("title", ""),
                "authors":    ", ".join(a.get("name", "") for a in p.get("authors", [])),
                "abstract":   p.get("summary", ""),
                "categories": set(),  # skip API call; HuggingFace is a trusted AI/ML source
            })
        return out
    except Exception as e:
        print(f"    [!] HuggingFace API: {e}", file=sys.stderr)
        return []

def scrape_emergentmind(url: str) -> list:
    """
    emergentmind.com — fully client-side rendered (Next.js).
    No server-side HTML contains paper links; returns empty.
    """
    print("    [–] emergentmind.com requires JavaScript rendering — skipping", file=sys.stderr)
    return []

def scrape_scholarinbox(url: str) -> list:
    """
    scholar-inbox.com — SPA; server response contains no paper links.
    Returns empty without attempting a scrape.
    """
    print("    [–] scholar-inbox.com requires JavaScript rendering — skipping", file=sys.stderr)
    return []

def scrape_alphasignal(url: str) -> list:
    """
    alphasignal.ai — SPA. Try RSS; fall back to empty.
    """
    for feed_url in (url.rstrip("/") + "/feed", url.rstrip("/") + "/rss"):
        try:
            feed = parse_feed(feed_url)
            if feed.entries:
                out = []
                for entry in feed.entries[:10]:
                    out.extend(extract_papers(rss_blob(entry)))
                if out:
                    return out
        except Exception:
            pass
    print("    [–] alphasignal.ai: no scrapable RSS found (site is a SPA)", file=sys.stderr)
    return []

def scrape_tldr_ai(url: str) -> list:
    """
    tldr.tech/ai — Next.js SPA; content is not in server HTML.
    """
    print("    [–] tldr.tech requires JavaScript rendering — skipping", file=sys.stderr)
    return []

def scrape_substack(url: str) -> list:
    """
    Generic Substack scraper — uses /feed RSS endpoint with a browser User-Agent
    (Substack rejects feedparser's default UA).
    """
    feed_url = url.rstrip("/") + "/feed"
    try:
        feed = parse_feed(feed_url)
        if not feed.entries:
            print(f"    [–] Substack feed at {feed_url} returned no entries", file=sys.stderr)
            return []
        out = []
        for entry in feed.entries[:10]:
            out.extend(extract_papers(rss_blob(entry)))
        return out
    except Exception as e:
        print(f"    [!] Substack {url}: {e}", file=sys.stderr)
        return []

def scrape_wordpress(url: str) -> list:
    """
    Generic WordPress RSS scraper (Import AI, etc.) — uses /feed with browser UA.
    """
    feed_url = url.rstrip("/") + "/feed"
    try:
        feed = parse_feed(feed_url)
        if not feed.entries:
            print(f"    [–] WordPress feed at {feed_url} returned no entries", file=sys.stderr)
            return []
        out = []
        for entry in feed.entries[:10]:
            out.extend(extract_papers(rss_blob(entry)))
        return out
    except Exception as e:
        print(f"    [!] WordPress RSS {url}: {e}", file=sys.stderr)
        return []

def scrape_the_batch(url: str) -> list:
    """deeplearning.ai/the-batch — navigate to the latest issue page via HTML links."""
    try:
        r    = SESSION.get(url, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        issue_hrefs = sorted({
            a["href"] for a in soup.find_all("a", href=re.compile(r"/the-batch/issue-\d+"))
        })
        if issue_hrefs:
            latest = issue_hrefs[-1]
            if not latest.startswith("http"):
                latest = "https://www.deeplearning.ai" + latest
            r = SESSION.get(latest, timeout=15)
        return extract_papers(r.text)
    except Exception as e:
        print(f"    [!] The Batch: {e}", file=sys.stderr)
        return []

def scrape_lastweekinai(url: str) -> list:
    """lastweekin.ai — SPA; try RSS anyway, otherwise empty."""
    for feed_url in (url.rstrip("/") + "/feed", url.rstrip("/") + "/rss.xml"):
        try:
            feed = parse_feed(feed_url)
            if feed.entries:
                out = []
                for entry in feed.entries[:5]:
                    out.extend(extract_papers(rss_blob(entry)))
                if out:
                    return out
        except Exception:
            pass
    print("    [–] lastweekin.ai: no scrapable RSS found (site is a SPA)", file=sys.stderr)
    return []

def scrape_thegradient(url: str) -> list:
    """
    thegradient.pub — SPA; try RSS at /rss/ or /feed.
    """
    for feed_url in ("https://thegradient.pub/rss/", url.rstrip("/") + "/feed"):
        try:
            feed = parse_feed(feed_url)
            if feed.entries:
                out = []
                for entry in feed.entries[:10]:
                    out.extend(extract_papers(rss_blob(entry)))
                if out:
                    return out
        except Exception:
            pass
    print("    [–] thegradient.pub: no scrapable RSS found", file=sys.stderr)
    return []

def scrape_distill(url: str) -> list:
    """
    distill.pub — papers live at distill.pub/YYYY/name, not arXiv.
    RSS feed at distill.pub/rss.xml carries full title and authors.
    """
    try:
        feed = parse_feed("https://distill.pub/rss.xml")
        out  = []
        for entry in feed.entries:
            paper_url = entry.get("link", "").rstrip("/")
            if not paper_url or "distill.pub" not in paper_url:
                continue
            title    = entry.get("title", "")
            authors  = ", ".join(a.get("name", "") for a in entry.get("authors", []))
            abstract = entry.get("summary", "")
            out.append({"paper_url": paper_url, "title": title, "authors": authors, "abstract": abstract})
        if not out:
            print("    [–] distill.pub RSS returned no entries (site may be inactive)", file=sys.stderr)
        return out
    except Exception as e:
        print(f"    [!] Distill.pub: {e}", file=sys.stderr)
        return []

def scrape_alignmentforum(url: str) -> list:
    """AI Alignment Forum — RSS feed; posts often link to arXiv papers."""
    try:
        feed = parse_feed("https://www.alignmentforum.org/feed.xml")
        out  = []
        for entry in feed.entries[:30]:
            out.extend(extract_papers(rss_blob(entry)))
        if not out:
            print("    [–] Alignment Forum RSS returned no entries or no paper links", file=sys.stderr)
        return out
    except Exception as e:
        print(f"    [!] Alignment Forum: {e}", file=sys.stderr)
        return []

def scrape_paperswithcode(url: str) -> list:
    """Papers With Code — public REST API returns recent papers with arXiv IDs."""
    try:
        api  = "https://paperswithcode.com/api/v1/papers/?format=json&ordering=-published&items_per_page=50"
        r    = SESSION.get(api, timeout=15)
        r.raise_for_status()
        data = r.json()
        out  = []
        for item in data.get("results", []):
            aid = item.get("arxiv_id", "")
            if aid:
                out.append({
                    "paper_url":  arxiv_canonical(aid),
                    "title":      item.get("title", ""),
                    "authors":    "",
                    "categories": set(),
                })
        return out
    except Exception as e:
        print(f"    [!] Papers With Code API: {e}", file=sys.stderr)
        return []

def scrape_arxiv_listing(url: str) -> list:
    """arXiv listing page (/list/cs.LG/recent etc.) — structured HTML with full metadata."""
    try:
        soup = BeautifulSoup(SESSION.get(url, timeout=20).text, "html.parser")
        out  = []
        for dt in soup.select("dl dt"):
            a = dt.find("a", title="Abstract")
            if not a:
                continue
            m = re.search(r"/abs/(\d{4}\.\d{4,5})", a.get("href", ""))
            if not m:
                continue
            dd = dt.find_next_sibling("dd")
            title = authors = ""
            if dd:
                t = dd.find("div", class_="list-title")
                if t:
                    title = t.get_text().replace("Title:", "").strip()
                au = dd.find("div", class_="list-authors")
                if au:
                    # get_text with separator produces extra commas for empty elements; clean up
                    raw_authors = au.get_text(separator=", ").replace("Authors:", "").strip()
                    authors = re.sub(r",\s*,", ",", raw_authors).strip(", ")
            # arXiv listing category is known from the URL (e.g. cs.LG)
            cat_match = re.search(r"arxiv\.org/list/([^/]+)/", url)
            categories = {cat_match.group(1)} if cat_match else set()
            out.append({
                "paper_url":  arxiv_canonical(m.group(1)),
                "title":      title,
                "authors":    authors,
                "categories": categories,
            })
        return out
    except Exception as e:
        print(f"    [!] arXiv listing: {e}", file=sys.stderr)
        return []

def scrape_arxiv_sanity(url: str) -> list:
    """arxiv-sanity-lite.com — paper cards with arXiv links."""
    try:
        return extract_papers(SESSION.get(url, timeout=15).text)
    except Exception as e:
        print(f"    [!] arXiv Sanity: {e}", file=sys.stderr)
        return []

def scrape_github_readme(url: str) -> list:
    """
    GitHub repo — fetch raw README.md and extract paper links.
    Also follows relative links to other .md files in the repo (e.g. years/2026.md)
    to handle repos like DAIR.AI that store paper lists in sub-pages.
    """
    m = re.match(r"https://github\.com/([^/?#]+/[^/?#]+)", url)
    if not m:
        return []
    repo   = m.group(1)
    papers = []
    fetched_paths = set()

    def fetch_md(path: str) -> str:
        for branch in ("main", "master"):
            try:
                r = SESSION.get(
                    f"https://raw.githubusercontent.com/{repo}/{branch}/{path}",
                    timeout=10,
                )
                if r.status_code == 200:
                    return r.text
            except Exception:
                pass
        return ""

    readme = fetch_md("README.md")
    if not readme:
        return []
    fetched_paths.add("README.md")
    papers.extend(extract_papers(readme))

    # Follow relative .md links found in the README (up to 3 sub-pages)
    md_links = re.findall(r"\[.*?\]\(([^)]+\.md[^)]*)\)", readme)
    for link in md_links[:3]:
        link = link.split("#")[0].strip()   # strip anchor fragments
        if link.startswith("http") or link in fetched_paths:
            continue
        fetched_paths.add(link)
        content = fetch_md(link)
        if content:
            papers.extend(extract_papers(content))
            time.sleep(0.3)

    return papers

def scrape_reddit(url: str) -> list:
    """
    Reddit — uses the subreddit RSS feed (/.rss), which is less aggressively
    rate-limited than the JSON API endpoint. Falls back to the JSON API with a
    Reddit-style User-Agent if the RSS returns nothing.
    """
    # RSS approach
    rss_url = url.rstrip("/") + "/.rss?limit=100"
    feed = parse_feed(rss_url)
    if feed.entries:
        out = []
        for entry in feed.entries:
            out.extend(extract_papers(rss_blob(entry)))
        if out:
            return out

    # JSON API fallback
    try:
        r = SESSION.get(
            url.rstrip("/") + ".json?limit=100",
            headers={"User-Agent": REDDIT_UA},
            timeout=15,
        )
        r.raise_for_status()
        out = []
        for post in r.json().get("data", {}).get("children", []):
            d = post.get("data", {})
            out.extend(extract_papers(d.get("url", "") + " " + d.get("selftext", "")))
        return out
    except Exception as e:
        print(f"    [!] Reddit: {e}", file=sys.stderr)
        return []

def scrape_semantic_scholar(url: str) -> list:
    """Semantic Scholar — public graph API; queries recent AI/ML papers."""
    try:
        api  = (
            "https://api.semanticscholar.org/graph/v1/paper/search"
            "?query=machine+learning+deep+learning"
            "&fields=title,authors,externalIds"
            "&sort=citationCount&limit=50"
        )
        data = SESSION.get(api, timeout=15).json()
        out  = []
        for item in data.get("data", []):
            ids     = item.get("externalIds", {})
            aid     = ids.get("ArXiv", "")
            title   = item.get("title", "")
            authors = ", ".join(a.get("name", "") for a in item.get("authors", []))
            if aid:
                out.append({"paper_url": arxiv_canonical(aid), "title": title, "authors": authors})
        return out
    except Exception as e:
        print(f"    [!] Semantic Scholar API: {e}", file=sys.stderr)
        return []

def scrape_lab_page(url: str) -> list:
    """
    Generic scraper for university and industry lab pages.
    Strategy (in order):
      1. Try RSS/Atom at the domain root and common blog sub-paths.
      2. Fetch the URL itself and common /publications, /research, /papers sub-paths.
    All discovered arXiv/OpenReview/ACL links are returned; the AI/ML category
    filter in main() then discards papers outside AI/ML.
    """
    parsed = urlparse(url)
    base   = f"{parsed.scheme}://{parsed.netloc}"
    out    = []

    # 1. RSS discovery
    for feed_suffix in ("/feed", "/rss.xml", "/feed.xml", "/atom.xml",
                        "/blog/feed", "/blog/rss", "/news/feed"):
        feed_url = base + feed_suffix
        try:
            feed = parse_feed(feed_url)
            if feed.entries:
                for entry in feed.entries[:20]:
                    out.extend(extract_papers(rss_blob(entry)))
                if out:
                    return out
        except Exception:
            pass

    # 2. Publications sub-pages
    candidates = [url]
    for suffix in ("/publications", "/publications/", "/research", "/papers",
                   "/pub", "/pubs", "/work"):
        candidates.append(base + suffix)
        if parsed.path.rstrip("/"):
            candidates.append(url.rstrip("/") + suffix)

    for page_url in candidates:
        try:
            r = SESSION.get(page_url, timeout=15)
            if r.status_code == 200:
                out.extend(extract_papers(r.text))
        except Exception:
            pass
        time.sleep(0.5)

    return out

def scrape_skip(url: str) -> list:
    """Sources requiring authentication, email delivery, or on-demand interaction."""
    print("    [–] skipped (requires login, email, or on-demand interaction)", file=sys.stderr)
    return []

# ── Generic fallbacks ──────────────────────────────────────────────────────────
def scrape_rss(url: str) -> list:
    candidates = [url] + [
        url.rstrip("/") + s
        for s in ("/feed", "/rss", "/feed.xml", "/rss.xml", "/atom.xml")
    ]
    for feed_url in candidates:
        try:
            feed = parse_feed(feed_url)
        except Exception:
            continue
        if not feed.entries:
            continue
        out = []
        for entry in feed.entries:
            out.extend(extract_papers(rss_blob(entry)))
        if out:
            return out
    return []

def scrape_html(url: str) -> list:
    try:
        return extract_papers(SESSION.get(url, timeout=15).text)
    except Exception as e:
        print(f"    [!] HTML scrape {url}: {e}", file=sys.stderr)
        return []

# ── Lab domains ────────────────────────────────────────────────────────────────
LAB_DOMAINS = frozenset({
    # Stanford
    "ai.stanford.edu", "nlp.stanford.edu", "crfm.stanford.edu", "hai.stanford.edu",
    # MIT
    "csail.mit.edu", "cocosci.mit.edu", "madry-lab.ml",
    # CMU
    "ml.cmu.edu", "lti.cs.cmu.edu",
    # Berkeley
    "bair.berkeley.edu", "humancompatible.ai",
    # Canada
    "mila.quebec", "vectorinstitute.ai",
    # Cornell / NYU / Princeton / UW
    "nlp.cs.cornell.edu", "tech.cornell.edu", "cds.nyu.edu",
    "nlp.cs.princeton.edu", "nlp.washington.edu",
    # Europe (universities)
    "oatml.cs.ox.ac.uk", "robots.ox.ac.uk", "gatsby.ucl.ac.uk",
    "edinburghnlp.inf.ed.ac.uk", "ai.ethz.ch", "is.mpg.de",
    "amlab.science.uva.nl", "idsia.ch", "chechiklab.biu.ac.il",
    # Asia (universities)
    "nlp.csai.tsinghua.edu.cn", "keg.cs.tsinghua.edu.cn",
    "ai.kaist.ac.kr", "gsai.kaist.ac.kr", "comp.nus.edu.sg",
    # Industry labs
    "deepmind.google", "research.google",
    "ai.meta.com",
    "microsoft.com",
    "openai.com",
    "anthropic.com",
    "machinelearning.apple.com",
    "amazon.science",
    "research.nvidia.com",
    "salesforceairesearch.com",
    "research.ibm.com",
    "allenai.org",
    "eleuther.ai",
    "redwoodresearch.org",
    "alignmentresearchcenter.org",
    "kyutai.org",
    "research.baidu.com",
    "damo.alibaba.com",
    "ai.tencent.com",
    "seed.bytedance.com",
    "shlab.org.cn",
})

SKIP_HOSTS = frozenset({
    "scholar.google.com",   # email alerts only
    "researchgate.net",     # login required
    "connectedpapers.com",  # on-demand graph, no feed
    "x.com", "twitter.com",
})

# ── Dispatch ───────────────────────────────────────────────────────────────────
def dispatch(name: str, url: str) -> list:
    host = urlparse(url).netloc.lower().lstrip("www.")
    full = url.lower()

    if any(d in host for d in SKIP_HOSTS):
        return scrape_skip(url)

    if "huggingface.co/papers" in full:           return scrape_huggingface(url)
    if "emergentmind.com"       in host:           return scrape_emergentmind(url)
    if "scholar-inbox.com"      in host:           return scrape_scholarinbox(url)
    if "alphasignal.ai"         in host:           return scrape_alphasignal(url)
    if "tldr.tech"              in host:           return scrape_tldr_ai(url)
    if "deeplearning.ai"        in host and "the-batch" in full:
                                                   return scrape_the_batch(url)
    if "lastweekin.ai"          in host:           return scrape_lastweekinai(url)
    if "thegradient.pub"        in host:           return scrape_thegradient(url)
    if "distill.pub"            in host:           return scrape_distill(url)
    if "alignmentforum.org"     in host:           return scrape_alignmentforum(url)
    if "paperswithcode.com"     in host:           return scrape_paperswithcode(url)
    if "arxiv.org/list"         in full:           return scrape_arxiv_listing(url)
    if "arxiv-sanity-lite.com"  in host:           return scrape_arxiv_sanity(url)
    if "github.com"             in host:           return scrape_github_readme(url)
    if "reddit.com/r/"          in full:           return scrape_reddit(url)
    if "semanticscholar.org"    in host:           return scrape_semantic_scholar(url)

    if any(d in host for d in ("substack.com", "interconnects.ai", "sebastianraschka.com")):
        return scrape_substack(url)

    if any(d in host for d in ("jack-clark.net",)):
        return scrape_wordpress(url)

    if any(d in host for d in LAB_DOMAINS):
        return scrape_lab_page(url)

    papers = scrape_rss(url)
    return papers if papers else scrape_html(url)

# ── TRACKED_SOURCES.md parser ──────────────────────────────────────────────────

# Maps substrings of ## section headings to source group names.
_SECTION_GROUP_MAP = [
    ("daily",               "curated"),
    ("weekly",              "curated"),
    ("bi-weekly",           "curated"),
    ("social feeds",        "curated"),
    ("university",          "educational"),
    ("industry-affiliated", "corporate"),
    ("algorithmic",         "uncurated"),
    ("uncurated",           "uncurated"),
]

def parse_sources(md_path: Path) -> list:
    """Return list of (name, url, group) tuples from TRACKED_SOURCES.md."""
    url_re        = re.compile(r"https?://[^\s\)\]|>\"',]+")
    seen_urls     = set()
    sources       = []
    current_group = "curated"  # default for any unrecognised top-level section

    for line in md_path.read_text().splitlines():
        # Only ## headings switch the active group; ### sub-headings are ignored.
        if line.startswith("## "):
            heading = line[3:].strip().lower()
            for pattern, group in _SECTION_GROUP_MAP:
                if pattern in heading:
                    current_group = group
                    break

        if re.fullmatch(r"[\s|:\-]+", line):
            continue
        urls = [u.rstrip(".,)") for u in url_re.findall(line)]
        if not urls:
            continue
        for url in urls:
            if url in seen_urls:
                continue
            seen_urls.add(url)

            if "|" in line:
                cols = [c.strip() for c in line.split("|") if c.strip()]
                name = cols[0] if cols else url
            else:
                name = re.sub(r"^[-*•\s]+", "", line).split("(")[0].strip()

            name = re.sub(r"\*+([^*]+)\*+", r"\1", name)
            name = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", name)
            name = name.strip(" —-") or url
            sources.append((name, url, current_group))

    return sources

# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check tracked sources for new AI/ML papers.",
        epilog="At least one GROUP must be provided. Use 'all' to check every source.",
    )
    parser.add_argument(
        "groups",
        nargs="*",
        metavar="GROUP",
        choices=["curated", "uncurated", "educational", "corporate", "all"],
        help="Source groups to check: curated, uncurated, educational, corporate, all",
    )
    args = parser.parse_args()

    if not args.groups:
        parser.print_help()
        sys.exit(0)

    active = set(args.groups)
    check_all = "all" in active

    PAPERS_DIR.mkdir(exist_ok=True)
    seen = load_seen()

    md_path = ROOT / "TRACKED_SOURCES.md"
    if not md_path.exists():
        sys.exit(f"Error: {md_path} not found")

    all_sources = parse_sources(md_path)
    sources = [(name, url) for name, url, group in all_sources if check_all or group in active]
    print(f"Checking {len(sources)} sources …\n")

    new_rows    = []
    filtered_ct = 0

    for name, url in sources:
        print(f"  {name}")
        try:
            papers = dispatch(name, url)
        except Exception as e:
            print(f"    [!] {e}", file=sys.stderr)
            papers = []

        fresh = [p for p in papers if p.get("paper_url") and p["paper_url"] not in seen]

        for p in fresh:
            p = enrich(p)

            if not is_ai_ml(p, source_url=url):
                filtered_ct += 1
                continue

            seen.add(p["paper_url"])
            new_rows.append({
                "source_name": name,
                "source_url":  url,
                "paper_url":   p["paper_url"],
                "title":       p.get("title", ""),
                "authors":     p.get("authors", ""),
                "abstract":    p.get("abstract", ""),
                "keywords":    p.get("keywords", ""),
            })

        if fresh:
            kept = len([p for p in fresh if p.get("paper_url") in seen])
            print(f"    → {kept} new AI/ML paper(s) kept")
        time.sleep(1)

    append_seen(new_rows)

    if filtered_ct:
        print(f"\n  [{filtered_ct} non-AI/ML paper(s) filtered out]")

    if new_rows:
        with OUT_FILE.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(
                f,
                fieldnames=["source_name", "source_url", "paper_url", "title", "authors", "abstract", "keywords"],
                delimiter="\t",
            )
            w.writeheader()
            w.writerows(new_rows)
        print(f"\n{len(new_rows)} new papers written to {OUT_FILE.relative_to(ROOT)}")
    else:
        print("\nNo new papers found.")

if __name__ == "__main__":
    main()
