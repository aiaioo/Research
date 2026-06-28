# AI/ML Research Tracker — Usage Guide

A pipeline of Python scripts that collects, deduplicates, categorises, and analyses AI/ML papers, then identifies the most impactful researchers and institutions in the field.

---

## Run schedule

### Daily

Run these three scripts each day, in order:

```bash
python paper_searcher.py all          # 1. collect new papers from all sources
python paper_deduplicator.py          # 2. remove duplicates
python paper_categorizer.py           # 3. assign topic categories + impactful flags
```

### Monthly

Run these three scripts once a month after the daily pipeline, in order:

```bash
python paper_authors.py               # 1. count author appearances across tracked papers
python scholar_lookup.py              # 2. enrich top authors with Scholar profiles + citations
python institution_lookup.py          # 3. aggregate citation counts by institution
python institution_details.py         # 4. look up canonical name + URL for each institution
```

### Anytime

```bash
python paper_viewer.py                # launch the web UI to browse papers
```

---

## `paper_searcher.py`

Scans a curated list of AI/ML sources for new papers, enriches them with
metadata, and writes results to TSV files.

On each run the script:

1. Reads source URLs from `TRACKED_SOURCES.md`
2. Fetches each source using a source-appropriate strategy (RSS, dedicated API, HTML scraping)
3. Extracts paper links (arXiv, OpenReview, ACL Anthology, PMLR, and venue-specific URLs)
4. Enriches each paper with metadata — title, authors, abstract, keywords — via the arXiv Atom API or OpenReview API
5. Filters out papers outside AI/ML when the source is not a trusted AI/ML venue
6. Skips any paper URL already recorded in the current month's seen-papers log
7. Appends new papers to `papers/seen_papers_YYYYMM.tsv` (permanent monthly log)
8. Writes new papers to `papers/new_papers_YYYYMMDD.tsv` (today's discoveries)

### Output columns

| Column | Description |
|--------|-------------|
| `date_seen` | Date the paper was first discovered (YYYYMMDD) |
| `source_name` | Human-readable name of the source |
| `source_url` | URL of the source that surfaced the paper |
| `paper_url` | Canonical URL of the paper |
| `title` | Paper title |
| `authors` | Comma-separated author names |
| `abstract` | Paper abstract (where available) |
| `keywords` | Keywords or arXiv categories (where available) |
| `pub_date` | Publication date |
| `place` | Venue (conference, journal, or publisher) |
| `category` | Topic category assigned by `paper_categorizer.py` |
| `viewed` / `read` / `bookmarked` / `labelled` | User flags set via `paper_viewer.py` |
| `impactful_researcher` | `true` if any author has >10,000 Scholar citations |
| `impactful_institution` | `true` if any impactful author is at an institution with >50,000 combined citations |

### Usage

At least one group must be specified. Use `all` to check every source.

```bash
python paper_searcher.py <GROUP> [GROUP ...]
```

```bash
python paper_searcher.py all                    # every source
python paper_searcher.py curated                # curated feeds only
python paper_searcher.py educational            # university lab pages only
python paper_searcher.py corporate              # industry lab pages only
python paper_searcher.py uncurated              # algorithmic/high-volume feeds only
python paper_searcher.py conferences            # conference proceedings only
python paper_searcher.py journals               # peer-reviewed journals only
python paper_searcher.py curated uncurated      # multiple groups combined
python paper_searcher.py conferences journals   # conferences and journals together
```

### Source groups

Sources are divided into six groups, matching the sections in `TRACKED_SOURCES.md`.

#### `curated`

Manually or community-curated feeds with a human editorial layer. Low volume, high signal.

| Source | Type | Notes |
|--------|------|-------|
| Hugging Face Papers | Daily | Community-upvoted papers; typically 10–30 per day |
| Scholar Inbox Trending | Daily | Personalised + trending view |
| Emergent Mind | Daily | arXiv papers gaining social traction |
| AlphaSignal | Daily | Editor-selected ML papers and repos |
| TLDR AI | Daily | 3–5 papers chosen by editors |
| DAIR.AI – AI Papers of the Week | Weekly | ~10 papers with short descriptions |
| Ahead of AI (Sebastian Raschka) | Weekly | Deep-dive newsletter with paper commentary |
| Import AI (Jack Clark) | Weekly | Broad coverage including policy |
| Last Week in AI | Weekly | Structured roundup of papers and news |
| The Batch (deeplearning.ai) | Weekly | Accessible summaries of notable work |
| Interconnects (Nathan Lambert) | Weekly | RLHF and alignment focus |
| ML Papers Explained (Ritvik Rastogi) | Weekly | Growing annotated reference list |
| The Gradient | Monthly/irregular | Long-form articles and paper reviews |
| Distill.pub | Irregular | Interactive paper explanations |
| AI Alignment Forum | Irregular | Safety and alignment research |
| r/MachineLearning | Social | Community-voted Reddit posts |
| Papers With Code | Social | Weekly digest + latest papers |

#### `conferences`

Top-tier and second-tier AI/ML conference proceedings fetched from official proceedings hosts (OpenReview, PMLR, ACL Anthology, CVF Open Access, etc.).

**Tier 1** — NeurIPS, ICML, ICLR, CVPR, ACL, ICCV

**Tier 2** — AAAI, EMNLP, ECCV, NAACL, AISTATS, UAI, ICANN, IJCAI, CoRL, COLING, INTERSPEECH

Year-specific URLs need to be updated annually in `TRACKED_SOURCES.md`.

#### `journals`

Peer-reviewed AI/ML journals via RSS feeds.

**Tier 1** — JMLR, IEEE TPAMI, Nature Machine Intelligence, Artificial Intelligence (Elsevier)

**Tier 2** — IEEE TNNLS, IJCV (Springer), Pattern Recognition (Elsevier), Neural Networks (Elsevier), Machine Learning (Springer), IEEE TIP

#### `educational`

University and academic research lab publication pages. Covers labs at Stanford, MIT, CMU, UC Berkeley, Cornell, UW, NYU, Princeton, Oxford, UCL, Edinburgh, ETH Zürich, MPI Tübingen, University of Amsterdam, IDSIA, Tsinghua, KAIST, NUS, Bar-Ilan / Weizmann, Mila, and Vector Institute.

Papers are filtered against AI/ML arXiv categories to exclude non-AI work.

#### `corporate`

Industry and industry-affiliated research labs. Covers Google DeepMind, Google Research, Meta FAIR, Microsoft Research, OpenAI, Anthropic, Apple ML Research, Amazon Science, NVIDIA Research, Salesforce AI Research, IBM Research, Allen Institute for AI, EleutherAI, Redwood Research, ARC, Kyutai, Baidu Research, Alibaba DAMO, Tencent AI Lab, ByteDance Seed, Shanghai AI Laboratory, and KAIST AI Graduate School.

#### `uncurated`

High-volume or algorithmic feeds useful for breadth but noisier than curated sources. Includes arXiv cs.LG/recent, arXiv Sanity Lite, Papers With Code (latest), and Semantic Scholar.

### Metadata sources

| Paper venue | Title & authors | Abstract | Keywords |
|-------------|----------------|----------|----------|
| arXiv | arXiv Atom API | arXiv Atom API | arXiv category codes |
| OpenReview | OpenReview API | OpenReview API | `keywords` / `topics` field |
| Hugging Face | HF API | HF API (`summary`) | — |
| Distill.pub | RSS feed | RSS feed | — |
| ACL Anthology | URL extraction | — | — |
| PMLR | HTML title | — | — |
| IEEE / Springer / Nature / Elsevier journals | RSS feed | RSS feed | — |

---

## `paper_deduplicator.py`

Removes duplicate papers (by `paper_url`) from `seen_papers` and `new_papers` TSV files.

For `seen_papers_YYYYMM.tsv` files, deduplication is **global across all months**: files are processed in chronological order and a `paper_url` first recorded in an earlier month is removed from all later months. Within-file duplicates are also removed.

For `new_papers_YYYYMMDD.tsv` files, duplicates are removed within each file only.

```bash
python paper_deduplicator.py              # dedup all seen files + today's new file
python paper_deduplicator.py --all        # dedup all seen files + all new files
python paper_deduplicator.py --new papers/new_papers_20260626.tsv
```

---

## `paper_categorizer.py`

Assigns a topic category to each uncategorised paper using keyword scoring over
the title (3×) and abstract (1×), supplemented by arXiv category codes.  Also
sets `impactful_researcher` and `impactful_institution` flags for every paper on
each run.

### Categories

`safety` · `voice` · `vision` · `memory` · `models` · `training`

Ties are broken by the priority order above.  Papers that score below the
minimum threshold are left uncategorised (they appear under "others" in the
viewer).

### Impactful flags

On every run, `paper_categorizer.py` loads the latest
`people/RESEARCHERS_FREQUENCY_YYYYMM.tsv` and
`people/INSTITUTIONS_FREQUENCY_YYYYMM.tsv` files and updates two flags for all
papers:

- **`impactful_researcher`** — set to `true` when at least one author has more than 10,000 Scholar citations.
- **`impactful_institution`** — set to `true` when at least one impactful author's affiliated institution has more than 50,000 combined citations in the institution table.

These flags are refreshed for every paper in every file on each run, so running
`paper_categorizer.py` after a fresh `scholar_lookup.py` / `institution_lookup.py`
will propagate the updated research impact data across the entire corpus.

### Usage

```bash
python paper_categorizer.py                    # categorise all uncategorised papers
python paper_categorizer.py --dry-run          # print categories without writing
python paper_categorizer.py --file FILE.tsv    # process one specific file
python paper_categorizer.py --stats            # print category distribution
python paper_categorizer.py --reclassify       # re-do all non-labelled papers
```

---

## `paper_authors.py`

Counts how many tracked papers each author appears in (across all category TSV
files) and writes a ranked TSV of authors sorted by total paper count.

Only papers from NeurIPS, ICML, and ICLR (detected via the `place` column) are
counted.  Author names are normalised to "First Last" form and deduplicated per
paper URL to avoid double-counting.

Output: `people/RESEARCHERS_FREQUENCY_YYYYMM.tsv`

| Column | Description |
|--------|-------------|
| `author` | Normalised author name |
| `total` | Total papers across all categories |
| `models` / `training` / `safety` / `memory` / `vision` / `voice` / `other` | Papers per category |
| `scholar_url` | Google Scholar profile URL (filled by `scholar_lookup.py`) |
| `affiliation` | Institutional affiliation (filled by `scholar_lookup.py`) |
| `citations` | Total Scholar citations (filled by `scholar_lookup.py`) |

```bash
python paper_authors.py
```

---

## `scholar_lookup.py`

For the top authors in `people/RESEARCHERS_FREQUENCY_YYYYMM.tsv`, searches
Google Scholar for their author profile and records the profile URL, affiliation,
and citation count.  Skips rows that already have a `scholar_url`.  Results are
written back to the same TSV after each lookup so progress is preserved if the
run is interrupted.

Rate-limited to a random 15–30 second delay between requests to avoid triggering
Scholar's bot detection.  A browser window is opened (non-headless Chrome) to
allow the user to solve any CAPTCHA that appears.

```bash
python scholar_lookup.py
```

> Run after `paper_authors.py`.  May take several hours if many new authors need
> to be looked up.

---

## `institution_lookup.py`

Reads `people/RESEARCHERS_FREQUENCY_YYYYMM.tsv`, extracts and normalises the
`affiliation` field for each researcher, sums citation counts per institution,
and writes a ranked TSV.

Affiliation strings are cleaned by stripping job-title prefixes (e.g.
"Professor of Computer Science at") and selecting the institution-like part of
comma-separated strings.

Output: `people/INSTITUTIONS_FREQUENCY_YYYYMM.tsv`

| Column | Description |
|--------|-------------|
| `institution` | Cleaned institution name |
| `citations` | Sum of Scholar citations for all tracked researchers at this institution |
| `country` | Country of the institution (filled manually or by future enrichment) |
| `institution_url` | Official website URL (filled by `institution_details.py`) |

```bash
python institution_lookup.py
```

> Run after `scholar_lookup.py`.

---

## `institution_details.py`

For each institution in `people/INSTITUTIONS_FREQUENCY_YYYYMM.tsv`, searches
DuckDuckGo for the institution's canonical name and official website URL.
Skips institutions already recorded in `INSTITUTION_DETAILS.tsv`. Results are
written after each lookup so progress is preserved if the run is interrupted.

Rate-limited to a random 20–60 second delay between requests. A browser window
is opened (non-headless Chrome) so the session appears human.

Output: `people/INSTITUTION_DETAILS.tsv`

| Column | Description |
|--------|-------------|
| `institution` | Raw institution name from `INSTITUTIONS_FREQUENCY_*.tsv` |
| `canonical_name` | Name as returned by the first DuckDuckGo result |
| `institution_url` | Official website URL |

```bash
python institution_details.py
```

> Run after `institution_lookup.py`. May take several hours for a full run (435 institutions × up to 60 s each). Resume safely by re-running — already-resolved institutions are skipped.

---

## `paper_viewer.py`

A Flask web application for browsing the tracked paper corpus.  Papers are
grouped into tabs by category.  Supports filtering by viewed / read /
bookmarked / labelled status and by venue.  Inline category reassignment and
paper deletion are also available.

Additionally, pasting an arXiv or OpenReview URL into the search bar fetches,
classifies, and adds the paper to the corpus on the fly.

```bash
python paper_viewer.py                 # http://localhost:5000
python paper_viewer.py --port 8080
python paper_viewer.py --host 0.0.0.0 --port 8080
```

---

## Dependencies

```
feedparser
requests
beautifulsoup4
flask
playwright
```

Install with:

```bash
pip install -r requirements.txt
playwright install chromium
```
