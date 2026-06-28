import os
import csv
import json
from pathlib import Path

PEOPLE_DIR = Path(__file__).parent / "people"
TSV_IN = PEOPLE_DIR / "RESEARCHERS_FREQUENCY.tsv"
PROGRESS_FILE = PEOPLE_DIR / ".scholar_lookup_progress.json"

TOP_N = 400
MIN_DELAY = 15   # seconds
MAX_DELAY = 30   # seconds

# ── helpers ─────────────────────────────────────────────────────────────────

institutions = dict()

def load_tsv() -> tuple[list[str], list[dict]]:
    rows = []
    with open(TSV_IN, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        fieldnames = reader.fieldnames or []
        for row in reader:
            rows.append(dict(row))
    return list(fieldnames), rows

if __name__ == "__main__":
    column_names, rows = load_tsv()
    print(column_names)
