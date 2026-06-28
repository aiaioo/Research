"""
paper_authors.py

Counts author appearances in NeurIPS, ICML, and ICLR papers across all
category TSV files, normalises names to "First Last" form, and writes
RESEARCHERS_FREQUENCY.tsv sorted by total paper count descending.
"""

import csv
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

PAPERS_DIR = Path(__file__).parent / "papers"
PEOPLE_DIR = Path(__file__).parent / "people"
OUTPUT_FILE = PEOPLE_DIR / "RESEARCHERS_FREQUENCY.tsv"

# Canonical category order for output columns
CATEGORIES = ["models", "training", "safety", "memory", "vision", "voice", "other"]

# Files that are staging/inbox lists — include them but dedup by URL
ALL_TSV_GLOB = "*.tsv"

# ---------------------------------------------------------------------------
# Conference matching
# ---------------------------------------------------------------------------

_CONF_PATTERNS = [
    re.compile(r"\bneurips\b", re.I),
    re.compile(r"neural information processing systems", re.I),
    re.compile(r"\bicml\b", re.I),
    re.compile(r"international conference on machine learning", re.I),
    re.compile(r"\biclr\b", re.I),
    re.compile(r"international conference on learning representations", re.I),
]


def is_target_conference(place: str) -> bool:
    return any(p.search(place) for p in _CONF_PATTERNS)


# ---------------------------------------------------------------------------
# Name normalisation
# ---------------------------------------------------------------------------

# LaTeX accent commands followed by a letter, e.g. \'a  \`{e}  \c{c}  \~n
_LATEX_ACCENT = re.compile(
    r"\\(?:[`'^~\"=.cHkruvtdobBdlLdcuub]|[a-zA-Z]+)\{?([a-zA-Z])\}?"
)
# Bare apostrophe-accent shorthand inside names: J'anos -> Janos
# Only strip when apostrophe sits between two letters (not a real apostrophe)
_BARE_ACCENT = re.compile(r"(?<=[a-zA-Z])'(?=[a-z])")


def _clean_latex(text: str) -> str:
    text = _LATEX_ACCENT.sub(r"\1", text)
    text = _BARE_ACCENT.sub("", text)
    text = re.sub(r"[{}]", "", text)
    # Normalise any remaining unicode to NFC then strip accents to ASCII
    text = unicodedata.normalize("NFC", text)
    return text.strip()


def _to_ascii_name(text: str) -> str:
    """Best-effort unicode → ASCII for consistent deduplication."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize_name(raw: str) -> str | None:
    """Return 'First Last', dropping middle names/initials, or None if empty."""
    raw = _clean_latex(raw.strip())
    if not raw:
        return None

    # Handle "Last, First" format
    if "," in raw:
        last, _, first = raw.partition(",")
        raw = f"{first.strip()} {last.strip()}"

    tokens = raw.split()
    tokens = [t for t in tokens if t]  # drop empty strings
    if not tokens:
        return None
    if len(tokens) == 1:
        return tokens[0].capitalize()

    first = tokens[0].capitalize()
    last = tokens[-1].capitalize()

    # Handle hyphenated last names: keep capitalisation per part
    if "-" in last:
        last = "-".join(part.capitalize() for part in last.split("-"))

    return f"{first} {last}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    # author_name -> {total, categories: {cat: count}}
    stats: dict[str, dict] = defaultdict(
        lambda: {"total": 0, "categories": defaultdict(int)}
    )

    # Global dedup: (normalised_author, paper_url) pairs already counted
    seen: set[tuple[str, str]] = set()

    for tsv_path in sorted(PAPERS_DIR.glob(ALL_TSV_GLOB)):
        with open(tsv_path, encoding="utf-8") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            # Skip files whose header row doesn't have the expected columns
            if reader.fieldnames is None or "place" not in reader.fieldnames:
                continue

            for row in reader:
                place = (row.get("place") or "").strip()
                if not is_target_conference(place):
                    continue

                category = (row.get("category") or "").strip()
                if category not in CATEGORIES:
                    category = "other"

                authors_raw = (row.get("authors") or "").strip()
                paper_url = (row.get("paper_url") or "").strip()
                if not authors_raw:
                    continue

                for raw_author in authors_raw.split(","):
                    name = normalize_name(raw_author)
                    if not name:
                        continue

                    key = (name, paper_url)
                    if key in seen:
                        continue
                    seen.add(key)

                    stats[name]["total"] += 1
                    stats[name]["categories"][category] += 1

    # Sort by total descending, then alphabetically for ties
    sorted_authors = sorted(
        stats.items(), key=lambda kv: (-kv[1]["total"], kv[0])
    )

    PEOPLE_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t")
        writer.writerow(["author", "total"] + CATEGORIES)
        for name, data in sorted_authors:
            row = [name, data["total"]] + [
                data["categories"].get(cat, 0) for cat in CATEGORIES
            ]
            writer.writerow(row)

    print(f"Wrote {len(sorted_authors)} authors → {OUTPUT_FILE}")
    if sorted_authors:
        print("\nTop 10 authors:")
        for name, data in sorted_authors[:10]:
            print(f"  {data['total']:3d}  {name}")


if __name__ == "__main__":
    main()
