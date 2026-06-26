#!/usr/bin/env python3
"""
paper_deduplicator.py

Remove duplicate papers (by paper_url) from seen_papers and new_papers TSV files.

For seen_papers_YYYYMM.tsv files, deduplication is always global across all months:
files are processed in chronological order and a paper_url first seen in an earlier
month is removed from all later months.

For new_papers_YYYYMMDD.tsv files, duplicates are removed within each file only.

Usage:
    python paper_deduplicator.py              # global dedup all seen files + today's new file
    python paper_deduplicator.py --all        # global dedup all seen files + all new files
    python paper_deduplicator.py --new papers/new_papers_20260626.tsv
"""

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

ROOT       = Path(__file__).parent
PAPERS_DIR = ROOT / "papers"
TODAY      = datetime.now().strftime("%Y%m%d")


def deduplicate_seen_globally(seen_paths: list) -> int:
    """
    Deduplicate seen_papers_*.tsv files across all months in chronological order.
    A paper_url first seen in an earlier file is removed from all later files.
    Also removes within-file duplicates.
    Returns total rows removed.
    """
    global_seen = set()
    total_dupes = 0

    for path in sorted(seen_paths):  # YYYYMM filenames sort chronologically
        if not path.exists():
            print(f"  [–] {path} not found, skipping")
            continue

        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            if "paper_url" not in (reader.fieldnames or []):
                print(f"  [!] {path.name}: no paper_url column, skipping", file=sys.stderr)
                continue
            fieldnames = reader.fieldnames
            rows = list(reader)

        unique_rows, dupes = [], 0
        for row in rows:
            key = row.get("paper_url", "")
            if key and key in global_seen:
                dupes += 1
            else:
                if key:
                    global_seen.add(key)
                unique_rows.append(row)

        if dupes == 0:
            print(f"  {path.name}: no duplicates found")
            continue

        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
            writer.writeheader()
            writer.writerows(unique_rows)

        print(f"  {path.name}: removed {dupes} duplicate(s)  ({len(rows)} → {len(unique_rows)} rows)")
        total_dupes += dupes

    return total_dupes


def deduplicate_new_file(path: Path) -> int:
    """
    Deduplicate a single new_papers TSV file in-place by paper_url.
    Returns the number of duplicate rows removed.
    """
    if not path.exists():
        print(f"  [–] {path} not found, skipping")
        return 0

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        if "paper_url" not in (reader.fieldnames or []):
            print(f"  [!] {path.name}: no paper_url column, skipping", file=sys.stderr)
            return 0
        fieldnames = reader.fieldnames
        rows = list(reader)

    seen_keys, unique_rows, dupes = set(), [], 0
    for row in rows:
        key = row.get("paper_url", "")
        if key and key in seen_keys:
            dupes += 1
        else:
            if key:
                seen_keys.add(key)
            unique_rows.append(row)

    if dupes == 0:
        print(f"  {path.name}: no duplicates found")
        return 0

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(unique_rows)

    print(f"  {path.name}: removed {dupes} duplicate(s)  ({len(rows)} → {len(unique_rows)} rows)")
    return dupes


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove duplicate papers from seen/new paper TSV files."
    )
    parser.add_argument("--all", action="store_true",
                        help="Also deduplicate all new_papers_*.tsv files (seen files are always all processed)")
    parser.add_argument("--new", metavar="FILE",
                        help="Specific new_papers TSV to deduplicate (within-file only)")
    args = parser.parse_args()

    total = 0
    all_seen_paths = sorted(PAPERS_DIR.glob("seen_papers_*.tsv"))

    print("Deduplicating seen files (global, all months):")
    total += deduplicate_seen_globally(all_seen_paths)

    print("\nDeduplicating new files:")
    if args.new:
        total += deduplicate_new_file(Path(args.new))
    elif args.all:
        for path in sorted(PAPERS_DIR.glob("new_papers_*.tsv")):
            total += deduplicate_new_file(path)
    else:
        total += deduplicate_new_file(PAPERS_DIR / f"new_papers_{TODAY}.tsv")

    print(f"\nTotal duplicates removed: {total}" if total else "\nAll files already deduplicated.")


if __name__ == "__main__":
    main()
