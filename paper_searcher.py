#!/usr/bin/env python3
"""
check_papers.py

Scans all sources listed in TRACKED_SOURCES.md for AI/ML papers not seen before.
Writes new ones to papers/new_papers_YYYYMMDD.tsv and appends them to
papers/seen_papers_YYYYMM.tsv (title, authors, and URL only — not the paper itself).

Usage: python check_papers.py <GROUP> [GROUP ...]

  Groups: curated  uncurated  educational  corporate  conferences  journals  all

  At least one group must be specified.  Use 'all' to check every source.
"""

import argparse
import csv
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
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
SEEN_FIELDS = ["date_seen", "source_name", "source_url", "paper_url", "title", "authors",
               "abstract", "keywords", "pub_date", "place", "category",
               "viewed", "read", "bookmarked", "labelled"]

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
    # conference proceedings
    "papers.nips.cc",
    "openaccess.thecvf.com",
    "ojs.aaai.org",
    "ecva.net",
    "ijcai.org",
    "isca-archive.org",
    # peer-reviewed journals
    "jmlr.org",
    "ieeexplore.ieee.org",
    "link.springer.com",
    "nature.com",
    "sciencedirect.com",
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
    if any(d in paper_url for d in (
        "openreview.net", "aclanthology.org", "proceedings.mlr.press", "distill.pub",
        "papers.nips.cc", "openaccess.thecvf.com", "ojs.aaai.org", "ecva.net",
        "ijcai.org", "isca-archive.org", "jmlr.org",
        "ieeexplore.ieee.org", "link.springer.com", "nature.com", "sciencedirect.com",
    )):
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
    """Fetch title, authors, categories, pub date, and journal ref from the arXiv Atom API."""
    clean = re.sub(r"v[0-9]+$", "", arxiv_id)
    try:
        r = SESSION.get(f"http://export.arxiv.org/api/query?id_list={clean}", timeout=10)
        root = ET.fromstring(r.text)
        ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
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
        published_el = entry.find("a:published", ns)
        pub_date = published_el.text[:10] if published_el is not None and published_el.text else ""
        jr_el = entry.find("arxiv:journal_ref", ns)
        journal_ref = jr_el.text.strip() if jr_el is not None and jr_el.text else ""
        return {"title": title, "authors": authors, "categories": categories,
                "abstract": abstract, "pub_date": pub_date, "journal_ref": journal_ref}
    except Exception:
        return {}

def openreview_metadata(paper_id: str) -> dict:
    def _val(x):
        return x.get("value", "") if isinstance(x, dict) else (x or "")

    note = None
    for api_base in ("https://api2.openreview.net", "https://api.openreview.net"):
        try:
            r = SESSION.get(f"{api_base}/notes?id={paper_id}", timeout=10)
            notes = r.json().get("notes", [])
            if notes:
                note = notes[0]
                break
        except Exception:
            continue
    if note is None:
        return {}

    try:
        content  = note.get("content", {})
        title    = _val(content.get("title", ""))
        authors  = _val(content.get("authors", []))
        abstract = _val(content.get("abstract", ""))

        raw_kw = None
        for kw_field in ("keywords", "topics", "keyphrases"):
            raw_kw = content.get(kw_field)
            if raw_kw:
                break
        if isinstance(raw_kw, dict):
            raw_kw = raw_kw.get("value", [])
        keywords = ", ".join(raw_kw) if isinstance(raw_kw, list) else (str(raw_kw) if raw_kw else "")

        cdate = note.get("cdate") or note.get("tcdate")
        pub_date = ""
        if isinstance(cdate, (int, float)):
            pub_date = datetime.fromtimestamp(cdate / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

        # Prefer venueid (structured) → parse to "CONFNAME YEAR"; fall back to venue (human-readable)
        venueid = _val(content.get("venueid", ""))
        venue = ""
        if venueid:
            venue = _parse_openreview_invitation(venueid) or venueid.split("/")[0]
        if not venue:
            venue = _val(content.get("venue", ""))
        if not venue:
            invitations = note.get("invitations", note.get("invitation", ""))
            if isinstance(invitations, list):
                invitations = invitations[0] if invitations else ""
            venue = _parse_openreview_invitation(invitations)

        return {
            "title":    title,
            "authors":  ", ".join(authors) if isinstance(authors, list) else authors,
            "abstract": abstract,
            "keywords": keywords,
            "pub_date": pub_date,
            "place":    venue,
        }
    except Exception:
        return {}

def _citation_meta(url: str) -> dict:
    """
    Fetch a paper landing page and extract structured metadata from citation_* meta tags
    and the on-page abstract element.  Works for PMLR, IJCAI, ISCA, AAAI, and many
    other scholarly publishers that embed Google Scholar / Highwire-style meta tags.
    """
    try:
        r    = SESSION.get(url, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")

        def _meta_values(name):
            return [t.get("content", "").strip()
                    for t in soup.find_all("meta", attrs={"name": name})
                    if t.get("content", "").strip()]

        title   = (_meta_values("citation_title") + [""])[0]
        authors = ", ".join(_meta_values("citation_author"))
        venue   = (_meta_values("citation_conference_title")
                   + _meta_values("citation_inbook_title")
                   + _meta_values("citation_journal_title") + [""])[0]

        pub_date = ""
        raw = (_meta_values("citation_publication_date") + [""])[0]
        if raw:
            parts = raw.replace("/", "-").split("-")
            pub_date = "-".join(p.zfill(2) for p in parts[:3])

        # Abstract: look for an element whose class contains "abstract"
        abstract = ""
        for el in soup.find_all(True, class_=lambda c: c and "abstract" in " ".join(c).lower()):
            text = el.get_text(strip=True)
            if len(text) > 80:
                abstract = text
                break
        if not abstract:
            for name in ("twitter:description", "DC.Description", "description"):
                td = soup.find("meta", attrs={"name": name})
                if td:
                    candidate = td.get("content", "").strip()
                    if len(candidate) > 80:
                        abstract = candidate
                        break

        return {"title": title, "authors": authors, "pub_date": pub_date,
                "abstract": abstract, "place": venue}
    except Exception:
        return {}


def _parse_openreview_invitation(invitation: str) -> str:
    """Extract 'CONFNAME YEAR' from an OpenReview invitation or venue ID string."""
    # "NeurIPS.cc/2024/Conference" or "ICLR.cc/2025/Conference/-/Submission"
    m = re.match(r"([A-Za-z]+)\.(?:cc|org|net)/(\d{4})", invitation)
    if m and m.group(1).lower() not in ("aclweb", "openreview"):
        return f"{m.group(1)} {m.group(2)}"
    # "/CONFNAME/YEAR/" path pattern (e.g. aclweb.org/ACL/2024/Conference)
    m = re.search(r"/([A-Z]{2,10})/(\d{4})(?:/|$)", invitation)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return ""

def _acl_venue(paper_id: str) -> str:
    """Infer venue name from ACL Anthology paper ID. E.g. '2024.acl-long.1' → 'ACL 2024'."""
    m = re.match(r"(\d{4})\.([a-z]+)", paper_id.lower())
    if m:
        return f"{m.group(2).split('-')[0].upper()} {m.group(1)}"
    return "ACL Anthology"

def enrich(p: dict) -> dict:
    """Fill in missing title/authors and fetch arXiv categories when not already present."""
    url = p.get("paper_url", "")
    m   = ARXIV_RE.search(url)

    # Fetch arXiv metadata when we lack categories (AI/ML filter) OR when abstract is missing
    if m and (not p.get("categories") or not p.get("abstract")):
        meta = arxiv_metadata(m.group(1))
        time.sleep(0.4)  # arXiv API rate limit
        p["title"]      = p.get("title")    or meta.get("title", "")
        p["authors"]    = p.get("authors")  or meta.get("authors", "")
        p["abstract"]   = p.get("abstract") or meta.get("abstract", "")
        p["keywords"]   = p.get("keywords") or meta.get("keywords", "")
        p["categories"] = meta.get("categories", set())
        p["pub_date"]   = p.get("pub_date") or meta.get("pub_date", "")
        if not p.get("place"):
            p["place"] = meta.get("journal_ref", "")
    elif "openreview.net" in url and not (p.get("title") and p.get("abstract") and p.get("pub_date")):
        m2   = re.search(r"id=([\w\-]+)", url)
        meta = openreview_metadata(m2.group(1)) if m2 else {}
        time.sleep(0.15)  # OpenReview API rate limit
        p["title"]    = p.get("title")    or meta.get("title", "")
        p["authors"]  = p.get("authors")  or meta.get("authors", "")
        p["abstract"] = p.get("abstract") or meta.get("abstract", "")
        p["keywords"] = p.get("keywords") or meta.get("keywords", "")
        p["pub_date"] = p.get("pub_date") or meta.get("pub_date", "")
        p["place"]    = p.get("place")    or meta.get("place", "")
    elif any(h in url for h in ("proceedings.mlr.press", "ojs.aaai.org", "ijcai.org/proceedings",
                                "ecva.net/papers")) and not p.get("abstract"):
        # These venues embed citation_* meta tags (and AAAI/PMLR have on-page abstracts)
        meta = _citation_meta(url)
        p["title"]    = p.get("title")    or meta.get("title", "")
        p["authors"]  = p.get("authors")  or meta.get("authors", "")
        p["abstract"] = p.get("abstract") or meta.get("abstract", "")
        p["pub_date"]  = p.get("pub_date")  or meta.get("pub_date", "")
        if not p.get("place") and meta.get("place"):
            p["place"] = meta["place"]

    # Approximate pub_date from arXiv ID (YYMM.NNNNN → YYYY-MM) when still missing
    if m and not p.get("pub_date"):
        id_m = re.match(r"(\d{2})(\d{2})\.\d+", m.group(1))
        if id_m:
            p["pub_date"] = f"20{id_m.group(1)}-{id_m.group(2)}"

    # Venue inference from paper URL when not already set
    if not p.get("place") and "aclanthology.org" in url:
        acl_m = ACL_RE.search(url)
        if acl_m:
            p["place"] = _acl_venue(acl_m.group(1))

    if not p.get("place") and "proceedings.mlr.press" in url:
        pmlr_m = re.search(r"/v(\d+)/", url)
        if pmlr_m:
            p["place"] = f"PMLR v{pmlr_m.group(1)}"

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
            pp = entry.get("published_parsed")
            pub_date = f"{pp.tm_year:04d}-{pp.tm_mon:02d}-{pp.tm_mday:02d}" if pp else ""
            out.append({"paper_url": paper_url, "title": title, "authors": authors,
                        "abstract": abstract, "pub_date": pub_date, "place": "Distill"})
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
    """Papers With Code — tries JSON API first; falls back to HTML scraping."""
    api = "https://paperswithcode.com/api/v1/papers/?format=json&ordering=-published&items_per_page=50"
    try:
        r = SESSION.get(api, timeout=15)
        ct = r.headers.get("content-type", "")
        if r.status_code == 200 and "json" in ct:
            out = []
            for item in r.json().get("results", []):
                aid = item.get("arxiv_id", "")
                if aid:
                    out.append({
                        "paper_url":  arxiv_canonical(aid),
                        "title":      item.get("title", ""),
                        "authors":    "",
                        "categories": set(),
                    })
            if out:
                return out
    except Exception:
        pass
    # API is blocked (Cloudflare); scrape arXiv links from the HTML page
    try:
        papers = extract_papers(SESSION.get(url, timeout=15).text)
        if papers:
            return papers
        print("    [–] Papers With Code: API blocked and HTML has no arXiv links", file=sys.stderr)
    except Exception as e:
        print(f"    [!] Papers With Code: {e}", file=sys.stderr)
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
    """arxiv-sanity-lite.com — client-side SPA; no server-rendered arXiv links."""
    print("    [–] arxiv-sanity-lite.com is client-side rendered (SPA) — skipping", file=sys.stderr)
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
        time.sleep(5)  # unauthenticated endpoint has a strict rate limit; get a free API key
        api  = (
            "https://api.semanticscholar.org/graph/v1/paper/search"
            "?query=machine+learning+deep+learning"
            "&fields=title,authors,externalIds"
            "&sort=citationCount&limit=50"
        )
        r = SESSION.get(api, timeout=15)
        if r.status_code == 429:
            print("    [–] Semantic Scholar: rate-limited (429); consider adding an API key", file=sys.stderr)
            return []
        data = r.json()
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

def scrape_openreview_venue(url: str) -> list:
    """
    OpenReview venue page (openreview.net/group?id=...) — queries the v2 then v1
    OpenReview API for all submissions in the venue.
    """
    m = re.search(r"id=([^&\s]+)", url)
    if not m:
        return scrape_html(url)
    venue_id   = m.group(1)
    venue_name = _parse_openreview_invitation(venue_id)
    out = []
    for api_base in ("https://api2.openreview.net", "https://api.openreview.net"):
        for suffix in ("/-/Submission", "/-/Blind_Submission", "/-/Camera_Ready_Submission"):
            try:
                api = f"{api_base}/notes?invitation={venue_id}{suffix}&limit=1000&offset=0"
                r   = SESSION.get(api, timeout=20)
                r.raise_for_status()
                notes = r.json().get("notes", [])
                for note in notes:
                    content  = note.get("content", {})
                    title    = content.get("title", "")
                    if isinstance(title, dict):    title    = title.get("value", "")
                    abstract = content.get("abstract", "")
                    if isinstance(abstract, dict): abstract = abstract.get("value", "")
                    authors_raw = content.get("authors", [])
                    if isinstance(authors_raw, dict): authors_raw = authors_raw.get("value", [])
                    authors = ", ".join(authors_raw) if isinstance(authors_raw, list) else ""
                    note_id = note.get("id", "")
                    cdate   = note.get("cdate") or note.get("tcdate")
                    pub_date = ""
                    if isinstance(cdate, (int, float)):
                        pub_date = datetime.fromtimestamp(cdate / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                    if note_id:
                        out.append({
                            "paper_url": f"https://openreview.net/forum?id={note_id}",
                            "title":     title,
                            "authors":   authors,
                            "abstract":  abstract,
                            "pub_date":  pub_date,
                            "place":     venue_name,
                        })
                if out:
                    return out
            except Exception:
                pass
        if out:
            break
    if not out:
        print(f"    [–] OpenReview venue {venue_id}: no papers via API; falling back to HTML",
              file=sys.stderr)
        return scrape_html(url)
    return out


_PMLR_CONF_ABBREVS = {
    "International Conference on Machine Learning":                         "ICML",
    "International Conference on Artificial Intelligence and Statistics":   "AISTATS",
    "Conference on Uncertainty in Artificial Intelligence":                 "UAI",
    "Conference on Learning Theory":                                        "COLT",
    "Asian Conference on Machine Learning":                                 "ACML",
    "Robot Learning":                                                       "CoRL",
    "Medical Imaging with Deep Learning":                                   "MIDL",
    "Symposium on Advances in Approximate Bayesian Inference":              "AABI",
    "Conference on Causal Learning and Reasoning":                          "CLeaR",
}


def _pmlr_rss(vol: str) -> tuple[dict, str]:
    """
    Fetch RSS feed for a PMLR volume.
    Returns ({paper_url: {title, abstract, pub_date}}, venue_string).
    venue_string is e.g. "ICML 2024" or "PMLR v235" as fallback.
    """
    from email.utils import parsedate
    rss_url = f"https://proceedings.mlr.press/v{vol}/assets/rss/feed.xml"
    try:
        r    = SESSION.get(rss_url, timeout=15)
        root = ET.fromstring(r.text)
    except Exception:
        return {}, f"PMLR v{vol}"

    # Extract venue name from channel description first line
    channel   = root.find("channel")
    chan_desc  = (channel.findtext("description") or "") if channel else ""
    first_line = chan_desc.strip().split("\n")[0].strip()
    # Strip "Proceedings of (the Nth) " prefix
    conf_long  = re.sub(r"^Proceedings of(?: the \d+(?:st|nd|rd|th)?)? ", "", first_line).strip()
    conf_short = _PMLR_CONF_ABBREVS.get(conf_long, "")

    url_to_meta: dict = {}
    for item in root.findall(".//item"):
        link = (item.findtext("link") or "").strip()
        if not link:
            continue
        pdate_raw = item.findtext("pubDate") or ""
        pub_date  = ""
        if pdate_raw:
            dt = parsedate(pdate_raw)
            if dt:
                pub_date = f"{dt[0]:04d}-{dt[1]:02d}-{dt[2]:02d}"
        url_to_meta[link] = {
            "title":    (item.findtext("title") or "").strip(),
            "abstract": (item.findtext("description") or "").strip(),
            "pub_date": pub_date,
        }

    year = ""
    if url_to_meta:
        year = next(iter(url_to_meta.values()))["pub_date"][:4]
    venue = f"{conf_short} {year}".strip() if conf_short and year else (
            f"{conf_long} {year}".strip() if conf_long and year else f"PMLR v{vol}")

    return url_to_meta, venue


def scrape_pmlr_volume(url: str) -> list:
    """
    PMLR volume page (proceedings.mlr.press/vNNN/) — scrapes titles+authors from HTML,
    then augments with abstracts and pub_dates from the volume RSS feed.
    """
    vol_m = re.search(r"/v(\d+)/", url)
    vol   = vol_m.group(1) if vol_m else ""

    # Fetch RSS for abstracts and venue name (one HTTP call for the whole volume)
    rss_meta, place = _pmlr_rss(vol) if vol else ({}, "PMLR")

    try:
        r    = SESSION.get(url, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        out  = []
        seen: set = set()
        for div in soup.find_all("div", class_="paper"):
            abs_a = div.find("a", string=re.compile(r"\babs\b", re.I))
            if not abs_a:
                continue
            href = abs_a.get("href", "").strip()
            if not href:
                continue
            paper_url = href if href.startswith("http") else "https://proceedings.mlr.press" + href
            if paper_url in seen:
                continue
            seen.add(paper_url)
            title_el   = div.find(class_="title") or div.find("p", class_="title")
            title      = title_el.get_text(strip=True) if title_el else abs_a.get_text(strip=True)
            authors_el = div.find(class_="authors") or div.find("span", class_="authors")
            authors    = authors_el.get_text(strip=True) if authors_el else ""
            rss        = rss_meta.get(paper_url, {})
            out.append({
                "paper_url": paper_url,
                "title":     rss.get("title") or title,
                "authors":   authors,
                "abstract":  rss.get("abstract", ""),
                "pub_date":  rss.get("pub_date", ""),
                "place":     place,
            })
        return out
    except Exception as e:
        print(f"    [!] PMLR: {e}", file=sys.stderr)
        return []


def scrape_neurips(url: str) -> list:
    """
    papers.nips.cc — static HTML archive of all NeurIPS papers.
    For years 2022+ also tries the OpenReview API when a year is in the URL.
    """
    year_m = re.search(r"/(\d{4})", url)
    place  = f"NeurIPS {year_m.group(1)}" if year_m else "NeurIPS"
    if year_m and int(year_m.group(1)) >= 2022:
        or_papers = scrape_openreview_venue(
            f"https://openreview.net/group?id=NeurIPS.cc/{year_m.group(1)}/Conference"
        )
        if or_papers:
            return or_papers
    try:
        papers = extract_papers(SESSION.get(url, timeout=20).text)
        for p in papers:
            p.setdefault("place", place)
        return papers
    except Exception as e:
        print(f"    [!] NeurIPS: {e}", file=sys.stderr)
        return []


def scrape_cvf(url: str) -> list:
    """
    CVF Open Access (openaccess.thecvf.com) — the full paper list is at ?day=all
    and contains arXiv IDs for most papers.  Falls back to the base URL if needed.
    """
    conf_m = re.search(r"/(CVPR|ICCV|ECCV|WACV)(\d{4})", url.upper())
    place  = f"{conf_m.group(1)} {conf_m.group(2)}" if conf_m else "CVF"
    # Ensure we request the full listing (the base URL is just a landing page)
    base  = url.split("?")[0].rstrip("/")
    urls_to_try = [f"{base}?day=all", base]
    for try_url in urls_to_try:
        try:
            papers = extract_papers(SESSION.get(try_url, timeout=60).text)
            if papers:
                for p in papers:
                    p.setdefault("place", place)
                return papers
        except Exception as e:
            print(f"    [!] CVF {try_url}: {e}", file=sys.stderr)
    return []


def scrape_jmlr(url: str) -> list:
    """JMLR — RSS at jmlr.org/jmlr.xml; falls back to HTML if RSS is empty."""
    try:
        feed = parse_feed("https://jmlr.org/jmlr.xml")
        out  = []
        for entry in feed.entries:
            pp       = entry.get("published_parsed")
            pub_date = f"{pp.tm_year:04d}-{pp.tm_mon:02d}-{pp.tm_mday:02d}" if pp else ""
            blob = rss_blob(entry)
            ax   = extract_papers(blob)
            if ax:
                for p in ax:
                    p.setdefault("pub_date", pub_date)
                    p.setdefault("place", "JMLR")
                out.extend(ax)
            else:
                paper_url = entry.get("link", "").strip()
                if not paper_url:
                    continue
                title   = entry.get("title", "")
                authors = ", ".join(a.get("name", "") for a in entry.get("authors", []))
                abstract = entry.get("summary", "")
                out.append({"paper_url": paper_url, "title": title,
                            "authors": authors, "abstract": abstract,
                            "pub_date": pub_date, "place": "JMLR"})
        if out:
            return out
    except Exception:
        pass
    print("    [–] JMLR RSS returned no entries; trying HTML", file=sys.stderr)
    return scrape_html(url)


def scrape_aaai_proceedings(url: str) -> list:
    """
    AAAI proceedings (ojs.aaai.org) — parses div.obj_article_summary elements
    which contain inline title, authors and paper URL on the issue page.
    Falls through to the latest issue when given the archive listing.
    """
    year_m = re.search(r"/(\d{4})(?:/|$)", url)
    place  = f"AAAI {year_m.group(1)}" if year_m else "AAAI"
    try:
        r    = SESSION.get(url, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        articles = soup.find_all("div", class_="obj_article_summary")
        if not articles:
            # We're on the archive listing; find and follow the latest issue link
            issue_hrefs = [
                a["href"] for a in soup.find_all("a", href=re.compile(r"/index\.php/AAAI/issue/view/\d+"))
                if a.get("href")
            ]
            if issue_hrefs:
                latest = issue_hrefs[0]
                if not latest.startswith("http"):
                    latest = "https://ojs.aaai.org" + latest
                r2   = SESSION.get(latest, timeout=20)
                soup = BeautifulSoup(r2.text, "html.parser")
                articles = soup.find_all("div", class_="obj_article_summary")
        out = []
        for div in articles:
            title_a = div.select_one("h3.title a") or div.select_one(".title a")
            if not title_a:
                continue
            title     = title_a.get_text(strip=True)
            paper_url = title_a.get("href", "").strip()
            if not paper_url.startswith("http"):
                paper_url = "https://ojs.aaai.org" + paper_url
            authors_el = div.select_one(".authors") or div.select_one(".meta .authors")
            authors    = authors_el.get_text(strip=True) if authors_el else ""
            if title and paper_url:
                out.append({"paper_url": paper_url, "title": title,
                            "authors": authors, "abstract": "", "place": place})
        return out
    except Exception as e:
        print(f"    [!] AAAI: {e}", file=sys.stderr)
        return []


def scrape_ijcai(url: str) -> list:
    """
    IJCAI proceedings — parses div.paper_wrapper elements which contain inline
    title, authors, and paper URL on proceedings listing pages.
    """
    year_m = re.search(r"/proceedings/(\d{4})", url)
    place  = f"IJCAI {year_m.group(1)}" if year_m else "IJCAI"
    try:
        r    = SESSION.get(url, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        wrappers = soup.find_all("div", class_="paper_wrapper")
        if not wrappers and not year_m:
            # Might be on the index page; follow the most recent proceedings link
            proc_hrefs = [
                a["href"] for a in soup.find_all("a", href=re.compile(r"/proceedings/\d{4}$"))
                if a.get("href")
            ]
            if proc_hrefs:
                latest = max(proc_hrefs)
                yr = re.search(r"/proceedings/(\d{4})", latest)
                if yr:
                    place = f"IJCAI {yr.group(1)}"
                full  = latest if latest.startswith("http") else "https://www.ijcai.org" + latest
                r2    = SESSION.get(full, timeout=20)
                soup  = BeautifulSoup(r2.text, "html.parser")
                wrappers = soup.find_all("div", class_="paper_wrapper")
        out = []
        for div in wrappers:
            title_el   = div.find("div", class_="title")
            authors_el = div.find("div", class_="authors")
            title   = title_el.get_text(strip=True)   if title_el   else ""
            authors = authors_el.get_text(strip=True)  if authors_el else ""
            if not title:
                continue
            details = div.find("div", class_="details")
            link_a  = details.find("a", href=re.compile(r"/proceedings/\d{4}/\d+")) if details else None
            if not link_a:
                continue
            href      = link_a.get("href", "")
            paper_url = href if href.startswith("http") else "https://www.ijcai.org" + href
            out.append({"paper_url": paper_url, "title": title,
                        "authors": authors, "abstract": "", "place": place})
        return out
    except Exception as e:
        print(f"    [!] IJCAI: {e}", file=sys.stderr)
        return []


def scrape_ecva(url: str) -> list:
    """
    ECVA papers page (ecva.net) — parses dt.ptitle / dd pairs where each dt
    holds a link with the paper title and each dd starts with the author list.
    """
    try:
        r    = SESSION.get(url, timeout=30)
        soup = BeautifulSoup(r.text, "html.parser")
        out  = []
        seen = set()
        base = "https://www.ecva.net"
        for dt in soup.find_all("dt", class_="ptitle"):
            a = dt.find("a", href=True)
            if not a:
                continue
            title = a.get_text(strip=True)
            href  = a.get("href", "")
            paper_url = href if href.startswith("http") else base + "/" + href.lstrip("/")
            if paper_url in seen:
                continue
            seen.add(paper_url)
            # Infer venue from the href (e.g. /papers/eccv_2024/papers_ECCV/...)
            yr_m  = re.search(r"(?:eccv|cvpr|iccv)_(\d{4})", href, re.I)
            conf  = re.search(r"/(eccv|cvpr|iccv)_", href, re.I)
            cname = conf.group(1).upper() if conf else "ECCV"
            place = f"{cname} {yr_m.group(1)}" if yr_m else cname
            # Authors are text nodes in the immediately following <dd>
            dd      = dt.find_next_sibling("dd")
            authors = ""
            if dd:
                # Collect direct text before any <a> links (PDFs come after author names)
                parts = []
                for child in dd.children:
                    if hasattr(child, "name") and child.name == "a":
                        break
                    text = str(child).strip()
                    if text:
                        parts.append(text)
                authors = " ".join(parts).strip()
            out.append({"paper_url": paper_url, "title": title,
                        "authors": authors, "abstract": "", "place": place})
        return out
    except Exception as e:
        print(f"    [!] ECVA: {e}", file=sys.stderr)
        return []


def scrape_isca(url: str) -> list:
    """
    ISCA Archive (isca-archive.org) — extracts paper links and inline metadata.
    Each paper link (<a class="w3-text">) contains a <p> with the title as the
    first text node and author names inside a <span class="w3-text-theme">.
    """
    year_m = re.search(r"(\d{4})", url)
    conf_m = re.search(r"interspeech|icassp|eurospeech", url, re.I)
    cname  = conf_m.group(0).upper() if conf_m else "ISCA"
    place  = f"{cname} {year_m.group(1)}" if year_m else cname
    try:
        r    = SESSION.get(url, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        base = url.rstrip("/") + "/"
        out  = []
        seen = set()
        pat  = re.compile(r"[\w]+_(?:interspeech|icassp|eurospeech|\d{4})[\w]*\.html$", re.I)
        for a in soup.find_all("a", href=pat):
            href  = a.get("href", "").strip()
            if not href:
                continue
            paper_url = href if href.startswith("http") else base + href.lstrip("/")
            if paper_url in seen:
                continue
            seen.add(paper_url)
            title   = ""
            authors = ""
            p_el = a.find("p")
            if p_el:
                # Title: direct text before the <br> or author <span>
                parts = []
                for child in p_el.children:
                    if hasattr(child, "name") and child.name in ("br", "span"):
                        break
                    text = str(child).strip()
                    if text:
                        parts.append(text)
                title = " ".join(parts).strip()
                span    = p_el.find("span", class_="w3-text-theme")
                authors = span.get_text(strip=True) if span else ""
            if not title:
                title = a.get_text(strip=True)
            out.append({"paper_url": paper_url, "title": title,
                        "authors": authors, "abstract": "", "place": place})
        return out
    except Exception as e:
        print(f"    [!] ISCA: {e}", file=sys.stderr)
        return []


def scrape_ieee_journal(url: str) -> list:
    """
    IEEE Xplore journal — uses per-journal RSS (TOC{punumber}.XML).
    Returns arXiv papers when available; falls back to IEEE Xplore link from RSS.
    """
    pn = re.search(r"punumber=(\d+)", url)
    if pn:
        rss_url = f"https://ieeexplore.ieee.org/rss/TOC{pn.group(1)}.XML"
        try:
            feed = parse_feed(rss_url)
            journal_name = feed.feed.get("title", "")
            out  = []
            for entry in feed.entries[:60]:
                pp       = entry.get("published_parsed")
                pub_date = f"{pp.tm_year:04d}-{pp.tm_mon:02d}-{pp.tm_mday:02d}" if pp else ""
                blob = rss_blob(entry)
                ax   = extract_papers(blob)
                if ax:
                    for p in ax:
                        p.setdefault("pub_date", pub_date)
                        p.setdefault("place", journal_name)
                    out.extend(ax)
                else:
                    link  = entry.get("link", "").strip()
                    title = entry.get("title", "")
                    auth  = ", ".join(a.get("name", "") for a in entry.get("authors", []))
                    abstr = entry.get("summary", "")
                    if link:
                        out.append({"paper_url": link, "title": title,
                                    "authors": auth, "abstract": abstr,
                                    "pub_date": pub_date, "place": journal_name})
            if out:
                return out
        except Exception:
            pass
    print(f"    [–] IEEE journal: RSS unavailable for {url}", file=sys.stderr)
    return []


def scrape_springer_journal(url: str) -> list:
    """
    Springer journal (link.springer.com/journal/N) — uses Springer RSS.
    Returns arXiv papers when available; falls back to Springer article link.
    """
    jn = re.search(r"journal/(\d+)", url)
    if jn:
        rss_url = (
            f"https://link.springer.com/search.rss"
            f"?query=&search-within=Journal&facet-journal-id={jn.group(1)}&sortOrder=newestFirst"
        )
        try:
            feed = parse_feed(rss_url)
            journal_name = feed.feed.get("title", "")
            out  = []
            for entry in feed.entries[:30]:
                pp       = entry.get("published_parsed")
                pub_date = f"{pp.tm_year:04d}-{pp.tm_mon:02d}-{pp.tm_mday:02d}" if pp else ""
                blob = rss_blob(entry)
                ax   = extract_papers(blob)
                if ax:
                    for p in ax:
                        p.setdefault("pub_date", pub_date)
                        p.setdefault("place", journal_name)
                    out.extend(ax)
                else:
                    link  = entry.get("link", "").strip()
                    title = entry.get("title", "")
                    auth  = ", ".join(a.get("name", "") for a in entry.get("authors", []))
                    abstr = entry.get("summary", "")
                    if link:
                        out.append({"paper_url": link, "title": title,
                                    "authors": auth, "abstract": abstr,
                                    "pub_date": pub_date, "place": journal_name})
            if out:
                return out
        except Exception:
            pass
    return scrape_rss(url) or scrape_html(url)


def scrape_nature_journal(url: str) -> list:
    """
    Nature journal (nature.com/SLUG) — uses Nature RSS at /SLUG.rss.
    Returns arXiv papers when available; falls back to Nature article link.
    """
    slug    = urlparse(url).path.strip("/")
    rss_url = f"https://www.nature.com/{slug}.rss"
    try:
        feed = parse_feed(rss_url)
        journal_name = feed.feed.get("title", "")
        out  = []
        for entry in feed.entries[:30]:
            pp       = entry.get("published_parsed")
            pub_date = f"{pp.tm_year:04d}-{pp.tm_mon:02d}-{pp.tm_mday:02d}" if pp else ""
            blob = rss_blob(entry)
            ax   = extract_papers(blob)
            if ax:
                for p in ax:
                    p.setdefault("pub_date", pub_date)
                    p.setdefault("place", journal_name)
                out.extend(ax)
            else:
                link  = entry.get("link", "").strip()
                title = entry.get("title", "")
                auth  = ", ".join(a.get("name", "") for a in entry.get("authors", []))
                abstr = entry.get("summary", "")
                if link:
                    out.append({"paper_url": link, "title": title,
                                "authors": auth, "abstract": abstr,
                                "pub_date": pub_date, "place": journal_name})
        if out:
            return out
    except Exception:
        pass
    return scrape_rss(url)


def scrape_elsevier_journal(url: str) -> list:
    """
    Elsevier ScienceDirect journal — tries common RSS patterns.
    Returns arXiv papers when available; falls back to ScienceDirect article link.
    """
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    for rss_url in (
        f"https://www.sciencedirect.com/journal/{slug}/rss",
        f"https://rss.sciencedirect.com/publication/science/{slug}",
    ):
        try:
            feed = parse_feed(rss_url)
            if not feed.entries:
                continue
            journal_name = feed.feed.get("title", "")
            out = []
            for entry in feed.entries[:30]:
                pp       = entry.get("published_parsed")
                pub_date = f"{pp.tm_year:04d}-{pp.tm_mon:02d}-{pp.tm_mday:02d}" if pp else ""
                blob = rss_blob(entry)
                ax   = extract_papers(blob)
                if ax:
                    for p in ax:
                        p.setdefault("pub_date", pub_date)
                        p.setdefault("place", journal_name)
                    out.extend(ax)
                else:
                    link  = entry.get("link", "").strip()
                    title = entry.get("title", "")
                    auth  = ", ".join(a.get("name", "") for a in entry.get("authors", []))
                    abstr = entry.get("summary", "")
                    if link:
                        out.append({"paper_url": link, "title": title,
                                    "authors": auth, "abstract": abstr,
                                    "pub_date": pub_date, "place": journal_name})
            if out:
                return out
        except Exception:
            pass
    print(f"    [–] Elsevier: no accessible RSS for {url}", file=sys.stderr)
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

    if "openreview.net" in host and ("group?id=" in full or "venue?id=" in full):
                                                   return scrape_openreview_venue(url)
    if "proceedings.mlr.press"  in host:           return scrape_pmlr_volume(url)
    if "papers.nips.cc"         in host:           return scrape_neurips(url)
    if "openaccess.thecvf.com"  in host:           return scrape_cvf(url)
    if "jmlr.org"               in host:           return scrape_jmlr(url)
    if "ojs.aaai.org"           in host:           return scrape_aaai_proceedings(url)
    if "ijcai.org"              in host:           return scrape_ijcai(url)
    if "ecva.net"               in host:           return scrape_ecva(url)
    if "isca-archive.org"       in host:           return scrape_isca(url)
    if "ieeexplore.ieee.org"    in host:           return scrape_ieee_journal(url)
    if "link.springer.com"      in host and ("/journal/" in full or "/conference/" in full):
                                                   return scrape_springer_journal(url)
    if "nature.com"             in host:           return scrape_nature_journal(url)
    if "sciencedirect.com"      in host:           return scrape_elsevier_journal(url)

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
    ("tier 1 conference",   "conferences"),
    ("tier 2 conference",   "conferences"),
    ("tier 1 journal",      "journals"),
    ("tier 2 journal",      "journals"),
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

# ── Backfill ───────────────────────────────────────────────────────────────────
def backfill_metadata(paths: list) -> None:
    """
    Re-enrich any row missing title, abstract, or pub_date.
    Also migrates old TSV files that lack pub_date/place columns.
    Only calls the arXiv/OpenReview API for papers with those URLs.
    """
    for path in paths:
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as f:
            reader     = csv.DictReader(f, delimiter="\t")
            fieldnames = list(reader.fieldnames or [])
            rows       = list(reader)

        # Migrate old TSVs to the current SEEN_FIELDS schema
        changed_schema = False
        for col in SEEN_FIELDS:
            if col not in fieldnames:
                fieldnames.append(col)
                for row in rows:
                    row.setdefault(col, "")
                changed_schema = True
        if changed_schema:
            print(f"  {path.name}: migrated schema ({', '.join(c for c in SEEN_FIELDS if c not in (fieldnames or []))})", flush=True)

        SCRAPEABLE = (
            "proceedings.mlr.press", "ojs.aaai.org",
            "ijcai.org/proceedings", "ecva.net/papers",
            "aclanthology.org",
        )

        def needs_backfill(row: dict) -> bool:
            url = row.get("paper_url", "")
            has_source = bool(
                ARXIV_RE.search(url)
                or OPENREVIEW_RE.search(url)
                or any(h in url for h in SCRAPEABLE)
            )
            missing = (
                not row.get("title", "").strip()
                or not row.get("abstract", "").strip()
                or not row.get("pub_date", "").strip()
            )
            return has_source and missing

        candidates = [i for i, row in enumerate(rows) if needs_backfill(row)]
        if not candidates and not changed_schema:
            print(f"  {path.name}: nothing to backfill", flush=True)
            continue
        if not candidates:
            # Schema was migrated but no API calls needed — just rewrite
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=SEEN_FIELDS, delimiter="\t",
                                        restval="", extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
            continue

        print(f"  {path.name}: backfilling {len(candidates)} rows …", flush=True)
        fixed = 0
        for i in candidates:
            row = rows[i]
            p   = {
                "paper_url":  row.get("paper_url", ""),
                "title":      row.get("title", ""),
                "authors":    row.get("authors", ""),
                "abstract":   row.get("abstract", ""),
                "keywords":   row.get("keywords", ""),
                "pub_date":   row.get("pub_date", ""),
                "place":      row.get("place", ""),
            }
            p = enrich(p)
            if p.get("title") or p.get("abstract") or p.get("pub_date"):
                rows[i]["title"]    = p.get("title",    "") or row.get("title",    "")
                rows[i]["authors"]  = p.get("authors",  "") or row.get("authors",  "")
                rows[i]["abstract"] = p.get("abstract", "") or row.get("abstract", "")
                rows[i]["keywords"] = p.get("keywords", "") or row.get("keywords", "")
                rows[i]["pub_date"] = p.get("pub_date", "") or row.get("pub_date", "")
                rows[i]["place"]    = p.get("place",    "") or row.get("place",    "")
                fixed += 1

        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=SEEN_FIELDS, delimiter="\t",
                                    restval="", extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        print(f"    → enriched {fixed}/{len(candidates)} rows", flush=True)

    # Second pass: PMLR papers — batch-fetch one RSS per volume
    print("\nPMLR RSS backfill …", flush=True)
    _backfill_pmlr_rss(paths)


def _backfill_pmlr_rss(paths: list) -> None:
    """
    For PMLR papers missing abstract/place in the given TSV files, fetch each volume's
    RSS feed once and batch-update matching rows.  One HTTP request per volume.
    """
    PMLR_RE = re.compile(r"proceedings\.mlr\.press/v(\d+)/")

    for path in paths:
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as f:
            reader     = csv.DictReader(f, delimiter="\t")
            fieldnames = list(reader.fieldnames or [])
            rows       = list(reader)

        # Group row indices by volume that need backfill
        vol_to_indices: dict = {}
        for i, row in enumerate(rows):
            url = row.get("paper_url", "")
            m   = PMLR_RE.search(url)
            if not m:
                continue
            if row.get("abstract", "").strip() and row.get("place", "").strip():
                continue  # already complete
            vol_to_indices.setdefault(m.group(1), []).append(i)

        if not vol_to_indices:
            print(f"  {path.name}: no PMLR rows to backfill", flush=True)
            continue

        total_fixed = 0
        for vol, indices in sorted(vol_to_indices.items()):
            print(f"  {path.name}: v{vol} — fetching RSS for {len(indices)} papers …", flush=True)
            rss_meta, venue = _pmlr_rss(vol)
            if not rss_meta:
                print(f"    [!] no RSS data for v{vol}", flush=True)
                continue
            fixed = 0
            for i in indices:
                url  = rows[i].get("paper_url", "")
                meta = rss_meta.get(url, {})
                if meta.get("abstract") and not rows[i].get("abstract", "").strip():
                    rows[i]["abstract"] = meta["abstract"]
                if meta.get("title") and not rows[i].get("title", "").strip():
                    rows[i]["title"] = meta["title"]
                if meta.get("pub_date") and not rows[i].get("pub_date", "").strip():
                    rows[i]["pub_date"] = meta["pub_date"]
                if not rows[i].get("place", "").strip():
                    rows[i]["place"] = venue
                fixed += 1
            total_fixed += fixed
            print(f"    → updated {fixed}/{len(indices)} rows  (venue: {venue})", flush=True)

        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=SEEN_FIELDS, delimiter="\t",
                                    restval="", extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        print(f"  {path.name}: PMLR done ({total_fixed} total rows updated)", flush=True)


# ── Diagnostic helpers ─────────────────────────────────────────────────────────

def diagnose_missing(paths: list) -> None:
    """
    Break down papers missing title/abstract by URL type, and report how many
    the backfill would attempt vs. can't touch.
    """
    import re as _re
    SCRAPEABLE = (
        "proceedings.mlr.press", "ojs.aaai.org",
        "ijcai.org/proceedings", "ecva.net/papers", "aclanthology.org",
    )
    buckets: dict = {
        "arxiv":          {"total": 0, "no_title": 0, "no_abstract": 0},
        "openreview":     {"total": 0, "no_title": 0, "no_abstract": 0},
        "pmlr":           {"total": 0, "no_title": 0, "no_abstract": 0},
        "aaai":           {"total": 0, "no_title": 0, "no_abstract": 0},
        "acl":            {"total": 0, "no_title": 0, "no_abstract": 0},
        "other_scrapeable":{"total": 0,"no_title": 0, "no_abstract": 0},
        "no_api":         {"total": 0, "no_title": 0, "no_abstract": 0},
    }
    sample_no_api: list = []

    for path in paths:
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                url  = row.get("paper_url", "")
                has_t = bool(row.get("title", "").strip())
                has_a = bool(row.get("abstract", "").strip())
                if ARXIV_RE.search(url):
                    b = "arxiv"
                elif OPENREVIEW_RE.search(url):
                    b = "openreview"
                elif "proceedings.mlr.press" in url:
                    b = "pmlr"
                elif "ojs.aaai.org" in url:
                    b = "aaai"
                elif "aclanthology.org" in url:
                    b = "acl"
                elif any(h in url for h in SCRAPEABLE):
                    b = "other_scrapeable"
                else:
                    b = "no_api"
                    if not has_t and len(sample_no_api) < 5:
                        sample_no_api.append(url)
                buckets[b]["total"] += 1
                if not has_t: buckets[b]["no_title"] += 1
                if not has_a: buckets[b]["no_abstract"] += 1

    print(f"\n{'Source':<20} {'total':>7} {'no title':>9} {'no abstract':>12}", flush=True)
    print("-" * 52, flush=True)
    for name, d in buckets.items():
        if d["total"]:
            print(f"  {name:<18} {d['total']:>7,} {d['no_title']:>9,} {d['no_abstract']:>12,}", flush=True)

    if sample_no_api:
        print(f"\nSample 'no_api' URLs (can't auto-enrich):", flush=True)
        for u in sample_no_api:
            print(f"  {u}", flush=True)


def test_enrich_url(url: str) -> None:
    """Fetch metadata for a single URL and print what enrich() returns."""
    print(f"Testing enrich for: {url}\n", flush=True)
    p = {"paper_url": url, "title": "", "authors": "", "abstract": "",
         "keywords": "", "pub_date": "", "place": ""}
    result = enrich(p)
    for field in ("title", "authors", "abstract", "pub_date", "place", "keywords"):
        val = result.get(field, "")
        if val:
            snippet = val[:120].replace("\n", " ")
            print(f"  {field:<12}: {snippet}", flush=True)
        else:
            print(f"  {field:<12}: (empty)", flush=True)


def test_pmlr_vol(vol: str) -> None:
    """Fetch the RSS for one PMLR volume and show what metadata comes back."""
    print(f"Fetching PMLR RSS for volume {vol} …", flush=True)
    meta, venue = _pmlr_rss(vol)
    print(f"  Venue : {venue}", flush=True)
    print(f"  Papers: {len(meta)}", flush=True)
    for url, d in list(meta.items())[:5]:
        title = (d.get("title") or "")[:80]
        print(f"  {url}", flush=True)
        print(f"    title: {title}", flush=True)


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
        choices=["curated", "uncurated", "educational", "corporate", "conferences", "journals", "all"],
        help="Source groups to check: curated, uncurated, educational, corporate, conferences, journals, all",
    )
    parser.add_argument(
        "--backfill", action="store_true",
        help="Re-enrich rows missing title/abstract in all seen/new TSV files, then exit",
    )
    parser.add_argument(
        "--diagnose", action="store_true",
        help="Report which URL types are missing titles/abstracts and whether backfill can help",
    )
    parser.add_argument(
        "--test-enrich", metavar="URL",
        help="Run enrich() on a single URL and print the result",
    )
    parser.add_argument(
        "--test-pmlr", metavar="VOL",
        help="Fetch PMLR RSS for a specific volume number and show sample results",
    )
    args = parser.parse_args()

    if args.diagnose:
        paths = (sorted(PAPERS_DIR.glob("seen_papers_*.tsv"))
                 + sorted(PAPERS_DIR.glob("new_papers_*.tsv")))
        diagnose_missing(paths)
        return

    if args.test_enrich:
        test_enrich_url(args.test_enrich)
        return

    if args.test_pmlr:
        test_pmlr_vol(args.test_pmlr)
        return

    if args.backfill:
        paths = (sorted(PAPERS_DIR.glob("seen_papers_*.tsv"))
                 + sorted(PAPERS_DIR.glob("new_papers_*.tsv")))
        print(f"Backfilling metadata across {len(paths)} file(s) …\n", flush=True)
        backfill_metadata(paths)
        return

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
                "pub_date":    p.get("pub_date", ""),
                "place":       p.get("place", ""),
                "category":    "",
                "viewed":      "",
                "read":        "",
                "bookmarked":  "",
                "labelled":    "",
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
            w = csv.DictWriter(f, fieldnames=SEEN_FIELDS, delimiter="\t",
                               restval="", extrasaction="ignore")
            w.writeheader()
            w.writerows(new_rows)
        print(f"\n{len(new_rows)} new papers written to {OUT_FILE.relative_to(ROOT)}")
    else:
        print("\nNo new papers found.")

if __name__ == "__main__":
    main()
