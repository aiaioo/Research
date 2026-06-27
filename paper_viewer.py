#!/usr/bin/env python3
"""
paper_viewer.py — Web app to browse AI/ML papers from the TSV files.

Usage:
    python paper_viewer.py            # http://localhost:5000
    python paper_viewer.py --port 8080
"""

import argparse
import csv
from collections import defaultdict
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template_string, request, url_for

ROOT       = Path(__file__).parent
PAPERS_DIR = ROOT / "papers"

CATEGORIES = ["vision", "training", "models", "memory", "safety", "voice"]
ALL_TABS   = sorted(CATEGORIES) + ["others"]
PAGE_SIZE  = 100

USER_FIELDS = {"viewed", "read", "bookmarked", "labelled", "category"}

app    = Flask(__name__)
_cache: list | None = None


# ── Data loading ───────────────────────────────────────────────────────────────

def load_papers() -> list:
    global _cache
    if _cache is not None:
        return _cache
    by_url: dict = {}
    for path in (sorted(PAPERS_DIR.glob("seen_papers_*.tsv"))
                 + sorted(PAPERS_DIR.glob("new_papers_*.tsv"))):
        try:
            with path.open(newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f, delimiter="\t"):
                    url = row.get("paper_url", "").strip()
                    if url and url not in by_url:
                        by_url[url] = dict(row)
        except Exception:
            pass
    papers = list(by_url.values())

    def _sort_key(p: dict) -> str:
        d = (p.get("pub_date") or "").strip()
        if not d:
            ds = (p.get("date_seen") or "").strip()
            d = f"{ds[:4]}-{ds[4:6]}-{ds[6:]}" if len(ds) == 8 else ds
        return d

    papers.sort(key=_sort_key, reverse=True)
    _cache = papers
    return papers


def group_by_tab(papers: list) -> dict:
    groups: dict = defaultdict(list)
    for p in papers:
        cat = (p.get("category") or "").strip()
        groups[cat if cat in CATEGORIES else "others"].append(p)
    return groups


def build_page_range(page: int, total: int) -> list[int]:
    if total <= 9:
        return list(range(1, total + 1))
    near    = set(range(max(1, page - 2), min(total + 1, page + 3)))
    anchors = {1, 2, total - 1, total}
    result, prev = [], None
    for pg in sorted(near | anchors):
        if prev and pg - prev > 1:
            result.append(-1)
        result.append(pg)
        prev = pg
    return result


def update_paper_field(paper_url: str, updates: dict) -> bool:
    """Write field updates for one paper into whichever TSV file contains it."""
    if not all(f in USER_FIELDS for f in updates):
        return False
    for path in (sorted(PAPERS_DIR.glob("seen_papers_*.tsv"))
                 + sorted(PAPERS_DIR.glob("new_papers_*.tsv"))):
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as f:
            reader     = csv.DictReader(f, delimiter="\t")
            fieldnames = list(reader.fieldnames or [])
            rows       = list(reader)
        for col in ("viewed", "read", "bookmarked", "labelled"):
            if col not in fieldnames:
                fieldnames.append(col)
                for r in rows:
                    r.setdefault(col, "")
        found = False
        for row in rows:
            if row.get("paper_url", "").strip() == paper_url:
                for field, value in updates.items():
                    row[field] = value
                found = True
                break
        if found:
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t",
                                        restval="", extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
            if _cache is not None:
                for p in _cache:
                    if p.get("paper_url") == paper_url:
                        p.update(updates)
                        break
            return True
    return False


# ── HTML template ──────────────────────────────────────────────────────────────

TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI/ML Papers</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"
        rel="stylesheet">
  <style>
    /* ── Theme tokens ─────────────────────────────────────────────────────────
       Light: clean, cool-tinted whites — airy, low-noise
       Dark:  deep blue-black (not gray) — premium feel, intentional depth
    */
    :root {
      --bg:         #f5f7fc;
      --surface:    #ffffff;
      --border:     #dce1ee;
      --text:       #0d1121;
      --text-sub:   #2a3252;
      --text-muted: #5e6882;
      --primary:    #1d52d6;
      --primary-bg: rgba(29,82,214,.07);
      --filter-bg:  #eaecf6;
      --shadow:     0 1px 2px rgba(0,0,0,.04), 0 2px 5px rgba(0,0,0,.06);
      --accent-v:   #1d52d6;
      --accent-b:   #b45309;
    }
    html.dark {
      --bg:         #080c14;
      --surface:    #0d1220;
      --border:     #1e2a42;
      --text:       #dde5f8;
      --text-sub:   #9eadd4;
      --text-muted: #6879a0;
      --primary:    #7bb3ff;
      --primary-bg: rgba(123,179,255,.08);
      --filter-bg:  #0a0f1a;
      --shadow:     none;
      --accent-v:   #7bb3ff;
      --accent-b:   #f59e0b;
    }

    /* ── Reset & base ─────────────────────────────────────────────────────── */
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: 'Inter', system-ui, sans-serif;
      font-size: .78rem;              /* sz-text — base size for all body text */
      background: var(--bg);
      color: var(--text);
      line-height: 1.55;
      transition: background .2s, color .2s;
    }

    /* ── Three font sizes (all text lives in one of these three) ──────────── */
    .sz-h1  { font-size: 1.15rem; font-weight: 700; letter-spacing: -.01em; }
    .sz-sub { font-size: .92rem;  font-weight: 600; line-height: 1.38; }
    /* sz-text = base .78rem applied to body                                   */

    a { color: inherit; }

    /* ── Layout ───────────────────────────────────────────────────────────── */
    .wrap { max-width: 1100px; margin: 0 auto; padding: 1.25rem 1.5rem; }

    /* ── Top bar ──────────────────────────────────────────────────────────── */
    .top-bar {
      display: flex; align-items: center; gap: .7rem;
      padding-bottom: .9rem; margin-bottom: 1rem;
      border-bottom: 1px solid var(--border);
    }
    .top-bar .spacer { flex: 1; }
    .top-bar .total  { color: var(--text-muted); }

    .theme-btn {
      font-family: inherit; font-size: .78rem;
      background: var(--surface); color: var(--text-sub);
      border: 1px solid var(--border); border-radius: 5px;
      padding: .25rem .65rem; cursor: pointer; line-height: 1.5;
      transition: border-color .15s, color .15s;
    }
    .theme-btn:hover { color: var(--text); border-color: var(--text-muted); }

    .reload-link { color: var(--text-sub); text-decoration: none; }
    .reload-link:hover { color: var(--text); }

    /* ── Tab bar ──────────────────────────────────────────────────────────── */
    .tab-bar {
      display: flex; flex-wrap: wrap; gap: 2px;
      border-bottom: 1px solid var(--border);
      margin-bottom: .9rem;
    }
    .tab-link {
      font-size: .78rem; color: var(--text-sub);
      padding: .3rem .7rem;
      border-radius: 5px 5px 0 0;
      border: 1px solid transparent; border-bottom: none;
      text-decoration: none;
      display: inline-flex; align-items: center; gap: .28rem;
      transition: color .12s, background .12s;
    }
    .tab-link:hover { color: var(--text-sub); background: var(--filter-bg); }
    .tab-link.active {
      color: var(--text); font-weight: 600;
      background: var(--surface);
      border-color: var(--border); border-bottom-color: var(--surface);
      margin-bottom: -1px;
    }
    .tab-badge {
      font-size: .67rem; font-weight: 500;
      background: var(--filter-bg); color: var(--text-muted);
      border-radius: 5px; padding: .04rem .36rem;
    }
    .tab-link.active .tab-badge { background: var(--primary-bg); color: var(--primary); }

    /* ── Filter bar ───────────────────────────────────────────────────────── */
    .filter-bar {
      background: var(--filter-bg);
      border: 1px solid var(--border); border-radius: 5px;
      padding: .45rem .85rem;
      display: flex; align-items: center; gap: .9rem; flex-wrap: wrap;
      margin-bottom: .9rem;
    }
    .filter-label  { font-weight: 600; color: var(--text-sub); white-space: nowrap; }
    .filter-divider { width: 1px; height: .9rem; background: var(--border); align-self: center; }
    .filter-check {
      display: flex; align-items: center; gap: .28rem;
      color: var(--text-sub); cursor: pointer; user-select: none;
    }
    .filter-check:hover { color: var(--text); }
    .filter-check input { cursor: pointer; accent-color: var(--primary); }

    .venue-select {
      font-family: inherit; font-size: .78rem;
      padding: .18rem .45rem; border-radius: 5px;
      border: 1px solid var(--border);
      background: var(--surface); color: var(--text);
      cursor: pointer; max-width: 220px;
    }
    .venue-select:focus { outline: none; border-color: var(--primary); box-shadow: 0 0 0 2px var(--primary-bg); }

    /* ── Result count ─────────────────────────────────────────────────────── */
    .result-count { color: var(--text-muted); margin-bottom: .75rem; }
    .result-count strong { color: var(--text-sub); font-weight: 600; }

    /* ── Paper cards ──────────────────────────────────────────────────────── */
    .paper-list { display: flex; flex-direction: column; gap: .45rem; }

    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 5px;
      box-shadow: var(--shadow);
      padding: .7rem 1rem;
    }
    /* Left accent: bookmarked takes priority over viewed visually */
    .card.is-viewed     { border-left: 3px solid var(--accent-v) !important; }
    .card.is-bookmarked { border-left: 3px solid var(--accent-b) !important; }
    /* Read: fade content only, leave controls usable */
    .card.is-read .paper-title a,
    .card.is-read .paper-authors,
    .card.is-read .abstract-text { opacity: .5; }

    .paper-title { margin-bottom: .16rem; }
    .paper-title a {
      font-size: .92rem; font-weight: 600;
      color: var(--primary); text-decoration: none; line-height: 1.38;
    }
    .paper-title a:hover { text-decoration: underline; }

    .paper-authors { color: var(--text-sub); margin-bottom: .28rem; }

    .paper-meta {
      display: flex; align-items: center; flex-wrap: wrap;
      gap: .4rem; color: var(--text-muted);
    }
    .meta-left    { display: flex; align-items: center; gap: .3rem; flex-wrap: wrap; flex: 1; min-width: 0; }
    .paper-date   { font-weight: 500; color: var(--text-sub); }
    .meta-sep     { color: var(--border); user-select: none; }
    .paper-controls {
      display: flex; align-items: center; gap: .4rem; flex-wrap: wrap; flex-shrink: 0;
    }

    .cat-select {
      font-family: inherit; font-size: .78rem;
      padding: .12rem .38rem; border-radius: 5px;
      border: 1px solid var(--border);
      background: var(--surface); color: var(--text);
      cursor: pointer; max-width: 130px;
    }
    .cat-select:focus { outline: none; border-color: var(--primary); box-shadow: 0 0 0 2px var(--primary-bg); }

    .paper-check {
      display: flex; align-items: center; gap: .22rem;
      color: var(--text-muted); cursor: pointer;
      white-space: nowrap; user-select: none;
    }
    .paper-check:hover { color: var(--text-sub); }
    .paper-check input { cursor: pointer; margin: 0; accent-color: var(--primary); }

    /* Abstract — left rule, comfortable line-height */
    .abstract-text {
      color: var(--text-sub); line-height: 1.65;
      border-left: 2px solid var(--border);
      padding-left: .75rem; margin-top: .55rem;
    }

    .no-papers { color: var(--text-muted); text-align: center; padding: 3rem 0; }

    /* ── Pagination ───────────────────────────────────────────────────────── */
    .pagination {
      display: flex; justify-content: center;
      flex-wrap: wrap; gap: .25rem; margin-top: 1.5rem;
    }
    .page-link {
      font-family: inherit; font-size: .78rem;
      padding: .3rem .65rem; border-radius: 5px;
      border: 1px solid var(--border);
      background: var(--surface); color: var(--text-sub);
      text-decoration: none; line-height: 1.5;
      transition: background .1s, color .1s, border-color .1s;
    }
    .page-link:hover  { background: var(--filter-bg); color: var(--text); border-color: var(--text-muted); }
    .page-link.active { background: var(--primary); border-color: var(--primary); color: #fff; font-weight: 600; }
    .page-link.disabled { opacity: .32; pointer-events: none; }
  </style>
</head>
<body>
<div class="wrap">

  <!-- Top bar -->
  <div class="top-bar">
    <span class="sz-h1">AI/ML Paper Viewer</span>
    <span class="total">{{ total }} papers</span>
    <div class="spacer"></div>
    <button class="theme-btn" id="theme-toggle">☾ Dark</button>
    <a href="/reload" class="reload-link">↺ Reload</a>
  </div>

  <!-- Tabs -->
  <div class="tab-bar">
    {% for t in tabs %}
    <a class="tab-link {% if t == tab %}active{% endif %}"
       href="?tab={{ t }}&page=1{{ filter_qs }}">
      {{ t }}<span class="tab-badge">{{ counts[t] }}</span>
    </a>
    {% endfor %}
  </div>

  <!-- Filter bar -->
  <div class="filter-bar">
    <span class="filter-label">Show only</span>
    <div class="filter-divider"></div>
    <label class="filter-check">
      <input type="checkbox" id="filter-viewed" {% if show_viewed %}checked{% endif %}> Viewed
    </label>
    <label class="filter-check">
      <input type="checkbox" id="filter-read" {% if show_read %}checked{% endif %}> Read
    </label>
    <label class="filter-check">
      <input type="checkbox" id="filter-bookmarked" {% if show_bookmarked %}checked{% endif %}> Bookmarked
    </label>
    <label class="filter-check">
      <input type="checkbox" id="filter-labelled" {% if show_labelled %}checked{% endif %}> Labelled
    </label>
    <div class="filter-divider"></div>
    <select class="venue-select" id="filter-venue">
      <option value="">All venues</option>
      {% for v in all_venues %}
      <option value="{{ v }}"{% if v == filter_venue %} selected{% endif %}>{{ v }}</option>
      {% endfor %}
    </select>
  </div>

  <!-- Result count -->
  <p class="result-count">
    {% if tab_count == 0 %}
      No papers{% if any_filter %} matching the active filter{% endif %}.
    {% else %}
      Showing <strong>{{ start + 1 }}–{{ start + shown_count }}</strong>
      of <strong>{{ tab_count }}</strong>
      {% if any_filter %}(filtered){% endif %}
      {% if total_pages > 1 %}&ensp;·&ensp;page {{ page }} / {{ total_pages }}{% endif %}
    {% endif %}
  </p>

  <!-- Paper list -->
  {% if papers %}
  <div class="paper-list">
  {% for p in papers %}
  {% set is_viewed     = p.viewed     == 'true' %}
  {% set is_read       = p['read']    == 'true' %}
  {% set is_bookmarked = p.bookmarked == 'true' %}
  <div class="card{% if is_viewed %} is-viewed{% endif %}{% if is_bookmarked %} is-bookmarked{% endif %}{% if is_read %} is-read{% endif %}">

    <div class="paper-title">
      <a href="{{ p.paper_url }}" target="_blank" rel="noopener"
         class="paper-link" data-url="{{ p.paper_url }}">
        {{ p.title if p.title else p.paper_url }}
      </a>
    </div>

    {% if p.authors %}
    <div class="paper-authors">{{ p.authors }}</div>
    {% endif %}

    <div class="paper-meta">
      <div class="meta-left">
        {% set display_date = p.pub_date or p.date_seen %}
        {% if display_date %}
        <span class="paper-date">{{ display_date[:4] ~ '-' ~ display_date[4:6] ~ '-' ~ display_date[6:8] if display_date | length == 8 else display_date }}</span>
        {% endif %}
        {% if p.place %}
        <span class="meta-sep">·</span><span>{{ p.place }}</span>
        {% endif %}
        {% if p.source_name %}
        <span class="meta-sep">via</span><span>{{ p.source_name }}</span>
        {% endif %}
      </div>
      <div class="paper-controls">
        <select class="cat-select" data-url="{{ p.paper_url }}">
          <option value="">— category —</option>
          {% for cat in categories|sort %}
          <option value="{{ cat }}"{% if p.category == cat %} selected{% endif %}>{{ cat }}</option>
          {% endfor %}
          <option value="others"{% if p.category not in categories and p.category != '' %} selected{% endif %}>others</option>
        </select>
        <label class="paper-check">
          <input type="checkbox" class="paper-check-input" data-url="{{ p.paper_url }}" data-field="viewed"
                 {% if is_viewed %}checked{% endif %}> Viewed
        </label>
        <label class="paper-check">
          <input type="checkbox" class="paper-check-input" data-url="{{ p.paper_url }}" data-field="read"
                 {% if is_read %}checked{% endif %}> Read
        </label>
        <label class="paper-check">
          <input type="checkbox" class="paper-check-input" data-url="{{ p.paper_url }}" data-field="bookmarked"
                 {% if is_bookmarked %}checked{% endif %}> Bookmarked
        </label>
      </div>
    </div>

    {% if p.abstract %}
    <div class="abstract-text">{{ p.abstract }}</div>
    {% endif %}

  </div>
  {% endfor %}
  </div>
  {% else %}
  <p class="no-papers">No papers in this category yet.</p>
  {% endif %}

  <!-- Pagination -->
  {% if total_pages > 1 %}
  <nav class="pagination">
    <a class="page-link {% if page == 1 %}disabled{% endif %}"
       href="?tab={{ tab }}&page={{ page - 1 }}{{ filter_qs }}">‹ Prev</a>
    {% for pg in page_range %}
      {% if pg == -1 %}
      <span class="page-link disabled">…</span>
      {% else %}
      <a class="page-link {% if pg == page %}active{% endif %}"
         href="?tab={{ tab }}&page={{ pg }}{{ filter_qs }}">{{ pg }}</a>
      {% endif %}
    {% endfor %}
    <a class="page-link {% if page == total_pages %}disabled{% endif %}"
       href="?tab={{ tab }}&page={{ page + 1 }}{{ filter_qs }}">Next ›</a>
  </nav>
  {% endif %}

</div><!-- /wrap -->

<script>
// ── Theme toggle ───────────────────────────────────────────────────────────────
const html = document.documentElement;
const btn  = document.getElementById('theme-toggle');

function applyTheme(dark) {
  html.classList.toggle('dark', dark);
  btn.textContent = dark ? '☀ Light' : '☾ Dark';
}
const saved = localStorage.getItem('theme');
applyTheme(saved ? saved === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches);
btn.addEventListener('click', () => {
  const nowDark = !html.classList.contains('dark');
  applyTheme(nowDark);
  localStorage.setItem('theme', nowDark ? 'dark' : 'light');
});

// ── Paper state patches ────────────────────────────────────────────────────────
function patch(paperUrl, updates) {
  Object.entries(updates).forEach(([field, value]) => {
    fetch('/update', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({paper_url: paperUrl, field, value})
    });
  });
}

document.querySelectorAll('.cat-select').forEach(sel => {
  sel.addEventListener('change', function() {
    patch(this.dataset.url, {category: this.value, labelled: 'true'});
  });
});

document.querySelectorAll('.paper-check-input').forEach(cb => {
  cb.addEventListener('change', function() {
    const val = this.checked ? 'true' : 'false';
    patch(this.dataset.url, {[this.dataset.field]: val});
    const card = this.closest('.card');
    if (!card) return;
    if (this.dataset.field === 'viewed')     card.classList.toggle('is-viewed',     this.checked);
    if (this.dataset.field === 'read')       card.classList.toggle('is-read',       this.checked);
    if (this.dataset.field === 'bookmarked') card.classList.toggle('is-bookmarked', this.checked);
  });
});

document.querySelectorAll('.paper-link').forEach(a => {
  a.addEventListener('click', function() {
    patch(this.dataset.url, {viewed: 'true'});
    this.closest('.card')?.classList.add('is-viewed');
    const cb = this.closest('.card')?.querySelector('[data-field="viewed"]');
    if (cb) cb.checked = true;
  });
});

// ── Filters ────────────────────────────────────────────────────────────────────
['filter-viewed', 'filter-read', 'filter-bookmarked', 'filter-labelled'].forEach(id => {
  document.getElementById(id)?.addEventListener('change', function() {
    const params = new URLSearchParams(window.location.search);
    const key = id.replace('filter-', 'show_');
    if (this.checked) params.set(key, '1');
    else params.delete(key);
    params.set('page', '1');
    window.location.search = params.toString();
  });
});

document.getElementById('filter-venue')?.addEventListener('change', function() {
  const params = new URLSearchParams(window.location.search);
  if (this.value) params.set('venue', this.value);
  else params.delete('venue');
  params.set('page', '1');
  window.location.search = params.toString();
});
</script>
</body>
</html>
"""


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    papers = load_papers()
    groups = group_by_tab(papers)

    tab = request.args.get("tab", ALL_TABS[0])
    if tab not in ALL_TABS:
        tab = ALL_TABS[0]

    try:
        page = max(1, int(request.args.get("page", 1) or 1))
    except (ValueError, TypeError):
        page = 1

    show_viewed     = request.args.get("show_viewed",     "") == "1"
    show_read       = request.args.get("show_read",       "") == "1"
    show_bookmarked = request.args.get("show_bookmarked", "") == "1"
    show_labelled   = request.args.get("show_labelled",   "") == "1"
    filter_venue    = request.args.get("venue", "").strip()
    any_filter      = show_viewed or show_read or show_bookmarked or show_labelled or filter_venue

    filter_qs = ""
    if show_viewed:     filter_qs += "&show_viewed=1"
    if show_read:       filter_qs += "&show_read=1"
    if show_bookmarked: filter_qs += "&show_bookmarked=1"
    if show_labelled:   filter_qs += "&show_labelled=1"
    if filter_venue:    filter_qs += f"&venue={filter_venue}"

    all_venues = sorted({
        p.get("place", "").strip()
        for p in papers
        if p.get("place", "").strip()
    })

    tab_papers = groups.get(tab, [])

    if show_viewed or show_read or show_bookmarked or show_labelled:
        tab_papers = [
            p for p in tab_papers
            if (show_viewed     and p.get("viewed")     == "true")
            or (show_read       and p.get("read")        == "true")
            or (show_bookmarked and p.get("bookmarked")  == "true")
            or (show_labelled   and p.get("labelled")    == "true")
        ]
    if filter_venue:
        tab_papers = [p for p in tab_papers if p.get("place", "").strip() == filter_venue]

    tab_count   = len(tab_papers)
    total_pages = max(1, (tab_count + PAGE_SIZE - 1) // PAGE_SIZE)
    page        = min(page, total_pages)
    start       = (page - 1) * PAGE_SIZE
    shown       = tab_papers[start: start + PAGE_SIZE]
    counts      = {t: len(groups.get(t, [])) for t in ALL_TABS}

    return render_template_string(
        TEMPLATE,
        tabs=ALL_TABS, tab=tab,
        categories=CATEGORIES,
        counts=counts, total=len(papers),
        papers=shown, start=start, shown_count=len(shown),
        tab_count=tab_count,
        page=page, total_pages=total_pages,
        page_range=build_page_range(page, total_pages),
        show_viewed=show_viewed, show_read=show_read,
        show_bookmarked=show_bookmarked, show_labelled=show_labelled,
        filter_venue=filter_venue, all_venues=all_venues,
        any_filter=any_filter, filter_qs=filter_qs,
    )


@app.route("/update", methods=["POST"])
def update_paper():
    data      = request.get_json(force=True, silent=True) or {}
    paper_url = (data.get("paper_url") or "").strip()
    field     = (data.get("field") or "").strip()
    value     = str(data.get("value", ""))

    if not paper_url or field not in USER_FIELDS:
        return jsonify(ok=False, error="invalid params"), 400

    ok = update_paper_field(paper_url, {field: value})
    return jsonify(ok=ok)


@app.route("/reload")
def reload_cache():
    global _cache
    _cache = None
    return redirect(url_for("index"))


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI/ML paper viewer")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    print(f"  Paper viewer →  http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)
