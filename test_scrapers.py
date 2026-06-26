#!/usr/bin/env python3
"""
test_scrapers.py

Tests each scraper in paper_searcher.py with a known-good URL,
then calls enrich() on a small sample to verify the full metadata pipeline.

Usage:
    python test_scrapers.py                     # test all scrapers, enrich 1 per scraper
    python test_scrapers.py lab-openai          # filter by name substring
    python test_scrapers.py --no-enrich         # skip enrich() API calls (fastest)
    python test_scrapers.py --enrich-n=3        # enrich 3 papers per scraper

Output is flushed after each scraper so you can tail -f the log.
"""

import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from paper_searcher import (
    scrape_huggingface, scrape_paperswithcode, scrape_arxiv_listing,
    scrape_arxiv_sanity, scrape_distill, scrape_alignmentforum,
    scrape_openreview_venue, scrape_pmlr_volume, scrape_neurips, scrape_cvf,
    scrape_jmlr, scrape_aaai_proceedings, scrape_ijcai, scrape_ecva, scrape_isca,
    scrape_ieee_journal, scrape_springer_journal, scrape_nature_journal,
    scrape_elsevier_journal, scrape_semantic_scholar, scrape_reddit,
    scrape_substack, scrape_wordpress, scrape_github_readme,
    scrape_lab_page, scrape_the_batch, enrich,
)

FIELDS = ["title", "authors", "abstract", "pub_date", "place", "keywords"]

# (display-name, scraper-function, test-url)
SCRAPERS = [
    # ── Curated aggregators ────────────────────────────────────────────────────
    ("huggingface",       scrape_huggingface,      "https://huggingface.co/papers"),
    ("paperswithcode",    scrape_paperswithcode,   "https://paperswithcode.com"),
    ("arxiv-cs.LG",       scrape_arxiv_listing,    "https://arxiv.org/list/cs.LG/recent"),
    ("arxiv-sanity",      scrape_arxiv_sanity,     "https://arxiv-sanity-lite.com"),
    ("distill",           scrape_distill,          "https://distill.pub"),
    ("alignmentforum",    scrape_alignmentforum,   "https://www.alignmentforum.org"),
    ("reddit-ml",         scrape_reddit,           "https://www.reddit.com/r/MachineLearning/"),
    ("the-batch",         scrape_the_batch,        "https://www.deeplearning.ai/the-batch/"),
    ("github-dair",       scrape_github_readme,    "https://github.com/dair-ai/ML-Papers-of-the-Week"),
    ("semanticscholar",   scrape_semantic_scholar, "https://www.semanticscholar.org"),
    # ── Conferences ────────────────────────────────────────────────────────────
    ("openreview-iclr24", scrape_openreview_venue, "https://openreview.net/group?id=ICLR.cc/2024/Conference"),
    ("pmlr-v235-icml24",  scrape_pmlr_volume,      "https://proceedings.mlr.press/v235/"),
    ("neurips-2023",      scrape_neurips,          "https://papers.nips.cc/paper_files/paper/2023"),
    ("cvf-cvpr2024",      scrape_cvf,              "https://openaccess.thecvf.com/CVPR2024"),
    ("aaai",              scrape_aaai_proceedings, "https://ojs.aaai.org/index.php/AAAI/issue/archive"),
    ("ijcai-2024",        scrape_ijcai,            "https://www.ijcai.org/proceedings/2024"),
    ("ecva",              scrape_ecva,             "https://www.ecva.net/papers.php"),
    ("interspeech-2023",  scrape_isca,             "https://www.isca-archive.org/interspeech_2023/"),
    # ── Journals ───────────────────────────────────────────────────────────────
    ("jmlr",              scrape_jmlr,             "https://jmlr.org"),
    ("ieee-tpami",        scrape_ieee_journal,     "https://ieeexplore.ieee.org/xpl/RecentIssue.jsp?punumber=34"),
    ("ieee-tnnls",        scrape_ieee_journal,     "https://ieeexplore.ieee.org/xpl/RecentIssue.jsp?punumber=5962385"),
    ("springer-ml",       scrape_springer_journal, "https://link.springer.com/journal/10994"),
    ("nature-mi",         scrape_nature_journal,   "https://www.nature.com/natmachintell"),
    ("elsevier-nn",       scrape_elsevier_journal, "https://www.sciencedirect.com/journal/neural-networks"),
    # ── Lab pages (industry) ───────────────────────────────────────────────────
    ("lab-openai",        scrape_lab_page,         "https://openai.com/research"),
    ("lab-deepmind",      scrape_lab_page,         "https://deepmind.google"),
    ("lab-anthropic",     scrape_lab_page,         "https://www.anthropic.com"),
    ("lab-meta-ai",       scrape_lab_page,         "https://ai.meta.com"),
    ("lab-google-res",    scrape_lab_page,         "https://research.google"),
    ("lab-microsoft",     scrape_lab_page,         "https://www.microsoft.com/en-us/research/"),
    ("lab-allenai",       scrape_lab_page,         "https://allenai.org"),
    ("lab-eleuther",      scrape_lab_page,         "https://www.eleuther.ai"),
    # ── Lab pages (academic) ───────────────────────────────────────────────────
    ("lab-bair",          scrape_lab_page,         "https://bair.berkeley.edu"),
    ("lab-mila",          scrape_lab_page,         "https://mila.quebec"),
    ("lab-stanford-ai",   scrape_lab_page,         "https://ai.stanford.edu"),
    ("lab-mit-csail",     scrape_lab_page,         "https://www.csail.mit.edu"),
    ("lab-cmu-ml",        scrape_lab_page,         "https://www.ml.cmu.edu"),
]


def field_presence(papers: list) -> dict:
    return {f: sum(1 for p in papers if p.get(f)) for f in FIELDS}


def run_test(name: str, fn, url: str, enrich_n: int = 1) -> dict:
    print(f"\n{'─' * 68}")
    print(f"  [{name}]")
    print(f"  {url}")
    sys.stdout.flush()

    t0 = time.time()
    try:
        papers = fn(url)
    except Exception as e:
        print(f"  ✗ SCRAPE ERROR: {e}")
        traceback.print_exc()
        sys.stdout.flush()
        return {"name": name, "ok": False, "count": 0, "error": str(e)}
    elapsed = time.time() - t0

    n = len(papers)
    tick = "✓" if n > 0 else "✗"
    print(f"  {tick} {n} papers returned  ({elapsed:.1f}s)")

    if n == 0:
        sys.stdout.flush()
        return {"name": name, "ok": False, "count": 0}

    # Raw presence across all papers
    raw = field_presence(papers)
    raw_str = "  ".join(f"{f}={raw[f]}/{n}" for f in FIELDS if raw[f] > 0)
    print(f"  Raw fields:      {raw_str or 'none populated'}")
    print(f"  Example URL:     {papers[0].get('paper_url', '')[:80]}")
    sys.stdout.flush()

    if enrich_n <= 0:
        return {"name": name, "ok": True, "count": n}

    # Enrich a small sample
    sample = [dict(p) for p in papers[:enrich_n]]
    enriched = []
    for p in sample:
        try:
            enriched.append(enrich(p))
        except Exception as e:
            print(f"    enrich error: {e}")

    if enriched:
        e_pres = field_presence(enriched)
        e_str = "  ".join(f"{f}={e_pres[f]}/{len(enriched)}" for f in FIELDS)
        print(f"  After enrich({len(enriched)}):  {e_str}")
        e0 = enriched[0]
        for f in FIELDS:
            v = str(e0.get(f, "")).strip()
            if v:
                print(f"    {f:10}: {v[:80]}")

    sys.stdout.flush()
    return {"name": name, "ok": True, "count": n,
            "enriched_ok": len(enriched),
            "missing_after_enrich": [f for f in FIELDS if not enriched[0].get(f)] if enriched else FIELDS}


def main() -> None:
    args = sys.argv[1:]
    do_enrich  = "--no-enrich" not in args
    enrich_n   = 1
    filter_str = None

    for a in args:
        if a.startswith("--enrich-n="):
            enrich_n = int(a.split("=", 1)[1])
        elif not a.startswith("--"):
            filter_str = a.lower()

    print("=" * 68)
    print("paper_searcher.py — scraper test suite")
    print(f"enrich_n={enrich_n if do_enrich else 0}  "
          f"filter={filter_str or 'all'}  "
          f"scrapers={len(SCRAPERS)}")
    print("=" * 68)
    sys.stdout.flush()

    start = time.time()
    results = []
    for name, fn, url in SCRAPERS:
        if filter_str and filter_str not in name.lower():
            continue
        r = run_test(name, fn, url, enrich_n=enrich_n if do_enrich else 0)
        results.append(r)
        time.sleep(1.5)

    # ── Summary ────────────────────────────────────────────────────────────────
    ok  = [r for r in results if r.get("ok")]
    bad = [r for r in results if not r.get("ok")]
    total_elapsed = time.time() - start

    print(f"\n\n{'=' * 68}")
    print(f"SUMMARY  ({total_elapsed:.0f}s total)")
    print(f"{'=' * 68}")
    print(f"  Passed: {len(ok)}/{len(results)}")

    print(f"\n  {'Scraper':<25} {'Papers':>7}  {'Missing after enrich'}")
    for r in results:
        tick    = "✓" if r.get("ok") else "✗"
        count   = r.get("count", 0)
        missing = r.get("missing_after_enrich")
        err     = f"  ERROR: {r['error']}" if r.get("error") else ""
        miss_str = ", ".join(missing) if missing else ""
        print(f"  {tick} {r['name']:<25} {count:>7}  {miss_str}{err}")

    if bad:
        print(f"\n  FAILED scrapers ({len(bad)}):")
        for r in bad:
            print(f"    ✗ {r['name']}: {r.get('error', 'returned 0 papers')}")

    print()


if __name__ == "__main__":
    main()
