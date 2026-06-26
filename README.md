# AI Paper Searcher

`paper_searcher.py` scans a curated list of AI/ML sources for new papers, deduplicates them against a running history, and writes the results to TSV files.

---

## What it does

On each run the script:

1. Reads source URLs from `TRACKED_SOURCES.md`
2. Fetches each source using a source-appropriate strategy (RSS, dedicated API, HTML scraping)
3. Extracts paper links (arXiv, OpenReview, ACL Anthology, PMLR, and venue-specific URLs for conferences and journals)
4. Enriches each paper with metadata — title, authors, abstract, keywords — via the arXiv Atom API or OpenReview API
5. Filters out papers outside AI/ML when the source is not a trusted AI/ML venue
6. Skips any paper URL already recorded in the current month's seen-papers log
7. Appends new papers to `papers/seen_papers_YYYYMM.tsv` (permanent monthly log)
8. Writes new papers to `papers/new_papers_YYYYMMDD.tsv` (today's discoveries)

---

## Output files

Both output files are tab-separated (TSV) with the following columns:

| Column | Description |
|--------|-------------|
| `date_seen` | Date the paper was first discovered (YYYYMMDD) |
| `source_name` | Human-readable name of the source |
| `source_url` | URL of the source that surfaced the paper |
| `paper_url` | Canonical URL of the paper (arXiv, OpenReview, ACL Anthology, PMLR, or publisher page for journal/conference papers not on arXiv) |
| `title` | Paper title |
| `authors` | Comma-separated author names |
| `abstract` | Paper abstract (where available) |
| `keywords` | Keywords, topics, or arXiv categories (where available) |

`seen_papers_YYYYMM.tsv` accumulates all papers found during the month and is used to avoid re-reporting the same paper on subsequent runs. `new_papers_YYYYMMDD.tsv` contains only the papers discovered in the current run and is overwritten each time.

---

## Usage

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

---

## Source groups

Sources are divided into six groups, matching the sections in `TRACKED_SOURCES.md`.

### `curated`

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

### `conferences`

Top-tier and second-tier AI/ML conference proceedings. Papers are fetched from the venue's official proceedings host (OpenReview, PMLR, ACL Anthology, CVF Open Access, etc.).

**Tier 1** — NeurIPS, ICML, ICLR, CVPR, ACL, ICCV

**Tier 2** — AAAI, EMNLP, ECCV, NAACL, AISTATS, UAI, ICANN, IJCAI, CoRL, COLING, INTERSPEECH

Year-specific URLs (CVPR, ICLR, ICML volume, CoRL, INTERSPEECH, IJCAI) need to be updated annually in `TRACKED_SOURCES.md`.

### `journals`

Peer-reviewed AI/ML journals. Papers are fetched via each publisher's RSS feed where available. When a paper is also on arXiv, the arXiv URL is used as the canonical `paper_url`; otherwise the publisher's article page is used.

**Tier 1** — JMLR, IEEE TPAMI, Nature Machine Intelligence, Artificial Intelligence (Elsevier)

**Tier 2** — IEEE TNNLS, IJCV (Springer), Pattern Recognition (Elsevier), Neural Networks (Elsevier), Machine Learning (Springer), IEEE TIP

### `educational`

University and academic research lab publication pages.

Covers labs at Stanford, MIT, CMU, UC Berkeley, Cornell, UW, NYU, Princeton, Oxford, UCL, Edinburgh, ETH Zürich, MPI Tübingen, University of Amsterdam, IDSIA, Tsinghua, KAIST, NUS, Bar-Ilan / Weizmann, Mila, and Vector Institute.

Papers from these sources are filtered against a list of AI/ML arXiv categories (`cs.AI`, `cs.LG`, `cs.CL`, `cs.CV`, `cs.NE`, `cs.RO`, `cs.IR`, `cs.MA`, `cs.HC`, `stat.ML`, `eess.AS`, `eess.IV`) to exclude non-AI work.

### `corporate`

Industry and industry-affiliated research labs publishing peer-reviewed work.

Covers Google DeepMind, Google Research, Meta FAIR, Microsoft Research, OpenAI, Anthropic, Apple ML Research, Amazon Science, NVIDIA Research, Salesforce AI Research, IBM Research, Allen Institute for AI, EleutherAI, Redwood Research, ARC, Kyutai, Baidu Research, Alibaba DAMO, Tencent AI Lab, ByteDance Seed, Shanghai AI Laboratory, and KAIST AI Graduate School.

The same AI/ML category filter is applied as for `educational` sources.

### `uncurated`

High-volume or algorithmic feeds. Useful for breadth but noisier than curated sources.

| Source | Notes |
|--------|-------|
| arXiv cs.LG/recent | Raw daily listing; hundreds of papers per day |
| arXiv Sanity Lite | SVD-based recommendations |
| Papers With Code (latest) | Algorithmically ranked by GitHub stars + citations |
| Semantic Scholar | Large-corpus search; no editorial curation |
| Connected Papers | On-demand graph tool — skipped automatically |
| Google Scholar Alerts | Email-only — skipped automatically |
| ResearchGate | Login required — skipped automatically |

---

## Metadata sources

| Paper venue | Title & authors | Abstract | Keywords |
|-------------|----------------|----------|----------|
| arXiv | arXiv Atom API | arXiv Atom API | arXiv category codes (e.g. `cs.LG, cs.CL`) |
| OpenReview (individual papers) | OpenReview API | OpenReview API | `keywords` / `topics` / `keyphrases` field |
| OpenReview (venue/group pages) | OpenReview API | OpenReview API | — |
| Hugging Face daily papers | HF API | HF API (`summary`) | — |
| Distill.pub | RSS feed | RSS feed (`summary`) | — |
| ACL Anthology | URL extraction only | — | — |
| PMLR | HTML title from listing page | — | — |
| IEEE journals | RSS feed | RSS feed (`summary`) | — |
| Springer journals | RSS feed | RSS feed (`summary`) | — |
| Nature journals | RSS feed | RSS feed (`summary`) | — |
| Elsevier / ScienceDirect | RSS feed (where accessible) | RSS feed (`summary`) | — |
| JMLR | RSS feed (`jmlr.xml`) | RSS feed (`summary`) | — |

---

## Deduplication

`paper_deduplicator.py` removes duplicate papers from the TSV output files. Run it after `paper_searcher.py` to keep the logs clean.

For `seen_papers_YYYYMM.tsv` files, deduplication is always **global across all months**: files are processed in chronological order and a `paper_url` first recorded in an earlier month is removed from all later months. Within-file duplicates are also removed. For `new_papers_YYYYMMDD.tsv` files, duplicates are removed within each file only.

```bash
python paper_deduplicator.py              # global dedup all seen files + today's new file
python paper_deduplicator.py --all        # global dedup all seen files + all new files
python paper_deduplicator.py --new papers/new_papers_20260626.tsv
```

---

## Dependencies

```
feedparser
requests
beautifulsoup4
```

Install with:

```bash
pip install -r requirements.txt
```
