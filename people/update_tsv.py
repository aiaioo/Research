"""
Called after each browser batch to merge results into RESEARCHERS_FREQUENCY.tsv.
Usage: python3 update_tsv.py '<json_results>'
"""
import csv, json, sys
from pathlib import Path

PEOPLE = Path(__file__).parent
TSV = PEOPLE / "RESEARCHERS_FREQUENCY.tsv"
PROGRESS = PEOPLE / ".scholar_lookup_progress.json"

def main():
    raw = sys.argv[1] if len(sys.argv) > 1 else "{}"
    results = json.loads(raw)

    progress = json.loads(PROGRESS.read_text()) if PROGRESS.exists() else {}

    rows = []
    with open(TSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        fieldnames = list(reader.fieldnames)
        for row in reader:
            rows.append(dict(row))

    for col in ["scholar_url", "affiliation", "citations"]:
        if col not in fieldnames:
            fieldnames.append(col)
        for row in rows:
            row.setdefault(col, "")

    updated = 0
    for row in rows:
        name = row["author"]
        if name in results:
            r = results[name]
            if isinstance(r, dict) and r.get("scholar_url"):
                row["scholar_url"] = r["scholar_url"]
                row["affiliation"] = r.get("affiliation", "")
                row["citations"]   = r.get("citations", "")
                progress[name] = r["scholar_url"]
                updated += 1
            else:
                progress[name] = "not_found"

    with open(TSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    PROGRESS.write_text(json.dumps(progress, indent=2))
    print(f"Updated {updated}/{len(results)} authors. Total done: {sum(1 for v in progress.values() if v not in ('not_found','error'))}")

if __name__ == "__main__":
    main()
