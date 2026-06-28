import os
import csv
import json
from pathlib import Path
import re
from operator import itemgetter

PEOPLE_DIR = Path(__file__).parent / "people"
TSV_IN = PEOPLE_DIR / "RESEARCHERS_FREQUENCY.tsv"
PROGRESS_FILE = PEOPLE_DIR / ".scholar_lookup_progress.json"

TOP_N = 400
MIN_DELAY = 15   # seconds
MAX_DELAY = 30   # seconds

# ── helpers ─────────────────────────────────────────────────────────────────

institutions = dict()

def _remove_title_prefixes(text: str) -> str:
    # Pattern explanation:
    # ^ ensures the match only happens at the very start of the string.
    # (?: ... ) is a non-capturing group containing all alternatives.
    # .*? matches any characters lazily (useful for the "of * at" and "at" variations).
    # \s* removes any trailing whitespace left over after stripping the title.
    pattern = r"^(?:Director at the?|Director of .*? at the?|Assistant Professor at the?|Assistant Professor of .*? at the?|Professor at the?|Professor of .*? at the?|Teacher at the?|Teacher of .*? at the?|Researcher at the?|Dean at the?|Dean of .*? at the?|Prof.? at the?|Prof.? of .*? at the?|Ph.D.? student at)\s*"
    
    # re.IGNORECASE makes the match case-insensitive (optional, remove if case matters)
    return re.sub(pattern, "", text, flags=re.IGNORECASE).strip()

# --- Verification and Examples ---
test_strings = [
    "Director of Engineering at University of Doncaster",
    "Professor of Computer Science at the University of Sunnyvale",
    "Processor of Data at Tech Corp Alice",
    "Researcher at the Lab Bob",
    "Dean of Admissions at University Charlie",
    "Regular Employee Dave"  # Should remain unchanged
]

def test_prefix_removal():
    for s in test_strings:
        print(f"Original: {s}")
        print(f"Cleaned:  {_remove_title_prefixes(s)}\n")

def load_tsv() -> tuple[list[str], list[dict]]:
    rows = []
    with open(TSV_IN, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        fieldnames = reader.fieldnames or []
        for row in reader:
            rows.append(dict(row))
    return list(fieldnames), rows

def _match_university(result):
    return 'Univ' in result or 'Inst' in result or 'Polytech' in result or result in ('MIT', 'UCL', 'Stanford', 'Berkeley', 'Yale', 'Harvard','Oxford', 'Cambridge', 'Cornell')

def _select_section(affiliation):
    "The input string is a comma separated mix of titles and institutions.  This function tries to return the part with the university name."
    result = affiliation
    for splitter in (',',';','|'):
        if splitter in affiliation:
            parts = affiliation.split(splitter)
            result = parts[-1]
            if not _match_university(result):
                for part in reversed(parts[0:-1]):
                    if _match_university(part):
                        result = part
                        break
    return result

def _clean_up_affiliation(affiliation):
    result = _select_section(affiliation)
    return _remove_title_prefixes(result)

def is_noise(key):
    return key.lower() in ("", "ltd", "researcher") or ("…" in key and len(key) < 10)

def _count_institutions(rows):
    institution_dict = dict()
    for row in rows:
        author = row['author']
        affiliation = row['affiliation']
        citations = 0
        try:
            citations = int(row['citations'])
        except ValueError:
            pass
        if affiliation is None or "" == affiliation or len(affiliation.strip()) == 0:
            continue

        cleaned_up_affiliation = _clean_up_affiliation(affiliation)
        if not is_noise(cleaned_up_affiliation):
            if cleaned_up_affiliation not in institution_dict:
                institution_dict[cleaned_up_affiliation] = citations
            else:
                institution_dict[cleaned_up_affiliation] += citations

    # Sort by total descending, then alphabetically for ties (thanks to Gemini)
    sorted_descending = sorted(institution_dict.items(), key=itemgetter(1), reverse=True)

    return institution_dict, sorted_descending

if __name__ == "__main__":
    column_names, rows = load_tsv()
    #print(column_names)
    #test_prefix_removal()
    institution_dict, ordered_keys = _count_institutions(rows)
    TARGET_FILE = 'people/INSTITUTIONS_FREQUENCY.csv'
    with open(TARGET_FILE, 'w', encoding="utf-8") as fout:
        for key in ordered_keys:
            fout.write(key[0] + "\t" + str(institution_dict[key[0]]))
        fout.flush()
    print("" + str(len(ordered_keys)) + " rows written to " + TARGET_FILE)
